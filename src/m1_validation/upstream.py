"""`sensing_foundation` との唯一の接点。

design.md「Components and Interfaces / L2: 上流との接点 / UpstreamGateway」、
tasks.md タスク 2.1、要件 3.1, 3.6, 7.1, 7.3, 7.4, 7.9。

**本モジュールは `sensing_foundation` を import する唯一のモジュールである。**
他の層は取得も記録もログもここを経由する。接点を1つに閉じるのは、上流の
公開面が変わったときに**直す場所が1箇所で済む**ようにするためであり、
`test_m1_upstream.py` が「他のどのモジュールも import していないこと」を
静的に固定している。

**集計器を二重に持たない**（`research.md` Decision 7）。段階別レイテンシの
集計は上流の `summarize_log()` へそのまま委譲する。同じ数字が2つの計算式から
出ると、食い違ったときにどちらが正しいのか決められなくなる。上流の集計器は
未知の段階名もそのまま読めるので、本 Spec が段階を足しても集計側の改修は
要らない（要件 7.3）。

**集計を実機上で常時実行する前提を持たない**（要件 7.9）。`summarize_stages()`
はログファイルを読むだけであり、取得中の状態に依存しない。取得の最中に集計が
乗ると、計測対象そのものを歪める。

**`Logger` の実体を取り出す穴が1つだけある**（`get_logger_handle()`）。
上流の `TrackingPipeline.__init__` が `Logger` の実体を要求するため、
`emit()` のような関数形の委譲だけでは足りない。取り出した値は
**本 Spec のどの層も解釈しない不透明値**として扱い、型注釈も `object` に
留める——`Logger` と書いた瞬間、その注釈を読む層が上流の型を知ることになる。

**実装判断: 自由関数ではなくクラスにした。** design.md の Service Interface は
`open_frames` / `session_clock_ms` / `emit` / `get_logger_handle` …を自由関数の
擬似コードで示しているが、これらは**すべて1つのセッション時計と1つのログ器を
共有する**。自由関数にするとモジュール大域の可変状態が要り、テストが実行順に
依存する。コンポーネント名（`UpstreamGateway`）が示すとおり、セッション寿命を
持つ1つの窓口として実装し、擬似コードの関数名はメソッド名としてそのまま残した。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import TracebackType
from typing import Self

from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from prediction_core import SCHEMA_VERSION
from sensing_foundation import (
    CaptureMetrics,
    RuntimeSettings,
    SessionClock,
    Sysstat,
    ThrowRecordStore,
    ThrowRecordVersionError,
    get_logger,
    open_source,
    summarize_log,
)

#: 予測区間の計測点を載せる段階名（要件 7.4）。
STAGE_PREDICT = "predict"

#: 投擲単位の計測点を載せる段階名（要件 7.4）。
STAGE_M1 = "m1"

#: 本 Spec が足す段階名。**上流の予約名と衝突しない**。
M1_STAGES: tuple[str, str] = (STAGE_PREDICT, STAGE_M1)

#: 上流が予約している段階名（`sensing_foundation.obslog.RESERVED_STAGES`）。
#:
#: ⚠️ **上流はこの集合を公開入口に出していない**ため、ここに写しを持つ。
#: 上流が予約名を増やした場合、本モジュールは自動では追随しない
#: （`sensing_foundation.obslog` の `RESERVED_STAGES` を確認すること）。
UPSTREAM_RESERVED_STAGES: frozenset[str] = frozenset({"system", "capture", "record"})


def resolve_runtime_settings(
    *,
    file: Path | None,
    env: Mapping[str, str],
    overrides: Mapping[str, object],
) -> object:
    """上流の `RuntimeSettings.resolve()` への素通し。

    **本 Spec は取得の設定値を一切決めない**（既定値も持たない）。この関数が
    あるのは、`sensing_foundation` を import するのが本モジュールだけという
    境界を保ったまま、`cli.py` が上流の解決結果を手に入れられるようにする
    ためだけである（`seam.py` の `resolve_tracking_settings()` と同じ理由。
    design.md「Seam」の当該 docstring 参照）。

    上流の署名は `resolve(*, file, env, overrides)` で3つとも必須である。
    本関数が一部を内部で空に埋めると、**上流側の環境変数と CLI 上書きが
    黙って捨てられる**ので、3つとも呼び出し元から受け取ってそのまま渡す。

    Returns:
        `sensing_foundation.RuntimeSettings`。**本 Spec のどの層も中身を
        解釈しない不透明値**として扱うため、注釈は `object` に留める。
    """
    return RuntimeSettings.resolve(file=file, env=env, overrides=overrides)


class UpstreamGateway:
    """`sensing_foundation` へのセッション寿命の窓口。

    1つの `SessionClock` と1つの `Logger` を保持し、フレーム供給・時刻・
    ログ送出・Throw Record の保存と読み出し・ログ集計を上流へ委譲する。

    Preconditions:
        `emit()` の `stage` は `UPSTREAM_RESERVED_STAGES` に含まれないこと。
    Postconditions:
        `store_record()` は `prediction_core` のスキーマをそのまま用いる。
        `get_logger_handle()` の戻り値は本 Spec の型ではない。
    """

    __slots__ = ("_clock", "_logger", "_metrics", "_session_id", "_source_spec")

    def __init__(
        self,
        *,
        session_id: str,
        source_spec: object,
        clock: object,
        logger: object,
        metrics: object,
    ) -> None:
        """直接は呼ばない。`UpstreamGateway.open()` を使うこと。"""
        self._session_id = session_id
        self._source_spec = source_spec
        self._clock = clock
        self._logger = logger
        self._metrics = metrics

    @classmethod
    def open(cls, *, session_id: str, source_spec: object) -> Self:
        """セッション時計とログ器を組み立てて窓口を開く。

        Args:
            session_id: セッション識別子。ログのファイル名にもなる。
            source_spec: 入力元の指定（`sensing_foundation.RuntimeSettings`）。
                **本 Spec は中身を解釈しない**——`resolve_runtime_settings()`
                で得た値をそのまま渡す。取得設定もログ設定もここから取る
                （1セッション = 1入力元。上流の `cli.py` と同じ組み立て方）。

        Returns:
            開いた窓口。使い終わったら `close()` すること（`with` も使える）。
        """
        clock = SessionClock(session_id=session_id)
        logger = get_logger(source_spec.logging, clock)  # type: ignore[attr-defined]
        metrics = CaptureMetrics(logger, clock, Sysstat())
        return cls(
            session_id=session_id,
            source_spec=source_spec,
            clock=clock,
            logger=logger,
            metrics=metrics,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """ログ器を閉じる。二重呼び出しは安全（上流の契約）。"""
        self._logger.close()  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # 時刻・ログ
    # ------------------------------------------------------------------

    def session_clock_ms(self) -> float:
        """セッション開始からの経過 ms。end-to-end の基準（要件 7.2）。"""
        return float(self._clock.now_ms())  # type: ignore[attr-defined]

    def emit(self, stage: str, event: str, data: Mapping[str, object]) -> None:
        """構造化ログへ1件送出する。

        Args:
            stage: 段階名。`STAGE_PREDICT` / `STAGE_M1` を使う。
            event: イベント名。
            data: 付随データ。

        Raises:
            M1ConfigError: `stage` が上流の予約名（`system` / `capture` /
                `record`）である場合。**上流の `obslog` は stage 名を実行時に
                検証しない**（区分は文書上の約束）ので、衝突を防ぐ責任は
                足す側にある。混ざると段階別レイテンシが上流の値と混線し、
                どちらの区間の数字か後から分けられなくなる（要件 7.4）。
        """
        if stage in UPSTREAM_RESERVED_STAGES:
            raise M1ConfigError(
                f"上流が予約している段階名は使えない: {stage!r}"
                f"（予約: {sorted(UPSTREAM_RESERVED_STAGES)}、"
                f"本 Spec の段階名: {list(M1_STAGES)}）",
                {"stage": stage, "reserved": sorted(UPSTREAM_RESERVED_STAGES)},
            )
        self._logger.emit(stage, event, **dict(data))  # type: ignore[attr-defined]

    def get_logger_handle(self) -> object:
        """上流の `Logger` の実体を返す（**不透明値**）。

        上流の `TrackingPipeline.__init__` が `Logger` の実体を要求するため、
        関数形の委譲だけでは足りない。**戻り値の中身を本 Spec のどの層も
        解釈しない**——属性アクセスも型検査も行わず、`Seam.open_tracking()`
        へ素通しするだけである。注釈を `object` に留めているのはそのため
        （具体型を書くと、その注釈を読む層が上流の型を知ることになる）。

        呼ぶたび**同じ実体**を返す。別物を返すと送出先が分かれて
        1セッションのログが割れる。
        """
        return self._logger

    # ------------------------------------------------------------------
    # フレーム供給
    # ------------------------------------------------------------------

    def open_frames(
        self,
        *,
        supplier: object = None,
        speed: str = "fast",
    ) -> Iterator[object]:
        """`open()` で受け取った入力元指定からフレームを供給する（要件 3.1）。

        Args:
            supplier: 合成入力のときの供給関数（上流の `FrameSupplier`）。
                他の入力元では使われない。
            speed: 記録再生のときの速度（上流の `ReplaySpeed`）。

        Yields:
            上流の `CaptureFrame`。**入力元の種別によらず同じ形**である
            （要件 3.6 の「実機 / 記録再生 / 合成」はこの1経路で切り替わる）。

        入力元は必ず文脈管理で開閉する。途中で例外が出ても停止処理が走る。
        """
        source = open_source(
            self._source_spec,  # type: ignore[arg-type]
            self._metrics,  # type: ignore[arg-type]
            clock=self._clock,  # type: ignore[arg-type]
            supplier=supplier,  # type: ignore[arg-type]
            speed=speed,  # type: ignore[arg-type]
        )
        with source:
            yield from source.frames()

    # ------------------------------------------------------------------
    # Throw Record の保存と読み出し
    # ------------------------------------------------------------------

    def store_record(self, record: object, path: Path) -> None:
        """Throw Record を1行の JSON として追記する（要件 3.3）。

        `prediction_core` が定めたスキーマをそのまま用い、**再定義しない**
        （要件 3.4）。保存の実体は上流の `ThrowRecordStore` である。
        """
        ThrowRecordStore(Path(path)).append(record)  # type: ignore[arg-type]

    def load_records(self, path: Path) -> Iterator[object]:
        """保存した Throw Record を先頭から読み出す。

        読み出し時に**記録スキーマ版を `prediction_core.SCHEMA_VERSION` と
        照合する**（tasks.md 2.1）。照合そのものは上流の `ThrowRecordStore`
        が行う——ここで独自に比較し直すと、同じ判定が2箇所に増えて食い違い得る。

        Raises:
            SeamFailure: 記録スキーマ版が未知の場合
                （`FailureReason.UNKNOWN_RECORD_SCHEMA`）。上流の例外型を
                そのまま外へ出さないのは、**接点を1モジュールに閉じる**という
                制約を例外の経路でも守るためである（呼び出し側が上流の例外を
                捕まえるために `sensing_foundation` を import する羽目になると、
                境界が例外側から崩れる）。
        """
        store = ThrowRecordStore(Path(path))
        try:
            yield from store.iter_records()
        except ThrowRecordVersionError as exc:
            raise SeamFailure(
                FailureReason.UNKNOWN_RECORD_SCHEMA,
                f"記録スキーマ版が未知である: {exc}",
                {"path": str(path), "expected_schema_version": SCHEMA_VERSION},
            ) from exc

    # ------------------------------------------------------------------
    # ログ集計
    # ------------------------------------------------------------------

    def summarize_stages(
        self, log_path: Path, stages: Sequence[str] | None = None
    ) -> object:
        """構造化ログを段階×イベントで集計する（要件 7.1, 7.3）。

        **上流の集計器へそのまま委譲する。集計器を二重に持たない**
        （`research.md` Decision 7）。上流が記録した段階も、本 Spec が足した
        段階も、同じ1つの集計器で読める（要件 7.3: 集計側の改修を要さない）。

        **ログファイルを読むだけ**であり、取得中の状態に依存しない
        （要件 7.9: 集計を実機上で常時実行する前提を持たない）。

        Args:
            log_path: 構造化ログ（NDJSON）のパス。
            stages: 集計対象の段階名。`None` なら全段階。

        Returns:
            上流の `LogSummary`。
        """
        return summarize_log(Path(log_path), stages=stages)
