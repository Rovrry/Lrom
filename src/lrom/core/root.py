"""自动化 root —— 核心流程。

设计原则（对应项目的四条铁律）：
  1. **不砖机 > 功能多**
     - patch 前强制检查 ramdisk（防静默失败，见 bootimg.py）
     - 刷写前强制产出 retry 方案并落盘
     - 任何一步失败立即中止，不猜、不硬闯
  2. **离线优先**
     - 所有素材来自本地：ROM 包解出的 init_boot.img + 本机放置的 ksud
     - 唯一的外部产物是用户自己下载的 ksud / Manager APK，Lrom 不联网
  3. **先出计划，后执行**
     - 每个写设备动作都要经过 plan → 用户确认 → 执行
  4. **永不自动重启设备**
     - 刷写完成后停下来，把重启决策权交给用户

两种工作方式（对应 --method）：
  - ``ksud``：用 KernelSU 官方的 ksud 命令行工具在 PC 侧 patch。
    纯命令行、可全自动，推荐。
  - ``app``：把 init_boot 推到设备，引导用户用 KernelSU / Magisk
    Manager APP 手工 patch，再拉回来刷入。适合没有 ksud 的情况。

关于本机实测得到的几个硬事实（写代码时务必遵守）：
  - 目标分区是 **init_boot**，不是 boot（boot 的 ramdisk_size = 0）
  - KMI 必须按内核版本推导，不是按 Android 版本
    （本机内核 6.1.141-android14-11 → KMI = android14-6.1）
  - header 里的 ramdisk_size 不可信，不能拿来做解压缓冲区大小
  - AVB 在已解锁 + verifiedbootstate=orange 的设备上不会拦截
    （2026-10-27 本机实测：改过内容的 init_boot 可正常启动）
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..device.runner import AdbRunner, FastbootRunner, ToolNotFoundError, run
from ..device.tools import find_tool
from .bootimg import BootImageError, check_ramdisk, parse_boot_image

__all__ = [
    "RootMethod",
    "RootPlan",
    "PatchResult",
    "derive_kmi",
    "save_stock_image",
    "build_root_plan",
    "patch_with_ksud",
    "push_for_app_patch",
    "pull_app_patched",
    "flash_patched_image",
    "ROOT_PARTITION_CANDIDATES",
]

# Android 13（GKI 2.0）起 ramdisk 移到 init_boot。
# 顺序很重要：init_boot 优先。
ROOT_PARTITION_CANDIDATES = ("init_boot", "boot")


class RootMethod(Enum):
    KSUD = "ksud"
    APP = "app"


class RootError(Exception):
    """root 流程错误。"""


# ---------------------------------------------------------------------------
# KMI 推导
# ---------------------------------------------------------------------------


def derive_kmi(kernel_release: str) -> str | None:
    """从内核版本字符串推导 KMI。

    GKI 内核版本格式：
        Version.PatchLevel.SubLevel-AndroidRelease-KmiGeneration-suffix
        即 w.x.y-zzz-k-something
    KMI = ``w.x-zzz-k``（**SubLevel 不属于 KMI**）

    例如：
        5.10.101-android12-9-g30979850fc20  ->  5.10-android12-9
        6.1.141-android14-11-o-g5cdab3b518c1  ->  6.1-android14-11

    注意：这里的 AndroidRelease 是**内核**的代号，跟随设备出厂时的
    Android 版本，与设备当前 Android 版本无关。本机设备 Android 16
    但内核是 android14-11，KMI 是 ``6.1-android14-11`` 而非
    ``android16-6.12``。选错必然安装失败。

    Args:
        kernel_release: ``uname -r`` 的输出。

    Returns:
        KMI 字符串；无法解析时返回 None。
    """
    if not kernel_release:
        return None
    # 取第一段：6.1.141-android14-11-o-g5cdab3b518c1
    core = kernel_release.strip().split()[0] if kernel_release.strip() else ""
    m = re.match(
        r"^(?P<major>\d+)\.(?P<patch>\d+)\.\d+-(?P<android>android\d+)-(?P<gen>\d+)",
        core,
    )
    if not m:
        return None
    return f"{m.group('major')}.{m.group('patch')}-{m.group('android')}-{m.group('gen')}"


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


@dataclass
class RootPlan:
    """一次 root 操作的完整计划，供用户审阅。"""

    method: RootMethod
    partition: str
    source_image: Path
    serial: str | None = None
    slot: str | None = None
    stock_sha256: str = ""
    image_size: int = 0
    partition_size: int = 0
    kmi: str | None = None
    kernel_release: str | None = None
    ksud_path: str | None = None
    # 阻塞项：非空则不可执行
    blockers: list[str] = field(default_factory=list)
    # 警告项：可执行但需用户知晓
    warnings: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    @property
    def can_proceed(self) -> bool:
        return not self.blockers

    def describe(self) -> str:
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("  Lrom 自动化 root — 执行计划")
        lines.append("=" * 60)
        lines.append("")
        lines.append(f"  方式      : {self.method.value}")
        lines.append(f"  目标分区  : {self.partition}")
        if self.slot:
            lines.append(f"  当前槽位  : {self.slot}")
        if self.serial:
            lines.append(f"  设备序列号: {self.serial}")
        lines.append(f"  源镜像    : {self.source_image}")
        lines.append(f"  镜像大小  : {self.image_size:,} 字节")
        if self.partition_size:
            lines.append(f"  分区上限  : {self.partition_size:,} 字节")
        if self.kernel_release:
            lines.append(f"  内核版本  : {self.kernel_release}")
        if self.kmi:
            lines.append(f"  内核 KMI  : {self.kmi}")
        if self.ksud_path:
            lines.append(f"  ksud      : {self.ksud_path}")
        lines.append("")

        if self.steps:
            lines.append("  将要执行的步骤：")
            for i, s in enumerate(self.steps, 1):
                lines.append(f"    {i}. {s}")
            lines.append("")

        if self.warnings:
            lines.append("  ⚠️  注意事项：")
            for w in self.warnings:
                lines.append(f"     - {w}")
            lines.append("")

        if self.blockers:
            lines.append("  ⛔ 阻止执行的问题：")
            for b in self.blockers:
                lines.append(f"     {b}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)


@dataclass
class PatchResult:
    """patch 结果。"""

    ok: bool
    output: Path | None = None
    message: str = ""
    sha256: str = ""


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_root_plan(
    *,
    image: Path,
    method: RootMethod = RootMethod.KSUD,
    partition: str | None = None,
    device=None,
    fastboot: FastbootRunner | None = None,
) -> RootPlan:
    """生成 root 计划，不写设备、不改文件。

    Args:
        image: 待 patch 的原厂镜像（init_boot.img）。
        method: patch 方式。
        partition: 目标分区名，默认按镜像推断。
        device: discover.Device，用于读取槽位/内核。
        fastboot: 已构造的 FastbootRunner（可选，用于读分区大小）。
    """
    image = Path(image)
    plan = RootPlan(
        method=method,
        partition=partition or "init_boot",
        source_image=image,
        serial=getattr(device, "serial", None),
        slot=getattr(device, "slot", None),
    )

    # --- 1. 镜像基础检查 ---
    if not image.is_file():
        plan.blockers.append(f"镜像不存在：{image}")
        return plan

    plan.image_size = image.stat().st_size
    plan.stock_sha256 = _sha256(image)

    try:
        info = parse_boot_image(image)
    except BootImageError as exc:
        plan.blockers.append(str(exc))
        return plan

    # --- 2. ramdisk 检查（最关键）---
    ok, msg = check_ramdisk(image, partition=plan.partition)
    if not ok:
        plan.blockers.append(msg)
        return plan

    # --- 3. 分区大小检查 ---
    if fastboot is not None:
        try:
            raw_size = fastboot.getvar(f"partition-size:{plan.partition}")
            if raw_size:
                plan.partition_size = int(raw_size, 0)
        except (ValueError, Exception):  # noqa: BLE001
            plan.partition_size = 0
    if plan.partition_size and plan.image_size > plan.partition_size:
        plan.blockers.append(
            f"镜像 {plan.image_size:,} 字节超过分区 {plan.partition} 的 "
            f"{plan.partition_size:,} 字节，无法写入。"
        )

    # --- 4. 内核 / KMI ---
    kernel_release = None
    if plan.kmi is None:
        # 优先从设备读
        try:
            adb = AdbRunner()
            kernel_release = adb.shell("uname -r").stdout.strip()
        except Exception:  # noqa: BLE001
            kernel_release = None
    if kernel_release:
        plan.kernel_release = kernel_release
        plan.kmi = derive_kmi(kernel_release)
        if plan.kmi is None:
            plan.warnings.append(
                f"无法从内核版本 {kernel_release!r} 推导 KMI，"
                "需要手动指定 --kmi。"
            )

    # --- 5. 方式相关的可用性检查 ---
    if method is RootMethod.KSUD:
        try:
            plan.ksud_path = find_tool("ksud")
        except ToolNotFoundError:
            plan.blockers.append(
                "未找到 ksud。\n"
                "\n"
                "     ksud 是 KernelSU 官方提供的命令行 patch 工具，\n"
                "     Lrom 不捆绑它（保持零依赖），请自行下载后放到 PATH：\n"
                "\n"
                "       https://github.com/tiann/KernelSU/releases\n"
                "       下载 ksud-<版本>-x86_64-unknown-linux-musl，\n"
                "       chmod +x 后放到 ~/.local/bin/ 或 /usr/local/bin/\n"
                "\n"
                "     也可以用环境变量指定：\n"
                "       LROM_KSUD=/path/to/ksud ./lrom root ...\n"
                "\n"
                "     或者改用 APP 方式（不需要 ksud）：\n"
                "       ./lrom root --method app ..."
            )

    # --- 6. 通用警告 ---
    plan.warnings.append(
        "刷写后需要手动重启设备（Lrom 永不自动重启）。"
    )
    plan.warnings.append(
        "每次系统 OTA 升级都会覆盖 init_boot，root 会丢失，"
        "需要重新执行本命令。"
    )
    if method is RootMethod.APP:
        plan.warnings.append(
            "APP 方式需要你在手机上手动操作一次（安装 Manager、点击修补）。"
        )

    # --- 7. 步骤 ---
    if method is RootMethod.KSUD:
        kmi_hint = f" --kmi {plan.kmi}" if plan.kmi else ""
        plan.steps = [
            f"用 ksud patch 镜像：ksud boot-patch -b {image.name} "
            f"-o <输出>{kmi_hint}",
            "校验 patch 后的镜像（大小、ramdisk、AVB footer）",
            "把原厂镜像另存一份到 root-work/（回滚用）",
            "进入 fastboot 模式",
            f"fastboot flash {plan.partition} <patch后的镜像>",
            "停下，等待你手动重启",
            "重启后验证 root（su -c id）",
        ]
    else:
        plan.steps = [
            f"把 {image.name} 推送到设备 /sdcard/Download/",
            "等你用 Manager APP 修补该文件，导出一个新镜像",
            "把修补后的镜像拉回电脑",
            "校验 patch 后的镜像",
            "把原厂镜像另存一份到 root-work/（回滚用）",
            "进入 fastboot 模式",
            f"fastboot flash {plan.partition} <patch后的镜像>",
            "停下，等待你手动重启",
        ]

    return plan


# ---------------------------------------------------------------------------
# 原厂镜像留存（回滚基础）
# ---------------------------------------------------------------------------


def save_stock_image(image: Path, *, workdir: Path, partition: str) -> Path:
    """把原厂镜像另存一份，作为回滚用的底图。

    返回留存后的路径。
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = workdir / f"{partition}-stock-{stamp}.img"
    shutil.copy2(image, dest)

    meta = {
        "tool": "lrom",
        "kind": "root-stock-backup",
        "created_at": stamp,
        "partition": partition,
        "source": str(image),
        "size": dest.stat().st_size,
        "sha256": _sha256(dest),
    }
    (workdir / f"{partition}-stock-{stamp}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return dest


# ---------------------------------------------------------------------------
# ksud patch
# ---------------------------------------------------------------------------


def patch_with_ksud(
    *,
    image: Path,
    output: Path,
    ksud: str | None = None,
    kmi: str | None = None,
    on_line=None,
) -> PatchResult:
    """用 ksud 在 PC 侧 patch 镜像。

    注意：不加 --flash。Lrom 自己负责刷写，
    以便复用现有的风险检查与确认流程。
    """
    try:
        binary = ksud or find_tool("ksud")
    except ToolNotFoundError as exc:
        return PatchResult(ok=False, message=str(exc))

    argv = [binary, "boot-patch", "-b", str(image), "-o", str(output)]
    if kmi:
        argv += ["--kmi", kmi]

    try:
        res = run(argv, timeout=900, on_line=on_line)
    except ToolNotFoundError as exc:
        return PatchResult(ok=False, message=f"无法执行 ksud：{exc}")

    if res.timed_out:
        return PatchResult(ok=False, message="ksud 执行超时。")
    if res.exit_code != 0:
        return PatchResult(
            ok=False,
            message=f"ksud 失败（退出码 {res.exit_code}）\n{res.combined.strip()}",
        )

    # ksud 有时不按 -o 精确命名，尝试解析输出里的路径
    produced = output
    if not produced.is_file():
        m = re.search(r"(/\S+\.img)\b", res.combined)
        if m and Path(m.group(1)).is_file():
            produced = Path(m.group(1))
    if not produced.is_file():
        return PatchResult(
            ok=False,
            message=f"ksud 报告成功，但没找到输出镜像 {output}。\n"
            f"ksud 输出：\n{res.combined.strip()}",
        )

    return PatchResult(
        ok=True, output=produced, message="ksud patch 完成。", sha256=_sha256(produced)
    )


# ---------------------------------------------------------------------------
# APP 方式
# ---------------------------------------------------------------------------


def push_for_app_patch(*, image: Path, adb: AdbRunner | None = None) -> Path:
    """把镜像推到设备，供 Manager APP 修补。返回设备端路径。"""
    adb = adb or AdbRunner()
    remote_dir = "/sdcard/Download"
    adb.shell(f"mkdir -p {remote_dir}")
    remote = f"{remote_dir}/{Path(image).name}"
    res = adb.run("push", str(image), remote, timeout=300)
    if res.exit_code != 0:
        raise RootError(f"推送失败：{res.combined.strip()}")
    return Path(remote)


_PATCHED_PATTERNS = (
    re.compile(r"magisk_patched.*\.img$", re.IGNORECASE),
    re.compile(r".*kernelsu.*\.img$", re.IGNORECASE),
    re.compile(r".*ksu.*patched.*\.img$", re.IGNORECASE),
    re.compile(r".*patched.*\.img$", re.IGNORECASE),
)


def pull_app_patched(*, dest_dir: Path, adb: AdbRunner | None = None) -> Path | None:
    """从设备拉回 APP 修补好的镜像。

    在常见的下载目录里按文件名模式查找，取最新修改的那一个。
    """
    adb = adb or AdbRunner()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[str] = []
    for d in ("/sdcard/Download", "/sdcard", "/sdcard/Documents"):
        res = adb.shell(f"ls -t {d} 2>/dev/null")
        for line in res.stdout.splitlines():
            name = line.strip()
            if name.lower().endswith(".img") and any(
                p.search(name) for p in _PATCHED_PATTERNS
            ):
                candidates.append(f"{d}/{name}")
        if candidates:
            break

    if not candidates:
        return None

    remote = candidates[0]
    local = dest_dir / Path(remote).name
    res = adb.run("pull", remote, str(local), timeout=300)
    if res.exit_code != 0 or not local.is_file():
        raise RootError(f"拉取失败：{res.combined.strip()}")
    return local


# ---------------------------------------------------------------------------
# 刷写
# ---------------------------------------------------------------------------


def verify_patched_image(
    *, stock: Path, patched: Path, partition_size: int = 0
) -> tuple[bool, list[str]]:
    """校验 patch 后的镜像是否可安全刷入。

    这一步的价值最高：它把「刷进去才发现坏了」提前到「刷之前」。
    """
    problems: list[str] = []

    if not Path(patched).is_file():
        return False, [f"patch 后的镜像不存在：{patched}"]

    size = Path(patched).stat().st_size
    if partition_size and size > partition_size:
        problems.append(
            f"patch 后镜像 {size:,} 字节超过分区 {partition_size:,} 字节。"
        )

    try:
        stock_info = parse_boot_image(Path(stock))
        patched_info = parse_boot_image(Path(patched))
    except BootImageError as exc:
        return False, [f"patch 后的镜像无法解析：{exc}"]

    if not patched_info.has_ramdisk:
        problems.append("patch 后的镜像没有 ramdisk —— patch 可能是无效的。")

    if patched_info.header_version != stock_info.header_version:
        problems.append(
            f"patch 后 header 版本变了（v{stock_info.header_version} → "
            f"v{patched_info.header_version}），可能不兼容。"
        )

    if not patched_info.has_avb_footer:
        problems.append(
            "patch 后的镜像丢失了 AVB footer。"
            "原厂镜像有 footer 而 patch 后没有，可能导致无法启动。"
        )

    return (not problems), problems


def flash_patched_image(
    *,
    image: Path,
    partition: str,
    partition_size: int = 0,
    fastboot: FastbootRunner | None = None,
) -> tuple[bool, str]:
    """刷入 patch 后的镜像。调用前必须已通过 verify_patched_image。"""
    fastboot = fastboot or FastbootRunner()
    res = fastboot.run("flash", partition, str(image), timeout=600)
    if not fastboot.ok(res):
        return False, res.combined.strip()
    return True, "刷写完成"
