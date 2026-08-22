"""独立検証: 検証点の World 座標算出と独立性の検査（design.md「L6-L9: 永続化・
検証・入口」Verifier コンポーネント / tasks.md タスク 5.1 / 要件 4.1, 4.2, 4.3,
4.9）。

requirements.md Requirement 4「独立検証ステップ ★」の Objective が述べる
とおり、本 Spec の価値は「変換できること」そのものではなく、**変換が
正しいことを独立に確かめられること**にある。本モジュールはその中心である。

**入力は「保存済みの結果」と「検証点の観測」だけ**（design.md Verifier
「Responsibilities & Constraints」/ 要件 4.1）。`evaluate_verification_points`
は `world_frame_calibration` パッケージ内の型（`errors` / `plan` / `result` /
`types`）のみに依存し、物体検出・追跡・予測のいずれも呼ばない。これは
「検出も予測も動かさずに単体で合否の材料を得られる」という本 Spec の
中心的な性質（要件 4.1）を、依存関係そのものによって構造的に満たす。

**確立に使った点は定義上一致するため検証にならない**（design.md Verifier
「Responsibilities & Constraints」/ 要件 4.3）。`CalibrationResult.
origin_anchor` / `x_axis_anchor` は World frame の確立にそのまま使われた
マーカー観測であり、その物理点を検証点として混ぜれば、`result.transform` は
その点を（定義上）誤差ゼロで再現する。これは「変換が正しい」ことの証拠には
ならず、単なる自己言及（tautology）である。したがって本モジュールは、
検証点の観測ラベルが確立用マーカー（`origin_anchor.label` /
`x_axis_anchor.label`）のいずれかと一致する場合、その点を
`independent=False` として明示し、独立点の集計（将来の集計はタスク 5.2 が
担う）から除外できるようにする。独立な検証点が1つも残らなければ、
検証として成立しないため `CalibrationFailure
(FailureReason.VERIFICATION_NOT_INDEPENDENT)` を送出する。空の検証結果を
vacuous な成功として返すことは、`errors.py` が定める「縮退条件では結果を
出さない」（A-9）の一形態であり、本モジュールもこれに従う。

**床上の点と既知の高さを持つ点を同じ経路で扱う**（design.md Verifier
「Responsibilities & Constraints」/ 要件 4.9）。マーカー観測
（`AnchorObserver`, タスク 2.4）は確立用マーカーの位置を求める際、高さを
判定にのみ使い、床平面へ**投影した**点（`AnchorObservation.
point_on_plane_mm`）を確立に用いる（要件 2.6: マーカーの高さは結果に
影響しない）。しかし検証点は逆に、**高さそのものが検証対象**である
（要件 4.9: 「既知の高さに置かれた検証点が与えられた場合、高さ方向の誤差も
同じ形式で報告する」）。そのため本モジュールは検証点の観測に
`point_on_plane_mm`（投影後、常に平面上）ではなく
`point_camera_mm`（投影前のロバスト代表点、カメラ座標系）を用いる。
`result.transform` は回転と平行移動のみの剛体変換であり
（`transform.py` 参照）、床平面上にない任意の3次元点にもそのまま適用できる。
World frame の Z 軸は床平面の法線に一致するよう構成される
（`frame.build_world_frame` 参照）ため、`transform.apply_point
(point_camera_mm)` の z 成分は「床平面からの符号付き距離」＝高さを直接表す。
床上の点（高さ0）も高さのある点も、追加の分岐なしに同じ
`apply_point` 呼び出しだけで正しい World 座標（高さを含む）を返す。

**誤差は軸ごとに報告する**（design.md Verifier「Contracts」`PointError.
error_mm` / 要件 4.2）。スカラーの距離1つに潰すと、水平方向のずれなのか
垂直方向のずれなのかが区別できず、`docs/requirements.md §6.2` が要求する
「系統誤差か予測誤差かを即座に分離できる」という本 Spec の目的（A-4）に
反する。`error_mm` は `measured_world_mm - truth_world_mm`（軸ごと）で
定義する。

**タスク境界について（タスク 5.1）**: タスク 5.1 が担ったのは要件 4.1, 4.2,
4.3, 4.9（検証点の World 座標算出・独立性の検査）のみである。誤差をバイアスと
ばらつきへ分解すること、距離帯ごとの集計、メジャー実測基線長との突き合わせ
（要件 4.4, 4.5, 9.3）はタスク 5.2 が、許容値による合否判定・レポートとしての
統合（要件 4.6, 4.7, 4.10）はタスク 5.3 が、それぞれ本モジュールを拡張して
担う（tasks.md「5. 独立検証」節、`_Depends: 5.1_`）。したがって
`PointVerification` は design.md が示す最終形の `PointError`（`verdict` /
`spread_mm` / `sample_count` などを含む）のサブセットであり、それらの
フィールドは後続タスクが追加する。

---

**タスク 5.2（誤差の分解とスケールの独立確認）について**（design.md
Verifier「Contracts」/ 要件 4.4, 4.5, 9.3）。

タスク 5.1 が確立した `evaluate_verification_points` / `PointVerification`
の上に、本タスクは以下を追加する:

- `PointVerification` に、design.md `PointError` が定める誤差の派生量
  （`error_norm_mm` / `horizontal_error_mm` / `vertical_error_mm` /
  `range_from_camera_mm`）を追加する。これらは `error_mm`（タスク 5.1）と
  観測の `range_from_camera_mm` から機械的に導ける値であり、独立点の
  集計（バイアス・ばらつき・距離帯・最大誤差の内訳）が必要とする
  入力そのものである
- `compute_verification_statistics`: `evaluate_verification_points` の
  出力（`PointVerification` の並び）から、**独立点のみ**を対象に
  軸ごとのバイアス・スカラーのばらつき（RMS）・距離帯集計・水平/垂直/
  全体の最大誤差を算出し、計画のメジャー実測基線長と算出基線長を
  突き合わせる（`VerificationStatistics` を返す）

**なぜ独立点のみを対象にするのか**（要件 4.3 の踏襲）: 確立用マーカーと
重複する検証点（`independent=False`）は、`result.transform` によって
定義上誤差ゼロで再現される。これをバイアス・ばらつき・距離帯集計に
混ぜると、系統誤差やばらつきを実際より小さく見せてしまう（サンプルの
一部が「常に完璧」であるかのように振る舞うため）。したがって
`compute_verification_statistics` は `points` のうち `independent is True`
の点だけを集計に用いる。

**読み分け規則**（design.md Verifier「Implementation Notes」/ tasks.md
タスク 5.2 が docstring への明記を要求する）:

    バイアス支配なら座標系、ばらつき支配なら観測、遠方だけ大きいなら
    Depth の距離特性。

`bias_mm`（軸ごとの平均オフセット）が支配的に大きければ、World frame の
確立（座標系）そのものに系統的なずれがある可能性が高い。
`scatter_rms_mm`（バイアス除去後の残差 RMS）が支配的に大きければ、
個々の観測（マーカー観測・Depth のランダムノイズ）のばらつきが原因で
ある可能性が高い。`range_buckets`（カメラからの距離帯ごとの集計）のうち
遠方の帯だけ誤差が大きければ、Depth センサの距離依存誤差特性（測距誤差が
距離に応じて増大する性質）が原因である可能性が高い。この3層の切り分け
こそが `docs/requirements.md §6.2` の「系統誤差か予測誤差かを即座に
分離できる」の実体である（design.md Verifier「Implementation Notes」）。

**バイアスとばらつきは分離可能でなければならない**: 検証点の観測へ
一様な（軸ごとに一定の）バイアスを注入しても、`scatter_rms_mm`
（バイアス除去後の残差の広がり）は変化しない。ばらつきが「平均を引いた
後の残差の RMS」として定義されているため、平均そのもの（バイアス）を
どれだけ動かしても残差の形は保たれるという数学的な性質による
（`TestBiasScatterSeparability` が既知量のバイアス注入で確認する）。

**スケールの独立確認について**（要件 9.3）: `CalibrationPlan.
expected_baseline_mm`（メジャー実測の基線長）は、World frame の確立
（`frame.build_world_frame`）が使う「原点マーカー→方向マーカーの
**向き**」とは異なり、両マーカー間の**絶対的な距離**である。
`frame.build_world_frame` は基線長を**下限判定にのみ**用い（短すぎれば
`ANCHOR_BASELINE_TOO_SHORT` として失敗させる）、frame の軸そのものの
定義（スケール基準）には使わない。したがって、確立結果から算出した
基線長（`FrameGeometry.baseline_mm`）とメジャー実測値
（`expected_baseline_mm`）を突き合わせることは、frame の定義に使った
情報から独立な検証になる（`ScaleCheck` の docstring も参照）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from world_frame_calibration.errors import (
    CalibrationConfigError,
    CalibrationFailure,
    FailureReason,
)
from world_frame_calibration.plan import VerificationPointSpec
from world_frame_calibration.result import CalibrationResult
from world_frame_calibration.types import AnchorObservation

__all__ = [
    "DEFAULT_RANGE_BUCKET_EDGES_MM",
    "PointVerification",
    "RangeBucket",
    "ScaleCheck",
    "VerificationStatistics",
    "compute_verification_statistics",
    "evaluate_verification_points",
]


@dataclass(frozen=True, slots=True)
class PointVerification:
    """1つの検証点について算出した World 座標と、既知位置との軸ごとの差分。

    design.md Verifier「Contracts」の `PointError` のうち、タスク 5.1 が
    担う基本フィールドに加え、タスク 5.2 が誤差の派生量
    （`error_norm_mm` / `horizontal_error_mm` / `vertical_error_mm` /
    `range_from_camera_mm`）を追加したもの。合否判定（`verdict`）と
    マーカー観測の散らばり（`spread_mm` / `sample_count`）はタスク 5.3 が
    追加する。

    Attributes:
        label: 検証点の識別ラベル（`VerificationPointSpec.label` と一致）。
        independent: `False` なら、この点は World frame の確立に用いた
            基準マーカー（`origin_anchor` / `x_axis_anchor`）と重複する
            ため、独立検証としては成立しない（要件 4.3）。
        measured_world_mm: `result.transform` を観測点へ適用して算出した
            World 座標。
        truth_world_mm: メジャー実測による既知の World 座標
            （`VerificationPointSpec.truth_world_mm`）。
        truth_source: 既知位置の実測手段の記録
            （`VerificationPointSpec.truth_source`）。
        error_mm: `measured_world_mm - truth_world_mm`（軸ごと。スカラーの
            距離1つに潰さない。要件 4.2）。
        error_norm_mm: 誤差ベクトルのノルム（`sqrt(x^2 + y^2 + z^2)`、mm）。
            全体の最大誤差・距離帯集計に用いる（design.md Verifier
            「Contracts」`PointError.error_norm_mm` / タスク 5.2 / 要件 4.4,
            4.5）。
        horizontal_error_mm: 水平方向（X・Y）の誤差のノルム
            （`sqrt(x^2 + y^2)`、mm。design.md Verifier「Contracts」
            `PointError.horizontal_error_mm` / タスク 5.2 / 要件 4.4）。
        vertical_error_mm: 鉛直方向（Z）の誤差の絶対値（mm。design.md
            Verifier「Contracts」`PointError.vertical_error_mm` / タスク 5.2
            / 要件 4.4）。
        range_from_camera_mm: この検証点の観測時点でのカメラからの距離
            （`AnchorObservation.range_from_camera_mm` をそのまま転記した
            もの）。距離帯ごとの集計に用いる（design.md Verifier
            「Contracts」`PointError.range_from_camera_mm` / タスク 5.2 /
            要件 4.5, 9.3）。
    """

    label: str
    independent: bool
    measured_world_mm: tuple[float, float, float]
    truth_world_mm: tuple[float, float, float]
    truth_source: str
    error_mm: tuple[float, float, float]
    error_norm_mm: float
    horizontal_error_mm: float
    vertical_error_mm: float
    range_from_camera_mm: float


def evaluate_verification_points(
    result: CalibrationResult,
    observations: Sequence[tuple[VerificationPointSpec, AnchorObservation]],
) -> tuple[PointVerification, ...]:
    """保存済みの `result` と検証点の観測から、各検証点の World 座標を算出し、
    既知の位置との差分を軸ごとに求める（design.md Verifier「Contracts」/
    tasks.md タスク 5.1 / 要件 4.1, 4.2, 4.3, 4.9）。

    **検出・追跡・予測のいずれも呼ばない**（要件 4.1）: 入力は `result`
    （保存済みキャリブレーション結果）と `observations`（検証点の仕様と、
    それに対応する `AnchorObserver.observe_anchor` の観測結果の組）だけで
    ある。

    **確立用マーカーと重複する検証点は `independent=False` として明示する**
    （要件 4.3）。`result.origin_anchor.label` または `result.x_axis_anchor.
    label` と一致するラベルを持つ検証点は、World frame の確立にそのまま
    使われた点であり、`result.transform` はその点を定義上誤差ゼロで
    再現するため、独立検証の材料にならない。独立点が1つも残らない場合
    （検証点がすべて重複、または `observations` が空）は、検証として
    成立しないものとして `CalibrationFailure
    (FailureReason.VERIFICATION_NOT_INDEPENDENT)` を送出する。空の検証を
    vacuous な成功として返すことはしない。

    **高さのある検証点も同じ経路で扱う**（要件 4.9）: `observation.
    point_camera_mm`（投影前のロバスト代表点）へ `result.transform` を
    そのまま適用する。`point_on_plane_mm`（床平面へ投影済み、確立用
    マーカーが使うもの）は用いない。投影すると高さの情報が失われ、
    既知の高さを持つ検証点の垂直方向誤差が測れなくなるためである。

    Args:
        result: 検証対象の保存済みキャリブレーション結果。
        observations: 検証点の仕様（`VerificationPointSpec`）と、
            `anchors.observe_anchor(..., role=AnchorRole.VERIFICATION)`
            が返す観測（`AnchorObservation`）の組の並び。組ごとに
            `spec.label == observation.label` であること（`observe_anchor`
            は常に `label=spec.label` で観測を返すため、通常は自然に
            成立する）。

    **誤差の派生量も併せて算出する**（タスク 5.2 / design.md Verifier
    「Contracts」`PointError`）: `error_mm` から `error_norm_mm`
    （全体のノルム）・`horizontal_error_mm`（X・Y のノルム）・
    `vertical_error_mm`（Z の絶対値）を機械的に導出し、`observation.
    range_from_camera_mm` をそのまま `PointVerification.
    range_from_camera_mm` へ転記する。これらは `compute_verification_
    statistics`（タスク 5.2）が距離帯集計・最大誤差の内訳を算出する際の
    入力になる。

    Returns:
        検証点ごとの `PointVerification` のタプル。`independent=False` の
        点も含む（除外された事実そのものを呼び出し側が確認できるように、
        取り除かずに残す）。

    Raises:
        CalibrationConfigError: `(spec, observation)` の組でラベルが
            食い違う場合（呼び出し側の誤り。座標系が定まらない条件では
            ないため `CalibrationFailure` ではなくこちらを使う）。
        CalibrationFailure: `reason=FailureReason.
            VERIFICATION_NOT_INDEPENDENT`。独立な検証点が1つも無い場合
            （要件 4.3）。`context` に除外されたラベルの一覧を含める。
    """
    establishment_labels = {result.origin_anchor.label, result.x_axis_anchor.label}

    points: list[PointVerification] = []
    for spec, observation in observations:
        if spec.label != observation.label:
            raise CalibrationConfigError(
                "verification observation label does not match its spec: "
                f"spec.label={spec.label!r} != observation.label="
                f"{observation.label!r}; (spec, observation) pairs must "
                "come from the same VerificationPointSpec"
            )

        independent = spec.label not in establishment_labels
        measured_world_mm = result.transform.apply_point(observation.point_camera_mm)
        error_mm = (
            measured_world_mm[0] - spec.truth_world_mm[0],
            measured_world_mm[1] - spec.truth_world_mm[1],
            measured_world_mm[2] - spec.truth_world_mm[2],
        )
        ex, ey, ez = error_mm
        error_norm_mm = math.sqrt(ex * ex + ey * ey + ez * ez)
        horizontal_error_mm = math.sqrt(ex * ex + ey * ey)
        vertical_error_mm = abs(ez)
        points.append(
            PointVerification(
                label=spec.label,
                independent=independent,
                measured_world_mm=measured_world_mm,
                truth_world_mm=spec.truth_world_mm,
                truth_source=spec.truth_source,
                error_mm=error_mm,
                error_norm_mm=error_norm_mm,
                horizontal_error_mm=horizontal_error_mm,
                vertical_error_mm=vertical_error_mm,
                range_from_camera_mm=observation.range_from_camera_mm,
            )
        )

    independent_count = sum(1 for point in points if point.independent)
    if independent_count == 0:
        excluded_labels = tuple(point.label for point in points)
        raise CalibrationFailure(
            reason=FailureReason.VERIFICATION_NOT_INDEPENDENT,
            detail=(
                "no independent verification points remain: every candidate "
                "either duplicates an establishment marker "
                f"(origin={result.origin_anchor.label!r}, "
                f"x_axis={result.x_axis_anchor.label!r}) or no verification "
                "points were provided at all; a point used to build the "
                "transform trivially 'verifies' as correct by construction "
                "and cannot count as independent confirmation"
            ),
            context={
                "excluded_labels": excluded_labels,
                "origin_label": result.origin_anchor.label,
                "x_axis_label": result.x_axis_anchor.label,
                "candidate_count": len(points),
                "independent_count": independent_count,
            },
        )

    return tuple(points)


# ---------------------------------------------------------------------------
# タスク 5.2: 誤差の分解（バイアス／ばらつき／距離帯）とスケールの独立確認
# （design.md Verifier「Contracts」の一部 / 要件 4.4, 4.5, 9.3）
# ---------------------------------------------------------------------------

DEFAULT_RANGE_BUCKET_EDGES_MM: tuple[float, ...] = (
    0.0,
    1000.0,
    2000.0,
    3000.0,
    4000.0,
    math.inf,
)
"""距離帯集計（`RangeBucket`）の既定境界（mm）。

design.md はこの境界値を明示しない（`RangeBucket` の型は定めるが、境界の
刻み幅は実装判断に委ねている）ため、本モジュールが選んだ暫定スキームで
ある: 1m（1000mm）刻みで `[0, 1000)`, `[1000, 2000)`, `[2000, 3000)`,
`[3000, 4000)`, `[4000, ∞)` の5帯に分ける。RealSense D435 の実用測距
レンジ（およそ 0.2m〜10m 程度）と、`procedure.md` が要求する検証点配置
（tasks.md タスク 7.4: 「最低3点・うち1点は想定投擲距離の端」）を踏まえ、
数m規模の投擲距離帯を複数の帯へ分解し、「遠方だけ誤差が大きい」という
Depth の距離依存誤差特性（読み分け規則、本モジュールの docstring参照）を
検出できる粒度として 1m を選んだ。実測データが蓄積された時点で見直す
暫定値である（`tech.md` 開発標準1: 未実測の数値を固定しすぎない、との
両立のため「暫定であること」自体をここに明記する）。呼び出し側は
`compute_verification_statistics(..., range_bucket_edges_mm=...)` で
独自の境界を指定できる。
"""


@dataclass(frozen=True, slots=True)
class RangeBucket:
    """カメラからの距離帯ごとの誤差集計（design.md Verifier「Contracts」
    `RangeBucket` / tasks.md タスク 5.2 / 要件 4.5, 9.3）。

    半開区間 `[range_lo_mm, range_hi_mm)` に入る独立点だけを集計する。
    点が1つも無い距離帯は `compute_verification_statistics` の出力
    （`VerificationStatistics.range_buckets`）に含めない（0 埋めしない。
    `plan.py` / `result.py` が採る「欠測は省く」方針と同じ考え方）。

    Attributes:
        range_lo_mm: 距離帯の下限（mm、含む）。
        range_hi_mm: 距離帯の上限（mm、含まない）。既定の最終帯では
            `math.inf`（`DEFAULT_RANGE_BUCKET_EDGES_MM` 参照）。
        point_count: この距離帯に入った独立点の数。
        mean_error_norm_mm: この距離帯に入った点の誤差ノルム
            （`PointVerification.error_norm_mm`）の平均。
        max_error_norm_mm: この距離帯に入った点の誤差ノルムの最大値。
    """

    range_lo_mm: float
    range_hi_mm: float
    point_count: int
    mean_error_norm_mm: float
    max_error_norm_mm: float


@dataclass(frozen=True, slots=True)
class ScaleCheck:
    """計画のメジャー実測基線長と、確立結果から算出した基線長の突き合わせ
    （design.md Verifier「Contracts」`ScaleCheck` / tasks.md タスク 5.2 /
    要件 4.4, 9.3）。

    **なぜこれが独立な検証量なのか**: World frame の確立
    （`frame.build_world_frame`）は、原点マーカーから方向マーカーへ向かう
    ベクトルの**向き**（平面内成分）だけを +X 軸の定義に使う。基線長
    （2マーカー間の**絶対的な距離**）は下限判定（`ANCHOR_BASELINE_TOO_
    SHORT`）にのみ使われ、frame の軸そのものの定義（スケール基準）には
    使われない（`plan.py` の `CalibrationPlan.expected_baseline_mm`
    docstring: 「World frame の**定義には使わず**、スケールの独立確認に
    のみ用いる」と同じ整合性）。したがって `measured_baseline_mm`
    （`CalibrationResult.geometry.baseline_mm`、確立結果から算出）を
    メジャー実測の `expected_baseline_mm` と突き合わせることは、frame の
    定義に使った情報（マーカーの向き）とは独立な検証になる（tasks.md
    タスク 5.2: 「この距離は frame の定義に使っていないため独立な検証量に
    なる」）。

    **真値の測り方を記録する**（design.md Verifier「Risks」）:
    `expected_baseline_mm` はメジャー実測値であり、それ自体に測定誤差が
    ある。出典の記録は `CalibrationPlan` 側の責務（`ToleranceSpec.source`
    等と同じ規約）であり、本型はその実測値と算出値の差分のみを扱う。

    Attributes:
        expected_baseline_mm: 計画に記録されたメジャー実測の基線長
            （`CalibrationPlan.expected_baseline_mm`）。未実測なら `None`
            （要件 4.7 と同じ「実測が無ければ判定しない」方針を、ここでは
            「比較できない」という形で反映する）。
        measured_baseline_mm: 確立結果から算出した基線長
            （`CalibrationResult.geometry.baseline_mm`）。
        difference_mm: `measured_baseline_mm - expected_baseline_mm`。
            `expected_baseline_mm` が `None` なら比較対象が無いため `None`
            （0 で埋めない）。
    """

    expected_baseline_mm: float | None
    measured_baseline_mm: float
    difference_mm: float | None


@dataclass(frozen=True, slots=True)
class VerificationStatistics:
    """独立点のみから算出した誤差のバイアス／ばらつき／距離帯分解と、
    基線長のスケール確認（design.md Verifier「Contracts」の一部 /
    tasks.md タスク 5.2 / 要件 4.4, 4.5, 9.3）。

    design.md が示す最終形 `VerificationReport` のうち、本タスクが担う
    フィールドのサブセットである。許容値による合否判定（`tolerance` /
    `verdict`）と結果の識別情報（`calibration_id` 等）・検証点そのもの
    （`points`）はタスク 5.3 が本統計を用いて `VerificationReport` へ
    統合する。

    **読み分け規則**（design.md Verifier「Implementation Notes」/
    tasks.md タスク 5.2 が docstring への明記を要求する）:

        バイアス支配なら座標系、ばらつき支配なら観測、遠方だけ大きいなら
        Depth の距離特性。

    `bias_mm` が支配的に大きければ World frame の確立（座標系）そのものに
    系統的なずれがある可能性が高く、`scatter_rms_mm` が支配的に大きければ
    個々の観測のばらつき（マーカー観測・Depth のランダムノイズ）が原因で
    ある可能性が高い。`range_buckets` のうち遠方の帯だけ誤差が大きければ、
    Depth センサの距離依存誤差特性が原因である可能性が高い。

    Attributes:
        independent_point_count: バイアス・ばらつき・距離帯集計に用いた
            独立点の数（`PointVerification.independent is True` の点数。
            確立用マーカーと重複する点はここに含めない。要件 4.3 の踏襲）。
        bias_mm: 軸ごとの平均オフセット（バイアス）。独立点の `error_mm`
            の軸ごとの平均（要件 4.4）。
        scatter_rms_mm: バイアスを除去した残差の二乗平均平方根
            （ばらつき）。軸ごとのバイアスを引いた残差ベクトルのノルムの
            二乗平均平方根をスカラー1つで表す（design.md Verifier
            「Contracts」`VerificationReport.scatter_rms_mm: float` に
            合わせる。要件 4.4）。
        max_error_norm_mm: 独立点の誤差ノルムの最大値（全体、要件 4.4）。
        max_horizontal_error_mm: 独立点の水平誤差（X・Y のノルム）の
            最大値。
        max_vertical_error_mm: 独立点の鉛直誤差（Z の絶対値）の最大値。
        range_buckets: カメラからの距離帯ごとの集計。点が1つも無い帯は
            含めない（`RangeBucket` の docstring参照）。
        scale_check: メジャー実測基線長と算出基線長の突き合わせ。
    """

    independent_point_count: int
    bias_mm: tuple[float, float, float]
    scatter_rms_mm: float
    max_error_norm_mm: float
    max_horizontal_error_mm: float
    max_vertical_error_mm: float
    range_buckets: tuple[RangeBucket, ...]
    scale_check: ScaleCheck


def _compute_range_buckets(
    independent_points: Sequence[PointVerification],
    edges_mm: Sequence[float],
) -> tuple[RangeBucket, ...]:
    """`independent_points`（呼び出し元で独立点にフィルタ済み）を
    `edges_mm` が定める半開区間 `[lo, hi)` へ分け、点が1つも無い帯を
    結果から省く（`RangeBucket` の docstring参照）。
    """
    buckets: list[RangeBucket] = []
    for lo, hi in zip(edges_mm, edges_mm[1:]):
        bucket_points = [
            point for point in independent_points if lo <= point.range_from_camera_mm < hi
        ]
        if not bucket_points:
            continue
        norms = [point.error_norm_mm for point in bucket_points]
        buckets.append(
            RangeBucket(
                range_lo_mm=lo,
                range_hi_mm=hi,
                point_count=len(bucket_points),
                mean_error_norm_mm=sum(norms) / len(norms),
                max_error_norm_mm=max(norms),
            )
        )
    return tuple(buckets)


def compute_verification_statistics(
    result: CalibrationResult,
    points: Sequence[PointVerification],
    *,
    expected_baseline_mm: float | None,
    range_bucket_edges_mm: Sequence[float] = DEFAULT_RANGE_BUCKET_EDGES_MM,
) -> VerificationStatistics:
    """独立点のみから、誤差をバイアス・ばらつき・距離帯へ分解し、計画の
    メジャー実測基線長と確立結果の算出基線長を突き合わせる（design.md
    Verifier「Contracts」/ tasks.md タスク 5.2 / 要件 4.4, 4.5, 9.3）。

    **独立点のみを集計に用いる**（要件 4.3 を踏襲）: `points` のうち
    `independent is False` の点（`evaluate_verification_points` が確立用
    マーカーと重複すると判定した点）は、バイアス・ばらつき・距離帯集計の
    いずれからも除外する。確立に使った点は `result.transform` によって
    定義上誤差ゼロで再現されるため、集計に混ぜると系統誤差・ばらつきを
    実際より小さく見せてしまう。

    **読み分け規則**: バイアス支配なら座標系、ばらつき支配なら観測、遠方
    だけ大きいなら Depth の距離特性（`VerificationStatistics` の docstring
    に詳細を記す。tasks.md タスク 5.2 の明示的指示）。

    **バイアスとばらつきの分離**: `bias_mm` は独立点の `error_mm` の
    軸ごとの平均、`scatter_rms_mm` はそこから `bias_mm` を引いた残差の
    RMS である。したがって、観測へ一様なバイアスを注入しても
    `scatter_rms_mm`（残差の広がり）は変化しない。これは平均を引いた後の
    値のみに依存する量だからであり、この分離可能性は本関数の正しさの
    中心的な性質である（`tests/world_frame_calibration/
    test_world_frame_calibration_verify.py::TestBiasScatterSeparability`
    が既知量のバイアス注入で確認する）。

    Args:
        result: `points` の算出元となった保存済みキャリブレーション結果。
            `result.geometry.baseline_mm`（算出済み基線長）をスケール
            確認に用いる。
        points: `evaluate_verification_points(result, observations)` が
            返す検証点ごとの結果。`independent=False` の点を含んでいて
            よい（本関数が集計から除外する）。
        expected_baseline_mm: 計画に記録されたメジャー実測の基線長
            （`CalibrationPlan.expected_baseline_mm`）。未実測なら `None`。
        range_bucket_edges_mm: 距離帯の境界（mm、昇順、半開区間）。既定は
            `DEFAULT_RANGE_BUCKET_EDGES_MM`（1m 刻み。本モジュールが選んだ
            暫定スキーム。同定数の docstring参照）。

    Returns:
        独立点から算出した `VerificationStatistics`。

    Raises:
        CalibrationConfigError: `points` に独立点が1つも含まれない場合
            （呼び出し側の誤り）。`evaluate_verification_points` は独立点
            ゼロの `observations` に対して既に `CalibrationFailure
            (VERIFICATION_NOT_INDEPENDENT)` を送出しているため、通常の
            呼び出し順序（`evaluate_verification_points` の戻り値を
            そのまま渡す）ではここに到達しない。到達した場合は、独立点
            ゼロの `points` を人為的に組み立てて渡した呼び出し側の誤りで
            あり、座標系が定まらない条件ではないため
            `CalibrationFailure` ではなくこちらを送出する。
    """
    independent_points = [point for point in points if point.independent]
    if not independent_points:
        raise CalibrationConfigError(
            "compute_verification_statistics received no independent points: "
            "evaluate_verification_points already rejects zero independent "
            "points via CalibrationFailure(VERIFICATION_NOT_INDEPENDENT), so "
            "an empty independent subset here means the caller passed a "
            "manually constructed or pre-filtered `points` sequence with no "
            "independent entries"
        )

    axis_count = 3
    point_count = len(independent_points)
    bias_mm = tuple(
        sum(point.error_mm[axis] for point in independent_points) / point_count
        for axis in range(axis_count)
    )

    residual_squared_sum = 0.0
    for point in independent_points:
        for axis in range(axis_count):
            residual = point.error_mm[axis] - bias_mm[axis]
            residual_squared_sum += residual * residual
    scatter_rms_mm = math.sqrt(residual_squared_sum / point_count)

    max_error_norm_mm = max(point.error_norm_mm for point in independent_points)
    max_horizontal_error_mm = max(point.horizontal_error_mm for point in independent_points)
    max_vertical_error_mm = max(point.vertical_error_mm for point in independent_points)

    range_buckets = _compute_range_buckets(independent_points, range_bucket_edges_mm)

    measured_baseline_mm = result.geometry.baseline_mm
    difference_mm = (
        None if expected_baseline_mm is None else measured_baseline_mm - expected_baseline_mm
    )
    scale_check = ScaleCheck(
        expected_baseline_mm=expected_baseline_mm,
        measured_baseline_mm=measured_baseline_mm,
        difference_mm=difference_mm,
    )

    return VerificationStatistics(
        independent_point_count=point_count,
        bias_mm=bias_mm,  # type: ignore[arg-type]
        scatter_rms_mm=scatter_rms_mm,
        max_error_norm_mm=max_error_norm_mm,
        max_horizontal_error_mm=max_horizontal_error_mm,
        max_vertical_error_mm=max_vertical_error_mm,
        range_buckets=range_buckets,
        scale_check=scale_check,
    )
