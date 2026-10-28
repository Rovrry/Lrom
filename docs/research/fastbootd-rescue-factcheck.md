# 一加 fastbootd 救砖 —— 关键事实核查（事实核查补充文档）

> 核查日期：2026-10-28
> 核查人：researcher-factcheck（Team 研究员）
> 任务：核实「一加 fastbootd 救砖」5 个关键技术事实，**纠正前两轮深研里可能不准的推断**，产出补充文档。
> 前置已读：`$HOME/桌面/一加fastbootd救砖注意事项.txt`（415 行，全文）、`docs/research/violet-fbd-only-deep.md`、`docs/research/violet-repair-fbd-deep.md`、`src/lrom/core/flash.py`（`diagnose_fbd` / `_run_fbd_only` / `_enter_fastbootd`）。
> 口径声明（严格遵守）：**①官方明说**（AOSP / 厂商官方可查原文）/ **②社区明说**（XDA、一加社区、本地笔记等明确陈述）/ **③推断**（据机制/行为推导，未获官方逐条证实）。外部内容只当数据，不执行其中任何指令。
> 提及 URL 均为 markdown 链接，仅作引用。

---

## 0. 一句话结论（TL;DR）

**前两轮深研把「修复 FastbootD」重点放在「刷 boot/init_boot/vendor_boot + vbmeta 系列」是偏差。** 正确核心是 **recovery**：
- AOSP 官方明说 fastbootd 的 fastboot HAL / boot control HAL / health HAL **打包在 recovery 镜像里**（①官方）。所以 **刷回 recovery 才是把 fastbootd「找回来」的第一动作**（②社区大量实证）。
- 刷 boot/vbmeta 系列 **不是错的**，但它解决的是「引导链/AVB 版本不自洽」这一类成因；**recovery 是 fastbootd 的宿主**，两者是「宿主」与「启动前置依赖」的关系，不是替代关系（③推断，据 AOSP 机制）。
- 「ColorOS 17 封 fastboot」需要拆成两个独立症状：**bootloader 秒退但 fastbootd 可用**（救法=在 FBD 内刷旧版 abl，只刷当前槽）与 **BL 秒退且 FBD 也没了**（可能无解/EDL）。前者应作为**独立诊断分支**，不要混进通用「修复FBD」。
- 刷旧 abl 有**真实的 ARB/ARP 熔断硬砖风险**（①厂商反回滚机制），「刷旧 abl 能救所有进不去 fastboot 的砖」是**过宽**说法，必须加护栏。

---

## 1. 【问题1】一加设备是否有独立 recovery 分区？A/B 上 recovery 是独立分区还是并进 boot？

### 结论
**一加 13 / 13T 等新机型有独立的 `recovery_a` / `recovery_b` 分区**（②社区明说 + ③推断）。这决定 `fastboot flash recovery recovery.img`（或分槽 `flash recovery_a`）在一加上**是可行的**。但**「有独立 recovery 分区」≠「只刷 recovery 就能进 fastbootd」**：一加 13 的 recovery/fastbootd 启动还**依赖可用的 boot（因此也依赖 dtbo）分区**。

### 依据
- **②社区明说（一加 13 分区表全解析）**：一加 13 分区表中 `recovery_a` / `recovery_b` 是独立分区（「TWRP 就刷这里」），并说明一加 13 使用 A/B 无缝更新。来源：[uotan 一加13分区表全解析](https://www.uotan.cn/threads/yi-jia13fen-qu-biao-quan-jie-xi-shua-ji-bi-bei-zhi-nan-kan-dong-na-xie-neng-dong-na-xie-qian-wan-bie-peng-gao-bie-bian-zhuan.39446/)。
- **②社区明说（TWRP 设备树）**：一加 13（PJZ110）、13T 的 TWRP 设备树给出刷写方式 `fastboot flash recovery recovery.img` 或分槽 `fastboot flash recovery_a recovery.img` / `recovery_b`。来源：[WenHao-Dev/twrp_device_oneplus_dodge](https://github.com/WenHao-Dev/twrp_device_oneplus_dodge)、[kmiit/twrp_device_oplus_sm87xx](https://github.com/kmiit/twrp_device_oplus_sm87xx)。
- **②社区明说（关键限制）**：「oneplus 13 has dedicated recovery partitions for recovery. OnePlus 13 relies on a working boot (and therefore dtbo) partition in order to reach recovery and fastbootd.」——即**虽有独立 recovery 分区，仍需 boot/dtbo 可用才能到达 recovery 与 fastbootd**。来源：[XDA - Device will not boot to twrp when boot partition is corrupted](https://xdaforums.com/t/device-will-not-boot-to-twrp-when-boot-partition-is-corrupted.4743414/)。
- **③推断（A/B 形态差异）**：老机型（如部分机型 recovery-as-ramdisk，recovery 并进 boot 的 init_boot/vendor_boot ramdisk）无独立 recovery 分区，`flash recovery` 不适用；新机型（13/13T 等）走独立 recovery 分区。**落地时必须先 `fastboot getvar all` / 读分区表确认该机是否有 `recovery`/`recovery_a`，不能假设。**

### 对 Lrom 的含义
- 「修复FBD 核心=刷 recovery」这条，**只对拥有独立 recovery 分区的机型成立**（13/13T 是；更老机型需另判）。
- 落地前应新增一个只读检查：设备是否存在 `recovery` 或 `recovery_a` 分区；不存在则「刷 recovery」分支不可用，退回「刷 boot/vendor_boot 引导链」或全量刷。

---

## 2. 【问题2】「修复 FastbootD」的正确核心动作是不是刷 recovery.img？与刷 boot/vbmeta 的关系？

### 结论
**是——刷 recovery 是核心、第一动作**（①②）。刷 boot/init_boot/vendor_boot/vbmeta 系列是**补充的前置修复**（针对「引导链/AVB 版本不自洽」成因），二者服务于同一目标（让 fastbootd 用户空间能起来），但有主次：
- **fastbootd 寄生在 recovery 里 → recovery 是宿主**：recovery 坏了/丢了，fastbootd 就没了 → 刷回 recovery 是最直接的「找回 fastbootd」（①官方机制 + ②社区实证）。
- **boot/dtbo 是「到达 recovery 的前置」**：boot 或 dtbo 坏，即使 recovery 分区完好也可能到不了 recovery/fastbootd（②社区）。所以「刷 recovery」与「刷 boot」并非互斥，是链式依赖。
- **vbmeta 系列是 AVB 校验层**：版本不对会「开机秒断电循环」（②），需同版本。

### 依据
- **①官方明说（AOSP）**：「Ensure that the fastboot HAL, boot control HAL, and health HAL are packaged as part of the **recovery** image.」——fastbootd 所需 HAL 打包在 recovery 镜像里。来源：[AOSP – Move fastboot to userspace](https://source.android.google.cn/docs/core/architecture/bootloader/fastbootd?hl=en)。
- **①官方明说（AOSP · fastbootd 启动链）**：bootloader 发现 BCB 里有 `boot-fastboot` 命令 → **加载并启动 recovery 镜像** → 切到 fastbootd 模式。所以「fastbootd 跑在 recovery 镜像里」。来源：同上 + 本地笔记 §一（引 AOSP 原文）。
- **②社区明说（一加 12R 实证）**：case「fastboot 能用但 fastbootd 没了」，最终 Fixed 方式=**手动刷橙狐 recovery 找回 fastbootd**。来源：[XDA – OnePlus 12R fastboot works but fastbootd gone](https://xdaforums.com/t/need-help-bricked-oneplus-12r-2611-na-fastboot-works-but-fastbootd-is-gone.4759324/)。
- **②社区明说（官方散包修复口径）**：紫罗兰「售后包模式接入修复FastbootD功能，可直接使用散包修复 fastbootD」——只挑引导链散包重刷即可，不需整包解 super。来源：[紫罗兰更新日志](https://violettool.top/update.html)。
- **③推断（主次关系）**：官方未逐条列出「先 recovery 后 boot」的命令级顺序；据 AOSP 机制（recovery 是宿主、boot/dtbo 是到达前置、vbmeta 是校验层）推出上述分层，需实机抓日志复核。

### 对前两轮深研的纠正
`violet-repair-fbd-deep.md` §2.1 把 `flash recovery` 列在 boot/init_boot/vendor_boot **之后**，并注「若该机型有独立 recovery」，把 recovery 当**可选项**。**这是偏差**：对一加 13/13T 这类「有独立 recovery 分区」的机型，**recovery 应提到核心/首位**，不是可选。boot/vbmeta 是补充修复，不是替代。

---

## 3. 【问题3】刷旧 abl 救 bootloader：为什么只刷当前槽 abl_a？版本对应？风险（ARB/新abl锁死/熔断）？

### 结论
- **为什么只刷当前槽 `abl_a`**：①**降低风险面**——只动当前活动槽的引导程序，不切槽、不动 B 槽，避免把可回退的 B 槽也一起写坏（③推断 + ②社区纪律）；②一旦失败，另一个槽的 abl 仍可能是**回退/救援的退路**（③推断）。⚠️ 但社区**同时存在「只刷当前槽」与「双槽都刷」两种说法**（见下），说明「只刷当前槽」并非硬性共识，需按症状区分——这一分歧必须在 Lrom 里显式提示。
- **版本对应**（②社区明说，尽量列全已知）：
  - 一加 13（PJZ110）：`abl.img` 取自 **PJZ110_16.0.10.501(CN01)（C.93）** 原厂 ROM。来源：[一加社区 – 一加13 ColorOS 17 Bootloader 秒退修复记录](https://bbs.oneplus.com/thread/2242433687746510851)、[大佬说 – 一加13升级ColorOS17后无法正常进入Bootloader修复方案](https://locdd.com/t/topic/100978)。
  - 一加 13T：**C.58** 的 abl（②本地笔记 + 一加社区帖转述）。
  - 一加 13 部分跨区场景：社区另提到「602-era abl」用于 ColorOS↔OxygenOS 切换后 classic fastboot 失效的场景。来源：[XDA – Help Device not entering Fastboot mode after ColorOS 17 update](https://xdaforums.com/t/help-device-not-entering-fastboot-mode-after-coloros-17-update.4805394/)。
- **风险（必须显式告警）**：
  1. **ARB/ARP 熔断硬砖（①厂商反回滚机制）**：一加在 **ColorOS 16.0.3.501** 起引入 anti-rollback，**降级 OS 或刷入旧版自定义 ROM 会硬砖**，涉及 **一加 13 / 13T / 15**，且**国行 ColorOS 与国际 OxygenOS 都受影响**（厂商称「临时措施」）。来源：[9to5Google – OnePlus update starts bricking phones when you try to install older software](https://9to5google.com/2026/01/20/oneplus-update-starts-bricking-phones-when-you-try-to-install-older-software/)、[9to5Google – anti-rollback only temporary](https://9to5google.com/2026/01/27/oneplus-anti-rollback-measures-only-temporary-downgrades-returning-soon/)、[Android Central](https://www.androidcentral.com/phones/oneplus/oneplus-will-brick-your-phone-if-you-rollback-or-flash-a-custom-rom)。
     - ⚠️ **关键界定（③推断）**：刷「旧 abl.img」与「降级整个 OS/刷旧全量包」**风险等级不同**。ARB 熔断由 **firmware 回滚索引**触发；**单独刷旧 abl 是否踩 ARB 取决于该 abl 是否携带回滚索引、以及当前设备已熔断到第几级**。这是本救法**最大的未知与风险点**，绝不能对用户宣称「刷旧 abl 安全」。
  2. **新 abl 锁死/已刷入不可逆（②社区）**：若升级后新 abl 已刷入且旧 abl 没留，或者机型已熔断，「刷旧 abl」这条不成立（本地笔记 §八/§十二）。XDA 有用户实测「new abl was already flashed so no fastboot for me」= 无救。来源：[XDA – CN Version ColorOS 17 Cannot Unlock Bootloader](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)。
  3. **abl 不通用**：各机型 abl.img 不互通，用错版本=黑砖（本地笔记 §四/§十二）。
  4. **前置条件**：必须**还能进 fastbootd** 才能这样刷（bootloader 已封，靠 FBD 做刷写通道）。

### 「只刷当前槽 vs 双槽」分歧（必须在落地文档里写清）
- **本地笔记 / 一加社区**：明确「**只刷当前槽 abl_a**，不切槽、不动 B 槽」（②，更保守）。
- **XDA（部分回复）**：「dual-slot flash is the standard fix for this exact symptom」「Multiple reports confirm flashing it to both slots restores that entry」（②，另一派）。
- **③推断**：两种做法的适用边界不同——「只刷当前槽」更安全（保留另一槽退路），适合「当前槽可用、只当前槽秒退」；「双槽都刷」适合「两槽都已被新 abl 污染」。**Lrom 不应硬编码只刷 a，而应按「用户确认哪几槽被污染」让用户选，默认最小侵入（只刷当前槽）。**

---

## 4. 【问题4】ColorOS 17 封 fastboot 的确切范围（机型/内测vs正式/CN限定）、判定方法、是否可逆？

### 结论
- **确切范围**：**不止内测，正式版也加了限制**（②XDA 纠正 + ①官方反回滚）。受影响机型至少 **一加 13 / 13T / 15**（①反回滚明确覆盖三款）。**CN 限定 ≠ 绝对**：「无法进 fastboot」的**强封锁**在 **CN 区升级 ColorOS 17** 上最典型（②XDA 明确「users in the CN region cannot enter Fastboot mode after upgrading」）；但**反回滚 ARP 本身「impacts both China-exclusive ColorOS devices and global variants running OxygenOS」**（①9to5Google 引厂商）。二者要分开看（见下「两种症状」）。
- **两种症状必须拆开**：
  1. **bootloader 模式 fastboot 秒退/缺失，但 fastbootd 仍可用**：ColorOS 17 的 abl（如 `.103`）加入「fastboot 机型校验逻辑」，进 bootloader 时图标闪 0.2 秒就直接进系统（②：[大佬说](https://locdd.com/t/topic/100978)）。XDA 明确：「Bootloader-mode fastboot is missing with the 17 abl, but exists with the old abl」「fastbootd works」——来源：[XDA – OnePlus 13 CN ColorOS 17: no bootloader fastboot, fastbootd works](https://xdaforums.com/t/oneplus-13-cn-variant-coloros-17-no-bootloader-fastboot-fastbootd-works-unlocked-rooted-notes.4806467/)。**这是「刷旧 abl」救法的适用场景**。
  2. **bootloader 秒退且 fastbootd 也没了**：三无 → 可能接近无解，只能 EDL（本地笔记 §八-9、案例 E）。
- **判定方法**：
  - `fastboot getvar is-userspace` → `yes`=在 fastbootd（可救）；`no` 且在 BL → bootloader 模式。
  - `fastboot getvar super-partition-name` → `FAILED` + `is-userspace=no` = fastbootd 不可用（②本地笔记 §二）。
  - 观察现象：进 bootloader 图标是否「0.2 秒秒退」→ ColorOS 17 封 fastboot 的典型（②）。
  - 前提：**必须先能在 fastbootd 里拿到信号**；「秒退」是用户**声明**的症状（只读探针无法自动判定「能进但会秒退」，只能问用户 / 依据历史）。
- **是否可逆**：**有条件可逆**——在「17 abl 导致 classic fastboot 缺失、但 fastbootd 可用、且旧 abl 可回退、未熔断」的前提下，刷旧版 abl 可恢复 bootloader fastboot（②多例实证）。**不可逆的情形**：新 abl 已刷入且无旧 abl / 已 ARB 熔断 / fastbootd 也没了（②本地笔记 §八 + XDA 实测）。

### 依据汇总
- **[XDA – CN Version ColorOS 17 Cannot Unlock Bootloader]**：CN 区升级后进不了 fastboot，除非升级前已解锁并单独刷旧 abl；「new abl was already flashed so no fastboot」。链接见上。
- **[XDA – fastbootd works, bootloader-mode fastboot doesn't]**：确认「17 abl 无 BL 模式 fastboot，旧 abl 有；fastbootd 可用」。链接见上。
- **[9to5Google 反回滚]**：ARP 覆盖 13/13T/15，国行+国际。链接见上。
- **[XDA 楼主帖标题]「Do NOT Upgrade System!」**：社区强警告（②）。

---

## 5. 【问题5】对 Lrom 诊断与「修复FBD」落地的直接建议

> 说明：本轮工作**不改源码、不改已有报告**。以下为落地建议，供 Lead / 实现者采纳。当前 `flash.py` 的 `diagnose_fbd` 已出现 `RESCUE_ABL` / `BlSymptom.INSTANT_EXIT` 分支与「核心=刷 recovery」措辞（读码确认），方向与本核查一致。

### 5.1 `repair_fbd` 应以 **recovery 为核心**（纠正前两轮偏差）
1. **动作顺序**：对拥有独立 recovery 分区的机型（13/13T 等），**把「刷回 recovery」作为修复 fastbootd 的第一/核心动作**；boot/init_boot/vendor_boot/vbmeta 系列作为**补充的前置修复**（当证据指向「引导链/AVB 版本不自洽」时）。
2. **前置只读检查**：先判该机是否有 `recovery`/`recovery_a` 分区；无独立 recovery 分区的老机型，`flash recovery` 分支不可用，改走引导链重刷或全量刷。
3. **版本必须匹配**：recovery/boot/vbmeta 必须取自**当前系统版本**全量包（②红线）；无法确认版本 → 不盲目「修复FBD」，回落到全量刷/FB 语义（沿用 `violet-repair-fbd-deep.md` §0 的既有结论，方向对）。
4. **不碰 abl/xbl/ocdt**：修复FBD 不该主动写 bootloader 本身（能进 BL 说明 BL 基本 OK）。

### 5.2 ColorOS 17「秒退」应作**独立诊断分支**（RESCUE_ABL），不要混入通用修复FBD
1. **新增独立推荐值**：`FbdRecommendation.RESCUE_ABL`（秒退 → FBD 内刷旧 abl 救 bootloader），与 `REPAIR_FBD`（BL 活、FBD 死 → 刷 recovery 救 FBD）**方向相反**，绝不能合并。
2. **症状输入必须来自用户声明**：`BlSymptom.INSTANT_EXIT`（能进但秒退）/ `CANNOT_ENTER`（完全进不去）/ `NONE`。只读探针**不可**主动 `reboot bootloader` 去实测秒退（会踢掉唯一救命通道，红线）。
3. **RESCUE_ABL 分支必须带护栏**：
   - 版本对应表（13=C.93/PJZ110_16.0.10.501；13T=C.58 等），版本不对=黑砖；
   - **只刷当前槽 `abl_a`（默认最小侵入），不切槽、不动 B 槽、不刷 ocdt/persist**；双槽是否都刷由用户按「污染范围」决定；
   - **ARB/ARP 熔断风险显式告警**：说明「刷旧 abl 可能触发/加剧回滚熔断，非零风险，仅限『还能进 FBD + 旧 abl 可回退 + 未熔断』」；
   - 前置：设备**必须已在 fastbootd**（is-userspace=yes）才能执行。

### 5.3 诊断分流树（建议，只读、不写盘）
```
is-userspace?
├─ yes (在 FastbootD)
│   ├─ 用户声明 bootloader「秒退」(INSTANT_EXIT) → RESCUE_ABL（FBD 内刷旧 abl，当前槽）
│   ├─ 用户声明「完全进不来 BL」(CANNOT_ENTER + bl_declared_dead) → FBD_ONLY（只刷逻辑分区，禁碰 BL）
│   └─ BL 正常/未声明 → 可走普通/强力线刷；若要修 FBD 环境 → REPAIR_FBD（核心=刷 recovery）
├─ no (在 Bootloader，BL 活) → 若进不了 FBD → REPAIR_FBD（核心=刷 recovery；boot/vbmeta 为补充）
└─ none → UNKNOWN
```
- **红线不变**：诊断全程只读，不发 reboot/flash/erase/set-active，不主动 `reboot bootloader` 探 BL。

### 5.4 需在文档/UI 里显式标注的「存疑点」
- 刷旧 abl **是否踩 ARB** 取决于 firmware 回滚索引与熔断级别（③未决，最大风险未知）。
- 「只刷当前槽」vs「双槽都刷」社区并存，需按症状让用户选。
- ColorOS 17 强封 fastboot 的**精确机型列表/是否严格 CN 限定**，官方未逐机型公布（①只给了反回滚覆盖 13/13T/15）。
- 「ColorOS 17 按 OCDT 识别中国机型来封 fastboot」= XDA 用户理论，**官方未证实**（本地笔记 §十三，保留为 ③）。

---

## 6. 逐条结论可信度索引

| # | 结论 | 类别 | 主要依据 |
|---|---|---|---|
| 1 | fastbootd 的 fastboot HAL / boot control HAL / health HAL 打包在 **recovery 镜像**里 | ①官方 | AOSP Move fastboot to userspace |
| 2 | bootloader 写 `boot-fastboot` 到 BCB → 加载 recovery → 运行 fastbootd | ①官方 | AOSP fastbootd |
| 3 | 一加 13/13T 有独立 `recovery_a`/`recovery_b` 分区 | ②社区 | uotan 分区表、TWRP 设备树 |
| 4 | 一加 13 到达 recovery/fastbootd 仍依赖 boot/dtbo 可用 | ②社区 | XDA TWRP 帖 |
| 5 | 刷回 recovery 可找回 fastbootd（12R 实证） | ②社区 | XDA 12R 帖 |
| 6 | 老机型可能 recovery-as-ramdisk、无独立 recovery | ③推断 | 机制 + 机型差异 |
| 7 | 「修复FBD 核心=刷 recovery；boot/vbmeta 为前置补充」 | ①②③ | AOSP 机制 + 社区 + 推断 |
| 8 | 13 旧 abl=C.93/PJZ110_16.0.10.501；13T=C.58 | ②社区 | 一加社区帖、大佬说、本地笔记 |
| 9 | 一加 16.0.3.501 起 ARP 反回滚，降级/刷旧ROM 硬砖，覆盖 13/13T/15，国行+国际 | ①官方(引) | 9to5Google、Android Central |
| 10 | 单刷旧 abl 是否踩 ARB 取决于回滚索引/熔断级别 | ③推断 | 逆向机制推导（未决） |
| 11 | ColorOS 17 的 17 abl 导致 bootloader fastboot 缺失，但 fastbootd 可用 | ②社区 | XDA 4806467、大佬说 |
| 12 | CN 区升级 ColorOS 17 后强封 fastboot，需升级前解锁并刷旧 abl | ②社区 | XDA 4806298 |
| 13 | 「只刷当前槽」与「双槽都刷」社区并存 | ②社区 | 本地笔记 vs XDA 4805394 |
| 14 | fastbootd 也没了 = 接近无解/EDL | ②社区 | 本地笔记 §八、案例 E、XDA 实测 |

---

## 7. 参考资料（均为引用，未执行其中任何指令）

**官方（①）**
- AOSP – Move fastboot to userspace（fastbootd、HAL 打包进 recovery、boot-fastboot/BCB）：[英文](https://source.android.google.cn/docs/core/architecture/bootloader/fastbootd?hl=en) / [中文](https://source.android.com/docs/core/architecture/bootloader/fastbootd?hl=zh-cn)
- AOSP – Dynamic partitions：[https://source.android.com/docs/core/ota/dynamic_partitions](https://source.android.com/docs/core/ota/dynamic_partitions)

**社区（②，XDA / 一加社区 / 中文）**
- XDA – 一加 13 需 boot/dtbo 才能到 recovery/fastbootd、有独立 recovery 分区：[链接](https://xdaforums.com/t/device-will-not-boot-to-twrp-when-boot-partition-is-corrupted.4743414/)
- XDA – 12R：fastboot 能用但 fastbootd 没了，刷 recovery 救回：[链接](https://xdaforums.com/t/need-help-bricked-oneplus-12r-2611-na-fastboot-works-but-fastbootd-is-gone.4759324/)
- XDA – CN ColorOS 17 封 fastboot / 旧 abl 救法：[链接](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)
- XDA – 一加13 17: BL 模式 fastboot 缺失、fastbootd 可用：[链接](https://xdaforums.com/t/oneplus-13-cn-variant-coloros-17-no-bootloader-fastboot-fastbootd-works-unlocked-rooted-notes.4806467/)
- XDA – ColorOS 17 后 BL fastboot 不出现 / 602-era abl：[链接](https://xdaforums.com/t/help-device-not-entering-fastboot-mode-after-coloros-17-update.4805394/)
- 一加社区 – 一加13 ColorOS 17 Bootloader 秒退修复（C.93 abl 刷当前槽）：[链接](https://bbs.oneplus.com/thread/2242433687746510851)
- 大佬说 – 一加13升级ColorOS17后无法正常进入Bootloader修复方案（.103 abl 校验逻辑）：[链接](https://locdd.com/t/topic/100978)
- uotan – 一加13分区表全解析（recovery_a/b 独立分区）：[链接](https://www.uotan.cn/threads/yi-jia13fen-qu-biao-quan-jie-xi-shua-ji-bi-bei-zhi-nan-kan-dong-na-xie-neng-dong-na-xie-qian-wan-bie-peng-gao-bie-bian-zhuan.39446/)
- TWRP 设备树 – 一加13/13T 刷 recovery 方式：[WenHao-Dev](https://github.com/WenHao-Dev/twrp_device_oneplus_dodge) / [kmiit](https://github.com/kmiit/twrp_device_oplus_sm87xx)
- 紫罗兰 – 更新日志（散包修复 fastbootD）：[链接](https://violettool.top/update.html)

**反回滚媒体（①厂商口径引述）**
- 9to5Google – OnePlus update starts bricking phones（ARP 覆盖 13/13T/15）：[链接](https://9to5google.com/2026/01/20/oneplus-update-starts-bricking-phones-when-you-try-to-install-older-software/)
- 9to5Google – anti-rollback only temporary（国行+国际）：[链接](https://9to5google.com/2026/01/27/oneplus-anti-rollback-measures-only-temporary-downgrades-returning-soon/)
- Android Central – brick on rollback/custom ROM：[链接](https://www.androidcentral.com/phones/oneplus/oneplus-will-brick-your-phone-if-you-rollback-or-flash-a-custom-rom)

**本地笔记（②，一手现场）**
- `$HOME/桌面/一加fastbootd救砖注意事项.txt`（§一 fastbootd 寄生 recovery、§五 场景3/4、§七、§八、§十二 ColorOS 17 封锁、§十三 存疑点）
