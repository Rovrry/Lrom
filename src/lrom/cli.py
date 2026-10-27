"""Lrom 命令行入口。"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .core.package import (
    PackageInfo,
    Severity,
    Verdict,
    check_device_match,
    inspect_package,
)

# 终端颜色
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"


def _use_color() -> bool:
    return sys.stdout.isatty()


def paint(text: str, color: str) -> str:
    if not _use_color():
        return text
    return f"{color}{text}{C.RESET}"


SEVERITY_STYLE = {
    Severity.INFO: ("··", C.BLUE),
    Severity.WARN: ("!!", C.YELLOW),
    Severity.DANGER: ("**", C.MAGENTA),
    Severity.BLOCKER: ("XX", C.RED),
}


def print_info(info: PackageInfo) -> None:
    print()
    print(paint(f"╭─ ROM 包分析 ─ {info.path.name}", C.BOLD))
    print(f"│ 文件      : {info.path}")
    size_mb = info.path.stat().st_size / 1e6 if info.path.exists() else 0
    print(f"│ 大小      : {size_mb:,.0f} MB")
    print(f"│ 包类型    : {paint(info.kind.value, C.CYAN)}")

    if info.product_name:
        print(f"│ 产品名    : {info.product_name}")
    if info.pre_device:
        print(f"│ 设备代号  : {info.pre_device}")
    if info.display_os_version:
        print(f"│ 显示版本  : {info.display_os_version}")
    if info.version_name:
        print(f"│ 版本名    : {info.version_name}")
    if info.android_version:
        print(f"│ Android   : {info.android_version}")
    if info.security_patch:
        print(f"│ 安全补丁  : {info.security_patch}")
    if info.ota_type:
        print(f"│ OTA 类型  : {info.ota_type}")
    print(paint("╰" + "─" * 40, C.BOLD))

    if info.manifest:
        print()
        print(paint(f"  分区清单（{len(info.manifest.partitions)} 个）", C.BOLD))
        _print_partitions(info)

    print()
    print(paint("  检查结果", C.BOLD))
    for f in info.findings:
        mark, color = SEVERITY_STYLE[f.severity]
        print(f"  {paint(mark, color)} {f.message}")

    print()
    verdict_color = C.GREEN if info.verdict is Verdict.FLASHABLE else C.RED
    print(paint(f"  结论: {info.verdict.value}", verdict_color + C.BOLD))
    print()


def _print_partitions(info: PackageInfo) -> None:
    assert info.manifest
    bs = info.manifest.block_size
    parts = sorted(
        info.manifest.partitions,
        key=lambda p: p.size_bytes(bs),
        reverse=True,
    )
    print(f"  {'分区名':<20}{'大小(MB)':>10}{'操作数':>8}  {'类型'}")
    print(f"  {'─' * 20}{'─' * 10}{'─' * 8}  {'─' * 30}")
    for p in parts:
        mb = p.size_bytes(bs) / 1e6
        types = ",".join(sorted(t.label for t in p.op_types))
        flag = paint(" [需源数据]", C.RED) if p.needs_source_data else ""
        print(f"  {p.name:<20}{mb:>10,.0f}{len(p.operations):>8}  {types}{flag}")


def cmd_inspect(args: argparse.Namespace) -> int:
    info = inspect_package(args.package)
    print_info(info)

    if not args.no_device:
        _check_against_device(info, getattr(args, 'adb_port', None))

    return 0 if info.verdict is Verdict.FLASHABLE else 1


def _check_against_device(info: PackageInfo, adb_port: int | None = None) -> None:
    from .device.discover import DeviceMode, detect

    print(paint("  设备核对", C.BOLD))
    dev = detect(adb_server_port=adb_port)
    if not dev:
        print(f"  {paint('··', C.BLUE)} 未检测到设备，跳过型号核对")
        print()
        return

    print(f"  ·· {dev.serial}  {dev.mode.value}")
    if dev.mode is not DeviceMode.ADB:
        print(f"  {paint('!!', C.YELLOW)} 设备不在 ADB 模式，无法读取型号核对")
        print()
        return

    if dev.codename:
        findings = check_device_match(info, dev.codename, dev.model)
        for f in findings:
            mark, color = SEVERITY_STYLE[f.severity]
            print(f"  {paint(mark, color)} {f.message}")
    print()


def cmd_devices(args: argparse.Namespace) -> int:
    from .device.discover import detect

    dev = detect()
    if not dev:
        print(paint("未检测到设备", C.YELLOW))
        print()
        print("排查建议:")
        print("  1. 确认数据线插在电脑上（不是充电器）")
        print("  2. 手机已开启「开发者选项 → USB 调试」")
        print("  3. 手机上确认「允许 USB 调试」弹窗")
        print("  4. 运行 lsusb 检查系统是否识别到设备")
        return 1

    print()
    print(dev.describe())
    print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lrom",
        description="Lrom — Linux 原生 Android 线刷工具",
        epilog="不带参数运行可进入交互菜单",
    )
    p.add_argument("-V", "--version", action="version", version=f"Lrom {__version__}")

    sub = p.add_subparsers(dest="command")

    pi = sub.add_parser("inspect", help="分析 ROM 包，判断能否线刷")
    pi.add_argument("package", type=Path, help="ROM 包路径（.zip 或目录）")
    pi.add_argument("--no-device", action="store_true", help="跳过设备核对")
    pi.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pi.set_defaults(func=cmd_inspect)

    pd = sub.add_parser("devices", help="检测连接的设备")
    pd.set_defaults(func=cmd_devices)

    psc = sub.add_parser("selfcheck", help="环境自检（adb/udev/权限）")
    psc.set_defaults(func=cmd_selfcheck)

    pb = sub.add_parser("backup", help="刷前备份关键分区（需要 root）")
    pb.add_argument(
        "--partitions",
        nargs="+",
        default=None,
        help="要备份的分区（默认: boot init_boot vbmeta 等关键分区）",
    )
    pb.add_argument("-o", "--output", default="backups", help="备份根目录（默认 backups/）")
    pb.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pb.set_defaults(func=cmd_backup)

    pp = sub.add_parser("plan", help="生成刷写计划（不写设备）")
    pp.add_argument("package", type=Path, help="ROM 包路径")
    pp.add_argument("-o", "--output", type=Path, default=None, help="保存计划为 JSON")
    pp.add_argument("--partitions", nargs="+", default=None, help="只刷指定分区")
    pp.add_argument("--allow-dangerous", action="store_true", help="允许写入危险分区")
    pp.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pp.set_defaults(func=cmd_plan)

    pu = sub.add_parser("unpack", help="解包 ROM 中的分区镜像")
    pu.add_argument("package", type=Path, help="ROM 包路径")
    pu.add_argument("-o", "--output", type=Path, default=None, help="输出目录")
    pu.add_argument("--partitions", nargs="+", default=None, help="只解指定分区")
    pu.add_argument("-q", "--quiet", action="store_true", help="安静模式")
    pu.set_defaults(func=cmd_unpack)

    pf = sub.add_parser("flash", help="执行刷写")
    pf.add_argument("package", type=Path, help="ROM 包路径")
    pf.add_argument(
        "-i", "--images", default="unpacked/rom", help="镜像目录（默认 unpacked/rom）"
    )
    pf.add_argument("--dry-run", action="store_true", help="预演，不写设备")
    pf.add_argument("--yes", action="store_true", help="跳过确认（危险）")
    pf.add_argument("--partitions", nargs="+", default=None, help="只刷指定分区")
    pf.add_argument("--allow-dangerous", action="store_true", help="允许写危险分区")
    pf.add_argument(
        "--no-reboot",
        action="store_true",
        help=argparse.SUPPRESS,  # 已废弃：Lrom 永不自动重启
    )
    pf.add_argument(
        "--both-slots",
        action="store_true",
        help="同时刷写 A/B 两个槽位（切换槽后不会回退到旧版本）",
    )
    pf.add_argument(
        "--no-recreate-logical",
        action="store_true",
        help="不重建逻辑分区（默认会先 delete/create，更可靠）",
    )
    pf.add_argument(
        "--force-arb",
        action="store_true",
        help="跳过 ARB 熔断拦截（降级时极度危险，可能硬砖）",
    )
    pf.add_argument(
        "--wipe",
        action="store_true",
        help="清空用户数据（恢复出厂设置，降级/救砖时使用）",
    )
    pf.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pf.set_defaults(func=cmd_flash)

    pa = sub.add_parser("arb", help="ARB 熔断检测（判断降级是否会变砖）")
    pa.add_argument(
        "-i", "--images", default="unpacked/rom", help="镜像目录（默认 unpacked/rom）"
    )
    pa.add_argument("--xbl-config", default=None, help="指定 xbl_config 镜像路径")
    pa.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pa.set_defaults(func=cmd_arb)

    pr = sub.add_parser(
        "root",
        help="自动化 root（patch init_boot 建立 root）",
        description=(
            "自动化 root。默认用 ksud 在 PC 侧 patch init_boot，"
            "校验后刷入，最后停下等你手动重启。"
        ),
    )
    pr.add_argument(
        "-i",
        "--image",
        type=Path,
        default=None,
        help="原厂 init_boot.img 路径（默认自动在 unpacked/rom 里找）",
    )
    pr.add_argument(
        "--method",
        choices=["ksud", "app"],
        default="ksud",
        help="patch 方式：ksud（全自动，推荐）或 app（用手机上的 Manager 修补）",
    )
    pr.add_argument(
        "--partition",
        default=None,
        help="目标分区（默认 init_boot）",
    )
    pr.add_argument(
        "--flash-mode",
        choices=["fastboot", "fastbootd"],
        default=None,
        help="刷入模式：fastboot（bootloader，默认）或 fastbootd（用户空间，"
        "OCDT 损坏时用）；不指定则交互时询问",
    )
    pr.add_argument(
        "--kmi",
        default=None,
        help="手动指定 KMI（如 6.1-android14-11），默认按内核版本自动推导",
    )
    pr.add_argument(
        "--dry-run",
        action="store_true",
        help="只生成计划，不 patch、不写设备",
    )
    pr.add_argument(
        "--yes",
        action="store_true",
        help="跳过确认（危险，不推荐）",
    )
    pr.add_argument(
        "--workdir",
        default="root-work",
        help="工作目录，存放 patch 产物与原厂底图（默认 root-work/）",
    )
    pr.add_argument("--adb-port", type=int, default=None, help="adb server 端口")
    pr.set_defaults(func=cmd_root)

    pm = sub.add_parser(
        "fetch-manager",
        help="下载 root 管理器 APK（**唯一的联网命令**）",
        description=(
            "从 GitHub Releases 下载最新版本的 root 管理器 APK。\n"
            "这是 Lrom 唯一会联网的功能：只访问 api.github.com / "
            "github.com，只下载文件、不执行任何远程代码，"
            "下载后强制校验 sha256。"
        ),
    )
    pm.add_argument(
        "--source",
        choices=["ksu", "ksu-next", "sukisu", "magisk"],
        default="ksu",
        help="管理器来源（默认 ksu）",
    )
    pm.add_argument(
        "--channel",
        choices=["stable", "beta"],
        default="stable",
        help="渠道：stable（正式版，默认）或 beta（测试版）",
    )
    pm.add_argument(
        "--dest",
        default="root-work",
        help="保存目录（默认 root-work/）",
    )
    pm.add_argument(
        "--asset",
        default=None,
        help="手动指定 asset 文件名（自动选择有歧义时使用）",
    )
    pm.add_argument(
        "--yes",
        action="store_true",
        help="跳过下载前确认",
    )
    pm.set_defaults(func=cmd_fetch_manager)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        from .tui.menu import run_menu

        return run_menu()

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已取消")
        return 130


if __name__ == "__main__":
    sys.exit(main())


def cmd_backup(args: argparse.Namespace) -> int:
    """刷前备份关键分区。"""
    from .core import backup as bk
    from .device.discover import DeviceMode, detect
    from .device.runner import AdbRunner

    dev = detect()
    if not dev:
        print(paint("未检测到设备", C.RED))
        return 1
    if dev.mode is not DeviceMode.ADB:
        print(paint(f"设备当前在 {dev.mode.value}，备份需要 ADB 模式", C.RED))
        return 1

    adb = AdbRunner(server_port=args.adb_port)
    adb.serial = dev.serial

    if not bk.has_root(adb):
        print(paint("未检测到 root。请给 adb shell 授予 root 权限后重试。", C.RED))
        return 1
    print(paint("✓ root 权限可用", C.GREEN))

    from .core.backup import DEFAULT_BACKUP_PARTITIONS

    parts = list(args.partitions or DEFAULT_BACKUP_PARTITIONS)
    out_dir = bk.new_session_dir(Path(args.output), dev)

    needed = bk.estimate_size(adb, parts, dev.slot)
    ok, msg = bk.check_space(out_dir, needed)
    print(f"  预计需要 {needed / 2**30:.2f} GB，{msg}")
    if not ok:
        print(paint(f"  {msg}", C.RED))
        return 1

    print()
    print(paint(f"备份到: {out_dir}", C.BOLD))
    print()

    session = bk.BackupSession(
        out_dir=out_dir,
        device=bk.snapshot_device(adb, dev),
        started_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
    )

    critical = [p for p in parts if p not in bk.OPTIONAL_PARTITIONS]

    for part in parts:
        print(f"  {part:<18}", end="", flush=True)
        result = bk.backup_partition_adb(
            adb, part, out_dir / f"{part}.img", slot=dev.slot
        )
        session.results.append(result)
        if result.ok:
            print(paint(f"✓ {result.size_mb:>8,.1f} MB", C.GREEN))
        elif part in bk.OPTIONAL_PARTITIONS:
            # 部分机型没有这个分区（例如某些版本没有 fsg），不算失败
            print(paint(f"· 跳过（本机可能无此分区）", C.DIM))
        else:
            print(paint(f"✗ {result.error}", C.RED))

    manifest = bk.write_manifest(session)
    print()
    print(f"  清单: {manifest}")

    ok_parts = [r for r in session.results if r.ok]
    failed_critical = [r for r in session.results if not r.ok and r.partition in critical]

    print()
    if not failed_critical:
        print(
            paint(
                f"✓ 备份完成（{len(ok_parts)} 个分区）",
                C.GREEN + C.BOLD,
            )
        )
        # OCDT 单独强调 —— 它是唯一「丢了就救不回来」的分区
        if any(r.partition == "engineering_cdt" and r.ok for r in session.results):
            print()
            print(
                paint(
                    "  ★ engineering_cdt（OCDT）已备份。",
                    C.GREEN,
                )
            )
            print("    这份文件是本机独有的 —— 恢复时**必须**用它，")
            print("    用别的设备的 OCDT 会导致进不了 Fastboot。")
            print("    建议另存到 U 盘/云盘，不要只留在这台电脑上。")
        return 0

    print(
        paint(
            f"✗ 关键分区备份失败: {', '.join(r.partition for r in failed_critical)}",
            C.RED + C.BOLD,
        )
    )
    print()
    print("  这些分区缺失时刷机**没有安全退路**，建议先解决再继续：")
    for r in failed_critical:
        print(f"    · {r.partition}: {r.error}")
    return 1


def cmd_plan(args: argparse.Namespace) -> int:
    """生成刷写计划（不碰设备）。"""
    from .core.package import Severity, Verdict, check_device_match, inspect_package
    from .core.payload import payload_data_offset_in_zip, read_payload_header
    from .core.plan import build_plan, format_plan
    from .device.discover import DeviceMode, detect, list_partitions
    from .device.runner import AdbRunner

    info = inspect_package(args.package)
    if info.verdict is not Verdict.FLASHABLE or info.manifest is None:
        print(paint(f"✗ 该包不能线刷: {info.verdict.value}", C.RED + C.BOLD))
        for f in info.findings:
            if f.severity in (Severity.BLOCKER, Severity.DANGER):
                print(f"  {f.message}")
        return 1

    # 设备信息
    dev = detect(adb_server_port=args.adb_port)

    # 预演阶段就检查 ARB 熔断机型 —— 免得到真刷时才发现刷不了
    from .core.arb_db import ArbRisk as _ArbRisk
    from .core.arb_db import check_arb_risk as _check_arb

    _arb = _check_arb(
        package_version=info.version_name,
        device_version=getattr(dev, "build_id", None) if dev else None,
        model_code=info.product_name,
        codename=(getattr(dev, "codename", None) if dev else None) or info.pre_device,
    )
    if _arb.risk is _ArbRisk.BLOCK:
        print(paint("✗ ARB 熔断保护：预演不通过", C.RED + C.BOLD))
        print()
        print(f"  {_arb.message}")
        print()
        print("  刷入这个包会直接把设备变砖，且无自救通道。")
        print()
        return 1
    if _arb.risk is _ArbRisk.WARN:
        print(paint("  !! ARB 熔断风险（预演）", C.YELLOW))
        print(f"  {_arb.message}")
        print()

    # 预演阶段就检查 Bootloader 锁 —— 免得到真刷时才发现刷不了
    if dev is not None:
        if dev.locked is None:
            print(
                paint("  !! 无法确认 Bootloader 是否已解锁", C.YELLOW)
            )
        elif str(dev.locked) != "0":
            print(paint("✗ Bootloader 未解锁，无法刷写", C.RED + C.BOLD))
            print("  预演不通过：即使现在生成计划，真正刷写时也会被拒绝。")
            return 1
        else:
            print(paint("  ✓ Bootloader 已解锁", C.GREEN))

    device_parts: set[str] = set()
    if dev and dev.mode is DeviceMode.ADB:
        findings = check_device_match(info, dev.codename or "", dev.model)
        blockers = [f for f in findings if f.severity is Severity.BLOCKER]
        if blockers:
            print(paint("✗ 型号不匹配，拒绝生成计划", C.RED + C.BOLD))
            for f in blockers:
                print(f"  {f.message}")
            return 1
        adb = AdbRunner(server_port=args.adb_port)
        device_parts = list_partitions(adb, dev.serial)

    plan = build_plan(
        info.manifest,
        package_path=str(info.path),
        device_model=dev.model if dev else None,
        device_codename=dev.codename if dev else None,
        slot=dev.slot if dev else None,
        device_partitions=device_parts or None,
        allow_dangerous=args.allow_dangerous,
        include=args.partitions,
    )

    print()
    print(format_plan(plan))
    print()

    if args.output:
        out = plan.save(Path(args.output))
        print(f"计划已保存: {out}")
        print()
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    """解包 ROM 包中的分区镜像。"""
    import time as _time

    from .core.package import Severity, Verdict, inspect_package
    from .core.unpack import unpack_package, write_index

    info = inspect_package(args.package)
    if info.verdict is not Verdict.FLASHABLE or info.manifest is None:
        print(paint(f"✗ 该包不能解包: {info.verdict.value}", C.RED + C.BOLD))
        for f in info.findings:
            if f.severity in (Severity.BLOCKER, Severity.DANGER):
                print(f"  {f.message}")
        return 1

    out_dir = Path(args.output) if args.output else (
        Path("unpacked") / Path(args.package).stem
    )

    # ---- 磁盘空间检查 ----
    # 全量包解出来的镜像往往比 zip 本身大得多（payload 是压缩的），
    # 不检查的话会解到一半磁盘满，留下半截文件更难处理。
    try:
        targets = set(args.partitions or [p.name for p in info.manifest.partitions])
        bs = info.manifest.block_size
        need = sum(
            p.size_bytes(bs)
            for p in info.manifest.partitions
            if p.name in targets
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(out_dir).free
        # 留 5% 余量，避免刚好卡在边界
        need_with_margin = int(need * 1.05)
        if free < need_with_margin:
            print(
                paint(
                    "✗ 磁盘空间不足，解包会中途失败",
                    C.RED + C.BOLD,
                )
            )
            print()
            print(f"  需要  : {need_with_margin / 2**30:.1f} GB")
            print(f"  可用  : {free / 2**30:.1f} GB")
            print(f"  目录  : {out_dir}")
            print()
            print("  建议：")
            print("    · 换一个空间更大的目录：-o /path/to/bigger/disk")
            print("    · 或只解需要的分区：--partitions boot init_boot")
            print()
            return 1
    except OSError:
        pass  # 拿不到磁盘信息就跳过检查，不阻塞

    print()
    print(paint(f"解包: {Path(args.package).name}", C.BOLD))
    print(f"  输出目录: {out_dir}")
    if args.partitions:
        print(f"  仅解: {', '.join(args.partitions)}")
    print()

    last_shown = {"name": "", "pct": -10}

    def on_progress(name: str, done: int, total: int) -> None:
        pct = done / total * 100 if total else 100.0
        # 每个分区只刷新约 10 次，避免刷屏
        if last_shown["name"] != name:
            last_shown["name"] = name
            last_shown["pct"] = -10
        if pct - last_shown["pct"] < 10 and pct < 100:
            return
        last_shown["pct"] = pct
        bar_len = 24
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  {name:<18} {bar} {pct:5.1f}%", end="", flush=True)

    def on_result(res) -> None:
        print("\r" + " " * 70, end="\r")
        if res.ok:
            print(f"  {res.partition:<18} {paint('✓', C.GREEN)} {res.size_mb:>9,.1f} MB")
        else:
            print(f"  {res.partition:<18} {paint('✗', C.RED)} {res.error}")

    started = _time.time()
    results = unpack_package(
        Path(args.package),
        out_dir,
        partitions=args.partitions,
        progress_cb=on_progress if not args.quiet else None,
        result_cb=on_result,
    )
    elapsed = _time.time() - started

    index = write_index(results, out_dir)
    ok = [r for r in results if r.ok]
    bad = [r for r in results if not r.ok]

    print()
    print(f"  索引: {index}")
    print(f"  耗时: {elapsed:.1f} 秒")
    print()

    if bad:
        print(paint(f"✗ {len(bad)} 个分区解包失败", C.RED + C.BOLD))
        for r in bad:
            print(f"  {r.partition}: {r.error}")
        return 1

    total_gb = sum(r.size for r in ok) / 2**30
    print(paint(f"✓ 解包完成：{len(ok)} 个分区，{total_gb:.2f} GB", C.GREEN + C.BOLD))
    return 0


def cmd_flash(args: argparse.Namespace) -> int:
    """执行刷写。"""
    from .core.flash import (
        FlashExecutor,
        StepStatus,
        dry_run_report,
        preflight,
        summarize,
    )
    from .core.package import Severity, Verdict, check_device_match, inspect_package
    from .core.plan import Phase, build_plan
    from .device.discover import DeviceMode, detect, list_partitions
    from .device.runner import AdbRunner, FastbootRunner

    dry_run = args.dry_run

    # ---- 1) 包校验 ----
    info = inspect_package(args.package)
    if info.verdict is not Verdict.FLASHABLE or info.manifest is None:
        print(paint(f"✗ 该包不能线刷: {info.verdict.value}", C.RED + C.BOLD))
        return 1

    image_dir = Path(args.images)
    if not image_dir.exists():
        print(paint(f"✗ 镜像目录不存在: {image_dir}", C.RED))
        print(f"  请先运行: lrom unpack {args.package} -o {image_dir}")
        return 1

    # ---- 2) 设备探测 ----
    dev = detect(adb_server_port=args.adb_port)
    if not dev:
        print(paint("✗ 未检测到设备", C.RED))
        return 1

    # ---- 2.3) ARB 熔断机型检查 ----
    # 这是最后一道兜底：用户可能没跑 `lrom arb` 就直接 flash。
    # 硬编码的熔断表能在这种情况下拦住「降级到熔断前版本」这个
    # 最常见的黑砖场景。
    from .core.arb_db import ArbRisk, check_arb_risk, format_arb_check

    arb_result = check_arb_risk(
        package_version=info.version_name,
        device_version=dev.build_id,
        model_code=info.product_name,
        codename=dev.codename or info.pre_device,
    )
    if arb_result.risk is ArbRisk.BLOCK:
        print(paint("✗ ARB 熔断保护：拒绝刷写", C.RED + C.BOLD))
        print()
        print(format_arb_check(arb_result))
        print()
        print("  为什么必须停手：")
        print("    · 这些机型的 ARB 已从 0 升到 1，降级刷入会直接黑砖")
        print("    · 同时免授权 9008 已失效，没有自救通道")
        print("    · 只能拆字库或换主板")
        print()
        print("  想确认具体数值，可以先跑（只读，不写设备）：")
        print(paint("    ./lrom arb --images <镜像目录>", C.CYAN))
        print()
        return 1
    if arb_result.risk is ArbRisk.WARN:
        print(paint("  !! ARB 熔断风险警告", C.YELLOW + C.BOLD))
        print(f"  {format_arb_check(arb_result)}")
        print()
        if not args.yes:
            from .tui.menu import confirm

            if not confirm("仍然继续刷写？"):
                print("  已取消。")
                return 1

    # ---- 2.5) Bootloader 锁检查（必须在任何写操作之前）----
    # 为什么必须查：锁定的设备刷写会失败在中途，或者只写进去一半，
    # 比「提前拒绝」危险得多。另外用户在「临时 root」状态下
    # （比如从 fastboot 临时启动而非持久刷入）容易误判自己已解锁。
    if dev.locked is None:
        print(
            paint(
                "  !! 无法确认 Bootloader 是否已解锁，继续但请自行确认。",
                C.YELLOW,
            )
        )
    elif str(dev.locked) != "0":
        print(paint("✗ Bootloader 未解锁，拒绝刷写", C.RED + C.BOLD))
        print()
        print("  设备当前处于已锁定状态，无法写入任何分区。")
        print()
        print("  可能的原因：")
        print("    · 从未执行过 OEM 解锁（开发者选项 → OEM 解锁）")
        print("    · 执行过但未完成 fastboot oem unlock 流程")
        print("    · 使用了「临时 root」（fastboot boot）但未持久化，")
        print("      这种状态下重启后锁会恢复，并不是真正的解锁")
        print()
        print("  请先在设备上完成解锁，再重试本命令。")
        print("  注意：解锁会清空全部用户数据。")
        print()
        return 1
    else:
        print(paint("  ✓ Bootloader 已解锁", C.GREEN))

    device_parts: set[str] = set()
    if dev.mode is DeviceMode.ADB:
        findings = check_device_match(info, dev.codename or "", dev.model)
        blockers = [f for f in findings if f.severity is Severity.BLOCKER]
        if blockers:
            print(paint("✗ 型号不匹配，拒绝刷写", C.RED + C.BOLD))
            for f in blockers:
                print(f"  {f.message}")
            return 1
        adb = AdbRunner(server_port=args.adb_port)
        device_parts = list_partitions(adb, dev.serial)
    else:
        # fastboot 模式下无法读取 ro.product.*，型号校验以包内 pre-device
        # 与 getvar product 的宽松比对为准，不一致时警告但不阻止
        if info.pre_device and dev.codename and info.pre_device != dev.codename:
            print(
                paint(
                    f"  !! fastboot 下代号为 {dev.codename}，包要求 {info.pre_device}"
                    f"（部分平台此处返回平台名，属正常）",
                    C.YELLOW,
                )
            )

    # ---- 3) 计划 ----
    from .device.runner import FastbootRunner as _FB

    fb_early = _FB()
    plan = build_plan(
        info.manifest,
        package_path=str(info.path),
        device_model=dev.model,
        device_codename=dev.codename,
        slot=dev.slot,
        device_partitions=device_parts or None,
        allow_dangerous=args.allow_dangerous,
        include=args.partitions,
    )

    # ---- 4) 前置检查 ----
    # 读取设备 super 分区大小，用于空间校验
    super_size: int | None = None
    try:
        from .device.runner import FastbootRunner as _FBR

        if dev.mode is DeviceMode.ADB:
            res = AdbRunner(server_port=args.adb_port).shell(
                "su -c 'blockdev --getsize64 /dev/block/by-name/super'", timeout=15
            )
            if res.exit_code == 0 and res.stdout.strip().isdigit():
                super_size = int(res.stdout.strip())
        if super_size is None:
            v = _FBR().getvar("partition-size:super")
            if v and v.startswith("0x"):
                super_size = int(v, 16)
    except Exception:
        pass

    issues = preflight(plan, image_dir, device_parts or None, super_size)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        print(paint("✗ 前置检查未通过", C.RED + C.BOLD))
        for i in errors:
            print(f"  {i.message}")
        return 1

    # ---- 4.5) ARB 熔断检测（防止降级硬砖）----
    from .core.arb import (
        ArbVerdict,
        compare_arb,
        parse_xbl_config,
        read_device_arb_adb,
        read_device_arb_fastboot,
    )

    pkg_arb = None
    for cand in (image_dir / "xbl_config.img", image_dir / "xbl_config_a.img"):
        if cand.exists():
            try:
                pkg_arb = parse_xbl_config(cand)
                break
            except Exception:
                pass

    dev_arb = None
    try:
        if dev.mode is DeviceMode.ADB:
            dev_arb = read_device_arb_adb(
                AdbRunner(server_port=args.adb_port), dev.serial
            )
        if dev_arb is None and dev.mode is not DeviceMode.ADB:
            # 仅在已在 fastboot 时才尝试 fetch，避免在系统模式下长时间等待
            dev_arb = read_device_arb_fastboot(fb_early)
    except Exception:
        pass

    arb_cmp = compare_arb(pkg_arb, dev_arb)
    if arb_cmp.verdict is ArbVerdict.BRICK and not getattr(args, "force_arb", False):
        print()
        print(paint("✗ ARB 熔断检测未通过：此包会导致硬砖", C.RED + C.BOLD))
        print(f"  包内 ARB      : {pkg_arb.arb_index if pkg_arb else '?'}")
        print(f"  设备已熔断 ARB: {dev_arb.arb_index if dev_arb else '?'}")
        print()
        print("  这是硬件级不可恢复操作，Lrom 已阻止。")
        print("  如果确认要继续，请使用 --force-arb（极度危险）")
        return 1

    # ---- 5) 展示计划 ----
    fb_ops = plan.by_phase(Phase.FASTBOOT)
    fbd_ops = plan.by_phase(Phase.FASTBOOTD)
    print()
    print(paint("╭─ 刷写计划 " + "─" * 46, C.BOLD))
    print(f"│ 包     : {info.path.name}")
    print(f"│ 设备   : {dev.model} ({dev.codename})  槽位: _{dev.slot}")
    print(f"│ 阶段一 : {paint(f'Fastboot  {len(fb_ops)} 个分区', C.CYAN)}")
    print(f"│ 阶段二 : {paint(f'FastbootD {len(fbd_ops)} 个分区', C.CYAN)}")
    print(f"│ 总量   : {plan.total_bytes / 2**30:.2f} GB")
    if getattr(args, "wipe", False):
        print(f"│ 数据   : {paint('将清空（恢复出厂设置）', C.RED)}")
    else:
        print(f"│ 数据   : 保留")
    arb_color = {
        ArbVerdict.SAFE: C.GREEN,
        ArbVerdict.UPGRADE: C.GREEN,
        ArbVerdict.UNKNOWN: C.YELLOW,
    }.get(arb_cmp.verdict, C.YELLOW)
    print(f"│ ARB    : {paint(arb_cmp.verdict.value, arb_color)}")
    if getattr(args, "both_slots", False):
        print(f"│ 槽位   : {paint('A + B 双槽刷写', C.CYAN)}")
    else:
        print(f"│ 槽位   : 仅 _{dev.slot or 'a'}")
    if not getattr(args, "no_recreate_logical", False):
        print(f"│ 逻辑分区: 写入前会 delete + create（清理 COW 层）")
    print(paint("╰" + "─" * 56, C.BOLD))

    for i in issues:
        if i.severity == "warn":
            print(f"  {paint('!!', C.YELLOW)} {i.message}")

    # ---- 6) 确认 ----
    if not dry_run and not args.yes:
        print()
        print(paint("⚠️  这将实际写入设备，可能变砖。", C.RED + C.BOLD))
        if getattr(args, "wipe", False):
            print(paint("    数据策略：将清空所有用户数据！", C.RED + C.BOLD))
        else:
            print("    数据策略：保留用户数据（升级模式）")
            print(
                paint(
                    "    注意：如果这是降级包，保留数据会无法开机，"
                    "降级必须加 --wipe",
                    C.YELLOW,
                )
            )
        print("    请确认：电量 > 50%、数据线稳定、不要中途拔线。")
        print()
        try:
            answer = input(f"  输入 {paint('FLASH', C.BOLD)} 确认开始，其他任何输入取消: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 130
        if answer.strip() != "FLASH":
            print("已取消")
            return 130

    # ---- 7) 执行 ----
    fb = FastbootRunner()
    adb = AdbRunner(server_port=args.adb_port)
    mode_printed = {"v": None}

    def on_mode_change(phase) -> None:
        from .core.plan import Phase as _P

        if mode_printed["v"] == phase:
            return
        if mode_printed["v"] is not None:
            print()
        mode_printed["v"] = phase
        label = {
            _P.FASTBOOT: "阶段一：Fastboot（bootloader）",
            _P.FASTBOOTD: "阶段二：FastbootD（用户空间）",
        }.get(phase, str(phase))
        print(paint(f"── {label} ──", C.BOLD))

    executor = FlashExecutor(
        plan, image_dir, fb=fb, adb=adb, dry_run=dry_run,
        on_mode_change=on_mode_change,
        wipe_data=getattr(args, "wipe", False),
        both_slots=getattr(args, "both_slots", False),
        recreate_logical=not getattr(args, "no_recreate_logical", False),
        on_notice=lambda msg: print(paint(f"  {msg}", C.YELLOW)),
    )

    # ---- 刷写进行中的实时提示（防止用户误以为卡死而拔线）----
    _spin = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    _state = {"i": 0}

    def _on_pending(partition: str, size: int, start: float) -> None:
        import time as _t

        elapsed = _t.time() - start
        spin = _spin[_state["i"] % len(_spin)]
        _state["i"] += 1
        pct = ""
        # 按经验速度估算剩余时间（约 40 MB/s）
        if elapsed > 1:
            speed = size / elapsed
            remain = max(0, (size - speed * elapsed) / speed) if speed > 0 else 0
            pct = f"  剩余约 {remain:.0f}s"
        line = f"\r  {spin} {partition:<16} 刷写中… {size/1e6:>8,.1f} MB  {elapsed:>5.1f}s{pct}   "
        print(line, end="", flush=True)

    def _on_done() -> None:
        print("\r" + " " * 90 + "\r", end="", flush=True)

    executor.on_progress_pending = _on_pending
    executor.on_progress_done = _on_done

    # ---- 预演：只出报告，不逐条模拟 ----
    if dry_run:
        print()
        print(paint("╭─ 预演报告 " + "─" * 44, C.BOLD))
        report = dry_run_report(plan, image_dir)
        for line in report.splitlines():
            print(f"│ {line}")
        print(paint("╰" + "─" * 56, C.BOLD))
        print()
        print(paint("预演不会写入任何数据。确认无误后去掉 --dry-run 再执行。", C.DIM))
        print()
        return 0

    print()
    print(paint("[刷写] 开始", C.GREEN))
    print()

    total_bytes = plan.total_bytes
    done_bytes = 0


    def on_step(sr) -> None:
        nonlocal done_bytes
        if sr.status is StepStatus.OK:
            done_bytes += sr.size
            pct = done_bytes / total_bytes * 100 if total_bytes else 0
            print(
                f"  {sr.partition:<18}{paint('✓', C.GREEN)} "
                f"{sr.size_mb:>9,.1f} MB  {sr.duration:>6.1f}s  [{pct:5.1f}%]"
            )
        elif sr.status is StepStatus.SKIPPED:
            print(f"  {sr.partition:<18}{paint('·', C.DIM)} {sr.message}")
        elif sr.status is StepStatus.LOCKED:
            print(f"  {sr.partition:<18}{paint('🔒', C.YELLOW)} 关键分区未解锁，跳过")
            done_bytes += sr.size
        else:
            print(f"  {sr.partition:<18}{paint('✗', C.RED)} {sr.message}")

    executor.on_step = on_step

    try:
        result = executor.run()
    except KeyboardInterrupt:
        print(paint("\n\n⚠️  用户中断", C.YELLOW))
        return 130

    # ---- 8) 汇总 ----
    print()
    print(paint("╭─ 结果 " + "─" * 48, C.BOLD))
    for line in summarize(result).splitlines():
        print(f"│ {line}")
    print(paint("╰" + "─" * 56, C.BOLD))
    print()

    if dry_run:
        print(paint("✓ 预演完成，未写入设备", C.GREEN + C.BOLD))
        return 0

    if result.ok:
        print(paint("✓ 刷写完成", C.GREEN + C.BOLD))
        print()
        _print_after_flash_notice(arb_cmp, wipe=getattr(args, "wipe", False))
        return 0

    print(paint("✗ 刷写失败", C.RED + C.BOLD))
    print()
    print("恢复建议:")
    print("  · 设备应仍在 fastboot/fastbootd，可重新运行本命令")
    print("  · 若无法进入系统，用备份恢复:")
    print("      lrom restore backups/<机型>_<时间>/")
    return 1


def _print_after_flash_notice(arb_cmp, *, wipe: bool) -> None:
    """刷写完成后的提示。强调：不自动重启、降级必须格式化。

    Lrom 刻意不自动重启设备 —— 重启决策权必须留给用户，
    尤其是在降级场景下，先重启会绕过格式化步骤导致无法开机。
    """
    from .core.arb import ArbVerdict

    print(paint("╭─ 后续操作（请务必阅读）" + "─" * 24, C.YELLOW + C.BOLD))

    is_downgrade_risk = arb_cmp is not None and arb_cmp.verdict in (
        ArbVerdict.SAFE,
        ArbVerdict.BRICK,
        ArbVerdict.UNKNOWN,
    )

    if not wipe:
        print("│")
        print(paint("│ ⚠️  若是降级（刷入的系统版本低于原版本）：", C.RED + C.BOLD))
        print(paint("│    必须先格式化系统分区（清除用户数据），", C.RED))
        print(paint("│    否则会无法开机甚至变砖！", C.RED + C.BOLD))
        print("│")
        print("│    正确做法（二选一）：")
        print("│      1) 在 Fastboot 下执行:")
        print("│           fastboot -w")
        print("│      2) 或进入 Recovery，选择「格式化 data / 恢复出厂设置」")
        print("│")
        print("│    本次刷写保留了用户数据（升级场景）。")

    if arb_cmp is not None and arb_cmp.verdict is ArbVerdict.UPGRADE:
        print("│")
        print(paint("│ 注意：本次刷写已提升 ARB 索引，此后再无法降级到更低版本。", C.YELLOW))

    print("│")
    print(paint("│ Lrom 不会自动重启设备。", C.BOLD))
    print("│ 请确认以上事项后再手动重启：")
    print("│    fastboot reboot          # 当前在 fastboot")
    print("│    或长按电源键 10 秒")
    print("│")
    print(paint("╰" + "─" * 44, C.YELLOW + C.BOLD))
    print()


def cmd_arb(args: argparse.Namespace) -> int:
    """ARB 熔断检测：判断降级是否会变砖。"""
    from .core.arb import (
        ArbVerdict,
        compare_arb,
        parse_xbl_config,
        ArbError,
        read_device_arb_adb,
        read_device_arb_fastboot,
    )
    from .device.discover import DeviceMode, detect
    from .device.runner import AdbRunner, FastbootRunner

    # ---- 包的 ARB ----
    pkg_arb = None
    img_dir = Path(args.images)
    candidates = [img_dir / "xbl_config.img", img_dir / "xbl_config_a.img"]
    if args.xbl_config:
        candidates.insert(0, Path(args.xbl_config))

    for cand in candidates:
        if cand.exists():
            try:
                pkg_arb = parse_xbl_config(cand)
                pkg_arb.source = str(cand)
                break
            except ArbError as exc:
                print(f"  {paint('!!', C.YELLOW)} 解析 {cand.name} 失败: {exc}")

    # ---- 设备的 ARB ----
    dev_arb = None
    dev_note = None
    dev = detect(adb_server_port=args.adb_port)
    if dev:
        if dev.mode is DeviceMode.ADB:
            adb = AdbRunner(server_port=args.adb_port)
            dev_arb = read_device_arb_adb(adb, dev.serial)
            if dev_arb is None:
                dev_note = (
                    "设备在 ADB 模式但无法读取 ARB（需要 root）。\n"
                    "       把设备切到 fastboot 模式可尝试用 fetch 读取，\n"
                    "       但部分一加机型（含平板 Pro）的 bootloader 不支持 fetch。"
                )
        elif dev.mode in (DeviceMode.FASTBOOT, DeviceMode.FASTBOOTD):
            dev_arb = read_device_arb_fastboot(
                FastbootRunner(),
                on_notice=lambda m: print(f"  {paint('·', C.DIM)} {m}"),
            )
    else:
        dev_note = "未连接设备。只能参考包侧 ARB 判断。"

    print()
    print(paint("╭─ ARB 熔断检测 " + "─" * 42, C.BOLD))
    if pkg_arb:
        print(f"│ 包内 xbl_config : {paint(pkg_arb.describe(), C.CYAN)}")
    else:
        print(f"│ 包内 xbl_config : {paint('未能读取', C.YELLOW)}")
    if dev_arb:
        print(f"│ 设备当前        : {paint(dev_arb.describe(), C.CYAN)}   [{dev_arb.source}]")
    else:
        print(f"│ 设备当前        : {paint('未能读取', C.YELLOW)}")
    print(paint("╰" + "─" * 56, C.BOLD))
    print()

    cmp = compare_arb(pkg_arb, dev_arb)
    colors = {
        ArbVerdict.SAFE: C.GREEN,
        ArbVerdict.UPGRADE: C.GREEN,
        ArbVerdict.BRICK: C.RED,
        ArbVerdict.UNKNOWN: C.YELLOW,
    }
    print(paint(f"  结论: {cmp.verdict.value}", colors[cmp.verdict] + C.BOLD))
    print(f"  {cmp.message}")
    print()

    if cmp.verdict is ArbVerdict.UNKNOWN:
        if dev_note:
            print(f"  原因：{dev_note}")
            print()
        print("  ⚠ 无法确认设备侧 ARB 时，请勿降级到更低的系统版本。")
        print()
        if pkg_arb is not None:
            if pkg_arb.arb_index >= 1:
                print(
                    paint(
                        f"  注意：本包 xbl_config 的 ARB = {pkg_arb.arb_index}。",
                        C.YELLOW + C.BOLD,
                    )
                )
                print("        ARB ≥ 1 表示该版本已引入防回滚熔断 ——")
                print("        一旦刷入，将无法再降级到 ARB 更低的版本（不可逆）。")
                print("        如果还没刷，请先在社区确认目标版本是否已熔断。")
            else:
                print(
                    paint(
                        f"  本包 xbl_config 的 ARB = {pkg_arb.arb_index}，"
                        "降级不会触发熔断。",
                        C.GREEN,
                    )
                )
        print()

    return 0 if cmp.verdict is not ArbVerdict.BRICK else 1


def cmd_selfcheck(args: argparse.Namespace) -> int:
    """环境自检。"""
    from .device.selfcheck import format_selfcheck, run_selfcheck

    print()
    print(paint("╭─ 环境自检 " + "─" * 45, C.BOLD))
    print()
    items = run_selfcheck()
    print(format_selfcheck(items))
    print()

    from .device.selfcheck import Level

    return 1 if any(i.level is Level.ERROR for i in items) else 0


def _find_stock_image(explicit: Path | None) -> Path | None:
    """定位原厂 init_boot.img。

    优先级：
      1. 用户显式指定
      2. unpacked/rom/init_boot.img（全量包解出的，版本最可靠）
      3. unpacked/rom/boot.img（老设备，但会因无 ramdisk 被拦下）
    """
    if explicit:
        return Path(explicit)
    for cand in ("unpacked/rom/init_boot.img", "unpacked/rom/boot.img"):
        p = Path(cand)
        if p.is_file():
            return p
    return None


def cmd_root(args: argparse.Namespace) -> int:
    """自动化 root。"""
    from .core import root as rt
    from .device.discover import DeviceMode, detect

    print()
    print(paint("╭─ 自动化 Root " + "─" * 46, C.BOLD))
    print()

    # --- 定位镜像 ---
    image = _find_stock_image(args.image)
    if image is None:
        print(paint("  ⛔ 找不到原厂镜像。", C.RED))
        print()
        print("  请先从全量 ROM 包解出 init_boot：")
        print(f"    {paint('./lrom unpack <ROM包> --partitions init_boot', C.CYAN)}")
        print("  或用 -i 指定 init_boot.img 路径。")
        print()
        return 1

    # --- 设备状态 ---
    device = detect(adb_server_port=args.adb_port)
    if device is None:
        print(paint("  ⛔ 未检测到设备。", C.RED))
        print("  请先连接设备并确认能识别（./lrom devices）。")
        print()
        return 1

    if device.mode is DeviceMode.FASTBOOT or device.mode is DeviceMode.FASTBOOTD:
        print(paint("  ⚠ 设备当前在 fastboot 模式。", C.YELLOW))
        print("  root 流程需要先读取设备信息，请开机启动后再运行。")
        print()
        return 1

    if device.locked is None:
        print(
            paint(
                "  ⚠ 无法确认 Bootloader 是否已解锁，请自行确认。",
                C.YELLOW,
            )
        )
        print()
    elif str(device.locked) != "0":
        print(paint("  ⛔ Bootloader 未解锁，无法刷写。", C.RED))
        print()
        print("  可能的原因：")
        print("    · 从未执行过 OEM 解锁（开发者选项 → OEM 解锁）")
        print("    · 使用了「临时 root」（fastboot boot）但未持久化")
        print()
        print("  注意：解锁会清空全部用户数据。")
        print()
        return 1

    # --- 选择刷入模式（由使用者选择，不替用户判断）---
    flash_mode = _choose_flash_mode(args.flash_mode)

    # --- 生成计划 ---
    method = rt.RootMethod(args.method)
    plan = rt.build_root_plan(
        image=image,
        method=method,
        partition=args.partition,
        device=device,
        flash_mode=flash_mode,
    )
    if args.kmi:
        plan.kmi = args.kmi

    print(plan.describe())

    if not plan.can_proceed:
        print(paint("  存在阻止执行的问题，已停止。", C.RED))
        print()
        return 1

    if args.dry_run:
        print(paint("  这是预演（--dry-run），未做任何修改。", C.YELLOW))
        print()
        return 0

    # --- 确认 ---
    if not args.yes:
        from .tui.menu import confirm

        if not confirm(
            f"确认 patch 并刷入 {plan.partition}？（原厂镜像会先备份）"
        ):
            print("\n  已取消。\n")
            return 0

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # --- 步骤 1：先留存原厂底图（回滚基础，必须在任何写操作之前）---
    print()
    print(paint("  [1/5] 留存原厂镜像…", C.CYAN))
    try:
        stock_copy = rt.save_stock_image(
            image, workdir=workdir, partition=plan.partition
        )
    except OSError as exc:
        print(paint(f"  ⛔ 保存原厂镜像失败：{exc}", C.RED))
        print("  为安全起见已中止（没有底图就不刷写）。")
        print()
        return 1
    print(f"        {stock_copy}")
    print(f"        sha256 {plan.stock_sha256[:32]}…")

    # --- 步骤 2：patch ---
    print()
    patched: Path | None = None

    if method is rt.RootMethod.KSUD:
        print(paint("  [2/5] 用 ksud patch 镜像…", C.CYAN))
        out = workdir / f"{plan.partition}-patched-{time_str()}.img"
        result = rt.patch_with_ksud(
            image=image,
            output=out,
            ksud=plan.ksud_path,
            kmi=plan.kmi,
            on_line=lambda s: None,
        )
        if not result.ok:
            print(paint(f"  ⛔ {result.message}", C.RED))
            print()
            return 1
        patched = result.output
        print(f"        产出 {patched}")
    else:
        from .device.runner import AdbRunner

        print(paint("  [2/5] 推送镜像到设备…", C.CYAN))
        adb = AdbRunner(server_port=args.adb_port)
        try:
            remote = rt.push_for_app_patch(image=image, adb=adb)
        except rt.RootError as exc:
            print(paint(f"  ⛔ {exc}", C.RED))
            print()
            return 1
        print(f"        已推送到 {remote}")
        print()
        print(paint("  请现在在手机上操作：", C.BOLD))
        print("    1. 安装 KernelSU Manager（或 Magisk）APK")
        print(f"    2. 打开后选择「安装 / Install」→「选择文件 / Select a file」")
        print(f"    3. 选中 {remote}")
        print("    4. 等待修补完成，确认已导出到 Download 目录")
        print()
        try:
            input(paint("  修补完成后按回车继续…", C.DIM))
        except (EOFError, KeyboardInterrupt):
            print("\n  已取消。\n")
            return 130

        print(paint("  [2b/5] 拉回修补后的镜像…", C.CYAN))
        try:
            patched = rt.pull_app_patched(dest_dir=workdir, adb=adb)
        except rt.RootError as exc:
            print(paint(f"  ⛔ {exc}", C.RED))
            print()
            return 1
        if patched is None:
            print(paint("  ⛔ 没在设备上找到修补后的镜像。", C.RED))
            print("  请确认 Manager 已导出 .img 到 Download 目录。")
            print()
            return 1
        print(f"        已拉回 {patched}")

    # --- 步骤 3：校验 ---
    print()
    print(paint("  [3/5] 校验 patch 结果…", C.CYAN))
    ok, problems = rt.verify_patched_image(
        stock=image, patched=patched, partition_size=plan.partition_size
    )
    if not ok:
        print(paint("  ⛔ 校验未通过，已中止（不会刷入）：", C.RED))
        for p in problems:
            print(f"        - {p}")
        print()
        return 1
    print("        ✓ 大小、header、ramdisk、AVB footer 均正常")
    print(f"        sha256 {rt._sha256(patched)[:32]}…")

    # --- 步骤 4：刷写 ---
    print()
    print(paint("  [4/5] 刷入…", C.CYAN))
    if plan.flash_mode.value == "fastboot":
        print(paint("        会先把设备切到 Fastboot 模式", C.DIM))
    else:
        print(paint("        会先把设备切到 FastbootD 模式", C.DIM))
    ok, detail = rt.flash_patched_image(
        image=patched,
        partition=plan.partition,
        partition_size=plan.partition_size,
        flash_mode=plan.flash_mode,
        on_notice=lambda msg: print(paint(f"        {msg}", C.YELLOW)),
    )
    if not ok:
        print(paint("  ⛔ 刷写失败：", C.RED))
        print(f"    {detail}")
        print()
        print(f"  原厂镜像已保存在：")
        print(f"    {stock_copy}")
        print()
        print("  可以这样恢复：")
        print(
            paint(
                f"    fastboot flash {plan.partition} {stock_copy}",
                C.CYAN,
            )
        )
        print()
        return 1
    print("        ✓ 刷写完成")

    # --- 步骤 5：收尾（永不自动重启）---
    print()
    print(paint("  [5/5] 完成", C.GREEN + C.BOLD))
    print()
    print("  请手动重启设备：")
    print(paint("    fastboot reboot", C.CYAN))
    print()
    print("  重启后验证 root：")
    print(paint("    adb shell su -c id", C.CYAN))
    print()
    print("  出问题可随时用原厂镜像恢复：")
    print(paint(f"    fastboot flash {plan.partition} {stock_copy}", C.CYAN))
    print()
    return 0


def _choose_flash_mode(explicit: str | None):
    """决定刷入模式。未显式指定时交互询问。"""
    from .core.root import FlashMode
    from .tui.menu import ask

    if explicit == "fastboot":
        return FlashMode.FASTBOOT
    if explicit == "fastbootd":
        return FlashMode.FASTBOOTD

    print()
    print(paint("  选择刷入模式：", C.BOLD))
    print("    [1] Fastboot  （bootloader 模式，常规，默认）")
    print("    [2] FastbootD （用户空间模式）")
    print()
    print(paint("        提示：如果设备的 OCDT 分区被抹掉，bootloader 会进不去，", C.DIM))
    print(paint("              此时只能选 FastbootD（它由系统里的 fastbootd 服务启动，", C.DIM))
    print(paint("              不依赖 OCDT）。", C.DIM))
    print()
    choice = ask("请选择", "1")
    print()
    return FlashMode.FASTBOOTD if choice.strip() == "2" else FlashMode.FASTBOOT


def time_str() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")



def cmd_fetch_manager(args: argparse.Namespace) -> int:
    """下载 root 管理器 APK —— Lrom 唯一的联网命令。"""
    from .core import manager_fetch as mf

    print()
    print(paint("╭─ 下载 root 管理器 " + "─" * 40, C.BOLD))
    print()

    # 联网协议公示（每次都要打，不能只写在文档里）
    print(paint("  这是 Lrom 唯一会联网的功能。承诺如下：", C.BOLD))
    print("    · 只访问 api.github.com / github.com / objects.githubusercontent.com")
    print("    · 只下载文件，绝不执行任何远程代码或脚本")
    print("    · 下载后强制校验 sha256，不匹配立即丢弃")
    print("    · 不发送任何设备信息、序列号或统计（零遥测）")
    print()

    try:
        source = mf.resolve_manager_source(args.source)
    except mf.FetchError as exc:
        print(paint(f"  ⛔ {exc}", C.RED))
        print()
        return 1

    channel_label = "正式版" if args.channel == "stable" else "测试版"
    print(f"  来源  : {source.label}  ({source.repo})")
    print(f"  渠道  : {channel_label}")
    print()

    print(paint("  正在查询最新版本…", C.CYAN))
    try:
        release = mf.fetch_latest_release(source, channel=args.channel)
    except mf.FetchError as exc:
        print(paint(f"  ⛔ {exc}", C.RED))
        print()
        return 1

    tag_line = release.tag
    if release.prerelease:
        tag_line += paint("  (预发布)", C.YELLOW)
    print(f"  最新版本: {tag_line}")
    if release.published_at:
        print(f"  发布时间: {release.published_at}")
    print()

    try:
        if args.asset:
            asset = next(
                (a for a in release.assets if a.name == args.asset), None
            )
            if asset is None:
                names = ", ".join(a.name for a in release.assets)
                raise mf.FetchError(
                    f"Release 里没有 {args.asset!r}。可用：{names}"
                )
        else:
            asset = mf.pick_asset(release, source)
    except mf.FetchError as exc:
        print(paint(f"  ⛔ {exc}", C.RED))
        print()
        return 1

    digest = asset.sha256
    print(f"  文件  : {asset.name}")
    print(f"  大小  : {asset.size:,} 字节 ({asset.size / 1024 / 1024:.1f} MB)")
    print(f"  sha256: {digest or paint('（未提供，将拒绝下载）', C.RED)}")
    print()

    if not args.yes:
        from .tui.menu import confirm

        if not confirm("确认下载？"):
            print("\n  已取消。\n")
            return 0

    dest = Path(args.dest)
    print()
    print(paint(f"  下载到 {dest}/ …", C.CYAN))

    last = {"pct": -1}

    def on_progress(done: int, total: int) -> None:
        if total <= 0:
            return
        pct = int(done * 100 / total)
        if pct != last["pct"]:
            last["pct"] = pct
            print(f"\r        {pct:3d}%  ({done:,}/{total:,})", end="", flush=True)

    try:
        path = mf.download_asset(
            asset, dest, expected_sha256=digest, on_progress=on_progress
        )
    except mf.FetchError as exc:
        print()
        print(paint(f"  ⛔ {exc}", C.RED))
        print()
        return 1

    print("\r" + " " * 50 + "\r", end="")
    print(paint("  ✓ 下载完成，校验通过", C.GREEN))
    print()
    print(f"  {path}")
    print()
    print("  安装到设备：")
    print(paint(f"    adb install -r {path}", C.CYAN))
    print()
    print("  或用 APP 方式跑 root（会自动推开安装提示）：")
    print(paint(f"    ./lrom root --method app", C.CYAN))
    print()
    return 0
