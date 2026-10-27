# ARB 对照实验报告

> 实验日期：2026-10-27
> 设备：OnePlus OPD2404（一加平板 Pro）· OP5D77L1 · Snapdragon 8 Gen 3 (pineapple)
> **本实验全程只读，未向设备写入任何字节。**

---

## 0. 一句话结论

**出厂版本 `14.1.0.226` 的 ARB = 0，设备当前 ARB = 1。**
**降级到出厂版本会直接黑砖，不可恢复。绝对不要尝试。**

---

## 1. 实验目的

用户希望用早期出厂 ROM（据说带免授权 9008）作为对照，判断是否存在
安全的降级路径。实验前用户明确要求：**禁止将出厂版本 ROM 刷入现有设备**。

本实验遵守该约束，只做只读分析。

---

## 2. 实验方法

1. 读取出厂包 `META-INF/com/android/metadata`，确认版本
2. 用 `lrom unpack --partitions xbl_config` 只解出 `xbl_config.img`（0.2 MB，秒级）
3. 用 `lrom core.arb.parse_xbl_config()` 解析 ARB
4. 从设备 dd 出当前 `xbl_config_a`（需 root）
5. 三方对照 + 二进制级比对

---

## 3. 数据

### 3.1 两个包的版本信息

| 项 | 出厂包 | 当前设备 |
|---|---|---|
| 版本名 | `OPD2404_14.1.0.226(CN01)` | `OPD2404_16.0.5.1201(CN01)` |
| Android | 14.1 | 16 |
| 安全补丁 | 2024-06-05 | — |
| build 指纹 | `U.18ac812-c3c8-c3c9` | `U.247d78a_a3a1b_629cb` |
| 代号 | `OP5D77L1` | `OP5D77L1` |
| ota-id | `OPD2404_11.A.31_0310_202406221852` | — |

### 3.2 ★ 核心数据：ARB 值

| 来源 | ARB | 说明 |
|---|---|---|
| **出厂包 `14.1.0.226`** | **0** | 降级不会触发 ARB |
| **当前设备（已熔断）** | **1** | 已处于熔断状态 |
| 当前包 `16.0.5.1201` | 1 | 与设备一致 |

### 3.3 二进制级对照

| 项 | 出厂 | 设备当前 |
|---|---|---|
| `xbl_config.img` 大小 | 233,472 字节 | 307,200 字节 |
| 结构版本 | 3.0 | 3.0 |
| ARB | 0 | 1 |

**相同长度区间内，48.1%（112,257 / 233,472）的字节不同。**

说明这是两代差异很大的 bootloader 配置，不是简单改了个 ARB 数值。

---

## 4. 结论

### 4.1 直接结论

```
lrom arb --images unpacked/factory --xbl-config unpacked/factory/xbl_config.img
```

输出：

```
│ 包内 xbl_config : ARB=0 (version 3.0)
│ 设备当前        : ARB=1 (version 3.0)
╰────────────────────────────────────────────────────────
  结论: 禁止（降级会变砖）
  包 ARB=0 低于设备已熔断值 1，刷入会导致硬件级变砖（不可恢复）
```

**这是不可跨越的鸿沟。** 设备 ARB=1 且不可回退，
任何 ARB=0 的包都不能刷。

### 4.2 三个必须澄清的误区

#### ❌ 误区一：刷早期 ROM 能恢复免授权 9008

**不能。** EDL 免授权状态由设备侧决定，不是 ROM 侧。
一旦失效就是单向的，刷任何 ROM 都救不回来。

#### ❌ 误区二：ARB=0 的包是"安全的退路"

**对它自己安全，对你不是。** ARB=0 的包只有在**设备也是 ARB=0** 时才安全。
你的设备已经 ARB=1，刷 ARB=0 的包 = 直接黑砖。

这正是注意事项里写的：

> ✗ 不要用"本地安装"降级到 ARB=0 的包
> → **这是社区记录里最常见的黑砖触发方式，会直接黑屏变砖**

#### ❌ 误区三：官方说"恢复降级能力"所以还能等

官方 2026-01 声明说"暂时暂停，下个更新恢复"。
但截至 2026-10-27，**所有中招机型 ARB 仍全部 =1，没有一个回到 0**。

按注意事项的结论：**承诺没兑现，按永久处理。**

---

## 5. 你的机型在熔断名单里

来自 OnePlus ARB Checker（GitHub Actions 每日扫描 OTA 包的实测数据）：

> **一加平板 Pro → OPD2404_16.0.3.501 及之后**

你的设备是 `16.0.5.1201`，远在 `.501` 之后，**确认已熔断**。

同批次中招的还有：一加 13 / 13R / 13T / 13s、一加 12、
Ace 6 / Ace 3 Pro、OnePlus Pad 2 / 2 Pro / 3。

> 注意：媒体常把"一加 15 = 熔断"写错，实测一加 15 全版本 ARB=0。
> **信实测数据，别信媒体表格。**

---

## 6. 现在能做什么 / 不能做什么

### ✅ 安全

| 操作 | 说明 |
|---|---|
| 刷**同版本或更高**的官方包 | ARB=1 → ARB=1 或更高，可行 |
| 用 `lrom root` 保持 root | 只动 `init_boot`，不碰 BL 相关分区 |
| 备份分区（尤其 OCDT） | 只读，零风险 |
| 用 ARB Checker 查其他机型 | https://oparb.pages.dev/ |

### ⚠️ 高风险（不建议）

| 操作 | 风险 |
|---|---|
| 刷 ARB=0 的包（包括这个出厂包） | **直接黑砖** |
| 第三方 ROM | 基本都构建于熔断前，刷入触发 ARB |
| 跨区转换（国行 ↔ OxygenOS） | 几乎必砖 |
| "免熔断升级"教程（改 BL 分区） | 窗口期出错即黑砖且无救砖通道 |

### ❌ 已不可能

| 愿望 | 现实 |
|---|---|
| 恢复免授权 9008 | 单向不可逆 |
| 降级到 Android 14/15 | ARB 阻断 |
| 用 EDL 自主救砖 | 无免授权通道，需走 OTA 云授权/售后 |

---

## 7. 如果将来真要救砖

按注意事项，ARB=1 状态下唯一可行的路径：

1. **走售后** —— 官方声明说可联系售后降级，但需先电话确认网点有对应固件
2. **OPlus EDL Tool V3 + 高通云 loader** —— XDA 有一例 ARB=1 的一加 12 成功救活
   - 必须 V3.2026.028（V2 只出 logo 后黑屏）
   - Cloud Loader 选 SM8650 (8 Gen3 NEW)
   - 需装 Qualcomm Software Center（要注册高通账号）
   - **必做：取消勾选 persist + 勾选 Protect LUN5**
   - 依赖云授权，不是"免授权"

---

## 8. 最重要的防护建议

**如果 OCDT 还没备份，现在立刻备份。**

```
lrom backup
```

`engineering_cdt` 和 `ocdt` 已加入默认清单（v0.4.5）。

原因见注意事项第【十一】节后果链：

```
免授权 9008 失效 → 失去 EDL 救砖通道 → OCDT 一旦损坏
→ 售后也救不了 → 只能拆字库 / 换主板
```

**OCDT 是本机独有的**，用别的设备的会进不了 Fastboot，必须备份自己这份，
并另存到 U 盘/网盘。

---

## 9. 参考资料

- OnePlus ARB Checker（实测数据源）
  https://github.com/Bartixxx32/Oneplus-antirollchecker
  https://oparb.pages.dev/
- 一加官方声明（9to5Google 全文引用，2026-01-27）
  https://9to5google.com/2026/01/27/oneplus-antirollback-measures-only-temporary-downgrades-returning-soon/
- 技术分析《熔断的本质》（酷安 @小黄泊）
  https://www.coolapk.com/feed/69935474
- XDA 熔断总帖
  https://xdaforums.com/t/critical-warning-coloros-16-0-8-xxx-16-0-3-501-permanent-anti-rollback-arb-fuse-blown-do-not-update.4775930/
- ARB=1 救砖成功案例
  https://xdaforums.com/t/unbrick-oneplus-pjd110-chinese-version-arb-1.4804310/

---

## 附录：原始数据文件

保存在 `arb-lab/`：

| 文件 | 来源 | ARB |
|---|---|---|
| `xbl_config-factory-14.1.0.226.img` | 出厂包解出 | 0 |
| `xbl_config-device-current.img` | 设备 dd 读出 | 1 |

---

*实验执行：Lrom v0.4.5 · 全程只读*
