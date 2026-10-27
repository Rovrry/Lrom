"""刷写前的检查顺序测试。

核心回归点：**型号一致性检查必须早于 ARB 检查**。

若不这样，拿一加 15（PLK110）刷平板包（OPD2404）时，ARB 检查会用
包里的 product_name 当成设备型号，得出「这台平板正在降级」的荒唐
结论 —— 而真实原因是机型不一致。用户已经踩过这个坑。
"""

from __future__ import annotations

import argparse

import pytest

from lrom import cli
from lrom.core.package import PackageInfo, Severity, Verdict
from lrom.device.discover import Device, DeviceMode


class _FakeInfo:
    """够 cmd_flash 走到型号检查的最小 PackageInfo 替身。"""

    def __init__(self, pre_device="OP5D77L1", product_name="OPD2404"):
        self.path = "x.zip"
        self.kind = type("K", (), {"value": "全量 OTA 包"})()
        self.verdict = Verdict.FLASHABLE
        self.manifest = object()
        self.product_name = product_name
        self.pre_device = pre_device
        self.display_os_version = None
        self.version_name = "OPD2404_14.1.0.226(CN01)"
        self.ota_type = None
        self.android_version = "14"
        self.security_patch = None
        self.ota_id = None
        self.findings = []
        self.blocked = False
        self.partitions = []


def _args(tmp_path):
    return argparse.Namespace(
        package="x.zip",
        images=str(tmp_path),
        dry_run=False,
        yes=True,
        partitions=None,
        allow_dangerous=False,
        force_arb=False,
        no_reboot=True,
        wipe=False,
        both_slots=False,
        no_recreate_logical=False,
        adb_port=None,
    )


def test_mismatched_device_blocked_before_arb(tmp_path, monkeypatch, capsys):
    """一加 15 刷平板包：应以『机型不匹配』拦下，绝不能报成 ARB 降级。"""
    (tmp_path / "boot.img").write_bytes(b"x")

    import lrom.core.package as pkg_mod

    fake_info = _FakeInfo()
    monkeypatch.setattr(pkg_mod, "inspect_package", lambda p: fake_info)

    oneplus15 = Device(
        serial="S1",
        mode=DeviceMode.ADB,
        codename="PLK110",
        model="PLK110",
        product_name="PLK110",
        build_id="PLK110_17.0.0.105(CN01)",
        android_version="17",
        slot="a",
        locked="0",
    )
    import lrom.device.discover as disc_mod

    monkeypatch.setattr(disc_mod, "detect", lambda adb_server_port=None: oneplus15)

    # 关键：ARB 检查若被调用应当暴露出来 —— 正常路径不该走到它
    called = {"arb": False}

    def _spy_arb(*a, **k):
        called["arb"] = True
        raise AssertionError("型号不一致时不应进入 ARB 检查")

    import lrom.core.arb_db as arb_db

    monkeypatch.setattr(arb_db, "check_arb_risk", _spy_arb)

    rc = cli.cmd_flash(_args(tmp_path))
    out = capsys.readouterr().out

    assert rc == 1
    assert "机型与 ROM 包不匹配" in out
    assert "ARB 熔断保护" not in out
    assert called["arb"] is False


def test_matched_device_proceeds_to_arb(tmp_path, monkeypatch, capsys):
    """机型一致时应继续往下走（本例由 ARB 正常判定拦住降级）。"""
    (tmp_path / "boot.img").write_bytes(b"x")

    import lrom.core.package as pkg_mod

    fake_info = _FakeInfo()
    monkeypatch.setattr(pkg_mod, "inspect_package", lambda p: fake_info)

    pad = Device(
        serial="S2",
        mode=DeviceMode.ADB,
        codename="OP5D77L1",
        model="OPD2404",
        product_name="OPD2404",
        build_id="OPD2404_16.0.5.1201(CN01)",
        android_version="16",
        slot="a",
        locked="0",
    )
    import lrom.device.discover as disc_mod

    monkeypatch.setattr(disc_mod, "detect", lambda adb_server_port=None: pad)

    rc = cli.cmd_flash(_args(tmp_path))
    out = capsys.readouterr().out

    # 机型匹配 → 往下走到 ARB，降级被拦
    assert "机型与 ROM 包不匹配" not in out
    assert rc == 1
    assert "ARB 熔断保护" in out
