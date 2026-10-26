"""设备实时监测测试（不依赖真实设备）。"""

from __future__ import annotations

import time

import pytest

from lrom.device.monitor import DeviceMonitor, DeviceSnapshot, probe_once


def test_snapshot_key_changes_with_state():
    a = DeviceSnapshot(present=True, mode="adb", serial="X", model="M", slot="a")
    b = DeviceSnapshot(present=True, mode="adb", serial="X", model="M", slot="a")
    assert a.key() == b.key()

    c = DeviceSnapshot(present=False)
    assert a.key() != c.key()

    d = DeviceSnapshot(present=True, mode="fastboot", serial="X", model="M", slot="a")
    assert a.key() != d.key()


def test_snapshot_summary_when_disconnected():
    s = DeviceSnapshot(present=False)
    assert s.summary() == "未连接"


def test_snapshot_summary_when_connected():
    s = DeviceSnapshot(present=True, model="OPD2404", slot="a", mode="adb")
    assert "OPD2404" in s.summary()
    assert "_a" in s.summary()


def test_probe_returns_snapshot():
    """probe_once 必须总是返回 DeviceSnapshot，即使没有设备。"""
    snap = probe_once()
    assert isinstance(snap, DeviceSnapshot)
    assert snap.checked_at > 0


def test_monitor_start_stop():
    mon = DeviceMonitor(interval=0.3)
    mon.start()
    try:
        assert mon.snapshot is not None
        time.sleep(0.5)
        assert mon.snapshot.checked_at > 0
    finally:
        mon.stop()


def test_monitor_calls_on_change_when_state_flips():
    """状态变化时必须触发回调。"""
    changes = []
    mon = DeviceMonitor(interval=0.2, on_change=lambda s: changes.append(s))
    mon.start()
    try:
        time.sleep(0.3)
        initial = mon.snapshot
        # 人为修改内部快照键，模拟"状态变化"
        mon._last_key = ("bogus",)
        mon._update(initial)
    finally:
        mon.stop()
    assert changes


def test_refresh_now_returns_fresh_snapshot():
    mon = DeviceMonitor(interval=10.0)  # 后台几乎不轮询
    mon.start()
    try:
        before = mon.snapshot.checked_at
        time.sleep(0.05)
        snap = mon.refresh_now()
        assert snap.checked_at >= before
    finally:
        mon.stop()
