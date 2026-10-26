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
