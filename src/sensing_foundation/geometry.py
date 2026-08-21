"""ピンホール逆投影の基本演算（design.md「L0-L2: 基盤」Geometry）。

本モジュールは**状態を持たない純関数のみ**を提供する。クラス・グローバル
状態は持たない。扱うのは numpy 配列ではなくスカラである。

**`world-frame-calibration` と `flying-object-tracking` はこの3関数
（`is_valid_depth` / `depth_raw_to_mm` / `deproject_pixel`）を呼び、
同等の式を自前で再実装してはならない。** 両 Spec が同じ演算に乗っている
ことが、片方（`world-frame-calibration`）で校正した床平面をもう片方
（`flying-object-tracking`）が復元した3次元点へそのまま適用してよい
根拠になる。この3関数のいずれかを変更することは Revalidation Trigger に
該当し、両 Spec の再検証が必要になる（design.md「Revalidation Triggers」）。

**画素中心の規約**: 整数座標 `(u, v)` は**その画素の中心**を指す。
したがって `deproject_pixel()` は **`+0.5` の補正を加えない**
（RealSense SDK の `rs2_deproject_pixel_to_point` と同じ規約）。重心などの
小数座標をそのまま渡せるよう、`u_px` / `v_px` は `float` で受ける。

**mm 換算は `depth_raw_to_mm()` でのみ行う**。`raw * depth_scale_mm` を
適用するのはこの関数だけであり、`deproject_pixel()` は**すでに mm へ
換算済みの `z_mm`** を受け取る。呼び出し側が `depth_scale_mm` を自分で
掛けたうえで `deproject_pixel()` に渡すと、換算が二重に適用され、値が
`depth_scale_mm` 倍だけ誤った3次元点になる。この二重適用は実行時には
検出できない（渡された `z_mm` が単位換算済みかどうかを値だけから判定する
手段がない）ため、引数名に単位（`_mm`）を含めることでのみ防止している。
呼び出し側は必ず `depth_raw_to_mm()` の戻り値をそのまま `z_mm` として渡すこと。

**無効 Depth は逆投影の前に弾く**: RealSense D435 は「測距不能」（穴・
反射・測距範囲外など）を生カウント `0`（`INVALID_DEPTH_RAW`）で表す。
これを `0 mm` として `deproject_pixel()` に渡すと、原点 `(0, 0, 0)` という
**もっともらしい嘘の点**が返る（例外にもならず、後段の処理からは正当な
測距点と区別がつかない）。`is_valid_depth()` はこの事故を防ぐために
存在する。呼び出し側は必ず `is_valid_depth(raw)` が `True` の画素だけを
`deproject_pixel()` に渡すこと。`deproject_pixel()` 自身は `z_mm` が
`0`（無効値由来）かどうかを検査しない（無効判定は `is_valid_depth()` に
一本化する）。

**歪み補正は適用しない**: `CameraIntrinsics.coeffs`（Brown-Conrady 歪み
係数）はこのモジュールでは使用しない。D435 の Depth ストリームは
これらの係数が全て `0` で提供されるため、現時点では歪み補正の有無が
恒等変換となり結果に差が出ない。将来、歪み係数が非ゼロな Color ストリーム
側の内部パラメータを使う必要が生じた場合に限り、歪み補正はこのモジュールへ
**追加**する（別モジュールを新設しない）。この追加は Revalidation Trigger
に該当する（design.md「Revalidation Triggers」）。

**スコープ外（意図的に含めない）**: World frame への座標変換・床平面の
推定はここに置かない（要件 3.7）。本モジュールが返すのは**カメラ座標系**
までである。それ以降の変換は `world-frame-calibration` Spec の責務である。

Dependency Direction（design.md）: 本モジュールは `errors` / `types` のみを
import する。`source` / `recording` / アダプタは import しない。
"""

from __future__ import annotations

from sensing_foundation.errors import SensingConfigError
from sensing_foundation.types import CameraIntrinsics

INVALID_DEPTH_RAW: int = 0
"""D435 が「測距不能」（穴）を表す生カウント値。`0 mm` として扱ってはならない。"""


def is_valid_depth(raw: int) -> bool:
    """raw が測距済みの値か。

    `0`（`INVALID_DEPTH_RAW`）は無効（穴・反射・測距範囲外など）であり、
    `0 mm` として扱ってはならない。呼び出し側は `deproject_pixel()` に
    渡す前に必ずこの述語で無効画素を弾くこと（弾かずに逆投影すると、
    原点 `(0, 0, 0)` というもっともらしい嘘の点が返る）。
    """
    return raw != INVALID_DEPTH_RAW


def depth_raw_to_mm(raw: int, depth_scale_mm: float) -> float:
    """生カウント → mm。`raw * depth_scale_mm` を適用する唯一の場所。

    呼び出し側はこの関数以外の場所で `depth_scale_mm` を乗じてはならない
    （`deproject_pixel()` に渡す `z_mm` はこの関数の戻り値をそのまま使うこと。
    二重に適用すると値が `depth_scale_mm` 倍だけ誤った3次元点になる）。

    この関数自体は `raw` の有効性を検査しない（`raw=0` もそのまま
    `0.0 mm` に変換する）。無効判定は `is_valid_depth()` の責務である。
    """
    return raw * depth_scale_mm


def deproject_pixel(
    intrinsics: CameraIntrinsics, u_px: float, v_px: float, z_mm: float
) -> tuple[float, float, float]:
    """画素 (u, v) と奥行き z_mm から**カメラ座標系**の (x_mm, y_mm, z_mm) を返す。

    画素中心規約（整数座標はその画素の中心を指す）に従い、`+0.5` の補正は
    加えない。`z_mm` は `depth_raw_to_mm()` で**すでに mm へ換算済み**の
    値を渡すこと（このモジュール docstring の「mm 換算」節を参照）。また、
    `z_mm` が有効な測距値であることは呼び出し側が `is_valid_depth()` で
    事前に確認しておくこと（このモジュール docstring の「無効 Depth」節を
    参照）。歪み係数（`intrinsics.coeffs`）は適用しない（モジュール docstring
    「歪み補正は適用しない」節を参照）。

    Raises:
        SensingConfigError: `intrinsics.fx_px` または `intrinsics.fy_px` が
            `0` の場合。ゼロ除算・無意味な出力につながる不正な内部パラメータ
            であり、呼び出し方の誤りとして扱う。
    """
    if intrinsics.fx_px == 0 or intrinsics.fy_px == 0:
        raise SensingConfigError(
            "CameraIntrinsics.fx_px / fy_px は 0 であってはならない "
            f"(fx_px={intrinsics.fx_px!r}, fy_px={intrinsics.fy_px!r})"
        )

    x_mm = (u_px - intrinsics.ppx_px) / intrinsics.fx_px * z_mm
    y_mm = (v_px - intrinsics.ppy_px) / intrinsics.fy_px * z_mm
    return x_mm, y_mm, z_mm
