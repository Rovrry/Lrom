"""终端输出样式（备份/信息展示共用的底色块与时长相助）。

设计目标（本轮需求）：
  · 不同类别的输出用**不同文字底色**区分（成功=绿底，警告=黄底，错误/重要=红底，信息=蓝底）；
  · 重要提示、报错直接用**红色底色**；
  · 提供统一的时长格式化，供备份进度实时显示，避免用户误以为卡死。

注意：非 TTY（管道/重定向/测试）时一律返回纯文本，不输出 ANSI 转义，保证可读与可断言。
"""

from __future__ import annotations

import sys


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    # 前景
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    BLACK = "\033[30m"
    WHITE = "\033[97m"
    # 背景
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


def use_color() -> bool:
    return sys.stdout.isatty()


def paint(text: str, style: str = "") -> str:
    return f"{style}{text}{Style.RESET}" if use_color() else text


def band(text: str, bg: str, fg: str = "", *, bold: bool = False) -> str:
    """把文字渲染成「底色块」。"""
    if not use_color():
        return text
    lead = Style.BOLD if bold else ""
    return f"{lead}{bg}{fg}{text}{Style.RESET}"


def status(kind: str, text: str) -> str:
    """按类别渲染状态文字。

    约定（本轮调整）：**只保留红色文字底色**用于「重要 / 报错 / 危险」；
    成功 / 警告 / 信息等改用前景色（不再加底色），避免整屏色块过重。
    """
    if kind in ("error", "important", "danger"):
        return band(f" {text} ", Style.BG_RED, Style.WHITE, bold=True)

    fg_map = {
        "ok": Style.GREEN,
        "success": Style.GREEN,
        "warn": Style.YELLOW,
        "warning": Style.YELLOW,
        "info": Style.CYAN,
        "note": Style.DIM,
    }
    fg = fg_map.get(kind, Style.CYAN)
    return paint(f" {text} ", f"{fg}{Style.BOLD}")


def format_duration(seconds: float) -> str:
    """秒 → ``HH:MM:SS``（不足 1 小时则 ``MM:SS``）。"""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def format_bytes(n: float) -> str:
    """字节 → 人类可读（GB/MB）。"""
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KB"
    return f"{int(n)} B"
