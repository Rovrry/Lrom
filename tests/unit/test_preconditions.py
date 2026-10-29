"""刷机前置条件展示的测试。

需求：启动时环境检查会「一闪而过」，且刷机前应明确展示是否缺少 adb/fastboot 等。
"""

from __future__ import annotations

from lrom.device.selfcheck import CheckItem, Level, precondition_summary


def test_precondition_summary_order_labels_and_levels():
    items = [
        CheckItem(Level.ERROR, "adb 未安装", "未找到 adb", "apt install adb", key="adb"),
        CheckItem(Level.OK, "fastboot 已安装", "/usr/bin/fastboot\n    version 35.0.0", key="fastboot"),
        CheckItem(Level.WARN, "未找到 Android udev 规则", "", "install rules", key="udev"),
        CheckItem(Level.INFO, "配置文件", "/x/config.json", key="config"),
    ]
    summary = precondition_summary(items)
    labels = [s[0] for s in summary]
    # 顺序固定；config 不进入前置条件面板
    assert labels == ["adb", "fastboot", "设备驱动(udev)"]
    assert summary[0][1] is Level.ERROR
    assert summary[1][1] is Level.OK
    # 值取 detail 第一行
    assert summary[1][2] == "/usr/bin/fastboot"


def _patch_selfcheck(monkeypatch, items):
    import lrom.device.selfcheck as sc

    monkeypatch.setattr(sc, "run_selfcheck", lambda: items)


def test_show_flash_preconditions_blocks_when_tools_missing(monkeypatch, capsys):
    from lrom.tui import menu

    _patch_selfcheck(
        monkeypatch,
        [
            CheckItem(Level.ERROR, "adb 未安装", "未找到 adb", "sudo apt install adb", key="adb"),
            CheckItem(Level.ERROR, "fastboot 未安装", "未找到 fastboot", "sudo apt install fastboot", key="fastboot"),
        ],
    )
    monkeypatch.setattr(menu, "pause", lambda *a, **k: None)
    ok = menu._show_flash_preconditions()
    out = capsys.readouterr().out
    assert ok is False
    assert "无法刷机" in out
    assert "缺少关键工具" in out


def test_show_flash_preconditions_ok_with_warning(monkeypatch, capsys):
    from lrom.tui import menu

    _patch_selfcheck(
        monkeypatch,
        [
            CheckItem(Level.OK, "adb 已安装", "/usr/bin/adb", key="adb"),
            CheckItem(Level.OK, "fastboot 已安装", "/usr/bin/fastboot", key="fastboot"),
            CheckItem(Level.WARN, "adb server 归属异常", "属于 root", key="adb_server"),
            CheckItem(Level.WARN, "未找到 Android udev 规则", "", key="udev"),
        ],
    )
    ok = menu._show_flash_preconditions()
    out = capsys.readouterr().out
    assert ok is True
    assert "有 2 项提示" in out


def test_startup_env_check_pauses_on_tty(monkeypatch):
    """启动自检通过后应暂停（tty 下），避免结果一闪而过。"""
    import inspect

    from lrom.tui import menu

    src = inspect.getsource(menu.startup_env_check)
    assert "LROM_SKIP_ENV_PAUSE" in src
    assert "按回车进入主菜单" in src
