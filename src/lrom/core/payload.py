"""Android OTA payload.bin 解析器。

只实现只读解析：从 payload.bin 的 manifest 中读出分区清单与操作序列，
用于判断一个 ROM 包能否用于线刷。

重要：本模块使用 AOSP 官方 update_metadata.proto 的字段语义。
不要参考任何裁剪过的 proto 定义 —— 紫罗兰工具箱仓库内的
`Protos/update_metadata.proto` 就是一个裁剪版，它的 InstallOperation.Type
枚举与 AOSP 原版完全不同（例如把 8 定义成 REPLACE_ZSTD，而 AOSP 原版是
REPLACE_XZ），照抄会导致把全量包误判为增量包。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class OpType(IntEnum):
    """AOSP chromeos_update_engine.InstallOperation.Type 官方枚举。"""

    REPLACE = 0
    REPLACE_BZ = 1
    MOVE = 2
    BSDIFF = 3
    SOURCE_COPY = 4
    SOURCE_BSDIFF = 5
    ZERO = 6
    DISCARD = 7
    REPLACE_XZ = 8
    PUFFDIFF = 9
    BROTLI_BSDIFF = 10
    ZUCCHINI = 11
    LZ4DIFF_BSDIFF = 12
    LZ4DIFF_PUFFDIFF = 13
    ZSTD = 14

    @property
    def label(self) -> str:
        return self.name


#: 这些操作自带完整数据，不需要设备上的源数据。
SELF_CONTAINED_OPS = frozenset(
    {OpType.REPLACE, OpType.REPLACE_BZ, OpType.REPLACE_XZ, OpType.ZSTD, OpType.ZERO}
)

#: 这些操作需要从设备现有分区读取源数据（增量 OTA 专用）。
SOURCE_DEPENDENT_OPS = frozenset(
    {
        OpType.MOVE,
        OpType.BSDIFF,
        OpType.SOURCE_COPY,
        OpType.SOURCE_BSDIFF,
        OpType.BROTLI_BSDIFF,
        OpType.ZUCCHINI,
        OpType.LZ4DIFF_BSDIFF,
        OpType.LZ4DIFF_PUFFDIFF,
        OpType.PUFFDIFF,
    }
)

PAYLOAD_MAGIC = b"CrAU"


# --------------------------------------------------------------------------
# protobuf 最小解码器（避免引入第三方依赖）
# --------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("protobuf varint 越界")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return result, pos


def _iter_fields(buf: bytes):
    """迭代 (field_number, wire_type, value)。"""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _read_varint(buf, pos)
        field_no = key >> 3
        wire_type = key & 0x07

        if wire_type == 0:  # varint
            value, pos = _read_varint(buf, pos)
        elif wire_type == 2:  # length-delimited
            length, pos = _read_varint(buf, pos)
            if pos + length > end:
                raise ValueError("protobuf 长度字段越界")
            value = buf[pos : pos + length]
            pos += length
        elif wire_type == 1:  # 64-bit
            value = buf[pos : pos + 8]
            pos += 8
        elif wire_type == 5:  # 32-bit
            value = buf[pos : pos + 4]
            pos += 4
        else:
            # 3/4 (group) 在 payload 中不会出现，遇到就停止
            break
        yield field_no, wire_type, value


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------

@dataclass
class Extent:
    start_block: int
    num_blocks: int


@dataclass
class InstallOperation:
    op_type: OpType
    data_offset: int = 0
    data_length: int = 0
    dst_extents: list[Extent] = field(default_factory=list)
    src_extents: list[Extent] = field(default_factory=list)
    data_sha256: bytes | None = None
    src_sha256: bytes | None = None

    @property
    def dst_blocks(self) -> int:
        return sum(e.num_blocks for e in self.dst_extents)

    @property
    def src_blocks(self) -> int:
        return sum(e.num_blocks for e in self.src_extents)


@dataclass
class PartitionUpdate:
    name: str
    operations: list[InstallOperation] = field(default_factory=list)

    @property
    def blocks(self) -> int:
        return sum(op.dst_blocks for op in self.operations)

    def size_bytes(self, block_size: int) -> int:
        return self.blocks * block_size

    @property
    def op_types(self) -> set[OpType]:
        return {op.op_type for op in self.operations}

    @property
    def needs_source_data(self) -> bool:
        """是否依赖设备上的源数据（即增量 OTA）。"""
        return any(op.op_type in SOURCE_DEPENDENT_OPS for op in self.operations)


@dataclass
class PayloadManifest:
    block_size: int = 4096
    partitions: list[PartitionUpdate] = field(default_factory=list)
    minor_version: int = 0

    def partition(self, name: str) -> PartitionUpdate | None:
        for p in self.partitions:
            if p.name == name:
                return p
        return None

    @property
    def is_full_payload(self) -> bool:
        """所有分区都不依赖源数据 => 全量包，可以解包成独立镜像。"""
        return not any(p.needs_source_data for p in self.partitions)

    @property
    def source_dependent_partitions(self) -> list[str]:
        return [p.name for p in self.partitions if p.needs_source_data]


# --------------------------------------------------------------------------
# 解析入口
# --------------------------------------------------------------------------

class PayloadFormatError(Exception):
    """payload.bin 结构异常。"""


def parse_manifest(buf: bytes) -> PayloadManifest:
    """解析 payload manifest 的 protobuf 字节串。"""
    manifest = PayloadManifest()

    for field_no, wire_type, value in _iter_fields(buf):
        if field_no == 3 and wire_type == 0:  # DeltaArchiveManifest.block_size
            manifest.block_size = int(value)
        elif field_no == 11 and wire_type == 0:  # minor_version
            manifest.minor_version = int(value)
        elif field_no == 13 and wire_type == 2:  # repeated partitions
            manifest.partitions.append(_parse_partition(value))

    return manifest


def _parse_partition(buf: bytes) -> PartitionUpdate:
    part = PartitionUpdate(name="")
    for field_no, wire_type, value in _iter_fields(buf):
        if field_no == 1 and wire_type == 2:  # PartitionUpdate.partition_name
            part.name = value.decode("utf-8", errors="replace")
        elif field_no == 8 and wire_type == 2:  # repeated operations
            part.operations.append(_parse_operation(value))
    return part


def _parse_operation(buf: bytes) -> InstallOperation:
    op = InstallOperation(op_type=OpType.REPLACE)
    for field_no, wire_type, value in _iter_fields(buf):
        if field_no == 1 and wire_type == 0:  # type
            try:
                op.op_type = OpType(int(value))
            except ValueError:
                raise PayloadFormatError(f"未知的 InstallOperation 类型: {value}") from None
        elif field_no == 2 and wire_type == 0:  # data_offset
            op.data_offset = int(value)
        elif field_no == 3 and wire_type == 0:  # data_length
            op.data_length = int(value)
        elif field_no == 6 and wire_type == 2:  # dst_extents
            op.dst_extents.append(_parse_extent(value))
        elif field_no == 7 and wire_type == 2:  # src_extents
            op.src_extents.append(_parse_extent(value))
        elif field_no == 8 and wire_type == 2:  # data_sha256_hash
            op.data_sha256 = bytes(value)
        elif field_no == 9 and wire_type == 2:  # src_sha256_hash
            op.src_sha256 = bytes(value)
    return op


def _parse_extent(buf: bytes) -> Extent:
    start = 0
    count = 0
    for field_no, wire_type, value in _iter_fields(buf):
        if field_no == 1 and wire_type == 0:
            start = int(value)
        elif field_no == 2 and wire_type == 0:
            count = int(value)
    return Extent(start_block=start, num_blocks=count)


def read_payload_header(fp) -> tuple[PayloadManifest, int, int]:
    """从已打开的 payload 流读取头部与 manifest。

    返回 (manifest, payload_data_start, metadata_signature_size)。

    payload 头部为大端 24 字节：
        magic(4s) version(u64) manifest_size(u64) metadata_signature_size(u32)
    """
    header = fp.read(24)
    if len(header) < 24:
        raise PayloadFormatError("payload 头部不足 24 字节")

    magic = header[:4]
    if magic != PAYLOAD_MAGIC:
        raise PayloadFormatError(f"payload magic 不匹配: {magic!r} (期望 {PAYLOAD_MAGIC!r})")

    version, manifest_size, signature_size = struct.unpack(">QQI", header[4:24])
    if version not in (2,):
        raise PayloadFormatError(f"不支持的 payload 版本: {version}")

    manifest_bytes = fp.read(manifest_size)
    if len(manifest_bytes) != manifest_size:
        raise PayloadFormatError(
            f"manifest 读取不完整: 期望 {manifest_size} 字节，实得 {len(manifest_bytes)}"
        )

    manifest = parse_manifest(manifest_bytes)

    # 数据区起点 = 头部 + manifest + metadata 签名
    data_start = 24 + manifest_size + signature_size
    return manifest, data_start, signature_size


def payload_data_offset_in_zip(zip_path: Path) -> tuple[int, int]:
    """对于 STORED（未压缩）的 payload.bin，返回 (数据起始偏移, 大小)。

    OTA 全量包里的 payload.bin 几乎总是 STORED，因此可以零拷贝直接 seek，
    无需解压 7GB。
    """
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo("payload.bin")
        if info.compress_type != zipfile.ZIP_STORED:
            raise PayloadFormatError(
                f"payload.bin 是压缩存储 (method={info.compress_type})，"
                "当前只支持 STORED 的 OTA 包"
            )
        with open(zip_path, "rb") as f:
            f.seek(info.header_offset)
            local = f.read(30)
            _, _, _, _, _, _, _, _, _, name_len, extra_len = struct.unpack(
                "<IHHHHHIIIHH", local
            )
            data_offset = info.header_offset + 30 + name_len + extra_len
            return data_offset, info.file_size
