"""字库备份：整盘分区表 + 全分区回读 + RawProgram XML（对齐紫罗兰「备份字库」）。

紫罗兰的做法（官方教程）：
  「备份字库会备份设备分区并生成主、备份 GPT 及 RawProgram XML，
    请确保磁盘空间充足。」——且必须在 **ADB + shell root** 下做，
    Fastboot 模式不可用。

本模块实现同一目标（纯 ADB + root，不碰 EDL）：
  1. 从 sysfs 读分区表（start/size），不依赖 GPT 可读（部分机型内核屏蔽 LBA0-33）；
  2. 尝试回读主/备份 GPT 原始字节（能读则存 .bin，读不到如实告警）；
  3. 逐分区 dd 回读为 .img；
  4. 生成 rawprogramN.xml（EDL/编程器刷写脚本）+ disk_info.json（清单）。

安全默认：
  · 排除 userdata / cache（用户数据、无备份价值且极大）
  · 默认排除 super（9GB+，系统分区可由官方包重刷）；需要时显式包含
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..device.runner import AdbRunner

#: 字库备份默认排除的分区（用户数据 / 缓存 / 极大系统分区）
DEFAULT_EXCLUDE = frozenset({"userdata", "cache", "super"})

#: 需要 root 才能读
_GPT_PRIMARY_SECTORS = 34
_GPT_BACKUP_SECTORS = 33


@dataclass
class DiskEntry:
    """一个分区在磁盘上的位置信息。"""

    name: str
    disk: str            # 如 "sde"
    node: str            # 如 "sde13"
    start_lba: int
    num_sectors: int
    size_bytes: int = 0
    sector_size: int = 512   # 设备逻辑扇区大小（UFS 常为 4096）

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1e6


@dataclass
class FullDumpResult:
    ok: bool = False
    out_dir: Path | None = None
    dumped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    gpt_files: list[Path] = field(default_factory=list)
    xml_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _sh(adb: AdbRunner, root_prefix: str, cmd: str, timeout: float = 60) -> str:
    res = adb.shell(f"{root_prefix} '{cmd}'", timeout=timeout)
    return (res.stdout or "").strip()


def list_whole_disks(adb: AdbRunner, root_prefix: str = "su -c") -> list[str]:
    """列出整盘块设备名（sda、sdb…，排除 sda1 这类分区节点）。"""
    out = _sh(adb, root_prefix, "ls /sys/class/block/")
    disks = []
    for name in out.split():
        if name.startswith("sd") and name[-1].isalpha() and "boot" not in name:
            disks.append(name)
    return sorted(disks)


def logical_block_size(adb: AdbRunner, root_prefix: str, disk: str) -> int:
    """读磁盘逻辑扇区大小（512 或 4096）。UFS 常见 4096。"""
    out = _sh(adb, root_prefix, f"cat /sys/class/block/{disk}/queue/logical_block_size")
    try:
        v = int(out)
        return v if v in (512, 4096) else 512
    except (TypeError, ValueError):
        return 512


def _disk_of_node(adb: AdbRunner, root_prefix: str, node: str) -> str | None:
    """把 sde13 → sde。"""
    out = _sh(adb, root_prefix, f"readlink -f /sys/class/block/{node}")
    if not out:
        return None
    # /sys/devices/.../block/sde/sde13
    parts = out.rstrip("/").split("/")
    return parts[-2] if len(parts) >= 2 else None


def read_partition_table(
    adb: AdbRunner, root_prefix: str = "su -c"
) -> list[DiskEntry]:
    """从 sysfs 读分区表（start/size），不依赖 GPT 是否可读。"""
    entries: list[DiskEntry] = []

    # by-name → 真实节点
    mapping: dict[str, str] = {}
    out = _sh(adb, root_prefix, "ls -l /dev/block/by-name/")
    for line in out.splitlines():
        # lrwxrwxrwx ... name -> /dev/block/sde13
        if "->" not in line:
            continue
        left, _, right = line.partition("->")
        name = left.split()[-1] if left.split() else ""
        node = right.strip().split("/")[-1]
        if name and node:
            mapping[name] = node

    lbs_cache: dict[str, int] = {}
    for name, node in sorted(mapping.items()):
        if not node.startswith("sd") or not node[-1].isdigit():
            continue  # 跳过 by-name 里的整盘别名
        disk = _disk_of_node(adb, root_prefix, node)
        if not disk:
            continue
        if disk not in lbs_cache:
            lbs_cache[disk] = logical_block_size(adb, root_prefix, disk)
        lbs = lbs_cache[disk]
        start_s = _sh(adb, root_prefix, f"cat /sys/class/block/{node}/start")
        size_s = _sh(adb, root_prefix, f"cat /sys/class/block/{node}/size")
        if not (start_s.isdigit() and size_s.isdigit()):
            continue
        # sysfs 的 start/size 单位固定是 512 字节；换算成设备逻辑扇区（UFS 常 4096）
        factor = max(1, lbs // 512)
        entries.append(
            DiskEntry(
                name=name,
                disk=disk,
                node=node,
                start_lba=int(start_s) // factor,
                num_sectors=int(size_s) // factor,
                size_bytes=int(size_s) * 512,
                sector_size=lbs,
            )
        )
    return entries


def dump_gpt(
    adb: AdbRunner, root_prefix: str, disk: str, out_dir: Path
) -> tuple[list[Path], list[str]]:
    """尝试回读主/备份 GPT 原始字节。

    返回 (写出的文件, 警告)。若 GPT 被内核屏蔽（读出全零），不写文件并告警。
    """
    files: list[Path] = []
    warns: list[str] = []
    lbs = logical_block_size(adb, root_prefix, disk)
    total_s = _sh(adb, root_prefix, f"cat /sys/class/block/{disk}/size")
    if not total_s.isdigit():
        return files, [f"{disk}: 读不到磁盘总扇区数，跳过 GPT 导出"]
    # sysfs size 是 512 字节扇区数 → 换算成设备 LBA
    total = int(total_s) // max(1, lbs // 512)

    def _read(skip: int, count: int, dest: Path) -> bool:
        argv = [
            adb.binary,
            *(("-s", adb.serial) if adb.serial else ()),
            "exec-out",
            f"{root_prefix} 'dd if=/dev/block/{disk} bs={lbs} skip={skip} count={count} 2>/dev/null'",
        ]
        try:
            res = subprocess.run(argv, capture_output=True, timeout=180)
        except subprocess.TimeoutExpired:
            return False
        data = res.stdout or b""
        if not data:
            return False
        # 主 GPT：头在块内第 2 个扇区（offset lbs）；
        # 备份 GPT：头在块内最后一个扇区（offset (count-1)*lbs）。
        if b"EFI PART" not in (data[lbs:lbs + 8], data[(count - 1) * lbs:(count - 1) * lbs + 8]):
            return False
        dest.write_bytes(data)
        return True

    primary = out_dir / f"gpt_primary_{disk}.bin"
    if _read(0, _GPT_PRIMARY_SECTORS, primary):
        files.append(primary)
    else:
        warns.append(
            f"{disk}: 主 GPT 读不到（内核把 LBA0-33 屏蔽成全零）。"
            "已改用 sysfs 分区表生成 XML；原始 GPT 无法导出。"
        )

    backup = out_dir / f"gpt_backup_{disk}.bin"
    if total > _GPT_BACKUP_SECTORS:
        if _read(total - _GPT_BACKUP_SECTORS, _GPT_BACKUP_SECTORS, backup):
            files.append(backup)
    return files, warns


def write_rawprogram_xml(
    entries: list[DiskEntry], out_path: Path, *, sector_size: int | None = None
) -> Path:
    """生成 rawprogramN.xml（EDL/编程器刷写脚本，按 LUN 分组）。

    每个磁盘（LUN）一个文件：rawprogram0.xml = sda，rawprogram1.xml = sdb…
    """
    by_disk: dict[str, list[DiskEntry]] = {}
    for e in entries:
        by_disk.setdefault(e.disk, []).append(e)

    lines: list[str] = []
    for lun_idx, disk in enumerate(sorted(by_disk)):
        items = sorted(by_disk[disk], key=lambda x: x.start_lba)
        if not items:
            continue
        fname = out_path.parent / f"rawprogram{lun_idx}.xml"
        # 每个磁盘用自己的逻辑扇区大小（UFS 常 4096）
        ss = sector_size or items[0].sector_size
        lines = [
            '<?xml version="1.0" ?>',
            '<data>',
            f'  <!-- LUN{lun_idx} = /dev/block/{disk}（按 sd 字母顺序推定，'
            "各机型实际 LUN 编号可能不同，刷写前务必核对） -->",
        ]
        for e in items:
            lines.append(
                '  <program SECTOR_SIZE_IN_BYTES="{}" file_sector_offset="0" '
                'filename="{}" label="{}" num_partition_sectors="{}" '
                'physical_partition_number="{}" start_sector="{}" />'.format(
                    ss, f"{e.name}.img", e.name,
                    e.num_sectors, lun_idx, e.start_lba,
                )
            )
        lines += ["</data>", ""]
        fname.write_text("\n".join(lines), encoding="utf-8")
    # 返回第一个（兼容调用方）；实际可能多个
    return out_path.parent / "rawprogram0.xml"


def dump_partitions(
    adb: AdbRunner,
    root_prefix: str,
    entries: list[DiskEntry],
    out_dir: Path,
    *,
    exclude: frozenset[str] = DEFAULT_EXCLUDE,
    on_result=None,
    on_progress=None,
) -> tuple[list[str], list[str], dict[str, str]]:
    """逐分区 dd 回读。返回 (成功, 跳过, 失败)。

    ``on_progress(name, done_bytes, total_bytes, elapsed_s)`` 在写入过程中约每秒
    回调一次，供上层实时显示已用时长/进度（大分区如 super 14GB 时尤其重要，
    否则长时间无输出会被误认为卡死）。
    """
    dumped: list[str] = []
    skipped: list[str] = []
    failed: dict[str, str] = {}

    for e in entries:
        if e.name in exclude:
            skipped.append(e.name)
            continue
        dest = out_dir / f"{e.name}.img"
        argv = [
            adb.binary,
            *(("-s", adb.serial) if adb.serial else ()),
            "exec-out",
            f"{root_prefix} 'dd if=/dev/block/by-name/{e.name} bs=4M 2>/dev/null'",
        ]
        tmp = dest.with_suffix(".img.part")
        start = time.monotonic()
        timed_out = False
        err = b""
        try:
            with open(tmp, "wb") as fh:
                proc = subprocess.Popen(argv, stdout=fh, stderr=subprocess.PIPE)
                while True:
                    try:
                        proc.wait(timeout=1.0)
                        break
                    except subprocess.TimeoutExpired:
                        if on_progress is not None:
                            try:
                                done = tmp.stat().st_size
                            except OSError:
                                done = 0
                            on_progress(
                                e.name, done, e.size_bytes, time.monotonic() - start
                            )
                        if time.monotonic() - start > 3600:
                            proc.kill()
                            proc.wait()
                            timed_out = True
                            break
                err = proc.stderr.read() if proc.stderr else b""
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            failed[e.name] = str(exc)
            if on_result:
                on_result(e.name, False, str(exc), time.monotonic() - start)
            continue

        elapsed = time.monotonic() - start
        if timed_out:
            tmp.unlink(missing_ok=True)
            failed[e.name] = "超时"
            if on_result:
                on_result(e.name, False, "超时", elapsed)
            continue
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            failed[e.name] = (err or b"").decode("utf-8", "replace")[:200]
            if on_result:
                on_result(e.name, False, failed[e.name], elapsed)
            continue
        size = tmp.stat().st_size
        if e.size_bytes and size != e.size_bytes:
            tmp.unlink(missing_ok=True)
            failed[e.name] = f"尺寸不符：期望 {e.size_bytes}，实得 {size}"
            if on_result:
                on_result(e.name, False, failed[e.name], elapsed)
            continue
        tmp.replace(dest)
        dumped.append(e.name)
        if on_result:
            on_result(e.name, True, f"{size / 1e6:.1f} MB", elapsed)
    return dumped, skipped, failed


def full_dump(
    adb: AdbRunner,
    out_dir: Path,
    *,
    root_prefix: str = "su -c",
    exclude: frozenset[str] = DEFAULT_EXCLUDE,
    on_result=None,
    on_notice=None,
    on_progress=None,
    meta: dict | None = None,
) -> FullDumpResult:
    """字库备份主流程。

    ``meta`` 会原样并入 ``disk_info.json``（用于记录机型/日期等来源信息）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = FullDumpResult(out_dir=out_dir)

    entries = read_partition_table(adb, root_prefix)
    if not entries:
        res.warnings.append("读不到分区表（sysfs 不可用或无 root）")
        return res

    if on_notice:
        on_notice(f"读到 {len(entries)} 个分区，开始导出 GPT…")
    for disk in sorted({e.disk for e in entries}):
        files, warns = dump_gpt(adb, root_prefix, disk, out_dir)
        res.gpt_files.extend(files)
        res.warnings.extend(warns)

    if on_notice:
        on_notice("回读分区…")
    dumped, skipped, failed = dump_partitions(
        adb, root_prefix, entries, out_dir,
        exclude=exclude, on_result=on_result, on_progress=on_progress,
    )
    res.dumped, res.skipped, res.failed = dumped, skipped, failed

    # 只把真正导出成功的分区写进 XML（未导出/排除的不写，避免刷写时找不到文件）
    exported = [e for e in entries if e.name in set(dumped)]
    xml = write_rawprogram_xml(exported, out_dir / "rawprogram0.xml")
    res.xml_files = sorted(out_dir.glob("rawprogram*.xml"))

    info = {
        "tool": "lrom",
        "kind": "fulldump",
        **(meta or {}),
        "partitions": [
            {
                "name": e.name, "disk": e.disk, "node": e.node,
                "start_lba": e.start_lba, "num_sectors": e.num_sectors,
                "size_bytes": e.size_bytes,
            }
            for e in exported
        ],
        "dumped": dumped, "skipped": skipped, "failed": failed,
        "gpt_files": [p.name for p in res.gpt_files],
        "xml_files": [p.name for p in res.xml_files],
        "warnings": res.warnings,
    }
    (out_dir / "disk_info.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    res.ok = bool(dumped) and not failed
    return res
