"""上流から借りる項目が公開入口から取得でき、CAD 無しで使えること（要件 1.3、タスク 1.6）。

要件 1.3 は「上流が公開している寸法値・造形制約・継手方針を**参照して用い**、
同じ値を自身の設定ファイルへ再定義しない」を求める。参照して用いる以上、
**借りている項目が上流の公開入口から実際に取れて、実際に使える**ことが本 Spec の
前提条件になる。本ファイルはその前提を機械的に固定する。

design.md「Existing Architecture Analysis」は
「⚠️ **本 Spec はこの契約の唯一の消費者である**」と述べる。上流側の
`tests/catch_mechanism/test_catch_downstream_contract.py` は `__all__` を
**上流の側**から（＝公開面の全体として）固定するが、本ファイルは**消費者の側**から
（＝本 Spec が実際に依存する部分集合として）固定する。上流がこの9項目のどれかを
落としたとき、落ちるべきなのは本ファイルである。

**借りる9項目**（tasks.md タスク 1.6 が名指しする並び。`BORROWED_ITEMS` が正）:

1. 造形制約 — `PrintingConstraints` / `ALLOWED_MATERIALS`
2. 継手方針 — `JointPolicy`
3. ゴミ箱の採寸値 — `TrashCanMeasurements`（`load_params()` 経由）
4. 出所の型 — `Provenance` / `PARAMETER_PATHS`
5. 造形可能寸法と材料と継手の検査 — `check_envelope` / `check_material` / `check_joint`
6. 円環の分割数導出 — `required_segment_count`
7. 形状指標の型 — `PartMetrics` / `GeometryBaseline` / `load_baseline`
8. 形状指標の照合 — `compare_metrics` / `MetricsMismatch` と在／不在の符号化
9. 質量の目安 — `estimate_mass_g`

⚠️ **存在（`hasattr`）だけでは足りない。** 各項目について「本 Spec が下流で行う
呼び方そのもの」を1回通す（`_exercise_*` 群）。名前が残ったまま意味が変わった場合も
落ちるようにするためである。

**役割分担**:

- `tests/chassis_mechanism/test_chassis_boundaries.py`（タスク 1.5）は
  `src/chassis_mechanism/*.py` を `ast` で読み、上流へ**公開入口からのみ**到達する
  ことと、本 Spec のパラメータパスが上流のコンポーネント名と衝突しないことを見る
  （import の**衛生**）。⚠️ **本ファイルはそれを繰り返さない。**
- 本ファイルは実際に `import catch_mechanism` して**借りた項目が働く**ことを見る
  （契約の**中身**）。

**再検証の連動**: design.md「Revalidation Triggers」は
「⚠️ 上流側では、**`catch-mechanism` の Revalidation Triggers 項目1・4・5** が
発火したとき本 Spec の再検証が必要になる」と記録する。項目1（`dimensions.json` の
構造・キー名・単位）・項目4（公開 API のシンボル）・項目5（`GeometryBaseline` の
形式）は、それぞれ本ファイルの `_exercise_trash_can_measurements` /
`test_every_borrowed_name_is_reachable_from_the_package_root` /
`_exercise_geometry_metric_types` が触れている。上流の該当変更は本ファイルを
落とすことで再検証を要求する。

**ファイル名について**: design.md「Directory Structure」の
`test_chassis_upstream_contract.py` そのものである（`tests/` に `__init__.py` が
無くテストモジュール名がフラットであるため `test_chassis_` 接頭辞が要る）。
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import importlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve()
REPO_ROOT = MODULE_PATH.parents[2]
UPSTREAM_SRC_DIR = REPO_ROOT / "src" / "catch_mechanism"
CHASSIS_SRC_DIR = REPO_ROOT / "src" / "chassis_mechanism"
DESIGN_PATH = REPO_ROOT / ".kiro" / "specs" / "chassis-mechanism" / "design.md"

PACKAGE = "catch_mechanism"
"""⚠️ 上流へ触れてよい唯一の名前。内部モジュールは本ファイルでも import しない。"""


# ---------------------------------------------------------------------------
# 借りる項目の表（⚠️ ここが「本 Spec が上流に依存している範囲」の正である）
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BorrowedItem:
    """借りる項目1件（tasks.md タスク 1.6 の箇条 → 上流の公開シンボル）。

    Attributes:
        label: タスク記述に現れる呼び名。
        names: その項目のために `catch_mechanism` から取る公開シンボル。
        design_reference: 借りることを宣言している design.md の箇所。
    """

    label: str
    names: tuple[str, ...]
    design_reference: str


BORROWED_ITEMS: Mapping[str, BorrowedItem] = {
    "printing_constraints": BorrowedItem(
        label="造形制約",
        names=("PrintingConstraints", "ALLOWED_MATERIALS"),
        design_reference="Out of Boundary / Technology Stack（造形可能寸法・許可材料の定義は上流）",
    ),
    "joint_policy": BorrowedItem(
        label="継手方針",
        names=("JointPolicy",),
        design_reference="#### Joints Dependencies External `catch_mechanism.JointPolicy`",
    ),
    "trash_can_measurements": BorrowedItem(
        label="ゴミ箱の採寸値",
        names=("TrashCanMeasurements", "MechanismParams", "load_params"),
        design_reference="#### Config「上流の `load_params()` を呼び……取り込んだ `ResolvedParams` を返す」",
    ),
    "provenance_type": BorrowedItem(
        label="出所の型",
        names=("Provenance", "PARAMETER_PATHS"),
        design_reference="#### Params「`Provenance` は**上流の型をそのまま使う**。⚠️ 独自に定義しない」",
    ),
    "build_checks": BorrowedItem(
        label="造形可能寸法と材料と継手の検査",
        names=(
            "Envelope",
            "BuildViolation",
            "check_envelope",
            "check_material",
            "check_joint",
            "ParameterError",
        ),
        design_reference="要件 2.2 / 2.4 / 2.9「上流が公開する検査を用いて確認する」",
    ),
    "segment_count": BorrowedItem(
        label="円環の分割数導出",
        names=("required_segment_count",),
        design_reference="#### Joints「**円環部品**（ゴミ箱固定アダプタ）→ 上流 `required_segment_count` を用いる」",
    ),
    "geometry_metric_types": BorrowedItem(
        label="形状指標の型",
        names=("PartMetrics", "GeometryBaseline", "load_baseline"),
        design_reference="#### Baseline「**型と照合は上流から借りる**。⚠️ **同じ型を再定義しない**」",
    ),
    "geometry_metric_comparison": BorrowedItem(
        label="形状指標の照合",
        names=(
            "compare_metrics",
            "MetricsMismatch",
            "PRESENCE_FIELD",
            "PRESENT",
            "ABSENT",
        ),
        design_reference="#### Baseline「型と照合は上流から借りる」／上流の在・不在の符号化",
    ),
    "mass_estimate": BorrowedItem(
        label="質量の目安",
        names=("estimate_mass_g",),
        design_reference="#### Shapes「質量の目安は上流 `estimate_mass_g` を用いる（要件 7.9）」",
    ),
}
"""借りる9項目。⚠️ **上流がこのどれかを落としたら、落ちるべきなのは本ファイルである。**

キーは本ファイル内の識別子（テスト ID と JSON 報告のキーを兼ねる）。
並びは tasks.md タスク 1.6 の箇条の並びである。
"""

BORROWED_NAMES: tuple[str, ...] = tuple(
    name for item in BORROWED_ITEMS.values() for name in item.names
)


# ---------------------------------------------------------------------------
# 上流が公開していない操作（⚠️ 本 Spec はこれらに依存しない）
# ---------------------------------------------------------------------------

UNPUBLISHED_UPSTREAM_OPERATIONS: Mapping[str, str] = {
    "write_baseline": "metrics",
    "verify_baseline_digest": "metrics",
}
"""上流が**意図して**公開していない、記録を書き換える操作とその由来モジュール。

上流 `src/catch_mechanism/__init__.py` の docstring「公開しないもの」:
「同じ理由で、記録を**書き換える**操作（`metrics.write_baseline`）とその鮮度検査
（`metrics.verify_baseline_digest`）も公開しない——下流は記録を消費するだけで
再生成しない。」

⚠️ **本 Spec は形状指標の記録を書き出す必要がある**（要件 1.12）。しかし上流の
非公開関数を掘り出して使うのではなく、**書き出しだけを自前で実装する**——それが
design.md `#### Baseline`「**書き出しだけ本 Spec が持つ**。上流は `write_baseline`
を公開していない（消費専用の設計）」であり、tasks.md タスク 2.5
「⚠️ **形状指標の型と照合は上流から借り、書き出しだけを実装する**」である。
したがって `src/chassis_mechanism/baseline.py` が**自前の** `write_baseline` を
定義することは違反ではない。違反は「上流のそれへ到達すること」である
（`upstream_operations_reached` はこの区別を実装しており、
`test_the_unpublished_operation_scan_allows_a_local_definition` が固定する）。
"""


def _upstream_aliases(tree: ast.Module) -> frozenset[str]:
    """`import catch_mechanism [as X]` が束縛した名前を集める。"""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}."):
                    aliases.add(alias.asname or alias.name.split(".")[0])
    return frozenset(aliases)


def upstream_operations_reached(source: str) -> list[str]:
    """`source` が上流の**非公開操作**へ到達している箇所を返す（空なら到達なし）。

    ⚠️ **上流経由の到達だけを数える。** 同名の関数をこのパッケージ自身が定義・
    呼び出すこと（タスク 2.5 の `chassis_mechanism.baseline.write_baseline`）は
    違反ではないため、以下の2形だけを検出する。

    1. `from catch_mechanism[...] import write_baseline` — 公開入口にも内部
       モジュールにも掛かる。
    2. `cm.write_baseline` — `import catch_mechanism as cm` で束縛した名前
       （およびその属性連鎖）への属性アクセス。

    Args:
        source: 検査する Python ソース。

    Returns:
        `"cm.write_baseline"` の形の到達箇所の並び（昇順・重複なし）。
    """
    tree = ast.parse(source)
    aliases = _upstream_aliases(tree)
    hits: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == PACKAGE or module.startswith(f"{PACKAGE}."):
                hits.update(
                    f"{module}.{alias.name}"
                    for alias in node.names
                    if alias.name in UNPUBLISHED_UPSTREAM_OPERATIONS
                )
        elif isinstance(node, ast.Attribute):
            if node.attr not in UNPUBLISHED_UPSTREAM_OPERATIONS:
                continue
            base = ast.unparse(node.value)
            if base in aliases or any(base.startswith(f"{a}.") for a in aliases):
                hits.add(f"{base}.{node.attr}")

    return sorted(hits)


def _module_all_via_ast(path: Path) -> tuple[str, ...]:
    """`path` の `__all__` を、モジュールを import せずに読み取る。

    ⚠️ 上流の内部モジュール（`catch_mechanism.metrics`）を import しないため
    （design.md「Allowed Dependencies」）、ソースをテキストとして読む。
    """
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


# ---------------------------------------------------------------------------
# 借りた項目を実際に使う（⚠️ 存在確認ではなく、本 Spec が行う呼び方そのもの）
# ---------------------------------------------------------------------------


def _upstream():  # noqa: ANN202 - 公開入口のモジュールオブジェクト
    """上流の公開入口を返す（⚠️ 内部モジュールへは触れない）。"""
    return importlib.import_module(PACKAGE)


@functools.lru_cache(maxsize=1)
def _params():  # noqa: ANN202 - 上流の `MechanismParams`
    """上流の寸法パラメータを1度だけ読む（不変オブジェクトのため使い回せる）。"""
    return _upstream().load_params()


@functools.lru_cache(maxsize=1)
def _baseline():  # noqa: ANN202 - 上流の `GeometryBaseline`
    """上流の出荷済み形状指標の記録を1度だけ読む。"""
    return _upstream().load_baseline()


def _exercise_printing_constraints() -> dict[str, object]:
    """造形制約: 造形可能寸法・材料・材料密度・余裕を入口から取る。"""
    cm = _upstream()
    printing = _params().printing
    return {
        "is_printing_constraints": isinstance(printing, cm.PrintingConstraints),
        "build_mm": [printing.build_x_mm, printing.build_y_mm, printing.build_z_mm],
        "material": printing.material,
        "material_is_allowed": printing.material in set(cm.ALLOWED_MATERIALS),
        "allowed_materials": sorted(cm.ALLOWED_MATERIALS),
        "material_density_g_cm3": printing.material_density_g_cm3,
        "segment_margin_mm": printing.segment_margin_mm,
    }


def _exercise_joint_policy() -> dict[str, object]:
    """継手方針: ボルト呼び・インサート長・当たり面の下限・ダボ径を入口から取る。"""
    cm = _upstream()
    joint = _params().joint
    return {
        "is_joint_policy": isinstance(joint, cm.JointPolicy),
        "bolt_designation": joint.bolt_designation,
        "through_hole_diameter_mm": joint.through_hole_diameter_mm,
        "insert_outer_diameter_mm": joint.insert_outer_diameter_mm,
        "insert_length_mm": joint.insert_length_mm,
        "dowel_diameter_mm": joint.dowel_diameter_mm,
        "min_bearing_area_mm2": joint.min_bearing_area_mm2,
    }


#: 本 Spec がゴミ箱固定アダプタの導出に用いる採寸項目（要件 6.1 / 6.7 / 6.9）と単位。
#: ⚠️ **値をここへ書かない。** 値の正は `configs/catch_mechanism/dimensions.json`
#: であり、本ファイルが数値を持てばそれ自体が要件 1.3 違反（第2の定義）になる。
BORROWED_MEASUREMENTS: Mapping[str, str] = {
    "bottom_outer_diameter_mm": "mm",
    "bottom_flat_diameter_mm": "mm",
    "opening_inner_diameter_mm": "mm",
    "top_outer_diameter_mm": "mm",
    "height_mm": "mm",
    "taper_deg": "deg",
    "mass_g": "g",
    "bottom_thickness_mm": "mm",
}


def _exercise_trash_can_measurements() -> dict[str, object]:
    """ゴミ箱の採寸値: 本 Spec が座の導出に使う項目を入口から取る。"""
    cm = _upstream()
    params = _params()
    can = params.trash_can
    return {
        "is_mechanism_params": isinstance(params, cm.MechanismParams),
        "is_trash_can_measurements": isinstance(can, cm.TrashCanMeasurements),
        "model_id": can.model_id,
        "values": {name: getattr(can, name) for name in BORROWED_MEASUREMENTS},
        "flat_within_outer": (
            can.bottom_flat_diameter_mm <= can.bottom_outer_diameter_mm
        ),
    }


def _exercise_provenance_type() -> dict[str, object]:
    """出所の型: 仮値と実測値を利用側が区別でき、単位が表から引ける。"""
    cm = _upstream()
    params = _params()
    assumed = cm.Provenance.ASSUMED
    measured = cm.Provenance.MEASURED
    paths = {name: f"trash_can.{name}" for name in BORROWED_MEASUREMENTS}
    return {
        "members": sorted(str(member) for member in cm.Provenance),
        "weakest_of_mixed": str(cm.Provenance.weakest(measured, assumed)),
        "weakest_of_measured": str(cm.Provenance.weakest(measured, measured)),
        "units": {
            name: cm.PARAMETER_PATHS[path].unit for name, path in paths.items()
        },
        "paths_are_self_consistent": all(
            cm.PARAMETER_PATHS[path].path == path for path in paths.values()
        ),
        "provenance": {
            name: str(params.provenance.get(path, assumed))
            for name, path in paths.items()
        },
        "all_are_provenance_instances": all(
            isinstance(params.provenance.get(path, assumed), cm.Provenance)
            for path in paths.values()
        ),
    }


DISALLOWED_MATERIAL = "ABS"
"""許可一覧に無い材料。⚠️ design.md「Technology Stack」が
「⚠️ **荷重部材で最も欲しい ASA は造形機の制約で選べない**」と記録しており、
許可一覧が `PETG` / `PLA` に閉じていることを前提に検査の**否定側**を作る
（`_exercise_build_checks` が一覧に含まれないことを毎回確かめてから使う）。"""


def _exercise_build_checks() -> dict[str, object]:
    """造形可能寸法・材料・継手の検査: 判定を再実装せずに呼び、結果を消費する。"""
    cm = _upstream()
    printing = _params().printing
    joint = _params().joint

    fitting = cm.Envelope(
        x_mm=printing.build_x_mm / 2.0,
        y_mm=printing.build_y_mm / 2.0,
        z_mm=printing.build_z_mm / 2.0,
    )
    oversized = cm.Envelope(
        x_mm=printing.build_x_mm + 10.0,
        y_mm=printing.build_y_mm / 2.0,
        z_mm=printing.build_z_mm + 25.0,
    )

    violations = cm.check_envelope("chassis_drive_base", oversized, printing)

    # 材料の否定側: 構築時検証を迂回した個体を作る（上流の判定そのものが働くこと）。
    bypassed = dataclasses.replace(printing)
    object.__setattr__(bypassed, "material", DISALLOWED_MATERIAL)

    def _raises(call: Callable[[], object]) -> bool:
        try:
            call()
        except cm.ParameterError:
            return True
        return False

    return {
        "disallowed_material_is_really_disallowed": (
            DISALLOWED_MATERIAL not in set(cm.ALLOWED_MATERIALS)
        ),
        "fitting_envelope_has_no_violation": (
            cm.check_envelope("chassis_drive_base", fitting, printing) == ()
        ),
        "violations_are_build_violations": all(
            isinstance(item, cm.BuildViolation) for item in violations
        ),
        "violations": [
            {
                "part_name": item.part_name,
                "axis": item.axis,
                "envelope_mm": item.envelope_mm,
                "limit_mm": item.limit_mm,
                "excess_mm": item.excess_mm,
            }
            for item in violations
        ],
        "check_material_accepts_the_configured_material": (
            cm.check_material(printing) is None
        ),
        "check_material_rejects_a_disallowed_material": _raises(
            lambda: cm.check_material(bypassed)
        ),
        "check_joint_accepts_the_limit": (
            cm.check_joint(joint, joint.min_bearing_area_mm2) is None
        ),
        "check_joint_rejects_below_the_limit": _raises(
            lambda: cm.check_joint(joint, joint.min_bearing_area_mm2 / 2.0)
        ),
    }


def _exercise_segment_count() -> dict[str, object]:
    """円環の分割数導出: ゴミ箱固定アダプタの外径から分割数を導く（手で決めない）。

    要件 2.1「手で決めた分割数を設定値として持たない」の依存先がこれである。
    """
    cm = _upstream()
    printing = _params().printing
    can = _params().trash_can

    adapter_outer_diameter_mm = can.bottom_outer_diameter_mm
    small_diameter_mm = min(printing.build_x_mm, printing.build_y_mm) / 4.0
    # 造形可能寸法を超えるが、分割すれば収まる径。ここが 1 のままなら
    # 「分割しない実装」であり、導出しているように見えて導出していない。
    # ⚠️ 極端に大きな径を使わないこと。半径方向の広がりは分割数を増やしても
    # 縮まないため、上流は分割で解決しない径を `GeometryError` で拒む。
    splitting_diameter_mm = (
        min(printing.build_x_mm, printing.build_y_mm) - printing.segment_margin_mm
    ) * 1.5

    return {
        "adapter_segment_count": cm.required_segment_count(
            adapter_outer_diameter_mm, printing
        ),
        "small_ring_segment_count": cm.required_segment_count(
            small_diameter_mm, printing
        ),
        "splitting_ring_segment_count": cm.required_segment_count(
            splitting_diameter_mm, printing
        ),
    }


def _exercise_geometry_metric_types() -> dict[str, object]:
    """形状指標の型: 記録を読み、`PartMetrics` を組み、不正値が拒まれる。"""
    cm = _upstream()
    baseline = _baseline()

    sample = cm.PartMetrics(
        part_name="chassis_drive_base",
        volume_mm3=1234.5,
        bbox_mm=(10.0, 20.0, 30.0),
        solid_count=1,
    )

    try:
        cm.PartMetrics(
            part_name="chassis_drive_base",
            volume_mm3=1234.5,
            bbox_mm=(10.0, 20.0, 30.0),
            solid_count=0,
        )
    except cm.ParameterError:
        rejects_empty_solid = True
    else:
        rejects_empty_solid = False

    return {
        "is_geometry_baseline": isinstance(baseline, cm.GeometryBaseline),
        "schema_version": baseline.schema_version,
        "digest_prefix": baseline.parameters_digest.split(":")[0],
        "volume_rel_tolerance": baseline.volume_rel_tolerance,
        "bbox_abs_tolerance_mm": baseline.bbox_abs_tolerance_mm,
        "part_names": sorted(baseline.parts),
        "parts_are_part_metrics": all(
            isinstance(part, cm.PartMetrics) for part in baseline.parts.values()
        ),
        "sample_bbox_axes": len(sample.bbox_mm),
        "sample_volume_mm3": sample.volume_mm3,
        "rejects_a_part_without_a_solid": rejects_empty_solid,
    }


def _exercise_geometry_metric_comparison() -> dict[str, object]:
    """形状指標の照合: 一致・体積の食い違い・不在の3通りを上流の照合で判別する。"""
    cm = _upstream()
    baseline = _baseline()

    identical = cm.compare_metrics(baseline, dict(baseline.parts))

    part_name = sorted(baseline.parts)[0]
    recorded = baseline.parts[part_name]
    mutated = dict(baseline.parts)
    mutated[part_name] = cm.PartMetrics(
        part_name=recorded.part_name,
        volume_mm3=recorded.volume_mm3 * 2.0,
        bbox_mm=recorded.bbox_mm,
        solid_count=recorded.solid_count,
    )
    volume_mismatches = cm.compare_metrics(baseline, mutated)

    absent = cm.compare_metrics(baseline, {})

    return {
        "identical_metrics_report_no_mismatch": identical == (),
        "mutated_volume_fields": sorted(
            {item.field_name for item in volume_mismatches}
        ),
        "mutated_volume_parts": sorted({item.part_name for item in volume_mismatches}),
        "mismatches_are_metrics_mismatch": all(
            isinstance(item, cm.MetricsMismatch) for item in volume_mismatches
        ),
        "presence_field": cm.PRESENCE_FIELD,
        "present": cm.PRESENT,
        "absent": cm.ABSENT,
        "absent_fields": sorted({item.field_name for item in absent}),
        # ⚠️ 組ではなく列で返す。子プロセスとの照合は JSON を経由するため、
        # 組のまま返すと本プロセス側だけが組で、比較が必ず食い違う。
        "absent_encoding": [
            list(pair)
            for pair in sorted({(item.recorded, item.regenerated) for item in absent})
        ],
        "absent_count": len(absent),
        "recorded_part_count": len(baseline.parts),
    }


def _exercise_mass_estimate() -> dict[str, object]:
    """質量の目安: 体積と材料密度から目安を得る（要件 7.9 の依存先）。"""
    cm = _upstream()
    printing = _params().printing
    baseline = _baseline()

    density = printing.material_density_g_cm3
    part_name = sorted(baseline.parts)[0]
    volume_mm3 = baseline.parts[part_name].volume_mm3

    return {
        "one_cm3_of_the_configured_material_g": cm.estimate_mass_g(1000.0, density),
        "density_g_cm3": density,
        "recorded_part_name": part_name,
        "recorded_part_mass_g": cm.estimate_mass_g(volume_mm3, density),
        "recorded_part_volume_mm3": volume_mm3,
    }


EXERCISES: Mapping[str, Callable[[], dict[str, object]]] = {
    "printing_constraints": _exercise_printing_constraints,
    "joint_policy": _exercise_joint_policy,
    "trash_can_measurements": _exercise_trash_can_measurements,
    "provenance_type": _exercise_provenance_type,
    "build_checks": _exercise_build_checks,
    "segment_count": _exercise_segment_count,
    "geometry_metric_types": _exercise_geometry_metric_types,
    "geometry_metric_comparison": _exercise_geometry_metric_comparison,
    "mass_estimate": _exercise_mass_estimate,
}
"""項目 ID → 借用の実演。⚠️ **`BORROWED_ITEMS` と鍵が一致していること**を
`test_every_borrowed_item_has_an_exercise` が固定する（表だけ増やして実演を
書き忘れると、その項目は「名前があるだけ」で通ってしまう）。"""


# ---------------------------------------------------------------------------
# 子プロセス（形状ライブラリ非導入の環境）から呼ぶ入口
# ---------------------------------------------------------------------------


def borrowed_item_report() -> dict[str, object]:
    """借りる9項目をすべて実演し、JSON 化できる観測の対応表を返す。

    ⚠️ **本関数は子プロセスからも呼ばれる**（`_probe_source`）。形状ライブラリを
    import 不能にした環境で同じ結果が出ることが、要件 1.3 の
    「CAD 無しで参照できる」の直接の証拠になる。
    """
    return {item_id: run() for item_id, run in EXERCISES.items()}


def missing_borrowed_names() -> list[str]:
    """借りる名前のうち、公開入口に束縛されていないものを返す（空であるべき）。"""
    cm = _upstream()
    return [name for name in BORROWED_NAMES if not hasattr(cm, name)]


def borrowed_names_absent_from_all() -> list[str]:
    """借りる名前のうち、`__all__` に載っていないものを返す（空であるべき）。"""
    cm = _upstream()
    published = set(cm.__all__)
    return [name for name in BORROWED_NAMES if name not in published]


def unpublished_operations_on_the_package_root() -> list[str]:
    """公開入口に**現れてしまっている**非公開操作を返す（空であるべき）。"""
    cm = _upstream()
    published = set(cm.__all__)
    return sorted(
        name
        for name in UNPUBLISHED_UPSTREAM_OPERATIONS
        if hasattr(cm, name) or name in published
    )


def as_json_roundtrip(value: object) -> object:
    """JSON を1往復させた値を返す（子プロセスの報告と同じ形へ揃える）。"""
    return json.loads(json.dumps(value))


# ---------------------------------------------------------------------------
# 1. 表そのものの健全性（検査が空振りにならないこと）
# ---------------------------------------------------------------------------


def test_the_borrowed_item_table_covers_the_nine_items_the_task_names() -> None:
    """借りる項目がタスク 1.6 の9項目ちょうどである。

    ⚠️ 項目を1つ落として表を作れば、その項目は誰も検査しないまま通る。
    """
    assert list(BORROWED_ITEMS) == [
        "printing_constraints",
        "joint_policy",
        "trash_can_measurements",
        "provenance_type",
        "build_checks",
        "segment_count",
        "geometry_metric_types",
        "geometry_metric_comparison",
        "mass_estimate",
    ]
    assert len(BORROWED_ITEMS) == 9


def test_every_borrowed_item_has_an_exercise() -> None:
    """全項目に「実際に使う」実演があり、存在確認だけで済ませていない。"""
    assert set(EXERCISES) == set(BORROWED_ITEMS)
    assert list(EXERCISES) == list(BORROWED_ITEMS)
    for item_id, item in BORROWED_ITEMS.items():
        assert item.names, f"{item_id} が借りるシンボルを1つも挙げていない"
        assert item.label, f"{item_id} に呼び名が無い"
        assert item.design_reference, f"{item_id} に設計上の根拠が無い"


def test_no_borrowed_name_is_listed_twice() -> None:
    """同じシンボルを2つの項目から借りていない（表の重複は数え上げを狂わせる）。"""
    duplicates = sorted({n for n in BORROWED_NAMES if BORROWED_NAMES.count(n) > 1})
    assert duplicates == []


# ---------------------------------------------------------------------------
# 2. 借りる名前が公開入口から取れる（⚠️ 消費者の側からの契約の固定）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item_id", list(BORROWED_ITEMS))
def test_every_borrowed_name_is_reachable_from_the_package_root(item_id: str) -> None:
    """`from catch_mechanism import X` が通り、`__all__` にも載っている（要件 1.3）。

    ⚠️ **内部モジュールからではなくパッケージ根から取れること**を見る
    （design.md「Allowed Dependencies」の
    「`import catch_mechanism` / `from catch_mechanism import X` のみ」）。
    `__all__` への掲載まで見るのは、上流の
    「⚠️ この `__all__` に**明示列挙されたものだけ**が公開契約である」に沿うため
    ——列挙されていない束縛に頼れば、上流の再検証の網から漏れる。
    """
    cm = _upstream()
    published = set(cm.__all__)

    for name in BORROWED_ITEMS[item_id].names:
        namespace: dict[str, object] = {}
        exec(f"from {PACKAGE} import {name}", namespace)  # noqa: S102
        assert namespace[name] is getattr(cm, name), (
            f"{name} が公開入口の束縛と一致しない"
        )
        assert name in published, (
            f"{name} は catch_mechanism.__all__ に無い"
            "（公開契約に載っていないものへ依存している）"
        )


def test_no_borrowed_name_is_missing_from_the_public_entry() -> None:
    """借りる名前の欠落が1件も無い（子プロセス側と同じ判定関数を通す）。"""
    assert missing_borrowed_names() == []
    assert borrowed_names_absent_from_all() == []


# ---------------------------------------------------------------------------
# 3. 借りた項目が実際に使える（9項目それぞれ）
# ---------------------------------------------------------------------------


def test_the_printing_constraints_are_usable_from_the_public_entry() -> None:
    """造形制約: 造形可能寸法・材料・密度・余裕が値として取れる（要件 2.4, 2.5）。"""
    report = _exercise_printing_constraints()

    assert report["is_printing_constraints"] is True
    assert all(extent > 0.0 for extent in report["build_mm"])
    assert report["material_is_allowed"] is True
    assert report["allowed_materials"], "許可材料の一覧が空である"
    assert report["material_density_g_cm3"] > 0.0
    assert report["segment_margin_mm"] >= 0.0


def test_the_joint_policy_is_usable_from_the_public_entry() -> None:
    """継手方針: 締結要素の呼びと寸法・当たり面の下限が取れる（要件 2.6, 2.9）。"""
    report = _exercise_joint_policy()

    assert report["is_joint_policy"] is True
    assert report["bolt_designation"], "ボルトの呼びが空である"
    for key in (
        "through_hole_diameter_mm",
        "insert_outer_diameter_mm",
        "insert_length_mm",
        "dowel_diameter_mm",
        "min_bearing_area_mm2",
    ):
        assert report[key] > 0.0, f"{key} が正でない: {report[key]!r}"


def test_the_trash_can_measurements_are_usable_from_the_public_entry() -> None:
    """ゴミ箱の採寸値: 座の導出に使う項目が型のついた正の値として取れる（要件 1.3, 6.1）。

    ⚠️ **値をリテラルで固定しない。** 値の正は
    `configs/catch_mechanism/dimensions.json` にあり、本ファイルが数値を持てば
    それ自体が「同じ値を再定義しない」への違反になる。
    """
    report = _exercise_trash_can_measurements()

    assert report["is_mechanism_params"] is True
    assert report["is_trash_can_measurements"] is True
    assert report["model_id"], "どの実物を測った値なのかが入口から辿れない"
    values = report["values"]
    assert set(values) == set(BORROWED_MEASUREMENTS)
    for name, value in values.items():
        assert isinstance(value, float), f"{name} が float でない: {value!r}"
        assert value > 0.0, f"{name} が正でない: {value!r}"
    assert report["flat_within_outer"] is True


def test_the_provenance_type_distinguishes_measured_from_assumed() -> None:
    """出所の型: 仮値と実測値を利用側が区別でき、単位が表から引ける（要件 1.2, 1.9）。

    design.md `#### Params` は「`Provenance` は**上流の型をそのまま使う**
    （`measured` / `assumed`）。⚠️ 独自に定義しない」と定める。
    """
    report = _exercise_provenance_type()

    assert report["members"] == ["assumed", "measured"]
    assert report["weakest_of_mixed"] == "assumed"
    assert report["weakest_of_measured"] == "measured"
    assert report["units"] == dict(BORROWED_MEASUREMENTS)
    assert report["paths_are_self_consistent"] is True
    assert report["all_are_provenance_instances"] is True
    assert set(report["provenance"].values()) <= {"measured", "assumed"}
    # 出所表が「全部 assumed」で塗り潰されていない（区別が実在する証拠）。
    assert "measured" in set(report["provenance"].values())


def test_the_upstream_checks_are_usable_from_the_public_entry() -> None:
    """検査: 造形可能寸法・材料・継手の判定を再実装せずに呼べる（要件 2.2, 2.4, 2.5, 2.9）。

    ⚠️ **合格側だけでは足りない。** 判定が実際に**落とす**ことまで確かめないと、
    「何も判定していない関数」を借りていても気付けない。
    """
    report = _exercise_build_checks()

    assert report["disallowed_material_is_really_disallowed"] is True
    assert report["fitting_envelope_has_no_violation"] is True
    assert report["violations_are_build_violations"] is True

    violations = report["violations"]
    assert [item["axis"] for item in violations] == ["x", "z"], (
        "超過した軸だけが、x→y→z の順で全件返るはずである"
    )
    for item in violations:
        assert item["part_name"] == "chassis_drive_base"
        assert item["excess_mm"] > 0.0
        assert item["envelope_mm"] - item["limit_mm"] == pytest.approx(
            item["excess_mm"]
        )

    assert report["check_material_accepts_the_configured_material"] is True
    assert report["check_material_rejects_a_disallowed_material"] is True
    assert report["check_joint_accepts_the_limit"] is True
    assert report["check_joint_rejects_below_the_limit"] is True


def test_the_segment_count_derivation_is_usable_from_the_public_entry() -> None:
    """円環の分割数導出: アダプタの外径から分割数が導ける（要件 2.1, 2.2）。

    要件 2.1 は「手で決めた分割数を設定値として持たない」を求めており、
    本 Spec のゴミ箱固定アダプタの分割数はこの導出の戻り値である。
    ⚠️ **両側を見る。** 分割不要の径で 1 が返ること（常に 2 以上を返す実装の排除）
    と、造形可能寸法を大きく超える径で 2 以上が返ること（常に 1 を返す実装の排除）。
    片側だけでは「導出している」ようで導出していない実装を見逃す。
    """
    report = _exercise_segment_count()

    assert report["small_ring_segment_count"] == 1
    assert report["splitting_ring_segment_count"] > 1
    assert report["adapter_segment_count"] >= 1
    assert isinstance(report["adapter_segment_count"], int)
    assert report["adapter_segment_count"] >= report["small_ring_segment_count"]


def test_the_geometry_metric_types_are_usable_from_the_public_entry() -> None:
    """形状指標の型: 記録が読め、指標が組め、立体を持たない指標は拒まれる（要件 1.12）。

    design.md `#### Baseline`「**型と照合は上流から借りる**……⚠️ **同じ型を
    再定義しない**」。
    """
    report = _exercise_geometry_metric_types()

    assert report["is_geometry_baseline"] is True
    assert report["schema_version"]
    assert report["digest_prefix"] == "sha256"
    assert report["volume_rel_tolerance"] >= 0.0
    assert report["bbox_abs_tolerance_mm"] >= 0.0
    assert report["part_names"], "記録が0部品では照合が必ず成功してしまう"
    assert report["parts_are_part_metrics"] is True
    assert report["sample_bbox_axes"] == 3
    assert report["sample_volume_mm3"] > 0.0
    assert report["rejects_a_part_without_a_solid"] is True


def test_the_metric_comparison_is_usable_from_the_public_entry() -> None:
    """照合: 一致・体積の食い違い・不在を上流の照合が判別する（要件 1.12）。

    ⚠️ **一致側と不一致側の両方**を見る。一致側だけなら「常に空を返す照合」を、
    不一致側だけなら「常に何か返す照合」を借りていても気付けない。
    """
    report = _exercise_geometry_metric_comparison()

    assert report["identical_metrics_report_no_mismatch"] is True
    assert report["mutated_volume_fields"] == ["volume_mm3"]
    assert len(report["mutated_volume_parts"]) == 1
    assert report["mismatches_are_metrics_mismatch"] is True

    # 在／不在の符号化（`PRESENCE_FIELD` / `PRESENT` / `ABSENT`）を書き写さずに読む。
    assert report["absent_fields"] == [report["presence_field"]]
    assert report["absent_encoding"] == [[report["present"], report["absent"]]]
    assert report["absent_count"] == report["recorded_part_count"]
    assert report["present"] != report["absent"]


def test_the_mass_estimate_is_usable_from_the_public_entry() -> None:
    """質量の目安: 体積と材料密度から目安が得られる（要件 7.9）。

    design.md `#### Shapes`「質量の目安は上流 `estimate_mass_g` を用いる」。
    単位換算（1 cm^3 = 1000 mm^3）まで上流に委ねられていることを見る。
    """
    report = _exercise_mass_estimate()

    assert report["one_cm3_of_the_configured_material_g"] == pytest.approx(
        report["density_g_cm3"]
    )
    assert report["recorded_part_mass_g"] > 0.0
    assert report["recorded_part_mass_g"] == pytest.approx(
        report["recorded_part_volume_mm3"] / 1000.0 * report["density_g_cm3"]
    )


# ---------------------------------------------------------------------------
# 4. 上流が公開していない操作へ依存していないこと（⚠️ 否定の明示）
# ---------------------------------------------------------------------------


def test_the_record_rewriting_operations_are_not_part_of_the_public_contract() -> None:
    """記録を書き換える操作は公開入口に無い（上流 docstring「公開しないもの」）。

    上流は `metrics.write_baseline`（記録の書き換え）と
    `metrics.verify_baseline_digest`（記録の鮮度検査）を意図して公開していない。
    ⚠️ **本 Spec はこれらを掘り出して使わない。**
    """
    cm = _upstream()
    published = set(cm.__all__)

    for name in UNPUBLISHED_UPSTREAM_OPERATIONS:
        assert name not in published, f"{name} が公開契約に現れている"
        assert not hasattr(cm, name), f"{name} が公開入口に束縛されている"

    assert unpublished_operations_on_the_package_root() == []


def test_the_unpublished_operations_still_exist_in_their_upstream_module() -> None:
    """非公開の裁定が空振りでない（操作は上流側に実在する）。

    ⚠️ 関数ごと消えていれば「公開しない」という裁定は意味を失い、本 Spec が
    「使わない」と宣言している対象も無くなる。⚠️ 検査は `ast` で行う——
    `catch_mechanism.metrics` を import することが既に境界違反である。
    """
    for name, module_name in UNPUBLISHED_UPSTREAM_OPERATIONS.items():
        names = _module_all_via_ast(UPSTREAM_SRC_DIR / f"{module_name}.py")
        assert names, f"{module_name}.py の __all__ を読み取れなかった"
        assert name in names, f"{module_name}.{name} が上流から消えている"


def test_this_spec_does_not_reach_the_unpublished_upstream_operations() -> None:
    """本 Spec の実装コードが上流の非公開操作へ到達していない（要件 1.3 の裏返し）。

    ⚠️ **本 Spec が形状指標の記録を書き出さないという意味ではない。**
    tasks.md タスク 2.5 が「⚠️ **形状指標の型と照合は上流から借り、書き出しだけを
    実装する**（上流は書き換え操作を公開していない）」を本 Spec 側に課しており、
    `src/chassis_mechanism/baseline.py` は**自前の**書き出しを持つ。ここで禁じて
    いるのは上流のそれへ到達することだけである
    （`test_the_unpublished_operation_scan_allows_a_local_definition` 参照）。
    """
    sources = sorted(CHASSIS_SRC_DIR.glob("*.py"))
    assert sources, "本 Spec の実装コードが1つも見つからない"

    for path in sources:
        hits = upstream_operations_reached(path.read_text(encoding="utf-8"))
        assert hits == [], f"{path.name} が上流の非公開操作へ到達している: {hits}"


@pytest.mark.parametrize(
    "source",
    [
        "import catch_mechanism as cm\nx = cm.write_baseline\n",
        "import catch_mechanism\ncatch_mechanism.verify_baseline_digest(1, 2)\n",
        "from catch_mechanism import write_baseline\n",
        "from catch_mechanism.metrics import verify_baseline_digest\n",
    ],
)
def test_the_unpublished_operation_scan_flags_a_source_that_reaches_them(
    source: str,
) -> None:
    """違反ケース: 上流経由の到達は検出される。

    ⚠️ 検出器が実際に働くことを示さないと、
    `test_this_spec_does_not_reach_the_unpublished_upstream_operations` は
    恒真になりうる。
    """
    assert upstream_operations_reached(source) != []


def test_the_unpublished_operation_scan_allows_a_local_definition() -> None:
    """誤検知の否定: 本 Spec が**自前で**同名の書き出しを持つことは違反ではない。

    tasks.md タスク 2.5 が実装するのはこの形である。上流の非公開関数を借りるので
    はなく、書き出しだけを本 Spec が持つ（design.md `#### Baseline`）。
    """
    local = (
        "from pathlib import Path\n"
        "def write_baseline(baseline: object, path: Path) -> None:\n"
        "    return None\n"
        "def verify_baseline_digest(a: object, b: str) -> str:\n"
        "    return b\n"
        "write_baseline(object(), Path('x'))\n"
        "verify_baseline_digest(object(), 'sha256:0')\n"
    )
    assert upstream_operations_reached(local) == []


def test_the_design_records_that_the_writing_side_is_this_specs_own() -> None:
    """設計が「書き出しだけ本 Spec が持つ」ことを記録している（否定の出所）。

    本ファイルの裁定は本ファイル発ではなく design.md `#### Baseline` 発である。
    設計側が方針を変えたら、本テストが先に落ちる。
    """
    text = DESIGN_PATH.read_text(encoding="utf-8")
    start = text.index("#### Baseline")
    section = text[start : text.index("\n#### ", start + 1)]

    assert "write_baseline" in section
    assert "公開していない" in section
    assert "型と照合は上流から借りる" in section


# ---------------------------------------------------------------------------
# 5. 形状ライブラリ非導入の環境で全項目が成立する（⚠️ 観測可能な完了状態）
# ---------------------------------------------------------------------------

_STUB_MESSAGE = "build123d is blocked by the chassis CAD-absence stub"
_STUB_SOURCE = f'raise ImportError({_STUB_MESSAGE!r})\n'

_PROBE_BODY = """
import importlib.util
import json
import sys

try:
    import build123d  # noqa: F401
except ImportError:
    blocked = True
else:
    blocked = False

spec = importlib.util.spec_from_file_location("chassis_upstream_contract_probe", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
# `slots=True` のデータクラスは定義時に自身のモジュールを `sys.modules` から
# 引くため、実行前に登録しておく（登録しないと `AttributeError` になる）。
sys.modules[spec.name] = module
spec.loader.exec_module(module)

print(json.dumps({
    "stub_blocked_the_shape_library": blocked,
    "report": module.borrowed_item_report(),
    "missing_borrowed_names": module.missing_borrowed_names(),
    "borrowed_names_absent_from_all": module.borrowed_names_absent_from_all(),
    "unpublished_on_the_package_root": module.unpublished_operations_on_the_package_root(),
    "shape_library_modules": sorted(
        name for name in sys.modules if name.split(".")[0] == "build123d"
    ),
    "cad_layer_modules": sorted(
        name
        for name in sys.modules
        if name in ("catch_mechanism.shapes", "catch_mechanism.export")
    ),
}))
"""


def _probe_source() -> str:
    """子プロセスへ渡すソース（本ファイル自身をモジュールとして読み込ませる）。

    ⚠️ **借用の実演を文字列へ書き写さない。** 書き写せば、CAD 非導入の経路だけが
    古びて「通っているのは古い写し」という状態になる。子プロセスは本ファイルを
    そのまま import し、`borrowed_item_report()` を呼ぶ。
    """
    return f"MODULE_PATH = {str(MODULE_PATH)!r}\n{_PROBE_BODY}"


def _run_with_cad_blocked(stub_dir: Path, code: str) -> subprocess.CompletedProcess[str]:
    """形状ライブラリを import 不能にした子プロセスで `code` を実行する。

    上流 `tests/catch_mechanism/test_catch_baseline_digest.py` と同じ技法である
    ——`PYTHONPATH` の先頭へ、`ImportError` を送出するだけの `build123d.py` を
    置いたディレクトリを挿す。⚠️ **スタブは `tmp_path` に置く**（pytest が管理
    する一時ディレクトリ。`/tmp` 直下へ置くと実行の途中で消えて遮断が黙って
    無効になる事故がある）。

    ⚠️ **遮断が効いていることを別途確かめる。** 本リポジトリの `.venv` には
    `build123d` が**導入済み**であり、遮断せずに通しても全件緑になる。
    `test_the_cad_blocking_stub_actually_blocks_the_shape_library` が、
    スタブ固有のメッセージが出ることまで見る。
    """
    (stub_dir / "build123d.py").write_text(_STUB_SOURCE, encoding="utf-8")
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{stub_dir}{os.pathsep}{existing}" if existing else str(stub_dir)
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )


def test_the_cad_blocking_stub_actually_blocks_the_shape_library(
    tmp_path: Path,
) -> None:
    """遮断スタブが効いていることを先に確かめる（後続を空振りにしないため）。

    ⚠️ `"ImportError" in stderr` だけでは「そもそも導入されていない」場合と
    区別できない。**スタブ固有のメッセージ**が出ることまで見る。
    """
    completed = _run_with_cad_blocked(tmp_path, "import build123d")

    assert completed.returncode != 0, completed.stdout
    assert "ImportError" in completed.stderr
    assert _STUB_MESSAGE in completed.stderr, (
        "遮断したつもりの実行が、スタブ以外の理由で失敗している"
    )


def test_every_borrowed_item_holds_without_the_shape_library(tmp_path: Path) -> None:
    """⚠️ **観測可能な完了状態**: 借りる9項目が CAD 非導入の環境で全件成立する。

    要件 1.3 と design.md「Integration Tests」の
    「上流の公開 API から借りている項目がすべて存在し、CAD 無しで参照できること
    （要件 1.3）」に対応する。

    子プロセスは本ファイルを import して `borrowed_item_report()` を呼ぶため、
    検査の内容は本プロセスと**同一**である。結果が一致することは、借りた9項目の
    どれ1つとして形状ライブラリを要していないことの直接の証拠になる。
    """
    completed = _run_with_cad_blocked(tmp_path, _probe_source())

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)

    # 遮断が効いた状態での結果であること。
    assert payload["stub_blocked_the_shape_library"] is True
    assert payload["shape_library_modules"] == [], (
        "形状ライブラリが子プロセスの sys.modules に現れている"
    )
    assert payload["cad_layer_modules"] == [], (
        "上流の CAD 層モジュールが読み込まれている（入口が触れている）"
    )

    # 借りる名前が1つも欠けていない。
    assert payload["missing_borrowed_names"] == []
    assert payload["borrowed_names_absent_from_all"] == []
    assert payload["unpublished_on_the_package_root"] == []

    # 9項目の実演結果が本プロセスと一致する。
    assert set(payload["report"]) == set(BORROWED_ITEMS)
    assert payload["report"] == as_json_roundtrip(borrowed_item_report())
