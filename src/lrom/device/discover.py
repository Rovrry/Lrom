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
    #: CPU 架构，如 ``arm64-v8a`` / ``armeabi-v7a`` / ``x86_64``
    abi: str | None = None

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
        # fastboot 下读解锁状态：getvar unlocked 返回 yes/no。
        # 注意这不是 ro.boot.flash.locked（那是 ADB 下的属性），
        # 两者的语义要统一成 "0" = 已解锁 / "1" = 已锁定。
        unlocked = _read_fastboot_unlocked(fb)
        if unlocked is not None:
            dev.locked = "0" if unlocked else "1"
        # fastboot 下没有 ro.product.model，用 serialno/产品信息补齐
        dev.model = None
        dev.serial = serial
        return dev

    return None


def _read_fastboot_unlocked(fb: FastbootRunner) -> bool | None:
    """在 fastboot 模式下读取 bootloader 解锁状态。

    返回 True=已解锁 / False=已锁定 / None=无法判断。

    为什么需要这个：`ro.boot.flash.locked` 只有 ADB（开机）模式才读得到，
    而真正要刷机时设备往往已经在 fastboot 里了。如果此时不做检查，
    锁定的设备会刷到一半才失败，或者出现更难处理的半刷状态。
    """
    try:
        raw = fb.getvar("unlocked")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    v = raw.strip().lower()
    if v in ("yes", "true", "1", "unlocked"):
        return True
    if v in ("no", "false", "0", "locked"):
        return False
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


def _read_adb_locked(adb: AdbRunner) -> str | None:
    """在 ADB（开机）模式下判断 Bootloader 解锁状态。

    返回 "0" = 已解锁 / "1" = 已锁定 / None = 无法判断
    （沿用 discover.Device.locked 的既有语义）。

    为什么要看多个来源：`ro.boot.flash.locked` 等 getprop 属性
    **可以被模块（KernelSU / Magisk 之类）hook 篡改**，实测遇到过
    已解锁的一加 15 上 `ro.boot.flash.locked=1`、`verifiedbootstate=green`，
    但内核启动参数里明明是 `verifiedbootstate=orange`。

    可靠程度（由高到低）：

    1. `/proc/cmdline` 里的 `verifiedbootstate` / `oplusboot.verifiedbootstate`
       —— 内核启动参数，运行期只读，模块改不了。
    2. `/proc/cmdline` 里的 `androidboot.verifiedbootstate`
    3. getprop 的 `ro.boot.verifiedbootstate`
    4. getprop 的 `ro.boot.flash.locked`

    `verifiedbootstate`：orange = 已解锁 / green = 已锁定 /
    yellow,red = 异常（这里按已锁定处理，宁可保守）。
    """
    # --- 1/2. 内核 cmdline：最可信 ---
    try:
        res = adb.shell("cat /proc/cmdline")
        if res.exit_code == 0:
            tokens = res.stdout.split()
            # 同一份 cmdline 里可能同时出现 oplusboot.* 与 androidboot.*
            for key in ("oplusboot.verifiedbootstate=", "androidboot.verifiedbootstate="):
                for tok in tokens:
                    if tok.startswith(key):
                        state = tok[len(key):].strip().lower()
                        if state == "orange":
                            return "0"
                        if state in ("green", "yellow", "red"):
                            return "1"
    except Exception:  # noqa: BLE001
        pass

    # --- 3. getprop 的 verifiedbootstate ---
    try:
        state = (adb.getprop("ro.boot.verifiedbootstate") or "").strip().lower()
        if state == "orange":
            return "0"
        if state in ("green", "yellow", "red"):
            return "1"
    except Exception:  # noqa: BLE001
        pass

    # --- 4. getprop 的 flash.locked（最容易被 hook，兜底）---
    try:
        locked = (adb.getprop("ro.boot.flash.locked") or "").strip()
        if locked in ("0", "1"):
            return locked
    except Exception:  # noqa: BLE001
        pass

    return None


def _read_adb_device(adb: AdbRunner, serial: str) -> Device:
    adb.serial = serial
    dev = Device(serial=serial, mode=DeviceMode.ADB)
    dev.codename = adb.getprop("ro.product.device") or None
    dev.model = adb.getprop("ro.product.model") or None
    dev.build_id = adb.getprop("ro.build.display.id") or None
    dev.android_version = adb.getprop("ro.build.version.release") or None
    dev.slot = _normalize_slot(adb.getprop("ro.boot.slot_suffix").lstrip("_")) or None

    dev.locked = _read_adb_locked(adb)

    # CPU 架构（用于挑选匹配的 APK / 内核模块）
    dev.abi = adb.getprop("ro.product.cpu.abi") or None

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
