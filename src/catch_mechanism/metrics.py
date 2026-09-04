"""形状指標の型・記録・照合と質量の目安（design.md「Metrics」/ 要件 4.1, 4.2,
4.4, 8.7）。

**指標は素の数値だけで表す。** `PartMetrics` は部品名・体積・境界箱・立体数しか
持たず、形状オブジェクト（build123d の `Part`）を保持しない。⚠️ **これにより
照合ロジックが CAD 非導入環境でもテストできる**（design.md「Metrics」
Responsibilities / 要件 5.7）。形状を持つ側は `shapes.BuiltPart`（`solid` を
持つ）であり、中核層へは `measure_part` が抽出した数値だけが渡る。本モジュールは
形状ライブラリを import しないどころか、形状という概念を型として知らない。

**不一致は値であって例外ではない。** `compare_metrics` は不一致の**一覧**を返す
（`errors.py` docstring「評価結果（不適合・不一致）は値で返す」）。⚠️ 最初の
不一致で例外を送出する実装へ滑ると、要件 4.4 の「部品名と乖離した指標の双方の
値を示す」が一覧として成立しない——どこが駄目なのかを2件目以降について言えなく
なる。照合の結果を失敗として扱うかどうかは呼び出し側（`cli` の `check`、
タスク 4.1）が決める。

**部品の不在も不一致である。** 記録にあって再生成に無い部品、およびその逆を
報告する（design.md「Metrics」Invariants）。⚠️ 欠けた部品には「双方の数値」が
存在しないため、`MetricsMismatch` の数値2つを**在（`PRESENT` = 1.0）／不在
（`ABSENT` = 0.0）**として用い、項目名を `PRESENCE_FIELD` とする。design.md の
`MetricsMismatch` は `recorded: float` / `regenerated: float` を宣言しており、
型を変えずに不在を表すにはこの符号化しかない（`NaN` は「不明」としか読めず、
`==` で比較もできない）。⚠️ **不在の部品について体積や境界箱の不一致を
でっち上げない。** 「体積 1000 が 0 になった」と報告すれば、測っていない 0 が
測定値であるかのように読める。

**許容差は相対（体積）と絶対（境界箱）で持ち、記録側に明示する**（design.md
「Metrics」Responsibilities）。体積は部品の大きさに比例して数値が動くため相対で、
境界箱は寸法そのものであり mm 単位の意味を持つため絶対で見る。立体数に許容差は
無い——1個の部品が2個へ割れたことは、量の差ではなく形の破綻である。

**記録には `parameters_digest` を含める。** digest の不一致は、形状を再生成せず
とも検出できる（要件 4.5）。⚠️ 記録の構築時に見るのは「識別子が
`config.parameters_digest` と同じ**書式**であること」までであり、現在の設定
ファイルの識別子との突き合わせは `verify_baseline_digest` が別途行う
（`ConsistencyError`）。書式検査を欠くと、識別子らしくない文字列が記録に残った
まま突き合わせへ流れ込む。

**識別子の突き合わせはここに在り、CAD を要さない。** `verify_baseline_digest` は
形状も数値の指標も見ず2つの文字列を比べるだけなので、形状ライブラリ非導入の
環境でも「寸法を変えたまま記録を更新していない」状態を検出できる（要件 4.5,
5.7）。⚠️ **識別子そのものは本モジュールが計算しない**——現在の識別子を作るのは
`config.parameters_digest` であり、ここは受け取った文字列を比較するだけである
（タスク 4.1 は `_Boundary: Cli_` しか持たなかったため `cli.py` に置いたが、
タスク 4.2 が本来の層である `metrics` へ移した。tasks.md「Implementation Notes」
タスク 4.1(b)）。

**記録は出荷されている。** `configs/catch_mechanism/geometry-baseline.json` は
タスク 4.2 が実形状から生成して出荷した（`DEFAULT_BASELINE_PATH`）。
⚠️ `load_baseline()` は記録が無いとき**空の記録を黙って返さない**——空の記録は
どんな再生成結果とも一致してしまい、「記録を作り忘れた」状態が緑のまま流れる。
パスを示す `ParameterError` で失敗する。

読み込みの規律は `config.py` に揃える（**あらゆる階層で未知キーを拒否する**、
項目名を示す、欠損を既定値で埋めない、LF・インデント2・キー整列・末尾改行で
書き出す）。記録形式の版は `config.SCHEMA_VERSION` を共有する（tasks.md
「Implementation Notes」タスク 1.4(b)。⚠️ 独立に版を刻む必要が出たら、その時点で
この単一定数を分割すること）。

⚠️ **同層 import を行わない**（tasks.md「Implementation Notes」タスク 1.6(b)）。
`selection` / `tolerance` / `constraints` は互いに import できないため、
`tolerance.py` と重なる小さな検証補助（有限性・正値・非空文字列）は共有せずに
ここへ置く。共有したくなったら、まず design.md の依存方向を改めること。
同じ理由で部品名を `shapes.PART_NAMES` と突き合わせることもしない——部品名の
正は形状層にあり、記録は形状層より下で読めなければならない。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import ConsistencyError, ParameterError

__all__ = [
    "DEFAULT_BASELINE_PATH",
    "MM3_PER_CM3",
    "PRESENCE_FIELD",
    "PRESENT",
    "ABSENT",
    "PartMetrics",
    "GeometryBaseline",
    "MetricsMismatch",
    "load_baseline",
    "write_baseline",
    "compare_metrics",
    "verify_baseline_digest",
    "estimate_mass_g",
]


DEFAULT_BASELINE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "catch_mechanism"
    / "geometry-baseline.json"
)
"""形状指標の記録の既定パス（design.md「Data Models」
`configs/catch_mechanism/geometry-baseline.json`）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。記録はタスク 4.2 が実形状から生成して出荷しており、
`tests/catch_mechanism/test_catch_geometry_regression.py` が中身を現在の実装と
突き合わせている。⚠️ それでも `load_baseline()` は不在を黙認しない——記録が
無ければパスを示して失敗する（本モジュール docstring）。
"""

MM3_PER_CM3: Final[float] = 1000.0
"""1 cm^3 に含まれる mm^3 の数（質量の目安の単位換算）。

⚠️ 体積は mm^3、密度は g/cm^3 である。この換算を落とすと目安が 1000 倍に、
逆向きに掛ければ 1/1000 になる——どちらも一目では気付けない桁ではない一方、
数字だけを見て「重い部品だ」と誤読するには十分である。
"""

PRESENCE_FIELD: Final[str] = "presence"
"""部品の在／不在を表す不一致の項目名（design.md「Metrics」Invariants）。

⚠️ 記録と再生成の片方にしか無い部品は、体積や境界箱の**比較が成立しない**。
その1件を `field_name=PRESENCE_FIELD`、数値を `PRESENT` / `ABSENT` として
報告する。`MetricsMismatch` の型（`recorded: float` / `regenerated: float`）を
変えずに不在を表すための符号化である。
"""

PRESENT: Final[float] = 1.0
"""その側に部品が在ることを表す値（`PRESENCE_FIELD` の不一致で用いる）。"""

ABSENT: Final[float] = 0.0
"""その側に部品が無いことを表す値（`PRESENCE_FIELD` の不一致で用いる）。"""


_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_DIGEST_KEY: Final[str] = "parameters_digest"
_VOLUME_TOLERANCE_KEY: Final[str] = "volume_rel_tolerance"
_BBOX_TOLERANCE_KEY: Final[str] = "bbox_abs_tolerance_mm"
_GENERATOR_KEY: Final[str] = "generator_version"
_PARTS_KEY: Final[str] = "parts"
_PART_NAME_KEY: Final[str] = "part_name"
_VOLUME_KEY: Final[str] = "volume_mm3"
_BBOX_KEY: Final[str] = "bbox_mm"
_SOLID_COUNT_KEY: Final[str] = "solid_count"

_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {
        _SCHEMA_VERSION_KEY,
        _DIGEST_KEY,
        _VOLUME_TOLERANCE_KEY,
        _BBOX_TOLERANCE_KEY,
        _GENERATOR_KEY,
        _PARTS_KEY,
    }
)
_PART_KEYS: Final[frozenset[str]] = frozenset({_VOLUME_KEY, _BBOX_KEY, _SOLID_COUNT_KEY})

_BBOX_AXES: Final[int] = 3
"""境界箱の軸数（X, Y, Z）。⚠️ 並びは軸の順であり、書き出し側の責任である。"""

_DIGEST_PATTERN: Final[re.Pattern[str]] = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
"""`config.parameters_digest` が返す形（`sha256:` + 64桁の16進小文字）。

⚠️ 書式の写しであり、識別子の**値**の正ではない。`config` 側がアルゴリズムを
変えたときは、ここも同時に直す必要がある（`config._DIGEST_ALGORITHM` は非公開の
ため import せず、書式だけを固定する）。
"""


def _require_finite(value: object, name: str) -> float:
    """`value` が有限な数（`bool` を除く）であることを検証し `float` で返す。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterError(
            f"{name}={value!r} は数値でなければならない（{type(value).__name__} だった）。"
        )
    if not math.isfinite(value):
        raise ParameterError(f"{name}={value!r} は有限でなければならない。")
    return float(value)


def _require_positive_finite(value: object, name: str) -> float:
    """`value` が正の有限値であることを検証し `float` で返す。"""
    number = _require_finite(value, name)
    if number <= 0.0:
        raise ParameterError(f"{name}={value!r} は正でなければならない。")
    return number


def _require_nonneg_finite(value: object, name: str) -> float:
    """`value` が非負の有限値であることを検証し `float` で返す。"""
    number = _require_finite(value, name)
    if number < 0.0:
        raise ParameterError(f"{name}={value!r} は非負でなければならない。")
    return number


def _require_nonempty_str(value: object, name: str) -> str:
    """`value` が空でない文字列であることを検証する。"""
    if not isinstance(value, str) or not value.strip():
        raise ParameterError(f"{name}={value!r} は空でない文字列でなければならない。")
    return value


@dataclass(frozen=True, slots=True)
class PartMetrics:
    """部品1点の形状指標（要件 4.1）。

    ⚠️ **素の数値だけを持つ。** 形状オブジェクトを保持しないため、本型に関する
    検査は形状ライブラリ非導入の環境で完結する（design.md「Metrics」
    Responsibilities）。

    Attributes:
        part_name: 部品名。不一致の報告先であり、記録の対応表のキーと一致する。
        volume_mm3: 体積（mm^3）。正である。
        bbox_mm: 軸並行の外接箱の寸法（mm）を X, Y, Z の順に並べた3要素の組。
            ⚠️ **並びは軸の順**であり、記録の配列もこの順で書き出す。
        solid_count: 立体の数。1 以上である——0 個の立体は「部品」ではなく、
            形状生成が空を返した事故であって、指標として記録してよい状態ではない。

    Raises:
        ParameterError: 名が空の場合、体積が正の有限値でない場合、境界箱が3つの
            正の有限値でない場合、または立体数が 1 以上の整数でない場合。
    """

    part_name: str
    volume_mm3: float
    bbox_mm: tuple[float, float, float]
    solid_count: int

    def __post_init__(self) -> None:
        """全不変条件を検証し、違反時は項目名と値を添えて拒否する。"""
        _require_nonempty_str(self.part_name, _PART_NAME_KEY)
        _require_positive_finite(self.volume_mm3, _VOLUME_KEY)

        if not isinstance(self.bbox_mm, tuple) or len(self.bbox_mm) != _BBOX_AXES:
            raise ParameterError(
                f"{_BBOX_KEY}={self.bbox_mm!r} は X, Y, Z の3要素の組でなければならない。"
            )
        for index, extent in enumerate(self.bbox_mm):
            _require_positive_finite(extent, f"{_BBOX_KEY}[{index}]")

        if isinstance(self.solid_count, bool) or not isinstance(self.solid_count, int):
            raise ParameterError(
                f"{_SOLID_COUNT_KEY}={self.solid_count!r} は整数でなければならない"
                f"（{type(self.solid_count).__name__} だった）。"
            )
        if self.solid_count < 1:
            raise ParameterError(
                f"{_SOLID_COUNT_KEY}={self.solid_count!r} は 1 以上でなければならない"
                "（立体を持たないものを部品の指標として記録しない）。"
            )


@dataclass(frozen=True, slots=True)
class GeometryBaseline:
    """形状指標の記録（design.md「Data Models」`geometry-baseline.json` / 要件 4.2）。

    Attributes:
        schema_version: 記録形式の版。`config.SCHEMA_VERSION` と一致する。
        parameters_digest: 記録時の寸法パラメータの識別子（`sha256:<hex>`）。
            ⚠️ ここで検査するのは**書式のみ**である。現在の設定ファイルの識別子と
            の突き合わせは `verify_baseline_digest` が行う（`ConsistencyError`）。
        volume_rel_tolerance: 体積の**相対**許容差（無次元）。非負である。
        bbox_abs_tolerance_mm: 境界箱の**絶対**許容差（mm）。非負である。
        generator_version: 記録時の形状ライブラリ版（情報用）。照合には用いない
            ——版が上がっただけで照合が落ちてよい理由にはならず、実際の差は
            指標そのものに現れる。
        parts: 部品名から指標への対応表。1件以上を持ち、キーは各指標の
            `part_name` と一致する。構築時に `dict` へ複製するため、呼び出し側の
            辞書のその後の変更は反映されない。

    Raises:
        ParameterError: 版が未対応の場合、識別子の書式が違う場合、許容差が非負の
            有限値でない場合、生成ライブラリ版が空の場合、対応表が空・キーと
            `part_name` が食い違う・値が `PartMetrics` でない場合。
    """

    schema_version: str
    parameters_digest: str
    volume_rel_tolerance: float
    bbox_abs_tolerance_mm: float
    generator_version: str
    parts: Mapping[str, PartMetrics]

    def __post_init__(self) -> None:
        """全不変条件を検証し、エイリアスを切った複製で `parts` を置き換える。"""
        if self.schema_version != SCHEMA_VERSION:
            raise ParameterError(
                f"{_SCHEMA_VERSION_KEY}={self.schema_version!r} は未対応である"
                f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
            )
        _require_nonempty_str(self.parameters_digest, _DIGEST_KEY)
        if _DIGEST_PATTERN.match(self.parameters_digest) is None:
            raise ParameterError(
                f"{_DIGEST_KEY}={self.parameters_digest!r} は "
                "'sha256:' に続く64桁の16進小文字でなければならない"
                "（config.parameters_digest の書式）。"
            )
        _require_nonneg_finite(self.volume_rel_tolerance, _VOLUME_TOLERANCE_KEY)
        _require_nonneg_finite(self.bbox_abs_tolerance_mm, _BBOX_TOLERANCE_KEY)
        _require_nonempty_str(self.generator_version, _GENERATOR_KEY)

        if not isinstance(self.parts, Mapping):
            raise ParameterError(
                f"{_PARTS_KEY}={self.parts!r} は部品名から指標への対応表で"
                "なければならない。"
            )
        if not self.parts:
            raise ParameterError(
                f"{_PARTS_KEY}: 記録は1件以上の部品を持たなければならない"
                "（部品を持たない記録は、どんな再生成結果とも一致してしまう）。"
            )
        _validate_metrics_mapping(self.parts, _PARTS_KEY)

        # 検証を通った記録が以降の照合で差し替わらないよう、呼び出し側の辞書との
        # エイリアスを切る。⚠️ `MappingProxyType` で包まない——`dataclasses.asdict`
        # が dict でないマッピングを `copy.deepcopy` へ回して壊れる
        # （tasks.md「Implementation Notes」タスク 1.3(a)、`params.MechanismParams`
        # と同じ扱い）。
        object.__setattr__(self, _PARTS_KEY, dict(self.parts))


@dataclass(frozen=True, slots=True)
class MetricsMismatch:
    """照合で見つかった不一致1件（要件 4.4）。

    ⚠️ **例外ではなく値である。** `errors.py` は本型を「評価結果」の側に置いて
    おり（同 docstring）、`compare_metrics` はこれを一覧で返す。

    Attributes:
        part_name: 食い違った部品の名。
        field_name: 食い違った項目の名（`volume_mm3` / `bbox_mm[0..2]` /
            `solid_count`、または部品の不在を表す `PRESENCE_FIELD`）。
        recorded: 記録側の値。`PRESENCE_FIELD` のときは `PRESENT` / `ABSENT`。
        regenerated: 再生成側の値。同上。

    Raises:
        ParameterError: 名が空の場合、または値が有限な数でない場合。
    """

    part_name: str
    field_name: str
    recorded: float
    regenerated: float

    def __post_init__(self) -> None:
        """全不変条件を検証する（報告そのものが壊れていないことを保証する）。"""
        _require_nonempty_str(self.part_name, _PART_NAME_KEY)
        _require_nonempty_str(self.field_name, "field_name")
        _require_finite(self.recorded, "recorded")
        _require_finite(self.regenerated, "regenerated")


def _validate_metrics_mapping(mapping: Mapping[str, PartMetrics], label: str) -> None:
    """対応表の値が `PartMetrics` であり、キーが `part_name` と一致することを要求する。

    キーと `part_name` の食い違いを通すと、不一致の報告が「対応表のどこにも無い
    部品名」を名乗ることになり、要件 4.4 の「部品名を示す」が意味を失う。
    """
    for key, part in mapping.items():
        if not isinstance(part, PartMetrics):
            raise ParameterError(
                f"{label}[{key!r}]={part!r} は PartMetrics でなければならない"
                f"（{type(part).__name__} だった）。"
            )
        if key != part.part_name:
            raise ParameterError(
                f"{label}: キー {key!r} と {_PART_NAME_KEY}={part.part_name!r} が"
                "一致しない（対応表のキーは部品名そのものでなければならない）。"
            )


def _volume_agrees(recorded: float, regenerated: float, rel_tolerance: float) -> bool:
    """体積が**相対**許容差の範囲で一致するか（境界は許容側に含む）。

    記録側の値を基準に取る——比較の基準が再生成のたびに動くと、同じ差が
    合格にも不合格にもなる。`PartMetrics.volume_mm3` は正であるため、
    基準が 0 になることはない。
    """
    return abs(regenerated - recorded) <= rel_tolerance * recorded


def _extent_agrees(recorded: float, regenerated: float, abs_tolerance_mm: float) -> bool:
    """境界箱の1軸が**絶対**許容差（mm）の範囲で一致するか（境界を含む）。"""
    return abs(regenerated - recorded) <= abs_tolerance_mm


def _compare_part(
    part_name: str,
    recorded: PartMetrics,
    regenerated: PartMetrics,
    baseline: GeometryBaseline,
) -> list[MetricsMismatch]:
    """両側に存在する部品1点を照合し、不一致を宣言順に並べて返す。"""
    mismatches: list[MetricsMismatch] = []

    if not _volume_agrees(
        recorded.volume_mm3, regenerated.volume_mm3, baseline.volume_rel_tolerance
    ):
        mismatches.append(
            MetricsMismatch(
                part_name=part_name,
                field_name=_VOLUME_KEY,
                recorded=recorded.volume_mm3,
                regenerated=regenerated.volume_mm3,
            )
        )

    for index in range(_BBOX_AXES):
        recorded_extent = recorded.bbox_mm[index]
        regenerated_extent = regenerated.bbox_mm[index]
        if not _extent_agrees(
            recorded_extent, regenerated_extent, baseline.bbox_abs_tolerance_mm
        ):
            mismatches.append(
                MetricsMismatch(
                    part_name=part_name,
                    field_name=f"{_BBOX_KEY}[{index}]",
                    recorded=recorded_extent,
                    regenerated=regenerated_extent,
                )
            )

    if recorded.solid_count != regenerated.solid_count:
        # 立体数に許容差は無い。1個の部品が2個へ割れたことは量の差ではない。
        mismatches.append(
            MetricsMismatch(
                part_name=part_name,
                field_name=_SOLID_COUNT_KEY,
                recorded=float(recorded.solid_count),
                regenerated=float(regenerated.solid_count),
            )
        )

    return mismatches


def compare_metrics(
    baseline: GeometryBaseline, measured: Mapping[str, PartMetrics]
) -> tuple[MetricsMismatch, ...]:
    """記録済みの指標と再生成した指標を照合する（要件 4.4）。

    ⚠️ **不一致は例外ではなく値で返す。** 最初の1件で打ち切らず、部品名・項目名・
    双方の値を持つ不一致を**すべて**返す（本モジュール docstring）。

    ⚠️ **片側にしか無い部品も不一致である**（design.md「Metrics」Invariants）。
    その1件は `field_name=PRESENCE_FIELD`、値は在＝`PRESENT` / 不在＝`ABSENT` と
    して報告し、比較が成立しない体積・境界箱の不一致は**でっち上げない**。

    Args:
        baseline: 記録済みの指標と許容差。
        measured: 再生成した指標（部品名 → 指標）。キーは各指標の `part_name` と
            一致していなければならない。

    Returns:
        不一致の並び。一致していれば空タプル（design.md「Metrics」
        Postconditions）。並びは**部品名の昇順 → 項目の宣言順**で決定的である
        ——実行ごとに順が変われば、出力の差分が読めなくなる。

    Raises:
        ParameterError: `measured` が対応表でない場合、値が `PartMetrics` でない
            場合、またはキーが `part_name` と食い違う場合（照合の**呼び出し方**の
            誤りであり、照合結果ではない）。
    """
    if not isinstance(measured, Mapping):
        raise ParameterError(
            f"measured={measured!r} は部品名から指標への対応表でなければならない。"
        )
    _validate_metrics_mapping(measured, "measured")

    mismatches: list[MetricsMismatch] = []
    for part_name in sorted(set(baseline.parts) | set(measured)):
        recorded = baseline.parts.get(part_name)
        regenerated = measured.get(part_name)
        if recorded is None or regenerated is None:
            mismatches.append(
                MetricsMismatch(
                    part_name=part_name,
                    field_name=PRESENCE_FIELD,
                    recorded=ABSENT if recorded is None else PRESENT,
                    regenerated=ABSENT if regenerated is None else PRESENT,
                )
            )
            continue
        mismatches.extend(_compare_part(part_name, recorded, regenerated, baseline))

    return tuple(mismatches)


def verify_baseline_digest(
    baseline: GeometryBaseline,
    current_digest: str,
    *,
    baseline_path: Path,
    dimensions_path: Path,
) -> str:
    """記録の識別子が現在の寸法設定の識別子と一致することを検査する（要件 4.5）。

    ⚠️ **形状を再生成せずに実行できる。** 本関数は数値の指標も形状も見ず、
    2つの文字列だけを突き合わせる——だからこそ形状ライブラリ非導入の環境でも
    「寸法パラメータを変更したまま記録を更新していない」状態を検出できる
    （要件 5.7 / design.md「Metrics」Responsibilities「digest の不一致は、形状を
    再生成せずとも検出できる」）。

    ⚠️ **識別子は本モジュールが計算しない。** 現在の識別子を作るのは
    `config.parameters_digest` であり、本関数は受け取った文字列を比較するだけで
    ある。計算を持ち込めば `params` への依存が生まれ、「記録は形状層より下で
    読めなければならない」という本モジュールの立ち位置が濁る。

    Args:
        baseline: 検査する記録。
        current_digest: 現在の寸法設定ファイルの識別子（`config.parameters_digest`
            の戻り値）。⚠️ 書式（`sha256:` + 64桁の16進小文字）を満たすこと。
        baseline_path: 記録の在り処。失敗時の参照元として示す。
        dimensions_path: 現在の寸法設定ファイルの在り処。同上。

    Returns:
        `current_digest`（一致したときのみ）。呼び出し側が「一致した識別子」を
        そのまま出力に使えるようにするための戻り値である。

    Raises:
        ConsistencyError: 記録と現在の識別子が食い違う場合。⚠️ **双方の値と
            双方の参照元を載せる**——片方しか出さない失敗は、どちらが古いのかを
            読み手に決めさせてしまう（`errors.ConsistencyError`:
            「照合を待たずに成立していない整合を拒否する」）。
        ParameterError: `current_digest` が識別子の書式を満たさない場合。
            ⚠️ これは**呼び出し方の誤り**であって不整合ではない。書式の壊れた
            文字列を「一致しない」として扱うと、記録が古いのか呼び出しが壊れて
            いるのかが区別できなくなる（`compare_metrics` が壊れた `measured` を
            `ParameterError` で拒むのと同じ規律）。
    """
    _require_nonempty_str(current_digest, "current_digest")
    if _DIGEST_PATTERN.match(current_digest) is None:
        raise ParameterError(
            f"current_digest={current_digest!r} は "
            "'sha256:' に続く64桁の16進小文字でなければならない"
            "（config.parameters_digest の書式）。"
        )

    if baseline.parameters_digest != current_digest:
        raise ConsistencyError(
            f"記録 {baseline_path} の {_DIGEST_KEY}={baseline.parameters_digest!r} は、"
            f"現在の {dimensions_path} の識別子 {current_digest!r} と一致しない。"
            "寸法パラメータを変更したまま形状指標の記録を更新していない（要件 4.5）。"
            "`build --update-baseline` で記録を更新すること。"
        )
    return current_digest


def estimate_mass_g(volume_mm3: float, density_g_cm3: float) -> float:
    """体積と材料密度から質量の**目安**を算出する（要件 8.7）。

    ⚠️ **中身の詰まった立体としての目安である。** 充填率・外壁・上下面の設定を
    考慮しないため、実際の FDM 造形物はこの値より軽くなる（典型的な充填率では
    大きく下回る）。要件 8.7 が求めるのは「質量の目安」であり、合否条件でも
    見積り書に載せる値でもない。⚠️ 目安であることを黙示にせず、呼び出し側の
    出力にもその旨を添えること。

    Args:
        volume_mm3: 生成物の体積（mm^3）。正の有限値。
        density_g_cm3: 材料の密度（g/cm^3）。正の有限値。
            `params.PrintingConstraints.material_density_g_cm3` が供給する。

    Returns:
        質量の目安（g）。⚠️ 単位換算は `MM3_PER_CM3`（1 cm^3 = 1000 mm^3）で
        あり、例えば 1000 mm^3 の PETG（1.27 g/cm^3）は 1.27 g である。

    Raises:
        ParameterError: 体積または密度が正の有限値でない場合。
    """
    volume = _require_positive_finite(volume_mm3, _VOLUME_KEY)
    density = _require_positive_finite(density_g_cm3, "density_g_cm3")
    return volume / MM3_PER_CM3 * density


def _to_document(baseline: GeometryBaseline) -> dict[str, object]:
    """`baseline` を記録ファイルの形（JSON へ書ける素の値）へ写す。"""
    return {
        _SCHEMA_VERSION_KEY: baseline.schema_version,
        _DIGEST_KEY: baseline.parameters_digest,
        _VOLUME_TOLERANCE_KEY: baseline.volume_rel_tolerance,
        _BBOX_TOLERANCE_KEY: baseline.bbox_abs_tolerance_mm,
        _GENERATOR_KEY: baseline.generator_version,
        _PARTS_KEY: {
            name: {
                _VOLUME_KEY: part.volume_mm3,
                _BBOX_KEY: list(part.bbox_mm),
                _SOLID_COUNT_KEY: part.solid_count,
            }
            for name, part in baseline.parts.items()
        },
    }


def write_baseline(baseline: GeometryBaseline, path: Path) -> None:
    """形状指標の記録を `path` へ書き出す（要件 4.2）。

    整形は `config.dump_params` に揃える——**インデント2・キー整列・末尾改行**、
    そして**改行は LF に固定**する。`.gitattributes` が
    `configs/catch_mechanism/*.json` を `text eol=lf` に倒しているため、本関数が
    書くバイト列は git がチェックアウトする内容と同一であり、値が変わって
    いなければ `git status` は変更を報告しない（tasks.md「Implementation Notes」
    タスク 1.5）。

    ⚠️ `bbox_mm` は**配列であり、並びは軸の順**（X, Y, Z）である。キー整列は
    オブジェクトの内側にしか効かないため、この並びは書き出し側の責任である。

    書き出しの失敗（`OSError`）は包まずにそのまま送出する（`dump_params` と同じ
    扱い。出力先の不備は記録内容の不正ではない）。

    Args:
        baseline: 書き出す記録。
        path: 書き出し先。既存ファイルは上書きされる。
    """
    text = json.dumps(
        _to_document(baseline),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    path.write_text(f"{text}\n", encoding="utf-8", newline="\n")


def _read_document(path: Path) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `ParameterError` へ統一
    する（`config._read_document` と同形）。⚠️ **未存在を空の記録へ読み替え
    ない**——記録を作り忘れた状態が照合の成功として現れてしまう。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{path}: 形状指標の記録を読み込めない: {exc}") from exc
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

    未知キーを先に見るのは、綴り誤り（`volume_mm_3`）が「未知キー1件 + 欠損1件」
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


def _build_part(name: object, entry: object, label: str) -> PartMetrics:
    """部品1件分の JSON オブジェクトから `PartMetrics` を構築する。

    ⚠️ 部品名は既知の一覧と突き合わせない。部品名の正は形状層
    （`shapes.PART_NAMES`）にあり、本モジュールはそこへ依存できない
    （本モジュール docstring の同層・上位層 import の禁止）。
    """
    where = f"{label}: {_PARTS_KEY}[{name!r}]"
    if not isinstance(name, str):  # pragma: no cover - 構造上の防御
        # JSON オブジェクトのキーは常に文字列であるため通常は到達しない。
        # `PartMetrics.part_name` の不変条件を型の上でも閉じるために置く。
        raise ParameterError(
            f"{label}: {_PARTS_KEY} のキー {name!r} は文字列でなければならない。"
        )
    values = _require_object(entry, where)
    _reject_unknown_and_missing(values, _PART_KEYS, where)

    volume = _require_finite(values[_VOLUME_KEY], f"{where}: {_VOLUME_KEY}")

    extents = values[_BBOX_KEY]
    if not isinstance(extents, list) or len(extents) != _BBOX_AXES:
        raise ParameterError(
            f"{where}: {_BBOX_KEY}={extents!r} は3つの数の配列（X, Y, Z）で"
            "なければならない。"
        )
    bbox = tuple(
        _require_finite(extent, f"{where}: {_BBOX_KEY}[{index}]")
        for index, extent in enumerate(extents)
    )

    solid_count = values[_SOLID_COUNT_KEY]
    if isinstance(solid_count, bool) or not isinstance(solid_count, int):
        raise ParameterError(
            f"{where}: {_SOLID_COUNT_KEY}={solid_count!r} は整数でなければならない"
            f"（{type(solid_count).__name__} だった）。"
        )

    try:
        return PartMetrics(
            part_name=name,
            volume_mm3=volume,
            bbox_mm=(bbox[0], bbox[1], bbox[2]),
            solid_count=solid_count,
        )
    except ParameterError as exc:
        # 構築時検証（正の体積・正の外接箱・1以上の立体数）の違反。項目名と値は
        # 例外側が持っているため、どのファイルのどの部品かだけを補って再送する。
        raise ParameterError(f"{where}: {exc}") from exc


def load_baseline(path: Path | None = None) -> GeometryBaseline:
    """形状指標の記録を読み戻し、検証済みの `GeometryBaseline` を返す（要件 4.2）。

    ⚠️ **読み戻しは書式の復元ではなく、記録の検査である。** 版・識別子の書式・
    許容差・各部品の指標が成立していることを、そのつど構築を通して確かめる
    （`tolerance.load_derivation` と同じ規律）。

    ⚠️ **記録が無ければ失敗する。** 空の記録を返せば照合は「部品0件」で必ず成功
    し、記録を作り忘れた状態が緑のまま流れる（本モジュール docstring）。

    Args:
        path: 読み込む記録。省略時は `DEFAULT_BASELINE_PATH`。⚠️ 既定の記録は
            タスク 4.2 が初期化する。

    Returns:
        構築時検証を通った `GeometryBaseline`。

    Raises:
        ParameterError: ファイルを読めない場合、JSON として解析できない場合、
            いずれかの階層に未知キー・欠損キーがある場合、値の型が違う場合、
            記録形式の版が未対応の場合、または記録の内容が指標として成立しない
            場合。
    """
    target = DEFAULT_BASELINE_PATH if path is None else path
    label = str(target)

    document = _require_object(_read_document(target), label)
    _reject_unknown_and_missing(document, _TOP_LEVEL_KEYS, label)

    version = document[_SCHEMA_VERSION_KEY]
    if version != SCHEMA_VERSION:
        raise ParameterError(
            f"{label}: {_SCHEMA_VERSION_KEY}={version!r} は未対応である"
            f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
        )

    digest = document[_DIGEST_KEY]
    if not isinstance(digest, str):
        raise ParameterError(
            f"{label}: {_DIGEST_KEY}={digest!r} は文字列でなければならない"
            f"（{type(digest).__name__} だった）。"
        )
    generator_version = document[_GENERATOR_KEY]
    if not isinstance(generator_version, str):
        raise ParameterError(
            f"{label}: {_GENERATOR_KEY}={generator_version!r} は文字列でなければ"
            f"ならない（{type(generator_version).__name__} だった）。"
        )

    volume_rel_tolerance = _require_finite(
        document[_VOLUME_TOLERANCE_KEY], f"{label}: {_VOLUME_TOLERANCE_KEY}"
    )
    bbox_abs_tolerance_mm = _require_finite(
        document[_BBOX_TOLERANCE_KEY], f"{label}: {_BBOX_TOLERANCE_KEY}"
    )

    entries = _require_object(document[_PARTS_KEY], f"{label}: {_PARTS_KEY}")
    parts = {
        name: _build_part(name, entry, label) for name, entry in entries.items()
    }

    try:
        return GeometryBaseline(
            schema_version=version,
            parameters_digest=digest,
            volume_rel_tolerance=volume_rel_tolerance,
            bbox_abs_tolerance_mm=bbox_abs_tolerance_mm,
            generator_version=generator_version,
            parts=parts,
        )
    except ParameterError as exc:
        raise ParameterError(f"{label}: {exc}") from exc
