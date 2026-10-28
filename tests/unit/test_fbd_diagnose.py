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


# ---------------------------------------------------------------------------
# 真机 bug 回归：plan.slot 缺失时，逻辑分区重建必须兜底读设备当前槽
# （实机 OPD2404/A16 踩到：默认 _a 但设备是 _b → create 错槽 → flash 裸名失败）
# ---------------------------------------------------------------------------

def test_slot_suffix_falls_back_to_device_current_slot(tmp_path):
    """plan.slot=None 时，_target_slot_suffix 应读设备 current-slot（此处 _b）。"""
    plan = _logical_only_plan(tmp_path)
    assert plan.slot is None
    fb = _fb(fbd=True, slot="b")
    ex = FlashExecutor(plan, tmp_path, fb=fb, fbd_only=True)
    assert ex._target_slot_suffix() == "_b"


def test_fbd_only_creates_target_slot_from_device(tmp_path):
    """fbd_only 应 create 设备当前槽（_b），而不是默认 _a，且刷写成功。"""
    plan = _logical_only_plan(tmp_path)
    fb = _fb(fbd=True, slot="b")
    ex = FlashExecutor(plan, tmp_path, fb=fb, fbd_only=True)
    ex.on_notice = lambda m: None
    result = ex.run()

    assert result.ok, result.error
    created = [c[1] for c in fb.calls if c and c[0] == "create-logical-partition"]
    # 系统分区 system 的镜像槽位：必须建 _b（设备当前槽）
    assert any(c.endswith("_b") for c in created), created
    assert not any(c.endswith("_a") for c in created), created


# ---------------------------------------------------------------------------
# 入口接线（DEVNOTES 1.8）：仅FBD作为线刷第4模式；修复FastbootD在「工具」区菜单13
# ---------------------------------------------------------------------------

def test_fbd_only_is_a_flash_mode():
    """仅FBD救砖应是 FlashMode 的第 4 个模式，而非独立菜单项。"""
    from lrom.tui import menu

    assert hasattr(menu.FlashMode, "FBD_ONLY")
    assert menu.FlashMode.FBD_ONLY.is_fbd_only is True
    # 其它模式都不是 fbd_only
    for m in (menu.FlashMode.NORMAL, menu.FlashMode.STRONG, menu.FlashMode.RELOCK):
        assert m.is_fbd_only is False


def test_repair_fbd_handler_at_menu_13():
    """修复FastbootD 应在「工具」区（菜单13），不在高危区。"""
    import inspect

    from lrom.tui import menu

    assert callable(getattr(menu, "_menu_repair_fbd", None))
    src = inspect.getsource(menu)
    assert '"13": _menu_repair_fbd' in src
    # 已不在高危旧编号 18 / 没有独立仅FBD handler
    assert '"18":' not in src
    assert "_menu_fbd_only" not in src


def test_cmd_flash_forwards_fbd_only():
    """cmd_flash 必须把 args.fbd_only 透传给 FlashExecutor（否则菜单17形同虚设）。"""
    import inspect

    import lrom.cli as cli

    src = inspect.getsource(cli.cmd_flash)
    assert 'fbd_only=getattr(args, "fbd_only", False)' in src


# ---------------------------------------------------------------------------
# 修复FBD 判定：bootloader 态实测 FBD 可达性（probe_fbd）
# ---------------------------------------------------------------------------

def _fb_bl_with_probe(reachable: bool):
    """bootloader 态 fake fb；reboot fastboot 后根据 reachable 返回 FBD 或 BL。"""
    class _FB(FakeFastboot):
        _state = {"in_fbd": False}

        def devices(self):
            return ["FAKE"]

        def is_fastbootd(self):
            return self._state["in_fbd"]

        def getvar(self, name):
            if name == "current-slot":
                return "a"
            if name == "is-userspace":
                return "yes" if self._state["in_fbd"] else "no"
            return None

        def run(self, *args, timeout=None):
            self.calls.append(list(args))
            if args and args[0] == "reboot" and args[1] == "fastboot":
                self._state["in_fbd"] = reachable
            return __import__("lrom.device.runner", fromlist=["RunResult"]).RunResult(
                argv=list(args), exit_code=0, stdout="ok\n"
            )

    return _FB()


def test_probe_fbd_reachable_gives_normal():
    fb = _fb_bl_with_probe(reachable=True)
    d = diagnose_fbd(adb=None, fb=fb, probe_fbd=True)
    # _probe_fbd_reachable 用 time.sleep + 轮询；FakeFastboot 立即返回 FBD
    assert d.recommendation is FbdRecommendation.NORMAL


def test_probe_fbd_unreachable_gives_repair_fbd(monkeypatch):
    # 让轮询尽快超时
    import lrom.core.flash as flash

    monkeypatch.setattr(flash.time, "sleep", lambda *a, **k: None)
    # 用一个时间已过期的 deadline：把 time.time 固定递增
    seq = iter(range(0, 10_000, 100))
    monkeypatch.setattr(flash.time, "time", lambda: next(seq))
    fb = _fb_bl_with_probe(reachable=False)
    d = diagnose_fbd(adb=None, fb=fb, probe_fbd=True)
    assert d.recommendation is FbdRecommendation.REPAIR_FBD


def test_probe_never_used_from_fbd_state():
    """在 FBD 态即使 probe_fbd=True 也绝不发 reboot（不踢掉仅FBD设备）。"""
    fb = _fb(fbd=True)
    diagnose_fbd(adb=None, fb=fb, bl_symptom=BlSymptom.INSTANT_EXIT, probe_fbd=True)
    assert not any(c and c[0] == "reboot" for c in fb.calls)
