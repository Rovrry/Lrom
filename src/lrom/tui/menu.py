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

def _sanitize_dirname(name: str) -> str:
    """把版本号/文件名整理成安全的目录名。

    去掉路径分隔符和 shell 敏感字符，保留字母数字与 . _ -。
    ``OPD2404_16.0.5.1201(CN01)`` → ``OPD2404_16.0.5.1201_CN01``
    """
    import re

    cleaned = re.sub(r"[^\w.\-]+", "_", name.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "unnamed"


class Session:
    """菜单会话状态。"""

    def __init__(self) -> None:
        from ..device.tools import load_config

        self.package: Path | None = None
        self.work_dir: Path = Path("work")
        # 包版本号缓存（避免每次进菜单都解析 ZIP）
        self._cached_version: str | None = None
        self._cached_version_for: str | None = None

        cfg = load_config()
        if wd := cfg.get("work_dir"):
            self.work_dir = Path(wd)
        last = cfg.get("last_package")
        if last and Path(last).exists():
            self.package = Path(last)

    @property
    def image_dir(self) -> Path | None:
        """解包产物目录。

        优先按**包内版本号**命名（如 ``work/OPD2404_16.0.5.1201``），
        因为下载来的包文件名常被随机化或改过，不可靠。
        版本号读不出来时退回按文件名（stem）命名。

        查找顺序：版本号目录 → 文件名目录 → unpacked/ 下的同名目录。
        """
        if not self.package:
            return None
        names = self._image_dir_names()
        for name in names:
            for base in (self.work_dir, Path("unpacked")):
                c = base / name
                if c.exists() and any(c.glob("*.img")):
                    return c
        return self.work_dir / names[0]

    def _image_dir_names(self) -> list[str]:
        """返回该包可能的解包目录名，优先版本号。"""
        names: list[str] = []
        ver = self._package_version()
        if ver:
            names.append(_sanitize_dirname(ver))
        stem = self.package.stem if self.package else ""
        if stem:
            names.append(_sanitize_dirname(stem))
        return names or ["unnamed"]

    def _package_version(self) -> str | None:
        """读取包内版本号（带缓存，避免每次进菜单都解析 ZIP）。"""
        if self._cached_version_for == (str(self.package) if self.package else None):
            return self._cached_version
        self._cached_version_for = str(self.package) if self.package else None
        self._cached_version = None
        if self.package and Path(self.package).exists():
            try:
                from ..core.package import inspect_package

                info = inspect_package(Path(self.package))
                self._cached_version = info.version_name or None
            except Exception:  # noqa: BLE001
                self._cached_version = None
        return self._cached_version


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
        f"  {_color('─ Root ────────────────────────────────────────', d)}",
        f"   {_color('7', C.CYAN)}) 获取 Root          下载管理器 + patch init_boot + 刷入",
        f"   {_color('8', C.CYAN)}) 只下载管理器      单独下载 APK，不走 root 流程",
        f"   {_color('9', C.CYAN)}) 回滚 Root          刷回原厂 init_boot",
        "",
        f"  {_color('─ 工具 ────────────────────────────────────────', d)}",
        f"   {_color('10', C.CYAN)}) 设备信息          型号 / 版本 / 分区",
        f"   {_color('11', C.CYAN)}) 重启设备          到 Fastboot / FastbootD / 系统",
        f"   {_color('12', C.CYAN)}) 环境自检          重新检查 adb / udev / 权限",
        f"   {_color('13', C.CYAN)}) 关于",
        "",
        f"  {_color('─ 高级（高风险）───────────────────────────────', d)}",
        f"   {_color('14', C.CYAN)}) BL 回锁           平刷两次后回锁（会清空数据，慎用）",
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


def _print_device_facts(snap) -> None:
    """在主页头部补印关键设备信息：Android 版本、BL 锁、9008 提示。"""
    from ..core.edl_db import EdlRisk

    parts: list[str] = []

    if snap.android_version:
        parts.append(f"Android {snap.android_version}")

    if snap.locked == "0":
        parts.append(_color("BL 已解锁", C.GREEN))
    elif snap.locked == "1":
        parts.append(_color("BL 已锁定", C.RED))
    else:
        parts.append(_color("BL 状态未知", C.YELLOW))

    if parts:
        print("              " + _color("·", C.DIM) + " " + "  ".join(parts))

    # 9008 免授权：仅按**设备自身**标识判定，未收录机型不显示任何提示。
    edl_res, _ = _check_edl_for(snap, snap.build_id or None)
    if edl_res is not None and edl_res.risk is EdlRisk.LIKELY_FUSED:
        print(
            "              "
            + _color("! 9008 免授权已熔断（不可逆，降级无效）", C.YELLOW)
        )


def _device_model_keys(snap) -> list[str]:
    """取**设备侧**可用于机型匹配的标识。

    只用设备自己的型号/代号/产品名，绝不能用包里的 product_name ——
    否则换一台设备（如把一加 15 接上来）仍会按包的机型给提醒，
    属于张冠李戴。

    型号（OPD2404）、代号（OP5D77L1）、产品名三者都可能是库里记录的形式，
    全部收进来逐个尝试。
    """
    if not snap or not getattr(snap, "present", False):
        return []
    keys: list[str] = []
    for attr in ("model", "codename", "product_name"):
        val = getattr(snap, attr, None)
        if val and val not in keys:
            keys.append(val)
    return keys


def _check_edl_for(snap, package_version):
    """按设备自身标识做 9008 判定，返回 (结果, 命中的标识) 或 (None, None)。"""
    from ..core.edl_db import EdlRisk, check_edl_risk

    device_ver = getattr(snap, "build_id", None) if snap else None
    for cand in _device_model_keys(snap):
        res = check_edl_risk(
            package_version=package_version,
            device_version=device_ver,
            model_code=cand,
            codename=getattr(snap, "codename", None),
        )
        if res.risk is not EdlRisk.UNKNOWN:
            return res, cand
    return None, None


def _warn_edl_risk(info, mon) -> bool:
    """在解包/刷写前提示 9008 免授权影响。

    返回 True 表示**应当中止流程**（当前配置为硬阻断）。

    判定机型只认设备侧标识（见 `_device_model_keys`）。未收录的机型
    （如一加 15 PLK110）不做任何提示。
    """
    from ..core.edl_db import EdlRisk

    snap = mon.snapshot if mon else None
    if not snap or not getattr(snap, "present", False):
        return False

    res, matched = _check_edl_for(snap, info.version_name)
    if res is None or res.risk is EdlRisk.SAFE:
        return False

    model_show = (
        getattr(snap, "model", None)
        or getattr(snap, "codename", None)
        or matched
    )

    print()
    if res.risk is EdlRisk.WILL_FUSE:
        print(_color("  ┌─ ⛔ 已拦截：9008 免授权将被永久熔断 " + "─" * 20, C.RED + C.BOLD))
        print(f"  │ 机型    : {model_show}（{matched}）")
        print(f"  │ 包版本  : {res.package_version}")
        print(f"  │ 设备版本: {res.device_version}")
        print(_color("  │", C.RED))
        print(_color(f"  │ {res.message}", C.RED))
        print(_color("  │", C.RED))
        print(_color("  │ 熔断后：失去 EDL 自主救砖通道，", C.RED))
        print(_color("  │ 若 OCDT 损坏，售后也无法修复。", C.RED))
        print(_color("  │ 降级【不能】恢复 —— 该状态由设备侧决定。", C.RED + C.BOLD))
        print(_color("  └" + "─" * 46, C.RED + C.BOLD))
        print()
        print(_color("  已中止刷写，未写入任何数据。", C.RED + C.BOLD))
        print(_color("  判定依据：系统版本号推断，设备侧实际状态无法读取", C.DIM))
        return True

    if res.risk is EdlRisk.LIKELY_FUSED:
        # 设备已越过阈值：熔断已成事实，刷同版本或升级不会让情况更糟 → 放行
        print(_color("  ! 9008 免授权状态提醒", C.YELLOW + C.BOLD))
        print(f"    机型    : {model_show}（{matched}）")
        print(f"    包版本  : {res.package_version}")
        print(f"    设备版本: {res.device_version}")
        print(f"    {res.message}")
        print(_color("    判定依据：系统版本号推断，设备侧实际状态无法读取", C.DIM))
        return False

    if res.risk is EdlRisk.DOWNGRADE_NO_RECOVER:
        # 降级已无 9008 可失，但要让用户知道救不回来 → 提醒后放行
        print(_color("  ! 降级操作提醒", C.YELLOW + C.BOLD))
        print(f"    机型    : {model_show}（{matched}）")
        print(f"    包版本  : {res.package_version}")
        print(f"    设备版本: {res.device_version}")
        print(f"    {res.message}")
        print(_color("    判定依据：系统版本号推断，设备侧实际状态无法读取", C.DIM))
        return False

    return False


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
        _print_device_facts(snap)
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
        "7": _menu_root,
        "8": _menu_fetch_manager,
        "9": _menu_root_rollback,
        "10": _menu_device_info,
        "11": _menu_reboot,
        "12": _menu_selfcheck,
        "13": _menu_about,
        "14": _menu_relock,
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
    print(_color("  输入 q 取消并返回主菜单", C.DIM))
    print()

    default = str(sess.package) if sess.package and sess.package.exists() else ""
    p = ask_path("ROM 包", default)
    if p and p.name.lower() in ("q", "quit", "exit"):
        print(f"  {_color('已取消，返回主菜单', C.DIM)}")
        pause()
        return
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
    print(_color("  ── ROM 包信息 " + "─" * 46, C.BOLD))
    print(f"    文件      : {p}")
    try:
        print(f"    大小      : {p.stat().st_size / (1024 ** 3):.2f} GB")
    except OSError:
        pass
    if info.product_name:
        print(f"    型号      : {info.product_name} ({info.pre_device or '?'})")
    if info.version_name:
        print(f"    版本      : {info.version_name}")
    if info.display_os_version:
        print(f"    显示版本  : {info.display_os_version}")
    if info.android_version:
        print(f"    Android   : {info.android_version}")
    if info.security_patch:
        print(f"    安全补丁  : {info.security_patch}")
    if info.ota_id:
        print(f"    OTA ID    : {info.ota_id}")
    print(f"    包类型    : {info.kind.value}")
    if info.manifest:
        total = 0
        try:
            total = sum(getattr(op, "data_length", 0) for part in info.manifest.partitions for op in part.operations)
        except Exception:
            total = 0
        line = f"    分区数    : {len(info.manifest.partitions)}"
        if total:
            line += f"（共 {total / (1024 ** 3):.2f} GB）"
        print(line)
    print(f"    线刷判定  : {info.verdict.value}")

    # ---- 9008 免授权检查（命中即中止）----
    if _warn_edl_risk(info, mon):
        pause()
        return

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
                    f"  ⚠ 这不是当前设备的 ROM 包",
                    C.RED + C.BOLD,
                )
            )
            dev_name = snap.model or snap.codename
            print(f"    当前设备 : {dev_name}（代号 {snap.codename}）")
            print(f"    包适用   : {info.product_name or '?'}（代号 {info.pre_device}）")
            print()
            print(
                _color(
                    "    解包只是拆文件、不写设备，可以先解出来看；\n"
                    "    但刷写时会被拒绝 —— 刷入其他机型的包会直接变砖。",
                    C.YELLOW,
                )
            )
            if not confirm("仍然解包吗？"):
                return
        else:
            print(f"    {_color('✓', C.GREEN)} 型号匹配: {snap.codename}")

    # ---- 解包 ----
    out = sess.image_dir
    assert out is not None
    print()
    print(f"  输出目录: {out}")
    if out.name == _sanitize_dirname(sess._package_version() or ""):
        print(_color("            （按包内版本号命名）", C.DIM))

    # 清除该目录下上一次的解包产物，避免新旧混合：
    # 若上一个包与本包的镜像集合不同（少一个 dtbo.img 之类），
    # 旧镜像会留下来，目录内容就与当前包不符。
    # 只清本目录、只清 *.img 与索引文件，绝不碰其它目录。
    if out.exists():
        stale = list(out.glob("*.img")) + list(out.glob("*.img.part"))
        idx = out / "unpacked.json"
        if idx.exists():
            stale.append(idx)
        if stale:
            print(f"  发现 {len(stale)} 个旧产物，清理后重新解包")
            for f in stale:
                try:
                    f.unlink()
                except OSError as e:
                    print(f"    ! 无法删除 {f.name}: {e}")
    out.mkdir(parents=True, exist_ok=True)

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
        print(_color("  输入 q 可取消", C.DIM))
        print()
        cmd_flash(ns)
        pause()
        return

    if pick:
        print()
        print("  输入要刷的分区名，空格分隔（例如: boot init_boot system）")
        print(_color("  直接回车或输入 q 取消", C.DIM))
        raw = ask("分区")
        if not raw or raw.strip().lower() in ("q", "quit", "exit"):
            print(f"  {_color('已取消，返回主菜单', C.DIM)}")
            pause()
            return
        ns.partitions = raw.split()

    # 刷写前的 9008 检查 —— 命中即中止，不进入刷写
    try:
        from ..core.package import inspect_package as _inspect

        _info = _inspect(sess.package) if sess.package else None
        if _info is not None:
            if _warn_edl_risk(_info, mon):
                pause()
                return
    except Exception as exc:  # noqa: BLE001
        # 检查本身出错时不能静默放过，但也不能因此阻断正常刷写
        print(_color(f"  ! 9008 检查未能完成：{exc}", C.YELLOW))

    print()
    print(f"  {_color('刷写选项：', C.BOLD)}")
    print(_color("  在下一步输入 q 可退出，不会写入任何数据", C.DIM))
    ans = ask("清空用户数据？（降级或救砖必须选 y，直接回车保留数据，输入 q 退出）")
    if ans.strip().lower() in ("q", "quit", "exit"):
        print(f"  {_color('已取消，未写入任何数据', C.DIM)}")
        pause()
        return
    if ans.strip().lower() in ("y", "yes", "是"):
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


def _menu_relock(sess: "Session", mon) -> None:
    """菜单 14：BL 回锁（高风险）。

    交互分三种：
      1) 让 Lrom 平刷两槽（当前槽 ↔ 相反槽各一遍），再回锁
      2) 用户已自己在设备上装好两次系统，直接回锁
      3) 用户确认两槽已是原厂，直接回锁
      0) 退出
    """
    import argparse

    from ..cli import cmd_relock

    print()
    print(f"  {_color('BL 回锁', C.RED + C.BOLD)}")
    print()
    print(f"  {_color('⚠ 回锁前提', C.RED + C.BOLD)}：两个槽的关键分区都必须是")
    print("    本机的原厂签名镜像（不是 root / 三方 ROM / 跨区包）。")
    print("    否则回锁后设备会变砖，且没有解锁通道自救。")
    print()
    print(f"  {_color('回锁会清空全部用户数据。', C.YELLOW)}")
    print()
    print("  请选择方式：")
    print(f"    {_color('1', C.CYAN)}) 让 Lrom 平刷两槽（推荐，自动刷当前槽+相反槽）")
    print(f"    {_color('2', C.CYAN)}) 我已自己安装过两次系统 ROM，直接回锁")
    print(f"    {_color('3', C.CYAN)}) 我已确认两槽都是原厂，直接回锁")
    print(f"    {_color('0', C.CYAN)}) 退出")
    print()
    choice = ask("请选择", "0").strip().lower()
    if choice in ("0", "q", "quit", "exit"):
        print(f"  {_color('已取消', C.DIM)}")
        pause()
        return
    if choice not in ("1", "2", "3"):
        print(f"  {_color('无效选择，已取消', C.YELLOW)}")
        pause()
        return

    # 1) 需要完整包做平刷；2/3) 直接回锁，但为做包适配性校验仍需指定包
    default = str(sess.package) if sess.package and Path(sess.package).exists() else ""
    if default:
        print()
        print(f"  当前 ROM 包: {default}")
    p = ask_path("完整系统包路径（.zip）", default)
    if p and p.name.lower() in ("q", "quit", "exit"):
        print(f"  {_color('已取消', C.DIM)}")
        pause()
        return
    if not p:
        if default:
            p = Path(default)
        else:
            print(f"  {_color('未指定系统包，已取消', C.YELLOW)}")
            pause()
            return
    if not p.exists():
        print(f"  {_color('文件不存在:', C.RED)} {p}")
        pause()
        return

    # 方式 1：用已解包镜像平刷；2/3 不刷，images 可为空
    img_dir = None
    skip_flash = choice in ("2", "3")
    if not skip_flash:
        try:
            _s = Session()
            _s.package = p
            _s.work_dir = sess.work_dir
            if _s.unpacked:
                img_dir = str(_s.image_dir)
        except Exception:  # noqa: BLE001
            img_dir = None
        if img_dir:
            print(f"  使用已解包镜像: {img_dir}")
        else:
            print(f"  {_color('注意：未找到已解包镜像，将直接从包内读取（较慢）。', C.DIM)}")
            print(f"  {_color('建议先执行菜单 1 解包。', C.DIM)}")
    else:
        print()
        if choice == "2":
            print(f"  {_color('已选择：你自己已安装过两次系统 ROM', C.CYAN)}")
        else:
            print(f"  {_color('已选择：你确认两槽都是原厂', C.CYAN)}")
        print("  将跳过平刷，直接校验并回锁。")
    print()

    rc = cmd_relock(
        argparse.Namespace(
            package=p,
            images=img_dir,
            dry_run=False,
            yes=False,
            adb_port=None,
            check_model=False,
            skip_flash=skip_flash,
        )
    )
    if rc != 0:
        print()
        print(f"  {_color('回锁未完成。', C.YELLOW)}")
    pause()


def _menu_about(sess: Session, mon) -> None:
    print()
    print(f"  {_color('Lrom', C.BOLD + C.MAGENTA)} v{__version__} · GPL-3.0-or-later")
    print("  https://github.com/Rovrry/Lrom")
    print()
    print("  设计原则:")
    print("    · 不砖机 > 功能多")
    print("    · 离线优先，零遥测（唯一联网：root 管理器下载）")
    print("    · 先出计划，后执行")
    print("    · 永不自动重启设备")
    print()
    print("  卸载方法: 直接删除本仓库目录即可。")
    print("  （Lrom 不安装到系统，没有残留文件）")
    pause()


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


def _find_stock_init_boot(sess: "Session"):
    """定位原厂 init_boot.img。"""
    from pathlib import Path

    candidates = []
    if sess.image_dir:
        candidates.append(Path(sess.image_dir) / "init_boot.img")
    candidates.append(Path("unpacked/rom/init_boot.img"))
    for p in candidates:
        if p.is_file():
            return p
    return None


def _verify_device_for_root(snap, sess: "Session", *, action: str) -> bool:
    """root / 回滚 root 前的机型与版本核对。

    为什么需要：这两个操作都会往设备写 init_boot。如果解出的镜像
    来自**别的机型**，或者设备当前系统版本与镜像不匹配，刷进去
    轻则 root 失效，重则无法开机。

    核对项：
    1. 设备已连接、处于 ADB 模式
    2. BL 已解锁
    3. 若当前选的包能读到版本/机型，且设备已连接 → 机型必须一致

    返回 True 表示可以继续。
    """
    if not snap or not getattr(snap, "present", False):
        print(_color("  ⛔ 未检测到设备", C.RED + C.BOLD))
        print("  请连接设备并确认能识别（./lrom devices）。")
        return False

    if snap.mode != "adb":
        print(_color(f"  ⛔ 设备当前处于 {snap.mode} 模式", C.RED + C.BOLD))
        print(f"  {action}需要读取设备信息，请先开机启动再操作。")
        return False

    # BL 锁
    if snap.locked == "1":
        print(_color("  ⛔ Bootloader 未解锁，无法刷写", C.RED + C.BOLD))
        print("  请先完成 OEM 解锁。注意：解锁会清空全部用户数据。")
        return False
    if snap.locked == "":
        print(_color("  ⚠ 无法确认 Bootloader 是否已解锁，请自行确认", C.YELLOW))

    # 机型一致性（仅当能读到包信息时）
    info = None
    if sess and sess.package and Path(sess.package).exists():
        try:
            from ..core.package import inspect_package

            info = inspect_package(Path(sess.package))
        except Exception:  # noqa: BLE001
            info = None

    if info is not None and (info.pre_device or info.product_name):
        dev_key = snap.codename or snap.model or ""
        pkg_key = info.pre_device or info.product_name
        mismatch = False
        if info.pre_device and snap.codename and info.pre_device != snap.codename:
            mismatch = True
        if info.product_name and snap.model and info.product_name != snap.model:
            mismatch = True
        if mismatch:
            print(_color("  ⛔ 当前 ROM 包与设备机型不一致，拒绝执行", C.RED + C.BOLD))
            print()
            print(f"    设备      : {snap.model or '?'}（代号 {snap.codename or '?'}）")
            print(f"    当前包适用: {info.product_name or '?'}（代号 {info.pre_device or '?'}）")
            print()
            print("    用别的机型的 init_boot 做 root 会直接导致无法开机。")
            print("    请先解包与你设备匹配的 ROM 包。")
            return False
        if dev_key and pkg_key:
            print(f"  {_color('✓', C.GREEN)} 机型匹配: {snap.model or snap.codename}")

    # 系统版本提示（不阻断，仅告知，便于用户核对）
    if snap.build_id:
        print(f"  {_color('·', C.DIM)} 设备系统版本: {snap.build_id}")
        if info is not None and info.version_name:
            print(f"  {_color('·', C.DIM)} 镜像来自包版本: {info.version_name}")
            if info.version_name != snap.build_id:
                print(
                    _color(
                        "  ! 镜像版本与设备当前版本不同 —— root 后可能出现"
                        "模块加载失败，请确认这是你要的版本",
                        C.YELLOW,
                    )
                )
    return True


def _menu_root(sess: "Session", mon) -> None:
    """菜单 7：获取 Root。

    流程：定位原厂镜像 → 机型核对 → （可选）下载并安装管理器 →
    执行 root 计划。下载管理器已内置进本流程，不必再单独走菜单 8。
    """
    import argparse

    from ..cli import cmd_root

    print()
    image = _find_stock_init_boot(sess)
    if image is None:
        print(f"  {_color('找不到原厂 init_boot.img', C.YELLOW)}")
        print()
        print("  请先执行菜单 1（解全量包），或用命令行指定：")
        print(f"    {_color('./lrom root -i <init_boot.img路径>', C.CYAN)}")
        pause()
        return

    print(f"  原厂镜像: {image}")
    print()

    # 机型与版本核对（危险写操作之前）
    snap = mon.refresh_now() if mon else None
    if not _verify_device_for_root(snap, sess, action="root"):
        pause()
        return
    print()

    # 明示退出方式，并给一次"开始前"的确认，避免走进去才发现想退出
    print(f"  {_color('按 q 可随时退出，返回主菜单（不会写入任何数据）', C.DIM)}")
    if not confirm("开始获取 Root 流程？"):
        print(f"  {_color('已取消，返回主菜单', C.DIM)}")
        pause()
        return
    print()

    # ---- 根管理器 APK（内置下载）----
    mgr = _ensure_root_manager(mon)
    if mgr is _ABORT:
        print(f"  {_color('已取消，返回主菜单', C.DIM)}")
        pause()
        return
    print()

    print(f"  {_color('提示', C.BOLD)}：")
    print("    · 刷写前会把原厂镜像备份到 root-work/，可随时回滚")
    print("    · APP 方式：按提示在管理器里选择刚推送的镜像进行修补")
    print("    · Lrom 不会自动重启设备")
    print()

    rc = cmd_root(
        argparse.Namespace(
            image=image,
            method="app",
            partition=None,
            flash_mode=None,
            kmi=None,
            dry_run=False,
            yes=False,
            workdir="root-work",
            adb_port=None,
        )
    )
    if rc != 0:
        print()
        print(f"  {_color('流程未完成。', C.YELLOW)}")
    pause()


#: 用户在交互中选择「退出整个流程」时返回的哨兵值
_ABORT = object()


def _ensure_root_manager(mon) -> Path | None:
    """确保 root 管理器 APK 已下载并安装。

    - 若 root-work/ 里已有 APK，询问是否沿用/重新下载
    - 选择管理器与渠道 → 下载 → 自动推送到设备（有设备时）
    - 明确标注 APK 的本机绝对路径

    返回已就绪的 APK 路径；用户跳过或失败时返回 None。
    """
    from ..core.manager_fetch import MANAGER_SOURCES

    workdir = Path("root-work")
    existing = sorted(workdir.glob("*.apk")) if workdir.is_dir() else []

    print(f"  {_color('Root 管理器（APK）', C.BOLD)}")
    if existing:
        print("  已有下载好的管理器：")
        for i, p in enumerate(existing, 1):
            try:
                mb = p.stat().st_size / 1024 / 1024
            except OSError:
                mb = 0
            print(
                f"    {_color(str(i), C.CYAN)}) {p.name}  ({mb:.1f} MB)"
                f"  {_color(str(p.resolve()), C.DIM)}"
            )
        print(f"    {_color('d', C.CYAN)}) 重新下载一个")
        print(f"    {_color('s', C.CYAN)}) 跳过（稍后自己安装）")
        print(f"    {_color('q', C.CYAN)}) 退出流程")
        print()
        pick = ask("请选择", "1").strip().lower()
        if pick in ("q", "quit", "exit"):
            return _ABORT
        if pick == "s":
            print(f"  {_color('已跳过管理器环节', C.DIM)}")
            return None
        if pick != "d":
            try:
                chosen = existing[int(pick) - 1]
            except (ValueError, IndexError):
                print(f"  {_color('无效选择，跳过', C.YELLOW)}")
                return None
            _install_apk_to_device(chosen, mon)
            return chosen

    # ---- 选择管理器 ----
    print("  这是 Lrom 唯一会联网的功能。只访问 GitHub，")
    print("  只下载文件、不执行任何远程代码，下载后强制校验 sha256。")
    print()
    keys = list(MANAGER_SOURCES)
    for i, key in enumerate(keys, 1):
        src = MANAGER_SOURCES[key]
        print(f"    {_color(str(i), C.CYAN)}) {src.label:<20s} {src.repo}")
    print(f"    {_color('s', C.CYAN)}) 跳过下载")
    print(f"    {_color('q', C.CYAN)}) 退出流程")
    print()
    pick = ask("请选择", "1").strip().lower()
    if pick in ("q", "quit", "exit"):
        return _ABORT
    if pick == "s":
        print(f"  {_color('已跳过管理器环节', C.DIM)}")
        return None
    try:
        key = keys[int(pick) - 1]
    except (ValueError, IndexError):
        print(f"  {_color('无效选择，跳过', C.YELLOW)}")
        return None

    print()
    print(f"  {_color('渠道', C.BOLD)}：")
    print(f"    {_color('1', C.CYAN)}) 正式版（stable）")
    print(f"    {_color('2', C.CYAN)}) 测试版（beta，可能有 bug）")
    print(f"    {_color('q', C.CYAN)}) 退出流程")
    print()
    ch = ask("请选择", "1").strip().lower()
    if ch in ("q", "quit", "exit"):
        return _ABORT
    channel = "beta" if ch == "2" else "stable"

    print()
    import argparse

    from ..cli import cmd_fetch_manager

    rc = cmd_fetch_manager(
        argparse.Namespace(
            source=key,
            channel=channel,
            dest=str(workdir),
            asset=None,
            yes=False,
            adb_port=None,
        )
    )
    if rc != 0:
        return None
    # cmd_fetch_manager 内部已完成安装；安装成功后本地包会被删除，
    # 此时无需返回路径。若因无设备/失败而保留了包，则回找最新 APK 返回。
    apks = sorted(
        workdir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return apks[0] if apks else None


def _install_apk_to_device(apk: Path, mon) -> None:
    """把已存在的 APK 装到设备（用于「沿用已有 APK」分支）。"""
    from ..core.manager_fetch import install_manager_apk

    print()
    print(f"  正在安装 {_color(apk.name, C.BOLD)} 到设备…")
    status, detail = install_manager_apk(apk)
    if status == "installed":
        print(f"  {_color('✓ 已安装', C.GREEN)}")
        # 装进设备后删掉本地包，避免残留
        try:
            apk.unlink()
            print(f"  {_color('✓ 已删除本地安装包', C.DIM)}")
        except OSError as exc:  # noqa: BLE001
            print(f"  {_color(f'! 删除失败: {exc}', C.YELLOW)}")
            print(f"    {apk.resolve()}")
    elif status == "no_device":
        print(f"  {_color('! 未检测到设备', C.YELLOW)}")
        print(f"    {detail}")
        print(f"  {_color('本机位置', C.DIM)}: {apk.resolve()}")
    else:
        print(f"  {_color('⛔ 安装失败', C.RED)}")
        print(f"    {detail}")
        print(f"  {_color('本机位置', C.DIM)}: {apk.resolve()}")


def _menu_fetch_manager(sess: "Session", mon) -> None:
    """菜单 8：下载 Root 管理器 APK。"""
    from ..core.manager_fetch import MANAGER_SOURCES

    print()
    print(f"  {_color('下载 Root 管理器', C.BOLD)}")
    print()
    print("  这是 Lrom 唯一会联网的功能。只访问 GitHub，")
    print("  只下载文件、不执行任何远程代码，下载后强制校验 sha256。")
    print()

    keys = list(MANAGER_SOURCES)
    for i, key in enumerate(keys, 1):
        src = MANAGER_SOURCES[key]
        print(f"    {_color(str(i), C.CYAN)}) {src.label:<20s} {src.repo}")
    print()
    pick = ask("请选择", "1")
    try:
        key = keys[int(pick.strip()) - 1]
    except (ValueError, IndexError):
        print(f"  {_color('无效选择', C.YELLOW)}")
        pause()
        return

    print()
    print(f"  {_color('渠道', C.BOLD)}：")
    print(f"    {_color('1', C.CYAN)}) 正式版（stable）")
    print(f"    {_color('2', C.CYAN)}) 测试版（beta，可能有 bug）")
    print()
    channel = "beta" if ask("请选择", "1").strip() == "2" else "stable"

    print()
    import argparse

    from ..cli import cmd_fetch_manager

    cmd_fetch_manager(
        argparse.Namespace(
            source=key,
            channel=channel,
            dest="root-work",
            asset=None,
            yes=False,
        )
    )
    pause()


def _menu_root_rollback(sess: "Session", mon) -> None:
    """菜单 9：回滚 Root（刷回原厂 init_boot）。"""
    from pathlib import Path

    print()
    print(f"  {_color('回滚 Root', C.BOLD)}")
    print()

    workdir = Path("root-work")
    backups = sorted(workdir.glob("init_boot-stock-*.img")) if workdir.is_dir() else []

    if not backups:
        print(f"  {_color('root-work/ 里没有找到原厂备份。', C.YELLOW)}")
        print()
        print("  可以改用全量包里解出的原厂镜像：")
        print(f"    {_color('fastboot flash init_boot unpacked/rom/init_boot.img', C.CYAN)}")
        pause()
        return

    print("  找到以下原厂备份：")
    for i, p in enumerate(backups, 1):
        size_mb = p.stat().st_size / 1024 / 1024
        # 读取备份元数据里记录的机型，便于核对
        meta_tag = ""
        meta_file = p.with_suffix(".json")
        if meta_file.exists():
            try:
                import json

                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                bits = [
                    meta.get("device_model"),
                    meta.get("device_build_id"),
                ]
                bits = [b for b in bits if b]
                if bits:
                    meta_tag = "  " + _color("[" + " · ".join(bits) + "]", C.DIM)
            except Exception:  # noqa: BLE001
                pass
        print(f"    {_color(str(i), C.CYAN)}) {p.name}  ({size_mb:.1f} MB){meta_tag}")
    print()
    pick = ask("请选择要刷回的镜像", "1")
    try:
        target = backups[int(pick.strip()) - 1]
    except (ValueError, IndexError):
        print(f"  {_color('无效选择', C.YELLOW)}")
        pause()
        return

    print()
    print(f"  即将把 {_color(target.name, C.BOLD)} 刷入 init_boot 分区。")
    print()

    # 机型与版本核对（危险写操作之前）—— 防止把别的机型的镜像刷进来
    snap = mon.refresh_now() if mon else None
    if not _verify_device_for_root(snap, sess, action="回滚 Root"):
        pause()
        return
    print()

    print("  注意：")
    print("    · 刷回后 root 会消失")
    print("    · 会自动把设备切到 fastboot 模式")
    print()
    if not confirm("确认回滚？"):
        print("  已取消。")
        pause()
        return

    # 自动切换模式：设备可能还在 ADB，直接 flash 会空等
    from ..core.root import FlashMode, flash_patched_image

    print()
    print("  正在刷写…")
    ok, detail = flash_patched_image(
        image=target,
        partition="init_boot",
        flash_mode=FlashMode.FASTBOOT,
        on_notice=lambda msg: print(f"    {_color(msg, C.YELLOW)}"),
    )
    if not ok:
        print(f"  {_color('刷写失败：', C.RED)}")
        print(f"    {detail}")
        pause()
        return

    print(f"  {_color('✓', C.GREEN)} 已刷回原厂镜像")
    print()
    print("  请手动重启设备：")
    print(f"    {_color('fastboot reboot', C.CYAN)}")
    pause()
