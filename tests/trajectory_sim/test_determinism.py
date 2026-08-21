"""決定性・評価順序非依存・Replay 再現の受け入れテスト（要件8.2, 8.3, 8.4,
8.6 / `.kiro/specs/trajectory-simulator/tasks.md` タスク5.2）。

タスク3.2（`tests/trajectory_sim/test_trajectory_sim_sweep.py`）・タスク4.2
（`tests/trajectory_sim/test_trajectory_sim_serialize.py`）はすでに単体
レベルで決定性・評価順序非依存・バイト単位一致の各性質を固定している。
本ファイルはそれらと同じ保証を、実際の公開パイプライン全体
（`sweep.run_sweep` → `serialize.write_sweep_result`）を通した受け入れ
テストのレベルへ引き上げる。あわせて、他のどのテストファイルでも検証
されていない新規の保証（要件8.6: 出力された Throw Record を
`prediction_core` の Replay に入力した場合、記録された予測系列と一致する
結果が再現されること）を追加する。

- 完了条件1（要件8.2）: 同一設定・同一種で2回実行した掃引結果をそれぞれ
  ファイルへ書き出すと、バイト単位で一致すること
- 完了条件2（要件8.3）: 軸の並び順（走査順序）を入れ替えても、同じ論理的
  な軸値の組み合わせに対する格子点の結果が変わらないこと
- 完了条件3（要件8.6）: 出力に埋め込まれた Throw Record を
  `prediction_core.replay` へ通すと、記録された予測系列
  （`ThrowRecord.predictions`）と等価な結果が再現されること
- 完了条件4（要件8.4）: 予測経路に決定性を弱める要素が入っていないことの
  機械的な裏付けとして、`pyproject.toml` の `[project] dependencies` が
  空であることを検証する

本ファイルは新規テストのみを追加し、`src/trajectory_sim/*.py` /
`src/prediction_core/*.py` のいずれも変更しない。

ファイル名は `tests/trajectory_sim/test_determinism.py`。`tests/
prediction_core/` 側には同名ファイルが存在しない
（`tests/prediction_core/test_replay.py` はあるが `test_determinism.py`
ではない）ため、basename の衝突はない。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from prediction_core import PredictionConfig, predictions_equivalent, replay

from trajectory_sim.params import (
    CatchCriteria,
    DrivetrainParams,
    LayoutParams,
    ObservationParams,
    ScenarioParams,
    ThrowDispersion,
    ThrowParams,
)
from trajectory_sim.serialize import write_sweep_result
from trajectory_sim.sweep import AxisSpec, SweepKind, SweepSpec, run_sweep

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


# ---------------------------------------------------------------------------
# 共通フィクスチャ（test_trajectory_sim_sweep.py と同じ構成方針だが、本
# ファイルは独立したテストファイルであるため、他ファイルからは import
# せず、ここで自己完結させる）。
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
    prediction: PredictionConfig | None = None,
) -> ScenarioParams:
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


# ---------------------------------------------------------------------------
# 完了条件1（要件8.2）: 同一設定・同一種の掃引を2回実行し、それぞれを
# 書き出したファイルがバイト単位で一致する（E2E: run_sweep → write_sweep_result）。
# ---------------------------------------------------------------------------


def test_byte_identical_file_output_across_independently_computed_sweeps(tmp_path: Path) -> None:
    """同一の `SweepSpec` / `base_params` で `run_sweep` を独立に2回実行し、
    それぞれの `SweepResult` を別ファイルへ書き出すと、バイト単位で完全に
    一致すること（要件8.2）。

    `run_sweep` を2回“独立に”実行することで、シリアライザだけでなく評価
    パイプライン全体（乱数の種の導出・観測ノイズ・予測・運動・キャッチ
    判定・集計）の決定性も併せて証明する。

    `PredictionConfig(measure_elapsed=False)` を用いて、`elapsed_ms`
    計測ノイズによる非決定性（task 2.5/3.2/4.2 で判明済みの既知の落とし
    穴）を避ける。
    """
    axis_speed = AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3500.0, 4500.0))
    axis_drivetrain = AxisSpec(
        name="drivetrain.max_speed_mm_s", unit="mm/s", values=(5000.0, 6000.0)
    )
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(axis_speed, axis_drivetrain),
        trials_per_cell=2,
        seed=123,
        catch_ratio_threshold=0.5,
        keep_representative_record=True,
    )
    base_params = _scenario_params(
        dispersion=ThrowDispersion(
            speed_sigma_mm_s=50.0, elevation_sigma_deg=1.0, azimuth_sigma_deg=1.0
        ),
        observation_params=_observation_params(sigma_x_mm=5.0, sigma_y_mm=5.0, dropout_ratio=0.1),
        prediction=PredictionConfig(measure_elapsed=False),
    )

    # 4格子点 x 2試行 = 8シナリオ評価。テスト速度を保ちつつ、多試行の集計
    # 経路も併せて検証する。
    result_a = run_sweep(spec, base_params)
    result_b = run_sweep(spec, base_params)

    path_a = tmp_path / "run_a.json"
    path_b = tmp_path / "run_b.json"
    write_sweep_result(result_a, path_a)
    write_sweep_result(result_b, path_b)

    assert path_a.read_bytes() == path_b.read_bytes()


# ---------------------------------------------------------------------------
# 完了条件2（要件8.3）: 軸の並び順（走査順序）を入れ替えても格子点の結果
# が変わらない（E2E: 論理的な軸値の組み合わせで突き合わせる）。
# ---------------------------------------------------------------------------


def test_cell_results_are_independent_of_axis_evaluation_order() -> None:
    """軸の並び順を入れ替えた2つの `SweepSpec`（したがって走査順序、
    `cell_index` の割り当て、`derive_seed` の導出値も変わる）で掃引しても、
    同じ論理的な軸値の組み合わせに対する `CellResult` の内容が一致する
    こと（要件8.3）。

    誤差要因ゼロ（既定の `ThrowDispersion` / 観測ノイズ）の構成を用いる。
    これにより、走査順序が変わっても論理的に同じパラメータの組み合わせ
    は常に同じ `ScenarioOutcome` を生み、`cell_index` が変わったことに
    起因する乱数列の違いが実際の結果に影響しないことを保証できる。
    """
    axis_speed = AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(3800.0, 4600.0))
    axis_drivetrain = AxisSpec(
        name="drivetrain.max_speed_mm_s", unit="mm/s", values=(4500.0, 7000.0)
    )

    spec_forward = SweepSpec(
        kind=SweepKind.THROW,
        axes=(axis_speed, axis_drivetrain),
        trials_per_cell=1,
        seed=99,
    )
    # 軸の並び順を反転し、かつ一方の軸の値そのものの列も反転する。
    reversed_drivetrain = AxisSpec(
        name="drivetrain.max_speed_mm_s",
        unit="mm/s",
        values=tuple(reversed(axis_drivetrain.values)),
    )
    spec_reversed = SweepSpec(
        kind=SweepKind.THROW,
        axes=(reversed_drivetrain, axis_speed),
        trials_per_cell=1,
        seed=99,
    )

    base_params = _scenario_params()  # 既定のばらつき・観測ノイズはすべてゼロ

    result_forward = run_sweep(spec_forward, base_params)
    result_reversed = run_sweep(spec_reversed, base_params)

    def canonical_map(
        spec: SweepSpec, cells: tuple
    ) -> dict[frozenset[tuple[str, object]], object]:
        names = [axis.name for axis in spec.axes]
        return {
            frozenset(zip(names, cell.axis_values)): cell
            for cell in cells
        }

    forward_by_combo = canonical_map(spec_forward, result_forward.cells)
    reversed_by_combo = canonical_map(spec_reversed, result_reversed.cells)

    assert set(forward_by_combo.keys()) == set(reversed_by_combo.keys())
    assert len(forward_by_combo) == 4  # 2x2 格子、全組み合わせが揃っていること

    for combo_key, fwd_cell in forward_by_combo.items():
        rev_cell = reversed_by_combo[combo_key]
        assert fwd_cell.status == rev_cell.status
        assert fwd_cell.success_ratio == rev_cell.success_ratio
        assert dict(fwd_cell.metrics) == dict(rev_cell.metrics)
        assert fwd_cell.not_evaluated_reason == rev_cell.not_evaluated_reason


# ---------------------------------------------------------------------------
# 完了条件3（要件8.6, 新規）: 出力に埋め込まれた Throw Record を
# prediction_core.replay へ通すと、記録された予測系列と一致する。
# ---------------------------------------------------------------------------


def test_embedded_throw_record_replays_to_the_same_prediction_sequence() -> None:
    """`SweepResult` の代表シナリオが保持する `ThrowRecord` を
    `prediction_core.replay` へ入力すると、記録時にライブで逐次生成された
    予測系列（`record.predictions`）と等価な予測系列が再現されること
    （要件8.6）。

    `replay` は `record.config` と `record.samples` のみから、
    `ThrowPredictionTracker`（逐次蓄積器）に一切依存せず予測系列を
    ゼロから再構成する（`src/prediction_core/record.py` の `replay` 自身の
    契約）。これが記録時の系列と一致することは、記録時の逐次評価に
    隠れた状態・順序依存が紛れ込んでいないことの直接的な証拠になる。

    `predictions_equivalent` は `elapsed_ms`（処理時間の実測値であり
    呼び出しごとに変動しうる）を比較から除外して残り全フィールドを
    比較するため、既定の `PredictionConfig(measure_elapsed=True)` を
    そのまま使ってよい（`measure_elapsed=False` は本チェックの必須要件
    ではない）。
    """
    spec = SweepSpec(
        kind=SweepKind.THROW,
        axes=(AxisSpec(name="throw.speed_mm_s", unit="mm/s", values=(4000.0,)),),
        trials_per_cell=1,
        keep_representative_record=True,
    )
    # 誤差要因ゼロの構成: cell_index=0, trial_index=0 の1試行が必ず評価
    # 対象になり、代表シナリオとして record を確実に保持する
    # （task 3.2 の既定 `keep_representative_record` 選定規則: cell_index=0
    # にのみ representative が保持される）。
    base_params = _scenario_params()

    result = run_sweep(spec, base_params)

    assert len(result.cells) == 1
    representative = result.cells[0].representative
    assert representative is not None, "cell_index=0 の representative が保持されていない"
    record = representative.record
    assert record is not None, "representative.record が None (ThrowRecord が保持されていない)"
    assert len(record.predictions) > 0  # 空の予測系列で自明に真になる退化ケースを避ける

    replayed = replay(record)

    assert predictions_equivalent(replayed, record.predictions)


# ---------------------------------------------------------------------------
# 完了条件4（要件8.4）: 予測経路に決定性を弱める要素（サードパーティ
# 実行時依存）が入っていないことの機械的な裏付け。
# ---------------------------------------------------------------------------


def test_pyproject_declares_zero_third_party_runtime_dependencies() -> None:
    """`pyproject.toml` の `[project] dependencies` が空であること
    （要件8.4 が依拠する具体的・機械的な保証）。

    `prediction_core` 自身の決定性は「標準ライブラリのみに依存し、
    サードパーティの数値ライブラリ（BLAS 実装等によりプラットフォーム
    依存の非決定性を持ちうる）を一切持ち込まない」ことに支えられている。
    この検査は `tests/prediction_core/test_packaging.py` および
    `tests/trajectory_sim/test_trajectory_sim_packaging.py` にも既に
    存在する回帰ガードだが、本ファイルの決定性の物語（バイト単位一致・
    評価順序非依存・Replay 一致）と同じ場所に再掲することで、要件8.4が
    依拠する土台を自己完結的に示す。
    """
    with PYPROJECT_PATH.open("rb") as fp:
        data = tomllib.load(fp)

    assert data["project"]["dependencies"] == []
