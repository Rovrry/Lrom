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
    monkeypatch.setattr(mf, "pick_asset", lambda r, s, **kw: fake_asset)

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


# ---------------------------------------------------------------------------
# asset 选择：特殊变体排除 + 架构识别
# ---------------------------------------------------------------------------


def _rel(names):
    from lrom.core.manager_fetch import Release, ReleaseAsset

    return Release(
        tag="v1",
        prerelease=False,
        published_at="",
        html_url="",
        assets=[ReleaseAsset(n, "u", 100) for n in names],
    )


def _src(key="sukisu"):
    from lrom.core.manager_fetch import resolve_manager_source

    return resolve_manager_source(key)


def test_spoofed_variant_is_skipped():
    """spoofed 是 AVC 伪装变体，普通用户应选不带 spoofed 的正式包。"""
    from lrom.core.manager_fetch import pick_asset

    rel = _rel([
        "SukiSU_v4.2.1-r2_41068-release-spoofed.apk",
        "SukiSU_v4.2.1-r2_41068-release.apk",
    ])
    picked = pick_asset(rel, _src("sukisu"), device_abi="arm64-v8a")
    assert picked.name == "SukiSU_v4.2.1-r2_41068-release.apk"
    assert "spoofed" not in picked.name


def test_nosu_variant_is_skipped():
    from lrom.core.manager_fetch import pick_asset

    rel = _rel(["X-release-nosu.apk", "X-release.apk"])
    picked = pick_asset(rel, _src("sukisu"))
    assert picked.name == "X-release.apk"


def test_arch_matches_device_abi():
    """有设备 ABI 时选同架构的包。"""
    from lrom.core.manager_fetch import pick_asset

    rel = _rel(["KernelSU_arm64-v8a.apk", "KernelSU_x86_64.apk"])
    picked = pick_asset(rel, _src("ksu"), device_abi="arm64-v8a")
    assert "arm64" in picked.name


def test_generic_apk_preferred_over_arch():
    """通用包优先于带架构字样的包。"""
    from lrom.core.manager_fetch import pick_asset

    rel = _rel(["KernelSU_arm64-v8a.apk", "KernelSU_release.apk", "KernelSU_x86_64.apk"])
    picked = pick_asset(rel, _src("ksu"), device_abi="arm64-v8a")
    assert picked.name == "KernelSU_release.apk"


def test_abi_family_helper():
    from lrom.core.manager_fetch import _abi_family, _asset_arch

    assert _abi_family("arm64-v8a") == "arm64"
    assert _abi_family("armeabi-v7a") == "arm"
    assert _abi_family("x86_64") == "x86_64"
    assert _abi_family(None) is None
    assert _asset_arch("foo-arm64-v8a.apk") == "arm64"
    assert _asset_arch("foo-release.apk") is None
