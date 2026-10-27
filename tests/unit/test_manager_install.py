"""root 管理器下载/安装流程的测试。

需求：
1. 下载管理器内置进「获取 root」流程
2. 下载后：有设备自动 adb install；无设备提示插设备
3. 明确标注 APK 的本机绝对路径
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lrom.core.manager_fetch import install_manager_apk


class _FakeAdb:
    def __init__(self, serial="S1"):
        self.serial = serial
        self.install_called = False

    def run(self, *args, timeout=None):
        self.install_called = True
        return SimpleNamespace(exit_code=0, stdout="Success", stderr="")


def test_install_no_device(monkeypatch):
    """没有设备 → no_device，不调 adb install。"""
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: None)
    apk = Path("/tmp/x.apk")
    apk.write_bytes(b"x")
    try:
        status, detail = install_manager_apk(apk)
        assert status == "no_device"
        assert "未连接" in detail or "adb install" in detail
    finally:
        apk.unlink()


def test_install_fastboot_mode_is_no_device(monkeypatch):
    """设备在 fastboot 模式不算可装 → no_device。"""
    import lrom.device.discover as disc

    dev = SimpleNamespace(mode=disc.DeviceMode.FASTBOOT, serial="S1")
    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: dev)
    apk = Path("/tmp/y.apk")
    apk.write_bytes(b"x")
    try:
        status, _ = install_manager_apk(apk)
        assert status == "no_device"
    finally:
        apk.unlink()


def test_install_success(monkeypatch):
    """有 ADB 设备 → 调用 adb install 并返回 installed。"""
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(
        disc, "detect",
        lambda adb_server_port=None: SimpleNamespace(
            mode=disc.DeviceMode.ADB, serial="S1"
        ),
    )
    fake = _FakeAdb()
    monkeypatch.setattr(runner_mod, "AdbRunner", lambda server_port=None: fake)

    apk = Path("/tmp/z.apk")
    apk.write_bytes(b"x")
    try:
        status, detail = install_manager_apk(apk)
        assert status == "installed"
        assert fake.install_called
    finally:
        apk.unlink()


def test_install_failure_reported(monkeypatch):
    """adb install 输出 Failure → failed。"""
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(
        disc, "detect",
        lambda adb_server_port=None: SimpleNamespace(
            mode=disc.DeviceMode.ADB, serial="S1"
        ),
    )

    class _FailAdb:
        serial = ""

        def run(self, *a, timeout=None):
            return SimpleNamespace(exit_code=1, stdout="", stderr="Failure [INSTALL_FAILED]")

    monkeypatch.setattr(runner_mod, "AdbRunner", lambda server_port=None: _FailAdb())

    apk = Path("/tmp/w.apk")
    apk.write_bytes(b"x")
    try:
        status, detail = install_manager_apk(apk)
        assert status == "failed"
        assert "Failure" in detail or "失败" in detail
    finally:
        apk.unlink()


def test_missing_apk_is_failed():
    status, detail = install_manager_apk(Path("/nonexistent/nope.apk"))
    assert status == "failed"
    assert "不存在" in detail


def test_fetch_manager_reports_absolute_path(tmp_path, monkeypatch, capsys):
    """cmd_fetch_manager 下载后必须打印绝对路径。"""
    import lrom.cli as cli_mod
    import lrom.core.manager_fetch as mf

    fake_asset = SimpleNamespace(
        name="KernelSU.apk", size=1024, sha256="0" * 64, browser_download_url="https://x/y"
    )
    fake_release = SimpleNamespace(
        tag="v1.0", prerelease=False, published_at=None, assets=[fake_asset]
    )
    monkeypatch.setattr(mf, "resolve_manager_source", lambda k: SimpleNamespace(label="KSU", repo="tiankou/x"))
    monkeypatch.setattr(mf, "fetch_latest_release", lambda s, channel=None: fake_release)
    monkeypatch.setattr(mf, "pick_asset", lambda r, s: fake_asset)

    def _fake_download(asset, dest, *, filename=None, expected_sha256=None, on_progress=None):
        d = Path(dest)
        d.mkdir(parents=True, exist_ok=True)
        f = d / "KernelSU.apk"
        f.write_bytes(b"x" * 10)
        return f

    monkeypatch.setattr(mf, "download_asset", _fake_download)
    monkeypatch.setattr(mf, "install_manager_apk", lambda p, **k: ("no_device", "未连接设备"))

    ns = SimpleNamespace(source="ksu", channel="stable", dest=str(tmp_path), asset=None, yes=True, adb_port=None)
    cli_mod.cmd_fetch_manager(ns)
    out = capsys.readouterr().out
    assert "保存位置" in out
    # 必须是绝对路径
    assert str(tmp_path.resolve()) in out or str((tmp_path / "KernelSU.apk").resolve()) in out
