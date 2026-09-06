"""通しの一貫性検証（タスク 6.2 / 要件 2.7, 3.2, 5.7, 9.1, 9.2, 9.3, 9.5）。

本ファイルは**統合**の検査であり、個々の部品の契約は既存ファイルが持つ。
⚠️ **既に固定されている主張をここで繰り返さない。** 本ファイルが足すのは、
どのファイルも単独では言えない次の4つだけである。

1. **通しの実行**（要件 3.2, 2.7）: 生成 → 照合 → 許容誤差導出 → 整合検査を、
   **実プロセスの `python -m catch_mechanism`** として1本の鎖で流し、各段が
   前段の生成物を入力に取って**すべて正常終了する**こと。既存の
   `test_catch_cli.py::test_build_then_check_round_trip_with_the_shape_library` は
   in-process の `cli.main` で build → check の**2段**を見ており、
   `test_catch_trajectory_sim_sync.py` は `tolerance --check` を**単独**で見ている。
   鎖として繋がっていることは、どちらからも出てこない。
2. **両環境の区別**（要件 5.7）: **同一の引数列・同一の作業ディレクトリ**を、
   形状ライブラリの有無だけを変えて2回流し、終了コードの**列**が
   `(0, 0, 0, 0, …)` と `(3, 3, 0, 0, …)` に分かれること。⚠️ 既存テストは
   個々の終了コードを片方の環境で見ているだけで、「両環境の実行結果が終了コードで
   区別できる」という**関係**を誰も持っていない。
3. **上流依存の不在の実行時観測**（要件 9.3）: 鎖の全段を、`trajectory_sim` /
   `prediction_core` を **import 不能にした環境**で流す。⚠️ 静的な証明（AST 走査）は
   `test_catch_boundaries.py::test_no_upstream_package_import_in_current_tree` が
   持っており、そちらが要件 9.3 の**主たる担保**である。本ファイルはそれを
   繰り返さず、**実際に走る入口・設定読み込み経路が動的 import も含めて上流に
   触れない**ことを実行で観測する（静的走査は `src/catch_mechanism/*.py` しか
   見ておらず、`importlib` 経由の参照や `__main__` の経路を原理的に見ない）。
4. **判断の記録と数値の一致**（要件 9.1, 9.2, 9.3, 9.5）: design.md
   「受け口形状の決定」節が記録する決定1〜5と、出荷パラメータの値が一致すること。
   ⚠️ **値そのものの不変条件は本ファイルの主張ではない**——
   `added_depth_mm == 0` / `bottom_modification in ALLOWED_BOTTOM_MODIFICATIONS` / 締結座の数が
   **表現不可能な状態として**固定されていることは `test_catch_rim_invariants.py`
   （タスク 4.4）が持ち、型の側の拒否は `test_catch_params.py` が持つ。
   本ファイルが足すのは**記録と値の突き合わせ**であり、記録の側の数字を書き換えても
   値の側を書き換えても落ちる。

## ⚠️ 記録（散文）を検査することの限界

要件 9.1 / 9.2 / 9.5 は「記録すること」を求めており、記録は散文である。
tasks.md「Implementation Notes」タスク 2.1(e) が警告するとおり、docstring や
文書の**部分文字列照合は弱い検査**であり、逆の方針を書いた文書でも語が含まれて
いれば通りうる。そのため本ファイルは、散文の照合を**単独では使わず**、可能な
限り機械可読な相手と突き合わせる:

- 決定2・決定3・決定4 → **出荷 `dimensions.json` の値**（`load_params()`）
- 決定1の帰結（外向き部分を算入しない）→ **出荷 `catch-opening.json` の前提**
- 決定5・`### Out of Boundary` → **パラメータ表に材質の項目が無いこと**

## ⚠️ 出荷ファイルを書き換えない

`build` は `var/cad/` へ、`tolerance` は `--output` 省略時に出荷の
`catch-opening.json` へ書く（tasks.md「Implementation Notes」タスク 4.1(g)）。
本ファイルの全実行は `--output-dir` / `--output` / `--baseline` を `tmp_path` へ
向けており、`test_the_pipeline_leaves_the_shipped_files_untouched` が
`configs/` と `src/` の全ファイルのハッシュで前後一致を固定する。

ファイル名について: `tests/` に `__init__.py` が無くテストモジュール名が
セッション全体でフラットであるため、本ディレクトリの規約どおり `test_catch_`
接頭辞を付ける（tasks.md「Implementation Notes」タスク 1.1）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, NamedTuple

import pytest

from catch_mechanism.config import load_params
from catch_mechanism.metrics import DEFAULT_BASELINE_PATH
from catch_mechanism.params import ALLOWED_MATERIALS, PARAMETER_PATHS, ParameterPath
from catch_mechanism.tolerance import DEFAULT_DERIVATION_PATH, load_derivation

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DESIGN_PATH: Final[Path] = REPO_ROOT / ".kiro" / "specs" / "catch-mechanism" / "design.md"
SIM_CONFIGS_DIR: Final[Path] = REPO_ROOT / "configs" / "trajectory_sim"

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "要件 5.7 により、形状生成を除く検査はこの環境でも完了する。",
)


# ---------------------------------------------------------------------------
# 1. 通しの鎖の定義
# ---------------------------------------------------------------------------


class Step(NamedTuple):
    """鎖の1段。

    Attributes:
        label: 人が読む段の名前（tasks.md 6.2 の「生成 → 照合 → 許容誤差導出 →
            整合検査」に対応する）。
        argv: `python -m catch_mechanism` へ渡す引数列。
        needs_cad: 形状ライブラリを要する段か。⚠️ **この列が
            「両環境で終了コードが分かれる位置」の予言**であり、
            `test_the_two_environments_are_distinguishable_by_exit_code` が
            実測と突き合わせる。
    """

    label: str
    argv: tuple[str, ...]
    needs_cad: bool


def executable_simulator_configs() -> list[Path]:
    """`parameters` オブジェクトを持つ＝シミュレータへ渡せる設定を構造で拾う。

    ⚠️ **ファイル名を列挙しない**（`test_catch_trajectory_sim_sync.py` と同じ
    規律。列挙すると設定が増えたときに整合検査から静かに漏れる）。機体パラメータ
    （`drivetrain-*.json`）は `parameters` を持たないため、この判定で外れる。
    """
    found: list[Path] = []
    for path in sorted(SIM_CONFIGS_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and isinstance(document.get("parameters"), dict):
            found.append(path)
    return found


def pipeline_steps(workdir: Path) -> tuple[Step, ...]:
    """通しの鎖（生成 → 照合 → 許容誤差導出 → 整合検査）を組み立てる。

    ⚠️ **段が前段の生成物を入力に取ることが鎖の実体である**。`build
    --update-baseline` が書いた記録を次段の `check --baseline` が読み、
    `tolerance --output` が書いた記録の導出値を `--check` が設定と突き合わせる。

    ⚠️ **出荷ファイルを一切指さない。** 記録・導出記録・生成物の出力先はすべて
    `workdir` の下である（tasks.md「Implementation Notes」タスク 4.1(g)）。
    """
    baseline = workdir / "geometry-baseline.json"
    record = workdir / "catch-opening.json"
    steps = [
        Step(
            "生成",
            (
                "build",
                "--output-dir",
                str(workdir / "cad"),
                "--update-baseline",
                "--baseline",
                str(baseline),
            ),
            needs_cad=True,
        ),
        #  ⚠️ `--digest-only` を付けない `check` は形状を再生成するため CAD を要する。
        Step("照合", ("check", "--baseline", str(baseline)), needs_cad=True),
        Step(
            "照合（識別子のみ）",
            ("check", "--digest-only", "--baseline", str(baseline)),
            needs_cad=False,
        ),
        Step("許容誤差導出", ("tolerance", "--output", str(record)), needs_cad=False),
    ]
    steps += [
        Step(
            f"整合検査（{config.name}）",
            ("tolerance", "--output", str(record), "--check", str(config)),
            needs_cad=False,
        )
        for config in executable_simulator_configs()
    ]
    return tuple(steps)


# ---------------------------------------------------------------------------
# 2. 実行環境（スタブと作業ディレクトリ）
# ---------------------------------------------------------------------------


def _stub_dir(root: Path, name: str, modules: dict[str, str]) -> Path:
    """`import <module>` が `ImportError` になるスタブ置き場を作る。

    ⚠️ **`/tmp` へ置かない**（tasks.md「Implementation Notes」タスク 4.2(f)）。
    実装者が検証中に WSL の `/tmp` を失い、遮断したつもりの実行が遮断されて
    いなかった事故がある。`tmp_path` 配下に置き、遮断が効いていることを
    `test_the_upstream_stub_actually_blocks_the_upstream_packages` /
    `test_the_cad_stub_actually_blocks_the_shape_library` /
    `test_the_stub_is_still_active_after_the_blocked_pipeline` が別途観測する。

    ⚠️ **素の `raise ImportError(...)` を書いた `.py` を使う**
    （タスク 5.1(g)）。`ModuleNotFoundError` 方式は上流テストの主張と食い違う。
    """
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    for module, message in modules.items():
        (directory / f"{module}.py").write_text(
            f'raise ImportError("{message}")\n', encoding="utf-8"
        )
    return directory


#: 上流遮断スタブが投げる文言。⚠️ **これが観測の目印である**——鎖のどの段でも
#: この文言が出力に現れなければ、上流へ触れていない。
UPSTREAM_BLOCK_MARKER: Final[str] = "upstream blocked by the catch_mechanism end-to-end test"

#: スタブ置き場のディレクトリ名。⚠️ 「鎖を流した後もスタブが残っているか」を見る
#: テストが同じ場所を指す必要があるため、リテラルを1箇所に集める。
UPSTREAM_STUB_DIRNAME: Final[str] = "no-upstream"
CAD_STUB_DIRNAME: Final[str] = "no-cad"


def upstream_stub(root: Path) -> Path:
    """`trajectory_sim` / `prediction_core` を import 不能にする（要件 9.3）。"""
    return _stub_dir(
        root,
        UPSTREAM_STUB_DIRNAME,
        {"trajectory_sim": UPSTREAM_BLOCK_MARKER, "prediction_core": UPSTREAM_BLOCK_MARKER},
    )


def cad_stub(root: Path) -> Path:
    """`build123d` を import 不能にする（形状ライブラリ非導入環境の再現）。"""
    return _stub_dir(
        root,
        CAD_STUB_DIRNAME,
        {"build123d": "No module named 'build123d' (catch_mechanism test stub)"},
    )


def _run_module(
    argv: tuple[str, ...], *, stub_dirs: tuple[Path, ...], timeout: float = 300.0
) -> subprocess.CompletedProcess[str]:
    """`python -m catch_mechanism <argv>` を実プロセスとして起動する。

    ⚠️ 復号は `encoding="utf-8"` を明示する。`text=True` はロケール依存であり、
    lib3mf がプロセスのロケールを `C` へ落とす既知の副作用
    （tasks.md「Implementation Notes」タスク 3.4(a) / 4.1(f)）に巻き込まれうる。
    """
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    entries = [str(path) for path in stub_dirs]
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return subprocess.run(
        [sys.executable, "-m", "catch_mechanism", *argv],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )


def _import_probe(module: str, *, stub_dirs: tuple[Path, ...]) -> subprocess.CompletedProcess[str]:
    """スタブが効いているかを `import` 1回で観測する。"""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    entries = [str(path) for path in stub_dirs]
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=120.0,
        check=False,
    )


#: 出荷ファイルの監視対象。⚠️ CLI が書きうるのはこの2つの木だけである
#: （記録・導出記録は `configs/`、実装は `src/`）。
_SHIPPED_GLOBS: Final[tuple[tuple[str, str], ...]] = (("configs", "**/*.json"), ("src", "**/*.py"))


def _shipped_digest() -> dict[str, str]:
    """出荷ファイルの内容ハッシュ表（パス文字列 → sha256）。"""
    digests: dict[str, str] = {}
    for root, pattern in _SHIPPED_GLOBS:
        for path in sorted((REPO_ROOT / root).glob(pattern)):
            digests[str(path.relative_to(REPO_ROOT)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


class PipelineRun(NamedTuple):
    """1本の鎖の実行結果。"""

    steps: tuple[Step, ...]
    results: tuple[subprocess.CompletedProcess[str], ...]
    workdir: Path
    shipped_before: dict[str, str]
    shipped_after: dict[str, str]

    @property
    def exit_codes(self) -> tuple[int, ...]:
        return tuple(result.returncode for result in self.results)

    def report(self) -> str:
        """失敗時に読める形へ整形する（どの段で何が起きたか）。"""
        return "\n".join(
            f"[{step.label}] exit={result.returncode}\n"
            f"  argv={' '.join(step.argv)}\n"
            f"  stderr={result.stderr.strip()[:400]}"
            for step, result in zip(self.steps, self.results)
        )


def _prepare_workdir(root: Path, name: str) -> Path:
    """作業ディレクトリを作り、出荷の形状指標記録を写す。

    ⚠️ **写すのは、両環境で「同一の作業ディレクトリの中身」から始めるためである。**
    形状ライブラリ非導入の環境では `build` が失敗して記録が生まれないため、
    写しておかないと後段の `check` が「記録が無い」（終了コード 2）で落ち、
    観測したい「形状環境の不在」（終了コード 3）と混ざる。
    ⚠️ 出荷ファイルは**読むだけ**である。
    """
    workdir = root / name
    workdir.mkdir(parents=True)
    shutil.copyfile(DEFAULT_BASELINE_PATH, workdir / "geometry-baseline.json")
    return workdir


def _run_pipeline(root: Path, name: str, *, block_cad: bool) -> PipelineRun:
    """鎖を1本流す。⚠️ 上流パッケージは**常に**遮断する（要件 9.3）。"""
    stubs = [upstream_stub(root)]
    if block_cad:
        stubs.append(cad_stub(root))
    stub_dirs = tuple(stubs)

    workdir = _prepare_workdir(root, name)
    steps = pipeline_steps(workdir)

    before = _shipped_digest()
    results = tuple(_run_module(step.argv, stub_dirs=stub_dirs) for step in steps)
    after = _shipped_digest()
    return PipelineRun(steps, results, workdir, before, after)


@pytest.fixture(scope="module")
def blocked_run(tmp_path_factory: pytest.TempPathFactory) -> PipelineRun:
    """形状ライブラリを遮断した環境で鎖を流す（常に実行される）。"""
    return _run_pipeline(tmp_path_factory.mktemp("nocad"), "pipeline", block_cad=True)


@pytest.fixture(scope="module")
def cad_run(tmp_path_factory: pytest.TempPathFactory) -> PipelineRun:
    """形状ライブラリを導入した環境で鎖を流す（`@requires_cad` の系のみ）。

    ⚠️ **形状の生成と再生成を含むため実時間で数十秒かかる。** モジュール全体で
    1回だけ流し、複数のテストが同じ実行結果を読む。
    """
    return _run_pipeline(tmp_path_factory.mktemp("cad"), "pipeline", block_cad=False)


# ---------------------------------------------------------------------------
# 3. スタブが効いていることの実証（⚠️ これが無いと以下すべてが空検査になる）
# ---------------------------------------------------------------------------


def test_the_upstream_stub_actually_blocks_the_upstream_packages(tmp_path: Path) -> None:
    """遮断が効いていることを `import` で観測する（要件 9.3 の検査の前提）。

    ⚠️ **スタブが効いていなければ「上流を import しない」の観測は空になる。**
    tasks.md「Implementation Notes」タスク 4.2(f) の事故（遮断したつもりの実行が
    遮断されていなかった）と同じ形の失敗を防ぐ。
    """
    stubs = (upstream_stub(tmp_path),)
    for module in ("trajectory_sim", "prediction_core"):
        completed = _import_probe(module, stub_dirs=stubs)
        assert completed.returncode != 0, f"{module} が遮断されていない"
        assert "ImportError" in completed.stderr

    #  ⚠️ 遮断は上流だけに効き、自パッケージには効かない（過剰遮断でないこと）。
    completed = _import_probe("catch_mechanism", stub_dirs=stubs)
    assert completed.returncode == 0, completed.stderr


def test_the_cad_stub_actually_blocks_the_shape_library(tmp_path: Path) -> None:
    """形状ライブラリの遮断が効いていることを `import` で観測する（要件 5.7）。"""
    completed = _import_probe("build123d", stub_dirs=(cad_stub(tmp_path),))
    assert completed.returncode != 0, "build123d が遮断されていない"
    assert "ImportError" in completed.stderr


def test_the_stub_is_still_active_after_the_blocked_pipeline(blocked_run: PipelineRun) -> None:
    """鎖を流した**後**も遮断が生きている（タスク 4.2(f) の再発防止）。

    ⚠️ スタブ置き場が実行中に消えると、遮断したつもりの結果が導入環境の結果に
    なる。前後の両方で観測することが、その事故と「本当に遮断されていた」ことを
    区別する唯一の方法である。

    ⚠️ **ここでスタブを作り直さない。** 作り直すと「消えていても緑」になり、
    観測したい事故そのものを隠す。鎖が使ったディレクトリを**そのまま**指す。
    """
    root = blocked_run.workdir.parent
    cad_dir = root / CAD_STUB_DIRNAME
    upstream_dir = root / UPSTREAM_STUB_DIRNAME
    assert (cad_dir / "build123d.py").exists(), "形状ライブラリのスタブが消えている"
    assert (upstream_dir / "trajectory_sim.py").exists(), "上流のスタブが消えている"

    completed = _import_probe("build123d", stub_dirs=(cad_dir,))
    assert completed.returncode != 0, completed.stdout
    completed = _import_probe("trajectory_sim", stub_dirs=(upstream_dir,))
    assert completed.returncode != 0, completed.stdout


# ---------------------------------------------------------------------------
# 4. 通しの実行（要件 3.2 / タスク 6.2 の第1項）
# ---------------------------------------------------------------------------


@requires_cad
def test_the_whole_pipeline_succeeds_end_to_end(cad_run: PipelineRun) -> None:
    """生成 → 照合 → 許容誤差導出 → 整合検査が通しですべて正常終了する。

    ⚠️ **これがタスク 6.2 の第1項そのものである。** 各段は前段の生成物を入力に
    取る——`build --update-baseline` が書いた記録を `check` が読み、
    `tolerance --output` が書いた導出記録の値を `--check` が設定と突き合わせる。
    段ごとの契約は既存ファイルが持つが、**鎖として繋がること**はここでしか
    観測されない。
    """
    assert cad_run.exit_codes == tuple(0 for _ in cad_run.steps), cad_run.report()


@requires_cad
def test_the_pipeline_produces_the_artifacts_that_the_next_step_consumes(
    cad_run: PipelineRun,
) -> None:
    """鎖が実際に生成物を作り、後段がそれを読んでいる（要件 3.2, 3.3）。

    ⚠️ 終了コードだけでは「何もせずに 0 を返した」と区別できない。
    """
    produced = sorted(path.name for path in (cad_run.workdir / "cad").iterdir())
    assert produced, "生成物が出力先に無い"
    assert all(name.endswith((".step", ".stl", ".3mf")) for name in produced), produced

    baseline = cad_run.workdir / "geometry-baseline.json"
    assert baseline.exists(), "照合が読む記録が生成されていない"

    record = cad_run.workdir / "catch-opening.json"
    #  ⚠️ 整合検査が突き合わせた導出値は、前段が書いたこの記録と同じ導出である。
    assert load_derivation(record) == load_derivation(DEFAULT_DERIVATION_PATH)


@requires_cad
def test_the_pipeline_leaves_the_shipped_files_untouched(cad_run: PipelineRun) -> None:
    """鎖の実行が `configs/` と `src/` の1バイトも変えない（タスク 4.1(g)）。

    ⚠️ `build` は `var/cad/` へ、`tolerance` は `--output` 省略時に出荷の
    `catch-opening.json` へ書く。鎖が出荷ファイルを書き換えていれば、
    「通しで正常終了した」という観測そのものが信用できなくなる。
    """
    assert cad_run.shipped_after == cad_run.shipped_before


def test_the_blocked_pipeline_leaves_the_shipped_files_untouched(blocked_run: PipelineRun) -> None:
    """遮断環境の鎖も出荷ファイルを変えない（`tolerance` は CAD 無しで走る）。"""
    assert blocked_run.shipped_after == blocked_run.shipped_before


# ---------------------------------------------------------------------------
# 5. 両環境の区別（要件 5.7 / タスク 6.2 の観測可能な完了状態）
# ---------------------------------------------------------------------------


def test_every_step_that_needs_no_shape_library_succeeds_without_it(
    blocked_run: PipelineRun,
) -> None:
    """形状を要さない段は形状ライブラリ非導入の環境でも 0 で終わる（要件 5.7）。

    照合（識別子のみ）・許容誤差導出・整合検査がこれに当たる。
    """
    for step, result in zip(blocked_run.steps, blocked_run.results):
        if step.needs_cad:
            continue
        assert result.returncode == 0, f"{step.label}: {result.returncode}\n{result.stderr}"


def test_every_step_that_needs_the_shape_library_fails_with_its_own_exit_code(
    blocked_run: PipelineRun,
) -> None:
    """形状を要する段は**専用の終了コード 3** で失敗する（要件 5.3, 5.7）。

    ⚠️ **0（成功）でも 2（入力の誤り）でもない。** 生成したつもりで生成物が
    無い状態は造形の直前まで気付けない事故になり、入力は正しいのだから
    「使い方の誤り」でもない。
    """
    for step, result in zip(blocked_run.steps, blocked_run.results):
        if not step.needs_cad:
            continue
        assert result.returncode == 3, f"{step.label}: {result.returncode}\n{result.stderr}"


@requires_cad
def test_the_two_environments_are_distinguishable_by_exit_code(
    cad_run: PipelineRun, blocked_run: PipelineRun
) -> None:
    """⚠️ **同一の引数列**の実行結果が、終了コードの列で区別できる。

    タスク 6.2 の観測可能な完了状態の後半そのものである。
    ⚠️ **違いが出るのは形状を要する段だけ**でなければならない——形状ライブラリの
    不在が、形状を要さない検査（要件 5.7）まで巻き込んで壊していないこと。
    """
    cad_labels = [step.label for step in cad_run.steps]
    blocked_labels = [step.label for step in blocked_run.steps]
    assert cad_labels == blocked_labels, "両環境で鎖の構成が違う"

    assert cad_run.exit_codes != blocked_run.exit_codes, (
        "両環境の実行結果が終了コードで区別できない\n"
        f"cad={cad_run.exit_codes} blocked={blocked_run.exit_codes}"
    )

    differing = {
        step.label
        for step, cad_code, blocked_code in zip(
            cad_run.steps, cad_run.exit_codes, blocked_run.exit_codes
        )
        if cad_code != blocked_code
    }
    assert differing == {step.label for step in cad_run.steps if step.needs_cad}


# ---------------------------------------------------------------------------
# 6. 上流依存の不在（要件 9.3）
# ---------------------------------------------------------------------------


def test_the_pipeline_runs_with_the_upstream_packages_made_unimportable(
    blocked_run: PipelineRun,
) -> None:
    """鎖の全段が、上流を import 不能にした環境で走りきる（要件 9.3）。

    ⚠️ **要件 9.3 の主たる担保は静的走査である**——
    `test_catch_boundaries.py::test_no_upstream_package_import_in_current_tree` が
    `trajectory_sim` への辺が1本も無いことを AST で証明しており、それが
    「シミュレータの出力を保持の根拠として用いない」の機械的な証明である。
    本テストはそれを繰り返さず、**実際に走る入口が動的 import も含めて上流に
    触れない**ことを実行で観測する（静的走査は `src/catch_mechanism/*.py` しか
    見ず、`importlib` 経由の参照や設定読み込み経路の import を原理的に見ない）。

    ⚠️ 遮断が効いていることは
    `test_the_upstream_stub_actually_blocks_the_upstream_packages` が別途固定する。

    ⚠️ **観測するのはスタブの文言であって `"trajectory_sim"` の文字列ではない。**
    整合検査の段は `configs/trajectory_sim/*.json` を読むため、正常な出力にも
    パスの一部としてパッケージ名が現れる。文字列照合では「設定を読んだ」と
    「上流を import した」を区別できない。
    """
    for step, result in zip(blocked_run.steps, blocked_run.results):
        for stream_name, stream in (("stderr", result.stderr), ("stdout", result.stdout)):
            assert UPSTREAM_BLOCK_MARKER not in stream, (
                f"{step.label}: 上流パッケージを import しようとした（{stream_name}）\n{stream}"
            )
        expected = 3 if step.needs_cad else 0
        assert result.returncode == expected, (
            f"{step.label}: 上流を遮断した環境で終了コードが {result.returncode} "
            f"（期待 {expected}）\n{result.stderr}"
        )


@requires_cad
def test_the_shape_generating_pipeline_also_runs_without_the_upstream_packages(
    cad_run: PipelineRun,
) -> None:
    """形状を生成する鎖も上流を要さない（要件 9.3 / 5.4）。

    ⚠️ `cad_run` も上流を遮断した環境で流している。**形状生成・書き出しの経路まで
    含めて**上流へ触れないことを、スタブの文言が出力へ一度も現れないこととして
    観測する。通しの成功そのものは
    `test_the_whole_pipeline_succeeds_end_to_end` の主張であり、ここでは
    繰り返さない。
    """
    for step, result in zip(cad_run.steps, cad_run.results):
        for stream_name, stream in (("stderr", result.stderr), ("stdout", result.stdout)):
            assert UPSTREAM_BLOCK_MARKER not in stream, (
                f"{step.label}: 上流パッケージを import しようとした（{stream_name}）\n{stream}"
            )


# ---------------------------------------------------------------------------
# 7. 判断の記録（要件 9.1, 9.2, 9.3, 9.5）
# ---------------------------------------------------------------------------

DECISION_SECTION_HEADING: Final[str] = "## 受け口形状の決定"
OUT_OF_BOUNDARY_HEADING: Final[str] = "### Out of Boundary"


def _section(heading: str, level: int) -> str:
    """design.md の見出しから、次の同位以上の見出しの手前までを切り出す。

    ⚠️ 見出しが一意でなければ失敗させる。節が分裂・複製した状態で「どちらかに
    書いてあれば通る」検査になると、記録の所在が曖昧になる。
    """
    lines = DESIGN_PATH.read_text(encoding="utf-8").splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith(heading)]
    assert len(starts) == 1, f"design.md に {heading!r} の節が一意でない: {starts}"
    start = starts[0]
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if not line.startswith("#"):
            continue
        depth = len(line) - len(line.lstrip("#"))
        if depth <= level:
            return "\n".join(lines[start:index])
    return "\n".join(lines[start:])


def _decisions() -> dict[int, str]:
    """「受け口形状の決定」節を決定番号 → 本文（見出しを含む）へ分解する。"""
    section = _section(DECISION_SECTION_HEADING, level=2)
    parts = re.split(r"^### 決定 (\d+):", section, flags=re.MULTILINE)
    #  parts[0] は節の前置き。以降は (番号, 本文) の繰り返し。
    return {
        int(parts[index]): parts[index] + ":" + parts[index + 1]
        for index in range(1, len(parts), 2)
    }


def _decision_rows(body: str) -> dict[str, str]:
    """決定の表（`| **決定** | … |`）を行名 → 内容へ分解する。"""
    rows: dict[str, str] = {}
    for match in re.finditer(r"^\|\s*\*\*(.+?)\*\*\s*\|(.*)\|\s*$", body, flags=re.MULTILINE):
        rows[match.group(1).strip()] = match.group(2).strip()
    return rows


def test_the_decision_record_exists_and_covers_depth_bottom_and_taper() -> None:
    """深さ・底の扱い・テーパーの方針が記録されている（要件 9.1）。

    ⚠️ 記録の**所在**は design.md「受け口形状の決定」節であり、design.md の
    「Boundary Context」がそれを「記録の正」と名指ししている。
    """
    decisions = _decisions()
    assert set(decisions) == {1, 2, 3, 4, 5}, f"決定の番号が {sorted(decisions)} である"

    body = "\n".join(decisions.values())
    for topic in ("深さ", "底", "テーパー"):
        assert topic in body, f"判断の記録に「{topic}」についての方針が無い（要件 9.1）"


def test_every_tabulated_decision_states_its_grounds() -> None:
    """各決定が「決定」「根拠」「帰結」を伴う（要件 9.1「判断の根拠とともに」）。

    ⚠️ 決定5（緩衝ライナー）は表ではなく散文であり、別のテストが扱う。
    """
    for number, body in _decisions().items():
        if number == 5:
            continue
        rows = _decision_rows(body)
        assert {"決定", "根拠", "帰結"} <= set(rows), f"決定 {number} の表に欠けがある: {sorted(rows)}"
        assert len(rows["根拠"]) > 40, f"決定 {number} の根拠が実質的でない: {rows['根拠']!r}"


def test_the_recorded_numbers_agree_with_the_shipped_parameters() -> None:
    """⚠️ 記録された保持方針の**数値的帰結**が出荷パラメータと一致する。

    タスク 6.2 の第2項である。記録（散文）と単一の正（`dimensions.json`）が
    食い違えば、どちらを読んだ人も違う設計を信じることになる。

    ⚠️ **値の不変条件そのものは本テストの主張ではない**——
    「追加の深さを持たない」「底に加工を行わない」が**表現不可能な状態として**
    固定されていることは `test_catch_rim_invariants.py`（タスク 4.4）が、型の側の
    拒否は `test_catch_params.py` が持つ。ここが足すのは**記録と値の突き合わせ**で
    あり、記録の数字を書き換えても値を書き換えても落ちる。
    """
    decisions = _decisions()
    retention = load_params().retention

    depth = re.search(r"added_depth_mm\s*=\s*(-?[0-9.]+)", decisions[2])
    assert depth is not None, "決定2 が `added_depth_mm` の決定値を書いていない"
    assert float(depth.group(1)) == retention.added_depth_mm

    # ⚠️ 文字クラスに `_` を含める。`[a-z]+` では `"bottom_removed"` に**一致せず**、
    # 記録と値の突き合わせが「決定値が書かれていない」として落ちる——記録が正しく
    # ても落ちる形であり、正規表現側の欠陥である。
    bottom = re.search(r'bottom_modification\s*=\s*"([a-z_]+)"', decisions[3])
    assert bottom is not None, "決定3 が `bottom_modification` の決定値を書いていない"
    assert bottom.group(1) == retention.bottom_modification

    seats = re.search(r"締結座を(\d+)箇所", decisions[4])
    assert seats is not None, "決定4 が後付け締結座の数を書いていない"
    assert int(seats.group(1)) == retention.retrofit_fastener_count

    #  帰結の側が、値を持つパラメータの名前を挙げていること（記録と表の接続）。
    assert "retrofit_fastener_count" in decisions[4]
    assert "liner_flat_min_diameter_mm" in decisions[3]


def test_the_record_states_the_opposing_effect_on_catching_and_holding() -> None:
    """同一形状が取りこぼし防止と保持へ**逆向きに**効く関係が明示されている（要件 9.2）。

    ⚠️ 散文の照合は弱い（tasks.md「Implementation Notes」タスク 2.1(e)）。単独では
    使わず、**決定1の帰結が機械可読な相手と一致すること**を併せて固定する——
    「外向きに張り出す部分を許容誤差の導出へ算入しない」という帰結は、出荷の
    導出記録 `catch-opening.json` の前提としても記録されている。記録が2箇所に
    あり、両者が同じことを言っていることが、散文だけの主張より強い。
    """
    decision = _decisions()[1]
    rows = _decision_rows(decision)
    grounds = rows["根拠"]
    #  ⚠️ 「逆向き」の語だけを見ない。同じ行に「逆向き」は2度現れるため、1度消しても
    #  素通りする（実測済み）。**どちらへ有利でどちらへ不利か**まで書かれていること。
    assert re.search(r"FR-7 に有利", grounds), grounds
    assert re.search(r"FR-12 に不利", grounds), grounds
    assert "逆向き" in grounds, grounds

    #  帰結: 外向き部分を算入しない（要件 7.2）。同じ前提が導出記録にもある。
    assert "算入しない" in rows["帰結"]
    derivation = load_derivation(DEFAULT_DERIVATION_PATH)
    assert any("外向きに張り出す部分" in assumption for assumption in derivation.assumptions), (
        "導出記録の前提に外向き部分の非算入が無い（記録が1箇所しかない）"
    )


def test_the_record_states_that_bounce_out_is_outside_the_model() -> None:
    """跳ね返りが評価対象外である旨が判断の記録に明示されている（要件 9.3 前半）。

    ⚠️ 要件 9.3 は2つの半分を持つ。**記録への明示**（本テスト）と、
    **シミュレータの出力を根拠に用いないこと**（`test_catch_boundaries.py` の
    静的走査が主たる担保、本ファイルの実行時観測が補強）である。
    """
    section = _section(DECISION_SECTION_HEADING, level=2)
    assert "bounce_out" in section
    assert "モデル外" in section
    assert "D-9" in section
    #  ⚠️ 「シミュレータからは出せない」ことが、机上で決めた理由として書かれている。
    assert "シミュレータからは出せず" in section or "シミュレータからは出せない" in section


def test_the_record_marks_the_judgements_as_not_pass_fail_criteria() -> None:
    """未実測の推定が合否条件と区別されている（要件 9.6 の記録側・要件 9.1 の一部）。"""
    section = _section(DECISION_SECTION_HEADING, level=2)
    assert "合否条件ではない" in section


# ---------------------------------------------------------------------------
# 8. 緩衝材の材質を決着させていないこと（要件 9.5）
# ---------------------------------------------------------------------------

#: 緩衝材（ライナー）を指す語。⚠️ 語**単位**で照合する（`test_catch_downstream_
#: contract.py` の語彙検出器と同じ規律。部分文字列照合は `liner` を含む無関係な
#: 名前に当たる）。
LINER_WORDS: Final[frozenset[str]] = frozenset(
    {"liner", "cushion", "damper", "padding", "bumper", "gasket"}
)

#: 材質の選定に踏み込む語。⚠️ **「後から貼れる平面」は幾何であって材質ではない**。
#: 平面径・面積・個数は担保してよく、硬さ・材質名・接着方式・厚みは踏み込みである。
MATERIAL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "material",
        "hardness",
        "shore",
        "durometer",
        "adhesive",
        "foam",
        "rubber",
        "silicone",
        "felt",
        "density",
        "grade",
        "thickness",
        "sheet",
        "supplier",
    }
)


def _words(path: ParameterPath) -> frozenset[str]:
    """パス文字列を語へ分解する（`.` と `_` で区切り、小文字化する）。"""
    return frozenset(word for word in re.split(r"[._]", path.path.lower()) if word)


def find_liner_material_commitments(paths: dict[str, ParameterPath]) -> list[str]:
    """緩衝材の**材質**へ踏み込んだパラメータを検出する（要件 9.5）。

    判定: 語に緩衝材を指す語を含み、かつ (a) 材質を指す語を含む、または
    (b) 値が文字列である（材質名・銘柄・供給元の選定はいずれも文字列になる）。

    ⚠️ **「後から貼れる平面」は検出しない。** `liner_flat_min_diameter_mm` は
    緩衝材を指す語を含むが、値は長さであり材質語を持たない——これは
    design.md「受け口形状の決定」決定3の帰結（平面を残す）であって、
    材質の決着ではない。
    """
    violations: list[str] = []
    for key, path in sorted(paths.items()):
        words = _words(path)
        if not (words & LINER_WORDS):
            continue
        if (words & MATERIAL_WORDS) or path.value_type is str:
            violations.append(key)
    return violations


def test_no_shipped_parameter_commits_to_a_liner_material() -> None:
    """パラメータ表に緩衝材の材質の項目が無い（要件 9.5）。

    ⚠️ **不在の主張である。** 不在は「書かなかった」だけでも成立してしまうため、
    検出器が働くことを `test_the_detector_flags_crafted_liner_material_parameters`
    が架空のパラメータで実証し、働きすぎないことを
    `test_the_detector_does_not_flag_geometric_liner_parameters` が実証する。
    """
    assert find_liner_material_commitments(dict(PARAMETER_PATHS)) == []


@pytest.mark.parametrize(
    ("field_name", "unit", "value_type"),
    [
        ("liner_material", "", str),
        ("liner_supplier", "", str),
        ("liner_shore_hardness", "", float),
        ("liner_thickness_mm", "mm", float),
        ("cushion_adhesive", "", str),
        ("damper_foam_grade", "", str),
    ],
)
def test_the_detector_flags_crafted_liner_material_parameters(
    field_name: str, unit: str, value_type: type
) -> None:
    """違反ケース: 緩衝材の材質へ踏み込む架空のパラメータ（検出器が空でないこと）。"""
    crafted = {
        f"retention.{field_name}": ParameterPath(
            path=f"retention.{field_name}",
            unit=unit,
            component="retention",
            field_name=field_name,
            value_type=value_type,
        )
    }
    assert find_liner_material_commitments(crafted) != []


@pytest.mark.parametrize(
    ("field_name", "unit", "value_type"),
    [
        ("liner_flat_min_diameter_mm", "mm", float),
        ("liner_flat_min_area_mm2", "mm^2", float),
        ("retrofit_fastener_count", "", int),
        ("bottom_thickness_mm", "mm", float),
        ("material", "", str),
    ],
)
def test_the_detector_does_not_flag_geometric_or_printing_parameters(
    field_name: str, unit: str, value_type: type
) -> None:
    """誤検知回避: 平面・個数・造形材料は緩衝材の材質決定ではない。

    ⚠️ 最後の `material`（造形フィラメント）が検出されないことが重要である——
    本 Spec が決着させている材質は**造形材料**であって緩衝材ではない。
    """
    crafted = {
        f"retention.{field_name}": ParameterPath(
            path=f"retention.{field_name}",
            unit=unit,
            component="retention",
            field_name=field_name,
            value_type=value_type,
        )
    }
    assert find_liner_material_commitments(crafted) == []


def test_the_only_liner_commitment_is_a_flat_area_and_retrofit_seats() -> None:
    """緩衝材について担保しているのが「平面」と「締結座」だけである（要件 9.5, 9.4, 9.7）。

    ⚠️ 「材質を決めていない」の裏返しとして、**何を決めているのか**を固定する。
    決めているのは (a) 底に残す平面の最小径、(b) 後付け締結座の数——どちらも
    幾何であり、後から材質を選べる余地をそのまま残している。
    """
    liner_paths = {
        key for key, path in PARAMETER_PATHS.items() if _words(path) & LINER_WORDS
    }
    assert liner_paths == {"retention.liner_flat_min_diameter_mm"}, liner_paths

    retention = load_params().retention
    assert retention.liner_flat_min_diameter_mm > 0.0
    assert retention.retrofit_fastener_count >= 1
    assert PARAMETER_PATHS["retention.liner_flat_min_diameter_mm"].value_type is float
    assert PARAMETER_PATHS["retention.retrofit_fastener_count"].value_type is int


def test_the_only_material_vocabulary_is_the_printing_filament() -> None:
    """材質の許可一覧が**造形材料**のものである（要件 2.5 との切り分け）。

    ⚠️ `ALLOWED_MATERIALS` が緩衝材へも効いていると読めてしまうと、
    「材質は決着済み」という誤読が生まれる。許可一覧が縛るのは
    `printing.material` の1項目だけである。
    """
    material_paths = {
        key
        for key, path in PARAMETER_PATHS.items()
        if path.value_type is str and "material" in _words(path)
    }
    assert material_paths == {"printing.material"}
    assert load_params().printing.material in ALLOWED_MATERIALS


def test_the_record_defers_the_liner_material_out_of_this_spec() -> None:
    """緩衝材の材質選定を本 Spec の決着対象から除外する旨が記録されている（要件 9.5）。

    ⚠️ 2箇所で記録されている: 判断の記録（決定5）と、境界の宣言
    （`### Out of Boundary`）である。片方だけだと「決定の場では触れたが境界には
    書いていない」あるいはその逆になり、下流が担当範囲を読み違える。
    """
    decision = _decisions()[5]
    assert "材質選定" in decision
    assert "M3" in decision, "先送り先（M3）が書かれていない"
    assert "決着させようとしない" in decision

    boundary = _section(OUT_OF_BOUNDARY_HEADING, level=3)
    liner_lines = [
        line for line in boundary.splitlines() if line.startswith("-") and "緩衝ライナー" in line
    ]
    assert len(liner_lines) == 1, f"境界の宣言に緩衝ライナーの行が一意でない: {liner_lines}"
    assert "材質決定" in liner_lines[0]
    assert "後から貼れる平面と締結箇所を残す" in liner_lines[0]
