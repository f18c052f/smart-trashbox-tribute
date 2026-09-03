"""寸法設定ファイルの読み書きとパラメータ識別子（design.md「Config」/ 要件 1.1,
1.3, 1.6, 1.7, 4.5, 6.6, 6.7）。

`configs/catch_mechanism/dimensions.json` を寸法パラメータの**単一の正**とし、
そこから `MechanismParams` を組み立てる（`load_params`）、同じ形式へ書き戻す
（`dump_params`）、値と出所から識別子を算出する（`parameters_digest`）の3つを
提供する。設計値をコードへ埋め込まないため、採寸値の反映は**設定ファイルの
書き換えだけ**で完結する（要件 1.6）。

**あらゆる階層で未知キーを拒否する。** 最上位・各コンポーネント・出所表の
いずれについても、既知の項目名と厳密に一致しないキーは `ParameterError` で
拒否する（要件 1.3 / design.md「Config」Responsibilities。上流
`src/trajectory_sim/cli.py` の設定読み込みと同じ規律である）。⚠️ 未知キーを
黙って読み飛ばすと、綴りを1文字誤った項目が「設定したつもり」のまま既定値で
動き、設定ファイルが正であるという前提そのものが崩れる。⚠️ **`trajectory_sim`
の実装を import して再利用しない**——依存方向が逆になる（design.md「Params」
Implementation Notes）。一致させるのは規律であって実装ではない。

**項目名の表を手書きしない。** 許容されるキー・値の型は
`params.PARAMETER_PATHS`（データクラス木の走査で生成される）から導く（要件 1.8）。
手書きの表であれば、フィールドを1つ足したときに読み込み側から黙って漏れる。

**欠損は既定値で埋めない。** 設定ファイルは全 32 パスを明示していなければ
ならない。`RetentionParams` の2項目は型の上では既定値を持つが、それは
**設計上の決定**（深さを足さない・底へ加工を行わない）であり、設定ファイルの
上からその決定が消えてよい理由にはならない（要件 1.4 / design.md「Params」
Invariants）。

**出所表は最上位の必須の節である。** 節の中に現れないパスは `ASSUMED` として
扱う——「実測を名乗るには明示が要る」方向に倒す（design.md「Logical Data
Model」）。⚠️ ただし節そのものの欠落は許さない。出所は要件 1.2 が求める
一級の記録であり、節ごと消えたファイルを「すべて仮値」と読み替えるのは、
欠損を黙って埋めることに他ならない。

**識別子（`parameters_digest`）は値と出所の両方から算出する。** 正規化した
表現（パス整列・浮動小数点の固定書式・出所表の補完）の SHA-256 であり、
書式の差——インデント・キーの並び・改行コード・`220` と `220.0` の書き分け・
表に現れない仮値と明示された仮値——では変化しない（design.md「Config」
Postconditions）。⚠️ **値または出所が変われば必ず変化する。** これが
「パラメータを変えたのに形状指標を更新し忘れた」事故を、形状ライブラリを
持たない環境でも捕まえる唯一の手掛かりである（要件 4.5）。出所を識別子から
外すと、仮値から実測への昇格が識別子に現れず、「仮値のときに記録した形状
指標」が実測の名の下でそのまま生き延びる。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import Final, get_type_hints

from catch_mechanism.errors import ParameterError
from catch_mechanism.params import (
    PARAMETER_PATHS,
    MechanismParams,
    ParameterPath,
    Provenance,
)

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_DIMENSIONS_PATH",
    "load_params",
    "dump_params",
    "parameters_digest",
]


SCHEMA_VERSION: Final[str] = "1.0"
"""設定ファイルの記録形式の版（design.md「Logical Data Model」）。

⚠️ **パラメータではない。** `PARAMETER_PATHS` の外にあるメタデータであり、
出所を持たず、`parameters_digest` にも参加しない（識別子が識別するのは
寸法パラメータの値と出所であって、記録形式ではない）。
"""


DEFAULT_DIMENSIONS_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "catch_mechanism" / "dimensions.json"
)
"""寸法パラメータの単一の正の既定パス（design.md「Config」Service Interface）。

`src/catch_mechanism/config.py` から見た `parents[2]` がリポジトリルートである
（`src` レイアウトのため）。設定ファイルはパッケージデータではなく**リポジトリ
の成果物**であり、`configs/trajectory_sim/*.json` と同じ場所に並ぶ。
"""


_DIGEST_ALGORITHM: Final[str] = "sha256"
_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_PROVENANCE_KEY: Final[str] = "provenance"


def _component_types() -> Mapping[str, type]:
    """`MechanismParams` 直下のコンポーネント名からその型への対応を返す。

    `PARAMETER_PATHS` はパス・単位・値の型を持つが、コンポーネントの**型**は
    持たないため、集約の注釈からここで解決する。パス表（項目名と値の型）の正は
    あくまで `PARAMETER_PATHS` 側にあり、本関数はデータクラスを構築するための
    型だけを補う。
    """
    hints = get_type_hints(MechanismParams)
    return {
        field.name: hints[field.name]
        for field in fields(MechanismParams)
        if field.name != _PROVENANCE_KEY
    }


_COMPONENT_TYPES: Final[Mapping[str, type]] = _component_types()


def _component_fields() -> Mapping[str, tuple[str, ...]]:
    """コンポーネント名から、そこに属するリーフフィールド名の並びを返す。

    `PARAMETER_PATHS` の走査結果だけを情報源とする（要件 1.8）。
    """
    grouped: dict[str, list[str]] = {name: [] for name in _COMPONENT_TYPES}
    for spec in PARAMETER_PATHS.values():
        grouped[spec.component].append(spec.field_name)
    return {component: tuple(names) for component, names in grouped.items()}


_COMPONENT_FIELDS: Final[Mapping[str, tuple[str, ...]]] = _component_fields()

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {_SCHEMA_VERSION_KEY, _PROVENANCE_KEY, *_COMPONENT_TYPES}
)

_PROVENANCE_VALUES: Final[Mapping[str, Provenance]] = {
    provenance.value: provenance for provenance in Provenance
}


def _read_document(path: Path) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `ParameterError` へ
    統一する（`trajectory_sim.cli._read_json_file` と同じ扱い）。呼び出し側が
    設定読み込みの失敗を単一の `except` でまとめて扱えるようにするためである。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{path}: 設定ファイルを読み込めない: {exc}") from exc
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
    """未知キーと欠損キーを、いずれも項目名を示して拒否する（要件 1.3）。

    未知キーを先に見るのは、綴り誤り（`heigth_mm`）が「未知キー1件 + 欠損1件」
    として現れるとき、直すべき側を先に示すためである。
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


def _convert_scalar(value: object, spec: ParameterPath, label: str) -> float | int | str:
    """JSON の値を、パス表が定める型へ変換する（型違いは項目名つきで拒否）。

    ⚠️ **`bool` を数値として通さない。** Python の `bool` は `int` の派生である
    ため、明示的に除かなければ JSON の `true` が「締結座 1 箇所」「高さ 1mm」
    として黙って通る。

    整数は浮動小数点の項目へ受け入れて `float` へ広げる（`220` と `220.0` は
    同じ値である）。逆に、個数の項目へ小数を受け入れることはしない——`6.5` 個の
    締結座は書き間違いであって、丸めてよい値ではない。
    """
    expected = spec.value_type
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ParameterError(
                f"{label}: {spec.path}={value!r} は数値でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return float(value)
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ParameterError(
                f"{label}: {spec.path}={value!r} は整数でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
    if expected is str:
        if not isinstance(value, str):
            raise ParameterError(
                f"{label}: {spec.path}={value!r} は文字列でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
    raise ParameterError(  # pragma: no cover - 構造上の防御
        f"{label}: {spec.path} の値の型 {expected!r} は設定ファイルで表現できない。"
    )


def _build_component(component: str, data: object, label: str) -> object:
    """1コンポーネント分の JSON オブジェクトからデータクラスを構築する。"""
    values = _require_object(data, f"{label}: {component}")
    allowed = frozenset(_COMPONENT_FIELDS[component])
    _reject_unknown_and_missing(values, allowed, f"{label}: {component}")
    kwargs = {
        field_name: _convert_scalar(
            values[field_name], PARAMETER_PATHS[f"{component}.{field_name}"], label
        )
        for field_name in _COMPONENT_FIELDS[component]
    }
    component_type = _COMPONENT_TYPES[component]
    try:
        return component_type(**kwargs)
    except ParameterError as exc:
        # 構築時検証（値域・大小関係・許可材料）の違反。項目名と値は例外側の
        # メッセージが持っているため、どのファイルの話かだけを補って再送する。
        raise ParameterError(f"{label}: {exc}") from exc


def _build_provenance(data: object, label: str) -> dict[str, Provenance]:
    """出所表を構築する。キーがパラメータパス表と一致しない場合は拒否する。

    表に現れないパスは補完しない——`MechanismParams.provenance` は「明示された
    出所」を保持し、現れないパスを `ASSUMED` として扱うのは読み手側の規約で
    ある（design.md「Logical Data Model」）。補完してしまうと、書き出しの
    たびに出所表が全パスへ膨らみ、実測がどれかを人が読み取れなくなる。
    """
    table = _require_object(data, f"{label}: {_PROVENANCE_KEY}")
    provenance: dict[str, Provenance] = {}
    for key, value in table.items():
        if key not in PARAMETER_PATHS:
            raise ParameterError(
                f"{label}: {_PROVENANCE_KEY} のキー {key!r} は"
                "パラメータパス表に存在しない"
                "（例: 'trash_can.opening_inner_diameter_mm'）。"
            )
        if not isinstance(value, str) or value not in _PROVENANCE_VALUES:
            allowed = sorted(_PROVENANCE_VALUES)
            raise ParameterError(
                f"{label}: {_PROVENANCE_KEY}[{key!r}]={value!r} は"
                f"出所として認められない（指定できるのは {allowed!r} のみ）。"
            )
        provenance[key] = _PROVENANCE_VALUES[value]
    return provenance


def load_params(path: Path | None = None) -> MechanismParams:
    """寸法設定ファイルを読み込み、検証済みの `MechanismParams` を返す。

    Args:
        path: 読み込む設定ファイル。省略時は `DEFAULT_DIMENSIONS_PATH`。

    Returns:
        構築時検証を通った `MechanismParams`。以降の層で再検証を要さない
        （design.md「Params」Postconditions）。

    Raises:
        ParameterError: ファイルを読めない場合、JSON として解析できない場合、
            いずれかの階層に未知キーがある場合、必須キーが欠けている場合、
            値の型が違う場合、値が範囲外・不変条件違反である場合、記録形式の
            版が未対応の場合、または出所表のキー・値が不正な場合。
    """
    target = DEFAULT_DIMENSIONS_PATH if path is None else path
    label = str(target)

    document = _require_object(_read_document(target), label)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)

    version = document[_SCHEMA_VERSION_KEY]
    if version != SCHEMA_VERSION:
        raise ParameterError(
            f"{label}: {_SCHEMA_VERSION_KEY}={version!r} は未対応である"
            f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
        )

    components = {
        component: _build_component(component, document[component], label)
        for component in _COMPONENT_TYPES
    }
    provenance = _build_provenance(document[_PROVENANCE_KEY], label)

    try:
        return MechanismParams(**components, provenance=provenance)
    except ParameterError as exc:  # pragma: no cover - 出所の検証は上で済んでいる
        raise ParameterError(f"{label}: {exc}") from exc


def _to_document(params: MechanismParams) -> dict[str, object]:
    """`params` を設定ファイルの形（JSON へ書ける素の値）へ写す。"""
    document: dict[str, object] = {_SCHEMA_VERSION_KEY: SCHEMA_VERSION}
    for component, field_names in _COMPONENT_FIELDS.items():
        instance = getattr(params, component)
        document[component] = {name: getattr(instance, name) for name in field_names}
    document[_PROVENANCE_KEY] = {
        path: provenance.value for path, provenance in params.provenance.items()
    }
    return document


def dump_params(params: MechanismParams, path: Path) -> None:
    """`params` を設定ファイルの形式で `path` へ書き出す（要件 1.7）。

    整形は**インデント2・キー整列・末尾改行**に固定する（design.md「Config」
    Responsibilities）。並びを入力の順に委ねると、同じ値を書き戻しただけで
    行が入れ替わり、変更が行単位の差分として読めなくなる。

    ⚠️ **改行は LF に固定する。** `.gitattributes` は `*.json` を作業ツリーで
    CRLF に固定するが、git はコミット時に LF へ正規化するため差分は生じない。
    一方、`os.linesep` に委ねると Windows と WSL で同じコマンドが別のバイト列を
    生み、差分が実行環境で揺れる（本リポジトリの Python は WSL 側にある）。

    書き出しの失敗（`OSError`）は包まずにそのまま送出する。読み込み側と違い、
    出力先の不備は設定の内容の不正ではない。

    Args:
        params: 書き出す寸法パラメータ。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(params),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _canonical_scalar(value: float | int | str, spec: ParameterPath) -> str:
    """1つの値を、書式に依らない正規表現（文字列）へ写す。

    浮動小数点は `repr` の最短往復表現を用いる（`220` / `220.0` / `2.2e2` は
    いずれも同じ `float` であり、同じ表現になる）。⚠️ `-0.0` は `0.0` へ畳む
    ——値としては等しいのに `repr` が異なり、識別子だけが動いてしまうためである。
    """
    if spec.value_type is float:
        number = float(value)
        if number == 0.0:
            number = 0.0
        return repr(number)
    if spec.value_type is int:
        return repr(int(value))
    return str(value)


def _canonical_payload(params: MechanismParams) -> str:
    """識別子の算出対象となる正規化表現を組み立てる。

    - 値は全 32 パスを**パス文字列で平坦化**して並べる。コンポーネントの
      入れ子や JSON の整形は識別の対象ではない
    - 出所は**全パスへ補完**する。表に現れないパスは `ASSUMED` であり
      （design.md「Logical Data Model」）、明示された `assumed` と同じ状態で
      なければならない——意味の変わらない書き足しで識別子が動けば、形状指標の
      記録が理由なく無効になる
    - `schema_version` は含めない（記録形式はパラメータではない）
    """
    values = {
        path: _canonical_scalar(getattr(getattr(params, spec.component), spec.field_name), spec)
        for path, spec in PARAMETER_PATHS.items()
    }
    provenance = {
        path: params.provenance.get(path, Provenance.ASSUMED).value for path in PARAMETER_PATHS
    }
    return json.dumps(
        {"values": values, _PROVENANCE_KEY: provenance},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parameters_digest(params: MechanismParams) -> str:
    """寸法パラメータの識別子を `"sha256:<hex>"` の形で返す（要件 4.5）。

    同じ値・同じ出所であれば、設定ファイルの書式に依らず同一の文字列になる
    （design.md「Config」Postconditions）。逆に、値または出所が1つでも変われば
    変化する。⚠️ 形状指標の記録（`geometry-baseline.json`、タスク 4.2）は
    この識別子を併せて保持し、**形状ライブラリを持たない環境でも**「パラメータ
    を変えたのに指標を更新し忘れた」状態を検出する。

    Args:
        params: 識別子を算出する寸法パラメータ。

    Returns:
        `"sha256:"` に続く 64 桁の16進文字列。
    """
    payload = _canonical_payload(params).encode("utf-8")
    return f"{_DIGEST_ALGORITHM}:{hashlib.sha256(payload).hexdigest()}"
