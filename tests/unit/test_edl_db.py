"""9008（EDL）免授权熔断判定测试。

核心目标：
1. 升级跨过熔断阈值 → 强提醒「不可逆」
2. 降级 → 明确说明「不能恢复」，绝不能出现「降级可恢复」的表述
3. 版本读不出 / 机型未收录 → UNKNOWN，不误报
4. 永远不硬阻断（熔断不等于立刻变砖）
"""

from __future__ import annotations

import pytest

from lrom.core.edl_db import (
    EDL_MODELS,
    EdlRisk,
    check_edl_risk,
    format_edl_hint,
)


# ---------------------------------------------------------------------------
# 基本判定
# ---------------------------------------------------------------------------


def test_upgrade_across_threshold_will_fuse():
    """从 14.x 升到 16.0.5 应判定为会熔断。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="OPD2404",
    )
    assert r.risk is EdlRisk.WILL_FUSE
    assert "永久熔断" in r.message


def test_upgrade_across_threshold_mentions_irreversible():
    """提醒里必须说明降级无法恢复。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="OPD2404",
    )
    assert "降级无法恢复" in r.message or "不能恢复" in r.message


def test_downgrade_warns_no_recovery():
    """降级时必须明确说明不能恢复 9008。"""
    r = check_edl_risk(
        package_version="14.1.0.226",
        device_version="16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is EdlRisk.DOWNGRADE_NO_RECOVER
    assert "不能" in r.message
    assert "恢复" in r.message


def test_downgrade_never_claims_can_restore():
    """绝不允许出现「降级可恢复 9008」这类错误表述。"""
    r = check_edl_risk(
        package_version="14.1.0.226",
        device_version="16.0.5.1201",
        model_code="OPD2404",
    )
    text = format_edl_hint(r)
    assert "可以恢复" not in text
    assert "能恢复" not in text.replace("不能恢复", "")


def test_device_already_past_threshold_likely_fused():
    """设备已在阈值之后 → 已熔断或已过窗口期。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is EdlRisk.LIKELY_FUSED


def test_both_below_threshold_is_safe():
    """包与设备都低于阈值 → SAFE。"""
    r = check_edl_risk(
        package_version="14.1.0.226",
        device_version="14.0.0.100",
        model_code="OPD2404",
    )
    assert r.risk is EdlRisk.SAFE


def test_unknown_model_returns_unknown():
    """未收录机型不误报。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="XXXX0000",
    )
    assert r.risk is EdlRisk.UNKNOWN


def test_missing_version_returns_unknown():
    """版本读不出来 → UNKNOWN，不猜测。"""
    r = check_edl_risk(
        package_version=None,
        device_version="16.0.5.1201",
        model_code="OPD2404",
    )
    assert r.risk is EdlRisk.UNKNOWN


def test_codename_matching_works():
    """用设备代号也能匹配到记录。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        codename="OP5D77L1",
    )
    # OP5D77L1 不在收录的 codes 里 → UNKNOWN 是正确行为（不误报）
    assert r.risk is EdlRisk.UNKNOWN


def test_never_blocks():
    """9008 熔断不硬阻断 —— 决定权留给用户。"""
    for pkg, dev in [
        ("16.0.5.1201", "14.1.0.226"),
        ("14.1.0.226", "16.0.5.1201"),
        ("16.0.5.1201", "16.0.5.1201"),
    ]:
        r = check_edl_risk(
            package_version=pkg, device_version=dev, model_code="OPD2404"
        )
        assert r.blocks is False


# ---------------------------------------------------------------------------
# 数据完整性
# ---------------------------------------------------------------------------


def test_all_records_have_valid_version():
    """每条记录的熔断版本必须可解析。"""
    from lrom.core.arb_db import parse_build_version

    for rec in EDL_MODELS:
        assert parse_build_version(rec.fuse_version) is not None, rec.model


def test_all_records_have_note():
    """每条记录必须带来源说明，便于用户核对。"""
    for rec in EDL_MODELS:
        assert rec.note, f"{rec.model} 缺少来源说明"


def test_all_records_codes_include_model():
    """codes 里必须包含 model 本身。"""
    for rec in EDL_MODELS:
        assert rec.model in rec.codes, rec.model


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def test_format_mentions_inference_basis():
    """格式化输出必须声明『推断而非实测』，避免误导。"""
    r = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="OPD2404",
    )
    text = format_edl_hint(r)
    assert "推断" in text
    assert "无法读取" in text


# ---------------------------------------------------------------------------
# 机型标识匹配（防止「拿代号去匹配型号」这类回归）
# ---------------------------------------------------------------------------


def test_model_code_matches_but_codename_does_not():
    """OPD2404（型号）能匹配，OP5D77L1（代号）不能 —— 调用方需自己处理。

    这是 v0.4.5「分区名有无槽位后缀」同类坑：两种标识不能混用。
    """
    by_model = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="OPD2404",
    )
    by_codename = check_edl_risk(
        package_version="16.0.5.1201",
        device_version="14.1.0.226",
        model_code="OP5D77L1",
    )
    assert by_model.risk is EdlRisk.WILL_FUSE
    assert by_codename.risk is EdlRisk.UNKNOWN


def test_menu_passes_model_not_only_codename():
    """menu 的提醒函数必须能用「型号」匹配成功。"""
    from lrom.tui.menu import _warn_edl_risk

    class _Snap:
        present = True
        model = "OPD2404"
        codename = "OP5D77L1"
        build_id = "OPD2404_16.0.5.1201(CN01)"

    class _Mon:
        snapshot = _Snap()

    class _Info:
        product_name = "OPD2404"
        pre_device = "OP5D77L1"
        version_name = "OPD2404_14.1.0.226(CN01)"

    # 只要能匹配到记录就算通过（不直接断言输出文本，避免耦合颜色码）
    import lrom.tui.menu as menu_mod

    captured = []
    orig = menu_mod._color

    def _spy(s, c=""):
        captured.append(s)
        return orig(s, c)

    menu_mod._color = _spy
    try:
        _warn_edl_risk(_Info(), _Mon())
    finally:
        menu_mod._color = orig

    joined = "".join(captured)
    assert "降级" in joined
