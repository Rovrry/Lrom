"""ARB（Anti-Rollback）熔断检测。

ARB 是高通平台的防降级机制：每代 bootloader 有一个递增的 `arb_index`，
一旦设备的 efuse 被"熔断"到某个值，刷入 arb_index 更低的固件会导致
**硬件级不可恢复**（无法再开机，只能换主板/字库）。

因此降级前必须比较：
    包内 xbl_config 的 arb_index  vs  设备当前已熔断的 arb_index
- 包 < 设备  => 硬砖，必须拒绝
- 包 = 设备  => 安全
- 包 > 设备  => 安全，但会熔断到更高值，此后无法再降级到更低版本

数据来源：
- 包侧：xbl_config.img（ELF64，OEM metadata 中的 arb_index）
- 设备侧：优先读 fuse；读不到时退化为读取设备当前 xbl_config 分区
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

#: HASH Table Segment Header 的固定长度（version..hash_table_size + 16 字节保留）
_HASH_HEADER_SIZE = 36
#: 扫描 HASH 段头部的最大范围
_SCAN_LIMIT = 0x1000


class ArbError(Exception):
    """ARB 解析失败。"""


class ArbVerdict(Enum):
    """ARB 比较结论。"""

    SAFE = "安全（版本相同）"
    UPGRADE = "安全（升级，将熔断到更高值）"
    BRICK = "禁止（降级会变砖）"
    UNKNOWN = "未知（无法获取一方数据）"


@dataclass
class ArbInfo:
    """单个 xbl_config 的 ARB 信息。"""

    major_version: int
    minor_version: int
    arb_index: int
    source: str = ""

    def describe(self) -> str:
        return (
            f"ARB={self.arb_index} "
            f"(version {self.major_version}.{self.minor_version})"
        )


@dataclass
class ArbComparison:
    package: ArbInfo | None
    device: ArbInfo | None
    verdict: ArbVerdict
    message: str

    @property
    def blocked(self) -> bool:
        return self.verdict is ArbVerdict.BRICK


# --------------------------------------------------------------------------
# xbl_config 解析
# --------------------------------------------------------------------------

def parse_xbl_config(path: str | Path) -> ArbInfo:
    """从 xbl_config 镜像中提取 ARB 索引。

    结构（与高通 `arbextract` 一致）：
      1. ELF64 头部（小端）
      2. 遍历程序头，取**最后一个非空 PT_NULL 段**作为 HASH 段
      3. 在 HASH 段前 0x1000 字节内扫描 Hash Table Segment Header：
           version(u32) common_size(u32) qti_size(u32) oem_size(u32) hash_table_size(u32)
      4. OEM metadata 紧跟 common/qti metadata 之后，前 12 字节为：
           major(u32) minor(u32) arb_index(u32)
    """
    path = Path(path)
    data = path.read_bytes()

    if len(data) < 64:
        raise ArbError("文件过小，不是有效的 xbl_config 镜像")

    if data[:4] != b"\x7fELF":
        raise ArbError("不是 ELF 镜像")
    if data[4] != 2:
        raise ArbError("不是 ELF64 格式")
    if data[5] != 1:
        raise ArbError("不是小端序 ELF")

    (phoff,) = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)

    if phentsize < 56 or phnum == 0:
        raise ArbError("ELF 程序头信息无效")

    hash_off = 0
    hash_size = 0
    # 从后向前找最后一个非空 PT_NULL 段
    for idx in range(phnum - 1, -1, -1):
        entry = phoff + idx * phentsize
        if entry + 56 > len(data):
            continue
        seg_type = struct.unpack_from("<I", data, entry)[0]
        seg_off = struct.unpack_from("<Q", data, entry + 0x08)[0]
        seg_filesz = struct.unpack_from("<Q", data, entry + 0x20)[0]
        if seg_type == 0 and seg_filesz > 0:
            hash_off, hash_size = seg_off, seg_filesz
            break

    if hash_size == 0:
        raise ArbError("未找到 xbl_config 的 HASH 段")

    if hash_off + hash_size > len(data):
        raise ArbError("HASH 段超出文件范围")

    seg = data[hash_off : hash_off + hash_size]

    header_off = -1
    common_size = qti_size = oem_size = 0
    for off in range(0, min(_SCAN_LIMIT, len(seg) - _HASH_HEADER_SIZE + 1), 4):
        version, common, qti, oem, htab = struct.unpack_from("<IIIII", seg, off)
        if (
            1 <= version <= 10
            and common <= 0x1000
            and 12 <= oem <= 0x4000
            and htab <= 0x4000
        ):
            end = off + _HASH_HEADER_SIZE + common + qti + oem
            if end <= len(seg):
                header_off = off
                common_size, qti_size, oem_size = common, qti, oem
                break

    if header_off < 0:
        raise ArbError("未找到 HASH Table Segment Header")

    oem_off = header_off + _HASH_HEADER_SIZE + common_size + qti_size
    if oem_off + 12 > len(seg):
        raise ArbError("OEM Metadata 范围无效")

    major, minor, arb = struct.unpack_from("<III", seg, oem_off)
    return ArbInfo(major_version=major, minor_version=minor, arb_index=arb, source=str(path))


# --------------------------------------------------------------------------
# 设备侧读取
# --------------------------------------------------------------------------

def read_device_arb_adb(adb, serial: str | None = None) -> ArbInfo | None:
    """通过 adb + root 读取设备当前 xbl_config 的 ARB。

    优先读 fuse（ro.boot.arb 等属性）；不可用时从 xbl_config 分区回读。
    """
    if serial:
        adb.serial = serial

    # 1) 属性方式（最快，但多数设备不暴露）
    for prop in ("ro.boot.arb", "ro.oplus.security.arb", "ro.vendor.oplus.arb"):
        try:
            val = adb.getprop(prop, timeout=10)
        except Exception:
            continue
        if val and val.isdigit():
            return ArbInfo(0, 0, int(val), source=f"prop:{prop}")

    # 2) 在设备上直接解析 xbl_config（需要 root）
    #    先快速探测 su 是否可用，避免在无 root 设备上长时间阻塞
    try:
        probe = adb.shell("su -c id", timeout=8)
    except Exception:
        return None
    if probe.exit_code != 0 or "uid=0" not in probe.stdout:
        return None

    import base64
    import tempfile

    script = (
        "su -c 'dd if=/dev/block/by-name/xbl_config$(getprop ro.boot.slot_suffix) "
        "bs=4096 2>/dev/null | base64'"
    )
    try:
        res = adb.shell(script, timeout=60)
    except Exception:
        return None
    if res.exit_code != 0 or not res.stdout.strip():
        return None

    try:
        raw = base64.b64decode("".join(res.stdout.split()))
    except Exception:
        return None

    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        return parse_xbl_config(tmp_path)
    except ArbError:
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def read_device_arb_fastboot(fb) -> ArbInfo | None:
    """通过 fastboot fetch 回读设备 xbl_config 并解析（不需要 root）。

    需要设备处于 fastboot 模式，且 fastboot 版本支持 fetch（34+）。
    """
    import tempfile

    for part in ("xbl_config", "xbl_config_a", "xbl_config_b"):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "xbl_config.img"
            try:
                res = fb.run("fetch", part, str(out), timeout=120)
            except Exception:
                continue
            if not out.exists() or out.stat().st_size == 0:
                continue
            try:
                info = parse_xbl_config(out)
                info.source = f"fastboot:{part}"
                return info
            except ArbError:
                continue
    return None


# --------------------------------------------------------------------------
# 比较
# --------------------------------------------------------------------------

def compare_arb(package: ArbInfo | None, device: ArbInfo | None) -> ArbComparison:
    """比较包与设备的 ARB，给出结论。"""
    if package is None and device is None:
        return ArbComparison(
            None, None, ArbVerdict.UNKNOWN, "双方都未能读取 ARB，无法判断"
        )
    if package is None:
        return ArbComparison(
            None, device, ArbVerdict.UNKNOWN, "未能从包中读取 ARB"
        )
    if device is None:
        return ArbComparison(
            package,
            None,
            ArbVerdict.UNKNOWN,
            "未能读取设备 ARB。降级前请手工确认，否则有硬砖风险",
        )

    if package.arb_index < device.arb_index:
        return ArbComparison(
            package,
            device,
            ArbVerdict.BRICK,
            f"包 ARB={package.arb_index} 低于设备已熔断值 {device.arb_index}，"
            f"刷入会导致硬件级变砖（不可恢复）",
        )
    if package.arb_index == device.arb_index:
        return ArbComparison(
            package, device, ArbVerdict.SAFE, f"ARB 相同（{package.arb_index}），降级安全"
        )
    return ArbComparison(
        package,
        device,
        ArbVerdict.UPGRADE,
        f"包 ARB={package.arb_index} 高于设备 {device.arb_index}，"
        f"刷入将熔断到更高值，此后无法降级到更低版本",
    )
