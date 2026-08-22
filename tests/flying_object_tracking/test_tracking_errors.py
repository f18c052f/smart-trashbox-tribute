"""例外階層を検証する（タスク 1.2、要件 10.4 / 10.5）。

design.md「Error Handling / Error Strategy」が定める3区分のうち、
本テストが固定するのは「呼び出し方・設定の誤り」と「前提の欠落」に
対応する例外側の契約である。

- 全例外が基底例外 `TrackingError` として一括捕捉できること。
- `TrackingConfigError`（設定不正、要件 10.5）は `prediction_core` の
  既存の慣行（`PredictionConfigError` など）に揃え、`ValueError` としても
  捕捉できること。呼び出し側が汎用の `except ValueError` を書いていても
  そのまま働く。
- `IntrinsicsUnavailableError`（内因パラメータ欠落、要件 10.4）と
  `DetectorUnavailableError`（検出手段の前提欠落）は「設定不正」とは
  別区分（前提の欠落）であり、`ValueError` としては捕捉できないこと。
- `IntrinsicsUnavailableError` のメッセージは「次に何を確認すべきか」を
  含む、実際に読んで役立つ文言であること。
"""

from __future__ import annotations

import pytest

from flying_object_tracking.errors import (
    DetectorUnavailableError,
    IntrinsicsUnavailableError,
    TrackingConfigError,
    TrackingError,
)

ALL_EXCEPTION_TYPES = (
    TrackingConfigError,
    IntrinsicsUnavailableError,
    DetectorUnavailableError,
)


@pytest.mark.parametrize("exc_type", ALL_EXCEPTION_TYPES)
def test_all_exceptions_are_catchable_as_tracking_error(exc_type) -> None:
    """全例外は基底例外 `TrackingError` として一括捕捉できる（観測可能な完了状態）。"""
    with pytest.raises(TrackingError):
        raise exc_type("message")


def test_tracking_error_itself_is_a_plain_exception() -> None:
    """基底例外は `Exception` を継承するだけで、`ValueError` は継承しない。"""
    assert issubclass(TrackingError, Exception)
    assert not issubclass(TrackingError, ValueError)


def test_tracking_config_error_is_also_catchable_as_value_error() -> None:
    """設定不正（要件 10.5）は `ValueError` としても捕捉できる
    （既存の予測コア `PredictionConfigError` の慣行に揃える）。
    """
    assert issubclass(TrackingConfigError, ValueError)
    with pytest.raises(ValueError):
        raise TrackingConfigError("roi is out of frame bounds")


@pytest.mark.parametrize(
    "exc_type", [IntrinsicsUnavailableError, DetectorUnavailableError]
)
def test_prerequisite_missing_errors_are_not_value_errors(exc_type) -> None:
    """前提の欠落（内因パラメータ欠落・検出手段の前提欠落）は設定不正とは
    別区分であり、`ValueError` としては捕捉できない。
    """
    assert not issubclass(exc_type, ValueError)
    with pytest.raises(exc_type):
        try:
            raise exc_type("message")
        except ValueError:  # pragma: no cover - 発生してはならない分岐
            pytest.fail(f"{exc_type.__name__} は ValueError であってはならない")


def test_intrinsics_unavailable_error_message_states_what_to_check_next() -> None:
    """`IntrinsicsUnavailableError` のメッセージは、次に何を確認すべきか
    （入力元の内因パラメータ設定を確認すること）を含む。
    """
    error = IntrinsicsUnavailableError(
        "frame.profile.intrinsics is None; "
        "入力元（simulated / recorded）の内因パラメータ設定を確認してください"
    )
    message = str(error)
    assert "intrinsics" in message
    assert "確認" in message


def test_detector_unavailable_error_is_catchable_as_tracking_error_only() -> None:
    """検出手段の前提欠落（OpenCV 等）は `TrackingError` としてのみ捕捉できる。"""
    with pytest.raises(TrackingError):
        raise DetectorUnavailableError("opencv-python-headless is not installed")
    with pytest.raises(TrackingError) as excinfo:
        raise DetectorUnavailableError("opencv-python-headless is not installed")
    assert not isinstance(excinfo.value, ValueError)
