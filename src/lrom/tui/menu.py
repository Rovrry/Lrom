"""交互式主菜单。

设计目标：不熟悉命令行的用户也能完成完整的刷机流程。

风格参考用户习惯（1.解全量包 2.线刷全量包），但每一项都对应
一个可脚本化的子命令，便于进阶用户与自动化。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import __version__


# ---------------------------------------------------------------------------
# 终端输出
# ---------------------------------------------------------------------------

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


def _color(s: str, c: str) -> str:
    return f"{c}{s}{C.RESET}" if sys.stdout.isatty() else s


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

class Session:
    """菜单会话状态，记住用户选过的包与目录。"""

    def __init__(self) -> None:
        from ..device.tools import load_config

        self.package: Path | None = None
        self.work_dir: Path = Path("work")
        self.device_desc: str = "检测中…"

        cfg = load_config()
        if wd := cfg.get("work_dir"):
            self.work_dir = Path(wd)
        last = cfg.get("last_package")
        if last and Path(last).exists():
            self.package = Path(last)

    @property
    def image_dir(self) -> Path | None:
        if not self.package:
            return None
        return self.work_dir / self.package.stem

    def save_state(self) -> None:
        from ..device.tools import load_config, save_config

        try:
            cfg = load_config()
            if self.package:
                cfg["last_package"] = str(self.package)
            cfg["work_dir"] = str(self.work_dir)
            save_config(cfg)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 输入辅助
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return val or default


def ask_path(prompt: str, default: str = "") -> Path | None:
    raw = ask(prompt, default)
    if not raw:
        return None
    raw = raw.strip().strip("'\"").replace("\\ ", " ")
    return Path(os.path.expanduser(raw))


def confirm(prompt: str, word: str | None = None) -> bool:
    """确认。word 非空时必须完整输入该词。"""
    if word:
        print()
        print(_color(f"  ⚠  {prompt}", C.RED + C.BOLD))
        print(f"  输入 {_color(word, C.BOLD)} 确认，其他任意输入取消: ", end="")
        try:
            return input().strip() == word
        except (EOFError, KeyboardInterrupt):
            print()
            return False
    return ask(f"{prompt} (y/N)", "n").lower().startswith("y")


def pause(msg: str = "按回车返回菜单…") -> None:
    try:
        input(f"\n  {_color(msg, C.DIM)}")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


# ---------------------------------------------------------------------------
# 头部
# ---------------------------------------------------------------------------

MENU = f"""
  {_color('─ 包处理 ──────────────────────────────────────', C.DIM)}
   {_color('1', C.CYAN)}) 解全量包          把 ROM 包解出分区镜像
   {_color('2', C.CYAN)}) 分析 ROM 包       判断能否线刷、查看分区
   {_color('3', C.CYAN)}) 选择 ROM 包       指定要操作的包

  {_color('─ 刷写 ────────────────────────────────────────', C.DIM)}
   {_color('4', C.CYAN)}) 预演检查          模拟刷写，不写设备
   {_color('5', C.CYAN)}) 线刷全量包        执行刷写（需确认）
   {_color('6', C.CYAN)}) 只刷指定分区      手动选择分区

  {_color('─ 安全 ────────────────────────────────────────', C.DIM)}
   {_color('7', C.CYAN)}) 备份设备分区      刷前备份（需 root）
   {_color('8', C.CYAN)}) ARB 熔断检测      防降级硬砖

  {_color('─ 工具 ────────────────────────────────────────', C.DIM)}
   {_color('9', C.CYAN)}) 设备信息          型号 / 版本 / 槽位
  {_color('10', C.CYAN)}) 重启设备          到 Fastboot / FastbootD / 系统
  {_color('11', C.CYAN)}) 环境自检          adb / 权限 / udev
  {_color('12', C.CYAN)}) 关于

   {_color('0', C.CYAN)}) 退出
"""


def print_header(sess: Session) -> None:
    os.system("clear")
    print()
    print(_color("  ┌" + "─" * 58 + "┐", C.MAGENTA))
    print(
        _color("  │ ", C.MAGENTA)
        + _color("Lrom", C.BOLD + C.MAGENTA)
        + "  ·  Linux Android 刷机工具"
        + _color(f"  v{__version__}", C.DIM)
    )
    print(_color("  └" + "─" * 58 + "┘", C.MAGENTA))
    print()

    pkg = _color(sess.package.name, C.CYAN) if sess.package else _color("未选择", C.DIM)
    print(f"    设备    : {sess.device_desc}")
    print(f"    ROM 包  : {pkg}")
    img_dir = sess.image_dir
    if img_dir and img_dir.exists():
        n = len(list(img_dir.glob("*.img")))
        if n:
            print(f"    已解包  : {_color(str(img_dir), C.CYAN)}  ({n} 个镜像)")
    print()


def refresh_device(sess: Session) -> None:
    from ..device.discover import detect

    try:
        dev = detect()
    except Exception:
        dev = None
    if not dev:
        sess.device_desc = _color("未检测到设备", C.DIM)
        return
    model = dev.model or dev.codename or "未知"
    sess.device_desc = (
        f"{_color('●', C.GREEN)} {model}  {dev.mode.value}"
        f"  槽位 _{dev.slot or '?'}"
    )


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def run_menu() -> int:
    sess = Session()
    refresh_device(sess)

    handlers = {
        "1": _menu_unpack,
        "2": _menu_inspect,
        "3": _menu_choose_package,
        "4": lambda s: _menu_flash(s, dry_run=True),
        "5": lambda s: _menu_flash(s, dry_run=False),
        "6": lambda s: _menu_flash(s, dry_run=False, pick=True),
        "7": _menu_backup,
        "8": _menu_arb,
        "9": _menu_device_info,
        "10": _menu_reboot,
        "11": _menu_selfcheck,
        "12": _menu_about,
    }

    while True:
        print_header(sess)
        print(MENU)
        try:
            choice = input(f"  {_color('请选择', C.BOLD)}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice == "0":
            print("  再见。\n")
            return 0

        handler = handlers.get(choice)
        if not handler:
            print(f"  {_color('无效选择', C.YELLOW)}")
            pause()
            continue

        try:
            handler(sess)
        except SystemExit:
            raise
        except KeyboardInterrupt:
            print(f"\n  {_color('已取消', C.YELLOW)}")
            pause()
        except Exception as exc:
            print(f"\n  {_color('出错了:', C.RED)} {exc}")
            pause()
        finally:
            # 每次操作后刷新设备状态
            try:
                refresh_device(sess)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 各菜单项
# ---------------------------------------------------------------------------

def _require_package(sess: Session) -> bool:
    if sess.package and sess.package.exists():
        return True
    print(f"\n  {_color('请先选择 ROM 包（菜单 3）', C.YELLOW)}")
    pause()
    return False


def _require_images(sess: Session) -> Path | None:
    d = sess.image_dir
    if d and d.exists() and any(d.glob("*.img")):
        return d
    print(f"\n  {_color('尚未解包，请先执行菜单 1（解全量包）', C.YELLOW)}")
    pause()
    return None


def _menu_choose_package(sess: Session) -> None:
    print()
    print("  请输入 ROM 包路径（.zip 文件，可直接把文件拖进终端）")
    p = ask_path("路径", str(sess.package) if sess.package else "")
    if not p:
        return
    if not p.exists():
        print(f"  {_color('文件不存在:', C.RED)} {p}")
        pause()
        return
    sess.package = p
    sess.save_state()
    print(f"  {_color('✓', C.GREEN)} 已选择: {p}")
    pause()


def _menu_inspect(sess: Session) -> None:
    from ..cli import print_info
    from ..core.package import inspect_package

    if not _require_package(sess):
        return
    print_info(inspect_package(sess.package))


def _menu_unpack(sess: Session) -> None:
    from ..core.package import Severity, Verdict, inspect_package
    from ..core.unpack import unpack_package, write_index

    if not _require_package(sess):
        return

    info = inspect_package(sess.package)
    if info.verdict is not Verdict.FLASHABLE:
        print(f"\n  {_color('此包不能解包:', C.RED + C.BOLD)} {info.verdict.value}")
        for f in info.findings:
            if f.severity in (Severity.BLOCKER, Severity.DANGER):
                print(f"    {f.message}")
        pause()
        return

    out = sess.image_dir
    assert out is not None
    print()
    print(f"  输出目录: {out}")
    if out.exists():
        n = len(list(out.glob("*.img")))
        if n and not confirm(f"目录已有 {n} 个镜像，覆盖吗？"):
            return

    print()
    counter = {"n": 0}

    def on_result(res):
        counter["n"] += 1
        mark = _color("✓", C.GREEN) if res.ok else _color("✗", C.RED)
        detail = f"{res.size_mb:>9,.1f} MB" if res.ok else res.error
        print(f"\r  {res.partition:<18} {mark} {detail:<34}")

    results = unpack_package(sess.package, out, result_cb=on_result)
    write_index(results, out)

    ok = sum(1 for r in results if r.ok)
    print()
    if ok == len(results) and results:
        print(f"  {_color(f'✓ 解包完成：{ok} 个分区', C.GREEN + C.BOLD)}")
    else:
        print(f"  {_color(f'完成 {ok}/{len(results)}', C.RED)}")
    pause()


def _menu_flash(sess: Session, *, dry_run: bool, pick: bool = False) -> None:
    import argparse

    from ..cli import cmd_flash

    if not _require_package(sess):
        return
    img_dir = _require_images(sess)
    if img_dir is None:
        return

    ns = argparse.Namespace(
        package=sess.package,
        images=str(img_dir),
        dry_run=dry_run,
        yes=False,
        partitions=None,
        allow_dangerous=False,
        force_arb=False,
        no_reboot=True,
        wipe=False,
        both_slots=False,
        no_recreate_logical=False,
        adb_port=None,
    )

    if pick:
        print()
        print("  输入要刷的分区名，空格分隔（例如: boot init_boot system）")
        raw = ask("分区")
        if not raw:
            return
        ns.partitions = raw.split()

    if not dry_run:
        print()
        print(f"  {_color('刷写选项：', C.BOLD)}")
        if confirm("同时刷写 A/B 两个槽位？（更安全，耗时翻倍）"):
            ns.both_slots = True
        if confirm("清空用户数据？（降级或救砖必须选 y）"):
            ns.wipe = True

    cmd_flash(ns)


def _menu_backup(sess: Session) -> None:
    import argparse

    from ..cli import cmd_backup

    cmd_backup(argparse.Namespace(partitions=None, output="backups", adb_port=None))
    pause()


def _menu_arb(sess: Session) -> None:
    import argparse

    from ..cli import cmd_arb

    if not _require_package(sess):
        return
    img_dir = _require_images(sess)
    if img_dir is None:
        return
    cmd_arb(argparse.Namespace(images=str(img_dir), xbl_config=None, adb_port=None))
    pause()


def _menu_device_info(sess: Session) -> None:
    from ..device.discover import detect

    print()
    dev = detect()
    if not dev:
        print(f"  {_color('未检测到设备', C.YELLOW)}")
        print()
        print("  排查建议:")
        print("    1. 数据线是否插在电脑上（不是充电器）")
        print("    2. 手机是否开启「开发者选项 → USB 调试」")
        print("    3. 手机上是否点了「允许 USB 调试」")
        print("    4. 运行菜单 11（环境自检）")
    else:
        for line in dev.describe().splitlines():
            print(f"  {line}")
    pause()


def _menu_reboot(sess: Session) -> None:
    from ..device.discover import DeviceMode, detect
    from ..device.runner import AdbRunner, FastbootRunner

    print()
    print("  重启到哪里？")
    print("    1) 系统（正常开机）")
    print("    2) Fastboot（bootloader）")
    print("    3) FastbootD（用户空间）")
    print("    4) Recovery")
    print("    0) 取消")
    choice = ask("选择", "1")
    if choice == "0":
        return

    dev = detect()
    if not dev:
        print(f"  {_color('未检测到设备', C.YELLOW)}")
        pause()
        return

    if dev.mode is DeviceMode.ADB:
        args = {"1": ("reboot",), "2": ("reboot", "bootloader"),
                "3": ("reboot", "fastboot"), "4": ("reboot", "recovery")}.get(choice)
        if not args:
            return
        AdbRunner().run(*args, timeout=60)
    else:
        fb = FastbootRunner()
        if choice == "1":
            fb.run("reboot", timeout=60)
        elif choice == "2":
            fb.run("reboot", "bootloader", timeout=60)
        elif choice == "3":
            fb.run("reboot", "fastboot", timeout=60)
        elif choice == "4":
            fb.run("reboot", "recovery", timeout=60)
        else:
            return

    print(f"  {_color('✓', C.GREEN)} 已发送重启命令")
    pause()


def _menu_selfcheck(sess: Session) -> None:
    import argparse

    from ..cli import cmd_selfcheck

    cmd_selfcheck(argparse.Namespace())
    pause()


def _menu_about(sess: Session) -> None:
    print()
    print(f"  {_color('Lrom', C.BOLD + C.MAGENTA)} v{__version__} · GPL-3.0-or-later")
    print("  https://github.com/Rovrry/Lrom")
    print()
    print("  设计原则:")
    print("    · 不砖机 > 功能多")
    print("    · 离线优先，零遥测")
    print("    · 先出计划，后执行")
    print("    · 永不自动重启设备")
    print()
    print("  卸载方法: 直接删除本仓库目录即可。")
    print("  （Lrom 不安装到系统，没有残留文件）")
    pause()
