# Lrom 做「自动化 root」的工程可行性评估

> 版本对应：Lrom v0.3.2 · 评估日期 2026-10-27 · 测试机 OnePlus OPD2404 / 代号 `OP5D77L1` / Android 16 / A/B · BL 已解锁、未 `unlock_critical`、当前无 root

## 0. 一句话结论

**能自动化的部分很少，卡点在"离线打包 boot 镜像"和"设备端 APP 授权"两处，不在 adb/fastboot 命令本身。**
Lrom 现有 5,344 行代码里，真正能复用到 root 流程的约 **30%**（`runner.py` / `discover.py` / `backup.py` 的框架 + 取镜像），
核心工作量集中在两件 Lrom 目前**完全没有**的能力上：
**(a) CPIO + LZ4 的 ramdisk 解包/重打包**，**(b) AVB vbmeta 重签或利用已解锁状态禁用校验**。
这两件事都无法在"零第三方依赖"的前提下做得安全，必须显式引入外部二进制（`lz4` / `magiskboot` / `avbtool`），而这与项目根基冲突。

---

## 1. 现有模块能复用多少（逐条对着源码核对）

### 1.1 ✅ 取 boot.img：已有两条现成路径

| 路径 | 位置 | 前提 | 对 root 流程可用性 |
|---|---|---|---|
| **fastboot fetch** | `src/lrom/core/backup.py:185-204`（`backup_partition_fastboot`） | 设备在 fastboot、fastboot 34+ | **完全可用**。未 root 设备唯一能"从设备取原厂 boot"的通道 |
| **adb + root dd** | `src/lrom/core/backup.py:114-182`（`backup_partition_adb`） | **必须先有 root**（`backup.py:41` `ROOT_REQUIRED`、`backup.py:96-102` `has_root`） | 第一次 root 时不可用；第二次起可用 |
| **从 ROM 包解出** | `src/lrom/core/unpack.py:164-219`（`unpack_package`），CLI 入口 `src/lrom/cli.py:399-475` | 有全量 OTA 包 | **可用**，且比从设备取更干净（不必启动 fastboot） |

`DEFAULT_BACKUP_PARTITIONS` 已经把 `boot` 和 `init_boot` 放在第一位（`src/lrom/core/backup.py:30-38`），
`lrom unpack` 也已经能把 `boot.img` / `init_boot.img` / `vbmeta.img` 落到磁盘 —— 本仓库的 `unpacked/rom/` 目录里就有实证产物。

> **所以"取 stock boot.img"这一步的复用度是 100%，不需要新写代码。**
> 但要注意区分两个来源：
> - **ROM 包里的** `unpacked/rom/boot.img`（版本 `16.0.5.1201`）
> - **设备里读出来的** `backups/<代号>_<时间>/boot.img`（版本 `16.0.5.702`）
>
> 两者**不能混用**。patch 出来的 boot 必须与设备当前系统版本严格对应（见 §2 步骤 3）。

### 1.2 ⚠️ 备份清单格式：结构可复用，语义要扩展

`BackupSession.to_json()`（`src/lrom/core/backup.py:69-85`）输出 `{tool, created_at, device, partitions[{name,file,size,sha256,ok,error}]}`，
写入 `backup.json`（`backup.py:265-271`）。这份清单**天然满足**"存 stock boot 用于回滚"的需求，因为：

- 有 `sha256`（`backup.py:88-93` 的 `_sha256_file`），可校验回滚镜像未被篡改；
- 有 `device` 指纹（`snapshot_device`，`backup.py:207-231`），记录了 `codename` / `build_id` /
  `ro.build.fingerprint` / `ro.boot.vbmeta.digest` / `ro.boot.verifiedbootstate` —— **这几个字段正是判断"能不能回滚"的关键**。

**但清单缺少 root 流程必需的元数据**，需要新增（不改已有字段，纯追加即可，向后兼容）：

```
patch: {
  tool: "lrom-patch", source: "unpacked/rom/boot.img",
  stock_sha256: "...", patched_sha256: "...",
  ramdisk_format: "lz4_legacy", ramdisk_compressed_size: 2389719,
  avb_footer: true, vbmeta_offset: 2396160, vbmeta_size: 960,
  target: {codename, build_id, kernel_version},
  ksu_version: "...", patch_mode: "gki_boot"   // 见 §2
}
```

**结论：清单格式复用度 ~80%，是"加字段"不是"重做"。**

### 1.3 ❌ 分区风险分级：`boot` / `init_boot` 目前是 SAFE

这是**一个必须在设计阶段就修掉的问题**。`_classify_partition`（`src/lrom/core/plan.py:215-225`）的分级规则是：

- `NEVER_OVERWRITE`（`plan.py:78-92`）→ BLOCKED
- `OPLUS_LOGICAL_PARTITIONS`（`plan.py:111-132`）→ SAFE
- `DANGER_PARTITIONS`（`plan.py:53-75`）→ DANGER
- `WARN_PARTITIONS`（`plan.py:95-108`：`super/modem/vbmeta/dtbo/...`）→ WARN
- 其余 → **SAFE**

`boot` 和 `init_boot` **不在任何一个集合里**，因此被归为 `Risk.SAFE`（`plan.py:225`）。
对普通刷机（刷官方全量包）这是合理的；但对 root 流程，**写坏了 boot 就是开机循环**，
必须按 DANGER 处理。方案：给 patch 流程单独引入一套风险模型，或者把 `boot`/`init_boot` 加进
`DANGER_PARTITIONS`（需确认是否会影响现有 36 个 `test_flash.py` 用例的断言，
`tests/unit/test_flash.py:1-60` 里对 `FakeFastboot` 的行为有硬编码假设）。

### 1.4 ⚠️ `runner.py`：可复用，但 patch 流程会绕开它

`runner.py` 的严格退出码判定（`src/lrom/device/runner.py:57-135`，`FastbootRunner.ok` 在 `runner.py:177-184`）
可以直接用于 `fastboot flash boot` 这一步 —— 这正是它存在的价值（`runner.py:141-144` 注释记录了 fastboot 失败仍返回 0 的坑）。

但 **ramdisk 解包/重打包这类"本地文件处理"不是子进程调用**，`runner.py` 用不上。
若改用外部 `magiskboot` / `lz4` 二进制，则应走 `tools.py:79-100` 的 `find_tool` + `runner.run`，
这样能自动继承 `LROM_*` 环境变量与配置覆盖（`tools.py:79-99`）—— 这一点设计上很干净。

### 1.5 ❌ `discover.py` 有一个缺口：读不到设备内核版本

root 方案（尤其是 KernelSU）必须知道**设备实际内核版本**来决定模块是否匹配。
`_read_adb_device`（`src/lrom/device/discover.py:112-130`）读了 `ro.product.device` / `ro.product.model` /
`ro.build.display.id` / `ro.build.version.release` / `ro.boot.slot_suffix` / `ro.boot.flash.locked`，
**没有读 `ro.product.kernel.version` 或 `uname -r`**。

这不是小问题：项目实测记录过一次真实事故 ——
备份里的 `kernelsu.ko` vermagic 是 `6.1.166-dirty`，设备内核是 `6.1.141`，**模块从未生效过**。
`Device` dataclass（`discover.py:19-31`）需要新增 `kernel_version` 字段。

### 1.6 ⚠️ 另一个半途而废的 root 依赖点：`lrom arb` 的"提升 lrom 体验"的通道

`read_device_arb_adb`（`src/lrom/core/arb.py:165-215`）在 `arb.py:179-185` 会先探测 `su -c id`，
有 root 就 `dd` 出 `xbl_config` 解析 ARB。**当前无 root → 这条路径永远返回 None**，
只能走 `read_device_arb_fastboot`（`arb.py` 后续函数）。也就是说 **root 会同时解锁 `arb` 的完整能力**，
这是"做 root"的附加收益，值得在立项时一并说明。

---

## 2. 自动化边界：把「root 一台设备」拆成步骤

### 2.0 先说死循环（这是给决策用的关键）

```
┌─────────────────────────────────────────────────────────────────┐
│  ❌ 悖论：KernelSU 的 LKM 模式需要「先有 root 才能加载内核模块」   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   想用 KernelSU LKM 模式                                         │
│        └─> 需要一个 .ko 内核模块                                 │
│              └─> 在设备上加载模块 = insmod                       │
│                    └─> insmod / init_module() 是 CAP_SYS_MODULE  │
│                          └─> 只有 uid=0 才有这个 capability      │
│                                └─> 需要 root                     │
│                                      └─> 而 root 正是我们要装的    │
│                                                                 │
│   ══════════════ 纯逻辑死循环，无技术捷径 ══════════════          │
└─────────────────────────────────────────────────────────────────┘
```

**为什么没有旁路：**

1. **不是"权限配置问题"，是内核能力门禁。** `/proc/sys/kernel/modules_disabled`
   （Android 上默认 0，即允许加载）只能"关掉加载"，**不能"不 root 就允许加载"**。
   `capable(CAP_SYS_MODULE)` 的判定在内核里，与 SELinux 无关，没有用户态开关。
2. **没有任何"未 root 也能 insmod"的官方入口。** 若存在，Android 的安全模型当场失效。
   所有"LKM 模式"的 KernelSU / APatch 文档都写明：**首次安装必须 patch boot 镜像**；
   LKM 模式的价值是"以后升级 KernelSU 不用再刷 boot"，**不是"绕开首次刷 boot"**。
3. **`adb root` 也不是旁路。** `adb root` 依赖 `ro.debuggable=1` 或 `service.adb.root=1`
   且 adbd 以 root 启动 —— 量产 `user` 版本两者都不成立。本机备份清单
   （`backups/OP5D77L1_20261026-212432/backup.json`）里 `ro.build.fingerprint` 尾部是
   `...:user/release-keys`，即**正式 user 版**，`adb root` 不可用。

**结论：第一次 root 只有一条路 —— 离线 patch 一个 boot 类分区并 fastboot flash 回去。**
LKM 模式只能是**第二次及以后**的省事方案。任何"用 LKM 免刷 boot 完成首次 root"的说法都是错的。

**本机的一个额外事实（决定该 patch 哪个分区）：**
我实测了仓库里现有的两个 `init_boot.img`：

```
backups/.../init_boot.img      size=8,388,608   ramdisk@4096    LZ4 legacy，解压 ~2.4 MB
                               cpio 内容只有 system/etc/init/snapuserd.rc + system/etc/ramdisk/build.prop
unpacked/rom/init_boot.img     size=8,388,608   ramdisk 编码不同，cpio 内容同量级
```

**也就是说：这台设备的 `init_boot` 是"通用 GKI init_boot"，不含厂商 init 逻辑；
真正的 `init` 在 `boot.img` 的 ramdisk 里。** 在 Android 13+ 的 GKI 设备上，厂商通常把 `init`
放 `init_boot`（对应 Magisk 的 `init_boot` patch 模式），但本机实测 `init_boot` ramdisk 内容极少，
**因此"patch init_boot 还是 patch boot"在这一台上必须实测确认，不能照抄网上教程**。
这是一个**真实的未知项**，建议优先验证（见 §5 的 `lrom root check`）。

> 注：我在分析时按 Linux `INITRAMFS_MAGIC = 0x184c2102` 直接搜索，`unpacked/rom/init_boot.img`
> 的 ramdisk 区首 4 字节即 `0x184c2102`（LZ4 legacy 帧头），但其 header 中的 `ramdisk_size`
> 字段（`0x1c0001a8` = 469,762,472）**明显不合理**（大于分区本身），且后续字节解释出的
> 内容与 LZ4 块语义不完全自洽。**推测该镜像的 header 字段被人为改写/混淆处理过，
> 需要在实现时以"解压失败即中止"为硬约束，不能信任 header 里的 size。**

### 2.1 步骤清单与自动化等级

图例：✅ 可全自动 ｜ ⚠️ 半自动（需用户确认 / 需外部文件 / 需设备端操作）｜ ❌ 不可自动

| # | 步骤 | 等级 | 说明与源码依据 |
|---|---|---|---|
| 1 | 检测设备与模式 | ✅ | `discover.detect()`（`discover.py:53-93`）已实现 |
| 2 | 校验 BL 已解锁 | ✅ | `ro.boot.flash.locked`（`discover.py:121-122`）；本机值 `"0"` = 已解锁 |
| 3 | 确认设备型号 / build / **内核版本** | ⚠️→✅ | 型号已有；**内核版本字段缺失**（见 §1.5），需新增 |
| 4 | 取得 stock boot/init_boot | ✅ | `unpack_package`（`unpack.py:164`）或 `fastboot fetch`（`backup.py:185`） |
| 5 | **首次植入前备份原厂 boot + vbmeta** | ✅ | `cmd_backup`（`cli.py:277-344`）已有；但**当前无 root 只能走 `fastboot fetch`**，而 `cmd_backup` 强制要求 ADB + root（`cli.py:287-296`）→ **需新增 fastboot 备份通路** |
| 6 | 解析 boot 头（v0-v4）、定位 ramdisk | ⚠️ 需新写 | 现在**完全不存在**。可行：`struct` 标准库，参考 `arb.py:20` 已用 `struct` 解析二进制 |
| 7 | 解压 ramdisk（gzip / **LZ4 legacy** / LZ4 frame / xz） | ⚠️ 需新写 | gzip/xz 有标准库；**LZ4 没有** → 见 §3 |
| 8 | 解包/重打包 CPIO（newc） | ⚠️ 需新写 | 标准库可做，但要对 4 字节对齐、`TRAILER!!!`、inode/uid/gid 保持原样，容错要求高 |
| 9 | 注入 root 组件（Magisk 的 `magiskinit` 替换 / KSU 的 init hook） | ❌ 半自动 | **核心逻辑是第三方项目的资产**，Lrom 自己实现 = 重写 Magisk，不现实；只能"下载 + 调用" |
| 10 | 重新压缩 ramdisk | ⚠️ 需新写 | 压缩率若不优于原厂，boot 可能超出分区大小 → 必须校验 |
| 11 | 重算 AVB（vbmeta 摘要 / footer） | ⚠️ 需新写或外挂 | RAMDISK 摘要可改；**header 的 auth 块需要 RSA 私钥，Lrom 永远不可能有** → 需确认本机 vbmeta header 的 `flags` / `auth` 状态 |
| 12 | 校验 patch 后镜像（sha256、尺寸不超分区、cpio 可回读） | ✅ 可做 | 是纯本地校验，安全价值最高，**必须强制** |
| 13 | 进入 fastboot | ✅ | `flash.py:545-568` `_ensure_fastboot` 已实现并有 `adb reboot bootloader` |
| 14 | `fastboot flash boot` / `init_boot` | ✅ | `flash.py:271-273` 已有成熟封装 + `runner.py:177-184` 严格判定 |
| 15 | **重启设备** | ⚠️ **项目原则禁止** | `cli.py:220-222` 把 `--no-reboot` 标为已废弃（"Lrom 永不自动重启"），`cli.py:802-841` `_print_after_flash_notice` |
| 16 | 开机后安装 KernelSU / APatch Manager APP | ❌ **不可自动** | 需要用户从 GitHub Releases 下载 APK 并侧载。可 `adb install`，但**APK 来源与信任必须用户确认**（离线优先原则下不能由 Lrom 联网下载） |
| 17 | **在 APP 内完成授权 / 刷入 LKM 模块** | ❌ **物理不可自动** | KernelSU Manager 的模块安装、`superkey` 授予，全部在设备 UI 上。**没有任何公开 API**，且这正是 §2.0 死循环的落点 |
| 18 | 校验 root 是否生效 | ✅ | `backup.has_root()`（`backup.py:96-102`）已有 `su -c id` 探测 |
| 19 | 失败回滚（刷回 stock boot） | ✅ 可做，**需新写** | 依赖 #5 的备份 + `fastboot flash`。`cli.py:798` 已经指向一个**尚不存在**的 `lrom restore`（当时确认未实现） |

### 2.2 汇总：自动化上限

```
✅ 可全自动     : 1,2,4,5(部分),12,13,14,18   → 9 项，几乎全是"搬运 + 校验"
⚠️ 需新写代码   : 3,6,7,8,10,11,15,19          → 8 项，是真实工作量所在
❌ 不可自动     : 16,17（+ 9 半自动）           → 3 项，全部卡在"设备端 APP"
```

**能自动化到哪一步：到 `fastboot flash boot` 为止、再往后到"验证 `su` 可用"为止。**
中间那段（用户在手机上下载 APK、打开 APP、点授权）**永远需要人手**。

---

## 3. 两条路线的工程成本对比

### 路线 A：离线 patch boot / init_boot

**做什么**：Python 侧实现 boot image 解析 + ramdisk 编解码 + cpio 增删 + AVB 重算，产出可 flash 的镜像。

| 子模块 | 代码量级 | 难度 | 备注 |
|---|---|---|---|
| boot header v0–v4 解析/序列化 | ~250 行 | 低 | `struct` 就够；v3/v4 的 page_size 恒 4096，v0–v2 是 2048/4096/8192 三档，需按 `header_version` 分支 |
| ramdisk 解压/压缩（gzip/xz） | ~150 行 | 低 | `gzip` / `lzma` 是标准库 |
| **ramdisk LZ4 legacy + LZ4 frame 编解码** | **~400–600 行** | **高** | **LZ4 不在标准库**。纯 Python 实现解压可行（我手写过 LZ4 块解码验证通过），**压缩做不到原厂压缩率** → boot 可能超大 |
| CPIO newc 解析/序列化 | ~300 行 | 中 | 4 字节对齐、inode/uid/gid/mtime 保真、`TRAILER!!!` |
| AVB footer / hashtree / vbmeta 重算 | ~400–600 行 | 高 | 需要 SHA-256 + AVB 结构知识；**如果需重签 RSA 则不可能** |
| 与 root 方案对接（调用 Magisk/KSU 的 patch 逻辑） | ~200 行 | 中 | 实际是"调外部二进制" |
| CLI / 计划 / 校验 / 回滚 | ~400 行 | 低 | 可大量复用 `plan.py` / `flash.py` 的框架 |
| **合计** | **~2,100–2,700 行** | | 相当于现有 `core/` 的一半体量（现有 core 约 1,600 行） |

**风险**：

1. **🔴 最高风险：AVB 重签。** 我在本机备份上实测了两个 vbmeta：
   - `vbmeta.img`（65536 字节）：`avb_vbmeta_header` 的 `auth_block_size = 0`、`aux_block_size = 576`、`flags = 0x0`、
     `rollback_index = 0` —— **无签名块、无 `AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED`(bit0) / `VERIFICATION_DISABLED`(bit1)**
   - `boot.img` 尾部有合法 `AVBf` footer（`vbmeta_offset = 0x220C000`），`init_boot.img` 尾部也有
     （`vbmeta_offset = 0x249000`）
   - `vbmeta.img` 的 aux 区里能搜到 `boot\x00` 描述符名称 → **`boot` 确实被 `vbmeta` 绑定**

   含义：本机 vbmeta **没有禁用校验**。按标准 AVB 逻辑，改动 boot 内容后 `vbmeta` 里的 boot 摘要会失配。
   **但 device 当前 `ro.boot.verifiedbootstate = orange`**（备份清单里实测值），说明设备跑在黄色/橙色状态
   （BL 已解锁）。**已解锁设备是否仍校验 boot 摘要、是否会因失配拒绝启动 —— 这一点我没有实机验证，
   必须标注「需确认」，这是全案最大的技术未知。**

2. **🔴 次高风险：内核/ramdisk 与系统版本强绑定。** `boot.img` patch 后，OTA 升级会把 boot 覆盖回原厂
   → root 丢失（记录的正是这次事故）。必须在文档里显式告知"每次 OTA 后要重新 patch"。

3. **🟡 中风险：boot 分区大小硬上限。** 镜像要写回固定分区。本机 `boot` 备份是 201,326,592 字节（192 MB），
   当前 `boot.img` ramdisk 压缩后仅几 MB，**空间充裕**，但需在代码里硬校验 `patched_size <= partition_size`
   （可用 `fastboot getvar partition-size:boot`，`runner.py:200-203` 的 `getvar` 已具备该能力）。

**与"零第三方依赖"的冲突**：**中等偏低**。
- 解析/CPIO/gzip/xz 全部标准库可做；
- **唯一的硬冲突是 LZ4**。三条出路：
  - (a) 纯 Python 实现 LZ4 **解压**（可行）+ **调用系统 `lz4` 命令压缩**（`apt install lz4`，`find_tool("lz4")` 与现有 adb/fastboot 查找机制完全一致，`tools.py:79-100`）—— **推荐**，与"Lrom 不捆绑 adb/fastboot"的哲学一致（`tools.py:3-5`）；
  - (b) 纯 Python 实现 LZ4 **压缩**（输出合法但体积大 1.5–3 倍）—— 会牺牲 boot 空间安全，**不推荐**；
  - (c) 外挂 `magiskboot` —— 功能最全但引入最大信任面，**最不推荐**（见路线 B）。
- AVB 重算如果只需要改摘要（不含签名），纯标准库可做；**若需要重签则必须外挂 `avbtool` + 私钥 —— 不可能，直接放弃该路线分支。**

### 路线 B：内置内核 / LKM 类

**做什么**：把 root 方案（KernelSU / APatch 的 LKM）直接内置进 Lrom，或"下载现成的 patched boot / 预编译 `.ko`"。

| 子项 | 代码量级 | 可行性 |
|---|---|---|
| 下载/校验第三方预编译 patched boot | ~200 行 | ❌ **违反"离线优先，零遥测"**（项目原则 2） |
| 打包/签名内核 | — | ❌ 需要厂商内核源码 + 交叉编译工具链，**不是 Python 项目该做的事** |
| 在设备上 `insmod` 已 root 后加载的模块 | ~200 行 | ⚠️ 只能在**已有 root** 时用 → 回到 §2.0 死循环，**不能作为首次 root 方案** |
| 校验 `.ko` vermagic 与设备内核匹配 | ~100 行 | ✅ 可做，且有明确价值（正是那次事故的教训） |
| **合计** | **~500 行，但价值极低** | |

**风险**：🔴 **最高**。
- `kernelsu.ko` 必须与内核版本严格匹配。本机内核 `6.1.141-android14-11-o-g5cdab3b518c1`，
  而备份里那个 `.ko` 的 vermagic 是 `6.1.166-dirty` → **已经实证过不匹配**。
- 即使 vermagic 匹配，Android 的 GKI 内核还要求 `kmi` 兼容、`module_layout` 校验、`CONFIG_MODULE_SIG_FORCE`
  等一堆条件 —— **离线无法保证**。
- 内置内核 = 让 Lrom 从"搬运 + 校验工具"变成"发行内核的厂商"，**与项目定位（`README.md` "这不是复刻，只做线刷工具"）直接冲突**。

**与零依赖冲突**：**低**（代码少），但**与项目原则冲突：极高**。路线 B **应当明确否决**。

### 3.1 结论对比

| 维度 | 路线 A（离线 patch） | 路线 B（内置内核/LKM） |
|---|---|---|
| 代码量 | ~2,100–2,700 行 | ~500 行 |
| 首次 root 是否可行 | ✅ 是（唯一可行） | ❌ 否（死循环） |
| 核心风险 | AVB 重签/校验行为；LZ4 压缩率 | 内核版本/ABI 匹配；违反项目定位 |
| 与零依赖冲突 | 中（需 `lz4` 一个外部命令） | 低（代码少）但**原则冲突极高** |
| 与"离线优先"冲突 | 低（除 root 组件下载） | **高**（要联网下预编译产物） |
| **建议** | **唯一值得做的方向** | **否决** |

> 顺带：路线 A 的产物还能**顺带救活两个已停摆项** —— `lrom restore`（用备份回滚，`cli.py:798` 引用了但不存在）
> 和 `arb` 的 root 通路（`arb.py:179-185`）。这条路的边际收益比看起来高。

---

## 4. 测试策略

### 4.1 现状

我用只读命令确认过：`python3 -m pytest tests/unit -q` → **114 个用例全通过，0 真实设备依赖**。
测试按文件分布：`test_flash.py` 36、`test_menu_integrity.py` 18、`test_arb.py` 14、`test_package.py` 13、
`test_plan.py` 11、`test_payload.py` 8、`test_unpack.py` 7、`test_monitor.py` 7。
已有的 mock 范式非常干净：`tests/unit/test_flash.py:21-60` 的 `FakeFastboot`，
**关键设计是 `ok()` 直接复用真实 `FastbootRunner.ok`**（`test_flash.py:50-55`）——"测的是生产代码，不是 mock 自己"。
root 功能的测试必须沿用这个范式。

### 4.2 分层策略

| 层 | 内容 | 能否 mock | 依赖 |
|---|---|---|---|
| **L1 纯单元**（必须做，占 70%） | boot header 解析/序列化往返；CPIO 打包-解包往返；LZ4 解压正确性（用固定字节串断言）；AVB footer 解析；`patched_size <= partition_size` 校验；清单 schema 扩展 | ✅ 全 mock，**用真实镜像做 fixture** | 无设备 |
| **L2 二进制 fixture 回归** | 把 `backups/.../boot.img`、`init_boot.img`、`vbmeta.img` **脱敏后裁成小 fixture**（保留 header + 少量 ramdisk 前缀），断言"解析出的 header_version = 4 / ramdisk 偏移 = 4096" | ✅ 纯本地文件 | 无设备 |
| **L3 设备交互**（`FakeFastboot` / `FakeAdb`） | 断言调用序列确实是 `flash boot <path>`、断言 `FAILED` 输出即使退出码 0 也被判失败、断言**绝不出现 `reboot` 调用** | ✅ 复用 `test_flash.py` 范式 | 无设备 |
| **L4 真机只读** | `fastboot getvar partition-size:boot`、`fastboot fetch boot`、`adb shell getprop`、`su -c id` | ❌ 必须真机 | 真机，**只读，不写** |
| **L5 真机写入**（危险） | 实际 `fastboot flash boot` 并开机 | ❌ 必须真机 + 人工在旁 | 真机 + 用户在场 |

### 4.3 具体建议

1. **把 fixture 做薄。** 不要提交 192 MB 的 `boot.img` 进 git（`.gitignore` 里已有 `backups/`、`unpacked/`）。
   建议做一个 `tests/fixtures/` 生成器：commit 一份**人工构造的 boot image**（30–60 行 Python 生成，
   header_version=4 + page_size=4096 + 压缩的迷你 cpio），这样 L1/L2/L3 全部可跑。
2. **`FakeFastboot` 直接扩展，别新建。** 加 `fetch` / `getvar` 分支即可（对比 `test_flash.py:32-48` 的现有分支结构）。
3. **必须 mock 掉时间与随机。** CPIO 里的 mtime/inode 若不留存会导致"两次 patch 结果不一致"，
   测试里要断言**确定性**（同一输入 → 同一输出 sha256）。
4. **把"永不自动重启"变成测试断言。** `test_menu_integrity.py` 已有类似完整性测试的思路，
   建议新增一条：root 流程全程 `fb.calls` 中不得出现 `["reboot", ...]`。这条测试的价值高于大部分功能测试。
5. **L5 真机步骤：给用户一份"验尸清单"而不是自动化。** 因为失败的表现是"开不了机"，
   必须有 `fastboot flash boot <stock>` 的一行命令可以直接复制（参照 `flash.py:191-194` 的 `_recovery_hint` 设计，
   这个恢复提示机制已经存在，root 流程应当复用）。
6. **测试成本估计**：L1–L3 约 300–400 行测试代码；L4 用 `pytest.mark.skipif(无设备)` 标记，不进入 CI 默认集。

---

## 5. 安全与可回滚设计建议（对齐四条原则）

四条原则；"永不自动重启"的代码体现：`cli.py:220-222`、`cli.py:802-841`。

### 5.1 CLI 形态建议

**命令名不要叫 `root`。** `root` 隐含"我帮你搞定一切"，会误导用户以为全自动。
建议拆成三个动作分明的子命令，与现有 `python -m argparse` 注册方式一致（`cli.py:161-254`）：

```
lrom root check                  # 只读：能不能 root，缺什么
lrom root prepare <boot.img>     # 本地：产出 patched img + 计划（不碰设备）
lrom root apply  <patched.img>   # 写设备：备份 + flash + 提示手动重启
lrom root rollback <backup-dir>  # 回滚：从备份刷回 stock boot
```

理由：现有 CLI 的子命令风格就是"每个动作一个动词"（`inspect` / `unpack` / `plan` / `flash` / `backup` / `arb`），
新增三个名字与之同构；`flash` 本身已经是"计划 + 执行"的复合体，**不要**把 root 塞进 `flash`，
否则会污染现有 36 个 `test_flash.py` 用例的语义。

### 5.2 关键参数（逐个对应原则）

| 参数 | 作用 | 对应的原则 |
|---|---|---|
| **默认 dry-run** | `lrom root apply` **不带 `--yes` 时只打印计划，不写设备** | 原则 3「先出计划后执行」。现有 `flash` 已用 `input("FLASH")` 二次确认（`cli.py:651-674`），root 应更严：**要求输入 `PATCH` 且回显要写哪个分区、哪个镜像、sha256** |
| `--dry-run` | 显式预演，输出结构参照 `dry_run_report`（`flash.py:731-819`） | 同上 |
| `--backup-first`（应为**默认强制**） | 写设备前先 `fastboot fetch` 原厂 boot/vbmeta，失败即中止 | 原则 1「不砖机 > 功能多」 |
| `--slot a\|b` | 明确目标槽位 | 防误写另一槽 |
| `--no-reboot`（**不需要**） | 因为**永不重启**，所以不存在这个参数 | 原则 4 |
| `--accept-external-source <path>` | 必须由用户提供本地 root 组件路径 | 原则 2「离线优先，零遥测」—— Lrom 绝不联网 |

### 5.3 硬性校验（写设备前的 gate，全部本地可做）

按"宁可拒绝"的思路（`package.py:8` 已经写明这个原则），root 流程也应有自己的 `preflight`：

1. `patched.img` 的 `sha256` 与 `root prepare` 阶段清单一致；
2. `patched.img` 尺寸 **≤** `fastboot getvar partition-size:boot`（`runner.py:200-203`）；
3. `patched.img` 能被自己的解析器**重新读一遍**（header + ramdisk + cpio 全部可解）；
4. 设备 `codename` / `build_id` / `kernel_version` 与 `prepare` 时记录的一致 —— **不一致直接拒绝**（这是防"OTA 后拿旧镜像刷"的关键）；
5. 原厂 boot 备份**已存在且 sha256 校验通过**；
6. `ro.boot.verifiedbootstate` 与预期一致（本机 `orange`）；若变成 `green`（已重新上锁）→ 拒绝。

### 5.4 回滚路径（这是整个方案的安全底线）

```
lrom root rollback backups/OP5D77L1_20261026-212432/
  ├─ 读 backup.json（backup.py:265-271）
  ├─ 校验 boot.img 的 sha256（backup.py:88-93）
  ├─ 与设备 codename/build_id 比对（防刷错机）
  ├─ 出计划："将把 X 刷回 Y"，要求用户输入 PATCH 确认
  └─ fastboot flash boot <backup 里的 boot.img>
```

**必须实现 `lrom restore`**（`cli.py:798` 已经在失败提示里引用了它，但它**当前不存在**，
当时也把它列为待办）。root 功能如果上线而 `restore` 不存在，
就等于"给了用户一把没有安全绳的梯子"——**这是我认为最不该出现的交付形态**。

### 5.5 建议的推进顺序（按风险从低到高）

1. **`lrom restore`（\~200 行）** —— 零新增风险，纯复用 `backup.py` + `flash.py`，且**独立就有价值**；
2. **`discover.py` 加 `kernel_version`**（\~30 行）—— 补上 §1.5 的缺口；
3. **`lrom root check`（\~150 行）** —— 只读，把 §2.0 的死循环、分区选择、BL 状态一次性告诉用户；
4. **boot header 解析 + CPIO 工具（\~700 行）**—— 先只做"解析 + 断言"，不做 patch；
5. **实测 AVB 行为（§3 路线 A 风险 1）**——**这一步是 go/no-go 门禁**，在它之前不要写 patch 逻辑；
6. 等等 5 的结论再决定是否做 `prepare` / `apply`。

---

## 6. 未知项清单（必须"需确认"，不能猜）

| # | 未知项 | 影响 | 确认方式 |
|---|---|---|---|
| 1 | **BL 已解锁（`orange`）状态下，boot 摘要失配是否仍允许启动** | 决定路线 A 是否可行 —— **最大未知** | 真机：刷一个故意改动 1 字节的 boot，看是否开机 |
| 2 | `vbmeta` header 的 `flags` 被 `avbtool` 重签后是否等于 3（verify+hashtree disabled） | 决定是否需要改 vbmeta | `avbtool info_image --image vbmeta.img` |
| 3 | 本机首次 root 应 patch `boot` 还是 `init_boot` | 决定写入目标分区 | 解出两边 ramdisk 的 cpio，看 `init` 在哪 |
| 4 | `unpacked/rom/init_boot.img` header 的 `ramdisk_size` 字段是否被改写/混淆 | 影响解析器容错设计 | 与 `backups/` 版本交叉比对 |
| 5 | `boot`/`init_boot` 改判 DANGER 是否会破坏现有 `test_flash.py` 36 个用例 | 影响改动面 | 改一个分支跑一遍测试 |
| 6 | KernelSU / Magisk 哪一版支持 Android 16 + 本机内核 `6.1.141` | 决定注入哪个组件 | 外部调研（不属本任务范围） |
| 7 | OPLUS 的 `fastboot fetch boot` 在 fastbootd / bootloader 下是否都可用 | 影响"写前备份"能否在无 root 时完成 | 真机只读尝试 |

---

## 附录：本次分析实际读过的源码

- `src/lrom/core/backup.py`（全 271 行）、`core/unpack.py`（全 249 行）、`core/plan.py`（全 349 行）
- `src/lrom/core/flash.py`（全 819 行）、`core/package.py`（219-363 行 + 头部）、`core/arb.py`（165-215 行）
- `src/lrom/device/runner.py`（全 266 行）、`device/discover.py`（全 139 行）、`device/tools.py`（全 100 行）
- `src/lrom/device/selfcheck.py`（全 335 行）、`src/lrom/cli.py`（全 935 行）
- `tests/unit/test_flash.py`（1-60 行，`FakeFastboot` 范式）
- `README.md`、`pyproject.toml`
- 实测数据：`backups/OP5D77L1_20261026-212432/{backup.json,boot.img,init_boot.img,vbmeta.img}`、
  `unpacked/rom/{boot.img,init_boot.img}`
- 测试基线：`python3 -m pytest tests/unit -q` → 114 passed（只读命令）

> 本评估**未修改任何源码**，也未执行任何会写设备的命令（无 `fastboot flash`、无 `lrom backup`）。
