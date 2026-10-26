"""boot image 解析测试。

这些测试锁住几个**会导致静默失败**的关键行为：
  1. boot.img（ramdisk_size=0）必须被 check_ramdisk 拦下
  2. init_boot.img（有 ramdisk）必须放行
  3. header v0–v4 的偏移计算必须正确

用真实镜像结构构造的合成数据，不依赖仓库里的 unpacked/ 目录。
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lrom.core.bootimg import (
    BootImageError,
    check_ramdisk,
    parse_boot_image,
)


# ---------------------------------------------------------------------------
# 构造合成 boot image
# ---------------------------------------------------------------------------


def make_boot_v4(
    *,
    kernel_size: int = 0,
    ramdisk_size: int = 0,
    ramdisk_magic: bytes = b"\x02\x21\x4c\x18",  # LZ4 legacy
    with_avb: bool = False,
    avb_orig_size: int = 0,
) -> bytes:
    """构造一个 header v4 的 boot image。"""
    header = bytearray(1584)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, kernel_size)
    struct.pack_into("<I", header, 12, ramdisk_size)
    struct.pack_into("<I", header, 16, 0x1C0001A8)  # os_version
    struct.pack_into("<I", header, 20, 1584)  # header_size
    struct.pack_into("<I", header, 40, 4)  # header_version
    struct.pack_into("<I", header, 1584 - 4, 0)  # signature_size = 0

    body = bytearray(header)
    # 页对齐到 4096
    body += b"\x00" * (4096 - len(body) % 4096 if len(body) % 4096 else 0)

    if kernel_size:
        body += b"K" * kernel_size
        pad = (4096 - len(body) % 4096) % 4096
        body += b"\x00" * pad

    if ramdisk_size:
        payload = ramdisk_magic + b"R" * (ramdisk_size - len(ramdisk_magic))
        body += payload
        pad = (4096 - len(body) % 4096) % 4096
        body += b"\x00" * pad

    # 尾部留 64 字节给 AVB footer
    body += b"\x00" * (64 - len(body) % 64 if len(body) % 64 else 0)

    if with_avb:
        footer = bytearray(64)
        footer[0:4] = b"AVBf"
        struct.pack_into(">I", footer, 4, 1)
        struct.pack_into(">Q", footer, 12, avb_orig_size or len(body) - 64)
        body += footer
    else:
        body += b"\x00" * 64

    return bytes(body)


def make_boot_v2(*, kernel_size: int = 0, ramdisk_size: int = 0) -> bytes:
    """构造一个 header v2 的 boot image（2048 页）。"""
    page = 2048
    header = bytearray(page)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, kernel_size)
    struct.pack_into("<I", header, 12, 0)  # kernel_addr
    struct.pack_into("<I", header, 16, ramdisk_size)
    struct.pack_into("<I", header, 20, 0)  # ramdisk_addr
    struct.pack_into("<I", header, 36, page)  # page_size
    struct.pack_into("<I", header, 40, 2)  # header_version
    body = bytearray(header)
    if kernel_size:
        body += b"K" * kernel_size
        pad = (page - len(body) % page) % page
        body += b"\x00" * pad
    if ramdisk_size:
        body += b"\x1f\x8b" + b"R" * (ramdisk_size - 2)
        pad = (page - len(body) % page) % page
        body += b"\x00" * pad
    return bytes(body)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_boot_without_ramdisk_is_detected(tmp_path: Path):
    """boot.img 没有 ramdisk —— 这是必须被拦下的情况。"""
    p = tmp_path / "boot.img"
    p.write_bytes(make_boot_v4(kernel_size=4096, ramdisk_size=0))

    info = parse_boot_image(p)
    assert info.header_version == 4
    assert info.kernel_size == 4096
    assert info.ramdisk_size == 0
    assert info.has_ramdisk is False


def test_init_boot_with_ramdisk_passes_check(tmp_path: Path):
    """init_boot.img 有 ramdisk —— 应该放行。"""
    p = tmp_path / "init_boot.img"
    p.write_bytes(make_boot_v4(kernel_size=0, ramdisk_size=8192))

    ok, msg = check_ramdisk(p, partition="init_boot")
    assert ok is True
    assert "init_boot" in msg


def test_check_ramdisk_blocks_boot_image(tmp_path: Path):
    """关键回归测试：boot.img 必须被 BLOCKED，并给出改用 init_boot 的指引。

    这条对应真实踩坑：Magisk 的 boot_patch.sh 在没有 ramdisk 时不报错，
    而是从零造一个，结果是 patch 成功但无 root。
    """
    p = tmp_path / "boot.img"
    p.write_bytes(make_boot_v4(kernel_size=4096, ramdisk_size=0))

    ok, msg = check_ramdisk(p, partition="boot")
    assert ok is False
    assert "ramdisk_size = 0" in msg
    assert "init_boot" in msg


def test_lz4_legacy_ramdisk_detected(tmp_path: Path):
    p = tmp_path / "init_boot.img"
    p.write_bytes(make_boot_v4(ramdisk_size=8192, ramdisk_magic=b"\x02\x21\x4c\x18"))

    info = parse_boot_image(p)
    assert info.ramdisk_compression == "lz4_legacy"


def test_gzip_ramdisk_detected(tmp_path: Path):
    p = tmp_path / "boot.img"
    p.write_bytes(make_boot_v2(ramdisk_size=4096))

    info = parse_boot_image(p)
    assert info.ramdisk_compression == "gzip"


def test_header_v2_uses_page_aligned_offsets(tmp_path: Path):
    """v0–v2 用 page_size 对齐，不是固定 4096。"""
    p = tmp_path / "old_boot.img"
    p.write_bytes(make_boot_v2(kernel_size=3000, ramdisk_size=4096))

    info = parse_boot_image(p)
    assert info.header_version == 2
    assert info.page_size == 2048
    # header 1 页 + kernel 对齐到 2 页
    assert info.ramdisk_offset == 2048 + 4096


def test_vendor_boot_rejected_with_clear_message(tmp_path: Path):
    p = tmp_path / "vendor_boot.img"
    p.write_bytes(b"VNDRBOOT" + b"\x00" * 200)

    with pytest.raises(BootImageError) as exc:
        parse_boot_image(p)
    assert "vendor_boot" in str(exc.value)


def test_non_boot_image_rejected(tmp_path: Path):
    p = tmp_path / "random.img"
    p.write_bytes(b"\xde\xad\xbe\xef" * 64)

    with pytest.raises(BootImageError) as exc:
        parse_boot_image(p)
    assert "不是 Android boot image" in str(exc.value)


def test_unknown_header_version_rejected(tmp_path: Path):
    p = tmp_path / "future.img"
    data = bytearray(make_boot_v4(kernel_size=4096, ramdisk_size=4096))
    struct.pack_into("<I", data, 40, 99)
    p.write_bytes(bytes(data))

    with pytest.raises(BootImageError) as exc:
        parse_boot_image(p)
    assert "v99" in str(exc.value)


def test_avb_footer_detected(tmp_path: Path):
    p = tmp_path / "init_boot.img"
    p.write_bytes(
        make_boot_v4(ramdisk_size=8192, with_avb=True, avb_orig_size=8192)
    )

    info = parse_boot_image(p)
    assert info.has_avb_footer is True
    assert info.avb_orig_image_size == 8192


def test_os_version_decoding(tmp_path: Path):
    """os_version 里的 Android 版本与 SPL 应能解出。"""
    p = tmp_path / "init_boot.img"
    p.write_bytes(make_boot_v4(ramdisk_size=8192))

    info = parse_boot_image(p)
    # 0x1C0001A8 -> Android 14.0.0, SPL 424
    assert info.android_release == "14.0.0"
    assert info.security_patch_level == 424


def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(BootImageError):
        parse_boot_image(tmp_path / "nope.img")


@pytest.mark.parametrize("size", [4096, 8192, 16384])
def test_various_ramdisk_sizes(tmp_path: Path, size: int):
    p = tmp_path / f"r{size}.img"
    p.write_bytes(make_boot_v4(ramdisk_size=size))

    info = parse_boot_image(p)
    assert info.ramdisk_size == size
    assert info.has_ramdisk is True
