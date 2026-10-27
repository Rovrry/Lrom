"""ARB 熔断机型数据库测试。

核心目标：确保「降级到熔断前版本」被硬拦，而「正常升级/重刷」不被误伤。

数据来源：大侠阿木固件站 + Bartixxx32/Oneplus-antirollchecker 实测表。
"""

from __future__ import annotations

import pytest

from lrom.core.arb_db import (
    ARB_FUSED_MODELS,
    ARB_SAFE_MODELS,
    ArbRisk,
    Confidence,
    check_arb_risk,
    compare_build_versions,
    parse_build_version,
)


# ---------------------------------------------------------------------------
# 版本解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("OPD2404_16.0.5.1201(CN01)", (16, 0, 5)),
        ("OPD2404_14.1.0.226(CN01)", (14, 1, 0)),
        ("16.0.3.501", (16, 0, 3)),
        ("PJZ110_16.0.3.501(CN01) C.84", (16, 0, 3)),
        ("UKQ1.231108.001/U.247d78a_a3a1b_629cb", None),
    ],
)
def test_parse_build_version(text: str, expected):
    assert parse_build_version(text) == expected


def test_android_build_number_not_confused_with_version():
    """Android 构建号 UKQ1.231108.001 不能被当成 1.231108。

    这是个真实踩坑：早期正则把 UKQ1.231108 里的 "1.231108"
    误识别成业务版本号，会导致熔断判定完全错乱。
    """
    assert parse_build_version("UKQ1.231108.001/U.247d78a") is None


@pytest.mark.parametrize("text", ["", "garbage", "no-version-here", None])
def test_parse_build_version_invalid(text):
    assert parse_build_version(text) is None


def test_compare_build_versions():
    assert compare_build_versions("16.0.5.1201", "16.0.3.501") == 1
    assert compare_build_versions("16.0.3.501", "16.0.5.1201") == -1
    assert compare_build_versions("16.0.3.501", "16.0.3.501") == 0


def test_compare_build_versions_unparseable():
    assert compare_build_versions("garbage", "16.0.3.501") is None


# ---------------------------------------------------------------------------
# 数据库本身
# ---------------------------------------------------------------------------


def test_database_not_empty():
    assert len(ARB_FUSED_MODELS) >= 8


def test_all_confirmed_records_have_codes():
    """CONFIRMED 的记录必须有型号代码，否则永远匹配不上。"""
    for rec in ARB_FUSED_MODELS:
        if rec.confidence is Confidence.CONFIRMED:
            assert rec.codes, f"{rec.model} 缺少型号代码"


def test_all_records_have_parseable_trigger():
    for rec in ARB_FUSED_MODELS:
        assert parse_build_version(rec.trigger_version) is not None, rec.model


def test_oneplus_15_in_safe_list():
    """一加 15 媒体常误报熔断，实测全版本 ARB=0，必须在安全表里。"""
    assert "PLK110" in ARB_SAFE_MODELS
    assert "CPH2747" in ARB_SAFE_MODELS


def test_ace5pro_in_safe_list():
    """Ace 5 Pro 媒体说熔断，实测 ARB=0。"""
    assert "PKR110" in ARB_SAFE_MODELS


def test_pad_pro_in_fused_list():
    """一加平板 Pro（本项目测试机）必须在熔断表里。"""
    codes = {c for rec in ARB_FUSED_MODELS for c in rec.codes}
    assert "OPD2404" in codes


# ---------------------------------------------------------------------------
# ★ 核心：真实场景
# ---------------------------------------------------------------------------


def test_factory_rom_blocks_on_pad_pro():
    """★ 实测场景：出厂包 14.1.0.226 刷到已熔断的 OPD2404。

    这是本项目的真实案例 —— 用户拿到的出厂包 ARB=0，
    而设备 ARB=1，刷入直接黑砖。必须硬拦。
    """
    r = check_arb_risk(
        package_version="OPD2404_14.1.0.226(CN01)",
        device_version="OPD2404_16.0.5.1201(CN01)",
        model_code="OPD2404",
    )
    assert r.risk is ArbRisk.BLOCK
    assert r.blocks is True
    assert "永久黑砖" in r.message


def test_same_version_passes_on_pad_pro():
    """同版本的官方包必须放行，不能误伤。"""
    r = check_arb_risk(
        package_version="OPD2404_16.0.5.1201(CN01)",
        device_version="OPD2404_16.0.5.1201(CN01)",
        model_code="OPD2404",
    )
    assert r.risk is ArbRisk.SAFE


def test_newer_package_passes():
    """升级包（版本更高）必须放行。"""
    r = check_arb_risk(
        package_version="OPD2404_16.0.10.600",
        device_version="OPD2404_16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is ArbRisk.SAFE


def test_downgrade_within_fused_range_blocks():
    """包版本 >= 触发版本，但低于设备当前版本 —— 仍是降级，要拦。"""
    r = check_arb_risk(
        package_version="OPD2404_16.0.3.501",  # 已熔断但更旧
        device_version="OPD2404_16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is ArbRisk.BLOCK
    assert "降级" in r.message


def test_safe_model_not_blocked():
    """一加 15 即使在「熔断版本号」上也不能被拦。"""
    r = check_arb_risk(
        package_version="16.0.3.503",
        device_version="16.0.3.503",
        model_code="PLK110",
    )
    assert r.risk is ArbRisk.SAFE
    assert "未熔断" in r.message


def test_unknown_model_not_blocked():
    """未收录的机型不阻断，只提示去确认。"""
    r = check_arb_risk(
        package_version="16.0.3.501",
        device_version="16.0.3.501",
        model_code="XXXX9999",
    )
    assert r.risk is ArbRisk.UNKNOWN
    assert "oparb.pages.dev" in r.message


def test_missing_package_version_warns_not_blocks():
    """版本号读不出来时保守警告，不能硬拦（可能误伤）。"""
    r = check_arb_risk(
        package_version="garbage",
        device_version="OPD2404_16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is ArbRisk.WARN


def test_codename_matching_works():
    """没有型号代码时，代号也能匹配到记录。"""
    r = check_arb_risk(
        package_version="OPD2404_14.1.0.226",
        device_version="OPD2404_16.0.5.1201",
        codename="OPD2404",
    )
    assert r.risk is ArbRisk.BLOCK


def test_reported_confidence_only_warns():
    """单一来源的数据只警告不阻断，避免误伤。"""
    r = check_arb_risk(
        package_version="15.0.0.100",
        device_version="16.0.0.600",
        model_code="Find X7 Ultra",
    )
    assert r.risk is ArbRisk.WARN
    assert r.record is not None
    assert r.record.confidence is Confidence.REPORTED


def test_result_carries_details():
    """结果里要带机型、包版本、设备版本，方便用户核对。"""
    r = check_arb_risk(
        package_version="OPD2404_14.1.0.226",
        device_version="OPD2404_16.0.5.1201",
        model_code="OPD2404",
    )
    text = " ".join(r.details)
    assert "OPD2404" in text
    assert "16.0.3.501" in text


@pytest.mark.parametrize(
    "model,pkg,dev,expect",
    [
        # 一加 13 系列
        ("PJZ110", "PJZ110_16.0.3.501(CN01)", "PJZ110_16.0.5.1201", ArbRisk.BLOCK),
        ("PJZ110", "PJZ110_16.0.10.600", "PJZ110_16.0.5.1201", ArbRisk.SAFE),
        ("CPH2649", "CPH2649_16.0.1.100", "CPH2649_16.0.5.1201", ArbRisk.BLOCK),
        # 一加 12
        ("PJD110", "PJD110_15.0.9.999", "PJD110_16.0.5.1201", ArbRisk.BLOCK),
        # Ace 5 / 13R
        ("PKG110", "PKG110_16.0.3.500(CN01)", "PKG110_16.0.10.500", ArbRisk.BLOCK),
        # Ace 6
        ("PLQ110", "PLQ110_16.0.1.000", "PLQ110_16.0.5.700", ArbRisk.BLOCK),
        # 平板
        ("OPD2413", "OPD2413_15.0.0.100", "OPD2413_16.0.10.600", ArbRisk.BLOCK),
        ("OPD2415", "OPD2415_15.0.0.100", "OPD2415_16.0.10.600", ArbRisk.BLOCK),
    ],
)
def test_model_matrix(model: str, pkg: str, dev: str, expect: ArbRisk):
    r = check_arb_risk(
        package_version=pkg, device_version=dev, model_code=model
    )
    assert r.risk is expect, f"{model}: {r.message}"
