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


def test_core_layer_does_not_block_itself():
    """核心判定层只给风险等级，阻断决策交给调用方（menu）。"""
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


# ---------------------------------------------------------------------------
# 机型识别必须用「设备侧」标识，不能拿包里的型号张冠李戴
# ---------------------------------------------------------------------------


class _Snap:
    def __init__(self, model, codename, build):
        self.present = True
        self.model = model
        self.codename = codename
        self.product_name = model
        self.build_id = build


class _Mon:
    def __init__(self, snap):
        self.snapshot = snap


class _Info:
    def __init__(self, version):
        self.version_name = version
        self.product_name = "OPD2404"
        self.pre_device = "OP5D77L1"


def _warn(info, snap, capsys):
    import lrom.tui.menu as M

    ret = M._warn_edl_risk(info, _Mon(snap))
    out = capsys.readouterr().out
    return ret, out


def test_unlisted_model_gets_no_warning_at_all(capsys):
    """一加 15（PLK110）不在 9008 库里 → 即使包里是平板的包也不能弹提示。

    这是真实反馈的 bug：换一加 15 上来仍弹「降级不能恢复 9008」。
    根因是拿包里的 product_name 去匹配，而没用设备自身型号。
    """
    ret, out = _warn(_Info("OPD2404_16.0.5.1201(CN01)"), _Snap("PLK110", "PLK110", "PLK110_16.0.0.100(CN01)"), capsys)
    assert ret is False
    assert "9008" not in out
    assert "降级" not in out


def test_known_model_upgrade_is_blocked(capsys):
    """已收录机型从阈值前升上来 → 硬阻断。"""
    ret, out = _warn(_Info("OPD2404_16.0.5.1201(CN01)"), _Snap("OPD2404", "OP5D77L1", "OPD2404_14.1.0.226(CN01)"), capsys)
    assert ret is True
    assert "已拦截" in out
    assert "OPD2404" in out


def test_past_threshold_same_version_is_allowed(capsys):
    """设备已越过阈值且同版本重刷 → 放行（熔断已成事实，刷它不会更糟）。"""
    ret, out = _warn(_Info("OPD2404_16.0.5.1201(CN01)"), _Snap("OPD2404", "OP5D77L1", "OPD2404_16.0.5.1201(CN01)"), capsys)
    assert ret is False
    assert "已拦截" not in out
    assert "9008" in out


def test_past_threshold_upgrade_is_allowed(capsys):
    """设备已越过阈值后继续升级 → 放行，否则会被锁死在当前版本。"""
    ret, out = _warn(_Info("OPD2404_16.0.10.600(CN01)"), _Snap("OPD2404", "OP5D77L1", "OPD2404_16.0.5.1201(CN01)"), capsys)
    assert ret is False
    assert "已拦截" not in out


def test_downgrade_shows_model_and_does_not_block(capsys):
    """降级只提醒不阻断，但必须说明是哪个机型。"""
    ret, out = _warn(_Info("OPD2404_14.1.0.226(CN01)"), _Snap("OPD2404", "OP5D77L1", "OPD2404_16.0.5.1201(CN01)"), capsys)
    assert ret is False
    assert "降级" in out
    assert "机型" in out
    assert "OPD2404" in out


def test_no_device_means_no_check(capsys):
    """没连设备时不做判定，避免误报。"""
    class _NoDev:
        present = False

    import lrom.tui.menu as M

    ret = M._warn_edl_risk(_Info("OPD2404_16.0.5.1201(CN01)"), _Mon(_NoDev()))
    assert ret is False
    assert capsys.readouterr().out == ""


def test_device_model_keys_excludes_package_fields():
    """匹配键必须只来自设备侧。"""
    import lrom.tui.menu as M

    keys = M._device_model_keys(_Snap("PLK110", "PLK110", "X"))
    assert "OPD2404" not in keys
    assert "PLK110" in keys


def test_only_crossing_threshold_blocks(capsys):
    """阻断条件收敛：只有「从阈值前升到阈值后」才拦。

    其余三种情况（同版本重刷、越过阈值后升级、降级）都放行，
    避免用户被锁死在当前版本。
    """
    snap_old = _Snap("OPD2404", "OP5D77L1", "OPD2404_14.1.0.226(CN01)")
    snap_new = _Snap("OPD2404", "OP5D77L1", "OPD2404_16.0.5.1201(CN01)")

    # 跨阈值 → 拦
    ret, _ = _warn(_Info("OPD2404_16.0.5.1201(CN01)"), snap_old, capsys)
    assert ret is True

    # 同版本重刷 → 放
    ret, _ = _warn(_Info("OPD2404_16.0.5.1201(CN01)"), snap_new, capsys)
    assert ret is False

    # 越过阈值后升级 → 放
    ret, _ = _warn(_Info("OPD2404_16.0.10.600(CN01)"), snap_new, capsys)
    assert ret is False

    # 降级 → 放（降级已无 9008 可失）
    ret, _ = _warn(_Info("OPD2404_14.1.0.226(CN01)"), snap_new, capsys)
    assert ret is False
