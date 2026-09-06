"""例外階層の検証（タスク 1.2、要件 1.4, 2.3, 4.4）。

design.md「Components and Interfaces」の `#### Errors` と「Error Handling / Error Strategy」
が定める区分——**本 Spec の失敗を型で区別し、終了コードへ写せるようにする**——を、
`chassis_mechanism.errors` の階層として固定する。

本ファイルが固定するのは次の4点である。

1. 基底 `ChassisMechanismError` が `ValueError` を継承し、6つの具象例外が
   基底としても `ValueError` としても捕捉できること
   （tasks.md 1.2 の「観測可能な完了状態」の前半）
2. 例外の系統が design.md「Error Categories and Responses」の6系統
   （入力不正・形状/分割/干渉・床との隙間・観測の不足/不整合・記録との不一致・
   形状生成の環境が無い）に一致し、それ以外の系統を勝手に増やしていないこと
3. **上流 `catch_mechanism` の例外階層から独立していること。**
   `ParameterError` / `GeometryError` / `ConsistencyError` / `CadUnavailableError` の
   4つは**綴りが上流と衝突する**。design.md `#### Errors` の
   ⚠️「上流の `CatchMechanismError` 系を継承しない。また包み直さない」を、
   両方向の非継承・非同一・非捕捉として固定する
   （tasks.md 1.2 の「観測可能な完了状態」の後半）。
   ⚠️ 継承してしまうと、上流の設定の誤りと本 Spec の設定の誤りが同じ `except` に
   落ち、「どちらの設定が壊れているか」がメッセージから消える
4. **違反の詳細はメッセージだけが運ぶこと。** `errors` は依存を持たない層であり、
   違反を表す値型（上流 `BuildViolation` / 本 Spec の `ClearanceViolation`）を
   import できない。部位名・軸・超過量は例外メッセージへ載せる方針であるから、
   例外クラスは詳細を運ぶ独自の属性・`__init__` を持ってはならない

また、Components and Interfaces の表で Errors は **Dependencies が「なし」**であり、
「Dependency Direction」でも `errors` は最左の層である。これを
「`errors.py` は `from __future__ import annotations` 以外の import を一切持たない」
という静的検査として固定する（上流 `tests/catch_mechanism/test_catch_errors.py` と同じ形。
依存が生えると `errors → params` の一方向だった辺が循環しうるうえ、
`catch_mechanism` を import した瞬間に上の3が壊れる余地が生まれる）。

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
`test_errors.py` は `tests/sensing_foundation/test_errors.py` と、`test_catch_errors.py` は
上流と衝突するため、タスク 1.1 の `test_chassis_packaging.py` に倣い
`test_chassis_errors.py` とする。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import catch_mechanism
from chassis_mechanism import errors
from chassis_mechanism.errors import (
    CadUnavailableError,
    ChassisMechanismError,
    ClearanceError,
    ConsistencyError,
    GeometryError,
    MeasurementError,
    ParameterError,
)

#: design.md `#### Errors` の Service Interface が挙げる6系統の具象例外。
#: 並びは同ブロックの記載順（値・キー・範囲 → 造形/分割/干渉 → 床との隙間 →
#: 観測記録 → 記録との不一致 → 形状ライブラリ未導入）。
CONCRETE_ERROR_CLASSES = (
    ParameterError,
    GeometryError,
    ClearanceError,
    MeasurementError,
    ConsistencyError,
    CadUnavailableError,
)

#: 基底を含む、本モジュールが公開するすべての例外。
ALL_ERROR_CLASSES = (ChassisMechanismError, *CONCRETE_ERROR_CLASSES)

#: 上流 `catch_mechanism` の公開 API と**綴りが衝突する**例外の名前。
#: 上流にも同名の型があり、どちらも `ValueError` の派生であるため、
#: 独立性を機械的に確かめないと取り違えに気付けない。
COLLIDING_ERROR_NAMES = (
    "ParameterError",
    "GeometryError",
    "ConsistencyError",
    "CadUnavailableError",
)

ERRORS_MODULE_PATH = Path(errors.__file__).resolve()


def test_base_error_is_a_value_error() -> None:
    """基底 `ChassisMechanismError` は `ValueError` を継承する（design.md `#### Errors`）。"""
    assert issubclass(ChassisMechanismError, ValueError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_base_subclasses(error_cls: type[Exception]) -> None:
    """6つの具象例外はいずれも基底 `ChassisMechanismError` のサブクラスである。"""
    assert issubclass(error_cls, ChassisMechanismError)


@pytest.mark.parametrize("error_cls", CONCRETE_ERROR_CLASSES)
def test_concrete_errors_are_value_error_subclasses(error_cls: type[Exception]) -> None:
    """基底を経由して、6つの具象例外は `ValueError` のサブクラスでもある。"""
    assert issubclass(error_cls, ValueError)


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_error_is_catchable_as_base_error(error_cls: type[Exception]) -> None:
    """送出した例外を、基底 `ChassisMechanismError` として捕捉できる。

    tasks.md 1.2 の観測可能な完了状態の一部である。本パッケージ由来の失敗を
    呼び出し側が一括で捕捉できることを保証する。
    """
    with pytest.raises(ChassisMechanismError):
        raise error_cls("boundary violated")


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_error_is_catchable_as_value_error(error_cls: type[Exception]) -> None:
    """送出した例外を、`ValueError` としても捕捉できる。

    `chassis_mechanism` を知らない呼び出し側が既に書いている `except ValueError` を
    素通りさせない。
    """
    with pytest.raises(ValueError):
        raise error_cls("boundary violated")


def test_concrete_error_classes_are_mutually_distinct() -> None:
    """6系統は互いに独立したクラスであり、取り違えて捕捉できない。

    終了コードが系統ごとに違う（入力不正は 2、形状・隙間・観測・不一致は 1、
    形状環境の不在は 3。design.md「Error Categories and Responses」）ため、
    包含関係があると `cli` が誤った終了コードを返す。
    """
    assert len(set(CONCRETE_ERROR_CLASSES)) == len(CONCRETE_ERROR_CLASSES)
    for error_cls in CONCRETE_ERROR_CLASSES:
        for other in CONCRETE_ERROR_CLASSES:
            if other is error_cls:
                continue
            assert not issubclass(error_cls, other), f"{error_cls} が {other} を継承している"


def test_base_error_itself_is_not_one_of_the_concrete_subclasses() -> None:
    """基底 `ChassisMechanismError` 自体は6つの具象例外のいずれでもない。"""
    base_instance = ChassisMechanismError("generic failure")
    for error_cls in CONCRETE_ERROR_CLASSES:
        assert not isinstance(base_instance, error_cls)


def test_cad_unavailable_error_is_not_confused_with_parameter_error() -> None:
    """形状環境の不在（終了コード 3）が入力の誤り（終了コード 2）に紛れない。

    design.md「Error Categories and Responses」の ⚠️「成功にしない」に対応する区分。
    """
    with pytest.raises(CadUnavailableError):
        try:
            raise CadUnavailableError("build123d is not installed; `uv sync --extra cad`")
        except ParameterError:  # pragma: no cover - 発生しないはず
            raise AssertionError("CadUnavailableError が ParameterError として捕捉された")


# --- 上流 `catch_mechanism` からの独立性 ------------------------------------


def test_base_error_does_not_inherit_the_upstream_base() -> None:
    """基底は上流 `CatchMechanismError` を継承しない（design.md `#### Errors` の ⚠️）。

    継承すると `except catch_mechanism.CatchMechanismError` が本 Spec の失敗まで
    拾い、上流の設定を直せばよいのか本 Spec の設定を直せばよいのかが分からなくなる。
    """
    assert not issubclass(ChassisMechanismError, catch_mechanism.CatchMechanismError)


def test_upstream_base_does_not_inherit_the_chassis_base() -> None:
    """逆向きも成り立つ——上流の基底は本 Spec の基底の派生ではない。"""
    assert not issubclass(catch_mechanism.CatchMechanismError, ChassisMechanismError)


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_chassis_errors_are_not_upstream_errors(error_cls: type[Exception]) -> None:
    """本 Spec のどの例外も、上流の基底として捕捉できない。"""
    assert not issubclass(error_cls, catch_mechanism.CatchMechanismError)


@pytest.mark.parametrize("name", COLLIDING_ERROR_NAMES)
def test_colliding_names_are_distinct_classes(name: str) -> None:
    """綴りが衝突する4つは、上流と**別の型**である（同一オブジェクトの再エクスポートでない）。"""
    ours = getattr(errors, name)
    theirs = getattr(catch_mechanism, name)
    assert ours is not theirs, f"{name} が上流の型そのものになっている"


@pytest.mark.parametrize("name", COLLIDING_ERROR_NAMES)
def test_colliding_names_have_no_inheritance_in_either_direction(name: str) -> None:
    """綴りが衝突する4つに、どちらの向きの継承関係も無い。"""
    ours = getattr(errors, name)
    theirs = getattr(catch_mechanism, name)
    assert not issubclass(ours, theirs), f"{name} が上流の同名例外を継承している"
    assert not issubclass(theirs, ours), f"上流の {name} が本 Spec の同名例外を継承している"


@pytest.mark.parametrize("name", COLLIDING_ERROR_NAMES)
def test_chassis_failure_is_not_caught_by_the_upstream_handler(name: str) -> None:
    """本 Spec の失敗を送出しても、上流の同名例外の `except` は捕まえない。

    要件 1.4 は「該当する項目名を示す」ことを求める。上流の `except` に落ちると
    「上流の設定を直せ」という文脈で報告され、項目名の指す先が読み違えられる。
    """
    ours = getattr(errors, name)
    theirs = getattr(catch_mechanism, name)
    with pytest.raises(ours):
        try:
            raise ours("chassis side is broken")
        except theirs:  # pragma: no cover - 発生しないはず
            raise AssertionError(f"本 Spec の {name} が上流の {name} として捕捉された")


@pytest.mark.parametrize("name", COLLIDING_ERROR_NAMES)
def test_upstream_failure_is_not_caught_by_the_chassis_handler(name: str) -> None:
    """逆向きも成り立つ——上流の失敗を本 Spec の `except` が横取りしない。

    design.md「Error Strategy」の ⚠️「上流の失敗を包み直さない」の実体である。
    上流の失敗は上流の型のまま伝播しなければならない。
    """
    ours = getattr(errors, name)
    theirs = getattr(catch_mechanism, name)
    with pytest.raises(theirs):
        try:
            raise theirs("upstream side is broken")
        except ours:  # pragma: no cover - 発生しないはず
            raise AssertionError(f"上流の {name} が本 Spec の {name} として捕捉された")


# --- 詳細を運ぶのはメッセージだけである ------------------------------------


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_error_message_is_preserved_when_caught_as_the_base(
    error_cls: type[Exception],
) -> None:
    """基底として捕捉しても、送出時のメッセージが失われない。

    要件 1.4（項目名）・2.3（軸と超過量）・4.4（部位と不足量）が求める情報を
    載せる先はメッセージであり、途中の階層で握り潰されないことを固定する。
    """
    message = "clearance.bracket_to_floor_mm = 3.2 < 5.0 (不足 1.8mm)"
    try:
        raise error_cls(message)
    except ChassisMechanismError as exc:
        assert str(exc) == message


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_errors_carry_detail_only_through_the_message(
    error_cls: type[Exception],
) -> None:
    """例外クラスは詳細を運ぶ独自の属性・`__init__` を持たない。

    `errors` は依存を持たない層であり、違反を表す値型（上流 `BuildViolation` /
    本 Spec の `ClearanceViolation`）を import できない。したがって部位名・軸・
    不足量は**例外メッセージ**が運ぶ、というのがこの層の方針である
    （design.md `#### Errors` の ⚠️）。独自の `__init__` や属性を生やすと、
    その方針が静かに破れ、値型を受け取る署名へ滑っていく。
    """
    assert "__init__" not in vars(error_cls), f"{error_cls} が独自の __init__ を持つ"
    assert "__new__" not in vars(error_cls), f"{error_cls} が独自の __new__ を持つ"
    assert "__slots__" not in vars(error_cls), f"{error_cls} が独自の __slots__ を持つ"
    extra = {name for name in vars(error_cls) if not name.startswith("__")}
    assert extra == set(), f"{error_cls} が詳細を運ぶ属性を持つ: {sorted(extra)}"
    assert error_cls.__init__ is ValueError.__init__


@pytest.mark.parametrize("error_cls", ALL_ERROR_CLASSES)
def test_error_arguments_are_left_untouched(error_cls: type[Exception]) -> None:
    """送出時の引数はそのまま `args` に残る（包み直し・整形をしない）。"""
    message = "base.plate_thickness_mm: 造形可能寸法 z を 4.5mm 超過"
    assert error_cls(message).args == (message,)


@pytest.mark.parametrize("value_type_name", ["BuildViolation", "ClearanceViolation"])
def test_violation_value_types_are_not_part_of_the_exception_hierarchy(
    value_type_name: str,
) -> None:
    """違反を表す値型は例外階層に置かない（design.md「Error Strategy」の区分）。

    造形可能寸法の違反（上流 `BuildViolation`）と床との隙間の違反
    （`ClearanceViolation`）は**全件を値として返す**。`errors` 側に現れたら
    「最初の違反で打ち切る」設計へ滑っている。前者は上流が、後者は後続タスクの
    `clearance.py` が持つ。
    """
    assert not hasattr(errors, value_type_name)


# --- 系統を勝手に増やさない・依存を持たない --------------------------------


def test_module_exposes_exactly_the_six_categories_and_the_base() -> None:
    """公開するのは基底＋6系統だけであり、系統を勝手に増やしていない。

    design.md `#### Errors` の Service Interface に無い系統を足すと、`cli` の
    終了コードとの対応が曖昧になる。`__all__` を表明として固定する。
    """
    assert set(errors.__all__) == {
        "ChassisMechanismError",
        "ParameterError",
        "GeometryError",
        "ClearanceError",
        "MeasurementError",
        "ConsistencyError",
        "CadUnavailableError",
    }


def test_module_defines_no_extra_exception_classes() -> None:
    """モジュール内に `__all__` 外の例外クラスが隠れていない。

    上流の例外を import して別名で置いておく、といった抜け道も塞ぐ。
    """
    defined = {
        name
        for name, value in vars(errors).items()
        if isinstance(value, type) and issubclass(value, BaseException)
    }
    assert defined == set(errors.__all__)


def test_errors_module_has_no_dependencies() -> None:
    """`errors.py` は `from __future__ import annotations` 以外を import しない。

    design.md「Components and Interfaces」で Errors の Dependencies は**「なし」**、
    「Dependency Direction」でも `errors` は最左の層である。自パッケージの他モジュール
    にも上流にもサードパーティにも依存しないことを、モジュールを読み込まずに静的へ
    固定する（依存が生えると `errors → params` の一方向の辺が循環しうる。また
    `catch_mechanism` を import できてしまうと、上流の型を継承・包み直しする経路が
    開く）。
    """
    tree = ast.parse(ERRORS_MODULE_PATH.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1, f"想定外の import がある: {[ast.dump(node) for node in imports]}"
    only = imports[0]
    assert isinstance(only, ast.ImportFrom)
    assert only.module == "__future__"
    assert [alias.name for alias in only.names] == ["annotations"]


def test_every_error_class_states_why_its_category_exists() -> None:
    """基底と6系統のすべてに、なぜその系統が要るのかを述べた docstring がある。

    ⚠️ この層は違反の値型を持てないため、**どの情報をメッセージへ載せるか**が
    コードから読み取れない。方針の記述そのものが契約の一部である
    （上流 `src/catch_mechanism/errors.py` と同じ house style）。
    """
    assert errors.__doc__ is not None and errors.__doc__.strip()
    for error_cls in ALL_ERROR_CLASSES:
        doc = error_cls.__doc__
        assert doc is not None and doc.strip(), f"{error_cls} に docstring が無い"
