"""终端输出样式（ui.py）测试 + 「项目不再使用 emoji」回归闸。

本轮需求：
  · 按输出类别给不同文字底色；重要提示/报错 → 红色底色；
  · 去掉项目里的 emoji / 象形符号；
  · 备份时显示时长，避免误判卡死。
"""

from __future__ import annotations

import pathlib

from lrom import ui


def test_format_duration():
    assert ui.format_duration(0) == "00:00"
    assert ui.format_duration(65) == "01:05"
    assert ui.format_duration(3725) == "1:02:05"


def test_format_bytes():
    assert ui.format_bytes(512) == "512 B"
    assert ui.format_bytes(2 * 1024 ** 2) == "2.0 MB"
    assert ui.format_bytes(3 * 1024 ** 3) == "3.00 GB"


def test_status_is_plain_when_not_tty():
    # pytest 的 stdout 不是 TTY，应返回纯文本（便于断言/重定向）
    out = ui.status("error", "失败")
    assert "失败" in out
    assert "\033[" not in out


def test_no_pictograph_emoji_in_source():
    """项目源码里不应再残留 ✓ ✗ ⚠ ⛔ ★ ● ○ 🔒 或其它 emoji。"""
    bad = set("\u2713\u2717\u26a0\u26d4\u2605\u25cf\u25cb\U0001f512")
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "lrom"
    offenders = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for ch in text:
            if ch in bad or 0x1F000 <= ord(ch) <= 0x1FAFF:
                offenders.append((p.name, hex(ord(ch))))
                break
    assert not offenders, f"仍有 emoji/象形符号: {offenders}"
