"""设备分区备份。

刷机前把关键分区读回主机，是唯一的"后悔药"。

两种方式：
  - adb + root（dd）：设备已 root 时可用，速度快，可读任意分区
  - fastboot fetch：设备未 root、在 fastboot 模式时可用

备份产出一个目录，内含各分区 .img 与 manifest.json（记录 SHA-256、
设备信息、时间戳），便于事后校验与恢复。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..device.discover import Device
from ..device.runner import AdbRunner, FastbootRunner


#: 建议备份的分区（按重要性排序）。
#:
#: 这份清单来自一加社区/售后/XDA 的实战复盘，不是凭感觉排的。
#: 每一条都对应一种「不可逆」的损失：
#:
#:   P0  OCDT / engineering_cdt
#:         **最容易被忽略、后果最严重的一个。**
#:         OCDT 记录设备出厂校准数据，损坏后 bootloader 进不去。
#:         而「用别机的 OCDT」也会导致进不了 Fastboot —— 必须备份**本机**的。
#:         真正救不回来的砖，绝大多数是这个分区没了。
#:
#:   P0  boot / init_boot / vbmeta* / dtbo
#:         刷坏后唯一能救回开机的镜像。
#:
#:   P1  persist
#:         指纹、传感器、屏幕校准。丢了不可再生。
#:
#:   P2  modem / fsg
#:         信号相关，跨区刷写后的「信号修复」靠它。
#:
#: 注意：一加**没有独立的 efs 分区**，网上说的「备份 efs」在这类机型上
#: 实际抓手就是 persist + modem。
DEFAULT_BACKUP_PARTITIONS = (
    # P0 —— OCDT 必须排第一，因为它最容易被忘掉。
    # 注意这是**两个不同的分区**，都要备份：
    #   ocdt              128 KB，裸名无槽位
    #   engineering_cdt   1 MB × 2 槽
    "ocdt",
    "engineering_cdt",
    # P0 —— 救回开机的最小集合
    "boot",
    "init_boot",
    "vbmeta",
    "vbmeta_system",
    "vbmeta_vendor",
    "dtbo",
    "recovery",
    # P1 —— 不可再生的小分区
    "persist",
    # P2 —— 信号相关
    "modem",
    "fsg",
)

#: 分区表里就是裸名、**没有** _a/_b 后缀的分区。
#: 若误加后缀会去找一个不存在的设备节点，备份直接失败。
_NO_SLOT_PARTITIONS = frozenset(
    {
        "super",
        "userdata",
        "metadata",
        "misc",
        "persist",
        "ocdt",  # ← 实测：一加平板 Pro 上 ocdt 无槽位
        "cdt",
        "frp",
        "devinfo",
        "param",
    }
)

#: 需要 root 才能读（否则会被 SELinux 拒绝）
ROOT_REQUIRED = frozenset(
    {"super", "modem", "persist", "fsg", "engineering_cdt", "ocdt"}
)

#: 这些分区缺失时不算失败（部分机型没有对应分区）
OPTIONAL_PARTITIONS = frozenset({"fsg"})


@dataclass
class BackupResult:
    partition: str
    path: Path | None
    size: int = 0
    sha256: str = ""
    ok: bool = True
    error: str = ""

    @property
    def size_mb(self) -> float:
        return self.size / 1e6


@dataclass
class BackupSession:
    out_dir: Path
    device: dict
    results: list[BackupResult] = field(default_factory=list)
    started_at: str = ""

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def to_json(self) -> dict:
        return {
            "tool": "lrom",
            "created_at": self.started_at,
            "device": self.device,
            "partitions": [
                {
                    "name": r.partition,
                    "file": r.path.name if r.path else None,
                    "size": r.size,
                    "sha256": r.sha256,
                    "ok": r.ok,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def has_root(adb: AdbRunner) -> bool:
    """检查 su 是否可用且能拿到 uid=0。"""
    try:
        res = adb.shell("su -c id", timeout=20)
    except Exception:
        return False
    return res.exit_code == 0 and "uid=0" in res.stdout


def _partition_name(part: str, slot: str | None) -> str:
    """把裸分区名映射为实际设备节点名（补 _a/_b）。

    注意不是所有分区都有槽位。以下这些在分区表里就是裸名：
      super / userdata / metadata / misc / persist
      ocdt / cdt          ← OCDT 相关，**没有槽位**，补了反而找不到
    """
    if part.endswith(("_a", "_b")):
        return part
    if part in _NO_SLOT_PARTITIONS:
        return part
    if slot:
        return f"{part}_{slot}"
    return part


def backup_partition_adb(
    adb: AdbRunner,
    partition: str,
    out_path: Path,
    *,
    slot: str | None,
) -> BackupResult:
    """用 adb + root dd 备份单个分区。"""
    node = _partition_name(partition, slot)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 先读尺寸，用于进度与校验
    size_res = adb.shell(
        f"su -c 'blockdev --getsize64 /dev/block/by-name/{node}'", timeout=30
    )
    expected = 0
    if size_res.exit_code == 0 and size_res.stdout.strip().isdigit():
        expected = int(size_res.stdout.strip())

    # exec-out 走二进制通道，不经过 pty，避免数据损坏
    argv = [
        adb.binary,
        *(("-s", adb.serial) if adb.serial else ()),
        "exec-out",
        f"su -c 'dd if=/dev/block/by-name/{node} bs=4M 2>/dev/null'",
    ]
    env = None
    if adb.env:
        import os

        env = os.environ.copy()
        env.update(adb.env)

    tmp = out_path.with_suffix(out_path.suffix + ".part")
    try:
        with open(tmp, "wb") as f:
            proc = subprocess.Popen(argv, stdout=f, stderr=subprocess.PIPE, env=env)
            _, err = proc.communicate(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        tmp.unlink(missing_ok=True)
        return BackupResult(partition, None, ok=False, error="备份超时")

    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        return BackupResult(
            partition, None, ok=False, error=f"dd 失败: {err.decode('utf-8', 'replace')[:200]}"
        )

    actual = tmp.stat().st_size
    if expected and actual != expected:
        tmp.unlink(missing_ok=True)
        return BackupResult(
            partition,
            None,
            ok=False,
            error=f"尺寸不符：期望 {expected}，实得 {actual}",
        )
    if actual == 0:
        tmp.unlink(missing_ok=True)
        return BackupResult(partition, None, ok=False, error="读出的数据为空")

    tmp.replace(out_path)
    return BackupResult(
        partition=partition,
        path=out_path,
        size=actual,
        sha256=_sha256_file(out_path),
    )


def backup_partition_fastboot(
    fb: FastbootRunner, partition: str, out_path: Path, *, slot: str | None
) -> BackupResult:
    """用 fastboot fetch 备份单个分区（需要 fastboot 34+）。"""
    node = _partition_name(partition, slot)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    res = fb.run("fetch", node, str(out_path), timeout=1800)
    if not out_path.exists() or out_path.stat().st_size == 0:
        return BackupResult(
            partition, None, ok=False, error=f"fetch 失败: {res.combined.strip()[:200]}"
        )

    return BackupResult(
        partition=partition,
        path=out_path,
        size=out_path.stat().st_size,
        sha256=_sha256_file(out_path),
    )


def snapshot_device(adb: AdbRunner | None, dev: Device) -> dict:
    """记录设备指纹，写入备份清单。"""
    info = {
        "serial": dev.serial,
        "mode": dev.mode.value,
        "codename": dev.codename,
        "model": dev.model,
        "product_name": dev.product_name,
        "build_id": dev.build_id,
        "android_version": dev.android_version,
        "slot": dev.slot,
        "locked": dev.locked,
    }
    if adb:
        for prop in (
            "ro.build.fingerprint",
            "ro.boot.verifiedbootstate",
            "ro.boot.vbmeta.digest",
            "ro.boot.vbmeta.avb_version",
        ):
            try:
                info[prop] = adb.getprop(prop)
            except Exception:
                pass
    return info


def check_space(out_dir: Path, needed: int) -> tuple[bool, str]:
    usage = shutil.disk_usage(out_dir)
    if usage.free < needed:
        return False, (
            f"磁盘空间不足：需要 {needed / 2**30:.1f} GB，"
            f"可用 {usage.free / 2**30:.1f} GB"
        )
    return True, f"磁盘可用 {usage.free / 2**30:.1f} GB"


def estimate_size(adb: AdbRunner, partitions: list[str], slot: str | None) -> int:
    """估算备份总大小。"""
    total = 0
    for p in partitions:
        node = _partition_name(p, slot)
        res = adb.shell(
            f"su -c 'blockdev --getsize64 /dev/block/by-name/{node}'", timeout=20
        )
        if res.exit_code == 0 and res.stdout.strip().isdigit():
            total += int(res.stdout.strip())
    return total


def new_session_dir(base: Path, device: Device) -> Path:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    name = f"{device.codename or 'unknown'}_{stamp}"
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_manifest(session: BackupSession) -> Path:
    path = session.out_dir / "backup.json"
    path.write_text(
        json.dumps(session.to_json(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
