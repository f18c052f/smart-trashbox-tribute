"""既知姿勢からの合成 Depth 生成器（design.md「File Structure Plan」
`tests/world_frame_calibration/synthetic.py` / SyntheticDepth コンポーネント
/ tasks.md タスク 1.6 / 要件 9.1, 9.5）。

**このモジュールをテストツリーに置く理由**（`src/world_frame_calibration/`
には置かない）: 投擲物理・ノイズ生成は `trajectory-simulator` の責務であり
本 Spec の責務外である（tasks.md タスク 1.6）。本モジュールが生成するのは
投擲物理ではなく、既知の静的な幾何（床平面・直方体マーカー・任意の外れ値
平面）を既知のカメラ姿勢からレンダリングした合成 Depth 画像であり、これは
本 Spec 自身の検証（要件 9）のためだけに存在するテスト専用フィクスチャで
ある。

**⚠️ モジュール名の衝突に関する注意（将来このモジュールを import するすべての
テストファイルへ向けた警告）**: `tests/sensing_foundation/synthetic.py` が
既に**同じ最上位モジュール名 `synthetic`** を名乗っている
（`tests/sensing_foundation` タスク 1.7）。`tests/**` 配下には `__init__.py`
が一切無いため、pytest の既定インポートモード（`prepend`）はモジュールを
「ディレクトリを冠さない裸の名前」で `sys.modules` にキャッシュする。
そのため、同一の pytest セッション内で両方のテストスイートが収集される
場合（＝フルスイート実行時は常に）、単純な `from synthetic import ...` は
**収集順序次第でどちらのファイルが「勝つか」が決まる**という壊れやすい
状態になる。本モジュールを import するテストファイルは、必ず
`importlib.util.spec_from_file_location` で本ファイルを**パス指定**で
読み込み、衝突しない一意な名前（例:
`"world_frame_calibration._test_synthetic"`）で `sys.modules` に登録する
こと。`tests/world_frame_calibration/test_world_frame_calibration_synthetic.py`
がその参照実装である。

**幾何モデルの方針**: `sensing_foundation.geometry.deproject_pixel`
（画素 + Depth → カメラ座標系の3次元点）の**厳密な逆演算**として実装する。
すなわち、画素ごとにカメラ座標系のレイ

    d_cam = ((u_px - ppx_px) / fx_px, (v_px - ppy_px) / fy_px, 1.0)

を張り、既知の `CameraPose`（カメラ → World の剛体変換）で World 座標系へ
写し、床平面（World z = 0）・直方体マーカー・外れ値平面のうち**最も近い
交点**を探索する。交点が見つかった深度値 `z_cam`（= レイのカメラ座標系 Z
成分）をそのまま画素の Depth 値（mm）とする。これにより

    deproject_pixel(intrinsics, u_px, v_px, depth_mm[v_px, u_px])
        == z_cam * d_cam
        == (交点の World 座標を CameraPose で逆変換したカメラ座標系の点)

が数値誤差なく厳密に成立する（画素中心規約に `+0.5` を加えない点も含めて
上流と同じ規約を用いる）。この一致が、後続タスク（2.3 の床平面推定・7.1 の
end-to-end 検証）が本モジュールの出力から既知の正解姿勢を復元できることの
土台になる。

**レンダリングの忠実度について**: 本モジュールはプロダクションレンダラーで
はなく、正しい射影幾何を持つ最小限のテストフィクスチャである。反射・遮蔽の
連鎖・アンチエイリアシングは扱わない。1画素につき「床・各マーカー箱・各
外れ値平面」との交差候補のうち最も近いものを採用する単純なレイキャストで
十分である（tasks.md タスク 1.6: 「レンダリング忠実度を過剰に作り込まない」）。

**決定性**: 乱数は呼び出しごとに `numpy.random.default_rng(rng_seed)` で
新規生成し、グローバルな `numpy.random` の状態には一切触れない
（`np.random.seed()` や裸の `np.random.rand()` を使わない）。同じ引数での
2回の呼び出しは常にビット単位で同一の Depth 画像を返す（tasks.md タスク 1.6
「観測可能な完了状態」）。

**縮退条件の再現**（要件 9.5）: 本モジュール自体は縮退の判定を行わない
（判定は上位コンポーネントの責務）。以下のように、既存の引数の組み合わせで
各縮退条件を持つ合成入力を構成できる:

- **内点不足**: `invalid_fraction` を大きくする、`max_range_mm` を小さくして
  遠方の床を無効にする、または床の一部をマーカー箱で覆う
- **浅い入射角**: `camera_pose_looking_at_floor` の `pitch_deg` を小さくする
  （`pitch_deg` は光軸と床面のなす入射角そのものになるよう構成してある。
  下記 `camera_pose_looking_at_floor` の docstring 参照）
- **マーカー未配置**: マーカーの `center_xy_world_mm` をカメラの視野外
  （画素範囲に一切投影されない位置）に置く
- **短い基線長**: 原点マーカーと方向マーカーの `MarkerBox` を近接させる
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from world_frame_calibration.types import DepthImage, Intrinsics, StreamSignature

__all__ = [
    "CameraPose",
    "MarkerBox",
    "OutlierPlane",
    "SyntheticFrame",
    "camera_pose_looking_at_floor",
    "generate_synthetic_depth",
]

_MIN_POSITIVE_T = 1e-6
"""レイのカメラ後方（自分自身の位置など）を交点として誤採用しないための下限。"""


@dataclass(frozen=True, slots=True)
class CameraPose:
    """カメラ座標系 → World 座標系の既知の剛体変換（合成生成の「正解」）。

    `world_point_mm = rotation_cam_to_world @ camera_point_mm + position_world_mm`

    Attributes:
        position_world_mm: カメラ原点（レンズ中心）の World 座標。
        rotation_cam_to_world: 3x3 回転行列（行優先のタプルのタプル）。各
            **列**がカメラ座標系の軸（X=右, Y=下, Z=光軸/前方）を World
            座標系で表した単位ベクトルである。
    """

    position_world_mm: tuple[float, float, float]
    rotation_cam_to_world: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]

    def rotation_matrix(self) -> np.ndarray:
        """回転行列を `(3, 3)` の `float64` 配列として返す。"""
        return np.array(self.rotation_cam_to_world, dtype=np.float64)

    def position_vector(self) -> np.ndarray:
        """カメラ位置を `(3,)` の `float64` 配列として返す。"""
        return np.array(self.position_world_mm, dtype=np.float64)


@dataclass(frozen=True, slots=True)
class MarkerBox:
    """直方体マーカー（床上のマーカー、または「浮遊物」外れ値として使う）。

    軸並行の直方体として World 座標系に置く。周囲の床面から区別できる
    高さを持つ物体であればよいという要件（A-2 / 要件 2.6）に対応し、色・柄・
    既知寸法は要求しない。

    Attributes:
        label: 識別ラベル（人間が読むためのものであり、幾何には影響しない）。
        center_xy_world_mm: 箱の底面（`base_z_world_mm` の高さの面）の
            中心の World (x, y)。
        size_xy_mm: 箱の底面サイズ（x 方向の幅, y 方向の奥行き）。
        height_mm: 箱の高さ（`base_z_world_mm` から鉛直上向きに測る）。
        base_z_world_mm: 箱の底面の World z。既定の `0.0` は「床の上に置か
            れたマーカー」を表す。正の値を与えると床から浮いた「浮遊物」
            外れ値（tasks.md タスク 1.6）として使える。
    """

    label: str
    center_xy_world_mm: tuple[float, float]
    size_xy_mm: tuple[float, float]
    height_mm: float
    base_z_world_mm: float = 0.0

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """`(box_min, box_max)` を `(3,)` 配列の組として返す。"""
        cx, cy = self.center_xy_world_mm
        half_w, half_d = self.size_xy_mm[0] / 2.0, self.size_xy_mm[1] / 2.0
        box_min = np.array(
            [cx - half_w, cy - half_d, self.base_z_world_mm], dtype=np.float64
        )
        box_max = np.array(
            [cx + half_w, cy + half_d, self.base_z_world_mm + self.height_mm],
            dtype=np.float64,
        )
        return box_min, box_max


@dataclass(frozen=True, slots=True)
class OutlierPlane:
    """床平面推定の外れ値として混入させる、床以外の任意の平面（例: 壁）。

    Attributes:
        point_world_mm: 平面上の1点（World 座標）。
        normal_world: 平面の法線（単位長でなくてもよい。内部で正規化する）。
    """

    point_world_mm: tuple[float, float, float]
    normal_world: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class SyntheticFrame:
    """`generate_synthetic_depth` の返り値。

    Depth 画像だけでなく、生成に用いた**正解の変換**（カメラ → World）を
    同時に返す（tasks.md タスク 1.6:「正解の変換を同時に返し、以降のテスト
    が復元結果と直接比較できるようにする」）。`camera_to_world` は呼び出し側
    が渡した `CameraPose` をそのまま保持するだけであり、生成処理はこれを
    書き換えない。

    Attributes:
        depth: 生成された合成 Depth 画像。
        camera_to_world: 生成に用いた既知の正解姿勢（カメラ → World）。
    """

    depth: DepthImage
    camera_to_world: CameraPose


def camera_pose_looking_at_floor(
    position_world_mm: tuple[float, float, float],
    yaw_deg: float,
    pitch_deg: float,
    roll_deg: float = 0.0,
) -> CameraPose:
    """位置と yaw/pitch/roll から、床を見込むカメラ姿勢を構成する。

    軸の約束（本モジュール専用。カメラ座標系は X=右, Y=下, Z=光軸/前方の
    右手系とする。`right x down == forward`）:

    - `yaw_deg`: World +Z 軸まわりの水平方向（`forward` の水平成分の向き）。
    - `pitch_deg`: 水平からの下向き傾斜角。`0` で水平を見込み、`90` で
      真下（床）を見込む。**この値は光軸（forward）と床平面（法線 = World
      +Z）のなす入射角そのものになるように構成してある**
      （`incidence_angle_deg == pitch_deg`。要件 5.4 の「入射角が浅すぎる」
      縮退条件を、この引数だけで直接再現できる）。
    - `roll_deg`: 光軸まわりのカメラの傾き（画像の回転）。既定 `0`。

    Returns:
        床を見込む姿勢の `CameraPose`。
    """
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)

    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    forward = np.array([cp * cy, cp * sy, -sp], dtype=np.float64)
    right = np.array([sy, -cy, 0.0], dtype=np.float64)
    down = np.cross(forward, right)

    if roll_deg != 0.0:
        cr, sr = math.cos(roll), math.sin(roll)
        right, down = right * cr + down * sr, down * cr - right * sr

    rotation = np.stack([right, down, forward], axis=1)  # 列が各カメラ軸
    return CameraPose(
        position_world_mm=tuple(float(c) for c in position_world_mm),  # type: ignore[arg-type]
        rotation_cam_to_world=tuple(tuple(float(v) for v in row) for row in rotation),  # type: ignore[arg-type]
    )


def _ray_plane_intersections(
    origin: np.ndarray,
    directions: np.ndarray,
    point_on_plane: np.ndarray,
    normal: np.ndarray,
) -> np.ndarray:
    """レイ `origin + t * directions[i]` と平面の交点パラメータ `t` を返す。

    交差しない（レイが平面と平行、または交点が `origin` の後方）画素は
    `np.inf` とする。`directions` は `(N, 3)`。返り値は `(N,)`。
    """
    unit_normal = normal / np.linalg.norm(normal)
    denom = directions @ unit_normal  # (N,)
    numer = float(np.dot(point_on_plane - origin, unit_normal))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = numer / denom
    valid = np.abs(denom) > 1e-12
    t = np.where(valid, t, np.inf)
    t = np.where(t > _MIN_POSITIVE_T, t, np.inf)
    return t


def _ray_box_intersections(
    origin: np.ndarray,
    directions: np.ndarray,
    box_min: np.ndarray,
    box_max: np.ndarray,
) -> np.ndarray:
    """レイ・軸並行直方体（AABB）交差の最近傍入射パラメータ `t` を返す。

    スラブ法（各軸ごとに交差区間を求め、共通部分を取る）。交差しない画素は
    `np.inf`。`directions` は `(N, 3)`。返り値は `(N,)`。
    """
    n = directions.shape[0]
    t_enter = np.full(n, -np.inf, dtype=np.float64)
    t_exit = np.full(n, np.inf, dtype=np.float64)

    for axis in range(3):
        d = directions[:, axis]
        o = float(origin[axis])
        lo = float(box_min[axis])
        hi = float(box_max[axis])

        parallel = np.abs(d) < 1e-12
        with np.errstate(divide="ignore", invalid="ignore"):
            t1 = (lo - o) / d
            t2 = (hi - o) / d
        tmin_axis = np.minimum(t1, t2)
        tmax_axis = np.maximum(t1, t2)

        inside_slab = lo <= o <= hi
        tmin_axis = np.where(parallel, -np.inf if inside_slab else np.inf, tmin_axis)
        tmax_axis = np.where(parallel, np.inf if inside_slab else -np.inf, tmax_axis)

        t_enter = np.maximum(t_enter, tmin_axis)
        t_exit = np.minimum(t_exit, tmax_axis)

    hit = (t_enter <= t_exit) & (t_enter > _MIN_POSITIVE_T)
    return np.where(hit, t_enter, np.inf)


def generate_synthetic_depth(
    camera_pose: CameraPose,
    intrinsics: Intrinsics,
    *,
    markers: Sequence[MarkerBox] = (),
    outlier_planes: Sequence[OutlierPlane] = (),
    noise_std_mm: float = 0.0,
    invalid_fraction: float = 0.0,
    max_range_mm: float = 20_000.0,
    stream_signature: StreamSignature | None = None,
    rng_seed: int = 0,
) -> SyntheticFrame:
    """既知のカメラ姿勢・内部パラメータ・マーカー配置から合成 Depth を生成する。

    各画素についてカメラ座標系のレイを張り、World 座標系（`camera_pose`
    経由）で床平面（z = 0）・`markers`・`outlier_planes` のうち最も近い
    交点を探し、そのカメラ座標系 Z 成分（= 実際の Depth センサが返す
    Depth 値と同じ量）を画素値とする。交点が無い、または `max_range_mm`
    を超える画素は無効（`NaN`）とする。

    Args:
        camera_pose: 既知のカメラ → World 変換。
        intrinsics: 既知の内部パラメータ（この解像度で画像を生成する）。
        markers: 直方体マーカー（`base_z_world_mm > 0` で「浮遊物」外れ値
            にもなる）。
        outlier_planes: 床以外の平面（外れ値）。
        noise_std_mm: 有効画素へ加える独立ガウスノイズの標準偏差（mm）。
            `0.0` ならノイズを加えない。
        invalid_fraction: 幾何的に有効な画素のうち、無効画素（`NaN`）へ
            置き換える割合（`0.0`〜`1.0`）。
        max_range_mm: これを超える Depth 値は無効として扱う（センサーの
            最大測距距離を模す。「浅い入射角」縮退条件は、床までの距離が
            急激に伸びてこの上限を超えることで再現できる）。
        stream_signature: 出力 `DepthImage.signature` に用いる値。省略時は
            `intrinsics` の解像度から妥当な既定値を構成する。
        rng_seed: ノイズ・無効画素選択に用いる乱数シード。**グローバルな
            `numpy.random` の状態には一切触れない**
            （`numpy.random.default_rng(rng_seed)` を毎回新規に構築する）。
            同じ引数であれば常にビット単位で同一の結果を返す。

    Returns:
        生成した `DepthImage` と、生成に用いた正解の `CameraPose` を持つ
        `SyntheticFrame`。
    """
    height_px, width_px = intrinsics.height_px, intrinsics.width_px

    v_idx, u_idx = np.meshgrid(
        np.arange(height_px, dtype=np.float64),
        np.arange(width_px, dtype=np.float64),
        indexing="ij",
    )
    # 画素中心規約: 整数座標がその画素の中心を指す。+0.5 を加えない
    # （`sensing_foundation.geometry.deproject_pixel` と同じ規約）。
    dx = (u_idx - intrinsics.ppx_px) / intrinsics.fx_px
    dy = (v_idx - intrinsics.ppy_px) / intrinsics.fy_px
    dz = np.ones_like(dx)
    d_cam = np.stack([dx, dy, dz], axis=-1).reshape(-1, 3)  # (N, 3)

    rotation = camera_pose.rotation_matrix()
    origin = camera_pose.position_vector()
    d_world = d_cam @ rotation.T  # (N, 3): 各行が世界座標系のレイ方向

    candidates = [
        _ray_plane_intersections(
            origin, d_world, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
        )
    ]
    for marker in markers:
        box_min, box_max = marker.bounds()
        candidates.append(_ray_box_intersections(origin, d_world, box_min, box_max))
    for outlier in outlier_planes:
        candidates.append(
            _ray_plane_intersections(
                origin,
                d_world,
                np.array(outlier.point_world_mm, dtype=np.float64),
                np.array(outlier.normal_world, dtype=np.float64),
            )
        )

    t_nearest = np.min(np.stack(candidates, axis=0), axis=0)  # (N,)
    hit = np.isfinite(t_nearest) & (t_nearest <= max_range_mm)
    depth_flat = np.where(hit, t_nearest, np.nan)

    rng = np.random.default_rng(rng_seed)

    if noise_std_mm > 0.0:
        noise = rng.normal(0.0, noise_std_mm, size=depth_flat.shape)
        depth_flat = np.where(np.isnan(depth_flat), depth_flat, depth_flat + noise)

    if invalid_fraction > 0.0:
        drop = rng.random(size=depth_flat.shape) < invalid_fraction
        depth_flat = np.where(drop, np.nan, depth_flat)

    depth_mm = depth_flat.reshape(height_px, width_px).astype(np.float64)
    valid_count = (~np.isnan(depth_mm)).astype(np.int32)

    if stream_signature is None:
        stream_signature = StreamSignature(
            width_px=width_px,
            height_px=height_px,
            fps=30,
            depth_scale_mm=1.0,
            color_enabled=False,
        )

    depth_image = DepthImage(
        depth_mm=depth_mm,
        valid_count=valid_count,
        frames_used=1,
        intrinsics=intrinsics,
        signature=stream_signature,
        source_kind="simulated",
    )
    return SyntheticFrame(depth=depth_image, camera_to_world=camera_pose)
