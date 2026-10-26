"""解包器测试。

用合成 payload 验证：正确解压、extent 写入位置、SHA-256 校验，
以及"遇到增量操作必须拒绝"这条安全红线。
"""

from __future__ import annotations

import bz2
import hashlib
import io
import lzma

import pytest

from lrom.core.payload import OpType, read_payload_header
from lrom.core.unpack import ExtractionError, extract_partition

from .test_payload import f_bytes, f_varint, make_extent, make_manifest


def build_payload(spec, block_size=4096):
    """构造完整 payload。

    spec: [(partition_name, [(op_type, dst_extents, src_extents, raw_data)])]
    """
    datas: list[bytes] = []
    offset = 0
    part_bytes_list = []
    for name, ops in spec:
        ops_bytes = b""
        for op_type, dst, src, data in ops:
            op = f_varint(1, int(op_type))
            if data is not None:
                op += f_varint(2, offset)
                op += f_varint(3, len(data))
                datas.append(data)
                offset += len(data)
                op += f_bytes(8, hashlib.sha256(data).digest())
            for start, count in dst:
                op += f_bytes(6, make_extent(start, count))
            for start, count in src or []:
                op += f_bytes(7, make_extent(start, count))
            ops_bytes += f_bytes(8, op)
        part_bytes_list.append(f_bytes(1, name.encode()) + ops_bytes)

    manifest_bytes = make_manifest(part_bytes_list, block_size)
    header = (
        b"CrAU"
        + (2).to_bytes(8, "big")
        + len(manifest_bytes).to_bytes(8, "big")
        + (0).to_bytes(4, "big")
    )
    stream = header + manifest_bytes + b"".join(datas)
    return stream, 24 + len(manifest_bytes)


def test_extract_replace_roundtrip(tmp_path):
    # REPLACE 的数据长度必须等于 dst_extents 覆盖的字节数（真实 payload 语义）
    payload = (b"HELLO-WORLD" * 400)[:4096]
    assert len(payload) == 4096
    stream, hdr_end = build_payload(
        [("boot", [(OpType.REPLACE, [(0, 1)], None, payload)])]
    )
    fp = io.BytesIO(stream)
    mf, header_end, _ = read_payload_header(fp)
    assert header_end == hdr_end

    out = tmp_path / "boot.img"
    res = extract_partition(fp, mf.partitions[0], out, block_size=4096, payload_base=hdr_end)

    assert res.ok
    assert res.size == 4096
    assert out.read_bytes() == payload


def test_extract_xz(tmp_path):
    raw = b"X" * 4096
    stream, hdr_end = build_payload(
        [("system", [(OpType.REPLACE_XZ, [(0, 1)], None, lzma.compress(raw))])]
    )
    fp = io.BytesIO(stream)
    mf, _, _ = read_payload_header(fp)

    out = tmp_path / "system.img"
    extract_partition(fp, mf.partitions[0], out, block_size=4096, payload_base=hdr_end)
    assert out.read_bytes() == raw


def test_extract_bz2(tmp_path):
    raw = b"B" * 8192
    stream, hdr_end = build_payload(
        [("vendor", [(OpType.REPLACE_BZ, [(0, 2)], None, bz2.compress(raw))])]
    )
    fp = io.BytesIO(stream)
    mf, _, _ = read_payload_header(fp)

    out = tmp_path / "vendor.img"
    extract_partition(fp, mf.partitions[0], out, block_size=4096, payload_base=hdr_end)
    assert out.read_bytes() == raw


def test_extract_zero_fills(tmp_path):
    stream, hdr_end = build_payload([("meta", [(OpType.ZERO, [(0, 2)], None, None)])])
    fp = io.BytesIO(stream)
    mf, _, _ = read_payload_header(fp)

    out = tmp_path / "meta.img"
    res = extract_partition(
        fp, mf.partitions[0], out, block_size=4096, payload_base=hdr_end
    )
    assert res.size == 8192
    assert out.read_bytes() == b"\x00" * 8192


def test_extract_multiple_extents_places_data_correctly(tmp_path):
    # 两个 extent 各 1 块 => 数据必须 8192 字节
    data = b"A" * 4096 + b"B" * 4096
    stream, hdr_end = build_payload(
        [("x", [(OpType.REPLACE, [(0, 1), (2, 1)], None, data)])]
    )
    fp = io.BytesIO(stream)
    mf, _, _ = read_payload_header(fp)

    out = tmp_path / "x.img"
    extract_partition(fp, mf.partitions[0], out, block_size=4096, payload_base=hdr_end)
    content = out.read_bytes()
    assert len(content) == 3 * 4096
    assert content[0:4096] == b"A" * 4096
    assert content[4096:8192] == b"\x00" * 4096   # 空洞
    assert content[8192:12288] == b"B" * 4096


def test_rejects_source_copy(tmp_path):
    """安全红线：增量操作必须拒绝，绝不输出残缺镜像。"""
    stream, hdr_end = build_payload(
        [("system", [(OpType.SOURCE_COPY, [(0, 1)], [(10, 1)], None)])]
    )
    fp = io.BytesIO(stream)
    mf, _, _ = read_payload_header(fp)

    with pytest.raises(ExtractionError, match="增量包"):
        extract_partition(
            fp, mf.partitions[0], tmp_path / "s.img",
            block_size=4096, payload_base=hdr_end,
        )
    assert list(tmp_path.iterdir()) == []


def test_rejects_bad_data_hash(tmp_path):
    """数据哈希不匹配时必须报错，而不是静默写出坏镜像。"""
    stream, hdr_end = build_payload(
        [("boot", [(OpType.REPLACE, [(0, 1)], None, b"G" * 4096)])]
    )
    tampered = bytearray(stream)
    tampered[-1] ^= 0xFF
    fp = io.BytesIO(bytes(tampered))
    mf, _, _ = read_payload_header(fp)

    with pytest.raises(ExtractionError, match="SHA-256"):
        extract_partition(
            fp, mf.partitions[0], tmp_path / "boot.img",
            block_size=4096, payload_base=hdr_end,
        )
    assert list(tmp_path.iterdir()) == []
