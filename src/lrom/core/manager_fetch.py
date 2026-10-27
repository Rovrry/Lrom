"""从 GitHub Releases 获取 root 管理器（**唯一的联网模块**）。

## 联网协议（写死在代码里，不是只写在文档里）

Lrom 的核心原则是「离线优先，零遥测」。本模块是这个原则的**唯一例外**，
因此把边界收得比文档更严：

1. **域名白名单**：只允许 ``api.github.com`` / ``github.com`` /
   ``objects.githubusercontent.com``。任何其他域名在发出请求**之前**
   就被拒绝（``_check_url``）。
2. **强制 HTTPS**：明文 HTTP 一律拒绝，不做「降级重试」。
3. **完整性校验**：下载后必须与 Release API 给出的 ``digest``
   （``sha256:...``）一致。校验失败立即删除文件并报错，
   **绝不留下一个来路不明但「看起来下好了」的文件**。
4. **只有显式命令才联网**：``lrom root`` 主流程永不联网，
   只有用户主动执行 ``lrom root fetch-manager`` 时才会走到这里。
5. **下载前公示**：先打印即将访问的域名、文件名、大小、摘要，
   用户确认后才发起下载。
6. **零遥测**：不发送任何设备信息、序列号、统计。请求头只有
   ``User-Agent: lrom/<版本>``（GitHub 要求必须带 UA）和 Accept。

除此之外不接受任何远程指令、不下发可执行脚本、不读取远程配置。
远程内容只被当作「文件」对待，绝不执行。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ALLOWED_HOSTS",
    "ManagerSource",
    "MANAGER_SOURCES",
    "ReleaseAsset",
    "Release",
    "FetchError",
    "fetch_latest_release",
    "pick_asset",
    "download_asset",
    "install_manager_apk",
]

#: 唯一允许联网的域名。不在此列表的一律拒绝。
ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
    }
)

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 30


class FetchError(Exception):
    """下载/查询失败。"""


# ---------------------------------------------------------------------------
# 渠道定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagerSource:
    """一个 root 管理器来源。"""

    key: str
    label: str
    repo: str
    #: asset 名必须匹配的正则
    asset_pattern: str


MANAGER_SOURCES: dict[str, ManagerSource] = {
    "ksu": ManagerSource(
        key="ksu",
        label="KernelSU（官方）",
        repo="tiann/KernelSU",
        asset_pattern=r"^KernelSU_.*\.apk$",
    ),
    "ksu-next": ManagerSource(
        key="ksu-next",
        label="KernelSU Next",
        repo="KernelSU-Next/KernelSU-Next",
        asset_pattern=r"\.apk$",
    ),
    "sukisu": ManagerSource(
        key="sukisu",
        label="SukiSU-Ultra",
        repo="SukiSU-Ultra/SukiSU-Ultra",
        asset_pattern=r"\.apk$",
    ),
    "magisk": ManagerSource(
        key="magisk",
        label="Magisk（官方）",
        repo="topjohnwu/Magisk",
        asset_pattern=r"^Magisk-.*\.apk$",
    ),
}

#: 明显不适用于手机的 asset 关键词
_APK_EXCLUDE = ("debug", "test", "legacy", "x86", "x86_64")

#: 带架构字样的 asset（不是通用包）
_ARCH_PATTERN = re.compile(
    r"(arm64|armeabi|aarch64|armv7|armeabi-v7a|x86|universal)", re.IGNORECASE
)

#: 特殊变体关键词。同一版本可能同时发布普通版与这些变体，
#: 普通用户应默认用不带变体标记的正式包。
#: - spoofed：AVC 日志伪装版（SukiSU 等），功能变体，非默认
#: - nosu / noksu：不含内置 su 的变体
#: - debug / test / legacy：开发或旧版
_VARIANT_EXCLUDE = ("spoofed", "nosu", "noksu", "debug", "test", "legacy")

#: 架构关键词 → 归一的架构族
_ARCH_FAMILY = (
    (re.compile(r"arm64|aarch64", re.I), "arm64"),
    (re.compile(r"armeabi|armv7", re.I), "arm"),
    (re.compile(r"x86_64|x86", re.I), "x86_64"),
    (re.compile(r"riscv", re.I), "riscv64"),
)


def _asset_arch(name: str) -> str | None:
    """从文件名推断架构族；无架构字样返回 None（视为通用包）。"""
    for pat, fam in _ARCH_FAMILY:
        if pat.search(name):
            return fam
    return None


def _abi_family(abi: str | None) -> str | None:
    """把设备 ABI 归一成架构族。"""
    if not abi:
        return None
    a = abi.lower()
    if "arm64" in a or "aarch64" in a:
        return "arm64"
    if "armeabi" in a or "armv7" in a:
        return "arm"
    if "x86" in a:
        return "x86_64"
    if "riscv" in a:
        return "riscv64"
    return None


@dataclass
class ReleaseAsset:
    name: str
    url: str
    size: int
    digest: str | None = None

    @property
    def sha256(self) -> str | None:
        """从 ``sha256:xxx`` 形式的 digest 中取出十六进制摘要。"""
        if self.digest and self.digest.startswith("sha256:"):
            return self.digest.split(":", 1)[1]
        return None


@dataclass
class Release:
    tag: str
    prerelease: bool
    published_at: str
    html_url: str
    assets: list[ReleaseAsset] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV")


# ---------------------------------------------------------------------------
# 网络访问（受控）
# ---------------------------------------------------------------------------


def _check_url(url: str) -> None:
    """在真正发请求之前校验 URL 是否在白名单内且为 HTTPS。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise FetchError(
            f"拒绝非 HTTPS 地址：{url}\n"
            "Lrom 只允许加密连接（不允许降级到 HTTP）。"
        )
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise FetchError(
            f"拒绝访问未授权域名：{host}\n"
            f"Lrom 只允许访问：{', '.join(sorted(ALLOWED_HOSTS))}\n"
            "这是项目的联网协议，不可绕过。"
        )


def _request(url: str, *, accept: str = "application/vnd.github+json"):
    _check_url(url)
    from .. import __version__

    req = urllib.request.Request(
        url,
        headers={
            # GitHub 要求必须带 User-Agent；这是唯一随请求发出的标识
            "User-Agent": f"lrom/{__version__}",
            "Accept": accept,
        },
    )
    ctx = ssl.create_default_context()
    return urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx)


def _get_json(url: str) -> dict | list:
    try:
        with _request(url) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise FetchError(f"仓库或 Release 不存在：{url}") from exc
        if exc.code == 403:
            raise FetchError(
                "GitHub API 拒绝访问（403）——可能是请求频率超限，"
                "请稍后重试。"
            ) from exc
        raise FetchError(f"GitHub API 返回 {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"网络不可达：{exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError("网络请求超时。") from exc


# ---------------------------------------------------------------------------
# Release 查询
# ---------------------------------------------------------------------------


def fetch_latest_release(source: ManagerSource, *, channel: str = "stable") -> Release:
    """获取最新 Release。

    Args:
        channel: ``stable`` 取 GitHub 的 latest 指针；
                 ``beta`` 取列表里最新的 prerelease。
    """
    if channel not in ("stable", "beta"):
        raise FetchError(f"未知渠道 {channel!r}（支持 stable / beta）")

    if channel == "stable":
        data = _get_json(f"{_GITHUB_API}/repos/{source.repo}/releases/latest")
        if not isinstance(data, dict):
            raise FetchError("GitHub 返回了意外的数据格式。")
        return _parse_release(data)

    # beta：拉列表，找最新的 prerelease；没有 prerelease 就退回最新 release
    data = _get_json(f"{_GITHUB_API}/repos/{source.repo}/releases?per_page=20")
    if not isinstance(data, list) or not data:
        raise FetchError(f"{source.repo} 没有任何 Release。")
    for item in data:
        if item.get("prerelease"):
            return _parse_release(item)
    # 没有预发布版，退回第一个（最新的）
    return _parse_release(data[0])


def _parse_release(data: dict) -> Release:
    assets = []
    for a in data.get("assets", []):
        assets.append(
            ReleaseAsset(
                name=a.get("name", ""),
                url=a.get("browser_download_url", ""),
                size=a.get("size", 0),
                digest=a.get("digest"),
            )
        )
    return Release(
        tag=data.get("tag_name", ""),
        prerelease=bool(data.get("prerelease")),
        published_at=data.get("published_at", ""),
        html_url=data.get("html_url", ""),
        assets=assets,
    )


# ---------------------------------------------------------------------------
# asset 选择
# ---------------------------------------------------------------------------


def pick_asset(
    release: Release,
    source: ManagerSource,
    *,
    want_apk: bool = True,
    device_abi: str | None = None,
) -> ReleaseAsset:
    """从 Release 里挑出合适的那一个文件。

    规则（宁可报错也不猜错）：

    1. 匹配 source.asset_pattern
    2. 排除 debug/test/legacy/x86(非 arm) 等明显不适用的
    3. 排除特殊变体（spoofed / nosu / noksu 等），普通用户用普通版
    4. 有设备 ABI 时，排除架构不符的（无架构字样的通用包不受影响）
    5. 多个候选时优先「不含架构字样」的通用包
    6. 仍无法唯一确定 -> 抛错并列出全部候选，让用户自己选
    """
    pattern = re.compile(source.asset_pattern)
    candidates = [a for a in release.assets if pattern.search(a.name)]

    if not candidates:
        names = ", ".join(a.name for a in release.assets) or "（无 asset）"
        raise FetchError(
            f"{source.label} 的 Release {release.tag} 里没有匹配 "
            f"{source.asset_pattern} 的文件。\n可用的 asset：{names}"
        )

    def _narrow(pred) -> None:
        """按条件收窄候选；收窄后若为空则保留原候选（避免过度过滤）。"""
        nonlocal candidates
        if len(candidates) <= 1:
            return
        kept = [a for a in candidates if pred(a)]
        if kept:
            candidates = kept

    if want_apk:
        _narrow(lambda a: not any(k in a.name.lower() for k in _APK_EXCLUDE))
        # 特殊变体：spoofed / nosu 等，普通用户默认不用
        _narrow(lambda a: not any(k in a.name.lower() for k in _VARIANT_EXCLUDE))
        # 架构匹配：有设备 ABI 时优先同架构
        fam = _abi_family(device_abi)
        if fam is not None:
            _narrow(
                lambda a: _asset_arch(a.name) in (None, fam)
            )

    if len(candidates) == 1:
        return candidates[0]

    # 有多个候选：先看有没有「唯一一个」不带架构字样的通用包
    generic = [a for a in candidates if not _ARCH_PATTERN.search(a.name)]
    if len(generic) == 1:
        return generic[0]

    # 仍然无法唯一确定 —— 宁可报错也不猜
    names = "\n  ".join(f"{a.name}（{a.size:,} 字节）" for a in candidates)
    raise FetchError(
        f"{source.label} {release.tag} 有多个候选文件，无法自动确定：\n  {names}\n"
        "请用 --asset <文件名> 明确指定。"
    )


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


def download_asset(
    asset: ReleaseAsset,
    dest_dir: Path,
    *,
    filename: str | None = None,
    expected_sha256: str | None = None,
    on_progress=None,
) -> Path:
    """下载 asset，并做完整性校验。

    校验失败会删除已下载的文件再抛错 —— 绝不留下可疑产物。
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (filename or asset.name)

    expected = (expected_sha256 or asset.sha256 or "").lower()
    if not expected:
        raise FetchError(
            f"无法获取 {asset.name} 的摘要，拒绝下载。\n"
            "Lrom 的联网协议要求每个下载都必须可校验。"
        )

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".lrom-download-", dir=str(dest_dir)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    h = hashlib.sha256()
    written = 0
    try:
        with _request(asset.url, accept="application/octet-stream") as resp:
            total = asset.size or int(resp.headers.get("Content-Length") or 0)
            with open(tmp_path, "wb") as fh:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    fh.write(chunk)
                    h.update(chunk)
                    written += len(chunk)
                    if on_progress:
                        on_progress(written, total)
        actual = h.hexdigest()
        if actual != expected:
            raise FetchError(
                f"校验失败！{asset.name}\n"
                f"  期望 sha256: {expected}\n"
                f"  实际 sha256: {actual}\n"
                "文件已丢弃，未写入磁盘。可能是下载被截断或来源被篡改。"
            )
        os.replace(tmp_path, target)
    except FetchError:
        tmp_path.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError) as exc:
        tmp_path.unlink(missing_ok=True)
        raise FetchError(f"下载失败：{exc}") from exc
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise FetchError(f"写入失败：{exc}") from exc

    return target


def resolve_manager_source(key: str) -> ManagerSource:
    src = MANAGER_SOURCES.get(key)
    if src is None:
        raise FetchError(
            f"未知管理器 {key!r}。支持："
            + ", ".join(sorted(MANAGER_SOURCES))
        )
    return src


def install_manager_apk(
    apk_path: Path,
    *,
    adb_server_port: int | None = None,
    on_line=None,
) -> tuple[str, str]:
    """把已下载的管理器 APK 安装（推送）到设备。

    返回 ``(status, detail)``，status 取值：

    - ``"installed"``：已成功安装（detail 里是 adb 输出）
    - ``"no_device"``：没有可用设备（detail 里是可给用户的提示）
    - ``"failed"``：有设备但安装失败（detail 里是错误）

    为什么不直接 subprocess：统一走 AdbRunner，与其他命令同源，
    便于在测试里替换。
    """
    from ..device.discover import DeviceMode, detect
    from ..device.runner import AdbRunner

    apk_path = Path(apk_path)
    if not apk_path.is_file():
        return ("failed", f"APK 不存在: {apk_path}")

    dev = detect(adb_server_port=adb_server_port)
    if dev is None or dev.mode is not DeviceMode.ADB:
        return (
            "no_device",
            "未连接处于 ADB（开机）模式的设备，无法自动安装。\n"
            "  请用数据线连接设备、开启 USB 调试后重试，\n"
            f"  或手动安装：adb install -r {apk_path}",
        )

    adb = AdbRunner(server_port=adb_server_port)
    adb.serial = dev.serial
    # -r 覆盖安装；-d 允许降级（用户可能换回旧版）
    res = adb.run("install", "-r", "-d", str(apk_path), timeout=300)
    if res.exit_code != 0 or "Failure" in (res.stdout + res.stderr):
        out = (res.stderr or res.stdout or "").strip()
        return ("failed", out or f"adb install 退出码 {res.exit_code}")
    return ("installed", (res.stdout or "").strip())
