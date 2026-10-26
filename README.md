# Lrom

> **L**inux **rom** flashing toolkit — 在 Linux 上刷 Android ROM 的交互式工具
>
> 面向欧加（OPPO / OnePlus / realme）设备，专注把「解包 → 校验 → 线刷」这一条链路做对、做安全。

![status](https://img.shields.io/badge/status-alpha-orange)
![license](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)
![platform](https://img.shields.io/badge/platform-Linux-informational)

---

## 这是什么

Lrom 是一个 **Linux 原生的 Android 线刷工具**，提供交互式菜单，让不熟悉命令行的用户也能安全地刷写 ROM。

它不是 Windows 工具的简单移植，而是围绕三个设计原则重写的：

1. **不砖机 > 功能多** — 危险分区默认禁写，刷前先预演。
2. **离线优先** — 核心链路不依赖任何服务器，零遥测。
3. **可审阅** — 刷写计划是纯数据，执行前可见、可改。

## 设计原则

- **先计划，后执行**：任何写操作先生成可审查的刷写计划（JSON），确认后才动手。
- **预演（dry-run）是一等公民**：能模拟出"将写哪些分区、共多少 GB、有哪些风险"。
- **危险分区硬阻止**：`xbl` / `abl` / `persist` / `modemst*` 等默认禁止写入。
- **ARB 降级硬阻止**：`rollback_index` 降级 = 硬件级变砖，直接拒绝。
- **完全离线、零遥测**：不上报任何数据。

## 支持范围（v0.1 规划中）

| 包格式 | 状态 |
|---|---|
| 欧加（OPPO/OnePlus/realme）全量包 | 🚧 规划中 |
| 通用 payload.bin 包 | 🚧 规划中 |
| 散装 fastboot 包 | 🚧 规划中 |
| 小米 tgz 线刷包 | 📋 计划中 |
| OFP / OPS / OZIP | ❌ 明确不支持 |

> **v0.1 不包含**：GUI、EDL(9008) 直连、OFP 解密、增量 OTA、自动 Root、联网下载 ROM。

## 安装

> 尚未发布，开发中。

```bash
# 规划中
git clone https://github.com/<owner>/Lrom.git
cd Lrom
./install.sh
```

## 使用

> 尚未发布，开发中。

```
  1) 解全量包          2) 查看包信息        3) 生成刷写计划
  4) 预演检查          5) 线刷全量包        6) 只刷指定分区
  7) 备份设备分区      8) 设备信息          9) 启动到 Fastboot
 10) 环境自检         11) 查看日志          0) 退出
```

## 安全警告

⚠️ **刷机有风险，可能变砖、丢失数据、失去保修。** 请务必：

1. 刷机前**备份所有数据**
2. 确认设备型号与 ROM 包**完全匹配**
3. 首次使用先跑**预演检查**
4. 保持设备电量 > 50%，使用**直连 USB 2.0 端口**（不要用 Hub）

开发者不对任何设备损坏负责。详见 [LICENSE](LICENSE) 中的免责条款。

## 环境要求

- Linux（Ubuntu 22.04+ / Debian 12+ / Kali / Fedora / Arch）
- `adb`、`fastboot`（从发行版仓库安装，无需 root）
- Python ≥ 3.10

## 许可证

[GPL-3.0-or-later](LICENSE)

## 致谢

本项目在功能规划上参考了 [VioletToolBox](https://github.com/Smart-Paocai/VioletToolBox)（GPL-3.0）的实践经验，但为独立实现，不使用其代码、商标与素材。
