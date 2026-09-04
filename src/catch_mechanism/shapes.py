"""受け口の幾何導出（design.md「Shapes」/ 要件 8.1, 8.2, 8.5, 8.6）。

寸法パラメータだけから受け口（ワイドリム）の**数値としての幾何**を導く。
本モジュールが返す `RimGeometry` は、取り付け部の内径・通過できる最小径・外径・
分割数の4つであり、⚠️ **形状（ソリッド）を一切構築しない**。design.md「Shapes」
Implementation Notes の Validation「`rim_geometry` は形状構築の前に評価でき、
不変条件の検査を軽量に行える」がこの分離の理由である——開口を狭めていないか、
造形可能寸法に収まる分割数が存在するか、という**成立条件の検査は形状より前に
決まる**。形状を作らなければ答えられない検査にしてしまうと、CAD の入っていない
環境では受け口が成立するかどうかすら分からなくなる（要件 5.2, 5.3）。

そのため**本モジュールは形状ライブラリ（build123d）をモジュール直下で import
しない**。design.md「Allowed Dependencies」は `shapes` / `export` に限って import
を許すが、⚠️ **許されていることと「モジュール読み込み時に必要にしてよい」ことは
別である**。`rim_geometry` / `segment_envelope` は純粋な算術であり、`cad` extra
非導入の環境でも全不変条件を検査できる。モジュール直下へ import を置くと
`rim_geometry` を呼ぶだけで CAD が要ることになり、上記の分離も要件 5.7 も壊れる。
build123d の import は**ソリッドを実際に構築する `build_segments` の内側**にある。
`test_shapes_does_not_import_the_shape_library_at_module_level` がこれを固定する。

⚠️ **指標の抽出（`measure_part`）もこの規律の内側にある。** `measure_part` は
`solid` を `object` として受け取り、`volume` / `bounding_box()` / `solids()` という
**属性の形**だけに依存する。build123d の名前は署名にも実装にも現れないため、指標の
抽出は形状ライブラリ非導入の環境でも（同じ属性を持つ任意のオブジェクトに対して）
評価できる。⚠️ 型で受けるために `from build123d import Part` を足すと、この規律が
`measure_part` の側から崩れる。

## 形状と指標の分離（タスク 3.3 / 要件 3.7, 4.1）

`BuiltPart` は「構築物 `solid`」と「素の数値だけの指標 `metrics`」を並べて持つ。
⚠️ **中核層へ渡るのは `metrics` だけである**（`metrics.PartMetrics` は形状という
概念を型として知らない）。指標は**構築したソリッドから抽出した実測値**であり、
解析式ではない——寸法から体積を再計算する実装は、形状の側に入った誤りをそのまま
「一致」と報告してしまい、要件 4「形状指標による二重管理の検出」が成立しない。

⚠️ **抽出で丸めない。** OCCT の版差は**記録側の許容差**
（`metrics.GeometryBaseline`）で吸収する設計であり（design.md「Shapes」Risks）、
抽出側にも丸めを置くと許容差の機構が二重になる。詳細は `measure_part` の docstring。

## 造形向き（⚠️ 全形状に共通する前提）

**リム面を造形面へ寝かせる向き**（本モジュールの Z 軸＝造形機の Z 軸）を前提と
する。このとき層法線は +Z であり、**接合面（セグメント端面）の法線は XY 平面内に
あって層法線と一致しない**——ボルトの締結力を層間の接着ではなく層内のせん断と
支圧で受ける配置である。⚠️ 端面を造形面へ伏せる置き方をすると、接合部の強度が
層間剥離に律速される。この前提は `build_segments` の docstring と
`test_joint_faces_are_not_normal_to_the_layer_direction` が併せて固定している。

## 通過できる最小径（`clear_opening_diameter_mm`）の意味

⚠️ **取り付け部の内径ではない。** 取り付け部の内径は
`top_outer_diameter_mm + 2 × fit_clearance_mm` であり、ゴミ箱の縁の**外側**へ
被せる穴であるから、ゴミ箱自身の開口内径より**広い**。一方、落ちてくる物は
「受け口の穴」と「ゴミ箱自身の開口」の**両方**を通らなければならない。したがって
実際に通過できる最小径は2つのうち**狭い方**、すなわち

    clear_opening = min(取り付け部の内径, 開口内径)

である。取り付け部の内径をそのまま返す実装は、出荷相当の値（内径 227.0 /
開口 220.0）でも design.md の Postconditions `clear_opening >= opening_inner` を
通ってしまうが、φ225 の物が落ちると主張することになる。逆に開口内径を無条件で
返す実装は Postconditions を**恒真**にし、要件 8.2「受け口がゴミ箱本体の開口
内径を狭めないことを検査する」の検査を消してしまう。狭い方を採る形だけが、
「狭めていない」を主張ではなく**観測**にする。

## 開口を狭める寸法の組み合わせは拒否する（要件 8.2）

`params` が強制する径の大小関係は `bottom_flat <= bottom_outer <= opening_inner`
だけであり、**上端外径と開口内径の関係は縛られていない**。したがって
「上端外径が開口内径より小さいゴミ箱」を表す `MechanismParams` は構築できて
しまう（現実のテーパー容器では縁の肉厚の分だけ上端外径が上回るはずだが、採寸の
取り違えや将来の別形状で起こりうる）。その場合、取り付け部の内径は開口内径を
下回り、受け口は開口の内側へせり出す——design.md「受け口形状の決定」決定1
「フランジは外向きにのみ張り出し、開口内径を一切狭めない」に真っ向から反する
形であり、Postconditions を満たす値が存在しない。

⚠️ **この状況で値を返してはならない。** `GeometryError`（「形状が成立しない」、
`errors.py`）で拒否する。`ParameterError` ではないのは、個々のパラメータはどれも
それ自体として妥当であり、成立していないのは**受け口という形状**だからである
（`cli` の終了コードはいずれも 2 で観測差は無い）。

## 造形制約との関係

外径は「取り付け部の内径 ＋ フランジ幅×2」である。フランジは外向きにのみ
張り出すため（決定1）、片側の幅が直径には2倍で効く。⚠️ **壁の肉厚
（`wall_thickness_mm`）は算入しない。** フランジの内周は取り付け部の内径そのもの
であり（design.md「Shapes」Responsibilities の「フランジの内周は開口内径以上」は
この内径に対する条件である）、フランジ幅はそこから外向きに測る。この読みは
tasks.md「Implementation Notes」タスク 2.1(a) が実部品として記録する
リム外径 287.0mm = 225 + 2×1.0 + 2×30 と一致する。

分割数は**この外径を造形制約の検査へ渡して**導く。⚠️ **同じ幾何を再実装しない**
（tasks.md「Implementation Notes」タスク 2.1(b)）。`constraints.required_segment_count`
は弦長だけでなく半径方向の広がりも見ており、式を書き写した実装は「大きな分割数を
返せばいつか収まる」という誤りへ静かに分岐する。収まる分割数が存在しない場合の
`GeometryError` も**握り潰さず伝播させる**——分割で解決しない外径を、無検査のまま
形状構築（タスク 3.2）へ渡さないためである。

⚠️ **`rim_geometry` は `check_envelope` を呼ばない。** design.md「Shapes」の
Preconditions「`check_material` / `check_envelope` を通過している」は
**呼び出し側の責務**であり、`check_envelope` は違反を例外ではなく**値**として
返す設計である（design.md「Error Strategy」/ `constraints.py`）。なお
`required_segment_count` が返した分割数の扇形は必ず `check_envelope` を通る
（余裕 `segment_margin_mm` の分だけ厳しい上限で導出しているため。
tasks.md タスク 2.1(c)）。

⚠️ **ただし Z 軸だけは事情が異なり、`build_segments` が自分で検査する。**
`required_segment_count` は分割数の導出にあたり高さへ `build_z_mm` を置くため、
**実高さの違反を構造的に検出できない**（tasks.md「Implementation Notes」
タスク 3.1(a)）。実高さ（取り付け部の高さ ＋ 肉厚 ＋ フランジの立ち上がり）を
知るのは形状の側であり、それを `segment_envelope` として組み立てて
`check_envelope` へ渡せるのは本モジュールが初めてである。ここで通さないまま
ソリッドを返すと、造形できない部品が書き出し（タスク 3.4）まで無検査で流れる
——要件 2.7「検査を形状生成の一部として実行し、検査を通らない形状の生成物を
出力しない」の形状生成側の半分にあたる。書き出し直前の再検査は 3.4 の担当である。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from catch_mechanism.constraints import (
    Envelope,
    check_envelope,
    check_joint,
    required_segment_count,
    sector_envelope,
)
from catch_mechanism.errors import GeometryError
from catch_mechanism.metrics import PartMetrics
from catch_mechanism.params import MechanismParams, RimParams

__all__ = [
    "PART_NAMES",
    "BuiltPart",
    "RimGeometry",
    "RimSegment",
    "build_parts",
    "build_segments",
    "measure_part",
    "rim_geometry",
    "segment_envelope",
    "segment_height_mm",
    "segment_part_names",
]


@dataclass(frozen=True, slots=True)
class RimGeometry:
    """受け口の幾何（design.md「Shapes」Service Interface）。

    ⚠️ **形状オブジェクトを持たない。** 本型は数値だけで構成され、形状ライブラリの
    無い環境でも生成・比較できる。

    本型は `rim_geometry` の**出力**であり、本モジュール自身が整合の取れた値で
    構築する。したがって構築時検証を持たない（`constraints.BuildViolation` と
    同じ扱い）——出力側で再検証しても直せる呼び出し側はおらず、導出の失敗が
    検証自身の例外に化けるだけである。成立条件の検査は `rim_geometry` が
    構築の**前**に行う。

    Attributes:
        segment_count: 分割数。`constraints.required_segment_count` が外径と
            造形制約から導いた値であり、⚠️ パラメータではない。
        inner_diameter_mm: 取り付け部の内径（mm）。
            `top_outer_diameter_mm + 2 × fit_clearance_mm`（要件 8.5, 8.6）。
        clear_opening_diameter_mm: 実際に通過できる最小径（mm）。受け口の穴と
            ゴミ箱自身の開口の**狭い方**であり、常に開口内径以上である
            （design.md Postconditions / 要件 8.2。本モジュール docstring 参照）。
        outer_diameter_mm: 受け口全体の外径（mm）。
            `inner_diameter_mm + 2 × flange_width_mm`。
    """

    segment_count: int
    inner_diameter_mm: float
    clear_opening_diameter_mm: float
    outer_diameter_mm: float


def rim_geometry(params: MechanismParams) -> RimGeometry:
    """寸法パラメータから受け口の幾何を導く（要件 8.1, 8.2, 8.5, 8.6）。

    採寸値が更新されれば取り付け部の内径・外径・分割数がそのまま追随する
    （要件 8.5）。⚠️ **形状を構築しない。** 本関数は算術のみで完結し、形状
    ライブラリの無い環境でも評価できる（design.md「Shapes」Implementation Notes）。

    `params` は構築時に検証済みであるため、本関数はフィールド単位の再検証を
    行わない（design.md「Params」Postconditions）。本関数が見るのは、個々の
    パラメータからは決まらない**受け口としての成立条件**だけである。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        取り付け部の内径・通過できる最小径・外径・分割数。

    Raises:
        GeometryError: 取り付け部の内径が開口内径を下回り、受け口が開口を狭める
            場合（要件 8.2 / design.md「受け口形状の決定」決定1）。または、
            `MAX_SEGMENT_COUNT` までのどの分割数でも外径が造形可能寸法に収まらない
            場合（`constraints.required_segment_count` からの伝播。⚠️ 握り潰さない）。
    """
    trash_can = params.trash_can
    rim = params.rim

    # 取り付け部の内径。隙間は**半径方向**の量であるため直径には2倍で効く
    # （要件 8.6: 個体差を吸収する量を寸法パラメータとして保持する）。
    inner_diameter_mm = trash_can.top_outer_diameter_mm + 2.0 * rim.fit_clearance_mm

    # 通過できる最小径は「受け口の穴」と「ゴミ箱自身の開口」の狭い方である
    # （本モジュール docstring）。フランジは外向きにのみ張り出すため、これ以外の
    # 絞りは受け口に存在しない。
    clear_opening_diameter_mm = min(
        inner_diameter_mm, trash_can.opening_inner_diameter_mm
    )
    if clear_opening_diameter_mm < trash_can.opening_inner_diameter_mm:
        raise GeometryError(
            f"取り付け部の内径 {inner_diameter_mm}mm"
            f"（top_outer_diameter_mm={trash_can.top_outer_diameter_mm!r} + 2 × "
            f"fit_clearance_mm={rim.fit_clearance_mm!r}）が "
            f"opening_inner_diameter_mm={trash_can.opening_inner_diameter_mm!r} を"
            f"下回るため、受け口が開口を "
            f"{trash_can.opening_inner_diameter_mm - inner_diameter_mm}mm 狭める。"
            "⚠️ 受け口は開口内径を一切狭めない（フランジは外向きにのみ張り出す）。"
            "上端外径の採寸値を確かめること。"
        )

    # 外径は取り付け部にフランジを外向きに足したもの。⚠️ 壁の肉厚は算入しない
    # （本モジュール docstring）。
    outer_diameter_mm = inner_diameter_mm + 2.0 * rim.flange_width_mm

    # 分割数は導出値であり、造形制約の検査へ外径を渡して得る。⚠️ 同じ幾何を
    # 再実装しない（tasks.md タスク 2.1(b)）。収まる分割数が無ければ
    # `GeometryError` がそのまま伝播する。
    segment_count = required_segment_count(outer_diameter_mm, params.printing)

    return RimGeometry(
        segment_count=segment_count,
        inner_diameter_mm=inner_diameter_mm,
        clear_opening_diameter_mm=clear_opening_diameter_mm,
        outer_diameter_mm=outer_diameter_mm,
    )


# ---------------------------------------------------------------------------
# ワイドリムのセグメント形状（タスク 3.2 / 要件 2.6, 3.1, 8.3, 8.4, 9.7）
# ---------------------------------------------------------------------------

PART_NAMES: tuple[str, ...] = ("rim_segment",)
"""受け口を構成する部品の**種類**（design.md「Shapes」Service Interface）。

⚠️ **1件の要素は「部品の種類」であって、造形する部品の点数ではない。** 受け口は
扇形セグメントという1種類の部品だけからなるが、実際に造形するのは
`rim_geometry(params).segment_count` 点である。個々の部品名は
`segment_part_names` が本定数から導く（`rim_segment_1` …）。

⚠️ **セグメントは互いに同一形状とは限らない。** 後付け部品用の締結座は
**リング全体で** `RetentionParams.retrofit_fastener_count` 箇所であり
（design.md「受け口形状の決定」決定4「後付け部品用の締結座を6箇所」）、その数が
分割数で割り切れないとき（出荷値の 6 箇所 / 5 分割など）、座の数はセグメント間で
1 箇所ずれる。割り切れるときは全セグメントが同一形状になる。
指標の記録（タスク 4.2）が部品ごとの行を必要とするのはこのためである。
"""


_MIN_FEATURE_EDGE_MM: float = 2.0
"""穴の縁から部品の縁・他の穴までに残す最小の肉（mm）。

FDM で成立する最小の壁として置く。⚠️ この値を下回る配置は「作れるが割れる」形で
あり、`GeometryError` で拒否する——黙って縁の薄い穴を開けると、造形してから
割れて初めて気付く。
"""

_JOINT_BOSS_INSERT_DIAMETER_MULTIPLE: float = 2.0
"""継手座の半径方向の張り出しを決める係数（金属インサート外径の倍数）。

継手座の半径方向の幅は `wall_thickness + 2 × insert_outer_diameter` である。
⚠️ **壁の肉厚（4mm 程度）にボルト穴（φ3.4）は入らない。** 座を設けずに壁へ直接
穴を開けると残り肉が 0.3mm になる。継手は**フランジの下の空間へ外向きに**張り
出した座が受ける（内向きへ張り出すと決定1 に反して開口を狭める）。
"""

_JOINT_BOSS_ARC_LENGTH_MULTIPLE: float = 3.0
"""継手座の周方向の厚み（金属インサート長さの倍数）。

インサート（長さ 5.7mm）とボルト先端の逃げを収め、なお貫通ボルト穴が座を抜けて
フランジ下の空間へ出られる厚みとして置く。
"""

_BOLT_AXIS_HEIGHT_FRACTION: float = 0.65
"""ボルト軸の高さ（取り付け部の高さに対する比）。"""

_DOWEL_AXIS_HEIGHT_FRACTION: float = 0.28
"""位置決めダボの軸の高さ（取り付け部の高さに対する比）。

⚠️ **ボルト軸と別の高さに置く。** 同軸・同高さに並べるとダボが荷重経路へ入り、
「荷重を受けるのは貫通ボルトと金属インサートだけ」という `JointPolicy` の区別
（要件 2.6, 8.4）が形状の側で破れる。
"""

_DOWEL_FIT_CLEARANCE_MM: float = 0.2
"""ダボ穴の直径に与えるすきま（mm）。

⚠️ **すきま嵌めであることがダボに荷重を負わせない条件そのものである。** 締まり
嵌めにすると、せん断がダボへ回る。
"""

_DOWEL_DEPTH_DIAMETER_MULTIPLE: float = 1.5
"""ダボ穴の深さ（ダボ呼び径の倍数）。"""

_BOLT_TIP_CLEARANCE_LENGTH_MULTIPLE: float = 3.0
"""インサート座の奥に続くボルト先端の逃げの深さ（インサート長さの倍数）。"""

_RETROFIT_PAD_DIAMETER_INSERT_MULTIPLE: float = 2.0
"""後付け用締結座のパッド外径（金属インサート外径の倍数）。"""

_RETROFIT_PAD_DROP_MARGIN_MM: float = 2.0
"""後付け用締結座のパッドが、インサート長さより余分に垂れ下がる量（mm）。"""


def _flange_rise_mm(rim: RimParams) -> float:
    """フランジが外周までに立ち上がる高さ（mm）。

    ⚠️ **外周が高く内周が低い**（design.md「受け口形状の決定」決定1）。立ち上が
    りは常に非負であり、内周側が高くなることはない。
    """
    return rim.flange_width_mm * math.tan(math.radians(rim.flange_slope_deg))


def segment_height_mm(params: MechanismParams) -> float:
    """セグメント1点の**実高さ**（mm）。

    ⚠️ `RimParams.height_mm` は**取り付け部の高さ**であって部品の高さではない
    （`params.RimParams` の docstring）。部品の高さはそこへ、フランジの肉厚と
    外向きの立ち上がりを足したものである。

    ⚠️ **この値が tasks.md「Implementation Notes」タスク 3.1(a) の申し送りの
    決着である。** `constraints.required_segment_count` は分割数の導出にあたり
    高さへ `build_z_mm` を置くため、Z 軸の違反を構造的に検出できない。実高さを
    `check_envelope` へ渡せるのは本関数が初めてである。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        取り付け部の高さ ＋ 壁の肉厚 ＋ フランジの立ち上がり（mm）。
    """
    rim = params.rim
    return rim.height_mm + rim.wall_thickness_mm + _flange_rise_mm(rim)


def segment_envelope(params: MechanismParams) -> Envelope:
    """セグメント1点の軸並行外接箱を**実高さで**返す（要件 2.3, 8.3）。

    ⚠️ **扇形の外接箱を再実装しない。** `constraints.sector_envelope` へ委譲する
    （tasks.md「Implementation Notes」タスク 2.1(b)「形状層（タスク 3.2）は同じ
    幾何を再実装せず、この関数を使うこと」）。半径方向は「中心を含む扇形」の
    最悪値 `D/2` になるため、実形状（円環断片）の外接箱より大きい——上界として
    使えるが、造形可能寸法ぎりぎりの判定に使うと保守側へ倒れすぎる点に注意する。

    形状ライブラリを必要としないため、`cad` extra 非導入の環境でも評価できる
    （要件 5.7）。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        セグメント1点の軸並行外接箱。

    Raises:
        GeometryError: `rim_geometry` からの伝播（開口を狭める寸法、または収まる
            分割数が存在しない場合）。
    """
    geometry = rim_geometry(params)
    return sector_envelope(
        geometry.outer_diameter_mm,
        geometry.segment_count,
        segment_height_mm(params),
    )


def segment_part_names(params: MechanismParams) -> tuple[str, ...]:
    """造形するセグメントの部品名を、導出された分割数だけ返す。

    ⚠️ 名前は `PART_NAMES[0]` から導く。部品名の正は1箇所であり、ここで別の
    文字列を作らない。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        `("rim_segment_1", …)`。長さは `rim_geometry(params).segment_count`。

    Raises:
        GeometryError: `rim_geometry` からの伝播。
    """
    count = rim_geometry(params).segment_count
    return tuple(f"{PART_NAMES[0]}_{index + 1}" for index in range(count))


@dataclass(frozen=True, slots=True)
class RimSegment:
    """構築済みのセグメント1点（design.md「Shapes」Service Interface の一部）。

    ⚠️ **`BuiltPart` ではない。** 本型は「構築したソリッドと、**構築時にしか
    分からない数**」——後付け座の数と支圧面積——を運ぶ。design.md の `BuiltPart`
    が伴う `PartMetrics`（体積・境界箱・立体数）は、逆に**構築したソリッドを
    測れば得られる**量であり、`measure_part` が本型の外側で抽出する。
    2つの型が別なのは工程が別だからであり、`build_parts` が本型を包んで
    `BuiltPart` を返す。継手の検査に属する数が要る呼び出し側は本型を、
    書き出し（タスク 3.4）と記録（タスク 4.2）は `BuiltPart` を使う。

    ⚠️ **`solid` を中核層へ渡さない。** 型を `object` にしてあるのは、形状
    ライブラリの型が中核層の署名へ漏れないようにするためである
    （design.md「Shapes」Service Interface の `solid: object`）。

    Attributes:
        name: 部品名。`segment_part_names` の同じ位置の要素と一致する。
        index: 0 起点のセグメント番号。リング上での位置（`index × 分割角`）を
            指し、生成順ではない。
        solid: build123d の `Part`。
        retrofit_seat_count: このセグメントが持つ後付け用締結座の数。
            ⚠️ 全セグメントの合計が `RetentionParams.retrofit_fastener_count` に
            一致する（リング全体で指定数。design.md 決定4）。
        joint_bearing_area_mm2: 端面1面あたりの支圧面積（mm^2）。
            `constraints.check_joint` を通過している（要件 2.6, 8.4）。
            ⚠️ **継手座の範囲に局在した量である。** フランジ・壁の断面のうち座の
            範囲を外れる部分は算入しない——`check_joint` が名指しする破壊モードは
            ボルト座面のめり込みであり、ボルトから離れた材料は座面圧を下げない。
            離れた断面を足すと `flange_width_mm` を広げるだけで支圧面積が増える
            式になる。したがって本値は**フランジ幅に非感応**である。
            ⚠️ 構築されたソリッドを半径 `inner_diameter/2 + 継手座の張り出し` で
            切り出した領域の端面の実面積と一致する（穴の大きい方の端面、すなわち
            接触面積の狭い方を採る）。
            分割数が 1 で合わせ目が存在しない場合は `None`——0.0 ではない。
            「継手が無い」と「面積が 0 である」は別の事実であり、後者は継手の
            失敗を意味する。
    """

    name: str
    index: int
    solid: object
    retrofit_seat_count: int
    joint_bearing_area_mm2: float | None


@dataclass(frozen=True, slots=True)
class _JointLayout:
    """端面の継手座の配置（本モジュール内部）。"""

    boss_radial_mm: float
    boss_arc_mm: float
    boss_arc_deg: float
    feature_radius_mm: float
    bolt_axis_height_mm: float
    dowel_axis_height_mm: float
    dowel_hole_diameter_mm: float
    bearing_area_mm2: float


@dataclass(frozen=True, slots=True)
class _RetrofitLayout:
    """後付け用締結座の配置（本モジュール内部）。"""

    center_radius_mm: float
    pad_diameter_mm: float
    pad_bottom_z_mm: float
    pad_height_mm: float
    bore_diameter_mm: float
    bore_depth_mm: float
    local_angles_deg: tuple[tuple[float, ...], ...]


def _joint_layout(params: MechanismParams, geometry: RimGeometry) -> _JointLayout:
    """端面の継手座を配置し、支圧面積を求める（要件 2.6, 8.4 / design.md）。

    継手座は**フランジの下の空き空間へ外向きに**張り出す。⚠️ 内向きへ張り出すと
    決定1「開口内径を一切狭めない」に反する。壁の肉厚（出荷値 4mm）にボルト穴
    （φ3.4）を通すと残り肉が 0.3mm になるため、座を設けずに壁へ直接穴を開ける
    構成は採らない。

    Raises:
        GeometryError: 座・穴・縁の肉が成立しない寸法の組み合わせの場合。
    """
    rim = params.rim
    joint = params.joint
    inner_radius_mm = geometry.inner_diameter_mm / 2.0
    tan_slope = math.tan(math.radians(rim.flange_slope_deg))

    boss_radial_mm = (
        rim.wall_thickness_mm
        + _JOINT_BOSS_INSERT_DIAMETER_MULTIPLE * joint.insert_outer_diameter_mm
    )
    if boss_radial_mm > rim.flange_width_mm:
        raise GeometryError(
            f"継手座の張り出し {boss_radial_mm}mm"
            f"（wall_thickness_mm={rim.wall_thickness_mm!r} + "
            f"{_JOINT_BOSS_INSERT_DIAMETER_MULTIPLE} × "
            f"insert_outer_diameter_mm={joint.insert_outer_diameter_mm!r}）が "
            f"flange_width_mm={rim.flange_width_mm!r} を超える。"
            "⚠️ 継手座はフランジの下へ外向きに張り出す（内向きへ張り出すと開口を"
            "狭める）ため、フランジ幅を広げるか金属インサートを細いものにすること。"
        )

    # 穴の軸はフランジの下、壁の外側に置く。⚠️ 壁の内側（半径 `inner_radius` 未満）
    # へ寄せると開口を狭め、壁の中へ入れると穴が周方向へ抜けずに壁を延々と貫く。
    feature_radius_mm = (
        inner_radius_mm + rim.wall_thickness_mm + joint.insert_outer_diameter_mm
    )
    bolt_axis_height_mm = rim.height_mm * _BOLT_AXIS_HEIGHT_FRACTION
    dowel_axis_height_mm = rim.height_mm * _DOWEL_AXIS_HEIGHT_FRACTION
    dowel_hole_diameter_mm = joint.dowel_diameter_mm + _DOWEL_FIT_CLEARANCE_MM

    # 荷重を受ける軸上の穴は、始端面が貫通ボルト穴（φ3.4）、終端面が金属インサート
    # 座（φ4.6）である。⚠️ **太い方**で縁の肉を見る（細い方で見ると終端面の縁が
    # 足りない配置を通してしまう）。
    axis_half_mm = joint.insert_outer_diameter_mm / 2.0
    dowel_half_mm = dowel_hole_diameter_mm / 2.0
    checks = (
        (
            "ダボ穴の下端",
            dowel_axis_height_mm - dowel_half_mm,
            _MIN_FEATURE_EDGE_MM,
        ),
        (
            "ボルト軸とダボ軸の間の肉",
            (bolt_axis_height_mm - axis_half_mm)
            - (dowel_axis_height_mm + dowel_half_mm),
            _MIN_FEATURE_EDGE_MM,
        ),
        (
            "ボルト穴の上端から取り付け部の上端までの肉",
            rim.height_mm - (bolt_axis_height_mm + axis_half_mm),
            _MIN_FEATURE_EDGE_MM,
        ),
    )
    for label, available_mm, required_mm in checks:
        if available_mm < required_mm:
            raise GeometryError(
                f"{label}が {available_mm}mm しかなく、下限 {required_mm}mm を"
                f"下回る（height_mm={rim.height_mm!r}、"
                f"insert_outer_diameter_mm={joint.insert_outer_diameter_mm!r}、"
                f"dowel_diameter_mm={joint.dowel_diameter_mm!r}）。"
                "⚠️ 取り付け部の高さが継手を収めるには足りない。高さを増すか、"
                "締結要素を細くすること（穴を重ねて逃げてはならない）。"
            )

    boss_arc_mm = _JOINT_BOSS_ARC_LENGTH_MULTIPLE * joint.insert_length_mm
    boss_arc_deg = math.degrees(boss_arc_mm / feature_radius_mm)
    sector_deg = 360.0 / geometry.segment_count
    if 2.0 * boss_arc_deg > sector_deg:
        raise GeometryError(
            f"両端の継手座（各 {boss_arc_deg}°）が分割角 {sector_deg}° を"
            "占め尽くす。⚠️ 分割数を減らすか、金属インサートを短いものにすること。"
        )

    # 支圧面積は**継手座の範囲に局在した端面の接触面積**である。
    #
    # ⚠️ **フランジ・壁の断面のうち、継手座の範囲を外れる部分は算入しない。**
    # `check_joint`（`constraints.py`）が名指しする破壊モードは「ボルトの締め付け力
    # が樹脂へ集中して座面がめり込み、締結が緩む」——支配量はボルト**座面圧**で
    # ある。ボルト軸（半径 `feature_radius_mm`、高さ `bolt_axis_height_mm`）から
    # 十数 mm 離れたフランジ外周へ材料を足しても座面圧は下がらない。離れた断面を
    # 足すと `flange_width_mm` を広げるだけで支圧面積が増える式になり、
    # 「当たり面が足りない」という判定を寸法で買えることになる。
    # `test_joint_bearing_area_is_insensitive_to_the_flange_width` がこの性質を
    # 値ではなく**非感応性**として固定する。
    #
    # ⚠️ **解析値ではあるが実形状の代用ではない。** 下の式は、構築されるソリッドを
    # 半径 `inner_radius + boss_radial` で切り出した領域の端面と厳密に一致し、
    # `test_joint_bearing_area_matches_the_measured_boss_face_of_the_built_solid`
    # が両者を突き合わせる——式を定数倍すれば落ちる。実測ではなく解析で持つのは、
    # `check_joint`（要件 2.6, 8.4）を**ソリッドを作る前**に通す必要があるため
    # である（要件 2.7「検査を形状生成の一部として実行し、検査を通らない形状の
    # 生成物を出力しない」）。
    #
    # 座の範囲の端面は「継手座の台形（フランジ下面まで）」と「その直上のフランジの
    # 帯（`boss_radial × wall_thickness`）」の和である。⚠️ 取り付け部の壁の断面は
    # 台形に**含まれている**（両者は半径 `inner_radius`〜`+wall_thickness` で
    # 重なる）ため、別途足さない。
    boss_face_area_mm2 = (
        boss_radial_mm * rim.height_mm + boss_radial_mm**2 * tan_slope / 2.0
    )
    contact_area_mm2 = (
        boss_face_area_mm2 + boss_radial_mm * rim.wall_thickness_mm
    )
    # 座に開く穴は、始端面が貫通ボルト穴、終端面が金属インサート座であり、
    # ダボ穴は両面に開く。⚠️ **穴の大きい方**（＝接触面積の狭い方の端面）を採る。
    # ⚠️ ダボ穴の断面は引く（`check_joint` の「ダボの面積を足してはならない」より
    # 一段強い扱い。穴には実際に材料が無い）。
    load_bore_diameter_mm = max(
        joint.through_hole_diameter_mm, joint.insert_outer_diameter_mm
    )
    bearing_area_mm2 = (
        contact_area_mm2
        - math.pi * (load_bore_diameter_mm / 2.0) ** 2
        - math.pi * dowel_half_mm**2
    )
    if bearing_area_mm2 <= 0.0:
        raise GeometryError(
            f"継手座の端面の接触面積 {contact_area_mm2}mm^2 が穴で埋まり、"
            f"支圧面積が {bearing_area_mm2}mm^2 になる。"
            f"⚠️ 締結要素（insert_outer_diameter_mm="
            f"{joint.insert_outer_diameter_mm!r}、"
            f"dowel_diameter_mm={joint.dowel_diameter_mm!r}）が端面より大きい。"
            "座を広げるか、締結要素を細くすること。"
        )

    return _JointLayout(
        boss_radial_mm=boss_radial_mm,
        boss_arc_mm=boss_arc_mm,
        boss_arc_deg=boss_arc_deg,
        feature_radius_mm=feature_radius_mm,
        bolt_axis_height_mm=bolt_axis_height_mm,
        dowel_axis_height_mm=dowel_axis_height_mm,
        dowel_hole_diameter_mm=dowel_hole_diameter_mm,
        bearing_area_mm2=bearing_area_mm2,
    )


def _retrofit_layout(
    params: MechanismParams, geometry: RimGeometry
) -> _RetrofitLayout:
    """後付け部品用の締結座を配置する（要件 9.7 / design.md 決定4）。

    座は**フランジの下面**へ垂らしたパッドに、下から止まり穴として設ける。
    ⚠️ 上面（物が落ちてくる面）へ抜かない。⚠️ 内向きへ張り出さない（決定1）。
    ⚠️ フランジの肉厚（出荷値 4mm）に金属インサート（長さ 5.7mm）は収まらない
    ため、パッドを設けずに座を作ることはできない。

    座はリング全体で `retrofit_fastener_count` 箇所であり、分割の境目に当たらない
    よう半ピッチずらした角度に置く。⚠️ **セグメントごとに指定数を設けない**
    （リング全体では指定数 × 分割数になり、決定4 の記録から外れる）。

    Raises:
        GeometryError: 座がフランジの内側／外側へはみ出す、下面まで届かない、
            またはセグメントの端面をまたぐ場合。
    """
    rim = params.rim
    joint = params.joint
    retention = params.retention
    inner_radius_mm = geometry.inner_diameter_mm / 2.0
    outer_radius_mm = geometry.outer_diameter_mm / 2.0
    tan_slope = math.tan(math.radians(rim.flange_slope_deg))

    center_radius_mm = inner_radius_mm + rim.flange_width_mm / 2.0
    pad_diameter_mm = (
        _RETROFIT_PAD_DIAMETER_INSERT_MULTIPLE * joint.insert_outer_diameter_mm
    )
    pad_half_mm = pad_diameter_mm / 2.0
    # ⚠️ **内側と外側を1つの検査にまとめてある。** パッドの中心はフランジ帯の
    # 中央（`inner_radius + flange_width / 2`）に置くため、内側へはみ出す条件
    # `flange_width < 2 × insert_outer_diameter` と外側へはみ出す条件は**同値**で
    # ある。2つの `if` に分けると後ろ側が構造的に到達不能になり、検査したつもりの
    # 死んだ枝が残る。中心半径の取り方を将来変えても両側が守られるよう、条件は
    # 帯への包含として書く。
    if (
        center_radius_mm - pad_half_mm < inner_radius_mm
        or center_radius_mm + pad_half_mm > outer_radius_mm
    ):
        raise GeometryError(
            f"後付け用締結座のパッド（外径 {pad_diameter_mm}mm、中心半径 "
            f"{center_radius_mm}mm）が、フランジの帯 "
            f"[{inner_radius_mm}, {outer_radius_mm}]mm に収まらない"
            f"（flange_width_mm={rim.flange_width_mm!r}、"
            f"insert_outer_diameter_mm={joint.insert_outer_diameter_mm!r}）。"
            "⚠️ 内側へはみ出せば受け口が開口内径を狭め（決定1 に反する）、"
            "外側へはみ出せば外径が `rim_geometry` の導出値と食い違う。"
            "フランジ幅を広げるか、金属インサートを細いものにすること。"
        )

    # フランジ下面の高さ。外周へ向かって上がるため、座の位置での下面はこの値。
    underside_z_mm = rim.height_mm + (center_radius_mm - inner_radius_mm) * tan_slope
    pad_drop_mm = joint.insert_length_mm + _RETROFIT_PAD_DROP_MARGIN_MM
    pad_bottom_z_mm = underside_z_mm - pad_drop_mm
    if pad_bottom_z_mm < _MIN_FEATURE_EDGE_MM:
        raise GeometryError(
            f"後付け用締結座のパッドの下端が z={pad_bottom_z_mm}mm となり、"
            f"部品の底面（z=0）から {_MIN_FEATURE_EDGE_MM}mm の余裕を取れない"
            f"（height_mm={rim.height_mm!r}、"
            f"insert_length_mm={joint.insert_length_mm!r}）。"
            "⚠️ パッドが底面より下へ出ると、ゴミ箱の縁に受け口が座らない。"
        )
    # パッドの上端はフランジの肉の中で止める。⚠️ 上面（捕捉面）へ抜かない。
    pad_height_mm = pad_drop_mm + rim.wall_thickness_mm / 2.0

    sector_deg = 360.0 / geometry.segment_count
    half_deg = sector_deg / 2.0
    pad_half_deg = math.degrees(math.atan(pad_half_mm / center_radius_mm))
    per_segment: list[list[float]] = [[] for _ in range(geometry.segment_count)]
    for seat in range(retention.retrofit_fastener_count):
        # ⚠️ 半ピッチずらす。ずらさないと座の1つが必ず分割の境目に載る
        # （例: 6 箇所 / 5 分割では 0° が境目と一致する）。
        global_deg = 360.0 * (seat + 0.5) / retention.retrofit_fastener_count
        index = min(int(global_deg // sector_deg), geometry.segment_count - 1)
        local_deg = global_deg - (index * sector_deg + half_deg)
        if geometry.segment_count > 1 and abs(local_deg) + pad_half_deg > half_deg:
            raise GeometryError(
                f"後付け用締結座（全体で {retention.retrofit_fastener_count} 箇所）"
                f"の1つが、セグメント {index + 1} の端面をまたぐ"
                f"（局所角 {local_deg}°、パッド半角 {pad_half_deg}°、"
                f"分割半角 {half_deg}°）。"
                "⚠️ 端面をまたぐ座は隣のセグメントと干渉する。座の数か分割数を"
                "見直すこと。"
            )
        per_segment[index].append(local_deg)

    return _RetrofitLayout(
        center_radius_mm=center_radius_mm,
        pad_diameter_mm=pad_diameter_mm,
        pad_bottom_z_mm=pad_bottom_z_mm,
        pad_height_mm=pad_height_mm,
        bore_diameter_mm=joint.insert_outer_diameter_mm,
        bore_depth_mm=joint.insert_length_mm,
        local_angles_deg=tuple(tuple(angles) for angles in per_segment),
    )


def build_segments(params: MechanismParams) -> tuple[RimSegment, ...]:
    """受け口のセグメントを全点構築する（要件 2.6, 3.1, 8.3, 8.4, 9.7）。

    形状は**寸法パラメータから決定される手続き**であり、対話操作も外部 CAD の
    起動も要さない（要件 3.1, 3.2）。断面（半径-高さ平面の閉じた輪郭）を Z 軸
    まわりに分割角だけ回転させて本体を作り、そこへ継手座と締結座を足し引きする。

    ## 形状の決定（design.md「受け口形状の決定」）

    - 決定1: フランジは**外周が高く内周が低い**外向きの傾斜であり、取り付け部の
      内径（`inner_diameter_mm`）より内側へは一切張り出さない。⚠️ **内向きの
      漏斗を作らない。** 立ち上がりは常に外向きであり、符号を反転させた実装は
      `test_flange_rises_outward_and_never_forms_an_inward_funnel` が落とす
    - 決定2: 深さを足さない。取り付け部は縁へ被せる筒であり、本体の内側へ
      伸びる筒を持たない
    - 決定4: 内向きリップの代わりに、後付け部品用の締結座を**リング全体で**
      `retrofit_fastener_count` 箇所設ける

    ## 造形向き（⚠️ 注記）

    **リム面を造形面へ寝かせる向き**（本モジュールの Z 軸＝造形機の Z 軸）を
    前提とする。このとき層法線は +Z であり、**接合面（端面）の法線は XY 平面内に
    あって層法線と一致しない**。ボルトの締結力は層間剥離ではなく層内のせん断と
    支圧で受けられる。⚠️ この前提が崩れる置き方（端面を造形面へ伏せる）では、
    接合部の強度が層間の接着に律速される。
    `test_joint_faces_are_not_normal_to_the_layer_direction` が端面の法線を固定
    している。

    ## 参照解決

    参照は**幾何セレクタ**（角度・半径・高さ）で明示的に組み立てる。⚠️ 生成名
    （`Face6` 等）を一切使わない——寸法を変えたときに名前が振り直されても、
    位置と向きで書かれた参照は同じ場所を指し続ける（design.md「Shapes」
    Implementation Notes）。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        セグメント1点につき1つの `RimSegment`。長さは
        `rim_geometry(params).segment_count` に一致し、`index` はリング上の位置。

    Raises:
        GeometryError: 開口を狭める寸法、収まる分割数が存在しない場合
            （`rim_geometry` からの伝播）、実高さを含む外接箱が造形可能寸法を
            超える場合、または継手・締結座が寸法的に成立しない場合。
        ParameterError: 支圧面積が `min_bearing_area_mm2` を下回る場合
            （`constraints.check_joint` からの伝播。要件 2.6, 8.4）。
        ImportError: 形状ライブラリ（`cad` extra）が導入されていない場合。
            ⚠️ 上記の検査は**すべて import より前**に済むため、寸法が成立しない
            ことは CAD 非導入の環境でも観測できる（要件 5.7）。
    """
    geometry = rim_geometry(params)
    rim = params.rim
    joint_policy = params.joint

    # ⚠️ **実高さを伴う造形制約の検査**（tasks.md タスク 3.1(a) の申し送りの決着）。
    # `required_segment_count` は高さに `build_z_mm` を置くため Z を検査しない。
    violations = check_envelope(PART_NAMES[0], segment_envelope(params), params.printing)
    if violations:
        detail = "、".join(
            f"軸 {violation.axis} が {violation.envelope_mm}mm で"
            f"上限 {violation.limit_mm}mm を {violation.excess_mm}mm 超過"
            for violation in violations
        )
        raise GeometryError(
            f"セグメントの外接箱が造形可能寸法に収まらない（{detail}）。"
            "⚠️ 分割は円周方向にのみ効くため、Z 軸の超過は分割数では解決しない"
            f"（実高さ {segment_height_mm(params)}mm は取り付け部の高さ "
            f"height_mm={rim.height_mm!r} に肉厚とフランジの立ち上がりを足した値）。"
        )

    if rim.wall_thickness_mm >= rim.flange_width_mm:
        raise GeometryError(
            f"wall_thickness_mm={rim.wall_thickness_mm!r} が "
            f"flange_width_mm={rim.flange_width_mm!r} 以上であり、フランジの断面が"
            "成立しない。⚠️ フランジは取り付け部の壁より外側へ張り出す部分である。"
        )

    joint = None if geometry.segment_count == 1 else _joint_layout(params, geometry)
    if joint is not None:
        # 要件 2.6, 8.4: 支圧面積の下限を満たさない継手は構築しない。
        check_joint(joint_policy, joint.bearing_area_mm2)
    retrofit = _retrofit_layout(params, geometry)

    # ⚠️ **形状ライブラリの import は関数内で行う。** design.md は `shapes` に
    # build123d の import を許すが、モジュール直下へ置くと `rim_geometry` すら
    # CAD 非導入の環境で評価できなくなり、design.md「Shapes」Implementation Notes
    # の Validation（形状構築の前に不変条件を検査できる）と要件 5.7 が壊れる。
    from build123d import (
        Align,
        Axis,
        Cylinder,
        Location,
        Polyline,
        Rotation,
        make_face,
        revolve,
    )

    inner_radius_mm = geometry.inner_diameter_mm / 2.0
    outer_radius_mm = geometry.outer_diameter_mm / 2.0
    wall_mm = rim.wall_thickness_mm
    skirt_mm = rim.height_mm
    tan_slope = math.tan(math.radians(rim.flange_slope_deg))
    rise_mm = _flange_rise_mm(rim)
    sector_deg = 360.0 / geometry.segment_count
    half_deg = sector_deg / 2.0

    def profile(points: tuple[tuple[float, float], ...]) -> object:
        """半径-高さの点列（`(r, z)`）から、Z 軸を含む平面上の面を作る。"""
        return make_face(
            Polyline(*[(radius, 0.0, height) for radius, height in points], close=True)
        )

    # 断面。⚠️ 上面は内周 `skirt + wall` から外周 `skirt + wall + rise` へ**上がる**
    # （決定1: 外周が高く内周が低い）。取り付け部の内周面は z=0 から上端まで
    # 径 `inner_diameter` の円筒であり、ここが「通過できる最小径」を決める。
    body = revolve(
        Rotation(0, 0, -half_deg)
        * profile(
            (
                (inner_radius_mm, 0.0),
                (inner_radius_mm, skirt_mm + wall_mm),
                (outer_radius_mm, skirt_mm + wall_mm + rise_mm),
                (outer_radius_mm, skirt_mm + rise_mm),
                (inner_radius_mm + wall_mm, skirt_mm + wall_mm * tan_slope),
                (inner_radius_mm + wall_mm, 0.0),
            )
        ),
        Axis.Z,
        revolution_arc=sector_deg,
    )

    def face_bore(
        angle_deg: float,
        into_material: bool,
        radius_mm: float,
        height_mm: float,
        bore_diameter_mm: float,
        depth_mm: float,
    ) -> object:
        """端面へ、その面の法線方向に穴を掘る円筒を作る（幾何セレクタ）。

        位置は「角度・半径・高さ」、向きは「その角度における接線」で決まる。
        `into_material` が真なら反時計回りの接線（＝始端面から材料側）、偽なら
        その逆（＝終端面から材料側）を向く。
        """
        theta = math.radians(angle_deg)
        axis_deg = angle_deg if into_material else angle_deg + 180.0
        return (
            Location(
                (
                    radius_mm * math.cos(theta),
                    radius_mm * math.sin(theta),
                    height_mm,
                )
            )
            * Rotation(0, 0, axis_deg)
            * Rotation(-90, 0, 0)
            * Cylinder(
                bore_diameter_mm / 2.0,
                depth_mm,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
        )

    if joint is not None:
        boss = profile(
            (
                (inner_radius_mm, 0.0),
                (inner_radius_mm + joint.boss_radial_mm, 0.0),
                (
                    inner_radius_mm + joint.boss_radial_mm,
                    skirt_mm + joint.boss_radial_mm * tan_slope,
                ),
                (inner_radius_mm, skirt_mm),
            )
        )
        # 両端面にフランジ下面までを満たす座を足す。
        body += revolve(
            Rotation(0, 0, -half_deg) * boss, Axis.Z, revolution_arc=joint.boss_arc_deg
        )
        body += revolve(
            Rotation(0, 0, half_deg - joint.boss_arc_deg) * boss,
            Axis.Z,
            revolution_arc=joint.boss_arc_deg,
        )
        # 始端面: 貫通ボルト穴（バカ穴）。⚠️ 座を**抜けて**フランジ下の空間へ出る
        # 深さを取る。止まり穴にするとボルトを通せない。
        body -= face_bore(
            -half_deg,
            True,
            joint.feature_radius_mm,
            joint.bolt_axis_height_mm,
            joint_policy.through_hole_diameter_mm,
            2.0 * joint.boss_arc_mm,
        )
        # 終端面: 金属インサート座と、その奥へ続くボルト先端の逃げ。
        body -= face_bore(
            half_deg,
            False,
            joint.feature_radius_mm,
            joint.bolt_axis_height_mm,
            joint_policy.insert_outer_diameter_mm,
            joint_policy.insert_length_mm,
        )
        body -= face_bore(
            half_deg,
            False,
            joint.feature_radius_mm,
            joint.bolt_axis_height_mm,
            joint_policy.through_hole_diameter_mm,
            _BOLT_TIP_CLEARANCE_LENGTH_MULTIPLE * joint_policy.insert_length_mm,
        )
        # 両端面: 位置決めダボ。⚠️ **荷重を受けない**——ボルト軸とは別の高さに、
        # すきま嵌めの穴として置く（要件 2.6, 8.4）。
        dowel_depth_mm = (
            _DOWEL_DEPTH_DIAMETER_MULTIPLE * joint_policy.dowel_diameter_mm
        )
        body -= face_bore(
            -half_deg,
            True,
            joint.feature_radius_mm,
            joint.dowel_axis_height_mm,
            joint.dowel_hole_diameter_mm,
            dowel_depth_mm,
        )
        body -= face_bore(
            half_deg,
            False,
            joint.feature_radius_mm,
            joint.dowel_axis_height_mm,
            joint.dowel_hole_diameter_mm,
            dowel_depth_mm,
        )

    def retrofit_cylinder(angle_deg: float, diameter_mm: float, height_mm: float) -> object:
        """締結座のパッド／止まり穴を、局所角 `angle_deg` の位置に作る。"""
        return Rotation(0, 0, angle_deg) * (
            Location((retrofit.center_radius_mm, 0.0, retrofit.pad_bottom_z_mm))
            * Cylinder(
                diameter_mm / 2.0,
                height_mm,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
        )

    names = segment_part_names(params)
    segments: list[RimSegment] = []
    for index, name in enumerate(names):
        solid = body
        angles = retrofit.local_angles_deg[index]
        for angle_deg in angles:
            solid += retrofit_cylinder(
                angle_deg, retrofit.pad_diameter_mm, retrofit.pad_height_mm
            )
        for angle_deg in angles:
            # ⚠️ 座は**下から**掘る止まり穴である。上面（捕捉面）へ抜かない。
            solid -= retrofit_cylinder(
                angle_deg, retrofit.bore_diameter_mm, retrofit.bore_depth_mm
            )
        segments.append(
            RimSegment(
                name=name,
                index=index,
                solid=solid,
                retrofit_seat_count=len(angles),
                joint_bearing_area_mm2=(
                    None if joint is None else joint.bearing_area_mm2
                ),
            )
        )
    return tuple(segments)


# ---------------------------------------------------------------------------
# 形状指標の抽出（タスク 3.3 / 要件 3.7, 4.1）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuiltPart:
    """構築済みの部品1点と、その形状指標（design.md「Shapes」Service Interface）。

    ⚠️ **`solid` を中核層へ渡さない。** 中核層（`metrics` 以左）へ渡るのは
    `metrics` だけであり、その中身は素の数値である（`metrics.PartMetrics`）。
    `solid` の型を `object` にしてあるのは、形状ライブラリの型が中核層の署名へ
    漏れないようにするためである（`RimSegment.solid` と同じ扱い）。

    ⚠️ **`RimSegment` を置き換えるものではない。** `RimSegment` は「構築時にしか
    分からない数」（後付け座の数・支圧面積）を運び、本型は「構築物とその指標」を
    運ぶ。書き出し（タスク 3.4）と記録（タスク 4.2）が必要とするのは後者であり、
    前者は継手の検査に属する。両方が要る呼び出し側は `build_segments` を使う。

    Attributes:
        name: 部品名。`segment_part_names` の同じ位置の要素と一致し、
            `metrics.part_name` とも一致する。
        solid: build123d の `Part`。⚠️ 中核層へは渡らない。
        metrics: 体積・境界箱・立体数（`metrics.PartMetrics`）。⚠️ **`solid` から
            抽出した値であり、解析式ではない。**
    """

    name: str
    solid: object
    metrics: PartMetrics


def measure_part(name: str, solid: object) -> PartMetrics:
    """構築済みの形状から体積・境界箱・立体数を抽出する（要件 4.1）。

    ⚠️ **形状ライブラリを import しない。** `solid` は `object` として受け取り、
    `volume` / `bounding_box()` / `solids()` という**属性の形**だけに依存する。
    型注釈にも実装にも build123d の名前が現れないため、本モジュールの遅延 import
    の規律（モジュール docstring / 要件 5.7）を本関数が破ることはない。
    形状ライブラリ非導入の環境でも、同じ属性を持つ任意のオブジェクトを測れる
    ——`test_measure_part_extracts_the_three_metrics_by_duck_typing` がこれを
    偽のソリッドで固定する。

    ⚠️ **丸めない・量子化しない。** OCCT が返した値をそのまま `PartMetrics` へ
    渡す。design.md「Shapes」Risks が言う「OCCT のバージョン差で体積の下位桁が
    動く」への備えは**記録側の許容差**（`metrics.GeometryBaseline` の
    `volume_rel_tolerance` / `bbox_abs_tolerance_mm`）であり、抽出側にも丸めを
    置くと許容差の機構が二重になる。二重にすると、記録側の許容差を 0 に置いても
    抽出側の丸め幅より小さいずれは観測できず、「どれだけ動いたか」を記録から
    読めなくなる。`test_measure_part_does_not_round_or_quantize_the_extracted_numbers`
    がビット列で固定する。

    ⚠️ **境界箱は `solid` が置かれている座標系のままの軸並行外接箱である。**
    部品固有の座標系へ移し替えた最小外接箱ではない。`build_segments` の返す
    セグメントはリングの中心を原点として +X 軸まわりに置かれているため、
    ここで得る箱は `constraints.check_envelope` へ渡している量
    （`test_built_part_bbox_is_the_envelope_used_for_the_build_volume_check`）と
    同一である。⚠️ 将来セグメントを造形板へ寝かせて再配置する場合、指標の
    境界箱も一緒に変わる——記録（タスク 4.2）は配置込みの値である。

    Args:
        name: 部品名。`PartMetrics.part_name` になる。
        solid: 構築済みの形状。`volume` / `bounding_box()` / `solids()` を持つ。

    Returns:
        抽出した形状指標。

    Raises:
        ParameterError: 名が空、体積が正の有限値でない、境界箱が3つの正の有限値で
            ない、または立体数が 1 未満の場合（`metrics.PartMetrics` の構築時検証
            からの伝播）。⚠️ **立体を持たない形状を「指標」として記録しない。**
            0 個の立体は部品ではなく、形状生成が空を返した事故である。
        AttributeError: `solid` が上記3つの属性を持たない場合。
    """
    size = solid.bounding_box().size  # type: ignore[attr-defined]
    return PartMetrics(
        part_name=name,
        volume_mm3=float(solid.volume),  # type: ignore[attr-defined]
        bbox_mm=(float(size.X), float(size.Y), float(size.Z)),
        solid_count=len(solid.solids()),  # type: ignore[attr-defined]
    )


def build_parts(params: MechanismParams) -> tuple[BuiltPart, ...]:
    """受け口の全部品を構築し、それぞれの形状指標を添えて返す（要件 3.7, 4.1）。

    design.md「Shapes」Service Interface の入口である。⚠️ **形状の構築を持たない。**
    `build_segments` へ委譲し、その結果を測って包むだけである——決定1（内向きに
    張り出さない）・締結座の配分・継手の検査といった規則を2箇所へ分岐させない
    （`test_build_parts_delegates_the_construction_to_build_segments` が呼び出し
    そのものを観測する）。

    ⚠️ **返る部品は1点ではなく `rim_geometry(params).segment_count` 点である。**
    後付け部品用の締結座はリング全体で `retrofit_fastener_count` 箇所であり
    （design.md「受け口形状の決定」決定4）、その数が分割数で割り切れないとき
    ——出荷値の 6 箇所 / 5 分割——座の配分が `[1, 1, 2, 1, 1]` となって
    **セグメントは同一形状にならない**。「1点を分割数だけ刷る」のではないため、
    書き出し（タスク 3.4）も記録（タスク 4.2）も部品ごとの行を必要とする。

    ## 決定性（design.md「Shapes」Invariants / 要件 3.7）

    同一パラメータからの再構築は**完全に同一の** `PartMetrics` を返す。⚠️ これは
    許容差つきの一致ではない——build123d 0.11.1 / OCCT では、同一プロセス内でも
    別プロセス間でも体積・境界箱がビット単位で一致することを実測で確認している。
    記録側の許容差（`metrics.GeometryBaseline`）は**ライブラリの版差**を吸収する
    ためのものであり、同一環境での再構築がそれに頼ってよい理由にはならない。
    `test_metrics_are_exactly_identical_across_two_independent_builds` が
    `pytest.approx` を使わずに固定する。

    ⚠️ **一致は「全部品が同じ値になること」ではない。** 座を2つ持つ部品は他の4点より
    約 184mm^3 大きく、これは実形状の差である。
    `test_the_segment_with_two_retrofit_seats_is_measurably_distinct` が
    「違いが実在する」側を観測している。

    ⚠️ **座を1つ持つ4点が下位桁で分かれるのは求積誤差であり、実形状の差ではない。**
    実測で 36524.60576950924〜36524.606679998156（幅 約 9.1e-4 mm^3、相対 2.5e-8）に
    ばらつくが、`BRepGProp` の `Eps` を 1e-9 まで締めると4点は
    36524.60616466… に**相対 2e-13 で収束する**。座の角度ごとに融合面の
    パラメータ化が変わるために既定精度の `Shape.volume` が返す値が動くだけであり、
    真の体積は同一である。剛体回転・鏡映による体積変化は相対 4e-16（1〜2 ULP）に
    すぎず、配置そのものは体積を動かさない。実際、`retrofit_fastener_count` を
    10 や 5（全セグメントが同一相対角）にすると5点はビット単位で一致する。
    ⚠️ この誤差は決定的（プロセス間でビット一致）だが、design.md「Shapes」Risks が
    言う「OCCT の版差で体積の下位桁が動く」の実例である。**記録側の許容差
    （`metrics.GeometryBaseline.volume_rel_tolerance`）は 1e-7 以上を下限とすること**
    ——実測の相対誤差 1.3e-8 を下回る値を記録すると版差で確実に破綻する。

    Args:
        params: 寸法パラメータの集約。

    Returns:
        セグメント1点につき1つの `BuiltPart`。並びと名前は
        `segment_part_names(params)` に一致する。

    Raises:
        GeometryError: `build_segments` からの伝播（開口を狭める寸法、収まる分割数
            が無い、実高さが造形可能寸法を超える、継手・締結座が成立しない）。
        ParameterError: `constraints.check_joint` からの伝播（支圧面積の不足）、
            または抽出した指標が `PartMetrics` の不変条件に反する場合。
        ImportError: 形状ライブラリ（`cad` extra）が導入されていない場合。
    """
    return tuple(
        BuiltPart(
            name=segment.name,
            solid=segment.solid,
            metrics=measure_part(segment.name, segment.solid),
        )
        for segment in build_segments(params)
    )
