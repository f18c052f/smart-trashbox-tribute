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

**タスク境界について**: 本モジュールが担うのは要件 4.1, 4.2, 4.3, 4.9
（検証点の World 座標算出・独立性の検査）のみである。誤差をバイアスと
ばらつきへ分解すること、距離帯ごとの集計、メジャー実測基線長との突き合わせ
（要件 4.4, 4.5, 9.3）はタスク 5.2 が、許容値による合否判定・レポートとしての
統合（要件 4.6, 4.7, 4.10）はタスク 5.3 が、それぞれ本モジュールを拡張して
担う（tasks.md「5. 独立検証」節、`_Depends: 5.1_`）。したがって
`PointVerification` は design.md が示す最終形の `PointError`（`verdict` /
`error_norm_mm` / `horizontal_error_mm` / `vertical_error_mm` などを含む）の
サブセットであり、それらのフィールドは後続タスクが追加する。
"""

from __future__ import annotations

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
    "PointVerification",
    "evaluate_verification_points",
]


@dataclass(frozen=True, slots=True)
class PointVerification:
    """1つの検証点について算出した World 座標と、既知位置との軸ごとの差分。

    design.md Verifier「Contracts」の `PointError` のうち、本タスク
    （5.1）が担うフィールドのサブセット。誤差のバイアス・ばらつき分解
    （`error_norm_mm` / `horizontal_error_mm` / `vertical_error_mm` 等）や
    合否判定（`verdict`）はタスク 5.2 / 5.3 が追加する。

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
    """

    label: str
    independent: bool
    measured_world_mm: tuple[float, float, float]
    truth_world_mm: tuple[float, float, float]
    truth_source: str
    error_mm: tuple[float, float, float]


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
        points.append(
            PointVerification(
                label=spec.label,
                independent=independent,
                measured_world_mm=measured_world_mm,
                truth_world_mm=spec.truth_world_mm,
                truth_source=spec.truth_source,
                error_mm=error_mm,
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
