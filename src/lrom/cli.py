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
        _check_against_device(info)

    return 0 if info.verdict is Verdict.FLASHABLE else 1


def _check_against_device(info: PackageInfo) -> None:
    from .device.discover import DeviceMode, detect

    print(paint("  设备核对", C.BOLD))
    dev = detect()
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
    pi.set_defaults(func=cmd_inspect)

    pd = sub.add_parser("devices", help="检测连接的设备")
    pd.set_defaults(func=cmd_devices)

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
