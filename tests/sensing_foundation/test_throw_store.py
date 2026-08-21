"""`sensing_foundation.throw_store` に対するテスト（タスク 5、要件 7.1-7.8）。

観測可能な完了状態（tasks.md 5）を固定する:
- 複数の `ThrowRecord` を追記して読み戻すと、同数・等価なレコードが得られる
  （`iter_records()`、要件 7.3 / 7.4）
- 途中に壊れた行と `schema_version` 違いの行を混ぜても、`iter_with_issues()`
  は他の健全な行を読み続け、それぞれ別種の報告（`kind`）として区別できる
  （要件 7.5 / 7.6）
- `iter_records()`（簡潔な経路）は破損行に到達すると
  `ThrowRecordFormatError` / `ThrowRecordVersionError` を送出して停止する
  （design.md「ThrowRecordStore」の `# 破損行で停止する` コメントの解釈）
- `link_to_session()` はセッション対応付けを `extra["sensing"]` の1キーへ
  収め、既存の他の `extra` キーを破壊しない（要件 7.7）
- `throw_store.py` が `prediction_core` の公開入口のみを import すること
  （内部モジュール不参照。要件 7.8）
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from prediction_core import SCHEMA_VERSION, PredictionConfig, Sample, SourceKind, ThrowRecord
from sensing_foundation.errors import (
    SensingFoundationError,
    ThrowRecordFormatError,
    ThrowRecordVersionError,
)
from sensing_foundation.throw_store import (
    ThrowRecordReadIssue,
    ThrowRecordStore,
    link_to_session,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
THROW_STORE_SOURCE_PATH = REPO_ROOT / "src" / "sensing_foundation" / "throw_store.py"


def _samples() -> tuple[Sample, ...]:
    return (
        Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=500.0),
        Sample(t_ms=50.0, x_mm=12.5, y_mm=-2.5, z_mm=480.0),
    )


def _record(record_id: str, *, extra: dict | None = None) -> ThrowRecord:
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.SIMULATED,
        config=PredictionConfig(),
        samples=_samples(),
        predictions=(),
        extra=extra or {},
    )


class TestAppendAndIterRecordsRoundTrip:
    """追記した内容を `iter_records()` で1件ずつ読み戻すと等価になる（要件 7.3 / 7.4）。"""

    def test_multiple_records_round_trip(self, tmp_path: Path) -> None:
        store = ThrowRecordStore(tmp_path / "throws" / "session-a.ndjson")
        records = [_record(f"throw-{i}") for i in range(5)]

        for record in records:
            store.append(record)

        restored = list(store.iter_records())

        assert len(restored) == len(records)
        assert restored == records

    def test_count_matches_number_of_appended_records(self, tmp_path: Path) -> None:
        store = ThrowRecordStore(tmp_path / "throws.ndjson")
        assert store.count() == 0

        for i in range(3):
            store.append(_record(f"throw-{i}"))

        assert store.count() == 3

    def test_append_creates_parent_directory(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "throws.ndjson"
        store = ThrowRecordStore(path)

        store.append(_record("throw-0"))

        assert path.exists()

    def test_iterating_twice_yields_same_sequence(self, tmp_path: Path) -> None:
        store = ThrowRecordStore(tmp_path / "throws.ndjson")
        store.append(_record("throw-0"))
        store.append(_record("throw-1"))

        first_pass = list(store.iter_records())
        second_pass = list(store.iter_records())

        assert first_pass == second_pass == [_record("throw-0"), _record("throw-1")]


class TestFileFormat:
    """各行が改行を含まない単一の JSON オブジェクトであること（design.md Invariants）。"""

    def test_each_line_is_single_compact_json_object(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        store = ThrowRecordStore(path)
        store.append(_record("throw-0"))
        store.append(_record("throw-1"))

        lines = path.read_text(encoding="utf-8").splitlines()

        assert len(lines) == 2
        for line in lines:
            assert "\n" not in line
            parsed = json.loads(line)
            assert parsed["record_id"] in {"throw-0", "throw-1"}

    def test_line_round_trips_through_throw_record_from_json(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        store = ThrowRecordStore(path)
        record = _record("throw-x")
        store.append(record)

        line = path.read_text(encoding="utf-8").splitlines()[0]

        assert ThrowRecord.from_json(line) == record


class TestIterRecordsStopsAtCorruptedLine:
    """`iter_records()` は破損行に到達すると例外で停止する簡潔な経路である。"""

    def test_malformed_json_line_raises_format_error(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        path.write_text(
            _record("throw-good").to_json(indent=None) + "\n" + "{not valid json" + "\n",
            encoding="utf-8",
        )
        store = ThrowRecordStore(path)

        with pytest.raises(ThrowRecordFormatError):
            list(store.iter_records())

    def test_version_mismatch_line_raises_version_error(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        bad = _record("throw-bad").to_dict()
        bad["schema_version"] = "999.0"
        path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        store = ThrowRecordStore(path)

        with pytest.raises(ThrowRecordVersionError):
            list(store.iter_records())

    def test_version_error_is_a_format_error(self) -> None:
        assert issubclass(ThrowRecordVersionError, ThrowRecordFormatError)

    def test_errors_catchable_as_sensing_foundation_error(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        path.write_text("{not valid json\n", encoding="utf-8")
        store = ThrowRecordStore(path)

        with pytest.raises(SensingFoundationError):
            list(store.iter_records())

    def test_good_lines_before_bad_line_are_not_lost_by_caller(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        path.write_text(
            _record("throw-good-1").to_json(indent=None)
            + "\n"
            + "{not valid json"
            + "\n"
            + _record("throw-good-2").to_json(indent=None)
            + "\n",
            encoding="utf-8",
        )
        store = ThrowRecordStore(path)

        seen: list[ThrowRecord] = []
        with pytest.raises(ThrowRecordFormatError):
            for record in store.iter_records():
                seen.append(record)

        # 破損行より前に読めた行はジェネレータから既に yield 済みである。
        assert seen == [_record("throw-good-1")]


class TestIterWithIssuesContinuesPastBadLines:
    """`iter_with_issues()` は破損行・版違い行を挟んでも他の行を読み続ける（要件 7.5 / 7.6）。"""

    def test_mixed_good_malformed_and_version_mismatch_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        good1 = _record("throw-1")
        good2 = _record("throw-2")

        version_mismatch = good1.to_dict()
        version_mismatch["record_id"] = "throw-mismatch"
        version_mismatch["schema_version"] = "999.0"

        lines = [
            good1.to_json(indent=None),
            "{this is not json",
            json.dumps(version_mismatch),
            good2.to_json(indent=None),
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        store = ThrowRecordStore(path)
        results = list(store.iter_with_issues())

        assert len(results) == 4
        assert results[0] == good1
        assert isinstance(results[1], ThrowRecordReadIssue)
        assert results[1].kind == "malformed_json"
        assert results[1].line_no == 2
        assert isinstance(results[2], ThrowRecordReadIssue)
        assert results[2].kind == "version_mismatch"
        assert results[2].line_no == 3
        assert results[3] == good2

    def test_malformed_and_version_mismatch_are_distinct_kinds(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        version_mismatch = _record("throw-x").to_dict()
        version_mismatch["schema_version"] = "0.1"
        path.write_text(
            "{broken\n" + json.dumps(version_mismatch) + "\n",
            encoding="utf-8",
        )
        store = ThrowRecordStore(path)

        results = list(store.iter_with_issues())

        kinds = [issue.kind for issue in results if isinstance(issue, ThrowRecordReadIssue)]
        assert kinds == ["malformed_json", "version_mismatch"]
        assert len(set(kinds)) == 2

    def test_schema_error_kind_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        # 必須キー（predictions）を欠落させ、RecordSchemaError を誘発する。
        broken_schema = _record("throw-schema-bad").to_dict()
        del broken_schema["predictions"]
        path.write_text(json.dumps(broken_schema) + "\n", encoding="utf-8")
        store = ThrowRecordStore(path)

        results = list(store.iter_with_issues())

        assert len(results) == 1
        assert isinstance(results[0], ThrowRecordReadIssue)
        assert results[0].kind == "schema_error"
        assert results[0].line_no == 1

    def test_all_good_lines_yield_equivalent_records(self, tmp_path: Path) -> None:
        store = ThrowRecordStore(tmp_path / "throws.ndjson")
        records = [_record(f"throw-{i}") for i in range(3)]
        for record in records:
            store.append(record)

        results = list(store.iter_with_issues())

        assert results == records


class TestLinkToSession:
    """`link_to_session()` は `extra['sensing']` の1キーへ収め、他のキーを破壊しない（要件 7.7）。"""

    def test_sets_single_sensing_namespace_key(self) -> None:
        record = _record("throw-0")

        linked = link_to_session(record, "session-abc", 10, 42)

        assert linked.extra["sensing"] == {
            "session_id": "session-abc",
            "frame_index_from": 10,
            "frame_index_to": 42,
        }

    def test_preserves_other_pre_existing_extra_keys(self) -> None:
        record = _record("throw-0", extra={"note": "hardware-test", "batch": 3})

        linked = link_to_session(record, "session-xyz", 0, 5)

        assert linked.extra["note"] == "hardware-test"
        assert linked.extra["batch"] == 3
        assert linked.extra["sensing"] == {
            "session_id": "session-xyz",
            "frame_index_from": 0,
            "frame_index_to": 5,
        }

    def test_returns_new_record_and_does_not_mutate_original(self) -> None:
        record = _record("throw-0")

        linked = link_to_session(record, "session-abc", 0, 1)

        assert linked is not record
        assert "sensing" not in record.extra

    def test_result_round_trips_through_json(self, tmp_path: Path) -> None:
        record = _record("throw-0")
        linked = link_to_session(record, "session-abc", 0, 1)

        store = ThrowRecordStore(tmp_path / "throws.ndjson")
        store.append(linked)

        restored = list(store.iter_records())[0]

        assert restored == linked
        assert restored.extra["sensing"]["session_id"] == "session-abc"


class TestPreconditionSchemaVersionMatchesCurrent:
    """`append()` に渡すレコードの `schema_version` は現行版と一致することを前提とする。"""

    def test_records_use_current_schema_version_by_default(self) -> None:
        record = _record("throw-0")
        assert record.schema_version == SCHEMA_VERSION


class TestBoundaryOnlyImportsFromPublicEntrypoint:
    """`throw_store.py` が `prediction_core` の公開入口のみを import することを固定する（要件 7.8）。"""

    def test_imports_public_entrypoint_module_only_not_internal_submodules(self) -> None:
        source = THROW_STORE_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(THROW_STORE_SOURCE_PATH))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith("prediction_core."), (
                    "throw_store は prediction_core の内部モジュールを import してはならない: "
                    f"{node.module}"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("prediction_core."), (
                        "throw_store は prediction_core の内部モジュールを import してはならない: "
                        f"{alias.name}"
                    )

    def test_imports_from_prediction_core_public_entrypoint(self) -> None:
        source = THROW_STORE_SOURCE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(THROW_STORE_SOURCE_PATH))

        imported_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "prediction_core":
                imported_names.extend(alias.name for alias in node.names)

        assert imported_names, "throw_store は prediction_core を公開入口から import しなければならない"
        assert set(imported_names) <= {
            "InvalidPrediction",
            "InvalidReason",
            "Prediction",
            "PredictionConfig",
            "PredictionConfigError",
            "PredictionCoreError",
            "PredictionOutcome",
            "RecordSchemaError",
            "RecordSerializationError",
            "SCHEMA_VERSION",
            "Sample",
            "SourceKind",
            "ThrowPredictionTracker",
            "ThrowRecord",
            "TrajectoryParameters",
            "predict",
            "predictions_equivalent",
            "replay",
        }
