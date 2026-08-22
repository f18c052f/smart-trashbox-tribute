"""公開 API の確定を検証する（要件 11.1、design.md「PublicApi」）。

`trajectory_sim/__init__.py` は再エクスポートのみを行い、ロジックを一切
持たない契約である（design.md「PublicApi / Responsibilities &
Constraints」: 「再エクスポートのみ。ロジックを持たない」）。本テストは
以下を固定する。

- `trajectory_sim.__all__` が本タスクで確定した33個の公開シンボル名と
  **完全一致**すること（多すぎても少なすぎても失敗）
- `__all__` に列挙された各名前が実際に `trajectory_sim` から
  import/`getattr` できること
- `prediction_core` の公開シンボル（18個、`prediction_core.__all__` から
  直接読み取る）が1つも `trajectory_sim.__all__` に紛れ込んでいないこと
  （design.md: 「`prediction_core` のシンボルを再エクスポートしない」）
- `dir(trajectory_sim)` から dunder と内部サブモジュール名を除いたものが
  `__all__` の集合と一致すること（意図しない漏れ export が無いこと）
- `__init__.py` 自身のソースが import 文・`__all__` 代入・docstring
  のみで構成され、関数/クラス定義や制御構文を一切持たないこと
- **完了条件そのもの**: `trajectory_sim.*`（トップレベルの属性経由）
  だけを使って、到達可否掃引（`SweepKind.REACHABILITY`）と全経路掃引
  （`SweepKind.THROW`）の両方を実行し、`write_sweep_result` による
  書き出しまで完走できること（サブモジュールの dotted-path アクセスを
  一切使わない）

本テストファイルは `prediction_core` を直接 import する（`ScenarioParams.
prediction` に渡す `PredictionConfig` を構築するため、および上記の
シンボル非漏洩チェックのため）。これは、design.md が意図する「上流に
依存する呼び出し側は `prediction_core` を自分で import する」という
方針そのものを体現しており、`trajectory_sim` パッケージ本体（プロダクション
コード）が `prediction_core` を re-export しないこととは矛盾しない。

ファイル名は `test_trajectory_sim_public_api.py`
（`tests/prediction_core/test_public_api.py` と同名になり pytest の
prepend インポートモードで `import file mismatch` を起こすため、
`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲して
回避する。tasks.md Implementation Notes 参照）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import prediction_core

import trajectory_sim

# 本タスクで確定した33個の公開シンボル名（design.md「PublicApi」に列挙
# された7カテゴリ――パラメータ型・掃引型・結果型・掃引実行・出力書き出し・
# 評価関数・例外階層――を、各モジュールの実際の公開シンボルへ具体化した
# ものである）。
EXPECTED_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # params.py（パラメータ型）
        "CalibrationStage",
        "Provenance",
        "CatchPolicy",
        "ThrowParams",
        "ThrowDispersion",
        "ObservationParams",
        "DrivetrainParams",
        "CatchCriteria",
        "LayoutParams",
        "ScenarioParams",
        "PARAMETER_PATHS",
        "ParameterPath",
        "replace_by_path",
        # results.py（結果型）
        "NotEvaluatedReason",
        "CellStatus",
        "MODEL_EXCLUSIONS",
        "ScenarioOutcome",
        "CellResult",
        "SweepResult",
        # sweep.py（掃引型・掃引実行）
        "SweepKind",
        "AxisSpec",
        "SweepSpec",
        "derive_seed",
        "run_sweep",
        # serialize.py（出力書き出し・出力形式の版）
        "OUTPUT_SCHEMA_VERSION",
        "sweep_result_to_dict",
        "write_sweep_result",
        # evaluate.py（評価関数）
        "evaluate_throw",
        "evaluate_reachability",
        # errors.py（例外階層）
        "TrajectorySimError",
        "ParameterError",
        "SweepDefinitionError",
        "OutputError",
    }
)

# `import trajectory_sim` だけで実際に属性として現れる内部サブモジュール名
# （`__init__.py` が直接 import するもの: errors, evaluate, params, results,
# serialize, sweep。加えて `evaluate` の import 連鎖で推移的に読み込まれる
# もの: drivetrain, observation, physics, prediction_link, units）。
#
# `cli` / `__main__` は `__init__.py` 自身の import 連鎖には含まれないが、
# 同一プロセス内で他のテストモジュール（`test_trajectory_sim_cli.py` 等）が
# `from trajectory_sim import cli` を実行すると、Python の import 機構により
# `trajectory_sim` パッケージオブジェクトへ属性として恒久的に束縛される
# （プロセス寿命の間、以後のどのテストの `dir(trajectory_sim)` にも現れる）。
# これはテスト実行順序に依存する環境の副作用であり、`__init__.py` 自身が
# 再エクスポートしたことを意味しない（design.md「PublicApi」が `cli` を
# 「エントリポイントスクリプトであり、ライブラリ API ではない」と明示的に
# 除外している対象そのもの）ため、ここで安全側に倒して除外リストへ含める。
INTERNAL_SUBMODULE_NAMES: frozenset[str] = frozenset(
    {
        "errors",
        "evaluate",
        "params",
        "results",
        "serialize",
        "sweep",
        "drivetrain",
        "observation",
        "physics",
        "prediction_link",
        "units",
        "cli",
        # `from __future__ import annotations` により `dir()` に現れる
        # 言語機能オブジェクト。公開シンボルではない。
        "annotations",
    }
)


def test_all_has_exactly_33_symbols() -> None:
    """`__all__` の要素数が33個ちょうどである。"""
    assert len(trajectory_sim.__all__) == 33


def test_all_matches_expected_public_symbols_exactly() -> None:
    """`__all__` が確定した33シンボルと完全一致する（多くも少なくもない）。"""
    assert set(trajectory_sim.__all__) == EXPECTED_PUBLIC_SYMBOLS


def test_all_has_no_duplicate_entries() -> None:
    """`__all__` に重複が無い（完全一致だけでは重複+別の欠落の相殺を見逃すため）。"""
    assert len(trajectory_sim.__all__) == len(set(trajectory_sim.__all__))


def test_every_symbol_in_all_is_accessible_via_getattr() -> None:
    """`__all__` に列挙された各名前が `trajectory_sim` から実際に取得できる。"""
    for name in trajectory_sim.__all__:
        assert hasattr(trajectory_sim, name), f"{name} が trajectory_sim に存在しない"


def test_every_symbol_in_all_is_importable_via_from_import() -> None:
    """`from trajectory_sim import <name>` の形で全シンボルが import できる。"""
    import importlib

    module = importlib.import_module("trajectory_sim")
    for name in trajectory_sim.__all__:
        # `getattr` が例外を送出しないこと自体が「import できる」ことの確認である。
        getattr(module, name)


def test_no_prediction_core_symbols_leak_into_public_api() -> None:
    """`prediction_core` の公開シンボルが1つも `trajectory_sim.__all__` に含まれない。

    design.md「PublicApi / Responsibilities & Constraints」: 「利用側は
    上流を直接 import する（依存関係を隠さない）」。`prediction_core.__all__`
    をこのテストファイル内でだけ読み取り、否定チェックに使う
    （プロダクションコードが `prediction_core` を re-export することとは
    無関係な、テスト専用の検証目的の import である）。
    """
    upstream_symbols = set(prediction_core.__all__)
    assert upstream_symbols, "prediction_core.__all__ が空である（前提が崩れている）"
    leaked = upstream_symbols & set(trajectory_sim.__all__)
    assert leaked == set(), f"prediction_core の公開シンボルが漏洩している: {leaked!r}"


def test_namespace_has_no_unintended_public_leaks() -> None:
    """`dir(trajectory_sim)` のうち非 dunder・非内部モジュール名が `__all__` と一致する。

    ここに `__all__` に無い名前が余分に現れる場合、意図せず内部実装を
    re-export してしまっている（漏れ export）ことを意味する。
    """
    visible_names = {
        name
        for name in dir(trajectory_sim)
        if not name.startswith("_") and name not in INTERNAL_SUBMODULE_NAMES
    }
    assert visible_names == EXPECTED_PUBLIC_SYMBOLS


def test_init_module_source_contains_no_logic() -> None:
    """`__init__.py` が import 文と `__all__` 代入とモジュール docstring のみで構成される。

    再エクスポート専用という設計契約（design.md「再エクスポートのみ。
    ロジックを持たない」）を、ソース構造そのものから機械的に確認する。
    関数定義・クラス定義・if/for等の制御構文が無いことを AST で検査する
    （`tests/prediction_core/test_public_api.py::
    test_init_module_source_contains_no_logic` と同じ技法）。
    """
    init_path = Path(trajectory_sim.__file__).resolve()
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    allowed_node_types = (
        ast.Import,
        ast.ImportFrom,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,  # モジュール docstring
    )
    for node in tree.body:
        assert isinstance(node, allowed_node_types), (
            f"__init__.py に許可されないトップレベル構文が存在する: {ast.dump(node)}"
        )
        if isinstance(node, ast.Expr):
            # docstring（文字列定数）以外の式文は許可しない。
            assert isinstance(node.value, ast.Constant), (
                "__init__.py の式文は docstring のみ許可される"
            )


# ---------------------------------------------------------------------------
# 完了条件本体: 入口モジュール経由だけで到達可否掃引・全経路掃引を実行できる
# ---------------------------------------------------------------------------


def _build_scenario_params() -> "trajectory_sim.ScenarioParams":
    """`trajectory_sim.*`（トップレベル属性）のみを使って最小構成の
    `ScenarioParams` を構築する。`prediction_core.PredictionConfig` だけは
    上流型であり `trajectory_sim` から再エクスポートされないため、本関数は
    `prediction_core` を直接 import する（design.md が意図する通り）。
    """
    throw = trajectory_sim.ThrowParams(
        release_x_mm=0.0,
        release_y_mm=0.0,
        release_z_mm=2000.0,
        speed_mm_s=4000.0,
        elevation_deg=45.0,
        azimuth_deg=0.0,
    )
    dispersion = trajectory_sim.ThrowDispersion()
    observation_params = trajectory_sim.ObservationParams(
        detection_start_delay_ms=20.0,
        sample_period_ms=20.0,
        sample_latency_ms=5.0,
        prediction_latency_ms=3.0,
    )
    drivetrain_params = trajectory_sim.DrivetrainParams(
        max_speed_mm_s=6000.0,
        max_accel_mm_s2=30000.0,
        max_decel_mm_s2=30000.0,
        control_period_ms=1.0,
        command_latency_ms=1.0,
        integration_step_ms=1.0,
    )
    catch = trajectory_sim.CatchCriteria()
    layout = trajectory_sim.LayoutParams(home_x_mm=0.0, home_y_mm=0.0)
    prediction_config = prediction_core.PredictionConfig()

    return trajectory_sim.ScenarioParams(
        throw=throw,
        dispersion=dispersion,
        observation=observation_params,
        drivetrain=drivetrain_params,
        catch=catch,
        layout=layout,
        prediction=prediction_config,
    )


def test_reachability_sweep_runnable_via_public_api_only() -> None:
    """到達可否掃引を `trajectory_sim.*` のみで構築・実行できる（完了条件）。

    `trajectory_sim.sweep.*` のようなサブモジュールの dotted-path
    アクセスは一切使わない。
    """
    params = _build_scenario_params()
    spec = trajectory_sim.SweepSpec(
        kind=trajectory_sim.SweepKind.REACHABILITY,
        axes=(
            trajectory_sim.AxisSpec(
                name="hold_time_ms", unit="ms", values=(200.0, 400.0)
            ),
            trajectory_sim.AxisSpec(
                name="required_distance_mm", unit="mm", values=(100.0, 500.0)
            ),
        ),
        trials_per_cell=1,
        seed=0,
    )

    result = trajectory_sim.run_sweep(spec, params)

    assert isinstance(result, trajectory_sim.SweepResult)
    assert len(result.cells) == 4
    for cell in result.cells:
        assert isinstance(cell, trajectory_sim.CellResult)
        assert cell.status in (
            trajectory_sim.CellStatus.CATCHABLE,
            trajectory_sim.CellStatus.NOT_CATCHABLE,
            trajectory_sim.CellStatus.NOT_EVALUATED,
        )
        assert cell.trials == 1


def test_throw_sweep_runnable_via_public_api_only_and_writes_output(tmp_path: Path) -> None:
    """全経路掃引を `trajectory_sim.*` のみで構築・実行し、書き出しまで
    完走できる（完了条件、`write_sweep_result` を含む）。

    軸名には `trajectory_sim.PARAMETER_PATHS` のキーを使う。
    """
    params = _build_scenario_params()
    assert "throw.speed_mm_s" in trajectory_sim.PARAMETER_PATHS

    spec = trajectory_sim.SweepSpec(
        kind=trajectory_sim.SweepKind.THROW,
        axes=(
            trajectory_sim.AxisSpec(
                name="throw.speed_mm_s", unit="mm/s", values=(3500.0, 4500.0)
            ),
        ),
        trials_per_cell=1,
        seed=0,
    )

    result = trajectory_sim.run_sweep(spec, params)

    assert isinstance(result, trajectory_sim.SweepResult)
    assert len(result.cells) == 2

    output_path = tmp_path / "result.json"
    trajectory_sim.write_sweep_result(result, output_path)

    assert output_path.exists()
    written_text = output_path.read_text(encoding="utf-8")
    assert '"output_schema_version"' in written_text
    assert trajectory_sim.OUTPUT_SCHEMA_VERSION in written_text

    # `sweep_result_to_dict` も同じ入口から使えることを確認する。
    result_dict = trajectory_sim.sweep_result_to_dict(result)
    assert result_dict["output_schema_version"] == trajectory_sim.OUTPUT_SCHEMA_VERSION
