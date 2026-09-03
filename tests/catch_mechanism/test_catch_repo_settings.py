"""リポジトリ設定が生成物と外部 CAD の作業ファイルを成果物から外すことを検証する
（タスク 1.5 / 要件 3.5, 4.6, 4.7）。

本ファイルが固定するのは3点である。

1. **外部 CAD（FreeCAD）の作業ファイル `*.FCStd` / `*.FCStd1` と、生成物の出力先
   `var/cad/` 配下のファイルを実際に置いても、バージョン管理の未追跡ファイル一覧に
   現れない**（要件 3.5, 4.6 / タスク 1.5 の観測可能な完了状態）。
2. **生成物形式 `*.step` / `*.stl` / `*.3mf` が改行変換の対象外である**
   （design.md「Modified Files」`.gitattributes`）。誤ってコミットされても内容が壊れない。
3. **`configs/catch_mechanism/*.json` が作業ツリーでも LF に解決される**。
   要件 4.7 の「外部 CAD の測定値を設定ファイルへ書き戻す経路」は
   `catch_mechanism.config.dump_params` であり、これはプラットフォーム非依存に
   LF を書く。`.gitattributes` の一般則 `*.json text eol=crlf` のままだと
   書き戻すたびに `git status --porcelain` が「変更済み」を報告し続け
   （blob は同一なので `git diff` はゼロ差分）、書き戻し経路に幽霊の差分が乗る。

検査は `.gitattributes` の文字列照合ではなく **git 自身へ問い合わせる**
（`git check-attr` / `git status`）。属性は最後にマッチした行が勝つため、
一般則 `*.json` と個別則 `configs/catch_mechanism/*.json` の**順序を含めた
実効値**を確かめる必要があり、文字列照合ではそこを取り逃がす。

git が使えない環境・git チェックアウトでない環境では skip する。
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 実際に配置して未追跡一覧に出ないことを確かめる探り用のパス（リポジトリ相対）。
#: 外部 CAD の作業ファイルは置き場所を規約で縛っていないため、リポジトリ直下と
#: ソースツリー内の双方に置き、除外がパスに依存しないことも併せて固定する。
PROBE_PATHS = (
    "var/cad/task15_probe.step",
    "var/cad/task15_probe.stl",
    "var/cad/task15_probe.3mf",
    "task15_probe.FCStd",
    "task15_probe.FCStd1",
    "src/catch_mechanism/task15_probe.FCStd",
)

#: 改行変換の対象外であるべき生成物形式（design.md「Modified Files」）。
ARTIFACT_SUFFIXES = (".step", ".stl", ".3mf")

DIMENSIONS_PATH = REPO_ROOT / "configs" / "catch_mechanism" / "dimensions.json"


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


def _require_git_checkout() -> None:
    _run_git(["rev-parse", "--git-dir"])


def _check_attr(attributes: list[str], paths: list[str]) -> dict[str, dict[str, str]]:
    """`git check-attr` の実効値を `{path: {attr: value}}` で返す。

    `-z` を使うのは、出力を `": "` で分解するとパスに含まれる区切りと
    曖昧になるためである。
    """
    raw = _run_git(["check-attr", "-z", *attributes, "--", *paths])
    fields = raw.split("\0")
    result: dict[str, dict[str, str]] = {}
    for index in range(0, len(fields) - 2, 3):
        path, attribute, value = fields[index : index + 3]
        result.setdefault(path, {})[attribute] = value
    return result


@pytest.fixture
def placed_probe_files() -> Iterator[tuple[str, ...]]:
    """探り用のファイルを実際のパスへ置き、必ず後片付けする。

    アサーションが落ちても作業ツリーに何も残さないため、生成したファイルと
    **自分が作ったディレクトリだけ**を teardown で消す。
    """
    _require_git_checkout()
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for relative in PROBE_PATHS:
            target = REPO_ROOT / relative
            assert not target.exists(), (
                f"探り用のパス {relative} が既に存在する。"
                "前回の実行が後片付けに失敗した可能性がある"
            )
            for parent in reversed(target.parents):
                if REPO_ROOT in parent.parents and not parent.exists():
                    parent.mkdir()
                    created_dirs.append(parent)
            target.write_bytes(b"probe\n")
            created_files.append(target)
        yield PROBE_PATHS
    finally:
        for target in created_files:
            target.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()


def _untracked_paths() -> set[str]:
    raw = _run_git(["status", "--porcelain", "--untracked-files=all"])
    untracked: set[str] = set()
    for line in raw.splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        untracked.add(path)
    return untracked


def test_cad_working_files_and_artifacts_stay_out_of_untracked_list(
    placed_probe_files: tuple[str, ...],
) -> None:
    """外部 CAD の作業ファイルと生成物を置いても未追跡一覧に現れない（要件 3.5, 4.6）。

    タスク 1.5 の観測可能な完了状態そのもの。
    """
    untracked = _untracked_paths()
    leaked = sorted(path for path in placed_probe_files if path in untracked)
    assert leaked == [], (
        "外部 CAD の作業ファイル・生成物がバージョン管理の未追跡一覧に現れた: "
        f"{leaked}。.gitignore の除外が効いていない"
    )


def test_probe_paths_are_ignored_by_an_explicit_rule(
    placed_probe_files: tuple[str, ...],
) -> None:
    """未追跡一覧に出ない理由が「無視規則にマッチしたから」であることを固定する。

    `git check-ignore` は無視されるパスだけを出力するため、全件が返ることを
    確かめれば「たまたま追跡済みだった」などの別要因を排除できる。
    """
    completed = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=REPO_ROOT,
        input="\0".join(placed_probe_files),
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = {field for field in completed.stdout.split("\0") if field}
    missing = sorted(set(placed_probe_files) - ignored)
    assert missing == [], f".gitignore の除外規則にマッチしないパスがある: {missing}"


def test_artifact_formats_are_excluded_from_newline_conversion() -> None:
    """生成物形式が改行変換の対象外である（design.md「Modified Files」）。

    誤ってコミットされた場合でも内容が壊れないことを、`text` 属性が
    unset（`-text`）であることで固定する。`text` が `set` / `auto` のままだと
    STL・3MF のバイト列が改行変換で破壊されうる。
    """
    paths = [f"var/cad/sample{suffix}" for suffix in ARTIFACT_SUFFIXES]
    resolved = _check_attr(["text", "eol"], paths)
    for path in paths:
        attributes = resolved[path]
        assert attributes["text"] == "unset", (
            f"{path} の text 属性が {attributes['text']!r} である。"
            "生成物形式は改行変換の対象から外すこと（-text）"
        )
        assert attributes["eol"] == "unspecified", (
            f"{path} に eol={attributes['eol']!r} が指定されている。"
            "生成物形式には改行を指定しないこと"
        )


def test_catch_mechanism_config_json_resolves_to_lf() -> None:
    """`configs/catch_mechanism/*.json` の実効属性が LF である（要件 4.7）。

    一般則 `*.json text eol=crlf` より後ろに個別則を置く必要がある
    （git は最後にマッチした行を採用する）。ここで実効値を問うのは、
    その順序を取り違えたまま追記しても気づけないためである。
    """
    paths = [
        "configs/catch_mechanism/dimensions.json",
        "configs/catch_mechanism/anything-else.json",
    ]
    resolved = _check_attr(["text", "eol"], paths)
    for path in paths:
        attributes = resolved[path]
        assert attributes["eol"] == "lf", (
            f"{path} の eol 属性が {attributes['eol']!r} である。"
            "dump_params が書く LF と作業ツリーの内容を一致させること"
        )
        assert attributes["text"] == "set", (
            f"{path} の text 属性が {attributes['text']!r} である"
        )


def test_shipped_dimensions_json_has_no_cr_bytes() -> None:
    """同梱の `dimensions.json` が実際に LF で置かれている（要件 4.7）。

    属性を LF に直しても作業ツリーの実体が CRLF のままだと、
    `git status --porcelain` が変更済みを報告し続ける。
    """
    content = DIMENSIONS_PATH.read_bytes()
    assert b"\r" not in content, (
        f"{DIMENSIONS_PATH} に CR バイトが含まれる。"
        "dump_params が書き出す LF と一致させること"
    )
