"""环境自检。

Linux 用户使用刷机工具的第一道坎通常不是"不会用"，而是：
  - adb/fastboot 没装
  - 装了但没 udev 规则，普通用户没权限
  - adb server 被 root 启动过，之后普通用户连不上
  - 用户已经登录但没重登，组权限没生效

这个命令把这些一次性查清楚，并给出可以直接复制的修复命令。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum

from .tools import CONFIG_PATH, find_tool, load_config


class Level(Enum):
    OK = "ok"
    WARN = "warn"
    ERROR = "error"
    INFO = "info"


@dataclass
class CheckItem:
    level: Level
    title: str
    detail: str = ""
    fix: str = ""


#: 已经安装 udev 规则时，设备节点通常会属于这些组之一
_CANDIDATE_GROUPS = ("plugdev", "adbusers", "wheel", "uucp")


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def _user_groups() -> set[str]:
    try:
        import grp

        groups = set()
        for gid in os.getgroups():
            try:
                groups.add(grp.getgrgid(gid).gr_name)
            except KeyError:
                pass
        return groups
    except Exception:
        return set()


def _udev_rules_present() -> tuple[bool, str]:
    """检查是否安装了 android udev 规则。"""
    paths = [
        "/etc/udev/rules.d/51-android.rules",
        "/usr/lib/udev/rules.d/51-android.rules",
        "/lib/udev/rules.d/51-android.rules",
        "/usr/lib/udev/rules.d/51-edl.rules",
    ]
    for p in paths:
        if os.path.exists(p):
            return True, p
    # 有些发行版把规则放在 android-sdk-platform-tools-common 包里
    code, out = _run(["sh", "-c", "ls /usr/lib/udev/rules.d/ 2>/dev/null | grep -i android"])
    if code == 0 and out.strip():
        return True, out.strip().splitlines()[0]
    return False, ""


def _adb_servers() -> list[tuple[int, str, str]]:
    """列出所有运行中的 adb server：(pid, user, 命令行)。"""
    import pwd

    out_rows: list[tuple[int, str, str]] = []
    code, out = _run(["pgrep", "-a", "^adb$"])
    if code != 0:
        code, out = _run(["sh", "-c", "ps -eo pid,user,args | grep '[a]db -L tcp'"])
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            owner = pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
        except Exception:
            owner = parts[1]
        out_rows.append((pid, owner, parts[-1] if len(parts) > 2 else ""))
    return out_rows


def _adb_server_owner_ok(adb_path: str) -> tuple[bool, str]:
    """检查 adb server 是否被其他用户（通常是 root）占用。"""
    code, out = _run(["pgrep", "-a", "adb"])
    if code != 0 or not out.strip():
        return True, "未运行"
    for line in out.splitlines():
        # 形如 "1234 adb -L tcp:5037 fork-server server"
        if "fork-server" not in line and "server" not in line:
            continue
        try:
            pid = int(line.split()[0])
        except (ValueError, IndexError):
            continue
        try:
            import pwd

            owner = pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
        except Exception:
            continue
        me = os.environ.get("USER") or pwd.getpwuid(os.getuid()).pw_name
        if owner != me:
            return False, f"adb server 属于用户 {owner}（你是 {me}）"
        return True, f"属于用户 {owner}"
    return True, "未发现冲突"


def _usb_android_devices() -> list[str]:
    """列出当前 USB 上的 Android 设备（lsusb）。"""
    code, out = _run(["lsusb"])
    if code != 0:
        return []
    found = []
    for line in out.splitlines():
        low = line.lower()
        if any(
            k in low
            for k in ("google", "oneplus", "oppo", "realme", "xiaomi", "qualcomm", "android")
        ):
            found.append(line.strip())
    return found


def _blocked_usb() -> list[str]:
    """检查是否有设备但没有访问权限（lsusb 能看到，adb 看不到）。"""
    code, out = _run(["sh", "-c", "ls -l /dev/bus/usb/*/* 2>/dev/null | head -50"])
    return []


#: 低于这个版本刷 super 等大分区会明显变慢、易超时。
#: Google Platform-Tools 35.0.0（2024-02）起优化了下载吞吐。
MIN_FASTBOOT_VERSION = 35.0


def _parse_platform_tools_version(text: str) -> float | None:
    """从 ``fastboot version 34.0.5-debian`` 这类输出里取出主版本号。"""
    import re

    m = re.search(r"version\s+(\d+)\.(\d+)", text)
    if not m:
        return None
    try:
        return float(f"{m.group(1)}.{m.group(2)}")
    except ValueError:
        return None


def run_selfcheck() -> list[CheckItem]:
    items: list[CheckItem] = []

    # ---- Python ----
    import sys

    if sys.version_info >= (3, 10):
        items.append(
            CheckItem(Level.OK, "Python 版本", f"{sys.version_info.major}.{sys.version_info.minor}")
        )
    else:
        items.append(
            CheckItem(
                Level.ERROR,
                "Python 版本过低",
                f"{sys.version_info.major}.{sys.version_info.minor}，需要 >= 3.10",
                "安装 Python 3.10 或更高版本",
            )
        )

    # ---- adb / fastboot ----
    tools: dict[str, str | None] = {}
    for name in ("adb", "fastboot"):
        try:
            path = find_tool(name)
            tools[name] = path
            code, out = _run([path, "version" if name == "adb" else "--version"])
            raw = out.strip().splitlines()[0] if out.strip() else "版本未知"
            # adb 输出版本格式是 "Android Debug Bridge version 1.0.41"，
            # 不代表 platform-tools 版本，用 adb --version 才准
            ver_num = None
            if name == "fastboot":
                ver_num = _parse_platform_tools_version(raw)
            hint = f"{path}\n    {raw}"
            if ver_num is not None and ver_num < MIN_FASTBOOT_VERSION:
                items.append(
                    CheckItem(
                        Level.WARN,
                        f"{name} 版本偏旧",
                        hint,
                        "建议升级到 Google 官方 Platform-Tools "
                        f"{MIN_FASTBOOT_VERSION:.0f}.0.0 或更新："
                        "\n    旧版刷写 super 等大分区会很慢，容易超时失败。"
                        "\n    https://developer.android.com/tools/releases/platform-tools",
                    )
                )
            else:
                items.append(CheckItem(Level.OK, f"{name} 已安装", hint))
        except Exception as exc:
            tools[name] = None
            items.append(
                CheckItem(
                    Level.ERROR,
                    f"{name} 未安装",
                    str(exc).splitlines()[0],
                    "Debian/Ubuntu/Kali: sudo apt install adb fastboot\n"
                    "    Arch:               sudo pacman -S android-tools",
                )
            )

    # ---- 配置文件 ----
    if CONFIG_PATH.exists():
        cfg = load_config()
        items.append(
            CheckItem(Level.INFO, "配置文件", f"{CONFIG_PATH}\n    {cfg}")
        )

    # ---- udev 规则 ----
    has_rules, rule_path = _udev_rules_present()
    if has_rules:
        items.append(CheckItem(Level.OK, "udev 规则", rule_path))
    else:
        items.append(
            CheckItem(
                Level.WARN,
                "未找到 Android udev 规则",
                "没有规则时，非 root 用户可能无法访问设备",
                "Debian/Ubuntu/Kali: sudo apt install android-sdk-platform-tools-common\n"
                "    或手动安装 51-android.rules 后：sudo udevadm control --reload-rules && sudo udevadm trigger",
            )
        )

    # ---- 当前用户组 ----
    groups = _user_groups()
    need = [g for g in _CANDIDATE_GROUPS if g in groups]
    if has_rules:
        if need:
            items.append(
                CheckItem(Level.OK, "用户组权限", f"已在组: {', '.join(need)}")
            )
        else:
            items.append(
                CheckItem(
                    Level.WARN,
                    "当前用户不在设备访问组中",
                    f"你的组: {', '.join(sorted(groups)) or '未知'}",
                    "sudo usermod -aG plugdev $USER\n"
                    "    ⚠️ 需要【注销后重新登录】才生效（重新插拔不够）",
                )
            )

    # ---- adb server ----
    if tools.get("adb"):
        servers = _adb_servers()
        if len(servers) > 1:
            ports = []
            for _pid, _owner, cmd in servers:
                for tok in cmd.split():
                    if tok.startswith("tcp:"):
                        ports.append(tok[4:])
            detail = "检测到 " + str(len(servers)) + " 个 adb server 同时在运行"
            if ports:
                detail += "（端口: " + ", ".join(ports) + "）"
            items.append(
                CheckItem(
                    Level.WARN,
                    "多个 adb server 冲突",
                    detail + "\n    它们会互相抢占设备，导致时好时坏",
                    "adb kill-server && pkill -f 'adb -L tcp'\n"
                    "    然后重新运行 lrom（正常只应有 1 个 server，端口 5037）",
                )
            )
        else:
            ok, detail = _adb_server_owner_ok(tools["adb"])  # type: ignore[arg-type]
            if ok:
                items.append(CheckItem(Level.OK, "adb server", detail))
            else:
                items.append(
                    CheckItem(
                        Level.WARN,
                        "adb server 归属异常",
                        detail,
                        "adb kill-server        # 然后用普通用户重新运行 lrom\n"
                        "    （绝不要用 sudo 运行 adb/fastboot，否则 server 会被 root 占用）",
                    )
                )

    # ---- USB 设备可见性 ----
    usb = _usb_android_devices()
    if usb:
        items.append(
            CheckItem(Level.OK, f"USB 上发现 {len(usb)} 个 Android 设备", "\n    ".join(usb[:3]))
        )
    else:
        items.append(
            CheckItem(
                Level.INFO,
                "USB 上未发现 Android 设备",
                "（如果设备没插，这是正常的）",
                "检查：数据线是否插在电脑上、手机是否开启 USB 调试、"
                "通知栏是否允许了 USB 访问",
            )
        )

    return items


def format_selfcheck(items: list[CheckItem]) -> str:
    from ..cli import C, paint

    marks = {
        Level.OK: ("✓", C.GREEN),
        Level.WARN: ("!", C.YELLOW),
        Level.ERROR: ("✗", C.RED),
        Level.INFO: ("·", C.BLUE),
    }
    lines: list[str] = []
    errors = 0
    warns = 0

    for it in items:
        mark, color = marks[it.level]
        if it.level is Level.ERROR:
            errors += 1
        elif it.level is Level.WARN:
            warns += 1
        lines.append(f"  {paint(mark, color)} {it.title}")
        if it.detail:
            for ln in it.detail.splitlines():
                lines.append(f"      {ln}")
        if it.fix:
            lines.append(paint("      修复:", C.CYAN))
            for ln in it.fix.splitlines():
                lines.append(f"      {ln}")

    lines.append("")
    if errors:
        lines.append(paint(f"  发现 {errors} 个错误，{warns} 个警告", C.RED))
        lines.append("  请先修复错误项，否则设备相关功能无法使用。")
    elif warns:
        lines.append(paint(f"  发现 {warns} 个警告，核心功能可用", C.YELLOW))
    else:
        lines.append(paint("  环境检查全部通过", C.GREEN))

    return "\n".join(lines)
