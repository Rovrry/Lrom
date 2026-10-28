"""脱机 Root（offline_root）测试：ksud/lkm 资产选择、引擎获取、patch 输出目录处理。"""

from __future__ import annotations

import types

import pytest

from lrom.core import offline_root as orm
from lrom.core.manager_fetch import Release, ReleaseAsset


def _rel(assets):
    return Release(
        tag="v3.3.1", prerelease=False, published_at="", html_url="",
        assets=[ReleaseAsset(name=n, url="https://x", size=1, digest=None) for n in assets],
    )


def test_pick_ksud_prefers_musl():
    rel = _rel([
        "ksud-x86_64-unknown-linux-gnu",
        "ksud-x86_64-unknown-linux-musl",
        "ksud-aarch64-unknown-linux-musl",
        "KernelSU_v3.3.1_32680-release.apk",
    ])
    a = orm._pick_ksud_asset(rel)
    assert a.name == "ksud-x86_64-unknown-linux-musl"


def test_pick_ksud_raises_when_missing():
    rel = _rel(["ksud-aarch64-unknown-linux-musl", "some.apk"])
    from lrom.core.manager_fetch import FetchError

    with pytest.raises(FetchError):
        orm._pick_ksud_asset(rel)


def test_kmi_to_ksud_format():
    # derive_kmi 输出 6.1-android14-11 → ksud 要 android14-6.1
    assert orm.kmi_to_ksud("6.1-android14-11") == "android14-6.1"
    assert orm.kmi_to_ksud("6.6-android15-8") == "android15-6.6"
    # 已是 ksud 格式则原样
    assert orm.kmi_to_ksud("android16-6.12") == "android16-6.12"
    assert orm.kmi_to_ksud(None) is None


def test_ensure_engine_uses_local_ksud_first(tmp_path):
    """本地有 ksud 时不联网，直接返回。"""
    ksud = tmp_path / "ksud"
    ksud.write_bytes(b"#!/bin/sh\n")
    e = orm.ensure_engine(tmp_path, kmi="6.1-android14-11", ksud_path=str(ksud), allow_download=False)
    assert e.ksud == ksud
    assert e.version == "(local)"


def test_ensure_engine_no_ksud_no_download_raises(tmp_path, monkeypatch):
    """无本地 ksud 且禁止下载 → 报错（不静默）。"""
    monkeypatch.setattr("lrom.device.tools.find_tool", lambda name: (_ for _ in ()).throw(Exception("no")))
    from lrom.core.manager_fetch import FetchError

    with pytest.raises(FetchError):
        orm.ensure_engine(tmp_path, kmi=None, allow_download=False)


def test_patch_with_ksud_output_dir_handling(tmp_path, monkeypatch):
    """patch_with_ksud 应把 -o 传成目录（ksud 需要目录），并从输出解析产物。"""
    captured = {}

    def fake_run(argv, timeout=None, on_line=None):
        captured["argv"] = argv
        # 模拟 ksud 在目录里生成文件
        out_dir = argv[argv.index("-o") + 1]
        import pathlib
        p = pathlib.Path(out_dir) / "kernelsu_patched_x.img"
        p.write_bytes(b"patched")
        return types.SimpleNamespace(
            timed_out=False, exit_code=0, combined=f"- Output file is written to\n- {p}\n", stdout="", stderr=""
        )

    monkeypatch.setattr("lrom.core.root.run", fake_run)
    img = tmp_path / "init_boot.img"
    img.write_bytes(b"\x00" * 16)
    out = tmp_path / "sub" / "patched.img"  # 文件路径
    from lrom.core.root import patch_with_ksud

    res = patch_with_ksud(image=img, output=out, ksud="/usr/bin/ksud", kmi="6.1-android14-11",
                          module=tmp_path / "kernelsu.ko")
    # -o 必须是目录（out 的父目录）
    o_val = captured["argv"][captured["argv"].index("-o") + 1]
    assert o_val == str(tmp_path / "sub")
    assert res.ok
    assert res.output.name == "kernelsu_patched_x.img"
