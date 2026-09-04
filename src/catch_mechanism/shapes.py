"""受け口の幾何導出（design.md「Shapes」/ 要件 8.1, 8.2, 8.5, 8.6）。

寸法パラメータだけから受け口（ワイドリム）の**数値としての幾何**を導く。
本モジュールが返す `RimGeometry` は、取り付け部の内径・通過できる最小径・外径・
分割数の4つであり、⚠️ **形状（ソリッド）を一切構築しない**。design.md「Shapes」
Implementation Notes の Validation「`rim_geometry` は形状構築の前に評価でき、
不変条件の検査を軽量に行える」がこの分離の理由である——開口を狭めていないか、
造形可能寸法に収まる分割数が存在するか、という**成立条件の検査は形状より前に
決まる**。形状を作らなければ答えられない検査にしてしまうと、CAD の入っていない
環境では受け口が成立するかどうかすら分からなくなる（要件 5.2, 5.3）。

そのため**本モジュールは現時点で形状ライブラリ（build123d）を import しない**。
design.md「Allowed Dependencies」は `shapes` / `export` に限って import を許すが、
許されていることと必要であることは別である。`rim_geometry` は純粋な算術であり、
`cad` extra 非導入の環境でも全不変条件を検査できる。
⚠️ **申し送り**: ソリッド構築（`build_parts` / `measure_part`、および部品名の表
`PART_NAMES`）は**タスク 3.2 / 3.3 の担当**である。build123d の import はそこで
入る。本モジュールの現在の公開面が design.md の Service Interface より狭いのは、
未実装ではなく**タスク境界**による。

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

⚠️ **`check_envelope` は本モジュールから呼ばない。** design.md「Shapes」の
Preconditions「`check_material` / `check_envelope` を通過している」は
**呼び出し側の責務**である。`check_envelope` は違反を例外ではなく**値**として
返す設計であり（design.md「Error Strategy」/ `constraints.py`）、それを失敗として
扱うのは生成物を書き出す側（タスク 3.4）である。本モジュールが返す外径と分割数は
そこで `sector_envelope` に渡され検査される。なお `required_segment_count` が
返した分割数の扇形は必ず `check_envelope` を通る（余裕 `segment_margin_mm` の
分だけ厳しい上限で導出しているため。tasks.md タスク 2.1(c)）。
"""

from __future__ import annotations

from dataclasses import dataclass

from catch_mechanism.constraints import required_segment_count
from catch_mechanism.errors import GeometryError
from catch_mechanism.params import MechanismParams

__all__ = [
    "RimGeometry",
    "rim_geometry",
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
