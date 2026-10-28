"""执行刷写。

设计要点（针对 Windows 版工具踩过的坑）：
  1. 每个分区的结果严格判定，不靠 stdout.Contains 猜测
  2. 大分区先刷，早期暴露空间/连线问题
  3. modem 最后刷（提前刷会导致刷完没信号）
  4. 任一阶段致命失败立即中止，不继续往下刷
  5. 每步都有可复制的恢复命令
  6. 支持 dry-run：完整的校验流水线，但不写设备
"""

from __future__ import annotations

import re
import shutil
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ..device.runner import AdbRunner, FastbootRunner, RunResult
from .plan import FlashOp, FlashPlan, Phase, Risk


class StepStatus(Enum):
    PENDING = "待执行"
    RUNNING = "进行中"
    OK = "成功"
    FAILED = "失败"
    SKIPPED = "已跳过"
    LOCKED = "锁定（需 unlock_critical）"
    BLOCKED = "已阻止"


@dataclass
class StepResult:
    partition: str
    status: StepStatus
    duration: float = 0.0
    size: int = 0
    message: str = ""
    recovery_hint: str = ""
    slot_written: str = ""

    @property
    def size_mb(self) -> float:
        return self.size / 1e6


@dataclass
class FlashResult:
    plan: FlashPlan
    steps: list[StepResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    aborted: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return (
            not self.aborted
            and not self.error
            and all(
                s.status in (StepStatus.OK, StepStatus.SKIPPED, StepStatus.LOCKED)
                for s in self.steps
            )
            and len(self.steps) > 0
        )

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if s.status is StepStatus.FAILED]

    @property
    def elapsed(self) -> float:
        return self.finished_at - self.started_at if self.finished_at else 0.0


class FlashAborted(Exception):
    """刷写被中止。"""


# --------------------------------------------------------------------------
# FastbootD 只读诊断（救砖分流）
# --------------------------------------------------------------------------

class FbdState(Enum):
    """设备当前所处模式。"""

    ADB = "adb"            # 已开机进系统
    FASTBOOT = "fastboot"  # bootloader
    FASTBOOTD = "fastbootd"  # 用户空间 fastbootd
    NONE = "none"          # 没检测到


class FbdRecommendation(Enum):
    """诊断结论 —— 该走哪条刷写路径的「建议」（不是自动执行）。"""

    NORMAL = "normal"          # 模式正常，走普通/强力线刷即可
    REPAIR_FBD = "repair_fbd"  # 能进 BL、进不了 FBD → 建议「修复 FastbootD」（核心=刷 recovery）
    FBD_ONLY = "fbd_only"      # 只剩 FBD、BL 永久不可用 → 「仅 FastbootD」（禁碰 BL）
    RESCUE_ABL = "rescue_abl"  # BL 秒退/进不去但 FBD 可用 → 用 FBD 刷旧 abl 救回 bootloader
    UNKNOWN = "unknown"        # 信息不足，无法判断，给排查建议


class BlSymptom(Enum):
    """用户声明的 bootloader 症状（只读诊断无法自动判定，必须问用户）。"""

    NONE = "none"                    # bootloader 正常
    CANNOT_ENTER = "cannot_enter"    # 完全进不了 bootloader
    INSTANT_EXIT = "instant_exit"    # 能进但秒退（ColorOS 封 fastboot 典型症状）


@dataclass
class FbdDiagnosis:
    """FastbootD 只读诊断结果。**不写设备、不重启**，纯读取。"""

    state: FbdState
    is_fastbootd: bool = False
    current_slot: str | None = None
    product: str | None = None
    recommendation: FbdRecommendation = FbdRecommendation.UNKNOWN
    summary: str = ""
    notes: list[str] = field(default_factory=list)

    def to_lines(self) -> list[str]:
        lines = [f"设备模式: {self.state.value}" + ("（FastbootD 用户空间）" if self.is_fastbootd else "")]
        if self.current_slot:
            lines.append(f"当前槽位: _{self.current_slot}")
        if self.product:
            lines.append(f"平台/产品: {self.product}")
        lines.append(f"诊断结论: {self.recommendation.value}")
        if self.summary:
            lines.append(f"说明: {self.summary}")
        for n in self.notes:
            lines.append(f"  · {n}")
        return lines


def diagnose_fbd(
    *,
    adb: AdbRunner | None = None,
    fb: FastbootRunner | None = None,
    bl_declared_dead: bool = False,
    bl_symptom: "BlSymptom" = None,  # type: ignore[assignment]
) -> FbdDiagnosis:
    """只读诊断设备处于哪种刷写场景，给出「该走哪条路」的建议。

    这是「仅 FastbootD / 修复 FastbootD / 刷旧 abl 救 bootloader」几条救砖
    路径的分流探针（对齐紫罗兰用 getvar is-userspace 作为分流根节点的做法；
    机制依据：fastbootd 的 HAL 打包在 recovery 镜像里，见 docs/research/
    fastbootd-rescue-factcheck.md 与 AOSP Move fastboot to userspace）。

    设计铁律（安全）：
      · **纯只读**：绝不调用 reboot / flash / erase / set-active 等任何写/重启命令。
      · 绝不发 `reboot bootloader` 去「试探」BL 是否可用 —— 对「仅 FBD」设备那会
        把它唯一的救命通道踢掉、彻底失联（深研报告红线）。
      · 因此「BL 是否可进」只能靠用户声明（bl_declared_dead / bl_symptom）。

    参数：
      adb/fb：可注入的 runner（便于测试与复用）；为 None 时按现状探测。
      bl_declared_dead：用户/历史是否已声明「这台机进不了 bootloader」
        （丢 OCDT / 格机 / 换主板，永久不可用）。FBD_ONLY 判定的关键输入。
      bl_symptom：用户声明的 bootloader 症状。INSTANT_EXIT（能进但秒退，
        典型 ColorOS 16/17 封 fastboot）→ 可用「在 FBD 内刷旧版 abl」救回
        bootloader（RESCUE_ABL）；CANNOT_ENTER 且 bl_declared_dead → FBD_ONLY。

    返回：FbdDiagnosis（含 state / recommendation / 人类可读 summary）。
    """
    if bl_symptom is None:
        bl_symptom = BlSymptom.NONE

    # -- 1) 判定当前模式（只读）--
    state = FbdState.NONE
    is_fbd = False

    if adb is not None:
        try:
            for _s, st in adb.devices().items():
                if st == "device":
                    state = FbdState.ADB
                    break
        except Exception:  # noqa: BLE001
            pass

    if state is FbdState.NONE and fb is not None:
        try:
            if fb.devices():
                is_fbd = fb.is_fastbootd()
                state = FbdState.FASTBOOTD if is_fbd else FbdState.FASTBOOT
        except Exception:  # noqa: BLE001
            pass

    diag = FbdDiagnosis(state=state, is_fastbootd=is_fbd)

    # -- 2) 只读补一些上下文（拿不到就跳过，不报错）--
    if fb is not None and state in (FbdState.FASTBOOT, FbdState.FASTBOOTD):
        try:
            slot = fb.getvar("current-slot")
            if slot:
                diag.current_slot = slot.lstrip("_")
        except Exception:  # noqa: BLE001
            pass
        try:
            diag.product = fb.getvar("product")
        except Exception:  # noqa: BLE001
            pass

    # -- 3) 分流判断 --
    if state is FbdState.ADB:
        diag.recommendation = FbdRecommendation.NORMAL
        diag.summary = "设备已开机进系统，无需救砖。要刷机请先进 fastboot/fastbootd。"
    elif state is FbdState.FASTBOOTD:
        if bl_symptom is BlSymptom.INSTANT_EXIT:
            # ColorOS 16/17 封 fastboot 典型症状：能进 BL 但秒退；但 FBD 还能进。
            # 救法：在 FBD 内刷「旧版本、当前槽」的 abl 即可找回 bootloader。
            diag.recommendation = FbdRecommendation.RESCUE_ABL
            diag.summary = (
                "设备在 FastbootD，且你描述 bootloader 会「秒退」（典型 ColorOS 16/17 "
                "封堵 fastboot）→ 建议用【FastbootD 刷旧版 abl】救回 bootloader："
                "只刷当前槽 abl_a（版本必须对得上、且 abl 可回退）。"
            )
            diag.notes.append(
                "风险：各机型 abl 不通用（如一加13 用 C.93、13T 用 C.58），"
                "用错版本=黑砖；ARB 熔断/新 abl 锁死的机型此路不通。"
            )
            diag.notes.append("只刷当前槽 abl_a，不要切槽、不要动 B 槽、不要刷 ocdt/persist。")
        elif bl_declared_dead:
            diag.recommendation = FbdRecommendation.FBD_ONLY
            diag.summary = (
                "设备已在 FastbootD 且你声明无法进入 Bootloader → "
                "建议【仅 FastbootD】模式：只在 FBD 内重建并刷 super 逻辑分区，"
                "**绝不写 bootloader 侧**。"
            )
            diag.notes.append("铁律：本模式全程不发 reboot bootloader / 不写 abl/xbl/ocdt 等 BL 侧分区。")
        else:
            diag.recommendation = FbdRecommendation.NORMAL
            diag.summary = (
                "设备在 FastbootD。若你能正常进 Bootloader，可走普通线刷；"
                "若 bootloader 秒退/进不去，请再跑一次并选择相应症状。"
            )
    elif state is FbdState.FASTBOOT:
        # 已在 BL 说明 BL 可用；至于能否进 FBD，只读无法可靠判断（不敢 reboot 探）。
        diag.recommendation = FbdRecommendation.NORMAL
        diag.summary = (
            "设备在 Fastboot（bootloader，说明 BL 可用）。"
            "若线刷时进不了 FastbootD（卡住/超时），可用「修复 FastbootD」——"
            "核心是刷回 recovery（fastbootd 寄生在 recovery 里）。"
        )
        diag.notes.append("提示：本诊断只读，不主动重启去测 FBD 可达性（避免误伤）。")
    else:
        diag.recommendation = FbdRecommendation.UNKNOWN
        diag.summary = "未检测到设备。请检查数据线 / USB 调试 / adb、fastboot 是否可用。"

    return diag


# --------------------------------------------------------------------------
# 前置检查
# --------------------------------------------------------------------------

@dataclass
class PreflightIssue:
    severity: str  # "error" | "warn" | "info"
    message: str


def preflight(
    plan: FlashPlan,
    image_dir: Path,
    device_parts: set[str] | None = None,
    super_size: int | None = None,
) -> list[PreflightIssue]:
    """刷写前检查，返回问题列表。任何 error 都不应开始刷写。"""
    issues: list[PreflightIssue] = []

    # 1) 所有需要的镜像都存在且非空
    missing = []
    empty = []
    for op in plan.active_ops:
        if not op.image:
            continue
        p = image_dir / op.image
        if not p.exists():
            missing.append(op.image)
        elif p.stat().st_size == 0:
            empty.append(op.image)
    if missing:
        issues.append(
            PreflightIssue("error", f"缺少 {len(missing)} 个镜像: {', '.join(missing[:5])}")
        )
    if empty:
        issues.append(
            PreflightIssue("error", f"{len(empty)} 个镜像为空: {', '.join(empty[:5])}")
        )

    # 2) 危险分区提醒
    dangerous = [op.partition for op in plan.active_ops if op.risk is Risk.DANGER]
    if dangerous:
        issues.append(
            PreflightIssue(
                "warn",
                f"{len(dangerous)} 个 bootloader 链分区将被刷写: {', '.join(dangerous)}",
            )
        )

    # 2.5) OCDT（engineering_cdt）专项提醒：这是机独有校准分区，
    # 用非本机的会导致进不了 Fastboot，损坏后售后也无法修复。
    ocdt = [
        op.partition
        for op in plan.active_ops
        if op.partition in ("engineering_cdt", "ocdt")
    ]
    if ocdt:
        issues.append(
            PreflightIssue(
                "warn",
                f"将刷写 {'/'.join(ocdt)}（OCDT 机独有校准分区）："
                "必须确认镜像来自本机同型号同版本的全量包，"
                "用别机的会导致进不了 Fastboot，损坏后只能拆字库",
            )
        )

    # 3) 磁盘剩余空间
    try:
        free = shutil.disk_usage(image_dir).free
        issues.append(
            PreflightIssue("info", f"镜像目录剩余空间 {free / 2**30:.1f} GB")
        )
    except OSError:
        pass

    # 4) super 空间校验（逻辑分区）
    logical = [op for op in plan.active_ops if op.phase is Phase.FASTBOOTD]
    if logical:
        need = sum(op.size for op in logical)
        if super_size:
            if need > super_size:
                issues.append(
                    PreflightIssue(
                        "error",
                        f"super 空间不足：逻辑分区需要 "
                        f"{need / 2**30:.2f} GB，但 super 只有 "
                        f"{super_size / 2**30:.2f} GB。"
                        f"请去掉 --both-slots（逻辑分区不能双槽刷写）",
                    )
                )
            else:
                issues.append(
                    PreflightIssue(
                        "info",
                        f"super 空间检查通过：需要 {need / 2**30:.2f} GB / "
                        f"可用 {super_size / 2**30:.2f} GB",
                    )
                )

    # 5) 计划分区是否都在设备上
    if device_parts:
        unknown = [
            op.partition
            for op in plan.active_ops
            if op.phase is Phase.FASTBOOT
            and op.partition not in device_parts
            and f"{op.partition}_a" not in device_parts
        ]
        if unknown:
            issues.append(
                PreflightIssue(
                    "warn", f"{len(unknown)} 个分区在设备上未找到: {', '.join(unknown[:5])}"
                )
            )

    return issues


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def _recovery_hint(part: str, phase: Phase, msg: str = "") -> str:
    # FastbootD 阶段出现空间不足（resize 失败），最常见的成因是「没有重建
    # 逻辑分区、直接覆盖」时目标镜像比原分区大。此时正确做法是改用
    # 「强力线刷」（delete/create 重建逻辑分区），而不是重试同一条命令。
    low = msg.lower()
    if phase is Phase.FASTBOOTD and (
        "not enough space" in low or "resize partition" in low
    ):
        return (
            f"{part}: super 空间不足（多为未重建逻辑分区直接覆盖大镜像）\n"
            f"      → 改用「强力线刷」模式（会先 delete/create 重建逻辑分区），"
            f"或手动：fastboot reboot fastboot 后 "
            f"fastboot delete-logical-partition {part} 再重刷"
        )
    if phase is Phase.FASTBOOT:
        return f"fastboot flash {part} {part}.img"
    return f"fastboot flash {part} {part}.img   # 需先 fastboot reboot fastboot"


#: 超过这个大小的分区改用分块传输（-S）。
#: 中小分区没必要分块，反而多一层开销。
SPARSE_THRESHOLD = 512 * 1024 * 1024  # 512 MB

#: 分块大小。100M 是社区验证过的稳妥值：
#: 既能让单次传输窗口足够小，又不会因块太小而显著变慢。
SPARSE_CHUNK = "100M"


class FlashExecutor:
    """刷写执行器。"""

    def __init__(
        self,
        plan: FlashPlan,
        image_dir: Path,
        *,
        fb: FastbootRunner | None = None,
        adb: AdbRunner | None = None,
        dry_run: bool = False,
        on_step: Callable[[StepResult], None] | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
        on_mode_change: Callable[[Phase], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
        on_progress_pending: Callable[[str, int, float], None] | None = None,
        on_progress_done: Callable[[], None] | None = None,
        wipe_data: bool = False,
        both_slots: bool = False,
        recreate_logical: bool = True,
        target_slot: str | None = None,
        fbd_only: bool = False,
    ) -> None:
        self.plan = plan
        self.image_dir = image_dir
        self.fb = fb
        self.adb = adb
        self.dry_run = dry_run
        self.on_step = on_step
        self.on_progress = on_progress
        self.on_mode_change = on_mode_change
        self.on_notice = on_notice
        self.on_progress_pending = on_progress_pending
        self.on_progress_done = on_progress_done
        self.wipe_data = wipe_data
        self.both_slots = both_slots
        self.recreate_logical = recreate_logical
        #: 若指定，进 fastboot 后先 set-active 到该槽再刷（物理分区是裸名，
        #: 落哪个槽由当前激活槽决定）。用于「刷到另一个槽」。
        self.target_slot = target_slot
        #: 「仅 FastbootD」救砖模式：设备进不了 bootloader、只剩 FBD。
        #: 此时**绝不**把设备踢回 bootloader（那会断掉唯一救命通道），
        #: 且**只跑 FASTBOOTD 阶段**、跳过 FASTBOOT 阶段。
        self.fbd_only = fbd_only
        self.result = FlashResult(plan=plan)
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    # -- 单步 --------------------------------------------------------

    def _flash_one(self, op: FlashOp) -> StepResult:
        if not op.image:
            return StepResult(op.partition, StepStatus.SKIPPED, message="无镜像文件")

        img = self.image_dir / op.image
        size = img.stat().st_size if img.exists() else 0

        start = time.time()
        if self.dry_run:
            return StepResult(
                op.partition,
                StepStatus.OK,
                duration=0.0,
                size=size,
                message=f"[预演] fastboot flash {op.partition} {img.name}",
            )

        assert self.fb is not None

        # 大分区刷写耗时长（my_stock 可能 100 秒以上），
        # 期间必须持续给用户反馈，否则用户会以为卡死而拔线。
        stop_ticker = threading.Event()
        ticker = None
        if size > 20 * 1024 * 1024:
            ticker = threading.Thread(
                target=self._tick_progress,
                args=(op.partition, size, start, stop_ticker),
                daemon=True,
            )
            ticker.start()
        try:
            # 大分区（如 super 动辄几 GB）一次性传输容易撑爆 USB 缓冲，
            # 表现为「卡在 waiting for device」后超时失败。
            # fastboot 的 -S <size> 会自动把镜像切成分块传输，规避这个限制。
            flash_args: list[str] = []
            if size >= SPARSE_THRESHOLD:
                flash_args += ["-S", SPARSE_CHUNK]
            res: RunResult = self.fb.run(
                *flash_args, "flash", op.partition, str(img), timeout=3600
            )
        finally:
            stop_ticker.set()
            if ticker is not None:
                ticker.join(timeout=2)
            if self.on_progress_done:
                self.on_progress_done()
        duration = time.time() - start

        ok = self.fb.ok(res)
        if ok:
            return StepResult(
                op.partition, StepStatus.OK, duration=duration, size=size
            )

        # 提取失败原因
        msg = _extract_error(res)

        # 关键分区未解锁：这不是致命错误，可跳过继续
        if is_critical_locked(msg):
            return StepResult(
                op.partition,
                StepStatus.LOCKED,
                duration=duration,
                size=size,
                message="关键分区未解锁，已跳过",
                recovery_hint="fastboot flashing unlock_critical  # 风险高，谨慎",
            )

        return StepResult(
            op.partition,
            StepStatus.FAILED,
            duration=duration,
            size=size,
            message=msg,
            recovery_hint=_recovery_hint(op.partition, op.phase, msg),
        )

    def _tick_progress(
        self, partition: str, size: int, start: float, stop: threading.Event
    ) -> None:
        """刷写进行中定期回调，让用户看到"还在动"。"""
        if not self.on_progress_pending:
            return
        while not stop.wait(1.0):
            self.on_progress_pending(partition, size, start)

    # -- 阶段 --------------------------------------------------------

    def _run_phase(self, phase: Phase) -> bool:
        ops = [op for op in self.plan.active_ops if op.phase is phase]
        if not ops:
            return True

        for op in ops:
            if self._stop:
                self.result.aborted = True
                return False

            # 逻辑分区：先删除再重建，确保容量正确、清理 COW 层
            if (
                phase is Phase.FASTBOOTD
                and self.recreate_logical
                and not self.dry_run
            ):
                rebuild_note = self._recreate_logical_partition(op)
                if rebuild_note is not None:
                    sr = StepResult(
                        op.partition,
                        StepStatus.FAILED,
                        message=rebuild_note,
                        recovery_hint=self._logical_recovery_hint(op),
                    )
                    self.result.steps.append(sr)
                    if self.on_step:
                        self.on_step(sr)
                    return False

            sr = self._flash_one(op)
            self.result.steps.append(sr)
            if self.on_step:
                self.on_step(sr)

            if sr.status is StepStatus.FAILED:
                # 致命失败：立即中止，不再往下刷
                return False
            # LOCKED（关键分区未解锁）不中止，继续下一分区

            # 双槽：再刷另一个槽位
            if self.both_slots and sr.status is StepStatus.OK and not self.dry_run:
                sr2 = self._flash_other_slot(op)
                if sr2 is not None:
                    self.result.steps.append(sr2)
                    if self.on_step:
                        self.on_step(sr2)
                    if sr2.status is StepStatus.FAILED:
                        return False

        return True

    # -- 逻辑分区重建 -------------------------------------------------

    def _target_slot_suffix(self) -> str:
        """当前目标槽位后缀，如 "_a"。"""
        slot = self.plan.slot or "a"
        return slot if slot.startswith("_") else f"_{slot}"

    def _recreate_logical_partition(self, op: FlashOp) -> str | None:
        """删除并重建逻辑分区，返回 None 表示成功，否则返回错误信息。

        为什么需要：直接 flash 一个逻辑分区时，若目标分区不存在或容量不足会失败；
        另外虚拟 A/B 的 COW 快照层（xxx_a-cow）会残留旧数据。

        关键：**只重建目标槽**（当前要写入的那个槽，如 _a）。
        原因——super 是单个物理分区，两个槽的逻辑分区共用同一空间。
        若 _a/_b 都按镜像尺寸 create，空间需求翻倍，反而会触发
        "Not enough space to resize partition"。而 Lrom 刷的是当前槽的裸名
        （fastboot flash odm 落到 odm_<当前槽>），根本不会写另一槽，
        所以另一槽的副本纯属浪费空间，不建。

        做法参照 Universal_Flasher（delete 后按镜像字节数换算块数 create）：
          delete-logical-partition <name>_<目标槽> [-cow] [<name>_<另一槽> ...]
          create-logical-partition <name>_<目标槽> <size_blocks>
        """
        assert self.fb is not None
        if not op.size:
            return None

        base = op.partition
        # super 内的分区名通常不带槽位
        if base.endswith(("_a", "_b")):
            base = base[:-2]

        target_suffix = self._target_slot_suffix()  # 如 "_a"
        other_suffix = "_b" if target_suffix == "_a" else "_a"
        target = f"{base}{target_suffix}"
        other = f"{base}{other_suffix}"

        size_blocks = max(1, op.size // 4096)
        errors: list[str] = []

        # 删除旧的（含两槽的普通分区与 COW 层），释放空间；不存在会报错，忽略
        for name in (target, other, f"{target}-cow", f"{other}-cow"):
            self.fb.run("delete-logical-partition", name, timeout=60)

        # 只重建目标槽（另一槽不写，不占空间）
        res = self.fb.run(
            "create-logical-partition", target, str(size_blocks), timeout=120
        )
        if not self.fb.ok(res):
            errors.append(f"create {target}: {_extract_error(res)}")

        if errors:
            return " ; ".join(errors[:2])
        return None

    def _logical_recovery_hint(self, op: FlashOp) -> str:
        base = op.partition
        if base.endswith(("_a", "_b")):
            base = base[:-2]
        blocks = max(1, op.size // 4096)
        target = f"{base}{self._target_slot_suffix()}"
        return (
            f"fastboot create-logical-partition {target} {blocks} 然后 "
            f"fastboot flash {base} {base}.img"
        )

    # -- 双槽刷写 -----------------------------------------------------

    def _flash_other_slot(self, op: FlashOp) -> StepResult | None:
        """把同一镜像再刷到另一个槽位。

        ⚠️ 只用于物理分区。逻辑分区（super 内）绝不能双槽刷写：
        super 是单个物理分区，两个槽位的逻辑分区共用同一空间。
        双槽刷写会让空间需求翻倍，必然触发
        "Not enough space to resize partition"。
        """
        assert self.fb is not None
        if op.partition in ("super", "userdata", "metadata", "misc"):
            return None  # 非槽位分区
        # 逻辑分区禁止双槽（关键安全约束）
        if op.phase is Phase.FASTBOOTD:
            return None
        if op.partition.endswith("_a") or op.partition.endswith("_b"):
            base = op.partition[:-2]
        else:
            base = op.partition

        # 判断设备是否存在对应槽位分区：直接尝试，失败则静默跳过
        img = self.image_dir / (op.image or f"{base}.img")
        if not img.exists():
            return None

        other = f"{base}_b" if self._target_slot_suffix() == "_a" else f"{base}_a"
        start = time.time()
        res = self.fb.run("flash", other, str(img), timeout=3600)
        duration = time.time() - start

        if self.fb.ok(res):
            return StepResult(
                other,
                StepStatus.OK,
                duration=duration,
                size=img.stat().st_size,
                message="双槽刷写",
                slot_written=other,
            )
        msg = _extract_error(res)
        if is_critical_locked(msg):
            return StepResult(
                other, StepStatus.LOCKED, duration=duration, message="关键分区未解锁，已跳过"
            )
        return StepResult(
            other,
            StepStatus.FAILED,
            duration=duration,
            message=msg,
            recovery_hint=f"fastboot flash {other} {img.name}",
        )

    def run(self) -> FlashResult:
        self.result.started_at = time.time()

        # ---- 「仅 FastbootD」救砖分支 ----
        # 设备进不了 bootloader、只剩 FBD。绝不能 _ensure_fastboot()（那会
        # fastboot reboot bootloader 把唯一救命通道踢掉），也不跑 FASTBOOT 阶段，
        # 只在 FBD 内刷逻辑分区。见 docs/research/violet-fbd-only-deep.md。
        if self.fbd_only:
            return self._run_fbd_only()

        # ---- 步骤 0：确保设备处于 Fastboot 模式 ----
        # 设备可能处于 ADB（开机）、已是 Fastboot，或 FastbootD。
        # 必须先统一到 Fastboot，否则所有 fastboot flash 都会失败。
        if not self.dry_run:
            if not self._ensure_fastboot():
                self.result.error = "无法进入 Fastboot 模式，已中止"
                self.result.finished_at = time.time()
                return self.result
            # 进 fastboot 后若需刷到指定槽，先切槽再刷
            if self.target_slot:
                assert self.fb is not None
                self._notify_mode(Phase.FASTBOOT)
                sr = self._set_active_slot(self.target_slot)
                if sr is not None and not sr:
                    self.result.error = f"切换到 {self.target_slot} 槽失败，已中止"
                    self.result.finished_at = time.time()
                    return self.result
        else:
            self._notify_mode(Phase.FASTBOOT)

        # ---- 阶段一：Fastboot（物理分区）----
        if not self._run_phase(Phase.FASTBOOT):
            self.result.finished_at = time.time()
            if not self.result.aborted:
                self.result.error = "Fastboot 阶段失败，已中止"
            return self.result

        # ---- 阶段二：FastbootD（逻辑分区）----
        fbd_ops = [op for op in self.plan.active_ops if op.phase is Phase.FASTBOOTD]
        if fbd_ops:
            self._notify_mode(Phase.FASTBOOTD)
            if not self.dry_run:
                if not self._enter_fastbootd():
                    self.result.error = "无法进入 FastbootD 模式，已中止"
                    self.result.finished_at = time.time()
                    return self.result

            if not self._run_phase(Phase.FASTBOOTD):
                self.result.finished_at = time.time()
                if not self.result.aborted:
                    self.result.error = "FastbootD 阶段失败，已中止"
                return self.result

        # ---- 可选：清空用户数据 ----
        if self.wipe_data and not self.dry_run:
            if not self._wipe_userdata():
                self.result.error = "清空用户数据失败，已中止"
                self.result.finished_at = time.time()
                return self.result

        self.result.finished_at = time.time()
        return self.result

    def _run_fbd_only(self) -> FlashResult:
        """「仅 FastbootD」执行：只刷逻辑分区，绝不触碰 bootloader 侧。

        前置（安全红线）：
          · 设备必须**已在 FastbootD**（is-userspace=yes）。绝不应由本方法
            主动 reboot 去切模式——那可能断掉唯一救命通道。
          · 计划里不允许出现任何 FASTBOOT 阶段分区（物理/引导链）；出现即拒绝，
            防止误把 bootloader 侧分区刷进坏 BL 的设备。
        """
        # 1) 计划里绝不能有 FASTBOOT 阶段分区
        bl_ops = [op for op in self.plan.active_ops if op.phase is Phase.FASTBOOT]
        if bl_ops:
            names = ", ".join(op.partition for op in bl_ops[:6])
            self.result.error = (
                f"仅 FastbootD 模式禁止刷 bootloader 侧分区（{names} 等 "
                f"{len(bl_ops)} 个）。请只选择逻辑分区。已中止以防二次变砖。"
            )
            self.result.finished_at = time.time()
            return self.result

        # 2) 确认设备已在 FastbootD（只读 getvar，不 reboot）
        if not self.dry_run:
            assert self.fb is not None
            try:
                in_fbd = bool(self.fb.devices()) and self.fb.is_fastbootd()
            except Exception:  # noqa: BLE001
                in_fbd = False
            if not in_fbd:
                if self.on_notice:
                    self.on_notice(
                        "仅 FastbootD 模式要求设备已处于 FastbootD（is-userspace=yes），"
                        "但当前未检测到。\n"
                        "  请先手动让设备进入 FastbootD 后重试；本模式不会替你 reboot，"
                        "以免断掉唯一可用的通道。"
                    )
                self.result.error = "设备不在 FastbootD 模式，仅 FastbootD 刷写已中止"
                self.result.finished_at = time.time()
                return self.result

        # 3) 只跑 FASTBOOTD 阶段
        self._notify_mode(Phase.FASTBOOTD)
        if not self._run_phase(Phase.FASTBOOTD):
            self.result.finished_at = time.time()
            if not self.result.aborted:
                self.result.error = "FastbootD 阶段失败，已中止"
            return self.result

        # 4) 清用户数据（可选，同样在 FBD 之后）
        if self.wipe_data and not self.dry_run:
            if not self._wipe_userdata():
                self.result.error = "清空用户数据失败，已中止"
                self.result.finished_at = time.time()
                return self.result

        self.result.finished_at = time.time()
        return self.result


    def _wipe_userdata(self) -> bool:
        """清空用户数据（等同恢复出厂设置）。

        fastboot -w 会擦除 userdata / metadata / cache。
        必须在刷完所有分区之后执行。
        """
        assert self.fb is not None
        res = self.fb.run("-w", timeout=1800)
        return self.fb.ok(res)

    def _notify_mode(self, phase: Phase) -> None:
        """通知上层当前进入哪个阶段（用于打印分隔）。"""
        if self.on_mode_change:
            self.on_mode_change(phase)

    # -- 模式切换 ----------------------------------------------------

    def _ensure_fastboot(self, timeout: float = 180.0) -> bool:
        """确保设备处于 Fastboot（bootloader）模式。

        - 已在 fastboot：直接返回
        - 在 adb（开机）：adb reboot bootloader
        - 在 fastbootd：fastboot reboot bootloader
        """
        state = self._detect_mode()
        if state == "fastboot":
            return True
        if state == "fastbootd":
            # 从 fastbootd 回 bootloader
            assert self.fb is not None
            self.fb.run("reboot", "bootloader", timeout=60)
        elif state == "adb":
            if self.adb is None:
                return False
            res = self.adb.run("reboot", "bootloader", timeout=60)
            if res.exit_code != 0:
                return False
        else:
            # 什么都没检测到，仍尝试等待（用户可能手动进入了）
            pass
        return self._wait_for_fastboot(timeout)

    def _detect_mode(self) -> str:
        """检测当前设备模式：adb / fastboot / fastbootd / none。"""
        if self.adb is not None:
            try:
                for _serial, st in self.adb.devices().items():
                    if st == "device":
                        return "adb"
            except Exception:
                pass
        if self.fb is not None:
            try:
                if self.fb.devices():
                    return "fastbootd" if self.fb.is_fastbootd() else "fastboot"
            except Exception:
                pass
        return "none"

    def _wait_for_fastboot(self, timeout: float) -> bool:
        """轮询等待设备进入 fastboot 模式。"""
        assert self.fb is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop:
                return False
            time.sleep(3)
            try:
                if self.fb.devices():
                    if not self.fb.is_fastbootd():
                        time.sleep(2)  # 给 bootloader 一点时间稳定
                        return True
            except Exception:
                pass
        return False

    def _set_active_slot(self, slot: str) -> bool | None:
        """把活动槽切到指定槽。返回 True 成功 / False 失败 / None 跳过。"""
        if self.fb is None:
            return None
        slot = slot.lstrip("_")
        if slot not in ("a", "b"):
            return None
        res = self.fb.run(f"--set-active={slot}", timeout=120)
        return self.fb.ok(res)

    def _enter_fastbootd(self, timeout: float = 300.0) -> bool:
        """从 fastboot 切换到 fastbootd 并等待就绪。

        注意：OPLUS 设备的 fastbootd 可能显示语言选择界面并暂停，
        此时需要用户手动点选。因此等待期间会周期性输出提示，
        超时后给出明确的处理建议。
        """
        assert self.fb is not None
        self.fb.run("reboot", "fastboot", timeout=60)

        start = time.time()
        deadline = start + timeout
        hinted = False
        while time.time() < deadline:
            if self._stop:
                return False
            time.sleep(3)
            elapsed = time.time() - start

            # 15 秒还没就绪，提示用户可能需要手动操作
            if elapsed > 15 and not hinted and self.on_notice:
                hinted = True
                self.on_notice(
                    "正在等待设备进入 FastbootD…\n"
                    "  如果手机屏幕上出现语言选择/English 界面，请手动点选后继续等待。"
                )

            try:
                serials = self.fb.devices()
            except Exception:
                serials = []
            if serials:
                if self.fb.is_fastbootd():
                    time.sleep(2)
                    return True

        if self.on_notice:
            self.on_notice(
                "等待 FastbootD 超时（{}秒）。请检查手机屏幕：\n"
                "  · 是否停在语言选择界面？点选 English 后重试\n"
                "  · 是否已回到系统？执行 fastboot reboot fastboot 重试\n"
                "  · 数据线是否正常？".format(int(timeout))
            )
        return False


#: fastboot 对关键分区未解锁时的错误特征
_CRITICAL_LOCK_PATTERNS = (
    "not allowed for critical partitions",
    "flashing is not allowed",
    "critical partitions",
)


def is_critical_locked(message: str) -> bool:
    """判断失败原因是否为"关键分区未解锁"。"""
    low = message.lower()
    return any(p in low for p in _CRITICAL_LOCK_PATTERNS)


def _extract_error(res: RunResult) -> str:
    """从 fastboot 输出中提取失败原因。

    注意：fastboot 的错误信息可能跨多行（remote 消息内含换行），
    例如 "FAILED (remote: 'Flashing is not allowed for Critical Partitions.\n')"。
    因此这里对整体文本做搜索，而不是逐行匹配。
    """
    if res.timed_out:
        return "命令超时"

    text = res.combined

    # 优先提取 remote / FAILED 里的实际原因
    for pattern in (
        r"FAILED\s*\(?\s*remote:\s*'?([^'\n]+)",
        r"FAILED\s*\(?([^)\n]+)",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:200]

    for line in text.splitlines():
        ls = line.strip()
        if ls and ls.lower().startswith("error"):
            return ls[:200]

    if res.exit_code != 0:
        # 没有可识别的错误行时，把最后几行非空内容附上
        tail = [x for x in text.splitlines() if x.strip()]
        hint = tail[-1].strip()[:150] if tail else ""
        return f"退出码 {res.exit_code}" + (f": {hint}" if hint else "")

    return text.strip().splitlines()[-1][:200] if text.strip() else "未知错误"


def summarize(result: FlashResult) -> str:
    """生成结果摘要。"""
    lines: list[str] = []
    ok = [s for s in result.steps if s.status is StepStatus.OK]
    fail = result.failed
    skipped = [s for s in result.steps if s.status is StepStatus.SKIPPED]

    locked = [s for s in result.steps if s.status is StepStatus.LOCKED]
    lines.append(f"成功: {len(ok)} 个分区")
    if skipped:
        lines.append(f"跳过: {len(skipped)} 个分区")
    if locked:
        lines.append(f"锁定: {len(locked)} 个分区（关键分区，需 unlock_critical）")
    if fail:
        lines.append(f"失败: {len(fail)} 个分区")
    lines.append(f"耗时: {result.elapsed:.1f} 秒")

    if fail:
        lines.append("")
        lines.append("失败详情:")
        for s in fail:
            lines.append(f"  {s.partition}: {s.message}")
            if s.recovery_hint:
                lines.append(f"    重试: {s.recovery_hint}")

    if result.error:
        lines.append("")
        lines.append(f"中止原因: {result.error}")

    return "\n".join(lines)


def dry_run_report(plan: FlashPlan, image_dir: Path) -> str:
    """生成预演报告：告诉用户"如果现在刷，会发生什么"。

    与真正的执行日志不同，这里不逐条打印分区，而是回答四个关键问题：
      1. 会写入多少数据、分几个阶段
      2. 有没有风险项（危险分区 / 会跳过的分区）
      3. 镜像是否完整（缺失 / 空文件）
      4. 数据会不会丢
    """
    lines: list[str] = []

    fb_ops = plan.by_phase(Phase.FASTBOOT)
    fbd_ops = plan.by_phase(Phase.FASTBOOTD)
    active = plan.active_ops

    # ---- 1. 规模 ----
    lines.append("【刷写规模】")
    lines.append(f"  阶段一 Fastboot   : {len(fb_ops):>2} 个分区")
    lines.append(f"  阶段二 FastbootD  : {len(fbd_ops):>2} 个逻辑分区")
    lines.append(f"  合计写入          : {plan.total_bytes / 2**30:.2f} GB")

    # ---- 2. 镜像完整性 ----
    missing, empty = [], []
    for op in active:
        if not op.image:
            continue
        p = image_dir / op.image
        if not p.exists():
            missing.append(op.image)
        elif p.stat().st_size == 0:
            empty.append(op.image)

    lines.append("")
    lines.append("【镜像完整性】")
    if missing or empty:
        if missing:
            lines.append(f"  ✗ 缺失 {len(missing)} 个: {', '.join(missing[:5])}")
        if empty:
            lines.append(f"  ✗ 空文件 {len(empty)} 个: {', '.join(empty[:5])}")
        lines.append("  → 现在刷会失败，请先重新解包")
    else:
        total_files = len([op for op in active if op.image])
        lines.append(f"  ✓ {total_files} 个镜像文件全部存在且非空")

    # ---- 3. 风险项 ----
    lines.append("")
    lines.append("【风险项】")
    danger = [op.partition for op in active if op.risk is Risk.DANGER]
    blocked = [op.partition for op in plan.ops if op.skip and op.risk is Risk.BLOCKED]

    if not danger and not blocked:
        lines.append("  ✓ 无风险项")
    if danger:
        lines.append(
            f"  ! {len(danger)} 个 bootloader 链分区将被写入："
        )
        lines.append(f"      {', '.join(danger[:10])}")
        lines.append("      这些是官方全量包的正常内容，但写错会导致无法开机")
    if blocked:
        lines.append(f"  · {len(blocked)} 个分区将被跳过（保护 IMEI/指纹/密钥）：")
        lines.append(f"      {', '.join(blocked[:10])}")

    # ---- 4. 数据影响 ----
    lines.append("")
    lines.append("【数据影响】")
    skipped_parts = [op.partition for op in plan.ops if op.skip]
    active_names = {op.partition for op in active}
    if "userdata" in active_names:
        lines.append("  ✗ userdata 将被刷写 —— 会清空所有用户数据")
    else:
        lines.append("  ✓ 不会动 userdata，用户数据保留")

    if skipped_parts:
        lines.append(
            f"  说明: {len(skipped_parts)} 个分区被跳过，"
            f"实际写入 {len(active)} 个分区"
        )

    # ---- 5. 结论 ----
    lines.append("")
    lines.append("【结论】")
    if missing or empty:
        lines.append("  ✗ 现在不能刷 —— 镜像不完整")
    elif plan.blocked_ops:
        lines.append("  ✓ 可以刷（部分保护分区会跳过）")
    else:
        lines.append("  ✓ 可以安全刷写")

    return "\n".join(lines)
