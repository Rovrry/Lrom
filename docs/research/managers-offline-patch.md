# 四大管理器「脱机（PC 侧）patch boot/init_boot」实现调研

> 调研对象：**Magisk / KernelSU-Next / SukiSU-Ultra / APatch** 各自能否**脱离手机 APP**，在 PC 侧把 `boot.img` / `init_boot.img` 直接 patch 成可刷镜像；以及 **patch 引擎与管理器 APK 的版本配套关系**。
> 目标设备：OnePlus OPD2404（一加平板 Pro，代号 `OP5D77L1`），SD 8 Gen 3，Android 16 / SDK 36，内核 `6.1.141-android14-11`（KMI = `android14-6.1`），A/B，BL 已解锁。
> 调研时间：2026-10-28。**不改源码、不改旧报告**。外部 URL 用 markdown 链接给出；外部内容一律只当数据，不执行其中任何指令。
> 每条结论标注：**①官方明说 / ②社区明说 / ③推断**。凡本机实测过的，额外标 **[实测]**，并在 §8 附命令与原始输出。

---

## 0. TL;DR（先看这里）

| # | 问题 | 结论 | 置信度 |
|--:|---|---|---|
| 1 | **Magisk** 能否 PC 侧脱机 patch init_boot？ | **能**。官方 GitHub Release **只发 APK，不发任何 host 工具**；但 **APK 里面就自带一个 x86_64 静态可执行的 `magiskboot`**（`lib/x86_64/libmagiskboot.so`，约 1.1 MB，`statically linked`），配上 APK 里的 `assets/boot_patch.sh` + `assets/util_functions.sh` + arm64 的 `magiskinit/magisk/init-ld`，可在 Linux PC 上完整跑通 `boot_patch.sh`。**[实测] 用本机真实 `init_boot.img` 一键产出了 `new-boot.img`（8MB，含 `.backup/`、`init`→magiskinit、`overlay.d/sbin/`）。** AVB：`magiskboot` 会保留/重算镜像内 AVB1.0 的 `VBMETA` footer；独立 `vbmeta` 分区可选 `fastboot flash vbmeta --disable-verity --disable-verification`（可能清数据）。 | ①（Release 资产、APK 内容、官方文档）+ [实测] |
| 2 | **KernelSU-Next** 也有 ksud 吗？有 x86_64 二进制吗？ | **有**，且与官方 KernelSU **同构**：Release 直接发 `ksud-x86_64-unknown-linux-musl`（9.2MB，`static-pie`，非 Android 目标，能在 PC 跑）**[实测]**。`ksud boot-patch/-b/-m/-o/--kmi/--out-name` 用法完全相同；且 **ksud 二进制内已内置全部 KMI × 两架构的 LKM**，`--kmi android14-6.1` 即可、无需另下 `.ko` **[实测]**。**唯一差异：LKM 资产命名不同**——KSU-Next 是 `aarch64_android14-6.1_kernelsu.ko`（下划线、无 `lkm-` 前缀），KernelSU 官方是 `lkm-aarch64-android14-6.1_kernelsu.ko`。 | ①（Release 资产、源码）+ [实测] |
| 3 | **SukiSU-Ultra** 脱机 patch 怎么做？有 PC 侧 CLI 吗？ | **官方 Release 里的 ksud 只有 `*-linux-android`（Android/bionic 动态链接）——在普通 Linux PC 上跑不起来** **[实测：报 "找不到需要的文件"，interpreter=/system/bin/linker64]**。Release 无任何 host（glibc/musl）二进制；官方安装文档全程是「管理器 APP 内点选文件修补」。**源码里有 `cli_non_android.rs`，理论上可自行交叉编译出 host 版 ksud，但官方不发布、不背书**。susfs 是内核侧特性，不是 patch 工具。→ 结论：**SukiSU 官方只能 APP 手动**；要做 PC 一键必须自己从源码编译 host ksud。 | ①（Release 资产、CI 配置、官方文档）+ [实测] |
| 4 | **APatch** 脱机 patch 怎么做？有命令行吗？ | **有，而且是官方明说的**！APatch 本体只发 APK，但底层 **KernelPatch 项目 Release 直接发 host 工具**：`kptools-linux`（x86_64 GNU/Linux 动态可执行，740KB）、`kpimg-android`、`kptools-mac`、`kptools-msys2-win.7z`。官方 APatch 文档「Manually patching」章节**明确给出 Linux 命令**：`kptools-linux -p --image kernel-b --skey "YourKey" --kpimg kpimg-android --out kernel` + `kptools-linux repack boot.img`。**[实测] 更进一步：`kptools-linux -p -i boot.img -k kpimg-android -s <KEY> -o out.img` 一步到位，本机真实 `boot.img` 已成功 patch 并自动保留 AVB footer。** ⚠️ **APatch 打的是 `boot.img` 的内核（KERNEL_SZ>0），不是 `init_boot`**（官方明文禁止对 init_boot 打 APatch）。 | ①（官方文档 + Release 资产）+ [实测] |
| 5 | 管理器 APK 与 patch 引擎必须版本配套吗？能跨管理器混用吗？ | **必须同源配套，不建议跨管理器混用。** ① 管理器会校验**内核驱动（LKM）版本**：driver 版本低于管理器要求就红字报错（KSU-Next #917）。② 社区明确「管理器必须与刷入的**口味**一致（KernelSU / KSU-Next / SukiSU / FinaSU…），否则 root 不工作」。③ **安全做法：管理器 APK、ksud、内置 LKM 全部取自同一 Release tag**。官方 KernelSU 的 ksud 甚至拒绝 patch 已被 Magisk 改过的镜像（`Cannot work with Magisk patched image`）。跨管理器（ksud patch + SukiSU 管理器）非官方支持、易翻车。 | ②③（官方 issue + 社区 + 源码） |
| 6 | 对 Lrom 落地建议 | **可「脱机一键」**：KernelSU 官方（已有）、KernelSU-Next（加个 ksud，几行适配）、**APatch（用 kptools-linux 打 boot.img，可行！推翻旧报告「APatch 无法 CLI」的判断）**。**只能「APP 手动」**：SukiSU-Ultra（官方无 host ksud）、Magisk（官方只认 APP，PC 自跑属非官方流程，可作「进阶可选」）。**可抽象**：把 `patch_with_ksud` 抽成 `Patcher` 协议/引擎矩阵，但各引擎的**目标分区不同**（KSU 系→init_boot，APatch→boot）、**输出命名不同**、**LKM 命名不同**，不能只换二进制。 | ③（基于以上实测的推断） |

一句话：**真正成熟、官方背书、能在 PC 端脚本化一键的，只有 KernelSU 家族（官方 / Next）和 APatch（走 KernelPatch 的 kptools）；Magisk 靠 APK 内藏 x86_64 magiskboot 也能跑（非官方）；SukiSU 官方则只有 Android 版 ksud，PC 侧要么手动 APP、要么自己编译。**

---

## 1. Magisk：PC 侧脱机 patch 全解

### 1.1 官方 Release 里有什么 host 工具？——**没有**（①）

用 GitHub API 拉 `topjohnwu/Magisk` 近 12 个 Release 的资产清单（2026-10-28 实测）：

| Release | 资产 |
|---|---|
| `v31.1`（2026-10-25） | `app-debug.apk`, `Magisk-v31.1.apk` |
| `v31.0` | `app-debug.apk`, `Magisk-v31.0.apk` |
| `v30.7` / `v30.6` / … | `app-debug.apk`, `Magisk-vXX.apk`, `notes.md` |

- **结论：Magisk 官方 Release 从不发布 `magiskboot` / `magiskinit` 等独立的 host 工具二进制**（①，[v31.1 Release](https://github.com/topjohnwu/Magisk/releases/tag/v31.1)）。网上一些教程说的「下载 magiskboot.exe」通常是第三方从别的渠道（旧版/自编译）提取的，不是官方 Release 资产。
- 复核 `v31.1` 的 `expanded_assets` 与 Atom feed 亦只有 APK。旧报告 `root-managers-magisk.md` 里「v31.1 起 magiskboot 支持在 Linux/macOS 宿主机构建」指的是**从源码编译**，并非 Release 提供二进制。

### 1.2 但——**APK 里自带可用的 x86_64 `magiskboot`**（① + [实测]）

把 `Magisk-v31.1.apk` 当 zip 拆开，关键成员：

```
assets/boot_patch.sh            (6.7 KB，官方 patch 脚本)
assets/util_functions.sh        (20 KB)
lib/x86_64/libmagiskboot.so     (1.1 MB)  ← host 工具：真正能在 PC 跑
lib/arm64-v8a/libmagiskinit.so  (184 KB)  ← 设备侧：放回 ramdisk 的 init
lib/arm64-v8a/libmagisk.so      (384 KB)  ← 设备侧：magisk / magiskd
lib/arm64-v8a/libinit-ld.so     (4.9 KB)  ← 设备侧：/init 的 LD_PRELOAD
```

**[实测]** 对 `lib/x86_64/libmagiskboot.so` 做 `file`：

```
ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked, stripped
```

`readelf -d` 无任何 `NEEDED`（**完全静态、零动态依赖**），`chmod +x` 后直接 `./libmagiskboot.so` 即打印完整用法（`unpack / repack / cpio / verify / sha1 / dtb / hexpatch / compress …`）。也就是说：**APK 里这份 `lib/x86_64/libmagiskboot.so` 就是一个纯正的 x86-64 Linux 版 magiskboot，不需要另找、也不需要编译。**

### 1.3 完整 PC 侧脱机 patch 流程（[实测] 已跑通）

`boot_patch.sh` 的成员要求（脚本头注释，①）：

| 文件名 | 作用 | 取自 APK |
|---|---|---|
| `boot_patch.sh` | 主脚本 | `assets/boot_patch.sh` |
| `util_functions.sh` | 辅助函数 | `assets/util_functions.sh` |
| `magiskboot` | **host** 镜像处理工具 | `lib/x86_64/libmagiskboot.so` |
| `magiskinit` | **arm64**，替换 ramdisk 里的 `/init` | `lib/arm64-v8a/libmagiskinit.so` |
| `magisk` | **arm64**，magisk 二进制 | `lib/arm64-v8a/libmagisk.so` |
| `init-ld` | **arm64**，`/init` 的 LD_PRELOAD | `lib/arm64-v8a/libinit-ld.so` |

> 关键点：**只有 `magiskboot` 需要 x86_64（跑在 PC 上）；`magiskinit/magisk/init-ld` 必须是 arm64（装进手机 ramdisk，开机在手机上跑）**。混用（全拿 x86_64，或全拿 arm64）都不对。

实测步骤（在 `/tmp/mgp`）：

```bash
unzip magisk.apk "assets/boot_patch.sh" "assets/util_functions.sh" \
    "lib/x86_64/libmagiskboot.so" \
    "lib/arm64-v8a/libmagiskinit.so" "lib/arm64-v8a/libmagisk.so" "lib/arm64-v8a/libinit-ld.so"
cp assets/boot_patch.sh assets/util_functions.sh .
cp lib/x86_64/libmagiskboot.so magiskboot
cp lib/arm64-v8a/libmagiskinit.so magiskinit
cp lib/arm64-v8a/libmagisk.so     magisk
cp lib/arm64-v8a/libinit-ld.so    init-ld
chmod +x magiskboot magiskinit magisk init-ld
bash ./boot_patch.sh ./init_boot.img      # 产出 new-boot.img
```

真实输出节选（本机真实 `init_boot.img`，8 MB）：

```
Parsing boot image: [./orig_init_boot.img]
HEADER_VER [4]  KERNEL_SZ [0]  RAMDISK_SZ [2389727]  PAGESIZE [4096]  RAMDISK_FMT [lz4_legacy]  VBMETA
- Patching ramdisk
  Add file [init] ... Create directory [overlay.d] ... Add file [overlay.d/sbin/magisk.xz]
  Add file [overlay.d/sbin/init-ld.xz] ... Backup [init] -> [.backup/init.xz]
Repack to boot image: [new-boot.img]
  HEADER_VER [4]  KERNEL_SZ [0]  RAMDISK_SZ [1995716]  PAGESIZE [4096]
```

复核 `new-boot.img`：`magiskboot unpack` 通过，`ramdisk.cpio` 内含 `.backup`、`init`（184 KB = magiskinit 大小一致）、`overlay.d/`。原图与产物 `cmp` 明确不同。**没有报错**（唯一噪声是 `util_functions.sh: 行 29: /proc/self/fd/: 是一个目录`，那是脚本探测 busybox/ash 的无害提示，不影响结果）。

> 这说明：**Magisk 完全可以「完全脱机 patch init_boot、不用手机 APP」**——只是这条路径**不是官方支持的安装方式**（官方文档只写「APP 内选文件修补」并在安装页红字警告「NEVER patch image on another device」）。这里打的是**本机自己的 init_boot**，不违反其本意；但 Lrom 若采用，应明示「非官方流程、失败自负」。

### 1.4 AVB / verity 怎么处理？（① + [实测]）

- **镜像内 AVB1.0 footer**：本机 `init_boot.img` 解包时报 `VBMETA`（尾部自带 AVB1.0 签名块）。`magiskboot repack` 会**保留并重算**该 footer；**[实测]** 对原图和新图分别 `magiskboot verify`，都输出 `VBMETA` 且退出码 0（都是「有效的 AVB1.0 签名镜像」）。
- **独立 vbmeta 分区（AVB2.0 链）**：官方文档原文（[Magisk 安装文档](https://topjohnwu.github.io/Magisk/install.html)）：
  > "(Optional) If your device has a separate `vbmeta` partition, you can patch the `vbmeta` partition with command: `fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img` (note that it may wipe your data)"
- **本机（一加/欧加）**：BL 已解锁、`verifiedbootstate=orange` 时，改过的 init_boot 正常开机、AVB 不拦（详见本仓 `violet-auto-root.md` §TL;DR 第 5 条与本机实测）。故 Lrom **不需要**自签 AVB、也不需要动 vbmeta；如需保守，可选 `fastboot flash vbmeta --disable-verity --disable-verification`（会清数据，慎用）。
- Magisk 脚本里 `KEEPVERITY / KEEPFORCEENCRYPT / PATCHVBMETAFLAG` 三个环境变量控制是否从 fstab 去掉 verity/forceencrypt、是否把 boot 内 vbmeta 的 disable 标志置位（①，`boot_patch.sh` 头注释）。PC 端默认 `false`，与官方面向「解锁设备」的默认一致。

### 1.5 对 Lrom 的要点

- **不依赖任何额外 host 下载**：Magisk APK 本身就是「引擎包」——`manager_fetch.py` 已经能下 `Magisk-*.apk`，直接拆包即可拿到 x86_64 `magiskboot` 与 arm64 运行件。
- 官方**明确只支持 APP patch**，PC 侧流程属「社区可行、官方不支持」。若 Lrom 要做，建议标注为「进阶 / 非官方」，并严格「只 patch 本机同型号镜像」。
- Magisk 与 KSU 系**互斥**：ksud 会检测并拒绝 Magisk 已 patch 的镜像（源码 `ensure!(!cpio.is_magisk_patched(), "Cannot work with Magisk patched image")`，①），magiskboot 也会把「被其他程序改过」标为 unsupported。**同分区不能叠加。**

---

## 2. KernelSU-Next：与官方同构，可直接 PC 跑

### 2.1 Release 资产（①，2026-10-28 实测）

[`KernelSU-Next/KernelSU-Next` v3.4.2](https://github.com/KernelSU-Next/KernelSU-Next/releases/tag/v3.4.2)（2026-10-13）资产（节选）：

```
ksud-x86_64-unknown-linux-musl      9.2 MB   ← 可在 PC 跑
ksud-aarch64-unknown-linux-musl     9.0 MB
ksud-x86_64-apple-darwin / aarch64-apple-darwin
ksud-aarch64-linux-android / x86_64-linux-android / riscv64-linux-android
KernelSU_Next_v3.4.2_33355-release.apk (+ -spoofed)
aarch64_android<ver>-<kernel>_kernelsu.ko     ← 8 个 KMI（无 lkm- 前缀、下划线）
x86_64_android<ver>-<kernel>_kernelsu.ko
android<ver>-<kernel>_susfs_guard_lkm.ko      ← susfs 守卫 LKM
```

**有 `ksud-x86_64-unknown-linux-musl`**（①）。**[实测]** 下载后：

```
ksunext-ksud: ELF 64-bit LSB pie executable, x86-64, static-pie linked, stripped
Usage: ksunext-ksud <COMMAND>   →  boot-patch / boot-restore / boot-patch-v2 / get-sign / supported-kmis
```

`--help` 顶层与官方 KernelSU 一字不差：**`KernelSU cli for non-android`**，说明它就是同一套 `cli_non_android` 的构建（KSU-Next 是 KernelSU fork）。

### 2.2 用法是否与官方一致？——是（① + [实测]）

**[实测]** KSU-Next 的 ksud 用**内置 LKM**、仅给 `--kmi android14-6.1` 就成功 patch 了本机真实 `init_boot.img`：

```
- Adding KernelSU LKM
- Adding bundled LKM config
- Repacking boot image
- Output file is written to ./outN/pn.img   - Done!
```

`supported-kmis` 输出 `aarch64/android14-6.1`、`x86_64/android14-6.1` …（格式 = `{arch}/android{ver}-{kernel}`，注意**不是** `6.1-android14-11`）。

### 2.3 LKM 命名差异（**落地必看**）

| 项目 | aarch64 LKM 资产名 |
|---|---|
| KernelSU 官方 | `lkm-aarch64-android14-6.1_kernelsu.ko` |
| **KernelSU-Next** | **`aarch64_android14-6.1_kernelsu.ko`**（无 `lkm-`、下划线） |
| SukiSU-Ultra | `lkm-aarch64-android14-6.1_kernelsu.ko`（同官方） |

→ 现有 `offline_root._pick_lkm_asset` 只匹配 `lkm-aarch64-` 前缀，**若把引擎切到 KSU-Next 会找不到 .ko**（不过推荐改用「ksud 内置 LKM」，见 §6，这条不再关键）。

### 2.4 小结

KernelSU-Next **完全可做成 PC 脱机一键**：下 `ksud-x86_64-unknown-linux-musl` + `--kmi android14-6.1`，与官方 KernelSU 同一套代码路径。

---

## 3. SukiSU-Ultra：官方无 PC 侧 host ksud → 只能 APP 手动

### 3.1 Release 资产（①，实测）

[`SukiSU-Ultra/SukiSU-Ultra` v4.2.1-r2](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.1-r2)（2026-10-24）资产：

```
ksud-aarch64-linux-android / ksud-armv7-linux-androideabi / ksud-riscv64-linux-android / ksud-x86_64-linux-android   ← 全部 *-linux-android
ksuinit-aarch64 / arm / riscv64 / x86_64
lkm-aarch64-android<ver>-<kernel>_kernelsu.ko  （8 个 KMI，命名同官方 KSU）
lkm-x86_64-..._kernelsu.ko
SukiSU_v4.2.1-r2_41068-release.apk (+ -spoofed)
```

**关键：没有任何 `*-unknown-linux-musl` 或 `*-pc-windows` 等 host 二进制**；`ksud` 只有 `linux-android`（NDK/Android 目标）。

### 3.2 官方 ksud 在 PC 上跑不起来（[实测]）

```
$ ./sukisu-ksud --help
bash: ./sukisu-ksud: 无法执行：找不到需要的文件
$ file sukisu-ksud
ELF 64-bit LSB pie executable, x86-64, dynamically linked, interpreter /system/bin/linker64, for Android 26, built by NDK r29
```

`interpreter=/system/bin/linker64` 是 **Android bionic 动态链接器**，普通 Linux PC 没有它 → 直接报「找不到需要的文件」。**所以 SukiSU 官方分发的 ksud 不能在 PC 上 patch。**（①+[实测]）

### 3.3 官方安装方式就是「APP 手动」（①）

[SukiSU 官方安装文档](https://sukisu.org/zh/guide/installation) 全文只有管理器 APP 路径：「在主页点击安装卡片 → 选择文件并修补 → 选择 KMI → 刷入」。**通篇没有 PC 侧 ksud 命令行方案**。兼容性页（[sukisu.org/zh/guide/compatibility](https://sukisu.org/zh/guide/compatibility)）也只讲设备/架构支持。

### 3.4 有没有「理论上」的 PC 路径？（② + 源码）

SukiSU 的 ksud 源码 **确实包含 `userspace/ksud/src/cli_non_android.rs`**（与 KernelSU 同名文件），意味着**代码层面支持非 Android CLI**。其 CI 配置 [`.github/workflows/ksud.yml`](https://github.com/SukiSU-Ultra/SukiSU-Ultra/blob/main/.github/workflows/ksud.yml) 只构建 `*-linux-android / *-androideabi / riscv64-linux-android`（用 `ANDROID_NDK_HOME`），**没有 host target**。

→ 所以：**要么自己 `cargo build --target x86_64-unknown-linux-musl` 编译一份 host ksud（社区做法、可行但非官方支持、需自行承担版本匹配风险），要么用 APP。** 对 Lrom「零依赖、开箱即用」定位，SukiSU 只能标「APP 手动」。

### 3.5 susfs 是什么？——不是 patch 工具

SUSFS 是**内核侧隐藏/伪装特性**（`susfs4ksu` 内核补丁 + 用户态配置），运行在已 root 的内核里，与「PC 侧 patch 镜像」无关。SukiSU 把 susfs 相关代码/配置编进了管理器与内核，但**它不提供脱机 patch CLI**。（②社区/项目结构）

---

## 4. APatch：官方明说可 PC 侧 patch（走 KernelPatch 的 `kptools-linux`）

### 4.1 三个仓的关系（①）

- **APatch**（`bmax121/APatch`）：管理器 APP，**Release 只发 `APatch_*_release-signed.apk`**（实测：tag `11282`，2026-10-15，仅 1 个 APK）。
- **KernelPatch**（`bmax121/KernelPatch`）：APatch 依赖的**底层 patch 引擎**（`kpimg` 镜像 + `kptools` 工具 + `kpatch`），**Release 发 host 工具**（见下）。
- APatch 本质 = 「把 KernelPatch 的 patch 结果（注入到内核）用管理器 UI 包起来」。

### 4.2 KernelPatch Release 的 host 工具（①，实测）

[`KernelPatch` 0.13.9](https://github.com/bmax121/KernelPatch/releases/tag/0.13.9)（2026-09-24）资产：

```
kptools-linux               740 KB   ← x86_64 GNU/Linux 可执行（PC 直接用）
kptools-mac                 763 KB
kptools-msys2-win.7z        1.1 MB   ← Windows(MSYS2) 版
kptools-android             248 KB   ← 设备侧
kpimg-android               296 KB   ← 注入镜像（arch=android/arm64）
kpimg-linux / kpimg-x86              ← 其他目标
android<ver>-<kernel>_kernelpatch.ko （8 个 KMI，仅「越狱模式」用）
demo-*.kpm                  （KPM 内核模块示例）
```

**[实测]** `file kptools-linux` → `ELF 64-bit LSB pie executable, x86-64, dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2, for GNU/Linux 3.2.0`（**标准 Linux glibc，PC 可跑**）；直接运行打印完整子命令：`-p/--patch`、`-u/--unpatch`、`-r/--reset-skey`、`-d/--dump`、`-l/--list`、`unpack/repack`，以及「若 `-i` 是 Android boot image（`ANDROID!` magic），会自动解出内核→patch→重打包」。

### 4.3 官方文档给出的 PC 命令（①）

[APatchDocs `docs/install.md`「Manually patching」](https://github.com/AndroidPatch/APatchDocs/blob/main/docs/install.md)（英文原文）明确写 Linux 流程：

```sh
./kptools-linux unpack boot.img     # 解出 kernel 文件
# 把 kernel 重命名为 kernel-b
./kptools-linux -p --image kernel-b --skey "YourKey" --kpimg kpimg-android --out kernel
./kptools-linux repack boot.img     # 生成 new-boot.img（即打好的镜像）
```

并注明「When the KernelPatch is updated and APatch Manager remains unchanged, you can choose to manually patch the kernel」——即**官方承认「工具可独立于管理器升级」**（kptools/kpimg 比管理器新是允许的），还给了在线 patch 入口 `https://kernelpatch-on-web.pages.dev/`。

### 4.4 一步到位（[实测]，更省事）

`kptools` 的 `-p -i <boot.img>` 会自动完成「解内核→patch→重打包」，**[实测] 本机真实 `boot.img`（201 MB）一次跑通**：

```bash
./kptools-linux -p -i boot.img -k kpimg-android -s "TestKey12345" -o boot_apatch.img
# 输出（节选）：
[+] kallsyms_lookup_name: type: T, offset: 0x0056b234
[+] superkey configured (value omitted)
[+] Starting automatic repack...
[+] AVB footer: image_size=35704832, vbmeta_offset=35704832, vbmeta_size=2496, shift=299008
[+] - Preserved original AVB metadata at offset 36003840
[+] Repack completed: /tmp/boot_apatch.img
```

复核产物 `boot_apatch.img`：`magiskboot unpack` 解析为 `HEADER_VER [4] KERNEL_SZ [35997200] RAMDISK_SZ [0]`——**内核已被替换/注入，ramdisk=0**，正是 APatch「改内核、不塞 ramdisk」的特征。`kpimg-android` 版本 `d09`（`kptools-linux -v -k kpimg-android`）。

### 4.5 关键前提与「坑」（①，必读）

来自 [APatch 安装文档](https://github.com/AndroidPatch/APatchDocs/blob/main/docs/install.md)：

1. **APatch 永远 patch `boot.img`，不要对 `init_boot` / 其他分区打**（原文：「APatch always patch the `boot.img` of any device. Don't attempt to patch or flash the `init_boot`…」）。→ 与 KSU 系「init_boot」完全不同，Lrom 的分区选择必须按引擎区分。
2. **仅 ARM64；仅 Android 内核 3.18–6.12**；需 `CONFIG_KALLSYMS=y`（最好 `CONFIG_KALLSYMS_ALL=y`）——APatch 靠 kallsyms 定位内核符号。
3. **SuperKey 规则**：8–63 位、含数字与字母、**无特殊字符**；禁止弱口令。
4. **不要用别的管理器 patch 过的 boot.img**。
5. 6.6 内核仅小米/一加充分测试（其余品牌风险自负）。

> ⚠️ **跨报告更正**：旧报告 `root-managers-kernelsu.md` §7 判「APatch 的 patch 结果无法用成熟 CLI 复现、不建议自动化」，本仓 `violet-auto-root.md` TL;DR 也把 APatch 归为「绑 APP、不做」。**本次实测 + 官方文档推翻了这一判断**：APatch **有**官方 PC 命令行（`kptools-linux`），可完全脱机 patch。区别在于它打 `boot.img` 内核、失败后果更重（改内核，ramdisk 无兜底），所以**风险等级高**，但**技术上可自动化**成立。

---

## 5. 管理器 APK 与 patch 引擎的版本配套 / 跨管理器混用

### 5.1 是否必须配套？——是（①②③）

1. **管理器校验内核驱动（LKM）版本**（②，官方 issue）：[KernelSU-Next #917](https://github.com/KernelSU-Next/KernelSU-Next/issues/917) 真实报错——「The current KernelSU Next version **12139** is too low for the manager to work properly. Please upgrade to **12797** or higher.」即**管理器会比对自己期望的驱动版本号**，LKM 太旧就红字拒绝管理 root。
2. **社区明确「管理器口味必须与刷入口味一致」**（②，[wildkernels/gki_kernelsu_susfs post-install](https://github.com/wildkernels/gki_kernelsu_susfs/blob/00d2d90477f4b703b9890fc820be7391957fc2e8/docs/post-install.md)）："ensure the manager matches the flashed **flavor** (KernelSU / KernelSU-Next / BakaSU / FinaSU / Suki-SU Ultra / KowSU)… Root not working — ensure the manager matches the flashed flavor."
3. **最佳实践**（②，[privacyportal 分析](https://www.privacyportal.co.uk/blogs/free-rooting-tips-and-tricks/should-kernelsu-next-manager-and-kernel-versions-match)）："Prefer the Manager, driver or LKM, and ksud from the **same official release**." 与 Lrom `offline_root.py` 文档头「同源保证版本一致」的思路一致。

### 5.2 能否跨管理器混用（ksud patch + SukiSU 管理器）？——**不建议、非官方**

- 机制上：LKM（`.ko`）承载**真正的内核驱动**，决定内核侧「这是哪个 flavor、什么版本」；管理器按该 flavor+version 判断能否管理。**用 KernelSU 的 ksud 打的包，ramdisk 里塞的是 KernelSU 的 LKM / ksuinit / 配置，装 SukiSU 管理器属于跨 flavor**，官方 issue/社区均视作「不匹配」。
- **Magisk 与 KSU 系硬互斥**（①，源码）：`ksud` 检测到 `cpio.is_magisk_patched()` 直接 `Cannot work with Magisk patched image`；Magisk 的 `boot_patch.sh` 也会把非本工具改过的镜像判为「patched by unsupported programs」而 abort。
- **SukiSU 与 KernelSU 是 fork 关系**，UAPI 大体同源，**早期**存在交叉可用的传闻，但 v3.x 后各 fork 已明显分叉（各自 LKM 版本号、各自管理器）。**Lrom 不应赌跨管理器兼容**。

### 5.3 Lrom 的正确配套策略（建议）

- **管理器 APK、ksud 二进制、LKM 全部取自同一 Release tag**（例如都取 `tiann/KernelSU v3.3.1`）。`manager_fetch.py` 已支持按 repo 取 latest，应**复用同一个 `Release` 对象**同时取 APK 与 ksud（现有 `ensure_engine` 已从 `MANAGER_SOURCES['ksu']` 的同一个 release 取 ksud，思路正确）。
- **优先用 ksud 内置 LKM**（见 §6.2）：内置 LKM 与 ksud 二进制**天然同版本**，比「另下 .ko」更不容易版本错配。
- 刷入后**装同 tag 的管理器 APK**；若用户装的是别的版本，可能触发「版本太低」红字。

---

## 6. 对 Lrom 的落地建议（实现导向）

### 6.1 引擎能力矩阵（结论表）

| 引擎 | PC 侧 host 工具来源 | 目标分区 | 一步命令（PC） | 可行性 | 官方支持度 |
|---|---|---|---|---|---|
| **KernelSU 官方** | Release `ksud-x86_64-unknown-linux-musl`（内置 LKM） | `init_boot` | `ksud boot-patch -b init_boot.img --kmi android14-6.1 -o <dir> --out-name out.img` | ✅ 已接入 | ① CLI 官方 |
| **KernelSU-Next** | Release `ksud-x86_64-unknown-linux-musl`（内置 LKM） | `init_boot` | 同上（`--kmi android14-6.1`） | ✅ 加引擎即可 | ① CLI 官方 |
| **SukiSU-Ultra** | ❌ Release 只有 `*-linux-android`；需自编译 host ksud | `init_boot` | （自编译后同 ksud 用法） | ⚠️ 需自编译 | 官方=APP |
| **Magisk** | ✅ **APK 内** `lib/x86_64/libmagiskboot.so` + `assets/boot_patch.sh` | `init_boot`（本机） | 解包 APK → `bash boot_patch.sh init_boot.img` | ✅ 实测跑通 | 官方=APP（PC 非官方） |
| **APatch** | Release `kptools-linux` + `kpimg-android`（KernelPatch 仓） | **`boot`** | `kptools-linux -p -i boot.img -k kpimg-android -s <KEY> -o out.img` | ✅ 实测跑通 | ① 官方「Manually patching」 |

### 6.2 现有 `offline_root` 怎么改（具体、最小的改造）

现状（`src/lrom/core/offline_root.py` + `root.py::patch_with_ksud`）只接 KernelSU 官方 ksud，且**额外下载 LKM**。基于实测，建议：

1. **去掉单独下载 LKM 的步骤（Recommended，最高性价比）**
   - 实测：官方 `ksud` 二进制**已内嵌** `aarch64/`、`x86_64/` 两套、8 个 KMI 的 `*_kernelsu.ko` 与 `ksuinit`（`strings ksud | grep aarch64/android14-6.1_kernelsu.ko` 命中；`ksud supported-kmis` 列出 8×2）。
   - 因此只需 `--kmi android14-6.1` 即可，**无需 `--module`、无需连 GitHub 下 `.ko`**（少一次网络请求，且版本天然与 ksud 同步）。
   - ⚠️ **注意 `--kmi` 的取值格式**：ksud 要的是 `android{ver}-{kernel}`（如 `android14-6.1`），**不是** `derive_kmi()` 目前返回的 `6.1-android14-11`。现状因为同时传了 `--module`，ksud 忽略 `--kmi` 的资产查找才没暴露；一旦改成内置 LKM 就会因格式不符报 `asset not found: aarch64/6.1-android14-11_kernelsu.ko`（[实测] 复现）。→ 需要把 `derive_kmi` 结果再转成 ksud 格式（`6.1-android14-11` → `android14-6.1`）。
   - 附带好处：`_pick_lkm_asset` 因命名差异（KSU `lkm-` vs KSU-Next 无前缀）导致的匹配脆弱性直接消失。

2. **把 `patch_with_ksud` 抽象成 `Patcher` 协议 / 引擎矩阵**
   - 三个必变维度（否则「只换二进制」跑不通）：
     - **目标分区**：KSU 系/Magisk → `init_boot`；**APatch → `boot`**。`RootPlan.partition` 与 `check_ramdisk` 检查需**按引擎分派**——APatch 的 `boot.img` `RAMDISK_SZ=0`，现有 `build_root_plan` 的 ramdisk 必填检查会**直接 block** APatch。
     - **输出命名**：ksud 原生产物名是 `kernelsu_patched_<ts>.img`，`--out-name` 可控；Magisk 脚本固定产出 `new-boot.img`；kptools `-o` 可控。抽象层统一「按 out_name 落盘 + 校验 sha256」即可（现有 `patch_with_ksud` 末尾的兜底查找逻辑可复用/泛化）。
     - **LKM/素材来源**：KSU 内置；Magisk 从 APK 抽；APatch 需 `kpimg-android`（同 KernelPatch tag）。
   - 建议接口（伪代码）：
     ```python
     class Patcher(Protocol):
         key: str                      # "ksu" | "ksu-next" | "magisk" | "apatch" | "sukisu"
         target_partitions: tuple[str,...]   # ("init_boot",) 或 ("boot",)
         def ensure(self, dest, *, release, on_notice) -> Engine
         def patch(self, image, output, *, kmi, on_line) -> PatchResult
     ```
     各引擎注册进 `PATCHERS: dict[str, Patcher]`，`build_root_plan(method=...)` 按 key 取分区/校验策略。

3. **确定性 & 安全**
   - 沿用 `manager_fetch` 的域名白名单（`ALLOWED_HOSTS`）、HTTPS、sha256（`ReleaseAsset.sha256`）——**四个引擎的 host 工具都能从 GitHub 官方 Release 拿到**（Magisk 走 APK 内的 x86_64 magiskboot，也仍来自 `topjohnwu/Magisk` release）。APatch 需把 `bmax121/KernelPatch` 加进白名单（**唯一新增域名，仍是 github.com**，不违反零遥测）。
   - **APatch 的 SuperKey**：官方要求 8–63 字母数字。这是**安全边界**——Lrom **不应**自动生成/接管（既有报告一致）。可设计为「用户输入」或「留空由 APatch APP 首次启动设置」（注：kptools 打 boots.img 时 SuperKey 已写死进镜像，若留空后续难改；需产品决策，建议交互输入）。

### 6.3 哪些能「脱机一键」、哪些只能「APP 手动」

| 管理器 | Lrom 能力定位 | 理由 |
|---|---|---|
| **KernelSU 官方** | ✅ **脱机一键（已完成）** | 官方 host ksud + 内置 LKM，实测跑通 |
| **KernelSU-Next** | ✅ **脱机一键（低成本新增）** | 同构 ksud，`--kmi` 用法一致；只需注册新引擎 + 处理 LKM 命名差异 |
| **APatch** | ✅ **脱机一键（可行，风险较高）** | 官方 `kptools-linux` 文档，实测跑通；但**打 boot.img、改内核、需 KALLSYMS、需 SuperKey**，失败后果重 |
| **Magisk** | 🟡 **进阶可选（非官方流程）** | APK 内藏 x86_64 magiskboot，实测跑通；但官方只认 APP，Lrom 应标注「非官方、仅本机镜像」 |
| **SukiSU-Ultra** | ❌ **APP 手动（官方）** / ⚠️ 自编译 host ksud 才可一键 | Release 无 host 二进制，官方安装=APP |

---

## 7. 可信度边界 / 未确认项

**高可信（①，Release 资产/源码/官方文档直读，2026-10-28 快照）**
- 四仓 Release 资产清单、KernelPatch 的 host 工具清单、APatch 只发 APK。
- KernelSU / KSU-Next 的 `cli_non_android` 子命令与 `boot-patch` 参数；ksud 拒绝 Magisk 镜像；ksud 内置 LKM（源码 `assets::get_asset("{arch}/{kmi}_kernelsu.ko")`）。
- Magisk APK 内含 `lib/x86_64/libmagiskboot.so` 且为 `statically linked`；`boot_patch.sh` 成员要求；Magisk 安装文档「绝不在别的设备 patch」「独立 vbmeta 可选 disable-verity」。
- APatch 官方「Manually patching」Linux 命令；APatch 只打 boot.img / 仅 ARM64 / 需 KALLSYMS / SuperKey 规则。
- SukiSU `ksud.yml` 只构建 Android 目标（无 host）。

**本机实测（[实测]，样本=OPD2404 真实镜像）**
- Magisk：APK 内 x86_64 magiskboot + arm64 运行件 + boot_patch.sh，端到端产出 `new-boot.img`（含 `.backup/`、`init`、`overlay.d/sbin/`），`magiskboot verify` 通过。
- APatch：`kptools-linux -p -i boot.img` 端到端产出 `boot_apatch.img`（KERNEL_SZ≈36MB、ramdisk=0），保留 AVB footer。
- KernelSU / KSU-Next：官方 & Next 的 musl ksud 均能在本机 Linux 跑，`--kmi android14-6.1`（内置 LKM）patch init_boot 成功。
- SukiSU：`ksud-x86_64-linux-android` 在本机 Linux **无法执行**（interpreter `/system/bin/linker64`）。

**中可信 / 需后续验证（②③）**
- 「跨管理器混用必然失败」多为社区/issue 证据 + 机制推断；未穷举所有 fork 组合。**保守结论：不混用。**
- APatch 的 kpimg 版本与 APatch 管理器版本的**精确兼容矩阵**未逐版实测；官方只说「KernelPatch 更新、管理器不变时可手动 patch」（暗示向前兼容），但极端跨大版本可能仍需实测。
- SukiSU 自编译 host ksud 的「可用性/版本风险」仅源码层面可行，未实际编译验证。
- Magisk PC 流程的**长期稳定性**：官方不背书，未来 Magisk 改 APK 结构（如去掉 x86_64 运行件）会破坏该路径。

**未确认**
- 本机 `boot.img` 的 `CONFIG_KALLSYMS_ALL` 是否 `=y`（APatch 硬前提，需 `adb shell zcat /proc/config.gz | grep CONFIG_KALLSYMS` 或 `/sys/module/*/sections` 实测）。→ **APatch 真要落地前必须验证**。
- Magisk 产物在**未解锁**设备上的可启动性（本机已解锁，未测锁机场景；官方红字亦警告需解锁）。

---

## 8. 附录：实测环境与原始命令/输出

环境：Linux x86_64（宿主），本机真实镜像 `/…/work/OPD2404_16.0.5.1201_CN01/{init_boot.img,boot.img}`，`ksud` 取自 `tiann/KernelSU v3.3.1`（`sha256=29b50f94…`，与 Release 资产一致）。

**A. Magisk（APK 内 x86_64 magiskboot 端到端）**
```text
$ file lib/x86_64/libmagiskboot.so
ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked, stripped
$ readelf -d lib/x86_64/libmagiskboot.so | grep NEEDED   # （无输出=零动态依赖）
$ bash ./boot_patch.sh ./init_boot.img
… Parsing boot image … Patching ramdisk … Repack to boot image: [new-boot.img] …
$ file new-boot.img   # 8,388,608 B
$ magiskboot unpack new-boot.img && magiskboot cpio ramdisk.cpio ls   # 含 .backup / init / overlay.d
$ magiskboot verify new-boot.img   # VBMETA, exit 0
```

**B. KernelSU / KernelSU-Next（内置 LKM，仅 `--kmi`）**
```text
$ ksud supported-kmis        # aarch64/android14-6.1 … x86_64/android17-6.18
$ ksud boot-patch -b init_boot.img --kmi android14-6.1 -o ./out --out-name p.img
  - Adding KernelSU LKM / - Adding bundled LKM config / - Done!
$ ksud boot-patch -b init_boot.img --kmi 6.1-android14-11 …   # ✗ asset not found（格式错）
$ ksunext-ksud boot-patch -b init_boot.img --kmi android14-6.1 -o ./outN --out-name pn.img   # ✓
```

**C. SukiSU（host 跑不动）**
```text
$ file ksud-x86_64-linux-android
ELF 64-bit LSB pie executable, x86-64, dynamically linked, interpreter /system/bin/linker64, for Android 26
$ ./ksud-x86_64-linux-android --help
bash: ./ksud-x86_64-linux-android: 无法执行：找不到需要的文件
```

**D. APatch / KernelPatch（kptools-linux 端到端）**
```text
$ file kptools-linux
ELF 64-bit LSB pie executable, x86-64, dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2, for GNU/Linux 3.2.0
$ ./kptools-linux -v -k kpimg-android     # d09
$ ./kptools-linux -p -i boot.img -k kpimg-android -s "TestKey12345" -o boot_apatch.img
  [+] kallsyms_lookup_name … [+] superkey configured … [+] Starting automatic repack …
  [+] AVB footer: image_size=35704832 … [+] Preserved original AVB metadata …
  [+] Repack completed: /tmp/boot_apatch.img
$ magiskboot unpack boot_apatch.img   # HEADER_VER[4] KERNEL_SZ[35997200] RAMDISK_SZ[0]  （内核已注入，ramdisk=0）
```

---

## 9. 引用（外部 URL）

- KernelSU 官方 Release v3.3.1（资产清单）：<https://github.com/tiann/KernelSU/releases/tag/v3.3.1>
- KernelSU 非 Android CLI 源码：<https://github.com/tiann/KernelSU/blob/main/userspace/ksud/src/cli_non_android.rs> ；patch 逻辑：<https://github.com/tiann/KernelSU/blob/main/userspace/ksud/src/boot_patch.rs>
- KernelSU 安装文档（LKM/APP 流程、KMI）：<https://kernelsu.org/guide/installation.html>
- KernelSU-Next Release v3.4.2：<https://github.com/KernelSU-Next/KernelSU-Next/releases/tag/v3.4.2>
- KernelSU-Next 版本不匹配 issue #917：<https://github.com/KernelSU-Next/KernelSU-Next/issues/917>
- SukiSU-Ultra Release v4.2.1-r2：<https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.1-r2>
- SukiSU ksud CI（仅 Android 目标）：<https://github.com/SukiSU-Ultra/SukiSU-Ultra/blob/main/.github/workflows/ksud.yml>
- SukiSU 安装文档：<https://sukisu.org/zh/guide/installation> ；兼容性：<https://sukisu.org/zh/guide/compatibility>
- APatch Release（仅 APK）：<https://github.com/bmax121/APatch/releases/tag/11282>
- KernelPatch Release 0.13.9（kptools-linux / kpimg-android 等）：<https://github.com/bmax121/KernelPatch/releases/tag/0.13.9>
- APatchDocs 安装/手动 patch 文档：<https://github.com/AndroidPatch/APatchDocs/blob/main/docs/install.md>
- KernelPatch kptools 命令参考（DeepWiki）：<https://deepwiki.com/bmax121/KernelPatch/8.1-kptools-command-reference>
- SukiSU ksud CLI（DeepWiki）：<https://deepwiki.com/ShirkNeko/SukiSU-Ultra/3.1-ksud-daemon-and-cli>
- Magisk Release v31.1（仅 APK）：<https://github.com/topjohnwu/Magisk/releases/tag/v31.1>
- Magisk `boot_patch.sh` 源码：<https://github.com/topjohnwu/Magisk/blob/master/scripts/boot_patch.sh>
- Magisk 安装文档（AVB/vbmeta、patch 流程、设备警告）：<https://topjohnwu.github.io/Magisk/install.html>
- wildkernels 安装后校验（管理器须匹配口味）：<https://github.com/wildkernels/gki_kernelsu_susfs/blob/00d2d90477f4b703b9890fc820be7391957fc2e8/docs/post-install.md>
- 管理器/内核版本配套分析：<https://www.privacyportal.co.uk/blogs/free-rooting-tips-and-tricks/should-kernelsu-next-manager-and-kernel-versions-match>
- 内部交叉参考：[`violet-auto-root.md`](violet-auto-root.md)（紫罗兰脱机修补=成熟 patcher 封装）、[`root-managers-kernelsu.md`](root-managers-kernelsu.md)、[`root-managers-magisk.md`](root-managers-magisk.md)
