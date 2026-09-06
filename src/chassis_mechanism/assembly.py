"""組立後の観測の記録と組立完了の判定（design.md `#### Assembly` / 要件 4.5,
9.4, 9.5, 9.6, 9.7, 9.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.9）。

本モジュールは**実物から得た観測**を持つ。設計入力（`dimensions.json`）でも
導出結果（`layout.json` / `joint-schedule.json`）でもない第3の集約であり、
⚠️ **設計を変えない**（design.md「Domain Model」の集約の境界）。

⚠️ **観測は設計入力と別のファイルへ置く**（`measurements.json`）。混ぜれば
`config.parameters_digest` が観測のたびに動き、「形状を再生成すべき変更」と
「再生成不要な変更」が区別できなくなる（design.md「Key Decisions」/
`#### Baseline`「⚠️ **観測（`measurements.json`）は識別子に含めない**」）。
本モジュールは `config.parameters_digest` を呼ばず、記録に識別子を持たない——
持たせた時点で、機体を測り直すたびに形状の再生成が要求される。

**読み込みと完了判定は別の問題である。**

- `load_assembly_record` は**構造と整合**だけを見る。⚠️ **全項目が未記入の記録も
  読み込みは成功する**（tasks.md タスク 2.4:「⚠️ **6群の実測が始まる前から
  読み込めるようにする**（読み込み自体が失敗すると、組立前に他の検査を回せなく
  なる）」）。出荷される `configs/chassis_mechanism/measurements.json` は
  まさにその全項目未記入の初期状態である。
- `missing_observations` が**未了の項目を全件**返し、それが空であることを
  `is_assembly_complete` が組立完了と呼ぶ。

⚠️ **完了の判定にモータへ通電する項目を1つも置かない**（要件 9.8 / 9.9 /
design.md「⚠️ **判定にモータへの通電を含む項目を1つも置かない**」）。必須項目は
静止した機体に対する測定（重量・重心・寸法・隙間）と手による確認だけであり、
転動を伴う校正（`ENCODER_COUNTS_PER_WHEEL_REV` の決定）は下流 `teleop-bringup` の
M2a-0 に属する。`test_chassis_assembly.py` が、必須項目の識別子と
`missing_observations` の出力を語彙で検査し、`is_assembly_complete` の本体が
`missing_observations(record) == ()` そのものであることを `ast` で固定する。

**未記入（値が無い）と仮値（値はあるが出所が `assumed`）を区別する。**
どちらも完了を妨げるが、現物へ戻るときにやることが違う——前者は測る、後者は
測り直して出所を実測へ更新する。`missing_observations` は両者を別の語で返す
（要件 10.9 / design.md Risks「⚠️ **出所が `assumed` のままの必須項目を
`missing_observations` が拾う**」）。⚠️ **観測の不足を既定値で埋めない**
（`errors.MeasurementError` の docstring）。

**枠の欠落と未記入も別の問題である。** 隙間の5部位・ホイール3個・手による確認3件は
記録の**枠**であり、未記入のまま常に存在する。枠が欠けた記録は
`MeasurementError` で拒否する——欠けたまま読めてしまえば、「測っていない部位」と
「そもそも見ていない部位」が一覧の上で区別できない（design.md `#### Assembly`
Integration:「⚠️ **部位が1つでも欠けた記録を「完了」と呼ばない**」）。
⚠️ **5部位の名は `clearance.CLEARANCE_ITEM_NAMES` が唯一の正であり、ここへ
書き写さない**。書き写せば、算出側へ部位を1つ足したときに観測側が黙って古いまま
になる。`test_chassis_assembly.py` が、部位名の文字列リテラルが本モジュールに
1つも現れないことを固定する。

**代表値は3個の平均である**（design.md `#### Assembly` Invariants /
「Revalidation Triggers」項目3）。⚠️ **平均と一致しない記録は `ConsistencyError`
で拒否する**——代表値を独立に書けるなら、実効転がり径は2箇所で管理されているのと
変わらない。同じ理由で、隙間の差 `difference_mm` は実測と設計値から一意に決まる
（要件 4.5）。

**転動を伴わない測定は、その測定が捉えられない範囲の記述を必須とする**
（要件 10.5）。荷重下のノギス当ては接地面の潰れを含んだ静的な径であって、
転動1回転あたりの実効周長ではない。⚠️ **限界の記述が空なら `MeasurementError`**
——値だけの測定は、どこまで信じてよいかを持たないまま下流の設定値になる
（`params.BracketMeasurements.mount_face_reference` と同じ規律である）。

依存の制約（design.md「Dependency Direction」）:
    ⚠️ **本モジュールは上流 `catch_mechanism` を import しない。** `assembly` は
    上流を import してよい5モジュール（`params` / `config` / `joints` /
    `baseline` / `shapes`）に含まれない。出所の型 `Provenance` は上流の型その
    ものであり、`chassis_mechanism.params` 経由で受け取る——⚠️ **第2の出所型を
    定義しない**（design.md `#### Params`）。
    ⚠️ **`layout` を右から呼び返さない。** 実効転がり半径の差し込み口は
    `layout._effective_rolling_radius_mm` の1箇所であり、そこへ本モジュールの
    代表値を渡すのは**呼び出し側（`cli` / `__init__`）の仕事**である。
    `layout` が本モジュールを import すれば依存方向が逆になる。本モジュールは
    `representative_rolling_radius_mm` で半径を1箇所から提供するところまでを持つ。
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from chassis_mechanism.clearance import CLEARANCE_ITEM_NAMES
from chassis_mechanism.config import SCHEMA_VERSION
from chassis_mechanism.errors import ConsistencyError, MeasurementError, ParameterError
from chassis_mechanism.params import Provenance

__all__ = [
    "DEFAULT_MEASUREMENTS_PATH",
    "DERIVED_ABS_TOLERANCE_MM",
    "WHEEL_OBSERVATION_COUNT",
    "ROLLING_METHOD",
    "STATIC_LOADED_METHOD",
    "MEASUREMENT_METHODS",
    "NON_ROLLING_METHODS",
    "DOWNSTREAM_ROLLING_CALIBRATION",
    "CHECK_RESULTS",
    "REQUIRED_CHECK_NAMES",
    "OBSERVATION_PATHS",
    "WheelObservation",
    "ClearanceObservation",
    "FitDeviation",
    "AssemblyCheck",
    "AssemblyRecord",
    "load_assembly_record",
    "dump_assembly_record",
    "missing_observations",
    "is_assembly_complete",
    "representative_rolling_radius_mm",
]


DEFAULT_MEASUREMENTS_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "chassis_mechanism"
    / "measurements.json"
)
"""観測記録の既定パス（design.md「Data Models」）。

`config.DEFAULT_DIMENSIONS_PATH` と同じディレクトリに置くが、⚠️ **設計入力とは
別のファイルである**。同じファイルに混ぜればパラメータ識別子が観測のたびに動く
（モジュール docstring）。
"""

DERIVED_ABS_TOLERANCE_MM: Final[float] = 1e-9
"""導出関係の照合に用いる絶対許容差（mm）。

代表値と3個の平均、隙間の差と「実測 − 設計値」の2箇所で使う。⚠️ **許容差を
置くのは浮動小数の丸めのためだけであり、値の食い違いを見逃すためではない。**
1e-9mm は現物の測定分解能（ノギスで 0.01mm）より6桁以上細かく、人が書き込む
記録の食い違いはこの幅に収まらない。ちょうど許容差の記録は受け入れ、超えた
記録を拒否する。
"""

WHEEL_OBSERVATION_COUNT: Final[int] = 3
"""観測するホイールの数（要件 10.3「3個それぞれの値と個体差を記録する」）。

`base.wheel_count`（現在 3）と同じ量だが、⚠️ **`load_assembly_record` は
寸法設定を読まない**——design.md が定める署名の引数はパスだけであり、また
設計入力が壊れている状況でも観測記録は読めなければならない（読めなくなると
組立前に他の検査を回せない）。両者が食い違わないことは
`test_chassis_assembly.py` が出荷されている `base.wheel_count` と突き合わせて
固定する。
"""

STATIC_LOADED_METHOD: Final[str] = "static_loaded"
"""機体の重量をかけたまま静止状態で測る手順（ノギス当て）。

⚠️ **転動を伴わない。** 接地面の潰れを含んだ静的な径であり、転動1回転あたりの
実効周長は捉えない（要件 10.5）。この手順を選ぶ記録は限界の記述を要する。
"""

ROLLING_METHOD: Final[str] = "rolling"
"""実際に転がして距離と回転数から求める手順。転動を伴うため限界の記述を要さない。"""

MEASUREMENT_METHODS: Final[tuple[str, ...]] = (STATIC_LOADED_METHOD, ROLLING_METHOD)
"""実効転がり径の測定手順として記録できる値（要件 10.4）。

未記入は空文字であり、この一覧には含めない——「まだ測っていない」は手順ではない。
"""

NON_ROLLING_METHODS: Final[frozenset[str]] = frozenset({STATIC_LOADED_METHOD})
"""転動を伴わない手順（要件 10.5 の「測定手順が転動を伴わない場合」）。"""

DOWNSTREAM_ROLLING_CALIBRATION: Final[str] = (
    "転動を伴う校正（ホイール1回転あたりのエンコーダ計数の決定）は"
    "下流 teleop-bringup の M2a-0 が行う。本 Spec は静的な荷重下の値と"
    "その限界を記録するところまでを持つ（要件 10.5 / 9.9）。"
)
"""静的測定の限界に添える定型の説明（要件 10.5 の後段）。

⚠️ **記録の必須項目ではない。** 通電を伴う校正が下流にあることは本 Spec の
**責務の境界**の説明であって、組立完了の条件ではない（要件 9.8）。限界の記述を
欠いた記録を拒否するときの案内としてだけ用いる。
"""

_PENDING_RESULT: Final[str] = "pending"
_PASS_RESULT: Final[str] = "pass"
_FAIL_RESULT: Final[str] = "fail"

CHECK_RESULTS: Final[tuple[str, ...]] = (_PENDING_RESULT, _PASS_RESULT, _FAIL_RESULT)
"""手による確認の結果として記録できる値。

`pending` は未実施であり、⚠️ **合格でも不合格でもない**。初期状態はこれである。
"""

REQUIRED_CHECK_NAMES: Final[tuple[str, ...]] = (
    "hub_setscrew_no_slip",
    "wheel_bolt_circle",
    "stand_retention",
)
"""手による確認の名と並び（design.md「組立手順」の確認項目）。

- `hub_setscrew_no_slip`: 手でホイールを回してハブが滑らないこと（要件 9.5）。
  ⚠️ **手で回すのであってモータで回すのではない**（要件 9.8）。
- `wheel_bolt_circle`: ホイールの取付穴群とハブのボルト円の対応（要件 9.6）。
- `stand_retention`: 整備スタンド上で手で押しても外れないこと（要件 5.7）。

⚠️ **この3件の増減は下流の再検証を要する変更である**（design.md
「Revalidation Triggers」項目7）。
"""


# ---------------------------------------------------------------------------
# 観測パス（出所表のキー）
#
# ⚠️ **5部位と3輪から導く。** 部位名を書き写さないことで、算出側
# （`clearance.CLEARANCE_ITEM_NAMES`）に部位が増えたときへ自動で追随する。
# ---------------------------------------------------------------------------

_MASS_PATH: Final[str] = "mass_g"
_COG_HEIGHT_PATH: Final[str] = "cog_height_mm"
_COG_OFFSET_PATH: Final[str] = "cog_radial_offset_mm"
_COG_METHOD_KEY: Final[str] = "cog_method"
_REPRESENTATIVE_PATH: Final[str] = "representative_wheel_diameter_mm"

_WHEELS_KEY: Final[str] = "wheels"
_CLEARANCES_KEY: Final[str] = "clearances"
_FIT_DEVIATIONS_KEY: Final[str] = "fit_deviations"
_CHECKS_KEY: Final[str] = "checks"
_PROVENANCE_KEY: Final[str] = "provenance"
_SCHEMA_VERSION_KEY: Final[str] = "schema_version"

_INDEX_KEY: Final[str] = "index"
_DIAMETER_KEY: Final[str] = "effective_rolling_diameter_mm"
_METHOD_KEY: Final[str] = "method"
_LIMITATION_NOTE_KEY: Final[str] = "limitation_note"

_NAME_KEY: Final[str] = "name"
_MEASURED_KEY: Final[str] = "measured_mm"
_DESIGN_KEY: Final[str] = "design_mm"
_DIFFERENCE_KEY: Final[str] = "difference_mm"

_LOCATION_KEY: Final[str] = "location"
_ACTUAL_KEY: Final[str] = "actual_mm"
_REFLECTED_KEY: Final[str] = "reflected_in_parameters"

_RESULT_KEY: Final[str] = "result"
_NOTE_KEY: Final[str] = "note"


def _wheel_path(index: int, field: str) -> str:
    """ホイール1個の観測パスを組み立てる。"""
    return f"{_WHEELS_KEY}.{index}.{field}"


def _clearance_path(name: str, field: str) -> str:
    """隙間1部位の観測パスを組み立てる。"""
    return f"{_CLEARANCES_KEY}.{name}.{field}"


def _check_path(name: str) -> str:
    """手による確認1件のパスを組み立てる。"""
    return f"{_CHECKS_KEY}.{name}"


OBSERVATION_PATHS: Final[tuple[str, ...]] = (
    _MASS_PATH,
    _COG_HEIGHT_PATH,
    _COG_OFFSET_PATH,
    *(
        _wheel_path(index, _DIAMETER_KEY)
        for index in range(WHEEL_OBSERVATION_COUNT)
    ),
    _REPRESENTATIVE_PATH,
    *(_clearance_path(name, _MEASURED_KEY) for name in CLEARANCE_ITEM_NAMES),
)
"""出所を持つ観測の値のパス（要件 10.9 / `params.PARAMETER_PATHS` と同じ役割）。

⚠️ **出所表のキー集合はこれと一致しなければならない。** 出所の無い観測値は、
実測なのか概算なのかを利用側が区別できないまま下流へ流れる。

⚠️ **手順（`cog_method` / `method`）と確認結果（`checks`）はここに含めない。**
出所は「その**値**が実測か概算か」を表すものであり、手順の記述や合否そのものは
値ではない。手順が空であること・確認が未実施であることは
`missing_observations` が別の形で拾う。
"""


# ---------------------------------------------------------------------------
# 共通の検証部品（⚠️ メッセージは常に項目名と値を持つ）
# ---------------------------------------------------------------------------


def _require_finite(value: object, name: str) -> float:
    """`value` が有限な数（`bool` を除く）であることを要求する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(
            f"{name}={value!r} は数値でなければならない（{type(value).__name__} だった）。"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ParameterError(f"{name}={value!r} は有限でなければならない。")
    return number


def _require_positive(value: object, name: str) -> float:
    """`value` が正の有限値であることを要求する。"""
    number = _require_finite(value, name)
    if number <= 0.0:
        raise ParameterError(f"{name}={value!r} は正でなければならない。")
    return number


def _require_nonnegative(value: object, name: str) -> float:
    """`value` が非負の有限値であることを要求する。"""
    number = _require_finite(value, name)
    if number < 0.0:
        raise ParameterError(f"{name}={value!r} は負であってはならない。")
    return number


def _require_str(value: object, name: str) -> str:
    """`value` が文字列であることを要求する（空文字は未記入として許す）。"""
    if not isinstance(value, str):
        raise ParameterError(
            f"{name}={value!r} は文字列でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return value


def _is_blank(text: str) -> bool:
    """空白だけの記述を未記入として扱う（空白は記述ではない）。"""
    return not text.strip()


def _agrees(left: float, right: float) -> bool:
    """2つの量が導出関係として一致するか（`DERIVED_ABS_TOLERANCE_MM`）。"""
    return abs(left - right) <= DERIVED_ABS_TOLERANCE_MM


def _mean(values: Sequence[float]) -> float:
    """平均（`math.fsum` で加算順に依らない値を得る）。"""
    return math.fsum(values) / len(values)


# ---------------------------------------------------------------------------
# 観測の型
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WheelObservation:
    """ホイール1個の荷重下の実効転がり径（要件 10.3, 10.4, 10.5）。

    Attributes:
        index: 輪番号。0 始まりで `layout.ChassisLayout.wheel_angles_deg` の
            並びに対応する（⚠️ 輪番号と符号の規約は `firmware` が正である。
            `layout.ASSUMPTIONS` を参照）。
        effective_rolling_diameter_mm: 実効転がり**径**（mm）。未記入は `None`。
            ⚠️ **半径ではない**——記録は径で持ち、半径が要る側
            （`layout` の鉛直スタック）は `representative_rolling_radius_mm`
            を通す。
        method: 測定手順。`MEASUREMENT_METHODS` のいずれか、または未記入の空文字。
        limitation_note: その測定が捉えられない範囲（要件 10.5）。⚠️ **手順が
            転動を伴わない場合は必須**である。

    Raises:
        ParameterError: 輪番号・値・手順の型や範囲が不正な場合、または値と手順の
            片方だけが埋まっている場合。
        MeasurementError: 転動を伴わない手順で限界の記述が空の場合。
    """

    index: int
    effective_rolling_diameter_mm: float | None
    method: str
    limitation_note: str

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise ParameterError(
                f"{_INDEX_KEY}={self.index!r} は整数でなければならない"
                f"（{type(self.index).__name__} だった）。"
            )
        if self.index < 0:
            raise ParameterError(f"{_INDEX_KEY}={self.index!r} は負であってはならない。")
        label = _wheel_path(self.index, "")
        method = _require_str(self.method, f"{label}{_METHOD_KEY}")
        note = _require_str(self.limitation_note, f"{label}{_LIMITATION_NOTE_KEY}")
        if method and method not in MEASUREMENT_METHODS:
            raise ParameterError(
                f"{label}{_METHOD_KEY}={method!r} は測定手順として認められない"
                f"（指定できるのは {list(MEASUREMENT_METHODS)!r} と未記入の空文字のみ）。"
            )
        if self.effective_rolling_diameter_mm is not None:
            _require_positive(
                self.effective_rolling_diameter_mm, f"{label}{_DIAMETER_KEY}"
            )
        # ⚠️ **値と手順は同時に埋まる。** 手順の無い値は、どこまで信じてよいかを
        # 持たない数である（要件 10.4）。値の無い手順は測定ではない。
        if (self.effective_rolling_diameter_mm is None) != (not method):
            raise ParameterError(
                f"{label}{_DIAMETER_KEY}="
                f"{self.effective_rolling_diameter_mm!r} と "
                f"{label}{_METHOD_KEY}={method!r} は同時に記入する"
                "（値だけ・手順だけの記録は測定として成立しない）。"
            )
        if method in NON_ROLLING_METHODS and _is_blank(note):
            raise MeasurementError(
                f"{label}{_LIMITATION_NOTE_KEY} が空である。手順 {method!r} は"
                "転動を伴わないため、その測定が捉えられない範囲を記述すること"
                f"（要件 10.5）。{DOWNSTREAM_ROLLING_CALIBRATION}"
            )


@dataclass(frozen=True, slots=True)
class ClearanceObservation:
    """1部位の床との隙間の実測と設計値との差（要件 4.5）。

    Attributes:
        name: 部位名。⚠️ **`clearance.CLEARANCE_ITEM_NAMES` のいずれか**である。
        measured_mm: 実測値（mm）。未記入は `None`。⚠️ 負にもなり得る
            （`clearance.ClearanceItem` と同じく、床へ垂れた配線は負である）。
        design_mm: 同じ部位の設計上の値（mm）。`clearance.clearance_items` の
            戻り値を書き写す欄である。
        difference_mm: `measured_mm - design_mm`（mm）。

    Raises:
        MeasurementError: 部位名が算出側の一覧に無い場合。
        ParameterError: 3つの値が半端に埋まっている場合、または型が不正な場合。
        ConsistencyError: 差が実測と設計値から導かれる値と食い違う場合。
    """

    name: str
    measured_mm: float | None
    design_mm: float | None
    difference_mm: float | None

    def __post_init__(self) -> None:
        name = _require_str(self.name, f"{_CLEARANCES_KEY}.{_NAME_KEY}")
        if name not in CLEARANCE_ITEM_NAMES:
            raise MeasurementError(
                f"{_CLEARANCES_KEY}: 部位名 {name!r} は算出側の部位と一致しない"
                f"（指定できるのは {list(CLEARANCE_ITEM_NAMES)!r} のみ）。"
            )
        values = (self.measured_mm, self.design_mm, self.difference_mm)
        blanks = sum(1 for value in values if value is None)
        if blanks not in (0, len(values)):
            raise ParameterError(
                f"{_clearance_path(name, '')}: 実測・設計値・差は同時に記入する"
                f"（{_MEASURED_KEY}={self.measured_mm!r} "
                f"{_DESIGN_KEY}={self.design_mm!r} "
                f"{_DIFFERENCE_KEY}={self.difference_mm!r}）。"
            )
        if blanks:
            return
        measured = _require_finite(
            self.measured_mm, _clearance_path(name, _MEASURED_KEY)
        )
        design = _require_finite(self.design_mm, _clearance_path(name, _DESIGN_KEY))
        difference = _require_finite(
            self.difference_mm, _clearance_path(name, _DIFFERENCE_KEY)
        )
        # ⚠️ **差は独立な値ではない。** 独立に書けるなら、同じ量が2箇所で
        # 管理されているのと変わらない（要件 4.5）。
        if not _agrees(difference, measured - design):
            raise ConsistencyError(
                f"{_clearance_path(name, _DIFFERENCE_KEY)}={difference!r} は "
                f"{_MEASURED_KEY}={measured!r} − {_DESIGN_KEY}={design!r} = "
                f"{measured - design!r} と一致しなければならない。"
            )


@dataclass(frozen=True, slots=True)
class FitDeviation:
    """組立時に見つかった設計値との差（要件 9.7）。

    干渉した箇所・締結できなかった箇所を、設計値と実際の値の対として残す。
    ⚠️ **記録しただけでは終わらない。** 寸法パラメータへ反映するまでが要件 9.7
    であり、`reflected_in_parameters` が偽の差分は `missing_observations` が
    未了として拾う。

    Attributes:
        location: 差分が出た箇所の名（自由記述）。空であってはならない。
        design_mm: 設計上の値（mm）。
        actual_mm: 実際の値（mm）。
        reflected_in_parameters: 寸法パラメータ（`dimensions.json`）へ反映済みか。

    Raises:
        ParameterError: 箇所名が空の場合、値が有限でない場合、
            または反映済みの印が真偽値でない場合。
    """

    location: str
    design_mm: float
    actual_mm: float
    reflected_in_parameters: bool

    def __post_init__(self) -> None:
        location = _require_str(self.location, f"{_FIT_DEVIATIONS_KEY}.{_LOCATION_KEY}")
        if _is_blank(location):
            raise ParameterError(
                f"{_FIT_DEVIATIONS_KEY}.{_LOCATION_KEY} が空である"
                "（どこで出た差分かが分からなければ現物にも設定ファイルにも戻れない）。"
            )
        _require_finite(self.design_mm, f"{_FIT_DEVIATIONS_KEY}.{_DESIGN_KEY}")
        _require_finite(self.actual_mm, f"{_FIT_DEVIATIONS_KEY}.{_ACTUAL_KEY}")
        if not isinstance(self.reflected_in_parameters, bool):
            raise ParameterError(
                f"{_FIT_DEVIATIONS_KEY}.{_REFLECTED_KEY}="
                f"{self.reflected_in_parameters!r} は真偽値でなければならない。"
            )


@dataclass(frozen=True, slots=True)
class AssemblyCheck:
    """手による確認1件の結果（要件 5.7, 9.5, 9.6）。

    ⚠️ **どの確認もモータへ通電しない。** 手でホイールを回す・手で押す・目視する
    のいずれかである（要件 9.8）。

    Attributes:
        name: 確認の名。`REQUIRED_CHECK_NAMES` のいずれか。
        result: `CHECK_RESULTS` のいずれか。初期状態は `pending`。
        note: 観察の記述。⚠️ **不合格の場合は必須**である。

    Raises:
        MeasurementError: 確認の名が既知の一覧に無い場合、または不合格に記述が
            無い場合。
        ParameterError: 結果が既知の値でない場合、または型が不正な場合。
    """

    name: str
    result: str
    note: str

    def __post_init__(self) -> None:
        name = _require_str(self.name, f"{_CHECKS_KEY}.{_NAME_KEY}")
        if name not in REQUIRED_CHECK_NAMES:
            raise MeasurementError(
                f"{_CHECKS_KEY}: 確認の名 {name!r} は既知の確認項目と一致しない"
                f"（指定できるのは {list(REQUIRED_CHECK_NAMES)!r} のみ）。"
            )
        result = _require_str(self.result, f"{_check_path(name)}.{_RESULT_KEY}")
        if result not in CHECK_RESULTS:
            raise ParameterError(
                f"{_check_path(name)}.{_RESULT_KEY}={result!r} は結果として"
                f"認められない（指定できるのは {list(CHECK_RESULTS)!r} のみ）。"
            )
        note = _require_str(self.note, f"{_check_path(name)}.{_NOTE_KEY}")
        if result == _FAIL_RESULT and _is_blank(note):
            raise MeasurementError(
                f"{_check_path(name)}.{_NOTE_KEY} が空である。不合格の確認は"
                "何がどうだったのかを記述すること（記述の無い不合格からは"
                "現物へ戻れない）。"
            )


@dataclass(frozen=True, slots=True)
class AssemblyRecord:
    """組立後の観測の集約（design.md `#### Assembly` Service Interface）。

    ⚠️ **全項目が未記入の状態も正当な記録である**（未記入は `None` / 空文字 /
    `pending`）。組立が始まる前から読み込めることが要件であり、未記入であることは
    `missing_observations` が報せる（モジュール docstring）。

    ⚠️ **design.md の Service Interface が `mass_g: float` と書く箇所を
    `float | None` としている。** これは設計からの逸脱ではなく、同じ design.md
    と tasks.md タスク 2.4 が課す「全項目が未記入の初期状態を読み込めること」を
    満たすための表現である——未記入を 0.0 のような番兵で表せば、重心の半径方向の
    偏り 0.0mm（完全に中心にある、という正当な観測）と区別できなくなる。

    Attributes:
        schema_version: 記録形式の版。`config.SCHEMA_VERSION` と一致すること。
        mass_g: 実測重量（g、要件 10.1）。未記入は `None`。
        cog_height_mm: 重心の高さ（mm、床を原点とする。要件 10.2）。
        cog_radial_offset_mm: 重心の半径方向の偏り（mm、機体軸からの距離）。
            ⚠️ 0.0 は「偏りが無い」という**測定結果**であり未記入ではない。
        cog_method: 重心の測定手順（要件 10.2）。値だけでは手順を再現できない。
        wheels: ホイール3個の観測。⚠️ **常に3件**であり、輪番号は 0 から順である。
        representative_wheel_diameter_mm: 代表値（mm）。⚠️ **3個の平均**である。
        clearances: 5部位の隙間の観測。⚠️ **常に `CLEARANCE_ITEM_NAMES` の順で
            5件**である。
        fit_deviations: 組立時の差分（要件 9.7）。⚠️ **空は正当な状態である**
            ——「差分が1件も出なかった」という結果を表す。
        checks: 手による確認3件。⚠️ **常に `REQUIRED_CHECK_NAMES` の順**である。
        provenance: 観測パスから出所への対応表。キー集合は `OBSERVATION_PATHS`
            と一致する（要件 10.9）。

    Raises:
        ParameterError: 版・型・範囲・出所表が不正な場合。
        MeasurementError: 枠（5部位・3輪・3確認）が欠けている場合、または
            3個そろわないうちに代表値だけが記入されている場合。
        ConsistencyError: 代表値が3個の平均と一致しない場合。
    """

    schema_version: str
    mass_g: float | None
    cog_height_mm: float | None
    cog_radial_offset_mm: float | None
    cog_method: str
    wheels: tuple[WheelObservation, ...]
    representative_wheel_diameter_mm: float | None
    clearances: tuple[ClearanceObservation, ...]
    fit_deviations: tuple[FitDeviation, ...]
    checks: tuple[AssemblyCheck, ...]
    provenance: Mapping[str, Provenance]

    def __post_init__(self) -> None:
        version = _require_str(self.schema_version, _SCHEMA_VERSION_KEY)
        if version != SCHEMA_VERSION:
            raise ParameterError(
                f"{_SCHEMA_VERSION_KEY}={version!r} は対応していない"
                f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
            )
        if self.mass_g is not None:
            _require_positive(self.mass_g, _MASS_PATH)
        if self.cog_height_mm is not None:
            _require_positive(self.cog_height_mm, _COG_HEIGHT_PATH)
        if self.cog_radial_offset_mm is not None:
            _require_nonnegative(self.cog_radial_offset_mm, _COG_OFFSET_PATH)
        _require_str(self.cog_method, _COG_METHOD_KEY)

        self._validate_frame(
            self.wheels,
            WheelObservation,
            tuple(str(index) for index in range(WHEEL_OBSERVATION_COUNT)),
            lambda wheel: str(wheel.index),
            _WHEELS_KEY,
        )
        self._validate_frame(
            self.clearances,
            ClearanceObservation,
            CLEARANCE_ITEM_NAMES,
            lambda item: item.name,
            _CLEARANCES_KEY,
        )
        self._validate_frame(
            self.checks,
            AssemblyCheck,
            REQUIRED_CHECK_NAMES,
            lambda check: check.name,
            _CHECKS_KEY,
        )
        if not isinstance(self.fit_deviations, tuple) or not all(
            isinstance(item, FitDeviation) for item in self.fit_deviations
        ):
            raise ParameterError(
                f"{_FIT_DEVIATIONS_KEY}={self.fit_deviations!r} は "
                "FitDeviation の並びでなければならない。"
            )
        self._validate_representative()
        self._validate_provenance()

    @staticmethod
    def _validate_frame(
        items: object,
        expected_type: type,
        expected_keys: tuple[str, ...],
        key_of: Callable[[object], str],
        label: str,
    ) -> None:
        """記録の**枠**（件数・名・並び）を検証する。

        ⚠️ **枠が欠けた記録を受け付けない。** 受け付ければ「測っていない部位」と
        「そもそも見ていない部位」が一覧の上で区別できなくなる（design.md
        `#### Assembly` Integration）。
        """
        if not isinstance(items, tuple) or not all(
            isinstance(item, expected_type) for item in items
        ):
            raise ParameterError(
                f"{label}={items!r} は {expected_type.__name__} の並びでなければ"
                "ならない。"
            )
        observed = tuple(key_of(item) for item in items)
        if observed != expected_keys:
            raise MeasurementError(
                f"{label}: 記録の枠が {list(observed)!r} である。"
                f"{list(expected_keys)!r} と同じ並びで過不足なく持つこと"
                "（未記入のまま枠だけを置く。枠を省くと、測っていない項目と"
                "見ていない項目が区別できない）。"
            )

    def _validate_representative(self) -> None:
        """代表値と3個の平均の一致を検証する（design.md Invariants）。"""
        if self.representative_wheel_diameter_mm is None:
            return
        representative = _require_positive(
            self.representative_wheel_diameter_mm, _REPRESENTATIVE_PATH
        )
        blanks = [
            _wheel_path(wheel.index, _DIAMETER_KEY)
            for wheel in self.wheels
            if wheel.effective_rolling_diameter_mm is None
        ]
        if blanks:
            raise MeasurementError(
                f"{_REPRESENTATIVE_PATH}={representative!r} が記入されているが、"
                f"個々の実測が欠けている: {blanks!r}"
                "（代表値は3個の平均であり、3個そろう前には決まらない）。"
            )
        diameters = [
            wheel.effective_rolling_diameter_mm
            for wheel in self.wheels
            if wheel.effective_rolling_diameter_mm is not None
        ]
        mean = _mean(diameters)
        # ⚠️ **代表値を独立に書けるなら、実効転がり径は2箇所で管理されている
        # のと変わらない**（design.md Invariants /「Revalidation Triggers」項目3）。
        if not _agrees(representative, mean):
            raise ConsistencyError(
                f"{_REPRESENTATIVE_PATH}={representative!r} は "
                f"{len(diameters)}個の平均 {mean!r} と一致しなければならない"
                f"（差 {abs(representative - mean)!r}mm、"
                f"許容差 {DERIVED_ABS_TOLERANCE_MM!r}mm）。"
            )

    def _validate_provenance(self) -> None:
        """出所表のキー集合が `OBSERVATION_PATHS` と一致することを要求する。"""
        if not isinstance(self.provenance, Mapping):
            raise ParameterError(
                f"{_PROVENANCE_KEY}={self.provenance!r} はパスから出所への"
                "対応表でなければならない。"
            )
        for key, value in self.provenance.items():
            if key not in OBSERVATION_PATHS:
                raise ParameterError(
                    f"{_PROVENANCE_KEY} のキー {key!r} は OBSERVATION_PATHS の"
                    f"パス文字列と一致しない（既知のパスは {list(OBSERVATION_PATHS)!r}）。"
                )
            if not isinstance(value, Provenance):
                raise ParameterError(
                    f"{_PROVENANCE_KEY}[{key!r}]={value!r} は Provenance で"
                    f"なければならない（指定できるのは {Provenance.MEASURED!r} と "
                    f"{Provenance.ASSUMED!r} のみ）。"
                )
        missing = sorted(set(OBSERVATION_PATHS) - set(self.provenance))
        if missing:
            raise ParameterError(
                f"{_PROVENANCE_KEY} に出所の無い観測がある: "
                + ", ".join(missing)
                + "（要件 10.9: 実測値と概算値を利用側が区別できる形で記録する）。"
            )
        # 検証後に中身が差し替わらないよう、呼び出し側の辞書とのエイリアスを切る
        # （`params.ChassisParams` と同じ。⚠️ `MappingProxyType` で包まない）。
        object.__setattr__(self, _PROVENANCE_KEY, dict(self.provenance))


# ---------------------------------------------------------------------------
# 完了の判定（要件 9.8）
# ---------------------------------------------------------------------------

_BLANK_LABEL: Final[str] = "未記入"
_PROVISIONAL_LABEL: Final[str] = "仮値（出所が assumed のまま）"
_PENDING_LABEL: Final[str] = "未実施"
_FAILED_LABEL: Final[str] = "不合格"
_UNREFLECTED_LABEL: Final[str] = "寸法パラメータへ未反映"


def _value_state(
    path: str, value: object | None, provenance: Mapping[str, Provenance]
) -> str | None:
    """1つの観測値が未了かどうかを判定し、未了なら理由つきの1行を返す。

    ⚠️ **未記入と仮値を1つの項目で二重に数えない。** 未記入の値の出所は必ず
    仮値であるため、先に未記入を返す——現物へ戻るときにやることは「測る」の1つ
    だからである。
    """
    if value is None:
        return f"{path}: {_BLANK_LABEL}"
    if provenance[path] != Provenance.MEASURED:
        return f"{path}: {_PROVISIONAL_LABEL}"
    return None


def missing_observations(record: AssemblyRecord) -> tuple[str, ...]:
    """未了の必須項目を**全件**返す（要件 9.8 / design.md `#### Assembly`）。

    返る順は観測の6群の順（重量 → 重心 → ホイール → 隙間 → 組立時の差分 →
    手による確認）で固定である。⚠️ **値によって並べ替えない**——順序が入れ替われば
    2回の実行の差分として読めなくなる。

    各行は「パス: 理由」の形であり、理由は次の5種である。

    - `未記入`: 値が無い。現物を測る。
    - `仮値（出所が assumed のまま）`: 値はあるが概算である。測り直して出所を
      実測へ更新する（design.md Risks）。
    - `未実施`: 手による確認をまだ行っていない。
    - `不合格`: 手による確認が通っていない。⚠️ **不合格を完了と呼ばない。**
    - `寸法パラメータへ未反映`: 組立時の差分を記録しただけで `dimensions.json`
      へ反映していない（要件 9.7）。

    ⚠️ **モータへ通電する項目は1つも無い**（要件 9.8, 9.9）。ここに挙がるのは
    静止した機体に対する測定と手による確認だけである。

    Args:
        record: 判定する観測記録。

    Returns:
        未了の項目。すべて満たされていれば空タプル。
    """
    provenance = record.provenance
    outstanding: list[str] = []

    # 1群: 実測重量（要件 10.1）
    outstanding.append(_value_state(_MASS_PATH, record.mass_g, provenance))
    # 2群: 重心（要件 10.2）。⚠️ 手順は値ではないため出所を持たない。
    outstanding.append(
        _value_state(_COG_HEIGHT_PATH, record.cog_height_mm, provenance)
    )
    outstanding.append(
        _value_state(_COG_OFFSET_PATH, record.cog_radial_offset_mm, provenance)
    )
    outstanding.append(
        f"{_COG_METHOD_KEY}: {_BLANK_LABEL}" if _is_blank(record.cog_method) else None
    )
    # 3群: ホイール3個と代表値（要件 10.3, 10.4）
    for wheel in record.wheels:
        outstanding.append(
            _value_state(
                _wheel_path(wheel.index, _DIAMETER_KEY),
                wheel.effective_rolling_diameter_mm,
                provenance,
            )
        )
        outstanding.append(
            f"{_wheel_path(wheel.index, _METHOD_KEY)}: {_BLANK_LABEL}"
            if not wheel.method
            else None
        )
    outstanding.append(
        _value_state(
            _REPRESENTATIVE_PATH, record.representative_wheel_diameter_mm, provenance
        )
    )
    # 4群: 5部位の隙間（要件 4.5）
    for item in record.clearances:
        outstanding.append(
            _value_state(
                _clearance_path(item.name, _MEASURED_KEY), item.measured_mm, provenance
            )
        )
    # 5群: 組立時の差分（要件 9.7）。⚠️ 空は正当な状態であり、未了ではない。
    for index, deviation in enumerate(record.fit_deviations):
        outstanding.append(
            f"{_FIT_DEVIATIONS_KEY}.{index}（{deviation.location}）: "
            f"{_UNREFLECTED_LABEL}"
            if not deviation.reflected_in_parameters
            else None
        )
    # 6群: 手による確認（要件 5.7, 9.5, 9.6）
    for check in record.checks:
        if check.result == _PENDING_RESULT:
            outstanding.append(f"{_check_path(check.name)}: {_PENDING_LABEL}")
        elif check.result == _FAIL_RESULT:
            outstanding.append(f"{_check_path(check.name)}: {_FAILED_LABEL}")
        else:
            outstanding.append(None)

    return tuple(entry for entry in outstanding if entry is not None)


def is_assembly_complete(record: AssemblyRecord) -> bool:
    """組立完了かどうかを返す（要件 9.8 / design.md Postconditions）。

    ⚠️ **これは `missing_observations(record) == ()` そのものである。**
    条件をここへ足さない——足せば、判定の正が2箇所に分かれる。とりわけ
    **モータへ通電する条件を足さない**（要件 9.8: 組立の完了は通電することなく
    判定する。要件 9.9: 走行・通電・エンコーダの校正は本 Spec の対象外である）。
    `test_chassis_assembly.py` が本関数の本体を `ast` で読み、判定式1文だけで
    あることを固定する。

    Args:
        record: 判定する観測記録。

    Returns:
        未了の項目が1件も無ければ真。
    """
    return missing_observations(record) == ()


def representative_rolling_radius_mm(record: AssemblyRecord) -> float | None:
    """代表値から実効転がり**半径**を返す（design.md `#### Layout` Notes）。

    ⚠️ **径から半径への変換をここ1箇所に置く。** `layout` の鉛直スタックは
    半径で組み上がっており（`layout.VerticalStack.effective_rolling_radius_mm`）、
    観測は径で持つ。両者の変換が2箇所にあれば、片方だけが直径のまま扱われる事故が
    残る。

    ⚠️ **本関数は `layout` を呼ばない。** 依存方向は
    `layout → clearance/joints → assembly` であり、逆流させない。観測を
    `layout._effective_rolling_radius_mm` へ差し込むのは呼び出し側
    （`cli` / 公開 API）の仕事である。

    Args:
        record: 観測記録。

    Returns:
        実効転がり半径（mm）。代表値が未記入なら `None`——このとき `layout` は
        公称値の半分を用いる（`layout.ASSUMPTIONS`）。
    """
    if record.representative_wheel_diameter_mm is None:
        return None
    return record.representative_wheel_diameter_mm / 2.0


# ---------------------------------------------------------------------------
# 直列化
# ---------------------------------------------------------------------------


def _to_document(record: AssemblyRecord) -> dict[str, object]:
    """`record` を記録の形へ写す。

    ⚠️ **数を丸めない**（`layout.dump_layout` / `joints.dump_fastener_schedule`
    との意図した差である）。あちらが丸めるのは、導出結果が実行環境ごとの最下位
    ビットの揺れを持ち得るためである。こちらは**人が書き込んだ観測**であり、
    丸めれば書いた値と読み戻す値が食い違う（代表値は平均であるため、丸めた瞬間に
    3個の平均との一致が許容差の縁に載る）。
    """
    return {
        _SCHEMA_VERSION_KEY: record.schema_version,
        _MASS_PATH: record.mass_g,
        _COG_HEIGHT_PATH: record.cog_height_mm,
        _COG_OFFSET_PATH: record.cog_radial_offset_mm,
        _COG_METHOD_KEY: record.cog_method,
        _WHEELS_KEY: [
            {
                _INDEX_KEY: wheel.index,
                _DIAMETER_KEY: wheel.effective_rolling_diameter_mm,
                _METHOD_KEY: wheel.method,
                _LIMITATION_NOTE_KEY: wheel.limitation_note,
            }
            for wheel in record.wheels
        ],
        _REPRESENTATIVE_PATH: record.representative_wheel_diameter_mm,
        _CLEARANCES_KEY: [
            {
                _NAME_KEY: item.name,
                _MEASURED_KEY: item.measured_mm,
                _DESIGN_KEY: item.design_mm,
                _DIFFERENCE_KEY: item.difference_mm,
            }
            for item in record.clearances
        ],
        _FIT_DEVIATIONS_KEY: [
            {
                _LOCATION_KEY: deviation.location,
                _DESIGN_KEY: deviation.design_mm,
                _ACTUAL_KEY: deviation.actual_mm,
                _REFLECTED_KEY: deviation.reflected_in_parameters,
            }
            for deviation in record.fit_deviations
        ],
        _CHECKS_KEY: [
            {
                _NAME_KEY: check.name,
                _RESULT_KEY: check.result,
                _NOTE_KEY: check.note,
            }
            for check in record.checks
        ],
        _PROVENANCE_KEY: {
            path: record.provenance[path].value for path in OBSERVATION_PATHS
        },
    }


def dump_assembly_record(record: AssemblyRecord, path: Path) -> None:
    """`record` を観測記録として `path` へ書き出す（要件 10.1-10.5, 11.3）。

    整形は `config.dump_params` に揃える（**インデント2・キー整列・末尾改行・LF**）。
    ⚠️ **LF は `.gitattributes` の `configs/chassis_mechanism/*.json text eol=lf`
    と対で成立する**——本関数が書くバイト列が git のチェックアウト内容と同一で
    あるため、値が変わっていなければ `git status` は変更を報告しない。

    Args:
        record: 書き出す観測記録。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(record),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _read_document(path: Path) -> Mapping[str, object]:
    """`path` を UTF-8 テキストとして読み、JSON オブジェクトとして返す。

    ⚠️ **記録が存在しない場合は `MeasurementError` である**（design.md
    `#### Assembly` Preconditions）。読めない記録は「観測が無い」ことと同じであり、
    設定の不正（`ParameterError`）ではない。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MeasurementError(f"{path}: 観測記録を読み込めない: {exc}") from exc
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


def _object(value: object, label: str) -> Mapping[str, object]:
    """`value` を JSON オブジェクトとして取り出す。"""
    if not isinstance(value, dict):
        raise ParameterError(
            f"{label}: オブジェクト（{{...}}）を期待したが "
            f"{type(value).__name__} だった。"
        )
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    """`value` を並びとして取り出す（文字列は並びとして扱わない）。"""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ParameterError(
            f"{label}: 並び（[...]）を期待したが {type(value).__name__} だった。"
        )
    return value


def _number(data: Mapping[str, object], key: str, label: str) -> float:
    """`data[key]` を有限な数として取り出す。"""
    return _require_finite(data[key], f"{label}.{key}")


def _number_or_none(
    data: Mapping[str, object], key: str, label: str
) -> float | None:
    """`data[key]` を有限な数、または未記入（`null`）として取り出す。"""
    if data[key] is None:
        return None
    return _number(data, key, label)


def _text(data: Mapping[str, object], key: str, label: str) -> str:
    """`data[key]` を文字列として取り出す。"""
    return _require_str(data[key], f"{label}.{key}")


def _integer(data: Mapping[str, object], key: str, label: str) -> int:
    """`data[key]` を整数として取り出す。"""
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParameterError(
            f"{label}.{key}={value!r} は整数でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return value


def _boolean(data: Mapping[str, object], key: str, label: str) -> bool:
    """`data[key]` を真偽値として取り出す。"""
    value = data[key]
    if not isinstance(value, bool):
        raise ParameterError(
            f"{label}.{key}={value!r} は真偽値でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    return value


_WHEEL_KEYS: Final[frozenset[str]] = frozenset(
    {_INDEX_KEY, _DIAMETER_KEY, _METHOD_KEY, _LIMITATION_NOTE_KEY}
)
_CLEARANCE_KEYS: Final[frozenset[str]] = frozenset(
    {_NAME_KEY, _MEASURED_KEY, _DESIGN_KEY, _DIFFERENCE_KEY}
)
_FIT_DEVIATION_KEYS: Final[frozenset[str]] = frozenset(
    {_LOCATION_KEY, _DESIGN_KEY, _ACTUAL_KEY, _REFLECTED_KEY}
)
_CHECK_KEYS: Final[frozenset[str]] = frozenset({_NAME_KEY, _RESULT_KEY, _NOTE_KEY})
_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        _SCHEMA_VERSION_KEY,
        _MASS_PATH,
        _COG_HEIGHT_PATH,
        _COG_OFFSET_PATH,
        _COG_METHOD_KEY,
        _WHEELS_KEY,
        _REPRESENTATIVE_PATH,
        _CLEARANCES_KEY,
        _FIT_DEVIATIONS_KEY,
        _CHECKS_KEY,
        _PROVENANCE_KEY,
    }
)

_PROVENANCE_VALUES: Final[Mapping[str, Provenance]] = {
    provenance.value: provenance for provenance in Provenance
}


def _wheel_from_document(value: object, label: str) -> WheelObservation:
    """記録の1件を `WheelObservation` へ読み戻す（不変条件は型が検証する）。"""
    data = _object(value, label)
    _reject_unknown_and_missing(data, _WHEEL_KEYS, label)
    return WheelObservation(
        index=_integer(data, _INDEX_KEY, label),
        effective_rolling_diameter_mm=_number_or_none(data, _DIAMETER_KEY, label),
        method=_text(data, _METHOD_KEY, label),
        limitation_note=_text(data, _LIMITATION_NOTE_KEY, label),
    )


def _clearance_from_document(value: object, label: str) -> ClearanceObservation:
    """記録の1件を `ClearanceObservation` へ読み戻す。"""
    data = _object(value, label)
    _reject_unknown_and_missing(data, _CLEARANCE_KEYS, label)
    return ClearanceObservation(
        name=_text(data, _NAME_KEY, label),
        measured_mm=_number_or_none(data, _MEASURED_KEY, label),
        design_mm=_number_or_none(data, _DESIGN_KEY, label),
        difference_mm=_number_or_none(data, _DIFFERENCE_KEY, label),
    )


def _fit_deviation_from_document(value: object, label: str) -> FitDeviation:
    """記録の1件を `FitDeviation` へ読み戻す。"""
    data = _object(value, label)
    _reject_unknown_and_missing(data, _FIT_DEVIATION_KEYS, label)
    return FitDeviation(
        location=_text(data, _LOCATION_KEY, label),
        design_mm=_number(data, _DESIGN_KEY, label),
        actual_mm=_number(data, _ACTUAL_KEY, label),
        reflected_in_parameters=_boolean(data, _REFLECTED_KEY, label),
    )


def _check_from_document(value: object, label: str) -> AssemblyCheck:
    """記録の1件を `AssemblyCheck` へ読み戻す。"""
    data = _object(value, label)
    _reject_unknown_and_missing(data, _CHECK_KEYS, label)
    return AssemblyCheck(
        name=_text(data, _NAME_KEY, label),
        result=_text(data, _RESULT_KEY, label),
        note=_text(data, _NOTE_KEY, label),
    )


def _provenance_from_document(value: object, label: str) -> dict[str, Provenance]:
    """出所表を読み戻す（キー集合は `OBSERVATION_PATHS` と一致すること）。"""
    data = _object(value, label)
    _reject_unknown_and_missing(data, frozenset(OBSERVATION_PATHS), label)
    provenance: dict[str, Provenance] = {}
    for path in OBSERVATION_PATHS:
        raw = data[path]
        if not isinstance(raw, str) or raw not in _PROVENANCE_VALUES:
            raise ParameterError(
                f"{label}.{path}={raw!r} は出所として認められない"
                f"（指定できるのは {sorted(_PROVENANCE_VALUES)!r} のみ）。"
            )
        provenance[path] = _PROVENANCE_VALUES[raw]
    return provenance


def load_assembly_record(path: Path | None = None) -> AssemblyRecord:
    """観測記録を読み戻す（要件 4.5, 9.7, 10.1-10.5, 10.9, 11.3）。

    ⚠️ **全項目が未記入の記録も読み込みは成功する。** 読み込みが見るのは構造と
    整合だけであり、「実測が済んでいるか」は `missing_observations` の問題である
    （モジュール docstring）。読み込み自体が失敗すると、組立前に他の検査を
    回せなくなる（tasks.md タスク 2.4）。

    ⚠️ **欠けている項目を既定値で埋めない**（`errors.MeasurementError`）。
    埋めた瞬間に、実測されていない値が実測値として下流へ流れる。

    Args:
        path: 読み込む記録。`None` なら `DEFAULT_MEASUREMENTS_PATH`。

    Returns:
        読み戻した観測記録。

    Raises:
        MeasurementError: 記録が読めない場合（存在しない場合を含む）、記録の枠が
            欠けている場合、転動を伴わない測定に限界の記述が無い場合、
            不合格の確認に記述が無い場合。
        ParameterError: 構造・型・範囲・出所が不正な場合。
        ConsistencyError: 代表値が3個の平均と食い違う場合、または隙間の差が
            実測と設計値から導かれる値と食い違う場合。
    """
    target = DEFAULT_MEASUREMENTS_PATH if path is None else path
    label = str(target)
    document = _read_document(target)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)
    return AssemblyRecord(
        schema_version=_text(document, _SCHEMA_VERSION_KEY, label),
        mass_g=_number_or_none(document, _MASS_PATH, label),
        cog_height_mm=_number_or_none(document, _COG_HEIGHT_PATH, label),
        cog_radial_offset_mm=_number_or_none(document, _COG_OFFSET_PATH, label),
        cog_method=_text(document, _COG_METHOD_KEY, label),
        wheels=tuple(
            _wheel_from_document(item, f"{label}.{_WHEELS_KEY}[{index}]")
            for index, item in enumerate(
                _sequence(document[_WHEELS_KEY], f"{label}.{_WHEELS_KEY}")
            )
        ),
        representative_wheel_diameter_mm=_number_or_none(
            document, _REPRESENTATIVE_PATH, label
        ),
        clearances=tuple(
            _clearance_from_document(item, f"{label}.{_CLEARANCES_KEY}[{index}]")
            for index, item in enumerate(
                _sequence(document[_CLEARANCES_KEY], f"{label}.{_CLEARANCES_KEY}")
            )
        ),
        fit_deviations=tuple(
            _fit_deviation_from_document(
                item, f"{label}.{_FIT_DEVIATIONS_KEY}[{index}]"
            )
            for index, item in enumerate(
                _sequence(
                    document[_FIT_DEVIATIONS_KEY], f"{label}.{_FIT_DEVIATIONS_KEY}"
                )
            )
        ),
        checks=tuple(
            _check_from_document(item, f"{label}.{_CHECKS_KEY}[{index}]")
            for index, item in enumerate(
                _sequence(document[_CHECKS_KEY], f"{label}.{_CHECKS_KEY}")
            )
        ),
        provenance=_provenance_from_document(
            document[_PROVENANCE_KEY], f"{label}.{_PROVENANCE_KEY}"
        ),
    )
