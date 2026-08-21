"""`sensing_foundation.types` の共通フレーム表現に対するテスト（タスク 1.5）。

観測可能な完了状態（tasks.md 1.5）を固定する:
- Depth 配列（`CaptureFrame.depth`）への書き込みが拒否される（読み取り専用）
- `sensing_foundation.types.SourceKind` が `prediction_core.SourceKind` と
  **同一オブジェクト**である（`is` による同一性。値の等価性ではない）

あわせて design.md「CoreTypes」の State Management / Implementation Notes /
Preconditions が定める以下の点も固定する:
- 全データ型が `frozen=True, slots=True` の不変値であること
- フィールド名に単位（mm / ms / px）が含まれること
- 時刻・レイテンシの欠測が `None` で表現でき、センチネル値を使わないこと
- 構築時に shape / dtype の検証を行わない（検証はアダプタ・SessionReader の境界の責務）
- 本モジュールが `prediction_core` から import するのは `SourceKind` のみであり、
  Throw Record スキーマ関連シンボル（`ThrowRecord` / `SCHEMA_VERSION` など）には
  一切触れないこと（静的解析による境界確認）
- 座標変換・床平面推定に相当するフィールド・処理を持たない旨がモジュール
  docstring に明記されていること
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import numpy as np
import pytest

import prediction_core
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    CaptureStats,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TYPES_SOURCE_PATH = REPO_ROOT / "src" / "sensing_foundation" / "types.py"


def _make_intrinsics() -> CameraIntrinsics:
    return CameraIntrinsics(
        width_px=640,
        height_px=480,
        fx_px=615.0,
        fy_px=615.0,
        ppx_px=320.0,
        ppy_px=240.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


_UNSET = object()


def _make_profile(*, intrinsics: CameraIntrinsics | None | object = _UNSET) -> StreamProfile:
    resolved = _make_intrinsics() if intrinsics is _UNSET else intrinsics
    return StreamProfile(
        width_px=640,
        height_px=480,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=resolved,  # type: ignore[arg-type]
    )


def _make_depth(width: int = 640, height: int = 480) -> np.ndarray:
    return np.zeros((height, width), dtype=np.uint16)


def _make_frame(
    *,
    depth: np.ndarray | None = None,
    profile: StreamProfile | None = None,
    device_timestamp_ms: float | None = 123.0,
    timestamp_domain: TimestampDomain = TimestampDomain.HARDWARE_CLOCK,
    capture_latency_ms: float | None = None,
) -> CaptureFrame:
    return CaptureFrame(
        index=0,
        seq=0,
        t_capture_ms=0.0,
        device_timestamp_ms=device_timestamp_ms,
        timestamp_domain=timestamp_domain,
        capture_latency_ms=capture_latency_ms,
        depth=depth if depth is not None else _make_depth(),
        profile=profile if profile is not None else _make_profile(),
        source=SourceKind.LIVE,
        dropped_before=0,
        gap_before=0,
    )


class TestSourceKindReexport:
    """`SourceKind` が `prediction_core` のものと同一オブジェクトであることを固定する（要件 4.3）。"""

    def test_source_kind_is_identical_object_to_prediction_core(self) -> None:
        assert SourceKind is prediction_core.SourceKind

    def test_source_kind_is_identical_object_to_prediction_core_types_module(self) -> None:
        import prediction_core.types as pc_types

        # prediction_core.__init__ が再エクスポートするオブジェクトと、
        # prediction_core.types で定義されたオブジェクトが同一であることも
        # あわせて固定する（別オブジェクトへのすり替わりを検出するため）。
        assert SourceKind is pc_types.SourceKind

    def test_source_kind_members_are_usable(self) -> None:
        # 新規に同義の列挙型を定義していないことの間接証拠として、
        # prediction_core が定義するメンバがそのまま使えることを確認する。
        assert SourceKind.LIVE.value == "live"
        assert SourceKind.RECORDED.value == "recorded"
        assert SourceKind.SIMULATED.value == "simulated"


class TestDepthArrayIsReadOnly:
    """Depth 配列への書き込みが拒否されることを固定する（Replay の再現性を守る要）。"""

    def test_writing_to_depth_array_raises(self) -> None:
        frame = _make_frame()

        with pytest.raises(ValueError):
            frame.depth[0, 0] = 42

    def test_depth_array_is_not_writeable(self) -> None:
        frame = _make_frame()

        assert frame.depth.flags.writeable is False

    def test_depth_array_stays_read_only_even_if_input_array_was_writeable(self) -> None:
        # 呼び出し側が書き込み可能な配列を渡しても、CaptureFrame の構築を
        # 経由すれば読み取り専用になることを固定する。
        mutable_depth = _make_depth()
        assert mutable_depth.flags.writeable is True

        frame = _make_frame(depth=mutable_depth)

        assert frame.depth.flags.writeable is False
        with pytest.raises(ValueError):
            frame.depth[1, 1] = 7

    def test_depth_dtype_is_uint16(self) -> None:
        frame = _make_frame()
        assert frame.depth.dtype == np.uint16


class TestImmutability:
    """全データ型が frozen かつ slots であることを固定する。"""

    def test_camera_intrinsics_is_frozen(self) -> None:
        intrinsics = _make_intrinsics()
        with pytest.raises(dataclasses.FrozenInstanceError):
            intrinsics.fx_px = 999.0  # type: ignore[misc]

    def test_camera_intrinsics_has_no_dict(self) -> None:
        intrinsics = _make_intrinsics()
        assert not hasattr(intrinsics, "__dict__")

    def test_stream_profile_is_frozen(self) -> None:
        profile = _make_profile()
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.fps = 60  # type: ignore[misc]

    def test_stream_profile_has_no_dict(self) -> None:
        profile = _make_profile()
        assert not hasattr(profile, "__dict__")

    def test_capture_frame_is_frozen(self) -> None:
        frame = _make_frame()
        with pytest.raises(dataclasses.FrozenInstanceError):
            frame.index = 1  # type: ignore[misc]

    def test_capture_frame_has_no_dict(self) -> None:
        frame = _make_frame()
        assert not hasattr(frame, "__dict__")

    def test_capture_stats_is_frozen(self) -> None:
        stats = CaptureStats(
            frames_yielded=10,
            frames_dropped=1,
            frames_missing=0,
            duration_ms=333.3,
            measured_fps=30.0,
            acquire_errors=0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            stats.frames_yielded = 11  # type: ignore[misc]

    def test_capture_stats_has_no_dict(self) -> None:
        stats = CaptureStats(
            frames_yielded=10,
            frames_dropped=1,
            frames_missing=0,
            duration_ms=333.3,
            measured_fps=30.0,
            acquire_errors=0,
        )
        assert not hasattr(stats, "__dict__")


class TestFieldNamesIncludeUnits:
    """距離 mm・時刻 ms・画素量 px がフィールド名に含まれることを固定する（要件 3.3）。"""

    def test_camera_intrinsics_pixel_fields_have_px_suffix(self) -> None:
        names = {f.name for f in dataclasses.fields(CameraIntrinsics)}
        for expected in ("width_px", "height_px", "fx_px", "fy_px", "ppx_px", "ppy_px"):
            assert expected in names

    def test_stream_profile_units_in_field_names(self) -> None:
        names = {f.name for f in dataclasses.fields(StreamProfile)}
        assert "width_px" in names
        assert "height_px" in names
        assert "depth_scale_mm" in names

    def test_capture_frame_units_in_field_names(self) -> None:
        names = {f.name for f in dataclasses.fields(CaptureFrame)}
        assert "t_capture_ms" in names
        assert "device_timestamp_ms" in names
        assert "capture_latency_ms" in names

    def test_capture_stats_units_in_field_names(self) -> None:
        names = {f.name for f in dataclasses.fields(CaptureStats)}
        assert "duration_ms" in names


class TestMissingIsRepresentedByNone:
    """欠測がセンチネル値ではなく `None` で表現できることを固定する。"""

    def test_device_timestamp_ms_accepts_none(self) -> None:
        frame = _make_frame(device_timestamp_ms=None)
        assert frame.device_timestamp_ms is None

    def test_capture_latency_ms_accepts_none(self) -> None:
        frame = _make_frame(capture_latency_ms=None)
        assert frame.capture_latency_ms is None

    def test_capture_latency_ms_is_only_meaningful_for_global_time_domain(self) -> None:
        # domain が GLOBAL_TIME のときのみ意味を持つ、という設計上の取り決めは
        # 構築時には強制されない（design.md「検証しない」方針）。値を保持
        # できることのみを固定する。
        frame = _make_frame(
            timestamp_domain=TimestampDomain.GLOBAL_TIME,
            capture_latency_ms=12.5,
        )
        assert frame.capture_latency_ms == pytest.approx(12.5)

    def test_stream_profile_intrinsics_accepts_none(self) -> None:
        profile = _make_profile(intrinsics=None)
        assert profile.intrinsics is None
        # intrinsics が無い入力元でもフレームを構築できる。
        frame = _make_frame(profile=profile)
        assert frame.profile.intrinsics is None


class TestTimestampDomainMembers:
    """`TimestampDomain` が design.md の4値を持つことを固定する。"""

    def test_members(self) -> None:
        assert TimestampDomain.HARDWARE_CLOCK.value == "hardware_clock"
        assert TimestampDomain.SYSTEM_TIME.value == "system_time"
        assert TimestampDomain.GLOBAL_TIME.value == "global_time"
        assert TimestampDomain.UNKNOWN.value == "unknown"


class TestNoConstructionTimeValidation:
    """構築時に shape / dtype を検証しないことを固定する（design.md CoreTypes「Validation」）。

    検証はアダプタと `SessionReader` の境界の責務であり、`CaptureFrame` 自身の
    責務ではない（`prediction_core` の値オブジェクトの方針を踏襲）。
    """

    def test_mismatched_shape_does_not_raise_at_construction(self) -> None:
        # profile は 640x480 を宣言しているが、depth は別の shape を渡す。
        # それでも構築時には例外が送出されないことを固定する。
        mismatched_depth = _make_depth(width=64, height=48)
        frame = _make_frame(depth=mismatched_depth)
        assert frame.depth.shape == (48, 64)

    def test_non_uint16_dtype_does_not_raise_at_construction(self) -> None:
        depth_wrong_dtype = np.zeros((480, 640), dtype=np.int32)
        frame = _make_frame(depth=depth_wrong_dtype)
        assert frame.depth.dtype == np.int32


class TestCaptureFrameAndStatsFieldSet:
    """design.md State Management が定めるフィールド集合そのものを固定する。"""

    def test_capture_frame_field_names(self) -> None:
        names = [f.name for f in dataclasses.fields(CaptureFrame)]
        assert names == [
            "index",
            "seq",
            "t_capture_ms",
            "device_timestamp_ms",
            "timestamp_domain",
            "capture_latency_ms",
            "depth",
            "profile",
            "source",
            "dropped_before",
            "gap_before",
        ]

    def test_capture_stats_field_names(self) -> None:
        names = [f.name for f in dataclasses.fields(CaptureStats)]
        assert names == [
            "frames_yielded",
            "frames_dropped",
            "frames_missing",
            "duration_ms",
            "measured_fps",
            "acquire_errors",
        ]

    def test_stream_profile_field_names(self) -> None:
        names = [f.name for f in dataclasses.fields(StreamProfile)]
        assert names == [
            "width_px",
            "height_px",
            "fps",
            "depth_scale_mm",
            "color_enabled",
            "intrinsics",
        ]

    def test_camera_intrinsics_field_names(self) -> None:
        names = [f.name for f in dataclasses.fields(CameraIntrinsics)]
        assert names == [
            "width_px",
            "height_px",
            "fx_px",
            "fy_px",
            "ppx_px",
            "ppy_px",
            "model",
            "coeffs",
        ]

    def test_capture_frame_source_field_holds_source_kind(self) -> None:
        frame = _make_frame()
        assert frame.source is SourceKind.LIVE
        assert isinstance(frame.source, SourceKind)


class TestOutOfScopeDocumented:
    """座標変換・床平面推定を責務に含めない旨が docstring に明記されることを固定する（要件 3.7）。"""

    def test_module_docstring_mentions_out_of_scope(self) -> None:
        import sensing_foundation.types as types_module

        doc = types_module.__doc__ or ""
        assert "床平面" in doc
        # World frame への座標変換が対象外であることの明記。
        assert "World frame" in doc or "world" in doc.lower()

    def test_module_docstring_mentions_geometry_ownership(self) -> None:
        import sensing_foundation.types as types_module

        doc = types_module.__doc__ or ""
        assert "geometry" in doc


class TestPredictionCoreImportBoundary:
    """`prediction_core` からの import が `SourceKind` の再エクスポートに限られることを固定する（要件 4.3）。

    静的解析（`ast`）でモジュールの `ImportFrom` 文を走査し、`prediction_core`
    起点の import では `SourceKind` 以外のシンボルに触れていないことを検証する。
    スキーマ関連シンボル（`ThrowRecord` / `SCHEMA_VERSION` / `replay` /
    `predictions_equivalent` など）は Throw Record 保存モジュール専用であり、
    本モジュールが参照してはならない。
    """

    def test_types_module_only_imports_source_kind_from_prediction_core(self) -> None:
        source = TYPES_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TYPES_SOURCE_PATH))

        imported_names_from_prediction_core: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module == "prediction_core" or node.module.startswith("prediction_core."):
                    for alias in node.names:
                        imported_names_from_prediction_core.append(alias.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "prediction_core" and not alias.name.startswith(
                        "prediction_core."
                    ), "types はスキーマ全体を import してはならない（SourceKind の再エクスポートに限る）"

        assert imported_names_from_prediction_core == ["SourceKind"]

    def test_types_module_imports_from_public_entrypoint_not_submodule(self) -> None:
        # design.md Dependency Direction: 「公開入口のみ」。
        # prediction_core.types のような内部サブモジュールから直接引かない。
        source = TYPES_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(TYPES_SOURCE_PATH))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                if node.module.startswith("prediction_core."):
                    pytest.fail(
                        f"types は prediction_core の公開入口のみを import すべき: "
                        f"見つかった内部サブモジュール import = {node.module}"
                    )
