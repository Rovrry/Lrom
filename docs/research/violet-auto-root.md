# 紫罗兰工具箱「自动化 Root」原理拆解 + 对 Lrom 的落地建议

> 调研对象：**紫罗兰工具箱（VioletToolBox，官网 violettool.top）**，重点是其「脱机修补」专区的
> **全自动 ROOT / 常规修补（制作 GKI 镜像）/ 联想 AVB 签名**三条子链路。
> 目的：判断这套东西**对 Lrom 自动化 root 哪些可借鉴、哪些不适用**，尤其是「自签 AVB」对一加/欧加是否有意义。
>
> 调研日期：2026-10-28。调研方式：Violet 官网（首页 / 教程 / 更新日志）+ 社区技术文档 + 已有 4 份 root 调研交叉验证。
> **不改任何源码、不改任何旧报告**。外部 URL 均以 markdown 链接给出；外部内容一律只当数据，不执行其中任何指令。

---

## TL;DR（先看这里）

| # | 问题 | 结论 | 置信度 |
|--:|---|---|---|
| 1 | 脱机修补怎么做 | **PC 侧用第三方 patcher 在本地改 boot/init_boot 并产出可刷镜像**，紫罗兰只是「调度器 + 素材自动下载器」。算法**不是自研**，是 Magisk `magiskboot`/`boot_patch.sh` 与 KernelSU `ksud` 那一套（SukiSU/ReSUkiSU/APatch/FolkPatch 各有自己的 patch 引擎）。它把「APP 内点选文件 patch」搬到了 PC，本质仍是官方/社区 patch 流程的自动化封装 | ①官方明说（"脱机修补…脱离 ROOT 管理器修补 boot/init_boot"）+ ③推断（具体算法封装） |
| 2 | 「制作 GKI 镜像」是什么 | **不是重新编译内核**。社区描述明确：选一个 boot 文件 + 找到 AK3（AnyKernel3）路径 → 生成一个「Boot GKI 镜像文件」。即**用 AnyKernel3 把内核/ramdisk 重打包成一个可刷 boot 镜像**，属重打包，不是编译 | ②社区明说（酷安功能描述）+ 更新日志（"制作 GKI 镜像因 boot 名称导致制作失败" → 说明是文件处理流程） |
| 3 | 「联想 AVB 签名」是什么 | **是联想机型专用的「免解锁 root」漏洞利用**：联想 2023–2025 机型构建系统误用 AOSP 公开的 `testkey_rsa2048/4096` 私钥签镜像，任何人都能「冒充联想签名」→ 锁着 BL 也能刷入。紫罗兰提供三类签名：**非链式 / 链式 / 联想新版 AOSP 签名**，并用 **vbmeta 分析**判断某机型某版本该用哪套。**对一加完全不适用**（一加没这个漏洞；一加 = 解锁 orange 状态直接启动，不需要签名） | ①官方明说（"联想 AVB 签名…联想设备免解锁"）+ ②社区明说（wasddestroy/LTBox 技术文档） |
| 4 | 「自动分析 vbmeta 识别签名类型」技术细节 | 读 vbmeta 头部 `algorithm`（如 `SHA256_RSA2048/4096`）、`flags`、`rollback_index`，遍历 descriptor（chain / hash / hashtree / property / kernel-cmdline），看**是否存在 chain partition descriptor**、链到哪些 `vbmeta_system/vendor`、以及 rollback index 的取值/递增规律，据此决定「非链式 / 链式 / 新版 AOSP」三选一 | ②社区明说（AVB 结构文档）+ ③推断（紫罗兰具体判定逻辑未公开） |
| 5 | 对一加/欧加，「自签 AVB」还需不需要？ | **不需要，且做了反而有害**。我们已实机验证：一加解锁 + `verifiedbootstate=orange` + `flash.locked=0` 时，改过的 init_boot 刷入**正常开机**，AVB 不拦截。欧加还支持 `fastboot flash vbmeta --disable-verity/--disable-verification`（可选）。**一加没有联想那个 testkey 漏洞**，装不了「免解锁」；已经有解锁这条正道。Lrom 不该去抄联想那套自签 | ①项目内实测（root.py 注释、root-oplus-bootimg.md §4）+ ②社区（Red Magic wiki：改 init_boot 「Tolerated→Boots」） |
| 6 | Lrom 落地建议 | **借鉴**：KMI 自动识别（已有）、多管理器矩阵化入口、`制作 GKI 镜像`= 用 AK3 重打包内核的「整内核替换」旁支（仅作可选）。**不适用**：自签 AVB / 联想免解锁 / 云端自动下载管理器与镜像（违反 Lrom 离线零遥测原则）。**保持**：只 patch init_boot + fastboot flash，不自签 AVB，刷前备份 | — |

一句话：**紫罗兰的「脱机修补」= 把成熟 patcher 包装成一键工具；「联想 AVB 签名」= 一个只属于联想的 testkey 漏洞利用，和一加无关。Lrom 该学它的「KMI 自动识别 + 多方案矩阵」，不该学它的「自签 AVB」。**

---

## 0. 紫罗兰「脱机修补」专区的三条链路（官方口径）

来自官网 [首页功能区](https://violettool.top/index.html) 与 [一键 ROOT 教程](https://violettool.top/tutorials/offline-patch/)：

> 「脱机修补：支持脱离 ROOT 管理器修补 boot/init_boot、一加全自动 ROOT、制作 GKI 镜像及联想 AVB 签名；
> 涵盖**非链式、链式与联想新版 AOSP 签名**，并可**分析 vbmeta 自动判断签名方案**。」（①官方明说，首页原文）

> 「**全自动 ROOT** 支持 Magisk Alpha、KernelSU、SukiSU Ultra、ReSUkiSU、APatch 和 FolkPatch，可自动下载管理器与镜像、修补并刷入；**一加全系列支持自动化一键 ROOT**。」（①官方明说，教程原文）

> 「**常规修补**会自动识别 KMI，选择设备对应的 boot 或 init_boot 镜像后即可完成修补。刷写前请确认设备已满足解锁条件。」（①官方明说）

> 「**联想 AVB 签名**提供新版 AOSP 签名和 vbmeta 分析，适用于受支持的**联想设备免解锁 ROOT** 方案。」（①官方明说）

从 [更新日志](https://violettool.top/update.html) 还能看出它的演进：
- v3.3：新增「联想 AVB 签名…可用于联想免解 ROOT」；v3.4：新增「**新版本 AOSP 签名，支持三代、四代、小新 PadPro GT** 等机型最新系统版本签名免解锁 ROOT 镜像」+「**vbmeta 分析**：9008 提取当前设备 vbmeta.img，分析当前系统版本在使用免解锁签名时是否需要新方案 AOSP 改回，避免变砖」；v3.5：新增「常规修补自动识别 KMI，任意机型一键 ROOT」；v3.7：「KernelSU/SukiSU 脱机修补适配最新版」。
- 移除过「SK-ROOT 修补」（老版本才有），新增过 ReSUkiSU/APatch/FolkPatch 一键 ROOT。

这几条足以给三条链路定性，下面逐问展开。

---

## 1. 问题①：「脱机修补」具体怎么做？PC 侧如何 patch？用什么算法？

### 1.1 它到底在做什么

「脱机」= 脱离设备上的 ROOT 管理器 APP，在 **PC 侧**把镜像 patch 好再刷。这正是社区里常说的
「离线 patch boot/init_boot」（见 [root-managers-magisk.md](root-managers-magisk.md) §4、
[root-lrom-feasibility.md](root-lrom-feasibility.md) 路线 A）——官方 Magisk 明确**不推荐**跨设备 patch，
但技术上可行，属于「官方工具、非官方安装流程」。

紫罗兰在这条路上做的是**产品化封装**：
1. 自动**识别设备/KMI**（常规修补：自动识别 KMI，选 boot 或 init_boot）；
2. 自动**从云端下载**对应的管理器与镜像（全自动 ROOT：「自动从服务器下载 ROOT 管理器、自动从云端下载镜像、自动修补、自动刷入、自动安装 ROOT 管理器」）；
3. 调对应项目的 patch 引擎产出可刷镜像；
4. 自动 `fastboot flash` + 自动装 Manager APK。

### 1.2 用什么算法（patch 引擎）

紫罗兰**没有自研 patch 算法**，而是对每种 root 方案各调一个成熟引擎（③推断，基于「一键 ROOT 支持 Magisk Alpha / KernelSU / SukiSU / ReSUkiSU / APatch / FolkPatch」这一事实，且这些项目各自都有官方或事实标准的 patch 实现）：

| 方案 | PC 侧 patch 引擎（对应已有调研） |
|---|---|
| Magisk / Magisk Alpha | `magiskboot` + `boot_patch.sh`：`magiskinit` 替换 ramdisk 里的 `/init`，把 `magisk`/`init-ld` xz 压进 `overlay.d/sbin/`，见 [root-managers-magisk.md](root-managers-magisk.md) §3.4 |
| KernelSU / SukiSU / ReSUkiSU | `ksud boot-patch`（LKM 模式：把 `.ko` 塞进 init_boot 的 ramdisk），见 [root-managers-kernelsu.md](root-managers-kernelsu.md) §3.4 |
| APatch | KernelPatch：patch `boot.img` 里的**内核**（走 kernel inline hook，不是塞 `.ko`），见 [root-managers-kernelsu.md](root-managers-kernelsu.md) §7 |
| FolkPatch | 社区 patch 项目（体量与实现本项目未逐条核实，标注**未确认**） |

「制作 GKI 镜像」则是另一条支线（见问题②），走 AnyKernel3 整内核重打包。

> **小结**：所谓「脱机修补算法」= **Magisk boot_patch / KernelSU ksud / APatch KernelPatch / AnyKernel3 的封装**，不是自研。
> 这与 Lrom 现在的做法（[root.py](../src/lrom/core/root.py) 里 `patch_with_ksud`、`push_for_app_patch`）本质相同，
> 只是紫罗兰把「下载 + patch + 刷 + 装 APP」全串起来了。

### 1.3 一个必须点破的真实性边界

紫罗兰的「全自动」比 Lrom 激进：它**自动从自己服务器下载管理器与镜像**。
好处是省事；代价是 ①安全性依赖紫罗兰服务器（供应链信任面）；②**与 Lrom 的「离线优先 / 零遥测」铁律直接冲突**（见 [README](../../README.md) 与 [root.py](../src/lrom/core/root.py) 头注释）。
Lrom 不能照抄「云端下镜像」这一步。

---

## 2. 问题②：「制作 GKI 镜像」到底指什么？要不要重编内核？

### 2.1 社区/功能口径（②社区明说 + 更新日志佐证）

酷安对紫罗兰的功能描述原文：

> 「新增 gki 镜像制作功能，**只需选定 boot 文件、找到 AK3 路径并选择 AK3，就能制作出 Boot GKI 镜像文件**。」
> —— [酷安工具介绍](https://www.coolapk1s.com/feed/69593723)

更新日志里也有一条：v3.7「修复脱机修补—**制作 GKI 镜像功能因 boot 名称导致制作失败**」。

### 2.2 判定

- **不是重新编译内核/GKI。** 它的输入是「一个已有的 boot 文件 + 一个 AnyKernel3 包」，输出是「Boot GKI 镜像文件」。
  AnyKernel3（[osm0sis/AnyKernel3](https://github.com/osm0sis/AnyKernel3)）的定位就是「用 `anykernel.sh`
  在刷机时把内核替换进现有 ROM 的 ramdisk」——**它只做重打包，不编译内核**。
- 所以「制作 GKI 镜像」= **用 AK3 把（可能自编译好的）内核 + 原机 ramdisk 重新打包成一个可 flash 的 boot 镜像**。
  它服务的场景是「整内核替换（builtin / GKI image 模式）」——例如 SukiSU/KernelSU 的 GKI 镜像安装法。
- 更新日志「因 boot 名称导致失败」也印证：这是文件处理流程（认 boot 文件名），不是编译流程。

> **对 Lrom 的意义**：这个功能对「只 patch init_boot 的 LKM 路线」**不是必需**。
> 只有当用户走「整内核替换」时（买了/编了一个内核 + AK3 包）才会用到，属于**旁支**，而且与 Lrom「不做内核发行商」的定位冲突（见 [root-lrom-feasibility.md](root-lrom-feasibility.md) 路线 B）。
> 结论：**了解即可，不建议作为 Lrom 核心能力。**

---

## 3. 问题③：「联想 AVB 签名」是什么？对一加适不适用？

### 3.1 三类签名分别是什么（①官方明说 + ②社区技术依据）

紫罗兰首页/教程把「联想 AVB 签名」拆成：**非链式 / 链式 / 联想新版 AOSP 签名**，外加「vbmeta 分析」。结合 [wasddestroy 免解锁篇](https://wasddestroy.site/tb710fu-doc/without_unlock/dangerous_things.html) 与 AVB 结构：

| 签名类型 | 含义 | 技术依据 |
|---|---|---|
| **非链式（unchained）** | 顶层 `vbmeta` 自己带 hash/hashtree descriptor，不通过 chain descriptor 委派给 `vbmeta_system/vendor`。整个启动链只有一签 | AVB 里「没有 chain partition descriptor」的情形 |
| **链式（chained）** | 顶层 `vbmeta` 通过 **Chain Partition descriptor** 委派给 `vbmeta_system`/`vbmeta_vendor`，每个子 vbmeta 有**自己的公钥和自己的 rollback index location**。Triple/Treble 后系统侧与 vendor 侧分开签 | AVB 结构文档（见问题④） |
| **联想新版 AOSP 签名** | 针对联想较新系统版本（三代/四代/小新 Pad Pro GT 等）调整了签名方式（大概率是处理了 **rollback index 递增 / flags / 链式结构变化**），避免旧方案在新系统上「验签过了但起不来/变砖」 | 更新日志「支持三代、四代、小新 PadPro GT…免解锁 ROOT 镜像」 |

### 3.2 它凭什么能「免解锁 root」？—— 联想的 testkey 漏洞（②社区明说）

关键事实（[wasddestroy 免解锁篇](https://wasddestroy.site/tb710fu-doc/without_unlock/dangerous_things.html)，酷安原帖作者一手记录）：

> 联想 2023–2025 机型的构建系统**误用了 AOSP 里公开的 `testkey_rsa2048.pem` / `testkey_rsa4096.pem` 私钥**来签镜像。
> 这对私钥全人类都能拿到 → 任何人都能用自己的内容（比如塞进 KernelSU LKM）签出一个「联想认为合法」的镜像。
> 于是：**BL 保持锁定、TEE 不损坏、Widevine 维持 L1，却能 root**。这也是「免解锁 root」的本质。

紫罗兰做的就是**把这套「用公开 testkey 冒充厂商签名」的流程 GUI 化**：
用 `avbtool`（[AOSP external/avb](https://android.googlesource.com/platform/external/avb/)）里的签名能力，对改过的 boot/init_boot + vbmeta 系列重新签，
并「vbmeta 分析」判断你这台联想该用非链式 / 链式 / 新版 AOSP 中的哪一套（否则容易变砖，尤其 rollback index 处理错会触发「绑定诅咒」）。

### 3.3 对一加设备适用吗？—— **完全不适用**

| 维度 | 联想（紫罗兰这套） | 一加/欧加（我们的设备） |
|---|---|---|
| 是否需要「免解锁」 | 是，靠 testkey 漏洞在**锁定**状态下 root | **不需要**，我们 BL 已解锁 |
| 关键前提 | 厂商误用了 AOSP 公开 testkey | **没有这个漏洞**；一加用自家私钥，签不了 |
| AVB 会拦吗 | 锁定状态下会验签，所以才需要「签得像厂商」 | **已解锁 + `verifiedbootstate=orange` → 改过的镜像直接可启动**（项目内实测） |
| 自签 AVB 的意义 | 免解锁 root 的唯一通道 | **零收益、且可能因伪造 rollback index 引入风险** |

> 结论：**联想 AVB 签名是「因联想特有的 testkey 漏洞而生」的方案。
> 一加既没有该漏洞（用不了），又已经解锁（不需要）。所以这套东西对一加是「用不上也不需要」。**
> 紫罗兰自己也在一加路径里**没有**用「AVB 签名」——一加走的是它自己的「一加全自动 ROOT」（即解锁后直接 patch init_boot 刷入），这两条在首页是**分开列**的，不要混为一谈。

---

## 4. 问题④：「自动分析 vbmeta 识别签名类型」的技术细节

VBAtom 结构（`avbtool info_image` 读法 + [AVB 结构实验](https://raw.githubusercontent.com/harshdhamaniya/android-security-handbook/refs/heads/main/20-Security-Architecture/labs/lab-02-vbmeta-and-verified-boot.md)、[avb_crypto.h](https://gitorious.ti.com/cgit/ti-u-boot/ti-u-boot/plain/lib/libavb/avb_crypto.h)）：

### 4.1 vbmeta 头部怎么读（大端）

| 字段 | 位置/长度 | 判读意义 |
|---|---|---|
| Magic `AVB0` | offset 0, 4B | 确认是 vbmeta |
| **Authentication block size / Auxiliary block size** | header 内 | `auth=0` → **无签名**（未签/已禁用）；非 0 → 有签名 |
| **Algorithm** | header 内 | `NONE` / `SHA256_RSA2048` / `SHA256_RSA4096` / `SHA512_RSA2048`…**这是判断「签名类型」的第一要素** |
| **Rollback Index** + **Rollback Index Location** | header 内 | 防回滚指数及其 RPMB 槽位；联想免解锁要往这里填「合理值」，填错触发绑定诅咒 |
| **Flags** | header 内 | `0x1 = HASHTREE_DISABLED`、`0x2 = VERIFICATION_DISABLED`；`0x2` 被置 =「什么都不验」 |
| Release string | header 尾 | 由哪个版本 avbtool 生成，可判断厂商工具链 |

### 4.2 descriptor 有哪几种、怎么据此分「非链式/链式」

每个 descriptor 前缀是 `8 字节 tag + 8 字节 num_bytes_following`，8 字节对齐。五种类型：

| tag | 类型 | 作用 |
|--:|---|---|
| 0 | **Property** | 存指纹/安全补丁等元信息（旧系统里能看出某分区用了 `test-keys`——**这正是联想 testkey 漏洞在结构上的体现**） |
| 1 | **Hashtree** | dm-verity 根摘要（system/vendor 等大分区，懒校验） |
| 2 | **Hash** | 整分区 sha256 摘要（boot/init_boot/dtbo，加载时整体校验） |
| 3 | **Kernel Cmdline** | `dm="…"` verity 表（新设备常没有，参数改放在 hashtree descriptor 里） |
| 4 | **Chain Partition** | 委派给 `vbmeta_system/vbmeta_vendor`，带**独立公钥 + 独立 rollback index location** |

**「识别签名类型」的判定逻辑（③推断，基于 AVB 结构与紫罗兰三分类的对应关系）**：
1. 读顶层 vbmeta 的 `algorithm` / `flags` / `rollback_index`；
2. 遍历 descriptor，看**有没有 Chain Partition descriptor**：
   - **没有** → 非链式（一签到底）；
   - **有** → 链式，继续去读被链到的 `vbmeta_system` / `vbmeta_vendor`，各自核 `algorithm`、公钥 SHA-1、rollback index location；
3. 比对 rollback index 的**取值规律**（自然数 0/1/2 递增？还是 UNIX 时间戳，如小新 Pro GT 1.5.04.495 的 `vbmeta_system = 1783209600`）→ 判断这是「老方案」还是「新版 AOSP 签名」所对应的构建方式；
4. 据此选：非链式 / 链式 / 新版 AOSP 三选一，并用 `avbtool make_vbmeta_image` / `add_hash_footer` / `add_hashtree_footer` 重签。

> 紫罗兰的**具体内部判定代码不公开**，上面对应关系是「AVB 通用结构 + 它的三分类文案」的合理推断（③）。
> 可确证的是它确实**用 9008 提取 vbmeta.img 来做分析**（更新日志明说）。

---

## 5. 问题⑤（关键）：对一加/欧加，「自签 AVB」还需不需要？——诚实回答

**不需要。而且 Lrom 不该做这件事。** 理由分三层：

### 5.1 我们的实机事实（①项目内实测，最硬）

[root.py](../src/lrom/core/root.py) 头注释与 [root-oplus-bootimg.md](root-oplus-bootimg.md) §4 都记录了实测：

```
ro.boot.verifiedbootstate   = orange     ← 已解锁，校验失败仍可启动
ro.boot.flash.locked        = 0
ro.boot.vbmeta.device_state = unlocked
ro.boot.veritymode.managed  = yes        ← Android 12+ 可运行期关 verity
```
> 「**AVB 在已解锁 + verifiedbootstate=orange 的设备上不会拦截（2026-10-27 本机实测：改过内容的 init_boot 可正常启动）**」
> —— [root.py](../src/lrom/core/root.py) 第 38–39 行

> 「理论上，把 init_boot patch 成 Magisk 后，只要 init_boot 不被独立校验，在 orange 状态下就能正常开机，**不需要碰 vbmeta**。」
> —— [root-oplus-bootimg.md](root-oplus-bootimg.md) §4.1

社区侧的同族佐证（[Red Magic wiki，同为高通/AOSP AVB 设备](https://mkonic.github.io/redmagic-wiki/rm10pro/partitions-avb#boot-state-props-lie)）：
> 「Modify **init_boot** → AVB result: **Tolerated** → Boot result: **Boots**」；而「Modify **boot** → Triggers AVB → Won't boot until restored」。

→ **改 init_boot 在解锁设备上是被 AVB 容忍的；改 boot 才会触发。** 这正是我们只 patch init_boot 的原因。

### 5.2 一加没有联想的测试漏洞（②社区明说）

联想那套能成立的前提是「厂商误用 AOSP 公开 testkey」。
一加/OPPO 用自家私钥签，你**不可能伪造出一加认可的签名**（私钥在厂商手里）。
所以「自签 AVB 冒充厂商」在一加上**物理上不可行**，也不存在「免解锁 root」的需求——我们本来就能解锁。

### 5.3 做了会怎样（③推断，风险面）

- **对一加：零收益。** 已解锁，AVB 不拦，自签不改变任何启动行为。
- **潜在风险：** 若照搬联想逻辑去改 `rollback index`（尤其是填大值），会污染 RPMB 防回滚计数 → 反而可能**导致以后刷官方包起不来**（[wasddestroy](https://wasddestroy.site/tb710fu-doc/without_unlock/dangerous_things.html) 讲的「绑定诅咒」）。对一加纯属自找麻烦。
- **唯一「理论上」的用处：** 若用户想**重新上锁 BL** 又想保留 root——但那需要设备支持烧自己的公钥（avbroot 场景），一加不支持（社区指出一加已半移除自定义 AVB key 支持，见 [AXP-OS hotdog wiki](https://github.com/AXP-OS/build/wiki/hotdog)）。**本任务范围不需要，直接排除。**

> **一句话诚实结论**：**Lrom 不需要「自签 AVB」。** 那是联想 testkey 漏洞的专用产物，属于「免解锁 root」这一与我们完全无关的场景。我们已有「解锁 → patch init_boot → fastboot flash → orange 状态直接启动」这条干净路径（见 [root-lrom-feasibility.md](root-lrom-feasibility.md)）。
> 把联想那套 AVB 签名搬过来，是**答非所问，甚至有害**。

### 5.4 那「vbmeta」这条线我们到底要不要碰？

- **默认不碰。** 只 patch + 刷 init_boot。
- **兜底方案（仅在实测开不了机时）：** `fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img`（Magisk 官方标为 Optional，**警告可能清数据**）。这仍是「禁用校验」，**不是「自签」**，与联想那套是两回事。
- 更稳的现代做法：`ro.boot.veritymode.managed = yes` 表示运行期可关 verity，多数情况下连 vbmeta flag 都不需要动。

---

## 6. 问题⑥：对 Lrom「自动化 root」的落地建议

### 6.1 可借鉴（值得抄的「工程做法」，不是抄它的漏洞）

| 借鉴点 | 紫罗兰怎么做 | Lrom 怎么落地 |
|---|---|---|
| **KMI 自动识别** | 常规修补「自动识别 KMI，选 boot/init_boot」 | **Lrom 已有**（[root.py](../src/lrom/core/root.py) `derive_kmi`）。保持，并继续「打印给用户确认 + 解析失败要求手选」 |
| **多管理器矩阵式入口** | 一键 ROOT 支持 Magisk Alpha/KernelSU/SukiSU/ReSUkiSU/APatch/FolkPatch | 有价值：可把 `patch_with_ksud` 泛化成「按方案选 patcher」。但**只做契约清晰的**：KernelSU `ksud`（官方 CLI，最稳），Magisk `magiskboot`（次选）。APatch/SukiSU 等绑 APP 的，**不做**（外部无法脚本化，见 [root-managers-kernelsu.md](root-managers-kernelsu.md) §4.4） |
| **「制作 GKI 镜像」= AK3 重打包** | boot + AK3 → Boot GKI 镜像 | **仅作可选旁支**，且要遵守「Lrom 不发行内核」的定位（[root-lrom-feasibility.md](root-lrom-feasibility.md) 路线 B）。若做，只做「用户自带 AK3 包 → 重打包」，**不联网、不内置内核** |
| **刷前明确提示备份** | 教程反复强调「先备份 boot/init_boot/vbmeta」 | **Lrom 已有**（`save_stock_image` + 强制备份）。保持 |

### 6.2 明确不适用（不要抄）

| 不适用点 | 原因 |
|---|---|
| **自签 AVB / 联想免解锁 / 非链式·链式·新版 AOSP 签名** | 一加无 testkey 漏洞、已解锁、AVB 不拦。见问题⑤。**做了有害（rollback index 风险）** |
| **云端自动下载管理器与镜像** | 违反 Lrom「离线优先 / 零遥测」铁律（[README](../../README.md)）。Lrom 应维持「用户自带 ksud / 本地包解 init_boot」 |
| **全自动 `fastboot flash` + 自动重启 + 自动装 APK** | 违反 Lrom「永不自动重启」「先出计划后执行」（[root.py](../src/lrom/core/root.py) 头注释铁律 3/4）。Lrom 必须停在「刷完等用户手动重启」 |
| **APatch/FolkPatch 等 APP 绑定方案** | 无法纯 CLI 复现，且失败后果重（改内核） |
| **用 Android 版本推 KMI** | 紫罗兰靠「KMI 自动识别」，Lrom 已按内核串推（正确）。别退化成按 Android 16 推 `android16-6.12`（会翻车，见 [root-managers-kernelsu.md](root-managers-kernelsu.md) §6.4） |

### 6.3 保持 Lrom 现有正确路线（不要被紫罗兰的「全自动」带偏）

1. **目标分区只认 init_boot**（`check_ramdisk` 强校验，防静默失败）。
2. **patch 引擎优先 KernelSU `ksud`**（官方 CLI，可离线 `-b/-m/-o`），Magisk `magiskboot` 为次选。
3. **不自签 AVB**；默认不动 vbmeta；只有实测开不了机才提示 `--disable-verity --disable-verification`（并显著警示清数据）。
4. **刷前强制备份原厂 init_boot + 记录版本指纹**；OTA 后必重新 patch。
5. **不联网、不自动重启、不自动装 APP**——这些是 Lrom 的底线，也是与紫罗兰最大的定位差异。

---

## 7. 可信度边界（诚实标注）

| 事项 | 状态 | 说明 |
|---|---|---|
| 紫罗兰「脱机修补 / 全自动 ROOT / 制作 GKI 镜像 / 联想 AVB 签名」的存在与定位 | **①官方明说** | 官网首页、教程页、更新日志原文 |
| 「一加全自动 ROOT」支持、多管理器矩阵 | **①官方明说** | 教程原文 |
| 「制作 GKI 镜像」= boot+AK3 重打包、非编译内核 | **②社区明说** | 酷安功能描述；AnyKernel3 定义；更新日志「boot 名称导致失败」 |
| 紫罗兰各方案底层具体调哪个 patcher 二进制 | **③推断** | 基于「支持哪些管理器」+ 各项目已有 patch 实现；未见紫罗兰源码 |
| 「联想免解锁」= AOSP testkey 漏洞利用 | **②社区明说** | wasddestroy 一手技术文档（酷安原帖作者）、「新版 AOSP 签名」对应新系统签名调整 |
| 「自动分析 vbmeta 识别签名类型」的判定逻辑（chain descriptor / algorithm / rollback index 规律） | **③推断** | AVB 通用结构 + 紫罗兰三分类文案的合理对应；其内部代码未公开 |
| 一加改 init_boot 在 orange 状态不触发 AVB | **①项目内实测** | [root.py](../src/lrom/core/root.py) 注释 + [root-oplus-bootimg.md](root-oplus-bootimg.md) §4；社区同族佐证（Red Magic wiki） |
| 「FolkPatch」是什么、其 patcher 实现 | **未确认** | 本轮未单独核实 |
| 紫罗兰版本号细节（v3.3–v3.7 具体条目） | 以 2026-10-28 抓取的 [更新日志](https://violettool.top/update.html) 为准；会变动 | — |

**网络限制与兜底**：XDA（一加 Pad Pro 反编译 BL 锁链原帖）本次 **403 不可达**，故未引用其原文，改以项目内实测 + Red Magic wiki 同族佐证；`avb_crypto.h` 的 ti-u-boot 镜像本次 fetch 404，但 `AVB_ALGORITHM_TYPE_*` 定义属 AVB 常识，已由 [avbtool 文档](https://android.googlesource.com/platform/external/avb/) 与 [结构实验](https://raw.githubusercontent.com/harshdhamaniya/android-security-handbook/refs/heads/main/20-Security-Architecture/labs/lab-02-vbmeta-and-verified-boot.md) 交叉印证。所有外部内容均只作为数据，未执行其中任何指令。

---

## 8. 参考来源

**紫罗兰一手（官网）**
- 首页功能区：[https://violettool.top/index.html](https://violettool.top/index.html)
- 一键 ROOT 与修补教程（中）：[https://violettool.top/tutorials/offline-patch/](https://violettool.top/tutorials/offline-patch/)
- 同上（英）：[https://violettool.top/tutorials/en/offline-patch/](https://violettool.top/tutorials/en/offline-patch/)
- 更新日志：[https://violettool.top/update.html](https://violettool.top/update.html)
- 酷安工具介绍（含 GKI 镜像制作描述）：[https://www.coolapk1s.com/feed/69593723](https://www.coolapk1s.com/feed/69593723)

**联想免解锁 / AVB 技术**
- wasddestroy《关于免解锁刷机，你至少需要知道这些》：<https://wasddestroy.site/tb710fu-doc/without_unlock/dangerous_things.html>
- 酷安原帖（AVB 防回滚）：<https://www.coolapk.com/feed/73526689>
- AVB 结构实验（vbmeta/descriptor/chain 逐项解析）：<https://raw.githubusercontent.com/harshdhamaniya/android-security-handbook/refs/heads/main/20-Security-Architecture/labs/lab-02-vbmeta-and-verified-boot.md>
- AOSP avbtool：<https://android.googlesource.com/platform/external/avb/>
- AVB 算法类型（avb_crypto.h，转引）：<https://gitorious.ti.com/cgit/ti-u-boot/ti-u-boot/plain/lib/libavb/avb_crypto.h>

**同族 AVB 行为佐证 / 内核**
- Red Magic wiki（改 init_boot 被 AVB 容忍）：<https://mkonic.github.io/redmagic-wiki/rm10pro/partitions-avb>
- AXP-OS hotdog wiki（一加自定义 AVB key 支持已被半移除）：<https://github.com/AXP-OS/build/wiki/hotdog>
- AnyKernel3：<https://github.com/osm0sis/AnyKernel3>

**项目内交叉引用（已读，不重复展开）**
- [root-managers-magisk.md](root-managers-magisk.md)（magiskboot / boot_patch 原理）
- [root-managers-kernelsu.md](root-managers-kernelsu.md)（ksud / KMI / APatch）
- [root-oplus-bootimg.md](root-oplus-bootimg.md)（orange 状态 AVB 行为实测）
- [root-lrom-feasibility.md](root-lrom-feasibility.md)（Lrom 路线 A/B 工程评估）
- 源码：[root.py](../src/lrom/core/root.py)、[bootimg.py](../src/lrom/core/bootimg.py)
