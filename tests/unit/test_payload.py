"""payload 解析器单元测试。

这些测试不依赖任何真实 ROM 包：用合成数据构造 manifest，
确保解析逻辑和 AOSP 枚举映射正确。
"""

from __future__ import annotations

import struct

import pytest

from lrom.core.payload import (
    OpType,
    PayloadFormatError,
    parse_manifest,
    read_payload_header,
)

# --------------------------------------------------------------------------
# 手工构造 protobuf 的小工具
# --------------------------------------------------------------------------


def varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def tag(field_no: int, wire_type: int) -> bytes:
    return varint((field_no << 3) | wire_type)


def f_varint(field_no: int, value: int) -> bytes:
    return tag(field_no, 0) + varint(value)


def f_bytes(field_no: int, value: bytes) -> bytes:
    return tag(field_no, 2) + varint(len(value)) + value


def make_extent(start: int, count: int) -> bytes:
    return f_varint(1, start) + f_varint(2, count)


def make_op(
    op_type: int,
    *,
    data_length: int = 0,
    dst: list[tuple[int, int]] | None = None,
    src: list[tuple[int, int]] | None = None,
) -> bytes:
    out = f_varint(1, op_type)
    if data_length:
        out += f_varint(3, data_length)
    # dst=None 表示"用默认一个 extent"；dst=[] 表示"明确不写 dst_extents"
    dst_list = [(0, 1)] if dst is None else dst
    for start, count in dst_list:
        out += f_bytes(6, make_extent(start, count))
    for start, count in src or []:
        out += f_bytes(7, make_extent(start, count))
    return out


def make_partition(name: str, ops: list[bytes]) -> bytes:
    out = f_bytes(1, name.encode())
    for op in ops:
        out += f_bytes(8, op)
    return out


def make_manifest(partitions: list[bytes], block_size: int = 4096) -> bytes:
    out = f_varint(3, block_size)
    for p in partitions:
        out += f_bytes(13, p)
    return out


# --------------------------------------------------------------------------
# 测试
# --------------------------------------------------------------------------


def test_aosp_enum_mapping_is_correct():
    """AOSP 官方枚举不能被裁剪版 proto 误导。

    这是本项目最容易出错的地方：紫罗兰工具箱仓库内的裁剪版 proto
    把 4 定义成 ZERO、8 定义成 REPLACE_ZSTD，与 AOSP 完全不符。
    """
    assert OpType.REPLACE == 0
    assert OpType.REPLACE_BZ == 1
    assert OpType.BSDIFF == 3
    assert OpType.SOURCE_COPY == 4
    assert OpType.SOURCE_BSDIFF == 5
    assert OpType.ZERO == 6
    assert OpType.REPLACE_XZ == 8
    assert OpType.BROTLI_BSDIFF == 10
    assert OpType.ZUCCHINI == 11
    assert OpType.ZSTD == 14


def test_parse_simple_full_payload():
    op = make_op(OpType.REPLACE_XZ, data_length=1000, dst=[(0, 10)])
    mf = parse_manifest(make_manifest([make_partition("boot", [op])]))

    assert mf.block_size == 4096
    assert len(mf.partitions) == 1
    part = mf.partitions[0]
    assert part.name == "boot"
    assert part.operations[0].op_type is OpType.REPLACE_XZ
    assert part.operations[0].data_length == 1000
    assert part.blocks == 10
    assert not part.needs_source_data
    assert mf.is_full_payload


def test_parse_delta_payload_detects_source_copy():
    op = make_op(OpType.SOURCE_COPY, dst=[(0, 10)], src=[(100, 10)])
    mf = parse_manifest(make_manifest([make_partition("system", [op])]))

    part = mf.partitions[0]
    assert part.operations[0].op_type is OpType.SOURCE_COPY
    assert part.needs_source_data
    assert not mf.is_full_payload
    assert mf.source_dependent_partitions == ["system"]


def test_repalce_xz_is_not_source_copy():
    """回归测试：REPLACE_XZ(8) 绝不能被误判为 SOURCE_COPY。

    这正是把全量包错判成增量包的根因。
    """
    op = make_op(OpType.REPLACE_XZ, data_length=5000, dst=[(0, 5)])
    mf = parse_manifest(make_manifest([make_partition("system", [op])]))

    assert mf.partitions[0].needs_source_data is False
    assert mf.is_full_payload


def test_multiple_partitions_and_blocks():
    ops_a = [
        make_op(OpType.REPLACE, data_length=4096, dst=[(0, 1)]),
        make_op(OpType.ZERO, dst=[(1, 2)]),
    ]
    ops_b = [make_op(OpType.REPLACE_BZ, data_length=2048, dst=[(0, 4)])]
    mf = parse_manifest(
        make_manifest(
            [make_partition("boot", ops_a), make_partition("system", ops_b)]
        )
    )

    assert [p.name for p in mf.partitions] == ["boot", "system"]
    assert mf.partitions[0].blocks == 3
    assert mf.partitions[1].blocks == 4
    assert mf.block_size == 4096
    assert mf.partitions[1].size_bytes(4096) == 4 * 4096


def test_read_header_big_endian():
    """payload 头部使用大端序。"""
    manifest_bytes = make_manifest(
        [make_partition("boot", [make_op(OpType.REPLACE, data_length=10)])]
    )
    sig = b"\x00" * 16
    header = b"CrAU" + struct.pack(">QQI", 2, len(manifest_bytes), len(sig))
    stream = header + manifest_bytes + sig

    import io

    mf, data_start, sig_size = read_payload_header(io.BytesIO(stream))
    assert sig_size == 16
    assert data_start == 24 + len(manifest_bytes) + 16
    assert mf.partitions[0].name == "boot"


def test_bad_magic_rejected():
    import io

    with pytest.raises(PayloadFormatError, match="magic"):
        read_payload_header(io.BytesIO(b"XXXX" + b"\x00" * 20))


def test_truncated_header_rejected():
    import io

    with pytest.raises(PayloadFormatError, match="头部"):
        read_payload_header(io.BytesIO(b"CrAU"))
