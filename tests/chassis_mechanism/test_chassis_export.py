"""生成物の原子的な書き出し（タスク 3.7 / 要件 1.11）。

本ファイルが固定するのは design.md `#### Export` の Responsibilities /
Service Interface / Batch Contract と、tasks.md タスク 3.7 の「観測可能な完了
状態」——⚠️ **途中で失敗させた場合に出力先が呼び出し前の状態へ戻り、成功時は
全部品の全形式が揃うこと**——である。

1. **中間形式とメッシュ形式の双方が部品ごとに出る**こと。STEP（組立確認・
   図面化用の中間形式）と STL・3MF（造形用のメッシュ形式）を、
   `shapes.build_parts` が返した部品ごとに1組ずつ書き出す
2. **単位がミリメートルである**こと。3MF は `unit="millimeter"` を宣言する。
   ⚠️ **STL には単位の欄が無く、STEP の `SI_UNIT` 行は単位に感応しない**ため、
   どちらも三角形・直交座標点から外接箱を組み立て、`PartMetrics.bbox_mm`（mm）と
   一致することでしか単位を観測できない
3. **書き出しが原子的である**こと。⚠️ 本ファイルの中心である。失敗を**注入**して
   観測する——書き出しの途中・移し替えの途中・そして⚠️ **例外ではなくプロセス
   そのものが死ぬ場合**の3つの窓それぞれについて、出力先がどうなるかを固定する
4. **関門を迂回した生成物が出ない**こと（タスク 3.6 の申し送り）。書き出しは
   `build_parts` の戻り値だけを消費し、⚠️ **部品名の表からファイル名を組み立てて
   書く経路を持たない**——ソリッドを伴わない名前からは1バイトも出ない
5. **出力先がバージョン管理の対象外である**こと。既定は `var/cad/chassis/` で
   あり、`.gitignore` の `var/` が効く

⚠️ **本ファイルは形状ライブラリをモジュール直下で import しない。** 上記4と5は
CAD 非導入の環境でも観測できなければならない（design.md「Allowed Dependencies」）。
ソリッドを実際に構築する検査だけを個別に skip する（`test_chassis_shapes.py` と同形）。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名がセッション
全体でフラットであるため、`test_chassis_` 接頭辞を付ける
（design.md「Directory Structure」）。
"""

from __future__ import annotations

import ast
import locale
import os
import re
import struct
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

import pytest
from catch_mechanism import PartMetrics

from chassis_mechanism import export as export_module
from chassis_mechanism.config import load_params
from chassis_mechanism.errors import GeometryError
from chassis_mechanism.export import (
    DEFAULT_OUTPUT_DIR,
    EXPORT_SUFFIXES,
    INTERMEDIATE_SUFFIXES,
    MESH_SUFFIXES,
    ExportedPart,
    export_parts,
)
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import BuiltPart

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "src" / "chassis_mechanism"
EXPORT_SOURCE = PACKAGE_ROOT / "export.py"

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（design.md「Allowed Dependencies」）。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "design.md「Allowed Dependencies」により、形状生成を除く検査は"
    "この環境でも完了する。",
)


@pytest.fixture(scope="module")
def built() -> tuple[BuiltPart, ...]:
    """出荷の寸法から構築した全部品（⚠️ **1回だけ作る**）。

    ⚠️ **`build_parts` の戻り値をそのまま使う。** 書き出しが消費してよいのは
    これだけであり（タスク 3.6 の申し送り）、テストの側で `BuiltPart` を捏造して
    渡せば⚠️ **関門を通っていない生成物**を書き出すことになる。捏造した記録を
    渡すのは、それが**拒まれる**ことを見る検査だけである。
    """
    from chassis_mechanism.shapes import build_parts

    params = load_params()
    return build_parts(params, derive_layout(params))


@pytest.fixture(scope="module")
def few(built: tuple[BuiltPart, ...]) -> tuple[BuiltPart, ...]:
    """原子性の検査に使う3点（⚠️ 全点でなくてよい。実物であることが要件）。"""
    return built[:3]


def _files_in(directory: Path) -> tuple[str, ...]:
    """ディレクトリ直下の名前（存在しない場合は空）。"""
    if not directory.exists():
        return ()
    return tuple(sorted(entry.name for entry in directory.iterdir()))


def _snapshot(directory: Path) -> dict[str, bytes]:
    """出力先の中身をバイト列ごと控える（⚠️ 名前だけでは書き換えを見逃す）。"""
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


def _expected_names(parts: tuple[BuiltPart, ...]) -> set[str]:
    return {f"{part.name}{suffix}" for part in parts for suffix in EXPORT_SUFFIXES}


def _rollback_dirs(parent: Path) -> list[Path]:
    """出力先の隣に残っている退避先（⚠️ **残ってよいのは二重障害のときだけ**）。

    退避先は⚠️ **出力先が既に在るすべての確定で作られる**。片付け忘れれば、
    再書き出しのたびに `.chassis-mechanism-rollback-*` が出力先の隣へ積もる。
    """
    return sorted(
        entry
        for entry in parent.iterdir()
        if entry.name.startswith(export_module._ROLLBACK_PREFIX)
    )


# ---------------------------------------------------------------------------
# 1. 出力先と形式（⚠️ CAD 非導入の環境でも走る）
# ---------------------------------------------------------------------------


def test_the_default_output_directory_is_var_cad_chassis_and_is_git_ignored() -> None:
    """既定の出力先は `var/cad/chassis/` であり、版管理の対象外である。

    design.md `#### Export` Responsibilities「出力先は `var/cad/chassis/`
    （`.gitignore` 済み）。⚠️ **生成物をコミットしない**」/ タスク 3.7
    「出力先をバージョン管理の対象外とし、同じ入力からいつでも再生成できる状態に
    保つ」。

    ⚠️ **`.gitignore` の行を読むだけにしない。** 除外されているかを決めるのは
    git であって行の見た目ではない（`var/` の後ろに再包含の行が足されれば行の
    存在は嘘になる）。`git check-ignore` に判定させる。
    """
    assert DEFAULT_OUTPUT_DIR == REPO_ROOT / "var" / "cad" / "chassis"
    probe = DEFAULT_OUTPUT_DIR / f"hub_plate{EXPORT_SUFFIXES[0]}"
    result = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-v", "--no-index", str(probe)],  # noqa: S607
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=60.0,
        check=False,
    )
    assert result.returncode == 0, (
        "既定の出力先が版管理の対象外になっていない: "
        f"{result.stdout}{result.stderr}"
    )
    assert "var/" in result.stdout


def test_both_kinds_of_format_are_written_and_the_intermediate_comes_first() -> None:
    """中間形式とメッシュ形式の**双方**を書く（タスク 3.7 の1行目）。

    ⚠️ **どちらか一方では足りない。** 中間形式（STEP）は組立確認と図面化のため、
    メッシュ形式（STL / 3MF）は造形のためであり、用途が違う。両者が空でないこと
    と、互いに素であることを型の上で固定する。
    """
    assert INTERMEDIATE_SUFFIXES == (".step",)
    assert MESH_SUFFIXES == (".stl", ".3mf")
    assert EXPORT_SUFFIXES == INTERMEDIATE_SUFFIXES + MESH_SUFFIXES
    assert set(INTERMEDIATE_SUFFIXES).isdisjoint(MESH_SUFFIXES)


def test_export_module_does_not_import_the_shape_library_at_module_level() -> None:
    """形状ライブラリの import は関数内に限る。

    ⚠️ design.md「Allowed Dependencies」は `export` に build123d の import を
    **許す**が、許可と「モジュール読み込み時に必要にしてよい」は別である
    （`shapes` のモジュール docstring）。モジュール直下へ置くと CAD 非導入環境で
    本ファイルが**収集時 ERROR** になり、出力先と前提条件の検査を観測できなくなる。
    """
    tree = ast.parse(EXPORT_SOURCE.read_text(encoding="utf-8"))
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            module_level.append(node.module)
    assert [name for name in module_level if name.split(".")[0] == "build123d"] == []


# ---------------------------------------------------------------------------
# 2. 関門を迂回した生成物が出ない（タスク 3.6 の申し送り）
#
# ⚠️ 書き出しが「名前」から生成物を作れてしまうと、関門を1度も通っていない
# ファイルが出力先に並ぶ。書き出しが消費してよいのは `build_parts` の戻り値
# （＝関門を通ったソリッド）だけである。
# ---------------------------------------------------------------------------


def _functions_referencing(source: str, wanted: str) -> set[str]:
    """`wanted` という名前を参照している関数名の集合。"""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and child.id == wanted:
                found.add(node.name)
    return found


def _built_part_constructions(source: str) -> list[int]:
    """`BuiltPart(...)` を構築している行番号。"""
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BuiltPart"
    ]


def test_the_part_name_table_is_never_used_to_decide_what_to_write() -> None:
    """⚠️ **部品名の表から書き出す名前を組み立てる経路が無い。**

    `export` が `shapes.PART_NAMES` を参照してよいのは、⚠️ **自分が所有する
    ファイル名の集合を判定する**ため（前回の生成物の掃除）だけである。書き出す
    名前は渡された `BuiltPart.name` からしか作らない——名前から作れてしまえば、
    ソリッドを1つも構築せず、したがって関門を1度も通していない生成物が出る。

    ⚠️ 本件は静的な固定である。実際に「渡した部品のぶんしか出ない」ことは
    `test_only_the_parts_that_were_handed_over_are_written` が実物で観測する。
    """
    source = EXPORT_SOURCE.read_text(encoding="utf-8")
    assert _functions_referencing(source, "PART_NAMES") == {"_owned_file_names"}


def test_only_the_shape_module_constructs_built_parts() -> None:
    """⚠️ **`BuiltPart` を作るのは `shapes` だけである。**

    関門を通っていない生成物が出ないことの根拠は次の鎖である。

    1. 公開の `build_*` は**すべて**関門を先頭で通る
       （`test_chassis_shapes.py::test_every_public_builder_passes_through_the_gate`）
    2. `BuiltPart` を構築するのは `shapes` だけである（本件）
    3. `export` は渡された `BuiltPart` からしかファイル名を作らない
       （`test_the_part_name_table_is_never_used_to_decide_what_to_write`）

    ⚠️ 2 が破れると、中核層や `export` が自前で `BuiltPart` を捏造でき、鎖が
    そこで切れる。
    """
    offenders: list[str] = []
    for source_path in sorted(PACKAGE_ROOT.glob("*.py")):
        if source_path.name == "shapes.py":
            continue
        source = source_path.read_text(encoding="utf-8")
        offenders.extend(
            f"{source_path.name} (line {lineno})"
            for lineno in _built_part_constructions(source)
        )
    assert offenders == [], f"`shapes` の外で BuiltPart を構築している: {offenders}"
    # ⚠️ 走査そのものが空振りでないこと——`shapes` は実際に構築している。
    shapes_source = (PACKAGE_ROOT / "shapes.py").read_text(encoding="utf-8")
    assert len(_built_part_constructions(shapes_source)) >= 5


def test_the_two_static_pins_detect_their_own_counter_examples() -> None:
    """⚠️ 上の2件の静的な固定が**空振りではない**ことの反例。

    実際のソースに違反が無いことを述べるだけの検査は、走査そのものが壊れても
    緑のまま通る。名前を表から組み立てる架空のソースと、`BuiltPart` を捏造する
    架空のソースの双方が、同じ走査で**検出される**ことを示す。
    """
    crafted = (
        "def _write_everything(directory):\n"
        "    for name in PART_NAMES:\n"
        "        (directory / (name + '.stl')).write_bytes(b'')\n"
    )
    assert _functions_referencing(crafted, "PART_NAMES") == {"_write_everything"}
    assert _functions_referencing("def quiet():\n    return 1\n", "PART_NAMES") == set()

    forged = "def make():\n    return BuiltPart('x', None, None)\n"
    assert _built_part_constructions(forged) == [2]
    assert _built_part_constructions("def make():\n    return 1\n") == []


# ---------------------------------------------------------------------------
# 3. 前提条件（⚠️ CAD 非導入の環境でも走る。1バイトも書かずに拒む）
# ---------------------------------------------------------------------------


def _hollow_record(name: str = "hub_plate") -> BuiltPart:
    """名前と指標だけを持つ記録（＝関門を1度も通っていない何か）。"""
    return BuiltPart(
        name=name,
        solid=None,
        metrics=PartMetrics(
            part_name=name,
            volume_mm3=1.0,
            bbox_mm=(1.0, 1.0, 1.0),
            solid_count=1,
        ),
    )


def test_an_empty_part_tuple_is_refused_without_touching_the_destination(
    tmp_path: Path,
) -> None:
    """部品が空の呼び出しを拒む（design.md `#### Export` Preconditions）。

    ⚠️ **空を「成功。0件書いた」にしない。** 出力先が空のまま成功が返れば、
    呼び出し側は生成物が揃ったと読む。
    """
    destination = tmp_path / "cad"
    with pytest.raises(GeometryError) as excinfo:
        export_parts((), destination)
    assert "部品" in str(excinfo.value)
    assert not destination.exists()


def test_a_record_without_a_solid_is_refused(tmp_path: Path) -> None:
    """⚠️ **ソリッドを伴わない記録からは1バイトも出さない。**

    名前と指標だけを持つ記録を渡しても、出力先には何も現れない。
    """
    destination = tmp_path / "cad"
    with pytest.raises(GeometryError) as excinfo:
        export_parts((_hollow_record(),), destination)
    assert "hub_plate" in str(excinfo.value)
    assert not destination.exists()


def test_two_parts_with_the_same_name_are_refused(tmp_path: Path) -> None:
    """⚠️ **同名の部品を黙って上書きしない。**

    同じ名前が2つあると後から書いた方だけが残り、⚠️ **点数が足りないことに
    誰も気づかない**（生成物の点数は造形の点数である）。
    """
    destination = tmp_path / "cad"
    metrics = PartMetrics(
        part_name="hub_plate", volume_mm3=1.0, bbox_mm=(1.0, 1.0, 1.0), solid_count=1
    )
    twin = BuiltPart(name="hub_plate", solid=object(), metrics=metrics)
    with pytest.raises(GeometryError) as excinfo:
        export_parts((twin, twin), destination)
    assert "hub_plate" in str(excinfo.value)
    assert not destination.exists()


# ---------------------------------------------------------------------------
# 4. 成功時は全部品の全形式が揃う（タスク 3.7 の観測可能な完了状態・後半）
# ---------------------------------------------------------------------------


@requires_cad
def test_every_built_part_gets_every_format(
    built: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """⚠️ **全部品の全形式が揃う。** 1点でも1形式でも欠けたら失敗である。"""
    destination = tmp_path / "cad"
    exported = export_parts(built, destination)

    assert [record.name for record in exported] == [part.name for part in built]
    assert set(_files_in(destination)) == _expected_names(built)
    assert len(_files_in(destination)) == len(built) * len(EXPORT_SUFFIXES)
    for path in destination.iterdir():
        assert path.stat().st_size > 0, path.name


@requires_cad
def test_the_returned_records_name_the_files_that_exist(
    built: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """戻り値の `file_names` が実在するファイルを指す（Service Interface）。

    ⚠️ **呼び出し側がファイル名の規則を組み立て直さない**ための戻り値である。
    規則の正は `export` の中に1箇所しかない。
    """
    destination = tmp_path / "cad"
    exported = export_parts(built, destination)

    for record in exported:
        assert isinstance(record, ExportedPart)
        assert len(record.file_names) == len(EXPORT_SUFFIXES)
        for file_name in record.file_names:
            assert (destination / file_name).is_file(), file_name
        # ⚠️ 中間形式とメッシュ形式の双方が1点ごとに揃う。
        suffixes = {Path(file_name).suffix for file_name in record.file_names}
        assert suffixes & set(INTERMEDIATE_SUFFIXES)
        assert suffixes & set(MESH_SUFFIXES)


@requires_cad
def test_only_the_parts_that_were_handed_over_are_written(
    few: tuple[BuiltPart, ...], built: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """⚠️ **渡された部品のぶんしか出ない**（関門を迂回した生成物が出ない）。

    部品名の表（`PART_NAMES` / `part_names`）から書き出す実装であれば、渡して
    いない部品のファイルまで並ぶ——それらは⚠️ **ソリッドを1つも構築しておらず、
    したがって関門を1度も通っていない**。3点だけ渡して、出るのが3点ぶんだけで
    あることを実物で観測する。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)

    assert set(_files_in(destination)) == _expected_names(few)
    absent = {part.name for part in built} - {part.name for part in few}
    assert absent, "⚠️ 部分集合になっていない（この検査は何も主張していない）"
    for name in absent:
        for suffix in EXPORT_SUFFIXES:
            assert not (destination / f"{name}{suffix}").exists()


# ---------------------------------------------------------------------------
# 5. 単位はミリメートル
# ---------------------------------------------------------------------------


def _model_xml(path: Path) -> str:
    """3MF（zip）の中の `3D/3dmodel.model` を読む。"""
    with zipfile.ZipFile(path) as archive:
        return archive.read("3D/3dmodel.model").decode("utf-8")


@requires_cad
def test_step_and_3mf_declare_millimetres(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """STEP / 3MF が単位をミリメートルとして**宣言**する。

    ⚠️ **STEP 側の `SI_UNIT(.MILLI.,.METRE.)` は単位の観測にならない。**
    `export_step` は `Unit` のどの値でも必ずこの行を書き、`unit` 引数は宣言では
    なく**座標の倍率**として効く。したがって本件の STEP 側 assert は `unit` に
    対して恒真であり、**書式が壊れていないこと**しか主張しない。STEP の単位を
    守るのは `test_step_vertex_coordinates_are_in_millimetres` である。

    3MF 側の `unit="millimeter"` は `Mesher(unit=...)` に感応するため、こちらは
    単位の観測として有効である。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    name = few[0].name

    step_text = (destination / f"{name}.step").read_text(errors="replace")
    assert "SI_UNIT(.MILLI.,.METRE.)" in step_text
    assert 'unit="millimeter"' in _model_xml(destination / f"{name}.3mf")


@requires_cad
def test_step_geometry_reads_back_in_millimetres(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """STEP を**読み直した形状**が mm で測れる。

    ⚠️ **`export_step(unit=...)` は座標の倍率であり、書式中の `SI_UNIT` 行は
    どの単位でも `.MILLI.,.METRE.` のままである**（`test_step_and_3mf_declare_millimetres`
    の docstring）。したがって単位は**座標の数値としてしか観測できない**。

    ⚠️ **テキストから座標を拾わない。** `CARTESIAN_POINT` には円・円筒の**中心
    点**も含まれ、部品の外へ落ちる点が混じる（実測で hub_plate の z は外接箱
    15.0mm に対し 75.0mm になる）。⚠️ **書き出したファイルをそのまま読み直し、
    そこから外接箱を測る**——読み手が受け取る形状そのものを見る唯一の方法で
    ある。`Unit.M` で書けば読み直した箱は 1/1000、`Unit.IN` なら 1/25.4、
    最小の単位変更 `Unit.CM` でも 1/10 になる。
    """
    import build123d

    destination = tmp_path / "cad"
    export_parts(few, destination)
    for part in few:
        reread = build123d.import_step(destination / f"{part.name}.step")
        size = reread.bounding_box().size
        measured = (float(size.X), float(size.Y), float(size.Z))
        for actual, expected in zip(measured, part.metrics.bbox_mm, strict=True):
            assert actual == pytest.approx(expected, abs=1e-6)


def _stl_bounding_box(path: Path) -> tuple[float, float, float]:
    """バイナリ STL の頂点座標から軸並行外接箱の辺長を組み立てる。

    ⚠️ **STL には単位の欄が無い。** 数値そのものを `PartMetrics.bbox_mm`（mm）と
    突き合わせることでしか単位を観測できない。
    """
    raw = path.read_bytes()
    (count,) = struct.unpack_from("<I", raw, 80)
    axes: tuple[list[float], list[float], list[float]] = ([], [], [])
    offset = 84
    for _ in range(count):
        values = struct.unpack_from("<12f", raw, offset)
        for vertex in range(3, 12, 3):
            for axis in range(3):
                axes[axis].append(values[vertex + axis])
        offset += 50
    extents = tuple(max(values) - min(values) for values in axes)
    return (extents[0], extents[1], extents[2])


@requires_cad
def test_stl_vertex_coordinates_are_in_millimetres(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """STL の頂点座標が mm である。

    メートルやインチで書かれていれば辺長は桁で外れる。三角形分割の誤差と単精度
    浮動小数を吸収する 0.05mm の絶対許容差で照合する。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    for part in few:
        measured = _stl_bounding_box(destination / f"{part.name}.stl")
        for actual, expected in zip(measured, part.metrics.bbox_mm, strict=True):
            assert actual == pytest.approx(expected, abs=0.05)


@requires_cad
def test_the_3mf_carries_the_part_name(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """3MF が**部品名を中に持つ**（`MESH_SUFFIXES` の docstring）。

    ⚠️ **「STL だけにしない」理由の半分がこれである**——STL は単位も名前も持たず、
    ファイル名を付け替えれば何の部品か分からなくなる。3MF は `partnumber` として
    形状そのものに名前を書ける（`_write_3mf` の `part_number`）。

    ⚠️ **ファイル名ではなく中身を見る。** ファイル名が正しいことは
    `test_every_built_part_gets_every_format` が既に見ており、それは
    `part_number=` を実装から外しても緑のまま通る（再実行の一致検査も、両実行から
    等しく名前が消えるだけなので通る）。名前が**中に**在ることはここでしか
    観測しない。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    for part in few:
        model_xml = _model_xml(destination / f"{part.name}.3mf")
        assert f'partnumber="{part.name}"' in model_xml, part.name


# ---------------------------------------------------------------------------
# 6. 同じ入力からいつでも再生成できる（タスク 3.7 の3行目）
# ---------------------------------------------------------------------------


_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _without_uuids(text: str) -> str:
    return _UUID.sub("<uuid>", text)


@requires_cad
def test_re_running_the_export_reproduces_the_same_content(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """同一入力の再実行が同じ内容を出す（Batch Contract の Idempotency）。

    生成物を版管理しない根拠は「同じ入力からいつでも再生成できる」ことであり、
    ⚠️ **再生成のたびに中身が変わるなら、その根拠は成り立たない**。

    ⚠️ **3MF はバイト単位では一致しない。** lib3mf が書き出しのたびに乱数の
    UUID（ラッパの object / component / build item）を振るためであり、
    build123d の公開 API から止められるのは形状1つぶんの `uuid_value` だけで
    ある。したがって 3MF は **UUID を伏せた上で**一致を主張する。

    ⚠️ **伏せる前に、伏せる当の値を観測する。** 形状の UUID は部品名から
    `uuid5(NAMESPACE_URL, "urn:chassis-mechanism:part:<部品名>")` で導いてあり
    （`_write_3mf` の `uuid_value`）、⚠️ **それが両実行で同じであること**が 3MF 側
    の再現性の中身である。`_without_uuids` はその値ごと `<uuid>` へ潰すため、
    ⚠️ **この観測が無いと `uuid_value=` を実装から外しても本検査は緑のまま通る**
    ——実装は乱数の UUID を振り、両実行の差は伏せられて見えなくなる。期待値は
    実装の定数からではなく**本検査が持つ文字列から**導き、規則そのものを固定する。

    STEP は表題部の日時を固定してあり、STL とともにバイト単位で一致する。
    """
    first = tmp_path / "first"
    second = tmp_path / "second"
    export_parts(few, first)
    export_parts(few, second)

    for part in few:
        for suffix in (".step", ".stl"):
            name = f"{part.name}{suffix}"
            assert (first / name).read_bytes() == (second / name).read_bytes(), name
        mesh = f"{part.name}.3mf"
        derived = uuid.uuid5(
            uuid.NAMESPACE_URL, f"urn:chassis-mechanism:part:{part.name}"
        )
        for directory in (first, second):
            model_xml = _model_xml(directory / mesh)
            assert f'p:UUID="{derived}"' in model_xml, (
                f"⚠️ 形状の UUID が部品名から導かれていない（{directory.name}/{mesh}）"
                f": {derived} が無い"
            )
        assert _without_uuids(_model_xml(first / mesh)) == _without_uuids(
            _model_xml(second / mesh)
        )


@requires_cad
def test_the_default_output_directory_is_used_when_none_is_given(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`directory` を省略すると既定の出力先へ書く。

    ⚠️ **本物の `var/cad/chassis/` へは書かない**（テストがリポジトリの作業ツリーを
    汚さない）。既定の定数を差し替えて経路だけを観測する。
    """
    destination = tmp_path / "var" / "cad" / "chassis"
    monkeypatch.setattr(export_module, "DEFAULT_OUTPUT_DIR", destination)

    export_parts(few)

    assert set(_files_in(destination)) == _expected_names(few)


# ---------------------------------------------------------------------------
# 7. 原子性（⚠️ 本ファイルの中心。タスク 3.7 の観測可能な完了状態・前半）
#
# ⚠️ **失敗を注入して観測する。** 正常系をいくら見ても「失敗時に部分ファイルを
# 残さない」ことは分からない。窓は3つある——書き出しの途中・移し替えの途中・
# プロセスそのものの死である。
# ---------------------------------------------------------------------------


@requires_cad
def test_a_failure_midway_through_the_write_leaves_nothing_in_the_destination(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き出しの途中で失敗させると、出力先に部分的なファイルが残らない。

    3点ぶんのうち2回目の STL で失敗させる。⚠️ このとき1点目の3形式は**既に
    書き終わっている**——一時ディレクトリの中に、である。出力先には1バイトも
    現れてはならない。
    """
    calls: list[str] = []
    original = export_module._write_stl

    def failing(solid: object, path: Path) -> None:
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("書き出しの途中で失敗させる（テスト）")
        original(solid, path)

    monkeypatch.setattr(export_module, "_write_stl", failing)
    destination = tmp_path / "cad"

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)

    assert few[1].name in str(excinfo.value)
    assert len(calls) == 2
    assert _files_in(destination) == ()


@requires_cad
def test_a_failure_does_not_change_the_output_of_a_previous_run(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **既存の生成物も壊さない**（タスク 3.7 の2行目の後半）。

    先行する実行の生成物が出力先にある状態で失敗させ、⚠️ **古いファイルが
    バイト単位でそのまま残る**ことを固定する。名前が残っているだけでは足りない
    ——途中まで書かれた中身に置き換わっていれば、造形すると壊れた部品が出る。

    ⚠️ **目印つきの中身へ置き換えてから失敗させる**（書き出しは決定的であり、
    実物のままだと「置き換わった」ことが中身に現れない。
    `test_a_failure_while_moving_into_the_destination_restores_the_previous_files`
    の docstring）。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    for path in destination.iterdir():
        path.write_bytes(f"previous:{path.name}".encode())
    before = _snapshot(destination)
    assert len(before) == len(few) * len(EXPORT_SUFFIXES)

    calls: list[str] = []
    original = export_module._write_3mf

    def failing(solid: object, part_name: str, path: Path) -> None:
        calls.append(part_name)
        if len(calls) == 3:
            raise OSError("書き出しの途中で失敗させる（テスト）")
        original(solid, part_name, path)

    monkeypatch.setattr(export_module, "_write_3mf", failing)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)

    assert few[2].name in str(excinfo.value)
    assert _snapshot(destination) == before


@requires_cad
def test_a_failure_while_moving_into_the_destination_restores_the_previous_files(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **移し替えの途中**で失敗しても、出力先は元の状態へ戻る。

    一時領域へ書き終えてから移す設計でも、⚠️ **出力先が既にある場合の移し替えは
    ファイル単位の `os.replace` の列であって単一の原子操作ではない**。列の途中で
    失敗させ、補償処理が出力先を元へ戻すことを固定する——これが本モジュールの
    保証のうち最も弱い箇所であり、だからこそ実際に踏んで確かめる。

    ⚠️ **先行する生成物を目印つきの中身へ置き換えてから失敗させる。** 実物の
    生成物のままだと、置き換わった新ファイルの中身が旧ファイルと**バイト単位で
    同じ**（書き出しは決定的である）ため、⚠️ **補償処理を実装から外しても緑の
    まま通る**——実際にこの空振りを踏んだ。目印を入れれば、戻っていないファイルは
    必ず中身の違いとして現れる。
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    for path in destination.iterdir():
        path.write_bytes(f"previous:{path.name}".encode())
    before = _snapshot(destination)

    calls: list[int] = []
    original = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 5:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original(source, target)

    monkeypatch.setattr(export_module, "_replace", failing)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)

    assert "戻した" in str(excinfo.value)
    assert len(calls) == 5, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    assert _snapshot(destination) == before
    # ⚠️ **単一障害では退避先を残さない。** 旧ファイルは出力先へ戻っており、
    # 退避先は空である——残せば失敗のたびに出力先の隣へ積もる。
    assert _rollback_dirs(destination.parent) == [], (
        "⚠️ 補償が成功したのに退避先が残っている"
    )


@requires_cad
def test_files_that_the_exporter_does_not_own_survive_a_successful_run(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """出力先を丸ごと置き換えない。

    ⚠️ 出力先ディレクトリごと `rename` で差し替える実装は原子性としては強いが、
    指定された場所の**無関係なファイルを消す**。書き出しは自分が所有する名前
    だけを触る。

    ⚠️ **所有する名前の「後ろに続きがある」名前を必ず混ぜる**（`hub_plate.stl.bak`
    など）。`_owned_file_names` の正規表現は `re.match` で照合するため、
    ⚠️ **末尾の `$` を落としても前方一致は成立し、手で置いた退避（`.bak` /
    `.orig` / `.tmp`）が黙って消える**——`notes.txt` と `hub_plate.txt` は
    **拡張子**の枝で弾かれるため、この末尾の錨を1バイトも踏まない
    （`$` を外す変異が全検査を素通りすることを実測した）。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    keep = destination / "notes.txt"
    keep.write_text("手で置いたメモ", encoding="utf-8")
    also_keep = destination / "hub_plate.txt"
    also_keep.write_text("拡張子が違う（所有していない）", encoding="utf-8")
    # ⚠️ 所有する名前＋所有する拡張子＋**さらに続き**。末尾の錨だけが弾く。
    tails = {
        "hub_plate.stl.bak": "手で取った退避（所有していない）",
        "hub_plate.step.orig": "手で取った退避（所有していない）",
        "hub_plate.3mf.tmp": "書きかけの退避（所有していない）",
        "adapter_segment_9.stl.bak": "連番つきの退避（所有していない）",
        "hub_plate.step~": "エディタの退避（所有していない）",
    }
    for name, text in tails.items():
        (destination / name).write_text(text, encoding="utf-8")

    export_parts(few, destination)

    assert keep.read_text(encoding="utf-8") == "手で置いたメモ"
    assert also_keep.exists()
    for name, text in tails.items():
        assert (destination / name).read_text(encoding="utf-8") == text, (
            f"⚠️ 所有していない名前を消した: {name}"
        )
    assert set(_files_in(destination)) == {
        "notes.txt",
        "hub_plate.txt",
        *tails,
        *_expected_names(few),
    }


def test_the_names_to_clean_up_are_listed_in_dictionary_order(tmp_path: Path) -> None:
    """掃除の対象は⚠️ **辞書順で決定的**である（`_owned_file_names` の Returns）。

    ⚠️ ディレクトリの走査順を決めるのはファイルシステムであって、作った順でも
    辞書順でもない。並べ替えを外すと、⚠️ **同じ出力先に対する掃除の順が実行環境
    ごとに変わる**——退避先へ退ける順も、失敗したときに戻す順も揺れ、再現しない
    差として現れる（並べ替えを外す変異が全検査を素通りすることを実測した）。

    ⚠️ **`keep` の除外も同時に見る。** 今回書く名前を掃除の対象へ入れてしまえば、
    書き出したそばから消し合う（掃除は移し替えより**先**に走る）。

    ⚠️ 形状ライブラリを要さない——ソリッドを1つも作らずに観測できる
    （design.md「Allowed Dependencies」）。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    # ⚠️ **辞書順とは違う順で作る**（作成順のまま返す実装を弾く）。
    owned = (
        "service_stand.stl",
        "catch_deck.3mf",
        "cable_guide.step",
        "board_deck.stl",
        "battery_tray.step",
        "adapter_segment_10.3mf",
        "adapter_segment_9.stl",
        "motor_arm.step",
    )
    for name in owned:
        (destination / name).write_bytes("前回の生成物".encode())
    (destination / "notes.txt").write_text("手で置いたメモ", encoding="utf-8")
    (destination / "hub_plate.stl.bak").write_text("手で取った退避", encoding="utf-8")
    # ⚠️ **末尾に改行を持つ名前。** POSIX ではこれも合法なファイル名である。
    # 正規表現を `$` で閉じると `$` が「末尾の改行の直前」にも合うため、
    # ⚠️ **所有していないこの名前を所有と判定して黙って消す**（`\Z` は合わない）。
    (destination / "hub_plate.stl\n").write_text("改行で終わる名前", encoding="utf-8")
    # ⚠️ **所有する名前を持つ「ディレクトリ」。** 掃除の対象は**ファイル**だけで
    # ある——`entry.is_file()` を落とすと、この名前が所有と判定されて退避先へ
    # `os.replace` で丸ごと移される。⚠️ 出力先に手で作った作業ディレクトリが
    # 名前の綴りだけで持ち去られることになり、`\Z` の欠落と同じ種類の欠陥である
    # （`entry.is_file()` を `True` にする変異が全検査を素通りすることを実測した）。
    (destination / "hub_plate.3mf").mkdir()

    cleaned = export_module._owned_file_names(destination, frozenset())
    assert "hub_plate.3mf" not in cleaned, (
        f"⚠️ 同名のディレクトリを掃除の対象にした（ファイルだけを見ること）: {cleaned}"
    )
    assert cleaned == tuple(sorted(owned))
    kept_back = export_module._owned_file_names(
        destination, frozenset({"board_deck.stl", "motor_arm.step"})
    )
    assert "hub_plate.3mf" not in kept_back, kept_back
    assert kept_back == tuple(sorted(set(owned) - {"board_deck.stl", "motor_arm.step"}))


@requires_cad
def test_stale_output_from_a_previous_run_does_not_survive(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """⚠️ **前回の生成物のうち今回作られない点は残さない。**

    分割数は導出であって設定値ではない（要件 2.1）。寸法を変えると断片の点数が
    **減る**ことがあり、そのとき前回の `adapter_segment_9.stl` が出力先に残る。
    ⚠️ 残れば、設計に無い部品がそのまま造形へ回る——「同じ入力から再生成した
    出力先」が入力を表していないことになる。

    ⚠️ **消してよいのは自分が所有する名前だけである**（`notes.txt` は残る。
    `test_files_that_the_exporter_does_not_own_survive_a_successful_run`）。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    stale_segment = destination / "adapter_segment_9.stl"
    stale_segment.write_bytes("前回の生成物".encode())
    stale_plain = destination / "battery_tray.step"
    stale_plain.write_bytes("前回の生成物".encode())
    keep = destination / "notes.txt"
    keep.write_text("手で置いたメモ", encoding="utf-8")

    export_parts(few, destination)

    assert not stale_segment.exists()
    assert not stale_plain.exists()
    assert keep.exists()
    assert set(_files_in(destination)) == {"notes.txt", *_expected_names(few)}


@requires_cad
def test_stale_output_comes_back_when_the_move_fails(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **掃除も補償の対象である。**

    前回の生成物を退けたあとで移し替えが失敗した場合、退けたファイルも元へ戻る
    ——⚠️ 戻らなければ「失敗したのに出力先が変わった」ことになる。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    stale = destination / "adapter_segment_9.stl"
    stale.write_bytes("前回の生成物".encode())
    before = _snapshot(destination)

    calls: list[int] = []
    original = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 3:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original(source, target)

    monkeypatch.setattr(export_module, "_replace", failing)

    with pytest.raises(GeometryError):
        export_parts(few, destination)

    assert len(calls) == 3, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    assert _snapshot(destination) == before
    assert stale.read_bytes() == "前回の生成物".encode()


@requires_cad
def test_a_double_fault_keeps_the_displaced_files(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **補償処理そのものが失敗しても、旧ファイルを失わせない**（二重障害）。

    移し替えの列の途中で失敗させ、⚠️ **さらに退避先から戻す `os.replace` も
    失敗させる**。⚠️ **本検査が踏むのは二重障害の一方の側だけである**——退避した
    旧ファイルを戻せず、置いた新ファイルは取り除けた側。このとき退避済みの旧
    ファイルは出力先から**欠落する**（実測でも `missing` に2点が並び `changed` は
    空である。本検査の `missing` / 中身の照合がそれを固定する）。

    ⚠️ **「二重障害では新旧が混在しない」と一般には言えない。** 新ファイルを
    取り除けない側では出力先に新ファイルが残る——旧ファイルが在った名前でも
    （`test_a_double_fault_separates_the_lost_old_files_from_the_new_ones`）、
    無かった名前でも（`test_a_double_fault_that_only_leaves_new_files_claims_no_loss`）。
    ⚠️ **結末が違えば文面も違う**ため、この3検査は別々に踏む。

    ⚠️ **欠落そのものは避けられないが、失わせることは避けられる。** 退避先を
    `finally` で消してしまえば旧ファイルは出力先からも退避先からも消え、
    ⚠️ **復旧手段が無くなる**（生成物は再生成できるとはいえ、前回の出力を
    黙って失うのはタスク 3.7 の「既存の生成物も壊さない」の正反対である）。
    二重障害のときだけ退避先を残し、例外の文面が欠落した名前とその場所を示す
    ことを固定する——⚠️ **文面のとおりに手で戻せば呼び出し前の状態に復せる。**
    """
    destination = tmp_path / "cad"
    export_parts(few, destination)
    # ⚠️ 目印つきの中身へ置き換える（書き出しは決定的であり、実物のままでは
    # 「戻っていない」ことが中身に現れない）。
    for path in destination.iterdir():
        path.write_bytes(f"previous:{path.name}".encode())
    before = _snapshot(destination)

    calls: list[int] = []
    original_replace = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 4:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original_replace(source, target)

    real_os_replace = os.replace

    def failing_restore(source: object, target: object) -> None:
        # ⚠️ 補償処理は `_replace` ではなく `os.replace` を直接呼ぶ。退避先から
        # **戻す**動きだけを失敗させ、それ以外はそのまま通す。
        if Path(str(source)).parent.name.startswith(export_module._ROLLBACK_PREFIX):
            raise OSError("戻す処理も失敗させる（テスト・二重障害）")
        real_os_replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_replace", failing)
    monkeypatch.setattr(os, "replace", failing_restore)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)
    monkeypatch.undo()

    assert len(calls) == 4, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    message = str(excinfo.value)

    # ⚠️ この側では出力先からは**消えている**（新ファイルは取り除けた）。
    missing = sorted(set(before) - set(_files_in(destination)))
    assert missing == [f"{few[0].name}.step", f"{few[0].name}.stl"], missing
    changed = [
        name
        for name, content in before.items()
        if name not in missing and (destination / name).read_bytes() != content
    ]
    assert changed == [], f"⚠️ 欠落ではなく書き換わっている: {changed}"

    # ⚠️ 文面が「欠落」と述べ、欠落した名前を挙げる。
    assert "欠落" in message, message
    for name in missing:
        assert name in message, message

    # ⚠️ 退避先が消されずに残り、その場所を文面が示す。
    rollbacks = [
        entry
        for entry in destination.parent.iterdir()
        if entry.name.startswith(export_module._ROLLBACK_PREFIX)
    ]
    assert len(rollbacks) == 1, f"⚠️ 退避先が残っていない（旧ファイルを失った）: {rollbacks}"
    rollback = rollbacks[0]
    assert str(rollback) in message, message

    # ⚠️ 旧ファイルは失われていない——文面の場所に、バイト単位でそのまま在る。
    for name in missing:
        assert (rollback / name).read_bytes() == before[name], name
        (destination / name).write_bytes((rollback / name).read_bytes())
    assert _snapshot(destination) == before


def _always_failing_unlink(self: Path, *args: object, **kwargs: object) -> None:
    """新ファイルを取り除く処理を失敗させる（テスト・二重障害の片側）。"""
    raise OSError("新ファイルを取り除く処理を失敗させる（テスト）")


@requires_cad
def test_a_double_fault_that_only_leaves_new_files_claims_no_loss(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **旧ファイルが無かった名前の二重障害は「欠落」ではない。**

    `test_a_double_fault_keeps_the_displaced_files` が踏むのは⚠️ **退避した旧
    ファイルを戻せない**側の二重障害である。もう一方の側——⚠️ **置いた新ファイル
    を取り除けない**側——は結末が正反対になる。呼び出し前に同名のファイルが
    **無かった**名前は退避されておらず、退避先にその実体は1バイトも無い。
    したがってその名前は出力先から欠落せず、⚠️ **新しいファイルがそのまま
    残置される。**

    ⚠️ **ここを「欠落」と述べると文面が嘘になる**（挙げた名前は出力先に在る）。
    ⚠️ **退避先の場所を示して「手で戻せ」と述べればもっと嘘になる**——退避先は
    空であり、戻すべき実体がどこにも無い。さらに退避先を残す条件をここへ広げると、
    中身が空のまま `.chassis-mechanism-rollback-*` が出力先の隣へ積もる。
    残置された名前を挙げ、⚠️ **退避先を通常どおり消す**ことを固定する。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)  # ⚠️ 空だが**存在する**（列の経路へ入る）

    calls: list[int] = []
    original_replace = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 3:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original_replace(source, target)

    monkeypatch.setattr(export_module, "_replace", failing)
    monkeypatch.setattr(Path, "unlink", _always_failing_unlink)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)
    monkeypatch.undo()

    assert len(calls) == 3, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    message = str(excinfo.value)
    left = [f"{few[0].name}.step", f"{few[0].name}.stl"]

    # ⚠️ 新ファイルが出力先に**在る**。欠落していない。
    assert set(_files_in(destination)) == set(left), _files_in(destination)

    # ⚠️ 文面が残置と述べ、その名前を挙げる。
    assert "残置" in message, message
    for name in left:
        assert name in message, message

    # ⚠️ **「欠落」とは述べない**（挙げた名前は出力先に在る）。
    assert "欠落" not in message, message
    # ⚠️ **手で戻せとも述べない**（退避先は空であり、戻す実体が無い）。
    assert "手で戻す" not in message, message

    # ⚠️ 退避先は通常どおり消す（空の退避先を積もらせない）。
    assert _rollback_dirs(destination.parent) == [], (
        "⚠️ 旧ファイルを1つも失っていないのに退避先が残っている"
    )


@requires_cad
def test_a_double_fault_separates_the_lost_old_files_from_the_new_ones(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **2つの結末は同じ実行の中に同居する。** 文面はそれを分けて述べる。

    出力先に旧ファイルが**在る名前**と**無い名前**を同時に作り、⚠️ **戻す処理
    （退避先からの `os.replace`）も、新ファイルを取り除く処理（`unlink`）も
    両方失敗させる。** このとき

    - 旧ファイルが在った名前: 旧ファイルへ戻っておらず、⚠️ **実体は退避先に在る**
      ——退避先を消してはならない。出力先には新しいファイルが残置されている
    - 旧ファイルが無かった名前: 新しいファイルが残置されているだけであり、
      ⚠️ **退避先に戻すべき実体は無い**

    ⚠️ **前者を「欠落」と述べれば嘘になり、後者に退避先を示せば嘘になる。**
    どちらも文面のとおりに手を動かせば元へ戻せることを、実際に戻して固定する。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    kept = f"{few[0].name}.step"  # ⚠️ 旧ファイルが**在る**名前
    fresh = f"{few[0].name}.stl"  # ⚠️ 旧ファイルが**無い**名前
    (destination / kept).write_bytes(b"previous:step")
    before = _snapshot(destination)

    calls: list[int] = []
    original_replace = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 4:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original_replace(source, target)

    real_os_replace = os.replace

    def failing_restore(source: object, target: object) -> None:
        if Path(str(source)).parent.name.startswith(export_module._ROLLBACK_PREFIX):
            raise OSError("戻す処理も失敗させる（テスト・二重障害）")
        real_os_replace(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_replace", failing)
    monkeypatch.setattr(os, "replace", failing_restore)
    monkeypatch.setattr(Path, "unlink", _always_failing_unlink)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)
    monkeypatch.undo()

    assert len(calls) == 4, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    message = str(excinfo.value)

    # ⚠️ どちらの名前も出力先に**在り**、どちらも新しい中身である。
    assert set(_files_in(destination)) == {kept, fresh}, _files_in(destination)
    assert (destination / kept).read_bytes() != before[kept]
    assert "欠落" not in message, message
    assert "残置" in message, message
    for name in (kept, fresh):
        assert name in message, message

    # ⚠️ **名前がどちらの文へ載るかまで見る。** 文面に名前が「在る」ことだけを
    # 見ると、⚠️ **同じ名前が両方の文に載る**実装が素通りする（`leftover` を
    # `remaining - unrestored` から `remaining` にする変異が全検査を素通りする
    # ことを実測した）。そのとき1つの名前について
    # 「旧ファイルは退避先に在る、消さずに手で戻せ」と
    # 「戻すべき旧ファイルは無い、取り除け」を**同時に**述べることになり、
    # ⚠️ **後者に従えば旧ファイルの唯一の実体を捨てる**。3つの集合が互いに素で
    # あること（`_restore` の Returns）は、文面の上でこう現れる。
    overwritten_part, marker, leftover_part = message.partition(
        "⚠️ 出力先には新しいファイル"
    )
    assert marker, f"⚠️ 残置の文が無い（検査が空振り）: {message}"
    assert kept in overwritten_part, message
    assert fresh not in overwritten_part, (
        f"⚠️ 旧ファイルが無かった名前を「退避先から手で戻せ」の側へ載せた: {message}"
    )
    assert fresh in leftover_part, message
    assert kept not in leftover_part, (
        f"⚠️ 旧ファイルが在った名前を「戻すべき旧ファイルは無い」の側へも載せた"
        f"——文面のとおりに取り除けば旧ファイルの唯一の実体を捨てる: {message}"
    )

    # ⚠️ 旧ファイルが在った名前についてだけ、退避先が残り文面がその場所を示す。
    rollbacks = _rollback_dirs(destination.parent)
    assert len(rollbacks) == 1, f"⚠️ 退避先が残っていない（旧ファイルを失った）: {rollbacks}"
    rollback = rollbacks[0]
    assert str(rollback) in message, message
    assert _files_in(rollback) == (kept,), _files_in(rollback)
    assert (rollback / kept).read_bytes() == before[kept]

    # ⚠️ 文面のとおりに手を動かせば呼び出し前へ戻る——在った名前は退避先から
    # 上書きし、無かった名前は取り除く。
    (destination / kept).write_bytes((rollback / kept).read_bytes())
    (destination / fresh).unlink()
    assert _snapshot(destination) == before


@requires_cad
def test_a_single_fault_into_an_empty_destination_is_not_called_a_double_fault(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **補償が成功した失敗を「二重障害」と述べない。**

    出力先が⚠️ **空だが存在する**（ファイル単位の列へ入るが、旧ファイルは1つも
    無い）状態で移し替えを失敗させる。⚠️ **補償処理には何も注入しない**——置いた
    新ファイルは素直に取り除かれ、出力先は呼び出し前の「空」へ戻る。したがって
    文面は単一障害のものでなければならない。

    ⚠️ **この検査が守っているのは `placed.append` の位置である。** `_replace` の
    **後**に置くから `placed` は「実際に置けた名前」を表す。⚠️ **前**へ動かすと、
    失敗した `_replace` の名前まで `placed` に入り、補償処理がその名前を
    `unlink` して `FileNotFoundError`（＝`OSError`）を拾い、⚠️ **出力先には
    1バイトも無いのに「新しいファイルが残置されている」と述べる**——
    `test_a_failure_while_moving_into_the_destination_restores_the_previous_files`
    は全ファイルが**既に在る**出力先しか踏まず、
    `test_stale_output_comes_back_when_the_move_fails` は文面を読まないため、
    この変異は全検査を素通りする（実測した）。

    ⚠️ **嘘の向きが重い。** 文面を信じた人は在りもしないファイルを探し、
    出力先が空であることを「取り除き損ねた残骸」と読む。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)  # ⚠️ 空だが**存在する**（列の経路へ入る）

    calls: list[int] = []
    original_replace = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original_replace(source, target)

    # ⚠️ **`os.replace` も `Path.unlink` も差し替えない。** 補償処理は最後まで
    # 成功する——単一障害である。
    monkeypatch.setattr(export_module, "_replace", failing)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)
    monkeypatch.undo()

    assert len(calls) == 2, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    message = str(excinfo.value)

    # ⚠️ 出力先は呼び出し前の「空」へ戻っている。
    assert _files_in(destination) == (), _files_in(destination)
    assert "戻した" in message, message
    assert "二重障害" not in message, (
        f"⚠️ 補償が成功したのに二重障害と述べた: {message}"
    )
    assert "残置" not in message, (
        f"⚠️ 出力先は空なのに残置されたファイルを挙げた: {message}"
    )
    # ⚠️ 何も失っていないのだから退避先は通常どおり消える。
    assert _rollback_dirs(destination.parent) == [], (
        "⚠️ 補償が成功したのに退避先が残っている"
    )


@requires_cad
def test_a_restored_old_file_is_never_reported_as_left_over(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **退避した旧ファイルを先に戻すから、新ファイルを消す必要が無い。**

    `_restore` の docstring は⚠️ **「退避した旧ファイルを先に戻す」**と宣言する
    ——`os.replace` は同名の新ファイルごと上書きするため、⚠️ **「消してから戻す」
    順は、消せたのに戻せない窓を自分で作る**。本検査はその順序を⚠️ **観測可能な
    帰結として固定する**。

    踏むのは二重障害の**4つ目の象限**である。既存の3検査は
    「戻せない＋取り除ける」（`keeps_the_displaced_files`）、
    「退避が無い＋取り除けない」（`that_only_leaves_new_files`）、
    「戻せない＋取り除けない」（`separates_the_lost_old_files`）を踏むが、
    ⚠️ **「戻せた＋取り除けない」を踏んでいない。**

    正しい順（戻す→消す）では、戻せた名前に `unlink` は**そもそも呼ばれない**
    ——旧ファイルへ戻っているものを消すはずがない。したがって `unlink` を全て
    失敗させても⚠️ **単一障害のまま**であり、出力先はバイト単位で呼び出し前へ
    戻る。順を入れ替えると（消す→戻す）、まだ戻していない新ファイルへ `unlink`
    が走って失敗し、⚠️ **その後 `os.replace` が旧ファイルを戻して出力先は完全に
    復旧しているのに、文面は「二重障害」「新しいファイルが残置されている」と
    述べる**——挙げた名前は出力先に在るが、中身は**旧ファイル**である。
    ⚠️ **文面のとおりに「取り除く」と、復旧済みの旧ファイルを捨てることになる。**
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    kept = f"{few[0].name}.step"  # ⚠️ 旧ファイルが**在る**名前
    (destination / kept).write_bytes(b"previous:step")
    before = _snapshot(destination)

    calls: list[int] = []
    original_replace = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        # 1: kept を退避先へ / 2: kept へ新ファイルを置く / 3: ここで失敗させる。
        # ⚠️ kept が「退避され、かつ新ファイルで置き換わった」後でなければ、
        # 戻す動きそのものが起きない（検査が空振りする）。
        if len(calls) == 3:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original_replace(source, target)

    # ⚠️ **`os.replace` は差し替えない**（戻す動きは成功する）。
    # ⚠️ **`Path.unlink` だけを失敗させる**——順が正しければ呼ばれもしない。
    monkeypatch.setattr(export_module, "_replace", failing)
    monkeypatch.setattr(Path, "unlink", _always_failing_unlink)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(few, destination)
    monkeypatch.undo()

    assert len(calls) == 3, "⚠️ 失敗が列の途中で起きていない（検査が空振り）"
    message = str(excinfo.value)

    # ⚠️ 出力先は**バイト単位で**呼び出し前へ戻っている。
    assert _snapshot(destination) == before, _files_in(destination)
    assert "戻した" in message, message
    assert "二重障害" not in message, (
        f"⚠️ 出力先は完全に復旧しているのに二重障害と述べた: {message}"
    )
    assert "欠落" not in message, message
    assert "残置" not in message, (
        f"⚠️ 旧ファイルへ戻した名前を「残置された新しいファイル」として挙げた"
        f"——文面のとおりに取り除けば復旧済みの旧ファイルを捨てる: {message}"
    )
    # ⚠️ 何も失っていないのだから退避先は通常どおり消える。
    assert _rollback_dirs(destination.parent) == [], (
        "⚠️ 旧ファイルを1つも失っていないのに退避先が残っている"
    )


@requires_cad
def test_a_rollback_directory_never_survives_a_run_that_lost_nothing(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **退避先を残すのは二重障害のときだけである。**

    退避先は⚠️ **出力先が既に在るすべての確定で作られる**（旧ファイルの置き場が
    要る）。片付けを外すと、⚠️ **再書き出しのたびに `.chassis-mechanism-rollback-*`
    が出力先の隣へ積もる**——`var/cad/` が実行回数ぶんのゴミで埋まる。既存の検査
    は出力先の**中**しか見ておらず、隣を見ていなかった（片付けを `pass` にする
    変異が全検査を素通りすることを実測した）。

    ⚠️ **「作られたうえで消えた」ことを見る。** 出力先の隣が空であることだけでは
    「そもそも作られていない」場合と区別できず、検査が空振りする。
    """
    destination = tmp_path / "cad"
    created: list[Path] = []
    real_mkdtemp = export_module.tempfile.mkdtemp

    def watching_mkdtemp(*args: object, **kwargs: object) -> str:
        made = real_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
        prefix = kwargs.get("prefix")
        if isinstance(prefix, str) and prefix.startswith(export_module._ROLLBACK_PREFIX):
            created.append(Path(made))
        return made

    monkeypatch.setattr(export_module.tempfile, "mkdtemp", watching_mkdtemp)

    # 1回目は出力先が無く、`rename` 1回で確定する——退避先は要らない。
    export_parts(few, destination)
    assert created == [], "⚠️ `rename` の経路で退避先を作っている"
    assert _rollback_dirs(destination.parent) == []

    # ⚠️ 2回目は出力先が在る。退避先が**作られ**、成功したので**消える**。
    export_parts(few, destination)
    assert created, "⚠️ 退避先が作られていない（検査が空振り）"
    assert [path for path in created if path.exists()] == [], (
        "⚠️ 成功した再書き出しが退避先を残した（再実行のたびに積もる）"
    )
    assert _rollback_dirs(destination.parent) == [], (
        "⚠️ 出力先の隣に退避先が残っている"
    )
    assert set(_files_in(destination)) == _expected_names(few)


@requires_cad
def test_a_missing_destination_is_created_by_exactly_one_rename(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **出力先が無い場合は `os.rename` 1回で確定する**（唯一の単一原子操作）。

    design.md「提供する原子性」とモジュール docstring の表が、この経路を
    ⚠️ **「唯一の単一原子操作」として名指しで宣言している**。経路を丸ごと外して
    ファイル単位の列へ落としても、⚠️ **出力先の中身も権限も同じになるため他の
    検査は1つも落ちない**（`if not destination.exists():` を `if False:` にする
    変異が全検査を素通りすることを実測した）。宣言を守るのは⚠️ **分岐そのものを
    観測する**この検査だけである。

    ⚠️ **`rename` の回数と、出力先が一時ディレクトリ「そのもの」であること
    （inode の同一性）の両方を見る。** 回数だけでは別の場所を rename しても通り、
    inode だけでは複数回の rename を見逃す。
    """
    destination = tmp_path / "cad"
    assert not destination.exists(), "⚠️ `rename` の経路を通っていない（検査が空振り）"

    staging: list[Path] = []
    staging_inode: list[int] = []
    original_write = export_module._write_step

    def watching(solid: object, path: Path) -> None:
        if not staging:
            staging.append(path.parent)
            staging_inode.append(path.parent.stat().st_ino)
        original_write(solid, path)

    renames: list[tuple[str, str]] = []
    real_rename = os.rename

    def counting(source: object, target: object) -> None:
        renames.append((str(source), str(target)))
        real_rename(source, target)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_write_step", watching)
    monkeypatch.setattr(os, "rename", counting)

    export_parts(few, destination)
    monkeypatch.undo()

    assert staging, "⚠️ 書き出しが1度も呼ばれていない"
    assert renames == [(str(staging[0]), str(destination))], renames
    assert destination.stat().st_ino == staging_inode[0], (
        "⚠️ 出力先が一時ディレクトリそのものではない"
        "（`rename` ではなく作り直しで用意された）"
    )
    assert set(_files_in(destination)) == _expected_names(few)


@requires_cad
def test_a_destination_created_by_a_concurrent_run_falls_back_to_the_file_sequence(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **`rename` が競り負けても、ファイル単位の列へ落ちて完了する。**

    出力先が無いことを確かめてから `os.rename` を呼ぶまでには隙間があり、
    ⚠️ **その間に並行する実行が出力先を作れば `rename` は失敗する**（空でない
    ディレクトリの上へは移せない）。ここで送出してしまえば、⚠️ **同時に走った
    だけで書き出しが落ちる**——一時ディレクトリの中身は全部揃っているのに、で
    ある。`os.rename` の `except OSError` はこの窓を列へ落とすためだけに在り、
    ⚠️ **正常系にも失敗注入にも現れない**（この `except` を `raise` にする変異が
    全検査を素通りすることを実測した）。

    ⚠️ **並行する実行が置いたものを消さないことも同時に見る。** 列へ落ちた側は
    自分が所有する名前しか触らない。
    """
    destination = tmp_path / "cad"
    real_rename = os.rename

    def racing(source: object, target: object) -> None:
        # ⚠️ 判定の直後に並行する実行が出力先を作った状況を、その場で作る。
        raced = Path(str(target))
        raced.mkdir(parents=True)
        (raced / "notes.txt").write_text("並行する実行が置いた", encoding="utf-8")
        raise OSError("出力先が既に在る（テスト・並行する実行）")

    monkeypatch.setattr(os, "rename", racing)

    export_parts(few, destination)
    monkeypatch.undo()

    assert set(_files_in(destination)) == {"notes.txt", *_expected_names(few)}
    assert (destination / "notes.txt").read_text(encoding="utf-8") == (
        "並行する実行が置いた"
    )
    assert real_rename is os.rename, "⚠️ 差し替えが戻っていない"
    assert _rollback_dirs(destination.parent) == []


@requires_cad
def test_the_staging_directory_sits_next_to_the_destination_and_never_survives(
    few: tuple[BuiltPart, ...], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ディレクトリは出力先の**親**に作られ、実行後に残らない。

    ⚠️ システムの一時領域は出力先と別のファイルシステムであり得る。実際にこの
    リポジトリでは `/tmp` と `/mnt/c` が別デバイスであり、そこを跨ぐ
    `os.rename` / `os.replace` は `EXDEV` で失敗する。⚠️ 出力先の親へ作ることが
    「原子的な `rename` / `replace` が使える」ことの前提である。
    """
    destination = tmp_path / "nested" / "cad"
    seen: list[Path] = []
    original = export_module._write_step

    def watching(solid: object, path: Path) -> None:
        seen.append(path.parent)
        original(solid, path)

    monkeypatch.setattr(export_module, "_write_step", watching)
    export_parts(few, destination)

    assert seen, "⚠️ 書き出しが1度も呼ばれていない"
    staging = seen[0]
    assert staging.parent == destination.parent
    assert staging != destination
    assert staging.name.startswith(export_module._STAGING_PREFIX)
    assert _files_in(destination.parent) == ("cad",)

    monkeypatch.setattr(export_module, "_write_step", watching)

    def failing(solid: object, path: Path) -> None:
        raise OSError("書き出しの途中で失敗させる（テスト）")

    monkeypatch.setattr(export_module, "_write_stl", failing)
    with pytest.raises(GeometryError):
        export_parts(few, destination)
    assert _files_in(destination.parent) == ("cad",)


@requires_cad
def test_a_destination_created_by_the_move_is_readable_by_others(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """出力先が**他者から読める**権限で作られる（`_OUTPUT_DIR_MODE`）。

    ⚠️ **出力先が存在しない場合、一時ディレクトリ自身が `rename` されて出力先に
    なる。** `tempfile.mkdtemp` は 0o700 で作るため、何もしなければ生成物の
    置き場が⚠️ **作成者以外には開けないディレクトリ**になる——スライサを別の
    ユーザで動かす・コンテナへマウントするといった当たり前の使い方が、原因の
    見えない `Permission denied` で止まる。

    ⚠️ **この経路は正常系のどの検査にも現れない**（作った当人は読めるため）。
    実際、`os.chmod` を実装から外しても他の検査は1つも落ちない——実測で確認した。
    """
    destination = tmp_path / "cad"
    assert not destination.exists(), "⚠️ `rename` の経路を通っていない（検査が空振り）"
    export_parts(few, destination)

    mode = destination.stat().st_mode & 0o777
    assert mode & 0o055 == 0o055, f"出力先が他者から読めない: {mode:o}"


_DYING_PROBE = """
import os
import sys
from pathlib import Path

from chassis_mechanism import export as export_module
from chassis_mechanism.config import load_params
from chassis_mechanism.layout import derive_layout
from chassis_mechanism.shapes import build_parts

destination = Path(sys.argv[1])
params = load_params()
parts = build_parts(params, derive_layout(params))[:3]

calls = []
original = export_module._write_stl


def dying(solid, path):
    calls.append(path.name)
    if len(calls) == 2:
        # ⚠️ **例外ではない。** finally も atexit も走らないままプロセスが消える
        # ——SIGKILL・電源断と区別がつかない終わり方である。
        os._exit(137)
    original(solid, path)


export_module._write_stl = dying
export_module.export_parts(parts, destination)
"""
"""書き出しの途中でプロセスごと死ぬ小片（原子性の3つ目の窓）。"""


@requires_cad
def test_a_dying_process_cannot_corrupt_the_destination(tmp_path: Path) -> None:
    """⚠️ **プロセスが死んでも出力先は壊れない**（例外を送出しない失敗）。

    `finally` に頼った後始末は、⚠️ **プロセスが死ぬ場合には一切走らない**。
    本モジュールが実際に提供する保証は次のとおりであり、これ以上は主張しない。

    - **出力先**: 書き出しの途中で死んでも1バイトも変わらない。ファイルは出力先の
      外（一時ディレクトリ）で作られ、出力先へは `rename` / `replace` でしか
      現れないためである
    - **一時ディレクトリ**: ⚠️ **残る。** 後始末は `finally` にあり、死んだ
      プロセスはそこへ到達しない。残骸は出力先の**隣**に、既知の接頭辞つきで
      残る——⚠️ 出力先の中には入らないため、生成物の一覧を汚さない
    - **次回の実行**: 残骸があっても成功する（残骸を入力として読まない）

    ⚠️ **移し替えの列の途中で死んだ場合は新旧が混ざり得る。** それは補償処理を
    走らせる主体が居ないためであり、本件は「書き出しの途中」の窓を固定する。
    その旨はモジュール docstring が述べている。
    """
    destination = tmp_path / "cad"
    from chassis_mechanism.shapes import build_parts

    params = load_params()
    parts = build_parts(params, derive_layout(params))[:3]
    export_parts(parts, destination)
    before = _snapshot(destination)

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _DYING_PROBE, str(destination)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=600.0,
        check=False,
    )

    assert result.returncode == 137, (
        f"⚠️ プロセスが死んでいない（検査が空振り）: {result.stdout}{result.stderr}"
    )
    # 出力先は1バイトも変わっていない。
    assert _snapshot(destination) == before
    # ⚠️ 残骸は出力先の**隣**にある。中には入らない。
    leftovers = [
        name
        for name in _files_in(destination.parent)
        if name.startswith(export_module._STAGING_PREFIX)
    ]
    assert leftovers, "⚠️ 一時ディレクトリが出力先の隣に作られていない"
    # 残骸があっても次の実行は成功する（残骸を入力として読む経路が無い）。
    export_parts(parts, destination)
    after = _snapshot(destination)
    assert set(after) == set(before)
    # ⚠️ 3MF は lib3mf の乱数 UUID でバイト列が動くため、決定的な2形式で照合する
    # （`test_re_running_the_export_reproduces_the_same_content`）。
    for name, content in before.items():
        if Path(name).suffix != ".3mf":
            assert after[name] == content, name


@requires_cad
def test_the_export_leaves_the_process_locale_unchanged(
    few: tuple[BuiltPart, ...], tmp_path: Path
) -> None:
    """書き出しがプロセス全体のロケールを変えない。

    ⚠️ `Mesher.write` が呼ぶ lib3mf はロケールを `C` に設定したまま戻さない。
    ライブラリ関数として許されない副作用であり、⚠️ **上流で実際にこの退行を
    踏んでいる**——`subprocess.run(..., text=True)` の復号が `UnicodeDecodeError`
    を起こし、全スイート実行時にのみ落ちた。本ファイルにも
    `test_a_dying_process_cannot_corrupt_the_destination` という
    `subprocess.run(..., encoding="utf-8")` の検査がある。

    ⚠️ **現在のロケールをそのまま基準にしてはならない。** 先行する書き出し系の
    検査が既にロケールを `C` へ漏らしていると `before == after == 'C'` となって
    **恒真**になる。基準を採る**前に**既知の UTF-8 ロケールへ固定する。
    """
    original = locale.setlocale(locale.LC_ALL)
    try:
        try:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except locale.Error:  # pragma: no cover - C.UTF-8 を持たない環境
            pytest.skip("C.UTF-8 ロケールが利用できない環境である。")

        before = locale.setlocale(locale.LC_ALL)
        before_encoding = locale.getpreferredencoding(False)
        assert before_encoding.upper().replace("-", "") == "UTF8"

        export_parts(few, tmp_path / "cad")

        assert locale.setlocale(locale.LC_ALL) == before
        assert locale.getpreferredencoding(False) == before_encoding
    finally:
        locale.setlocale(locale.LC_ALL, original)
