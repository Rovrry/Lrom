"""Lrom 命令行入口。"""

from __future__ import annotations

import argparse
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

    for part in parts:
        print(f"  {part:<16}", end="", flush=True)
        result = bk.backup_partition_adb(
            adb, part, out_dir / f"{part}.img", slot=dev.slot
        )
        session.results.append(result)
        if result.ok:
            print(paint(f"✓ {result.size_mb:>8,.1f} MB", C.GREEN))
        else:
            print(paint(f"✗ {result.error}", C.RED))

    manifest = bk.write_manifest(session)
    print()
    print(f"  清单: {manifest}")

    if session.ok:
        print(paint(f"\n✓ 备份完成（{len(session.results)} 个分区）", C.GREEN + C.BOLD))
        return 0

    failed = [r.partition for r in session.results if not r.ok]
    print(paint(f"\n✗ 部分备份失败: {', '.join(failed)}", C.RED + C.BOLD))
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
    issues = preflight(plan, image_dir, device_parts or None)
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

    print()
    mode = paint("[预演]", C.YELLOW) if dry_run else paint("[刷写]", C.GREEN)
    print(f"{mode} 开始")
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
    dev = detect(adb_server_port=args.adb_port)
    if dev:
        if dev.mode is DeviceMode.ADB:
            adb = AdbRunner(server_port=args.adb_port)
            dev_arb = read_device_arb_adb(adb, dev.serial)
        elif dev.mode in (DeviceMode.FASTBOOT, DeviceMode.FASTBOOTD):
            dev_arb = read_device_arb_fastboot(FastbootRunner())
    else:
        # 没有设备也可能在 fastboot
        try:
            dev_arb = read_device_arb_fastboot(FastbootRunner())
        except Exception:
            pass

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
        print("  提示：设备侧 ARB 需要在 fastboot 模式下回读，或设备已 root。")
        print("        无法确认时，请勿降级到更低的系统版本。")
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
