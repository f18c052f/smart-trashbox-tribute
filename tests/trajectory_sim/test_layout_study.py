"""レイアウト検討とホイール径比較の E2E 検証（tasks.md タスク5.3）。

`configs/trajectory_sim/sweep-layout.json` を `trajectory_sim.cli.main()` の
実経路（タスク4.3が固定した経路）で実行し、以下を確認する:

- 候補ごとの必要移動量（`metrics["required_distance_mm"]`）と成立性
  （`status`）が格子として得られること（要件10.3）
- 成立する候補と成立しない候補の**境界**が読み取れる形で結果に現れること、
  すなわち格子内のどこかに、隣接する格子点間で `status` が
  `catchable` / `not_catchable` に切り替わる箇所が実在すること（要件10.4）
- 60mm と 48mm のホイール径で同一の掃引を実行すると結果が異なること
  （要件4.8。`test_trajectory_sim_configs.py` タスク4.4がこれを
  `sweep-reachability.json` で検証済みだが、本ファイルは
  `sweep-layout.json` 自身の「レイアウト検討」というストーリーの一部として
  独立に再確認する）
- 停止方針（`stop_and_wait`）と通過方針（`pass_through`）の結果が同一の
  掃引から取り出せること（要件5.6）

`configs/trajectory_sim/sweep-layout.json` の `layout.home_x_mm` 軸は、
タスク5.3の調査により、当初の委譲値（`-300.0, 0.0, 300.0`、投擲の原点
付近）では真の落下地点から常に遠すぎ、いずれのホイール径でも全格子点が
`not_catchable` に固定される（境界が存在しない）ことが判明したため、
落下地点付近まで候補を伸ばす形に更新した（`1200.0, 1800.0, 2400.0,
3000.0`）。この変更は軸の値のみであり、掃引の種別・試行回数・乱数種・
閾値・`catch.policy` / `throw.azimuth_deg` 軸には手を加えていない。
`test_trajectory_sim_configs.py`（タスク4.4）の全テストはこの変更後も
すべて成立する。
"""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

from trajectory_sim import cli

CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs" / "trajectory_sim"

SWEEP_LAYOUT = CONFIGS_DIR / "sweep-layout.json"
DRIVETRAIN_WHEEL60 = CONFIGS_DIR / "drivetrain-wheel60.json"
DRIVETRAIN_WHEEL48 = CONFIGS_DIR / "drivetrain-wheel48.json"


def _run_layout_sweep(drivetrain_path: Path, output_path: Path) -> dict[str, object]:
    """`sweep-layout.json` を `drivetrain_path` で `cli.main()` の実経路から実行する。"""
    exit_code = cli.main(
        [
            "--config",
            str(SWEEP_LAYOUT),
            "--drivetrain",
            str(drivetrain_path),
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0, f"sweep-layout.json x {drivetrain_path.name} の実行が失敗した"
    assert output_path.exists(), "出力ファイルが生成されていない"
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert isinstance(result["cells"], list)
    assert len(result["cells"]) > 0
    return result


def _find_status_transitions(
    result: dict[str, object],
) -> list[tuple[str, tuple[object, ...], str, tuple[object, ...], str]]:
    """格子内で `catchable`/`not_catchable` が切り替わる隣接格子点対を全て探す。

    「隣接」とは、ちょうど1つの軸の値だけが異なり（他の全軸は固定）、
    かつその軸自身の `values` の並び順で連続する値の対であることを指す
    （要件10.4「境界が読み取れる形」の定義そのもの）。1つの事前計算された
    ペアをハードコードするのではなく、任意の軸・任意の他軸の組み合わせに
    ついて一般的に走査することで、「境界が読み取れる」という性質そのものを
    検査する。
    """
    axes = result["sweep"]["axes"]
    axis_value_lists = [axis["values"] for axis in axes]
    status_by_key: dict[tuple[object, ...], str] = {
        tuple(cell["axis_values"]): cell["status"] for cell in result["cells"]
    }

    transitions: list[tuple[str, tuple[object, ...], str, tuple[object, ...], str]] = []
    for axis_index in range(len(axes)):
        values = axis_value_lists[axis_index]
        other_indices = [i for i in range(len(axes)) if i != axis_index]
        other_value_lists = [axis_value_lists[i] for i in other_indices]
        for other_combo in itertools.product(*other_value_lists):
            for i in range(len(values) - 1):
                key_a: list[object] = [None] * len(axes)
                key_b: list[object] = [None] * len(axes)
                for idx, val in zip(other_indices, other_combo):
                    key_a[idx] = val
                    key_b[idx] = val
                key_a[axis_index] = values[i]
                key_b[axis_index] = values[i + 1]
                tuple_a, tuple_b = tuple(key_a), tuple(key_b)
                if tuple_a not in status_by_key or tuple_b not in status_by_key:
                    continue
                status_a, status_b = status_by_key[tuple_a], status_by_key[tuple_b]
                if {status_a, status_b} == {"catchable", "not_catchable"}:
                    transitions.append(
                        (axes[axis_index]["name"], tuple_a, status_a, tuple_b, status_b)
                    )
    return transitions


def test_every_cell_has_required_distance_as_grid(tmp_path: Path) -> None:
    """要件10.3: 候補ごとの必要移動量と成立性が格子として現れる。

    全格子点が `axis_values` / `status` / `metrics["required_distance_mm"]`
    （実数値）を持つことを確認する。
    """
    result = _run_layout_sweep(DRIVETRAIN_WHEEL60, tmp_path / "out.json")

    for cell in result["cells"]:
        assert "axis_values" in cell
        assert isinstance(cell["axis_values"], list)
        assert "status" in cell
        assert cell["status"] in {"catchable", "not_catchable", "not_evaluated"}
        assert "metrics" in cell
        metrics = cell["metrics"]
        assert "required_distance_mm" in metrics, (
            f"格子点 {cell['axis_values']!r} の metrics に "
            "required_distance_mm が無い"
        )
        value = metrics["required_distance_mm"]
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        assert math.isfinite(value), f"required_distance_mm が有限値でない: {value!r}"


def test_catchable_and_not_catchable_boundary_exists(tmp_path: Path) -> None:
    """要件10.4: 成立する候補と成立しない候補の境界が読み取れる。

    格子内のどこかに、隣接する格子点間で `status` が `catchable` と
    `not_catchable` の間で切り替わる箇所が少なくとも1つ実在することを、
    ハードコードされた特定のペアではなく一般的な走査で確認する
    （退化した「全格子点が同一状態」の結果ではないことの証明）。
    """
    result = _run_layout_sweep(DRIVETRAIN_WHEEL60, tmp_path / "out.json")

    statuses = {cell["status"] for cell in result["cells"]}
    assert {"catchable", "not_catchable"} <= statuses, (
        f"catchable/not_catchable の両方が格子に現れていない: {statuses!r}"
    )

    transitions = _find_status_transitions(result)
    assert transitions, (
        "格子内に catchable/not_catchable の境界（隣接格子点間での状態の"
        "切り替わり）が1つも見つからない。全格子点が同一状態に退化している"
        "可能性がある。"
    )


def test_wheel_diameter_changes_layout_sweep_outcome(tmp_path: Path) -> None:
    """要件4.8: 60mm / 48mm ホイールで同一のレイアウト掃引を実行すると
    結果が異なることを確認する（`sweep-layout.json` 自身のE2Eストーリーとして
    独立に再確認する。`test_trajectory_sim_configs.py` とは別の掃引軸の
    組み合わせ・独立したアサーションである）。
    """
    result_60 = _run_layout_sweep(DRIVETRAIN_WHEEL60, tmp_path / "out60.json")
    result_48 = _run_layout_sweep(DRIVETRAIN_WHEEL48, tmp_path / "out48.json")

    cells_60 = result_60["cells"]
    cells_48 = result_48["cells"]
    assert len(cells_60) == len(cells_48)
    assert cells_60 != cells_48, (
        "60mm/48mm ホイールで同一のレイアウト掃引の結果が完全に同一であり、"
        "ホイール径の差が結果に反映されていない"
    )


def test_both_catch_policies_present_and_independently_queryable(tmp_path: Path) -> None:
    """要件5.6: 停止方針/通過方針の結果が同一の掃引から取り出せる。

    `catch.policy` 軸の両方の値が格子点として存在し、各々が自身の
    `status`/`metrics` を独立に持つことを確認する（同一掃引の出力から
    両方針の結果をそれぞれ取り出せることの証明）。
    """
    result = _run_layout_sweep(DRIVETRAIN_WHEEL60, tmp_path / "out.json")

    axis_names = [axis["name"] for axis in result["sweep"]["axes"]]
    policy_index = axis_names.index("catch.policy")
    other_indices = [i for i in range(len(axis_names)) if i != policy_index]

    by_policy: dict[str, dict[tuple[object, ...], dict[str, object]]] = {
        "stop_and_wait": {},
        "pass_through": {},
    }
    for cell in result["cells"]:
        axis_values = cell["axis_values"]
        policy = axis_values[policy_index]
        assert policy in by_policy, f"未知の catch.policy 値: {policy!r}"
        other_key = tuple(axis_values[i] for i in other_indices)
        by_policy[policy][other_key] = cell

    assert by_policy["stop_and_wait"], "stop_and_wait 方針の格子点が存在しない"
    assert by_policy["pass_through"], "pass_through 方針の格子点が存在しない"

    # 停止方針/通過方針は、他の軸（layout.home_x_mm / throw.azimuth_deg）の
    # 値が完全に同一の組み合わせについて、同一掃引内でそれぞれ独立に
    # status/metrics を持つ（＝同一条件下での比較が同一掃引から取り出せる）。
    common_keys = set(by_policy["stop_and_wait"]) & set(by_policy["pass_through"])
    assert common_keys, (
        "layout.home_x_mm / throw.azimuth_deg が同一の組み合わせで、"
        "stop_and_wait と pass_through の両方の格子点が揃っていない"
    )
    for key in common_keys:
        stop_cell = by_policy["stop_and_wait"][key]
        pass_cell = by_policy["pass_through"][key]
        assert stop_cell["status"] in {"catchable", "not_catchable", "not_evaluated"}
        assert pass_cell["status"] in {"catchable", "not_catchable", "not_evaluated"}
        assert "metrics" in stop_cell and "metrics" in pass_cell
