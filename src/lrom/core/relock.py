"""BL 回锁（重新锁定 Bootloader）。

## 为什么这是最危险的功能

BL 回锁 = 把设备重新锁回 bootloader 锁定状态。前提是**两个槽位里
都是完整、一致的原厂系统**。如果 A/B 槽里有一个槽还留着用户数据、
半刷的镜像、或三方 ROM 的残留，回锁后设备会：

- 校验失败 → 无法开机（硬砖，且此时已无解锁通道可自救）
- 或者能开机但某个槽损坏，切换槽位时直接砖

**所以本模块的核心不是"执行 lock"，而是"证明可以 lock"。**

## 安全规则（不可妥协）

1. **必须平刷两次完整系统包**：A 槽刷一遍、B 槽刷一遍。只刷一次
   再回锁 = 另一槽还是旧的/三方状态 → 高概率变砖。
2. **两次刷写都必须成功**（无 FAILED）。任何一次失败都不许回锁。
3. **两个槽的关键分区清单必须来自同一个包**（同一版本），
   保证版本一致。
4. **两次刷写的分区集合必须完全相同**，否则说明包或设备异常。
5. 任何校验不通过 → **拒绝回锁**，绝不"试试看"。

## 实现边界

本模块不直接调用 fastboot 写设备，而是让调用方（CLI）执行刷写，
把两次刷写的结果传进来做校验。这样副作用集中、易于测试。

真正执行 `fastboot flashing lock` 的入口也放在这里，但**只有在校验
通过后才会被返回**，调用方拿到 lock 指令才可执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "RelockStage",
    "RelockCheck",
    "RelockPlan",
    "build_relock_plan",
    "verify_two_pass",
]


class RelockStage(Enum):
    """回锁前的阶段。"""

    NEED_FIRST_PASS = "need_first_pass"    # 还没刷第一遍
    NEED_SECOND_PASS = "need_second_pass"  # 刷了 A 槽，还需刷 B 槽
    READY = "ready"                        # 两遍都成功，可以回锁
    BLOCKED = "blocked"                    # 校验不通过，拒绝回锁


@dataclass
class RelockCheck:
    """两次刷写结果的校验结论。"""

    stage: RelockStage
    reasons: list[str] = field(default_factory=list)
    #: 两次刷写各自的分区集合（便于发现不一致）
    pass_a_partitions: set[str] = field(default_factory=set)
    pass_b_partitions: set[str] = field(default_factory=set)
    #: 两次刷写记录的包版本（若有）
    pass_a_version: str | None = None
    pass_b_version: str | None = None

    @property
    def can_relock(self) -> bool:
        return self.stage is RelockStage.READY


@dataclass
class RelockPlan:
    """回锁操作的完整计划，供用户审阅。"""

    device_model: str = ""
    device_codename: str = ""
    current_slot: str = ""
    #: 执行 lock 时用的 fastboot 子命令
    lock_command: tuple[str, ...] = ("flashing", "lock")
    #: 是否还需要先擦除用户数据（回锁会清数据，实为 fastboot 行为）
    wipes_data: bool = True
    steps: list[str] = field(default_factory=list)

    def describe(self) -> str:
        lines = ["=" * 60, "  BL 回锁 — 执行计划", "=" * 60, ""]
        if self.device_model:
            lines.append(f"  设备      : {self.device_model} ({self.device_codename})")
        if self.current_slot:
            lines.append(f"  当前槽位  : {self.current_slot}")
        lines.append(f"  回锁命令  : fastboot {' '.join(self.lock_command)}")
        lines.append(f"  数据影响  : {'会清空全部用户数据' if self.wipes_data else '不清数据'}")
        lines.append("")
        if self.steps:
            lines.append("  将要执行的步骤：")
            for i, s in enumerate(self.steps, 1):
                lines.append(f"    {i}. {s}")
            lines.append("")
        return "\n".join(lines)


def build_relock_plan(
    *,
    device_model: str = "",
    device_codename: str = "",
    current_slot: str = "",
) -> RelockPlan:
    """生成回锁计划。不写设备。"""
    slot = current_slot or "?"
    return RelockPlan(
        device_model=device_model,
        device_codename=device_codename,
        current_slot=slot,
        steps=[
            "确认设备处于已解锁状态（bootloader / fastboot 模式）",
            "第一次平刷：刷完整系统包到 A 槽，校验全部成功",
            "切换活动槽到 B 槽",
            "第二次平刷：刷完整系统包到 B 槽，校验全部成功",
            "校验两次刷写：均无失败、分区集合一致、版本一致",
            "切回 A 槽（原起始槽）",
            f"执行 fastboot {' '.join(('flashing', 'lock'))} 回锁",
            "回锁后设备会清空数据并重启，请勿中断",
        ],
    )


def verify_two_pass(
    *,
    pass_a_ok: bool,
    pass_b_ok: bool,
    pass_a_partitions: set[str] | None,
    pass_b_partitions: set[str] | None,
    pass_a_version: str | None = None,
    pass_b_version: str | None = None,
    pass_a_failed: list[str] | None = None,
    pass_b_failed: list[str] | None = None,
) -> RelockCheck:
    """校验两次平刷结果，判断能否回锁。

    只有**全部满足**才返回 READY：

    1. 两次刷写都成功（ok 且无 failed 分区）
    2. 两次刷写的分区集合完全一致
    3. 若两边都能读到版本号，版本必须一致

    否则返回 BLOCKED 并列出原因（不猜、不"试试看"）。
    """
    reasons: list[str] = []

    fs_a = set(pass_a_partitions or set())
    fs_b = set(pass_b_partitions or set())

    if not pass_a_ok:
        reasons.append("第一次平刷（A 槽）未成功")
    if not pass_b_ok:
        reasons.append("第二次平刷（B 槽）未成功")

    fails_a = list(pass_a_failed or [])
    fails_b = list(pass_b_failed or [])
    if fails_a:
        reasons.append(f"A 槽刷写失败的分区：{', '.join(fails_a)}")
    if fails_b:
        reasons.append(f"B 槽刷写失败的分区：{', '.join(fails_b)}")

    if fs_a and fs_b:
        only_a = fs_a - fs_b
        only_b = fs_b - fs_a
        if only_a:
            reasons.append(f"两次刷写分区不一致（仅 A 有）：{', '.join(sorted(only_a))}")
        if only_b:
            reasons.append(f"两次刷写分区不一致（仅 B 有）：{', '.join(sorted(only_b))}")
    else:
        reasons.append("缺少刷写分区清单，无法确认两槽一致")

    if pass_a_version and pass_b_version and pass_a_version != pass_b_version:
        reasons.append(
            f"两次刷写的包版本不一致：{pass_a_version} vs {pass_b_version}"
        )

    stage = RelockStage.BLOCKED if reasons else RelockStage.READY
    return RelockCheck(
        stage=stage,
        reasons=reasons,
        pass_a_partitions=fs_a,
        pass_b_partitions=fs_b,
        pass_a_version=pass_a_version,
        pass_b_version=pass_b_version,
    )
