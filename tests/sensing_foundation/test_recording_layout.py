"""`sensing_foundation.recording.layout` に対するテスト（タスク 4.1）。

観測可能な完了状態（tasks.md 4.1）を固定する:
- メタ情報（manifest）を書いて読み戻すと同一の辞書が得られる
- セッション識別子が連続生成で衝突しない

あわせて design.md「RecordingLayout」が定める以下の点も固定する:
- ディレクトリ規約・4ファイル名・`RECORDING_FORMAT_VERSION` が1箇所に固定されている
- `new_session_id()` の書式が `"YYYYMMDDTHHMMSSZ-<8 hex>"` であること
- `session_dir()` が `root / session_id` を返す純粋な計算であること
- 既存のセッションディレクトリへの上書きは拒否される（`errors.py` の例外で識別可能）
- 出力先の既定が版管理対象外（`var/` 配下）であること
- 観測フレーム記録が Throw Record とは別の記録である旨が docstring に明記されていること
- `recording/layout.py` が `source` を import していないこと（依存方向の静的検証）
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from sensing_foundation import recording as recording_pkg
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.recording import layout

LAYOUT_SOURCE_PATH = Path(layout.__file__)


class TestConstants:
    """design.md「RecordingLayout」が定める定数の固定。"""

    def test_format_version(self) -> None:
        assert layout.RECORDING_FORMAT_VERSION == "1.0"

    def test_file_names(self) -> None:
        assert layout.MANIFEST_NAME == "manifest.json"
        assert layout.INDEX_NAME == "frames.ndjson"
        assert layout.BLOB_NAME == "depth.bin"
        assert layout.SUMMARY_NAME == "summary.json"

    def test_default_sessions_root_is_outside_version_control(self) -> None:
        # 要件 12.6: 記録データの既定出力先は版管理対象外（var/ 配下）。
        # .gitignore は `var/` を丸ごと除外している（タスク 1.1）。
        root = layout.DEFAULT_SESSIONS_ROOT
        parts = Path(root).parts
        assert "var" in parts


class TestNewSessionId:
    """`new_session_id()` の書式と衝突耐性。"""

    def test_format_matches_pattern(self) -> None:
        session_id = layout.new_session_id(now_wall_ms=1_700_000_000_000.0)
        assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", session_id)

    def test_timestamp_portion_reflects_known_epoch_ms(self) -> None:
        # 1700000000000 ms = 2023-11-14T22:13:20Z (UTC)
        session_id = layout.new_session_id(now_wall_ms=1_700_000_000_000.0)
        timestamp_part = session_id.split("-", maxsplit=1)[0]
        assert timestamp_part == "20231114T221320Z"

    def test_many_consecutive_calls_do_not_collide(self) -> None:
        # 同一の now_wall_ms を渡し続けても、ランダム接尾辞が衝突を防ぐことを示す。
        ids = [layout.new_session_id(now_wall_ms=1_700_000_000_000.0) for _ in range(2000)]
        assert len(ids) == len(set(ids))

    def test_suffix_varies_across_calls(self) -> None:
        first = layout.new_session_id(now_wall_ms=1_700_000_000_000.0)
        second = layout.new_session_id(now_wall_ms=1_700_000_000_000.0)
        assert first != second
        assert first.split("-", maxsplit=1)[0] == second.split("-", maxsplit=1)[0]


class TestSessionDir:
    """`session_dir()` は副作用のない純粋なパス計算である。"""

    def test_returns_root_joined_with_session_id(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        result = layout.session_dir(tmp_path, session_id)
        assert result == tmp_path / session_id

    def test_does_not_create_directory(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        result = layout.session_dir(tmp_path, session_id)
        assert not result.exists()


class TestCreateSessionDir:
    """既存ディレクトリへの上書き拒否（Batch/Job Contract の Idempotency & recovery）。"""

    def test_creates_directory(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        created = layout.create_session_dir(tmp_path, session_id)
        assert created == tmp_path / session_id
        assert created.is_dir()

    def test_creates_missing_root(self, tmp_path: Path) -> None:
        root = tmp_path / "nested" / "sessions"
        session_id = "20231114T221320Z-deadbeef"
        created = layout.create_session_dir(root, session_id)
        assert created.is_dir()

    def test_rejects_existing_session_directory(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        layout.create_session_dir(tmp_path, session_id)

        with pytest.raises(SensingConfigError):
            layout.create_session_dir(tmp_path, session_id)


class TestManifestRoundTrip:
    """メタ情報を書いて読み戻すと同一の辞書が得られる（tasks.md 4.1 の観測可能な完了状態）。"""

    def _populated_manifest(self) -> dict[str, object]:
        return {
            "format_version": layout.RECORDING_FORMAT_VERSION,
            "session_id": "20231114T221320Z-deadbeef",
            "source": "live",
            "started_wall_ms": 1_700_000_000_000.0,
            "profile": {
                "width_px": 640,
                "height_px": 480,
                "fps": 30,
                "depth_scale_mm": 1.0,
                "color_enabled": False,
                "pixel_format": "z16",
            },
            "intrinsics": {
                "fx_px": 385.5,
                "fy_px": 385.5,
                "ppx_px": 320.0,
                "ppy_px": 240.0,
                "model": "brown_conrady",
                "coeffs": [0.0, 0.0, 0.0, 0.0, 0.0],
            },
            "device": {
                "serial": "123456789",
                "firmware": "5.15.1",
                "usb_type": "3.2",
                "product_line": "D400",
            },
            "runtime": {
                "os": "Linux",
                "kernel": "6.6.0",
                "python_version": "3.11.9",
                "sdk_version": "2.55.1",
                "hostname": "raspberrypi",
                "global_time_enabled": True,
            },
            "capture": {
                "queue_capacity": 4,
                "drain_enabled": True,
                "acquire_timeout_ms": 1000.0,
            },
            "blob": {
                "file": layout.BLOB_NAME,
                "dtype": "uint16",
                "little_endian": True,
                "frame_bytes": 614400,
                "compression": None,
            },
        }

    def _null_manifest(self) -> dict[str, object]:
        manifest = self._populated_manifest()
        manifest["source"] = "simulated"
        manifest["intrinsics"] = None
        manifest["device"] = None
        return manifest

    def test_populated_manifest_round_trips_identically(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        session_dir = layout.create_session_dir(tmp_path, session_id)
        manifest = self._populated_manifest()

        layout.write_manifest(session_dir, manifest)
        read_back = layout.read_manifest(session_dir)

        assert read_back == manifest

    def test_manifest_with_null_intrinsics_and_device_round_trips_identically(
        self, tmp_path: Path
    ) -> None:
        session_id = "20231114T221320Z-cafebabe"
        session_dir = layout.create_session_dir(tmp_path, session_id)
        manifest = self._null_manifest()

        layout.write_manifest(session_dir, manifest)
        read_back = layout.read_manifest(session_dir)

        assert read_back == manifest
        assert read_back["intrinsics"] is None
        assert read_back["device"] is None

    def test_manifest_is_written_under_manifest_name(self, tmp_path: Path) -> None:
        session_id = "20231114T221320Z-deadbeef"
        session_dir = layout.create_session_dir(tmp_path, session_id)
        layout.write_manifest(session_dir, self._populated_manifest())

        manifest_path = session_dir / layout.MANIFEST_NAME
        assert manifest_path.is_file()
        with manifest_path.open(encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["session_id"] == session_id


class TestDocstringDocumentsSeparationFromThrowRecord:
    """要件 5.9: 観測フレーム記録は Throw Record とは別の記録である旨の明記。"""

    def test_module_docstring_mentions_throw_record_separation(self) -> None:
        assert layout.__doc__ is not None
        assert "Throw Record" in layout.__doc__


class TestImportBoundary:
    """design.md Dependency Direction: `recording/*` は `source` を import しない。"""

    def test_layout_module_does_not_import_source(self) -> None:
        source = LAYOUT_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(LAYOUT_SOURCE_PATH))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert node.module not in (
                    "sensing_foundation.source",
                    "source",
                ), f"recording/layout.py は source を import してはならない: {node.module}"
                assert not node.module.startswith(
                    "sensing_foundation.sources"
                ), f"recording/layout.py はアダプタ層を import してはならない: {node.module}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in (
                        "sensing_foundation.source",
                        "source",
                    ), f"recording/layout.py は source を import してはならない: {alias.name}"

    def test_recording_package_marker_exists(self) -> None:
        # design.md File Structure Plan: recording/ はサブパッケージであり __init__.py を持つ。
        assert recording_pkg.__file__ is not None
        assert Path(recording_pkg.__file__).name == "__init__.py"
