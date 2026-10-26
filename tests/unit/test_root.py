"""自动化 root 流程测试。

重点锁住：
  1. KMI 推导必须按内核版本，不是 Android 版本
     （本机实测：Android 16 但 KMI 是 6.1-android14-11）
  2. patch 前必须检查 ramdisk，没有则拒绝
  3. patch 后校验能识别越界、丢 ramdisk、丢 AVB footer
  4. 任何写操作之前必须先留存原厂镜像
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from lrom.core.root import (
    RootMethod,
    build_root_plan,
    derive_kmi,
    save_stock_image,
    verify_patched_image,
)


def make_boot_v4(
    *,
    kernel_size: int = 0,
    ramdisk_size: int = 0,
    with_avb: bool = True,
    header_version: int = 4,
    total_pad: int = 0,
) -> bytes:
    header = bytearray(1584)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, kernel_size)
    struct.pack_into("<I", header, 12, ramdisk_size)
    struct.pack_into("<I", header, 16, 0x1C0001A8)
    struct.pack_into("<I", header, 20, 1584)
    struct.pack_into("<I", header, 40, header_version)

    body = bytearray(header)
    body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if kernel_size:
        body += b"K" * kernel_size
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if ramdisk_size:
        body += b"\x02\x21\x4c\x18" + b"R" * (ramdisk_size - 4)
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)
    if total_pad:
        body += b"\x00" * total_pad
        body += b"\x00" * ((4096 - len(body) % 4096) % 4096)

    if with_avb:
        footer = bytearray(64)
        footer[0:4] = b"AVBf"
        struct.pack_into(">I", footer, 4, 1)
        struct.pack_into(">Q", footer, 12, len(body))
        body += footer
    else:
        body += b"\x00" * 64
    return bytes(body)


# ---------------------------------------------------------------------------
# KMI 推导
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "release,expected",
    [
        # 本项目真机实测值
        ("6.1.141-android14-11-o-g5cdab3b518c1", "6.1-android14-11"),
        # KernelSU 官方文档的例子
        ("5.10.101-android12-9-g30979850fc20", "5.10-android12-9"),
        # SubLevel 不属于 KMI
        ("5.10.137-android12-9-gabcdef", "5.10-android12-9"),
        ("6.6.30-android16-6-abc", "6.6-android16-6"),
        ("6.12.1-android16-6-g1234567", "6.12-android16-6"),
    ],
)
def test_derive_kmi(release: str, expected: str):
    assert derive_kmi(release) == expected


def test_derive_kmi_sublevel_not_part_of_kmi():
    """同一 KMI 下不同 SubLevel 必须推导为相同结果。"""
    a = derive_kmi("6.1.141-android14-11-o-g5cdab3b518c1")
    b = derive_kmi("6.1.166-android14-11-o-gdeadbeef")
    assert a == b == "6.1-android14-11"


def test_derive_kmi_android16_kernel_still_android14():
    """关键：设备是 Android 16，但内核是 android14-11，KMI 必须取内核的。

    照网上教程按 Android 版本选 android16-6.12 必然失败。
    """
    kmi = derive_kmi("6.1.141-android14-11-o-g5cdab3b518c1")
    assert kmi == "6.1-android14-11"
    assert not kmi.startswith("android16")


@pytest.mark.parametrize("release", ["", "garbage", "6.1.141", None])
def test_derive_kmi_unparseable(release):
    assert derive_kmi(release) is None


# ---------------------------------------------------------------------------
# 计划生成
# ---------------------------------------------------------------------------


class FakeDevice:
    serial = "test123"
    slot = "a"
    locked = "0"


def test_plan_blocks_boot_image(tmp_path: Path):
    """patch boot.img（无 ramdisk）必须被阻止。"""
    img = tmp_path / "boot.img"
    img.write_bytes(make_boot_v4(kernel_size=8192, ramdisk_size=0))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert plan.can_proceed is False
    assert any("ramdisk" in b for b in plan.blockers)


def test_plan_accepts_init_boot(tmp_path: Path, monkeypatch):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    # APP 方式不需要 ksud，应该可以继续
    assert plan.can_proceed is True
    assert plan.partition == "init_boot"
    assert plan.image_size == 8192 + 4096 + 64


def test_plan_missing_image(tmp_path: Path):
    plan = build_root_plan(
        image=tmp_path / "nope.img", method=RootMethod.APP, device=FakeDevice()
    )
    assert plan.can_proceed is False
    assert any("不存在" in b for b in plan.blockers)


def test_plan_ksud_requires_tool(tmp_path: Path):
    """ksud 方式在找不到 ksud 时必须阻止，并给出获取方法。"""
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.KSUD, device=FakeDevice())
    assert plan.can_proceed is False
    blocked = " ".join(plan.blockers)
    assert "ksud" in blocked
    assert "github.com/tiann/KernelSU" in blocked


def test_plan_warns_never_auto_reboot(tmp_path: Path):
    """项目铁律：必须提示手动重启。"""
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert any("手动重启" in w for w in plan.warnings)


def test_plan_warns_ota_loses_root(tmp_path: Path):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert any("OTA" in w for w in plan.warnings)


def test_plan_describe_contains_key_info(tmp_path: Path):
    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    text = plan.describe()
    assert "init_boot" in text
    assert "test123" in text


# ---------------------------------------------------------------------------
# 原厂镜像留存
# ---------------------------------------------------------------------------


def test_save_stock_image_creates_copy_and_manifest(tmp_path: Path):
    src = tmp_path / "init_boot.img"
    src.write_bytes(make_boot_v4(ramdisk_size=8192))

    work = tmp_path / "root-work"
    dest = save_stock_image(src, workdir=work, partition="init_boot")

    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()

    meta_path = dest.with_suffix(".json")
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["partition"] == "init_boot"
    assert meta["size"] == len(src.read_bytes())
    assert len(meta["sha256"]) == 64


def test_save_stock_image_sha_matches(tmp_path: Path):
    import hashlib

    src = tmp_path / "init_boot.img"
    data = make_boot_v4(ramdisk_size=8192)
    src.write_bytes(data)

    dest = save_stock_image(src, workdir=tmp_path / "w", partition="init_boot")
    meta = json.loads(dest.with_suffix(".json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# patch 后校验
# ---------------------------------------------------------------------------


def test_verify_rejects_image_exceeding_partition(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, total_pad=16384))

    ok, problems = verify_patched_image(
        stock=stock, patched=patched, partition_size=5000
    )
    assert ok is False
    assert any("超过分区" in p for p in problems)


def test_verify_rejects_lost_ramdisk(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=0))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("ramdisk" in p for p in problems)


def test_verify_rejects_lost_avb_footer(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192, with_avb=True))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, with_avb=False))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("AVB" in p for p in problems)


def test_verify_rejects_header_version_change(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192, header_version=3))
    patched = tmp_path / "patched.img"
    patched.write_bytes(make_boot_v4(ramdisk_size=8192, header_version=4))

    ok, problems = verify_patched_image(stock=stock, patched=patched)
    assert ok is False
    assert any("header 版本" in p for p in problems)


def test_verify_accepts_good_patch(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))
    patched = tmp_path / "patched.img"
    # 大小变了但结构完整 —— 正常的 patch 就是这样
    patched.write_bytes(make_boot_v4(ramdisk_size=16384))

    ok, problems = verify_patched_image(
        stock=stock, patched=patched, partition_size=1_000_000
    )
    assert ok is True
    assert problems == []


def test_verify_handles_missing_patched_file(tmp_path: Path):
    stock = tmp_path / "stock.img"
    stock.write_bytes(make_boot_v4(ramdisk_size=8192))

    ok, problems = verify_patched_image(
        stock=stock, patched=tmp_path / "missing.img"
    )
    assert ok is False
    assert any("不存在" in p for p in problems)


# ---------------------------------------------------------------------------
# 刷入模式（fastboot / fastbootd）
# ---------------------------------------------------------------------------


def test_flash_mode_labels():
    from lrom.core.root import FlashMode

    assert FlashMode.FASTBOOT.value == "fastboot"
    assert FlashMode.FASTBOOTD.value == "fastbootd"
    assert "bootloader" in FlashMode.FASTBOOT.label
    assert "用户空间" in FlashMode.FASTBOOTD.label


def test_plan_carries_flash_mode(tmp_path: Path):
    from lrom.core.root import FlashMode

    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(
        image=img,
        method=RootMethod.APP,
        device=FakeDevice(),
        flash_mode=FlashMode.FASTBOOTD,
    )
    assert plan.flash_mode is FlashMode.FASTBOOTD
    assert "FastbootD" in plan.describe()


def test_plan_defaults_to_fastboot(tmp_path: Path):
    from lrom.core.root import FlashMode

    img = tmp_path / "init_boot.img"
    img.write_bytes(make_boot_v4(ramdisk_size=8192))

    plan = build_root_plan(image=img, method=RootMethod.APP, device=FakeDevice())
    assert plan.flash_mode is FlashMode.FASTBOOT


class FakeFastboot:
    """模拟 fastboot，用于测试模式探测与切换。"""

    def __init__(self, *, devices, is_user_space, reboot_ok=True):
        self._devices = devices
        self._is_user_space = is_user_space
        self._reboot_ok = reboot_ok
        self.calls = []

    def devices(self):
        return ["serial1"] if self._devices else []

    def is_fastbootd(self):
        return self._is_user_space

    def run(self, *args, timeout=None):
        self.calls.append(args)

        class R:
            exit_code = 0
            timed_out = False
            stdout = ""
            stderr = ""
            combined = "OKAY"

        if args[:2] == ("reboot", "fastboot") and self._reboot_ok:
            # 模拟切换成功
            self._is_user_space = True
        return R()

    def ok(self, result):
        return True


def test_detect_fastboot_mode_none():
    from lrom.core.root import detect_fastboot_mode

    fb = FakeFastboot(devices=False, is_user_space=False)
    assert detect_fastboot_mode(fb) == "none"


def test_detect_fastboot_mode_fastboot():
    from lrom.core.root import detect_fastboot_mode

    fb = FakeFastboot(devices=True, is_user_space=False)
    assert detect_fastboot_mode(fb) == "fastboot"


def test_detect_fastboot_mode_fastbootd():
    from lrom.core.root import detect_fastboot_mode

    fb = FakeFastboot(devices=True, is_user_space=True)
    assert detect_fastboot_mode(fb) == "fastbootd"


def test_enter_fastbootd_already_there():
    from lrom.core.root import enter_fastbootd

    fb = FakeFastboot(devices=True, is_user_space=True)
    ok, msg = enter_fastbootd(fastboot=fb)
    assert ok is True
    assert "已在" in msg
    # 不应触发 reboot
    assert not any(c[:2] == ("reboot", "fastboot") for c in fb.calls)


def test_enter_fastbootd_switches():
    from lrom.core.root import enter_fastbootd

    fb = FakeFastboot(devices=True, is_user_space=False)
    ok, msg = enter_fastbootd(fastboot=fb, timeout=10)
    assert ok is True
    assert any(c[:2] == ("reboot", "fastboot") for c in fb.calls)


def test_enter_fastbootd_no_device_gives_ocdt_hint():
    """没有 fastboot 设备时，提示里要点明 OCDT 场景的绕行办法。"""
    from lrom.core.root import enter_fastbootd

    fb = FakeFastboot(devices=False, is_user_space=False)
    ok, msg = enter_fastbootd(fastboot=fb, timeout=5)
    assert ok is False
    assert "adb reboot fastboot" in msg


# ---------------------------------------------------------------------------
# 模式切换：flash 前必须先把设备切到 fastboot（v0.4.4 修复）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_poll_delay(monkeypatch):
    """把模式切换的轮询间隔设为 0，测试不要真等。"""
    import lrom.core.root as root_mod

    monkeypatch.setattr(root_mod, "_POLL_INTERVAL", 0.0)


class FakeAdb:
    def __init__(self, *, in_adb=True, reboot_ok=True):
        self._in_adb = in_adb
        self._reboot_ok = reboot_ok
        self.calls = []

    def devices(self):
        return {"serial1": "device"} if self._in_adb else {}

    def run(self, *args, timeout=None):
        self.calls.append(args)

        class R:
            exit_code = 0 if self._reboot_ok else 1
            timed_out = False
            stdout = ""
            stderr = "" if self._reboot_ok else "boom"
            combined = "OKAY" if self._reboot_ok else "error: boom"

        if args[:2] == ("reboot", "bootloader") and self._reboot_ok:
            self._in_adb = False
        return R()


def test_ensure_fastboot_from_adb_reboots():
    """设备在 ADB 模式时必须 adb reboot bootloader，否则 fastboot flash 会空等。

    这是 v0.4.2 的真实 bug：用户跑到第 4 步「刷入」时卡死，
    因为设备还在 ADB 模式，fastboot 找不到设备。
    """
    from lrom.core.root import ensure_fastboot

    fb = FakeFastboot(devices=True, is_user_space=False)
    # 一开始不在 bootloader，reboot 后才在
    fb._devices = False
    adb = FakeAdb(in_adb=True)

    # adb reboot bootloader 成功后设备才出现在 fastboot
    def adb_run_side_effect(*args, timeout=None):
        if args[:2] == ("reboot", "bootloader"):
            fb._devices = True
        return FakeAdb.run(adb, *args, timeout=timeout)

    adb.run = adb_run_side_effect

    ok, msg = ensure_fastboot(fastboot=fb, adb=adb, timeout=10)
    assert ok is True
    assert any(c[:2] == ("reboot", "bootloader") for c in adb.calls)


def test_ensure_fastboot_already_in_fastboot_no_reboot():
    from lrom.core.root import ensure_fastboot

    fb = FakeFastboot(devices=True, is_user_space=False)
    adb = FakeAdb(in_adb=False)

    ok, msg = ensure_fastboot(fastboot=fb, adb=adb, timeout=5)
    assert ok is True
    assert "已在" in msg
    assert adb.calls == []


def test_ensure_fastboot_from_fastbootd_goes_to_bootloader():
    from lrom.core.root import ensure_fastboot

    fb = FakeFastboot(devices=True, is_user_space=True)
    adb = FakeAdb(in_adb=False)

    def run_side_effect(*args, timeout=None):
        if args[:2] == ("reboot", "bootloader"):
            fb._is_user_space = False
        return FakeFastboot.run(fb, *args, timeout=timeout)

    fb.run = run_side_effect

    ok, msg = ensure_fastboot(fastboot=fb, adb=adb, timeout=10)
    assert ok is True


def test_ensure_fastboot_adb_reboot_failure_reports():
    from lrom.core.root import ensure_fastboot

    fb = FakeFastboot(devices=False, is_user_space=False)
    adb = FakeAdb(in_adb=True, reboot_ok=False)

    ok, msg = ensure_fastboot(fastboot=fb, adb=adb, timeout=3)
    assert ok is False
    assert "adb reboot bootloader" in msg


def test_ensure_fastboot_timeout_gives_guidance():
    from lrom.core.root import ensure_fastboot

    fb = FakeFastboot(devices=False, is_user_space=False)
    adb = FakeAdb(in_adb=False)

    ok, msg = ensure_fastboot(fastboot=fb, adb=adb, timeout=3)
    assert ok is False
    assert "超时" in msg


def test_flash_calls_ensure_fastboot_first():
    """flash_patched_image 必须先切模式，再 flash。

    这条是防回归：确保不会退回到「直接 fastboot flash」的老行为。
    """
    from lrom.core.root import FlashMode, flash_patched_image

    order = []

    class RecordingFastboot:
        def __init__(self):
            self._devices = False

        def devices(self):
            return ["s"] if self._devices else []

        def is_fastbootd(self):
            return False

        def run(self, *args, timeout=None):
            order.append(args)
            if args[:2] == ("reboot", "bootloader"):
                self._devices = True

            class R:
                exit_code = 0
                timed_out = False
                stdout = ""
                stderr = ""
                combined = "OKAY"

            return R()

        def ok(self, result):
            return True

    fb = RecordingFastboot()
    fb._devices = False  # 初始不在 fastboot，模拟真实卡死场景
    adb = FakeAdb(in_adb=True)

    # adb reboot bootloader 后设备才出现在 fastboot
    def adb_run_side_effect(*args, timeout=None):
        if args[:2] == ("reboot", "bootloader"):
            fb._devices = True
        return FakeAdb.run(adb, *args, timeout=timeout)

    adb.run = adb_run_side_effect

    import lrom.core.root as root_mod

    orig = root_mod.AdbRunner
    root_mod.AdbRunner = lambda *a, **k: adb
    try:
        ok, detail = flash_patched_image(
            image=Path("/tmp/x.img"),
            partition="init_boot",
            fastboot=fb,
            flash_mode=FlashMode.FASTBOOT,
        )
    finally:
        root_mod.AdbRunner = orig

    assert ok is True
    # 关键断言：必须先通过 adb 切到 bootloader，之后才有 flash
    assert any(c[:2] == ("reboot", "bootloader") for c in adb.calls), (
        "flash 之前必须先 adb reboot bootloader，否则设备还在系统里，"
        "fastboot flash 会空等（v0.4.2 的卡死 bug）"
    )
    assert ("flash", "init_boot", "/tmp/x.img") in order


# ---------------------------------------------------------------------------
# Bootloader 锁检测（v0.4.4）
# ---------------------------------------------------------------------------


def test_read_fastboot_unlocked_yes():
    from lrom.device.discover import _read_fastboot_unlocked

    class FB:
        def getvar(self, name):
            return "yes"

    assert _read_fastboot_unlocked(FB()) is True


@pytest.mark.parametrize("value", ["no", "NO", "false", "0", "locked"])
def test_read_fastboot_unlocked_no(value):
    from lrom.device.discover import _read_fastboot_unlocked

    class FB:
        def getvar(self, name):
            return value

    assert _read_fastboot_unlocked(FB()) is False


def test_read_fastboot_unlocked_unknown():
    from lrom.device.discover import _read_fastboot_unlocked

    class FB:
        def getvar(self, name):
            return None

    assert _read_fastboot_unlocked(FB()) is None


def test_read_fastboot_unlocked_handles_exception():
    from lrom.device.discover import _read_fastboot_unlocked

    class FB:
        def getvar(self, name):
            raise RuntimeError("boom")

    assert _read_fastboot_unlocked(FB()) is None


def test_read_fastboot_unlocked_garbage():
    from lrom.device.discover import _read_fastboot_unlocked

    class FB:
        def getvar(self, name):
            return "maybe"

    assert _read_fastboot_unlocked(FB()) is None
