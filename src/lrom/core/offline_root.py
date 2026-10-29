"""脱机 Root：PC 侧 patch init_boot，用户只需装 root 管理器。

设计目标（对应用户要求「一键、最后只点装管理器」）：
  1. 自动获取 patcher 引擎 ksud（KernelSU / KernelSU-Next 官方 CLI，x86_64 linux）。
  2. 用 ksud 内置的 LKM（官方 ksud 二进制已内嵌各 KMI 的内核模块，无需单独下载）。
  3. PC 侧 `ksud boot-patch` 把 init_boot.img patch 成可刷镜像。
  4. （由调用方）备份原厂 init_boot、fastboot flash、提示装管理器 APK。

支持引擎（见 docs/research/managers-offline-patch.md §6）：
  · ksu（KernelSU 官方）—— ksud 官方 CLI，
  · ksu-next（KernelSU-Next）—— 同构 ksud，
  · sukisu —— 官方 release 只有 *-linux-android（PC 跑不了）→ 不支持脱机，走 APP
  · magisk —— APK 内藏 x86_64 magiskboot（非官方流程），暂不支持脱机一键
  · apatch —— kptools-linux 打 boot.img 内核（高危），暂不支持

联网协议：沿用 manager_fetch 的域名白名单 + HTTPS + sha256 校验，只从 GitHub 官方
release 下载 ksud。**不引入任何新域名或遥测。**
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .manager_fetch import (
    MANAGER_SOURCES,
    FetchError,
    Release,
    ReleaseAsset,
    download_asset,
    fetch_latest_release,
)
from .root import PatchResult, patch_with_ksud


#: 支持「脱机一键」的引擎（key → manager_fetch 的 source key）
#: ksu / ksu-next 都用 ksud 官方 CLI，机制相同。
OFFLINE_ENGINES = {
    "ksu": "ksu",
    "ksu-next": "ksu-next",
}
#: 明确不支持脱机一键的管理器（需 APP 手动 / 暂未实现）
UNSUPPORTED_OFFLINE = {
    "sukisu": "SukiSU 官方 Release 不提供 PC 可用的 host ksud（只有 linux-android 版，PC 跑不起来），请用 APP 手动 patch。",
    "magisk": "Magisk 官方只支持 APP 内 patch；PC 侧走非官方流程（APK 内 magiskboot），本版本暂不提供。请用 APP 手动 patch。",
    "apatch": "APatch 走 kptools-linux 打 boot.img 内核（高危、需 SuperKey），本版本暂不提供脱机一键。",
}


@dataclass
class OfflineRootEngine:
    """脱机 root 所需的本地产物。"""

    ksud: Path
    lkm: Path | None = None          # 通常为 None（ksud 内置 LKM）
    version: str = ""
    engine: str = "ksu"
    warnings: list[str] = field(default_factory=list)


def kmi_to_ksud(kmi: str | None) -> str | None:
    """把 derive_kmi 的输出（如 ``6.1-android14-11``）转成 ksud 的 --kmi 格式
    （``android14-6.1``）。

    ksud 内部按 ``<arch>/<androidXX>-<kernel>_kernelsu.ko`` 查内置 LKM，
    所以必须是 ``android14-6.1`` 这种格式；直接传 derive_kmi 的
    ``6.1-android14-11`` 会报 asset not found（[实测] 曾复现）。
    已是 androidXX-kernel 格式则原样返回。
    """
    if not kmi:
        return None
    kmi = kmi.strip()
    if kmi.startswith("android"):
        return kmi
    # 6.1-android14-11 → kernel=6.1, android=14
    m = re.match(r"^(?P<kernel>\d+\.\d+)-android(?P<av>\d+)", kmi)
    if m:
        return f"android{m.group('av')}-{m.group('kernel')}"
    return kmi


def _pick_ksud_asset(release: Release) -> ReleaseAsset:
    """从 release 挑 PC 侧可用的 ksud 二进制（x86_64 linux musl 优先）。"""
    names = {a.name: a for a in release.assets}
    for cand in (
        "ksud-x86_64-unknown-linux-musl",
        "ksud-x86_64-unknown-linux-gnu",
    ):
        if cand in names:
            return names[cand]
    raise FetchError(
        "该管理器的 Release 里没有 x86_64 linux 版 ksud。"
        f"现有 asset：{', '.join(sorted(names))[:300]}"
    )


def ensure_engine(
    dest_dir: Path,
    *,
    kmi: str | None,
    engine: str = "ksu",
    ksud_path: str | Path | None = None,
    allow_download: bool = True,
    on_notice=None,
) -> OfflineRootEngine:
    """确保 ksud 引擎就位；缺失时从对应管理器官方 release 下载。

    - 本地已有 ksud（ksud_path 或 PATH）→ 直接用，不联网。
    - 否则（allow_download）→ 从 GitHub 下载 ksud（内置 LKM，不单独下 .ko）。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    if engine in UNSUPPORTED_OFFLINE:
        raise FetchError(UNSUPPORTED_OFFLINE[engine])
    if engine not in OFFLINE_ENGINES:
        raise FetchError(f"未知的脱机引擎：{engine}")

    # 1) 本地 ksud 优先
    local_ksud: Path | None = None
    if ksud_path:
        p = Path(ksud_path)
        if p.is_file():
            local_ksud = p
    if local_ksud is None:
        from ..device.tools import find_tool
        try:
            local_ksud = Path(find_tool("ksud"))
        except Exception:  # noqa: BLE001
            local_ksud = None

    if local_ksud is not None and local_ksud.is_file():
        if on_notice:
            on_notice(f"使用本地 ksud：{local_ksud}")
        return OfflineRootEngine(ksud=local_ksud, version="(local)", engine=engine)

    if not allow_download:
        raise FetchError(
            "未找到 ksud 引擎，且当前禁止联网下载。\n"
            "请把 ksud 放到 PATH 或用 LROM_KSUD 指定，或允许联网自动下载。"
        )

    # 2) 从对应管理器官方 release 下载 ksud（内置 LKM）
    src_key = OFFLINE_ENGINES[engine]
    if on_notice:
        on_notice(f"未找到本地 ksud，正在从 {MANAGER_SOURCES[src_key].label} 官方 Release 获取 patcher 引擎…")
    release = fetch_latest_release(MANAGER_SOURCES[src_key], channel="stable")
    ksud_asset = _pick_ksud_asset(release)
    ksud_local = dest_dir / "ksud"
    download_asset(ksud_asset, dest_dir, filename="ksud", on_progress=None)
    try:
        ksud_local.chmod(0o755)
    except OSError:
        pass
    if on_notice:
        on_notice(f"ksud 已就绪（{release.tag}）：{ksud_local}")

    if not kmi:
        warnings.append("未知 KMI，patch 时可能无法匹配内置内核模块；建议先确认内核版本。")

    return OfflineRootEngine(ksud=ksud_local, lkm=None, version=release.tag,
                             engine=engine, warnings=warnings)


def offline_patch(
    image: Path,
    output: Path,
    *,
    kmi: str | None,
    engine: OfflineRootEngine,
    on_line=None,
) -> PatchResult:
    """用就位的 ksud 引擎在 PC 侧 patch init_boot（ksud 内置 LKM）。"""
    return patch_with_ksud(
        image=image,
        output=output,
        ksud=str(engine.ksud),
        kmi=kmi_to_ksud(kmi),
        module=None,  # ksud 已内置 LKM，不再单独传 --module
        on_line=on_line,
    )
