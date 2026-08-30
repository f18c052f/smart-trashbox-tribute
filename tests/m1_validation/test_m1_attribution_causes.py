"""既知の原因を注入して、帰属がその原因を指すことを確かめる（タスク 5.3、要件 6.3-6.7 / 6.10）。

**本ファイルは負の対照（negative control）である。** タスク 5.2 の
`test_m1_attribution.py` は、判定関数へ**既に分解された量**（偏りベクトル・
ばらつき・残差）を直接与えて、規則1〜7 の分岐が規則の文言どおりに書けて
いることを固定した。それは「規則として正しく書けているか」を見る問いで
あって、「**本当に既知の原因を指し当てるか**」は別の問いである。

本ファイルは**軌道の段から原因を注入し**、
`観測 → 予測（prediction_core.predict）→ 誤差（measure_accuracy）→
集計（aggregate）→ 帰属（attribute）` の経路を丸ごと通したうえで、
帰属が注入した原因を指すことを確かめる。注入する原因は5種類:

1. **World 座標系に固定された偏り** → キャリブレーション由来（要件 6.4）
2. **カメラ視線方向の偏り**（投擲位置ごとに World 上の向きが変わる）
   → 検出由来の候補（要件 6.5）
3. **観測ノイズのみ**（偏り無し・等方） → 観測ノイズ由来（要件 6.6）
4. **投擲位置が1箇所で2方向が縮退する入力** → 判別不能（要件 6.10）
5. **非放物的な軌道**（速度に比例する空気抵抗を入れる） → モデル由来（要件 6.7）

**この分岐こそが本 Spec の存在意義そのものである。** `docs/requirements.md
§6.2` が警告するとおり、座標系が数 cm ずれていても症状は「予測が悪い」に
しか見えない。キャリブレーションのずれも、Depth が対象物のカメラ側表面を
測ることによる寄りも、予測モデルの当てはまりの悪さも、**落下地点が外れる**
という同じ1つの症状に潰れる。潰れたままでは、直すべき側が分からないまま
時間だけが溶ける。帰属が**原因を指せなかったら本 Spec を実施する意味が
無い**ので、指せることを注入実験で確かめるのが本ファイルの役割である。

**1 と 2 を分けるには投擲位置が複数要る。** World 座標系に固定された向きは
投擲位置が変わっても同じ向きのままだが、カメラ視線方向は投擲位置ごとに
World 上で別の向きになる。したがって投擲位置が2箇所以上あれば両者は別々の
向きとして現れ、判別できる。**4 はまさにそれが1箇所しか無い場合**であり、
2つの向きが同じ軸へ縮退して原理的に決められない
（`research.md` Decision 4 の Follow-up、design.md「ErrorAttributor」規則4）。
`TestThrowPositionCountDecidesTheAnswer` は、**注入した原因もキャリブレー
ション検証レポートも完全に同一のまま、投擲位置の数だけを 1 と 2 で振って**、
判別不能とキャリブレーション由来が入れ替わることを固定する——これが本
Spec の存在意義を最短で言い表した1件である。

**期待値は実装に触れずに組む。** 軌道・重力・注入量・カメラ位置・検証
レポートの値はすべて本ファイルのリテラルであり、`m1_validation` の閾値・
説明文・既定値を期待値へ持ち込まない（tasks.md「Implementation Notes」
タスク4.1 / 5.2）。比較してよいのは `Attribution` 列挙のメンバだけである。

**「期待どおりの帰属が返る」を1件ずつ見るだけでは負の対照にならない。**
5種類がどれも同じ判定へ落ちる実装でも、そのうち何件かは通ってしまう。
`TestTheFiveCausesAreToldApart` が**5種類が互いに異なる帰属を返すこと**を
固定し、`TestInjectedDirectionDecidesTheAnswer` /
`TestObservationNoiseAmountFlowsThrough` /
`TestTrajectoryModelDecidesTheScatterAnswer` が**入力側を変異させると判定が
動くこと**（偏りの向きを反転する・World 固定をカメラ視線へ差し替える・
ノイズ量を変える・軌道を非放物にする）を固定する。帰属先のラベルだけでなく
**適用された規則と成分の値**まで見るのは、別の規則が偶然同じ答えを出した
場合を見逃さないためである。
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import pytest
from m1fixtures import write_layout

from m1_validation.attribution import AttributionResult, attribute
from m1_validation.config import M1Settings
from m1_validation.metrics.accuracy import measure_accuracy
from m1_validation.metrics.aggregate import ThrowMetrics, aggregate
from m1_validation.metrics.convergence import analyze_convergence
from m1_validation.metrics.flight import measure_flight
from m1_validation.truth import attach_truth, derive_truth
from m1_validation.types import Attribution
from prediction_core import (
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowRecord,
    predict,
)

# ---------------------------------------------------------------------------
# 測定条件（すべて本ファイルのリテラル。実装の既定値を参照しない）
# ---------------------------------------------------------------------------

#: 重力加速度（mm/s^2）。**予測コアの既定値を読まず、ここで明示して
#: `PredictionConfig` へ渡す。** 合成軌道と予測器が同じ重力を使っていないと、
#: 注入していない誤差が勝手に生まれて原因の切り分けにならない。
GRAVITY_MM_S2 = 9806.65
G_MM_MS2 = GRAVITY_MM_S2 / 1_000_000.0

CONFIG = PredictionConfig(gravity_mm_s2=GRAVITY_MM_S2)

#: カメラ位置（World mm）。`m1fixtures.write_layout` が書く値と同じである
#: ことは `TestLayoutInvariants` が独立に固定する（自己比較にしない）。
CAMERA_WORLD_MM = (0.0, -1500.0, 1000.0)

#: 群のキャリブレーション識別子。
CAL_ID = "cal-causes-0031"

#: 観測の刻み（60 fps）と本数。**基準時刻は 0 にしない**——0 を基準にすると
#: 時刻の再基準化や差分の取り違えがまるごと素通りする（tasks.md
#: 「Implementation Notes」タスク2.2）。
T_BASE_MS = 4000.0
FIRST_SAMPLE_S_MS = 60.0
SAMPLE_INTERVAL_MS = 1000.0 / 60.0
SAMPLE_COUNT = 30

#: リリース時の鉛直速度（mm/ms）。**0 にしない**（同上）。
RELEASE_VZ_MM_MS = 1.0
RELEASE_HEIGHT_MM = 1500.0

#: 逐次予測を出すサンプル数。最後の予測が全サンプルに基づく最終予測であり、
#: 集計はそれを誤差ベクトルとして採る。
PREDICTION_PREFIXES = (3, 15, SAMPLE_COUNT)

#: 注入する共通偏りの大きさ（mm）。
INJECTED_BIAS_MM = 30.0

#: 注入する観測ノイズの標準偏差（mm、軸ごとに独立）。
NOISE_SIGMA_MM = 2.0

#: ノイズ量を振ったときの倍率。**10 倍差**にしてあるのは、ばらつきが
#: ノイズ量に応じて増えることを大小比較ではなく**比**で確かめるためである。
NOISE_SCALE = 10.0

#: 注入ノイズの乱数種（**テスト局所**であり、実装の再抽出の種とは無関係）。
NOISE_SEED = 271828

#: 再抽出の回数と種（テストを速く保つ小さい値。実装の既定値には落ちない）。
BOOTSTRAP_ITERATIONS = 24
BOOTSTRAP_SEED = 7

#: 空気抵抗の係数（1/ms）。速度に比例する減速であり、**放物線では表せない**
#: 軌道を作る。予測コアは放物線しか当てはめられないので、残差が大きくなり、
#: 落下地点は飛行方向へ行き過ぎる側へ外れる。
DRAG_PER_MS = 0.0005
STRONG_DRAG_PER_MS = 0.0010

#: 検証レポートのばらつき（mm）。平均オフセットが「認められる」かどうかは
#: レポート自身のばらつきとの相対比較で決まる（design.md 規則2 / 3）。
CAL_SCATTER_RMS_MM = 4.0

#: World の +X を向く、**認められる**大きさの平均オフセット。
CAL_BIAS_ALONG_X_MM = [30.0, 0.0, 0.0]

#: 向きは +X だが、レポートのばらつきに対して小さく、**認められない**
#: 平均オフセット。「認められない」と「そもそも測っていない」を混ぜない
#: ため、0 ベクトルではなく**小さな非 0** を使う。
CAL_BIAS_NEGLIGIBLE_MM = [3.0, 0.0, 0.0]

#: 偏りが 0 と**記録された**レポート。「小さい」とも「測っていない」とも
#: 別の入力であり、3つとも別に用意する。
CAL_BIAS_ZERO_MM = [0.0, 0.0, 0.0]

#: 真値の測り方（要件 4.1 が必須にしている記述）と不確かさ。
TRUTH_SOURCE = "床のマークをメジャーで実測"
TRUTH_UNCERTAINTY_MM = 5.0


# ---------------------------------------------------------------------------
# 既知の軌道と、原因の注入
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThrowPlan:
    """投擲位置1つぶんの既知の軌道（本ファイルのリテラルだけで決まる）。

    リリース点から目標点まで水平等速で飛び、鉛直は初速 `RELEASE_VZ_MM_MS`
    の放物運動をする。床（z = 0）を横切る点がその投擲の**落下地点の真値**
    である。

    Attributes:
        label: 投擲位置の名前（記録の識別子に使う）。
        release_world_mm: リリース点（World mm）。
        target_world_mm: 落下地点（World mm の水平2成分）。
    """

    label: str
    release_world_mm: tuple[float, float, float]
    target_world_mm: tuple[float, float]

    @property
    def flight_time_ms(self) -> float:
        """リリースから床面到達までの時間（ms）。"""
        z0 = self.release_world_mm[2]
        discriminant = RELEASE_VZ_MM_MS**2 + 2.0 * G_MM_MS2 * z0
        return (RELEASE_VZ_MM_MS + math.sqrt(discriminant)) / G_MM_MS2

    @property
    def velocity_mm_ms(self) -> tuple[float, float]:
        """水平速度（mm/ms）。目標点へちょうど届く速さに決める。"""
        s_f = self.flight_time_ms
        return (
            (self.target_world_mm[0] - self.release_world_mm[0]) / s_f,
            (self.target_world_mm[1] - self.release_world_mm[1]) / s_f,
        )

    def position_at(self, s_ms: float) -> tuple[float, float, float]:
        """リリースからの経過 `s_ms` における真の位置（World mm）。"""
        vx_mm_ms, vy_mm_ms = self.velocity_mm_ms
        x0_mm, y0_mm, z0_mm = self.release_world_mm
        return (
            x0_mm + vx_mm_ms * s_ms,
            y0_mm + vy_mm_ms * s_ms,
            z0_mm + RELEASE_VZ_MM_MS * s_ms - 0.5 * G_MM_MS2 * s_ms * s_ms,
        )

    @property
    def impact_world_mm(self) -> tuple[float, float, float]:
        """落下地点の真値（World mm）。"""
        return (self.target_world_mm[0], self.target_world_mm[1], 0.0)


#: 観測時刻（リリースからの経過 ms）。飛行のうち検出できている区間を表す。
SAMPLE_TIMES_S_MS = tuple(
    FIRST_SAMPLE_S_MS + index * SAMPLE_INTERVAL_MS for index in range(SAMPLE_COUNT)
)

#: 投擲位置A（カメラから見て左手、+Y へ飛ぶ）。
THROW_A = ThrowPlan("a", (-1000.0, 200.0, RELEASE_HEIGHT_MM), (-1000.0, 1800.0))

#: 投擲位置B（カメラから見て右手、**−Y へ飛ぶ**）。A と飛行方向が逆なので、
#: 空気抵抗による誤差は A と逆向きに出て、投擲群の平均としては打ち消し合う
#: ——**ばらつきだけが大きい群**を作れる（原因5）。
THROW_B = ThrowPlan("b", (1000.0, 1800.0, RELEASE_HEIGHT_MM), (1000.0, 200.0))

#: 2箇所の投擲位置（各4投）。**A と B ではカメラ視線方向が World 上で
#: 40 度以上ずれる**ので、World 固定の向きとカメラ視線方向を判別できる。
TWO_POSITIONS: tuple[ThrowPlan, ...] = (THROW_A,) * 4 + (THROW_B,) * 4

#: 1箇所だけの投擲（8投とも A）。**2方向が縮退しうる配置**である。
ONE_POSITION: tuple[ThrowPlan, ...] = (THROW_A,) * 8


def camera_ray_unit_to(point_world_mm: tuple[float, float, float]) -> tuple[float, float, float]:
    """カメラからその点へ向かう単位ベクトル（World、3成分）。**本ファイルで解く。**

    `seam.camera_ray_unit()` を呼ばないのは、実装の写経にしないためである。
    """
    dx_mm = point_world_mm[0] - CAMERA_WORLD_MM[0]
    dy_mm = point_world_mm[1] - CAMERA_WORLD_MM[1]
    dz_mm = point_world_mm[2] - CAMERA_WORLD_MM[2]
    norm = math.sqrt(dx_mm * dx_mm + dy_mm * dy_mm + dz_mm * dz_mm)
    return (dx_mm / norm, dy_mm / norm, dz_mm / norm)


def mean_camera_ray_horizontal(plan: ThrowPlan) -> tuple[float, float]:
    """その投擲を代表するカメラ視線方向（World の水平2成分、単位ベクトル）。

    観測点ごとの視線方向を平均して水平成分を正規化する。1投擲のあいだに
    対象が動くので視線方向もわずかに動くが、**投擲群に共通する偏りが
    どちらを向くか**を見るには投擲を代表する1方向で足りる。
    """
    sum_x = 0.0
    sum_y = 0.0
    for s_ms in SAMPLE_TIMES_S_MS:
        unit = camera_ray_unit_to(plan.position_at(s_ms))
        sum_x += unit[0]
        sum_y += unit[1]
    norm = math.hypot(sum_x, sum_y)
    return (sum_x / norm, sum_y / norm)


#: 注入する偏りを決める関数。投擲ごとに**全観測へ同じだけ**足す水平ベクトル
#: （World mm）を返す。定数オフセットにしてあるのは、放物線の当てはめが
#: 位置に対して線形なので、**落下地点がちょうど同じ量だけずれる**からで
#: ある——注入量と結果の対応が厳密になり、余計な量が混ざらない。
Injection = Callable[[ThrowPlan], tuple[float, float]]


def no_injection(plan: ThrowPlan) -> tuple[float, float]:
    """偏りを注入しない（原因3 / 原因5 で使う）。"""
    del plan
    return (0.0, 0.0)


def world_fixed_injection(vector_mm: tuple[float, float]) -> Injection:
    """**World 座標系に固定された偏り**を注入する（原因1）。

    投擲位置が変わっても World 上で同じ向き・同じ大きさである。座標系の
    ずれ（キャリブレーション由来）はこの形で現れる。
    """

    def inject(plan: ThrowPlan) -> tuple[float, float]:
        del plan
        return vector_mm

    return inject


def camera_ray_injection(magnitude_mm: float) -> Injection:
    """**カメラ視線方向の偏り**を注入する（原因2）。

    投擲ごとに、その投擲のカメラ視線方向に沿って**カメラ側へ**寄せる。
    Depth が対象物のカメラ側表面を測ることによる系統的な寄りがこの形で
    現れる。**World 上の向きは投擲位置ごとに変わる**ので、原因1 とは
    別物として現れなければならない。
    """

    def inject(plan: ThrowPlan) -> tuple[float, float]:
        unit_x, unit_y = mean_camera_ray_horizontal(plan)
        return (-magnitude_mm * unit_x, -magnitude_mm * unit_y)

    return inject


def _drag_trajectory(
    plan: ThrowPlan, drag_per_ms: float
) -> tuple[list[tuple[float, float, float]], tuple[float, float, float], float]:
    """速度に比例する空気抵抗を入れて数値積分する（原因5）。

    加速度は `-g·ẑ − k·v` である。**放物線では表せない軌道**になるので、
    予測コアの当てはめには必ず残差が残り、落下地点も外れる。真値（落下
    地点）も同じ積分から採るので、注入した非放物性だけが誤差になる。

    Returns:
        `(刻みごとの位置, 落下地点, 刻み幅 ms)`。
    """
    step_ms = 0.25
    velocity = [*plan.velocity_mm_ms, RELEASE_VZ_MM_MS]
    point = list(plan.release_world_mm)
    points: list[tuple[float, float, float]] = [tuple(point)]  # type: ignore[arg-type]
    impact: tuple[float, float, float] | None = None
    for _ in range(20_000):
        previous = (point[0], point[1], point[2])
        acceleration = (
            -drag_per_ms * velocity[0],
            -drag_per_ms * velocity[1],
            -G_MM_MS2 - drag_per_ms * velocity[2],
        )
        for axis in range(3):
            point[axis] += (
                velocity[axis] * step_ms
                + 0.5 * acceleration[axis] * step_ms * step_ms
            )
            velocity[axis] += acceleration[axis] * step_ms
        points.append((point[0], point[1], point[2]))
        if impact is None and point[2] <= 0.0 < previous[2]:
            fraction = previous[2] / (previous[2] - point[2])
            impact = (
                previous[0] + fraction * (point[0] - previous[0]),
                previous[1] + fraction * (point[1] - previous[1]),
                0.0,
            )
            break
    assert impact is not None, "空気抵抗つきでも床には届くはずである"
    return points, impact, step_ms


def _interpolate(
    points: Sequence[tuple[float, float, float]], step_ms: float, s_ms: float
) -> tuple[float, float, float]:
    """積分した軌道を線形内挿して任意時刻の位置を返す。"""
    index = min(int(s_ms / step_ms), len(points) - 2)
    lower = points[index]
    upper = points[index + 1]
    fraction = (s_ms - index * step_ms) / step_ms
    return (
        lower[0] + fraction * (upper[0] - lower[0]),
        lower[1] + fraction * (upper[1] - lower[1]),
        lower[2] + fraction * (upper[2] - lower[2]),
    )


def _true_positions(
    plan: ThrowPlan, *, drag_per_ms: float
) -> tuple[list[tuple[float, float, float]], tuple[float, float, float]]:
    """観測時刻ごとの真の位置と、落下地点の真値。"""
    if not drag_per_ms:
        return (
            [plan.position_at(s_ms) for s_ms in SAMPLE_TIMES_S_MS],
            plan.impact_world_mm,
        )
    points, impact, step_ms = _drag_trajectory(plan, drag_per_ms)
    return (
        [_interpolate(points, step_ms, s_ms) for s_ms in SAMPLE_TIMES_S_MS],
        impact,
    )


def _build_record(
    plan: ThrowPlan,
    *,
    record_id: str,
    injection: Injection,
    noise_sigma_mm: float,
    drag_per_ms: float,
    calibration_bias_mm: list[float] | None,
    rng: random.Random,
) -> tuple[ThrowRecord, dict[str, object]]:
    """1投擲ぶんの記録と、真値ファイルの記入を組む。

    観測は「真の位置 + 注入した偏り + 観測ノイズ」であり、そこから
    `prediction_core.predict` を逐次に呼んで予測系列を作る。**予測は
    実際に走らせる**——注入した原因が予測を経由して誤差に現れることを
    見るのが本ファイルの主旨なので、予測値を手で置いたら経路が切れる。

    `extra["m1"]` の形は `runner._with_m1_extra()` が書くものと同じであり、
    帰属は記録に埋め込まれた要約からしか検証レポートを読まない（要件 13.1）。
    観測点ごとの視線方向（`provenance[*].camera_ray_unit`）は
    **観測した位置**から解く——センサが見るのは注入後の位置だからである。
    """
    true_points, impact_world_mm = _true_positions(plan, drag_per_ms=drag_per_ms)
    offset_x_mm, offset_y_mm = injection(plan)

    samples: list[Sample] = []
    provenance: list[dict[str, object]] = []
    for s_ms, point in zip(SAMPLE_TIMES_S_MS, true_points, strict=True):
        x_mm = point[0] + offset_x_mm
        y_mm = point[1] + offset_y_mm
        z_mm = point[2]
        if noise_sigma_mm:
            x_mm += rng.gauss(0.0, noise_sigma_mm)
            y_mm += rng.gauss(0.0, noise_sigma_mm)
            z_mm += rng.gauss(0.0, noise_sigma_mm)
        samples.append(Sample(t_ms=T_BASE_MS + s_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
        provenance.append(
            {"camera_ray_unit": list(camera_ray_unit_to((x_mm, y_mm, z_mm)))}
        )

    predictions = tuple(
        predict(tuple(samples[:count]), CONFIG) for count in PREDICTION_PREFIXES
    )
    record = ThrowRecord(
        record_id=record_id,
        source=SourceKind.LIVE,
        config=CONFIG,
        samples=tuple(samples),
        predictions=predictions,
        extra={
            "m1": {
                "m1_extra_version": "1.0",
                "calibration": {
                    "calibration_id": CAL_ID,
                    "verification_state": "passed",
                    "verified": True,
                    "verified_at_wall_ms": 1_700_000_500_000.0,
                    "bias_mm": calibration_bias_mm,
                    "scatter_rms_mm": CAL_SCATTER_RMS_MM,
                    "max_error_norm_mm": 9.0,
                    "point_count": 6,
                    "independent_point_count": 4,
                },
                "provenance": provenance,
                "truth": None,
                "verified": True,
                "failed_reason": None,
            }
        },
    )
    entry = {
        "impact_point_world_mm": list(impact_world_mm),
        "impact_point_source": TRUTH_SOURCE,
        "impact_point_uncertainty_mm": TRUTH_UNCERTAINTY_MM,
    }
    return record, entry


def attribute_injected(
    plans: Sequence[ThrowPlan],
    *,
    settings: M1Settings,
    injection: Injection = no_injection,
    noise_sigma_mm: float = NOISE_SIGMA_MM,
    drag_per_ms: float = 0.0,
    calibration_bias_mm: list[float] | None = CAL_BIAS_NEGLIGIBLE_MM,
) -> AttributionResult:
    """原因を注入した投擲群を、**本番の経路を丸ごと通して**帰属させる。

    `観測 → 予測 → 誤差 → 集計 → 帰属` を順に呼ぶ。誤差ベクトルを手で
    置かないのが 5.2 との違いであり、**注入した原因が経路のどこかで
    消えてしまわないこと**もここで初めて確かめられる。
    """
    rng = random.Random(NOISE_SEED)
    records: list[ThrowRecord] = []
    entries: dict[str, dict[str, object]] = {}
    for index, plan in enumerate(plans):
        record_id = f"throw-{plan.label}-{index:04d}"
        record, entry = _build_record(
            plan,
            record_id=record_id,
            injection=injection,
            noise_sigma_mm=noise_sigma_mm,
            drag_per_ms=drag_per_ms,
            calibration_bias_mm=calibration_bias_mm,
            rng=rng,
        )
        records.append(record)
        entries[record_id] = entry

    metrics: list[ThrowMetrics] = []
    attached: list[ThrowRecord] = []
    for record in records:
        truth = derive_truth(record, entries[record.record_id], layout=settings.layout)
        with_truth = attach_truth(record, truth)
        attached.append(with_truth)
        accuracy = measure_accuracy(with_truth, truth)
        metrics.append(
            ThrowMetrics(
                record=with_truth,
                truth=truth,
                flight=measure_flight(with_truth, truth, layout=settings.layout),
                accuracy=accuracy,
                convergence=analyze_convergence(
                    with_truth, accuracy, settings=settings
                ),
            )
        )

    groups = aggregate(metrics, settings=settings)
    assert len(groups) == 1, "同じキャリブレーションの群は1つにまとまるはずである"
    return attribute(groups[0], attached, settings=settings)


# ---------------------------------------------------------------------------
# 判定の根拠の読み取り（実装の私有定数を参照しない）
# ---------------------------------------------------------------------------

#: 説明文から「規則N」を拾う。**帰属先のラベルだけを見ると、別の規則が
#: 偶然同じ答えを出した場合を見逃す**——たとえば「縮退（規則4）」と
#: 「どちらの向きとも整合しない（規則4）」はどちらも判別不能だが、直し方は
#: まったく違う（前者は投擲位置を増やせば解ける）。
_RULE_PATTERN = re.compile(r"規則([1-7])")

#: 縮退と不整合を分ける語（本ファイルのリテラル。実装から import しない）。
DEGENERACY_WORD = "縮退"
MISMATCH_PHRASE = "どちらの向きとも整合しない"


def applied_rules(result: AttributionResult) -> set[int]:
    """判定の根拠として記録された規則番号の集合。"""
    return {int(match) for match in _RULE_PATTERN.findall(result.judgement.rationale)}


def outcome(result: AttributionResult) -> tuple[Attribution, Attribution]:
    """帰属先の対（偏り成分・ばらつき成分）。**単一の帰属先へ畳まない。**"""
    return (result.bias.attribution, result.scatter.attribution)


def camera_ray_agreement_deg(result: AttributionResult) -> float:
    assert result.bias.camera_ray_agreement_deg is not None
    return result.bias.camera_ray_agreement_deg


def world_fixed_agreement_deg(result: AttributionResult) -> float:
    assert result.bias.world_fixed_agreement_deg is not None
    return result.bias.world_fixed_agreement_deg


def scatter_rms_mm(result: AttributionResult) -> float:
    assert result.scatter.rms_mm is not None
    return result.scatter.rms_mm


def bootstrap_rms_mm(result: AttributionResult) -> float:
    assert result.scatter.bootstrap_rms_mm is not None
    return result.scatter.bootstrap_rms_mm


def residual_median_mm(result: AttributionResult) -> float:
    assert result.scatter.residual_median_mm is not None
    return result.scatter.residual_median_mm


# ---------------------------------------------------------------------------
# 5種類の原因（本ファイルの中心）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def causes_settings(tmp_path_factory: pytest.TempPathFactory) -> M1Settings:
    """注入実験に使う設定（再抽出の回数と種だけを小さく固定する）。

    レイアウトの雛形は共有ヘルパ `m1fixtures` から取る——同じフィクスチャを
    2箇所に置くと、形式が変わったとき片方だけ直して食い違う。
    """
    layout_file = write_layout(tmp_path_factory.mktemp("causes"))
    return M1Settings.resolve(
        file=None,
        env={},
        overrides={
            "layout_file": str(layout_file),
            "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
    )


def calibration_cause(settings: M1Settings) -> AttributionResult:
    """原因1: World 座標系に固定された偏り + それと整合する検証レポート。"""
    return attribute_injected(
        TWO_POSITIONS,
        settings=settings,
        injection=world_fixed_injection((INJECTED_BIAS_MM, 0.0)),
        calibration_bias_mm=CAL_BIAS_ALONG_X_MM,
    )


def detection_cause(settings: M1Settings) -> AttributionResult:
    """原因2: カメラ視線方向の偏り + 偏りが認められない検証レポート。"""
    return attribute_injected(
        TWO_POSITIONS,
        settings=settings,
        injection=camera_ray_injection(INJECTED_BIAS_MM),
        calibration_bias_mm=CAL_BIAS_NEGLIGIBLE_MM,
    )


def observation_noise_cause(settings: M1Settings) -> AttributionResult:
    """原因3: 観測ノイズだけ（偏りを注入しない）。"""
    return attribute_injected(TWO_POSITIONS, settings=settings)


def degenerate_cause(settings: M1Settings) -> AttributionResult:
    """原因4: 投擲位置が1箇所で、World 固定方向とカメラ視線方向が縮退する。

    注入する偏りも検証レポートの平均オフセットも、**その1箇所のカメラ視線
    方向**に沿って置く。この配置では「World に固定された偏り」と「カメラ
    視線方向の偏り」が同じ軸に乗り、原理的に区別できない。
    """
    return attribute_injected(
        ONE_POSITION,
        settings=settings,
        injection=world_fixed_injection(_bias_along_ray_of(THROW_A)),
        calibration_bias_mm=_report_along_ray_of(THROW_A),
    )


def model_cause(settings: M1Settings) -> AttributionResult:
    """原因5: 非放物的な軌道（空気抵抗）。偏りは注入しない。"""
    return attribute_injected(
        TWO_POSITIONS, settings=settings, drag_per_ms=DRAG_PER_MS
    )


def _bias_along_ray_of(plan: ThrowPlan, *, sign: float = 1.0) -> tuple[float, float]:
    """その投擲のカメラ視線方向に沿った、World 固定の偏りベクトル。

    `sign` が負ならカメラ側を向く（視線方向とは逆を向く）。**同じ軸の
    両向きを作れるようにしてある**——向きを軸で比べるのか符号付きで比べる
    のかは、片方の向きだけを見ていると区別できない。
    """
    unit_x, unit_y = mean_camera_ray_horizontal(plan)
    return (sign * INJECTED_BIAS_MM * unit_x, sign * INJECTED_BIAS_MM * unit_y)


def _report_along_ray_of(plan: ThrowPlan) -> list[float]:
    """同じ向きを持つ、**認められる**大きさの検証レポート平均オフセット。"""
    bias_x_mm, bias_y_mm = _bias_along_ray_of(plan)
    return [bias_x_mm, bias_y_mm, 0.0]


class TestLayoutInvariants:
    """本ファイルのリテラルが、レイアウトの値と一致していることを独立に固定する。

    視線方向はカメラ位置から解くので、レイアウトのカメラ位置と食い違うと
    **フィクスチャの意味そのものが変わる**。実装の出力との自己比較ではなく、
    設定ファイル側の値との照合である。
    """

    def test_the_camera_position_matches_the_layout(
        self, causes_settings: M1Settings
    ) -> None:
        assert causes_settings.layout.camera_position_world_mm == CAMERA_WORLD_MM

    def test_the_two_positions_are_seen_from_clearly_different_directions(
        self,
    ) -> None:
        """2箇所の投擲位置は、カメラから見て**別の向き**にある。

        ここが縮退していると、World 固定の偏りとカメラ視線方向の偏りを
        分ける材料がそもそも入力に存在しないことになり、原因1 と原因2 の
        テストは**差の現れない入力**のまま通ってしまう。
        """
        unit_a = mean_camera_ray_horizontal(THROW_A)
        unit_b = mean_camera_ray_horizontal(THROW_B)
        dot = unit_a[0] * unit_b[0] + unit_a[1] * unit_b[1]
        separation_deg = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
        assert separation_deg > 40.0
        # World の +X は、どちらの視線方向とも軸として大きく離れている
        # （原因1 の偏りが「たまたま視線方向でもあった」状態を排除する）。
        for unit in (unit_a, unit_b):
            angle_deg = math.degrees(math.acos(min(1.0, abs(unit[0]))))
            assert angle_deg > 45.0


class TestCalibrationCause:
    """原因1: **World 座標系に固定された偏り** → キャリブレーション由来（要件 6.4）。

    投擲位置が変わっても World 上で同じ向きへ 30 mm ずれる観測を与える。
    座標系のずれはこの形で現れ、検証レポートの平均オフセットと向きが
    整合する。
    """

    def test_a_world_fixed_bias_is_attributed_to_calibration(
        self, causes_settings: M1Settings
    ) -> None:
        result = calibration_cause(causes_settings)
        assert result.bias.attribution is Attribution.CALIBRATION
        assert applied_rules(result) == {2, 5}

    def test_the_recovered_bias_is_the_injected_one(
        self, causes_settings: M1Settings
    ) -> None:
        """成分の値まで見る。**帰属先のラベルだけでは根拠が固定されない。**"""
        result = calibration_cause(causes_settings)
        bias_x_mm, bias_y_mm = result.bias.vector_mm
        assert abs(bias_x_mm - INJECTED_BIAS_MM) < 2.0
        assert abs(bias_y_mm) < 2.0
        assert result.bias.norm_mm > 0.9 * INJECTED_BIAS_MM

    def test_the_direction_agrees_with_the_report_and_not_with_the_ray(
        self, causes_settings: M1Settings
    ) -> None:
        result = calibration_cause(causes_settings)
        assert world_fixed_agreement_deg(result) < 2.0
        assert camera_ray_agreement_deg(result) > 45.0
        assert result.bias.degenerate is False
        assert result.bias.significance_ratio is not None
        assert result.bias.significance_ratio > 5.0


class TestDetectionCause:
    """原因2: **カメラ視線方向の偏り** → 検出由来の候補（要件 6.5）。

    投擲ごとに、その投擲のカメラ視線方向へ 30 mm 寄せる。World 上の向きは
    投擲位置ごとに変わるので、原因1 とは**別の向き**として現れなければ
    ならない。
    """

    def test_a_camera_ray_bias_is_attributed_to_detection(
        self, causes_settings: M1Settings
    ) -> None:
        result = detection_cause(causes_settings)
        assert result.bias.attribution is Attribution.DETECTION
        assert applied_rules(result) == {3, 6}

    def test_the_same_cause_also_shows_up_as_scatter(
        self, causes_settings: M1Settings
    ) -> None:
        """ばらつき成分は**モデル由来（規則6）**として返る。これは正しい。

        カメラ視線方向の偏りは、投擲位置ごとに World 上で別の向きへ出る。
        したがって同じ原因が、投擲群の**共通の偏り**（視線方向の平均）と
        **投擲ごとのばらつき**（位置による向きの違い）の両方を作る。偏り
        成分とばらつき成分は独立に判定される（要件 6.9、design.md
        「偏りとばらつきは独立に判定する。両方が同時に存在しうる」）ので、
        両方に判定が出ること自体は想定どおりである。

        ⚠️ ただし、**ばらつき側の語彙には「検出由来」が無い**（要件 6.6 /
        6.7 は観測ノイズ由来・モデル由来・判別不能の3つしか置いていない）
        ため、検出由来の偏りが生む位置依存のばらつきは規則6 でモデル由来
        と名指しされる。読み手が偏り成分を見れば取り違えようは無いが、
        **ばらつき成分だけを単独で読むと予測モデルを疑いに行く**。要件側の
        限界であって実装の逸脱ではないので、ここでは現に返る値を固定し、
        フィーチャレベル検証への申し送りとする。
        """
        result = detection_cause(causes_settings)
        assert result.scatter.attribution is Attribution.PREDICTION
        assert scatter_rms_mm(result) > bootstrap_rms_mm(result)

    def test_the_direction_agrees_with_the_ray_and_not_with_the_report(
        self, causes_settings: M1Settings
    ) -> None:
        result = detection_cause(causes_settings)
        assert camera_ray_agreement_deg(result) < 25.0
        assert world_fixed_agreement_deg(result) > 45.0
        assert result.bias.degenerate is False

    def test_the_bias_points_back_toward_the_camera(
        self, causes_settings: M1Settings
    ) -> None:
        """カメラ側へ寄る向きである（視線方向とは**逆**を向く）。

        カメラは `y = -1500` にあり、どちらの投擲もそれより遠い側を飛ぶので、
        カメラ側へ寄る偏りは **−Y 側**を向く。符号を見ないと、軸で比べる
        規則が符号付きの比較へ差し替わっても気付けない。
        """
        result = detection_cause(causes_settings)
        bias_x_mm, bias_y_mm = result.bias.vector_mm
        assert bias_y_mm < -0.8 * INJECTED_BIAS_MM
        assert abs(bias_x_mm) < 0.2 * INJECTED_BIAS_MM


class TestObservationNoiseCause:
    """原因3: **観測ノイズだけ** → 観測ノイズ由来（要件 6.6）。

    ⚠️ **規則5 と規則6 は、2つの見積もりがちょうど一致する点で分かれる。**
    投擲群のばらつきも、再抽出で見積もった観測由来のばらつきも、**同じ
    観測ノイズを測っている**ので、期待値としては同じ大きさになる。したがって
    純粋な観測ノイズの群がどちらへ落ちるかは実現値しだいであり、種を振ると
    比（ばらつき ÷ 見積もり）は概ね 0.6〜1.4 に散らばる。本ファイルの
    `NOISE_SEED` はその比が 0.58 になる実現値を選んである——**この余裕は
    規則の性質ではなく、選んだ種の性質である**。実測でこの分岐を使うときは、
    ノイズだけの群がモデル由来へ倒れうることを前提に読むこと（要件 13.7:
    閾値は暫定の評価候補であって必須性能ではない）。フィーチャレベル検証への
    申し送りとする。
    """

    def test_isotropic_noise_alone_is_attributed_to_observation_noise(
        self, causes_settings: M1Settings
    ) -> None:
        result = observation_noise_cause(causes_settings)
        assert outcome(result) == (Attribution.NONE, Attribution.OBSERVATION_NOISE)
        assert applied_rules(result) == {1, 5}

    def test_no_common_bias_is_claimed(self, causes_settings: M1Settings) -> None:
        """偏りは「無い」であって「決めきれない」ではない（**別物**である）。"""
        result = observation_noise_cause(causes_settings)
        assert result.bias.norm_mm < 0.5 * scatter_rms_mm(result)

    def test_the_scatter_sits_inside_the_bootstrap_range(
        self, causes_settings: M1Settings
    ) -> None:
        """ばらつきは**再抽出で見積もった範囲の中**にあり、かつ 0 ではない。

        0 に潰れた入力で「範囲内」を確かめても、判定は何も見ていない。
        """
        result = observation_noise_cause(causes_settings)
        assert scatter_rms_mm(result) > 0.0
        assert scatter_rms_mm(result) <= bootstrap_rms_mm(result)
        assert scatter_rms_mm(result) > 0.5 * bootstrap_rms_mm(result)


class TestDegenerateCause:
    """原因4: **投擲位置が1箇所** → 判別不能（要件 6.10）。

    注入した偏りは World 上では固定されているが、その向きが**唯一の投擲
    位置のカメラ視線方向と同じ軸**に乗る。この入力からキャリブレーション
    由来と検出由来のどちらかを選ぶ根拠は無い。**判別不能は正常な結果で
    ある。**
    """

    def test_two_degenerate_directions_yield_undetermined(
        self, causes_settings: M1Settings
    ) -> None:
        result = degenerate_cause(causes_settings)
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert result.bias.degenerate is True
        assert applied_rules(result) == {4, 5}

    def test_the_reason_names_degeneracy_and_not_a_mismatch(
        self, causes_settings: M1Settings
    ) -> None:
        """縮退と不整合は**どちらも判別不能だが、直し方が違う**。

        縮退は投擲位置を増やせば解ける。不整合として記録されると、
        存在しない別の原因を探しにいくことになる。
        """
        result = degenerate_cause(causes_settings)
        assert DEGENERACY_WORD in result.judgement.rationale
        assert MISMATCH_PHRASE not in result.judgement.rationale

    def test_both_directions_agree_with_the_bias(
        self, causes_settings: M1Settings
    ) -> None:
        """縮退の中身: 2つの向きが**どちらも**偏りと整合してしまっている。"""
        result = degenerate_cause(causes_settings)
        assert world_fixed_agreement_deg(result) < 2.0
        assert camera_ray_agreement_deg(result) < 2.0

    def test_the_degeneracy_also_happens_on_the_opposite_direction(
        self, causes_settings: M1Settings
    ) -> None:
        """**軸の両向きで縮退する。**

        カメラ視線方向と同じ軸に乗っていても、偏りが**カメラ側**を向く場合
        がある——Depth が対象物のカメラ側表面を測ることによる寄りはまさに
        その向きである。検証レポートの平均オフセットも同じ向きを申告して
        いるなら、投擲位置が1箇所である限り、較正のずれなのか検出の寄りなの
        かは決められない。

        向きを**軸**ではなく**符号付き**で比べる実装は、この入力を
        「縮退していない」と読んでキャリブレーション由来へ倒す
        ——**検出側が原因なのに較正をやり直しに行く**（tasks.md
        「Implementation Notes」タスク5.2）。
        """
        toward_camera = _bias_along_ray_of(THROW_A, sign=-1.0)
        result = attribute_injected(
            ONE_POSITION,
            settings=causes_settings,
            injection=world_fixed_injection(toward_camera),
            calibration_bias_mm=[toward_camera[0], toward_camera[1], 0.0],
        )
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert result.bias.degenerate is True
        assert DEGENERACY_WORD in result.judgement.rationale
        assert result.bias.vector_mm[1] < 0.0


class TestModelCause:
    """原因5: **非放物的な軌道** → モデル由来（要件 6.7）。

    速度に比例する空気抵抗を入れる。予測コアは放物線しか当てはめられない
    ので残差が大きくなり、落下地点は飛行方向へ行き過ぎる側へ外れる。
    A と B は飛行方向が逆なので、誤差は**平均としては打ち消し合い、
    ばらつきだけが残る**。
    """

    def test_a_non_parabolic_trajectory_is_attributed_to_the_model(
        self, causes_settings: M1Settings
    ) -> None:
        result = model_cause(causes_settings)
        assert outcome(result) == (Attribution.NONE, Attribution.PREDICTION)
        assert applied_rules(result) == {1, 6}

    def test_the_scatter_leaves_the_bootstrap_range_and_the_residual_is_large(
        self, causes_settings: M1Settings
    ) -> None:
        result = model_cause(causes_settings)
        assert scatter_rms_mm(result) > 3.0 * bootstrap_rms_mm(result)
        assert residual_median_mm(result) > bootstrap_rms_mm(result)

    def test_a_stronger_drag_moves_the_measured_scatter(
        self, causes_settings: M1Settings
    ) -> None:
        """**注入量を変えると測られた量が動く。** 定数を返す実装では動かない。"""
        weak = model_cause(causes_settings)
        strong = attribute_injected(
            TWO_POSITIONS, settings=causes_settings, drag_per_ms=STRONG_DRAG_PER_MS
        )
        assert scatter_rms_mm(strong) > 1.5 * scatter_rms_mm(weak)
        assert residual_median_mm(strong) > 1.5 * residual_median_mm(weak)
        assert strong.scatter.attribution is Attribution.PREDICTION


class TestTheFiveCausesAreToldApart:
    """**5種類が互いに異なる帰属を返す**（負の対照が負の対照であるための条件）。

    「注入した原因ごとに期待どおりの帰属が返る」を1件ずつ見るだけでは
    足りない。**すべてに同じ判定を返す実装でも一部は通る**からである。
    ここでは5種類の結果が互いに区別されていることを1件で押さえる。
    """

    def test_the_five_causes_give_five_different_answers(
        self, causes_settings: M1Settings
    ) -> None:
        results = [
            calibration_cause(causes_settings),
            detection_cause(causes_settings),
            observation_noise_cause(causes_settings),
            degenerate_cause(causes_settings),
            model_cause(causes_settings),
        ]
        assert len({outcome(result) for result in results}) == len(results)

    def test_each_cause_is_reached_through_a_different_rule(
        self, causes_settings: M1Settings
    ) -> None:
        """適用された規則の組も5通りに割れる。

        帰属先のラベルだけが一致していて、**別の規則が偶然同じ答えを
        出している**状態を排除する。
        """
        rule_sets = [
            frozenset(applied_rules(calibration_cause(causes_settings))),
            frozenset(applied_rules(detection_cause(causes_settings))),
            frozenset(applied_rules(observation_noise_cause(causes_settings))),
            frozenset(applied_rules(degenerate_cause(causes_settings))),
            frozenset(applied_rules(model_cause(causes_settings))),
        ]
        assert len(set(rule_sets)) == len(rule_sets)

    def test_the_two_bias_causes_are_not_the_same_answer(
        self, causes_settings: M1Settings
    ) -> None:
        """**原因1 と原因2 が別物として返る**——本 Spec の核心の1件。

        どちらも「投擲群に共通する 30 mm の偏り」として現れ、**大きさでは
        区別できない**。向きで切り分けられて初めて、較正をやり直すのか
        検出を疑うのかが決まる。
        """
        calibration = calibration_cause(causes_settings)
        detection = detection_cause(causes_settings)
        assert calibration.bias.attribution is not detection.bias.attribution
        assert abs(calibration.bias.norm_mm - detection.bias.norm_mm) < 5.0
        assert calibration.judgement.rationale != detection.judgement.rationale
        assert str(Attribution.CALIBRATION) in calibration.judgement.verdict
        assert str(Attribution.DETECTION) in detection.judgement.verdict


class TestThrowPositionCountDecidesTheAnswer:
    """**投擲位置が1箇所か2箇所かだけで、判別不能とキャリブレーション由来が入れ替わる。**

    注入した偏り（World 固定・投擲位置A の視線方向に沿う）も、検証レポートの
    平均オフセットも、観測ノイズも**完全に同一**にしたまま、投擲位置の数
    だけを振る。1箇所では World 固定方向とカメラ視線方向が同じ軸へ縮退して
    判別できないが、2箇所あれば視線方向が投擲位置ごとに変わるので、偏りが
    World に固定されていることが分かる。

    **これが本 Spec の存在意義そのものである**（research.md Decision 4 の
    Follow-up、design.md「ErrorAttributor」Risks、要件 6.10）。実験の前に
    投擲位置を2箇所以上にしておかないと、誤差の原因は測っても分からない
    ——そのことを、実測前に固定した規則がちゃんと言えることを確かめる。
    """

    def test_one_position_cannot_tell_the_two_directions_apart(
        self, causes_settings: M1Settings
    ) -> None:
        result = degenerate_cause(causes_settings)
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert result.bias.degenerate is True

    def test_two_positions_resolve_the_very_same_injection(
        self, causes_settings: M1Settings
    ) -> None:
        result = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            injection=world_fixed_injection(_bias_along_ray_of(THROW_A)),
            calibration_bias_mm=_report_along_ray_of(THROW_A),
        )
        assert result.bias.attribution is Attribution.CALIBRATION
        assert result.bias.degenerate is False
        assert camera_ray_agreement_deg(result) > 30.0

    def test_the_recovered_bias_is_the_same_in_both_layouts(
        self, causes_settings: M1Settings
    ) -> None:
        """**測られた偏りは同じで、判別できるかどうかだけが違う。**

        偏りそのものが変わっているなら、入れ替わったのは判別可能性では
        なく入力である。
        """
        one = degenerate_cause(causes_settings)
        two = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            injection=world_fixed_injection(_bias_along_ray_of(THROW_A)),
            calibration_bias_mm=_report_along_ray_of(THROW_A),
        )
        for index in range(2):
            assert abs(one.bias.vector_mm[index] - two.bias.vector_mm[index]) < 2.0


class TestInjectedDirectionDecidesTheAnswer:
    """**入力側を変異させると判定が動く**（注入の作り分けが効いていることの確認）。

    注入する向きと検証レポートの2軸を振る。原因が指せているなら、
    「World 固定 × レポートが偏りを認める」と
    「カメラ視線方向 × レポートが偏りを認めない」の**2通りだけ**が原因を
    名指しでき、残りは判別不能でなければならない。
    """

    def test_a_world_fixed_bias_without_a_matching_report_is_undetermined(
        self, causes_settings: M1Settings
    ) -> None:
        """レポートが偏りを認めなければ、World 固定の偏りでも原因は決まらない。

        **測っていない／認められないレポートを「整合した」の根拠にしない。**
        """
        result = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            injection=world_fixed_injection((INJECTED_BIAS_MM, 0.0)),
            calibration_bias_mm=CAL_BIAS_NEGLIGIBLE_MM,
        )
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert MISMATCH_PHRASE in result.judgement.rationale

    def test_a_camera_ray_bias_with_a_biased_report_is_undetermined(
        self, causes_settings: M1Settings
    ) -> None:
        """レポートが別の向きの偏りを申告していれば、検出由来とは言えない。"""
        result = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            injection=camera_ray_injection(INJECTED_BIAS_MM),
            calibration_bias_mm=CAL_BIAS_ALONG_X_MM,
        )
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert MISMATCH_PHRASE in result.judgement.rationale

    def test_reversing_the_injected_direction_changes_the_answer(
        self, causes_settings: M1Settings
    ) -> None:
        """**向きを反転させただけで、キャリブレーション由来が消える。**

        大きさも有意性も変わらないので、ここで判定が動かないなら
        「向きで切り分けている」という主張は成立していない。レポートとは
        **符号付き**で比べるので、真逆の偏りは整合しない。
        """
        result = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            injection=world_fixed_injection((-INJECTED_BIAS_MM, 0.0)),
            calibration_bias_mm=CAL_BIAS_ALONG_X_MM,
        )
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert world_fixed_agreement_deg(result) > 170.0
        assert result.bias.norm_mm > 0.9 * INJECTED_BIAS_MM

    def test_only_the_two_matching_combinations_name_a_cause(
        self, causes_settings: M1Settings
    ) -> None:
        """4通りの組み合わせのうち、原因を名指しできるのは2通りだけである。"""
        answers = {
            ("world", "recognized"): calibration_cause(causes_settings),
            ("camera", "negligible"): detection_cause(causes_settings),
            ("world", "negligible"): attribute_injected(
                TWO_POSITIONS,
                settings=causes_settings,
                injection=world_fixed_injection((INJECTED_BIAS_MM, 0.0)),
                calibration_bias_mm=CAL_BIAS_NEGLIGIBLE_MM,
            ),
            ("camera", "recognized"): attribute_injected(
                TWO_POSITIONS,
                settings=causes_settings,
                injection=camera_ray_injection(INJECTED_BIAS_MM),
                calibration_bias_mm=CAL_BIAS_ALONG_X_MM,
            ),
        }
        assert {
            key: result.bias.attribution for key, result in answers.items()
        } == {
            ("world", "recognized"): Attribution.CALIBRATION,
            ("camera", "negligible"): Attribution.DETECTION,
            ("world", "negligible"): Attribution.UNDETERMINED,
            ("camera", "recognized"): Attribution.UNDETERMINED,
        }

    def test_an_unverified_report_cannot_support_the_detection_answer(
        self, causes_settings: M1Settings
    ) -> None:
        """**「偏りが認められない」と「そもそも測っていない」は別物である。**

        検出由来の候補（規則3）が要求するのは前者である。同じカメラ視線
        方向の偏りを注入しても、レポートに平均オフセットが**記録されて
        いない**群では「認められない」とも言えないので、規則3 は適用でき
        ない。ここを同一視すると、**検証を実施していないだけの群がまるごと
        検出由来へ倒れる**（要件 6.5 / 2.2）。

        レポートに 0 と**記録された**場合は「認められない」であり、検出
        由来の候補になる——記録された 0 と未記録は別の入力である。
        """
        answers = {
            "zero": attribute_injected(
                TWO_POSITIONS,
                settings=causes_settings,
                injection=camera_ray_injection(INJECTED_BIAS_MM),
                calibration_bias_mm=CAL_BIAS_ZERO_MM,
            ),
            "unmeasured": attribute_injected(
                TWO_POSITIONS,
                settings=causes_settings,
                injection=camera_ray_injection(INJECTED_BIAS_MM),
                calibration_bias_mm=None,
            ),
        }
        assert {
            key: result.bias.attribution for key, result in answers.items()
        } == {
            "zero": Attribution.DETECTION,
            "unmeasured": Attribution.UNDETERMINED,
        }
        assert MISMATCH_PHRASE in answers["unmeasured"].judgement.rationale
        # 未記録では「World 固定方向との角度差」も欠測になる
        # （**測っていないことを「整合した」の根拠にしない**）。
        assert answers["unmeasured"].bias.world_fixed_agreement_deg is None
        assert answers["zero"].bias.world_fixed_agreement_deg is None


class TestObservationNoiseAmountFlowsThrough:
    """**注入したノイズ量が、測られたばらつきに現れる。**

    ノイズを 10 倍にすると、投擲群のばらつきも再抽出の見積もりも 10 倍
    前後になる。大小比較ではなく**比**で見るのは、定数を返す実装や
    スケールを無視する実装が大小比較だけなら偶然通り得るからである。
    """

    def test_ten_times_the_noise_gives_ten_times_the_scatter(
        self, causes_settings: M1Settings
    ) -> None:
        quiet = observation_noise_cause(causes_settings)
        loud = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            noise_sigma_mm=NOISE_SIGMA_MM * NOISE_SCALE,
        )
        scatter_ratio = scatter_rms_mm(loud) / scatter_rms_mm(quiet)
        bootstrap_ratio = bootstrap_rms_mm(loud) / bootstrap_rms_mm(quiet)
        assert 0.8 * NOISE_SCALE < scatter_ratio < 1.2 * NOISE_SCALE
        assert 0.8 * NOISE_SCALE < bootstrap_ratio < 1.2 * NOISE_SCALE

    def test_more_noise_is_still_observation_noise(
        self, causes_settings: M1Settings
    ) -> None:
        """**ノイズが増えてもモデル由来へ倒れない。**

        観測由来の範囲もノイズとともに広がるので、帰属先は変わらない
        （絶対値の目標を置かない、という判定規則の主旨）。
        """
        loud = attribute_injected(
            TWO_POSITIONS,
            settings=causes_settings,
            noise_sigma_mm=NOISE_SIGMA_MM * NOISE_SCALE,
        )
        assert outcome(loud) == (Attribution.NONE, Attribution.OBSERVATION_NOISE)


class TestTrajectoryModelDecidesTheScatterAnswer:
    """**軌道だけを非放物に差し替えると、ばらつきの帰属が入れ替わる。**

    投擲位置・観測ノイズ・検証レポートは原因3 とまったく同じで、違うのは
    軌道が放物線か、空気抵抗を含むかだけである。観測ノイズ由来とモデル
    由来を分けているのが**本当に軌道の当てはまり**であることをここで
    押さえる。
    """

    def test_the_same_group_flips_from_noise_to_model(
        self, causes_settings: M1Settings
    ) -> None:
        parabolic = observation_noise_cause(causes_settings)
        with_drag = attribute_injected(
            TWO_POSITIONS, settings=causes_settings, drag_per_ms=DRAG_PER_MS
        )
        assert parabolic.scatter.attribution is Attribution.OBSERVATION_NOISE
        assert with_drag.scatter.attribution is Attribution.PREDICTION
        assert scatter_rms_mm(with_drag) > 5.0 * scatter_rms_mm(parabolic)
        assert residual_median_mm(with_drag) > 2.0 * residual_median_mm(parabolic)

    def test_neither_group_claims_a_common_bias(
        self, causes_settings: M1Settings
    ) -> None:
        """どちらの群も偏りは有意でない——**入れ替わったのはばらつきだけ**。"""
        parabolic = observation_noise_cause(causes_settings)
        with_drag = attribute_injected(
            TWO_POSITIONS, settings=causes_settings, drag_per_ms=DRAG_PER_MS
        )
        assert parabolic.bias.attribution is Attribution.NONE
        assert with_drag.bias.attribution is Attribution.NONE
