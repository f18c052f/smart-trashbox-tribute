"""寸法パラメータの不変表現と構築時検証（design.md「Params」/ 要件 1.1, 1.2,
1.4, 1.5, 1.8, 2.1, 2.5, 2.6, 8.6, 9.4, 10.1）。

ゴミ箱の採寸値・対象物・造形制約・継手方針・受け口・保持方針を、すべて
`frozen=True, slots=True` のデータクラスとして定義し、`MechanismParams` を
**唯一の集約ルート**として束ねる（design.md「Domain Model」）。値等価であり、
設定ファイルの識別子（`config.parameters_digest`、タスク 1.4）を値から計算できる。

**実物の寸法に既定値を与えない。** `TrashCanMeasurements` / `ObjectSpec` /
`PrintingConstraints` / `JointPolicy` / `RimParams` の全フィールドは dataclass の
必須フィールドであり、省略した構築は `TypeError` として失敗する（要件 1.4、
`trajectory_sim.DrivetrainParams` と同じ扱い）。既定値があると、設定ファイルに
書き忘れた項目が「もっともらしい数」で黙って埋まり、未実測の値が実測のふりを
する。既定値を持つのは `RetentionParams.added_depth_mm = 0.0` と
`RetentionParams.bottom_modification = "none"` の2つだけで、これらは実測値では
なく**設計上の決定**である（design.md「受け口形状の決定」）。

出所（`Provenance`）は **`MEASURED` / `ASSUMED` の2値**とする。⚠️ **第3の値を
作らない。** `trajectory_sim.Provenance` と値集合を一致させ、還元時に翻訳が
要らないようにするためであり（design.md「Params」Responsibilities）、導出量は
独自の出所を持たず `Provenance.weakest` で**入力の最弱を継承する**（要件 1.5:
1つでも仮値を含めば仮値）。⚠️ **`trajectory_sim.params` の型は import しない**
——依存方向が逆になる（design.md「Params」Implementation Notes）。一致させるのは
値集合だけであり、その一致は `tests/catch_mechanism/test_catch_params.py` が
両者を突き合わせて固定する。

`PARAMETER_PATHS` は `MechanismParams` のデータクラス木を `dataclasses.fields()`
で走査して構築時に一度だけ生成し、手書きの表を二重管理しない（要件 1.8 /
design.md「Params」Responsibilities）。単位はフィールド名の接尾辞から導く
（`_SUFFIX_UNITS` 参照）。`ParameterPath` はこの表のエントリの形であり、design.md
本文には内部構造が明記されていないため、本モジュールが「パス文字列・単位・所属
コンポーネント・フィールド名・値の型」を持つ最小限の frozen dataclass として
定義する（設計ギャップの補完）。値の型を持たせるのは、設定読み込み（`config`、
タスク 1.4）が JSON の各値をこの表だけで検証できるようにするためである。

検証は各データクラスの `__post_init__` に置き、**違反フィールド名と値**を含む
メッセージで `catch_mechanism.errors.ParameterError` を送出する（要件 1.4:
「該当する項目名と値を示す」/ design.md「Params」Validation。`trajectory_sim` の
慣行に合わせる）。構築を通った `MechanismParams` は以降の層で再検証を要さない
（design.md「Params」Postconditions）ため、`provenance` は構築時に**素の `dict`
として複製**し、呼び出し側の辞書との参照の共有を切る。⚠️ ここを
`MappingProxyType` で包むと `dataclasses.asdict()` / `copy.deepcopy()` /
`pickle` が `TypeError: cannot pickle 'mappingproxy' object` で落ち、集約の
直列化（タスク 1.4 の `dump_params` / `parameters_digest`）が壊れる。上流の
`trajectory_sim.ScenarioParams.provenance` も素の `dict` を保持する。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import get_type_hints

from catch_mechanism.errors import ParameterError

__all__ = [
    "Provenance",
    "TrashCanMeasurements",
    "ObjectSpec",
    "PrintingConstraints",
    "JointPolicy",
    "RimParams",
    "RetentionParams",
    "MechanismParams",
    "ParameterPath",
    "PARAMETER_PATHS",
    "ALLOWED_MATERIALS",
]


ALLOWED_MATERIALS: frozenset[str] = frozenset({"PETG", "PLA"})
"""造形に用いてよい材料の一覧（要件 2.5 / design.md「Constraints」）。

⚠️ **ASA / ABS / PC / PA / CF・GF は含めない。** 反りと臭気、あるいは乾燥・高温
ノズル・摩耗対策といった設備要求が、手元の造形環境の前提を超えるためである
（design.md「Constraints」Responsibilities）。PLA は非構造部材（治具・確認用の
試作）に限る想定であり、荷重を受ける受け口本体は PETG を前提とする。

一覧に無い材料は `PrintingConstraints.__post_init__` が構築時に拒否する。
表記揺れ（`"petg"` / `"PETG "`）は**黙って正規化しない**——正規化を許すと、
許可一覧そのものが実質的に曖昧になる。
"""


def _require_nonneg_finite(value: float, name: str) -> None:
    """`value` が 0 以上の有限値であることを検証する。"""
    if not (math.isfinite(value) and value >= 0.0):
        raise ParameterError(f"{name}={value!r} は 0 以上の有限値でなければならない。")


def _require_positive_finite(value: float, name: str) -> None:
    """`value` が正の有限値であることを検証する（design.md「Params」Preconditions）。

    長さ・直径・質量・面積はいずれも 0 を含まない。0 の厚みや 0 の直径は形状として
    成立せず、設定ファイルの書き忘れ（未入力の 0）を通してしまう。
    """
    if not (math.isfinite(value) and value > 0.0):
        raise ParameterError(f"{name}={value!r} は正の有限値でなければならない。")


def _require_angle_deg(value: float, name: str) -> None:
    """`value` が 0 以上 90 度未満の有限値であることを検証する。

    design.md「Params」Preconditions が定める角度の値域である。テーパー角も
    フランジの傾斜角も**片側**の角度であり、90 度に達すると面が軸と平行になって
    形状が成立しない。0 は許す（テーパー 0 = 円筒は有効な形状である）。
    """
    if not (math.isfinite(value) and 0.0 <= value < 90.0):
        raise ParameterError(f"{name}={value!r} は 0 以上 90 度未満の有限値でなければならない。")


def _require_nonempty_str(value: str, name: str) -> None:
    """`value` が空でない文字列であることを検証する。"""
    if not isinstance(value, str) or not value:
        raise ParameterError(f"{name}={value!r} は空でない文字列でなければならない。")


class Provenance(StrEnum):
    """パラメータ値の出所（実測 / 仮値）（要件 1.2, 1.5, 9.4, 10.4）。

    ⚠️ **2値ちょうどである。** `trajectory_sim.Provenance` と値集合を一致させ、
    キャッチ許容誤差をシミュレータ設定へ還元するときに翻訳を要らなくする
    （design.md「Params」Responsibilities /「許容誤差の還元」）。導出量のための
    第3の値（"derived" のようなもの）を作らない——導出量の出所は
    `weakest` で入力から決まるため不要であり、値集合が食い違えば下流の設定
    ファイルが受け付けない値が生まれるだけである。

    `StrEnum` であるため `str` としてそのまま JSON へ書ける（design.md
    Implementation Notes）。
    """

    MEASURED = "measured"
    ASSUMED = "assumed"

    @staticmethod
    def weakest(*values: "Provenance") -> "Provenance":
        """入力の最弱の出所を返す（要件 1.5）。

        半順序は `MEASURED` > `ASSUMED`（design.md「Domain Model」）。1つでも
        `ASSUMED` を含めば結果は `ASSUMED` であり、すべてが `MEASURED` のときに
        限り `MEASURED` を返す。⚠️ **未実測の推定が実測を名乗って合否条件へ
        紛れ込むことを防ぐ**のがこの規則の目的である。

        Args:
            *values: 導出に用いた入力それぞれの出所。1つ以上必要である。

        Returns:
            入力の最弱の出所。

        Raises:
            ParameterError: 入力が空、または `Provenance` 以外を含む場合。
                空を `MEASURED` と解釈すると、入力を1つも持たない値が実測を
                名乗ってしまう。
        """
        if not values:
            raise ParameterError(
                "Provenance.weakest には1つ以上の出所が必要である"
                "（入力が無ければ導出量の出所は定まらない）。"
            )
        for value in values:
            if not isinstance(value, Provenance):
                raise ParameterError(
                    f"Provenance.weakest の入力 {value!r} は Provenance でなければならない"
                    f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
                )
        if any(value is Provenance.ASSUMED for value in values):
            return Provenance.ASSUMED
        return Provenance.MEASURED


@dataclass(frozen=True, slots=True)
class TrashCanMeasurements:
    """選定したゴミ箱の機種識別情報と採寸値（要件 1.1, 1.4, 6.5, 6.8, 8.6, 10.1）。

    下流（`chassis-mechanism`）が底の外径・平面部径・テーパー角・高さ・重量を
    ここから参照し、同じ値を再定義しない（要件 10.1）。

    Attributes:
        model_id: 採寸した実物の機種識別子（例: `"yamada-kagaku-no335"`）。
            ⚠️ 寸法ではないが**同じ型に載せる**。要件 6.8 は選定結果と寸法設定を
            同じ正の上で突き合わせることを求めており、この識別子が無いと
            「どの実物を測った値なのか」が設定ファイルから失われ、選定結果との
            照合（タスク 5.1）が成立しない。design.md `## Data Models` の
            `dimensions.json` 例が `trash_can.model_id` を持つことに合わせる
            （`#### Params` の Service Interface には現れないが、`## Data Models`
            側・要件 6.8・タスク 5.1 と整合する形を採る）。空文字は許さない。
        opening_inner_diameter_mm: 開口の内径（mm）。キャッチ判定の許容誤差
            （`tolerance`）の分母となる、本 Spec で最も効く1値である。
        top_outer_diameter_mm: 上端の外径（mm）。受け口の取り付け部内径は
            この値と `RimParams.fit_clearance_mm` から導出する（要件 8.5）。
        bottom_outer_diameter_mm: 底の外径（mm）。駆動ベースの固定径の入力。
        bottom_flat_diameter_mm: 底の平面部の径（mm）。角の丸みを除いた、
            実際に接地・固定できる平面の径である。
        height_mm: 全高（mm）。
        mass_g: 実測重量（g）。
        bottom_thickness_mm: 底の肉厚（mm）。
        taper_deg: 片側のテーパー角（度）。0 以上 90 度未満。

    Raises:
        ParameterError: 機種識別子が空文字の場合、長さ・直径・質量が正の有限値で
            ない場合、テーパー角が値域を外れる場合、または径の大小関係
            （`bottom_flat <= bottom_outer <= opening_inner`）が逆転する場合。
    """

    model_id: str
    opening_inner_diameter_mm: float
    top_outer_diameter_mm: float
    bottom_outer_diameter_mm: float
    bottom_flat_diameter_mm: float
    height_mm: float
    mass_g: float
    bottom_thickness_mm: float
    taper_deg: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_nonempty_str(self.model_id, "model_id")
        _require_positive_finite(self.opening_inner_diameter_mm, "opening_inner_diameter_mm")
        _require_positive_finite(self.top_outer_diameter_mm, "top_outer_diameter_mm")
        _require_positive_finite(self.bottom_outer_diameter_mm, "bottom_outer_diameter_mm")
        _require_positive_finite(self.bottom_flat_diameter_mm, "bottom_flat_diameter_mm")
        _require_positive_finite(self.height_mm, "height_mm")
        _require_positive_finite(self.mass_g, "mass_g")
        _require_positive_finite(self.bottom_thickness_mm, "bottom_thickness_mm")
        _require_angle_deg(self.taper_deg, "taper_deg")
        # 上へ広がるテーパー容器としての整合（design.md「Params」Invariants）。
        # 等号を含むのは、円筒形（テーパー 0）のゴミ箱を排除しないためである。
        if self.bottom_flat_diameter_mm > self.bottom_outer_diameter_mm:
            raise ParameterError(
                f"bottom_flat_diameter_mm={self.bottom_flat_diameter_mm!r} は "
                f"bottom_outer_diameter_mm={self.bottom_outer_diameter_mm!r} 以下で"
                "なければならない（底の平面部は底の外形の内側にある）。"
            )
        if self.bottom_outer_diameter_mm > self.opening_inner_diameter_mm:
            raise ParameterError(
                f"bottom_outer_diameter_mm={self.bottom_outer_diameter_mm!r} は "
                f"opening_inner_diameter_mm={self.opening_inner_diameter_mm!r} 以下で"
                "なければならない（上へ広がるテーパー容器を前提とする）。"
            )


@dataclass(frozen=True, slots=True)
class ObjectSpec:
    """キャッチ対象物の諸元（要件 1.4, 7.1）。

    M1 の実験条件である空き缶（350ml 缶相当）を前提とする。値は許容誤差の
    導出（`tolerance`）で開口内径から差し引かれる。

    Attributes:
        diameter_mm: 対象物の代表直径（mm）。
        height_mm: 対象物の高さ（mm）。

    Raises:
        ParameterError: いずれかが正の有限値でない場合。
    """

    diameter_mm: float
    height_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_positive_finite(self.diameter_mm, "diameter_mm")
        _require_positive_finite(self.height_mm, "height_mm")


@dataclass(frozen=True, slots=True)
class PrintingConstraints:
    """造形機の制約（要件 2.1, 2.5, 8.7）。

    ⚠️ **寸法パラメータと同じ設定基盤の上に置く**（要件 2.1）。造形可能寸法を
    コードへ埋め込むと、造形機が変わったときに形状生成のコードを書き換える
    ことになる。

    Attributes:
        build_x_mm, build_y_mm, build_z_mm: 造形可能寸法（mm）。⚠️ 円は正方形の
            対角線を使えないため、判定は**軸並行の外接箱**で行う
            （design.md「Constraints」）。
        material: 材料名。`ALLOWED_MATERIALS` の一覧からのみ受け付ける（要件 2.5）。
        material_density_g_cm3: 材料の密度（g/cm^3）。質量の目安の算出に用いる
            （要件 8.7）。
        segment_margin_mm: 分割数の導出で造形可能寸法から差し引く余裕（mm）。
            0 を許すのは「余裕を取らない」が意味を持つ設定だからである。

    Raises:
        ParameterError: 寸法・密度が正の有限値でない場合、余裕が負または非有限の
            場合、または材料が `ALLOWED_MATERIALS` に無い場合。
    """

    build_x_mm: float
    build_y_mm: float
    build_z_mm: float
    material: str
    material_density_g_cm3: float
    segment_margin_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_positive_finite(self.build_x_mm, "build_x_mm")
        _require_positive_finite(self.build_y_mm, "build_y_mm")
        _require_positive_finite(self.build_z_mm, "build_z_mm")
        _require_positive_finite(self.material_density_g_cm3, "material_density_g_cm3")
        _require_nonneg_finite(self.segment_margin_mm, "segment_margin_mm")
        if self.material not in ALLOWED_MATERIALS:
            allowed = ", ".join(sorted(ALLOWED_MATERIALS))
            raise ParameterError(
                f"material={self.material!r} は許可されていない。"
                f"指定できるのは {allowed} のみである"
                "（大文字小文字・前後の空白も一致していなければならない）。"
            )


@dataclass(frozen=True, slots=True)
class JointPolicy:
    """部品間の継手方針（要件 2.6, 8.4）。

    ⚠️ **荷重を受ける締結要素と、位置決めのみを担う要素とを区別して保持する**
    （要件 2.6）。前者は貫通ボルトと金属インサート（`bolt_designation` /
    `through_hole_diameter_mm` / `insert_*`）、後者はダボ（`dowel_diameter_mm`）
    である。ダボへ荷重を負わせない設計判断を、型の上でも分けて表す。

    Attributes:
        bolt_designation: 締結ボルトの呼び（例: `"M3"`）。空文字は許さない。
        through_hole_diameter_mm: 貫通穴の径（mm）。
        insert_outer_diameter_mm: 金属インサートの外径（mm）。
        insert_length_mm: 金属インサートの長さ（mm）。
        dowel_diameter_mm: 位置決めダボの径（mm）。荷重は受けない。
        min_bearing_area_mm2: 締結部が確保すべき最小の支圧面積（mm^2）。

    Raises:
        ParameterError: 呼びが空文字の場合、または径・長さ・面積が正の有限値で
            ない場合。
    """

    bolt_designation: str
    through_hole_diameter_mm: float
    insert_outer_diameter_mm: float
    insert_length_mm: float
    dowel_diameter_mm: float
    min_bearing_area_mm2: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_nonempty_str(self.bolt_designation, "bolt_designation")
        _require_positive_finite(self.through_hole_diameter_mm, "through_hole_diameter_mm")
        _require_positive_finite(self.insert_outer_diameter_mm, "insert_outer_diameter_mm")
        _require_positive_finite(self.insert_length_mm, "insert_length_mm")
        _require_positive_finite(self.dowel_diameter_mm, "dowel_diameter_mm")
        _require_positive_finite(self.min_bearing_area_mm2, "min_bearing_area_mm2")


@dataclass(frozen=True, slots=True)
class RimParams:
    """受け口（ワイドリム）の寸法（要件 8.1, 8.5, 8.6）。

    Attributes:
        fit_clearance_mm: 取り付け部の隙間（mm）。ゴミ箱の個体差を吸収する量で
            あり、**寸法パラメータとして保持する**（要件 8.6）。取り付け部の
            内径は `top_outer_diameter_mm + 2 × fit_clearance_mm` で導出する。
        flange_width_mm: フランジの外向きの幅（mm）。
        flange_slope_deg: フランジの傾斜角（度）。0 以上 90 度未満。
        wall_thickness_mm: 壁の肉厚（mm）。
        height_mm: 取り付け部の高さ（mm）。⚠️ 受け口が本体に足す深さではない
            （それは `RetentionParams.added_depth_mm` であり 0 に固定される）。

    Raises:
        ParameterError: 長さが正の有限値でない場合、または傾斜角が値域を外れる場合。
    """

    fit_clearance_mm: float
    flange_width_mm: float
    flange_slope_deg: float
    wall_thickness_mm: float
    height_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_positive_finite(self.fit_clearance_mm, "fit_clearance_mm")
        _require_positive_finite(self.flange_width_mm, "flange_width_mm")
        _require_angle_deg(self.flange_slope_deg, "flange_slope_deg")
        _require_positive_finite(self.wall_thickness_mm, "wall_thickness_mm")
        _require_positive_finite(self.height_mm, "height_mm")


@dataclass(frozen=True, slots=True)
class RetentionParams:
    """保持（FR-12）についての決定（要件 9.4, 9.7）。

    ⚠️ 本型の後半2フィールドは**採寸値ではなく設計上の決定**であり、型の側で
    固定する（design.md「Params」Invariants /「受け口形状の決定」）。決定を
    設定ファイルの自由な入力にすると、「深さを足さない」「底へ加工を行わない」
    という判断が黙って覆る。

    Attributes:
        retrofit_fastener_count: 後付け部品用の締結座の数（要件 9.7）。既存の
            受け口を作り直さずに跳ね出し抑制部品を足せるようにするため、1 以上。
        liner_flat_min_diameter_mm: 底面に残すべき平面の最小径（mm）。後から
            緩衝材を貼れる平面を設計上の制約として保持する（要件 9.4）。
            ⚠️ 緩衝材の材質選定と調達は本 Spec の対象外である（要件 9.5）。
        added_depth_mm: 受け口が本体に足す深さ（mm）。**決定値は 0.0**。
        bottom_modification: 底への加工。**`"none"` 固定**。

    Raises:
        ParameterError: 締結座の数が 1 以上の整数でない場合、平面の最小径が正の
            有限値でない場合、`added_depth_mm != 0.0` の場合、または
            `bottom_modification != "none"` の場合。
    """

    retrofit_fastener_count: int
    liner_flat_min_diameter_mm: float
    added_depth_mm: float = 0.0
    bottom_modification: str = "none"

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        # bool は int の派生であるため明示的に除く。JSON の `true` が
        # 「締結座 1 箇所」として黙って通ることを防ぐ。
        if (
            isinstance(self.retrofit_fastener_count, bool)
            or not isinstance(self.retrofit_fastener_count, int)
            or self.retrofit_fastener_count < 1
        ):
            raise ParameterError(
                f"retrofit_fastener_count={self.retrofit_fastener_count!r} は 1 以上の整数で"
                "なければならない（後付け部品を取り付けられる締結座が要る）。"
            )
        _require_positive_finite(self.liner_flat_min_diameter_mm, "liner_flat_min_diameter_mm")
        if self.added_depth_mm != 0.0:
            raise ParameterError(
                f"added_depth_mm={self.added_depth_mm!r} は 0.0 でなければならない"
                "（受け口は本体に深さを足さない、という決定を型で表している）。"
            )
        if self.bottom_modification != "none":
            raise ParameterError(
                f"bottom_modification={self.bottom_modification!r} は 'none' で"
                "なければならない（底へ加工を行わない、という決定を型で表している）。"
            )


@dataclass(frozen=True, slots=True)
class MechanismParams:
    """寸法パラメータの集約ルート（要件 1.1, 1.2, 1.8, 9.4, 10.1）。

    各コンポーネントは自身の `__post_init__` で構築時検証済みであるため、本型は
    フィールド単位の追加検証を行わず、`provenance` の整合だけを見る
    （design.md「Params」Postconditions: 構築を通った `MechanismParams` は以降の
    層で再検証を要さない）。

    Attributes:
        trash_can: 選定したゴミ箱の採寸値。
        target_object: キャッチ対象物の諸元。
        printing: 造形機の制約。
        joint: 継手方針。
        rim: 受け口の寸法。
        retention: 保持についての決定。
        provenance: 各パラメータの出所の対応表（要件 1.2, 9.4）。キーは
            `PARAMETER_PATHS` のパス文字列でなければならない。⚠️ **未知のキーは
            拒否する**——出所が黙って無視されると、実測したつもりの値が仮値の
            まま残る（design.md「Params」Risks）。表に現れないパスは `ASSUMED`
            として扱う運用であり（design.md「Logical Data Model」）、
            「実測を名乗るには明示が要る」方向に倒している。構築時に `dict` へ
            複製するため、呼び出し側の辞書のその後の変更は反映されない。

    Raises:
        ParameterError: `provenance` が対応表でない場合、キーが
            `PARAMETER_PATHS` に無い場合、または値が `Provenance` でない場合。
    """

    trash_can: TrashCanMeasurements
    target_object: ObjectSpec
    printing: PrintingConstraints
    joint: JointPolicy
    rim: RimParams
    retention: RetentionParams
    provenance: Mapping[str, Provenance]

    def __post_init__(self) -> None:
        """`provenance` を検証し、エイリアスを切った複製で置き換える（要件 9.4）。

        `PARAMETER_PATHS` は本クラス定義の直後にモジュールレベルで構築される
        ため、この検証はどのインスタンス構築時にも安全に参照できる（モジュールの
        ロードが完了するまでインスタンスは作られない）。
        """
        if not isinstance(self.provenance, Mapping):
            raise ParameterError(
                f"provenance={self.provenance!r} はパスから出所への対応表で"
                "なければならない。"
            )
        for key, value in self.provenance.items():
            if key not in PARAMETER_PATHS:
                raise ParameterError(
                    f"provenance のキー {key!r} は PARAMETER_PATHS のパス文字列と"
                    "一致しない。既知のパス（例: 'trash_can.opening_inner_diameter_mm'）"
                    "のみを出所の対応表のキーとして指定できる。"
                )
            if not isinstance(value, Provenance):
                raise ParameterError(
                    f"provenance[{key!r}]={value!r} は Provenance でなければならない"
                    f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
                )
        # 検証を通った集約が以降の層で再検証を要さないためには、検証後に中身が
        # 差し替わらないことが要る。呼び出し側の辞書との**エイリアスを切る**
        # 複製を置くことでこれを満たす。
        # ⚠️ `MappingProxyType` で包んではならない。`dataclasses.asdict()` は
        # dict ではないマッピング型を再帰対象と認識せず `copy.deepcopy()` へ
        # 回すため `TypeError: cannot pickle 'mappingproxy' object` になり、
        # 集約の直列化（`config.dump_params` / `parameters_digest`、タスク 1.4）
        # が最も自然な経路で壊れる（同じ罠の記録が
        # `src/flying_object_tracking/bench/compare.py` にある）。上流の
        # `trajectory_sim.ScenarioParams.provenance` も素の `dict` を保持する。
        object.__setattr__(self, "provenance", dict(self.provenance))


@dataclass(frozen=True, slots=True)
class ParameterPath:
    """`PARAMETER_PATHS` の1エントリ（要件 1.8, 9.4, 10.1）。

    design.md「Params」は `PARAMETER_PATHS: Mapping[str, ParameterPath]` という型
    のみを宣言し、`ParameterPath` 自身の内部構造までは定めていない（設計ギャップ）。
    ここでは、下流が必要とする3つの機能——(a) `provenance` のキー検証、
    (b) 設定ファイルの読み書きにおける値の型検査（`config`、タスク 1.4）、
    (c) 公開項目への単位と出所の併記（要件 10.2, 10.4）——を満たす最小限の形と
    して、以下の5フィールドを持つ frozen dataclass を選んだ。

    Attributes:
        path: パス文字列そのもの（例: `"trash_can.height_mm"`）。
            `PARAMETER_PATHS` の対応するキーと常に一致する。
        unit: フィールド名の接尾辞から導いた単位文字列（`_SUFFIX_UNITS` 参照）。
            単位を持たない項目（材料名・個数など）は `""`。
        component: `MechanismParams` 直下のコンポーネント名（例: `"trash_can"`）。
        field_name: `component` の中のリーフフィールド名。
        value_type: リーフフィールドの型（`float` / `int` / `str`）。設定ファイル
            の値がこの型であることを `config` が検査する。
    """

    path: str
    unit: str
    component: str
    field_name: str
    value_type: type


# フィールド名の接尾辞から単位を導くための表（要件 1.8: 手書きの表を二重管理
# しない）。この表は単位の**導出規則**であってパス表そのものではなく、単位に
# ついての唯一の情報源である。
#
# より特殊的な（長い）接尾辞を先に判定する。特に `_g_cm3` は `_g` の前に、
# `_mm2` は `_mm` の前に置かなければ誤って一致する。
_SUFFIX_UNITS: tuple[tuple[str, str], ...] = (
    ("_g_cm3", "g/cm^3"),
    ("_mm2", "mm^2"),
    ("_mm", "mm"),
    ("_deg", "deg"),
    ("_g", "g"),
)


def _derive_unit(field_name: str) -> str:
    """フィールド名の接尾辞から単位文字列を導く。

    どの接尾辞にも該当しないフィールド（材料名 `material`、ボルトの呼び
    `bolt_designation`、個数 `retrofit_fastener_count` など）は `""`（単位なし）を
    返す。この既定フォールバックも本関数に一本化し、呼び出し側で単位判定を
    重複させない。
    """
    for suffix, unit in _SUFFIX_UNITS:
        if field_name.endswith(suffix):
            return unit
    return ""


#: `PARAMETER_PATHS` から除外する `MechanismParams` 直下のフィールド。
#: `provenance` は寸法ではなく**パスから出所への対応表そのもの**であり、単一の
#: リーフ値ではない。ここへ含めると「出所の出所」を要求することになる。
_EXCLUDED_ROOT_FIELDS: frozenset[str] = frozenset({"provenance"})


def _build_parameter_paths() -> Mapping[str, ParameterPath]:
    """`MechanismParams` のデータクラス木を走査し `PARAMETER_PATHS` を構築する。

    `dataclasses.fields()` で直下のコンポーネントを列挙し、それぞれのリーフ
    フィールドを `"<component>.<field>"` の形でパスにする。手書きの表は一切
    持たず、この走査結果だけが唯一の情報源である（要件 1.8）——手書きであれば、
    フィールドを1つ増やしたときに表から黙って漏れる。

    `MechanismParams` は `from __future__ import annotations` の影響で
    `Field.type` が文字列注釈のままになるため、`typing.get_type_hints` で実体の
    型へ解決してからデータクラス判定・値の型の記録を行う。本関数はクラスの構造を
    調べるだけで、いかなるインスタンスも構築しない。
    """
    paths: dict[str, ParameterPath] = {}
    root_hints = get_type_hints(MechanismParams)
    for root_field in fields(MechanismParams):
        if root_field.name in _EXCLUDED_ROOT_FIELDS:
            continue
        component_type = root_hints[root_field.name]
        if not is_dataclass(component_type):  # pragma: no cover - 構造上の防御
            raise TypeError(
                f"MechanismParams.{root_field.name} はデータクラスでなければならない。"
            )
        leaf_hints = get_type_hints(component_type)
        for leaf_field in fields(component_type):
            path = f"{root_field.name}.{leaf_field.name}"
            paths[path] = ParameterPath(
                path=path,
                unit=_derive_unit(leaf_field.name),
                component=root_field.name,
                field_name=leaf_field.name,
                value_type=leaf_hints[leaf_field.name],
            )
    return MappingProxyType(paths)


PARAMETER_PATHS: Mapping[str, ParameterPath] = _build_parameter_paths()
"""寸法パラメータのパス表（要件 1.8 / design.md「Params」Responsibilities）。

`MechanismParams` のデータクラス木からモジュールロード時に一度だけ生成される
不変マッピング。`provenance` のキーの正当性はこの表で決まり（要件 9.4）、
設定ファイル（`configs/catch_mechanism/dimensions.json`、タスク 1.4）の項目名と
値の型もこの表と突き合わせて検査される。
"""
