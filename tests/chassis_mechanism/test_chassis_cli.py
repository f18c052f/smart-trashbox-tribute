"""コマンド入口の検証（design.md `#### Cli` / 要件 1.11, 2.3, 4.4, 9.8, 10.6）。

tasks.md タスク 4.1 の観測可能な完了状態——「形状ライブラリ非導入の環境で幾何の
導出と接合部の導出と識別子照合が完走し、形状生成だけが専用の終了コードで失敗し、
**組立前でも総合検査が正常終了する**」——を、次の6系統で固定する。

1. **終了コードの写像**（`exit_code_for`）: 本 Spec の6系統と**上流の5系統**が
   それぞれ design.md「Error Categories and Responses」の値へ写ること。
   ⚠️ とくに `GeometryError` は**綴りが同じで終了コードが違う**（本 Spec 1 /
   上流 2）ため、取り違えを専用の検査で塞ぐ
2. **`params` → `layout` の対**: 幾何を要する全経路が
   `load_params` → `derive_layout` の順で組み立てること。⚠️ 古い `layout` を
   新しい `params` に対して検査する経路を作らない（tasks.md 3.6 の申し送り）
3. **実効転がり半径の配線**: 観測を `ObservedRollingRadius`（値＋出所）として
   渡し、⚠️ **出所の畳み込みから公称値の寄与が外れる**こと
4. **サブコマンドの振る舞い**: `main()` を in-process で呼ぶ
5. **実プロセスでの通し**: 形状ライブラリを遮断した `PYTHONPATH` の下で
   `python -m chassis_mechanism` を起動し、**本物の非導入環境**で成り立つこと
6. **書き出しの失敗が届く型**: `export` の `RuntimeError` 包みが
   `GeometryError` のまま届き、終了コード 1 へ写ること（tasks.md 3.7 の申し送り）

⚠️ **本ファイルは `configs/chassis_mechanism/geometry-baseline.json` の有無にも
内容にも依存しない。** 既定の記録を作るのはタスク 4.2 であり、ここで出荷ファイルへ
ピン留めすると 4.2 が自力で緑に戻せなくなる（上流 `test_catch_cli.py` と同じ規律）。
記録が要る検査は `--baseline` で `tmp_path` の記録を指す。

⚠️ **出荷の設定ファイルへ書き戻さない。** 記録を書き出すサブコマンド
（`layout` / `joints` / `build --update-baseline`）は必ず `tmp_path` を指す。

⚠️ サブプロセスの復号は `encoding="utf-8"` を明示する。`text=True` はロケール
依存であり、lib3mf がプロセスのロケールを `C` へ落とす既知の副作用に巻き込まれうる。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest
from catch_mechanism import (
    CadUnavailableError as UpstreamCadUnavailableError,
)
from catch_mechanism import (
    CatchMechanismError as UpstreamCatchMechanismError,
)
from catch_mechanism import (
    ConsistencyError as UpstreamConsistencyError,
)
from catch_mechanism import (
    GeometryBaseline,
    PartMetrics,
    Provenance,
    load_baseline,
)
from catch_mechanism import (
    GeometryError as UpstreamGeometryError,
)
from catch_mechanism import (
    ParameterError as UpstreamParameterError,
)
from catch_mechanism import (
    SelectionError as UpstreamSelectionError,
)

from chassis_mechanism import assembly as assembly_module
from chassis_mechanism import cli as cli_module
from chassis_mechanism import export as export_module
from chassis_mechanism import layout as layout_module
from chassis_mechanism import shapes as shapes_module
from chassis_mechanism.assembly import (
    DEFAULT_MEASUREMENTS_PATH,
    REPRESENTATIVE_DIAMETER_PATH,
    load_assembly_record,
)
from chassis_mechanism.baseline import DEFAULT_BASELINE_PATH, dump_baseline
from chassis_mechanism.clearance import CLEARANCE_ITEM_NAMES
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    ResolvedParams,
    load_params,
    parameters_digest,
)
from chassis_mechanism.joints import DEFAULT_JOINT_SCHEDULE_PATH
from chassis_mechanism.errors import (
    CadUnavailableError,
    ChassisMechanismError,
    ClearanceError,
    ConsistencyError,
    GeometryError,
    MeasurementError,
    ParameterError,
)
from chassis_mechanism.layout import (
    DEFAULT_LAYOUT_PATH,
    ChassisLayout,
    ObservedRollingRadius,
    derive_layout,
    load_compression_mm,
)
from chassis_mechanism.export import ExportedPart
from chassis_mechanism.shapes import BuiltPart

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_SOURCE = (REPO_ROOT / "src" / "chassis_mechanism" / "cli.py").read_text(
    encoding="utf-8"
)

#: シミュレータの駆動系設定における実効ホイール径のキー（要件 10.6-10.8）。
SIMULATOR_WHEEL_KEY = "wheel_diameter_mm"


# ---------------------------------------------------------------------------
# 形状ライブラリの有無
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "本 Spec の中核はこの環境でも完走する（design.md「Allowed Dependencies」）。",
)


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _params() -> ResolvedParams:
    """出荷の寸法設定ファイルを読む（`cli` が既定で読むものと同一）。"""
    return load_params()


def _fake_metrics(name: str, volume_mm3: float = 1000.0) -> PartMetrics:
    """形状ライブラリを要さない指標（素の数値だけを持つ）。"""
    return PartMetrics(
        part_name=name,
        volume_mm3=volume_mm3,
        bbox_mm=(10.0, 20.0, 30.0),
        solid_count=1,
    )


def _fake_parts(
    names: tuple[str, ...] = ("hub_plate", "battery_tray"),
    volume_mm3: float = 1000.0,
) -> tuple[BuiltPart, ...]:
    """`shapes.build_parts` の差し替え用。`solid` は使われないため素のオブジェクト。"""
    return tuple(
        BuiltPart(name=name, solid=object(), metrics=_fake_metrics(name, volume_mm3))
        for name in names
    )


def _fake_export(
    parts: tuple[BuiltPart, ...], directory: Path | None = None
) -> tuple[ExportedPart, ...]:
    """`export.export_parts` の差し替え。⚠️ **出力先へ触れない。**

    `--update-baseline` の分岐だけを見たい検査は形状ライブラリを要さないが、
    実物の書き出しは `BuiltPart.solid` を必要とする。ファイル名の規則は
    `export._part_file_names` の所有であり、ここでは**名前が並ぶこと**だけを
    真似る（規則を書き写すと正が2箇所になる）。
    """
    return tuple(
        ExportedPart(name=part.name, file_names=(f"{part.name}.step",))
        for part in parts
    )


def _write_baseline_file(
    path: Path,
    parts: tuple[BuiltPart, ...],
    *,
    digest: str | None = None,
) -> Path:
    """`parts` の指標を持つ記録を `path` へ書く（既定の識別子は現在の寸法のもの）。"""
    dump_baseline(
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest=(
                digest if digest is not None else parameters_digest(_params().chassis)
            ),
            volume_rel_tolerance=1e-6,
            bbox_abs_tolerance_mm=1e-3,
            generator_version="test-fixture",
            parts={part.name: part.metrics for part in parts},
        ),
        path,
    )
    return path


def _measurements_with_representative(
    path: Path, *, diameter_mm: float, provenance: Provenance
) -> Path:
    """代表値が埋まった観測記録を `path` へ書く（3輪とも同じ径＝平均と一致）。"""
    document = json.loads(DEFAULT_MEASUREMENTS_PATH.read_text(encoding="utf-8"))
    for wheel in document["wheels"]:
        wheel["effective_rolling_diameter_mm"] = diameter_mm
        wheel["method"] = "static_loaded"
        wheel["limitation_note"] = "転動を伴わないため、周方向のたわみは捉えられない。"
    document["representative_wheel_diameter_mm"] = diameter_mm
    for index in range(3):
        document["provenance"][
            f"wheels.{index}.effective_rolling_diameter_mm"
        ] = provenance.value
    document["provenance"][REPRESENTATIVE_DIAMETER_PATH] = provenance.value
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _dimensions_with_provenance(
    path: Path, *, measured_except: tuple[str, ...] = ()
) -> Path:
    """出所を全件 `measured` にした寸法設定を書く（`measured_except` だけ仮値）。"""
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    document["provenance"] = {
        key: (
            Provenance.ASSUMED.value
            if key in measured_except
            else Provenance.MEASURED.value
        )
        for key in document["provenance"]
    }
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _nocad_stub(tmp_path: Path) -> Path:
    """`import build123d` が `ImportError` になるスタブ置き場を作って返す。"""
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "build123d.py").write_text(
        'raise ImportError("No module named \'build123d\' (test stub)")\n',
        encoding="utf-8",
    )
    return stub_dir


def _blocked_env(stub_dir: Path) -> dict[str, str]:
    """`stub_dir` を先頭に足した `PYTHONPATH` を持つ環境変数の写しを返す。"""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")
    return env


def _assert_stub_blocks(stub_dir: Path) -> None:
    """⚠️ **スタブ自身が効いていることを先に確かめる。**

    スタブが無効なら「形状ライブラリが無い環境」を名乗るだけの検査になり、
    実際には導入済みの環境で緑になってしまう。
    """
    completed = subprocess.run(
        [sys.executable, "-c", "import build123d"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=180.0,
        check=False,
    )
    assert completed.returncode != 0, (
        f"{stub_dir} のスタブが効いていない（build123d が import できてしまった）。"
    )
    assert "test stub" in (completed.stderr or ""), completed.stderr


def _run_module(
    args: list[str],
    *,
    stub_dir: Path | None = None,
    timeout: float = 300.0,
) -> subprocess.CompletedProcess[str]:
    """`python -m chassis_mechanism <args>` を実プロセスとして起動する。"""
    env = os.environ.copy() if stub_dir is None else _blocked_env(stub_dir)
    return subprocess.run(
        [sys.executable, "-m", "chassis_mechanism", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. 終了コードの写像（design.md「Cli」/「Error Categories and Responses」）
# ---------------------------------------------------------------------------


def test_exit_codes_have_the_documented_values() -> None:
    """design.md「Cli」の「0 正常 / 1 検査の不一致・違反 / 2 入力の誤り / 3 CAD 不在」。"""
    assert cli_module.EXIT_OK == 0
    assert cli_module.EXIT_MISMATCH == 1
    assert cli_module.EXIT_USAGE == 2
    assert cli_module.EXIT_CAD_UNAVAILABLE == 3


def test_the_four_exit_codes_are_distinct_and_named() -> None:
    """4つの終了コードは**互いに異なり、名前を持つ**（tasks.md 4.1）。"""
    names = ("EXIT_OK", "EXIT_MISMATCH", "EXIT_USAGE", "EXIT_CAD_UNAVAILABLE")
    values = [getattr(cli_module, name) for name in names]
    assert len(set(values)) == len(values), f"終了コードが重複している: {values}"
    assert set(names) <= set(cli_module.__all__)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ParameterError("項目名が不正"), 2),
        (GeometryError("造形制約に違反"), 1),
        (ClearanceError("床との隙間が不足"), 1),
        (MeasurementError("観測が足りない"), 1),
        (ConsistencyError("記録と現在が食い違う"), 1),
        (CadUnavailableError("形状ライブラリが無い"), 3),
    ],
)
def test_each_local_error_family_maps_to_its_own_exit_code(
    error: ChassisMechanismError, expected: int
) -> None:
    """本 Spec の6系統が design.md「Error Categories and Responses」の値へ写る。"""
    assert cli_module.exit_code_for(error) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (UpstreamParameterError("上流の項目名が不正"), 2),
        (UpstreamSelectionError("上流の選定入力が不正"), 2),
        (UpstreamGeometryError("上流の造形制約に違反"), 2),
        (UpstreamConsistencyError("上流の記録と食い違う"), 1),
        (UpstreamCadUnavailableError("上流の形状ライブラリが無い"), 3),
        (UpstreamCatchMechanismError("上流の基底"), 2),
    ],
)
def test_each_upstream_error_family_maps_to_its_own_exit_code(
    error: UpstreamCatchMechanismError, expected: int
) -> None:
    """⚠️ **上流の例外階層も終了コード表に含む**（tasks.md 4.1 / design.md「Cli」）。

    上流の失敗を包み直さないため、上流の型のまま終了コードへ写せなければならない。
    """
    assert cli_module.exit_code_for(error) == expected


def test_the_two_geometry_errors_map_to_different_exit_codes() -> None:
    """⚠️ **綴りが同じで終了コードが違う**（tasks.md「Implementation Notes」1.2）。

    本 Spec の `GeometryError` は「形状・分割・干渉」で 1、上流の `GeometryError`
    は本 Spec から見れば「上流の設定・呼び出しの誤り」で 2 である。表を作る際に
    ⚠️ **上流例外を先に捕捉する順序で書くと取り違える**。
    """
    assert cli_module.exit_code_for(GeometryError("本 Spec")) == cli_module.EXIT_MISMATCH
    assert (
        cli_module.exit_code_for(UpstreamGeometryError("上流")) == cli_module.EXIT_USAGE
    )
    assert not issubclass(GeometryError, UpstreamGeometryError)
    assert not issubclass(UpstreamGeometryError, GeometryError)


def test_local_and_upstream_hierarchies_are_disjoint() -> None:
    """2つの階層に継承関係が無い（`except` の順序で終了コードが変わらない）。"""
    local = (
        ParameterError,
        GeometryError,
        ClearanceError,
        MeasurementError,
        ConsistencyError,
        CadUnavailableError,
    )
    upstream = (
        UpstreamParameterError,
        UpstreamSelectionError,
        UpstreamGeometryError,
        UpstreamConsistencyError,
        UpstreamCadUnavailableError,
    )
    for left in local:
        for right in local:
            if left is not right:
                assert not issubclass(left, right)
        for right in upstream:
            assert not issubclass(left, right)
            assert not issubclass(right, left)


def test_exit_code_lookup_does_not_depend_on_the_table_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⚠️ 表の並びを逆にしても写像が変わらない。

    基底（`ChassisMechanismError` / `CatchMechanismError`）が表に居るため、
    素朴な「最初に一致した族を返す」走査は並び順で答えが変わる。⚠️ **その実装
    では上流の `CadUnavailableError`（3）が基底の 2 に化ける。**
    """
    samples: tuple[BaseException, ...] = (
        ParameterError("x"),
        GeometryError("x"),
        ClearanceError("x"),
        MeasurementError("x"),
        ConsistencyError("x"),
        CadUnavailableError("x"),
        UpstreamParameterError("x"),
        UpstreamGeometryError("x"),
        UpstreamConsistencyError("x"),
        UpstreamCadUnavailableError("x"),
        UpstreamCatchMechanismError("x"),
    )
    before = [cli_module.exit_code_for(error) for error in samples]
    reversed_table: Mapping[type, int] = dict(
        reversed(list(cli_module.EXIT_CODE_BY_ERROR.items()))
    )
    monkeypatch.setattr(cli_module, "EXIT_CODE_BY_ERROR", reversed_table)
    after = [cli_module.exit_code_for(error) for error in samples]
    assert before == after


def test_exit_code_lookup_prefers_the_most_derived_family() -> None:
    """派生した例外は、その族の終了コードを継ぐ（基底へ落ちない）。"""

    class _Derived(CadUnavailableError):
        pass

    class _UpstreamDerived(UpstreamCadUnavailableError):
        pass

    assert cli_module.exit_code_for(_Derived("x")) == cli_module.EXIT_CAD_UNAVAILABLE
    assert (
        cli_module.exit_code_for(_UpstreamDerived("x"))
        == cli_module.EXIT_CAD_UNAVAILABLE
    )


def test_exit_code_table_covers_every_declared_error_family() -> None:
    """本 Spec の `errors.__all__` の全系統が表に載っている。

    ⚠️ 例外を1つ足して表に載せ忘れると、その失敗は既定（2）へ黙って落ちる。
    """
    from chassis_mechanism import errors as errors_module

    declared = {
        getattr(errors_module, name) for name in errors_module.__all__
    }
    assert declared <= set(cli_module.EXIT_CODE_BY_ERROR)


def test_exit_code_table_covers_every_upstream_error_family() -> None:
    """⚠️ **上流の `errors.__all__` も全系統が表に載っている**（design.md `#### Cli`）。

    上流の失敗を包み直さない方針を採る以上、終了コード表は上流の階層も含む。
    ⚠️ **上流が7つ目の系統を足したとき、それは表に無い例外として既定（2）へ
    黙って落ちる**——`CadUnavailableError` のような 3 に写すべき系統が増えれば、
    「導入すれば直る」ことが終了コードから消える。本 Spec 側の全系統を見る
    検査（1つ上）と対にして、⚠️ **両方の階層について取りこぼしを塞ぐ**。
    """
    from catch_mechanism import errors as upstream_errors

    declared = {
        getattr(upstream_errors, name) for name in upstream_errors.__all__
    }
    assert declared, "上流の errors.__all__ が空である（前提が崩れた）"
    missing = sorted(
        family.__name__
        for family in declared
        if family not in set(cli_module.EXIT_CODE_BY_ERROR)
    )
    assert not missing, f"上流の系統が終了コード表に無い: {missing}"


def test_bare_import_error_maps_to_the_cad_exit_code() -> None:
    """形状層から届く裸の `ImportError` は 3 である（2 にしない）。

    入力は正しく、⚠️ **導入すれば直る**ことが伝わらなければならない。
    """
    assert (
        cli_module.exit_code_for(ImportError("No module named 'build123d'"))
        == cli_module.EXIT_CAD_UNAVAILABLE
    )


def test_os_error_maps_to_the_usage_exit_code() -> None:
    """書き出し先の不備は利用者が直せる**入力の誤り**である。"""
    assert cli_module.exit_code_for(OSError("読めない")) == cli_module.EXIT_USAGE


def test_a_unicode_decode_error_is_an_explicit_entry_in_the_table() -> None:
    """UTF-8 でない設定ファイルは入力の誤り（2）であり、⚠️ **表に明示的に載る**。

    値としては既定（2）と同じであるため、⚠️ **表から落ちても振る舞いは変わらない**
    ——だからこそ表への掲載そのものを固定する。`EXIT_CODE_BY_ERROR` は `__all__` に
    載る公開契約であり、「この失敗をどう分類したか」を読む側はここを読む。
    暗黙の既定に頼ると、分類したのか取りこぼしたのかが区別できない。
    """
    assert cli_module.EXIT_CODE_BY_ERROR[UnicodeDecodeError] == cli_module.EXIT_USAGE
    error = UnicodeDecodeError("utf-8", b"\x8e", 0, 1, "invalid start byte")
    assert cli_module.exit_code_for(error) == cli_module.EXIT_USAGE
    assert not isinstance(error, OSError), (
        "UnicodeDecodeError が OSError なら本件は OSError の行で足りていた"
    )


# ---------------------------------------------------------------------------
# 2. `params` → `layout` の対（tasks.md 3.6 の申し送り）
# ---------------------------------------------------------------------------


def _functions_of(source: str) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    }


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                names.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                names.add(child.func.attr)
    return names


def test_derive_layout_is_called_from_exactly_one_place() -> None:
    """⚠️ 幾何を組み立てる場所を1つに保つ（`shapes.check_before_build` の前提）。

    tasks.md 3.6 の申し送り:「関門は渡された `layout` をそのまま信じる
    （⚠️ **`params` から引き直さない**）。古い `layout` を渡せば、隙間の高さは
    古い寸法のものになる……⚠️ **入口（4.1）が `load_params` → `derive_layout` の
    順で組み立てる**ことでのみ守られる」。
    """
    functions = _functions_of(CLI_SOURCE)
    callers = sorted(
        name for name, node in functions.items() if "derive_layout" in _called_names(node)
    )
    assert callers == [cli_module.PARAMS_AND_LAYOUT_FUNCTION], (
        f"`derive_layout` の呼び出しが {callers} に散らばっている。"
        "⚠️ 2箇所目は「古い layout を新しい params に対して検査する」経路を作る"
    )


def test_load_params_is_called_from_exactly_one_place() -> None:
    """`load_params` も同じ1箇所から呼ぶ（対で組み立てるため）。"""
    functions = _functions_of(CLI_SOURCE)
    callers = sorted(
        name for name, node in functions.items() if "load_params" in _called_names(node)
    )
    assert callers == [cli_module.PARAMS_AND_LAYOUT_FUNCTION]


@pytest.mark.parametrize(
    "handler", ["_cmd_build", "_cmd_check", "_cmd_layout", "_cmd_joints"]
)
def test_every_subcommand_builds_params_and_layout_through_that_helper(
    handler: str,
) -> None:
    """4サブコマンドのすべてが同じ組み立て手順を通る。"""
    functions = _functions_of(CLI_SOURCE)
    assert handler in functions, f"{handler} が cli.py に無い"
    assert cli_module.PARAMS_AND_LAYOUT_FUNCTION in _called_names(functions[handler])


def test_the_layout_handed_to_the_gate_comes_from_the_params_that_were_loaded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """⚠️ **実際に渡る対**を観測する（`ast` の検査だけでは形しか見ていない）。

    `load_params` が返した当の `ResolvedParams` が `derive_layout` へ渡り、
    その戻り値が関門（`check_before_build`）へ渡ることを、同一性で確かめる。
    """
    seen: dict[str, object] = {}
    real_load_params = cli_module.load_params
    real_derive_layout = cli_module.derive_layout

    def _spy_load_params(path: Path | None = None) -> ResolvedParams:
        params = real_load_params(path)
        seen["params"] = params
        return params

    def _spy_derive_layout(
        params: ResolvedParams,
        observed_rolling_radius: ObservedRollingRadius | None = None,
    ) -> ChassisLayout:
        seen["derive_input"] = params
        layout = real_derive_layout(params, observed_rolling_radius)
        seen["layout"] = layout
        return layout

    def _spy_gate(params: ResolvedParams, layout: ChassisLayout) -> None:
        seen["gate_params"] = params
        seen["gate_layout"] = layout

    monkeypatch.setattr(cli_module, "load_params", _spy_load_params)
    monkeypatch.setattr(cli_module, "derive_layout", _spy_derive_layout)
    monkeypatch.setattr(shapes_module, "check_before_build", _spy_gate)

    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    assert (
        cli_module.main(["check", "--digest-only", "--baseline", str(baseline)])
        == cli_module.EXIT_OK
    )
    assert seen["derive_input"] is seen["params"]
    assert seen["gate_params"] is seen["params"]
    assert seen["gate_layout"] is seen["layout"]


# ---------------------------------------------------------------------------
# 3. 実効転がり半径の配線（tasks.md「実効転がり半径の配線」/ design.md `#### Layout`）
# ---------------------------------------------------------------------------


def test_an_observed_rolling_radius_replaces_the_nominal_half_diameter() -> None:
    """観測があれば鉛直スタックはその値で組み上がる（要件 10.3, 4.7）。"""
    params = _params()
    nominal_radius_mm = params.chassis.wheel.nominal_diameter_mm / 2.0
    observed_radius_mm = nominal_radius_mm - 0.75

    before = derive_layout(params).vertical
    after = derive_layout(
        params,
        ObservedRollingRadius(
            radius_mm=observed_radius_mm, provenance=Provenance.MEASURED
        ),
    ).vertical

    assert after.effective_rolling_radius_mm == pytest.approx(observed_radius_mm)
    for name in (
        "effective_rolling_radius_mm",
        "axle_center_height_mm",
        "motor_body_bottom_height_mm",
        "fastener_bottom_height_mm",
        "mount_face_height_mm",
    ):
        assert getattr(after, name) - getattr(before, name) == pytest.approx(-0.75), (
            f"{name} が観測された実効転がり半径に追随していない"
        )


def test_using_an_observation_drops_the_nominal_diameter_from_the_provenance_fold(
    tmp_path: Path,
) -> None:
    """⚠️ **観測を使ったのに仮値を名乗る状態を塞ぐ**（design.md `#### Layout` Notes）。

    出所を `wheel.nominal_diameter_mm` だけ仮値にした寸法設定を用意する。
    観測を渡さなければ導出の出所は仮値（公称値を使ったのだから正しい）。
    実測の観測を渡せば ⚠️ **公称値の寄与が畳み込みから外れて実測になる**。
    """
    path = _dimensions_with_provenance(
        tmp_path / "dimensions.json",
        measured_except=("wheel.nominal_diameter_mm",),
    )
    params = load_params(path)

    assert derive_layout(params).provenance == Provenance.ASSUMED
    assert (
        derive_layout(
            params,
            ObservedRollingRadius(radius_mm=29.25, provenance=Provenance.MEASURED),
        ).provenance
        == Provenance.MEASURED
    )


def test_an_assumed_observation_pulls_the_provenance_down(tmp_path: Path) -> None:
    """⚠️ **逆向きも塞ぐ。** 概算の観測を使えば導出の出所は仮値である。

    ⚠️ 観測の出所を畳み込みへ足し忘れると、全項目が実測の寸法設定の下で
    「概算の観測を使ったのに実測を名乗る」状態が通ってしまう。
    """
    path = _dimensions_with_provenance(tmp_path / "dimensions.json")
    params = load_params(path)

    assert derive_layout(params).provenance == Provenance.MEASURED
    assert (
        derive_layout(
            params,
            ObservedRollingRadius(radius_mm=29.25, provenance=Provenance.ASSUMED),
        ).provenance
        == Provenance.ASSUMED
    )


def test_the_cli_passes_the_recorded_observation_into_the_geometry(
    tmp_path: Path,
) -> None:
    """観測記録の代表値が `layout` サブコマンドの導出記録へ現れる。

    ⚠️ **差し替え点は `layout._effective_rolling_radius_mm` ただ1つであり、
    そこへ値と出所を運ぶのがコマンド入口の責務である**（tasks.md の申し送り）。
    """
    measurements = _measurements_with_representative(
        tmp_path / "measurements.json",
        diameter_mm=58.5,
        provenance=Provenance.MEASURED,
    )
    output = tmp_path / "layout.json"
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(output),
                "--measurements",
                str(measurements),
            ]
        )
        == cli_module.EXIT_OK
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["vertical"]["effective_rolling_radius_mm"] == pytest.approx(
        58.5 / 2.0
    )


def test_the_cli_uses_the_nominal_radius_when_the_record_has_no_representative(
    tmp_path: Path,
) -> None:
    """代表値が未記入（組立前）なら公称値の半分を使う。⚠️ **失敗にしない。**"""
    output = tmp_path / "layout.json"
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(output),
                "--measurements",
                str(DEFAULT_MEASUREMENTS_PATH),
            ]
        )
        == cli_module.EXIT_OK
    )
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["vertical"]["effective_rolling_radius_mm"] == pytest.approx(
        _params().chassis.wheel.nominal_diameter_mm / 2.0
    )
    assert load_assembly_record(DEFAULT_MEASUREMENTS_PATH).representative_wheel_diameter_mm is None


@pytest.mark.parametrize(
    ("representative", "mass", "expected"),
    [
        (Provenance.MEASURED, Provenance.ASSUMED, Provenance.MEASURED),
        (Provenance.ASSUMED, Provenance.MEASURED, Provenance.ASSUMED),
    ],
    ids=["representative_measured", "representative_assumed"],
)
def test_the_cli_carries_the_representative_diameters_own_provenance(
    representative: Provenance,
    mass: Provenance,
    expected: Provenance,
    tmp_path: Path,
) -> None:
    """⚠️ **入口が運ぶのは代表径の出所であって、観測記録の他のどの項目でもない。**

    `_observed_rolling_radius` は `record.provenance[REPRESENTATIVE_DIAMETER_PATH]`
    を読む。⚠️ **ここを別のキー（例: `mass_g`）へ差し替えても、出荷の観測記録では
    全項目が同じ出所であるため何も起きない**——差し替えが見えるのは、項目ごとに
    出所が食い違い始めたときである（タスク 6.7 は項目を1つずつ埋める）。その瞬間に
    `layout.json` の `provenance` は嘘になり、⚠️ **実測を使ったのに仮値を名乗る
    （またはその逆の）幾何**が下流へ流れる。

    そこで代表径と `mass_g` の出所を**互いに逆向き**に置き、両方向で
    `layout.json` の出所が代表径のほうへ従うことを固定する。寸法パラメータは
    全件を実測にしてあるため、畳み込みの結果を決めるのは観測の出所だけである。
    """
    dimensions = _dimensions_with_provenance(tmp_path / "dimensions.json")
    measurements = _measurements_with_representative(
        tmp_path / "measurements.json",
        diameter_mm=58.5,
        provenance=representative,
    )
    document = json.loads(measurements.read_text(encoding="utf-8"))
    document["provenance"]["mass_g"] = mass.value
    measurements.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    record = load_assembly_record(measurements)
    assert record.provenance[REPRESENTATIVE_DIAMETER_PATH] is representative
    assert record.provenance["mass_g"] is mass, "取り違えを見せる前提が崩れた"

    output = tmp_path / "layout.json"
    assert (
        cli_module.main(
            [
                "layout",
                "--dimensions",
                str(dimensions),
                "--measurements",
                str(measurements),
                "--output",
                str(output),
            ]
        )
        == cli_module.EXIT_OK
    )
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["provenance"] == expected.value, (
        "layout.json の出所が代表径の出所に従っていない"
        f"（代表径={representative.value} / mass_g={mass.value}）"
    )


def test_load_compression_rejects_a_layout_that_is_not_paired_with_the_params() -> None:
    """⚠️ **対になっていない寸法と幾何から「公称値との差」を出さない**（要件 10.4）。

    差は「公称の転がり半径 − 実効転がり半径」であり、記録側の公称値と寸法設定の
    公称値が食い違えば、⚠️ **どちらの公称値に対する差なのかが決まらない**。
    古い記録を新しい寸法に対して読んだ状態がまさにそれである。
    """
    params = _params()
    layout = derive_layout(params)
    stale = replace(
        layout,
        vertical=replace(
            layout.vertical,
            nominal_rolling_radius_mm=layout.vertical.nominal_rolling_radius_mm + 1.0,
        ),
    )
    with pytest.raises(ConsistencyError) as excinfo:
        load_compression_mm(stale, params)
    assert "nominal_rolling_radius_mm" in str(excinfo.value)


def test_load_compression_is_the_difference_from_the_nominal_radius() -> None:
    """要件 10.4「公称値との差」の導出は1箇所にある。"""
    params = _params()
    nominal_radius_mm = params.chassis.wheel.nominal_diameter_mm / 2.0
    observed = ObservedRollingRadius(
        radius_mm=nominal_radius_mm - 0.75, provenance=Provenance.MEASURED
    )
    layout = derive_layout(params, observed)
    assert load_compression_mm(layout, params) == pytest.approx(0.75)
    assert load_compression_mm(derive_layout(params), params) == pytest.approx(0.0)


def test_the_layout_command_reports_the_difference_from_the_nominal_diameter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """要件 10.4: 実効転がり径と**公称値との差**が読める形で出る。

    ⚠️ **差は公称径から導く。** 「1.5」のような直書きは、公称径が実測で動いた
    瞬間に差そのものを見ないまま落ちる（60.0 → 57.9 で実際に起きた）。
    ⚠️ **数字列の含有ではなく値で照合する**——`"1.5"` は `"21.5"` にも
    `"1.53"` にも含まれてしまい、差が出ていない出力を通してしまう。
    """
    import re

    observed_diameter_mm = 58.5
    nominal_diameter_mm = _params().chassis.wheel.nominal_diameter_mm
    assert observed_diameter_mm != pytest.approx(nominal_diameter_mm), (
        "観測が公称と同値では「差が出る」ことを示せない"
    )
    measurements = _measurements_with_representative(
        tmp_path / "measurements.json",
        diameter_mm=observed_diameter_mm,
        provenance=Provenance.MEASURED,
    )
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(tmp_path / "layout.json"),
                "--measurements",
                str(measurements),
            ]
        )
        == cli_module.EXIT_OK
    )
    out = capsys.readouterr().out
    assert str(observed_diameter_mm) in out
    printed = re.search(
        r"実効転がり径 (\S+?)mm（公称 (\S+?)mm / 差 (\S+?)mm", out
    )
    assert printed is not None, f"実効転がり径と公称値との差の行が出力に無い: {out}"
    assert float(printed.group(1)) == pytest.approx(observed_diameter_mm)
    assert float(printed.group(2)) == pytest.approx(nominal_diameter_mm)
    assert float(printed.group(3)) == pytest.approx(
        nominal_diameter_mm - observed_diameter_mm
    ), f"公称 {nominal_diameter_mm}mm との差が出力に無い: {out}"


# ---------------------------------------------------------------------------
# 4. サブコマンドの振る舞い（in-process）
# ---------------------------------------------------------------------------


def test_the_parser_declares_exactly_the_four_subcommands() -> None:
    """design.md `#### Cli` の表がそのまま4サブコマンドである。"""
    import argparse

    parser = cli_module.build_parser()
    actions = [
        action
        for action in parser._actions  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    assert actions, "サブコマンドが1つも宣言されていない"
    assert set(actions[0].choices) == {"build", "check", "layout", "joints"}


def test_layout_writes_the_derivation_record(tmp_path: Path) -> None:
    """`layout` は幾何を導出して記録を書く（形状ライブラリを要さない）。"""
    output = tmp_path / "layout.json"
    assert cli_module.main(["layout", "--output", str(output)]) == cli_module.EXIT_OK
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["base_radius_mm"] == pytest.approx(
        derive_layout(_params()).base_radius_mm
    )


def test_layout_check_accepts_a_simulator_config_that_agrees(tmp_path: Path) -> None:
    """`--check CONFIG` は還元先の値と一致すれば正常終了する（要件 10.6-10.8）。"""
    params = _params()
    config = tmp_path / "drivetrain.json"
    config.write_text(
        json.dumps(
            {SIMULATOR_WHEEL_KEY: params.chassis.wheel.nominal_diameter_mm},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    assert (
        cli_module.main(
            ["layout", "--output", str(tmp_path / "layout.json"), "--check", str(config)]
        )
        == cli_module.EXIT_OK
    )


def test_layout_check_rejects_a_simulator_config_that_disagrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """還元先の値が食い違えば終了コード 1（要件 10.8）。"""
    config = tmp_path / "drivetrain.json"
    config.write_text(
        json.dumps({SIMULATOR_WHEEL_KEY: 48.0}, ensure_ascii=False), encoding="utf-8"
    )
    assert (
        cli_module.main(
            ["layout", "--output", str(tmp_path / "layout.json"), "--check", str(config)]
        )
        == cli_module.EXIT_MISMATCH
    )
    assert "48.0" in capsys.readouterr().err


def test_layout_check_rejects_a_simulator_config_without_the_key(
    tmp_path: Path,
) -> None:
    """⚠️ **記録が無いことを一致として読み飛ばさない**（上流 `tolerance --check` と同じ）。"""
    config = tmp_path / "drivetrain.json"
    config.write_text(json.dumps({"motor_rpm": 530.0}), encoding="utf-8")
    assert (
        cli_module.main(
            ["layout", "--output", str(tmp_path / "layout.json"), "--check", str(config)]
        )
        == cli_module.EXIT_MISMATCH
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "⚠️ **タスク 7.1 が還元するまで一致しない。** ホイールの公称径が実測で "
        "57.9mm になり、出荷のシミュレータ設定（60.0mm）と食い違う。⚠️ **いま "
        "57.9 を書き込んではならない**——7.1 が還元するのは*荷重下の実効転がり径*"
        "であり公称値ではない（measurements.json の representative_wheel_diameter_mm "
        "は今も null）。しかも 59.5mm 未満へ還元すると trajectory_sim の要件 4.8 の"
        "検査が落ちる（到達可否の掃引格子では 48mm と区別できなくなる）。"
        "⚠️ **strict=True である**——7.1 の決着を経ずに還元すれば、この検査が "
        "XPASS で赤くなって知らせる（要件 10.6, 10.7, 10.8）。"
    ),
)
def test_the_shipped_simulator_config_agrees_with_the_current_geometry(
    tmp_path: Path,
) -> None:
    """出荷のシミュレータ設定は現在の幾何と一致する（要件 10.8 の現状）。"""
    config = REPO_ROOT / "configs" / "trajectory_sim" / "drivetrain-wheel60.json"
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(tmp_path / "layout.json"),
                "--check",
                str(config),
            ]
        )
        == cli_module.EXIT_OK
    )


def test_joints_writes_the_fastener_schedule(tmp_path: Path) -> None:
    """`joints` は接合部と締結部品を導出して記録を書く（形状ライブラリを要さない）。"""
    output = tmp_path / "joint-schedule.json"
    assert cli_module.main(["joints", "--output", str(output)]) == cli_module.EXIT_OK
    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["parameters_digest"] == parameters_digest(_params().chassis)
    assert document["joints"], "接合部が1件も導出されていない"
    assert document["lines"], "締結部品の一覧が空である"


def test_check_reports_a_missing_baseline_record_with_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ **記録が無いことをパスつきで報せる**（tasks.md 2.5 → 4.1 / 4.2 の申し送り）。

    既定の記録 `configs/chassis_mechanism/geometry-baseline.json` を作るのは
    タスク 4.2 である。それまで識別子照合は「記録が無い」ところまで完走する。
    ⚠️ **無いことを成功にしない**——空の記録はどんな再生成とも一致してしまう。
    """
    missing = tmp_path / "geometry-baseline.json"
    assert (
        cli_module.main(["check", "--digest-only", "--baseline", str(missing)])
        == cli_module.EXIT_USAGE
    )
    assert str(missing) in capsys.readouterr().err


def test_check_digest_only_succeeds_against_a_current_record(tmp_path: Path) -> None:
    """識別子が一致すれば `--digest-only` は正常終了する（形状を再生成しない）。"""
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    assert (
        cli_module.main(["check", "--digest-only", "--baseline", str(baseline)])
        == cli_module.EXIT_OK
    )


def test_check_digest_only_detects_a_stale_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """寸法を変えたまま記録を更新していない状態は終了コード 1（要件 1.12）。"""
    stale = "sha256:" + "0" * 64
    baseline = _write_baseline_file(
        tmp_path / "baseline.json", _fake_parts(), digest=stale
    )
    assert (
        cli_module.main(["check", "--digest-only", "--baseline", str(baseline)])
        == cli_module.EXIT_MISMATCH
    )
    err = capsys.readouterr().err
    assert stale in err
    assert parameters_digest(_params().chassis) in err, "現在の識別子が併記されていない"


def test_check_digest_only_does_not_regenerate_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ `--digest-only` は形状構築へ一切触れない（触れれば CAD が要る）。"""

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("--digest-only なのに形状を再生成した")

    monkeypatch.setattr(shapes_module, "build_parts", _explode)
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    assert (
        cli_module.main(["check", "--digest-only", "--baseline", str(baseline)])
        == cli_module.EXIT_OK
    )


def test_the_full_check_compares_regenerated_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既定の `check` は形状を再生成して指標を照合する。"""
    parts = _fake_parts()
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: parts)
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)
    assert cli_module.main(["check", "--baseline", str(baseline)]) == cli_module.EXIT_OK


def test_the_full_check_reports_every_metric_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """指標の食い違いは終了コード 1 で、部品名つきで出る。

    ⚠️ **部品の不在／余剰を他の乖離と区別する**（`_format_mismatch`）。「部品が
    消えた」ことと「体積が 1% ずれた」ことは、直し方も緊急度も違う——前者は
    分割や生成の取りこぼしであり、後者は寸法か許容差の話である。`VANISHED_LABEL`
    と `APPEARED_LABEL` は `__all__` に載る公開契約であり、⚠️ **入れ替えても
    値としては「1件の不一致」でしかない**ため、行に現れる印そのものを固定する。
    """
    recorded = _fake_parts(("hub_plate", "battery_tray"), volume_mm3=1000.0)
    regenerated = _fake_parts(("hub_plate", "cable_guide"), volume_mm3=2000.0)
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: regenerated)
    baseline = _write_baseline_file(tmp_path / "baseline.json", recorded)
    assert (
        cli_module.main(["check", "--baseline", str(baseline)])
        == cli_module.EXIT_MISMATCH
    )
    err = capsys.readouterr().err
    lines = {
        name: [line for line in err.splitlines() if name in line]
        for name in ("hub_plate", "battery_tray", "cable_guide")
    }
    for name, matched in lines.items():
        assert matched, f"{name} の不一致が報告されていない"

    # 記録にあって再生成に無い部品 → 「消えた」
    (vanished,) = lines["battery_tray"]
    assert cli_module.VANISHED_LABEL in vanished, vanished
    assert cli_module.APPEARED_LABEL not in vanished, vanished
    assert "記録=在 再生成=不在" in vanished, (
        f"在／不在の向きが行から読めない: {vanished}"
    )

    # 再生成にあって記録に無い部品 → 「増えた」
    (appeared,) = lines["cable_guide"]
    assert cli_module.APPEARED_LABEL in appeared, appeared
    assert cli_module.VANISHED_LABEL not in appeared, appeared
    assert "記録=不在 再生成=在" in appeared, (
        f"在／不在の向きが行から読めない: {appeared}"
    )

    # 体積の食い違い → 「乖離」であって不在／余剰ではない
    (deviation,) = [line for line in lines["hub_plate"] if "volume_mm3" in line]
    assert cli_module.DEVIATION_LABEL in deviation, deviation
    assert cli_module.VANISHED_LABEL not in deviation, deviation
    assert cli_module.APPEARED_LABEL not in deviation, deviation


def test_the_default_check_passes_before_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **組立前でも総合検査が正常終了する**（tasks.md 4.1 の完了状態）。

    観測記録は出荷時点で全項目が未記入であり、⚠️ **必ず未充足である**。
    それを既定の総合検査へ混ぜると、寸法・隙間・接合の検査を組立前に回せなくなる。
    """
    assert load_assembly_record(DEFAULT_MEASUREMENTS_PATH).representative_wheel_diameter_mm is None
    parts = _fake_parts()
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: parts)
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)
    assert cli_module.main(["check", "--baseline", str(baseline)]) == cli_module.EXIT_OK


def test_the_default_check_does_not_consult_the_observation_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **充足検査は既定の総合検査に混ざらない**（tasks.md 4.1）。

    観測の**充足**を見に行けばここで落ちる。⚠️ 実効転がり半径の読み取り
    （`load_assembly_record`）とは別物であり、混同すると本検査が空振りする。
    """
    monkeypatch.setattr(
        cli_module,
        "missing_observations",
        lambda record: (_ for _ in ()).throw(
            AssertionError("既定の check が観測の充足検査を行った")
        ),
    )
    parts = _fake_parts()
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: parts)
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)
    assert cli_module.main(["check", "--baseline", str(baseline)]) == cli_module.EXIT_OK


def test_check_observations_lists_every_missing_item_before_assembly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--observations` は未了の観測を**全件**挙げて終了コード 1 になる（要件 9.8）。"""
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    assert (
        cli_module.main(
            [
                "check",
                "--digest-only",
                "--observations",
                "--baseline",
                str(baseline),
                "--measurements",
                str(DEFAULT_MEASUREMENTS_PATH),
            ]
        )
        == cli_module.EXIT_MISMATCH
    )
    err = capsys.readouterr().err
    record = load_assembly_record(DEFAULT_MEASUREMENTS_PATH)
    missing = assembly_module.missing_observations(record)
    assert missing, "出荷の観測記録が既に充足している（前提が崩れた）"
    for entry in missing:
        assert entry in err, f"未了項目 {entry!r} が報告されていない"


def test_check_observations_reports_the_missing_items_even_without_a_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ **充足検査は形状指標の記録の読み込みより前にある**（4.1 の是正）。

    充足検査の入力は `measurements.json` だけであり、記録に論理的に依存しない。
    後ろに置くと、既定の記録を作るタスク 4.2 より前は `--observations` を
    指定しても「記録が無い」で止まり、⚠️ **観測の未了一覧を1度も見られない**。
    ⚠️ 終了コードは「記録が無い」（2）のままである——記録の不在は依然として
    失敗であり、観測の報告がそれを覆い隠すことはない。
    """
    missing_baseline = tmp_path / "absent-baseline.json"
    assert not missing_baseline.exists()
    assert (
        cli_module.main(
            [
                "check",
                "--digest-only",
                "--observations",
                "--baseline",
                str(missing_baseline),
                "--measurements",
                str(DEFAULT_MEASUREMENTS_PATH),
            ]
        )
        == cli_module.EXIT_USAGE
    )
    err = capsys.readouterr().err
    assert str(missing_baseline) in err, err
    outstanding = assembly_module.missing_observations(
        load_assembly_record(DEFAULT_MEASUREMENTS_PATH)
    )
    assert outstanding, "出荷の観測記録が既に充足している（前提が崩れた）"
    for entry in outstanding:
        assert entry in err, f"未了項目 {entry!r} が記録の不在に覆い隠されている"


def test_check_observations_succeeds_on_a_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """充足していれば `--observations` は正常終了する（要件 9.8 の判定）。"""
    monkeypatch.setattr(cli_module, "missing_observations", lambda record: ())
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    assert (
        cli_module.main(
            ["check", "--digest-only", "--observations", "--baseline", str(baseline)]
        )
        == cli_module.EXIT_OK
    )


def test_check_reports_every_clearance_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """床との隙間の不足は終了コード 1 で、⚠️ **違反した部位が全件**不足量つきで出る。

    要件 4.4:「該当する部位と不足量を示す」。⚠️ **1件目で打ち切らない**——
    「ブラケットを直したら次はボルト頭」という往復を避けるのが `clearance` が
    違反を値で返す理由であり、入口はその全件をそのまま流さねばならない。

    ⚠️ **違反していない部位を並べない**ことも併せて固定する。全5部位の名前を
    無条件に並べる実装は、この検査を「名前が出ている」だけで通してしまう。

    ⚠️ 出荷パラメータで**5部位すべてを同時に違反させることはできない**。
    下限を上げると `ChassisParams` がバッテリトレイの下面（28.0mm）との関係で
    先に構築を拒否し（要件 7.1）、実効転がり半径を下げて機体を沈めると
    モータ胴体下面が床下（負）になって `VerticalStack` が先に拒否する。
    到達できる最大が本件の2部位である。
    """
    import re

    violating = {"motor_body", "bracket"}
    raised_minimum_mm = 27.0
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    document["clearance"]["min_ground_clearance_mm"] = raised_minimum_mm
    dimensions = tmp_path / "dimensions.json"
    dimensions.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    baseline = _write_baseline_file(
        tmp_path / "baseline.json",
        _fake_parts(),
        digest=parameters_digest(load_params(dimensions).chassis),
    )
    assert (
        cli_module.main(
            [
                "check",
                "--digest-only",
                "--baseline",
                str(baseline),
                "--dimensions",
                str(dimensions),
            ]
        )
        == cli_module.EXIT_MISMATCH
    )
    err = capsys.readouterr().err
    for name in violating:
        assert name in err, f"違反した部位 {name} が報告されていない"
    # ⚠️ **不足量は下限と鉛直スタックから導く。** 「15.5」のような直書きは、
    # 車軸高さがホイールの実測で動いた瞬間に嘘になる（60.0 → 57.9 で実際に
    # 落ちた）。⚠️ **数字列の含有ではなく値で照合する**——部位ごとに
    # 「隙間・下限・不足量」の3つ組を読み、⚠️ **不足量が下限と隙間の差である**
    # ことまで見る（不足量の欄に何を入れても通る検査にしない）。
    reported = {
        name: (float(gap_mm), float(limit_mm), float(shortfall_mm))
        for name, gap_mm, limit_mm, shortfall_mm in re.findall(
            r"(\w+) の隙間 (\S+?)mm が下限 (\S+?)mm を (\S+?)mm 下回る", err
        )
    }
    assert set(reported) == violating, f"報告された部位が違う: {err}"
    vertical = derive_layout(load_params(dimensions)).vertical
    for name, (gap_mm, limit_mm, shortfall_mm) in reported.items():
        assert limit_mm == pytest.approx(raised_minimum_mm), name
        assert shortfall_mm == pytest.approx(limit_mm - gap_mm), name
        assert shortfall_mm > 0.0, name
    assert reported["motor_body"][0] == pytest.approx(
        vertical.motor_body_bottom_height_mm
    ), f"モータ胴体の隙間が鉛直スタックの高さと一致しない: {err}"
    for name in set(CLEARANCE_ITEM_NAMES) - violating:
        assert name not in err, f"違反していない部位 {name} が違反として並んでいる"


def test_unknown_keys_in_the_dimensions_file_exit_with_the_usage_code(
    tmp_path: Path,
) -> None:
    """設定の未知キーは入力不正（終了コード 2）である（要件 1.4）。"""
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    document["surprise"] = 1
    dimensions = tmp_path / "dimensions.json"
    dimensions.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert (
        cli_module.main(
            ["layout", "--output", str(tmp_path / "l.json"), "--dimensions", str(dimensions)]
        )
        == cli_module.EXIT_USAGE
    )


def test_build_without_update_baseline_leaves_the_record_byte_for_byte_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **既定の `build` は記録に触れない**（`--update-baseline` の help の約束）。

    ⚠️ `--baseline` の既定は**出荷の記録**（`DEFAULT_BASELINE_PATH`）である。
    この関門が壊れると、ふつうの `build` が自分の比較対象を上書きし、以後
    `check` は形状が何に変わっても一致し続ける——⚠️ **要件 1.12 の照合が
    空振りになる**。記録が「一致した」と言い続ける状態は、記録が無い状態より悪い。
    """
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: _fake_parts())
    monkeypatch.setattr(export_module, "export_parts", _fake_export)
    # 再生成結果とは**違う**内容の記録を置く。触れられれば必ずバイト列が動く。
    baseline = _write_baseline_file(
        tmp_path / "baseline.json", _fake_parts(("hub_plate",), volume_mm3=7.0)
    )
    before = baseline.read_bytes()
    assert (
        cli_module.main(
            [
                "build",
                "--output-dir",
                str(tmp_path / "cad"),
                "--baseline",
                str(baseline),
            ]
        )
        == cli_module.EXIT_OK
    )
    assert baseline.read_bytes() == before, (
        "--update-baseline を指定していないのに形状指標の記録が書き換わった"
    )


def test_update_baseline_inherits_the_tolerances_of_an_existing_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **既存の記録の許容差は引き継がれ、黙って既定値へ戻らない**（4.2 への申し送り）。

    許容差の見直しは形状の実測に基づく判断であり、記録の内容はタスク 4.2 の
    所有である。⚠️ **再生成の副作用で緩む（あるいは締まる）と、それに気付ける
    のは次に `check` が落ちた／落ちなくなったときだけ**である。
    `--update-baseline` が更新してよいのは指標とパラメータ識別子だけである。
    """
    parts = _fake_parts()
    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: parts)
    monkeypatch.setattr(export_module, "export_parts", _fake_export)
    baseline = tmp_path / "baseline.json"
    dump_baseline(
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest=parameters_digest(_params().chassis),
            # ⚠️ どちらも既定値（1e-6 / 1e-3mm）とは違う値である。
            volume_rel_tolerance=5e-7,
            bbox_abs_tolerance_mm=2e-2,
            generator_version="test-fixture",
            parts={"stale_part": _fake_metrics("stale_part", volume_mm3=1.0)},
        ),
        baseline,
    )
    assert (
        cli_module.main(
            [
                "build",
                "--output-dir",
                str(tmp_path / "cad"),
                "--update-baseline",
                "--baseline",
                str(baseline),
            ]
        )
        == cli_module.EXIT_OK
    )
    written = load_baseline(baseline)
    assert written.volume_rel_tolerance == 5e-7, "体積の許容差が既定値へ戻された"
    assert written.bbox_abs_tolerance_mm == 2e-2, "境界箱の許容差が既定値へ戻された"
    # 指標そのものは更新されている（引き継ぐのは許容差だけである）。
    assert set(written.parts) == {part.name for part in parts}


def test_a_non_utf8_config_is_an_input_error_not_a_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ **UTF-8 でない設定ファイルは終了コード 2 であり、追跡情報を残さない。**

    `Path.read_text(encoding="utf-8")` が投げる `UnicodeDecodeError` は
    `ValueError` の一種であり ⚠️ **`OSError` ではない**——`main` の `except` に
    無ければ表を素通りし、追跡情報つきの異常終了（終了コード 1）になる。
    本 Spec の設定ファイルは日本語を含み、⚠️ Windows の編集器が CP932 で保存する
    経路は現実にある。1 は「検査の不一致・違反」であるため、そこへ落ちると
    ⚠️ **CI が「読めないファイル」と「乖離した形状指標」を区別できない**。
    """
    dimensions = tmp_path / "dimensions.json"
    # ⚠️ `errors="ignore"` は CP932 に無い記号（⚠️ など）を落とすためだけのもので
    # ある。残る日本語が CP932 で符号化されていること自体が本件の再現である。
    dimensions.write_bytes(
        DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8").encode(
            "cp932", errors="ignore"
        )
    )
    with pytest.raises(UnicodeDecodeError):
        dimensions.read_text(encoding="utf-8")
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(tmp_path / "l.json"),
                "--dimensions",
                str(dimensions),
            ]
        )
        == cli_module.EXIT_USAGE
    )
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert err.startswith(f"{cli_module.PROGRAM}: "), err


def test_a_non_utf8_simulator_config_is_reported_with_its_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ 本モジュール自身の読み手も同じ扱いである（`_read_json_document`）。

    ⚠️ **どのファイルが読めないのかを失敗から消さない**——裸の
    `UnicodeDecodeError` はパス名を持たない。
    """
    config = tmp_path / "drivetrain.json"
    config.write_bytes(
        json.dumps({SIMULATOR_WHEEL_KEY: 60.0, "備考": "日本語"}, ensure_ascii=False)
        .encode("cp932")
    )
    assert (
        cli_module.main(
            [
                "layout",
                "--output",
                str(tmp_path / "l.json"),
                "--check",
                str(config),
            ]
        )
        == cli_module.EXIT_USAGE
    )
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert str(config) in err, err


def test_an_unwritable_output_path_is_an_input_error_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ **`OSError` も `main` が終了コードへ写す**（送出しない）。

    書き出し先の不備は利用者が直せる入力の誤りであり、終了コード 2 である。
    ⚠️ `main` の `except` から `OSError` を外しても既存の検査は緑のままである
    ことが変異掃討で判っており、⚠️ **「例外は写像され、送出されない」という
    `main` の中心的な主張**をここで固定する。
    """
    missing_dir = tmp_path / "not-created"
    assert not missing_dir.exists()
    assert (
        cli_module.main(["layout", "--output", str(missing_dir / "layout.json")])
        == cli_module.EXIT_USAGE
    )
    err = capsys.readouterr().err
    assert "Traceback" not in err, err
    assert err.startswith(f"{cli_module.PROGRAM}: "), err


def test_help_exits_zero() -> None:
    """`--help` は使い方の誤りではない。"""
    assert cli_module.main(["--help"]) == cli_module.EXIT_OK


def test_an_unknown_subcommand_exits_with_the_usage_code() -> None:
    """`argparse` の使用法エラーも終了コード 2 である。"""
    assert cli_module.main(["nonexistent"]) == cli_module.EXIT_USAGE


def test_no_subcommand_exits_with_the_usage_code() -> None:
    """サブコマンドは必須である。"""
    assert cli_module.main([]) == cli_module.EXIT_USAGE


# ---------------------------------------------------------------------------
# 5. 形状ライブラリ非導入の環境（⚠️ スタブ自身の検査を先に行う）
# ---------------------------------------------------------------------------


def test_the_nocad_stub_actually_blocks_the_shape_library(tmp_path: Path) -> None:
    """⚠️ **スタブが効いていることを先に固定する。**

    スタブが無効なら、以下の「非導入環境」の検査はすべて導入済み環境で
    実行されることになり、何も証明しない。
    """
    _assert_stub_blocks(_nocad_stub(tmp_path))


def test_layout_completes_without_the_shape_library(tmp_path: Path) -> None:
    """幾何の導出は形状ライブラリを要さない（tasks.md 4.1 の完了状態）。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    completed = _run_module(
        ["layout", "--output", str(tmp_path / "layout.json")], stub_dir=stub_dir
    )
    assert completed.returncode == cli_module.EXIT_OK, completed.stderr
    assert (tmp_path / "layout.json").exists()


def test_joints_completes_without_the_shape_library(tmp_path: Path) -> None:
    """接合部と締結部品の導出は形状ライブラリを要さない。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    completed = _run_module(
        ["joints", "--output", str(tmp_path / "joints.json")], stub_dir=stub_dir
    )
    assert completed.returncode == cli_module.EXIT_OK, completed.stderr
    assert (tmp_path / "joints.json").exists()


def test_digest_only_check_completes_without_the_shape_library(tmp_path: Path) -> None:
    """識別子だけの照合は形状ライブラリを要さない（要件 1.12）。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    completed = _run_module(
        ["check", "--digest-only", "--baseline", str(baseline)], stub_dir=stub_dir
    )
    assert completed.returncode == cli_module.EXIT_OK, completed.stderr


def test_digest_only_check_names_the_missing_record_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """記録が無い場合も**完走**し、パスを名指しする（4.2 より前の状態）。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    missing = tmp_path / "absent-baseline.json"
    completed = _run_module(
        ["check", "--digest-only", "--baseline", str(missing)], stub_dir=stub_dir
    )
    assert completed.returncode == cli_module.EXIT_USAGE
    assert str(missing) in completed.stderr


def test_build_fails_with_the_dedicated_exit_code_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """⚠️ **形状生成だけが専用の終了コードで失敗する。成功にしない**（要件 1.11）。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    output_dir = tmp_path / "out"
    completed = _run_module(
        ["build", "--output-dir", str(output_dir)], stub_dir=stub_dir
    )
    assert completed.returncode == cli_module.EXIT_CAD_UNAVAILABLE, completed.stderr
    assert not output_dir.exists(), "⚠️ 失敗したのに出力先が作られている"


def test_the_full_check_fails_with_the_cad_exit_code_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """`--digest-only` を付けない `check` は形状を再生成するため 3 で失敗する。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    baseline = _write_baseline_file(tmp_path / "baseline.json", _fake_parts())
    completed = _run_module(["check", "--baseline", str(baseline)], stub_dir=stub_dir)
    assert completed.returncode == cli_module.EXIT_CAD_UNAVAILABLE, completed.stderr


def test_importing_the_cli_does_not_pull_in_the_shape_library(tmp_path: Path) -> None:
    """`import chassis_mechanism.cli` が形状ライブラリへ到達しない（遅延 import）。"""
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import chassis_mechanism.cli as c; "
            "assert 'build123d' not in sys.modules; print(c.EXIT_CAD_UNAVAILABLE)",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=180.0,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "3"


# ---------------------------------------------------------------------------
# 6. 書き出しの失敗が届く型（tasks.md 3.7 → 4.1 の申し送り）
# ---------------------------------------------------------------------------


def test_a_runtime_error_from_the_writer_reaches_the_cli_as_a_geometry_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ **終了コード表はこの型に依存する**（tasks.md 3.7 の申し送り）。

    build123d は書き込み失敗を `RuntimeError` で報告する。`export._write_part` が
    それを `GeometryError` へ包んでいなければ、素の `RuntimeError` が
    `main()` を素通りして追跡情報つきの異常終了になる。
    ⚠️ 変異掃討で「この `RuntimeError` を外しても書き出しの 37 件すべてが緑」
    であることが判っており、⚠️ **塞ぐのは本件である**。
    """

    def _explode(solid: object, path: Path) -> None:
        raise RuntimeError("Failed to write STEP file")

    monkeypatch.setattr(export_module, "_write_step", _explode)
    with pytest.raises(GeometryError) as excinfo:
        export_module.export_parts(_fake_parts(("hub_plate",)), tmp_path / "out")
    assert cli_module.exit_code_for(excinfo.value) == cli_module.EXIT_MISMATCH
    assert not (tmp_path / "out").exists()


def test_the_cli_maps_the_wrapped_write_failure_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """書き出しの失敗は `main()` の中で終了コードへ写る（例外を送出しない）。"""

    def _explode(solid: object, path: Path) -> None:
        raise RuntimeError("Failed to write STEP file")

    monkeypatch.setattr(shapes_module, "build_parts", lambda *_: _fake_parts(("hub_plate",)))
    monkeypatch.setattr(export_module, "_write_step", _explode)
    assert (
        cli_module.main(["build", "--output-dir", str(tmp_path / "out")])
        == cli_module.EXIT_MISMATCH
    )
    assert "hub_plate" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 7. 形状ライブラリを導入した環境での通し
# ---------------------------------------------------------------------------


@requires_cad
def test_build_then_check_round_trip(tmp_path: Path) -> None:
    """`build --update-baseline` → `check` が実形状で通しで成立する（要件 1.11, 1.12）。"""
    baseline = tmp_path / "baseline.json"
    output_dir = tmp_path / "cad"
    assert (
        cli_module.main(
            [
                "build",
                "--output-dir",
                str(output_dir),
                "--update-baseline",
                "--baseline",
                str(baseline),
            ]
        )
        == cli_module.EXIT_OK
    )
    assert list(output_dir.glob("*.step")), "STEP が1つも出ていない"
    assert list(output_dir.glob("*.stl"))
    assert list(output_dir.glob("*.3mf"))
    assert cli_module.main(["check", "--baseline", str(baseline)]) == cli_module.EXIT_OK


# ---------------------------------------------------------------------------
# 8. `python -m chassis_mechanism` の入口
# ---------------------------------------------------------------------------


def test_the_module_entry_delegates_to_the_cli() -> None:
    """`__main__` は `cli.main` の戻り値をそのまま終了コードにする。"""
    source = (REPO_ROOT / "src" / "chassis_mechanism" / "__main__.py").read_text(
        encoding="utf-8"
    )
    called = _called_names(ast.parse(source))
    assert "main" in called
    assert "exit" in called


def test_the_module_entry_runs() -> None:
    """`python -m chassis_mechanism --help` が正常終了する。"""
    completed = _run_module(["--help"])
    assert completed.returncode == cli_module.EXIT_OK, completed.stderr
    for name in ("build", "check", "layout", "joints"):
        assert name in completed.stdout


@pytest.mark.parametrize(
    ("subcommand", "shipped"),
    [
        ("layout", DEFAULT_LAYOUT_PATH),
        ("joints", DEFAULT_JOINT_SCHEDULE_PATH),
    ],
)
def test_the_entry_point_reproduces_the_shipped_record_byte_for_byte(
    subcommand: str, shipped: Path, tmp_path: Path
) -> None:
    """出荷の導出記録は、この入口が今書くものと**バイト単位で同一**である。

    ⚠️ **これが崩れると `git status` が空の差分を報告し続ける。** 記録は
    `configs/chassis_mechanism/*.json`（LF 固定）であり、入口が別の整形や別の値を
    書けば、記録を書き出すたびに作業ツリーとチェックアウト内容がずれる。
    ⚠️ 併せて「既定の `--output` が出荷の記録を指している」ことも固定する
    ——別の場所を指していれば、この比較は永遠に成立しない。
    """
    generated = tmp_path / shipped.name
    assert (
        cli_module.main([subcommand, "--output", str(generated)]) == cli_module.EXIT_OK
    )
    assert generated.read_bytes() == shipped.read_bytes()

    parser = cli_module.build_parser()
    args = parser.parse_args([subcommand])
    assert args.output == shipped


def test_the_default_baseline_path_is_the_one_the_next_task_creates() -> None:
    """`--baseline` の既定は本 Spec の記録である（上流の記録ではない）。"""
    assert DEFAULT_BASELINE_PATH.name == "geometry-baseline.json"
    assert DEFAULT_BASELINE_PATH.parent.name == "chassis_mechanism"
