"""world_frame_calibration.errors の例外階層と FailureReason 列挙の検証
（tasks.md タスク 1.2 / 要件 5.5, 5.6）。

design.md「L0-L2: 基盤・入力」節の Errors コンポーネント契約を固定する。
本テストが確認すること:

- 例外階層が3段（`WorldFrameCalibrationError` → `CalibrationConfigError` /
  `CalibrationFailure`）であり、すべての失敗が基底例外として捕捉できる
  （要件 5.5, 5.6 の「部分的な結果や既定値で埋めた結果として返さない」を
  例外で強制する設計の根幹）
- `FailureReason` が要件 5.1〜5.4 相当の11理由を過不足なく列挙し、
  `StrEnum` により文字列比較で分岐できる（要件 5.6）
- `CalibrationFailure` が実測値と判定に用いた下限を運ぶ `context` の器を持つ
  （design.md Errors「Validation」注記）
- なぜ値ではなく例外にするのかの理由（縮退した変換が下流へ流れること自体が
  事故であるため）が docstring として明記されている（tasks.md タスク 1.2）
"""

from __future__ import annotations

import pytest

from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
    WorldFrameCalibrationError,
)


class TestExceptionHierarchy:
    """3階層の例外がすべて基底例外として捕捉できることを固定する（要件 5.5, 5.6）。"""

    def test_base_exception_is_a_plain_exception(self) -> None:
        assert issubclass(WorldFrameCalibrationError, Exception)

    def test_config_error_is_a_world_frame_calibration_error(self) -> None:
        assert issubclass(CalibrationConfigError, WorldFrameCalibrationError)

    def test_calibration_failure_is_a_world_frame_calibration_error(self) -> None:
        assert issubclass(CalibrationFailure, WorldFrameCalibrationError)

    def test_config_error_is_catchable_as_base(self) -> None:
        with pytest.raises(WorldFrameCalibrationError):
            raise CalibrationConfigError("plan の探索範囲が不正である")

    def test_calibration_failure_is_catchable_as_base(self) -> None:
        with pytest.raises(WorldFrameCalibrationError):
            raise CalibrationFailure(
                reason=FailureReason.INSUFFICIENT_DEPTH_POINTS,
                detail="有効な Depth 点が下限を下回った",
                context={"valid_points": 12, "minimum_required": 100},
            )

    def test_config_error_and_calibration_failure_are_distinct_branches(self) -> None:
        """呼び出し方の誤りと座標系が定まらない条件は、互いを継承しない別枝である。"""
        assert not issubclass(CalibrationConfigError, CalibrationFailure)
        assert not issubclass(CalibrationFailure, CalibrationConfigError)


class TestFailureReasonEnumeration:
    """FailureReason が要件 5.1〜5.4 相当の11理由を列挙し、文字列比較できることを固定する。"""

    EXPECTED_VALUES = {
        "INSUFFICIENT_DEPTH_POINTS": "insufficient_depth_points",
        "PLANE_NOT_SUPPORTED": "plane_not_supported",
        "INCIDENCE_ANGLE_TOO_SHALLOW": "incidence_angle_too_shallow",
        "ANCHOR_NOT_FOUND": "anchor_not_found",
        "ANCHOR_BASELINE_TOO_SHORT": "anchor_baseline_too_short",
        "ANCHOR_DEGENERATE": "anchor_degenerate",
        "UNSUPPORTED_DISTORTION": "unsupported_distortion",
        "ROTATION_NOT_ORTHONORMAL": "rotation_not_orthonormal",
        "UNKNOWN_FORMAT_VERSION": "unknown_format_version",
        "PROFILE_MISMATCH": "profile_mismatch",
        "VERIFICATION_NOT_INDEPENDENT": "verification_not_independent",
    }

    def test_enumeration_has_exactly_the_expected_members(self) -> None:
        actual_names = {member.name for member in FailureReason}
        assert actual_names == set(self.EXPECTED_VALUES)

    @pytest.mark.parametrize("name,value", sorted(EXPECTED_VALUES.items()))
    def test_member_equals_its_string_value(self, name: str, value: str) -> None:
        """StrEnum により、呼び出し側が文字列比較のみで分岐できることを固定する（要件 5.6）。"""
        member = getattr(FailureReason, name)
        assert member == value
        assert isinstance(member, str)

    def test_members_are_distinguishable_by_string_comparison(self) -> None:
        """代表例: INSUFFICIENT_DEPTH_POINTS と PLANE_NOT_SUPPORTED は文字列として異なる。"""
        assert FailureReason.INSUFFICIENT_DEPTH_POINTS != FailureReason.PLANE_NOT_SUPPORTED
        assert FailureReason.INSUFFICIENT_DEPTH_POINTS == "insufficient_depth_points"
        assert FailureReason.PLANE_NOT_SUPPORTED == "plane_not_supported"

    def test_reason_can_drive_a_string_based_branch(self) -> None:
        """呼び出し側が if/elif の文字列比較だけで理由ごとに分岐できることを固定する。"""

        def classify(reason: FailureReason) -> str:
            if reason == "anchor_baseline_too_short":
                return "baseline"
            if reason == "insufficient_depth_points":
                return "points"
            return "other"

        assert classify(FailureReason.ANCHOR_BASELINE_TOO_SHORT) == "baseline"
        assert classify(FailureReason.INSUFFICIENT_DEPTH_POINTS) == "points"
        assert classify(FailureReason.ANCHOR_DEGENERATE) == "other"


class TestCalibrationFailureContext:
    """CalibrationFailure が実測値と下限を運ぶ context の器を持つことを固定する
    （design.md Errors「Validation」: 内点率・基線長・入射角などの実測値と
    判定に用いた下限を必ず入れる）。
    """

    def test_carries_reason_detail_and_context(self) -> None:
        context = {"baseline_mm": 42.0, "minimum_baseline_mm": 150.0}
        failure = CalibrationFailure(
            reason=FailureReason.ANCHOR_BASELINE_TOO_SHORT,
            detail="原点マーカーと方向マーカーの距離が下限を下回った",
            context=context,
        )
        assert failure.reason == FailureReason.ANCHOR_BASELINE_TOO_SHORT
        assert failure.detail == "原点マーカーと方向マーカーの距離が下限を下回った"
        assert failure.context == context

    def test_context_defaults_to_empty_mapping_when_omitted(self) -> None:
        failure = CalibrationFailure(
            reason=FailureReason.ANCHOR_NOT_FOUND,
            detail="方向マーカーが指定範囲内に見つからなかった",
        )
        assert dict(failure.context) == {}

    def test_context_is_available_after_catching_via_base_exception(self) -> None:
        """基底例外として捕捉しても reason/context に到達できる（呼び出し側の分岐材料）。"""
        context = {"valid_points": 3, "minimum_required": 50}
        try:
            raise CalibrationFailure(
                reason=FailureReason.INSUFFICIENT_DEPTH_POINTS,
                detail="有効点が不足した",
                context=context,
            )
        except WorldFrameCalibrationError as caught:
            assert isinstance(caught, CalibrationFailure)
            assert caught.reason == "insufficient_depth_points"
            assert caught.context == context
        else:
            pytest.fail("CalibrationFailure が送出されなかった")

    def test_str_includes_reason_and_detail_for_readability(self) -> None:
        """人が読むログ・トレースバックに理由と詳細が出ることを最低限確認する。"""
        failure = CalibrationFailure(
            reason=FailureReason.PLANE_NOT_SUPPORTED,
            detail="内点率が下限を下回った",
            context={"inlier_ratio": 0.4, "minimum_inlier_ratio": 0.8},
        )
        rendered = str(failure)
        assert "plane_not_supported" in rendered
        assert "内点率が下限を下回った" in rendered


class TestExceptionVsValueRationaleIsDocumented:
    """なぜ値ではなく例外にするのかの理由が docstring に明記されていることを固定する
    （tasks.md タスク 1.2: 縮退した変換が下流へ流れること自体が事故であるため）。
    """

    def test_module_docstring_explains_exception_over_value_rationale(self) -> None:
        import world_frame_calibration.errors as errors_module

        docstring = errors_module.__doc__ or ""
        assert docstring.strip() != ""
        # 「値ではなく例外にする」という設計判断そのものへの言及
        assert "例外" in docstring
        # 縮退した変換が下流へ静かに流れることが事故である、という理由への言及
        assert "下流" in docstring

    def test_calibration_failure_docstring_is_non_empty(self) -> None:
        assert (CalibrationFailure.__doc__ or "").strip() != ""
