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
    plan: FlashPlan, image_dir: Path, device_parts: set[str] | None = None
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

    # 3) 磁盘剩余空间
    try:
        free = shutil.disk_usage(image_dir).free
        issues.append(
            PreflightIssue("info", f"镜像目录剩余空间 {free / 2**30:.1f} GB")
        )
    except OSError:
        pass

    # 4) 计划分区是否都在设备上
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
        wipe_data: bool = False,
    ) -> None:
        self.plan = plan
        self.image_dir = image_dir
        self.fb = fb
        self.adb = adb
        self.dry_run = dry_run
        self.on_step = on_step
        self.on_progress = on_progress
        self.on_mode_change = on_mode_change
        self.wipe_data = wipe_data
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
        res: RunResult = self.fb.run(
            "flash", op.partition, str(img), timeout=3600
        )
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

    # -- 阶段 --------------------------------------------------------

    def _run_phase(self, phase: Phase) -> bool:
        ops = [op for op in self.plan.active_ops if op.phase is phase]
        if not ops:
            return True

        for op in ops:
            if self._stop:
                self.result.aborted = True
                return False

            sr = self._flash_one(op)
            self.result.steps.append(sr)
            if self.on_step:
                self.on_step(sr)

            if sr.status is StepStatus.FAILED:
                # 致命失败：立即中止，不再往下刷
                return False
            # LOCKED（关键分区未解锁）不中止，继续下一分区

        return True

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

    def _enter_fastbootd(self, timeout: float = 180.0) -> bool:
        """从 fastboot 切换到 fastbootd 并等待就绪。"""
        assert self.fb is not None
        self.fb.run("reboot", "fastboot", timeout=60)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop:
                return False
            time.sleep(3)
            try:
                serials = self.fb.devices()
            except Exception:
                serials = []
            if serials:
                if self.fb.is_fastbootd():
                    time.sleep(2)
                    return True
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
