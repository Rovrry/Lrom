# Lrom

> **L**inux **rom** flashing toolkit — 在 Linux 上刷 Android ROM 的交互式工具
>
> 面向欧加（OPPO / OnePlus / realme）设备，专注把「分析 → 备份 → 预演 → 刷写」这条链路做对、做安全。

![status](https://img.shields.io/badge/status-alpha-orange)
![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![platform](https://img.shields.io/badge/platform-Linux-informational)
![python](https://img.shields.io/badge/python-3.10%2B-blue)

---

## 这是什么

Lrom 是一个 **Linux 原生的 Android 线刷工具**。它把刷机拆成可审阅、可回退的步骤，
默认做足安全检查，并且**永不替你自动重启设备**。

它解决 Windows 版工具普遍存在的三个问题：

| 问题 | Windows 版工具 | Lrom |
|---|---|---|
| **增量包误判** | 不检查，解出残缺镜像照样刷 → 变砖 | 解析 manifest，增量包**直接拒绝** |
| **型号不匹配** | 不校验 | 比对 `pre-device`，不匹配**硬阻止** |
| **降级熔断** | 部分只警告 | 解析 ARB 索引，降级**硬阻止** |

## 特性

- ✅ **包分析** — 识别全量/增量 OTA、型号、版本、安全补丁
- ✅ **刷前备份** — adb+root `dd` 备份 boot/init_boot/vbmeta 等关键分区，附 SHA-256 清单
- ✅ **刷写计划** — 纯数据、可审阅、可导出 JSON
- ✅ **预演模式** — 完整校验流水线，不写设备
- ✅ **ARB 熔断检测** — 防降级硬砖
- ✅ **两阶段刷写** — Fastboot（物理分区）+ FastbootD（逻辑分区）
- ✅ **关键分区保护** — `persist`/`modemst`/`fsg` 等默认禁写
- ✅ **零遥测** — 完全离线，不上报任何数据

## 快速开始（无需安装）

```bash
git clone https://github.com/Rovrry/Lrom.git
cd Lrom
./lrom              # 交互式菜单
./lrom devices      # 或直接用子命令
```

**Lrom 零第三方依赖**（只用 Python 标准库），克隆即可运行，**不需要安装**。

**卸载**：直接删除仓库目录即可，不会在系统中留下任何文件。

<details>
<summary>可选：把 lrom 加入 PATH（能在任意目录直接输入 <code>lrom</code>）</summary>

```bash
./install.sh        # 会询问确认
```

卸载系统安装：
```bash
./uninstall.sh
```
</details>

需要 `adb` 与 `fastboot`（从发行版仓库安装，**不需要 root**，Linux 内核自带 USB 驱动）：

```bash
# Debian / Ubuntu / Kali
sudo apt install adb fastboot android-sdk-platform-tools-common

# Arch
sudo pacman -S android-tools android-udev

# Fedora
sudo dnf install android-tools
```

> **注意**：Kali/Debian 的包名是 `adb` / `fastboot`，不是废弃的 `android-tools-adb`。
> `android-sdk-platform-tools-common` 提供 udev 规则，不装的话普通用户可能无法访问设备。

安装后建议先跑一次自检：

```bash
lrom selfcheck
```

它会检查 adb/fastboot、udev 规则、用户组权限、adb server 归属，并给出可直接复制的修复命令。

## 使用

### 完整流程

```bash
# 1. 分析 ROM 包，确认能否线刷
lrom inspect ~/Downloads/rom.zip

# 2. 备份设备关键分区（强烈建议）
lrom backup

# 3. 解包出分区镜像
lrom unpack ~/Downloads/rom.zip -o unpacked/rom

# 4. ARB 熔断检测
lrom arb --images unpacked/rom

# 5. 预演
lrom flash ~/Downloads/rom.zip --images unpacked/rom --dry-run

# 6. 真正刷写
lrom flash ~/Downloads/rom.zip --images unpacked/rom
```

### 命令一览

| 命令 | 说明 |
|---|---|
| `lrom` | 无参数进入交互菜单（含 Root 一节） |
| `lrom devices` | 检测连接设备 |
| `lrom selfcheck` | 环境自检（adb/udev/权限/adb server） |
| `lrom inspect <包>` | 分析 ROM 包 |
| `lrom backup` | 刷前备份关键分区（需 root） |
| `lrom unpack <包>` | 解包出分区镜像 |
| `lrom plan <包>` | 生成刷写计划 |
| `lrom arb` | ARB 熔断检测 |
| `lrom flash <包>` | 执行刷写（含 ARB 熔断保护）|
| `lrom root` | 自动化 root（patch init_boot） |
| `lrom fetch-manager` | 下载 root 管理器 APK（**唯一联网命令**） |

### 自动化 root

刷完机后用全量包里留下的 `init_boot.img` 建立 root，**整个流程离线**：

```bash
./lrom root --dry-run      # 先看计划
./lrom root                # ksud 方式，全自动
./lrom root --method app   # 用手机上的 Manager APP 修补
```

`--method ksud` 需要自行准备 [ksud](https://github.com/tiann/KernelSU/releases)
（Lrom 不捆绑，保持零依赖），放到 PATH 或用 `LROM_KSUD=/path/to/ksud` 指定。

刷入模式**由你选择**：

```bash
./lrom root --flash-mode fastboot    # bootloader 模式（常规）
./lrom root --flash-mode fastbootd   # 用户空间模式
./lrom root                          # 不指定则交互询问
```

> **什么时候需要 fastbootd**：如果 OCDT 分区被恶意脚本抹掉，
> bootloader 会进不去、fastboot 完全不可用。fastbootd 由系统里的
> 用户空间服务启动、不依赖 OCDT，此时是唯一还能刷写的通道。

下载 root 管理器（唯一联网命令，支持正式版/测试版，多来源）：

```bash
./lrom fetch-manager --source ksu --channel stable   # KernelSU 正式版
./lrom fetch-manager --source ksu --channel beta     # KernelSU 测试版
adb install -r root-work/KernelSU_*.apk
```

来源可选 `ksu` / `ksu-next` / `sukisu` / `magisk`，始终取 GitHub 最新发布。

`lrom root` 会自动完成：校验 ramdisk、按内核版本推导 KMI、解包并 patch、
刷入 init_boot，并在动手前把原厂镜像留存到 `root-work/`。

失败可随时恢复，`lrom root` 会在刷写前把原厂镜像存到 `root-work/`：

```bash
fastboot flash init_boot root-work/init_boot-stock-<时间>.img
```

### ARB 熔断保护

部分一加/OPPO 机型从特定版本起会提高 `xbl_config` 的 ARB 值，
**降级刷入会永久黑砖，且免授权 9008 已失效无法自救**。

Lrom 内置了实测的熔断机型表，`lrom plan` 和 `lrom flash` 会在写设备前检查：

```
✗ ARB 熔断保护：拒绝刷写

  ⛔ 这个包是 16.0.3.501 之前的版本，而该机型从那时起已熔断 ARB。
    刷入等于降级到熔断前，会导致永久黑砖，且免授权 9008 已失效，无法自救。
```

已收录：一加平板 Pro/Pad 2、一加 13/13T/13s/12、Ace 5/3 Pro/6、
Pad 2 Pro/3 等。同时维护「安全表」（一加 15、Ace 5 Pro 等实测未熔断机型
永不误伤）。

精确判断请用 `lrom arb`，或在线查 https://oparb.pages.dev/

### 联网说明

Lrom 除 `fetch-manager` 外**不发起任何网络请求**。该命令的约束：

- 只访问 `api.github.com` / `github.com` / `objects.githubusercontent.com`
- 强制 HTTPS，不降级
- 下载后强制校验 sha256，不匹配立即丢弃文件
- 只下载文件，**绝不执行任何远程代码**
- 不发送任何设备信息或统计（零遥测）


### 关键参数

| 参数 | 说明 |
|---|---|
| `--dry-run` | 预演检查，不写设备（见下方说明） |
| `--wipe` | 清空用户数据（**降级/救砖必用**） |
| `--partitions` | 只刷指定分区 |
| `--both-slots` | 双槽刷写（**仅限物理分区**，见下方说明） |
| `--allow-dangerous` | 允许写危险分区 |
| `--force-arb` | 跳过 ARB 拦截（极度危险） |

## ⚠️ `--both-slots` 的适用范围

`--both-slots` **只对物理分区生效**（boot / xbl / vbmeta / dtbo 等）。

**逻辑分区（system / vendor / odm / my_* 等，位于 `super` 内）不会被双槽刷写**，
因为 `super` 是单个物理分区，两槽共用同一空间：

```
super 总容量      14.00 GB
单槽逻辑分区需求   8.97 GB
双槽合计          17.94 GB   ← 放不下
```

强行双槽会报 `Not enough space to resize partition`。
Lrom 已在代码层面禁止该行为，并在预检阶段校验 `super` 空间。

## 预演检查是什么

`--dry-run`（或菜单第 4 项）**只检查、不写入**。它回答四个问题：

```
╭─ 预演报告 ────────────────────────────────────────────╮
│ 【刷写规模】
│   阶段一 Fastboot   : 32 个分区
│   阶段二 FastbootD  : 15 个逻辑分区
│   合计写入          : 9.66 GB
│
│ 【镜像完整性】
│   ✓ 47 个镜像文件全部存在且非空
│
│ 【风险项】
│   ! 16 个 bootloader 链分区将被写入
│       tz, uefi, hyp, xbl, keymaster, ...
│
│ 【数据影响】
│   ✓ 不会动 userdata，用户数据保留
│
│ 【结论】
│   ✓ 可以安全刷写
╰──────────────────────────────────────────────────────╯
```

**什么时候用**：真刷之前，尤其是首次使用某个 ROM 包时。
它能提前发现"镜像没解完整""包和机型不匹配""会误删数据"等问题，
避免刷到一半才发现。

## ⚠️ 重要：Lrom 不会自动重启设备

刷写完成后，Lrom 会停下来让你确认。这是刻意的设计——**重启的决策权必须留给你**。

### 如果是降级，必须先格式化

> **降级**（刷入版本低于原系统）时，**必须先格式化系统分区**，否则会无法开机甚至变砖。

```bash
# 方式一：Fastboot 下执行
fastboot -w

# 方式二：进入 Recovery，选择「格式化 data / 恢复出厂设置」
```

用 Lrom 刷降级包时，请加 `--wipe`：

```bash
lrom flash old_rom.zip --images unpacked/rom --wipe
```

### 确认后手动重启

```bash
fastboot reboot      # 当前在 fastboot
# 或长按电源键 10 秒
```

## 支持范围

| 包格式 | 状态 |
|---|---|
| 欧加（OPPO/OnePlus/realme）全量 OTA 包 | ✅ 已验证 |
| 通用 payload.bin 全量包 | ✅ |
| 增量 OTA 包 | ❌ 明确拒绝 |
| 小米 tgz / OFP / OPS / OZIP | 📋 计划中 |

**v0.1 不包含**：GUI、EDL(9008) 直连、OFP 解密、增量 OTA、自动 Root、联网下载 ROM。

## 安全警告

⚠️ **刷机有风险，可能变砖、丢失数据、失去保修。** 请务必：

1. 刷机前**备份所有数据**，并先运行 `lrom backup`
2. 确认设备型号与 ROM 包**完全匹配**（`lrom inspect` 会校验）
3. 首次使用先跑 **`--dry-run` 预演**
4. **降级必须格式化**，否则会变砖
5. 保持设备电量 > 50%，使用**直连 USB 2.0 端口**（不要用 Hub）

开发者不对任何设备损坏负责。详见 [LICENSE](LICENSE)。

## 已验证环境

| 项目 | 详情 |
|---|---|
| 设备 | OnePlus OPD2404（一加平板 Pro），Android 16，A/B 双槽 |
| 系统 | Kali GNU/Linux Rolling 2026.2 |
| 刷机结果 | `16.0.5.702` → `16.0.5.1201`，34 个分区成功，用户数据保留 |

> 注：该设备 BL 已解锁但未执行 `unlock_critical`，13 个关键分区（xbl/abl/tz 等）无法写入。
> Lrom 会自动识别并跳过，不影响正常开机。

## 开发

实验记录与方案调研：

- **[docs/ARB-实验报告.md](docs/ARB-实验报告.md)** —— ARB 熔断对照实验，
  出厂包 `ARB=0` 与已熔断设备 `ARB=1` 的完整对照数据
- **[docs/ROOT-IMPLEMENTATION.md](docs/ROOT-IMPLEMENTATION.md)** —— root 自动化实现方法
- **[docs/research/](docs/research/)** —— root 方案调研与来源
  （KernelSU / Magisk / 欧加 bootimg 结构 / 可行性分析）

版本历史见 **[CHANGELOG.md](CHANGELOG.md)**。

```bash
# 运行测试
python3 -m pytest tests/unit -q

# 本地安装
pip install -e ".[dev]"
```

测试覆盖（364 个）：

| 文件 | 覆盖内容 |
|---|---|
| `test_payload.py` | protobuf 解析、AOSP 枚举、大端头部 |
| `test_package.py` | 包识别、全量/增量判定、型号校验 |
| `test_plan.py` | 分区分级、刷写顺序、危险分区 |
| `test_unpack.py` | 解压、extent 布局、SHA-256、拒绝增量 |
| `test_flash.py` | 刷写流程、失败中止、关键分区跳过 |
| `test_arb.py` | ARB 解析与降级拦截、fetch 健壮性 |
| `test_backup.py` | 备份清单（OCDT）、分区槽位映射 |
| `test_arb_db.py` | ARB 熔断机型数据库、降级阻断 |
| `test_monitor.py` | 设备监测 |
| `test_menu_integrity.py` | 菜单模块 AST 名字解析（防误删函数） |
| `test_bootimg.py` | boot image 解析、ramdisk 检查、AVB footer |
| `test_root.py` | KMI 推导、root 计划、原厂留存、patch 后校验、刷入模式 |
| `test_manager_fetch.py` | 联网协议：域名/HTTPS 白名单、摘要校验 |

## 许可证

[GPL-3.0-or-later](LICENSE)

## 致谢

功能规划上参考了 [VioletToolBox](https://github.com/Smart-Paocai/VioletToolBox)（GPL-3.0）的实践经验，
但为完全独立实现，不使用其代码、商标与素材。
Payload 格式遵循 AOSP [update_metadata.proto](https://android.googlesource.com/platform/system/update_engine/)。
