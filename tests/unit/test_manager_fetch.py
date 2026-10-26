"""下载模块测试（manager_fetch）。

重点锁住**联网协议**的硬约束 —— 这些不是文档承诺，是代码级拒绝：
  1. 非白名单域名必须拒绝（在发请求之前）
  2. 非 HTTPS 必须拒绝
  3. 摘要缺失必须拒绝下载
  4. 校验失败必须删除文件

这些测试全部不联网（monkeypatch 掉真实请求）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lrom.core import manager_fetch as mf


# ---------------------------------------------------------------------------
# 联网协议：域名与协议
# ---------------------------------------------------------------------------


def test_allowed_hosts_is_exactly_three():
    """白名单必须是精确的三个域名，不能被随便扩大。"""
    assert mf.ALLOWED_HOSTS == {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/x.apk",
        "https://raw.githubusercontent.com/x/y",
        "https://pypi.org/simple/",
        "https://api.github.com.evil.com/x",
        "https://codeload.github.com/x",
    ],
)
def test_reject_non_whitelisted_hosts(url: str):
    with pytest.raises(mf.FetchError) as exc:
        mf._check_url(url)
    assert "未授权域名" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/x.apk",
        "http://api.github.com/repos/a/b",
        "ftp://github.com/x",
    ],
)
def test_reject_non_https(url: str):
    with pytest.raises(mf.FetchError) as exc:
        mf._check_url(url)
    assert "非 HTTPS" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/tiann/KernelSU/releases/latest",
        "https://github.com/tiann/KernelSU",
        "https://objects.githubusercontent.com/abc",
    ],
)
def test_accept_whitelisted_urls(url: str):
    mf._check_url(url)  # 不抛异常即通过


def test_https_enforced_before_host_check():
    """非 HTTPS 的请求不应该先暴露域名检查细节。"""
    with pytest.raises(mf.FetchError) as exc:
        mf._check_url("http://evil.com/x")
    assert "非 HTTPS" in str(exc.value)


# ---------------------------------------------------------------------------
# asset 选择
# ---------------------------------------------------------------------------


def _release(*names, prerelease=False, tag="v1.0.0"):
    return mf.Release(
        tag=tag,
        prerelease=prerelease,
        published_at="2026-10-27T00:00:00Z",
        html_url="https://github.com/x/y/releases/tag/v1.0.0",
        assets=[
            mf.ReleaseAsset(
                name=n, url=f"https://github.com/x/{n}", size=1000
            )
            for n in names
        ],
    )


def test_pick_ksu_apk_ignores_non_apk_assets():
    src = mf.MANAGER_SOURCES["ksu"]
    rel = _release(
        "KernelSU_v3.3.1_32680-release.apk",
        "ksud-x86_64-unknown-linux-musl",
        "ksuinit-aarch64",
        "lkm-aarch64-android14-6.1_kernelsu.ko",
        tag="v3.3.1",
    )
    asset = mf.pick_asset(rel, src)
    assert asset.name == "KernelSU_v3.3.1_32680-release.apk"


def test_pick_magisk_apk():
    src = mf.MANAGER_SOURCES["magisk"]
    rel = _release("Magisk-v31.1.apk", "v31.1.zip", "app-debug.apk")
    asset = mf.pick_asset(rel, src)
    assert asset.name == "Magisk-v31.1.apk"


def test_pick_asset_raises_when_none_match():
    src = mf.MANAGER_SOURCES["ksu"]
    rel = _release("something.zip", "other.tar")
    with pytest.raises(mf.FetchError) as exc:
        mf.pick_asset(rel, src)
    assert "没有匹配" in str(exc.value)


def test_pick_asset_ambiguous_raises_and_lists():
    """多个候选且无法判断时必须报错，不能瞎猜。"""
    src = mf.MANAGER_SOURCES["ksu-next"]
    rel = _release(
        "KernelSU_Next_arm64-v8a.apk", "KernelSU_Next_armeabi-v7a.apk"
    )
    with pytest.raises(mf.FetchError) as exc:
        mf.pick_asset(rel, src)
    assert "无法自动确定" in str(exc.value)
    assert "--asset" in str(exc.value)


def test_pick_asset_prefers_generic_over_arch():
    src = mf.MANAGER_SOURCES["ksu-next"]
    rel = _release(
        "KernelSU_Next.apk",
        "KernelSU_Next_arm64-v8a.apk",
        "KernelSU_Next_x86_64.apk",
    )
    asset = mf.pick_asset(rel, src)
    assert asset.name == "KernelSU_Next.apk"


# ---------------------------------------------------------------------------
# digest 解析
# ---------------------------------------------------------------------------


def test_asset_sha256_parsing():
    a = mf.ReleaseAsset(
        name="x.apk",
        url="https://github.com/x",
        size=1,
        digest="sha256:abc123",
    )
    assert a.sha256 == "abc123"


def test_asset_sha256_none_when_missing():
    a = mf.ReleaseAsset(name="x.apk", url="https://github.com/x", size=1)
    assert a.sha256 is None


def test_asset_sha256_none_for_other_algorithms():
    a = mf.ReleaseAsset(
        name="x.apk", url="https://github.com/x", size=1, digest="sha512:zzz"
    )
    assert a.sha256 is None


# ---------------------------------------------------------------------------
# 下载：必须校验
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload: bytes, headers=None):
        self._payload = payload
        self._pos = 0
        self.headers = headers or {}

    def read(self, n=-1):
        if n < 0:
            chunk = self._payload[self._pos :]
            self._pos = len(self._payload)
            return chunk
        chunk = self._payload[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_download_refuses_without_digest(tmp_path: Path):
    """没有摘要的 asset 必须拒绝 —— 不可校验的下载不允许发生。"""
    a = mf.ReleaseAsset(
        name="x.apk", url="https://github.com/x/x.apk", size=10
    )
    with pytest.raises(mf.FetchError) as exc:
        mf.download_asset(a, tmp_path)
    assert "摘要" in str(exc.value)


def test_download_verifies_and_writes(tmp_path: Path, monkeypatch):
    payload = b"hello apk content"
    digest = hashlib.sha256(payload).hexdigest()
    a = mf.ReleaseAsset(
        name="good.apk",
        url="https://github.com/x/good.apk",
        size=len(payload),
        digest=f"sha256:{digest}",
    )
    monkeypatch.setattr(mf, "_request", lambda url, **kw: FakeResponse(payload))

    out = mf.download_asset(a, tmp_path)
    assert out.is_file()
    assert out.read_bytes() == payload


def test_download_rejects_bad_checksum_and_cleans_up(tmp_path: Path, monkeypatch):
    payload = b"tampered"
    a = mf.ReleaseAsset(
        name="bad.apk",
        url="https://github.com/x/bad.apk",
        size=len(payload),
        digest="sha256:" + "0" * 64,
    )
    monkeypatch.setattr(mf, "_request", lambda url, **kw: FakeResponse(payload))

    with pytest.raises(mf.FetchError) as exc:
        mf.download_asset(a, tmp_path)
    assert "校验失败" in str(exc.value)
    # 关键：不能留下任何文件
    assert list(tmp_path.iterdir()) == []


def test_download_progress_callback(tmp_path: Path, monkeypatch):
    payload = b"x" * 1000
    digest = hashlib.sha256(payload).hexdigest()
    a = mf.ReleaseAsset(
        name="p.apk",
        url="https://github.com/x/p.apk",
        size=1000,
        digest=f"sha256:{digest}",
    )
    monkeypatch.setattr(mf, "_request", lambda url, **kw: FakeResponse(payload))

    calls = []
    mf.download_asset(
        a, tmp_path, on_progress=lambda d, t: calls.append((d, t))
    )
    assert calls
    assert calls[-1][0] == 1000


# ---------------------------------------------------------------------------
# Release 解析
# ---------------------------------------------------------------------------


def test_parse_release_extracts_fields():
    data = {
        "tag_name": "v3.3.1",
        "prerelease": False,
        "published_at": "2026-10-01T00:00:00Z",
        "html_url": "https://github.com/tiann/KernelSU/releases/tag/v3.3.1",
        "assets": [
            {
                "name": "KernelSU_v3.3.1.apk",
                "browser_download_url": "https://github.com/a/b.apk",
                "size": 123,
                "digest": "sha256:" + "a" * 64,
            }
        ],
    }
    rel = mf._parse_release(data)
    assert rel.tag == "v3.3.1"
    assert rel.version == "3.3.1"
    assert len(rel.assets) == 1
    assert rel.assets[0].sha256 == "a" * 64


def test_resolve_unknown_source():
    with pytest.raises(mf.FetchError) as exc:
        mf.resolve_manager_source("nope")
    assert "未知管理器" in str(exc.value)


def test_all_sources_defined():
    assert set(mf.MANAGER_SOURCES) == {"ksu", "ksu-next", "sukisu", "magisk"}
    for src in mf.MANAGER_SOURCES.values():
        assert "/" in src.repo
        assert src.asset_pattern


def test_invalid_channel_rejected():
    src = mf.MANAGER_SOURCES["ksu"]
    with pytest.raises(mf.FetchError) as exc:
        mf.fetch_latest_release(src, channel="nightly")
    assert "渠道" in str(exc.value)
