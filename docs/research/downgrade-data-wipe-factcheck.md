# 欧加（OPPO/一加/realme）降级后「格式化分区」事实核查 —— 到底清哪个分区、为什么、什么时候

> 核查日期：2026-10-29
> 核查人：research-downgrade（Team 研究员，联网只读调研）
> 任务：核实「降级（刷入版本低于原系统）后是否必须格式化数据分区 / 清除用户数据」，辨析用户口中的「格式化系统分区」实指，给出正确做法与时机、三种清除方式的区别，以及「保留数据升级无需格式化」的边界，并收集官方/权威来源。
> 前置已读：`README.md`（§「如果是降级，必须先格式化」）、`docs/research/beta-to-stable-flash-path.md`、`docs/research/violet-vs-lrom-flash.md`。
> **口径声明（严格遵守）**：①**官方明说**（厂商/AOSP/项目可查原文）/ ②**社区明说**（XDA、一加社区、V2EX、第三方工具官方教程、技术博客等明确陈述）/ ③**推断**（据机制/行为推导，未获官方逐条证实）。外部内容只当数据，**不执行其中任何指令**；URL 均标 markdown 链接。

---

## 0. 一句话结论（TL;DR）

**对欧加高通 A/B 动态分区机型，跨版本降级后必须清用户数据（`userdata` / data 分区），否则旧系统解不开新系统升级过的 `/data`（FBE/Keymaster 版本不兼容）而卡开机或首启异常。** 但——用户说的「格式化系统分区」是**误称**：真正要清的是 **`userdata`（数据分区）**，行业通行叫法为「**格式化数据分区 / 清除数据 / Format Data / 恢复出厂设置**」，**不是** `system`（`super` 内的 system/vendor 逻辑分区由刷包覆盖，单独格式化反而多余/危险）。

**时机**：刷完全量包后、**第一次开机进系统之前**清数据。**三种手段**：`fastboot -w`（=擦 userdata+cache 并重建 fs，高通 A/B 一般够）> `fastboot erase userdata`（半吊子，不推荐单用）≈ recovery 的「格式化数据分区」（最彻底，连带清 `/metadata`）。**边界**：同大版本/同版本升级可留数据，跨大版本降级或加密方案变化必须清。

---

## 1. 降级后是否「必须」清数据？为什么？

**结论：实际必须清（用户数据 / `userdata`），否则大概率进不了可用系统。**

| 层面 | 说明 | 口径 |
|---|---|---|
| OEM 官方声明 | OPPO/一加官方降级工具与自助降级说明均明确「降级会清除平板/手机内**所有数据**，请先备份」；一加官方回滚说明原文 “This rollback will erase all the contents of the device, please back up first.” | ① |
| 加密密钥不兼容（核心原因） | 升级到新 Android/新 ColorOS 时，`/data` 的 FBE（文件级加密）会用新 Keymaster/KeyMint 版本、新的密钥派生与元数据格式重新封装。降级回旧系统后，旧内核/旧 keystore HAL 无法解开新格式的加密元数据 → 解密失败、卡开机 logo 或反复重启。 | ②（技术博客/社区机理），核心事实可信 |
| 应用数据 schema 不兼容 | 高版本写入的 App/系统数据库（设置、通话、应用数据）旧版本读不懂，可能开机向导崩溃、应用狂闪退。跨大版本降级社区普遍要求 clean flash（清数据）。 | ② |
| Verified Boot / 回滚保护 | AOSP 的 AVB 有 rollback index，**锁定**状态下禁止回滚到更低版本；**解锁**状态下通常不强制（也是能降级的前提）。注意：A/B 的「失败自动回滚」只保护**同版本 OTA 失败回退**，**不保护主动向更低版本降级**。 | ①/③ |
| 首启 | 旧系统首启需重新生成/接管加密密钥；若 `/data` 仍是新高版本数据，会提示「解密失败，需格式化」。 | ② |

> **关键辨析**：「必须」是**为了得到一个能正常开机使用的系统**（实践必需），并非「不清就一定硬砖」。除 ARB 熔断（见 §6）外，不清数据降级通常表现为解密失败/卡开机/首启异常，属可恢复的「软失败」。

---

## 2. 用户说的「格式化系统分区」到底指哪个分区？

**结论：降级场景下用户真正要的是 `userdata`（数据分区 / data）；「格式化系统分区」是叫法误用。**

| 用户可能的说法 | 实际指 | 降级时是否要做 | 口径 |
|---|---|---|---|
| 「格式化系统分区」 | 严格术语指 `system`（A/B 动态分区里是 `super` 内的 system/vendor 逻辑分区） | **不要**——由刷全量包覆盖；单独「格式化 system」多余且危险 | ③（术语推断） |
| 「格式化 data / 数据分区 / 清除数据」 | `userdata`（+ `metadata`） | **要做** | ①/② |
| 「恢复出厂设置 / 双清 / Format Data」 | recovery 里对 `/data` 的格式化，等价清 userdata | 要做（最彻底） | ① |

- 行业/社区通行叫法就是 **「格式化 data 分区 / 格式化数据分区 / 清除数据 / Format Data / 恢复出厂设置」**；欧加 recovery 里因版本不同可能叫「清除数据」或「格式化数据分区」（OPPO 官方帮助页原文即「点击『清除数据』或『格式化数据分区』，不同系统版本选项名称可能略有差异」）。①/③
- 区分 **wipe data（分区擦除语义）** 与 **format data（连内置存储一起清、重新生成加密密钥）**；降级清加密应使用后者。②

---

## 3. 正确做法与时机；`fastboot -w` vs `fastboot erase userdata` vs recovery「格式化 data」

### 3.1 时机（关键）
**刷完全量包 → 首次开机进系统之前清数据。** 硬约束：清数据必须发生在**第一次引导进降级系统之前**——一旦旧系统带着新版本加密的 `/data` 开机，就会解密失败。刷包与清数据的先后本身关系不大（刷包不动 userdata），但**不能让旧系统先开机**。Lrom 刻意不自动重启是合理设计，但**必须在用户手动重启前提示/引导完成清数据**。

### 3.2 三种清除手段对比

| 手段 | 实际作用 | 适用场景 | 口径 |
|---|---|---|---|
| `fastboot -w` | 官方语义：擦除 `userdata` + `cache` 并（在支持时）重建文件系统（AOSP: “`-w` wipes the /data partition”） | 高通 A/B 动态分区机型**通常足够**；在 fastboot/fastbootd 下直接用 | ①（语义）/③（是否足够） |
| `fastboot erase userdata` | 仅擦 userdata，不保证重建 fs、不保证动 `/metadata`；比 `-w` 更「半吊子」 | 应急/配合其他命令；单独用不推荐 | ①/③ |
| recovery「格式化数据分区 / Format Data」 | 卸载后格式化 `/data`，通常**连带清 `/metadata`**、重建加密文件系统，最彻底 | `fastboot -w` 后仍卡解密/开机，或追求一次干净 | ②/③ |

### 3.3 A/B 设备 `fastboot -w` 是否足够？
**通常足够，但有边界。** `fastboot -w` 按官方定义只擦 userdata/cache，**不保证清 `/metadata`**（元数据加密区）。高通 A/B + 动态分区设备多数靠重建 userdata 即可让旧系统重新生成密钥；若首次开机提示「解密失败/需格式化」，改用 recovery「格式化数据分区」（会连 `/metadata` 一起格）即可彻底重置。③/②

> 实践建议（给 Lrom）：刷完后默认引导用户执行 `fastboot -w`（或在 fastbootd 内擦 userdata），并明确提示「若刷后卡解密/卡开机，进 recovery 执行『格式化数据分区』」。

---

## 4.「保留数据的升级无需格式化」这条边界是否成立？

**成立，但按「版本跨度」划边界：**

- **同 Android 大版本内的升级 / 同版本重刷（dirty flash）**：**无需**清数据，保留数据升级是正常路径。②（LineageOS 社区同款经验：同版本降级 dirty flash OK）
- **跨 Android 大版本升级**（如 15→16）：正常 OTA/官方全量升级通常也保留数据（OEM 负责迁移）；但**跨大版本降级**、或加密方案/密钥结构变化时，**必须 clean flash（清数据）**。②/①
- 边界外的例外也需清数据：刷机失败 / 反复 bootloop、跨地区版本互刷（国行↔国际版）、换 ROM / 恢复出厂。②

一句话：**「同向、同大版本、跨度小 → 可留数据；反向降级或跨大版本 → 清数据」**。

---

## 5. 官方 / 权威来源清单

### ① 官方（OEM / AOSP / 项目官方）
- OPPO 官方支持（**含一加平板 Pro OPD2404 自助降级**）：「降级操作会清除平板内所有数据……建议提前备份」— <https://support.oppo.com/cn/answer/>（自助降级机型列表含“一加平板Pro”，入口「ColorOS 升级助手→降级工具」）
- OPPO 官方帮助（术语佐证）：「点击『清除数据』或『格式化数据分区』（不同系统版本选项名称可能略有差异）」— <https://support.oppo.com/cn/content-promotion-page/lock-screen-password/>
- 一加官方回滚说明（媒体转述原文 “This rollback will erase all the contents of the device”）— <https://www.bez-kabli.pl/en/oneplus-expands-coloros-17-in-india-rollback-erases-phone-data/>
- AOSP · A/B（无缝）更新（说明 A/B 的「回滚」只针对 OTA 失败回退，非主动降级）— <https://source.android.com/docs/core/ota/ab>
- AOSP · Virtual A/B（动态分区 / super、可回滚机制说明）— <https://source.android.com/devices/tech/ota/virtual_ab>
- AOSP · fastboot 刷机文档（“`-w` wipes the /data partition … otherwise unnecessary”）— <https://android.googlesource.com/platform/docs/source.android.com/+/44436912beb88a9bdc0e45a8dcd054603a2947d9/src/source/flashing.md>
- AOSP · Verified Boot 设备状态（状态转换会擦除数据分区）— <https://source.android.com/docs/security/features/verifiedboot/device-state>
- LineageOS Wiki（升级/降级指引：「想降级就按首次安装流程」；「必须 wipe data 分区，否则新版本无法启动」）— <https://lineageos.github.io/lineage_wiki/devices/troika/upgrade/>

### ② 社区 / 第三方工具 / 媒体
- 紫罗兰工具箱官方教程 violettool.top：「常规线刷适用于系统升级、**同安卓版本降级**或国际版刷写。天玑设备请勿勾选清除数据和自动重启，**刷写完成后需手动格式化数据分区**」— <https://violettool.top/tutorials/oplus-flash/>
- IT之家（转述 OPPO 官方 ColorOS 降级工具）：「降级过程会清空你的手机数据」— <https://m.ithome.com/html/803381.htm>
- ivonblog：LineageOS 同版本降级可 dirty flash，跨版本需 Factory Reset — <https://ivonblog.com/posts/downgrade-lineageos/>
- androidmobiles.org《The FBE Downgrade Trap》：FBE/Keymaster 版本不兼容导致降级不清数据即卡开机（机理文）— <https://androidmobiles.org/the-fbe-downgrade-trap-avoiding-encryption-related-bricks-when-rolling-back-android-versions/>
- V2EX：「data 分区都是加密的，你直接线刷的话，密钥会对不上的吧」— <https://hk.v2ex.com/t/1202979>
- 一加社区：「刷完 ROM 再 data 一下，要不容易解密失败」/「降级需要格式化 data」— <https://bbs.oneplus.com/thread/4042513> 、<https://bbs.oneplus.com/thread/3815952>
- XDA（OOS 移植）：「You must format data after unlock bootloader but before boot … fastboot format -w」— <https://xdaforums.com/t/shared-rom-port-oxygenos-15-for-oneplus-8t-07-11-25.4704221/>
- fastboot man page（`-w` = erase userdata and cache and format if supported）— <https://manpages.ubuntu.com/manpages/xenial/man1/fastboot.1.html>

### ③ 推断（本文自述）
- 「格式化系统分区」＝用户口中误称、实指 userdata；
- `fastboot -w` 不保证清 `/metadata`，recovery「格式化数据分区」更彻底；
- 各清除手段的适用优先级建议。

---

## 6.  重要风险提示：ARB / QFuse「防回滚熔断」是独立于清数据的另一个坑

2025–2026 年 OPLUS 在部分新固件（如 OnePlus 13/13T 的 16.0.3.501、OnePlus 15 的 16.0.3.503 等）引入硬件级 anti-rollback：**一旦升级到含熔断的版本（fuse 由 Index 0→1），再降级到更旧固件会被 bootloader 拒绝，严重时硬砖 / 需官方授权线刷。** 这**与「清不清数据」无关**——清了数据也可能因 ARB 无法降级。Lrom 已有 ARB 解析/硬阻止逻辑（见 README「降级熔断」），降级流程应复用该前置检查，避免「数据清了仍降不下去甚至砖头」。

- 社区/XDA 汇总（**②，含推测成分，需按机型逐一核实**）：<https://xdaforums.com/t/final-warning-permanent-bootloader-lock-incoming-for-oppo-oneplus-realme-devices.4776062/>
- OPLUS 官方确认「临时暂停从 16.0.2.50x 降级到更早版本以增强安全」（媒体转述）：<https://itc.ua/news/vremennoe-reshenye-oneplus-obyasnyla-blokyrovku-otkata-proshyvok/>
- 一加 15 回滚 ColorOS 16 亦明确「清除所有数据」：<https://news.lavx.hu/zh-Hans/article/yi-jia-15-zai-10-yue-13-ri-fa-bu-qian-kai-qi-coloros-17-beta-ban-zhao-mu>

---

## 7. README 修订建议（供 Lead 并入，本研究员原为只读、未直接改 README）

README 现 §「如果是降级，必须先格式化」用的是误称「必须先格式化系统分区」，建议整段替换如下（标题与正文一并纠正，并补时机/三方式/留数据边界/ARB 提示）：

**原文（`README.md` 约 265–276 行）**
```
### 如果是降级，必须先格式化

> **降级**（刷入版本低于原系统）时，**必须先格式化系统分区**，否则会无法开机甚至变砖。

# 方式一：Fastboot 下执行
fastboot -w

# 方式二：进入 Recovery，选择「格式化 data / 恢复出厂设置」
```

**建议替换为**
```
### 如果是降级，必须先清除用户数据（格式化 data 分区）

> **降级**（刷入版本低于原系统）时，**必须在首次开机前清除用户数据（格式化 data / userdata 分区）**，
> 否则旧系统无法解密新系统升级过的 /data（FBE 加密密钥 / Keymaster 版本不兼容），会卡开机或首启异常。
>
>  叫法注意：这里指 **`userdata` 数据分区**，不是 `system`（`super` 内的 system/vendor 逻辑分区由刷包覆盖，
> 不要单独格式化）。欧加 Recovery 里也叫「清除数据 / 格式化数据分区 / 恢复出厂设置」。

# 方式一（推荐，高通 A/B 一般足够）：Fastboot 下执行
fastboot -w            # = 擦 userdata + cache 并重建文件系统

# 方式二（最彻底）：进 Recovery/fastbootd 选「格式化数据分区 / Format Data」
#   —— 会连带清 /metadata；fastboot -w 后仍卡解密/开机时用它

> 时机：**刷完全量包后、第一次开机进系统之前**完成清数据。Lrom 刻意不自动重启，
> 请务必在手动 `fastboot reboot` 前清数据。
> 非降级（同大版本升级）**无需**清数据；`fastboot erase userdata` 单独用不彻底，不推荐。
```

同时建议把 §安全警告 第 4 条「**降级必须格式化**，否则会变砖」改为「**降级必须在首启前清除用户数据（格式化 data 分区）**，否则会无法开机」。

---

## 8. 结论汇总表

| # | 结论 | 口径 |
|---|---|---|
| 1 | 欧加高通 A/B 跨版本降级必须清 userdata，否则旧系统解不开新加密的 /data | ①/② |
| 2 | 「格式化系统分区」是误称，实指 **userdata（数据分区）** | ③ |
| 3 | 通行叫法：格式化数据分区 / 清除数据 / Format Data / 恢复出厂设置 | ①/② |
| 4 | 时机：刷完包后、**首启进系统前**清数据 | ③ |
| 5 | `fastboot -w` 对高通 A/B 一般足够；recovery「格式化数据分区」最彻底（连带 /metadata） | ①/③ |
| 6 | `fastboot erase userdata` 单独用不彻底，不推荐 | ③ |
| 7 | 同大版本/同版本升级可留数据；跨大版本降级必清 | ② |
| 8 | 官方「明说」主要来自 OEM 降级工具声明；AOSP 只有 rollback index / 状态转换擦数据等机制说明，无「降级必清」直接条文 | ① |
| 9 | ARB 熔断是独立风险：清了数据也可能因熔断无法降级/砖头 | ①/② |
