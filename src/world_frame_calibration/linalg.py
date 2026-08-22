"""幾何に必要な最小限の線形代数とロバスト統計（design.md「L0-L2: 基盤・入力」
LinAlg コンポーネント / tasks.md タスク 1.3 / 要件 2.4, 3.3）。

本モジュールが提供するもの:

- `unit`: ベクトルの正規化
- `orthonormal_basis`: Z 軸と X 方向ヒントから右手系の正規直交基底を構成する
  （design.md: 「z を保ち、x_hint の z 直交成分を x とし、y = z × x を返す」）
- `x_hint_degeneracy`: X 方向ヒントの Z 直交成分がほぼ消えているかどうかの
  真偽と大きさを返す。**判定（例外を送出して失敗として扱うかどうか）自体は
  上位の `frame` モジュールが行う**（design.md Integration 注記）。本モジュール
  はその材料（真偽・大きさ）を返すだけであり、`CalibrationFailure` を送出しない
- `is_orthonormal`: 与えられた3x3行列が正規直交かどうかを、呼び出し側が
  与えた許容差で判定する
- `rotation_angle_deg`: 2つの回転行列間の全体角を、相対回転
  `R_a^T @ R_b` のトレースから `arccos((trace(R) - 1) / 2)` で算出する。
  浮動小数の誤差で引数が [-1, 1] をわずかに超えても NaN にならないよう
  クリップする
- `robust_center`: 点群の各軸の中央値と、中央絶対偏差（MAD）から導いた
  散らばりを返す

**本モジュールは `numpy` 以外の何も import しない**（design.md Invariants）。
パッケージ内の他モジュール（`errors` を含む）にも依存しない。座標系が
定まらない条件をどう扱うか（例外を送出するかどうか）は本モジュールの
関心事ではなく、常に上位のモジュールが判断する。
"""

from __future__ import annotations

import numpy as np


def unit(v: "np.ndarray") -> "np.ndarray":
    """ベクトル `v` を単位長へ正規化して返す。

    Preconditions:
        `v` は非零ベクトルであること。
    """
    norm = np.linalg.norm(v)
    return np.asarray(v, dtype=np.float64) / norm


def orthonormal_basis(
    z_axis: "np.ndarray", x_hint: "np.ndarray"
) -> tuple["np.ndarray", "np.ndarray", "np.ndarray"]:
    """z を保ち、x_hint の z 直交成分を x とし、y = z × x を返す（右手系）。

    Preconditions:
        `z_axis` と `x_hint` は非零。`x_hint` の z 直交成分が数値的に
        消えていないこと（消えている場合は `x_hint_degeneracy` で検出できるが、
        本関数自体は例外を送出せず、数値的に不安定な結果を返しうる）。

    Postconditions:
        返す3軸 (x, y, z) は右手系で単位長。
    """
    z = unit(z_axis)
    x_hint_arr = np.asarray(x_hint, dtype=np.float64)
    # x_hint の z 直交成分: x_hint から z 方向の成分を除去する。
    x_orthogonal = x_hint_arr - np.dot(x_hint_arr, z) * z
    x = unit(x_orthogonal)
    y = np.cross(z, x)
    return x, y, z


def x_hint_degeneracy(
    z_axis: "np.ndarray", x_hint: "np.ndarray"
) -> tuple[bool, float]:
    """x_hint の z 直交成分がほぼ消えているかどうかの真偽と大きさを返す。

    大きさは、正規化した z と x_hint から求めた z 直交成分の長さを、
    x_hint 自体の長さで正規化した比率（0 に近いほど z 軸に平行、
    1 に近いほど z 軸と直交している）で表す。

    **判定自体（縮退として失敗として扱うかどうか）はここでは行わない。**
    上位の `frame` モジュールが、この真偽・大きさを材料に
    `CalibrationFailure(ANCHOR_DEGENERATE)` を送出するかどうかを決める
    （design.md LinAlg「Integration」注記）。
    """
    z = unit(z_axis)
    x_hint_arr = np.asarray(x_hint, dtype=np.float64)
    x_hint_norm = np.linalg.norm(x_hint_arr)
    x_orthogonal = x_hint_arr - np.dot(x_hint_arr, z) * z
    orthogonal_norm = np.linalg.norm(x_orthogonal)
    magnitude = float(orthogonal_norm / x_hint_norm)
    is_degenerate = bool(np.isclose(magnitude, 0.0, atol=1e-9))
    return is_degenerate, magnitude


def is_orthonormal(r: "np.ndarray", tol: float) -> bool:
    """`r` が正規直交行列（R^T R = I）であるかを、許容差 `tol` の下で判定する。

    Postconditions:
        許容差は呼び出し側が与える。
    """
    r_arr = np.asarray(r, dtype=np.float64)
    identity = np.eye(r_arr.shape[0])
    residual = r_arr.T @ r_arr - identity
    return bool(np.max(np.abs(residual)) <= tol)


def rotation_angle_deg(r_a: "np.ndarray", r_b: "np.ndarray") -> float:
    """2つの回転行列間の全体角を度で返す（相対回転 R_a^T @ R_b のトレースから算出）。

    `arccos((trace(R) - 1) / 2)` の引数を浮動小数の誤差による
    オーバーシュートから守るため [-1, 1] へクリップする。
    """
    r_a_arr = np.asarray(r_a, dtype=np.float64)
    r_b_arr = np.asarray(r_b, dtype=np.float64)
    relative = r_a_arr.T @ r_b_arr
    trace = np.trace(relative)
    cos_angle = (trace - 1.0) / 2.0
    cos_angle_clipped = np.clip(cos_angle, -1.0, 1.0)
    angle_rad = np.arccos(cos_angle_clipped)
    return float(np.degrees(angle_rad))


def robust_center(points: "np.ndarray") -> tuple["np.ndarray", float]:
    """各軸の中央値と、中央絶対偏差（MAD）から導いた散らばりを返す。

    散らばりは、各点の中央値からの距離（ユークリッドノルム）の中央値
    として算出する。外れ値の影響を受けにくい。
    """
    points_arr = np.asarray(points, dtype=np.float64)
    center = np.median(points_arr, axis=0)
    distances = np.linalg.norm(points_arr - center, axis=1)
    spread = float(np.median(distances))
    return center, spread
