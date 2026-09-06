"""本 Spec 固有の寸法パラメータの不変表現と構築時検証（design.md `#### Params` /
要件 1.1, 1.2, 1.9, 3.7, 4.3, 7.8, 8.1, 8.2）。

ブラケット・モータ・ホイール・ハブ・ベース・隙間下限・アダプタ・バッテリ・基板・
電源・スタンド・接合下限の**12群**を、すべて `frozen=True, slots=True` の
データクラスとして定義し、`ChassisParams` を**唯一の集約ルート**として束ねる。
値等価であり、設定ファイルの識別子（`config.parameters_digest`、タスク 1.4）を
値から計算できる。

⚠️ **上流 `catch_mechanism` が持つ値をここへ書かない**（要件 1.3 /
design.md `#### Params` Risks）。造形可能寸法・許可材料・継手方針・ゴミ箱の採寸値は
上流の公開 API から**参照して用いる**ものであり、本モジュールのどの群にも現れない。
同じ値が2箇所にあれば、いつか食い違う。この禁止は `PARAMETER_PATHS` に上流の
コンポーネント名（`trash_can` / `target_object` / `printing` / `joint` / `rim` /
`retention`）が現れないことで機械的に担保される（design.md「静的検査」）。

**実物の寸法に既定値を与えない。** 12群の全フィールドは dataclass の必須フィールド
であり、省略した構築は `TypeError` として失敗する（上流 `catch_mechanism.params` と
同じ扱い）。既定値があると、設定ファイルに書き忘れた項目が「もっともらしい数」で
黙って埋まり、未実測の値が実測のふりをする。⚠️ 未決の項目は既定値ではなく
`None`（`PowerParams`）で表す——「まだ決めていない」は「0 である」とは違う。

出所（`Provenance`）は**上流の型をそのまま使う**（design.md `#### Params`
Responsibilities: 「⚠️ 独自に定義しない」）。⚠️ **`catch_mechanism.params` を直接
import しない**——下流が参照してよい入口は上流の `__init__` だけである
（design.md「Allowed Dependencies」）。導出量は独自の出所を持たず、
`weakest_provenance` で**入力の最弱を継承する**（要件 1.9: 1つでも仮値を含めば仮値）。

`PARAMETER_PATHS` は `ChassisParams` のデータクラス木を `dataclasses.fields()` で
走査してモジュールのロード時に一度だけ生成し、**手書きの表を二重管理しない**
（tasks.md タスク 1.3）。手書きであれば、フィールドを1つ増やしたときに表から黙って
漏れ、その項目だけ出所を持たないまま設計へ流れる。単位はフィールド名の接尾辞から
導く（`_SUFFIX_UNITS` 参照）。

検証は各データクラスの `__post_init__` に置き、**違反項目名と値**を含むメッセージで
`chassis_mechanism.errors.ParameterError` を送出する（要件 1.4 / design.md
`#### Params` Responsibilities）。⚠️ **上流の `ParameterError` ではない**——
綴りは同じでも型が違うのは、直すべき設定ファイルが違うからである
（`errors.py` の docstring 参照）。

⚠️ **本モジュールはファイルを読まない。** 設定ファイルの読み書きは `config`
（タスク 1.4）の責務である。この分離があるため、`joint_local.min_bearing_area_mm2`
が上流の下限以上であることの検査は構築時には行えない（上流の下限は上流の設定
ファイルを読まなければ分からない）。したがってこの1点だけは
`validate_against_upstream(joint_policy)` という**明示的な検査**として持ち、上流の
`JointPolicy` を読み込んだ `config` がこれを呼ぶ。⚠️ **黙って省略してよい検査では
ない**（要件 2.9 / design.md `#### Params` Implementation Notes）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType, UnionType
from typing import Union, get_args, get_origin, get_type_hints

from catch_mechanism import JointPolicy, Provenance

from chassis_mechanism.errors import ParameterError

__all__ = [
    "BracketMeasurements",
    "MotorSpec",
    "WheelSpec",
    "HubSpec",
    "BaseSpec",
    "ClearanceLimits",
    "AdapterSpec",
    "BatterySpec",
    "BoardSpec",
    "PowerParams",
    "StandSpec",
    "LocalJointLimits",
    "MassItem",
    "ChassisParams",
    "ParameterPath",
    "PARAMETER_PATHS",
    "weakest_provenance",
]


# ---------------------------------------------------------------------------
# 構築時検証の共通部品
#
# ⚠️ すべてのメッセージが**項目名と値**を持つ（要件 1.4 / tasks.md タスク 1.3 の
# 観測可能な完了状態）。「設定が不正」とだけ言われても、どの項目をどう直せば
# よいかが分からなければ現物にも設定ファイルにも戻れない。
# ---------------------------------------------------------------------------


def _require_number(value: object, name: str) -> float:
    """`value` が数値（`bool` を除く）であることを検証し、`float` として返す。

    ⚠️ `bool` を弾くのは、`bool` が `int` の派生であるため JSON の `true` が
    「1mm」として黙って通ってしまうからである。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(f"{name}={value!r} は数値でなければならない。")
    return float(value)


def _require_positive_finite(value: object, name: str) -> None:
    """`value` が正の有限値であることを検証する（design.md Preconditions）。

    長さ・直径・質量・面積はいずれも 0 を含まない。0 の厚みや 0 の直径は形状として
    成立せず、設定ファイルの書き忘れ（未入力の 0）を通してしまう。
    """
    number = _require_number(value, name)
    if not (math.isfinite(number) and number > 0.0):
        raise ParameterError(f"{name}={value!r} は正の有限値でなければならない。")


def _require_nonneg_finite(value: object, name: str) -> None:
    """`value` が 0 以上の有限値であることを検証する。

    0 を許すのは、「余裕を取らない」「差を吸収しない」が設定として意味を持つ項目
    （隙間・長穴の移動量など）に限る。
    """
    number = _require_number(value, name)
    if not (math.isfinite(number) and number >= 0.0):
        raise ParameterError(f"{name}={value!r} は 0 以上の有限値でなければならない。")


def _require_angle_deg(value: object, name: str) -> None:
    """`value` が `-360 < x < 360` の有限値であることを検証する。

    design.md `#### Params` Preconditions が定める角度の値域である。取付角は
    向きを持つため負を許し、一周を超える値は同じ向きの別表現になってしまうため
    許さない（`361deg` と `1deg` が両方書ける設定は差分が読めない）。
    """
    number = _require_number(value, name)
    if not (math.isfinite(number) and -360.0 < number < 360.0):
        raise ParameterError(
            f"{name}={value!r} は -360 より大きく 360 より小さい有限値でなければならない。"
        )


def _require_nonblank_str(value: object, name: str) -> None:
    """`value` が空白のみでない文字列であることを検証する。

    ⚠️ 空白だけの記述を通さないのは、「書いたが中身が無い」記述が「書いた」として
    残ってしまうからである。
    """
    if not isinstance(value, str) or not value.strip():
        raise ParameterError(
            f"{name}={value!r} は空白のみでない文字列でなければならない。"
        )


def _require_count(value: object, name: str, minimum: int) -> None:
    """`value` が `minimum` 以上の整数であることを検証する。

    ⚠️ `bool` は `int` の派生であるため明示的に除く。JSON の `true` が
    「1個」として黙って通ることを防ぐ。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ParameterError(
            f"{name}={value!r} は {minimum} 以上の整数でなければならない。"
        )


def _require_bool_or_none(value: object, name: str) -> None:
    """`value` が `bool` か `None`（未決）であることを検証する。"""
    if value is not None and not isinstance(value, bool):
        raise ParameterError(
            f"{name}={value!r} は真偽値、または未決を表す None でなければならない。"
        )


def _require_positive_or_none(value: object, name: str) -> None:
    """`value` が正の有限値か `None`（未決）であることを検証する。"""
    if value is not None:
        _require_positive_finite(value, name)


def _require_nonblank_str_or_none(value: object, name: str) -> None:
    """`value` が空白のみでない文字列か `None`（未決）であることを検証する。"""
    if value is not None:
        _require_nonblank_str(value, name)


def _require_le(
    smaller: float, smaller_name: str, larger: float, larger_name: str, reason: str
) -> None:
    """`smaller <= larger` を検証する（形が成立するための大小関係）。"""
    if smaller > larger:
        raise ParameterError(
            f"{smaller_name}={smaller!r} は {larger_name}={larger!r} 以下で"
            f"なければならない（{reason}）。"
        )


def _require_lt(
    smaller: float, smaller_name: str, larger: float, larger_name: str, reason: str
) -> None:
    """`smaller < larger` を検証する（形が成立するための大小関係）。"""
    if smaller >= larger:
        raise ParameterError(
            f"{smaller_name}={smaller!r} は {larger_name}={larger!r} より小さく"
            f"なければならない（{reason}）。"
        )


def weakest_provenance(*values: Provenance) -> Provenance:
    """導出量の出所として、入力の最も弱いものを返す（要件 1.9）。

    半順序は `MEASURED` > `ASSUMED`。1つでも `ASSUMED` を含めば結果は `ASSUMED`
    であり、すべてが `MEASURED` のときに限り `MEASURED` を返す。⚠️ **未実測の推定が
    実測を名乗って設計判断へ紛れ込むことを防ぐ**のがこの規則の目的である。

    順序の定義そのものは上流の `Provenance.weakest` が持ち、本関数はそれを
    **再実装しない**（同じ規則が2箇所にあれば、いつか食い違う）。本関数が足すのは、
    呼び違え——空の入力・`Provenance` 以外の値——を**本 Spec の**
    `ParameterError` として拒否することだけである。上流の失敗と本 Spec の呼び違えを
    同じ型にすると、どちらの設定を直せばよいかがメッセージから消える
    （`errors.py` docstring）。

    `layout`（タスク 2.1）の導出値の出所はすべてこの関数を通る。

    Args:
        *values: 導出に用いた入力それぞれの出所。1つ以上必要である。

    Returns:
        入力の最弱の出所。

    Raises:
        ParameterError: 入力が空、または `Provenance` 以外を含む場合。
            ⚠️ 空を `MEASURED` と解釈すると、入力を1つも持たない値が実測を
            名乗ってしまう。「入力が無い」ことは「測った」ことではない。
    """
    if not values:
        raise ParameterError(
            "weakest_provenance には1つ以上の出所が必要である"
            "（入力が無ければ導出量の出所は定まらない）。"
        )
    for value in values:
        if not isinstance(value, Provenance):
            raise ParameterError(
                f"weakest_provenance の入力 {value!r} は Provenance でなければならない"
                f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
            )
    return Provenance.weakest(*values)


# ---------------------------------------------------------------------------
# 12群
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BracketMeasurements:
    """モータ付属の金属ブラケットの実測値（要件 1.6, 3.7, 4.1）。

    ⚠️ **値だけでなく「どの面のどの点からどの方向へ測ったか」を必須で持つ**
    （`mount_face_reference`）。これは型の飾りではなく、失敗の記録である——
    別途 105.6mm という測定値があったが、**基準面が不明であったため破棄した**。
    値だけを残しても、次に測る人が同じ量を測り直せない。

    Attributes:
        outline_x_mm: ブラケット外形の X 方向寸法（mm）。
        outline_y_mm: ブラケット外形の Y 方向寸法（mm）。
        mount_face_to_contact_mm: 取付面から接地点までの鉛直距離（mm）。
            駆動ベース下面高さの導出（要件 4.1）と床との隙間の起点になる。
        mount_face_to_wheel_center_mm: 取付面からホイール中心までの軸方向距離
            （mm）。⚠️ ホイール配置半径の式の第2項である（要件 3.3）。
            ⚠️ **基準面の確認が済むまで、この値から導いた量の出所は仮値である**
            （design.md `#### Layout` Risks）。
        mount_face_reference: 基準面の定義（人が読む記述）。⚠️ **必須。空白のみは
            拒否する。** 「どの面のどの点から、どの向きへ測ったか」を書く。
        mount_hole_count: 取付穴の数。1 以上。
        mount_hole_diameter_mm: 取付穴の径（mm）。
        mount_hole_pitch_mm: 取付穴のピッチ（mm）。

    Raises:
        ParameterError: 基準面の記述が空白のみの場合、長さが正の有限値でない場合、
            または取付穴の数が 1 以上の整数でない場合。
    """

    outline_x_mm: float
    outline_y_mm: float
    mount_face_to_contact_mm: float
    mount_face_to_wheel_center_mm: float
    mount_face_reference: str
    mount_hole_count: int
    mount_hole_diameter_mm: float
    mount_hole_pitch_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.outline_x_mm, "outline_x_mm")
        _require_positive_finite(self.outline_y_mm, "outline_y_mm")
        _require_positive_finite(self.mount_face_to_contact_mm, "mount_face_to_contact_mm")
        _require_positive_finite(
            self.mount_face_to_wheel_center_mm, "mount_face_to_wheel_center_mm"
        )
        _require_nonblank_str(self.mount_face_reference, "mount_face_reference")
        _require_count(self.mount_hole_count, "mount_hole_count", 1)
        _require_positive_finite(self.mount_hole_diameter_mm, "mount_hole_diameter_mm")
        _require_positive_finite(self.mount_hole_pitch_mm, "mount_hole_pitch_mm")


@dataclass(frozen=True, slots=True)
class MotorSpec:
    """ギヤードモータの外形（要件 1.1, 3.7, 4.2）。

    ⚠️ **造形部品でモータ本体を直接クランプしない**（要件 3.7）。本群の値は
    干渉と床との隙間の算出に用いるものであり、保持のための寸法ではない。

    Attributes:
        body_diameter_mm: 胴体の外径（mm）。床との隙間の一覧で最も低い部位
            （`motor_body`）の高さを決める。
        body_length_mm: 胴体の全長（mm）。
        shaft_diameter_mm: 出力シャフトの径（mm）。ハブの内径と対で成立する。
        shaft_length_mm: 出力シャフトの長さ（mm）。
        shaft_flat_present: シャフトに平面部（D カット）があるか。止めネジの
            当たり方が変わるため、有無を値として持つ。

    Raises:
        ParameterError: 長さが正の有限値でない場合、平面部の有無が真偽値でない
            場合、またはシャフト径が胴体径以上の場合。
    """

    body_diameter_mm: float
    body_length_mm: float
    shaft_diameter_mm: float
    shaft_length_mm: float
    shaft_flat_present: bool

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.body_diameter_mm, "body_diameter_mm")
        _require_positive_finite(self.body_length_mm, "body_length_mm")
        _require_positive_finite(self.shaft_diameter_mm, "shaft_diameter_mm")
        _require_positive_finite(self.shaft_length_mm, "shaft_length_mm")
        if not isinstance(self.shaft_flat_present, bool):
            raise ParameterError(
                f"shaft_flat_present={self.shaft_flat_present!r} は真偽値でなければならない。"
            )
        _require_lt(
            self.shaft_diameter_mm,
            "shaft_diameter_mm",
            self.body_diameter_mm,
            "body_diameter_mm",
            "出力シャフトは胴体の内側から出る",
        )


@dataclass(frozen=True, slots=True)
class WheelSpec:
    """オムニホイールの公称寸法（要件 1.8, 1.9, 7.8）。

    ⚠️ **公称値である。** 実効転がり径は組立後の観測（`measurements.json`、
    タスク 4.x）が持ち、実測が無い間は `nominal_diameter_mm / 2` を用いる。
    どちらを使ったかは出所に現れる（要件 1.9）。

    Attributes:
        nominal_diameter_mm: 公称外径（mm）。
        width_mm: 幅（mm）。整備スタンドの脚がホイールを挟む幅の入力になる。
        center_bore_diameter_mm: 中心穴の径（mm）。
        bolt_circle_diameter_mm: 取付穴のボルト円径（mm）。⚠️ ハブのボルト円との
            対応は**現物で確認**し、結果は観測側に記録する（要件 1.8）。
        mount_hole_count: 取付穴の数。1 以上。
        mass_g: 質量（g）。

    Raises:
        ParameterError: 寸法・質量が正の有限値でない場合、取付穴の数が 1 以上の
            整数でない場合、または `center_bore < bolt_circle < nominal` の
            大小関係が崩れる場合。
    """

    nominal_diameter_mm: float
    width_mm: float
    center_bore_diameter_mm: float
    bolt_circle_diameter_mm: float
    mount_hole_count: int
    mass_g: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.nominal_diameter_mm, "nominal_diameter_mm")
        _require_positive_finite(self.width_mm, "width_mm")
        _require_positive_finite(self.center_bore_diameter_mm, "center_bore_diameter_mm")
        _require_positive_finite(
            self.bolt_circle_diameter_mm, "bolt_circle_diameter_mm"
        )
        _require_count(self.mount_hole_count, "mount_hole_count", 1)
        _require_positive_finite(self.mass_g, "mass_g")
        _require_lt(
            self.bolt_circle_diameter_mm,
            "bolt_circle_diameter_mm",
            self.nominal_diameter_mm,
            "nominal_diameter_mm",
            "ボルト円はホイールの外形の内側にある",
        )
        _require_lt(
            self.center_bore_diameter_mm,
            "center_bore_diameter_mm",
            self.bolt_circle_diameter_mm,
            "bolt_circle_diameter_mm",
            "中心穴はボルト円の内側にある",
        )


@dataclass(frozen=True, slots=True)
class HubSpec:
    """シャフトとホイールをつなぐハブの寸法（要件 1.7, 7.8）。

    軸方向スタック（ギヤボックス端面 → ホイール内側面 → ホイール中心面）の導出に
    用いる。⚠️ **メーカー資料からの導出値と実測が食い違う場合は実測を正とする**
    （要件 1.7）。その判断は観測側（タスク 4.x）が行い、本群は入力の側を持つ。

    Attributes:
        bore_diameter_mm: 内径（mm）。モータのシャフト径と対で成立する。
        boss_diameter_mm: ボス部の外径（mm）。
        boss_length_mm: ボス部の長さ（mm）。
        flange_diameter_mm: フランジの外径（mm）。
        flange_thickness_mm: フランジの厚さ（mm）。
        overall_length_mm: 全長（mm）。軸方向スタックの1項である。
        set_screw_designation: 止めネジの呼び（例: `"M4"`）。空白のみは許さない。
        mass_g: 質量（g）。

    Raises:
        ParameterError: 寸法・質量が正の有限値でない場合、呼びが空白のみの場合、
            または `bore < boss <= flange` / `flange_thickness <= overall` の
            大小関係が崩れる場合。
    """

    bore_diameter_mm: float
    boss_diameter_mm: float
    boss_length_mm: float
    flange_diameter_mm: float
    flange_thickness_mm: float
    overall_length_mm: float
    set_screw_designation: str
    mass_g: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.bore_diameter_mm, "bore_diameter_mm")
        _require_positive_finite(self.boss_diameter_mm, "boss_diameter_mm")
        _require_positive_finite(self.boss_length_mm, "boss_length_mm")
        _require_positive_finite(self.flange_diameter_mm, "flange_diameter_mm")
        _require_positive_finite(self.flange_thickness_mm, "flange_thickness_mm")
        _require_positive_finite(self.overall_length_mm, "overall_length_mm")
        _require_nonblank_str(self.set_screw_designation, "set_screw_designation")
        _require_positive_finite(self.mass_g, "mass_g")
        _require_lt(
            self.bore_diameter_mm,
            "bore_diameter_mm",
            self.boss_diameter_mm,
            "boss_diameter_mm",
            "内径はボスの肉の内側にある",
        )
        _require_le(
            self.boss_diameter_mm,
            "boss_diameter_mm",
            self.flange_diameter_mm,
            "flange_diameter_mm",
            "フランジはボスより細くならない",
        )
        _require_le(
            self.flange_thickness_mm,
            "flange_thickness_mm",
            self.overall_length_mm,
            "overall_length_mm",
            "全長はフランジの厚さを含む",
        )


@dataclass(frozen=True, slots=True)
class BaseSpec:
    """駆動ベース（中央部＋放射状のモータ取付部）の寸法（要件 3.1, 3.2, 3.8）。

    Attributes:
        wheel_count: 輪の数。⚠️ **3 以上**（3輪オムニが前提。design.md
            `#### Layout` Preconditions）。分割数もこの数から従属する。
        hub_outer_diameter_mm: 中央部の外径（mm）。⚠️ `HubSpec`（シャフト側の
            ハブ）とは別物であり、こちらは駆動ベースの中央部である。
        plate_thickness_mm: ベース板の厚さ（mm）。
        arm_width_mm: 放射状アームの幅（mm）。
        arm_thickness_mm: 放射状アームの厚さ（mm）。
        slot_travel_mm: ブラケット取付穴の長穴が吸収できる移動量（mm）。要件 3.8
            が寸法パラメータとしての保持を求める値である。⚠️ **0 を許す**——
            「差を吸収しない」は設定として意味を持つ。切削で合わせる前提を置か
            ない代わりに、寸法差はこの量と隙間で吸収する（決定 4）。
        first_wheel_angle_deg: 第1輪の取付角（度）。基準は**機体 +x から反時計
            回り**。⚠️ **輪番号と角度の規約をここで定義し直さない**（要件 3.6）。

    Raises:
        ParameterError: 輪の数が 3 以上の整数でない場合、寸法が正の有限値でない
            場合、長穴の移動量が負または非有限の場合、または取付角が値域を外れる
            場合。
    """

    wheel_count: int
    hub_outer_diameter_mm: float
    plate_thickness_mm: float
    arm_width_mm: float
    arm_thickness_mm: float
    slot_travel_mm: float
    first_wheel_angle_deg: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_count(self.wheel_count, "wheel_count", 3)
        _require_positive_finite(self.hub_outer_diameter_mm, "hub_outer_diameter_mm")
        _require_positive_finite(self.plate_thickness_mm, "plate_thickness_mm")
        _require_positive_finite(self.arm_width_mm, "arm_width_mm")
        _require_positive_finite(self.arm_thickness_mm, "arm_thickness_mm")
        _require_nonneg_finite(self.slot_travel_mm, "slot_travel_mm")
        _require_angle_deg(self.first_wheel_angle_deg, "first_wheel_angle_deg")


@dataclass(frozen=True, slots=True)
class ClearanceLimits:
    """床との隙間についての下限と設計量（要件 4.2, 4.3, 4.6）。

    ⚠️ **下限値は寸法パラメータとして保持する**（要件 4.3）。根拠——使用環境が
    屋内の平坦床であること——は design.md 側が持ち、値はここが持つ。
    コードに埋め込むと、床が変わったときに検査の実装を書き換えることになる。

    Attributes:
        min_ground_clearance_mm: 床との隙間の下限（mm）。5部位すべてに課される。
        cable_lowest_offset_mm: 配線の最下点の、ベース下面からのオフセット（mm）。
            ⚠️ 下向きが正である（配線は下へ垂れる）。0 は「垂れない」を意味し、
            設定として成立する。
        fastener_protrusion_mm: 締結部品（ボルト頭・ナット）の下方への突出量
            （mm）。0 は皿頭などで突出が無い状態を表す。

    Raises:
        ParameterError: 下限が正の有限値でない場合、またはオフセット・突出量が
            負もしくは非有限の場合。
    """

    min_ground_clearance_mm: float
    cable_lowest_offset_mm: float
    fastener_protrusion_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(
            self.min_ground_clearance_mm, "min_ground_clearance_mm"
        )
        _require_nonneg_finite(self.cable_lowest_offset_mm, "cable_lowest_offset_mm")
        _require_nonneg_finite(self.fastener_protrusion_mm, "fastener_protrusion_mm")


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """ゴミ箱固定アダプタ（円錐台を受ける座）の寸法（要件 6.x）。

    ⚠️ **底の径とテーパー角は上流の `TrashCanMeasurements` が正である**
    （要件 1.3, 10.1）。本群が持つのは、その値を受けるためにこちらが決める量
    （隙間・肉厚・立ち上がり・保持箇所の数）だけである。

    Attributes:
        seat_clearance_mm: 座とゴミ箱の底との隙間（mm）。個体差を吸収する量で
            あり、0（隙間なし）も設定として成立する。
        wall_thickness_mm: 座の肉厚（mm）。
        rise_height_mm: 座の立ち上がり高さ（mm）。
        retention_point_count: 保持箇所の数。1 以上。

    Raises:
        ParameterError: 隙間が負または非有限の場合、肉厚・高さが正の有限値でない
            場合、または保持箇所の数が 1 以上の整数でない場合。
    """

    seat_clearance_mm: float
    wall_thickness_mm: float
    rise_height_mm: float
    retention_point_count: int

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_nonneg_finite(self.seat_clearance_mm, "seat_clearance_mm")
        _require_positive_finite(self.wall_thickness_mm, "wall_thickness_mm")
        _require_positive_finite(self.rise_height_mm, "rise_height_mm")
        _require_count(self.retention_point_count, "retention_point_count", 1)


@dataclass(frozen=True, slots=True)
class BatterySpec:
    """バッテリとそのトレイの寸法（要件 7.1, 7.2, 7.8）。

    Attributes:
        length_mm: 外形の長さ（mm）。
        width_mm: 外形の幅（mm）。
        height_mm: 外形の高さ（mm）。
        mass_g: 質量（g）。⚠️ 機体で最も重い搭載物であり、合成重心の見積もりの
            主要項である（要件 7.8）。
        tray_wall_thickness_mm: トレイの肉厚（mm）。
        hold_height_mm: 保持高さ（mm）。接地点（床）を原点とする、搭載物の重心
            高さの設計値である。⚠️ バッテリは**機体の最下部**へ保持する
            （要件 7.1）。

    Raises:
        ParameterError: いずれかの寸法・質量・高さが正の有限値でない場合。
    """

    length_mm: float
    width_mm: float
    height_mm: float
    mass_g: float
    tray_wall_thickness_mm: float
    hold_height_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.length_mm, "length_mm")
        _require_positive_finite(self.width_mm, "width_mm")
        _require_positive_finite(self.height_mm, "height_mm")
        _require_positive_finite(self.mass_g, "mass_g")
        _require_positive_finite(self.tray_wall_thickness_mm, "tray_wall_thickness_mm")
        _require_positive_finite(self.hold_height_mm, "hold_height_mm")


@dataclass(frozen=True, slots=True)
class BoardSpec:
    """基板トレイと搭載する基板類の寸法（要件 7.4, 7.5, 7.8）。

    Attributes:
        deck_x_mm: デッキの X 方向寸法（mm）。
        deck_y_mm: デッキの Y 方向寸法（mm）。
        standoff_height_mm: スタンドオフの高さ（mm）。
        driver_count: モータドライバの台数。1 以上（要件 7.4 は3台を要求するが、
            台数そのものは設定値である）。
        cooling_gap_mm: 発熱部品の周囲に確保する隙間（mm）。要件 7.5 が寸法
            パラメータとしての保持を求める量である。0 は「隙間を取らない」を
            意味し、設定として成立する。
        mass_g: 基板類とトレイの質量（g）。
        hold_height_mm: 保持高さ（mm）。接地点（床）を原点とする。

    Raises:
        ParameterError: 寸法・質量・高さが正の有限値でない場合、隙間が負もしくは
            非有限の場合、または台数が 1 以上の整数でない場合。
    """

    deck_x_mm: float
    deck_y_mm: float
    standoff_height_mm: float
    driver_count: int
    cooling_gap_mm: float
    mass_g: float
    hold_height_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.deck_x_mm, "deck_x_mm")
        _require_positive_finite(self.deck_y_mm, "deck_y_mm")
        _require_positive_finite(self.standoff_height_mm, "standoff_height_mm")
        _require_count(self.driver_count, "driver_count", 1)
        _require_nonneg_finite(self.cooling_gap_mm, "cooling_gap_mm")
        _require_positive_finite(self.mass_g, "mass_g")
        _require_positive_finite(self.hold_height_mm, "hold_height_mm")


@dataclass(frozen=True, slots=True)
class PowerParams:
    """電源系の機構的な決着を値として持つ（要件 8.1, 8.2, 8.5, 8.7）。

    ⚠️ **未決を未決として表せることが本群の要件である。** メインスイッチ・分岐
    端子・ヒューズホルダ・非常停止の取付余地について、要否・位置・寸法の**決定は
    タスク 5.6** であり、本タスクは「決まっていない」を `None` で正直に表せる形を
    与えるだけである。もっともらしい既定値を置くと、決めていないことが決めたこと
    として設定ファイルに残る。決定の**根拠**は design.md「機構の決定」節（決定 6 /
    決定 7）が記録の正であり、ここが持つのは値だけである。

    Attributes:
        main_switch_present: メイン電源スイッチを設けるか（要件 8.1）。未決は
            `None`。
        main_switch_position: スイッチの取付位置の記述（要件 8.1, 8.3）。
        main_switch_height_mm: スイッチの操作面の高さ（mm）。接地点を原点とする。
        terminal_block_present: 電源分岐端子（端子台）を設けるか（要件 8.2）。
        terminal_block_position: 端子台の保持位置の記述（要件 8.4）。
        terminal_block_length_mm: 端子台の長さ（mm）。
        terminal_block_width_mm: 端子台の幅（mm）。
        terminal_block_height_mm: 端子台の高さ（mm）。
        terminal_block_mass_g: 端子台の質量（g）。⚠️ 決まっていれば合成重心の
            見積もりへ算入する（決定 7）。
        terminal_block_hold_height_mm: 端子台の保持高さ（mm）。
        fuse_holder_position: 主ヒューズホルダの位置の記述（要件 8.5）。
            ⚠️ **バッテリ直近（端子台より上流）**という決定を書く場所である。
        estop_provision: 非常停止手段を後から追加できる取付余地の記述
            （要件 8.7）。⚠️ 非常停止手段そのものの決着は本 Spec の対象外
            （要件 8.6）であり、ここに書くのは**余地**だけである。

    Raises:
        ParameterError: 要否が真偽値でも `None` でもない場合、与えられた数値が
            正の有限値でない場合、与えられた記述が空白のみの場合、または
            「設けない」と決めた要素に位置・寸法が与えられている場合。
    """

    main_switch_present: bool | None
    main_switch_position: str | None
    main_switch_height_mm: float | None
    terminal_block_present: bool | None
    terminal_block_position: str | None
    terminal_block_length_mm: float | None
    terminal_block_width_mm: float | None
    terminal_block_height_mm: float | None
    terminal_block_mass_g: float | None
    terminal_block_hold_height_mm: float | None
    fuse_holder_position: str | None
    estop_provision: str | None

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_bool_or_none(self.main_switch_present, "main_switch_present")
        _require_bool_or_none(self.terminal_block_present, "terminal_block_present")
        _require_nonblank_str_or_none(
            self.main_switch_position, "main_switch_position"
        )
        _require_nonblank_str_or_none(
            self.terminal_block_position, "terminal_block_position"
        )
        _require_nonblank_str_or_none(self.fuse_holder_position, "fuse_holder_position")
        _require_nonblank_str_or_none(self.estop_provision, "estop_provision")
        _require_positive_or_none(self.main_switch_height_mm, "main_switch_height_mm")
        _require_positive_or_none(
            self.terminal_block_length_mm, "terminal_block_length_mm"
        )
        _require_positive_or_none(
            self.terminal_block_width_mm, "terminal_block_width_mm"
        )
        _require_positive_or_none(
            self.terminal_block_height_mm, "terminal_block_height_mm"
        )
        _require_positive_or_none(self.terminal_block_mass_g, "terminal_block_mass_g")
        _require_positive_or_none(
            self.terminal_block_hold_height_mm, "terminal_block_hold_height_mm"
        )
        # ⚠️ 「設けない」と「その位置・寸法」は同時に成立しない。決定を覆した
        # ときに古い値が残る形を型で塞ぐ。未決（None）は何も縛らない。
        self._require_absent_when_not_present(
            "main_switch_present",
            self.main_switch_present,
            {
                "main_switch_position": self.main_switch_position,
                "main_switch_height_mm": self.main_switch_height_mm,
            },
        )
        self._require_absent_when_not_present(
            "terminal_block_present",
            self.terminal_block_present,
            {
                "terminal_block_position": self.terminal_block_position,
                "terminal_block_length_mm": self.terminal_block_length_mm,
                "terminal_block_width_mm": self.terminal_block_width_mm,
                "terminal_block_height_mm": self.terminal_block_height_mm,
                "terminal_block_mass_g": self.terminal_block_mass_g,
                "terminal_block_hold_height_mm": self.terminal_block_hold_height_mm,
            },
        )

    @staticmethod
    def _require_absent_when_not_present(
        presence_name: str, presence: bool | None, dependents: Mapping[str, object]
    ) -> None:
        """「設けない」と決めた要素に付随値が残っていないことを検証する。"""
        if presence is not False:
            return
        for name, value in dependents.items():
            if value is not None:
                raise ParameterError(
                    f"{presence_name}={presence!r} であるのに {name}={value!r} が"
                    "与えられている（設けないと決めた要素に位置・寸法は無い）。"
                )


@dataclass(frozen=True, slots=True)
class StandSpec:
    """整備スタンドの寸法（要件 5.3, 5.4, 5.5）。

    ⚠️ **3脚独立**であり、ホイール外側または駆動ベース端部で支持する（決定 5）。
    駆動ベース下面では支持しない——最低地上高の余裕が小さく、そこにはブラケット・
    ボルト頭・配線が並ぶため、接触部が特定できない。

    Attributes:
        support_span_mm: 1脚がホイールを挟む支持スパン（mm）。
        lift_height_mm: 機体の持ち上げ高さ（mm）。
        wheel_rotation_clearance_mm: 台上でホイールが回るための隙間（mm）。
            ⚠️ 台上確認（M2a-0 の #15〜#18）が成立するための前提である。
        leg_count: 支持脚の数。⚠️ **輪の数と一致する**（`ChassisParams` が検証
            する）——輪ごとに載せ降ろしできることが3脚独立の目的である。

    Raises:
        ParameterError: スパン・持ち上げ高さが正の有限値でない場合、回転の隙間が
            負もしくは非有限の場合、または脚の数が 1 以上の整数でない場合。
    """

    support_span_mm: float
    lift_height_mm: float
    wheel_rotation_clearance_mm: float
    leg_count: int

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.support_span_mm, "support_span_mm")
        _require_positive_finite(self.lift_height_mm, "lift_height_mm")
        _require_nonneg_finite(
            self.wheel_rotation_clearance_mm, "wheel_rotation_clearance_mm"
        )
        _require_count(self.leg_count, "leg_count", 1)


@dataclass(frozen=True, slots=True)
class LocalJointLimits:
    """本 Spec が接合部へ課す下限（要件 2.9 / design.md 決定 3）。

    ⚠️ **上流の下限を厳しくすることはできるが、緩めることはできない。** モータ
    反力（曲げ・ねじり）を受ける接合部には、上流 `JointPolicy` の
    `min_bearing_area_mm2` より**広い**当たり面を要求することがある。逆向きの
    値——上流より緩い下限——は `validate_against_upstream` が拒否する。

    ⚠️ **上流の下限値そのものをここへ写さない**（要件 1.3）。写せば、上流が下限を
    引き上げたときに本 Spec だけが古い値で通ってしまう。本群が持つのは本 Spec の
    下限だけであり、両者の比較は上流の値を読んだ `config`（タスク 1.4）が
    `validate_against_upstream` を呼んで行う。

    Attributes:
        min_bearing_area_mm2: 当たり面の下限（mm^2）。上流の下限以上でなければ
            ならない。⚠️ ダボは位置決め専用であり、この面積に算入しない。

    Raises:
        ParameterError: 下限が正の有限値でない場合。
    """

    min_bearing_area_mm2: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_positive_finite(self.min_bearing_area_mm2, "min_bearing_area_mm2")

    def validate_against_upstream(self, joint_policy: JointPolicy) -> None:
        """上流の下限以上であることを検証する（要件 2.9）。

        ⚠️ **構築時には行えない検査である。** 上流の下限は上流の設定ファイルを
        読まなければ分からず、本モジュールはファイルを読まない（モジュール
        docstring）。したがってこの検査は明示的な呼び出しとして分離されており、
        上流の `JointPolicy` を手にした `config`（タスク 1.4）が必ず呼ぶ。

        Args:
            joint_policy: 上流 `catch_mechanism` の継手方針。

        Raises:
            ParameterError: 引数が `JointPolicy` でない場合、または本 Spec の
                下限が上流の下限を下回る場合（メッセージに**両方の値**を載せる
                ——どちらへ寄せればよいかが分からなければ直せない）。
        """
        if not isinstance(joint_policy, JointPolicy):
            raise ParameterError(
                f"joint_policy={joint_policy!r} は上流の JointPolicy でなければならない"
                "（当たり面の下限の比較には上流の下限が要る）。"
            )
        if self.min_bearing_area_mm2 < joint_policy.min_bearing_area_mm2:
            raise ParameterError(
                f"joint_local.min_bearing_area_mm2={self.min_bearing_area_mm2!r} は "
                f"上流 JointPolicy.min_bearing_area_mm2="
                f"{joint_policy.min_bearing_area_mm2!r} 以上でなければならない"
                "（本 Spec は上流の下限を厳しくできるが緩められない）。"
            )


# ---------------------------------------------------------------------------
# 搭載物の質量と保持高さ（要件 7.8）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MassItem:
    """搭載物1件の質量と保持高さ（要件 7.8）。

    合成重心の見積もりの**入力**である。⚠️ 見積もりの算出そのものは `layout`
    （タスク 2.1）が行い、その結果は**未実測の推定**として扱われ合否条件に
    用いられない（要件 7.9）。

    Attributes:
        name: 搭載物の名前（`battery` / `board` / `power_terminal_block`）。
        mass_g: 質量（g）。
        hold_height_mm: 保持高さ（mm）。接地点（床）を原点とする。0 は床面に
            置かれた状態を表し、設定として成立する。

    Raises:
        ParameterError: 名前が空白のみの場合、質量が正の有限値でない場合、または
            保持高さが負もしくは非有限の場合。
    """

    name: str
    mass_g: float
    hold_height_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反項目名と値を添えて拒否する。"""
        _require_nonblank_str(self.name, "name")
        _require_positive_finite(self.mass_g, "mass_g")
        _require_nonneg_finite(self.hold_height_mm, "hold_height_mm")


# ---------------------------------------------------------------------------
# 集約ルート
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChassisParams:
    """本 Spec 固有の寸法パラメータの集約ルート（要件 1.1, 1.2, 7.8）。

    各群は自身の `__post_init__` で構築時検証済みであるため、本型はフィールド単位
    の再検証を行わず、**群をまたぐ整合**と `provenance` だけを見る（design.md
    `#### Params` Postconditions: 構築に成功した `ChassisParams` は以降の導出で
    再検証を要さない）。

    Attributes:
        bracket: 付属金属ブラケットの実測値。
        motor: モータの外形。
        wheel: オムニホイールの公称寸法。
        hub: ハブの寸法。
        base: 駆動ベースの寸法。
        clearance: 床との隙間の下限と設計量。
        adapter: ゴミ箱固定アダプタの寸法。
        battery: バッテリとトレイの寸法。
        board: 基板トレイの寸法。
        power: 電源系の決着（未決は `None`）。
        stand: 整備スタンドの寸法。
        joint_local: 本 Spec が課す接合部の下限。
        provenance: 各パラメータの出所の対応表（要件 1.2）。⚠️ **キー集合は
            `PARAMETER_PATHS` と一致しなければならない**（design.md
            `#### Params` Invariants）。未知のキーも欠けたキーも拒否する——
            前者は出所が黙って無視される形であり、後者は出所を持たない寸法値が
            設計へ流れる形である。構築時に素の `dict` へ複製するため、呼び出し側
            の辞書のその後の変更は反映されない。

    Raises:
        ParameterError: 脚の数と輪の数が食い違う場合、`provenance` が対応表で
            ない場合、キー集合が `PARAMETER_PATHS` と一致しない場合、または値が
            `Provenance` でない場合。
    """

    bracket: BracketMeasurements
    motor: MotorSpec
    wheel: WheelSpec
    hub: HubSpec
    base: BaseSpec
    clearance: ClearanceLimits
    adapter: AdapterSpec
    battery: BatterySpec
    board: BoardSpec
    power: PowerParams
    stand: StandSpec
    joint_local: LocalJointLimits
    provenance: Mapping[str, Provenance]

    def __post_init__(self) -> None:
        """群をまたぐ整合と `provenance` を検証し、複製で置き換える。

        `PARAMETER_PATHS` は本クラス定義の直後にモジュールレベルで構築されるため、
        この検証はどのインスタンス構築時にも安全に参照できる（モジュールのロードが
        完了するまでインスタンスは作られない）。
        """
        # ⚠️ 3脚独立の目的は「輪ごとに載せ降ろしできること」である（決定 5）。
        # 脚が輪より少なければ支えられず、多ければ支持方式が別物になる。
        if self.stand.leg_count != self.base.wheel_count:
            raise ParameterError(
                f"stand.leg_count={self.stand.leg_count!r} は "
                f"base.wheel_count={self.base.wheel_count!r} と一致しなければならない"
                "（整備スタンドは輪ごとに独立した脚で支持する）。"
            )
        if not isinstance(self.provenance, Mapping):
            raise ParameterError(
                f"provenance={self.provenance!r} はパスから出所への対応表で"
                "なければならない。"
            )
        for key, value in self.provenance.items():
            if key not in PARAMETER_PATHS:
                raise ParameterError(
                    f"provenance のキー {key!r} は PARAMETER_PATHS のパス文字列と"
                    "一致しない。既知のパス（例: 'bracket.mount_face_to_contact_mm'）"
                    "のみを出所の対応表のキーとして指定できる。"
                )
            if not isinstance(value, Provenance):
                raise ParameterError(
                    f"provenance[{key!r}]={value!r} は Provenance でなければならない"
                    f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
                )
        missing = sorted(set(PARAMETER_PATHS) - set(self.provenance))
        if missing:
            raise ParameterError(
                "provenance に出所の無いパラメータがある: "
                + ", ".join(missing)
                + "（要件 1.2: 各寸法値について実測か仮値かを値ごとに保持する）。"
            )
        # 検証を通った集約が以降の層で再検証を要さないためには、検証後に中身が
        # 差し替わらないことが要る。呼び出し側の辞書との**エイリアスを切る**複製を
        # 置くことでこれを満たす。
        # ⚠️ `MappingProxyType` で包んではならない。`dataclasses.asdict()` は
        # dict ではないマッピング型を再帰対象と認識せず `copy.deepcopy()` へ回す
        # ため `TypeError: cannot pickle 'mappingproxy' object` になり、集約の
        # 直列化（`config.dump_params` / `parameters_digest`、タスク 1.4）が最も
        # 自然な経路で壊れる。上流 `MechanismParams.provenance` も素の `dict` を持つ。
        object.__setattr__(self, "provenance", dict(self.provenance))

    def validate_against_upstream(self, joint_policy: JointPolicy) -> None:
        """上流の値と突き合わせる検査をまとめて行う（要件 2.9）。

        現在は接合部の当たり面の下限1件だけだが、⚠️ **`config` が呼ぶ入口を1つに
        しておく**ことで、上流と突き合わせる検査が増えたときに呼び出し側を
        書き換えずに済む。

        Args:
            joint_policy: 上流 `catch_mechanism` の継手方針。

        Raises:
            ParameterError: 上流の下限より緩い値を持つ場合。
        """
        self.joint_local.validate_against_upstream(joint_policy)

    def mass_items(self) -> tuple[MassItem, ...]:
        """搭載物の質量と保持高さを並べて返す（要件 7.8）。

        合成重心の見積もり（`layout`、タスク 2.1）の入力である。

        ⚠️ **未決の搭載物は並びに現れない。** 端子台は要否も質量も未決でありうる
        （タスク 5.6）ため、質量と保持高さの**両方が決まっているときだけ**算入する。
        未決を 0 として算入すると、「質量が無い」という主張になってしまう——
        見積もりが軽い側へ黙って寄る。

        Returns:
            搭載物ごとの `MassItem`。並びは定義順で安定している。
        """
        items = [
            MassItem(
                name="battery",
                mass_g=self.battery.mass_g,
                hold_height_mm=self.battery.hold_height_mm,
            ),
            MassItem(
                name="board",
                mass_g=self.board.mass_g,
                hold_height_mm=self.board.hold_height_mm,
            ),
        ]
        if (
            self.power.terminal_block_mass_g is not None
            and self.power.terminal_block_hold_height_mm is not None
        ):
            items.append(
                MassItem(
                    name="power_terminal_block",
                    mass_g=self.power.terminal_block_mass_g,
                    hold_height_mm=self.power.terminal_block_hold_height_mm,
                )
            )
        return tuple(items)


# ---------------------------------------------------------------------------
# パラメータパス表（⚠️ データクラス木の走査で生成する。手書きの表を持たない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParameterPath:
    """`PARAMETER_PATHS` の1エントリ。

    design.md `#### Params` は `PARAMETER_PATHS: Mapping[str, ParameterPath]` と
    いう型のみを宣言し、`ParameterPath` 自身の内部構造までは定めていない
    （設計ギャップ）。⚠️ **上流の同名の型を借りることはできない**——上流の
    `ParameterPath` は `catch_mechanism.__init__.__all__` に**無く**、公開契約の
    外にある（公開されているのは `PARAMETER_PATHS` という表と `PARAMETER_PATHS
    [path].unit` という読み方だけである）。内部モジュールへ直接 import しない
    という制約（design.md「Allowed Dependencies」）に従い、本 Spec は自前の
    エントリ型を定義する。上流の形（パス・単位・所属・フィールド名・値の型）は
    そのまま踏襲し、未決を許す項目のために `optional` を1つ足す。

    Attributes:
        path: パス文字列そのもの（例: `"bracket.outline_x_mm"`）。
            `PARAMETER_PATHS` の対応するキーと常に一致する。
        unit: フィールド名の接尾辞から導いた単位文字列（`_SUFFIX_UNITS` 参照）。
            単位を持たない項目（記述・個数・真偽値）は `""`。
        component: `ChassisParams` 直下のコンポーネント名（例: `"bracket"`）。
        field_name: `component` の中のリーフフィールド名。
        value_types: リーフが取りうる値の型（`None` を除く）。設定ファイルの値が
            この型であることを `config`（タスク 1.4）が検査する。
        optional: `None`（未決）を許すか。⚠️ 真になるのは電源系だけである——
            寸法に「未決」は無い。
    """

    path: str
    unit: str
    component: str
    field_name: str
    value_types: tuple[type, ...]
    optional: bool


# フィールド名の接尾辞から単位を導くための表（手書きの表を二重管理しないため）。
# この表は単位の**導出規則**であってパス表そのものではなく、単位についての唯一の
# 情報源である。
#
# より特殊的な（長い）接尾辞を先に判定する。⚠️ `_mm2` は `_mm` の前に置かなければ
# 誤って一致する。
_SUFFIX_UNITS: tuple[tuple[str, str], ...] = (
    ("_mm2", "mm^2"),
    ("_mm", "mm"),
    ("_deg", "deg"),
    ("_g", "g"),
)


def _derive_unit(field_name: str) -> str:
    """フィールド名の接尾辞から単位文字列を導く。

    どの接尾辞にも該当しないフィールド（記述 `mount_face_reference`、個数
    `wheel_count`、真偽値 `shaft_flat_present` など）は `""`（単位なし）を返す。
    この既定フォールバックも本関数に一本化し、呼び出し側で単位判定を重複させない。
    """
    for suffix, unit in _SUFFIX_UNITS:
        if field_name.endswith(suffix):
            return unit
    return ""


def _split_optional(hint: object) -> tuple[tuple[type, ...], bool]:
    """型注釈を「`None` を除く型の並び」と「`None` を許すか」に分ける。

    `float | None`（未決を許す電源系の項目）と `float` の両方を、パス表の同じ形へ
    落とすために要る。
    """
    if get_origin(hint) in (Union, UnionType):
        args = get_args(hint)
        optional = type(None) in args
        types = tuple(arg for arg in args if arg is not type(None))
        return types, optional
    return (hint,), False  # type: ignore[return-value]


#: `PARAMETER_PATHS` から除外する集約ルート直下のフィールド。
#: `provenance` は寸法ではなく**パスから出所への対応表そのもの**であり、単一の
#: リーフ値ではない。ここへ含めると「出所の出所」を要求することになる。
_EXCLUDED_ROOT_FIELDS: frozenset[str] = frozenset({"provenance"})


def _build_parameter_paths(
    root: type, excluded: frozenset[str] = _EXCLUDED_ROOT_FIELDS
) -> Mapping[str, ParameterPath]:
    """データクラス木を走査してパス表を構築する。

    `dataclasses.fields()` で直下のコンポーネントを列挙し、それぞれのリーフ
    フィールドを `"<component>.<field>"` の形でパスにする。⚠️ **手書きの表は一切
    持たず、この走査結果だけが唯一の情報源である**（tasks.md タスク 1.3）——
    手書きであれば、フィールドを1つ増やしたときに表から黙って漏れる。

    `root` を引数に取るのは、この関数が特定の型に結び付いた列挙ではなく**木の
    走査**であることをテストが確かめられるようにするためである。

    `from __future__ import annotations` の影響で `Field.type` は文字列注釈の
    ままであるため、`typing.get_type_hints` で実体の型へ解決してからデータクラス
    判定・値の型の記録を行う。本関数はクラスの構造を調べるだけで、いかなる
    インスタンスも構築しない。
    """
    paths: dict[str, ParameterPath] = {}
    root_hints = get_type_hints(root)
    for root_field in fields(root):  # type: ignore[arg-type]
        if root_field.name in excluded:
            continue
        component_type = root_hints[root_field.name]
        if not is_dataclass(component_type):  # pragma: no cover - 構造上の防御
            raise TypeError(
                f"{root.__name__}.{root_field.name} はデータクラスでなければならない。"
            )
        leaf_hints = get_type_hints(component_type)
        for leaf_field in fields(component_type):
            path = f"{root_field.name}.{leaf_field.name}"
            value_types, optional = _split_optional(leaf_hints[leaf_field.name])
            paths[path] = ParameterPath(
                path=path,
                unit=_derive_unit(leaf_field.name),
                component=root_field.name,
                field_name=leaf_field.name,
                value_types=value_types,
                optional=optional,
            )
    return MappingProxyType(paths)


PARAMETER_PATHS: Mapping[str, ParameterPath] = _build_parameter_paths(ChassisParams)
"""本 Spec 固有の寸法パラメータのパス表（tasks.md タスク 1.3）。

`ChassisParams` のデータクラス木からモジュールのロード時に一度だけ生成される
不変マッピング。`provenance` のキー集合はこの表と**一致**しなければならず
（design.md `#### Params` Invariants）、設定ファイル
（`configs/chassis_mechanism/dimensions.json`、タスク 1.4）の項目名と値の型も
この表と突き合わせて検査される。

⚠️ **この表に上流のコンポーネント名は現れない**（`trash_can` / `target_object` /
`printing` / `joint` / `rim` / `retention`）。要件 1.3 の機械的な担保であり、
design.md「静的検査」が全モジュールへ広げる。
"""
