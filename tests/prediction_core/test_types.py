"""値オブジェクトと予測結果の直和型の検証（要件 1.1 / 3.3 / 4.3 / 5.3 / 6.6 / 6.7 / 10.4 / 10.6）。

design.md「L0-L2 基盤層 / CoreTypes」の State Management が定める型の公開面を固定する。
特に次の 2 点を型レベルで担保する。

- 要件 6.7: `InvalidPrediction` は落下地点・落下時刻のフィールドを**持たない**。
  無効を正常な予測値として読み出す経路が型として存在しないことを検証する。
- 要件 1.3 / 1.4: `SourceKind` は Throw Record のメタ情報専用であり、
  予測経路の値オブジェクトのフィールドには現れない。

`PredictionConfig` は L2（`config`）に属し `types` より上層にあるため、
本テストは `PredictionConfig` を import しない。`config` フィールドには
代役（`_ConfigStub`）を与える。`types` が `config` を実行時 import しないこと
（design.md「Dependency Direction」）も併せて検証する。
"""

from __future__ import annotations

import dataclasses
import importlib
import subprocess
import sys
import typing
from pathlib import Path

import pytest

from prediction_core.types import (
    InvalidPrediction,
    InvalidReason,
    Prediction,
    PredictionOutcome,
    Sample,
    SourceKind,
    TrajectoryParameters,
)

# 要件 6.7 が `InvalidPrediction` から排除することを求めるフィールド名。
HIT_FIELD_NAMES = (
    "predicted_hit_x_mm",
    "predicted_hit_y_mm",
    "predicted_hit_time_ms",
    "remaining_time_ms",
)


@dataclasses.dataclass(frozen=True, slots=True)
class _ConfigStub:
    """`PredictionConfig` の代役。

    `types` は L1 層であり、L2 の `config` を実行時に import しない。
    値オブジェクト自体は `config` を検証しないため（design.md CoreTypes
    「Implementation Notes / Validation」）、任意のオブジェクトを保持できる。
    """

    min_samples: int = 3


def _trajectory() -> TrajectoryParameters:
    return TrajectoryParameters(
        t_ref_ms=100.0,
        x0_mm=10.0,
        y0_mm=20.0,
        z0_mm=1500.0,
        estimated_vx_mm_s=1000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=2000.0,
        gravity_mm_s2=9806.65,
    )


def _prediction() -> Prediction:
    return Prediction(
        predicted_hit_x_mm=350.0,
        predicted_hit_y_mm=-120.0,
        predicted_hit_time_ms=780.0,
        remaining_time_ms=680.0,
        estimated_vx_mm_s=1000.0,
        estimated_vy_mm_s=-500.0,
        estimated_vz_mm_s=2000.0,
        residual=0.25,
        trajectory=_trajectory(),
        sample_count=3,
        based_on_time_ms=100.0,
        elapsed_ms=0.4,
        config=_ConfigStub(),
    )


def _invalid_prediction() -> InvalidPrediction:
    return InvalidPrediction(
        reason=InvalidReason.INSUFFICIENT_SAMPLES,
        detail="有効サンプルが 2 点しかない",
        sample_count=2,
        based_on_time_ms=100.0,
        elapsed_ms=None,
        config=_ConfigStub(),
    )


def _all_instances() -> tuple[object, ...]:
    return (
        Sample(t_ms=0.0, x_mm=1.0, y_mm=2.0, z_mm=3.0),
        _trajectory(),
        _prediction(),
        _invalid_prediction(),
    )


# design.md CoreTypes の State Management が宣言するフィールド定義そのもの。
# 名前・宣言順・型注釈の 3 点を契約として固定する。
FIELD_CONTRACT: dict[type, dict[str, str]] = {
    Sample: {
        "t_ms": "float",
        "x_mm": "float",
        "y_mm": "float",
        "z_mm": "float",
    },
    TrajectoryParameters: {
        "t_ref_ms": "float",
        "x0_mm": "float",
        "y0_mm": "float",
        "z0_mm": "float",
        "estimated_vx_mm_s": "float",
        "estimated_vy_mm_s": "float",
        "estimated_vz_mm_s": "float",
        "gravity_mm_s2": "float",
    },
    Prediction: {
        "predicted_hit_x_mm": "float",
        "predicted_hit_y_mm": "float",
        "predicted_hit_time_ms": "float",
        "remaining_time_ms": "float",
        "estimated_vx_mm_s": "float",
        "estimated_vy_mm_s": "float",
        "estimated_vz_mm_s": "float",
        "residual": "float",
        "trajectory": "TrajectoryParameters",
        "sample_count": "int",
        "based_on_time_ms": "float",
        "elapsed_ms": "float | None",
        "config": "PredictionConfig",
    },
    InvalidPrediction: {
        "reason": "InvalidReason",
        "detail": "str",
        "sample_count": "int",
        "based_on_time_ms": "float | None",
        "elapsed_ms": "float | None",
        "config": "PredictionConfig",
    },
}


@pytest.mark.parametrize("cls", list(FIELD_CONTRACT))
def test_field_names_order_and_annotations_match_design(cls: type) -> None:
    """各型のフィールド名・宣言順・型注釈が design.md の宣言と一致する（要件 3.3 / 4.3 / 5.3 / 10.6）。"""
    expected = FIELD_CONTRACT[cls]
    actual = {field.name: field.type for field in dataclasses.fields(cls)}
    assert actual == expected
    assert tuple(actual) == tuple(expected), "フィールドの宣言順が design.md と異なる"


def test_invalid_prediction_has_no_impact_fields() -> None:
    """無効予測は落下地点・落下時刻を保持しない（要件 6.7）。"""
    invalid_field_names = {field.name for field in dataclasses.fields(InvalidPrediction)}
    invalid = _invalid_prediction()
    for name in HIT_FIELD_NAMES:
        assert name not in invalid_field_names, f"{name} が InvalidPrediction に存在してはならない"
        assert not hasattr(invalid, name), f"{name} が無効予測から読み出せてはならない"
    # 対になる正常値側には同じフィールドが揃っている（要件 3.3）。
    prediction_field_names = {field.name for field in dataclasses.fields(Prediction)}
    assert set(HIT_FIELD_NAMES) <= prediction_field_names


def test_invalid_prediction_carries_reason_and_config() -> None:
    """無効理由が区別可能で、使用設定を追跡できる（要件 6.6 / 10.6）。"""
    config = _ConfigStub()
    invalid = InvalidPrediction(
        reason=InvalidReason.NO_FUTURE_FLOOR_CROSSING,
        detail="最新観測時刻より後に z=0 の交点が無い",
        sample_count=5,
        based_on_time_ms=250.0,
        elapsed_ms=None,
        config=config,
    )
    assert invalid.reason is InvalidReason.NO_FUTURE_FLOOR_CROSSING
    assert invalid.detail
    assert invalid.config is config
    # 基準観測時刻はサンプル皆無の場合に定まらないため None を許す。
    assert dataclasses.replace(invalid, based_on_time_ms=None).based_on_time_ms is None


def test_prediction_tracks_sample_count_reference_time_and_config() -> None:
    """予測結果が何サンプル目・基準観測時刻・使用設定・処理時間を保持する（要件 4.3 / 5.3 / 8.1 / 10.6）。"""
    config = _ConfigStub()
    prediction = dataclasses.replace(_prediction(), config=config)
    assert prediction.sample_count == 3
    assert prediction.based_on_time_ms == 100.0
    assert prediction.config is config
    assert prediction.elapsed_ms == 0.4
    # 計測無効時は None を保持できる（要件 8.3）。
    assert dataclasses.replace(prediction, elapsed_ms=None).elapsed_ms is None


@pytest.mark.parametrize("instance", _all_instances())
def test_instances_are_frozen(instance: object) -> None:
    """全型が不変であり、生成後に値を書き換えられない。"""
    first_field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, first_field_name, 0.0)


@pytest.mark.parametrize("instance", _all_instances())
def test_instances_use_slots(instance: object) -> None:
    """全型が slots を持ち、宣言済みフィールド以外の属性を保持できない。"""
    assert not hasattr(instance, "__dict__")
    assert type(instance).__slots__ == tuple(
        field.name for field in dataclasses.fields(instance)
    )


@pytest.mark.parametrize("instance", _all_instances())
def test_instances_have_value_equality(instance: object) -> None:
    """値オブジェクトとして値等価性を持つ（design.md Data Models / Domain Model）。"""
    same = dataclasses.replace(instance)
    assert same is not instance
    assert same == instance


def test_invalid_reason_has_exactly_the_five_defined_members() -> None:
    """無効理由は要件 6.1-6.5 に対応する 5 種であり、文字列として区別できる（要件 6.6）。"""
    assert issubclass(InvalidReason, str)
    assert {member.name: member.value for member in InvalidReason} == {
        "INSUFFICIENT_SAMPLES": "insufficient_samples",
        "DEGENERATE_TIME": "degenerate_time",
        "NO_FUTURE_FLOOR_CROSSING": "no_future_floor_crossing",
        "NON_FINITE_VALUE": "non_finite_value",
        "MALFORMED_INPUT": "malformed_input",
    }
    assert InvalidReason.DEGENERATE_TIME == "degenerate_time"


def test_source_kind_has_the_three_record_source_members() -> None:
    """ソース種別は live / recorded / simulated の 3 種（要件 9.2）。"""
    assert issubclass(SourceKind, str)
    assert {member.name: member.value for member in SourceKind} == {
        "LIVE": "live",
        "RECORDED": "recorded",
        "SIMULATED": "simulated",
    }


def test_source_kind_does_not_appear_in_prediction_value_objects() -> None:
    """ソース種別は記録用メタ情報専用で、予測経路の値には現れない（要件 1.3 / 1.4）。"""
    for cls in FIELD_CONTRACT:
        annotations = [field.type for field in dataclasses.fields(cls)]
        assert "SourceKind" not in annotations, f"{cls.__name__} が SourceKind に依存している"


def test_prediction_outcome_is_the_sum_of_both_results() -> None:
    """予測結果は正常値と無効値の直和型である（要件 6.7）。"""
    assert set(typing.get_args(PredictionOutcome)) == {Prediction, InvalidPrediction}
    assert isinstance(_prediction(), PredictionOutcome)
    assert isinstance(_invalid_prediction(), PredictionOutcome)


def test_residual_documents_millimetre_unit() -> None:
    """`residual` は名前に単位を持たないため、mm である旨を docstring で示す（要件 10.4）。"""
    doc = Prediction.__doc__ or ""
    # `Attributes:` 節の residual の項目を取り出す。他フィールド名（*_mm）が
    # 紛れ込まない行単位で照合し、単位の明記そのものを検証する。
    residual_entries = [
        line.strip() for line in doc.splitlines() if line.strip().startswith("residual:")
    ]
    assert len(residual_entries) == 1, "Prediction の docstring に residual の説明が無い"
    assert "mm" in residual_entries[0], f"residual の単位 mm が未記載: {residual_entries[0]}"


def test_importing_types_does_not_import_config_at_runtime() -> None:
    """`types` は上層の `config` を実行時 import しない（design.md「Dependency Direction」）。

    タスク 5.1（PublicApi）以降、`prediction_core/__init__.py` は公開 API の
    再エクスポートのため `config` を含む全内部モジュールを import する。その
    ため `import prediction_core.types` は Python の import 機構上、親パッケージ
    `prediction_core` の `__init__.py` を経由し、必然的に `prediction_core.config`
    を `sys.modules` に載せる。「`prediction_core.config` が `sys.modules` に
    現れるか」という間接的なプローブは、もはや `types.py` **自身**の実行時
    import の有無を判別できない。

    本テストが固定したい不変条件はあくまで「`types.py` というモジュール自身が
    `config` を実行時 import しない」ことである（`PredictionConfig` への参照は
    `TYPE_CHECKING` ガード内に限る、モジュール docstring 参照）。そこで
    `types.py` を**パッケージ機構を経由せず**単体ファイルとして
    `importlib.util.spec_from_file_location` で直接ロードし
    （`prediction_core/__init__.py` を実行しない）、その過程で
    `prediction_core.config` が import されないことを確認する。
    """
    types_path = Path(importlib.import_module("prediction_core.types").__file__).resolve()
    probe = (
        "import sys, importlib.util; "
        "spec = importlib.util.spec_from_file_location("
        f"'_types_standalone_probe', {str(types_path)!r}); "
        "module = importlib.util.module_from_spec(spec); "
        "sys.modules[spec.name] = module; "
        "spec.loader.exec_module(module); "
        "sys.exit(1 if 'prediction_core.config' in sys.modules else 0)"
    )
    result = subprocess.run([sys.executable, "-c", probe], check=False)
    assert result.returncode == 0, "types の import が config を巻き込んでいる（上向き依存）"
