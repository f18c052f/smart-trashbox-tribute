"""導出記録とシミュレータ設定の整合検査（タスク 5.4、要件 7.5, 7.6, 7.7, 7.8）。

本 Spec が導出した位置許容誤差は、`configs/trajectory_sim/` の**実行可能な**
シミュレータ設定へ**値として**還元される。⚠️ **`src/trajectory_sim/` の実装
コードは1バイトも変更しない**（要件 7.8 / design.md「Boundary Context」）。
還元の唯一の担保は「設定に書かれた値・出所が、導出記録の値・出所と一致する」
という本ファイルの検査である。

固定するのは次の4点である。

1. **実行可能な設定を構造で見つける**こと。`configs/trajectory_sim/*.json` の
   うち `parameters` オブジェクトを持つものが `trajectory_sim.cli` の
   `--config` へ渡せる設定であり、`--drivetrain` へ渡す機体パラメータ
   （`drivetrain-*.json`）はこれを持たない。⚠️ **ファイル名を列挙しない**——
   列挙すると、将来 `parameters` を持つ設定が増えたときに検査から静かに
   漏れる（要件 7.6 の「実行可能な設定ファイル」を数え落とす）
2. **見つけた設定のすべてが値と出所を記録している**こと。⚠️ **鍵の不在を
   一致として読み飛ばさない。** 還元の済んでいない設定は「一致していない」の
   であって「検査対象外」ではない（`cli._recorded_tolerance` の docstring が
   同じ裁定を下している）
3. **記録された値・出所が導出記録と一致する**こと（要件 7.6）
4. **不一致のとき、双方の値と参照元が示される**こと（要件 7.7）。⚠️ 参照元は
   「どの設定ファイルか」と「どの導出記録か」の**両方**である。片方しか
   示さないと、読んだ人はどちらを直すべきか判断できない

⚠️ **突き合わせの相手は導出記録**（`configs/catch_mechanism/catch-opening.json`）
であって、`dimensions.json` からの再導出ではない。記録が寸法へ追随している
ことは `test_catch_tolerance.py` のトリップワイヤ
（`test_shipped_record_is_the_derivation_from_the_shipped_dimensions`）が別途
固定しており、本ファイルがそれを重複させると、寸法・記録・設定の3者のどこが
ずれたのかを検査の落ち方から読めなくなる。

⚠️ **リテラルの 72.5 を置かない。** 缶を実測すれば値も出所も動くが、記録と設定が
揃って更新されている限り本ファイルは緑である。値そのもののピン留めは
`test_catch_tolerance.py`（`_Boundary: Tolerance_`）が持つ。

ファイル名について: design.md「Directory Structure」は `test_trajectory_sim_sync.py`
と呼ぶが、`tests/` に `__init__.py` が無くテストモジュール名がセッション全体で
フラットであるため、`test_catch_*.py` の接頭辞に倣う（tasks.md「Implementation
Notes」タスク 1.1）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from catch_mechanism.params import Provenance
from catch_mechanism.tolerance import DEFAULT_DERIVATION_PATH, load_derivation

REPO_ROOT = Path(__file__).resolve().parents[2]
SIM_CONFIGS_DIR = REPO_ROOT / "configs" / "trajectory_sim"

#: 設定側の値の在り処。⚠️ `trajectory_sim.PARAMETER_PATHS` に実在する有効な
#: パスであり、還元は**値の追記だけ**で成立する（design.md「Boundary Context」）。
TOLERANCE_KEY = "position_tolerance_mm"
TOLERANCE_PATH = f"parameters.catch.{TOLERANCE_KEY}"

#: 設定側の出所表の行名。`parameters.provenance` のキーは
#: `trajectory_sim.PARAMETER_PATHS` のパス文字列と一致させる規約である。
PROVENANCE_KEY = "catch.position_tolerance_mm"
PROVENANCE_PATH = f"parameters.provenance.{PROVENANCE_KEY}"


def _load_document(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_executable_simulator_config(document: Any) -> bool:
    """`trajectory_sim.cli --config` へ渡せる設定かを**構造で**判定する。

    `parameters` オブジェクトを持つことがシナリオ設定の要件であり、
    `--drivetrain` へ渡す機体パラメータ（ホイール径・回転数などの平たい
    オブジェクト）はこれを持たない。
    """
    return isinstance(document, dict) and isinstance(document.get("parameters"), dict)


def executable_simulator_configs() -> list[Path]:
    """`configs/trajectory_sim/` の実行可能なシミュレータ設定を列挙する。"""
    return [
        path
        for path in sorted(SIM_CONFIGS_DIR.glob("*.json"))
        if _is_executable_simulator_config(_load_document(path))
    ]


def check_config_against_derivation(path: Path) -> list[str]:
    """設定 1 件を導出記録と突き合わせ、不一致の説明を返す（要件 7.6, 7.7）。

    ⚠️ **各説明は双方の値と双方の参照元を含む。** 「一致しない」とだけ書かれた
    失敗は、設定と記録のどちらが古いのかを読み手に伝えない。

    Returns:
        不一致の説明の並び。一致していれば空。
    """
    derivation = load_derivation(DEFAULT_DERIVATION_PATH)
    document = _load_document(path)
    problems: list[str] = []

    if not _is_executable_simulator_config(document):
        return [
            f"{path} は 'parameters' オブジェクトを持たず、シミュレータ設定として"
            "読めない（検査対象の選び方が誤っている）。"
        ]

    parameters = document["parameters"]
    catch = parameters.get("catch")
    recorded = catch.get(TOLERANCE_KEY) if isinstance(catch, dict) else None

    if recorded is None:
        problems.append(
            f"設定 {path} に {TOLERANCE_PATH} が記録されていない"
            f"（導出記録 {DEFAULT_DERIVATION_PATH} は "
            f"{TOLERANCE_KEY}={derivation.position_tolerance_mm!r} を持つ）。"
            "還元されていない設定は検査対象外ではなく不一致である（要件 7.5, 7.6）。"
        )
    elif isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        problems.append(
            f"設定 {path} の {TOLERANCE_PATH}={recorded!r} は数ではない"
            f"（導出記録 {DEFAULT_DERIVATION_PATH} の "
            f"{TOLERANCE_KEY}={derivation.position_tolerance_mm!r}）。"
        )
    elif float(recorded) != derivation.position_tolerance_mm:
        problems.append(
            f"設定 {path} の {TOLERANCE_PATH}={float(recorded)!r} は、"
            f"導出記録 {DEFAULT_DERIVATION_PATH} の "
            f"{TOLERANCE_KEY}={derivation.position_tolerance_mm!r} と一致しない"
            "（要件 7.7）。"
        )

    provenance_table = parameters.get("provenance")
    recorded_provenance = (
        provenance_table.get(PROVENANCE_KEY) if isinstance(provenance_table, dict) else None
    )
    if recorded_provenance is None:
        problems.append(
            f"設定 {path} に {PROVENANCE_PATH} が記録されていない"
            f"（導出記録 {DEFAULT_DERIVATION_PATH} の出所は "
            f"{derivation.provenance.value!r}）。⚠️ 出所の無い値は、"
            "まだ測っていない量が実測として下流へ流れる余地を残す（要件 1.5, 7.4）。"
        )
    elif recorded_provenance != derivation.provenance.value:
        problems.append(
            f"設定 {path} の {PROVENANCE_PATH}={recorded_provenance!r} は、"
            f"導出記録 {DEFAULT_DERIVATION_PATH} の出所 "
            f"{derivation.provenance.value!r} と一致しない（要件 7.4, 7.7）。"
        )

    return problems


# ---------------------------------------------------------------------------
# 検査対象の選び方そのもの
# ---------------------------------------------------------------------------


def test_the_executable_configs_are_discovered_by_structure_not_by_name() -> None:
    """実行可能な設定を構造で見つけ、機体パラメータを除く（要件 7.6）。

    ⚠️ **空集合で緑にならないこと**を併せて固定する。走査が何も拾わなければ
    以降の検査はすべて空回りし、還元が消えても誰も気づかない。
    """
    configs = executable_simulator_configs()

    assert configs, (
        f"{SIM_CONFIGS_DIR} に実行可能なシミュレータ設定が1件も見つからない。"
        "走査が空なら、以降の一致検査はすべて空回りする。"
    )
    for path in configs:
        assert isinstance(_load_document(path)["parameters"], dict)

    all_configs = sorted(SIM_CONFIGS_DIR.glob("*.json"))
    for path in [item for item in all_configs if item not in configs]:
        assert "parameters" not in _load_document(path), (
            f"{path} は 'parameters' を持つのに検査対象から漏れている"
        )


# ---------------------------------------------------------------------------
# 出荷される設定 ⇄ 出荷される導出記録（要件 7.6）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_every_executable_config_agrees_with_the_derivation_record(config_path: Path) -> None:
    """⚠️ 実行可能な設定の**すべて**が導出記録の値と出所に一致する（要件 7.6）。

    1件ずつの格子にしてあるため、どの設定が古いのかが失敗名から読める。
    """
    problems = check_config_against_derivation(config_path)

    assert not problems, "\n".join(problems)


def test_every_executable_config_records_the_value_and_the_provenance() -> None:
    """設定の側に値と出所の**両方**が実在する（要件 7.5）。

    上の格子は `check_config_against_derivation` 経由の間接的な主張である。
    ここでは「鍵が在ること」を直接読み、還元の形（どのキーへ書くか）を固定する。
    """
    for path in executable_simulator_configs():
        parameters = _load_document(path)["parameters"]
        assert TOLERANCE_KEY in parameters.get("catch", {}), f"{path}: {TOLERANCE_PATH} が無い"
        assert PROVENANCE_KEY in parameters.get("provenance", {}), (
            f"{path}: {PROVENANCE_PATH} が無い"
        )


def test_the_recorded_provenance_is_a_word_of_the_shared_vocabulary() -> None:
    """設定に書かれた出所が `Provenance` の値集合の語である（要件 1.5）。

    ⚠️ `catch_mechanism.Provenance` と `trajectory_sim.Provenance` は値集合を
    一致させる約束であり、還元は「値を写す」作業であって「訳す」作業ではない。
    """
    vocabulary = {item.value for item in Provenance}

    for path in executable_simulator_configs():
        recorded = _load_document(path)["parameters"]["provenance"][PROVENANCE_KEY]
        assert recorded in vocabulary, f"{path}: 出所 {recorded!r} は {vocabulary!r} に無い"


# ---------------------------------------------------------------------------
# ⚠️ 不一致の検出（要件 7.7）——値をずらす／出所を偽る／鍵を消す
#
# 出荷ファイルは書き換えず、`tmp_path` の写しに対して変異させる。
# ---------------------------------------------------------------------------


def _mutated_copy(
    config_path: Path, tmp_path: Path, mutate: Callable[[dict[str, Any]], None]
) -> Path:
    """出荷設定の写しを作り、`mutate` で改変して書き出す。"""
    document = _load_document(config_path)
    mutate(document)
    destination = tmp_path / config_path.name
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_shifting_the_configured_value_is_detected_with_both_values_and_sources(
    config_path: Path, tmp_path: Path
) -> None:
    """⚠️ 設定側の値を意図的にずらすと検査が失敗する（タスク 5.4 の完了状態）。

    失敗の説明に**双方の値**と**双方の参照元**が現れることを併せて固定する
    （要件 7.7）。⚠️ 「一致しない」とだけ言う失敗は、どちらを直すのかを
    読み手に委ねてしまう。
    """
    derivation = load_derivation(DEFAULT_DERIVATION_PATH)
    shifted = derivation.position_tolerance_mm + 1.0

    def mutate(document: dict[str, Any]) -> None:
        document["parameters"]["catch"][TOLERANCE_KEY] = shifted

    problems = check_config_against_derivation(_mutated_copy(config_path, tmp_path, mutate))

    assert problems, f"{config_path.name}: 値を {shifted!r} へずらしても検出されない"
    joined = "\n".join(problems)
    assert repr(shifted) in joined, f"設定側の値が示されていない: {joined}"
    assert repr(derivation.position_tolerance_mm) in joined, (
        f"導出記録の値が示されていない: {joined}"
    )
    assert config_path.name in joined, f"設定側の参照元が示されていない: {joined}"
    assert str(DEFAULT_DERIVATION_PATH) in joined, f"導出記録の参照元が示されていない: {joined}"


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_claiming_the_wrong_provenance_is_detected(config_path: Path, tmp_path: Path) -> None:
    """⚠️ 値が合っていても出所を偽れば検査が失敗する（要件 7.4, 7.7）。

    対象物（空き缶 φ65）が未実測である限り、導出全体の出所は仮値である。
    設定側が `measured` を名乗れば、まだ測っていない量が実測として下流へ流れる。
    """
    derivation = load_derivation(DEFAULT_DERIVATION_PATH)
    lie = next(item.value for item in Provenance if item.value != derivation.provenance.value)

    def mutate(document: dict[str, Any]) -> None:
        document["parameters"]["provenance"][PROVENANCE_KEY] = lie

    problems = check_config_against_derivation(_mutated_copy(config_path, tmp_path, mutate))

    assert problems, f"{config_path.name}: 出所を {lie!r} と偽っても検出されない"
    joined = "\n".join(problems)
    assert lie in joined and derivation.provenance.value in joined, (
        f"双方の出所が示されていない: {joined}"
    )
    assert config_path.name in joined and str(DEFAULT_DERIVATION_PATH) in joined, (
        f"双方の参照元が示されていない: {joined}"
    )


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_deleting_the_value_is_not_read_as_agreement(config_path: Path, tmp_path: Path) -> None:
    """⚠️ 鍵を消しても「対象外」として素通りしない（要件 7.5, 7.6）。

    還元の済んでいない設定は「一致していない」のであって「検査対象外」ではない。
    この分岐が無いと、将来 `catch` ブロックを得た設定が黙って検査を免れる。
    """

    def mutate(document: dict[str, Any]) -> None:
        del document["parameters"]["catch"][TOLERANCE_KEY]

    problems = check_config_against_derivation(_mutated_copy(config_path, tmp_path, mutate))

    assert problems, f"{config_path.name}: 値を消しても検出されない"
    assert TOLERANCE_PATH in "\n".join(problems)


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_deleting_the_provenance_row_is_not_read_as_agreement(
    config_path: Path, tmp_path: Path
) -> None:
    """⚠️ 出所の行を消しても素通りしない（要件 1.5, 7.4）。

    ⚠️ `cli._check_simulator_config` は出所の行が無いとき**黙って戻る**
    （`recorded_provenance is None` で `return`）。本ファイルはそこを閉じる。
    値だけが下流へ渡り、それが実測か仮値かが失われる状態を一致とみなさない。
    """

    def mutate(document: dict[str, Any]) -> None:
        del document["parameters"]["provenance"][PROVENANCE_KEY]

    problems = check_config_against_derivation(_mutated_copy(config_path, tmp_path, mutate))

    assert problems, f"{config_path.name}: 出所の行を消しても検出されない"
    assert PROVENANCE_PATH in "\n".join(problems)


# ---------------------------------------------------------------------------
# ⚠️ 実行可能な入口（`cli tolerance --check`）でも同じ結論になること
#
# design.md「Testing Strategy」の統合検証は
# `tolerance --check configs/trajectory_sim/sweep-reachability.json` が
# 一致・不一致で終了コードを分けることを求める（要件 7.5, 7.6）。
# ⚠️ `--output` を必ず `tmp_path` へ向ける（既定は出荷記録の上書きである。
# tasks.md「Implementation Notes」タスク 4.1(g)）。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_cli_tolerance_check_exits_zero_for_every_executable_config(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⚠️ 出荷の設定に対し `tolerance --check` が 0 で終わる（要件 7.5, 7.6）。

    ライブラリ側の突き合わせ（上の格子）と CLI は別の実装である
    （`cli._check_simulator_config`）。⚠️ **片方だけを緑にしても還元は閉じない**——
    利用者が実際に叩くのは CLI であり、CLI が exit 1 のままなら「還元済み」とは
    言えない。
    """
    from catch_mechanism import cli

    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "record.json"), "--check", str(config_path)]
    )
    capsys.readouterr()

    assert exit_code == 0, f"{config_path.name}: tolerance --check が {exit_code} で終わった"


@pytest.mark.parametrize(
    "config_path", executable_simulator_configs(), ids=lambda path: path.name
)
def test_cli_tolerance_check_exits_one_when_the_configured_value_is_shifted(
    config_path: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """設定側の値をずらすと CLI が 1（検査の不一致）で終わる（要件 7.7）。

    ⚠️ 2（入力の誤り）でも 3（形状ライブラリ不在）でもない。不一致は
    「読めなかった」でも「環境が無い」でもない。
    """
    from catch_mechanism import cli

    def mutate(document: dict[str, Any]) -> None:
        document["parameters"]["catch"][TOLERANCE_KEY] += 1.0

    mutated = _mutated_copy(config_path, tmp_path, mutate)

    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "record.json"), "--check", str(mutated)]
    )
    captured = capsys.readouterr()

    assert exit_code == 1, f"{config_path.name}: ずらしても {exit_code} で終わった"
    assert str(mutated) in captured.err or str(mutated) in captured.out


# ---------------------------------------------------------------------------
# ⚠️ 要件 7.8: シミュレータの実装コードを変更せずに値を伝える
# ---------------------------------------------------------------------------


def test_this_check_does_not_import_the_simulator_implementation() -> None:
    """本ファイルは `trajectory_sim` を import しない（要件 7.8 / 依存境界）。

    ⚠️ 還元の担保は**設定ファイルの値の読み取り**であり、下流の実装を呼んで
    確かめることではない。呼んでしまうと `catch_mechanism` のテストが
    `trajectory_sim` へ依存し、design.md「Boundary Context」の
    「`catch_mechanism` は `trajectory_sim` を import しない」が破れる。
    """
    import ast

    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    assert not [name for name in imported if name.split(".")[0] == "trajectory_sim"], (
        f"`trajectory_sim` を import している: {imported!r}"
    )
