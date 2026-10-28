"""解包/备份「只保留最新一份」清理 + 回滚默认用解包镜像 的测试。"""

from __future__ import annotations

import json
from pathlib import Path

from lrom.core.unpack import prune_other_unpacked
from lrom.core.backup import prune_other_backups


def _mk_unpacked(base: Path, name: str, marker=True):
    d = base / name
    d.mkdir(parents=True)
    (d / "init_boot.img").write_bytes(b"x")
    if marker:
        (d / "unpacked.json").write_text("{}")
    return d


def test_prune_other_unpacked_keeps_only_latest(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    a = _mk_unpacked(work, "OPD2404_16.0.5")
    b = _mk_unpacked(work, "OPD2404_16.0.9")
    # 非解包目录（无 unpacked.json）不应被删
    other = work / "misc"
    other.mkdir()
    (other / "note.txt").write_text("hi")

    removed = prune_other_unpacked(b, [work])
    assert set(removed) == {a}
    assert b.exists()
    assert other.exists()  # 不误删
    assert not a.exists()


def test_prune_other_unpacked_noop_if_only_one(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    a = _mk_unpacked(work, "only")
    assert prune_other_unpacked(a, [work]) == []
    assert a.exists()


def _mk_backup(base: Path, name: str):
    d = base / name
    d.mkdir(parents=True)
    (d / "boot.img").write_bytes(b"x")
    (d / "backup.json").write_text("{}")
    return d


def test_prune_other_backups_keeps_only_latest(tmp_path):
    base = tmp_path / "backups"
    base.mkdir()
    old = _mk_backup(base, "OPD2404_20261001-000000")
    new = _mk_backup(base, "OPD2404_20261029-000000")
    removed = prune_other_backups(new, base)
    assert set(removed) == {old}
    assert new.exists() and not old.exists()


def test_prune_other_backups_noop_if_only_one(tmp_path):
    base = tmp_path / "backups"
    base.mkdir()
    a = _mk_backup(base, "only")
    assert prune_other_backups(a, base) == []
    assert a.exists()


# ---------------------------------------------------------------------------
# 线刷前「是否备份分区」提示（可选、非强制）
# ---------------------------------------------------------------------------

def test_flow_flash_prompts_backup_and_runs_it(monkeypatch, tmp_path):
    import types
    import lrom.tui.menu as menu
    import lrom.cli as cli

    # sess：已解包
    class _Sess:
        package = "x.zip"
        image_dir = tmp_path

        @property
        def unpacked(self):
            return True

    class _Mon:
        def refresh_now(self):
            return types.SimpleNamespace(present=True)

    monkeypatch.setattr(menu, "_choose_flash_mode", lambda dry: menu.FlashMode.NORMAL)
    monkeypatch.setattr(menu, "_warn_edl_risk", lambda info, mon: False)
    monkeypatch.setattr(menu, "pause", lambda *a, **k: None)

    calls = {"flash": 0, "backup": 0}

    # 输入序列：清空数据提示(n) → 是否备份(y) → 无
    answers = iter(["n", "y"])
    monkeypatch.setattr(menu, "ask", lambda *a, **k: next(answers, ""))

    monkeypatch.setattr(cli, "cmd_flash", lambda ns: calls.__setitem__("flash", calls["flash"] + 1))
    monkeypatch.setattr(cli, "cmd_backup", lambda ns: calls.__setitem__("backup", calls["backup"] + 1))

    menu._flow_flash(_Sess(), _Mon(), dry_run=False)
    assert calls["backup"] == 1, "选了 y 应执行备份"
    assert calls["flash"] == 1, "备份后应继续刷写"


def test_flow_flash_backup_is_optional(monkeypatch, tmp_path):
    import types
    import lrom.tui.menu as menu
    import lrom.cli as cli

    class _Sess:
        package = "x.zip"
        image_dir = tmp_path

        @property
        def unpacked(self):
            return True

    class _Mon:
        def refresh_now(self):
            return types.SimpleNamespace(present=True)

    monkeypatch.setattr(menu, "_choose_flash_mode", lambda dry: menu.FlashMode.NORMAL)
    monkeypatch.setattr(menu, "_warn_edl_risk", lambda info, mon: False)
    monkeypatch.setattr(menu, "pause", lambda *a, **k: None)

    calls = {"flash": 0, "backup": 0}
    answers = iter(["n", "n"])  # 不备份
    monkeypatch.setattr(menu, "ask", lambda *a, **k: next(answers, ""))
    monkeypatch.setattr(cli, "cmd_flash", lambda ns: calls.__setitem__("flash", calls["flash"] + 1))
    monkeypatch.setattr(cli, "cmd_backup", lambda ns: calls.__setitem__("backup", calls["backup"] + 1))

    menu._flow_flash(_Sess(), _Mon(), dry_run=False)
    assert calls["backup"] == 0, "默认不强制备份"
    assert calls["flash"] == 1


# ---------------------------------------------------------------------------
# root 留存原厂镜像：源就是解包出来的原厂镜像时不再重复复制
# ---------------------------------------------------------------------------

def test_save_stock_image_copy_true_makes_copy(tmp_path):
    from lrom.core.root import save_stock_image

    img = tmp_path / "unpacked" / "init_boot.img"
    img.parent.mkdir()
    img.write_bytes(b"\x00" * 64)
    wd = tmp_path / "root-work"
    out = save_stock_image(img, workdir=wd, partition="init_boot", copy_file=True)
    assert out.exists() and out.suffix == ".img"
    assert out.parent == wd
    assert list(wd.glob("init_boot-stock-*.json"))


def test_save_stock_image_copy_false_only_records(tmp_path):
    from lrom.core.root import save_stock_image
    import json

    img = tmp_path / "unpacked" / "init_boot.img"
    img.parent.mkdir()
    img.write_bytes(b"\x00" * 64)
    wd = tmp_path / "root-work"
    out = save_stock_image(img, workdir=wd, partition="init_boot", copy_file=False)
    # 不产生 .img 副本
    assert not list(wd.glob("init_boot-stock-*.img"))
    # 返回源路径
    assert out == img
    # 记录了指纹
    metas = list(wd.glob("init_boot-stock-*.json"))
    assert len(metas) == 1
    data = json.loads(metas[0].read_text(encoding="utf-8"))
    assert data["copy_file"] is False
    assert data["source"] == str(img)
    assert len(data["sha256"]) == 64
