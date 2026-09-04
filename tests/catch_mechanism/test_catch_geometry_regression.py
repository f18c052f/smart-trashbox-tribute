"""出荷する形状指標の記録と、その照合の回帰検査（タスク 4.2 / 要件 3.7, 4.2,
4.3, 4.4）。

design.md「Testing Strategy」の Integration Tests 1（指標照合の失敗経路）と
3（決定性）を担う。⚠️ design.md「Directory Structure」は本ファイルを
`test_geometry_regression.py` と呼ぶが、`tests/` 配下に `__init__.py` が無く
テストモジュール名がフラットな名前空間を共有するため、本ディレクトリの既存
ファイルと同じく `test_catch_` 接頭辞を付ける（tasks.md「Implementation Notes」
タスク 1.1）。

## 本ファイルが固定する2つの半分

タスク 4.2 の観測可能な完了状態は2文からなり、**両方**を固定する。

1. 「形状ライブラリ導入環境で照合が成功し、指標を人為的にずらすと失敗する」
   ——出荷した `configs/catch_mechanism/geometry-baseline.json` が現在の実装と
   パラメータから再生成した指標と一致し、記録をずらすと部品名と双方の値を
   伴って失敗する（`@requires_cad` の系）。
2. 「非導入環境では**理由が明示されたうえで**当該テストのみが実行対象外になる」
   ——⚠️ **モジュール全体を `pytest.importorskip` で落とさない。** 記録そのものの
   健全性（版・部品数・許容差・識別子・改行）の検査は形状ライブラリを要さず、
   非導入環境でも**走らなければならない**（要件 5.7）。skip する理由が読めることは
   `test_the_skip_marker_states_why_the_shape_tests_are_skipped` が固定する。

## ⚠️ 本ファイルは出荷ファイルへ意図的にピン留めしている

`test_catch_cli.py` は既定の記録の有無にも中身にも依存しない（タスク 4.1 が
記録を出荷しなかったため）。**本ファイルはその逆で、出荷ファイルそのものを検査
対象にする**——タスク 4.2 が所有するのは「出荷された記録が現在の実装と整合して
いること」であり、それは `tmp_path` の記録では言えない。

⚠️ **数値のリテラルを書かない。** 体積・境界箱の実測値をテストへ写すと、正当な
設計変更のたびに2箇所（記録と本ファイル）を直すことになり、二重管理になる。
本ファイルが主張するのは**関係**（記録 == 再生成、2回の生成が同値、ずらせば
落ちる）と、記録が満たすべき**性質**（部品数・許容差の下限・改行）だけである。
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from catch_mechanism import cli
from catch_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    load_params,
    parameters_digest,
)
from catch_mechanism.metrics import (
    DEFAULT_BASELINE_PATH,
    GeometryBaseline,
    PartMetrics,
    compare_metrics,
    load_baseline,
    verify_baseline_digest,
    write_baseline,
)
from catch_mechanism.shapes import segment_part_names

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ⚠️ 理由の文字列は `test_the_skip_marker_states_why_the_shape_tests_are_skipped`
# が検査する。「なぜ飛ばしたのか」が読めない skip は、環境の不備と設計の健全性を
# 区別できなくする。
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


MIN_VOLUME_REL_TOLERANCE: float = 1e-7
"""体積の相対許容差の下限（tasks.md「Implementation Notes」タスク 3.3）。

同一ライブラリが同一形状に対して出す求積誤差の実測が相対 **1.3e-8** である。
⚠️ 0 や 1e-9 を記録すると build123d / OCCT の版差で確実に破綻する。
"""

MAX_VOLUME_REL_TOLERANCE: float = 1e-4
"""体積の相対許容差の上限。

⚠️ 下限だけを検査すると「1 を記録して常に緑」を通してしまう。相対 1e-4 は
体積 36,500mm^3 に対し 3.65mm^3 であり、求積誤差（9.1e-4mm^3）の約 4000 倍・
締結座1個ぶんの差（約 184mm^3）の 1/50 である——実形状の差は必ずはみ出す。
"""

MAX_BBOX_ABS_TOLERANCE_MM: float = 1e-2
"""境界箱の絶対許容差の上限（mm）。

境界箱は解析値と厳密一致する（タスク 3.3(c)）ため許容差は本来 0 でよいが、
版差の余地として 10μm までを認める。⚠️ これを超えると造形公差
（積層 0.2mm 級）と紛れ、形状が変わったのか丸めたのかが読めなくなる。
"""


def _shipped_baseline() -> GeometryBaseline:
    """出荷されている記録を読む（既定パス）。"""
    return load_baseline()


def _regenerate() -> dict[str, PartMetrics]:
    """現在の実装とパラメータから形状を再生成し、指標だけを取り出す。

    ⚠️ **形状ライブラリを要する。** 呼び出し側は `@requires_cad` を付けること。
    """
    from catch_mechanism.shapes import build_parts

    return {part.name: part.metrics for part in build_parts(load_params())}


def _skewed(baseline: GeometryBaseline, part_name: str, **overrides: object) -> GeometryBaseline:
    """記録の1部品だけを人為的にずらした複製を返す。"""
    part = dataclasses.replace(baseline.parts[part_name], **overrides)
    return dataclasses.replace(baseline, parts={**baseline.parts, part_name: part})


# ---------------------------------------------------------------------------
# 1. 出荷された記録そのものの健全性（形状ライブラリを要さない。要件 4.2, 5.7）
# ---------------------------------------------------------------------------


def test_the_shipped_record_exists_at_the_declared_default_path() -> None:
    """既定パスに記録が出荷されている（タスク 4.2 の成果物）。

    ⚠️ `metrics.DEFAULT_BASELINE_PATH` はタスク 2.4 の時点では「宣言された場所」に
    すぎなかった。本タスクが実体を置いたことをここで主張する。
    """
    assert DEFAULT_BASELINE_PATH.is_file(), (
        f"{DEFAULT_BASELINE_PATH} が無い。"
        "`python -m catch_mechanism build --update-baseline` で作成すること。"
    )


def test_the_shipped_record_loads_and_declares_the_current_schema_version() -> None:
    """記録は `load_baseline` の全検証を通り、現在の記録形式の版を名乗る。"""
    baseline = _shipped_baseline()

    assert baseline.schema_version == SCHEMA_VERSION
    assert baseline.generator_version.strip()


def test_the_shipped_record_covers_every_segment() -> None:
    """記録の部品は、導出された分割数ぶんの全セグメントである（要件 4.2）。

    ⚠️ **1個を5回刷るのではない。** 締結座はリング全体で `retrofit_fastener_count`
    箇所であり、出荷値（6箇所 / 5分割）では配分が `[1,1,2,1,1]` となってセグメントは
    同一形状にならない（tasks.md「Implementation Notes」タスク 3.2(b)）。記録は
    5部品ぶんの行を持たなければならない。

    ⚠️ 期待する部品名は `shapes.segment_part_names` から取る——本ファイルへ
    `rim_segment_1` … と書き写すと、部品名の正が2箇所になる。
    """
    baseline = _shipped_baseline()
    expected = segment_part_names(load_params())

    assert len(expected) > 1, "分割数が1では本検査が退化する（出荷値は5分割）"
    assert set(baseline.parts) == set(expected)


def test_the_shipped_record_declares_tolerances_that_can_still_fail() -> None:
    """許容差が「版差に耐える」と「実形状の差を捕まえる」の両方を満たす。

    ⚠️ 下限（`MIN_VOLUME_REL_TOLERANCE`）だけでも上限
    （`MAX_VOLUME_REL_TOLERANCE`）だけでも足りない。前者を欠けば版が上がるだけで
    落ち、後者を欠けば何を壊しても緑のままになる。
    """
    baseline = _shipped_baseline()

    assert MIN_VOLUME_REL_TOLERANCE <= baseline.volume_rel_tolerance
    assert baseline.volume_rel_tolerance <= MAX_VOLUME_REL_TOLERANCE
    assert 0.0 < baseline.bbox_abs_tolerance_mm <= MAX_BBOX_ABS_TOLERANCE_MM


def test_the_shipped_record_matches_the_current_dimensions_digest() -> None:
    """記録の識別子が現在の `dimensions.json` と一致する（要件 4.5 の正常側）。

    ⚠️ **本検査は形状ライブラリを要さない。** `verify_baseline_digest` は
    `metrics` にあり、CAD へ触れずに「寸法を変えたまま記録を更新していない」状態を
    検出できる（tasks.md「Implementation Notes」タスク 4.1(b)）。非導入環境で
    skip されてはならない。
    """
    current = verify_baseline_digest(
        _shipped_baseline(),
        parameters_digest(load_params()),
        baseline_path=DEFAULT_BASELINE_PATH,
        dimensions_path=DEFAULT_DIMENSIONS_PATH,
    )

    assert current == parameters_digest(load_params())


def test_the_shipped_record_has_no_carriage_returns() -> None:
    """記録は LF のみで書かれている（`.gitattributes` の `text eol=lf`）。

    ⚠️ CR が混ざると、WSL 側の `write_baseline`（常に LF）が書き戻すたびに
    `git status` が空の差分を報告し続ける（tasks.md「Implementation Notes」
    タスク 1.4 → 1.5）。
    """
    raw = DEFAULT_BASELINE_PATH.read_bytes()

    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_rewriting_the_shipped_record_reproduces_it_byte_for_byte(tmp_path: Path) -> None:
    """読んで書き直すと同じバイト列になる（整形が記録側と一致している）。

    ⚠️ これが崩れると `build --update-baseline` を実行するたびに、値が1つも
    変わっていないのに差分が出る。
    """
    written = tmp_path / "geometry-baseline.json"
    write_baseline(_shipped_baseline(), written)

    assert written.read_bytes() == DEFAULT_BASELINE_PATH.read_bytes()


def test_the_skip_marker_states_why_the_shape_tests_are_skipped() -> None:
    """形状を要する検査の skip には**理由**が付いている（タスク 4.2 の完了状態）。

    ⚠️ 理由の無い skip は、形状ライブラリが無いのか検査が壊れているのかを
    区別できなくする。非導入環境で「当該テストのみが実行対象外になる」ことが
    読めるのは、この文字列が出るからである。
    """
    reason = requires_cad.kwargs.get("reason")

    assert isinstance(reason, str) and reason.strip()
    assert "build123d" in reason
    assert "5.7" in reason


# ---------------------------------------------------------------------------
# 2. 再生成との照合（形状ライブラリを要する。要件 4.3, 4.4）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_shipped_record_agrees_with_the_regenerated_shape() -> None:
    """出荷された記録が現在の実装からの再生成と一致する（要件 4.3）。

    ⚠️ **これが本タスクの中心の主張である。** 記録は「その場の導出値へピン留め
    した数」ではなく、実際に構築した形状の指標である。
    """
    baseline = _shipped_baseline()

    assert compare_metrics(baseline, _regenerate()) == ()


@requires_cad
def test_skewing_a_recorded_volume_makes_the_comparison_fail() -> None:
    """記録の体積をずらすと、部品名と双方の値を伴って照合が失敗する（要件 4.4）。

    ずらし幅は相対 1%——記録された許容差（1e-6 級）より4桁大きく、求積誤差
    （相対 2.5e-8）とは紛れない。
    """
    baseline = _shipped_baseline()
    measured = _regenerate()
    target = sorted(baseline.parts)[0]
    original = baseline.parts[target].volume_mm3

    mismatches = compare_metrics(
        _skewed(baseline, target, volume_mm3=original * 1.01), measured
    )

    assert len(mismatches) == 1
    (mismatch,) = mismatches
    assert mismatch.part_name == target
    assert mismatch.field_name == "volume_mm3"
    assert mismatch.recorded == pytest.approx(original * 1.01)
    assert mismatch.regenerated == pytest.approx(original)


@requires_cad
def test_skewing_a_recorded_bounding_box_makes_the_comparison_fail() -> None:
    """境界箱をずらしても同様に失敗する（許容差は絶対値 mm である）。"""
    baseline = _shipped_baseline()
    measured = _regenerate()
    target = sorted(baseline.parts)[0]
    original = baseline.parts[target].bbox_mm

    mismatches = compare_metrics(
        _skewed(baseline, target, bbox_mm=(original[0], original[1] + 1.0, original[2])),
        measured,
    )

    assert len(mismatches) == 1
    (mismatch,) = mismatches
    assert mismatch.part_name == target
    assert mismatch.field_name == "bbox_mm[1]"
    assert mismatch.recorded == pytest.approx(original[1] + 1.0)
    assert mismatch.regenerated == pytest.approx(original[1])


@requires_cad
def test_dropping_a_recorded_part_is_reported_as_a_presence_mismatch() -> None:
    """記録から部品を1つ落とすと、体積の乖離ではなく「増えた」として現れる。

    ⚠️ 不在の側について体積や境界箱の行を**でっち上げない**（tasks.md
    「Implementation Notes」タスク 2.4(b)）。
    """
    baseline = _shipped_baseline()
    measured = _regenerate()
    target = sorted(baseline.parts)[0]
    without = dataclasses.replace(
        baseline,
        parts={name: part for name, part in baseline.parts.items() if name != target},
    )

    mismatches = compare_metrics(without, measured)

    assert len(mismatches) == 1
    (mismatch,) = mismatches
    assert mismatch.part_name == target
    assert mismatch.field_name == "presence"


@requires_cad
def test_generating_twice_yields_identical_metrics() -> None:
    """同一パラメータからの2回生成が同一指標になる（要件 3.7）。

    ⚠️ **許容差ではなく `==` で固定する。** 同一環境の再構築は体積・境界箱まで
    ビット単位で一致する（tasks.md「Implementation Notes」タスク 3.3）。ここで
    許容差を使うと、決定性の破れが「許容差の内側」に隠れる。

    ⚠️ **「部品どうしの値が異なること」は主張しない。** 締結座の配分で
    `rim_segment_3` だけが大きいのは今日の実形状の事実にすぎず、座数が分割数で
    割り切れる値になれば正当に全部品が同値になる。
    """
    first = _regenerate()
    second = _regenerate()

    assert first == second


# ---------------------------------------------------------------------------
# 3. コマンド入口からの通し（要件 4.3, 4.4）
# ---------------------------------------------------------------------------


@requires_cad
def test_check_succeeds_against_the_shipped_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既定パス（出荷ファイル）の `check` が正常終了する。

    ⚠️ タスク 4.1 の時点では、記録が無いため既定パスの `check` は終了コード 2 で
    失敗していた（tasks.md「Implementation Notes」タスク 4.1(c)）。記録を出荷した
    本タスクで 0 に変わる。
    """
    assert cli.main(["check"]) == cli.EXIT_OK

    out = capsys.readouterr().out
    assert "パラメータ識別子は記録と一致する" in out
    assert f"{len(segment_part_names(load_params()))} 部品" in out


@requires_cad
def test_check_fails_and_names_the_part_and_both_values_when_the_record_is_skewed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """出荷ファイルの写しをずらすと `check` が終了コード 1 で失敗する（要件 4.4）。

    ⚠️ **出荷ファイルそのものを書き換えない。** 写しを `tmp_path` へ置いて
    `--baseline` で指す（ずらしたまま検査が落ちれば、後続のテストも巻き添えに
    なる）。
    """
    document = json.loads(DEFAULT_BASELINE_PATH.read_text(encoding="utf-8"))
    target = sorted(document["parts"])[0]
    original = document["parts"][target]["volume_mm3"]
    document["parts"][target]["volume_mm3"] = original * 1.01
    skewed = tmp_path / "geometry-baseline.json"
    skewed.write_text(json.dumps(document), encoding="utf-8")

    assert cli.main(["check", "--baseline", str(skewed)]) == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    assert target in err
    assert repr(original * 1.01) in err
    assert repr(original) in err
