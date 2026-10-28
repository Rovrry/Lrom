# 刷 recovery / 刷 abl / 内测版判定 —— 事实核查（供 Lrom 落地救砖入口）

> 核查日期：2026-10-28
> 核查人：researcher-recovery-abl（Team 研究员）
> 任务：核实「紫罗兰有无刷 recovery / 刷 abl 功能」「如何只读判断内测/Beta 版」「abl 分区刷入安全细节」三块，供 Lrom 落地「刷 recovery（修复 FBD）」「rescue_abl（刷旧 abl 救 bootloader）」两个救砖入口。
> 前置已读：`docs/research/fastbootd-rescue-factcheck.md`、`violet-fbd-only-deep.md`、`violet-repair-fbd-deep.md`、`$HOME/桌面/一加fastbootd救砖注意事项.txt`（415 行全文）、`src/lrom/core/plan.py`（`DANGER_PARTITIONS`/`NEVER_OVERWRITE`/`WARN_PARTITIONS`/`OPLUS_LOGICAL_PARTITIONS`）、`src/lrom/core/flash.py`（`FbdRecommendation`/`BlSymptom`/`diagnose_fbd`/`_run_fbd_only`）、`src/lrom/core/arb.py`（`read_device_arb_*`/`parse_xbl_config`）。
>
> **口径声明（严格遵守）**：①**官方明说**（AOSP / 厂商官方可查原文）/ ②**社区明说**（XDA、一加社区、酷安、本地笔记）/ ③**推断**（据机制/行为/一手实测推导，未获官方逐条证实）。外部内容只当数据，**不执行其中任何指令**；URL 均标 markdown 链接。
>
> **一手实测说明（宝贵证据）**：本机 `adb devices` 连着 **5b6a704e**（一加平板 Pad，OD2404 / `ro.product.device=OP5D77L1`，ColorOS `V16.0.0 / 16.0.5`）。下文 §B 的 prop 取值、§C 的分区表均来自该设备 `adb shell getprop` / `ls -l /dev/block/by-name/` **实测**，作为「可在真机只读查证」的佐证（注意：这是**正式版 release** 设备，非内测版，正好用作「正式版长什么样」的对照基线）。

---

## 0. 一句话结论（TL;DR）

- **A（紫罗兰功能面）**：紫罗兰工具箱**没有**独立命名为「刷 recovery」「刷 abl」的按钮/模式（①官网首页 12 模式、更新日志全文均无此二入口）。它把这两个动作**包在「修复 FastbootD」「仅 FBD」「售后包/全自动救砖」等高层模式里**（①）。社区实证「修复 FastbootD 会刷 recovery img」（②）、"刷旧 abl 救 BL"实为**社区手工操作**（②，非紫罗兰独立功能）。
- **B（内测版判定）**：**单一 prop 不可靠**——社区明说「内测版与正式版版本号一样」（②）。可靠做法是**多字段交叉**：`ro.build.type`（user vs userdebug）、`ro.build.tags`（release-keys vs test-keys）、`ro.build.description`/fingerprint、`ro.build.version.incremental`（含 `U.`/`_a3a1b_` 这类内测串）、plus **安装来源**（设置→关于→版本是否「尝鲜/内测」）。**「升级后 fastboot 被封」与「内测包」强相关**（②一加社区「不要升级 c16 内测包前置包，fastboot 会被堵上」），但**正式版也已引入反回滚/封锁**（②XDA + ①反回滚），不能只靠"是否内测"预警。Lrom **可只读**（adb getprop，免 root）读取并告警，建议多 prop 组合判据（见 §B.4）。
- **C（abl 刷入安全）**：A/B 设备上 `abl` 是**分槽分区**（实测 `abl_a`/`abl_b` 各在 sde10/sde45），**裸名 `fastboot flash abl` 会落到「当前活动槽」**（③推断 + ②大佬说「abl_a 是卡槽位，b 槽要写 abl_b」）。「只刷当前槽」= `fastboot flash abl_<current_slot>`（②）。双槽都刷有第二派做法（②XDA），风险=把回退退路也污染。刷旧 abl 的 **ARB 熔断能否只读预判**：`fastboot getvar anti` 读回滚索引（②），或不支持时 `fetch xbl_config` 解析（Lrom `arb.py` 已实现），**只能判断"会不会因降级踩 ARB"，不能 100% 保证硬砖不发生**（③）。

---

## A. 紫罗兰有没有「刷 recovery」「刷 abl」独立功能？

### A.1 结论
**没有独立命名这两个功能。** ①官网 12 模式（常规/强力/仅FBD/修复FBD/AB通刷/售后包 FBD·FB·全自动/修复Super真死/EDL 9008/降级助手…）**通篇无「刷 recovery」「刷 abl」按钮**；更新日志（v1.0.1→v3.7 全文）也无此独立入口（①）。它们是被**更高层模式吸收**的动作：
- **「刷 recovery」** 被「**修复 FastbootD**」/「常规镜像刷入-快捷选择 recovery」吸收。**②社区实证**：用户「按了工具箱里面的『修复fastbootd』，看着他刷了几个 img，**包括 recovery**」。→ fastbootd 寄生在 recovery，修 FBD 自然刷 rec。
- **「刷 abl」** 紫罗兰**没有为此开专门模式**；"FBD 内刷旧 abl 救 bootloader"是**社区手工 fastboot 操作**（②大佬说/一加社区帖），不是紫罗兰的一键功能（③据功能面推定；其内部 payload 不可反编译，见 `violet-vs-lrom-flash.md` §0）。
- 有**相关但不同**的入口：①「常规镜像刷入」提供常用镜像名快捷选择（含 `recovery`、`vendor_boot`）；①更新日志有「修复Super真死」「ARB熔断检测」「OCDT分析」；①「售后包模式/全自动救砖」会整包刷（自然含 recovery/abl 扇区）。

### A.2 入口 / 模式 / 校验（①官方文案 + ②社区）
| 动作 | 紫罗兰承载入口 | 来源 | 有无机型/版本校验 |
|---|---|---|---|
| 刷 recovery（修 FBD） | 「修复 FastbootD」（售后包模式内接入）＋「常规镜像刷入」快捷选 `recovery` | ①[更新日志](https://violettool.top/update.html)「售后包模式接入修复FastbootD功能，可直接使用散包修复fastbootD」；①[基础刷入教程](https://violettool.top/tutorials/basic-flash.html)「常规刷入提供常用镜像名称快捷选择」 | ①「分区表校验」比对包与设备分区表（防刷错包）＋①「ARB熔断检测」「自动识别安全补丁/熔断版本提示」 |
| 刷 abl（救 BL） | **无独立入口**（社区手工做） | ②[大佬说](https://locdd.com/t/topic/100978)；②[一加社区](https://bbs.oneplus.com/thread/2242433687746510851) | 手工操作无工具校验；用户自负（型号/版本自辨） |
| （相关）整包含 abl/recovery | 售后包 FB 模式 / 全自动救砖 / 强力线刷 | ①[首页](https://violettool.top/index.html)、[更新日志](https://violettool.top/update.html) | ①分区表校验＋ARB 熔断检测 |

> 结论口径：**「独立刷 recovery / 独立刷 abl 功能」= 不存在（①官方功能面可证）**；**「修 FBD 时工具会刷 recovery」= ②社区实证**；**「刷旧 abl」= ②社区手工，非紫罗兰功能（③）**。

### A.3 对 Lrom 的含义
- Lrom 做「刷 recovery」「rescue_abl 刷旧 abl」两个**独立入口是合理的、甚至比紫罗兰更细**——紫罗兰把它们藏在高层模式里，用户反而不好精确控制"只刷当前槽 abl"。
- Lrom 应向紫罗兰**学习其校验气质**（①分区表校验/ARB 熔断检测），移植到两个新入口的前置只读检查里，而不是照搬其并列按钮交互。

---

## B. 如何只读判断当前系统是不是「内测/Beta 版」

### B.1 结论
**没有单一可靠 prop。** ②社区明说「尝鲜/内测 **跟正式版版本号一样**」（[一加社区 colors17 帖](https://bbs.oneplus.com/thread/2243781780345716736)：「跟正式版版本号一样」），所以**不能只看版本号**。可靠做法是**多字段交叉 + 安装来源**：`ro.build.type`、`ro.build.tags`、`ro.build.description`/fingerprint、`ro.build.version.incremental` 形态、以及系统设置里的"尝鲜/内测"标识。**一加/ColorOS 的内测包一般是 `user`+`release-keys` 签名（非 userdebug/test-keys）**，所以**"userdebug 才叫内测"是错的**——这条必须纠正。

### B.2 实测对照表（一手证据：本机 Ohd2404 正式版 = 基线；内测版典型值 = ②社区/③推断）
| Prop | 本机实测（**正式版基线**，OPD2404 ColorOS16） | 内测/Beta 典型差异 | 判读 |
|---|---|---|---|
| `ro.build.type` | `user` | 多为 `user`（**内测也用 user**，不是 userdebug） | ⚠️ **不能单凭此判内测**；`userdebug/eng` = 更"开"的调试版（②AOSP 定义） |
| `ro.build.tags` | `release-keys` | 多为 `release-keys`（内测仍用发布签名）；`test-keys` = 重签/第三方/极少数测试 | ⚠️ `test-keys` 是强信号，但**内测往往也是 release-keys** |
| `ro.build.description` | `qssi-user 16 BP2A.250605.015 1786633575764 release-keys` | 内测描述串可能带 `beta`/`preview`/内部代号（未获官方逐条证实，③） | 中 |
| `ro.build.fingerprint` | `OnePlus/OPD2404/OP5D77L1:16/UKQ1.231108.001/U.247d78a_a3a1b_629cb:user/release-keys` | 版本段（incremental）形态可能不同；`userdebug` 会直接暴露 | 中 |
| `ro.build.version.incremental` | `U.247d78a_a3a1b_629cb` | 内测/灰度可能带不同后缀串（③，社区称版本号可与正式版相同） | 弱-中 |
| `ro.build.version.oplusrom` | `V16.0.0` | 内测可能显示同一大版本（②「跟正式版版本号一样」） | ⚠️ 弱 |
| `ro.build.display.id` | `OPD2404_16.0.5.1201(CN01)` | 内测 display id 可能不同（需样本） | 弱 |
| `ro.system/vendor/product.build.type` | 均 `user` | 内测仍多 `user` | 弱 |
| `ro.system/vendor/product.build.tags` | 均 `release-keys` | 同 `ro.build.tags` | 弱 |
| `ro.debuggable` | `0` | 内测若为 userdebug 则 `1`（一般仍 0） | 弱 |
| `ro.build.version.security_patch` | `2026-08-01` | 内测通常补丁更新（但正式版也会更新） | 弱 |
| `ro.vendor.oplus.regionmark` | `CN` | 区域无关内测，但**判断国行/国际**有用（本机 CN） | 辅助 |
| `ro.build.characteristics` | `nosdcard,tablet` | 平板标识，无关内测 | — |
| `ro.boot.verifiedbootstate` | `orange` | 解锁态；正式锁机应 `green` | 与内测无关（判断是否解锁） |
| `ro.boot.flash.locked` | `0` | 0=已解锁 | 与内测无关 |
| `ro.boot.vbmeta.device_state` | `unlocked` | 同上 | 与内测无关 |

> **结论（③）**：想靠 prop **唯一、确定**地判「内测版」**做不到**（②版本号相同的社区实证）。可靠的综合判据应为：
> 1. **系统设置 → 关于手机 → 版本号/系统更新渠道**是否显示「尝鲜/内测/Beta」（②用户可见，最直接）。
> 2. prop 侧只作**辅助告警**：`ro.build.type`/`ro.build.tags` 若为 `userdebug`/`eng`/`test-keys` → **强提示"非正式发行版"**；若均为 `user`+`release-keys` → **不能排除内测**，需结合来源。

### B.3 「升级后 fastboot 被封」是否只内测版？正式版是否也封？
- **与内测包强相关（②明说）**：一加社区「［紧急通知］致想升级 c16 的 root 用户」原文「**不要升级 c16 内测包的前置包，fastboot 会被堵上**，升级只会让手机变成砖头」——直接点名**内测包会堵 fastboot**（[链接](https://bbs.oneplus.com/thread/1975276399090466822)）。
- **但正式版也加了限制（②XDA + ①反回滚）**：
  - ②XDA 明确「Restrictions have appeared on the 13/13T… other models will also be unable to unlock after the update」（[XDA 4806298](https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/)）；「new abl was already flashed so no fastboot」。
  - ①厂商反回滚（ARP）覆盖 13/13T/15、国行+国际（9to5Google / Android Central，见 `fastbootd-rescue-factcheck.md` §3）。
  - ColorOS 17 的 `.103` abl 引入 fastboot 机型白名单校验（②大佬说：旧 C.91 白名单含其项目号 23821，`.103` 不含 → 进 BL 0.2 秒就重启）。
- **③结论**：**"fastboot 被封"不是内测版专属**——内测包是**高危触发源**（②强证据），**正式版 OTA 也可能封（至少 13/13T 已出现，①反回滚 + ②XDA）**。**预警逻辑：既要提示"内测/尝鲜渠道风险更高"，也要提示"正式版升级后同样可能封，刷前先备份、别盲升级"**，不能只拿"是否内测"当唯一闸。

### B.4 给 Lrom 的只读告警建议（查哪些 prop）
> 前提：**全部 adb 免 root 可读**（本机实测这些 prop 在 `adb shell getprop` 全部可读）。Lrom 只读、不写设备，符合诊断红线。

**建议判据（③，可直接落地为 `diagnose_beta_channel()`）**：
```text
# 强信号（命中即"非正式发行/调试版"高危提示）
ro.build.type ∈ {userdebug, eng}            → 高危：调试版
ro.debuggable == 1                           → 高危：可调试
ro.build.tags / ro.system.build.tags / ro.vendor.build.tags == test-keys → 高危：非发布签名

# 中信号（组合提示"疑似尝鲜/内测，fastboot 封锁风险上升"）
ro.build.type == user && tags == release-keys 但 用户设置里渠道=尝鲜/内测  → 提示（需问用户）

# 弱信号（仅记录/展示，不作硬判）
ro.build.version.incremental / ro.build.version.oplusrom / ro.build.display.id
ro.build.version.security_patch（补丁日期过新 → 提示"可能是跨版本/新版 abl，留意 fastboot 封锁"）
ro.build.description / ro.build.fingerprint

# 区域/机型辅助（非内测判据，但用于匹配 abl 版本）
ro.vendor.oplus.regionmark (CN/…), ro.boot.prjname, ro.product.device/product
```
**告警文案建议**：
- 命中强信号 → 「检测到**非正式发行版**（`ro.build.type=…`/`tags=…`），该渠道已知会封锁 fastboot（②一加社区），**刷机/升级前务必备份，切勿盲刷旧版本**」。
- 弱信号 + 用户声明尝鲜 → 「**尝鲜/内测渠道**：正式/内测版本号可能相同（②），**升级后 fastboot 可能被封**（含 13/13T 正式版亦已出现，②XDA）」。
- 全 formal（user+release-keys）→ 「正式发行版签名；**但仍不能排除升级后 fastboot 被反回滚/abl 校验封锁（①）**，刷前请先备份并用 `getvar anti`/`fetch xbl_config` 判熔断」。

> **纠正前文潜在误读**：不要写「`ro.build.type=userdebug` 才是内测」——**一加内测包通常仍是 `user`+`release-keys`**（②版本号相同的实证），userdebug 只是"更开"的调试版，不代表"内测"。内测判定必须**渠道（设置里）+ 多 prop 组合**，prop 只是辅助告警。

---

## C. abl 分区刷入安全细节

### C.1 A/B 设备裸名 `fastboot flash abl` 会落到当前槽吗？「只刷当前槽」命令？
- **实测分区布局（一手证据，OPD2404）**：`ls -l /dev/block/by-name/` 显示 **`abl_a -> sde10`、`abl_b -> sde45` 是两个独立分槽分区**；同理 `recovery_a/recovery_b`、`xbl_config_a/xbl_config_b` 均分槽；当前槽 `ro.boot.slot_suffix=_b`。①AOSP A/B 语义：`getvar has-slot:abl` 返回 yes。
- **裸名落到当前槽（③推断 + ②印证）**：Android fastboot 对 A/B 分槽分区，**裸名 `flash abl` 会自动补当前活动槽后缀 → 等价 `flash abl_<current-slot>`**（AOSP slot 机制）。②[大佬说](https://locdd.com/t/topic/100978) 原话：「**`abl_a` 是卡槽位，如果是 b 卡槽，要替换成 `abl_b`**，不知道的话用命令/柚坛工具箱查看」——即**明确分槽，且推荐显式写槽名**。⚠️ 裸名虽通常落当前槽，但**显式写 `abl_<slot>` 更安全、无歧义**（③，避免对不透明 bootloader 行为赌运气）。
- **「只刷当前槽」的稳妥命令序列（②+③）**：
  ```text
  fastboot getvar current-slot          # 先读，假设返回 b（或 a）
  # 确认 abl 确为分槽：fastboot getvar has-slot:abl  → 期望 yes
  fastboot flash abl_b abl.img          # 显式分槽（把 b 换成实测 current-slot）
  # 不要 --set-active、不要动 abl_a、不碰 ocdt/persist
  ```
  ②一加社区修复帖流程：「只刷 abl_a，不改 B 槽、不切槽」（[bbs](https://bbs.oneplus.com/thread/2242433687746510851)）。

### C.2 双槽都刷的风险 & 社区分歧
- **两派并存（②）**：
  - **保守派（本地笔记/一加社区）**：只刷当前槽，保留另一槽作为**退路**（③推断：另一槽 abl 未被新 abl 污染，万一当前槽写坏还有回退空间）。
  - **双刷派（XDA）**：「flash ABL to both slots: `fastboot flash abl_a abl-16.0.6.img` + `fastboot flash abl_b abl-16.0.6.img`」（[XDA PMA110 convert CN→Global page-11](https://xdaforums.com/t/rom-pma110-pma120-convert-cn-to-global.4799566/page-11#post-90744740)），适用"两槽都被新 abl 污染"。
- **风险（③）**：双刷=**把唯一未污染的退路也写掉**；若镜像版本与其 ARB 不匹配或写一半断电，**两槽同时半砖→进不了任何模式**。所以：**默认最小侵入"只刷当前槽"，双刷必须用户明确"两槽都已被新 abl 污染"后显式勾选**（与 `fastbootd-rescue-factcheck.md` §3「Lrom 不应硬编码只刷 a，按污染范围让用户选」一致）。Lrom 当前 `diagnose_fbd` 的 RESCUE_ABL 分支已写「只刷当前槽 abl_a，不切槽/不动 B 槽」（`flash.py:230`），方向正确。

### C.3 刷旧 abl 的 ARB / 反回滚风险，能否只读预判？
- **反回滚机制（①厂商）**：一加自 ColorOS 16.0.3.501 起引入 anti-rollback（ARP/ARB）：刷入**回滚索引（rollback index）低于**设备已熔断等级 → **硬砖**，覆盖 13/13T/15、国行+国际（9to5Google、Android Central，见 factcheck §3）。AOSP 机制（①）：设备一旦启动过 rollback index=N 的镜像，bootloader **永久拒绝**更低 index 的镜像。
- **只读预判手段（②/③ + Lrom 已实现）**：
  1. **`fastboot getvar anti`（②）**：在 bootloader 下执行，返回数字=当前 rollback index；**空白=未启用 ARB**（[Anarchy-Droid 讨论](https://github.com/amo13/Anarchy-Droid/discussions/24)、[xiaomi.eu](https://xiaomi.eu/community/threads/what-about-arb-protection.76254/)）。⚠️ 本机为平板、当前在 adb 非 fastboot，`fastboot getvar anti` 实测 `< waiting for any device >`（无法在本机取到，需进 fastboot 才能读）。
  2. **`fetch xbl_config` 解析（③，Lrom `arb.py` 已实现）**：ARB 字段在 `xbl_config` 的 OEM Metadata。`read_device_arb_fastboot()` 用 `fastboot fetch xbl_config` 回读并 `parse_xbl_config()` 解析 `arb_index`；`read_device_arb_adb()` 走 root 读 `xbl_config` 或 `ro.boot.arb` 类属性（**本机实测 `ro.boot.arb`/`ro.oplus.security.arb`/`ro.vendor.oplus.arb` 均为空**，故 prop 法在多数一加不生效，只能靠 fetch/root 回读）。`compare_arb()` 比对包 vs 设备，判断降级是否越 ARB。
  3. **工具侧参考**：②社区有 "OnePlus ARB Checker"（[XDA 工具帖](https://xdaforums.com/t/tool-guide-oneplus-anti-rollback-arb-checker-bot-web-tracker.4778602/)、[fossdroid](https://fossdroid.com/a/oneplus-arb-checker.html)）可查设备/包 ARB index。
- **能否 100% 预判硬砖（③）**：**不能**。`getvar anti`/`fetch xbl_config` 只能读**当前已熔断等级**与**包的 ARB 值**，回答的是"这个旧 abl 的回滚索引会不会低于设备已熔断等级 → 是否会**因降级**踩 ARB"。但：
  - **单刷 `abl` 与"降级整个 OS"风险层级不同**——`abl` 是否携带回滚索引、该索引是否参与熔断，各机型/版本未必一致（③）；**存在"读到的 ARB 都匹配，但单独刷旧 abl 仍触发/加剧熔断"的可能**（这是最大未决风险，见 factcheck §3）。
  - 结论：**ARB 只读预判是"必要不充分"护栏**——能筛掉"明显会因反回滚硬砖"的降级，不能保证安全。落地时必须**显式告知"非零风险、无 100% 保证"**，并要求"已备份、还能进 FBD、旧 abl 可回退"。

### C.4 对 Lrom「rescue_abl」入口的具体建议
1. **动作**：`fastboot getvar current-slot` → `fastboot flash abl_<slot> <旧版本abl.img>`（显式分槽，默认只当前槽）。**禁止** `--set-active`、`flash abl_b`（除非用户勾"双槽都污染"）、碰 `ocdt/persist/xbl`。
2. **前置只读（全部只读，不写不重启）**：
   - `getvar is-userspace == yes`（必须在 FBD 才能安全刷 abl，且这是救命通道）；
   - `getvar product/product_name` 与 abl 来源机型匹配（各机型 abl 不通用，②）；
   - `getvar anti`（或 `fetch xbl_config`+`parse_xbl_config`）读 ARB，与目标旧 abl 的 ARB 比对；不匹配 → **硬阻断**；
   - 展示当前 `ro.build.type/tags/oplusrom/incremental` 供用户确认版本方向。
3. **人机确认**：刷 abl = `DANGER`（`plan.py` 已归类 DANGER），需二次确认；文案明写「**ARB/熔断非零风险、版本不对=黑砖、双刷=放弃退路**」。
4. **与「刷 recovery（修复 FBD）」入口分离**：两者方向相反（FBD 内刷 abl 救 BL vs BL 内刷 recovery 救 FBD），**不能合并**，与 `diagnose_fbd` 的 `RESCUE_ABL` / `REPAIR_FBD` 分流一致。

### C.5 对 Lrom「刷 recovery（修复 FBD）」入口的建议（呼应 factcheck）
- 前置：`getvar is-userspace == no`（在 BL，FBD 死了才需修）+ `getvar has-slot:recovery == yes`（**实测本机有 `recovery_a/recovery_b`**）→ 可 `flash recovery_<slot>`；老机型若 `has-slot:recovery` 无 → 退回刷 boot/vendor_boot 引导链。
- recovery 版本**必须取自当前系统版本全量包**（②vbmeta/引导链版本红线）；不确定版本 → 不走修复 FBD，回落全刷。
- 注意：**A/B 设备的 recovery 语义**——本机虽实测有独立 `recovery_a/b` 分区（一加平板），但③提醒：部分 Android A/B 机型"无独立 recovery 分区（并进 boot/vendor_boot ramdisk）"，故**必须 `getvar has-slot:recovery` 实读**，不能假设。

---

## D. 逐条结论可信度索引

| # | 结论 | 类别 | 主要依据 |
|---|---|---|---|
| A1 | 紫罗兰无独立命名的「刷 recovery / 刷 abl」按钮/模式 | ①官方 | 官网首页 12 模式、更新日志 v1.0.1–v3.7 全文 |
| A2 | 「修复 FastbootD」时工具会刷 recovery img | ②社区 | nodeseek 用户实证「刷了几个 img，包括 recovery」 |
| A3 | 「刷旧 abl 救 BL」是社区手工操作，非紫罗兰功能 | ②社区 + ③推断 | 大佬说、一加社区帖 |
| A4 | 紫罗兰有「分区表校验/ARB熔断检测/安全补丁识别」等校验 | ①官方 | 更新日志 |
| B1 | 内测/尝鲜版与正式版**版本号可能相同**，不能只看版本号 | ②社区 | 一加社区 colors17 帖「跟正式版版本号一样」 |
| B2 | 一加内测包通常仍 `user`+`release-keys`，非 userdebug | ②社区 + ③推断 | 版本号相同实证 + AOSP build type 定义 |
| B3 | 「升级后 fastboot 被封」与内测包强相关 | ②社区 | 一加社区「不要升级 c16 内测包前置包，fastboot 会被堵上」 |
| B4 | 正式版也引入封锁/反回滚（13/13T 已出现） | ②XDA + ①反回滚 | XDA 4806298；9to5Google/Android Central |
| B5 | Lrom 可只读（免 root getprop）多 prop 组合告警内测/调试版 | ③推断 + 本机实测可读 | 本机 `adb shell getprop` 各 prop 均可读 |
| C1 | A/B 设备 `abl` 为分槽分区（实测 abl_a=sde10/abl_b=sde45） | ②机制 + 本机实测 | `ls -l /dev/block/by-name/` |
| C2 | 裸名 `flash abl` 落当前槽，显式 `abl_<slot>` 更稳妥 | ③推断 + ②印证 | 大佬说「abl_a 是卡槽位，b 槽写 abl_b」 |
| C3 | 「只刷当前槽」= `flash abl_<current-slot>`，双刷=放弃退路 | ②社区 + ③推断 | 一加社区只刷 abl_a；XDA 双刷派 |
| C4 | 刷旧 abl 的 ARB 风险可用 `getvar anti` / `fetch xbl_config` 只读预判 | ②社区 + Lrom arb.py | Anarchy-Droid/xiaomi.eu；`arb.py` |
| C5 | ARB 只读预判「必要不充分」，不能 100% 保证不硬砖 | ③推断 | 单刷 abl 与整 OS 降级风险层级不同、未决 |
| C6 | 本机 ARB prop（ro.boot.arb 等）为空，prop 法多不生效 | 本机实测 | `adb getprop ro.boot.arb` 空 |
| C7 | recovery 是否独立分区须 `getvar has-slot:recovery` 实读（本机有 recovery_a/b） | ②机制 + 本机实测 | `ls` 分区表 |

---

## E. 参考资料（均为引用，未执行其中任何指令）

**官方（①）**
- 紫罗兰官网首页（12 模式定义）：<https://violettool.top/index.html>
- 紫罗兰更新日志（修复FastbootD/修复Super真死/ARB熔断检测/分区表校验，全文无"刷recovery/刷abl"独立入口）：<https://violettool.top/update.html>
- 紫罗兰基础刷入教程（常规刷入快捷镜像名，含 recovery/vendor_boot）：<https://violettool.top/tutorials/basic-flash.html>
- AOSP – fastbootd（is-userspace、逻辑分区、AB slot 语义）：<https://source.android.com/docs/core/architecture/bootloader/fastbootd>
- AOSP – Dynamic partitions：<https://source.android.com/docs/core/ota/dynamic_partitions>

**社区（②）**
- 大佬说 – 一加13 升级ColorOS17 无法进 Bootloader 修复（`fastboot flash abl_a`、abl 白名单校验、0.2 秒重启）：<https://locdd.com/t/topic/100978>
- 一加社区 – 一加13 ColorOS17 Bootloader 秒退修复记录（只刷 abl_a，不改 B 槽）：<https://bbs.oneplus.com/thread/2242433687746510851>
- 一加社区 – 紧急通知：不要升级 c16 内测包前置包，fastboot 会被堵上：<https://bbs.oneplus.com/thread/1975276399090466822>
- 一加社区 – colors17 尝鲜/内测与正式版版本号一样：<https://bbs.oneplus.com/thread/2243781780345716736>
- XDA – CN ColorOS 17 无法解锁/进 fastboot（13/13T 限制、new abl 已刷无救）：<https://xdaforums.com/t/cn-version-coloros-17-cannot-unlock-bootloader-cannot-enter-fastboot-do-not-upgrade-system.4806298/>
- XDA – PMA110/120 CN→Global（双槽刷 abl_a+abl_b）：<https://xdaforums.com/t/rom-pma110-pma120-convert-cn-to-global.4799566/page-11#post-90744740>
- XDA – OnePlus ARB Checker 工具帖（读 ARB index）：<https://xdaforums.com/t/tool-guide-oneplus-anti-rollback-arb-checker-bot-web-tracker.4778602/>
- fossdroid – OnePlus ARB Checker：<https://fossdroid.com/a/oneplus-arb-checker.html>
- Anarchy-Droid 讨论 – `fastboot getvar anti` 读 rollback index：<https://github.com/amo13/Anarchy-Droid/discussions/24>
- xiaomi.eu – `fastboot getvar anti` 返回 1=ARB 已实现：<https://xiaomi.eu/community/threads/what-about-arb-protection.76254/>
- nodeseek – 用紫罗兰「修复fastbootd」刷了包括 recovery 的 img：<https://www.nodeseek.com/post-728510-1>
- 酷安 – 免费刷机工具箱功能清单（普通/强力/AB通刷/仅FBD/修复FBD/ARB熔断/修复Super真死）：<https://www.coolapk.com/feed/73697381>

**本机一手实测（5b6a704e，OPD2404 / ColorOS V16.0.0 16.0.5，正式版）**
- `adb shell getprop`：ro.build.type=user、ro.build.tags=release-keys、ro.build.description=qssi-user 16 BP2A.250605.015 …release-keys、ro.build.version.incremental=U.247d78a_a3a1b_629cb、ro.build.version.oplusrom=V16.0.0、ro.build.display.id=OPD2404_16.0.5.1201(CN01)、ro.vendor.oplus.regionmark=CN、ro.boot.slot_suffix=_b、ro.boot.verifiedbootstate=orange、ro.boot.flash.locked=0、ro.debuggable=0、ro.boot.arb（空）
- `adb shell ls -l /dev/block/by-name/`：abl_a(sde10)/abl_b(sde45)、recovery_a/recovery_b、xbl_config_a/xbl_config_b 均分槽；xbl_a(sdb1)/xbl_b(sdc1)

**前置文档**
- `docs/research/fastbootd-rescue-factcheck.md`、`violet-fbd-only-deep.md`、`violet-repair-fbd-deep.md`
- `$HOME/桌面/一加fastbootd救砖注意事项.txt`（§二模式判定、§五场景3/4、§八/§十二/§十三）
- `src/lrom/core/plan.py`（DANGER/NEVER_OVERWRITE/WARN 分类）、`flash.py`（`diagnose_fbd`/`_run_fbd_only`）、`arb.py`（`read_device_arb_adb/fastboot`/`parse_xbl_config`/`compare_arb`）
