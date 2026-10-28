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
    # ── P0 不可再生 / 官方包没有（丢了基本救不回）──
    # OCDT 必须排第一，因为它最容易被忘掉。注意这是**两个不同的分区**：
    #   ocdt              128 KB，裸名无槽位
    #   engineering_cdt   1 MB × 2 槽
    "ocdt",
    "engineering_cdt",
    # 身份/基带（IMEI 依赖链：persist → modem → fsg → modemst1/2 → IMEI）
    "persist",       # 指纹/传感器校准/Wi-Fi MAC/DRM，工厂标定不可再生
    "modemst1",      # 高通标准名：IMEI、射频校准、网络 provisioning
    "modemst2",
    "nonmodemst1",   # 欧加改名版（一加平板 Pro 实测叫这个）
    "nonmodemst2",
    "fsg",           # modem 校准的黄金副本
    "fsc",           # 关联 IMEI 恢复
    # 欧加/高通校准类小分区（不可再生，官方包没有）
    "preisp_dt",     # ISP（相机）校准
    "preisp_otp",
    "preisp_dt_bk",
    "apdp",          # Qualcomm 供给/配置数据
    "apdpb",
    "apdp_full",
    "rtice",         # TEE/RTC 相关
    "rticmpdata_a",
    "secdata",       # 安全/attestation 数据
    "storsec",
    # 欧加专有：回锁相关 / AVB 备份（跨区恢复 Wi-Fi/IMEI 要用）
    "oplusreserve1",
    "oplusstanvbk",
    # ── P0 救回开机的最小集合 ──
    "boot",
    "init_boot",
    "vbmeta",
    "vbmeta_system",
    "vbmeta_vendor",
    "dtbo",
    "recovery",
    # ── P1 BL 链 / ARB（xbl_config 存 ARB 字段）──
    "xbl",
    "xbl_config",
    "abl",
    # ── P2 其它 ──
    "modem",         # 基带固件本体（官方包有，可重刷）
    "frp",           # 恢复出厂保护
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
        # 高通 efs 类 / 欧加专有：通常都是裸名（无 _a/_b）
        "modemst1",
        "modemst2",
        "fsg",
        "fsc",
        "mdm1m9kefs1",
        "mdm1m9kefs2",
        "mdm1m9kefs3",
        "mdm1m9kefsc",
        "oplusreserve1",
        "oplusstanvbk",
        "nonmodemst1",
        "nonmodemst2",
        "preisp_dt",
        "preisp_otp",
        "preisp_dt_bk",
        "apdp",
        "apdpb",
        "apdp_full",
        "rtice",
        "rticmpdata_a",
        "secdata",
        "storsec",
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
    #: 本机根本没有这个分区（不同机型差异），跳过而非失败
    missing: bool = False

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


def detect_root_prefix(adb: AdbRunner, timeout: float = 8.0) -> str | None:
    """探测可用的 root shell 前缀（"su -c" / "ksu -c" / ...），无 root 返回 None。

    备份必须 root：读取 /dev/block/by-name/* 需要 uid=0。普通 adb shell 是
    uid=2000，读不到。需在 root 管理器（Magisk / KernelSU / SukiSU / APatch）
    里【给 shell 授予 root 权限】。
    """
    from .arb import find_root_shell

    return find_root_shell(adb, timeout=timeout)


def has_root(adb: AdbRunner) -> bool:
    """检查 root shell 是否可用且能拿到 uid=0。"""
    return detect_root_prefix(adb) is not None
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


def _node_size(adb: AdbRunner, node: str, root_prefix: str = "su -c") -> int | None:
    """读分区节点字节数；节点不存在/无权限返回 None。"""
    res = adb.shell(
        f"{root_prefix} 'blockdev --getsize64 /dev/block/by-name/{node}'", timeout=30
    )
    if res.exit_code == 0 and res.stdout.strip().isdigit():
        return int(res.stdout.strip())
    return None


def resolve_node(
    adb: AdbRunner, partition: str, slot: str | None, root_prefix: str = "su -c"
) -> str | None:
    """探测分区在设备上的**实际节点名**（带槽 _a/_b 或裸名），都不存在返回 None。

    不同机型对同一分区的槽位情况不同（如 persist 无槽、xbl 有槽），
    硬编码容易错。这里按「首选名 → 备选名」依次探测，最稳。
    """
    preferred = _partition_name(partition, slot)
    bare = partition[:-2] if partition.endswith(("_a", "_b")) else partition
    candidates = [preferred]
    if bare != preferred:
        candidates.append(bare)
    # 若首选是裸名但设备可能是带槽的，再补一个带槽候选
    if slot and not preferred.endswith(("_a", "_b")):
        candidates.append(f"{bare}_{slot}")
    seen = set()
    for node in candidates:
        if node in seen:
            continue
        seen.add(node)
        if _node_size(adb, node, root_prefix) is not None:
            return node
    return None


def backup_partition_adb(
    adb: AdbRunner,
    partition: str,
    out_path: Path,
    *,
    slot: str | None,
    root_prefix: str = "su -c",
) -> BackupResult:
    """用 adb + root dd 备份单个分区。

    需要 root（给 adb shell 授予 root 权限）：读取 /dev/block 需要 uid=0。
    分区在本机不存在时返回 missing=True（跳过，不算失败）。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    node = resolve_node(adb, partition, slot, root_prefix)
    if node is None:
        return BackupResult(
            partition, None, ok=False, missing=True,
            error="本机无此分区（已跳过）",
        )

    # 先读尺寸，用于进度与校验
    expected = _node_size(adb, node, root_prefix) or 0

    # exec-out 走二进制通道，不经过 pty，避免数据损坏
    argv = [
        adb.binary,
        *(("-s", adb.serial) if adb.serial else ()),
        "exec-out",
        f"{root_prefix} 'dd if=/dev/block/by-name/{node} bs=4M 2>/dev/null'",
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


def prune_other_backups(keep: Path, base: Path) -> list[Path]:
    """删除除 keep 以外的其它「备份会话目录」，让本地只保留最新一份备份。

    判定：目录里存在 ``backup.json``（write_manifest 写的清单）。只看这个标记，
    绝不误删用户其它目录（如 unpacked/、root-work/）。

    返回被删除的目录列表。
    """
    # 安全前提：只有 keep 目录真实存在且已写入 backup.json 时才清理旧备份，
    # 否则（备份失败/中断）一律不删，避免把上一次可用备份删掉。
    try:
        if not keep.is_dir() or not (keep / "backup.json").exists():
            return []
    except OSError:
        return []

    keep_path = keep.resolve()
    removed: list[Path] = []
    try:
        if not base.is_dir():
            return removed
    except OSError:
        return removed
    for child in base.iterdir():
        try:
            if not child.is_dir():
                continue
            if child.resolve() == keep_path:
                continue
            if not (child / "backup.json").exists():
                continue
            shutil.rmtree(child)
            removed.append(child)
        except OSError:
            continue
    return removed
