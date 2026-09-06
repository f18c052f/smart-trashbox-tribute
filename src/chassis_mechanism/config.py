"""寸法設定ファイルの読み書き・上流の取り込み・パラメータ識別子
（design.md `#### Config` / 要件 1.1, 1.3, 1.4, 1.10, 2.9, 6.3, 6.4）。

`configs/chassis_mechanism/dimensions.json` を**本 Spec 固有の**寸法の単一の正と
し、そこから `ChassisParams` を組み立て、上流 `catch_mechanism` の公開 API から
造形制約・継手方針・ゴミ箱の採寸値を取り込んで `ResolvedParams` を返す
（`load_params`）。同じ形式へ書き戻し（`dump_params`）、値と出所から識別子を
算出し（`parameters_digest`）、上流の仮値を実測へ更新する（
`update_upstream_measurement`）。設計値をコードへ埋め込まないため、採寸値の反映は
**設定ファイルの書き換えだけ**で完結する（要件 1.5）。

⚠️ **上流が持つ値を本 Spec のファイルへ複製しない**（要件 1.3）。造形可能寸法・
許可材料・継手方針・ゴミ箱の採寸値は上流の `load_params()` から**実行時に**
取り込むものであり、`dimensions.json` のどの階層にも現れない。同じ値が2箇所に
あれば、いつか食い違う。この禁止は `params.PARAMETER_PATHS` に上流の
コンポーネント名が現れないことと対で成立している。

**あらゆる階層で未知キーを拒否する。** 最上位・各コンポーネント・出所表の
いずれについても、既知の項目名と厳密に一致しないキーは
`chassis_mechanism.errors.ParameterError` で拒否する（要件 1.4 / design.md
`#### Config` Responsibilities。上流 `src/catch_mechanism/config.py` と同じ規律で
ある）。⚠️ 未知キーを黙って読み飛ばすと、綴りを1文字誤った項目が「設定した
つもり」のまま既定値で動き、設定ファイルが正であるという前提そのものが崩れる。
⚠️ **上流の実装を import して再利用しない**——参照してよいのは公開 API だけで
あり（design.md「Allowed Dependencies」）、一致させるのは規律であって実装では
ない。

**項目名の表を手書きしない。** 許容されるキー・値の型・未決の可否は
`params.PARAMETER_PATHS`（データクラス木の走査で生成される）から導く。手書きの
表であれば、フィールドを1つ足したときに読み込み側から黙って漏れる。

**出所表のキー集合はパラメータパス表と一致しなければならない。** ⚠️ 上流は表に
現れないパスを `ASSUMED` として扱うが、**本 Spec は欠損も拒否する**——
`ChassisParams` 自身が全パスの出所を要求するためである（design.md `#### Params`
Invariants）。出所を持たない寸法値が設計へ流れる形を作らない。

**上流との突き合わせは、この層でしか行えない。** 接合部の当たり面の下限が上流の
`JointPolicy` 以上であることは、上流の設定ファイルを読まなければ判定できないため
`params` の構築時検証には置けない（`params` モジュール docstring）。したがって
`ResolvedParams` を組み立てる**唯一の**私的関数 `_resolve_params` が
`ChassisParams.validate_against_upstream` を呼ぶ。⚠️ **構築箇所を1つに保つこと
自体が検証の一部である**——2箇所目ができた時点で、突き合わせを通らない
`ResolvedParams` を作れてしまう。呼ばれない検証は何も拒否しない。

**識別子（`parameters_digest`）は `ChassisParams` だけの純関数である。** 正規化
した表現（パス整列・浮動小数点の固定書式）の SHA-256 であり、書式の差——
インデント・キーの並び・改行コード・`60` と `60.0` の書き分け——では変化せず、
値または出所が変われば必ず変化する（design.md `#### Config` Postconditions）。
⚠️ **組立後の観測（`measurements.json`、タスク 2.4）を含めない。** 含めれば、
機体を測り直すたびに形状の再生成が要求される（design.md「Logical Data Model」）。
⚠️ **上流の `parameters_digest` は引数型が違うため使えない**（design.md
`#### Config`）。正規化規則だけを上流と同形にする——浮動小数点は `repr` の最短
往復表現、`-0.0` は `0.0` へ畳む、キーは整列、区切りは最小、UTF-8。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Final, get_type_hints

from catch_mechanism import (
    PARAMETER_PATHS as UPSTREAM_PARAMETER_PATHS,
)
from catch_mechanism import (
    JointPolicy,
    PrintingConstraints,
    Provenance,
    TrashCanMeasurements,
)
from catch_mechanism import (
    dump_params as upstream_dump_params,
)
from catch_mechanism import (
    load_params as upstream_load_params,
)

from chassis_mechanism.errors import ParameterError
from chassis_mechanism.params import PARAMETER_PATHS, ChassisParams, ParameterPath

__all__ = [
    "SCHEMA_VERSION",
    "DEFAULT_DIMENSIONS_PATH",
    "UPSTREAM_DIMENSIONS_PATH",
    "ResolvedParams",
    "load_params",
    "dump_params",
    "parameters_digest",
    "update_upstream_measurement",
]


SCHEMA_VERSION: Final[str] = "1.0"
"""設定ファイルの記録形式の版（design.md `#### Config` Service Interface）。

⚠️ **パラメータではない。** `PARAMETER_PATHS` の外にあるメタデータであり、
出所を持たず、`parameters_digest` にも参加しない（識別子が識別するのは寸法
パラメータの値と出所であって、記録形式ではない）。
"""


_REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

DEFAULT_DIMENSIONS_PATH: Final[Path] = (
    _REPOSITORY_ROOT / "configs" / "chassis_mechanism" / "dimensions.json"
)
"""本 Spec 固有の寸法の単一の正の既定パス（design.md「Directory Structure」）。

`src/chassis_mechanism/config.py` から見た `parents[2]` がリポジトリルートで
ある（`src` レイアウトのため）。設定ファイルはパッケージデータではなく
**リポジトリの成果物**であり、`configs/catch_mechanism/*.json` と同じ場所に並ぶ。
"""

UPSTREAM_DIMENSIONS_PATH: Final[Path] = (
    _REPOSITORY_ROOT / "configs" / "catch_mechanism" / "dimensions.json"
)
"""上流の寸法設定ファイルのパス（`update_upstream_measurement` の既定の書き戻し先）。

⚠️ **上流の値ではなく、上流のファイルの所在である**（要件 1.3 が禁じるのは値の
複製である）。上流はこのパスを公開契約に載せていない——`catch_mechanism.__all__`
に `DEFAULT_DIMENSIONS_PATH` は無い——ため、内部モジュールへ手を伸ばす代わりに
本 Spec 側で持つ。design.md「Modified Files」がこのファイルを本 Spec の書き戻し
先として名指ししており、所在の記録はそれと対になる。
"""


_DIGEST_ALGORITHM: Final[str] = "sha256"
_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_PROVENANCE_KEY: Final[str] = "provenance"


def _component_types() -> Mapping[str, type]:
    """`ChassisParams` 直下のコンポーネント名からその型への対応を返す。

    `PARAMETER_PATHS` はパス・単位・値の型を持つが、コンポーネントの**型**は
    持たないため、集約の注釈からここで解決する。項目名と値の型の正はあくまで
    `PARAMETER_PATHS` 側にあり、本関数はデータクラスを構築するための型だけを
    補う。
    """
    hints = get_type_hints(ChassisParams)
    return {
        field.name: hints[field.name]
        for field in fields(ChassisParams)
        if field.name != _PROVENANCE_KEY
    }


_COMPONENT_TYPES: Final[Mapping[str, type]] = _component_types()


def _component_fields() -> Mapping[str, tuple[str, ...]]:
    """コンポーネント名から、そこに属するリーフフィールド名の並びを返す。

    `PARAMETER_PATHS` の走査結果だけを情報源とする（手書きの表を持たない）。
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


@dataclass(frozen=True, slots=True)
class ResolvedParams:
    """本 Spec の寸法と、上流から取り込んだ値を1つにまとめた読み込み結果。

    design.md `#### Config` Service Interface が定める形である。⚠️ **上流の値を
    保持するのであって、複製するのではない**——`printing` / `joint` /
    `trash_can` は上流の `load_params()` が返した値そのものであり、本 Spec の
    設定ファイルには現れない（要件 1.3）。

    Attributes:
        chassis: 本 Spec 固有の寸法（`dimensions.json` 由来）。
        printing: 上流の造形制約。
        joint: 上流の継手方針。⚠️ `chassis.joint_local` の下限はこの下限以上で
            なければならず、その突き合わせは `_resolve_params` が済ませている。
        trash_can: 上流のゴミ箱の採寸値。
    """

    chassis: ChassisParams
    printing: PrintingConstraints
    joint: JointPolicy
    trash_can: TrashCanMeasurements


def _read_document(path: Path) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `ParameterError` へ
    統一する。呼び出し側が設定読み込みの失敗を単一の `except` でまとめて
    扱えるようにするためである。
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
    """未知キーと欠損キーを、いずれも項目名を示して拒否する（要件 1.4）。

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


def _convert_scalar(
    value: object, spec: ParameterPath, label: str
) -> float | int | str | bool | None:
    """JSON の値を、パス表が定める型へ変換する（型違いは項目名つきで拒否）。

    ⚠️ **`bool` を数値・整数として通さない。** Python の `bool` は `int` の派生
    であるため、明示的に除かなければ JSON の `true` が「輪 1個」「隙間 1mm」と
    して黙って通る。

    整数は浮動小数点の項目へ受け入れて `float` へ広げる（`60` と `60.0` は同じ
    値である）。逆に、個数の項目へ小数を受け入れることはしない——`3.5` 輪の
    機体は書き間違いであって、丸めてよい値ではない。

    `null`（未決）を許すのは `spec.optional` が真の項目——電源系だけ——である。
    ⚠️ 寸法に「未決」は無い。もっともらしい既定値で埋めれば、決めていないことが
    決めたこととして設定ファイルに残る。
    """
    if value is None:
        if spec.optional:
            return None
        raise ParameterError(
            f"{label}: {spec.path}=null は許されない"
            "（未決を表せるのは電源系の項目だけである）。"
        )
    expected = spec.value_types[0]
    if expected is bool:
        if not isinstance(value, bool):
            raise ParameterError(
                f"{label}: {spec.path}={value!r} は真偽値でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
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
        # 構築時検証（値域・大小関係）の違反。項目名と値は例外側のメッセージが
        # 持っているため、どのファイルの話かだけを補って再送する。
        raise ParameterError(f"{label}: {exc}") from exc


def _build_provenance(data: object, label: str) -> dict[str, Provenance]:
    """出所表を構築する。キー集合がパラメータパス表と一致しない場合は拒否する。

    ⚠️ **未知キーも欠損も拒否する。** 前者は出所が黙って無視される形であり、
    後者は出所を持たない寸法値が設計へ流れる形である（design.md `#### Params`
    Invariants）。
    """
    table = _require_object(data, f"{label}: {_PROVENANCE_KEY}")
    provenance: dict[str, Provenance] = {}
    for key, value in table.items():
        if key not in PARAMETER_PATHS:
            raise ParameterError(
                f"{label}: {_PROVENANCE_KEY} のキー {key!r} は"
                "パラメータパス表に存在しない"
                "（例: 'bracket.mount_face_to_contact_mm'。⚠️ 上流が所有する"
                "パスをここへ書くこともできない）。"
            )
        if not isinstance(value, str) or value not in _PROVENANCE_VALUES:
            allowed = sorted(_PROVENANCE_VALUES)
            raise ParameterError(
                f"{label}: {_PROVENANCE_KEY}[{key!r}]={value!r} は"
                f"出所として認められない（指定できるのは {allowed!r} のみ）。"
            )
        provenance[key] = _PROVENANCE_VALUES[value]
    missing = sorted(set(PARAMETER_PATHS) - set(provenance))
    if missing:
        raise ParameterError(
            f"{label}: {_PROVENANCE_KEY} に出所の無いパラメータがある: "
            + ", ".join(missing)
            + "（要件 1.2: 各寸法値について実測か仮値かを値ごとに保持する）。"
        )
    return provenance


def _build_chassis_params(document: Mapping[str, object], label: str) -> ChassisParams:
    """検証済みの JSON オブジェクトから `ChassisParams` を構築する。

    ⚠️ **`ChassisParams` を構築する唯一の場所である。** 2箇所目ができれば、
    未知キーの拒否や出所の検証を通らない集約を作れてしまう。
    """
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
        return ChassisParams(**components, provenance=provenance)
    except ParameterError as exc:
        raise ParameterError(f"{label}: {exc}") from exc


def _resolve_params(chassis: ChassisParams, label: str) -> ResolvedParams:
    """上流の値を取り込み、突き合わせを済ませた `ResolvedParams` を返す。

    ⚠️ **`ResolvedParams` を組み立てる唯一の場所である。** 上流の下限との
    突き合わせ（`validate_against_upstream`）をここで必ず通すことが、
    「呼ばれない検証」を作らないための仕掛けである（要件 2.9 / design.md
    `#### Params` Implementation Notes）。

    上流の読み込みの失敗（上流の `ParameterError` 等）は**包み直さずに**
    そのまま伝播させる。どちらの設定ファイルが壊れているかをメッセージから
    消さないためである（design.md「Error Strategy」）。
    """
    upstream = upstream_load_params()
    try:
        chassis.validate_against_upstream(upstream.joint)
    except ParameterError as exc:
        # メッセージは自分の値と上流の値の**両方**を持っている（`params` 側）。
        # どのファイルを直せばよいかだけを補って再送する。
        raise ParameterError(f"{label}: {exc}") from exc
    return ResolvedParams(
        chassis=chassis,
        printing=upstream.printing,
        joint=upstream.joint,
        trash_can=upstream.trash_can,
    )


def load_params(path: Path | None = None) -> ResolvedParams:
    """寸法設定ファイルを読み込み、上流の値を取り込んだ結果を返す。

    Args:
        path: 読み込む設定ファイル。省略時は `DEFAULT_DIMENSIONS_PATH`。

    Returns:
        検証済みの `ResolvedParams`。以降の層で再検証を要さない
        （design.md `#### Params` Postconditions）。

    Raises:
        ParameterError: ファイルを読めない場合、JSON として解析できない場合、
            いずれかの階層に未知キーがある場合、必須キーが欠けている場合、
            値の型が違う場合、値が範囲外・不変条件違反である場合、記録形式の
            版が未対応の場合、出所表のキー集合がパラメータパス表と一致しない
            場合、または接合部の当たり面の下限が上流の下限を下回る場合。
    """
    target = DEFAULT_DIMENSIONS_PATH if path is None else path
    label = str(target)
    document = _require_object(_read_document(target), label)
    chassis = _build_chassis_params(document, label)
    return _resolve_params(chassis, label)


def _to_document(params: ChassisParams) -> dict[str, object]:
    """`params` を設定ファイルの形（JSON へ書ける素の値）へ写す。"""
    document: dict[str, object] = {_SCHEMA_VERSION_KEY: SCHEMA_VERSION}
    for component, field_names in _COMPONENT_FIELDS.items():
        instance = getattr(params, component)
        document[component] = {name: getattr(instance, name) for name in field_names}
    document[_PROVENANCE_KEY] = {
        path: provenance.value for path, provenance in params.provenance.items()
    }
    return document


def dump_params(params: ChassisParams, path: Path) -> None:
    """`params` を設定ファイルの形式で `path` へ書き出す（要件 1.10）。

    整形は**インデント2・キー整列・末尾改行**に固定する（design.md
    `#### Config` Responsibilities）。並びを入力の順に委ねると、同じ値を書き
    戻しただけで行が入れ替わり、変更が行単位の差分として読めなくなる。

    ⚠️ **改行は LF に固定する。** `os.linesep` に委ねると Windows と WSL で同じ
    コマンドが別のバイト列を生み、差分が実行環境で揺れる（本リポジトリの Python
    は WSL 側にある）。これと対になる取り決めとして、`.gitattributes` は
    `*.json` の一般則（`eol=crlf`）より後ろで
    `configs/chassis_mechanism/*.json text eol=lf` を指定している。つまり
    **本関数が書くバイト列は git がチェックアウトする内容と同一**であり、値が
    変わっていなければ `git status` は変更を報告しない。

    書き出しの失敗（`OSError`）は包まずにそのまま送出する。読み込み側と違い、
    出力先の不備は設定の内容の不正ではない。

    Args:
        params: 書き出す寸法パラメータ。⚠️ 本 Spec 固有の寸法だけを書く。
            上流の値（`ResolvedParams.printing` ほか）は書かない。
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


def _canonical_scalar(value: object, spec: ParameterPath) -> str | None:
    """1つの値を、書式に依らない正規表現へ写す（未決は `None` のまま）。

    浮動小数点は `repr` の最短往復表現を用いる（`60` / `60.0` / `6e1` はいずれ
    も同じ `float` であり、同じ表現になる）。⚠️ `-0.0` は `0.0` へ畳む——値と
    しては等しいのに `repr` が異なり、識別子だけが動いてしまうためである。
    正規化規則は上流と同形である（design.md `#### Config`）。

    未決（`None`）は文字列へ落とさずそのまま残す。⚠️ `"None"` という文字列へ
    写すと、`estop_provision` に `"None"` と書いた設定と未決とが同じ識別子に
    なってしまう。
    """
    if value is None:
        return None
    expected = spec.value_types[0]
    if expected is bool:
        return repr(bool(value))
    if expected is float:
        number = float(value)  # type: ignore[arg-type]
        if number == 0.0:
            number = 0.0
        return repr(number)
    if expected is int:
        return repr(int(value))  # type: ignore[call-overload]
    return str(value)


def _canonical_payload(params: ChassisParams) -> str:
    """識別子の算出対象となる正規化表現を組み立てる。

    - 値は全パスを**パス文字列で平坦化**して並べる。コンポーネントの入れ子や
      JSON の整形は識別の対象ではない
    - 出所も全パスを並べる（`ChassisParams` はキー集合の一致を保証している）
    - `schema_version` は含めない（記録形式はパラメータではない）
    - ⚠️ **組立後の観測は含めない。** 入力は `params` ただ1つであり、観測記録を
      読む手段をこの関数は持たない（design.md「Logical Data Model」）
    """
    values = {
        path: _canonical_scalar(
            getattr(getattr(params, spec.component), spec.field_name), spec
        )
        for path, spec in PARAMETER_PATHS.items()
    }
    provenance = {
        path: params.provenance[path].value for path in PARAMETER_PATHS
    }
    return json.dumps(
        {"values": values, _PROVENANCE_KEY: provenance},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def parameters_digest(params: ChassisParams) -> str:
    """寸法パラメータの識別子を `"sha256:<hex>"` の形で返す。

    同じ値・同じ出所であれば、設定ファイルの書式に依らず同一の文字列になる
    （design.md `#### Config` Postconditions）。逆に、値または出所が1つでも
    変われば変化する。⚠️ 形状指標の記録（`geometry-baseline.json`、タスク 6.x）
    はこの識別子を併せて保持し、**形状ライブラリを持たない環境でも**
    「パラメータを変えたのに指標を更新し忘れた」状態を検出する。

    ⚠️ **引数は `ChassisParams` ただ1つである。** 組立後の観測
    （`measurements.json`）も上流の値も入力にしない。前者を入れれば観測のたびに
    形状の再生成が要求され、後者を入れれば上流の設定変更が本 Spec の形状指標を
    無効にしてしまう。

    Args:
        params: 識別子を算出する本 Spec 固有の寸法パラメータ。

    Returns:
        `"sha256:"` に続く 64 桁の16進文字列。
    """
    payload = _canonical_payload(params).encode("utf-8")
    return f"{_DIGEST_ALGORITHM}:{hashlib.sha256(payload).hexdigest()}"


def update_upstream_measurement(
    path_key: str, value: float, path: Path | None = None
) -> None:
    """上流の仮値を実測値で更新する（要件 6.3 / 6.4）。

    ⚠️ **上流の設定ファイルへ書く唯一の経路である**（design.md `#### Config`
    State Management）。書き換えるのは**値と出所の該当行だけ**であり、構造・
    キー名・単位には触れない。これを保証するために、書き出しは上流自身の
    `dump_params` を通す——本 Spec が上流の整形規則を書き写せば、上流が整形を
    変えたときに黙って食い違う。

    出所は `MEASURED` へ更新する。⚠️ **値だけを書き換えて出所を仮値のまま残さ
    ない**——実測で置き換えた事実が記録に現れなければ、次に読む人は同じ値を
    測り直すか、仮値の上に設計を載せ続ける。

    ⚠️ 引数 `path` は**試験のための逃げ道ではなく、書き戻し先を明示できる
    ようにするためのもの**である。既定は `UPSTREAM_DIMENSIONS_PATH` であり、
    通常の呼び出しでは省略する。

    Args:
        path_key: 上流のパラメータパス（例:
            `"trash_can.bottom_flat_diameter_mm"`）。数値の項目に限る。
        value: 実測値。有限の数値でなければならない。
        path: 書き戻し先。省略時は `UPSTREAM_DIMENSIONS_PATH`。

    Raises:
        ParameterError: パスが上流のパラメータパス表に無い場合、対象が数値の
            項目でない場合、または値が有限の数値でない場合。⚠️ **本 Spec の
            パスを渡す誤りもここで落ちる**（本 Spec の寸法は上流のファイルに
            属さない。要件 1.3）。
    """
    spec = UPSTREAM_PARAMETER_PATHS.get(path_key)
    if spec is None:
        raise ParameterError(
            f"path_key={path_key!r} は上流 catch_mechanism のパラメータパス表に"
            "存在しない（書き戻せるのは上流が所有する寸法だけである）。"
        )
    if spec.value_type is not float:
        raise ParameterError(
            f"path_key={path_key!r} は数値の項目ではない"
            f"（値の型は {spec.value_type.__name__}）。"
            "採寸値の書き戻しが変更してよいのは数値と出所だけである。"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(
            f"{path_key}={value!r} は数値でなければならない"
            f"（{type(value).__name__} だった）。"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ParameterError(f"{path_key}={value!r} は有限の数値でなければならない。")

    target = UPSTREAM_DIMENSIONS_PATH if path is None else path
    params = upstream_load_params(target)
    component = replace(getattr(params, spec.component), **{spec.field_name: number})
    provenance = dict(params.provenance)
    provenance[path_key] = Provenance.MEASURED
    upstream_dump_params(
        replace(params, **{spec.component: component}, provenance=provenance), target
    )
