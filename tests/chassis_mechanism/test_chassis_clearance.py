"""`chassis_mechanism.clearance` の検査（design.md `#### Clearance` / 要件 4.1,
4.2, 4.3, 4.4）。

固定するのは次の6点である。

1. **部位は常に5件**であり、名と並びが動かないこと（design.md `#### Clearance`
   Invariants「`clearance_items` は常に5件を返す。⚠️ **部位を黙って省かない**」）。
   ⚠️ 要件 4.2 が名指しするのは4部位だが、design.md は**モータ胴体下面**を
   基準として加えて5部位とする。黙って4件へ戻る変更をここで落とす。
2. **5部位すべての高さが鉛直スタックから算出される**こと（要件 4.7 /
   tasks.md タスク 2.2 の観測可能な完了状態「実効転がり半径を小さくすると5部位
   すべての高さが追随し」）。⚠️ 1部位でも寸法パラメータから直に読んでいれば、
   スタックだけを下げた入力で追随しない。
3. **例外を送出せず、違反を全件まとめて値として返す**こと（design.md
   `#### Clearance`「⚠️ **例外を送出しない。**」/ tasks.md タスク 2.2
   「1件ずつ直す往復を避けるため」）。⚠️ `ast` でモジュール内に `raise` 文が
   1つも無いことを固定する（`ClearanceError` は呼び出し側——CLI、タスク 4.1——が
   送出する）。
4. **下限は寸法パラメータから読む**こと（要件 4.3 / tasks.md タスク 2.2
   「⚠️ **数値をコードへ埋め込まない**」）。⚠️ `ast` でモジュール内に数値
   リテラルが1つも無いことを固定する。
5. **戻り値が空であることと、全部位が下限以上であることが同値**であること
   （design.md `#### Clearance` Postconditions）。⚠️ **両向き**に検査し、
   **ちょうど下限の部位は違反ではない**ことも固定する。
6. **不足量は常に正**であること（design.md の `ClearanceViolation` の
   `shortfall_mm  # 常に正`）。
"""

from __future__ import annotations

import ast
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from chassis_mechanism import clearance as clearance_module
from chassis_mechanism import layout as layout_module
from chassis_mechanism.clearance import (
    CLEARANCE_ITEM_NAMES,
    ClearanceItem,
    ClearanceViolation,
    clearance_items,
    evaluate_clearance,
)
from chassis_mechanism.config import DEFAULT_DIMENSIONS_PATH, ResolvedParams, load_params
from chassis_mechanism.layout import ChassisLayout, derive_layout

_CLEARANCE_SOURCE: str = Path(clearance_module.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _params() -> ResolvedParams:
    """出荷されている寸法設定を読む。"""
    return load_params(DEFAULT_DIMENSIONS_PATH)


def _with_clearance(params: ResolvedParams, **changes: object) -> ResolvedParams:
    """隙間の下限と設計量だけを差し替えた `ResolvedParams` を作る。"""
    return replace(
        params,
        chassis=replace(
            params.chassis, clearance=replace(params.chassis.clearance, **changes)
        ),
    )


def _lowered(layout: ChassisLayout, delta_mm: float) -> ChassisLayout:
    """鉛直スタック**だけ**を `delta_mm` 下げた導出結果を作る。

    ⚠️ **寸法パラメータは一切動かさない。** 実効転がり半径が縮めば機体全体が
    その分だけ下がる（research.md「最低地上高は何から決まるか」: 公称 30mm に
    対し荷重下で 29.25mm なら、モータ胴体下面は 10.75mm へ下がる）。
    この入力で追随しない部位は、スタックではなく寸法パラメータから直に高さを
    読んでいることになる。
    """
    vertical = layout.vertical
    return replace(
        layout,
        vertical=replace(
            vertical,
            effective_rolling_radius_mm=vertical.effective_rolling_radius_mm - delta_mm,
            axle_center_height_mm=vertical.axle_center_height_mm - delta_mm,
            motor_body_bottom_height_mm=(
                vertical.motor_body_bottom_height_mm - delta_mm
            ),
            mount_face_height_mm=vertical.mount_face_height_mm - delta_mm,
            fastener_bottom_height_mm=vertical.fastener_bottom_height_mm - delta_mm,
        ),
    )


def _heights(params: ResolvedParams) -> dict[str, float]:
    """出荷寸法から算出した `{部位名: 高さ}`。"""
    layout = derive_layout(params)
    return {
        item.name: item.height_mm
        for item in clearance_items(layout, params.chassis)
    }


def _names(violations: tuple[ClearanceViolation, ...]) -> tuple[str, ...]:
    """違反の部位名を並び順のまま取り出す。"""
    return tuple(violation.name for violation in violations)


# ---------------------------------------------------------------------------
# 1. 部位は常に5件（design.md `#### Clearance` Invariants）
# ---------------------------------------------------------------------------


def test_clearance_items_always_returns_the_five_documented_parts() -> None:
    """⚠️ 5件ちょうどであり、名も並びも動かない（部位を黙って省かない）。"""
    params = _params()
    items = clearance_items(derive_layout(params), params.chassis)
    assert len(items) == 5
    assert tuple(item.name for item in items) == CLEARANCE_ITEM_NAMES
    assert CLEARANCE_ITEM_NAMES == (
        "motor_body",
        "bracket",
        "fastener",
        "base_underside",
        "cable",
    )


def test_the_fifth_part_is_the_motor_body_added_to_the_four_named_by_requirement() -> None:
    """⚠️ 要件 4.2 の4部位に**モータ胴体下面**を加えて5部位である。

    design.md `#### Clearance`:「モータ胴体は設計で動かせないが、⚠️ **最も低い
    部位であり基準として一覧に出す価値がある**」。4件へ戻す変更をここで落とす。
    """
    assert set(CLEARANCE_ITEM_NAMES) - {"motor_body"} == {
        "bracket",
        "fastener",
        "base_underside",
        "cable",
    }
    assert "motor_body" in CLEARANCE_ITEM_NAMES


def test_five_parts_are_returned_even_when_nothing_protrudes_below_the_plate() -> None:
    """突出も垂れ下がりも 0 の設定でも部位は5件のまま（省略しない）。"""
    params = _with_clearance(
        _params(), fastener_protrusion_mm=0.0, cable_lowest_offset_mm=0.0
    )
    items = clearance_items(derive_layout(params), params.chassis)
    assert tuple(item.name for item in items) == CLEARANCE_ITEM_NAMES


def test_module_docstring_records_why_there_are_five_parts_and_not_four() -> None:
    """⚠️ 「なぜ5件なのか」がモジュールの文書に残っている。

    根拠が残っていなければ、後から読む人が要件 4.2 の4部位に合わせて
    モータ胴体下面を「余分」として消してしまう。
    """
    docstring = clearance_module.__doc__ or ""
    assert "モータ胴体" in docstring
    assert "4" in docstring and "5" in docstring
    assert "4.2" in docstring


# ---------------------------------------------------------------------------
# 2. 5部位すべての高さが鉛直スタックから算出される（要件 4.1, 4.7）
# ---------------------------------------------------------------------------


def test_motor_body_height_is_the_bottom_of_the_motor_body() -> None:
    """モータ胴体下面の高さは鉛直スタックのその項である。"""
    params = _params()
    layout = derive_layout(params)
    heights = _heights(params)
    assert heights["motor_body"] == pytest.approx(
        layout.vertical.motor_body_bottom_height_mm
    )


def test_bracket_height_is_the_motor_body_underside_plane() -> None:
    """ブラケットの最下点はモータ胴体下面と同一平面として扱う。

    付属金属ブラケットはモータ胴体を抱いて吊るため、その最下点は胴体下面の
    平面にある（`docs/drivetrain-spec.md §6.3`）。⚠️ **外形寸法
    （`bracket.outline_x_mm` / `outline_y_mm`）を鉛直方向の張り出しとして
    使わない**——あれは取付面内の外形であり、鉛直の量ではない。
    """
    params = _params()
    layout = derive_layout(params)
    heights = _heights(params)
    assert heights["bracket"] == pytest.approx(
        layout.vertical.motor_body_bottom_height_mm
    )


def test_fastener_height_is_the_bottom_of_the_heads_and_nuts() -> None:
    """ボルト頭・ナットの高さは鉛直スタックの締結の下端である。"""
    params = _params()
    layout = derive_layout(params)
    heights = _heights(params)
    assert heights["fastener"] == pytest.approx(
        layout.vertical.fastener_bottom_height_mm
    )
    assert heights["fastener"] < heights["base_underside"]


def test_base_underside_height_is_the_mount_face() -> None:
    """駆動ベース下面の高さは取付面（＝ベース板下面）である（要件 4.1）。"""
    params = _params()
    layout = derive_layout(params)
    heights = _heights(params)
    assert heights["base_underside"] == pytest.approx(
        layout.vertical.mount_face_height_mm
    )


def test_cable_height_is_the_base_underside_lowered_by_the_offset() -> None:
    """配線の最下点はベース下面から `cable_lowest_offset_mm` だけ下がる。"""
    params = _with_clearance(_params(), cable_lowest_offset_mm=8.0)
    layout = derive_layout(params)
    heights = _heights(params)
    assert heights["cable"] == pytest.approx(
        layout.vertical.mount_face_height_mm - 8.0
    )


def test_the_cable_offset_moves_only_the_cable() -> None:
    """配線のオフセットは配線の高さだけを動かす（他部位へ漏れない）。"""
    base = _heights(_with_clearance(_params(), cable_lowest_offset_mm=0.0))
    lowered = _heights(_with_clearance(_params(), cable_lowest_offset_mm=12.0))
    assert lowered["cable"] == pytest.approx(base["cable"] - 12.0)
    for name in ("motor_body", "bracket", "fastener", "base_underside"):
        assert lowered[name] == pytest.approx(base[name])


def test_every_height_follows_the_vertical_stack() -> None:
    """⚠️ **実効転がり半径を下げると5部位すべての高さが追随する**（要件 4.7）。

    tasks.md タスク 2.2 の観測可能な完了状態そのものである。寸法パラメータは
    一切動かしていないため、⚠️ **1部位でもパラメータから直に高さを読んでいれば
    ここが落ちる**。
    """
    params = _params()
    layout = derive_layout(params)
    delta_mm = 0.75
    before = {
        item.name: item.height_mm for item in clearance_items(layout, params.chassis)
    }
    after = {
        item.name: item.height_mm
        for item in clearance_items(_lowered(layout, delta_mm), params.chassis)
    }
    assert set(after) == set(CLEARANCE_ITEM_NAMES)
    for name in CLEARANCE_ITEM_NAMES:
        assert after[name] == pytest.approx(before[name] - delta_mm), name


def test_derivation_from_a_config_file_lowers_all_five_heights_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **導出そのもの**を通して、5部位の高さが実効転がり半径に追随する。

    直前の `test_every_height_follows_the_vertical_stack` は鉛直スタックを手で
    下げた入力を与えるため、`clearance` が高さをスタックから読んでいることしか
    確かめられない——⚠️ **`layout` が取付面高さを定数として組み立てていても
    通ってしまう**。ここは設定ファイル → `load_params` → `derive_layout` →
    `clearance_items` の通しで確かめ、design.md `#### Clearance` の
    「隙間はすべて `VerticalStack` から算出されるため、実効転がり半径が変われば
    自動で追随する（要件 4.7）」を実際に固定する。tasks.md タスク 7.3 の
    「実効転がり半径を変更したとき、5部位の隙間がすべて再算出されること」が
    要求する検査でもある。

    ⚠️ 設定ファイルは出荷値の写しではなく `wheel.nominal_diameter_mm` を
    小さくした写しを使う——導出が出荷値（60.0mm / 30.0mm）に固有の関係へ
    寄りかかっていないことを併せて確かめるためである。
    """
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    document["wheel"]["nominal_diameter_mm"] = 58.5
    dimensions_path = tmp_path / "dimensions.json"
    dimensions_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    params = load_params(dimensions_path)

    before = {
        item.name: item.height_mm
        for item in clearance_items(derive_layout(params), params.chassis)
    }
    compression_mm = 0.75
    monkeypatch.setattr(
        layout_module,
        "_effective_rolling_radius_mm",
        # ⚠️ 第2引数は観測（`layout.ObservedRollingRadius`。タスク 4.1 が配線した）。
        # 本件は観測**無し**で縮んだ状態を作るため受け取って捨てる——差し替え点が
        # 1箇所であることは変わっていない。
        lambda nominal_mm, observed=None: nominal_mm - compression_mm,
    )
    after = {
        item.name: item.height_mm
        for item in clearance_items(derive_layout(params), params.chassis)
    }

    assert set(after) == set(CLEARANCE_ITEM_NAMES)
    for name in CLEARANCE_ITEM_NAMES:
        assert after[name] == pytest.approx(before[name] - compression_mm), (
            f"{name} が実効転がり半径に追随していない"
        )


def test_lowering_the_stack_alone_turns_a_clean_design_into_violations() -> None:
    """⚠️ 寸法パラメータを一切変えずに、スタックを下げるだけで違反が生じる。

    実効転がり半径の実測（要件 10.3）が公称を下回れば、床との隙間の判定は
    寸法パラメータの書き換え無しに変わる——それが要件 4.7 の「再算出」である。
    """
    params = _params()
    layout = derive_layout(params)
    assert evaluate_clearance(layout, params.chassis) == ()
    sunk = _lowered(layout, 7.0)
    violations = evaluate_clearance(sunk, params.chassis)
    assert _names(violations) == ("motor_body", "bracket")
    assert all(violation.shortfall_mm > 0.0 for violation in violations)


# ---------------------------------------------------------------------------
# 3. 違反は全件まとめて値で返る（要件 4.4 / design.md `#### Clearance`）
# ---------------------------------------------------------------------------


def test_no_violation_for_the_shipped_dimensions() -> None:
    """出荷されている寸法では違反が無い（空タプル）。"""
    params = _params()
    assert evaluate_clearance(derive_layout(params), params.chassis) == ()


def test_every_violating_part_is_reported_in_one_call() -> None:
    """⚠️ 下限を全部位が割る設定で、5件が**1回の呼び出しで**返る。

    tasks.md タスク 2.2:「1件ずつ直す往復を避けるため」。
    """
    params = _with_clearance(_params(), min_ground_clearance_mm=1000.0)
    violations = evaluate_clearance(derive_layout(params), params.chassis)
    assert _names(violations) == CLEARANCE_ITEM_NAMES


def test_several_violations_come_back_together_in_the_published_order() -> None:
    """一部だけが割る設定でも、該当部位が並び順のまま全件返る。"""
    params = _with_clearance(_params(), min_ground_clearance_mm=20.0)
    heights = _heights(params)
    violations = evaluate_clearance(derive_layout(params), params.chassis)
    assert _names(violations) == ("motor_body", "bracket")
    assert heights["motor_body"] < 20.0 and heights["fastener"] > 20.0


def test_violation_carries_the_part_name_and_the_shortfall() -> None:
    """違反は**部位名と不足量**を持つ（要件 4.4）。不足量は常に正である。"""
    params = _with_clearance(_params(), min_ground_clearance_mm=20.0)
    violations = evaluate_clearance(derive_layout(params), params.chassis)
    assert violations != ()
    for violation in violations:
        assert isinstance(violation, ClearanceViolation)
        assert violation.name in CLEARANCE_ITEM_NAMES
        assert violation.shortfall_mm > 0.0
        assert violation.shortfall_mm == pytest.approx(
            violation.minimum_mm - violation.height_mm
        )


def test_violation_heights_agree_with_the_items() -> None:
    """違反が持つ高さは `clearance_items` の高さと同一である（別経路で算出しない）。"""
    params = _with_clearance(_params(), min_ground_clearance_mm=100.0)
    layout = derive_layout(params)
    heights = {
        item.name: item.height_mm for item in clearance_items(layout, params.chassis)
    }
    for violation in evaluate_clearance(layout, params.chassis):
        assert violation.height_mm == pytest.approx(heights[violation.name])


# ---------------------------------------------------------------------------
# 4. 下限は寸法パラメータから読む（要件 4.3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("minimum_mm", [5.0, 11.0, 12.0, 57.5, 250.0])
def test_the_minimum_is_read_from_the_dimension_parameter(minimum_mm: float) -> None:
    """下限は設定ファイルの値がそのまま現れる（コードに埋め込まれていない）。"""
    params = _with_clearance(_params(), min_ground_clearance_mm=minimum_mm)
    heights = _heights(params)
    violations = evaluate_clearance(derive_layout(params), params.chassis)
    assert {violation.minimum_mm for violation in violations} <= {minimum_mm}
    assert _names(violations) == tuple(
        name for name in CLEARANCE_ITEM_NAMES if heights[name] < minimum_mm
    )


def test_module_embeds_no_numeric_literal() -> None:
    """⚠️ モジュール内に数値リテラルが1つも無い（tasks.md タスク 2.2）。

    下限も突出量もオフセットも寸法パラメータが正である。数を1つでも書けば、
    設定ファイルを書き換えても検査の判断が変わらない箇所が生まれる。
    """
    literals = [
        node.value
        for node in ast.walk(ast.parse(_CLEARANCE_SOURCE))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float, complex))
        and not isinstance(node.value, bool)
    ]
    assert literals == [], f"数値リテラルが埋め込まれている: {literals}"


# ---------------------------------------------------------------------------
# 5. 空であることと全部位が下限以上であることは同値（Postconditions）
# ---------------------------------------------------------------------------


def test_a_part_exactly_at_the_limit_is_not_a_violation() -> None:
    """⚠️ **ちょうど下限の部位は違反ではない**（境界は含む）。"""
    params = _params()
    lowest_mm = min(_heights(params).values())
    at_limit = _with_clearance(params, min_ground_clearance_mm=lowest_mm)
    assert evaluate_clearance(derive_layout(at_limit), at_limit.chassis) == ()


def test_a_part_just_below_the_limit_is_a_violation() -> None:
    """下限を最小の刻みだけ上げると、その部位は違反として現れる。"""
    params = _params()
    heights = _heights(params)
    lowest_mm = min(heights.values())
    just_above = _with_clearance(
        params, min_ground_clearance_mm=math.nextafter(lowest_mm, math.inf)
    )
    violations = evaluate_clearance(derive_layout(just_above), just_above.chassis)
    assert _names(violations) == tuple(
        name for name in CLEARANCE_ITEM_NAMES if heights[name] == lowest_mm
    )
    assert all(violation.shortfall_mm > 0.0 for violation in violations)


@pytest.mark.parametrize("minimum_mm", [0.5, 5.0, 11.5, 11.75, 57.0, 60.0, 61.0])
def test_emptiness_is_equivalent_to_every_part_meeting_the_limit(
    minimum_mm: float,
) -> None:
    """⚠️ **同値を両向きに固定する**（design.md `#### Clearance` Postconditions）。"""
    params = _with_clearance(_params(), min_ground_clearance_mm=minimum_mm)
    layout = derive_layout(params)
    items = clearance_items(layout, params.chassis)
    violations = evaluate_clearance(layout, params.chassis)
    all_at_or_above = all(item.height_mm >= minimum_mm for item in items)
    assert (violations == ()) is all_at_or_above


# ---------------------------------------------------------------------------
# 6. 例外を送出しない（design.md `#### Clearance` / 「Error Strategy」）
# ---------------------------------------------------------------------------


def test_module_contains_no_raise_statement() -> None:
    """⚠️ **モジュール内に `raise` 文が1つも無い**ことを構造として固定する。

    design.md `#### Clearance`:「⚠️ **例外を送出しない。** 違反は全件を値として
    返す。失敗として扱うのは呼び出し側である」。`ClearanceError` を送出するのは
    CLI（タスク 4.1）であり、ここではない。後から1件でも `raise` が入れば、
    「全件まとめて返す」性質が黙って失われる。
    """
    raises = [
        ast.dump(node)
        for node in ast.walk(ast.parse(_CLEARANCE_SOURCE))
        if isinstance(node, ast.Raise)
    ]
    assert raises == [], f"clearance.py に raise 文がある: {raises}"


def test_module_neither_imports_nor_names_the_clearance_error() -> None:
    """`ClearanceError` は呼び出し側の型であり、本モジュールは触れない。"""
    tree = ast.parse(_CLEARANCE_SOURCE)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported |= {alias.name for alias in node.names}
    assert "ClearanceError" not in imported
    assert "assert " not in _CLEARANCE_SOURCE


def test_extreme_inputs_come_back_as_values_not_exceptions() -> None:
    """床下へ垂れる配線のような極端な入力でも、例外ではなく値で返る。"""
    params = _with_clearance(_params(), cable_lowest_offset_mm=500.0)
    layout = derive_layout(params)
    items = clearance_items(layout, params.chassis)
    assert len(items) == 5
    cable = next(item for item in items if item.name == "cable")
    assert cable.height_mm < 0.0
    violations = evaluate_clearance(layout, params.chassis)
    assert "cable" in _names(violations)
    assert all(violation.shortfall_mm > 0.0 for violation in violations)


def test_items_are_immutable_values() -> None:
    """`ClearanceItem` / `ClearanceViolation` は凍結された値である。"""
    item = ClearanceItem(name="cable", height_mm=1.0)
    with pytest.raises(Exception):
        item.height_mm = 2.0  # type: ignore[misc]
    violation = ClearanceViolation(
        name="cable", height_mm=1.0, minimum_mm=5.0, shortfall_mm=4.0
    )
    with pytest.raises(Exception):
        violation.shortfall_mm = 0.0  # type: ignore[misc]
