"""world_frame_calibration.anchors（マーカー観測）の検証（tasks.md タスク 2.4 /
要件 2.2, 2.6, 5.2, 9.3）。

design.md「L3-L5: 推定と確立」節の AnchorObserver コンポーネント契約を固定する。
本テストが確認すること（tasks.md タスク 2.4「観測可能な完了状態」および
Critical constraints）:

- 選定基準は**床平面からの符号付き距離**（`plane.signed_distance_mm`）のみで
  あり、画像上の位置や絶対的な奥行きでは判定しないこと（高さ帯外の点が
  一切使われないこと。上下どちらの方向にも外れる点を混在させて確認する）
- 選ばれた点の**ロバスト代表値**（`linalg.robust_center`）を求め、**床平面へ
  法線方向に投影した点**（`plane.project_onto_plane`）が観測結果になること
- 範囲内に少数の外れ値を混ぜても代表点がほとんど動かないこと
  （`robust_center` の頑健性を活用していることの直接証拠）
- 点数が `limits.min_anchor_samples` に満たない場合、**どのマーカーかを
  ラベルで特定できる形**で `CalibrationFailure(ANCHOR_NOT_FOUND)` として
  失敗すること
- 確立用マーカー（`AnchorRole.ORIGIN` / `X_AXIS`）と検証点
  （`AnchorRole.VERIFICATION`）が**同一の関数**で観測でき、role 以外の結果が
  一致すること（観測方法の違いが検証結果に混入しないことの直接証拠）
- 既知のカメラ姿勢・既知のマーカー配置から生成した合成 Depth
  （`tests/world_frame_calibration/synthetic.py`）に対しても、マーカー中心の
  World 座標が正しく復元されること（現実的なシナリオでの疎通確認）

このテストファイルは `world_frame_calibration.anchors` がまだ存在しない時点
では `ModuleNotFoundError` で失敗する（RED）。

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_anchors.py` は使わずプレフィックス付きの
`test_world_frame_calibration_anchors.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6参照）。

`tests/world_frame_calibration/synthetic.py` は `tests/sensing_foundation/
synthetic.py` と裸のモジュール名が衝突するため、`importlib.util.
spec_from_file_location` によるパス指定ロードを使う（`synthetic.py` の
モジュール docstring / `test_world_frame_calibration_plane.py` の参照実装の
とおり）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from world_frame_calibration.errors import CalibrationFailure, FailureReason
from world_frame_calibration.plan import AnchorSpec, PlanLimits
from world_frame_calibration.types import (
    AnchorRole,
    DepthImage,
    Intrinsics,
    PixelRegion,
    Plane,
    PlaneQuality,
    StreamSignature,
)

# --- tests/world_frame_calibration/synthetic.py の衝突耐性ロード ---------
_SYNTHETIC_PATH = Path(__file__).resolve().parent / "synthetic.py"
_spec = importlib.util.spec_from_file_location(
    "world_frame_calibration._test_synthetic_for_anchors", _SYNTHETIC_PATH
)
assert _spec is not None and _spec.loader is not None
_synthetic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _synthetic
_spec.loader.exec_module(_synthetic)

MarkerBox = _synthetic.MarkerBox
camera_pose_looking_at_floor = _synthetic.camera_pose_looking_at_floor
generate_synthetic_depth = _synthetic.generate_synthetic_depth


# ---------------------------------------------------------------------------
# ヘルパ
# ---------------------------------------------------------------------------


def _intrinsics(
    *,
    width_px: int = 40,
    height_px: int = 40,
    fx_px: float = 100.0,
    fy_px: float = 100.0,
    ppx_px: float = 0.0,
    ppy_px: float = 0.0,
) -> Intrinsics:
    return Intrinsics(
        width_px=width_px,
        height_px=height_px,
        fx_px=fx_px,
        fy_px=fy_px,
        ppx_px=ppx_px,
        ppy_px=ppy_px,
        model="none",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )


def _signature(intr: Intrinsics) -> StreamSignature:
    return StreamSignature(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
    )


def _depth_image(
    depth_mm: np.ndarray, intr: Intrinsics, *, frames_used: int = 1
) -> DepthImage:
    valid_count = np.where(np.isnan(depth_mm), 0, 1).astype(np.int32)
    return DepthImage(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=frames_used,
        intrinsics=intr,
        signature=_signature(intr),
        source_kind="simulated",
    )


def _full_region(intr: Intrinsics) -> PixelRegion:
    return PixelRegion(x0_px=0, y0_px=0, x1_px=intr.width_px, y1_px=intr.height_px)


def _flat_plane(distance_mm: float) -> Plane:
    """カメラ座標系で `z = distance_mm` の水平面（法線 = (0, 0, 1)）を返す。

    `AnchorObserver` はどのような `Plane` が渡されても `signed_distance_mm`
    / `project_onto_plane` を通じて同じロジックで動くため、このテスト用の
    平面はシーンの物理的な整合性を必要としない。「ある z 座標を境に高さ帯へ
    出入りする点群」を作るための最小限の道具として使う。
    """
    quality = PlaneQuality(
        points_considered=1000,
        inlier_count=900,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.0,
        residual_abs_p95_mm=2.0,
        residual_rms_mm=1.5,
        frames_used=1,
        incidence_angle_deg=45.0,
        rng_seed=0,
    )
    return Plane(normal=(0.0, 0.0, 1.0), distance_mm=distance_mm, quality=quality)


def _place_points(
    depth_mm: np.ndarray,
    intr: Intrinsics,
    *,
    u_range: range,
    v_range: range,
    z_mm: float,
) -> None:
    """`(u, v)` の格子へ、`deproject_pixel` で厳密に `z_mm` になる Depth 値を置く。

    `x = (u - ppx) / fx * z`、`y = (v - ppy) / fy * z` の関係を使えば、
    整数の `(u, v)` へ Depth 値 `z_mm` を単純に代入するだけで、
    `deproject_region` が返すカメラ座標系の点の z 成分が厳密に `z_mm` になる
    （x, y は u, v にそのまま比例する）。これにより、高さ帯（床平面からの
    符号付き距離）で選ばれる点群を、丸め誤差なく正確に構成できる。
    """
    for v in v_range:
        for u in u_range:
            depth_mm[v, u] = z_mm


# ---------------------------------------------------------------------------
# 高さ帯: 床平面からの符号付き距離のみで判定する（画像上の位置・絶対的な
# 奥行きでは判定しない）
# ---------------------------------------------------------------------------


def test_observe_anchor_selects_only_points_within_the_height_band() -> None:
    """高さ帯外の点（上にも下にも外れる点）が一切使われない
    （design.md AnchorObserver「Invariants」/ tasks.md タスク 2.4）。
    """
    intr = _intrinsics(width_px=30, height_px=30)
    depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)

    plane = _flat_plane(distance_mm=1000.0)  # 床平面: カメラ座標系で z = 1000

    # 高さ帯 (10, 60) 内: u,v in [0, 9] x [0, 9] -> 100点、z = 1030 (高さ 30mm)
    _place_points(depth_mm, intr, u_range=range(0, 10), v_range=range(0, 10), z_mm=1030.0)
    # 高さ帯より低い（床のノイズ相当）: z = 1005 (高さ 5mm < 下限 10mm)
    _place_points(depth_mm, intr, u_range=range(15, 20), v_range=range(15, 20), z_mm=1005.0)
    # 高さ帯より高い（別の背の高い物体相当）: z = 1200 (高さ 200mm > 上限 60mm)
    _place_points(depth_mm, intr, u_range=range(25, 29), v_range=range(25, 29), z_mm=1200.0)

    image = _depth_image(depth_mm, intr)
    spec = AnchorSpec(
        label="origin", region=_full_region(intr), height_band_mm=(10.0, 60.0)
    )
    limits = PlanLimits(min_anchor_samples=50)

    from world_frame_calibration import anchors

    result = anchors.observe_anchor(image, plane, spec, AnchorRole.ORIGIN, limits)

    # 高さ帯内の点だけが使われた(100点ちょうど)
    assert result.sample_count == 100

    # 選ばれた点の中央値: u,v in [0,9] の格子 -> 中央値 u=v=4.5 (fx=fy=100, z=1030)
    expected_x_mm = 4.5 / 100.0 * 1030.0
    expected_y_mm = 4.5 / 100.0 * 1030.0
    assert result.point_camera_mm[0] == pytest.approx(expected_x_mm, abs=1e-6)
    assert result.point_camera_mm[1] == pytest.approx(expected_y_mm, abs=1e-6)
    assert result.point_camera_mm[2] == pytest.approx(1030.0, abs=1e-6)

    # 高さは床平面からの符号付き距離として厳密に 30mm
    assert result.height_above_plane_mm == pytest.approx(30.0, abs=1e-6)

    # 平面へ法線方向 (0,0,1) に投影 -> x, y は不変、z は平面の distance_mm
    assert result.point_on_plane_mm[0] == pytest.approx(expected_x_mm, abs=1e-6)
    assert result.point_on_plane_mm[1] == pytest.approx(expected_y_mm, abs=1e-6)
    assert result.point_on_plane_mm[2] == pytest.approx(1000.0, abs=1e-6)


def test_observe_anchor_selection_is_independent_of_absolute_depth_and_pixel_position() -> None:
    """選定基準が「絶対的な奥行き」や「画像上の位置」ではなく、あくまで
    「床平面からの符号付き距離」であることを、同じ高さ帯・異なる平面の
    `distance_mm`（＝異なる絶対奥行き）で確認する（design.md AnchorObserver
    「Invariants」）。
    """
    intr = _intrinsics(width_px=20, height_px=20)
    limits = PlanLimits(min_anchor_samples=50)
    spec = AnchorSpec(
        label="origin", region=_full_region(intr), height_band_mm=(10.0, 60.0)
    )

    from world_frame_calibration import anchors

    for distance_mm in (500.0, 1000.0, 3000.0):
        depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
        _place_points(
            depth_mm,
            intr,
            u_range=range(0, 10),
            v_range=range(0, 10),
            z_mm=distance_mm + 30.0,
        )
        image = _depth_image(depth_mm, intr)
        plane = _flat_plane(distance_mm=distance_mm)

        result = anchors.observe_anchor(image, plane, spec, AnchorRole.ORIGIN, limits)

        # 絶対奥行き（distance_mm + 30）が全く異なっていても、床平面からの
        # 高さは常に 30mm、選ばれる点数も常に 100点で一定
        assert result.sample_count == 100
        assert result.height_above_plane_mm == pytest.approx(30.0, abs=1e-6)


# ---------------------------------------------------------------------------
# ロバスト性: 範囲内に少数の外れ値を混ぜても代表点が動かない
# ---------------------------------------------------------------------------


def test_observe_anchor_robust_center_is_unmoved_by_a_few_outliers_within_the_region() -> None:
    """範囲内・高さ帯内に少数の外れ値（本来のマーカーから離れた位置の点）を
    混ぜても、`robust_center`（中央値ベース）の頑健性により代表点がほとんど
    動かないこと（tasks.md タスク 2.4「観測可能な完了状態」）。
    """
    intr = _intrinsics(width_px=130, height_px=130, fx_px=100.0, fy_px=100.0)
    plane = _flat_plane(distance_mm=1000.0)
    spec = AnchorSpec(
        label="origin", region=_full_region(intr), height_band_mm=(10.0, 60.0)
    )

    # 本来のマーカー: u,v in [0,20] x [0,20] -> 441点 (21x21グリッド)、
    # z=1030 (高さ30mm、高さ帯内)。各 u 値がちょうど21点を持つ正方グリッドに
    # することで、中央値がグリッドの中心 u=v=10 にちょうど乗り、後段の少数の
    # 外れ値追加が中央値の属する「バケツ」を跨がないための余裕(±10点)を作る。
    depth_clean = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
    _place_points(depth_clean, intr, u_range=range(0, 21), v_range=range(0, 21), z_mm=1030.0)

    # 外れ値: 同じ高さ帯内(z=1030)だが、遠く離れた画素位置に少数(5点)だけ置く
    depth_with_outliers = np.array(depth_clean, copy=True)
    outlier_pixels = [(100, 100), (105, 101), (110, 102), (100, 110), (115, 115)]
    for u, v in outlier_pixels:
        depth_with_outliers[v, u] = 1030.0

    image_clean = _depth_image(depth_clean, intr)
    image_with_outliers = _depth_image(depth_with_outliers, intr)

    from world_frame_calibration import anchors

    limits_clean = PlanLimits(min_anchor_samples=50)
    limits_outliers = PlanLimits(min_anchor_samples=50)

    result_clean = anchors.observe_anchor(
        image_clean, plane, spec, AnchorRole.ORIGIN, limits_clean
    )
    result_with_outliers = anchors.observe_anchor(
        image_with_outliers, plane, spec, AnchorRole.ORIGIN, limits_outliers
    )

    assert result_clean.sample_count == 441
    assert result_with_outliers.sample_count == 446

    # 代表点(床平面へ投影した点)は、少数(5点)の外れ値混入でまったく動かない
    # (中央値が属するバケツに十分な余裕があるため。中央値ベースの頑健性の
    # 直接証拠)
    for axis in range(3):
        assert result_with_outliers.point_on_plane_mm[axis] == pytest.approx(
            result_clean.point_on_plane_mm[axis], abs=1e-6
        )

    # 一方で散らばり(spread_mm)は外れ値の混入を反映して増える
    # (中央値は動かなくても、散らばりは中央値からの距離の中央値であり、
    # 外れ値の存在を示す指標として機能する)
    assert result_with_outliers.spread_mm > result_clean.spread_mm


# ---------------------------------------------------------------------------
# 点数不足: どのマーカーかを特定できる形で ANCHOR_NOT_FOUND
# ---------------------------------------------------------------------------


def test_observe_anchor_raises_anchor_not_found_when_samples_are_insufficient() -> None:
    """高さ帯内の点数が `limits.min_anchor_samples` に満たなければ
    `CalibrationFailure(ANCHOR_NOT_FOUND)` として失敗し、`context` に
    ラベル・範囲・実際の点数が入る（design.md AnchorObserver「Validation」/
    要件 5.2）。
    """
    intr = _intrinsics(width_px=20, height_px=20)
    depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
    # 高さ帯内の点はわずか3点だけ
    _place_points(depth_mm, intr, u_range=range(0, 3), v_range=range(0, 1), z_mm=1030.0)
    image = _depth_image(depth_mm, intr)
    plane = _flat_plane(distance_mm=1000.0)
    region = _full_region(intr)
    spec = AnchorSpec(label="x_axis", region=region, height_band_mm=(10.0, 60.0))
    limits = PlanLimits(min_anchor_samples=50)

    from world_frame_calibration import anchors

    with pytest.raises(CalibrationFailure) as exc_info:
        anchors.observe_anchor(image, plane, spec, AnchorRole.X_AXIS, limits)

    assert exc_info.value.reason == FailureReason.ANCHOR_NOT_FOUND
    # どのマーカーが見つからなかったか、ラベルで特定できる
    context = exc_info.value.context
    label_identifiable = context.get("label") == "x_axis" or "x_axis" in exc_info.value.detail
    assert label_identifiable
    assert context.get("sample_count") == 3
    assert context.get("min_anchor_samples") == 50


def test_observe_anchor_failure_identifies_the_specific_marker_label() -> None:
    """異なるラベルのマーカーがそれぞれ不足したとき、失敗がそれぞれ正しい
    ラベルを指す（「どれかを報告」ではなく「どれを」報告できることの確認。
    要件 5.2）。
    """
    intr = _intrinsics(width_px=20, height_px=20)
    plane = _flat_plane(distance_mm=1000.0)
    region = _full_region(intr)
    limits = PlanLimits(min_anchor_samples=50)

    from world_frame_calibration import anchors

    depth_empty = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
    image_empty = _depth_image(depth_empty, intr)

    for label, role in (
        ("origin", AnchorRole.ORIGIN),
        ("x_axis", AnchorRole.X_AXIS),
        ("verification_point_1", AnchorRole.VERIFICATION),
    ):
        spec = AnchorSpec(label=label, region=region, height_band_mm=(10.0, 60.0))
        with pytest.raises(CalibrationFailure) as exc_info:
            anchors.observe_anchor(image_empty, plane, spec, role, limits)
        assert exc_info.value.reason == FailureReason.ANCHOR_NOT_FOUND
        context = exc_info.value.context
        label_identifiable = (
            context.get("label") == label or label in exc_info.value.detail
        )
        assert label_identifiable, f"failure for {label!r} did not identify the marker"


# ---------------------------------------------------------------------------
# 同一関数: 確立用マーカー(ORIGIN/X_AXIS)と検証点(VERIFICATION)で同じ関数
# ---------------------------------------------------------------------------


def test_observe_anchor_uses_the_same_function_for_establishment_and_verification_roles() -> None:
    """同一の観測入力に対し、`role` だけを変えて呼び出すと、結果は `role` と
    `label`（呼び出し時に渡したラベル）以外まったく同一になる。これは
    「確立用マーカーと検証点で同一の関数を使う」という設計制約
    （design.md AnchorObserver「Integration」/ tasks.md タスク 2.4）の直接的な
    観測可能な証拠であり、内部で role ごとに異なるロジックへ分岐していない
    ことを保証する。
    """
    intr = _intrinsics(width_px=30, height_px=30)
    depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
    _place_points(depth_mm, intr, u_range=range(0, 10), v_range=range(0, 10), z_mm=1030.0)
    image = _depth_image(depth_mm, intr)
    plane = _flat_plane(distance_mm=1000.0)
    region = _full_region(intr)
    limits = PlanLimits(min_anchor_samples=50)

    from world_frame_calibration import anchors

    spec_origin = AnchorSpec(label="p1", region=region, height_band_mm=(10.0, 60.0))
    spec_verification = AnchorSpec(label="p1", region=region, height_band_mm=(10.0, 60.0))

    result_as_origin = anchors.observe_anchor(
        image, plane, spec_origin, AnchorRole.ORIGIN, limits
    )
    result_as_verification = anchors.observe_anchor(
        image, plane, spec_verification, AnchorRole.VERIFICATION, limits
    )

    assert result_as_origin.role == AnchorRole.ORIGIN
    assert result_as_verification.role == AnchorRole.VERIFICATION

    assert result_as_origin.point_camera_mm == result_as_verification.point_camera_mm
    assert result_as_origin.point_on_plane_mm == result_as_verification.point_on_plane_mm
    assert (
        result_as_origin.height_above_plane_mm == result_as_verification.height_above_plane_mm
    )
    assert result_as_origin.sample_count == result_as_verification.sample_count
    assert result_as_origin.spread_mm == result_as_verification.spread_mm
    assert (
        result_as_origin.range_from_camera_mm == result_as_verification.range_from_camera_mm
    )


# ---------------------------------------------------------------------------
# 色・柄・既知寸法を要求しない (要件 2.6)
# ---------------------------------------------------------------------------


def test_observe_anchor_does_not_require_color_pattern_or_known_dimensions() -> None:
    """`AnchorSpec` / `observe_anchor` のシグネチャに色・柄・寸法の指定が
    一切無く、範囲と高さ帯だけで観測できる（要件 2.6）。マーカーの高さは
    投影によって最終結果（`point_on_plane_mm`）から消える。
    """
    intr = _intrinsics(width_px=20, height_px=20)
    plane = _flat_plane(distance_mm=1000.0)
    region = _full_region(intr)
    limits = PlanLimits(min_anchor_samples=10)

    from world_frame_calibration import anchors

    # 高さ 30mm のマーカーと、高さ 55mm の別マーカー: どちらも高さ帯 (10,60)
    # 内に収まるので、色・柄・寸法の情報を一切与えずに観測できる
    for height_mm in (30.0, 55.0):
        depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
        _place_points(
            depth_mm, intr, u_range=range(0, 10), v_range=range(0, 10),
            z_mm=1000.0 + height_mm,
        )
        image = _depth_image(depth_mm, intr)
        spec = AnchorSpec(label="m", region=region, height_band_mm=(10.0, 60.0))

        result = anchors.observe_anchor(image, plane, spec, AnchorRole.ORIGIN, limits)

        # マーカーの高さ自体は投影で消え、平面上の z は常に plane.distance_mm
        assert result.point_on_plane_mm[2] == pytest.approx(1000.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 現実的なシナリオ: 合成 Depth (床平面 + 直方体マーカー) からの復元
# ---------------------------------------------------------------------------


def test_observe_anchor_recovers_marker_center_from_synthetic_depth() -> None:
    """既知のカメラ姿勢・既知のマーカー配置から生成した合成 Depth に対し、
    観測したマーカー中心（カメラ座標系、平面投影後）が、正解のカメラ→World
    変換から解析的に計算した「マーカー中心を床平面(World z=0)へ投影し
    カメラ座標系へ戻した点」と数値誤差の範囲で一致する（tasks.md タスク 2.4
    「観測可能な完了状態」/ 要件 9.3 のハードウェア非依存の現実的検証）。

    カメラは真下（pitch=90）を見込むため、視野内には床平面と2つのマーカー
    箱（目的のマーカーと、高さ帯の外側にある背の高い別物体）が入る。
    """
    intr = _intrinsics(
        width_px=200, height_px=200, fx_px=150.0, fy_px=150.0,
        ppx_px=100.0, ppy_px=100.0,
    )
    pose = camera_pose_looking_at_floor(
        position_world_mm=(0.0, 0.0, 2000.0), yaw_deg=0.0, pitch_deg=90.0
    )

    target_marker = MarkerBox(
        label="origin",
        center_xy_world_mm=(0.0, 0.0),
        size_xy_mm=(300.0, 300.0),
        height_mm=30.0,
    )
    # 高さ帯 (10, 60) の外側にある背の高い別の物体（誤って選ばれないことの確認）
    tall_object = MarkerBox(
        label="clutter",
        center_xy_world_mm=(800.0, 800.0),
        size_xy_mm=(100.0, 100.0),
        height_mm=300.0,
    )

    frame = generate_synthetic_depth(
        pose, intr, markers=[target_marker, tall_object], rng_seed=0
    )
    region = _full_region(intr)

    rotation = pose.rotation_matrix()  # columns = camera axes in world
    position = pose.position_vector()
    plane = Plane(
        normal=tuple(rotation.T @ np.array([0.0, 0.0, 1.0])),
        distance_mm=-float(position[2]),
        quality=PlaneQuality(
            points_considered=10000,
            inlier_count=9000,
            inlier_ratio=0.9,
            residual_abs_p50_mm=1.0,
            residual_abs_p95_mm=2.0,
            residual_rms_mm=1.5,
            frames_used=1,
            incidence_angle_deg=90.0,
            rng_seed=0,
        ),
    )

    spec = AnchorSpec(label="origin", region=region, height_band_mm=(10.0, 60.0))
    limits = PlanLimits(min_anchor_samples=50)

    from world_frame_calibration import anchors

    result = anchors.observe_anchor(frame.depth, plane, spec, AnchorRole.ORIGIN, limits)

    # 正解: World (0, 0, 0)（マーカー中心を床平面へ投影した点）をカメラ座標系へ
    # 変換した点。 world_point = rotation @ camera_point + position より
    # camera_point = rotation.T @ (world_point - position)
    expected_world_point = np.array([0.0, 0.0, 0.0])
    expected_camera_point = rotation.T @ (expected_world_point - position)

    np.testing.assert_allclose(
        result.point_on_plane_mm, expected_camera_point, atol=5.0
    )
    # 平面上にある（符号付き距離が数値誤差の範囲で 0）
    from world_frame_calibration import plane as plane_module

    signed_distance = plane_module.signed_distance_mm(
        plane, np.array([result.point_on_plane_mm])
    )[0]
    assert signed_distance == pytest.approx(0.0, abs=1e-6)

    # 背の高い別物体(clutter)の点は高さ帯外のため一切影響しない
    assert result.height_above_plane_mm == pytest.approx(30.0, abs=2.0)

    # frames_used, region はそのまま観測結果に反映される
    assert result.frames_used == frame.depth.frames_used
    assert result.region == region
    assert result.label == "origin"


# ---------------------------------------------------------------------------
# range_from_camera_mm: カメラからの距離
# ---------------------------------------------------------------------------


def test_observe_anchor_reports_range_from_camera() -> None:
    """`range_from_camera_mm` は観測結果（投影前のロバスト代表点）の
    カメラ（原点）からの距離を表す。
    """
    intr = _intrinsics(width_px=20, height_px=20)
    depth_mm = np.full((intr.height_px, intr.width_px), np.nan, dtype=np.float64)
    _place_points(depth_mm, intr, u_range=range(0, 1), v_range=range(0, 1), z_mm=1030.0)
    # min_anchor_samples=1 なので1点で観測できる。中央値はその1点自身。
    image = _depth_image(depth_mm, intr)
    plane = _flat_plane(distance_mm=1000.0)
    region = _full_region(intr)
    spec = AnchorSpec(label="p", region=region, height_band_mm=(10.0, 60.0))
    limits = PlanLimits(min_anchor_samples=1)

    from world_frame_calibration import anchors

    result = anchors.observe_anchor(image, plane, spec, AnchorRole.ORIGIN, limits)

    expected_range = float(np.linalg.norm(np.array(result.point_camera_mm)))
    assert result.range_from_camera_mm == pytest.approx(expected_range, abs=1e-6)
