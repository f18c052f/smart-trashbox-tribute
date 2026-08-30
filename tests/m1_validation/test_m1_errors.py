"""例外階層と失敗理由の列挙の検証（タスク 1.2 / 要件 1.4, 1.5, 1.6, 2.1）。

観測可能な完了状態（tasks.md 1.2）を固定する:

- すべての失敗が基底例外 `M1ValidationError` として捕捉できる
- 失敗理由が**文字列比較で分岐できる**（`StrEnum`。呼び出し側が
  `m1_validation.errors` の列挙型を import せずに `reason == "frame_mismatch"`
  と書ける）

あわせて design.md「Errors」が定める次の点も固定する:

- 例外が「実測値と判定に用いた基準」を載せる文脈情報の器を持つ
- 本モジュールが L0 層であり、標準ライブラリ以外を import しない
  （design.md「Dependency Direction」の層表。`errors` は依存を持たない）

ファイル名に `m1_` を冠しているのは `tests/sensing_foundation/test_errors.py`
と衝突するためである（tasks.md「Implementation Notes」タスク1.1）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from m1_validation.errors import (
    FailureReason,
    M1ConfigError,
    M1ValidationError,
    SeamFailure,
)

#: design.md「Errors / Responsibilities & Constraints」が列挙する9件の失敗理由。
#: **値は下流が文字列比較で分岐する契約**なので、変更は下流の再検証を要する。
EXPECTED_REASON_VALUES = {
    "FRAME_MISMATCH": "frame_mismatch",
    "UNKNOWN_HANDOFF_VERSION": "unknown_handoff_version",
    "UNKNOWN_CALIBRATION_VERSION": "unknown_calibration_version",
    "PROFILE_MISMATCH": "profile_mismatch",
    "CALIBRATION_NOT_VERIFIED": "calibration_not_verified",
    "NO_VALID_SAMPLE": "no_valid_sample",
    "TRUTH_MISSING": "truth_missing",
    "INSUFFICIENT_TRIALS": "insufficient_trials",
    "UNKNOWN_RECORD_SCHEMA": "unknown_record_schema",
}

#: 本モジュールが import してよい標準ライブラリ（L0 層）。
ALLOWED_IMPORT_ROOTS = {"__future__", "collections", "enum", "typing"}


def _imports_of_errors_module() -> list[tuple[str, int]]:
    """`m1_validation/errors.py` のモジュールレベル import を `(先頭名, 相対階層)` で返す。"""
    source = Path(inspect.getfile(M1ValidationError)).read_text(encoding="utf-8")
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            root = node.module.split(".")[0] if node.module else ""
            found.append((root, node.level))
    return found


class TestBaseCapture:
    """すべての失敗が1つの基底例外として捕捉できる（tasks.md 1.2 の完了状態）。"""

    def test_config_error_is_caught_by_the_base(self) -> None:
        with pytest.raises(M1ValidationError):
            raise M1ConfigError("閾値が範囲外である")

    def test_seam_failure_is_caught_by_the_base(self) -> None:
        with pytest.raises(M1ValidationError):
            raise SeamFailure(FailureReason.FRAME_MISMATCH, "カメラ座標系ではない")

    def test_the_two_families_are_distinguishable_from_each_other(self) -> None:
        """2系統は基底で一括捕捉できると同時に、互いに区別できる。

        設定の誤り（起動時に直すもの）と継ぎ目の不成立（実験の前提が
        崩れているもの）は、呼び出し側の対処が違う。
        """
        assert not issubclass(M1ConfigError, SeamFailure)
        assert not issubclass(SeamFailure, M1ConfigError)


class TestFailureReasonIsStringComparable:
    """失敗理由が文字列比較で分岐できる（tasks.md 1.2 の完了状態 / 要件 1.4-1.6）。"""

    def test_reason_equals_its_plain_string(self) -> None:
        failure = SeamFailure(FailureReason.FRAME_MISMATCH, "座標系が食い違う")
        # 呼び出し側が `FailureReason` を import せずに分岐できることが要点。
        assert failure.reason == "frame_mismatch"

    def test_all_nine_reasons_are_declared_with_the_expected_values(self) -> None:
        actual = {member.name: member.value for member in FailureReason}
        assert actual == EXPECTED_REASON_VALUES

    def test_reason_values_are_unique(self) -> None:
        """別の理由が同じ文字列になっていない（分岐が縮退しない）。"""
        values = [member.value for member in FailureReason]
        assert len(values) == len(set(values))


class TestContext:
    """例外が実測値と判定基準を運ぶ（design.md「Errors」/ tasks.md 1.2）。"""

    def test_context_carries_the_measured_value_and_the_criterion(self) -> None:
        failure = SeamFailure(
            FailureReason.INSUFFICIENT_TRIALS,
            "試行数が下限に届かない",
            {"trials": 7, "minimum_trials": 20},
        )
        assert failure.context == {"trials": 7, "minimum_trials": 20}

    def test_config_error_also_carries_context(self) -> None:
        """設定の誤りも「実測値と基準」を運ぶ。

        「閾値が不正」とだけ言われても、どの値がどの範囲を外れたのかが
        分からなければ直せない。
        """
        error = M1ConfigError(
            "収束帯域が範囲外である", {"band_mm": -5.0, "allowed_min_mm": 0.0}
        )
        assert error.context == {"band_mm": -5.0, "allowed_min_mm": 0.0}

    def test_context_defaults_to_an_empty_mapping_not_none(self) -> None:
        """未指定でも `None` にしない（呼び出し側が毎回 None 検査をしなくて済む）。"""
        assert SeamFailure(FailureReason.TRUTH_MISSING, "落下地点が未記入").context == {}
        assert M1ConfigError("レイアウト未指定").context == {}

    def test_context_is_copied_so_later_mutation_does_not_change_it(self) -> None:
        """渡した辞書をあとから書き換えても、例外が運ぶ値は変わらない。

        例外は「その時点で観測された事実」を運ぶ。呼び出し側が使い回して
        いる辞書をそのまま抱えると、記録された事実が後から書き換わる。
        """
        mutable = {"trials": 7, "minimum_trials": 20}
        failure = SeamFailure(FailureReason.INSUFFICIENT_TRIALS, "不足", mutable)
        mutable["trials"] = 999

        assert failure.context == {"trials": 7, "minimum_trials": 20}


class TestMessage:
    def test_seam_failure_message_names_the_reason(self) -> None:
        """例外がそのまま traceback やログへ出ても理由が読める。"""
        failure = SeamFailure(FailureReason.PROFILE_MISMATCH, "fps が食い違う")
        assert str(failure) == "profile_mismatch: fps が食い違う"

    def test_detail_stays_unprefixed(self) -> None:
        """`detail` は理由を前置しない生の説明文である（再整形の材料になる）。"""
        failure = SeamFailure(FailureReason.PROFILE_MISMATCH, "fps が食い違う")
        assert failure.detail == "fps が食い違う"

    def test_config_error_message_is_the_detail(self) -> None:
        assert str(M1ConfigError("レイアウト未指定")) == "レイアウト未指定"


class TestLayerZero:
    """`errors` は L0 層であり依存を持たない（design.md「Dependency Direction」）。"""

    def test_module_imports_only_the_standard_library(self) -> None:
        roots = {root for root, level in _imports_of_errors_module() if level == 0}
        assert roots <= ALLOWED_IMPORT_ROOTS, sorted(roots - ALLOWED_IMPORT_ROOTS)

    def test_module_has_no_relative_imports(self) -> None:
        """自パッケージの他モジュールも import しない（層の最下段である）。

        相対 import（`from . import types`）は上の検査を素通りするため、
        別に見る。`errors` が `types` を参照し始めると層が循環する。
        """
        relative = [root for root, level in _imports_of_errors_module() if level > 0]
        assert relative == []
