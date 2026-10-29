"""交互式主菜单。

设计目标：不熟悉命令行的用户也能完成完整的刷机流程。

风格参考用户习惯（1.解全量包 2.线刷全量包），但每一项都对应
一个可脚本化的子命令，便于进阶用户与自动化。
"""

from __future__ import annotations

import os
import sys
import time
from enum import Enum
from pathlib import Path

from .. import __version__
from ..ui import pad, status


class FlashMode(Enum):
    """线刷模式（决定槽位、是否重建逻辑分区、是否只在 FBD 内刷）。

    · NORMAL：只刷当前槽，不重建逻辑分区（常规升级/降级）。
    · STRONG：只刷当前槽，强制重建逻辑分区（修 super/COW/刷写异常）。
    · RELOCK：刷到另一槽，防 A/B 槽不一致（回锁前把两槽洗回原厂）。
    · FBD_ONLY：仅 FastbootD 救砖——进不了 bootloader、只剩 FBD 时，只重建并刷
      super 逻辑分区，绝不碰 bootloader 侧、不 reboot bootloader。
    """

    NORMAL = "normal"
    STRONG = "strong"
    RELOCK = "relock"
    FBD_ONLY = "fbd_only"

    @property
    def label(self) -> str:
        return {
            FlashMode.NORMAL: "普通线刷",
            FlashMode.STRONG: "强力线刷",
            FlashMode.RELOCK: "回锁线刷",
            FlashMode.FBD_ONLY: "仅FBD救砖",
        }[self]

    @property
    def to_opposite_slot(self) -> bool:
        return self is FlashMode.RELOCK

    @property
    def is_fbd_only(self) -> bool:
        return self is FlashMode.FBD_ONLY

    @property
    def recreate_logical(self) -> bool:
        # 强力线刷强制重建逻辑分区；回锁线刷也要重建（否则大镜像直接覆盖
        # 会触发 super 内 resize 不足，如 my_stock 3.8GB）；普通线刷按紫罗兰
        # 「普通模式」语义不重建（直接覆盖，必要时失败会提示改用强力线刷）。
        # 仅FBD救砖也必须重建（同强力线刷，否则 resize 不足）。
        return self is not FlashMode.NORMAL


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
    BLACK = "\033[30m"
    WHITE = "\033[97m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"
    BG_MAGENTA = "\033[45m"
    BG_CYAN = "\033[46m"
    BG_WHITE = "\033[47m"


def _style_to_bg(style: str) -> str:
    """只把红色前景改成红色文字底色（重要提示 / 报错）；其余颜色保持前景色。"""
    return style.replace(C.RED, C.BG_RED + C.WHITE)


def _color(s: str, c: str) -> str:
    return f"{_style_to_bg(c)}{s}{C.RESET}" if sys.stdout.isatty() else s


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
        print(_color(f"    {prompt}", C.RED + C.BOLD))
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


def _safe_pause(msg: str = "按回车继续…") -> None:
    """同 pause，但 Ctrl+C 只当作"继续"，不退出整个程序。"""
    try:
        input(f"\n  {_color(msg, C.DIM)}")
    except (EOFError, KeyboardInterrupt):
        print()


def _safe_refresh(mon) -> None:
    """刷新设备状态，吞掉一切异常（含 KeyboardInterrupt），绝不在这里崩。

    设备刚刷完处于 fastboot/fastbootd，或用户频繁 Ctrl+C 时，
    这里的探测可能阻塞/被中断。刷新失败不影响主流程，直接忽略。
    """
    if mon is None:
        return
    try:
        mon.refresh_now()
    except BaseException:  # noqa: BLE001 — 含 KeyboardInterrupt
        pass


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
        # 包信息缓存（主页头部的型号/版本/分区数要用，避免每次重绘都解析 ZIP）
        self._cached_info = None
        self._cached_info_for: str | None = None

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
        info = self.package_info()
        return info.version_name if info else None

    def package_info(self):
        """解析并缓存当前 ROM 包信息（主页头部展示型号/版本/分区数用）。

        按包路径缓存：路径不变就复用，避免实时重绘时反复解析 ZIP。
        解析失败返回 None（头部会退化为只显示文件名/路径）。
        """
        key = str(self.package) if self.package else None
        if key == self._cached_info_for:
            return self._cached_info
        self._cached_info_for = key
        self._cached_info = None
        if self.package and Path(self.package).exists():
            try:
                from ..core.package import inspect_package

                self._cached_info = inspect_package(Path(self.package))
            except Exception:  # noqa: BLE001
                self._cached_info = None
        return self._cached_info


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
        f"   {_color('2', C.CYAN)}) 预演检查         不写设备，选模式后预演",
        f"   {_color('3', C.CYAN)}) 线刷全量包        选模式：普通/强力/回锁/仅FBD",
        f"   {_color('4', C.CYAN)}) 只刷指定分区      手动选择分区",
        "",
        f"  {_color('─ 安全 ────────────────────────────────────────', d)}",
        f"   {_color('5', C.CYAN)}) 全量备份          全部分区 + GPT + XML（需 root）",
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
        f"   {_color('13', C.CYAN)}) 修复FastbootD     能进BL但进不了FBD时，刷回recovery",
        f"   {_color('14', C.CYAN)}) 关于",
        "",
        f"  {_color('─ 高级（高风险）───────────────────────────────', d)}",
        f"   {_color('15', C.CYAN)}) BL 解锁           解锁 Bootloader（会清空数据）",
        f"   {_color('16', C.CYAN)}) BL 回锁           回锁 Bootloader（需两槽原厂）",
        f"   {_color('17', C.CYAN)}) 诊断/救砖         判断设备该走哪条刷写路径（只读）",
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
        print(_color("  ┌─  已拦截：9008 免授权将被永久熔断 " + "─" * 20, C.RED + C.BOLD))
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
            f"  设备    : "
            f"{_color(info, C.BOLD)}  {_color('[' + label + ']', color)}"
        )
        if snap.slot:
            line += f"  槽位 _{snap.slot}"
        print(line)
        if snap.build_id:
            print(f"              {_color(snap.build_id, C.DIM)}")
        _print_device_facts(snap)
    else:
        print(f"  设备    : {_color('未连接', C.RED)}")
        print(f"              {_color('请检查数据线与 USB 调试', C.DIM)}")

    print()
    if sess.package:
        info = sess.package_info()
        size_txt = ""
        try:
            size_txt = (
                f"  {_color('·', C.DIM)}  "
                f"{sess.package.stat().st_size / (1024 ** 3):.2f} GB"
            )
        except OSError:
            size_txt = ""
        print(f"    ROM 包  : {_color(sess.package.name, C.CYAN)}{size_txt}")
        # 完整路径：让用户一眼看清这是**本地位置**，不是项目默认路径
        print(f"    路径    : {_color(str(sess.package), C.DIM)}")

        facts: list[str] = []
        if info and info.product_name:
            model = info.product_name
            if info.pre_device:
                model += f"（{info.pre_device}）"
            facts.append(f"机型 {_color(model, C.BOLD)}")
        if info and info.android_version:
            facts.append(f"Android {info.android_version}")
        if info and info.version_name:
            facts.append(f"版本 {info.version_name}")
        if facts:
            print("    " + "  ·  ".join(facts))

        status: list[str] = []
        if info and info.manifest:
            status.append(f"分区 {len(info.manifest.partitions)}")
        if sess.unpacked:
            n = len(list(sess.image_dir.glob("*.img")))
            status.append(_color(f"已解包 {n} 个镜像", C.GREEN))
        else:
            status.append(_color("尚未解包", C.DIM))
        print("    " + "  ·  ".join(status))
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
        Level.OK: ("通过", C.GREEN),
        Level.WARN: ("注意", C.YELLOW),
        Level.ERROR: ("错误", C.RED),
        Level.INFO: ("信息", C.BLUE),
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
            print(_color(f"   {it.title}", C.RED + C.BOLD))
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

    print(_color("   环境检测通过", C.GREEN + C.BOLD))
    print()
    # 让用户有机会读完上面的检查结果（旧版这里直接清屏，导致「一闪而过」）。
    # 仅在真实终端暂停；管道/脚本场景跳过，避免吞掉输入。
    if sys.stdin.isatty() and not os.environ.get("LROM_SKIP_ENV_PAUSE"):
        pause("按回车进入主菜单（如上方有『注意/错误』项，请先按修复方法处理）")
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
        "13": _menu_repair_fbd,
        "14": _menu_about,
        "15": _menu_unlock,
        "16": _menu_relock,
        "17": _menu_fbd_diagnose,
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
                _safe_pause()
                continue

            try:
                handler(sess, mon)
            except KeyboardInterrupt:
                # 用户在子流程里按了 Ctrl+C：友好提示后回到菜单，不甩 traceback
                print(f"\n  {_color('已中断当前操作，返回菜单', C.YELLOW)}")
                _safe_pause()
            except Exception as exc:  # noqa: BLE001
                print(f"\n  {_color('出错了:', C.RED)} {exc}")
                _safe_pause()
            finally:
                # 刷新设备状态：吞掉一切异常（含 KeyboardInterrupt），
                # 并给探测加超时保护，避免设备处于 fastboot/fastbootd 时卡住。
                _safe_refresh(mon)
    except KeyboardInterrupt:
        # 主循环层 Ctrl+C（例如在刷新设备时）：优雅退出
        print(f"\n  {_color('已退出', C.YELLOW)}")
        return 0
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
                    f"   这不是当前设备的 ROM 包",
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
            print("    " + status("ok", f"型号匹配: {snap.codename}"))

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
        mark = status("ok", "完成") if res.ok else status("error", "失败")
        detail = f"{res.size_mb:>9,.1f} MB" if res.ok else res.error
        print(f"  [{counter['n']:2}] {res.partition:<18} {mark} {detail:<36}")

    results = unpack_package(p, out, result_cb=on_result)
    write_index(results, out)

    ok = sum(1 for r in results if r.ok)
    all_ok = bool(results) and ok == len(results)

    # 只保留最新一份解包产物：**仅在本次全部成功时**才清理旧目录，
    # 避免解包失败时把上一次可用的产物删掉（数据丢失）。
    if all_ok:
        try:
            from ..core.unpack import prune_other_unpacked

            removed = prune_other_unpacked(out, [sess.work_dir, Path("unpacked")])
            if removed:
                print(_color(f"  已清理 {len(removed)} 个旧的解包目录（只保留本次）", C.DIM))
        except Exception:  # noqa: BLE001
            pass

    print()
    if all_ok:
        print(_color(f"   解包完成：{ok} 个分区", C.GREEN + C.BOLD))
    else:
        print(_color(f"  完成 {ok}/{len(results)}", C.RED))
    pause()


def _choose_flash_mode(dry_run: bool) -> "FlashMode | None":
    """让用户选择线刷模式（普通 / 强力 / 回锁 / 仅FBD救砖）。

    区别：
      · 普通线刷：只刷**当前激活槽**，**不重建**逻辑分区（直接覆盖，对应紫罗兰
        「普通模式」；大镜像空间不足时会提示改用强力线刷）。
      · 强力线刷：只刷**当前激活槽**，但**强制 delete/create 重建**逻辑分区，
        用于 super/COW 异常、逻辑分区刷写失败等（对应紫罗兰「强力线刷」）。
      · 回锁线刷：刷到**另一个槽**，并**重建逻辑分区**，用于配合回锁前把两槽都
        洗回原厂（防止 A/B 槽不一致）。
      · 仅FBD救砖：进不了 bootloader、只剩 FastbootD 时用；只在 FBD 内重建并刷
        super 逻辑分区，**绝不碰 bootloader 侧、不 reboot bootloader**。

    返回 FlashMode；用户取消则返回 None。
    """
    print()
    title = "请选择线刷模式" + ("（预演，不写设备）" if dry_run else "")
    print(f"  {_color(title, C.BOLD)}：")
    print()
    print(
        f"    {_color('1', C.CYAN)}) 普通线刷   "
        f"{_color('只刷当前槽，不重建逻辑分区（常规升级/降级）', C.DIM)}"
    )
    print(
        f"    {_color('2', C.CYAN)}) 强力线刷   "
        f"{_color('只刷当前槽，强制重建逻辑分区（修 super/刷写异常）', C.DIM)}"
    )
    print(
        f"    {_color('3', C.CYAN)}) 回锁线刷   "
        f"{_color('刷到另一槽，防 A/B 槽不一致（回锁前用）', C.DIM)}"
    )
    print(
        f"    {_color('4', C.CYAN)}) 仅FBD救砖  "
        f"{_color('进不了BL时：只在FastbootD刷逻辑分区，绝不碰BL侧', C.DIM)}"
    )
    print(f"    {_color('0', C.CYAN)}) 返回上级")
    print()
    while True:
        ans = ask("请选择", "").strip().lower()
        if ans in ("", "q", "quit", "exit", "0"):
            print(f"  {_color('已取消，返回主菜单', C.DIM)}")
            return None
        if ans == "1":
            return FlashMode.NORMAL
        if ans == "2":
            return FlashMode.STRONG
        if ans == "3":
            return FlashMode.RELOCK
        if ans == "4":
            return FlashMode.FBD_ONLY
        print(f"  {_color('输入无效，请重新选择', C.YELLOW)}")


def _show_flash_preconditions() -> bool:
    """刷机前展示前置条件（adb / fastboot / 驱动(udev) / 权限组 / adb server / USB）。

    返回 False 表示关键工具（adb 或 fastboot）缺失，应中止刷机。

    背景：环境自检只在启动时打印一次，很快被清屏（「一闪而过」）。真正刷机前
    再明确展示一次，并把缺失项用红色底色高亮 + 给出安装命令。
    """
    from ..device.selfcheck import Level, precondition_summary, run_selfcheck

    _clear_screen()
    print()
    print(_color("  ── 刷机前置条件 ──", C.BOLD))
    print()

    items = run_selfcheck()
    summary = precondition_summary(items)

    critical_missing: list[str] = []
    for label, level, value in summary:
        if level is Level.ERROR:
            mark, color = "错误", C.RED
            if label in ("adb", "fastboot"):
                critical_missing.append(label)
        elif level is Level.WARN:
            mark, color = "注意", C.YELLOW
        elif level is Level.OK:
            mark, color = "通过", C.GREEN
        else:
            mark, color = "信息", C.BLUE
        line = f"    {pad(label, 14)} {_color(mark, color)}"
        if value:
            line += f"  {_color(value, C.DIM)}"
        print(line)

    print()
    if critical_missing:
        print("  " + status("error", f"缺少关键工具：{', '.join(critical_missing)}，无法刷机"))
        for it in items:
            if it.level is Level.ERROR and it.key in ("adb", "fastboot"):
                for ln in (it.fix or "").splitlines():
                    print(f"      {ln}")
        print()
        return False

    warns = [s for s in summary if s[1] is Level.WARN]
    if warns:
        print(
            "  "
            + status(
                "warn",
                f"有 {len(warns)} 项提示，可能影响连接稳定性，请留意上面的修复建议",
            )
        )
        print()
    return True


def _flow_flash(
    sess: Session, mon, *, dry_run: bool, pick: bool = False,
) -> None:
    import argparse

    from ..cli import cmd_flash

    if not sess.unpacked:
        print()
        print(f"  {_color('尚未解包，请先执行菜单 1（解全量包）', C.YELLOW)}")
        pause()
        return

    # ── 刷机前置条件（adb / fastboot / 驱动 / 权限 / 连接）──
    if not _show_flash_preconditions():
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

    # 选线刷模式（只刷指定分区时不需要模式，直接用普通默认：当前槽、不重建）
    mode: FlashMode = FlashMode.NORMAL
    if not pick:
        chosen = _choose_flash_mode(dry_run)
        if chosen is None:
            pause()
            return
        mode = chosen

    # 「仅FBD救砖」：要求设备在 FastbootD，且只刷逻辑分区（绝不碰 BL 侧）
    fbd_only_partitions = None
    if mode is FlashMode.FBD_ONLY:
        from ..device.discover import DeviceMode as _DM, detect as _detect

        try:
            _dev = _detect()
        except Exception:  # noqa: BLE001
            _dev = None
        if getattr(_dev, "mode", None) != _DM.FASTBOOTD:
            print()
            print(_color("  仅FBD救砖要求设备已在 FastbootD（is-userspace=yes）。", C.RED + C.BOLD))
            print("  本模式不会替你 reboot 切换模式（以免断掉仅剩 FBD 设备的唯一通道）。")
            print("  请先手动进入 FastbootD 后重试。")
            pause()
            return
        try:
            from ..core.plan import Phase as _Phase, build_plan as _bp
            from ..core.package import inspect_package as _inspect

            _info = _inspect(sess.package) if sess.package else None
            _plan = _bp(_info.manifest, package_path=str(sess.package))
            fbd_only_partitions = [
                op.partition for op in _plan.active_ops if op.phase is _Phase.FASTBOOTD
            ]
        except Exception as exc:  # noqa: BLE001
            print(_color(f"  构建逻辑分区清单失败：{exc}", C.RED))
            pause()
            return
        if not fbd_only_partitions:
            print(_color("  该包没有逻辑分区，无需用仅FBD救砖。", C.YELLOW))
            pause()
            return
        print()
        print(_color("  仅FBD救砖：将只重建并刷下列逻辑分区，绝不碰 bootloader 侧：", C.BOLD))
        print(_color("    " + "  ".join(fbd_only_partitions), C.DIM))
        print()

    ns = argparse.Namespace(
        package=sess.package,
        images=str(sess.image_dir),
        dry_run=dry_run,
        yes=False,
        partitions=fbd_only_partitions,
        allow_dangerous=False,
        force_arb=False,
        no_reboot=True,
        wipe=False,
        both_slots=False,
        # 普通不重建；强力/回锁/仅FBD 强制重建
        no_recreate_logical=not mode.recreate_logical,
        adb_port=None,
        to_opposite_slot=mode.to_opposite_slot,
        slot=None,
        fbd_only=(mode is FlashMode.FBD_ONLY),
    )

    if dry_run:
        print()
        print(_color(f"  模式：{mode.label}（预演，不写入任何数据）", C.BOLD))
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
    ans = ask("清空用户数据（格式化数据分区）？（降级或救砖必须选 y，回车保留数据，输入 q 退出）")
    if ans.strip().lower() in ("q", "quit", "exit"):
        print(f"  {_color('已取消，未写入任何数据', C.DIM)}")
        pause()
        return
    if ans.strip().lower() in ("y", "yes", "是"):
        ns.wipe = True

    # 刷写前「是否备份分区」——可选、非强制。备份需要 root + ADB 模式。
    print()
    bk_ans = ask(
        "刷写前是否备份关键分区？（需 root、设备需在系统/ADB 模式；建议但非强制，直接回车=不备份）",
        "n",
    ).strip().lower()
    if bk_ans in ("y", "yes", "是"):
        import argparse as _argparse

        from ..cli import cmd_backup as _cmd_backup
        from ..core.backup import DEFAULT_BACKUP_PARTITIONS as _KEY_PARTS

        print()
        print(_color("  开始备份关键分区…（若设备不在 ADB 模式或未 root 会失败，可跳过继续）", C.DIM))
        try:
            # 刷写前这一步是「快速关键分区备份」，显式传入精选清单，
            # 不触发默认全备份（全备份请用菜单 5，避免刷机前无谓地占十几 GB）。
            _cmd_backup(
                _argparse.Namespace(
                    partitions=list(_KEY_PARTS), output="backups", adb_port=None
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(_color(f"  ! 备份未完成：{exc}（可继续刷写）", C.YELLOW))
        print()

    # 打印本模式的关键差异，避免用户误判（尤其回锁线刷会切槽）
    print()
    print(f"  {_color('已选模式：', C.BOLD)}{mode.label}")
    if mode is FlashMode.RELOCK:
        print(
            _color(
                "    · 将刷到【另一个槽】并切换活动槽，同时重建逻辑分区；"
                "回锁前需连刷两次把两槽都洗回原厂",
                C.YELLOW,
            )
        )
    elif mode is FlashMode.FBD_ONLY:
        print(_color("    · 只在 FastbootD 内重建并刷逻辑分区；绝不碰 bootloader 侧、不 reboot bootloader", C.YELLOW))
    elif mode is FlashMode.STRONG:
        print(_color("    · 将强制 delete/create 重建逻辑分区（修 super/COW 异常）", C.YELLOW))
    else:
        print(_color("    · 只刷当前激活槽，不重建逻辑分区（大镜像若空间不足请改用强力线刷）", C.DIM))
    print()

    cmd_flash(ns)
    pause()


def _menu_backup(sess: Session, mon) -> None:
    """菜单 5：备份设备分区（**默认全量备份**，对齐紫罗兰「备份字库」）。

    默认行为 = 全备份：
      · 回读设备**实际存在的全部分区**（不再只备精选清单）
      · 生成主/备 GPT + RawProgram XML（紫罗兰「备份字库」同款产物）
      · 默认排除 userdata/cache（用户数据、体积大且无回滚价值）
    需要 ADB 开机模式 + shell root。
    """
    import argparse

    from ..cli import cmd_fulldump
    from ..ui import status

    print()
    print(f"  {_color('备份设备分区（全量）', C.BOLD)}")
    print()
    print(f"  将备份 : {_color('设备全部分区', C.BOLD)} + 主/备 GPT + RawProgram XML")
    print("           默认排除 userdata / cache（用户数据）")
    print(
        "  输出到 : "
        + _color("backups/机型_日期/", C.CYAN)
        + "（如 backups/OPD2404_20261029-013451/）"
    )
    print()
    print("  " + status("warn", "全量备份耗时较久，约 10–20 分钟（取决于分区大小），请耐心等待，勿拔线"))
    print()

    if not confirm("开始全量备份？"):
        print(f"  {_color('已取消', C.DIM)}")
        pause()
        return

    rc = cmd_fulldump(
        argparse.Namespace(
            output="backups",
            include_super=True,
            exclude=None,
            adb_port=None,
        )
    )
    if rc != 0:
        print("  " + status("error", "全量备份未完整完成"))
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

    print("  " + status("ok", "已发送重启命令"))
    pause()


def _menu_selfcheck(sess: Session, mon) -> None:
    import argparse

    from ..cli import cmd_selfcheck

    cmd_selfcheck(argparse.Namespace())
    pause()


def _menu_unlock(sess: "Session", mon) -> None:
    """菜单 14：BL 解锁（会清空全部数据）。"""
    import argparse

    from ..cli import cmd_unlock

    print()
    print(f"  {_color('BL 解锁', C.BOLD)}")
    print()
    print(f"  {_color('解锁会清空全部用户数据', C.RED + C.BOLD)}（应用、照片、账号全部重置）。")
    print("  解锁后每次开机都会显示 bootloader 已解锁的警告。")
    print()
    print("  解锁前必须完成：")
    print("    1) 设置 → 关于手机 → 连点版本号，开启开发者选项")
    print("    2) 开发者选项 → 打开「OEM 解锁」和「USB 调试」")
    print("    3) 新机（ColorOS 16+，如一加 15）先申请「深度测试」并通过审核")
    print("    4) 设备进入 fastboot：adb reboot bootloader")
    print()
    print(f"  {_color('按 q 可取消', C.DIM)}")
    print()

    if not confirm("已了解会清空数据，继续？"):
        print(f"  {_color('已取消', C.DIM)}")
        pause()
        return

    # 旧设备用 oem unlock？给一次选择（默认 flashing unlock）
    legacy = False
    print()
    print(f"  {_color('解锁命令', C.BOLD)}：")
    print(f"    {_color('1', C.CYAN)}) fastboot flashing unlock（现代设备，推荐）")
    print(f"    {_color('2', C.CYAN)}) fastboot oem unlock（旧设备，AOSP 8.0 之前）")
    print()
    if ask("请选择", "1").strip() == "2":
        legacy = True

    print()
    rc = cmd_unlock(
        argparse.Namespace(
            dry_run=False,
            yes=False,
            legacy=legacy,
            adb_port=None,
        )
    )
    if rc != 0:
        print()
        print(f"  {_color('解锁未完成。', C.YELLOW)}")
    pause()


def _menu_relock(sess: "Session", mon) -> None:
    """菜单 14：BL 回锁（高风险）。

    前提：两个槽的关键分区都必须是原厂签名镜像。
    「线刷全量包」（菜单 3）现提供「回锁线刷」模式，会刷到**另一个槽**
    并切换活动槽。连刷两次（每次自动切到另一槽）即可保证两槽都洗回
    原厂包。刷完后用本菜单回锁。

    交互：
      1) 我已线刷/安装好两个槽，直接回锁
      0) 退出
    """
    import argparse

    from ..cli import cmd_relock

    print()
    print(f"  {_color('BL 回锁', C.RED + C.BOLD)}")
    print()
    print(f"  {_color(' 回锁前提', C.RED + C.BOLD)}：两个槽的关键分区都必须是")
    print("    本机的原厂签名镜像（不是 root / 三方 ROM / 跨区包）。")
    print("    否则回锁后设备会变砖，且没有解锁通道自救。")
    print()
    print(f"  {_color('回锁会清空全部用户数据。', C.YELLOW)}")
    print()
    print("  如何保证两槽都是原厂：")
    print(f"    · 用菜单 3「线刷全量包」选「回锁线刷」模式刷两次——它会自动刷到")
    print(f"      另一个槽，每次刷完活动槽切换，两次之后两槽都是这次刷的原厂包。")
    print(f"    · 或你已在设备本地安装过两次系统 ROM。")
    print()
    print("  请选择方式：")
    print(f"    {_color('1', C.CYAN)}) 我已刷好两个槽，直接回锁")
    print(f"    {_color('0', C.CYAN)}) 退出")
    print()
    choice = ask("请选择", "0").strip().lower()
    if choice in ("0", "q", "quit", "exit"):
        print(f"  {_color('已取消', C.DIM)}")
        pause()
        return
    if choice != "1":
        print(f"  {_color('无效选择，已取消', C.YELLOW)}")
        pause()
        return

    # 需要完整包做包适配性校验（不写设备，仅核对机型/包类型）
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

    print()
    rc = cmd_relock(
        argparse.Namespace(
            package=p,
            images=None,
            dry_run=False,
            yes=False,
            adb_port=None,
            check_model=False,
            skip_flash=True,
        )
    )
    if rc != 0:
        print()
        print(f"  {_color('回锁未完成。', C.YELLOW)}")
    pause()


def _menu_fbd_diagnose(sess: "Session", mon) -> None:
    """菜单 16：诊断/救砖 —— 判断设备该走哪条刷写路径（只读，不写设备）。

    这是「仅 FastbootD」「修复 FastbootD」救砖路径的分流探针：
    读 getvar is-userspace 判断在 bootloader 还是 FastbootD，给出建议。
    **纯只读**：不 reboot、不 flash、不 erase（避免误伤仅剩 FBD 的设备）。
    """
    from ..core.flash import BlSymptom, FbdRecommendation, diagnose_fbd
    from ..device.discover import DeviceMode, detect
    from ..device.runner import AdbRunner as _Adb, FastbootRunner as _Fb

    print()
    print(f"  {_color('诊断 / 救砖分流', C.BOLD)}")
    print(_color("  只读检查，不写设备、不重启。", C.DIM))
    print()

    # 重新探测一次真实设备状态（监测快照可能滞后/为空，见 DEVNOTES 1.4）。
    try:
        dev = detect()
    except Exception:  # noqa: BLE001
        dev = None
    mode = getattr(dev, "mode", None)

    if mode is None:
        print(_color("  未检测到设备。请检查数据线 / USB 调试 / adb、fastboot。", C.YELLOW))
        pause()
        return

    # 「BL 是否可用 / 症状」是 FBD_ONLY / RESCUE_ABL 判定的关键输入。
    # 只读无法可靠判断，必须问用户（绝不能发 reboot bootloader 去试探，会失联）。
    bl_dead = False
    bl_symptom = BlSymptom.NONE
    if mode == DeviceMode.FASTBOOTD:
        print("  设备当前在 FastbootD。请描述这台机的 Bootloader 情况：")
        print(f"    {_color('1', C.CYAN)}) Bootloader 正常（能进）")
        print(f"    {_color('2', C.CYAN)}) Bootloader 能进但「秒退」（ColorOS 16/17 封 fastboot 典型）")
        print(f"    {_color('3', C.CYAN)}) 完全进不了 Bootloader（丢 OCDT / 格机 / 换主板）")
        pick = ask("请选择", "1").strip()
        if pick == "2":
            bl_symptom = BlSymptom.INSTANT_EXIT
        elif pick == "3":
            bl_dead = True

    # 设备在 bootloader 时：可选「实测能否进 FastbootD」以判断是否需要「修复FBD」。
    # 这是唯一允许 reboot 的场景（BL 已好，进不去 FBD 也留在 BL，不失联）。
    probe_fbd = False
    if mode == DeviceMode.FASTBOOT:
        print("  是否实测「能否进入 FastbootD」？")
        print(_color("    会执行一次 fastboot reboot fastboot 探测（安全：进不去也只是留在 bootloader）。", C.DIM))
        ans = ask("实测 FBD 可达性？（y/N）", "n").strip().lower()
        probe_fbd = ans in ("y", "yes", "是")

    adb = _Adb() if mode == DeviceMode.ADB else None
    fb = None if mode == DeviceMode.ADB else _Fb()

    diag = diagnose_fbd(
        adb=adb, fb=fb, bl_declared_dead=bl_dead, bl_symptom=bl_symptom,
        probe_fbd=probe_fbd,
    )

    print()
    print(f"  {_color('诊断结果：', C.BOLD)}")
    for line in diag.to_lines():
        print(f"    {line}")
    print()

    # 附加：内测/Beta 版倾向告警 + ARB 只读预检（仅 ADB 态可读，纯只读）
    if mode == DeviceMode.ADB:
        _print_beta_and_arb_warnings(adb, getattr(dev, "serial", None))
        print()

    # 给出下一步指引（不自动执行，交用户决定）
    if diag.recommendation is FbdRecommendation.FBD_ONLY:
        print(_color("  建议：用菜单 3「线刷全量包」选「仅FBD救砖」模式执行。", C.YELLOW))
        print("    该模式只刷逻辑分区、绝不碰 bootloader 侧分区、不会替你 reboot 切换模式。")
    elif diag.recommendation is FbdRecommendation.RESCUE_ABL:
        print(_color("  建议：用 FastbootD 刷【旧版 abl】救回 bootloader —— 但本版本【不提供一键入口】。", C.YELLOW))
        print("    原因：刷错 abl / 触发 ARB 反回滚可能硬砖，风险高，故只给手动命令。")
        print("    手动流程：fastboot flash abl_<当前槽> <旧版abl.img>（只刷当前槽），再 fastboot reboot bootloader 验证。")
        print(_color("    各机型 abl 不通用；务必先做 ARB 只读预检（见上），ARB 熔断 / 新 abl 锁死的机型此路不通。", C.DIM))
    elif diag.recommendation is FbdRecommendation.REPAIR_FBD:
        print(_color("  建议：用菜单 13「修复FastbootD」—— 核心是刷回 recovery.img（fastbootd 寄生在 recovery 里）。", C.YELLOW))
        print("    注意：仅「有独立 recovery 分区」的机型适用；刷 recovery 本身不碰 ARB，相对安全。")
    elif diag.recommendation is FbdRecommendation.NORMAL:
        if diag.state.value == "adb":
            print(_color("  设备正常（已开机）。要刷机请先重启到 fastboot/fastbootd。", C.GREEN))
        else:
            print(_color("  FBD 环境正常，用菜单 2/3 的普通或强力线刷即可。", C.GREEN))
    elif diag.recommendation is FbdRecommendation.UNKNOWN:
        print(_color("  未能判断，请确认设备已连接（adb / fastboot 均可用）。", C.YELLOW))
    pause()


def _print_beta_and_arb_warnings(adb, serial) -> None:
    """只读打印：内测/Beta 版倾向 + 设备 ARB 回滚索引（仅 ADB 态可用）。"""
    # 1) 内测版倾向
    try:
        from ..device.discover import detect_beta_channel

        bi = detect_beta_channel(adb, serial)
        if bi.suspicion == "beta_likely":
            print(_color("   疑似【内测/Beta 版】系统：", C.RED + C.BOLD))
            for r in bi.reasons:
                print(f"    · {r}")
            print(_color("    内测版是 fastboot 被封的高危触发源，且内测→正式/降级可能触发 ARB。请谨慎。", C.YELLOW))
        elif bi.suspicion == "stable_likely":
            print(f"  内测/正式判定：{_color('偏正式版', C.GREEN)}（{', '.join(bi.reasons)}）")
            print(_color("    注：正式版 13/13T 等新版也已限制 fastboot，别只看是否内测。", C.DIM))
        else:
            print(f"  内测/正式判定：{_color('无法确定', C.YELLOW)}（请以 设置→关于→尝鲜/内测 渠道为准）")
    except Exception:  # noqa: BLE001
        pass

    # 2) ARB 只读预检（读设备回滚索引）
    try:
        from ..core.arb import arb_root_hint, read_device_arb_adb

        arb = read_device_arb_adb(adb, serial)
        if arb is not None and getattr(arb, "arb_index", None) is not None:
            idx = arb.arb_index
            level = C.RED if idx >= 1 else C.GREEN
            print(f"  设备 ARB 回滚索引：{_color(str(idx), level)}"
                  + (_color("   已熔断，任何降级/刷旧 abl 都可能硬砖！", C.RED) if idx >= 1 else ""))
        else:
            print(_color("  ARB 回滚索引：读不到。", C.DIM))
            print(_color(f"    {arb_root_hint()}", C.DIM))
    except Exception:  # noqa: BLE001
        print(_color("  ARB 预检未完成（读不到设备回滚索引）。", C.DIM))


def _menu_repair_fbd(sess: "Session", mon) -> None:
    """菜单 13：修复 FastbootD —— 能进 bootloader 但进不了 FastbootD 时，刷回 recovery。

    机制：fastbootd 寄生在 recovery 里，recovery 坏了 → 进不了 FBD → 刷回 recovery。
    安全：只刷 recovery 分区，不碰 abl/xbl/ARB/persist/ocdt；刷错通常是 bootloop/卡
    fastboot，可刷回，非硬砖（有社区案例支撑）。
    """
    from pathlib import Path

    from ..core.flash import repair_fbd
    from ..device.discover import DeviceMode, detect
    from ..device.runner import FastbootRunner

    print()
    print(f"  {_color('修复 FastbootD', C.RED + C.BOLD)}")
    print(_color("  fastbootd 跑在 recovery 里；刷回 recovery 即可让 fastbootd 恢复。", C.DIM))
    print()

    # 需设备在 bootloader（本修复站在 bootloader 通道上）
    try:
        dev = detect()
    except Exception:  # noqa: BLE001
        dev = None
    mode = getattr(dev, "mode", None)
    if mode is None:
        print(_color("  未检测到设备。请检查数据线 / 驱动 / fastboot。", C.YELLOW))
        pause()
        return
    if mode != DeviceMode.FASTBOOT:
        print(_color("  本修复需在 Fastboot（bootloader）模式下执行。", C.RED + C.BOLD))
        print("  当前设备不在 bootloader（请先 `adb reboot bootloader`）。")
        pause()
        return

    # 选 recovery.img：默认用当前解包目录的 recovery.img，也可输入自定义路径
    if not sess.unpacked:
        print(f"  {_color('尚未解包（无默认镜像）。可手动指定 recovery.img 路径。', C.YELLOW)}")
    default_img = ""
    try:
        if sess.unpacked:
            cand = Path(sess.image_dir) / "recovery.img"
            if cand.exists():
                default_img = str(cand)
    except Exception:  # noqa: BLE001
        default_img = ""
    if default_img:
        print(f"  默认镜像（当前解包目录）：{default_img}")
    print("  请输入 recovery.img 路径（直接回车用默认；无默认则必须输入；输入 q 取消）")
    raw = ask("recovery.img 路径", default_img)
    if raw.strip().lower() in ("q", "quit", "exit") or not raw.strip():
        print(f"  {_color('已取消，未写入任何设备', C.DIM)}")
        pause()
        return
    recovery_img = Path(raw.strip()).expanduser()
    if not recovery_img.exists():
        print(_color(f"  recovery 镜像不存在：{recovery_img}", C.RED))
        pause()
        return

    print(f"  recovery 镜像：{recovery_img}（{recovery_img.stat().st_size / 2**20:.0f} MB）")
    print(_color("   建议用【与设备当前系统同版本】的官方全量包里的 recovery.img。", C.YELLOW))
    print(_color("   刷错代价：通常 bootloop / 卡 fastboot（可刷回），非硬砖；但请尽量用匹配版本。", C.DIM))
    print()
    ans = ask("确认开始修复 FastbootD？输入 YES 继续（其他取消）", "")
    if ans.strip() != "YES":
        print(f"  {_color('已取消，未写入任何设备', C.DIM)}")
        pause()
        return

    # 版本信息（尽力而为）
    pkg_info = None
    try:
        from ..core.package import inspect_package

        if sess.package and Path(sess.package).exists():
            pkg_info = inspect_package(Path(sess.package))
    except Exception:  # noqa: BLE001
        pass
    # bootloader 读不到系统版本；尝试从包的 metadata 得版本作参照
    dev_version = getattr(pkg_info, "display_os_version", None) if pkg_info else None

    def on_notice(msg: str) -> None:
        print(f"    {msg}")

    res = repair_fbd(
        FastbootRunner(), recovery_img,
        package_info=pkg_info, device_version=dev_version,
        device_model=getattr(dev, "model", None), on_notice=on_notice,
    )
    print()
    if res.ok and res.flashed:
        if res.verified_fbd:
            print(_color("   修复成功：FastbootD 已恢复。", C.GREEN + C.BOLD))
        else:
            print(_color("   recovery 已刷入。", C.GREEN))
        print()
        print(
            "   "
            + status(
                "important",
                "救砖提示：后续刷完全量包后，请在首次开机前格式化数据分区（清除用户数据），否则可能无法开机",
            )
        )
    elif res.ok and not res.flashed:
        print(_color("  fastbootd 本身正常，无需修复。", C.GREEN))
    else:
        print(_color("   修复未完成。", C.RED + C.BOLD))
        if res.error:
            print(f"    {res.error}")
    if res.warnings:
        print()
        for w in res.warnings:
            print(_color(f"   {w}", C.YELLOW))
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
        print(_color("   未检测到设备", C.RED + C.BOLD))
        print("  请连接设备并确认能识别（./lrom devices）。")
        return False

    if snap.mode != "adb":
        print(_color(f"   设备当前处于 {snap.mode} 模式", C.RED + C.BOLD))
        print(f"  {action}需要读取设备信息，请先开机启动再操作。")
        return False

    # BL 锁
    if snap.locked == "1":
        print(_color("   Bootloader 未解锁，无法刷写", C.RED + C.BOLD))
        print("  请先完成 OEM 解锁。注意：解锁会清空全部用户数据。")
        return False
    if snap.locked == "":
        print(_color("   无法确认 Bootloader 是否已解锁，请自行确认", C.YELLOW))

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
            print(_color("   当前 ROM 包与设备机型不一致，拒绝执行", C.RED + C.BOLD))
            print()
            print(f"    设备      : {snap.model or '?'}（代号 {snap.codename or '?'}）")
            print(f"    当前包适用: {info.product_name or '?'}（代号 {info.pre_device or '?'}）")
            print()
            print("    用别的机型的 init_boot 做 root 会直接导致无法开机。")
            print("    请先解包与你设备匹配的 ROM 包。")
            return False
        if dev_key and pkg_key:
            print("  " + status("ok", f"机型匹配: {snap.model or snap.codename}"))

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

    # ---- 选择 patch 方式 ----
    print(f"  {_color('选择 Root 方式：', C.BOLD)}")
    print(f"    {_color('1', C.CYAN)}) 脱机一键（推荐）  PC 自动 patch，最后只需装管理器 APK")
    print(f"    {_color('2', C.CYAN)}) APP 手动         推镜像到手机，用管理器手动修补")
    print(f"    {_color('0', C.CYAN)}) 返回")
    pick = ask("请选择", "1").strip()
    if pick in ("0", "q", "quit", "exit"):
        print(f"  {_color('已取消，返回主菜单', C.DIM)}")
        pause()
        return
    method = "app" if pick == "2" else "offline"
    print()

    # ---- 根管理器 APK（内置下载；APP 方式同样需要管理器）----
    mgr = _ensure_root_manager(mon)
    if mgr is _ABORT:
        print(f"  {_color('已取消，返回主菜单', C.DIM)}")
        pause()
        return
    print()

    print(f"  {_color('提示', C.BOLD)}：")
    print("    · 刷写前会把原厂镜像备份到 root-work/，可随时回滚")
    if method == "offline":
        print("    · 脱机一键：PC 自动下载 ksud 引擎并 patch init_boot，最后装管理器即 Root")
    else:
        print("    · APP 方式：按提示在管理器里选择刚推送的镜像进行修补")
    print("    · Lrom 不会自动重启设备")
    print()

    # 若选的是不支持脱机一键的管理器（SukiSU/APatch/Magisk），降级为 APP 手动方式
    offline_engines = {"ksu", "ksu-next"}
    engine_key = _LAST_MANAGER_KEY if _LAST_MANAGER_KEY in offline_engines else "ksu"
    if method == "offline" and _LAST_MANAGER_KEY and _LAST_MANAGER_KEY not in offline_engines:
        print(_color(f"  注意：{_LAST_MANAGER_KEY} 不支持脱机一键 patch，已改为 APP 手动方式。", C.YELLOW))
        method = "app"
    print()

    rc = cmd_root(
        argparse.Namespace(
            image=image,
            method=method,
            partition=None,
            flash_mode=None,
            kmi=None,
            dry_run=False,
            yes=False,
            workdir="root-work",
            adb_port=None,
            ksud=None,
            engine=engine_key,
        )
    )
    if rc != 0:
        print()
        print(f"  {_color('流程未完成。', C.YELLOW)}")
    pause()


#: 用户在交互中选择「退出整个流程」时返回的哨兵值
_ABORT = object()

#: _ensure_root_manager 最近一次选中的管理器 key（ksu / ksu-next / sukisu / apatch），
#: 供 _menu_root 决定脱机 patch 引擎。
_LAST_MANAGER_KEY: str | None = None


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
    global _LAST_MANAGER_KEY
    _LAST_MANAGER_KEY = key

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
        print(f"  {_color(' 已安装', C.GREEN)}")
        # 装进设备后删掉本地包，避免残留
        try:
            apk.unlink()
            print(f"  {_color(' 已删除本地安装包', C.DIM)}")
        except OSError as exc:  # noqa: BLE001
            print(f"  {_color(f'! 删除失败: {exc}', C.YELLOW)}")
            print(f"    {apk.resolve()}")
    elif status == "no_device":
        print(f"  {_color('! 未检测到设备', C.YELLOW)}")
        print(f"    {detail}")
        print(f"  {_color('本机位置', C.DIM)}: {apk.resolve()}")
    else:
        print(f"  {_color(' 安装失败', C.RED)}")
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
    """菜单 9：回滚 Root（刷回原厂 init_boot）。

    默认直接使用「解包出来的 init_boot.img」（版本最可靠），不再让用户在多个
    备份里挑选。做版本对比：若解包镜像的版本与设备当前系统不一致，红字告警
    （刷入不匹配的 init_boot 可能无法开机）。
    解包镜像不存在时，才回退到 root-work/ 里最近一次的原厂备份。
    """
    from pathlib import Path

    print()
    print(f"  {_color('回滚 Root', C.BOLD)}")
    print()

    # ---- 选目标镜像：优先解包产物，其次最近一次 root 原厂备份 ----
    target: Path | None = None
    source_desc = ""
    pkg_ver = None
    try:
        if sess.unpacked:
            cand = Path(sess.image_dir) / "init_boot.img"
            if cand.exists():
                target = cand
                source_desc = "解包产物（全量包）"
                pkg_ver = sess._package_version()
    except Exception:  # noqa: BLE001
        target = None

    if target is None:
        workdir = Path("root-work")
        # 优先找独立副本（.img）；没有则读指纹记录（.json）里的 source 路径
        backups = sorted(
            workdir.glob("init_boot-stock-*.img"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if workdir.is_dir() else []
        metas = sorted(
            workdir.glob("init_boot-stock-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if workdir.is_dir() else []
        if backups:
            target = backups[0]
            source_desc = "root-work 最近一次原厂备份"
            meta = target.with_suffix(".json")
            if meta.exists():
                try:
                    import json

                    pkg_ver = json.loads(meta.read_text(encoding="utf-8")).get(
                        "source_version"
                    )
                except Exception:  # noqa: BLE001
                    pass
        elif metas:
            # 只记录了指纹（未复制副本）：从记录里取源镜像路径
            import json

            for meta in metas:
                try:
                    data = json.loads(meta.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                src = data.get("source")
                if src and Path(src).exists():
                    target = Path(src)
                    source_desc = "root-work 记录的源镜像（解包产物）"
                    pkg_ver = data.get("source_version")
                    break

    if target is None:
        print(f"  {_color('找不到可回滚的原厂 init_boot。', C.YELLOW)}")
        print()
        print("  请先执行菜单 1（解全量包），解出 init_boot 后再回滚；")
        print("  或用命令行指定：")
        print(f"    {_color('fastboot flash init_boot <原厂init_boot.img>', C.CYAN)}")
        pause()
        return

    print(f"  回滚镜像 : {target}   {_color('(' + source_desc + ')', C.DIM)}")

    # ---- 版本对比（解包镜像 vs 设备当前系统）----
    dev_ver = None
    try:
        from ..device.discover import detect

        dev = detect()
        dev_ver = getattr(dev, "build_id", None) if dev else None
    except Exception:  # noqa: BLE001
        dev_ver = None
    if pkg_ver or dev_ver:
        print(f"  镜像版本 : {pkg_ver or '未知'}")
        print(f"  设备版本 : {dev_ver or '未知'}")
        if pkg_ver and dev_ver and (str(pkg_ver) not in str(dev_ver)) and (
            str(dev_ver) not in str(pkg_ver)
        ):
            print()
            print(_color("   版本不一致：镜像与设备当前系统版本不同。", C.RED + C.BOLD))
            print(_color("    刷入不匹配的 init_boot 可能导致无法开机。建议用与设备相同的全量包。", C.RED))
    print()

    # 机型与版本核对（危险写操作之前）
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

    print("  " + status("ok", "已刷回原厂镜像"))
    print()
    print("  请手动重启设备：")
    print(f"    {_color('fastboot reboot', C.CYAN)}")
    pause()
