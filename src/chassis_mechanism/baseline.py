"""形状指標の記録の書き出しと識別子照合（design.md `#### Baseline` / 要件 1.12）。

**型と照合は上流から借り、書き出しだけをここが持つ。** 部品1点の指標
（`PartMetrics`）、記録の全体（`GeometryBaseline`）、不一致の型
（`MetricsMismatch`）、照合（`compare_metrics`）、記録の読み戻し
（`load_baseline`）、在／不在の符号化（`PRESENCE_FIELD` / `PRESENT` / `ABSENT`）は
いずれも上流 `catch_mechanism` の公開契約であり、⚠️ **同じ型をここで再定義しない**
（design.md `#### Baseline` Responsibilities / 要件 1.3）。本モジュールが足すのは、
記録を**書き出す**手段と、記録に埋め込まれた識別子を現在の寸法パラメータの識別子と
**突き合わせる**手段の2つだけである。

⚠️ **上流が意図して公開していない操作を掘り出さない。** 上流
`catch_mechanism/__init__.py` の docstring「公開しないもの」は、記録を書き換える
操作とその鮮度検査を「下流は記録を消費するだけで再生成しない」という理由で公開面
から外している。本 Spec は要件 1.12 により自身の記録を持つため、**同等の働きを
自前で実装する**——上流の非公開関数へ到達するのではない。この区別は
`tests/chassis_mechanism/test_chassis_upstream_contract.py` の
`UNPUBLISHED_UPSTREAM_OPERATIONS` が機械的に固定している。

**読み込みは借りたままで足りる。** 上流 `load_baseline(path)` は読み先をパスで
受け取るため、本 Spec の記録（`DEFAULT_BASELINE_PATH`）へそのまま向けられる。
⚠️ **同じ読み込みをここで書き直さない**——書き直せば、未知キー拒否・欠損拒否・
構築時検証という上流の規律が本 Spec の側だけ古びる余地が生まれる。往復が成立する
ことは `test_chassis_baseline.py::test_upstream_load_baseline_reads_a_record_written_here`
が固定している。

**書き出しは原子的である**（design.md `#### Baseline` Postconditions / 「Error
Handling」の「書き出しは原子的: 一時領域を経て確定する。失敗時に部分ファイルを
残さない」）。⚠️ **途中で失敗しても既存の記録を壊さない。** 直列化を先に済ませ、
同じディレクトリの一時ファイルへ書き切ってから差し替える。確定より前で失敗した
場合、書き出し先は1バイトも変わらず、一時ファイルも残らない。既存の
`config.dump_params` / `assembly.dump_assembly_record` が素朴な上書きで足りるのに
対し、本モジュールだけが原子性を要求されるのは、記録が**形状の再生成なしに古さを
判定する唯一の根拠**であり、壊れた記録は「照合できない」ではなく「照合の前提が
消えた」を意味するためである。

**識別子は `config.parameters_digest` が作る。** 本モジュールは受け取った2つの
文字列を比べるだけであり、⚠️ **形状も数値の指標も見ない**。だからこそ形状
ライブラリを導入していない環境でも「寸法パラメータを変更したまま記録を更新して
いない」状態を検出できる（要件 1.12 / design.md「Testing Strategy」）。
⚠️ 観測（`measurements.json`）は識別子に入らない——観測のたびに形状の再生成が
要求されてはならない（design.md 「Logical Data Model」）。

⚠️ **出荷される記録の中身はタスク 4.2 が作る。** 本モジュールが用意するのは
記録の**仕組み**（書き出し・原子性・識別子照合）であり、
`configs/chassis_mechanism/geometry-baseline.json` そのものは、実形状から全部品の
指標を生成できるようになった時点（tasks.md タスク 4.2「現在の寸法パラメータから
全部品を生成し、指標の記録を作る」）で初めて出荷される。⚠️ **作り物の指標を
置いて場所を埋めない**——上流 `GeometryBaseline` は部品0件の記録を拒む
（「部品を持たない記録は、どんな再生成結果とも一致してしまう」）ため、いま置ける
のは「実部品を騙る数値」だけになる。記録が無ければ `load_baseline` がパスを示して
失敗する（上流 `metrics` docstring「⚠️ **記録が無ければ失敗する。**」）。

依存の制約（design.md「Allowed Dependencies」/「Dependency Direction」）:
    本モジュールは中核層 `{assembly, baseline}` に属し、`errors` / `params` /
    `config` / `layout` / `clearance` / `joints` より右側を import しない。
    ⚠️ **形状ライブラリを import しない**——記録は形状層より下で読めなければ
    ならず、それが「形状を再生成せずに古さが判る」ことの前提である。
    上流へは公開入口（`from catch_mechanism import X`）からのみ触れる。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Final

from catch_mechanism import GeometryBaseline, PartMetrics

from chassis_mechanism.config import DEFAULT_DIMENSIONS_PATH, parameters_digest
from chassis_mechanism.errors import ConsistencyError
from chassis_mechanism.params import ChassisParams

__all__ = [
    "DEFAULT_BASELINE_PATH",
    "dump_baseline",
    "verify_digest",
]


DEFAULT_BASELINE_PATH: Final[Path] = (
    Path(__file__).resolve().parents[2]
    / "configs"
    / "chassis_mechanism"
    / "geometry-baseline.json"
)
"""本 Spec の形状指標の記録の既定パス（design.md「Data Models」）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。⚠️ **上流の記録（`configs/catch_mechanism/`）とは別物である**
——受け口の部品と駆動ベースの部品は別の寸法から決まり、別の識別子を持つ。

⚠️ **このファイルはまだ出荷されていない。** 中身を作るのはタスク 4.2 であり、
それまでこのパスを読もうとすれば上流 `load_baseline` がパスを示して失敗する
（本モジュール docstring 参照）。
"""

_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_DIGEST_KEY: Final[str] = "parameters_digest"
_VOLUME_TOLERANCE_KEY: Final[str] = "volume_rel_tolerance"
_BBOX_TOLERANCE_KEY: Final[str] = "bbox_abs_tolerance_mm"
_GENERATOR_KEY: Final[str] = "generator_version"
_PARTS_KEY: Final[str] = "parts"
_VOLUME_KEY: Final[str] = "volume_mm3"
_BBOX_KEY: Final[str] = "bbox_mm"
_SOLID_COUNT_KEY: Final[str] = "solid_count"

_TEMPORARY_SUFFIX: Final[str] = ".tmp"
"""一時ファイルの接尾辞。確定前の中身が記録として読まれないための目印である。"""


def _part_document(part: PartMetrics) -> dict[str, object]:
    """部品1点の指標を、記録ファイルの形（JSON へ書ける素の値）へ写す。

    ⚠️ `bbox_mm` は**配列であり、並びは軸の順**（X, Y, Z）である。キー整列は
    オブジェクトの内側にしか効かないため、この並びは書き出し側の責任である。
    """
    return {
        _VOLUME_KEY: part.volume_mm3,
        _BBOX_KEY: list(part.bbox_mm),
        _SOLID_COUNT_KEY: part.solid_count,
    }


def _to_document(baseline: GeometryBaseline) -> dict[str, object]:
    """`baseline` を記録ファイルの形へ写す（上流 `GeometryBaseline` の形そのもの）。

    design.md「Data Models」`configs/chassis_mechanism/geometry-baseline.json` は
    「上流 `GeometryBaseline` の形式に**そのまま従う**」と定める。⚠️ 項目を1つでも
    足し引きすると、借りている `load_baseline` が読めなくなる。
    """
    return {
        _SCHEMA_VERSION_KEY: baseline.schema_version,
        _DIGEST_KEY: baseline.parameters_digest,
        _VOLUME_TOLERANCE_KEY: baseline.volume_rel_tolerance,
        _BBOX_TOLERANCE_KEY: baseline.bbox_abs_tolerance_mm,
        _GENERATOR_KEY: baseline.generator_version,
        _PARTS_KEY: {
            name: _part_document(part) for name, part in baseline.parts.items()
        },
    }


def dump_baseline(baseline: GeometryBaseline, path: Path) -> None:
    """形状指標の記録を `path` へ**原子的に**書き出す（要件 1.12）。

    整形は `config.dump_params` に揃える——**インデント2・キー整列・末尾改行**、
    そして**改行は LF に固定**する。`.gitattributes` が
    `configs/chassis_mechanism/*.json` を `text eol=lf` に倒しているため、本関数が
    書くバイト列は git がチェックアウトする内容と同一であり、値が変わっていなければ
    `git status` は変更を報告しない。

    ⚠️ **原子性の実体は3つある**（design.md `#### Baseline` Postconditions
    「書き出しは原子的。⚠️ 途中で失敗しても既存の記録を壊さない」）:

    1. **直列化を先に済ませる。** 記録が JSON へ写せないと判った時点ではまだ
       一時ファイルすら作っていないため、後始末する対象が無い。
    2. **一時ファイルは確定先と同じディレクトリに作る。** 別のファイルシステム
       （`/tmp` 等）へ置くと差し替えが `Invalid cross-device link` になり、
       原子的な確定が成立しない。
    3. **差し替えで確定する。** 確定より前のどの時点で失敗しても、書き出し先は
       1バイトも変わらない。失敗時は一時ファイルを消してから送出するため、
       書き出し先のディレクトリに正体不明のファイルが残らない。

    ⚠️ **`baseline.parts` が空でないことは前提である**（design.md
    `#### Baseline` Preconditions）。上流 `GeometryBaseline` が構築時に拒むため、
    本関数へ空の記録が届く経路は無い——ここで数え直さないのはそのためである。

    書き出しの失敗（`OSError`）は包まずにそのまま送出する（`config.dump_params` と
    同じ扱い。出力先の不備は記録内容の不正ではない）。

    Args:
        baseline: 書き出す記録。
        path: 書き出し先。既存ファイルは差し替えられる。親ディレクトリは
            存在していなければならない。

    Raises:
        OSError: 一時ファイルを作れない場合、書き込めない場合、または差し替えに
            失敗した場合。⚠️ いずれの場合も既存の記録は変わらない。
    """
    text = json.dumps(
        _to_document(baseline),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    )
    payload = f"{text}\n"

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=_TEMPORARY_SUFFIX
    )
    temporary = Path(temporary_name)
    try:
        try:
            stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        except BaseException:
            # ⚠️ 記述子の所有権は `os.fdopen` が成功したときにのみ移る。
            # 失敗したこの経路だけが、閉じ損なった記述子の漏れる余地である。
            os.close(descriptor)
            raise
        with stream:
            stream.write(payload)
            stream.flush()
            # 確定の前に中身をディスクへ届ける。差し替えだけが原子的でも、
            # 中身が届いていなければ「空の記録へ差し替えた」になりうる。
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def verify_digest(baseline: GeometryBaseline, params: ChassisParams) -> None:
    """記録の識別子が現在の寸法パラメータの識別子と一致することを検査する（要件 1.12）。

    ⚠️ **形状を再生成せずに実行できる。** 本関数は形状も数値の指標も見ず、2つの
    文字列だけを突き合わせる——だからこそ形状ライブラリ非導入の環境でも
    「寸法パラメータを変更したまま記録を更新していない」状態を検出できる
    （design.md「Testing Strategy」/ tasks.md タスク 2.5 の観測可能な完了状態）。

    ⚠️ **識別子は本モジュールが計算しない。** 現在の識別子を作るのは
    `config.parameters_digest` であり、記録側の識別子は記録が運んでくる。
    値が同じでも**出所を仮値から実測へ昇格しただけ**で識別子は動く
    （`config._canonical_payload` が出所表を含めるため）。数値の差分が1つも無い
    この経路を可視化できるのが、指標そのものではなく識別子を見ることの利点である。

    Args:
        baseline: 検査する記録。
        params: 現在の本 Spec 固有の寸法パラメータ（`load_params().chassis`）。
            ⚠️ 上流の値も組立後の観測も入力にしない（`config.parameters_digest`）。

    Returns:
        `None`。一致は「何も起きない」ことで表す——照合の成否を戻り値で運ぶと、
        呼び出し側が見落としても静かに通ってしまう。

    Raises:
        ConsistencyError: 記録と現在の識別子が食い違う場合。⚠️ **双方の識別子と
            双方の参照元を載せる**——片方しか出さない失敗は、どちらが古いのかを
            読み手に決めさせてしまう（`errors.ConsistencyError`）。
            ⚠️ 本 Spec の型であり、上流の同名例外ではない。
        ParameterError: `params` が寸法パラメータとして成立しない場合
            （`config.parameters_digest` が送出する）。
    """
    current = parameters_digest(params)
    if baseline.parameters_digest == current:
        return

    raise ConsistencyError(
        f"形状指標の記録の {_DIGEST_KEY}={baseline.parameters_digest!r} は、"
        f"現在の寸法パラメータの識別子 {current!r} と一致しない。"
        "寸法パラメータを変更したまま形状指標の記録を更新していない（要件 1.12）。"
        f"参照元は記録が {DEFAULT_BASELINE_PATH}、"
        f"寸法が {DEFAULT_DIMENSIONS_PATH} である"
        "（いずれも既定の在り処。別のパスから読んだ場合はそちらを見ること）。"
        "形状を再生成して記録を書き直すこと。"
    )
