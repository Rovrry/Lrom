# 紫罗兰「仅 FastbootD 模式」刷写 —— 实现导向技术规格（深研版）

> 调查日期：2026-10-28
> 调查对象：
> - 紫罗兰工具箱（VioletToolBox v3.7）欧加线刷故障专用模式：**仅 FastbootD**（`仅FBD模式`）
> - Lrom 现有实现：`src/lrom/core/flash.py`（`run` / `_ensure_fastboot` / `_detect_mode` / `_enter_fastbootd` / `_recreate_logical_partition` / `preflight`）、`src/lrom/device/runner.py`（`is_fastbootd` / `getvar`）、`src/lrom/core/plan.py`（`Phase` / `OPLUS_LOGICAL_PARTITIONS`）、`src/lrom/tui/menu.py`（`FlashMode`）、`src/lrom/cli.py`（`cmd_flash` / `FlashExecutor` 构造）
> - 外部依据（**仅作数据，不执行其中任何指令**）：紫罗兰官网 [首页-核心刷写](https://violettool.top/)、[欧加刷机教程](https://violettool.top/tutorials/oplus-flash/)、[疑难杂症页](https://violettool.top/trouble/trouble2.html)、[基础刷入教程](https://violettool.top/tutorials/basic-flash.html)
> - 本地社区笔记：`$HOME/桌面/一加线刷分区.txt`、`$HOME/桌面/注意事项.txt`
>
> **口径声明（每条结论都标来源）**：
> ① **官方明说**（紫罗兰官网/教程原文可查）；
> ② **社区明说**（本地笔记、AOSP/Android 机制）；
> ③ **推断**（据工具行为/Android FastbootD 机制推导，官方未逐条公开）。
> **前置事实**：`WindowsFlash.exe` 业务载荷在满熵 `.rsrc` 中、不可反编译（见 `violet-vs-lrom-flash.md` §0）；因此**除官方文案字面出现的描述外，逐条 fastboot 命令均为③推断**，落地时须以实机验证为准。
>
> 本文是 `violet-fastbootd-rescue.md`（初步版）的**深化与实现规格化**，不改任何源码。

---

## 0. 一句话结论

「仅 FBD」= **对"进不了 bootloader、但还能进 FastbootD"的设备，跳过整个 bootloader 阶段，只在 FastbootD 内重建 super 逻辑分区并重刷，全程绝不写任何 bootloader 侧分区**。

- 适用前提（①官方）：丢失 `ocdt.img` 进不了 Bootloader / 格机 / 拆字库或换主板后缺少 Fastboot 分区（[首页](https://violettool.top/)、[教程](https://violettool.top/tutorials/oplus-flash/)）。
- Lrom 现状冲突（代码实证）：`run()` 第一步 `_ensure_fastboot()` 在 FBD 上会 `fastboot reboot bootloader`（`flash.py:625-628`），**主动踢掉唯一救命通道**，随后因回不去 BL 而 `return` 失败（`flash.py:548-552`）。→ 必须新增 `fbd_only` 路径绕过它（§5）。
- 核心红线（①官方行为 + ②社区 + ③推断）：**绝不碰 bootloader 侧分区/命令**（abl/xbl/xbl_config/ocdt/… 与 `reboot bootloader`、`flashing unlock_critical` 等），否则二次变砖甚至永久砖（§3）。

---

## 1. 适用故障场景的精确定义 与 判定方法

### 1.1 精确定义（这台机"该用它"的充要条件）

定义为三条件同时成立（③推断，由官方适用描述 + Android 引导链机制铺陈）：

1. **能进 FastbootD**（FBD 可达）：`fastboot getvar is-userspace` 返回 `yes`（②社区：`一加线刷分区.txt` §六 方法2）。
2. **进不了 / 无法稳定处于 Bootloader**（BL 不可用）：这是关键区分。表现为以下任一（②社区，`注意事项.txt` §七 / `一加线刷分区.txt` §三§五）：
   - 丢失/损坏 OCDT（`engineering_cdt`）→ 用非本机 OCDT 或损坏后"直接进不了 Fastboot"；
   - 拆字库 / 换主板后"缺少 Fastboot 分区"（①官方适用语：`教程`）；
   - 卡在 Bootloader、`fastboot reboot bootloader` 后设备不再出现（无法维持 BL 会话）。
3. **需要刷的正是 super 内的逻辑分区**（系统/app 层坏了要救回），而**不需要也不应该**动 bootloader 侧。

> 与相邻模式划界（①官方定义，[首页](https://violettool.top/)）：
> - **仅FBD**：进不了 Bootloader（BL 死、FBD 活）→ 跳过 BL 只刷逻辑分区。
> - **修复FBD**：卡 Bootloader、进不了 FastbootD（BL 活、FBD 死）→ 用匹配全量包重建 FBD 环境（会写引导链）。
> - **强力线刷**：BL+FBD 都能用，只是逻辑分区刷写失败/super 异常 → delete/create 重建。
> - 二者方向相反：**"仅FBD"绝不能去修 BL（会写 bootloader 侧）；"修复FBD"才写引导链。选错 = 二次变砖。**

### 1.2 Lrom 如何用现有能力判定「这台机只能进 FBD、进不了 BL」

用**现有 API 组合**即可实现，无需新协议。判定顺序（③推断的工程编排，底层能力均实测可用）：

```text
# A. adb 侧是否活着（能进系统）
adb devices                       # AdbRunner.devices()  -> 有 "device" 即系统可用
  └─ 系统可用时：adb reboot fastboot 进 FBD（不是必须走"仅FBD"，但可用 FBD 目标）

# B. fastboot 侧是否枚举到设备
fastboot devices                  # FastbootRunner.devices() -> 返回 serial 列表

# C. 枚举到了，判断它在 FBD 还是 BL（决定性）
fastboot getvar is-userspace      # FastbootRunner.is_fastbootd()：yes=FBD / no=BL
  ├─ yes  -> 当前在 FastbootD（fbd_only 可直接开工）
  └─ no   -> 当前在 Bootloader

# D. 若在 BL，尝试能否进 FBD（探 FBD 可达性）
fastboot reboot fastboot
fastboot getvar is-userspace      # 轮询：变 yes = FBD 可达
  ├─ 变 yes -> FBD 可达（可走 fbd_only，也可走普通流程；BL 活着说明其实不需要 fbd_only）
  └─ 超时/设备消失 -> FBD 也进不去（不属"仅FBD"，应走"修复FBD"/EDL，需人工介入）

# E. 确认它到底是"能进 BL 但 FBD 坏"还是"BL 坏 FBD 活"
#    —— fbd_only 的判定落点：
#    设备当前已在 FBD（C=yes）  --且--  尝试回 BL 失败/用户声明 BL 进不去
#    => 判定为"只能进 FBD"场景 => 走 fbd_only
```

**判定逻辑函数化（建议，③推断的实现口径）**，可放在诊断层：

```text
probe_device_mode() 返回枚举：
  ADB / FASTBOOT(BL) / FASTBOOTD(FBD) / NONE
  + can_enter_bl: bool（从 FBD 试 reboot bootloader 是否还能回来；代价高，需用户授权才做）
  + can_enter_fbd: bool（从 BL 试 reboot fastboot 是否能进）

fbd_only 适用判定：
  当前==FASTBOOTD 且（用户明确"进不了 BL" 或 can_enter_bl==False）
  → 推荐 fbd_only，并**禁止**任何 reboot bootloader / 写 BL 侧动作
```

> 关键安全点（③推断 + ②机制）：**"仅FBD"模式下不要去"验证能否回 BL"而真的 `reboot bootloader`**——一旦 BL 坏，回不来就彻底失联。**判定"BL 进不去"应主要依据用户输入/历史状态**，而不是真的发 `reboot bootloader` 去试。这与 Lrom 现有 `_ensure_fastboot` 恰好相反（它是主动踢回 BL），必须绕开。

### 1.3 Lrom 现有能力的可用/不可用处（读码确认）

| 能力 | 可用性 | 代码位置 |
|---|---|---|
| adb 探测 | ✅ `AdbRunner.devices()` 返回 `{serial: state}` | `runner.py:248+` |
| fastboot 探测 | ✅ `FastbootRunner.devices()` 返回 serial 列表 | `runner.py:~190` |
| FBD 判定 | ✅ `is_fastbootd()` 用 `getvar is-userspace` ∈ {yes} | `runner.py:205-209` |
| product/product_name 判定 | ✅ `getvar(name)` 通用读取 | `runner.py:200-203` |
| current-slot 判定 | ✅ `getvar("current-slot")` | 同上 |
| **检测"BL 进不去"** | ⚠️ 现无专门能力，且 `_detect_mode()` 一旦 FBD 即返回 `"fastbootd"` 不探 BL | `flash.py:640-655` |
| **会踢回 BL** | ❌ `_ensure_fastboot()` 主动破坏 FBD | `flash.py:615-638` |

---

## 2. 完整 fastboot 命令序列（逐步）

> ⚠️ 紫罗兰**只公开了"仅刷 FBD 阶段"的行为描述，未公布命令**（①）。下列命令骨架 = **官方行为（①）** + **Android FastbootD/LP 动态分区机制与工具资源（③推断）** + **社区笔记约束（②）**。落地以实机抓日志复核。

### 2.1 阶段 0：确认在 FBD（前置门）

```text
fastboot devices                       # 必须枚举到设备（否则是驱动/线材问题，不是本模式）
fastboot getvar is-userspace           # 必须 yes；若 no（在 BL）→ 说明不属"仅FBD"场景，停手走他路
```

- ①官方把"仅FBD"限定为"进不了 Bootloader"的设备；②`getvar is-userspace=yes` 是判定 FBD 的标准方法（`一加线刷分区.txt` §六 方法2）。
- **不在 FBD 就不要强行前进**：若当前在 BL，说明 BL 是活的，应走普通/强力线刷，而不是"仅FBD"（③推断）。

### 2.2 阶段 1：只读诊断（不写盘）

```text
fastboot getvar current-slot           # 当前槽 a/b
fastboot getvar slot-count             # 通常 2
fastboot getvar product                # 设备代号（型号匹配校验，绝不写盘）
fastboot getvar super-partition-size   # 或 getvar all 读 super（厂商可能屏蔽，②§六）
```

- 这些为只读（②`一加线刷分区.txt` §六 方法1/2）。
- **`getvar super-partition-size` 不一定 100% 可用**（厂商可能屏蔽）；取不到时用 `getvar all` 搜 `partition-size:super`，或退化为"按镜像总和 ≤ 已知 super 容量（一加12≈16GB）"的经验校验（③）。

### 2.3 阶段 2：逻辑分区重建（等价"强力线刷"的 delete/create，只对目标槽）

> Lrom 现有 `_recreate_logical_partition()` 已是"删两槽+COW、只重建目标槽"的正确姿势（`flash.py:429-476`），fbd_only 直接复用即可（§5）。

```text
# 对每个逻辑分区 base（system/system_ext/product/vendor/odm/*_dlkm/my_*）：
fastboot delete-logical-partition {base}_a
fastboot delete-logical-partition {base}_b
fastboot delete-logical-partition {base}_a-cow      # 清 COW 残留，释放 super 空间
fastboot delete-logical-partition {base}_b-cow
fastboot create-logical-partition {base}_<当前槽> <size_blocks>   # blocks = op.size / 4096
```

- **顺序**：先 `delete`（含 `-cow`）再 `create`；**只 create 目标槽**（③+②：super 单物理分区，两槽 create 会翻倍触发 `Not enough space`，见 `violet-slot-and-logical-strategy.md` §2/建议3，`flash.py:435-440` 已按此实现）。
- **是否 create 另一槽**：不建（③）。Lrom 已如此（`flash.py:468-470` 只 create target）。
- **`delete` 不存在的分区会报错**：忽略（Lrom 已忽略，`flash.py:463-465`）。

### 2.4 阶段 3：逐逻辑分区刷写（**只逻辑分区**）

```text
fastboot flash system        system.img
fastboot flash system_ext    system_ext.img
fastboot flash product       product.img
fastboot flash vendor        vendor.img
fastboot flash system_dlkm   system_dlkm.img
fastboot flash vendor_dlkm   vendor_dlkm.img
fastboot flash odm           odm.img
fastboot flash odm_dlkm      odm_dlkm.img
fastboot flash my_product    my_product.img
fastboot flash my_manifest   my_manifest.img
# …其余 my_*（my_stock/my_heytap/...）按需
```

- 这些分区名与"必须 FastbootD 刷"的归属：②`一加线刷分区.txt` §二-B；Lrom 用 `OPLUS_LOGICAL_PARTITIONS` 枚举 18 个归 `Phase.FASTBOOTD`（`plan.py:118,226-227`）。
- **刷哪些**：**以 `plan.by_phase(Phase.FASTBOOTD)` 为准**，即分包里属于 super 的所有逻辑分区（③工程口径，避免漏刷/多刷）。
- **是否也刷物理分区（boot/init_boot/vendor_boot 等）？**
  - **"仅FBD"模式下：不刷物理分区。** 原因：本模式的存在前提就是"进不了 BL"，而物理分区（boot/init_boot/vendor_boot/vbmeta…）**只能在 bootloader 模式刷**（②`一加线刷分区.txt` §二-A 明确标注 BL 模式刷；`教程`说仅FBD 适用"缺少 Fastboot 分区"的设备）。在 FBD 里对这些分区执行 `fastboot flash` 会失败或行为未定义（③）。
  - **例外（③推断，需人工评估）**：②`注意事项.txt` §七 记录社区有一种"在 fastbootd 下刷回旧版 abl.img"来恢复 Fastboot 的偏门做法——**这不是"仅FBD"的标准动作，且属于写 bootloader 侧，与本文红线冲突，不纳入"仅FBD"自动流程**；如需做须用户显式知情并单独决策。

### 2.5 阶段 4：槽位与收尾

```text
# 是否 set-active？
fastboot --set-active=<槽>      # 一般**沿用原槽，不切槽**（③：fbd_only 语义是"救活原系统"，非换槽）
# 是否 -w 清数据？
fastboot -w                     # 格机/换主板场景通常需要（③推断 + ①"格机…适用于"）
fastboot reboot                 # FBD 内 reboot 回系统（②/③；不要用 reboot bootloader）
```

- **set-active**：③推断——"仅FBD"是救活原系统/原槽，**默认不切槽**（与"AB 通刷/回锁"刻意切槽不同；见 `violet-slot-and-logical-strategy.md`）。保留原 `current-slot`。
- **`-w`**：①官方适用语含"格机"，救砖场景常需清数据；是否清取决于用户是否要保留数据（③）。Lrom 已有 `--wipe`/`wipe_data`，由用户显式选（`cli.py`/`flash.py:589-593`）。
- **reboot 用 `fastboot reboot`（回系统）**，**不用 `reboot bootloader`**（红线，§3）。
- **`fastboot reboot fastboot` 不在本流程出现**（本就在 FBD，不需要切回 FBD；与 Lrom `_enter_fastbootd()` 的"从 BL 发 reboot fastboot"相反，见 §5）。

### 2.6 命令来源标注汇总

| 命令/动作 | 来源 |
|---|---|
| `getvar is-userspace` 判 FBD | ②`一加线刷分区.txt` §六方法2 |
| `getvar current-slot/product` 只读诊断 | ②`一加线刷分区.txt` §六方法1 |
| `delete-logical-partition` / `create-logical-partition` 重建（含 `-cow`） | ①官方"重新构建逻辑分区" + ③机制；②§八 COW/尺寸近似 |
| 只 create 目标槽、不双槽 | ②§八 + ③；Lrom 已实现 |
| `flash <逻辑分区>` 逐分区 | ②`一加线刷分区.txt` §二-B + ③ |
| 不刷物理分区（boot/init_boot…） | ③推断（物理分区只能 BL 刷，②§二-A） |
| 不切槽 / 保留原槽 | ③推断（对比 AB 通刷语义） |
| `-w`、`fastboot reboot` 收尾 | ①适用语 + ②/③ |
| 具体命令逐条序列本身 | **③推断（非紫罗兰源码实证）** |

---

## 3. 「绝不碰 bootloader 侧」红线 —— 具体指哪些分区/命令，为何碰了会二次变砖

> 本节是"仅FBD"的**最高优先级安全约束**。违反即可能把"还能救"的机器变成永久砖。
> 依据：①官方"仅FBD 适用于进不了 Bootloader 的设备"（即这些设备本就没有可用的 bootloader 侧，强写=向真空写入/写错）；②`一加线刷分区.txt` §三"六个红线分区"、`注意事项.txt` §五/§七；③机制推断。

### 3.1 红线分区（①不可写/②极高风险）

| 分区 | 说明 | 触碰后果 | 来源 |
|---|---|---|---|
| **engineering_cdt（OCDT）** | 决定能否进 Fastboot 的机独有校准分区 | 用非本机的/写错 → **直接进不了 Fastboot**，损坏只能拆字库，售后也救不了 | ②`一加线刷分区.txt` §三/§五；`注意事项.txt` §六 |
| **abl / xbl / xbl_config** | 引导链三件套（xbl_config 含 ARB 字段） | 刷错 → 黑砖，可能进不了 Fastboot、进不了 EDL；`xbl_config` 写错还可能触发 ARB 熔断 | ②`一加线刷分区.txt` §三、`注意事项.txt` §一 |
| **vbmeta / vbmeta_system / vbmeta_vendor** | AVB 校验顶层 | 版本不匹配 → 开机秒断电循环（bootloop） | ②`一加线刷分区.txt` §三 |
| **persist** | 指纹/传感器/IMEI 校准/工厂标定 | 刷坏不可逆，只能同型号同版本备份回写 | ②`一加线刷分区.txt` §三 |
| **modem** | 基带固件（分槽 modem_a/b） | 刷错无信号/IMEI 异常 | ②`一加线刷分区.txt` §三 |
| **sbl1 / aboot**（旧平台） | 老骁龙次级引导 | 硬砖 | ②`一加线刷分区.txt` §三 |
| **frp** | 恢复出厂保护 | 勿乱动 | ②`一加线刷分区.txt` §二-D |

### 3.2 红线命令（③推断 + ②机制，为何二次变砖）

| 命令 | 为什么红线 |
|---|---|
| `fastboot reboot bootloader` | 在 BL 已坏/进不去的设备上，**这是把唯一活着的 FBD 踢掉**、且回不来 → 设备失联（本模式最致命的误操作；Lrom `_ensure_fastboot` 现正犯此错，§5） |
| `fastboot flash abl/xbl/xbl_config/vbmeta*` | 设备缺 Fastboot 分区/OCDT 坏，写引导链=向坏掉的引导链再写错版本 → 黑砖/ARB 熔断/bootloop（§3.1） |
| `fastboot flashing unlock_critical` | ②`注意事项.txt` §五：manpage 原文"Enables flashing bootloader-related partitions"——**需要它就等于你要动 bootloader 本身 = 应该停手**。①一加正常场景永不需它 |
| `fastboot erase super` / `flash super super.img`（在"仅FBD"里） | 整 super 重量级重刷属"修复 Super 真死"一档，且需先合并 ≥30GB super 包；在"仅FBD"里贸然整 super 覆盖风险高于逐分区重建（③；且本模式目标是**重建逻辑分区救系统**，非整卷重置） |
| `fastboot --set-active` 到一个空槽 | 若目标槽系统不完整，切过去=引导到空槽 → 无法启动（③） |
| `fastboot -w`（非必要时机） | 会擦 userdata/metadata/cache；只在确需清数据时做（③） |

### 3.3 为什么"碰 bootloader 侧"会二次变砖（机制，③推断）

"仅FBD"设备的共同点：**bootloader 侧本就不完整/不匹配**（OCDT 丢、缺 Fastboot 分区、拆字库换主板）。此时：
1. bootloader 链（abl/xbl）没有可用的、能自洽运行的组合；写一个"看似正确"的分区，往往只补了链条一环，**拼不出能启动的完整链** → 要么黑砖、要么 ARB/bootloop。
2. `xbl_config` 里是 **ARB 防回滚字段**：写错版本触发熔断 = **永久失去降级/EDL 通道**（②`注意事项.txt` §一）。
3. OCDT 是**机独有**：用别机/写坏 → 彻底进不了 Fastboot，且 EDL 若已熔断则成永久砖（②：`注意事项.txt` §七"真正的永久砖：无 Fastboot/无系统/进不了 FastbootD/且 EDL 已熔断"）。
→ 结论：**"仅FBD"下只做 super 内的逻辑分区，bootloader 侧一律"只读不写"**（读用 `getvar`）。

---

## 4. 前置安全校验清单（防二次变砖）

刷前必须逐条通过，任一硬门槛不过 → **不刷**（复用 Lrom 已有能力优先，③推断的工程编排）。

| # | 校验项 | 判定方法 | 不通过时 | 来源 |
|---|---|---|---|---|
| 1 | **FBD 可达性** | `getvar is-userspace` == `yes`（`is_fastbootd()`） | 不在 FBD → 不是"仅FBD"场景，停手（走普通/强力/修复FBD） | ②§六 |
| 2 | **设备枚举/线材** | `fastboot devices` 非空、稳定 | 换线/换口/驱动；不刷 | ②`注意事项.txt` §五 |
| 3 | **型号匹配（ARB/机型）** | `getvar product`/`product_name` 与包 `pre-device` 一致；ARB 值不高于设备允许 | 型号不符=BLOCKER；ARB 熔断=硬阻断 | ②§八+①官网"分区表校验/ARB熔断检测"；Lrom `check_device_match`/`arb_db` |
| 4 | **super 空间** | `sum(逻辑分区 size) ≤ super 容量`；recreate 时按"目标槽单份"估（Lrom 已单槽） | 不足 → 去掉多余分区/清 COW/或转"修复Super真死"；不硬刷 | ③ + ②§八；Lrom `preflight` `flash.py:161-183` |
| 5 | **槽位明确** | `getvar current-slot`；确认刷的就是这个槽（不切槽） | 槽位不明→打印实测值让用户确认 | ②§八；`violet-slot-and-logical-strategy.md` |
| 6 | **OCDT 处理（只读/不写）** | 检测包里是否含 `ocdt/engineering_cdt`；若含→**在 fbd_only 中强制排除、不刷** | 若有则提示"仅FBD 不写 OCDT"并跳过 | ②§三§五；Lrom `preflight` OCDT warn（`flash.py:135-150`） |
| 7 | **首次失败退出码为"未找到槽位信息"** | 该报错是连线不稳导致，非分区坏 | 重连后重试，不乱刷 | ①[疑难杂症页](https://violettool.top/trouble/trouble2.html) 情况一 |
| 8 | **镜像完整性** | 镜像存在/非空、包结构自洽 | 缺镜像=error 不刷 | Lrom `preflight` `flash.py:105-123` + `package.py` |
| 9 | **红线分区零命中** | 计划里不得含 bootloader 侧分区（§3.1） | 命中→BLOCK | ③+②（§3） |
| 10 | **EDL 熔断风险** | 命中 9008 熔断库 → 硬阻断 | 阻断 | Lrom `_warn_edl_risk`（②§一） |

> fbd_only 特有的**硬前置**（③推断，进 fbd_only 前必须做的专属校验）：
> - `is_userspace==yes` 是**进入 fbd_only 的准入闸**，不是可选项；
> - **计划中 Phase.FASTBOOT（物理/bootloader 侧）必须为空**（或全部列黑名单跳过），否则拒绝执行；
> - **不允许出现任何 `reboot bootloader` 动作**。

---

## 5. 与 Lrom 现有 `run()` 的冲突点 与 `fbd_only` 改造方案（只给方案，不改码）

### 5.1 冲突点（读码实证）

| 冲突 | 位置 | 后果 |
|---|---|---|
| `run()` 第一步强制 `_ensure_fastboot()` | `flash.py:548-552` | FBD 设备被踢回 BL |
| `_ensure_fastboot()` 在 FBD 时 `reboot bootloader` | `flash.py:625-628` | **主动破坏唯一通道**，且回不来 → 中止失败 |
| `_detect_mode()` 一旦 FBD 返回 `"fastbootd"`，不区分"该不该回 BL" | `flash.py:640-655` | 无条件倾向 BL |
| `run()` 无条件先跑 `Phase.FASTBOOT` | `flash.py:565-570` | "仅FBD"不应刷物理分区 |
| `_enter_fastbootd()` 从 BL 发 `reboot fastboot` | `flash.py:684-692` | "仅FBD"本就在 FBD，不需要这步 |

### 5.2 建议的函数级改法（方案，不落地）

**核心思路**：加一个**并行入口路径 `fbd_only`**，让 `run()` 在 `fbd_only=True` 时**完全跳过 BL 阶段与 `_ensure_fastboot`**，只走 FBD 分支。**不改默认行为**（默认仍走现有全量流程），风险隔离。

#### (a) `FlashExecutor.__init__` 增加开关

```text
新增参数:  fbd_only: bool = False     # 存 self.fbd_only
（沿用现有 dry_run / recreate_logical / wipe_data / target_slot 等，不删任何）
```

#### (b) `run()` 顶部加分支（最小侵入）

```text
def run(self):
    self.result.started_at = time.time()

    if self.fbd_only and not self.dry_run:
        return self._run_fbd_only()          # 新路径，完全不碰 BL
    if self.dry_run and self.fbd_only:
        return self._dry_run_fbd_only()      # 预演：只报告逻辑分区

    ...原有全量流程不变...
```

#### (c) 新增 `_run_fbd_only()`（核心，只做 FBD 阶段）

```text
def _run_fbd_only(self):
    # 1) 准入门：必须在 FBD，否则拒绝（绝不 reboot bootloader 去"试"）
    if not self._check_fbd_reachable():      # 用 self.fb.devices()+is_fastbootd()
        self.result.error = ("当前不在 FastbootD（getvar is-userspace != yes）。"
                             "本模式仅用于进不了 Bootloader、只能进 FastbootD 的设备；"
                             "请勿在 Bootloader 模式使用。已中止，未做任何写入。")
        return self.result
    #   （若在 ADB 系统内：可选先 adb reboot fastboot；若在 BL：直接拒绝，不踢、不写）

    # 2) 红线自检：计划里不得有 bootloader 侧（Phase.FASTBOOT）真实写入
    if self._has_bootloader_partitions():
        self.result.error = "fbd_only 计划包含 bootloader 侧分区，已拒绝（防二次变砖）。"
        return self.result

    # 3) 只读诊断：current-slot / product / super-size 打印（不写盘）
    self._fbd_preflight_readonly()

    # 4) 只跑 FastbootD 阶段（复用现有 _run_phase(Phase.FASTBOOTD)）
    self._notify_mode(Phase.FASTBOOTD)
    if not self._run_phase(Phase.FASTBOOTD):
        self.result.error = "FastbootD 阶段失败，已中止"
        return self.result

    # 5) 可选清数据（用户显式选，且放最后）
    if self.wipe_data:
        if not self._wipe_userdata():
            self.result.error = "清空用户数据失败，已中止"
            return self.result

    self.result.finished_at = time.time()
    return self.result
```

#### (d) 新增判定/自检辅助函数（复用已有能力）

```text
def _check_fbd_reachable(self) -> bool:
    # 复用 runner：devices() 非空 且 is_fastbootd()==True
    if self.fb is None: return False
    if not self.fb.devices(): return False
    return self.fb.is_fastbootd()          # runner.py:205-209 现成

def _has_bootloader_partitions(self) -> bool:
    # 只看 Phase.FASTBOOT 的真实写入（BLOCKED/BLOCK 风险已跳过的不算）
    return any(op.phase is Phase.FASTBOOT
               and op.status 不为 SKIP/BLOCK
               for op in self.plan.active_ops)   # 具体以 plan 字段为准

def _fbd_preflight_readonly(self):
    slot   = self.fb.getvar("current-slot")    # runner.py:200-203
    product= self.fb.getvar("product")
    super_sz = self.fb.getvar("super-partition-size")   # 可能 None
    # 打印/回写 result，不写设备；与 preflight() 的 super 空间校验联动
```

#### (e) 入口接通（菜单 / CLI，建议）

```text
· menu.py 加一个入口「仅刷逻辑分区（仅 FastbootD，救砖）」→ 走 _flow_flash(..., fbd_only=True)
  - 进入前先做一次 probe（is_userspace），非 FBD 直接拒绝并提示；
  - 该路径下强制：mode 固定为"重建逻辑分区开（recreate_logical=True）"，不选槽、不切槽、默认不 wipe（用户勾）。
· cli.py cmd_flash 增加 --fbd-only → FlashExecutor(..., fbd_only=True)
  - 与 --dry-run 组合出预演（只报 Phase.FASTBOOTD）；
  - 与现有 --no-recreate-logical 互斥检查：fbd_only 更宜 recreate=True。
```

#### (f) 建议的语义约束（安全默认，③推断）

- `fbd_only=True` 时**强制忽略/拒绝** `target_slot`（不切槽）、**拒绝** `both_slots`（逻辑分区禁双槽，`flash.py:503` 已 `return None`，再加显式拒绝）。
- `fbd_only=True` 时 **不调用** `_ensure_fastboot()` / `_enter_fastbootd()` / `_set_active_slot()`。
- `dry_run` 下 fbd_only 只输出 Phase.FASTBOOTD 的 dry-run 报告（`run()` 不写设备）。

### 5.3 改动面清单（便于评审）

| 文件 | 改动 | 侵入性 |
|---|---|---|
| `src/lrom/core/flash.py` | `__init__` 加 `fbd_only`；`run()` 顶部分支；新增 `_run_fbd_only`/`_check_fbd_reachable`/`_has_bootloader_partitions`/`_fbd_preflight_readonly`/`_dry_run_fbd_only` | 中（新增为主，默认路径不动） |
| `src/lrom/tui/menu.py` | 新入口 + `_flow_flash(..., fbd_only=True)` | 低 |
| `src/lrom/cli.py` | `cmd_flash` 加 `--fbd-only` 参数并透传 `FlashExecutor(fbd_only=...)` | 低 |
| `src/lrom/core/plan.py` | 无需改（`Phase.FASTBOOTD` / `OPLUS_LOGICAL_PARTITIONS` 现成） | 无 |

> **本报告不改任何源码**，以上仅为可落地方案。

---

## 6. 失败回退：FBD 里刷到一半失败怎么办？

> 依据：①官方"分区刷写失败→强力线刷重刷"（[疑难杂症页](https://violettool.top/trouble/trouble2.html) 情况一）；②`一加线刷分区.txt` §八（size/COW 约束）、`注意事项.txt` §七（"能进 FBD 就有救"）；③推断的工程口径。

### 6.1 先判失败类型（③推断）

| 报错特征 | 含义 | 处理 |
|---|---|---|
| `未找到槽位信息` / device not found | **连线不稳**，非分区坏 | 重连/换线/换口，重试；别乱刷（①情况一） |
| `Not enough space to resize partition` | 未重建直接覆盖、镜像变大、COW 残留 | **改重建**（delete/create 释放空间）后重刷；或清 `-cow`（②§八） |
| `FAILED (remote: 'Flashing is not allowed for Critical Partitions.')` | 命中关键分区锁（多发生在物理分区；fbd_only 本不该碰） | fbd_only 下应直接不碰；如强行要动=越红线，停手（③） |
| 设备从 fastboot 消失 | 线材/掉电/USB 重置 | 重插、`fastboot devices` 重新枚举，设备大概率仍在（FBD 掉线可回，别慌） |

### 6.2 可否"退回"？

- **能退回**（③推断）：FBD 是用户空间 fastboot，**失败时 device 通常仍在 FBD**（不像写 BL 那样一错就黑）。可**原地重试**：
  ```text
  fastboot devices                    # 还在？
  fastboot getvar is-userspace        # 仍 yes 就还在 FBD，可继续
  ```
- **绝不能"退回 BL"**——在"仅FBD"设备上 `reboot bootloader` 会失联（§3.2）。**"退回"指退回 FBD 重来，不是退回 BL。**

### 6.3 恢复命令（按场景，③推断 + 对应修复动作）

```text
# 场景 A：resize 空间不足（最常见）
fastboot delete-logical-partition {base}_a
fastboot delete-logical-partition {base}_a-cow
fastboot delete-logical-partition {base}_b-cow      # 释放 COW
fastboot create-logical-partition {base}_<槽> <blocks>   # blocks = img字节/4096
fastboot flash {base} {base}.img

# 场景 B：某分区 create 失败
fastboot getvar is-userspace        # 确认还在 FBD
fastboot delete-logical-partition {base}_<槽>       # 清理半成品
fastboot create-logical-partition {base}_<槽> <blocks>
fastboot flash {base} {base}.img

# 场景 C：设备掉线（fastboot 消失）
# 重插线/换口 → fastboot devices 重新枚举 → 从该分区的 delete/create 重来
# （FBD 掉线可回；不要 reboot bootloader）

# 场景 D：逐分区重建仍失败 / 疑似 super 结构坏
#   → 升级到"修复 Super 真死"档（整 super 重置/重刷，需先合并 ≥30GB super 包）
#   → 或走「修复FBD」重刷引导链（但那属于另一模式，会写 bootloader 侧，需用户显式知情）
```

- 官方对应（①）：逻辑分区刷写失败 → 重新进 Fastboot → **强力线刷模式重刷**（疑难杂症页 情况一）。即 **fbd_only 的重试语义 = 在 FBD 内以"重建"重刷**。
- Lrom 已有 `recovery_hint` 机制（`flash.py:208-215, 478-487`）可在失败 Step 上挂可复制命令，直接复用扩展。

### 6.4 何时"宣告需要人工/EDL"

- FBD 也进不去 + 系统也起不来 + 只能看 9008/900E → 已超出"仅FBD"能力，属"修复FBD/EDL 救砖"范畴（①官网模式分类；②§七）。**此时不应在 fbd_only 里硬撑**，应中止并给出升级建议（③）。

---

## 7. 与 Lrom 的落地要点速查（浓缩）

1. **判定**：`is_userspace==yes` + 用户/历史表明 BL 进不去 → fbd_only。**不在 FBD 不发任何 reboot bootloader 去"试"。**
2. **只刷** Phase.FASTBOOTD（逻辑分区），delete/create 只建目标槽、清 `-cow`，逐分区 `flash`。
3. **红线**：不写 abl/xbl/xbl_config/ocdt/vbmeta*/persist/modem；不发 `reboot bootloader`、`flashing unlock_critical`、整 `erase super`（除非升级到 Super 真死档）。
4. **前置**：FBD 可达、型号/ARB 匹配、super 空间够、槽位明确、计划无 BL 侧分区、EDL 未熔断。
5. **run() 改造**：加 `fbd_only` 开关 + `_run_fbd_only()` 旁路，完全跳过 `_ensure_fastboot()` 与 `Phase.FASTBOOT`；默认路径不动。
6. **回退**：失败先判类型；FBD 原地重试（重建+清 COW），**绝不 reboot bootloader**；到极限转"修复FBD/EDL"。

---

## 8. 可信度边界与未决点

- **命令序列**：紫罗兰二进制不可反编译（`violet-vs-lrom-flash.md` §0），除官方文案字面外**全为③推断**，须实机抓日志复核（紫罗兰 GUI 日志窗口可对照）。
- **"BL 进不去"的判定**：现无可靠只读方法，主要靠**用户声明/历史**；**不做 `reboot bootloader` 试探**（会失联）。→ 未决：是否有 `getvar` 层面的只读线索（如 `getvar all` 里 bootloader 版本/secure 状态）可辅助判断，需实机样本。
- **`getvar super-partition-size` 可用性**：因机型而异，可能被屏蔽，需 fallback。
- **是否在 fbd_only 里允许"应急写 bootloader 侧"（如刷恢复 Fastboot 的 abl）**：①官方无此说；②社区有偏门案例；本文立场=**不纳入自动流程**，需人工显式决策（§2.4 例外、§3）。
- **未决**：fbd_only 后是否应 `fastboot reboot`（回系统）还是留在 FBD 让用户决定；建议回系统并提示。

---

## 9. 参考资料

- 紫罗兰官网首页（12 模式定义 · 仅FBD/修复FBD/修复Super真死原文）：<https://violettool.top/>
- 紫罗兰欧加刷机教程（常规/强力/仅FBD/AB通刷 适用场景；"缺少 Fastboot 分区…正常设备无需启用"）：<https://violettool.top/tutorials/oplus-flash/>
- 紫罗兰疑难杂症页（逻辑分区刷失败→强力线刷；900E 黑砖；"未找到槽位信息"=连线问题）：<https://violettool.top/trouble/trouble2.html>
- 紫罗兰基础刷入教程（FastbootD 下擦 FRP 等）：<https://violettool.top/tutorials/basic-flash.html>
- 本地社区笔记：`$HOME/桌面/一加线刷分区.txt`（§二 分区总表 BL/FD 归属、§三 六个红线分区、§六 判定方法、§八 槽位/super/COW）、`$HOME/桌面/注意事项.txt`（§一 ARB/熔断、§五 `--slot=all`/unlock_critical、§六 备份、§七 救砖生路与永久砖判定）
- 本仓库既有报告：`docs/research/violet-fastbootd-rescue.md`（初步版）、`violet-vs-lrom-flash.md`（§0 不可反编译、§3/§6 槽位与重建）、`violet-slot-and-logical-strategy.md`（§0/§2 重建与 super 空间、建议 3）
- Lrom 现有实现：`src/lrom/core/flash.py`（`run:542-596` / `_ensure_fastboot:615-638` / `_detect_mode:640-655` / `_enter_fastbootd:684-727` / `_recreate_logical_partition:429-476` / `preflight:96-201`）、`src/lrom/device/runner.py`（`is_fastbootd:205-209` / `getvar:200-203`）、`src/lrom/core/plan.py`（`Phase:18-29` / `OPLUS_LOGICAL_PARTITIONS:118`）、`src/lrom/tui/menu.py`（`FlashMode:20-48`）、`src/lrom/cli.py`（`cmd_flash:716+` / `FlashExecutor:1043-1052`）
