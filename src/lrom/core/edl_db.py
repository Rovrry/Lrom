"""9008（EDL）免授权漏洞 —— 机型熔断阈值库与判定逻辑。

## 事实基础（实测结论，不可违背）

`docs/ARB-实验报告.md` 第 4.2 节已实测否定「刷早期 ROM 能恢复免授权 9008」：

    免授权 9008 一旦失效就是**单向不可逆**的，
    刷任何 ROM 都救不回来。

社区原文（AdaUnlocked 欧加高通解锁指南）：「升级系统会导致该机 9008
免授权被永久熔断，且降级无效！」

关键推论：

1. **能从版本号判断的只有「升级动作」的风险** —— 设备当前版本低于阈值时，
   升级到阈值及以上会触发熔断；熔断由设备在首次启动新系统时完成。
2. **不能声称降级可恢复** —— 熔断在设备侧 efuse，与 ROM 无关。
3. **读不到设备侧真实状态** —— 实测 `getprop` 中无 edl / sahara / firehose
   相关属性，`/sys/class/` 也无对应节点，因此本模块的判定基于
   **系统版本号**，属推断而非实测，必须如实告知用户。

## 因此本模块的判定只做两件事

- 告诉用户「升级到 X 版本后，9008 免授权将永久失效且不可逆」
- 降级时明确提示「降级**不能**恢复 9008 免授权」

绝不输出「降级到某版本可恢复」这类结论。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .arb_db import compare_build_versions, parse_build_version

__all__ = [
    "EdlRecord",
    "EdlRisk",
    "EdlResult",
    "EDL_MODELS",
    "check_edl_risk",
    "format_edl_hint",
]


class EdlRisk(Enum):
    """9008 免授权风险等级。"""

    SAFE = "safe"
    #: 升级会触发熔断（当前版本低于阈值）
    WILL_FUSE = "will_fuse"
    #: 设备已在阈值版本或之后，熔断已发生或随时可能发生
    LIKELY_FUSED = "likely_fused"
    #: 降级 —— 明确提示不能恢复
    DOWNGRADE_NO_RECOVER = "downgrade_no_recover"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EdlRecord:
    """一条「某机型从某系统版本起会熔断 9008 免授权」的记录。"""

    model: str
    codes: tuple[str, ...]
    #: 触发熔断的业务版本号，格式 ``16.0.3.501``
    fuse_version: str
    display_name: str
    #: 备注（来源与存疑点）
    note: str = ""

    def describe(self) -> str:
        return f"{self.display_name}（{self.model}）自 {self.fuse_version} 起熔断 9008 免授权"


@dataclass
class EdlResult:
    """一次 9008 检查的结果。"""

    risk: EdlRisk
    message: str
    record: EdlRecord | None = None
    package_version: str | None = None
    device_version: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def is_dangerous(self) -> bool:
        return self.risk in (EdlRisk.WILL_FUSE, EdlRisk.LIKELY_FUSED, EdlRisk.DOWNGRADE_NO_RECOVER)

    @property
    def blocks(self) -> bool:
        """是否应硬阻断。

        9008 熔断不会立刻变砖，而是**失去未来的自救通道**，
        因此不硬阻断 —— 只做强提醒，把决定权留给用户。
        """
        return False


#: 9008 免授权熔断机型表。
#:
#:  数据来源见 `note`。目前只有社区单一来源，**统一标注为存疑**，
#: 判定时也只用于提醒，不用于阻断。
EDL_MODELS: tuple[EdlRecord, ...] = (
    EdlRecord(
        model="OPD2404",
        codes=("OPD2404", "OPD2403"),
        fuse_version="16.0.3.501",
        display_name="一加平板 Pro / OnePlus Pad 2",
        note="社区来源（AdaUnlocked 欧加高通解锁指南）指出欧加近年系统更新会主动熔断 9008 免授权；具体起始版本未获官方确认，按 ARB 熔断同为 16.0.3.501 推定",
    ),
    EdlRecord(
        model="PJZ110",
        codes=("PJZ110", "CPH2649", "CPH2653", "CPH2655"),
        fuse_version="16.0.3.501",
        display_name="一加 13",
        note="同上，按 ARB 熔断版本推定",
    ),
)


def _match_record(model_code: str | None, codename: str | None) -> EdlRecord | None:
    keys = {k.upper() for k in (model_code, codename) if k}
    if not keys:
        return None
    for rec in EDL_MODELS:
        if any(c.upper() in keys for c in rec.codes):
            return rec
        if rec.model.upper() in keys:
            return rec
    return None


def check_edl_risk(
    *,
    package_version: str | None,
    device_version: str | None = None,
    model_code: str | None = None,
    codename: str | None = None,
) -> EdlResult:
    """检查「把某个包刷到这台设备」对 9008 免授权的影响。

    判定顺序：

    1. 机型未收录 → UNKNOWN（不提醒）
    2. 包版本或设备版本读不出 → UNKNOWN
    3. 包版本 < 设备版本（降级）→ DOWNGRADE_NO_RECOVER
    4. 设备版本 < 阈值 <= 包版本（升级跨越阈值）→ WILL_FUSE
    5. 设备版本 >= 阈值 → LIKELY_FUSED（已熔断或已过窗口期）
    6. 其余 → SAFE
    """
    pkg_ver_text = package_version
    pkg_ver = parse_build_version(package_version) if package_version else None

    rec = _match_record(model_code, codename)
    if rec is None:
        return EdlResult(
            risk=EdlRisk.UNKNOWN,
            message="该机型未收录 9008 免授权熔断数据，无法判断。",
            package_version=pkg_ver_text,
            device_version=device_version,
        )

    fuse_ver = parse_build_version(rec.fuse_version)
    dev_ver = parse_build_version(device_version) if device_version else None

    base_details = [
        f"机型：{rec.describe()}",
        f"包的版本：{pkg_ver_text or '未能识别'}",
        f"设备当前：{device_version or '未能识别'}",
        f"备注：{rec.note}",
    ]

    if pkg_ver is None or dev_ver is None:
        return EdlResult(
            risk=EdlRisk.UNKNOWN,
            message="版本号读取不完整，无法判断 9008 免授权影响。",
            record=rec,
            package_version=pkg_ver_text,
            device_version=device_version,
            details=base_details,
        )

    # 降级：明确告诉用户救不回来
    if pkg_ver < dev_ver:
        return EdlResult(
            risk=EdlRisk.DOWNGRADE_NO_RECOVER,
            message=(
                "这是降级操作。请注意：降级**不能**恢复 9008 免授权 —— "
                "熔断发生在设备侧，一旦失效即为单向不可逆。"
            ),
            record=rec,
            package_version=pkg_ver_text,
            device_version=device_version,
            details=base_details,
        )

    # 升级跨越阈值
    if fuse_ver is not None and dev_ver < fuse_ver <= pkg_ver:
        return EdlResult(
            risk=EdlRisk.WILL_FUSE,
            message=(
                f"升级到 {rec.fuse_version} 及以上，会使本机的 9008 免授权被永久熔断，"
                "且降级无法恢复。刷入后将失去 EDL 自主救砖通道。"
            ),
            record=rec,
            package_version=pkg_ver_text,
            device_version=device_version,
            details=base_details,
        )

    # 已在阈值之后：区分「同版本重刷 / 升级」与「降级」。
    # 降级已在上面拦截，这里只剩 package_version >= device_version。
    if fuse_ver is not None and dev_ver >= fuse_ver:
        upgraded = pkg_ver > dev_ver
        return EdlResult(
            risk=EdlRisk.LIKELY_FUSED,
            message=(
                f"设备当前版本 {device_version} 已在 {rec.fuse_version} 之后，"
                "9008 免授权可能已被熔断。"
                "该状态由设备侧决定，无法通过刷 ROM 恢复。"
                + ("本次为升级，不会改变熔断状态。" if upgraded else "本次为同版本重刷，不会改变熔断状态。")
            ),
            record=rec,
            package_version=pkg_ver_text,
            device_version=device_version,
            details=base_details,
        )

    return EdlResult(
        risk=EdlRisk.SAFE,
        message="包版本与设备版本均低于 9008 熔断阈值，此项无额外风险。",
        record=rec,
        package_version=pkg_ver_text,
        device_version=device_version,
        details=base_details,
    )


def format_edl_hint(result: EdlResult) -> str:
    """把结果格式化成可打印的多行文本（不含颜色）。"""
    lines = [result.message]
    for d in result.details:
        lines.append(f"  · {d}")
    lines.append("  · 判定依据：系统版本号推断，设备侧实际熔断状态无法读取")
    return "\n".join(lines)
