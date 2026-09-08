"""出荷する形状指標の記録と、その照合の回帰検査（タスク 4.2 / 要件 1.12）。

design.md「Directory Structure」の
`test_chassis_geometry_regression.py`（「再生成 → 指標照合・決定性（cad extra 必要）」）
に対応する。⚠️ **決定性そのものは 3群が既に固定している**
（`test_chassis_shapes.py::test_metrics_are_identical_across_two_independent_builds` /
`::test_two_builds_from_the_same_parameters_return_the_same_metrics`）。
本ファイルが足すのは、**出荷された記録**（`configs/chassis_mechanism/geometry-baseline.json`）
と現在の実装の関係である。

## タスク 4.2 の観測可能な完了状態は2文からなり、**両方**を固定する

1. 「記録を作った直後の照合が成功し」——出荷した記録が、現在の寸法パラメータから
   再生成した 18 部品の指標と一致する（`test_the_shipped_record_agrees_with_the_regenerated_shape`
   と `test_check_succeeds_against_the_shipped_record`）。
2. 「寸法を1つ変えると失敗する」——⚠️ **記録の数字を手でずらすのではなく、
   寸法パラメータを1つ変えて実際に形を作り直す**
   （`test_changing_one_dimension_makes_the_comparison_fail` と
   `test_check_fails_when_one_dimension_changed`）。記録側の数字をずらして落とす検査は、
   「照合が数を比べている」ことしか言えない——⚠️ **許容差が寸法変更を飲み込む幅で
   あっても緑のまま**であり、記録は飾りになる。実際に寸法を変えて生じた乖離が
   記録の許容差を何桁も超えることまで見て、初めて「照合は失敗しうる」と言える
   （`test_the_recorded_tolerances_are_tight_enough_to_catch_one_changed_dimension`）。

## 在／不在の符号化（タスク 4.2 の3項目め）

「部品が消えた場合も不一致として検出する」ことも、⚠️ **記録から行を削るのではなく
寸法変更で起こす**。`board.can_clearance_mm` を半分にすると基板デッキの外径が造形
可能寸法を超え、分割されない `board_deck` が消えて `board_deck_1..3` が現れる
（`test_a_dimension_change_that_splits_a_part_is_reported_as_presence`）。
⚠️ **これは「部品名が増減しうる」ことの実物の証拠である**——分割数は設定値ではなく
導出であり（要件 2.1）、寸法しだいで部品名の集合そのものが動く。

## ⚠️ 本ファイルは出荷ファイルへ意図的にピン留めしている

`test_chassis_cli.py` は既定の記録の有無にも中身にも依存しない（タスク 4.1 が記録を
出荷しなかったため）。**本ファイルはその逆で、出荷ファイルそのものを検査対象にする**
——タスク 4.2 が所有するのは「出荷された記録が現在の実装と整合していること」であり、
それは `tmp_path` の記録では言えない。

⚠️ **数値のリテラルを書かない。** 体積・境界箱の実測値をテストへ写すと、正当な設計
変更のたびに2箇所（記録と本ファイル）を直すことになる。変更する寸法の値すら
出荷ファイルから読んで加工する。本ファイルが主張するのは**関係**（記録 == 再生成、
寸法を変えれば落ちる、許容差より何桁も大きい）と、記録が満たすべき**性質**
（部品名の集合・許容差の上下限・改行・再現性）だけである。

## ⚠️ 幾何は必ず `cli` の対で組み立てる

寸法と幾何の対を作るのは `cli` の1関数だけである（4.1 の申し送り / `cli` docstring
「`params` → `layout` の対」）。本ファイルが `load_params` → `derive_layout` を自前で
並べると、⚠️ **`check` が見る幾何と本ファイルが見る幾何が将来ずれる**——実効転がり
半径の観測（要件 10.4）は入口が幾何へ差し込んでおり、代表値が記入された瞬間に
「入口の再生成」と「自前の再生成」は別物になる。関数名は公開定数
`cli.PARAMS_AND_LAYOUT_FUNCTION` から取る（手書きの名前は改名で空振りする）。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名がセッション
全体でフラットであるため、`test_chassis_` 接頭辞を付ける（design.md
「Directory Structure」）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from catch_mechanism import (
    ABSENT,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    PartMetrics,
    compare_metrics,
    load_baseline,
)

from chassis_mechanism import cli as cli_module
from chassis_mechanism.baseline import DEFAULT_BASELINE_PATH, dump_baseline, verify_digest
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    ChassisParams,
    ResolvedParams,
    load_params,
    parameters_digest,
)
from chassis_mechanism.layout import ChassisLayout
from chassis_mechanism.shapes import part_names

# ---------------------------------------------------------------------------
# 形状ライブラリの有無
# ⚠️ **モジュール全体を `pytest.importorskip` で落とさない。** 記録そのものの
# 健全性（版・部品名・許容差・識別子・改行・再現性）の検査は形状ライブラリを
# 要さず、非導入環境でも**走らなければならない**（4.1 の完了状態「形状ライブラリ
# 非導入の環境で … 識別子照合が完走する」）。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "記録の健全性と識別子照合はこの環境でも完走する（design.md「Allowed Dependencies」）。",
)


# ---------------------------------------------------------------------------
# 許容差の上下限（⚠️ **どちらか一方だけでは足りない**）
# ---------------------------------------------------------------------------

MIN_VOLUME_REL_TOLERANCE: float = 1e-7
"""体積の相対許容差の下限。

⚠️ 0 や 1e-9 を記録すると、同一ライブラリが同一形状に対して出す求積誤差
（本 Spec の実測で相対 1e-15 級）ではなく**版差**で破綻する。`cli` の既定
（`_DEFAULT_VOLUME_REL_TOLERANCE` = 1e-6）はこの下限の 10 倍である。
"""

MAX_VOLUME_REL_TOLERANCE: float = 1e-4
"""体積の相対許容差の上限。

⚠️ **下限だけを検査すると「1.0 を記録して常に緑」を通してしまう。** 上限を置く
だけでも足りず、「実際の寸法変更がこの幅を超えること」を
`test_the_recorded_tolerances_are_tight_enough_to_catch_one_changed_dimension` が
実測で確かめる。
"""

MAX_BBOX_ABS_TOLERANCE_MM: float = 1e-2
"""境界箱の絶対許容差の上限（mm）。

10μm を超えると造形公差（積層 0.2mm 級）と紛れ、形が変わったのか丸めたのかが
読めなくなる。
"""

TOLERANCE_HEADROOM: float = 100.0
"""寸法変更による乖離が許容差の何倍以上あれば「捕まえられる」と言えるか。

⚠️ **倍率で書くのは、許容差を締めても緩めても検査が意味を保つためである。**
実測はこれをはるかに上回る（下の各テストの docstring を参照）。
"""


# ---------------------------------------------------------------------------
# 変更する寸法（⚠️ **値はリテラルで書かず、出荷ファイルから読んで加工する**）
# ---------------------------------------------------------------------------

CHANGED_DIMENSION: tuple[str, str] = ("battery", "tray_wall_thickness_mm")
"""「寸法を1つ変える」で動かす寸法（バッテリトレイの肉厚）。

⚠️ **形が変わり、かつ設定として成立し続ける寸法を選ぶ。** 肉厚を 0.1mm 厚くすると
トレイの体積と外形、および配線ガイドが動く一方、材料・造形可能寸法・床との隙間・
接合部の検査はすべて通ったままである——⚠️ **構築が拒否されると「照合が失敗した」
のか「形が作れなかった」のかが混ざる**。
"""

DIMENSION_STEP_MM: float = 0.1
"""上の寸法へ加える変化量（mm）。

⚠️ **0.1mm は積層ピッチ（0.2mm 級）より小さい。** 記録がこの幅を捕まえられるなら、
造形に現れる差はすべて捕まえられる。
"""

SPLIT_DIMENSION: tuple[str, str] = ("board", "can_clearance_mm")
"""部品の在／不在を動かす寸法（基板デッキと缶側壁の隙間）。

半分にすると基板デッキの外径が上流の造形可能寸法を超え、`required_segment_count`
が 1 から 3 へ上がる。⚠️ **分割数は設定値ではなく導出である**（要件 2.1）ため、
寸法1つで部品名の集合そのものが動く。
"""


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
"""リポジトリルート（子プロセスの作業ディレクトリ）。"""

_STUB_MESSAGE: str = (
    "test_chassis_geometry_regression: build123d を意図的に遮断している"
)
"""形状ライブラリの遮断スタブ固有のメッセージ。

⚠️ `"ImportError" in stderr` だけでは「そもそも導入されていない」場合と区別
できない。**この文字列が出ること**まで見て、初めて遮断が効いたと言える。
"""


def _shipped_baseline() -> GeometryBaseline:
    """出荷されている記録を読む（既定パス）。"""
    return load_baseline(DEFAULT_BASELINE_PATH)


def _pair(dimensions: Path | None = None) -> tuple[ResolvedParams, ChassisLayout]:
    """寸法と幾何の対を、⚠️ **入口と同じ1関数**から得る（本モジュール docstring）。"""
    argv = ["check"]
    if dimensions is not None:
        argv += ["--dimensions", str(dimensions)]
    args = cli_module.build_parser().parse_args(argv)
    build_pair = getattr(cli_module, cli_module.PARAMS_AND_LAYOUT_FUNCTION)
    return build_pair(args)


def _regenerate(dimensions: Path | None = None) -> dict[str, PartMetrics]:
    """形状を再生成し、部品名から指標への対応表を返す。

    ⚠️ **指標は `BuiltPart.metrics` から取る。** 書き出したファイルを読み直して
    測り直さない——3MF はバイト列が安定せず、STL は三角形分割の誤差を持つ
    （3.7 の申し送り）。

    ⚠️ **形状ライブラリを要する。** 呼び出し側は `@requires_cad` を付けること。
    """
    from chassis_mechanism.shapes import build_parts

    params, layout = _pair(dimensions)
    return {part.name: part.metrics for part in build_parts(params, layout)}


def _dimensions_with(
    tmp_path: Path, component: str, key: str, value: object
) -> Path:
    """出荷の寸法設定の写しへ、1項目だけ違う値を書いた設定ファイルを作る。

    ⚠️ **出荷ファイルは読むだけである。** 改変は必ず写しに対して行う。
    """
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    document[component][key] = value
    path = tmp_path / "dimensions.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _shipped_value(component: str, key: str) -> float:
    """出荷の寸法設定から1項目を読む（⚠️ **本ファイルへ値を書き写さない**）。"""
    document = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    value = document[component][key]
    assert isinstance(value, (int, float)), f"{component}.{key} が数値でない"
    return float(value)


@pytest.fixture(scope="module")
def shipped_metrics() -> Mapping[str, PartMetrics]:
    """出荷の寸法から再生成した指標（⚠️ 構築は重いのでモジュール内で1回だけ）。"""
    return _regenerate()


@pytest.fixture(scope="module")
def changed_dimensions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """寸法を1つだけ変えた設定ファイル（バッテリトレイの肉厚 +0.1mm）。"""
    component, key = CHANGED_DIMENSION
    return _dimensions_with(
        tmp_path_factory.mktemp("changed"),
        component,
        key,
        _shipped_value(component, key) + DIMENSION_STEP_MM,
    )


@pytest.fixture(scope="module")
def changed_metrics(changed_dimensions: Path) -> Mapping[str, PartMetrics]:
    """寸法を1つ変えた状態から再生成した指標。"""
    return _regenerate(changed_dimensions)


@pytest.fixture(scope="module")
def split_dimensions(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """基板デッキが分割される寸法（缶側壁との隙間を半分にした設定ファイル）。"""
    component, key = SPLIT_DIMENSION
    return _dimensions_with(
        tmp_path_factory.mktemp("split"),
        component,
        key,
        _shipped_value(component, key) / 2.0,
    )


@pytest.fixture(scope="module")
def split_metrics(split_dimensions: Path) -> Mapping[str, PartMetrics]:
    """部品名の集合が変わった状態から再生成した指標。"""
    return _regenerate(split_dimensions)


# ---------------------------------------------------------------------------
# 1. 出荷された記録そのものの健全性（⚠️ 形状ライブラリを要さない）
# ---------------------------------------------------------------------------


def test_the_shipped_record_exists_at_the_declared_default_path() -> None:
    """既定パスに記録が出荷されている（タスク 4.2 の成果物）。

    ⚠️ `baseline.DEFAULT_BASELINE_PATH` はタスク 2.5 / 4.1 の時点では「宣言された
    場所」にすぎず、`check` は「記録が無い」で終了コード 2 になっていた
    （`test_chassis_cli.py::test_check_reports_a_missing_baseline_record_with_its_path`）。
    本タスクが実体を置いたことをここで主張する。
    """
    assert DEFAULT_BASELINE_PATH.is_file(), (
        f"{DEFAULT_BASELINE_PATH} が無い。"
        "`python -m chassis_mechanism build --update-baseline` で作成すること。"
    )


def test_the_shipped_record_loads_and_declares_the_current_schema_version() -> None:
    """記録は上流 `load_baseline` の全検証を通り、現在の記録形式の版を名乗る。"""
    baseline = _shipped_baseline()

    assert baseline.schema_version == SCHEMA_VERSION
    assert baseline.generator_version.strip()


def test_the_shipped_record_covers_every_part_the_current_parameters_name() -> None:
    """記録の部品名は、現在の寸法から導かれる全部品と過不足なく一致する。

    ⚠️ **期待する部品名は `shapes.part_names` から取る**——本ファイルへ
    `hub_plate` … と書き写すと、部品名の正が2箇所になる。⚠️ **分割される部品を
    1点だけ記録して済ませない**: `motor_arm` は3本、`adapter_segment` と
    `catch_deck` は導出された分割数ぶんあり、記録はその全部を持たなければ
    ならない（持たなければ、消えた断片が照合をすり抜ける）。

    ⚠️ **本検査は形状ライブラリを要さない**——`part_names` は分割数の導出
    （`joints.segment_counts`）だけを見ており、形を作らない。
    """
    expected = part_names(load_params())

    assert len(expected) > 1, "分割される部品が1つも無いと本検査が退化する"
    assert set(_shipped_baseline().parts) == set(expected)


def test_the_shipped_record_declares_tolerances_within_the_usable_band() -> None:
    """許容差が「版差に耐える」と「実形状の差を捕まえる」の帯の中にある。

    ⚠️ **下限だけでも上限だけでも足りない。** 前者を欠けば版が上がるだけで落ち、
    後者を欠けば何を壊しても緑のままになる。⚠️ **この検査は帯の内側にあることしか
    言えない**——実際の寸法変更を捕まえられることは
    `test_the_recorded_tolerances_are_tight_enough_to_catch_one_changed_dimension`
    が実測で示す。
    """
    baseline = _shipped_baseline()

    assert MIN_VOLUME_REL_TOLERANCE <= baseline.volume_rel_tolerance
    assert baseline.volume_rel_tolerance <= MAX_VOLUME_REL_TOLERANCE
    assert 0.0 < baseline.bbox_abs_tolerance_mm <= MAX_BBOX_ABS_TOLERANCE_MM


def test_the_shipped_record_matches_the_current_dimensions_digest() -> None:
    """記録の識別子が現在の `dimensions.json` と一致する（要件 1.12 の正常側）。

    ⚠️ **本検査は形状ライブラリを要さない。** `baseline.verify_digest` は2つの
    文字列を突き合わせるだけであり、CAD 非導入の環境でも「寸法を変えたまま記録を
    更新していない」状態を検出できる（タスク 4.3 の土台）。
    """
    verify_digest(_shipped_baseline(), load_params().chassis)


def test_the_shipped_record_is_written_with_lf_only() -> None:
    """記録は LF のみで書かれ、末尾に改行を持つ（`.gitattributes` の `text eol=lf`）。

    ⚠️ CR が混ざると、WSL 側の `dump_baseline`（常に LF）が書き戻すたびに
    `git status` が空の差分を報告し続ける。
    """
    raw = DEFAULT_BASELINE_PATH.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_rewriting_the_shipped_record_reproduces_it_byte_for_byte(tmp_path: Path) -> None:
    """読んで書き直すと同じバイト列になる（整形が記録側と一致している）。

    ⚠️ これが崩れると `build --update-baseline` を実行するたびに、値が1つも
    変わっていないのに差分が出る。
    """
    written = tmp_path / DEFAULT_BASELINE_PATH.name
    dump_baseline(_shipped_baseline(), written)

    assert written.read_bytes() == DEFAULT_BASELINE_PATH.read_bytes()


def test_digest_only_check_succeeds_against_the_shipped_record_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """⚠️ **記録を出荷しても、形状ライブラリ非導入の環境は壊れない**（4.1 の完了状態）。

    タスク 4.1 は「非導入の環境で識別子照合が完走する」を完了状態に掲げたが、
    その時点では既定の記録が無く、完走の中身は「記録が無いと報せて終了コード 2」で
    あった。⚠️ **記録を置いた本タスクで、既定の `check --digest-only` は 0 になる**
    ——ここが 0 にならなければ、記録の出荷は非 CAD 環境を壊したことになる。

    ⚠️ **スタブが効いていることを先に確かめる**（本リポジトリの `.venv` には
    形状ライブラリが導入済みであり、遮断できていなければ本検査は何も証明しない）。
    遮断技法は `test_chassis_cli.py` / `test_chassis_baseline.py` と同一である。
    """
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir()
    (stub_dir / "build123d.py").write_text(
        f"raise ImportError({_STUB_MESSAGE!r})\n", encoding="utf-8"
    )
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")

    blocked = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import build123d"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=180.0,
        check=False,
    )
    assert blocked.returncode != 0, blocked.stdout
    assert _STUB_MESSAGE in blocked.stderr, (
        "遮断したつもりの実行が、スタブ以外の理由で失敗している"
    )

    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "chassis_mechanism", "check", "--digest-only"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=300.0,
        check=False,
    )

    assert completed.returncode == cli_module.EXIT_OK, completed.stderr
    assert str(DEFAULT_BASELINE_PATH) in completed.stdout


def test_the_skip_marker_states_why_the_shape_tests_are_skipped() -> None:
    """形状を要する検査の skip には**理由**が付いている。

    ⚠️ 理由の無い skip は、形状ライブラリが無いのか検査が壊れているのかを
    区別できなくする。
    """
    reason = requires_cad.kwargs.get("reason")

    assert isinstance(reason, str) and reason.strip()
    assert "build123d" in reason


# ---------------------------------------------------------------------------
# 2. 記録 == 再生成（⚠️ 形状ライブラリを要する）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_shipped_record_agrees_with_the_regenerated_shape(
    shipped_metrics: Mapping[str, PartMetrics],
) -> None:
    """出荷された記録が現在の実装からの再生成と一致する（タスク 4.2 の完了状態・前半）。

    ⚠️ **これが本タスクの中心の主張である。** 記録は「寸法から計算した数」ではなく、
    実際に構築したソリッドから抽出した指標である（`BuiltPart.metrics`）。
    """
    assert compare_metrics(_shipped_baseline(), shipped_metrics) == ()


@requires_cad
def test_check_succeeds_against_the_shipped_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既定（出荷ファイル）の `check` が正常終了する（要件 1.12）。

    ⚠️ タスク 4.1 の時点では、記録が無いため既定の `check` は終了コード 2 で
    失敗していた。記録を出荷した本タスクで 0 に変わる。
    """
    assert cli_module.main(["check"]) == cli_module.EXIT_OK

    out = capsys.readouterr().out
    assert "パラメータ識別子は現在の寸法と一致する" in out
    assert f"{len(part_names(load_params()))} 部品の形状指標が記録と一致する" in out


# ---------------------------------------------------------------------------
# 3. 寸法を1つ変えると失敗する（⚠️ **記録の数字を手でずらすのではない**）
# ---------------------------------------------------------------------------


@requires_cad
def test_changing_one_dimension_makes_the_comparison_fail(
    changed_metrics: Mapping[str, PartMetrics],
) -> None:
    """寸法を1つ変えて作り直すと、記録との照合が部品名つきで失敗する（完了状態・後半）。

    ⚠️ **記録は一切いじっていない。** 変えたのは `dimensions.json` の写しの1項目
    （バッテリトレイの肉厚 +0.1mm）だけであり、形は実際に作り直している。
    """
    mismatches = compare_metrics(_shipped_baseline(), changed_metrics)

    assert mismatches, "寸法を1つ変えたのに照合が1件も不一致を報せない"
    # ⚠️ 在／不在ではなく**乖離**として出る（部品名の集合は変わっていない）。
    assert all(mismatch.field_name != PRESENCE_FIELD for mismatch in mismatches)

    fields = {(mismatch.part_name, mismatch.field_name) for mismatch in mismatches}
    tray = {field for part, field in fields if part == "battery_tray"}
    assert "volume_mm3" in tray, f"体積の乖離が出ていない: {sorted(fields)}"
    assert any(field.startswith("bbox_mm[") for field in tray), (
        f"外接箱の乖離が出ていない: {sorted(fields)}"
    )

    for mismatch in mismatches:
        assert mismatch.recorded != mismatch.regenerated


@requires_cad
def test_the_recorded_tolerances_are_tight_enough_to_catch_one_changed_dimension(
    shipped_metrics: Mapping[str, PartMetrics],
    changed_metrics: Mapping[str, PartMetrics],
) -> None:
    """⚠️ **許容差が寸法変更を飲み込まないことを実測で確かめる。**

    許容差が広ければ、照合は「常に一致する」——記録は置いてあるだけの飾りになり、
    形が変わっても誰も気付かない。0.1mm の肉厚変更で生じる乖離は、実測で

    - 体積: 相対 3.9e-2（記録の許容差 1e-6 の **約 39,000 倍**）
    - 外接箱: 0.3mm（記録の許容差 1e-3mm の **300 倍**）

    である。⚠️ **数値をここへ書き写さず、毎回測って倍率で主張する**——許容差を
    締めても緩めても、この検査は意味を保つ。

    参考（下限が必要な理由）: 上の寸法変更に**関係しない** `board_deck` の体積は
    相対 1e-15 級で動く。求積は浮動小数の演算列であり、⚠️ **許容差 0 は同一環境
    でも危うい**。
    """
    baseline = _shipped_baseline()
    common = sorted(set(shipped_metrics) & set(changed_metrics))
    assert common

    volume_ratio = max(
        abs(changed_metrics[name].volume_mm3 - shipped_metrics[name].volume_mm3)
        / shipped_metrics[name].volume_mm3
        for name in common
    )
    bbox_gap_mm = max(
        abs(regenerated - recorded)
        for name in common
        for recorded, regenerated in zip(
            shipped_metrics[name].bbox_mm, changed_metrics[name].bbox_mm
        )
    )

    assert volume_ratio > baseline.volume_rel_tolerance * TOLERANCE_HEADROOM, (
        f"体積の許容差 {baseline.volume_rel_tolerance!r} が広すぎる: "
        f"寸法 {DIMENSION_STEP_MM}mm の変更で生じる相対差は {volume_ratio!r} しかない"
    )
    assert bbox_gap_mm > baseline.bbox_abs_tolerance_mm * TOLERANCE_HEADROOM, (
        f"外接箱の許容差 {baseline.bbox_abs_tolerance_mm!r}mm が広すぎる: "
        f"寸法 {DIMENSION_STEP_MM}mm の変更で生じる差は {bbox_gap_mm!r}mm しかない"
    )


@requires_cad
def test_check_fails_when_one_dimension_changed(
    changed_dimensions: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """入口からも、寸法を1つ変えると `check` が終了コード 1 で落ちる（要件 1.12）。

    ⚠️ **識別子の関門を先に通しておく。** 寸法を変えれば `parameters_digest` も
    動くため、素直に `check --dimensions <変更後>` を実行すると
    `baseline.verify_digest` が先に落ち、⚠️ **指標の照合まで到達しない**——
    それはタスク 4.3 が固定する別の失敗である。ここで見たいのは
    「**形が変わったことを指標の照合が捕まえる**」ことなので、記録の写しの
    識別子だけを変更後のものへ揃え、⚠️ **指標は出荷のまま**にして照合させる。

    ⚠️ **出荷ファイルそのものを書き換えない。** 写しを `tmp_path` へ置いて
    `--baseline` で指す。
    """
    document = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    changed_params, _ = _pair(changed_dimensions)
    document["parameters_digest"] = parameters_digest(changed_params.chassis)
    realigned = tmp_path / DEFAULT_BASELINE_PATH.name
    realigned.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    assert (
        cli_module.main(
            [
                "check",
                "--dimensions",
                str(changed_dimensions),
                "--baseline",
                str(realigned),
            ]
        )
        == cli_module.EXIT_MISMATCH
    )

    err = capsys.readouterr().err
    assert "不一致がある" in err
    deviations = [line for line in err.splitlines() if cli_module.DEVIATION_LABEL in line]
    assert deviations, err
    assert any("battery_tray" in line for line in deviations), err


# ---------------------------------------------------------------------------
# 4. 在／不在の符号化（タスク 4.2 の3項目め）
# ---------------------------------------------------------------------------


@requires_cad
def test_a_dimension_change_that_splits_a_part_is_reported_as_presence(
    shipped_metrics: Mapping[str, PartMetrics],
    split_metrics: Mapping[str, PartMetrics],
) -> None:
    """寸法1つで部品が消え／増えると、在／不在の符号として現れる（要件 1.12）。

    缶側壁との隙間を半分にすると基板デッキの外径が造形可能寸法を超え、分割されない
    `board_deck` が消えて連番の断片が現れる。⚠️ **記録から行を削って作った不一致
    ではない**——分割数は導出であり（要件 2.1）、寸法が部品名の集合を動かす。

    ⚠️ **不在の側について体積や境界箱の行をでっち上げない**——比較が成立しない
    項目の不一致は作らず、`PRESENCE_FIELD` の1件だけを出す。
    """
    vanished = set(shipped_metrics) - set(split_metrics)
    appeared = set(split_metrics) - set(shipped_metrics)
    assert vanished, "寸法を変えても消えた部品が無い（本検査が退化している）"
    assert appeared, "寸法を変えても増えた部品が無い（本検査が退化している）"

    mismatches = compare_metrics(_shipped_baseline(), split_metrics)
    presence = {
        mismatch.part_name: mismatch
        for mismatch in mismatches
        if mismatch.field_name == PRESENCE_FIELD
    }
    assert set(presence) == vanished | appeared

    for name in vanished:
        assert presence[name].recorded == PRESENT, name
        assert presence[name].regenerated == ABSENT, name
    for name in appeared:
        assert presence[name].recorded == ABSENT, name
        assert presence[name].regenerated == PRESENT, name

    # ⚠️ 片側にしか無い部品について、体積・境界箱の不一致は作られていない。
    for mismatch in mismatches:
        if mismatch.part_name in presence:
            assert mismatch.field_name == PRESENCE_FIELD


# ---------------------------------------------------------------------------
# 5. 記録は入口から再現できる（設定ファイルとしての性質）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_entry_point_reproduces_the_shipped_record_byte_for_byte(
    tmp_path: Path,
) -> None:
    """出荷の記録は `build --update-baseline` が今書くものと**バイト単位で同一**である。

    ⚠️ **記録は手で編集する対象ではない**（design.md「Domain Model」: 導出結果を
    手で編集しない）。写しに対して入口を走らせ、同じバイト列が出ることで「記録が
    入口から再現できる」ことを主張する。⚠️ **写しから始めるのは許容差の引き継ぎも
    同時に通すためである**——空の場所へ書けば `cli` の既定値が入り、将来許容差を
    見直したときに本検査が黙って既定値へ戻す経路を許してしまう。

    ⚠️ 生成物は `tmp_path` へ出す（`var/cad/chassis/` を触らない）。
    """
    regenerated = tmp_path / DEFAULT_BASELINE_PATH.name
    regenerated.write_bytes(DEFAULT_BASELINE_PATH.read_bytes())

    assert (
        cli_module.main(
            [
                "build",
                "--output-dir",
                str(tmp_path / "cad"),
                "--update-baseline",
                "--baseline",
                str(regenerated),
            ]
        )
        == cli_module.EXIT_OK
    )

    assert regenerated.read_bytes() == DEFAULT_BASELINE_PATH.read_bytes()


def test_the_default_baseline_argument_points_at_the_shipped_record() -> None:
    """`--baseline` の既定が、本ファイルが検査している出荷ファイルそのものである。

    ⚠️ **これが崩れると上の検査群は永遠に別のファイルを見ることになる。**
    """
    parser = cli_module.build_parser()

    assert parser.parse_args(["check"]).baseline == DEFAULT_BASELINE_PATH
    assert parser.parse_args(["build"]).baseline == DEFAULT_BASELINE_PATH


def test_the_record_is_not_a_hand_written_parameter_file() -> None:
    """記録の識別子は寸法から作られ、⚠️ **記録自身の中身からは作られない**。

    出荷の記録が持つ識別子は `config.parameters_digest(load_params().chassis)` と
    一致する。⚠️ この関係が崩れた記録（識別子を手で書いた記録）は、寸法を変えても
    「一致している」と言い続ける。
    """
    params: ChassisParams = load_params().chassis

    assert _shipped_baseline().parameters_digest == parameters_digest(params)
