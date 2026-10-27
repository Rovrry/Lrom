"""解包目录按版本号命名 + root 前机型/版本核对的测试。

需求背景：
1. 下载来的 ROM 包文件名常被随机化/改过，目录名应按包内**版本号**
   而非文件名。
2. root 与回滚 root 都会写 init_boot，必须先核对机型与系统版本，
   避免把别的机型的镜像刷进去导致无法开机。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lrom.tui.menu import Session, _sanitize_dirname


# ---------------------------------------------------------------------------
# 目录命名
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("OPD2404_16.0.5.1201(CN01)", "OPD2404_16.0.5.1201_CN01"),
        ("rom.zip", "rom.zip"),  # 扩展名的点保留
        ("a/b\\c", "a_b_c"),
        ("", "unnamed"),
        ("...", "unnamed"),
    ],
)
def test_sanitize_dirname(raw, expected):
    assert _sanitize_dirname(raw) == expected


def test_image_dir_uses_version_not_filename(tmp_path):
    """包文件名是随机的，目录必须按版本号。"""
    s = Session()
    s.work_dir = tmp_path
    # 真实存在包，version_name 才有值；这里用真实包
    pkg = Path("/home/rov/桌面/rom.zip")
    if not pkg.exists():
        pytest.skip("测试包不存在")
    s.package = pkg
    d = s.image_dir
    assert d.name.startswith("OPD2404_")
    assert d.name != "rom"


def test_existing_old_dir_still_recognized(tmp_path):
    """旧的按文件名解包目录仍然能被识别（向后兼容）。"""
    s = Session()
    s.work_dir = tmp_path
    pkg = Path("/home/rov/桌面/rom.zip")
    if not pkg.exists():
        pytest.skip("测试包不存在")
    s.package = pkg
    old = tmp_path / "rom"
    old.mkdir()
    (old / "boot.img").write_bytes(b"x")
    # 旧目录存在且有序镜像 → 优先复用，避免用户已解包的失效
    assert s.image_dir == old


# ---------------------------------------------------------------------------
# 备份元数据
# ---------------------------------------------------------------------------


def test_stock_backup_records_device_info(tmp_path):
    """save_stock_image 必须记录机型与版本，供回滚核对。"""
    from lrom.core.root import save_stock_image

    img = tmp_path / "init_boot.img"
    img.write_bytes(b"BLK" * 100)

    dest = save_stock_image(
        img,
        workdir=tmp_path / "root-work",
        partition="init_boot",
        device_model="OPD2404",
        device_codename="OP5D77L1",
        device_build_id="OPD2404_16.0.5.1201(CN01)",
    )
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["device_model"] == "OPD2404"
    assert meta["device_codename"] == "OP5D77L1"
    assert meta["device_build_id"] == "OPD2404_16.0.5.1201(CN01)"


# ---------------------------------------------------------------------------
# root 前核对
# ---------------------------------------------------------------------------


class _Snap:
    def __init__(self, mode="adb", model="OPD2404", codename="OP5D77L1", build="OPD2404_16.0.5.1201(CN01)", locked="0", present=True):
        self.present = present
        self.mode = mode
        self.model = model
        self.codename = codename
        self.build_id = build
        self.locked = locked


def _sess(pkg="/home/rov/桌面/rom.zip"):
    s = Session()
    s.package = Path(pkg) if pkg else None
    return s


def test_verify_rejects_no_device():
    from lrom.tui.menu import _verify_device_for_root

    snap = _Snap(present=False)
    assert _verify_device_for_root(snap, _sess(), action="root") is False


def test_verify_rejects_locked():
    from lrom.tui.menu import _verify_device_for_root

    assert _verify_device_for_root(_Snap(locked="1"), _sess(), action="root") is False


def test_verify_rejects_fastboot_mode():
    from lrom.tui.menu import _verify_device_for_root

    assert _verify_device_for_root(_Snap(mode="fastboot"), _sess(), action="root") is False


def test_verify_rejects_model_mismatch():
    """一加 15 连上、选的是平板包 → 必须拒绝。"""
    from lrom.tui.menu import _verify_device_for_root

    pkg = Path("/home/rov/桌面/rom.zip")
    if not pkg.exists():
        pytest.skip("测试包不存在")
    oneplus15 = _Snap(model="PLK110", codename="PLK110", build="PLK110_17.0.0.105(CN01)")
    assert _verify_device_for_root(oneplus15, _sess(str(pkg)), action="root") is False


def test_verify_accepts_matching_device(capsys):
    """平板 + 平板包 → 放行。"""
    from lrom.tui.menu import _verify_device_for_root

    pkg = Path("/home/rov/桌面/rom.zip")
    if not pkg.exists():
        pytest.skip("测试包不存在")
    pad = _Snap()
    assert _verify_device_for_root(pad, _sess(str(pkg)), action="root") is True
    out = capsys.readouterr().out
    assert "机型匹配" in out
