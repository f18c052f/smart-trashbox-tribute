"""幾何の導出と導出記録の直列化（design.md `#### Layout` / 要件 3.1, 3.2, 3.3,
3.5, 3.6, 4.1, 7.8, 7.9）。

**ホイール配置半径の式はプロジェクト内でここにしか無い**（要件 3.3 / tasks.md
タスク 2.1「⚠️ この導出をここ以外に置かない」）。下流の `clearance` / `joints` /
`shapes` は `ChassisLayout` を**消費する**のであって、同じ式を書き直さない——
同じ式が2箇所にあれば、片方だけが直った状態を誰も検出できない
（上流 `catch_mechanism.tolerance` が採る規律と同じである）。

**「機体中心 → 取付面」は導出せず、寸法パラメータから読む**（要件 1.1, 1.5, 3.3 /
design.md `#### Layout`: `base_radius_mm = hub_center_to_mount_face_mm +
bracket.mount_face_to_wheel_center_mm` はこの値を**入力として**扱う）。
⚠️ **本 Spec が決める設計変数はこの1つだけであり、それを選ぶのはタスク 5.4 である**
（要件 3.4 / `docs/bom.md §B`「駆動ベースの半径を決めれば R が定まるため、設計変数が
1つに減る」）。他の寸法から式で組み立てれば、決めるべき値が設定ファイルの外へ移り、
値を書き換えるだけの再導出（要件 1.5）も、4条件に照らして範囲から選ぶ決定
（タスク 5.4）も成り立たなくなる。

**輪番号と各輪の取付角の規約は本 Spec が定義しない**（要件 3.6 / design.md
「Out of Boundary」）。⚠️ `firmware/lib/drivetrain_control` の `Kinematics` の
行定義が唯一の正であり、本モジュールが行うのは「機体 +x から反時計回り」という
**受け取った基準**の上で `first_wheel_angle_deg` から等配置を生成することだけで
ある。⚠️ **`firmware/` を import しない**（別のビルド系列であり、依存境界の静的
検査が禁じている）。角度を [0, 360) へ折り返さないのも同じ理由である——折り返せば
隣接角の差が符号を変え、輪番号と符号の対応を本 Spec が作り直したことになる。

**鉛直スタックは接地点（床）を原点として組み立てる**（要件 4.1）。⚠️ 高さの順は
**床 → モータ胴体下面 → 車軸中心 → 締結の下端 → 取付面（＝ベース板下面）**であり、
`VerticalStack` のフィールドの宣言順（design.md の Service Interface のまま）とは
異なる。モータはホイールと同軸に吊り下がるため、⚠️ **胴体下面は必ず車軸中心より
低い**（`docs/drivetrain-spec.md §6.3`: 車軸 30mm に対し胴体下面 11.5mm）。
これは design.md `#### Layout` Invariants の
`0 < motor_body_bottom_height_mm < axle_center_height_mm ==
effective_rolling_radius_mm < fastener_bottom_height_mm <= mount_face_height_mm`
をそのまま実装したものである。⚠️ **最後の対だけが非厳密である**——
`clearance.fastener_protrusion_mm == 0`（皿頭などで突出が無い状態）のとき締結の
下端は取付面と同一平面になり、これは成立する設計である。
逆転する入力は `GeometryError` で拒否し、**逆転した対と量**をメッセージに載せる
（`errors.py`: 依存を持たない層であるため違反は例外メッセージが運ぶ）。

**⚠️ 取付面の高さは実測距離そのままの定数ではない**（要件 4.7, 10.3）。
`bracket.mount_face_to_contact_mm`（60.0mm）は**ホイールを付けた状態で**測った
「取付面 → 接地点」であり、⚠️ **公称の転がり半径をすでに含んでいる**
（`docs/bom.md §B`:「垂直方向では 60.0 − 30 ＝ 30.0mm が取付面から車軸までの
高さになり整合する」）。したがって荷重で転がり半径が δ 縮めば機体全体が δ 低く
座り、取付面の高さも締結の下端も δ 下がる。⚠️ **ここを定数にすると、床との隙間の
5部位のうち3部位が実効転がり半径に追随しない**——`clearance` 側は高さをすべて
`VerticalStack` から読んでいるため、追随しない原因は本モジュールにしか無く、
「隙間は足りている」という誤った判定だけが残る（design.md `#### Clearance`
Responsibilities「隙間はすべて `VerticalStack` から算出されるため、実効転がり半径が
変われば自動で追随する（要件 4.7）」）。補正項は「公称 − 実効」であり、観測記録が
入るまでは 0 であるため、⚠️ **要件 4.1（「モータ取付面から接地点までの実測距離
から導出する」）が定める出所はそのまま保たれる**——4.1 が固定するのは高さの
**出所**であって、荷重下でも高さが動かないことではない（動かないことまで求めて
いると読むと要件 4.7 と両立しない）。

**転倒余裕は合否条件ではない**（要件 3.5, 7.9 / `tech.md` 開発標準1）。
`TippingEstimate.is_pass_criterion` は常に `False` であり、⚠️ **`True` を持つ推定は
構築できない**。加えて本モジュールには `accel_limit_mm_s2` を何かと比較する箇所が
1つも無い——比較が無ければ、後から「未達だから設計を変える」経路が生まれない。
`test_chassis_layout.py` がこの不在を `ast` で固定する。

**出所は入力の最弱を継承する**（要件 1.9 / `params.weakest_provenance`）。
⚠️ **`bracket.mount_face_reference` の確認が済むまで `base_radius_mm` の出所は
仮値である**（design.md `#### Layout` Risks）。本モジュールはこれを黙って実測へ
格上げしない。実効転がり半径も、観測記録（`measurements.json`、タスク 2.4）が
まだ存在しないため**公称値の半分**を用い、その出所（`wheel.nominal_diameter_mm`）
を継承する。⚠️ 観測の読み手をここへ先取りで置かない——`assembly` は本モジュールの
右側の層であり、依存方向が逆になる。⚠️ **観測値を差し込む点は
`_effective_rolling_radius_mm` の1箇所だけである**（タスク 2.4）——鉛直スタックの
全高さはその戻り値と公称値との差から組み上がるため、差し替えても積み上げ自体を
組み替える必要がない。

読み書きの規律は `config.py` に揃える（**あらゆる階層で未知キーを拒否する**、
項目名を示す、欠損を既定値で埋めない、LF・インデント2・キー整列・末尾改行）。
記録形式の版は `config.SCHEMA_VERSION` を共有する。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from chassis_mechanism.config import SCHEMA_VERSION, ResolvedParams
from chassis_mechanism.errors import GeometryError, ParameterError
from chassis_mechanism.params import (
    PARAMETER_PATHS,
    ChassisParams,
    Provenance,
    weakest_provenance,
)

__all__ = [
    "DEFAULT_LAYOUT_PATH",
    "GRAVITY_MM_S2",
    "FORMULA",
    "ARM_LENGTH_FORMULA",
    "TIPPING_FORMULA",
    "ASSUMPTIONS",
    "REQUIRED_INPUT_NAMES",
    "TIPPING_NOTE",
    "VerticalStack",
    "TippingEstimate",
    "ChassisLayout",
    "derive_layout",
    "dump_layout",
    "load_layout",
]


DEFAULT_LAYOUT_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "chassis_mechanism" / "layout.json"
)
"""導出記録の既定パス（design.md「Data Models」`configs/chassis_mechanism/layout.json`）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。⚠️ **設計入力の隣に置くが、設計入力ではない**——この記録は
`derive_layout` の写しであり、手で編集する対象ではない（`load_layout` が入力と
食い違う記録を拒否する）。
"""

GRAVITY_MM_S2: Final[float] = 9806.65
"""標準重力加速度（mm/s^2）。

⚠️ **本 Spec の単位はミリメートルである**（`docs/requirements.md` の単位規約）。
9.80665 m/s^2 をミリメートル系へ直した値であり、転倒余裕の見積もりの唯一の
定数入力である。
"""


_HUB_OUTER_DIAMETER_PATH: Final[str] = "base.hub_outer_diameter_mm"
_HUB_CENTER_TO_MOUNT_FACE_PATH: Final[str] = "base.hub_center_to_mount_face_mm"
_MOUNT_FACE_TO_WHEEL_CENTER_PATH: Final[str] = "bracket.mount_face_to_wheel_center_mm"
_MOUNT_FACE_REFERENCE_PATH: Final[str] = "bracket.mount_face_reference"
_MOUNT_FACE_TO_CONTACT_PATH: Final[str] = "bracket.mount_face_to_contact_mm"
_WHEEL_NOMINAL_DIAMETER_PATH: Final[str] = "wheel.nominal_diameter_mm"
_WHEEL_WIDTH_PATH: Final[str] = "wheel.width_mm"
_MOTOR_BODY_DIAMETER_PATH: Final[str] = "motor.body_diameter_mm"
_FASTENER_PROTRUSION_PATH: Final[str] = "clearance.fastener_protrusion_mm"
_HUB_FLANGE_THICKNESS_PATH: Final[str] = "hub.flange_thickness_mm"
_FIRST_WHEEL_ANGLE_PATH: Final[str] = "base.first_wheel_angle_deg"
_WHEEL_COUNT_PATH: Final[str] = "base.wheel_count"

FORMULA: Final[str] = (
    f"hub_center_to_mount_face_mm + {_MOUNT_FACE_TO_WHEEL_CENTER_PATH}"
)
"""ホイール配置半径の導出式（要件 3.3 / design.md `#### Layout`）。

`docs/bom.md §B` の「R（機体中心 → ホイール接地点）＝（機体中心 → 取付面の距離）
＋ 33.9mm」をそのまま型にしたものである。演算子は ASCII に揃える（記録は grep と
突き合わせの対象であるため、見た目の異なる同義字を持ち込まない）。

⚠️ **第1項は寸法パラメータ `base.hub_center_to_mount_face_mm` をそのまま読む
（式で作らない）。** `docs/bom.md §B` が「駆動ベースの半径を決めれば R が定まる
ため、設計変数が1つに減る」と書いているとおり、この値こそが本 Spec の唯一の
設計変数であり、その決定はタスク 5.4（要件 3.4）が持つ。ここで別の寸法から
組み立ててしまうと、⚠️ **決めるべき値が設定ファイルの外——コードの中——に
移り**、値を書き換えるだけの再導出（要件 1.5）が成り立たなくなる。
"""

ARM_LENGTH_FORMULA: Final[str] = f"base_radius_mm - {_HUB_OUTER_DIAMETER_PATH} / 2"
"""放射状アームの半径方向の張り出しの導出式。

中央部（`hub_plate`）の外縁からホイール中心面までが、モータ取付部
（`motor_arm_*`）が受け持つ区間である（design.md `#### Shapes` の部品表）。
⚠️ **アームが成立しない配置半径は `GeometryError` で拒否する**——長さが正で
あることと、ハブとの接合部の当たり面を確保できる長さであることの両方を要する
（design.md `#### Layout` Postconditions / Validation、要件 3.10 の前提）。
"""

TIPPING_FORMULA: Final[str] = "GRAVITY_MM_S2 * base_radius_mm / (2 * cog_height_mm)"
"""転倒余裕の見積もりの式（design.md `#### Layout` / 決定 1 の条件4）。

⚠️ **合否条件ではない**（要件 3.5）。`TIPPING_NOTE` を参照。
"""

TIPPING_NOTE: Final[str] = (
    "転倒余裕は未実測の推定であり、⚠️ 合否条件ではない（要件 3.5 / 3.4 の決定1 条件4）。"
    "未達を理由に設計を変えず、実測と性能値の決定は m2-motion-validation が持つ。"
    "搭載物の質量と保持高さは仮値であり、合成重心の見積もりもその上に載っている。"
)
"""転倒余裕に必ず添える注記（要件 3.5）。

⚠️ **型と記録の両方に「合否条件ではない」ことを書く。** 型だけに書くと記録を
読んだ人がその位置付けを知らないまま数値を使い、記録だけに書くとコードから
呼ぶ人が知らないまま比較を書ける。
"""

ASSUMPTIONS: Final[tuple[str, ...]] = (
    f"「機体中心 → 取付面」は寸法パラメータ {_HUB_CENTER_TO_MOUNT_FACE_PATH} を"
    "そのまま読む。⚠️ 現在の値は仮値（101.3mm）であり、これを選ぶのは"
    "タスク 5.4（造形可能性・接合の成立・座の到達・転倒余裕の記録可能性の4条件）"
    "である。docs/bom.md §B が「駆動ベースの半径を決めれば R が定まる」と記すとおり、"
    "本 Spec の設計変数はこの1つだけであり、⚠️ 他の寸法から式で作らない"
    "（作れば決めるべき値が設定ファイルの外へ移り、値の書き換えだけでは"
    "再導出できなくなる）。",
    f"⚠️ {_MOUNT_FACE_REFERENCE_PATH} の確認が済むまで、配置半径と鉛直スタックの"
    "出所は仮値である（design.md #### Layout Risks）。derive_layout はこれを黙って"
    "実測へ格上げしない。配置半径の確定はタスク 5.4 が4条件に照らして行う。",
    "実効転がり半径は、観測記録がまだ存在しないため公称値の半分を用いる"
    f"（{_WHEEL_NOMINAL_DIAMETER_PATH} / 2）。荷重下の実効転がり径が測定されれば"
    "（要件 10.3）鉛直スタックの全高さがそれに追随する。",
    "合成重心の見積もりは ChassisParams.mass_items()（バッテリ・基板・電源端子台）の"
    "質量と保持高さだけを入力とし、⚠️ 構造材の質量を算入しない。"
    "各入力の値と出所は configs/chassis_mechanism/dimensions.json が正である。",
    "各入力の出所（実測 / 仮値）は dimensions.json の provenance 表が単一の正であり、"
    "本記録は導出値が継承した最弱の出所だけを持つ（同じ出所を2箇所に持たない）。"
    "⚠️ 上流 catch-opening.json が入力ごとに provenance を併記するのに対し本記録が"
    "併記しないのは、書き落としではなく意図した差である——design.md が定める "
    "dump_layout(layout, path) の引数は導出結果だけであり、寸法パラメータを"
    "受け取らないため、入力ごとの出所を記録から一意に決められない。"
    "入力ごとの出所を知るには dimensions.json の provenance 表を引く。",
    "wheel_angles_deg は機体 +x から反時計回りの度であり、first_wheel_angle_deg から"
    "等配置で並べたまま折り返していない。⚠️ 輪番号と符号の規約は "
    "firmware/lib/drivetrain_control の Kinematics の行定義が唯一の正であり、"
    "そちら側は同じ角度を (-180, 180] へ折り返して保持する"
    "（例: 240 度と -120 度は同じ輪である）。転記の際は単位と折り返しに注意する。",
)
"""導出に伴う前提（要件 3.4 / 上流 `catch-opening.json` の `assumptions` と同形）。

記録はこの6件を**必ず含む**。前提の落ちた記録を受け付ければ、「どういう条件下で
成り立つ値なのか」を持たない数だけが下流（`teleop-bringup` のファーム設定）へ流れる。
"""

REQUIRED_INPUT_NAMES: Final[tuple[str, ...]] = (
    "hub_center_to_mount_face_mm",
    _MOUNT_FACE_TO_WHEEL_CENTER_PATH,
)
"""導出記録が持つ入力の名と並び（`FORMULA` の2項ちょうど）。

⚠️ **過不足のいずれも許さない。** 欠ければ「何から導いたのか」が記録から失われ、
余れば式に現れない量が根拠であるかのように読める（上流 `tolerance.REQUIRED_INPUT_NAMES`
と同じ規律）。第1項の名は design.md `#### Layout` の式に現れる短い名のままであり、
その値の正は寸法パラメータ `base.hub_center_to_mount_face_mm` である。
"""


_LAYOUT_INPUT_PATHS: Final[tuple[str, ...]] = (
    _HUB_OUTER_DIAMETER_PATH,
    _HUB_CENTER_TO_MOUNT_FACE_PATH,
    _MOUNT_FACE_TO_WHEEL_CENTER_PATH,
    _MOUNT_FACE_REFERENCE_PATH,
    _MOUNT_FACE_TO_CONTACT_PATH,
    _WHEEL_NOMINAL_DIAMETER_PATH,
    _WHEEL_WIDTH_PATH,
    _MOTOR_BODY_DIAMETER_PATH,
    _FASTENER_PROTRUSION_PATH,
    _HUB_FLANGE_THICKNESS_PATH,
    _FIRST_WHEEL_ANGLE_PATH,
    _WHEEL_COUNT_PATH,
)
"""導出に参加する寸法パラメータのパス（出所の継承の対象）。

⚠️ **搭載物の質量と保持高さは `_mass_item_paths` が動的に足す**——端子台は要否が
未決でありうるため（`ChassisParams.mass_items()`）、一覧を固定できない。

⚠️ **`bracket.mount_face_reference` を外さない。** 基準面の確認が済むまで配置半径の
出所は仮値であるという design.md `#### Layout` Risks の取り決めは、この記述項目の
出所を導出の入力として継承することで機械的に成立している（記述に数値としての
出番は無いが、出所としての出番がある）。
"""

_ROUND_DIGITS: Final[int] = 9
"""記録へ書き出す数の丸め桁数。

⚠️ **値を変える丸めではない。** 導出は `+` と `/` の連鎖であり、`60.0 + 41.3`
から `41.3` を引き戻すと `41.30000000000001` のような表現差が出る。ミリメートル
9桁は現物の測定分解能（0.05mm 程度）を7桁下回るため、丸めても意味は変わらない。
一方で丸めなければ**同じ入力から書き出すたびに桁の揺れが差分として現れる**——
記録は行単位の差分が読めることを要件 1.10 が求めている。
"""

_ABS_TOL: Final[float] = 10.0 ** (-_ROUND_DIGITS + 3)
"""記録の整合検査に用いる絶対許容差（`_ROUND_DIGITS` の丸めより1000倍緩い）。"""

_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_FORMULA_KEY: Final[str] = "formula"
_INPUTS_KEY: Final[str] = "inputs"
_ASSUMPTIONS_KEY: Final[str] = "assumptions"
_PROVENANCE_KEY: Final[str] = "provenance"
_BASE_RADIUS_KEY: Final[str] = "base_radius_mm"
_WHEEL_ANGLES_KEY: Final[str] = "wheel_angles_deg"
_HUB_CENTER_KEY: Final[str] = "hub_center_to_mount_face_mm"
_ARM_LENGTH_KEY: Final[str] = "arm_length_mm"
_VERTICAL_KEY: Final[str] = "vertical"
_AXIAL_STACK_KEY: Final[str] = "axial_stack_mm"
_TIPPING_KEY: Final[str] = "tipping"
_NAME_KEY: Final[str] = "name"
_VALUE_KEY: Final[str] = "value_mm"

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        _SCHEMA_VERSION_KEY,
        _FORMULA_KEY,
        _INPUTS_KEY,
        _ASSUMPTIONS_KEY,
        _PROVENANCE_KEY,
        _BASE_RADIUS_KEY,
        _WHEEL_ANGLES_KEY,
        _HUB_CENTER_KEY,
        _ARM_LENGTH_KEY,
        _VERTICAL_KEY,
        _AXIAL_STACK_KEY,
        _TIPPING_KEY,
    }
)
_INPUT_KEYS: Final[frozenset[str]] = frozenset({_NAME_KEY, _VALUE_KEY})

_EFFECTIVE_ROLLING_RADIUS_KEY: Final[str] = "effective_rolling_radius_mm"
_AXLE_CENTER_KEY: Final[str] = "axle_center_height_mm"
_MOTOR_BODY_BOTTOM_KEY: Final[str] = "motor_body_bottom_height_mm"
_MOUNT_FACE_HEIGHT_KEY: Final[str] = "mount_face_height_mm"
_FASTENER_BOTTOM_KEY: Final[str] = "fastener_bottom_height_mm"

_VERTICAL_KEYS: Final[frozenset[str]] = frozenset(
    {
        _EFFECTIVE_ROLLING_RADIUS_KEY,
        _AXLE_CENTER_KEY,
        _MOTOR_BODY_BOTTOM_KEY,
        _MOUNT_FACE_HEIGHT_KEY,
        _FASTENER_BOTTOM_KEY,
    }
)

_ACCEL_LIMIT_KEY: Final[str] = "accel_limit_mm_s2"
_COG_HEIGHT_KEY: Final[str] = "cog_height_mm"
_IS_PASS_CRITERION_KEY: Final[str] = "is_pass_criterion"
_NOTE_KEY: Final[str] = "note"

_TIPPING_KEYS: Final[frozenset[str]] = frozenset(
    {_ACCEL_LIMIT_KEY, _COG_HEIGHT_KEY, _IS_PASS_CRITERION_KEY, _NOTE_KEY}
)

_PROVENANCE_VALUES: Final[Mapping[str, Provenance]] = {
    provenance.value: provenance for provenance in Provenance
}


# ---------------------------------------------------------------------------
# 共通の検証部品（⚠️ メッセージは常に項目名と値を持つ）
# ---------------------------------------------------------------------------


def _require_finite(value: object, name: str) -> float:
    """`value` が有限な数（`bool` を除く）であることを要求する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(
            f"{name}={value!r} は数値でなければならない（{type(value).__name__} だった）。"
        )
    number = float(value)
    if not math.isfinite(number):
        raise GeometryError(f"{name}={value!r} は有限でなければならない。")
    return number


def _require_positive(value: object, name: str) -> float:
    """`value` が正の有限値であることを要求する。"""
    number = _require_finite(value, name)
    if number <= 0.0:
        raise GeometryError(f"{name}={value!r} は正でなければならない。")
    return number


def _require_below(
    lower_name: str, lower: float, upper_name: str, upper: float, *, strict: bool
) -> None:
    """鉛直方向の順序を1対だけ検証する。

    ⚠️ **逆転した対と量をメッセージに載せる**（`errors.py`: 依存を持たない層で
    あるため、部位名と量は例外メッセージが運ぶ）。「スタックが不正」とだけ言われ
    ても、どの2つがどれだけ食い違っているかが分からなければ現物にも設定ファイル
    にも戻れない。
    """
    if upper > lower or (not strict and upper == lower):
        return
    raise GeometryError(
        f"鉛直スタックが逆転している: {upper_name}={upper!r} は "
        f"{lower_name}={lower!r} より {lower - upper!r}mm 低い"
        "（接地点を原点として、床 → モータ胴体下面 → 車軸中心 → 締結の下端 → "
        "取付面の順に高くなる）。"
    )


# ---------------------------------------------------------------------------
# 導出結果の型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerticalStack:
    """接地点（床）を原点とする鉛直方向の積み上がり（要件 4.1）。

    ⚠️ **フィールドの宣言順は design.md の Service Interface のままであり、
    高さの順ではない。** 高さの順は
    **床 → `motor_body_bottom_height_mm` → `axle_center_height_mm` →
    `fastener_bottom_height_mm` → `mount_face_height_mm`** である。
    モータはホイールと同軸に吊り下がるため胴体下面は必ず車軸中心より低く
    （`docs/drivetrain-spec.md §6.3`: 車軸 30mm に対し胴体下面 11.5mm）、
    締結部品はベース板下面から下方へ突き出るため取付面より低い。

    Attributes:
        effective_rolling_radius_mm: 実効転がり半径（mm）。⚠️ 観測記録が無い間は
            公称値の半分である（`ASSUMPTIONS`）。
        axle_center_height_mm: 車軸中心の高さ（mm）。⚠️ **実効転がり半径と一致する**
            ——ホイールが床に接している以上、これは定義であって独立な値ではない。
        motor_body_bottom_height_mm: モータ胴体下面の高さ（mm）。全部位で最も低い。
        mount_face_height_mm: 取付面（＝ベース板下面）の高さ（mm）。全部位で最も高い。
            ⚠️ **`bracket.mount_face_to_contact_mm` そのままの定数ではない**——
            あの実測距離はホイールを付けた状態で測られており公称の転がり半径を
            含むため、実効転がり半径が縮んだ分だけ低くなる（要件 4.7, 10.3）。
        fastener_bottom_height_mm: 締結部品（ボルト頭・ナット）の下端の高さ（mm）。
            取付面から `clearance.fastener_protrusion_mm` だけ下がる。

    Raises:
        GeometryError: 数が有限でない場合、モータ胴体下面が床以下の場合、
            または高さが逆転する場合。
    """

    effective_rolling_radius_mm: float
    axle_center_height_mm: float
    motor_body_bottom_height_mm: float
    mount_face_height_mm: float
    fastener_bottom_height_mm: float

    def __post_init__(self) -> None:
        """高さの順序を検証し、逆転する入力を対と量つきで拒否する。"""
        radius = _require_positive(
            self.effective_rolling_radius_mm, _EFFECTIVE_ROLLING_RADIUS_KEY
        )
        axle = _require_positive(self.axle_center_height_mm, _AXLE_CENTER_KEY)
        motor_bottom = _require_finite(
            self.motor_body_bottom_height_mm, _MOTOR_BODY_BOTTOM_KEY
        )
        mount_face = _require_positive(self.mount_face_height_mm, _MOUNT_FACE_HEIGHT_KEY)
        fastener_bottom = _require_finite(
            self.fastener_bottom_height_mm, _FASTENER_BOTTOM_KEY
        )

        if not math.isclose(axle, radius, rel_tol=0.0, abs_tol=_ABS_TOL):
            raise GeometryError(
                f"{_AXLE_CENTER_KEY}={axle!r} は "
                f"{_EFFECTIVE_ROLLING_RADIUS_KEY}={radius!r} と一致しなければならない"
                "（ホイールが床に接している以上、車軸中心の高さは実効転がり半径である）。"
            )
        if motor_bottom <= 0.0:
            raise GeometryError(
                f"{_MOTOR_BODY_BOTTOM_KEY}={motor_bottom!r} は正でなければならない"
                "（モータ胴体が床へ接触する配置は成立しない。"
                "docs/drivetrain-spec.md §6.3）。"
            )
        _require_below(
            _MOTOR_BODY_BOTTOM_KEY, motor_bottom, _AXLE_CENTER_KEY, axle, strict=True
        )
        _require_below(
            _AXLE_CENTER_KEY, axle, _FASTENER_BOTTOM_KEY, fastener_bottom, strict=True
        )
        # ⚠️ 最後の対だけ等号を許す。`clearance.fastener_protrusion_mm` は 0 を
        # 許す値であり（皿頭などで突出が無い状態）、そのとき締結の下端は取付面と
        # 同一平面になる。ここを厳密にすると、成立する設計を拒むことになる。
        _require_below(
            _FASTENER_BOTTOM_KEY,
            fastener_bottom,
            _MOUNT_FACE_HEIGHT_KEY,
            mount_face,
            strict=False,
        )


@dataclass(frozen=True, slots=True)
class TippingEstimate:
    """転倒余裕の見積もり（要件 3.5, 7.9）。

    ⚠️ **これは合否条件ではない。** `is_pass_criterion` は常に `False` であり、
    `True` を持つ推定は構築できない。加えて本モジュールには `accel_limit_mm_s2`
    を何かと比較する箇所が1つも無い——比較が無ければ、後から「未達だから設計を
    変える」経路が生まれない。実測と性能値の決定は `m2-motion-validation` が持つ。

    Attributes:
        accel_limit_mm_s2: 転倒が始まる目安の水平加速度（mm/s^2）。`TIPPING_FORMULA`。
        cog_height_mm: 搭載物の合成重心の高さ（mm、接地点を原点とする）。
        is_pass_criterion: ⚠️ **常に `False`**。型の上での表明である。
        note: 位置付けの注記。`TIPPING_NOTE` と一致しなければならない。

    Raises:
        ParameterError: `is_pass_criterion` が `False` でない場合、注記が
            `TIPPING_NOTE` と異なる場合、または数が正の有限値でない場合。
    """

    accel_limit_mm_s2: float
    cog_height_mm: float
    is_pass_criterion: bool
    note: str

    def __post_init__(self) -> None:
        """⚠️ 合否条件を名乗る推定を構築できないことを保証する。"""
        if self.is_pass_criterion is not False:
            raise ParameterError(
                f"{_IS_PASS_CRITERION_KEY}={self.is_pass_criterion!r} は常に False で"
                "なければならない（要件 3.5: 転倒余裕の見積もりを合否条件に用いない）。"
            )
        if self.note != TIPPING_NOTE:
            raise ParameterError(
                f"{_NOTE_KEY}={self.note!r} は TIPPING_NOTE と一致しなければならない"
                "（位置付けの注記が落ちた記録を受け付けない）。"
            )
        for value, name in (
            (self.accel_limit_mm_s2, _ACCEL_LIMIT_KEY),
            (self.cog_height_mm, _COG_HEIGHT_KEY),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ParameterError(
                    f"{name}={value!r} は数値でなければならない"
                    f"（{type(value).__name__} だった）。"
                )
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ParameterError(f"{name}={value!r} は正の有限値でなければならない。")


@dataclass(frozen=True, slots=True)
class ChassisLayout:
    """幾何の導出結果（design.md `#### Layout` Service Interface）。

    ⚠️ **不整合な導出結果は構築できない。** `derive_layout` が作ったものも
    `load_layout` が読み戻したものも同じ不変条件を通る——記録の側が別の値を
    主張できるなら、導出が唯一の箇所にある意味が失われる。

    Attributes:
        base_radius_mm: ホイール配置半径（mm）。`FORMULA`。
        wheel_angles_deg: 各輪の取付角（度）。⚠️ **基準は機体 +x から反時計回り**
            であり、規約そのものは上流の制御ロジックが持つ（要件 3.6）。
        hub_center_to_mount_face_mm: 機体中心から取付面までの半径方向の距離（mm）。
            ⚠️ **寸法パラメータ `base.hub_center_to_mount_face_mm` をそのまま
            読んだ値であり、導出値ではない**（`FORMULA` の第1項）。
        arm_length_mm: 放射状アームの張り出し（mm）。`ARM_LENGTH_FORMULA`。
        vertical: 接地点を原点とする鉛直スタック。
        axial_stack_mm: ギヤボックス端面からの軸方向の積み上がり（mm）。
            順に**ホイール内側面・ホイール中心面・ホイール外側面**であり、
            後の2つは `docs/bom.md §B` が導出済みの 16.8 / 29.6mm と照合できる
            （要件 1.7）。
        tipping: 転倒余裕の見積もり。⚠️ 合否条件ではない。
        provenance: 導出値の出所。⚠️ 入力の最弱を継承する（要件 1.9）。

    Raises:
        GeometryError: 取付角が等間隔でない場合、アームが成立しない場合、
            軸方向スタックが単調増加でない場合、または数が有限でない場合。
        ParameterError: `provenance` が `Provenance` でない場合。
    """

    base_radius_mm: float
    wheel_angles_deg: tuple[float, ...]
    hub_center_to_mount_face_mm: float
    arm_length_mm: float
    vertical: VerticalStack
    axial_stack_mm: tuple[float, float, float]
    tipping: TippingEstimate
    provenance: Provenance

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は項目名と値を添えて拒否する。"""
        base_radius = _require_positive(self.base_radius_mm, _BASE_RADIUS_KEY)
        hub_center = _require_positive(self.hub_center_to_mount_face_mm, _HUB_CENTER_KEY)
        # ⚠️ アームが成立しない配置半径は形状不正である（design.md Postconditions）。
        # 当たり面の下限との突き合わせは寸法パラメータを要するため `derive_layout`
        # が行う。ここが持つのは「長さが正である」という記録の側の不変条件である。
        _require_positive(self.arm_length_mm, _ARM_LENGTH_KEY)

        if not isinstance(self.vertical, VerticalStack):
            raise GeometryError(
                f"{_VERTICAL_KEY}={self.vertical!r} は VerticalStack でなければならない。"
            )
        if not isinstance(self.tipping, TippingEstimate):
            raise GeometryError(
                f"{_TIPPING_KEY}={self.tipping!r} は TippingEstimate でなければならない。"
            )
        if not isinstance(self.provenance, Provenance):
            raise ParameterError(
                f"{_PROVENANCE_KEY}={self.provenance!r} は Provenance でなければならない"
                f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
            )

        if not isinstance(self.wheel_angles_deg, tuple) or len(self.wheel_angles_deg) < 3:
            raise GeometryError(
                f"{_WHEEL_ANGLES_KEY}={self.wheel_angles_deg!r} は3件以上の組で"
                "なければならない（3輪オムニが前提。design.md Preconditions）。"
            )
        angles = [
            _require_finite(angle, f"{_WHEEL_ANGLES_KEY}[{index}]")
            for index, angle in enumerate(self.wheel_angles_deg)
        ]
        step = 360.0 / len(angles)
        for index in range(len(angles) - 1):
            actual = angles[index + 1] - angles[index]
            if not math.isclose(actual, step, rel_tol=0.0, abs_tol=_ABS_TOL):
                raise GeometryError(
                    f"{_WHEEL_ANGLES_KEY}: 隣接角の差 {actual!r} が等配置の "
                    f"{step!r} と一致しない（{index} 番目と {index + 1} 番目）。"
                    "⚠️ 取付角は機体中心まわりに等しい角度間隔で並ぶ（要件 3.2）。"
                )

        if not isinstance(self.axial_stack_mm, tuple) or len(self.axial_stack_mm) != 3:
            raise GeometryError(
                f"{_AXIAL_STACK_KEY}={self.axial_stack_mm!r} は3件の組でなければならない"
                "（ホイール内側面・中心面・外側面）。"
            )
        axial = [
            _require_positive(value, f"{_AXIAL_STACK_KEY}[{index}]")
            for index, value in enumerate(self.axial_stack_mm)
        ]
        for index in range(len(axial) - 1):
            if axial[index + 1] <= axial[index]:
                raise GeometryError(
                    f"{_AXIAL_STACK_KEY}: {axial[index + 1]!r} は "
                    f"{axial[index]!r} より大きくなければならない"
                    "（ギヤボックス端面から外側へ順に積み上がる）。"
                )

        if base_radius <= hub_center:
            raise GeometryError(
                f"{_BASE_RADIUS_KEY}={base_radius!r} は "
                f"{_HUB_CENTER_KEY}={hub_center!r} より大きくなければならない"
                f"（{FORMULA}。取付面からホイール中心までの距離は正である）。"
            )


# ---------------------------------------------------------------------------
# 導出
# ---------------------------------------------------------------------------


def _provenance_of(chassis: ChassisParams, path: str) -> Provenance:
    """`path` の実効的な出所を返す（表に現れないパスは仮値）。

    `ChassisParams.provenance` はキー集合が `PARAMETER_PATHS` と一致することを
    構築時に保証しているため通常は必ず引けるが、⚠️ **既定を `MEASURED` にしない**
    ——「表に無い」ことは「測った」ことではない（`config._canonical_payload` と
    上流 `tolerance._provenance_of` が採る規約と同一である）。
    """
    if path not in PARAMETER_PATHS:
        raise ParameterError(
            f"{path!r} は PARAMETER_PATHS のパス文字列と一致しない"
            "（導出の入力は寸法パラメータの単一の正を指す）。"
        )
    return chassis.provenance.get(path, Provenance.ASSUMED)


def _mass_item_paths(chassis: ChassisParams) -> tuple[str, ...]:
    """合成重心の見積もりに実際に参加する搭載物のパスを返す。

    ⚠️ **未決の搭載物は並びに現れない**（`ChassisParams.mass_items()` の規則）。
    参加していない項目の出所を継承すると、決まっていない値が導出値の出所を
    引き下げる——「測っていない」ではなく「そもそも入力ではない」ためである。
    """
    names = {item.name for item in chassis.mass_items()}
    paths: list[str] = []
    for name, prefix in (
        ("battery", "battery"),
        ("board", "board"),
        ("power_terminal_block", "power.terminal_block"),
    ):
        if name not in names:
            continue
        if prefix == "power.terminal_block":
            paths.extend(
                ["power.terminal_block_mass_g", "power.terminal_block_hold_height_mm"]
            )
        else:
            paths.extend([f"{prefix}.mass_g", f"{prefix}.hold_height_mm"])
    return tuple(paths)


def _cog_height_mm(chassis: ChassisParams) -> float:
    """搭載物の質量で重み付けした保持高さの平均を返す（要件 7.8）。

    ⚠️ **構造材の質量を算入しない。** 算入するには各部品の体積が要り、それは
    形状ライブラリ（`shapes`）が持つ——本モジュールは標準ライブラリだけで動く層
    である。したがってこの値は「搭載物の合成重心」であり、機体全体の重心ではない。
    実測は要件 10.2 が `assembly` に求めている。
    """
    items = chassis.mass_items()
    if not items:
        raise GeometryError(
            "合成重心の見積もりに用いる搭載物が1件も無い"
            "（要件 7.8: 各搭載物の質量と保持高さを記録する）。"
        )
    total_mass_g = math.fsum(item.mass_g for item in items)
    if total_mass_g <= 0.0:
        raise GeometryError(
            f"搭載物の質量の合計 {total_mass_g!r}g は正でなければならない。"
        )
    moment = math.fsum(item.mass_g * item.hold_height_mm for item in items)
    height = moment / total_mass_g
    if height <= 0.0:
        raise GeometryError(
            f"合成重心の高さ {height!r}mm は正でなければならない"
            "（搭載物がすべて接地面上にある配置では転倒余裕を見積もれない）。"
        )
    return height


def _effective_rolling_radius_mm(nominal_rolling_radius_mm: float) -> float:
    """実効転がり半径を返す（要件 10.3 / design.md `#### Layout` Implementation Notes）。

    ⚠️ **観測値を差し込む点はここ1箇所だけである。** design.md は「観測があれば
    `measurements.json` の代表値、無ければ公称値の半分」と定めるが、観測記録は
    タスク 2.4 まで存在しないため、現時点では公称値の半分をそのまま返す。

    ⚠️ **観測の読み手をここへ置かない**——`assembly` は本モジュールの右側の層で
    あり、import すれば依存方向が逆になる（モジュール docstring）。タスク 2.4 は
    代表値を**引数として**受け取る形へこの関数を広げるだけでよく、`derive_layout`
    の積み上げそのものを組み替える必要はない——鉛直スタックの全高さは、この戻り値
    と公称値との差（荷重による縮み）から組み上がっているためである。
    """
    return nominal_rolling_radius_mm


def derive_layout(params: ResolvedParams) -> ChassisLayout:
    """寸法パラメータから幾何を導出する（要件 3.1-3.6, 3.9, 4.1, 7.8）。

    ⚠️ **ホイール配置半径の式はここにしか無い**（tasks.md タスク 2.1）。
    下流は `ChassisLayout` を消費するのであって、同じ式を書き直さない。

    Args:
        params: `config.load_params()` の戻り値。⚠️ 上流の値（造形制約・継手方針・
            ゴミ箱の採寸値）は本関数の入力に**入らない**——幾何は本 Spec 固有の
            寸法だけから決まり、整備スタンドの設計入力を配置半径と現物採寸値に
            限れる（要件 5.2）ための性質である。

    Returns:
        導出結果。`provenance` は入力の最弱を継承する。

    Raises:
        GeometryError: アームが成立しない場合（長さが正でない、または接合部の
            当たり面を確保できない）、鉛直スタックの高さが逆転する場合、
            または導出値が有限でない場合。
        ParameterError: 導出の入力に指定したパスが `PARAMETER_PATHS` に無い場合。
    """
    chassis = params.chassis
    bracket = chassis.bracket
    base = chassis.base

    # ⚠️ **読むだけである。** 「機体中心 → 取付面」は本 Spec が決める唯一の設計
    # 変数であり（docs/bom.md §B の OQ-07）、その決定はタスク 5.4 が持つ。ここで
    # 他の寸法から式で組み立てると、決めるべき値が設定ファイルの外へ移り、値を
    # 書き換えるだけの再導出（要件 1.5）が成り立たなくなる。
    hub_center_to_mount_face_mm = base.hub_center_to_mount_face_mm
    base_radius_mm = (
        hub_center_to_mount_face_mm + bracket.mount_face_to_wheel_center_mm
    )
    arm_length_mm = base_radius_mm - base.hub_outer_diameter_mm / 2.0

    # ⚠️ **アームが成立しない配置半径はここで拒否する**（要件 3.10 の前提 /
    # design.md `#### Layout` Validation）。当たり面の下限も幅も正であるため
    # `minimum_arm_length_mm` は必ず正であり、この検査は「長さが正であること」
    # （design.md Postconditions）を含む——正でない長さは必ず下限を下回る。
    minimum_arm_length_mm = (
        chassis.joint_local.min_bearing_area_mm2 / base.arm_width_mm
    )
    if arm_length_mm < minimum_arm_length_mm:
        raise GeometryError(
            f"{_ARM_LENGTH_KEY}={arm_length_mm!r} は "
            f"min_bearing_area_mm2={chassis.joint_local.min_bearing_area_mm2!r} と "
            f"arm_width_mm={base.arm_width_mm!r} が要求する最小長さ "
            f"{minimum_arm_length_mm!r}mm を "
            f"{minimum_arm_length_mm - arm_length_mm!r}mm 下回る"
            "（ハブとの接合部の当たり面を確保できない配置半径は成立しない。要件 3.10）。"
        )

    step_deg = 360.0 / base.wheel_count
    wheel_angles_deg = tuple(
        base.first_wheel_angle_deg + index * step_deg
        for index in range(base.wheel_count)
    )

    nominal_rolling_radius_mm = chassis.wheel.nominal_diameter_mm / 2.0
    effective_rolling_radius_mm = _effective_rolling_radius_mm(nominal_rolling_radius_mm)

    # ⚠️ **取付面の高さは定数ではない。** `bracket.mount_face_to_contact_mm` は
    # ホイールを付けた状態で測った「取付面 → 接地点」であり、⚠️ **公称の転がり
    # 半径をすでに含んでいる**（docs/bom.md §B:「垂直方向では 60.0 − 30 ＝ 30.0mm
    # が取付面から車軸までの高さになり整合する」）。したがって荷重で転がり半径が
    # δ 縮めば機体全体が δ 低く座り、取付面も締結の下端も δ 下がる（要件 4.7,
    # 10.3）。ここを実測距離そのままの定数にすると、床との隙間の5部位のうち
    # **3部位が実効転がり半径に追随せず**、「隙間は足りている」という誤った判定が
    # 残る（design.md `#### Clearance`「隙間はすべて VerticalStack から算出される
    # ため、実効転がり半径が変われば自動で追随する」が偽になる）。
    # ⚠️ 補正項は「公称 − 実効」であるため、観測記録が入るまでは 0 であり、
    # 取付面高さはちょうど実測距離に等しい——要件 4.1（「モータ取付面から接地点
    # までの実測距離から導出する」）が定める**出所**はそのまま保たれる
    # （4.1 が固定するのは出所であって、荷重下でも高さが動かないことではない）。
    load_compression_mm = nominal_rolling_radius_mm - effective_rolling_radius_mm
    mount_face_height_mm = bracket.mount_face_to_contact_mm - load_compression_mm
    vertical = VerticalStack(
        effective_rolling_radius_mm=effective_rolling_radius_mm,
        axle_center_height_mm=effective_rolling_radius_mm,
        motor_body_bottom_height_mm=(
            effective_rolling_radius_mm - chassis.motor.body_diameter_mm / 2.0
        ),
        mount_face_height_mm=mount_face_height_mm,
        # ⚠️ 締結の下端は取付面から突出量だけ下がった位置である。取付面が実効
        # 転がり半径へ追随する以上、ここも自動で追随する（同じ補正を2度書かない）。
        fastener_bottom_height_mm=(
            mount_face_height_mm - chassis.clearance.fastener_protrusion_mm
        ),
    )

    flange_thickness_mm = chassis.hub.flange_thickness_mm
    half_wheel_width_mm = chassis.wheel.width_mm / 2.0
    axial_stack_mm = (
        flange_thickness_mm,
        flange_thickness_mm + half_wheel_width_mm,
        flange_thickness_mm + 2.0 * half_wheel_width_mm,
    )

    cog_height_mm = _cog_height_mm(chassis)
    tipping = TippingEstimate(
        accel_limit_mm_s2=GRAVITY_MM_S2 * base_radius_mm / (2.0 * cog_height_mm),
        cog_height_mm=cog_height_mm,
        is_pass_criterion=False,
        note=TIPPING_NOTE,
    )

    paths = _LAYOUT_INPUT_PATHS + _mass_item_paths(chassis)
    provenance = weakest_provenance(*(_provenance_of(chassis, path) for path in paths))

    return ChassisLayout(
        base_radius_mm=base_radius_mm,
        wheel_angles_deg=wheel_angles_deg,
        hub_center_to_mount_face_mm=hub_center_to_mount_face_mm,
        arm_length_mm=arm_length_mm,
        vertical=vertical,
        axial_stack_mm=axial_stack_mm,
        tipping=tipping,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# 導出記録の直列化
# ---------------------------------------------------------------------------


def _rounded(value: float) -> float:
    """記録へ書き出す数を `_ROUND_DIGITS` で丸める。"""
    return round(float(value), _ROUND_DIGITS)


def _record_inputs(layout: ChassisLayout) -> list[dict[str, object]]:
    """記録に載せる入力（`FORMULA` の2項ちょうど）を組み立てる。

    ⚠️ **入力は `ChassisLayout` から読み戻せる量だけで構成する。** design.md が
    定める `dump_layout` の引数は導出結果だけであり、寸法パラメータを受け取らない
    ——記録と導出結果が食い違わないためには、記録が導出結果から一意に決まる必要が
    ある。各入力の**出所**は `dimensions.json` の `provenance` 表が単一の正であり、
    ここへ複製しない（要件 1.3 と同じ「同じ値を2箇所に持たない」規律）。
    """
    hub_center_mm = layout.hub_center_to_mount_face_mm
    return [
        {_NAME_KEY: REQUIRED_INPUT_NAMES[0], _VALUE_KEY: _rounded(hub_center_mm)},
        {
            _NAME_KEY: REQUIRED_INPUT_NAMES[1],
            _VALUE_KEY: _rounded(layout.base_radius_mm - hub_center_mm),
        },
    ]


def _to_document(layout: ChassisLayout) -> dict[str, object]:
    """`layout` を記録の形へ写す。"""
    return {
        _SCHEMA_VERSION_KEY: SCHEMA_VERSION,
        _FORMULA_KEY: FORMULA,
        _INPUTS_KEY: _record_inputs(layout),
        _ASSUMPTIONS_KEY: list(ASSUMPTIONS),
        _PROVENANCE_KEY: layout.provenance.value,
        _BASE_RADIUS_KEY: _rounded(layout.base_radius_mm),
        _WHEEL_ANGLES_KEY: [_rounded(angle) for angle in layout.wheel_angles_deg],
        _HUB_CENTER_KEY: _rounded(layout.hub_center_to_mount_face_mm),
        _ARM_LENGTH_KEY: _rounded(layout.arm_length_mm),
        _VERTICAL_KEY: {
            _EFFECTIVE_ROLLING_RADIUS_KEY: _rounded(
                layout.vertical.effective_rolling_radius_mm
            ),
            _AXLE_CENTER_KEY: _rounded(layout.vertical.axle_center_height_mm),
            _MOTOR_BODY_BOTTOM_KEY: _rounded(layout.vertical.motor_body_bottom_height_mm),
            _MOUNT_FACE_HEIGHT_KEY: _rounded(layout.vertical.mount_face_height_mm),
            _FASTENER_BOTTOM_KEY: _rounded(layout.vertical.fastener_bottom_height_mm),
        },
        _AXIAL_STACK_KEY: [_rounded(value) for value in layout.axial_stack_mm],
        _TIPPING_KEY: {
            _ACCEL_LIMIT_KEY: _rounded(layout.tipping.accel_limit_mm_s2),
            _COG_HEIGHT_KEY: _rounded(layout.tipping.cog_height_mm),
            _IS_PASS_CRITERION_KEY: False,
            _NOTE_KEY: layout.tipping.note,
        },
    }


def dump_layout(layout: ChassisLayout, path: Path) -> None:
    """`layout` を導出記録として `path` へ書き出す（要件 3.4, 11.7）。

    整形は `config.dump_params` に揃える（**インデント2・キー整列・末尾改行・LF**）。
    ⚠️ **LF は `.gitattributes` の `configs/chassis_mechanism/*.json text eol=lf`
    と対で成立する**——本関数が書くバイト列が git のチェックアウト内容と同一で
    あるため、値が変わっていなければ `git status` は変更を報告しない。

    Args:
        layout: 書き出す導出結果。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(layout),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _read_document(path: Path) -> Mapping[str, object]:
    """`path` を UTF-8 テキストとして読み、JSON オブジェクトとして返す。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{path}: 導出記録を読み込めない: {exc}") from exc
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{path}: JSON として解析できない: {exc}") from exc
    if not isinstance(document, dict):
        raise ParameterError(
            f"{path}: オブジェクト（{{...}}）を期待したが "
            f"{type(document).__name__} だった。"
        )
    return document


def _reject_unknown_and_missing(
    data: Mapping[str, object], allowed: frozenset[str], label: str
) -> None:
    """未知キーと欠損キーを、いずれも項目名を示して拒否する（`config` と同形）。"""
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ParameterError(
            f"{label}: 未知のキー {unknown!r}。"
            f"指定できるのは {sorted(allowed)!r} のみである。"
        )
    missing = sorted(allowed - set(data))
    if missing:
        raise ParameterError(
            f"{label}: 必須のキーが欠けている {missing!r}"
            "（欠けている項目を既定値で埋めない）。"
        )


def _number(data: Mapping[str, object], key: str, label: str) -> float:
    """`data[key]` を有限な数として取り出す。"""
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(
            f"{label}.{key}={value!r} は数値でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ParameterError(f"{label}.{key}={value!r} は有限でなければならない。")
    return number


def _numbers(data: Mapping[str, object], key: str, label: str) -> tuple[float, ...]:
    """`data[key]` を有限な数の並びとして取り出す。"""
    value = data[key]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ParameterError(
            f"{label}.{key}={value!r} は数の並びでなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return tuple(
        _number({"item": item}, "item", f"{label}.{key}[{index}]")
        for index, item in enumerate(value)
    )


def _object(data: Mapping[str, object], key: str, label: str) -> Mapping[str, object]:
    """`data[key]` を JSON オブジェクトとして取り出す。"""
    value = data[key]
    if not isinstance(value, dict):
        raise ParameterError(
            f"{label}.{key}: オブジェクト（{{...}}）を期待したが "
            f"{type(value).__name__} だった。"
        )
    return value


def load_layout(path: Path | None = None) -> ChassisLayout:
    """導出記録を読み戻す（要件 11.1, 11.5）。

    ⚠️ **記録は導出の写しであり、独立に編集してよい自由記述ではない。**
    未知キー・欠損・型不正に加えて、**記録された入力から導かれる値と結果が
    食い違う記録**、**式や前提が書き換えられた記録**、**版が違う記録**を拒否する。
    ここを素通しにすると、導出が唯一の箇所にある意味が失われる——記録の側が
    別の値を主張できるなら、導出は2箇所にあるのと変わらない。

    Args:
        path: 読み込む記録。`None` なら `DEFAULT_LAYOUT_PATH`。

    Returns:
        読み戻した導出結果。`derive_layout` の戻り値と同じ不変条件を満たす。

    Raises:
        ParameterError: 記録が読めない、構造が不正、または導出と食い違う場合。
        GeometryError: 記録された幾何が不変条件に反する場合。
    """
    target = DEFAULT_LAYOUT_PATH if path is None else path
    label = str(target)
    document = _read_document(target)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)

    version = document[_SCHEMA_VERSION_KEY]
    if version != SCHEMA_VERSION:
        raise ParameterError(
            f"{label}.{_SCHEMA_VERSION_KEY}={version!r} は "
            f"{SCHEMA_VERSION!r} と一致しなければならない。"
        )
    if document[_FORMULA_KEY] != FORMULA:
        raise ParameterError(
            f"{label}.{_FORMULA_KEY}={document[_FORMULA_KEY]!r} は導出式 "
            f"{FORMULA!r} と一致しない（導出はプロジェクト内の唯一の箇所で行う）。"
        )
    assumptions = document[_ASSUMPTIONS_KEY]
    if not isinstance(assumptions, list) or tuple(assumptions) != ASSUMPTIONS:
        raise ParameterError(
            f"{label}.{_ASSUMPTIONS_KEY}: 前提は {list(ASSUMPTIONS)!r} と"
            "一致しなければならない（前提の落ちた記録を受け付けない）。"
        )

    provenance_value = document[_PROVENANCE_KEY]
    if provenance_value not in _PROVENANCE_VALUES:
        raise ParameterError(
            f"{label}.{_PROVENANCE_KEY}={provenance_value!r} は "
            f"{sorted(_PROVENANCE_VALUES)!r} のいずれかでなければならない。"
        )
    provenance = _PROVENANCE_VALUES[str(provenance_value)]

    inputs = document[_INPUTS_KEY]
    if not isinstance(inputs, list) or len(inputs) != len(REQUIRED_INPUT_NAMES):
        raise ParameterError(
            f"{label}.{_INPUTS_KEY}: 入力は {list(REQUIRED_INPUT_NAMES)!r} の"
            f"{len(REQUIRED_INPUT_NAMES)}件ちょうどでなければならない"
            "（式に現れない量を根拠として書き足さない）。"
        )
    input_values: list[float] = []
    for index, item in enumerate(inputs):
        item_label = f"{label}.{_INPUTS_KEY}[{index}]"
        if not isinstance(item, dict):
            raise ParameterError(
                f"{item_label}: オブジェクト（{{...}}）を期待したが "
                f"{type(item).__name__} だった。"
            )
        _reject_unknown_and_missing(item, _INPUT_KEYS, item_label)
        if item[_NAME_KEY] != REQUIRED_INPUT_NAMES[index]:
            raise ParameterError(
                f"{item_label}.{_NAME_KEY}={item[_NAME_KEY]!r} は "
                f"{REQUIRED_INPUT_NAMES[index]!r} でなければならない。"
            )
        input_values.append(_number(item, _VALUE_KEY, item_label))

    base_radius_mm = _number(document, _BASE_RADIUS_KEY, label)
    expected_base_radius_mm = input_values[0] + input_values[1]
    if not math.isclose(
        base_radius_mm, expected_base_radius_mm, rel_tol=0.0, abs_tol=_ABS_TOL
    ):
        raise ParameterError(
            f"{label}.{_BASE_RADIUS_KEY}={base_radius_mm!r} は記録された入力から"
            f"導かれる値 {expected_base_radius_mm!r} と一致しない"
            f"（{FORMULA} に {input_values[0]!r} と {input_values[1]!r} を与えた結果）。"
        )
    hub_center_to_mount_face_mm = _number(document, _HUB_CENTER_KEY, label)
    if not math.isclose(
        hub_center_to_mount_face_mm, input_values[0], rel_tol=0.0, abs_tol=_ABS_TOL
    ):
        raise ParameterError(
            f"{label}.{_HUB_CENTER_KEY}={hub_center_to_mount_face_mm!r} は記録された"
            f"入力 {input_values[0]!r} と一致しない。"
        )

    vertical_document = _object(document, _VERTICAL_KEY, label)
    vertical_label = f"{label}.{_VERTICAL_KEY}"
    _reject_unknown_and_missing(vertical_document, _VERTICAL_KEYS, vertical_label)
    vertical = VerticalStack(
        effective_rolling_radius_mm=_number(
            vertical_document, _EFFECTIVE_ROLLING_RADIUS_KEY, vertical_label
        ),
        axle_center_height_mm=_number(
            vertical_document, _AXLE_CENTER_KEY, vertical_label
        ),
        motor_body_bottom_height_mm=_number(
            vertical_document, _MOTOR_BODY_BOTTOM_KEY, vertical_label
        ),
        mount_face_height_mm=_number(
            vertical_document, _MOUNT_FACE_HEIGHT_KEY, vertical_label
        ),
        fastener_bottom_height_mm=_number(
            vertical_document, _FASTENER_BOTTOM_KEY, vertical_label
        ),
    )

    tipping_document = _object(document, _TIPPING_KEY, label)
    tipping_label = f"{label}.{_TIPPING_KEY}"
    _reject_unknown_and_missing(tipping_document, _TIPPING_KEYS, tipping_label)
    note = tipping_document[_NOTE_KEY]
    if not isinstance(note, str):
        raise ParameterError(
            f"{tipping_label}.{_NOTE_KEY}={note!r} は文字列でなければならない。"
        )
    tipping = TippingEstimate(
        accel_limit_mm_s2=_number(tipping_document, _ACCEL_LIMIT_KEY, tipping_label),
        cog_height_mm=_number(tipping_document, _COG_HEIGHT_KEY, tipping_label),
        is_pass_criterion=tipping_document[_IS_PASS_CRITERION_KEY],
        note=note,
    )

    axial_values = _numbers(document, _AXIAL_STACK_KEY, label)
    if len(axial_values) != 3:
        raise ParameterError(
            f"{label}.{_AXIAL_STACK_KEY}: 3件の並びでなければならない"
            f"（{len(axial_values)} 件だった）。"
        )

    return ChassisLayout(
        base_radius_mm=base_radius_mm,
        wheel_angles_deg=_numbers(document, _WHEEL_ANGLES_KEY, label),
        hub_center_to_mount_face_mm=hub_center_to_mount_face_mm,
        arm_length_mm=_number(document, _ARM_LENGTH_KEY, label),
        vertical=vertical,
        axial_stack_mm=(axial_values[0], axial_values[1], axial_values[2]),
        tipping=tipping,
        provenance=provenance,
    )
