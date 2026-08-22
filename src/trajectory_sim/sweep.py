"""掃引エンジン（design.md「SweepEngine」/ 要件 6.1, 6.4, 6.6, 6.7, 8.1, 8.3）。

`SweepSpec` が定義する軸（`AxisSpec` の列）の直積を行優先順で走査し、格子点
ごとに `trajectory_sim.evaluate` の評価器（`evaluate_throw` /
`evaluate_reachability`）を反復し、成立数・評価件数・成立割合・指標の平均
を集計して `SweepResult` にまとめる（design.md「SweepEngine」
Responsibilities & Constraints）。

**評価を1件も行う前に、軸名・値・閾値の妥当性を一括検証する**
（design.md「SweepEngine」Batch / Job Contract: 「1つでも不正なら1件も
評価しない」）。`run_sweep` は `_validate_spec` を最初に呼び、以下のいずれか
に該当する場合は `trajectory_sim.errors.SweepDefinitionError` を送出し、
グリッド生成・評価のいずれも行わない:

1. 軸が1本もない。
2. いずれかの軸の値が空である。
3. 軸名が重複している。
4. `SweepKind.REACHABILITY` の軸名が `{"hold_time_ms", "required_distance_mm"}`
   と過不足なく一致しない。
5. `SweepKind.THROW` の軸名が `trajectory_sim.params.PARAMETER_PATHS` に
   存在しない。
6. `trials_per_cell < 1`。
7. `trials_per_cell >= 2` かつ `catch_ratio_threshold is None`。
8. `catch_ratio_threshold` が指定されている場合に、それが `[0.0, 1.0]`
   の範囲外または非有限値である。
9. `SweepKind.THROW` のとき、いずれかの軸のいずれかの値が
   `trajectory_sim.params.replace_by_path` に単独で（他の軸の値と
   組み合わせずに）適用できない（enum 値パスへの不正な文字列など）。

設計上の判断（design.md が明示していない点についての本モジュールの選択。
将来のレビューア・後続タスクのために明記する）:

- **(4) について**: design.md 本文は「`SweepKind.REACHABILITY` の軸名は
  `hold_time_ms` / `required_distance_mm` に限る」としか書いておらず、
  片方のみの軸を許すかどうかは明記していない。`evaluate_reachability` は
  両方の値を必須の位置引数として要求し、既定値を持たないため、片方だけを
  軸にした場合に「もう一方の固定値」をどこから得るかが design.md 上
  定義されていない。本モジュールは、値を捏造しない安全側の選択として、
  **両方の軸名が過不足なく存在すること**を要求する。
- **(8) について**: design.md「SweepEngine」の本文には明記がないが、
  `trajectory_sim.params`（例: `ObservationParams.dropout_ratio`）が
  確立した「値に自然な範囲がある場合は範囲検証を行う」という方針を踏襲し、
  `catch_ratio_threshold` が指定される場合はそれが割合として意味を持つ
  `[0.0, 1.0]` の有限値であることも検証する。
- **代表シナリオの選定**: `keep_representative_record=True` のとき、
  `cell_index=0` の格子点の `CellResult.representative` にのみ、その
  格子点の最初の試行（`trial_index=0`）の `ScenarioOutcome` 全体を保持する。
  design.md は「代表シナリオは1つだけ」という単一性以外の選定基準を
  定めていないため、最も単純で完全に決定的な「反復順序の先頭」を選んだ。
  「最初に成立した格子点」のような基準は、格子を固定順序で走査する前提を
  崩す（先読みや2パスが必要になる）ため、本タスクの範囲では採用しない。
- **格子点が全評価対象外のときの `not_evaluated_reason`**: 格子点内の
  複数試行がそれぞれ異なる評価対象外理由を持ちうる場合に、どれを格子点
  代表の理由とするかを design.md は定めていない。本モジュールは、
  最も単純で決定的な選択として、その格子点の**最初の試行**（`trial_index=0`）
  の理由をそのまま格子点の理由として採用する。

`SweepKind.THROW` の各格子点は、`base_params` から
`trajectory_sim.params.replace_by_path` を軸の並び順に逐次適用して構築する。
`replace_by_path` は enum 値パス（`catch.policy` / `calibration_stage`）へ
不正な文字列が渡された場合に素の `ValueError`（enum コンストラクタ由来。
`trajectory_sim.errors.TrajectorySimError` のサブクラスではない）を送出する
既知の挙動があるため（タスク1.5の実装メモ）、本モジュールはこの
非 `TrajectorySimError` な `ValueError` だけを捕捉し
`SweepDefinitionError` へ包み直す。`ParameterError`（パラメータ自身の
不変条件違反）はそのまま伝播させ、誤って握りつぶさない。

この判別ロジック（非 `TrajectorySimError` な `ValueError` だけを
`SweepDefinitionError` へ包み直す）は、`_validate_spec`（上記(9): 評価前の
一括検証、軸の値を単独で試す）と `_build_throw_cell_params`（格子点構築時、
軸の並び順で逐次適用する）の双方で使われる。前者は「1件も評価しない」
という Batch / Job Contract を満たすための事前チェックであり、後者は
実際に格子点のパラメータを構築する処理である。両者は同じ入力に対して
常に同じ判定（合格/`SweepDefinitionError`/`ParameterError` 伝播）をする
必要があるため、判別ロジックを重複させず同一の分岐（`isinstance(exc,
TrajectorySimError)`）を用いる。

`SweepKind.REACHABILITY` は `base_params` を一切変更せず、軸の値
（`hold_time_ms` / `required_distance_mm`）を `evaluate_reachability` へ
直接渡す。`replace_by_path` は呼び出さない。

乱数の種は `derive_seed(seed, cell_index, trial_index)` により、基準種・
格子点番号・試行番号から `hashlib.blake2b` で決定的に導出する（要件8.1,
8.3）。プロセスごとに変わる組み込み `hash()` は使わない
（design.md「SweepEngine」Implementation Notes）。各試行は
`random.Random(derive_seed(...))` という**その試行専用の新しい乱数源**を
使う。格子点や試行をまたいで `Random` インスタンスを共有・再利用しない
ことが、「評価順序に依存しない」という不変条件（要件8.3）の根拠である。
"""

from __future__ import annotations

import hashlib
import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from random import Random
from types import MappingProxyType

from trajectory_sim import evaluate
from trajectory_sim.errors import SweepDefinitionError, TrajectorySimError
from trajectory_sim.params import PARAMETER_PATHS, ScenarioParams, replace_by_path
from trajectory_sim.results import (
    CellResult,
    CellStatus,
    NotEvaluatedReason,
    ScenarioOutcome,
    SweepResult,
)

__all__ = ["SweepKind", "AxisSpec", "SweepSpec", "derive_seed", "run_sweep"]


class SweepKind(StrEnum):
    """掃引の種別（design.md「SweepEngine」Service Interface）。"""

    REACHABILITY = "reachability"
    THROW = "throw"


@dataclass(frozen=True, slots=True)
class AxisSpec:
    """掃引の1軸（design.md「SweepEngine」Service Interface）。

    Attributes:
        name: 軸名。`SweepKind.THROW` では `PARAMETER_PATHS` のキー、
            `SweepKind.REACHABILITY` では `"hold_time_ms"` /
            `"required_distance_mm"` のいずれかでなければならない。
        unit: 軸の単位文字列（表示・出力用。`run_sweep` 自身はこの値を
            検証・使用しない）。
        values: この軸が取る値の列。空であってはならない。
    """

    name: str
    unit: str
    values: tuple[float | str, ...]


@dataclass(frozen=True, slots=True)
class SweepSpec:
    """掃引全体の定義（design.md「SweepEngine」Service Interface）。

    Attributes:
        kind: 掃引の種別。
        axes: 掃引軸の列。空であってはならず、軸名の重複も許さない。
        trials_per_cell: 1格子点あたりの試行回数。1以上でなければならない。
        seed: 基準となる乱数の種（要件8.1: 外部から指定可能）。
        catch_ratio_threshold: 成立割合の閾値。`trials_per_cell >= 2` の
            ときは必須（`None` は拒否される）。指定する場合は
            `[0.0, 1.0]` の有限値でなければならない。
        keep_representative_record: 真のとき、`cell_index=0` の格子点の
            最初の試行の `ScenarioOutcome`（`ThrowRecord` を含みうる）を
            `CellResult.representative` に保持する。
    """

    kind: SweepKind
    axes: tuple[AxisSpec, ...]
    trials_per_cell: int = 1
    seed: int = 0
    catch_ratio_threshold: float | None = None
    keep_representative_record: bool = False


_REACHABILITY_AXIS_NAMES: frozenset[str] = frozenset({"hold_time_ms", "required_distance_mm"})

# 評価対象の試行について平均を取る指標フィールド（design.md「SweepEngine」
# Responsibilities: 「集計は評価対象外を分母から除く」。`ScenarioOutcome`
# のうち、格子点集計として意味を持つ数値フィールドをここに列挙する。
_METRIC_FIELDS: tuple[str, ...] = (
    "position_error_mm",
    "hold_time_ms",
    "required_distance_mm",
    "prediction_error_mm",
    "residual_speed_mm_s",
)


def derive_seed(seed: int, cell_index: int, trial_index: int) -> int:
    """基準種・格子点番号・試行番号から、1試行分の乱数の種を決定的に導出する。

    （design.md「SweepEngine」Implementation Notes / 要件8.1, 8.3）

    3つの整数それぞれを8バイト符号付きビッグエンディアンへ変換して連結し、
    `hashlib.blake2b` でハッシュ化した先頭8バイトを整数化する。プロセス
    ごとに値が変わる組み込み `hash()` は使わない（design.md が明示的に
    禁じている）。同一の3つ組は常に同一の値を返し、異なる3つ組は
    （ハッシュ関数の性質上）ほぼ確実に異なる値を返す。
    """
    packed = (
        seed.to_bytes(8, "big", signed=True)
        + cell_index.to_bytes(8, "big", signed=True)
        + trial_index.to_bytes(8, "big", signed=True)
    )
    digest = hashlib.blake2b(packed).digest()
    return int.from_bytes(digest[:8], "big")


def _validate_spec(spec: SweepSpec, base_params: ScenarioParams) -> None:
    """`spec` の軸名・値・試行回数・閾値の妥当性を一括検証する。

    どれか1つでも不正なら、グリッド生成やいかなる評価も行う前に
    `SweepDefinitionError` を送出する（design.md「SweepEngine」Batch /
    Job Contract）。

    `SweepKind.THROW` の各軸については、軸の値そのもの
    （enum 値パスへの不正な文字列など）も、グリッド走査・評価を1件も
    行う前にここで一括検証する。`replace_by_path` を軸の各値に対して
    単独に（他の軸の値と組み合わせずに）1回ずつ試すことで、格子点の
    組み合わせ数ではなく全軸の値の総数に対して線形のコストで検証できる。
    """
    if len(spec.axes) == 0:
        raise SweepDefinitionError(
            "掃引軸が1本もない: SweepSpec.axes には少なくとも1本の軸が必要である。"
        )

    names = [axis.name for axis in spec.axes]
    if len(set(names)) != len(names):
        raise SweepDefinitionError(f"軸名が重複している: axes の名前一覧={names!r}")

    for axis in spec.axes:
        if len(axis.values) == 0:
            raise SweepDefinitionError(
                f"軸 {axis.name!r} の values が空である: 少なくとも1つの値が必要である。"
            )

    if spec.kind is SweepKind.REACHABILITY:
        name_set = frozenset(names)
        if name_set != _REACHABILITY_AXIS_NAMES:
            raise SweepDefinitionError(
                "SweepKind.REACHABILITY の軸名は "
                f"{sorted(_REACHABILITY_AXIS_NAMES)!r} を過不足なく指定しなければ"
                f"ならない: 指定された軸名={names!r}"
            )
    else:
        for name in names:
            if name not in PARAMETER_PATHS:
                raise SweepDefinitionError(
                    f"未知の軸名: {name!r} は PARAMETER_PATHS に存在しない。"
                    "SweepKind.THROW の軸名には PARAMETER_PATHS のキーのみを"
                    "指定できる。"
                )

    if spec.trials_per_cell < 1:
        raise SweepDefinitionError(
            f"trials_per_cell={spec.trials_per_cell!r} は1以上でなければならない。"
        )

    if spec.trials_per_cell >= 2 and spec.catch_ratio_threshold is None:
        raise SweepDefinitionError(
            "trials_per_cell が2以上のとき catch_ratio_threshold は必須である"
            "（指定されていない）。"
        )

    if spec.catch_ratio_threshold is not None:
        threshold = spec.catch_ratio_threshold
        if not (math.isfinite(threshold) and 0.0 <= threshold <= 1.0):
            raise SweepDefinitionError(
                f"catch_ratio_threshold={threshold!r} は0以上1以下の有限値で"
                "なければならない。"
            )

    if spec.kind is SweepKind.THROW:
        for axis in spec.axes:
            for value in axis.values:
                try:
                    replace_by_path(base_params, axis.name, value)
                except ValueError as exc:
                    if isinstance(exc, TrajectorySimError):
                        raise
                    raise SweepDefinitionError(
                        f"軸 {axis.name!r} の値 {value!r} を適用できない: {exc}"
                    ) from exc


def _build_throw_cell_params(
    base_params: ScenarioParams, axes: tuple[AxisSpec, ...], combination: Sequence[float | str]
) -> ScenarioParams:
    """`base_params` に軸の並び順で `replace_by_path` を逐次適用し、格子点のパラメータを構築する。

    `replace_by_path` が enum 値パスへの不正な文字列に対して送出する
    素の `ValueError`（`TrajectorySimError` のサブクラスではない。
    タスク1.5の実装メモ）だけを `SweepDefinitionError` へ包み直す。
    `ParameterError`（パラメータ自身の不変条件違反）や既存の
    `SweepDefinitionError`（未知パス、本来ここには到達しない）は
    そのまま伝播させ、握りつぶさない。
    """
    cell_params = base_params
    for axis, value in zip(axes, combination):
        try:
            cell_params = replace_by_path(cell_params, axis.name, value)
        except ValueError as exc:
            if isinstance(exc, TrajectorySimError):
                raise
            raise SweepDefinitionError(
                f"軸 {axis.name!r} の値 {value!r} を適用できない: {exc}"
            ) from exc
    return cell_params


def _evaluate_cell_throw(
    spec: SweepSpec,
    base_params: ScenarioParams,
    cell_index: int,
    combination: Sequence[float | str],
) -> tuple[ScenarioOutcome, ...]:
    """`SweepKind.THROW` の1格子点について、全試行を評価する。"""
    cell_params = _build_throw_cell_params(base_params, spec.axes, combination)
    outcomes: list[ScenarioOutcome] = []
    for trial_index in range(spec.trials_per_cell):
        trial_seed = derive_seed(spec.seed, cell_index, trial_index)
        rng = Random(trial_seed)
        record_id = f"sim-{cell_index}-{trial_index}"
        keep_record = (
            spec.keep_representative_record and cell_index == 0 and trial_index == 0
        )
        extra: Mapping[str, object] = {
            "sim": {
                "sim_extra_version": "1.0",
                "cell_index": cell_index,
                "trial_index": trial_index,
            }
        }
        outcomes.append(evaluate.evaluate_throw(cell_params, rng, record_id, keep_record, extra))
    return tuple(outcomes)


def _evaluate_cell_reachability(
    spec: SweepSpec,
    base_params: ScenarioParams,
    combination: Sequence[float | str],
) -> tuple[ScenarioOutcome, ...]:
    """`SweepKind.REACHABILITY` の1格子点について、全試行を評価する。

    `base_params` は一切変更しない。`evaluate_reachability` は乱数を
    使わず決定的であるため、同一格子点内の全試行は同一結果になる。
    """
    values_by_name = dict(zip((axis.name for axis in spec.axes), combination))
    hold_time_ms = values_by_name["hold_time_ms"]
    required_distance_mm = values_by_name["required_distance_mm"]
    outcomes = tuple(
        evaluate.evaluate_reachability(
            hold_time_ms,  # type: ignore[arg-type]
            required_distance_mm,  # type: ignore[arg-type]
            base_params,
        )
        for _ in range(spec.trials_per_cell)
    )
    return outcomes


def _aggregate_cell(
    spec: SweepSpec,
    cell_index: int,
    axis_values: tuple[float | str, ...],
    outcomes: tuple[ScenarioOutcome, ...],
) -> CellResult:
    """1格子点の全試行の `ScenarioOutcome` から `CellResult` を集計する。

    評価対象外の試行は分母（`evaluated_trials`）からも指標平均からも除く
    （design.md「SweepEngine」Responsibilities: 「集計は評価対象外を分母
    から除く」/ 要件6.7）。
    """
    trials = spec.trials_per_cell
    evaluated_outcomes = [o for o in outcomes if o.not_evaluated_reason is None]
    evaluated_trials = len(evaluated_outcomes)
    success_count = sum(1 for o in evaluated_outcomes if o.catchable is True)
    success_ratio = success_count / evaluated_trials if evaluated_trials > 0 else None

    not_evaluated_reason: NotEvaluatedReason | None
    if evaluated_trials == 0:
        status = CellStatus.NOT_EVALUATED
        # 全試行が評価対象外のときの代表理由として、最初の試行(trial_index=0)
        # の理由を採用する(design.md 未規定の点についての本モジュールの選択。
        # モジュール docstring 参照)。
        not_evaluated_reason = outcomes[0].not_evaluated_reason
    elif trials == 1:
        status = (
            CellStatus.CATCHABLE if evaluated_outcomes[0].catchable else CellStatus.NOT_CATCHABLE
        )
        not_evaluated_reason = None
    else:
        assert spec.catch_ratio_threshold is not None  # _validate_spec が保証する
        assert success_ratio is not None
        status = (
            CellStatus.CATCHABLE
            if success_ratio >= spec.catch_ratio_threshold
            else CellStatus.NOT_CATCHABLE
        )
        not_evaluated_reason = None

    metrics: dict[str, float] = {}
    if evaluated_trials > 0:
        for field_name in _METRIC_FIELDS:
            field_values = [
                value
                for outcome in evaluated_outcomes
                if (value := getattr(outcome, field_name)) is not None
            ]
            if field_values:
                metrics[field_name] = sum(field_values) / len(field_values)

    representative: ScenarioOutcome | None = None
    if spec.keep_representative_record and cell_index == 0:
        representative = outcomes[0]

    return CellResult(
        axis_values=axis_values,
        status=status,
        trials=trials,
        evaluated_trials=evaluated_trials,
        success_count=success_count,
        success_ratio=success_ratio,
        metrics=MappingProxyType(metrics),
        not_evaluated_reason=not_evaluated_reason,
        representative=representative,
    )


def run_sweep(spec: SweepSpec, base_params: ScenarioParams) -> SweepResult:
    """`spec` が定義する格子を走査し、全格子点を評価して `SweepResult` にまとめる。

    （design.md「SweepEngine」Batch / Job Contract / 要件6.1, 6.4, 6.6, 6.7,
    8.1, 8.3）

    `spec` の妥当性を最初に一括検証し、1つでも不正なら
    `SweepDefinitionError` を送出して、グリッド生成やいかなる評価も
    行わない。妥当な場合、軸の直積を行優先順（`itertools.product` の
    既定の反復順序。**最後の軸が最も速く変化する**）で走査し、
    `cell_index` はこの反復順序上の0始まりの位置である。
    """
    _validate_spec(spec, base_params)

    axis_value_lists = [axis.values for axis in spec.axes]
    cells: list[CellResult] = []
    for cell_index, combination in enumerate(itertools.product(*axis_value_lists)):
        if spec.kind is SweepKind.THROW:
            outcomes = _evaluate_cell_throw(spec, base_params, cell_index, combination)
        else:
            outcomes = _evaluate_cell_reachability(spec, base_params, combination)
        cells.append(_aggregate_cell(spec, cell_index, tuple(combination), outcomes))

    return SweepResult(spec=spec, base_params=base_params, cells=tuple(cells))
