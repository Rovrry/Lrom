# 自动化 Root 实现方法

> 对应版本 v0.4.1 · 2026-10-27 · 本文记录 `lrom root` 的完整实现思路、
> 关键决策依据和踩坑过程。读完能理解**为什么这么写**，而不只是**写了什么**。
>
> 相关调研：`docs/research/` 下四份（KernelSU 系、Magisk 系、欧加设备、工程可行性）

---

## 0. 一句话概括

**用全量 ROM 包里已经解出来的 `init_boot.img`，在 PC 侧或手机端 patch 出带 root 的镜像，校验后 `fastboot flash init_boot` 刷回，重启即得 root。**

全程离线，素材零下载（除非用户主动 `fetch-manager`）。

---

## 1. 为什么是这个方案

### 1.1 排除了什么

调研了全部主流方案后逐一排除：

| 方案 | 为什么不用 |
|---|---|
| **KernelSU 内置内核（GKI 模式）** | 要刷通用 GKI `boot.img`。但 OPPO 系改过 f2fs，通用镜像不可用（两处独立文档佐证）。且违背「Lrom 只做搬运不做内核发行」的定位 |
| **KernelSU LKM「Direct install」** | 需要**先有 root** 才能用，首次 root 死循环 |
| **自行编译内核** | 需要厂商内核源码 + 交叉编译链，不是 Python 项目该做的事 |
| **Magisk Delta / Kitsune** | `HuskyDG` 账号与仓库全部 404，已消亡 |
| **联网下载预编译 patched boot** | 违反离线优先，且来源不可信 |

### 1.2 选中的两条路

同一套「离线 patch」思路，两种执行方式：

| 方式 | 谁执行 patch | 是否需要人工 | 依赖 |
|---|---|---|---|
| `--method ksud` | PC 侧 `ksud` 命令行 | ❌ 全自动 | 用户自备 ksud 二进制 |
| `--method app` | 手机上的 Manager APP | ✅ 需点几下 | 用户自备 APK |

**为什么 `app` 方式必须有人工**：KernelSU Manager 的「Select a file」是纯 UI 交互，
没有对外 API，也没有 exported Activity 能传参。用 `adb shell input tap` 按坐标点是
脆弱的黑盒 hack（分辨率/版本一变就废，误点有风险），违背「不砖机 > 功能多」。

---

## 2. 核心：三个必须先搞对的前置知识

这三条如果搞错，功能必然失败，而且**大多数是静默失败**。

### 2.1 要 patch 的是 `init_boot`，不是 `boot`

**Android 13（GKI 2.0）起，通用 ramdisk 从 `boot` 移到了 `init_boot`。**

本机实测（`ANDROID!` header v4，page_size 4096）：

```
boot.img        kernel_size=35,699,200   ramdisk_size=0        ← 完全没有 ramdisk
init_boot.img   kernel_size=0            ramdisk_size=2,389,727 ← 才有 ramdisk
vendor_boot.img VNDRBOOT v4                                    ← 另一种结构
```

**后果**：Magisk 的 `boot_patch.sh` 在找不到 ramdisk 时**不报错**，而是从零造一个：

```sh
else
  # No ramdisk found, create one from scratch
  RAMDISK=ramdisk.cpio
```

结果是：**patch 显示成功 → fastboot 刷入显示 OK → 开机正常 → 完全没有 root**。
零报错，纯浪费一次重启。

**实现**：`bootimg.check_ramdisk()` 在 patch 前强制检查，`ramdisk_size == 0` 直接拦下，
并告诉用户改用 `init_boot`。

### 2.2 KMI 按**内核版本**推导，不是按 Android 版本

GKI 内核版本格式：

```
Version.PatchLevel.SubLevel-AndroidRelease-KmiGeneration-suffix
  w    .x        .y     -zzz          -k              -something
KMI = w.x-zzz-k          ← SubLevel 不属于 KMI
```

本机设备：

```
Android 16，但内核是 6.1.141-android14-11-o-g5cdab3b518c1
→ KMI = 6.1-android14-11     ✅
→ 不是 android16-6.12        ❌ 网上教程常这么写
```

**为什么**：内核版本跟随设备**出厂**时的 Android，后续 OTA 不换内核。
社区已有一加平板 A16 因选错 KMI 试错两轮的实例
（[SukiSU #815](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/815)）。

**实现**：`root.derive_kmi()` 正则解析，`--kmi` 可手动覆盖。
测试 `test_derive_kmi_android16_kernel_still_android14` 专门锁住这条。

### 2.3 AVB 不拦（但必须实测确认）

原先这是最大的未知项。`vbmeta.img` 实测：

```
alg=2 (SHA256_RSA2048，有真签名)
flags=0x20 (不是 VERIFICATION_DISABLED)
hash descriptor 确实绑定了 init_boot，img_size=2,396,160 与 AVB footer 吻合
```

按标准 AVB 逻辑，改了 `init_boot` 摘要必然失配。**但设备 `verifiedbootstate=orange`。**

**探测实验**（正式写代码前做的，这步很关键）：往 ramdisk 加一个空文件、
原结构 LZ4 压回、修正 AVB footer 的 `orig_image_size`、刷入、重启。

结果：**30 秒正常开机，`verifiedbootstate` 仍是 `orange`，未变红、未拒绝。**

**结论：已解锁设备改 `init_boot` 不需要动 `vbmeta`，不需要 `--disable-verification`。**

> 这个实验花 10 分钟，但它决定了两千行代码要不要写。**先验证未知项，再动手。**

---

## 3. 模块结构

```
src/lrom/core/
├── bootimg.py        248 行   boot image 只读解析
├── root.py           687 行   root 流程
└── manager_fetch.py  388 行   唯一的联网模块

tests/unit/
├── test_bootimg.py          234 行 / 15 个测试
├── test_root.py             423 行 / 35 个测试
└── test_manager_fetch.py    317 行 / 29 个测试
```

---

## 4. `core/bootimg.py` —— 解析层

### 4.1 功能

```python
parse_boot_image(path) -> BootImageInfo     # 解析 header v0–v4
check_ramdisk(path, partition=) -> (bool, str)  # 强制前置检查
```

`BootImageInfo` 字段：

| 字段 | 说明 |
|---|---|
| `header_version` | 0–4 |
| `kernel_size` / `ramdisk_size` | 原始字段值 |
| `page_size` | v0–v2 按 header 读（2048/4096/8192）；v3–v4 固定 4096 |
| `ramdisk_offset` | 推导出的 ramdisk 起点 |
| `ramdisk_compression` | gzip / lz4_legacy / lz4_frame / xz / lzma / zstd / cpio |
| `has_avb_footer` / `avb_orig_image_size` | AVB footer 探测 |

### 4.2 两种 header 布局

```
v0–v2:  header(1 页) + kernel → 按 page_size 对齐 → ramdisk
v3–v4:  header(1584) + sig + kernel → 按 4096 对齐   → ramdisk
```

v4 的 signature_size 在 offset 40（紧接 v3 部分的 reserved 之后）。

### 4.3 一个实测得到的教训

**header 里的 `ramdisk_size` 字段不可信。**

本机 `init_boot.img` 的该字段声称 2,389,727，但实际 LZ4 解压需要**精确的
4,533,760 字节**缓冲区，按它声明的值解压直接失败。

因此本模块**只把该字段当「有没有 ramdisk」的判据**，不当精确长度用。
真实逻辑长度以 AVB footer 的 `orig_image_size` 为准。

---

## 5. `core/root.py` —— 流程层

### 5.1 两个正交维度

```
--method        ksud | app          谁执行 patch
--flash-mode    fastboot | fastbootd  走哪条刷写通道
```

#### `--flash-mode` 为什么必须可选（用户提出）

**OCDT 分区被恶意脚本抹掉后，bootloader 进不去，`fastboot` 模式完全不可用。**
而 fastbootd 由**系统里的用户空间服务**启动，不依赖 OCDT —— 此时是唯一还能刷写的通道。

`init_boot` 是物理分区，两种模式都能写，所以技术上等价；但**设备状态**决定了
可能只有一条路走得通。按用户要求，Lrom **不替用户判断**，交给使用者选择。

交互选择界面：

```
  选择刷入模式：
    [1] Fastboot  （bootloader 模式，常规，默认）
    [2] FastbootD （用户空间模式）

        提示：如果设备的 OCDT 分区被抹掉，bootloader 会进不去，
              此时只能选 FastbootD（它由系统里的 fastbootd 服务启动，
              不依赖 OCDT）。
```

`enter_fastbootd()` 复用 `flash.py` 的经验：OPLUS 设备可能停在语言选择界面，
等待期间给提示；没检测到设备时直接给出 `adb reboot fastboot` 这条绕过 bootloader 的办法。

### 5.2 主流程

```
build_root_plan()          生成计划（不写设备）
   ├─ 镜像存在？ → parse_boot_image()
   ├─ check_ramdisk()       ⛔ 无 ramdisk 直接拦
   ├─ 分区大小够不够？
   ├─ 读设备 uname -r → derive_kmi()
   └─ ksud 是否可用？

save_stock_image()         留存原厂底图  ★ 必须在任何写操作之前
patch_with_ksud()          或 push_for_app_patch() + pull_app_patched()
verify_patched_image()     刷前校验   ★ 把「刷进去才发现坏了」提前
flash_patched_image()      fastboot / fastbootd 刷入
[停]                       打印手动重启命令，永不自动重启
```

### 5.3 三道安全闸

| 闸 | 位置 | 拦什么 |
|---|---|---|
| **ramdisk 检查** | patch 前 | 防「静默失败」，patch 了没 ramdisk 的分区 |
| **刷前校验** | 刷入前 | 越界、丢 ramdisk、丢 AVB footer、header 版本变化 |
| **底图先行** | 任何写操作之前 | 保证永远有退路 |

`verify_patched_image()` 检查四项：

```python
if size > partition_size:           "超过分区"
if not patched_info.has_ramdisk:    "没有 ramdisk，patch 可能是无效的"
if header_version 和原厂不同:        "header 版本变了，可能不兼容"
if 原厂有 AVB footer 而 patch 后没有: "丢失 AVB footer，可能无法启动"
```

### 5.4 失败即打印恢复命令

```python
print(f"    fastboot flash {plan.partition} {stock_copy}")
```

---

## 6. `core/manager_fetch.py` —— 联网层

### 6.1 为什么允许联网

用户明确要求：「修改协议，标明联网只从 GitHub 下载 root 管理器，无恶意远程代码」。

原则 2 从「离线优先，零遥测」修订为：

> 离线优先，零遥测 —— 核心链路不依赖任何服务器。
> **唯一的联网是用户显式发起的 root 管理器下载。**

### 6.2 承诺写死在代码里

| 约束 | 实现 | 测试 |
|---|---|---|
| 域名白名单（仅 3 个） | `_check_url()` 在**发请求之前**校验 | `test_reject_non_whitelisted_hosts` |
| 强制 HTTPS，不降级 | `_check_url()` 检查 scheme | `test_reject_non_https` |
| 无摘要拒绝下载 | `download_asset()` 强制要求 digest | `test_download_refuses_without_digest` |
| 校验失败立即删文件 | `unlink()` 后抛错 | `test_download_rejects_bad_checksum_and_cleans_up` |
| 只下载不执行 | 远程内容只当文件写盘 | — |
| 不发送设备信息 | 请求头只有 `User-Agent: lrom/<版本>` | — |
| `lrom root` 永不联网 | 联网只存在于 `fetch-manager` | — |

```python
ALLOWED_HOSTS = frozenset({
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
})
```

### 6.3 始终取最新版

- `stable`：GitHub 的 `/releases/latest` 指针
- `beta`：`/releases` 列表里最新的 `prerelease`

所以 KernelSU 发新版时 **Lrom 不用改代码**。

### 6.4 asset 选择的教训

写测试时发现真 bug：排除词用 `"arm64-v8"` 想排掉架构包，但排不掉 `"armeabi-v7a"`，
两个架构包都留下后被「优先通用包」逻辑**误判成唯一通用包** → 可能下错架构。

改成独立 `_ARCH_PATTERN` 正则，且**只有恰好一个不带架构字样才采用**，
否则报错列出全部候选，让用户 `--asset` 指定。**宁可报错不猜错。**

---

## 7. 真机验证记录

2026-10-27，OnePlus OPD2404（一加平板 Pro），Android 16，`U.247d78a_a3a1b_629cb`。

| 步骤 | 结果 |
|---|---|
| 下载 KernelSU v3.3.1 APK（13.0 MB） | ✅ sha256 校验通过 |
| `adb install` | ✅ |
| 推送 `init_boot.img` 到 `/sdcard/Download/` | ✅ |
| APP 内「Select a file」修补 | ✅ 产出 `kernelsu_patched_*.img` |
| CPIO 内容检查 | ✅ `init`(603KB) + `init.real`(3MB) + `kernelsu.ko`(413KB) + `ksu_config` |
| `verify_patched_image()` | ✅ PASS |
| 刷入 `init_boot` | ✅ OKAY |
| 重启 | ✅ 30 秒开机 |
| 内核模块 | ✅ `/proc/modules`：`kernelsu 225280 ... Live (O)` |
| KernelSU APP | ✅ **「工作中」LKM**，显示设备实际内核版本 |

### 一个反直觉的发现

patch 出来的 `kernelsu.ko` 的 vermagic 是 **`6.1.166-dirty`**，
设备内核是 **`6.1.141`** —— **不一致，但模块照样加载成功**。

原因：这个 `.ko` 与官方 `lkm-aarch64-android14-6.1_kernelsu.ko`
**sha256 逐字节相同**（413296 字节）。KMI 兼容就够了，
vermagic 里的 SubLevel 不参与判定。

> 这修正了项目早期「vermagic 不匹配所以没生效」的推断
> —— 那次失败的真正原因是流程没走通，不是 vermagic。

### `su -c id` 从 adb shell 失败是正常的

KernelSU 默认不授予 `shell` uid 的 root，需要在 APP「超级用户」里手动添加。
**这不影响 root 是否存在**——以模块加载 + APP 显示「工作中」为准。

---

## 8. 测试策略

### 8.1 分层

| 层 | 方式 | 数量 |
|---|---|---|
| L1 纯函数 | 合成 boot image 字节流 | `test_bootimg.py` 15 |
| L2 流程 | FakeDevice / FakeFastboot 注入 | `test_root.py` 35 |
| L3 网络 | monkeypatch `_request`，完全不联网 | `test_manager_fetch.py` 29 |
| L4 真机 | 手动，见第 7 节 | — |

### 8.2 合成镜像构造器

`test_bootimg.py` 里的 `make_boot_v4()` / `make_boot_v2()` 按真实结构拼字节流，
不依赖仓库里的 `unpacked/`（那是运行产物，不入库）。

### 8.3 关键回归测试

| 测试 | 锁住什么 |
|---|---|
| `test_check_ramdisk_blocks_boot_image` | 不让 patch 无 ramdisk 分区 |
| `test_derive_kmi_android16_kernel_still_android14` | 不按 Android 版本选 KMI |
| `test_derive_kmi_sublevel_not_part_of_kmi` | SubLevel 不属于 KMI |
| `test_download_rejects_bad_checksum_and_cleans_up` | 校验失败必须不留文件 |
| `test_pick_asset_ambiguous_raises_and_lists` | asset 有歧义必须报错不猜 |

---

## 9. CLI 设计

```bash
lrom root                                  # 交互，会问刷入模式
lrom root --dry-run                        # 只出计划
lrom root --method ksud                    # PC 侧全自动
lrom root --method app                     # 手机 APP 修补
lrom root --flash-mode fastbootd           # 强制用户空间模式
lrom root --kmi 6.1-android14-11           # 手动指定 KMI
lrom root -i path/to/init_boot.img         # 手动指定源镜像

lrom fetch-manager --source ksu --channel stable
lrom fetch-manager --source magisk --channel beta
```

### 交互菜单入口

除 CLI 外，交互菜单有独立的「Root」节：

```
  ─ Root ────────────────────────────────────────
   7) 获取 Root          patch init_boot 并刷入
   8) 下载 Root 管理器   从 GitHub 下载 APK（唯一联网）
   9) 回滚 Root          刷回原厂 init_boot
```

> v0.4.2 曾漏掉这个入口（只做了 CLI），v0.4.3 补上。
> 现在有 `test_menu_entry_count_matches_dispatch` 自动校验
> 「菜单编号」与「分发函数」的一致性，防止同类遗漏。

### 素材定位优先级

```
1. -i / --image 显式指定
2. unpacked/rom/init_boot.img     ← 全量包解出的，版本最可靠
3. unpacked/rom/boot.img          ← 老设备，但会因无 ramdisk 被拦
```

### 回滚

`save_stock_image()` 在刷写**之前**把原厂镜像存到 `root-work/`，
同时写一份 `.json` 清单（含 sha256）。失败时直接打印：

```
fastboot flash init_boot root-work/init_boot-stock-<时间>.img
```

---

## 10. 仍然存在的限制

| 限制 | 说明 |
|---|---|
| APP 方式必须人工 | Manager 的「Select a file」无对外 API，见 1.2 |
| 每次 OTA 丢 root | 系统升级会覆盖 `init_boot`，需重新执行 |
| `lrom restore` 未实现 | 回滚目前靠手动 `fastboot flash`，见待办 |
| 只验证过一台设备 | OPD2404 / Android 16，其他机型未测 |
| `vendor_boot` 不支持 | `parse_boot_image()` 明确拒绝并说明原因 |
| 不重打包 ramdisk | Lrom 只做「调用外部工具 patch + 校验 + 刷写」，不自己实现 LZ4 压缩 |

---

## 11. 给后续开发的建议

如果要继续加功能，按这个顺序：

1. **`lrom restore`** —— 回滚能力先行。`cli.py` 里早有引用但至今不存在，
   独立就有价值，且是 `lrom root` 的保险绳
2. **`discover.py` 补 `kernel_version` 字段** —— 现在每次都要 `adb shell uname -r`，
   应该在设备发现阶段就带上
3. **`plan.py` 把 `boot`/`init_boot` 加入 `DANGER_PARTITIONS`** ——
   目前它们归为 `Risk.SAFE`，对 root 场景不合适
4. **`--method ksud` 的完整真机验证** —— 本次真机走的是 APP 方式

---

*文档最后更新：2026-10-27 · 对应版本 v0.4.4*
