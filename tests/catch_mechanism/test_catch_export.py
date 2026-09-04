"""生成物の原子的な書き出し（タスク 3.4、要件 2.7, 3.3, 3.4, 3.5, 3.6）。

本ファイルが固定するのは design.md `#### Export` の Responsibilities /
Batch Contract と、tasks.md タスク 3.4 の「観測可能な完了状態」である。

1. **3形式が部品ごとに出る**こと。STEP（組立確認・図面化用）と STL・3MF
   （造形用）を、`segment_part_names` が導く**部品名ごとに1組ずつ**書き出す
   （要件 3.3 / design.md Batch Contract「`var/cad/<part_name>.step` / `.stl` /
   `.3mf`」）。⚠️ **1点を分割数だけ刷るのではない**——後付け締結座はリング全体で
   `retrofit_fastener_count` 箇所であり、出荷値（6箇所 / 5分割）では
   `rim_segment_3` だけ座を2つ持つ（tasks.md「Implementation Notes」タスク
   3.2(b)）。`test_every_segment_gets_its_own_file_set_and_the_shapes_differ` が
   「5部品ぶんのファイルが要る」側を観測する
2. **単位がミリメートルである**こと（要件 3.4）。STEP は `SI_UNIT(.MILLI.,.METRE.)`、
   3MF は `unit="millimeter"` を持つ。⚠️ **STL には単位の欄が無い**ため、
   三角形の頂点座標から外接箱を組み立て、`PartMetrics.bbox_mm`（mm）と一致する
   ことで単位を観測する。書式の文字列照合では STL の単位を検査できない
3. **書き出しが原子的である**こと（要件 3.6 / design.md「一時ディレクトリへ全
   ファイルを書き終えてから出力先へ移す。失敗時は何も残さない」）。書き出しの
   途中で失敗させたとき、出力先に**部分的なファイルが残らない**こと、および
   出力先に既にあったファイルが**書き換わらない**こと
4. **造形制約の検査を書き出しの前に通す**こと（要件 2.7）。材料が許可一覧に無い
   場合と、部品の外接箱が造形可能寸法を超える場合に、**1バイトも書かない**
5. **出力先の既定がバージョン管理外である**こと（要件 3.5）。既定は
   `var/cad/` であり、`.gitignore` が `var/` を除外している

⚠️ **出荷 `configs/catch_mechanism/dimensions.json` を読まない。** 採寸値は
タスク 5.2 で実測へ更新される予定であり、出荷ファイルの値に依存したテストは
境界の外側から落ちる（tasks.md「Implementation Notes」タスク 2.3(b) / 3.1 と
同じ理由）。本ファイルは局所のヘルパでパラメータを組み立てる。

⚠️ **本ファイルは形状ライブラリをモジュール直下で import しない。** 造形制約の
関門（上記4）は CAD 非導入の環境でも観測できなければならない（要件 5.7）。
ソリッドを実際に構築する検査だけを個別に skip する（`test_catch_shapes.py` と
同形）。

ファイル名について: `tests/` 配下に `__init__.py` が無く pytest の import-mode も
既定（prepend）のため、テストモジュール名はセッション全体でフラットである。
既存の `test_catch_*.py` に倣う（tasks.md「Implementation Notes」タスク 1.1）。
"""

from __future__ import annotations

import ast
import locale
import re
import struct
import zipfile
from pathlib import Path

import pytest

from catch_mechanism import export as export_module
from catch_mechanism.errors import CatchMechanismError, GeometryError, ParameterError
from catch_mechanism.export import (
    DEFAULT_OUTPUT_DIR,
    EXPORT_SUFFIXES,
    ExportedPart,
    export_parts,
)
from catch_mechanism.metrics import PartMetrics
from catch_mechanism.params import (
    JointPolicy,
    MechanismParams,
    ObjectSpec,
    PrintingConstraints,
    Provenance,
    RetentionParams,
    RimParams,
    TrashCanMeasurements,
)
from catch_mechanism.shapes import BuiltPart, rim_geometry, segment_part_names

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "要件 5.7 により、形状生成を除く検査はこの環境でも完了する。",
)

# ---------------------------------------------------------------------------
# パラメータ組み立ての補助（`test_catch_shapes.py` と同形）。
# ---------------------------------------------------------------------------


def _params(
    *,
    opening_inner_diameter_mm: float = 220.0,
    top_outer_diameter_mm: float = 225.0,
    rim_height_mm: float = 18.0,
    material: str = "PETG",
) -> MechanismParams:
    """受け口が成立するパラメータを局所の値から組み立てる。"""
    return MechanismParams(
        trash_can=TrashCanMeasurements(
            model_id="probe-model",
            opening_inner_diameter_mm=opening_inner_diameter_mm,
            top_outer_diameter_mm=top_outer_diameter_mm,
            bottom_outer_diameter_mm=opening_inner_diameter_mm * 0.7,
            bottom_flat_diameter_mm=opening_inner_diameter_mm * 0.6,
            height_mm=240.0,
            mass_g=228.0,
            bottom_thickness_mm=1.5,
            taper_deg=7.0,
        ),
        target_object=ObjectSpec(diameter_mm=65.0, height_mm=122.0),
        printing=PrintingConstraints(
            build_x_mm=180.0,
            build_y_mm=180.0,
            build_z_mm=180.0,
            material=material,
            material_density_g_cm3=1.27,
            segment_margin_mm=5.0,
        ),
        joint=JointPolicy(
            bolt_designation="M3",
            through_hole_diameter_mm=3.4,
            insert_outer_diameter_mm=4.6,
            insert_length_mm=5.7,
            dowel_diameter_mm=3.0,
            min_bearing_area_mm2=60.0,
        ),
        rim=RimParams(
            fit_clearance_mm=1.0,
            flange_width_mm=30.0,
            flange_slope_deg=15.0,
            wall_thickness_mm=4.0,
            height_mm=rim_height_mm,
        ),
        retention=RetentionParams(
            retrofit_fastener_count=6,
            liner_flat_min_diameter_mm=opening_inner_diameter_mm * 0.5,
        ),
        provenance={"trash_can.opening_inner_diameter_mm": Provenance.ASSUMED},
    )


def _five_segment_params() -> MechanismParams:
    """出荷相当の 5 分割（座 6 箇所 → 配分 `[1, 1, 2, 1, 1]`）。"""
    params = _params()
    assert rim_geometry(params).segment_count == 5
    return params


def _single_segment_params() -> MechanismParams:
    """分割の要らない小径の受け口（1 部品 = 3 ファイル）。

    3形式の中身そのものを見る検査は、部品数が少ないほど速い。⚠️ 分割数は
    `constraints.required_segment_count` の導出値であり、直接は指定できない。
    """
    params = _params(opening_inner_diameter_mm=95.0, top_outer_diameter_mm=100.0)
    assert rim_geometry(params).segment_count == 1
    return params


def _expected_names(params: MechanismParams) -> set[str]:
    return {
        f"{part_name}{suffix}"
        for part_name in segment_part_names(params)
        for suffix in EXPORT_SUFFIXES
    }


def _files_in(directory: Path) -> tuple[str, ...]:
    if not directory.exists():
        return ()
    return tuple(sorted(entry.name for entry in directory.iterdir()))


def _model_xml(path: Path) -> str:
    """3MF（ZIP）の中の形状記述を取り出す。"""
    with zipfile.ZipFile(path) as archive:
        return archive.read("3D/3dmodel.model").decode("utf-8")


def _without_uuids(xml: str) -> str:
    """lib3mf が実行ごとに振る UUID を伏せる（`test_re_running_...` の docstring）。"""
    return re.sub(r'UUID="[0-9a-fA-F-]{36}"', 'UUID="*"', xml)


# ---------------------------------------------------------------------------
# 出力先の既定と形式（要件 3.3, 3.5）。
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_default_output_directory_is_var_cad_and_is_not_version_controlled() -> None:
    """既定の出力先は `var/cad/` であり、`.gitignore` が `var/` を除外している。

    design.md「Export」Responsibilities「出力先の既定は `var/cad/`
    （`.gitignore` 済み）」/ 要件 3.5。
    """
    assert DEFAULT_OUTPUT_DIR == _REPO_ROOT / "var" / "cad"
    ignored = (_REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "var/" in [line.strip() for line in ignored]


def test_export_suffixes_are_the_three_documented_formats() -> None:
    """中間形式 1 種（STEP）とメッシュ形式 2 種（STL / 3MF）である（要件 3.3）。"""
    assert EXPORT_SUFFIXES == (".step", ".stl", ".3mf")


def test_export_module_does_not_import_the_shape_library_at_module_level() -> None:
    """形状ライブラリの import は関数内に限る（要件 5.7）。

    ⚠️ design.md「Allowed Dependencies」は `export` に build123d の import を
    **許す**が、許可と「モジュール読み込み時に必要にしてよい」は別である
    （tasks.md「Implementation Notes」タスク 3.2(g)）。モジュール直下へ置くと
    CAD 非導入環境で本ファイルが**収集時 ERROR** になり、造形制約の関門を
    観測できなくなる。
    """
    source = Path(export_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            module_level.append(node.module)
    assert [name for name in module_level if name.split(".")[0] == "build123d"] == []


# ---------------------------------------------------------------------------
# 造形制約の関門（要件 2.7）。⚠️ CAD 非導入環境でも走る。
# ---------------------------------------------------------------------------


def test_material_outside_the_allowed_list_blocks_every_write(tmp_path: Path) -> None:
    """許可一覧に無い材料では 1 バイトも書かない（要件 2.5, 2.7）。

    ⚠️ `PrintingConstraints.__post_init__` は `pickle` 復元や
    `object.__setattr__` を迂回できる（`constraints.check_material` の
    docstring）。`check_material` は「生成物を書き出す直前に呼ぶ関門」として
    設計されており、その関門が**書き出し側にある**ことを本件が固定する。
    """
    params = _params()
    object.__setattr__(params.printing, "material", "ABS")
    destination = tmp_path / "cad"

    with pytest.raises(ParameterError) as excinfo:
        export_parts(params, output_dir=destination)

    assert "ABS" in str(excinfo.value)
    assert _files_in(destination) == ()


def test_a_part_that_exceeds_the_build_volume_blocks_every_write(tmp_path: Path) -> None:
    """造形可能寸法を超える部品は書き出さない（要件 2.3, 2.4, 2.7）。

    ⚠️ 分割は円周方向にのみ効くため、Z 軸の超過は分割数では解決しない
    （tasks.md「Implementation Notes」タスク 3.1(a)）。この検査は build123d の
    import より前に発火するため、CAD 非導入環境でも観測できる。
    """
    params = _params(rim_height_mm=500.0)
    destination = tmp_path / "cad"

    with pytest.raises(GeometryError) as excinfo:
        export_parts(params, output_dir=destination)

    assert "z" in str(excinfo.value)
    assert not destination.exists()


def test_the_measured_bounding_box_is_checked_in_the_exporter_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き出し側が**実測の**外接箱を造形可能寸法と突き合わせる（要件 2.4, 2.7）。

    ⚠️ `shapes.build_segments` が通す `check_envelope` は `sector_envelope` の
    **解析上界**（半径方向 `D/2`）に対するものであり、実際に構築されたソリッドを
    見ていない。書き出しの直前に**測った箱**を検査することで、形状側が上界を
    通ったまま実体が収まらない事故を出力の手前で止める。違反の軸と超過量を
    メッセージへ載せる（tasks.md「Implementation Notes」タスク 1.2(b)）。
    """
    params = _params()
    oversized = BuiltPart(
        name="rim_segment_1",
        solid=object(),
        metrics=PartMetrics(
            part_name="rim_segment_1",
            volume_mm3=1000.0,
            bbox_mm=(200.0, 50.0, 20.0),
            solid_count=1,
        ),
    )
    monkeypatch.setattr(export_module, "build_parts", lambda _: (oversized,))
    destination = tmp_path / "cad"

    with pytest.raises(GeometryError) as excinfo:
        export_parts(params, output_dir=destination)

    message = str(excinfo.value)
    assert "rim_segment_1" in message
    assert "x" in message
    assert "20.0" in message  # 200.0 - 180.0 の超過量
    assert not destination.exists()


def test_the_gate_failures_are_catch_mechanism_errors() -> None:
    """関門の失敗は基底例外としても捕捉できる（要件 1.3, 1.4 / `errors.py`）。"""
    assert issubclass(GeometryError, CatchMechanismError)
    assert issubclass(ParameterError, CatchMechanismError)


# ---------------------------------------------------------------------------
# 3形式の書き出し（要件 3.3, 3.4）。
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def five_segment_export(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, tuple[ExportedPart, ...]]:
    """出荷相当（5 分割）の書き出しを 1 回だけ行い、複数の検査で共有する。"""
    if _build123d is None:  # pragma: no cover - `cad` extra 非導入の環境
        pytest.skip("形状ライブラリ（build123d / `cad` extra）が未導入である。")
    destination = tmp_path_factory.mktemp("five") / "cad"
    exported = export_parts(_five_segment_params(), output_dir=destination)
    return destination, exported


@requires_cad
def test_every_segment_gets_its_own_file_set_and_the_shapes_differ(
    five_segment_export: tuple[Path, tuple[ExportedPart, ...]],
) -> None:
    """5 部品 × 3 形式 = 15 ファイルが出る（要件 3.3 / タスク 3.2(b)）。

    ⚠️ **1 点を 5 回刷るのではない。** 後付け締結座はリング全体で 6 箇所であり、
    5 分割では配分が `[1, 1, 2, 1, 1]` となって `rim_segment_3` だけ座を 2 つ
    持つ。ファイルの中身が部品ごとに異なることを、同形式のバイト列の相違で
    観測する（体積差は約 184mm^3 の**実形状の差**である）。
    """
    destination, exported = five_segment_export
    params = _five_segment_params()

    assert set(_files_in(destination)) == _expected_names(params)
    assert len(_files_in(destination)) == 15
    assert tuple(part.name for part in exported) == segment_part_names(params)
    for part in exported:
        for path in part.paths:
            assert path.parent == destination
            assert path.stat().st_size > 0

    seat_two = (destination / "rim_segment_3.step").read_bytes()
    seat_one = (destination / "rim_segment_1.step").read_bytes()
    assert seat_two != seat_one


@requires_cad
def test_exported_parts_carry_the_metrics_of_the_written_solid(
    five_segment_export: tuple[Path, tuple[ExportedPart, ...]],
) -> None:
    """戻り値は「ファイル」と「指標」の双方を運ぶ（design.md Batch Contract）。"""
    _destination, exported = five_segment_export
    for part in exported:
        assert isinstance(part.metrics, PartMetrics)
        assert part.metrics.part_name == part.name
        assert part.metrics.volume_mm3 > 0.0
        assert len(part.paths) == len(EXPORT_SUFFIXES)


@requires_cad
def test_step_and_3mf_declare_millimetres(tmp_path: Path) -> None:
    """STEP / 3MF が単位をミリメートルとして**宣言**する（要件 3.4）。

    ⚠️ **STEP 側の `SI_UNIT(.MILLI.,.METRE.)` は単位の観測にならない。**
    `export_step` は `Unit` の全6値（MC / MM / CM / M / IN / FT）で必ずこの行を
    書き、`unit` 引数は宣言ではなく**座標の倍率**として効く（10mm の箱は
    `Unit.IN` で 254mm、`Unit.M` で 10000mm の座標になる）。したがって本件の
    STEP 側 assert は `unit` に対して恒真であり、**書式が壊れていないこと**しか
    主張しない。要件 3.4 を STEP について守るのは
    `test_step_vertex_coordinates_are_in_millimetres` である。

    3MF 側の `unit="millimeter"` は `Mesher(unit=...)` に正しく感応するため、
    こちらは単位の観測として有効である。
    """
    destination = tmp_path / "cad"
    export_parts(_single_segment_params(), output_dir=destination)

    step_text = (destination / "rim_segment_1.step").read_text(errors="replace")
    assert "SI_UNIT(.MILLI.,.METRE.)" in step_text
    assert 'unit="millimeter"' in _model_xml(destination / "rim_segment_1.3mf")


#: STEP の `CARTESIAN_POINT('', (x, y, z))` から座標を抜く。
#: ⚠️ OCCT は約72桁で行を折り返すため、照合の前に改行を畳む必要がある。
_CARTESIAN_POINT = re.compile(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(([^)]*)\)\s*\)")


def _step_coordinate_extents(path: Path) -> tuple[float, float, float]:
    """STEP テキストの直交座標点から、軸ごとの（最大 − 最小）を組み立てる。

    ⚠️ **これは部品の外接箱ではない。** 円・円筒の**中心点**もこの実体で書かれ、
    リング中心（原点付近）にある中心点は部品自身の X の範囲の外に落ちる。実測で
    X は `PartMetrics.bbox_mm` より 64〜81mm 大きい。⚠️ 一方 **Y と Z は
    `PartMetrics.bbox_mm` と 1e-6 未満で一致する**ため、単位（＝座標の倍率）の
    観測にはこの2軸を使う。`_stl_bounding_box` と同じ手法である。
    """
    text = path.read_text(errors="replace").replace("\r", "").replace("\n", "")
    axes: tuple[list[float], list[float], list[float]] = ([], [], [])
    for match in _CARTESIAN_POINT.finditer(text):
        fields = [field.strip() for field in match.group(1).split(",")]
        if len(fields) != 3:
            continue
        try:
            values = [float(field) for field in fields]
        except ValueError:  # pragma: no cover - 座標でない CARTESIAN_POINT は無い
            continue
        for axis, value in enumerate(values):
            axes[axis].append(value)
    assert all(axes), f"{path.name} に直交座標点が見つからない"
    extents = tuple(max(values) - min(values) for values in axes)
    return (extents[0], extents[1], extents[2])


@requires_cad
def test_step_vertex_coordinates_are_in_millimetres(
    five_segment_export: tuple[Path, tuple[ExportedPart, ...]],
) -> None:
    """STEP の座標が mm である（要件 3.4）。

    ⚠️ **`export_step(unit=...)` は座標の倍率であり、書式中の `SI_UNIT` 行は
    どの単位でも `.MILLI.,.METRE.` のままである**（`test_step_and_3mf_declare_millimetres`
    の docstring）。したがって単位は**座標の数値としてしか観測できない**——
    `Unit.M` なら 1000 倍、`Unit.IN` なら 25.4 倍、`Unit.CM` なら 10 倍に外れる。

    Y と Z のみを見る。X には円・円筒の中心点（リング中心 ≒ 原点）が混じり、
    部品の外接箱より 64〜81mm 広くなるためである（`_step_coordinate_extents`）。
    Y・Z は実測で `PartMetrics.bbox_mm` と 1e-6 未満で一致する。許容差 1e-4mm は
    STEP の書式精度の揺れを吸収するためで、⚠️ 最小の単位変更（`Unit.CM` の
    10 倍）に対してもなお5桁の余裕がある。
    """
    destination, exported = five_segment_export
    for part in exported:
        extents = _step_coordinate_extents(destination / f"{part.name}.step")
        for axis in (1, 2):  # Y, Z
            assert extents[axis] == pytest.approx(part.metrics.bbox_mm[axis], abs=1e-4)


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
    five_segment_export: tuple[Path, tuple[ExportedPart, ...]],
) -> None:
    """STL の頂点座標が mm である（要件 3.4）。

    メートルやインチで書かれていれば辺長は桁で外れる。三角形分割の誤差
    （既定の線形許容差 0.001mm）を吸収する 0.05mm の絶対許容差で照合する。
    """
    destination, exported = five_segment_export
    for part in exported:
        measured = _stl_bounding_box(destination / f"{part.name}.stl")
        for actual, expected in zip(measured, part.metrics.bbox_mm, strict=True):
            assert actual == pytest.approx(expected, abs=0.05)


@requires_cad
def test_re_running_the_export_reproduces_the_same_content(tmp_path: Path) -> None:
    """同一入力の再実行は同じ内容を出す（design.md Batch Contract の Idempotency）。

    ⚠️ **3MF はバイト単位では一致しない。** lib3mf が書き出しのたびに乱数の
    UUID（ラッパの object / component / build item）を振るためであり、
    build123d の公開 API から止められるのは形状 1 つぶんの `uuid_value` だけで
    ある。したがって 3MF は **UUID を伏せた上で**一致を主張する。STEP は表題部の
    日時を固定してあり（`export_step(..., timestamp=...)`）、STL とともにバイト
    単位で一致する。
    """
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    export_parts(_single_segment_params(), output_dir=first_dir)
    export_parts(_single_segment_params(), output_dir=second_dir)

    for suffix in (".step", ".stl"):
        name = f"rim_segment_1{suffix}"
        assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()

    first_mesh = _without_uuids(_model_xml(first_dir / "rim_segment_1.3mf"))
    second_mesh = _without_uuids(_model_xml(second_dir / "rim_segment_1.3mf"))
    assert first_mesh == second_mesh


# ---------------------------------------------------------------------------
# 原子性（要件 3.6 / design.md「失敗時は何も残さない」）。
# ---------------------------------------------------------------------------


@requires_cad
def test_a_failure_midway_through_the_write_leaves_nothing_in_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き出しの途中で失敗させると、出力先に部分的なファイルが残らない（要件 3.6）。

    5 部品ぶんのうち 2 回目の STL で失敗させる。⚠️ このとき 1 部品目の 3 形式は
    **既に書き終わっている**——一時ディレクトリの中に、である。出力先には
    1 バイトも現れてはならない。
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
        export_parts(_five_segment_params(), output_dir=destination)

    # 要件 3.6「失敗した部品名と理由を示す」。`errors.GeometryError` の docstring は
    # 「書き出しの失敗（`OSError` を包む）」を本例外の用途として挙げている。
    assert "rim_segment_2" in str(excinfo.value)
    assert len(calls) == 2
    assert _files_in(destination) == ()


@requires_cad
def test_a_failure_does_not_change_files_that_were_already_in_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失敗時は出力先を変更しない（design.md Batch Contract の recovery）。

    先行する実行の生成物が出力先にある状態で失敗させ、**古いファイルがそのまま
    残る**ことを固定する。⚠️ 出力先が既に在る場合、差し替えは「ファイル単位の
    `os.replace` の列」であって単一の原子操作ではない。失敗時に出力先を元の
    状態へ戻すのは補償処理であり、本件がその補償を観測する。
    """
    destination = tmp_path / "cad"
    export_parts(_five_segment_params(), output_dir=destination)
    before = {path.name: path.read_bytes() for path in destination.iterdir()}
    assert len(before) == 15

    calls: list[str] = []
    original = export_module._write_3mf

    def failing(solid: object, part_name: str, path: Path) -> None:
        calls.append(part_name)
        if len(calls) == 3:
            raise OSError("書き出しの途中で失敗させる（テスト）")
        original(solid, part_name, path)

    monkeypatch.setattr(export_module, "_write_3mf", failing)

    with pytest.raises(GeometryError) as excinfo:
        export_parts(_five_segment_params(), output_dir=destination)

    assert "rim_segment_3" in str(excinfo.value)
    after = {path.name: path.read_bytes() for path in destination.iterdir()}
    assert after == before


@requires_cad
def test_a_failure_while_moving_into_the_destination_restores_the_previous_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**移し替えの途中**で失敗しても、出力先は元の状態へ戻る（要件 3.6）。

    ⚠️ 一時ディレクトリへ書き終えてから移す設計でも、移し替えそのものが途中で
    失敗すると出力先に新旧が混ざり得る。ファイル単位の `os.replace` を 8 回目で
    失敗させ、補償処理が出力先を元へ戻すことを固定する。
    """
    destination = tmp_path / "cad"
    export_parts(_five_segment_params(), output_dir=destination)
    before = {path.name: path.read_bytes() for path in destination.iterdir()}

    calls: list[int] = []
    original = export_module._replace

    def failing(source: Path, target: Path) -> None:
        calls.append(1)
        if len(calls) == 8:
            raise OSError("移し替えの途中で失敗させる（テスト）")
        original(source, target)

    monkeypatch.setattr(export_module, "_replace", failing)

    with pytest.raises(GeometryError):
        export_parts(_five_segment_params(), output_dir=destination)

    after = {path.name: path.read_bytes() for path in destination.iterdir()}
    assert after == before


@requires_cad
def test_files_that_the_exporter_does_not_own_survive_a_successful_run(
    tmp_path: Path,
) -> None:
    """出力先を丸ごと置き換えない。

    ⚠️ 出力先ディレクトリごと `rename` で差し替える実装は原子性としては強いが、
    `--output-dir` に指定された場所の**無関係なファイルを消す**。書き出しは
    自分が作るファイルだけを差し替える。
    """
    destination = tmp_path / "cad"
    destination.mkdir(parents=True)
    keep = destination / "notes.txt"
    keep.write_text("手で置いたメモ", encoding="utf-8")

    export_parts(_single_segment_params(), output_dir=destination)

    assert keep.read_text(encoding="utf-8") == "手で置いたメモ"
    assert set(_files_in(destination)) == {
        "notes.txt",
        *_expected_names(_single_segment_params()),
    }


@requires_cad
def test_no_staging_directory_survives_a_successful_or_a_failed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ディレクトリは出力先の**隣**に作られ、実行後に残らない。

    ⚠️ システムの一時領域は出力先と別のファイルシステムであり得る。実際にこの
    リポジトリでは `/tmp`（dev 75）と `/mnt/c`（dev 71）が別であり、そこを跨ぐ
    `os.rename` は `EXDEV`（`Invalid cross-device link`）で失敗する。出力先の親へ
    一時ディレクトリを作ることが「原子的な `rename` が使える」ための前提である。
    """
    destination = tmp_path / "nested" / "cad"
    export_parts(_single_segment_params(), output_dir=destination)
    assert _files_in(destination.parent) == ("cad",)

    def failing(solid: object, path: Path) -> None:
        raise OSError("書き出しの途中で失敗させる（テスト）")

    monkeypatch.setattr(export_module, "_write_step", failing)
    with pytest.raises(GeometryError):
        export_parts(_single_segment_params(), output_dir=destination)
    assert _files_in(destination.parent) == ("cad",)


@requires_cad
def test_the_export_leaves_the_process_locale_unchanged(tmp_path: Path) -> None:
    """書き出しがプロセス全体のロケールを変えない。

    ⚠️ `Mesher.write` が呼ぶ lib3mf はロケールを `C` に設定したまま戻さない。
    ライブラリ関数として許されない副作用であり、⚠️ **実際にこの退行を踏んだ**
    ——上流 `tests/sensing_foundation/test_sensing_cli.py::test_module_entrypoint_smoke`
    と `tests/trajectory_sim/test_trajectory_sim_cli.py::test_python_dash_m_trajectory_sim_end_to_end`
    が `subprocess.run(..., text=True)` の復号で `UnicodeDecodeError` を起こし、
    全スイート実行時にのみ落ちた（当該ファイル単独では落ちない）。
    `export.py` の `_write_3mf` が退避と復元を行う。

    ⚠️ **現在のロケールをそのまま基準にしてはならない。** module スコープの
    `five_segment_export` fixture や先行する書き出し系の検査が既にロケールを
    `C` へ漏らしていると、`before == after == 'C'` となって**恒真**になる
    ——退避・復元を実装から外しても落ちなくなる。基準を採る**前に**既知の
    UTF-8 ロケールへ固定し、`finally` で元へ戻す。
    """
    original = locale.setlocale(locale.LC_ALL)
    try:
        try:
            locale.setlocale(locale.LC_ALL, "C.UTF-8")
        except locale.Error:  # pragma: no cover - C.UTF-8 を持たない環境
            pytest.skip("C.UTF-8 ロケールが利用できない環境である。")

        before = locale.setlocale(locale.LC_ALL)
        before_encoding = locale.getpreferredencoding(False)
        # ⚠️ 前提の明示: 基準が非 UTF-8 だと lib3mf の漏れを観測できない。
        assert before_encoding.upper().replace("-", "") == "UTF8"

        export_parts(_single_segment_params(), output_dir=tmp_path / "cad")

        assert locale.setlocale(locale.LC_ALL) == before
        assert locale.getpreferredencoding(False) == before_encoding
    finally:
        locale.setlocale(locale.LC_ALL, original)


@requires_cad
def test_the_default_output_directory_is_used_when_none_is_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`output_dir` を省略すると既定の出力先へ書く（要件 3.5）。"""
    destination = tmp_path / "var" / "cad"
    monkeypatch.setattr(export_module, "DEFAULT_OUTPUT_DIR", destination)

    export_parts(_single_segment_params())

    assert set(_files_in(destination)) == _expected_names(_single_segment_params())
