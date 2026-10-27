"""刷写计划测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lrom.core.payload import OpType
from lrom.core.plan import (
    DANGER_PARTITIONS,
    NEVER_OVERWRITE,
    OPLUS_LOGICAL_PARTITIONS,
    Phase,
    Risk,
    build_plan,
)

from .test_payload import make_manifest, make_op, make_partition, parse_manifest


def manifest_of(*parts):
    return parse_manifest(make_manifest(list(parts)))


def test_logical_partitions_go_to_fastbootd():
    mf = manifest_of(
        make_partition("system", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("my_stock", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")

    by_name = {op.partition: op for op in plan.ops}
    assert by_name["system"].phase is Phase.FASTBOOTD
    assert by_name["my_stock"].phase is Phase.FASTBOOTD
    assert by_name["boot"].phase is Phase.FASTBOOT


def test_bootloader_partitions_are_danger_not_skipped():
    """bootloader 链分区是官方包的正常内容，应保留但标危险。"""
    mf = manifest_of(
        make_partition("xbl", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("abl", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")
    for op in plan.ops:
        assert op.risk is Risk.DANGER
        assert op.skip is False


def test_never_overwrite_partitions_skipped_by_default():
    mf = manifest_of(
        make_partition("persist", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("modemst1", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")
    assert all(op.skip for op in plan.ops)
    assert all(op.risk is Risk.BLOCKED for op in plan.ops)


def test_never_overwrite_can_be_forced():
    mf = manifest_of(
        make_partition("persist", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip", allow_dangerous=True)
    assert plan.ops[0].skip is False


def test_modem_is_last_in_fastboot():
    mf = manifest_of(
        make_partition("modem", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=400)]),
        make_partition("system", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")
    fb_ops = plan.by_phase(Phase.FASTBOOT)
    assert fb_ops[-1].partition == "modem"


def test_partition_not_on_device_is_skipped():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("weird_part", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(
        mf, package_path="x.zip", device_partitions={"boot_a", "boot_b", "super"}
    )
    by_name = {op.partition: op for op in plan.ops}
    assert by_name["boot"].skip is False
    assert by_name["weird_part"].skip is True


def test_logical_partition_not_in_by_name_still_planned():
    """逻辑分区本来就不在 /dev/block/by-name 里，不能被误跳过。"""
    mf = manifest_of(
        make_partition("system", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(
        mf, package_path="x.zip", device_partitions={"boot_a", "super"}
    )
    assert plan.ops[0].skip is False


def test_include_only_selected():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("system", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip", include=["boot"])
    assert len(plan.ops) == 1
    assert plan.ops[0].partition == "boot"


def test_total_bytes_counts_only_active():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("persist", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")
    # boot 4096 字节（1 block），persist 被跳过
    assert plan.total_bytes == 4096


def test_plan_serializes_to_json():
    mf = manifest_of(
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip")
    data = plan.to_json()
    assert data["tool"] == "lrom"
    assert data["summary"]["total_ops"] == 1
    assert data["ops"][0]["phase"] == "fastboot"
    assert data["ops"][0]["risk"] == "safe"


def test_critical_sets_do_not_overlap():
    """安全性断言：永不可覆盖与危险分区集合不应重叠。"""
    assert not (NEVER_OVERWRITE & OPLUS_LOGICAL_PARTITIONS)
    assert not (NEVER_OVERWRITE & DANGER_PARTITIONS)


# ---------------------------------------------------------------------------
# OCDT / 校准分区的警告等级（社区资料对照）
# ---------------------------------------------------------------------------


def test_engineering_cdt_is_warn_not_safe():
    """OCDT 是机独有校准分区，不能当普通分区静默刷。

    社区资料（一加线刷分区清单）标它为红线：用非本机的会进不了 Fastboot，
    损坏后售后也无法修复。备份清单里有它，但刷写时须明确警告。
    """
    from lrom.core.plan import WARN_PARTITIONS

    assert "engineering_cdt" in WARN_PARTITIONS
    assert "ocdt" in WARN_PARTITIONS


def test_oplusstanvbk_is_warn():
    """欧加 AVB 备份分区也须提醒。"""
    from lrom.core.plan import WARN_PARTITIONS

    assert "oplusstanvbk" in WARN_PARTITIONS


def test_preflight_flags_ocdt():
    """预演报告必须对 OCDT 单独给一条警告。"""
    from lrom.core.flash import preflight

    mf = manifest_of(
        make_partition("engineering_cdt", [make_op(OpType.REPLACE_XZ, data_length=100)]),
        make_partition("boot", [make_op(OpType.REPLACE_XZ, data_length=100)]),
    )
    plan = build_plan(mf, package_path="x.zip", device_model="OPD2404", slot="a")
    issues = preflight(plan, Path("/tmp"), super_size=None)
    msgs = " ".join(i.message for i in issues)
    assert "OCDT" in msgs or "engineering_cdt" in msgs


def test_all_package_partitions_are_classified():
    """真实包里的分区不应有『既非逻辑、也非 danger/warn/blocked』的漏网。

    需要设置环境变量 LROM_TEST_PACKAGE 指向一个真实 ROM 包，否则跳过。
    """
    import os

    pkg_path = os.environ.get("LROM_TEST_PACKAGE")
    if not pkg_path or not Path(pkg_path).exists():
        pytest.skip("未设置 LROM_TEST_PACKAGE")
    from lrom.core.package import inspect_package
    from lrom.core.plan import _classify_partition

    info = inspect_package(Path(pkg_path))
    for part in info.manifest.partitions:
        ph, risk, _ = _classify_partition(part.name)
        assert risk is not None
