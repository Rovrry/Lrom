"""包识别与校验测试。"""

from __future__ import annotations

import pytest

from lrom.core.package import (
    PackageKind,
    Severity,
    Verdict,
    check_device_match,
    classify_payload,
    parse_ota_metadata,
    validate_manifest_structure,
)
from lrom.core.payload import OpType

from .test_payload import make_manifest, make_op, make_partition, parse_manifest


def manifest_of(*parts):
    return parse_manifest(make_manifest(list(parts)))


# --------------------------------------------------------------------------


def test_parse_metadata():
    text = """ota-type=AB
pre-device=OP5D77L1
product_name=OPD2404
display_os_version=16.0.5
version_name=OPD2404_16.0.5.1201(CN01)
"""
    meta = parse_ota_metadata(text)
    assert meta["pre-device"] == "OP5D77L1"
    assert meta["product_name"] == "OPD2404"
    assert meta["ota-type"] == "AB"


def test_classify_full_payload_is_flashable():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("system", [make_op(OpType.REPLACE, data_length=4096)]),
    )
    kind, verdict, findings = classify_payload(mf)
    assert kind is PackageKind.OTA_FULL
    assert verdict is Verdict.FLASHABLE
    assert all(f.severity is not Severity.BLOCKER for f in findings)


def test_classify_delta_payload_is_blocked():
    mf = manifest_of(
        make_partition(
            "system",
            [make_op(OpType.SOURCE_COPY, dst=[(0, 10)], src=[(50, 10)])],
        ),
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    kind, verdict, findings = classify_payload(mf)
    assert kind is PackageKind.OTA_DELTA
    assert verdict is Verdict.NEEDS_SOURCE
    assert any(f.severity is Severity.BLOCKER for f in findings)


def test_classify_empty_manifest_malformed():
    mf = parse_manifest(make_manifest([]))
    kind, verdict, findings = classify_payload(mf)
    assert verdict is Verdict.MALFORMED
    assert any(f.severity is Severity.BLOCKER for f in findings)


def test_structure_ok_for_self_contained():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE, data_length=4096)]),
    )
    assert validate_manifest_structure(mf) == []


def test_structure_flags_missing_data():
    """REPLACE 必须自带数据，data_length=0 应判为结构异常。"""
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE, data_length=0)]),
    )
    findings = validate_manifest_structure(mf)
    assert any(f.severity is Severity.BLOCKER for f in findings)
    assert "应自带数据" in findings[0].message


def test_structure_flags_missing_src_extents():
    """SOURCE_COPY 必须声明 src_extents。"""
    bad_op = make_op(OpType.SOURCE_COPY, dst=[(0, 5)])  # 无 src
    mf = manifest_of(make_partition("system", [bad_op]))
    findings = validate_manifest_structure(mf)
    assert any("src_extents" in f.message for f in findings)


def test_zero_op_allowed_without_data():
    mf = manifest_of(make_partition("boot", [make_op(OpType.ZERO, dst=[(0, 3)])]))
    assert validate_manifest_structure(mf) == []


def test_structure_flags_no_dst_extents():
    op = make_op(OpType.REPLACE, data_length=100, dst=[])
    mf = manifest_of(make_partition("boot", [op]))
    findings = validate_manifest_structure(mf)
    assert any("dst_extents" in f.message for f in findings)


def test_structure_flags_partition_without_ops():
    mf = manifest_of(make_partition("boot", []))
    findings = validate_manifest_structure(mf)
    assert any("没有任何操作" in f.message for f in findings)


# -- 设备匹配 -------------------------------------------------------------


class _FakeInfo:
    def __init__(self, pre_device, product_name):
        self.pre_device = pre_device
        self.product_name = product_name


def test_device_match_ok():
    info = _FakeInfo("OP5D77L1", "OPD2404")
    findings = check_device_match(info, "OP5D77L1", "OPD2404")
    assert all(f.severity is not Severity.BLOCKER for f in findings)


def test_device_codename_mismatch_blocks():
    """这是最关键的保护：型号不匹配必须硬阻止。

    真实案例：OPD2404 的设备刷入 OPD2407 的包会变砖。
    """
    info = _FakeInfo("OP615AL1", "OPD2407")
    findings = check_device_match(info, "OP5D77L1", "OPD2404")
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    assert len(blockers) == 2
    assert any("pre-device" in f.message for f in blockers)
    assert any("产品名" in f.message for f in blockers)


def test_device_mismatch_only_product():
    info = _FakeInfo("OP5D77L1", "OPD2407")
    findings = check_device_match(info, "OP5D77L1", "OPD2404")
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    assert len(blockers) == 1
    assert "产品名" in blockers[0].message
