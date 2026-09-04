"""形状指標の型・記録・照合の検査（要件 4.1, 4.2, 4.4, 8.7、タスク 2.4）。

⚠️ **本ファイルは形状ライブラリ（build123d / OCP）を一切必要としない。**
`catch_mechanism.metrics` が素の数値だけを扱うことの実証そのものであり、
CAD 非導入の環境で全件が通る（design.md「Metrics」Responsibilities の
「⚠️ **これにより照合ロジックが CAD 非導入環境でもテストできる**」/ 要件 5.7）。

⚠️ **出荷される `configs/catch_mechanism/geometry-baseline.json` に依存しない。**
記録の初期化はタスク 4.2（`_Depends: 4.1_`、形状ライブラリ導入環境を要する）の
担当であり、本ファイルが実在の記録ファイルを読んだり、その値を期待値として
書き込んだりすると、4.2 が自力で記録を作れなくなる。読み書きの検査は
`tmp_path` の一時ファイルだけで行い、既定パスについては**「宣言された場所」と
「ファイルが無いときに大きな声で失敗すること」**のみを固定する。
⚠️ したがってタスク 4.2 が記録を出荷しても本ファイルは影響を受けない
（実在するともしないとも主張していない）。

**ファイル名について**: design.md「Directory Structure」は
`tests/catch_mechanism/test_metrics.py` を挙げるが、その名前は
`tests/sensing_foundation/test_metrics.py` が既に使っている。`tests/` 配下に
`__init__.py` が無く、テストモジュール名がフラットな名前空間を共有するため、
同名ファイルは収集時に衝突する（tasks.md「Implementation Notes」タスク 1.1）。
本ディレクトリの既存ファイルと同じく `test_catch_` 接頭辞を付けて回避する。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest

from catch_mechanism import metrics as metrics_module
from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import CatchMechanismError, ConsistencyError, ParameterError
from catch_mechanism.metrics import (
    ABSENT,
    DEFAULT_BASELINE_PATH,
    MM3_PER_CM3,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    MetricsMismatch,
    PartMetrics,
    compare_metrics,
    estimate_mass_g,
    load_baseline,
    verify_baseline_digest,
    write_baseline,
)
from catch_mechanism.params import PrintingConstraints

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_DIGEST = "sha256:" + "0" * 64
"""識別子の**書式**だけを満たす値。⚠️ 実在のパラメータの識別子ではない。

`config.parameters_digest` の戻り値と同じ形（`sha256:` + 64桁の16進）であり、
記録の書式検査を通すためだけに用いる。⚠️ **実在の `dimensions.json` の識別子を
読みに行かない。** 「識別子の突き合わせ」節は `verify_baseline_digest` の契約
（何を返し、何を拒み、失敗時に何を示すか）だけを固定し、出荷ファイルの中身には
一切触れない——「寸法設定ファイルを変更したまま記録を更新していない」という
**状況**の再現はタスク 4.3 の担当である。
"""


def make_part(
    name: str = "rim_segment",
    *,
    volume_mm3: float = 1000.0,
    bbox_mm: tuple[float, float, float] = (10.0, 20.0, 30.0),
    solid_count: int = 1,
) -> PartMetrics:
    """検査用の `PartMetrics` を組み立てる。"""
    return PartMetrics(
        part_name=name, volume_mm3=volume_mm3, bbox_mm=bbox_mm, solid_count=solid_count
    )


def make_baseline(
    *parts: PartMetrics,
    volume_rel_tolerance: float = 0.01,
    bbox_abs_tolerance_mm: float = 0.1,
    parameters_digest: str = VALID_DIGEST,
    generator_version: str = "build123d 0.0.0-test",
) -> GeometryBaseline:
    """検査用の `GeometryBaseline` を組み立てる（既定は単一部品）。"""
    members = parts if parts else (make_part(),)
    return GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest=parameters_digest,
        volume_rel_tolerance=volume_rel_tolerance,
        bbox_abs_tolerance_mm=bbox_abs_tolerance_mm,
        generator_version=generator_version,
        parts={part.part_name: part for part in members},
    )


def as_measured(*parts: PartMetrics) -> dict[str, PartMetrics]:
    """再生成側の対応表（部品名 → 指標）を組み立てる。"""
    return {part.part_name: part for part in parts}


# --------------------------------------------------------------------------
# 指標の型（要件 4.1）
# --------------------------------------------------------------------------


def test_part_metrics_holds_only_plain_numbers() -> None:
    """`PartMetrics` は素の数値だけを持ち、形状オブジェクトを保持しない。

    design.md「Metrics」Responsibilities の「⚠️ **これにより照合ロジックが CAD
    非導入環境でもテストできる**」を、フィールドの型として固定する。
    `object` 型のフィールド（`shapes.BuiltPart.solid` のような形状の格納先）が
    紛れ込めば落ちる。
    """
    annotations = {field.name: field.type for field in fields(PartMetrics)}
    assert annotations == {
        "part_name": "str",
        "volume_mm3": "float",
        "bbox_mm": "tuple[float, float, float]",
        "solid_count": "int",
    }


def test_part_metrics_is_frozen() -> None:
    """指標は構築後に書き換えられない（照合の途中で値が動かない）。"""
    part = make_part()
    with pytest.raises(FrozenInstanceError):
        part.volume_mm3 = 2.0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("kwargs", "expected_fragment"),
    [
        ({"volume_mm3": 0.0}, "volume_mm3"),
        ({"volume_mm3": -1.0}, "volume_mm3"),
        ({"volume_mm3": float("nan")}, "volume_mm3"),
        ({"bbox_mm": (10.0, 20.0)}, "bbox_mm"),
        ({"bbox_mm": (10.0, 20.0, 30.0, 40.0)}, "bbox_mm"),
        ({"bbox_mm": [10.0, 20.0, 30.0]}, "bbox_mm"),
        ({"bbox_mm": (10.0, -20.0, 30.0)}, "bbox_mm"),
        ({"solid_count": 0}, "solid_count"),
        ({"solid_count": True}, "solid_count"),
        ({"solid_count": 1.0}, "solid_count"),
    ],
)
def test_part_metrics_rejects_impossible_values(
    kwargs: dict[str, object], expected_fragment: str
) -> None:
    """成立しない指標は構築できず、拒否は**項目名**を示す（要件 1.3 と同じ規律）。"""
    with pytest.raises(ParameterError) as excinfo:
        make_part(**kwargs)  # type: ignore[arg-type]
    assert expected_fragment in str(excinfo.value)


def test_part_metrics_rejects_empty_name() -> None:
    """部品名の無い指標は不一致の報告先を持たない（要件 4.4）。"""
    with pytest.raises(ParameterError) as excinfo:
        make_part("   ")
    assert "part_name" in str(excinfo.value)


# --------------------------------------------------------------------------
# 記録の型（要件 4.2）
# --------------------------------------------------------------------------


def test_baseline_carries_digest_tolerances_and_generator_version() -> None:
    """記録は識別子・双方の許容差・生成ライブラリ版を持つ（要件 4.2）。"""
    baseline = make_baseline()
    assert baseline.schema_version == SCHEMA_VERSION
    assert baseline.parameters_digest == VALID_DIGEST
    assert baseline.volume_rel_tolerance == 0.01
    assert baseline.bbox_abs_tolerance_mm == 0.1
    assert baseline.generator_version == "build123d 0.0.0-test"
    assert set(baseline.parts) == {"rim_segment"}


def test_baseline_rejects_digest_that_is_not_in_the_config_format() -> None:
    """識別子は `config.parameters_digest` と同じ書式でなければならない。

    ⚠️ 現在の設定ファイルの識別子と**一致するか**の検査はタスク 4.3 の担当で
    あり、ここで見るのは書式だけである。書式さえ検査しなければ、記録の側が
    「識別子らしくない文字列」を持ったまま 4.3 の比較へ流れ込む。
    """
    for bad in ("", "deadbeef", "md5:" + "0" * 32, "sha256:" + "0" * 63, "sha256:" + "Z" * 64):
        with pytest.raises(ParameterError) as excinfo:
            make_baseline(parameters_digest=bad)
        assert "parameters_digest" in str(excinfo.value)


def test_baseline_rejects_unsupported_schema_version() -> None:
    """記録形式の版は `config.SCHEMA_VERSION` を共有する（タスク 1.4(b)）。"""
    with pytest.raises(ParameterError) as excinfo:
        GeometryBaseline(
            schema_version="0.9",
            parameters_digest=VALID_DIGEST,
            volume_rel_tolerance=0.01,
            bbox_abs_tolerance_mm=0.1,
            generator_version="build123d 0.0.0-test",
            parts={"rim_segment": make_part()},
        )
    assert "schema_version" in str(excinfo.value)


@pytest.mark.parametrize("field_name", ["volume_rel_tolerance", "bbox_abs_tolerance_mm"])
def test_baseline_rejects_negative_tolerance(field_name: str) -> None:
    """許容差は非負でなければならない（負の許容差は何も一致させない）。"""
    with pytest.raises(ParameterError) as excinfo:
        make_baseline(**{field_name: -0.01})  # type: ignore[arg-type]
    assert field_name in str(excinfo.value)


def test_baseline_rejects_empty_parts() -> None:
    """部品を1件も持たない記録は、何と照合しても必ず一致してしまう。"""
    with pytest.raises(ParameterError) as excinfo:
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest=VALID_DIGEST,
            volume_rel_tolerance=0.01,
            bbox_abs_tolerance_mm=0.1,
            generator_version="build123d 0.0.0-test",
            parts={},
        )
    assert "parts" in str(excinfo.value)


def test_baseline_rejects_key_that_disagrees_with_part_name() -> None:
    """対応表のキーと `part_name` の食い違いを許さない。

    食い違ったままだと、不一致の報告が「記録のどこにも無い部品名」を名乗る。
    """
    with pytest.raises(ParameterError) as excinfo:
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest=VALID_DIGEST,
            volume_rel_tolerance=0.01,
            bbox_abs_tolerance_mm=0.1,
            generator_version="build123d 0.0.0-test",
            parts={"other_name": make_part("rim_segment")},
        )
    message = str(excinfo.value)
    assert "other_name" in message
    assert "rim_segment" in message


def test_baseline_cuts_the_alias_to_the_caller_mapping() -> None:
    """構築後に呼び出し側の辞書を書き換えても記録は動かない。"""
    parts = {"rim_segment": make_part()}
    baseline = GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest=VALID_DIGEST,
        volume_rel_tolerance=0.01,
        bbox_abs_tolerance_mm=0.1,
        generator_version="build123d 0.0.0-test",
        parts=parts,
    )
    parts["injected"] = make_part("injected")
    assert set(baseline.parts) == {"rim_segment"}


# --------------------------------------------------------------------------
# 照合（要件 4.4 / タスク 2.4 の観測可能な完了状態）
# --------------------------------------------------------------------------


def test_compare_returns_empty_tuple_when_everything_agrees() -> None:
    """一致時は空の一覧が返る（design.md「Metrics」Postconditions）。"""
    baseline = make_baseline()
    assert compare_metrics(baseline, as_measured(make_part())) == ()


def test_compare_reports_part_name_field_name_and_both_values_for_volume() -> None:
    """記録された指標を人為的にずらすと、部品名と双方の値を伴う不一致が返る。"""
    baseline = make_baseline(make_part(volume_mm3=1000.0))
    mismatches = compare_metrics(baseline, as_measured(make_part(volume_mm3=1200.0)))
    assert mismatches == (
        MetricsMismatch(
            part_name="rim_segment",
            field_name="volume_mm3",
            recorded=1000.0,
            regenerated=1200.0,
        ),
    )


def test_volume_tolerance_is_relative_to_the_recorded_value() -> None:
    """体積の許容差は**相対**である。"""
    baseline = make_baseline(make_part(volume_mm3=1000.0), volume_rel_tolerance=0.01)
    assert compare_metrics(baseline, as_measured(make_part(volume_mm3=1009.0))) == ()
    assert compare_metrics(baseline, as_measured(make_part(volume_mm3=1011.0))) != ()

    # 相対であることの実証: 記録値を 100 倍すれば同じ絶対差が許容へ収まる。
    larger = make_baseline(make_part(volume_mm3=100_000.0), volume_rel_tolerance=0.01)
    assert compare_metrics(larger, as_measured(make_part(volume_mm3=100_011.0))) == ()


def test_tolerance_boundary_is_inclusive() -> None:
    """許容差ちょうどの差は一致として扱う（境界は許容側に含む）。

    ⚠️ 許容差・値ともに2進で厳密に表せる数を選んでいる（0.5 と 1000.0 / 20.0）。
    0.1 のような数で境界を突くと、丸めの向きが検査対象になってしまう。
    """
    baseline = make_baseline(
        make_part(volume_mm3=1000.0, bbox_mm=(10.0, 20.0, 30.0)),
        volume_rel_tolerance=0.5,
        bbox_abs_tolerance_mm=0.5,
    )
    on_boundary = make_part(volume_mm3=1500.0, bbox_mm=(10.5, 19.5, 30.0))
    assert compare_metrics(baseline, as_measured(on_boundary)) == ()

    beyond = make_part(volume_mm3=1501.0, bbox_mm=(10.75, 20.0, 30.0))
    assert [item.field_name for item in compare_metrics(baseline, as_measured(beyond))] == [
        "volume_mm3",
        "bbox_mm[0]",
    ]


def test_bbox_tolerance_is_absolute_and_reported_per_axis() -> None:
    """境界箱の許容差は**絶対**であり、不一致は軸ごとに項目名を持つ。"""
    baseline = make_baseline(make_part(bbox_mm=(10.0, 20.0, 30.0)), bbox_abs_tolerance_mm=0.1)
    assert compare_metrics(baseline, as_measured(make_part(bbox_mm=(10.05, 20.05, 29.95)))) == ()

    mismatches = compare_metrics(baseline, as_measured(make_part(bbox_mm=(10.0, 20.5, 30.75))))
    assert mismatches == (
        MetricsMismatch(
            part_name="rim_segment", field_name="bbox_mm[1]", recorded=20.0, regenerated=20.5
        ),
        MetricsMismatch(
            part_name="rim_segment", field_name="bbox_mm[2]", recorded=30.0, regenerated=30.75
        ),
    )

    # 絶対であることの実証: 記録値が大きくなっても許容量は増えない。
    larger = make_baseline(make_part(bbox_mm=(1000.0, 20.0, 30.0)), bbox_abs_tolerance_mm=0.1)
    assert compare_metrics(larger, as_measured(make_part(bbox_mm=(1000.5, 20.0, 30.0)))) != ()


def test_solid_count_must_agree_exactly() -> None:
    """立体数に許容差は無い（1個の部品が2個に割れていれば不一致である）。"""
    baseline = make_baseline(make_part(solid_count=1))
    mismatches = compare_metrics(baseline, as_measured(make_part(solid_count=2)))
    assert mismatches == (
        MetricsMismatch(
            part_name="rim_segment", field_name="solid_count", recorded=1.0, regenerated=2.0
        ),
    )


def test_part_recorded_but_not_regenerated_is_a_mismatch() -> None:
    """記録にあって再生成に無い部品を不一致として報告する（Invariants）。

    ⚠️ 欠けた部品には「双方の数値」が存在しないため、`MetricsMismatch` の
    数値2つを**在／不在の 1.0 / 0.0** として使う。項目名は `PRESENCE_FIELD`。
    """
    baseline = make_baseline(make_part("rim_segment"), make_part("bracket"))
    mismatches = compare_metrics(baseline, as_measured(make_part("rim_segment")))
    assert mismatches == (
        MetricsMismatch(
            part_name="bracket",
            field_name=PRESENCE_FIELD,
            recorded=PRESENT,
            regenerated=ABSENT,
        ),
    )
    assert (PRESENT, ABSENT) == (1.0, 0.0)


def test_part_regenerated_but_not_recorded_is_a_mismatch() -> None:
    """再生成にあって記録に無い部品も不一致として報告する（Invariants の「その逆」）。"""
    baseline = make_baseline(make_part("rim_segment"))
    mismatches = compare_metrics(
        baseline, as_measured(make_part("rim_segment"), make_part("bracket"))
    )
    assert mismatches == (
        MetricsMismatch(
            part_name="bracket",
            field_name=PRESENCE_FIELD,
            recorded=ABSENT,
            regenerated=PRESENT,
        ),
    )


def test_absent_part_does_not_report_fabricated_numeric_fields() -> None:
    """不在の部品について、体積や境界箱の不一致を**でっち上げない**。

    「体積 1000 が 0 になった」と報告すると、実際には測っていない 0 が
    測定値であるかのように読める。不在は不在としてのみ報告する。
    """
    baseline = make_baseline(make_part("bracket", volume_mm3=1000.0))
    mismatches = compare_metrics(baseline, as_measured())
    assert [item.field_name for item in mismatches] == [PRESENCE_FIELD]


def test_mismatches_are_ordered_by_part_name_then_field_order() -> None:
    """不一致の並びは決定的である（部品名の昇順 → 項目の宣言順）。"""
    baseline = make_baseline(
        make_part("b_part", volume_mm3=1000.0, bbox_mm=(10.0, 20.0, 30.0), solid_count=1),
        make_part("a_part", volume_mm3=1000.0, bbox_mm=(10.0, 20.0, 30.0), solid_count=1),
    )
    measured = as_measured(
        make_part("b_part", volume_mm3=2000.0, bbox_mm=(11.0, 20.0, 30.0), solid_count=3),
        make_part("a_part", volume_mm3=2000.0, bbox_mm=(10.0, 20.0, 30.0), solid_count=1),
    )
    mismatches = compare_metrics(baseline, measured)
    assert [(item.part_name, item.field_name) for item in mismatches] == [
        ("a_part", "volume_mm3"),
        ("b_part", "volume_mm3"),
        ("b_part", "bbox_mm[0]"),
        ("b_part", "solid_count"),
    ]


def test_compare_does_not_raise_on_mismatch() -> None:
    """不一致は**値**であり例外ではない（`errors.py` docstring の区分）。

    最初の不一致で打ち切る実装へ滑れば、要件 4.4 の「部品名と乖離した指標の
    双方の値を示す」が一覧として成立しなくなる。
    """
    baseline = make_baseline(make_part(volume_mm3=1000.0))
    mismatches = compare_metrics(baseline, as_measured(make_part(volume_mm3=9999.0)))
    assert len(mismatches) == 1
    assert not isinstance(mismatches[0], BaseException)
    assert not issubclass(MetricsMismatch, BaseException)
    assert not issubclass(MetricsMismatch, CatchMechanismError)


def test_compare_rejects_measured_mapping_whose_key_disagrees_with_part_name() -> None:
    """再生成側の対応表もキーと部品名の食い違いを許さない。"""
    baseline = make_baseline()
    with pytest.raises(ParameterError) as excinfo:
        compare_metrics(baseline, {"typo_name": make_part("rim_segment")})
    assert "typo_name" in str(excinfo.value)


def test_compare_rejects_non_metrics_values() -> None:
    """再生成側に指標でない値が混ざっていれば呼び出し方の誤りとして拒否する。"""
    baseline = make_baseline()
    with pytest.raises(ParameterError):
        compare_metrics(baseline, {"rim_segment": "1000"})  # type: ignore[dict-item]


# --------------------------------------------------------------------------
# 記録ファイルの読み書き（要件 4.2）
# --------------------------------------------------------------------------


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    """書き出した記録を読み戻すと同じ値になる。"""
    baseline = make_baseline(
        make_part("rim_segment", volume_mm3=1234.5, bbox_mm=(1.5, 2.5, 3.5), solid_count=2),
        make_part("bracket", volume_mm3=99.0, bbox_mm=(4.0, 5.0, 6.0), solid_count=1),
        volume_rel_tolerance=0.02,
        bbox_abs_tolerance_mm=0.25,
        generator_version="build123d 0.9.1",
    )
    path = tmp_path / "geometry-baseline.json"
    write_baseline(baseline, path)
    assert load_baseline(path) == baseline


def test_written_file_uses_lf_sorted_keys_indent_two_and_trailing_newline(
    tmp_path: Path,
) -> None:
    """整形は `config.dump_params` / `tolerance.dump_derivation` に揃える。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(), path)
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    document = json.loads(text)
    assert text == json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_written_document_matches_the_declared_record_shape(tmp_path: Path) -> None:
    """記録ファイルの項目は design.md「Data Models」の表どおりである。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(
        make_baseline(make_part("rim_segment", volume_mm3=12.5, bbox_mm=(1.0, 2.0, 3.0))),
        path,
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "parameters_digest",
        "volume_rel_tolerance",
        "bbox_abs_tolerance_mm",
        "generator_version",
        "parts",
    }
    assert document["parts"] == {
        "rim_segment": {"volume_mm3": 12.5, "bbox_mm": [1.0, 2.0, 3.0], "solid_count": 1}
    }


def test_bbox_is_written_in_axis_order(tmp_path: Path) -> None:
    """境界箱は X, Y, Z の並びで書き出す（キー整列は配列の中身に効かない）。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(make_part(bbox_mm=(3.0, 1.0, 2.0))), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["parts"]["rim_segment"]["bbox_mm"] == [3.0, 1.0, 2.0]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda doc: doc.update(extra=1), id="unknown-top-level-key"),
        pytest.param(lambda doc: doc.pop("generator_version"), id="missing-top-level-key"),
        pytest.param(
            lambda doc: doc["parts"]["rim_segment"].update(extra=1), id="unknown-part-key"
        ),
        pytest.param(
            lambda doc: doc["parts"]["rim_segment"].pop("solid_count"), id="missing-part-key"
        ),
    ],
)
def test_load_rejects_unknown_and_missing_keys_at_every_level(
    tmp_path: Path, mutate: Callable[[dict], object]
) -> None:
    """未知キー・欠損キーをあらゆる階層で拒否する（`config.py` の規律）。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(path)
    assert str(path) in str(excinfo.value)


def test_load_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    """未対応の記録形式の版は拒否する。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = "99.0"
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(path)
    assert "schema_version" in str(excinfo.value)


def test_load_rejects_tampered_metrics_values(tmp_path: Path) -> None:
    """読み戻しは書式の復元ではなく検査である（成立しない指標は読めない）。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["parts"]["rim_segment"]["volume_mm3"] = -1.0
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(path)
    assert "volume_mm3" in str(excinfo.value)


def test_load_rejects_bbox_that_is_not_three_numbers(tmp_path: Path) -> None:
    """境界箱は3つの数の配列でなければならない。"""
    path = tmp_path / "geometry-baseline.json"
    write_baseline(make_baseline(), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["parts"]["rim_segment"]["bbox_mm"] = [1.0, 2.0]
    path.write_text(json.dumps(document), encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(path)
    assert "bbox_mm" in str(excinfo.value)


def test_load_rejects_broken_json(tmp_path: Path) -> None:
    """JSON として解析できない記録は項目名ではなくファイル名を示して拒否する。"""
    path = tmp_path / "geometry-baseline.json"
    path.write_text("{", encoding="utf-8", newline="\n")
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(path)
    assert str(path) in str(excinfo.value)


# --------------------------------------------------------------------------
# 識別子の突き合わせ（要件 4.5、タスク 4.2 が `cli` から移設）
#
# ⚠️ **形状を再生成しない検査である。** 本ファイルは形状ライブラリを一切
# 必要としないため、この節が通ること自体が「CAD 非導入環境でも識別子の不整合を
# 検出できる」ことの実証になっている（tasks.md「Implementation Notes」
# タスク 4.1(b)）。⚠️ 「寸法設定ファイルを変更したまま記録を更新していない」
# という**状況**の再現はタスク 4.3 の担当であり、ここで固定するのは関数の
# 契約（何を返し、何を拒み、失敗時に何を示すか）だけである。
# --------------------------------------------------------------------------


def test_verify_baseline_digest_returns_the_current_digest_when_they_agree() -> None:
    """識別子が一致すれば、現在の識別子をそのまま返す（呼び出し側の出力用）。"""
    baseline = make_baseline()

    assert (
        verify_baseline_digest(
            baseline,
            VALID_DIGEST,
            baseline_path=Path("baseline.json"),
            dimensions_path=Path("dimensions.json"),
        )
        == VALID_DIGEST
    )


def test_verify_baseline_digest_names_both_values_and_both_sources() -> None:
    """食い違うときは双方の値と双方の参照元を示して `ConsistencyError` になる。

    ⚠️ 片方の値しか出さない失敗は直しようがない——どちらが古いのかが読めない。
    """
    current = "sha256:" + "1" * 64
    baseline = make_baseline(parameters_digest=VALID_DIGEST)
    baseline_path = Path("configs") / "geometry-baseline.json"
    dimensions_path = Path("configs") / "dimensions.json"

    with pytest.raises(ConsistencyError) as excinfo:
        verify_baseline_digest(
            baseline,
            current,
            baseline_path=baseline_path,
            dimensions_path=dimensions_path,
        )

    message = str(excinfo.value)
    assert VALID_DIGEST in message
    assert current in message
    assert str(baseline_path) in message
    assert str(dimensions_path) in message


def test_verify_baseline_digest_is_a_consistency_failure_not_a_parameter_error() -> None:
    """不整合は「入力の誤り」ではない（終了コードが 2 と 1 で分かれる）。

    ⚠️ `errors.py` の5系統は互いに素であり、`ConsistencyError` を
    `ParameterError` として捕まえられてはならない。
    """
    baseline = make_baseline(parameters_digest=VALID_DIGEST)

    with pytest.raises(CatchMechanismError) as excinfo:
        verify_baseline_digest(
            baseline,
            "sha256:" + "1" * 64,
            baseline_path=Path("baseline.json"),
            dimensions_path=Path("dimensions.json"),
        )

    assert isinstance(excinfo.value, ConsistencyError)
    assert not isinstance(excinfo.value, ParameterError)


@pytest.mark.parametrize(
    "current",
    ["", "sha256:zz", "0" * 64, "sha512:" + "0" * 64, "SHA256:" + "0" * 64],
)
def test_verify_baseline_digest_rejects_a_malformed_current_digest(current: str) -> None:
    """現在の識別子が書式を満たさないのは**呼び出し方の誤り**である。

    ⚠️ 書式の壊れた文字列を「一致しない」として `ConsistencyError` にすると、
    記録が古いのか呼び出しが壊れているのかが区別できなくなる（`compare_metrics`
    が対応表の壊れた `measured` を `ParameterError` で拒むのと同じ規律）。
    """
    baseline = make_baseline()

    with pytest.raises(ParameterError) as excinfo:
        verify_baseline_digest(
            baseline,
            current,
            baseline_path=Path("baseline.json"),
            dimensions_path=Path("dimensions.json"),
        )

    assert "current_digest" in str(excinfo.value)


# --------------------------------------------------------------------------
# 既定パス（タスク 4.2 が記録を出荷する前も後も成立する検査）
# --------------------------------------------------------------------------


def test_default_baseline_path_is_the_declared_location() -> None:
    """既定パスは design.md「Data Models」が宣言する場所を指す。

    ⚠️ ファイルの**存在**は主張しない。記録の初期化はタスク 4.2 の担当である。
    """
    assert DEFAULT_BASELINE_PATH == (
        REPO_ROOT / "configs" / "catch_mechanism" / "geometry-baseline.json"
    )


def test_load_without_path_reads_the_default_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引数なしの `load_baseline()` は既定パスを読む。

    ⚠️ 既定パスを一時ファイルへ差し替えて検査する。出荷される記録の**中身**に
    依存しないため、タスク 4.2 が記録を作っても壊れない。
    """
    path = tmp_path / "geometry-baseline.json"
    baseline = make_baseline(make_part(volume_mm3=42.0))
    write_baseline(baseline, path)
    monkeypatch.setattr(metrics_module, "DEFAULT_BASELINE_PATH", path)
    assert load_baseline() == baseline


def test_load_without_path_fails_loudly_when_the_record_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録が無いとき、空の記録を黙って返さずパスを示して失敗する。

    空の記録を返す実装だと、照合は「部品0件」で必ず成功し、
    「記録を作り忘れた」状態が緑のまま流れる。
    """
    absent = tmp_path / "does-not-exist.json"
    monkeypatch.setattr(metrics_module, "DEFAULT_BASELINE_PATH", absent)
    with pytest.raises(ParameterError) as excinfo:
        load_baseline()
    assert str(absent) in str(excinfo.value)


def test_load_with_absent_path_fails_loudly(tmp_path: Path) -> None:
    """明示したパスが無いときも同じ（ファイル名を示す `ParameterError`）。"""
    absent = tmp_path / "does-not-exist.json"
    with pytest.raises(ParameterError) as excinfo:
        load_baseline(absent)
    assert str(absent) in str(excinfo.value)


# --------------------------------------------------------------------------
# 質量の目安（要件 8.7）
# --------------------------------------------------------------------------


def test_estimate_mass_converts_mm3_to_cm3() -> None:
    """1000 mm^3 の PETG（1.27 g/cm^3）は 1.27 g である。

    ⚠️ 1 cm^3 = 1000 mm^3。換算を落とせば 1270 g、逆向きに掛ければ
    0.00127 g になる——いずれも「目安」として使い物にならない桁である。
    """
    assert estimate_mass_g(1000.0, 1.27) == pytest.approx(1.27)
    assert MM3_PER_CM3 == 1000.0


def test_estimate_mass_is_not_off_by_a_factor_of_1000() -> None:
    """桁の取り違えを直接固定する。"""
    mass_g = estimate_mass_g(1000.0, 1.27)
    assert mass_g != pytest.approx(1270.0)
    assert mass_g != pytest.approx(0.00127)


def test_estimate_mass_scales_linearly_with_volume_and_density() -> None:
    """体積にも密度にも線形である。"""
    assert estimate_mass_g(2000.0, 1.27) == pytest.approx(2.54)
    assert estimate_mass_g(1000.0, 2.54) == pytest.approx(2.54)


def test_estimate_mass_uses_the_material_density_parameter() -> None:
    """密度は `PrintingConstraints.material_density_g_cm3` から来る（要件 8.7）。"""
    printing = PrintingConstraints(
        build_x_mm=180.0,
        build_y_mm=180.0,
        build_z_mm=180.0,
        material="PETG",
        material_density_g_cm3=1.27,
        segment_margin_mm=5.0,
    )
    part = make_part(volume_mm3=100_000.0)
    assert estimate_mass_g(part.volume_mm3, printing.material_density_g_cm3) == pytest.approx(
        127.0
    )


@pytest.mark.parametrize(
    ("volume_mm3", "density_g_cm3", "expected_fragment"),
    [
        (0.0, 1.27, "volume_mm3"),
        (-1.0, 1.27, "volume_mm3"),
        (float("inf"), 1.27, "volume_mm3"),
        (1000.0, 0.0, "density_g_cm3"),
        (1000.0, -1.27, "density_g_cm3"),
        (1000.0, float("nan"), "density_g_cm3"),
    ],
)
def test_estimate_mass_rejects_impossible_inputs(
    volume_mm3: float, density_g_cm3: float, expected_fragment: str
) -> None:
    """成立しない入力は項目名を示して拒否する。"""
    with pytest.raises(ParameterError) as excinfo:
        estimate_mass_g(volume_mm3, density_g_cm3)
    assert expected_fragment in str(excinfo.value)


def test_estimate_mass_is_documented_as_a_solid_material_upper_bound() -> None:
    """⚠️ 中身の詰まった立体としての目安であることが docstring に現れている。

    実際の FDM 部品は充填率と外壁で決まり、体積 × 密度より軽い。
    「目安」（要件 8.7）であることを黙示にせず、明示する。
    """
    doc = estimate_mass_g.__doc__ or ""
    assert "目安" in doc
    assert "充填" in doc
