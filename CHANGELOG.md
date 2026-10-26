# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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
