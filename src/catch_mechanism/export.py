"""生成物の原子的な書き出し（design.md「Export」/ 要件 2.7, 3.3, 3.4, 3.5, 3.6）。

構築済みの受け口を **STEP（組立確認・図面化用）** と **STL・3MF（造形用）** の
3形式で、**単位ミリメートル**で書き出す（要件 3.3, 3.4）。出力先の既定は
`var/cad/` であり `.gitignore` 済みである——生成物はコミットせず、同じ寸法
パラメータからいつでも再生成する（要件 3.5 / design.md「Export」
Responsibilities）。

## 書き出す点数（⚠️ 1点を分割数だけ刷るのではない）

書き出す部品は `shapes.segment_part_names` が導く `rim_segment_1` … であり、
点数は `rim_geometry(params).segment_count` である。⚠️ **セグメントは互いに
同一形状とは限らない。** 後付け部品用の締結座はリング全体で
`RetentionParams.retrofit_fastener_count` 箇所であり、その数が分割数で割り切れ
ないとき（出荷値の6箇所 / 5分割）は配分が `[1, 1, 2, 1, 1]` となって
`rim_segment_3` だけ座を2つ持つ（tasks.md「Implementation Notes」タスク
3.2(b)）。したがって**部品ごとに1組ずつ、計 分割数 × 3 個のファイル**を出す。

## 造形制約の関門（要件 2.7）

要件 2.7「造形制約の検査を形状生成の一部として実行し、検査を通らない形状の
生成物を出力しない」を、本モジュールは**3段の関門**で満たす。

1. `constraints.check_material` — ⚠️ **ここが唯一の実行箇所である。**
   `PrintingConstraints.__post_init__` は `pickle` 復元や `object.__setattr__`
   を迂回できるため、`check_material` は「生成物を書き出す直前に呼ぶ関門」として
   設計されている（`constraints.check_material` の docstring）。形状側は呼ばない
2. `shapes.build_parts` — 実高さを含む `check_envelope`（`sector_envelope` の
   **解析上界**）を内包し、収まらなければ `GeometryError` を送出する
   （tasks.md タスク 3.1(a) / 3.2 の申し送り）。⚠️ 本モジュールはこれを
   **再実行しない**。同じ値を2度計算しても新しい情報は出ない
3. `_reject_parts_that_do_not_fit` — 構築されたソリッドから**実測した**外接箱
   （`BuiltPart.metrics.bbox_mm`）を `check_envelope` へ渡す。2 が見ているのは
   寸法パラメータから導いた上界であり、**実体そのものではない**。
   `check_envelope` は「⚠️ 例外を送出しない。違反は値として返り、それを失敗と
   して扱うのは呼び出し側（形状生成・書き出し、タスク 3.4）である」と定めて
   おり、その呼び出し側が本モジュールである

⚠️ 現在の形状では実測箱は常に上界以下であるため、3 が単独で発火することは無い
（半径方向の上界 `D/2` に対し実体は円環断片であり必ず細い）。それでも 3 を置く
のは、**出力の直前に実体を見る唯一の場所**だからである——将来セグメントを造形板
向けに再配置したり、形状側が上界の見積りを変えたりしたときに、収まらない部品が
そのまま出力へ抜けることを防ぐ。

## 原子性（⚠️ 本モジュールの中心。要件 3.6）

design.md「Export」Responsibilities は「**一時ディレクトリへ全ファイルを書き
終えてから出力先へ移す。** 失敗時は何も残さない」と定め、Batch Contract は
「失敗時は出力先を変更しない」と定める。**実際に提供する保証は次のとおりで
あり、これ以上を主張しない。**

- **書き出し中の失敗**: 出力先は1バイトも変わらない。ファイルは1つ残らず
  一時ディレクトリの中で作られ、全部品を書き終えるまで出力先へは触れない
- **出力先が存在しない場合の移し替え**: `os.rename` **1回**で完了する。
  ⚠️ これが本モジュールで唯一の「単一の原子操作」である
- **出力先が既に存在する場合の移し替え**: ファイル単位の `os.replace` の**列**
  であり、⚠️ **単一の原子操作ではない。** 各 `os.replace` は個別には原子的だが、
  列の途中を第三者が観測すれば新旧が混ざって見える。列の途中で失敗した場合は
  補償処理（置いた新ファイルを消し、退避した旧ファイルを戻す）で出力先を元の
  状態へ戻す。⚠️ 補償処理そのものが失敗した場合（二重障害）は出力先が混ざった
  まま残り得る。そのときは送出する例外の文面がその旨を述べる

⚠️ **一時ディレクトリは出力先の親に作る。** `tempfile.mkdtemp()` の既定
（システムの一時領域）は出力先と**別のファイルシステム**であり得る。実際にこの
リポジトリの実行環境では `/tmp` と `/mnt/c` が別デバイスであり、そこを跨ぐ
`os.rename` / `os.replace` は `EXDEV`（`Invalid cross-device link`）で失敗する。
`shutil.move` はコピー＋削除へ退避するが、⚠️ **コピーは原子的ではない**ため
上記の保証が崩れる。出力先の親に作れば同一ファイルシステムであることが保証され、
`rename` / `replace` がそのまま使える。

⚠️ **出力先ディレクトリごと差し替えない。** ディレクトリ1回の `rename` は
原子性としては最も強いが、`output_dir` に指定された場所の**無関係なファイルを
消す**。本モジュールは自分が作るファイル名だけを差し替える。

## 再実行（design.md Batch Contract の Idempotency）

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
（`shapes` のモジュール docstring / tasks.md タスク 3.2(g)）。モジュール直下へ
置くと `cad` extra 非導入の環境で本モジュールを import した時点で失敗し、上記の
造形制約の関門を CAD 非導入環境から観測できなくなって要件 5.7 が壊れる。
build123d の import は**実際に書き出す `_write_*` の内側**にある。

⚠️ `ImportError` を `CadUnavailableError` へ変換しない。design.md の Traceability
は要件 5.1-5.3 の実現先を `Cli` としており、終了コードへの対応づけを含めて
タスク 4.1 の所有である。本モジュールは `shapes` と同じく素の `ImportError` を
通す（実際には `build_parts` の側が先に送出する）。
"""

from __future__ import annotations

import locale
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from catch_mechanism.constraints import Envelope, check_envelope, check_material
from catch_mechanism.errors import GeometryError
from catch_mechanism.metrics import PartMetrics
from catch_mechanism.params import MechanismParams, PrintingConstraints
from catch_mechanism.shapes import BuiltPart, build_parts

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "EXPORT_SUFFIXES",
    "ExportedPart",
    "export_parts",
]


DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "var" / "cad"
)
"""生成物の既定の出力先（design.md「Export」Responsibilities / 要件 3.5）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。⚠️ **`var/` は `.gitignore` 済みであり、生成物はコミット
しない。** 同じ寸法パラメータからいつでも再生成できることが、生成物を版管理
しない根拠である（要件 3.5）。
"""

EXPORT_SUFFIXES: Final[tuple[str, ...]] = (".step", ".stl", ".3mf")
"""書き出す3形式の拡張子（要件 3.3）。

中間形式（STEP）が1種、メッシュ形式（STL / 3MF）が2種である。⚠️ **順序に
意味がある**——`_write_part` はこの順に書き、`ExportedPart.paths` も同じ順で
並ぶ。
"""

_STEP_TIMESTAMP: Final[str] = "1970-01-01T00:00:00"
"""STEP の表題部へ書く固定の日時。

⚠️ **実行時刻を書かせない。** `export_step` は `timestamp=None` のとき OCCT に
現在時刻を書かせるため、同一入力の再実行がバイト単位で一致しなくなる
（design.md「Export」Batch Contract の Idempotency「同一入力で何度実行しても
同じ内容」）。値そのものに意味は無く、固定であることだけに意味がある。
"""

_MESH_UUID_NAME_PREFIX: Final[str] = "urn:catch-mechanism:rim:"
"""3MF の形状 UUID を部品名から導くための接頭辞（`uuid.uuid5` の名前）。"""

_STAGING_PREFIX: Final[str] = ".catch-mechanism-staging-"
_ROLLBACK_PREFIX: Final[str] = ".catch-mechanism-rollback-"

_OUTPUT_DIR_MODE: Final[int] = 0o755
"""一時ディレクトリへ与える権限。

`tempfile.mkdtemp` は 0o700 で作る。出力先が存在しない場合は一時ディレクトリ
そのものが `rename` されて出力先になるため、そのままでは出力先が 0o700 になる。
"""


@dataclass(frozen=True, slots=True)
class ExportedPart:
    """書き出した部品1点（design.md「Export」Batch Contract の Output）。

    Batch Contract は Output を「`var/cad/<part_name>.step` / `.stl` / `.3mf`、
    **および指標**」と定めており、本型はその両方を運ぶ。

    ⚠️ **`solid` を運ばない。** 書き出しが終わればソリッドは不要であり、
    形状ライブラリの型が呼び出し側（`cli`、タスク 4.1）の署名へ漏れる理由が無い。
    形状そのものが要る呼び出し側は `shapes.build_parts` を使う。

    ⚠️ **`paths` を呼び出し側で組み立て直さない。** 部品名と拡張子から出力先の
    パスを作る規則は本モジュールの1箇所（`_part_file_names`）にあり、`cli` が
    同じ規則を書き直すと部品名の正が2箇所になる。

    本型は書き出しの**出力**であり、本モジュール自身が整合の取れた値で構築する。
    したがって構築時検証を持たない（`constraints.BuildViolation` /
    `shapes.RimGeometry` と同じ扱い）。

    Attributes:
        name: 部品名。`shapes.segment_part_names` の要素と一致する。
        metrics: 書き出したソリッドから抽出した形状指標。⚠️ **書き出しの前に
            測った値であり、ファイルを読み直した値ではない。** 記録
            （タスク 4.2）はこの値を使う。
        paths: 出力先に置かれた3つのファイル。並びは `EXPORT_SUFFIXES` と同じ。
    """

    name: str
    metrics: PartMetrics
    paths: tuple[Path, ...]


def _part_file_names(part_name: str) -> tuple[str, ...]:
    """部品名から3形式のファイル名を導く（規則の正はここ1箇所）。"""
    return tuple(f"{part_name}{suffix}" for suffix in EXPORT_SUFFIXES)


# ---------------------------------------------------------------------------
# 造形制約の関門（要件 2.7）
# ---------------------------------------------------------------------------


def _reject_parts_that_do_not_fit(
    parts: tuple[BuiltPart, ...], printing: PrintingConstraints
) -> None:
    """実測の外接箱が造形可能寸法を超える部品があれば書き出しを中止する。

    ⚠️ **`check_envelope` は例外を送出しない。** 違反は値として返り、それを失敗と
    して扱うのは呼び出し側であると `constraints.check_envelope` の docstring が
    定めている（「形状生成・書き出し、タスク 3.4」）。本関数がその呼び出し側で
    ある。

    ⚠️ **最初の違反で打ち切らない。** 全部品・全軸の違反をまとめて示す
    （要件 2.4「超過している軸と超過量を示す」/ 要件 3.6「失敗した部品名と理由を
    示す」）。`GeometryError` は違反の値型 `BuildViolation` を属性として運べない
    ため（`errors.py` は `constraints` へ依存を持たない。tasks.md タスク 1.2(b)）、
    軸と超過量は**メッセージへ載せる**。

    Args:
        parts: 構築済みの部品。`metrics.bbox_mm` を実測の外接箱として使う。
        printing: 造形機の制約。

    Raises:
        GeometryError: 収まらない部品が1つでもある場合。
    """
    violations = [
        violation
        for part in parts
        for violation in check_envelope(
            part.name, Envelope(*part.metrics.bbox_mm), printing
        )
    ]
    if not violations:
        return
    detail = "、".join(
        f"{violation.part_name} の軸 {violation.axis} が {violation.envelope_mm}mm で"
        f"上限 {violation.limit_mm}mm を {violation.excess_mm}mm 超過"
        for violation in violations
    )
    raise GeometryError(
        f"造形可能寸法に収まらない部品があるため生成物を出力しない（{detail}）。"
        "⚠️ 検査に通らない形状の生成物は1バイトも書き出さない（要件 2.7）。"
    )


# ---------------------------------------------------------------------------
# 3形式の書き出し（要件 3.3, 3.4）
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
    プロセス全体のロケールを `C` に設定したまま**戻さない**（実測: 書き出し前
    `LC_CTYPE=C.UTF-8` → 書き出し後 `C`、`locale.getpreferredencoding()` が
    `UTF-8` から `ANSI_X3.4-1968` へ変わる）。⚠️ これはライブラリ関数として
    許されない副作用である——呼び出し側（タスク 4.1 の `cli`）が書き出しの後に
    ロケール依存の入出力（`subprocess(text=True)` など）を行うと、日本語を含む
    バイト列が `ascii` で復号されて壊れる。⚠️ **実際にこの退行を踏み**、
    上流の `tests/sensing_foundation/test_sensing_cli.py` と
    `tests/trajectory_sim/test_trajectory_sim_cli.py` のサブプロセス検査が
    `UnicodeDecodeError` で落ちた。`test_the_export_leaves_the_process_locale_unchanged`
    がこれを固定する。
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
        GeometryError: 書き出しに失敗した場合。⚠️ **失敗した部品名と理由を示す**
            （要件 3.6）。`errors.GeometryError` の docstring が「書き出しの失敗
            （`OSError` を包む）」を本例外の用途として挙げている。
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
            "出力先には何も残らない（要件 3.6）。"
        ) from exc
    return names


# ---------------------------------------------------------------------------
# 出力先への移し替え（要件 3.6）
# ---------------------------------------------------------------------------


def _replace(source: Path, target: Path) -> None:
    """同一ファイルシステム内でファイル1つを原子的に置き換える。

    ⚠️ **`shutil.move` を使わない。** `shutil.move` は跨ぐときコピー＋削除へ
    退避し、コピーは原子的ではない。一時ディレクトリを出力先の親に作ることで
    同一ファイルシステムであることを保証してあるため、`os.replace` で足りる
    （モジュール docstring「原子性」）。
    """
    os.replace(source, target)


def _restore(
    destination: Path, rollback: Path, placed: list[str], displaced: list[str]
) -> None:
    """移し替えの失敗を補償し、出力先を元の状態へ戻す。

    ⚠️ **`_replace` ではなく `os.replace` を直接呼ぶ。** 補償処理は「移し替えが
    失敗した」状況で走るものであり、失敗の注入や差し替えの影響を受けてはならない。

    ⚠️ **個々の失敗を握り潰す。** ここで送出すると、元の失敗（呼び出し側が包んで
    送出する）が補償処理の失敗にすり替わる。戻し切れなかったことは呼び出し側の
    メッセージが述べる。
    """
    for name in placed:
        try:
            (destination / name).unlink()
        except OSError:  # pragma: no cover - 二重障害の経路
            pass
    for name in displaced:
        try:
            os.replace(rollback / name, destination / name)
        except OSError:  # pragma: no cover - 二重障害の経路
            pass


def _commit(staging: Path, destination: Path, file_names: tuple[str, ...]) -> None:
    """書き終えた一時ディレクトリの中身を出力先へ移す。

    提供する保証はモジュール docstring「原子性」のとおりである。⚠️ **出力先が
    既に在る場合は単一の原子操作ではない**——ファイル単位の `os.replace` の列で
    あり、失敗時は補償処理で元へ戻す。

    Args:
        staging: 全ファイルを書き終えた一時ディレクトリ。出力先と同一ファイル
            システム上にある。
        destination: 出力先。
        file_names: 移すファイル名。

    Raises:
        GeometryError: 移し替えに失敗した場合（`OSError` を包む）。
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

    rollback = Path(tempfile.mkdtemp(dir=destination.parent, prefix=_ROLLBACK_PREFIX))
    displaced: list[str] = []
    placed: list[str] = []
    try:
        for name in file_names:
            current = destination / name
            if current.exists():
                _replace(current, rollback / name)
                displaced.append(name)
            _replace(staging / name, destination / name)
            placed.append(name)
    except OSError as exc:
        _restore(destination, rollback, placed, displaced)
        raise GeometryError(
            f"生成物を出力先 {destination} へ移し替える途中で失敗した（{exc}）。"
            "出力先は移し替えの前の状態へ戻した"
            "（⚠️ 戻す処理自体が失敗していた場合に限り、新旧が混在し得る）。"
        ) from exc
    finally:
        shutil.rmtree(rollback, ignore_errors=True)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def export_parts(
    params: MechanismParams, output_dir: Path | None = None
) -> tuple[ExportedPart, ...]:
    """受け口の全部品を3形式で書き出す（要件 2.7, 3.3, 3.4, 3.5, 3.6）。

    design.md「Export」Batch Contract の実体である。⚠️ **コマンド入口ではない**
    ——`python -m catch_mechanism build` の実装はタスク 4.1（`_Boundary: Cli_`）の
    所有であり、本関数はそれが駆動する呼び出し可能物である。

    処理の順は次のとおりで、⚠️ **出力先へ触れるのは最後の1手だけである**。

    1. 造形制約の関門（`check_material` → `build_parts` → 実測箱の
       `check_envelope`）。モジュール docstring「造形制約の関門」を参照
    2. 出力先の**親**に一時ディレクトリを作り、そこへ全ファイルを書く
    3. 書き終えてから出力先へ移す（`_commit`）

    Args:
        params: 寸法パラメータの集約。
        output_dir: 出力先。省略時は `DEFAULT_OUTPUT_DIR`（`var/cad/`）。
            ⚠️ 存在しない場合は作る。存在する場合、本関数が作るファイル名以外の
            中身には触れない。

    Returns:
        部品1点につき1つの `ExportedPart`。並びと名前は
        `shapes.segment_part_names(params)` に一致する。

    Raises:
        ParameterError: 材料が許可一覧に無い場合（`check_material` からの伝播）、
            または継手の支圧面積が下限を下回る場合（`build_parts` からの伝播）。
        GeometryError: 受け口が成立しない場合・造形可能寸法に収まらない場合
            （`build_parts` および `_reject_parts_that_do_not_fit`）、
            書き出しまたは移し替えに失敗した場合。
        ImportError: 形状ライブラリ（`cad` extra）が導入されていない場合。
            ⚠️ 上記1の材料の関門は import より前に済むため、材料の誤りは CAD
            非導入の環境でも観測できる（要件 5.7）。
    """
    destination = Path(output_dir) if output_dir is not None else DEFAULT_OUTPUT_DIR

    # 関門1: 材料（⚠️ 生成物を書き出す直前の関門として設計されている）。
    check_material(params.printing)
    # 関門2: 受け口の成立と、解析上界による造形可能寸法の検査を内包する。
    parts = build_parts(params)
    # 関門3: 構築された実体の外接箱。
    _reject_parts_that_do_not_fit(parts, params.printing)

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
        shutil.rmtree(staging, ignore_errors=True)

    return tuple(
        ExportedPart(
            name=part.name,
            metrics=part.metrics,
            paths=tuple(destination / name for name in _part_file_names(part.name)),
        )
        for part in parts
    )
