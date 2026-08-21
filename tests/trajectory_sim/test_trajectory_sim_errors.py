"""呼び出し方の誤りを表す例外階層の検証(タスク 1.3、要件 11.5)。

design.md「Errors」節が定める 4 種の例外
(`TrajectorySimError` / `ParameterError` / `SweepDefinitionError` /
`OutputError`)について、次を固定する。

- `TrajectorySimError` が `ValueError` を継承すること
- 3 つの具象例外がいずれも `TrajectorySimError` のサブクラスであり、
  したがって `ValueError` のサブクラスでもあること
- 各具象例外を送出した際に、基底例外 `TrajectorySimError` としても
  `ValueError` としても捕捉できること(観測可能な完了状態)
- 3 種が互いに独立したクラスであること(取り違えて捕捉できてしまわないこと)

ファイル名は `test_trajectory_sim_errors.py`
(`test_errors.py` ではない)。`tests/trajectory_sim/` 配下の既存テスト
(`test_trajectory_sim_units.py` / `test_trajectory_sim_packaging.py`)が
採用している命名方針(`tests/**` にどのディレクトリにも `__init__.py` が
存在せず、pytest の既定 import-mode ではベース名の衝突が起こり得るため、
`test_trajectory_sim_*` で統一する)を踏襲する。
"""

from __future__ import annotations

import pytest

from trajectory_sim.errors import (
    OutputError,
    ParameterError,
    SweepDefinitionError,
    TrajectorySimError,
)

CONCRETE_ERROR_CLASSES = (ParameterError, SweepDefinitionError, OutputError)


def test_trajectory_sim_error_is_a_value_error() -> None:
    """基底例外 `TrajectorySimError` は `ValueError` を継承する(design.md「Errors」)。"""
    assert issubclass(TrajectorySimError, ValueError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_trajectory_sim_error_subclasses(error_cls) -> None:
    """3 つの具象例外はいずれも `TrajectorySimError` のサブクラスである。"""
    assert issubclass(error_cls, TrajectorySimError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_value_error_subclasses(error_cls) -> None:
    """`TrajectorySimError` を経由して、3 つの具象例外は `ValueError` のサブクラスでもある。"""
    assert issubclass(error_cls, ValueError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_error_is_catchable_as_trajectory_sim_error(error_cls) -> None:
    """具象例外を送出しても、基底例外 `TrajectorySimError` として捕捉できる。"""
    with pytest.raises(TrajectorySimError):
        raise error_cls("boundary violated")


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_error_is_catchable_as_value_error(error_cls) -> None:
    """具象例外を送出しても、`ValueError` として捕捉できる
    (`trajectory_sim` を知らない呼び出し側の `except ValueError` を素通りさせないため)。
    """
    with pytest.raises(ValueError):
        raise error_cls("boundary violated")


def test_parameter_error_message_is_preserved() -> None:
    try:
        raise ParameterError("min_samples < 3")
    except TrajectorySimError as exc:
        assert str(exc) == "min_samples < 3"


def test_sweep_definition_error_message_is_preserved() -> None:
    try:
        raise SweepDefinitionError("empty axis")
    except TrajectorySimError as exc:
        assert str(exc) == "empty axis"


def test_output_error_message_is_preserved() -> None:
    try:
        raise OutputError("non-finite value in output")
    except TrajectorySimError as exc:
        assert str(exc) == "non-finite value in output"


def test_concrete_error_classes_are_mutually_distinct() -> None:
    """3 種は互いに独立したクラスであり、取り違えて捕捉できない。"""
    assert ParameterError is not SweepDefinitionError
    assert ParameterError is not OutputError
    assert SweepDefinitionError is not OutputError

    with pytest.raises(ParameterError):
        try:
            raise ParameterError("boundary violated")
        except SweepDefinitionError:  # pragma: no cover - 発生しないはず
            raise AssertionError("ParameterError が誤って SweepDefinitionError として捕捉された")


def test_trajectory_sim_error_itself_is_not_one_of_the_concrete_subclasses() -> None:
    """基底 `TrajectorySimError` 自体は 3 つの具象例外のいずれでもない。"""
    base_instance = TrajectorySimError("generic failure")
    assert not isinstance(base_instance, ParameterError)
    assert not isinstance(base_instance, SweepDefinitionError)
    assert not isinstance(base_instance, OutputError)
