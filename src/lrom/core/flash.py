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

def _recovery_hint(part: str, phase: Phase) -> str:
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
            recovery_hint=_recovery_hint(op.partition, op.phase),
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
        做法参照 Universal_Flasher：
          delete-logical-partition <name>_a / _b / _a-cow / _b-cow
          create-logical-partition <name>_a <size_blocks>
          create-logical-partition <name>_b <size_blocks>
        """
        assert self.fb is not None
        if not op.size:
            return None

        base = op.partition
        # super 内的分区名通常不带槽位
        if base.endswith(("_a", "_b")):
            base = base[:-2]

        size_blocks = max(1, op.size // 4096)
        errors: list[str] = []

        # 删除所有 4 种命名（已存在则删，不存在会报错，忽略）
        for name in (f"{base}_a", f"{base}_b", f"{base}_a-cow", f"{base}_b-cow"):
            self.fb.run("delete-logical-partition", name, timeout=60)

        # 重建两个槽位的逻辑分区
        for name in (f"{base}_a", f"{base}_b"):
            res = self.fb.run(
                "create-logical-partition", name, str(size_blocks), timeout=120
            )
            if not self.fb.ok(res):
                errors.append(f"create {name}: {_extract_error(res)}")

        if errors:
            return " ; ".join(errors[:2])
        return None

    def _logical_recovery_hint(self, op: FlashOp) -> str:
        base = op.partition
        if base.endswith(("_a", "_b")):
            base = base[:-2]
        blocks = max(1, op.size // 4096)
        return (
            f"fastboot create-logical-partition {base}_a {blocks} 然后 "
            f"fastboot flash {base}_a {base}.img"
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
