"""ROM 包识别与线刷可行性校验。

核心职责：给定一个 ROM 包，回答三个问题：
  1. 这是什么包？（型号、版本、格式）
  2. 能否用于线刷？（结构自洽性校验）
  3. 能否刷到这台设备上？（型号/分区匹配）

设计原则：宁可拒绝，不可误判。所有"不确定"一律按不可刷处理。
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .payload import (
    SELF_CONTAINED_OPS,
    SOURCE_DEPENDENT_OPS,
    OpType,
    PayloadFormatError,
    PayloadManifest,
    payload_data_offset_in_zip,
    read_payload_header,
)


class PackageKind(Enum):
    OTA_FULL = "全量 OTA 包"
    OTA_DELTA = "增量 OTA 包"
    FASTBOOT_SPARSE = "fastboot 散包"
    UNKNOWN = "未知格式"


class Verdict(Enum):
    FLASHABLE = "可以线刷"
    NEEDS_SOURCE = "不能线刷（依赖设备源数据）"
    MODEL_MISMATCH = "不能刷（型号不匹配）"
    MALFORMED = "不能刷（包结构异常）"
    UNKNOWN = "无法判断"


class Severity(Enum):
    INFO = "信息"
    WARN = "警告"
    DANGER = "危险"
    BLOCKER = "阻止"


@dataclass
class Finding:
    severity: Severity
    message: str


@dataclass
class PackageInfo:
    path: Path
    kind: PackageKind = PackageKind.UNKNOWN
    verdict: Verdict = Verdict.UNKNOWN

    # 元数据
    product_name: str | None = None
    pre_device: str | None = None
    display_os_version: str | None = None
    version_name: str | None = None
    ota_type: str | None = None
    android_version: str | None = None
    security_patch: str | None = None
    ota_id: str | None = None
    #: post-build 完整指纹，如 "OnePlus/OPD2404/OP5D77L1:16/.../U.247...:user/release-keys"
    post_build: str | None = None
    #: post-build-incremental，如 "U.247d78a_a3a1b_629cb"
    build_incremental: str | None = None

    # payload
    manifest: PayloadManifest | None = None
    payload_offset: int = 0
    payload_size: int = 0

    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: Severity, message: str) -> None:
        self.findings.append(Finding(severity, message))

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.BLOCKER for f in self.findings)

    @property
    def partitions(self) -> list[str]:
        return [p.name for p in self.manifest.partitions] if self.manifest else []


# --------------------------------------------------------------------------
# 元数据解析
# --------------------------------------------------------------------------

_META_KEYS = {
    "product_name": "product_name",
    "pre-device": "pre_device",
    "display_os_version": "display_os_version",
    "version_name": "version_name",
    "ota-type": "ota_type",
    "android_version": "android_version",
    "security_patch": "security_patch",
    "ota-id": "ota-id",
}


def parse_ota_metadata(text: str) -> dict[str, str]:
    """解析 META-INF/com/android/metadata 的 key=value 格式。"""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


# --------------------------------------------------------------------------
# 结构自洽性校验 —— 这是防止误判的关键
# --------------------------------------------------------------------------

def validate_manifest_structure(manifest: PayloadManifest) -> list[Finding]:
    """校验每个 InstallOperation 的结构是否自洽。

    规则：
      - 自带数据的操作 (REPLACE/REPLACE_BZ/REPLACE_XZ/ZSTD) 必须有 data_length > 0
      - ZERO 操作不应有数据
      - 依赖源数据的操作必须声明 src_extents
      - 每个操作必须声明 dst_extents
    任何一条不满足 => 包结构异常，拒绝刷写。
    """
    findings: list[Finding] = []

    for part in manifest.partitions:
        if not part.operations:
            findings.append(
                Finding(Severity.BLOCKER, f"分区 {part.name} 没有任何操作")
            )
            continue

        for idx, op in enumerate(part.operations):
            where = f"{part.name}[{idx}]"

            if not op.dst_extents:
                findings.append(
                    Finding(Severity.BLOCKER, f"{where} ({op.op_type.label}) 缺少 dst_extents")
                )

            if op.op_type in SELF_CONTAINED_OPS:
                if op.op_type is not OpType.ZERO and op.data_length <= 0:
                    findings.append(
                        Finding(
                            Severity.BLOCKER,
                            f"{where} ({op.op_type.label}) 应自带数据但 data_length=0",
                        )
                    )

            if op.op_type in SOURCE_DEPENDENT_OPS:
                if not op.src_extents:
                    findings.append(
                        Finding(
                            Severity.BLOCKER,
                            f"{where} ({op.op_type.label}) 依赖源数据但缺少 src_extents",
                        )
                    )

    return findings


def classify_payload(manifest: PayloadManifest) -> tuple[PackageKind, Verdict, list[Finding]]:
    """根据操作类型分布判断是全量还是增量。"""
    findings: list[Finding] = []
    source_parts = manifest.source_dependent_partitions
    total = len(manifest.partitions)

    if not manifest.partitions:
        return (
            PackageKind.UNKNOWN,
            Verdict.MALFORMED,
            [Finding(Severity.BLOCKER, "manifest 中没有任何分区")],
        )

    if not source_parts:
        findings.append(
            Finding(
                Severity.INFO,
                f"全部 {total} 个分区都自带完整数据（无 SOURCE_COPY/BSDIFF），是全量包",
            )
        )
        return PackageKind.OTA_FULL, Verdict.FLASHABLE, findings

    # 存在依赖源数据的分区
    ratio = len(source_parts) / total
    findings.append(
        Finding(
            Severity.BLOCKER,
            f"{len(source_parts)}/{total} 个分区依赖设备源数据："
            f"{', '.join(source_parts[:8])}"
            f"{' ...' if len(source_parts) > 8 else ''}",
        )
    )
    findings.append(
        Finding(
            Severity.INFO,
            "这是增量 OTA 包，只能用于设备原地升级（adb sideload），"
            "不能解包成独立镜像后线刷",
        )
    )
    return PackageKind.OTA_DELTA, Verdict.NEEDS_SOURCE, findings


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------

def inspect_package(path: str | Path) -> PackageInfo:
    """识别并校验一个 ROM 包。"""
    path = Path(path).expanduser()
    info = PackageInfo(path=path)

    if not path.exists():
        info.add(Severity.BLOCKER, f"文件不存在: {path}")
        info.verdict = Verdict.MALFORMED
        return info

    if path.is_dir():
        _inspect_directory(info)
        return info

    if not zipfile.is_zipfile(path):
        info.add(Severity.BLOCKER, "不是 ZIP 格式的 ROM 包")
        info.verdict = Verdict.UNKNOWN
        return info

    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())

            if "META-INF/com/android/metadata" in names:
                text = zf.read("META-INF/com/android/metadata").decode(
                    "utf-8", errors="replace"
                )
                meta = parse_ota_metadata(text)
                info.product_name = meta.get("product_name")
                info.pre_device = meta.get("pre-device")
                info.display_os_version = meta.get("display_os_version")
                info.version_name = meta.get("version_name")
                info.ota_type = meta.get("ota-type")
                info.android_version = meta.get("android_version")
                info.security_patch = meta.get("security_patch")
                info.ota_id = meta.get("ota-id")
                info.post_build = meta.get("post-build")
                info.build_incremental = meta.get("post-build-incremental")

            if "payload.bin" not in names:
                info.add(
                    Severity.DANGER,
                    "包内没有 payload.bin，可能是 fastboot 散包或特殊格式",
                )
                info.kind = PackageKind.UNKNOWN
                info.verdict = Verdict.UNKNOWN
                return info

            _inspect_payload(info, path)

    except zipfile.BadZipFile as exc:
        info.add(Severity.BLOCKER, f"ZIP 文件损坏: {exc}")
        info.verdict = Verdict.MALFORMED

    return info


def _inspect_payload(info: PackageInfo, path: Path) -> None:
    try:
        data_offset, payload_size = payload_data_offset_in_zip(path)
    except PayloadFormatError as exc:
        info.add(Severity.BLOCKER, str(exc))
        info.verdict = Verdict.MALFORMED
        return

    info.payload_offset = data_offset
    info.payload_size = payload_size

    try:
        with open(path, "rb") as f:
            f.seek(data_offset)
            manifest, _, sig_size = read_payload_header(f)
    except (PayloadFormatError, ValueError) as exc:
        info.add(Severity.BLOCKER, f"payload 解析失败: {exc}")
        info.verdict = Verdict.MALFORMED
        return

    info.manifest = manifest

    # 1) 结构自洽性
    structure_findings = validate_manifest_structure(manifest)
    info.findings.extend(structure_findings)
    if any(f.severity is Severity.BLOCKER for f in structure_findings):
        info.kind = PackageKind.UNKNOWN
        info.verdict = Verdict.MALFORMED
        return

    # 2) 全量 / 增量判定
    kind, verdict, findings = classify_payload(manifest)
    info.kind = kind
    info.verdict = verdict
    info.findings.extend(findings)

    total_mb = sum(p.size_bytes(manifest.block_size) for p in manifest.partitions) / 1e6
    info.add(
        Severity.INFO,
        f"共 {len(manifest.partitions)} 个分区，声明总大小 {total_mb:,.0f} MB",
    )


def _inspect_directory(info: PackageInfo) -> None:
    """识别 fastboot 散包目录。"""
    imgs = sorted(p.name for p in info.path.glob("*.img"))
    if imgs:
        info.kind = PackageKind.FASTBOOT_SPARSE
        info.verdict = Verdict.FLASHABLE
        info.add(Severity.INFO, f"检测到 {len(imgs)} 个 .img 镜像（fastboot 散包）")
    else:
        info.verdict = Verdict.UNKNOWN
        info.add(Severity.WARN, "目录中没有找到 .img 镜像")


def check_device_match(
    info: PackageInfo, device_codename: str, device_model: str | None = None
) -> list[Finding]:
    """校验包与设备是否匹配。

    只做保守判断：任何明确不一致 => BLOCKER。
    """
    findings: list[Finding] = []

    if info.pre_device:
        if info.pre_device != device_codename:
            findings.append(
                Finding(
                    Severity.BLOCKER,
                    f"型号不匹配：包要求 pre-device={info.pre_device}，"
                    f"设备代号={device_codename}",
                )
            )
        else:
            findings.append(
                Finding(Severity.INFO, f"设备代号匹配: {device_codename}")
            )

    if device_model and info.product_name:
        if info.product_name != device_model:
            findings.append(
                Finding(
                    Severity.BLOCKER,
                    f"产品名不匹配：包是 {info.product_name}，设备是 {device_model}",
                )
            )
        else:
            findings.append(Finding(Severity.INFO, f"产品名匹配: {device_model}"))

    return findings
