"""値オブジェクトと共通の判定結果の形の検証（タスク 1.3 / 要件 1.8, 3.3, 4.4, 5.9, 6.9, 9.1）。

観測可能な完了状態（tasks.md 1.3）を固定する:

- 全型が**値等価**である（frozen かつ slots の dataclass）
- **サンプル列と由来情報列の長さ一致**が不変条件として固定される

あわせて design.md「M1Types」が定める次の点も固定する:

- 真値は「値・求め方・不確かさ・測り方の記述」を必ず伴い、**測り方の記述を
  空にできない**（要件 4.4）
- 判定結果は「問い・規則の説明文・判定値・根拠・証跡・暫定フラグ」を必ず伴い、
  **規則の説明文を空にできない**（要件 9.1）
- `prediction_core` の型を**再定義せず公開入口から参照する**

ファイル名に `m1_` を冠しているのは `tests/prediction_core/test_types.py` と
衝突するためである（tasks.md「Implementation Notes」タスク1.1）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import prediction_core
from m1_validation import types as m1_types
from m1_validation.errors import M1ConfigError
from m1_validation.types import (
    M1_EXTRA_VERSION,
    Attribution,
    Judgement,
    Oq27Verdict,
    SampleProvenance,
    SampleReject,
    ThrowSamples,
    TruthMethod,
    TruthValue,
)
from prediction_core import Sample

#: `types` が import してよい先（design.md「Dependency Direction」: errors → types）。
#: `prediction_core` は層に属さない共通語彙として参照してよい。
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "enum",
    "typing",
    "m1_validation",
    "prediction_core",
}


def _make_provenance(**overrides: object) -> SampleProvenance:
    base: dict[str, object] = {
        "frame_index": 12,
        "frame_seq": 3012,
        "valid_depth_px": 41,
        "depth_spread_mm": 18.5,
        "apparent_diameter_px": 9.2,
        "expected_diameter_px": 8.8,
        "rivals": 0,
        "gap_before": 0,
        "camera_ray_unit": (0.0, 0.0, 1.0),
    }
    base.update(overrides)
    return SampleProvenance(**base)  # type: ignore[arg-type]


def _make_truth(**overrides: object) -> TruthValue:
    base: dict[str, object] = {
        "value": (1200.0, 300.0, 0.0),
        "method": TruthMethod.MEASURED,
        "uncertainty_mm": 10.0,
        "uncertainty_ms": None,
        "source": "メジャーで床面のマークから実測（±10mm）",
    }
    base.update(overrides)
    return TruthValue(**base)  # type: ignore[arg-type]


def _make_judgement(**overrides: object) -> Judgement:
    base: dict[str, object] = {
        "question": "OQ-27",
        "criterion": "予測レイテンシの中央値が総飛行時間の 1/4 を超えないこと",
        "verdict": Oq27Verdict.CONTINUE,
        "rationale": "中央値 82ms に対し総飛行時間の 1/4 は 200ms である",
        "evidence": {"latency_p50_ms": 82.0, "flight_time_ms": 800.0},
        "provisional": False,
    }
    base.update(overrides)
    return Judgement(**base)  # type: ignore[arg-type]


def _imports_of(module: object) -> list[tuple[str, int]]:
    """モジュールレベル import を `(先頭名, 相対階層)` で返す。"""
    source = Path(inspect.getfile(module)).read_text(encoding="utf-8")  # type: ignore[arg-type]
    found: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.extend((alias.name.split(".")[0], 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module.split(".")[0] if node.module else "", node.level))
    return found


ALL_VALUE_TYPES = (SampleProvenance, ThrowSamples, TruthValue, Judgement)


class TestValueSemantics:
    """全型が値等価であり、不変である（tasks.md 1.3 の完了状態）。"""

    @pytest.mark.parametrize("cls", ALL_VALUE_TYPES)
    def test_is_a_frozen_slotted_dataclass(self, cls: type) -> None:
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
        # slots=True の dataclass はインスタンス辞書を持たない
        # （フィールドの綴り間違いが静かに通らない）。
        assert not hasattr(cls, "__dict__") or "__slots__" in cls.__dict__

    def test_same_fields_compare_equal(self) -> None:
        assert _make_provenance() == _make_provenance()
        assert _make_truth() == _make_truth()
        assert _make_judgement() == _make_judgement()

    def test_differing_fields_compare_unequal(self) -> None:
        assert _make_provenance() != _make_provenance(rivals=2)
        assert _make_truth() != _make_truth(method=TruthMethod.INTERPOLATED)
        assert _make_judgement() != _make_judgement(provisional=True)

    def test_fields_cannot_be_reassigned(self) -> None:
        provenance = _make_provenance()
        with pytest.raises(dataclasses.FrozenInstanceError):
            provenance.rivals = 5  # type: ignore[misc]

    def test_unknown_field_is_rejected(self) -> None:
        """綴り間違いが静かに通らない（slots の効き目）。"""
        with pytest.raises(TypeError):
            SampleProvenance(typo_field=1)  # type: ignore[call-arg]


class TestTruthValue:
    """真値は値だけでなく求め方と不確かさを必ず伴う（要件 4.4）。"""

    def test_carries_value_method_uncertainty_and_source(self) -> None:
        truth = _make_truth()
        assert truth.value == (1200.0, 300.0, 0.0)
        assert truth.method == "measured"
        assert truth.uncertainty_mm == 10.0
        assert truth.source

    @pytest.mark.parametrize("empty", ["", "   ", "\t\n"])
    def test_source_cannot_be_empty(self, empty: str) -> None:
        """測り方の記述を空にできない（tasks.md 1.3）。

        空白だけの文字列も拒否する。「とりあえず空文字を埋めておく」と
        **どう測ったか分からない真値**が生まれ、後から不確かさを評価できない
        ——真値の値だけが独り歩きするのが、この形が防ごうとしている事故である。
        """
        with pytest.raises(M1ConfigError):
            _make_truth(source=empty)

    def test_rejection_says_which_field_was_empty(self) -> None:
        with pytest.raises(M1ConfigError) as exc:
            _make_truth(source="")
        assert exc.value.context.get("field") == "source"

    def test_missing_truth_is_representable(self) -> None:
        """欠測は「値が無い」ことを表せる（要件 4.6）。それでも source は要る。"""
        truth = _make_truth(
            value=None,
            method=TruthMethod.MISSING,
            uncertainty_mm=None,
            source="床面を跨ぐ区間が観測されなかった",
        )
        assert truth.value is None
        assert truth.method == "missing"

    def test_method_compares_as_a_plain_string(self) -> None:
        assert {member.name: member.value for member in TruthMethod} == {
            "MEASURED": "measured",
            "INTERPOLATED": "interpolated",
            "EXTRAPOLATED": "extrapolated",
            "EXTERNAL_MARK": "external_mark",
            "MISSING": "missing",
        }


class TestJudgement:
    """すべての判断が載る共通の形（要件 5.9, 6.9, 9.1）。"""

    def test_carries_question_criterion_verdict_rationale_evidence_and_flag(self) -> None:
        judgement = _make_judgement()
        assert judgement.question == "OQ-27"
        assert judgement.criterion
        assert judgement.verdict == "continue"
        assert judgement.rationale
        assert judgement.evidence == {"latency_p50_ms": 82.0, "flight_time_ms": 800.0}
        assert judgement.provisional is False

    @pytest.mark.parametrize("empty", ["", "   "])
    def test_criterion_cannot_be_empty(self, empty: str) -> None:
        """規則の説明文を空にできない（要件 9.1）。

        判定値だけが残って「どの基準でそう判断したか」が失われると、
        あとから規則を都合よく読み替えられる。実測前に固定した規則を
        **判定結果と同じ場所に**置くことが要件 9.1 の要求である。
        """
        with pytest.raises(M1ConfigError):
            _make_judgement(criterion=empty)

    def test_evidence_is_copied_so_later_mutation_does_not_change_it(self) -> None:
        """渡した辞書をあとから書き換えても、判断が持つ証跡は変わらない。

        判断は「その時点の根拠でそう決めた」という記録である。呼び出し側が
        使い回す辞書を抱えると、レポートの証跡が判断と食い違い得る。
        """
        mutable = {"latency_p50_ms": 82.0}
        judgement = _make_judgement(evidence=mutable)
        mutable["latency_p50_ms"] = 999.0

        assert judgement.evidence == {"latency_p50_ms": 82.0}

    def test_provisional_marks_results_that_must_not_drive_a_decision(self) -> None:
        """試行数下限未達などは暫定として印を付ける（要件 5.10）。"""
        assert _make_judgement(provisional=True).provisional is True


class TestThrowSamples:
    """継ぎ目の出力（要件 1.1 / 1.8）。"""

    def test_samples_and_provenance_have_the_same_length(self) -> None:
        """長さ一致が本型の不変条件である（design.md「M1Types」Preconditions）。"""
        throw = ThrowSamples(
            samples=(Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=1500.0),),
            provenance=(_make_provenance(),),
            rejected=((SampleReject.BELOW_FLOOR, 2),),
            handoff_version="1.0",
            calibration_id="cal-0001",
            verification_state="verified",
            verified=True,
        )
        assert len(throw.samples) == len(throw.provenance)

    def test_construction_does_not_validate_the_length(self) -> None:
        """**構築時には検証しない**（design.md「M1Types」Validation）。

        上流3 Spec と同じ「生成時に検証しない。検証は各サービスの境界で行う」
        方針をそのまま採る。ここでその判断を明示的に固定しておくのは、
        **長さ検査がどこにも無いことを見落とさないため**である
        ——検査の持ち主は継ぎ目（`seam.py`、タスク 2.2）であり、本型ではない。
        """
        mismatched = ThrowSamples(
            samples=(Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=1500.0),),
            provenance=(),
            rejected=(),
            handoff_version="1.0",
            calibration_id="cal-0001",
            verification_state="verified",
            verified=True,
        )
        assert len(mismatched.samples) != len(mismatched.provenance)

    def test_pairing_by_index_fails_loudly_when_lengths_disagree(self) -> None:
        """添字で対応させる設計の壊れ方を明示する（design.md「M1Types」Risks）。

        `zip(..., strict=True)` で辿れば食い違いはその場で例外になる。
        黙って短い方に切り詰めると、**別の観測点の品質情報がサンプルに
        付いたまま集計へ流れる**。
        """
        samples = (Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=1500.0),)
        with pytest.raises(ValueError):
            list(zip(samples, (), strict=True))

    def test_unverified_calibration_is_representable(self) -> None:
        """未検証のまま実行した場合の印（要件 2.2）。"""
        throw = ThrowSamples(
            samples=(),
            provenance=(),
            rejected=(),
            handoff_version="1.0",
            calibration_id="cal-0002",
            verification_state="not_verified",
            verified=False,
        )
        assert throw.verified is False

    def test_rejected_counts_are_paired_with_their_reason(self) -> None:
        """除外理由ごとの件数を残す（要件 1.7）。"""
        throw = ThrowSamples(
            samples=(),
            provenance=(),
            rejected=((SampleReject.NOT_FINITE, 3), (SampleReject.BELOW_FLOOR, 1)),
            handoff_version="1.0",
            calibration_id="cal-0003",
            verification_state="verified",
            verified=True,
        )
        assert dict(throw.rejected) == {"not_finite": 3, "below_floor": 1}


class TestSampleProvenance:
    """Sample に入れられない観測品質（要件 1.8 / 6.3）。"""

    def test_carries_the_quality_fields_the_requirement_names(self) -> None:
        provenance = _make_provenance()
        # 要件 1.8: 有効画素数・奥行きのばらつき・競合候補数・フレーム欠落の有無
        assert provenance.valid_depth_px == 41
        assert provenance.depth_spread_mm == 18.5
        assert provenance.rivals == 0
        assert provenance.gap_before == 0

    def test_carries_the_camera_ray_direction_in_world_frame(self) -> None:
        """帰属が向きを使うため、World 系のカメラ視線方向を持つ（要件 6.3）。"""
        assert _make_provenance().camera_ray_unit == (0.0, 0.0, 1.0)

    def test_dimensional_fields_name_their_unit(self) -> None:
        """距離 mm・画素 px を**フィールド名に含める**（tasks.md 1.3）。"""
        names = {field.name for field in dataclasses.fields(SampleProvenance)}
        assert {"depth_spread_mm", "valid_depth_px", "apparent_diameter_px"} <= names


class TestEnumerations:
    def test_sample_reject_values(self) -> None:
        assert {member.name: member.value for member in SampleReject} == {
            "NOT_FINITE": "not_finite",
            "BELOW_FLOOR": "below_floor",
            "DEPTH_SPREAD_TOO_LARGE": "depth_spread_too_large",
            "INSUFFICIENT_VALID_PIXELS": "insufficient_valid_pixels",
        }

    def test_attribution_values(self) -> None:
        assert {member.name: member.value for member in Attribution} == {
            "CALIBRATION": "calibration",
            "DETECTION": "detection",
            "PREDICTION": "prediction",
            "OBSERVATION_NOISE": "observation_noise",
            "NONE": "none",
            "UNDETERMINED": "undetermined",
        }

    def test_attribution_can_report_undetermined(self) -> None:
        """「判別不能」は正常な結果である（要件 6.10）。"""
        assert Attribution.UNDETERMINED == "undetermined"

    def test_oq27_verdict_values(self) -> None:
        assert {member.name: member.value for member in Oq27Verdict} == {
            "CONTINUE": "continue",
            "CONTINUE_WITH_CONSTRAINTS": "continue_with_constraints",
            "INSUFFICIENT": "insufficient",
            "DEFERRED": "deferred",
        }


class TestExtraVersion:
    def test_extra_version_is_declared(self) -> None:
        """`ThrowRecord.extra["m1"]` の形の版（要件 3.4）。"""
        assert M1_EXTRA_VERSION == "1.0"


class TestUpstreamTypesAreNotRedefined:
    """`prediction_core` の型は公開入口から参照し、再定義しない（tasks.md 1.3）。"""

    def test_sample_used_here_is_the_upstream_class_itself(self) -> None:
        throw = ThrowSamples(
            samples=(Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=1500.0),),
            provenance=(_make_provenance(),),
            rejected=(),
            handoff_version="1.0",
            calibration_id="cal-0001",
            verification_state="verified",
            verified=True,
        )
        assert type(throw.samples[0]) is prediction_core.Sample

    def test_module_defines_no_class_shadowing_an_upstream_name(self) -> None:
        """`Sample` / `Prediction` / `SourceKind` を自前で定義していない。"""
        source = Path(inspect.getfile(m1_types)).read_text(encoding="utf-8")
        defined = {
            node.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ClassDef)
        }
        assert defined.isdisjoint({"Sample", "Prediction", "SourceKind", "ThrowRecord"})

    def test_upstream_types_come_from_the_public_entry_point(self) -> None:
        """内部モジュール（`prediction_core.types`）へ直接届かない（要件 13.1）。"""
        modules = {
            node.module
            for node in ast.walk(ast.parse(Path(inspect.getfile(m1_types)).read_text("utf-8")))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(
            module.startswith("prediction_core.") for module in modules
        ), sorted(modules)


class TestLayerOne:
    """`types` は `errors` と共通語彙のみに依存する（design.md「Dependency Direction」）。"""

    def test_module_imports_only_allowed_roots(self) -> None:
        roots = {root for root, level in _imports_of(m1_types) if level == 0}
        assert roots <= ALLOWED_IMPORT_ROOTS, sorted(roots - ALLOWED_IMPORT_ROOTS)

    def test_module_has_no_relative_imports(self) -> None:
        assert [root for root, level in _imports_of(m1_types) if level > 0] == []

    def test_module_does_not_import_layers_above_itself(self) -> None:
        """`layout` / `config` 以降を import しない（層が循環する）。"""
        source = Path(inspect.getfile(m1_types)).read_text(encoding="utf-8")
        modules = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        forbidden = {
            "m1_validation.layout",
            "m1_validation.config",
            "m1_validation.upstream",
            "m1_validation.seam",
        }
        assert modules.isdisjoint(forbidden), sorted(modules & forbidden)
