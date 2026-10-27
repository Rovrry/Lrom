"""BL 回锁的安全校验测试。

回锁是最危险的功能：两槽系统不一致就回锁 = 变砖。
本模块的核心不是"执行 lock"，而是"证明可以 lock"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lrom.core.relock import (
    RelockStage,
    build_relock_plan,
    verify_two_pass,
)

PARTS = {"boot", "init_boot", "system", "vendor", "xbl", "abl", "tz"}


def test_ready_when_both_passes_succeed_and_match():
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=True,
        pass_a_partitions=PARTS,
        pass_b_partitions=PARTS,
        pass_a_version="16.0.5.1201",
        pass_b_version="16.0.5.1201",
    )
    assert r.stage is RelockStage.READY
    assert r.can_relock is True
    assert r.reasons == []


def test_blocked_when_first_pass_failed():
    r = verify_two_pass(
        pass_a_ok=False,
        pass_b_ok=True,
        pass_a_partitions=PARTS,
        pass_b_partitions=PARTS,
    )
    assert r.stage is RelockStage.BLOCKED
    assert r.can_relock is False
    assert any("第一次平刷" in x for x in r.reasons)


def test_blocked_when_second_pass_failed():
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=False,
        pass_a_partitions=PARTS,
        pass_b_partitions=PARTS,
    )
    assert r.stage is RelockStage.BLOCKED
    assert any("第二次平刷" in x for x in r.reasons)


def test_blocked_when_partition_sets_differ():
    """两槽刷出的分区集合不同 → 拒绝（这是最关键的防砖检查）。"""
    a = {"boot", "init_boot", "system"}
    b = {"boot", "init_boot", "system", "vendor"}  # B 多一个
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=True,
        pass_a_partitions=a,
        pass_b_partitions=b,
    )
    assert r.stage is RelockStage.BLOCKED
    assert any("不一致" in x for x in r.reasons)


def test_blocked_when_version_mismatch():
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=True,
        pass_a_partitions=PARTS,
        pass_b_partitions=PARTS,
        pass_a_version="16.0.5.1201",
        pass_b_version="16.0.5.702",  # 不同版本
    )
    assert r.stage is RelockStage.BLOCKED
    assert any("版本不一致" in x for x in r.reasons)


def test_blocked_when_failed_partitions_present():
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=True,
        pass_a_partitions=PARTS,
        pass_b_partitions=PARTS,
        pass_a_failed=["tz"],
    )
    assert r.stage is RelockStage.BLOCKED
    assert any("tz" in x for x in r.reasons)


def test_blocked_when_no_partition_info():
    """没有分区清单就无法证明一致 → 拒绝，不能默认放行。"""
    r = verify_two_pass(
        pass_a_ok=True,
        pass_b_ok=True,
        pass_a_partitions=None,
        pass_b_partitions=None,
    )
    assert r.stage is RelockStage.BLOCKED
    assert any("无法确认两槽一致" in x for x in r.reasons)


def test_multiple_reasons_all_reported():
    r = verify_two_pass(
        pass_a_ok=False,
        pass_b_ok=False,
        pass_a_partitions={"a"},
        pass_b_partitions={"b"},
        pass_a_version="v1",
        pass_b_version="v2",
    )
    assert r.stage is RelockStage.BLOCKED
    assert len(r.reasons) >= 3


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


def test_plan_contains_two_pass_steps():
    p = build_relock_plan(device_model="OPD2404", current_slot="a")
    text = p.describe()
    assert "第一次平刷" in text
    assert "第二次平刷" in text
    assert "flashing lock" in text
    assert "清空全部用户数据" in text


def test_plan_wipes_data_flag():
    p = build_relock_plan()
    assert p.wipes_data is True


# ---------------------------------------------------------------------------
# cmd_relock 的先决条件检查
# ---------------------------------------------------------------------------




def _mock_pkg(monkeypatch):
    """让 inspect_package 返回一个可线刷的假包。"""
    import lrom.core.package as pkg_mod
    from types import SimpleNamespace

    fake = SimpleNamespace(
        verdict=pkg_mod.Verdict.FLASHABLE,
        manifest=SimpleNamespace(partitions=[SimpleNamespace(name="boot"), SimpleNamespace(name="xbl")]),
        version_name="16.0.5.1201",
    )
    monkeypatch.setattr(pkg_mod, "inspect_package", lambda p: fake)

def _ns(**kw):
    from types import SimpleNamespace

    base = dict(package="/tmp/x.zip", images=None, dry_run=True, yes=False, adb_port=None, slot=None, check_model=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_relock_refuses_when_not_fastboot(monkeypatch, capsys):
    """设备在 ADB 模式（非 fastboot）→ 拒绝，提示 reboot bootloader。"""
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.ADB, locked="0", model="OPD2404", codename="OP5D77L1", slot="a"))
    _mock_pkg(monkeypatch)
    rc = cli.cmd_relock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "不在 fastboot 模式" in out or "reboot bootloader" in out


def test_relock_refuses_when_already_locked(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked="1", model="OPD2404", codename="OP5D77L1", slot="a"))
    _mock_pkg(monkeypatch)
    rc = cli.cmd_relock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "已是锁定状态" in out


def test_relock_refuses_when_no_device(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: None)
    _mock_pkg(monkeypatch)
    rc = cli.cmd_relock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "未检测到设备" in out


def test_relock_refuses_when_lock_state_unknown(monkeypatch, capsys):
    import lrom.cli as cli
    import lrom.device.discover as disc

    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.FASTBOOT, locked=None, model="OPD2404", codename="OP5D77L1", slot="a"))
    _mock_pkg(monkeypatch)
    rc = cli.cmd_relock(_ns())
    out = capsys.readouterr().out
    assert rc == 1
    assert "无法确认" in out


# ---------------------------------------------------------------------------
# 包适配性：跨区/第三方包回锁必砖
# ---------------------------------------------------------------------------


def test_suitability_ok_same_model():
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, reasons = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model="OPD2404",
        device_codename="OP5D77L1",
    )
    assert suit is PackageSuitability.OK
    assert reasons == []


def test_suitability_wrong_model_blocked():
    """异机型/跨区包 → WRONG_MODEL，回锁必砖。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, reasons = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model="PLK110",  # 一加 15
        device_codename="PLK110",
    )
    assert suit is PackageSuitability.WRONG_MODEL
    assert any("机型不符" in r for r in reasons)


def test_suitability_delta_not_allowed():
    """增量包不能用于回锁。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, reasons = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=False,
        device_model="OPD2404",
        device_codename="OP5D77L1",
    )
    assert suit is PackageSuitability.NOT_OFFICIAL
    assert any("全量" in r for r in reasons)


def test_suitability_unknown_model_blocked():
    """读不到包机型 → UNKNOWN，拒绝（不能赌）。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, reasons = check_package_suitability(
        pkg_product_name=None,
        pkg_pre_device=None,
        pkg_is_full_ota=True,
        device_model="OPD2404",
        device_codename="OP5D77L1",
    )
    assert suit is PackageSuitability.UNKNOWN
    assert any("读不到" in r for r in reasons)


def test_suitability_matches_by_codename_only():
    """product_name 缺失但 pre_device 匹配，也算 OK。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, _ = check_package_suitability(
        pkg_product_name=None,
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model="OPD2404",
        device_codename="OP5D77L1",
    )
    assert suit is PackageSuitability.OK


# ---------------------------------------------------------------------------
# fastboot 平台名 ≠ 机型（pineapple 不能判成机型不符）
# ---------------------------------------------------------------------------


def test_platform_name_detected():
    from lrom.core.relock import is_platform_name

    assert is_platform_name("pineapple") is True
    assert is_platform_name("PINEAPPLE") is True
    assert is_platform_name("kalama") is True
    assert is_platform_name("OPD2404") is False
    assert is_platform_name("OP5D77L1") is False
    assert is_platform_name(None) is False


def test_fastboot_platform_name_is_unknown_not_wrong_model():
    """fastboot 下 codename=pineapple（平台名）→ 无法比对机型，判 UNKNOWN。

    绝不能判成 WRONG_MODEL —— 那会误拦所有高通机型。
    这是真机实测暴露的问题：OPD2404 在 fastboot 下 getvar product
    返回 pineapple，被旧逻辑误判为"机型不符"。
    """
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, reasons = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model=None,
        device_codename="pineapple",  # 平台名，不是机型
    )
    assert suit is PackageSuitability.UNKNOWN
    assert any("平台名" in r for r in reasons)


def test_adb_real_model_matches():
    """ADB 下读到真机型 → 正常匹配 OK。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, _ = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model="OPD2404",
        device_codename="OP5D77L1",
    )
    assert suit is PackageSuitability.OK


def test_adb_real_model_mismatch_still_blocked():
    """ADB 下真机型不符 → 仍然 WRONG_MODEL（平台名宽松不影响正常校验）。"""
    from lrom.core.relock import PackageSuitability, check_package_suitability

    suit, _ = check_package_suitability(
        pkg_product_name="OPD2404",
        pkg_pre_device="OP5D77L1",
        pkg_is_full_ota=True,
        device_model="PLK110",  # 一加 15
        device_codename="PLK110",
    )
    assert suit is PackageSuitability.WRONG_MODEL


def test_check_model_flag_adb_ok(monkeypatch, capsys):
    """--check-model 在 ADB 模式下核对机型，匹配则返回 0。"""
    import lrom.cli as cli
    import lrom.device.discover as disc
    import lrom.core.package as pkg

    fake = SimpleNamespace(
        verdict=pkg.Verdict.FLASHABLE,
        manifest=SimpleNamespace(partitions=[SimpleNamespace(name="boot")]),
        product_name="OPD2404", pre_device="OP5D77L1", version_name="16.0.5.1201",
        kind=pkg.PackageKind.OTA_FULL,
    )
    monkeypatch.setattr(pkg, "inspect_package", lambda p: fake)
    monkeypatch.setattr(disc, "detect", lambda adb_server_port=None: SimpleNamespace(
        mode=disc.DeviceMode.ADB, locked="0", model="OPD2404", codename="OP5D77L1", slot="a"))

    rc = cli.cmd_relock(_ns(dry_run=False, check_model=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "机型匹配" in out
