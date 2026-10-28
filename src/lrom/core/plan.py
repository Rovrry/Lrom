"""刷写计划：把 ROM 包转换成可审阅、可执行的刷写清单。

这是 Lrom 相对 Windows 版工具的核心差异：
先产出纯数据的计划，用户能看到每一条要做什么、有什么风险，
确认后才真正写入设备。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from .payload import PayloadManifest


class Phase(Enum):
    """刷写阶段。必须先完成 FASTBOOT，再切到 FASTBOOTD。"""

    FASTBOOT = "fastboot"
    FASTBOOTD = "fastbootd"
    ADB = "adb"

    @property
    def label(self) -> str:
        return {
            Phase.FASTBOOT: "Fastboot 模式（bootloader）",
            Phase.FASTBOOTD: "FastbootD 模式（用户空间）",
            Phase.ADB: "ADB 模式（开机状态）",
        }[self]


class Risk(Enum):
    SAFE = "safe"
    WARN = "warn"
    DANGER = "danger"
    BLOCKED = "blocked"

    @property
    def label(self) -> str:
        return {
            Risk.SAFE: "安全",
            Risk.WARN: "注意",
            Risk.DANGER: "危险",
            Risk.BLOCKED: "已阻止",
        }[self]


#: 需要**强制二次确认**的分区：写错会永久丢失数据或直接变砖。
#: 注意：这些分区在官方全量包里是正常的刷写内容（不刷反而可能版本不匹配），
#: 因此默认不跳过，而是标记为 DANGER 并要求显式确认。
DANGER_PARTITIONS = frozenset(
    {
        # bootloader 链：版本不匹配会无法开机，但官方包刷写是必要的
        "xbl",
        "xbl_config",
        "abl",
        "tz",
        "hyp",
        "keymaster",
        "devcfg",
        "aop",
        "aop_config",
        "qupfw",
        "cpucp",
        "shrm",
        "uefisecapp",
        "featenabler",
        "imagefv",
        "uefi",
        "tzsc",
        "pvmfw",
    }
)

#: 绝不允许刷写，除非用户明确要求：写这些会丢失 IMEI、指纹、DRM 密钥等不可再生数据
NEVER_OVERWRITE = frozenset(
    {
        "persist",
        "devinfo",
        "modemst1",
        "modemst2",
        "fsg",
        "fsc",
        "frp",
        "secdata",
        "storsec",
        "keystore",
        "userdata",  # 会清空用户数据
    }
)

#: 需要额外提醒的分区（有风险但允许）。
WARN_PARTITIONS = frozenset(
    {
        "super",
        "modem",
        "metadata",
        "misc",
        "param",
        "splash",
        "logo",
        "vbmeta",
        "vbmeta_system",
        "vbmeta_vendor",
        # recovery：fastbootd（用户空间 fastboot）寄生在 recovery 里
        # （AOSP：「fastboot / boot-control / health HAL 打包进 recovery 镜像」）。
        # recovery 坏了 → 进不了 fastbootd → 救砖能力全失。所以可刷但须提示风险
        # （「修复 FastbootD」正是靠刷回 recovery）。
        "recovery",
        # OCDT（engineering_cdt）：决定能否进 Fastboot 的机独有校准分区。
        # 用非本机的会被拒绝进 Fastboot，损坏后售后也无法修复（只能拆字库）。
        # 备份清单里有它，但刷写时必须明确警告，不能当普通分区静默刷。
        "engineering_cdt",
        "ocdt",
        # 欧加 AVB 备份分区
        "oplusstanvbk",
    }
)

#: super 内的逻辑分区（欧加系）。它们必须走 FastbootD。
OPLUS_LOGICAL_PARTITIONS = frozenset(
    {
        "system",
        "system_ext",
        "product",
        "vendor",
        "odm",
        "system_dlkm",
        "vendor_dlkm",
        "odm_dlkm",
        "my_product",
        "my_carrier",
        "my_company",
        "my_engineering",
        "my_heytap",
        "my_manifest",
        "my_preload",
        "my_region",
        "my_stock",
        "my_bigball",
    }
)

#: 必须最后刷写的分区（modem 若提前刷，可能找不到信号）
LAST_PARTITIONS = ("modem",)


@dataclass
class FlashOp:
    """一条刷写操作。"""

    partition: str
    phase: Phase
    image: str | None = None
    size: int = 0
    skip: bool = False
    risk: Risk = Risk.SAFE
    reason: str = ""
    slot: str | None = None

    @property
    def size_mb(self) -> float:
        return self.size / 1e6


@dataclass
class FlashPlan:
    package: str
    device_model: str | None = None
    device_codename: str | None = None
    slot: str | None = None
    ops: list[FlashOp] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_phase(self, phase: Phase) -> list[FlashOp]:
        return [op for op in self.ops if op.phase is phase and not op.skip]

    @property
    def active_ops(self) -> list[FlashOp]:
        return [op for op in self.ops if not op.skip]

    @property
    def blocked_ops(self) -> list[FlashOp]:
        return [op for op in self.ops if op.risk is Risk.BLOCKED]

    @property
    def total_bytes(self) -> int:
        return sum(op.size for op in self.active_ops)

    def to_json(self) -> dict:
        def conv(o: FlashOp) -> dict:
            d = asdict(o)
            d["phase"] = o.phase.value
            d["risk"] = o.risk.value
            return d

        return {
            "tool": "lrom",
            "package": self.package,
            "device": {
                "model": self.device_model,
                "codename": self.device_codename,
                "slot": self.slot,
            },
            "summary": {
                "total_ops": len(self.ops),
                "active_ops": len(self.active_ops),
                "blocked_ops": len(self.blocked_ops),
                "total_bytes": self.total_bytes,
                "fastboot_ops": len(self.by_phase(Phase.FASTBOOT)),
                "fastbootd_ops": len(self.by_phase(Phase.FASTBOOTD)),
            },
            "notes": self.notes,
            "ops": [conv(op) for op in self.ops],
        }

    def save(self, path: Path) -> Path:
        path.write_text(
            json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path


def _classify_partition(name: str) -> tuple[Phase, Risk, str]:
    """判定分区应走哪个阶段、风险等级与说明。"""
    if name in NEVER_OVERWRITE:
        return Phase.FASTBOOT, Risk.BLOCKED, "会永久丢失 IMEI/指纹/密钥或清空数据，默认禁止"
    if name in OPLUS_LOGICAL_PARTITIONS:
        return Phase.FASTBOOTD, Risk.SAFE, "super 内的逻辑分区，需 FastbootD"
    if name in DANGER_PARTITIONS:
        return Phase.FASTBOOT, Risk.DANGER, "bootloader 链分区，写错会导致无法开机"
    if name in WARN_PARTITIONS:
        return Phase.FASTBOOT, Risk.WARN, "重要分区，刷写前已备份"
    return Phase.FASTBOOT, Risk.SAFE, ""


def build_plan(
    manifest: PayloadManifest,
    *,
    package_path: str,
    device_model: str | None = None,
    device_codename: str | None = None,
    slot: str | None = None,
    device_partitions: set[str] | None = None,
    allow_dangerous: bool = False,
    include: list[str] | None = None,
) -> FlashPlan:
    """根据 manifest 与设备信息生成刷写计划。"""
    plan = FlashPlan(
        package=package_path,
        device_model=device_model,
        device_codename=device_codename,
        slot=slot,
    )
    bs = manifest.block_size
    include_set = set(include) if include else None

    for part in manifest.partitions:
        if include_set and part.name not in include_set:
            continue

        phase, risk, reason = _classify_partition(part.name)
        size = part.size_bytes(bs)
        skip = False

        # 仅"永不覆盖"类分区默认跳过；DANGER 类正常刷写但会提示
        if risk is Risk.BLOCKED and not allow_dangerous:
            skip = True
            reason = reason + "，已跳过"

        # 设备上不存在该分区 => 跳过（防止刷到错误的节点）
        if device_partitions:
            node = part.name
            if not (node in device_partitions or f"{node}_a" in device_partitions
                    or f"{node}_b" in device_partitions):
                if part.name not in OPLUS_LOGICAL_PARTITIONS:
                    skip = True
                    reason = "设备上不存在该分区，已跳过"

        plan.ops.append(
            FlashOp(
                partition=part.name,
                phase=phase,
                image=f"{part.name}.img",
                size=size,
                skip=skip,
                risk=risk,
                reason=reason,
                slot=slot,
            )
        )

    # modem 必须最后刷
    for last in LAST_PARTITIONS:
        for i, op in enumerate(plan.ops):
            if op.partition == last:
                plan.ops.append(plan.ops.pop(i))
                break

    # 同阶段内按容量从大到小（大分区先刷，早暴露空间不足）
    def sort_key(op: FlashOp):
        return (list(Phase).index(op.phase), -op.size)

    plan.ops.sort(key=sort_key)

    # 阶段顺序统计提示
    n_fb = len(plan.by_phase(Phase.FASTBOOT))
    n_fbd = len(plan.by_phase(Phase.FASTBOOTD))
    plan.notes.append(f"阶段一（Fastboot）: {n_fb} 个分区")
    plan.notes.append(f"阶段二（FastbootD）: {n_fbd} 个逻辑分区")
    if plan.blocked_ops:
        plan.notes.append(
            f"已阻止 {len(plan.blocked_ops)} 个分区（会丢失 IMEI/指纹/密钥或清空数据），"
            f"需要 --allow-dangerous 才写入"
        )
    n_danger = len([op for op in plan.ops if op.risk is Risk.DANGER and not op.skip])
    if n_danger:
        plan.notes.append(
            f"{n_danger} 个 bootloader 链分区将被刷写（官方全量包的正常组成，"
            f"但请确保包与设备型号严格匹配）"
        )
    plan.notes.append("modem 将最后刷写")

    return plan


def format_plan(plan: FlashPlan) -> str:
    """生成人类可读的计划文本。"""
    lines: list[str] = []
    lines.append(f"ROM 包: {plan.package}")
    dev = plan.device_codename or "未知"
    model = plan.device_model or "未知"
    lines.append(f"设备  : {model} ({dev})   槽位: {plan.slot or '自动'}")
    lines.append(f"总计  : {len(plan.active_ops)} 个分区, "
                 f"{plan.total_bytes / 2**30:.2f} GB")
    lines.append("")

    for phase in (Phase.FASTBOOT, Phase.FASTBOOTD, Phase.ADB):
        ops = [op for op in plan.ops if op.phase is phase]
        if not ops:
            continue
        active = [op for op in ops if not op.skip]
        lines.append(f"── {phase.label}  ({len(active)} 个) " + "─" * 20)
        lines.append(f"  {'分区':<20}{'大小(MB)':>10}  {'风险':<6} 说明")
        for op in ops:
            mark = "[跳过] " if op.skip else ""
            lines.append(
                f"  {mark}{op.partition:<20}{op.size_mb:>10,.1f}  "
                f"{op.risk.label:<6} {op.reason}"
            )
        lines.append("")

    if plan.notes:
        lines.append("说明:")
        for n in plan.notes:
            lines.append(f"  · {n}")

    return "\n".join(lines)
