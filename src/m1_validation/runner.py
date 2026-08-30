"""1投擲を取得から逐次予測・記録まで通して実行する。

design.md「Components and Interfaces / L3: 実行 / ThrowRunner」、
tasks.md タスク 3.1 / 3.2、要件 2.3, 3.1-3.8, 7.4。

並びは **取得 → 検出・追跡 → 継ぎ目 → 逐次予測 → 記録** である。本モジュールが
持つのは**この並びだけ**であり、検出も変換も予測も自分では行わない。

- 追跡パイプラインは `seam.open_tracking()` から得る。**本モジュールは
  検出・追跡パッケージを import しない**
- 取得・時計・ログ・保存は `UpstreamGateway` へ委ねる。**本モジュールは
  `sensing_foundation` を import しない**
- 予測の更新は `prediction_core.ThrowPredictionTracker` に委ねる。
  **再フィットの制御を本 Spec は行わない**

**検証ゲートは投擲を始める前に評価する。** フレームを1枚も引く前に
`seam.open_calibration()` を通す——投げてから拒否すると**その1投擲を無駄に
する**。人が物を投げる作業なので、投げ直しの費用は小さくない。

**`extra["m1"]` の付与に `dataclasses.replace()` を使う。**
`ThrowPredictionTracker.to_record()` は `extra` を引数に取らず、`ThrowRecord`
は frozen かつ slots である。したがって拡張領域は後から差し替えるしかない。
`trajectory-simulator` は「蓄積済みのサンプル列・予測系列から公開コンストラクタ
で直接構築する」ほうを採っており、design.md は**両下流が同じ手段を採ること**を
意図しているが、ここでは `replace` を選んだ——`to_record()` が組み立てを1箇所に
持っている以上、その結果を写すほうが**構築の重複を作らない**。**`prediction_core`
には一切手を入れない**（迂回であって修正ではない）。

**予測経路の中で集計しない**（`tech.md` 開発標準5）。計測は送出するだけで、
統計処理はログを後から読む側の仕事である。取得中に集計が乗ると、計測対象
そのものを歪める。

**観測の不成立は値にする**（要件 3.8、design.md「Error Categories and
Responses」）。有効サンプル0件・追跡不成立・上流の失敗のいずれも、理由付き
の記録として返し、例外で実験を止めない——例外で落ちると1試行を失い、人が
もう一度投げる羽目になる。失敗投擲は記録に残したうえで、集計の入口
（`successful_throws()`）で除く。

**ただし継ぎ目の不成立は例外のまま**である（design.md「Error Strategy」）。
検証ゲート・整合性検査は投擲を始める前に、座標系・形式版の食い違いは投擲中
に、いずれも `SeamFailure` として送出する。**これらは上流の失敗ではなく本
Spec 自身の拒否**であり、値にすると食い違ったまま実験が進んでしまう。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from m1_validation.config import M1Settings
from m1_validation.errors import FailureReason, M1ConfigError, SeamFailure
from m1_validation.seam import (
    build_samples,
    calibration_summary,
    open_calibration,
    open_tracking,
)
from m1_validation.types import M1_EXTRA_VERSION, SampleProvenance, SampleReject
from m1_validation.upstream import STAGE_PREDICT, UpstreamGateway
from prediction_core import (
    Prediction,
    PredictionConfig,
    SourceKind,
    ThrowPredictionTracker,
    ThrowRecord,
)

#: 上流（取得・追跡）が投擲の途中で落ちたときの失敗理由。
#:
#: `FailureReason` に対応するメンバが無い。あちらは継ぎ目・真値・判断の
#: 語彙であり、**上流が落ちたという事象はそのどれでもない**。有効サンプル
#: 0件（`NO_VALID_SAMPLE`）へ丸めると、6点まで観測できて途中で落ちた投擲が
#: 「1点も取れなかった投擲」と同じ理由になり、原因が読めなくなる。
#: `errors.py` へメンバを足すのは下流の再検証を要する変更（design.md
#: 「Revalidation Triggers」）であり、本タスクの境界の外である。
UPSTREAM_FAILURE = "upstream_failure"


@dataclass(frozen=True, slots=True)
class ThrowRunResult:
    """1投擲の実行結果（design.md「ThrowRunner」Contracts）。

    Attributes:
        record_id: 投擲の識別子。
        record: `prediction_core.ThrowRecord`。本 Spec 固有の情報は
            `extra["m1"]` にある。
        samples_appended: 予測へ渡したサンプル数（除外後）。
        rejected: 除外理由ごとの件数（要件 1.7）。
        first_valid_sample_count: **初回予測が成立したサンプル数**
            （要件 5.3 の材料）。最後まで成立しなければ `None`。
        failed_reason: 追跡不成立などの失敗理由（要件 3.8）。成功なら `None`。
    """

    record_id: str
    record: ThrowRecord
    samples_appended: int
    rejected: tuple[tuple[SampleReject, int], ...]
    first_valid_sample_count: int | None
    failed_reason: str | None


def run_throw(
    *,
    settings: M1Settings,
    gateway: UpstreamGateway,
    calibration_path: Path,
    record_id: str,
    tracking_settings: object,
    signature: object,
    intrinsics: object,
    supplier: object = None,
    allow_unverified: bool = False,
) -> ThrowRunResult:
    """1投擲を実行し、`ThrowRecord` として組み立てて返す（要件 3.1）。

    Args:
        settings: 本 Spec の設定（除外規則・レイアウト）。
        gateway: 上流基盤への窓口。フレーム供給・時計・ログ送出をここから得る。
        calibration_path: キャリブレーション結果ファイル。**投擲を始める前に**
            検証ゲートと整合性検査を通す。
        record_id: 投擲の識別子。
        tracking_settings: 上流の追跡設定。**本 Spec は中身を解釈せず**
            `seam.open_tracking()` へ素通しする（調達は
            `seam.resolve_tracking_settings()` に限る）。
        signature: 現在の入力元のストリーム識別情報（同上。整合性検査へ素通し）。
        intrinsics: 現在の入力元のカメラ内部パラメータ（同上）。
        supplier: 合成入力のときの供給関数（上流の `FrameSupplier`）。
        allow_unverified: 未検証のキャリブレーションでの実行を明示的に許可する。

    Returns:
        `ThrowRunResult`。永続化は行わない——保存は呼び出し側が
        `UpstreamGateway.store_record()` で行う（実行と保存を分けることで、
        失敗投擲の扱いを呼び出し側が決められる）。

        投擲が始まった後の**観測の不成立**は返り値として返す（要件 3.8）。
        `failed_reason` に理由が入り、そこまでに得たサンプルは記録に残る:

        - 有効サンプルが0件（追跡不成立を含む）: `no_valid_sample`
        - 上流（取得・追跡）が落ちた: `UPSTREAM_FAILURE`

    Raises:
        SeamFailure: 検証ゲート・整合性検査が成立しない場合（**フレームを
            引く前**に判明する。投げてから拒否しない）、および**投擲中に
            継ぎ目が拒否した場合**（座標系・形式版の食い違い）。継ぎ目の
            不成立は失敗投擲へ丸めない——値にすると、食い違ったまま実験が
            進む（design.md「Error Handling / Error Strategy」）。
        M1ConfigError: 設定の誤り。投擲単位の失敗として飲み込まない
            （design.md「Error Categories and Responses」）。
        KeyboardInterrupt: 人が実験を止めた場合。失敗投擲にしない。

    設計上の逸脱（design.md の擬似コード署名との違い）:
        - `source_spec` ではなく `gateway` を受け取る。入力元の指定は
          `UpstreamGateway.open()` が保持しており（1セッション = 1入力元）、
          時計とログ器もそこに1つずつしか無いためである。
        - `logger` を受け取らない。design.md は「取得は
          `UpstreamGateway.get_logger_handle()` に限る」と定めており、
          `gateway` があれば呼び出し側が持ち回る必要が無い。
        - `signature` / `intrinsics` を受け取る。上流の
          `check_compatibility()` はこの2つを要求するが、**上流の公開入口
          からは組み立てられない**（tasks.md タスク2.3 の申し送り）。
          解決するまでは `tracking_settings` と同じ不透明値として
          呼び出し元から受け取る。
    """
    # 1. 検証ゲート。**フレームを引く前**に評価する。
    calibration = open_calibration(
        calibration_path,
        settings=settings,
        signature=signature,
        intrinsics=intrinsics,
        allow_unverified=allow_unverified,
    )

    pipeline = open_tracking(settings, tracking_settings, gateway.get_logger_handle())
    tracker = ThrowPredictionTracker(
        record_id=record_id,
        source=SourceKind(gateway.source_kind),
        config=PredictionConfig(),
    )

    rejected: dict[SampleReject, int] = {}
    provenance: list[SampleProvenance] = []
    first_valid_sample_count: int | None = None
    last_track: object | None = None
    interrupted_by: str | None = None

    # 取得から予測までを投擲単位で囲う。**ここで捕まえないと1試行を失う**
    # （design.md「ThrowRunner」Risks）。捕まえた失敗は理由付きの記録に
    # なり、呼び出し側は次の投擲へ進める。
    try:
        for frame in gateway.open_frames(supplier=supplier):
            update = pipeline.process(frame)
            last_track = update.track
            if update.appended is None:
                continue

            # 継ぎ目へは**追加された1点だけ**を渡す。累積の点列を毎回渡すと
            # 変換をやり直すことになり、除外件数も二重に数えてしまう。
            single_point_track = dataclasses.replace(
                update.track, points=(update.appended,)
            )
            built = build_samples(single_point_track, calibration, settings=settings)
            for reason, count in built.rejected:
                rejected[reason] = rejected.get(reason, 0) + count

            for sample, sample_provenance in zip(
                built.samples, built.provenance, strict=True
            ):
                outcome = tracker.add_sample(sample)
                provenance.append(sample_provenance)
                sample_count = len(tracker.samples)
                if first_valid_sample_count is None and isinstance(outcome, Prediction):
                    first_valid_sample_count = sample_count
                # fire-and-forget。ここで集計しない（`tech.md` 開発標準5）。
                gateway.emit(
                    STAGE_PREDICT,
                    "update",
                    {
                        "record_id": record_id,
                        "sample_t_ms": sample.t_ms,
                        "sample_count": sample_count,
                        # end-to-end は「観測時刻 → その観測に基づく予測」と
                        # 定義されている（要件 7.2）。**両端を残す**——片方
                        # だけでは後から算出できない。
                        "predicted_at_ms": gateway.session_clock_ms(),
                        "valid": isinstance(outcome, Prediction),
                    },
                )
    except M1ConfigError:
        # 設定の誤りは**起動時に拒否する**分類であり（design.md「Error
        # Categories and Responses」）、投擲単位の失敗ではない。飲み込むと
        # 同じ誤りで全投擲が静かに失敗し続け、設定を直せば済むことに
        # 気付けなくなる。
        raise
    except SeamFailure:
        # 継ぎ目の不成立は**例外のまま**外へ出す（design.md「Error Handling /
        # Error Strategy」および「Error Categories and Responses」）。座標系・
        # 形式版・設定の食い違いは上流の失敗ではなく**本 Spec 自身の拒否**で
        # あり、`errors.py` が言うとおり「呼び出し側が戻り値の確認を怠っても
        # 処理が止まる形」にしておく必要がある。値にすると、たとえば上流が
        # `handoff_version` を上げたときに一度も送出されず、**全投擲が失敗
        # 記録として静かに積み上がる**（設計どおりなら最初の投擲で止まり、
        # その場で直せる）。
        raise
    except Exception:  # noqa: BLE001 - 意図的な広い捕捉（要件 3.8）
        # `BaseException` は捕まえない——`KeyboardInterrupt` を失敗投擲に
        # すると、人が実験を止める手段が奪われる。
        interrupted_by = UPSTREAM_FAILURE

    failed_reason = interrupted_by or (
        None if provenance else str(FailureReason.NO_VALID_SAMPLE)
    )
    record = _with_m1_extra(
        tracker.to_record(),
        settings=settings,
        calibration=calibration,
        track=last_track,
        provenance=provenance,
        rejected=rejected,
        failed_reason=failed_reason,
    )

    return ThrowRunResult(
        record_id=record_id,
        record=record,
        samples_appended=len(provenance),
        rejected=tuple(rejected.items()),
        first_valid_sample_count=first_valid_sample_count,
        failed_reason=failed_reason,
    )


def failed_reason_of(record: ThrowRecord) -> str | None:
    """記録に残された失敗理由を返す。成功した投擲なら `None`（要件 3.8）。

    理由の置き場所（`extra["m1"]["failed_reason"]`）を知っているのは
    **記録を組み立てた本モジュールだけ**である。集計側が拡張領域の構造を
    各所で覚え直すと、置き場所が変わったときに黙って全件が「成功」に
    見える。

    `extra["m1"]` を持たない記録は**失敗と決めつけない**——本 Spec の拡張が
    無いことは「本 Spec が失敗と判定した事実が無い」というだけである。
    """
    payload = record.extra.get("m1")
    if not isinstance(payload, Mapping):
        return None
    reason = payload.get("failed_reason")
    return None if reason is None else str(reason)


def successful_throws(records: Iterable[ThrowRecord]) -> tuple[ThrowRecord, ...]:
    """失敗投擲を除いた記録を返す（要件 3.8）。

    **除外は記録を捨てることではない。** 失敗投擲は理由付きで残したうえで、
    集計の入口で外す（design.md「Error Categories and Responses」: 観測の
    不成立は値として扱い、集計から除く）。混ぜたままにすると、成功試行の
    ばらつきに0件の投擲や中断した投擲が算入され、実測値が歪む。
    """
    return tuple(record for record in records if failed_reason_of(record) is None)


def _with_m1_extra(
    record: ThrowRecord,
    *,
    settings: M1Settings,
    calibration: object,
    track: object | None,
    provenance: list[SampleProvenance],
    rejected: dict[SampleReject, int],
    failed_reason: str | None,
) -> ThrowRecord:
    """`extra["m1"]` を付与した新しい `ThrowRecord` を返す（要件 3.4）。

    **既存の拡張キーを保つ**。`sensing_foundation.link_to_session()` が後から
    `extra["sensing"]` を足す（要件 7.7）ので、`extra` を丸ごと置き換える
    書き方をすると**順序が変わった瞬間に対応付けが消える**。

    `ThrowRecord` は frozen かつ slots であり `to_record()` は `extra` を
    受け取らないため、`dataclasses.replace()` で差し替える（モジュール
    docstring 参照）。
    """
    summary = calibration_summary(calibration)  # type: ignore[arg-type]
    payload: dict[str, object] = {
        "m1_extra_version": M1_EXTRA_VERSION,
        "layout": settings.describe()["layout"],
        "calibration": summary,
        "tracking": _tracking_summary(track),
        "provenance": [_provenance_to_dict(item) for item in provenance],
        "rejected": [
            {"reason": str(reason), "count": count} for reason, count in rejected.items()
        ],
        # 真値は投擲の実行とは分離して後から追記する（要件 4.7）。
        "truth": None,
        "verified": summary["verified"],
        "failed_reason": failed_reason,
    }
    return dataclasses.replace(record, extra={**record.extra, "m1": payload})


def _tracking_summary(track: object | None) -> dict[str, object] | None:
    """追跡の識別情報（生データへ辿るための材料。要件 3.5）。"""
    if track is None:
        return None
    return {
        "handoff_version": track.handoff_version,  # type: ignore[attr-defined]
        "track_id": track.track_id,  # type: ignore[attr-defined]
        "detector_kind": track.detector_kind,  # type: ignore[attr-defined]
        "started_t_ms": track.started_t_ms,  # type: ignore[attr-defined]
    }


def _provenance_to_dict(item: SampleProvenance) -> dict[str, object]:
    """`SampleProvenance` を JSON 化できる形へ写す（サンプルと同順・同数）。"""
    return {
        "frame_index": item.frame_index,
        "frame_seq": item.frame_seq,
        "valid_depth_px": item.valid_depth_px,
        "depth_spread_mm": item.depth_spread_mm,
        "apparent_diameter_px": item.apparent_diameter_px,
        "expected_diameter_px": item.expected_diameter_px,
        "rivals": item.rivals,
        "gap_before": item.gap_before,
        "camera_ray_unit": list(item.camera_ray_unit),
    }
