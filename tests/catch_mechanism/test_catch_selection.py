"""ゴミ箱の選定基準と候補判定（タスク 2.2、要件 6.1, 6.2, 6.3, 6.4）。

本ファイルが固定するのは design.md「Selection」の Service Interface /
Postconditions / Invariants と、tasks.md タスク 2.2 の「観測可能な完了状態」である。

1. **基準の正は `.kiro/steering/roadmap.md`** であり、`selection-criteria.json` は
   その転記にすぎない（design.md「Selection」Responsibilities の
   「⚠️ **推測で基準を書き換えない**」）。転記が roadmap からずれていないことを
   数値ごとに突き合わせる
2. **判定は全項目について行い、最初の不適合で打ち切らない**（要件 6.2 の
   「不適合であった項目名を示す」は一覧で返って初めて満たせる）
3. **第一候補が適合し、強テーパー品・開口内径が下限未満の品・蓋付きの品が
   それぞれ不適合になる**（要件 6.3, 6.4 / design.md「Testing Strategy」Unit Tests 6）
4. **望ましいが必須でない項目（外向きリム）は警告にのみ現れ、適合判定を
   左右しない**（design.md「Selection」Invariants）
5. **`accepted` が偽なら `failed_items` は空でない**（同 Postconditions）
6. 設定ファイルの読み込みは `config.py` と同じ規律に従う——**あらゆる階層で
   未知キーを拒否し、欠損を既定値で埋めない**（要件 1.3 の選定基準・候補への適用）。
   ⚠️ 送出する例外は `SelectionError` である（`errors.py` の docstring が
   「選定基準の設定ファイルが未知の項目名を挙げている、候補の諸元に必須項目が
   欠けている」をこの型に割り当てている）

ファイル名について: `tests/` 配下には `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
design.md「Directory Structure」が挙げる `test_selection.py` は将来
`tests/trajectory_sim/test_selection.py` 等と衝突しうるため使えない。既存の
`test_catch_constraints.py` / `test_catch_config.py` に倣い `test_catch_selection.py`
とする（tasks.md「Implementation Notes」タスク 1.1）。
"""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any

import pytest

from catch_mechanism import selection as selection_module
from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import CatchMechanismError, SelectionError
from catch_mechanism.params import Provenance
from catch_mechanism.selection import (
    DEFAULT_CANDIDATES_PATH,
    DEFAULT_CRITERIA_PATH,
    Candidate,
    CandidateVerdict,
    SelectionCriteria,
    evaluate_candidate,
    load_candidates,
    load_criteria,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ROADMAP_PATH = REPO_ROOT / ".kiro" / "steering" / "roadmap.md"

PRIMARY_IDENTIFIER = "yamada-kagaku-no335"
RUNNER_UP_IDENTIFIER = "seria-brooklyn-dustbox"
STRONG_TAPER_IDENTIFIER = "watts-re-b"
BELOW_MINIMUM_IDENTIFIER = "illustrative-3l-class-below-minimum"
LIDDED_IDENTIFIER = "illustrative-lidded-upper-price-tier"


def _criteria_document() -> dict[str, Any]:
    """リポジトリの選定基準ファイルを素の辞書として読む（改変用の複製）。"""
    return json.loads(DEFAULT_CRITERIA_PATH.read_text(encoding="utf-8"))


def _candidates_document() -> dict[str, Any]:
    """リポジトリの候補ファイルを素の辞書として読む（改変用の複製）。"""
    return json.loads(DEFAULT_CANDIDATES_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, name: str, document: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n")
    return path


def _compliant(**overrides: object) -> Candidate:
    """全項目が基準を満たす候補（第一候補の諸元）を作り、指定項目だけ差し替える。"""
    base: dict[str, object] = {
        "identifier": "fixture-compliant",
        "shape": "round",
        "opening_inner_diameter_mm": 220.0,
        "height_mm": 244.0,
        "mass_g": 228.0,
        "taper_deg": 7.0,
        "price_jpy": 110,
        "has_lid": False,
        "has_outward_rim": True,
        "provenance": Provenance.ASSUMED,
    }
    base.update(overrides)
    return Candidate(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. 基準の正は roadmap である
# ---------------------------------------------------------------------------


def test_criteria_file_transcribes_the_roadmap_thresholds() -> None:
    """`selection-criteria.json` の各しきい値が roadmap の記載と一致する。

    ⚠️ この突き合わせが落ちたときに**テストを緩めてはならない**。roadmap
    （`.kiro/steering/roadmap.md`「ゴミ箱の選定基準」）が基準の正であり、
    落ちたということは転記がずれたか roadmap が改訂されたかのいずれかである。
    どちらの場合も**転記をやり直す**のが正しい対処である。
    """
    criteria = load_criteria()
    roadmap = ROADMAP_PATH.read_text(encoding="utf-8")

    assert criteria.shape == "round"
    assert criteria.opening_inner_diameter_min_mm == 200.0
    assert "最低 φ200" in roadmap
    assert criteria.opening_inner_diameter_reject_below_mm == 180.0
    assert "φ180 未満は不可" in roadmap
    assert criteria.opening_inner_diameter_typical_max_mm == 225.0
    assert "φ215〜225" in roadmap
    assert (criteria.height_min_mm, criteria.height_max_mm) == (200.0, 300.0)
    assert "200〜300mm" in roadmap
    assert criteria.mass_max_g == 300.0
    assert "300g 以下" in roadmap
    assert criteria.price_max_jpy == 110
    assert "110円で足りる" in roadmap
    assert criteria.requires_lidless is True
    assert "フタなしを選ぶ" in roadmap
    assert criteria.prefers_outward_rim is True


def test_taper_limit_separates_the_two_examples_named_by_the_roadmap() -> None:
    """テーパー上限が roadmap の「可」の例と「強すぎる」の例を分ける。

    roadmap は上限を数値ではなく**2つの例**で述べる（上φ220→底φ158（片側 約7°）は
    可、上φ225→底φ145（約10°）は強すぎる）。したがって上限値は「7.24° を通し
    10.0° を落とす」という条件だけから決まり、どちらの例に対しても余裕を持つ位置に
    置かれていなければならない。⚠️ 偶然どちらか一方に接している値は、片側の例が
    わずかに動いただけで意味が反転する。
    """
    criteria = load_criteria()
    # 上φ220→底φ158・H244 の実際の角度（roadmap の「約7°」の出所）
    permitted_example_deg = 7.2405901296450095
    # 上φ225→底φ145 の角度（roadmap の「約10°」）
    forbidden_example_deg = 10.0

    assert permitted_example_deg < criteria.taper_max_deg < forbidden_example_deg
    assert criteria.taper_max_deg - permitted_example_deg >= 1.0
    assert forbidden_example_deg - criteria.taper_max_deg >= 1.0


def test_candidates_file_records_the_products_named_by_the_roadmap() -> None:
    """候補ファイルが第一候補・次点・非推奨例を roadmap の諸元どおりに持つ。"""
    by_id = {candidate.identifier: candidate for candidate in load_candidates()}

    primary = by_id[PRIMARY_IDENTIFIER]
    assert primary.shape == "round"
    assert primary.opening_inner_diameter_mm == 220.0
    assert primary.height_mm == 244.0
    assert primary.mass_g == 228.0
    assert primary.price_jpy == 110
    assert primary.has_lid is False

    runner_up = by_id[RUNNER_UP_IDENTIFIER]
    assert runner_up.opening_inner_diameter_mm == 215.0
    assert runner_up.height_mm == 220.0

    strong_taper = by_id[STRONG_TAPER_IDENTIFIER]
    assert strong_taper.opening_inner_diameter_mm == 225.0
    assert strong_taper.taper_deg == 10.0


def test_primary_identifier_matches_the_dimensions_file_model_id() -> None:
    """第一候補の識別子が寸法設定ファイルの機種識別情報と一致する（要件 6.8 への地ならし）。"""
    dimensions = json.loads(
        (REPO_ROOT / "configs" / "catch_mechanism" / "dimensions.json").read_text(encoding="utf-8")
    )
    assert dimensions["trash_can"]["model_id"] == PRIMARY_IDENTIFIER


def test_candidates_not_named_by_the_roadmap_are_marked_as_illustrative() -> None:
    """roadmap に無い候補は、識別子と `role` の両方で例示であると分かる。

    ⚠️ 実売調査で確かめた品と、基準の説明のために置いた例とを取り違えると、
    「調べた」ことになっていない品を買いに行く事故になる。
    """
    document = _candidates_document()
    roles = {entry["identifier"]: entry["role"] for entry in document["candidates"]}

    assert roles[PRIMARY_IDENTIFIER] == "primary"
    assert roles[RUNNER_UP_IDENTIFIER] == "runner_up"
    assert roles[STRONG_TAPER_IDENTIFIER] == "not_recommended"
    for identifier in (BELOW_MINIMUM_IDENTIFIER, LIDDED_IDENTIFIER):
        assert roles[identifier] == "illustrative_non_example"
        assert identifier.startswith("illustrative-")

    surveyed = {PRIMARY_IDENTIFIER, RUNNER_UP_IDENTIFIER, STRONG_TAPER_IDENTIFIER}
    for identifier, role in roles.items():
        assert (role == "illustrative_non_example") == (identifier not in surveyed)


# ---------------------------------------------------------------------------
# 2. 観測可能な完了状態（要件 6.2, 6.3, 6.4）
# ---------------------------------------------------------------------------


def test_primary_candidate_is_accepted() -> None:
    """第一候補（山田化学 No.335）が適合する。"""
    criteria = load_criteria()
    by_id = {candidate.identifier: candidate for candidate in load_candidates()}

    verdict = evaluate_candidate(by_id[PRIMARY_IDENTIFIER], criteria)

    assert verdict.identifier == PRIMARY_IDENTIFIER
    assert verdict.accepted is True
    assert verdict.failed_items == ()


@pytest.mark.parametrize(
    ("identifier", "expected_item"),
    [
        (STRONG_TAPER_IDENTIFIER, "taper_deg"),
        (BELOW_MINIMUM_IDENTIFIER, "opening_inner_diameter_mm"),
        (LIDDED_IDENTIFIER, "has_lid"),
    ],
)
def test_non_compliant_candidates_are_rejected_with_the_item_name(
    identifier: str, expected_item: str
) -> None:
    """強テーパー品・開口内径が下限未満の品・蓋付きの品が、理由の項目名つきで落ちる。"""
    criteria = load_criteria()
    by_id = {candidate.identifier: candidate for candidate in load_candidates()}

    verdict = evaluate_candidate(by_id[identifier], criteria)

    assert verdict.accepted is False
    assert expected_item in verdict.failed_items


def test_strong_taper_candidate_fails_only_on_the_taper() -> None:
    """非推奨例（ワッツ Re.B）が落ちる理由はテーパーだけである。

    roadmap が Re.B を非推奨とする理由は**強テーパーのみ**であり、他の項目
    （開口内径 φ225・価格・蓋）は基準を満たす。理由が増えていれば諸元の転記が
    ずれている。
    """
    criteria = load_criteria()
    by_id = {candidate.identifier: candidate for candidate in load_candidates()}

    verdict = evaluate_candidate(by_id[STRONG_TAPER_IDENTIFIER], criteria)

    assert verdict.failed_items == ("taper_deg",)


def test_evaluation_does_not_stop_at_the_first_failure() -> None:
    """全項目を評価し、不適合項目を**一覧で**返す（要件 6.2）。

    ⚠️ 最初の不適合で打ち切る実装は、この主張だけが捕まえられる。1項目しか
    返さない実装でも「不適合として扱う」テストは通ってしまう。
    """
    criteria = load_criteria()
    hopeless = _compliant(
        identifier="fixture-fails-every-item",
        shape="square",
        opening_inner_diameter_mm=150.0,
        height_mm=150.0,
        mass_g=500.0,
        taper_deg=20.0,
        price_jpy=550,
        has_lid=True,
    )

    verdict = evaluate_candidate(hopeless, criteria)

    assert verdict.accepted is False
    assert set(verdict.failed_items) == {
        "shape",
        "opening_inner_diameter_mm",
        "height_mm",
        "mass_g",
        "taper_deg",
        "price_jpy",
        "has_lid",
    }


def test_failed_items_have_no_duplicates_and_a_stable_order() -> None:
    """不適合項目の一覧に重複が無く、並びが評価順に固定されている。"""
    criteria = load_criteria()
    below_reject = _compliant(opening_inner_diameter_mm=150.0, has_lid=True)

    verdict = evaluate_candidate(below_reject, criteria)

    assert verdict.failed_items == ("opening_inner_diameter_mm", "has_lid")
    assert len(set(verdict.failed_items)) == len(verdict.failed_items)


@pytest.mark.parametrize("opening_mm", [179.9, 199.9])
def test_opening_below_the_minimum_is_rejected(opening_mm: float) -> None:
    """開口内径が下限（φ200）を下回る候補は、拒否値（φ180）の上下いずれでも不適合（要件 6.3）。"""
    verdict = evaluate_candidate(_compliant(opening_inner_diameter_mm=opening_mm), load_criteria())

    assert verdict.accepted is False
    assert "opening_inner_diameter_mm" in verdict.failed_items


def test_taper_above_the_limit_is_rejected_and_the_limit_itself_is_accepted() -> None:
    """テーパー角が上限を超える候補は不適合、上限ちょうどは適合（要件 6.4）。"""
    criteria = load_criteria()

    assert evaluate_candidate(_compliant(taper_deg=criteria.taper_max_deg), criteria).accepted
    over = evaluate_candidate(_compliant(taper_deg=criteria.taper_max_deg + 0.1), criteria)
    assert over.accepted is False
    assert "taper_deg" in over.failed_items


# ---------------------------------------------------------------------------
# 3. 望ましいが必須でない項目は警告にとどまる（design.md「Selection」Invariants）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("has_outward_rim", [False, None])
def test_outward_rim_is_a_warning_and_never_affects_acceptance(
    has_outward_rim: bool | None,
) -> None:
    """外向きリムが無い／不明でも適合判定は変わらず、警告にのみ現れる。"""
    verdict = evaluate_candidate(_compliant(has_outward_rim=has_outward_rim), load_criteria())

    assert verdict.accepted is True
    assert verdict.failed_items == ()
    assert "has_outward_rim" in verdict.warnings


def test_outward_rim_present_produces_no_warning() -> None:
    """外向きリムがある候補には警告が付かない。"""
    verdict = evaluate_candidate(_compliant(has_outward_rim=True), load_criteria())

    assert verdict.warnings == ()


def test_unknown_mass_is_a_warning_not_a_failure() -> None:
    """重量が未記録の候補は警告にとどまる（要件 6.7: 未採寸でも完了できる）。"""
    verdict = evaluate_candidate(_compliant(mass_g=None), load_criteria())

    assert verdict.accepted is True
    assert "mass_g" in verdict.warnings
    assert "mass_g" not in verdict.failed_items


def test_opening_above_the_typical_maximum_is_a_warning_not_a_failure() -> None:
    """実売の実質上限（φ225）を超える開口内径は警告にとどまる。

    roadmap は「100均の丸型は φ215〜225 で頭打ちであり φ240 以上は存在しない」と
    述べる。上限超えは**不適合ではない**（広い開口は許容誤差の上で有利である）が、
    外径を内径として転記した疑いがあるため警告として示す。
    """
    verdict = evaluate_candidate(_compliant(opening_inner_diameter_mm=240.0), load_criteria())

    assert verdict.accepted is True
    assert "opening_inner_diameter_mm" in verdict.warnings
    assert verdict.failed_items == ()


def test_postconditions_hold_for_every_recorded_candidate() -> None:
    """記録済みの全候補について、Postconditions と Invariants が成り立つ。"""
    criteria = load_criteria()

    for candidate in load_candidates():
        verdict = evaluate_candidate(candidate, criteria)
        assert verdict.accepted == (verdict.failed_items == ())
        assert "has_outward_rim" not in verdict.failed_items


# ---------------------------------------------------------------------------
# 4. 設定ファイルの読み込み規律（要件 1.3 の選定への適用）
# ---------------------------------------------------------------------------


def test_default_paths_point_at_the_repository_config_files() -> None:
    """既定パスが `configs/catch_mechanism/` の2ファイルを指す。"""
    configs = REPO_ROOT / "configs" / "catch_mechanism"

    assert DEFAULT_CRITERIA_PATH == configs / "selection-criteria.json"
    assert DEFAULT_CANDIDATES_PATH == configs / "candidates.json"
    assert DEFAULT_CRITERIA_PATH.is_file()
    assert DEFAULT_CANDIDATES_PATH.is_file()


def test_both_files_share_the_schema_version_owned_by_config() -> None:
    """記録形式の版は `config.SCHEMA_VERSION` の1箇所が正である。"""
    assert _criteria_document()["schema_version"] == SCHEMA_VERSION
    assert _candidates_document()["schema_version"] == SCHEMA_VERSION
    assert selection_module.SCHEMA_VERSION is SCHEMA_VERSION


@pytest.mark.parametrize("name", ["schema_version", "shape", "taper_max_deg"])
def test_criteria_missing_key_is_rejected_with_the_item_name(tmp_path: Path, name: str) -> None:
    """必須キーの欠損を、項目名を示して拒否する（既定値で埋めない）。"""
    document = _criteria_document()
    del document[name]

    with pytest.raises(SelectionError) as excinfo:
        load_criteria(_write(tmp_path, "criteria.json", document))

    assert name in str(excinfo.value)


def test_criteria_unknown_key_is_rejected_with_the_item_name(tmp_path: Path) -> None:
    """未知キーを、項目名を示して拒否する（綴り誤りを既定値で通さない）。"""
    document = _criteria_document()
    document["taper_max_degrees"] = 8.5

    with pytest.raises(SelectionError) as excinfo:
        load_criteria(_write(tmp_path, "criteria.json", document))

    assert "taper_max_degrees" in str(excinfo.value)


def test_criteria_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    document = _criteria_document()
    document["schema_version"] = "0.9"

    with pytest.raises(SelectionError):
        load_criteria(_write(tmp_path, "criteria.json", document))


def test_criteria_boolean_is_not_accepted_as_a_number(tmp_path: Path) -> None:
    """⚠️ `bool` は `int` の派生である。数値の項目へ `true` を通さない。"""
    document = _criteria_document()
    document["mass_max_g"] = True

    with pytest.raises(SelectionError) as excinfo:
        load_criteria(_write(tmp_path, "criteria.json", document))

    assert "mass_max_g" in str(excinfo.value)


def test_criteria_reject_value_above_the_minimum_is_refused(tmp_path: Path) -> None:
    """拒否値が下限を上回る基準は成立しない（`φ180 未満は不可` は下限の下にある）。"""
    document = _criteria_document()
    document["opening_inner_diameter_reject_below_mm"] = 210.0

    with pytest.raises(SelectionError) as excinfo:
        load_criteria(_write(tmp_path, "criteria.json", document))

    assert "opening_inner_diameter_reject_below_mm" in str(excinfo.value)


def test_criteria_height_range_must_not_be_inverted(tmp_path: Path) -> None:
    document = _criteria_document()
    document["height_min_mm"] = 400.0

    with pytest.raises(SelectionError):
        load_criteria(_write(tmp_path, "criteria.json", document))


def test_missing_criteria_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SelectionError):
        load_criteria(tmp_path / "absent.json")


def test_criteria_file_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(SelectionError):
        load_criteria(_write(tmp_path, "criteria.json", [1, 2, 3]))


def test_candidates_unknown_key_inside_an_entry_is_rejected(tmp_path: Path) -> None:
    """候補1件の中の未知キーも拒否する（未知キー拒否はあらゆる階層で働く）。"""
    document = _candidates_document()
    document["candidates"][0]["colour"] = "white"

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert "colour" in str(excinfo.value)


def test_candidates_unknown_top_level_key_is_rejected(tmp_path: Path) -> None:
    document = _candidates_document()
    document["notes"] = "..."

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert "notes" in str(excinfo.value)


def test_candidates_missing_key_inside_an_entry_is_rejected(tmp_path: Path) -> None:
    document = _candidates_document()
    del document["candidates"][0]["taper_deg"]

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert "taper_deg" in str(excinfo.value)


def test_candidates_unknown_role_is_rejected(tmp_path: Path) -> None:
    """`role` は決められた語のみを受け付ける（例示か調査済みかを取り違えない）。"""
    document = _candidates_document()
    document["candidates"][0]["role"] = "favourite"

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert "favourite" in str(excinfo.value)


def test_candidates_unknown_provenance_is_rejected(tmp_path: Path) -> None:
    document = _candidates_document()
    document["candidates"][0]["provenance"] = "guessed"

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert "guessed" in str(excinfo.value)


def test_candidates_duplicate_identifier_is_rejected(tmp_path: Path) -> None:
    document = _candidates_document()
    document["candidates"].append(copy.deepcopy(document["candidates"][0]))

    with pytest.raises(SelectionError) as excinfo:
        load_candidates(_write(tmp_path, "candidates.json", document))

    assert PRIMARY_IDENTIFIER in str(excinfo.value)


def test_candidates_entry_that_is_not_an_object_is_rejected(tmp_path: Path) -> None:
    document = _candidates_document()
    document["candidates"].append("yamada")

    with pytest.raises(SelectionError):
        load_candidates(_write(tmp_path, "candidates.json", document))


def test_optional_fields_accept_null(tmp_path: Path) -> None:
    """`mass_g` / `has_outward_rim` は `null`（未記録）を受け付ける。"""
    document = _candidates_document()
    document["candidates"][0]["mass_g"] = None
    document["candidates"][0]["has_outward_rim"] = None

    candidates = load_candidates(_write(tmp_path, "candidates.json", document))

    assert candidates[0].mass_g is None
    assert candidates[0].has_outward_rim is None


def test_loaded_candidates_keep_the_file_order() -> None:
    identifiers = [candidate.identifier for candidate in load_candidates()]

    assert identifiers == [entry["identifier"] for entry in _candidates_document()["candidates"]]


def test_provenance_is_the_value_type_shared_with_params() -> None:
    """候補の出所は `params.Provenance` である（第3の値集合を作らない）。"""
    for candidate in load_candidates():
        assert isinstance(candidate.provenance, Provenance)


# ---------------------------------------------------------------------------
# 5. 型の形（design.md「Selection」Service Interface）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dataclass_type", "expected"),
    [
        (
            SelectionCriteria,
            (
                "shape",
                "opening_inner_diameter_min_mm",
                "opening_inner_diameter_reject_below_mm",
                "opening_inner_diameter_typical_max_mm",
                "height_min_mm",
                "height_max_mm",
                "mass_max_g",
                "taper_max_deg",
                "price_max_jpy",
                "requires_lidless",
                "prefers_outward_rim",
            ),
        ),
        (
            Candidate,
            (
                "identifier",
                "shape",
                "opening_inner_diameter_mm",
                "height_mm",
                "mass_g",
                "taper_deg",
                "price_jpy",
                "has_lid",
                "has_outward_rim",
                "provenance",
            ),
        ),
        (CandidateVerdict, ("identifier", "accepted", "failed_items", "warnings")),
    ],
)
def test_field_names_match_the_design_service_interface(
    dataclass_type: type, expected: tuple[str, ...]
) -> None:
    assert tuple(field.name for field in fields(dataclass_type)) == expected


def test_values_are_frozen() -> None:
    for instance in (_compliant(), load_criteria()):
        name = fields(instance)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(instance, name, "changed")


def test_candidate_construction_rejects_impossible_values() -> None:
    """構築時検証が項目名と値を添えて拒否する（`params.py` と同じ規律）。"""
    with pytest.raises(SelectionError) as excinfo:
        _compliant(opening_inner_diameter_mm=-1.0)

    assert "opening_inner_diameter_mm" in str(excinfo.value)


def test_selection_errors_are_catchable_as_the_package_base_and_value_error() -> None:
    with pytest.raises(CatchMechanismError):
        _compliant(height_mm=0.0)
    with pytest.raises(ValueError):
        _compliant(identifier="")
