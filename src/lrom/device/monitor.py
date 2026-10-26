"""实时设备监测。

问题：菜单顶部显示的设备信息是启动时读取的一次性快照。
用户拔掉数据线后，菜单仍然显示"已连接 OPD2404"，
这会给用户错误的安全感 —— 他可能会接着执行刷写命令。

方案：后台线程定期轮询设备状态，状态变化时立刻反映到菜单上。

注意：不能每进一次菜单就重启一个 adb server 或重新枚举全部属性
（那样很慢，每 2 秒一次会让菜单卡顿）。所以：
  - adb 通道用 `adb devices` 快速探测（几十毫秒）
  - 只在状态/序列号变化时才重新读取详细属性
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class DeviceSnapshot:
    """一次探测得到的设备状态。"""

    present: bool = False
    mode: str = ""          # adb / fastboot / fastbootd / ""
    serial: str = ""
    model: str = ""
    codename: str = ""
    slot: str = ""
    build_id: str = ""
    checked_at: float = 0.0

    def key(self) -> tuple:
        """用于判断"是否发生变化"的比较键。"""
        return (self.present, self.mode, self.serial, self.model, self.slot)

    def summary(self) -> str:
        if not self.present:
            return "未连接"
        parts = [self.model or self.codename or "设备"]
        if self.slot:
            parts.append(f"槽位 _{self.slot}")
        return "  ".join(parts)


def probe_once(*, adb_server_port: int | None = None) -> DeviceSnapshot:
    """执行一次快速探测。"""
    from ..device.discover import DeviceMode, detect

    snap = DeviceSnapshot(checked_at=time.time())
    try:
        dev = detect(adb_server_port=adb_server_port)
    except Exception:
        return snap

    if not dev:
        return snap

    snap.present = True
    snap.serial = dev.serial
    snap.model = dev.model or ""
    snap.codename = dev.codename or ""
    snap.slot = dev.slot or ""
    snap.build_id = dev.build_id or ""
    snap.mode = {
        DeviceMode.ADB: "adb",
        DeviceMode.FASTBOOT: "fastboot",
        DeviceMode.FASTBOOTD: "fastbootd",
        DeviceMode.UNAUTHORIZED: "unauthorized",
    }.get(dev.mode, "")
    return snap


class DeviceMonitor:
    """后台设备监测线程。

    用法：
        mon = DeviceMonitor(on_change=callback)
        mon.start()
        ...
        snap = mon.snapshot      # 随时读取最新状态，不阻塞
        mon.stop()
    """

    def __init__(
        self,
        *,
        interval: float = 1.5,
        on_change=None,
        adb_server_port: int | None = None,
    ) -> None:
        self.interval = interval
        self.on_change = on_change
        self.adb_server_port = adb_server_port
        self._snapshot = DeviceSnapshot()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_key: tuple | None = None

    @property
    def snapshot(self) -> DeviceSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # 先同步探测一次，保证菜单首屏就有数据
        snap = probe_once(adb_server_port=self.adb_server_port)
        with self._lock:
            self._snapshot = snap
        self._last_key = snap.key()

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def refresh_now(self) -> DeviceSnapshot:
        """立即探测一次（用于用户主动操作后刷新）。"""
        snap = probe_once(adb_server_port=self.adb_server_port)
        self._update(snap)
        return snap

    def _update(self, snap: DeviceSnapshot) -> None:
        with self._lock:
            self._snapshot = snap
        key = snap.key()
        if key != self._last_key:
            self._last_key = key
            if self.on_change:
                try:
                    self.on_change(snap)
                except Exception:
                    pass

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self._update(probe_once(adb_server_port=self.adb_server_port))
            except Exception:
                pass


def wait_for_state(
    want: str,
    *,
    timeout: float = 180.0,
    interval: float = 2.0,
    on_tick=None,
) -> DeviceSnapshot | None:
    """等待设备进入指定状态（adb / fastboot / fastbootd）。

    on_tick(elapsed, snap) 每轮调用，用于显示"正在等待…"。
    """
    from ..device.tools import default_adb_port

    deadline = time.time() + timeout
    start = time.time()
    port = default_adb_port()
    last: DeviceSnapshot | None = None

    while time.time() < deadline:
        snap = probe_once(adb_server_port=port)
        last = snap
        if on_tick:
            try:
                on_tick(time.time() - start, snap)
            except Exception:
                pass
        if snap.present and snap.mode == want:
            return snap
        time.sleep(interval)

    return last
