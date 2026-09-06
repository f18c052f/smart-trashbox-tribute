"""下流が消費する公開契約を固定する（タスク 6.1 / 要件 10.1-10.6）。

本ファイルが固定するのは「`catch_mechanism` の公開入口が、下流 Spec
（`chassis-mechanism` ほか）の必要とする値・型・関数をちょうど公開しており、
その参照に形状ライブラリを要さない」ことである。

**役割分担**: `tests/catch_mechanism/test_catch_boundaries.py` は
`src/catch_mechanism/*.py` の import 関係を静的に検査する（依存の**向き**）。
本ファイルは公開**シンボルの一覧そのもの**を契約として固定し、実際に import
して値を取り出す（契約の**中身**）。前者は入口が CAD 層へ到達しないことを
架空のツリーでも実証しており、本ファイルはそれを実プロセスと、形状ライブラリを
import 不能にした子プロセスの双方で裏取りする。

**要件との対応**:

- 10.1 底の外径・平面部径・テーパー角・高さ・実測重量を下流が参照できる
  → `test_downstream_reads_the_trash_can_measurements_through_the_public_entry`
- 10.2 造形制約と継手方針を再実装せずに参照できる
  → `test_downstream_reads_the_printing_constraints_and_joint_policy`
- 10.3 参照が形状生成用の外部ライブラリを要さない
  → `test_the_public_entry_imports_only_the_core_layer` /
    `test_the_public_contract_holds_without_the_shape_library`
- 10.4 各項目に出所を併記し、仮値と実測値を区別できる
  → `test_every_published_measurement_carries_a_unit_and_a_provenance`
- 10.5 公開項目の意味・単位・構造の変更は下流の再検証を要する変更である
  → `test_the_design_records_the_public_api_as_a_revalidation_trigger` ほか。
    ⚠️ **`PUBLIC_CONTRACT` の編集がその変更そのものである**（下記）
- 10.6 下流の部品（駆動ベース・固定アダプタ・トレイ類・整備スタンド）を
  責務に含めない
  → `test_no_published_name_refers_to_a_downstream_part` と、その検出器が
    実際に働くことを示す `test_the_downstream_part_detector_flags_*` 系

**ファイル名について**: design.md `### Directory Structure` は
`test_downstream_contract.py` を挙げるが、本ディレクトリの規約
（tasks.md Note 1.1 / `tests/` に `__init__.py` が無く、テストモジュール名が
セッション全体でフラットである）に従い `test_catch_` 接頭辞を付ける。
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "catch_mechanism"
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "catch-mechanism" / "design.md"

PACKAGE = "catch_mechanism"

# ---------------------------------------------------------------------------
# 公開契約の正（⚠️ ここの編集は「再検証を要する変更」である）
# ---------------------------------------------------------------------------

PUBLIC_CONTRACT: Mapping[str, tuple[str, ...]] = {
    "errors": (
        "CatchMechanismError",
        "ParameterError",
        "SelectionError",
        "GeometryError",
        "ConsistencyError",
        "CadUnavailableError",
    ),
    "params": (
        "Provenance",
        "TrashCanMeasurements",
        "ObjectSpec",
        "PrintingConstraints",
        "JointPolicy",
        "RimParams",
        "RetentionParams",
        "MechanismParams",
        "PARAMETER_PATHS",
        "ALLOWED_MATERIALS",
    ),
    "config": (
        "SCHEMA_VERSION",
        "load_params",
        "dump_params",
        "parameters_digest",
    ),
    "selection": (
        "CRITERIA_ITEMS",
        "SelectionCriteria",
        "Candidate",
        "CandidateVerdict",
        "SelectionResult",
        "evaluate_candidate",
        "load_criteria",
        "load_candidates",
        "load_selection_result",
    ),
    "tolerance": (
        "DEFAULT_DERIVATION_PATH",
        "ToleranceInput",
        "ToleranceDerivation",
        "derive_position_tolerance",
        "load_derivation",
    ),
    "constraints": (
        "Envelope",
        "BuildViolation",
        "required_segment_count",
        "check_envelope",
        "check_material",
        "check_joint",
    ),
    "metrics": (
        "PRESENCE_FIELD",
        "PRESENT",
        "ABSENT",
        "PartMetrics",
        "GeometryBaseline",
        "MetricsMismatch",
        "load_baseline",
        "compare_metrics",
        "estimate_mass_g",
    ),
}
"""公開シンボルの正（`catch_mechanism.__all__` と**由来モジュール**の対応）。

⚠️ **この表の編集は design.md `### Revalidation Triggers` 項目4
「公開 API（`catch_mechanism.__init__`）のシンボル追加・削除・意味変更」
に当たる。** 名前を1つ足す・消す・意味を変えるとき、`__init__.py` だけを
直しても本表が落ちる。下流（`chassis-mechanism` / `trajectory-simulator` /
`m1-prediction-validation`）の再検証を伴わない公開面の変更が黙って通らない
ための仕掛けである。

キーは design.md `### Dependency Direction` の中核層のモジュール名であり、
値はそのモジュールが `__all__` で公開している名前の部分集合である
（`test_every_published_name_is_re_exported_from_its_core_module` が対応を検査する）。
"""

PUBLIC_NAMES: tuple[str, ...] = tuple(
    name for names in PUBLIC_CONTRACT.values() for name in names
)

CORE_MODULES: frozenset[str] = frozenset(PUBLIC_CONTRACT)
"""公開の由来となりうるモジュール（design.md「標準ライブラリのみで動く中核」）。"""

CAD_LAYER_MODULES: tuple[str, ...] = ("shapes", "export")
"""design.md「CAD 層（`cad` extra が必要）」。⚠️ ここからは1名も公開しない。"""

#: design.md `#### PublicApi` の `__all__` に載っていないが本契約が公開する名前。
#: ⚠️ **裁定の記録である。** 追加の理由は「既に公開されている名前を、この
#: パッケージの外で値を再定義せずに**呼ぶ・読む**ために要る」ことに限る。
#:
#: - `SelectionResult` / `load_selection_result` / `CRITERIA_ITEMS`:
#:   タスク 5.1 が要件 6.8（再調達性）のために追加した型・関数・語彙。
#:   design.md は選定 API を全公開しており（`Candidate` / `CandidateVerdict` /
#:   `evaluate_candidate` ほか）、その結論を表す型だけが非公開では
#:   「どの実物を測った値なのか」が入口から辿れない。`CRITERIA_ITEMS` は
#:   公開済みの `CandidateVerdict.failed_items` と
#:   `SelectionResult.decisive_criteria_items` の**語彙**であり、これが無いと
#:   利用側が項目名を文字列で再定義することになる（tasks.md Note 5.1(f)③）。
#: - `DEFAULT_DERIVATION_PATH`: 公開済みの `load_derivation(path)` は
#:   `path` を**必須引数**に取る。既定パスが非公開だと、出荷記録の場所を
#:   利用側が書き写すことになり、要件 10.1 の「同じ値を2箇所で持たない」に反する。
#: - `PRESENCE_FIELD` / `PRESENT` / `ABSENT`: 公開済みの `compare_metrics` が
#:   返す `MetricsMismatch` の、部品の在／不在を表す符号化そのもの。
#:   これが無いと利用側が `"presence"` / `1.0` / `0.0` を書き写すことになる。
RECORDED_ADDITIONS: frozenset[str] = frozenset(
    {
        "SelectionResult",
        "load_selection_result",
        "CRITERIA_ITEMS",
        "DEFAULT_DERIVATION_PATH",
        "PRESENCE_FIELD",
        "PRESENT",
        "ABSENT",
    }
)

#: 公開しないと裁定した名前と、その由来モジュール（design.md との差のもう一方）。
#: tasks.md Note 2.4(e) / 4.2(e) が design.md との整合を本タスクへ送っている。
#: 裁定: `#### Metrics` の Service Interface は**モジュールの**公開面であり、
#: `#### PublicApi` は**下流の**公開面である。両者が一致する必要は無い。
#: `write_baseline` は本 Spec 自身の記録を**書き換える**操作、
#: `verify_baseline_digest` はその記録の鮮度検査であり、どちらも
#: `python -m catch_mechanism build` / `check` の側の道具である。下流は記録を
#: 消費するだけで再生成しない（要件 10.6 の「自身の責務に含めない」の裏返し）。
DELIBERATELY_UNPUBLISHED: Mapping[str, str] = {
    "write_baseline": "metrics",
    "verify_baseline_digest": "metrics",
}

# ---------------------------------------------------------------------------
# 補助: `ast` によるソースの読み取り（対象モジュールを import しない）
# ---------------------------------------------------------------------------


def _module_all_via_ast(path: Path) -> tuple[str, ...]:
    """`path` の `__all__` を、モジュールを import せずに読み取る。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
            and node.value is not None
        ):
            return tuple(ast.literal_eval(node.value))
    return ()


def _module_level_import_targets(path: Path) -> tuple[str, ...]:
    """`path` のモジュール直下の import 先を返す（`from X import y` は `X`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise AssertionError(
                    f"{path.name} が相対 import を使っている（絶対 import に揃えること）"
                )
            if node.module is not None:
                targets.append(node.module)
    return tuple(targets)


# ---------------------------------------------------------------------------
# 1. 公開シンボルの一覧そのもの（要件 10.5 の掛かり先）
# ---------------------------------------------------------------------------


def test_the_public_entry_publishes_exactly_the_recorded_contract() -> None:
    """`__all__` が `PUBLIC_CONTRACT` とちょうど一致する（要件 10.5）。

    ⚠️ **本アサーションが design.md `### Revalidation Triggers` 項目4 の
    機械的な現れである。** 公開シンボルの追加・削除・改名は必ずこの表の編集を
    伴う（＝下流の再検証が要る変更であることを、作業者が素通りできない）。
    """
    package = importlib.import_module(PACKAGE)

    assert list(package.__all__) == list(PUBLIC_NAMES)


def test_the_public_entry_declares_no_duplicate_names() -> None:
    """`__all__` に重複が無い（同じ名前を2箇所の層から公開していない）。"""
    package = importlib.import_module(PACKAGE)

    declared = list(package.__all__)
    duplicates = sorted({name for name in declared if declared.count(name) > 1})
    assert duplicates == []


def test_every_published_name_is_bound_on_the_package() -> None:
    """`__all__` の全名が実際に束縛されている（`from catch_mechanism import *` が通る）。"""
    package = importlib.import_module(PACKAGE)

    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert missing == []


def test_every_published_name_is_re_exported_from_its_core_module() -> None:
    """各公開名が、対応する中核モジュールの**同一オブジェクト**である（要件 10.3）。

    再エクスポートであることを同一性で確かめる。入口側で値を作り直していれば
    （＝寸法や定数の第2の定義が生まれていれば）ここで落ちる。
    """
    package = importlib.import_module(PACKAGE)

    for module_name, names in PUBLIC_CONTRACT.items():
        module = importlib.import_module(f"{PACKAGE}.{module_name}")
        for name in names:
            assert hasattr(module, name), f"{module_name}.{name} が存在しない"
            assert getattr(package, name) is getattr(module, name), (
                f"{name} は {module_name} の同一オブジェクトではない"
            )
            assert name in module.__all__, (
                f"{name} は {module_name}.__all__ に無い（内部名を公開している）"
            )


def test_no_published_name_comes_from_the_cad_layer() -> None:
    """CAD 層（`shapes` / `export`）の公開名を1つも再エクスポートしない（要件 10.3）。

    ⚠️ 検査対象は `ast` で読んだ `__all__` である。CAD 層のモジュールを
    import してしまうと、本ファイル自身が要件 5.7 の反例になりかねない。
    """
    published = set(PUBLIC_NAMES)
    for module_name in CAD_LAYER_MODULES:
        cad_names = _module_all_via_ast(SRC_DIR / f"{module_name}.py")
        assert cad_names, f"{module_name}.py の __all__ を読み取れなかった"
        leaked = sorted(published & set(cad_names))
        assert leaked == [], f"{module_name} の名前を公開している: {leaked}"


def test_the_public_entry_imports_only_the_core_layer() -> None:
    """`__init__.py` のモジュール直下 import が中核層に限られる（要件 10.3, 5.7）。

    ⚠️ **許可リスト方式である。** 「`build123d` を import しない」だけを
    禁じると、CAD 層のモジュール（関数内の遅延 import で形状ライブラリを引く）を
    経由した到達を見逃す。
    """
    imports = _module_level_import_targets(SRC_DIR / "__init__.py")
    assert imports, "`__init__.py` がモジュール直下で何も import していない"

    allowed = {f"{PACKAGE}.{name}" for name in CORE_MODULES} | {"__future__"}
    unexpected = sorted(set(imports) - allowed)
    assert unexpected == [], f"入口が中核層の外を import している: {unexpected}"


# ---------------------------------------------------------------------------
# 2. design.md との突き合わせ（「公開シンボルの一覧が設計の記述と一致する」）
# ---------------------------------------------------------------------------


def _design_public_api_names() -> tuple[str, ...]:
    """design.md `#### PublicApi` の `__all__` を読み取る。"""
    text = DESIGN_PATH.read_text(encoding="utf-8")
    heading = text.index("#### PublicApi")
    fence = text.index("```python", heading)
    body_start = fence + len("```python")
    body_end = text.index("```", body_start)
    tree = ast.parse(text[body_start:body_end])
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError("design.md `#### PublicApi` に __all__ が見つからない")


def test_the_public_contract_covers_every_name_the_design_declares() -> None:
    """design.md が挙げる公開名を1つも落としていない（「設計の記述と一致する」）。"""
    design_names = _design_public_api_names()
    assert len(design_names) == 42, f"design.md の宣言数が変わった: {len(design_names)}"

    dropped = sorted(set(design_names) - set(PUBLIC_NAMES))
    assert dropped == [], f"design.md が挙げる公開名を落としている: {dropped}"


def test_the_additions_beyond_the_design_are_exactly_the_recorded_ones() -> None:
    """design.md との差分が、裁定として記録した名前ちょうどである。

    ⚠️ **差分をゼロにできないのは design.md 側が古いためである**
    （tasks.md Note 5.1(f)③ が `SelectionResult` / `load_selection_result` /
    `CRITERIA_ITEMS` の欠落を本タスクへ送っている）。差分を**明示の集合**として
    固定することで、「設計に無い名前が黙って増える」ことと区別する。
    """
    design_names = _design_public_api_names()

    additions = set(PUBLIC_NAMES) - set(design_names)
    assert additions == set(RECORDED_ADDITIONS)


def test_the_names_left_unpublished_on_purpose_still_exist_in_their_module() -> None:
    """公開しないと裁定した名前が、モジュール側には在る（裁定が空振りでない）。

    ⚠️ 名前が消えていれば「公開しない」という裁定自体が意味を失う。
    tasks.md Note 2.4(e) / 4.2(e) が design.md との整合を本タスクへ送っており、
    本テストはその裁定（PublicApi はモジュール公開面より狭い）を固定する。
    """
    for name, module_name in DELIBERATELY_UNPUBLISHED.items():
        names = _module_all_via_ast(SRC_DIR / f"{module_name}.py")
        assert name in names, f"{module_name}.{name} が存在しない"
        assert name not in PUBLIC_NAMES


# ---------------------------------------------------------------------------
# 3. 要件 10.5: 再検証の引き金としての記録（設計の該当箇所との対応付け）
# ---------------------------------------------------------------------------


def _design_revalidation_triggers() -> dict[int, str]:
    """design.md `### Revalidation Triggers` の番号付き箇条を返す。"""
    text = DESIGN_PATH.read_text(encoding="utf-8")
    start = text.index("### Revalidation Triggers")
    end = text.index("\n## ", start)
    section = text[start:end]
    return {
        int(number): body.strip()
        for number, body in re.findall(r"^(\d+)\.\s+(.*)$", section, flags=re.MULTILINE)
    }


def test_the_design_records_the_public_api_as_a_revalidation_trigger() -> None:
    """公開 API の変更が再検証を要する変更として design.md に記録されている（要件 10.5）。

    対応箇所: design.md `### Revalidation Triggers` 項目4（公開シンボルの
    追加・削除・意味変更）と項目1（構造・キー名・単位の変更）。
    要件 10.5 の「意味・単位・構造」はこの2項目に分かれて記録されている。
    """
    triggers = _design_revalidation_triggers()

    assert "公開 API" in triggers[4]
    assert "catch_mechanism.__init__" in triggers[4]
    assert "シンボル追加・削除・意味変更" in triggers[4]
    assert "構造・キー名・単位" in triggers[1]


def test_the_public_entry_docstring_points_at_the_revalidation_triggers() -> None:
    """入口の docstring が再検証の記録先を名指ししている（要件 10.5）。

    ⚠️ 一覧を読んだ利用者が「変えてよいか」を判断する場所は design.md の
    当該節であり、その所在が入口から辿れなければ対応付けが失われる。
    """
    package = importlib.import_module(PACKAGE)
    doc = package.__doc__ or ""

    assert "Revalidation Triggers" in doc
    assert "再検証" in doc


# ---------------------------------------------------------------------------
# 4. 要件 10.6: 下流の部品を扱わないことを公開シンボルの範囲として表現する
# ---------------------------------------------------------------------------

DOWNSTREAM_PART_WORDS: Mapping[str, tuple[str, ...]] = {
    "駆動ベース": ("drive", "drivetrain", "base", "chassis", "wheel", "motor"),
    "固定アダプタ": ("adapter", "mount", "clamp", "bracket", "fixture"),
    "トレイ類": ("tray", "magazine", "hopper", "feeder", "rack"),
    "整備スタンド": ("stand", "jig", "cradle", "dock", "maintenance"),
}
"""design.md `### Out of Boundary` が挙げる下流部品の語彙。

⚠️ **語（word）単位で照合する。** 部分文字列で照合すると `GeometryBaseline`
が `base` に当たってしまい、検査が「たまたま通っている」状態になる
（`test_the_downstream_part_detector_does_not_flag_baseline_names` が
この区別を固定する）。
"""


def _identifier_words(name: str) -> tuple[str, ...]:
    """識別子を語へ分解する（`snake_case` / `CamelCase` / `UPPER_SNAKE` に対応）。"""
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", name)
    return tuple(part.lower() for part in parts)


def find_downstream_part_names(names: Iterable[str]) -> list[str]:
    """公開名の並びから、下流部品を名乗るものを検出する（要件 10.6）。

    `"DriveBase (駆動ベース: base, drive)"` の形の文字列の並びを返す。
    空列であれば「下流の部品を公開面に持たない」。
    """
    hits: list[str] = []
    for name in names:
        words = set(_identifier_words(str(name)))
        for part, vocabulary in DOWNSTREAM_PART_WORDS.items():
            matched = sorted(words & set(vocabulary))
            if matched:
                hits.append(f"{name} ({part}: {', '.join(matched)})")
    return hits


def test_no_published_name_refers_to_a_downstream_part() -> None:
    """公開シンボルの範囲が下流の部品に及んでいない（要件 10.6）。

    設計の該当箇所: design.md `### Out of Boundary`
    「駆動ベース・固定アダプタ・トレイ・整備スタンドの形状
    （`chassis-mechanism` が本基盤を消費する）」。
    """
    package = importlib.import_module(PACKAGE)

    hits = find_downstream_part_names(package.__all__)
    assert hits == [], f"下流部品を名乗る公開名がある: {hits}"


@pytest.mark.parametrize(
    ("surface", "expected_part"),
    [
        (("DriveBaseSpec",), "駆動ベース"),
        (("load_can_mount_adapter",), "固定アダプタ"),
        (("TRAY_SLOT_COUNT",), "トレイ類"),
        (("MaintenanceStand",), "整備スタンド"),
        (("chassis_bolt_pattern",), "駆動ベース"),
    ],
)
def test_the_downstream_part_detector_flags_a_surface_that_names_them(
    surface: tuple[str, ...], expected_part: str
) -> None:
    """違反ケース: 下流部品を名乗る架空の公開面は検出される。

    ⚠️ 検出器が実際に働くことを示さないと、
    `test_no_published_name_refers_to_a_downstream_part` は恒真になりうる。
    """
    hits = find_downstream_part_names(surface)

    assert hits != []
    assert expected_part in hits[0]


def test_the_downstream_part_detector_does_not_flag_baseline_names() -> None:
    """誤検知の否定: `GeometryBaseline` は `base` に当たらない（語単位の照合）。

    現に公開している名前が「たまたま」通っているのではなく、語単位の照合という
    規則によって通っていることを示す。
    """
    assert find_downstream_part_names(("GeometryBaseline", "load_baseline")) == []
    assert find_downstream_part_names(("Baseline_Drive",)) != []


def test_the_design_out_of_boundary_still_names_the_four_downstream_parts() -> None:
    """検出器の語彙の出所（design.md `### Out of Boundary`）が変わっていない。

    設計側が下流部品の一覧を書き換えたら、本ファイルの語彙表も追随が要る。
    """
    text = DESIGN_PATH.read_text(encoding="utf-8")
    start = text.index("### Out of Boundary")
    section = text[start : text.index("\n### ", start)]

    for part in ("駆動ベース", "固定アダプタ", "トレイ", "整備スタンド"):
        assert part in section, f"design.md `### Out of Boundary` に {part} が無い"
    assert "chassis-mechanism" in section


def test_the_public_surface_publishes_no_geometry_generation_entry_point() -> None:
    """形状生成そのものを公開しない（要件 10.6 の構造的な現れ）。

    ⚠️ 下流の部品を作らないことは、部品名を出さないことに留まらない。
    `catch_mechanism` の入口は**中核層の7モジュール**に閉じており、形状を作る
    手段も生成物を書き出す手段も一切公開しない。
    """
    assert set(PUBLIC_CONTRACT) == {
        "errors",
        "params",
        "config",
        "selection",
        "tolerance",
        "constraints",
        "metrics",
    }
    package = importlib.import_module(PACKAGE)
    for name in package.__all__:
        assert not str(name).startswith("build_"), f"形状生成を公開している: {name}"
        assert not str(name).startswith("export_"), f"生成物の書き出しを公開している: {name}"


# ---------------------------------------------------------------------------
# 5. 要件 10.1 / 10.2 / 10.4: 値・単位・出所が入口から取れる
# ---------------------------------------------------------------------------

#: 要件 10.1 が名指しする5項目（フィールド名 → `PARAMETER_PATHS` のパスと単位）。
PUBLISHED_MEASUREMENTS: Mapping[str, tuple[str, str]] = {
    "bottom_outer_diameter_mm": ("trash_can.bottom_outer_diameter_mm", "mm"),
    "bottom_flat_diameter_mm": ("trash_can.bottom_flat_diameter_mm", "mm"),
    "taper_deg": ("trash_can.taper_deg", "deg"),
    "height_mm": ("trash_can.height_mm", "mm"),
    "mass_g": ("trash_can.mass_g", "g"),
}


def test_downstream_reads_the_trash_can_measurements_through_the_public_entry() -> None:
    """底の外径・平面部径・テーパー角・高さ・重量を入口から取得できる（要件 10.1）。

    ⚠️ **値をリテラルで固定しない。** 値の正は
    `configs/catch_mechanism/dimensions.json` にあり（タスク 5.2）、本ファイルが
    数値を持てばそれ自体が第2の定義になる。ここで固定するのは「入口から、型の
    ついた正の値として取り出せる」ことである。
    """
    package = importlib.import_module(PACKAGE)

    params = package.load_params()
    assert isinstance(params, package.MechanismParams)
    can = params.trash_can
    assert isinstance(can, package.TrashCanMeasurements)

    for field_name in PUBLISHED_MEASUREMENTS:
        value = getattr(can, field_name)
        assert isinstance(value, float), f"{field_name} が float でない: {value!r}"
        assert value > 0.0, f"{field_name} が正でない: {value!r}"

    assert can.model_id, "どの実物を測った値なのかが入口から辿れない"


def test_every_published_measurement_carries_a_unit_and_a_provenance() -> None:
    """公開する各項目に単位と出所が併記される（要件 10.4）。

    出所表に現れないパスは `ASSUMED` として扱う運用である
    （design.md「Logical Data Model」/「実測を名乗るには明示が要る」）。
    """
    package = importlib.import_module(PACKAGE)
    params = package.load_params()

    for field_name, (path, unit) in PUBLISHED_MEASUREMENTS.items():
        entry = package.PARAMETER_PATHS[path]
        assert entry.path == path
        assert entry.unit == unit, f"{field_name} の単位が {entry.unit!r} になっている"
        assert entry.value_type is float

        provenance = params.provenance.get(path, package.Provenance.ASSUMED)
        assert isinstance(provenance, package.Provenance)

    # 出所の2値が入口から区別できる（要件 10.4 の「利用側が区別できる」）。
    assert set(package.Provenance) == {
        package.Provenance.MEASURED,
        package.Provenance.ASSUMED,
    }
    assert (
        package.Provenance.weakest(
            package.Provenance.MEASURED, package.Provenance.ASSUMED
        )
        is package.Provenance.ASSUMED
    )

    # 表が「全部 assumed」で塗り潰されていないこと（取得が空振りでない証拠）。
    published = {
        params.provenance.get(path, package.Provenance.ASSUMED)
        for path, _ in PUBLISHED_MEASUREMENTS.values()
    }
    assert package.Provenance.MEASURED in published


def test_downstream_reads_the_printing_constraints_and_joint_policy() -> None:
    """造形制約と継手方針を、定義を再実装せずに入口から参照できる（要件 10.2）。

    型・値・許可材料の一覧・**検査関数**のすべてが入口から届くことを確かめる。
    下流が同じ判定（材料の可否・支圧面積の下限）を書き直す必要が無い。
    """
    package = importlib.import_module(PACKAGE)
    params = package.load_params()

    printing = params.printing
    assert isinstance(printing, package.PrintingConstraints)
    assert printing.material in package.ALLOWED_MATERIALS
    package.check_material(printing)

    # 許可一覧の判定そのものが公開されている（構築時検証を迂回した個体でも働く）。
    bypassed = dataclasses.replace(printing)
    object.__setattr__(bypassed, "material", "ABS")
    with pytest.raises(package.ParameterError):
        package.check_material(bypassed)

    joint = params.joint
    assert isinstance(joint, package.JointPolicy)
    assert joint.bolt_designation
    package.check_joint(joint, joint.min_bearing_area_mm2)
    with pytest.raises(package.ParameterError):
        package.check_joint(joint, joint.min_bearing_area_mm2 / 2.0)


def test_downstream_reads_the_selection_record_through_the_public_entry() -> None:
    """どの実物を選んだかの記録が入口から辿れる（要件 6.8 / 10.1 の前提）。

    `RECORDED_ADDITIONS` の `SelectionResult` / `load_selection_result` /
    `CRITERIA_ITEMS` を公開する根拠がこれである。
    """
    package = importlib.import_module(PACKAGE)

    result = package.load_selection_result()
    assert isinstance(result, package.SelectionResult)
    assert result.selected_identifier == package.load_params().trash_can.model_id
    assert set(result.decisive_criteria_items) <= set(package.CRITERIA_ITEMS)


def test_the_shipped_tolerance_record_is_reachable_from_the_public_entry() -> None:
    """公開された `load_derivation` を既定パスの公開だけで呼べる（要件 10.1）。

    `RECORDED_ADDITIONS` の `DEFAULT_DERIVATION_PATH` を公開する根拠がこれである
    （`load_derivation` は `path` を必須引数に取るため、既定パスが非公開だと
    利用側が出荷記録の場所を書き写すことになる）。
    """
    package = importlib.import_module(PACKAGE)

    derivation = package.load_derivation(package.DEFAULT_DERIVATION_PATH)
    assert isinstance(derivation, package.ToleranceDerivation)
    assert derivation.position_tolerance_mm > 0.0
    assert isinstance(derivation.provenance, package.Provenance)
    assert all(isinstance(item, package.ToleranceInput) for item in derivation.inputs)


def test_the_metrics_presence_encoding_is_reachable_from_the_public_entry() -> None:
    """`MetricsMismatch` の在／不在の符号化が入口から取れる（要件 10.2 の趣旨）。

    `RECORDED_ADDITIONS` の `PRESENCE_FIELD` / `PRESENT` / `ABSENT` を公開する
    根拠がこれである（公開済みの `compare_metrics` の戻り値を、値を書き写さずに
    読み解けること）。
    """
    package = importlib.import_module(PACKAGE)

    baseline = package.load_baseline()
    mismatches = package.compare_metrics(baseline, {})
    assert mismatches, "空の再生成結果に対して不一致が1件も出ないのはおかしい"
    for mismatch in mismatches:
        assert isinstance(mismatch, package.MetricsMismatch)
        assert mismatch.field_name == package.PRESENCE_FIELD
        assert mismatch.recorded == package.PRESENT
        assert mismatch.regenerated == package.ABSENT


# ---------------------------------------------------------------------------
# 6. 要件 10.3 / 5.7: 形状ライブラリ非導入の環境で成立する
# ---------------------------------------------------------------------------

_STUB_SOURCE = 'raise ImportError("build123d is blocked by the CAD-absence stub")\n'

_PROBE = """
import json
import sys

try:
    import build123d  # noqa: F401
except ImportError:
    blocked = True
else:
    blocked = False

import catch_mechanism as cm

params = cm.load_params()
can = params.trash_can
report = {
    "stub_blocked_the_shape_library": blocked,
    "all": list(cm.__all__),
    "missing": [name for name in cm.__all__ if not hasattr(cm, name)],
    "shape_library_modules": sorted(
        name for name in sys.modules if name.split(".")[0] == "build123d"
    ),
    "cad_layer_modules": sorted(
        name
        for name in sys.modules
        if name in ("catch_mechanism.shapes", "catch_mechanism.export")
    ),
    "measurements": {
        "bottom_outer_diameter_mm": can.bottom_outer_diameter_mm,
        "bottom_flat_diameter_mm": can.bottom_flat_diameter_mm,
        "taper_deg": can.taper_deg,
        "height_mm": can.height_mm,
        "mass_g": can.mass_g,
    },
    "material": params.printing.material,
    "bolt_designation": params.joint.bolt_designation,
    "provenance": {path: str(value) for path, value in params.provenance.items()},
    "selected_identifier": cm.load_selection_result().selected_identifier,
    "position_tolerance_mm": cm.load_derivation(
        cm.DEFAULT_DERIVATION_PATH
    ).position_tolerance_mm,
}
print(json.dumps(report))
"""


def _run_with_cad_blocked(stub_dir: Path, code: str) -> subprocess.CompletedProcess[str]:
    """形状ライブラリを import 不能にした子プロセスで `code` を実行する。

    ⚠️ スタブは `tmp_path`（pytest が管理する一時ディレクトリ）に置く。
    `/tmp` 直下へ置くと、実行の途中で消えて遮断が黙って無効になる事故がある
    （tasks.md Note 4.2(f)）。遮断が効いていることは
    `test_the_cad_blocking_stub_actually_blocks_the_shape_library` と、
    本経路の戻り値 `stub_blocked_the_shape_library` の**両方**で確かめる。
    """
    (stub_dir / "build123d.py").write_text(_STUB_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{stub_dir}{os.pathsep}{existing}" if existing else str(stub_dir)
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


def test_the_cad_blocking_stub_actually_blocks_the_shape_library(tmp_path: Path) -> None:
    """遮断スタブが効いていることを先に確かめる（後続の検査を空振りにしないため）。

    ⚠️ tasks.md Note 4.2(f): 遮断したつもりの実行が CAD 導入時と同じ結果を
    返した事故がある。**遮断が効いていること自体を毎回検査する。**
    """
    completed = _run_with_cad_blocked(tmp_path, "import build123d")

    assert completed.returncode != 0, completed.stdout
    assert "ImportError" in completed.stderr


def test_the_public_contract_holds_without_the_shape_library(tmp_path: Path) -> None:
    """形状ライブラリ非導入の環境で公開契約が丸ごと成立する（要件 10.3, 5.7）。

    入口の import・全公開名の束縛・要件 10.1 / 10.2 / 10.4 の値の取得までを、
    形状ライブラリを import 不能にした子プロセスで通す。⚠️ 併せて
    `sys.modules` に形状ライブラリと CAD 層のモジュールが**現れない**ことを
    見る（遅延 import であっても入口が触れていないことの直接の証拠）。
    """
    completed = _run_with_cad_blocked(tmp_path, _PROBE)

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)

    assert report["stub_blocked_the_shape_library"] is True
    assert report["shape_library_modules"] == []
    assert report["cad_layer_modules"] == []
    assert report["missing"] == []
    assert report["all"] == list(PUBLIC_NAMES)

    for field_name in PUBLISHED_MEASUREMENTS:
        assert report["measurements"][field_name] > 0.0
    assert report["material"]
    assert report["bolt_designation"]
    assert set(report["provenance"].values()) <= {"measured", "assumed"}
    assert report["selected_identifier"]
    assert report["position_tolerance_mm"] > 0.0
