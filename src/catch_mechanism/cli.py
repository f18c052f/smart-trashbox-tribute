"""サブコマンド入口（design.md「Cli」/ 要件 3.2, 4.3, 5.3, 6.2, 6.7, 7.5）。

`python -m catch_mechanism <subcommand>` の実体である。design.md「Cli」
Service Interface が定める4サブコマンドを持つ。

| サブコマンド | 動作 | 形状ライブラリ |
|---|---|---|
| `build` | 形状を生成し STEP / STL / 3MF と指標を出力（`--update-baseline` で記録を更新） | 必要 |
| `check` | 記録済み指標との照合（`--digest-only` は識別子の照合のみ） | `--digest-only` は不要 |
| `select` | 候補を選定基準で評価し、適合・不適合と理由を出力 | 不要 |
| `tolerance` | 位置許容誤差を導出して記録へ書き出す（`--check` で設定値と比較） | 不要 |

## 終了コード（本モジュールが本 Spec の唯一の決定箇所である）

design.md「Cli」/「Error Categories and Responses」:

- `0` 正常
- `1` 検査の不一致（`ConsistencyError`、および `compare_metrics` が返す
  `MetricsMismatch` が空でないこと）
- `2` 使い方の誤り・入力不正（`ParameterError` / `SelectionError` / `GeometryError`）
- `3` 形状生成の環境が無い（`CadUnavailableError`、および形状層から届く裸の
  `ImportError`）

⚠️ **`SelectionError` の割り当てはここで確定させる。** tasks.md
「Implementation Notes」タスク 1.2(a) は、design.md「Error Categories and
Responses」に対応行が無く `errors.py` の docstring からの外挿である旨を記録し、
**確定を本タスク（4.1）の所有**とした。決定は **2** である。`SelectionError` は
選定の**入力**の誤り（基準ファイルの未知の項目名、候補の諸元の欠損、存在しない
候補の名指し）だけを表し、それは「使い方の誤り・入力不正」そのものである。
候補が基準を満たさないこと（不適合）は `CandidateVerdict.accepted = False` と
いう**値**であってこの経路を通らないため、2 に割り当てても「検査の不一致」
（1）と混ざらない。

⚠️ **形状ライブラリの不在を成功として黙って読み飛ばさない**（要件 5.3）。
生成したつもりで生成物が無い状態は、造形の直前まで気付けない事故になる。
`export.py` は design.md の Traceability が要件 5.1〜5.3 の実現手段を `Cli` へ
置いているため、非導入を**裸の `ImportError` のまま**通す。それを
`CadUnavailableError`（終了コード 3）へ写すのは本モジュールの責務である。
⚠️ 2（入力の誤り）へ落としてはならない——入力は正しく、導入すれば直る。

## 遅延 import

⚠️ **`shapes` / `export` は関数の内側で import する**（design.md
「Dependency Direction」/ tasks.md タスク 1.6(c)）。モジュール直下に書くと
`import catch_mechanism.cli` が形状ライブラリへ到達しうる形になり、
`tests/catch_mechanism/test_catch_boundaries.py` の
`find_module_level_cad_imports` が落ちる。⚠️ 形状ライブラリ自身（`build123d` /
`OCP`）は**関数内であっても** import してはならない（`find_cad_import_violations`
は遅延 import も対象にする）。したがって本モジュールは形状ライブラリの有無を
自分で調べず、形状層の呼び出しが投げる `ImportError` を観測して判断する。

## ロケール

⚠️ `build` は 3MF の書き出しで lib3mf を呼ぶ。lib3mf はプロセスのロケールを
`C` に変えたまま戻さない（tasks.md「Implementation Notes」タスク 3.4(a)）。
**退避・復元は `export.py` の `_write_3mf` が内部で行っており、`cli` は
`export_parts` 経由でしか `Mesher.write` へ到達しないため、本モジュール側の
ガードは要らない。** この事実は
`test_catch_cli.py::test_build_then_check_round_trip_with_the_shape_library`
が `build` の前後でロケールを突き合わせて固定している。⚠️ 将来 `cli` が
`export_parts` を通さずに `Mesher.write` へ到達する経路を作る場合は、そこで
同じ退避・復元が要る。

## 出力先

`--output-dir` の既定は `None` であり、解決は `export.DEFAULT_OUTPUT_DIR` に
委ねる。⚠️ `cli` が既定値を書き写すにはモジュール直下で CAD 層を import せねば
ならず、上記の遅延 import の規律に反する。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

from catch_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    load_params,
    parameters_digest,
)
from catch_mechanism.errors import (
    CadUnavailableError,
    CatchMechanismError,
    ConsistencyError,
    GeometryError,
    ParameterError,
    SelectionError,
)
from catch_mechanism.metrics import (
    ABSENT,
    DEFAULT_BASELINE_PATH,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    MetricsMismatch,
    PartMetrics,
    compare_metrics,
    load_baseline,
    write_baseline,
)
from catch_mechanism.params import MechanismParams
from catch_mechanism.selection import (
    CandidateVerdict,
    evaluate_candidate,
    load_candidates,
    load_criteria,
)
from catch_mechanism.tolerance import (
    DEFAULT_DERIVATION_PATH,
    ToleranceDerivation,
    derive_position_tolerance,
    dump_derivation,
)

__all__ = [
    "EXIT_OK",
    "EXIT_MISMATCH",
    "EXIT_USAGE",
    "EXIT_CAD_UNAVAILABLE",
    "EXIT_CODE_BY_ERROR",
    "ILLUSTRATIVE_PREFIX",
    "ILLUSTRATIVE_LABEL",
    "VANISHED_LABEL",
    "APPEARED_LABEL",
    "build_parser",
    "exit_code_for",
    "main",
]

PROGRAM: Final[str] = "catch_mechanism"
"""メッセージの接頭辞。⚠️ 標準エラーの1行目からどのコマンドの失敗かが読める。"""

# ---------------------------------------------------------------------------
# 終了コード（design.md「Cli」）
# ---------------------------------------------------------------------------

EXIT_OK: Final[int] = 0
"""正常終了。"""

EXIT_MISMATCH: Final[int] = 1
"""検査の不一致（記録と現在が食い違う）。"""

EXIT_USAGE: Final[int] = 2
"""使い方の誤り・入力不正。⚠️ `argparse` の使用法エラーもこの値である。"""

EXIT_CAD_UNAVAILABLE: Final[int] = 3
"""形状生成の環境が無い。⚠️ **成功にしない**（要件 5.3）。"""

EXIT_CODE_BY_ERROR: Final[Mapping[type, int]] = {
    ConsistencyError: EXIT_MISMATCH,
    ParameterError: EXIT_USAGE,
    SelectionError: EXIT_USAGE,
    GeometryError: EXIT_USAGE,
    CadUnavailableError: EXIT_CAD_UNAVAILABLE,
}
"""例外の系統から終了コードへの表（本モジュール docstring「終了コード」）。

⚠️ **5系統は互いに素である**（`errors.py`: 「系統の間に継承関係を作ると
`except` の順序で終了コードが変わってしまう」）。互いに素であることに依って、
本表は順序に依らない `isinstance` の走査で引ける。
`test_catch_cli.py::test_error_families_are_pairwise_disjoint` と
`test_exit_code_table_covers_every_error_family` が両方を固定する。
"""

# ---------------------------------------------------------------------------
# 出力の印
# ---------------------------------------------------------------------------

ILLUSTRATIVE_PREFIX: Final[str] = "illustrative-"
"""例示の非適合例（`role: "illustrative_non_example"`）の識別子接頭辞。

⚠️ tasks.md「Implementation Notes」タスク 2.2(d): `candidates.json` には
roadmap に無い**例示**が2件ある。`load_candidates` は `role` を戻り値に載せない
（design.md の `Candidate` に `role` が無い）ため、**識別子の接頭辞が
唯一の手掛かり**である。⚠️ 実売を調査した品と取り違えると、調べていない品を
買いに行く事故になる。
"""

ILLUSTRATIVE_LABEL: Final[str] = "[例示]"
"""例示の候補に付ける印。"""

ACCEPTED_LABEL: Final[str] = "[適合]"
REJECTED_LABEL: Final[str] = "[不適合]"

VANISHED_LABEL: Final[str] = "[部品が消えた]"
"""記録にあって再生成に無い部品の印（`PRESENCE_FIELD` の不一致）。"""

APPEARED_LABEL: Final[str] = "[部品が増えた]"
"""再生成にあって記録に無い部品の印（`PRESENCE_FIELD` の不一致）。"""

DEVIATION_LABEL: Final[str] = "[乖離]"
"""体積・境界箱・立体数の不一致の印。"""

# ---------------------------------------------------------------------------
# 記録の既定値
# ---------------------------------------------------------------------------

_SHAPE_LIBRARY_DISTRIBUTION: Final[str] = "build123d"
"""形状ライブラリの配布名。

⚠️ **これは import ではない。** `importlib.metadata.version` は配布メタデータを
読むだけでモジュールを実行しないため、本モジュールが形状ライブラリを import
しない規律（本モジュール docstring「遅延 import」）を破らない。版が要るのは
`GeometryBaseline.generator_version` の記録時だけであり、その時点では形状生成が
成功しているため配布は必ず存在する。
"""

_DEFAULT_VOLUME_REL_TOLERANCE: Final[float] = 1e-6
"""記録を新規に作るときの体積の相対許容差。

⚠️ **1e-7 を下回らせない。** 同一ライブラリが同一形状に対して出す求積誤差の実測が
相対 1.3e-8 であり、0 や 1e-9 を記録すると build123d / OCCT の版差で確実に破綻する
（tasks.md「Implementation Notes」タスク 3.3）。
⚠️ 既存の記録があるときはその許容差を引き継ぐ——記録の内容はタスク 4.2 の所有で
あり、`--update-baseline` が黙って上書きしてよい値ではない。
"""

_DEFAULT_BBOX_ABS_TOLERANCE_MM: Final[float] = 1e-3
"""記録を新規に作るときの境界箱の絶対許容差（mm）。1μm は形状として無意味な幅である。"""

_TOLERANCE_CONFIG_KEY: Final[str] = "position_tolerance_mm"
_TOLERANCE_CONFIG_PATH: Final[str] = "parameters.catch.position_tolerance_mm"
"""`trajectory_sim` の設定ファイルにおける位置許容誤差の場所（要件 7.5, 7.6）。"""

_TOLERANCE_PROVENANCE_KEY: Final[str] = "catch.position_tolerance_mm"
"""同設定の `parameters.provenance` における出所のキー。"""


# ---------------------------------------------------------------------------
# 終了コードの写像
# ---------------------------------------------------------------------------


def exit_code_for(error: BaseException) -> int:
    """例外から終了コードを決める（本モジュール docstring「終了コード」）。

    Args:
        error: 送出された例外。

    Returns:
        `EXIT_MISMATCH` / `EXIT_USAGE` / `EXIT_CAD_UNAVAILABLE` のいずれか。
        表に無い `ImportError` は `EXIT_CAD_UNAVAILABLE` へ写す——形状層から
        届く裸の `ImportError` は形状ライブラリの不在そのものであり、
        ⚠️ これを 2 にすると「入力が悪い」と読めて、導入すれば直ることが
        伝わらない。それ以外は `EXIT_USAGE`（呼び出し方の誤りの既定）。
    """
    for family, code in EXIT_CODE_BY_ERROR.items():
        if isinstance(error, family):
            return code
    if isinstance(error, ImportError):
        return EXIT_CAD_UNAVAILABLE
    return EXIT_USAGE


def _cad_unavailable(cause: BaseException) -> CadUnavailableError:
    """形状層から届いた `ImportError` を、導入方法を添えた専用の失敗へ変える。

    ⚠️ `errors.CadUnavailableError` の docstring が「メッセージには**導入方法**を
    載せる」と定める。任意依存 `cad` は既定でインストールされないため、
    利用できないことは異常ではなく既定の状態である。

    ⚠️ `uv sync --extra cad` を単独で案内してはならない。`uv sync` は宣言された
    集合へ**刈り込む**ため他の extras が消える（tasks.md
    「Implementation Notes」の環境節に実際に踏んだ記録がある）。
    """
    return CadUnavailableError(
        f"形状生成に必要な形状ライブラリを読み込めない（{cause}）。"
        "任意依存 `cad` を導入すること: `uv sync --all-extras`。"
        "⚠️ `uv sync --extra cad` を単独で実行すると他の extras が刈り取られる。"
        "⚠️ 寸法パラメータの読み込み・選定・許容誤差の導出・識別子のみの照合は、"
        "この環境でも実行できる（要件 5.2, 5.3）。"
    )


# ---------------------------------------------------------------------------
# 入力の読み取り
# ---------------------------------------------------------------------------


def _read_json_document(path: Path, label: str) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ⚠️ 読めない・解析できないは**入力の誤り**（終了コード 2）である。
    照合の不一致（1）と混ぜない——ファイルが壊れていることと、値が食い違って
    いることは、直し方が違う。

    Raises:
        ParameterError: 読み込みまたは解析に失敗した場合。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{label} {path} を読めない（{exc}）。") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{label} {path} を JSON として解析できない（{exc}）。") from exc


# ---------------------------------------------------------------------------
# select（要件 6.2）
# ---------------------------------------------------------------------------


def _is_illustrative(identifier: str) -> bool:
    """識別子が例示の非適合例のものか（`ILLUSTRATIVE_PREFIX` を参照）。"""
    return identifier.startswith(ILLUSTRATIVE_PREFIX)


def _format_verdict(verdict: CandidateVerdict) -> str:
    """判定1件を1行に整形する。⚠️ **1候補につきちょうど1行**である。

    識別子が複数行に現れると、どの行がその候補の結論なのかが読めなくなる。
    """
    parts = [ACCEPTED_LABEL if verdict.accepted else REJECTED_LABEL]
    if _is_illustrative(verdict.identifier):
        parts.append(ILLUSTRATIVE_LABEL)
    parts.append(verdict.identifier)
    if verdict.failed_items:
        parts.append(f"不適合項目: {', '.join(verdict.failed_items)}")
    if verdict.warnings:
        parts.append(f"警告: {', '.join(verdict.warnings)}")
    return "  " + " ".join(parts)


def _cmd_select(args: argparse.Namespace) -> int:
    """候補を選定基準で評価し、適合・不適合と理由を出力する（要件 6.2）。

    ⚠️ **不適合は失敗ではない。** 評価が完了した以上、終了コードは 0 である
    （`errors.py` の区分: 「評価結果は値で返し、呼び出し方の誤りだけを例外にする」）。
    不適合を 1 にすると、次点が基準を外れている現状で「検査の不一致」と読めて
    しまい、記録の不整合と区別できなくなる。
    """
    criteria = load_criteria()
    candidates = load_candidates()

    print(f"{PROGRAM} select: 候補 {len(candidates)} 件を選定基準で評価した")
    illustrative = 0
    for candidate in candidates:
        if _is_illustrative(candidate.identifier):
            illustrative += 1
        print(_format_verdict(evaluate_candidate(candidate, criteria)))

    if illustrative:
        # ⚠️ この行に識別子を書かない（1候補1行の規律を保つため）。
        print(
            f"{PROGRAM} select: {ILLUSTRATIVE_LABEL} は選定基準の説明のために置いた"
            f"例であり、実売を調査した品ではない（識別子の接頭辞 "
            f"{ILLUSTRATIVE_PREFIX!r} で区別している）。"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# tolerance（要件 7.5）
# ---------------------------------------------------------------------------


def _simulator_parameters(document: object, path: Path) -> Mapping[str, object]:
    """シミュレータ設定の `parameters` を取り出す。

    Raises:
        ParameterError: 設定がシミュレータの設定として読めない場合。
            ⚠️ これは入力の誤り（終了コード 2）であり、値の不一致（1）ではない。
    """
    if not isinstance(document, dict):
        raise ParameterError(f"--check {path}: 設定はオブジェクトでなければならない。")
    parameters = document.get("parameters")
    if not isinstance(parameters, dict):
        raise ParameterError(
            f"--check {path}: シミュレータの設定として読めない"
            "（'parameters' オブジェクトが無い）。"
        )
    return parameters


def _recorded_tolerance(parameters: Mapping[str, object], path: Path) -> float:
    """シミュレータ設定から位置許容誤差を取り出す（要件 7.5, 7.6）。

    Raises:
        ParameterError: `parameters.catch` がオブジェクトでない場合、または
            値が数でない場合。
        ConsistencyError: 値が記録されていない場合。⚠️ **記録が無いことを
            一致として読み飛ばさない**——還元（タスク 5.4）の済んでいない設定は
            「一致していない」のであって「検査対象外」ではない。
    """
    catch = parameters.get("catch")
    if catch is not None and not isinstance(catch, dict):
        raise ParameterError(f"--check {path}: 'parameters.catch' はオブジェクトでなければならない。")

    recorded = catch.get(_TOLERANCE_CONFIG_KEY) if isinstance(catch, dict) else None
    if recorded is None:
        raise ConsistencyError(
            f"--check {path}: {_TOLERANCE_CONFIG_PATH} が記録されていない。"
            "導出した位置許容誤差が設定へ還元されていない（要件 7.5, 7.6）。"
        )
    if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        raise ParameterError(
            f"--check {path}: {_TOLERANCE_CONFIG_PATH}={recorded!r} は数でなければならない。"
        )
    return float(recorded)


def _check_simulator_config(path: Path, derivation: ToleranceDerivation) -> None:
    """シミュレータ設定の値・出所が導出と一致することを検査する（要件 7.6, 7.7）。

    Raises:
        ConsistencyError: 値または出所が食い違う場合。⚠️ **双方の値と参照元を
            載せる**（`errors.ConsistencyError` の docstring）。
        ParameterError: 設定が読めない・構造が違う場合。
    """
    document = _read_json_document(path, "--check の設定")
    parameters = _simulator_parameters(document, path)
    recorded = _recorded_tolerance(parameters, path)

    if recorded != derivation.position_tolerance_mm:
        raise ConsistencyError(
            f"{path} の {_TOLERANCE_CONFIG_PATH}={recorded!r} は、"
            f"{DEFAULT_DIMENSIONS_PATH} から導出した値 "
            f"{derivation.position_tolerance_mm!r} と一致しない（要件 7.7）。"
        )

    provenance_table = parameters.get("provenance")
    if not isinstance(provenance_table, dict):
        return
    recorded_provenance = provenance_table.get(_TOLERANCE_PROVENANCE_KEY)
    if recorded_provenance is None:
        return
    if recorded_provenance != derivation.provenance.value:
        # ⚠️ 値が合っていても出所が「実測」を名乗れば、まだ測っていない値が
        # 実測として下流へ流れる（要件 1.5, 7.4）。
        raise ConsistencyError(
            f"{path} の parameters.provenance.{_TOLERANCE_PROVENANCE_KEY}="
            f"{recorded_provenance!r} は、導出の出所 {derivation.provenance.value!r} と"
            "一致しない（要件 7.4, 7.7）。"
        )


def _cmd_tolerance(args: argparse.Namespace) -> int:
    """位置許容誤差を導出し、導出記録へ書き出す（要件 7.5）。

    ⚠️ 導出そのものは `tolerance.derive_position_tolerance` にしか無い
    （要件 7.1「単一の箇所で導出する」）。本関数は導出しない——読み、呼び、書く。
    """
    params = load_params()
    derivation = derive_position_tolerance(params)
    dump_derivation(derivation, args.output)

    print(
        f"{PROGRAM} tolerance: {_TOLERANCE_CONFIG_KEY}="
        f"{derivation.position_tolerance_mm!r}（出所: {derivation.provenance.value}）を "
        f"{args.output} へ書き出した"
    )
    for item in derivation.inputs:
        print(f"  入力 {item.name}={item.value_mm!r}（出所: {item.provenance.value}）")
    print(f"  導出式 {derivation.formula}")

    if args.check is not None:
        _check_simulator_config(args.check, derivation)
        print(f"{PROGRAM} tolerance: {args.check} の設定値と出所は導出と一致する")
    return EXIT_OK


# ---------------------------------------------------------------------------
# check（要件 4.3, 4.4, 4.5）
# ---------------------------------------------------------------------------


def _verify_digest(baseline: GeometryBaseline, params: MechanismParams, path: Path) -> str:
    """記録の識別子が現在の寸法設定と一致することを検査する（要件 4.5）。

    ⚠️ **形状を再生成せずに実行できる**——これが `--digest-only` の存在理由であり、
    形状ライブラリ非導入の環境でも「寸法を変えたまま記録を更新していない」状態を
    検出できる唯一の手段である。

    Returns:
        現在の寸法設定ファイルの識別子。

    Raises:
        ConsistencyError: 記録と現在の識別子が食い違う場合。⚠️ 双方の値と
            参照元（記録のパスと寸法設定ファイルのパス）を載せる。
    """
    current = parameters_digest(params)
    if baseline.parameters_digest != current:
        raise ConsistencyError(
            f"記録 {path} の parameters_digest={baseline.parameters_digest!r} は、"
            f"現在の {DEFAULT_DIMENSIONS_PATH} の識別子 {current!r} と一致しない。"
            "寸法パラメータを変更したまま形状指標の記録を更新していない（要件 4.5）。"
            f"`python -m {PROGRAM} build --update-baseline` で記録を更新すること。"
        )
    return current


def _regenerate_metrics(params: MechanismParams) -> Mapping[str, PartMetrics]:
    """形状を再生成し、部品名から指標への対応表を返す（要件 4.3）。

    ⚠️ **形状層はここで初めて import される**（本モジュール docstring「遅延
    import」）。非導入の環境では `ImportError` が届くため、専用の失敗へ写す。
    """
    try:
        from catch_mechanism.shapes import build_parts

        parts = build_parts(params)
    except ImportError as exc:
        raise _cad_unavailable(exc) from exc
    return {part.name: part.metrics for part in parts}


def _presence(value: float) -> str:
    """`PRESENCE_FIELD` の値を人の読む語にする（`PRESENT` / `ABSENT`）。"""
    if value == PRESENT:
        return "在"
    if value == ABSENT:
        return "不在"
    return repr(value)  # pragma: no cover - `compare_metrics` はこの2値しか作らない


def _format_mismatch(mismatch: MetricsMismatch) -> str:
    """不一致1件を1行に整形する。

    ⚠️ **部品の不在／余剰を他の乖離と区別する**（tasks.md「Implementation Notes」
    タスク 2.4(b)）。`compare_metrics` は片側にしか無い部品を
    `field_name=PRESENCE_FIELD` の1件だけで報告し、比較の成立しない体積・境界箱の
    行を併せて出さない。出力もその形をそのまま保つ——「部品が消えた」ことと
    「体積が 1% ずれた」ことは、直し方も緊急度も違う。
    """
    if mismatch.field_name == PRESENCE_FIELD:
        label = VANISHED_LABEL if mismatch.regenerated == ABSENT else APPEARED_LABEL
        return (
            f"  {label} {mismatch.part_name}: "
            f"記録={_presence(mismatch.recorded)} 再生成={_presence(mismatch.regenerated)}"
        )
    return (
        f"  {DEVIATION_LABEL} {mismatch.part_name} {mismatch.field_name}: "
        f"記録={mismatch.recorded!r} 再生成={mismatch.regenerated!r}"
    )


def _cmd_check(args: argparse.Namespace) -> int:
    """記録済み指標との照合（要件 4.3, 4.4, 4.5）。

    ⚠️ **記録が無ければ失敗する**（`metrics.load_baseline` の規律）。空の記録を
    黙って作れば照合は「部品0件」で必ず成功し、記録を作り忘れた状態が緑のまま
    流れる。既定の記録 `configs/catch_mechanism/geometry-baseline.json` を作るのは
    タスク 4.2 であり、それまで既定パスでの `check` はファイル名を示して
    終了コード 2 で失敗する。
    """
    baseline = load_baseline(args.baseline)
    params = load_params()

    # ⚠️ 識別子の検査を先に行う。記録が古いと分かっているものへ再生成結果を
    # 突き合わせても、出てくる不一致は「寸法を変えたのだから当然」でしかない
    # （`errors.ConsistencyError`:「照合を待たずに成立していない整合を拒否する」）。
    current = _verify_digest(baseline, params, args.baseline)
    print(f"{PROGRAM} check: パラメータ識別子は記録と一致する（{current}）")

    if args.digest_only:
        print(
            f"{PROGRAM} check: --digest-only のため形状を再生成しない"
            "（形状ライブラリを要さない検査のみを行った）"
        )
        return EXIT_OK

    measured = _regenerate_metrics(params)
    mismatches = compare_metrics(baseline, measured)
    if not mismatches:
        print(f"{PROGRAM} check: {len(measured)} 部品の形状指標が記録と一致する")
        return EXIT_OK

    print(
        f"{PROGRAM} check: 記録 {args.baseline} と再生成の間に "
        f"{len(mismatches)} 件の不一致がある（要件 4.4）",
        file=sys.stderr,
    )
    for mismatch in mismatches:
        print(_format_mismatch(mismatch), file=sys.stderr)
    return EXIT_MISMATCH


# ---------------------------------------------------------------------------
# build（要件 3.2, 5.3）
# ---------------------------------------------------------------------------


def _generator_version() -> str:
    """記録に残す形状ライブラリの版（情報用。照合には使わない）。"""
    try:
        version = importlib.metadata.version(_SHAPE_LIBRARY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - 生成成功後は在る
        return f"{_SHAPE_LIBRARY_DISTRIBUTION} (版不明)"
    return f"{_SHAPE_LIBRARY_DISTRIBUTION} {version}"


def _existing_tolerances(path: Path) -> tuple[float, float]:
    """既存の記録から許容差を引き継ぐ（無ければ既定値）。

    ⚠️ **記録の内容はタスク 4.2 の所有である。** `--update-baseline` は指標と
    識別子を更新するためのものであって、許容差を黙って既定値へ戻してよい
    ものではない——許容差の見直しは形状の実測に基づく判断であり、再生成の
    副作用として消えてはならない。

    読めない記録（壊れている・版が違う）に当たった場合は、既定値へ戻すことを
    ⚠️ **標準エラーへ明示してから**行う（黙って戻すと、締めたはずの許容差が
    緩んだことに気付けない）。
    """
    if not path.exists():
        return _DEFAULT_VOLUME_REL_TOLERANCE, _DEFAULT_BBOX_ABS_TOLERANCE_MM
    try:
        previous = load_baseline(path)
    except ParameterError as exc:
        print(
            f"{PROGRAM} build: 既存の記録 {path} を読めなかったため、許容差を既定値"
            f"（体積 {_DEFAULT_VOLUME_REL_TOLERANCE!r} / 境界箱 "
            f"{_DEFAULT_BBOX_ABS_TOLERANCE_MM!r}mm）で作り直す（{exc}）",
            file=sys.stderr,
        )
        return _DEFAULT_VOLUME_REL_TOLERANCE, _DEFAULT_BBOX_ABS_TOLERANCE_MM
    return previous.volume_rel_tolerance, previous.bbox_abs_tolerance_mm


def _update_baseline(
    path: Path, params: MechanismParams, measured: Mapping[str, PartMetrics]
) -> None:
    """形状指標の記録を書き出す（要件 4.2 の書き出し経路）。

    ⚠️ **出荷する記録の中身を決めるのはタスク 4.2 である。** 本関数はその経路を
    与えるだけで、既定の記録ファイルをここで作りはしない。
    """
    volume_tolerance, bbox_tolerance = _existing_tolerances(path)
    baseline = GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest=parameters_digest(params),
        volume_rel_tolerance=volume_tolerance,
        bbox_abs_tolerance_mm=bbox_tolerance,
        generator_version=_generator_version(),
        parts=dict(measured),
    )
    write_baseline(baseline, path)
    print(f"{PROGRAM} build: 形状指標の記録を {path} へ更新した（{len(measured)} 部品）")


def _cmd_build(args: argparse.Namespace) -> int:
    """形状を生成し、3形式の生成物と指標を出力する（要件 3.2, 3.3, 5.3）。

    ⚠️ **画面表示・対話操作・外部 CAD の起動を要さない**（要件 3.2）。本関数は
    `export_parts` を1回呼んで終わる。

    ⚠️ 形状ライブラリが無い環境では `ImportError` が届く。これを専用の失敗
    （終了コード 3）へ写すのが本モジュールの責務であり、**成功として黙って
    読み飛ばさない**（要件 5.3）。
    """
    params = load_params()
    try:
        from catch_mechanism.export import export_parts

        exported = export_parts(params, args.output_dir)
    except ImportError as exc:
        raise _cad_unavailable(exc) from exc

    print(f"{PROGRAM} build: {len(exported)} 部品を書き出した")
    for part in exported:
        metrics = part.metrics
        print(
            f"  {part.name}: 体積={metrics.volume_mm3!r}mm^3 "
            f"外接箱={metrics.bbox_mm!r}mm 立体数={metrics.solid_count}"
        )
        for path in part.paths:
            print(f"    {path}")

    if args.update_baseline:
        _update_baseline(args.baseline, params, {part.name: part.metrics for part in exported})
    return EXIT_OK


# ---------------------------------------------------------------------------
# 引数の解析と入口
# ---------------------------------------------------------------------------

_HANDLERS: Final[Mapping[str, Callable[[argparse.Namespace], int]]] = {
    "build": _cmd_build,
    "check": _cmd_check,
    "select": _cmd_select,
    "tolerance": _cmd_tolerance,
}


def build_parser() -> argparse.ArgumentParser:
    """4サブコマンドの引数解析器を組み立てる（design.md「Cli」Service Interface）。"""
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "受け口（キャッチ機構）の生成・照合・選定・許容誤差導出を行う"
            "1回きりのバッチ入口。常駐しない。終了コードは "
            f"{EXIT_OK} 正常 / {EXIT_MISMATCH} 検査の不一致 / "
            f"{EXIT_USAGE} 使い方の誤り・入力不正 / "
            f"{EXIT_CAD_UNAVAILABLE} 形状生成の環境が無い、である。"
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True, metavar="subcommand")

    build = subparsers.add_parser(
        "build",
        help="形状を生成し STEP / STL / 3MF と指標を出力する（形状ライブラリが要る）",
        description=(
            "寸法パラメータから受け口の全部品を構築し、3形式で書き出す。"
            "形状ライブラリが無い環境では終了コード "
            f"{EXIT_CAD_UNAVAILABLE} で失敗する（成功にはしない）。"
        ),
    )
    build.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="生成物の出力先。省略時は export の既定（var/cad/）を用いる。",
    )
    build.add_argument(
        "--update-baseline",
        action="store_true",
        help="生成した指標で形状指標の記録を更新する。既定では記録に触れない。",
    )
    build.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="--update-baseline の書き出し先となる形状指標の記録。",
    )

    check = subparsers.add_parser(
        "check",
        help="記録済み指標との照合（--digest-only は形状を再生成しない）",
        description=(
            "形状指標の記録に対して、パラメータ識別子と（--digest-only でなければ）"
            "再生成した形状指標を照合する。不一致は終了コード "
            f"{EXIT_MISMATCH} である。"
        ),
    )
    check.add_argument(
        "--digest-only",
        action="store_true",
        help=(
            "形状を再生成せず、記録のパラメータ識別子だけを現在の寸法設定と"
            "突き合わせる。形状ライブラリを要さない。"
        ),
    )
    check.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="照合する形状指標の記録。",
    )

    subparsers.add_parser(
        "select",
        help="候補を選定基準で評価し、適合・不適合と理由を出力する",
        description=(
            "候補の諸元を選定基準で評価する。候補の不適合は失敗ではないため、"
            f"評価が完了すれば終了コードは {EXIT_OK} である。"
            f"識別子が {ILLUSTRATIVE_PREFIX!r} で始まる候補は基準の説明のために"
            "置いた例であり、実売を調査した品ではない。"
        ),
    )

    tolerance = subparsers.add_parser(
        "tolerance",
        help="位置許容誤差を導出し、導出記録へ書き出す",
        description=(
            "開口内半径と対象物の代表寸法から位置許容誤差を導出し、値・入力・"
            "出所・式・前提を記録へ書き出す。--check を与えると、シミュレータの"
            f"設定に記録された値・出所と突き合わせる（不一致は終了コード "
            f"{EXIT_MISMATCH}）。"
        ),
    )
    tolerance.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DERIVATION_PATH,
        help="導出記録の書き出し先。",
    )
    tolerance.add_argument(
        "--check",
        type=Path,
        default=None,
        metavar="CONFIG",
        help=(
            "シミュレータの設定ファイル。"
            f"{_TOLERANCE_CONFIG_PATH} と、記録されていれば出所を導出と比較する。"
        ),
    )
    return parser


def _exit_code_of_system_exit(exc: SystemExit) -> int:
    """`argparse` が投げる `SystemExit` を終了コードへ写す。

    `--help` は 0、使用法の誤りは 2 である（`argparse` の既定と design.md の
    「使い方の誤り」が一致している）。⚠️ `main()` を総関数に保つために吸収する
    ——in-process の検査が `pytest.raises(SystemExit)` を強いられずに済む。
    """
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    """コマンド入口の実処理。1回呼ぶと1回だけ処理し、終了コードを返す。

    常駐しない。サーバ・ソケット・監視ループを持たない。

    Args:
        argv: 引数列。省略時は `sys.argv[1:]`。

    Returns:
        `EXIT_OK` / `EXIT_MISMATCH` / `EXIT_USAGE` / `EXIT_CAD_UNAVAILABLE`。

    Notes:
        ⚠️ 例外は**送出せずに終了コードへ写す**。`CatchMechanismError` の5系統は
        `EXIT_CODE_BY_ERROR` が、形状層から届く裸の `ImportError` は
        `exit_code_for` が受ける。⚠️ `ImportError` を握るのは形状ライブラリの
        不在を専用の終了コードで伝えるためであり、**成功へ落とすためではない**。

        `OSError`（記録・導出記録の書き出し先が書けない等）も受けて 2 を返す。
        ⚠️ 書き出し先の不備は利用者が直せる**入力の誤り**であり、追跡情報を
        並べて異常終了するより、1行で理由を示すほうが直しやすい。
        `export_parts` 内部の書き出し失敗は `GeometryError` に包まれて届くため
        （`export.py`）、ここへは来ない。
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code_of_system_exit(exc)

    handler = _HANDLERS[args.subcommand]
    try:
        return handler(args)
    except (CatchMechanismError, ImportError, OSError) as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return exit_code_for(exc)
