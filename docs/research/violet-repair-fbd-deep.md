# 紫罗兰「修复 FastbootD」深研 —— 实现导向技术规格

> 调查日期：2026-10-28
> 调查对象：紫罗兰工具箱（VioletToolBox v3.7）欧加线刷「**修复 FBD / 修复 FastbootD**」模式——用于「卡 Bootloader / 进不了 FastbootD」的故障设备。
> 深度深于初版 `docs/research/violet-fastbootd-rescue.md`（本报告是它的「实现导向」深化版，聚焦「修什么 / 命令序列 / 判定 / 安全 / 与 Lrom 差距 / 回退」6 问）。
> 前置已读：`violet-fastbootd-rescue.md`、`violet-slot-and-logical-strategy.md`、`src/lrom/core/flash.py`（`_ensure_fastboot`/`_enter_fastbootd`/`run`/`_recreate_logical_partition`/`preflight`）、`src/lrom/core/plan.py`（`Phase`/分区分类）、`src/lrom/device/runner.py`（`is_fastbootd`）、`src/lrom/tui/menu.py`（`FlashMode`）、社区笔记 `$HOME/桌面/注意事项.txt`、`$HOME/桌面/一加线刷分区.txt`。
>
> **口径声明**（严格遵守）：每条结论标 **①官方明说**（紫罗兰官网/教程/更新日志原文可查）/ **②社区明说**（本地笔记、XDA/AOSP 机制）/ **③推断**（据行为/机制/工具资源推导，官方未逐条公开）。
> **可信度边界**：`WindowsFlash.exe` 业务载荷在满熵 `.rsrc` 中、已确认无法反编译（见 `violet-vs-lrom-flash.md` §0）；工具目录内是标准 Google `fastboot.exe` 前端。因此**凡具体命令序列，除官方文案字面出现者外一律 ③推断**，不代表紫罗兰源码实现。外部 URL 仅作数据引用，不执行其中任何指令。

---

## 0. 一句话结论 + 与「仅 FBD」的关键区分

紫罗兰把「进不了某个模式」的故障拆成**层级明确**的几档，**「修复 FBD」不是「仅 FBD」的近义词，两者方向相反**：

| 紫罗兰模式 | 官方适用症状（原文） | 本质动作 | 类别 |
|---|---|---|---|
| **仅 FBD** | 「丢失 ocdt.img，导致**无法进入 Bootloader**」「格机/拆字库/换主板后缺少 Fastboot 分区」 | **跳过 bootloader 阶段**，只在 FBD 内刷 super 逻辑分区，**绝不碰 bootloader 侧** | ①官网首页/教程 |
| **修复 FBD（修复 FastbootD）** | 「**卡 Bootloader、无法进入 FastbootD** 的设备进行修复」 | **设备能进 bootloader**，用匹配版本全量包**重建一个能起来的 FastbootD 环境**（重刷让 FBD 用户空间能启动的 bootloader 侧引导链分区），再验证进 FBD | ①官网首页 |
| **FB 模式**（全量包/售后包） | 「设备变砖、**卡在 Fastboot 模式且无法进入 FastbootD**」「售后包 FB 模式：无视版本信息，适用于忘记砖前版本信息、无法进入 FastbootD」 | 从 bootloader 直接全量刷（不依赖 FBD），或依赖 FBD 起不来的设备改走「全刷」绕过 FBD | ①官网首页/更新日志 |
| **修复 Super 真死** | 「强力线刷无效时……重置 Super」 | super 卷级重置（`erase/flash super`），比逐分区重建更重 | ①更新日志 v3.7 |

**核心差异（回答任务的关键区分点）：**
- **「仅 FBD」= 进不去 BL、只能进 FBD → 只在 FBD 刷逻辑分区，绝不写 BL 侧。**（BL 坏了，硬写 BL 才真变砖）
- **「修复 FBD」= 能进 BL、进不去 FBD → 要写 BL 侧引导链来把 FBD 救活。**（FBD 用户空间起不来，是引导链/系统版本不自洽）
- 二者**方向相反**：前者假设 FBD 可用、BL 不可用；后者假设 BL 可用、FBD 不可用。**判定"当前在 BL 还是 FBD"是整个分流的根节点**（`fastboot getvar is-userspace`）。

> **⚠️ 重要发现（官方原文，①）**：官网首页把 **FB 模式**独立列为「适用于设备变砖、卡在 Fastboot 模式且无法进入 FastbootD 模式」；售后包模式页更明确「**FB 模式（刷写速度较慢）：全网首发无视版本信息，直接刷机，适用于手机变砖后，忘记砖前版本信息无法进入 FastbootD 模式时使用**」（更新日志 v2.0.1 段，①）。这揭示了紫罗兰对「**修复 FBD 还是 FB 模式**」的分流逻辑：
> - **记得砖前版本、且 FBD 能救** → 「修复 FBD」（用匹配版本全量包重建 FBD 环境）。
> - **不记得版本 / FBD 也进不去** → 「FB 模式」（无视版本直接全刷，绕过 FBD）。
> 这条对 Lrom 落地极重要（见 §5）：**Lrom 若无法确认版本匹配，就不能盲目走"修复 FBD"，而应回落到全刷/FB 语义**。

---

## 1. 「修复 FastbootD」到底修什么？设备为什么进不了 FBD？

### 1.1 FastbootD 是什么、依赖什么（②社区+AOSP 机制，③归纳）

- **FastbootD 不是 bootloader，是运行在 Android 用户空间的 `fastbootd` 服务**（AOSP recovery 的一部分，由 `init` 拉起，挂在 `recovery`/`vendor_boot` 的 ramdisk 里，用 `/system/bin/fastbootd`）。**②社区明说/机制**（`一加线刷分区.txt` §零/§一：`fastboot flash super super.img` 等逻辑分区"必须在 fastbootd 模式下刷，不是 bootloader"）。
- 因此 **bootloader（abl/xbl）能起来 ≠ FastbootD 能起来**，反过来 FBD 能起来也 ≠ bootloader 正常。二者是**两条独立的启动路径**：
  - **进 bootloader（fastboot 模式）**：由 `abl`（Android Bootloader）+ `xbl`/`xbl_config` 引导链决定；`engineering_cdt`（OCDT）校准分区若损坏/非本机，会导致**连 bootloader 都进不去**（②`一加线刷分区.txt` §三）。
  - **进 FastbootD（用户空间 fastboot）**：bootloader 正常后，要能加载并启动 **`recovery`/`vendor_boot`（含 ramdisk）** 与 **`init_boot`**，并让 `fastbootd` 服务在用户空间起来；这需要 **boot/vendor_boot/init_boot/recovery 与 `vbmeta` 系列（AVB）版本自洽**。任一环节版本不自洽（如刷了别的版本 boot/vbmeta）→ FBD 起不来。

### 1.2 「卡 Bootloader、进不了 FastbootD」的成因（③推断 + ②机制）

综合机制与官方"用匹配版本全量包修复"的话，最可能成因（按概率）：

1. **引导链/系统版本不自洽（最常见）**：刷了**非本机版本、或跨版本**的 `boot / init_boot / vendor_boot / vbmeta*`，导致 FBD 用户空间启动校验失败 → 卡在 bootloader，`fastboot reboot fastboot` 没反应或回黑屏/900E。→ **官方对策正是"用与原系统版本匹配的全量包修复"（①教程）**。
2. **`vbmeta` 校验失败导致 FBD 启动被 AVB 拦下**：`vbmeta.img` 用错版本会"开机秒断电循环"（②`一加线刷分区.txt` §三）；同理 FBD 下的 ramdisk 校验失败也起不来。
3. **`recovery`/`vendor_boot` 损坏或缺失**：FBD 服务依赖 recovery ramdisk，缺了/坏了 FBD 无法拉起。
4. **super 侧逻辑分区/元数据损坏连带**：FBD 起来要先挂载逻辑分区，super 结构坏会连带 FBD 起不来（此时单纯重刷引导链不够，还要修 super，见 §3.2/§6）。
5. **`abl`/`xbl` 版本与 boot 不配套**：如跨区刷后 abl 未配套，"进 fastboot 但留不住"（②`注意事项.txt` §九）。

> **关键界定（回答"修什么"）**：修复 FBD 修的是**"让 `fastbootd` 这个用户空间服务能起来"所需的 bootloader 侧引导链分区**——**不是**修 super 里的逻辑分区本身，**也不是**修 bootloader 本身（bootloader 是好的，否则就是"仅 FBD"场景了）。官方一句话可证：**「设备无法进入 FastbootD 时，请使用与原系统版本匹配的全量包修复」**（①教程）。

### 1.3 官方描述的适用症状汇总（①官方原文）

| 症状（官方语） | 出处 |
|---|---|
| 「修复FBD：适用于**卡 Bootloader、无法进入 FastbootD 的设备**进行修复」 | 官网首页 <https://violettool.top/> |
| 「设备**无法进入 FastbootD** 时，请使用**与原系统版本匹配的全量包**修复，也可使用工具的快捷提取功能」 | 教程 <https://violettool.top/tutorials/oplus-flash/> |
| 「新增欧加刷写**售后包模式接入修复FastbootD功能，可直接使用散包修复fastbootD**」 | 更新日志 <https://violettool.top/update.html> |
| （对照）**FB 模式**「适用于设备变砖、**卡在 Fastboot 模式且无法进入 FastbootD**」/「无视版本信息，直接刷机，适用于忘记砖前版本信息无法进入 FastbootD」 | 官网首页 + 更新日志 v2.0.1 |
| （对照，900E）「高通设备刷完日志无报错但**黑砖不亮屏**……设备管理器见 Qualcomm HS-USB **Diagnostics 900E** 端口……重新进 Fastboot 勾选强力线刷重刷」 | 疑难杂症页 <https://violettool.top/trouble/trouble2.html> |

> 注意 **900E ≠ FBD 场景**：官方把 900E 黑砖归到「强力线刷（重新构建逻辑分区）」处理（①疑难杂症页），**不是**「修复 FBD」。900E 是"刷机包不完整"的诊断态（②`注意事项.txt` §五：900E 是诊断模式，不是 EDL）。修复 FBD 的目标症状是「**能进 BL 但 FBD 起不来**」。

---

## 2. 修复动作的完整命令序列（重建一个可用的 FastbootD 环境）

> ⚠️ 命令序列除官方文案字面写明者外均为 **③推断**（按 Android FastbootD/AVB 机制 + 工具资源 [Google 官方 fastboot.exe] 推断）。落地需实机验证。

### 2.1 分两步：先「重建 FBD 环境」→ 再「验证进 FBD」

**阶段 A —— 在 bootloader 内，重刷"让 FBD 能起来"的引导链分区（③推断，依据①"用匹配版本全量包修复"）：**

```text
# 前提：设备停在 bootloader（fastboot 模式），能响应 fastboot
fastboot getvar current-slot            # 记下目标槽（如 a）
fastboot getvar is-userspace            # 期望 no（确认在 bootloader，不是 FBD）

# —— 核心：让 fastbootd（用户空间）能拉起的引导链 ——
# 顺序原则：先 ramdisk/内核侧（boot/init_boot/vendor_boot/recovery），
#          再 AVB 校验（vbmeta 系列），最后 dtbo 等硬件描述。
fastboot flash boot        boot.img
fastboot flash init_boot   init_boot.img        # Android 13+/GKI 机型（②社区，FBD 依赖的通用 ramdisk）
fastboot flash vendor_boot vendor_boot.img      # FBD（recovery）ramdisk 在 vendor_boot 里
fastboot flash recovery    recovery.img         # FBD 服务承载（若该机型有独立 recovery）
fastboot flash dtbo        dtbo.img
fastboot flash vbmeta         vbmeta.img          # 必须与当前系统版本一致（②红线）
fastboot flash vbmeta_system  vbmeta_system.img
fastboot flash vbmeta_vendor  vbmeta_vendor.img
```

> 说明（③推断 + ②）：
> - **优先只重刷 `boot/init_boot/vendor_boot/recovery/vbmeta*`**：这些是 FBD 用户空间启动的最小集合；**不碰 `abl/xbl/xbl_config`**（那些是 bootloader 本身，能进 BL 说明它们基本 OK，乱刷才是真变砖风险）。
> - 若已知是**跨版本/跨区**导致不自洽，官方/社区口径主张**用匹配版本全量包整套刷**（即把全部物理+固件分区都刷成同一版本，①教程"与原系统版本匹配的全量包"）——此时等价于走"普通/强力线刷"的 FASTBOOT 阶段全量。
> - **`vbmeta` 版本必须=当前系统版本**（②`一加线刷分区.txt` §三），否则进了也秒断电循环。

**阶段 B —— 验证 FBD 是否恢复（③推断，机制）：**

```text
fastboot reboot fastboot        # 从 bootloader 请求进入 FastbootD
# 等待设备重启到 FBD，再确认：
fastboot getvar is-userspace    # 期望 yes（成功进 FBD）
```

- **成功** → 若系统数据仍需补齐，转入「修复 Super / 强力线刷」在 FBD 内刷逻辑分区（见 §2.2）。
- **失败（仍卡 BL / 黑屏 / 900E）** → 见 §6 回退（可能是 `abl`/引导链更深层不配套，或版本仍不匹配；继续升级到全量刷 或 FB 模式/EDL）。

### 2.2 修复 FBD 成功后，若还要补系统（③推断，衔接逻辑分区）

进 FBD 后，若要修系统：等价"强力线刷/修复 Super"——

```text
# 在 FastbootD 内（is-userspace=yes）
fastboot delete-logical-partition system_a
fastboot delete-logical-partition system_a-cow      # 清 COW 残留（②）
fastboot create-logical-partition system_a <blocks> # blocks = size_bytes/4096
fastboot flash system system.img
# ……其余逻辑分区同理（system_ext/product/vendor/odm/*_dlkm/my_*）
fastboot --set-active=a        # 或沿用原槽
fastboot reboot
```

> 注：**逻辑分区禁双槽**（②`一加线刷分区.txt` §八；`--slot=all` 刷 super 另一槽无法保留会要求 `-w`）。Lrom 已对 FASTBOOTD 阶段禁双槽（`_flash_other_slot` 直接 return None），方向正确。

### 2.3 官方"散包修复"路径（①更新日志）

「售后包模式接入修复FastbootD功能，**可直接使用散包修复fastbootD**」（①更新日志）→ 说明修复 FBD **不需要整包解 payload 合并 super**，可以直接挑引导链散包重刷即可。这对 Lrom 落地的启示：**修复 FBD 只需 `boot/init_boot/vendor_boot/recovery/vbmeta*` 这几个小镜像**，无需 ≥30GB 合并空间（与"修复 Super 真死"的重资源路径区分开）。

---

## 3. 判定方法：Lrom 如何判断「卡 bootloader、进不了 FBD」？如何与「仅 FBD」区分？

### 3.1 现有 Lrom 的判定能力（读码）

- `FastbootRunner.is_fastbootd()`（`runner.py:205-209`）：`fastboot getvar is-userspace` → `yes`=FBD。**这是唯一的"在 BL 还是 FBD"判据**（②等价笔记方法2）。
- `FlashExecutor._detect_mode()`（`flash.py:640-655`）：返回 `adb / fastboot / fastbootd / none`。**但它不分"修复 FBD vs 仅 FBD"**，只是模式探测。
- `_ensure_fastboot()`（`flash.py:615-638`）：若在 fastbootd → **主动 `fastboot reboot bootloader`** 踢回 BL；在 adb → `adb reboot bootloader`。
- `_enter_fastbootd()`（`flash.py:684-727`）：从 BL 发 `fastboot reboot fastboot` 轮询，**超时（默认 300s）只报错/打印提示**，**没有修复路径**（`flash.py:720-726`）。
- `run()`（`flash.py:542-596`）：**第一步强制 `_ensure_fastboot()`**（回到 BL），刷 FASTBOOT 阶段，再 `_enter_fastbootd()` 刷 FASTBOOTD 阶段。

### 3.2 判定「卡 BL、进不了 FBD」的正确姿势（③设计，依 ②机制 + ①官方症状）

需要一个**三态诊断探针**，在真正刷写前先探测设备落在哪个"故障层级"：

```text
步骤1  探测物理模式：
       adb devices 有 device?      → state=adb
       fastboot devices 有设备?     → 若 is-userspace=yes → state=fbd
                                      否则                 → state=bootloader
       都没有                       → state=none（可能 900E/EDL/真砖）

步骤2  关键分流（以"当前进哪个模式 + 能否进入另一个模式"为判定）：
  A. state=bootloader 且从 BL 发 `fastboot reboot fastboot` 后 is-userspace 仍 != yes
        → 【修复 FBD 候选】：卡 BL、进不了 FBD。
     （安全前提：再确认 `fastboot getvar product`/版本号能读、型号匹配 → 才允许重刷引导链）
  B. state=fbd（is-userspace=yes，但 BL 进不去 / OCDT 丢）
        → 【仅 FBD 候选】：只在 FBD 刷逻辑分区，绝不写 BL 侧。
     （判据：从 FBD 发 `fastboot reboot bootloader` 后回不到 BL / getvar product 异常 ②）
  C. state=bootloader 且 reboot fastboot 能进 FBD
        → 正常，走普通/强力线刷（不需修复 FBD）。
  D. state=none / 900E / 连 fastboot 都无
        → 超出修复 FBD 范围，走强力线刷（若 900E 且 BL 可进）/ FB 模式 / EDL（见 §6）。
```

**「修复 FBD」与「仅 FBD」的判别表（最终判据）：**

| 判据 | 修复 FBD | 仅 FBD |
|---|---|---|
| `is-userspace` 当前值 | **no**（在 BL） | **yes**（在 FBD） |
| 从 BL `reboot fastboot` 能进 FBD？ | **否**（进不去，才需修） | （本就在 FBD，不测此项） |
| 从 FBD `reboot bootloader` 能回 BL？ | 能（BL 是好的） | **否 / OCDT 丢**（BL 进不去） |
| 要写 bootloader 侧分区？ | **是**（boot/init_boot/vendor_boot/recovery/vbmeta*） | **绝不** |
| 危险点 | 版本不匹配 → AVB 断电循环/更砖 | 写 BL 侧 → 真变砖 |

> **③推断的安全铁律**：**永远先测 `is-userspace`**。
> - 若已 `yes`（FBD 可用），**绝不走"修复 FBD"路径去写 BL**——那会人为制造"仅 FBD"场景甚至更砖；
> - 若 `no` 且 `reboot fastboot` 进不去 → 才是修复 FBD；
> - 若 `reboot fastboot` 能进 → 直接正常刷，不必修。

### 3.3 现有 Lrom 在判定上的两个硬缺口

1. **无法区分"设备在 BL 但 FBD 起不来"。** 现在 `_ensure_fastboot()` 只保证"在 BL"，随后 `_enter_fastbootd()` 去切 FBD，超时只报错。**逻辑上它"卡 BL 进不了 FBD"时等同于普通线刷在 FASTBOOTD 阶段失败**，没有独立诊断。
2. **"仅 FBD"场景会被 `run()` 主动破坏**：`run()` 第一步 `_ensure_fastboot()` 若检测到 FBD，会 `reboot bootloader` 把设备踢回 BL；若该设备 BL 进不去（OCDT 丢），则直接被踢死。**这是与"仅 FBD"语义的正面冲突**（初版报告已指出，仍未修）。

---

## 4. 前置安全校验与防二次变砖

> 紫罗兰对"前置判断"披露少，只能据官方零散文案 + Android 机制 + 社区笔记归纳，**以下多属③推断**，官方明说的单独标①。

### 4.1 官方明说的安全能力（①）

- **ARB 熔断检测**：「自动识别当前设备或固件包是否熔断，防止降级变砖」（①官网首页）。
- **分区表校验**：「通过比对刷机包中的部分底层分区与设备分区表内容，判断当前刷机包是否与设备匹配，预防刷错包导致黑砖；仅在刷写前进行安全检查，无任何风险」（①更新日志）。
- **自动识别熔断版本并提示**：「全部方案新增自动识别手机当前系统的安全补丁，自动识别熔断版本并提示，防止熔断版本降级变砖」（①更新日志）。

### 4.2 修复 FBD 专属的前置硬门槛（③设计，依据 ②红线 + ①官方）

修复 FBD 要写 `vbmeta*`（红线）与 `boot/init_boot/vendor_boot`，**必须逐条卡死**：

1. **型号必须匹配**：`fastboot getvar product`（或 `getvar all` 里 product）与包内 codename 一致；否则拒绝（②`一加线刷分区.txt` §三/§十，刷错机型包=直接变砖）。
2. **版本必须匹配**：①官方"用与原系统版本匹配的全量包"。修复 FBD 的引导链分区（尤其 `vbmeta*`）**必须取自设备当前系统版本的全量包**（②`一加线刷分区.txt` §三：vbmeta 版本用错秒断电循环）。**无法确认版本 → 不得走修复 FBD，回落 FB 全刷/EDL**（见 §0 官方 FB 模式口径）。
3. **ARB 不得降级熔断**：目标包 `xbl_config` ARB 值若高于设备已熔断等级 → 拒绝（①ARB 检测；②`注意事项.txt` §一）。注意 **修复 FBD 一般不重刷 `xbl_config`**（不碰 BL 主体），但仍需确认版本方向不触发熔断。
4. **槽位正确**：`getvar current-slot`，引导链分区按目标槽刷；逻辑分区禁双槽（②§八）。
5. **不碰红线/身份分区**：**绝不刷 `engineering_cdt`/OCDT、`persist`、`modem`**（修复 FBD 只重建 FBD 引导链，不涉及这些；②`一加线刷分区.txt` §六/§三）。`plan.py` 的 `NEVER_OVERWRITE`、`WARN_PARTITIONS` 已覆盖这些（含 `engineering_cdt`/`ocdt` 的 DANGER 提醒），方向正确。
6. **电量/线材/驱动**：>50% 电量、原装线、Google 官方 fastboot ≥35.0.0（②`注意事项.txt` §五）。

### 4.3 "修复 FBD 反而变砖"的三大风险（③，必须显式告警）

1. **版本不匹配写 `vbmeta*` → 断电循环**（比修之前更糟）。
2. **误把"仅 FBD"设备当"修复 FBD"去写 BL 侧 → 从"能进 FBD 有救"变成真砖**。
3. **中途断电/断线写 bootloader 链分区 → 半砖**（②`注意事项.txt` §七：新 abl 写入中途失败→三模式全无→EDL 熔断→镇纸）。

---

## 5. 与 Lrom 现有实现的差距 & 落地方案（不改代码，只给设计）

### 5.1 差距盘点（读码确认）

| 能力 | 紫罗兰「修复 FBD」需要 | Lrom 现状 | 代码位置 |
|---|---|---|---|
| 模式探测 | BL / FBD / adb / none | ✅ `_detect_mode()` 有 | `flash.py:640-655` |
| "FBD 可达性"诊断 | 从 BL `reboot fastboot` 后判 `is-userspace` | ❌ `_enter_fastbootd()` 只在刷写中做，超时仅报错 | `flash.py:684-727` |
| 重建 FBD 环境 | 重刷 boot/init_boot/vendor_boot/recovery/vbmeta* 再验 FBD | ❌ 无此子流程 | — |
| 版本匹配校验 | 引导链/vbmeta 必须同版本 | ⚠️ 有 `arb_db`/`check_device_match`（型号/ARB），**无"版本一致性"判定** | 见 arb.py/arb_db.py |
| 散包挑镜像 | 只需小镜像即可修 | ⚠️ 依赖 payload 解包出的 image_dir；未做"修复子集"选择 | `plan.py` |
| 与仅 FBD 分流 | 先测 is-userspace 决定方向 | ❌ `run()` 第一步强制回 BL，**会破坏仅 FBD** | `flash.py:542-563` |
| 修复 Super 真死 | 整 super 重置 | ❌ 无整 super 档（`recreate_logical` 只到逻辑分区级） | `flash.py:429-476` |

**结论：Lrom 目前完全没有"修复 FBD"的实现**——`_enter_fastbootd` 超时只是报错（`flash.py:720-726`），不带任何"重刷引导链→再验 FBD"的修复路径；且 `run()` 的强制回 BL 与"仅 FBD"冲突。

### 5.2 落地方案（设计，不改源码）

**总体原则（与 `violet-vs-lrom-flash.md` §8/§10 一致）**：不照搬紫罗兰"并列按钮模式"，而是把其**结果**吸收为 Lrom 的「**只读诊断 → 给建议 → 单一安全执行器**」。

#### P0-A：新增「FBD 诊断」只读探针（不改写盘，先诊断）
- 新增 `diagnose_fbd()`（纯只读）：输出
  - `is-userspace`（在 BL 还是 FBD）
  - 若在 BL：`fastboot reboot fastboot` 后轮询 `is_fastbootd()`（短超时 ~60s）→ 得到 `fbd_reachable: yes/no`
  - `current-slot`、`product`、能否读版本
- 产出结论文本：**三选一**
  - `fbd_reachable=no & 在BL` → 「检测到卡 Bootloader、进不了 FastbootD → 建议走【修复 FBD】」
  - `is-userspace=yes & BL 回不去` → 「检测到仅剩 FastbootD、BL 不可用 → 建议走【仅 FBD】(禁写 BL 侧)」
  - `fbd_reachable=yes` → 「FBD 正常，走普通/强力线刷即可」
- **这一步完全只读**，与官方"分区表校验…仅在刷写前安全检查，无任何风险"（①）气质一致。

#### P0-B：新增「修复 FBD」执行路径（`repair_fbd` 子流程）
- 仅在 `diagnose_fbd()` 判定为「卡 BL 进不了 FBD」**且**型号/版本匹配时启用。执行：
  1. 强制停在 BL（`_ensure_fastboot`，但**先判不是"仅 FBD"场景**，见 P0-C）。
  2. 从 `image_dir` **只挑** `boot/init_boot/vendor_boot/recovery/dtbo/vbmeta/vbmeta_system/vbmeta_vendor` 重刷（§2.1）。
  3. `fastboot reboot fastboot` → `is_fastbootd()` 验证。
  4. 成功→打印"FBD 已恢复，可继续刷逻辑分区"；失败→ §6 回退建议。
- **不改现有 `run()` 主流程**，作为独立执行入口（菜单/CLI）接入。
- **必须二次确认**：写 `vbmeta*`/引导链属 `Risk.DANGER`，preflight 已能识别（`plan.py` DANGER 集含 vbmeta 系列？——vbmeta 在 `WARN_PARTITIONS`，boot 系列不在危险集），落地时给这一子流程**显式"将写入 bootloader 侧引导链"高风险确认**。

#### P0-C：修正 `run()` 破坏"仅 FBD"的问题
- `run()` 进 `_ensure_fastboot()` 前，**先判 `is-userspace`**：
  - 若已是 FBD 且检测到 BL 不可达（仅 FBD 场景）→ **不得 `reboot bootloader`**，跳过 FASTBOOT 阶段，直接只跑 FASTBOOTD（初版报告 §5.3 P0 已建议）。
  - 若在 FBD 且 BL 可达 → 保持现有回 BL 行为。
- 这一改动同时消除"仅 FBD"与"修复 FBD"两条路径的互相误伤。

#### P1：版本一致性校验（接入 preflight）
- 在 `preflight()` 增加"版本匹配"检查（读设备当前版本 vs 包版本；无则显式 warn 且**禁止走修复 FBD**）。复用现有 `arb_db`/`arb.py` 的 ARB 检查。
- **落点**：修复 FBD 子流程的准入闸。

#### P1：「修复 Super 真死」整 super 重刷档（与修复 FBD 分层）
- 修复 FBD 只重建 FBD 环境，**可能仍因 super 结构坏而刷不进逻辑分区** → 需「整 super 重刷」档（`erase super`/`flash super super.img`，需合并散包 ≥30GB，①教程；②§六）。
- 语义分档写入 help：普通(不重建)/强力(重建逻辑分区)/修复FBD(重建FBD环境+验证)/整super(重置super)/仅FBD(只FBD不碰BL)。

### 5.3 不建议做（沿用前任结论）

- ❌ 不照搬紫罗兰"4~12 个并列按钮"交互，避免"选错模式"。
- ❌ 修复 FBD 路径**绝不**顺手写 `abl/xbl/xbl_config/persist/OCDT`（能进 BL 说明它们好；碰了才真变砖）。
- ❌ 版本不确定时**不要**走修复 FBD（回落 FB 全刷/EDL，见 §0 官方 FB 模式口径）。

---

## 6. 失败回退与恢复命令

> 修复 FBD 失败（重刷引导链后仍进不了 FBD）的逐级回退（③推断，依 ①官方模式分层 + ②社区救砖路径）：

### 6.1 分级回退（由轻到重）

```text
# —— 级别 0：重试进入 FBD（排除"卡在语言选择界面/线松动"）——
fastboot reboot fastboot
fastboot getvar is-userspace            # 仍 != yes 则升级

# —— 级别 1：确认是否版本不自洽 → 换正确版本引导链再试 ——
#   （官方：用与原系统版本匹配的全量包；②vbmeta 必须同版本）
#   重新核对 getvar product / 版本号 → 重刷正确版本 boot/vendor_boot/vbmeta*

# —— 级别 2：FBD 彻底起不来 + 记得砖前版本 → 全量刷（普通/强力线刷，BL 内全量）——
#   等价紫罗兰 FB 模式（无视 FBD，直接从 BL 全量刷）：
#   在 bootloader 内把物理+固件+（能否）逻辑全刷成同一匹配版本

# —— 级别 3：连 fastboot 都无 / 900E 黑砖 → 官方"强力线刷"→ 仍无效 → "修复 Super 真死"——
#   ①疑难杂症页：900E→强力线刷重刷；①更新日志：强力线刷无效→修复 Super 真死(重置 super)

# —— 级别 4：以上全无效 → EDL 9008 深刷（风险最高，注意 Protect LUN5 / persist）——
#   ②注意事项.txt §七：进 9008；深刷散包；勾选保护 LUN5；OCDT 只能用本机备份
```

### 6.2 关键恢复命令清单

| 场景 | 恢复命令（③推断） |
|---|---|
| 想再进 FBD | `fastboot reboot fastboot` + `fastboot getvar is-userspace` |
| 回 bootloader | `fastboot reboot bootloader`（FBD 内可用时） |
| 逻辑分区 resize 失败 | 改「强力线刷」（delete/create 重建），或 `fastboot delete-logical-partition <p>_<slot>` 后重刷（Lrom 已有 `_recovery_hint`，`flash.py:208-224`） |
| 想临时验证 boot 镜像 | `fastboot boot boot.img`（②`注意事项.txt` §四：能 boot 试的绝不直接 flash） |
| 900E 黑砖 | 官方：重进 Fastboot→强力线刷重刷（①疑难杂症页）；②900E≠EDL，断电+三键退出 |
| 真砖 | EDL 9008 深刷（②`注意事项.txt` §七，Protect LUN5，OCDT 用本机备份） |

### 6.3 回退的安全红线

- **回退过程中不要把"修复 FBD"误升级为"写 bootloader 主体"**：重刷 `abl/xbl` 才是终极端，只在最后 EDL/官方全量时做，且必须型号+版本严格匹配（②§三）。
- **任何 `vbmeta*` 重刷必须版本匹配**，否则从"卡 BL"变"断电循环"（更糟）。
- **记住至少一条生路**：②「进不去 Fastboot ≠ 完全变砖，只要还能进 FBD 就有救」——所以修复 FBD 时**千万别把 FBD 通道也弄没**（不要在没有把握时乱写 BL 主体）。

---

## 7. 官方 / 社区 / 推断 逐条索引

| # | 结论 | 类别 | 出处 |
|---|---|---|---|
| 1 | 修复FBD 适用「卡 Bootloader、无法进入 FastbootD 的设备进行修复」 | ①官方 | 官网首页 <https://violettool.top/> |
| 2 | 无法进入 FastbootD 时用「与原系统版本匹配的全量包」修复 | ①官方 | 教程 <https://violettool.top/tutorials/oplus-flash/> |
| 3 | 售后包模式「接入修复FastbootD功能，可直接使用散包修复fastbootD」 | ①官方 | 更新日志 <https://violettool.top/update.html> |
| 4 | 仅FBD 适用「丢失 ocdt.img，导致无法进入 Bootloader」/「格机/拆字库/换主板后缺少 Fastboot 分区」 | ①官方 | 官网首页 + 教程 |
| 5 | FB 模式「适用于设备变砖、卡在 Fastboot 模式且无法进入 FastbootD」/「无视版本信息…忘记砖前版本无法进入 FBD」 | ①官方 | 官网首页 + 更新日志 v2.0.1 |
| 6 | 900E 黑砖 → 强力线刷重刷（不是修复FBD） | ①官方 | 疑难杂症页 <https://violettool.top/trouble/trouble2.html> |
| 7 | 修复FBD 修的是「让 fastbootd 用户空间能起来的引导链分区」，非 super 逻辑分区本身、非 bootloader 主体 | ③推断 | 由 #1/#2 + FBD 机制推断 |
| 8 | FBD 是用户空间 fastbootd 服务，依赖 recovery/vendor_boot/init_boot/boot/vbmeta 版本自洽 | ②社区/机制 | `一加线刷分区.txt` §零/§一、AOSP fastbootd |
| 9 | `getvar is-userspace` = yes 判 FBD、no 判 BL | ②社区 | `一加线刷分区.txt` 方法2（代码 `runner.py:205-209` 同） |
| 10 | vbmeta 版本用错 → 开机秒断电循环；vbmeta 必须取自当前系统版本 | ②社区 | `一加线刷分区.txt` §三 |
| 11 | OCDT 丢失/非本机 → 进不了 bootloader（=仅FBD场景的成因） | ②社区 | `一加线刷分区.txt` §三/§五 |
| 12 | 逻辑分区禁双槽、`--slot=all` 刷 super 另一槽不保留会要求 `-w` | ②社区 | `一加线刷分区.txt` §八 / `注意事项.txt` §五 |
| 13 | 「进不去 Fastboot ≠ 变砖，能进 FBD 就有救」 | ②社区 | `注意事项.txt` §七 |
| 14 | 修复FBD 具体命令序列（boot/init_boot/vendor_boot/recovery/vbmeta* → reboot fastboot → is-userspace 验证） | ③推断 | 由 #2/#3 + FBD/AVB 机制推断，非源码实证 |
| 15 | 修复FBD 失败回退分层（FBD 重试→换版本→FB 全量→强力→整super→EDL） | ③推断 | 由 #4/#5/#6 + 官方模式分层推断 |
| 16 | Lrom `_enter_fastbootd` 超时仅报错、`run()` 强制回 BL 破坏仅FBD | 本仓库代码事实 | `flash.py:684-727, 542-563` |

> **可信度边界**：紫罗兰内部实现不可反编译（`.rsrc` 满熵，`violet-vs-lrom-flash.md` §0）。除官方文案字面可查者外，命令序列均按 Android FastbootD/AVB 机制推断，**落地须实机验证**，不视为紫罗兰官方实现。

---

## 8. 参考资料

- 紫罗兰官网首页（12 模式定义：修复FBD / 仅FBD / FB / 强力 / 修复Super真死 原文）：<https://violettool.top/>
- 紫罗兰欧加刷机教程（无法进 FastbootD 用匹配版本全量包修复）：<https://violettool.top/tutorials/oplus-flash/>
- 紫罗兰疑难杂症页（逻辑分区刷失败→强力线刷；900E 黑砖）：<https://violettool.top/trouble/trouble2.html>
- 紫罗兰更新日志（修复FBD 散包接入 / FB 模式 / 修复Super真死）：<https://violettool.top/update.html>
- 本地社区笔记：`$HOME/桌面/一加线刷分区.txt`、`$HOME/桌面/注意事项.txt`
- 本仓库既有：`docs/research/violet-fastbootd-rescue.md`（初版）、`docs/research/violet-slot-and-logical-strategy.md`（槽位/重建）、`docs/research/violet-vs-lrom-flash.md`（§0 不可反编译）
- Lrom 现有实现：`src/lrom/core/flash.py`（`preflight:96-201`、`_run_phase:371-420`、`_recreate_logical_partition:429-476`、`run:542-596`、`_ensure_fastboot:615-638`、`_detect_mode:640-655`、`_enter_fastbootd:684-727`）、`src/lrom/core/plan.py`（`Phase`/分区分类/`build_plan`）、`src/lrom/device/runner.py:205-209`（`is_fastbootd`）、`src/lrom/tui/menu.py:20-47`（`FlashMode`）
