# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-10-27

### 新增：主页设备关键信息

菜单头部在型号、版本、槽位之外，补印两行关键状态：

```
● 设备    : OPD2404  [ADB]  槽位 _a
            OPD2404_16.0.5.1201(CN01)
            · Android 16  BL 已解锁
            ! 9008 免授权可能已熔断（不可逆，降级无效）
```

- **Android 版本** —— 判断能否使用某些工具链的前提
- **BL 锁状态** —— 已解锁 / 已锁定 / 无法确认（三态，不猜测）
- **9008 免授权** —— 设备已越过熔断版本时给一行提示，详情留给刷写流程

### 新增：9008 免授权熔断判定（`core/edl_db.py`）

刷入 ROM 时判断是否会让 9008（EDL）免授权永久熔断，**命中即中止刷写**。

**关键事实**（`docs/ARB-实验报告.md` 实测结论）：
9008 免授权一旦熔断即**单向不可逆**，由设备侧决定，刷任何 ROM 都救不回来。

判定规则（只有第一种阻断）：

| 场景 | 处理 |
|---|---|
| 从阈值前**升级跨过**阈值 | ⛔ **中止刷写**，红框说明永久熔断与后果 |
| 设备已越过阈值：同版本重刷 | 提示「不会改变熔断状态」，放行 |
| 设备已越过阈值：继续升级 | 提示，放行（避免被锁死在当前版本） |
| 降级 | 提示「降级**不能**恢复 9008」，放行 |
| 机型未收录 | 无任何提示 |

❌ 绝不输出「降级到某版本可恢复」这类错误结论。

机型识别只认**设备侧**标识（型号 / 代号 / 产品名）。用包里的
product_name 匹配会张冠李戴 —— 换一台未收录机型（如一加 15）
仍会按包的机型弹提示。

判定基于系统版本号推断。实测设备侧 `getprop` 无 edl/sahara/firehose
相关属性，`/sys/class/` 也无对应节点，**无法读取真实熔断状态**，
输出中会明确声明这一点。

### 新增：ROM 包信息补全

解包流程的包信息从 5 行扩充到 11 行：
文件路径、包大小、型号与代号、版本、显示版本、Android 版本、
安全补丁、OTA ID、包类型、分区数与总大小、线刷判定。

### 新增：关键流程的退出入口

解全量包、只刷指定分区、刷写选项三处均可输入 `q` 取消返回主菜单，
防止路径输错或误操作。

### 修复

- **9008 提示张冠李戴**：初版拿包里的 `product_name` 去匹配 9008 库，
  导致接上一加 15（PLK110）时仍按平板的包弹「降级不能恢复 9008」。
  改为只认设备侧标识，并加回归测试锁住。
- **机型标识混用**：型号（`OPD2404`）与代号（`OP5D77L1`）是两种标识，
  只拿代号匹配会全部落到 UNKNOWN，现两种都尝试。
- **阻断范围过宽**：初版对「设备已越过阈值」一律阻断，会把用户
  锁死在当前版本；收敛为只拦「跨过阈值」这一种。

测试 274 -> 298。

## [0.4.8] - 2026-10-27

### 仓库内容精简

#### 清理

| 项目 | 处理 |
|---|---|
| `arb-lab/`（ARB 实验数据，含设备分区镜像） | 删除 |
| `unpacked/`（解包产物） | 删除 |
| `__pycache__/` `.pytest_cache/` `*.egg-info/` | 删除 |

开发记录与踩坑笔记不随仓库发布，移至仓库外本地维护。

测试 274 个通过。

## [0.4.7] - 2026-10-27

### 安全：移除含个人信息的开发文档

早期提交的开发文档含设备序列号、个人邮箱、本机绝对路径等内容，
且其历史版本仍可从 git 历史访问。已重写全部历史并强推远端，
全新镜像克隆扫描确认全历史无残留。

### 文档调整

- **删除**该开发文档
- 新增提炼后的开发笔记，保留全部踩坑记录与开发规范，
  去除个人环境信息（序列号、本机路径、账号）

### 教训

1. `.gitignore` 要在第一次提交前配好，事后删 HEAD 历史仍在
2. 敏感信息一旦进 git 历史，清理代价极大
3. 命令示例用 `$HOME` / `$LROM` 占位符，不写真实路径

## [0.4.6] - 2026-10-27

### 新增：ARB 熔断机型数据库 —— 降级刷写直接阻断

用户要求：「把调查出来能在早期版本通过 9008 免授权刷机的机型和最后的
系统版本写死，后续识别到这些特定机型降级刷写直接阻断线刷并给予提示」。

新增 `core/arb_db.py`，把社区实测的熔断数据硬编码进 Lrom。

#### 为什么硬编码

1. **离线优先** —— 核心链路不依赖服务器，熔断拦截必须始终有效
2. **数据变化极慢**（一年几个版本），不值得为它开网络依赖
3. **它是最后一道兜底** —— 用户没跑 `lrom arb` 就直接 flash 时还能拦住

#### 收录的机型（13 条记录）

| 机型 | 型号代码 | 触发熔断版本 |
|---|---|---|
| **一加平板 Pro / Pad 2** | OPD2404 / OPD2403 | **16.0.3.501** |
| 一加 13 | PJZ110 / CPH2649 / CPH2653 / CPH2655 | 16.0.3.501 |
| 一加 13T / 13s | PKX110 / CPH2723 | 16.0.3.501 |
| Ace 5 / 一加 13R | PKG110 / CPH2645 / CPH2691 | 16.0.3.500 |
| 一加 12 | PJD110 / CPH2573 / CPH2581 | 16.0.3.500 |
| Ace 3 Pro | PJX110 | 16.0.3.500 |
| Ace 6 | PLQ110 | 16.0.5.700 |
| OnePlus Pad 2 Pro | OPD2413 | 16.0.10.600 |
| OnePlus Pad 3 | OPD2415 | 16.0.10.600 |
| OPPO Find X7 Ultra 等 | — | 16.0.0.500（**仅警告**）|

数据来源：大侠阿木固件站触发版本 + [Bartixxx32/Oneplus-antirollchecker](https://github.com/Bartixxx32/Oneplus-antirollchecker) ARB index 实测。

#### 可信度分级 —— 避免误伤

每条记录带 `confidence` 字段：

- **CONFIRMED**（固件站 + 追踪表交叉验证）→ **硬阻断**
- **REPORTED**（单一来源，如 XDA 主帖）→ **只警告不阻断**

同时维护 **安全表** `ARB_SAFE_MODELS`，收录实测 ARB=0 的机型：

- **一加 15**（媒体常误报熔断，实测全版本 ARB=0）
- **Ace 5 Pro**（媒体说熔断，实测 ARB=0）
- 一加 15R/15T/12R/11/11R、Ace 6T/3V/3、OnePlus Open 等

**在安全表里的机型永不阻断**，哪怕版本号看起来吓人。

#### 判定逻辑

1. 机型在安全表 → SAFE
2. 命中熔断记录：
   - 包版本 < 触发版本 → **BLOCK**（降级到熔断前 = 黑砖）
   - 包版本 ≥ 触发版本，但 < 设备当前版本 → **BLOCK**（实为降级）
   - 否则 SAFE
3. 未收录机型 → UNKNOWN（不阻断，提示去 `lrom arb` 或 oparb.pages.dev 确认）
4. 版本号读不出来 → WARN（保守警告，不硬拦）

#### 接入位置

- **`lrom flash`** —— 设备探测后、BL 锁检查前，BLOCK 直接 return 1
- **`lrom plan`** —— 预演阶段就拦，避免「预演通过、真刷才失败」

用户可见输出：

```
✗ ARB 熔断保护：拒绝刷写

  ⛔ 这个包是 16.0.3.501 之前的版本，而该机型从那时起已熔断 ARB。
    刷入等于降级到熔断前，会导致**永久黑砖**，且免授权 9008 已失效，
    无法自救。

  为什么必须停手：
    · 这些机型的 ARB 已从 0 升到 1，降级刷入会直接黑砖
    · 同时免授权 9008 已失效，没有自救通道
    · 只能拆字库或换主板
```

#### 真机验证

用用户的真实场景测试（出厂包 `OPD2404_14.1.0.226` 刷到已熔断的
`OPD2404_16.0.5.1201`）：

- `lrom plan 出厂版本.zip` → **正确阻断，退出码 1**
- `lrom plan rom.zip`（同版本）→ **正确放行，无误伤**

#### 顺带修的一个正则 bug

初版 `parse_build_version` 用 `(\d+)\.(\d+)\.(\d+)`，会把 Android
构建号 `UKQ1.231108.001` 里的 `1.231108` 误识别成业务版本号。
改为要求前后不能紧跟字母数字，并限定前两段 1-2 位、第三段 1-4 位。

**这个 bug 会导致熔断判定完全错乱，是写测试时发现的。**

### 测试

238 → **274**（新增 36 个，`test_arb_db.py`）：
- 版本解析（含 Android 构建号误判的回归测试）
- 数据库完整性（CONFIRMED 必须有型号代码）
- **真实场景**：出厂包被拦、同版本放行、升级放行
- 机型矩阵：8 个机型 × 不同版本组合
- 安全表保护：一加 15 / Ace 5 Pro 不被误伤

## [0.4.5] - 2026-10-27

### 依据社区实测情报做的一轮加固

用户提供了一份一加刷机注意事项（社区/售后/XDA 整理），逐条对照后发现
7 个问题。本版本修复其中 6 个，按风险排序。

### 🔴 修复：备份清单漏了 OCDT —— 最严重

旧清单：

```python
DEFAULT_BACKUP_PARTITIONS = ("boot", "init_boot", "vbmeta", ...)
```

**没有 OCDT**。而社区情报明确写：

> 用非本机的 OCDT 会导致**进不了 Fastboot**
> OCDT 损坏 → 售后也无法救砖 → **只能拆字库**

用户跑完 `lrom backup` 以为备份齐了，最危险的那个分区却没备。
**真正救不回来的砖，绝大多数是这个分区没了。**

实测确认本机有**两个**相关分区，都已加入清单并排在最前：

| 分区 | 大小 | 说明 |
|---|---|---|
| `ocdt` | 128 KB | 裸名，**无槽位** |
| `engineering_cdt` | 1 MB × 2 槽 | 有 `_a`/`_b` |

同时补上 `persist`（指纹/传感器/校准，不可再生）和 `fsg`（信号）。

并新增 `OPTIONAL_PARTITIONS`：`fsg` 等分区在部分机型不存在
（**实测本机就没有 fsg**），缺失不算失败，避免整轮备份报红。

备份成功后会对 OCDT 单独强调：

```
★ engineering_cdt（OCDT）已备份。
  这份文件是本机独有的 —— 恢复时**必须**用它，
  用别的设备的 OCDT 会导致进不了 Fastboot。
  建议另存到 U 盘/云盘，不要只留在这台电脑上。
```

### 🔴 修复：`ocdt` 被错误地加上槽位后缀

`_partition_name()` 会把裸分区名补成 `xxx_a`，但 **`ocdt` 在分区表里是裸名**。
实测：`partition-type:ocdt` 存在，`ocdt_a` 不存在。
误加后缀会去找不存在的节点，备份直接失败。

新增 `_NO_SLOT_PARTITIONS` 集合，`ocdt` / `cdt` / `persist` / `frp` 等保持裸名。

### 🔴 修复：`lrom arb` 会挂起 6 分钟

**实测**：无设备时 `read_device_arb_fastboot()` 对 3 个分区名各尝试一次
`fastboot fetch`（每次 `timeout=120`），而 fastboot 在没有设备时会无限
等待 `< waiting for any device >`，只能靠外部 timeout 杀掉。

而且**这台设备本来就不支持 fetch**（实测 `Does not support fetch command`），
照样要等满超时。

修复三条：
1. **先确认设备在线** —— 不在线立刻返回，绝不进入 fetch
2. **先探测 `max-fetch-size`** —— 不支持立即返回，不逐个分区试
3. **单次超时大幅缩短** —— 15 秒探测 / 60 秒总预算

**实测效果：从挂起 6 分钟 → 0 秒返回。**

顺带让 ARB 结论更有用：读不到设备侧时，会明确提示包侧 ARB 值的含义：

```
注意：本包 xbl_config 的 ARB = 1。
      ARB ≥ 1 表示该版本已引入防回滚熔断 ——
      一旦刷入，将无法再降级到 ARB 更低的版本（不可逆）。
```

### 🟡 新增：fastboot 版本检查

社区情报：**Platform-Tools < 35.0.0 刷 super 等大分区会很慢、易超时**。
`selfcheck` 原本只打印版本号，不判断。现在低于 35.0 会明确警告。

**实测本机就是 `fastboot version 34.0.5-debian`，已触发警告。**

### 🟡 新增：大分区改用 `-S` 分块刷写

情报：「大分区单次传输撑爆 USB 缓冲 → 卡在 waiting for device」，
解法是 `fastboot -S 100M`。

现在超过 **512 MB** 的分区自动加 `-S 100M`，中小分区保持原样
（分块对它们只是额外开销）。

### 🟡 新增：解包前检查磁盘空间

`lrom unpack` 原本不检查空间，可能解到一半磁盘满，留下半截文件更难处理。
现在会先用 payload manifest 算出所需空间（留 5% 余量）再判断。

**实测本机全量包解出约 9.66 GB。**

### 附带发现：有 root 后设备侧 ARB 能读到了

收尾实测时意外发现 `lrom arb` 读到了设备侧数值：

```
包内 xbl_config : ARB=1 (version 3.0)
设备当前        : ARB=1 (version 3.0)
结论: 安全（版本相同）
```

之前一直读不到，**不是硬件限制，而是因为设备没有 root**。
真实情况是：

| 设备状态 | 能否读 ARB |
|---|---|
| 无 root + ADB | ❌ |
| 无 root + fastboot | ❌（本机 bootloader 不支持 fetch）|
| **有 root + ADB** | ✅ **能读** |

**root 顺带解锁了 ARB 完整检测能力**，这也让「做 root」本身多了一条收益。
`lrom arb` 的提示文案已相应更新。

### 测试

212 → **238**（新增 26 个）：
- `test_backup.py`（23 个）—— **新建**，锁住 OCDT 必须在清单、
  必须排第一、`ocdt` 不能加槽位等
- `test_arb.py` 新增 3 个 —— 无设备立即返回、不支持 fetch 时短路探测

## [0.4.4] - 2026-10-27

### 修复：刷入卡死（设备不会自动进入 fastboot）

**现象**：跑到 `[4/5] 刷入` 这一步就卡住不动，设备停在系统里没有进 fastboot。

**原因**：`flash_patched_image()` 直接调 `fastboot flash`，
但此时设备往往还在 ADB（开机）模式。fastboot 找不到设备就无限空等。

而 `core/flash.py` 里早就有成熟的 `_ensure_fastboot()` 做这件事
（处理 adb / fastboot / fastbootd 三种状态），我写 root 时另写了一套也没接上。
**这是继 v0.4.3「菜单漏接入口」之后，第二类「有现成能力却没复用」的错误。**

**修复**：新增 `root.ensure_fastboot()`：

| 设备状态 | 动作 |
|---|---|
| 已在 fastboot | 直接继续 |
| 在 fastbootd | `fastboot reboot bootloader` |
| 在 ADB（开机） | `adb reboot bootloader` ← 之前缺这一步 |
| 未检测到 | 提示 + 等待，超时给排查指引 |

刷写步骤现在会先打印模式切换提示，过程中也能看到
「设备在 ADB 模式，正在重启到 bootloader…」。

**真机验证**（OnePlus OPD2404）：
```
>> 设备在 ADB 模式，正在重启到 bootloader…
>> 正在写入 init_boot…
结果: True | 刷写完成
```

### 修复：回滚 Root 不会自动进入 fastboot

菜单 9「回滚 Root」原本是 `if not fb.devices(): 提示手动进入`，
设备在 ADB 模式时直接放弃。改为复用 `flash_patched_image()`，
自动切模式后刷写。

**真机验证**：ADB 模式下执行回滚 → 自动切 fastboot → 刷回原厂 →
重启后 `kernelsu` 模块消失，root 已移除 ✅

### 新增：刷 ROM 前检查 Bootloader 锁

用户提出：防止在「临时 root」状态下误刷。

**为什么必要**：锁定的设备刷写会失败在中途或只写进去一半，
比「提前拒绝」危险得多。而「临时 root」（`fastboot boot` 临时启动
而非持久刷入）容易让人误以为已解锁。

**`lrom flash`**：在设备探测后、生成计划前检查，未解锁直接拒绝并说明原因。

**`lrom plan`**：预演阶段也检查 —— 否则预演通过、真刷才失败，白跑一趟。

**`lrom root`**：原来只在 ADB 模式检查（fastboot 下 `locked` 是 None 就跳过），
现在补上明确的「无法确认」提示。

### 修复：fastboot 模式下读不到解锁状态

`discover.detect()` 在 fastboot 模式下根本没读锁状态，
只有 ADB 模式读 `ro.boot.flash.locked`。但真正刷机时设备往往已经在 fastboot 里。

新增 `_read_fastboot_unlocked()`，用 `getvar unlocked`（返回 `yes`/`no`），
统一成与 ADB 相同的 `"0"`=已解锁 / `"1"`=已锁定 语义。

**真机验证**：fastboot 下 `detect()` 正确返回 `locked='0'`，
`describe()` 显示「Bootloader: 已解锁」。

### 顺带：测试性能

新增的模式切换测试会让 `_wait_for_fastboot` 里的 `sleep(2)` 真实执行，
导致整套测试从 10 秒涨到 60+ 秒。
把轮询间隔抽成模块级 `_POLL_INTERVAL`，加 autouse fixture 在测试里设为 0，
恢复到 **8.7 秒**。

### 测试

203 → **212**（新增 9 个）：
- 模式切换 6 个（含防回归 `test_flash_calls_ensure_fastboot_first`）
- Bootloader 锁检测 4 个

## [0.4.3] - 2026-10-27

### 修复：交互菜单里没有 Root 入口

用户反馈：v0.4.2 虽然加了 `lrom root` 命令，但**交互菜单里看不到**。
原因是只实现了 CLI 子命令，忘记接进 `tui/menu.py`。

这正是 v0.3.1 那次「菜单崩溃」事故的同类问题 ——
**功能做了但没接上入口**，用户根本用不到。

新增「Root」菜单节：

```
  ─ Root ────────────────────────────────────────
   7) 获取 Root          patch init_boot 并刷入
   8) 下载 Root 管理器   从 GitHub 下载 APK（唯一联网）
   9) 回滚 Root          刷回原厂 init_boot
```

原「工具」节顺延为 10–13。

三个新菜单项：
- **7 获取 Root**：定位 `unpacked/rom/init_boot.img` → 显示提示 →
  进入 `cmd_root`（含刷入模式交互选择）
- **8 下载 Root 管理器**：列出 4 个来源 + 双渠道选择 → `cmd_fetch_manager`
- **9 回滚 Root**：列出 `root-work/` 下的原厂备份 → 确认 → `fastboot flash`

### 新增菜单完整性测试（4 个）

**`test_menu_entry_count_matches_dispatch`** —— 解析菜单渲染出的编号
与 `handlers` 分发表的键，**两者必须完全一致**。
这条能自动防住「菜单加了编号但忘记接分发」的错误，正是本次 bug 的成因。

其余三条覆盖：处理函数存在性、分发表包含 Root 三项、渲染文本含 Root 节。

### 顺带修复

- 菜单 8 里「正在查询最新版本…」重复打印两次
- 「关于」页的原则 2 补充说明「唯一联网：root 管理器下载」

### 测试

193 → **197**（新增 4 个）

## [0.4.2] - 2026-10-27

### 真机验证通过：OnePlus OPD2404 首次 root 成功

2026-10-27 在真机上完整跑通了 `lrom root --method app` 全流程。

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
| 内核模块 | ✅ `/proc/modules`: `kernelsu 225280 ... Live (O)` |
| KernelSU APP | ✅ **「工作中」LKM**，显示设备实际内核版本 |

#### 修正此前的两个错误推断

**① vermagic 不一致并不影响加载**

patch 出来的 `kernelsu.ko` 的 vermagic 是 `6.1.166-dirty`，设备内核是
`6.1.141` —— **不一致，但模块照样加载成功**。

原因：该 `.ko` 与官方 `lkm-aarch64-android14-6.1_kernelsu.ko`
**sha256 逐字节相同**（413296 字节）。KMI 兼容就够了，
vermagic 里的 SubLevel 不参与判定。

这推翻了项目早期「vermagic 不匹配所以没生效」的推断
—— 那次失败的真正原因是流程没走通，不是 vermagic。

**② `su -c id` 从 adb shell 失败是正常现象**

KernelSU 默认不授予 `shell` uid 的 root，需要在 APP「超级用户」里手动添加。
**这不影响 root 是否存在**。验证应以模块加载 + APP 状态为准。

#### 关于「APP 方式必须人工」的确认

KernelSU Manager 的「Select a file」是纯 UI 交互，没有对外 API，
也没有 exported Activity 能传参。用 `adb shell input tap` 按坐标点属于
脆弱的黑盒 hack（分辨率/版本一变就废，误点有风险），违背「不砖机 > 功能多」。

**结论：APP 方式必然有一步人工，这是 KernelSU 的设计决定的。**
要全自动请用 `--method ksud`。

### 真机验证与关键结论

真机 root 验证通过，并修正了两处此前的错误推断：
- vermagic 不一致不影响模块加载，KMI 兼容即可
- `su -c id` 从 adb shell 失败是正常现象，KSU 默认不授予 shell uid

确认 APP 方式必然有一步人工（KernelSU Manager 的文件选择是纯 UI 交互，
无对外 API；按坐标点按属脆弱黑盒 hack），要全自动请用 `--method ksud`。

## [0.4.1] - 2026-10-27

### 新增：`lrom fetch-manager` —— 唯一的联网命令

下载 root 管理器 APK，始终取 GitHub 最新发布，支持正式版/测试版双渠道。

```bash
./lrom fetch-manager --source ksu --channel stable
./lrom fetch-manager --source ksu --channel beta
./lrom fetch-manager --source magisk --channel beta
```

来源：`ksu`（KernelSU 官方）/ `ksu-next` / `sukisu` / `magisk`。

#### 为什么这次允许联网

用户明确要求：「修改协议，标明联网只从 GitHub 下载 root 管理器，
无恶意远程代码」。因此本版本把「离线优先」原则正式修订为：

> 离线优先，零遥测 —— 核心链路不依赖任何服务器。
> 唯一的联网是用户显式发起的 root 管理器下载。

并把这条承诺**写死在代码里**，不只是写在文档里：

| 约束 | 实现 |
|---|---|
| 域名白名单（仅 3 个） | `_check_url()` 在发请求**之前**校验 |
| 强制 HTTPS，不降级 | `_check_url()` 检查 scheme |
| 无摘要拒绝下载 | `download_asset()` 强制要求 `digest` |
| 校验失败立即删文件 | 不通过直接 `unlink()` |
| 只下载不执行 | 远程数据只当文件写盘 |
| 不发送设备信息 | 请求头只有 `User-Agent: lrom/<版本>` |
| `lrom root` 主流程永不联网 | 联网仅存在于 `fetch-manager` |

每条约束都有对应测试（`test_manager_fetch.py`，29 个）。

### 新增：`--flash-mode` —— 刷入通道由使用者选择

```bash
./lrom root --flash-mode fastboot    # bootloader（常规）
./lrom root --flash-mode fastbootd   # 用户空间
./lrom root                          # 不指定则交互询问
```

**为什么需要 fastbootd**：用户指出 —— **OCDT 分区被恶意脚本抹掉后
bootloader 进不去，fastboot 模式完全不可用**。fastbootd 由系统里的
用户空间服务启动，不依赖 OCDT，此时是唯一还能刷写的通道。
`init_boot` 是物理分区，两种模式都能写。

按用户要求，Lrom **不替用户判断走哪条**，交给使用者决定。

### 修复：asset 选择逻辑会误选架构包

写测试时发现：`_APK_EXCLUDE` 里用 `arm64-v8` 想排除架构包，
但排除不掉 `armeabi-v7a`，导致多个架构包同时留下后被误判为「唯一通用包」。

改为独立的 `_ARCH_PATTERN` 正则，多个候选时**只有恰好一个不带架构字样**
才采用，否则报错列出全部候选让用户用 `--asset` 指定——**宁可报错不猜错**。

### 测试

155 → **193**（新增 38 个）：
- `test_manager_fetch.py`（29 个）：域名/HTTPS 白名单、摘要强制、
  校验失败清理、asset 选择、渠道解析
- `test_root.py` 新增 9 个：flash-mode 传递、模式探测、fastbootd 切换、
  OCDT 场景提示

### 实测记录

- `fetch-manager --source ksu` 真实下载 v3.3.1（13.0 MB），
  sha256 校验通过
- 双渠道实测：KSU beta 无预发布版时正确退回 v3.3.1；
  Magisk beta 正确取到 v31.0（prerelease）
- 顺带确认 release 里确有 `lkm-aarch64-android14-6.1_kernelsu.ko`
  与 `ksud-x86_64-unknown-linux-musl`，印证设备 KMI 判定正确

## [0.4.0] - 2026-10-27

### 新增：`lrom root` —— 自动化 root

用全量包里已有的 `init_boot.img` 建立 root。**整个流程离线、不联网**。

```bash
./lrom root --dry-run              # 先看计划
./lrom root                        # ksud 方式，全自动
./lrom root --method app           # 用手机上的 Manager APP 修补
```

#### 背景：为什么会踩「静默失败」的坑

Magisk 的 `boot_patch.sh` 在找不到 ramdisk 时**不报错**，
而是从零造一个空 ramdisk：

```sh
else
  # No ramdisk found, create one from scratch
  RAMDISK=ramdisk.cpio
```

结果是 **patch 显示成功、fastboot 刷入显示 OK、开机也正常，但完全没有 root**。

而本项目的目标设备 `boot.img` 的 `ramdisk_size` 实测就是 **0**
（GKI 2.0 起通用 ramdisk 移到了 `init_boot`）。照着网上教程去
patch `boot.img` 必然掉进这个坑。

**`lrom root` 的第一件事就是把这种镜像拦下来**，
并明确告诉你应该改用 `init_boot`。

#### 新增模块

**`core/bootimg.py`** —— boot image 只读解析
- 支持 header v0–v4（v0–v2 按 page_size 对齐，v3–v4 固定 4096）
- 识别 ramdisk 压缩格式：gzip / lz4_legacy / lz4_frame / xz / lzma / zstd / cpio
- 识别 AVB footer 与镜像逻辑长度
- `vendor_boot` 明确拒绝（不支持直接 patch）

**`core/root.py`** —— root 流程
- `derive_kmi()`：从内核版本推导 KMI
- `build_root_plan()`：生成可审阅的计划
- `save_stock_image()`：原厂镜像留存 + 清单
- `patch_with_ksud()` / `push_for_app_patch()` / `pull_app_patched()`
- `verify_patched_image()`：刷前校验
- `flash_patched_image()`：复用 `FastbootRunner` 的严格判定

#### 安全设计（对应四条铁律）

| 原则 | 落实 |
|---|---|
| 不砖机 > 功能多 | patch 前强制查 ramdisk；刷前强制校验；**留存原厂镜像在刷写之前** |
| 离线优先 | 素材全部来自本地，Lrom 不联网下载任何东西 |
| 先出计划后执行 | 每个写设备动作都先出计划 → 确认 → 执行，支持 `--dry-run` |
| 永不自动重启 | 刷完后停下，打印手动重启命令 |

失败时自动打印恢复命令：
`fastboot flash init_boot root-work/init_boot-stock-<时间>.img`

#### 关键：KMI 必须按内核版本推导，不能按 Android 版本

本机设备是 **Android 16**，但内核是 `6.1.141-android14-11-o-...`，
所以 KMI 是 **`6.1-android14-11`**，不是 `android16-6.12`。

内核版本跟随设备**出厂**时的 Android，后续 OTA 不会改内核。
选错 KMI 必然安装失败——社区已有一加平板 A16 试错两轮的实例。

`lrom root` 会自动读 `uname -r` 推导，也可 `--kmi` 手动覆盖。

#### ksud 不打包

`ksud` 是 KernelSU 官方提供的命令行 patch 工具，Lrom **不捆绑**它
（保持零依赖、免安装）。按现有 `adb`/`fastboot` 的同一套机制查找：

1. 环境变量 `LROM_KSUD`
2. 配置文件 `~/.config/lrom/config.json`
3. 系统 PATH
4. 常见目录

下载：https://github.com/tiann/KernelSU/releases

#### 实测记录（2026-10-27，OnePlus OPD2404 / Android 16）

在做功能前的**探测实验**中确认：

| 项 | 结果 |
|---|---|
| 改过内容的 `init_boot` 能否启动 | ✅ 正常开机（30 秒） |
| `verifiedbootstate` | `orange`（未变红、未拒绝） |
| 是否需要动 `vbmeta` | ❌ 不需要 |
| `init_boot` 是否锁区 | ✅ 可正常刷写 |
| `fastboot fetch` | ❌ 设备不支持，回滚底图改从 ROM 包取 |

结论：**已解锁设备改 `init_boot` 后能正常启动，AVB 不拦截**。
这是此前最大的未知项，现在有真机实证。

#### 另一个实测发现：`backups/` 里的 init_boot 不是原厂

对比两份镜像的 CPIO 内容：

```
backups/.../init_boot.img   → 多出 kernelsu.ko / ksu_config / init.real
                              init 从 3MB 缩到 603KB
unpacked/rom/init_boot.img  → 21 个标准 ramdisk 条目，无 KSU 痕迹
```

即 `backups/` 那份**本身是一次 KSU patch 的产物**，且其中的
`kernelsu.ko` 的 vermagic 是 `6.1.166-dirty`（设备内核 `6.1.141`）。

这解释了一个悬案：上次 root 失败**不是没刷进去**，而是
**patch 成功、刷入成功，但内核拒绝加载版本不对的 .ko**。
早期「模块本来就没生效过」的记录现在有了镜像级证据。

**回滚底图必须用 `unpacked/rom/init_boot.img`。**

#### 测试

新增 41 个测试（总计 114 → **155**）：

| 文件 | 覆盖 |
|---|---|
| `test_bootimg.py`（15 个） | header v0–v4 解析、ramdisk 检查、压缩格式识别、AVB footer、错误分支 |
| `test_root.py`（26 个） | KMI 推导、计划生成、原厂留存、patch 后校验 |

关键回归测试：
- `test_check_ramdisk_blocks_boot_image` —— 防止 patch 无 ramdisk 的分区
- `test_derive_kmi_android16_kernel_still_android14` —— 防止按 Android 版本选错 KMI
- `test_derive_kmi_sublevel_not_part_of_kmi` —— 锁住「SubLevel 不属于 KMI」

## [0.3.2] - 2026-10-27

### 修复：菜单顶部设备状态不是实时的

**现象**（用户实测）：

```
  ○ 设备    : 未连接        ← 顶部还是旧的
  ...
  请选择:
  [设备] 已连接 OPD2404 [adb]   ← 底部打印了提示，但顶部没变
```

**原因**：`print_header()` 只在绘制菜单时调用一次，
之后程序阻塞在 `input()` 上。设备插拔虽然被监测线程捕获，
但顶部区域不会重绘。

**修复**：
- 新增 `input_with_redraw()`：用 `select` 以 0.2 秒超时轮询 stdin
- 无输入时每 1.2 秒调用一次重绘回调，刷新整个菜单（含设备状态）
- 重绘时保留用户已敲入的字符
- 只在真实 TTY 启用；管道/重定向自动退回普通 `input()`
- 移除底部重复的「已连接/已断开」提示行（顶部已实时反映）

**验证**：pty 模拟真实终端，实测 5 秒内重绘 4 次。

### 修复：完整性测试误报

`test_no_undefined_module_level_calls` 把关键字参数名
（如 `f(redraw=...)` 中的 `redraw`）误判为未定义函数。
现已排除关键字的参数名。

## [0.3.1] - 2026-10-27

### 修复：菜单点击部分选项直接崩溃

**现象**（真机实测）：

```
请选择: 7
  设备序列号: ...
  ...
  出错了: name 'pause' is not defined
NameError: name 'pause' is not defined
```

**原因**：v0.3.0 重构 menu.py 时用脚本批量改动，
`ask` / `ask_path` / `confirm` / `pause` 四个输入辅助函数被误删。
Python 语法检查发现不了（名字在运行时才解析），
所以静态检查通过、发布后用户一点就崩。

**修复**：
- 补回四个输入辅助函数
- 修复 `_menu_arb` 对已删除的 `_require_package` / `_require_images` 的引用

### 新增：菜单完整性测试（防止复发）

`tests/unit/test_menu_integrity.py`（18 个用例）：

- `test_no_undefined_module_level_calls` — AST 解析，找出所有被调用但未定义的名字
- `test_required_helpers_exist` — 校验 ask/ask_path/confirm/pause 等基础设施存在
- `test_menu_handlers_signature` — 每个菜单处理函数必须是 `(sess, mon)`
- `test_all_handlers_referenced_in_run_menu` — run_menu 注册的处理函数必须真实存在

已实测验证：临时删掉 `pause` 后，该测试立即失败并给出准确提示。

测试总数 96 → 114。

## [0.3.0] - 2026-10-27

### 变更：交互流程重构（按用户反馈）

**1. 启动先检测环境**
运行 `./lrom` 后第一步是系统环境检测，逐项显示 adb / fastboot / udev /
用户组 / adb server / USB 设备。缺失项会给出可直接复制的安装命令，
并支持 `(r)` 修复后重新检测 / `(c)` 继续使用离线功能 / `(q)` 退出。

**2. 「解全量包」内嵌选择 ROM 包**
原来的「3. 选择 ROM 包」是独立菜单项，与「1. 解全量包」割裂。
现在合并为一步：进入「解全量包」直接询问包路径（支持拖拽，回车沿用上次），
随后自动完成分析 → 型号核对 → 解包。

**3. 设备状态实时监测**
菜单顶部的设备信息原来是启动时的一次性快照，用户拔掉数据线后
仍显示"已连接 OPD2404"，容易造成误判。

现在由后台线程每 1.5 秒轮询一次：
- 拔线后菜单立即显示「○ 设备: 未连接」
- 重新插上后自动恢复
- 状态变化时会打印提示行（不清屏，不打断阅读）
- 危险操作（线刷）前会强制 `refresh_now()` 再确认一次，不使用滞后的快照

菜单结构也相应调整：

```
1) 解全量包    2) 预演检查    3) 线刷全量包    4) 只刷指定分区
5) 备份设备分区  6) ARB 熔断检测
7) 设备信息    8) 重启设备    9) 环境自检     10) 关于
```

### 新增

- `device/monitor.py`：设备实时监测（后台线程 + 状态变化回调）
- `DeviceMonitor.refresh_now()`：危险操作前同步刷新
- 7 个监测模块单元测试

## [0.2.1] - 2026-10-27

### ⚠️ 重要修复：双槽刷写会撑爆 super（真实事故）

**事故经过**：用户使用 `--both-slots` 刷写，在 fastbootd 阶段出现：

```
my_stock     ✓ 3,831.0 MB
my_stock_b   ✓ 3,831.0 MB
odm          ✓ 1,627.1 MB
odm_b        ✗ Not enough space to resize partition
```

**根因**：`super` 是**单个物理分区**，两个槽位的逻辑分区**共用同一空间**。

| 项 | 容量 |
|---|---|
| `super` 总容量 | 14.00 GB |
| 单槽逻辑分区需求 | 8.97 GB |
| 双槽合计 | 17.94 GB |
| **超出** | **约 4 GB** |

`--both-slots` 只适用于**物理分区**（boot/xbl/vbmeta 等），
逻辑分区（system/vendor/odm/my_* 等，位于 super 内）**绝对不能**双槽刷写。

**修复**：
- `_flash_other_slot()` 遇到 `Phase.FASTBOOTD` 直接返回，逻辑分区不再双槽刷写
- `super` 分区本身也排除
- 新增回归测试 `test_both_slots_does_not_apply_to_logical`

### 新增

- **刷写进行中的实时提示**
  大分区刷写耗时长（`my_stock` 约 105 秒），期间屏幕无输出，
  用户容易误以为卡死而拔线。现在每个超过 20 MB 的分区刷写时，
  会每秒钟刷新 spinner 与已用时间、预估剩余时间：

  ```
  ⠹ my_stock         刷写中…  3,831.0 MB   42.3s  剩余约 63s
  ```

- **super 空间预检**
  刷写前读取设备 `super` 分区容量（root 或 `getvar partition-size:super`），
  与逻辑分区总需求比较，不足时在**预检阶段直接阻止**，
  而不是刷到一半才报错。

### 修复

- 逻辑分区双槽刷写导致 super 空间耗尽（详见上文）

## [0.2.0] - 2026-10-27

### 新增

- **交互式菜单**
  无参数运行 `./lrom` 进入菜单，共 12 项：解全量包 / 分析包 / 选择包 /
  预演检查 / 线刷全量包 / 只刷分区 / 备份 / ARB 检测 / 设备信息 /
  重启设备 / 环境自检 / 关于。
  顶部常驻设备状态与当前 ROM 包；记住上次使用的包；支持拖拽文件进终端。

- **免安装运行**
  新增 `./lrom` 启动脚本，自动选择 Python 3.10+ 并设置 PYTHONPATH。
  克隆即可运行，无需 venv / pip / 写 PATH。卸载即删除目录。

- **`lrom selfcheck` 环境自检**
  检查 adb/fastboot、udev 规则、用户组权限、adb server 归属、USB 设备可见性，
  每项附可直接复制的修复命令。

- **`--both-slots` 双槽刷写**
- **逻辑分区写入前 delete + create**（清理 COW 快照层）
- **FastbootD 切换超时提示**

### 修复

- **菜单信息一闪而过**
  两处原因：`os.system("clear")` 每次返回菜单都清屏，且 `_menu_inspect`
  与 `_menu_flash` 缺少暂停。现在只在真实 TTY 清屏，并补齐所有暂停点。

- **解包目录识别错误**
  菜单在 `work/<包名>` 查找镜像，但解包默认输出到 `unpacked/<包名>`，
  导致已解包仍提示"尚未解包"。现在自动在多个常见位置查找。

- **install.sh 引导不清**
  明确说明"Linux 内核自带 USB 驱动，不需要装驱动"，
  区分驱动与命令行工具，给出各发行版准确包名与无 sudo 方案。

### 变更

- **预演检查重写**
  原先只是逐条打印 47 个分区的流水账，信息量低。
  现改为回答四个关键问题：刷写规模、镜像完整性、风险项、数据影响，
  并给出明确结论（能不能刷）。

- `install.sh` 改为可选（会先说明不需要安装）
- 新增 `uninstall.sh`

## [未发布]

### 新增

- **逻辑分区写入前先 delete + create**
  直接 `fastboot flash` 逻辑分区时，若目标分区不存在或容量不足会失败；
  虚拟 A/B 的 COW 快照层（`xxx_a-cow` / `xxx_b-cow`）也可能残留旧数据。
  现在会先 `delete-logical-partition`（4 种命名）再 `create-logical-partition`（两槽），
  然后才写入。可用 `--no-recreate-logical` 关闭。
- **双槽刷写 `--both-slots`**
  每个分区刷完当前槽位后，再补刷另一槽位，避免切换槽位后回退到旧版本。
- **FastbootD 切换超时提示**
  OPLUS 设备的 fastbootd 可能显示语言选择界面并暂停。现在 15 秒未就绪会提示
  "屏幕若有语言选择请手动点选"，超时后给出明确的排查步骤。等待上限提升至 300 秒。

### 变更

- 参考 [koaaN/Universal_Flasher](https://github.com/koaaN/Universal_Flasher) 的实战做法补齐上述细节

## [0.1.0] - 2026-10-26

首个可发布版本。已在真实设备（OnePlus OPD2404 / Android 16）上完成端到端刷机验证。

### 新增

**包分析（`lrom inspect`）**
- 解析 `payload.bin` manifest（遵循 AOSP 官方 `update_metadata.proto` 语义）
- 读取 OTA 元数据：产品名、设备代号、版本、安全补丁、OTA 类型
- 全量包 / 增量包判定
- 包结构自洽性校验（每个操作的数据长度与 extent 是否匹配）
- 设备型号核对，不匹配时硬阻止

**刷前备份（`lrom backup`）**
- 通过 adb + root `dd` 备份关键分区：boot / init_boot / vbmeta×3 / dtbo / recovery
- 生成 `backup.json` 清单，记录每个分区的大小、SHA-256 与设备指纹
- 备份前校验磁盘空间与 root 权限

**刷写计划（`lrom plan`）**
- 纯数据结构，可审阅、可导出 JSON
- 三级风险分类：`NEVER_OVERWRITE` / `DANGER` / `SAFE`
- 自动判定逻辑分区（走 FastbootD）与物理分区（走 Fastboot）
- modem 强制最后刷写

**解包（`lrom unpack`）**
- 支持 REPLACE / REPLACE_BZ / REPLACE_XZ / ZSTD / ZERO
- 零拷贝读取 STORED 的 payload.bin，流式解压不占内存
- 每个操作校验 `data_sha256`，损坏立即报错
- 增量操作直接拒绝，绝不输出残缺镜像

**ARB 熔断检测（`lrom arb`）**
- 解析 `xbl_config` ELF64 → HASH 段 → OEM metadata → `arb_index`
- 设备侧读取支持 adb+root 与 fastboot fetch（均不需要额外工具）
- 降级时硬阻止，防止硬件级变砖

**刷写（`lrom flash`）**
- 两阶段刷写：Fastboot（物理分区）→ FastbootD（逻辑分区）
- `--dry-run` 预演：完整校验流水线，不写设备
- 自动完成 ADB → Fastboot → FastbootD 的模式切换与等待
- 关键分区锁定自动识别并跳过，不中止流程
- 失败立即中止，附可复制的恢复命令
- `--wipe` 清空用户数据（降级/救砖）

### 安全设计

- **永不自动重启设备** — 重启决策权留给用户
- 刷写后强制提示：降级必须先格式化，否则变砖
- 需输入完整 `FLASH` 才执行，不用 `y/n`
- fastboot 退出码为 0 但输出含 `FAILED` 时判定失败
- 零遥测，完全离线

### 已知限制

- 未实现 `unlock_critical`，13 个关键分区（xbl/abl/tz/hyp 等）在未解锁关键分区的设备上无法写入（会自动跳过）
- 增量 OTA 包明确不支持
- 未实现 `lrom restore` 与 `lrom patch`（恢复 root）
- 仅支持全量 OTA 包，小米 tgz / OFP / OPS / OZIP 待实现

### 验证

- 设备：OnePlus OPD2404（一加平板 Pro），Android 16，A/B 双槽
- 系统：Kali GNU/Linux Rolling 2026.2
- 结果：`16.0.5.702` → `16.0.5.1201`，34 个分区写入成功，用户数据保留，设备正常开机
- 71 个单元测试通过
