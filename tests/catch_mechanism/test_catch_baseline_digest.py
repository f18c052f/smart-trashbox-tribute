"""パラメータ変更と指標記録の不整合検出（タスク 4.3 / 要件 4.5, 5.7）。

design.md「Testing Strategy」の Integration Tests 2——「`dimensions.json` を変更
して記録を更新しない状態が、**形状ライブラリ非導入の環境でも**失敗として検出
される」——を担う。⚠️ design.md「Directory Structure」は本ファイルを
`test_baseline_digest.py` と呼ぶが、`tests/` 配下に `__init__.py` が無く
テストモジュール名がフラットな名前空間を共有するため、本ディレクトリの既存
ファイルと同じく `test_catch_` 接頭辞を付ける（tasks.md「Implementation Notes」
タスク 1.1）。

## 本ファイルが固定する3つのこと

1. **状況の再現**（要件 4.5）: 寸法設定ファイルを変更し、記録を更新しないまま
   `verify_baseline_digest` を当てると `ConsistencyError` になり、**双方の識別子と
   双方の参照元**が失敗文に現れる。⚠️ 値を動かす経路だけでなく、値は同じまま
   出所を仮値→実測へ**昇格しただけ**の経路も検出できることを別々に固定する
   （tasks.md「Implementation Notes」タスク 1.4(a)。識別子は出所表を含む）。
2. **CAD 非導入環境での実行**（要件 5.7）: 形状ライブラリを遮断した**実プロセス**で
   同じ検出が成立し、検出の経路が `build123d` を（遅延 import ですら）読み込んで
   いないことを `sys.modules` で確かめる。
3. ⚠️ **検査そのものが CAD 依存として印を付けられていないこと（メタ検査）**:
   `test_catch_geometry_regression.py` の
   `test_the_shipped_record_matches_the_current_dimensions_digest` は、出荷された
   記録と現在の `dimensions.json` の一致を主張する**正常側**の検査である。
   タスク 4.2 のレビューで、この関数に `@requires_cad` を付けても**どのテストも
   落ちない**ことが判明した（tasks.md「Implementation Notes」タスク 4.2(c)）。
   すなわち「この検査は CAD 非導入環境でも走る」という要件 5.7 の保証は、
   誰も見張っていなかった。本ファイルはこれを2重に固定する:

   - `test_the_shipped_digest_check_is_not_marked_as_requiring_cad`
     ——関数と module の pytest マークを直接読み、skip 系が付いていないことを見る
   - `test_the_shipped_digest_check_actually_runs_with_the_shape_library_blocked`
     ——形状ライブラリを遮断した実プロセスで pytest を起動し、当該テストが
     **skip されずに通る**ことを見る（module 直下の `importorskip` へ逃げても
     こちらが捕まえる）

## ⚠️ 出荷ファイルを書き換えない

`configs/catch_mechanism/dimensions.json` は**読むだけ**である。変更は必ず
`tmp_path` の写しに対して行い、`load_params(path)` でその写しを指す。出荷ファイルを
1バイトでも書き換えると、以降のテストと `git status` が巻き添えになる
（`test_catch_geometry_regression.py` の同趣旨の注記を参照）。

## ⚠️ 遮断のスタブは毎回「効いていること」を確かめる

`PYTHONPATH` に置いた `build123d.py` のスタブは、置き場所が消えると黙って無効に
なる——「遮断したつもりの実行」が CAD 導入時と同じ数字を返す（tasks.md
「Implementation Notes」タスク 4.2(f)）。本ファイルは遮断下の実行の**前後**で
親プロセスから `import build123d` の失敗を確かめ、さらに子プロセス自身にも冒頭で
同じ確認をさせる。緑は「遮断が効いていた」ことまで含めて意味を持つ。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from catch_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    load_params,
    parameters_digest,
)
from catch_mechanism.errors import ConsistencyError
from catch_mechanism.metrics import (
    DEFAULT_BASELINE_PATH,
    load_baseline,
    verify_baseline_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

REGRESSION_PATH = Path(__file__).resolve().with_name("test_catch_geometry_regression.py")
"""出荷記録の回帰検査。⚠️ 本ファイルは**読む・import する**だけで編集しない
（タスク 4.3 の境界は `Metrics` であり、4.2 の成果物は 4.2 の所有物である）。"""

DIGEST_TEST_NAME = "test_the_shipped_record_matches_the_current_dimensions_digest"
"""要件 5.7 が「非導入環境でも走る」ことを求める検査の名。

⚠️ この名を変えるとメタ検査が「関数が見つからない」で落ちる。それは正しい失敗で
ある——見張る対象が消えたのに緑のままになるほうが悪い。
"""

_SKIP_MARK_NAMES = frozenset({"skip", "skipif"})
"""実行を取り止める pytest マーク。⚠️ `@requires_cad` は `skipif` である。"""

_BLOCKED_MARKER = "OK-DETECTED-WITHOUT-CAD"
"""遮断下の子プロセスが「不整合を検出した」ことを親へ伝える合図。"""


# ---------------------------------------------------------------------------
# 補助: 出荷ファイルの写しと、その改変
# ---------------------------------------------------------------------------


def _copy_shipped_dimensions(directory: Path) -> Path:
    """出荷の `dimensions.json` を `directory` へ複製して返す。

    ⚠️ **出荷ファイルそのものは絶対に書き換えない**（本モジュール docstring）。
    複製はバイト列のまま行う——`load_params` → `dump_params` を経由すると、
    「写しただけ」と「整形で変わった」が区別できなくなる。
    """
    directory.mkdir(parents=True, exist_ok=True)
    copied = directory / "dimensions.json"
    copied.write_bytes(DEFAULT_DIMENSIONS_PATH.read_bytes())
    return copied


def _edit_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> Path:
    """`path` の JSON を読み、`mutate` で書き換えて書き戻す。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _stale_by_value(directory: Path) -> Path:
    """寸法の**値**を変えた写しを返す（記録は更新しない）。

    リムの高さを 1mm 動かす。⚠️ 造形上も意味のある変更であり、形状指標も変わる
    ——だが本ファイルが見るのは指標ではなく識別子であり、**形状を再生成せずに**
    古さが判る（要件 4.5）。
    """
    copied = _copy_shipped_dimensions(directory)

    def _bump(document: dict[str, Any]) -> None:
        document["rim"]["height_mm"] = float(document["rim"]["height_mm"]) + 1.0

    return _edit_json(copied, _bump)


def _stale_by_provenance(directory: Path) -> Path:
    """**値は変えず**出所を仮値→実測へ昇格した写しを返す（記録は更新しない）。

    ⚠️ これは要件 4.5 の中でも見落としやすい経路である。数値の差分が1つも無い
    ため目視では「何も変えていない」ように見えるが、識別子は出所表を含むため
    変わる（tasks.md「Implementation Notes」タスク 1.4(a)）。「まだ仮値だった
    ときの記録」を CAD 非導入環境で可視化する唯一の手段がこれである。
    """
    copied = _copy_shipped_dimensions(directory)

    def _promote(document: dict[str, Any]) -> None:
        provenance = document["provenance"]
        for key in sorted(provenance):
            if provenance[key] == "assumed":
                provenance[key] = "measured"
                return
        raise AssertionError("昇格できる仮値が出荷の出所表に無い（前提が崩れている）")

    return _edit_json(copied, _promote)


# ---------------------------------------------------------------------------
# 補助: 形状ライブラリの遮断と実プロセス起動
# ---------------------------------------------------------------------------


def _nocad_stub(tmp_path: Path) -> Path:
    """`import build123d` が `ImportError` になるスタブ置き場を作って返す。

    ⚠️ `PYTHONPATH` は site-packages より**前**に置かれるため、本物が導入済みでも
    こちらが勝つ。⚠️ スタブ置き場が消えると遮断は黙って無効になるので、遮断下の
    実行は毎回 `_assert_stub_blocks` と `_blocked_preamble` で確かめる。
    """
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "build123d.py").write_text(
        "raise ImportError(\"No module named 'build123d' (test stub)\")\n",
        encoding="utf-8",
    )
    return stub_dir


def _blocked_env(stub_dir: Path) -> dict[str, str]:
    """`stub_dir` を先頭に足した `PYTHONPATH` を持つ環境変数の写しを返す。"""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")
    return env


def _blocked_preamble() -> str:
    """子プロセスの冒頭に置く「遮断が効いていること」の確認コード。

    ⚠️ tasks.md「Implementation Notes」タスク 4.2(f): 遮断実行のたびに
    `import build123d` が `ImportError` になることを確かめること。確認を省いた
    遮断は、置き場所が消えた瞬間に CAD 導入環境と同じ実行へ化ける。
    """
    return textwrap.dedent(
        """
        import sys

        try:
            import build123d  # noqa: F401
        except ImportError:
            pass
        else:
            raise SystemExit("STUB-INEFFECTIVE: build123d was importable")
        sys.modules.pop("build123d", None)
        """
    ).strip()


def _run_blocked(code: str, stub_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """形状ライブラリを遮断した実プロセスで `code` を走らせる。

    ⚠️ 復号は `encoding="utf-8"` を明示する（`text=True` はロケール依存であり、
    tasks.md「Implementation Notes」タスク 3.4(a) の副作用に巻き込まれうる）。
    """
    return subprocess.run(
        [sys.executable, "-c", f"{_blocked_preamble()}\n{code}", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=300.0,
        check=False,
    )


def _assert_stub_blocks(stub_dir: Path) -> None:
    """スタブが本当に `import build123d` を止めていることを親側でも確かめる。

    子プロセス側の `_blocked_preamble` と重複するが、⚠️ **遮断の前後で確かめる**
    ことが申し送りの指示である（タスク 4.2(f)）。片方だけでは「遮断が効いて
    いなかった実行」を「検出できた実行」として読んでしまう。
    """
    completed = subprocess.run(
        [sys.executable, "-c", "import build123d"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=120.0,
        check=False,
    )

    assert completed.returncode != 0, (
        f"{stub_dir} のスタブが効いていない（build123d が import できてしまった）。"
        "遮断できていない実行の結果は、CAD 導入環境の結果と区別が付かない。"
    )
    assert "ImportError" in completed.stderr


# ---------------------------------------------------------------------------
# 補助: 回帰検査モジュールの読み出し（⚠️ 編集ではなく検分）
# ---------------------------------------------------------------------------


def _regression_module() -> ModuleType:
    """`test_catch_geometry_regression.py` を module として得る。

    pytest が既に import 済みならそれを使い（同じ関数オブジェクトを見たい）、
    単独実行などで未 import なら file から読み込む。⚠️ `sys.modules` へは登録
    しない——pytest が持つ収集済みの module を後から差し替えない。

    ⚠️ **`sys.modules` を全走査して `Path.resolve()` してはならない。** build123d を
    読み込んだ状態では module が数千あり、`/mnt/c` の 9p マウント上では1回あたり
    約7秒かかる。pytest は既定（rootdir 相対の prepend）で `test_catch_geometry_regression`
    という素の名前で登録するため、名前で引けば済む。
    """
    module = sys.modules.get(REGRESSION_PATH.stem)
    if module is not None and getattr(module, "__file__", None):
        if Path(module.__file__).resolve() == REGRESSION_PATH:
            return module

    spec = importlib.util.spec_from_file_location(
        "_catch_geometry_regression_under_inspection", REGRESSION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mark_names(obj: object) -> set[str]:
    """`obj` に付いた pytest マークの名を返す（未指定なら空）。"""
    return {mark.name for mark in getattr(obj, "pytestmark", ())}


# ---------------------------------------------------------------------------
# 1. 状況の再現: 寸法を変えて記録を更新しない（要件 4.5）
# ---------------------------------------------------------------------------


def test_an_untouched_copy_of_the_shipped_dimensions_still_matches_the_record(
    tmp_path: Path,
) -> None:
    """写しただけの寸法設定は出荷記録と一致する（対照）。

    ⚠️ これが無いと、後続の2件が「写しを作った副作用で落ちている」のか
    「変更を検出した」のかを言い分けられない。
    """
    copied = _copy_shipped_dimensions(tmp_path / "untouched")
    current = parameters_digest(load_params(copied))

    assert (
        verify_baseline_digest(
            load_baseline(),
            current,
            baseline_path=DEFAULT_BASELINE_PATH,
            dimensions_path=copied,
        )
        == current
    )


def test_changing_a_dimension_without_updating_the_record_fails_the_check(
    tmp_path: Path,
) -> None:
    """寸法の値を変えて記録を更新しないと検査が失敗する（要件 4.5）。

    ⚠️ **形状を再生成していない。** 読んだのは寸法設定ファイルと記録の2つだけで
    あり、比べたのは識別子の文字列だけである。これが「形状生成の環境が利用でき
    ない環境であっても、この不整合を検出して失敗させる」（要件 4.5）の実体である。
    """
    stale = _stale_by_value(tmp_path / "value")
    baseline = load_baseline()
    current = parameters_digest(load_params(stale))

    assert current != baseline.parameters_digest, (
        "寸法を変えたのに識別子が動いていない（config.parameters_digest の退行）"
    )
    with pytest.raises(ConsistencyError):
        verify_baseline_digest(
            baseline,
            current,
            baseline_path=DEFAULT_BASELINE_PATH,
            dimensions_path=stale,
        )


def test_the_failure_names_both_digests_and_both_files(tmp_path: Path) -> None:
    """失敗文が**双方の識別子と双方の参照元**を載せる（要件 4.5 / `errors.py`）。

    ⚠️ 片方しか出さない失敗は、どちらが古いのかを読み手に決めさせてしまう。
    記録が古いのか設定を戻し忘れたのかは、4つの情報が揃って初めて自力で判る。
    """
    stale = _stale_by_value(tmp_path / "value")
    baseline = load_baseline()
    current = parameters_digest(load_params(stale))

    with pytest.raises(ConsistencyError) as excinfo:
        verify_baseline_digest(
            baseline,
            current,
            baseline_path=DEFAULT_BASELINE_PATH,
            dimensions_path=stale,
        )

    message = str(excinfo.value)
    assert baseline.parameters_digest in message
    assert current in message
    assert str(DEFAULT_BASELINE_PATH) in message
    assert str(stale) in message


def test_promoting_a_value_from_assumed_to_measured_fails_the_check(
    tmp_path: Path,
) -> None:
    """値を1つも動かさず出所だけ昇格しても検出される（要件 4.5）。

    ⚠️ 識別子は**出所表を含む**（tasks.md「Implementation Notes」タスク 1.4(a)）。
    「まだ仮値だったときの記録」がそのまま残っている状態を、形状を再生成せずに
    可視化できるのはこの性質による。数値の差分が無いぶん目視では気付けない。
    """
    stale = _stale_by_provenance(tmp_path / "provenance")
    shipped_values = json.loads(DEFAULT_DIMENSIONS_PATH.read_text(encoding="utf-8"))
    stale_values = json.loads(stale.read_text(encoding="utf-8"))
    del shipped_values["provenance"], stale_values["provenance"]

    assert shipped_values == stale_values, "出所表以外を変えてしまっている"
    with pytest.raises(ConsistencyError):
        verify_baseline_digest(
            load_baseline(),
            parameters_digest(load_params(stale)),
            baseline_path=DEFAULT_BASELINE_PATH,
            dimensions_path=stale,
        )


def test_these_tests_leave_the_shipped_files_untouched(tmp_path: Path) -> None:
    """上記の改変が出荷ファイルへ及んでいない。

    ⚠️ `tmp_path` の写しを指し損ねると、出荷の `dimensions.json` を書き換えたまま
    テストが緑になり、被害は次のタスクで表面化する。バイト列で確かめる。
    """
    before_dimensions = DEFAULT_DIMENSIONS_PATH.read_bytes()
    before_baseline = DEFAULT_BASELINE_PATH.read_bytes()

    _stale_by_value(tmp_path / "value")
    _stale_by_provenance(tmp_path / "provenance")

    assert DEFAULT_DIMENSIONS_PATH.read_bytes() == before_dimensions
    assert DEFAULT_BASELINE_PATH.read_bytes() == before_baseline


# ---------------------------------------------------------------------------
# 2. 形状ライブラリ非導入の環境での実行（要件 5.7）
# ---------------------------------------------------------------------------


def test_the_stale_record_is_detected_in_a_process_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """遮断した**実プロセス**でも同じ不整合が検出される（要件 4.5, 5.7）。

    ⚠️ 検出の経路が `build123d` へ一切触れていないことまで見る——子プロセスの
    終了時に `sys.modules` へ `build123d` が現れていれば、遅延 import であっても
    「CAD 非導入環境で走る」とは言えない。
    """
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    stale = _stale_by_value(tmp_path / "value")

    code = textwrap.dedent(
        f"""
        from pathlib import Path

        from catch_mechanism.config import load_params, parameters_digest
        from catch_mechanism.errors import ConsistencyError
        from catch_mechanism.metrics import (
            DEFAULT_BASELINE_PATH,
            load_baseline,
            verify_baseline_digest,
        )

        dimensions = Path(sys.argv[1])
        try:
            verify_baseline_digest(
                load_baseline(),
                parameters_digest(load_params(dimensions)),
                baseline_path=DEFAULT_BASELINE_PATH,
                dimensions_path=dimensions,
            )
        except ConsistencyError as exc:
            if "build123d" in sys.modules:
                raise SystemExit("CAD-TOUCHED: 検出の経路が形状ライブラリを読み込んだ")
            print("{_BLOCKED_MARKER}", str(exc), sep="|")
        else:
            raise SystemExit("NOT-DETECTED: 古い記録が見過ごされた")
        """
    ).strip()
    completed = _run_blocked(code, stub_dir, str(stale))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _BLOCKED_MARKER in completed.stdout
    assert str(stale) in completed.stdout
    _assert_stub_blocks(stub_dir)


# ---------------------------------------------------------------------------
# 3. メタ検査: 検査そのものが CAD 依存として印を付けられていないこと（要件 5.7）
# ---------------------------------------------------------------------------


def test_the_shipped_digest_check_exists_under_the_expected_name() -> None:
    """見張る対象が在る（名前が変わったらここで気付く）。"""
    module = _regression_module()

    assert callable(getattr(module, DIGEST_TEST_NAME, None)), (
        f"{REGRESSION_PATH.name} に {DIGEST_TEST_NAME} が無い。"
        "改名したなら本ファイルの DIGEST_TEST_NAME も併せて直すこと"
        "（要件 5.7 の見張りを外したままにしない）。"
    )


def test_the_shipped_digest_check_is_not_marked_as_requiring_cad() -> None:
    """出荷記録の識別子照合に skip 系のマークが付いていない（要件 5.7）。

    ⚠️ **これはタスク 4.2 のレビューが見つけた穴を塞ぐメタ検査である。**
    当時、`@requires_cad` を付けても落ちるテストが1件も無かった
    （tasks.md「Implementation Notes」タスク 4.2(c)）。識別子の照合は
    `metrics.verify_baseline_digest` だけで完結し形状を要さないため、CAD 非導入
    環境で skip されてはならない——skip すれば、要件 4.5 が保証する「形状生成の
    環境が利用できない環境であっても検出する」が、その環境でだけ検証されなく
    なる。

    module 直下の `pytestmark` も併せて見る（module 全体へ skipif を付ける形の
    退行も同じ穴を開ける）。
    """
    module = _regression_module()
    target = getattr(module, DIGEST_TEST_NAME)
    offenders = sorted((_mark_names(target) | _mark_names(module)) & _SKIP_MARK_NAMES)

    assert not offenders, (
        f"{DIGEST_TEST_NAME} に {offenders!r} のマークが付いている。"
        "識別子の照合は形状を再生成しないため、CAD 非導入環境でも走らなければ"
        "ならない（要件 5.7 / tasks.md「Implementation Notes」タスク 4.2(c)）。"
    )


def test_the_shipped_digest_check_actually_runs_with_the_shape_library_blocked(
    tmp_path: Path,
) -> None:
    """遮断した実プロセスの pytest で、当該テストが skip されずに通る（要件 5.7）。

    ⚠️ マークを読むだけの検査は、module 直下の `pytest.importorskip("build123d")`
    のような別の形の退行を見落とす。ここでは**実際に走らせて**「1 passed」で
    あることを見る——`@requires_cad` を付ければ「1 skipped」に変わり、本検査が
    落ちる。

    ⚠️ 遮断の前後でスタブが効いていることを確かめる（tasks.md
    「Implementation Notes」タスク 4.2(f)）。
    """
    stub_dir = _nocad_stub(tmp_path)
    _assert_stub_blocks(stub_dir)
    node_id = f"{REGRESSION_PATH.relative_to(REPO_ROOT).as_posix()}::{DIGEST_TEST_NAME}"

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", node_id, "-q", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=_blocked_env(stub_dir),
        timeout=300.0,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "1 passed" in completed.stdout, completed.stdout
    assert "skipped" not in completed.stdout, (
        f"{node_id} が形状ライブラリ非導入の環境で skip された（要件 5.7 違反）。"
        f"\n{completed.stdout}"
    )
    _assert_stub_blocks(stub_dir)
