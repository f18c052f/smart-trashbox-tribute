"""形状指標の記録の書き出しと識別子照合（タスク 2.5、要件 1.12）。

design.md `#### Baseline` の Preconditions / Postconditions / Invariants と、
tasks.md タスク 2.5 の「観測可能な完了状態」——「寸法を1つ変えると識別子照合が
失敗し、⚠️ **形状ライブラリ非導入の環境でもこの不整合を検出できる**」——を固定する。

## 本ファイルが固定する4つのこと

1. **型と照合は上流から借り、書き出しだけを本 Spec が持つ**（design.md
   `#### Baseline`「**型と照合は上流から借りる**」「**書き出しだけ本 Spec が
   持つ**」）。⚠️ 上流 `catch_mechanism` が意図して閉じている非公開操作へは
   到達しない——上流 `__init__` docstring「公開しないもの」の面である。借りた
   `load_baseline` が**本 Spec の記録をそのまま読み戻せる**ことを往復で確かめ、
   同じ読み込みを自前で書き直していないことの根拠にする。
2. **書き出しは原子的**（design.md `#### Baseline` Postconditions「書き出しは
   原子的。⚠️ 途中で失敗しても既存の記録を壊さない」）。⚠️ **正常系だけでは
   原子性は何も検証できない。** 直列化と確定のそれぞれで実際に失敗を注入し、
   既存の記録が**バイト単位で不変**であること・一時ファイルが残らないこと・
   確定の瞬間まで書き出し先が旧内容のままであることを見る。
3. **識別子の不一致は本 Spec の `ConsistencyError`**（design.md `#### Baseline`
   Invariants、`errors.ConsistencyError`「メッセージには**記録側と現在値の
   双方**（および参照元）を載せる」）。⚠️ 上流の同名例外とは別の型である。
4. ⚠️ **観測可能な完了状態**: 寸法を1つ変えた状態の検出が、形状ライブラリを
   遮断した**実プロセス**で成立し、その経路が形状ライブラリを（遅延 import
   ですら）読み込んでいないこと。遮断技法は
   `tests/chassis_mechanism/test_chassis_upstream_contract.py` および上流
   `tests/catch_mechanism/test_catch_baseline_digest.py` と同一である
   ——`PYTHONPATH` の先頭へ `ImportError` を送出するスタブを置く。
   ⚠️ **本リポジトリの `.venv` には形状ライブラリが導入済み**であり、遮断せずに
   通しても全件緑になる。遮断が効いていることを親子の両方で毎回確かめる。

## ⚠️ 出荷ファイルを書き換えない

`configs/chassis_mechanism/dimensions.json` は**読むだけ**である。改変は必ず
`tmp_path` の写しに対して行う。

## 部品の指標は「試験用の作り物」である

本ファイルが組み立てる `PartMetrics` は、記録の**器**が働くことを見るための値で
あって、実部品の指標ではない。⚠️ **出荷される記録
（`configs/chassis_mechanism/geometry-baseline.json`）はタスク 4.2 が実形状から
生成する**（tasks.md タスク 4.2「現在の寸法パラメータから全部品を生成し、指標の
記録を作る」）。本ファイルは出荷記録の中身を主張しない。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名が
セッション全体でフラットであるため、`test_chassis_` 接頭辞を付ける
（design.md「Directory Structure」）。
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from catch_mechanism import (
    ABSENT,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    PartMetrics,
    compare_metrics,
    load_baseline,
)
from catch_mechanism import ConsistencyError as UpstreamConsistencyError

from chassis_mechanism import baseline as baseline_module
from chassis_mechanism.baseline import (
    DEFAULT_BASELINE_PATH,
    dump_baseline,
    verify_digest,
)
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    load_params,
    parameters_digest,
)
from chassis_mechanism.errors import ConsistencyError

MODULE_PATH = Path(__file__).resolve()
REPO_ROOT = MODULE_PATH.parents[2]
BASELINE_SOURCE = Path(baseline_module.__file__).resolve().read_text(encoding="utf-8")

RECORD_NAME = "geometry-baseline.json"

UPSTREAM_PACKAGE = "catch_mechanism"

UNPUBLISHED_UPSTREAM_OPERATIONS = frozenset({"write_baseline", "verify_baseline_digest"})
"""上流が**意図して**公開していない、記録を書き換える操作。

`tests/chassis_mechanism/test_chassis_upstream_contract.py` の同名の表と対を成す。
⚠️ 本 Spec が**自前の**書き出しを定義することは違反ではない。違反は「上流のそれ
へ到達すること」である。
"""

SHAPE_LIBRARY_ROOTS = frozenset({"build123d", "OCP"})
"""形状ライブラリと、その推移依存である OCCT バインディングのトップレベル名。"""


# ---------------------------------------------------------------------------
# ヘルパ: 寸法設定の写しと改変（⚠️ 出荷ファイルへは書かない）
# ---------------------------------------------------------------------------


def _copy_shipped_dimensions(directory: Path, name: str = "dimensions.json") -> Path:
    """出荷の `dimensions.json` をバイト列のまま `directory` へ複製して返す。"""
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / name
    copied.write_bytes(DEFAULT_DIMENSIONS_PATH.read_bytes().replace(b"\r\n", b"\n"))
    return copied


def _edit_json(path: Path, mutate: Any) -> Path:
    """`path` の JSON を読み、`mutate` で書き換えて書き戻す。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


def _one_dimension_changed(directory: Path) -> Path:
    """寸法を**1つだけ**変えた写しを返す（タスク 2.5 の観測可能な完了状態）。

    ハブ板の外径を 1mm 動かす。⚠️ 形状にも意味のある変更だが、本ファイルが見るのは
    指標ではなく識別子であり、**形状を再生成せずに**古さが判る（要件 1.12）。
    """
    copied = _copy_shipped_dimensions(directory, name="dimensions-changed.json")

    def _bump(document: dict[str, Any]) -> None:
        document["base"]["hub_outer_diameter_mm"] = (
            float(document["base"]["hub_outer_diameter_mm"]) + 1.0
        )

    return _edit_json(copied, _bump)


def _only_provenance_promoted(directory: Path) -> Path:
    """**値は変えず**出所を仮値→実測へ昇格しただけの写しを返す。

    ⚠️ 数値の差分が1つも無いため目視では「何も変えていない」ように見えるが、
    識別子は出所表を含むため変わる（`config._canonical_payload`）。
    """
    copied = _copy_shipped_dimensions(directory, name="dimensions-promoted.json")

    def _promote(document: dict[str, Any]) -> None:
        provenance = document["provenance"]
        for key in sorted(provenance):
            if provenance[key] == "assumed":
                provenance[key] = "measured"
                return
        raise AssertionError("昇格できる仮値が出荷の出所表に無い（前提が崩れている）")

    return _edit_json(copied, _promote)


# ---------------------------------------------------------------------------
# ヘルパ: 試験用の記録（⚠️ 実部品の指標ではない。本モジュール docstring 参照）
# ---------------------------------------------------------------------------


def _probe_parts() -> dict[str, PartMetrics]:
    """記録の器を試すための、作り物の部品指標2件。"""
    return {
        "probe_alpha": PartMetrics(
            part_name="probe_alpha",
            volume_mm3=1000.0,
            bbox_mm=(10.0, 20.0, 30.0),
            solid_count=1,
        ),
        "probe_beta": PartMetrics(
            part_name="probe_beta",
            volume_mm3=250.5,
            bbox_mm=(5.0, 5.0, 10.0),
            solid_count=2,
        ),
    }


def _record(
    digest: str, parts: dict[str, PartMetrics] | None = None
) -> GeometryBaseline:
    """`digest` を埋め込んだ試験用の記録を組み立てる。"""
    return GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest=digest,
        volume_rel_tolerance=0.01,
        bbox_abs_tolerance_mm=0.1,
        generator_version="test-fixture",
        parts=_probe_parts() if parts is None else parts,
    )


def _shipped_digest() -> str:
    """出荷の寸法設定の識別子。"""
    return parameters_digest(load_params().chassis)


# ---------------------------------------------------------------------------
# ヘルパ: 失敗を注入する差し替え（⚠️ `baseline` の名前だけを差し替える）
# ---------------------------------------------------------------------------


class _JsonWithFailingDumps:
    """`dumps` だけが失敗し、他は本物の `json` へ委譲する差し替え。

    ⚠️ グローバルの `json.dumps` を直接潰さない。潰すと、記録の直列化以外
    （識別子の正規化など）まで巻き添えになり、注入した失敗が「どこで起きたか」を
    言えなくなる。
    """

    message = "直列化の注入された失敗"

    def __getattr__(self, name: str) -> Any:
        return getattr(json, name)

    def dumps(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError(self.message)


class _OsWithFailingReplace:
    """`replace` だけが失敗し、他は本物の `os` へ委譲する差し替え。"""

    message = "確定の注入された失敗"

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def replace(self, *args: object, **kwargs: object) -> None:
        raise OSError(self.message)


class _OsWatchingReplace:
    """`replace` の呼び出し時点の状態を記録してから本物へ委譲する差し替え。"""

    def __init__(self) -> None:
        self.source: Path | None = None
        self.source_bytes: bytes | None = None
        self.destination_bytes: bytes | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(os, name)

    def replace(self, source: Any, destination: Any) -> None:
        self.source = Path(source)
        self.source_bytes = Path(source).read_bytes()
        destination_path = Path(destination)
        self.destination_bytes = (
            destination_path.read_bytes() if destination_path.exists() else None
        )
        os.replace(source, destination)


# ---------------------------------------------------------------------------
# ヘルパ: `baseline.py` の静的な検分
# ---------------------------------------------------------------------------


def _import_edges() -> list[tuple[str, str]]:
    """`baseline.py` の import を `(モジュール, 名前)` の並びで返す。

    ⚠️ 文字列としての照合ではなく構文木で見る。docstring が説明のために書いた
    名前を「import している」と誤読しないためである。
    """
    edges: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(BASELINE_SOURCE)):
        if isinstance(node, ast.Import):
            edges.extend((alias.name, "") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            edges.extend((module, alias.name) for alias in node.names)
    return edges


def _string_constants() -> set[str]:
    """`baseline.py` に現れる文字列リテラルの集合（docstring を含む）。"""
    return {
        node.value
        for node in ast.walk(ast.parse(BASELINE_SOURCE))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


# ---------------------------------------------------------------------------
# 1. 既定パスと借用の範囲
# ---------------------------------------------------------------------------


def test_default_path_points_at_this_specs_record() -> None:
    """既定パスが**本 Spec の**記録を指す（上流の記録ではない）。"""
    assert DEFAULT_BASELINE_PATH.name == RECORD_NAME
    assert DEFAULT_BASELINE_PATH.parent.name == "chassis_mechanism"
    assert DEFAULT_BASELINE_PATH.parent.parent.name == "configs"
    assert DEFAULT_BASELINE_PATH.parent.parent.parent == REPO_ROOT


def test_the_module_borrows_from_the_upstream_package_root_only() -> None:
    """上流へは公開入口からのみ触れる（内部モジュールへ直接 import しない）。"""
    upstream = [
        (module, name)
        for module, name in _import_edges()
        if module.split(".")[0] == UPSTREAM_PACKAGE
    ]
    assert upstream, "上流から型と照合を借りていない（design.md `#### Baseline`）"
    for module, _ in upstream:
        assert module == UPSTREAM_PACKAGE, f"上流の内部モジュール {module} を import している"


def test_the_module_does_not_reach_upstreams_unpublished_operations() -> None:
    """上流が意図して閉じている操作へ到達しない（要件 1.3）。

    ⚠️ 本 Spec は**書き出しを自前で実装する**のであって、非公開関数を掘り出して
    使うのではない（`test_chassis_upstream_contract.py` の
    `UNPUBLISHED_UPSTREAM_OPERATIONS` docstring）。
    """
    reached = sorted(
        f"{module}.{name}"
        for module, name in _import_edges()
        if module.split(".")[0] == UPSTREAM_PACKAGE
        and name in UNPUBLISHED_UPSTREAM_OPERATIONS
    )
    assert reached == [], f"上流の非公開操作へ到達している: {reached}"


def test_the_module_does_not_import_the_shape_library() -> None:
    """形状ライブラリを import しない（design.md「Allowed Dependencies」）。"""
    roots = {module.split(".")[0] for module, _ in _import_edges()}
    assert roots.isdisjoint(SHAPE_LIBRARY_ROOTS)


def test_the_module_does_not_write_down_the_presence_encoding() -> None:
    """在／不在の符号化を literal で書き写していない（上流の定数が正）。

    ⚠️ 記録の器が `PRESENCE_FIELD` を運べることはタスク 4.2 の回帰検査の前提で
    あり、その符号化の正は上流にある。写した瞬間に二重管理が生まれる。
    """
    assert PRESENCE_FIELD not in _string_constants()
    assigned = {
        target.id
        for node in ast.walk(ast.parse(BASELINE_SOURCE))
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    assert assigned.isdisjoint({"PRESENCE_FIELD", "PRESENT", "ABSENT"})


# ---------------------------------------------------------------------------
# 2. 書き出しの形式と、上流 `load_baseline` による読み戻し
# ---------------------------------------------------------------------------


def test_dump_writes_lf_indent_two_sorted_keys_and_a_trailing_newline(
    tmp_path: Path,
) -> None:
    """整形は `config.dump_params` に揃う（LF・インデント2・キー整列・末尾改行）。

    ⚠️ LF は `.gitattributes` の `configs/chassis_mechanism/*.json text eol=lf` と
    対で成立する——書いたバイト列が git のチェックアウト内容と同一であるため、
    値が変わっていなければ `git status` は変更を報告しない。
    """
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)

    raw = target.read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("utf-8")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")

    document = json.loads(text)
    assert text == (
        json.dumps(
            document, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True
        )
        + "\n"
    )


def test_dump_writes_the_upstream_record_shape(tmp_path: Path) -> None:
    """記録は上流 `GeometryBaseline` の形にそのまま従う（design.md「Data Models」）。"""
    target = tmp_path / RECORD_NAME
    record = _record(_shipped_digest())
    dump_baseline(record, target)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "parameters_digest",
        "volume_rel_tolerance",
        "bbox_abs_tolerance_mm",
        "generator_version",
        "parts",
    }
    assert set(document["parts"]) == set(record.parts)
    entry = document["parts"]["probe_alpha"]
    assert set(entry) == {"volume_mm3", "bbox_mm", "solid_count"}
    # ⚠️ 境界箱は配列であり、並びは軸の順（X, Y, Z）である。
    assert entry["bbox_mm"] == [10.0, 20.0, 30.0]


def test_upstream_load_baseline_reads_a_record_written_here(tmp_path: Path) -> None:
    """借りた `load_baseline` が本 Spec の記録をそのまま読み戻す。

    ⚠️ **これが成り立つ限り、読み込みを自前で書き直す理由は無い**（design.md
    `#### Baseline`「型と照合は上流から借りる」）。上流の `load_baseline` はパスを
    引数に取るため、本 Spec の記録へ向けられる。
    """
    target = tmp_path / RECORD_NAME
    record = _record(_shipped_digest())
    dump_baseline(record, target)

    assert load_baseline(target) == record


def test_dump_replaces_an_existing_record(tmp_path: Path) -> None:
    """既存の記録は置き換えられる（書き出し先が育ち続けない）。"""
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)
    smaller = _record(
        _shipped_digest(), parts={"probe_alpha": _probe_parts()["probe_alpha"]}
    )
    dump_baseline(smaller, target)

    assert load_baseline(target) == smaller


def test_dump_leaves_no_temporary_file_behind_on_success(tmp_path: Path) -> None:
    """成功時に一時ファイルを残さない。"""
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)

    assert sorted(path.name for path in tmp_path.iterdir()) == [RECORD_NAME]


def test_a_missing_output_directory_fails_as_an_oserror(tmp_path: Path) -> None:
    """出力先の不備は `OSError` のまま伝える（記録内容の不正ではない）。"""
    with pytest.raises(OSError):
        dump_baseline(_record(_shipped_digest()), tmp_path / "absent" / RECORD_NAME)


# ---------------------------------------------------------------------------
# 3. 原子性（⚠️ 失敗を注入して確かめる。正常系では何も検証できない）
# ---------------------------------------------------------------------------


def _existing_record(tmp_path: Path) -> tuple[Path, bytes]:
    """先に書き出した記録と、そのバイト列を返す（失敗注入の対照）。"""
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)
    return target, target.read_bytes()


def test_a_serialization_failure_leaves_the_existing_record_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直列化が失敗しても、既存の記録は1バイトも変わらない。"""
    target, before = _existing_record(tmp_path)
    replacement = _record(_shipped_digest(), parts={"probe_beta": _probe_parts()["probe_beta"]})

    monkeypatch.setattr(baseline_module, "json", _JsonWithFailingDumps())

    with pytest.raises(RuntimeError, match=_JsonWithFailingDumps.message):
        dump_baseline(replacement, target)

    assert target.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [RECORD_NAME]


def test_a_replace_failure_leaves_the_existing_record_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確定が失敗しても、既存の記録は1バイトも変わらず一時ファイルも残らない。

    ⚠️ 一時ファイルを書き終えた段階での失敗である。ここで後始末を怠ると、
    書き出し先のディレクトリに正体不明のファイルが残る。
    """
    target, before = _existing_record(tmp_path)
    replacement = _record(_shipped_digest(), parts={"probe_beta": _probe_parts()["probe_beta"]})

    monkeypatch.setattr(baseline_module, "os", _OsWithFailingReplace())

    with pytest.raises(OSError, match=_OsWithFailingReplace.message):
        dump_baseline(replacement, target)

    assert target.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [RECORD_NAME]


def test_the_record_is_only_installed_at_the_moment_of_the_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確定の直前まで、書き出し先は**旧内容のまま**である。

    ⚠️ これが原子性の本体である。書き込みが確定先へ直接流れていれば、確定を
    呼ぶ時点で既に内容が変わっている（あるいは切り詰められている）。
    """
    target, before = _existing_record(tmp_path)
    updated = _record(_shipped_digest(), parts={"probe_beta": _probe_parts()["probe_beta"]})
    watcher = _OsWatchingReplace()

    monkeypatch.setattr(baseline_module, "os", watcher)
    dump_baseline(updated, target)

    assert watcher.destination_bytes == before, (
        "確定前に書き出し先の内容が変わっている（一時領域を経ていない）"
    )
    assert watcher.source_bytes == target.read_bytes()
    assert load_baseline(target) == updated


def test_the_temporary_file_is_created_beside_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一時ファイルは確定先と**同じディレクトリ**に作る。

    ⚠️ 別のファイルシステム（`/tmp` など）へ置くと確定が
    `OSError: Invalid cross-device link` になり、原子的な差し替えが成立しない。
    """
    target = tmp_path / RECORD_NAME
    watcher = _OsWatchingReplace()

    monkeypatch.setattr(baseline_module, "os", watcher)
    dump_baseline(_record(_shipped_digest()), target)

    assert watcher.source is not None
    assert watcher.source.parent == target.parent
    assert watcher.source != target


# ---------------------------------------------------------------------------
# 4. 識別子照合（design.md `#### Baseline` Invariants / 要件 1.12）
# ---------------------------------------------------------------------------


def test_verify_digest_accepts_a_record_carrying_the_current_digest() -> None:
    """識別子が一致する記録は受け入れられる（対照）。"""
    params = load_params().chassis
    assert verify_digest(_record(parameters_digest(params)), params) is None


def test_verify_digest_rejects_a_record_whose_digest_is_stale(tmp_path: Path) -> None:
    """⚠️ **観測可能な完了状態**: 寸法を1つ変えると識別子照合が失敗する。"""
    changed = load_params(_one_dimension_changed(tmp_path)).chassis
    stale = _record(_shipped_digest())

    assert parameters_digest(changed) != stale.parameters_digest
    with pytest.raises(ConsistencyError):
        verify_digest(stale, changed)


def test_verify_digest_names_both_digests_in_the_failure(tmp_path: Path) -> None:
    """失敗文に**双方の識別子と参照元**が現れる。

    `errors.ConsistencyError`「メッセージには**記録側と現在値の双方**（および
    参照元）を載せる」。どちらが古いのかを読み手に決めさせないための条件である。
    """
    changed = load_params(_one_dimension_changed(tmp_path)).chassis
    stale = _record(_shipped_digest())
    current = parameters_digest(changed)

    with pytest.raises(ConsistencyError) as raised:
        verify_digest(stale, changed)

    message = str(raised.value)
    assert stale.parameters_digest in message
    assert current in message
    assert DEFAULT_BASELINE_PATH.name in message
    assert DEFAULT_DIMENSIONS_PATH.name in message


def test_verify_digest_rejects_when_only_the_provenance_was_promoted(
    tmp_path: Path,
) -> None:
    """値は同じでも出所を昇格しただけで不一致になる。

    ⚠️ 数値の差分が1つも無いため目視では気付けない経路である。識別子は出所表を
    含むため（`config._canonical_payload`）、ここが唯一の可視化手段になる。
    """
    promoted = load_params(_only_provenance_promoted(tmp_path)).chassis
    stale = _record(_shipped_digest())

    assert parameters_digest(promoted) != stale.parameters_digest
    with pytest.raises(ConsistencyError):
        verify_digest(stale, promoted)


def test_verify_digest_raises_this_specs_consistency_error(tmp_path: Path) -> None:
    """例外は**本 Spec の** `ConsistencyError` である（上流の同名型ではない）。

    `errors.ConsistencyError`「⚠️ 上流 `catch_mechanism.ConsistencyError` とは別の
    型である。整合が壊れているのが上流の記録なのか本 Spec の記録なのかは、型でも
    区別できなければならない」。
    """
    changed = load_params(_one_dimension_changed(tmp_path)).chassis

    with pytest.raises(ConsistencyError) as raised:
        verify_digest(_record(_shipped_digest()), changed)

    assert not isinstance(raised.value, UpstreamConsistencyError)


def test_a_record_written_after_the_change_verifies_again(tmp_path: Path) -> None:
    """記録を書き直せば照合は再び成立する（不整合の直し方が閉じている）。"""
    changed = load_params(_one_dimension_changed(tmp_path)).chassis
    target = tmp_path / RECORD_NAME

    dump_baseline(_record(parameters_digest(changed)), target)
    assert verify_digest(load_baseline(target), changed) is None


# ---------------------------------------------------------------------------
# 5. 借りた照合が本 Spec の記録に対して働く（在／不在の符号化を含む）
# ---------------------------------------------------------------------------


def test_borrowed_comparison_works_on_a_record_written_here(tmp_path: Path) -> None:
    """`compare_metrics` は書き出した記録の読み戻しに対して素直に働く。"""
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)
    reloaded = load_baseline(target)

    assert compare_metrics(reloaded, dict(reloaded.parts)) == ()


def test_the_record_carries_the_presence_encoding_for_a_vanished_part(
    tmp_path: Path,
) -> None:
    """部品が消えた場合が在／不在の符号化で検出される（タスク 4.2 の前提）。"""
    target = tmp_path / RECORD_NAME
    dump_baseline(_record(_shipped_digest()), target)
    reloaded = load_baseline(target)

    remaining = {"probe_alpha": reloaded.parts["probe_alpha"]}
    mismatches = compare_metrics(reloaded, remaining)

    assert [mismatch.part_name for mismatch in mismatches] == ["probe_beta"]
    assert mismatches[0].field_name == PRESENCE_FIELD
    assert mismatches[0].recorded == PRESENT
    assert mismatches[0].regenerated == ABSENT


# ---------------------------------------------------------------------------
# 6. ⚠️ 観測可能な完了状態: 形状ライブラリ非導入の環境での検出
# ---------------------------------------------------------------------------

_STUB_MESSAGE = "build123d is blocked by the chassis baseline CAD-absence stub"
_STUB_SOURCE = f"raise ImportError({_STUB_MESSAGE!r})\n"

_PROBE_BODY = """
import importlib.util
import json
import sys

try:
    import build123d  # noqa: F401
except ImportError:
    blocked = True
else:
    blocked = False
sys.modules.pop("build123d", None)

spec = importlib.util.spec_from_file_location("chassis_baseline_probe", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
# `slots=True` のデータクラスは定義時に自身のモジュールを `sys.modules` から
# 引くため、実行前に登録しておく。
sys.modules[spec.name] = module
spec.loader.exec_module(module)

report = module.digest_mismatch_report(WORK_DIR)
report["stub_blocked_the_shape_library"] = blocked
report["shape_library_modules"] = sorted(
    name for name in sys.modules if name.split(".")[0] in ("build123d", "OCP")
)
report["upstream_cad_modules"] = sorted(
    name
    for name in sys.modules
    if name in ("catch_mechanism.shapes", "catch_mechanism.export")
)
print(json.dumps(report))
"""


def digest_mismatch_report(work_dir: str) -> dict[str, object]:
    """寸法を1つ変えた状態の検出を実演し、結果を素の値で返す。

    ⚠️ **子プロセスへ実演を書き写さない。** 書き写せば CAD 非導入の経路だけが
    古びる（`test_chassis_upstream_contract.py` の `_probe_source` と同じ規律）。
    子プロセスは本ファイルを import してこの関数を呼ぶため、検査の内容は本
    プロセスと**同一**である。
    """
    directory = Path(work_dir)
    directory.mkdir(parents=True, exist_ok=True)

    pristine = load_params(_copy_shipped_dimensions(directory)).chassis
    changed = load_params(_one_dimension_changed(directory)).chassis

    record_path = directory / RECORD_NAME
    dump_baseline(_record(parameters_digest(pristine)), record_path)
    reloaded = load_baseline(record_path)

    report: dict[str, object] = {
        "record_is_lf": b"\r\n" not in record_path.read_bytes(),
        "recorded_digest": reloaded.parameters_digest,
        "current_digest": parameters_digest(changed),
        "matching_record_verifies": verify_digest(reloaded, pristine) is None,
        "detected": False,
        "message": "",
        "error_type": "",
    }
    try:
        verify_digest(reloaded, changed)
    except ConsistencyError as exc:
        report["detected"] = True
        report["message"] = str(exc)
        report["error_type"] = type(exc).__name__
    return report


def _nocad_stub(tmp_path: Path) -> Path:
    """形状ライブラリの import が `ImportError` になるスタブ置き場を作って返す。

    ⚠️ スタブは `tmp_path` に置く（pytest が管理する一時ディレクトリ）。
    `PYTHONPATH` は site-packages より前に置かれるため、本物が導入済みでも勝つ。
    """
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "build123d.py").write_text(_STUB_SOURCE, encoding="utf-8")
    return stub_dir


def _blocked_env(stub_dir: Path) -> dict[str, str]:
    """`stub_dir` を先頭に足した `PYTHONPATH` を持つ環境変数の写しを返す。"""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")
    return env


def _run_blocked(code: str, stub_dir: Path) -> subprocess.CompletedProcess[str]:
    """形状ライブラリを遮断した実プロセスで `code` を走らせる。

    ⚠️ 復号は `encoding="utf-8"` を明示する（`text=True` はロケール依存）。
    """
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=300.0,
        check=False,
    )


def _probe_code(work_dir: Path) -> str:
    """子プロセスへ渡すソース（本ファイル自身をモジュールとして読み込ませる）。"""
    return (
        f"MODULE_PATH = {str(MODULE_PATH)!r}\n"
        f"WORK_DIR = {str(work_dir)!r}\n"
        f"{_PROBE_BODY}"
    )


def test_the_cad_blocking_stub_actually_blocks_the_shape_library(
    tmp_path: Path,
) -> None:
    """遮断スタブが効いていることを先に確かめる（後続を空振りにしないため）。

    ⚠️ `"ImportError" in stderr` だけでは「そもそも導入されていない」場合と
    区別できない。**スタブ固有のメッセージ**が出ることまで見る。
    """
    completed = _run_blocked("import build123d", _nocad_stub(tmp_path))

    assert completed.returncode != 0, completed.stdout
    assert "ImportError" in completed.stderr
    assert _STUB_MESSAGE in completed.stderr, (
        "遮断したつもりの実行が、スタブ以外の理由で失敗している"
    )


def test_the_digest_mismatch_is_detected_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """⚠️ **観測可能な完了状態**: 形状ライブラリ非導入の環境でも不整合を検出する。

    tasks.md タスク 2.5「寸法を1つ変えると識別子照合が失敗し、⚠️ **形状ライブラリ
    非導入の環境でもこの不整合を検出できる**ことをテストで固定する」。
    """
    completed = _run_blocked(_probe_code(tmp_path / "work"), _nocad_stub(tmp_path))

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)

    # 遮断が効いた状態での結果であること。
    assert payload["stub_blocked_the_shape_library"] is True
    assert payload["shape_library_modules"] == [], (
        "形状ライブラリが子プロセスの sys.modules に現れている"
    )
    assert payload["upstream_cad_modules"] == [], (
        "上流の CAD 層モジュールが読み込まれている（経路が形状層へ触れている）"
    )

    # 検出が成立していること。
    assert payload["detected"] is True
    assert payload["error_type"] == "ConsistencyError"
    assert payload["matching_record_verifies"] is True
    assert payload["record_is_lf"] is True
    assert payload["recorded_digest"] != payload["current_digest"]
    assert payload["recorded_digest"] in payload["message"]
    assert payload["current_digest"] in payload["message"]


def test_the_blocked_report_agrees_with_the_in_process_one(tmp_path: Path) -> None:
    """遮断下の結果が、形状ライブラリのある本プロセスの結果と一致する（対照）。

    ⚠️ 一致することが「この経路は形状ライブラリを要していない」ことの直接の
    証拠になる（片方だけを見ても、差が無いことは言えない）。
    """
    completed = _run_blocked(_probe_code(tmp_path / "blocked"), _nocad_stub(tmp_path))
    assert completed.returncode == 0, completed.stderr
    blocked = json.loads(completed.stdout)

    here = digest_mismatch_report(str(tmp_path / "here"))

    for key in ("recorded_digest", "current_digest", "detected", "error_type", "message"):
        assert blocked[key] == here[key], f"{key} が遮断の有無で食い違う"
