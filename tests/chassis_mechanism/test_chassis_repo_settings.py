"""本 Spec の設定ファイルの改行規約を検証する（タスク 1.1 / 要件 1.1）。

本 Spec の設定ファイル（`configs/chassis_mechanism/*.json`）は、Python 側の書き出しが
プラットフォーム非依存に **LF** を書く（design.md「Config」: 書き出しは LF・キー整列・
末尾改行）。`.gitattributes` の一般則 `*.json text eol=crlf` のままだと、書き戻すたびに
作業ツリーとチェックアウト内容がずれ、`git status --porcelain` が空の差分を報告し続ける。

固定するのは2点である。

1. **`.gitattributes` の例外行が、一般則 `*.json` より後ろの行にある**こと。
   ⚠️ git は**最後にマッチした行**を採用するため、順序を取り違えて追記すると例外は効かない。
   行の存在だけを見るテストではこの取り違えを取り逃がす。
2. **git 自身に問い合わせた実効属性が LF である**こと（`git check-attr`）。
   併せて、例外が `configs/chassis_mechanism/` に**閉じている**こと——他の `*.json` は
   CRLF のままである（tasks.md「改行コード」: `.md` と他の `.json` は CRLF、混在させない）ことを
   確かめる。1 の順序検査だけでは、一般則そのものを壊す形の追記を見逃す。

git が使えない環境・git チェックアウトでない環境では skip する
（上流 `tests/catch_mechanism/test_catch_repo_settings.py` と同じ流儀）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GITATTRIBUTES_PATH = REPO_ROOT / ".gitattributes"

#: 一般則。これより後ろに本 Spec の例外行を置く必要がある。
GENERAL_JSON_PATTERN = "*.json"

#: 本 Spec の設定ファイル群に対する改行変換の例外。
CHASSIS_JSON_PATTERN = "configs/chassis_mechanism/*.json"


def _attribute_lines() -> list[tuple[int, str, tuple[str, ...]]]:
    """`.gitattributes` を「行番号・パターン・属性の並び」へ分解する。

    コメント行と空行は落とす。パターンに空白を含む書式（引用符）は本ファイルでは
    使っていないため、単純な空白分割で足りる。
    """
    lines: list[tuple[int, str, tuple[str, ...]]] = []
    text = GITATTRIBUTES_PATH.read_text(encoding="utf-8")
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        lines.append((number, fields[0], tuple(fields[1:])))
    return lines


def _line_number_of(pattern: str) -> int:
    matches = [number for number, candidate, _ in _attribute_lines() if candidate == pattern]
    assert matches, f".gitattributes にパターン {pattern!r} の行が無い"
    assert len(matches) == 1, f".gitattributes にパターン {pattern!r} の行が複数ある: {matches}"
    return matches[0]


def _run_git(args: list[str]) -> str:
    """リポジトリルートで git を実行し標準出力を返す。失敗時は skip する。"""
    if shutil.which("git") is None:
        pytest.skip("git が見つからないため、リポジトリ設定の実効値を検査できない")
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:  # pragma: no cover - 環境依存
        pytest.skip(f"git を実行できない: {exc}")
    if completed.returncode != 0:
        pytest.skip(
            f"git {' '.join(args)} が失敗した（git チェックアウトでない可能性）: "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout


def _check_attr(attributes: list[str], paths: list[str]) -> dict[str, dict[str, str]]:
    """`git check-attr` の実効値を `{path: {attr: value}}` で返す。

    `-z` を使うのは、出力を `": "` で分解するとパスに含まれる区切りと曖昧になるためである。
    """
    raw = _run_git(["check-attr", "-z", *attributes, "--", *paths])
    fields = raw.split("\0")
    result: dict[str, dict[str, str]] = {}
    for index in range(0, len(fields) - 2, 3):
        path, attribute, value = fields[index : index + 3]
        result.setdefault(path, {})[attribute] = value
    return result


def test_chassis_lf_exception_is_declared_after_the_general_json_rule() -> None:
    """例外行が一般則 `*.json` より後ろの行にある（tasks.md 1.1 の ⚠️ 注記）。

    ⚠️ git は最後にマッチした行を採用する。前に置くと一般則（CRLF）に上書きされ、
    追記したのに効かないという最も気付きにくい失敗になる。
    """
    general_line = _line_number_of(GENERAL_JSON_PATTERN)
    chassis_line = _line_number_of(CHASSIS_JSON_PATTERN)
    assert chassis_line > general_line, (
        f"{CHASSIS_JSON_PATTERN} の行（{chassis_line} 行目）が "
        f"一般則 {GENERAL_JSON_PATTERN}（{general_line} 行目）より前にある。"
        "git は最後にマッチした行を採用するため、この順序では例外が効かない"
    )


def test_chassis_lf_exception_declares_lf_text() -> None:
    """例外行が宣言する属性が `text eol=lf` である。"""
    attributes = {
        pattern: values for _, pattern, values in _attribute_lines()
    }[CHASSIS_JSON_PATTERN]
    assert attributes == ("text", "eol=lf"), (
        f"{CHASSIS_JSON_PATTERN} の属性が {attributes} である。"
        "設定ファイルの書き出しは LF であるため text eol=lf を宣言すること"
    )


def test_chassis_config_json_resolves_to_lf() -> None:
    """`configs/chassis_mechanism/*.json` の実効属性が LF である（要件 1.1）。

    ここで実効値を問うのは、順序を取り違えたまま追記しても
    `.gitattributes` の文字列照合では気づけないためである。ファイルはまだ存在しないが、
    `git check-attr` はパス名に対して属性を解決するため、先に規約だけを固定できる。
    """
    paths = [
        "configs/chassis_mechanism/dimensions.json",
        "configs/chassis_mechanism/layout.json",
        "configs/chassis_mechanism/anything-else.json",
    ]
    resolved = _check_attr(["text", "eol"], paths)
    for path in paths:
        attributes = resolved[path]
        assert attributes["eol"] == "lf", (
            f"{path} の eol 属性が {attributes['eol']!r} である。"
            "設定ファイルの書き出しが書く LF と作業ツリーの内容を一致させること"
        )
        assert attributes["text"] == "set", (
            f"{path} の text 属性が {attributes['text']!r} である"
        )


def test_lf_exception_stays_scoped_to_this_spec_configs() -> None:
    """例外が本 Spec の設定ディレクトリに閉じており、他の `*.json` は CRLF のままである。

    tasks.md「改行コード」が `.md` と他の `.json` は CRLF と定めている。一般則そのものを
    弱める形の追記（例: `*.json text eol=lf` への書き換え）を検出するための足場である。
    ⚠️ 上流 `configs/catch_mechanism/*.json` の LF 例外は上流の所有物であり、
    本 Spec はそれを壊していないことだけを確かめる。
    """
    resolved = _check_attr(
        ["eol"],
        [
            "configs/trajectory_sim/drivetrain-wheel60.json",
            "package-lock.json",
            "configs/catch_mechanism/dimensions.json",
        ],
    )
    assert resolved["configs/trajectory_sim/drivetrain-wheel60.json"]["eol"] == "crlf"
    assert resolved["package-lock.json"]["eol"] == "crlf"
    assert resolved["configs/catch_mechanism/dimensions.json"]["eol"] == "lf"
