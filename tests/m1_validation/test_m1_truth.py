"""真値の受け取りと導出の検証（タスク 4.1、要件 4.1-4.7）。

観測可能な完了状態（tasks.md 4.1）を固定する:

- **既知の軌道から生成した記録に対し、内挿による落下時刻と外挿による
  リリース時刻が解析解と一致する**
- **跨ぐ区間が無い入力では欠測が返る**

あわせて design.md「TruthDeriver」が定める点も固定する:

- 落下地点は外部から与えられる実測値であり、**測り方の記述が無ければ拒否する**
- 内挿は床面を**跨ぐ**隣接2点に限る（片側外挿で落下時刻を作らない）
- 求められなかった真値は `TruthMethod.MISSING` として返り、**例外を投げない**
- 真値の入力は投擲の実行と分離され、**実行後に追記できる**

**「解析解と一致」の許容差をどう置いたか。** 2つの求め方で桁が違う。

- **外挿（リリース時刻）**: 観測サンプルを厳密な放物線の上に置いているので、
  重力を固定した最小二乗フィットは軌道パラメータを浮動小数の丸め誤差まで
  復元する。したがって外挿値は解析解と**厳密に一致するのが正しい**。
  許容差 `RELEASE_TOLERANCE_MS`（1e-6 ms）はフィットの条件数だけに由来する
  余裕であり（実測は 1e-12 ms 規模）、**外挿の定式化を誤れば数十 ms 規模で
  ずれる**——許容差の中に隠れる誤りは無い。
- **内挿（落下時刻）**: 隣接2点の**線形**内挿なので、放物線の解析解とは
  原理的に一致しない。弦は放物線の下を通る（`z(t) - L(t) = (g/2)(t-t_i)
  (t_{i+1}-t)  >= 0`）ため、**内挿は必ず解析解より早い側に出る**。
  そこで「たまたま通る許容差」を置く代わりに、3つを検査する。
  **(a) ずれが実装自身の申告する不確かさ以下であること**、
  **(b) ずれが 0 でなく、必ず早い側であること**、
  **(c) 申告された不確かさそのものが、テスト定数だけから組み立てた
  絶対量と一致すること**。

  (a) だけでは**過小申告しか捕まらない**——不確かさを 100 倍に膨らませた
  実装も、内挿をやめて中点固定にしたうえで不確かさを 100 倍にした実装も、
  (a) と (b) は通ってしまう（独立レビューの変異解析で判明）。許容差に
  実装自身の出力を使う検査は、それ単独では**上限側に対して無力**である。
  (c) が上限側を閉じる。外挿側（`expected_ms`）と同じ扱いである。

**解析解は実装の定数に触れない。** 床面高さは本ファイルの `FLOOR_Z_MM`
（リテラルの 0.0）から組み立て、`truth.FLOOR_HEIGHT_MM` を参照しない。
実装の定数で参照解を組むと、**その定数を変えたときに参照解が一緒に動いて
差が消える**（同じく変異解析で判明: `FLOOR_HEIGHT_MM` を 50.0 にしても
全件通っていた）。実装の定数が 0 であることは、参照解の側ではなく
`test_floor_height_is_pinned_to_zero` が独立に固定する。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout
from m1_validation.truth import (
    FLOOR_HEIGHT_MM,  # `test_floor_height_is_pinned_to_zero` 専用。解析解には使わない
    TRUTH_FORMAT_VERSION,
    attach_truth,
    derive_truth,
    ingest_truth,
    load_truth_file,
    truth_to_dict,
)
from m1_validation.types import M1_EXTRA_VERSION, ThrowTruth, TruthMethod
from prediction_core import (
    Prediction,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowPredictionTracker,
    ThrowRecord,
)

# --- 既知の軌道（解析解を手で書ける形にする）-------------------------------
#
# 基準になる量に 0 を置かない。タスク2.2 / 3.1 の Implementation Notes が
# 「0 を既定値にしたフィクスチャは、差分・オフセット・基準の取り違えを
# まるごと素通しさせる」と記録している——リリース時刻も観測開始時刻も
# 差分として現れるので、ここは 0 以外でなければ検査が働かない。

CONFIG = PredictionConfig()
G_MM_MS2 = CONFIG.gravity_mm_ms2

#: 床面の高さ（mm）。**実装の `truth.FLOOR_HEIGHT_MM` を参照せず、ここに
#: リテラルで置く。** 参照解が実装の定数に追随すると、定数を変えたときに
#: 両方が一緒に動いて差が消える（モジュール docstring 参照）。
FLOOR_Z_MM = 0.0

RELEASE_T_MS = 5000.0
RELEASE_HEIGHT_MM = 1500.0
RELEASE_X_MM = -2000.0
RELEASE_Y_MM = 0.0
VX_MM_MS = 3.0
VY_MM_MS = -0.5
VZ_MM_MS = 2.0

#: 最初の有効サンプルの観測時刻。リリースの 100 ms 後（= §3 区間1 に相当）。
FIRST_SAMPLE_T_MS = 5100.0
#: サンプル間隔（60 fps 相当）。
DT_MS = 1000.0 / 60.0

#: 外挿の許容差（モジュール docstring 参照）。
RELEASE_TOLERANCE_MS = 1e-6


def position_at(t_ms: float) -> tuple[float, float, float]:
    """既知の軌道上の位置（World mm）。"""
    s = t_ms - RELEASE_T_MS
    return (
        RELEASE_X_MM + VX_MM_MS * s,
        RELEASE_Y_MM + VY_MM_MS * s,
        RELEASE_HEIGHT_MM + VZ_MM_MS * s - 0.5 * G_MM_MS2 * s * s,
    )


def analytic_impact_time_ms() -> float:
    """解析解の落下時刻（`z = 0` を満たす遅い方の根）。"""
    disc = VZ_MM_MS * VZ_MM_MS + 2.0 * G_MM_MS2 * (RELEASE_HEIGHT_MM - FLOOR_Z_MM)
    return RELEASE_T_MS + (VZ_MM_MS + math.sqrt(disc)) / G_MM_MS2


def sample_times(*, until_ms: float, first_ms: float = FIRST_SAMPLE_T_MS) -> list[float]:
    t = first_ms
    times: list[float] = []
    while t <= until_ms:
        times.append(t)
        t += DT_MS
    return times


def build_record(
    *,
    times: Sequence[float] | None = None,
    z_offsets: Mapping[int, float] | None = None,
    record_id: str = "throw-0007",
    config: PredictionConfig = CONFIG,
) -> ThrowRecord:
    """既知の軌道から `ThrowRecord` を組み立てる。

    予測系列は**本物の `ThrowPredictionTracker`** に作らせる。真値の外挿は
    「最終予測の軌道パラメータを用いる」（design.md「TruthDeriver」）ので、
    手で書いた軌道パラメータを渡すと**外挿の入口だけを検査して、実際に
    通る経路を検査しない**ことになる。
    """
    if times is None:
        times = sample_times(until_ms=analytic_impact_time_ms() + DT_MS)
    tracker = ThrowPredictionTracker(
        record_id=record_id, source=SourceKind.SIMULATED, config=config
    )
    for index, t_ms in enumerate(times):
        x_mm, y_mm, z_mm = position_at(t_ms)
        if z_offsets is not None and index in z_offsets:
            z_mm += z_offsets[index]
        tracker.add_sample(Sample(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
    return tracker.to_record()


def build_layout(**overrides: object) -> ThrowLayout:
    values: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": (RELEASE_X_MM, RELEASE_Y_MM, RELEASE_HEIGHT_MM),
        "release_height_mm": RELEASE_HEIGHT_MM,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": (0.0, 0.0),
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": (0.0, -1500.0, 1000.0),
        "notes": "仮値。確定ではない。",
    }
    values.update(overrides)
    return ThrowLayout(**values)  # type: ignore[arg-type]


def measured_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "impact_point_world_mm": [1240.0, -310.0, 0.0],
        "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
        "impact_point_uncertainty_mm": 15.0,
    }
    entry.update(overrides)
    return entry


def write_truth_file(tmp_path: Path, **overrides: object) -> Path:
    document: dict[str, object] = {
        "truth_format_version": TRUTH_FORMAT_VERSION,
        "layout_id": "throw-a",
        "entries": {"throw-0007": measured_entry()},
    }
    document.update(overrides)
    path = tmp_path / "truth.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def final_prediction(record: ThrowRecord) -> Prediction:
    valid = [item for item in record.predictions if isinstance(item, Prediction)]
    assert valid, "テストの前提: 最終予測が成立していること"
    return valid[-1]


# ---------------------------------------------------------------------------
# 落下時刻（内挿。要件 4.2）
# ---------------------------------------------------------------------------


class TestImpactTimeInterpolation:
    """床面を跨ぐ隣接2点の内挿（要件 4.2）。"""

    def test_matches_analytic_solution_within_reported_uncertainty(self) -> None:
        """内挿による落下時刻が解析解と一致する（tasks.md 4.1 の完了状態）。

        許容差の根拠はモジュール docstring のとおりであり、
        **実装自身が申告した不確かさ**を許容差として使う。申告が過小なら
        この検査が落ちる——「不確かさの目安」を数値だけ並べて済ませない
        ための検査でもある。
        """
        record = build_record()
        truth = derive_truth(record, measured_entry(), layout=build_layout())

        impact = truth.impact_time_ms
        assert impact.method is TruthMethod.INTERPOLATED
        assert impact.uncertainty_ms is not None
        assert impact.uncertainty_ms > 0.0
        assert impact.source.strip()
        assert isinstance(impact.value, float)

        error_ms = impact.value - analytic_impact_time_ms()
        assert abs(error_ms) <= impact.uncertainty_ms
        # 弦は放物線の下を通るので、線形内挿は**必ず早い側**へ出る。
        # 0 でないことも固定する（厳密一致すると主張する実装は間違い）。
        assert -impact.uncertainty_ms <= error_ms < 0.0

        # 申告された不確かさそのものを、**テスト定数だけから組み立てた
        # 絶対量**と突き合わせる。上の2つは許容差に実装自身の出力を使って
        # おり、**過小申告しか捕まえられない**——不確かさを 100 倍に
        # 膨らませても通ってしまう（独立レビューの変異解析で判明）。
        #
        # 区間内で弦と放物線が離れる最大量は `g dt^2 / 8`（中点で最大）で
        # あり、それを接地時の降下速度で時間へ換算したものが目安になる。
        # 降下速度は合成軌道から解析的に求まる。
        descent_speed_mm_ms = abs(
            VZ_MM_MS - G_MM_MS2 * (analytic_impact_time_ms() - RELEASE_T_MS)
        )
        expected_unc_ms = (G_MM_MS2 * DT_MS**2 / 8.0) / descent_speed_mm_ms
        # 実装は弦の傾き（＝区間**中点**での瞬時速度）で割るので、接地点での
        # 瞬時速度を使う上の値とは最大で `g * dt / 2` ぶん（約 1.4%）ずれる。
        # 許容差 5% はその差のためだけのものであり、100 倍・2 倍といった
        # 桁の違う申告はここを通れない。
        assert impact.uncertainty_ms == pytest.approx(expected_unc_ms, rel=0.05)

    def test_uses_the_first_descending_crossing(self) -> None:
        """跳ね返って2度跨ぐ入力では、**最初の接地**を返す。

        真値ファイルの例（design.md「真値ファイル」）が「缶が1回バウンド
        した。初弾接地位置を記録」と書いているとおり、実測の基準は初弾で
        ある。最後の交差を返す実装だと、落下地点の実測（初弾）と落下時刻
        （2度目）が**別の事象を指す**ことになる。
        """
        impact_ms = analytic_impact_time_ms()
        times = sample_times(until_ms=impact_ms + DT_MS)
        record = build_record(times=times)
        first_crossing = derive_truth(
            record, None, layout=build_layout()
        ).impact_time_ms
        assert isinstance(first_crossing.value, float)

        # 接地後に跳ね上がり、もう一度床を跨ぐ列を作る。
        bounced = list(record.samples)
        last_t = bounced[-1].t_ms
        for step, z_mm in enumerate((80.0, 30.0, -20.0), start=1):
            bounced.append(
                Sample(
                    t_ms=last_t + DT_MS * step,
                    x_mm=bounced[-1].x_mm + VX_MM_MS * DT_MS,
                    y_mm=bounced[-1].y_mm + VY_MM_MS * DT_MS,
                    z_mm=z_mm,
                )
            )
        bounced_record = _replace_samples(record, bounced)

        again = derive_truth(bounced_record, None, layout=build_layout()).impact_time_ms
        assert again.value == first_crossing.value

    def test_missing_when_no_pair_straddles_the_floor(self) -> None:
        """跨ぐ区間が無い入力では欠測が返る（tasks.md 4.1 の完了状態）。

        床面より上の点しか無い列に対し、**片側外挿で落下時刻を作らない**
        （design.md「TruthDeriver」Validation）。作ってしまうと、観測が
        途切れた投擲の落下時刻が「観測できた」ことになり、予測誤差の
        分母が静かに水増しされる。
        """
        impact_ms = analytic_impact_time_ms()
        # 交差の 3 サンプル手前で観測が途切れた列。
        times = sample_times(until_ms=impact_ms - DT_MS * 3.0)
        record = build_record(times=times)
        assert all(sample.z_mm > FLOOR_Z_MM for sample in record.samples)

        truth = derive_truth(record, measured_entry(), layout=build_layout())

        assert truth.impact_time_ms.method is TruthMethod.MISSING
        assert truth.impact_time_ms.value is None
        assert truth.impact_time_ms.uncertainty_ms is None
        assert truth.impact_time_ms.source.strip()
        # 当該項目のみ欠測とし、他項目は止めない（要件 4.6）。
        assert truth.impact_point_world_mm.method is TruthMethod.MEASURED
        assert truth.release_time_ms.method is TruthMethod.EXTRAPOLATED

    def test_ascending_crossing_is_not_an_impact(self) -> None:
        """下から上へ抜ける交差を落下時刻にしない。

        床面下の点（`floor_margin_mm` の内側なので継ぎ目を通り抜ける）から
        始まる列でも、**降下方向の交差だけ**が接地である。
        """
        record = build_record()
        rising = [
            Sample(t_ms=4000.0, x_mm=-3000.0, y_mm=100.0, z_mm=-20.0),
            Sample(t_ms=4016.0, x_mm=-2950.0, y_mm=95.0, z_mm=40.0),
        ]
        combined = _replace_samples(record, [*rising, *record.samples])

        truth = derive_truth(combined, None, layout=build_layout())

        assert isinstance(truth.impact_time_ms.value, float)
        assert truth.impact_time_ms.value > rising[-1].t_ms + 1.0


# ---------------------------------------------------------------------------
# リリース時刻（外挿。要件 4.3）
# ---------------------------------------------------------------------------


class TestReleaseTimeExtrapolation:
    """推定軌道を観測開始より前へ外挿する（要件 4.3）。"""

    def test_matches_analytic_solution(self) -> None:
        """外挿によるリリース時刻が解析解と一致する（tasks.md 4.1 の完了状態）。

        合成軌道は `RELEASE_T_MS` にリリース高さから出発しているので、
        解析解は `RELEASE_T_MS` そのものである。**観測開始（5100 ms）より
        前**に出ることも同時に固定する——ここが `docs/requirements.md §3`
        区間1（プロジェクトで最も未検証な量）の実測そのものである。
        """
        record = build_record()

        truth = derive_truth(record, measured_entry(), layout=build_layout())

        release = truth.release_time_ms
        assert release.method is TruthMethod.EXTRAPOLATED
        assert isinstance(release.value, float)
        assert abs(release.value - RELEASE_T_MS) <= RELEASE_TOLERANCE_MS
        assert release.value < record.samples[0].t_ms
        assert release.uncertainty_ms is not None
        assert release.source.strip()

    def test_missing_when_the_trajectory_never_reaches_release_height(self) -> None:
        """推定軌道がリリース高さに達しない場合は欠測（要件 4.6）。

        欠測の**理由**まで固定する。欠測が2通り（高さに達しない／通過が
        観測開始より後）あり、どちらの分岐で欠測になったのかを見ないと、
        片方の検査が消えていても気付けない（実際、最初に書いた検査は
        この空振りを踏んでいた——負の対照で判明）。
        """
        record = build_record()
        layout = build_layout(release_height_mm=5000.0)

        truth = derive_truth(record, measured_entry(), layout=layout)

        assert truth.release_time_ms.method is TruthMethod.MISSING
        assert truth.release_time_ms.value is None
        assert "リリース高さ" in truth.release_time_ms.source
        assert "達しない" in truth.release_time_ms.source
        # 他項目は止めない。
        assert truth.impact_time_ms.method is TruthMethod.INTERPOLATED
        assert truth.impact_point_world_mm.method is TruthMethod.MEASURED

    def test_missing_when_the_crossing_is_not_before_observation_start(self) -> None:
        """リリース高さの通過が観測開始より後なら欠測。

        要件 4.3 は「観測開始より**前**へ外挿し」と定める。観測区間の内側や
        その後ろにある交差を返すと、**外挿ではない値**が「リリース時刻」の
        名前で区間1 の実測へ入り込む（区間1 が負になる）。
        """
        # リリース高さを軌道の頂点（≒1704 mm）の少し下に置く。観測開始
        # （5100 ms）の時点ではまだこの高さに達しておらず、上昇側の通過は
        # 観測が始まったあとに来る。**軌道はこの高さに確かに達する**ので、
        # 「高さに達しない」分岐との区別が付く。
        height_mm = 1700.0
        record = build_record()
        layout = build_layout(release_height_mm=height_mm)
        assert position_at(FIRST_SAMPLE_T_MS)[2] < height_mm
        apex_mm = RELEASE_HEIGHT_MM + VZ_MM_MS**2 / (2.0 * G_MM_MS2)
        assert apex_mm > height_mm

        truth = derive_truth(record, None, layout=layout)

        assert truth.release_time_ms.method is TruthMethod.MISSING
        assert "観測開始より前にない" in truth.release_time_ms.source

    def test_missing_without_a_valid_prediction(self) -> None:
        """有効な予測が1件も無い記録では欠測（要件 4.6）。

        外挿は最終予測の軌道パラメータを用いる（design.md「TruthDeriver」
        Implementation Notes: **新しいフィッティングを実装しない**）。
        予測が無ければ外挿の土台が無い。
        """
        times = sample_times(until_ms=FIRST_SAMPLE_T_MS + DT_MS)
        record = build_record(times=times)
        assert not [item for item in record.predictions if isinstance(item, Prediction)]

        truth = derive_truth(record, measured_entry(), layout=build_layout())

        assert truth.release_time_ms.method is TruthMethod.MISSING
        assert truth.impact_point_world_mm.method is TruthMethod.MEASURED

    def test_uncertainty_grows_with_residual_and_extrapolation_span(self) -> None:
        """外挿の不確かさを**外挿区間の長さと残差**から導く（要件 4.4）。

        design.md「TruthDeriver」Risks が「外挿の不確かさが区間1 の実測値を
        そのまま左右する」と名指ししている量である。2つの入力それぞれに
        単調に反応することを固定する:

        - 残差が大きいほど不確かさが大きい（フィットが観測と合っていない）
        - 外挿区間が長いほど不確かさが大きい（観測から遠いほど当てにならない）

        2つ目は**残差あたりの不確かさ**で比べる。観測開始を遅らせると
        サンプル数も残差も変わるので、素の値どうしを比べると
        「残差が増えたから増えた」のか「外挿区間が伸びたから増えた」のかを
        区別できない——タスク2.2 / 3.1 が繰り返し踏んだ「その差が現れない
        入力でだけ検査している」空振りと同じ形になる。
        """
        offsets = {2: 40.0, 5: -35.0, 9: 25.0}
        doubled = {index: value * 2.0 for index, value in offsets.items()}
        clean = build_record()
        noisy = build_record(z_offsets=offsets)
        louder = build_record(z_offsets=doubled)

        layout = build_layout()
        clean_unc = derive_truth(clean, None, layout=layout).release_time_ms
        noisy_unc = derive_truth(noisy, None, layout=layout).release_time_ms
        louder_unc = derive_truth(louder, None, layout=layout).release_time_ms
        assert clean_unc.uncertainty_ms is not None
        assert noisy_unc.uncertainty_ms is not None
        assert louder_unc.uncertainty_ms is not None

        # 観測が厳密に放物線の上に乗っていれば残差はほぼ 0 であり、
        # **残差から導いた**不確かさもほぼ 0 になる。定数を返す実装は
        # ここで落ちる（「大小が付いていればよい」では通してしまう）。
        assert clean_unc.uncertainty_ms < 1e-9

        # ずれを2倍にすると残差が2倍になり、不確かさも2倍になる。
        # **比例することまで見る**——大小関係だけの検査は、外挿区間の
        # わずかな違いで偶然通ってしまう（負の対照で確認済み）。
        residual_ratio = final_prediction(louder).residual / final_prediction(
            noisy
        ).residual
        assert residual_ratio == pytest.approx(2.0, rel=1e-6)
        assert louder_unc.uncertainty_ms / noisy_unc.uncertainty_ms == pytest.approx(
            residual_ratio, rel=0.05
        )

        # 大きさそのものも、合成軌道の**解析量から独立に組み立てて**突き合わせる。
        # 残差（mm）はリリース高さ通過時の鉛直速度（設計上 `VZ_MM_MS` mm/ms）で
        # 時間へ換算されねばならない。この換算を落とすと 2 倍ずれる——
        # 「mm を ms として報告する」型の誤りは、比例関係だけを見る検査では
        # 素通りする（負の対照で確認済み）。
        observed_span_ms = noisy.samples[-1].t_ms - noisy.samples[0].t_ms
        expected_ms = (final_prediction(noisy).residual / VZ_MM_MS) * (
            1.0 + (FIRST_SAMPLE_T_MS - RELEASE_T_MS) / observed_span_ms
        )
        assert noisy_unc.uncertainty_ms == pytest.approx(expected_ms, rel=0.05)

        # 観測開始を 200 ms 遅らせる（＝外挿区間が 100 ms → 300 ms、
        # 観測区間は短くなる）。残差で正規化した量が増えることを見る。
        far = build_record(
            times=sample_times(
                until_ms=analytic_impact_time_ms() + DT_MS,
                first_ms=FIRST_SAMPLE_T_MS + 200.0,
            ),
            z_offsets=offsets,
        )
        far_unc = derive_truth(far, None, layout=layout).release_time_ms
        assert far_unc.uncertainty_ms is not None
        near_per_residual = noisy_unc.uncertainty_ms / final_prediction(noisy).residual
        far_per_residual = far_unc.uncertainty_ms / final_prediction(far).residual
        assert far_per_residual > near_per_residual


# ---------------------------------------------------------------------------
# 落下地点（実測。要件 4.1）と外部の合図（要件 4.5）
# ---------------------------------------------------------------------------


class TestMeasuredImpactPoint:
    """外部から受け取る落下地点（要件 4.1）。"""

    def test_carries_value_method_uncertainty_and_source(self) -> None:
        record = build_record()

        truth = derive_truth(record, measured_entry(), layout=build_layout())

        point = truth.impact_point_world_mm
        assert point.value == (1240.0, -310.0, 0.0)
        assert point.method is TruthMethod.MEASURED
        assert point.uncertainty_mm == 15.0
        assert point.uncertainty_ms is None
        assert "メジャー実測" in point.source

    @pytest.mark.parametrize("source", ["", "   ", "\t\n"])
    def test_rejects_a_point_without_a_measurement_description(
        self, source: str
    ) -> None:
        """**測り方の記述を必須とする**（要件 4.1）。空白のみも拒否する。

        「1240 mm」とだけ書かれた落下地点は、±5 mm で測ったのか ±100 mm
        なのかが分からず、そこから出した誤差が意味を持たない。
        """
        record = build_record()

        with pytest.raises(M1ConfigError):
            derive_truth(
                record, measured_entry(impact_point_source=source), layout=build_layout()
            )

    def test_rejects_a_point_without_an_uncertainty(self) -> None:
        """不確かさの目安の併記も必須である（要件 4.4）。"""
        entry = measured_entry()
        del entry["impact_point_uncertainty_mm"]
        record = build_record()

        with pytest.raises(M1ConfigError):
            derive_truth(record, entry, layout=build_layout())

    def test_missing_entry_yields_a_missing_point_without_raising(self) -> None:
        """真値がまだ記入されていない投擲でも例外にしない（要件 4.6）。

        design.md「Error Categories and Responses」は真値の欠測を
        **値として扱う**と定めている。ここで例外にすると、1件の未記入が
        投擲群全体の集計を止める。
        """
        record = build_record()

        for entry in (None, {}, {"notes": "落下地点はまだ測っていない"}):
            truth = derive_truth(record, entry, layout=build_layout())
            assert truth.impact_point_world_mm.method is TruthMethod.MISSING
            assert truth.impact_point_world_mm.value is None
            assert truth.impact_point_world_mm.uncertainty_mm is None
            assert truth.impact_point_world_mm.source.strip()
            # 内挿・外挿は落下地点に依存しないので続く。
            assert truth.impact_time_ms.method is TruthMethod.INTERPOLATED
            assert truth.release_time_ms.method is TruthMethod.EXTRAPOLATED

    def test_record_id_is_carried(self) -> None:
        record = build_record(record_id="throw-0042")
        truth = derive_truth(record, None, layout=build_layout())
        assert truth.record_id == "throw-0042"


class TestExternalReleaseMark:
    """外部の合図との突き合わせ（要件 4.5）。"""

    def test_delta_against_the_extrapolated_release_time(self) -> None:
        """外部の合図が記録されていれば、外挿値との**差を残す**。"""
        record = build_record()
        entry = measured_entry(external_release_mark_ms=RELEASE_T_MS + 12.0)

        truth = derive_truth(record, entry, layout=build_layout())

        assert truth.external_mark_delta_ms is not None
        assert truth.release_time_ms.value is not None
        assert truth.external_mark_delta_ms == pytest.approx(
            RELEASE_T_MS + 12.0 - truth.release_time_ms.value
        )
        assert truth.external_mark_delta_ms == pytest.approx(12.0, abs=1e-6)

    def test_no_delta_without_a_mark(self) -> None:
        record = build_record()
        truth = derive_truth(record, measured_entry(), layout=build_layout())
        assert truth.external_mark_delta_ms is None

    def test_no_delta_when_the_release_time_is_missing(self) -> None:
        """外挿が欠測なら差も出さない（0 で埋めない）。"""
        record = build_record()
        entry = measured_entry(external_release_mark_ms=RELEASE_T_MS)

        truth = derive_truth(record, entry, layout=build_layout(release_height_mm=5000.0))

        assert truth.release_time_ms.method is TruthMethod.MISSING
        assert truth.external_mark_delta_ms is None


# ---------------------------------------------------------------------------
# 真値ファイルの受け取り（要件 4.7）
# ---------------------------------------------------------------------------


class TestTruthFile:
    """投擲の実行と分離された真値ファイル（要件 4.7）。"""

    def test_loads_entries_by_record_id(self, tmp_path: Path) -> None:
        path = write_truth_file(tmp_path)

        entries = load_truth_file(path)

        assert set(entries) == {"throw-0007"}
        assert entries["throw-0007"]["impact_point_uncertainty_mm"] == 15.0

    def test_rejects_an_unknown_format_version(self, tmp_path: Path) -> None:
        """未知の形式版を**推測して読まない**（上流3 Spec と同じ方針）。"""
        path = write_truth_file(tmp_path, truth_format_version="9.9")

        with pytest.raises(M1ConfigError):
            load_truth_file(path)

    def test_rejects_unknown_keys_in_an_entry(self, tmp_path: Path) -> None:
        """綴り間違いを黙って捨てない。

        `impact_point_uncertainty` と書き間違えた不確かさが黙って無視され
        ると、**測ったはずの不確かさが結果から消える**。
        """
        path = write_truth_file(
            tmp_path,
            entries={"throw-0007": measured_entry(impact_point_uncertainy_mm=15.0)},
        )

        with pytest.raises(M1ConfigError):
            load_truth_file(path)

    def test_rejects_an_empty_source_at_load_time(self, tmp_path: Path) -> None:
        """測り方の記述の必須性は、**取り込みの時点で**拒否する（要件 13.6）。"""
        path = write_truth_file(
            tmp_path, entries={"throw-0007": measured_entry(impact_point_source=" ")}
        )

        with pytest.raises(M1ConfigError):
            load_truth_file(path)

    def test_rejects_a_layout_id_mismatch(self, tmp_path: Path) -> None:
        """別レイアウトの真値を混ぜない。

        誤差の帰属は**投擲位置ごとに向きが変わること**を使って原因を
        切り分ける（`research.md` Decision 4）。レイアウトを取り違えた
        真値が混ざると、その結論が直接ねじ曲がる。
        """
        path = write_truth_file(tmp_path, layout_id="throw-b")

        with pytest.raises(M1ConfigError):
            load_truth_file(path, expected_layout_id="throw-a")

        assert load_truth_file(path, expected_layout_id="throw-b")

    def test_rejects_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(M1ConfigError):
            load_truth_file(tmp_path / "absent.json")


class TestIngest:
    """記録への対応付け（要件 4.7、design.md `ingest-truth`）。"""

    def test_attaches_truth_to_each_record(self) -> None:
        records = (_with_m1_extra(build_record(record_id="throw-0007")),)
        entries = {"throw-0007": measured_entry()}

        result = ingest_truth(records, entries, layout=build_layout())

        assert len(result.truths) == 1
        assert result.truths[0].impact_point_world_mm.method is TruthMethod.MEASURED
        assert result.unknown_record_ids == ()
        payload = result.records[0].extra["m1"]
        assert isinstance(payload, Mapping)
        assert payload["truth"] == truth_to_dict(result.truths[0])

    def test_reports_unknown_record_ids_instead_of_dropping_them(self) -> None:
        """記録に存在しない識別子の真値は**警告として報告し、黙って捨てない**。

        黙って捨てると、`throw-0007` と書くべきところを `throw-007` と書いた
        真値が「未記入」と区別できなくなる——測ったのに反映されていない
        ことに誰も気付けない。
        """
        records = (_with_m1_extra(build_record(record_id="throw-0007")),)
        entries = {
            "throw-0007": measured_entry(),
            "throw-007": measured_entry(),
            "throw-9999": measured_entry(),
        }

        result = ingest_truth(records, entries, layout=build_layout())

        assert result.unknown_record_ids == ("throw-007", "throw-9999")
        assert len(result.truths) == 1

    def test_records_without_an_entry_are_still_derived(self) -> None:
        """真値ファイルに無い投擲も、内挿・外挿の真値だけは求める（要件 4.6）。"""
        records = (_with_m1_extra(build_record(record_id="throw-0007")),)

        result = ingest_truth(records, {}, layout=build_layout())

        assert result.truths[0].impact_point_world_mm.method is TruthMethod.MISSING
        assert result.truths[0].impact_time_ms.method is TruthMethod.INTERPOLATED


class TestAttachTruth:
    """実行後の追記（要件 4.7）。"""

    def test_appends_without_disturbing_other_extras(self) -> None:
        """`extra["m1"]["truth"]` へ後から書き、他の拡張キーを壊さない。

        `sensing_foundation` が `extra["sensing"]` を後から足す（要件 3.5）
        ので、`extra` を丸ごと置き換えると**セッションへの対応付けが消える**。
        """
        record = _with_m1_extra(build_record(), sensing={"session_path": "var/x"})
        truth = derive_truth(record, measured_entry(), layout=build_layout())

        updated = attach_truth(record, truth)

        payload = updated.extra["m1"]
        assert isinstance(payload, Mapping)
        assert payload["truth"] == truth_to_dict(truth)
        # 既存の拡張は残る。
        assert updated.extra["sensing"] == {"session_path": "var/x"}
        assert payload["m1_extra_version"] == M1_EXTRA_VERSION
        assert payload["verified"] is True
        # 元の記録は書き換わらない（値オブジェクトである）。
        original = record.extra["m1"]
        assert isinstance(original, Mapping)
        assert original["truth"] is None

    def test_serialised_truth_is_json_safe(self) -> None:
        """`allow_nan=False` で直列化できる（design.md「集計・判断の出力」）。"""
        truth = derive_truth(build_record(), measured_entry(), layout=build_layout())

        text = json.dumps(truth_to_dict(truth), allow_nan=False, ensure_ascii=False)

        restored = json.loads(text)
        assert restored["impact_point_world_mm"]["method"] == "measured"
        assert restored["impact_time_ms"]["method"] == "interpolated"
        assert restored["release_time_ms"]["method"] == "extrapolated"
        assert restored["release_time_ms"]["uncertainty_ms"] is not None

    def test_rejects_a_record_without_the_m1_extension(self) -> None:
        """本 Spec の拡張を持たない記録へは追記しない。

        `m1_extra_version` の無い記録に `truth` だけを差し込むと、版の分から
        ない拡張領域ができる（design.md「Data Models」: 読み出し時に
        `schema_version` と `m1_extra_version` の**両方**の既知性を検査する）。
        """
        record = build_record()
        truth = derive_truth(record, None, layout=build_layout())

        with pytest.raises(M1ConfigError):
            attach_truth(record, truth)

    def test_rejects_an_unknown_m1_extra_version(self) -> None:
        record = _with_m1_extra(build_record(), m1_extra_version="9.9")
        truth = derive_truth(record, None, layout=build_layout())

        with pytest.raises(M1ConfigError):
            attach_truth(record, truth)


class TestDocumentedIntent:
    """文書化そのものが要求されている項目（tasks.md 4.1 の最後の箇条）。"""

    def test_module_docstring_states_why_the_method_is_mandatory(self) -> None:
        """**求め方を書かずに数値だけを出すと誤差の出どころを議論できない**。

        A-4 が本 Spec の存在理由として挙げている文であり、実装の docstring
        から消えると「値だけ返す真値」が後から足される。文書化要求に対して
        打てる検査はこれだけである（タスク1.4 と同じ形）。
        """
        from m1_validation import truth as truth_module

        doc = truth_module.__doc__ or ""
        assert "求め方" in doc
        assert "誤差の出どころ" in doc
        assert "議論できない" in doc

    def test_floor_height_is_pinned_to_zero(self) -> None:
        """床面高さは `z = 0` に固定されており、本 Spec が決め直してはならない。

        `src/prediction_core/impact.py` は落下地点を「床面は z = 0 固定」
        として平面との交点に定義しており、`prediction_core/types.py` の
        `Sample.z_mm` も「床面は z = 0 と仮定する」と書いている。World frame
        は床平面を `z = 0` として確立される（`world-frame-calibration`）。

        **内挿の落下時刻と予測の落下時刻が違う平面を指すと、その差は誤差
        ではなく定義の食い違いになる**（`truth.py` の `FLOOR_HEIGHT_MM` の
        docstring が自ら名指ししている危険）。それでいて症状は「予測が
        悪い」にしか見えない。

        この検査を置くまで、`FLOOR_HEIGHT_MM` を 50.0 に変えても全件が
        通っていた——参照解の側が同じ定数から組まれていたためである。
        床を 50 mm にすると内挿値は真の z=0 解から 8.71 ms ずれる。
        本ファイルが報告する不確かさ（約 0.059 ms）の 147 倍であり、
        要件 5.5（落下時刻誤差の系列）が丸ごと偏る。
        """
        assert FLOOR_HEIGHT_MM == 0.0

    def test_throw_truth_is_exported_from_types(self) -> None:
        """`ThrowTruth` は `types.py` に置く（design.md「File Structure Plan」）。"""
        assert ThrowTruth.__module__ == "m1_validation.types"


# ---------------------------------------------------------------------------
# テスト用のヘルパ
# ---------------------------------------------------------------------------


def _replace_samples(record: ThrowRecord, samples: Sequence[Sample]) -> ThrowRecord:
    return ThrowRecord(
        record_id=record.record_id,
        source=record.source,
        config=record.config,
        samples=tuple(samples),
        predictions=record.predictions,
        schema_version=record.schema_version,
        extra=record.extra,
    )


def _with_m1_extra(record: ThrowRecord, **extra: object) -> ThrowRecord:
    """`runner.py` が付ける `extra["m1"]` を模した記録を作る。

    `runner.run_throw()` を通すには上流のフレーム供給が要るので、ここでは
    拡張領域の形だけを再現する（キーは design.md「Data Models」の表）。
    """
    m1_version = extra.pop("m1_extra_version", M1_EXTRA_VERSION)
    payload: dict[str, object] = {
        "m1_extra_version": m1_version,
        "layout": {"layout_id": "throw-a"},
        "calibration": {"calibration_id": "cal-test-0001"},
        "tracking": None,
        "provenance": [],
        "rejected": [],
        "truth": None,
        "verified": True,
        "failed_reason": None,
    }
    return ThrowRecord(
        record_id=record.record_id,
        source=record.source,
        config=record.config,
        samples=record.samples,
        predictions=record.predictions,
        schema_version=record.schema_version,
        extra={**record.extra, "m1": payload, **extra},
    )
