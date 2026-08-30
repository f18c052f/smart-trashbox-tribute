"""段階別レイテンシと資源使用の集計の検証（タスク 4.5、要件 5.3, 7.1, 7.2, 7.3, 7.9）。

観測可能な完了状態（tasks.md 4.5）を固定する:

- **未知の段階を含む合成ログを集計できる**
- **end-to-end の定義文が出力に含まれる**

あわせて design.md「LatencyAggregator」と research.md Decision 7 が定める点も
固定する:

- end-to-end は「ある観測の取得時刻 → その観測を含めた予測が得られるまで」であり、
  **段階の合計とは一致しない**旨が定義文に入る
- 実測項目3 は**単発予測ではなく初回予測**を基準としている旨が結果に出る
- 上流が記録した段階を**集計側の改修なしに**読み取り、未知の段階名も捨てない
- 資源値が取得できない環境では**欠測**（`None`。0 で埋めない）

**説明文は「一文」として固定する。** tasks.md「Implementation Notes」タスク4.4
が記録した空振り——単語を個別に `in` で見ていたために、別の文に同じ単語が
現れて規則の一文を削っても全件通った——を踏まないため、本ファイルは
`EXPECTED_*` に**識別可能な一文をリテラルで**持ち、その一文の有無を検査する。
語の断片では検査しない。

**「別のもの」は相互排他で固定する。** 同じくタスク4.4 の教訓であり、`!=` だけ
では取り違え（2つの文字列の入れ替え・2つの fps の入れ替え）が素通りする。
したがって
- 定義文（要件 7.2）と初回予測の明示文（要件 5.3）は**それぞれ自分の一文を
  含むこと**を両方向から固定する
- 取得 fps（25.0）と実処理 fps（10.0）、取りこぼし（3 件）と欠落（1 件）、
  CPU 平均（30.0）と RSS 最大（3000）は**すべて異なる値**にしてある。
  入れ替えた実装は必ずどちらかで落ちる
- 出所の札（`log` / `record` / `derived`）は**テスト局所のリテラルで持ち**、
  3つが互いに別物であることを `TestLatencySourceLabels` が固定する。
  実装の定数を期待値に使うと、**3つを同じ文字列へ潰す変異が素通りする**
  ——札が全部同じになれば「同じ量を二重に載せない」保証が消えるのに、
  期待値も一緒に動くので差が現れない（タスク4.1 の教訓の再発形）

**期待値は実装の定数から組まない**（タスク4.1 の教訓）。分位点・fps・差分の
期待値はすべて本ファイルのリテラルから手計算で置いてある。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from m1_validation.errors import M1ConfigError
from m1_validation.metrics import latency as latency_module
from m1_validation.metrics.flight import _first_valid_sample_time_ms
from m1_validation.metrics.latency import (
    END_TO_END_FIELD,
    LATENCY_SOURCE_DERIVED,
    LATENCY_SOURCE_LOG,
    LATENCY_SOURCE_RECORD,
    RECORD_PREDICT_EVENT,
    LatencyResult,
    StageLatency,
    aggregate_latency,
)
from m1_validation.upstream import (
    STAGE_PREDICT,
    UpstreamGateway,
    resolve_runtime_settings,
)
from prediction_core import (
    Prediction,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowRecord,
    TrajectoryParameters,
)
from sensing_foundation import summarize_log

# ---------------------------------------------------------------------------
# 説明文の期待値（**一文としてリテラルで持つ**。モジュールの定数を参照しない）
# ---------------------------------------------------------------------------

EXPECTED_DEFINITION_SENTENCE = (
    "**ある観測の取得時刻から、その観測を含めた予測が得られるまでの"
    "経過時間**である。"
)
EXPECTED_NOT_A_SUM_SENTENCE = "**この値は段階別レイテンシの合計とは一致しない。**"
EXPECTED_INVALID_EXCLUDED_SENTENCE = (
    "予測が成立しなかった更新（`valid` が偽）は「予測が得られた」に"
    "当たらないので算入しない。"
)
EXPECTED_FIRST_PREDICTION_SENTENCE = (
    "**本 Spec は単発予測ではなく初回予測を基準としている。**"
)
EXPECTED_NOT_SINGLE_SHOT_SENTENCE = "**単発予測の値として読まないこと。**"
EXPECTED_UNKNOWN_STAGE_SENTENCE = (
    "**上流が段階を足しても本モジュールの改修は要らず、本 Spec が知らない"
    "段階名も捨てずに残す**"
)

# ---------------------------------------------------------------------------
# 出所の札（**テスト局所のリテラル**。実装の定数と自分自身を比べない）
# ---------------------------------------------------------------------------

#: `StageLatency.source` の3値。tasks.md「Implementation Notes」タスク4.1 の
#: 教訓——**参照解を実装の定数から組むと、その定数を変えたとき参照解が一緒に
#: 動いて差が消える**——をここにも当てる。実装の
#: `LATENCY_SOURCE_LOG` / `_RECORD` / `_DERIVED` を期待値に使うと、3つを同じ
#: 文字列へ潰す変異が素通りする（レビュー指摘）。
#:
#: この3つは「同じ量を別の出所から二重に載せない」ための札であり、
#: **互いに別物であること自体が保証の中身**である。したがって
#: `TestLatencySourceLabels` が値の一致だけでなく**相互排他**も固定する。
EXPECTED_SOURCE_LOG = "log"
EXPECTED_SOURCE_RECORD = "record"
EXPECTED_SOURCE_DERIVED = "derived"

# ---------------------------------------------------------------------------
# 合成ログの中身（期待値はここから手計算で置く）
# ---------------------------------------------------------------------------

#: `capture`/`frame` の送出時刻（ms）。5 件・幅 160 ms → (5-1)/0.160 = 25.0 fps。
CAPTURE_T_MS = (100.0, 140.0, 180.0, 220.0, 260.0)
EXPECTED_CAPTURE_FPS = 25.0

#: `track`/`frame` の送出時刻（ms）。3 件・幅 200 ms → (3-1)/0.200 = 10.0 fps。
TRACK_T_MS = (100.0, 200.0, 300.0)
EXPECTED_PROCESS_FPS = 10.0

#: `capture`/`frame` の各区間（ms）。`total_ms` は wait+drain+handoff。
CAPTURE_WAIT_MS = (10.0, 20.0, 30.0, 40.0, 50.0)
CAPTURE_DRAIN_MS = (1.0, 3.0, 5.0, 7.0, 9.0)
CAPTURE_HANDOFF_MS = (2.0, 4.0, 6.0, 8.0, 10.0)
CAPTURE_TOTAL_MS = (13.0, 27.0, 41.0, 55.0, 69.0)

#: 取りこぼし（3 件）と欠落（1 件）。**異なる値**にしてある（入れ替え検出）。
CAPTURE_DROPPED_BEFORE = (0, 1, 0, 2, 0)
CAPTURE_GAP_BEFORE = (0, 0, 1, 0, 0)
EXPECTED_FRAMES_DROPPED = 3
EXPECTED_FRAMES_MISSING = 1

#: 資源値。平均 30.0 と最大 3000 は**異なる値**にしてある（入れ替え検出）。
RESOURCE_CPU_PERCENT = (10.0, 20.0, 60.0)
RESOURCE_RSS_BYTES = (1000, 3000, 2000)
EXPECTED_CPU_PERCENT_MEAN = 30.0
EXPECTED_RSS_BYTES_MAX = 3000

#: `capture`/`frame` の `total_ms` の分位点（線形補間。n=5）。
#: p50: k=(5-1)*0.5=2 → v[2]=41.0 / p95: k=3.8 → 55*0.2+69*0.8=66.2
EXPECTED_TOTAL_P50_MS = 41.0
EXPECTED_TOTAL_P95_MS = 66.2
EXPECTED_TOTAL_MEAN_MS = 41.0

#: 投擲 `t-1` の予測更新。**有効な2件だけ**が end-to-end に入る。
#: 13.0 = 1093-1080 / 9.0 = 1129-1120
EXPECTED_END_TO_END_MS = (13.0, 9.0)
#: 分位点（n=2、昇順 [9.0, 13.0]）。p50: k=0.5 → 11.0 / p95: k=0.95 → 12.8
EXPECTED_E2E_P50_MS = 11.0
EXPECTED_E2E_P95_MS = 12.8

#: 実測項目3。1093（初回の有効予測が得られた時刻）− 1000（検出開始）。
EXPECTED_DETECT_TO_FIRST_PREDICTION_MS = 93.0
EXPECTED_FIRST_PREDICTION_SAMPLE_COUNT = 3

RECORD_1 = "t-1"
RECORD_2 = "t-2"
RECORD_3 = "t-3"
FOREIGN_RECORD = "t-9"

RECORD_1_SAMPLE_T_MS = (1000.0, 1040.0, 1080.0, 1120.0, 1160.0)
RECORD_2_SAMPLE_T_MS = (2000.0, 2050.0)
RECORD_3_SAMPLE_T_MS = (3000.0,)

TOLERANCE = 1e-9


def _line(stage: str, event: str, t_ms: float, data: dict[str, object]) -> dict:
    return {
        "t_ms": t_ms,
        "session_id": "s-test",
        "stage": stage,
        "event": event,
        "seq": 0,
        "data": data,
    }


def canonical_rows() -> list[dict]:
    """本ファイルの検査で共通に使う合成ログの行。

    上流が記録する段階（`capture` / `detect` / `track`）・本 Spec の段階
    （`predict`）・**本 Spec が知らない段階**（`warp` / `aux` / `ghost`）を
    混ぜてある。
    """
    rows: list[dict] = []
    for index, t_ms in enumerate(CAPTURE_T_MS):
        rows.append(
            _line(
                "capture",
                "frame",
                t_ms,
                {
                    "index": index,
                    "seq": index,
                    "wait_ms": CAPTURE_WAIT_MS[index],
                    "drain_ms": CAPTURE_DRAIN_MS[index],
                    "handoff_ms": CAPTURE_HANDOFF_MS[index],
                    "total_ms": CAPTURE_TOTAL_MS[index],
                    "dropped_before": CAPTURE_DROPPED_BEFORE[index],
                    "gap_before": CAPTURE_GAP_BEFORE[index],
                },
            )
        )
    for index, t_ms in enumerate((150.0, 250.0, 350.0)):
        rows.append(
            _line(
                "capture",
                "resources",
                t_ms,
                {
                    "cpu_percent": RESOURCE_CPU_PERCENT[index],
                    "process_rss_bytes": RESOURCE_RSS_BYTES[index],
                },
            )
        )
    for t_ms, detect_ms in ((99.0, 4.0), (199.0, 6.0)):
        rows.append(_line("detect", "frame", t_ms, {"detect_ms": detect_ms}))
    for index, t_ms in enumerate(TRACK_T_MS):
        rows.append(
            _line(
                "track",
                "frame",
                t_ms,
                {"appended": True, "track_ms": (2.0, 4.0, 9.0)[index]},
            )
        )

    # 投擲 t-1: 3 サンプル目で初回予測が成立する。
    for sample_t_ms, predicted_at_ms, sample_count, valid in (
        (1000.0, 1005.0, 1, False),
        (1040.0, 1046.0, 2, False),
        (1080.0, 1093.0, 3, True),
        (1120.0, 1129.0, 4, True),
    ):
        rows.append(
            _line(
                STAGE_PREDICT,
                "update",
                predicted_at_ms,
                {
                    "record_id": RECORD_1,
                    "sample_t_ms": sample_t_ms,
                    "sample_count": sample_count,
                    "predicted_at_ms": predicted_at_ms,
                    "valid": valid,
                },
            )
        )
    # `predicted_at_ms` を欠いた行（使えない行として数え、黙って捨てない）。
    rows.append(
        _line(
            STAGE_PREDICT,
            "update",
            1170.0,
            {
                "record_id": RECORD_1,
                "sample_t_ms": 1160.0,
                "sample_count": 5,
                "valid": True,
            },
        )
    )
    # 投擲 t-2: 最後まで予測が成立しない。
    for sample_t_ms, predicted_at_ms, sample_count in (
        (2000.0, 2004.0, 1),
        (2050.0, 2055.0, 2),
    ):
        rows.append(
            _line(
                STAGE_PREDICT,
                "update",
                predicted_at_ms,
                {
                    "record_id": RECORD_2,
                    "sample_t_ms": sample_t_ms,
                    "sample_count": sample_count,
                    "predicted_at_ms": predicted_at_ms,
                    "valid": False,
                },
            )
        )
    # 集計対象に含まれない投擲（混ぜない。差 50.0 は端に出るので混入は必ず見える）。
    rows.append(
        _line(
            STAGE_PREDICT,
            "update",
            3050.0,
            {
                "record_id": FOREIGN_RECORD,
                "sample_t_ms": 3000.0,
                "sample_count": 3,
                "predicted_at_ms": 3050.0,
                "valid": True,
            },
        )
    )

    # 本 Spec が知らない段階。所要時間を持つもの／持たないもの／欠測のもの。
    rows.append(
        _line("warp", "frame", 400.0, {"warp_ms": 7.0, "started_at_ms": 5000.0})
    )
    rows.append(_line("aux", "note", 410.0, {"count": 3}))
    rows.append(_line("ghost", "tick", 420.0, {"elapsed_ms": None}))
    return rows


def write_log(path: Path, rows: Sequence[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def summarize(log_path: Path) -> object:
    """集計器の注入（実運用では `UpstreamGateway.summarize_stages`）。"""
    return summarize_log(log_path, stages=None)


def record(
    record_id: str,
    sample_t_ms: Sequence[float],
    predictions: Sequence[Prediction] = (),
) -> ThrowRecord:
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=PredictionConfig(),
        samples=tuple(
            Sample(t_ms=t_ms, x_mm=0.0, y_mm=0.0, z_mm=1500.0) for t_ms in sample_t_ms
        ),
        predictions=tuple(predictions),
        extra={},
    )


def canonical_records() -> tuple[ThrowRecord, ...]:
    return (
        record(RECORD_1, RECORD_1_SAMPLE_T_MS),
        record(RECORD_2, RECORD_2_SAMPLE_T_MS),
        record(RECORD_3, RECORD_3_SAMPLE_T_MS),
    )


@pytest.fixture
def canonical(tmp_path: Path) -> LatencyResult:
    log_path = write_log(tmp_path / "session.ndjson", canonical_rows())
    return aggregate_latency(log_path, canonical_records(), summarize=summarize)


def stage_of(result: LatencyResult, stage: str, event: str, field: str) -> StageLatency:
    for entry in result.stages:
        if (entry.stage, entry.event, entry.field) == (stage, event, field):
            return entry
    raise AssertionError(f"段階が見つからない: {stage}/{event}/{field}")


class TestEndToEndDefinition:
    """end-to-end の定義文が出力に含まれる（要件 7.2。完了状態の片方）。"""

    def test_definition_states_both_ends(self, canonical: LatencyResult) -> None:
        assert EXPECTED_DEFINITION_SENTENCE in canonical.definition

    def test_definition_states_it_is_not_the_sum_of_stages(
        self, canonical: LatencyResult
    ) -> None:
        """**段階の合計と一致しない**旨が定義文にある（design.md Risks）。

        この一文が消えると、読み手は段階の合計と end-to-end の差を
        「どこかの計測漏れ」と読み違える。
        """
        assert EXPECTED_NOT_A_SUM_SENTENCE in canonical.definition

    def test_definition_states_invalid_updates_are_excluded(
        self, canonical: LatencyResult
    ) -> None:
        assert EXPECTED_INVALID_EXCLUDED_SENTENCE in canonical.definition

    def test_definition_is_not_the_first_prediction_note(
        self, canonical: LatencyResult
    ) -> None:
        """2つの説明文を**取り違えていない**（相互排他で固定する）。"""
        assert EXPECTED_FIRST_PREDICTION_SENTENCE not in canonical.definition
        assert EXPECTED_DEFINITION_SENTENCE not in canonical.first_prediction_basis


class TestFirstPredictionBasis:
    """単発予測ではなく初回予測を基準としている旨を明示する（要件 5.3）。"""

    def test_states_the_basis_is_the_first_prediction(
        self, canonical: LatencyResult
    ) -> None:
        assert EXPECTED_FIRST_PREDICTION_SENTENCE in canonical.first_prediction_basis

    def test_warns_against_reading_it_as_a_single_shot_value(
        self, canonical: LatencyResult
    ) -> None:
        assert EXPECTED_NOT_SINGLE_SHOT_SENTENCE in canonical.first_prediction_basis


class TestStageReadingNote:
    """未知の段階名を捨てない旨が出力に残る（要件 7.3）。"""

    def test_states_unknown_stages_are_kept(self, canonical: LatencyResult) -> None:
        assert EXPECTED_UNKNOWN_STAGE_SENTENCE in canonical.stage_note


class TestUnknownStages:
    """未知の段階を含む合成ログを集計できる（完了状態のもう片方。要件 7.3）。"""

    def test_unknown_stage_latency_is_kept(self, canonical: LatencyResult) -> None:
        entry = stage_of(canonical, "warp", "frame", "warp_ms")
        assert entry.count == 1
        assert entry.p50_ms == pytest.approx(7.0, abs=TOLERANCE)
        assert entry.source == EXPECTED_SOURCE_LOG

    def test_unknown_stage_names_are_listed(self, canonical: LatencyResult) -> None:
        """所要時間を持たない段階・欠測の段階も**名前は残す**。"""
        assert canonical.unknown_stages == ("aux", "ghost", "warp")

    def test_known_stages_are_not_reported_as_unknown(
        self, canonical: LatencyResult
    ) -> None:
        for known in ("capture", "detect", "track", STAGE_PREDICT):
            assert known not in canonical.unknown_stages

    def test_a_brand_new_stage_needs_no_change_here(self, tmp_path: Path) -> None:
        """上流が段階を足しても集計側の改修が要らない（要件 7.3）。"""
        rows = [
            _line("segmentation", "frame", 10.0, {"segment_ms": 2.0}),
            _line("segmentation", "frame", 20.0, {"segment_ms": 4.0}),
        ]
        log_path = write_log(tmp_path / "new-stage.ndjson", rows)
        result = aggregate_latency(log_path, (), summarize=summarize)
        entry = stage_of(result, "segmentation", "frame", "segment_ms")
        assert entry.count == 2
        assert entry.p50_ms == pytest.approx(3.0, abs=TOLERANCE)
        assert result.unknown_stages == ("segmentation",)


class TestStageLatencies:
    """段階別レイテンシは上流の集計器の出力をそのまま並べ替えたものである。"""

    def test_lists_every_duration_field_it_read(
        self, canonical: LatencyResult
    ) -> None:
        assert tuple(
            (entry.stage, entry.event, entry.field, entry.source)
            for entry in canonical.stages
        ) == (
            ("capture", "frame", "drain_ms", EXPECTED_SOURCE_LOG),
            ("capture", "frame", "handoff_ms", EXPECTED_SOURCE_LOG),
            ("capture", "frame", "total_ms", EXPECTED_SOURCE_LOG),
            ("capture", "frame", "wait_ms", EXPECTED_SOURCE_LOG),
            ("detect", "frame", "detect_ms", EXPECTED_SOURCE_LOG),
            ("track", "frame", "track_ms", EXPECTED_SOURCE_LOG),
            ("warp", "frame", "warp_ms", EXPECTED_SOURCE_LOG),
        )

    def test_quantiles_come_from_the_upstream_summary(
        self, canonical: LatencyResult
    ) -> None:
        entry = stage_of(canonical, "capture", "frame", "total_ms")
        assert entry.count == 5
        assert entry.p50_ms == pytest.approx(EXPECTED_TOTAL_P50_MS, abs=TOLERANCE)
        assert entry.p95_ms == pytest.approx(EXPECTED_TOTAL_P95_MS, abs=TOLERANCE)
        assert entry.mean_ms == pytest.approx(EXPECTED_TOTAL_MEAN_MS, abs=TOLERANCE)
        assert entry.min_ms == pytest.approx(13.0, abs=TOLERANCE)
        assert entry.max_ms == pytest.approx(69.0, abs=TOLERANCE)

    def test_timestamp_fields_are_not_read_as_durations(
        self, canonical: LatencyResult
    ) -> None:
        """`sample_t_ms` / `predicted_at_ms` / `started_at_ms` は**時刻**である。

        所要時間として並べると、`predict` 段の「レイテンシ」が投擲の
        観測時刻そのもの（数千 ms）になって表を壊す。
        """
        read = {(entry.stage, entry.field) for entry in canonical.stages}
        assert (STAGE_PREDICT, "sample_t_ms") not in read
        assert (STAGE_PREDICT, "predicted_at_ms") not in read
        assert ("warp", "started_at_ms") not in read

    def test_fields_with_no_numeric_value_are_left_out(
        self, canonical: LatencyResult
    ) -> None:
        """欠測は**0 で埋めない**。数値が1件も無い段階は行を作らない。"""
        assert all(entry.stage != "ghost" for entry in canonical.stages)


class TestEndToEnd:
    """end-to-end の算出（要件 7.2）。"""

    def test_uses_only_valid_updates(self, canonical: LatencyResult) -> None:
        assert canonical.end_to_end.count == len(EXPECTED_END_TO_END_MS)
        assert canonical.end_to_end.min_ms == pytest.approx(9.0, abs=TOLERANCE)
        assert canonical.end_to_end.max_ms == pytest.approx(13.0, abs=TOLERANCE)

    def test_quantiles_match_the_hand_computed_values(
        self, canonical: LatencyResult
    ) -> None:
        assert canonical.end_to_end.p50_ms == pytest.approx(
            EXPECTED_E2E_P50_MS, abs=TOLERANCE
        )
        assert canonical.end_to_end.p95_ms == pytest.approx(
            EXPECTED_E2E_P95_MS, abs=TOLERANCE
        )

    def test_is_identified_as_derived(self, canonical: LatencyResult) -> None:
        assert canonical.end_to_end.stage == STAGE_PREDICT
        assert canonical.end_to_end.field == END_TO_END_FIELD
        assert canonical.end_to_end.source == EXPECTED_SOURCE_DERIVED

    def test_direction_is_prediction_minus_observation(
        self, canonical: LatencyResult
    ) -> None:
        """引く向きを取り違えると符号が反転する（13.0 と -13.0 は別物）。"""
        assert canonical.end_to_end.max_ms == pytest.approx(13.0, abs=TOLERANCE)
        assert canonical.end_to_end.min_ms > 0.0

    def test_updates_of_other_throws_are_not_mixed_in(
        self, canonical: LatencyResult
    ) -> None:
        """集計対象に無い投擲の予測を混ぜない（混ざれば 50.0 が端に出る）。"""
        assert canonical.foreign_prediction_events == 1
        assert canonical.end_to_end.max_ms == pytest.approx(13.0, abs=TOLERANCE)

    def test_unusable_updates_are_counted_not_dropped(
        self, canonical: LatencyResult
    ) -> None:
        assert canonical.unusable_prediction_events == 1

    def test_no_predictions_at_all_is_missing_not_zero(self, tmp_path: Path) -> None:
        log_path = write_log(
            tmp_path / "no-predict.ndjson",
            [_line("capture", "frame", 1.0, {"total_ms": 5.0})],
        )
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.end_to_end.count == 0
        assert result.end_to_end.p50_ms is None
        assert result.end_to_end.mean_ms is None


class TestDetectToFirstPrediction:
    """実測項目3（検出開始 → 初回予測）。"""

    def test_measures_from_detection_start_to_the_first_valid_prediction(
        self, canonical: LatencyResult
    ) -> None:
        first = canonical.detect_to_first_prediction[0]
        assert first.record_id == RECORD_1
        assert first.detection_start_ms == pytest.approx(1000.0, abs=TOLERANCE)
        assert first.detect_to_first_prediction_ms == pytest.approx(
            EXPECTED_DETECT_TO_FIRST_PREDICTION_MS, abs=TOLERANCE
        )
        assert (
            first.first_prediction_sample_count
            == EXPECTED_FIRST_PREDICTION_SAMPLE_COUNT
        )

    def test_detection_start_is_the_first_valid_sample_not_the_first_log_line(
        self, canonical: LatencyResult
    ) -> None:
        """検出開始は**記録の最初の有効サンプル**である（要件 5.2 と同じ規則）。

        初回の有効予測が基づいた観測（1080.0）を起点にすると 13.0 になる。
        93.0 との差がそのまま現れる。
        """
        first = canonical.detect_to_first_prediction[0]
        assert first.detect_to_first_prediction_ms != pytest.approx(13.0, abs=1e-6)

    def test_detection_start_agrees_with_the_flight_metrics_rule(
        self, canonical: LatencyResult
    ) -> None:
        """`flight.py` の「最初の有効サンプルの観測時刻」と一致する。

        同じ量が2つの規則から出ると、実測項目2（リリース〜検出開始）と
        項目3（検出開始〜初回予測）が別の起点を指し、区間1 と区間2 の境目が
        重なったり空いたりする。**本モジュールが申告した検出開始**を
        `flight.py` の規則の出力と突き合わせる。
        """
        target = record(RECORD_1, RECORD_1_SAMPLE_T_MS)
        assert canonical.detect_to_first_prediction[0].detection_start_ms == (
            pytest.approx(_first_valid_sample_time_ms(target.samples), abs=TOLERANCE)
        )

    def test_a_throw_without_a_valid_prediction_is_missing(
        self, canonical: LatencyResult
    ) -> None:
        """**0 で埋めない**（「0 ms で予測が出た」と「出なかった」は別）。"""
        second = canonical.detect_to_first_prediction[1]
        assert second.record_id == RECORD_2
        assert second.detect_to_first_prediction_ms is None
        assert second.first_prediction_sample_count is None

    def test_a_throw_absent_from_the_log_is_missing(
        self, canonical: LatencyResult
    ) -> None:
        third = canonical.detect_to_first_prediction[2]
        assert third.record_id == RECORD_3
        assert third.detect_to_first_prediction_ms is None

    def test_follows_the_order_of_the_given_records(
        self, canonical: LatencyResult
    ) -> None:
        assert tuple(
            item.record_id for item in canonical.detect_to_first_prediction
        ) == (RECORD_1, RECORD_2, RECORD_3)

    def test_duplicate_record_ids_are_refused(self, tmp_path: Path) -> None:
        """同じ識別子の投擲を2つ渡されたら、黙って束ねない。"""
        log_path = write_log(tmp_path / "dup.ndjson", canonical_rows())
        records = (
            record(RECORD_1, RECORD_1_SAMPLE_T_MS),
            record(RECORD_1, RECORD_2_SAMPLE_T_MS),
        )
        with pytest.raises(M1ConfigError):
            aggregate_latency(log_path, records, summarize=summarize)


class TestPredictionLatencyFromRecords:
    """予測そのものの所要時間は記録（`Prediction.elapsed_ms`）から読む。"""

    def test_reports_elapsed_ms_of_the_predictions(self, tmp_path: Path) -> None:
        log_path = write_log(tmp_path / "empty.ndjson", [])
        records = (
            record(
                RECORD_1,
                RECORD_1_SAMPLE_T_MS,
                predictions=(
                    prediction(sample_count=3, elapsed_ms=2.0),
                    prediction(sample_count=4, elapsed_ms=6.0),
                ),
            ),
        )
        result = aggregate_latency(log_path, records, summarize=summarize)
        entry = stage_of(result, STAGE_PREDICT, RECORD_PREDICT_EVENT, "elapsed_ms")
        assert entry.source == EXPECTED_SOURCE_RECORD
        assert entry.count == 2
        assert entry.p50_ms == pytest.approx(4.0, abs=TOLERANCE)
        assert entry.max_ms == pytest.approx(6.0, abs=TOLERANCE)

    def test_unmeasured_prediction_time_is_missing_not_zero(
        self, tmp_path: Path
    ) -> None:
        """計測が無効なら `elapsed_ms` は `None` である（要件 7.5）。行を作らない。"""
        log_path = write_log(tmp_path / "empty.ndjson", [])
        records = (
            record(
                RECORD_1,
                RECORD_1_SAMPLE_T_MS,
                predictions=(prediction(sample_count=3, elapsed_ms=None),),
            ),
        )
        result = aggregate_latency(log_path, records, summarize=summarize)
        assert result.stages == ()


class TestThroughput:
    """取得 fps と実処理 fps（§13.1）。**入れ替えたら両方落ちる**値にしてある。"""

    def test_capture_and_process_fps_are_distinct_quantities(
        self, canonical: LatencyResult
    ) -> None:
        assert canonical.capture_fps == pytest.approx(
            EXPECTED_CAPTURE_FPS, abs=TOLERANCE
        )
        assert canonical.process_fps == pytest.approx(
            EXPECTED_PROCESS_FPS, abs=TOLERANCE
        )

    def test_fps_is_missing_when_the_span_cannot_be_measured(
        self, tmp_path: Path
    ) -> None:
        """1 件では幅が取れない。**0 で埋めない**。"""
        log_path = write_log(
            tmp_path / "single.ndjson",
            [_line("capture", "frame", 1.0, {"total_ms": 5.0})],
        )
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.capture_fps is None
        assert result.process_fps is None


class TestResourceUsage:
    """資源使用（§13.1）。取得できない環境では欠測（要件 7.1）。"""

    def test_reports_cpu_mean_and_rss_max(self, canonical: LatencyResult) -> None:
        assert canonical.cpu_percent_mean == pytest.approx(
            EXPECTED_CPU_PERCENT_MEAN, abs=TOLERANCE
        )
        assert canonical.rss_bytes_max == EXPECTED_RSS_BYTES_MAX

    def test_missing_resource_events_are_missing_not_zero(
        self, tmp_path: Path
    ) -> None:
        log_path = write_log(
            tmp_path / "no-res.ndjson",
            [_line("capture", "frame", 1.0, {"total_ms": 5.0})],
        )
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.cpu_percent_mean is None
        assert result.rss_bytes_max is None

    def test_null_resource_values_are_missing_not_zero(self, tmp_path: Path) -> None:
        """`/proc` が読めない環境では上流が `None` を送る（`Sysstat`）。"""
        log_path = write_log(
            tmp_path / "null-res.ndjson",
            [
                _line(
                    "capture",
                    "resources",
                    1.0,
                    {"cpu_percent": None, "process_rss_bytes": None},
                )
            ],
        )
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.cpu_percent_mean is None
        assert result.rss_bytes_max is None


class TestDroppedAndMissingFrames:
    """取りこぼし（§13.1 dropped frames）と欠落。"""

    def test_sums_dropped_and_missing_separately(
        self, canonical: LatencyResult
    ) -> None:
        assert canonical.frames_dropped == EXPECTED_FRAMES_DROPPED
        assert canonical.frames_missing == EXPECTED_FRAMES_MISSING

    def test_no_capture_events_means_missing_not_zero(self, tmp_path: Path) -> None:
        """「取りこぼしが 0 だった」と「取得を測っていない」は別である。"""
        log_path = write_log(
            tmp_path / "no-capture.ndjson",
            [_line("warp", "frame", 1.0, {"warp_ms": 5.0})],
        )
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.frames_dropped is None
        assert result.frames_missing is None


class TestLogHealth:
    """集計の入力そのものが欠けていないか（ログ器が捨てた行・読めなかった行）。"""

    def test_reports_dropped_and_skipped_log_lines(self, tmp_path: Path) -> None:
        rows = [
            _line(
                "system",
                "session_end",
                9.0,
                {"emitted": 10, "dropped": 4, "written": 6, "write_errors": 0},
            )
        ]
        log_path = write_log(tmp_path / "health.ndjson", rows)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("これは JSON ではない\n")
        result = aggregate_latency(log_path, (), summarize=summarize)
        assert result.log_lines_dropped == 4
        assert result.log_lines_skipped == 1


class TestNoStandingAggregationOnDevice:
    """集計を実機上で常時実行する前提を持たない（要件 7.9）。"""

    def test_only_reads_a_log_file(self, tmp_path: Path) -> None:
        """取得も送出も要求せず、ログファイルと記録だけで完結する。"""
        log_path = write_log(tmp_path / "standalone.ndjson", canonical_rows())
        result = aggregate_latency(log_path, canonical_records(), summarize=summarize)
        assert result.end_to_end.count == 2

    def test_repeating_the_aggregation_gives_the_same_result(
        self, tmp_path: Path
    ) -> None:
        """同一入力に対して同一の集計値を返す（要件 12.4）。"""
        log_path = write_log(tmp_path / "twice.ndjson", canonical_rows())
        first = aggregate_latency(log_path, canonical_records(), summarize=summarize)
        second = aggregate_latency(log_path, canonical_records(), summarize=summarize)
        assert first == second


class TestUpstreamAggregatorIsUsed:
    """段階の集計は上流の集計器へ委譲する（research.md Decision 7）。"""

    def test_accepts_the_gateway_method_as_the_summarizer(
        self, tmp_path: Path
    ) -> None:
        """実運用の注入元（`UpstreamGateway.summarize_stages`）で通ること。"""
        log_path = write_log(tmp_path / "gateway.ndjson", canonical_rows())
        gateway = UpstreamGateway.open(
            session_id="test-latency",
            source_spec=resolve_runtime_settings(
                file=None,
                env={},
                overrides={
                    "source": "simulated",
                    "width_px": 8,
                    "height_px": 6,
                    "fps": 30,
                    "logging_path": str(tmp_path / "logs"),
                    "recording_root": str(tmp_path / "sessions"),
                },
            ),
        )
        try:
            result = aggregate_latency(
                log_path,
                canonical_records(),
                summarize=gateway.summarize_stages,
            )
        finally:
            gateway.close()
        assert stage_of(result, "capture", "frame", "total_ms").count == 5

    def test_does_not_import_the_upstream_package_itself(self) -> None:
        """`sensing_foundation` へ直接は触らない（接点は `upstream.py` だけ）。"""
        source = Path(latency_module.__file__).read_text(encoding="utf-8")
        assert "import sensing_foundation" not in source
        assert "from sensing_foundation" not in source


class TestLatencySourceLabels:
    """出所の札3つ（要件 7.1 の「二重に載せない」保証そのもの）。

    `source` は「同じ量を別の出所から二重に載せない」ための札であり、
    **3つが互いに別物であることが保証の中身**である。3つが同じ文字列に
    潰れれば、ログ由来の所要時間と記録由来の所要時間と本モジュールの算出値が
    区別できなくなり、`predict` 段の `elapsed_ms` が二重計上されていても
    読み手には見分けが付かない。

    期待値は**テスト局所のリテラル**（`EXPECTED_SOURCE_*`）で持つ。実装の
    定数を期待値に使うと、定数を変えたとき期待値も一緒に動いて差が消える
    （tasks.md「Implementation Notes」タスク4.1 と同じ形の空振り）。
    """

    def test_labels_have_the_expected_values(self) -> None:
        assert LATENCY_SOURCE_LOG == EXPECTED_SOURCE_LOG
        assert LATENCY_SOURCE_RECORD == EXPECTED_SOURCE_RECORD
        assert LATENCY_SOURCE_DERIVED == EXPECTED_SOURCE_DERIVED

    def test_labels_are_mutually_exclusive(self) -> None:
        """**相互排他で固定する**（`!=` の連鎖ではなく、3つが別物であること）。"""
        assert (
            len({LATENCY_SOURCE_LOG, LATENCY_SOURCE_RECORD, LATENCY_SOURCE_DERIVED})
            == 3
        )

    def test_every_reported_source_is_one_of_the_three(
        self, canonical: LatencyResult
    ) -> None:
        """出所の分からない行を作らない。"""
        reported = {entry.source for entry in canonical.stages}
        reported.add(canonical.end_to_end.source)
        assert reported <= {
            EXPECTED_SOURCE_LOG,
            EXPECTED_SOURCE_RECORD,
            EXPECTED_SOURCE_DERIVED,
        }


class TestResultShape:
    def test_result_is_frozen(self, canonical: LatencyResult) -> None:
        with pytest.raises(FrozenInstanceError):
            canonical.capture_fps = 1.0  # type: ignore[misc]


def prediction(*, sample_count: int, elapsed_ms: float | None) -> Prediction:
    """`elapsed_ms` 以外は結果に効かない予測（形を満たすだけ）。"""
    return Prediction(
        predicted_hit_x_mm=0.0,
        predicted_hit_y_mm=0.0,
        predicted_hit_time_ms=1200.0,
        remaining_time_ms=100.0,
        estimated_vx_mm_s=1000.0,
        estimated_vy_mm_s=0.0,
        estimated_vz_mm_s=0.0,
        residual=1.0,
        trajectory=TrajectoryParameters(
            t_ref_ms=1000.0,
            x0_mm=0.0,
            y0_mm=0.0,
            z0_mm=1500.0,
            estimated_vx_mm_s=1000.0,
            estimated_vy_mm_s=0.0,
            estimated_vz_mm_s=0.0,
            gravity_mm_s2=9806.65,
        ),
        sample_count=sample_count,
        based_on_time_ms=1000.0 + 40.0 * sample_count,
        elapsed_ms=elapsed_ms,
        config=PredictionConfig(),
    )
