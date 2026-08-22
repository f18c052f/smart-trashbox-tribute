"""計測 ON/OFF の比較（design.md「L8-L9: 比較・入口 → OverheadBench」、タスク 7.2）。

`tech.md` 開発標準5「計測が計測対象を歪めないこと」を、`detect`/`track` 区間
について実測で確認する。`bench/compare.py`（タスク 7.1、OQ-26 の決着）とは
別の問い——「どの検出方式が良いか」ではなく「**計測を有効にすることで
処理時間が計測できないほど歪むか**」——に答えるコンポーネントである
（design.md「OverheadBench / Intent」）。

Requirements: 8.6, 8.7, 8.8

依存方向（design.md「Dependency Direction」表の `bench/*` 行）: 0〜7層
（`errors` / `types` / `config` / `metrics` / `detection/*` / `projection` /
`tracking` / `pipeline`）＋ `sensing_foundation`（公開入口）を import してよい。
`cli`（L9）は import しない。

==============================================================================
判定基準の形を上流と揃える（design.md「Implementation Notes」必読）
==============================================================================

design.md は明記する:「判定基準の形は上流 `sensing-foundation` の
`LoggingOverheadBench` と揃える。同じ問いに違う基準を使わない」。

上流の実装（`src/sensing_foundation/bench/logging_overhead.py`
`_compute_verdict()`）を実際に読んで確認した判定基準の形:

    median_delta_ms = abs(on.total_ms_p50 - off.total_ms_p50)
    baseline_iqr_ms = off.total_ms_iqr
    passed = median_delta_ms <= baseline_iqr_ms   # 境界値は合格（"以内"）

本モジュールの `compute_verdict()` は、この式をそのまま踏襲する
（`median_delta_ms` は ON/OFF 中央値の**差の絶対値**、基準は **OFF 条件の
IQR**、比較は `<=`——「以内」を文字通り実装し境界を合格とする、という
上流と同一の3点をすべて引き継ぐ）。

**上流との意図的な差分（1点のみ）**: 上流の `_compute_verdict()` は
`frames_dropped`（ドレイン破棄件数が増えていないこと）を追加の合格条件に
含める。これは `capture` 層固有の概念（フレーム取得のドレイン）であり、
`detect`/`track` 層には対応する計数が無い（`TrackingCounters` にも
`frames_dropped` に相当するフィールドは無い）。design.md 自身の
「OverheadBench」節が定める判定基準の文（「ON 条件の…中央値と OFF 条件の
中央値の差が、OFF 条件の四分位範囲以内であるとき『有意に変化しない』と
判定する」）も frames_dropped 相当の付帯条件に一切言及していない。
したがって本モジュールは**中央値差 vs 基準 IQR という核となる判定式の形**
だけを厳密に揃え、`capture` 層固有の付帯条件は移植しない
（「同じ問いに違う基準を使わない」の対象は判定式の形であり、他層固有の
副条件までの完全一致を要求するとは読めない）。

==============================================================================
「1フレームあたり総処理時間」の測定方法（design.md との差分の明記）
==============================================================================

タスク本文は「1フレームあたり総処理時間」の中央値を比較対象とする。
これを `TrackingMetrics` が内部で送出する `detect_ms` + `track_ms`
（ログイベントの値）の合算として得ようとすると、**OFF 条件では
そもそも計測が無効化されており、`detect_ms`/`track_ms` 自体がログへ
送出されない**（要件 8.7: 計測が無効な間は計測値の生成そのものを行わない
——`metrics.py` `TrackingMetrics.__init__` が `config.enabled=False` のとき
内部ロガーを `NullLogger` に強制的に差し替える）。ログ経由の値だけを
比較対象にすると OFF 条件で比較対象そのものが存在せず、比較が成立しない。

本モジュールは上流 `LoggingOverheadBench` と同じ解決を採る（同モジュール
docstring「本モジュール独自の `total_ms` 測定」参照）: **`detect`/`track`
の2段を合わせた1フレーム分の総処理時間を、`TrackingPipeline.process()`
呼び出しの直前・直後で外側から `time.perf_counter()` により計測する。**
`process()` は内部で検出 → 逆投影 → 追跡を順に実行するため、この外側計測は
「detect_ms + track_ms 相当」を自然に含み、かつ **ON 条件で `logger.emit()`
が実際に行うキュー投入コスト自体も測定対象に含まれる**（これが本来測ろうと
しているオーバーヘッドそのものである——ログ経由の値だけを使うと、
`emit()` のコストそのものには構造的に盲目になる、という上流と同じ理由）。

==============================================================================
A/B/A/B 交互実行の実装（design.md との差分の明記）
==============================================================================

上流 `LoggingOverheadBench` は連続的なライブ/再生ストリームを対象にし、
各条件が独立した `FrameSource` イテレータを持ち、`segment_s` 秒ぶんずつ
交互に引き出す。本 Spec の比較対象は**有限の合成/記録済みフレーム列**
（`Sequence[CaptureFrame]` を丸ごと受け取る）であり、継続的なライブ供給を
前提にできない。本モジュールはこれを「**1セグメント = フレーム列の
1回の完全な通し**」に単純化する: `(measurement_off, measurement_on)` を
1巡として `cycles` 回繰り返し、各セグメントで**同一の** `frames` 列を
最初から最後まで処理する。これにより「同一入力」（文字通り同一の
`CaptureFrame` 列を両条件が同じ回数処理する）と「同一時間」
（両条件とも `cycles` 回ぶんの完全な通しを行う——処理するフレーム数が
両条件で常に等しい）を素直に満たしつつ、単純な「OFF を全部やってから
ON を全部やる」ではなく本当に交互に実行する（`segment_order` に記録する）。

各セグメントは**新規に構築した `TrackingPipeline`** で実行する（同一の
パイプラインを複数セグメントにまたいで使い回さない）。使い回すと、後の
セグメントほど追跡の内部状態（既に `ENDED` になった追跡へ新たな軌道の
先頭フレームを流し込む等）が前のセグメントの残滓を引きずり、「同一入力・
同一設定」で比較しているはずが実質的に異なる初期状態からの実行になって
しまうため。

計測有効側（`measurement_on`）のロガーは、上流と同じ理由（実際の
`logger.emit()` のコストを測定対象に含める）で `sensing_foundation.
get_logger()` が返す実物の `StructuredLogger`（バックグラウンド書き込み
スレッドを持つ）を使う。単なるインメモリのダミーではなく、実際にファイル
へ書き出す構成にすることで、本番相当のオーバーヘッドを測る。このロガーは
`cycles` 回すべての `measurement_on` セグメントを通して**1つだけ**構築し
（セグメントごとに開き直さない——1セッションに1ロガーという本番の使い方
に近づけるため）、`run()` の最後に `close()` する。

==============================================================================
対象段階名の明示（design.md「Risks」要件 — 上流の取得区間比較との混同防止）
==============================================================================

design.md「OverheadBench / Implementation Notes / Risks」: 「本 Spec の
計測対象は `detect` / `track` であり、上流の `capture` 区間とは別である。
結果を混同しないよう、出力に stage 名を明示する」。

本モジュールは `OverheadReport.stage_names`（実際に渡された
`TrackingSettings.measurement.detect_stage` / `track_stage` の値、既定
`("detect", "track")`）と `OverheadReport.note`（上流 `capture` 区間の
比較（`sensing_foundation.bench.modes.ModeSweep` /
`sensing_foundation.bench.logging_overhead.LoggingOverheadBench`）とは
対象区間が異なる旨の説明文）の2つで、この要求を満たす。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from sensing_foundation import (
    CaptureFrame,
    Logger,
    LoggingConfig,
    NullLogger,
    SessionClock,
    get_logger,
)

from flying_object_tracking.config import TrackingSettings
from flying_object_tracking.errors import TrackingConfigError
from flying_object_tracking.pipeline import TrackingPipeline

__all__ = [
    "OverheadResult",
    "OverheadVerdict",
    "OverheadReport",
    "CRITERION_TEXT",
    "compute_verdict",
    "OverheadBench",
    "overhead_output_path",
    "write_overhead_result",
]


# ==============================================================================
# 判定基準の説明文（実測前に確定させる。要件 8.6/8.7/8.8。モジュール docstring
# 「判定基準の形を上流と揃える」参照 — 核となる式は上流 LoggingOverheadBench
# の _compute_verdict() と同一の形にしてある）
# ==============================================================================

CRITERION_TEXT: str = (
    "measurement_on 条件の1フレームあたり総処理時間（detect+track 相当。"
    "TrackingPipeline.process() を外側から計測した壁時計時間の中央値）と、"
    "measurement_off 条件の中央値との差の絶対値が、measurement_off 条件の"
    "四分位範囲（IQR）以内であるとき「有意に変化しない」と判定する"
    "（median_delta_ms <= baseline_iqr_ms。境界値は合格）。"
    "本判定基準の形は上流 sensing_foundation.bench.logging_overhead."
    "LoggingOverheadBench の判定基準と同一である"
    "（design.md「OverheadBench / Implementation Notes」: "
    "『同じ問いに違う基準を使わない』）。"
)

_CONDITIONS: tuple[str, str] = ("measurement_off", "measurement_on")

_STAGE_NOTE_TEMPLATE: str = (
    "本結果は flying_object_tracking の {detect_stage!r}/{track_stage!r} 区間"
    "（検出・追跡）に対する計測 ON/OFF 比較である。sensing_foundation の "
    "capture 区間の取得比較（bench.modes.ModeSweep / "
    "bench.logging_overhead.LoggingOverheadBench）とは対象区間が異なる。"
    "混同しないこと（design.md「OverheadBench / Implementation Notes / Risks」）。"
)


# ==============================================================================
# データモデル
# ==============================================================================


@dataclass(frozen=True, slots=True)
class OverheadResult:
    """1条件分の集計結果（design.md「OverheadBench」）。

    Attributes:
        condition: `"measurement_off"` または `"measurement_on"`。
        samples: `total_ms` の生サンプル数。
        total_ms_p50: 1フレームあたり総処理時間（detect+track 相当。
            モジュール docstring「『1フレームあたり総処理時間』の測定方法」
            参照）の中央値。
        total_ms_p95: 同、95パーセンタイル。
        total_ms_iqr: 同、四分位範囲（p75 - p25）。判定基準の基準値として使う。
    """

    condition: Literal["measurement_off", "measurement_on"]
    samples: int
    total_ms_p50: float
    total_ms_p95: float
    total_ms_iqr: float


@dataclass(frozen=True, slots=True)
class OverheadVerdict:
    """ON/OFF 1組の比較判定（design.md「OverheadBench」）。

    Attributes:
        criterion: 判定基準の説明文（常に `CRITERION_TEXT`。実測前に確定済み
            で実行時のデータに依存しない）。
        passed: 「有意に変化しない」と判定されたら `True`。
        median_delta_ms: ON/OFF の `total_ms_p50` の差の絶対値。
        baseline_iqr_ms: OFF 条件の `total_ms_iqr`（判定の基準）。
        detail: 判定の根拠を人間可読な形で説明する文字列。
    """

    criterion: str
    passed: bool
    median_delta_ms: float
    baseline_iqr_ms: float
    detail: str


@dataclass(frozen=True, slots=True)
class OverheadReport:
    """`OverheadBench.run()` の戻り値（design.md「比較結果」節の要求：判定基準の

    説明文と判定結果を、結果と同じファイル/オブジェクトに含める。要件 8.8。

    Attributes:
        results: `(measurement_off の結果, measurement_on の結果)`。固定順。
        verdict: `compute_verdict()` の結果。`criterion`（説明文）と
            `passed`（判定結果）の両方をここに持つ——「判定基準の説明文と
            判定結果が結果ファイルに含まれる」というタスクの観測可能な
            完了状態そのもの。
        raw_samples: 条件名をキーにした `total_ms` の生サンプル列
            （収集順。判定を後から再計算できるようにする）。
        segment_order: 実際に実行したセグメントの条件名の列
            （`("measurement_off","measurement_on") * cycles` のはず——
            交互実行の構造的な証跡）。
        stage_names: `(detect_stage, track_stage)`。対象段階名の明示
            （design.md「Risks」: 上流の capture 区間比較と混同しないため）。
        note: 上流の取得区間比較（`ModeSweep` / `LoggingOverheadBench`）とは
            対象区間が異なる旨の説明文。
    """

    results: tuple[OverheadResult, OverheadResult]
    verdict: OverheadVerdict
    raw_samples: Mapping[str, tuple[float, ...]]
    segment_order: tuple[str, ...]
    stage_names: tuple[str, str]
    note: str


# ==============================================================================
# 分位点・四分位範囲（`bench/compare.py` と同じ規約: k=(n-1)*q の線形補間。
# 上流 `sensing_foundation.bench.logging_overhead._percentile()` とも同一規約
# ——3箇所とも独立実装だが同じ補間式に揃えてある）
# ==============================================================================


def _percentile(sorted_values: Sequence[float], fraction: float) -> float:
    """昇順ソート済み数値列から、線形補間で分位点を求める。値が無ければ 0.0。"""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * fraction
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    return sorted_values[f] * (c - k) + sorted_values[c] * (k - f)


def _iqr(sorted_values: Sequence[float]) -> float:
    """四分位範囲（Q3 - Q1）。空列は 0.0。"""
    if not sorted_values:
        return 0.0
    q1 = _percentile(sorted_values, 0.25)
    q3 = _percentile(sorted_values, 0.75)
    return q3 - q1


# ==============================================================================
# 判定（純粋関数。実行を経由しない——モジュール docstring「判定基準の形を
# 上流と揃える」参照。`bench/compare.py` の `decide()` と同じ切り出し方針）
# ==============================================================================


def compute_verdict(on: OverheadResult, off: OverheadResult) -> OverheadVerdict:
    """`on`（`measurement_on`）と `off`（`measurement_off`。ベースライン）の

    1組から `OverheadVerdict` を1つ作る、実行から独立した純粋関数。人工的に
    組み立てた `OverheadResult` を直接渡して判定規則だけを単体でテストできる
    （`bench/compare.py` の `decide()` と同じ、実行を伴わない検証のしやすさ
    を意図した切り出し）。

    Args:
        on: `measurement_on` 条件の集計結果。
        off: `measurement_off` 条件（ベースライン）の集計結果。

    Returns:
        `criterion` は常に `CRITERION_TEXT`。`passed` は
        `median_delta_ms <= baseline_iqr_ms` のとき `True`
        （境界値 `median_delta_ms == baseline_iqr_ms` は合格。「以内」の
        文字通りの実装。上流 `LoggingOverheadBench._compute_verdict()` と
        同一の判定式）。
    """
    median_delta_ms = abs(on.total_ms_p50 - off.total_ms_p50)
    baseline_iqr_ms = off.total_ms_iqr
    passed = median_delta_ms <= baseline_iqr_ms

    if passed:
        detail = (
            f"{on.condition!r} は {off.condition!r} 比較で有意な差が観測されなかった"
            f"（median_delta_ms={median_delta_ms:.4f} <= baseline_iqr_ms="
            f"{baseline_iqr_ms:.4f}）。"
        )
    else:
        detail = (
            f"{on.condition!r} は {off.condition!r} 比較で有意な差が観測された"
            f"（median_delta_ms={median_delta_ms:.4f} > baseline_iqr_ms="
            f"{baseline_iqr_ms:.4f}）。計測結果を無条件に有効なものとして扱わない。"
        )

    return OverheadVerdict(
        criterion=CRITERION_TEXT,
        passed=passed,
        median_delta_ms=median_delta_ms,
        baseline_iqr_ms=baseline_iqr_ms,
        detail=detail,
    )


# ==============================================================================
# OverheadBench: A/B/A/B 交互実行（モジュール docstring「A/B/A/B 交互実行の
# 実装」参照）
# ==============================================================================


class OverheadBench:
    """計測 ON/OFF の2条件を A/B/A/B で交互実行し、有意差の有無を判定する。

    design.md「OverheadBench」。要件 8.6, 8.7, 8.8。

    Preconditions: `cycles` は1以上。
    Postconditions: `run()` が返す `OverheadReport.results` は常に
        `(measurement_off の結果, measurement_on の結果)` の2件。
        `segment_order` は `("measurement_off","measurement_on") * cycles`。
    Invariants: `CRITERION_TEXT`（判定基準の説明文）は実測値に一切依存しない
        固定値である。
    """

    def __init__(self, cycles: int) -> None:
        """`OverheadBench` を組み立てる。

        Args:
            cycles: `(measurement_off, measurement_on)` の1巡を繰り返す回数。
                各回で `frames` 列を最初から最後まで1回通す（モジュール
                docstring「A/B/A/B 交互実行の実装」参照）。2以上でなければ
                「交互」の効果は薄いが、最小の動作確認用に1も許容する。

        Raises:
            TrackingConfigError: `cycles < 1`（呼び出し方の誤り）。
        """
        if cycles < 1:
            raise TrackingConfigError(f"cycles は1以上でなければならない: {cycles}")
        self._cycles = cycles

    def run(
        self,
        frames: Sequence[CaptureFrame],
        settings: TrackingSettings,
        *,
        work_dir: Path,
    ) -> OverheadReport:
        """`frames` を `measurement_off`/`measurement_on` で交互に `cycles` 回

        通し、`OverheadReport` を返す。

        Args:
            frames: 両条件で共通に使う、同一のフレーム列（合成入力または
                記録済みセッションから読み出したもの）。空であってはならない。
            settings: 検出・逆投影・追跡の設定。`measurement.enabled` だけを
                条件ごとに上書きした複製を使う（それ以外は完全に同一——
                「同一設定」を満たす）。`measurement.detect_stage` /
                `track_stage` は `OverheadReport.stage_names` にそのまま使う。
            work_dir: `measurement_on` 条件のログを書き出す作業ディレクトリ
                （`work_dir / "logs"` を使う）。

        Returns:
            2条件の `OverheadResult`・1つの `OverheadVerdict`・条件別の
            生サンプル・実行したセグメント順・対象段階名・注記を束ねた
            `OverheadReport`。

        Raises:
            TrackingConfigError: `frames` が空。
        """
        frame_tuple = tuple(frames)
        if not frame_tuple:
            raise TrackingConfigError("frames は空であってはならない（比較対象が無い）")

        work_dir = Path(work_dir)

        off_settings = replace(settings, measurement=replace(settings.measurement, enabled=False))
        on_settings = replace(settings, measurement=replace(settings.measurement, enabled=True))

        logger_off: Logger = NullLogger()
        clock_on = SessionClock(session_id="bench-overhead-measurement-on")
        logger_on: Logger = get_logger(
            LoggingConfig(enabled=True, path=work_dir / "logs"), clock_on
        )

        raw_samples: dict[str, list[float]] = {name: [] for name in _CONDITIONS}
        segment_order: list[str] = []

        settings_by_condition = {"measurement_off": off_settings, "measurement_on": on_settings}
        logger_by_condition: dict[str, Logger] = {
            "measurement_off": logger_off,
            "measurement_on": logger_on,
        }

        try:
            for _ in range(self._cycles):
                for condition in _CONDITIONS:
                    segment_order.append(condition)
                    pipeline = TrackingPipeline(
                        settings_by_condition[condition], logger_by_condition[condition]
                    )
                    samples = raw_samples[condition]
                    for frame in frame_tuple:
                        t0 = time.perf_counter()
                        pipeline.process(frame)
                        t1 = time.perf_counter()
                        samples.append((t1 - t0) * 1000.0)
        finally:
            logger_on.close()

        off_result = self._build_result("measurement_off", raw_samples["measurement_off"])
        on_result = self._build_result("measurement_on", raw_samples["measurement_on"])
        verdict = compute_verdict(on_result, off_result)

        stage_names = (settings.measurement.detect_stage, settings.measurement.track_stage)

        return OverheadReport(
            results=(off_result, on_result),
            verdict=verdict,
            raw_samples={name: tuple(values) for name, values in raw_samples.items()},
            segment_order=tuple(segment_order),
            stage_names=stage_names,
            note=_STAGE_NOTE_TEMPLATE.format(
                detect_stage=stage_names[0], track_stage=stage_names[1]
            ),
        )

    @staticmethod
    def _build_result(condition: str, samples: list[float]) -> OverheadResult:
        sorted_samples = sorted(samples)
        return OverheadResult(
            condition=condition,  # type: ignore[arg-type]
            samples=len(sorted_samples),
            total_ms_p50=_percentile(sorted_samples, 0.5),
            total_ms_p95=_percentile(sorted_samples, 0.95),
            total_ms_iqr=_iqr(sorted_samples),
        )


# ==============================================================================
# 書き出し（`bench/compare.py` の `write_comparison_result()` と同じ流儀）
# ==============================================================================


def overhead_output_path(output_root: Path, session_id: str) -> Path:
    """`OverheadReport` の書き出し先パスを返す（design.md「OverheadBench」節）。"""
    return output_root / f"overhead-{session_id}.json"


def write_overhead_result(report: OverheadReport, output_root: Path, session_id: str) -> Path:
    """`report` を `var/tracking/overhead-<session_id>.json` へ JSON 化する。

    判定基準の説明文（`verdict.criterion`）と判定結果（`verdict.passed`）は
    `report.verdict` の中に共に含まれており、`report` 自体が単一のオブジェクト
    であるため、この2つが別ファイルへ分離することは構造的に無い（要件 8.8の
    観測可能な完了状態「判定基準の説明文と判定結果が結果ファイルに含まれる」
    をそのまま満たす）。

    Args:
        report: 書き出す `OverheadReport`。
        output_root: 出力先ルート（`TrackingSettings.output_root` を渡す想定）。
            存在しなければ作成する。
        session_id: `overhead_output_path()` に渡すファイル名の一部。

    Returns:
        書き出したファイルのパス。
    """
    output_root.mkdir(parents=True, exist_ok=True)
    path = overhead_output_path(output_root, session_id)
    payload = {
        "results": [asdict(r) for r in report.results],
        "verdict": asdict(report.verdict),
        "raw_samples": {name: list(values) for name, values in report.raw_samples.items()},
        "segment_order": list(report.segment_order),
        "stage_names": list(report.stage_names),
        "note": report.note,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
