"""寸法設定ファイルの読み書きとパラメータ識別子（タスク 1.4、要件 1.1, 1.3,
1.6, 1.7, 4.5, 6.6, 6.7）。

本ファイルが固定するのは design.md「Config」の Preconditions / Postconditions /
Invariants と、tasks.md タスク 1.4 の「観測可能な完了状態」である。

1. **`configs/catch_mechanism/dimensions.json` が寸法パラメータの単一の正**で
   あり、第一候補の公称値が**すべて仮値**として記録されていること
   （要件 1.1, 6.7）。⚠️ 出所表に現れないパスは `ASSUMED` として扱う運用である
   ため、「明示されていない = 実測ではない」が全パスについて成り立つ
2. **あらゆる階層で未知キーを拒否する**こと（要件 1.3 / design.md「Config」
   Responsibilities）。最上位・コンポーネント・出所表のどこに混ぜても、
   該当する項目名を示して失敗する
3. **欠損・範囲外・型違いを項目名つきで拒否する**こと（要件 1.3 と、
   `params` 側の構築時検証が読み込み経路でも働くこと）
4. **読み込み → 書き出し → 読み込みで値と出所が保存される**こと
   （design.md「Config」Invariants / 要件 6.6: 採寸値を書き込んでも実装コードを
   変更せずに以降の導出へ流れる）
5. **識別子は値が同じなら書式に依らず同一**であること（design.md「Config」
   Postconditions）。⚠️ 逆に、値または出所が変われば識別子は変わる——これが
   「パラメータを変えたのに形状指標を更新し忘れた」事故を CAD 非導入環境でも
   捕まえる唯一の手掛かりである（要件 4.5）
6. **書き出しが行単位の差分として読める整形**であること（要件 1.7）

ファイル名について: design.md「Directory Structure」は `test_config.py` を挙げる
が、`tests/prediction_core/test_config.py` と衝突する（`tests/` に `__init__.py`
が無く pytest の import-mode も既定のため、テストモジュール名はセッション全体で
フラットである）。タスク 1.1〜1.3 の `test_catch_*.py` に倣う
（tasks.md「Implementation Notes」）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from catch_mechanism import config as config_module
from catch_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    dump_params,
    load_params,
    parameters_digest,
)
from catch_mechanism.errors import CatchMechanismError, ParameterError
from catch_mechanism.params import PARAMETER_PATHS, MechanismParams, Provenance

# ---------------------------------------------------------------------------
# ヘルパ
#
# テスト側で値表を作らない。すべて出荷される `dimensions.json` を読み、必要な
# 1項目だけを差し替える形に統一する（値の二重管理を避けるため。要件 1.8）。
# ---------------------------------------------------------------------------


def _shipped_document() -> dict[str, Any]:
    """出荷される設定ファイルを素の辞書として読む（改変用の土台）。"""
    return json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))


def _write(
    tmp_path: Path,
    document: object,
    *,
    name: str = "dimensions.json",
    newline: str = "\n",
    **dumps_kwargs: Any,
) -> Path:
    """`document` を JSON として書き出し、そのパスを返す。"""
    kwargs: dict[str, Any] = {"indent": 2, "sort_keys": True, "ensure_ascii": False}
    kwargs.update(dumps_kwargs)
    target = tmp_path / name
    target.write_text(json.dumps(document, **kwargs) + "\n", encoding="utf-8", newline=newline)
    return target


def _effective_provenance(params: MechanismParams) -> dict[str, Provenance]:
    """全パスについての実効的な出所（表に無いパスは仮値）を返す。"""
    return {path: params.provenance.get(path, Provenance.ASSUMED) for path in PARAMETER_PATHS}


# ---------------------------------------------------------------------------
# 単一の正としての `configs/catch_mechanism/dimensions.json`（要件 1.1, 6.7）
# ---------------------------------------------------------------------------


def test_default_path_points_at_the_single_source_of_truth() -> None:
    """既定パスが `configs/catch_mechanism/dimensions.json` を指し、実在する。"""
    assert DEFAULT_DIMENSIONS_PATH.name == "dimensions.json"
    assert DEFAULT_DIMENSIONS_PATH.parent.name == "catch_mechanism"
    assert DEFAULT_DIMENSIONS_PATH.parent.parent.name == "configs"
    assert DEFAULT_DIMENSIONS_PATH.is_file()


def test_load_without_arguments_reads_the_default_path() -> None:
    """引数なしの読み込みが既定パスの内容を返す（要件 1.1）。"""
    assert load_params() == load_params(DEFAULT_DIMENSIONS_PATH)


def test_shipped_values_match_the_design_data_model() -> None:
    """出荷値が design.md「Logical Data Model」の第一候補の公称値である。"""
    params = load_params()
    assert params.trash_can.model_id == "yamada-kagaku-no335"
    assert params.trash_can.opening_inner_diameter_mm == 220.0
    assert params.trash_can.taper_deg == 7.0
    assert params.target_object.diameter_mm == 65.0
    assert params.printing.material == "PETG"
    assert params.joint.bolt_designation == "M3"
    assert params.rim.flange_width_mm == 30.0
    assert params.retention.retrofit_fastener_count == 6
    # 設計上の決定は型でも固定されているが、設定ファイル側でも同じ値である。
    assert params.retention.added_depth_mm == 0.0
    assert params.retention.bottom_modification == "none"


def test_every_shipped_value_is_assumed() -> None:
    """⚠️ 出荷値は**すべて仮値**である（要件 6.7 / タスク 1.4）。

    実測が1つでも紛れていれば、未実測の公称値が合否条件として使われうる。
    明示されている出所も、明示されていないパス（= 仮値扱い）も、まとめて
    全 32 パスについて `ASSUMED` であることを固定する。
    """
    effective = _effective_provenance(load_params())
    assert set(effective) == set(PARAMETER_PATHS)
    measured = sorted(path for path, prov in effective.items() if prov is not Provenance.ASSUMED)
    assert measured == []


def test_shipped_document_has_the_schema_version_outside_the_parameters() -> None:
    """`schema_version` は記録形式の版であってパラメータではない。"""
    document = _shipped_document()
    assert document["schema_version"] == SCHEMA_VERSION
    assert "schema_version" not in PARAMETER_PATHS
    assert not any(path.endswith(".schema_version") for path in PARAMETER_PATHS)


def test_shipped_document_covers_every_parameter_path() -> None:
    """設定ファイルがパラメータパス表の全項目を持つ（欠損なし）。"""
    document = _shipped_document()
    present = {
        f"{component}.{field_name}"
        for component, values in document.items()
        if isinstance(values, dict) and component != "provenance"
        for field_name in values
    }
    assert present == set(PARAMETER_PATHS)


def test_shipped_document_is_written_in_the_canonical_dump_format(tmp_path: Path) -> None:
    """出荷ファイルが `dump_params` の出力そのものである（要件 1.7）。

    手編集で並び順や整形が崩れると、以降の書き出しが巨大な差分を生む。
    ⚠️ 改行は比較前に正規化する——`.gitattributes` が `*.json` を作業ツリーで
    CRLF に固定する一方、`dump_params` はプラットフォームに依らず LF を書く。
    """
    regenerated = tmp_path / "dimensions.json"
    dump_params(load_params(), regenerated)
    shipped = DEFAULT_DIMENSIONS_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert shipped == regenerated.read_bytes()


def test_shipped_document_is_line_diffable() -> None:
    """1項目 = 1行であり、末尾改行を持つ（要件 1.7）。"""
    text = DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # 32 パラメータ + `schema_version` + 括弧 = 40 行を優に超える。1行 JSON で
    # 保存されていれば行単位の差分は読めない。
    assert len(text.splitlines()) > 40


# ---------------------------------------------------------------------------
# あらゆる階層での未知キーの拒否（要件 1.3）
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """最上位の未知キーを、項目名を示して拒否する。"""
    document = _shipped_document()
    document["chassis"] = {"wheel_diameter_mm": 48.0}
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "chassis" in str(excinfo.value)


def test_unknown_component_key_is_rejected(tmp_path: Path) -> None:
    """コンポーネント内の未知キーを、項目名を示して拒否する。"""
    document = _shipped_document()
    document["trash_can"]["lid_diameter_mm"] = 210.0
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "lid_diameter_mm" in message
    assert "trash_can" in message


@pytest.mark.parametrize(
    "component",
    ["trash_can", "target_object", "printing", "joint", "rim", "retention"],
)
def test_unknown_key_is_rejected_in_every_component(tmp_path: Path, component: str) -> None:
    """未知キーの拒否がコンポーネントごとに漏れていない（あらゆる階層）。"""
    document = _shipped_document()
    document[component]["surely_unknown_mm"] = 1.0
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "surely_unknown_mm" in str(excinfo.value)


def test_unknown_provenance_key_is_rejected(tmp_path: Path) -> None:
    """出所表のキーがパラメータパス表と一致しない場合に拒否する（要件 1.3）。

    ⚠️ 黙って無視すると、実測したつもりの値が仮値のまま残る
    （design.md「Params」Risks）。
    """
    document = _shipped_document()
    document["provenance"]["trash_can.rim_thickness_mm"] = "measured"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "trash_can.rim_thickness_mm" in str(excinfo.value)


def test_provenance_key_naming_a_component_only_is_rejected(tmp_path: Path) -> None:
    """コンポーネント名だけの出所キー（リーフでない）も拒否する。"""
    document = _shipped_document()
    document["provenance"]["trash_can"] = "measured"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "trash_can" in str(excinfo.value)


def test_unknown_provenance_value_is_rejected(tmp_path: Path) -> None:
    """出所の値は `measured` / `assumed` の2値のみ（第3の値を作らない）。"""
    document = _shipped_document()
    document["provenance"]["trash_can.height_mm"] = "derived"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "derived" in message
    assert "trash_can.height_mm" in message


# ---------------------------------------------------------------------------
# 欠損・範囲外・型違いの拒否（要件 1.3）
# ---------------------------------------------------------------------------


def test_missing_component_is_rejected(tmp_path: Path) -> None:
    """コンポーネントの欠損を、項目名を示して拒否する。"""
    document = _shipped_document()
    del document["rim"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "rim" in str(excinfo.value)


def test_missing_leaf_value_is_rejected(tmp_path: Path) -> None:
    """必須の寸法値の欠損を、項目名を示して拒否する（既定値で埋めない）。"""
    document = _shipped_document()
    del document["trash_can"]["height_mm"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "height_mm" in str(excinfo.value)


def test_missing_decision_value_is_rejected(tmp_path: Path) -> None:
    """設計上の決定値（型に既定値がある項目）も設定ファイルでは必須である。

    ⚠️ 既定値で黙って埋めると、「深さを足さない」という決定が設定ファイルの
    上から消え、何が決まっているのかがファイルから読めなくなる。
    """
    document = _shipped_document()
    del document["retention"]["added_depth_mm"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "added_depth_mm" in str(excinfo.value)


def test_missing_provenance_table_is_rejected(tmp_path: Path) -> None:
    """出所表そのものの欠落を拒否する（節ごと消えたファイルを読み替えない）。

    ⚠️ 表の**中に現れないパス**を仮値として扱うのは規約だが、表そのものが
    無いファイルを「すべて仮値」と読み替えるのは、欠損を黙って埋めることに
    他ならない。出所は要件 1.2 が求める一級の記録である。
    """
    document = _shipped_document()
    del document["provenance"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "provenance" in str(excinfo.value)


def test_empty_provenance_table_is_accepted(tmp_path: Path) -> None:
    """空の出所表は「すべて仮値」として受け付ける（要件 6.7）。"""
    document = _shipped_document()
    document["provenance"] = {}
    params = load_params(_write(tmp_path, document))
    assert set(_effective_provenance(params).values()) == {Provenance.ASSUMED}


def test_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    """記録形式の版の欠損を拒否する。"""
    document = _shipped_document()
    del document["schema_version"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "schema_version" in str(excinfo.value)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """未対応の版を、項目名と値を示して拒否する。"""
    document = _shipped_document()
    document["schema_version"] = "2.0"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "schema_version" in message
    assert "2.0" in message


@pytest.mark.parametrize(
    ("component", "field_name", "value"),
    [
        ("trash_can", "taper_deg", 95.0),
        ("trash_can", "height_mm", -1.0),
        ("trash_can", "mass_g", 0.0),
        ("rim", "flange_slope_deg", 90.0),
        ("printing", "segment_margin_mm", -0.5),
        ("retention", "retrofit_fastener_count", 0),
    ],
)
def test_out_of_range_value_is_rejected(
    tmp_path: Path, component: str, field_name: str, value: float
) -> None:
    """範囲外の値を、項目名と値を示して拒否する（要件 1.3）。"""
    document = _shipped_document()
    document[component][field_name] = value
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert field_name in message
    assert str(value) in message or repr(value) in message


def test_cross_field_invariant_violation_is_rejected(tmp_path: Path) -> None:
    """径の大小関係の逆転も読み込み経路で拒否される（構築時検証が働く）。"""
    document = _shipped_document()
    document["trash_can"]["bottom_flat_diameter_mm"] = 200.0
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "bottom_flat_diameter_mm" in message
    assert "bottom_outer_diameter_mm" in message


def test_disallowed_material_is_rejected(tmp_path: Path) -> None:
    """許可一覧に無い材料を拒否する（要件 2.5 が読み込み経路でも働く）。"""
    document = _shipped_document()
    document["printing"]["material"] = "ABS"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "ABS" in str(excinfo.value)


@pytest.mark.parametrize(
    ("component", "field_name", "value"),
    [
        ("trash_can", "height_mm", "244.0"),
        ("trash_can", "model_id", 335),
        ("trash_can", "opening_inner_diameter_mm", None),
        ("trash_can", "height_mm", True),
        ("retention", "retrofit_fastener_count", 6.5),
        ("retention", "retrofit_fastener_count", True),
        ("printing", "material", ["PETG"]),
    ],
)
def test_wrong_value_type_is_rejected(
    tmp_path: Path, component: str, field_name: str, value: object
) -> None:
    """型違いを、項目名を示して拒否する。

    ⚠️ `true` は Python では `int` の派生であるため、明示的に除かなければ
    「締結座 1 箇所」「高さ 1mm」として黙って通る。
    """
    document = _shipped_document()
    document[component][field_name] = value
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert field_name in str(excinfo.value)


def test_integer_is_accepted_where_a_float_is_expected(tmp_path: Path) -> None:
    """`220` と `220.0` は同じ値である（JSON の書式差を値の差にしない）。"""
    document = _shipped_document()
    document["trash_can"]["opening_inner_diameter_mm"] = 220
    params = load_params(_write(tmp_path, document))
    assert isinstance(params.trash_can.opening_inner_diameter_mm, float)
    assert params.trash_can.opening_inner_diameter_mm == 220.0


@pytest.mark.parametrize("component", ["trash_can", "provenance"])
def test_non_object_where_an_object_is_expected_is_rejected(tmp_path: Path, component: str) -> None:
    """オブジェクトを期待する位置のスカラを拒否する。"""
    document = _shipped_document()
    document[component] = 5
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert component in str(excinfo.value)


def test_non_object_document_is_rejected(tmp_path: Path) -> None:
    """最上位が配列などの場合を拒否する。"""
    with pytest.raises(ParameterError):
        load_params(_write(tmp_path, [1, 2, 3]))


def test_missing_file_is_rejected_as_a_parameter_error(tmp_path: Path) -> None:
    """ファイル未存在を `ParameterError` へ統一する（呼び出し側の捕捉を1つに）。"""
    missing = tmp_path / "no-such-file.json"
    with pytest.raises(ParameterError) as excinfo:
        load_params(missing)
    assert "no-such-file.json" in str(excinfo.value)


def test_malformed_json_is_rejected_as_a_parameter_error(tmp_path: Path) -> None:
    """JSON として解釈できない内容を `ParameterError` へ統一する。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError):
        load_params(broken)


def test_config_errors_are_catchable_as_value_error(tmp_path: Path) -> None:
    """設定の不正は `CatchMechanismError` / `ValueError` としても捕捉できる。"""
    document = _shipped_document()
    document["unknown_top_level"] = 1
    path = _write(tmp_path, document)
    with pytest.raises(CatchMechanismError):
        load_params(path)
    with pytest.raises(ValueError):
        load_params(path)


# ---------------------------------------------------------------------------
# 読み込み → 書き出し → 読み込み（design.md「Config」Invariants / 要件 6.6）
# ---------------------------------------------------------------------------


def test_round_trip_preserves_values_and_provenance(tmp_path: Path) -> None:
    """出荷ファイルの往復で値と出所が保存される。"""
    original = load_params()
    written = tmp_path / "round-trip.json"
    dump_params(original, written)
    assert load_params(written) == original


def test_round_trip_preserves_measured_provenance(tmp_path: Path) -> None:
    """採寸で `measured` へ更新した出所が往復で保存される（要件 6.6）。

    ⚠️ 往復で実測が仮値へ落ちれば、採寸の成果が黙って消える。逆に仮値が実測へ
    昇格すれば、未実測の値が実測を名乗る。どちらも起きてはならない。
    """
    document = _shipped_document()
    document["provenance"]["trash_can.opening_inner_diameter_mm"] = "measured"
    document["provenance"]["trash_can.height_mm"] = "measured"
    document["trash_can"]["opening_inner_diameter_mm"] = 218.4
    original = load_params(_write(tmp_path, document))

    written = tmp_path / "round-trip.json"
    dump_params(original, written)
    reloaded = load_params(written)

    assert reloaded == original
    assert reloaded.trash_can.opening_inner_diameter_mm == 218.4
    effective = _effective_provenance(reloaded)
    assert effective["trash_can.opening_inner_diameter_mm"] is Provenance.MEASURED
    assert effective["trash_can.height_mm"] is Provenance.MEASURED
    assert effective["trash_can.mass_g"] is Provenance.ASSUMED


def test_round_trip_is_byte_stable(tmp_path: Path) -> None:
    """2度目の書き出しが1度目と同一のバイト列になる（差分が揺れない）。"""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_params(load_params(), first)
    dump_params(load_params(first), second)
    assert first.read_bytes() == second.read_bytes()


def test_round_trip_from_a_reordered_compact_file(tmp_path: Path) -> None:
    """整形されていない入力からでも、往復で値と出所が保存される（要件 1.6）。"""
    document = _shipped_document()
    scrambled = dict(reversed(list(document.items())))
    source = _write(tmp_path, scrambled, sort_keys=False, indent=None)
    original = load_params(source)

    written = tmp_path / "normalized.json"
    dump_params(original, written)
    assert load_params(written) == original


def test_dump_writes_a_line_diffable_document(tmp_path: Path) -> None:
    """書き出しはインデント2・キー整列・末尾改行である（要件 1.7）。"""
    written = tmp_path / "dumped.json"
    dump_params(load_params(), written)
    text = written.read_text(encoding="utf-8")

    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    lines = text.splitlines()
    assert len(lines) > 40
    assert lines[1].startswith("  ")
    assert not lines[1].startswith("   ")

    # 最上位のキーが整列している（辞書の挿入順に依らない安定した並び）。
    document = json.loads(text)
    assert list(document) == sorted(document)
    for value in document.values():
        if isinstance(value, dict):
            assert list(value) == sorted(value)


def test_dump_writes_lf_line_endings(tmp_path: Path) -> None:
    """書き出しの改行は LF に固定する（実行プラットフォームに依存させない）。

    `.gitattributes` は `*.json` を作業ツリーで CRLF に固定するが、git は
    コミット時に LF へ正規化するため、LF で書き出しても差分は生じない。
    ⚠️ `os.linesep` 依存にすると、Windows と WSL で同じコマンドが別のバイト列を
    生み、差分が実行環境で揺れる。
    """
    written = tmp_path / "dumped.json"
    dump_params(load_params(), written)
    assert b"\r" not in written.read_bytes()


def test_dump_overwrites_an_existing_file(tmp_path: Path) -> None:
    """既存ファイルへの書き出しが残骸を残さない。"""
    written = tmp_path / "dumped.json"
    written.write_text("x" * 100_000, encoding="utf-8", newline="\n")
    dump_params(load_params(), written)
    assert load_params(written) == load_params()


# ---------------------------------------------------------------------------
# パラメータ識別子（要件 4.5 / design.md「Config」Postconditions）
# ---------------------------------------------------------------------------


def test_digest_format() -> None:
    """識別子は `sha256:<hex>` である。"""
    digest = parameters_digest(load_params())
    algorithm, _, hexdigest = digest.partition(":")
    assert algorithm == "sha256"
    assert len(hexdigest) == 64
    assert set(hexdigest) <= set("0123456789abcdef")


def test_digest_is_independent_of_formatting(tmp_path: Path) -> None:
    """同じ値なら書式に依らず識別子が一致する（タスク 1.4 の完了状態）。

    インデント・キーの並び順・改行コード・整数と小数の書き分けのいずれも
    値ではない。⚠️ ファイルのバイト列をそのままハッシュすると、ここが崩れる。
    """
    document = _shipped_document()
    canonical = load_params(_write(tmp_path, document, name="canonical.json"))

    scrambled_document = dict(reversed(list(document.items())))
    scrambled_document["trash_can"] = dict(reversed(list(document["trash_can"].items())))
    scrambled_document["trash_can"]["opening_inner_diameter_mm"] = 220
    scrambled_document["trash_can"]["taper_deg"] = 7e0
    scrambled = load_params(
        _write(
            tmp_path,
            scrambled_document,
            name="scrambled.json",
            newline="\r\n",
            sort_keys=False,
            indent=None,
        )
    )

    assert parameters_digest(scrambled) == parameters_digest(canonical)


def test_digest_ignores_explicit_versus_implicit_assumed(tmp_path: Path) -> None:
    """明示された `assumed` と、表に現れないパス（仮値扱い）は同じ状態である。

    ⚠️ ここを区別すると、意味の変わらない出所表の書き足しで識別子が動き、
    形状指標の記録が理由なく無効になる。
    """
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json"))

    document["provenance"]["rim.wall_thickness_mm"] = "assumed"
    document["provenance"]["joint.dowel_diameter_mm"] = "assumed"
    expanded = load_params(_write(tmp_path, document, name="expanded.json"))

    assert parameters_digest(expanded) == parameters_digest(baseline)


def test_digest_treats_negative_zero_as_zero(tmp_path: Path) -> None:
    """`-0.0` と `0.0` は同じ値である（書式差を値の差にしない）。"""
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json"))
    document["retention"]["added_depth_mm"] = -0.0
    negative_zero = load_params(_write(tmp_path, document, name="negative-zero.json"))
    assert parameters_digest(negative_zero) == parameters_digest(baseline)


@pytest.mark.parametrize(
    ("component", "field_name", "value"),
    [
        ("trash_can", "opening_inner_diameter_mm", 218.4),
        ("trash_can", "model_id", "some-other-model"),
        ("printing", "material", "PLA"),
        ("retention", "retrofit_fastener_count", 8),
    ],
)
def test_digest_changes_when_a_value_changes(
    tmp_path: Path, component: str, field_name: str, value: object
) -> None:
    """値が変われば識別子も変わる（要件 4.5 の検出手段）。"""
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json"))
    document[component][field_name] = value
    changed = load_params(_write(tmp_path, document, name="changed.json"))
    assert parameters_digest(changed) != parameters_digest(baseline)


def test_digest_changes_when_provenance_changes(tmp_path: Path) -> None:
    """出所が変われば識別子も変わる（要件 4.5）。

    値が同じでも仮値から実測へ変われば、記録済みの形状指標が「まだ仮値だった
    ときの記録」であることは変わらない。⚠️ 出所を識別子から外すと、この
    昇格が CAD 非導入環境では一切検出できなくなる。
    """
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json"))
    document["provenance"]["trash_can.opening_inner_diameter_mm"] = "measured"
    measured = load_params(_write(tmp_path, document, name="measured.json"))
    assert parameters_digest(measured) != parameters_digest(baseline)


def test_digest_is_stable_across_calls() -> None:
    """同じ入力に対して識別子が安定している（ハッシュの無作為化に依らない）。"""
    params = load_params()
    assert parameters_digest(params) == parameters_digest(load_params())


# ---------------------------------------------------------------------------
# 依存境界（要件 5.4 / design.md「Params」Implementation Notes）
# ---------------------------------------------------------------------------


def test_config_does_not_import_upstream_packages() -> None:
    """⚠️ `config` が上流パッケージを import しない（依存方向が逆になる）。

    `params` と同じ規律を `config` にも課す。静的に見るのは、実行時に
    import されない分岐の中へ紛れ込んでも捕まえるためである。
    """
    forbidden = {"trajectory_sim", "prediction_core", "sensing_foundation"}
    source = Path(config_module.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert imported & forbidden == set()
