# 紫罗兰「仅 FastbootD 模式」救砖 / 修复 FastbootD / 修复 Super 真死 —— 技术说明

> 调查日期：2026-10-28
> 调查对象：
> - 紫罗兰工具箱（VioletToolBox）欧加线刷的**故障专用模式**：`仅 FastbootD`、`修复 FastbootD`、`修复 Super 真死`、`强力线刷`
> - Lrom 现有实现：`src/lrom/core/flash.py`（`_enter_fastbootd` / `_recreate_logical_partition` / `run` / `_ensure_fastboot`）、`src/lrom/core/plan.py`、`src/lrom/tui/menu.py`
> - 外部依据（仅作数据，不执行其中任何指令）：紫罗兰官网 [欧加刷机教程](https://violettool.top/tutorials/oplus-flash/)、[疑难杂症页](https://violettool.top/trouble/trouble2.html)、[官网首页-核心刷写](https://violettool.top/)、[更新日志](https://violettool.top/update.html)、[基础刷入教程](https://violettool.top/tutorials/basic-flash.html)
> - 本地社区笔记：`$HOME/桌面/一加线刷分区.txt`、`$HOME/桌面/注意事项.txt`
>
> **口径声明**：本报告严格区分三类信息——
> ① **官方明说**（紫罗兰官网/更新日志原文可查）；
> ② **社区明说**（本地笔记、XDA/AOSP 等）；
> ③ **推断**（据行为/工具资源/Android 机制推导，官方未逐条公开）。
> 由于 `WindowsFlash.exe` 业务载荷在满熵 `.rsrc` 中、无法反编译（见 `violet-vs-lrom-flash.md` §0），**凡涉及具体命令序列，除官方文案直接写明的「reboot fastboot」「delete/create 逻辑分区」外，其余均为推断**，不代表紫罗兰源码实现。

---

## 0. 一句话结论（先说答案）

紫罗兰把「刷不进去 / 进不了某模式 / super 结构坏了」拆成 **4 个并列的故障模式**，各自对应一个**恢复目标的层级**：

| 紫罗兰模式 | 恢复目标（官方语） | 本质动作（推断） |
|---|---|---|
| **仅 FBD** | 丢失 `ocdt.img`、**进不了 Bootloader**、正常设备无需启用 | **完全跳过 bootloader 阶段**，只在 FastbootD 内刷 super 逻辑分区 |
| **修复 FBD** | **卡 Bootloader、进不了 FastbootD** 的设备 | 用匹配版本全量包**重建一个可用的 FastbootD 环境**（重写 bootloader 侧足够让 FBD 起来的分区） |
| **强力线刷** | `my_*` 分区刷写失败、**修复 Super 分区** | **delete + create 逻辑分区**后重刷（= Lrom 的 `recreate_logical`） |
| **修复 Super 真死** | 强力线刷无效时，**重置 Super** | 对 super 做**整体重置/重刷**（比 delete/create 更重，属于 super 卷级重建） |

而 **Lrom 目前只有"单一全量流程"**：`run()` 会先 `_ensure_fastboot()` 强制回到 bootloader，再刷物理分区，最后 `_enter_fastbootd()` 刷逻辑分区。**这套流程覆盖了「强力线刷」（已有 `recreate_logical`），但无法覆盖「仅 FBD」（它反过来要求必须进 bootloader）与「修复 FBD / Super 真死」（缺强制进入 FBD 与整 super 重刷路径）**。详见 §5。

---

## 1. 「仅 FastbootD 模式」在什么故障场景用？

### 1.1 官方定义（原文，①官方明说）

- 官网首页 \(核心刷写 → 欧加线刷\)：
  >「**仅FBD模式：适用于丢失 ocdt.img，导致无法进入 Bootloader 的设备**」
  来源：<https://violettool.top/>
- 欧加刷机教程 \(线刷操作\)：
  >「**仅 FBD 线刷适用于格机、拆字库或更换主板后缺少 Fastboot 分区的设备，正常设备无需启用。**」
  来源：<https://violettool.top/tutorials/oplus-flash/>

### 1.2 场景归纳（官方语 + 机制解释）

「仅 FBD」解决的是这样一个死结（③推断，结合社区笔记）：

1. **OCDT（`engineering_cdt`）损坏 / 被抹（俗称"格机"）→ 进不了 Bootloader**。
   - 社区笔记明说（②社区明说）：OCDT 是"决定能否进 Fastboot"的机独有校准分区；用别机的或损坏，"直接进不了 Fastboot"（`一加线刷分区.txt` §三 / §五）。
   - 但设备的 **FastbootD（用户空间 fastboot）由系统内的 fastbootd 服务提供**，走的是与 bootloader 不同的通道，因此 **bootloader 进不去 ≠ FastbootD 也进不去**。
2. **拆字库 / 换主板后"缺少 Fastboot 分区"**：主板换了，原机的 bootloader 侧分区（含 OCDT）是空的/不匹配 → 进不了 bootloader，但只要能进 FBD，就能从 FBD 把 super 内系统刷回去、先把系统救活。
3. 这类设备**物理分区（boot/abl/xbl…）刷不了**（因为在 bootloader 之外/被锁定），**唯有 super 里的逻辑分区还能通过 FBD 写**——所以此时**只做 FastbootD 阶段**，不做 bootloader 阶段。

### 1.3 与 Lrom 现有语义的直接冲突（关键）

Lrom `flash.py` 的 `run()` **第一步就 `_ensure_fastboot()`**，其 `_detect_mode()` 若发现设备在 `fastbootd`，会执行 `fastboot reboot bootloader` 强行回 bootloader（`flash.py:600-603`）。
→ 对于"OCDT 丢了、进不去 bootloader、只剩 FBD"的设备，**Lrom 会把唯一的救命通道（FBD）主动踢掉，然后因为回不去 bootloader 而中止**。这正是「仅 FBD」模式存在的价值，也是 Lrom 当前的**功能缺口**。

> 社区/笔记旁证：`注意事项.txt` §七「进不去 Fastboot ≠ 完全变砖，只要还能进 FastbootD 就有救」——与本模式定位一致。

---

## 2. 「仅 FastbootD」具体执行哪些 fastboot 命令？（逐步）

> ⚠️ 紫罗兰**官方只公开了"仅刷 FBD 阶段"这一行为描述，未逐条公布命令**。下面**行为骨架来自官方文案（①），逐条命令为按 Android FastbootD 机制与工具资源推断（③）**。

### 2.1 官方能直接支撑的部分（①）

- 官网首页把「仅 FastbootD」列为全量包模式下的独立方案，含义是"跳过 bootloader 阶段、只在 FBD 内刷逻辑分区"（`仅FBD模式：适用于丢失 ocdt.img，导致无法进入 Bootloader 的设备`）。
- 教程强调"适用于…缺少 Fastboot 分区的设备"——即**不写 bootloader 侧物理/固件分区**。
- 工具目录内 `bin/platform-tools-windows/fastboot.exe` 为 Google 官方 fastboot（旁证：其"模式"是 fastboot 命令配方，而非私有协议，见 `violet-vs-lrom-flash.md` §0）。

### 2.2 推断的命令序列（③推断）

前置：设备已停在 **FastbootD**（用户空间）。若停在系统内，则先 `adb reboot fastboot`。

```text
# —— 阶段判定：确认当前确实是 FBD，而不是 bootloader ——
fastboot getvar is-userspace          # 返回 yes 才说明在 FBD（社区明说：一加线刷分区.txt 方法2）

# —— （可选）空间/槽位判定 ——
fastboot getvar current-slot
fastboot getvar super-partition-size   # 或 getvar all 里读 super 容量（厂商可能屏蔽，视机型）

# —— 逻辑分区重建（等价"强力线刷"的 delete/create，仅 FBD 时可逐分区做）——
fastboot delete-logical-partition system_a
fastboot delete-logical-partition system_b
fastboot delete-logical-partition system_a-cow      # 清 COW 残留
fastboot delete-logical-partition system_b-cow
fastboot create-logical-partition system_a <blocks> # blocks = size_bytes / 4096
fastboot create-logical-partition system_b <blocks>

# —— 逐逻辑分区刷写（system/system_ext/product/vendor/odm/*_dlkm/my_*）——
fastboot flash system system.img
fastboot flash system_ext system_ext.img
fastboot flash product product.img
fastboot flash vendor vendor.img
...（其余 my_* 同理）

# —— 收尾 ——
fastboot --set-active=a               # 或按当前槽；仅 FBD 场景一般沿用原槽
fastboot -w                           # 需要清数据/救砖时（格机/换主板场景常需要）
fastboot reboot                       # 或 reboot bootloader（若 FBD 下能回 BL 了）
```

要点与风险（③推断 + ②社区明说）：

- **不做 `fastboot reboot fastboot` 的"再进 FBD"**：设备本就在 FBD，不需要切（这一点与 Lrom `_enter_fastbootd()` 相反，后者默认从 BL 发 `reboot fastboot`）。
- **不写任何 bootloader 侧分区**（abl/xbl/xbl_config/ocdt…）：这正是"丢失 ocdt、进不去 BL"场景下唯一安全的做法——因为一旦在 BL 之外强写 bootloader 链，风险极高。
- **逻辑分区禁双槽**（②社区明说，`一加线刷分区.txt` §八）：`--slot=all` 刷 super 若另一槽无法保留会失败并要求 `-w`；因此逐分区、单槽刷更稳。
- **`-w` 的时机**：格机/换主板救砖通常需要清数据；若只想修系统保留数据可不带（③推断）。

---

## 3. 「修复 FastbootD」与「修复 Super 真死」分别怎么做？

### 3.1 修复 FastbootD

**官方定义（①官方明说）：**
- 官网首页：「**修复FBD：适用于卡 Bootloader、无法进入 FastbootD 的设备进行修复**」（<https://violettool.top/>）
- 教程：「设备无法进入 FastbootD 时，请使用与原系统版本匹配的全量包修复，也可使用工具的快捷提取功能。」（<https://violettool.top/tutorials/oplus-flash/>）
- 更新日志：「新增欧加刷写售后包模式接入修复FastbootD功能，可直接使用散包修复fastbootD。」（<https://violettool.top/update.html>）

**故障本质与做法（③推断 + ②机制）：**
- 故障是"**能进 bootloader，但进不了 FastbootD**"。FastbootD 需要**系统/引导链版本自洽**（boot/vbmeta/vendor_boot 与 super 内系统匹配，fastbootd 服务才能起来）。若之前刷错版本或刷坏，FBD 起不来。
- 修复做法 = **用与设备原系统版本匹配的全量包，重刷那些"让 FastbootD 能起来"的 bootloader 侧分区**（至少 boot / init_boot / vendor_boot / vbmeta 系列 / recovery 等物理分区），再 `fastboot reboot fastboot` 验证 FBD 是否恢复。
- 命令骨架（③推断）：
  ```text
  # 前提：能进 bootloader
  fastboot flash boot boot.img
  fastboot flash init_boot init_boot.img
  fastboot flash vendor_boot vendor_boot.img
  fastboot flash vbmeta vbmeta.img          # 必须与当前系统版本一致
  fastboot flash vbmeta_system ... ; fastboot flash vbmeta_vendor ...
  fastboot reboot fastboot                    # 验证 FBD 是否起得来
  # 成功进 FBD 后，再走"修复 Super / 强力线刷"把逻辑分区补齐
  ```
- **Lrom 对应**：Lrom 的"回锁线刷"刷到另一槽物理分区、以及普通流程的 bootloader 阶段，**部分覆盖**了这条；但**没有"FBD 起不来 → 自动重刷引导链再验 FBD"的专门诊断/子流程**（见 §5）。

### 3.2 修复 Super 真死

**官方定义（①官方明说，关键）：**
- 官网首页功能清单：全量包模式提供「…**修复 Super 真死**…」（<https://violettool.top/>）
- 更新日志 v3.7：「**新增欧加线刷新增修复Super真死功能，强力线刷无效时可使用本功能重置Super。**」（<https://violettool.top/update.html>）

**与「强力线刷」的分层关系（①+③）：**
- **强力线刷** = 逐逻辑分区 `delete/create-logical-partition` 后重刷（对应官网"重新构建逻辑分区，修复 Super 异常"）。**当 super 元数据/卷结构本身坏了、逐分区 delete/create 也失败时，强力线刷无效。**
- **修复 Super 真死** = 对 **super 整卷**做**重置/重刷**（官方原文"重置 Super"）。这是比 delete/create 更底层的一档：③推断其本质是 **`fastboot erase super` 后整 super 重刷 `fastboot flash super super.img`**，或刷一个**干净的/官方的 super 镜像**把卷结构、逻辑分区表一并恢复。

**回答任务里的关键子问题——Super 真死是否要 erase super / 整 super 重刷 / 仅重建逻辑？**

| 手段 | 作用层级 | 适用 | 判断依据 |
|---|---|---|---|
| 逐分区 delete/create（强力线刷） | 逻辑分区级 | super 结构还在，只是某分区坏了/COW 残留/空间异常 | ①官网"重新构建逻辑分区，修复 Super 异常" |
| **整 super 重刷 / erase super（修复 Super 真死）** | **super 卷级** | **强力线刷无效**（逐分区 delete/create 也救不回）、super 元数据损坏 | ①更新日志"强力线刷无效时可使用本功能**重置 Super**" |
| 仅重建逻辑分区 | 逻辑分区级 | 普通 COW/空间问题 | ③ |

→ **结论**：「修复 Super 真死」在官方口径里明确是**强力线刷（逐分区重建）失败后的下一档、对 super 做"重置"**，即**至少要整 super 层面的重写，而不只是重建逻辑分区**（③推断具体为 erase super + 整 super `flash super super.img`）。**注意**：`fastboot flash super super.img` 需要先合并散包 super（≥30GB 空间，②社区明说 `一加线刷分区.txt` §八/§九；官网教程也要求"剩余空间 > 30GB 解包"）。

---

## 4. 这些模式如何保证不"二次变砖"？（前置判断）

> 紫罗兰官方对"前置判断"披露很少，只能据官网零散文案 + Android 机制 + 社区笔记归纳，**以下多属③推断**，凡官方明说的单独标①。

### 4.1 官方明说的安全相关（①）
- **ARB 熔断检测**：识别设备/固件包是否熔断，"防止降级变砖"（官网首页）。
- **分区表校验**：比对刷机包底层分区与设备分区表，"预防刷错包导致黑砖；仅在刷写前进行安全检查，无任何风险"（更新日志）。
- **只读诊断不写盘**：OCDT 分析、"强开 OEM"等被放在 9008/基本刷入里作为只读或独立操作。

### 4.2 模式间的"降级保护"（①+③）
紫罗兰的设计逻辑（③推断，但由官方模式定义支撑）是**用模式层级来兜底**：
- 能进 BL 但 FBD 坏 → 先「修复 FBD」重刷引导链，**不要**直接去「仅 FBD」（进不去）。
- FBD 能进、逻辑分区坏 → 「强力线刷」逐分区重建。
- 强力线刷仍无效 → 「修复 Super 真死」整 super 重置。
- 只能进 FBD（BL 进不去）→ 「仅 FBD」，**绝不碰 bootloader 侧**（因为写 bootloader 才是真正会二次变砖的动作）。

### 4.3 应具备的前置判断（②社区明说 + ③推断，建议 Lrom 落地时显式化）
1. **当前在 BL 还是 FBD**：`fastboot getvar is-userspace`（yes=FBD）——决定走哪条模式（②笔记方法2）。
2. **能否进 FBD**：从 BL 发 `fastboot reboot fastboot` 能否进入（进不去→「修复 FBD」）。
3. **槽位**：`fastboot getvar current-slot`；逻辑分区**禁双槽**（②笔记 §八）。
4. **super 剩余空间**：`sum(逻辑分区 size) ≤ super_size`；不足则要么去掉双槽，要么整 super 重刷（③，Lrom 已有 preflight 校验）。
5. **ARB/型号匹配**：防降级变砖与刷错机型（①官网有，Lrom 已有 arb_db + check_device_match）。
6. **OCDT 只在必要时才碰**：仅 FBD 场景不写 OCDT（写了反而可能更砖）。

---

## 5. 对 Lrom 的落地建议（现有 `_enter_fastbootd + recreate_logical` 够不够？）

### 5.1 现状盘点（读码确认）

| 能力 | Lrom 现状 | 代码位置 |
|---|---|---|
| 进 FBD | `_enter_fastbootd()`：从 BL 发 `reboot fastboot` 轮询就绪，>15s 提示点 English | `flash.py:659-702` |
| **强制回 BL** | `run()` 第一步 `_ensure_fastboot()`，**在 FBD 时主动 `reboot bootloader`** | `flash.py:590-613` / `run():523-527` |
| 逻辑分区重建 | `_recreate_logical_partition()`：delete `_a/_b/_a-cow/_b-cow` + create `_a/_b` | `flash.py:416-452` |
| super 空间校验 | `preflight()` 算 `sum(logical) vs super_size`，超了报错提示去双槽 | `flash.py:161-183` |
| 模式 | 普通 / 强力(recreate) / 回锁；**无"仅 FBD"/"修复 FBD"/"整 super"** | `menu.py:20-47,873-913` |
| 逻辑分区禁双槽 | `_flash_other_slot()` 对 FASTBOOTD 直接 `return None` | `flash.py:466-483` |

### 5.2 覆盖度结论

- ✅ **「强力线刷」已完全覆盖**：`recreate_logical=True`（STRONG 模式）就是 delete/create 逐分区重建，与紫罗兰"强力线刷"语义一致。**这块不用改。**
- ⚠️ **「仅 FBD」未覆盖，且现有流程会主动破坏它**：`run()` 强制 `_ensure_fastboot()` 会把只在 FBD 的设备踢回 BL，然后失败。**这是最关键缺口。**
- ⚠️ **「修复 FBD」未覆盖**：无法在"进不了 FBD"时给出"重刷引导链→验 FBD"的专门路径。
- ⚠️ **「修复 Super 真死」未覆盖**：没有整 super 重刷 / `erase super` 的一档，`recreate_logical` 只到逻辑分区级，无法救"逐分区重建也失败"的 super 结构损坏。

### 5.3 具体落地建议（按优先级；均为建议，本报告不改源码）

**P0 — 让"仅 FBD"可行（最小改动，收益最大）**
- 在 `FlashExecutor.run()` 增加一个 `fbd_only: bool` 开关：为真时**跳过 `_ensure_fastboot()` 与整个 `Phase.FASTBOOT` 阶段**，直接校验"当前在 FBD"（`is_fastbootd()`），必要时 `adb reboot fastboot`，然后只跑 `_run_phase(Phase.FASTBOOTD)`。
- 这与"仅 FBD 适用于丢失 ocdt/进不去 BL"完全对应，且**避免踢掉唯一通道**。
- CLI/菜单加一个「仅刷逻辑分区（FastbootD）」入口（对应现有"只刷指定分区"思路，但限定 FBD）。

**P1 — 「修复 FBD」诊断/子流程**
- 增加"FBD 可达性探测"：从 BL `fastboot reboot fastboot` 后 `is_fastbootd()` 判定；失败则**提示并引导**用匹配全量包重刷引导链分区（boot/init_boot/vendor_boot/vbmeta 系列/recovery），再重试。
- 至少要先**打印诊断结论**（"检测到进不了 FastbootD，建议重刷引导链后重试"），把紫罗兰"模式即用途"的产品化意识变成 Lrom 的"诊断+建议"，而非新增并列模式（与 `violet-vs-lrom-flash.md` §8/§10 结论一致）。

**P1 — 「修复 Super 真死」整 super 重刷路径**
- 增一条显式"整 super 重刷"路径：`erase super`（或直接 `flash super`）→ 需**先合并散包 super.payload**（或支持用户指定已合并的 `super.img`）。
- **前置硬门槛**：合并/落盘需 ≥30GB 空间（官网教程要求）、`super.img` 大小 ≤ 目标 super 物理容量、ARB/型号必须匹配。
- 语义分档写进 help：普通(decreate 关) / 强力(decreate 开) / 整 super 重刷(=Super 真死)。让用户知道"逐分区重建失败再上升到整 super"。

**P2 — 把前置判断显式化（复用已有能力）**
- 已有 `is_fastbootd()`、`preflight()` 的 super 空间校验、`arb_db`、`check_device_match`——把它们在"模式判定"阶段串成**一次诊断**：在 BL 还是 FBD？FBD 能否进？槽位？super 空间够不够？ARB 是否熔断？→ 输出"建议走哪条路径"的建议，而不是让用户凭经验选。
- 保持 Lrom 的"宁可拒绝不可误判"哲学：整 super 重刷、erase super 等高危动作**必须二次确认 + preflight 全绿**才允许执行。

### 5.4 不建议做的
- ❌ 不建议照搬紫罗兰"4 个并列按钮模式"的交互：会重新引入"选错模式"风险。应把紫罗兰的**结果**（每类故障有明确对策）以 Lrom 的**诊断+建议+单一安全执行器**方式吸收。
- ❌ 不建议在"仅 FBD"路径里顺手写 bootloader 侧分区（那正是二次变砖的根源）。

---

## 6. 官方 / 社区 / 推断 的分界（逐条索引）

| # | 结论 | 类别 | 出处 |
|---|---|---|---|
| 1 | 仅FBD 适用"丢失 ocdt.img 导致进不了 Bootloader 的设备" | ①官方 | 官网首页 <https://violettool.top/> |
| 2 | 仅FBD 适用"格机/拆字库/换主板后缺少 Fastboot 分区的设备，正常设备无需启用" | ①官方 | 教程 <https://violettool.top/tutorials/oplus-flash/> |
| 3 | 仅FBD **跳过 bootloader 阶段、只刷 FBD 逻辑分区** | ①官方(行为)+③命令 | 官网首页/教程 + §2.2 推断 |
| 4 | 修复FBD 适用"卡 Bootloader、进不了 FastbootD 的设备" | ①官方 | 官网首页 |
| 5 | 无法进 FastbootD 用"与原系统版本匹配的全量包"修复 | ①官方 | 教程 |
| 6 | 修复FBD 可用散包（售后包模式）直接修 | ①官方 | 更新日志 <https://violettool.top/update.html> |
| 7 | 强力线刷 = "重新构建逻辑分区，修复 Super 异常" | ①官方 | 官网首页 |
| 8 | 强力线刷用于"`my_*` 分区刷写失败" | ①官方 | 教程 |
| 9 | 修复Super真死 = "强力线刷无效时可使用本功能**重置 Super**" | ①官方 | 更新日志 v3.7 |
| 10 | Super真死需整 super 重置（erase/flash super），**不止重建逻辑分区** | ③推断 | 由 #9 + 机制推断 |
| 11 | 逻辑分区必须 FBD 刷、禁双槽、`--slot=all` 刷 super 会失败要求 `-w` | ②社区 | `一加线刷分区.txt` §八 |
| 12 | OCDT 坏了进不了 BL、用别机 OCDT 进不了 BL、售后也难救 | ②社区 | `一加线刷分区.txt` §三/§五 |
| 13 | "进不去 Fastboot ≠ 变砖，能进 FBD 就有救" | ②社区 | `注意事项.txt` §七 |
| 14 | 进 FBD 用 `fastboot reboot fastboot`；`getvar is-userspace=yes` 判 FBD | ②社区 | `一加线刷分区.txt` §六/方法2 |
| 15 | `-w` 清数据；合并 super 需 ≥30GB | ①官方+②社区 | 教程 + `一加线刷分区.txt` §九 |
| 16 | 具体 fastboot 命令逐条序列（delete/create/flash/set-active/erase super） | ③推断 | 据 Android FastbootD 机制与工具资源（`bin/platform-tools-windows/fastboot.exe`）推断，**非源码实证** |

> **可信度边界**：紫罗兰内部实现无法反编译（`.rsrc` 满熵，见 `violet-vs-lrom-flash.md` §0）。除官方文案字面可查的描述外，命令序列均为按 Android FastbootD 机制推断，落地时需以实机验证为准，不应视为紫罗兰官方实现细节。

---

## 7. 参考资料

- 紫罗兰官网首页（核心刷写 · 12 模式定义）：<https://violettool.top/>
- 紫罗兰欧加刷机教程：<https://violettool.top/tutorials/oplus-flash/>
- 紫罗兰疑难杂症页（强力线刷修分区失败 / 900E 黑砖）：<https://violettool.top/trouble/trouble2.html>
- 紫罗兰更新日志（修复 Super 真死 / 售后包接入修复 FBD）：<https://violettool.top/update.html>
- 紫罗兰基础刷入教程（FastbootD 下擦 FRP 等）：<https://violettool.top/tutorials/basic-flash.html>
- 本地社区笔记：`$HOME/桌面/一加线刷分区.txt`、`$HOME/桌面/注意事项.txt`
- 本仓库既有对比报告：`docs/research/violet-vs-lrom-flash.md`（§1/§3/§8）
- Lrom 现有实现：`src/lrom/core/flash.py`（`_ensure_fastboot` / `_enter_fastbootd` / `_recreate_logical_partition` / `run`）、`src/lrom/core/plan.py`、`src/lrom/tui/menu.py`（`FlashMode`）
