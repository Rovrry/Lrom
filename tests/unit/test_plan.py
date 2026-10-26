"""刷写计划测试。"""

from __future__ import annotations

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
