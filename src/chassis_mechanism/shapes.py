"""部品の形状構築と指標の抽出（design.md `#### Shapes` / 要件 1.11, 1.12, 2.2,
5.1-5.6, 5.8）。

⚠️ **本タスク（3.1）が構築するのは整備スタンドだけである。** 要件 5.1 は整備
スタンドを他のどの造形物よりも先に設計・造形・検証することを求めており、
design.md `#### Shapes` の部品表の残り（`hub_plate` / `motor_arm_*` /
`adapter_segment_*` / `battery_tray` / `board_tray` / `cable_guide_*`）は
タスク 3.2〜3.5 が本モジュールへ足す。`PART_NAMES` と `build_parts` はその都度
広がる。

## 形状ライブラリを module 直下で import しない（design.md「Allowed Dependencies」）

⚠️ **CAD 非導入の環境で評価できることを求める chassis 要件は存在しない。** この
義務の出所は design.md の「Allowed Dependencies」（`build123d` の import は
`shapes` / `export` に限る）と「Dependency Direction」（`__init__` は `shapes` /
`export` を import せず、公開 API が OCCT を要求しない）、および上流
`catch_mechanism.shapes` の先例である。⚠️ **許されていることと「モジュール読み込み
時に必要にしてよい」ことは別である**。`stand_geometry` は
純粋な算術であり、`cad` extra 非導入の環境でも**全数値と成立条件**を評価できる
——脚が成立するかどうかを知るために CAD を要求しない。import は実際にソリッドを
構築する `build_service_stand_legs` の内側にあり、失敗は `CadUnavailableError`
（`errors.py`、`cli` の終了コード 3）へ写す。⚠️ **形状生成の要求を成功として
黙って読み飛ばさない。**

## 整備スタンドの形（design.md「機構の決定」決定 5 / 要件 5.3-5.6, 5.8）

⚠️ **脚は輪ごとに独立した1点であり、1体の枠にしない**（決定 5）。造形可能寸法を
超えるうえ、輪ごとに載せ降ろしできなければ一人での作業が難しくなる（要件 5.9）。

局所座標は **原点＝ホイール中心の真下の床面、+x＝機体外向き（半径方向）、
+y＝接線方向、+z＝上** である。3脚は同一形状であり、`placement_radius_mm` と
`leg_angles_deg` だけが据え付けの位置を与える。

1脚は次の3つからなる。

- **谷（ソケット）**: ホイールと同軸・半径 `R + 隙間` の樋。⚠️ **ホイールの
  等距離面である**ため、外周とも側面とも隙間はどこでも
  `wheel_rotation_clearance_mm` ちょうどになる（要件 5.5 / タスク 3.1 の
  観測可能な完了状態「ホイール外周と台の隙間が寸法パラメータと一致」）。
  谷は接線方向の両側と半径方向の外側を壁で囲い、⚠️ **機体側と車軸より上は開けて
  ある**——前者はモータ胴体の逃げ、後者は回転方向の目視（要件 5.8）である
- **支持パッド**: 谷より機体側、駆動ベース**端部**にぶら下がる駆動ユニットの
  下面を受ける水平面。上面の高さは `motor_body_bottom_height_mm + lift_height_mm`
  であり、⚠️ **駆動ベース下面（`mount_face_height_mm + lift_height_mm`）へは
  届かない**。脚のどの点もその高さより低い
- **案内リブ**: パッドの両脇に立ち上げ、機体を載せるときの位置決めに使う。
  ⚠️ 駆動ユニットへ触れない位置（`motor_body_diameter_mm / 2 + 隙間` の外側）に置く

### 支持面が駆動ユニットの下面である根拠（要件 5.3）

要件 5.3 は支持面を**駆動ベース端部にぶら下がる駆動ユニット（モータ胴体）の
下面**と定め、他の候補が成立しない理由をそのまま持っている。本モジュールは
その要件文を実装するだけであり、⚠️ **ここで支持面を選び直さない**。

- **ホイールそのものを受けない**——要件 5.4（3輪が床にも台にも触れない）と
  5.5（自由に回転できる隙間）が禁じる。ホイール外周より外側および上方に機体の
  構造は無い（台上でホイール頂点＝ベース板下面＝80.0mm）。⚠️ 荷重の経路の上でも
  そうすべきである——ホイールで受けると機体の重量が**ハブの M4 止めネジ1本**
  （`docs/bom.md` §B が「トルクを受けるのはこれ1本だけ」と記す最も弱い環）を
  経由する。モータ胴体の下面で受ければ、荷重はその経路を通らない
- **駆動ベース端部の外形を当てにしない**——それはタスク 3.2 が設計する輪郭で
  あり、⚠️ スタンドの設計入力を「配置半径と現物採寸値に限る」要件 5.2 と、
  スタンドを他のどの造形物よりも先に出す要件 5.1 の両方に反する
- **ベース板下面で支持しない**——ホイールより内側の空き帯はハブのフランジ厚
  ぶん（出荷値で 4.0mm、ギヤボックス端面 118.4mm ↔ ホイール内側面 122.4mm）
  しかなく、⚠️ そこにはブラケット・締結の頭・配線が並ぶ

⚠️ **ベース板下面には一切触れない**（脚の全体がその高さより低いことを
`test_chassis_invariants.py` が実形状に対して固定する）。

⚠️ **残るリスクを記録として残す（要件 5.5 のリスクであって 5.4 のそれではない）。**
付属ブラケットの下帯がモータ胴体の下面より δ 下がっていれば、機体はそちらへ座り、
胴体下面は δ 高い位置に来る——⚠️ **ホイールは δ 持ち上がるが谷はその場に留まる**
ため、`wheel_rotation_clearance_mm` の隙間は 10 − δ へ縮み、δ = 10mm で消える。
案内リブの上端（出荷値で 35.5mm）がブラケットの下帯と干渉する余地もある。
ブラケットの取付穴と向きの実測（要件 1.6）が済んだ時点で、支持面の高さの出所を
見直すこと。⚠️ **「床から遠ざかる側だから安全」ではない。**

## 設計入力の限定（要件 5.2）

⚠️ **整備スタンドはゴミ箱とトレイ類の確定を待たない。** そのために、構築関数が
受け取るのは `ResolvedParams` ではなく `StandInputs`——配置半径・取付角・
ホイールとモータの現物採寸値・鉛直スタック・スタンド自身の寸法だけを持つ型で
ある。⚠️ **アダプタ・バッテリ・基板・電源・ゴミ箱の値は型の上で到達できない。**
`test_chassis_shapes.py` がフィールド集合と（静的走査による）属性参照の両方を
固定する。

## 決定性（要件 1.12）

同一の寸法パラメータからの再構築は同一の `PartMetrics` を返す。指標は**構築した
ソリッドから抽出した実測値**であり、寸法からの再計算ではない（上流
`catch_mechanism.shapes.measure_part` と同じ規律。⚠️ 抽出で丸めない）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Final

from catch_mechanism import (
    Envelope,
    PartMetrics,
    PrintingConstraints,
    check_envelope,
    check_material,
)

from chassis_mechanism.config import ResolvedParams
from chassis_mechanism.errors import CadUnavailableError, GeometryError
from chassis_mechanism.layout import ChassisLayout

__all__ = [
    "MIN_HAND_ACCESS_MM",
    "PART_NAMES",
    "BuiltPart",
    "StandGeometry",
    "StandInputs",
    "build_parts",
    "build_service_stand_legs",
    "measure_part",
    "part_names",
    "stand_geometry",
    "stand_inputs",
]


PART_NAMES: Final[tuple[str, ...]] = ("service_stand",)
"""本モジュールが構築する部品の**種類**（design.md `#### Shapes` の部品表）。

⚠️ **1件の要素は「部品の種類」であって造形する点数ではない。** 整備スタンドは
1種類の脚からなり、実際に造形するのは `stand.leg_count` 点である（部品名は
`part_names` が本定数から導く）。⚠️ タスク 3.2〜3.5 が駆動ベース・アダプタ・
トレイ・配線ガイドを足すと、この表はその順に伸びる。
"""

MIN_HAND_ACCESS_MM: Final[float] = 85.0
"""隣り合う脚の間に残す最小の開き（mm、要件 5.8）。

台上でエンコーダ配線・コネクタ・電源の操作部へ手が届くための開きであり、成人の
手の幅（手掌幅）の目安として置く。⚠️ **寸法パラメータではない**——スタンド自身の
形が満たすべき下限であり、設定ファイルで緩められる値にすると「手が入らない台」を
設定で作れてしまう。出荷の配置（配置半径 135.2mm、脚幅 92mm、3脚 120 度等配置）で
実際の開きは約 96mm である。
⚠️ **「手が届く」を完全に機械化することはできない。** 本定数が固定するのは
「脚が繋がっていない」「隣の脚との間に手の幅ぶんの開きがある」という**形状側の
必要条件**だけであり、十分条件ではない。実際に届くかは組立後の確認
（要件 5.8 の実地確認。タスク 6.1）が持つ。

⚠️ **下限は `stand_geometry` が拒否する**（`StandGeometry.leg_separation_mm`）。
テストだけで見ていると、脚を近づけるパラメータ変更が黙って造形物になる。
"""

_WALL_THICKNESS_MM: Final[float] = 6.0
"""谷の壁と案内リブの肉厚（mm）。

⚠️ FDM で反力を面で受ける壁として置く。0.4mm ノズルで 15 本相当であり、
薄い壁で受けると層間で割れる（要件 2.5 が言う「薄いリブや小さな当たり面で
受けない」と同じ理由）。
"""

_GUIDE_LIP_HEIGHT_MM: Final[float] = 4.0
"""支持パッドの両脇に立てる案内リブの、パッド上面からの高さ（mm）。

⚠️ **拘束ではなく位置決めである。** 機体を載せるときに駆動ユニットがパッドの
中央へ落ちるようにするためのものであり、反力に対する拘束は谷の形が受け持つ
（要件 5.6 / `joints.CONTACT_BEARING_AREA_FORMULA`）。
"""

_MIN_TROUGH_FLOOR_MM: Final[float] = 3.0
"""谷の底に残す最小の肉厚（mm）。

⚠️ 台上でのホイール最下点と回転の隙間の差がこれを下回る配置は、「作れるが割れる」
形である。黙って薄い底を作らず `GeometryError` で拒否する。
"""

_SUPPORT_PAD_DEPTH_MULTIPLE: Final[float] = 1.0
"""支持パッドの半径方向の奥行き（モータ胴体径の倍数）。

パッドは駆動ユニットの下面を線ではなく**面で**受ける。奥行きを胴体径に取ると、
⚠️ 胴体の直径ぶんの長さで受けることになり、機体が半径方向へ傾いても座りが
変わらない。⚠️ **胴体を掴まない**（要件 3.7 が禁じるのは造形部品でモータ本体を
クランプすることであり、下から受ける平面は掴む形ではない）。
"""


# ---------------------------------------------------------------------------
# 設計入力（要件 5.2: 配置半径と現物採寸値に限る）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StandInputs:
    """整備スタンドの設計入力（要件 5.2）。

    ⚠️ **本型に無い値をスタンドは読まない。** ゴミ箱・トレイ・電源・アダプタの
    確定を待たずにスタンドを出せることが要件 5.1（他のどの造形物よりも先に出す）
    の前提であり、その限定を**型で**担保する。

    Attributes:
        base_radius_mm: ホイール配置半径（`ChassisLayout.base_radius_mm`）。
        wheel_angles_deg: 各輪の取付角（度、機体 +x から反時計回り）。
            ⚠️ 規約は上流の制御ロジックが持ち、ここで定義し直さない（要件 3.6）。
        wheel_diameter_mm: ホイールの公称外径（mm）。
        wheel_width_mm: ホイールの幅（mm）。
        motor_body_diameter_mm: モータ胴体の外径（mm）。
        motor_body_bottom_height_mm: 接地点を原点とするモータ胴体下面の高さ（mm）。
            ⚠️ **支持面の高さの出所である**（駆動ユニットの下面を受けるため）。
        axle_center_height_mm: 接地点を原点とする車軸中心の高さ（mm）。
            ⚠️ **谷の高さの出所である。** 荷重下の実効転がり半径が実測へ
            置き換われば（タスク 2.4）この値は公称半径より下がり、台上の車軸も
            同じだけ下がる——⚠️ **公称半径で代用しない**（谷だけが取り残され、
            ホイール外周と台の隙間が寸法パラメータより狭くなる。要件 1.5, 3.9）。
        mount_face_height_mm: 接地点を原点とする取付面（＝ベース板下面）の高さ
            （mm）。⚠️ **支持のためではなく、そこへ触れないことを言うために持つ**
            （要件 5.3）。
        support_span_mm: 谷の受け面の高さ（mm）。谷の底から測る。
            当たり面は `joints.CONTACT_BEARING_AREA_FORMULA`
            （`wheel.width_mm * stand.support_span_mm`）である。
        lift_height_mm: 台上での機体の**公称の接地点**の高さ（mm、＝持ち上げ
            高さ）。⚠️ **支持パッドが固定するのはこの高さである**（パッド上面
            ＝ `motor_body_bottom_height_mm + lift_height_mm`）。⚠️ ホイールの
            最下点がこの高さに一致するのは `axle_center_height_mm ==
            wheel_diameter_mm / 2` の間だけであり、実測で車軸が下がれば
            ホイール最下点も同じだけ下がる（`StandGeometry.wheel_bottom_height_mm`）。
        wheel_rotation_clearance_mm: ホイールと台の隙間（mm、要件 5.5）。
        leg_count: 脚の数。⚠️ 輪の数と一致する（`ChassisParams` が検証済み）。
    """

    base_radius_mm: float
    wheel_angles_deg: tuple[float, ...]
    wheel_diameter_mm: float
    wheel_width_mm: float
    motor_body_diameter_mm: float
    motor_body_bottom_height_mm: float
    axle_center_height_mm: float
    mount_face_height_mm: float
    support_span_mm: float
    lift_height_mm: float
    wheel_rotation_clearance_mm: float
    leg_count: int


def stand_inputs(params: ResolvedParams, layout: ChassisLayout) -> StandInputs:
    """寸法パラメータと幾何の導出結果から、スタンドの設計入力だけを取り出す。

    ⚠️ **ここが唯一の絞り込み点である。** 以降の導出と構築は `StandInputs` しか
    見ないため、要件 5.2 の限定が構造として保たれる。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        整備スタンドの設計入力。
    """
    chassis = params.chassis
    return StandInputs(
        base_radius_mm=layout.base_radius_mm,
        wheel_angles_deg=layout.wheel_angles_deg,
        wheel_diameter_mm=chassis.wheel.nominal_diameter_mm,
        wheel_width_mm=chassis.wheel.width_mm,
        motor_body_diameter_mm=chassis.motor.body_diameter_mm,
        motor_body_bottom_height_mm=layout.vertical.motor_body_bottom_height_mm,
        axle_center_height_mm=layout.vertical.axle_center_height_mm,
        mount_face_height_mm=layout.vertical.mount_face_height_mm,
        support_span_mm=chassis.stand.support_span_mm,
        lift_height_mm=chassis.stand.lift_height_mm,
        wheel_rotation_clearance_mm=chassis.stand.wheel_rotation_clearance_mm,
        leg_count=chassis.stand.leg_count,
    )


# ---------------------------------------------------------------------------
# 幾何の導出（⚠️ 形状ライブラリを要さない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StandGeometry:
    """整備スタンド1脚の幾何（design.md 追跡表の `StandGeometry`）。

    ⚠️ **形状オブジェクトを持たない。** 本型は数値だけで構成され、形状ライブラリ
    の無い環境でも生成・比較できる（design.md「Allowed Dependencies」/
    「Dependency Direction」）。値はすべて脚の局所座標
    （原点＝ホイール中心の真下の床面、+x＝機体外向き、+y＝接線方向）である。

    本型は `stand_geometry` の**出力**であり、成立条件の検査は構築の前に
    `stand_geometry` が済ませている（上流 `RimGeometry` と同じ扱い）。

    Attributes:
        leg_count: 脚の数。
        placement_radius_mm: 脚の局所原点が置かれる半径（＝配置半径）。
        leg_angles_deg: 各脚の据え付け角（度）。輪の取付角と一致する。
        socket_radius_mm: 谷の半径（mm）。⚠️ `ホイール半径 + 隙間`。
        socket_half_width_mm: 谷の半幅（mm）。⚠️ `ホイール幅/2 + 隙間`。
        trough_floor_height_mm: 谷の底の高さ（mm）。
            `wheel_bottom_height_mm - 隙間`。
        socket_top_height_mm: 谷の受け面の上端の高さ（mm）。
            `trough_floor + support_span`。⚠️ 車軸中心を超えない（要件 5.8）。
        wheel_bottom_height_mm: 台上でのホイール最下点の高さ（mm）。
            `wheel_center_height_mm - 公称半径`。⚠️ **持ち上げ高さと一致するとは
            限らない**（車軸が実測で下がれば同じだけ下がる）。
        wheel_center_height_mm: 台上での車軸中心の高さ（mm）。
            `lift + axle_center_height`。⚠️ **公称半径から作らない。**
        support_pad_height_mm: 支持面の高さ（mm）。
            `motor_body_bottom_height + lift`。⚠️ 駆動ベース下面より低い。
        support_pad_inner_x_mm: 支持パッドの機体側の端（局所 x、負）。
        support_pad_outer_x_mm: 支持パッドのホイール側の端（局所 x、負）。
        support_pad_half_width_mm: 支持パッドの半幅（mm、接線方向）。
        guide_lip_top_height_mm: 案内リブの上端の高さ（mm）。
        outer_x_mm: 脚の外周側の端（局所 x）。
        half_width_mm: 脚の半幅（mm、接線方向）。
        top_height_mm: 脚の最高点の高さ（mm）。
        leg_separation_mm: 据え付けたとき隣り合う脚の間に残る最小の開き（mm）。
            ⚠️ **配置半径・取付角・脚の外形だけから決まる**（形状を要さない）。
            `MIN_HAND_ACCESS_MM` を下回る配置は `stand_geometry` が拒否する
            （要件 5.8）。
        retention_bearing_area_mm2: 拘束の当たり面（mm^2）。
            ⚠️ `joints.CONTACT_BEARING_AREA_FORMULA` と同じ量であり、
            **形の側がこれを実現している**ことを不変条件が確かめる（要件 5.6）。
        envelope: 1脚の軸並行外接箱。造形可能寸法の検査へ渡す量である。
    """

    leg_count: int
    placement_radius_mm: float
    leg_angles_deg: tuple[float, ...]
    socket_radius_mm: float
    socket_half_width_mm: float
    trough_floor_height_mm: float
    socket_top_height_mm: float
    wheel_bottom_height_mm: float
    wheel_center_height_mm: float
    support_pad_height_mm: float
    support_pad_inner_x_mm: float
    support_pad_outer_x_mm: float
    support_pad_half_width_mm: float
    guide_lip_top_height_mm: float
    outer_x_mm: float
    half_width_mm: float
    top_height_mm: float
    leg_separation_mm: float
    retention_bearing_area_mm2: float
    envelope: Envelope


def _leg_separation_mm(
    *,
    placement_radius_mm: float,
    leg_angles_deg: tuple[float, ...],
    boxes: tuple[tuple[float, float, float], ...],
) -> float:
    """据え付けた脚どうしの間に残る最小の開きを算術で求める（要件 5.8）。

    ⚠️ **形状ライブラリを要さない。** 脚の水平投影を、局所 x の範囲と接線方向の
    半幅で表した箱の**和**で覆う（`boxes` の各要素は `(x_min, x_max, 半幅)`）。
    覆いであるため、ここで得る開きは**実形状の開きの下限**である——不変条件の側
    （`test_chassis_invariants.py`）が実形状の距離を測り、この値以上であることを
    確かめる。

    2脚は隣り合う取付角の**二等分線**について互いの鏡像である（脚は接線方向に
    対称であり、据え付けは回転だけである）。⚠️ 集合 A が線 L の片側にあるとき、
    A と L 鏡像 A の距離はちょうど `2 × dist(A, L)` である——一方の点と他方の点は
    L の反対側にあり、L に垂直な成分だけで `dist(a, L) + dist(a', L) ≥ 2·dist(A, L)`
    になるためである（凸性を要さない）。

    Args:
        placement_radius_mm: 脚の局所原点が置かれる半径。
        leg_angles_deg: 各脚の据え付け角（度）。
        boxes: 脚を覆う箱の列。各要素は `(局所 x の下端, 局所 x の上端, 半幅)`。

    Returns:
        隣り合う脚の間に残る最小の開き（mm）。⚠️ 脚が二等分線を跨ぐ配置では
        0 以下になる。
    """
    separations: list[float] = []
    for index, first_deg in enumerate(leg_angles_deg):
        for second_deg in leg_angles_deg[index + 1 :]:
            delta_deg = abs(first_deg - second_deg) % 360.0
            delta_deg = min(delta_deg, 360.0 - delta_deg)
            half_angle_rad = math.radians(delta_deg / 2.0)
            sin_half = math.sin(half_angle_rad)
            cos_half = math.cos(half_angle_rad)
            distances = [
                radius_mm * sin_half + tangential_mm * cos_half
                for x_min_mm, x_max_mm, half_mm in boxes
                for radius_mm in (
                    placement_radius_mm + x_min_mm,
                    placement_radius_mm + x_max_mm,
                )
                for tangential_mm in (-half_mm, half_mm)
            ]
            separations.append(2.0 * min(distances))
    return min(separations) if separations else math.inf


def stand_geometry(inputs: StandInputs) -> StandGeometry:
    """設計入力から整備スタンド1脚の幾何を導く（要件 5.3-5.6, 5.8）。

    ⚠️ **形状を構築しない。** 本関数は算術のみで完結し、形状ライブラリの無い環境
    でも評価できる（design.md「Allowed Dependencies」/「Dependency Direction」）。

    ## ⚠️ 谷の高さは鉛直スタックから来る（要件 1.5, 3.9）

    台上での車軸中心は `lift_height_mm + axle_center_height_mm` である。
    ⚠️ **`lift + 公称半径` と書かない**——荷重下の実効転がり半径が実測へ置き換われば
    （タスク 2.4。`layout._effective_rolling_radius_mm` が唯一の差し込み点）車軸も
    モータ胴体下面も同じだけ下がり、機体は台上でも低く座る。公称半径で書くと谷だけ
    が取り残され、ホイール外周と台の隙間が寸法パラメータより狭くなる（タスク 3.1 の
    観測可能な完了状態が出荷値でだけ成り立つ状態になる）。

    ⚠️ **谷の半径は公称半径から作る。** 台上のホイールは無荷重であり、縮むのは
    接地している側だけである——追随するのは車軸の**高さ**であって樋の**半径**では
    ない。

    Args:
        inputs: 整備スタンドの設計入力。

    Returns:
        1脚の幾何。

    Raises:
        GeometryError: 隙間がホイール最下点の高さ以上で谷の底が床へ抜ける場合、
            受け面が車軸中心を越えて回転を隠す場合（要件 5.8）、
            谷の底に肉が残らない場合、案内リブが車軸中心を越える場合、
            または据え付けた脚の間に手の幅が残らない場合（要件 5.8）。
            ⚠️ メッセージには**項目名と値**を載せる。
    """
    wheel_radius_mm = inputs.wheel_diameter_mm / 2.0
    clearance_mm = inputs.wheel_rotation_clearance_mm

    # ⚠️ 台上での車軸の高さは鉛直スタックが決める（公称半径ではない）。
    wheel_center_height_mm = inputs.lift_height_mm + inputs.axle_center_height_mm
    wheel_bottom_height_mm = wheel_center_height_mm - wheel_radius_mm

    # ⚠️ 谷の底はホイール最下点より隙間ぶん低い。隙間がその高さ以上であれば
    # 底は床面より下になり、脚として成立しない。
    if clearance_mm >= wheel_bottom_height_mm:
        raise GeometryError(
            f"wheel_rotation_clearance_mm={clearance_mm!r} は台上でのホイール"
            f"最下点 {wheel_bottom_height_mm!r}mm より小さくなければならない"
            f"（持ち上げ高さ lift_height_mm={inputs.lift_height_mm!r} ＋ "
            f"axle_center_height_mm={inputs.axle_center_height_mm!r} − ホイール半径 "
            f"{wheel_radius_mm!r}mm）——谷の底はホイール最下点より隙間ぶん低い"
            "位置にあり、床面より下へは掘れない。"
        )

    socket_radius_mm = wheel_radius_mm + clearance_mm
    socket_half_width_mm = inputs.wheel_width_mm / 2.0 + clearance_mm
    trough_floor_height_mm = wheel_bottom_height_mm - clearance_mm
    socket_top_height_mm = trough_floor_height_mm + inputs.support_span_mm

    # ⚠️ 受け面が車軸中心を越えると、谷がホイールの上半分を覆って回転方向が
    # 見えなくなる（要件 5.8）。載せ降ろしも真上へ抜けなくなる（要件 5.9）。
    if socket_top_height_mm > wheel_center_height_mm:
        raise GeometryError(
            f"support_span_mm={inputs.support_span_mm!r} は "
            f"{socket_radius_mm!r}mm（ホイール半径 ＋ 隙間）以下でなければならない"
            f"——受け面の上端 {socket_top_height_mm!r}mm が車軸中心 "
            f"{wheel_center_height_mm!r}mm を越え、谷がホイールの上半分を覆う"
            "（要件 5.8: 台上でホイールの回転方向を目視できること）。"
        )

    if trough_floor_height_mm < _MIN_TROUGH_FLOOR_MM:
        raise GeometryError(
            f"谷の底の肉厚が {trough_floor_height_mm!r}mm しかなく、下限 "
            f"{_MIN_TROUGH_FLOOR_MM!r}mm を下回る"
            f"（台上でのホイール最下点 {wheel_bottom_height_mm!r}mm − "
            f"wheel_rotation_clearance_mm={clearance_mm!r}。最下点は "
            f"lift_height_mm={inputs.lift_height_mm!r} ＋ "
            f"axle_center_height_mm={inputs.axle_center_height_mm!r} − ホイール半径）。"
            "⚠️ 持ち上げ高さを増すか隙間を詰めること（薄い底は載せた瞬間に割れる）。"
        )

    support_pad_height_mm = inputs.motor_body_bottom_height_mm + inputs.lift_height_mm
    support_pad_outer_x_mm = -socket_half_width_mm
    support_pad_inner_x_mm = support_pad_outer_x_mm - (
        _SUPPORT_PAD_DEPTH_MULTIPLE * inputs.motor_body_diameter_mm
    )
    support_pad_half_width_mm = inputs.motor_body_diameter_mm / 2.0 + clearance_mm
    guide_lip_top_height_mm = support_pad_height_mm + _GUIDE_LIP_HEIGHT_MM

    # ⚠️ 支持面は駆動ベース下面へ届かない（要件 5.3）。届く高さの入力は、
    # 「ベース下面で支持する」設計そのものであるため拒否する。
    on_stand_underside_mm = inputs.mount_face_height_mm + inputs.lift_height_mm
    if guide_lip_top_height_mm >= on_stand_underside_mm:
        raise GeometryError(
            f"脚の最高点 {guide_lip_top_height_mm!r}mm が台上での駆動ベース下面 "
            f"{on_stand_underside_mm!r}mm へ届く"
            f"（mount_face_height_mm={inputs.mount_face_height_mm!r}、"
            f"motor_body_bottom_height_mm={inputs.motor_body_bottom_height_mm!r}）。"
            "⚠️ ベース下面にはブラケット・締結の頭・配線が並ぶため、そこでは"
            "支持しない（要件 5.3）。"
        )
    if guide_lip_top_height_mm > wheel_center_height_mm:
        raise GeometryError(
            f"案内リブの上端 {guide_lip_top_height_mm!r}mm が車軸中心 "
            f"{wheel_center_height_mm!r}mm を越える"
            f"（motor_body_bottom_height_mm={inputs.motor_body_bottom_height_mm!r}）。"
            "⚠️ 車軸より上に材料を置かない（要件 5.8: 回転方向を目視できること）。"
        )

    outer_x_mm = socket_half_width_mm + _WALL_THICKNESS_MM
    half_width_mm = socket_radius_mm + _WALL_THICKNESS_MM
    top_height_mm = max(socket_top_height_mm, guide_lip_top_height_mm)

    # ⚠️ 手の入る開きは形状を作る前に決まる（要件 5.8）。他のスタンド側の下限と
    # 同じく、成立しない配置はここで拒む。
    leg_separation_mm = _leg_separation_mm(
        placement_radius_mm=inputs.base_radius_mm,
        leg_angles_deg=inputs.wheel_angles_deg,
        boxes=(
            (support_pad_outer_x_mm, outer_x_mm, half_width_mm),
            (
                support_pad_inner_x_mm,
                support_pad_outer_x_mm,
                support_pad_half_width_mm + _WALL_THICKNESS_MM,
            ),
        ),
    )
    if leg_separation_mm < MIN_HAND_ACCESS_MM:
        raise GeometryError(
            f"据え付けた脚の間の開きが {leg_separation_mm!r}mm しかなく、下限 "
            f"{MIN_HAND_ACCESS_MM!r}mm を下回る"
            f"（base_radius_mm={inputs.base_radius_mm!r}、"
            f"wheel_angles_deg={inputs.wheel_angles_deg!r}、脚の半幅 "
            f"{half_width_mm!r}mm）。⚠️ 台上でエンコーダ配線・コネクタ・電源の"
            "操作部へ手が届かなくなる（要件 5.8）。"
        )

    envelope = Envelope(
        x_mm=outer_x_mm - support_pad_inner_x_mm,
        y_mm=2.0 * half_width_mm,
        z_mm=top_height_mm,
    )

    return StandGeometry(
        leg_count=inputs.leg_count,
        placement_radius_mm=inputs.base_radius_mm,
        leg_angles_deg=inputs.wheel_angles_deg,
        socket_radius_mm=socket_radius_mm,
        socket_half_width_mm=socket_half_width_mm,
        trough_floor_height_mm=trough_floor_height_mm,
        socket_top_height_mm=socket_top_height_mm,
        wheel_bottom_height_mm=wheel_bottom_height_mm,
        wheel_center_height_mm=wheel_center_height_mm,
        support_pad_height_mm=support_pad_height_mm,
        support_pad_inner_x_mm=support_pad_inner_x_mm,
        support_pad_outer_x_mm=support_pad_outer_x_mm,
        support_pad_half_width_mm=support_pad_half_width_mm,
        guide_lip_top_height_mm=guide_lip_top_height_mm,
        outer_x_mm=outer_x_mm,
        half_width_mm=half_width_mm,
        top_height_mm=top_height_mm,
        leg_separation_mm=leg_separation_mm,
        retention_bearing_area_mm2=inputs.wheel_width_mm * inputs.support_span_mm,
        envelope=envelope,
    )


def part_names(params: ResolvedParams) -> tuple[str, ...]:
    """造形する部品の名を返す（design.md `#### Shapes` Service Interface）。

    ⚠️ **名は `PART_NAMES` から導く。** 部品名の正は1箇所であり、ここで別の
    文字列を作らない。⚠️ 現在は整備スタンドの脚だけである（タスク 3.2〜3.5 が
    駆動ベース・アダプタ・トレイ・配線ガイドを足す）。

    Args:
        params: `config.load_params()` の戻り値。

    Returns:
        `("service_stand_1", …)`。長さは `stand.leg_count`。
    """
    return tuple(
        f"{PART_NAMES[0]}_{index}"
        for index in range(1, params.chassis.stand.leg_count + 1)
    )


# ---------------------------------------------------------------------------
# 形状指標（⚠️ 形状ライブラリを import しない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuiltPart:
    """構築済みの部品1点と、その形状指標（design.md `#### Shapes`）。

    ⚠️ **`solid` を中核層へ渡さない。** 型を `object` にしてあるのは、形状
    ライブラリの型が中核層の署名へ漏れないようにするためである（上流
    `catch_mechanism.shapes.BuiltPart` と同じ扱い）。

    Attributes:
        name: 部品名。`part_names` の同じ位置の要素と一致する。
        solid: build123d の `Part`。⚠️ 中核層へは渡らない。
        metrics: 体積・境界箱・立体数（上流の `PartMetrics`）。
            ⚠️ **`solid` から抽出した値であり、寸法からの再計算ではない。**
    """

    name: str
    solid: object
    metrics: PartMetrics


def measure_part(name: str, solid: object) -> PartMetrics:
    """構築済みの形状から体積・境界箱・立体数を抽出する（要件 1.12）。

    ⚠️ **形状ライブラリを import しない。** `solid` は `object` として受け取り、
    `volume` / `bounding_box()` / `solids()` という**属性の形**だけに依存する
    （上流 `catch_mechanism.shapes.measure_part` と同じ規律）。⚠️ **丸めない**
    ——版差の吸収は記録側の許容差（`baseline`）が持つ。

    Args:
        name: 部品名。`PartMetrics.part_name` になる。
        solid: 構築済みの形状。

    Returns:
        抽出した形状指標。

    Raises:
        AttributeError: `solid` が上記3つの属性を持たない場合。
    """
    size = solid.bounding_box().size  # type: ignore[attr-defined]
    return PartMetrics(
        part_name=name,
        volume_mm3=float(solid.volume),  # type: ignore[attr-defined]
        bbox_mm=(float(size.X), float(size.Y), float(size.Z)),
        solid_count=len(solid.solids()),  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# 構築（⚠️ 形状ライブラリの import はこの下だけ）
# ---------------------------------------------------------------------------


def _require_shape_library() -> Any:
    """形状ライブラリを遅延 import し、未導入なら `CadUnavailableError` にする。

    ⚠️ **形状生成の要求を成功として黙って読み飛ばさない**（`errors.py` /
    design.md「Error Categories and Responses」）。メッセージには導入方法を載せる
    ——`cad` は上流が宣言する任意依存であり、未導入は異常ではなく既定の状態である。
    """
    try:
        import build123d
    except ImportError as exc:  # pragma: no cover - 導入済み環境では通らない
        raise CadUnavailableError(
            "形状ライブラリ（build123d）を読み込めないため形状を生成できない。"
            "任意依存 `cad` extra を導入すること"
            "（例: `pip install -e '.[cad]'`）。"
            "⚠️ 寸法の読み込み・幾何の導出・隙間の算出・接合部の導出は"
            "この環境でも成立する（design.md「Allowed Dependencies」/"
            "「Dependency Direction」）。"
        ) from exc
    return build123d


def build_service_stand_legs(
    inputs: StandInputs, printing: PrintingConstraints
) -> tuple[BuiltPart, ...]:
    """整備スタンドの脚を全点構築する（要件 5.1, 5.3-5.6, 5.8）。

    形状は**寸法パラメータから決定される手続き**であり、対話操作も外部 CAD の
    起動も要さない（要件 1.11）。⚠️ **3脚は同一形状**であり、据え付けの角度だけが
    異なる（`StandGeometry.leg_angles_deg`）——脚が独立していることが決定 5 の
    要点であり、位置を形へ焼き付けると1体の枠と変わらなくなる。

    ## 構築の順序（⚠️ 検査が先）

    design.md `#### Shapes` は「構築の前に `check_material` / `check_envelope` を
    通す」と定め、要件 2.3 は超過した軸と超過量を示すことを求める。⚠️ **検査を
    通らない形状のソリッドを作らない**——作ってから捨てる実装は、書き出し
    （タスク 3.7）が検査を飛ばした瞬間に無検査の生成物を出す。

    Args:
        inputs: 整備スタンドの設計入力（要件 5.2 の限定はこの型が担保する）。
        printing: 上流の造形制約。⚠️ 設計入力ではなく**造形可能性の関門**である。

    Returns:
        脚1点につき1つの `BuiltPart`。名前と並びは `service_stand_1` からの連番。

    Raises:
        GeometryError: 幾何が成立しない場合（`stand_geometry` からの伝播）、
            または外接箱が造形可能寸法に収まらない場合。
        ParameterError: 材料が上流の許可一覧に無い場合（`check_material` からの
            伝播。要件 2.4）。
        CadUnavailableError: 形状ライブラリが導入されていない場合。⚠️ 上記の検査は
            **すべて import より前**に済むため、脚が成立しないことは CAD 非導入の
            環境でも観測できる（design.md「Allowed Dependencies」/
            「Dependency Direction」）。
    """
    geometry = stand_geometry(inputs)

    # 要件 2.4: 材料は上流の許可一覧の範囲から選ぶ。
    check_material(printing)

    # 要件 2.2, 2.3: 断片の外接箱が造形可能寸法に収まることを、上流の検査で見る。
    # ⚠️ **1脚ずつ**の検査である（決定 5「1体の枠にすると造形可能寸法を超える」）。
    violations = check_envelope(PART_NAMES[0], geometry.envelope, printing)
    if violations:
        detail = "、".join(
            f"軸 {violation.axis} が {violation.envelope_mm}mm で"
            f"上限 {violation.limit_mm}mm を {violation.excess_mm}mm 超過"
            for violation in violations
        )
        raise GeometryError(
            f"{PART_NAMES[0]} の外接箱が造形可能寸法に収まらない（{detail}）。"
            "⚠️ 脚は3つに分かれているため、これ以上の分割で解決する問題ではない"
            "（決定 5: 1体の枠にしない）。ホイール配置と隙間の値を見直すこと。"
        )

    solid = _build_leg(geometry)
    return tuple(
        BuiltPart(
            name=f"{PART_NAMES[0]}_{index}",
            solid=solid,
            metrics=measure_part(f"{PART_NAMES[0]}_{index}", solid),
        )
        for index in range(1, geometry.leg_count + 1)
    )


def _build_leg(geometry: StandGeometry) -> object:
    """脚1点のソリッドを組み立てる（局所座標。本モジュール docstring 参照）。

    参照は**幾何セレクタ**（座標と範囲）で明示的に組み立てる。⚠️ 生成名
    （`Face6` 等）を一切使わない——寸法を変えたときに名前が振り直されても、
    位置で書かれた参照は同じ場所を指し続ける。
    """
    build123d = _require_shape_library()
    align = (build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER)

    def block(
        x_range: tuple[float, float],
        y_range: tuple[float, float],
        z_range: tuple[float, float],
    ) -> object:
        """局所座標の範囲で表した直方体。"""
        x_min, x_max = x_range
        y_min, y_max = y_range
        z_min, z_max = z_range
        center = (
            (x_min + x_max) / 2.0,
            (y_min + y_max) / 2.0,
            (z_min + z_max) / 2.0,
        )
        return build123d.Location(center) * build123d.Box(
            x_max - x_min, y_max - y_min, z_max - z_min, align=align
        )

    half_width_mm = geometry.half_width_mm
    pad_half_mm = geometry.support_pad_half_width_mm
    pad_x = (geometry.support_pad_inner_x_mm, geometry.support_pad_outer_x_mm)

    # 谷を含む本体。⚠️ 機体側の面（-x）は開いている——モータ胴体の逃げであり、
    # 機体を真上から降ろしたあと台の上で位置を合わせられる形でもある。
    body = block(
        (geometry.support_pad_outer_x_mm, geometry.outer_x_mm),
        (-half_width_mm, half_width_mm),
        (0.0, geometry.socket_top_height_mm),
    )

    # ⚠️ **谷はホイールの等距離面である。** 車軸と同軸・半径 `R + 隙間` の円筒を
    # 抜くため、外周との隙間はどこでも `wheel_rotation_clearance_mm` になる
    # （要件 5.5）。抜く範囲は幅方向にも隙間ぶん広く、機体側へは本体の面を
    # 貫いて開く（ホイールを真上から降ろせる形）。⚠️ 外周側は貫かない
    # ——半径方向外向きの壁が残り、機体が外へ抜けない（要件 5.6）。
    socket_length_mm = 2.0 * geometry.socket_half_width_mm + _WALL_THICKNESS_MM
    socket_center_x_mm = geometry.socket_half_width_mm - socket_length_mm / 2.0
    body -= (
        build123d.Location(
            (socket_center_x_mm, 0.0, geometry.wheel_center_height_mm)
        )
        * build123d.Rotation(0, 90, 0)
        * build123d.Cylinder(geometry.socket_radius_mm, socket_length_mm, align=align)
    )

    # ⚠️ **支持パッドは谷を抜いた後に足す。** 谷の円筒は機体側へ本体の面を貫いて
    # 伸びるため、先に足すとパッドの外側が削られ、脚が2つの立体へ割れる
    # （`test_build_parts_returns_one_independent_solid_per_leg` が
    # `solid_count == 1` で捉える）。パッドは谷の底の肉と面で繋がる。
    body += block(pad_x, (-pad_half_mm, pad_half_mm), (0.0, geometry.support_pad_height_mm))

    # 案内リブ（⚠️ 位置決めのみ。駆動ユニットへは触れない）。
    for sign in (-1.0, 1.0):
        near_mm = sign * pad_half_mm
        far_mm = sign * (pad_half_mm + _WALL_THICKNESS_MM)
        body += block(
            pad_x,
            (min(near_mm, far_mm), max(near_mm, far_mm)),
            (0.0, geometry.guide_lip_top_height_mm),
        )

    # ⚠️ 谷より上（車軸より上）に材料を残さない（要件 5.8）。円筒は車軸中心を
    # 通るため、受け面の上端が車軸より低いときに限り本体の上部が残る——その
    # 残りは谷の壁そのものであり、覆いではない。
    return body


def build_parts(
    params: ResolvedParams, layout: ChassisLayout
) -> tuple[BuiltPart, ...]:
    """全部品を構築し、それぞれの形状指標を添えて返す（design.md `#### Shapes`）。

    ⚠️ **現在返るのは整備スタンドの脚だけである**（要件 5.1: 他のどの造形物よりも
    先に出す。タスク 3.2〜3.5 が残りの部品を足す）。並びと名前は
    `part_names(params)` に一致する。

    ⚠️ **設計入力の絞り込みは `stand_inputs` が行う。** 本関数がスタンドの構築へ
    `ResolvedParams` を渡すことはない（要件 5.2）。

    ## 決定性（要件 1.12）

    同一の `ResolvedParams` からの複数回の生成は同一の `PartMetrics` を返す。
    ⚠️ 3脚は**同一のソリッド**を共有するため、指標も互いに一致する——脚が
    別形状になるのは、脚ごとに違う値を読んだときだけである。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        部品1点につき1つの `BuiltPart`。

    Raises:
        GeometryError: 幾何が成立しない、または造形可能寸法に収まらない場合。
        ParameterError: 材料が上流の許可一覧に無い場合。
        CadUnavailableError: 形状ライブラリが導入されていない場合。
    """
    return build_service_stand_legs(stand_inputs(params, layout), params.printing)
