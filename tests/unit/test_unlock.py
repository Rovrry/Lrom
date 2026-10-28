"""BL 解锁的测试。

解锁不会变砖（只清数据 + 打开刷写权限），所以风险低于回锁。
真正要防的是：没开 OEM 解锁、没通过深度测试、用户不知道会清数据。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lrom.core.unlock import (
    UnlockReadiness,
    build_unlock_plan,
    check_unlock_readiness,
)


# ---------------------------------------------------------------------------
# 可行性判定
# ---------------------------------------------------------------------------


def test_ready_when_fastboot_and_locked():
    r, reasons = check_unlock_readiness(
        device_present=True, device_mode="fastboot", locked="1"
    )
    assert r is UnlockReadiness.READY
    assert reasons == []


def test_already_unlocked():
    r, reasons = check_unlock_readiness(
        device_present=True, device_mode="fastboot", locked="0"
    )
    assert r is UnlockReadiness.ALREADY_UNLOCKED
    assert any("已是解锁" in x for x in reasons)


def test_no_device():
    r, _ = check_unlock_readiness(device_present=False, device_mode="", locked=None)
    assert r is UnlockReadiness.NO_DEVICE


def test_not_fastboot():
    r, reasons = check_unlock_readiness(
        device_present=True, device_mode="adb", locked="1"
    )
    assert r is UnlockReadiness.NOT_FASTBOOT
    assert any("reboot bootloader" in x for x in reasons)


def test_lock_state_unknown():
    r, _ = check_unlock_readiness(
        device_present=True, device_mode="fastboot", locked=None
    )
    assert r is UnlockReadiness.LOCK_STATE_UNKNOWN


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


def test_plan_default_uses_flashing_unlock():
    p = build_unlock_plan(device_model="OPD2404", current_slot="a")
    assert p.unlock_command == ("flashing", "unlock")
    text = p.describe()
    assert "flashing unlock" in text
    assert "清空全部用户数据" in text
    assert "UNLOCK THE BOOTLOADER" in text


def test_plan_legacy_uses_oem_unlock():
    p = build_unlock_plan(legacy_command=True)
    assert p.unlock_command == ("oem", "unlock")
    assert "oem unlock" in p.describe()


def test_plan_mentions_deep_test():
    """计划里必须提到新机需要深度测试。"""
    p = build_unlock_plan()
    assert "深度测试" in p.describe()


# ---------------------------------------------------------------------------
# cmd_unlock 前置检查
# ---------------------------------------------------------------------------


def _ns(**kw):
    base = dict(dry_run=True, yes=False, legacy=False, adb_port=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_unlock_refuses_no_device(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: None)
    rc = cli.cmd_unlock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "未检测到设备" in out


def test_unlock_refuses_adb_mode(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.ADB, locked="1", model="OPD2404", codename="OP5D77L1", slot="a"))
    rc = cli.cmd_unlock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "reboot bootloader" in out


def test_unlock_says_already_unlocked(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked="0", model="OPD2404",
        codename="OP5D77L1", slot="a"))
    rc = cli.cmd_unlock(_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert "已是解锁" in out


def test_unlock_dry_run_prints_plan(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked="1", model="OPD2404",
        codename="OP5D77L1", slot="a"))
    rc = cli.cmd_unlock(_ns(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "flashing unlock" in out
    assert "预演" in out


def test_unlock_runs_command_and_handles_failure(monkeypatch, capsys):
    """实际执行路径：命令失败时给出 OEM 解锁/深度测试等原因提示。"""
    import lrom.cli as cli
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked="1", model="OPD2404",
        codename="OP5D77L1", slot="a", serial="S1"))

    calls = []

    class _FailFB:
        def __init__(self, *a, **k): pass
        def run(self, *a, **k):
            calls.append(a)
            return SimpleNamespace(exit_code=1, stdout="", stderr="FAILED", combined="FAILED")
        def ok(self, r): return False

    monkeypatch.setattr(runner_mod, "FastbootRunner", _FailFB)

    rc = cli.cmd_unlock(_ns(dry_run=False, yes=True))
    out = capsys.readouterr().out
    assert rc == 1
    assert calls and calls[0] == ("flashing", "unlock")
    assert "OEM 解锁" in out
    assert "深度测试" in out


def test_unlock_legacy_command(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked="1", model="OPD2404",
        codename="OP5D77L1", slot="a", serial="S1"))

    calls = []

    class _OkFB:
        def __init__(self, *a, **k): pass
        def run(self, *a, **k):
            calls.append(a)
            return SimpleNamespace(exit_code=0, stdout="OKAY", stderr="", combined="OKAY")
        def ok(self, r): return True

    monkeypatch.setattr(runner_mod, "FastbootRunner", _OkFB)

    rc = cli.cmd_unlock(_ns(dry_run=False, yes=True, legacy=True))
    assert rc == 0
    assert calls[0] == ("oem", "unlock")


def test_unlock_device_prompt_mentions_volume_power(capsys):
    """解锁前必须提示设备端用音量键+电源键确认。"""
    import lrom.cli as cli

    cli._print_unlock_device_prompt()
    out = capsys.readouterr().out
    assert "Unlock the bootloader" in out
    assert "音量" in out
    assert "电源" in out
