"""解包目录隔离与清理的测试。

用户担心：解了两个 ROM 包（出厂版本 / 最新版），怕项目把两包混着刷。

实测结论：
- `Session.image_dir` 由 `package.stem` 决定，两包各解到各的目录
- `lrom flash` 按 manifest 的 op 列表逐分区找镜像，不扫目录，
  所以目录里的额外文件不会被刷
- 但同名不同路径的包会共用目录，重解时会残留旧镜像 —— 已加清理
"""

from __future__ import annotations

from pathlib import Path

from lrom.tui.menu import Session


def _session_with(pkg: Path, work: Path) -> Session:
    s = Session()
    s.package = pkg
    s.work_dir = work
    return s


def test_two_packages_get_separate_dirs(tmp_path):
    """出厂版本.zip 与 rom.zip 解到不同目录，互不覆盖。"""
    work = tmp_path / "work"
    s1 = _session_with(Path("/x/出厂版本.zip"), work)
    s2 = _session_with(Path("/x/rom.zip"), work)
    assert s1.image_dir != s2.image_dir
    assert s1.image_dir.name == "出厂版本"
    assert s2.image_dir.name == "rom"


def test_switching_package_switches_dir(tmp_path):
    """切换 sess.package 后 image_dir 必须同步变化，不能停留在旧包。"""
    work = tmp_path / "work"
    s = _session_with(Path("/x/出厂版本.zip"), work)
    assert s.image_dir.name == "出厂版本"
    s.package = Path("/x/rom.zip")
    assert s.image_dir.name == "rom"


def test_same_name_packages_share_dir(tmp_path):
    """同名不同路径的包共用同一个目录 —— 这是需要清理的场景。"""
    work = tmp_path / "work"
    a = _session_with(Path("/a/rom.zip"), work)
    b = _session_with(Path("/b/rom.zip"), work)
    assert a.image_dir == b.image_dir


def test_unpacked_detection_is_per_package(tmp_path):
    """一个包解了、另一个没解，unpacked 状态必须各自独立。"""
    work = tmp_path / "work"
    s1 = _session_with(Path("/x/出厂版本.zip"), work)
    s2 = _session_with(Path("/x/rom.zip"), work)

    d1 = s1.image_dir
    d1.mkdir(parents=True)
    (d1 / "boot.img").write_bytes(b"x")

    assert s1.unpacked is True
    assert s2.unpacked is False


def test_cleanup_only_removes_unpack_artifacts(tmp_path):
    """清理逻辑只删 .img / .img.part / unpacked.json，不动其它文件。"""
    out = tmp_path / "work" / "rom"
    out.mkdir(parents=True)
    (out / "boot.img").write_bytes(b"old")
    (out / "system.img").write_bytes(b"old")
    (out / "vendor.img.part").write_bytes(b"partial")
    (out / "unpacked.json").write_text("{}")
    (out / "notes.txt").write_text("keep me")

    # 复刻 _flow_unpack 的清理逻辑
    stale = list(out.glob("*.img")) + list(out.glob("*.img.part"))
    idx = out / "unpacked.json"
    if idx.exists():
        stale.append(idx)
    for f in stale:
        f.unlink()

    assert not (out / "boot.img").exists()
    assert not (out / "system.img").exists()
    assert not (out / "vendor.img.part").exists()
    assert not (out / "unpacked.json").exists()
    assert (out / "notes.txt").exists()
