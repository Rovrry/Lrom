"""BL 锁状态读取的可靠性测试。

背景（实测反馈）：一加 15 已解锁，但 getprop 的
`ro.boot.flash.locked=1`、`ro.boot.verifiedbootstate=green`
被模块 hook 篡改，导致 Lrom 误报「已锁定」。
而 `/proc/cmdline` 里是 `oplusboot.verifiedbootstate=orange`（真实状态）。

结论：cmdline 优先，getprop 兜底。
"""

from __future__ import annotations

from types import SimpleNamespace

from lrom.device.discover import _read_adb_locked


class _FakeAdb:
    """可控的 AdbRunner 替身。"""

    def __init__(self, cmdline: str = "", props: dict[str, str] | None = None):
        self._cmdline = cmdline
        self._props = props or {}

    def shell(self, command: str, timeout: float = 60.0):
        if "cmdline" in command:
            return SimpleNamespace(exit_code=0, stdout=self._cmdline, stderr="")
        return SimpleNamespace(exit_code=1, stdout="", stderr="")

    def getprop(self, name: str) -> str:
        return self._props.get(name, "")


def test_cmdline_orange_wins_over_hooked_getprop():
    """cmdline=orange 但 getprop 被 hook 成 locked/green → 判为已解锁。"""
    adb = _FakeAdb(
        cmdline="console=ttyMSM0 oplusboot.verifiedbootstate=orange androidboot.verifiedbootstate=orange",
        props={"ro.boot.flash.locked": "1", "ro.boot.verifiedbootstate": "green"},
    )
    assert _read_adb_locked(adb) == "0"


def test_cmdline_green_means_locked():
    adb = _FakeAdb(cmdline="androidboot.verifiedbootstate=green")
    assert _read_adb_locked(adb) == "1"


def test_getprop_used_when_cmdline_absent():
    """没有 cmdline 信息时退回 getprop。"""
    adb = _FakeAdb(props={"ro.boot.verifiedbootstate": "orange"})
    assert _read_adb_locked(adb) == "0"


def test_flash_locked_is_last_resort():
    adb = _FakeAdb(props={"ro.boot.flash.locked": "0"})
    assert _read_adb_locked(adb) == "0"


def test_unknown_returns_none():
    adb = _FakeAdb()
    assert _read_adb_locked(adb) is None


def test_yellow_and_red_treated_as_locked():
    """异常校验状态按已锁定处理，宁可保守。"""
    assert _read_adb_locked(_FakeAdb(cmdline="androidboot.verifiedbootstate=yellow")) == "1"
    assert _read_adb_locked(_FakeAdb(cmdline="androidboot.verifiedbootstate=red")) == "1"


def test_shell_exception_falls_back():
    """cmdline 读不到（抛异常）时不能崩，退回 getprop。"""

    class _BrokenAdb:
        def shell(self, command, timeout=60.0):
            raise RuntimeError("boom")

        def getprop(self, name):
            return {"ro.boot.verifiedbootstate": "orange"}.get(name, "")

    assert _read_adb_locked(_BrokenAdb()) == "0"
