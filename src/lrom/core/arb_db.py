"""ARB 熔断机型数据库 —— 硬编码的「已熔断」机型与版本。

## 这是什么

2026 年起，OPPO / 一加 / 真我 通过 OTA 把部分机型的
``xbl_config`` 里的 Anti-Rollback Version 从 0 提到 1。
一旦设备升到这个版本，**降级刷入 ARB 更低的包会直接黑砖，不可恢复**。

社区实测（[OnePlus ARB Checker](https://github.com/Bartixxx32/Oneplus-antirollchecker)，
GitHub Actions 每日扫描各区域 OTA 包）已确认一批机型中招。
本模块把这些数据硬编码进来，**在刷写前做兜底拦截**。

## 为什么硬编码而不是联网查

1. **离线优先** —— Lrom 的核心链路不依赖任何服务器
2. **熔断数据变化极慢**（一年也就几个版本），不值得为它开一个网络依赖
3. **这是最后一道防线** —— 网络不可用时它必须仍然有效

代价是需要跟着上游更新。发现新机型中招时，改这个文件即可。

## ⚠️ 数据可信度分级

每条记录都标注 ``confidence``：

- ``CONFIRMED``  —— 多方交叉验证（固件站 + ARB 追踪表 + 社区实测）
- ``REPORTED``   —— 单一来源（通常是 XDA 主帖），未经固件站确认

**只有 CONFIRMED 的记录才做硬阻断。** REPORTED 的只警告不阻断，
避免因为一条存疑数据把用户挡在正常刷机之外。

## ⚠️ 三道防线的关系

本模块是**第三道**，也是最粗的一道（只看机型 + 版本号）。

前两道更精确，应该优先：
  1. ``lrom arb``         —— 直接比包与设备的 ARB 值（最准，需要 root 或 fetch）
  2. 设备侧 ``ro.boot.…`` —— 部分机型能读到熔断状态

本模块的价值在于：**用户没跑 arb 就直接 flash 时，还能兜住。**

## 数据来源与更新日期

- 触发版本：大侠阿木固件站（yun.daxiaamu.com）各机型页的「重大提醒」
- ARB index 实测：[Bartixxx32/Oneplus-antirollchecker](https://github.com/Bartixxx32/Oneplus-antirollchecker)
- 官方口径：Android Authority / 9to5Google 2026-01 报道

**最后更新：2026-10-27**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Confidence",
    "ArbRecord",
    "ARB_FUSED_MODELS",
    "ArbRisk",
    "check_arb_risk",
    "parse_build_version",
    "compare_build_versions",
]


class Confidence(Enum):
    """数据可信度。"""

    CONFIRMED = "confirmed"  # 多方交叉验证 → 硬阻断
    REPORTED = "reported"  # 单一来源 → 仅警告


@dataclass(frozen=True)
class ArbRecord:
    """一条「某机型从某版本起已熔断」的记录。"""

    model: str
    #: 型号代码（OPD2404 / PJZ110 / CPH2649 …）。用于与包的 pre-device、
    #: 以及 fastboot getvar product 比对。
    codes: tuple[str, ...]
    #: 触发熔断的业务版本号，格式 ``16.0.3.501``
    trigger_version: str
    #: 人类可读的机型名
    display_name: str
    confidence: Confidence = Confidence.CONFIRMED
    #: 备注（来源或存疑点）
    note: str = ""

    def describe(self) -> str:
        return f"{self.display_name}（{self.model}）自 {self.trigger_version} 起熔断"


#: 已确认熔断的机型表。
#:
#: ⚠️ 只收录**有交叉验证**的机型。存疑的单独标注 confidence=REPORTED。
ARB_FUSED_MODELS: tuple[ArbRecord, ...] = (
    # --- 第一波，CONFIRMED（固件站 + 追踪表双重确认）---
    ArbRecord(
        model="OPD2404",
        codes=("OPD2404", "OPD2403"),
        trigger_version="16.0.3.501",
        display_name="一加平板 Pro / OnePlus Pad 2",
        note="CN=OPD2404(代号 caihong) 海外=OPD2403(rainbow)，固件站标注 F.27 及之后",
    ),
    ArbRecord(
        model="PJZ110",
        codes=("PJZ110", "CPH2649", "CPH2653", "CPH2655"),
        trigger_version="16.0.3.501",
        display_name="一加 13",
        note="CN=PJZ110 C.84 / IN=CPH2649 / EU,GLO=CPH2653 / NA=CPH2655",
    ),
    ArbRecord(
        model="PKX110",
        codes=("PKX110", "CPH2723"),
        trigger_version="16.0.3.501",
        display_name="一加 13T / 13s",
        note="CN=PKX110 C.39 / IN=CPH2723",
    ),
    ArbRecord(
        model="PKG110",
        codes=("PKG110", "CPH2645", "CPH2691"),
        trigger_version="16.0.3.500",
        display_name="Ace 5 / 一加 13R",
        note="CN=PKG110 F.32 / CPH2645 / CPH2691",
    ),
    ArbRecord(
        model="PJD110",
        codes=("PJD110", "CPH2573", "CPH2581"),
        trigger_version="16.0.3.500",
        display_name="一加 12",
        note="CN=PJD110 F.39 / CPH2573 / CPH2581",
    ),
    ArbRecord(
        model="PJX110",
        codes=("PJX110",),
        trigger_version="16.0.3.500",
        display_name="Ace 3 Pro",
        note="PJX110 F.14 及之后",
    ),
    ArbRecord(
        model="PLQ110",
        codes=("PLQ110",),
        trigger_version="16.0.5.700",
        display_name="Ace 6",
        note="PLQ110 A.39 及之后",
    ),
    # --- 追踪表实测 ARB=1，但固件站无提示 ---
    ArbRecord(
        model="OPD2413",
        codes=("OPD2413",),
        trigger_version="16.0.10.600",
        display_name="OnePlus Pad 2 Pro",
        note="ARB 追踪表实测 ARB=1；固件站无提示，触发版本以最新实测版为界",
    ),
    ArbRecord(
        model="OPD2415",
        codes=("OPD2415",),
        trigger_version="16.0.10.600",
        display_name="OnePlus Pad 3",
        note="ARB 追踪表实测 ARB=1（全 4 区域）",
    ),
    # --- 单一来源，仅警告 ---
    ArbRecord(
        model="Find X7 Ultra",
        codes=(),
        trigger_version="16.0.0.500",
        display_name="OPPO Find X7 Ultra / X6 Pro / Find N3",
        confidence=Confidence.REPORTED,
        note="仅 XDA 主帖提及，未获固件站确认 —— 只警告不阻断",
    ),
)


#: 明确「未熔断」（ARB=0）的机型。
#: 用来避免误伤 —— 如果机型在这张表里，即使版本号看起来高也不阻断。
ARB_SAFE_MODELS: frozenset[str] = frozenset(
    {
        # 综合实测表里 ARB 全为 0 的
        "CPH2747",
        "CPH2745",
        "CPH2749",
        "PLK110",  # 一加 15 —— 媒体常误报熔断，实测全版本 ARB=0
        "CPH2769",
        "CPH2767",  # 一加 15R
        "PLZ110",  # 一加 15T
        "CPH2609",  # 一加 12R
        "CPH2449",  # 一加 11 / 11R
        "PKR110",  # Ace 5 Pro —— 媒体说熔断，实测 ARB=0
        "PLR110",  # Ace 6T
        "PJF110",
        "PJE110",  # Ace 3V / Ace 3
        "CPH2551",  # OnePlus Open
    }
)


# ---------------------------------------------------------------------------
# 版本号解析与比较
# ---------------------------------------------------------------------------


#: 业务版本号形如 16.0.3.501 / 14.1.0.226。
#:
#: 注意不能直接匹配任意 x.y.z —— Android 构建号里也有这种形状：
#:   ``UKQ1.231108.001`` 会被误当成 1.231108
#: 所以要求**整段是纯数字**（前面不能紧跟字母或数字）。
_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})\.(\d{1,2})\.(\d{1,4})")


def parse_build_version(text: str) -> tuple[int, int, int] | None:
    """从版本字符串里取出 ``16.0.3.501`` 这样的业务版本号。

    支持这些输入形式：
      ``OPD2404_16.0.5.1201(CN01)``
      ``16.0.3.501``
      ``PJZ110_16.0.3.501(CN01) C.84``

    注意：只取**前三段数字**，不要被前面的机型代码干扰
    （``OPD2404_`` 里也有数字，但带下划线，正则匹配的是 ``x.y.z`` 形式）。
    """
    if not text:
        return None
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def compare_build_versions(a: str, b: str) -> int | None:
    """比较两个版本号。返回 -1 / 0 / 1；无法解析返回 None。"""
    va = parse_build_version(a)
    vb = parse_build_version(b)
    if va is None or vb is None:
        return None
    if va < vb:
        return -1
    if va > vb:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 风险判定
# ---------------------------------------------------------------------------


class ArbRisk(Enum):
    """熔断风险等级。"""

    SAFE = "safe"  # 确定安全
    WARN = "warn"  # 可疑，警告但放行
    BLOCK = "block"  # 确定熔断，硬阻断
    UNKNOWN = "unknown"  # 数据不足


@dataclass
class ArbCheckResult:
    """一次熔断检查的结果。"""

    risk: ArbRisk
    message: str
    record: ArbRecord | None = None
    #: 包版本 / 设备版本，便于用户核对
    package_version: str | None = None
    device_version: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        return self.risk is ArbRisk.BLOCK


def _match_record(
    model_code: str | None, codename: str | None
) -> ArbRecord | None:
    """按型号代码或代号匹配熔断记录。"""
    keys = {k.upper() for k in (model_code, codename) if k}
    if not keys:
        return None
    for rec in ARB_FUSED_MODELS:
        if any(c.upper() in keys for c in rec.codes):
            return rec
        if rec.model.upper() in keys:
            return rec
    return None


def check_arb_risk(
    *,
    package_version: str | None,
    device_version: str | None = None,
    model_code: str | None = None,
    codename: str | None = None,
) -> ArbCheckResult:
    """检查「把某个包刷到这台设备」是否有熔断风险。

    判定逻辑（按优先级）：

    1. 机型在「安全表」里 → SAFE（不阻断，即使版本号看起来吓人）
    2. 机型命中熔断记录：
       a. 包版本 < 触发版本 → **BLOCK**（降级到熔断前版本 = 黑砖）
       b. 包版本 >= 触发版本 → 若设备当前版本 > 包版本 → **BLOCK**（实为降级）
                              否则 SAFE
    3. 机型未命中任何记录 → UNKNOWN（不阻断，交给 lrom arb 精确判断）

    Args:
        package_version: 包的业务版本号（如 ``16.0.3.501`` 或完整串）。
        device_version: 设备当前版本（可选，用于判断是否为降级）。
        model_code: 设备型号代码（如 ``OPD2404``）。
        codename: 设备代号（如 ``OP5D77L1``）。
    """
    pkg_ver = parse_build_version(package_version) if package_version else None

    # --- 1. 安全表 ---
    keys = {k.upper() for k in (model_code, codename) if k}
    if keys & ARB_SAFE_MODELS:
        return ArbCheckResult(
            risk=ArbRisk.SAFE,
            message="该机型实测未熔断（ARB=0），可以正常刷写。",
            package_version=package_version,
            device_version=device_version,
        )

    # --- 2. 熔断表 ---
    rec = _match_record(model_code, codename)
    if rec is not None:
        trigger = parse_build_version(rec.trigger_version)
        details = [
            f"机型：{rec.describe()}",
            f"包的版本：{package_version or '未能识别'}",
        ]
        if device_version:
            details.append(f"设备当前：{device_version}")
        if rec.note:
            details.append(f"备注：{rec.note}")

        # 包版本读不出来 —— 保守警告，不硬拦
        if pkg_ver is None:
            return ArbCheckResult(
                risk=ArbRisk.WARN,
                message=(
                    f"该机型已知会熔断（{rec.trigger_version} 起），"
                    "但无法识别这个包的版本号。请自行确认后再刷。"
                ),
                record=rec,
                package_version=package_version,
                device_version=device_version,
                details=details,
            )

        # a. 包版本低于触发版本 → 这是熔断前的包
        if trigger is not None and pkg_ver < trigger:
            risk = (
                ArbRisk.BLOCK
                if rec.confidence is Confidence.CONFIRMED
                else ArbRisk.WARN
            )
            return ArbCheckResult(
                risk=risk,
                message=(
                    f"⛔ 这个包是 {rec.trigger_version} 之前的版本，"
                    "而该机型从那时起已熔断 ARB。\n"
                    "    刷入等于降级到熔断前，会导致**永久黑砖**，"
                    "且免授权 9008 已失效，无法自救。"
                ),
                record=rec,
                package_version=package_version,
                device_version=device_version,
                details=details,
            )

        # b. 包版本够新，但若设备版本更高 → 实为降级
        dev_ver = parse_build_version(device_version) if device_version else None
        if dev_ver is not None and pkg_ver < dev_ver:
            return ArbCheckResult(
                risk=ArbRisk.BLOCK,
                message=(
                    "⛔ 包版本低于设备当前版本 —— 这是一次降级。\n"
                    "    该机型的 ARB 已熔断，降级会导致**永久黑砖**。"
                ),
                record=rec,
                package_version=package_version,
                device_version=device_version,
                details=details,
            )

        return ArbCheckResult(
            risk=ArbRisk.SAFE,
            message=(
                f"包版本 {package_version} 不早于熔断触发版本 "
                f"{rec.trigger_version}，且高于设备当前版本，可以刷写。"
            ),
            record=rec,
            package_version=package_version,
            device_version=device_version,
            details=details,
        )

    # --- 3. 未知机型 ---
    return ArbCheckResult(
        risk=ArbRisk.UNKNOWN,
        message=(
            "未收录该机型的熔断数据。\n"
            "    建议先运行 `lrom arb` 精确比对包与设备的 ARB 值；\n"
            "    或在 https://oparb.pages.dev/ 查询你的机型。"
        ),
        package_version=package_version,
        device_version=device_version,
    )


def format_arb_check(result: ArbCheckResult) -> str:
    """把检查结果格式化成给人看的文本。"""
    lines = [result.message]
    if result.details:
        lines.append("")
        for d in result.details:
            lines.append(f"    {d}")
    return "\n".join(lines)
