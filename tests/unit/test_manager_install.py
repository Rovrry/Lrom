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


# ---------------------------------------------------------------------------
# 安装后删除本地包
# ---------------------------------------------------------------------------


def _run_fetch_with(tmp_path, monkeypatch, install_status):
    """跑一遍 cmd_fetch_manager，返回下载目录里剩余文件。"""
    import lrom.cli as cli_mod
    import lrom.core.manager_fetch as mf

    fake_asset = SimpleNamespace(name="KernelSU.apk", size=100, sha256="0" * 64, url="")
    fake_release = SimpleNamespace(tag="v1", prerelease=False, published_at=None, assets=[fake_asset])

    monkeypatch.setattr(mf, "resolve_manager_source", lambda k: SimpleNamespace(label="KSU", repo="t/x"))
    monkeypatch.setattr(mf, "fetch_latest_release", lambda s, channel=None: fake_release)
    monkeypatch.setattr(mf, "pick_asset", lambda r, s, **k: fake_asset)

    def _dl(asset, dest, *, filename=None, expected_sha256=None, on_progress=None):
        f = Path(dest) / "KernelSU.apk"
        f.write_bytes(b"x" * 100)
        return f

    monkeypatch.setattr(mf, "download_asset", _dl)
    monkeypatch.setattr(mf, "install_manager_apk", lambda p, **k: install_status)

    ns = SimpleNamespace(source="ksu", channel="stable", dest=str(tmp_path), asset=None, yes=True, adb_port=None)
    cli_mod.cmd_fetch_manager(ns)
    return list(tmp_path.glob("*.apk"))


def test_apk_deleted_after_successful_install(tmp_path, monkeypatch, capsys):
    """安装成功后应删除本地安装包。"""
    remaining = _run_fetch_with(tmp_path, monkeypatch, ("installed", "Success"))
    assert remaining == []
    assert "已删除本地安装包" in capsys.readouterr().out


def test_apk_kept_when_no_device(tmp_path, monkeypatch, capsys):
    """无设备时必须保留本地包，供手动安装。"""
    remaining = _run_fetch_with(tmp_path, monkeypatch, ("no_device", "未连接设备"))
    assert len(remaining) == 1


def test_apk_kept_when_install_failed(tmp_path, monkeypatch, capsys):
    """安装失败时也必须保留本地包。"""
    remaining = _run_fetch_with(tmp_path, monkeypatch, ("failed", "Failure"))
    assert len(remaining) == 1


# ---------------------------------------------------------------------------
# 获取 Root 的退出选项
# ---------------------------------------------------------------------------


def test_ensure_manager_q_returns_abort(monkeypatch, capsys):
    """管理器选择里输入 q → 返回 _ABORT。"""
    import lrom.tui.menu as menu

    monkeypatch.setattr(menu, "ask", lambda p, d="": "q")
    r = menu._ensure_root_manager(None)
    assert r is menu._ABORT


def test_menu_root_aborts_without_running_cmd_root(monkeypatch, capsys):
    """管理器返回 _ABORT 时，_menu_root 不应进入 cmd_root。"""
    import lrom.tui.menu as menu
    from pathlib import Path as _P

    monkeypatch.setattr(menu, "_find_stock_init_boot", lambda s: _P("x.img"))
    monkeypatch.setattr(menu, "_verify_device_for_root", lambda *a, **k: True)
    monkeypatch.setattr(menu, "confirm", lambda p, word=None: True)
    monkeypatch.setattr(menu, "ask", lambda p, default="": "1")  # 选脱机一键
    monkeypatch.setattr(menu, "pause", lambda m="": None)
    monkeypatch.setattr(menu, "_ensure_root_manager", lambda mon: menu._ABORT)

    called = {"root": False}

    class _Sess:
        package = None
        work_dir = _P("work")

    # cmd_root 是函数内 import，用 sys.modules 拦截
    import lrom.cli as cli_mod

    monkeypatch.setattr(cli_mod, "cmd_root", lambda ns: called.__setitem__("root", True) or 0, raising=False)

    menu._menu_root(_Sess(), None)
    out = capsys.readouterr().out
    assert "已取消" in out
    assert called["root"] is False


def test_install_minus99_hints_unlock_screen(monkeypatch):
    """安装失败 -99 时，提示应引导『点亮/解锁屏幕』（熄屏是常见真因），而非归咎命令。"""
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(
        disc, "detect",
        lambda adb_server_port=None: SimpleNamespace(mode=disc.DeviceMode.ADB, serial="S1"),
    )

    class _Fail99:
        serial = ""

        def run(self, *a, timeout=None):
            if a and a[0] == "install":
                return SimpleNamespace(exit_code=1, stdout="", stderr="Failure [-99]")
            return SimpleNamespace(exit_code=0, stdout="", stderr="")  # wake/unlock 等

    monkeypatch.setattr(runner_mod, "AdbRunner", lambda server_port=None: _Fail99())
    apk = Path("/tmp/m99.apk"); apk.write_bytes(b"x")
    try:
        status, detail = install_manager_apk(apk)
        assert status == "failed"
        assert "解锁" in detail or "熄屏" in detail
    finally:
        apk.unlink()


def test_install_wakes_screen_before_install(monkeypatch):
    """安装前应尝试唤醒屏幕 + 解除锁屏。"""
    import lrom.device.discover as disc
    import lrom.device.runner as runner_mod

    monkeypatch.setattr(
        disc, "detect",
        lambda adb_server_port=None: SimpleNamespace(mode=disc.DeviceMode.ADB, serial="S1"),
    )
    calls = []

    class _Cap:
        serial = ""

        def run(self, *a, timeout=None):
            calls.append(a)
            return SimpleNamespace(exit_code=0, stdout="Success", stderr="")

    monkeypatch.setattr(runner_mod, "AdbRunner", lambda server_port=None: _Cap())
    apk = Path("/tmp/wk.apk"); apk.write_bytes(b"x")
    try:
        install_manager_apk(apk)
        flat = " ".join(" ".join(map(str, c)) for c in calls)
        assert "KEYCODE_WAKEUP" in flat
        assert "dismiss-keyguard" in flat
    finally:
        apk.unlink()
