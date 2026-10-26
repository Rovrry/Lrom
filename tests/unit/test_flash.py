"""刷写执行器测试（不触碰真实设备）。"""

from __future__ import annotations

import pytest

from lrom.core.flash import (
    FlashExecutor,
    StepStatus,
    _extract_error,
    preflight,
    summarize,
)
from lrom.core.payload import OpType
from lrom.core.plan import Phase, Risk, build_plan
from lrom.device.runner import FastbootRunner, RunResult

from .test_payload import make_manifest, make_op, make_partition, parse_manifest


class FakeFastboot:
    """模拟 fastboot，可配置每个分区的成败。"""

    _FAIL_PATTERNS = FastbootRunner._FAIL_PATTERNS

    def __init__(self, fail_on: set[str] | None = None, fail_output: str = "FAILED (remote: nope)"):
        self.fail_on = fail_on or set()
        self.fail_output = fail_output
        self.calls: list[list[str]] = []

    def run(self, *args, timeout=None):
        self.calls.append(list(args))
        if args and args[0] == "flash":
            part = args[1]
            if part in self.fail_on:
                return RunResult(
                    argv=list(args),
                    exit_code=0,  # 故意用 0：模拟 fastboot 的坑
                    stdout=f"Sending '{part}'  FAILED (remote: 'no space')\n",
                )
            return RunResult(
                argv=list(args),
                exit_code=0,
                stdout=f"Sending '{part}'  OKAY\nWriting '{part}'  OKAY\nFinished.\n",
            )
        if args and args[0] == "reboot":
            return RunResult(argv=list(args), exit_code=0, stdout="rebooting\n")
        return RunResult(argv=list(args), exit_code=0)

    def ok(self, result: RunResult) -> bool:
        # 直接复用真实 FastbootRunner 的判定逻辑，保证测的是生产代码
        from lrom.device.runner import FastbootRunner

        return FastbootRunner.ok(self, result)

    def devices(self):
        return ["FAKE"]

    def is_fastbootd(self):
        # 默认视为已在 fastboot（非 fastbootd），这样 _ensure_fastboot 直接通过
        return self.in_fastbootd

    in_fastbootd = False


def make_executor(plan, tmp_path, fb, *, dry_run=False):
    """构造执行器，并关闭真实的模式切换等待（测试里由 FakeFastboot 直接返回）。"""
    ex = FlashExecutor(plan, tmp_path, fb=fb, dry_run=dry_run)
    # 已在 fastboot 模式：_ensure_fastboot 会立即返回 True
    ex._detect_mode = lambda: "fastboot"
    # 模拟切换成功，不真的等 180 秒
    ex._enter_fastbootd = lambda timeout=180.0: True
    return ex


def make_plan(tmp_path, *, with_logical=True):
    mf = parse_manifest(
        make_manifest(
            [
                make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
                make_partition("modem", [make_op(OpType.REPLACE_XZ, data_length=100)]),
                *(
                    [
                        make_partition(
                            "system", [make_op(OpType.REPLACE_XZ, data_length=100)]
                        )
                    ]
                    if with_logical
                    else []
                ),
            ]
        )
    )
    plan = build_plan(mf, package_path="x.zip")
    # 造出镜像文件
    for op in plan.active_ops:
        p = tmp_path / f"{op.partition}.img"
        p.write_bytes(b"\x00" * max(op.size, 16))
    return plan


def test_dry_run_never_calls_flash(tmp_path):
    plan = make_plan(tmp_path)
    fb = FakeFastboot()
    ex = make_executor(plan, tmp_path, fb, dry_run=True)
    result = ex.run()

    assert result.ok
    assert not [c for c in fb.calls if c and c[0] == "flash"]
    assert all(s.status is StepStatus.OK for s in result.steps)


def test_flash_success_counts_all(tmp_path):
    plan = make_plan(tmp_path)
    fb = FakeFastboot()
    ex = make_executor(plan, tmp_path, fb)
    result = ex.run()

    assert result.ok
    flashed = [c[1] for c in fb.calls if c and c[0] == "flash"]
    assert "boot" in flashed
    assert "system" in flashed


def test_fastboot_exit_zero_but_failed_is_detected(tmp_path):
    """核心安全测试：fastboot 返回 0 但输出 FAILED，必须判定为失败。"""
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot(fail_on={"boot"})
    ex = make_executor(plan, tmp_path, fb)
    result = ex.run()

    assert not result.ok
    failed = [s for s in result.steps if s.status is StepStatus.FAILED]
    assert len(failed) == 1
    assert failed[0].partition == "boot"


def test_aborts_on_first_failure(tmp_path):
    """失败后必须立即中止，不能继续刷后面的分区。"""
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot(fail_on={"boot"})
    ex = make_executor(plan, tmp_path, fb)
    result = ex.run()

    flashed = [c[1] for c in fb.calls if c and c[0] == "flash"]
    # boot 是第一个，失败后不应再刷 modem
    assert flashed == ["boot"]
    assert result.error


def test_modem_flashed_last(tmp_path):
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot()
    ex = make_executor(plan, tmp_path, fb)
    ex.run()

    flashed = [c[1] for c in fb.calls if c and c[0] == "flash"]
    assert flashed[-1] == "modem"


def test_recovery_hint_present_on_failure(tmp_path):
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot(fail_on={"boot"})
    ex = make_executor(plan, tmp_path, fb)
    result = ex.run()

    failed = result.failed[0]
    assert "fastboot flash boot" in failed.recovery_hint


def test_preflight_missing_image(tmp_path):
    plan = make_plan(tmp_path)
    (tmp_path / "boot.img").unlink()
    issues = preflight(plan, tmp_path)
    assert any(i.severity == "error" and "缺少" in i.message for i in issues)


def test_preflight_empty_image(tmp_path):
    plan = make_plan(tmp_path)
    (tmp_path / "boot.img").write_bytes(b"")
    issues = preflight(plan, tmp_path)
    assert any(i.severity == "error" and "为空" in i.message for i in issues)


def test_preflight_warns_on_dangerous(tmp_path):
    mf = parse_manifest(
        make_manifest(
            [make_partition("xbl", [make_op(OpType.REPLACE_XZ, data_length=100)])]
        )
    )
    plan = build_plan(mf, package_path="x.zip")
    (tmp_path / "xbl.img").write_bytes(b"\x00" * 4096)
    issues = preflight(plan, tmp_path)
    assert any(i.severity == "warn" and "bootloader" in i.message for i in issues)


def test_extract_error_from_output():
    res = RunResult(argv=["fastboot"], exit_code=0, stdout="FAILED (remote: 'no space')\n")
    assert "no space" in _extract_error(res)


def test_extract_error_timeout():
    res = RunResult(argv=["fastboot"], exit_code=0, timed_out=True)
    assert "超时" in _extract_error(res)


def test_summarize_reports_failure():
    plan = parse_manifest(make_manifest([]))
    from lrom.core.flash import FlashResult

    r = FlashResult(plan=None)
    from lrom.core.flash import StepResult

    r.steps = [
        StepResult("boot", StepStatus.OK),
        StepResult("system", StepStatus.FAILED, message="no space", recovery_hint="fastboot flash system system.img"),
    ]
    text = summarize(r)
    assert "成功: 1" in text
    assert "失败: 1" in text
    assert "fastboot flash system" in text


# -- 关键分区锁定处理 ---------------------------------------------------

def test_is_critical_locked_detects_message():
    from lrom.core.flash import is_critical_locked

    assert is_critical_locked(
        "FAILED (remote: 'Flashing is not allowed for Critical Partitions.')"
    )
    assert not is_critical_locked("FAILED (remote: 'no space')")


def test_locked_partition_does_not_abort(tmp_path):
    """关键分区被锁时应跳过并继续刷后面的分区，而不是整体中止。"""
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot()
    # 让第一个分区返回 critical 锁定错误
    original_run = fb.run

    def run_with_lock(*args, timeout=None):
        if args and args[0] == "flash" and args[1] == "boot":
            fb.calls.append(list(args))
            return RunResult(
                argv=list(args),
                exit_code=1,
                stderr="FAILED (remote: 'Flashing is not allowed for Critical Partitions.')",
            )
        return original_run(*args, timeout=timeout)

    fb.run = run_with_lock

    ex = make_executor(plan, tmp_path, fb)
    result = ex.run()

    locked = [s for s in result.steps if s.status is StepStatus.LOCKED]
    assert len(locked) == 1
    assert locked[0].partition == "boot"
    # 后续分区仍被刷写
    flashed = [c[1] for c in fb.calls if c and c[0] == "flash"]
    assert "modem" in flashed
    assert result.ok  # LOCKED 不算失败


def test_locked_partition_counts_in_summary(tmp_path):
    from lrom.core.flash import FlashResult, StepResult

    r = FlashResult(plan=None)
    r.steps = [
        StepResult("boot", StepStatus.OK),
        StepResult("tz", StepStatus.LOCKED, message="关键分区未解锁，已跳过"),
    ]
    text = summarize(r)
    assert "锁定: 1" in text


# -- 数据擦除 -----------------------------------------------------------

def test_wipe_disabled_by_default(tmp_path):
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot()
    ex = make_executor(plan, tmp_path, fb)
    ex.run()

    assert not [c for c in fb.calls if c and c[0] == "-w"]


def test_wipe_runs_after_all_flashes(tmp_path):
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot()
    ex = make_executor(plan, tmp_path, fb)
    ex.wipe_data = True
    ex.run()

    calls = [c[0] for c in fb.calls if c]
    assert "-w" in calls
    # 擦除必须在最后一次 flash 之后
    assert calls.index("-w") > max(i for i, c in enumerate(calls) if c == "flash")


def test_wipe_failure_reported(tmp_path):
    plan = make_plan(tmp_path, with_logical=False)
    fb = FakeFastboot()

    original_run = fb.run

    def failing_wipe(*args, timeout=None):
        if args and args[0] == "-w":
            fb.calls.append(list(args))
            return RunResult(argv=list(args), exit_code=1, stderr="FAILED (remote: 'busy')")
        return original_run(*args, timeout=timeout)

    fb.run = failing_wipe
    ex = make_executor(plan, tmp_path, fb)
    ex.wipe_data = True
    result = ex.run()

    assert not result.ok
    assert "清空用户数据失败" in result.error
