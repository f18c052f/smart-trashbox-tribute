"""部品の形状構築と指標の抽出（design.md `#### Shapes` / 要件 1.11, 1.12, 2.2,
2.5, 2.6, 2.11, 3.1, 3.2, 3.7, 3.8, 3.10, 5.1-5.6, 5.8, 6.1, 6.2, 6.5, 6.7, 6.9）。

⚠️ **現在構築するのは整備スタンドと駆動ベースとゴミ箱固定アダプタである。**
要件 5.1 は整備スタンドを他のどの造形物よりも先に設計・造形・検証することを
求めており（タスク 3.1）、駆動ベース（`hub_plate` / `motor_arm_*`、タスク 3.2）と
ゴミ箱固定アダプタ（`adapter_segment_*`、タスク 3.3）がそれに続く。design.md
`#### Shapes` の部品表の残り（`battery_tray` / `board_tray` / `cable_guide_*`）は
タスク 3.4〜3.5 が本モジュールへ足す。
`PART_NAMES` と `build_parts` はその都度広がる。

## 常時荷重がかかる部位の断面の根拠（要件 2.5 / design.md「機構の決定」決定 3）

design.md は「⚠️ **PLA は Tg 以下でも常時荷重下でクリープする**。PETG も耐
クリープ性が高いわけではないため、**常時荷重を薄いリブや小さな当たり面で受けない**」
と定めている。駆動ベースはその「常時荷重がかかる部位」そのものである
——⚠️ **Ø270 を張る構造が機体と搭載物の質量を常時支える。**

`base.plate_thickness_mm` と `base.arm_thickness_mm` を 15.0mm に採るのは、
独立した2つの根拠が同じ向きを指すためである。

1. **接合面が座の環を載せられること**（要件 2.9）。中央部↔モータ取付部の接合面の
   高さはアームの厚さであり、⚠️ **座の外径 Ø9.2（＝ `BOSS_DIAMETER_FACTOR ×
   insert_outer_diameter_mm`）を下回ると `joints.BEARING_AREA_FORMULA` が数える環が
   面に載らない**。6.0mm では実現する当たり面が 83.82mm^2 にしかならず、本 Spec の
   下限 90.0mm^2 を下回る——にもかかわらず解析式は 114.79mm^2 を報告する。
   15.0mm は 9.2 ＋ 両側 2.9mm の肉である
2. **断面がクリープに対して薄くないこと**（要件 2.5）。6mm の PETG で Ø270 を
   張る構造は、座の話を抜きにしても常時荷重に対して薄い

⚠️ **1 だけを根拠にしない。** 上流がより細いインサートへ変われば 1 は緩むが、
2 は緩まない（`drive_base_geometry` が拒否するのは 1 の側だけである）。

## ⚠️ 点数と接合部の諸元を数え直さない（要件 2.1, 2.9）

部品の点数は `joints.segment_counts()`、中央部↔モータ取付部のボルト本数と重ね代は
`joints.arm_joint_lap_length_mm` が唯一の正である。⚠️ **形の側で数え直すと、
当たり面の下限を上げたときに座だけが増えて舌が伸びない、という食い違いが黙って
残る**——本モジュールは `joints` の左側ではなく右側の層であり、読む側である。

## 形状ライブラリを module 直下で import しない（design.md「Allowed Dependencies」）

⚠️ **CAD 非導入の環境で評価できることを求める chassis 要件は存在しない。** この
義務の出所は design.md の「Allowed Dependencies」（`build123d` の import は
`shapes` / `export` に限る）と「Dependency Direction」（`__init__` は `shapes` /
`export` を import せず、公開 API が OCCT を要求しない）、および上流
`catch_mechanism.shapes` の先例である。⚠️ **許されていることと「モジュール読み込み
時に必要にしてよい」ことは別である**。`stand_geometry` と
`drive_base_geometry` は純粋な算術であり、`cad` extra 非導入の環境でも**全数値と
成立条件**を評価できる——脚や駆動ベースが成立するかどうかを知るために CAD を
要求しない。import は実際にソリッドを構築する関数（`build_service_stand_legs` /
`build_drive_base`）の内側にあり、失敗は `CadUnavailableError`
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
from collections.abc import Sequence
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
from chassis_mechanism.joints import (
    BOSS_DIAMETER_FACTOR,
    arm_joint_lap_length_mm,
    derive_joints,
    segment_counts,
)
from chassis_mechanism.layout import ChassisLayout

__all__ = [
    "MIN_HAND_ACCESS_MM",
    "PART_NAMES",
    "ADAPTER_SEGMENT_PART_NAME",
    "HUB_PLATE_PART_NAME",
    "MOTOR_ARM_PART_NAME",
    "SERVICE_STAND_PART_NAME",
    "AdapterGeometry",
    "BuiltPart",
    "DriveBaseGeometry",
    "StandGeometry",
    "StandInputs",
    "adapter_geometry",
    "build_adapter_segments",
    "build_drive_base",
    "build_parts",
    "build_service_stand_legs",
    "drive_base_geometry",
    "measure_part",
    "part_names",
    "stand_geometry",
    "stand_inputs",
]


HUB_PLATE_PART_NAME: Final[str] = "hub_plate"
MOTOR_ARM_PART_NAME: Final[str] = "motor_arm"
ADAPTER_SEGMENT_PART_NAME: Final[str] = "adapter_segment"
SERVICE_STAND_PART_NAME: Final[str] = "service_stand"

PART_NAMES: Final[tuple[str, ...]] = (
    HUB_PLATE_PART_NAME,
    MOTOR_ARM_PART_NAME,
    ADAPTER_SEGMENT_PART_NAME,
    SERVICE_STAND_PART_NAME,
)
"""本モジュールが構築する部品の**種類**（design.md `#### Shapes` の部品表）。

⚠️ **1件の要素は「部品の種類」であって造形する点数ではない。** 実際に造形する
点数は `joints.segment_counts()` が持ち（要件 2.1: 分割数は導出であって設定値では
ない）、部品名は `part_names` がその2つから組み立てる。⚠️ タスク 3.3〜3.5 が
アダプタ・トレイ・配線ガイドを足すと、この表はその順に伸びる。
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

_BOTH_SIDES: Final[int] = 2
"""量が中心線の**両側**に効くことを表す係数（⚠️ 寸法ではない）。

`joints._BOTH_SIDES` と同じ趣旨である。舌の隙間も、中央部の外径への舌の張り出しも、
片側ずつ効くため直径・厚さには2倍で効く。
"""

_UNSPLIT_PART_COUNT: Final[int] = 1
"""分割しない部品の点数（⚠️ 番号を付けない部品を表す。`joints._UNSPLIT_SEGMENT_COUNT`）。"""

_JOINT_FIT_CLEARANCE_MM: Final[float] = 0.4
"""造形部品どうしが噛み合う箇所の、片側あたりの嵌め合い隙間（mm、要件 2.11 /
決定 4）。⚠️ **重ね継手（舌と二股）だけでなく、アダプタの裾が中央部の外縁を
掴む隙間・裾がアームを避ける逃げ・座ぐりの落とし込みの余裕にも同じ量を使う**
——同じ物理（FDM の造形誤差をそのまま逃がすこと）に別の数を割り当てない。

⚠️ **切削加工を前提とする嵌合・面出しを設計に含めない。** ハブ板の舌は二股の溝
より両側で本値ぶん薄く、舌の先端と溝の底の間にも同じ量を残す。0.4mm は 0.4mm
ノズルの押出幅1本ぶんであり、FDM の造形誤差をそのまま逃がせる大きさである
——⚠️ **これを詰めると「削って合わせる」ことが前提の設計になる**（寸法差は長穴と
隙間で吸収する、が決定 4 である）。
"""

_TOOL_OVERSHOOT_MM: Final[float] = 1.0
"""切り取り工具を実体の面より外へ伸ばす余長（mm）。

⚠️ **形を決める量ではない。** 面とぴったり同じ長さの工具で切ると、同一面での
ブール演算の結果に形が委ねられる。⚠️ **この値を変えても部品の寸法は1つも
変わらない**——変わるのは工具の長さだけである（`_build_hub_plate` が舌の穴で
使っている 1.0 と同じ趣旨であり、同じ値を名前付きで置く）。
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

    ⚠️ **名は `PART_NAMES` から、点数は `joints.segment_counts()` から導く。**
    部品名の正は1箇所であり、ここで別の文字列を作らない。⚠️ **点数をここで
    数え直さない**——分割数は導出であって設定値ではなく（要件 2.1）、`joints` が
    その唯一の置き場所である。

    分割しない部品は番号を持たず（`"hub_plate"`）、分割する部品は 1 から始まる
    連番を持つ（`"motor_arm_1"`）。⚠️ この規約は `joints.derive_joints` が組み立てる
    接合部の部材名と一致する——一致しなければ、接合部の一覧が存在しない部品を
    指すことになる。

    Args:
        params: `config.load_params()` の戻り値。

    Returns:
        `("hub_plate", "motor_arm_1", …, "service_stand_1", …)`。並びは
        `PART_NAMES` の順である。
    """
    counts = segment_counts(params)
    names: list[str] = []
    for base_name in PART_NAMES:
        count = counts[base_name]
        if count == _UNSPLIT_PART_COUNT:
            names.append(base_name)
            continue
        names.extend(f"{base_name}_{index}" for index in range(1, count + 1))
    return tuple(names)


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
    violations = check_envelope(SERVICE_STAND_PART_NAME, geometry.envelope, printing)
    if violations:
        detail = "、".join(
            f"軸 {violation.axis} が {violation.envelope_mm}mm で"
            f"上限 {violation.limit_mm}mm を {violation.excess_mm}mm 超過"
            for violation in violations
        )
        raise GeometryError(
            f"{SERVICE_STAND_PART_NAME} の外接箱が造形可能寸法に収まらない（{detail}）。"
            "⚠️ 脚は3つに分かれているため、これ以上の分割で解決する問題ではない"
            "（決定 5: 1体の枠にしない）。ホイール配置と隙間の値を見直すこと。"
        )

    solid = _build_leg(geometry)
    return tuple(
        BuiltPart(
            name=f"{SERVICE_STAND_PART_NAME}_{index}",
            solid=solid,
            metrics=measure_part(f"{SERVICE_STAND_PART_NAME}_{index}", solid),
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

    ⚠️ **現在返るのは駆動ベースとゴミ箱固定アダプタと整備スタンドの脚である**
    （タスク 3.4〜3.5 がトレイ・配線ガイドを足す）。並びと名前は
    `part_names(params)` に一致する。

    ⚠️ **設計入力の絞り込みは `stand_inputs` が行う。** 本関数がスタンドの構築へ
    `ResolvedParams` を渡すことはない（要件 5.2）——駆動ベースにはその限定が無い
    （スタンドだけが「ゴミ箱とトレイ類の確定を待たない」ことを求められている）。

    ## 決定性（要件 1.12）

    同一の `ResolvedParams` からの複数回の生成は同一の `PartMetrics` を返す。
    ⚠️ 3脚と3本のアームはそれぞれ**同一のソリッド**を共有するため、指標も互いに
    一致する——別形状になるのは、点ごとに違う値を読んだときだけである。

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
    return (
        build_drive_base(params, layout)
        + build_adapter_segments(params, layout)
        + build_service_stand_legs(stand_inputs(params, layout), params.printing)
    )


# ---------------------------------------------------------------------------
# 駆動ベース（タスク 3.2 / 要件 2.5, 2.6, 2.11, 3.1, 3.2, 3.7, 3.8, 3.10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriveBaseGeometry:
    """駆動ベース（中央部＋放射状のモータ取付部）の幾何。

    ⚠️ **形状オブジェクトを持たない**（`StandGeometry` と同じ規律）。値はすべて
    **機体座標**であり、原点は機体中心、`z` は接地面（床）からの高さ、
    第1輪の方向を `+x` に採る。アーム1点はこの向きで構築し、⚠️ **据え付けの角度を
    形へ焼き付けない**——3本は同一形状であり、`wheel_angles_deg` だけが位置を与える
    （中央部は3方向の舌を持つため角度を知っている）。

    ## 中央部↔モータ取付部は重ね継手である（要件 2.6, 3.10）

    ハブ板の**舌**をアームの**二股**が挟み、接線方向のボルトが二面せん断で受ける
    （`joints.derive_joints` の家族表）。⚠️ **接合面の法線は接線方向 `y` であり、
    積層方向 `z` ではない**（要件 2.8 / A-5）。この向きにしたことの帰結は3つある。

    - 座を並べられるのは面内の**半径方向**だけである。重ね代
      （`joints.arm_joint_lap_length_mm`）はその並びの長さそのものである
    - 面のもう一方の辺は**アームの厚さ**である。⚠️ **厚さが座の外径
      （`joints.BOSS_DIAMETER_FACTOR × insert_outer_diameter_mm`）を下回ると、
      `joints.BEARING_AREA_FORMULA` が数える環が面に載らない**——解析値だけが
      下限を満たし、実物は満たさない状態になる。`drive_base_geometry` はその寸法を
      構築の前に拒否し、実形状との一致は `test_chassis_invariants.py` が測る
    - ボルトが貫くのは「二股の側壁 ＋ 舌」であり、⚠️ 側壁の厚さを
      `base.arm_thickness_mm`、溝の幅を `base.plate_thickness_mm` に採ることで
      `joints.FASTENER_LENGTH_FORMULA` の積み上がり厚さと一致する

    ## 寸法差は長穴と隙間で吸収する（要件 2.11 / 決定 4）

    ⚠️ **切削加工を前提とする嵌合・面出しを含めない。** 舌は溝より
    `_JOINT_FIT_CLEARANCE_MM` だけ薄く、ブラケット取付穴は長穴であり、
    造形する穴はどれも上流の貫通穴径以上である（`bore_diameters_mm`）。

    ## モータ本体を掴まない（要件 3.7）

    モータ胴体は付属金属ブラケットにぶら下がる。⚠️ **胴体の最上点は駆動ベースの
    下面より低い**——半径方向では中央部ともアームとも重なるため、隔てているのは
    高さだけである。`motor_*` の各値はその関係を検査できる形で持つ。

    Attributes:
        wheel_count: 輪の数（＝アームの本数）。
        wheel_angles_deg: 各輪の取付角（度）。⚠️ `ChassisLayout` から受け取る。
        hub_radius_mm: 中央部の外径の半分（`base.hub_outer_diameter_mm / 2`）。
        underside_height_mm: ベース板下面の高さ（＝取付面。鉛直スタックが持つ）。
        plate_thickness_mm: 中央部の厚さ（mm）。
        arm_thickness_mm: アームの厚さ（mm）。⚠️ **接合面の高さである。**
        arm_half_width_mm: アームの半幅（mm、接線方向）。
        arm_outer_radius_mm: アームの外端の半径（＝ホイール配置半径）。
        bolt_count: 中央部↔アームの接合部のボルト本数（⚠️ `joints` が正）。
        boss_diameter_mm: ボルト座の外径（mm、⚠️ `joints` が正）。
        through_hole_diameter_mm: 貫通穴の径（mm、上流 `JointPolicy`）。
        insert_bore_diameter_mm: インサート座の下穴径（mm、上流）。
        insert_bore_depth_mm: インサート座の深さ（mm、上流のインサート長）。
        lap_length_mm: 重ね代（mm、⚠️ `joints.arm_joint_lap_length_mm` が正）。
        tongue_thickness_mm: ハブ板の舌の厚さ（mm）。⚠️ 溝より隙間ぶん薄い。
        fork_slot_width_mm: 二股の溝の幅（mm、＝ `base.plate_thickness_mm`）。
        fork_wall_thickness_mm: 二股の側壁の厚さ（mm、＝ `base.arm_thickness_mm`）。
        fork_half_width_mm: 二股の外側の半幅（mm）。⚠️ **接合面はこの位置にある。**
        fork_root_radius_mm: 二股の溝の底の半径（mm）。舌の先端より隙間ぶん外。
        bolt_radii_mm: ボルトの半径方向の位置（mm）。
        bolt_height_mm: ボルトの軸の高さ（mm、アームの厚さの中央）。
        slot_width_mm: ブラケット取付長穴の幅（mm、＝上流の貫通穴径）。
        slot_length_mm: 長穴の長さ（mm、＝幅 ＋ 移動量）。
        slot_travel_mm: 長穴が吸収できる移動量（mm、`base.slot_travel_mm`）。
        slot_center_radius_mm: 長穴の中心の半径（mm、＝取付面までの距離）。
        slot_offsets_mm: 長穴の接線方向の位置（mm）。ブラケットの穴ピッチに従う。
        motor_body_diameter_mm: モータ胴体の外径（mm）。
        motor_axis_height_mm: モータ軸の高さ（mm、＝車軸中心）。
        motor_inner_radius_mm: 胴体の機体側の端の半径（mm）。
        motor_outer_radius_mm: 胴体のギヤボックス端面の半径（mm）。
        bore_diameters_mm: ⚠️ **造形する穴の径の一覧**（要件 2.11 の検査対象）。
        hub_plate_envelope: 中央部の軸並行外接箱。⚠️ 舌の張り出しを含む。
        motor_arm_envelope: アーム1本の軸並行外接箱。
    """

    wheel_count: int
    wheel_angles_deg: tuple[float, ...]
    hub_radius_mm: float
    underside_height_mm: float
    plate_thickness_mm: float
    arm_thickness_mm: float
    arm_half_width_mm: float
    arm_outer_radius_mm: float
    bolt_count: int
    boss_diameter_mm: float
    through_hole_diameter_mm: float
    insert_bore_diameter_mm: float
    insert_bore_depth_mm: float
    lap_length_mm: float
    tongue_thickness_mm: float
    fork_slot_width_mm: float
    fork_wall_thickness_mm: float
    fork_half_width_mm: float
    fork_root_radius_mm: float
    bolt_radii_mm: tuple[float, ...]
    bolt_height_mm: float
    slot_width_mm: float
    slot_length_mm: float
    slot_travel_mm: float
    slot_center_radius_mm: float
    slot_offsets_mm: tuple[float, ...]
    motor_body_diameter_mm: float
    motor_axis_height_mm: float
    motor_inner_radius_mm: float
    motor_outer_radius_mm: float
    bore_diameters_mm: tuple[float, ...]
    hub_plate_envelope: Envelope
    motor_arm_envelope: Envelope


def _bracket_hole_offsets_mm(bracket: Any) -> tuple[float, ...]:
    """ブラケット取付穴の接線方向の位置を、穴数とピッチから等配置で並べる。

    ⚠️ **手で書いた位置を持たない。** 2穴なら `±pitch/2` であり、穴数が増えれば
    ピッチの区間を等分する。

    Raises:
        GeometryError: 穴が2つ未満の場合（1点では機体が回る）。
    """
    count = bracket.mount_hole_count
    if count < 2:
        raise GeometryError(
            f"bracket.mount_hole_count={count!r} は 2 以上でなければならない"
            "（1点で留めると取付部がボルトのまわりに回る）。"
        )
    pitch_mm = bracket.mount_hole_pitch_mm
    return tuple(
        pitch_mm * (index / (count - 1) - 0.5) for index in range(count)
    )


def drive_base_geometry(
    params: ResolvedParams, layout: ChassisLayout
) -> DriveBaseGeometry:
    """寸法パラメータと幾何の導出結果から駆動ベースの形を決める（タスク 3.2）。

    ⚠️ **形状を構築しない。** 本関数は算術のみで完結し、形状ライブラリの無い環境
    でも**全数値と成立条件**を評価できる（design.md「Allowed Dependencies」/
    「Dependency Direction」。`stand_geometry` と同じ規律）。

    ⚠️ **座の本数と重ね代を数え直さない**——`joints.arm_joint_lap_length_mm` と
    `joints.derive_joints` が唯一の正である（要件 2.1, 2.9）。形の側で数え直せば、
    当たり面の下限を上げたときに座だけが増えて舌が伸びない、という食い違いが
    黙って残る。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        駆動ベースの幾何。

    Raises:
        GeometryError: 接合面がボルト座の環を載せられない厚さの場合（要件 2.5,
            2.9）、二股がアームの幅に収まらない場合、舌の肉が残らない場合、
            インサート座が側壁に収まらない場合、造形部品がモータ胴体を掴む
            配置になる場合（要件 3.7）、または長穴がアームに収まらない場合。
            ⚠️ メッセージには**項目名と値**を載せる。
        catch_mechanism.ParameterError: 上流の `check_joint` が当たり面を拒否した
            場合（`derive_joints` からの伝播）。
    """
    chassis = params.chassis
    base = chassis.base
    joint = params.joint

    hub_radius_mm = base.hub_outer_diameter_mm / 2.0
    underside_height_mm = layout.vertical.mount_face_height_mm
    boss_diameter_mm = BOSS_DIAMETER_FACTOR * joint.insert_outer_diameter_mm

    # ⚠️ **接合面の厚さが座の外径を下回ると、記録された当たり面が面に載らない。**
    # `joints.BEARING_AREA_FORMULA` は座の環をまるごと数えるため、面が薄いほうへ
    # 環がはみ出しても解析値は下がらない——下限を満たすという判定だけが残る。
    # これが `base.arm_thickness_mm` を 6.0mm から引き上げた根拠である（要件 2.5:
    # 「⚠️ 薄いリブや小さな当たり面で受けない」）。
    if base.arm_thickness_mm < boss_diameter_mm:
        raise GeometryError(
            f"arm_thickness_mm={base.arm_thickness_mm!r} が接合面の高さであり、"
            f"ボルト座の外径 {boss_diameter_mm!r}mm"
            f"（BOSS_DIAMETER_FACTOR={BOSS_DIAMETER_FACTOR!r} × "
            f"insert_outer_diameter_mm={joint.insert_outer_diameter_mm!r}）を"
            f"{boss_diameter_mm - base.arm_thickness_mm!r}mm 下回る。"
            "⚠️ 座の環が接合面に載らないため、joints.BEARING_AREA_FORMULA が"
            "記録する当たり面は実形状では実現しない（要件 2.5, 2.9）。"
            "断面を厚く取ること。"
        )

    fork_wall_thickness_mm = base.arm_thickness_mm
    fork_slot_width_mm = base.plate_thickness_mm
    fork_half_width_mm = fork_wall_thickness_mm + fork_slot_width_mm / 2.0
    arm_half_width_mm = base.arm_width_mm / 2.0
    if fork_half_width_mm > arm_half_width_mm:
        raise GeometryError(
            f"二股の外側の半幅 {fork_half_width_mm!r}mm が "
            f"arm_width_mm={base.arm_width_mm!r} の半分 {arm_half_width_mm!r}mm を"
            f"超える（側壁 arm_thickness_mm={base.arm_thickness_mm!r} ＋ 溝の半分 "
            f"plate_thickness_mm={base.plate_thickness_mm!r}/2）。"
            "⚠️ 二股がアームの幅を超えると、joints が断片の外接箱へ渡している "
            "arm_width_mm が実形状を覆わなくなる。"
        )

    tongue_thickness_mm = fork_slot_width_mm - _BOTH_SIDES * _JOINT_FIT_CLEARANCE_MM
    if tongue_thickness_mm <= 0.0:
        raise GeometryError(
            f"舌の厚さが {tongue_thickness_mm!r}mm になり肉が残らない"
            f"（溝の幅 plate_thickness_mm={base.plate_thickness_mm!r} − "
            f"両側の隙間 {_BOTH_SIDES * _JOINT_FIT_CLEARANCE_MM!r}mm）。"
            "⚠️ 寸法差は隙間で吸収する（要件 2.11 / 決定 4）ため、隙間を削って"
            "解決しない。"
        )

    if joint.insert_length_mm > fork_wall_thickness_mm:
        raise GeometryError(
            f"インサート長 insert_length_mm={joint.insert_length_mm!r} が二股の"
            f"側壁の厚さ {fork_wall_thickness_mm!r}mm を超える"
            f"（arm_thickness_mm={base.arm_thickness_mm!r}）——"
            "⚠️ 座が側壁を突き抜ける。"
        )

    # ⚠️ 本数と重ね代は `joints` が正である（形の側で数え直さない）。
    lap_length_mm = arm_joint_lap_length_mm(layout, params)
    bolt_count = round(lap_length_mm / boss_diameter_mm)
    bolt_radii_mm = tuple(
        hub_radius_mm + boss_diameter_mm * (index + 0.5) for index in range(bolt_count)
    )
    fork_root_radius_mm = hub_radius_mm + lap_length_mm + _JOINT_FIT_CLEARANCE_MM

    # ⚠️ モータ胴体は付属金属ブラケットにぶら下がる（要件 3.7）。胴体の最上点が
    # ベース下面へ届く寸法は、造形部品が胴体を掴む設計そのものである。
    motor_axis_height_mm = layout.vertical.axle_center_height_mm
    motor_top_height_mm = motor_axis_height_mm + chassis.motor.body_diameter_mm / 2.0
    if motor_top_height_mm > underside_height_mm:
        raise GeometryError(
            f"モータ胴体の最上点 {motor_top_height_mm!r}mm が駆動ベース下面 "
            f"{underside_height_mm!r}mm を超える"
            f"（body_diameter_mm={chassis.motor.body_diameter_mm!r}、"
            f"車軸中心 {motor_axis_height_mm!r}mm）。⚠️ 造形部品でモータ本体を"
            "直接クランプしない（要件 3.7）——取り付けは付属金属ブラケットが担う。"
        )
    # ⚠️ ギヤボックス端面はホイール中心面から軸方向スタックのぶん内側にある
    # （`layout.axial_stack_mm[1]` ＝ ハブのフランジ厚 ＋ ホイール半幅）。
    motor_outer_radius_mm = layout.base_radius_mm - layout.axial_stack_mm[1]
    motor_inner_radius_mm = motor_outer_radius_mm - chassis.motor.body_length_mm

    slot_width_mm = joint.through_hole_diameter_mm
    slot_length_mm = slot_width_mm + base.slot_travel_mm
    slot_center_radius_mm = base.hub_center_to_mount_face_mm
    slot_offsets_mm = _bracket_hole_offsets_mm(chassis.bracket)
    slot_inner_radius_mm = slot_center_radius_mm - slot_length_mm / 2.0
    slot_outer_radius_mm = slot_center_radius_mm + slot_length_mm / 2.0
    if slot_inner_radius_mm <= fork_root_radius_mm:
        raise GeometryError(
            f"ブラケット取付長穴の内端 {slot_inner_radius_mm!r}mm が二股の溝の底 "
            f"{fork_root_radius_mm!r}mm へ掛かる"
            f"（hub_center_to_mount_face_mm={slot_center_radius_mm!r}、"
            f"重ね代 {lap_length_mm!r}mm）。⚠️ 接合部と長穴が同じ場所を奪い合う。"
        )
    if slot_outer_radius_mm >= layout.base_radius_mm:
        raise GeometryError(
            f"ブラケット取付長穴の外端 {slot_outer_radius_mm!r}mm がアームの外端 "
            f"{layout.base_radius_mm!r}mm へ届く"
            f"（hub_center_to_mount_face_mm={slot_center_radius_mm!r}）。"
        )
    edge_mm = max(abs(offset_mm) for offset_mm in slot_offsets_mm) + slot_width_mm / 2.0
    if edge_mm >= arm_half_width_mm:
        raise GeometryError(
            f"ブラケット取付長穴の外縁 {edge_mm!r}mm が arm_width_mm="
            f"{base.arm_width_mm!r} の半分 {arm_half_width_mm!r}mm へ届く"
            f"（mount_hole_pitch_mm={chassis.bracket.mount_hole_pitch_mm!r}、"
            f"長穴の幅 {slot_width_mm!r}mm）。"
        )

    # ⚠️ **造形する穴の径の一覧である**（要件 2.11 の検査対象）。相手部品の呼び
    # 寸法と一致する穴を1つも持たないことを `test_chassis_shapes.py` が固定する。
    bore_diameters_mm = tuple(
        sorted({slot_width_mm, joint.through_hole_diameter_mm, joint.insert_outer_diameter_mm})
    )

    hub_plate_outer_diameter_mm = base.hub_outer_diameter_mm + _BOTH_SIDES * lap_length_mm
    return DriveBaseGeometry(
        wheel_count=base.wheel_count,
        wheel_angles_deg=layout.wheel_angles_deg,
        hub_radius_mm=hub_radius_mm,
        underside_height_mm=underside_height_mm,
        plate_thickness_mm=base.plate_thickness_mm,
        arm_thickness_mm=base.arm_thickness_mm,
        arm_half_width_mm=arm_half_width_mm,
        arm_outer_radius_mm=layout.base_radius_mm,
        bolt_count=bolt_count,
        boss_diameter_mm=boss_diameter_mm,
        through_hole_diameter_mm=joint.through_hole_diameter_mm,
        insert_bore_diameter_mm=joint.insert_outer_diameter_mm,
        insert_bore_depth_mm=joint.insert_length_mm,
        lap_length_mm=lap_length_mm,
        tongue_thickness_mm=tongue_thickness_mm,
        fork_slot_width_mm=fork_slot_width_mm,
        fork_wall_thickness_mm=fork_wall_thickness_mm,
        fork_half_width_mm=fork_half_width_mm,
        fork_root_radius_mm=fork_root_radius_mm,
        bolt_radii_mm=bolt_radii_mm,
        bolt_height_mm=underside_height_mm + base.arm_thickness_mm / 2.0,
        slot_width_mm=slot_width_mm,
        slot_length_mm=slot_length_mm,
        slot_travel_mm=base.slot_travel_mm,
        slot_center_radius_mm=slot_center_radius_mm,
        slot_offsets_mm=slot_offsets_mm,
        motor_body_diameter_mm=chassis.motor.body_diameter_mm,
        motor_axis_height_mm=motor_axis_height_mm,
        motor_inner_radius_mm=motor_inner_radius_mm,
        motor_outer_radius_mm=motor_outer_radius_mm,
        bore_diameters_mm=bore_diameters_mm,
        # ⚠️ 舌の張り出しを含む（`joints._check_fragment_envelopes` と同じ量）。
        # 舌の先端が描く円の外接正方形であり、実形状の外接箱の**覆い**である。
        hub_plate_envelope=Envelope(
            x_mm=hub_plate_outer_diameter_mm,
            y_mm=hub_plate_outer_diameter_mm,
            z_mm=base.plate_thickness_mm,
        ),
        motor_arm_envelope=Envelope(
            x_mm=layout.arm_length_mm,
            y_mm=base.arm_width_mm,
            z_mm=base.arm_thickness_mm,
        ),
    )


def _box_between(
    build123d: Any,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    z_range: tuple[float, float],
) -> Any:
    """座標の範囲で表した直方体（機体座標）。"""
    x_min, x_max = x_range
    y_min, y_max = y_range
    z_min, z_max = z_range
    return build123d.Location(
        ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0, (z_min + z_max) / 2.0)
    ) * build123d.Box(
        x_max - x_min,
        y_max - y_min,
        z_max - z_min,
        align=(build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER),
    )


def _bolt_bore(
    build123d: Any,
    *,
    diameter_mm: float,
    radius_mm: float,
    height_mm: float,
    y_range: tuple[float, float],
) -> Any:
    """接線方向（`y`）に開ける穴。`y_range` は穴の始端と終端である。"""
    y_min, y_max = y_range
    align = (build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER)
    return (
        build123d.Location((radius_mm, (y_min + y_max) / 2.0, height_mm))
        * build123d.Rotation(90, 0, 0)
        * build123d.Cylinder(diameter_mm / 2.0, y_max - y_min, align=align)
    )


def _slot_void(
    build123d: Any,
    geometry: DriveBaseGeometry,
    offset_mm: float,
    z_range: tuple[float, float],
) -> Any:
    """ブラケット取付長穴の抜き形状（半径方向に伸びる小判形）。

    ⚠️ **移動量は「長さ − 幅」である**——両端の半円が幅を、間の直線が移動量を
    与える。`slot_travel_mm == 0` のときは丸穴になり、それも設定として成立する
    （要件 3.8: 「0 を許す」）。
    """
    align = (build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER)
    z_min, z_max = z_range
    travel_mm = geometry.slot_length_mm - geometry.slot_width_mm
    void = _box_between(
        build123d,
        (
            geometry.slot_center_radius_mm - travel_mm / 2.0,
            geometry.slot_center_radius_mm + travel_mm / 2.0,
        ),
        (offset_mm - geometry.slot_width_mm / 2.0, offset_mm + geometry.slot_width_mm / 2.0),
        (z_min, z_max),
    ) if travel_mm > 0.0 else None
    for end_mm in (
        geometry.slot_center_radius_mm - travel_mm / 2.0,
        geometry.slot_center_radius_mm + travel_mm / 2.0,
    ):
        cap = build123d.Location(
            (end_mm, offset_mm, (z_min + z_max) / 2.0)
        ) * build123d.Cylinder(geometry.slot_width_mm / 2.0, z_max - z_min, align=align)
        void = cap if void is None else void + cap
    return void


def _build_motor_arm(geometry: DriveBaseGeometry) -> Any:
    """モータ取付部1本のソリッドを組み立てる（機体座標、第1輪の向き）。

    参照は**幾何セレクタ**（座標と範囲）で明示的に組み立てる。⚠️ 生成名を一切
    使わない（`_build_leg` と同じ規律）。
    """
    build123d = _require_shape_library()
    z_bottom_mm = geometry.underside_height_mm
    z_top_mm = z_bottom_mm + geometry.arm_thickness_mm
    overshoot_mm = geometry.arm_thickness_mm + geometry.lap_length_mm

    # 二股の区間（内側）と、その外側の本体。⚠️ 二股はアームの幅の内側に収まる。
    body = _box_between(
        build123d,
        (geometry.hub_radius_mm, geometry.fork_root_radius_mm),
        (-geometry.fork_half_width_mm, geometry.fork_half_width_mm),
        (z_bottom_mm, z_top_mm),
    )
    body += _box_between(
        build123d,
        (geometry.fork_root_radius_mm, geometry.arm_outer_radius_mm),
        (-geometry.arm_half_width_mm, geometry.arm_half_width_mm),
        (z_bottom_mm, z_top_mm),
    )

    # ⚠️ 溝は**機体側へ開いている**（舌を半径方向に差し込む）。厚さ方向にも
    # 貫いており、舌が板より厚くても噛み合う。
    body -= _box_between(
        build123d,
        (geometry.hub_radius_mm - overshoot_mm, geometry.fork_root_radius_mm),
        (-geometry.fork_slot_width_mm / 2.0, geometry.fork_slot_width_mm / 2.0),
        (z_bottom_mm - overshoot_mm, z_top_mm + overshoot_mm),
    )

    for radius_mm in geometry.bolt_radii_mm:
        # 貫通穴（ボルトが入る側）。⚠️ 座の当たり面はこの側の側壁の外面である。
        body -= _bolt_bore(
            build123d,
            diameter_mm=geometry.through_hole_diameter_mm,
            radius_mm=radius_mm,
            height_mm=geometry.bolt_height_mm,
            y_range=(-geometry.fork_half_width_mm - overshoot_mm, 0.0),
        )
        # 反対側の側壁のインサート座（⚠️ **袋穴**である。側壁を突き抜けない）。
        body -= _bolt_bore(
            build123d,
            diameter_mm=geometry.insert_bore_diameter_mm,
            radius_mm=radius_mm,
            height_mm=geometry.bolt_height_mm,
            y_range=(
                geometry.fork_slot_width_mm / 2.0,
                geometry.fork_slot_width_mm / 2.0 + geometry.insert_bore_depth_mm,
            ),
        )

    for offset_mm in geometry.slot_offsets_mm:
        body -= _slot_void(
            build123d,
            geometry,
            offset_mm,
            (z_bottom_mm - overshoot_mm, z_top_mm + overshoot_mm),
        )
    return body


def _build_hub_plate(geometry: DriveBaseGeometry, adapter: AdapterGeometry) -> Any:
    """中央部のソリッドを組み立てる（機体座標。3方向の舌を持つ）。

    ⚠️ **アダプタの締結の相手側もここに開ける。** `joints.derive_joints` は
    `hub_plate__adapter_segment_i` に「断片の裾を貫くボルト ＋ 中央部側の
    インサート」を記録している（`stack_thickness_mm` はアダプタの肉厚だけで
    あり、ボルトはそこを貫いて相手のインサートへ入る）。⚠️ **相手側に座が
    無ければ、記録されたインサートはどこにも入らない**——外縁に上流
    `JointPolicy.insert_length_mm` ぶんの袋穴を置く。
    """
    build123d = _require_shape_library()
    align = (build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER)
    z_bottom_mm = geometry.underside_height_mm
    z_top_mm = z_bottom_mm + geometry.plate_thickness_mm
    half_mm = geometry.tongue_thickness_mm / 2.0

    body = build123d.Location(
        (0.0, 0.0, (z_bottom_mm + z_top_mm) / 2.0)
    ) * build123d.Cylinder(
        geometry.hub_radius_mm, geometry.plate_thickness_mm, align=align
    )

    tongue = _box_between(
        build123d,
        (
            geometry.hub_radius_mm - geometry.lap_length_mm,
            geometry.hub_radius_mm + geometry.lap_length_mm,
        ),
        (-half_mm, half_mm),
        (z_bottom_mm, z_top_mm),
    )
    for radius_mm in geometry.bolt_radii_mm:
        tongue -= _bolt_bore(
            build123d,
            diameter_mm=geometry.through_hole_diameter_mm,
            radius_mm=radius_mm,
            height_mm=geometry.bolt_height_mm,
            y_range=(-half_mm - 1.0, half_mm + 1.0),
        )
    for angle_deg in geometry.wheel_angles_deg:
        body += build123d.Rotation(0, 0, angle_deg) * tongue

    # アダプタ断片の裾を留めるインサートの座（⚠️ **袋穴**。外縁から内側へ）。
    for angles_deg in adapter.mount_bolt_angles_deg:
        for angle_deg in angles_deg:
            body -= _radial_bore(
                build123d,
                angle_deg=angle_deg,
                height_mm=adapter.mount_bolt_height_mm,
                radius_range_mm=(
                    geometry.hub_radius_mm - adapter.insert_bore_depth_mm,
                    geometry.hub_radius_mm + _TOOL_OVERSHOOT_MM,
                ),
                diameter_mm=adapter.insert_bore_diameter_mm,
            )
    return body


def build_drive_base(
    params: ResolvedParams, layout: ChassisLayout
) -> tuple[BuiltPart, ...]:
    """駆動ベース（中央部1点とモータ取付部 `wheel_count` 点）を構築する。

    ⚠️ **検査が先である**（design.md `#### Shapes`）——材料と外接箱を通してから
    ソリッドを作る。⚠️ 3本のアームは**同一形状**であり、据え付けの角度だけが
    異なる（角度を形へ焼き付けない）。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        `("hub_plate", "motor_arm_1", …)` の順の構築済み部品。

    Raises:
        GeometryError: 幾何が成立しない場合、または外接箱が造形可能寸法に
            収まらない場合（⚠️ **超過を全件**示す）。
        ParameterError: 材料が上流の許可一覧に無い場合。
        CadUnavailableError: 形状ライブラリが導入されていない場合。
    """
    geometry = drive_base_geometry(params, layout)
    check_material(params.printing)

    violations = [
        violation
        for part_name, envelope in (
            (HUB_PLATE_PART_NAME, geometry.hub_plate_envelope),
            (MOTOR_ARM_PART_NAME, geometry.motor_arm_envelope),
        )
        for violation in check_envelope(part_name, envelope, params.printing)
    ]
    if violations:
        detail = "、".join(
            f"{violation.part_name} の 軸 {violation.axis} が "
            f"{violation.envelope_mm}mm で上限 {violation.limit_mm}mm を "
            f"{violation.excess_mm}mm 超過"
            for violation in violations
        )
        raise GeometryError(
            f"駆動ベースの外接箱が造形可能寸法に収まらない（{detail}）。"
            "⚠️ 中央部の外接箱は舌の張り出しを含む（要件 2.3）。"
            "中央部の外径かホイール配置半径を見直すこと。"
        )

    plate = _build_hub_plate(geometry, adapter_geometry(params, layout))
    parts = [
        BuiltPart(
            name=HUB_PLATE_PART_NAME,
            solid=plate,
            metrics=measure_part(HUB_PLATE_PART_NAME, plate),
        )
    ]
    arm = _build_motor_arm(geometry)
    parts.extend(
        BuiltPart(
            name=f"{MOTOR_ARM_PART_NAME}_{index}",
            solid=arm,
            metrics=measure_part(f"{MOTOR_ARM_PART_NAME}_{index}", arm),
        )
        for index in range(1, geometry.wheel_count + 1)
    )
    return tuple(parts)


# ---------------------------------------------------------------------------
# ゴミ箱固定アダプタ（タスク 3.3 / 要件 2.2, 6.1, 6.2, 6.5, 6.6, 6.7, 6.9, 6.10）
#
# ## ⚠️ 座ではなく、縁を掴むクランプである（決定 4b）
#
# ⚠️ **ゴミ箱の底は抜かれる。** 上流 `catch-mechanism` の決定 3（改訂版）が
# `bottom_modification = "bottom_removed"` を許可し、缶の内側は要件 7 の段積み
# 土台が使う。したがって⚠️ **底を下から支える座はもう作れない**——アダプタは、
# 切り取りで残った縁と円錐台の側壁を掴むクランプになる。
#
# 切り取り径は上流の**平面部径**を超えない（要件 6.10）。外径との差として残る
# 環（片側 `lip_width_mm`）がそのまま掴み代である。断片が中央部とアームの上に
# 載って外側へ張り出すこと、点数を上流の円環の導出（`joints.segment_counts()`）
# から採ることは変わらない（要件 2.1）。
#
# 断面（半径方向の断面。z は接地面からの高さ）:
#
#         缶の側壁 ╲          ┃╲ ← 受け面は円錐台の側面に沿う（要件 6.2）
#                   ╲         ┃ ╲  保持の締結は**側壁**を貫く（縁ではない）
#             縁 ────╂────────┫  ╲
#     ────────────╂──┸────────┫ ← 掴み面（floor_top_height_mm）
#       床 (floor) ↑切り取り径 ↑外径   ＝ 缶の底が載っていた高さ
#     ────────────┻───────────┛  ⚠️ 中央は開いている（段が通る）
#       裾 (skirt)┃               ⚠️ 中央部の外縁を掴み、半径方向のボルトで留める
#
# ## ⚠️ 受け面を円筒にしない（要件 6.2）
#
# 側壁は円錐台であり、上流 `taper_deg` の勾配で上へ広がる。⚠️ **円筒の受け面は
# 底の角だけで当たり、荷重が線に集まる。** 受け面は同じ勾配の円錐とし、隙間
# （`adapter.seat_clearance_mm`）はどの高さでも同じ量になる。
#
# ## ⚠️ 拘束の向きを取り違えない（要件 6.5, 6.10）
#
# - **水平方向**: 円錐の受け面が全周で囲う
# - **落ちる向き**: 縁の下へ入った掴み面（床の上面）が受ける。⚠️ **底が無い
#   のだから、ここが欠ければ缶は受け面が噛むまで沈む**（隙間 ÷ 勾配 ＝ 十数 mm）
# - **持ち上げる向き**: 側壁を貫く保持のボルトである（`joints` の
#   `adapter__trash_can`、`print_normal_axis == "x"`。座面（水平面）へ鉛直に
#   留めると接合面の法線が積層方向と一致する。要件 2.8）。⚠️ **テーパーを
#   くさびとして数えない**——缶が持ち上がる向きでは、同じ高さにある缶の径が
#   細くなる側であり受け面は緩む。くさびとして噛むのは沈む向きだけである
#
# ## ⚠️ 据え付けの順序（要件 6.6）
#
# 掴み面は縁の**下**にあるため、⚠️ **缶を上から落とし込んで留めることはできない。**
# 缶を置いてから断片を**半径方向に**差し込み、半径方向のボルトで留める。断片が
# 占める角度は 180 度以下であり、二等分線の向きへ動かすとき断片のどの点も軸から
# 遠ざかる（`|R e^{iθ} + d| >= R` が `|θ| <= 90` 度で成り立つ）ため、座った位置に
# 隙間があれば経路の全域に隙間がある。⚠️ **実形状での確認は
# `test_chassis_invariants.py` が持つ**——ここにあるのは論証だけである。
#
# ## ⚠️ 通過を狭めない（要件 6.7）
#
# 通過の基準は**切り取り開口**（`bottom_flat_diameter_mm`）から `taper_deg` で
# 広がる円錐である。⚠️ **底の内面ではない**——底はもう無い。アダプタは縁の下と
# 側壁の外にしか材料を持たず、この円錐の内側には1つも入らない。中央は段
# （タスク 3.4）が通れるよう開いており、⚠️ 開口より下では床が環として残るため
# 通過は裾の内径 `skirt_inner_radius_mm` に絞られる——⚠️ **段の柱はその内側
# （＝中央部の真上）から立ち上げる。** 受け口（ワイドリム）はゴミ箱の上端へ
# 被さる部品であり、クランプはそこまで登らない。
# ⚠️ **`opening_inner_diameter_mm` は内径の下限にならない**（design.md
# `#### Shapes` の不変条件がそう明記している）。外径（φ188）ですら開口（φ210）
# より小さい。
#
# ## ⚠️ 切断は不可逆である（要件 6.11）
#
# 段の寸法が確定し、造形可能性と干渉の検査を通るまで切らない。⚠️ **本モジュール
# が作るのは「切ったあと」の形であり、形状が生成できることは切断の許可では
# ない**（缶は再調達できるが、その利点は失敗1回につき一度しか使えない）。
#
# ## ⚠️ 保持の締結にインサートの座を作らない
#
# `joints` は `adapter__trash_can` を**ナットで受ける接合部**として導出する
# （`insert_count == 0`）。金属インサートは「モータ反力を樹脂へ渡す接合部で、
# 樹脂にねじを立てない」ための要素であり（要件 2.6 / A-5）、相手が購入部品で
# あるこの家族には居場所が無い——締結の軸（半径方向）に沿ってアダプタが持つ肉は
# 立ち上がりの肉厚（`wall_thickness_mm`）だけであり、上流 `insert_length_mm` に
# 足りない。⚠️ **足りない座を黙って浅く作らない**——貫通穴＋座ぐり（当たり面）
# として作り、ボルトはナットで受ける。
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AdapterGeometry:
    """ゴミ箱固定アダプタ（円環部品）の幾何。

    ⚠️ **形状オブジェクトを持たない**（`StandGeometry` / `DriveBaseGeometry` と
    同じ規律）。値はすべて**機体座標**であり、原点は機体中心、`z` は接地面
    （床）からの高さである。⚠️ **断片は据え付けの角度のまま構築する**
    ——組み上がり状態の干渉（要件 9.1）を実形状で見るためであり、
    `segment_start_angles_deg` がその角度を持つ。

    Attributes:
        segment_count: 断片の数（⚠️ `joints.segment_counts()` が正）。
        segment_span_deg: 断片1つが占める角度（度）。
        segment_start_angles_deg: 各断片の始まりの角度（度）。
        outer_radius_mm: 外周の半径（⚠️ `joints` の外径の式が正）。
        seat_bottom_radius_mm: 受け面の下端の半径（底の外半径 ＋ 隙間）。
        seat_top_radius_mm: 受け面の上端の半径。⚠️ 下端と等しくない。
        seat_slope: 受け面の勾配（＝ `tan(taper_deg)`）。
        taper_deg: 上流のテーパー角（度）。
        cut_radius_mm: 底の切り取り径の半分（＝上流の平面部径の半分。要件 6.10）。
            ⚠️ **通過の基準でもある**——ここから `seat_slope` で広がる円錐の
            内側には材料が1つも無い。
        lip_outer_radius_mm: 切り取りで残る縁の外半径（＝底の外半径）。
        lip_width_mm: 残る縁の幅（mm、片側）。⚠️ **これが掴み代である。**
        floor_bottom_height_mm: 床の下面（＝中央部とアームの上面）。
        floor_top_height_mm: 床の上面。⚠️ **縁を下から掴む面であり、
            ゴミ箱の底が載る高さである。**
        floor_thickness_mm: 床の厚さ（＝ `wall_thickness_mm`）。
        rise_height_mm: 立ち上がりの高さ（mm）。
        rise_top_height_mm: 立ち上がりの上端の高さ（mm）。
        skirt_inner_radius_mm: 裾の内側の半径（⚠️ **内側最小径**の半分）。
        skirt_outer_radius_mm: 裾の外側の半径（座ぐりが載る面）。
        skirt_bottom_height_mm: 裾の下端の高さ（＝駆動ベース下面）。
        boss_diameter_mm: ボルト座の外径（mm、⚠️ `joints` が正）。
        through_hole_diameter_mm: 貫通穴の径（mm、上流 `JointPolicy`）。
        insert_bore_diameter_mm: インサート座の下穴径（mm、上流）。
        insert_bore_depth_mm: インサート座の深さ（mm、上流のインサート長）。
        mount_bolt_count: 断片1つあたりの取付ボルト本数（⚠️ `joints` が正）。
        mount_bolt_height_mm: 取付ボルトの軸の高さ（＝中央部の厚さの中央）。
        mount_spotface_depth_mm: 取付の座ぐりの深さ（mm）。
        mount_bolt_angles_deg: 断片ごとの取付ボルトの角度（度）。
        retention_bolt_count: 保持の締結の本数（⚠️ `joints` が正）。
        retention_bolt_height_mm: 保持の締結の軸の高さ（mm）。
        retention_spotface_depth_mm: 保持の座ぐりの深さ（mm）。
        retention_bolt_angles_deg: 保持の締結の角度（度）。円周へ等配置する。
        can_height_mm: ゴミ箱の全高（mm、上流）。
        arm_angles_deg: アームの角度（度）。⚠️ 裾はこれを避ける。
        arm_void_half_width_mm: 裾がアームへ空ける逃げの半幅（mm）。
        bore_diameters_mm: ⚠️ **造形する穴の径の一覧**（要件 2.11 の検査対象）。
        segment_envelopes: 断片ごとの軸並行外接箱（⚠️ 据え付けの角度で変わる）。
    """

    segment_count: int
    segment_span_deg: float
    segment_start_angles_deg: tuple[float, ...]
    outer_radius_mm: float
    seat_bottom_radius_mm: float
    seat_top_radius_mm: float
    seat_slope: float
    taper_deg: float
    cut_radius_mm: float
    lip_outer_radius_mm: float
    lip_width_mm: float
    floor_bottom_height_mm: float
    floor_top_height_mm: float
    floor_thickness_mm: float
    rise_height_mm: float
    rise_top_height_mm: float
    skirt_inner_radius_mm: float
    skirt_outer_radius_mm: float
    skirt_bottom_height_mm: float
    boss_diameter_mm: float
    through_hole_diameter_mm: float
    insert_bore_diameter_mm: float
    insert_bore_depth_mm: float
    mount_bolt_count: int
    mount_bolt_height_mm: float
    mount_spotface_depth_mm: float
    mount_bolt_angles_deg: tuple[tuple[float, ...], ...]
    retention_bolt_count: int
    retention_bolt_height_mm: float
    retention_spotface_depth_mm: float
    retention_bolt_angles_deg: tuple[float, ...]
    can_height_mm: float
    arm_angles_deg: tuple[float, ...]
    arm_void_half_width_mm: float
    bore_diameters_mm: tuple[float, ...]
    segment_envelopes: tuple[Envelope, ...]


def _spotface_depth_mm(face_radius_mm: float, boss_diameter_mm: float) -> float:
    """円筒面へ平らな座を落とし込む深さ（mm）。

    ⚠️ **手で決めた深さを持たない。** 座の環（直径 `boss_diameter_mm`）が円筒面
    からはみ出す量（サジッタ）が「平らにするために最低限要る深さ」であり、そこへ
    造形誤差の逃げ（`_JOINT_FIT_CLEARANCE_MM`）を足す。⚠️ サジッタちょうどでは
    座の縁が円筒面へ接するだけになり、⚠️ **平面として実現しているかどうかが
    造形誤差で決まる**——それは「削って合わせる」設計である（決定 4）。

    Raises:
        GeometryError: 座の環が円筒面の半径を超える場合（面が丸ごと消える）。
    """
    boss_radius_mm = boss_diameter_mm / 2.0
    if boss_radius_mm >= face_radius_mm:
        raise GeometryError(
            f"ボルト座の半径 {boss_radius_mm!r}mm が座を落とし込む面の半径 "
            f"{face_radius_mm!r}mm 以上であり、平らな座が取れない。"
        )
    sagitta_mm = face_radius_mm - math.sqrt(face_radius_mm**2 - boss_radius_mm**2)
    return sagitta_mm + _JOINT_FIT_CLEARANCE_MM


def _sector_extent_mm(
    start_deg: float, span_deg: float, inner_radius_mm: float, outer_radius_mm: float
) -> tuple[float, float]:
    """円環を切り出した扇形の、軸並行外接箱の x/y の広がりを返す（mm）。

    ⚠️ **上流 `sector_envelope` の置き換えではない。** 上流は分割数の導出の
    ために「中心を含む扇形」という最悪値を採る（内径を受け取らない）。ここが
    返すのは**据え付けの角度のまま構築した断片の実際の広がり**であり、
    実形状の外接箱と一致しなければならない量である（`check_envelope` は
    こちらで通す。要件 2.2）。
    """
    xs: list[float] = []
    ys: list[float] = []
    for angle_deg in (start_deg, start_deg + span_deg):
        radians = math.radians(angle_deg)
        for radius_mm in (inner_radius_mm, outer_radius_mm):
            xs.append(radius_mm * math.cos(radians))
            ys.append(radius_mm * math.sin(radians))
    for quarter_deg in (0.0, 90.0, 180.0, 270.0):
        if (quarter_deg - start_deg) % 360.0 > span_deg:
            continue
        radians = math.radians(quarter_deg)
        xs.append(outer_radius_mm * math.cos(radians))
        ys.append(outer_radius_mm * math.sin(radians))
    return max(xs) - min(xs), max(ys) - min(ys)


def _free_arcs_deg(
    span_deg: float, blocked_offsets_deg: Sequence[float], blocked_half_deg: float
) -> tuple[tuple[float, float], ...]:
    """区間 `[0, span_deg]` から塞がれた弧を除いた、空きの弧を返す。"""
    blocked: list[tuple[float, float]] = []
    for offset_deg in blocked_offsets_deg:
        for shift_deg in (-360.0, 0.0, 360.0):
            low_deg = offset_deg + shift_deg - blocked_half_deg
            high_deg = offset_deg + shift_deg + blocked_half_deg
            if high_deg <= 0.0 or low_deg >= span_deg:
                continue
            blocked.append((max(low_deg, 0.0), min(high_deg, span_deg)))
    free: list[tuple[float, float]] = []
    cursor_deg = 0.0
    for low_deg, high_deg in sorted(blocked):
        if low_deg > cursor_deg:
            free.append((cursor_deg, low_deg))
        cursor_deg = max(cursor_deg, high_deg)
    if cursor_deg < span_deg:
        free.append((cursor_deg, span_deg))
    return tuple(free)


def adapter_geometry(
    params: ResolvedParams, layout: ChassisLayout
) -> AdapterGeometry:
    """上流の採寸値と幾何の導出結果からアダプタの形を決める（タスク 3.3）。

    ⚠️ **形状を構築しない。** 本関数は算術のみで完結し、形状ライブラリの無い
    環境でも**全数値と成立条件**を評価できる（`stand_geometry` /
    `drive_base_geometry` と同じ規律）。

    ⚠️ **底の外径・底の平面部径・テーパー角・底の肉厚は上流が正である**
    （要件 1.3, 6.1）。本 Spec 側が持つのは受けるための量（隙間・肉厚・
    立ち上がり・保持箇所の数）だけであり、⚠️ **上流の値を書き写さない**
    ——タスク 5.3 が底の平面部径を実測へ置き換えたとき、座は実装コードを
    変えずに追随する（要件 6.9）。

    ⚠️ **分割数と締結の本数は `joints` が正である**（要件 2.1, 2.9）。形の側で
    数え直すと、下限を上げたときに座だけが増えて断片が増えない、という
    食い違いが黙って残る。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        アダプタの幾何。

    Raises:
        GeometryError: 底を抜いた後に縁が残らない場合、座の環が立ち上がり／裾に
            載らない場合、床が縁の下へ届かない場合、座ぐりが立ち上がりを貫く
            場合、保持の締結がゴミ箱の縁の載る面より下へ来る場合、または取付の
            座がアームを避けた空きの弧へ並ばない場合。⚠️ メッセージには**項目名と値**を載せる。
        catch_mechanism.GeometryError: 円環の分割数が求まらない場合
            （⚠️ **包み直さない**）。
        catch_mechanism.ParameterError: 上流の `check_joint` が当たり面を
            拒否した場合（`derive_joints` からの伝播）。
    """
    chassis = params.chassis
    adapter = chassis.adapter
    can = params.trash_can
    joint = params.joint
    base = chassis.base

    drive_base = drive_base_geometry(params, layout)
    joints = {spec.name: spec for spec in derive_joints(layout, params)}

    segment_count = segment_counts(params)[ADAPTER_SEGMENT_PART_NAME]
    segment_span_deg = 360.0 / segment_count
    first_angle_deg = layout.wheel_angles_deg[0]
    segment_start_angles_deg = tuple(
        first_angle_deg + index * segment_span_deg for index in range(segment_count)
    )

    boss_diameter_mm = BOSS_DIAMETER_FACTOR * joint.insert_outer_diameter_mm
    seat_slope = math.tan(math.radians(can.taper_deg))
    seat_bottom_radius_mm = can.bottom_outer_diameter_mm / 2.0 + adapter.seat_clearance_mm
    seat_top_radius_mm = seat_bottom_radius_mm + adapter.rise_height_mm * seat_slope
    outer_radius_mm = seat_bottom_radius_mm + adapter.wall_thickness_mm

    floor_bottom_height_mm = (
        drive_base.underside_height_mm + drive_base.plate_thickness_mm
    )
    floor_top_height_mm = floor_bottom_height_mm + adapter.wall_thickness_mm
    rise_top_height_mm = floor_top_height_mm + adapter.rise_height_mm

    skirt_inner_radius_mm = drive_base.hub_radius_mm + _JOINT_FIT_CLEARANCE_MM
    skirt_outer_radius_mm = skirt_inner_radius_mm + adapter.wall_thickness_mm
    skirt_bottom_height_mm = drive_base.underside_height_mm

    # ⚠️ **底は平面部径まで抜かれる**（要件 6.10 / 決定 4b）。残る縁が掴み代で
    # あり、⚠️ **切り取り径を本 Spec 側の寸法パラメータとして持たない**——
    # 上限は上流の平面部径そのものである。
    cut_radius_mm = can.bottom_flat_diameter_mm / 2.0
    lip_outer_radius_mm = can.bottom_outer_diameter_mm / 2.0
    lip_width_mm = lip_outer_radius_mm - cut_radius_mm
    if lip_width_mm <= 0.0:
        raise GeometryError(
            f"底を平面部径まで抜くと縁が残らない（座面の幅 {lip_width_mm!r}mm）"
            f"——bottom_flat_diameter_mm={can.bottom_flat_diameter_mm!r} が "
            f"bottom_outer_diameter_mm={can.bottom_outer_diameter_mm!r} と"
            "等しく、⚠️ 缶の重量を受ける座面が消える（要件 6.10）。"
            "⚠️ 縁は持ち上げ方向を止めない。上方向の拘束は要件 6.5 の締結が担う。"
        )
    if cut_radius_mm <= skirt_outer_radius_mm:
        raise GeometryError(
            f"底の切り取り径の半径 {cut_radius_mm!r}mm が裾の外側 "
            f"{skirt_outer_radius_mm!r}mm 以下であり、床が縁の下へ届かない"
            f"（bottom_flat_diameter_mm={can.bottom_flat_diameter_mm!r}、"
            f"hub_outer_diameter_mm={base.hub_outer_diameter_mm!r}）。"
        )

    # ⚠️ 座の環が立ち上がり／裾に載りきることが、記録された当たり面が実形状で
    # 実現するための条件である（駆動ベースが `arm_thickness_mm` へ課したのと
    # 同じ成立条件。要件 2.5, 2.9）。
    retention_bolt_height_mm = floor_top_height_mm + adapter.rise_height_mm / 2.0
    if adapter.rise_height_mm < boss_diameter_mm:
        raise GeometryError(
            f"rise_height_mm={adapter.rise_height_mm!r} がボルト座の外径 "
            f"{boss_diameter_mm!r}mm（BOSS_DIAMETER_FACTOR="
            f"{BOSS_DIAMETER_FACTOR!r} × insert_outer_diameter_mm="
            f"{joint.insert_outer_diameter_mm!r}）を下回る。⚠️ 座の環が"
            "立ち上がりに載らず、保持の締結がゴミ箱の側面ではなく座の底を"
            "押さえることになる（要件 6.5, 2.9）。"
        )
    mount_bolt_height_mm = (
        drive_base.underside_height_mm + drive_base.plate_thickness_mm / 2.0
    )
    if base.plate_thickness_mm < boss_diameter_mm:
        raise GeometryError(
            f"plate_thickness_mm={base.plate_thickness_mm!r} がボルト座の外径 "
            f"{boss_diameter_mm!r}mm を下回る。⚠️ 裾の座の環が中央部の外縁の"
            "高さに載らないため、joints が記録する当たり面は実形状では"
            "実現しない（要件 2.9）。"
        )
    if joint.insert_length_mm >= drive_base.hub_radius_mm:
        raise GeometryError(
            f"インサート長 insert_length_mm={joint.insert_length_mm!r} が中央部の"
            f"半径 {drive_base.hub_radius_mm!r}mm 以上であり、座が中央部を"
            "貫いてしまう。"
        )

    mount_spotface_depth_mm = _spotface_depth_mm(skirt_outer_radius_mm, boss_diameter_mm)
    retention_spotface_depth_mm = _spotface_depth_mm(outer_radius_mm, boss_diameter_mm)
    wall_at_bolt_mm = outer_radius_mm - (
        seat_bottom_radius_mm
        + (retention_bolt_height_mm - floor_top_height_mm) * seat_slope
    )
    if wall_at_bolt_mm <= retention_spotface_depth_mm:
        raise GeometryError(
            f"保持の座ぐりの深さ {retention_spotface_depth_mm!r}mm が、その高さの"
            f"立ち上がりの肉 {wall_at_bolt_mm!r}mm 以上である"
            f"（wall_thickness_mm={adapter.wall_thickness_mm!r}、"
            f"taper_deg={can.taper_deg!r}）——⚠️ 座ぐりが壁を貫く。"
        )

    # 保持の締結は円周へ等配置する（要件 6.5）。⚠️ **分割の継ぎ目に置かない**
    # ——継ぎ目に掛かる座は2つの断片に割れて座として成立しない。
    retention_bolt_count = joints["adapter__trash_can"].bolt_count
    retention_half_deg = math.degrees(
        math.asin(boss_diameter_mm / 2.0 / outer_radius_mm)
    )
    retention_bolt_angles_deg = tuple(
        first_angle_deg + segment_span_deg / 2.0 + index * 360.0 / retention_bolt_count
        for index in range(retention_bolt_count)
    )
    for angle_deg in retention_bolt_angles_deg:
        for start_deg in segment_start_angles_deg:
            gap_deg = abs((angle_deg - start_deg + 180.0) % 360.0 - 180.0)
            if gap_deg < retention_half_deg:
                raise GeometryError(
                    f"保持の締結の角度 {angle_deg!r} 度が分割の継ぎ目 "
                    f"{start_deg!r} 度に掛かる（座の半角 {retention_half_deg!r} 度、"
                    f"retention_point_count={adapter.retention_point_count!r}、"
                    f"断片 {segment_count!r} 個）。"
                )

    # 取付の座はアームを避けた空きの弧へ並べる。⚠️ **裾はアームと同じ高さの帯を
    # 通る**ため、アームの角度には座も穴も置けない。
    mount_bolt_count = joints[
        f"hub_plate__adapter_segment_{_UNSPLIT_PART_COUNT}"
    ].bolt_count
    arm_void_half_width_mm = drive_base.arm_half_width_mm + _JOINT_FIT_CLEARANCE_MM
    if arm_void_half_width_mm >= skirt_inner_radius_mm:
        raise GeometryError(
            f"アームの逃げの半幅 {arm_void_half_width_mm!r}mm が裾の内側の半径 "
            f"{skirt_inner_radius_mm!r}mm 以上であり、裾が残らない"
            f"（arm_width_mm={base.arm_width_mm!r}）。"
        )
    arm_half_deg = math.degrees(
        math.asin(arm_void_half_width_mm / skirt_inner_radius_mm)
    )
    mount_half_deg = math.degrees(
        math.asin(boss_diameter_mm / 2.0 / skirt_outer_radius_mm)
    )
    mount_bolt_angles_deg: list[tuple[float, ...]] = []
    for start_deg in segment_start_angles_deg:
        offsets_deg = [
            (angle_deg - start_deg) % 360.0 for angle_deg in layout.wheel_angles_deg
        ]
        arcs = _free_arcs_deg(segment_span_deg, offsets_deg, arm_half_deg)
        widest = max(arcs, key=lambda arc: arc[1] - arc[0], default=(0.0, 0.0))
        width_deg = widest[1] - widest[0]
        required_deg = _BOTH_SIDES * mount_half_deg * mount_bolt_count
        if width_deg < required_deg:
            raise GeometryError(
                f"adapter_segment（始まり {start_deg!r} 度）: 取付ボルト "
                f"{mount_bolt_count!r} 本の座を並べるにはアームを避けた弧が "
                f"{required_deg!r} 度必要だが、空きは {width_deg!r} 度しかない"
                f"（アームの半角 {arm_half_deg!r} 度、"
                f"arm_width_mm={base.arm_width_mm!r}）。"
            )
        mount_bolt_angles_deg.append(
            tuple(
                start_deg
                + widest[0]
                + width_deg * (index + 0.5) / mount_bolt_count
                for index in range(mount_bolt_count)
            )
        )

    envelopes: list[Envelope] = []
    for start_deg in segment_start_angles_deg:
        x_mm, y_mm = _sector_extent_mm(
            start_deg, segment_span_deg, skirt_inner_radius_mm, outer_radius_mm
        )
        envelopes.append(
            Envelope(
                x_mm=x_mm,
                y_mm=y_mm,
                z_mm=rise_top_height_mm - skirt_bottom_height_mm,
            )
        )

    return AdapterGeometry(
        segment_count=segment_count,
        segment_span_deg=segment_span_deg,
        segment_start_angles_deg=segment_start_angles_deg,
        outer_radius_mm=outer_radius_mm,
        seat_bottom_radius_mm=seat_bottom_radius_mm,
        seat_top_radius_mm=seat_top_radius_mm,
        seat_slope=seat_slope,
        taper_deg=can.taper_deg,
        cut_radius_mm=cut_radius_mm,
        lip_outer_radius_mm=lip_outer_radius_mm,
        lip_width_mm=lip_width_mm,
        floor_bottom_height_mm=floor_bottom_height_mm,
        floor_top_height_mm=floor_top_height_mm,
        floor_thickness_mm=adapter.wall_thickness_mm,
        rise_height_mm=adapter.rise_height_mm,
        rise_top_height_mm=rise_top_height_mm,
        skirt_inner_radius_mm=skirt_inner_radius_mm,
        skirt_outer_radius_mm=skirt_outer_radius_mm,
        skirt_bottom_height_mm=skirt_bottom_height_mm,
        boss_diameter_mm=boss_diameter_mm,
        through_hole_diameter_mm=joint.through_hole_diameter_mm,
        insert_bore_diameter_mm=joint.insert_outer_diameter_mm,
        insert_bore_depth_mm=joint.insert_length_mm,
        mount_bolt_count=mount_bolt_count,
        mount_bolt_height_mm=mount_bolt_height_mm,
        mount_spotface_depth_mm=mount_spotface_depth_mm,
        mount_bolt_angles_deg=tuple(mount_bolt_angles_deg),
        retention_bolt_count=retention_bolt_count,
        retention_bolt_height_mm=retention_bolt_height_mm,
        retention_spotface_depth_mm=retention_spotface_depth_mm,
        retention_bolt_angles_deg=retention_bolt_angles_deg,
        can_height_mm=can.height_mm,
        arm_angles_deg=layout.wheel_angles_deg,
        arm_void_half_width_mm=arm_void_half_width_mm,
        # ⚠️ 座ぐりは相手部品と嵌まる穴ではないが、造形する穴として一覧に出す
        # （要件 2.11 の検査対象は「造形する穴」である）。
        bore_diameters_mm=tuple(
            sorted(
                {
                    joint.through_hole_diameter_mm,
                    joint.insert_outer_diameter_mm,
                    boss_diameter_mm,
                }
            )
        ),
        segment_envelopes=tuple(envelopes),
    )


def _radial_bore(
    build123d: Any,
    *,
    angle_deg: float,
    height_mm: float,
    radius_range_mm: tuple[float, float],
    diameter_mm: float,
) -> Any:
    """半径方向（角度 `angle_deg`）に開ける穴。範囲は機体中心からの半径である。"""
    near_mm, far_mm = radius_range_mm
    align = (build123d.Align.CENTER, build123d.Align.CENTER, build123d.Align.CENTER)
    return (
        build123d.Rotation(0, 0, angle_deg)
        * build123d.Location(((near_mm + far_mm) / 2.0, 0.0, height_mm))
        * build123d.Rotation(0, 90, 0)
        * build123d.Cylinder(diameter_mm / 2.0, abs(far_mm - near_mm), align=align)
    )


def _build_adapter_segment(geometry: AdapterGeometry, index: int) -> Any:
    """アダプタ断片1つのソリッドを組み立てる（機体座標、据え付けの角度のまま）。

    参照は**幾何セレクタ**（座標と範囲）で明示的に組み立てる。⚠️ 生成名を一切
    使わない（`_build_leg` / `_build_motor_arm` と同じ規律）。

    ⚠️ **足す形だけが扇形であり、抜く形はすべて全周である。** 扇形どうしの
    ブール演算は側面が同一平面で重なり、結果が演算の順序に左右される。
    """
    build123d = _require_shape_library()
    start_deg = geometry.segment_start_angles_deg[index]
    overshoot_mm = _TOOL_OVERSHOOT_MM

    def sector(radius_mm: float, z_range: tuple[float, float]) -> Any:
        """据え付けの角度に置いた扇形。

        ⚠️ `align=None` は軸を機体中心に置き、**下端を原点に置く**（中央では
        ない）。位置は `z_range[0]` である。
        """
        z_min, z_max = z_range
        return (
            build123d.Rotation(0, 0, start_deg)
            * build123d.Location((0.0, 0.0, z_min))
            * build123d.Cylinder(
                radius_mm,
                z_max - z_min,
                arc_size=geometry.segment_span_deg,
                align=None,
            )
        )

    def ring_tool(radius_mm: float, z_range: tuple[float, float]) -> Any:
        """全周の円筒（抜く側にだけ使う）。"""
        z_min, z_max = z_range
        return build123d.Location((0.0, 0.0, z_min)) * build123d.Cylinder(
            radius_mm, z_max - z_min, align=None
        )

    # 床（⚠️ **上面が縁を下から掴む面である**）と、その内側を抜いた環。
    # ⚠️ 中央を抜くのは段が通る道を残すためでもある（要件 6.7 / 7.10）。
    body = sector(
        geometry.outer_radius_mm,
        (geometry.floor_bottom_height_mm, geometry.floor_top_height_mm),
    ) - ring_tool(
        geometry.skirt_inner_radius_mm,
        (
            geometry.floor_bottom_height_mm - overshoot_mm,
            geometry.floor_top_height_mm + overshoot_mm,
        ),
    )

    # 裾（中央部の外縁を掴む）。⚠️ アームと同じ高さの帯を通る。
    body += sector(
        geometry.skirt_outer_radius_mm,
        (geometry.skirt_bottom_height_mm, geometry.floor_bottom_height_mm),
    ) - ring_tool(
        geometry.skirt_inner_radius_mm,
        (
            geometry.skirt_bottom_height_mm - overshoot_mm,
            geometry.floor_bottom_height_mm + overshoot_mm,
        ),
    )

    # 立ち上がり。⚠️ **受け面は円錐台の側面に沿う**（要件 6.2）——抜く形は
    # 円筒ではなく、上流のテーパー角と同じ勾配の円錐である。
    # ⚠️ **テーパー 0（円筒形のゴミ箱）だけは円筒で抜く。** 上流
    # `TrashCanMeasurements` はテーパー 0 を許しており（円筒形のゴミ箱を
    # 排除しないため）、そのとき円錐の上下の径は等しくなる——形状ライブラリは
    # その入力を拒む。⚠️ **円筒を既定にしない**（要件 6.2 が禁じているのは
    # 「円錐台の底に円筒の座を当てること」である）。
    seat_height_mm = geometry.rise_top_height_mm - geometry.floor_top_height_mm + overshoot_mm
    seat_range_mm = (
        geometry.floor_top_height_mm,
        geometry.floor_top_height_mm + seat_height_mm,
    )
    seat_tool = (
        ring_tool(geometry.seat_bottom_radius_mm, seat_range_mm)
        if geometry.seat_slope == 0.0
        else build123d.Location((0.0, 0.0, geometry.floor_top_height_mm))
        * build123d.Cone(
            geometry.seat_bottom_radius_mm,
            geometry.seat_bottom_radius_mm + seat_height_mm * geometry.seat_slope,
            seat_height_mm,
            align=None,
        )
    )
    body += (
        sector(
            geometry.outer_radius_mm,
            (geometry.floor_top_height_mm, geometry.rise_top_height_mm),
        )
        - seat_tool
    )

    # ⚠️ **床の上面には逃げを作らない**（決定 4b）。底が残っていた頃は角の丸みを
    # 逃がす環を落としていたが、⚠️ **底を抜いた後はそこが縁そのものである**
    # ——落とせば掴み面が縁の帯から消え、缶は受け面が噛むまで沈む。

    # アームの逃げ（⚠️ 裾だけを削る。床はアームの上に載る）。
    for angle_deg in geometry.arm_angles_deg:
        body -= build123d.Rotation(0, 0, angle_deg) * _box_between(
            build123d,
            (0.0, geometry.skirt_outer_radius_mm + overshoot_mm),
            (-geometry.arm_void_half_width_mm, geometry.arm_void_half_width_mm),
            (
                geometry.skirt_bottom_height_mm - overshoot_mm,
                geometry.floor_bottom_height_mm,
            ),
        )

    # 取付の座ぐりと貫通穴（⚠️ 座ぐりが無ければ、ボルト頭は円筒面へ線で当たる）。
    for angle_deg in geometry.mount_bolt_angles_deg[index]:
        body -= _radial_bore(
            build123d,
            angle_deg=angle_deg,
            height_mm=geometry.mount_bolt_height_mm,
            radius_range_mm=(
                geometry.skirt_outer_radius_mm - geometry.mount_spotface_depth_mm,
                geometry.skirt_outer_radius_mm + overshoot_mm,
            ),
            diameter_mm=geometry.boss_diameter_mm,
        )
        body -= _radial_bore(
            build123d,
            angle_deg=angle_deg,
            height_mm=geometry.mount_bolt_height_mm,
            radius_range_mm=(
                geometry.skirt_inner_radius_mm - overshoot_mm,
                geometry.skirt_outer_radius_mm + overshoot_mm,
            ),
            diameter_mm=geometry.through_hole_diameter_mm,
        )

    # 保持の締結（⚠️ **貫通穴である**。ボルトはゴミ箱のテーパー面へ届く）。
    for angle_deg in geometry.retention_bolt_angles_deg:
        if (angle_deg - start_deg) % 360.0 > geometry.segment_span_deg:
            continue
        body -= _radial_bore(
            build123d,
            angle_deg=angle_deg,
            height_mm=geometry.retention_bolt_height_mm,
            radius_range_mm=(
                geometry.outer_radius_mm - geometry.retention_spotface_depth_mm,
                geometry.outer_radius_mm + overshoot_mm,
            ),
            diameter_mm=geometry.boss_diameter_mm,
        )
        body -= _radial_bore(
            build123d,
            angle_deg=angle_deg,
            height_mm=geometry.retention_bolt_height_mm,
            radius_range_mm=(
                geometry.seat_bottom_radius_mm - overshoot_mm,
                geometry.outer_radius_mm + overshoot_mm,
            ),
            diameter_mm=geometry.through_hole_diameter_mm,
        )
    return body


def build_adapter_segments(
    params: ResolvedParams, layout: ChassisLayout
) -> tuple[BuiltPart, ...]:
    """ゴミ箱固定アダプタの断片を全点構築する（要件 2.2, 6.1, 6.2, 6.5, 6.7）。

    ⚠️ **検査が先である**（design.md `#### Shapes`）——材料と外接箱を通してから
    ソリッドを作る。⚠️ **断片は据え付けの角度のまま構築する**（組み上がり状態の
    干渉を実形状で見るため）。

    Args:
        params: `config.load_params()` の戻り値。
        layout: `layout.derive_layout()` の戻り値。

    Returns:
        `("adapter_segment_1", …)` の順の構築済み部品。

    Raises:
        GeometryError: 幾何が成立しない場合、または外接箱が造形可能寸法に
            収まらない場合（⚠️ **超過を全件**示す）。
        ParameterError: 材料が上流の許可一覧に無い場合。
        CadUnavailableError: 形状ライブラリが導入されていない場合。
    """
    geometry = adapter_geometry(params, layout)
    check_material(params.printing)

    violations = [
        violation
        for index, envelope in enumerate(geometry.segment_envelopes, start=1)
        for violation in check_envelope(
            f"{ADAPTER_SEGMENT_PART_NAME}_{index}", envelope, params.printing
        )
    ]
    if violations:
        detail = "、".join(
            f"{violation.part_name} の 軸 {violation.axis} が "
            f"{violation.envelope_mm}mm で上限 {violation.limit_mm}mm を "
            f"{violation.excess_mm}mm 超過"
            for violation in violations
        )
        raise GeometryError(
            f"アダプタ断片の外接箱が造形可能寸法に収まらない（{detail}）。"
            "⚠️ 分割数は上流の円環の導出（segment_counts）が決めており、"
            "そこは中心を含む扇形という最悪値で判定している（要件 2.3）。"
            "ゴミ箱の底の外径か座の肉厚を見直すこと。"
        )

    return tuple(
        BuiltPart(
            name=f"{ADAPTER_SEGMENT_PART_NAME}_{index + 1}",
            solid=(solid := _build_adapter_segment(geometry, index)),
            metrics=measure_part(f"{ADAPTER_SEGMENT_PART_NAME}_{index + 1}", solid),
        )
        for index in range(geometry.segment_count)
    )
