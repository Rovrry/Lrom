"""自动化 root 流程测试。

重点锁住：
  1. KMI 推导必须按内核版本，不是 Android 版本
     （本机实测：Android 16 但 KMI 是 6.1-android14-11）
  2. patch 前必须检查 ramdisk，没有则拒绝
  3. patch 后校验能识别越界、丢 ramdisk、丢 AVB footer
  4. 任何写操作之前必须先留存原厂镜像
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from lrom.core.root import (
    RootMethod,
    build_root_plan,
    derive_kmi,
    save_stock_image,
    verify_patched_image,
)


def make_boot_v4(
    *,
    kernel_size: int = 0,
    ramdisk_size: int = 0,
    with_avb: bool = True,
    header_version: int = 4,
    total_pad: int = 0,
) -> bytes:
    header = bytearray(1584)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, kernel_size)
    struct.pack_into("<I", header, 12, ramdisk_size)
    struct.pack_into("<I", header, 16, 0x1C0001A8)
    struct.pack_into("<I", header, 20, 1584)
    struct.pack_into("<I", header, 40, header_version)

    body = bytearray(header)
    body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if kernel_size:
        body += b"K" * kernel_size
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if ramdisk_size:
        body += b"\x02\x21\x4c\x18" + b"R" * (ramdisk_size - 4)
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if total_pad:
        body += b"\x00" * total_pad
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)

    if with_avb:
        footer = bytearray(64)
        footer[0:4] = b"AVBf"
        struct.pack_into(">I", footer, 4, 1)
        struct.pack_into(">Q", footer, 12, len(body))
        body += footer
    else:
        body += b"\x00" * 64
    return bytes(body)


# ---------------------------------------------------------------------------
# KMI 推导
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "release,expected",
    [
        # 本项目真机实测值
        ("6.1.141-android14-11-o-g5cdab3b518c1", "6.1-android14-11"),
        # KernelSU 官方文档的例子
        ("5.10.101-android12-9-g30979850fc20", "5.10-android12-9"),
        # SubLevel 不属于 KMI
        ("5.10.137-android12-9-gabcdef", "5.10-android12-9"),
        ("6.6.30-android16-6-abc", "6.6-android16-6"),
        ("6.12.1-android16-6-g1234567", "6.12-android16-6"),
    ],
)
def test_derive_kmi(release: str, expected: str):
    assert derive_kmi(release) == expected


def test_derive_kmi_sublevel_not_part_of_kmi():
    """同一 KMI 下不同 SubLevel 必须推导为相同结果。"""
    a = derive_kmi("6.1.141-android14-11-o-g5cdab3b518c1")
    b = derive_kmi("6.1.166-android14-11-o-gdeadbeef")
    assert a == b == "6.1-android14-11"


def test_derive_kmi_android16_kernel_still_android14():
    """关键：设备是 Android 16，但内核是 android14-11，KMI 必须取内核的。

    照网上教程按 Android 版本选 android16-6.12 必然失败。
    """
    kmi = derive_kmi("6.1.141-android14-11-o-g5cdab3b518c1")
    assert kmi == "6.1-android14-11"
    assert not kmi.startswith("android16")


@pytest.mark.parametrize("release", ["", "garbage", "6.1.141", None])
def test_derive_kmi_unparseable(release):
    assert derive_kmi(release) is None


# ---------------------------------------------------------------------------
# 计划生成
# ---------------------------------------------------------------------------


class FakeDevice:
    serial = "test123"
    slot = "a"
    locked = "0"


def test_plan_blocks_boot_image(tmp_path: Path):
    """patch boot.img（无 ramdisk）必须被阻止。"""
    img = tmp_path / "boot.img"
    img.write_bytes(make_boot_v4(kernel_size=8192, ramdisk_size=0))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert plan.can_proceed is False
    assert any("ramdisk" in b for b in plan.blockers)


def test_plan_accepts_init_boot(tmp_path: Path, monkeypatch):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    # APP 方式不需要 ksud，应该可以继续
    assert plan.can_proceed is True
    assert plan.partition == "init_boot"
    assert plan.image_size == 8192 + 4096 + 64


def test_plan_missing_image(tmp_path: Path):
    plan = build_root_plan(
        image=tmp_path / "nope.img", method=RootMethod.APP, device=FakeDevice()
    )
    assert plan.can_proceed is False
    assert any("不存在" in b for b in plan.blockers)


def test_plan_ksud_requires_tool(tmp_path: Path):
    """ksud 方式在找不到 ksud 时必须阻止，并给出获取方法。"""
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.KSUD, device=FakeDevice())
    assert plan.can_proceed is False
    blocked = " ".join(plan.blockers)
    assert "ksud" in blocked
    assert "github.com/tiann/KernelSU" in blocked


def test_plan_warns_never_auto_reboot(tmp_path: Path):
    """项目铁律：必须提示手动重启。"""
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert any("手动重启" in w for w in plan.warnings)


def test_plan_warns_ota_loses_root(tmp_path: Path):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert any("OTA" in w for w in plan.warnings)


def test_plan_describe_contains_key_info(tmp_path: Path):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    text = plan.describe()
    assert "init_boot" in text
    assert "test123" in text


# ---------------------------------------------------------------------------
# 原厂镜像留存
# ---------------------------------------------------------------------------


def test_save_stock_image_creates_copy_and_manifest(tmp_path: Path):
    src = tmp_path / "init_boot.img"
    src.write_bytes(make_boot_v4(ramdisk_size=8192))

    work = tmp_path / "root-work"
    dest = save_stock_image(src, workdir=work, partition="init_boot")

    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()

    meta_path = dest.with_suffix(".json")
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["partition"] == "init_boot"
    assert meta["size"] == len(src.read_bytes())
    assert len(meta["sha256"]) == 64


def test_save_stock_image_sha_matches(tmp_path: Path):
    import hashlib

    src = tmp_path / "init_boot.img"
    data = make_boot_v4(ramdisk_size=8192)
    src.write_bytes(data)

    dest = save_stock_image(src, workdir=tmp_path / "w", partition="init_boot")
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# patch 后校验
# ---------------------------------------------------------------------------


def test_verify_rejects_image_exceeding_partition(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, total_pad=16384))

    ok, problems = verify_patched_image(
        stock=stock, patched=patched, partition_size=5000
    )
    assert ok is False
    assert any("超过分区" in p for p in problems)


def test_verify_rejects_lost_ramdisk(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=0))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("ramdisk" in p for p in problems)


def test_verify_rejects_lost_avb_footer(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192, with_avb=True))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, with_avb=False))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("AVB" in p for p in problems)


def test_verify_rejects_header_version_change(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192, header_version=3))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, header_version=4))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("header 版本" in p for p in problems)


def test_verify_accepts_good_patch(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    # 大小变了但结构完整 —— 正常的 patch 就是这样
    patched.write_bytes(make_boot_v4(ramdisk_size=16384))

    ok, problems = verify_patched_image(
        stock=stock, patched=patched, partition_size=1_000_000
    )
    assert ok is True
    assert problems == []


def test_verify_handles_missing_patched_file(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))

    ok, problems = verify_patched_image(
        stock=stock, patched=tmp_path / "missing.img"
    )
    assert ok is False
    assert any("不存在" in p for p in problems)
