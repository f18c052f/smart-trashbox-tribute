"""SweepEngine のテスト（要件6.1, 6.4, 6.6, 6.7, 8.1, 8.3 / design.md
「SweepEngine」）。

`trajectory_sim.sweep` が以下を満たすことを固定する:

- 未知の軸名・空の軸・空の値・重複軸名・閾値未指定・試行回数不正が、
  いかなる評価（`evaluate_throw` / `evaluate_reachability`）も行う前に
  `SweepDefinitionError` として拒否されること（design.md Batch / Job
  Contract: 「1つでも不正なら1件も評価しない」）
- 軸の直積が `itertools.product` の行優先順で走査され、`cell_index` が
  その反復順序と一致すること
- 格子点ごとの集計（成立数・評価件数・成立割合・指標平均）が評価対象外を
  正しく分母・平均の双方から除くこと（要件6.7）
- 同一の `(cell_index, trial_index)` 組は、掃引全体の走査順序に関わらず
  同一の結果を導くこと（要件8.3: 評価順序に依存しない）
- 同一入力を2回掃引しても同一の結果になること（決定性・再現性）
- `derive_seed` が決定的かつ入力ごとに異なる値を返し、組み込み `hash()`
  に依存しないこと
- `SweepKind.REACHABILITY` の格子点が `evaluate_reachability` の直接呼び
  出しと一致すること
- `keep_representative_record` が真のとき、`cell_index=0` の格子点にのみ
  `representative` が保持されること

ファイル名は `test_trajectory_sim_sweep.py`
（`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する）。
"""

from __future__ import annotations

from random import Random

import pytest

from trajectory_sim import evaluate
from trajectory_sim.errors import SweepDefinitionError
from trajectory_sim.params import (
    CatchCriteria,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
)
from trajectory_sim.results import CellStatus, NotEvaluatedReason, ScenarioOutcome
from trajectory_sim.sweep import AxisSpec, SweepKind, SweepSpec, derive_seed, run_sweep

try:
    from prediction_core import PredictionConfig
except ImportError:  # pragma: no cover - prediction_core は依存として常に存在する想定
    PredictionConfig = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# 共通フィクスチャ（test_trajectory_sim_evaluate.py と同じ構成方針）
# ---------------------------------------------------------------------------


def _throw(**overrides: float) -> ThrowParams:
    base: dict[str, float] = dict(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=2000.0,
        speed_mm_s=4000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )
    base.update(overrides)
    return ThrowParams(**base)


def _observation_params(**overrides: float) -> ObservationParams:
    base: dict[str, float] = dict(
        detection_start_delay_ms=20.0,
        sample_period_ms=20.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=3.0,
    )
    base.update(overrides)
    return ObservationParams(**base)


def _drivetrain(**overrides: float) -> DrivetrainParams:
    base: dict[str, float] = dict(
        max_speed_mm_s=6000.0,
        max_accel_mm_s2=30000.0,
        max_decel_mm_s2=30000.0,
        control_period_ms=1.0,
        command_latency_ms=1.0,
        integration_step_ms=1.0,
    )
    base.update(overrides)
    return DrivetrainParams(**base)


def _scenario_params(
    *,
    throw: ThrowParams | None = None,
    dispersion: ThrowDispersion | None = None,
    observation_params: ObservationParams | None = None,
    drivetrain_params: DrivetrainParams | None = None,
    catch: CatchCriteria | None = None,
    layout: LayoutParams | None = None,
    prediction: "PredictionConfig | None" = None,
) -> ScenarioParams:
    assert PredictionConfig is not None
    return ScenarioParams(
        throw=throw if throw is not None else _throw(),
        dispersion=dispersion if dispersion is not None else ThrowDispersion(),
        observation=(
            observation_params if observation_params is not None else _observation_params()
        ),
        drivetrain=drivetrain_params if drivetrain_params is not None else _drivetrain(),
        catch=catch if catch is not None else CatchCriteria(),
        layout=layout if layout is not None else LayoutParams(home_x_mm=0.0, home_y_mm=0.0),
        prediction=prediction if prediction is not None else PredictionConfig(),
    )


def _outcome(
    *,
    catchable: bool | None,
    not_evaluated_reason: NotEvaluatedReason | None = None,
    position_error_mm: float | None = 0.0,
    hold_time_ms: float | None = 100.0,
    required_distance_mm: float | None = 50.0,
    prediction_error_mm: float | None = 0.0,
    residual_speed_mm_s: float | None = 0.0,
    record: object | None = None,
) -> ScenarioOutcome:
    """テスト専用の `ScenarioOutcome` 構築ヘルパ（値の妥当性は呼び出し側が保証する）。"""
    evaluated = catchable is not None
    return ScenarioOutcome(
        catchable=catchable,
        not_evaluated_reason=not_evaluated_reason,
        true_impact_x_mm=0.0 if evaluated else None,
        true_impact_y_mm=0.0 if evaluated else None,
        true_impact_time_ms=100.0 if evaluated else None,
        required_distance_mm=required_distance_mm,
        hold_time_ms=hold_time_ms,
        first_command_time_ms=10.0 if evaluated else None,
        position_error_mm=position_error_mm,
        residual_speed_mm_s=residual_speed_mm_s,
        prediction_error_mm=prediction_error_mm,
        sample_count=5,
        valid_prediction_count=3,
        record=record,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 完了条件1: 評価を1件も行う前の一括検証
# ---------------------------------------------------------------------------


def test_run_sweep_rejects_empty_axes() -> None:
    spec = SweepSpec(kind=SweepKind.THROW, axes=())
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_axis_with_empty_values() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=()),),
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_unknown_axis_name_for_throw_and_never_evaluates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> ScenarioOutcome:
        raise AssertionError("evaluate_throw は検証失敗時には一度も呼ばれてはならない")

    monkeypatch.setattr(evaluate, "evaluate_throw", fail_if_called)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="not.a.real.path", unit="", values=(1.0,)),),
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_unknown_axis_name_for_reachability_and_never_evaluates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> ScenarioOutcome:
        raise AssertionError("evaluate_reachability は検証失敗時には一度も呼ばれてはならない")

    monkeypatch.setattr(evaluate, "evaluate_reachability", fail_if_called)

    # hold_time_ms のみでは REACHABILITY 軸として不十分(両方必須)。
    spec = SweepSpec(
        kind=SweepKind.REACHABILITY,
        axes=(AxisSpec(name="hold_time_ms", unit="ms", values=(100.0,)),),
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_reachability_axis_names_with_foreign_name_mixed_in_and_never_evaluates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`SweepKind.REACHABILITY` の軸名集合に、必須の2つに加えて無関係な
    （THROW 専用の）軸名が混入している場合も拒否されること。

    既存のテスト（`test_run_sweep_rejects_unknown_axis_name_for_reachability_...`）
    は「必須の2つのうち片方が欠けている」ケースのみを検証しており、
    「必須の2つに加えて無関係な名前が混ざっている」ケースは検証していない。
    どちらも `frozenset(names) != _REACHABILITY_AXIS_NAMES` という同じ
    集合の不一致判定で拒否されるべきだが、判定ロジックの実装によっては
    見落とされ得るため、別ケースとして固定する（レビューア指摘の非必須
    改善点）。
    """

    def fail_if_called(*args: object, **kwargs: object) -> ScenarioOutcome:
        raise AssertionError("evaluate_reachability は検証失敗時には一度も呼ばれてはならない")

    monkeypatch.setattr(evaluate, "evaluate_reachability", fail_if_called)

    spec = SweepSpec(
        kind=SweepKind.REACHABILITY,
        axes=(
            AxisSpec(name="hold_time_ms", unit="ms", values=(100.0,)),
            AxisSpec(name="required_distance_mm", unit="mm", values=(50.0,)),
            AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),
        ),
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_duplicate_axis_names_and_never_evaluates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> ScenarioOutcome:
        raise AssertionError("evaluate_throw は検証失敗時には一度も呼ばれてはならない")

    monkeypatch.setattr(evaluate, "evaluate_throw", fail_if_called)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(
            AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),
            AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(2000.0,)),
        ),
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_trials_per_cell_below_one() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=0,
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_requires_threshold_when_trials_per_cell_is_two_or_more() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=2,
        catch_ratio_threshold=None,
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


@pytest.mark.parametrize("bad_threshold", [-0.1, 1.1, float("nan"), float("inf")])
def test_run_sweep_rejects_out_of_range_or_nonfinite_threshold(bad_threshold: float) -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=1,
        catch_ratio_threshold=bad_threshold,
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_wraps_bad_enum_axis_value_as_sweep_definition_error() -> None:
    """`catch.policy` 軸に不正な文字列を与えた場合、`replace_by_path` が送出する
    素の `ValueError`（`TrajectorySimError` ではない）が `SweepDefinitionError`
    へ包み直されること（タスク1.5の実装メモへの対応。本タスクの必須要件では
    ないが、掃引定義エラーの分類として一貫させるための追加テスト）。
    """
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="catch.policy", unit="", values=("not_a_real_policy",)),),
        trials_per_cell=1,
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


def test_run_sweep_rejects_bad_enum_axis_value_mixed_with_valid_value_and_never_evaluates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`catch.policy` 軸に有効な値と無効な値が混在する場合、無効な値が
    格子点の走査順で後方（`itertools.product` の反復順で後）にあっても、
    `evaluate_throw` が一度も呼ばれる前に `SweepDefinitionError` が
    送出されること。

    これは「評価を1件も行う前に軸名・値・閾値の妥当性を一括検証する」
    （design.md「SweepEngine」Batch / Job Contract: 「1つでも不正なら
    1件も評価しない」）という要求を、単一値の軸だけでは検出できない
    タイミングのバグ（先頭の値の格子点が評価された後で、後方の不正な
    値の格子点で初めてエラーになる）に対して直接固定する。
    """

    def fail_if_called(*args: object, **kwargs: object) -> ScenarioOutcome:
        raise AssertionError("evaluate_throw は検証失敗時には一度も呼ばれてはならない")

    monkeypatch.setattr(evaluate, "evaluate_throw", fail_if_called)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(
            AxisSpec(
                name="catch.policy",
                unit="",
                values=("stop_and_wait", "not_a_real_policy"),
            ),
        ),
        trials_per_cell=1,
    )
    with pytest.raises(SweepDefinitionError):
        run_sweep(spec, _scenario_params())


# ---------------------------------------------------------------------------
# 完了条件2: 行優先順の格子生成と cell_index の一致
# ---------------------------------------------------------------------------


def test_run_sweep_assigns_cell_index_in_row_major_product_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2x3 格子で `cell_index` が `itertools.product` の反復順と一致すること
    （最後の軸が最も速く変化する）。物理計算を避けるため `evaluate_throw` を
    固定の成立結果に差し替え、グリッド生成のみを検証する。
    """

    def fake_evaluate_throw(
        params: object, rng: Random, record_id: str, keep_record: bool, extra: object
    ) -> ScenarioOutcome:
        return _outcome(catchable=True)

    monkeypatch.setattr(evaluate, "evaluate_throw", fake_evaluate_throw)

    axis_a = AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0, 2000.0))
    axis_b = AxisSpec(
        name="drivetrain.max_speed_mm_s", unit="mm/s", values=(3000.0, 4000.0, 5000.0)
    )
    spec = SweepSpec(kind=SweepKind.THROW, axes=(axis_a, axis_b), trials_per_cell=1)

    result = run_sweep(spec, _scenario_params())

    expected_axis_values = [
        (a, b) for a in axis_a.values for b in axis_b.values
    ]  # itertools.product(axis_a.values, axis_b.values) と同じ順序
    assert [cell.axis_values for cell in result.cells] == expected_axis_values


# ---------------------------------------------------------------------------
# 完了条件3: 評価順序に依存しない（要件8.3）
# ---------------------------------------------------------------------------


def test_cell_result_for_a_given_combination_is_independent_of_axis_order() -> None:
    """軸の並び順（＝ `cell_index` の割り当て）を入れ替えても、同じ論理的な
    軸値の組み合わせに対する `CellResult` の内容（`axis_values` 自身の並び順
    を除く）が一致すること。

    誤差要因ゼロ（既定の `ThrowDispersion` / 観測ノイズ）の構成を用いる。
    これにより、軸の並び替えで `cell_index`（したがって `derive_seed` の
    導出値）が変わっても、乱数列が実際の結果に影響しないため、
    論理的に同じパラメータの組み合わせは常に同じ `ScenarioOutcome` を生む。
    このことは、掃引が格子点をまたいで乱数状態を共有・再利用していない
    （各試行が `(seed, cell_index, trial_index)` だけから独立に導出した
    専用の `Random` を使っている）ことの間接的な証拠になる: もし実装が
    走査順に依存する共有 `Random` を使っていれば、`replace_by_path` の
    適用順序自体が異なる２つの走査で、少なくとも一部の格子点の
    `ScenarioParams` 構築結果が変わってしまう可能性がある。
    """
    axis_speed = AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3500.0, 4500.0))
    axis_drivetrain = AxisSpec(
        name="drivetrain.max_speed_mm_s", unit="mm/s", values=(500.0, 6000.0)
    )

    spec_forward = SweepSpec(
        kind=SweepKind.THROW, axes=(axis_speed, axis_drivetrain), trials_per_cell=1, seed=42
    )
    spec_reversed = SweepSpec(
        kind=SweepKind.THROW, axes=(axis_drivetrain, axis_speed), trials_per_cell=1, seed=42
    )

    base_params = _scenario_params()

    result_forward = run_sweep(spec_forward, base_params)
    result_reversed = run_sweep(spec_reversed, base_params)

    def canonical_map(spec: SweepSpec, result: object) -> dict[tuple[tuple[str, object], ...], object]:
        names = [axis.name for axis in spec.axes]
        mapping: dict[tuple[tuple[str, object], ...], object] = {}
        for cell in result.cells:
            key = tuple(sorted(zip(names, cell.axis_values)))
            mapping[key] = cell
        return mapping

    forward_by_combo = canonical_map(spec_forward, result_forward)
    reversed_by_combo = canonical_map(spec_reversed, result_reversed)

    assert set(forward_by_combo.keys()) == set(reversed_by_combo.keys())
    for key in forward_by_combo:
        fwd_cell = forward_by_combo[key]
        rev_cell = reversed_by_combo[key]
        assert fwd_cell.status == rev_cell.status
        assert fwd_cell.trials == rev_cell.trials
        assert fwd_cell.evaluated_trials == rev_cell.evaluated_trials
        assert fwd_cell.success_count == rev_cell.success_count
        assert fwd_cell.success_ratio == pytest.approx(rev_cell.success_ratio)
        assert dict(fwd_cell.metrics) == pytest.approx(dict(rev_cell.metrics))
        assert fwd_cell.not_evaluated_reason == rev_cell.not_evaluated_reason


# ---------------------------------------------------------------------------
# 完了条件4: 決定性・再現性
# ---------------------------------------------------------------------------


def test_run_sweep_is_deterministic_across_repeated_runs() -> None:
    """同一の `SweepSpec` / `base_params` を2回掃引しても同一の結果になること。

    `PredictionConfig(measure_elapsed=False)` を用いて `elapsed_ms` の
    計測ノイズによる非決定性（task 2.5 で判明した既知の落とし穴）を避ける。
    """
    axis = AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3000.0, 4200.0))
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(axis,),
        trials_per_cell=2,
        seed=7,
        catch_ratio_threshold=0.5,
        keep_representative_record=False,
    )
    base_params = _scenario_params(
        dispersion=ThrowDispersion(
            speed_sigma_mm_s=50.0, elevation_sigma_deg=1.0, azimuth_sigma_deg=1.0
        ),
        observation_params=_observation_params(sigma_x_mm=5.0, sigma_y_mm=5.0, dropout_ratio=0.1),
        prediction=PredictionConfig(measure_elapsed=False),
    )

    first = run_sweep(spec, base_params)
    second = run_sweep(spec, base_params)

    assert first.cells == second.cells


# ---------------------------------------------------------------------------
# 完了条件5: derive_seed の性質
# ---------------------------------------------------------------------------


def test_derive_seed_is_deterministic_for_same_triple() -> None:
    assert derive_seed(0, 0, 0) == derive_seed(0, 0, 0)
    assert derive_seed(42, 7, 3) == derive_seed(42, 7, 3)


def test_derive_seed_differs_across_distinct_triples() -> None:
    triples = [(0, 0, 0), (0, 0, 1), (0, 1, 0), (1, 0, 0), (42, 7, 3), (-1, 2, 5)]
    values = [derive_seed(*triple) for triple in triples]
    assert len(set(values)) == len(values)


def test_derive_seed_does_not_match_builtin_hash() -> None:
    """組み込み `hash()` は使わない（design.md が明示的に禁じる）ことの
    弱いが害のない健全性チェック。"""
    seed, cell_index, trial_index = 1, 2, 3
    assert derive_seed(seed, cell_index, trial_index) != hash((seed, cell_index, trial_index))


# ---------------------------------------------------------------------------
# 完了条件6: 集計の正しさ（評価対象外を分母・平均から除く）
# ---------------------------------------------------------------------------


def test_cell_aggregation_excludes_not_evaluated_trials_from_ratio_and_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1格子点・4試行のうち、成立2件・不成立1件・評価対象外1件という混在
    ケースで、`evaluated_trials` / `success_count` / `success_ratio` /
    `metrics` が評価対象外の試行を正しく除外して集計されること。

    評価対象外の試行にはあえて `position_error_mm` 等へ「もし含まれれば
    明らかにおかしくなる」ほど大きな見張り値(999999.0)を仕込み、それが
    `metrics` の平均に一切寄与しないことを直接証明する。
    """
    scripted = {
        (0, 0): _outcome(
            catchable=True,
            position_error_mm=10.0,
            hold_time_ms=200.0,
            required_distance_mm=150.0,
            prediction_error_mm=5.0,
            residual_speed_mm_s=20.0,
        ),
        (0, 1): _outcome(
            catchable=False,
            position_error_mm=90.0,
            hold_time_ms=210.0,
            required_distance_mm=160.0,
            prediction_error_mm=95.0,
            residual_speed_mm_s=250.0,
        ),
        (0, 2): _outcome(
            catchable=None,
            not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES,
            position_error_mm=999999.0,
            hold_time_ms=999999.0,
            required_distance_mm=999999.0,
            prediction_error_mm=999999.0,
            residual_speed_mm_s=999999.0,
        ),
        (0, 3): _outcome(
            catchable=True,
            position_error_mm=14.0,
            hold_time_ms=190.0,
            required_distance_mm=140.0,
            prediction_error_mm=6.0,
            residual_speed_mm_s=25.0,
        ),
    }

    def fake_evaluate_throw(
        params: object, rng: Random, record_id: str, keep_record: bool, extra: dict
    ) -> ScenarioOutcome:
        sim = extra["sim"]
        return scripted[(sim["cell_index"], sim["trial_index"])]

    monkeypatch.setattr(evaluate, "evaluate_throw", fake_evaluate_throw)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=4,
        catch_ratio_threshold=0.5,
    )

    result = run_sweep(spec, _scenario_params())
    assert len(result.cells) == 1
    cell = result.cells[0]

    assert cell.trials == 4
    assert cell.evaluated_trials == 3
    assert cell.success_count == 2
    assert cell.success_ratio == pytest.approx(2 / 3)
    assert cell.status is CellStatus.CATCHABLE  # 2/3 >= 0.5
    assert cell.not_evaluated_reason is None

    assert cell.metrics["position_error_mm"] == pytest.approx((10.0 + 90.0 + 14.0) / 3)
    assert cell.metrics["hold_time_ms"] == pytest.approx((200.0 + 210.0 + 190.0) / 3)
    assert cell.metrics["required_distance_mm"] == pytest.approx((150.0 + 160.0 + 140.0) / 3)
    assert cell.metrics["prediction_error_mm"] == pytest.approx((5.0 + 95.0 + 6.0) / 3)
    assert cell.metrics["residual_speed_mm_s"] == pytest.approx((20.0 + 250.0 + 25.0) / 3)
    for value in cell.metrics.values():
        assert value < 999999.0  # 見張り値が一切混入していないこと


def test_cell_aggregation_with_stricter_threshold_yields_not_catchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同じ 2/3 の成立割合でも、閾値が 0.9 のように厳しければ不成立になること。"""
    scripted = {
        (0, 0): _outcome(catchable=True),
        (0, 1): _outcome(catchable=False),
        (0, 2): _outcome(catchable=True),
    }

    def fake_evaluate_throw(
        params: object, rng: Random, record_id: str, keep_record: bool, extra: dict
    ) -> ScenarioOutcome:
        sim = extra["sim"]
        return scripted[(sim["cell_index"], sim["trial_index"])]

    monkeypatch.setattr(evaluate, "evaluate_throw", fake_evaluate_throw)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=3,
        catch_ratio_threshold=0.9,
    )

    result = run_sweep(spec, _scenario_params())
    cell = result.cells[0]
    assert cell.success_ratio == pytest.approx(2 / 3)
    assert cell.status is CellStatus.NOT_CATCHABLE


def test_cell_aggregation_when_all_trials_not_evaluated(monkeypatch: pytest.MonkeyPatch) -> None:
    """全試行が評価対象外の格子点は `NOT_EVALUATED` となり、`success_ratio`
    は `None`、`metrics` は空になること。格子点の `not_evaluated_reason`
    は最初の試行(trial_index=0)の理由を採用する（design.md 未規定の点に
    ついての本モジュールの選択。モジュール docstring 参照）。
    """
    scripted = {
        (0, 0): _outcome(
            catchable=None, not_evaluated_reason=NotEvaluatedReason.NO_FLOOR_CROSSING
        ),
        (0, 1): _outcome(catchable=None, not_evaluated_reason=NotEvaluatedReason.NO_SAMPLES),
    }

    def fake_evaluate_throw(
        params: object, rng: Random, record_id: str, keep_record: bool, extra: dict
    ) -> ScenarioOutcome:
        sim = extra["sim"]
        return scripted[(sim["cell_index"], sim["trial_index"])]

    monkeypatch.setattr(evaluate, "evaluate_throw", fake_evaluate_throw)

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=2,
        catch_ratio_threshold=0.5,
    )

    result = run_sweep(spec, _scenario_params())
    cell = result.cells[0]

    assert cell.status is CellStatus.NOT_EVALUATED
    assert cell.evaluated_trials == 0
    assert cell.success_count == 0
    assert cell.success_ratio is None
    assert dict(cell.metrics) == {}
    assert cell.not_evaluated_reason is NotEvaluatedReason.NO_FLOOR_CROSSING  # 最初の試行の理由


def test_single_trial_cell_status_is_direct_pass_through_without_threshold() -> None:
    """`trials_per_cell == 1` のときは閾値を介さず、その1試行の `catchable`
    をそのまま格子点の成否とする。
    """
    def make_fake(outcome: ScenarioOutcome):
        def fake_evaluate_throw(
            params: object, rng: Random, record_id: str, keep_record: bool, extra: dict
        ) -> ScenarioOutcome:
            return outcome

        return fake_evaluate_throw

    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(1000.0,)),),
        trials_per_cell=1,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(evaluate, "evaluate_throw", make_fake(_outcome(catchable=True)))
        result_true = run_sweep(spec, _scenario_params())
    assert result_true.cells[0].status is CellStatus.CATCHABLE

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(evaluate, "evaluate_throw", make_fake(_outcome(catchable=False)))
        result_false = run_sweep(spec, _scenario_params())
    assert result_false.cells[0].status is CellStatus.NOT_CATCHABLE


# ---------------------------------------------------------------------------
# 完了条件7: SweepKind.REACHABILITY の格子点が直接呼び出しと一致する
# ---------------------------------------------------------------------------


def test_reachability_sweep_matches_direct_evaluate_reachability_calls() -> None:
    params = _scenario_params(drivetrain_params=_drivetrain())

    hold_time_values = (200.0, 2000.0)
    required_distance_values = (100.0, 5000.0)
    spec = SweepSpec(
        kind=SweepKind.REACHABILITY,
        axes=(
            AxisSpec(name="hold_time_ms", unit="ms", values=hold_time_values),
            AxisSpec(name="required_distance_mm", unit="mm", values=required_distance_values),
        ),
        trials_per_cell=1,
    )

    result = run_sweep(spec, params)

    assert len(result.cells) == len(hold_time_values) * len(required_distance_values)
    for cell in result.cells:
        hold_time_ms, required_distance_mm = cell.axis_values
        expected = evaluate.evaluate_reachability(hold_time_ms, required_distance_mm, params)
        assert cell.status is (
            CellStatus.CATCHABLE if expected.catchable else CellStatus.NOT_CATCHABLE
        )
        assert cell.evaluated_trials == 1
        assert cell.success_count == (1 if expected.catchable else 0)


# ---------------------------------------------------------------------------
# 完了条件8: representative の選定（cell_index=0, trial_index=0 のみ）
# ---------------------------------------------------------------------------


def test_representative_is_kept_only_for_cell_zero_when_enabled() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3000.0, 4500.0)),),
        trials_per_cell=1,
        keep_representative_record=True,
    )

    result = run_sweep(spec, _scenario_params())

    assert len(result.cells) == 2
    assert result.cells[0].representative is not None
    assert result.cells[0].representative.record is not None
    for cell in result.cells[1:]:
        assert cell.representative is None


def test_representative_is_none_everywhere_when_disabled() -> None:
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3000.0, 4500.0)),),
        trials_per_cell=1,
        keep_representative_record=False,
    )

    result = run_sweep(spec, _scenario_params())

    assert len(result.cells) == 2
    for cell in result.cells:
        assert cell.representative is None
