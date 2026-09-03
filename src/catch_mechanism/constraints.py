"""造形制約の検査と分割数の導出（design.md「Constraints」/ 要件 2.2, 2.3, 2.4,
2.7, 2.8, 8.3）。

**分割数はパラメータではなく導出値である。** 手で「3分割」と決めると、採寸値が
更新されたときに黙って造形可能寸法を超える（design.md「Constraints」
Responsibilities）。本モジュールは外径と造形制約だけから最小の分割数を導出し、
導出できない場合は `GeometryError` で拒否する。

⚠️ **円は正方形の対角線を使えない。** 判定は**軸並行の外接箱**で行う
（design.md「Constraints」/ `PrintingConstraints` の docstring）。180mm 角の
造形面に対して対角線 254.6mm を使ってよいことにすると、実際には載らない分割数が
「収まる」と判定される。

⚠️ **扇形の外接箱は弦長だけではない。** 円環を n 等分した扇形の外接箱は
「接線方向の広がり（弦長 `D·sin(π/n)`）」と「半径方向の広がり」の2辺からなり、
後者は**分割数を増やしても縮まない**。弦長だけを見る実装は、半径方向が造形面を
はみ出す形状に対して大きな分割数を黙って返してしまう。本モジュールは両方の辺を
`sector_envelope` で求め、design.md の Postconditions「戻り値 n に対し、n 等分した
扇形が造形可能寸法に収まる」を実際に成り立たせる。半径方向の広がりは
`required_segment_count` が受け取る唯一の形状情報である外径のみから決まる**最悪
値**（円環ではなく中心を含む扇形、すなわち半径 `D/2`）を採る——内径を知らない
以上、楽観的に見積もってはならない。この保守側への倒し方により、返る分割数は
「どんな内径の円環断片でも必ず収まる」ことが保証される。

## この検査の対象範囲（要件 2.8）

対象とするのは次の4点だけである。

- 部品の外接箱が造形可能寸法に収まること（要件 2.2, 2.3, 2.4）
- 円環を分割した断片が造形可能寸法に収まる最小の分割数（要件 8.3）
- 材料が許可一覧に含まれること（要件 2.5）
- 継手の当たり面（支圧面積）が下限を満たすこと（要件 2.6, 8.4）

⚠️ **切削加工（フライス・旋盤）を前提とする形状は、そもそも設計に含めない**
（要件 2.8）。したがって本モジュールに「切削で仕上げる面」「工具が届くか」と
いった検査は無い。これは検査の漏れではなく**対象範囲の宣言**である——手元に
工作機械が無い前提で、積層造形だけで作れる形状に限る、という設計上の方針を
検査の側から言い直したものであり、方針が破られたときに検査で拾えると誤解させ
ないために明示する。

同様に、造形時間・サポート材の量・オーバーハング角・反りといった造形の**質**に
関わる事柄も対象外である。これらは造形機の設定と後工程の領域であり、寸法
パラメータからは決まらない。

`check_envelope` は違反を**値として返す**（例外にしない）。要件 2.4 の
「超過している軸と超過量を示す」は全軸の一覧を返して初めて満たせるため、最初の
違反で打ち切ってはならない（design.md「Error Strategy」の「評価結果は値で返す」）。
それを失敗として扱うのは呼び出し側（形状生成・書き出し、タスク 3.4）であり、
そこで `GeometryError` を送出する。⚠️ `errors.GeometryError` は依存を持たない
モジュールにあり `BuildViolation` を import できないため、違反の**軸と超過量は
例外メッセージへ載せる**（tasks.md「Implementation Notes」タスク 1.2）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from catch_mechanism.errors import GeometryError, ParameterError
from catch_mechanism.params import ALLOWED_MATERIALS, JointPolicy, PrintingConstraints

__all__ = [
    "MAX_SEGMENT_COUNT",
    "Envelope",
    "BuildViolation",
    "sector_envelope",
    "required_segment_count",
    "check_envelope",
    "check_material",
    "check_joint",
]


MAX_SEGMENT_COUNT: int = 12
"""分割数の現実的な上限（design.md「Constraints」Invariants の「例: 12」）。

これを超えても収まらない場合は**例外で拒否する**。分割数を増やせばいつかは
収まる、という発想は接合部の数と組立誤差を無制限に増やすだけであり、実際には
12 分割の受け口は成立しない。

⚠️ 半径方向の広がりは分割数に依らず一定であるため、本モジュールの判定では
「上限まで探しても収まらない」状況は事実上**外径が造形面の2倍を超えた**ことを
意味する（n ≥ 6 では半径方向が接線方向を上回り、それ以上分割しても外接箱は
縮まない）。上限を明示的な定数として残すのは、探索が有限であることを型と
テストの上で示すためである。
"""


def _require_positive_finite(value: float, name: str) -> None:
    """`value` が正の有限値であることを検証する（`params` の同名の規約に合わせる）。"""
    if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0.0):
        raise ParameterError(f"{name}={value!r} は正の有限値でなければならない。")


def _require_segment_count(value: int, name: str) -> None:
    """`value` が 1 以上の整数であることを検証する。

    `bool` は `int` の派生であるため明示的に除く（`params.RetentionParams` と
    同じ扱い）。「2分割」を `True` で表せてしまう余地を残さない。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ParameterError(f"{name}={value!r} は 1 以上の整数でなければならない。")


@dataclass(frozen=True, slots=True)
class Envelope:
    """部品の軸並行な外接箱（design.md「Constraints」Service Interface）。

    ⚠️ **姿勢を含まない。** 部品をどう置くかは形状生成側の決定であり、本型は
    「置いた結果の各軸の広がり」だけを持つ。斜め配置を表現できないのは制限では
    なく、斜め配置に頼らせないための意図的な形である。

    Attributes:
        x_mm, y_mm, z_mm: 各軸方向の広がり（mm）。

    Raises:
        ParameterError: いずれかが正の有限値でない場合。0 の広がりを持つ部品は
            形状として成立せず、未計算の 0 を黙って通してしまう。
    """

    x_mm: float
    y_mm: float
    z_mm: float

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は違反フィールド名と値を添えて拒否する。"""
        _require_positive_finite(self.x_mm, "x_mm")
        _require_positive_finite(self.y_mm, "y_mm")
        _require_positive_finite(self.z_mm, "z_mm")


@dataclass(frozen=True, slots=True)
class BuildViolation:
    """造形可能寸法の超過1件（design.md「Constraints」Service Interface）。

    本型は検査の**出力**であり、本モジュール自身が矛盾のない値で構築する。
    したがって `Envelope` と異なり構築時検証を持たない——出力側で再検証しても
    直せる呼び出し側がおらず、検査の失敗が検査自身の例外に化けるだけである。

    Attributes:
        part_name: 違反した部品の名前。
        axis: 超過している軸（`"x"` / `"y"` / `"z"`）。
        envelope_mm: その軸の外接箱の広がり（mm）。
        limit_mm: その軸の造形可能寸法（mm）。
        excess_mm: 超過量（`envelope_mm - limit_mm`、常に正）。
    """

    part_name: str
    axis: str
    envelope_mm: float
    limit_mm: float
    excess_mm: float


def sector_envelope(
    outer_diameter_mm: float, segment_count: int, height_mm: float
) -> Envelope:
    """円環を `segment_count` 等分した断片の軸並行外接箱を返す（要件 2.3, 8.3）。

    姿勢は「扇形の対称軸を +X 軸に合わせ、頂点（円の中心）を原点に置く」ものと
    する。この姿勢での外接箱は次の2辺からなる。

    - **X（半径方向）**: 中心から外周までの `D/2`。⚠️ 内径を仮定せず、中心を含む
      扇形という**最悪値**を採る（内径が大きい細い円環ほど実際は小さくなるが、
      本関数は内径を受け取らない）。
    - **Y（接線方向）**: 弦長 `D·sin(π/n)`。`n = 2` で最大値 `D` を取り、
      `n` を増やすと単調に減る。

    `n = 1`（分割しない）は扇形ではなく円環そのものであり、外接箱は `D × D` に
    なる。⚠️ 弦長の式は `sin(π/1) = 0` を返すため、この場合を式に任せてはならない。

    Args:
        outer_diameter_mm: 円環の外径（mm）。
        segment_count: 分割数（1 以上）。
        height_mm: 断片の高さ（mm）。そのまま Z 軸の広がりになる。

    Returns:
        断片の軸並行外接箱。

    Raises:
        ParameterError: 外径・高さが正の有限値でない場合、または分割数が 1 以上の
            整数でない場合。
    """
    _require_positive_finite(outer_diameter_mm, "outer_diameter_mm")
    _require_segment_count(segment_count, "segment_count")
    if segment_count == 1:
        return Envelope(x_mm=outer_diameter_mm, y_mm=outer_diameter_mm, z_mm=height_mm)
    radial_mm = outer_diameter_mm / 2.0
    chord_mm = outer_diameter_mm * math.sin(math.pi / segment_count)
    return Envelope(x_mm=radial_mm, y_mm=chord_mm, z_mm=height_mm)


def _plane_limit_mm(printing: PrintingConstraints) -> tuple[str, float]:
    """造形面上で断片が使える1辺の上限と、その上限を与える軸の名前を返す。

    ⚠️ **どちらの軸に弦を向けるかを本モジュールは決められない。** 断片を造形面へ
    どう置くかは形状生成・スライサ側の決定であるため、`min(build_x, build_y)` を
    両辺の共通の上限とし、**どちらの向きに置いても収まる**分割数だけを導出する。
    正方形の造形面（A1 mini の 180×180）ではこれは何も損なわず、長方形の造形面
    でのみ保守側に倒れる。

    余裕（`segment_margin_mm`）はここで差し引く。0 を許すのは「余裕を取らない」が
    意味を持つ設定だからであり（`params.PrintingConstraints`）、0 のとき本関数は
    造形可能寸法そのものを返す。
    """
    if printing.build_x_mm <= printing.build_y_mm:
        axis, limit_mm = "x", printing.build_x_mm
    else:
        axis, limit_mm = "y", printing.build_y_mm
    return axis, limit_mm - printing.segment_margin_mm


def required_segment_count(outer_diameter_mm: float, printing: PrintingConstraints) -> int:
    """外径 `outer_diameter_mm` の円環が造形可能寸法に収まる最小の分割数を返す。

    要件 2.2（分割が必要かの判定）と要件 8.3（超えるなら分割した形で設計する）を
    1つの導出にまとめる。戻り値 1 は「分割しなくても収まる」ことを意味する。

    判定は `sector_envelope` が返す**軸並行の外接箱**の2辺を、造形面の上限
    （`min(build_x, build_y) - segment_margin_mm`）と比べて行う。⚠️ 斜め配置
    （対角線）を仮定しない。

    Args:
        outer_diameter_mm: 円環の外径（mm）。
        printing: 造形機の制約。

    Returns:
        `1` 以上 `MAX_SEGMENT_COUNT` 以下の最小の分割数。この戻り値 n に対し、
        n 等分した断片の外接箱は造形可能寸法に収まる（design.md Postconditions）。

    Raises:
        ParameterError: 外径が正の有限値でない場合。
        GeometryError: `MAX_SEGMENT_COUNT` までのどの分割数でも収まらない場合
            （design.md Invariants）。⚠️ **黙って大きな分割数を返さない。**
            メッセージには超過している軸・外接箱・上限・超過量を載せる
            （要件 2.4。`errors` は `BuildViolation` を import できないため、
            値ではなくメッセージで運ぶ）。
    """
    _require_positive_finite(outer_diameter_mm, "outer_diameter_mm")
    axis, limit_mm = _plane_limit_mm(printing)
    # 高さは分割数の導出に効かない（分割は円周方向にのみ行うため）。ここでは
    # 上限に収まる任意の正値を置き、Z 軸は `check_envelope` の担当とする。
    height_mm = printing.build_z_mm
    for segment_count in range(1, MAX_SEGMENT_COUNT + 1):
        envelope = sector_envelope(outer_diameter_mm, segment_count, height_mm)
        if max(envelope.x_mm, envelope.y_mm) <= limit_mm:
            return segment_count
    # 上限まで探索しても収まらなかった。外接箱の辺は分割数について単調に縮む
    # （半径方向は一定、接線方向は減少する）ため、`MAX_SEGMENT_COUNT` 分割での
    # 違反が**最も条件の良い**違反であり、これを報告する。
    envelope = sector_envelope(outer_diameter_mm, MAX_SEGMENT_COUNT, height_mm)
    longest_mm = max(envelope.x_mm, envelope.y_mm)
    worst = BuildViolation(
        part_name=f"ring-segment(1/{MAX_SEGMENT_COUNT})",
        axis=axis,
        envelope_mm=longest_mm,
        limit_mm=limit_mm,
        excess_mm=longest_mm - limit_mm,
    )
    raise GeometryError(
        f"outer_diameter_mm={outer_diameter_mm!r} の円環は、"
        f"{MAX_SEGMENT_COUNT} 分割までのどの分割数でも造形可能寸法に収まらない。"
        f"{MAX_SEGMENT_COUNT} 分割でも軸 {worst.axis} の外接箱が "
        f"{worst.envelope_mm}mm で、上限 {worst.limit_mm}mm"
        f"（造形可能寸法 - segment_margin_mm={printing.segment_margin_mm}）を "
        f"{worst.excess_mm}mm 超過する。⚠️ 半径方向の広がりは分割数を増やしても"
        "縮まないため、分割では解決しない（外径そのものを見直すこと）。"
    )


def check_envelope(
    part_name: str, envelope: Envelope, printing: PrintingConstraints
) -> tuple[BuildViolation, ...]:
    """部品の外接箱を造形可能寸法と突き合わせ、超過を**全件**返す（要件 2.2-2.4）。

    ⚠️ **最初の違反で打ち切らない。** 要件 2.4 の「超過している軸と超過量を示す」は、
    x だけ直したら次は y が出た、という往復を避けるために全軸をまとめて返す。

    ⚠️ **余裕（`segment_margin_mm`）は差し引かない。** 余裕は分割数の導出で
    見込む量であり（`_plane_limit_mm`）、造形可能寸法そのものを判定基準にする。
    したがって `required_segment_count` が返した分割数の断片は、必ず本関数も通る。

    ⚠️ **例外を送出しない。** 違反は値として返り、それを失敗として扱うのは
    呼び出し側（形状生成・書き出し、タスク 3.4）である
    （design.md「Error Strategy」）。

    Args:
        part_name: 部品の名前。違反に添えて返す（要件 3.6「失敗した部品名を示す」）。
        envelope: 部品の軸並行外接箱。
        printing: 造形機の制約。

    Returns:
        超過1件につき1つの `BuildViolation` を x, y, z の順に並べたタプル。
        収まっていれば空タプル。等しい場合は超過ではない。
    """
    limits = (
        ("x", envelope.x_mm, printing.build_x_mm),
        ("y", envelope.y_mm, printing.build_y_mm),
        ("z", envelope.z_mm, printing.build_z_mm),
    )
    return tuple(
        BuildViolation(
            part_name=part_name,
            axis=axis,
            envelope_mm=extent_mm,
            limit_mm=limit_mm,
            excess_mm=extent_mm - limit_mm,
        )
        for axis, extent_mm, limit_mm in limits
        if extent_mm > limit_mm
    )


def check_material(printing: PrintingConstraints) -> None:
    """材料が許可一覧に含まれることを検査する（要件 2.5, 2.7）。

    ⚠️ **`PrintingConstraints.__post_init__` と重複する検査ではない。** 構築時
    検証が働くのは `__init__` を通った場合だけであり、`pickle` による復元や
    `object.__setattr__` による書き換えはこれを迂回する（`slots=True` の
    データクラスは `__setstate__` 経由で復元され `__post_init__` を呼ばない）。
    本関数は**生成物を書き出す直前に呼ぶ関門**であり、要件 2.7 の「検査を形状生成の
    一部として実行し、検査を通らない形状の生成物を出力しない」を材料について
    満たす。

    許可一覧の正は `params.ALLOWED_MATERIALS` の1箇所だけである（ここで別の一覧を
    持たない）。⚠️ ASA / ABS / PC / PA / CF・GF は一覧に含まれない。

    Args:
        printing: 造形機の制約。

    Raises:
        ParameterError: 材料が `ALLOWED_MATERIALS` に無い場合。`cli` はこれを
            終了コード 2 に対応させる（design.md「Error Categories and Responses」）。
    """
    if printing.material not in ALLOWED_MATERIALS:
        allowed = ", ".join(sorted(ALLOWED_MATERIALS))
        raise ParameterError(
            f"material={printing.material!r} は許可されていない。"
            f"指定できるのは {allowed} のみである"
            "（大文字小文字・前後の空白も一致していなければならない）。"
        )


def check_joint(joint: JointPolicy, bearing_area_mm2: float) -> None:
    """継手の当たり面（支圧面積）が下限を満たすことを検査する（要件 2.6, 8.4）。

    締結の当たり面が小さいと、ボルトの締め付け力が樹脂へ集中して座面がめり込み、
    締結が緩む。`JointPolicy.min_bearing_area_mm2` はその下限であり、寸法
    パラメータとして設定ファイルに置かれている（要件 2.1）。

    ⚠️ **位置決めダボ（`dowel_diameter_mm`）の面積を足してはならない。** 荷重を
    受けるのは貫通ボルトと金属インサートだけであり、ダボへ荷重を負わせない区別を
    `JointPolicy` は型の上で表している（要件 2.6, 8.4）。本関数が受け取る
    `bearing_area_mm2` は**荷重を受ける当たり面の面積**である。

    Args:
        joint: 継手方針。
        bearing_area_mm2: 実際に確保できている支圧面積（mm^2）。

    Raises:
        ParameterError: 支圧面積が正の有限値でない場合、または下限を下回る場合。
            メッセージには双方の値と項目名を載せる（要件 1.4 と同じ方針）。
    """
    _require_positive_finite(bearing_area_mm2, "bearing_area_mm2")
    if bearing_area_mm2 < joint.min_bearing_area_mm2:
        shortfall_mm2 = joint.min_bearing_area_mm2 - bearing_area_mm2
        raise ParameterError(
            f"bearing_area_mm2={bearing_area_mm2!r} は "
            f"min_bearing_area_mm2={joint.min_bearing_area_mm2!r} を下回る"
            f"（不足 {shortfall_mm2}mm^2）。締結の当たり面を広げるか、"
            "締結箇所を増やすこと。⚠️ 位置決めダボの面積は算入できない。"
        )
