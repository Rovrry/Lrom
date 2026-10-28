"""从 payload.bin 提取分区镜像。

只支持全量包（所有操作都自带数据）。增量包会被上层拒绝，因此这里
遇到 SOURCE_COPY / BSDIFF 等需要源数据的操作时直接报错退出，绝不输出
残缺镜像 —— 这是 Windows 版工具最危险的缺陷。

实现要点：
  - 零拷贝读取：payload.bin 在 ZIP 中是 STORED，用文件偏移直接 seek
  - 流式解压，逐 extent 写入，不把整个分区读进内存
  - 每个操作校验 data_sha256（如 manifest 提供了）
  - 输出 sparse 或 raw，默认 raw（fastboot 能直接吃 raw）
"""

from __future__ import annotations

import bz2
import hashlib
import lzma
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .payload import (
    SOURCE_DEPENDENT_OPS,
    InstallOperation,
    OpType,
    PartitionUpdate,
    PayloadManifest,
    payload_data_offset_in_zip,
    read_payload_header,
)


class ExtractionError(Exception):
    """镜像提取失败。"""


@dataclass
class ExtractResult:
    partition: str
    path: Path
    size: int
    sha256: str
    ok: bool = True
    error: str = ""

    @property
    def size_mb(self) -> float:
        return self.size / 1e6


def _decompress_blob(op: InstallOperation, data: bytes) -> bytes:
    """解压单个操作的数据块。"""
    if op.op_type is OpType.REPLACE:
        return data
    if op.op_type is OpType.REPLACE_BZ:
        return bz2.decompress(data)
    if op.op_type is OpType.REPLACE_XZ:
        return lzma.decompress(data)
    if op.op_type is OpType.ZSTD:
        try:
            import zstandard  # type: ignore
        except ImportError as exc:
            raise ExtractionError(
                "该包使用 ZSTD 压缩，需要安装 python3-zstandard"
            ) from exc
        return zstandard.ZstdDecompressor().decompress(data, max_output_size=256 << 20)
    raise ExtractionError(f"不支持的操作类型: {op.op_type.label}")


def extract_partition(
    payload_fp,
    part: PartitionUpdate,
    out_path: Path,
    *,
    block_size: int,
    payload_base: int,
    progress: Callable[[int, int], None] | None = None,
) -> ExtractResult:
    """提取单个分区到 out_path。"""
    # 前置安全检查：任何需要源数据的操作 => 拒绝
    for op in part.operations:
        if op.op_type in SOURCE_DEPENDENT_OPS:
            raise ExtractionError(
                f"分区 {part.name} 含 {op.op_type.label} 操作，需要设备源数据。"
                f"这是增量包，不能解包成独立镜像。"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_blocks = part.blocks
    written = 0
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    hasher = hashlib.sha256()
    try:
        with open(tmp, "wb") as out:
            # 预分配到最终大小，避免碎片（用 truncate，稀疏文件）
            out.truncate(total_blocks * block_size)

            for op in part.operations:
                if op.op_type is OpType.ZERO:
                    # 文件已预置为 0，无需写入
                    written += op.dst_blocks
                    if progress:
                        progress(written, total_blocks)
                    continue

                # payload_base 是相对 payload.bin 起点的偏移（= 24 + manifest + 签名）
                payload_fp.seek(payload_base + op.data_offset)
                raw = payload_fp.read(op.data_length)
                if len(raw) != op.data_length:
                    raise ExtractionError(
                        f"{part.name}: 数据读取不完整 "
                        f"({len(raw)}/{op.data_length})"
                    )

                # 校验操作数据的哈希
                if op.data_sha256:
                    if hashlib.sha256(raw).digest() != op.data_sha256:
                        raise ExtractionError(
                            f"{part.name}: 操作数据 SHA-256 不匹配（包可能损坏）"
                        )

                data = _decompress_blob(op, raw)

                expected = op.dst_blocks * block_size
                if len(data) != expected:
                    raise ExtractionError(
                        f"{part.name}: 解压后大小不符 "
                        f"({len(data)} != {expected})"
                    )

                # 按 dst_extents 写入
                pos = 0
                for ext in op.dst_extents:
                    n = ext.num_blocks * block_size
                    out.seek(ext.start_block * block_size)
                    chunk = data[pos : pos + n]
                    out.write(chunk)
                    pos += n

                written += op.dst_blocks
                if progress:
                    progress(written, total_blocks)

        # 计算整文件哈希
        with open(tmp, "rb") as f:
            while block := f.read(1 << 20):
                hasher.update(block)

    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    tmp.replace(out_path)
    size = out_path.stat().st_size
    return ExtractResult(
        partition=part.name, path=out_path, size=size, sha256=hasher.hexdigest()
    )


def unpack_package(
    package_path: Path,
    out_dir: Path,
    *,
    partitions: list[str] | None = None,
    progress_cb: Callable[[str, int, int], None] | None = None,
    result_cb: Callable[[ExtractResult], None] | None = None,
) -> list[ExtractResult]:
    """解包一个 ROM 包的所有（或指定）分区。"""
    package_path = Path(package_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_offset, _ = payload_data_offset_in_zip(package_path)

    results: list[ExtractResult] = []
    with open(package_path, "rb") as pkg:
        pkg.seek(data_offset)
        manifest, header_end, _ = read_payload_header(pkg)
        # op.data_offset 是相对 payload 数据区起点的偏移；
        # 该数据区的绝对位置 = ZIP 内 payload.bin 起点 + (24 + manifest + 签名)
        payload_base_in_stream = data_offset + header_end

        bs = manifest.block_size
        wanted = set(partitions) if partitions else None

        for part in manifest.partitions:
            if wanted and part.name not in wanted:
                continue

            cb = None
            if progress_cb:
                cb = lambda done, total, name=part.name: progress_cb(name, done, total)

            try:
                res = extract_partition(
                    pkg,
                    part,
                    out_dir / f"{part.name}.img",
                    block_size=bs,
                    payload_base=payload_base_in_stream,
                    progress=cb,
                )
            except ExtractionError as exc:
                res = ExtractResult(
                    partition=part.name,
                    path=out_dir / f"{part.name}.img",
                    size=0,
                    sha256="",
                    ok=False,
                    error=str(exc),
                )
            results.append(res)
            if result_cb:
                result_cb(res)

    return results


def write_index(results: list[ExtractResult], out_dir: Path) -> Path:
    """写出解包索引，记录每个镜像的大小与哈希。"""
    import json

    path = out_dir / "unpacked.json"
    path.write_text(
        json.dumps(
            {
                "tool": "lrom",
                "partitions": [
                    {
                        "name": r.partition,
                        "file": r.path.name,
                        "size": r.size,
                        "sha256": r.sha256,
                        "ok": r.ok,
                        "error": r.error,
                    }
                    for r in results
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# 解包产物清理：只保留最新一份
# ---------------------------------------------------------------------------

def prune_other_unpacked(keep: Path, bases: list[Path]) -> list[Path]:
    """删除除 keep 以外的其它「解包产物目录」，让本地只保留最新一份。

    安全前提（很重要，否则会误删用户数据）：
      · **只有 keep 目录真实存在且含有 *.img 时才动手**；否则一律不删。
        （解包失败 / keep 还没生成时删旧目录 = 数据丢失）
      · 只删「目录里有 unpacked.json 标记」的目录，绝不碰其它目录。

    返回被删除的目录列表。
    """
    import shutil

    try:
        if not keep.is_dir() or not any(keep.glob("*.img")):
            return []
    except OSError:
        return []

    keep_resolved = keep.resolve()
    removed: list[Path] = []
    for base in bases:
        try:
            if not base.is_dir():
                continue
        except OSError:
            continue
        for child in base.iterdir():
            try:
                if not child.is_dir():
                    continue
                if child.resolve() == keep_resolved:
                    continue
                if not (child / "unpacked.json").exists():
                    continue
                shutil.rmtree(child)
                removed.append(child)
            except OSError:
                continue
    return removed
