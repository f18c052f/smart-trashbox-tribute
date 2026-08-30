"""`sensing_foundation.config`（`RuntimeSettings`）に対するテスト（タスク 1.6）。

**ファイル名についての注記**: design.md の File Structure Plan は本ファイルを
`test_config.py` としているが、`tests/prediction_core/test_config.py` が
既に存在し、`tests/` 配下のどのディレクトリにも `__init__.py` が無いため
pytest の既定 import mode（prepend）では同名 test ファイルをツリー全体で
一意にしか扱えず衝突する（タスク 1.5 と同じ事象。tasks.md の
「Implementation Notes」に記録された回避方針を踏襲し、本 Spec 側のみ
`test_sensing_config.py` へ改名している。境界違反ではない）。

観測可能な完了状態（tasks.md 1.6）を固定する:
- 解決順序（CLI 引数 > 環境変数 > 設定ファイル > 既定値）が上位から順に勝つこと
- 必要 RAM 超過・不正 fps・再生時のセッション未指定がいずれも起動時（`resolve()`
  呼び出し時）に `SensingConfigError` として拒否されること
- Color は既定で無効であること
- 解決結果が不変（`frozen=True, slots=True`）であること
- 解像度・fps の既定値が「初期評価候補であり必須性能ではない」旨を docstring に
  明記していること

**RAM 予算チェックの `Sysstat` 依存についての注記**: design.md の Components
table で `RuntimeSettings` の Key Dependencies は `types (P1)` のみが列挙されて
おり、`Sysstat`（L0）は挙げられていない。一方 Invariants の文章は「既定は
搭載 RAM の 25%」と書いており、搭載 RAM の実測値がどこかから来る必要がある。
本実装はこの矛盾を「`config.py` は `Sysstat` を直接 import しない」側に倒し、
`RuntimeSettings.resolve()` に `installed_ram_bytes: int | None = None` という
明示引数を追加した。実際の搭載 RAM 値は将来の CLI 配線タスク（8.1）が
`Sysstat.sample().system_total_bytes` から取得して渡す想定であり、本タスクの
境界（`errors, types` のみ import 可）を破らない。`installed_ram_bytes` も
`max_ring_bytes`（明示設定）も無い場合は、根拠のない固定値を捏造して安全側の
チェックのふりをするより、チェックを行わない方が誠実だと判断し、その旨を
`config.py` の docstring に明記した。以下のテストは
(1) `installed_ram_bytes` を明示的に渡した場合にチェックが機能すること、
(2) `max_ring_bytes` を明示的に渡した場合はそちらが優先されること、
(3) どちらも無い場合はチェックをスキップすること、の3点を固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sensing_foundation.config import (
    CaptureConfig,
    LoggingConfig,
    RecordingConfig,
    RuntimeSettings,
)
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.types import SourceKind

# 640x480 / 30fps / 3.0秒のリングに必要なバイト数（テスト全体で使う基準値）。
# width * height * 2(uint16) * fps * seconds
_DEFAULT_RING_BYTES = 640 * 480 * 2 * 30 * 3.0


# ---------------------------------------------------------------------------
# 既定値・不変オブジェクトとしての最小コンポーネント
# ---------------------------------------------------------------------------


def test_capture_config_defaults_disable_color():
    """Color は既定で無効（改善順序1番目。tasks.md 1.6 / design.md §13.2）。

    `fps` の既定はタスク 9.4 の実機実測により 30 → 60 へ変更された
    （`measurements.md` タスク9.4）。解像度 640×480 は候補モード列が
    640×480 固定であったため**比較していない**——こちらは実測の裏付けを
    持たない初期評価候補のままである
    （`test_default_resolution_fps_documented_as_initial_candidates_not_requirements`
    がその旨の docstring 表記を固定する）。
    """
    capture = CaptureConfig()
    assert capture.color_enabled is False
    assert capture.width_px == 640
    assert capture.height_px == 480
    assert capture.fps == 60
    assert capture.queue_capacity == 1
    assert capture.drain_enabled is True
    assert capture.on_acquire_error == "continue"


def test_recording_config_defaults():
    recording = RecordingConfig()
    assert recording.enabled is False
    assert recording.mode == "ring"
    assert recording.ring_seconds == 3.0
    assert recording.compression == "none"
    assert recording.root == Path("var/sessions")
    assert recording.max_ring_bytes is None


def test_logging_config_defaults():
    logging_cfg = LoggingConfig()
    assert logging_cfg.enabled is True
    assert logging_cfg.path == Path("var/logs")
    assert logging_cfg.queue_capacity == 4096
    assert logging_cfg.flush_each_line is True


def test_default_resolution_fps_documented_as_initial_candidates_not_requirements():
    """tasks.md 1.6 / design.md Risks: 既定値は初期評価候補であり必須性能ではない。"""
    doc = (CaptureConfig.__doc__ or "") + (RuntimeSettings.__doc__ or "")
    assert "初期評価候補" in doc
    assert "必須性能ではない" in doc


# ---------------------------------------------------------------------------
# resolve(): 既定値のみ
# ---------------------------------------------------------------------------


def test_resolve_with_nothing_supplied_yields_defaults():
    settings = RuntimeSettings.resolve(file=None, env={}, overrides={})
    assert settings.source == SourceKind.LIVE
    assert settings.capture == CaptureConfig()
    assert settings.recording == RecordingConfig()
    assert settings.logging == LoggingConfig()
    assert settings.session_path is None


# ---------------------------------------------------------------------------
# resolve(): 不変性（Postcondition）
# ---------------------------------------------------------------------------


def test_resolved_settings_are_immutable():
    settings = RuntimeSettings.resolve(file=None, env={}, overrides={})
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        settings.source = SourceKind.RECORDED  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.capture.fps = 999  # type: ignore[misc]
    with pytest.raises(Exception):
        settings.recording.enabled = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# resolve(): 解決順序（CLI 引数 > 環境変数 > 設定ファイル > 既定値）
# ---------------------------------------------------------------------------


def test_file_beats_defaults(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(json.dumps({"fps": 15, "width_px": 320}), encoding="utf-8")

    settings = RuntimeSettings.resolve(file=config_file, env={}, overrides={})

    assert settings.capture.fps == 15
    assert settings.capture.width_px == 320
    # ファイルが指定していないキーは既定値のまま
    assert settings.capture.height_px == 480


def test_env_beats_file(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(json.dumps({"fps": 15, "width_px": 320}), encoding="utf-8")

    settings = RuntimeSettings.resolve(
        file=config_file, env={"STB_SF_FPS": "20"}, overrides={}
    )

    assert settings.capture.fps == 20  # env が file に勝つ
    assert settings.capture.width_px == 320  # env が指定しないキーは file の値を維持


def test_overrides_beat_env_and_file(tmp_path: Path):
    config_file = tmp_path / "settings.json"
    config_file.write_text(json.dumps({"fps": 15, "width_px": 320}), encoding="utf-8")

    settings = RuntimeSettings.resolve(
        file=config_file,
        env={"STB_SF_FPS": "20"},
        overrides={"fps": 25},
    )

    assert settings.capture.fps == 25  # overrides (CLI) が最優先
    assert settings.capture.width_px == 320  # overrides が指定しないキーは file の値を維持


def test_env_var_prefix_is_stb_sf_and_coerces_bools():
    settings = RuntimeSettings.resolve(
        file=None,
        env={"STB_SF_COLOR_ENABLED": "true", "STB_SF_DRAIN_ENABLED": "0"},
        overrides={},
    )
    assert settings.capture.color_enabled is True
    assert settings.capture.drain_enabled is False


def test_session_path_resolvable_from_env():
    settings = RuntimeSettings.resolve(
        file=None,
        env={
            "STB_SF_SOURCE": "recorded",
            "STB_SF_SESSION_PATH": "var/sessions/abc",
        },
        overrides={},
    )
    assert settings.source == SourceKind.RECORDED
    assert settings.session_path == Path("var/sessions/abc")


# ---------------------------------------------------------------------------
# resolve(): 起動時の拒否（fps<=0 / 解像度<=0 / queue_capacity<1 / ring_seconds<=0）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_fps", [0, -1, -30])
def test_rejects_nonpositive_fps(bad_fps: int):
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"fps": bad_fps})


@pytest.mark.parametrize("key,bad_value", [("width_px", 0), ("width_px", -1), ("height_px", 0)])
def test_rejects_nonpositive_resolution(key: str, bad_value: int):
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={key: bad_value})


def test_rejects_queue_capacity_below_one():
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"queue_capacity": 0})


def test_rejects_nonpositive_ring_seconds():
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"ring_seconds": 0.0})
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"ring_seconds": -1.0})


def test_valid_settings_do_not_raise():
    RuntimeSettings.resolve(
        file=None,
        env={},
        overrides={"fps": 60, "width_px": 320, "height_px": 240, "queue_capacity": 2},
    )


# ---------------------------------------------------------------------------
# resolve(): 再生（RECORDED）時のセッション未指定を拒否
# ---------------------------------------------------------------------------


def test_rejects_recorded_source_without_session_path():
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"source": "recorded"})


def test_accepts_recorded_source_with_session_path():
    settings = RuntimeSettings.resolve(
        file=None,
        env={},
        overrides={"source": "recorded", "session_path": "var/sessions/xyz"},
    )
    assert settings.source == SourceKind.RECORDED
    assert settings.session_path == Path("var/sessions/xyz")


def test_live_source_does_not_require_session_path():
    settings = RuntimeSettings.resolve(file=None, env={}, overrides={})
    assert settings.source == SourceKind.LIVE
    assert settings.session_path is None  # 拒否されない


# ---------------------------------------------------------------------------
# resolve(): リングバッファの必要 RAM が上限を超える設定を起動時に拒否
# ---------------------------------------------------------------------------


def test_ram_budget_rejects_when_required_exceeds_explicit_cap():
    # 既定 640x480/30fps/3.0秒 で約 52.7 MiB 必要。上限を 1 MB に絞ると必ず超える。
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(
            file=None, env={}, overrides={"max_ring_bytes": 1_000_000}
        )


def test_ram_budget_rejects_using_installed_ram_derived_default_cap():
    # 既定は搭載 RAM の 25%。100MiB の 25% = 25MiB は 52.7MiB を下回るため拒否される。
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={},
            installed_ram_bytes=100 * 1024 * 1024,
        )


def test_ram_budget_accepts_within_installed_ram_derived_default_cap():
    # 4GiB の 25% = 1GiB は 52.7MiB を十分上回るため受理される。
    settings = RuntimeSettings.resolve(
        file=None,
        env={},
        overrides={},
        installed_ram_bytes=4 * 1024 * 1024 * 1024,
    )
    assert settings.recording.max_ring_bytes is None  # 上限は導出のみ、設定値自体は不変


def test_explicit_max_ring_bytes_takes_precedence_over_installed_ram_derivation():
    # 明示 max_ring_bytes（極小）が、搭載 RAM から導かれる緩い上限より優先される。
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(
            file=None,
            env={},
            overrides={"max_ring_bytes": 1_000_000},
            installed_ram_bytes=8 * 1024 * 1024 * 1024,  # 25% = 2GiB。本来なら通る量
        )


def test_ram_budget_check_skipped_when_no_cap_and_no_installed_ram_supplied():
    """`max_ring_bytes` も `installed_ram_bytes` も無い場合はチェックを行わない。

    根拠の無い固定バイト数を安全上限として捏造しない、という判断（モジュール
    docstring 参照）。ここでは意図的に極端に大きい設定を通し、例外が出ない
    ことでスキップされていることを確認する。
    """
    settings = RuntimeSettings.resolve(
        file=None,
        env={},
        overrides={
            "width_px": 4000,
            "height_px": 4000,
            "fps": 60,
            "ring_seconds": 60.0,
        },
    )
    assert settings.capture.width_px == 4000


def test_ram_budget_not_checked_when_recording_mode_is_continuous():
    settings = RuntimeSettings.resolve(
        file=None,
        env={},
        overrides={
            "recording_mode": "continuous",
            "max_ring_bytes": 1,  # ring モードなら確実に拒否される極小値
            "width_px": 4000,
            "height_px": 4000,
            "fps": 60,
            "ring_seconds": 60.0,
        },
    )
    assert settings.recording.mode == "continuous"


# ---------------------------------------------------------------------------
# resolve(): 未知のキー・壊れた設定ファイル
# ---------------------------------------------------------------------------


def test_unknown_override_key_rejected():
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=None, env={}, overrides={"not_a_real_key": 1})


def test_unknown_file_key_rejected(tmp_path: Path):
    config_file = tmp_path / "bad.json"
    config_file.write_text(json.dumps({"not_a_real_key": 1}), encoding="utf-8")
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=config_file, env={}, overrides={})


def test_missing_file_raises_config_error(tmp_path: Path):
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=missing, env={}, overrides={})


def test_invalid_json_file_raises_config_error(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=bad, env={}, overrides={})


def test_non_object_json_file_raises_config_error(tmp_path: Path):
    bad = tmp_path / "list.json"
    bad.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(SensingConfigError):
        RuntimeSettings.resolve(file=bad, env={}, overrides={})
