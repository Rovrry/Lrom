"""全量备份（默认全备份）+ 机型日期命名 + 主页 ROM 信息补全 的测试。

对应本轮需求：
  1. 备份分区逻辑改为**默认全备份**（枚举设备实际存在的全部分区）；
  2. 备份产物目录按「机型_日期」命名（机型取 device.model，而非代号）；
  3. 主页 ROM 包信息补全：完整路径 / 机型 / 版本 / Android / 大小 / 分区数 / 解包状态。
"""

from __future__ import annotations

import re
import types
from pathlib import Path

from lrom.core import backup as bk
from lrom.core.fulldump import DiskEntry
from lrom.device.discover import Device, DeviceMode


class _FakeAdb:
    def __init__(self, names: list[str]):
        self.names = names

    def shell(self, cmd, timeout=None):
        if "by-name" in cmd:
            return types.SimpleNamespace(
                exit_code=0, stdout="\n".join(self.names) + "\n", stderr=""
            )
        return types.SimpleNamespace(exit_code=0, stdout="", stderr="")


def _dev(model="OPD2404", codename="OP5D77L1") -> Device:
    return Device(serial="X", mode=DeviceMode.ADB, model=model, codename=codename)


# ---------------------------------------------------------------------------
# 机型日期命名
# ---------------------------------------------------------------------------


def test_device_label_prefers_model():
    assert bk.device_label(_dev()) == "OPD2404"


def test_device_label_falls_back_to_codename():
    assert bk.device_label(_dev(model=None)) == "OP5D77L1"


def test_new_session_dir_uses_model_and_date(tmp_path):
    out = bk.new_session_dir(tmp_path, _dev())
    # 机型在前，日期时间戳形如 20261029-013451
    assert out.name.startswith("OPD2404_")
    assert re.search(r"OPD2404_\d{8}-\d{6}$", out.name)
    assert out.is_dir()


# ---------------------------------------------------------------------------
# 全量备份：枚举全部分区，排除 userdata/cache，关键分区优先
# ---------------------------------------------------------------------------


def test_list_device_partitions_is_full_and_excludes_userdata_cache():
    adb = _FakeAdb(
        ["userdata", "cache", "boot_a", "boot_b", "system_a", "ocdt", "sda"]
    )
    parts = bk.list_device_partitions(adb)
    assert "userdata" not in parts
    assert "cache" not in parts
    assert "sda" not in parts  # 整盘别名
    # 设备上其余分区全部纳入（默认全备份，不再是精选清单）
    assert {"boot_a", "boot_b", "system_a", "ocdt"} <= set(parts)
    # 关键分区（ocdt）排在最前，便于核对
    assert parts[0] == "ocdt"


def test_list_device_partitions_returns_empty_without_root():
    class _NoRoot:
        def shell(self, cmd, timeout=None):
            return types.SimpleNamespace(exit_code=1, stdout="", stderr="denied")

    assert bk.list_device_partitions(_NoRoot()) == []


# ---------------------------------------------------------------------------
# cmd_backup：不指定 --partitions 时走「全备份」
# ---------------------------------------------------------------------------


def test_cmd_backup_defaults_to_full_partition_set(monkeypatch, tmp_path, capsys):
    import lrom.cli as cli
    from lrom.core.backup import BackupResult

    fake_dev = _dev()
    monkeypatch.setattr(
        "lrom.device.discover.detect", lambda *a, **k: fake_dev
    )

    class _Adb:
        def __init__(self, *a, **k):
            self.serial = None

    monkeypatch.setattr("lrom.device.runner.AdbRunner", _Adb)
    monkeypatch.setattr(bk, "detect_root_prefix", lambda adb: "su -c")

    full = ["ocdt", "boot_a", "boot_b", "system_a"]
    monkeypatch.setattr(bk, "list_device_partitions", lambda adb, root_prefix="su -c": list(full))
    monkeypatch.setattr(bk, "estimate_size", lambda adb, parts, slot: 0)
    monkeypatch.setattr(bk, "check_space", lambda out_dir, needed: (True, "ok"))
    monkeypatch.setattr(bk, "new_session_dir", lambda base, dev: tmp_path)
    monkeypatch.setattr(bk, "write_manifest", lambda session: tmp_path / "backup.json")

    seen: list[str] = []

    def fake_backup(adb, part, out_path, *, slot, root_prefix="su -c", on_progress=None):
        seen.append(part)
        return BackupResult(part, out_path, size=1, sha256="x", ok=True)

    monkeypatch.setattr(bk, "backup_partition_adb", fake_backup)
    monkeypatch.setattr(bk, "prune_other_backups", lambda keep, base: [])

    rc = cli.cmd_backup(
        types.SimpleNamespace(partitions=None, output=str(tmp_path), adb_port=None)
    )
    assert rc == 0
    # 备份的正是设备全部实际分区（默认全备份），不再局限于精选清单
    assert seen == full
    # 目录命名走「机型_日期」（此处被 monkeypatch 成 tmp_path，但范围提示应出现）
    assert "全部分区" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# full_dump：disk_info.json 记录机型/日期等来源信息
# ---------------------------------------------------------------------------


def test_full_dump_writes_meta_into_disk_info(monkeypatch, tmp_path):
    from lrom.core import fulldump as fd

    entry = DiskEntry(
        name="boot", disk="sda", node="sda1",
        start_lba=0, num_sectors=1, size_bytes=512, sector_size=512,
    )
    monkeypatch.setattr(fd, "read_partition_table", lambda adb, root_prefix="su -c": [entry])
    monkeypatch.setattr(fd, "dump_gpt", lambda adb, root_prefix, disk, out_dir: ([], []))
    monkeypatch.setattr(
        fd, "dump_partitions",
        lambda adb, root_prefix, entries, out_dir, exclude=None, on_result=None, on_progress=None: (
            ["boot"], [], {}
        ),
    )

    def fake_xml(entries, out_path, *, sector_size=None):
        p = out_path.parent / "rawprogram0.xml"
        p.write_text("<data/>", encoding="utf-8")
        return p

    monkeypatch.setattr(fd, "write_rawprogram_xml", fake_xml)

    res = fd.full_dump(
        _FakeAdb([]), tmp_path, meta={"device": {"model": "OPD2404"}, "created_at": "T"}
    )
    assert res.ok
    import json

    info = json.loads((tmp_path / "disk_info.json").read_text(encoding="utf-8"))
    assert info["device"]["model"] == "OPD2404"
    assert info["created_at"] == "T"
    assert info["dumped"] == ["boot"]


# ---------------------------------------------------------------------------
# 主页 ROM 信息补全
# ---------------------------------------------------------------------------


def test_print_header_shows_full_path_and_package_facts(tmp_path, capsys):
    from lrom.tui import menu

    pkg = tmp_path / "rom.zip"
    pkg.write_bytes(b"PK\x03\x04")
    (tmp_path / "boot.img").write_bytes(b"x")

    info = types.SimpleNamespace(
        product_name="OPD2404",
        pre_device="OP5D77L1",
        android_version="16",
        version_name="16.0.5.1201",
        manifest=types.SimpleNamespace(partitions=[object()] * 47),
    )

    class _Sess:
        package = pkg
        work_dir = tmp_path
        unpacked = True
        image_dir = tmp_path

        def package_info(self):
            return info

    menu.print_header(_Sess(), None)
    out = capsys.readouterr().out
    assert str(pkg) in out  # 完整路径（确认是本地位置，而非项目默认）
    assert "OPD2404" in out and "Android 16" in out
    assert "分区 47" in out
    assert "已解包 1 个镜像" in out


# ---------------------------------------------------------------------------
# cmd_fulldump：目录按机型_日期命名 + 全备份默认包含 super
# ---------------------------------------------------------------------------


def test_cmd_fulldump_naming_and_full_scope(monkeypatch, tmp_path):
    import lrom.cli as cli
    from lrom.core.fulldump import FullDumpResult

    fake_dev = _dev()
    monkeypatch.setattr("lrom.device.discover.detect", lambda *a, **k: fake_dev)

    class _Adb:
        def __init__(self, *a, **k):
            self.serial = None

    monkeypatch.setattr("lrom.device.runner.AdbRunner", _Adb)
    monkeypatch.setattr(bk, "detect_root_prefix", lambda adb: "su -c")
    monkeypatch.setattr(bk, "snapshot_device", lambda adb, dev: {"model": "OPD2404"})

    captured = {}

    def fake_full_dump(adb, out_dir, *, root_prefix="su -c", exclude=None,
                       on_result=None, on_notice=None, on_progress=None, meta=None):
        captured["out_dir"] = out_dir
        captured["exclude"] = exclude
        captured["meta"] = meta
        return FullDumpResult(ok=True, out_dir=out_dir)

    monkeypatch.setattr("lrom.core.fulldump.full_dump", fake_full_dump)

    rc = cli.cmd_fulldump(
        types.SimpleNamespace(
            output=str(tmp_path), include_super=True, exclude=None, adb_port=None
        )
    )
    assert rc == 0
    name = captured["out_dir"].name
    assert re.search(r"OPD2404_\d{8}-\d{6}$", name), name
    # 全备份默认包含 super，只排除 userdata/cache
    assert set(captured["exclude"]) == {"userdata", "cache"}
    assert captured["meta"]["device"]["model"] == "OPD2404"
