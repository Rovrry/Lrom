# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.1] - 2026-10-27

### 修复：菜单点击部分选项直接崩溃

**现象**（用户实测）：

```
请选择: 7
  设备序列号: ***REDACTED***
  ...
  出错了: name 'pause' is not defined
NameError: name 'pause' is not defined
```

**原因**：v0.3.0 重构 menu.py 时用脚本批量改动，
`ask` / `ask_path` / `confirm` / `pause` 四个输入辅助函数被误删。
Python 语法检查发现不了（名字在运行时才解析），
所以静态检查通过、发布后用户一点就崩。

**修复**：
- 补回四个输入辅助函数
- 修复 `_menu_arb` 对已删除的 `_require_package` / `_require_images` 的引用

### 新增：菜单完整性测试（防止复发）

`tests/unit/test_menu_integrity.py`（18 个用例）：

- `test_no_undefined_module_level_calls` — AST 解析，找出所有被调用但未定义的名字
- `test_required_helpers_exist` — 校验 ask/ask_path/confirm/pause 等基础设施存在
- `test_menu_handlers_signature` — 每个菜单处理函数必须是 `(sess, mon)`
- `test_all_handlers_referenced_in_run_menu` — run_menu 注册的处理函数必须真实存在

已实测验证：临时删掉 `pause` 后，该测试立即失败并给出准确提示。

测试总数 96 → 114。

## [0.3.0] - 2026-10-27

### 变更：交互流程重构（按用户反馈）

**1. 启动先检测环境**
运行 `./lrom` 后第一步是系统环境检测，逐项显示 adb / fastboot / udev /
用户组 / adb server / USB 设备。缺失项会给出可直接复制的安装命令，
并支持 `(r)` 修复后重新检测 / `(c)` 继续使用离线功能 / `(q)` 退出。

**2. 「解全量包」内嵌选择 ROM 包**
原来的「3. 选择 ROM 包」是独立菜单项，与「1. 解全量包」割裂。
现在合并为一步：进入「解全量包」直接询问包路径（支持拖拽，回车沿用上次），
随后自动完成分析 → 型号核对 → 解包。

**3. 设备状态实时监测**
菜单顶部的设备信息原来是启动时的一次性快照，用户拔掉数据线后
仍显示"已连接 OPD2404"，容易造成误判。

现在由后台线程每 1.5 秒轮询一次：
- 拔线后菜单立即显示「○ 设备: 未连接」
- 重新插上后自动恢复
- 状态变化时会打印提示行（不清屏，不打断阅读）
- 危险操作（线刷）前会强制 `refresh_now()` 再确认一次，不使用滞后的快照

菜单结构也相应调整：

```
1) 解全量包    2) 预演检查    3) 线刷全量包    4) 只刷指定分区
5) 备份设备分区  6) ARB 熔断检测
7) 设备信息    8) 重启设备    9) 环境自检     10) 关于
```

### 新增

- `device/monitor.py`：设备实时监测（后台线程 + 状态变化回调）
- `DeviceMonitor.refresh_now()`：危险操作前同步刷新
- 7 个监测模块单元测试

## [0.2.1] - 2026-10-27

### ⚠️ 重要修复：双槽刷写会撑爆 super（真实事故）

**事故经过**：用户使用 `--both-slots` 刷写，在 fastbootd 阶段出现：

```
my_stock     ✓ 3,831.0 MB
my_stock_b   ✓ 3,831.0 MB
odm          ✓ 1,627.1 MB
odm_b        ✗ Not enough space to resize partition
```

**根因**：`super` 是**单个物理分区**，两个槽位的逻辑分区**共用同一空间**。

| 项 | 容量 |
|---|---|
| `super` 总容量 | 14.00 GB |
| 单槽逻辑分区需求 | 8.97 GB |
| 双槽合计 | 17.94 GB |
| **超出** | **约 4 GB** |

`--both-slots` 只适用于**物理分区**（boot/xbl/vbmeta 等），
逻辑分区（system/vendor/odm/my_* 等，位于 super 内）**绝对不能**双槽刷写。

**修复**：
- `_flash_other_slot()` 遇到 `Phase.FASTBOOTD` 直接返回，逻辑分区不再双槽刷写
- `super` 分区本身也排除
- 新增回归测试 `test_both_slots_does_not_apply_to_logical`

### 新增

- **刷写进行中的实时提示**
  大分区刷写耗时长（`my_stock` 约 105 秒），期间屏幕无输出，
  用户容易误以为卡死而拔线。现在每个超过 20 MB 的分区刷写时，
  会每秒钟刷新 spinner 与已用时间、预估剩余时间：

  ```
  ⠹ my_stock         刷写中…  3,831.0 MB   42.3s  剩余约 63s
  ```

- **super 空间预检**
  刷写前读取设备 `super` 分区容量（root 或 `getvar partition-size:super`），
  与逻辑分区总需求比较，不足时在**预检阶段直接阻止**，
  而不是刷到一半才报错。

### 修复

- 逻辑分区双槽刷写导致 super 空间耗尽（详见上文）

## [0.2.0] - 2026-10-27

### 新增

- **交互式菜单**
  无参数运行 `./lrom` 进入菜单，共 12 项：解全量包 / 分析包 / 选择包 /
  预演检查 / 线刷全量包 / 只刷分区 / 备份 / ARB 检测 / 设备信息 /
  重启设备 / 环境自检 / 关于。
  顶部常驻设备状态与当前 ROM 包；记住上次使用的包；支持拖拽文件进终端。

- **免安装运行**
  新增 `./lrom` 启动脚本，自动选择 Python 3.10+ 并设置 PYTHONPATH。
  克隆即可运行，无需 venv / pip / 写 PATH。卸载即删除目录。

- **`lrom selfcheck` 环境自检**
  检查 adb/fastboot、udev 规则、用户组权限、adb server 归属、USB 设备可见性，
  每项附可直接复制的修复命令。

- **`--both-slots` 双槽刷写**
- **逻辑分区写入前 delete + create**（清理 COW 快照层）
- **FastbootD 切换超时提示**

### 修复

- **菜单信息一闪而过**
  两处原因：`os.system("clear")` 每次返回菜单都清屏，且 `_menu_inspect`
  与 `_menu_flash` 缺少暂停。现在只在真实 TTY 清屏，并补齐所有暂停点。

- **解包目录识别错误**
  菜单在 `work/<包名>` 查找镜像，但解包默认输出到 `unpacked/<包名>`，
  导致已解包仍提示"尚未解包"。现在自动在多个常见位置查找。

- **install.sh 引导不清**
  明确说明"Linux 内核自带 USB 驱动，不需要装驱动"，
  区分驱动与命令行工具，给出各发行版准确包名与无 sudo 方案。

### 变更

- **预演检查重写**
  原先只是逐条打印 47 个分区的流水账，信息量低。
  现改为回答四个关键问题：刷写规模、镜像完整性、风险项、数据影响，
  并给出明确结论（能不能刷）。

- `install.sh` 改为可选（会先说明不需要安装）
- 新增 `uninstall.sh`

## [未发布]

### 新增

- **逻辑分区写入前先 delete + create**
  直接 `fastboot flash` 逻辑分区时，若目标分区不存在或容量不足会失败；
  虚拟 A/B 的 COW 快照层（`xxx_a-cow` / `xxx_b-cow`）也可能残留旧数据。
  现在会先 `delete-logical-partition`（4 种命名）再 `create-logical-partition`（两槽），
  然后才写入。可用 `--no-recreate-logical` 关闭。
- **双槽刷写 `--both-slots`**
  每个分区刷完当前槽位后，再补刷另一槽位，避免切换槽位后回退到旧版本。
- **FastbootD 切换超时提示**
  OPLUS 设备的 fastbootd 可能显示语言选择界面并暂停。现在 15 秒未就绪会提示
  "屏幕若有语言选择请手动点选"，超时后给出明确的排查步骤。等待上限提升至 300 秒。

### 变更

- 参考 [koaaN/Universal_Flasher](https://github.com/koaaN/Universal_Flasher) 的实战做法补齐上述细节

## [0.1.0] - 2026-10-26

首个可发布版本。已在真实设备（OnePlus OPD2404 / Android 16）上完成端到端刷机验证。

### 新增

**包分析（`lrom inspect`）**
- 解析 `payload.bin` manifest（遵循 AOSP 官方 `update_metadata.proto` 语义）
- 读取 OTA 元数据：产品名、设备代号、版本、安全补丁、OTA 类型
- 全量包 / 增量包判定
- 包结构自洽性校验（每个操作的数据长度与 extent 是否匹配）
- 设备型号核对，不匹配时硬阻止

**刷前备份（`lrom backup`）**
- 通过 adb + root `dd` 备份关键分区：boot / init_boot / vbmeta×3 / dtbo / recovery
- 生成 `backup.json` 清单，记录每个分区的大小、SHA-256 与设备指纹
- 备份前校验磁盘空间与 root 权限

**刷写计划（`lrom plan`）**
- 纯数据结构，可审阅、可导出 JSON
- 三级风险分类：`NEVER_OVERWRITE` / `DANGER` / `SAFE`
- 自动判定逻辑分区（走 FastbootD）与物理分区（走 Fastboot）
- modem 强制最后刷写

**解包（`lrom unpack`）**
- 支持 REPLACE / REPLACE_BZ / REPLACE_XZ / ZSTD / ZERO
- 零拷贝读取 STORED 的 payload.bin，流式解压不占内存
- 每个操作校验 `data_sha256`，损坏立即报错
- 增量操作直接拒绝，绝不输出残缺镜像

**ARB 熔断检测（`lrom arb`）**
- 解析 `xbl_config` ELF64 → HASH 段 → OEM metadata → `arb_index`
- 设备侧读取支持 adb+root 与 fastboot fetch（均不需要额外工具）
- 降级时硬阻止，防止硬件级变砖

**刷写（`lrom flash`）**
- 两阶段刷写：Fastboot（物理分区）→ FastbootD（逻辑分区）
- `--dry-run` 预演：完整校验流水线，不写设备
- 自动完成 ADB → Fastboot → FastbootD 的模式切换与等待
- 关键分区锁定自动识别并跳过，不中止流程
- 失败立即中止，附可复制的恢复命令
- `--wipe` 清空用户数据（降级/救砖）

### 安全设计

- **永不自动重启设备** — 重启决策权留给用户
- 刷写后强制提示：降级必须先格式化，否则变砖
- 需输入完整 `FLASH` 才执行，不用 `y/n`
- fastboot 退出码为 0 但输出含 `FAILED` 时判定失败
- 零遥测，完全离线

### 已知限制

- 未实现 `unlock_critical`，13 个关键分区（xbl/abl/tz/hyp 等）在未解锁关键分区的设备上无法写入（会自动跳过）
- 增量 OTA 包明确不支持
- 未实现 `lrom restore` 与 `lrom patch`（恢复 root）
- 仅支持全量 OTA 包，小米 tgz / OFP / OPS / OZIP 待实现

### 验证

- 设备：OnePlus OPD2404（一加平板 Pro），Android 16，A/B 双槽
- 系统：Kali GNU/Linux Rolling 2026.2
- 结果：`16.0.5.702` → `16.0.5.1201`，34 个分区写入成功，用户数据保留，设备正常开机
- 71 个单元测试通过
