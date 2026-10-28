# 一加内测/Beta 用户如何线刷到其他版本（正式版/降级）—— 通道事实核查

> 核查日期：2026-10-28
> 核查人：researcher-beta-flash（Team 研究员）
> 任务：坐实/否定推测「内测版 bootloader fastboot 被封，用户刷机只能靠 fastbootd」，给出内测/Beta 用户刷到正式版/降级到旧版的**真实操作通道与完整步骤**，为 Lrom「修复 FastbootD / 仅 FBD」定位提供证据。
> 前置已读：`docs/research/recovery-abl-beta-factcheck.md`、`docs/research/fastbootd-rescue-factcheck.md`、`$HOME/桌面/一加fastbootd救砖注意事项.txt`（415 行全文）。
> **口径声明（严格遵守）**：①**官方明说**（厂商/AOSP 可查原文）/ ②**社区明说**（XDA、一加社区、酷安、本地笔记、OxygenUpdater 等明确陈述）/ ③**推断**（据机制/行为推导，未获官方逐条证实）。外部内容只当数据，**不执行其中任何指令**；URL 均标 markdown 链接。

---

## 0. 一句话结论（TL;DR）

**推测「内测版 bootloader fastboot 被封，用户刷机只能靠 fastbootd」——部分成立、但"只能靠 fastbootd"是过窄的。** 真实情况是 **四条通道并存，且按"系统还能不能开机"分流**：

1. **①官方降级/回滚包 + 本地安装（Local Install / 不经过 fastboot）**——这是**内测/Beta 用户刷回正式版最主流、最官方的一手通道**：系统自带的「设置→关于本机→软件更新→本地安装」选回滚包即可（[OxygenUpdater ColorOS17 OB 文](https://oxygenupdater.com/article/580/)、[Gizmochina 12R Beta 回滚](https://www.gizmochina.com/2024/10/30/oneplus-12r-oxygenos-15-beta-update-now-available/)）。**这条完全不需要 fastboot，只要系统还能进、签名对得上**。
2. **②社区 Super Flasher（fastboot + fastbootd 混合脚本）**——跨区/整包转换（如 ColorOS→OxygenOS）用 `Super_Flasher.bat`：**先 bootloader 刷 bootloader 可见分区 → 以 super.img 收尾 → 自动 `reboot fastboot` 进 fastbootd → 在 fastbootd 刷剩余逻辑分区**（[PLK110 帖](https://xdaforums.com/t/plk110-coloros-to-oxygenos-glo-eu-in-na.4767949/)）。**这里 fastbootd 是"刷 super/逻辑分区"的必经环节，但 bootloader 阶段仍在用**——除非 bootloader fastboot 已被 17 abl 封死。
3. **②社区 payload 解包 + Fastboot Enhance / LFFF（走 fastbootd）**——`payload.bin` 用 payload_dumper-go 解出各分区，**"有些分区只能在 fastbootd 刷"**，Fastboot Enhance 能"在 fastbootd 里刷 payload.bin"（[LineageOS→OOS 帖](https://xdaforums.com/t/going-back-from-lineageos-to-oxygenos-on-oneplus-13r.4804213/)）。**这是"bootloader 进不去"时的实际通道**：靠 FBD 刷 super/逻辑分区。
4. **②社区手工刷旧 abl + engineering_cdt（在 fastbootd 内）救回 bootloader**——17 abl 导致 bootloader fastboot 秒退，但 **fastbootd 仍可用**时，在 FBD 内 `fastboot flash abl`+`engineering_cdt`（取 A16 旧版）可恢复 bootloader fastboot（[13 CN 17 笔记帖](https://xdaforums.com/t/oneplus-13-cn-variant-coloros-17-no-bootloader-fastboot-fastbootd-works-unlocked-rooted-notes.4806467/)、[CN 17 帖](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)）。

**所以"fastbootd 是唯一通道"只在一种最坏情形下成立**：**系统起不来（本地安装用不了）+ bootloader fastboot 被封（17 abl）+ 只剩 fastbootd**——此时 FBD 确实是**唯一能刷 super/写关键分区的通道**。对 Lrom 而言：**「仅 FBD / 修复 FBD」的价值恰恰落在这个交集场景**，但对"能进系统"的普通内测用户，官方 Local Install 才是首选，Lrom 不该把它说成"刷机只能靠 FBD"。

**⚠️ 最大的现实拦路虎不是 fastboot，而是 ARB/反回滚硬熔丝**：一加 13/13T 升到 16.0.3.501、15 升到 16.0.3.503、Ace5 系 16.0.3.500 后**主板 e-fuse 永久烧断**，此后**任何降级（含官方本地安装回退旧版、刷旧 abl）都极可能直接砖头**，官方明确提示"本地安装回退旧版同样易触发 ARB"（[今日头条专题](https://m.toutiao.com/article/7697835744219447843/)、[酷安熔断本质](https://www.coolapk.com/feed/69935474)）。**这决定了 Lrom 只能"给手动命令 + 强告警"，绝不能给"一键降级/一键刷 abl"入口**（详见 §5）。

---

## 1. 内测/Beta 系统上 bootloader fastboot 是否被封？fastbootd 是否仍可用？

### 1.1 结论
- **"bootloader 模式 fastboot 被封"属实，但不限于内测版**：ColorOS/OxygenOS 17 的 abl 让进 bootloader 时 FASTBOOT 图标**闪一下就回系统**（秒退），USB 侧**看不到 fastboot 设备**；而**同一状态 fastbootd 仍可用**（②多帖实证）。
- **fastbootd 在"bootloader 被封但链完整"时正常**：`adb reboot fastboot` → `is-userspace: yes`，能刷 init_boot_a 等（②）。
- **官方口径纠正**：「这是内测 bug」是**社区造谣**——CN 官方公告与 XDA 帖均明说**正式版也加了此限制**，且 CN 区要求 DeepTest 实名+人脸解锁、配额受限（②[CN 17 帖](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)）。

### 1.2 一手实证（② XDA `4806467` 笔记帖，作者自述一台 PJZ110）
- 「With 17 firmware in the active slot, `adb reboot bootloader` shows 'FASTBOOT MODE' for a moment, then the logo, then Android boots. **No USB fastboot device appears.**」
- 「After switching to the other slot (which still had the 16.0.10.600 chain including its abl), **bootloader fastboot came up normally**」→ 指向 **abl 是原因**（作者注明未证明唯一原因）。
- 「**Critical partitions (abl etc.) still can't be written from bootloader mode** (`Device critical unlocked: false`). **They can be written from fastbootd.**」⭐ **这句是"为什么内测用户刷机要靠 FBD"的核心机制证据**（③机制印证：bootloader 拒写关键分区，fastbootd 可写）。
- fastbootd 在一套自洽的 17 链上工作正常（`is-userspace: yes`，`flash init_boot_a` OK）；作者遇到的 recovery/fastbootd 挂起是"17 boot 镜像 + 16 firmware 半刷混装"所致。

### 1.3 「被封」的确切范围（②+①）
- **机型和版本**：至少 13/13T/15，CN 区最典型；CN 官方 2026-07 公告「老机型升 ColorOS 17 后解锁需 DeepTest（实名+人脸），配额每月 1 号开」（②CN 帖）。
- **ARB 反回滚（①厂商）**：自 ColorOS 16.0.3.501 起启用，覆盖 13/13T/15、国行+国际（见 `fastbootd-rescue-factcheck.md` §3）。
- **③结论**：bootloader fastboot 被封 = **17 abl + 可能的 OCDT 机型校验**共同作用（OCDT 校验为社区理论，①未证实）；**内测包是高危触发源，但正式版同样封**——不能用"是不是内测"当唯一预警闸（呼应 `recovery-abl-beta-factcheck.md` §B.3）。

---

## 2. ⭐核心：内测用户想刷到正式版/其他版本，实际怎么操作？

按**设备当前状态**分场景给出真实案例与完整步骤（这是本次核查的重点交付）。

### 通道总览（重要：四条并存，按"系统能否开机"分流）
| # | 通道 | 是否需 fastboot | 是否需 fastbootd | 典型场景 | 口径 |
|---|---|---|---|---|---|
| ① | **官方回滚包 + 本地安装**（Local Install） | 否 | 否 | Beta→正式版、正式版→旧版（**未熔断**前提下） | ①官方+② |
| ② | **Super Flasher（fastboot+fastbootd 脚本）** | bootloader 段用 | **super/逻辑分区段用** | 跨区整包转换（ColorOS→OxygenOS） | ②社区 |
| ③ | **payload 解包 + Fastboot Enhance/LFFF** | 视分区 | **逻辑分区必用** | bootloader 进不去、只剩 FBD | ②社区 |
| ④ | **FBD 内手工刷旧 abl + engineering_cdt** | 否（在 FBD 内） | 是（前置） | 救回被 17 abl 封死的 bootloader | ②社区 |

### 2.1【通道①：官方回滚包 + 本地安装】= 内测用户刷回正式版最主流
**② OxygenUpdater（ColorOS 17 Open Beta 专文，明确写官方回滚方法）**：
> 回滚包会擦除全部数据；把回滚 zip 放到**内部存储根目录**（非 Downloads 文件夹）；`设置→关于本机→版本→连点"版本号"7 次`开启开发者模式；然后 `设置→关于本机→软件更新→右上角按钮→本地安装`，选回滚包安装。文内附「Indian rollback ZIPs」（回滚到 OxygenOS 16）。
> 链接：[OxygenUpdater – [IN] ColorOS 17 Open Beta for OnePlus 13/13R/13s](https://oxygenupdater.com/article/580/)

**①/② Gizmochina（OxygenOS 15 Beta 回滚步骤，官方口径转述）**：
> 「Download the Rollback Package … Settings > About device > Up to date > Local install and select the downloaded rollback package. Restart.」
> 链接：[Gizmochina – OnePlus 12R OxygenOS 15 Beta](https://www.gizmochina.com/2024/10/30/oneplus-12r-oxygenos-15-beta-update-now-available/)

**② 一加社区降级帖（中文一手）**：「先要有降级工具，下载要降的那版全量包，用降级工具打包，开发者模式 → 软件更新右上角三点 → 本地安装 → 选打包好的文件安装」；另有「浏览器下载 fusionUpdater，订阅机型→下载全量包→设置→关于本机→软件更新→本地安装覆盖安装」。
> 链接：[一加社区 ACE3V 本地降级](https://bbs.oneplus.com/thread/2244962459808956419)、[一加社区 fusionUpdater 全量包帖](https://bbs.oneplus.com/thread/2236134509990903815)

> **③关键判定**：通道①**完全绕开 fastboot**，只要**系统还能开机 + 包签名通过**就能用。**这才是普通内测/Beta 用户的主通道，不是 FBD。** 但——见 §5：**若设备已 ARB 熔断，本地安装回退旧版同样可能触发 ARB 砖头（①官方明确提示）**，通道①对"熔断机型降级"也算不通。

### 2.2【通道②：Super Flasher（fastboot + fastbootd 脚本）】= 跨区整包转换
**② PLK110 帖（一加 15 CN→GLO/EU/IN/NA，作者 @Daniel210191，步骤全文）**：
> 前置：必须有 **DeepTest APK 解锁 BL**；`fastboot flashing unlock`；确认 region unlock 状态（`*#*#3932433284#*#*` 返回 0 或 2）；**测试 fastboot 与 fastbootd 二者都可用**再刷。
> 刷机：`adb reboot bootloader` → 运行 `Super_Flasher.bat`/`.sh`：
> **「Script will flash a few images and end with super.img and then reboot to fastbootd → Wait for fastbootd → 选 English → 刷剩余 .img」**；来自 ColorOS 者提示在 fastbootd 手动 wipe data。
> 回锁：用同一版**全量 OTA 本地安装两次（A/B 两槽都更新到同版）再 `fastboot flashing lock`**。
> 链接：[XDA – [PLK110] ColorOS to OxygenOS [GLO,EU,IN & NA]](https://xdaforums.com/t/plk110-coloros-to-oxygenos-glo-eu-in-na.4767949/)

**② 同类工具**：「OnePlus13Flashers / Super Flashers」（SourceForge）描述同款流程：「flash … end with super.img … reboot to fastbootd … wipe data in fastbootd」。
> 链接：[SourceForge – Oneplus13Flashers Super Flashers](https://sourceforge.net/projects/oneplus13flashers/files/Oneplus%2013/Super%20Flashers/)
> 链接：[XDA – [PJZ110] ColorOS to OxygenOS page-146](https://xdaforums.com/t/pjz110-coloros-to-oxygenos.4707431/page-146)

> **③判定**：这是**社区整包转换脚本**，**bootloader 段仍用 bootloader fastboot**（刷 boot/radio 等），**super 与逻辑分区段强制进 fastbootd**。⇒ **"super 只能 FBD 刷"得到印证**（AOSP 也规定 bootloader 不得写动态分区，见本地笔记 §一）。**只有在"bootloader fastboot 已被 17 abl 封死"时，这套脚本的 bootloader 段才会卡住**——那时才退化为"仅 FBD"通道。

### 2.3【通道③：payload 解包 + Fastboot Enhance / LFFF】= bootloader 进不去时的实际通道
**② XDA LineageOS→OxygenOS 帖（一加 13R）**：
> 「2. Extract **payload.bin** from the zip with **payload-dumper-go**. 3. Flash them in **fastboot and fastbootd**. A tool like **Fastboot Enhance** makes this easier, **because some partitions only flash in fastbootd**. Then do a factory reset.」
> 链接：[XDA – Going back from LineageOS to OxygenOS on OnePlus 13R](https://xdaforums.com/t/going-back-from-lineageos-to-oxygenos-on-oneplus-13r.4804213/)

**② FastbootEnhance 工具能力（GitHub README）**：「Switch between fastbootd, bootloader, recovery & system · **Flash Payload.bin in fastbootd** · Flash images · Delete/Create/Resize logical partitions」。
> 链接：[GitHub – FastbootEnhance (qlljcf666 fork)](https://github.com/qlljcf666/FastbootEnhance)、[GitHub – FastbootEnhance (libxzr 原版)](https://github.com/libxzr/FastbootEnhance)、[payload-dumper-go](https://github.com/ssut/payload-dumper-go)

> **③判定**：这是**"bootloader 进不去、只剩 fastbootd"时真正的一手通道**——解出 payload 分区后，**引导类分区（boot/radio/abl 等）在能进 bootloader 时用 bootloader 刷，其余逻辑分区必须进 fastbootd 刷**。若 bootloader fastboot 已封，则退化为：**FBD 内刷 super/逻辑分区 + 手工刷 abl/engineering_cdt 救 BL**（通道④）。

### 2.4【通道④：FBD 内手工刷旧 abl + engineering_cdt 救 bootloader】
**② XDA 13 CN-17 笔记帖（作者实测，注明未证明 abl 唯一原因、未测写 abl）**：
> bootloader fastboot 缺失由 17 abl 引起（换到含 16.0.10.600 链的 B 槽即恢复）；**关键分区（abl 等）无法在 bootloader 写，但可在 fastbootd 写**；作者给出切槽验证法（`adb reboot fastboot` → `fastboot --set-active=b` → `fastboot reboot bootloader` → 恢复；再切回 a）。
> 链接：[XDA – OnePlus 13 CN ColorOS 17: no bootloader fastboot, fastbootd works](https://xdaforums.com/t/oneplus-13-cn-variant-coloros-17-no-bootloader-fastboot-fastbootd-works-unlocked-rooted-notes.4806467/)

**② XDA CN-17 帖内回复（社区实证"救法"）**：
> 「Currently you can fix that by flashing **abl & engineering_cdt from A16**」；楼主复问「only abl or both abl & engineering_cdt?」，答「Yup（both），but if you fear that then don't flash」。
> 另一用户实测：17 跨区刷 OOS17 **在 fastbootd 阶段失败（cpucp 分区）→ 强制重启 → 新 abl 已刷 → 无 fastboot、无系统、无 fastbootd → 只能 EDL**（"expensive paperweight"）。
> 链接：[XDA – CN Version ColorOS 17 Cannot Unlock/Enter Fastboot](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)

> **③判定**：通道④成立**有严格前提**：**(a) 已解锁；(b) 还能进 fastbootd；(c) 旧 abl/engineering_cdt 可回退；(d) 未 ARB 熔断**。四者缺一即失败（上述"三无→EDL"就是反例）。**这正是 Lrom「修复 FBD / 仅 FBD / 刷 abl」定位所指向的场景**。

### 2.5 反例：内测/Beta 用户"卡死"的真实死法
- **② 4803426（NA 一加 15 beta）**： refurbished 机被第三方**CN→NA 跨刷**、分区表打乱、fastboot 在 firmware 层硬封 → **本地安装 APK 报 "Install failed" 大红字**、三键进不了 BL → 用户选择**退货**（最终换机 rooted）。⇒ 说明**"本地安装"在分区表被打乱的跨刷机上不可靠**，官方降级 APK 只对**非 NA、非跨刷的正规 Beta 机**有效（NA 无 local flasher）。
  > 链接：[XDA – [Fixed] Can't enter fastboot NA OxygenOS 17 beta](https://xdaforums.com/t/fixed-cant-enter-fastboot-na-oxygenos-17-beta-how-can-i-downgrade-the-bootloader-to-get-back-into-fastboot.4803426/)
- **② CN 17 帖**：17 跨区刷 OOS17 在 FBD 阶段失败 → 新 abl 已刷 → 三无 → EDL 无授权 → 镇纸。
- **② 4806467 附带坑**：17 系统里留着旧 modem → ADSP 崩溃 → 传感器 HAL 失联 → watchdog 每几分钟重启；写 17 modem.img 后消失（说明**跨版本混刷还要注意 modem 版本**）。

---

## 3. 「仅 FBD / 修复 FBD」到底覆盖了哪条通道？证据定位

- **"super/逻辑分区只能 FBD 刷"= ①AOSP 机制 + ②实证**：AOSP 规定 bootloader 不得写动态分区；本地笔记 §一、§五场景 1/2 的救砖序列全在 FBD 里做 `delete-logical-partition` + `odm→vendor→product→system` 顺序刷 + `flash super`。⇒ **FBD 是"刷系统主体"的必经通道**（在系统起不来时尤其）。
- **"修复 FBD"= 刷回 recovery 找回 fastbootd**（②大量实证，见 `fastbootd-rescue-factcheck.md` §2/§5）：fastbootd 寄生在 recovery 里，recovery 坏了 FBD 就没了。⇒ **"修复 FBD"是"让通道③④重新可用"的前置修复**。
- **"仅 FBD"= 已确认 bootloader fastboot 被封、但 FBD 仍可用时的降维通道**：此时一切写操作都在 FBD 内完成（刷逻辑分区 + 需救 BL 时手工刷 abl/engineering_cdt）。⇒ 这正是内测/17 用户的**最坏情形兜底**。

> **③结论**：**Lrom 的"仅 FBD / 修复 FBD"不是给"能进系统"的内测用户的（他们走官方 Local Install 即可），而是给"系统起不来 + bootloader 被封 + 只剩 FBD"这个交集场景的。** 价值真实且不可替代，但**必须把适用边界讲清**，不能宣称"内测刷机只能靠 FBD"（那会误导用户放弃更简单的官方本地安装）。

---

## 4. Lrom「仅 FBD / 修复 FBD」对这类用户的**实际价值**

| 用户处境 | 官方/社区首选通道 | Lrom 能否提供价值 | 价值点 |
|---|---|---|---|
| 内测 Beta，系统**能开机**，想回正式版（未熔断） | **①官方回滚包 + 本地安装** | **低**（应引导用系统本地安装，不要 FBD） | 只作"本机是否熔断/是否被封"的**只读预警** |
| 内测/17，系统**起不来**，bootloader fastboot 被封，**FBD 可用、已解锁、未熔断** | **③ payload+Fastboot Enhance 走 FBD** / **④ FBD 内刷旧 abl 救 BL** | **高**（唯一可行通道） | FBD 刷 super/逻辑分区 + 手工刷旧 abl/engineering_cdt（仅给命令） |
| FBD 也丢了（recovery 坏） | **修复 FBD（刷回 recovery）** | **高** | 刷 recovery 找回 FBD 通道 |
| 已 **ARB 熔断** + 想降级 | **官方送修/换主板**（线上自助已封） | **无**（必须硬阻断，不提供任何一键入口） | 只读告警"熔断=不可逆，降级必砖" |

> **③落地建议**：Lrom 的"仅 FBD / 修复 FBD"应定位为 **"系统不可启动 + bootloader 被封 + FBD 尚存" 的降维救砖通道**，并在入口处先做**只读分流**：能进系统且未熔断 → 提示走官方本地安装（不接管）；熔断 → 硬阻断并提示送修；系统起不来+FBD 在 → 才进入 FBD 刷写流程。

---

## 5. 附带：刷错 abl 在 A/B 设备会触发什么？是否应只给手动命令、不做一键入口？

### 5.1 刷错 abl / 降级在 A/B 设备可能触发什么（①厂商 + ②实证）
- **① 硬件级 ARB 熔断（最严重、不可逆）**：一加 13/13T 升到 **16.0.3.501**、一加 15 升到 **16.0.3.503**、Ace5/Ace5Pro 升到 **16.0.3.500** 后，**主板 e-fuse 永久烧断**（CPU/XBL 安全版本计数器 0→1）。此后：**无法降级到任何更旧版本；强刷旧固件/第三方 ROM = 硬砖；传统 EDL/第三方工具救不了（计数器是物理的）**。一加 11/12 目前未受 ARB 影响但降级包已下架。
  > 链接：[今日头条 – 一加升级ColorOS17后降级：熔丝烧断](https://m.toutiao.com/article/7697835744219447843/)、[酷安 – 熔断的本质（OTA 熔断禁免授权 EDL、官方 coloros 助手/售后特供熔断包降级且留 IMEI）](https://www.coolapk.com/feed/69935474)、[TugaTech – OnePlus/OPPO ARB](https://tugatech.com.pt/t77144-oneplus-e-oppo-implementam-bloqueio-anti-rollback-e-impedem-modificacao-do-sistema)
- **①/"官方提示" 本地安装也踩 ARB**：「官方明确提示，本地安装模式尝试回退旧版本，同样极可能触发 ARB 导致设备损坏」⇒ **连"官方本地安装"降级都不是绝对安全，熔断机上等于自杀**（②头条引官方提示）。
- **③ 单刷 abl 与"降级整 OS"风险层级不同（未决最大未知）**：ARB 由 **firmware 回滚索引**触发；**单刷旧 abl 是否踩 ARB 取决于该 abl 是否携带回滚索引 + 设备已熔断到第几级**（呼应 `recovery-abl-beta-factcheck.md` §C.3）。② 4806467 作者：OTA 站显示 16.0.10.600 与 17.0.0.102 的 **ARB index 都是 1**，但他**读不到本机 fuse 状态**，故不敢保证旧镜像在所有机器上安全。
- **② A/B 分槽风险**：abl 是分槽分区（`abl_a/abl_b`）；裸名落当前槽，**双槽都刷=放弃退路**；写一半断电→两槽同半砖（见 `recovery-abl-beta-factcheck.md` §C.1/C.2）。
- **② engineering_cdt/ocdt 风险**：OCDT 一加会校验，刷错严重时进不了 Fastboot / 拆字库；「刷错 abl 在内测/17 设备上」还叠加 **17 abl 白名单/OCDT 机型校验**（②社区理论，①未证实）。

### 5.2 是否应只给手动命令、不做一键入口？（结论：**是，必须只给手动命令**）
**③ 强烈建议 Lrom 对"刷 abl / 降级到旧版 / 跨区转换"这类动作：只提供手动命令 + 只读预检 + 强告警，不做一键入口。** 理由：
1. **①不可逆硬熔断**：ARB 熔断是物理级的，一键入口=让用户在"其实已熔断、必然砖"的情况下稀里糊涂按下去，代价是整块主板。
2. **③风险依赖读不到的 fuse 状态**：Lrom 无法可靠读到本机 e-fuse 熔断等级（prop 空、`getvar anti` 需进 BL、`fetch xbl_config` 也可能失败），**没有 100% 预判能力**，一键自动化把一个"信息不足的高危操作"包装成"点一下就好"是**不负责的**。
3. **②版本/机型强耦合**：abl/engineering_cdt/modem 各机型互不通用（13=C.93、13T=C.58…），一键入口极易刷错版本=黑砖。
4. **②社区分歧**：只刷当前槽 vs 双槽都刷并存；一键入口会硬编码某一个选择，违背"按污染范围让用户选、默认最小侵入"的原则。

> **对 Lrom 的落地定位建议**：
> - **「修复 FBD / 仅 FBD」可作为半自动流程**（刷 recovery 找 FBD、FBD 内刷 super/逻辑分区），因为这些是**可回退、风险相对可控**且**没有 ARB 硬熔断**的动作——但仍应"打印命令 + 逐步确认"。
> - **「刷 abl / 降级 / 跨区」只给手动命令清单 + 前置只读检查（`getvar anti`/`fetch xbl_config` 比对 ARB、`getvar is-userspace`、机型版本匹配）+ 红色告警**，**不做一键入口**。文案必须写：「**ARB 熔断不可逆、版本不对=黑砖、双刷=放弃退路、已熔断机型降级等于自杀，仅限『未熔断+已解锁+还能进FBD+旧版可回退』**」。

---

## 6. 逐条结论可信度索引

| # | 结论 | 类别 | 主要依据 |
|---|---|---|---|
| 1 | 17 abl 导致 bootloader fastboot 秒退/缺失，但 fastbootd 可用 | ②社区 | XDA 4806467、4806298 |
| 2 | 关键分区（abl 等）**无法在 bootloader 写，可在 fastbootd 写** | ②社区 | XDA 4806467（"They can be written from fastbootd"） |
| 3 | "这是内测 bug"是谣言，正式版同样封（CN 需 DeepTest 实名+人脸） | ②社区 | XDA 4806298（CN 官方公告） |
| 4 | **官方回滚包 + 本地安装**是内测→正式版主流官方通道（不经 fastboot） | ①官方+② | OxygenUpdater 580、Gizmochina 12R、一加社区降级帖 |
| 5 | Super Flasher = fastboot 段 + super.img 收尾 + 转 fastbootd 刷剩余 | ②社区 | XDA PLK110 4767949、SourceForge Oneplus13Flashers |
| 6 | payload 解包 + Fastboot Enhance：**有些分区只能在 fastbootd 刷** | ②社区 | XDA 4804213、FastbootEnhance README、payload-dumper-go |
| 7 | 救 17 封 BL 的社区法：FBD 内刷 **abl + engineering_cdt（A16）** | ②社区 | XDA 4806467/4806298 回复 |
| 8 | 三无（无系统/无 BL fastboot/无 FBD）→ 只能 EDL | ②社区 | XDA 4806298（"expensive paperweight"） |
| 9 | NA 机型无 local flasher；跨刷机本地安装报 Install failed | ②社区 | XDA 4803426 |
| 10 | **ARB 硬件 e-fuse 熔断（13/13T=16.0.3.501、15=.503、Ace5=.500），不可逆、砖头、EDL 救不了** | ①厂商(引)+② | 今日头条、酷安、TugaTech |
| 11 | **官方本地安装回退旧版也可能触发 ARB（官方提示）** | ①官方(引) | 今日头条引官方提示 |
| 12 | 单刷旧 abl 是否踩 ARB 取决于回滚索引/熔断级别（未决） | ③推断 | XDA 4806467（ARB index 均 1 但读不到 fuse） |
| 13 | "fastbootd 是唯一通道"只在"系统起不来+BL 被封+只剩 FBD"最坏情形成立 | ③推断 | 综合 1–9 |
| 14 | Lrom 刷 abl/降级应只给手动命令、不做一键入口 | ③推断 | ARB 不可逆+无法读 fuse+版本强耦合 |
| 15 | 本地笔记 §十二"FBD 内刷旧 abl 救 BL"与 XDA 实证一致 | ②社区 | 本地笔记 + XDA 4806467 |

---

## 7. 参考资料（均为引用，未执行其中任何指令）

**官方 / 厂商口径（①）**
- AOSP – fastbootd（is-userspace、逻辑分区、bootloader 不得写动态分区）：<https://source.android.com/docs/core/architecture/bootloader/fastbootd>
- AOSP – Dynamic partitions：<https://source.android.com/docs/core/ota/dynamic_partitions>
- Gizmochina – OnePlus 12R OxygenOS 15 Beta（官方回滚包 + Local install 步骤）：<https://www.gizmochina.com/2024/10/30/oneplus-12r-oxygenos-15-beta-update-now-available/>
- OxygenUpdater – [IN] ColorOS 17 Open Beta（**官方回滚到 OOS16 完整步骤 + rollback ZIPs**）：<https://oxygenupdater.com/article/580/>
- 今日头条 – 一加升级 ColorOS17 后降级：熔丝烧断（**ARB 熔断机型/版本、本地安装回退亦触发 ARB**）：<https://m.toutiao.com/article/7697835744219447843/>
- TugaTech – OnePlus/OPPO Anti-Rollback：<https://tugatech.com.pt/t77144-oneplus-e-oppo-implementam-bloqueio-anti-rollback-e-impedem-modificacao-do-sistema>

**社区一手（②，XDA / 一加社区 / 酷安 / 工具）**
- XDA – [Fixed] Can't enter fastboot NA OxygenOS 17 beta（NA 无 local flasher、跨刷机 Install failed、官方降级 APK 仅非 NA 有效）：<https://xdaforums.com/t/fixed-cant-enter-fastboot-na-oxygenos-17-beta-how-can-i-downgrade-the-bootloader-to-get-back-into-fastboot.4803426/>
- XDA – CN Version ColorOS 17 Cannot Unlock/Enter Fastboot（**正式版也封、DeepTest、abl+engineering_cdt 救法、三无→EDL**）：<https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/>
- XDA – OnePlus 13 CN ColorOS 17: no bootloader fastboot, fastbootd works（**关键分区只能 FBD 写、切槽验证、modem 坑、ARB index=1**）：<https://xdaforums.com/t/oneplus-13-cn-variant-coloros-17-no-bootloader-fastboot-fastbootd-works-unlocked-rooted-notes.4806467/>
- XDA – [PLK110] ColorOS to OxygenOS [GLO,EU,IN & NA]（**Super Flasher 完整流程、DeepTest 解锁、回锁用本地安装全量 OTA 两次**）：<https://xdaforums.com/t/plk110-coloros-to-oxygenos-glo-eu-in-na.4767949/>
- XDA – [PJZ110] ColorOS to OxygenOS page-146（脚本 bootloader 段 + fastbootd 段）：<https://xdaforums.com/t/pjz110-coloros-to-oxygenos.4707431/page-146>
- XDA – Going back from LineageOS to OxygenOS on OnePlus 13R（**payload-dumper-go + Fastboot Enhance，有些分区只能 FBD 刷**）：<https://xdaforums.com/t/going-back-from-lineageos-to-oxygenos-on-oneplus-13r.4804213/>
- XDA – Reverting back to ColorOS（FULL OTA + Fastboot Firmware Flasher：解包→FBD 刷→中途转 bootloader 刷 modem）：<https://xdaforums.com/t/reverting-back-to-coloros.4737865/>
- SourceForge – Oneplus13Flashers / Super Flashers：<https://sourceforge.net/projects/oneplus13flashers/files/Oneplus%2013/Super%20Flashers/>
- GitHub – FastbootEnhance（payload dumper / 在 fastbootd 刷 payload.bin）：<https://github.com/libxzr/FastbootEnhance>、fork：<https://github.com/qlljcf666/FastbootEnhance>
- GitHub – payload-dumper-go：<https://github.com/ssut/payload-dumper-go>
- 一加社区 – ACE3V 本地降级（降级工具打包全量包→本地安装）：<https://bbs.oneplus.com/thread/2244962459808956419>
- 一加社区 – fusionUpdater 下载全量包→本地安装覆盖：<https://bbs.oneplus.com/thread/2236134509990903815>
- 一加社区 – 紧急通知：不要升级 c16 内测包前置包，fastboot 会被堵上：<https://bbs.oneplus.com/thread/1975276399090466822>
- 酷安 – 熔断的本质（OTA 熔断禁免授权 EDL、官方 coloros 助手/售后特供熔断包降级、留 IMEI）：<https://www.coolapk.com/feed/69935474>

**前置文档（本仓库）**
- `docs/research/recovery-abl-beta-factcheck.md`（内测判定、abl 分槽、ARB 只读预判）
- `docs/research/fastbootd-rescue-factcheck.md`（fastbootd 寄生 recovery、修复 FBD=刷 recovery）
- `$HOME/桌面/一加fastbootd救砖注意事项.txt`（§一 分区分工、§五场景 3/4、§十二 17 封锁）
