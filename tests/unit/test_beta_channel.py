"""内测/Beta 版只读判定测试（detect_beta_channel）。

背景：一加内测包通常仍是 user + release-keys（跟正式版版本号可能一样），
不能只看单个 prop 就断言。本函数给"倾向性"而非断言。
"""

from __future__ import annotations

from lrom.device.discover import BetaInfo, detect_beta_channel


class _FakeAdb:
    def __init__(self, props: dict[str, str]):
        self._props = props
        self.serial = None

    def getprop(self, name: str, timeout: float = 15.0) -> str:
        return self._props.get(name, "")


def _run(props):
    return detect_beta_channel(_FakeAdb(props), "X")


def test_stable_release_detected():
    bi = _run(
        {
            "ro.build.type": "user",
            "ro.build.tags": "release-keys",
            "ro.build.description": "qssi-user 16 BP2A release-keys",
            "ro.build.display.id": "OPD2404_16.0.5.1201",
        }
    )
    assert bi.suspicion == "stable_likely"


def test_userdebug_is_beta_likely():
    bi = _run({"ro.build.type": "userdebug", "ro.build.tags": "release-keys"})
    assert bi.suspicion == "beta_likely"


def test_test_keys_is_beta_likely():
    bi = _run({"ro.build.type": "user", "ro.build.tags": "test-keys"})
    assert bi.suspicion == "beta_likely"


def test_beta_word_in_display_id_is_beta_likely():
    bi = _run(
        {
            "ro.build.type": "user",
            "ro.build.tags": "release-keys",
            "ro.build.display.id": "PKX110_17.0.0.104 Beta",
        }
    )
    assert bi.suspicion == "beta_likely"


def test_unknown_when_no_signals():
    bi = _run({})
    assert bi.suspicion == "unknown"


def test_beta_info_never_crashes_on_missing(monkeypatch):
    # 全空也要能返回，不抛异常
    bi = _run({"ro.build.type": "", "ro.build.tags": ""})
    assert isinstance(bi, BetaInfo)
    assert bi.suspicion in ("unknown", "beta_likely", "stable_likely")
