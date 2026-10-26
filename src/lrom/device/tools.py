"""外部工具的路径解析。

Lrom 不捆绑 adb/fastboot，而是从系统 PATH 或用户配置中查找。
这样可以走发行版仓库、自动获得安全更新。

优先级：
  1. 环境变量 LROM_ADB / LROM_FASTBOOT
  2. 配置文件 ~/.config/lrom/config.json 的 tools 字段
  3. 系统 PATH
  4. 常见绝对路径（/usr/lib/android-sdk/platform-tools/ 等）
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

CONFIG_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "lrom" / "config.json"

_FALLBACK_DIRS = (
    "/usr/lib/android-sdk/platform-tools",
    "/usr/local/lib/android-sdk/platform-tools",
    "/opt/android-sdk/platform-tools",
    str(Path.home() / "Android/Sdk/platform-tools"),
)


class ToolNotFoundError(Exception):
    """所需外部工具不存在。"""

    def __init__(self, name: str) -> None:
        self.name = name
        hints = {
            "adb": "Debian/Kali: apt install adb     Arch: pacman -S android-tools",
            "fastboot": "Debian/Kali: apt install fastboot   Arch: pacman -S android-tools",
        }
        hint = hints.get(name, "")
        super().__init__(
            f"未找到命令 {name}。{hint}\n"
            f"也可以用环境变量指定：LROM_{name.upper()}=/path/to/{name}"
            f"\n或写入配置：{CONFIG_PATH}"
        )


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def tool_env(name: str) -> dict[str, str]:
    """返回该工具需要的额外环境变量（如非标准安装的 LD_LIBRARY_PATH）。

    配置示例：
        {"tools": {"adb": "/opt/pt/adb"},
         "tool_env": {"adb": {"LD_LIBRARY_PATH": "/opt/pt/lib"}}}
    """
    return dict(load_config().get("tool_env", {}).get(name, {}))


def default_adb_port() -> int | None:
    """配置里指定的 adb server 端口（可避免与系统 adb 版本冲突）。"""
    port = load_config().get("adb_server_port")
    return int(port) if port else None


def find_tool(name: str) -> str:
    """查找外部工具的可执行文件路径。"""
    env_key = f"LROM_{name.upper()}"
    if val := os.environ.get(env_key):
        if Path(val).is_file():
            return val
        raise ToolNotFoundError(name)

    tools = load_config().get("tools", {})
    if val := tools.get(name):
        if Path(val).is_file():
            return val

    if found := shutil.which(name):
        return found

    for d in _FALLBACK_DIRS:
        candidate = Path(d) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise ToolNotFoundError(name)
