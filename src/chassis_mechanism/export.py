"""生成物の原子的な書き出し（design.md `#### Export` / タスク 3.7 / 要件 1.11）。

構築済みの部品を **STEP（組立確認・図面化用の中間形式）** と **STL・3MF
（造形用のメッシュ形式）** の3形式で、**単位ミリメートル**で書き出す。出力先の
既定は `var/cad/chassis/` であり `.gitignore` 済みである——⚠️ **生成物はコミット
せず、同じ寸法パラメータからいつでも再生成する。**

## ⚠️ 書き出せるのは「関門を通ったソリッド」だけである

タスク 3.6 は、検査を通らない形状の生成物を出さない関門を `shapes` へ置いた
（`check_before_build`。公開の `build_*` は**すべて**本体の先頭でこれを通り、
`test_chassis_shapes.py::test_every_public_builder_passes_through_the_gate` が
`ast` で固定している）。本モジュールはその関門を**再実行しない**——⚠️ **代わりに、
関門を通っていないものを書き出せない形にしてある。**

- 入口が受け取るのは `shapes.build_parts` が返した `BuiltPart` だけである。
  ⚠️ **寸法パラメータを受け取らない**ため、部品名の表から書き出す名前を組み立てる
  ことが**できない**（できてしまえば、ソリッドを1つも構築せず、したがって関門を
  1度も通していない生成物が出る）
- `PART_NAMES` を参照するのは `_owned_file_names` **1箇所だけ**であり、そこは
  ⚠️ **消してよい名前を判定する**ためにしか使わない
  （`test_chassis_export.py::test_the_part_name_table_is_never_used_to_decide_what_to_write`）
- ⚠️ `BuiltPart` を構築するのは `shapes` だけである
  （`test_only_the_shape_module_constructs_built_parts`）

⚠️ **関門は形状ライブラリを1行も import しない。** したがって成立しない寸法は
`CadUnavailableError` ではなく、その寸法自身の失敗（`GeometryError` /
`ClearanceError` / 上流の `ParameterError`）で止まる。

## 原子性（⚠️ 本モジュールの中心。タスク 3.7 の2行目）

tasks.md は「一時領域へ全件を書き出してから確定し、⚠️ **失敗時に部分ファイルを
残さず既存の生成物も壊さない**」と定める。**実際に提供する保証は次のとおりで
あり、これ以上を主張しない。**

| 窓 | 出力先 | 一時領域 |
|---|---|---|
| **書き出し中の失敗（例外）** | 1バイトも変わらない | 破棄される |
| **移し替え中の失敗（例外）・出力先が無い** | 単一の `os.rename` で完了する。中間状態が存在しない | 出力先そのものになる |
| **移し替え中の失敗（例外）・出力先が在る** | ⚠️ **単一の原子操作ではない。** 補償処理で呼び出し前へ戻す。⚠️ 補償も失敗すれば、旧ファイルが在った名前は**旧ファイルを失い**（欠落するか、新ファイルが残置される）、旧ファイルが無かった名前は**新ファイルが残置される** | 破棄される。⚠️ **旧ファイルを戻せなかったときだけ残す**（その旧ファイルの在り処） |
| **プロセスの死（例外ではない）** | 書き出し中なら1バイトも変わらない。⚠️ **移し替えの列の途中なら新旧が混在し、退避済みの旧ファイルは欠落する** | ⚠️ **残骸が残る**（出力先の隣。欠落した旧ファイルはこの中に在る） |

- **書き出し中の失敗**: ファイルは1つ残らず一時ディレクトリの中で作られ、
  全部品を書き終えるまで出力先へは触れない
- **出力先が存在しない場合の移し替え**: `os.rename` **1回**で完了する。
  ⚠️ これが本モジュールで唯一の「単一の原子操作」である
- **出力先が既に存在する場合の移し替え**: ファイル単位の `os.replace` の**列**
  であり、⚠️ **単一の原子操作ではない。** 各 `os.replace` は個別には原子的だが、
  列の途中を第三者が観測すれば新旧が混ざって見える。列の途中で例外により失敗した
  場合は補償処理（退避した旧ファイルを戻し、旧ファイルが無かった名前の新ファイルを
  消す）で出力先を元の状態へ戻す。⚠️ **補償処理そのものが失敗した場合（二重障害）
  の結末は、名前ごとに次の2つへ分かれる**——⚠️ **どちらか一方だけを述べない。**

  - **呼び出し前に旧ファイルが在った名前**: 旧ファイルへ戻せない。出力先からは
    その名前が**欠落する**か、⚠️ **置いた新ファイルも取り除けなかった場合は
    新ファイルが残置される**（そのときも旧ファイルは失われている）。⚠️ **旧
    ファイルの実体は退避先（`.chassis-mechanism-rollback-*`）の中に在るため、
    そのとき退避先を消さずに残す**——消せば出力先からも退避先からも失われて
    復旧手段が無くなる（`test_a_double_fault_keeps_the_displaced_files` /
    `test_a_double_fault_separates_the_lost_old_files_from_the_new_ones`）
  - **呼び出し前に旧ファイルが無かった名前**: ⚠️ **新ファイルが出力先に残置
    される。** 退避していないのだから退避先にこの名前の実体は無く、⚠️ **手で
    戻す先が無い**（取り除くか、書き出しをやり直す）。⚠️ **この場合は退避先を
    通常どおり消す**——残せば中身の空の退避先が出力先の隣へ積もる
    （`test_a_double_fault_that_only_leaves_new_files_claims_no_loss`）

  送出する例外の文面は、この2つを**分けて**示す（欠落した名前・残置された名前・
  退避先の場所）
- **プロセスの死**: ⚠️ **`finally` も `atexit` も走らない。** 後始末を行う主体が
  居ないため、(1) 一時ディレクトリの残骸が出力先の**隣**に残り
  （⚠️ 出力先の**中**ではないため生成物の一覧は汚れない。残骸を入力として読む
  経路も無いので次回の実行は成功する）、(2) 移し替えの列の途中で死んだ場合は
  出力先に新旧が混在し、⚠️ **退避済みの旧ファイルは出力先から欠落する**
  （実体は退避先の残骸の中に在る）。⚠️ **この2点は設計上の限界であり、隠さない**
  ——生成物は版管理せず同じ入力から再生成できるため、復旧手段は「もう一度
  書き出す」ことである

⚠️ **一時ディレクトリは出力先の親に作る。** `tempfile.mkdtemp()` の既定
（システムの一時領域）は出力先と**別のファイルシステム**であり得る。実際にこの
リポジトリの実行環境では `/tmp` と `/mnt/c` が別デバイスであり、そこを跨ぐ
`os.rename` / `os.replace` は `EXDEV`（`Invalid cross-device link`）で失敗する。
`shutil.move` はコピー＋削除へ退避するが、⚠️ **コピーは原子的ではない**ため
上記の保証が崩れる。出力先の親に作れば同一ファイルシステムであることが保証され、
`rename` / `replace` がそのまま使える。

⚠️ **出力先ディレクトリごと差し替えない。** ディレクトリ1回の `rename` は
原子性としては最も強いが、`directory` に指定された場所の**無関係なファイルを
消す**。本モジュールが触るのは⚠️ **自分が所有する名前だけ**である。

## 前回の生成物の掃除（⚠️ 所有する名前に限る）

分割数は導出であって設定値ではない（要件 2.1）。寸法を変えると断片の点数が
**減る**ことがあり、そのとき前回の `adapter_segment_9.stl` が出力先に残る。
⚠️ **残れば、設計に無い部品がそのまま造形へ回る**——「同じ入力から再生成した
出力先」が入力を表していないことになる。そこで、⚠️ **本モジュールが作り得る名前
（`PART_NAMES` の部品名＋連番＋3形式の拡張子）でありながら今回作られなかった
もの**を、移し替えと同じ補償の対象として取り除く。⚠️ **それ以外の名前には
触れない**（手で置いたメモも、拡張子の違うファイルも残る）。

## 再実行（Batch Contract の Idempotency）

同一入力の再実行は同じ内容を出す。そのために STEP の表題部の日時
（`_STEP_TIMESTAMP`）と 3MF の形状 UUID（部品名から `uuid5` で導く）を固定して
ある——既定のままでは実行時刻と乱数が入り、内容が実行ごとに変わる。
⚠️ **3MF はそれでもバイト単位では一致しない。** lib3mf がラッパの object /
component / build item へ書き出しのたびに乱数 UUID を振り、build123d の公開
API から止められるのは形状1つぶんの `uuid_value` だけだからである。形状の記述
そのものは一致する（`test_re_running_the_export_reproduces_the_same_content`）。

## 形状ライブラリの import（⚠️ 遅延）

design.md「Allowed Dependencies」は `shapes` / `export` に build123d の import を
**許す**が、⚠️ **許可と「モジュール読み込み時に必要にしてよい」は別である**
（`shapes` のモジュール docstring）。モジュール直下へ置くと `cad` extra 非導入の
環境で本モジュールを import した時点で失敗し、出力先の既定・前提条件・上記の
静的な固定を CAD 非導入環境から観測できなくなる。build123d の import は
**実際に書き出す `_write_*` の内側**にある。
"""

from __future__ import annotations

import locale
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from chassis_mechanism.errors import GeometryError
from chassis_mechanism.shapes import PART_NAMES, BuiltPart

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "EXPORT_SUFFIXES",
    "INTERMEDIATE_SUFFIXES",
    "MESH_SUFFIXES",
    "ExportedPart",
    "export_parts",
]


DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "var" / "cad" / "chassis"
)
"""生成物の既定の出力先（design.md `#### Export` Responsibilities）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。⚠️ **`var/` は `.gitignore` 済みであり、生成物はコミット
しない。** 同じ寸法パラメータからいつでも再生成できることが、生成物を版管理
しない根拠である（タスク 3.7 の3行目）。⚠️ 上流 `catch_mechanism` の出力先は
`var/cad/` であり、本 Spec はその下の `chassis/` を使う——同じ場所へ混ぜると、
どちらの Spec の生成物か区別できなくなる。
"""

INTERMEDIATE_SUFFIXES: Final[tuple[str, ...]] = (".step",)
"""組立確認と図面化に用いる中間形式（タスク 3.7 の1行目）。

STEP は境界表現をそのまま運ぶため、寸法を測り直せる・断面を採れる・他の CAD へ
持ち込める。⚠️ **メッシュ形式では代用できない**——三角形へ落ちた時点で円筒は
多角柱になり、図面の寸法線が実物と合わなくなる。
"""

MESH_SUFFIXES: Final[tuple[str, ...]] = (".stl", ".3mf")
"""造形に用いるメッシュ形式（タスク 3.7 の1行目）。

STL はスライサが必ず読める最小公倍数であり、3MF は単位と部品名を保持する。
⚠️ **STL だけにしない**——STL には単位の欄が無く、mm 以外で読むスライサに対して
形状側から主張する手段が無い（`_write_stl` の docstring）。
"""

EXPORT_SUFFIXES: Final[tuple[str, ...]] = INTERMEDIATE_SUFFIXES + MESH_SUFFIXES
"""書き出す3形式の拡張子。

⚠️ **順序に意味がある**——`_write_part` はこの順に書き、`ExportedPart.file_names`
も同じ順で並ぶ。中間形式が先、メッシュ形式が後である。
"""

_STEP_TIMESTAMP: Final[str] = "1970-01-01T00:00:00"
"""STEP の表題部へ書く固定の日時。

⚠️ **実行時刻を書かせない。** `export_step` は `timestamp=None` のとき OCCT に
現在時刻を書かせるため、同一入力の再実行がバイト単位で一致しなくなる（Batch
Contract の Idempotency）。値そのものに意味は無く、固定であることだけに意味がある。
"""

_MESH_UUID_NAME_PREFIX: Final[str] = "urn:chassis-mechanism:part:"
"""3MF の形状 UUID を部品名から導くための接頭辞（`uuid.uuid5` の名前）。"""

_STAGING_PREFIX: Final[str] = ".chassis-mechanism-staging-"
_ROLLBACK_PREFIX: Final[str] = ".chassis-mechanism-rollback-"

_OUTPUT_DIR_MODE: Final[int] = 0o755
"""一時ディレクトリへ与える権限。

`tempfile.mkdtemp` は 0o700 で作る。出力先が存在しない場合は一時ディレクトリ
そのものが `rename` されて出力先になるため、そのままでは出力先が 0o700 になる。
"""


@dataclass(frozen=True, slots=True)
class ExportedPart:
    """書き出した部品1点（design.md `#### Export` Service Interface）。

    ⚠️ **`solid` も指標も運ばない。** 書き出しが終わればソリッドは不要であり、
    形状ライブラリの型が呼び出し側（`cli`、タスク 4.1）の署名へ漏れる理由が無い。
    指標が要る呼び出し側は `shapes.build_parts` の戻り値（`BuiltPart.metrics`）を
    そのまま持っている——⚠️ **本モジュールへ渡した当人である。**

    ⚠️ **`file_names` を呼び出し側で組み立て直さない。** 部品名と拡張子から
    ファイル名を作る規則は本モジュールの1箇所（`_part_file_names`）にあり、`cli`
    が同じ規則を書き直すと規則の正が2箇所になる。

    本型は書き出しの**出力**であり、本モジュール自身が整合の取れた値で構築する。
    したがって構築時検証を持たない（`shapes.BuiltPart` と同じ扱い）。

    Attributes:
        name: 部品名。渡された `BuiltPart.name` と一致する。
        file_names: 出力先に置かれた3つのファイル名。⚠️ **ディレクトリを含まない**
            ——出力先は呼び出し側が指定した（あるいは既定の）1つであり、名前ごとに
            違う場所へ置くことはない。並びは `EXPORT_SUFFIXES` と同じである。
    """

    name: str
    file_names: tuple[str, ...]


def _part_file_names(part_name: str) -> tuple[str, ...]:
    """部品名から3形式のファイル名を導く（規則の正はここ1箇所）。"""
    return tuple(f"{part_name}{suffix}" for suffix in EXPORT_SUFFIXES)


# ---------------------------------------------------------------------------
# 前提条件（⚠️ 1バイトも書かずに拒む）
# ---------------------------------------------------------------------------


def _require_exportable(parts: tuple[BuiltPart, ...]) -> None:
    """書き出せる部品の並びであることを確かめる。

    ⚠️ **出力先へ触れる前に済ませる。** ここで拒んだ呼び出しは、出力先を作りも
    しないし変えもしない。

    Raises:
        GeometryError: 部品が空、`BuiltPart` でないもの・ソリッドを持たないものが
            混じっている、または部品名が重複している場合。
    """
    if not parts:
        raise GeometryError(
            "書き出す部品が1点も無い（design.md `#### Export` Preconditions）。"
            "⚠️ 0件の書き出しを成功にしない——出力先が空のまま成功が返れば、"
            "呼び出し側は生成物が揃ったと読む。"
        )
    for part in parts:
        if not isinstance(part, BuiltPart) or part.solid is None:
            name = getattr(part, "name", part)
            raise GeometryError(
                f"{name!r} はソリッドを伴わないため書き出せない。"
                "⚠️ 書き出せるのは `shapes.build_parts` が返した部品だけである"
                "——名前と指標だけの記録は関門（`check_before_build`）を1度も"
                "通っておらず、そこから生成物を出せば無検査の造形物が出る。"
            )
    seen: set[str] = set()
    duplicated: set[str] = set()
    for part in parts:
        if part.name in seen:
            duplicated.add(part.name)
        seen.add(part.name)
    if duplicated:
        raise GeometryError(
            f"部品名が重複している（{'、'.join(sorted(duplicated))}）。"
            "⚠️ 同名を黙って上書きすると、後から書いた1点だけが残り、"
            "造形すべき点数が足りないことに誰も気づかない。"
        )


# ---------------------------------------------------------------------------
# 3形式の書き出し（⚠️ 形状ライブラリの import はこの節の内側だけ）
# ---------------------------------------------------------------------------


def _write_step(solid: object, path: Path) -> None:
    """STEP（組立確認・図面化用の中間形式）を書く。単位は `Unit.MM` に固定する。"""
    import build123d

    build123d.export_step(
        solid,  # type: ignore[arg-type]
        path,
        unit=build123d.Unit.MM,
        timestamp=_STEP_TIMESTAMP,
    )


def _write_stl(solid: object, path: Path) -> None:
    """STL（造形用メッシュ）を書く。

    ⚠️ **STL には単位の欄が無い。** `export_stl` に単位の引数が無いのはそのため
    であり、頂点座標はモデルの座標値（mm）がそのまま入る。単位が mm であることは
    座標の数値としてしか観測できない
    （`test_stl_vertex_coordinates_are_in_millimetres`）。
    """
    import build123d

    build123d.export_stl(solid, path)  # type: ignore[arg-type]


def _write_3mf(solid: object, part_name: str, path: Path) -> None:
    """3MF（造形用メッシュ）を書く。単位は `Unit.MM` に固定する。

    ⚠️ 形状の UUID を部品名から決定的に導く。既定では書き出しのたびに乱数の
    UUID が振られ、同一入力の再実行が同じ内容にならない。

    ⚠️ **ロケールを退避して復元する。** `Mesher.write` が呼ぶ lib3mf は
    プロセス全体のロケールを `C` に設定したまま**戻さない**。⚠️ これはライブラリ
    関数として許されない副作用であり、⚠️ **上流で実際にこの退行を踏んでいる**
    ——書き出しの後にロケール依存の入出力（`subprocess(text=True)` など）を行うと
    日本語を含むバイト列が `ascii` で復号されて壊れる
    （`test_the_export_leaves_the_process_locale_unchanged` が固定する）。
    """
    import build123d

    mesher = build123d.Mesher(unit=build123d.Unit.MM)
    mesher.add_shape(
        solid,  # type: ignore[arg-type]
        part_number=part_name,
        uuid_value=uuid.uuid5(uuid.NAMESPACE_URL, _MESH_UUID_NAME_PREFIX + part_name),
    )
    previous_locale = locale.setlocale(locale.LC_ALL)
    try:
        mesher.write(path)
    finally:
        locale.setlocale(locale.LC_ALL, previous_locale)


def _write_part(part: BuiltPart, directory: Path) -> tuple[str, ...]:
    """部品1点の3形式を `directory` へ書き、書いたファイル名を返す。

    Raises:
        GeometryError: 書き出しに失敗した場合。⚠️ **失敗した部品名と理由を示す。**
            build123d 自身は書き込み失敗を `RuntimeError` で報告するため
            （`export_step` の「Failed to write STEP file」）、これも包む。
    """
    names = _part_file_names(part.name)
    try:
        _write_step(part.solid, directory / names[0])
        _write_stl(part.solid, directory / names[1])
        _write_3mf(part.solid, part.name, directory / names[2])
    except (OSError, RuntimeError) as exc:
        raise GeometryError(
            f"部品 {part.name!r} の書き出しに失敗した（{exc}）。"
            "⚠️ 途中まで書き出したファイルは一時ディレクトリごと破棄され、"
            "出力先には何も残らない。"
        ) from exc
    return names


# ---------------------------------------------------------------------------
# 出力先への移し替え（⚠️ 提供する保証はモジュール docstring「原子性」のとおり）
# ---------------------------------------------------------------------------


def _replace(source: Path, target: Path) -> None:
    """同一ファイルシステム内でファイル1つを原子的に置き換える。

    ⚠️ **`shutil.move` を使わない。** `shutil.move` は跨ぐときコピー＋削除へ
    退避し、コピーは原子的ではない。一時ディレクトリを出力先の親に作ることで
    同一ファイルシステムであることを保証してあるため、`os.replace` で足りる。
    """
    os.replace(source, target)


def _owned_file_names(destination: Path, keep: frozenset[str]) -> tuple[str, ...]:
    """出力先にある「自分が作り得る名前」のうち、今回作らないものを返す。

    前回の生成物の掃除である（モジュール docstring「前回の生成物の掃除」）。
    ⚠️ **所有していない名前は1つも含めない**——手で置いたメモも、拡張子の違う
    ファイルも、ここには現れない。

    ⚠️ **本モジュールで `PART_NAMES` を参照するのはここだけである**
    （`test_the_part_name_table_is_never_used_to_decide_what_to_write` が `ast` で
    固定する）。部品名の表は「**消してよい名前**の判定」にしか使わない——書き出す
    名前は渡された `BuiltPart.name` からしか作らない。表から作れてしまえば、
    ソリッドを1つも構築せず、したがって関門を1度も通していない生成物が出る。

    Args:
        destination: 出力先。
        keep: 今回書き出すファイル名。

    Returns:
        取り除くべきファイル名（並びは辞書順で決定的）。
    """
    if not destination.is_dir():
        return ()
    # 部品名＋任意の連番＋3形式の拡張子。⚠️ 連番の有無は `shapes.part_names` の
    # 規約（分割しない部品は番号を持たない）に合わせる。
    # ⚠️ **末尾は `$` ではなく `\\Z` で閉じる。** `$` は文字列の末尾**または末尾の
    # 改行の直前**に合う。ファイル名に改行を含めることは POSIX では合法であり、
    # `$` のままだと手で置いた `hub_plate.stl\n` を「所有する名前」と判定して
    # 黙って消す（`\\Z` は文字列の末尾にしか合わない）。
    owned = re.compile(
        r"^(?:{names})(?:_[0-9]+)?(?:{suffixes})\Z".format(
            names="|".join(re.escape(name) for name in PART_NAMES),
            suffixes="|".join(re.escape(suffix) for suffix in EXPORT_SUFFIXES),
        )
    )
    return tuple(
        sorted(
            entry.name
            for entry in destination.iterdir()
            if entry.is_file()
            and entry.name not in keep
            and owned.match(entry.name) is not None
        )
    )


def _restore(
    destination: Path, rollback: Path, placed: list[str], displaced: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """移し替えの失敗を補償し、出力先を元の状態へ戻す。

    ⚠️ **`_replace` ではなく `os.replace` を直接呼ぶ。** 補償処理は「移し替えが
    失敗した」状況で走るものであり、失敗の注入や差し替えの影響を受けてはならない。

    ⚠️ **個々の失敗を握り潰すが、黙らない。** ここで送出すると、元の失敗
    （呼び出し側が包んで送出する）が補償処理の失敗にすり替わる。代わりに
    **戻し切れなかった名前を返す**——⚠️ 呼び出し側はそれを見て退避先を残し、
    文面でその場所を示す。⚠️ **握り潰したうえで何も返さないと、退避先が
    `finally` で消され、旧ファイルが出力先からも退避先からも消える**
    （＝復旧手段の無いデータ喪失。`test_a_double_fault_keeps_the_displaced_files`）。

    ⚠️ **退避した旧ファイルを先に戻す。** `os.replace` は同名の新ファイルごと
    上書きするため、先に消す必要が無い——「消してから戻す」順は、消せたのに
    戻せない窓（出力先からその名前が消える窓）を自分で作る。

    ⚠️ **結末は1つではない。** 戻し切れなかった名前を1つの集合にまとめると、
    出力先の状態も復旧手段も違うものが同じ文面で語られる（旧ファイルが無かった
    名前まで「欠落した・退避先から手で戻せ」と述べることになり、⚠️ **どちらも
    嘘になる**）。互いに素な3つに分けて返す。

    Returns:
        `(missing, overwritten, leftover)`（各々辞書順）。3つとも空であれば出力先は
        呼び出し前の状態へ戻っている。

        - `missing`: ⚠️ **旧ファイルを戻せず、新ファイルも残らなかった名前。**
          出力先にその名前は無い。実体は `rollback` の中に在る
        - `overwritten`: ⚠️ **旧ファイルを戻せず、新ファイルが残った名前。**
          出力先はその名前で**新しい内容**を持つ。実体は `rollback` の中に在る
        - `leftover`: ⚠️ **呼び出し前に同名のファイルが無く、置いた新ファイルを
          取り除けなかった名前。** 出力先に新しいファイルが残る。⚠️ `rollback`
          にこの名前の実体は無い（戻す先が無い）

        `missing` と `overwritten` のどちらかが空でない場合、退避した旧ファイルは
        `rollback` の中に残っている——⚠️ **呼び出し側はそこを消してはならない。**
        `leftover` だけの場合、`rollback` は空であり通常どおり消してよい。
    """
    displaced_names = set(displaced)
    unrestored: set[str] = set()
    remaining: set[str] = set()
    for name in displaced:
        try:
            os.replace(rollback / name, destination / name)
        except OSError:
            unrestored.add(name)
    for name in placed:
        if name in displaced_names and name not in unrestored:
            continue  # 上書きで旧ファイルへ戻っている。消してはならない。
        try:
            (destination / name).unlink()
        except OSError:
            # ⚠️ 新ファイルが出力先に残る。同名の旧ファイルを退避してあったか
            # どうかで結末が分かれる（`unrestored` との積・差）。
            remaining.add(name)
    return (
        tuple(sorted(unrestored - remaining)),
        tuple(sorted(unrestored & remaining)),
        tuple(sorted(remaining - unrestored)),
    )


def _commit(staging: Path, destination: Path, file_names: tuple[str, ...]) -> None:
    """書き終えた一時ディレクトリの中身を出力先へ移す。

    提供する保証はモジュール docstring「原子性」のとおりである。⚠️ **出力先が
    既に在る場合は単一の原子操作ではない**——ファイル単位の `os.replace` の列で
    あり、例外による失敗時は補償処理で元へ戻す。

    ⚠️ **前回の生成物の掃除を先に行う。** 掃除も補償の対象であり、掃除のあとで
    移し替えが失敗した場合には退けたファイルも元へ戻る
    （`test_stale_output_comes_back_when_the_move_fails`）。

    Args:
        staging: 全ファイルを書き終えた一時ディレクトリ。出力先と同一ファイル
            システム上にある。
        destination: 出力先。
        file_names: 移すファイル名。

    Raises:
        GeometryError: 移し替えに失敗した場合（`OSError` を包む）。⚠️ **補償処理
            そのものも失敗した場合（二重障害）は、結末を分けて文面が示す**——
            旧ファイルを戻せなかった名前には退避先の場所を示し、⚠️ **そのときだけ
            退避先を消さない。** 呼び出し前に存在しなかった名前の新ファイルが
            残置された場合は、その名前を挙げるだけで退避先を示さない（戻すべき
            旧ファイルが無いため。示せば嘘になり、残せば空の退避先が積もる）。
    """
    if not destination.exists():
        try:
            # ⚠️ 本モジュールで唯一の「単一の原子操作」。出力先が無ければ
            # ディレクトリ1回の rename で移し替えが完了し、中間状態が存在しない。
            os.rename(staging, destination)
            return
        except OSError:
            # 並行する実行が出力先を作った場合にのみここへ来る。ファイル単位へ落とす。
            pass

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GeometryError(
            f"出力先 {destination} を用意できなかった（{exc}）。"
            "生成物は書き出していない。"
        ) from exc

    stale = _owned_file_names(destination, frozenset(file_names))
    rollback = Path(tempfile.mkdtemp(dir=destination.parent, prefix=_ROLLBACK_PREFIX))
    displaced: list[str] = []
    placed: list[str] = []
    keep_rollback = False
    try:
        for name in stale:
            _replace(destination / name, rollback / name)
            displaced.append(name)
        for name in file_names:
            current = destination / name
            if current.exists():
                _replace(current, rollback / name)
                displaced.append(name)
            _replace(staging / name, destination / name)
            placed.append(name)
    except OSError as exc:
        missing, overwritten, leftover = _restore(
            destination, rollback, placed, displaced
        )
        if missing or overwritten or leftover:
            # ⚠️ **二重障害。** 結末は名前ごとに違う。⚠️ **1つの文面へまとめない**
            # ——出力先の状態も復旧手段も違うものを同じ言葉で述べれば、そのどれかが
            # 必ず嘘になる。
            details: list[str] = []
            if missing:
                details.append(f"出力先からは {'、'.join(missing)} が欠落している。")
            if overwritten:
                details.append(
                    f"出力先の {'、'.join(overwritten)} は新しいファイルが"
                    "残置されたままであり、旧ファイルへ戻っていない。"
                )
            if missing or overwritten:
                # ⚠️ **退避先を消さずに残し、その場所を文面で示す**（消せば、
                # 出力先からも退避先からも消えて復旧手段が無くなる）。
                keep_rollback = True
                details.append(
                    f"⚠️ **これらの旧ファイルは {rollback} に残してある**"
                    "——消さずに手で戻すこと。"
                )
            if leftover:
                # ⚠️ **退避先を示さない。** これらは呼び出し前に同名のファイルが
                # 無かった名前であり、退避先に実体は1バイトも無い。ここで退避先を
                # 残すと、中身の空の退避先が出力先の隣へ積もるだけである。
                details.append(
                    f"⚠️ 出力先には新しいファイル {'、'.join(leftover)} が"
                    "残置されている——これらは呼び出し前に存在しなかった名前で"
                    "あり、⚠️ **戻すべき旧ファイルは無い**（取り除くか、"
                    "書き出しをやり直すこと）。"
                )
            raise GeometryError(
                f"生成物を出力先 {destination} へ移し替える途中で失敗し（{exc}）、"
                "⚠️ **戻す処理そのものも失敗した（二重障害）。**" + "".join(details)
            ) from exc
        raise GeometryError(
            f"生成物を出力先 {destination} へ移し替える途中で失敗した（{exc}）。"
            "出力先は移し替えの前の状態へ戻した。"
        ) from exc
    finally:
        if not keep_rollback:
            shutil.rmtree(rollback, ignore_errors=True)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def export_parts(
    parts: tuple[BuiltPart, ...], directory: Path | None = None
) -> tuple[ExportedPart, ...]:
    """構築済みの全部品を3形式で原子的に書き出す（要件 1.11 / タスク 3.7）。

    design.md `#### Export` Batch Contract の実体である。⚠️ **コマンド入口では
    ない**——`python -m chassis_mechanism build` の実装はタスク 4.1（`Cli`）の
    所有であり、本関数はそれが駆動する呼び出し可能物である。

    処理の順は次のとおりで、⚠️ **出力先へ触れるのは最後の1手だけである**。

    1. 前提条件（`_require_exportable`）。⚠️ **関門はここには無い**——関門は
       `shapes.check_before_build` に1箇所だけあり、`parts` を作った
       `shapes.build_parts` が既に通している。⚠️ 本関数は寸法パラメータを
       受け取らないため、関門を迂回した生成物を作ることが**できない**
    2. 出力先の**親**に一時ディレクトリを作り、そこへ全ファイルを書く
    3. 書き終えてから出力先へ移す（`_commit`）

    Args:
        parts: `shapes.build_parts` の戻り値。⚠️ **空にできない。**
        directory: 出力先。省略時は `DEFAULT_OUTPUT_DIR`（`var/cad/chassis/`）。
            ⚠️ 存在しない場合は作る。存在する場合、⚠️ **自分が所有する名前
            （`_owned_file_names`）以外には触れない。**

    Returns:
        部品1点につき1つの `ExportedPart`。並びと名前は `parts` に一致する。

    Raises:
        GeometryError: 前提条件を満たさない場合、書き出しまたは移し替えに失敗した
            場合。
        ImportError: 形状ライブラリ（`cad` extra）が導入されていない場合。⚠️ 実際
            には `parts` を作る `shapes.build_parts` の側が先に
            `CadUnavailableError` を送出するため、本関数がこれを送出する経路は
            事実上無い。
    """
    _require_exportable(parts)
    destination = Path(directory) if directory is not None else DEFAULT_OUTPUT_DIR

    # ⚠️ 一時ディレクトリは出力先の**親**に作る（同一ファイルシステムの保証）。
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=destination.parent, prefix=_STAGING_PREFIX))
    try:
        file_names: list[str] = []
        for part in parts:
            file_names.extend(_write_part(part, staging))
        try:
            os.chmod(staging, _OUTPUT_DIR_MODE)
        except OSError:  # pragma: no cover - 権限を持たないファイルシステム
            pass
        _commit(staging, destination, tuple(file_names))
    finally:
        # 成功時は `os.rename` で消えているか、中身が移し終わっている。
        # ⚠️ **プロセスが死んだ場合はここへ到達しない**（モジュール docstring）。
        shutil.rmtree(staging, ignore_errors=True)

    return tuple(
        ExportedPart(name=part.name, file_names=_part_file_names(part.name))
        for part in parts
    )
