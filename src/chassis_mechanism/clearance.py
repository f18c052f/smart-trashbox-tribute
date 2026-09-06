"""床との隙間の算出と違反の列挙（design.md `#### Clearance` / 要件 4.1, 4.2,
4.3, 4.4, 4.7）。

**部位は常に5件である。⚠️ 要件 4.2 が名指しするのは4部位（ブラケット／ボルト頭と
ナット／駆動ベース下面／配線）だが、本モジュールはそこへ「モータ胴体下面」を加えて
5部位を返す**（design.md `#### Clearance` Responsibilities:「モータ胴体は設計で
動かせないが、⚠️ **最も低い部位であり基準として一覧に出す価値がある**」）。
モータ胴体の高さは付属ブラケットとホイールの寸法で決まってしまい、設計で動かせる
量ではない——それでも一覧に出すのは、他の4部位が**どれだけの余裕の中で**成立して
いるのかが、最も低い部位を見なければ読めないからである。⚠️ **4件へ戻さない。**
`test_chassis_clearance.py` が件数と部位名の並びを固定している。

**⚠️ 例外を送出しない。** 違反は全件を値として返し、それを失敗として扱うのは
呼び出し側（CLI、タスク 4.1）である（design.md `#### Clearance` /「Error Strategy」
の「評価結果は値で返す」、上流 `catch_mechanism.check_envelope` と同じ流儀）。
1件ずつ例外で止めると「ブラケットを直したら次はボルト頭」という往復になり、
どの部位がどれだけ足りないのかを一度に読めない。したがって
`errors.ClearanceError` は**ここでは import も送出もしない**——あれは呼び出し側が
本モジュールの戻り値を見て送出する型である。`test_chassis_clearance.py` が
`ast` で `raise` 文の不在を固定する。

**高さはすべて `ChassisLayout.vertical`（鉛直スタック）から読む**（要件 4.7 /
research.md「最低地上高は何から決まるか」:「⚠️ **すべての高さが実効転がり半径に
連動する。** 公称 30mm に対し荷重下で 29.25mm なら、モータ胴体下面は 10.75mm へ
下がる」）。⚠️ **1部位でも寸法パラメータから直に高さを組み立てない**——実効転がり
半径の実測（要件 10.3）が入ったときに追随しない部位が生まれ、「隙間は足りている」
という誤った判定が残る。スタックの組み立て自体は `layout` の責務であり、本
モジュールはそれを**消費するだけ**である（同じ積み上げを2箇所に書かない）。

**下限は寸法パラメータから読む**（要件 4.3 / `params.ClearanceLimits.
min_ground_clearance_mm`）。⚠️ **数値をコードへ埋め込まない**——埋め込めば、
使用環境（屋内の平坦床）の前提が変わったときに設定ファイルではなく実装を書き換える
ことになる。`test_chassis_clearance.py` が数値リテラルの不在を `ast` で固定する。

各部位の高さの出どころ:

| 部位 | 高さ | 根拠 |
|---|---|---|
| `motor_body` | `motor_body_bottom_height_mm` | 車軸中心 − モータ半径。全部位で最も低い |
| `bracket` | `motor_body_bottom_height_mm` | 付属ブラケットはモータ胴体を抱いて吊る（下記） |
| `fastener` | `fastener_bottom_height_mm` | ベース板下面から突出量だけ下がった締結の下端 |
| `base_underside` | `mount_face_height_mm` | 取付面＝ベース板下面（要件 4.1） |
| `cable` | `mount_face_height_mm - cable_lowest_offset_mm` | 配線はベース下面から下へ垂れる |

⚠️ **ブラケットの最下点はモータ胴体下面と同一平面として扱う。** 付属金属ブラケットは
モータ胴体を抱いてベース板から吊り下げる部品であり（A-7 / `docs/bom.md §B`）、
その最下点は胴体下面の平面にある。⚠️ **`bracket.outline_x_mm` /
`outline_y_mm` を鉛直方向の張り出しとして使わない**——あれは取付面内の外形
（41.3 × 38.8mm）であって鉛直の量ではなく、鉛直に読み替えれば根拠のない幾何の
主張になる。ブラケット固有の鉛直の張り出しが実測された場合（要件 1.6）は、
その値を寸法パラメータへ足したうえで**この行だけ**を差し替える。設計値と実物の差を
拾うのは組立後の実測（要件 4.5、`assembly`）である。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from chassis_mechanism.layout import ChassisLayout
from chassis_mechanism.params import ChassisParams

__all__ = [
    "CLEARANCE_ITEM_NAMES",
    "ClearanceItem",
    "ClearanceViolation",
    "clearance_items",
    "evaluate_clearance",
]


_MOTOR_BODY_NAME: Final[str] = "motor_body"
_BRACKET_NAME: Final[str] = "bracket"
_FASTENER_NAME: Final[str] = "fastener"
_BASE_UNDERSIDE_NAME: Final[str] = "base_underside"
_CABLE_NAME: Final[str] = "cable"

CLEARANCE_ITEM_NAMES: Final[tuple[str, ...]] = (
    _MOTOR_BODY_NAME,
    _BRACKET_NAME,
    _FASTENER_NAME,
    _BASE_UNDERSIDE_NAME,
    _CABLE_NAME,
)
"""返る部位の名と並び（design.md `#### Clearance` の `ClearanceItem` の注記の順）。

⚠️ **並びは値によらず固定である。** 不足量の大きい順などに並べ替えると、寸法を
少し変えるたびに一覧と違反の順序が入れ替わり、差分として読めなくなる。
下流（`assembly` の観測記録、要件 4.5）はこの5件の名と一致することを検査する
（design.md `#### Assembly` Integration）。
"""


@dataclass(frozen=True, slots=True)
class ClearanceItem:
    """1部位の床からの高さ（design.md `#### Clearance` Service Interface）。

    Attributes:
        name: 部位名。`CLEARANCE_ITEM_NAMES` のいずれか。
        height_mm: 床（接地点）を原点とする高さ（mm）。⚠️ **負にもなり得る**
            ——配線が床へ垂れる設定はあり得る入力であり、それを拒むのではなく
            違反として返すのが本モジュールの役割である。
    """

    name: str
    height_mm: float


@dataclass(frozen=True, slots=True)
class ClearanceViolation:
    """下限を下回った部位1件（要件 4.4「該当する部位と不足量を示す」）。

    ⚠️ **これは例外ではなく値である。** 失敗として扱うのは呼び出し側であり、
    本モジュールは全件をまとめて返すだけである。

    Attributes:
        name: 部位名。`CLEARANCE_ITEM_NAMES` のいずれか。
        height_mm: その部位の高さ（mm）。
        minimum_mm: 課された下限（mm）。寸法パラメータ
            `clearance.min_ground_clearance_mm` の値である。
        shortfall_mm: 不足量（mm）。⚠️ **常に正である**——`evaluate_clearance` は
            `height_mm < minimum_mm` の部位についてのみ本型を作るため、
            `minimum_mm - height_mm` は正になる（構築の側で保証しており、
            ⚠️ 検証で拒む形にはしない。本モジュールは例外を送出しない）。
    """

    name: str
    height_mm: float
    minimum_mm: float
    shortfall_mm: float


def clearance_items(
    layout: ChassisLayout, params: ChassisParams
) -> tuple[ClearanceItem, ...]:
    """5部位の床からの高さを鉛直スタックから算出する（要件 4.1, 4.2, 4.7）。

    ⚠️ **常に5件を返す**（design.md `#### Clearance` Invariants）。高さが同じで
    あっても、下限を大きく上回っていても、部位を省かない——省けば「見ていない
    部位」と「余裕のある部位」が一覧の上で区別できなくなる。

    ⚠️ **すべての高さは `layout.vertical` から読む。** 実効転がり半径が実測へ
    置き換われば（要件 10.3）5部位すべてが自動で追随する（要件 4.7）。

    Args:
        layout: `layout.derive_layout` の戻り値（design.md Preconditions）。
        params: 本 Spec の寸法パラメータ。配線のオフセットを読むために要る。

    Returns:
        `CLEARANCE_ITEM_NAMES` の順に並んだ5件。⚠️ 例外は送出しない。
    """
    vertical = layout.vertical
    mount_face_height_mm = vertical.mount_face_height_mm
    return (
        ClearanceItem(
            name=_MOTOR_BODY_NAME, height_mm=vertical.motor_body_bottom_height_mm
        ),
        # ⚠️ ブラケットはモータ胴体を抱いて吊るため、その最下点は胴体下面と
        # 同一平面である（モジュールの docstring を参照）。外形寸法を鉛直方向へ
        # 読み替えない。
        ClearanceItem(
            name=_BRACKET_NAME, height_mm=vertical.motor_body_bottom_height_mm
        ),
        ClearanceItem(
            name=_FASTENER_NAME, height_mm=vertical.fastener_bottom_height_mm
        ),
        ClearanceItem(name=_BASE_UNDERSIDE_NAME, height_mm=mount_face_height_mm),
        # ⚠️ オフセットは**下向きが正**である（`params.ClearanceLimits`）。
        ClearanceItem(
            name=_CABLE_NAME,
            height_mm=mount_face_height_mm - params.clearance.cable_lowest_offset_mm,
        ),
    )


def evaluate_clearance(
    layout: ChassisLayout, params: ChassisParams
) -> tuple[ClearanceViolation, ...]:
    """下限を下回った部位を**全件**値として返す（要件 4.3, 4.4）。

    ⚠️ **例外を送出せず、最初の違反で打ち切らない。** 「ブラケットを直したら次は
    ボルト頭」という1件ずつの往復を避けるため、5部位すべてを見てから返す
    （上流 `catch_mechanism.check_envelope` と同じ流儀）。失敗として扱い
    `errors.ClearanceError` を送出するのは呼び出し側（CLI、タスク 4.1）である。

    ⚠️ **ちょうど下限の部位は違反ではない。** 比較は厳密な `<` であり、戻り値が
    空であることと全部位が下限以上であることは同値である（design.md
    `#### Clearance` Postconditions）。許容差を入れるとこの同値が崩れる。

    Args:
        layout: `layout.derive_layout` の戻り値。
        params: 本 Spec の寸法パラメータ。下限
            `clearance.min_ground_clearance_mm` の正はここだけである（要件 4.3）。

    Returns:
        違反1件につき1つの `ClearanceViolation` を `CLEARANCE_ITEM_NAMES` の順に
        並べたタプル。すべて下限以上なら空タプル。
    """
    minimum_mm = params.clearance.min_ground_clearance_mm
    return tuple(
        ClearanceViolation(
            name=item.name,
            height_mm=item.height_mm,
            minimum_mm=minimum_mm,
            shortfall_mm=minimum_mm - item.height_mm,
        )
        for item in clearance_items(layout, params)
        if item.height_mm < minimum_mm
    )
