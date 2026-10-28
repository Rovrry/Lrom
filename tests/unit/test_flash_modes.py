"""线刷三档模式（普通 / 强力 / 回锁）测试。

背景：v0.7.8 之前菜单 3「线刷全量包」被硬编码为「刷到另一槽」
（为配合 BL 回锁的平刷而改）。现改为在线刷前让用户三选一：

  · 普通线刷：只刷当前槽，**不重建**逻辑分区（对应紫罗兰「普通模式」）
  · 强力线刷：只刷当前槽，**强制重建**逻辑分区（对应紫罗兰「强力线刷」）
  · 回锁线刷：刷到**另一槽**（防 A/B 槽不一致，配合回锁前洗原厂）

这里锁住三档到 cmd_flash 参数（no_recreate_logical / to_opposite_slot）的
映射，防止再次出现「改了流程但没接对参数」的回归（见 DEVNOTES 1.8）。
"""

from __future__ import annotations

import io
import contextlib
import types

import pytest

from lrom.tui.menu import FlashMode, _choose_flash_mode, _flow_flash


# ---------------------------------------------------------------------------
# FlashMode 属性映射
# ---------------------------------------------------------------------------

def test_flash_mode_props():
    assert FlashMode.NORMAL.label == "普通线刷"
    assert FlashMode.STRONG.label == "强力线刷"
    assert FlashMode.RELOCK.label == "回锁线刷"

    # 槽位：只有回锁线刷刷另一槽
    assert FlashMode.NORMAL.to_opposite_slot is False
    assert FlashMode.STRONG.to_opposite_slot is False
    assert FlashMode.RELOCK.to_opposite_slot is True

    # 逻辑分区重建：强力和回锁都重建；普通线刷（紫罗兰「普通模式」）不重建
    assert FlashMode.NORMAL.recreate_logical is False
    assert FlashMode.STRONG.recreate_logical is True
    assert FlashMode.RELOCK.recreate_logical is True


# ---------------------------------------------------------------------------
# 模式选择交互
# ---------------------------------------------------------------------------

@pytest.fixture
def _mock_ask(monkeypatch):
    """把 menu.ask 换成按序列吐答案的桩，返回设置函数。"""

    def _set(seqs):
        import lrom.tui.menu as menu

        it = iter(seqs)
        monkeypatch.setattr(menu, "ask", lambda *a, **k: next(it))

    return _set


def test_choose_mode_numbers(_mock_ask):
    _mock_ask(["1"])
    assert _choose_flash_mode(False) is FlashMode.NORMAL
    _mock_ask(["2"])
    assert _choose_flash_mode(False) is FlashMode.STRONG
    _mock_ask(["3"])
    assert _choose_flash_mode(False) is FlashMode.RELOCK


def test_choose_mode_cancel(_mock_ask):
    for token in ("0", "", "q"):
        _mock_ask([token])
        assert _choose_flash_mode(False) is None


def test_choose_mode_retries_on_invalid(_mock_ask):
    # 先输错 9，再输 2 → 最终应得到强力线刷
    _mock_ask(["9", "2"])
    assert _choose_flash_mode(False) is FlashMode.STRONG


# ---------------------------------------------------------------------------
# _flow_flash → cmd_flash 参数映射（回归保护）
# ---------------------------------------------------------------------------

class _Sess:
    package = "x.zip"
    image_dir = "/tmp"

    @property
    def unpacked(self) -> bool:
        return True


class _Mon:
    def refresh_now(self):
        return types.SimpleNamespace(present=True)


def _run_flow(monkeypatch, mode_token: str) -> dict:
    """跑一次 _flow_flash（真实刷写分支），返回传给 cmd_flash 的 ns。"""
    import lrom.tui.menu as menu
    import lrom.cli as cli

    captured: dict = {}

    def fake_cmd_flash(ns):
        captured.update(vars(ns))

    monkeypatch.setattr(cli, "cmd_flash", fake_cmd_flash)
    monkeypatch.setattr(menu, "pause", lambda *a, **k: None)

    # ask 第一次用于选模式；之后用于「清空数据？」→ 返回空（保留数据）
    answers = iter([mode_token, ""])
    monkeypatch.setattr(menu, "ask", lambda *a, **k: next(answers, ""))

    with contextlib.redirect_stdout(io.StringIO()):
        _flow_flash(_Sess(), _Mon(), dry_run=False)
    return captured


def test_flow_normal_flashes_current_slot_no_recreate(monkeypatch):
    ns = _run_flow(monkeypatch, "1")
    assert ns["no_recreate_logical"] is True   # 不重建
    assert ns["to_opposite_slot"] is False      # 当前槽


def test_flow_strong_recreates_on_current_slot(monkeypatch):
    ns = _run_flow(monkeypatch, "2")
    assert ns["no_recreate_logical"] is False   # 强制重建
    assert ns["to_opposite_slot"] is False      # 当前槽


def test_flow_relock_flashes_opposite_slot_and_recreates(monkeypatch):
    ns = _run_flow(monkeypatch, "3")
    assert ns["no_recreate_logical"] is False   # 回锁也重建（否则大镜像 resize 失败）
    assert ns["to_opposite_slot"] is True       # 刷另一槽


def test_flow_cancel_mode_writes_nothing(monkeypatch):
    """选模式时取消（0），不应调用 cmd_flash。"""
    import lrom.tui.menu as menu
    import lrom.cli as cli

    called = {"n": 0}

    def fake_cmd_flash(ns):
        called["n"] += 1

    monkeypatch.setattr(cli, "cmd_flash", fake_cmd_flash)
    monkeypatch.setattr(menu, "pause", lambda *a, **k: None)
    monkeypatch.setattr(menu, "ask", lambda *a, **k: "0")

    with contextlib.redirect_stdout(io.StringIO()):
        _flow_flash(_Sess(), _Mon(), dry_run=False)
    assert called["n"] == 0
