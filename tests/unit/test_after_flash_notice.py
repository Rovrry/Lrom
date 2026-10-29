"""刷写完成后「格式化数据分区」提醒的测试。

背景（联网调研结论）：
  · 降级 / 救砖后必须清的是**数据分区（userdata）**，即「格式化数据分区 / 清除数据」，
    **不是「格式化系统分区」**（system/vendor 由刷包覆盖，单独格式化无效且危险）；
  · 时机：刷写完成之后、首次开机进系统之前。
"""

from __future__ import annotations

from lrom.cli import _print_after_flash_notice


def _text(capsys) -> str:
    return capsys.readouterr().out


def test_notice_no_wipe_downgrade_rescue_mentions_format_data(capsys):
    _print_after_flash_notice(None, wipe=False)
    out = _text(capsys)
    assert "格式化数据分区" in out
    assert "fastboot -w" in out
    assert "Recovery" in out
    assert "首次开机前" in out
    # 纠正术语：不是格式化系统分区
    assert "不是系统分区" in out
    # 旧错误说法不应再作为指令出现
    assert "必须先格式化系统分区" not in out


def test_notice_wipe_already_done_guides_recovery(capsys):
    _print_after_flash_notice(None, wipe=True)
    out = _text(capsys)
    assert "已执行" in out and "fastboot -w" in out
    assert "Recovery" in out


def test_notice_rescue_adds_extra_warning(capsys):
    _print_after_flash_notice(None, wipe=False, rescue=True)
    out = _text(capsys)
    assert "救砖（仅FBD）后强烈建议格式化数据分区" in out


def test_cmd_flash_passes_rescue_flag_from_fbd_only():
    """cmd_flash 必须把 fbd_only 作为 rescue 传进后续提示。"""
    import inspect

    import lrom.cli as cli

    src = inspect.getsource(cli.cmd_flash)
    assert "rescue=getattr(args, \"fbd_only\", False)" in src
