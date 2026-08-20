"""例外階層と PredictionConfig の検証（要件 9.3 / 10.1 / 10.2 / 10.3 / 10.5）。

design.md「L0-L2 基盤層 / Errors」および「Error Handling / Error Strategy」が
定める失敗の 3 カテゴリ区分を、型の関係として固定する。

- 予測が構造的に成立しない場合は戻り値 `InvalidPrediction` で表し、例外にしない（要件 6.7）
- 呼び出し方の誤り（設定不正）は `PredictionConfigError`（要件 10.3）
- 記録の不整合は `RecordSchemaError` / `RecordSerializationError`（要件 9.3）

本ファイルはタスク 1.3（例外階層）とタスク 1.5（PredictionConfig の構築時検証）が
共有する。後続タスクは末尾に新しい節を追加する形で拡張し、
既存の節を組み替えないこと。
"""

from __future__ import annotations

import inspect
import math
from dataclasses import FrozenInstanceError

import pytest

from prediction_core.config import PredictionConfig
from prediction_core.errors import (
    PredictionConfigError,
    PredictionCoreError,
    RecordSchemaError,
    RecordSerializationError,
)
from prediction_core.units import mm_per_s2_to_mm_per_ms2

# ---------------------------------------------------------------------------
# 例外階層（タスク 1.3 / 要件 9.3・10.3）
# ---------------------------------------------------------------------------

CONCRETE_ERRORS = (
    PredictionConfigError,
    RecordSchemaError,
    RecordSerializationError,
)

DECLARED_ERROR_NAMES = {
    "PredictionCoreError",
    "PredictionConfigError",
    "RecordSchemaError",
    "RecordSerializationError",
}


def _error_id(error_type: type[BaseException]) -> str:
    return error_type.__name__


class TestErrorHierarchy:
    """`prediction_core.errors` の例外階層（design.md「Errors」）。"""

    def test_base_error_is_a_plain_exception(self) -> None:
        """基底例外は素の Exception であり、ValueError ではない。

        基底例外はパッケージ全体を一括捕捉するための入口である。
        これ自体を ValueError にすると、`except ValueError` が
        パッケージ由来でない値エラーまで巻き込む設計になってしまう。
        """
        assert issubclass(PredictionCoreError, Exception)
        assert not issubclass(PredictionCoreError, ValueError)

    @pytest.mark.parametrize("error_type", CONCRETE_ERRORS, ids=_error_id)
    def test_concrete_error_derives_from_base_and_value_error(
        self, error_type: type[BaseException]
    ) -> None:
        """3 系統の例外はいずれも基底例外と ValueError の両方を継承する。

        ValueError を併せ持つのは、利用側が本パッケージを知らないまま
        `except ValueError` で書いた既存の防御が働くようにするため。
        """
        assert issubclass(error_type, PredictionCoreError)
        assert issubclass(error_type, ValueError)

    @pytest.mark.parametrize("error_type", CONCRETE_ERRORS, ids=_error_id)
    def test_raised_error_is_catchable_as_base_and_as_value_error(
        self, error_type: type[BaseException]
    ) -> None:
        """送出された例外が基底例外としても ValueError としても捕捉できる。"""
        with pytest.raises(PredictionCoreError):
            raise error_type("min_samples=2 は 3 未満である")

        with pytest.raises(ValueError):
            raise error_type("min_samples=2 は 3 未満である")

        with pytest.raises(error_type, match="min_samples=2"):
            raise error_type("min_samples=2 は 3 未満である")

    def test_three_categories_are_mutually_independent(self) -> None:
        """設定不正・スキーマ不整合・直列化不能は互いに捕捉し合わない。

        いずれかが他の基底になっていると、
        design.md「Error Strategy」の 3 カテゴリ区分が崩れ、
        利用側が `except RecordSchemaError` で設定不正まで拾ってしまう。
        """
        for error_type in CONCRETE_ERRORS:
            for other in CONCRETE_ERRORS:
                if other is error_type:
                    continue
                assert not issubclass(error_type, other)

    def test_module_defines_exactly_the_declared_exceptions(self) -> None:
        """例外は宣言された 4 種のみであり、予測無効用の例外を持たない。

        要件 6.7 は「予測無効を値で返す」ことを求めており、
        無効理由に対応する例外型が存在しないこと自体が契約である。
        """
        from prediction_core import errors as errors_module

        defined = {
            name
            for name, obj in vars(errors_module).items()
            if inspect.isclass(obj)
            and issubclass(obj, BaseException)
            and obj.__module__ == errors_module.__name__
        }

        assert defined == DECLARED_ERROR_NAMES

    def test_errors_module_is_layer_zero(self) -> None:
        """errors は L0 層であり、パッケージ内の他モジュールを import しない。

        design.md「Dependency Direction」は L0 の `units` / `errors` が
        互いに import しないことを定める。
        """
        from prediction_core import errors as errors_module

        source = inspect.getsource(errors_module)
        import_lines = [
            line.strip()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]

        assert not [line for line in import_lines if "prediction_core" in line]


# ---------------------------------------------------------------------------
# PredictionConfig の構築時検証（タスク 1.5 / 要件 8.3・10.1・10.2・10.3・10.5）
# ---------------------------------------------------------------------------


class TestPredictionConfigDefaults:
    """既定値が導出根拠どおりに固定されていること（要件 10.2 / 10.5）。

    design.md の PredictionConfig パラメータ表が既定値の根拠を定めている。
    値そのものをここで固定し、根拠のない変更が気づかれずに紛れ込むことを防ぐ。
    """

    def test_default_values_match_derivation_rationale(self) -> None:
        config = PredictionConfig()

        assert config.gravity_mm_s2 == 9806.65
        assert config.min_samples == 3
        assert config.measure_elapsed is True
        assert config.time_degeneracy_rel_tol == 1.4901161193847656e-08

    def test_gravity_mm_ms2_delegates_to_units_conversion(self) -> None:
        """mm/ms^2 への換算は `units.mm_per_s2_to_mm_per_ms2` 一箇所にのみ存在する。

        ここで手製の換算式（例: `/ 1e6` の直書き）を実装すると、換算係数が
        `units.py` と `config.py` の2箇所に分裂し、要件10.5の
        「導出根拠のない固定値を埋め込まない」という方針が崩れる。
        """
        config = PredictionConfig(gravity_mm_s2=12345.0)

        assert config.gravity_mm_ms2 == mm_per_s2_to_mm_per_ms2(12345.0)


class TestPredictionConfigValidation:
    """不正な設定値が構築時に `PredictionConfigError` で拒否されること（要件10.3）。"""

    @pytest.mark.parametrize("min_samples", [0, 1, 2])
    def test_min_samples_below_three_is_rejected(self, min_samples: int) -> None:
        """2軸の未知数2個に対し n=2 は残差自由度0の厳密解になる（要件10.2）。

        メッセージにフィールド名と拒否された値の両方が含まれることも
        あわせて確認する。値が含まれないと、複数設定を扱う利用側が
        ログからどの値が拒否されたのか特定できない。
        """
        with pytest.raises(
            PredictionConfigError, match=rf"min_samples.*{min_samples}"
        ):
            PredictionConfig(min_samples=min_samples)

    def test_min_samples_at_lower_bound_is_accepted(self) -> None:
        config = PredictionConfig(min_samples=3)

        assert config.min_samples == 3

    @pytest.mark.parametrize("gravity_mm_s2", [0.0, -9806.65])
    def test_non_positive_gravity_is_rejected(self, gravity_mm_s2: float) -> None:
        with pytest.raises(PredictionConfigError, match="gravity_mm_s2"):
            PredictionConfig(gravity_mm_s2=gravity_mm_s2)

    @pytest.mark.parametrize("gravity_mm_s2", [math.nan, math.inf, -math.inf])
    def test_non_finite_gravity_is_rejected(self, gravity_mm_s2: float) -> None:
        with pytest.raises(PredictionConfigError, match="gravity_mm_s2"):
            PredictionConfig(gravity_mm_s2=gravity_mm_s2)

    @pytest.mark.parametrize("rel_tol", [0.0, 1.0, -0.1, 1.1])
    def test_time_degeneracy_rel_tol_outside_open_interval_is_rejected(
        self, rel_tol: float
    ) -> None:
        with pytest.raises(PredictionConfigError, match="time_degeneracy_rel_tol"):
            PredictionConfig(time_degeneracy_rel_tol=rel_tol)

    @pytest.mark.parametrize("rel_tol", [math.nan, math.inf])
    def test_non_finite_time_degeneracy_rel_tol_is_rejected(
        self, rel_tol: float
    ) -> None:
        with pytest.raises(PredictionConfigError, match="time_degeneracy_rel_tol"):
            PredictionConfig(time_degeneracy_rel_tol=rel_tol)


class TestPredictionConfigImmutabilityAndEquality:
    """予測結果へそのまま同梱できるための不変性・値等価性。

    design.md「PredictionConfig」Responsibilities & Constraints は、
    `frozen=True` と値等価性を予測結果への同梱の前提として要求している。
    """

    def test_config_is_frozen(self) -> None:
        config = PredictionConfig()

        with pytest.raises(FrozenInstanceError):
            config.min_samples = 5  # type: ignore[misc]

    def test_configs_with_equal_fields_are_value_equal(self) -> None:
        assert PredictionConfig() == PredictionConfig()

    def test_configs_with_different_fields_are_not_equal(self) -> None:
        assert PredictionConfig(min_samples=3) != PredictionConfig(min_samples=4)
