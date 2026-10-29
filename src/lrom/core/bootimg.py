"""Android boot image 解析（只读）。

这是「自动化 root」的基础：在 patch 之前，必须先确认
**这个镜像里到底有没有 ramdisk**，否则会静默失败。

为什么必须做这项检查：
  Magisk 的 ``boot_patch.sh`` 在找不到 ramdisk 时**不报错**，
  而是从零造一个空 ramdisk（源码可见
  ``else  # No ramdisk found, create one from scratch``）。
  结果是「patch 全成功、fastboot 刷入全 OK、开机正常、但没有 root」。
  KernelSU 的 Manager 在选错分区时也会得到类似结果。

  在本项目实测的 OPPO 设备上，``boot.img`` 的 ramdisk_size 就是 0
  （GKI 2.0 起 generic ramdisk 移到了 init_boot），
  照着网上教程去 patch ``boot.img`` 必然掉进这个坑。

实现要点（都是实测踩出来的）：
  1. header v0–v2 用 page_size（2048/4096/8192）对齐，
     v3–v4 固定按 4096 对齐。
  2. **不要信任 header 里的 ramdisk_size 字段**做解压缓冲区。
     本项目实测 init_boot 的该字段与实际 LZ4 帧长不符，
     按它分配缓冲区会直接解压失败。本模块只把它当作
     「有没有 ramdisk」的判据，不当作精确长度。
  3. AVB footer 的 ``orig_image_size`` 才是镜像的真实逻辑长度。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "BOOT_MAGIC",
    "AVB_FOOTER_MAGIC",
    "BootImageInfo",
    "BootImageError",
    "parse_boot_image",
    "check_ramdisk",
]

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"
AVB_FOOTER_MAGIC = b"AVBf"

# header v3/v4 固定页大小
_V3_PAGE_SIZE = 4096

# ramdisk 常见压缩格式的魔数
_RAMDISK_SIGNATURES = (
    (b"\x1f\x8b", "gzip"),
    (b"\x02\x21\x4c\x18", "lz4_legacy"),
    (b"\x04\x22\x4d\x18", "lz4_frame"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x5d\x00\x00", "lzma"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"070701", "cpio"),  # 未压缩
)


class BootImageError(Exception):
    """boot image 格式错误。"""


@dataclass
class BootImageInfo:
    """解析出的 boot image 头部信息。"""

    path: Path
    header_version: int
    kernel_size: int
    ramdisk_size: int
    os_version: int
    page_size: int
    total_size: int
    # AVB
    has_avb_footer: bool = False
    avb_orig_image_size: int = 0
    # 推导
    ramdisk_offset: int = 0
    ramdisk_compression: str | None = None

    @property
    def android_release(self) -> str:
        """os_version 字段里编码的 Android 版本（不是设备实际版本）。"""
        v = self.os_version
        return f"{v >> 25}.{(v >> 18) & 0x7F}.{(v >> 11) & 0x7F}"

    @property
    def security_patch_level(self) -> int:
        return self.os_version & 0x7FF

    @property
    def has_ramdisk(self) -> bool:
        return self.ramdisk_size > 0

    def describe(self) -> str:
        lines = [
            f"镜像      : {self.path.name}",
            f"文件大小  : {self.total_size:,} 字节",
            f"头部版本  : v{self.header_version}",
            f"页大小    : {self.page_size}",
            f"内核大小  : {self.kernel_size:,} 字节"
            + ("（无内核）" if self.kernel_size == 0 else ""),
            f"ramdisk   : {self.ramdisk_size:,} 字节"
            + ("（ 没有 ramdisk）" if not self.has_ramdisk else ""),
        ]
        if self.ramdisk_compression:
            lines.append(f"ramdisk取 : {self.ramdisk_compression}")
        if self.has_avb_footer:
            lines.append(f"AVB footer: 有（镜像逻辑长度 {self.avb_orig_image_size:,}）")
        else:
            lines.append("AVB footer: 无")
        return "\n".join(lines)


def _detect_compression(data: bytes) -> str | None:
    for magic, name in _RAMDISK_SIGNATURES:
        if data.startswith(magic):
            return name
    return None


def _find_avb_footer(data: bytes) -> tuple[bool, int]:
    """在镜像尾部 64 字节处查找 AVB footer。"""
    if len(data) < 64:
        return False, 0
    tail = data[-64:]
    if not tail.startswith(AVB_FOOTER_MAGIC):
        return False, 0
    # footer: magic[4] version[4] orig_image_size[8] vbmeta_offset[8] vbmeta_size[8] ...
    orig = struct.unpack(">Q", tail[12:20])[0]
    return True, orig


def parse_boot_image(path: Path) -> BootImageInfo:
    """解析 boot image（支持 header v0–v4）。

    Args:
        path: 镜像路径。

    Returns:
        BootImageInfo

    Raises:
        BootImageError: 不是合法 boot image。
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BootImageError(f"无法读取镜像 {path}: {exc}") from exc

    if len(raw) < 44:
        raise BootImageError(f"{path.name} 太小，不是 boot image（{len(raw)} 字节）")

    magic = raw[:8]
    if magic == VENDOR_BOOT_MAGIC:
        raise BootImageError(
            f"{path.name} 是 vendor_boot 镜像，不是 boot/init_boot。"
            "Lrom 目前不支持直接 patch vendor_boot。"
        )
    if magic != BOOT_MAGIC:
        raise BootImageError(
            f"{path.name} 不是 Android boot image（magic={magic!r}，期望 {BOOT_MAGIC!r}）"
        )

    header_version = struct.unpack_from("<I", raw, 40)[0]
    if header_version > 4:
        raise BootImageError(
            f"{path.name} 的 header 版本 v{header_version} 不认识（支持 v0–v4）"
        )

    if header_version <= 2:
        # v0–v2: 各段以 page_size 对齐
        page_size = struct.unpack_from("<I", raw, 36)[0]
        if page_size not in (2048, 4096, 8192, 16384):
            raise BootImageError(f"不合法的 page_size: {page_size}")
        kernel_size = struct.unpack_from("<I", raw, 8)[0]
        ramdisk_size = struct.unpack_from("<I", raw, 16)[0]
        os_version = struct.unpack_from("<I", raw, 44)[0] if len(raw) >= 48 else 0
        # v0 header 占 1 页；v1/v2 在 v0 基础上扩展但仍在第 0 页
        ramdisk_offset = page_size
        if kernel_size:
            ramdisk_offset += (kernel_size + page_size - 1) // page_size * page_size
    else:
        # v3/v4: 固定 4096 对齐，header 120 字节 + 可选 signature
        page_size = _V3_PAGE_SIZE
        kernel_size = struct.unpack_from("<I", raw, 8)[0]
        ramdisk_size = struct.unpack_from("<I", raw, 12)[0]
        os_version = struct.unpack_from("<I", raw, 16)[0]
        header_size = struct.unpack_from("<I", raw, 20)[0]
        sig_size = 0
        if header_version >= 4 and len(raw) >= header_size + 4:
            sig_size = struct.unpack_from("<I", raw, header_size)[0]
        ramdisk_offset = (header_size + sig_size + page_size - 1) // page_size * page_size
        if kernel_size:
            ramdisk_offset += (kernel_size + page_size - 1) // page_size * page_size

    has_avb, avb_orig = _find_avb_footer(raw)

    compression = None
    if ramdisk_size > 0 and ramdisk_offset < len(raw):
        compression = _detect_compression(raw[ramdisk_offset : ramdisk_offset + 8])

    return BootImageInfo(
        path=path,
        header_version=header_version,
        kernel_size=kernel_size,
        ramdisk_size=ramdisk_size,
        os_version=os_version,
        page_size=page_size,
        total_size=len(raw),
        has_avb_footer=has_avb,
        avb_orig_image_size=avb_orig,
        ramdisk_offset=ramdisk_offset,
        ramdisk_compression=compression,
    )


def check_ramdisk(path: Path, *, partition: str | None = None) -> tuple[bool, str]:
    """检查镜像是否含有 ramdisk —— 这是 patch 前的强制前置检查。

    返回:
        (是否可继续, 给用户看的说明)
    """
    info = parse_boot_image(path)
    name = partition or info.path.name

    if info.has_ramdisk:
        return True, (
            f"{name} 含 ramdisk（{info.ramdisk_size:,} 字节，"
            f"{info.ramdisk_compression or '未知压缩'}），可以 patch。"
        )

    # 没有 ramdisk —— 这是最危险的情况
    return False, (
        f" {name} 的 ramdisk_size = 0，**这个分区里没有 ramdisk**。\n"
        "\n"
        "   从 Android 13（GKI 2.0）起，通用 ramdisk 已从 boot 移到 init_boot。\n"
        "   而对没有 ramdisk 的镜像做 patch，Magisk / KernelSU 不会报错，\n"
        "   而是从零造一个空 ramdisk —— 结果是：patch 显示成功、刷入显示 OK、\n"
        "   开机也正常，但**完全没有 root**。\n"
        "\n"
        "   应该改用 init_boot 分区。可以这样取：\n"
        "     lrom unpack <ROM包> --partitions init_boot\n"
        "   或者用全量包里已解出的 init_boot.img。"
    )
