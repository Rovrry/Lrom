"""统一的子进程包装层。

设计要点（吸取 Windows 版工具的教训）：
  - 一律用 list 传参，绝不拼接字符串
  - 严格以退出码判定成败，不靠字符串 Contains 猜测
  - 流式读取输出，逐行回调，便于显示进度
  - 支持超时与取消
  - fastboot 的已知特例集中在此处理，并有明确注释
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .tools import ToolNotFoundError, find_tool, tool_env

__all__ = [
    "ToolNotFoundError",
    "CommandTimeout",
    "RunResult",
    "run",
    "FastbootRunner",
    "AdbRunner",
    "find_tool",
]


class CommandTimeout(Exception):
    """命令执行超时。"""


@dataclass
class RunResult:
    argv: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        # fastboot 的退出码不可靠，见 FastbootRunner
        return self.exit_code == 0 and not self.timed_out

    @property
    def combined(self) -> str:
        return f"{self.stdout}\n{self.stderr}"



def run(
    argv: Iterable[str],
    *,
    timeout: float | None = 300.0,
    on_line: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    check: bool = False,
) -> RunResult:
    """执行外部命令。

    on_line: 可选的行回调，流式接收 stdout 每一行。
    """
    argv = list(argv)

    proc_env = os.environ.copy()
    # 工具专属环境变量（如非标准安装的 adb 需要 LD_LIBRARY_PATH）
    if argv:
        tool_name = os.path.basename(argv[0])
        proc_env.update(tool_env(tool_name))
    if env:
        proc_env.update(env)
    # 统一 UTF-8，避免编码不一致导致乱码
    proc_env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=proc_env,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(f"无法执行 {argv[0]}: {exc}") from exc

    out_lines: list[str] = []
    err_lines: list[str] = []

    def _pump(stream, sink: list[str], cb):
        for line in stream:
            line = line.rstrip("\n")
            sink.append(line)
            if cb:
                cb(line)

    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_lines, on_line), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_lines, None), daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.wait()

    t_out.join(timeout=5)
    t_err.join(timeout=5)

    result = RunResult(
        argv=argv,
        exit_code=proc.returncode if proc.returncode is not None else -1,
        stdout="\n".join(out_lines),
        stderr="\n".join(err_lines),
        timed_out=timed_out,
    )

    if check and not result.ok:
        raise RuntimeError(f"命令失败 ({result.exit_code}): {' '.join(argv)}\n{result.combined}")

    return result


class FastbootRunner:
    """fastboot 命令封装。

    重要：`fastboot flash` 在失败时**可能仍返回退出码 0**，
    必须同时检查输出中的 FAILED / 错误标记。这是官方 fastboot 的已知行为，
    也正是 Windows 版工具靠字符串判断的由来 —— 这里把它收敛到唯一一处，
    并有明确测试覆盖。
    """

    _FAIL_PATTERNS = (
        re.compile(r"^\s*FAILED\b", re.IGNORECASE),
        re.compile(r"\bFAILED \(", re.IGNORECASE),
        re.compile(r"^\s*error:", re.MULTILINE | re.IGNORECASE),
    )

    def __init__(
        self,
        binary: str | None = None,
        *,
        serial: str | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        self.binary = binary or find_tool("fastboot")
        self.serial = serial
        self.on_line = on_line

    def _base(self) -> list[str]:
        argv = [self.binary]
        if self.serial:
            argv += ["-s", self.serial]
        return argv

    def run(self, *args: str, timeout: float | None = 300.0) -> RunResult:
        return run(
            [*self._base(), *args],
            timeout=timeout,
            on_line=self.on_line,
        )

    def ok(self, result: RunResult) -> bool:
        """综合退出码与输出判定成功。"""
        if result.timed_out or result.exit_code != 0:
            return False
        text = result.combined
        if any(p.search(text) for p in self._FAIL_PATTERNS):
            return False
        return True

    # -- 具体命令 ------------------------------------------------------

    def devices(self) -> list[str]:
        res = run([self.binary, "devices"], timeout=20)
        serials = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of"):
                continue
            parts = line.split()
            if parts:
                serials.append(parts[0])
        return serials

    def getvar(self, name: str) -> str | None:
        res = self.run("getvar", name, timeout=30)
        m = re.search(rf"{re.escape(name)}:\s*(.+)", res.combined)
        return m.group(1).strip() if m else None

    def is_fastbootd(self) -> bool:
        """通过是否存在动态分区判断是否在 fastbootd。"""
        res = self.run("getvar", "is-userspace", timeout=30)
        m = re.search(r"is-userspace:\s*(\w+)", res.combined)
        return bool(m and m.group(1).lower() == "yes")


class AdbRunner:
    """adb 命令封装。"""

    def __init__(
        self,
        binary: str | None = None,
        *,
        serial: str | None = None,
        on_line: Callable[[str], None] | None = None,
        server_port: int | None = None,
    ) -> None:
        self.binary = binary or find_tool("adb")
        self.serial = serial
        self.on_line = on_line
        self.env = {}
        if server_port is None:
            from .tools import default_adb_port

            server_port = default_adb_port()
        if server_port:
            self.env["ANDROID_ADB_SERVER_PORT"] = str(server_port)

    def _base(self) -> list[str]:
        argv = [self.binary]
        if self.serial:
            argv += ["-s", self.serial]
        return argv

    def run(self, *args: str, timeout: float | None = 60.0) -> RunResult:
        return run(
            [*self._base(), *args],
            timeout=timeout,
            on_line=self.on_line,
            env=self.env or None,
        )

    def devices(self) -> dict[str, str]:
        """返回 {serial: state}。"""
        res = run([self.binary, "devices"], timeout=20, env=self.env or None)
        result = {}
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("List of") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                result[parts[0]] = parts[1]
        return result

    def shell(self, command: str, timeout: float = 60.0) -> RunResult:
        return self.run("shell", command, timeout=timeout)

    def getprop(self, prop: str, timeout: float = 15.0) -> str:
        res = self.shell(f"getprop {prop}", timeout=timeout)
        return res.stdout.strip()
