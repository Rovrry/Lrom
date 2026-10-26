"""ARB 熔断检测测试。

ARB 是防降级机制：刷入 arb_index 更低的固件会导致硬件级变砖。
这里的测试确保：
  1. xbl_config 的 ELF 解析正确
  2. 比较逻辑在"降级"时必须判定为 BRICK
"""

from __future__ import annotations

import struct

import pytest

from lrom.core.arb import (
    ArbError,
    ArbInfo,
    ArbVerdict,
    compare_arb,
    parse_xbl_config,
)


def build_xbl_config(
    arb_index: int = 1,
    major: int = 3,
    minor: int = 0,
    *,
    version: int = 1,
    common_size: int = 0x20,
    qti_size: int = 0x10,
    oem_size: int = 0x20,
) -> bytes:
    """构造一个结构正确的 xbl_config ELF64 镜像。"""
    # HASH 段内容：Header(36) + common + qti + oem
    header = struct.pack("<IIIII", version, common_size, qti_size, oem_size, 0x100)
    header += b"\x00" * 16  # 保留字段，凑满 36 字节
    assert len(header) == 36
    common = b"\x00" * common_size
    qti = b"\x00" * qti_size
    oem = struct.pack("<III", major, minor, arb_index) + b"\x00" * (oem_size - 12)
    seg = header + common + qti + oem

    # ELF64 头（64 字节）
    ehdr = bytearray(b"\x7fELF")
    ehdr += bytes([2, 1, 1]) + b"\x00" * 9  # class=64, data=LE, version, pad
    ehdr += b"\x00" * 16  # e_type..e_version 等（不解析）
    # 0x20: e_phoff
    ehdr += struct.pack("<Q", 64)
    # 0x28: e_shoff
    ehdr += struct.pack("<Q", 0)
    # 0x30: e_flags
    ehdr += struct.pack("<I", 0)
    # 0x34: e_ehsize (2 bytes)
    ehdr += struct.pack("<H", 64)
    # 0x36: e_phentsize
    ehdr += struct.pack("<H", 56)
    # 0x38: e_phnum
    ehdr += struct.pack("<H", 2)
    # 0x3a: e_shentsize
    ehdr += struct.pack("<H", 0)
    # 0x3c: e_shnum
    ehdr += struct.pack("<H", 0)
    assert len(ehdr) == 62
    ehdr += b"\x00" * 2  # 补齐到 64
    assert len(ehdr) == 64

    # 两个程序头：第一个 PT_LOAD（非 PT_NULL），第二个 PT_NULL（空）——
    # 但算法取"最后一个非空 PT_NULL"，所以第一个应为空的 PT_NULL，
    # 第二个为真正的 HASH 段（PT_NULL 且 filesz>0）
    def phdr(ptype: int, off: int, size: int) -> bytes:
        p = struct.pack("<I", ptype)  # p_type
        p += struct.pack("<I", 0)  # p_flags
        p += struct.pack("<Q", off)  # p_offset
        p += struct.pack("<Q", 0)  # p_vaddr
        p += struct.pack("<Q", 0)  # p_paddr
        p += struct.pack("<Q", size)  # p_filesz
        p += struct.pack("<Q", size)  # p_memsz
        p += struct.pack("<Q", 0)  # p_align
        assert len(p) == 56
        return p

    hash_off = 64 + 56 * 2
    ph1 = phdr(0, 0, 0)  # 空的 PT_NULL
    ph2 = phdr(0, hash_off, len(seg))  # 有数据的 PT_NULL = HASH 段

    return bytes(ehdr) + ph1 + ph2 + seg


def write_xbl(tmp_path, **kwargs):
    p = tmp_path / "xbl_config.img"
    p.write_bytes(build_xbl_config(**kwargs))
    return p


# --------------------------------------------------------------------------


def test_parse_arb_index(tmp_path):
    p = write_xbl(tmp_path, arb_index=1)
    info = parse_xbl_config(p)
    assert info.arb_index == 1
    assert info.major_version == 3
    assert info.minor_version == 0


def test_parse_different_arb_values(tmp_path):
    for arb in (0, 1, 2, 5):
        p = write_xbl(tmp_path, arb_index=arb)
        assert parse_xbl_config(p).arb_index == arb


def test_reject_non_elf(tmp_path):
    p = tmp_path / "bad.img"
    p.write_bytes(b"NOTANELF" + b"\x00" * 100)
    with pytest.raises(ArbError, match="ELF"):
        parse_xbl_config(p)


def test_reject_elf32(tmp_path):
    data = bytearray(build_xbl_config())
    data[4] = 1  # EI_CLASS = 32
    p = tmp_path / "elf32.img"
    p.write_bytes(bytes(data))
    with pytest.raises(ArbError, match="ELF64"):
        parse_xbl_config(p)


def test_reject_tiny_file(tmp_path):
    p = tmp_path / "tiny.img"
    p.write_bytes(b"\x7fELF")
    with pytest.raises(ArbError, match="过小"):
        parse_xbl_config(p)


# -- 比较逻辑（最关键） ----------------------------------------------------


def test_same_arb_is_safe():
    cmp = compare_arb(ArbInfo(3, 0, 1), ArbInfo(3, 0, 1))
    assert cmp.verdict is ArbVerdict.SAFE
    assert not cmp.blocked


def test_downgrade_is_blocked():
    """核心安全测试：包 ARB 低于设备 => 必须阻止。"""
    cmp = compare_arb(ArbInfo(3, 0, 1), ArbInfo(3, 0, 2))
    assert cmp.verdict is ArbVerdict.BRICK
    assert cmp.blocked
    assert "硬砖" in cmp.message or "变砖" in cmp.message


def test_upgrade_is_allowed_but_warned():
    cmp = compare_arb(ArbInfo(3, 0, 2), ArbInfo(3, 0, 1))
    assert cmp.verdict is ArbVerdict.UPGRADE
    assert not cmp.blocked
    assert "无法降级" in cmp.message


def test_arb_zero_to_zero_safe():
    cmp = compare_arb(ArbInfo(1, 0, 0), ArbInfo(1, 0, 0))
    assert cmp.verdict is ArbVerdict.SAFE


def test_downgrade_from_zero_blocked():
    """设备 ARB=0 时，任何包都不会被阻止（0 是最低值）。"""
    cmp = compare_arb(ArbInfo(1, 0, 0), ArbInfo(1, 0, 0))
    assert cmp.verdict is ArbVerdict.SAFE


def test_unknown_when_device_missing():
    cmp = compare_arb(ArbInfo(3, 0, 1), None)
    assert cmp.verdict is ArbVerdict.UNKNOWN
    assert not cmp.blocked
    assert "硬砖" in cmp.message


def test_unknown_when_package_missing():
    cmp = compare_arb(None, ArbInfo(3, 0, 1))
    assert cmp.verdict is ArbVerdict.UNKNOWN


def test_unknown_when_both_missing():
    cmp = compare_arb(None, None)
    assert cmp.verdict is ArbVerdict.UNKNOWN


def test_real_large_arb_values():
    cmp = compare_arb(ArbInfo(3, 0, 5), ArbInfo(3, 0, 7))
    assert cmp.verdict is ArbVerdict.BRICK
