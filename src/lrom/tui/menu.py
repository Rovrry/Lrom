"""交互式主菜单。

设计目标：不熟悉命令行的用户也能完成完整的刷机流程。

风格参考用户习惯（1.解全量包 2.线刷全量包），但每一项都对应
一个可脚本化的子命令，便于进阶用户与自动化。
"""

from __future__ import annotations

import os
import sys
import time
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
# 输入辅助
# ---------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    suffix = f" {_color('[' + default + ']', C.DIM)}" if default else ""
    try:
        return input(f"  {prompt}{suffix}: ").strip() or default
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def ask_path(prompt: str, default: str = "") -> Path | None:
    raw = ask(prompt, default)
    if not raw:
        return None
    return Path(os.path.expanduser(raw.strip().strip("'\"").replace("\\ ", " ")))


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


def pause(msg: str = "按回车继续…") -> None:
    try:
        input(f"\n  {_color(msg, C.DIM)}")
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def input_with_redraw(
    prompt: str,
    *,
    redraw,          # callable() -> None，重绘整个菜单
    interval: float = 1.2,
) -> str:
    """带后台重绘的输入。

    问题：菜单顶部显示设备状态，但 input() 会阻塞，
    期间设备插拔时顶部不会刷新，用户看到的信息是过期的。

    方案：用 select 以小超时轮询 stdin。
      - 有输入时正常读取
      - 无输入时每 interval 秒调用一次 redraw()
      - 重绘时会保留用户已敲入的字符

    只在真实 TTY 上启用；管道/重定向场景退回普通 input()。
    """
    import select
    import sys

    if not sys.stdin.isatty():
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

    try:
        import termios
        import tty
    except ImportError:  # 非 POSIX
        try:
            return input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(0)

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    buf: list[str] = []

    def _render() -> None:
        # 清屏重画，然后还原提示行与已输入内容
        redraw()
        sys.stdout.write(prompt + "".join(buf))
        sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        _render()
        last = time.time()
        while True:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                ch = os.read(fd, 1).decode("utf-8", errors="ignore")
                if not ch:
                    raise EOFError
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    return "".join(buf)
                if ch in ("\x7f", "\x08"):  # 退格
                    if buf:
                        buf.pop()
                        _render()
                    continue
                if ch == "\x03":  # Ctrl+C
                    raise KeyboardInterrupt
                if ch == "\x04":  # Ctrl+D
                    raise EOFError
                if ch.isprintable():
                    buf.append(ch)
                    sys.stdout.write(ch)
                    sys.stdout.flush()
            elif time.time() - last >= interval:
                last = time.time()
                _render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

class Session:
    """菜单会话状态。"""

    def __init__(self) -> None:
        from ..device.tools import load_config

        self.package: Path | None = None
        self.work_dir: Path = Path("work")

        cfg = load_config()
        if wd := cfg.get("work_dir"):
            self.work_dir = Path(wd)
        last = cfg.get("last_package")
        if last and Path(last).exists():
            self.package = Path(last)

    @property
    def image_dir(self) -> Path | None:
        """解包产物目录。自动在几个常见位置查找。"""
        if not self.package:
            return None
        stem = self.package.stem
        for c in (self.work_dir / stem, Path("unpacked") / stem):
            if c.exists() and any(c.glob("*.img")):
                return c
        return self.work_dir / stem

    @property
    def unpacked(self) -> bool:
        d = self.image_dir
        return bool(d and d.exists() and any(d.glob("*.img")))

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
# 头部
# ---------------------------------------------------------------------------

def build_menu(sess: "Session", env_ok: bool) -> str:
    d = C.DIM
    lines = [
        f"  {_color('─ 刷写流程 ────────────────────────────────────', d)}",
        f"   {_color('1', C.CYAN)}) 解全量包          选择 ROM 包并解出镜像",
        f"   {_color('2', C.CYAN)}) 预演检查          ★ 不写设备，先看能不能刷",
        f"   {_color('3', C.CYAN)}) 线刷全量包        真正写入设备（需确认）",
        f"   {_color('4', C.CYAN)}) 只刷指定分区      手动选择分区",
        "",
        f"  {_color('─ 安全 ────────────────────────────────────────', d)}",
        f"   {_color('5', C.CYAN)}) 备份设备分区      刷前备份（需 root）",
        f"   {_color('6', C.CYAN)}) ARB 熔断检测      防降级硬砖",
        "",
        f"  {_color('─ 工具 ────────────────────────────────────────', d)}",
        f"   {_color('7', C.CYAN)}) 设备信息          型号 / 版本 / 分区",
        f"   {_color('8', C.CYAN)}) 重启设备          到 Fastboot / FastbootD / 系统",
        f"   {_color('9', C.CYAN)}) 环境自检          重新检查 adb / udev / 权限",
        f"  {_color('10', C.CYAN)}) 关于",
        "",
        f"   {_color('0', C.CYAN)}) 退出",
    ]
    if not env_ok:
        lines.insert(0, f"   {_color('! 环境未通过检测，设备功能可能不可用', C.RED)}")
        lines.insert(1, "")
    return "\n".join(lines)


def _clear_screen() -> None:
    """清屏。仅在真正的终端里做，且允许用 LROM_NO_CLEAR=1 关闭。"""
    if os.environ.get("LROM_NO_CLEAR"):
        print("\n" * 2)
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        # 管道/重定向场景不清屏，避免把输出冲掉
        print("\n" * 2)
        return
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def print_header(sess: Session, mon=None) -> None:
    _clear_screen()
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

    snap = mon.snapshot if mon else None
    if snap and snap.present:
        label, color = MODE_LABEL.get(snap.mode, (snap.mode, C.GREEN))
        info = snap.model or snap.codename or "未知型号"
        line = (
            f"  {_color('●', color)} 设备    : "
            f"{_color(info, C.BOLD)}  {_color('[' + label + ']', color)}"
        )
        if snap.slot:
            line += f"  槽位 _{snap.slot}"
        print(line)
        if snap.build_id:
            print(f"              {_color(snap.build_id, C.DIM)}")
    else:
        print(f"  {_color('○', C.RED)} 设备    : {_color('未连接', C.RED)}")
        print(f"              {_color('请检查数据线与 USB 调试', C.DIM)}")

    print()
    if sess.package:
        mark = _color("✓", C.GREEN) if sess.unpacked else _color("·", C.DIM)
        print(f"    ROM 包  : {_color(sess.package.name, C.CYAN)}")
        if sess.unpacked:
            n = len(list(sess.image_dir.glob("*.img")))
            print(f"    {mark} 已解包  : {n} 个镜像")
        else:
            print(f"    {mark} 已解包  : {_color('尚未解包', C.DIM)}")
    else:
        print(f"    ROM 包  : {_color('未选择', C.DIM)}")
    print()


MODE_LABEL = {
    "adb": ("ADB", C.GREEN),
    "fastboot": ("Fastboot", C.CYAN),
    "fastbootd": ("FastbootD", C.CYAN),
    "unauthorized": ("未授权", C.YELLOW),
    "": ("未连接", C.RED),
}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def startup_env_check() -> bool:
    """启动时的环境检测（阻塞）。返回 True 表示环境可用。"""
    from ..device.selfcheck import Level, run_selfcheck

    _clear_screen()
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
    print(_color("  第一步：检测系统环境", C.BOLD))
    print()

    items = run_selfcheck()
    marks = {
        Level.OK: ("✓", C.GREEN),
        Level.WARN: ("!", C.YELLOW),
        Level.ERROR: ("✗", C.RED),
        Level.INFO: ("·", C.BLUE),
    }
    errors, warns = [], []
    for it in items:
        mark, color = marks[it.level]
        print(f"  {_color(mark, color)} {it.title}")
        if it.level is Level.ERROR:
            errors.append(it)
        elif it.level is Level.WARN:
            warns.append(it)
    print()

    if errors:
        print(_color(f"  发现 {len(errors)} 个必须解决的问题：", C.RED + C.BOLD))
        print()
        for it in errors:
            print(_color(f"  ✗ {it.title}", C.RED + C.BOLD))
            if it.detail:
                for ln in it.detail.splitlines():
                    print(f"      {ln}")
            if it.fix:
                print(_color("    修复方法：", C.CYAN))
                for ln in it.fix.splitlines():
                    print(f"      {ln}")
            print()
        print(_color("  环境不满足要求，设备相关功能无法使用。", C.RED))
        print()
        ans = ask("(r) 修复后重新检测 / (c) 继续（仅离线功能）/ (q) 退出", "r")
        low = ans.lower()
        if low.startswith("q"):
            raise SystemExit(0)
        if low.startswith("r"):
            return startup_env_check()
        return False

    if warns:
        print(_color(f"  {len(warns)} 个提示（不影响使用）：", C.YELLOW))
        for it in warns:
            print(f"    ! {it.title}")
        print()

    print(_color("  ✓ 环境检测通过", C.GREEN + C.BOLD))
    print()
    return True


def run_menu() -> int:
    from ..device.monitor import DeviceMonitor

    # ── 第一步：环境检测（必须先过） ──
    env_ok = startup_env_check()

    sess = Session()

    # ── 启动实时设备监测 ──
    mon = DeviceMonitor(interval=1.5)

    # 设备状态变化交给顶部实时重绘处理，不再额外打印提示行
    # （否则会与重绘叠加，产生重复输出）
    mon.start()

    handlers = {
        "1": _flow_unpack,
        "2": lambda s_, m: _flow_flash(s_, m, dry_run=True),
        "3": lambda s_, m: _flow_flash(s_, m, dry_run=False),
        "4": lambda s_, m: _flow_flash(s_, m, dry_run=False, pick=True),
        "5": _menu_backup,
        "6": _menu_arb,
        "7": _menu_device_info,
        "8": _menu_reboot,
        "9": _menu_selfcheck,
        "10": _menu_about,
    }

    def _draw() -> None:
        print_header(sess, mon)
        print(build_menu(sess, env_ok))
        print()

    try:
        while True:
            _draw()
            try:
                choice = input_with_redraw(
                    f"  {_color('请选择', C.BOLD)}: ", redraw=_draw, interval=1.2
                ).strip()
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
                handler(sess, mon)
            except SystemExit:
                raise
            except KeyboardInterrupt:
                print(f"\n  {_color('已取消', C.YELLOW)}")
                pause()
            except Exception as exc:
                print(f"\n  {_color('出错了:', C.RED)} {exc}")
                pause()
            finally:
                try:
                    mon.refresh_now()
                except Exception:
                    pass
    finally:
        mon.stop()


def _flow_unpack(sess: Session, mon) -> None:
    """流程一：选择 ROM 包 -> 分析 -> 解包。

    用户的诉求：不要把"选择 ROM 包"做成独立菜单项，
    而是把它内嵌在"解全量包"这一步里。
    """
    from ..core.package import Severity, Verdict, inspect_package
    from ..core.unpack import unpack_package, write_index

    _clear_screen()
    print()
    print(_color("  ── 解全量包 " + "─" * 44, C.BOLD))
    print()
    print("  请输入 ROM 包路径（.zip 文件，可直接把文件拖进终端）")
    if sess.package and sess.package.exists():
        print(f"  {_color('直接回车可沿用上次的选择', C.DIM)}")
    print()

    default = str(sess.package) if sess.package and sess.package.exists() else ""
    p = ask_path("ROM 包", default)
    if not p:
        if default:
            p = Path(default)
        else:
            print(f"  {_color('未选择包', C.YELLOW)}")
            pause()
            return

    if not p.exists():
        print(f"  {_color('文件不存在:', C.RED)} {p}")
        pause()
        return

    sess.package = p
    sess.save_state()

    # ---- 分析包 ----
    print()
    print("  正在分析包…")
    info = inspect_package(p)

    print()
    if info.product_name:
        print(f"    型号    : {info.product_name} ({info.pre_device or '?'})")
    if info.version_name:
        print(f"    版本    : {info.version_name}")
    if info.android_version:
        print(f"    Android : {info.android_version}")
    print(f"    包类型  : {info.kind.value}")
    if info.manifest:
        print(f"    分区数  : {len(info.manifest.partitions)}")

    if info.verdict is not Verdict.FLASHABLE:
        print()
        print(f"  {_color('此包不能线刷:', C.RED + C.BOLD)} {info.verdict.value}")
        for f in info.findings:
            if f.severity in (Severity.BLOCKER, Severity.DANGER):
                print(f"    · {f.message}")
        pause()
        return

    # ---- 型号核对（若设备已连接）----
    snap = mon.snapshot if mon else None
    if snap and snap.present and info.pre_device and snap.codename:
        if snap.codename != info.pre_device:
            print()
            print(
                _color(
                    f"  ⚠ 型号不一致：包要求 {info.pre_device}，"
                    f"设备是 {snap.codename}",
                    C.RED + C.BOLD,
                )
            )
            if not confirm("型号不匹配，仍要解包吗？"):
                return
        else:
            print(f"    {_color('✓', C.GREEN)} 型号匹配: {snap.codename}")

    # ---- 解包 ----
    out = sess.image_dir
    assert out is not None
    print()
    print(f"  输出目录: {out}")
    if out.exists():
        n = len(list(out.glob("*.img")))
        if n and not confirm(f"已有 {n} 个镜像，覆盖吗？"):
            return

    print()
    counter = {"n": 0}

    def on_result(res):
        counter["n"] += 1
        mark = _color("✓", C.GREEN) if res.ok else _color("✗", C.RED)
        detail = f"{res.size_mb:>9,.1f} MB" if res.ok else res.error
        print(f"  [{counter['n']:2}] {res.partition:<18} {mark} {detail:<36}")

    results = unpack_package(p, out, result_cb=on_result)
    write_index(results, out)

    ok = sum(1 for r in results if r.ok)
    print()
    if results and ok == len(results):
        print(_color(f"  ✓ 解包完成：{ok} 个分区", C.GREEN + C.BOLD))
    else:
        print(_color(f"  完成 {ok}/{len(results)}", C.RED))
    pause()


def _flow_flash(sess: Session, mon, *, dry_run: bool, pick: bool = False) -> None:
    import argparse

    from ..cli import cmd_flash

    if not sess.unpacked:
        print()
        print(f"  {_color('尚未解包，请先执行菜单 1（解全量包）', C.YELLOW)}")
        pause()
        return

    # 危险操作前必须重新确认真实设备状态（监测快照可能滞后）
    snap = mon.refresh_now() if mon else None
    if not snap or not snap.present:
        print()
        print(f"  {_color('当前未检测到设备', C.RED + C.BOLD)}")
        print("  请检查：")
        print("    · 数据线是否插好")
        print("    · 手机是否开启 USB 调试")
        pause()
        return

    ns = argparse.Namespace(
        package=sess.package,
        images=str(sess.image_dir),
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

    if dry_run:
        print()
        print(_color("  预演检查：只看能不能刷，不写入任何数据。", C.BOLD))
        print()
        cmd_flash(ns)
        pause()
        return

    if pick:
        print()
        print("  输入要刷的分区名，空格分隔（例如: boot init_boot system）")
        raw = ask("分区")
        if not raw:
            return
        ns.partitions = raw.split()

    print()
    print(f"  {_color('刷写选项：', C.BOLD)}")
    if confirm("清空用户数据？（降级或救砖必须选 y）"):
        ns.wipe = True

    cmd_flash(ns)
    pause()


def _menu_backup(sess: Session, mon) -> None:
    import argparse

    from ..cli import cmd_backup

    cmd_backup(argparse.Namespace(partitions=None, output="backups", adb_port=None))
    pause()


def _menu_arb(sess: Session, mon) -> None:
    import argparse

    from ..cli import cmd_arb

    if not sess.unpacked:
        print()
        print(f"  {_color('尚未解包，请先执行菜单 1（解全量包）', C.YELLOW)}")
        pause()
        return
    img_dir = sess.image_dir
    cmd_arb(argparse.Namespace(images=str(img_dir), xbl_config=None, adb_port=None))
    pause()


def _menu_device_info(sess: Session, mon) -> None:
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


def _menu_reboot(sess: Session, mon) -> None:
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


def _menu_selfcheck(sess: Session, mon) -> None:
    import argparse

    from ..cli import cmd_selfcheck

    cmd_selfcheck(argparse.Namespace())
    pause()


def _menu_about(sess: Session, mon) -> None:
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
