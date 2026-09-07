"""上流 `StreamProfile` から自 Spec の値オブジェクトへの写像
(design.md「PublicApi」/ GeoTypes「Integration」/ tasks.md タスク 9.1 /
要件 6.4, 3.6, 8.2, 8.4, 8.6)。

**なぜ `upstream.py` から切り出したのか。** 下流（`m1-prediction-validation`）
が公開入口から `check_compatibility(result, signature, intrinsics)` を呼ぶ
には、その引数（`StreamSignature` / `Intrinsics`）を**現在の入力元から作る
手段**が要る。要件 6.4 は「読み込んだ結果の……が**現在の入力元のそれ**と
一致しない場合」と定めており、その「現在の入力元のそれ」を公開面から
作れなければ検査そのものが呼べないためである。ところが `upstream.py` は
`sensing_foundation` をモジュールレベルで import するため、そこから公開
入口へ写像を出すと「`sensing_foundation` が未導入の環境でも
`import world_frame_calibration` と全公開シンボルへのアクセスが成功する」
という既存契約（`test_world_frame_calibration_public_api.py`）が壊れる。

**本モジュールは `sensing_foundation` への実行時依存を持たない。** 写像の
本体は属性アクセスとデータクラス構築だけであり、`StreamProfile` は型注釈に
しか現れない。`from __future__ import annotations` により型注釈は実行時に
評価されないため、上流の import は `TYPE_CHECKING` 下だけで足りる。
`upstream.py`（上流の副作用に触れる唯一の接点）は本モジュールから写像を
**再 import して**使う——写像を2箇所に書くと、片方だけを直したときに
「保存された結果」と「現在の入力元」の突き合わせが静かに壊れるためである。

**`types.py` へ置かない理由**: `types.py` は純粋な値オブジェクトの置き場で
あり「生成時に検証・導出をしない」という設計方針を持つ（tasks.md
Implementation Notes タスク1.4）。`to_intrinsics` は
`CalibrationConfigError` を送出する**検証（安全弁）を持つ**ため、そこへは
置かない。

依存の層としては L2（`plan` / `deproject` と同じ）に置く。import してよい
のは `errors`（L0）と `types`（L1）だけである（design.md
「Dependency Direction」）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.types import Intrinsics, StreamSignature

if TYPE_CHECKING:  # pragma: no cover - 型検査時のみ評価される
    # ⚠️ **実行時 import にしないこと。** ここを `TYPE_CHECKING` の外へ出すと、
    # 本モジュールが `sensing_foundation` への実行時依存を獲得し、公開入口
    # （`__init__.py`）経由で「上流が未導入でも import できる」契約が壊れる。
    from sensing_foundation import StreamProfile

__all__ = [
    "to_intrinsics",
    "to_signature",
]


def to_intrinsics(profile: StreamProfile) -> Intrinsics:
    """`StreamProfile.intrinsics`（上流 `CameraIntrinsics`）を自 Spec の
    `Intrinsics` へ写像する。

    フィールド名は `types.Intrinsics` の docstring が定めるとおり
    `sensing_foundation.CameraIntrinsics` と完全に一致しているため、機械的な
    1:1 転記である（design.md GeoTypes「Integration」）。

    **安全弁の所有者について（要件 8.4 / design.md PublicApi「Risks」）**:
    `profile.intrinsics` が `None`（入力元が内部パラメータを提供できない）
    のとき、独自に保持した固定値で埋めずに `CalibrationConfigError` として
    弾く。**この安全弁は、公開入口へ本関数を出した後も較正側が所有したまま
    である。** 下流に写像を書かせると、下流ごとに転記されてこの弁が落ち、
    内部パラメータを持たない入力元に対して既定値で埋めた変換が静かに
    成立してしまう——座標系の数 cm のずれは「予測が悪い」という症状として
    しか現れないため（`docs/requirements.md §6.2`）、それは検出も予測も
    経由せずに下流へ流れ込む最悪の失敗になる。だから写像そのものを公開し、
    下流には**この関数を呼ばせる**。

    Args:
        profile: 現在の入力元から得た上流 `StreamProfile`。

    Returns:
        `StreamProfile.intrinsics` を転記した `Intrinsics`。

    Raises:
        CalibrationConfigError: `profile.intrinsics` が `None` の場合
            （入力元が内部パラメータを提供できない。要件 8.4 により独自の
            固定値で埋めることはできないため、続行できず失敗させる）。
    """
    if profile.intrinsics is None:
        raise CalibrationConfigError(
            "StreamProfile.intrinsics is None: the input source did not "
            "provide camera intrinsics, and world-frame-calibration must not "
            "substitute an independently-held fixed value (requirement 8.4)"
        )
    intr = profile.intrinsics
    return Intrinsics(
        width_px=intr.width_px,
        height_px=intr.height_px,
        fx_px=intr.fx_px,
        fy_px=intr.fy_px,
        ppx_px=intr.ppx_px,
        ppy_px=intr.ppy_px,
        model=intr.model,
        coeffs=intr.coeffs,
    )


def to_signature(profile: StreamProfile) -> StreamSignature:
    """上流 `StreamProfile` を整合性検査対象の `StreamSignature` へ写像する。

    フィールド名は `types.StreamSignature` の docstring が定めるとおり
    `sensing_foundation.StreamProfile` の対応部分と完全に一致する
    （design.md GeoTypes「Integration」）。

    解像度は**ストリーム設定側**（`profile.width_px` / `profile.height_px`）
    から採る。`profile.intrinsics` 側の解像度ではない——整合性検査
    （要件 6.4）が突き合わせるのは取得設定そのものであり、内部パラメータの
    比較は `Intrinsics` 側が別に担うためである。本関数は
    `profile.intrinsics` を一切参照しないため、内部パラメータを提供できない
    入力元（`intrinsics is None`）に対しても成功する。

    Args:
        profile: 現在の入力元から得た上流 `StreamProfile`。

    Returns:
        整合性検査に用いる `StreamSignature`。
    """
    return StreamSignature(
        width_px=profile.width_px,
        height_px=profile.height_px,
        fps=profile.fps,
        depth_scale_mm=profile.depth_scale_mm,
        color_enabled=profile.color_enabled,
    )
