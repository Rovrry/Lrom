"""FastbootD 只读诊断 + 「仅 FastbootD」执行路径测试。

对应 docs/research/violet-fbd-only-deep.md / violet-repair-fbd-deep.md 的
P0 落地：
  · diagnose_fbd：只读判断设备该走哪条刷写路径（普通/修复FBD/仅FBD）。
  · run(fbd_only=True)：进不了 bootloader 的设备只在 FBD 刷逻辑分区，
    绝不 reboot bootloader、绝不刷 bootloader 侧分区。
"""

from __future__ import annotations

import types

import pytest

from lrom.core.flash import (
    BlSymptom,
    FbdRecommendation,
    FbdState,
    FlashExecutor,
    diagnose_fbd,
)
from lrom.core.plan import Phase

from .test_flash import FakeFastboot, make_plan  # 复用测试桩


# ---------------------------------------------------------------------------
# 假 runner
# ---------------------------------------------------------------------------

class _FakeAdb:
    def __init__(self, devices: dict[str, str] | None = None):
        self._devices = devices or {}

    def devices(self):
        return dict(self._devices)


def _fb(*, fbd: bool, devices=("FAKE",), slot="a", product="OPD2404"):
    class _FB(FakeFastboot):
        in_fastbootd = fbd

        def devices(self):
            return list(devices)

        def getvar(self, name):
            if name == "current-slot":
                return slot
            if name == "product":
                return product
            if name == "is-userspace":
                return "yes" if fbd else "no"
            return None

    return _FB()


def _no_device_fb():
    class _FB(FakeFastboot):
        def devices(self):
            return []

    return _FB()


# ---------------------------------------------------------------------------
# diagnose_fbd 三态
# ---------------------------------------------------------------------------

def test_diagnose_adb_device_is_normal():
    d = diagnose_fbd(adb=_FakeAdb({"X": "device"}), fb=None)
    assert d.state is FbdState.ADB
    assert d.recommendation is FbdRecommendation.NORMAL


def test_diagnose_bootloader_is_normal_with_repair_hint():
    d = diagnose_fbd(adb=None, fb=_fb(fbd=False))
    assert d.state is FbdState.FASTBOOT
    assert d.recommendation is FbdRecommendation.NORMAL
    # 在 BL 时应提示「进不了 FBD 可用修复 FastbootD」
    assert any("修复 FastbootD" in n for n in d.notes) or "修复 FastbootD" in d.summary


def test_diagnose_fbd_with_bl_dead_is_fbd_only():
    d = diagnose_fbd(adb=None, fb=_fb(fbd=True), bl_declared_dead=True)
    assert d.state is FbdState.FASTBOOTD
    assert d.is_fastbootd is True
    assert d.recommendation is FbdRecommendation.FBD_ONLY
    # 必须强调红线（不发 reboot bootloader / 不写 BL 侧分区）
    assert any(("BL 侧" in n) or ("reboot bootloader" in n) for n in d.notes)


def test_diagnose_fbd_without_bl_dead_is_normal():
    """在 FBD 但用户没说 BL 坏 → 给中性建议，不武断判为仅 FBD。"""
    d = diagnose_fbd(adb=None, fb=_fb(fbd=True), bl_declared_dead=False)
    assert d.state is FbdState.FASTBOOTD
    assert d.recommendation is FbdRecommendation.NORMAL


def test_diagnose_no_device_is_unknown():
    d = diagnose_fbd(adb=None, fb=_no_device_fb())
    assert d.state is FbdState.NONE
    assert d.recommendation is FbdRecommendation.UNKNOWN


def test_diagnose_is_pure_readonly_no_reboot():
    """只读诊断绝不能发 reboot/flash/erase 等写/重启命令。"""
    fb = _fb(fbd=True)
    diagnose_fbd(adb=None, fb=fb, bl_declared_dead=True)
    for c in fb.calls:
        assert not c, "诊断过程不应发出任何 fastboot 写/重启命令"
    # getvar 只读查询是允许的（不计入 calls 的是吗？）——确认没有写动作
    actions = [c[0] for c in fb.calls if c]
    for forbidden in ("reboot", "flash", "erase", "flash", "set-active", "--set-active"):
        assert forbidden not in actions


# ---------------------------------------------------------------------------
# run(fbd_only=True) 行为
# ---------------------------------------------------------------------------

def _make_fbd_executor(plan, tmp_path, fb):
    ex = FlashExecutor(plan, tmp_path, fb=fb, fbd_only=True)
    # 防御：若 fbd_only 分支误调 _ensure_fastboot 就让它明显失败
    ex._ensure_fastboot = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("fbd_only 不应调用 _ensure_fastboot（会踢掉 FBD）")
    )
    return ex


def _logical_only_plan(tmp_path):
    """只含逻辑分区（system）的计划，模拟「仅 FBD」只刷 super 里的系统分区。"""
    plan = make_plan(tmp_path, with_logical=True)
    for op in plan.ops:
        if op.phase is Phase.FASTBOOT:
            op.skip = True
    return plan


def test_fbd_only_skips_bootloader_phase(tmp_path):
    """仅 FBD 模式不应刷任何 FASTBOOT 阶段分区，只刷 FASTBOOTD。"""
    plan = _logical_only_plan(tmp_path)
    fb = _fb(fbd=True)
    ex = _make_fbd_executor(plan, tmp_path, fb)
    result = ex.run()

    flashed = [c[1] for c in fb.calls if c and c[0] == "flash"]
    # 只应刷逻辑分区 system，绝不刷 boot/modem
    assert "system" in flashed
    assert "boot" not in flashed and "modem" not in flashed
    # 绝不应发 reboot
    assert not any(c and c[0] == "reboot" for c in fb.calls)


def test_fbd_only_refuses_if_plan_has_bootloader_partitions(tmp_path):
    """计划里只要有 FASTBOOT 阶段分区，fbd_only 必须直接拒绝（防二次变砖）。"""
    plan = make_plan(tmp_path, with_logical=True)  # 含 boot/modem（FASTBOOT）
    fb = _fb(fbd=True)
    ex = _make_fbd_executor(plan, tmp_path, fb)
    result = ex.run()

    assert result.error  # 有错误
    assert "bootloader 侧" in result.error or "禁止" in result.error
    assert not any(c and c[0] == "flash" for c in fb.calls)


def test_fbd_only_aborts_if_not_in_fastbootd(tmp_path):
    """设备不在 FBD 时，fbd_only 不能自作主张 reboot 去切模式，应中止并提示。"""
    # 构造一个只有逻辑分区的计划（避开 BL 分区拒绝）
    plan = make_plan(tmp_path, with_logical=True)
    # 删掉 FASTBOOT 阶段的 op，模拟只选逻辑分区
    for op in plan.ops:
        if op.phase is Phase.FASTBOOT:
            op.skip = True
    fb = _fb(fbd=False)  # 在 bootloader，不是 FBD
    ex = _make_fbd_executor(plan, tmp_path, fb)
    result = ex.run()

    assert result.error
    assert "FastbootD" in result.error
    # 绝不能 reboot 去切模式
    assert not any(c and c[0] == "reboot" for c in fb.calls)
    assert not any(c and c[0] == "flash" for c in fb.calls)


# ---------------------------------------------------------------------------
# ColorOS 封 fastboot / bootloader 秒退 → 刷旧 abl 救回（RESCUE_ABL）
# ---------------------------------------------------------------------------

def test_diagnose_fbd_instant_exit_suggests_rescue_abl():
    """在 FBD 且 bootloader 秒退（ColorOS 封 fastboot）→ 建议 RESCUE_ABL。"""
    d = diagnose_fbd(
        adb=None, fb=_fb(fbd=True), bl_symptom=BlSymptom.INSTANT_EXIT
    )
    assert d.state is FbdState.FASTBOOTD
    assert d.recommendation is FbdRecommendation.RESCUE_ABL
    # 必须提示版本风险与只刷当前槽
    joined = " ".join(d.notes) + d.summary
    assert "abl" in joined
    assert "当前槽" in joined or "abl_a" in joined


def test_diagnose_fbd_dead_still_fbd_only_even_with_symptom_none():
    """完全进不去 BL（bl_declared_dead）且无秒退 → 仍是 FBD_ONLY（永久，禁碰BL）。"""
    d = diagnose_fbd(adb=None, fb=_fb(fbd=True), bl_declared_dead=True)
    assert d.recommendation is FbdRecommendation.FBD_ONLY


def test_diagnose_instant_exit_takes_precedence_over_dead():
    """秒退症状优先判为可救（RESCUE_ABL），而不是直接判死 FBD_ONLY。"""
    d = diagnose_fbd(
        adb=None,
        fb=_fb(fbd=True),
        bl_declared_dead=True,
        bl_symptom=BlSymptom.INSTANT_EXIT,
    )
    assert d.recommendation is FbdRecommendation.RESCUE_ABL


def test_diagnose_rescue_abl_is_readonly():
    """RESCUE_ABL 诊断本身也必须只读（不自动刷 abl）。"""
    fb = _fb(fbd=True)
    diagnose_fbd(adb=None, fb=fb, bl_symptom=BlSymptom.INSTANT_EXIT)
    actions = [c[0] for c in fb.calls if c]
    for forbidden in ("flash", "erase", "reboot", "-w"):
        assert forbidden not in actions
