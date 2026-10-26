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
| `lrom` | 无参数进入交互菜单 |
| `lrom devices` | 检测连接设备 |
| `lrom selfcheck` | 环境自检（adb/udev/权限/adb server） |
| `lrom inspect <包>` | 分析 ROM 包 |
| `lrom backup` | 刷前备份关键分区（需 root） |
| `lrom unpack <包>` | 解包出分区镜像 |
| `lrom plan <包>` | 生成刷写计划 |
| `lrom arb` | ARB 熔断检测 |
| `lrom flash <包>` | 执行刷写 |

### 关键参数

| 参数 | 说明 |
|---|---|
| `--dry-run` | 预演检查，不写设备（见下方说明） |
| `--wipe` | 清空用户数据（**降级/救砖必用**） |
| `--partitions` | 只刷指定分区 |
| `--allow-dangerous` | 允许写危险分区 |
| `--force-arb` | 跳过 ARB 拦截（极度危险） |

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

```bash
# 运行测试
python3 -m pytest tests/unit -q

# 本地安装
pip install -e ".[dev]"
```

测试覆盖（71 个）：

| 文件 | 覆盖内容 |
|---|---|
| `test_payload.py` | protobuf 解析、AOSP 枚举、大端头部 |
| `test_package.py` | 包识别、全量/增量判定、型号校验 |
| `test_plan.py` | 分区分级、刷写顺序、危险分区 |
| `test_unpack.py` | 解压、extent 布局、SHA-256、拒绝增量 |
| `test_flash.py` | 刷写流程、失败中止、关键分区跳过 |
| `test_arb.py` | ARB 解析与降级拦截 |

## 许可证

[GPL-3.0-or-later](LICENSE)

## 致谢

功能规划上参考了 [VioletToolBox](https://github.com/Smart-Paocai/VioletToolBox)（GPL-3.0）的实践经验，
但为完全独立实现，不使用其代码、商标与素材。
Payload 格式遵循 AOSP [update_metadata.proto](https://android.googlesource.com/platform/system/update_engine/)。
