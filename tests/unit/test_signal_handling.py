"""Ctrl+C / KeyboardInterrupt 处理的回归测试。

真机实测：刷写完成后按 Ctrl+C，甩出 Python traceback。根因是
run_menu 的 finally 里 mon.refresh_now() 被 Ctrl+C 打断，且外层
except Exception 拦不住 KeyboardInterrupt（它不是 Exception 子类）。
"""

from __future__ import annotations

import inspect

from lrom.tui import menu as M


def test_safe_refresh_swallows_keyboard_interrupt():
    class _Mon:
        def refresh_now(self):
            raise KeyboardInterrupt

    # 不应抛出
    M._safe_refresh(_Mon())


def test_safe_refresh_swallows_any_exception():
    class _Mon:
        def refresh_now(self):
            raise RuntimeError("boom")

    M._safe_refresh(_Mon())


def test_safe_refresh_none_is_noop():
    M._safe_refresh(None)


def test_safe_pause_does_not_systemexit_on_ctrl_c(monkeypatch):
    def _raise(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", _raise)
    # 不应 raise SystemExit
    M._safe_pause()


def test_run_menu_uses_safe_refresh_in_finally():
    src = inspect.getsource(M.run_menu)
    assert "_safe_refresh(mon)" in src
    assert "except BaseException" in inspect.getsource(M._safe_refresh)


def test_run_menu_catches_keyboard_interrupt_at_top():
    src = inspect.getsource(M.run_menu)
    # 主循环外层应有 KeyboardInterrupt 处理，优雅退出
    assert "except KeyboardInterrupt" in src
