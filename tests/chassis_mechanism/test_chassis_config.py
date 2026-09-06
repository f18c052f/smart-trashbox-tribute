"""寸法設定ファイルの読み書き・上流の取り込み・パラメータ識別子（タスク 1.4、
要件 1.1, 1.3, 1.4, 1.10）。

本ファイルが固定するのは design.md `#### Config` の Preconditions /
Postconditions / Invariants と、tasks.md タスク 1.4 の「観測可能な完了状態」
——「未知キーを1つ足した設定が項目名つきで拒否され、読み書きの往復で値と出所が
保存され、観測記録を書き換えても識別子が動かないこと」——である。

1. **`configs/chassis_mechanism/dimensions.json` が本 Spec 固有の寸法の単一の正**
   であること（要件 1.1）。⚠️ **上流が持つ値をここへ複製しない**（要件 1.3）。
   造形制約・継手方針・ゴミ箱の採寸値は実行時に `catch_mechanism` の公開 API
   から取り込む
2. **あらゆる階層で未知キーを拒否する**こと（要件 1.4 / design.md `#### Config`
   Responsibilities）。最上位・コンポーネント・出所表のどこに混ぜても、
   該当する項目名を示して失敗する
3. **欠損・型不正・範囲外を項目名つきで拒否する**こと（要件 1.4）
4. **出所表のキー集合がパラメータパス表と一致する**こと（design.md
   `#### Params` Invariants）。⚠️ 上流と違い、本 Spec は**欠損も拒否する**——
   `ChassisParams` 自身が全パスの出所を要求するためである
5. **読み込み → 書き出し → 読み込みで値と出所が保存される**こと（design.md
   `#### Config` Postconditions）。整形は LF・キー整列・末尾改行に固定し、
   変更が行単位の差分として読める（要件 1.10）
6. **識別子は `ChassisParams` だけの純関数である**こと。⚠️ **組立後の観測
   （`measurements.json`、タスク 2.4）を含めない**——観測のたびに形状の再生成が
   要求されてはならない（design.md「Logical Data Model」）
7. **上流の下限との突き合わせが読み込み経路で必ず働く**こと（要件 2.9 /
   design.md `#### Params` Implementation Notes）。⚠️ **呼ばれない検証は何も
   拒否しない**ため、`ResolvedParams` を作る経路が1つだけであることを静的にも
   固定する
8. **上流への書き戻しは値と出所だけ**であること（要件 6.4 / design.md
   `#### Config` Invariants）。⚠️ 試験は必ず `tmp_path` の複製に対して行い、
   実物の `configs/catch_mechanism/dimensions.json` へ書かない

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名が
セッション全体でフラットであるため、全ファイルに `test_chassis_` 接頭辞を付ける
（design.md「Directory Structure」）。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from catch_mechanism import JointPolicy, PrintingConstraints, TrashCanMeasurements
from catch_mechanism import load_params as upstream_load_params

from chassis_mechanism import config as config_module
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    UPSTREAM_DIMENSIONS_PATH,
    ResolvedParams,
    dump_params,
    load_params,
    parameters_digest,
    update_upstream_measurement,
)
from chassis_mechanism.errors import ChassisMechanismError, ParameterError
from chassis_mechanism.params import PARAMETER_PATHS, ChassisParams

# ---------------------------------------------------------------------------
# ヘルパ
#
# テスト側で値表を作らない。すべて出荷される `dimensions.json` を読み、必要な
# 1項目だけを差し替える形に統一する（値の二重管理を避けるため）。
# ---------------------------------------------------------------------------

#: `ChassisParams` 直下のコンポーネント名（パス表から導く。手書きしない）。
COMPONENTS: tuple[str, ...] = tuple(
    sorted({spec.component for spec in PARAMETER_PATHS.values()})
)

#: 上流 `catch_mechanism` が所有するコンポーネント名。
#: ⚠️ **本 Spec の設定ファイルにこれらが現れてはならない**（要件 1.3）。
UPSTREAM_COMPONENTS: frozenset[str] = frozenset(
    {"trash_can", "target_object", "printing", "joint", "rim", "retention"}
)

#: 出荷時点で**実測**として記録されるパス（要件 1.9 / requirements.md A-8, A-9）。
#:
#: ⚠️ **A-9 が挙げる確定済みの値のうち、実測を名乗れるのは付属ブラケットの4値
#: だけである。** ホイールとハブは**非純正品**（A-8）であり、図面・メーカー資料
#: から取った公称寸法は「実測で置き換えられていない非純正部品の公称寸法」
#: そのものであるため、要件 1.9 により**仮値**として扱う。モータの外形も
#: 商品仕様からの転記であり実測ではない（`docs/drivetrain-spec.md §3`）。
#: 本 Spec がこれから測るのは、まさにこの仮値の側である。
MEASURED_PATHS: frozenset[str] = frozenset(
    {
        "bracket.outline_x_mm",
        "bracket.outline_y_mm",
        "bracket.mount_face_to_contact_mm",
        "bracket.mount_face_to_wheel_center_mm",
    }
)


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
    target.write_text(
        json.dumps(document, **kwargs) + "\n", encoding="utf-8", newline=newline
    )
    return target


def _config_source_tree() -> ast.Module:
    """`config.py` の構文木を返す（構築箇所の静的な数え上げに用いる）。"""
    return ast.parse(Path(config_module.__file__).read_text(encoding="utf-8"))


def _construction_sites(name: str) -> list[str]:
    """`config.py` の中で `name(...)` を構築している関数名を並べる。

    ⚠️ **構築箇所が1つであることは検証の要である。** 2箇所目ができた時点で、
    上流との突き合わせを通らない `ResolvedParams` を作れてしまう。
    """
    sites: list[str] = []
    for function in ast.walk(_config_source_tree()):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == name
            ):
                sites.append(function.name)
    return sites


def _method_calls_in(function_name: str) -> set[str]:
    """`config.py` の関数 `function_name` が呼ぶメソッド名の集合を返す。"""
    called: set[str] = set()
    for function in ast.walk(_config_source_tree()):
        if not isinstance(function, ast.FunctionDef) or function.name != function_name:
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    return called


def _upstream_copy(tmp_path: Path) -> Path:
    """上流の設定ファイルを `tmp_path` へ複製する（書き戻し試験の土台）。

    ⚠️ **書き戻しの試験は必ずこの複製に対して行う。** 実物へ書くと、テストが
    リポジトリの成果物を書き換えることになる（要件 6.4 の検証が要件 6.4 の
    違反になる）。
    """
    target = tmp_path / "upstream-dimensions.json"
    target.write_bytes(UPSTREAM_DIMENSIONS_PATH.read_bytes().replace(b"\r\n", b"\n"))
    return target


# ---------------------------------------------------------------------------
# 単一の正としての `configs/chassis_mechanism/dimensions.json`（要件 1.1）
# ---------------------------------------------------------------------------


def test_default_path_points_at_the_single_source_of_truth() -> None:
    """既定パスがリポジトリの設定ファイルを指す。"""
    assert DEFAULT_DIMENSIONS_PATH.name == "dimensions.json"
    assert DEFAULT_DIMENSIONS_PATH.parent.name == "chassis_mechanism"
    assert DEFAULT_DIMENSIONS_PATH.parent.parent.name == "configs"
    assert DEFAULT_DIMENSIONS_PATH.is_file()


def test_upstream_path_points_at_the_upstream_source_of_truth() -> None:
    """上流への書き戻し先が上流の設定ファイルを指す（要件 6.3 の経路）。"""
    assert UPSTREAM_DIMENSIONS_PATH.name == "dimensions.json"
    assert UPSTREAM_DIMENSIONS_PATH.parent.name == "catch_mechanism"
    assert UPSTREAM_DIMENSIONS_PATH.is_file()


def test_load_without_arguments_reads_the_default_path() -> None:
    """引数なしの読み込みが既定パスの読み込みと一致する。"""
    assert load_params() == load_params(DEFAULT_DIMENSIONS_PATH)


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


def test_shipped_provenance_keys_match_the_parameter_path_table() -> None:
    """出所表のキー集合がパラメータパス表と**一致**する（design.md Invariants）。"""
    document = _shipped_document()
    assert set(document["provenance"]) == set(PARAMETER_PATHS)


def test_shipped_provenance_names_exactly_the_measured_paths() -> None:
    """実測を名乗るのは付属ブラケットの4値だけである（要件 1.9 / A-8, A-9）。

    ⚠️ **非純正部品の公称寸法を実測に格上げしない。** ホイール Ø60 も
    ハブ内径 Ø6 も図面・メーカー資料の値であり、本 Spec がこれから測る対象
    そのものである。ここが緩むと、公称値の上に載った設計が「実測に基づく」と
    主張してしまう。
    """
    document = _shipped_document()
    measured = {
        path for path, value in document["provenance"].items() if value == "measured"
    }
    assert measured == set(MEASURED_PATHS)


def test_shipped_bracket_reference_face_is_still_assumed() -> None:
    """基準面の定義は**未確認**であり仮値である（design.md `#### Layout` Risks）。

    ⚠️ 取付面 → ホイール中心 33.9mm は測れているが、その 33.9mm が軸方向で
    あることは確認待ちである（`docs/bom.md §B`）。この不確かさは
    `mount_face_reference` の出所として記録され、`derive_layout`（タスク 2.1）が
    `base_radius_mm` の出所を仮値に保つための唯一の手掛かりになる。
    """
    document = _shipped_document()
    assert document["provenance"]["bracket.mount_face_reference"] == "assumed"
    assert document["bracket"]["mount_face_reference"].strip()


def test_shipped_document_does_not_duplicate_upstream_components() -> None:
    """⚠️ 上流が所有する値を本 Spec のファイルへ複製しない（要件 1.3）。

    造形制約・継手方針・ゴミ箱の採寸値は**実行時に上流から取り込む**。同じ値が
    2箇所にあれば、いつか食い違う。
    """
    document = _shipped_document()
    assert set(document) & UPSTREAM_COMPONENTS == set()
    assert {spec.component for spec in PARAMETER_PATHS.values()} & UPSTREAM_COMPONENTS == set()


def test_shipped_document_has_the_schema_version_outside_the_parameters() -> None:
    """`schema_version` は記録形式の版であってパラメータではない。"""
    document = _shipped_document()
    assert document["schema_version"] == SCHEMA_VERSION
    assert "schema_version" not in PARAMETER_PATHS


def test_shipped_document_is_written_in_the_canonical_dump_format(tmp_path: Path) -> None:
    """出荷ファイルが `dump_params` の出力そのものである（要件 1.10）。

    手編集で並び順や整形が崩れると、以降の書き出しが巨大な差分を生む。
    ⚠️ 改行は比較前に正規化する——作業ツリーの状態に依らず本文を比べるため。
    """
    regenerated = tmp_path / "dimensions.json"
    dump_params(load_params().chassis, regenerated)
    shipped = DEFAULT_DIMENSIONS_PATH.read_bytes().replace(b"\r\n", b"\n")
    assert shipped == regenerated.read_bytes()


def test_shipped_document_is_line_diffable() -> None:
    """1項目 = 1行であり、末尾改行を持つ（要件 1.10）。"""
    text = DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8")
    assert text.endswith("\n")
    # 全パラメータ + 同数の出所表 + `schema_version` + 括弧。1行 JSON で
    # 保存されていれば行単位の差分は読めない。⚠️ 下限をパラメータ数から
    # 導くことで、項目が黙って減った場合もここで落ちる。
    assert len(text.splitlines()) >= 2 * len(PARAMETER_PATHS)


def test_shipped_document_is_stored_with_lf_line_endings() -> None:
    """出荷ファイルの改行が LF である（`.gitattributes` の例外行と対で成立）。"""
    assert b"\r" not in DEFAULT_DIMENSIONS_PATH.read_bytes()


# ---------------------------------------------------------------------------
# 上流の取り込み（design.md `#### Config` Service Interface）
# ---------------------------------------------------------------------------


def test_load_params_resolves_the_upstream_values() -> None:
    """上流の造形制約・継手方針・ゴミ箱の採寸値を公開 API 経由で取り込む。"""
    resolved = load_params()
    upstream = upstream_load_params()
    assert isinstance(resolved, ResolvedParams)
    assert isinstance(resolved.chassis, ChassisParams)
    assert isinstance(resolved.printing, PrintingConstraints)
    assert isinstance(resolved.joint, JointPolicy)
    assert isinstance(resolved.trash_can, TrashCanMeasurements)
    assert resolved.printing == upstream.printing
    assert resolved.joint == upstream.joint
    assert resolved.trash_can == upstream.trash_can


def test_resolved_params_is_immutable() -> None:
    """`ResolvedParams` は凍結されている（取り込んだ値を後から差し替えない）。"""
    resolved = load_params()
    with pytest.raises((AttributeError, TypeError)):
        resolved.chassis = resolved.chassis  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 上流の下限との突き合わせ（要件 2.9 / タスク 1.3 からの持ち越し）
#
# ⚠️ **呼ばれない検証は何も拒否しない。** `LocalJointLimits.validate_against_upstream`
# は `params` 側に置かれているが、上流の下限を知るのは `config` だけであるため、
# 読み込み経路が必ずこれを呼ぶことをここで固定する。
# ---------------------------------------------------------------------------


def test_bearing_area_below_the_upstream_floor_is_rejected(tmp_path: Path) -> None:
    """上流の下限を下回る当たり面の下限を、**両方の値**を示して拒否する。

    本 Spec は上流の下限を**厳しくできるが緩められない**（design.md 決定 3）。
    どちらへ寄せればよいかが分からなければ設定ファイルを直せないため、
    メッセージは自分の値と上流の値の両方を持つ。
    """
    upstream_floor = upstream_load_params().joint.min_bearing_area_mm2
    document = _shipped_document()
    below = upstream_floor / 2.0
    document["joint_local"]["min_bearing_area_mm2"] = below
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert repr(below) in message
    assert repr(upstream_floor) in message
    assert "min_bearing_area_mm2" in message


def test_bearing_area_at_the_upstream_floor_is_accepted(tmp_path: Path) -> None:
    """上流の下限ちょうどは通る（境界を誤って弾かない）。"""
    upstream_floor = upstream_load_params().joint.min_bearing_area_mm2
    document = _shipped_document()
    document["joint_local"]["min_bearing_area_mm2"] = upstream_floor
    resolved = load_params(_write(tmp_path, document))
    assert resolved.chassis.joint_local.min_bearing_area_mm2 == upstream_floor


def test_shipped_bearing_area_is_at_least_the_upstream_floor() -> None:
    """出荷ファイル自身が上流の下限を満たす（出荷状態が検証を通る）。"""
    resolved = load_params()
    assert (
        resolved.chassis.joint_local.min_bearing_area_mm2
        >= resolved.joint.min_bearing_area_mm2
    )


def test_resolved_params_is_constructed_in_exactly_one_place() -> None:
    """`ResolvedParams` の構築箇所は1つだけである（迂回路を作らない）。"""
    assert _construction_sites("ResolvedParams") == ["_resolve_params"]


def test_chassis_params_is_constructed_in_exactly_one_place() -> None:
    """`ChassisParams` の構築箇所も1つだけである（検証の抜け道を塞ぐ）。"""
    assert _construction_sites("ChassisParams") == ["_build_chassis_params"]


def test_the_single_resolution_site_validates_against_upstream() -> None:
    """唯一の構築箇所が上流との突き合わせを呼ぶ（要件 2.9）。"""
    assert "validate_against_upstream" in _method_calls_in("_resolve_params")


# ---------------------------------------------------------------------------
# あらゆる階層での未知キーの拒否（要件 1.4）
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    """最上位の未知キーを、項目名を示して拒否する。"""
    document = _shipped_document()
    document["trash_can"] = {"height_mm": 235.0}
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "trash_can" in str(excinfo.value)


@pytest.mark.parametrize("component", COMPONENTS)
def test_unknown_key_is_rejected_in_every_component(tmp_path: Path, component: str) -> None:
    """未知キーの拒否がコンポーネントごとに漏れていない（あらゆる階層）。"""
    document = _shipped_document()
    document[component]["surely_unknown_mm"] = 1.0
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "surely_unknown_mm" in message
    assert component in message


def test_unknown_provenance_key_is_rejected(tmp_path: Path) -> None:
    """出所表の未知キーを、項目名を示して拒否する。

    ⚠️ 黙って無視すると、実測したつもりの値が仮値のまま残る。
    """
    document = _shipped_document()
    document["provenance"]["bracket.mount_face_thickness_mm"] = "measured"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "bracket.mount_face_thickness_mm" in str(excinfo.value)


def test_provenance_key_naming_a_component_only_is_rejected(tmp_path: Path) -> None:
    """コンポーネント名だけの出所キー（リーフでない）も拒否する。"""
    document = _shipped_document()
    document["provenance"]["bracket"] = "measured"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "bracket" in str(excinfo.value)


def test_upstream_provenance_key_is_rejected(tmp_path: Path) -> None:
    """上流のパスを出所表へ書くことも拒否する（要件 1.3）。"""
    document = _shipped_document()
    document["provenance"]["trash_can.bottom_outer_diameter_mm"] = "measured"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "trash_can.bottom_outer_diameter_mm" in str(excinfo.value)


def test_unknown_provenance_value_is_rejected(tmp_path: Path) -> None:
    """出所の値は `measured` / `assumed` の2値のみ（第3の値を作らない）。"""
    document = _shipped_document()
    document["provenance"]["wheel.mass_g"] = "derived"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "derived" in message
    assert "wheel.mass_g" in message


# ---------------------------------------------------------------------------
# 欠損・型不正・範囲外の拒否（要件 1.4）
# ---------------------------------------------------------------------------


def test_missing_provenance_entry_is_rejected(tmp_path: Path) -> None:
    """出所表の欠損を、項目名を示して拒否する（キー集合の**一致**を要求する）。

    ⚠️ 上流は表に現れないパスを仮値として扱うが、本 Spec は
    `ChassisParams` が全パスの出所を要求するため**欠損も拒否する**。
    出所を持たない寸法値が設計へ流れることを許さない。
    """
    document = _shipped_document()
    del document["provenance"]["stand.leg_count"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "stand.leg_count" in str(excinfo.value)


def test_missing_provenance_table_is_rejected(tmp_path: Path) -> None:
    """出所表そのものの欠落を拒否する（節ごと消して「全部仮値」にできない）。"""
    document = _shipped_document()
    del document["provenance"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "provenance" in str(excinfo.value)


def test_missing_component_is_rejected(tmp_path: Path) -> None:
    """コンポーネントの欠落を、項目名を示して拒否する。"""
    document = _shipped_document()
    del document["stand"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "stand" in str(excinfo.value)


def test_missing_leaf_value_is_rejected(tmp_path: Path) -> None:
    """リーフの欠落を、項目名を示して拒否する（既定値で埋めない）。"""
    document = _shipped_document()
    del document["wheel"]["width_mm"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "width_mm" in str(excinfo.value)


def test_missing_schema_version_is_rejected(tmp_path: Path) -> None:
    """記録形式の版の欠落を拒否する。"""
    document = _shipped_document()
    del document["schema_version"]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "schema_version" in str(excinfo.value)


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """未対応の記録形式の版を拒否する。"""
    document = _shipped_document()
    document["schema_version"] = "9.9"
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "9.9" in str(excinfo.value)


@pytest.mark.parametrize(
    ("component", "field_name", "value"),
    [
        ("wheel", "width_mm", "25.6"),
        ("wheel", "mount_hole_count", 6.5),
        ("hub", "set_screw_designation", 4),
        ("motor", "shaft_flat_present", 1),
        ("base", "wheel_count", True),
        ("clearance", "min_ground_clearance_mm", True),
    ],
)
def test_wrong_value_type_is_rejected(
    tmp_path: Path, component: str, field_name: str, value: object
) -> None:
    """値の型違いを、項目名を示して拒否する。

    ⚠️ `bool` を数値・整数として通さない。Python の `bool` は `int` の派生で
    あるため、明示的に除かなければ JSON の `true` が「輪 1個」「隙間 1mm」と
    して黙って通る。
    """
    document = _shipped_document()
    document[component][field_name] = value
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert field_name in str(excinfo.value)


def test_null_is_rejected_where_a_value_is_required(tmp_path: Path) -> None:
    """未決を許さない項目の `null` を拒否する（寸法に「未決」は無い）。"""
    document = _shipped_document()
    document["battery"]["mass_g"] = None
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "mass_g" in str(excinfo.value)


def test_null_is_accepted_where_the_decision_is_pending(tmp_path: Path) -> None:
    """電源系の未決は `null` として読める（もっともらしい既定値で埋めない）。"""
    document = _shipped_document()
    document["power"]["main_switch_height_mm"] = None
    resolved = load_params(_write(tmp_path, document))
    assert resolved.chassis.power.main_switch_height_mm is None


def test_decided_power_value_is_accepted(tmp_path: Path) -> None:
    """決着した電源系の値は素直に読める（未決だけの型ではない）。"""
    document = _shipped_document()
    document["power"]["main_switch_present"] = True
    document["power"]["main_switch_position"] = "基板トレイの側面"
    document["power"]["main_switch_height_mm"] = 120.0
    resolved = load_params(_write(tmp_path, document))
    assert resolved.chassis.power.main_switch_position == "基板トレイの側面"
    assert resolved.chassis.power.main_switch_height_mm == 120.0


def test_integer_is_accepted_where_a_float_is_expected(tmp_path: Path) -> None:
    """`60` と `60.0` は同じ値である（整数表記を型違いとして弾かない）。"""
    document = _shipped_document()
    document["bracket"]["mount_face_to_contact_mm"] = 60
    resolved = load_params(_write(tmp_path, document))
    assert resolved.chassis.bracket.mount_face_to_contact_mm == 60.0


def test_out_of_range_value_is_rejected(tmp_path: Path) -> None:
    """物理的にあり得ない値を、項目名と値を示して拒否する（要件 1.4）。"""
    document = _shipped_document()
    document["battery"]["height_mm"] = -1.0
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    message = str(excinfo.value)
    assert "height_mm" in message
    assert "-1.0" in message


def test_cross_field_invariant_violation_is_rejected(tmp_path: Path) -> None:
    """群をまたぐ大小関係の違反も読み込み経路で拒否される。"""
    document = _shipped_document()
    document["wheel"]["bolt_circle_diameter_mm"] = (
        document["wheel"]["nominal_diameter_mm"] + 10.0
    )
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "bolt_circle_diameter_mm" in str(excinfo.value)


def test_leg_count_mismatch_is_rejected(tmp_path: Path) -> None:
    """脚の数と輪の数の食い違いも読み込み経路で拒否される（決定 5）。"""
    document = _shipped_document()
    document["stand"]["leg_count"] = document["base"]["wheel_count"] + 1
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert "leg_count" in str(excinfo.value)


@pytest.mark.parametrize("component", COMPONENTS)
def test_non_object_where_an_object_is_expected_is_rejected(
    tmp_path: Path, component: str
) -> None:
    """コンポーネントが対応表でない場合を項目名つきで拒否する。"""
    document = _shipped_document()
    document[component] = [1, 2, 3]
    with pytest.raises(ParameterError) as excinfo:
        load_params(_write(tmp_path, document))
    assert component in str(excinfo.value)


def test_non_object_document_is_rejected(tmp_path: Path) -> None:
    """最上位が JSON オブジェクトでない場合を拒否する。"""
    with pytest.raises(ParameterError):
        load_params(_write(tmp_path, [1, 2, 3]))


def test_missing_file_is_rejected_as_a_parameter_error(tmp_path: Path) -> None:
    """ファイル未存在を `ParameterError` へ統一する。"""
    missing = tmp_path / "absent.json"
    with pytest.raises(ParameterError) as excinfo:
        load_params(missing)
    assert "absent.json" in str(excinfo.value)


def test_malformed_json_is_rejected_as_a_parameter_error(tmp_path: Path) -> None:
    """JSON として解析できない内容を `ParameterError` へ統一する。"""
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError):
        load_params(broken)


def test_config_errors_are_catchable_as_value_error(tmp_path: Path) -> None:
    """読み込みの失敗は本 Spec の階層でも `ValueError` でも捕まえられる。"""
    document = _shipped_document()
    document["surely_unknown"] = 1
    target = _write(tmp_path, document)
    with pytest.raises(ChassisMechanismError):
        load_params(target)
    with pytest.raises(ValueError):
        load_params(target)


# ---------------------------------------------------------------------------
# 読み書きの往復（design.md `#### Config` Postconditions / 要件 1.10）
# ---------------------------------------------------------------------------


def test_round_trip_preserves_values_and_provenance(tmp_path: Path) -> None:
    """読み込み → 書き出し → 読み込みで値と出所が保存される。"""
    original = load_params().chassis
    written = tmp_path / "dimensions.json"
    dump_params(original, written)
    assert load_params(written).chassis == original
    assert load_params(written).chassis.provenance == original.provenance


def test_round_trip_is_byte_stable(tmp_path: Path) -> None:
    """同じ値を2度書き出すと同じバイト列になる（空の差分を生まない）。"""
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    dump_params(load_params().chassis, first)
    dump_params(load_params(first).chassis, second)
    assert first.read_bytes() == second.read_bytes()


def test_round_trip_from_a_reordered_compact_file(tmp_path: Path) -> None:
    """並びの崩れた1行 JSON からでも、書き出しは正規の整形へ戻る。"""
    document = _shipped_document()
    scrambled = dict(reversed(list(document.items())))
    source = _write(
        tmp_path, scrambled, name="scrambled.json", sort_keys=False, indent=None
    )
    written = tmp_path / "normalized.json"
    dump_params(load_params(source).chassis, written)
    assert written.read_bytes() == DEFAULT_DIMENSIONS_PATH.read_bytes().replace(
        b"\r\n", b"\n"
    )


def test_dump_writes_a_line_diffable_document(tmp_path: Path) -> None:
    """書き出しがインデント2・キー整列・末尾改行である（要件 1.10）。"""
    written = tmp_path / "dumped.json"
    dump_params(load_params().chassis, written)
    text = written.read_text(encoding="utf-8")
    assert text.endswith("\n")
    lines = text.splitlines()
    assert lines[0] == "{"
    assert lines[1].startswith("  ")
    assert not lines[1].startswith("   ")
    document = json.loads(text)
    assert list(document) == sorted(document)
    for value in document.values():
        if isinstance(value, dict):
            assert list(value) == sorted(value)


def test_dump_writes_lf_line_endings(tmp_path: Path) -> None:
    """書き出しの改行は LF に固定する（実行プラットフォームに依存させない）。"""
    written = tmp_path / "dumped.json"
    dump_params(load_params().chassis, written)
    assert b"\r" not in written.read_bytes()


def test_dump_overwrites_an_existing_file(tmp_path: Path) -> None:
    """既存ファイルへの書き出しが残骸を残さない。"""
    written = tmp_path / "dumped.json"
    written.write_text("x" * 100_000, encoding="utf-8", newline="\n")
    dump_params(load_params().chassis, written)
    assert load_params(written).chassis == load_params().chassis


# ---------------------------------------------------------------------------
# パラメータ識別子（design.md `#### Config` Postconditions）
# ---------------------------------------------------------------------------


def test_digest_format() -> None:
    """識別子は `sha256:<hex>` である。"""
    digest = parameters_digest(load_params().chassis)
    algorithm, _, hexdigest = digest.partition(":")
    assert algorithm == "sha256"
    assert len(hexdigest) == 64
    assert set(hexdigest) <= set("0123456789abcdef")


def test_digest_is_pinned_to_a_stable_literal() -> None:
    """出荷される寸法の識別子を文字列として固定する。

    ⚠️ **識別子の入力が黙って広がらないための錨である。** 組立後の観測や
    記録形式の版が入力へ紛れ込めば、値を1つも変えていないのにこの値が動く。
    ⚠️ 設定ファイルの値または出所を意図して変えたときは、この定数も併せて
    更新する（識別子が動くこと自体は正しい振る舞いである）。
    """
    assert (
        parameters_digest(load_params().chassis)
        == "sha256:49894ea934f3c67b29ffba0a39a74f587aca23533a64e2705bd8c54b59467081"
    )


def test_digest_takes_only_the_local_dimension_parameters() -> None:
    """識別子は `ChassisParams` だけの純関数である（観測を含めない）。

    ⚠️ 引数がこの1つであることが、「観測のたびに形状の再生成が要求される」
    事態を型の上で防いでいる（design.md「Logical Data Model」）。
    """
    assert list(inspect.signature(parameters_digest).parameters) == ["params"]


def test_digest_is_unmoved_by_a_post_assembly_observation_file(tmp_path: Path) -> None:
    """観測記録を書き換えても識別子が動かない（タスク 1.4 の完了状態）。

    `measurements.json`（タスク 2.4）は組立後の観測であり、寸法パラメータでは
    ない。⚠️ ここが動くと、機体を測り直すたびに形状の再生成が要求される。
    """
    target = _write(tmp_path, _shipped_document())
    before = parameters_digest(load_params(target).chassis)
    observations = tmp_path / "measurements.json"
    observations.write_text(
        json.dumps({"mass_g": 2100.0, "cog_height_mm": 70.0}, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert parameters_digest(load_params(target).chassis) == before
    observations.write_text(
        json.dumps({"mass_g": 2145.0, "cog_height_mm": 68.5}, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    assert parameters_digest(load_params(target).chassis) == before


def test_digest_is_independent_of_formatting(tmp_path: Path) -> None:
    """同じ値なら書式に依らず識別子が一致する（タスク 1.4 の完了状態）。

    インデント・キーの並び順・改行コード・整数と小数の書き分けのいずれも
    値ではない。⚠️ ファイルのバイト列をそのままハッシュすると、ここが崩れる。
    """
    document = _shipped_document()
    canonical = load_params(_write(tmp_path, document, name="canonical.json")).chassis

    scrambled_document = dict(reversed(list(document.items())))
    scrambled_document["wheel"] = dict(reversed(list(document["wheel"].items())))
    scrambled_document["wheel"]["nominal_diameter_mm"] = 60
    scrambled_document["bracket"] = dict(document["bracket"])
    scrambled_document["bracket"]["mount_face_to_contact_mm"] = 6.0e1
    scrambled = load_params(
        _write(
            tmp_path,
            scrambled_document,
            name="scrambled.json",
            newline="\r\n",
            sort_keys=False,
            indent=None,
        )
    ).chassis

    assert parameters_digest(scrambled) == parameters_digest(canonical)


def test_digest_treats_negative_zero_as_zero(tmp_path: Path) -> None:
    """`-0.0` と `0.0` は同じ値である（書式差を値の差にしない）。"""
    document = _shipped_document()
    document["clearance"]["cable_lowest_offset_mm"] = 0.0
    baseline = load_params(_write(tmp_path, document, name="baseline.json")).chassis
    document["clearance"]["cable_lowest_offset_mm"] = -0.0
    negative_zero = load_params(
        _write(tmp_path, document, name="negative-zero.json")
    ).chassis
    assert parameters_digest(negative_zero) == parameters_digest(baseline)


@pytest.mark.parametrize(
    ("component", "field_name", "value"),
    [
        ("wheel", "nominal_diameter_mm", 59.4),
        ("bracket", "mount_face_reference", "別の基準面の記述"),
        ("base", "wheel_count", 4),
        ("motor", "shaft_flat_present", False),
        ("power", "main_switch_height_mm", 118.0),
        ("joint_local", "min_bearing_area_mm2", 120.0),
    ],
)
def test_digest_changes_when_a_value_changes(
    tmp_path: Path, component: str, field_name: str, value: object
) -> None:
    """値が変われば識別子も変わる（形状指標の更新漏れを捕まえる手段）。"""
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json")).chassis
    document[component][field_name] = value
    if component == "base" and field_name == "wheel_count":
        # 脚の数は輪の数と一致しなければならない（`ChassisParams` の不変条件）。
        document["stand"]["leg_count"] = value
    changed = load_params(_write(tmp_path, document, name="changed.json")).chassis
    assert parameters_digest(changed) != parameters_digest(baseline)


def test_digest_distinguishes_a_pending_decision_from_a_value(tmp_path: Path) -> None:
    """未決（`null`）と値のある状態は別の状態である。"""
    document = _shipped_document()
    document["power"]["fuse_holder_position"] = None
    pending = load_params(_write(tmp_path, document, name="pending.json")).chassis
    document["power"]["fuse_holder_position"] = "バッテリ直近（端子台より上流）"
    decided = load_params(_write(tmp_path, document, name="decided.json")).chassis
    assert parameters_digest(decided) != parameters_digest(pending)


def test_digest_changes_when_provenance_changes(tmp_path: Path) -> None:
    """出所が変われば識別子も変わる（要件 1.9 の昇格を見逃さない）。

    値が同じでも仮値から実測へ変われば、記録済みの形状指標が「まだ仮値だった
    ときの記録」であることは変わらない。
    """
    document = _shipped_document()
    baseline = load_params(_write(tmp_path, document, name="baseline.json")).chassis
    assert document["provenance"]["wheel.nominal_diameter_mm"] == "assumed"
    document["provenance"]["wheel.nominal_diameter_mm"] = "measured"
    measured = load_params(_write(tmp_path, document, name="measured.json")).chassis
    assert parameters_digest(measured) != parameters_digest(baseline)


def test_digest_is_stable_across_calls() -> None:
    """同じ入力に対して識別子が安定している（ハッシュの無作為化に依らない）。"""
    params = load_params().chassis
    assert parameters_digest(params) == parameters_digest(load_params().chassis)


# ---------------------------------------------------------------------------
# 上流への書き戻し（要件 6.3, 6.4 / design.md `#### Config` Invariants）
#
# ⚠️ すべて `tmp_path` の複製に対して行う。実物の
# `configs/catch_mechanism/dimensions.json` へは書かない。
# ---------------------------------------------------------------------------


def test_update_upstream_measurement_changes_only_the_value_and_its_provenance(
    tmp_path: Path,
) -> None:
    """値と出所の該当行だけを書き戻す（要件 6.4）。

    ⚠️ **構造・キー名・単位に触れない。** 上流の設定ファイルは上流のもので
    あり、本 Spec が書いてよいのは「測り直した値」と「仮値から実測へ」の2点
    だけである。
    """
    target = _upstream_copy(tmp_path)
    before = json.loads(target.read_text(encoding="utf-8"))
    path_key = "trash_can.bottom_flat_diameter_mm"
    component, _, field_name = path_key.partition(".")
    new_value = before[component][field_name] + 1.5

    update_upstream_measurement(path_key, new_value, path=target)

    after = json.loads(target.read_text(encoding="utf-8"))
    assert list(after) == list(before)
    for key in before:
        if key in {component, "provenance"}:
            continue
        assert after[key] == before[key]
    assert list(after[component]) == list(before[component])
    for name, value in before[component].items():
        if name == field_name:
            assert after[component][name] == new_value
        else:
            assert after[component][name] == value
    assert set(after["provenance"]) == set(before["provenance"])
    for key, value in before["provenance"].items():
        expected = "measured" if key == path_key else value
        assert after["provenance"][key] == expected


def test_update_upstream_measurement_result_is_loadable_by_upstream(
    tmp_path: Path,
) -> None:
    """書き戻した結果を上流自身が読み直せる（上流の `dump_params` を通す）。"""
    target = _upstream_copy(tmp_path)
    path_key = "trash_can.bottom_flat_diameter_mm"
    original = upstream_load_params(target)
    update_upstream_measurement(path_key, 168.4, path=target)
    updated = upstream_load_params(target)
    assert updated.trash_can.bottom_flat_diameter_mm == 168.4
    assert updated.trash_can.height_mm == original.trash_can.height_mm
    assert updated.printing == original.printing
    assert updated.joint == original.joint


def test_update_upstream_measurement_writes_lf_line_endings(tmp_path: Path) -> None:
    """書き戻しの改行も LF である（`.gitattributes` の例外行と対で成立）。"""
    target = _upstream_copy(tmp_path)
    update_upstream_measurement("trash_can.bottom_flat_diameter_mm", 168.4, path=target)
    assert b"\r" not in target.read_bytes()


def test_update_upstream_measurement_leaves_the_real_upstream_config_untouched(
    tmp_path: Path,
) -> None:
    """複製への書き戻しが実物へ及ばない（既定パスを誤って掴まない）。"""
    original = UPSTREAM_DIMENSIONS_PATH.read_bytes()
    target = _upstream_copy(tmp_path)
    update_upstream_measurement("trash_can.bottom_flat_diameter_mm", 169.9, path=target)
    assert UPSTREAM_DIMENSIONS_PATH.read_bytes() == original


def test_update_upstream_measurement_rejects_an_unknown_path(tmp_path: Path) -> None:
    """上流のパラメータパス表に無いパスを、項目名を示して拒否する。"""
    target = _upstream_copy(tmp_path)
    with pytest.raises(ParameterError) as excinfo:
        update_upstream_measurement("trash_can.lid_diameter_mm", 1.0, path=target)
    assert "trash_can.lid_diameter_mm" in str(excinfo.value)


def test_update_upstream_measurement_rejects_a_local_path(tmp_path: Path) -> None:
    """本 Spec のパスを上流へ書き戻そうとする誤りを拒否する（要件 1.3）。"""
    target = _upstream_copy(tmp_path)
    with pytest.raises(ParameterError) as excinfo:
        update_upstream_measurement("bracket.outline_x_mm", 41.3, path=target)
    assert "bracket.outline_x_mm" in str(excinfo.value)


def test_update_upstream_measurement_rejects_a_non_numeric_path(tmp_path: Path) -> None:
    """数値でない項目への書き戻しを拒否する（採寸値の反映だけを担う経路）。"""
    target = _upstream_copy(tmp_path)
    with pytest.raises(ParameterError) as excinfo:
        update_upstream_measurement("trash_can.model_id", 1.0, path=target)
    assert "trash_can.model_id" in str(excinfo.value)


def test_update_upstream_measurement_rejects_a_non_numeric_value(tmp_path: Path) -> None:
    """数値でない値を、項目名を示して拒否する。"""
    target = _upstream_copy(tmp_path)
    with pytest.raises(ParameterError) as excinfo:
        update_upstream_measurement(
            "trash_can.bottom_flat_diameter_mm",
            "168.4",  # type: ignore[arg-type]
            path=target,
        )
    assert "trash_can.bottom_flat_diameter_mm" in str(excinfo.value)


def test_upstream_write_back_has_exactly_one_entry_point() -> None:
    """上流の設定ファイルへ書く経路は1つだけである（design.md State Management）。

    ⚠️ 上流の `dump_params` を呼ぶ箇所が増えれば、値と出所以外を書き換える
    経路も同時に増える。
    """
    assert _construction_sites("upstream_dump_params") == ["update_upstream_measurement"]


# ---------------------------------------------------------------------------
# 依存境界（design.md「Allowed Dependencies」）
# ---------------------------------------------------------------------------


def test_config_imports_only_the_upstream_public_entry_point() -> None:
    """⚠️ 上流の内部モジュールを直接 import しない（公開 API だけを使う）。

    兄弟パッケージ（依存方向が逆になるもの）と形状ライブラリも import しない。
    静的に見るのは、実行時に import されない分岐へ紛れ込んでも捕まえるため
    である。
    """
    forbidden_roots = {
        "trajectory_sim",
        "prediction_core",
        "sensing_foundation",
        "build123d",
    }
    imported: set[str] = set()
    for node in ast.walk(_config_source_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    assert {name.split(".")[0] for name in imported} & forbidden_roots == set()
    upstream_modules = {name for name in imported if name.split(".")[0] == "catch_mechanism"}
    assert upstream_modules <= {"catch_mechanism"}
