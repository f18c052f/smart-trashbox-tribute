"""真値の受け取りと導出（落下地点=実測 / 落下時刻=内挿 / リリース時刻=外挿）。

design.md「Components and Interfaces / L4-L5: 真値と実測 / TruthDeriver」および
「Data Models / 真値ファイル（`truth.json`）」、tasks.md タスク 4.1、
要件 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7。

**求め方を書かずに数値だけを出すと、誤差の出どころを後から議論できない。**
これは本 Spec の存在理由そのものである（要件の A-4）。予測落下地点と
「実際の」落下地点の差を 80 mm と報告したとして、実際の落下地点を ±5 mm で
測ったのか ±100 mm で測ったのかが書かれていなければ、その 80 mm が予測の
誤差なのか測り方の誤差なのかを誰も判定できない。同じことが落下時刻
（内挿）とリリース時刻（外挿）にも当てはまる。したがって本モジュールが
返す真値は**必ず `TruthValue`**（値・求め方の種別・不確かさの目安・測り方の
記述）であり、生の数値を単体で返す関数を置かない。

3つの真値は求め方が異なる。

- **落下地点（実測）**: 外部ファイルから受け取る。人が床のマークを測った値で
  あり、本モジュールは算出しない。**測り方の記述を必須**とする（要件 4.1）
- **落下時刻（内挿）**: 観測サンプル列が床面高さを**跨ぐ隣接2点**の線形内挿。
  跨ぐ区間が無ければ欠測とし、**片側外挿で作らない**（要件 4.2）
- **リリース時刻（外挿）**: 最終予測の軌道パラメータを観測開始より前へ外挿し、
  レイアウトのリリース高さに達する時刻（要件 4.3）。
  **新しいフィッティングは実装しない**——軌道の推定は `prediction_core` の
  責務であり、ここで別のフィットを持つと「予測の軌道」と「真値の軌道」という
  2つの軌道ができてしまう

**欠測は例外ではなく値である**（design.md「Error Categories and Responses」:
真値の欠測 → 値として扱う。当該項目のみ欠測、他項目は継続）。1件の未記入で
投擲群全体の集計が止まってはならない。欠測は `TruthMethod.MISSING` と
`value=None` で表し、**0 で埋めない**——「差が 0 だった」と「測っていない」は
別である。

一方、**測り方の記述が無い真値・不確かさの無い真値は拒否する**
（`M1ConfigError`）。これは欠測ではなく**呼び出し方の誤り**である。値が
書かれているのに求め方が書かれていない真値は、上のとおり誤差の議論を
不可能にするので、黙って受け取ってはならない。

真値の入力は投擲の実行と分離されている（要件 4.7）。`runner.py` は
`extra["m1"]["truth"]` を `None` にしたまま記録を作り、後から人が
`truth.json` を書き、`ingest_truth()` が記録へ対応付ける。**記録に存在しない
識別子の真値は警告として返し、黙って捨てない**——`throw-0007` と書くべき
ところを `throw-007` と書いた真値が黙って消えると、測ったのに反映されて
いないことに誰も気付けない。

本モジュールは L4 層の評価側であり、`sensing_foundation` /
`flying_object_tracking` / `world_frame_calibration` を import しない
（design.md「Allowed Dependencies」。評価側は**記録された値だけ**を読む——
要件 12.5 を構造で保証するため）。数値計算は標準ライブラリだけで書く。
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.types import M1_EXTRA_VERSION, ThrowTruth, TruthMethod, TruthValue
from prediction_core import SCHEMA_VERSION, Prediction, Sample, ThrowRecord

#: 真値ファイルの形式版。未知の版は内容を推測して読まない
#: （`LAYOUT_FORMAT_VERSION` と同じ方針）。
TRUTH_FORMAT_VERSION: str = "1.0"

#: 床面の高さ（mm）。World frame は床平面を `z = 0` として確立されており
#: （`world-frame-calibration`）、`prediction_core` も落下地点を `z = 0` との
#: 交点として定義している。**本 Spec がここを別の値に決め直してはならない**
#: ——落下時刻の内挿と予測の落下時刻が違う平面を指すと、その差は誤差では
#: なく定義の食い違いになる。
FLOOR_HEIGHT_MM: float = 0.0

#: 真値ファイルの1件（`entries` の値）に書いてよいキー。
_ENTRY_KEYS: frozenset[str] = frozenset(
    {
        "impact_point_world_mm",
        "impact_point_source",
        "impact_point_uncertainty_mm",
        "external_release_mark_ms",
        "notes",
    }
)

_POINT3_LENGTH = 3


@dataclass(frozen=True, slots=True)
class TruthIngest:
    """真値ファイルを記録へ対応付けた結果（要件 4.7）。

    Attributes:
        records: 真値を追記した記録（入力と**同じ順序・同じ長さ**）。
        truths: 各記録の真値（`records` と同順・同数）。
        unknown_record_ids: 記録に存在しなかった識別子（昇順）。
            **警告として返す値であり、捨ててよいものではない**。
            呼び出し側（CLI）はこれを人に見せる責任を負う。
    """

    records: tuple[ThrowRecord, ...]
    truths: tuple[ThrowTruth, ...]
    unknown_record_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# 真値ファイルの読み取り（要件 4.1 / 4.7）
# ---------------------------------------------------------------------------


def load_truth_file(
    path: Path | str, *, expected_layout_id: str | None = None
) -> Mapping[str, Mapping[str, object]]:
    """真値ファイルを読み、`record_id` をキーとする記入内容を返す（要件 4.7）。

    ファイルは次の形の JSON である（design.md「真値ファイル（`truth.json`）」）::

        {
          "truth_format_version": "1.0",
          "layout_id": "L1-2026-09",
          "entries": {
            "throw-0007": {
              "impact_point_world_mm": [1240.0, -310.0, 0.0],
              "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
              "impact_point_uncertainty_mm": 15.0,
              "external_release_mark_ms": null,
              "notes": "缶が1回バウンドした。初弾接地位置を記録"
            }
          }
        }

    落下地点を書いていない（`impact_point_world_mm` が無い、または `null`）
    記入は**許される**——実行後に人が少しずつ書き足すファイルだからである
    （要件 4.7）。書いてある場合に限り、測り方の記述と不確かさを必須とする。

    Args:
        path: 真値ファイルのパス。
        expected_layout_id: 期待する投擲レイアウトの識別子。与えると
            ファイルの `layout_id` と照合し、食い違えば拒否する。
            **別レイアウトの真値が混ざると誤差の帰属が壊れる**——帰属は
            投擲位置ごとに偏りの向きが変わることを使って原因を切り分ける
            （`research.md` Decision 4）ためである。

    Returns:
        `record_id` から記入内容へのマッピング。内容は読み取ったままの
        形（`derive_truth()` がそのまま受け取れる）。

    Raises:
        M1ConfigError: ファイルが読めない・JSON でない・形式版が未知・
            `entries` の形が違う・記入に未知のキーがある・値が書かれて
            いるのに測り方の記述や不確かさが無い、のいずれか。
            **すべて取り込みの時点で拒否する**（要件 13.6）。1件ずつ
            derive の途中で気付くと、集計の最中に止まることになる。
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise M1ConfigError(
            f"真値ファイルを読み込めない: {target}", {"path": str(target)}
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise M1ConfigError(
            f"真値ファイルが正しい JSON でない: {target}", {"path": str(target)}
        ) from exc

    if not isinstance(data, Mapping):
        raise M1ConfigError(
            f"真値ファイルの最上位が object でない: {type(data).__name__}"
            f"（path={target}）",
            {"path": str(target)},
        )

    format_version = data.get("truth_format_version")
    if format_version != TRUTH_FORMAT_VERSION:
        raise M1ConfigError(
            "未知の真値ファイル形式版を読み込もうとした: "
            f"{format_version!r}（対応する形式版: {TRUTH_FORMAT_VERSION!r},"
            f" path={target}）",
            {"format_version": format_version, "expected": TRUTH_FORMAT_VERSION},
        )

    layout_id = data.get("layout_id")
    if not isinstance(layout_id, str) or not layout_id.strip():
        raise M1ConfigError(
            f"真値ファイルに layout_id が無い（path={target}）。"
            "どのレイアウトで測った真値かが分からないと投擲群を分けられない",
            {"path": str(target), "layout_id": layout_id},
        )
    if expected_layout_id is not None and layout_id != expected_layout_id:
        raise M1ConfigError(
            f"真値ファイルのレイアウトが実行時のレイアウトと違う: "
            f"{layout_id!r} ≠ {expected_layout_id!r}（path={target}）。"
            "別レイアウトの真値を混ぜると誤差の帰属が壊れる",
            {"layout_id": layout_id, "expected_layout_id": expected_layout_id},
        )

    raw_entries = data.get("entries")
    if not isinstance(raw_entries, Mapping):
        raise M1ConfigError(
            f"entries は object でなければならない（path={target}）",
            {"path": str(target)},
        )

    entries: dict[str, Mapping[str, object]] = {}
    for record_id, raw_entry in raw_entries.items():
        if not isinstance(record_id, str) or not record_id.strip():
            raise M1ConfigError(
                f"entries のキー（record_id）が空である（path={target}）",
                {"path": str(target)},
            )
        if not isinstance(raw_entry, Mapping):
            raise M1ConfigError(
                f"真値の記入が object でない: record_id={record_id!r}"
                f"（path={target}）",
                {"record_id": record_id, "path": str(target)},
            )
        # 取り込みの時点で全件を検査する。30件目の綴り間違いが集計の最中に
        # 出てくると、そこまでの結果を捨てることになる。
        _parse_entry(raw_entry, record_id=record_id, where=str(target))
        entries[record_id] = raw_entry

    return entries


@dataclass(frozen=True, slots=True)
class _Entry:
    """検証済みの記入内容（本モジュールの内部表現）。"""

    impact_point_world_mm: tuple[float, float, float] | None
    impact_point_source: str
    impact_point_uncertainty_mm: float | None
    external_release_mark_ms: float | None


def _parse_entry(
    entry: Mapping[str, object] | None, *, record_id: str, where: str
) -> _Entry:
    """記入内容を検証して内部表現へ写す。

    `None`（記入が無い）と、落下地点が書かれていない記入は**欠測**として
    通す。値が書かれている場合に限り、測り方の記述（要件 4.1）と不確かさ
    （要件 4.4）を必須とする。
    """
    if entry is None:
        return _Entry(None, "", None, None)

    unknown = sorted(set(entry) - _ENTRY_KEYS)
    if unknown:
        raise M1ConfigError(
            f"真値の記入に未知のキーがある: {unknown}"
            f"（record_id={record_id}, where={where}）。綴り間違いの可能性がある",
            {"unknown_keys": unknown, "record_id": record_id},
        )

    mark = entry.get("external_release_mark_ms")
    external_mark_ms = (
        None
        if mark is None
        else _require_finite_float(
            mark, key="external_release_mark_ms", record_id=record_id, where=where
        )
    )

    raw_point = entry.get("impact_point_world_mm")
    if raw_point is None:
        return _Entry(None, "", None, external_mark_ms)

    point = _require_point3(raw_point, record_id=record_id, where=where)

    source = entry.get("impact_point_source")
    if not isinstance(source, str) or not source.strip():
        raise M1ConfigError(
            "落下地点には測り方の記述が必須である（要件 4.1）: "
            f"impact_point_source={source!r}"
            f"（record_id={record_id}, where={where}）。"
            "求め方を書かずに数値だけを出すと、誤差の出どころを議論できない",
            {"record_id": record_id, "key": "impact_point_source"},
        )

    raw_uncertainty = entry.get("impact_point_uncertainty_mm")
    if raw_uncertainty is None:
        raise M1ConfigError(
            "落下地点には不確かさの目安が必須である（要件 4.4）: "
            f"impact_point_uncertainty_mm が無い"
            f"（record_id={record_id}, where={where}）。"
            "±5mm で測ったのか ±100mm なのかが分からない真値からは、"
            "そこから出した誤差の意味を読み取れない",
            {"record_id": record_id, "key": "impact_point_uncertainty_mm"},
        )
    uncertainty = _require_finite_float(
        raw_uncertainty,
        key="impact_point_uncertainty_mm",
        record_id=record_id,
        where=where,
    )
    if uncertainty < 0.0:
        raise M1ConfigError(
            f"不確かさは負にできない: {uncertainty}"
            f"（record_id={record_id}, where={where}）",
            {"record_id": record_id, "impact_point_uncertainty_mm": uncertainty},
        )

    return _Entry(point, source, uncertainty, external_mark_ms)


def _require_finite_float(
    value: object, *, key: str, record_id: str, where: str
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise M1ConfigError(
            f"{key} は数値でなければならない: {value!r}"
            f"（record_id={record_id}, where={where}）",
            {"record_id": record_id, "key": key},
        )
    number = float(value)
    if not math.isfinite(number):
        raise M1ConfigError(
            f"{key} が有限値でない: {number!r}（record_id={record_id}, where={where}）",
            {"record_id": record_id, "key": key},
        )
    return number


def _require_point3(
    value: object, *, record_id: str, where: str
) -> tuple[float, float, float]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise M1ConfigError(
            f"impact_point_world_mm は数値の配列でなければならない: {value!r}"
            f"（record_id={record_id}, where={where}）",
            {"record_id": record_id, "key": "impact_point_world_mm"},
        )
    if len(value) != _POINT3_LENGTH:
        raise M1ConfigError(
            f"impact_point_world_mm の要素数が {_POINT3_LENGTH} でない: {len(value)} 件"
            f"（record_id={record_id}, where={where}）",
            {"record_id": record_id, "length": len(value)},
        )
    x, y, z = (
        _require_finite_float(
            item, key="impact_point_world_mm", record_id=record_id, where=where
        )
        for item in value
    )
    return (x, y, z)


# ---------------------------------------------------------------------------
# 真値の導出（要件 4.1-4.6）
# ---------------------------------------------------------------------------


def derive_truth(
    record: ThrowRecord,
    entry: Mapping[str, object] | None,
    *,
    layout: ThrowLayout,
) -> ThrowTruth:
    """1投擲の真値を求める（要件 4.1, 4.2, 4.3, 4.4, 4.5）。

    3つの真値を**それぞれ別の求め方**で求め、いずれも
    `TruthValue`（値・求め方の種別・不確かさの目安・測り方の記述）として返す。
    **求め方を書かずに数値だけを返さない**——誤差の出どころを後から議論
    できなくなるからである（モジュール docstring 参照）。

    Args:
        record: 対象の投擲記録。落下時刻は `record.samples`、リリース時刻は
            `record.predictions` の**最終予測の軌道パラメータ**から求める。
        entry: 真値ファイルの当該記入（`load_truth_file()` の値）。
            まだ記入が無ければ `None` を渡す。
        layout: 投擲レイアウト。`release_height_mm` が外挿の基準高さである。

    Returns:
        `ThrowTruth`。求められなかった真値は `TruthMethod.MISSING` として
        返る（要件 4.6）。**3つは独立に欠測しうる**。

    Raises:
        M1ConfigError: 記入に未知のキーがある、落下地点が書かれているのに
            測り方の記述または不確かさが無い、値が数値でない、のいずれか。
            **これは欠測ではなく呼び出し方の誤りである**（design.md
            「Error Categories and Responses」の「設定の誤り」に当たる）。
            欠測（未記入）では例外を投げない。
    """
    parsed = _parse_entry(entry, record_id=record.record_id, where="derive_truth")

    impact_point = _impact_point_truth(parsed)
    impact_time = _impact_time_truth(record)
    release_time = _release_time_truth(record, layout=layout)

    external_delta_ms: float | None = None
    if parsed.external_release_mark_ms is not None and isinstance(
        release_time.value, float
    ):
        # 要件 4.5: 外部の合図と外挿値の差を残す。**符号は「合図 − 外挿」**。
        external_delta_ms = parsed.external_release_mark_ms - release_time.value

    return ThrowTruth(
        record_id=record.record_id,
        impact_point_world_mm=impact_point,
        impact_time_ms=impact_time,
        release_time_ms=release_time,
        external_mark_delta_ms=external_delta_ms,
    )


def _impact_point_truth(entry: _Entry) -> TruthValue:
    """落下地点（実測。要件 4.1）。"""
    if entry.impact_point_world_mm is None:
        return TruthValue(
            value=None,
            method=TruthMethod.MISSING,
            uncertainty_mm=None,
            uncertainty_ms=None,
            source="欠測: 真値ファイルに落下地点の記入が無い（要件 4.1 / 4.6）",
        )
    return TruthValue(
        value=entry.impact_point_world_mm,
        method=TruthMethod.MEASURED,
        uncertainty_mm=entry.impact_point_uncertainty_mm,
        uncertainty_ms=None,
        source=entry.impact_point_source,
    )


def _impact_time_truth(record: ThrowRecord) -> TruthValue:
    """落下時刻（内挿。要件 4.2）。

    観測サンプル列を先頭から辿り、床面高さを**降下方向に跨ぐ最初の隣接2点**
    を線形内挿する。最初の交差を採るのは、実測の落下地点が初弾の接地点だ
    からである（design.md「真値ファイル」の記入例: 「缶が1回バウンドした。
    初弾接地位置を記録」）。最後の交差を採ると、落下地点と落下時刻が別の
    事象を指すことになる。

    上向きの交差（床面下から上へ抜ける）は接地ではないので採らない。
    跨ぐ区間が無ければ欠測であり、**片側外挿で作らない**（design.md
    「TruthDeriver」Validation）。
    """
    straddle = _first_descending_straddle(record.samples)
    if straddle is None:
        return TruthValue(
            value=None,
            method=TruthMethod.MISSING,
            uncertainty_mm=None,
            uncertainty_ms=None,
            source=(
                "欠測: 観測サンプル列に床面高さ"
                f"（z = {FLOOR_HEIGHT_MM} mm）を降下方向に跨ぐ隣接2点が無い。"
                "片側外挿で落下時刻を作らない（要件 4.2）"
            ),
        )

    before, after = straddle
    dt_ms = after.t_ms - before.t_ms
    drop_mm = before.z_mm - after.z_mm
    fraction = (before.z_mm - FLOOR_HEIGHT_MM) / drop_mm
    t_cross_ms = before.t_ms + dt_ms * fraction
    speed_mm_ms = drop_mm / dt_ms  # 弦の傾き（降下速度の目安。正値）

    # 不確かさの目安（要件 4.4）は2つの成分から成る。
    #
    # (1) 内挿そのものの誤差。真の軌道は放物線であり弦はその下を通るので
    #     （`z(t) - L(t) = (g/2)(t - t_i)(t_{i+1} - t) >= 0`）、線形内挿は
    #     **必ず早い側**へ出る。区間内での高さのずれは最大 `g dt^2 / 8` で
    #     あり、これを降下速度で時間へ換算する。
    # (2) 観測そのもののばらつき。最終予測の残差（推定軌道と観測の乖離、mm）
    #     を同じく降下速度で時間へ換算する。
    #
    # **ここに含まれないもの**: 床面高さの定義そのもののずれ（World frame の
    # 較正誤差）と、対象物が「点」ではないこと（缶の半径ぶん早く接地する）。
    # 前者はキャリブレーション検証レポートが、後者はレイアウトの対象寸法が
    # 持っている量であり、真値側で二重に数えない。
    gravity_mm_ms2 = record.config.gravity_mm_ms2
    curvature_ms = (gravity_mm_ms2 * dt_ms * dt_ms / 8.0) / speed_mm_ms
    residual_ms = _final_residual_mm(record) / speed_mm_ms
    uncertainty_ms = math.hypot(curvature_ms, residual_ms)

    return TruthValue(
        value=t_cross_ms,
        method=TruthMethod.INTERPOLATED,
        uncertainty_mm=None,
        uncertainty_ms=uncertainty_ms,
        source=(
            "観測サンプル列が床面高さを降下方向に跨ぐ隣接2点の線形内挿"
            f"（t = {before.t_ms} ms で z = {before.z_mm} mm、"
            f"t = {after.t_ms} ms で z = {after.z_mm} mm）。"
            "不確かさは弦と放物線のずれと最終予測の残差から導いた目安であり、"
            "床面高さ自体のずれと対象物の寸法は含まない"
        ),
    )


def _release_time_truth(record: ThrowRecord, *, layout: ThrowLayout) -> TruthValue:
    """リリース時刻（外挿。要件 4.3）。

    最終予測の軌道パラメータを**観測開始より前へ**外挿し、レイアウトの
    リリース高さに達する時刻を返す。**新しいフィッティングは実装しない**
    （design.md「TruthDeriver」Implementation Notes）。

    リリース高さの通過は上昇側と下降側の2回あり、**リリースは早い方
    （上昇側）**である。求めた時刻が観測開始より前でなければ欠測とする——
    観測区間の内側にある交差を返すと、**外挿ではない値**が「リリース時刻」
    の名前で `docs/requirements.md §3` 区間1 の実測へ入り込む。
    """
    prediction = _final_prediction(record)
    if prediction is None:
        return _missing_release("有効な予測が1件も無いため外挿する軌道が無い")
    if not record.samples:
        return _missing_release(
            "観測サンプルが無いため外挿の基準になる観測開始時刻が無い"
        )

    first_t_ms = record.samples[0].t_ms
    last_t_ms = record.samples[-1].t_ms
    observed_span_ms = last_t_ms - first_t_ms
    if not (math.isfinite(observed_span_ms) and observed_span_ms > 0.0):
        return _missing_release(
            "観測区間の長さが正でないため外挿の当てにならなさを見積もれない"
        )

    trajectory = prediction.trajectory
    gravity_mm_ms2 = trajectory.gravity_mm_s2 / 1_000_000.0
    vz_mm_ms = trajectory.estimated_vz_mm_s / 1_000.0
    height_gap_mm = layout.release_height_mm - trajectory.z0_mm
    if not (math.isfinite(gravity_mm_ms2) and gravity_mm_ms2 > 0.0):
        # 重力が正でないと下の判別式の符号の意味が反転する。「高さに達しない」
        # と取り違えないよう、解く前に別の理由として返す。
        return _missing_release("推定軌道の重力加速度が正の有限値でない")

    # 0.5 g s^2 - vz s + (h - z0) = 0 を s（`t_ref_ms` からの経過 ms）について解く。
    discriminant = vz_mm_ms * vz_mm_ms - 2.0 * gravity_mm_ms2 * height_gap_mm
    if not math.isfinite(discriminant) or discriminant < 0.0:
        return _missing_release(
            "推定軌道がレイアウトのリリース高さ"
            f"（{layout.release_height_mm} mm）に達しない"
        )

    root_ms = math.sqrt(discriminant)
    s_ascending_ms = (vz_mm_ms - root_ms) / gravity_mm_ms2
    t_release_ms = trajectory.t_ref_ms + s_ascending_ms
    if not math.isfinite(t_release_ms):
        return _missing_release("外挿の解が有限値にならない")
    if t_release_ms >= first_t_ms:
        return _missing_release(
            "リリース高さの通過が観測開始より前にない"
            f"（通過 {t_release_ms} ms ≧ 観測開始 {first_t_ms} ms）。"
            "外挿でない値をリリース時刻として出さない"
        )

    # 交差時点の鉛直速度（mm/ms）。残差（mm）を時間（ms）へ換算する分母。
    speed_mm_ms = abs(vz_mm_ms - gravity_mm_ms2 * s_ascending_ms)
    if not (math.isfinite(speed_mm_ms) and speed_mm_ms > 0.0):
        return _missing_release(
            "リリース高さの通過時の鉛直速度が 0 に潰れており、"
            "時刻の不確かさを見積もれない（軌道の頂点がリリース高さに一致する）"
        )

    # 不確かさの目安（要件 4.4。design.md「TruthDeriver」Risks が
    # 「外挿の不確かさが区間1 の実測値をそのまま左右する」と名指しした量）を
    # **外挿区間の長さと残差**から導く（tasks.md 4.1）。
    #
    #   残差 / 鉛直速度        : フィットが観測とどれだけ合っていないかを
    #                            リリース高さ通過時刻の揺れへ換算した量
    #   1 + 外挿区間 / 観測区間: 観測の外へ出るほど当てにならなくなる度合い。
    #                            観測区間と同じ長さだけ外挿すれば 2 倍になる
    #
    # **ここに含まれないもの**: 放物運動モデル自体の誤り（空気抵抗・回転）と、
    # レイアウトのリリース高さ自体の測り方の誤差。前者は残差に部分的にしか
    # 現れず、後者は `ThrowLayout` が持つ量である。**この2つを含めていない
    # ことを承知したうえで区間1 の値を読むこと。**
    extrapolated_span_ms = first_t_ms - t_release_ms
    amplification = 1.0 + extrapolated_span_ms / observed_span_ms
    uncertainty_ms = (_final_residual_mm(record) / speed_mm_ms) * amplification

    return TruthValue(
        value=t_release_ms,
        method=TruthMethod.EXTRAPOLATED,
        uncertainty_mm=None,
        uncertainty_ms=uncertainty_ms,
        source=(
            "最終予測の軌道パラメータを観測開始より前へ外挿し、レイアウトの"
            f"リリース高さ（{layout.release_height_mm} mm）に達する時刻として求めた"
            f"（外挿区間 {extrapolated_span_ms} ms / 観測区間 {observed_span_ms} ms、"
            f"最終予測の残差 {_final_residual_mm(record)} mm）。"
            "不確かさは残差と外挿区間の長さから導いた目安であり、"
            "放物運動モデル自体の誤りとリリース高さの測り方の誤差は含まない"
        ),
    )


def _missing_release(why: str) -> TruthValue:
    return TruthValue(
        value=None,
        method=TruthMethod.MISSING,
        uncertainty_mm=None,
        uncertainty_ms=None,
        source=f"欠測: {why}（要件 4.3 / 4.6）",
    )


def _first_descending_straddle(
    samples: Sequence[Sample],
) -> tuple[Sample, Sample] | None:
    """床面高さを降下方向に跨ぐ最初の隣接2点を返す。無ければ `None`。"""
    for before, after in itertools.pairwise(samples):
        if not all(
            math.isfinite(value)
            for value in (before.t_ms, before.z_mm, after.t_ms, after.z_mm)
        ):
            continue
        if after.t_ms <= before.t_ms:
            # 時刻が進んでいない対は内挿の分母を作れない。
            continue
        if before.z_mm > FLOOR_HEIGHT_MM >= after.z_mm:
            return (before, after)
    return None


def _final_prediction(record: ThrowRecord) -> Prediction | None:
    """最終の有効な予測。1件も無ければ `None`。"""
    for outcome in reversed(record.predictions):
        if isinstance(outcome, Prediction):
            return outcome
    return None


def _final_residual_mm(record: ThrowRecord) -> float:
    """最終予測の残差（mm）。予測が無い・非有限なら 0 とみなす。

    0 とみなすのは「残差が無い」からではなく、**残差という材料が無い**から
    である。この場合の不確かさは内挿そのものの誤差だけになり、観測の
    ばらつきぶんを含まない——`source` にその旨を書いてある。
    """
    prediction = _final_prediction(record)
    if prediction is None or not math.isfinite(prediction.residual):
        return 0.0
    return abs(prediction.residual)


# ---------------------------------------------------------------------------
# 記録への追記（要件 4.7）
# ---------------------------------------------------------------------------


def truth_to_dict(truth: ThrowTruth) -> dict[str, object]:
    """`ThrowTruth` を JSON 化できる形へ写す（`extra["m1"]["truth"]` の中身）。

    `json.dumps(..., allow_nan=False)` を通せる形にする（design.md
    「集計・判断の出力」。NaN / Infinity は欠測として表す方針であり、
    本モジュールは非有限値を真値にしない）。
    """
    return {
        "record_id": truth.record_id,
        "impact_point_world_mm": _truth_value_to_dict(truth.impact_point_world_mm),
        "impact_time_ms": _truth_value_to_dict(truth.impact_time_ms),
        "release_time_ms": _truth_value_to_dict(truth.release_time_ms),
        "external_mark_delta_ms": truth.external_mark_delta_ms,
    }


def _truth_value_to_dict(value: TruthValue) -> dict[str, object]:
    return {
        "value": list(value.value) if isinstance(value.value, tuple) else value.value,
        "method": str(value.method),
        "uncertainty_mm": value.uncertainty_mm,
        "uncertainty_ms": value.uncertainty_ms,
        "source": value.source,
    }


def attach_truth(record: ThrowRecord, truth: ThrowTruth) -> ThrowRecord:
    """真値を追記した新しい記録を返す（要件 4.7）。

    投擲の実行と真値の入力は分離されている。`runner.py` は
    `extra["m1"]["truth"]` を `None` にしたまま記録を作り、人が実験のあとで
    落下地点を測ってファイルへ書き、ここで追記される。

    **既存の拡張キーを保つ**。`extra["sensing"]`（セッションへの対応付け）を
    落とすと生データへ辿れなくなる。`ThrowRecord` は frozen かつ slots
    なので、新しい記録を組み立てて返す（入力の記録は書き換えない）。

    Raises:
        M1ConfigError: 記録のスキーマ版が既知でない、本 Spec の拡張
            （`extra["m1"]`）が無い、その `m1_extra_version` が既知でない、
            記録と真値の `record_id` が違う、のいずれか。
            **未知の版を推測して読まない**（design.md「Data Models」:
            読み出し時に `schema_version` と `m1_extra_version` の両方の
            既知性を検査する）。継ぎ目の不成立ではないので `SeamFailure`
            ではなく `M1ConfigError` である。
    """
    if record.schema_version != SCHEMA_VERSION:
        raise M1ConfigError(
            f"未知の Throw Record スキーマ版に真値を追記しようとした: "
            f"{record.schema_version!r}（対応する版: {SCHEMA_VERSION!r}）",
            {"schema_version": record.schema_version, "expected": SCHEMA_VERSION},
        )
    if record.record_id != truth.record_id:
        raise M1ConfigError(
            "記録と真値の record_id が違う: "
            f"{record.record_id!r} ≠ {truth.record_id!r}。"
            "取り違えた真値を追記すると、別の投擲の誤差を測ることになる",
            {"record_id": record.record_id, "truth_record_id": truth.record_id},
        )

    payload = record.extra.get("m1")
    if not isinstance(payload, Mapping):
        raise M1ConfigError(
            "本 Spec の拡張（extra['m1']）を持たない記録には真値を追記できない: "
            f"record_id={record.record_id!r}。"
            "版の分からない拡張領域を作ると、後から読む側が推測して読むことになる",
            {"record_id": record.record_id},
        )
    version = payload.get("m1_extra_version")
    if version != M1_EXTRA_VERSION:
        raise M1ConfigError(
            f"未知の m1_extra_version に真値を追記しようとした: {version!r}"
            f"（対応する版: {M1_EXTRA_VERSION!r}, record_id={record.record_id!r}）",
            {"m1_extra_version": version, "expected": M1_EXTRA_VERSION},
        )

    updated_payload = {**payload, "truth": truth_to_dict(truth)}
    return ThrowRecord(
        record_id=record.record_id,
        source=record.source,
        config=record.config,
        samples=record.samples,
        predictions=record.predictions,
        schema_version=record.schema_version,
        extra={**record.extra, "m1": updated_payload},
    )


def ingest_truth(
    records: Iterable[ThrowRecord],
    entries: Mapping[str, Mapping[str, object]],
    *,
    layout: ThrowLayout,
) -> TruthIngest:
    """真値ファイルの内容を記録へ対応付ける（要件 4.7、design.md `ingest-truth`）。

    記入が無い記録も**飛ばさない**。落下時刻（内挿）とリリース時刻（外挿）は
    記録だけから求まるので、落下地点が未記入でもその2つは残る（要件 4.6）。

    **記録に存在しない識別子は警告として返す**（`unknown_record_ids`）。
    黙って捨てると、`throw-0007` と書くべきところを `throw-007` と書いた
    真値が「未記入」と区別できなくなり、測ったのに反映されていないことに
    誰も気付けない。ここで例外にしないのは、1件の書き間違いで他の投擲の
    集計まで止めないためである——**報告はするが、止めはしない**。

    Args:
        records: 対象の投擲記録。
        entries: `record_id` から記入内容へのマッピング（`load_truth_file()`
            の戻り値）。
        layout: 投擲レイアウト。

    Returns:
        `TruthIngest`。`records` / `truths` は入力と同順・同数であり、
        `unknown_record_ids` は昇順である。永続化は行わない——保存は
        呼び出し側の責務である（実行と保存を分ける `runner.py` と同じ）。

    Raises:
        M1ConfigError: 記入の形が不正な場合（`derive_truth()` と同じ条件）、
            および追記できない記録が含まれる場合（`attach_truth()` 参照）。
    """
    updated: list[ThrowRecord] = []
    truths: list[ThrowTruth] = []
    matched: set[str] = set()

    for record in records:
        entry = entries.get(record.record_id)
        if entry is not None:
            matched.add(record.record_id)
        truth = derive_truth(record, entry, layout=layout)
        truths.append(truth)
        updated.append(attach_truth(record, truth))

    unknown = tuple(sorted(set(entries) - matched))
    return TruthIngest(
        records=tuple(updated), truths=tuple(truths), unknown_record_ids=unknown
    )
