"""`sensing_foundation.errors` の例外階層に対するテスト（タスク 1.2）。

観測可能な完了状態（tasks.md 1.2）を固定する:
- 全例外が基底例外 `SensingFoundationError` として捕捉できる
- `SensingConfigError` は `ValueError` としても捕捉できる（多重継承）
- `SourceUnavailableError` は「次に何を確認すべきか」をメッセージに
  含められる（契約テスト。実際の送出箇所は後続タスクの責務）
"""

from __future__ import annotations

import pytest

from sensing_foundation.errors import (
    DeviceNotReadyError,
    RecordingFormatError,
    RecordingVersionError,
    RecordingWriteError,
    SensingConfigError,
    SensingFoundationError,
    SourceContractError,
    SourceUnavailableError,
)

ALL_EXCEPTION_TYPES = (
    SensingConfigError,
    SourceUnavailableError,
    DeviceNotReadyError,
    SourceContractError,
    RecordingFormatError,
    RecordingVersionError,
    RecordingWriteError,
)


class TestBaseCatchability:
    """全例外が基底例外として捕捉できることを固定する。"""

    @pytest.mark.parametrize("exc_type", ALL_EXCEPTION_TYPES)
    def test_all_exceptions_are_subclasses_of_base(self, exc_type: type[Exception]) -> None:
        assert issubclass(exc_type, SensingFoundationError)

    @pytest.mark.parametrize("exc_type", ALL_EXCEPTION_TYPES)
    def test_all_exceptions_catchable_as_base(self, exc_type: type[Exception]) -> None:
        with pytest.raises(SensingFoundationError):
            raise exc_type("boom")

    def test_base_is_plain_exception(self) -> None:
        assert issubclass(SensingFoundationError, Exception)
        assert not issubclass(SensingFoundationError, ValueError)


class TestSensingConfigErrorMultipleInheritance:
    """設定不正が ValueError としても捕捉できることを固定する。"""

    def test_is_subclass_of_value_error(self) -> None:
        assert issubclass(SensingConfigError, ValueError)

    def test_catchable_as_value_error(self) -> None:
        with pytest.raises(ValueError):
            raise SensingConfigError("invalid config")

    def test_catchable_as_base(self) -> None:
        with pytest.raises(SensingFoundationError):
            raise SensingConfigError("invalid config")


class TestHierarchyShape:
    """design.md の階層（継承関係）をそのまま固定する。"""

    def test_device_not_ready_is_source_unavailable(self) -> None:
        # 「認識はしたがモードを開けない」は SourceUnavailableError の一種。
        assert issubclass(DeviceNotReadyError, SourceUnavailableError)

    def test_device_not_ready_catchable_as_source_unavailable(self) -> None:
        with pytest.raises(SourceUnavailableError):
            raise DeviceNotReadyError("device recognized but mode unavailable")

    def test_recording_version_is_recording_format(self) -> None:
        # 「形式版が未知」は RecordingFormatError の一種。
        assert issubclass(RecordingVersionError, RecordingFormatError)

    def test_recording_version_catchable_as_recording_format(self) -> None:
        with pytest.raises(RecordingFormatError):
            raise RecordingVersionError("unknown format_version")

    def test_source_contract_error_is_not_source_unavailable(self) -> None:
        # アダプタ契約違反は「入力元利用不能」とは別系統。
        assert not issubclass(SourceContractError, SourceUnavailableError)

    def test_recording_write_error_is_not_recording_format(self) -> None:
        # 書き込み失敗（上限超過）は記録形式不整合とは別系統。
        assert not issubclass(RecordingWriteError, RecordingFormatError)

    def test_config_error_is_not_source_unavailable(self) -> None:
        assert not issubclass(SensingConfigError, SourceUnavailableError)


class TestSourceUnavailableGuidance:
    """`SourceUnavailableError` は「次に何を確認すべきか」を必ず含める（契約テスト）。"""

    def test_message_can_carry_next_step_guidance(self) -> None:
        exc = SourceUnavailableError(
            "pyrealsense2 を import できない。次に確認すべきこと: `doctor` の sdk_import を確認せよ"
        )
        assert "次に確認すべき" in str(exc)

    def test_device_not_ready_message_can_carry_next_step_guidance(self) -> None:
        # DeviceNotReadyError も SourceUnavailableError の一種として
        # 同じ契約（次に確認すべきかの明記）を満たせることを確認する。
        exc = DeviceNotReadyError(
            "要求した解像度でストリームを開けない。次に確認すべきこと: 対応解像度一覧を確認せよ"
        )
        assert "次に確認すべき" in str(exc)


class TestDocstringDocumentsObservedFactsPrinciple:
    """「観測された事実は例外にしない」という設計原則が明文化されていることを固定する。"""

    def test_module_docstring_mentions_observed_facts_are_not_exceptions(self) -> None:
        import sensing_foundation.errors as errors_module

        assert errors_module.__doc__ is not None
        assert "観測された事実" in errors_module.__doc__
        assert "例外にしない" in errors_module.__doc__
