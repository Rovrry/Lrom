"""设备发现与状态识别。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .runner import AdbRunner, FastbootRunner, ToolNotFoundError


class DeviceMode(Enum):
    NONE = "未连接"
    ADB = "ADB（开机状态）"
    FASTBOOT = "Fastboot（bootloader）"
    FASTBOOTD = "FastbootD（用户空间）"
    UNAUTHORIZED = "未授权（需在手机上允许 USB 调试）"


@dataclass
class Device:
    serial: str
    mode: DeviceMode

    # 由 ADB 读取
    codename: str | None = None
    model: str | None = None
    product_name: str | None = None
    build_id: str | None = None
    android_version: str | None = None
    slot: str | None = None
    locked: str | None = None

    def describe(self) -> str:
        lines = [f"设备序列号: {self.serial}", f"当前模式  : {self.mode.value}"]
        if self.model:
            lines.append(f"设备型号  : {self.model}")
        if self.codename:
            lines.append(f"设备代号  : {self.codename}")
        if self.product_name:
            lines.append(f"产品名    : {self.product_name}")
        if self.build_id:
            lines.append(f"系统版本  : {self.build_id}")
        if self.android_version:
            lines.append(f"Android   : {self.android_version}")
        if self.slot:
            lines.append(f"当前槽位  : {self.slot}")
        if self.locked is not None:
            unlocked = str(self.locked) == "0"
            lines.append(f"Bootloader: {'已解锁' if unlocked else '已锁定'}")
        return "\n".join(lines)


def detect(*, adb_server_port: int | None = None) -> Device | None:
    """检测当前连接的设备。优先 ADB，其次 Fastboot。

    任一工具缺失时静默跳过该通道，不影响其他检测。
    """
    try:
        adb = AdbRunner(server_port=adb_server_port)
        devices = adb.devices()
    except ToolNotFoundError:
        adb = None
        devices = {}

    for serial, state in devices.items():
        if state == "device" and adb is not None:
            return _read_adb_device(adb, serial)
        if state == "unauthorized":
            return Device(serial=serial, mode=DeviceMode.UNAUTHORIZED)

    try:
        fb = FastbootRunner()
        fb_serials = fb.devices()
    except ToolNotFoundError:
        fb_serials = []

    if fb_serials:
        serial = fb_serials[0]
        fb = FastbootRunner(serial=serial)
        mode = DeviceMode.FASTBOOTD if _fb_is_userspace(fb) else DeviceMode.FASTBOOT
        dev = Device(serial=serial, mode=mode)
        # fastboot 下 `getvar product` 通常返回设备代号，但部分高通机型
        # 返回平台名（如 pineapple）。尽量多取几个来源做交叉判断。
        dev.codename = fb.getvar("product")
        for var in ("variant", "partition-type:boot"):
            pass
        dev.slot = _normalize_slot(fb.getvar("current-slot"))
        # fastboot 下没有 ro.product.model，用 serialno/产品信息补齐
        dev.model = None
        dev.serial = serial
        return dev

    return None


def _fb_is_userspace(fb: FastbootRunner) -> bool:
    try:
        return fb.is_fastbootd()
    except Exception:
        return False


def _normalize_slot(slot: str | None) -> str | None:
    if not slot:
        return None
    slot = slot.strip()
    if slot in ("a", "b"):
        return slot
    return slot


def _read_adb_device(adb: AdbRunner, serial: str) -> Device:
    adb.serial = serial
    dev = Device(serial=serial, mode=DeviceMode.ADB)
    dev.codename = adb.getprop("ro.product.device") or None
    dev.model = adb.getprop("ro.product.model") or None
    dev.build_id = adb.getprop("ro.build.display.id") or None
    dev.android_version = adb.getprop("ro.build.version.release") or None
    dev.slot = _normalize_slot(adb.getprop("ro.boot.slot_suffix").lstrip("_")) or None

    locked = adb.getprop("ro.boot.flash.locked")
    dev.locked = locked if locked else None

    # 欧加系：产品名常在 ro.product.name / ro.build.product
    dev.product_name = (
        adb.getprop("ro.product.name")
        or adb.getprop("ro.build.product")
        or None
    )
    return dev


def list_partitions(adb: AdbRunner, serial: str) -> set[str]:
    """列出设备 /dev/block/by-name 下的分区名。需要设备处于 ADB 且可读。"""
    adb.serial = serial
    res = adb.shell("ls /dev/block/by-name/")
    if res.exit_code != 0:
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}
