"""修复 FastbootD（repair_fbd）流程测试。"""

from __future__ import annotations

import types

import pytest

import lrom.core.flash as flash
from lrom.core.flash import RepairFbdResult, repair_fbd
from lrom.device.runner import RunResult


class FakeFB:
    """可配置的 fastboot 桩：模拟 bootloader / FBD 切换、recovery 分区、flash 成败。"""

    def __init__(self, *, fbd_reachable=False, has_recovery=True, flash_ok=True,
                 fbd_after_flash=True):
        self.fbd_reachable = fbd_reachable
        self.has_recovery = has_recovery
        self.flash_ok = flash_ok
        self.fbd_after_flash = fbd_after_flash
        self.calls = []
        self._in_fbd = False
        self._flashed = False

    def devices(self):
        return ["FAKE"]

    def is_fastbootd(self):
        return self._in_fbd

    def ok(self, r):
        return r.exit_code == 0 and "FAILED" not in (r.stdout + r.stderr)

    def getvar(self, name):
        if name == "partition-size:recovery":
            return "0x6400000" if self.has_recovery else None
        if name == "current-slot":
            return "a"
        return None

    def run(self, *args, timeout=None):
        self.calls.append(list(args))
        a0 = args[0] if args else ""
        if a0 == "reboot" and len(args) > 1 and args[1] == "fastboot":
            if self._flashed:
                self._in_fbd = self.fbd_after_flash
            else:
                self._in_fbd = self.fbd_reachable
            return RunResult(argv=list(args), exit_code=0, stdout="ok\n")
        if a0 == "reboot" and len(args) > 1 and args[1] == "bootloader":
            self._in_fbd = False
            return RunResult(argv=list(args), exit_code=0, stdout="ok\n")
        if a0 == "flash" and len(args) > 1 and args[1] == "recovery":
            if self.flash_ok:
                self._flashed = True
                return RunResult(argv=list(args), exit_code=0, stdout="OKAY\n")
            return RunResult(argv=list(args), exit_code=1, stdout="FAILED (remote: x)\n")
        return RunResult(argv=list(args), exit_code=0, stdout="ok\n")


@pytest.fixture(autouse=True)
def _fast_probe(monkeypatch):
    # 用桩替换真实探测循环（真实循环靠 time.time 轮询，测试里不便真等）。
    # 桩直接依 FakeFB 的当前状态返回：模拟 reboot fastboot 后能否进 FBD。
    def _fake_probe(fb, timeout=60.0):
        # 触发一次 reboot fastboot 语义（更新 _in_fbd），再按其结果返回
        fb.run("reboot", "fastboot")
        return fb.is_fastbootd()

    monkeypatch.setattr(flash, "_probe_fbd_reachable", _fake_probe)


def _img(tmp_path):
    p = tmp_path / "recovery.img"
    p.write_bytes(b"\x00" * 1024)
    return p


def test_repair_skips_when_fbd_not_broken(tmp_path):
    """FBD 能进 → 无需修复，不刷 recovery。"""
    fb = FakeFB(fbd_reachable=True)
    res = repair_fbd(fb, _img(tmp_path))
    assert res.ok and not res.flashed
    assert not any(c[:2] == ["flash", "recovery"] for c in fb.calls)


def test_repair_flashes_recovery_and_verifies(tmp_path):
    """FBD 坏 → 刷 recovery → 验证 FBD 恢复。"""
    fb = FakeFB(fbd_reachable=False, flash_ok=True, fbd_after_flash=True)
    res = repair_fbd(fb, _img(tmp_path))
    assert res.ok and res.flashed and res.verified_fbd
    assert any(c[:2] == ["flash", "recovery"] for c in fb.calls)


def test_repair_when_no_independent_recovery_partition(tmp_path):
    """无独立 recovery 分区 → 拒绝（应改刷 boot/init_boot）。"""
    fb = FakeFB(fbd_reachable=False, has_recovery=False)
    res = repair_fbd(fb, _img(tmp_path))
    assert not res.ok
    assert "recovery 分区" in res.error
    assert not any(c[:2] == ["flash", "recovery"] for c in fb.calls)


def test_repair_flash_failure_reports_recoverable(tmp_path):
    """flash 失败 → 报错并提示可刷回（非硬砖）。"""
    fb = FakeFB(fbd_reachable=False, flash_ok=False)
    res = repair_fbd(fb, _img(tmp_path))
    assert not res.ok
    assert "失败" in res.error


def test_repair_missing_recovery_img(tmp_path):
    fb = FakeFB(fbd_reachable=False)
    res = repair_fbd(fb, tmp_path / "nope.img")
    assert not res.ok


def test_repair_version_mismatch_is_warning_not_block(tmp_path):
    """版本不匹配只警告、不截断（仍会尝试刷）。"""
    fb = FakeFB(fbd_reachable=False)
    pkg = types.SimpleNamespace(display_os_version="16.0.9", build_incremental="X")
    res = repair_fbd(fb, _img(tmp_path), package_info=pkg, device_version="16.0.5")
    assert res.warnings  # 有版本不一致警告
    # 仍执行了刷写（不因版本不一致而阻断）
    assert any(c[:2] == ["flash", "recovery"] for c in fb.calls)


def test_repair_refuses_when_in_fastbootd(tmp_path):
    """设备已在 FBD → 拒绝（本修复应在 bootloader 下）。"""
    fb = FakeFB()
    fb._in_fbd = True
    res = repair_fbd(fb, _img(tmp_path))
    assert not res.ok
    assert "bootloader" in res.error
