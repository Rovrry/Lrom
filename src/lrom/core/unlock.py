"""BL 解锁（Bootloader Unlock）。

## 与回锁的区别

解锁**不会变砖**（只是打开刷写权限 + 清空数据），所以风险等级远低于回锁，
前置检查可以宽松：不要求机型匹配、不要求镜像原厂。真正要防的是：

1. 用户没开「OEM 解锁」开关 → 命令必然失败
2. 新机（ColorOS 16+）需要先通过「深度测试」审核，否则开关是灰的
3. 用户不知道会清空全部数据

## 官方流程（社区实测确认）

1. 设置 → 关于手机 → 连点版本号，开启开发者选项
2. 设置 → 开发者选项 → 打开「OEM 解锁」与「USB 调试」
3. **新机（ColorOS 16 及以后，如一加 15）：还须先申请「深度测试」并通过审核**
4. `adb reboot bootloader` 或关机后音量键+电源键进 fastboot
5. `fastboot flashing unlock`
6. 设备屏幕用音量键选 `UNLOCK THE BOOTLOADER`，按电源键确认
7. 设备自动清空数据并重启

## 命令差异

- 现代设备：`fastboot flashing unlock`
- 旧设备（AOSP 8.0 之前）：`fastboot oem unlock`

本模块默认用 `flashing unlock`，遇到不支持时提示可尝试 `oem unlock`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "UnlockReadiness",
    "UnlockPlan",
    "check_unlock_readiness",
    "build_unlock_plan",
]


class UnlockReadiness(Enum):
    """解锁可行性。"""

    READY = "ready"                  # 已是 fastboot + 未锁定，可以解锁
    ALREADY_UNLOCKED = "unlocked"    # 已经解锁了，无需操作
    NOT_FASTBOOT = "not_fastboot"    # 不在 fastboot 模式
    NO_DEVICE = "no_device"          # 没检测到设备
    LOCK_STATE_UNKNOWN = "unknown"   # 读不到锁状态


@dataclass
class UnlockPlan:
    """解锁操作的完整计划，供用户审阅。"""

    device_model: str = ""
    device_codename: str = ""
    current_slot: str = ""
    #: 解锁命令（现代设备 flashing unlock，旧设备 oem unlock）
    unlock_command: tuple[str, ...] = ("flashing", "unlock")
    #: 解锁会清空全部用户数据
    wipes_data: bool = True
    steps: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = ["=" * 60, "  BL 解锁 — 执行计划", "=" * 60, ""]
        if self.device_model:
            lines.append(f"  设备      : {self.device_model} ({self.device_codename})")
        if self.current_slot:
            lines.append(f"  当前槽位  : {self.current_slot}")
        lines.append(f"  解锁命令  : fastboot {' '.join(self.unlock_command)}")
        lines.append(f"  数据影响  : {'会清空全部用户数据' if self.wipes_data else '不清数据'}")
        lines.append("")
        if self.steps:
            lines.append("  将要执行的步骤：")
            for i, s in enumerate(self.steps, 1):
                lines.append(f"    {i}. {s}")
            lines.append("")
        return "\n".join(lines)


def check_unlock_readiness(
    *,
    device_present: bool,
    device_mode: str,
    locked: str | None,
) -> tuple[UnlockReadiness, list[str]]:
    """判断当前设备是否具备解锁条件。

    Args:
        device_present: 是否检测到设备
        device_mode: "fastboot" / "adb" / "fastbootd" / ""
        locked: "0"=已解锁 / "1"=已锁定 / None=未知

    返回 (结论, 原因列表)。
    """
    if not device_present:
        return (
            UnlockReadiness.NO_DEVICE,
            ["未检测到设备。解锁需要设备处于 fastboot（bootloader）模式"],
        )

    if device_mode != "fastboot":
        return (
            UnlockReadiness.NOT_FASTBOOT,
            [
                f"设备当前不在 fastboot 模式（{device_mode or '未知'}）。",
                "请执行：adb reboot bootloader",
            ],
        )

    if locked is None:
        return (
            UnlockReadiness.LOCK_STATE_UNKNOWN,
            ["无法确认 Bootloader 锁状态，为安全起见停止"],
        )

    if str(locked) == "0":
        return (
            UnlockReadiness.ALREADY_UNLOCKED,
            ["设备已是解锁状态，无需重复解锁"],
        )

    return (UnlockReadiness.READY, [])


def build_unlock_plan(
    *,
    device_model: str = "",
    device_codename: str = "",
    current_slot: str = "",
    legacy_command: bool = False,
) -> UnlockPlan:
    """生成解锁计划。不写设备。

    legacy_command=True 时用旧命令 `oem unlock`（AOSP 8.0 之前设备）。
    """
    cmd = ("oem", "unlock") if legacy_command else ("flashing", "unlock")
    return UnlockPlan(
        device_model=device_model,
        device_codename=device_codename,
        current_slot=current_slot,
        unlock_command=cmd,
        steps=[
            "确认设备处于 fastboot（bootloader）模式",
            "确认开发者选项里「OEM 解锁」已打开",
            "新机（ColorOS 16+）须已通过「深度测试」审核",
            "执行 fastboot " + " ".join(cmd),
            "在设备屏幕上用音量键选 UNLOCK THE BOOTLOADER，按电源键确认",
            "设备会自动清空全部数据并重启",
            "重启后在开发者选项里可看到 OEM 解锁已生效",
        ],
    )
