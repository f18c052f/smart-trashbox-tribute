"""合成データによる end-to-end 検証（tasks.md タスク 8.2、要件 12.1, 12.2, 12.3）。

**要件 12.3（既知の偏りを注入した入力に対する帰属の確認）は本ファイルの
対象外である。** 帰属（`ErrorAttributor`）の統合テストはタスク 5.x で
`tests/m1_validation/test_m1_attribution.py` / `test_m1_attribution_causes.py`
として既に実装済みであり、本ファイルはそれを重複実装しない。本ファイルの
`attribute` 呼び出しは「CLI 経由で帰属まで到達すること」だけを確認する。

観測可能な完了状態（tasks.md 8.2）を固定する:

- 既知の放物軌道から合成した入力に対し、投擲実行 → 真値取り込み → 実測算出 →
  帰属 → 判断 → レポートを通す
- **実測7項目がすべて算出され、解析的に求まる値と一致する**
- 実測値が揃わない状態で時間予算の更新値が出ないことを確かめる
- ノイズ量を2段階変えたとき、ばらつきと収束サンプル数が単調に悪化することを
  確かめる
- 実行にハードウェアの接続を要求しないことを確かめる

**「解析的に求まる値と一致する」の範囲について。** 実測7項目それぞれの
解析解一致は、既にタスク 4.x で項目ごとに厳密に検証済みである
（`test_m1_metrics_flight.py` / `test_m1_metrics_accuracy.py` /
`test_m1_metrics_convergence.py`）。とくに落下時刻系の項目
（総飛行時間・検出開始まで・リリース時刻の外挿）は、線形内挿・外挿という
**放物線の解析解そのものとは原理的に一致しない**規則で定義されており
（`truth.py` の `_impact_time_truth` / `_release_time_truth` docstring）、
本ファイルで独自に再現しようとすると規則の重複実装になる。

本ファイルが確かめるのは**別の観測可能な完了状態**である: 各層を個別に
呼ぶのではなく **CLI 入口を通して全部つなげたときに**、同じ答えが出ること
（配線の正しさ）と、実機なしで最後まで到達できること。したがって解析解との
厳密一致は、経路に依存せず定義できる2項目——**落下地点誤差**
（`hit_error_norm_final_mm`。真値ファイルに書いた既知オフセットがそのまま
現れる）と**狙い誤差**（`aim_error_mm`。レイアウトの待機位置と真値の既知
リテラルだけで決まる幾何）——に絞る。この2つは真値の「跨ぐ区間の内挿」や
「外挿」を経由しないので、参照解を本ファイル内の literal だけで組める。

本ファイルの合成軌道（射出高さ 1500 mm、10 サンプル）は着地まで届かない
短い窓であり、これは既存の `test_m1_cli.py` と同じ選択である。そのため
`total_flight_ms` / `release_to_detect_ms` はこの入力では欠測になる
——「7項目のうち欠測がある」状態は要件 5.10 の暫定印の対象そのものであり、
`test_missing_measurements_stay_missing_and_are_not_zero_filled`（8.1）が
既に固定している。本ファイルは同じ入力形について再検証しない。
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pytest
from m1fixtures import verification_summary, write_calibration, write_layout

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackPoint,
    TrackState,
    TrackUpdate,
)
from m1_validation import runner as runner_module
from m1_validation.cli import main
from m1_validation.upstream import UpstreamGateway, resolve_runtime_settings
from sensing_foundation import INVALID_DEPTH_RAW

# ---------------------------------------------------------------------------
# 合成軌道（既知の放物運動。本ファイル局所の literal のみで組む）
# ---------------------------------------------------------------------------

WIDTH_PX = 8
HEIGHT_PX = 6
FPS = 30
DEPTH_SCALE_MM = 1.0
FX_PX = 385.0

LAYOUT_ID = "throw-a"
#: `m1fixtures.write_layout()` の既定値（本ファイルはこの値を変えない）。
STANDBY_POSITION_WORLD_MM = (0.0, 0.0)

#: 重力加速度（mm/ms²）。実装の定数を参照しない（Implementation Notes
#: タスク4.1: 参照解を実装の定数から組むと、その定数を変えたとき参照解が
#: 一緒に動いて差が消える）。
G_MM_MS2 = 0.00980665

T0_MS = 5000.0
DT_MS = 33.0
SAMPLE_COUNT = 10
X0_MM, Y0_MM, Z0_MM = -1500.0, 0.0, 1500.0
VX, VY, VZ = 3.0, 0.2, -1.0

#: キャリブレーションの平行移動（World = camera + translation）。
TRANSLATION_MM = (0.0, 0.0, -1000.0)

#: 真値ファイルへ書き込む、解析解からの既知オフセット（mm）。
TRUTH_OFFSET_MM = (37.0, -21.0)

#: `hit_error_norm_final_mm` の解析解との許容差。合成軌道はノイズが無く
#: 厳密に放物線上に乗るため、予測は解析解にほぼ厳密一致する
#: （`test_m1_metrics_accuracy.py` と同じ前提）。
HIT_ERROR_TOLERANCE_MM = 0.5


def _world_at(t_ms: float) -> tuple[float, float, float]:
    dt = t_ms - T0_MS
    return (
        X0_MM + VX * dt,
        Y0_MM + VY * dt,
        Z0_MM + VZ * dt - 0.5 * G_MM_MS2 * dt * dt,
    )


def _impact_point_world_mm() -> tuple[float, float, float]:
    """解析解による落下地点（z = 0 との交点）。テスト局所の literal だけで解く。"""
    a = 0.5 * G_MM_MS2
    t_impact = (-VZ - math.sqrt(VZ * VZ + 4.0 * a * Z0_MM)) / (-2.0 * a)
    return (X0_MM + VX * t_impact, Y0_MM + VY * t_impact, 0.0)


def _camera_points(
    *, sample_count: int = SAMPLE_COUNT, seed: int | None = None, sigma_mm: float = 0.0
) -> list[CameraPoint]:
    """合成カメラ座標系の点列。`seed` を与えると各軸へ独立な正規分布ノイズを
    加える（`seed=None` かつ `sigma_mm=0.0` のときは厳密に決定的で、複数回
    呼んでも常にビット同一の点列を返す——ノイズ2水準比較の基準側に使う）。
    """
    rng = random.Random(seed) if seed is not None else None
    points: list[CameraPoint] = []
    for index in range(sample_count):
        t_ms = T0_MS + DT_MS * index
        world = _world_at(t_ms)
        dx = dy = dz = 0.0
        if rng is not None and sigma_mm:
            dx, dy, dz = (rng.gauss(0.0, sigma_mm) for _ in range(3))
        points.append(
            CameraPoint(
                frame=CoordinateFrame.CAMERA,
                t_ms=t_ms,
                x_mm=world[0] - TRANSLATION_MM[0] + dx,
                y_mm=world[1] - TRANSLATION_MM[1] + dy,
                z_mm=world[2] - TRANSLATION_MM[2] + dz,
                valid_depth_px=40,
                depth_spread_mm=10.0,
                apparent_diameter_px=9.0,
                expected_diameter_px=8.5,
                intrinsics_source="stream_profile",
            )
        )
    return points


class FakePipeline:
    """フレーム1枚につき1点を追加する追跡パイプライン（`test_m1_cli.py` と同じ形）。"""

    def __init__(self, points: list[CameraPoint], *, track_id: int = 7) -> None:
        self._points = points
        self._track_id = track_id
        self._appended: list[TrackPoint] = []

    def process(self, frame: object) -> TrackUpdate:
        index = len(self._appended)
        appended: TrackPoint | None = None
        if index < len(self._points):
            appended = TrackPoint(
                point=self._points[index],
                frame_index=index,
                frame_seq=1000 + index,
                gap_before=0,
                rivals=0,
            )
            self._appended.append(appended)
        return TrackUpdate(
            track=self._track(),
            appended=appended,
            candidates=1 if appended is not None else 0,
            rejections=(),
            point_failures=(),
        )

    def _track(self) -> CameraTrack:
        return CameraTrack(
            handoff_version=HANDOFF_VERSION,
            frame=CoordinateFrame.CAMERA,
            track_id=self._track_id,
            started_t_ms=T0_MS,
            points=tuple(self._appended),
            state=TrackState.TRACKING if self._appended else TrackState.IDLE,
            end_reason=None,
            source=SourceKind.SIMULATED,
            detector_kind="depth_band",
        )


def _depth_supplier(frame_count: int = SAMPLE_COUNT):
    def supplier(index: int):
        if index >= frame_count:
            return None
        return np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW + 1, dtype=np.uint16)

    return supplier


# ---------------------------------------------------------------------------
# ストリーム識別のダブル（`test_m1_cli.py` と同じ理由。整合性検査を実機なしで通す）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeIntrinsics:
    width_px: int = WIDTH_PX
    height_px: int = HEIGHT_PX
    fx_px: float = FX_PX
    fy_px: float = 385.0
    ppx_px: float = 320.0
    ppy_px: float = 240.0
    model: str = "brown_conrady"
    coeffs: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class FakeStreamProfile:
    width_px: int = WIDTH_PX
    height_px: int = HEIGHT_PX
    fps: int = FPS
    depth_scale_mm: float = DEPTH_SCALE_MM
    color_enabled: bool = False
    intrinsics: object | None = field(default_factory=FakeIntrinsics)


class ProfileGateway:
    """実物の `UpstreamGateway` を包み、ストリーム識別だけを差し替える。"""

    def __init__(self, inner: UpstreamGateway, profile: object) -> None:
        self._inner = inner
        self._profile = profile

    def stream_profile(self, *, supplier: object = None, speed: str = "fast") -> object:
        return self._profile

    def load_records(self, path: Path) -> object:
        return self._inner.load_records(Path(path))

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# 環境
# ---------------------------------------------------------------------------


@dataclass
class Env:
    tmp_path: Path
    layout_path: Path
    calibration_path: Path
    records_path: Path
    truth_path: Path
    output_root: Path
    session_id: str
    gateway: ProfileGateway

    def common(self) -> list[str]:
        return [
            "--layout-file",
            str(self.layout_path),
            "--layout-id",
            LAYOUT_ID,
            "--output-root",
            str(self.output_root),
            "--bootstrap-iterations",
            "8",
            "--session-id",
            self.session_id,
        ]


def _make_env(tmp_path: Path, name: str) -> Env:
    """独立した実行環境を作る（呼び出しごとに `name` でパスを分ける）。"""
    root = tmp_path / name
    root.mkdir()
    session_id = f"e2e-{name}"
    spec = resolve_runtime_settings(
        file=None,
        env={},
        overrides={
            "source": "simulated",
            "width_px": WIDTH_PX,
            "height_px": HEIGHT_PX,
            "fps": FPS,
            "logging_path": str(root / "logs"),
            "recording_root": str(root / "sessions"),
        },
    )
    inner = UpstreamGateway.open(session_id=session_id, source_spec=spec)
    gateway = ProfileGateway(inner, FakeStreamProfile())
    return Env(
        tmp_path=root,
        layout_path=write_layout(root),
        calibration_path=write_calibration(
            root,
            width_px=WIDTH_PX,
            height_px=HEIGHT_PX,
            fx_px=FX_PX,
            verification=verification_summary(),
        ),
        records_path=root / "throws.ndjson",
        truth_path=root / "truth.json",
        output_root=root / "out",
        session_id=session_id,
        gateway=gateway,
    )


def _run(env: Env, argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, object], str]:
    code = main(list(argv), gateway=env.gateway, supplier=_depth_supplier())
    captured = capsys.readouterr()
    payload: dict[str, object] = {}
    if captured.out.strip():
        payload = json.loads(captured.out)
    return code, payload, captured.err


def _throw(
    env: Env,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    record_id: str,
    points: list[CameraPoint],
) -> tuple[int, dict[str, object], str]:
    monkeypatch.setattr(runner_module, "open_tracking", lambda *a, **k: FakePipeline(points))
    return _run(
        env,
        [
            "run-throw",
            *env.common(),
            "--calibration",
            str(env.calibration_path),
            "--record-id",
            record_id,
            "--records",
            str(env.records_path),
        ],
        capsys,
    )


def _write_truth(env: Env, record_ids: Sequence[str]) -> None:
    analytic = _impact_point_world_mm()
    truth_point = (
        analytic[0] + TRUTH_OFFSET_MM[0],
        analytic[1] + TRUTH_OFFSET_MM[1],
        0.0,
    )
    entries = {
        record_id: {
            "impact_point_world_mm": list(truth_point),
            "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
            "impact_point_uncertainty_mm": 15.0,
        }
        for record_id in record_ids
    }
    env.truth_path.write_text(
        json.dumps(
            {
                "truth_format_version": "1.0",
                "layout_id": LAYOUT_ID,
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return truth_point


def _evaluation_argv(env: Env, command: str, *, log: bool = True, extra: Sequence[str] = ()) -> list[str]:
    argv = [
        command,
        *env.common(),
        "--records",
        str(env.records_path),
        "--truth",
        str(env.truth_path),
        *extra,
    ]
    if log:
        argv += ["--log", str(env.output_root.parent / "logs" / f"{env.session_id}.ndjson")]
    return argv


# ---------------------------------------------------------------------------
# 1. 合成入力による full pipeline と、解析解との一致
# ---------------------------------------------------------------------------


class TestFullPipelineMatchesAnalytic:
    def test_pipeline_completes_and_hit_and_aim_error_match_analytic(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """要件 12.1, 12.2: 実機なしで全経路が通り、既知量が解析解と一致する。"""
        env = _make_env(tmp_path, "pipeline")
        clean_points = _camera_points()
        record_ids = ("throw-0001", "throw-0002")
        for record_id in record_ids:
            code, _, err = _throw(
                env, capsys, monkeypatch, record_id=record_id, points=clean_points
            )
            assert code == 0, err
        truth_point = _write_truth(env, record_ids)

        code, _, err = _run(
            env,
            [
                *_evaluation_argv(env, "ingest-truth"),
                "--out",
                str(env.tmp_path / "with-truth.ndjson"),
            ],
            capsys,
        )
        assert code == 0, err

        code, measured, err = _run(env, _evaluation_argv(env, "measure"), capsys)
        assert code == 0, err
        group = measured["groups"][0]
        items = group["items"]
        assert set(items) == {
            "total_flight_ms",
            "release_to_detect_ms",
            "detect_to_first_prediction_ms",
            "hit_error_norm_first_mm",
            "hit_error_norm_final_mm",
            "time_error_first_ms",
            "time_error_final_ms",
            "aim_error_mm",
            "valid_samples",
            "converged_at",
        }, "実測7項目に対応するキーが1つでも欠けていないこと"

        expected_hit_error_mm = math.hypot(*TRUTH_OFFSET_MM)
        assert items["hit_error_norm_final_mm"]["count"] == len(record_ids)
        assert items["hit_error_norm_final_mm"]["median"] == pytest.approx(
            expected_hit_error_mm, abs=HIT_ERROR_TOLERANCE_MM
        )

        expected_aim_error_mm = math.hypot(
            truth_point[0] - STANDBY_POSITION_WORLD_MM[0],
            truth_point[1] - STANDBY_POSITION_WORLD_MM[1],
        )
        assert items["aim_error_mm"]["median"] == pytest.approx(
            expected_aim_error_mm, abs=HIT_ERROR_TOLERANCE_MM
        )

        # 帰属・判断・レポートまで続けて通ることを確かめる（要件 12.1）。
        for command in ("attribute", "judge-oq27", "material-oq05"):
            code, _, err = _run(env, _evaluation_argv(env, command), capsys)
            assert code == 0, err

        code, report, err = _run(env, _evaluation_argv(env, "report"), capsys)
        assert code == 0, err
        assert report["written"]
        for path in report["written"]:
            assert Path(path).exists()

        # 要件 12.1: 実機の接続を要求しない。合成入力の gateway/supplier だけで
        # ここまで到達しており、`source_kind` が simulated のままであること。
        assert env.gateway.source_kind == "simulated"

    def test_budget_does_not_update_when_measurement_items_are_incomplete(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本ファイルの合成軌道は着地まで届かないため `total_flight_ms` /
        `release_to_detect_ms` が欠測する。**その状態で更新値を出さない**こと
        （要件 11.1）。
        """
        env = _make_env(tmp_path, "budget")
        clean_points = _camera_points()
        record_ids = ("throw-0001",)
        for record_id in record_ids:
            code, _, err = _throw(
                env, capsys, monkeypatch, record_id=record_id, points=clean_points
            )
            assert code == 0, err
        _write_truth(env, record_ids)
        code, _, err = _run(
            env,
            [
                *_evaluation_argv(env, "ingest-truth"),
                "--out",
                str(env.tmp_path / "with-truth.ndjson"),
            ],
            capsys,
        )
        assert code == 0, err

        code, budget, err = _run(env, _evaluation_argv(env, "budget"), capsys)
        assert code == 0, err
        result = budget["groups"][0]["budget"]
        assert result["ready"] is False
        assert "item1:total_flight_ms" in result["missing_items"]


# ---------------------------------------------------------------------------
# 2. ノイズ2水準によるばらつき・収束サンプル数の単調な悪化
# ---------------------------------------------------------------------------

#: ノイズ2水準の観測点数への上乗せ（mm、各軸独立の正規分布 stddev）。
#: 基準側は 0（決定的。全投擲が厳密に同一点列になる）。
NOISY_SIGMA_MM = 12.0
NOISE_THROW_COUNT = 6
#: 収束の帯域。基準側は厳密解に一致するため即座に収束し、ノイズ側でも
#: 大半が `SAMPLE_COUNT` 以内に収束する値を、実測しながら選んだ。
CONVERGENCE_BAND_MM = 30.0


class TestNoiseWorsensScatterAndConvergence:
    def test_more_noise_does_not_improve_scatter_or_convergence(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """要件 12.2: ノイズ量を2段階変えたとき、ばらつきと収束サンプル数が
        単調に悪化する（基準側より良くはならない）。

        基準側は `sigma_mm=0.0`（`seed=None`）で全投擲が厳密に同一の点列に
        なる——したがって `hit_error_norm_final_mm` の IQR は**厳密に 0**
        になる。ノイズ側は投擲ごとに独立な乱数（`seed=throw index`）で
        点列を揺らす。IQR が 0 から正へ動くことは、統計的な閾値調整では
        なく構造的な帰結（同一入力の分布は退化する）なので、この比較は
        フレーキーにならない。
        """
        low = _make_env(tmp_path, "noise-low")
        high = _make_env(tmp_path, "noise-high")

        low_record_ids: list[str] = []
        for i in range(NOISE_THROW_COUNT):
            record_id = f"throw-{i:04d}"
            low_record_ids.append(record_id)
            code, _, err = _throw(
                low,
                capsys,
                monkeypatch,
                record_id=record_id,
                points=_camera_points(seed=None, sigma_mm=0.0),
            )
            assert code == 0, err
        _write_truth(low, low_record_ids)
        code, low_measured, err = _run(
            low,
            _evaluation_argv(
                low, "measure", extra=["--convergence-band-mm", str(CONVERGENCE_BAND_MM)]
            ),
            capsys,
        )
        assert code == 0, err

        high_record_ids: list[str] = []
        for i in range(NOISE_THROW_COUNT):
            record_id = f"throw-{i:04d}"
            high_record_ids.append(record_id)
            code, _, err = _throw(
                high,
                capsys,
                monkeypatch,
                record_id=record_id,
                points=_camera_points(seed=1000 + i, sigma_mm=NOISY_SIGMA_MM),
            )
            assert code == 0, err
        _write_truth(high, high_record_ids)
        code, high_measured, err = _run(
            high,
            _evaluation_argv(
                high, "measure", extra=["--convergence-band-mm", str(CONVERGENCE_BAND_MM)]
            ),
            capsys,
        )
        assert code == 0, err

        low_items = low_measured["groups"][0]["items"]
        high_items = high_measured["groups"][0]["items"]

        # ばらつき: 基準側は退化して厳密に 0、ノイズ側は正。
        assert low_items["hit_error_norm_final_mm"]["iqr"] == pytest.approx(0.0, abs=1e-9)
        assert high_items["hit_error_norm_final_mm"]["iqr"] > 0.0

        # 収束サンプル数: ノイズ側は基準側より早く収束することはない。
        assert low_items["converged_at"]["count"] == NOISE_THROW_COUNT
        assert low_items["converged_at"]["missing"] == 0
        assert high_items["converged_at"]["median"] is not None, (
            "帯域が狭すぎてノイズ側が1件も収束しなかった。"
            "CONVERGENCE_BAND_MM を調整すること"
        )
        assert (
            high_items["converged_at"]["median"] >= low_items["converged_at"]["median"]
        )
