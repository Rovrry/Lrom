"""备份清单补全 + 节点探测 + 无root/有root 行为测试。"""

from __future__ import annotations

from pathlib import Path

from lrom.core import backup as bk


# ---------------------------------------------------------------------------
# 清单必须覆盖 IMEI/基带/回锁/BL 链关键分区
# ---------------------------------------------------------------------------

def test_default_list_covers_irreplaceable_partitions():
    d = set(bk.DEFAULT_BACKUP_PARTITIONS)
    # 不可再生（官方包没有）
    for p in ("persist", "modemst1", "modemst2", "fsg", "fsc",
              "engineering_cdt", "ocdt", "oplusreserve1", "oplusstanvbk"):
        assert p in d, f"备份清单缺少关键分区 {p}"
    # 救回开机
    for p in ("boot", "init_boot", "vbmeta", "dtbo", "recovery"):
        assert p in d
    # BL 链 / ARB
    for p in ("xbl", "xbl_config", "abl"):
        assert p in d


def test_default_list_has_no_duplicates():
    lst = list(bk.DEFAULT_BACKUP_PARTITIONS)
    assert len(lst) == len(set(lst))


# ---------------------------------------------------------------------------
# 节点探测：带槽 / 裸名都能正确解析
# ---------------------------------------------------------------------------

class _FakeAdb:
    """模拟 adb：给出存在的分区节点集合。"""

    def __init__(self, existing: set[str], *, root: str | None = "su -c"):
        self.existing = existing
        self.root = root
        self.calls = []

    def shell(self, cmd, timeout=None):
        import types
        self.calls.append(cmd)
        if self.root is None:
            return types.SimpleNamespace(exit_code=1, stdout="", stderr="su: not found")
        if "id" in cmd and cmd.strip().endswith("id"):
            return types.SimpleNamespace(exit_code=0, stdout="uid=0(root)", stderr="")
        # blockdev 查询
        if "blockdev" in cmd:
            for node in self.existing:
                if f"/{node}" in cmd:
                    return types.SimpleNamespace(exit_code=0, stdout="4096", stderr="")
            return types.SimpleNamespace(exit_code=1, stdout="", stderr="no")
        return types.SimpleNamespace(exit_code=0, stdout="", stderr="")


def test_resolve_node_prefers_slotted_when_exists():
    adb = _FakeAdb({"xbl_b", "persist"})
    assert bk.resolve_node(adb, "xbl", "b") == "xbl_b"


def test_resolve_node_falls_back_to_bare():
    adb = _FakeAdb({"persist"})
    assert bk.resolve_node(adb, "persist", "b") == "persist"


def test_resolve_node_returns_none_when_absent():
    adb = _FakeAdb({"boot_b"})
    assert bk.resolve_node(adb, "fsg", "b") is None


# ---------------------------------------------------------------------------
# 缺失分区 → missing=True（跳过，不算失败）
# ---------------------------------------------------------------------------

def test_backup_missing_partition_marks_missing(tmp_path):
    adb = _FakeAdb(set())  # 设备上什么都没有
    res = bk.backup_partition_adb(adb, "fsg", tmp_path / "fsg.img", slot="b")
    assert res.ok is False
    assert res.missing is True
    assert "跳过" in res.error or "无此分区" in res.error


# ---------------------------------------------------------------------------
# root 前缀探测：无 root / Magisk su / KernelSU ksu
# ---------------------------------------------------------------------------

def test_detect_root_prefix_none_without_root():
    adb = _FakeAdb({"persist"}, root=None)
    assert bk.detect_root_prefix(adb) is None


def test_detect_root_prefix_su():
    adb = _FakeAdb({"persist"}, root="su -c")
    assert bk.detect_root_prefix(adb) == "su -c"


def test_has_root_false_without_root():
    adb = _FakeAdb({"persist"}, root=None)
    assert bk.has_root(adb) is False
