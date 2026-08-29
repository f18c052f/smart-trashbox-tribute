"""上流基盤への接点の検証（タスク 2.1 / 要件 3.1, 3.6, 7.1, 7.3, 7.4, 7.9）。

観測可能な完了状態（tasks.md 2.1）を固定する:

- 上流への委譲が成立する（フレーム供給・時計・ログ・保存と読み出し・集計）
- **予約段階名を渡すと拒否される**
- **ログ器の取り出し入口が同一の実体を返す**

あわせて design.md「UpstreamGateway」が定める点も固定する:

- **`sensing_foundation` を import する唯一のモジュール**である
- 集計器を二重に持たない（上流の集計器へ委譲する）
- `get_logger_handle()` の戻り値は**本 Spec のどの層も解釈しない不透明値**であり、
  型注釈を具体型にしない

上流は既に実装済みなので、ダブルではなく**本物の `sensing_foundation`**に対して
検証する（tasks.md 2.1 は「上流公開型を模した最小ダブル」を挙げているが、それは
上流未実装の期間の代替手段である。本物で確かめられるならそのほうが強い）。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

import prediction_core
from m1_validation import upstream as upstream_module
from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from m1_validation.upstream import (
    M1_STAGES,
    STAGE_M1,
    STAGE_PREDICT,
    UPSTREAM_RESERVED_STAGES,
    UpstreamGateway,
    resolve_runtime_settings,
)
from prediction_core import PredictionConfig, Sample, ThrowRecord
from sensing_foundation import summarize_log

WIDTH_PX = 8
HEIGHT_PX = 6

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "m1_validation"


def _simulated_spec(tmp_path: Path) -> object:
    """合成入力の上流設定（実機を要さない）。"""
    return resolve_runtime_settings(
        file=None,
        env={},
        overrides={
            "source": "simulated",
            "width_px": WIDTH_PX,
            "height_px": HEIGHT_PX,
            "fps": 30,
            "logging_path": str(tmp_path / "logs"),
            "recording_root": str(tmp_path / "sessions"),
        },
    )


@pytest.fixture
def gateway(tmp_path: Path):
    gw = UpstreamGateway.open(
        session_id="test-upstream", source_spec=_simulated_spec(tmp_path)
    )
    yield gw
    gw.close()


def _record(record_id: str = "throw-0001") -> ThrowRecord:
    return ThrowRecord(
        record_id=record_id,
        source=prediction_core.SourceKind.SIMULATED,
        config=PredictionConfig(),
        samples=(Sample(t_ms=0.0, x_mm=0.0, y_mm=0.0, z_mm=1500.0),),
        predictions=(),
        extra={},
    )


class TestStageNames:
    """自前の段階名2つが、上流の予約名と衝突しない（要件 7.4）。"""

    def test_declares_two_stages_of_its_own(self) -> None:
        assert STAGE_PREDICT == "predict"
        assert STAGE_M1 == "m1"
        assert M1_STAGES == (STAGE_PREDICT, STAGE_M1)

    def test_own_stages_do_not_collide_with_reserved_ones(self) -> None:
        assert UPSTREAM_RESERVED_STAGES == frozenset({"system", "capture", "record"})
        assert set(M1_STAGES).isdisjoint(UPSTREAM_RESERVED_STAGES)


class TestEmit:
    @pytest.mark.parametrize("reserved", ["system", "capture", "record"])
    def test_reserved_stage_is_rejected(self, gateway, reserved: str) -> None:
        """予約段階名を渡すと拒否される（tasks.md 2.1 の完了状態）。

        上流の `obslog` は stage 名を実行時に検証しない（区分は文書上の
        約束である）。**衝突を防ぐ責任は足す側にある**ので、ここで拒否する
        ——混ざると段階別レイテンシの集計が上流の値と混線し、どちらの区間の
        数字なのか後から分けられなくなる。
        """
        with pytest.raises(M1ConfigError):
            gateway.emit(reserved, "something", {})

    def test_rejection_names_the_stage(self, gateway) -> None:
        with pytest.raises(M1ConfigError) as exc:
            gateway.emit("capture", "x", {})
        assert "capture" in str(exc.value)

    def test_own_stage_reaches_the_upstream_logger(self, tmp_path: Path) -> None:
        """送出が上流のログ器へ実際に届く（委譲が成立している）。"""
        gw = UpstreamGateway.open(
            session_id="test-emit", source_spec=_simulated_spec(tmp_path)
        )
        try:
            gw.emit(STAGE_PREDICT, "update", {"elapsed_ms": 12.5})
        finally:
            gw.close()

        lines = [
            json.loads(line)
            for path in sorted((tmp_path / "logs").glob("*.ndjson"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        emitted = [row for row in lines if row.get("stage") == STAGE_PREDICT]
        assert emitted, lines
        assert emitted[0]["event"] == "update"
        assert emitted[0]["data"]["elapsed_ms"] == 12.5


class TestLoggerHandle:
    def test_returns_the_same_instance_every_time(self, gateway) -> None:
        """同一の実体を返す（tasks.md 2.1 の完了状態）。

        上流の `TrackingPipeline.__init__` は `Logger` の実体を要求するので、
        呼ぶたび別物を返すと**送出先が分かれて1セッションのログが割れる**。
        """
        assert gateway.get_logger_handle() is gateway.get_logger_handle()

    def test_handle_is_the_logger_the_gateway_emits_through(self, gateway) -> None:
        """取り出した実体が、`emit()` の送出先と同じものである。"""
        assert gateway.get_logger_handle() is gateway._logger

    def test_return_annotation_is_object_not_a_concrete_type(self) -> None:
        """**型注釈を具体型にしない**（design.md「UpstreamGateway」Postconditions）。

        `Logger` と書いた瞬間、その型注釈を読む層が上流の型を知ることになる。
        不透明値として扱うという約束を、注釈の上でも守る。
        """
        annotation = inspect.signature(
            UpstreamGateway.get_logger_handle
        ).return_annotation
        assert annotation in ("object", object)


class TestSessionClock:
    def test_is_monotonic_non_decreasing(self, gateway) -> None:
        first = gateway.session_clock_ms()
        second = gateway.session_clock_ms()
        assert second >= first >= 0.0


class TestThrowRecordRoundTrip:
    def test_store_then_load(self, gateway, tmp_path: Path) -> None:
        path = tmp_path / "throws.ndjson"
        gateway.store_record(_record(), path)
        loaded = list(gateway.load_records(path))
        assert [item.record_id for item in loaded] == ["throw-0001"]

    def test_schema_is_not_redefined(self, gateway, tmp_path: Path) -> None:
        """`prediction_core` のスキーマをそのまま用いる（要件 3.4）。"""
        path = tmp_path / "throws.ndjson"
        gateway.store_record(_record(), path)
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert row["schema_version"] == prediction_core.SCHEMA_VERSION

    def test_unknown_schema_version_is_reported_as_a_seam_failure(
        self, gateway, tmp_path: Path
    ) -> None:
        """記録スキーマ版を予測コアの定義値と照合する（tasks.md 2.1）。

        上流の例外型をそのまま外へ出さないのは、**接点を1モジュールに閉じる**
        という制約を例外の上でも守るためである。呼び出し側が
        `sensing_foundation` の例外を捕捉するために import する羽目になると、
        境界が例外経路から崩れる。
        """
        path = tmp_path / "throws.ndjson"
        gateway.store_record(_record(), path)
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        row["schema_version"] = "99.0"
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

        with pytest.raises(SeamFailure) as exc:
            list(gateway.load_records(path))
        assert exc.value.reason == FailureReason.UNKNOWN_RECORD_SCHEMA


class TestSummarize:
    def test_delegates_to_the_upstream_aggregator(self, tmp_path: Path) -> None:
        """**集計器を二重に持たない**（`research.md` Decision 7）。

        同じログに対して上流の集計器と同じ結果になることで、独自実装を
        持っていないことを示す。二重に持つと、同じ数字が2つの計算式から
        出て食い違い得る。
        """
        gw = UpstreamGateway.open(
            session_id="test-summary", source_spec=_simulated_spec(tmp_path)
        )
        try:
            gw.emit(STAGE_PREDICT, "update", {"elapsed_ms": 10.0})
            gw.emit(STAGE_PREDICT, "update", {"elapsed_ms": 20.0})
            gw.emit(STAGE_M1, "throw_end", {"samples": 7})
        finally:
            gw.close()

        log_path = min((tmp_path / "logs").glob("*.ndjson"))
        assert gw.summarize_stages(log_path, stages=None) == summarize_log(
            log_path, stages=None
        )

    def test_reads_stages_it_does_not_know(self, tmp_path: Path) -> None:
        """上流が記録した段階を、集計側の改修なしに読み取れる（要件 7.3）。"""
        log_path = tmp_path / "upstream.ndjson"
        log_path.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in (
                    {
                        "t_ms": 1.0,
                        "stage": "detect",
                        "event": "done",
                        "data": {"elapsed_ms": 3.0},
                    },
                    {
                        "t_ms": 2.0,
                        "stage": "detect",
                        "event": "done",
                        "data": {"elapsed_ms": 5.0},
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        gw = UpstreamGateway.open(
            session_id="test-unknown-stage", source_spec=_simulated_spec(tmp_path)
        )
        try:
            summary = gw.summarize_stages(log_path, stages=None)
        finally:
            gw.close()
        assert ("detect", "done") in summary.events


class TestFrameSupply:
    def test_frames_come_from_the_upstream_source(self, tmp_path: Path) -> None:
        """フレーム供給が上流へ委譲されている（要件 3.1）。"""
        gw = UpstreamGateway.open(
            session_id="test-frames", source_spec=_simulated_spec(tmp_path)
        )
        try:
            supplied = [
                _depth(index) if index < 3 else None for index in range(4)
            ]
            frames = list(gw.open_frames(supplier=lambda i: supplied[i]))
        finally:
            gw.close()

        assert [frame.index for frame in frames] == [0, 1, 2]
        assert frames[0].depth.shape == (HEIGHT_PX, WIDTH_PX)


def _depth(seq: int):
    import numpy as np

    return np.full((HEIGHT_PX, WIDTH_PX), 1000 + seq, dtype=np.uint16)


class TestSoleContactPoint:
    """`sensing_foundation` を import する唯一のモジュールである。"""

    def test_no_other_m1_module_imports_sensing_foundation(self) -> None:
        offenders: list[str] = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            if path.name == "upstream.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                roots: list[str] = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots = [node.module.split(".")[0]]
                if "sensing_foundation" in roots:
                    offenders.append(f"{path.name}:{node.lineno}")
        assert offenders == []

    def test_upstream_module_does_import_it(self) -> None:
        """裏返しの確認——検査が空振りしていないこと。"""
        source = Path(inspect.getfile(upstream_module)).read_text(encoding="utf-8")
        assert "sensing_foundation" in source


class TestNoStandingAggregationOnDevice:
    """集計を実機上で常時実行する前提を持たない（要件 7.9）。"""

    def test_summarize_only_reads_a_log_file(self, gateway, tmp_path: Path) -> None:
        """集計はログファイルを読むだけで、取得も送出も要求しない。

        取得中の状態に依存する集計を書くと、実機で回している最中に
        集計処理が乗って計測対象そのものを歪める。
        """
        log_path = tmp_path / "standalone.ndjson"
        log_path.write_text(
            json.dumps(
                {"t_ms": 1.0, "stage": STAGE_M1, "event": "throw_end", "data": {}}
            )
            + "\n",
            encoding="utf-8",
        )
        assert gateway.summarize_stages(log_path, stages=[STAGE_M1]) is not None
