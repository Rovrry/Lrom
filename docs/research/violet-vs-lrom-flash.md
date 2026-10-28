# 紫罗兰工具箱（VioletToolBox）欧加线刷模式 vs Lrom 线刷模式 —— 差异调查报告

> 调查日期：2026-10-28
> 调查对象：
> - 紫罗兰工具箱 欧加真 Fastboot 线刷工具 v7.2（本地 `$HOME/桌面/欧加真Fastboot线刷工具v7.2/`）
> - 本项目 Lrom 线刷实现（`src/lrom/core/flash.py`、`plan.py`、`package.py`、`tui/menu.py`）
> - 佐证：DEVNOTES §1.1/§1.3/§5、桌面社区笔记、紫罗兰官网教程与文档、XDA / AOSP 资料
>
> 结论口径：每个维度给出「紫罗兰 / Lrom」对照，并给出 **Lrom 已更好 / 建议采纳 / 不采纳** 的明确结论。

---

## 0. 前置：二进制不可反编译（已亲自确认）

任务要求先确认 `WindowsFlash.exe` 无法反编译还原源码。以下为实测证据（`[个人版]WindowsFlash.exe`）：

| 检查项 | 结果 | 含义 |
|---|---|---|
| `file` | `PE32+ executable ... x86-64, 6 sections` | 原生 x86-64 PE，非脚本、非 .NET |
| PE Optional Header Magic | `0x20b` = PE32+ | 64 位原生 |
| CLR 数据目录（第 15 项）| `rva=0x0 size=0x0` | **无 .NET CLR 头**，不是托管程序集 |
| 关键字符串检索（`mscoree` / `CLR` / `msil` / `v4.0.30319`）| 全部无命中 | 排除 .NET / ILSpy 反编译路径 |
| 节表 | `.code/.text/.rdata/.pdata/.data/.rsrc` | 典型原生程序；`.rsrc` 高达 0xd1800（≈858 KB，占全文件 981 KB 的 87%） |
| `.rsrc` 区块熵 | **8.000 bits/byte** | 满熵 = 加密/压缩载荷，非明文资源 |
| 内嵌 zlib 痕迹 | `1.2.11`、`incorrect header check` 等一整组 inflate 错误串 | 程序静态链接 zlib，运行时解压资源 |
| 明文可读业务字符串 | 仅有 Win32 API（`SHBrowseForFolderW` 等）、zlib 报错、manifest | **没有任何业务逻辑/分区表/命令模板可读** |

**结论（如实说明）：无法从二进制还原源码。**
- `WindowsFlash.exe` 是打包的原生 PE，业务载荷加密/压缩存放在 `.rsrc`（满熵 8.000），需逆向其解包器才能取回——已超出且不适合本任务范围，不做。
- 因此对「紫罗兰欧加线刷模式内部实现」的还原，**依据的是官方公开教程 + 社区笔记 + 目录内可见资源**，而非反编译。凡属"官方未明说、仅从行为推断"的地方，下文均标注「（推断）」。
- 附带旁证：工具目录内实际是「标准 fastboot 前端」——`bin/platform-tools-windows/` 内含 Google 官方 `fastboot.exe`（i386）、`adb.exe`、`payload.exe`（payload 解包器）、`7z.exe`；`bin/Android/{MTK,QC5.4,QC5.15}/` 各含预置 `metadata.img`/`userdata.img`（MTK 另含 `my_company.img`/`my_preload.img`）。这说明其"模式"本质是**预置的 fastboot 命令配方 + 平台专属模板镜像**，而非黑盒魔法。

---

## 1. 紫罗兰「欧加线刷」到底是什么（官方定义）

据紫罗兰官网 [核心刷写-欧加线刷](https://violettool.top/) 与 [欧加刷机使用教程](https://violettool.top/tutorials/oplus-flash/)，「欧加线刷」在**全量包模式**下提供 7 个子模式：

| 紫罗兰子模式 | 官方描述 | 实际动作（据描述 + 社区笔记推断） |
|---|---|---|
| **普通模式** | 正常设备升级/降级线刷 | fastboot 刷物理分区 → fastbootd 刷逻辑分区，标准流程 |
| **强力线刷** | 重新构建逻辑分区，修复 Super 异常设备；用于 `my_*`/system/vendor 等逻辑分区刷写失败 | **delete + create 逻辑分区后重刷**（等价 Lrom 的 `recreate_logical`） |
| **仅 FBD 模式** | 适用于丢失 `ocdt.img`、无法进 Bootloader 的设备 | 跳过 bootloader 阶段，**只在 FastbootD 内刷逻辑分区** |
| **修复 FBD** | 卡 Bootloader、进不了 FastbootD 的设备 | 用匹配版本全量包重刷，重建可用的 FastbootD 环境 |
| **ARB 熔断检测** | 识别设备/固件包是否熔断，防止降级变砖 | 只读检测 xbl_config 的 ARB 值 |
| **分区表校验** | 识别固件包与设备是否匹配、数据线质量 | 刷前分区表比对（"双重刷写保障"） |
| **AB 通刷** | 灵活切换启动槽位，无视 Super 损坏，「目前最安全的线刷模式」 | 双槽 + 逻辑分区重建，容错 Super 异常 |

此外还有**售后包模式**（散包直接刷 FBD / FB）、**自动救砖**等，不属于"全量包线刷"主线，下文只在相关处提及。

> 关键点：紫罗兰把"刷写策略"**拆成多个并列模式**让用户手选；Lrom 则是**单一流程里用参数自适应**（`recreate_logical`、`both_slots`、`target_slot`、`to_opposite_slot` 等）。这是两者最根本的交互哲学差异（见 §10）。

---

## 2. Fastboot 阶段（bootloader / 物理分区）分区策略

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 刷什么 | 物理分区 9 个（boot/init_boot/vendor_boot/recovery/dtbo/vbmeta 系列/modem/persist）+ 固件/引导链 24~25 个（xbl/abl/tz/hyp…），受 `unlock_critical` 限制 | 由 `plan.py` 分类：`DANGER_PARTITIONS`（bootloader 链，正常刷但要求二次确认）/ `WARN_PARTITIONS` / 普通 safe；`NEVER_OVERWRITE`（persist/devinfo/modemst/frp/userdata…）**默认跳过** |
| persist / OCDT | 普通模式**会尝试刷**；社区笔记反复警告"能不刷就不刷"；工具未内置强保护 | **默认 BLOCKED 跳过** persist 等身份分区；OCDT(`engineering_cdt`/`ocdt`) 归 WARN，preflight 单独红字警告"用别机的会进不了 Fastboot、损坏只能拆字库" |
| 关键分区被锁 | 见 §5 | 见 §5 |
| 刷写顺序 | 官方未公开顺序；社区/经验值倾向"大分区先刷、modem 后刷"（推断） | 代码硬约束：**同阶段按 size 从大到小**；`modem` 强制排到最后（`LAST_PARTITIONS`，代码有注释"提前刷会导致刷完没信号"） |

**结论：Lrom 已经更好。** Lrom 用显式白/黑名单 + 风险分级 + 强制排序，把"该刷/不该刷/先刷/后刷"变成可审阅数据；紫罗兰把同样判断留给用户或内置固定流程，persist/OCDT 等红线分区的保护不如 Lrom 显式。

---

## 3. FastbootD 阶段（super 内逻辑分区）分区策略

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 走哪些分区 | system/system_ext/product/vendor/odm/*_dlkm + 欧加 my_*（15~18 个） | `OPLUS_LOGICAL_PARTITIONS` 枚举 18 个，统一归 `Phase.FASTBOOTD` |
| 进 FastbootD 方式 | `fastboot reboot fastboot`，OPLUS 设备会停在语言选择界面需人工点选（紫罗兰同样有此坑，社区笔记有载） | `_enter_fastbootd()`：`reboot fastboot` 后轮询，>15s 主动提示"若屏幕出现语言选择请点 English"，超时给三条排查建议 |
| 分区重建 | **强力线刷**模式才 delete/create（普通模式不重建） | `recreate_logical=True` 时**默认**先 `delete-logical-partition <name>_a/_b/_a-cow/_b-cow` 再 `create-logical-partition`，按 4096 块换算 size（参照 Universal_Flasher） |
| 空间校验 | "分区表校验"功能（描述较笼统，是否算 super 剩余空间未知） | preflight 显式算 `sum(逻辑分区 size)` vs `super_size`，超了报 error 并提示"去掉 --both-slots（逻辑分区不能双槽）" |

**结论：Lrom 已经更好（就"默认安全"而言）；"强力线刷"的 delete/create 思路值得采纳——而 Lrom 已经默认实现了。**
- 紫罗兰把"重建逻辑分区"放进单独的"强力线刷"模式，需用户判断何时启用；Lrom 默认就重建（`recreate_logical` 可关），对"COW 残留、容量不足"更稳。
- 紫罗兰"仅 FBD / 强力线刷"的存在恰恰说明这些是**故障场景专用**；Lrom 用参数覆盖，逻辑等价但心智负担更低。

---

## 4. 逻辑分区是否刷 / 如何刷

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 是否刷逻辑分区 | 普通/强力/AB 通刷**都刷**；仅 FBD 只刷逻辑 | 有 FASTORED 阶段就刷 `plan.by_phase(FASTBOOTD)` |
| 双槽刷逻辑分区 | AB 通刷会处理双槽；但社区笔记（`一加线刷分区.txt`）明确指出逻辑分区**不能简单双槽刷**（会翻倍占 super 空间） | 代码硬约束（`_flash_other_slot` 注释）：**逻辑分区（`Phase.FASTBOOTD`）一律禁止双槽**，直接 `return None`，理由"super 是单个物理分区，双槽会触发 Not enough space to resize partition" |
| 单刷 vs 整包 | 支持 `flash super super.img`（16GB）或逐分区刷 | 逐分区刷（`flash <part> <part>.img`），不做整 super 合并——**规避 Windows 工具需先合并 super 散包的 ≥30GB 场景** |

**结论：Lrom 已经更好。** Lrom 从代码层面禁止"逻辑分区双槽"这一高危误操作，紫罗兰依赖用户不勾选；Lrom 逐分区刷也省掉 super 合并的磁盘/时间成本。

---

## 5. 关键分区被锁（critical partition）处理

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 遇到 `Flashing is not allowed for Critical Partitions` | 官方未公开明确策略；从"普通模式受限、仅 FBD/强力可绕过"的描述看，是**靠切模式规避**（推断） | `flash.py`：`is_critical_locked()` 识别该错误 → 标记 `StepStatus.LOCKED`，**不中止流程、继续刷下一分区**；结果汇总单列"锁定 N 个分区（需 unlock_critical）"，并给 `fastboot flashing unlock_critical` 恢复提示 |
| DEVNOTES 依据 | — | §1.3 第 3 条："关键分区锁定……这是**可跳过**的，不该中止整个流程" |

**结论：Lrom 已经更好。** Lrom 把"锁定"作为一等的、可恢复的非致命状态处理（不 abort、有汇总、有 hint）；这是踩坑后固化的正确行为。

---

## 6. 槽位处理

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 默认行为 | AB 通刷：可从 A 或 B 槽启动、无视 Super 损坏（最安全模式）；普通模式按当前槽 | 菜单 3「线刷全量包」**默认刷到另一槽**（`to_opposite_slot=True`，`--set-active` 后再刷）；连刷两次即两槽都换新（v0.7.3/v0.7.4 设计，配合"平刷两次洗回原厂"） |
| 显式切槽 | 支持（首页可切槽、AB 通刷） | `target_slot` + `_set_active_slot()`；`--set-active=a/b` 后物理分区落当前槽 |
| 双槽同时刷 | AB 通刷/`--slot=all` 双槽刷物理分区 | `both_slots=True` 时对**物理分区**逐槽补刷；逻辑分区自动禁双槽（见 §4） |
| 风险提示 | 社区笔记："`--slot=all` 刷 super 时若另一槽无法保留，fastboot 直接失败并要求 -w 擦数据" | 用代码阻断该路径（逻辑分区禁双槽），从根上避开 |

**结论：Lrom 已经更好（更安全、更明确）。** 紫罗兰的"AB 通刷无视 Super 损坏"是营销点，但底层仍受 super 空间/双槽约束；Lrom 用"默认刷另一槽 + 逻辑分区禁双槽"把风险收敛。

---

## 7. 实际刷写分区数

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 满盘规模 | 一加12 全量包 48 个、一加13 53 个（社区笔记实测）；普通模式基本全刷，仅 FBD/强力 按需增删 | `plan.total_bytes` / `active_ops` 动态统计；`dry_run_report()` 明确"阶段一 N 个 + 阶段二 M 个 = 合计 X GB"；被跳过分区单独列出"实际写入 N 个" |
| 跳过机制 | 依赖模式选择 + 用户不勾选 | `NEVER_OVERWRITE` 默认跳过；设备上不存在的分区（`device_partitions` 比对）自动跳过；`include` 可指定只刷 |
| 可见性 | 日志窗口（用户需滚日志判断） | `format_plan()` 逐分区列"大小/风险/说明"；`dry_run_report()` 回答"写多少/有无风险/镜像全不全/数据丢不丢"四问 |

**结论：Lrom 已经更好。** Lrom 把"刷多少个、跳过哪些、为什么跳"在刷前就结构化呈现（预演报告），紫罗兰主要靠刷后日志。

---

## 8. 目标定位：修复 vs 全量替换

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 定位 | **模式即用途**：普通=全量替换；强力/仅 FBD/修复 FBD/自动救砖=**修复导向**（故障场景专用，含丢失 ocdt、卡 bootloader、Super 真死、900E 黑砖） | **单一全量流程 + 参数自适应**：`recreate_logical`（修复逻辑分区异常）、`both_slots`（换槽/洗原厂）、`wipe`（降级/救砖）、`include`/`pick`（只刷指定分区，用于精准修复） |
| 修复场景覆盖 | 覆盖广，且对每个故障场景有专门模式与文案（如 900E 端口黑砖→强力线刷） | 用同一执行器覆盖：`is_critical_locked` 跳过、逻辑分区重建、失败即中止+恢复命令；但**没有**"自动识别 900E/自动下载救砖包"这类一键救砖入口 |
| 语义清晰度 | 模式多 → 新手易选错（要读教程才知道该选哪个） | 一个流程 + 明确开关，语义集中；但**缺少"设备当前处于哪种故障、建议用哪条路径"的主动诊断** |

**结论：各有优势，此处「建议采纳」紫罗兰的修复场景分类意识，但不复刻其"多模式并列"。**
- 紫罗兰强在**把故障场景显式命名并给对应策略**（丢失 ocdt→仅 FBD；卡 FBD→修复 FBD；900E 黑砖→强力线刷）。Lrom 可借鉴的是：在预演/自检里**识别设备现状并主动推荐**（例如检测到 `is-userspace` 不对/进不了 FBD 时，直接建议"重建逻辑分区"或"仅 FBD 路径"），而不是让用户自己判断。
- **不采纳**：不建议把 Lrom 拆成 7 个并列模式——那会把"选错模式"的风险转移给用户，与 Lrom"宁可拒绝不可误判"的单一安全流程哲学冲突。

---

## 9. 安全校验差异（Lrom 的显著优势区）

| 校验点 | 紫罗兰 | Lrom |
|---|---|---|
| 包结构自洽 | "分区表校验"（包/设备匹配 + 数据线质量） | `package.py`：逐 `InstallOperation` 校验 `dst_extents`/`data_length`/`src_extents` 自洽，异常即 BLOCKER；**裁剪版 proto 枚举已被 DEVNOTES §1.1 纠正并测试锁死** |
| 全量/增量判定 | 解包时会因裁剪 proto 误判（DEVNOTES §1.1 记录的坑，紫罗兰原源码 `update_metadata.proto` 是错枚举） | `classify_payload()`：正确 AOSP 枚举，区分 OTA_FULL / OTA_DELTA；增量包判 `NEEDS_SOURCE` 拒绝线刷 |
| 型号匹配 | 分区表校验含匹配判断（粒度未知） | `check_device_match()`：`pre-device`≠设备代号 或 `product_name`≠机型 → **BLOCKER**；且机型只用**设备侧**标识，绝不用包内 product_name（防张冠李戴） |
| ARB 熔断 | 有"ARB 熔断检测"功能 | `arb.py` / `edl_db.py`：ARB 检测 + 9008 熔断风险库；`_warn_edl_risk()` 命中即**硬阻断**刷写 |
| 预演 dry-run | 无（描述未见） | `dry_run=True`：完整走校验流水线**但不写设备**；`dry_run_report()` 给四问结论 |
| 用户确认 | 点"开始"即刷 | 危险操作前 `refresh_now()` 重取真实设备态；清数据须显式输入 y；菜单 2 预演、菜单 3 实刷分离 |
| 恢复命令 | 日志报错 | 每个失败/锁定 `StepResult` 带 `recovery_hint`（可直接复制的 fastboot 命令） |

**结论：Lrom 已经更好（安全校验是 Lrom 核心护城河）。** 紫罗兰起点就带"裁剪 proto 误判"的原罪，Lrom 用正确枚举 + 结构自洽 + 型号保守匹配 + ARB/9008 硬阻断 + dry-run，形成更强的"宁可拒绝"防线。

---

## 10. 用户交互流程差异

| 维度 | 紫罗兰 | Lrom |
|---|---|---|
| 首页心智 | "12 种刷写模式"并列，按品牌/场景分专区（信息量极大、功能 120+） | 一个主菜单，刷写主线只 4 项：1 解包 / 2 预演 / 3 线刷全量包 / 4 只刷指定分区 |
| 刷前 | 解包 payload → 选模式 → 点"开始线刷" | 解全量包 → **菜单 2 预演（不写设备）** → 菜单 3 实刷；危险提示/清数据二次确认 |
| 模式选择负担 | 高（新手须读教程选对模式） | 低（默认刷另一槽；`recreate_logical` 默认开；`both_slots/pick/wipe` 按需） |
| 进度反馈 | GUI 日志 / 进度条 | 大分区刷写有 ticker 持续输出"还在动"（防用户以为卡死拔线）；FastbootD 等待有分阶段提示 |
| 失败处理 | 日志报错，用户按教程再切模式重刷 | 失败即中止 + 逐项 `recovery_hint`；critical 锁定不中止；汇总成功/跳过/锁定/失败计数 |
| 平台 | Windows GUI，闭源、带遥测/远程 APK 下载（DEVNOTES §5） | Python 标准库、跨平台 CLI+TUI、开源、无遥测 |

**结论：Lrom 的交互更聚焦、更安全；紫罗兰的"功能密度"是其卖点但也是选错模式的风险源。**
- **建议采纳**：借鉴紫罗兰"把常见故障给出明确的一键式建议路径"的**结果**（如自检后主动提示"检测到无法进 FastbootD，建议重建逻辑分区/仅刷逻辑分区"），但用 Lrom 的方式实现（诊断 + 建议，而非新增并列模式）。
- **不采纳**：不建议为了对齐紫罗兰而扩充成 12 模式/120 功能的"大而全"工具箱——与 Lrom 定位（安全、可审阅、单一流程）冲突，且会显著增加"选错模式"风险。

---

## 11. 汇总结论表

| # | 维度 | 紫罗兰做法 | Lrom 做法 | 结论 |
|---|---|---|---|---|
| 2 | Fastboot 阶段分区 | 全刷物理+固件，persist/OCDT 弱保护 | 黑白名单分级 + modem 最后 + 强制大→小 | **Lrom 已经更好** |
| 3 | FastbootD 阶段分区 | 普通不重建，强力才 delete/create | 默认 delete/create + super 空间校验 | **Lrom 已经更好**（强力线刷思路已内建） |
| 4 | 逻辑分区是否刷 | 视模式；双槽处理不硬约束 | 都刷；**逻辑分区禁双槽**（代码硬约束） | **Lrom 已经更好** |
| 5 | 关键分区被锁 | 靠切模式规避（推断） | 识别为 LOCKED、不中止、给恢复提示 | **Lrom 已经更好** |
| 6 | 槽位处理 | AB 通刷无视 Super（营销点） | 默认刷另一槽；逻辑分区禁双槽 | **Lrom 已经更好** |
| 7 | 实际刷写分区数 | 全刷为主，靠模式/勾选 | dry-run 结构化统计 + 跳过原因 | **Lrom 已经更好** |
| 8 | 目标定位（修复 vs 全量） | 模式即用途，故障场景显式命名 | 单一流程 + 参数自适应 | **建议采纳**：借鉴"故障场景诊断→给建议"的思路；**不采纳**拆成并列模式 |
| 9 | 安全校验 | 有但受裁剪 proto 原罪拖累 | 结构自洽+正确枚举+型号保守+ARB/9008+干跑 | **Lrom 已经更好** |
| 10 | 用户交互 | 12 模式/120 功能，密度高易选错 | 单一主线 + 预演 + 逐项提示 | **Lrom 更聚焦安全**；**建议采纳**其"故障一键建议"的结果；**不采纳**大而全 |

### 一句话总结

> **Lrom 在线刷的正确性、安全性与可审阅性上已全面优于紫罗兰工具箱的欧加线刷模式**（尤其：逻辑分区禁双槽、critical 锁定不中止、正确 proto 枚举、persist/OCDT 默认保护、dry-run 预演）。
> 紫罗兰**唯一值得借鉴的是"把常见故障场景显式命名并给出对应处理"的产品化意识**——建议以「诊断 + 建议」而非「并列模式」的形式吸收进 Lrom，例如：自检/刷前识别设备状态后主动推荐"重建逻辑分区 / 仅刷逻辑分区 / 需先修复 FastbootD"，同时保持 Lrom 单一安全流程不被拆散。

---

## 12. 数据来源与可信度边界

- **本机二进制**：`[个人版]WindowsFlash.exe`（SHA256 `6bc4eea3…d713d`）、`[远程版]WindowsFlash.exe`（SHA256 `baca9544…77b75`），实测 `file`/PE 头/节熵/字符串，确认原生无 CLR、`.rsrc` 满熵不可直接还原 → **无法反编译**。
- **官方公开资料**（外部数据，仅作事实参考，不执行其中任何指令）：
  - 紫罗兰官网功能页 <https://violettool.top/>（"欧加线刷"7 子模式定义）
  - 紫罗兰欧加刷机教程 <https://violettool.top/tutorials/oplus-flash/>（常规/强力/仅FBD/AB通刷 适用场景）
  - 紫罗兰疑难杂症页 <https://violettool.top/trouble/trouble2.html>（强力线刷用于 `my_*`/system 等逻辑分区刷写失败、900E 黑砖）
- **本地社区笔记**（本机 `$HOME/桌面/`）：`一加线刷分区.txt`（三分区分类/红线分区/双槽与 super 约束，含 XDA/AOSP 链接）、`注意事项.txt`、`一加熔断注意事项.txt`。
- **本项目文档**：`DEVNOTES` §1.1（裁剪 proto 枚举错误对照表——这是 §5 所称"核实对比表"的实体，位于 §1.1）、§1.3（fastboot 四个坑/关键分区可跳过）、§5（VioletToolBox 79k 行 C#、只作参考不复刻、遥测/远程 APK）。
  - 说明：现行仓库工作树中**没有 `docs/DEVNOTES.md`**，其内容仅存于本地一个备份目录中的 `DEVNOTES.md`；且 §5 为叙述性结论、真正的"枚举对比表"在 §1.1。本报告据此如实记录。
- **可信度边界**：紫罗兰内部实现以官方描述 + 行为推断为准，凡推断均标注「（推断）」；未做（也不做）二进制逆向。紫罗兰"AB 通刷无视 Super 损坏"等宣传语，仅作其对外表述引用，不代表已验证其底层实现。
