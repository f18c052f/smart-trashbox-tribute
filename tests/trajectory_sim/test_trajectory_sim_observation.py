"""観測モデルのテスト（要件2.1-2.5, 2.7 / design.md「ObservationModel」）。

`trajectory_sim.observation.observe` が以下を満たすことを固定する:

- 標本化時刻は `検出開始遅れ + k × 標本化周期`（`k = 0, 1, ...`）で、真の
  落下時刻を超えない範囲に限る（要件2.1, 2.2）
- 誤差要因（軸別ノイズ・距離依存ノイズ・欠測）が全てゼロのとき、生成
  サンプルは真の軌道と厳密に一致する（要件2.7、design.md「ObservationModel」
  Validation）
- 乱数の消費順序は「サンプルごとに 欠測判定 → x ノイズ → y ノイズ →
  z ノイズ」で固定し、ノイズ標準偏差が0の軸では乱数を消費しない
  （design.md「ObservationModel」Invariants）
- 欠測率に従ってサンプルを脱落させ、脱落したサンプルは x/y/z の乱数を
  消費しない（要件2.5）
- 実効標準偏差は `sigma_axis + distance_sigma_rel_per_m2 * (観測原点から
  の距離[m])^2` であり、距離の単位換算は `units` を経由する（要件2.4、
  design.md「ObservationModel」Implementation Notes）
- 戻り値の `t_ms` は狭義単調増加であり、全要素の `t_ms <= impact.time_ms`
  （design.md「ObservationModel」Postconditions）

ファイル名は `test_trajectory_sim_observation.py`
（`tests/trajectory_sim/test_trajectory_sim_*.py` の命名方針を踏襲する）。
"""

from __future__ import annotations

import math
from random import Random

import pytest

from trajectory_sim import observation, units
from trajectory_sim.params import ObservationParams
from trajectory_sim.physics import ImpactPoint, TrueTrajectory


def _trajectory(**overrides: float) -> TrueTrajectory:
    """検証を通る最小構成の `TrueTrajectory` を返す（明示指定分のみ上書き）。"""
    base: dict[str, float] = dict(
        t0_ms=0.0,
        x0_mm=0.0,
        y0_mm=0.0,
        z0_mm=2000.0,
        vx_mm_ms=1.0,
        vy_mm_ms=0.5,
        vz_mm_ms=0.0,
        gravity_mm_ms2=units.mm_per_s2_to_mm_per_ms2(9806.65),
    )
    base.update(overrides)
    return TrueTrajectory(**base)


def _observation(**overrides: float) -> ObservationParams:
    """検証を通る最小構成の `ObservationParams` を返す（明示指定分のみ上書き）。"""
    base: dict[str, float] = dict(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sample_latency_ms=0.0,
        prediction_latency_ms=0.0,
    )
    base.update(overrides)
    return ObservationParams(**base)


class _RecordingRandom(Random):
    """`random` / `normalvariate` の呼び出しを記録しつつ実体は基底実装へ委譲するスパイ。

    決定性を壊さないよう、記録後は必ず基底実装の返り値をそのまま返す。

    CPython の `Random.normalvariate` は内部で `self.random()` を（棄却法
    のため複数回）呼び出す。`self.random` はオーバーライドされたこの
    サブクラスのメソッドに束縛されるため、素朴に `random()` を記録すると
    `normalvariate` 内部の呼び出しまで記録されてしまう。`_in_normalvariate`
    フラグで内部呼び出し中の `random()` 記録を抑止し、`observe()` が
    明示的に呼んだ「欠測判定の1回」だけを記録する。
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, tuple[float, ...]]] = []
        self._in_normalvariate = False

    def random(self) -> float:
        value = super().random()
        if not self._in_normalvariate:
            self.calls.append(("random", ()))
        return value

    def normalvariate(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        self._in_normalvariate = True
        try:
            value = super().normalvariate(mu, sigma)
        finally:
            self._in_normalvariate = False
        self.calls.append(("normalvariate", (mu, sigma)))
        return value


# ---------------------------------------------------------------------------
# 完了条件: 誤差要因ゼロなら真の軌道と厳密に一致する（要件2.7）
# ---------------------------------------------------------------------------


def test_observe_matches_true_trajectory_exactly_when_all_errors_disabled() -> None:
    """全誤差要因ゼロ（既定値）のとき、サンプルは真の位置と厳密に一致する。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=999.0, y_mm=999.0, time_ms=530.0)
    observation_params = _observation(detection_start_delay_ms=50.0, sample_period_ms=100.0)
    rng = Random(0)

    samples = observation.observe(trajectory, impact, observation_params, rng)

    expected_times = [50.0, 150.0, 250.0, 350.0, 450.0]
    assert [s.t_ms for s in samples] == expected_times
    for sample in samples:
        expected_x, expected_y, expected_z = trajectory.position_at(sample.t_ms)
        assert sample.x_mm == expected_x
        assert sample.y_mm == expected_y
        assert sample.z_mm == expected_z


def test_observe_sampling_times_stop_at_or_before_impact_time() -> None:
    """`t_k > impact.time_ms` となる時刻は含めない（境界: ちょうど一致する場合は含む）。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(detection_start_delay_ms=50.0, sample_period_ms=100.0)

    samples = observation.observe(trajectory, impact, observation_params, Random(1))

    # 50, 150, 250 は <= 250.0 なので含まれ、350 は超えるため含まれない。
    assert [s.t_ms for s in samples] == [50.0, 150.0, 250.0]


def test_observe_uses_trajectory_t0_ms_as_anchor_not_zero() -> None:
    """`t0_ms` が 0 でない軌道でも、標本化時刻の起点として `t0_ms` を用いる。"""
    trajectory = _trajectory(t0_ms=1000.0)
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=1250.0)
    observation_params = _observation(detection_start_delay_ms=50.0, sample_period_ms=100.0)

    samples = observation.observe(trajectory, impact, observation_params, Random(2))

    assert [s.t_ms for s in samples] == [1050.0, 1150.0, 1250.0]


def test_observe_returns_empty_when_no_sampling_time_before_impact() -> None:
    """検出開始遅れが真の落下時刻を超える場合、サンプルは1つも生成されない。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=30.0)
    observation_params = _observation(detection_start_delay_ms=50.0, sample_period_ms=100.0)

    samples = observation.observe(trajectory, impact, observation_params, Random(3))

    assert samples == ()


# ---------------------------------------------------------------------------
# Postconditions: 狭義単調増加、t_ms <= impact.time_ms
# ---------------------------------------------------------------------------


def test_observe_postconditions_strictly_increasing_and_bounded_by_impact_time() -> None:
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=1000.0)
    observation_params = _observation(
        detection_start_delay_ms=10.0,
        sample_period_ms=30.0,
        sigma_x_mm=5.0,
        sigma_y_mm=5.0,
        sigma_z_mm=5.0,
        dropout_ratio=0.3,
    )
    rng = Random(42)

    samples = observation.observe(trajectory, impact, observation_params, rng)

    assert len(samples) > 0
    for prev, curr in zip(samples, samples[1:]):
        assert curr.t_ms > prev.t_ms
    for sample in samples:
        assert sample.t_ms <= impact.time_ms


# ---------------------------------------------------------------------------
# RNG 消費順序・回数の契約（design.md「ObservationModel」Invariants）
# ---------------------------------------------------------------------------


def test_observe_draws_dropout_check_always_even_when_dropout_ratio_zero() -> None:
    """`dropout_ratio == 0.0` でも欠測判定の `rng.random()` は毎回引く。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(detection_start_delay_ms=50.0, sample_period_ms=100.0)
    rng = _RecordingRandom(5)

    observation.observe(trajectory, impact, observation_params, rng)

    # 3 サンプル (50, 150, 250) 分の "random" 呼び出しのみで、ノイズ無しの
    # ため normalvariate は呼ばれない。
    assert [c[0] for c in rng.calls] == ["random", "random", "random"]


def test_observe_draws_in_fixed_order_dropout_then_x_y_z_when_all_sigma_nonzero() -> None:
    """全軸にノイズがあるとき、各サンプルで 欠測判定→x→y→z の順に4回引く。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=150.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=2.0,
        sigma_z_mm=3.0,
    )
    rng = _RecordingRandom(6)

    observation.observe(trajectory, impact, observation_params, rng)

    # サンプルは t=50, t=150 の2つ。各サンプルにつき random, normalvariate*3。
    kinds = [c[0] for c in rng.calls]
    assert kinds == [
        "random",
        "normalvariate",
        "normalvariate",
        "normalvariate",
        "random",
        "normalvariate",
        "normalvariate",
        "normalvariate",
    ]


def test_observe_skips_draw_for_individually_zero_sigma_axes() -> None:
    """一部の軸のみノイズが0のとき、その軸の乱数は消費しない。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=50.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=0.0,
        sigma_y_mm=2.0,
        sigma_z_mm=0.0,
    )
    rng = _RecordingRandom(7)

    observation.observe(trajectory, impact, observation_params, rng)

    assert [c[0] for c in rng.calls] == ["random", "normalvariate"]


def test_observe_dropped_sample_consumes_only_dropout_draw() -> None:
    """脱落したサンプルは x/y/z の乱数を一切消費しない。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=1.0,
        sigma_z_mm=1.0,
        dropout_ratio=0.999999,
    )
    rng = _RecordingRandom(8)

    samples = observation.observe(trajectory, impact, observation_params, rng)

    # dropout_ratio がほぼ1なので、rng.random() が dropout_ratio 未満になる
    # 可能性が極めて高い。実際に全て脱落したかどうかに関わらず、呼び出し
    # 回数が「サンプル数 + 3 * 脱落しなかったサンプル数」と一致することを
    # 確認する（脱落した分のスロットで x/y/z がスキップされていることの証明）。
    dropout_calls = [c for c in rng.calls if c[0] == "random"]
    normal_calls = [c for c in rng.calls if c[0] == "normalvariate"]
    assert len(dropout_calls) == 3  # 3候補時刻 (50, 150, 250) 分
    assert len(normal_calls) == 3 * len(samples)


def test_observe_does_not_use_gauss(monkeypatch: pytest.MonkeyPatch) -> None:
    """`rng.gauss` はキャッシュにより呼び出し順に状態が残るため使用してはならない。"""

    def _forbidden(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("observe は rng.gauss を呼び出してはならない")

    monkeypatch.setattr(Random, "gauss", _forbidden, raising=True)
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=1.0,
        sigma_z_mm=1.0,
        dropout_ratio=0.1,
    )
    observation.observe(trajectory, impact, observation_params, Random(9))  # 例外なければ成功


def test_observe_does_not_use_global_random_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """モジュール大域の `random` を使わず、渡された `rng` のみを消費する。"""
    import random as global_random_module

    def _forbidden(*_args: object, **_kwargs: object) -> float:
        raise AssertionError("observe はグローバルな random モジュールを使ってはならない")

    monkeypatch.setattr(global_random_module, "random", _forbidden, raising=True)
    monkeypatch.setattr(global_random_module, "normalvariate", _forbidden, raising=True)
    monkeypatch.setattr(global_random_module, "gauss", _forbidden, raising=True)

    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=1.0,
        sigma_z_mm=1.0,
        dropout_ratio=0.1,
    )
    observation.observe(trajectory, impact, observation_params, Random(10))  # 例外なければ成功


# ---------------------------------------------------------------------------
# 軸ごとのノイズ付与（要件2.3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sigma_field, other_fields",
    [
        ("sigma_x_mm", ("sigma_y_mm", "sigma_z_mm")),
        ("sigma_y_mm", ("sigma_x_mm", "sigma_z_mm")),
        ("sigma_z_mm", ("sigma_x_mm", "sigma_y_mm")),
    ],
)
def test_observe_applies_noise_only_to_axis_with_nonzero_sigma(
    sigma_field: str, other_fields: tuple[str, str]
) -> None:
    """ある軸のみ sigma > 0 のとき、その軸だけ真の位置からずれ、他の軸は厳密一致する。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=250.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        **{sigma_field: 50.0},
    )
    rng = Random(11)

    samples = observation.observe(trajectory, impact, observation_params, rng)

    axis_map = {"sigma_x_mm": "x_mm", "sigma_y_mm": "y_mm", "sigma_z_mm": "z_mm"}
    noisy_axis = axis_map[sigma_field]
    other_axes = [axis_map[f] for f in other_fields]

    assert len(samples) > 0
    any_noisy_differs = False
    for sample in samples:
        true_x, true_y, true_z = trajectory.position_at(sample.t_ms)
        true_position = {"x_mm": true_x, "y_mm": true_y, "z_mm": true_z}
        for axis in other_axes:
            assert getattr(sample, axis) == true_position[axis]
        if getattr(sample, noisy_axis) != true_position[noisy_axis]:
            any_noisy_differs = True
    assert any_noisy_differs


# ---------------------------------------------------------------------------
# 距離依存ノイズ項（要件2.4）
# ---------------------------------------------------------------------------


def test_observe_effective_sigma_matches_hand_computed_distance_term() -> None:
    """`sigma_x_mm=sigma_y_mm=sigma_z_mm=0`, `distance_sigma_rel_per_m2>0` のとき、
    実効シグマ（全軸共通の距離項のみ）は手計算値と一致する。

    2本の同シードの `Random` を用意し、片方は `observe()` に渡し、もう
    片方で「欠測判定 → x → y → z」という同じ draw 順序を、手計算した
    実効シグマを使って再現し、3軸すべてのノイズ値が一致することを確認
    する。これにより distance_sigma_rel_per_m2 の適用式そのものを直接
    検証する（3軸とも sigma_axis_mm=0 のため、実効シグマは軸によらず
    距離項のみで共通になる）。
    """
    trajectory = _trajectory(
        x0_mm=0.0,
        y0_mm=0.0,
        z0_mm=0.0,
        vx_mm_ms=0.0,
        vy_mm_ms=0.0,
        vz_mm_ms=0.0,
        gravity_mm_ms2=0.0,
    )
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=50.0)
    observer_x_mm, observer_y_mm, observer_z_mm = 3000.0, 4000.0, 0.0
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        distance_sigma_rel_per_m2=2.0,
        observer_x_mm=observer_x_mm,
        observer_y_mm=observer_y_mm,
        observer_z_mm=observer_z_mm,
    )

    seed = 123
    rng_under_test = Random(seed)
    samples = observation.observe(trajectory, impact, observation_params, rng_under_test)
    assert len(samples) == 1
    sample = samples[0]

    # 手計算: 真の位置は静止（release位置 (0,0,0)）。距離 = sqrt(3000^2+4000^2) = 5000mm = 5m。
    true_x, true_y, true_z = trajectory.position_at(sample.t_ms)
    distance_mm = math.sqrt(
        (true_x - observer_x_mm) ** 2
        + (true_y - observer_y_mm) ** 2
        + (true_z - observer_z_mm) ** 2
    )
    assert distance_mm == pytest.approx(5000.0)
    distance_m = units.mm_to_m(distance_mm)
    expected_sigma = observation_params.distance_sigma_rel_per_m2 * distance_m**2
    assert expected_sigma == pytest.approx(2.0 * 25.0)  # 2.0 * 5^2 = 50.0

    rng_reference = Random(seed)
    _ = rng_reference.random()  # 欠測判定の draw を再現
    expected_noise_x = rng_reference.normalvariate(0.0, expected_sigma)
    expected_noise_y = rng_reference.normalvariate(0.0, expected_sigma)
    expected_noise_z = rng_reference.normalvariate(0.0, expected_sigma)

    assert sample.x_mm == pytest.approx(true_x + expected_noise_x)
    assert sample.y_mm == pytest.approx(true_y + expected_noise_y)
    assert sample.z_mm == pytest.approx(true_z + expected_noise_z)


# ---------------------------------------------------------------------------
# 欠測（要件2.5）
# ---------------------------------------------------------------------------


def test_observe_drops_samples_deterministically_based_on_seeded_rng() -> None:
    """既知シードで欠測判定の乱数列を先読みし、脱落するサンプルを予測して照合する。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=450.0)
    dropout_ratio = 0.5
    observation_params = _observation(
        detection_start_delay_ms=50.0, sample_period_ms=100.0, dropout_ratio=dropout_ratio
    )
    seed = 999

    # 期待される脱落パターンを、同シードの参照 rng から先読みする
    # （観測ノイズが全てゼロなので消費するのは各候補で random() の1回のみ）。
    reference_rng = Random(seed)
    candidate_times = [50.0, 150.0, 250.0, 350.0, 450.0]
    expected_kept_times = []
    for t in candidate_times:
        u = reference_rng.random()
        if u >= dropout_ratio:
            expected_kept_times.append(t)

    samples = observation.observe(trajectory, impact, observation_params, Random(seed))

    assert [s.t_ms for s in samples] == expected_kept_times
    assert len(expected_kept_times) < len(candidate_times)  # 実際に何かが脱落する構成であること


def test_observe_dropped_samples_excluded_and_rng_slot_does_not_leak_into_other_axes() -> None:
    """脱落ありのシナリオで、脱落しなかったサンプル数だけ normalvariate が呼ばれる。"""
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=950.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=1.0,
        sigma_z_mm=1.0,
        dropout_ratio=0.5,
    )
    rng = _RecordingRandom(2024)

    samples = observation.observe(trajectory, impact, observation_params, rng)

    dropout_calls = [c for c in rng.calls if c[0] == "random"]
    normal_calls = [c for c in rng.calls if c[0] == "normalvariate"]
    candidate_count = 10  # 50,150,...,950 の10候補
    assert len(dropout_calls) == candidate_count
    assert len(normal_calls) == 3 * len(samples)
    assert len(samples) < candidate_count  # 実際に何かが脱落する構成であること


# ---------------------------------------------------------------------------
# 決定性
# ---------------------------------------------------------------------------


def test_observe_is_deterministic_for_same_seed_and_inputs() -> None:
    trajectory = _trajectory()
    impact = ImpactPoint(x_mm=0.0, y_mm=0.0, time_ms=450.0)
    observation_params = _observation(
        detection_start_delay_ms=50.0,
        sample_period_ms=100.0,
        sigma_x_mm=1.0,
        sigma_y_mm=1.0,
        sigma_z_mm=1.0,
        dropout_ratio=0.2,
    )
    samples_a = observation.observe(trajectory, impact, observation_params, Random(321))
    samples_b = observation.observe(trajectory, impact, observation_params, Random(321))
    assert samples_a == samples_b
