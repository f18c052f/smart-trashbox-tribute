"""例外階層の検証（タスク 1.2、要件 1.3, 1.4, 5.3）。

design.md「Error Handling / Error Strategy」が定める区分——
**呼び出し方の誤り・入力の不正は例外、評価結果（不適合・不一致）は値**——を、
`catch_mechanism.errors` の階層として固定する。

本ファイルが固定するのは次の3点である。

1. 基底 `CatchMechanismError` が `ValueError` を継承し、5つの具象例外が
   基底としても `ValueError` としても捕捉できること
   （tasks.md 1.2 の「観測可能な完了状態」そのもの）
2. 例外の系統が design.md「Error Categories and Responses」の5系統
   （パラメータ不正・選定不正・形状不正・整合不一致・形状環境不在）に
   一致し、それ以外の系統を勝手に増やしていないこと
3. **評価結果の型を例外階層へ持ち込んでいないこと。** 候補の不適合
   （`CandidateVerdict.accepted = False`）と指標の不一致（`MetricsMismatch`）は
   design.md では**値**であり、前者は `selection.py`、後者は `metrics.py`
   （いずれも後続タスク）が持つ。`errors` 側に現れたら区分が壊れている。

また、Components and Interfaces の表で Errors は Key Dependencies が
**「なし」**である。これを「`errors.py` は `from __future__ import annotations`
以外の import を一切持たない」という静的検査として固定する（自パッケージの
他モジュールにもサードパーティにも依存しないこと。依存が生えると
`params` → `errors` の一方向だった辺が循環しうる）。

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の
import-mode も既定（prepend）のため、テストモジュール名はセッション全体で
フラットである。design.md「Directory Structure」が挙げる `test_errors.py` は
`tests/sensing_foundation/test_errors.py` と衝突するため使えない。タスク 1.1 の
`test_catch_packaging.py` に倣い `test_catch_errors.py` とする
（tasks.md「Implementation Notes」）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from catch_mechanism import errors
from catch_mechanism.errors import (
    CadUnavailableError,
    CatchMechanismError,
    ConsistencyError,
    GeometryError,
    ParameterError,
    SelectionError,
)

#: design.md「Error Categories and Responses」の5系統に対応する具象例外。
#: 並びは同表の記載順（設定の不正 → 選定 → 造形制約 → 不一致 → 形状ライブラリ不在）。
CONCRETE_ERROR_CLASSES = (
    ParameterError,
    SelectionError,
    GeometryError,
    ConsistencyError,
    CadUnavailableError,
)

ERRORS_MODULE_PATH = Path(errors.__file__).resolve()


def test_base_error_is_a_value_error() -> None:
    """基底 `CatchMechanismError` は `ValueError` を継承する（design.md「Error Strategy」）。"""
    assert issubclass(CatchMechanismError, ValueError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_base_subclasses(error_cls: type[Exception]) -> None:
    """5つの具象例外はいずれも基底 `CatchMechanismError` のサブクラスである。"""
    assert issubclass(error_cls, CatchMechanismError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_value_error_subclasses(error_cls: type[Exception]) -> None:
    """基底を経由して、5つの具象例外は `ValueError` のサブクラスでもある。"""
    assert issubclass(error_cls, ValueError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_error_is_catchable_as_base_error(error_cls: type[Exception]) -> None:
    """具象例外を送出しても、基底 `CatchMechanismError` として捕捉できる。

    tasks.md 1.2 の観測可能な完了状態の前半である。本パッケージ由来の失敗を
    呼び出し側が一括で捕捉できることを保証する。
    """
    with pytest.raises(CatchMechanismError):
        raise error_cls("boundary violated")


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_error_is_catchable_as_value_error(error_cls: type[Exception]) -> None:
    """具象例外を送出しても、`ValueError` として捕捉できる。

    tasks.md 1.2 の観測可能な完了状態の後半である。`catch_mechanism` を
    知らない呼び出し側が既に書いている `except ValueError` を素通りさせない。
    """
    with pytest.raises(ValueError):
        raise error_cls("boundary violated")


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_error_message_is_preserved(error_cls: type[Exception]) -> None:
    """基底として捕捉しても、送出時のメッセージが失われない。

    要件 1.3 / 1.4 は「該当する項目名（と値）を示す」ことを求める。項目名を
    載せる先はメッセージであり、途中の階層で握り潰されないことを固定する。
    """
    try:
        raise error_cls("trash_can.opening_inner_diameter_mm = -1.0")
    except CatchMechanismError as exc:
        assert str(exc) == "trash_can.opening_inner_diameter_mm = -1.0"


def test_concrete_error_classes_are_mutually_distinct() -> None:
    """5系統は互いに独立したクラスであり、取り違えて捕捉できない。

    終了コードが系統ごとに違う（設定の不正は 2、不一致は 1、形状環境の不在は 3）
    ため、包含関係があると `cli` が誤った終了コードを返す。
    """
    assert len(set(CONCRETE_ERROR_CLASSES)) == len(CONCRETE_ERROR_CLASSES)
    for error_cls in CONCRETE_ERROR_CLASSES:
        others = [other for other in CONCRETE_ERROR_CLASSES if other is not error_cls]
        for other in others:
            assert not issubclass(error_cls, other), f"{error_cls} が {other} を継承している"


def test_cad_unavailable_error_is_not_confused_with_parameter_error() -> None:
    """形状環境の不在（終了コード 3）が入力の誤り（終了コード 2）に紛れない。

    design.md「Error Categories and Responses」の ⚠️「成功にしない」に対応する
    区分であり、要件 5.3 の「明示的な失敗として扱う」の実体である。
    """
    with pytest.raises(CadUnavailableError):
        try:
            raise CadUnavailableError("build123d is not installed; `uv sync --extra cad`")
        except ParameterError:  # pragma: no cover - 発生しないはず
            raise AssertionError("CadUnavailableError が ParameterError として捕捉された")


def test_base_error_itself_is_not_one_of_the_concrete_subclasses() -> None:
    """基底 `CatchMechanismError` 自体は5つの具象例外のいずれでもない。"""
    base_instance = CatchMechanismError("generic failure")
    for error_cls in CONCRETE_ERROR_CLASSES:
        assert not isinstance(base_instance, error_cls)


def test_module_exposes_exactly_the_five_categories_and_the_base() -> None:
    """公開するのは基底＋5系統だけであり、系統を勝手に増やしていない。

    design.md「Error Categories and Responses」に無い系統を足すと、`cli` の
    終了コードとの対応が曖昧になる。`__all__` を表明として固定する。
    """
    assert set(errors.__all__) == {
        "CatchMechanismError",
        "ParameterError",
        "SelectionError",
        "GeometryError",
        "ConsistencyError",
        "CadUnavailableError",
    }


def test_module_defines_no_extra_exception_classes() -> None:
    """モジュール内に `__all__` 外の例外クラスが隠れていない。"""
    defined = {
        name
        for name, value in vars(errors).items()
        if isinstance(value, type) and issubclass(value, BaseException)
    }
    assert defined == set(errors.__all__)


@pytest.mark.parametrize("value_type_name", ["MetricsMismatch", "CandidateVerdict"])
def test_evaluation_results_are_not_part_of_the_exception_hierarchy(
    value_type_name: str,
) -> None:
    """評価結果は例外階層に置かない（design.md「Error Strategy」の区分）。

    候補の不適合は `CandidateVerdict.accepted = False`、指標の不一致は
    `MetricsMismatch`（`compare_metrics` が返す frozen dataclass）であり、
    どちらも**値**である。`errors` に現れたら「不適合を例外にする」設計へ
    滑っている。値の型を持つのは後続タスクの `selection.py` / `metrics.py`
    であり、本モジュールではない。
    """
    assert not hasattr(errors, value_type_name)


def test_errors_module_has_no_dependencies() -> None:
    """`errors.py` は `from __future__ import annotations` 以外を import しない。

    design.md「Components and Interfaces」で Errors の Key Dependencies は
    **「なし」**である。自パッケージの他モジュールにもサードパーティにも
    依存しないことを、モジュールを読み込まずに静的へ固定する
    （依存が生えると `params` → `errors` の一方向の辺が循環しうる）。
    """
    tree = ast.parse(ERRORS_MODULE_PATH.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1, f"想定外の import がある: {[ast.dump(node) for node in imports]}"
    only = imports[0]
    assert isinstance(only, ast.ImportFrom)
    assert only.module == "__future__"
    assert [alias.name for alias in only.names] == ["annotations"]
