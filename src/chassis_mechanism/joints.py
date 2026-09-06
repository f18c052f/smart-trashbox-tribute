"""接合部の定義・当たり面と造形姿勢・締結部品の数え上げ（design.md
`#### Joints` / 要件 2.1, 2.2, 2.6, 2.7, 2.8, 2.9, 2.10, 3.10, 5.6, 6.8）。

⚠️ **OQ-09（締結部品の必要数と長さ）の決着はここが唯一の置き場所である**
（design.md「Boundary Commitments」）。

**接合部の一覧は導出であり、書き写しではない**（要件 2.1 / tasks.md タスク 2.3
「⚠️ **手書きの一覧を設定ファイルに持たない**」）。件数は
`ChassisLayout.wheel_angles_deg`・`segment_counts()`・`stand.leg_count`・
`adapter.retention_point_count` から決まり、`joint-schedule.json` は
`derive_fastener_schedule` の**写し**である（`load_fastener_schedule` が導出と
食い違う記録を拒否する）。⚠️ **記録を入力として読む経路をここへ作らない**——
作れば「設定ファイルに手で書いた一覧」と同じものになる。

**荷重を受ける要素と位置決めのみを担う要素を型の上で分ける**（要件 2.7 /
design.md 決定 3）。`JointSpec` は `bolt_count` / `insert_count`（荷重を受ける
貫通ボルトと金属インサート）と `dowel_count`（位置決め専用のダボ）を別の
フィールドに持ち、⚠️ **`bearing_area_mm2` はボルト座だけから算出する**——
`BEARING_AREA_FORMULA` にダボは1度も現れない（上流 `check_joint` の契約と同じ）。
締結部品の一覧の側でも、`FASTENER_KINDS` に `"dowel"` が無いため
**ダボを表す行は構築できない**（design.md `#### Joints` Invariants:
「⚠️ **ダボは `lines` に現れない**」）。ダボは造形で作る位置決め要素であり、
購入する締結部品ではない。

**当たり面は上流の下限と本 Spec のより厳しい下限の両方を満たす**（要件 2.9 /
design.md Postconditions）。上流の `check_joint` を実際に通したうえで、
モータ反力を受ける接合部には `LocalJointLimits.min_bearing_area_mm2` を課す。
⚠️ **上流の下限値をここへ書き写さない**——`params.ChassisParams.
validate_against_upstream` が「本 Spec の下限は上流以上」を既に保証しており
（`config.load_params` が呼ぶ）、本モジュールが行うのは**実際の当たり面**を
両方の下限へ突き合わせることである。

**接合面の法線が積層方向（Z）と一致する配置は形状不正である**（要件 2.8 /
A-5 / design.md 決定 3）。FDM は層間強度が XY 面内強度を大きく下回るため、
接合面の法線が積層方向を向く配置は層間剥離モードで落ちる。`JointSpec` は
`print_normal_axis` を持ち、⚠️ **`"z"` は `GeometryError` で拒否される**
（メッセージは接合部の名と軸を持つ）。
⚠️ **この軸は造形座標系の軸である。** 組み上がった機体では締結の向きが鉛直に
なる接合部（アダプタの座、トレイ）があるが、それは「その部品を横倒しで造形
する」ことで避ける——姿勢は設計の決定であり、`print_normal_axis` はその決定を
接合部ごとに表明したものである（要件 2.8 の「各接合部の造形姿勢を記録する」）。

**分割数の導出は部品の種類で分かれる**（要件 2.1 / research.md「Decision:
分割の導出を部品の種類で分ける」）:

- **円環部品**（ゴミ箱固定アダプタ）→ 上流 `required_segment_count` を用いる
- **位相が決まっている部品**（駆動ベース・配線ガイド・整備スタンド）→
  分割数は輪数・脚数から従属し、`check_envelope` が造形可能性の関門になる

⚠️ **円環でない部品を円環として近似しない。** 上流の docstring 自身が
「半径方向の広がりは分割数を増やしても縮まない」と述べており、円環でない形へ
当てれば**過大な分割数が「正しい導出」の顔をして返る**。

**当たり面は寸法パラメータから解析的に算出する**（design.md `#### Joints`
Risks）。⚠️ **本モジュールは build123d を import できない**（依存方向の左側の
層である）ため、形状から面積を採れない。したがって
`BEARING_AREA_FORMULA`（ボルト座）と `CONTACT_BEARING_AREA_FORMULA`（台上の
拘束）で算出し、⚠️ **実形状との一致は `test_chassis_invariants.py`（`cad`
extra、タスク 4.4）が検査する**。

**締結部品の長さは「積み上がり厚さ ＋ インサート長 ＋ 余裕」から導出する**
（要件 2.10 / `FASTENER_LENGTH_FORMULA`）。⚠️ **インサート長は上流
`JointPolicy.insert_length_mm` を使い、数値を書き写さない。**
⚠️ **「余裕」は `joint_local.fastener_length_margin_mm` である**——締結の向きに
沿った量として本 Spec が専用に持つ寸法パラメータである。
⚠️ **`clearance.fastener_protrusion_mm` を流用しない。** あちらは締結部品の
**下方**への突出量であり、床との隙間（要件 4.2, 4.3 / `layout`）の入力である。
導出されるボルトは半径方向 `x` か接線方向 `y` を向いており、下方を向くものは
1本も無い。皿頭を選べば `0.0` が正当な値になる量であるため、兼ねさせれば床に
ついての判断が調達するボルトの長さを黙って縮める。

**アーム↔付属金属ブラケットの接合部は、まだ導出できない。**
⚠️ `BracketMeasurements` に**取付フランジの厚さ**が無く、積み上がり厚さを
組み立てられないためである（要件 1.6 が求める実測が済んでいない項目である）。
⚠️ **推定値で埋めない**——埋めればブラケットのボルト長が実測に追随しない。
厚さが寸法パラメータへ入った時点で `_JOINT_FAMILIES` に1家族を足せばよく、
そのとき初めて `nut` の行が現れる（金属ブラケット側にはインサートを入れられ
ないため）。現在の導出では全ボルトがインサートで受けられているため、
`nut` の合計は 0 であり行に現れない。

読み書きの規律は `config.py` / `layout.py` に揃える（**あらゆる階層で未知キーを
拒否する**、項目名を示す、欠損を既定値で埋めない、LF・インデント2・キー整列・
末尾改行）。記録形式の版は `config.SCHEMA_VERSION` を共有する。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from catch_mechanism import Envelope, check_envelope, check_joint, required_segment_count

from chassis_mechanism.config import SCHEMA_VERSION, ResolvedParams, parameters_digest
from chassis_mechanism.errors import ConsistencyError, GeometryError, ParameterError
from chassis_mechanism.layout import ChassisLayout

__all__ = [
    "DEFAULT_JOINT_SCHEDULE_PATH",
    "BOSS_DIAMETER_FACTOR",
    "MIN_BOLTS_PER_FASTENED_JOINT",
    "FASTENER_KINDS",
    "LAYER_NORMAL_AXIS",
    "ALLOWED_PRINT_NORMAL_AXES",
    "BEARING_AREA_FORMULA",
    "CONTACT_BEARING_AREA_FORMULA",
    "FASTENER_LENGTH_FORMULA",
    "ADAPTER_OUTER_DIAMETER_FORMULA",
    "ANNULAR_PART_NAMES",
    "PHASE_PART_NAMES",
    "ASSUMPTIONS",
    "ARM_JOINT_LAP_LENGTH_FORMULA",
    "arm_joint_lap_length_mm",
    "JointSpec",
    "FastenerLine",
    "FastenerSchedule",
    "derive_joints",
    "segment_counts",
    "derive_fastener_schedule",
    "dump_fastener_schedule",
    "load_fastener_schedule",
]


DEFAULT_JOINT_SCHEDULE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "chassis_mechanism"
    / "joint-schedule.json"
)
"""導出記録の既定パス（design.md「Data Models」）。

`layout.DEFAULT_LAYOUT_PATH` と同じく `parents[2]` がリポジトリルートである。
⚠️ **設計入力の隣に置くが、設計入力ではない**——この記録は
`derive_fastener_schedule` の写しであり、手で編集する対象ではない。
"""

BOSS_DIAMETER_FACTOR: Final[int] = 2
"""熱圧入インサートの座（ボス）の外径を、インサート外径の何倍に採るか。

⚠️ **寸法ではなく設計規律である。** 熱圧入インサートは周囲の樹脂を溶かして
食い込むため、座の外径をインサート外径の2倍未満にすると壁が薄くなり、締め付け
時に膨らんで割れる（インサート各社の推奨も「ボス外径 ≧ インサート外径の2倍」で
一致している）。⚠️ **ミリメートルの値を持たない**——実際の座の径は上流
`JointPolicy.insert_outer_diameter_mm` に本係数を掛けて決まるため、上流が
インサートを変えれば当たり面も本数も追随する。
"""

MIN_BOLTS_PER_FASTENED_JOINT: Final[int] = 2
"""締結する接合部が持つボルトの最小本数。

⚠️ **1本では接合部が回る。** 当たり面の下限を1本で満たせる場合でも、面内の
回転を止める要素が無ければ接合部として成立しない（位置決めはダボが担うが、
ダボへ荷重を負わせない——要件 2.7）。
"""

_UNSPLIT_SEGMENT_COUNT: Final[int] = 1
"""分割しない部品の分割数（上流 `required_segment_count` の戻り値 1 と同義）。"""

_BOTH_SIDES: Final[int] = 2
"""直径へ足す量が半径ごとに1つずつ効くことを表す係数（⚠️ 寸法ではない）。

`ADAPTER_OUTER_DIAMETER_FORMULA` で用いる。隙間と肉厚は円の**両側**に付くため、
直径には2倍で効く。
"""

_LOCATING_DOWEL_COUNT: Final[int] = 2
"""造形部品どうしの接合部が持つ位置決めダボの本数。

⚠️ **1本では姿勢が決まらない**（軸まわりに回る）。⚠️ **荷重は受けない**——
本数を増やしても `bearing_area_mm2` は動かない（要件 2.7）。購入部品
（金属ブラケット・ゴミ箱・ホイール）との接合部にはダボを置かない。
"""

_NO_DOWELS: Final[int] = 0
_NO_BOLTS: Final[int] = 0

BOLT_KIND: Final[str] = "bolt"
NUT_KIND: Final[str] = "nut"
INSERT_KIND: Final[str] = "insert"

FASTENER_KINDS: Final[tuple[str, ...]] = (BOLT_KIND, NUT_KIND, INSERT_KIND)
"""締結部品の種別と、一覧に現れる順（要件 2.10「ねじ・ナット・熱圧入インサート」）。

⚠️ **`"dowel"` はここに無い。** ダボは造形で作る位置決め要素であり、購入する
締結部品ではない（design.md `#### Joints` Invariants）。種別の集合をここで
閉じているため、ダボの行は `FastenerLine` として**構築できない**。
"""

LAYER_NORMAL_AXIS: Final[str] = "z"
"""積層方向（造形座標系）。⚠️ **接合面の法線がこの軸を向く配置は禁忌**（要件 2.8）。"""

_RADIAL_NORMAL_AXIS: Final[str] = "x"
_TANGENTIAL_NORMAL_AXIS: Final[str] = "y"

ALLOWED_PRINT_NORMAL_AXES: Final[tuple[str, ...]] = (
    _RADIAL_NORMAL_AXIS,
    _TANGENTIAL_NORMAL_AXIS,
)
"""接合面の法線として採れる造形座標系の軸（要件 2.8）。

`x` は半径方向、`y` は接線方向の面である。⚠️ **`z` は含まない**——含めた
時点で、層間剥離で落ちる継手を設計へ通す経路ができる。
"""

BEARING_AREA_FORMULA: Final[str] = (
    "bolt_count * pi / 4 * "
    "((BOSS_DIAMETER_FACTOR * joint.insert_outer_diameter_mm) ** 2 "
    "- joint.through_hole_diameter_mm ** 2)"
)
"""締結する接合部の当たり面（支圧面積）の導出式（要件 2.9 / design.md Risks）。

ボルト1本あたりの当たり面は、インサート座（ボス）の端面から貫通穴を抜いた環で
ある。⚠️ **ダボは1度も現れない**（要件 2.7）。⚠️ **入力はすべて上流
`JointPolicy` である**——本 Spec がミリメートルの値を持たないため、上流が
インサートやボルトを変えれば当たり面と本数が自動で追随する。

⚠️ **本来は形状から採るべき量である**（design.md `#### Joints` Risks）。
`joints` は build123d を import できないため解析的に算出し、実形状との一致は
`test_chassis_invariants.py`（`cad` extra、タスク 4.4）が検査する。
"""

CONTACT_BEARING_AREA_FORMULA: Final[str] = "wheel.width_mm * stand.support_span_mm"
"""整備スタンドの拘束の当たり面の導出式（要件 5.6）。

⚠️ **締結部品を持たない接合部である。** 脚はホイールを両側から挟んで支え
（design.md 決定 5）、モータ反力は**面で**受ける。したがって当たり面はボルト座
ではなくホイールを受ける谷の投影面積であり、`BEARING_AREA_FORMULA` は適用
できない。⚠️ **ここへボルトを置かない**——載せ降ろしを一人で行える（要件 5.9）
ことと、工具なしで機体を台から外せることが設計の前提である。

⚠️ **「台の上で機体が外れない」（要件 5.6）を保証しているのは当たり面の下限では
なく形である。** 脚がホイールを両側から挟む谷の形が拘束を与えているのであって、
ここで算出される面積は下限を大きく上回る（下限の何倍にもなる）ため、下限の検査は
実質的に効いていない。⚠️ **この面積の検査を「外れないことの根拠」と読み違えない**
——形を変えれば（谷を浅くする、脚を片側だけにする）面積が下限を満たしたままでも
機体は外れる。面積の検査が担っているのは、モータ反力を受ける面としての最低限の
広さだけである。
"""

FASTENER_LENGTH_FORMULA: Final[str] = (
    "stack_thickness_mm + joint.insert_length_mm "
    "+ joint_local.fastener_length_margin_mm"
)
"""ボルト長の導出式（要件 2.10 / tasks.md タスク 2.3）。

⚠️ **インサート長は上流 `JointPolicy.insert_length_mm` を読む。数値を書き写さ
ない**——上流がインサートを変えたとき、書き写した側は追随しない。
「余裕」は `joint_local.fastener_length_margin_mm` である。⚠️ **床との隙間の
ための `clearance.fastener_protrusion_mm` ではない**（モジュール docstring の
「締結部品の長さ」を参照）——別の物理量であり、`0.0` が正当な値になる条件も
異なる。積み上がり厚さは接合部ごとに異なり、`_JOINT_FAMILIES` の各行が何を
積んでいるかを記述している。
"""

ARM_JOINT_LAP_LENGTH_FORMULA: Final[str] = (
    "bolt_count * BOSS_DIAMETER_FACTOR * joint.insert_outer_diameter_mm"
)
"""中央部↔モータ取付部の**重ね代**（ハブ板の舌がアームの二股へ差し込まれる
半径方向の長さ、mm）の導出式（要件 2.6, 3.10）。

接合面は接線方向 `y` を法線に持ち（要件 2.8 / A-5）、ボルト座はその面の
**半径方向**へ一列に並ぶ。したがって重ねている長さは「本数 × 座の外径」であり、
⚠️ **これが `_bolt_count` の検査（`count * boss_diameter <= face_width`）が
言っている量そのものである**。

⚠️ **この量は形（`shapes`）と断片の外接箱（`_check_fragment_envelopes`）の
双方が読む。** 舌は中央部の外縁（`base.hub_outer_diameter_mm / 2`）から
この長さだけ**外へ張り出す**ため、中央部の外接箱は公称外径のままではない——
片側ずつ張り出すので直径には2倍で効く。⚠️ **ここを公称外径のままにすると、
「造形可能寸法に収まる」という判定が実際より小さい部品について述べたものになる。**
"""

ADAPTER_OUTER_DIAMETER_FORMULA: Final[str] = (
    "trash_can.bottom_outer_diameter_mm + 2 * "
    "(adapter.seat_clearance_mm + adapter.wall_thickness_mm)"
)
"""ゴミ箱固定アダプタ（円環部品）の外径の導出式（要件 6.1）。

⚠️ **底の外径は上流 `TrashCanMeasurements` が正である**（要件 1.3）。座は底の
外周を隙間ぶんだけ逃がし、その外側に肉厚ぶんの壁が立つ。分割数の導出
（`required_segment_count`）はこの外径を入力とする。
"""

ANNULAR_PART_NAMES: Final[tuple[str, ...]] = ("adapter_segment",)
"""円環部品の名（上流 `required_segment_count` で分割数を導出する。要件 2.1）。"""

PHASE_PART_NAMES: Final[tuple[str, ...]] = (
    "hub_plate",
    "motor_arm",
    "battery_tray",
    "board_tray",
    "cable_guide",
    "service_stand",
)
"""位相が決まっている部品の名（分割数は輪数・脚数から従属する。要件 2.1）。

⚠️ **これらを円環として近似しない。** 円環の等分に載せると、造形面を狭めた
瞬間に過大な分割数が「正しい導出」の顔をして返る（research.md の Decision）。
造形可能性の関門は `check_envelope` である。
"""

ASSUMPTIONS: Final[tuple[str, ...]] = (
    "接合部の一覧は ChassisLayout（輪数）と ChassisParams（脚数・保持箇所の数）と"
    "上流の採寸値（アダプタの分割数）から導出する。⚠️ 手書きの一覧を設定ファイルへ"
    "持たない（要件 2.1 / tasks.md タスク 2.3）。",
    "モータ反力を受ける接合部（中央部↔モータ取付部、整備スタンドの拘束）には"
    "joint_local.min_bearing_area_mm2 を課し、それ以外は上流 JointPolicy の下限を"
    "課す。⚠️ どちらの場合も上流の check_joint を実際に通す（要件 2.9）。",
    "要件 6.8（締結箇所の配置の根拠）: ゴミ箱の底は薄く変形しやすい"
    "（厚さは上流 TrashCanMeasurements.bottom_thickness_mm が正である。"
    "⚠️ 値をここへ書き写さない——要件 6.3 が再実測を求めており、写せばこの根拠が"
    "黙って偽の数を述べることになる）ため、"
    "締結はゴミ箱の底へ穴を開けて点で引くのではなく、⚠️ 底の外周を"
    "アダプタの立ち上がりへ半径方向のクランプで押さえる。保持箇所は"
    "adapter.retention_point_count に従い円周へ等配置し、水平方向と上方向の"
    "拘束を同時に与える（要件 6.5）。ボルトの積み上がりは「クランプの肉厚 ＋"
    "ゴミ箱の底の肉厚」である。",
    "要件 5.6（台上での保持）: 脚はホイールを両側から挟んで受ける（design.md"
    "決定 5）。⚠️ 締結部品を持たない拘束であり、締結部品一覧へ何も足さない——"
    "モータ反力は面で受け、載せ降ろしは工具なしで一人で行える（要件 5.9）。",
    "アーム↔付属金属ブラケットの接合部は、BracketMeasurements に取付フランジの"
    "厚さが無いため導出できない（要件 1.6 の実測が済んでいない）。⚠️ 推定値で"
    "埋めず、実測が入った時点で1家族として足す。現在の導出では全ボルトが"
    "インサートで受けられるため nut の合計は 0 であり、行に現れない。",
    "当たり面は寸法パラメータから解析的に算出した値である（design.md #### Joints"
    "Risks）。⚠️ joints は build123d を import できないため形状から採れない。"
    "実形状との一致は test_chassis_invariants.py（cad extra、タスク 4.4）が検査する。",
)
"""導出に伴う前提と決定の根拠（要件 2.8 の造形姿勢、6.8 の配置の根拠、11.7）。

⚠️ **記録（`joint-schedule.json`）には載らない。** design.md「Data Models」が
定める `joint-schedule.json` の項目は `joints` / `lines` / `parameters_digest` /
`schema_version` の4つであり、根拠はコード側（本定数）と design.md
「機構の決定」節が持つ。⚠️ 記録の構造を増やすことは下流の再検証を要する変更
である（Revalidation Triggers 項目1）。
"""


_ROUND_DIGITS: Final[int] = 9
"""記録へ書き出す数の丸め桁数（`layout._ROUND_DIGITS` と同じ理由・同じ値）。"""

# ⚠️ 許容差の定数を置かない。記録と導出の突き合わせ（`_require_lines_match_joints`）
# は**双方を `_ROUND_DIGITS` で丸めてから完全一致で比べる**——記録は導出の写しで
# あり、近い値ではなく同じ値でなければならない。緩い比較を用意すると、書き出しと
# 読み戻しの間に生じてはならない差を黙って通す経路ができる。

_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_PARAMETERS_DIGEST_KEY: Final[str] = "parameters_digest"
_JOINTS_KEY: Final[str] = "joints"
_LINES_KEY: Final[str] = "lines"

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {_SCHEMA_VERSION_KEY, _PARAMETERS_DIGEST_KEY, _JOINTS_KEY, _LINES_KEY}
)

_NAME_KEY: Final[str] = "name"
_MEMBERS_KEY: Final[str] = "members"
_BOLT_COUNT_KEY: Final[str] = "bolt_count"
_BOLT_LENGTH_KEY: Final[str] = "bolt_length_mm"
_INSERT_COUNT_KEY: Final[str] = "insert_count"
_DOWEL_COUNT_KEY: Final[str] = "dowel_count"
_BEARING_AREA_KEY: Final[str] = "bearing_area_mm2"
_PRINT_NORMAL_AXIS_KEY: Final[str] = "print_normal_axis"
_MIN_BEARING_AREA_KEY: Final[str] = "min_bearing_area_mm2"

_JOINT_KEYS: Final[frozenset[str]] = frozenset(
    {
        _NAME_KEY,
        _MEMBERS_KEY,
        _BOLT_COUNT_KEY,
        _BOLT_LENGTH_KEY,
        _INSERT_COUNT_KEY,
        _DOWEL_COUNT_KEY,
        _BEARING_AREA_KEY,
        _PRINT_NORMAL_AXIS_KEY,
        _MIN_BEARING_AREA_KEY,
    }
)

_DESIGNATION_KEY: Final[str] = "designation"
_KIND_KEY: Final[str] = "kind"
_LENGTH_KEY: Final[str] = "length_mm"
_COUNT_KEY: Final[str] = "count"

_LINE_KEYS: Final[frozenset[str]] = frozenset(
    {_DESIGNATION_KEY, _KIND_KEY, _LENGTH_KEY, _COUNT_KEY}
)

_MEMBER_COUNT: Final[int] = 2


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


def _require_count(value: object, name: str, minimum: int) -> int:
    """`value` が `minimum` 以上の整数であることを要求する（`bool` を除く）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GeometryError(
            f"{name}={value!r} は {minimum} 以上の整数でなければならない。"
        )
    return value


def _require_nonblank(value: object, name: str) -> str:
    """`value` が空白のみでない文字列であることを要求する。"""
    if not isinstance(value, str) or not value.strip():
        raise GeometryError(
            f"{name}={value!r} は空白のみでない文字列でなければならない。"
        )
    return value


# ---------------------------------------------------------------------------
# 導出結果の型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JointSpec:
    """接合部1件（design.md `#### Joints` Service Interface）。

    ⚠️ **不整合な接合部は構築できない。** `derive_joints` が作ったものも
    `load_fastener_schedule` が読み戻したものも同じ不変条件を通る——記録の側が
    別の姿勢や別の当たり面を主張できるなら、導出が唯一の箇所にある意味が失われる。

    Attributes:
        name: 接合部の名。⚠️ **導出で組み立てられる**（部材名と番号）。
        members: 結ぶ2部材の名。造形部品・購入部品のいずれも現れる。
        bolt_count: 荷重を受ける貫通ボルトの本数。0 は締結部品を持たない拘束
            （整備スタンド、要件 5.6）を表す。
        bolt_length_mm: ボルトの長さ（mm）。`FASTENER_LENGTH_FORMULA`。
            ⚠️ `bolt_count == 0` のときに限り 0 である。
        insert_count: 金属ヒートインサートの数。⚠️ **ボルトより多くはならない**
            （インサートはボルトを受ける要素であるため）。
        dowel_count: 位置決めダボの本数。⚠️ **荷重を受けない**——
            `bearing_area_mm2` に算入せず、締結部品一覧にも現れない（要件 2.7）。
        bearing_area_mm2: 荷重を受ける当たり面の面積（mm^2）。
            `BEARING_AREA_FORMULA` / `CONTACT_BEARING_AREA_FORMULA`。
        print_normal_axis: 接合面の法線が向く造形座標系の軸。
            ⚠️ **`"z"`（積層方向）は禁忌**（要件 2.8 / A-5）。
        min_bearing_area_mm2: この接合部へ課される当たり面の下限（mm^2）。
            モータ反力を受ける接合部は本 Spec のより厳しい下限を持つ（要件 2.9）。

    Raises:
        GeometryError: 造形姿勢が積層方向と一致する場合、軸名でない場合、
            当たり面が下限を下回る場合、インサートがボルトより多い場合、
            ボルトと長さの有無が食い違う場合、または数・名が不正な場合。
    """

    name: str
    members: tuple[str, str]
    bolt_count: int
    bolt_length_mm: float
    insert_count: int
    dowel_count: int
    bearing_area_mm2: float
    print_normal_axis: str
    min_bearing_area_mm2: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は接合部の名と値を添えて拒否する。"""
        name = _require_nonblank(self.name, _NAME_KEY)

        if (
            not isinstance(self.members, tuple)
            or len(self.members) != _MEMBER_COUNT
        ):
            raise GeometryError(
                f"{name}.{_MEMBERS_KEY}={self.members!r} は2部材の組でなければ"
                "ならない（接合部は2つの部材を結ぶ）。"
            )
        for index, member in enumerate(self.members):
            _require_nonblank(member, f"{name}.{_MEMBERS_KEY}[{index}]")
        if self.members[0] == self.members[1]:
            raise GeometryError(
                f"{name}.{_MEMBERS_KEY}={self.members!r} は互いに異なる2部材で"
                "なければならない（部材が自身と接合することはない）。"
            )

        # ⚠️ **造形姿勢の検査を最初に置く理由は無いが、落とすことはできない。**
        # 積層方向と一致する接合面は層間剥離モードで落ちる（A-5 / 決定 3）。
        if self.print_normal_axis == LAYER_NORMAL_AXIS:
            raise GeometryError(
                f"{name}.{_PRINT_NORMAL_AXIS_KEY}={self.print_normal_axis!r} は"
                f"積層方向（{LAYER_NORMAL_AXIS!r}）と一致している"
                "（要件 2.8 / A-5: 接合面の法線が積層方向と一致する配置は禁忌である。"
                "FDM は層間強度が面内強度を大きく下回るため、この向きの接合部は"
                "層間剥離で落ちる。部品の造形姿勢を変えて "
                f"{list(ALLOWED_PRINT_NORMAL_AXES)!r} のいずれかにすること）。"
            )
        if self.print_normal_axis not in ALLOWED_PRINT_NORMAL_AXES:
            raise GeometryError(
                f"{name}.{_PRINT_NORMAL_AXIS_KEY}={self.print_normal_axis!r} は"
                f"{list(ALLOWED_PRINT_NORMAL_AXES)!r} のいずれかでなければならない"
                "（造形座標系の軸で接合面の法線を表明する）。"
            )

        bolt_count = _require_count(self.bolt_count, f"{name}.{_BOLT_COUNT_KEY}", 0)
        insert_count = _require_count(
            self.insert_count, f"{name}.{_INSERT_COUNT_KEY}", 0
        )
        _require_count(self.dowel_count, f"{name}.{_DOWEL_COUNT_KEY}", 0)
        if insert_count > bolt_count:
            raise GeometryError(
                f"{name}.{_INSERT_COUNT_KEY}={insert_count!r} は "
                f"{_BOLT_COUNT_KEY}={bolt_count!r} 以下でなければならない"
                "（インサートは貫通ボルトを受ける要素であり、単独では荷重を受けない）。"
            )

        bolt_length_mm = _require_finite(
            self.bolt_length_mm, f"{name}.{_BOLT_LENGTH_KEY}"
        )
        if bolt_count > 0 and bolt_length_mm <= 0.0:
            raise GeometryError(
                f"{name}.{_BOLT_LENGTH_KEY}={bolt_length_mm!r} は "
                f"{_BOLT_COUNT_KEY}={bolt_count!r} 本のボルトを持つ接合部では"
                f"正でなければならない（{FASTENER_LENGTH_FORMULA}）。"
            )
        if bolt_count == 0 and bolt_length_mm != 0.0:
            raise GeometryError(
                f"{name}.{_BOLT_LENGTH_KEY}={bolt_length_mm!r} は"
                "ボルトを持たない接合部では 0 でなければならない"
                "（締結部品を持たない拘束は一覧へ何も足さない）。"
            )
        if bolt_count == 0 and insert_count > 0:
            raise GeometryError(
                f"{name}.{_INSERT_COUNT_KEY}={insert_count!r} は"
                "ボルトを持たない接合部では 0 でなければならない。"
            )

        bearing_area_mm2 = _require_positive(
            self.bearing_area_mm2, f"{name}.{_BEARING_AREA_KEY}"
        )
        min_bearing_area_mm2 = _require_positive(
            self.min_bearing_area_mm2, f"{name}.{_MIN_BEARING_AREA_KEY}"
        )
        if bearing_area_mm2 < min_bearing_area_mm2:
            raise GeometryError(
                f"{name}.{_BEARING_AREA_KEY}={bearing_area_mm2!r} は "
                f"{_MIN_BEARING_AREA_KEY}={min_bearing_area_mm2!r} を "
                f"{min_bearing_area_mm2 - bearing_area_mm2!r}mm^2 下回る"
                "（要件 2.9。締結の当たり面を広げるか締結箇所を増やすこと。"
                "⚠️ 位置決めダボの面積は算入できない）。"
            )


@dataclass(frozen=True, slots=True)
class FastenerLine:
    """締結部品一覧の1行（要件 2.10「調達できる形の一覧」）。

    ⚠️ **ダボの行は構築できない**——`kind` は `FASTENER_KINDS` に閉じており、
    そこに `"dowel"` は無い（design.md `#### Joints` Invariants）。

    Attributes:
        designation: 呼び（例: `"M3"`）。上流 `JointPolicy.bolt_designation`。
        kind: 種別。`FASTENER_KINDS` のいずれか。
        length_mm: 長さ（mm）。⚠️ **ナットは長さを持たない**（`None`）。
        count: 数量。1 以上（0 の行は一覧に置かない——「要らない」は行の不在で表す）。

    Raises:
        ParameterError: 呼びが空白のみの場合、種別が `FASTENER_KINDS` に無い場合、
            長さと種別が食い違う場合、または数量が 1 以上の整数でない場合。
    """

    designation: str
    kind: str
    length_mm: float | None
    count: int

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は項目名と値を添えて拒否する。"""
        if not isinstance(self.designation, str) or not self.designation.strip():
            raise ParameterError(
                f"{_DESIGNATION_KEY}={self.designation!r} は空白のみでない文字列で"
                "なければならない。"
            )
        if self.kind not in FASTENER_KINDS:
            raise ParameterError(
                f"{_KIND_KEY}={self.kind!r} は {list(FASTENER_KINDS)!r} の"
                "いずれかでなければならない"
                "（⚠️ ダボは造形で作る位置決め要素であり、購入する締結部品ではない）。"
            )
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 1:
            raise ParameterError(
                f"{_COUNT_KEY}={self.count!r} は 1 以上の整数でなければならない"
                "（数量 0 の行は一覧に置かない）。"
            )
        if self.kind == NUT_KIND:
            if self.length_mm is not None:
                raise ParameterError(
                    f"{_LENGTH_KEY}={self.length_mm!r} はナットの行では "
                    "None でなければならない（ナットは長さで調達しない）。"
                )
            return
        if (
            self.length_mm is None
            or isinstance(self.length_mm, bool)
            or not isinstance(self.length_mm, (int, float))
            or not math.isfinite(float(self.length_mm))
            or float(self.length_mm) <= 0.0
        ):
            raise ParameterError(
                f"{_LENGTH_KEY}={self.length_mm!r} は "
                f"{self.kind!r} の行では正の有限値でなければならない。"
            )


@dataclass(frozen=True, slots=True)
class FastenerSchedule:
    """接合部と締結部品の一覧（design.md `#### Joints` Service Interface）。

    ⚠️ **`lines` は `joints` から一意に決まる**（design.md Invariants）。
    食い違う組み合わせは構築できない——記録の側が別の数量を主張できるなら、
    数え上げが唯一の箇所にある意味が失われる。

    Attributes:
        schema_version: 記録形式の版。`config.SCHEMA_VERSION` と一致する。
        parameters_digest: 寸法パラメータの識別子（`config.parameters_digest`）。
            ⚠️ **観測（`measurements.json`）は識別子に含めない**（design.md）。
        joints: 接合部の並び。導出の順（中央部↔アーム → アダプタ断片 →
            ゴミ箱の保持 → 台上の拘束）。
        lines: 締結部品の並び。⚠️ **ダボは現れない**。

    Raises:
        ParameterError: 版・識別子・要素の型が不正な場合。
        ConsistencyError: `lines` が `joints` から導かれる数量と食い違う場合。
    """

    schema_version: str
    parameters_digest: str
    joints: tuple[JointSpec, ...]
    lines: tuple[FastenerLine, ...]

    def __post_init__(self) -> None:
        """版・識別子・要素の型を検証し、`lines` を `joints` と突き合わせる。"""
        if self.schema_version != SCHEMA_VERSION:
            raise ParameterError(
                f"{_SCHEMA_VERSION_KEY}={self.schema_version!r} は "
                f"{SCHEMA_VERSION!r} と一致しなければならない。"
            )
        if not isinstance(self.parameters_digest, str) or not self.parameters_digest.strip():
            raise ParameterError(
                f"{_PARAMETERS_DIGEST_KEY}={self.parameters_digest!r} は空白のみで"
                "ない文字列でなければならない（記録は寸法パラメータの識別子を持つ）。"
            )
        if not isinstance(self.joints, tuple) or not self.joints:
            raise ParameterError(
                f"{_JOINTS_KEY}={self.joints!r} は1件以上の接合部の組でなければ"
                "ならない（接合部を持たない機体は組み上がらない）。"
            )
        if not all(isinstance(joint, JointSpec) for joint in self.joints):
            raise ParameterError(f"{_JOINTS_KEY}: すべて JointSpec でなければならない。")
        names = [joint.name for joint in self.joints]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ParameterError(
                f"{_JOINTS_KEY}: 接合部の名が重複している {duplicated!r}"
                "（名は導出から一意に決まる）。"
            )
        if not isinstance(self.lines, tuple) or not all(
            isinstance(line, FastenerLine) for line in self.lines
        ):
            raise ParameterError(
                f"{_LINES_KEY}={self.lines!r} は FastenerLine の組でなければならない。"
            )
        _require_lines_match_joints(self.joints, self.lines)


# ---------------------------------------------------------------------------
# 締結部品の数え上げ（⚠️ ダボは数えない）
# ---------------------------------------------------------------------------


def _bolt_length_histogram(joints: Sequence[JointSpec]) -> dict[float, int]:
    """ボルト長ごとの本数を数える（⚠️ ボルトを持たない接合部は寄与しない）。"""
    histogram: dict[float, int] = {}
    for joint in joints:
        if joint.bolt_count == 0:
            continue
        length_mm = round(joint.bolt_length_mm, _ROUND_DIGITS)
        histogram[length_mm] = histogram.get(length_mm, 0) + joint.bolt_count
    return histogram


def _require_lines_match_joints(
    joints: Sequence[JointSpec], lines: Sequence[FastenerLine]
) -> None:
    """`lines` が `joints` から導かれる数量と一致することを要求する。

    ⚠️ **インサートの長さだけは `joints` から導けない**（`JointSpec` は
    インサート長を持たず、上流 `JointPolicy` が正である）。したがって
    インサートについては**合計本数と行が1つであること**を検査する——
    導出は単一のインサートを用いるため、行が分かれていれば記録が導出以外の
    経路で書かれたことになる。

    Raises:
        ConsistencyError: 数量が食い違う場合（記録側と導出側の双方を示す）。
    """
    expected_bolts = _bolt_length_histogram(joints)
    actual_bolts: dict[float, int] = {}
    insert_total = 0
    insert_lines = 0
    nut_total = 0
    for line in lines:
        if line.kind == BOLT_KIND:
            length_mm = round(float(line.length_mm or 0.0), _ROUND_DIGITS)
            actual_bolts[length_mm] = actual_bolts.get(length_mm, 0) + line.count
        elif line.kind == INSERT_KIND:
            insert_total += line.count
            insert_lines += 1
        else:
            nut_total += line.count
    if actual_bolts != expected_bolts:
        raise ConsistencyError(
            f"{_LINES_KEY}: ボルトの数量が接合部と食い違う。"
            f"記録={actual_bolts!r} / 接合部から導かれる値={expected_bolts!r}"
            "（lines の総数は joints から一意に決まる）。"
        )
    expected_inserts = sum(joint.insert_count for joint in joints)
    if insert_total != expected_inserts:
        raise ConsistencyError(
            f"{_LINES_KEY}: インサートの数量が接合部と食い違う。"
            f"記録={insert_total!r} / 接合部から導かれる値={expected_inserts!r}。"
        )
    if insert_lines > 1:
        raise ConsistencyError(
            f"{_LINES_KEY}: インサートの行が {insert_lines!r} 件ある"
            "（導出は上流 JointPolicy の単一のインサートを用いるため1件である）。"
        )
    expected_nuts = sum(joint.bolt_count - joint.insert_count for joint in joints)
    if nut_total != expected_nuts:
        raise ConsistencyError(
            f"{_LINES_KEY}: ナットの数量が接合部と食い違う。"
            f"記録={nut_total!r} / 接合部から導かれる値={expected_nuts!r}"
            "（インサートで受けないボルトはナットで受ける）。"
        )
    designations = {line.designation for line in lines}
    if len(designations) > 1:
        raise ConsistencyError(
            f"{_LINES_KEY}: 呼びが複数ある {sorted(designations)!r}"
            "（導出は上流 JointPolicy の単一の呼び bolt_designation を用いる）。"
        )


def _fastener_lines(
    joints: Sequence[JointSpec], designation: str, insert_length_mm: float
) -> tuple[FastenerLine, ...]:
    """接合部から締結部品の一覧を組み立てる（要件 2.10）。

    ⚠️ **ダボを1本も数えない**（要件 2.7 / design.md Invariants）。
    ⚠️ **数量 0 の行を置かない**——「要らない」は行の不在で表す。
    """
    lines: list[FastenerLine] = [
        FastenerLine(
            designation=designation,
            kind=BOLT_KIND,
            length_mm=length_mm,
            count=count,
        )
        for length_mm, count in sorted(_bolt_length_histogram(joints).items())
    ]
    nut_count = sum(joint.bolt_count - joint.insert_count for joint in joints)
    if nut_count > 0:
        lines.append(
            FastenerLine(
                designation=designation, kind=NUT_KIND, length_mm=None, count=nut_count
            )
        )
    insert_count = sum(joint.insert_count for joint in joints)
    if insert_count > 0:
        lines.append(
            FastenerLine(
                designation=designation,
                kind=INSERT_KIND,
                length_mm=insert_length_mm,
                count=insert_count,
            )
        )
    return tuple(lines)


# ---------------------------------------------------------------------------
# 分割数の導出（⚠️ 部品の種類で分かれる）
# ---------------------------------------------------------------------------


def _adapter_outer_diameter_mm(params: ResolvedParams) -> float:
    """ゴミ箱固定アダプタの外径を上流の採寸値から導出する（要件 6.1, 6.9）。"""
    adapter = params.chassis.adapter
    return params.trash_can.bottom_outer_diameter_mm + _BOTH_SIDES * (
        adapter.seat_clearance_mm + adapter.wall_thickness_mm
    )


def segment_counts(params: ResolvedParams) -> Mapping[str, int]:
    """部品ごとの分割数を、部品の種類に応じた導出で返す（要件 2.1）。

    ⚠️ **円環部品と、位相が決まっている部品とで導出が分かれる**
    （research.md「Decision: 分割の導出を部品の種類で分ける」）:

    - **円環部品**（`ANNULAR_PART_NAMES`）→ 上流 `required_segment_count`
    - **位相が決まっている部品**（`PHASE_PART_NAMES`）→ 輪数・脚数から従属し、
      造形可能性の関門は `check_envelope`（`derive_joints`）である

    ⚠️ **円環でない部品を円環として近似しない。** 上流の分割数導出は扇形の
    外接箱で判定しており、半径方向の広がりは分割数を増やしても縮まない——
    円環でない形へ当てれば、過大な分割数が「正しい導出」の顔をして返る。

    ⚠️ **手で決めた分割数を設定値として持たない**（要件 2.1）。ここに現れる
    `1` は「分割しない」であり、寸法パラメータではない。

    Args:
        params: `config.load_params()` の戻り値。

    Returns:
        部品名から分割数への対応。並びは
        `ANNULAR_PART_NAMES` / `PHASE_PART_NAMES` に現れる部品を含む。

    Raises:
        catch_mechanism.GeometryError: 円環部品が現実的な上限までのどの分割数でも
            造形可能寸法に収まらない場合。⚠️ **上流の失敗を包み直さない**
            （design.md「Error Strategy」: どちらの設定が壊れているかを消さない）。
    """
    chassis = params.chassis
    return {
        "hub_plate": _UNSPLIT_SEGMENT_COUNT,
        "motor_arm": chassis.base.wheel_count,
        "adapter_segment": required_segment_count(
            _adapter_outer_diameter_mm(params), params.printing
        ),
        "battery_tray": _UNSPLIT_SEGMENT_COUNT,
        "board_tray": _UNSPLIT_SEGMENT_COUNT,
        "cable_guide": chassis.base.wheel_count,
        "service_stand": chassis.stand.leg_count,
    }


def _check_fragment_envelopes(layout: ChassisLayout, params: ResolvedParams) -> None:
    """分割後の断片の外接箱を上流の検査へ通す（要件 2.2, 2.3）。

    ⚠️ **位相が決まっている部品の関門はここである**（design.md `#### Joints`:
    「各断片は `check_envelope` が関門になる」）。円環部品は
    `required_segment_count` が「収まる分割数」を返すことで既に保証されている
    （上流 Postconditions）ため、ここで扇形の外接箱を組み直さない——組み直せば
    上流の導出を本 Spec が再実装したことになる。

    ⚠️ **トレイ・配線ガイド・スタンドの外接箱はここで作らない。** それらの外形は
    形状の設計変数（`shapes`、3群）が決めるものであり、寸法パラメータだけからは
    当て推量になる。当て推量の外接箱で「収まっている」と言うほうが、検査が無い
    ことより悪い。

    ## ⚠️ 中央部の外接箱は公称外径ではない（`ARM_JOINT_LAP_LENGTH_FORMULA`）

    中央部↔モータ取付部は**重ね継手**であり、ハブ板の舌は中央部の外縁から
    重ね代のぶん**外へ張り出す**（アームの二股がそれを挟む。要件 2.6）。
    ⚠️ アーム側は張り出さない——`ARM_LENGTH_FORMULA` が「中央部の外縁から
    ホイール中心面まで」と定めており、二股はその区間の内側にある。したがって
    外接箱が公称外径より大きくなるのは中央部だけであり、⚠️ **重ね代を無視した
    外接箱で「収まっている」と述べると、実際より小さい部品について述べたことに
    なる**（`shapes.drive_base_geometry` が構築する形と食い違う）。

    Raises:
        GeometryError: 収まらない断片がある場合。⚠️ **超過を全件**、部品名・軸・
            外接箱・上限・超過量つきで示す（1件ずつ直す往復を避ける）。
    """
    base = params.chassis.base
    hub_plate_outer_diameter_mm = base.hub_outer_diameter_mm + _BOTH_SIDES * (
        arm_joint_lap_length_mm(layout, params)
    )
    fragments = (
        (
            "hub_plate",
            Envelope(
                x_mm=hub_plate_outer_diameter_mm,
                y_mm=hub_plate_outer_diameter_mm,
                z_mm=base.plate_thickness_mm,
            ),
        ),
        (
            "motor_arm",
            Envelope(
                x_mm=layout.arm_length_mm,
                y_mm=base.arm_width_mm,
                z_mm=base.arm_thickness_mm,
            ),
        ),
    )
    violations = [
        violation
        for part_name, envelope in fragments
        for violation in check_envelope(part_name, envelope, params.printing)
    ]
    if not violations:
        return
    detail = "、".join(
        f"{violation.part_name} の {violation.axis} 軸が "
        f"{violation.envelope_mm!r}mm で上限 {violation.limit_mm!r}mm を "
        f"{violation.excess_mm!r}mm 超過"
        for violation in violations
    )
    raise GeometryError(
        f"造形可能寸法を超える断片が残っている: {detail}"
        "（要件 2.3。分割するか外形を小さくすること。"
        "⚠️ 位相が決まっている部品は円環の等分では分割できない）。"
    )


# ---------------------------------------------------------------------------
# 接合部の導出
# ---------------------------------------------------------------------------


def _boss_diameter_mm(params: ResolvedParams) -> float:
    """インサート座（ボス）の外径（`BOSS_DIAMETER_FACTOR` × インサート外径）。"""
    return BOSS_DIAMETER_FACTOR * params.joint.insert_outer_diameter_mm


def _bolt_bearing_area_mm2(params: ResolvedParams) -> float:
    """ボルト1本あたりの当たり面（`BEARING_AREA_FORMULA` の1本ぶん）。

    Raises:
        GeometryError: 座の外径が貫通穴径以下の場合（当たり面が残らない）。
    """
    joint = params.joint
    boss_diameter_mm = _boss_diameter_mm(params)
    if boss_diameter_mm <= joint.through_hole_diameter_mm:
        raise GeometryError(
            f"インサート座の外径 {boss_diameter_mm!r}mm が貫通穴径 "
            f"{joint.through_hole_diameter_mm!r}mm 以下であり、当たり面が残らない"
            f"（{BEARING_AREA_FORMULA}）。"
        )
    return (
        math.pi
        / 4
        * (boss_diameter_mm**2 - joint.through_hole_diameter_mm**2)
    )


def _bolt_count(
    name: str,
    minimum_bearing_area_mm2: float,
    pad_area_mm2: float,
    face_width_mm: float,
    boss_diameter_mm: float,
    floor_count: int,
) -> int:
    """当たり面の下限を満たす最小のボルト本数を返す（要件 2.9, 2.10）。

    ⚠️ **本数は下限から導出する。** 手で決めた本数を設定値として持たない——
    上流がインサートを変えるか本 Spec が下限を上げれば、本数が追随する。

    Args:
        name: 接合部の名（失敗時のメッセージに載せる）。
        minimum_bearing_area_mm2: この接合部へ課される当たり面の下限。
        pad_area_mm2: ボルト1本あたりの当たり面。
        face_width_mm: 接合面のうち、ボルト座を並べられる幅（mm）。
            ⚠️ **接合面の面内の軸で測った量である。** 締結の軸（＝
            `print_normal_axis` が指す軸）で測った量を渡さない——その方向には
            座を並べられない（並べれば座がボルトの軸上に重なる）。
        boss_diameter_mm: ボルト座の外径（mm）。
        floor_count: 設計上の最小本数（保持箇所の数など）。

    Returns:
        `MIN_BOLTS_PER_FASTENED_JOINT` 以上の本数。

    Raises:
        GeometryError: 必要な本数のボルト座が接合面の幅に並ばない場合。
            ⚠️ **接合部の名・必要な幅・使える幅**をメッセージに載せる。
    """
    count = max(
        MIN_BOLTS_PER_FASTENED_JOINT,
        floor_count,
        math.ceil(minimum_bearing_area_mm2 / pad_area_mm2),
    )
    required_width_mm = count * boss_diameter_mm
    if required_width_mm > face_width_mm:
        raise GeometryError(
            f"{name}: 当たり面の下限 {minimum_bearing_area_mm2!r}mm^2 を満たすには"
            f"ボルトが {count!r} 本要り、座の並びに {required_width_mm!r}mm が"
            f"必要だが、接合面の幅は {face_width_mm!r}mm しかない"
            f"（不足 {required_width_mm - face_width_mm!r}mm）。"
            "接合面を広げるか、下限を見直すこと。"
        )
    return count


ARM_JOINT_NAME_TEMPLATE: Final[str] = "hub_plate__motor_arm_{index}"
"""中央部↔モータ取付部の接合部の名（⚠️ 名の組み立てはここ1箇所である）。"""


def _arm_bolt_count(layout: ChassisLayout, params: ResolvedParams) -> int:
    """中央部↔モータ取付部のボルト本数（要件 2.9, 2.10, 3.10）。

    ⚠️ **本数の出所を1箇所にする。** 重ね代（`arm_joint_lap_length_mm`）と
    実際の接合部（`derive_joints`）と形（`shapes.drive_base_geometry`）が同じ
    本数を読む——別々に数えれば、下限を上げたときに舌だけが伸びない、あるいは
    座だけが増える、という食い違いが黙って残る。

    Raises:
        GeometryError: 必要な本数のボルト座が接合面の半径方向の幅に並ばない場合。
    """
    return _bolt_count(
        name=ARM_JOINT_NAME_TEMPLATE.format(index=1),
        minimum_bearing_area_mm2=params.chassis.joint_local.min_bearing_area_mm2,
        pad_area_mm2=_bolt_bearing_area_mm2(params),
        # ⚠️ 接合面の面内で座を並べられる方向は**半径方向**である（面の法線は
        # 接線方向 `y`）。`arm_width_mm` はボルトの軸そのものであり渡さない。
        face_width_mm=layout.arm_length_mm,
        boss_diameter_mm=_boss_diameter_mm(params),
        floor_count=MIN_BOLTS_PER_FASTENED_JOINT,
    )


def arm_joint_lap_length_mm(layout: ChassisLayout, params: ResolvedParams) -> float:
    """ハブ板の舌がアームの二股へ差し込まれる半径方向の長さ（mm）。

    `ARM_JOINT_LAP_LENGTH_FORMULA` を参照。⚠️ **形（`shapes`）と断片の外接箱
    （`_check_fragment_envelopes`）の双方がこの1つの関数を読む。**

    Args:
        layout: `layout.derive_layout` の戻り値。
        params: `config.load_params()` の戻り値。

    Returns:
        重ね代（mm）。⚠️ 座の並びに要る幅と同じ量である。

    Raises:
        GeometryError: ボルト座が接合面の半径方向の幅に並ばない場合。
    """
    return _arm_bolt_count(layout, params) * _boss_diameter_mm(params)


def _fastened_joint(
    *,
    name: str,
    members: tuple[str, str],
    stack_thickness_mm: float,
    face_width_mm: float,
    minimum_bearing_area_mm2: float,
    print_normal_axis: str,
    dowel_count: int,
    floor_count: int,
    params: ResolvedParams,
) -> JointSpec:
    """締結する接合部を1件導出する（要件 2.6, 2.9, 2.10）。

    ⚠️ **当たり面は上流の `check_joint` にも通す**（要件 2.9）——本 Spec の
    下限を満たしていても、上流の下限が引き上げられていれば失敗する。
    """
    pad_area_mm2 = _bolt_bearing_area_mm2(params)
    bolt_count = _bolt_count(
        name=name,
        minimum_bearing_area_mm2=minimum_bearing_area_mm2,
        pad_area_mm2=pad_area_mm2,
        face_width_mm=face_width_mm,
        boss_diameter_mm=_boss_diameter_mm(params),
        floor_count=floor_count,
    )
    bearing_area_mm2 = bolt_count * pad_area_mm2
    # ⚠️ 上流の検査を実際に通す（要件 2.9「上流が公開する検査を用いて確認する」）。
    # 上流の失敗はそのまま伝播させる——どちらの設定を直すべきかを消さない。
    check_joint(params.joint, bearing_area_mm2)
    return JointSpec(
        name=name,
        members=members,
        bolt_count=bolt_count,
        bolt_length_mm=(
            # ⚠️ 余裕は締結の向きに沿った専用の寸法パラメータである。床との隙間の
            # ための clearance.fastener_protrusion_mm を流用しない
            # （FASTENER_LENGTH_FORMULA の docstring）。
            stack_thickness_mm
            + params.joint.insert_length_mm
            + params.chassis.joint_local.fastener_length_margin_mm
        ),
        # ⚠️ インサートは1本のボルトにつき1つである（樹脂側にねじを切らない。A-5）。
        insert_count=bolt_count,
        dowel_count=dowel_count,
        bearing_area_mm2=bearing_area_mm2,
        print_normal_axis=print_normal_axis,
        min_bearing_area_mm2=minimum_bearing_area_mm2,
    )


def derive_joints(
    layout: ChassisLayout, params: ResolvedParams
) -> tuple[JointSpec, ...]:
    """接合部の一覧を幾何と寸法から導出する（要件 2.1, 2.6-2.10, 3.10, 5.6, 6.8）。

    導出される家族は次の4つである（⚠️ **手書きの一覧を持たない**——件数はすべて
    幾何と寸法から決まる）:

    | 家族 | 件数の出どころ | 荷重 | 造形姿勢 |
    |---|---|---|---|
    | 中央部↔モータ取付部 | `layout.wheel_angles_deg` | モータ反力（本 Spec の下限） | 接線方向 `y` |
    | 中央部↔アダプタ断片 | `segment_counts()["adapter_segment"]` | ゴミ箱の質量（上流の下限） | 半径方向 `x` |
    | アダプタ↔ゴミ箱 | 1（`retention_point_count` 本の締結） | ゴミ箱の質量（上流の下限） | 半径方向 `x` |
    | 整備スタンド↔ホイール | `stand.leg_count` | モータ反力（本 Spec の下限） | 接線方向 `y` |

    ⚠️ **中央部↔モータ取付部は、ハブ板の舌をアームの二股が挟む形である**
    （接線方向のボルトが二面せん断で受ける）。この向きにするのは、接合面の法線が
    積層方向を向く配置を避けるためである（要件 2.8 / A-5）——アームを平置きで
    造形しても接合面の法線は面内に留まる。

    ⚠️ **整備スタンドの拘束は締結部品を持たない**（要件 5.6 / design.md 決定 5）。
    脚がホイールを両側から挟み、モータ反力を面で受ける。工具なしで載せ降ろし
    できることが要件 5.9 の前提である。

    Args:
        layout: `layout.derive_layout` の戻り値（design.md Preconditions）。
        params: `config.load_params()` の戻り値。上流の継手方針・造形制約・
            ゴミ箱の採寸値もここから読む（⚠️ 同じ値を再定義しない。要件 1.3）。

    Returns:
        導出された接合部。⚠️ すべてが `print_normal_axis != "z"` を満たし、
        上流の下限と本 Spec の下限の**両方**を満たす（design.md Postconditions）。

    Raises:
        GeometryError: 断片が造形可能寸法を超える場合、必要なボルト座が接合面に
            並ばない場合、または当たり面が下限を満たさない場合。
        catch_mechanism.ParameterError: 上流の `check_joint` が当たり面を
            拒否した場合。⚠️ **包み直さない**。
        catch_mechanism.GeometryError: 円環部品の分割数が求まらない場合。
    """
    chassis = params.chassis
    base = chassis.base
    adapter = chassis.adapter
    counts = segment_counts(params)
    _check_fragment_envelopes(layout, params)

    local_floor_mm2 = chassis.joint_local.min_bearing_area_mm2
    upstream_floor_mm2 = params.joint.min_bearing_area_mm2
    adapter_outer_diameter_mm = _adapter_outer_diameter_mm(params)
    adapter_segment_count = counts["adapter_segment"]

    specs: list[JointSpec] = [
        # ⚠️ 件数は輪の数そのものである（`layout` が生成した取付角の数を読む）。
        # 要件 3.10「中央部と各モータ取付部の接合を、要件2が定める荷重の受け方に
        # 従って設計する」。モータ反力を受けるため本 Spec の下限を課す。
        _fastened_joint(
            name=ARM_JOINT_NAME_TEMPLATE.format(index=index),
            members=("hub_plate", f"motor_arm_{index}"),
            # 二股の側壁 ＋ ハブ板の舌を貫き、反対側の側壁のインサートで受ける。
            stack_thickness_mm=base.arm_thickness_mm + base.plate_thickness_mm,
            # ⚠️ **接合面は接線方向（`y`）を法線に持つ**——面内2軸は
            # **半径方向（アーム長）と厚さ方向**である。座を並べられるのは
            # 半径方向であり、⚠️ **`arm_width_mm` を渡さない**：それは
            # `_check_fragment_envelopes` が `y` へ写している量、すなわち
            # **ボルトの軸そのもの**であり、その方向に座は並ばない。
            # ⚠️ 面のもう一方の辺（厚さ `arm_thickness_mm`）が座の外径を下回ると、
            # `BEARING_AREA_FORMULA` が数える環が面に載らない。それは形の側の
            # 成立条件であるため `shapes.drive_base_geometry` が拒否し、
            # 解析値と実形状の一致は `test_chassis_invariants.py` が検査する
            # （本モジュールは build123d を import できない。design.md Risks）。
            face_width_mm=layout.arm_length_mm,
            minimum_bearing_area_mm2=local_floor_mm2,
            print_normal_axis=_TANGENTIAL_NORMAL_AXIS,
            dowel_count=_LOCATING_DOWEL_COUNT,
            floor_count=MIN_BOLTS_PER_FASTENED_JOINT,
            params=params,
        )
        for index in range(1, len(layout.wheel_angles_deg) + 1)
    ]

    # ⚠️ アダプタ断片の件数は上流の分割数導出が決める（円環部品）。
    # 座はハブ板の立ち上がりへ半径方向のボルトで留める——座面（水平面）へ
    # 鉛直に留めると、接合面の法線が積層方向と一致してしまう（要件 2.8）。
    specs.extend(
        _fastened_joint(
            name=f"hub_plate__adapter_segment_{index}",
            members=("hub_plate", f"adapter_segment_{index}"),
            stack_thickness_mm=adapter.wall_thickness_mm,
            face_width_mm=math.pi * adapter_outer_diameter_mm / adapter_segment_count,
            minimum_bearing_area_mm2=upstream_floor_mm2,
            print_normal_axis=_RADIAL_NORMAL_AXIS,
            dowel_count=_LOCATING_DOWEL_COUNT,
            floor_count=MIN_BOLTS_PER_FASTENED_JOINT,
            params=params,
        )
        for index in range(1, adapter_segment_count + 1)
    )

    # ⚠️ 要件 6.8: ゴミ箱の底は薄く（上流 `bottom_thickness_mm`）変形しやすいため、
    # 底へ穴を開けて点で引かず、底の外周を半径方向のクランプで押さえる。
    # 保持箇所は `retention_point_count` に従い円周へ等配置する（要件 6.5）。
    specs.append(
        _fastened_joint(
            name="adapter__trash_can",
            members=("adapter", "trash_can"),
            stack_thickness_mm=(
                adapter.wall_thickness_mm + params.trash_can.bottom_thickness_mm
            ),
            face_width_mm=math.pi * adapter_outer_diameter_mm,
            minimum_bearing_area_mm2=upstream_floor_mm2,
            print_normal_axis=_RADIAL_NORMAL_AXIS,
            # ⚠️ 購入部品との接合部にはダボを置かない（相手に穴を開けない）。
            dowel_count=_NO_DOWELS,
            floor_count=adapter.retention_point_count,
            params=params,
        )
    )

    # ⚠️ 要件 5.6: 台上での保持。締結部品を持たない拘束であり、一覧へ何も足さない。
    # 当たり面は `CONTACT_BEARING_AREA_FORMULA`（脚がホイールを受ける谷の投影）で
    # あり、ボルト座の式は適用できない。
    contact_area_mm2 = chassis.wheel.width_mm * chassis.stand.support_span_mm
    for index in range(1, chassis.stand.leg_count + 1):
        # ⚠️ 当たり面は上流の検査にも通す（要件 2.9）。締結部品が無くても、
        # 反力を受ける面である以上、下限の対象である。
        check_joint(params.joint, contact_area_mm2)
        specs.append(
            JointSpec(
                name=f"service_stand_{index}__wheel_{index}",
                members=(f"service_stand_{index}", f"wheel_{index}"),
                bolt_count=_NO_BOLTS,
                bolt_length_mm=0.0,
                insert_count=_NO_BOLTS,
                dowel_count=_NO_DOWELS,
                bearing_area_mm2=contact_area_mm2,
                print_normal_axis=_TANGENTIAL_NORMAL_AXIS,
                min_bearing_area_mm2=local_floor_mm2,
            )
        )
    return tuple(specs)


def derive_fastener_schedule(
    layout: ChassisLayout, params: ResolvedParams
) -> FastenerSchedule:
    """接合部と締結部品の一覧を導出する（要件 2.10 / OQ-09 の決着）。

    Args:
        layout: `layout.derive_layout` の戻り値。
        params: `config.load_params()` の戻り値。

    Returns:
        接合部と、そこから一意に決まる締結部品の一覧。⚠️ **ダボは含まれない**。

    Raises:
        GeometryError: 接合部が導出できない場合（`derive_joints` を参照）。
    """
    joints = derive_joints(layout, params)
    return FastenerSchedule(
        schema_version=SCHEMA_VERSION,
        parameters_digest=parameters_digest(params.chassis),
        joints=joints,
        lines=_fastener_lines(
            joints, params.joint.bolt_designation, params.joint.insert_length_mm
        ),
    )


# ---------------------------------------------------------------------------
# 記録の直列化
# ---------------------------------------------------------------------------


def _rounded(value: float) -> float:
    """記録へ書き出す数を `_ROUND_DIGITS` で丸める。"""
    return round(float(value), _ROUND_DIGITS)


def _to_document(schedule: FastenerSchedule) -> dict[str, object]:
    """`schedule` を記録の形へ写す。"""
    return {
        _SCHEMA_VERSION_KEY: schedule.schema_version,
        _PARAMETERS_DIGEST_KEY: schedule.parameters_digest,
        _JOINTS_KEY: [
            {
                _NAME_KEY: joint.name,
                _MEMBERS_KEY: list(joint.members),
                _BOLT_COUNT_KEY: joint.bolt_count,
                _BOLT_LENGTH_KEY: _rounded(joint.bolt_length_mm),
                _INSERT_COUNT_KEY: joint.insert_count,
                _DOWEL_COUNT_KEY: joint.dowel_count,
                _BEARING_AREA_KEY: _rounded(joint.bearing_area_mm2),
                _PRINT_NORMAL_AXIS_KEY: joint.print_normal_axis,
                _MIN_BEARING_AREA_KEY: _rounded(joint.min_bearing_area_mm2),
            }
            for joint in schedule.joints
        ],
        _LINES_KEY: [
            {
                _DESIGNATION_KEY: line.designation,
                _KIND_KEY: line.kind,
                _LENGTH_KEY: (
                    None if line.length_mm is None else _rounded(line.length_mm)
                ),
                _COUNT_KEY: line.count,
            }
            for line in schedule.lines
        ],
    }


def dump_fastener_schedule(schedule: FastenerSchedule, path: Path) -> None:
    """`schedule` を記録として `path` へ書き出す（要件 2.10, 11.7）。

    整形は `config.dump_params` / `layout.dump_layout` に揃える
    （**インデント2・キー整列・末尾改行・LF**）。⚠️ **LF は `.gitattributes` の
    `configs/chassis_mechanism/*.json text eol=lf` と対で成立する**——本関数が
    書くバイト列が git のチェックアウト内容と同一であるため、値が変わっていな
    ければ `git status` は変更を報告しない。

    Args:
        schedule: 書き出す一覧。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(schedule),
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
        raise ParameterError(f"{path}: 記録を読み込めない: {exc}") from exc
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


def _integer(data: Mapping[str, object], key: str, label: str) -> int:
    """`data[key]` を整数として取り出す（⚠️ `bool` を除く）。"""
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError(
            f"{label}.{key}={value!r} は整数でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return value


def _text(data: Mapping[str, object], key: str, label: str) -> str:
    """`data[key]` を文字列として取り出す。"""
    value = data[key]
    if not isinstance(value, str):
        raise ParameterError(
            f"{label}.{key}={value!r} は文字列でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return value


def _joint_from_document(data: object, label: str) -> JointSpec:
    """記録の1件を `JointSpec` へ読み戻す（不変条件は型が検証する）。"""
    if not isinstance(data, dict):
        raise ParameterError(
            f"{label}: オブジェクト（{{...}}）を期待したが "
            f"{type(data).__name__} だった。"
        )
    _reject_unknown_and_missing(data, _JOINT_KEYS, label)
    members = data[_MEMBERS_KEY]
    if (
        isinstance(members, (str, bytes))
        or not isinstance(members, Sequence)
        or len(members) != _MEMBER_COUNT
        or not all(isinstance(member, str) for member in members)
    ):
        raise ParameterError(
            f"{label}.{_MEMBERS_KEY}={members!r} は2つの部材名の並びでなければ"
            "ならない。"
        )
    return JointSpec(
        name=_text(data, _NAME_KEY, label),
        members=(str(members[0]), str(members[1])),
        bolt_count=_integer(data, _BOLT_COUNT_KEY, label),
        bolt_length_mm=_number(data, _BOLT_LENGTH_KEY, label),
        insert_count=_integer(data, _INSERT_COUNT_KEY, label),
        dowel_count=_integer(data, _DOWEL_COUNT_KEY, label),
        bearing_area_mm2=_number(data, _BEARING_AREA_KEY, label),
        print_normal_axis=_text(data, _PRINT_NORMAL_AXIS_KEY, label),
        min_bearing_area_mm2=_number(data, _MIN_BEARING_AREA_KEY, label),
    )


def _line_from_document(data: object, label: str) -> FastenerLine:
    """記録の1行を `FastenerLine` へ読み戻す（不変条件は型が検証する）。"""
    if not isinstance(data, dict):
        raise ParameterError(
            f"{label}: オブジェクト（{{...}}）を期待したが "
            f"{type(data).__name__} だった。"
        )
    _reject_unknown_and_missing(data, _LINE_KEYS, label)
    length = data[_LENGTH_KEY]
    return FastenerLine(
        designation=_text(data, _DESIGNATION_KEY, label),
        kind=_text(data, _KIND_KEY, label),
        length_mm=None if length is None else _number(data, _LENGTH_KEY, label),
        count=_integer(data, _COUNT_KEY, label),
    )


def load_fastener_schedule(path: Path | None = None) -> FastenerSchedule:
    """記録を読み戻す（要件 2.10, 11.7）。

    ⚠️ **記録は導出の写しであり、独立に編集してよい自由記述ではない。**
    未知キー・欠損・型不正に加えて、**接合部と食い違う締結部品の数量**、
    **積層方向と一致する造形姿勢**、**下限を下回る当たり面**、
    **ダボを名乗る行**を拒否する。ここを素通しにすると、数え上げが唯一の箇所に
    ある意味が失われる——記録の側が別の数量を主張できるなら、数え上げは2箇所に
    あるのと変わらない。

    Args:
        path: 読み込む記録。`None` なら `DEFAULT_JOINT_SCHEDULE_PATH`。

    Returns:
        読み戻した一覧。`derive_fastener_schedule` の戻り値と同じ不変条件を満たす。

    Raises:
        ParameterError: 記録が読めない、または構造・型・種別が不正な場合。
        GeometryError: 記録された接合部が不変条件に反する場合。
        ConsistencyError: 締結部品の数量が接合部と食い違う場合。
    """
    target = DEFAULT_JOINT_SCHEDULE_PATH if path is None else path
    label = str(target)
    document = _read_document(target)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)

    joints_value = document[_JOINTS_KEY]
    if isinstance(joints_value, (str, bytes)) or not isinstance(joints_value, Sequence):
        raise ParameterError(
            f"{label}.{_JOINTS_KEY}={joints_value!r} は接合部の並びでなければならない。"
        )
    lines_value = document[_LINES_KEY]
    if isinstance(lines_value, (str, bytes)) or not isinstance(lines_value, Sequence):
        raise ParameterError(
            f"{label}.{_LINES_KEY}={lines_value!r} は締結部品の並びでなければならない。"
        )

    return FastenerSchedule(
        schema_version=_text(document, _SCHEMA_VERSION_KEY, label),
        parameters_digest=_text(document, _PARAMETERS_DIGEST_KEY, label),
        joints=tuple(
            _joint_from_document(item, f"{label}.{_JOINTS_KEY}[{index}]")
            for index, item in enumerate(joints_value)
        ),
        lines=tuple(
            _line_from_document(item, f"{label}.{_LINES_KEY}[{index}]")
            for index, item in enumerate(lines_value)
        ),
    )
