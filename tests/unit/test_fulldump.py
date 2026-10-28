"""字库备份（fulldump）：分区表读取、4K 扇区换算、XML 生成、GPT 校验。"""

from __future__ import annotations

import types
from pathlib import Path

from lrom.core import fulldump as fd


class _FakeAdb:
    """模拟 adb shell 输出（按命令关键字返回）。"""

    binary = "adb"
    serial = "S1"

    def __init__(self, *, lbs=4096, byname=None, sizes=None, starts=None):
        self.lbs = lbs
        self.byname = byname or {}
        self.sizes = sizes or {}
        self.starts = starts or {}

    def shell(self, cmd, timeout=None):
        # cmd 形如: su -c '<inner>'
        inner = cmd.split("'", 1)[1].rsplit("'", 1)[0] if "'" in cmd else cmd
        if "ls -l /dev/block/by-name" in inner:
            lines = []
            for name, node in self.byname.items():
                lines.append(f"lrwxrwxrwx 1 root root 16 1970-01-01 00:00 {name} -> /dev/block/{node}")
            return types.SimpleNamespace(exit_code=0, stdout="\n".join(lines), stderr="")
        if "logical_block_size" in inner:
            return types.SimpleNamespace(exit_code=0, stdout=str(self.lbs), stderr="")
        if "readlink -f /sys/class/block/" in inner:
            import re as _re
            node = inner.rsplit("/", 1)[-1]
            disk = _re.sub(r"\d+$", "", node)
            return types.SimpleNamespace(
                exit_code=0, stdout=f"/sys/devices/platform/xxx/block/{disk}/{node}", stderr=""
            )
        if "/start" in inner:
            node = inner.split("/sys/class/block/")[1].split("/")[0]
            return types.SimpleNamespace(exit_code=0, stdout=str(self.starts.get(node, 0)), stderr="")
        if "/size" in inner:
            node = inner.split("/sys/class/block/")[1].split("/")[0]
            return types.SimpleNamespace(exit_code=0, stdout=str(self.sizes.get(node, 0)), stderr="")
        if "ls /sys/class/block/" in inner:
            return types.SimpleNamespace(exit_code=0, stdout="sda sda1 sde sde13", stderr="")
        return types.SimpleNamespace(exit_code=0, stdout="", stderr="")


def test_read_partition_table_converts_to_device_lba():
    """sysfs 的 start/size 是 512 字节单位；4K 设备要除以 8。"""
    adb = _FakeAdb(
        lbs=4096,
        byname={"boot_a": "sde13"},
        starts={"sde13": "973872"},
        sizes={"sde13": "1638400"},
    )
    t = fd.read_partition_table(adb, "su -c")
    assert len(t) == 1
    e = t[0]
    assert e.name == "boot_a" and e.disk == "sde" and e.node == "sde13"
    assert e.sector_size == 4096
    assert e.start_lba == 973872 // 8
    assert e.num_sectors == 1638400 // 8
    assert e.size_bytes == 1638400 * 512


def test_read_partition_table_512_device_no_change():
    adb = _FakeAdb(
        lbs=512,
        byname={"persist": "sda5"},
        starts={"sda5": "100"},
        sizes={"sda5": "200"},
    )
    t = fd.read_partition_table(adb, "su -c")
    assert t[0].start_lba == 100 and t[0].num_sectors == 200


def test_write_rawprogram_xml_uses_device_sector_size(tmp_path):
    entries = [
        fd.DiskEntry(name="boot_a", disk="sde", node="sde13",
                     start_lba=121734, num_sectors=204800, size_bytes=100, sector_size=4096),
        fd.DiskEntry(name="dtbo_a", disk="sde", node="sde14",
                     start_lba=400000, num_sectors=1000, size_bytes=10, sector_size=4096),
    ]
    xml = fd.write_rawprogram_xml(entries, tmp_path / "rawprogram0.xml")
    text = xml.read_text()
    assert 'SECTOR_SIZE_IN_BYTES="4096"' in text
    assert 'label="boot_a"' in text
    assert 'start_sector="121734"' in text
    assert 'num_partition_sectors="204800"' in text
    assert 'filename="boot_a.img"' in text


def test_write_rawprogram_xml_groups_per_disk(tmp_path):
    entries = [
        fd.DiskEntry("a1", "sda", "sda1", 10, 5, 0, 4096),
        fd.DiskEntry("b1", "sdb", "sdb1", 20, 6, 0, 4096),
    ]
    fd.write_rawprogram_xml(entries, tmp_path / "rawprogram0.xml")
    files = sorted(p.name for p in tmp_path.glob("rawprogram*.xml"))
    assert files == ["rawprogram0.xml", "rawprogram1.xml"]


def test_default_exclude_has_userdata_and_super():
    assert "userdata" in fd.DEFAULT_EXCLUDE
    assert "cache" in fd.DEFAULT_EXCLUDE
    assert "super" in fd.DEFAULT_EXCLUDE
