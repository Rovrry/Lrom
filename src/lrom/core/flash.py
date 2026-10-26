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
            and all(s.status in (StepStatus.OK, StepStatus.SKIPPED) for s in self.steps)
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
    ) -> None:
        self.plan = plan
        self.image_dir = image_dir
        self.fb = fb
        self.adb = adb
        self.dry_run = dry_run
        self.on_step = on_step
        self.on_progress = on_progress
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

        return True

    def run(self) -> FlashResult:
        self.result.started_at = time.time()

        # 阶段一：Fastboot
        if not self._run_phase(Phase.FASTBOOT):
            self.result.finished_at = time.time()
            if not self.result.aborted:
                self.result.error = "Fastboot 阶段失败，已中止"
            return self.result

        # 切换到 FastbootD
        fbd_ops = [op for op in self.plan.active_ops if op.phase is Phase.FASTBOOTD]
        if fbd_ops:
            if self.dry_run:
                for op in fbd_ops:
                    sr = self._flash_one(op)
                    self.result.steps.append(sr)
                    if self.on_step:
                        self.on_step(sr)
            else:
                if not self._enter_fastbootd():
                    self.result.error = "无法进入 FastbootD 模式，已中止"
                    self.result.finished_at = time.time()
                    return self.result

                if not self._run_phase(Phase.FASTBOOTD):
                    self.result.finished_at = time.time()
                    if not self.result.aborted:
                        self.result.error = "FastbootD 阶段失败，已中止"
                    return self.result

        self.result.finished_at = time.time()
        return self.result

    def _enter_fastbootd(self, timeout: float = 180.0) -> bool:
        """切换到 fastbootd 并等待设备就绪。"""
        assert self.fb is not None
        self.fb.run("reboot", "fastboot", timeout=60)

        deadline = time.time() + timeout
        while time.time() < deadline:
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


def _extract_error(res: RunResult) -> str:
    """从 fastboot 输出中提取失败原因。"""
    text = res.combined
    for line in text.splitlines():
        ls = line.strip()
        if not ls:
            continue
        upper = ls.upper()
        if upper.startswith("FAILED") or ls.lower().startswith("error"):
            return ls[:200]
    if res.timed_out:
        return "命令超时"
    if res.exit_code != 0:
        return f"退出码 {res.exit_code}"
    return text.strip().splitlines()[-1][:200] if text.strip() else "未知错误"


def summarize(result: FlashResult) -> str:
    """生成结果摘要。"""
    lines: list[str] = []
    ok = [s for s in result.steps if s.status is StepStatus.OK]
    fail = result.failed
    skipped = [s for s in result.steps if s.status is StepStatus.SKIPPED]

    lines.append(f"成功: {len(ok)} 个分区")
    if skipped:
        lines.append(f"跳过: {len(skipped)} 个分区")
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
