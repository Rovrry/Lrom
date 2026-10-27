"""分区备份测试。

重点锁住 v0.4.5 的两处修正：

1. **备份清单必须包含 OCDT**（`ocdt` 与 `engineering_cdt`）。
   OCDT 损坏后 bootloader 进不去，是唯一「丢了就救不回来、
   只能拆字库」的分区，而旧清单完全漏了它。

2. **`ocdt` 不能加槽位后缀**。
   实测一加平板 Pro 上 `ocdt` 在分区表里是裸名，
   误加 `_a` 会去找一个不存在的节点，备份直接失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lrom.core.backup import (
    DEFAULT_BACKUP_PARTITIONS,
    OPTIONAL_PARTITIONS,
    ROOT_REQUIRED,
    _partition_name,
)


# ---------------------------------------------------------------------------
# 备份清单内容
# ---------------------------------------------------------------------------


def test_ocdt_in_backup_list():
    """OCDT 必须在默认备份清单里 —— 这是最重要的一条。"""
    assert "ocdt" in DEFAULT_BACKUP_PARTITIONS
    assert "engineering_cdt" in DEFAULT_BACKUP_PARTITIONS


def test_ocdt_is_first():
    """OCDT 排第一，因为它最容易被忘掉。"""
    assert DEFAULT_BACKUP_PARTITIONS[0] == "ocdt"


def test_core_restore_partitions_present():
    """救回开机的最小集合。"""
    for p in ("boot", "init_boot", "vbmeta", "dtbo"):
        assert p in DEFAULT_BACKUP_PARTITIONS


def test_persist_present():
    """persist 存指纹/传感器/校准，丢了不可再生。"""
    assert "persist" in DEFAULT_BACKUP_PARTITIONS


def test_no_duplicate_partitions():
    assert len(DEFAULT_BACKUP_PARTITIONS) == len(set(DEFAULT_BACKUP_PARTITIONS))


def test_ocdt_requires_root():
    assert "ocdt" in ROOT_REQUIRED
    assert "engineering_cdt" in ROOT_REQUIRED


def test_ocdt_is_not_optional():
    """OCDT 缺失必须算失败，不能静默跳过。"""
    assert "ocdt" not in OPTIONAL_PARTITIONS
    assert "engineering_cdt" not in OPTIONAL_PARTITIONS


def test_fsg_is_optional():
    """fsg 在部分机型不存在（实测一加平板 Pro 就没有）。"""
    assert "fsg" in OPTIONAL_PARTITIONS


# ---------------------------------------------------------------------------
# 槽位映射 —— 这里出错的代价是「备份了一个不存在的分区」
# ---------------------------------------------------------------------------


def test_ocdt_has_no_slot_suffix():
    """实测：ocdt 在分区表里是裸名，加 _a 会找不到。

    这条对应真实踩坑：fastboot 里 `partition-type:ocdt` 存在，
    但 `ocdt_a` 不存在。
    """
    assert _partition_name("ocdt", "a") == "ocdt"
    assert _partition_name("ocdt", "b") == "ocdt"
    assert _partition_name("ocdt", None) == "ocdt"


def test_engineering_cdt_gets_slot():
    """engineering_cdt 反而是有槽位的（实测 _a/_b 都是 1MB）。"""
    assert _partition_name("engineering_cdt", "a") == "engineering_cdt_a"
    assert _partition_name("engineering_cdt", "b") == "engineering_cdt_b"


@pytest.mark.parametrize("part", ["boot", "init_boot", "vbmeta", "dtbo", "modem"])
def test_slotted_partitions_get_suffix(part: str):
    assert _partition_name(part, "a") == f"{part}_a"


@pytest.mark.parametrize(
    "part", ["super", "userdata", "metadata", "misc", "persist", "frp"]
)
def test_noslot_partitions_keep_bare_name(part: str):
    assert _partition_name(part, "a") == part


def test_explicit_slot_suffix_preserved():
    """已经带 _a/_b 的名字不应被二次处理。"""
    assert _partition_name("boot_a", "b") == "boot_a"
    assert _partition_name("init_boot_b", "a") == "init_boot_b"


def test_no_slot_when_slot_unknown():
    assert _partition_name("boot", None) == "boot"
