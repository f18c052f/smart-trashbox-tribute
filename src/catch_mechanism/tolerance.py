"""位置許容誤差の導出と導出記録の直列化（design.md「Tolerance」/ 要件 7.1, 7.2,
7.3, 7.4, 7.9）。

位置許容誤差を「**開口内半径 − 対象物の代表寸法の半分**」として、
プロジェクト内の**唯一の箇所**で導出する（要件 7.1）。式は `_apply_formula` に
1つだけ置き、`derive_position_tolerance` も記録の読み戻しもそこを通る。
⚠️ **シミュレータ側へは値だけが渡る**（design.md「開口径 → 位置許容誤差 →
シミュレータ設定」）。`trajectory_sim` の設定ファイルには数と出所しか現れず、
式はここにしか存在しない——同じ式が2箇所にあれば、片方だけが直った状態を
誰も検出できない。

**外向きに張り出す部分の寸法を算入しない**（要件 7.2）。⚠️ 受け口のフランジ幅・
傾斜角・取り付け部の隙間と肉厚は、どれ1つとして導出に参加しない。
「入った」と「キャッチできた」は別問題であり、許容誤差は**保持まで成立する
内径**——すなわちゴミ箱本体の開口内径——で決まる。フランジを広げるほど許容誤差が
増える実装は、缶が受け口をかすめて外へ落ちた試行まで成功として数えてしまう。
（受け口が本体の開口内径を狭めないことは要件 8.2 が別途要求しており、
`fit_clearance_mm` / `wall_thickness_mm` は本体の**外側**に被さる寸法である。）

**出所は入力の最弱を継承する**（要件 7.4 / `Provenance.weakest`）。開口内径と
対象物径の双方が実測であるときに**限り**実測を名乗る。⚠️ **未実測の値から
実測を名乗らない。** 出所表に現れないパスは `ASSUMED` として扱う——
`config.parameters_digest` と同じ「実測を名乗るには明示が要る」規約であり、
本モジュールはその規約を**再実装せず** `_provenance_of` で同じ形に引く。

**記録は導出の写しであり、独立に編集してよい自由記述ではない。** 値・入力・
出所・式・前提の整合は `ToleranceDerivation.__post_init__` が持ち、
`load_derivation` はその構築を通す。したがって「入力と食い違う値」「仮値の入力
から実測を名乗る出所」「書き換えられた式」「前提の落ちた記録」は、記録として
存在できない。⚠️ ここを素通しにすると、唯一の導出箇所を持つ意味が失われる
——記録の側が別の値を主張できるなら、導出は2箇所にあるのと変わらない。

読み込みの規律は `config.py` に揃える（**あらゆる階層で未知キーを拒否する**、
項目名を示す、欠損を既定値で埋めない、LF・インデント2・キー整列・末尾改行で
書き出す）。記録形式の版は `config.SCHEMA_VERSION` を共有する（tasks.md
「Implementation Notes」タスク 1.4(b)。⚠️ 独立に版を刻む必要が出たら、その時点で
この単一定数を分割すること）。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import ParameterError
from catch_mechanism.params import PARAMETER_PATHS, MechanismParams, Provenance

__all__ = [
    "DEFAULT_DERIVATION_PATH",
    "OPENING_DIAMETER_PATH",
    "OBJECT_DIAMETER_PATH",
    "REQUIRED_INPUT_NAMES",
    "FORMULA",
    "ASSUMPTIONS",
    "ToleranceInput",
    "ToleranceDerivation",
    "derive_position_tolerance",
    "dump_derivation",
    "load_derivation",
]


DEFAULT_DERIVATION_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "catch_mechanism" / "catch-opening.json"
)
"""導出記録の既定パス（design.md「Data Models」`configs/catch_mechanism/catch-opening.json`）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。⚠️ design.md の Service Interface は `dump_derivation` /
`load_derivation` の `path` を**必須引数**としているため、この定数は既定値では
なく「出荷される記録がどこにあるか」の宣言である。
"""

OPENING_DIAMETER_PATH: Final[str] = "trash_can.opening_inner_diameter_mm"
"""導出の第1入力。⚠️ **保持まで成立する内径**であり、外向きの張り出しを含まない。"""

OBJECT_DIAMETER_PATH: Final[str] = "target_object.diameter_mm"
"""導出の第2入力。M1 の実験条件である空き缶の代表直径（要件 7.9）。"""

REQUIRED_INPUT_NAMES: Final[tuple[str, ...]] = (OPENING_DIAMETER_PATH, OBJECT_DIAMETER_PATH)
"""導出記録が持たなければならない入力の名と並び（design.md「Tolerance」Postconditions）。

⚠️ **過不足のいずれも許さない。** 欠ければ「何から導いたのか」が記録から失われ、
余れば——たとえば `rim.flange_width_mm` を書き足せば——式に現れない量が根拠で
あるかのように読めてしまう（要件 7.2）。
"""

FORMULA: Final[str] = f"{OPENING_DIAMETER_PATH} / 2 - {OBJECT_DIAMETER_PATH} / 2"
"""導出式の文字列表現（要件 7.3）。

入力名（`PARAMETER_PATHS` のパス）だけで書き、演算子は ASCII に揃える
（design.md 本文は数式として `−` U+2212 を用いるが、記録は grep と突き合わせの
対象であるため見た目の異なる同義字を持ち込まない）。
"""

ASSUMPTIONS: Final[tuple[str, ...]] = (
    "対象物は M1 の実験条件である空き缶（350ml 缶相当）であり、"
    f"その代表寸法として {OBJECT_DIAMETER_PATH} を用いる"
    "（要件 7.9。対象物の一般化は OQ-02 として未決である）。",
    "外向きに張り出す部分（受け口のフランジ幅・傾斜角・取り付け部の隙間と肉厚）の"
    "寸法を算入しない。許容誤差は保持まで成立する開口内径だけで決まる（要件 7.2）。",
    "導出値は目標中心からの水平方向のずれの上限（半径方向）であり、"
    "シミュレータ設定の catch.position_tolerance_mm と同じ意味を持つ。",
)
"""導出に伴う前提（要件 7.2, 7.9 / design.md「Data Models」の `assumptions`）。

記録はこの3件を**必ず含む**。前提の落ちた記録を受け付ければ、「どういう条件下で
成り立つ値なのか」を持たない数だけが下流へ流れる。
"""


_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_TOLERANCE_KEY: Final[str] = "position_tolerance_mm"
_PROVENANCE_KEY: Final[str] = "provenance"
_INPUTS_KEY: Final[str] = "inputs"
_FORMULA_KEY: Final[str] = "formula"
_ASSUMPTIONS_KEY: Final[str] = "assumptions"
_NAME_KEY: Final[str] = "name"
_VALUE_KEY: Final[str] = "value_mm"

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        _SCHEMA_VERSION_KEY,
        _TOLERANCE_KEY,
        _PROVENANCE_KEY,
        _INPUTS_KEY,
        _FORMULA_KEY,
        _ASSUMPTIONS_KEY,
    }
)
_INPUT_KEYS: Final[frozenset[str]] = frozenset({_NAME_KEY, _VALUE_KEY, _PROVENANCE_KEY})

_PROVENANCE_VALUES: Final[Mapping[str, Provenance]] = {
    provenance.value: provenance for provenance in Provenance
}


def _apply_formula(opening_inner_diameter_mm: float, object_diameter_mm: float) -> float:
    """位置許容誤差を導出する**唯一の式**（要件 7.1）。

    ⚠️ **この関数の外で同じ計算を書かないこと。** 導出も記録の検証も必ずここを
    通す。⚠️ 引数は開口の**内径**と対象物の**代表直径**であり、外向きに張り出す
    部分の寸法は引数に取らない——算入し忘れではなく、算入しないことが要件 7.2
    である。
    """
    return opening_inner_diameter_mm / 2.0 - object_diameter_mm / 2.0


def _provenance_of(params: MechanismParams, path: str) -> Provenance:
    """`path` の実効的な出所を返す（表に現れないパスは仮値）。

    `MechanismParams.provenance` は「**明示された**出所」だけを持つ表であり、
    現れないパスを `ASSUMED` として読むのは読み手側の規約である（design.md
    「Logical Data Model」/「実測を名乗るには明示が要る」）。
    `config._canonical_payload` が識別子の算出で用いる規約と同一のものであり、
    ここで別の既定（例えば `MEASURED`）を採れば、同じ設定ファイルから
    「識別子の上では仮値、許容誤差の上では実測」という食い違いが生まれる。
    """
    return params.provenance.get(path, Provenance.ASSUMED)


def _require_finite(value: float, name: str) -> None:
    """`value` が有限な数（`bool` を除く）であることを検証する。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(
            f"{name}={value!r} は数値でなければならない（{type(value).__name__} だった）。"
        )
    if not math.isfinite(value):
        raise ParameterError(f"{name}={value!r} は有限でなければならない。")


def _require_positive_finite(value: float, name: str) -> None:
    """`value` が正の有限値であることを検証する。"""
    _require_finite(value, name)
    if value <= 0.0:
        raise ParameterError(f"{name}={value!r} は正でなければならない。")


def _require_nonempty_str(value: str, name: str) -> None:
    """`value` が空でない文字列であることを検証する。"""
    if not isinstance(value, str) or not value.strip():
        raise ParameterError(f"{name}={value!r} は空でない文字列でなければならない。")


@dataclass(frozen=True, slots=True)
class ToleranceInput:
    """導出に用いた入力1件（値と出所の併記。要件 7.3）。

    Attributes:
        name: 入力の名。`PARAMETER_PATHS` のパス文字列であり、寸法設定ファイル
            （単一の正）の該当項目へそのまま辿れる。⚠️ 自由な表示名を許すと、
            記録から元の項目へ戻れなくなる。
        value_mm: 導出時の値（mm）。
        provenance: その値の出所（実測 / 仮値）。

    Raises:
        ParameterError: 名が既知のパラメータパスでない場合、値が正の有限値で
            ない場合、または出所が `Provenance` でない場合。
    """

    name: str
    value_mm: float
    provenance: Provenance

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は項目名と値を添えて拒否する。"""
        _require_nonempty_str(self.name, _NAME_KEY)
        if self.name not in PARAMETER_PATHS:
            raise ParameterError(
                f"{_NAME_KEY}={self.name!r} は PARAMETER_PATHS のパス文字列と一致しない"
                f"（例: {OPENING_DIAMETER_PATH!r}）。"
            )
        _require_positive_finite(self.value_mm, _VALUE_KEY)
        if not isinstance(self.provenance, Provenance):
            raise ParameterError(
                f"{_PROVENANCE_KEY}={self.provenance!r} は Provenance でなければならない"
                f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
            )


@dataclass(frozen=True, slots=True)
class ToleranceDerivation:
    """位置許容誤差の導出結果（要件 7.1, 7.3, 7.4, 7.9）。

    ⚠️ **不整合な導出結果は構築できない。** 値・入力・出所・式・前提の整合を
    `__post_init__` が検査するため、`derive_position_tolerance` が作ったものも
    `load_derivation` が読み戻したものも、同じ不変条件を満たす。

    Attributes:
        position_tolerance_mm: 導出した位置許容誤差（mm）。正である
            （design.md「Tolerance」Postconditions）。
        provenance: 導出値の出所。入力の最弱を継承する（要件 7.4）。
        inputs: 導出に用いた入力の並び。開口内径・対象物径の2件ちょうど
            （design.md「Tolerance」Postconditions）。
        formula: 導出式の文字列表現（`FORMULA`）。
        assumptions: 前提。`ASSUMPTIONS` の各件を必ず含む（要件 7.2, 7.9）。

    Raises:
        ParameterError: いずれかの不変条件に反する場合。
    """

    position_tolerance_mm: float
    provenance: Provenance
    inputs: tuple[ToleranceInput, ...]
    formula: str
    assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は項目名と双方の値を添えて拒否する。"""
        _require_positive_finite(self.position_tolerance_mm, _TOLERANCE_KEY)
        if not isinstance(self.provenance, Provenance):
            raise ParameterError(
                f"{_PROVENANCE_KEY}={self.provenance!r} は Provenance でなければならない"
                f"（指定できるのは {Provenance.MEASURED!r} と {Provenance.ASSUMED!r} のみ）。"
            )
        if not isinstance(self.inputs, tuple) or not all(
            isinstance(item, ToleranceInput) for item in self.inputs
        ):
            raise ParameterError(
                f"{_INPUTS_KEY}={self.inputs!r} は ToleranceInput の組でなければならない。"
            )

        names = tuple(item.name for item in self.inputs)
        if names != REQUIRED_INPUT_NAMES:
            raise ParameterError(
                f"{_INPUTS_KEY}: 入力は {list(REQUIRED_INPUT_NAMES)!r} の2件ちょうどで"
                f"なければならない（{list(names)!r} だった）。"
                "式に現れない量を根拠として書き足さない。"
            )

        if self.formula != FORMULA:
            raise ParameterError(
                f"{_FORMULA_KEY}={self.formula!r} は導出式 {FORMULA!r} と一致しない"
                "（導出はプロジェクト内の唯一の箇所で行う）。"
            )

        opening_mm, object_mm = (item.value_mm for item in self.inputs)
        expected = _apply_formula(opening_mm, object_mm)
        if self.position_tolerance_mm != expected:
            raise ParameterError(
                f"{_TOLERANCE_KEY}={self.position_tolerance_mm!r} は記録された入力から"
                f"導かれる値 {expected!r} と一致しない"
                f"（{FORMULA} に {opening_mm!r} と {object_mm!r} を与えた結果）。"
            )

        weakest = Provenance.weakest(*(item.provenance for item in self.inputs))
        if self.provenance is not weakest:
            raise ParameterError(
                f"{_PROVENANCE_KEY}={self.provenance.value!r} は入力の最弱の出所 "
                f"{weakest.value!r} と一致しない（未実測の値から実測を名乗らない）。"
            )

        if not isinstance(self.assumptions, tuple):
            raise ParameterError(
                f"{_ASSUMPTIONS_KEY}={self.assumptions!r} は文字列の組でなければならない。"
            )
        for index, assumption in enumerate(self.assumptions):
            _require_nonempty_str(assumption, f"{_ASSUMPTIONS_KEY}[{index}]")
        missing = [item for item in ASSUMPTIONS if item not in self.assumptions]
        if missing:
            raise ParameterError(
                f"{_ASSUMPTIONS_KEY}: 必須の前提が記録されていない {missing!r}"
                "（前提の落ちた値だけが下流へ流れることを許さない）。"
            )


def derive_position_tolerance(params: MechanismParams) -> ToleranceDerivation:
    """寸法パラメータから位置許容誤差を導出する（要件 7.1, 7.2, 7.3, 7.4, 7.9）。

    ⚠️ **本 Spec における唯一の導出箇所である。** 下流（シミュレータ設定）へ渡る
    のは値と出所だけであり、式はここにしか無い。

    Args:
        params: 寸法パラメータ。開口内径・対象物の代表直径・出所表のみを読む。
            ⚠️ 受け口（`rim`）の寸法は**一切参照しない**（要件 7.2）。

    Returns:
        値・出所・入力・式・前提を併記した `ToleranceDerivation`。

    Raises:
        ParameterError: 開口内径が対象物の代表寸法以下である場合（design.md
            「Tolerance」Preconditions）。対象物が入る余地が無ければ許容誤差は
            定まらず、0 以下の値を返せば下流はそれを「厳しい合否条件」として
            受け取ってしまう。
    """
    opening_mm = params.trash_can.opening_inner_diameter_mm
    object_mm = params.target_object.diameter_mm
    if opening_mm <= object_mm:
        raise ParameterError(
            f"{OPENING_DIAMETER_PATH}={opening_mm!r} は "
            f"{OBJECT_DIAMETER_PATH}={object_mm!r} より大きくなければならない"
            "（対象物が開口へ入る余地が無ければ位置許容誤差は定まらない）。"
        )

    inputs = (
        ToleranceInput(
            name=OPENING_DIAMETER_PATH,
            value_mm=opening_mm,
            provenance=_provenance_of(params, OPENING_DIAMETER_PATH),
        ),
        ToleranceInput(
            name=OBJECT_DIAMETER_PATH,
            value_mm=object_mm,
            provenance=_provenance_of(params, OBJECT_DIAMETER_PATH),
        ),
    )
    return ToleranceDerivation(
        position_tolerance_mm=_apply_formula(opening_mm, object_mm),
        provenance=Provenance.weakest(*(item.provenance for item in inputs)),
        inputs=inputs,
        formula=FORMULA,
        assumptions=ASSUMPTIONS,
    )


def _to_document(derivation: ToleranceDerivation) -> dict[str, object]:
    """`derivation` を記録ファイルの形（JSON へ書ける素の値）へ写す。"""
    return {
        _SCHEMA_VERSION_KEY: SCHEMA_VERSION,
        _TOLERANCE_KEY: derivation.position_tolerance_mm,
        _PROVENANCE_KEY: derivation.provenance.value,
        _INPUTS_KEY: [
            {
                _NAME_KEY: item.name,
                _VALUE_KEY: item.value_mm,
                _PROVENANCE_KEY: item.provenance.value,
            }
            for item in derivation.inputs
        ],
        _FORMULA_KEY: derivation.formula,
        _ASSUMPTIONS_KEY: list(derivation.assumptions),
    }


def dump_derivation(derivation: ToleranceDerivation, path: Path) -> None:
    """導出記録を `path` へ書き出す（要件 7.3, 7.9）。

    整形は `config.dump_params` に揃える——**インデント2・キー整列・末尾改行**、
    そして**改行は LF に固定**する。`.gitattributes` が
    `configs/catch_mechanism/*.json` を `text eol=lf` に倒しているため、
    本関数が書くバイト列は git がチェックアウトする内容と同一であり、値が
    変わっていなければ `git status` は変更を報告しない（tasks.md
    「Implementation Notes」タスク 1.5）。

    ⚠️ `inputs` は**配列であり、並びは式の順**（開口内径 → 対象物径）である。
    キー整列は各オブジェクトの内側にしか効かないため、この並びは書き出し側の
    責任である。

    書き出しの失敗（`OSError`）は包まずにそのまま送出する（`dump_params` と
    同じ扱い。出力先の不備は記録内容の不正ではない）。

    Args:
        derivation: 書き出す導出結果。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(derivation),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _read_document(path: Path) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `ParameterError` へ統一
    する（`config._read_document` と同形）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{path}: 導出記録を読み込めない: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{path}: JSON として解析できない: {exc}") from exc


def _require_object(value: object, label: str) -> Mapping[str, object]:
    """`value` が JSON オブジェクトであることを要求する。"""
    if not isinstance(value, dict):
        raise ParameterError(
            f"{label}: オブジェクト（{{...}}）を期待したが {type(value).__name__} だった。"
        )
    return value


def _reject_unknown_and_missing(
    data: Mapping[str, object], allowed: frozenset[str], label: str
) -> None:
    """未知キーと欠損キーを、いずれも項目名を示して拒否する（`config` と同じ規律）。

    未知キーを先に見るのは、綴り誤り（`positon_tolerance_mm`）が「未知キー1件 +
    欠損1件」として現れるとき、直すべき側を先に示すためである。
    """
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


def _to_provenance(value: object, label: str) -> Provenance:
    """出所の文字列を `Provenance` へ写す（未知の語は拒否する）。"""
    if not isinstance(value, str) or value not in _PROVENANCE_VALUES:
        raise ParameterError(
            f"{label}: {_PROVENANCE_KEY}={value!r} は出所として認められない"
            f"（指定できるのは {sorted(_PROVENANCE_VALUES)!r} のみ）。"
        )
    return _PROVENANCE_VALUES[value]


def _build_input(entry: object, index: int, label: str) -> ToleranceInput:
    """入力1件分の JSON オブジェクトから `ToleranceInput` を構築する。"""
    where = f"{label}: {_INPUTS_KEY}[{index}]"
    values = _require_object(entry, where)
    _reject_unknown_and_missing(values, _INPUT_KEYS, where)

    name = values[_NAME_KEY]
    if not isinstance(name, str):
        raise ParameterError(
            f"{where}: {_NAME_KEY}={name!r} は文字列でなければならない"
            f"（{type(name).__name__} だった）。"
        )
    value = values[_VALUE_KEY]
    _require_finite(value, f"{where}: {_VALUE_KEY}")
    provenance = _to_provenance(values[_PROVENANCE_KEY], where)

    try:
        return ToleranceInput(name=name, value_mm=float(value), provenance=provenance)
    except ParameterError as exc:
        # 構築時検証（既知のパス・正の有限値）の違反。項目名と値は例外側が
        # 持っているため、どのファイルのどの要素かだけを補って再送する。
        raise ParameterError(f"{where}: {exc}") from exc


def load_derivation(path: Path) -> ToleranceDerivation:
    """導出記録を読み戻し、検証済みの `ToleranceDerivation` を返す（要件 7.3, 7.4, 7.9）。

    ⚠️ **読み戻しは書式の復元ではなく、記録の検査である。** 値が記録された入力
    から導けること、出所が入力の最弱であること、式が実装の式であること、必須の
    前提が揃っていることを、そのつど `ToleranceDerivation` の構築を通して確かめる
    （本モジュール docstring「記録は導出の写しであり、独立に編集してよい自由
    記述ではない」）。

    Args:
        path: 読み込む導出記録。

    Returns:
        構築時検証を通った `ToleranceDerivation`。

    Raises:
        ParameterError: ファイルを読めない場合、JSON として解析できない場合、
            いずれかの階層に未知キー・欠損キーがある場合、値の型が違う場合、
            記録形式の版が未対応の場合、または記録の内容が導出として整合しない
            場合（値・入力・出所・式・前提のいずれか）。
    """
    label = str(path)

    document = _require_object(_read_document(path), label)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)

    version = document[_SCHEMA_VERSION_KEY]
    if version != SCHEMA_VERSION:
        raise ParameterError(
            f"{label}: {_SCHEMA_VERSION_KEY}={version!r} は未対応である"
            f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
        )

    tolerance_mm = document[_TOLERANCE_KEY]
    _require_finite(tolerance_mm, f"{label}: {_TOLERANCE_KEY}")
    provenance = _to_provenance(document[_PROVENANCE_KEY], label)

    formula = document[_FORMULA_KEY]
    if not isinstance(formula, str):
        raise ParameterError(
            f"{label}: {_FORMULA_KEY}={formula!r} は文字列でなければならない"
            f"（{type(formula).__name__} だった）。"
        )

    entries = document[_INPUTS_KEY]
    if not isinstance(entries, list):
        raise ParameterError(
            f"{label}: {_INPUTS_KEY} は配列（[...]）でなければならない"
            f"（{type(entries).__name__} だった）。"
        )
    inputs = tuple(_build_input(entry, index, label) for index, entry in enumerate(entries))

    assumptions = document[_ASSUMPTIONS_KEY]
    if not isinstance(assumptions, list):
        raise ParameterError(
            f"{label}: {_ASSUMPTIONS_KEY} は配列（[...]）でなければならない"
            f"（{type(assumptions).__name__} だった）。"
        )

    try:
        return ToleranceDerivation(
            position_tolerance_mm=float(tolerance_mm),
            provenance=provenance,
            inputs=inputs,
            formula=formula,
            assumptions=tuple(assumptions),
        )
    except ParameterError as exc:
        raise ParameterError(f"{label}: {exc}") from exc
