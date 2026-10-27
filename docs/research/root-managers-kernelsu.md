# KernelSU 系（内核级）root 管理器调研

> 调研目的：为 Lrom（Linux 原生 Android 线刷工具，v0.3.2）后续「自动化 root」功能选型。
> 目标设备：OnePlus OPD2404（一加平板 Pro），代号 `OP5D77L1`，SD 8 Gen 3（高通 `pineapple` / SM8650），
> Android 16（SDK 36），内核 `6.1.141-android14-11-o-g5cdab3b518c1`，A/B 双槽，BL 已解锁，当前无 root。
>
> 调研时间：2026-10-27。所有 star 数 / 日期均来自当日实际抓取的页面或接口，**未查到的写「未确认」**。
> 数据来源两类：GitHub REST API（`api.github.com`，用于 star 数、`pushed_at`、release 资产清单）和
> GitHub / 项目官网页面（用于安装文档原文）。文中每条结论后附可点击 URL。

---

## 0. 一句话结论（先看这个）

| 问题 | 结论 |
|---|---|
| Lrom 该对接哪个项目 | **官方 `tiann/KernelSU` 的 LKM 模式**（最规范、有命令行 `ksud`、有明确的 KMI 规则），备选 `KernelSU-Next` |
| 能自动化到哪一步 | **能自动化到「patch 出 init_boot.img + flash + 校验」**；**卡在两点**：① `.ko` 必须与内核 ABI 匹配，通用 `.ko` 在 OPPO 系设备上大概率加载失败；② 必须替换 `init_boot`（不是 `boot`），且必须由用户手动备份/确认 |
| 最关键的一个坑 | 本项目设备内核是 `6.1.141`，**属于 KMI `android14-6.1`**，不是 `android16-6.12`。照「Android 16 → android16-6.12」直觉选 `.ko` 必翻车，社区已有同类设备的失败案例（[SukiSU #815](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/815)）。详见 §6 |

---

## 1. 项目全景对比

### 1.1 精确仓库地址与活跃度

数据抓取自 GitHub REST API `GET /repos/{owner}/{repo}`（2026-10-27）：

| 项目 | 精确 owner/repo | Star | Fork | 最近 push（`pushed_at`） | 创建时间 | 说明 |
|---|---|---|---|---|---|---|
| **KernelSU（官方）** | [`tiann/KernelSU`](https://github.com/tiann/KernelSU) | **19,676** | 4,745 | 2026-10-25 | 2022-12-09 | 上游本体，GPL-3.0 |
| **KernelSU Next** | [`KernelSU-Next/KernelSU-Next`](https://github.com/KernelSU-Next/KernelSU-Next) | **4,627** | 1,225 | 2026-10-25 | 2025-01-11 | 上游 fork，主打 SUSFS / 旧内核回移 |
| **SukiSU-Ultra** | [`SukiSU-Ultra/SukiSU-Ultra`](https://github.com/SukiSU-Ultra/SukiSU-Ultra) | **6,686** | 1,376 | **2026-10-26** | 2025-03-29 | fork，含 KPM（内核模块注入） |
| **5ec1cff/KernelSU（MKSU）** | [`5ec1cff/KernelSU`](https://github.com/5ec1cff/KernelSU) | **681** | 95 | 2026-09-01 | 2023-03-16 | 个人 fork（Magic Mount），非独立发行版 |
| **rsuntk/KernelSU（RKSU）** | [`rsuntk/KernelSU`](https://github.com/rsuntk/KernelSU) | **664** | 101 | 2026-07-22 | 2024-06-24 | 自称 "Personal fork of KernelSU Project" |
| **ReSukiSU** | ⚠️ **已改名**：`ReSukiSU/ReSukiSU` → 302 重定向到 [`Baka-SU/BakaSU`](https://github.com/Baka-SU/BakaSU) | **2,000**（BakaSU） | 未取到 | 2026-10-26 | — | 见 §1.3，**核实结果与原任务假设不同** |
| **APatch** | [`bmax121/APatch`](https://github.com/bmax121/APatch) | **8,093** | 800 | 2026-10-23 | 2023-08-21 | 非 GKI-only，走 KernelPatch，见 §7 |

> 说明：star 数是快照，会随时间变化；fork 数仅列作量级参考。
>
> **数据可信度交叉验证**：上表数字先由 `api.github.com` 的 `GET /repos/{owner}/{repo}` 取得；
> 事后又从仓库主页 HTML 内嵌的 `"stargazerCount"` 字段独立复核（如 `tiann/KernelSU` = 19676、
> `SukiSU-Ultra/SukiSU-Ultra` = 6686，两处完全一致），因此不是从导航噪声里误抓的数字。

### 1.2 最近 release 与最近提交

| 项目 | 最新 release | 发布日期 | Release 里有哪些资产 |
|---|---|---|---|
| KernelSU | [`v3.3.1`](https://github.com/tiann/KernelSU/releases/tag/v3.3.1) | 2026-10-12 | APK + `ksud`（android/linux/mac/win）+ `lkm-aarch64-android14-6.1_kernelsu.ko` 等 8 个 KMI × 2 架构 |
| KernelSU Next | [`v3.4.2`](https://github.com/KernelSU-Next/KernelSU-Next/releases/tag/v3.4.2) | 2026-10-13 | `aarch64_android14-6.1_kernelsu.ko` 等 |
| SukiSU-Ultra | [`v4.2.1-r2`](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.1-r2) | 2026-10-24 | `ksud-*`、`ksuinit-*`；APK 在 `manager-ci` 预发布 tag 下 |
| BakaSU（原 ReSukiSU） | 见 [Releases 页](https://github.com/Baka-SU/BakaSU/releases) | 未确认 | ReSukiSU 时期**不在 GitHub Release 发 manager**，只走 [nightly.link](https://nightly.link/ReSukiSU/ReSukiSU/workflows/build-manager/main/Manager-release.zip) 与 CI artifacts（[官方说明](https://resukisu.github.io/guide/install.md)） |
| APatch | tag `11282` | 2026-10-15 | 仅 `APatch_..._release-signed.apk` |

最近提交（`GET /repos/{r}/commits?per_page=3`）：

- KernelSU：`75e179d3` 2026-10-25（上游仍在日常维护）
- KernelSU Next：`b427a3f9` 2026-10-19
- SukiSU-Ultra：`2ec445ff` 2026-10-26（最活跃）
- BakaSU 前身 ReSukiSU CI：`ReSukiSU_37180105212`（2026-10-04）

### 1.3 ⚠️ 任务里「ReSukiSU / 5ec1cff / rsuntk」三者需要更正

任务描述把 `5ec1cff/KernelSU`、`rsuntk/KernelSU`、`ReSukiSU` 并列成「同一个」，实际是**三个不同东西**：

| 名字 | 实际情况 | 依据 |
|---|---|---|
| `5ec1cff/KernelSU`（俗称 **MKSU**） | 真实存在，但只是个人 fork，作用是「Magic Mount」来源。 **不是** ReSukiSU | [仓库](https://github.com/5ec1cff/KernelSU)；SukiSU README 的 Credit 写「MKSU: Magic Mount」 |
| `rsuntk/KernelSU`（俗称 **RKSU**） | 真实存在，自称个人 fork，主打非 GKI 支持。旧 release 有 `v3.2.2-10-legacy` 等 | [仓库](https://github.com/rsuntk/KernelSU) |
| **ReSukiSU** | 真实存在，但**已更名为 BakaSU**。`ReSukiSU/ReSukiSU` 的 GitHub 页面 og:description 直接写「Contribute to **Baka-SU/BakaSU** development」，README 首行「Formerly known as: ReSukiSU」 | [ReSukiSU/ReSukiSU](https://github.com/ReSukiSU/ReSukiSU) → [Baka-SU/BakaSU](https://github.com/Baka-SU/BakaSU)；文档站 [resukisu.github.io](https://resukisu.github.io/) |

ReSukiSU/BakaSU 的血统（[官网](https://resukisu.github.io/)）：「A KernelSU downstream project, **derived from the SukiSU Ultra branch**」。即：**KernelSU → SukiSU-Ultra → ReSukiSU/BakaSU**。

> 对 Lrom 的意义：**不要**把 ReSukiSU 当作独立技术路线；它是 SukiSU 的分支的再分支。选型时只需在「官方 / Next / SukiSU」三者里挑。

---

## 2. 安装形态：支持哪些方式

| 项目 | 内置内核（编译进 `Image`） | **LKM（`.ko` 动态加载）** | 替换 GKI `boot.img` | AnyKernel3 zip |
|---|---|---|---|---|
| KernelSU | 支持（编译内核时 `setup.sh`） | ✅ 自 v0.9.0 起 | ⚠️ **已弃用**（v3.0 起官方文档明确「dropped official support for GKI image mode」） | ✅（给 TWRP / Kernel Flasher 用） |
| KernelSU Next | 支持 | ✅ | ✅（Next 仍提供 GKI 镜像） | ✅ |
| SukiSU-Ultra | 支持（`builtin` 分支） | ✅（官方推荐） | ✅（Method 2） | ✅ |
| BakaSU | 支持 | ✅ | ✅ | ✅ |
| APatch | ❌（不用 GKI） | ❌ | 走 KernelPatch 打 `boot.img` | ❌ |

依据：
- KernelSU 双模式与 LKM/GKI 定义：[kernelsu.org/guide/installation.html](https://kernelsu.org/guide/installation.html)（原文「Since version 0.9.0, KernelSU supports two running modes on GKI devices: LKM / GKI」）
- KernelSU 弃用 GKI 镜像模式：[kernelsu.org/guide/how-to-build.html](https://kernelsu.org/guide/how-to-build.html)（原文「Since KernelSU v3.0, we have dropped official support for GKI image mode ... It is recommended to use Ylarod/ddk to build LKM」）
- SukiSU 四种安装法：[sukisu.org/guide/installation](https://sukisu.org/guide/installation)
- Next 安装页只给了**内核源码集成**（`setup.sh`）路径：[kernelsu-next.github.io/webpage/pages/installation.html](https://kernelsu-next.github.io/webpage/pages/installation.html)

> **关键事实**：官方 KernelSU 现在的「正道」就是 **LKM**。这决定了自动化方案的核心是「patch `init_boot.img` 塞入 `.ko`」，而不是「换内核」。

---

## 3. 官方 KernelSU：LKM 安装的实际事实步骤

来源：[kernelsu.org/guide/installation.html](https://kernelsu.org/guide/installation.html)（原文摘录 + 中文归纳）

### 3.1 前提检查（必须先做）

1. 安装 KernelSU Manager APK（`KernelSU_v3.3.1_32680-release.apk`，来自 [Releases](https://github.com/tiann/KernelSU/releases/tag/v3.3.1)）。
2. 打开 APP 看状态：
   - 显示 **`Not installed`** → 设备被官方支持（有对应 KMI 的 `.ko`），可以走 LKM。
   - 显示 **`Unsupported`** → 没有现成 `.ko`，**官方不会提供 boot.img**，只能自己编内核。
3. **备份 stock boot.img**。文档原文：「Before flashing, it's essential that you back up your stock boot.img … you can always restore the system by flashing back to the stock factory boot using fastboot.」

### 3.2 LKM 三条实际路径

文档把 LKM 安装拆成三种操作，**这三种就是自动化要覆盖的范围**：

| 路径 | 文档原文的行为 | 是否需要 root | 是否需要 APP | 外部可自动化？ |
|---|---|---|---|---|
| **Select a file**（选文件 patch） | 用户选一个官方固件，manager 自动 patch，用户自己刷 | ❌ 不需要 | ✅ 需要 | ⚠️ 半自动（APP 内点按钮） |
| **Direct install** | manager 自己抓设备信息 → patch → **自动刷入** | ✅ 需要（已 root） | ✅ 需要 | ❌ 首次不适用 |
| **Install to inactive slot** | A/B 设备专用，patch 后写入另一槽 | ✅ 需要 | ✅ 需要 | ⚠️ OTA 保 root 用 |

### 3.3 ★ 最重要的一条：LKM 改的是 `init_boot`，不是 `boot`

文档原文：

> Unlike GKI mode, LKM mode modifies the **ramdisk**. Therefore, on devices with Android 13, it needs to patch the **`init_boot`** partition instead of the `boot` partition, while GKI mode always operates on the `boot` partition.

**对 Lrom 的直接后果**：本项目设备是 Android 16（≥13），自动化必须 patch `init_boot.img`。这正好和 Lrom 现有能力吻合——Lrom 已经从 `payload.bin` 里解出 47 个分区镜像，`init_boot` 大概率就在里面，**不需要额外下载固件**。

### 3.4 ★ 第二条：有真正的命令行工具 `ksud`，不需要 APP

文档原文（「Use the command line」一节）：

> If you don't want to use the manager, you can also use the command line to install LKM. The `ksud` tool provided by KernelSU can help you quickly patch the official firmware and then flash it. This tool supports **macOS, Linux, and Windows**.

实测 `ksud boot-patch -h` 的输出（文档内嵌原文）：

```
Usage: ksud boot-patch [OPTIONS]
Options:
  -b, --boot <BOOT>      Boot image path. If not specified, it will try to find the boot image automatically
  -k, --kernel <KERNEL>  Kernel image path to be replaced
  -m, --module <MODULE>  LKM module path to be replaced. If not specified, the built-in module will be used
  -i, --init <INIT>      init to be replaced
  -u, --ota              Will use another slot if the boot image is not specified
  -f, --flash            Flash it to boot partition after patch
  -o, --out <OUT>        Output path. If not specified, the current directory will be used
  --magiskboot <PATH>    magiskboot path. If not specified, the built-in version will be used
  --kmi <KMI>            KMI version. If specified, the indicated KMI will be used
```

**这对 Lrom 是决定性的**：
- `ksud` 有 **`x86_64-unknown-linux-musl`** 与 **`x86_64-pc-windows-gnu.exe`** 预编译（见 v3.3.1 资产清单），Linux 开发机可直接跑；
- 参数 `-b <init_boot.img>` + `-m <kernelsu.ko>` + `-o <out.img>` **完全可脚本化**，**不必联网、不必装 APP**；
- `--magiskboot` 允许指定外部 `magiskboot`（Lrom 若想控制 repack 细节，可以自己带一个）。

> 注意：`-f/--flash` 是「刷到 boot 分区」，对 `init_boot` 场景不要用 `-f`，应让 `ksud` 只产出文件，再由 Lrom 用 fastboot 刷 `init_boot` 分区——这样 Lrom 现有的刷写执行器与风险分级仍然生效。

### 3.5 关于「是否要联网下载 `.ko`」

- **用 APP** → 联网。APP 按设备 KMI 自动挑 `.ko`（`Select a file` 路径），本质是内置/下载对应 KMI 模块。
- **用 `ksud`** → 文档原文「If not specified, **the built-in module** will be used」，即 `ksud` 自带内置模块；也可 `-m` 显式指定。
- **对 Lrom 最稳的做法**：Lrom 自己按 KMI 去 release 下载对应的 `lkm-aarch64-<KMI>_kernelsu.ko`，**显式**传给 `ksud -m`。这样版本可控、可离线缓存（Lrom 本就是零依赖 + 本地操作风格）。

### 3.6 卸载 / 还原

文档原文（「Back up the stock boot image」一节）：

- `Direct install` 时会自动备份 stock boot/init_boot 到 **`/data/adb/ksu/ksu_backup_$SHA1`**，SHA1 记录在已 patch 的镜像里。
- 卸载入口：manager 的 **「Uninstall → Restore stock image」**，若有匹配的 SHA1 备份则直接还原。
- 自 **3.3.0** 起，首次无 root 时用 `Select a file` patch 后可勾「Backup as stock image」，备份先存 manager 内部存储，首次开机后自动搬到 `/data/adb/ksu`。

**结论**：还原「能用 stock `init_boot.img` 覆盖」——这一点是成立的，只需 `fastboot flash init_boot <stock_init_boot.img>`。
但文档里的「Uninstall」按钮依赖 APP + 已备份文件，**对 Lrom 不是必需**：Lrom 只要**自己保存一份 stock `init_boot.img`**（反正刷机包里就有），还原就是一次 fastboot 刷写，完全不依赖 APP。

补充（[Rescue from bootloop](https://kernelsu.org/guide/rescue-from-bootloop.html)）：
- 刷错格式/不兼容镜像 → 「you can recover by flashing the stock boot image」。
- LKM 模式的额外保障（文档列在 LKM 优点里）：「Since it doesn't need to replace the boot partition, it won't trigger AVB and won't cause the device to be bricked」「LKM can be temporarily uninstalled … This process doesn't require flashing partitions, or even rebooting」。
- 模块导致的砖机可用 Safe Mode：首次开机画面后**连按音量下 3 次**（press-release ×3）。

---

## 4. 各 fork 的安装文档差异

### 4.1 KernelSU Next

- 官网安装页**只教内核源码集成**：[kernelsu-next.github.io/webpage/pages/installation.html](https://kernelsu-next.github.io/webpage/pages/installation.html)（`curl ... setup.sh | bash -s <branch>`）。**LKM 的对外文档在 Next 官网上未找到**，属「未确认」。
- README 宣称支持内核 **4.4 ~ 6.18**，5.10+ 走「pre-built images and LKM/KMI」（[README](https://github.com/KernelSU-Next/KernelSU-Next)）。
- **有设备白名单页**：[Unofficially supported devices](https://kernelsu-next.github.io/webpage/pages/devices.html)，里面列了 3 个 OnePlus 定制内核来源：
  - [`WildKernels/OnePlus_KernelSU_SUSFS`](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS)（731 star，2026-10-26 更新）
  - [`Numbersf/Action-Build`](https://github.com/Numbersf/Action-Build)（1,382 star）—— 明确写「Build SukiSU-Ultra/KernelSU/KernelSU-Next/BakaSU Kernels For **All OnePlus QCOM & MTK Devices (GKI2.0)**」
  - [`Bouteillepleine/OnePlus-KsuNext_NMS`](https://github.com/Bouteillepleine/OnePlus-KsuNext_NMS)（26 star）
- Next README 有一条 CAUTION：新内核在 **x86_64** 上可能导致 kernel panic（与本项目 arm64 无关，但说明其稳定性边界）。

### 4.2 SukiSU-Ultra

文档质量最好，且有**分机型说明**（[sukisu.org/guide/installation](https://sukisu.org/guide/installation)）：

Method 1（推荐，**LKM via Manager**）原文步骤：
1. 装 Manager（`SukiSU_*.apk`）；
2. 点首页 `Tap to install`；
3. 选 boot 镜像来源：`Direct install` / `Select a file` / `Download a file and patch`（喂 URL）/ **`Use local LKM file`（只接受 `.ko`）**；
4. `Select the KMI`：manager 显示设备 KMI，需要时手动挑；
5. flash 并重启。

预编译 LKM 覆盖的 KMI（原文）：
> android12-5.10, android13-5.10, android13-5.15, android14-5.15, **android14-6.1**, android15-6.6, android16-6.12 and android17-6.18（aarch64 与 x86_64 各一份）

Method 3 原文（**对 OnePlus 的专门说明**）：
> Method 3: Custom Build for OnePlus Devices — For OnePlus devices, you'll need to create a custom build.

Method 2 的脚注（**最关键的警告，见 §6**）：
> This method is not suitable for devices from manufacturers that heavily modify the kernel, like Meizu, **OnePlus, Realme, and Oppo**.

OTA 保 root：LKM 模式下「after the OTA has been installed and **before you reboot**, start the Manager's installer and choose `Install to inactive slot`」——**对 A/B 设备很友好**。

卸载（[Troubleshooting](https://sukisu.org/guide/troubleshooting)）：文档只提到「卸载 Manager 卡死」时一并卸载 `com.sony.playmemories.mobile`，**没有给出 LKM 卸载/还原流程**——属「未确认」。

### 4.3 ReSukiSU / BakaSU

[resukisu.github.io](https://resukisu.github.io/) 有一条**专门给 OPPO 设备**的页面 [`/guide/install/oppo.md`](https://resukisu.github.io/guide/install/oppo.md)，原文：

> ## LKM Installation
> LKM installation is identical to that on a standard GKI device.
>
> ## GKI Installation
> Due to OPPO's modifications to `f2fs`, **generic GKI images cannot be used**. You need to compile a kernel yourself from OPPO's open-source code.

**这就是整个调研里最有价值的一句话**：OPPO 系设备**不能**用通用 GKI 镜像（因为 OPPO 改过 `f2fs`），但 **LKM 路径仍然可行**（等同于标准 GKI 设备）。

LKM 实际步骤（[`/guide/install/gki.md`](https://resukisu.github.io/guide/install/gki.md)）：
> After installing ReSukiSU Manager, tap `Not Installed` → 选 **LKM patching/installation** → 选与当前固件匹配的 `boot` / `init_boot` / `vendor_boot` 镜像 → Manager 按设备 KMI 选 LKM 文件、patch 镜像、把 `KernelSU_patched_*.img` 写到下载目录 → 刷入对应分区 → 重启并在 Manager 确认。

同类提示：「Devices that require `vendor_boot` patching are quite rare, so normally only the `init_boot` file needs to be patched.」

### 4.4 三个 fork 的共同点与差异

| 维度 | KernelSU | Next | SukiSU | BakaSU |
|---|---|---|---|---|
| LKM 可用 | ✅ | ✅ | ✅（推荐） | ✅ |
| 提供 Linux 版命令行 patch 工具 | ✅ `ksud`（musl 静态） | 未确认 | 有 `ksud-*` 资产，用法未确认 | 未确认（不发 release） |
| 明确写「OPPO/OnePlus 不要用通用 GKI 镜像」 | ❌ 文档未提 | ❌ 文档未提 | ✅（Method 2 脚注） | ✅（专门 OPPO 页） |
| 明确写「OPPO 可用 LKM」 | 隐含（KMI 通用） | 未确认 | 隐含 | ✅ 显式 |
| 有 OnePlus 现成内核构建 | ❌ | ✅ 社区页 | ✅（Method 3） | 未确认 |

**判断**：对**自动化**而言，**官方 `tiann/KernelSU` 的 `ksud` 是唯一有清晰 CLI 契约的方案**；SukiSU / BakaSU 的价值是「文档承认 OPPO 现实」，但它们的 patch 能力绑定在 APP 里。

---

## 5. 安装过程的可自动化性拆解（核心交付物）

以「官方 KernelSU LKM + Android 16 + A/B」为基准，逐步标注自动化可行性：

| # | 步骤 | 谁做 | 自动化 | 说明/风险 |
|---|---|---|---|---|
| 1 | 确认 BL 已解锁 | 设备 | ✅ `fastboot getvar unlocked` | Lrom 已有 `flash.locked=0` 检测 |
| 2 | 读设备内核版本 → 推导 KMI | Lrom | ✅ `adb shell uname -r` | **必须自己做字符串解析**，见 §6 |
| 3 | 取 stock `init_boot.img` | Lrom | ✅ 从 `payload.bin` 解出 | Lrom 已有 `unpack.py`；无需联网下载固件 |
| 4 | 下载匹配 KMI 的 `.ko` | Lrom | ✅ `curl` release 资产 `lkm-aarch64-<KMI>_kernelsu.ko` | 需联网一次；可缓存 |
| 5 | 下载 `ksud`（musl 静态） | Lrom | ✅ `ksud-x86_64-unknown-linux-musl` | 无依赖，符合 Lrom 零依赖风格 |
| 6 | `ksud boot-patch -b init_boot.img -m x.ko -o out.img` | Lrom | ✅ **纯命令行** | 关键一步，见 §3.4 |
| 7 | 校验产物（大小/magic/是否含 `.ko`） | Lrom | ✅ 本地计算 sha256 | 建议加；Lrom 已有 sha256 校验范式 |
| 8 | `fastboot flash init_boot out.img` | Lrom | ✅ | 用 Lrom 现有刷写执行器 |
| 9 | `fastboot reboot` | Lrom | ✅ | A/B 需注意 `fastboot --set-active` |
| 10 | 确认 root 生效 | Lrom | ⚠️ 部分 | `adb shell su -c id` 需要**弹窗授权**（首次 `su` 会要求 APP 点「允许」）→ **人工一步** |
| 11 | 安装 Manager APK | Lrom | ✅ `adb install` | 但 APP 只在「授权/看日志」时才需要，**不是安装必需** |

**必须设备端手动 / 无法自动化的部分**：

| 卡点 | 为什么 | 依据 |
|---|---|---|
| 首次 `su` 授权 | KernelSU 的授权在 manager APP 内确认，CLI 无「静默预授权」；`su` 首次调用必然弹窗 | 需 APP 交互，[安装文档](https://kernelsu.org/guide/installation.html)全流程都围绕 manager |
| 用户确认「我知道会变砖风险」 | 刷 `init_boot` 失败会导致不进系统；文档反复强调先备份 | [Rescue from bootloop](https://kernelsu.org/guide/rescue-from-bootloop.html) |
| 安全补丁级别（SPL）闸门 | 文档明确警告：设备有 anti-rollback，刷入 SPL 更旧的镜像「may cause bootloop」 | [安装文档 KMI/SPL 一节](https://kernelsu.org/guide/installation.html) |
| 若 `.ko` ABI 不匹配 | 需要自己编内核（几十 GB 源码 + 交叉编译）→ 超出 Lrom 定位 | 见 §6 |

> **一句话**：Lrom 可以把 1–9 步做成「一键 patch + 一键刷」，第 10 步之后交给用户（在 APP 里点一下「允许」）。

---

## 6. ★ 内核版本匹配：为什么 `.ko` 会「装不上」

这一节是本调研对 Lrom 最有实操价值的部分。

### 6.1 KMI 的定义（官方原文）

[kernelsu.org/guide/installation.html](https://kernelsu.org/guide/installation.html)：

> KMI: **Kernel Module Interface**. Kernel versions with the same KMI are compatible, this is what "general" means in GKI.
>
> For GKI devices, the kernel version format is `Version.PatchLevel.SubLevel-AndroidRelease-KmiGeneration-suffix`, i.e. `w.x.y-zzz-k-something`. **`w.x-zzz-k` is the KMI version.**
>
> e.g. `5.10.101-android12-9-g30979850fc20` → KMI = `5.10-android12-9`.
>
> ⚠️ Note that the **SubLevel is not part of the KMI**! `5.10.101-android12-9-...` has the **same KMI** as `5.10.137-android12-9-...`

### 6.2 本项目设备的 KMI 推导（★ 关键陷阱）

设备内核：`6.1.141-android14-11-o-g5cdab3b518c1`（来源：设备实测 `adb shell uname -r`）

按上面规则逐段拆：

| 段 | 值 | 含义 |
|---|---|---|
| `Version.PatchLevel` | `6.1` | 内核主版本 |
| `SubLevel` | `141` | **不属于 KMI**，所以 `.141` vs `.118` vs `.166` 都不是「不匹配」的理由 |
| `AndroidRelease` | `android14` | **跟随出厂时搭载的 Android**，不是当前的 Android 16 |
| `KmiGeneration` | `11` | 代次 |
| suffix | `-o-g5cdab3b518c1` | OPPO 厂商后缀 + git hash |

→ **KMI = `android14-6.1`**

**为什么不能选 `android16-6.12`**：官方文档专章解释「Kernel version vs Android version」：

> Please note: Kernel version and Android version **aren't necessarily the same**! … The Linux kernel version is generally correspondent to the version of the Android system that comes with the device **when it was shipped**. If the Android system is upgraded later, **the kernel version will generally not change**.

设备出厂是 Android 14 平台内核线（`android14-...`），后续 OTA 到 Android 16 只改了 userland，内核仍是 6.1 —— 所以 KMI 停留在 `android14-6.1`。

### 6.3 官方与各项目提供的 KMI 清单（含 `android14-6.1` ✅）

KernelSU `kernel/build-all.sh` 里的 KMI 列表（源码，[build-all.sh](https://github.com/tiann/KernelSU/blob/main/kernel/build-all.sh)）：

```
KMIS="android12-5.10 android13-5.10 android13-5.15 android14-5.15 android14-6.1 android15-6.6 android16-6.12 android17-6.18"
```

v3.3.1 release 资产中确实存在 **`lkm-aarch64-android14-6.1_kernelsu.ko`**（[release 页资产清单](https://github.com/tiann/KernelSU/releases/tag/v3.3.1)）→ **本项目设备有对应的官方 `.ko`**。

SukiSU 官方文档列出的 KMI 清单同样包含 `android14-6.1`（[sukisu.org/guide/installation](https://sukisu.org/guide/installation)）。

### 6.4 那为什么会「模块不匹配装不上」？—— 四个真实原因

| 原因 | 机制 | 证据 |
|---|---|---|
| **A. 选错 KMI**（最常见） | 看到「Android 16」就去拿 `android16-6.12`，而设备是 `android14-6.1`。`.ko` 是按另一份内核符号/`vermagic` 编的，加载时符号解析失败 → 内核拒绝加载 → manager 仍显示 Not installed | 社区实例：[SukiSU #815](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/815)：OnePlus Pad 2 Pro / A16 / kernel 6.6，「tried android 15-6.6 and android16-6.12, both failed」→ `manager showed not installed` |
| **B. 子版本严格性**（项目间不同） | KMI 本身只看 `w.x-zzz-k`，但**厂商定制内核**（如 OPPO WildKernels 系列）要求更严格：其兼容性文档写明 `XX-YY.ZZ` **必须精确匹配**，`AAA`（子版本）至少相同或更大 | [WildKernels compatibility.md](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/blob/main/compatibility.md)：「If your device is not in list, please select the device which is most similar and matches the kernel version (androidXX-YY.ZZ.AAA). AAA must be atleast same or greater. XX-YY.ZZ must match exactly.」 |
| **C. `.ko` 的 vermagic / 内核配置不匹配** | `vermagic` 编码了内核版本、SMP/preempt 等配置位；厂商内核常带 `-dirty` 后缀与非标准 config。本项目现状就是一个真实例子：备份里的 `kernelsu.ko` vermagic 是 `6.1.166-dirty`，与设备 `6.1.141` 不符，所以「本来就没生效过」 | 项目内实测记录；KMI 与配置要求见官方文档 |
| **D. 刷错分区**（`boot` vs `init_boot`） | Android 13+ LKM 改的是 **ramdisk**，即 `init_boot`；刷进 `boot` 不生效，甚至开不了机 | 官方原文：LKM mode「on devices with Android 13, it needs to patch the `init_boot` partition instead of the `boot` partition」 |

**补充：`CONFIG_MODULES` 闸门**。LKM 既然要「动态加载 `.ko`」，内核就必须允许加载外部模块。OPPO 系内核普遍会 `blacklist` 掉 `kernelsu` 模块名（WildKernels 的配置里 `blacklist_modules` 明确含 `kernelsu`），说明厂商层面对此有防范——这既是「为什么现成 `.ko` 可能被拒」的原因之一，也解释了为什么 OPPO 用户更多走「自编译内核 + AnyKernel3」路线。

### 6.5 安全补丁级别（SPL）闸门

官方文档警告：

> Newer Android devices may have anti-rollback mechanisms that prevent flashing a boot image with an old security patch level. … if the security patch level is older than 2023-11, it may cause bootloop.

对 Lrom 的实操含义：**不要**从旧固件包里拿 `init_boot`；必须用**与设备当前版本一致**的包里那份（本项目包里就是 `OPD2404_16.0.5.1201(CN01)`，与设备匹配，正好合适）。

### 6.6 加载时机的一个易忽略点

官方 LKM 优点里提到：

> The volume key listener is registered during kernel module initialization (loaded when the kernel executes the `init` process in LKM mode).

即 LKM 是**在 `init` 阶段由 patch 过的 ramdisk 加载**的，所以「`.ko` 打进了 `init_boot` 的 ramdisk」这件事必须成立——`magiskboot repack` 阶段一旦丢文件，表现就是「装完没 root」而不是明确报错。

---

## 7. 旁证：APatch 属于同类但不依赖 GKI

[`bmax121/APatch`](https://github.com/bmax121/APatch)（8,093 star，2026-10-23 更新）。README 要点：

- 原理：**依赖 [KernelPatch](https://github.com/bmax121/KernelPatch)**，「patching of Android kernel and Android system」，**不打 GKI 通用内核、不加载 `.ko`**，而是直接 patch `boot.img` 里的内核。
- 支持范围：「Only supports the **ARM64** architecture」「Only supports Android kernel versions **3.18 – 6.12**」。→ **本项目 6.1.141 在范围内**。
- 硬性内核配置要求：「`CONFIG_KALLSYMS=y` and `CONFIG_KALLSYMS_ALL=y`」（`KALLSYMS_ALL=n` 只是 initial support）→ **需实机验证 OnePlus 是否满足，属未确认**。
- 特性：APM（类 Magisk 模块）+ KPM（内核 inline-hook / syscall-table-hook）。
- 安全模型差异：「The **SuperKey** has higher privileges than root access」——自动化工具**不应**替用户生成/保管 SuperKey，这是安全责任边界。

**判定**：APatch 属于「内核级 root」同类，但**路线不同**（patch 内核镜像 vs 加载 `.ko`），且**强依赖 `KALLSYMS_ALL`**。对 Lrom 的自动化优先级**低于** KernelSU LKM，理由是：它的 patch 结果无法用 `ksud` 那样成熟的 CLI 复现，且失败后果更重（直接改内核而 backups 之外无 ramdisk 兜底）。

---

## 8. 针对本项目设备（OPD2404 / A16 / 6.1.141 / SM8650）的支持情况

| 项目 | 能否直接用现成 `.ko` | 依据 |
|---|---|---|
| KernelSU 官方 | ✅ 有 `lkm-aarch64-android14-6.1_kernelsu.ko` | [v3.3.1 资产](https://github.com/tiann/KernelSU/releases/tag/v3.3.1) |
| KernelSU Next | ✅ 有 `aarch64_android14-6.1_kernelsu.ko` | [v3.4.2 资产](https://github.com/KernelSU-Next/KernelSU-Next/releases/tag/v3.4.2) |
| SukiSU | ✅ 声明覆盖 `android14-6.1` | [sukisu.org/guide/installation](https://sukisu.org/guide/installation) |
| 通用 GKI `boot.img` 替换（任何项目） | ❌ **不可用** | 「Due to OPPO's modifications to `f2fs`, generic GKI images cannot be used」（[ReSukiSU OPPO 页](https://resukisu.github.io/guide/install/oppo.md)）；SukiSU 同样点名 OnePlus/Oppo 不适合 Method 2 |

### 8.1 已确认的社区现成构建（作为交叉验证）

[`WildKernels/OnePlus_KernelSU_SUSFS`](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS) release [`v2.2.0-r4`](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/releases/tag/v2.2.0-r4)（2026-08-12，158 个资产）中存在：

- **`AK3_OP-PAD-2-SM8650_A16_android14-6.1.118_KSUN_33239_SuSFS_v2.2.0.zip`**

其对应配置 [`configs/a16/OP-PAD-2-SM8650-6.1.118.json`](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/blob/main/configs/a16/OP-PAD-2-SM8650-6.1.118.json) 明确写着：

```json
"model": "OP-PAD-2-SM8650-6.1.118",
"soc": "pineapple",
"android_version": "android14",
"kernel_version": "6.1",
"os_version": "A16"
```

> `soc: pineapple` + `kernel 6.1` + `os A16` **完全对上**本项目设备（SD 8 Gen 3 = `pineapple`，A16，6.1 内核）。这说明「OPD2404 = OP-PAD-2-SM8650」这一类映射成立（**但「一加平板 Pro 是否就是 PAD 2」这一机型命名对应关系，官方未给出映射表，属推断，需实机核对**）。
>
> 注意该 zip 是 **AnyKernel3 + 整颗自编译内核**（走内置模式），不是 LKM `.ko`，且其内核版本是 `6.1.118` 而设备是 `6.1.141` —— 按 §6.4-C 的严格性，这类「整内核替换」对子版本更敏感。

### 8.2 反面证据（必须写进风险提示）

[`SukiSU-Ultra/SukiSU-Ultra#815`](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/815)：用户在同一形态设备（OnePlus Pad 2 Pro，A16，kernel 6.6）上，「tried android 15-6.6 and android16-6.12, **both failed**」，manager 显示 not installed。

→ 说明：**「挑一个看起来对的 `.ko`」在 OPPO 平板这类设备上失败率不低**。Lrom 若做自动化，必须①做 KMI 解析并**打印给用户确认**，②失败时给出「回滚到 stock `init_boot`」的一键操作。

---

## 9. 卸载 / 还原方式对照

| 项目 | 官方卸载方式 | 能否用 stock 镜像恢复 | 对 Lrom 的建议 |
|---|---|---|---|
| KernelSU | Manager「Uninstall → Restore stock image」，靠 `/data/adb/ksu/ksu_backup_$SHA1` | ✅ `fastboot flash init_boot <stock>` | Lrom 自己存 stock `init_boot`，还原=一次 fastboot 刷写，最可靠 |
| Next | 未在官方页确认 | ✅（LKM 同理） | 同上 |
| SukiSU | Troubleshooting 未给 LKM 卸载流程 → **未确认** | ✅（LKM 同理） | 同上 |
| BakaSU | 未确认 | ✅（LKM 同理） | 同上 |
| APatch | 未确认 | ✅（README 未给流程；恢复 stock `boot.img`） | 不建议自动化 |

额外提示（LKM 的独特优势，官方原文）：`LKM can be temporarily uninstalled … doesn't require flashing partitions, or even rebooting`。即 LKM 模式下存在「不刷分区临时禁用 root」的路径（重启后恢复）——这对「Lrom 刷机前临时关 root」很有用，但**具体 CLI/接口本项目未查证，属未确认**。

---

## 10. 对 Lrom 的启示（结论）

### 10.1 走哪个项目

**走 `tiann/KernelSU` 官方，LKM 模式。**

理由（按重要性排序）：
1. **有真正可脚本化的 CLI**：`ksud boot-patch` 的 `-b/-m/-o/--magiskboot` 参数完备，且官方提供 `x86_64-unknown-linux-musl` 静态二进制 → 完全符合 Lrom「Python 标准库、零依赖、Linux 原生」的定位（[文档](https://kernelsu.org/guide/installation.html)、[资产](https://github.com/tiann/KernelSU/releases/tag/v3.3.1)）。
2. **不碰内核镜像**，只 patch `init_boot` 的 ramdisk → 与 Lrom 现有的「物理分区 / 逻辑分区 + 风险分级」刷写模型天然兼容，且**不需要 Lrom 具备内核编译能力**。
3. **卸载路径最干净**：stock `init_boot.img` 就在 Lrom 已解析的 `payload.bin` 里，还原 = 一次 `fastboot flash init_boot`。
4. Next / SukiSU 的 LKM 能力**绑死在 APP UI 上**，外部无法调用，自动化价值低；它们的差异化（SUSFS、KPM、旧内核）与 Lrom「刷机工具」的定位无关。

### 10.2 能自动化到哪一步

```
[全自动] 读 uname -r → 解析 KMI=android14-6.1 → 提示用户确认
[全自动] 从 payload.bin 解出 stock init_boot.img（Lrom 已有）
[全自动] 下载/命中缓存 lkm-aarch64-android14-6.1_kernelsu.ko
[全自动] 下载 ksud-x86_64-unknown-linux-musl
[全自动] ksud boot-patch -b init_boot.img -m kernelsu.ko -o patched.img
[全自动] sha256 校验 + 显示「将要刷 init_boot_a」
[半自动] 用户按 Enter 确认风险 ← 唯一必要的人工闸门
[全自动] fastboot flash init_boot patched.img → fastboot reboot
[人工]   开机后装 Manager APK，APP 内点一次「允许」完成 su 授权
```

即：**Lrom 可以做到「一条命令把 `.ko` 塞进 `init_boot` 并刷进去」，把原本需要「装 APP + 点选文件 patch + 手动 fastboot」的流程压到只剩「确认风险」+「点一次授权」。**

### 10.3 卡点（按严重程度）

| # | 卡点 | 影响 | 缓解 |
|---|---|---|---|
| 1 | **KMI 解析必须自己实现**：`uname -r` 是 `6.1.141-android14-11-o-...`，需按 `w.x-androidNN-k` 规则切出 `android14-6.1`，**不能用 Android 版本推断** | 解析错 → 一定失败（见 §6.4-A、#815） | Lrom 内实现一个小解析器 + 单测（已有 114 个测试的框架）；解析失败时要求用户手选 KMI |
| 2 | **OPPO 内核可能拒绝加载 `.ko`**（blacklist / `CONFIG_MODULES` / vermagic / `f2fs` 定制） | 装上但 manager 仍显示 Not installed | 失败检测：patch 后「有 `init_boot` 改动但无 root」→ 自动提示「需自编译内核」，并引导到 WildKernels 现成 AK3 包 |
| 3 | **首次 `su` 授权不能绕过** | 无法做到「刷完即 root shell」 | 接受；把「装 Manager + 点允许」写成引导步骤 |
| 4 | **SPL / anti-rollback** | 用错固件的 `init_boot` 会 bootloop | 强制用「与设备当前版本一致的包」（Lrom 已有型号+版本校验 `package.py`） |
| 5 | **`ksud` 与 `.ko` 版本需配对** | manager 与 kernel 版本不一致会「检测不到 root」（SukiSU 明确提到此现象） | Lrom 固定记录「用了哪个 release 的 `ksud` + 哪个 `.ko`」，写进备份元数据 |
| 6 | 网络安全 / 供应链 | 从网上下载 `.ko` 与 `ksud` 属于执行第三方二进制 | 建议 pin 版本 + 校验 sha256（GitHub release 有 digest 可查，本项目未逐条核对，属未确认） |

### 10.4 明确「不要做」的事

- ❌ **不要**给 OPPO 系设备做「替换通用 GKI `boot.img`」——[ReSukiSU OPPO 页](https://resukisu.github.io/guide/install/oppo.md)与 [SukiSU Method 2 脚注](https://sukisu.org/guide/installation) 两处独立原文都否定了这条路（OPPO 改过 `f2fs`）。
- ❌ **不要**用「Android 版本」选 `.ko`。
- ❌ **不要**自动编译内核（Lrom 是刷机工具，不是构建系统；WildKernels 这类项目已经提供了成品）。
- ❌ **不要**自动生成/接管 APatch 的 SuperKey（安全责任边界，[APatch README](https://github.com/bmax121/APatch)）。

---

## 11. 未确认清单（诚实标注）

| 项 | 状态 |
|---|---|
| KernelSU-Next 的 LKM 对外安装文档 URL | **未确认**（官网仅见源码集成页） |
| SukiSU 的 LKM 卸载/还原官方流程 | **未确认**（Troubleshooting 页未覆盖） |
| Next / SukiSU / BakaSU 是否提供 CLI patch 工具及其用法 | **未确认**（有 `ksud-*` 资产，但用法文档未找到） |
| 「一加平板 Pro」与 `OP-PAD-2-SM8650` 的官方机型映射表 | **未确认**（按 soc/kernel/OS 三元组推断，需实机 `ro.product.device` 核对） |
| 本项目内核 `6.1.141` 是否满足 `CONFIG_KALLSYMS_ALL=y`（APatch 前提） | **未确认**（需 `adb shell zcat /proc/config.gz` 或 `/sys/module/*`） |
| 本项目内核是否 `CONFIG_MODULES=y` 且未 `blacklist kernelsu` | **未确认**（同上） |
| GitHub release 资产的 sha256 digest | **未逐条核对**（API 限流，本次调研以页面/资产清单为准） |
| BakaSU 的 star 之外的 fork 数、release 日期 | **未确认**（`ReSukiSU/ReSukiSU` 已 302，需以 BakaSU 为目标重新取数） |

> 说明：本次调研的**搜索工具不可用**（`web_search` 报「no API key for DEEPSEEK_API_KEY」，`web_fetch` 直连 GitHub 返回 `fetch failed`，DuckDuckGo 直连超时）。以上结论全部通过 **`curl` 抓 GitHub 页面 / GitHub REST API / 各项目官网**获得，每条均有 URL。GitHub REST API 在调研中途触发了匿名限流（60 req/h），因此部分数据改由 HTML 页面解析得到（star 数等核心数据在限流前已取得）。

---

## 12. 参考链接汇总

**上游**
- <https://github.com/tiann/KernelSU> ／ <https://kernelsu.org> ／ [安装文档](https://kernelsu.org/guide/installation.html) ／ [如何构建](https://kernelsu.org/guide/how-to-build.html) ／ [FAQ](https://kernelsu.org/guide/faq.html) ／ [救砖](https://kernelsu.org/guide/rescue-from-bootloop.html) ／ [KMI 列表源码 build-all.sh](https://github.com/tiann/KernelSU/blob/main/kernel/build-all.sh) ／ [v3.3.1](https://github.com/tiann/KernelSU/releases/tag/v3.3.1)

**Fork**
- <https://github.com/KernelSU-Next/KernelSU-Next> ／ [官网](https://kernelsu-next.github.io/webpage/) ／ [安装](https://kernelsu-next.github.io/webpage/pages/installation.html) ／ [非官方设备页](https://kernelsu-next.github.io/webpage/pages/devices.html) ／ [v3.4.2](https://github.com/KernelSU-Next/KernelSU-Next/releases/tag/v3.4.2)
- <https://github.com/SukiSU-Ultra/SukiSU-Ultra> ／ [官网](https://sukisu.org) ／ [安装](https://sukisu.org/guide/installation) ／ [兼容性](https://sukisu.org/guide/compatibility) ／ [Troubleshooting](https://sukisu.org/guide/troubleshooting) ／ [v4.2.1-r2](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.1-r2) ／ [#815 反面案例](https://github.com/SukiSU-Ultra/SukiSU-Ultra/issues/815)
- <https://github.com/Baka-SU/BakaSU>（原 ReSukiSU；<https://github.com/ReSukiSU/ReSukiSU> 已重定向）／ [文档站](https://resukisu.github.io/) ／ [OPPO 设备页](https://resukisu.github.io/guide/install/oppo.md) ／ [GKI/LKM 页](https://resukisu.github.io/guide/install/gki.md) ／ [安装总页](https://resukisu.github.io/guide/install.md)
- <https://github.com/5ec1cff/KernelSU>（MKSU，Magic Mount）
- <https://github.com/rsuntk/KernelSU>（RKSU，个人 fork）

**旁证 / 社区构建**
- <https://github.com/bmax121/APatch> ／ <https://github.com/bmax121/KernelPatch>
- <https://github.com/WildKernels/OnePlus_KernelSU_SUSFS> ／ [兼容性](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/blob/main/compatibility.md) ／ [PAD-2-SM8650 A16 配置](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/blob/main/configs/a16/OP-PAD-2-SM8650-6.1.118.json) ／ [v2.2.0-r4](https://github.com/WildKernels/OnePlus_KernelSU_SUSFS/releases/tag/v2.2.0-r4)
- <https://github.com/Numbersf/Action-Build>（OnePlus QCOM/MTK 内核 CI 构建）
