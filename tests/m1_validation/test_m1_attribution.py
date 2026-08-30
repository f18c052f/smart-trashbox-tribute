"""観測由来の予測ばらつきを再抽出で見積もる部分の検証（タスク 5.1、要件 6.8）。

観測可能な完了状態（tasks.md 5.1）を固定する:

- **既知のノイズ量を持つ入力に対し、見積もったばらつきがノイズ量に応じて増える**
- **同一種で2回実行した結果が一致する**

あわせてタスク箇条と design.md「ErrorAttributor」が定める点も固定する:

- **新しい推定器を実装せず、予測コアの予測をそのまま呼び直す**
  （`prediction_core.predict` が再抽出回数ぶん呼ばれ、渡るのは**記録の
  サンプルそのもの**と**記録の設定そのもの**である）
- **乱数種を設定から与える**（同一種で一致し、別の種では別の抽出になる）
- **グローバルな乱数状態を触らない**（他テストとの干渉・並列実行での非決定を招く）
- 再抽出回数が設定可能であり、**既定値が暫定の評価候補である旨**が設定の
  説明に出る（要件 13.7）

**なぜ「呼び直す」ことをテストで固定するのか。** 本モジュールが自前で軌道
当てはめを書くと、見積もったばらつきは**本番の予測器の性質ではなく自前実装の
性質**になる。そのばらつきを基準にして「観測ノイズ由来か、モデル由来か」を
判定する（要件 6.6 / 6.7）ので、基準が別物になった時点で帰属そのものが
無意味になる。したがって「予測コアが呼ばれていること」は実装の内部事情では
なく、**要件 6.8 の意味そのもの**である。

**参照解は実装に触れない。** 軌道・ノイズ量・重力はすべて本ファイルの
リテラルから組み、`attribution` の定数を参照しない（tasks.md
「Implementation Notes」タスク4.1: 参照解を実装の定数から組むと、その定数を
変えたとき参照解が一緒に動いて差が消える）。基準になる量に 0 を置かない
（タスク2.2）——リリース位置・時刻・待機高さはすべて非 0 である。

**私有ヘルパを直接呼ぶ検査が2つある**（`TestSpreadReduction` と
`TestJudgementBoundaries`）。ばらつきの**分母（N か N-1 か）と平均まわりで
あること**、および規則5・6 とレポート偏りの**境界の包含性**は、公開経路からは
「実装の出力そのもの」を参照せずに独立に導けない——再抽出の見積もりは入力
データから決まる量なので、それとちょうど等しい値を公開経路の入力側だけから
作れないためである。判定関数はモジュール直下にあってリテラル引数から直接
呼べるので、境界だけをそこで突く。タスク3.1 が `_with_m1_extra()` を直接
呼んだのと同じ理由である。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import math
import random
from collections.abc import Sequence
from pathlib import Path

import pytest
from m1fixtures import write_layout

from m1_validation import attribution
from m1_validation.attribution import (
    AttributionResult,
    BiasComponent,
    BootstrapSpread,
    ScatterComponent,
    attribute,
    bootstrap_prediction_spread,
)
from m1_validation.config import PROVISIONAL_NOTICE, M1Settings
from m1_validation.errors import M1ConfigError, M1ValidationError
from m1_validation.metrics.aggregate import ThrowAggregate, ThrowRow
from m1_validation.types import Attribution
from prediction_core import (
    InvalidReason,
    Prediction,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowRecord,
    TrajectoryParameters,
)

# --- 既知の軌道（すべてテスト局所のリテラル）--------------------------------

CONFIG = PredictionConfig()
G_MM_MS2 = CONFIG.gravity_mm_ms2

RECORD_ID = "throw-bootstrap-0007"

RELEASE_T_MS = 5000.0
RELEASE_X_MM = -2000.0
RELEASE_Y_MM = 300.0
RELEASE_HEIGHT_MM = 1500.0
VX_MM_MS = 3.0
VY_MM_MS = -0.5
VZ_MM_MS = 2.0

FIRST_SAMPLE_T_MS = 5100.0
DT_MS = 1000.0 / 60.0
SAMPLE_COUNT = 8

#: 注入する観測ノイズの標準偏差（mm）。**10 倍差**にしてあるのは、
#: ばらつきの見積もりがノイズ量に比例して増えることを、単なる大小比較では
#: なく**比**で確かめるためである（定数を返す実装・スケールを無視する実装は
#: 大小比較だけなら偶然通り得る）。
LOW_NOISE_MM = 2.0
HIGH_NOISE_MM = 20.0

#: ノイズを与えない入力に対して残る量の上限（mm）。サンプルが厳密に放物線上に
#: あるので、どの再抽出でも当てはめは同じ厳密解になり、ばらつきは浮動小数の
#: 条件数だけに由来する。`LOW_NOISE_MM` に対して得られるばらつきより 6 桁
#: 小さく、両者は取り違えようがない。
NOISE_FREE_TOLERANCE_MM = 1e-6

#: 床面の高さ（mm）。**実装の定数を参照せず、ここにリテラルで置く**
#: （`test_m1_metrics_accuracy.py` と同じ組み立て）。
FLOOR_Z_MM = 0.0

#: ノイズ注入に使う乱数（**テスト局所**であり、実装の再抽出とは無関係）。
NOISE_SEED = 20260829


def position_at(t_ms: float) -> tuple[float, float, float]:
    """既知の軌道上の位置（World mm）。"""
    s = t_ms - RELEASE_T_MS
    return (
        RELEASE_X_MM + VX_MM_MS * s,
        RELEASE_Y_MM + VY_MM_MS * s,
        RELEASE_HEIGHT_MM + VZ_MM_MS * s - 0.5 * G_MM_MS2 * s * s,
    )


def analytic_impact_point_mm() -> tuple[float, float]:
    """既知の軌道が床面（z = 0）を横切る点（World mm の水平2成分）。

    **テスト局所のリテラルだけから組む。** 実装（`attribution` /
    `prediction_core`）の定数には一切触れない——参照解を実装の定数から組むと、
    その定数を変えたとき参照解が一緒に動いて差が消える（tasks.md
    「Implementation Notes」タスク4.1）。床面高さ 0.0 も本ファイルの
    リテラルである。
    """
    discriminant = VZ_MM_MS**2 + 2.0 * G_MM_MS2 * (RELEASE_HEIGHT_MM - FLOOR_Z_MM)
    s_ms = (VZ_MM_MS + math.sqrt(discriminant)) / G_MM_MS2
    return (RELEASE_X_MM + VX_MM_MS * s_ms, RELEASE_Y_MM + VY_MM_MS * s_ms)


def build_samples(
    *,
    noise_sigma_mm: float = 0.0,
    axis_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    count: int = SAMPLE_COUNT,
) -> tuple[Sample, ...]:
    """既知の軌道を等間隔で標本化し、必要なら既知量のノイズを載せる。

    `axis_scale` は軸ごとのノイズ倍率であり、**等方でないノイズ**を作れる
    ようにするためにある。等方なノイズだけで検査すると、「x 成分だけで測った
    ばらつき」と「水平2成分で測ったばらつき」が比でも大小でも区別できず、
    落下地点の成分の取り違え（例: `(x, y)` を `(x, x)` にする）がまるごと
    素通りする（既知の空振り形1: 区別すべき差が現れない入力でだけ検査する）。
    """
    rng = random.Random(NOISE_SEED)
    samples: list[Sample] = []
    scale_x, scale_y, scale_z = axis_scale
    for index in range(count):
        t_ms = FIRST_SAMPLE_T_MS + index * DT_MS
        x_mm, y_mm, z_mm = position_at(t_ms)
        if noise_sigma_mm:
            # 3軸ぶんの引きは倍率が 0 でも必ず消費する。軸を止めたときに
            # 他軸の実現値まで変わると、等方の入力と比べたときの差が
            # 「軸を止めたこと」由来なのか「別の乱数列」由来なのか読めない。
            x_mm += rng.gauss(0.0, 1.0) * noise_sigma_mm * scale_x
            y_mm += rng.gauss(0.0, 1.0) * noise_sigma_mm * scale_y
            z_mm += rng.gauss(0.0, 1.0) * noise_sigma_mm * scale_z
        samples.append(Sample(t_ms=t_ms, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm))
    return tuple(samples)


def build_record(
    *,
    noise_sigma_mm: float = 0.0,
    axis_scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    count: int = SAMPLE_COUNT,
) -> ThrowRecord:
    """再抽出の対象になる投擲記録。

    `predictions` は空でよい——再抽出は**観測サンプルから予測をやり直す**
    のであって、記録済みの予測を読み直すのではない。
    """
    return ThrowRecord(
        record_id=RECORD_ID,
        source=SourceKind.SIMULATED,
        config=CONFIG,
        samples=build_samples(
            noise_sigma_mm=noise_sigma_mm, axis_scale=axis_scale, count=count
        ),
        predictions=(),
    )


def spread_mm(result: BootstrapSpread) -> float:
    """`rms_mm` を数として取り出す（欠測なら検査を落とす）。"""
    assert result.rms_mm is not None
    return result.rms_mm


class TestDeterminism:
    """同一入力・同一種に対して同一結果（タスク 5.1、要件 12.4 / 3.7）。"""

    def test_same_seed_gives_an_identical_result(self) -> None:
        """**厳密一致**を要求する。近似一致では乱数種の無視を見逃す。"""
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        first = bootstrap_prediction_spread(record, iterations=64, seed=1234)
        second = bootstrap_prediction_spread(record, iterations=64, seed=1234)
        assert first == second

    def test_the_used_seed_is_reported_with_the_estimate(self) -> None:
        """**種は結果と同じ場所に残る**——種の分からない見積もりは再現できない。

        `rms_mm` だけを見る検査では、`seed` を定数へ潰した実装（実際に引いた
        種と報告する種が食い違う）が素通りする。**期待値はテスト局所の
        リテラル**であり、実装の既定値（`AttributionConfig.bootstrap_seed`）
        との自己比較にしない（既知の空振り形3）。
        """
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        assert bootstrap_prediction_spread(record, iterations=8, seed=1234).seed == 1234
        assert bootstrap_prediction_spread(record, iterations=8, seed=99).seed == 99

    def test_a_different_seed_draws_a_different_resample(self) -> None:
        """種を無視して固定の抽出を返す実装をここで落とす。

        `test_same_seed_gives_an_identical_result` と**対**である。片方だけ
        では「毎回新しい種を使う」実装（前者が落ちる）と「種を定数に潰した」
        実装（後者が落ちる）を区別できない。
        """
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        first = bootstrap_prediction_spread(record, iterations=64, seed=1234)
        other = bootstrap_prediction_spread(record, iterations=64, seed=99)
        assert spread_mm(first) != spread_mm(other)
        assert (first.seed, other.seed) == (1234, 99)

    def test_the_global_random_state_is_left_untouched(self) -> None:
        """**グローバルな乱数状態を触らない**（タスク 5.1）。

        `random.seed()` や `random.random()` をモジュール大域の乱数器で
        呼ぶと、**同じプロセスで走る他テストの乱数列が本関数の呼び出し回数に
        依存する**。並列実行・実行順の変更で結果が動く非決定の温床になる。
        """
        random.seed(4242)
        before = [random.random() for _ in range(5)]

        random.seed(4242)
        bootstrap_prediction_spread(
            build_record(noise_sigma_mm=LOW_NOISE_MM), iterations=32, seed=7
        )
        after = [random.random() for _ in range(5)]

        assert after == before


class TestNoiseScaling:
    """見積もったばらつきがノイズ量に応じて増える（タスク 5.1 の完了状態）。"""

    def test_noise_free_observations_leave_no_spread(self) -> None:
        """厳密に放物線上のサンプルなら、どう抜き直しても同じ解になる。

        ここが 0 でない実装は、再抽出以外の何か（自前の当てはめ・ノイズの
        自家生成）を混ぜている。
        """
        result = bootstrap_prediction_spread(
            build_record(noise_sigma_mm=0.0), iterations=64, seed=1234
        )
        assert spread_mm(result) < NOISE_FREE_TOLERANCE_MM

    def test_spread_grows_in_proportion_to_the_observation_noise(self) -> None:
        """ノイズを 10 倍にすれば、ばらつきの見積もりも同じ程度に増える。

        予測は観測位置に対しておおむね線形であり、ばらつきはノイズ量に比例
        する。**比が 7〜14 倍に収まること**を要求する——大小比較だけだと
        「ノイズ量を無視して定数を返す」実装が偶然通り得る。

        帯の根拠: 決定的な種（`NOISE_SEED` と `seed=1234`）に対する実測は
        9.51 であり、10 倍の比例からのずれは**有限回の再抽出**（200 回）と
        ノイズ実現値の差に由来する。帯は実測値の上下に約 4 割ずつ取った。
        比例を壊す実装——定数（比 1.0）・平方根スケール（3.16）・二乗スケール
        （100）——はいずれもこの帯の外へ出る。
        """
        low = spread_mm(
            bootstrap_prediction_spread(
                build_record(noise_sigma_mm=LOW_NOISE_MM), iterations=200, seed=1234
            )
        )
        high = spread_mm(
            bootstrap_prediction_spread(
                build_record(noise_sigma_mm=HIGH_NOISE_MM), iterations=200, seed=1234
            )
        )
        assert low > NOISE_FREE_TOLERANCE_MM * 1_000
        assert 7.0 < high / low < 14.0

    def test_both_horizontal_components_enter_the_spread(self) -> None:
        """**ばらつきは水平2成分の両方**から測る（要件 6.2 / 6.8）。

        y だけにノイズを載せた投擲は、落下地点の y だけが散らばる。落下地点を
        `(x, x)` のように片方の成分だけで組む実装は、ここでばらつきが消える
        （x にも z にもノイズが無いので落下地点の x は定数になる）。等方な
        ノイズだけで検査していると、この取り違えが比でも大小でも現れない。

        **y 由来と x 由来のばらつきが同程度になる**ことも要求する。両者は
        同じ時刻列・同じノイズ量を通る同じ形の当てはめであり、違いは乱数の
        実現値だけだからである（実測は x: 12.5 mm / y: 17.6 mm、比 1.41）。
        帯を 0.5〜2.0 倍としたのはその実現値差の範囲であり、片成分を落とす
        実装（比 0 または ∞）はここに入らない。
        """
        x_only = spread_mm(
            bootstrap_prediction_spread(
                build_record(noise_sigma_mm=LOW_NOISE_MM, axis_scale=(1.0, 0.0, 0.0)),
                iterations=200,
                seed=1234,
            )
        )
        y_only = spread_mm(
            bootstrap_prediction_spread(
                build_record(noise_sigma_mm=LOW_NOISE_MM, axis_scale=(0.0, 1.0, 0.0)),
                iterations=200,
                seed=1234,
            )
        )
        assert x_only > 1.0
        assert y_only > 1.0
        assert 0.5 < y_only / x_only < 2.0

    def test_resampling_actually_varies_the_input(self) -> None:
        """元のサンプル列をそのまま繰り返し予測する実装をここで落とす。

        その実装ではどの反復も同じ予測になり、ばらつきが 0 になる。**観測に
        ノイズがある以上、ばらつきは 0 ではない**——0 を返せば要件 6.6 の
        「観測ノイズ由来」の範囲が常に空になり、すべてがモデル由来へ倒れる。
        """
        result = bootstrap_prediction_spread(
            build_record(noise_sigma_mm=LOW_NOISE_MM), iterations=200, seed=1234
        )
        assert spread_mm(result) > 1.0


class TestPredictionCoreIsReused:
    """**新しい推定器を実装せず、予測コアの予測をそのまま呼び直す**。"""

    def test_the_core_predictor_is_called_once_per_iteration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """再抽出回数を設定できる（既定値に落ちない）ことも同時に固定する。

        戻り値の `iterations` を見るだけでは、**引数を写しただけで既定回数
        しか回さない**実装を捕まえられない。実際の呼び出し回数を数える。
        """
        calls = _count_predict_calls(monkeypatch)
        result = bootstrap_prediction_spread(
            build_record(noise_sigma_mm=LOW_NOISE_MM), iterations=7, seed=1234
        )
        assert len(calls) == 7
        assert result.iterations == 7
        assert result.valid_count == 7

    def test_the_core_receives_the_recorded_samples_and_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """渡るのは**記録のサンプルそのもの**と**記録の設定そのもの**である。

        サンプルを作り直す（値を揺らす・別の型に詰め替える）実装は、
        見積もったばらつきが観測ノイズではなく自家生成ノイズの性質になる。
        """
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        calls = _count_predict_calls(monkeypatch)
        bootstrap_prediction_spread(record, iterations=32, seed=1234)

        original = set(record.samples)
        for drawn, config in calls:
            assert config is record.config
            assert len(drawn) == len(record.samples)
            assert all(sample in original for sample in drawn)

    def test_every_recorded_sample_can_be_drawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**元のどのサンプルも引かれうる**（引き先が偏っていない）。

        「引いたものが元に含まれる」「件数が同じ」だけでは、特定のサンプルを
        絶対に引かない実装（例: 添字の上限を1つ間違えて末尾を落とす）が
        素通りする。**末尾のサンプルは落下地点への外挿に最も効く**ので、
        系統的に落とすと見積もりが観測品質を表さなくなり、要件 6.8 の意味が
        失われる。種を固定しているのでこの検査は決定的である。
        """
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        calls = _count_predict_calls(monkeypatch)
        bootstrap_prediction_spread(record, iterations=64, seed=1234)

        drawn = {sample for samples, _ in calls for sample in samples}
        assert drawn == set(record.samples)

    def test_the_resample_is_drawn_with_replacement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """再抽出は**重複を許す**引き直しである（ブートストラップ）。

        重複を許さない並べ替えだけだと当てはめの入力集合が変わらず、
        ばらつきが観測ノイズの影響をまったく反映しない。
        """
        record = build_record(noise_sigma_mm=LOW_NOISE_MM)
        calls = _count_predict_calls(monkeypatch)
        bootstrap_prediction_spread(record, iterations=32, seed=1234)

        assert any(len(set(drawn)) < len(drawn) for drawn, _ in calls)


class TestMeanHitPoint:
    """再予測の平均落下地点は**値として**正しい（タスク 5.2 が消費する）。"""

    def test_the_mean_matches_the_analytic_impact_point(self) -> None:
        """ノイズ 0 の投擲では、再予測の平均は解析解ちょうどになる。

        `mean_hit_mm` は「ばらつきの中心」であり、タスク 5.2 の共通偏り
        （要件 6.2 / 6.4 / 6.5）が向きの判別に使いうる公開値である。`None`
        か否かしか見ないと、`(0.0, 0.0)` を返す実装が素通りし、**帰属の向き
        判定が黙って狂う**。

        参照解は `analytic_impact_point_mm()`——**テスト局所のリテラルだけ**
        から組んだ値であり、実装の定数には触れない（タスク4.1 の教訓）。
        期待値 (380.32…, -96.72…) は 0 から遠く、x と y で符号も大きさも
        違うので、**成分の取り違え・0 埋め・片成分の複製**がそのまま差として
        現れる。
        """
        result = bootstrap_prediction_spread(
            build_record(noise_sigma_mm=0.0), iterations=32, seed=1234
        )
        expected_x_mm, expected_y_mm = analytic_impact_point_mm()
        assert result.mean_hit_mm is not None
        assert result.mean_hit_mm[0] == pytest.approx(expected_x_mm, abs=1e-6)
        assert result.mean_hit_mm[1] == pytest.approx(expected_y_mm, abs=1e-6)


class TestUnusableInput:
    """見積もれないときに 0 を返さない（**0 で埋めない**）。"""

    def test_too_few_samples_yield_a_missing_spread(self) -> None:
        """予測が1回も成立しなければ、ばらつきは欠測である。

        0.0 を返すと「ばらつきが無かった」として要件 6.6 の判定に入り、
        **観測できていない投擲がすべて観測ノイズ由来へ倒れる**。
        """
        result = bootstrap_prediction_spread(
            build_record(count=2), iterations=16, seed=1234
        )
        assert result.rms_mm is None
        assert result.mean_hit_mm is None
        assert result.valid_count == 0
        assert result.invalid_counts == ((InvalidReason.INSUFFICIENT_SAMPLES, 16),)

    def test_every_iteration_is_accounted_for(self) -> None:
        """有効・無効のどちらかに**必ず1回ぶん**数える（不変条件）。

        3点しか無い投擲では、引き直しの一部が同じ点ばかりを引いて時刻が縮退
        する。その反復を黙って捨てると、**見積もりが何回ぶんの再抽出に基づく
        のかが分からなくなる**——回数を増やしたのに見積もりが動かない、と
        いった読み違いの原因になる。ここは有効・無効が**両方現れる**入力で
        検査する（片方しか出ない入力では、数え落としがそのまま素通りする）。
        """
        result = bootstrap_prediction_spread(
            build_record(noise_sigma_mm=LOW_NOISE_MM, count=3),
            iterations=64,
            seed=1234,
        )
        invalid_total = sum(count for _, count in result.invalid_counts)
        assert result.valid_count > 0
        assert invalid_total > 0
        assert result.valid_count + invalid_total == 64
        assert result.rms_mm is not None

    @pytest.mark.parametrize("iterations", [0, -1])
    def test_non_positive_iterations_are_rejected(self, iterations: int) -> None:
        """1回も引き直さない指定は設定の誤りとして拒否する（要件 13.6）。"""
        with pytest.raises(M1ConfigError):
            bootstrap_prediction_spread(
                build_record(noise_sigma_mm=LOW_NOISE_MM),
                iterations=iterations,
                seed=1234,
            )


class TestSpreadReduction:
    """ばらつきの定義を固定する（私有ヘルパを直接突く唯一の検査）。

    **分母は N**（母集団 RMS）であり、**平均まわり**の距離である。公開経路
    からこれを確かめるには再抽出の引きをテスト側で再現するほかなく、それは
    実装の写経であって検査にならない（モジュール docstring 参照）。
    """

    def test_rms_is_taken_about_the_mean_with_n_as_the_divisor(self) -> None:
        """2点 (0,0) と (30,40) の平均は (15,20)、各距離は 25。

        - 分母を N-1 にすると 35.355…（誤り）
        - 原点まわりにすると 35.355…（誤り）
        - 平均を引き忘れずとも成分を取り違えると値が動く
        """
        assert attribution._rms_about_mean_mm(
            ((0.0, 0.0), (30.0, 40.0))
        ) == pytest.approx(25.0)

    def test_components_are_paired_with_their_own_mean(self) -> None:
        """3点 (0,0) / (10,0) / (0,20)。平均は (10/3, 20/3)。

        平方距離の和は (500 + 800 + 1700)/9 = 3000/9 であり、N=3 で割って
        平方根を取ると sqrt(1000)/3 になる。**x に y の平均を当てる**取り
        違えはこの値を動かす。
        """
        assert attribution._rms_about_mean_mm(
            ((0.0, 0.0), (10.0, 0.0), (0.0, 20.0))
        ) == pytest.approx(math.sqrt(1000.0) / 3.0)

    @pytest.mark.parametrize("points", [(), ((7.0, -3.0),)])
    def test_fewer_than_two_points_have_no_spread(
        self, points: Sequence[tuple[float, float]]
    ) -> None:
        """1点では**ばらつきが定義できない**。0.0 ではなく欠測を返す。"""
        assert attribution._rms_about_mean_mm(tuple(points)) is None


class TestSettingsWiring:
    """再抽出回数と乱数種は設定から与える（タスク 5.1、要件 13.5 / 13.7）。"""

    def test_iterations_and_seed_are_resolvable_settings(
        self, tmp_path: Path
    ) -> None:
        settings = _resolve_settings(
            tmp_path, overrides={"bootstrap_iterations": 37, "bootstrap_seed": 99}
        )
        assert settings.attribution.bootstrap_iterations == 37
        assert settings.attribution.bootstrap_seed == 99

        described = settings.describe()["attribution"]
        assert described["bootstrap_iterations"] == 37  # type: ignore[index]
        assert described["bootstrap_seed"] == 99  # type: ignore[index]

    def test_the_iteration_default_is_declared_provisional(
        self, tmp_path: Path
    ) -> None:
        """**既定の再抽出回数を必須性能・合否条件として扱わない**（要件 13.7）。

        200 回は暫定の評価候補であり、実測前に置いた仮の値である。
        `--print-settings` の読み手がこれを必須条件と受け取ると、そこから
        逆算した判断が独り歩きする。
        """
        assert "bootstrap_iterations" in PROVISIONAL_NOTICE
        notice = str(_resolve_settings(tmp_path).describe()["provisional_notice"])
        assert "bootstrap_iterations" in notice
        assert "暫定" in notice
        assert "必須性能ではない" in notice


# --- テスト用ヘルパ ---------------------------------------------------------


def _count_predict_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[tuple[Sample, ...], PredictionConfig]]:
    """`attribution` が呼ぶ予測コアを、**本物へ委譲する**記録器で包む。

    差し替えた偽物に予測をさせない——本物の戻り値のまま流すことで、
    呼び出しの観測だけを足す。
    """
    calls: list[tuple[tuple[Sample, ...], PredictionConfig]] = []
    real_predict = attribution.predict

    def recording_predict(
        samples: Sequence[Sample], config: PredictionConfig
    ) -> object:
        calls.append((tuple(samples), config))
        return real_predict(samples, config)

    monkeypatch.setattr(attribution, "predict", recording_predict)
    return calls


def _resolve_settings(
    tmp_path: Path, *, overrides: dict[str, object] | None = None
) -> M1Settings:
    """レイアウトファイルを書き出して設定を解決する。

    レイアウトの雛形は共有ヘルパ `m1fixtures` から取る——同じフィクスチャを
    2箇所に置くと、形式が変わったとき片方だけ直して食い違う（tasks.md
    「Implementation Notes」タスク3.1）。
    """
    layout_file = write_layout(tmp_path)
    resolved: dict[str, object] = {"layout_file": str(layout_file)}
    resolved.update(overrides or {})
    return M1Settings.resolve(file=None, env={}, overrides=resolved)


# ===========================================================================
# タスク 5.2: 帰属の判定規則（要件 6.1-6.7, 6.9, 6.10, 6.11, 2.4）
# ===========================================================================
#
# 観測可能な完了状態（tasks.md 5.2）を固定する:
#
# - **帰属結果が偏り成分・ばらつき成分・距離帯・判定規則の説明文を含む**
# - **誤差ベクトルが0件の入力が試行数不足として失敗する**
#
# あわせて design.md「ErrorAttributor」判定規則の7つの分岐と、
# **2方向が縮退した場合の判別不能**（要件 6.10）を固定する。
#
# **参照解はすべて本ファイルのリテラルから組む。** 実装の定数
# （`attribution` / `config` の既定値）を期待値に使わない——定数を変えたとき
# 参照解が一緒に動いて差が消える（tasks.md「Implementation Notes」タスク4.1
# / 4.5）。閾値も投擲レイアウトのカメラ位置も、テスト局所のリテラルで書き、
# 実装側と一致することは**独立した不変条件検査**で押さえる。

#: 群のキャリブレーション識別子（テスト局所）。
CAL_ID = "cal-attr-0042"

#: カメラ位置（World mm）。`m1fixtures.write_layout` が書く値と同じであることは
#: `TestLayoutInvariants` が独立に固定する（自己比較にしない）。
CAMERA_X_MM = 0.0
CAMERA_Y_MM = -1500.0
CAMERA_Z_MM = 1000.0

#: 判定に使う閾値（テスト局所のリテラル。実装の既定値と同値であることは
#: `TestSettingsWiring` が独立に固定する）。
DIRECTION_AGREEMENT_DEG = 30.0
BIAS_SIGNIFICANCE_RATIO = 1.0
RESIDUAL_SIGNIFICANCE_RATIO = 1.0
RANGE_BAND_MM = 500.0

#: 再抽出回数（テストを速く保つための小さい値。既定値には落ちない）。
ATTR_ITERATIONS = 24
ATTR_SEED = 7

#: 落下地点の真値（World mm）。カメラからの距離は
#: hypot(1500, 1000) = 1802.77… mm であり、帯域 500 mm では [1500, 2000)。
NEAR_IMPACT_MM = (0.0, 0.0, 0.0)
#: もう一方の真値。距離は hypot(3000, 1000) = 3162.27… mm で [3000, 3500)。
FAR_IMPACT_MM = (0.0, 1500.0, 0.0)

#: 検証レポートのばらつき（mm）。平均オフセットの「認められる / 認められない」
#: の判定に効く（大きさがこの `BIAS_SIGNIFICANCE_RATIO` 倍以上なら認める）。
CAL_SCATTER_RMS_MM = 4.0

#: World 固定の平均オフセット（+X 方向）。
CAL_BIAS_X_MM: list[float] = [30.0, 0.0, 0.0]
#: カメラ視線方向（+Y）と同じ向きの平均オフセット。**縮退を作る**ための値。
CAL_BIAS_Y_MM: list[float] = [0.0, 30.0, 0.0]
#: カメラ視線（+Y）と**軸を共有しつつ逆向き**の平均オフセット。
#: 較正のずれが対象から見てカメラ側を向く場合であり、`CAL_BIAS_Y_MM` と
#: 対にして「軸の**両向き**で縮退する」ことを押さえるために使う。
CAL_BIAS_NEG_Y_MM: list[float] = [0.0, -30.0, 0.0]

#: 偏りが認められない検証レポート。
CAL_BIAS_ZERO_MM: list[float] = [0.0, 0.0, 0.0]

#: 誤差ベクトル群（World mm、`予測 − 実測`）。
#: 平均 (30, 0)、平均まわり・分母 N の RMS は sqrt(50/3) = 4.0824…。
BIAS_ALONG_X: tuple[tuple[float, float], ...] = (
    (30.0, 0.0),
    (34.0, 3.0),
    (26.0, -3.0),
)
#: 平均 (0, -30)（カメラ視線の**軸**に沿い、カメラ側へ寄る向き）。RMS は同上。
BIAS_TOWARD_CAMERA: tuple[tuple[float, float], ...] = (
    (0.0, -30.0),
    (3.0, -34.0),
    (-3.0, -26.0),
)
#: 平均 (30, 30)。+X からも +Y からも 45 度離れており、どちらとも整合しない。
BIAS_DIAGONAL: tuple[tuple[float, float], ...] = (
    (30.0, 30.0),
    (34.0, 26.0),
    (26.0, 34.0),
)
#: 平均 (1, 0)、RMS 15.81…。比は 0.063 で有意でない。
BIAS_INSIGNIFICANT: tuple[tuple[float, float], ...] = (
    (21.0, 0.0),
    (-19.0, 0.0),
    (1.0, 10.0),
    (1.0, -10.0),
)
#: 平均 (0, 0)、RMS は sqrt(80000/3) = 163.29…。ばらつきだけが大きい群。
SCATTER_ONLY: tuple[tuple[float, float], ...] = (
    (0.0, 200.0),
    (0.0, -200.0),
    (0.0, 0.0),
)

#: 平均 (30, 0)、平均まわり・分母 N の RMS はちょうど 15.0（2件なので厳密）。
#: 比が **ちょうど 2.0** になるので、有意性の境界（`>=` か `>` か）を突ける。
BIAS_BOUNDARY: tuple[tuple[float, float], ...] = ((45.0, 0.0), (15.0, 0.0))

#: 平均 (-30, 0)。検証レポートの平均オフセット (+X) とは**真逆**である。
BIAS_AGAINST_REPORT: tuple[tuple[float, float], ...] = (
    (-30.0, 0.0),
    (-34.0, -3.0),
    (-26.0, 3.0),
)

#: 平均 (0, +30)。カメラ視線方向（+Y）とも `CAL_BIAS_Y_MM` とも**同じ向き**
#: であり、規則2（キャリブレーション由来）と規則4（縮退）が**競合する**。
#: 縮退フィクスチャに `BIAS_TOWARD_CAMERA`（平均 (0, -30)）を使うと
#: `world_deg = 180` になって規則2 がそもそも成立せず、**順序が働かない**。
BIAS_AWAY_FROM_CAMERA: tuple[tuple[float, float], ...] = (
    (0.0, 30.0),
    (3.0, 34.0),
    (-3.0, 26.0),
)

#: 誤差が1件も外れていない投擲群。偏りもばらつきも 0 である。
ZERO_ERRORS: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),
    (0.0, 0.0),
    (0.0, 0.0),
)

#: カメラ視線方向（+Y）と同じ向きだが、レポートのばらつき 4.0 mm より小さい
#: 平均オフセット。**認められない偏りとは縮退しようがない**ことを突く。
CAL_BIAS_SMALL_Y_MM: list[float] = [0.0, 2.0, 0.0]

#: 平均 (30, 40)。大きさは 50 ちょうどで、単位ベクトルは (0.6, 0.8) に厳密へ
#: 落ちる。検証レポートの +X 方向との内積は **ちょうど 0.6** なので、
#: 角度差が acos(0.6) = 53.13… 度に厳密に一致し、整合の境界を突ける。
BIAS_AT_BOUNDARY_ANGLE: tuple[tuple[float, float], ...] = (
    (30.0, 40.0),
    (34.0, 43.0),
    (26.0, 37.0),
)

#: 検証レポートのばらつき（4.0 mm）より小さい平均オフセット。
#: **偏りが「認められない」**（測っていないのとは別）レポートを作る。
CAL_BIAS_SMALL_MM: list[float] = [2.0, 0.0, 0.0]

#: カメラ (0, -1500, 1000) から水平 2400 mm・高さ 1000 mm の落下地点。
#: 3次元距離はちょうど 2600 mm（帯 [2500, 3000)）だが、高さを捨てると
#: 2400 mm（帯 [2000, 2500)）になり、**別の帯に入る**。
DEPTH_SENSITIVE_IMPACT_MM = (0.0, 900.0, 0.0)

#: 横へ大きくずれた落下地点。カメラ (0, -1500, 1000) から見た水平方向は
#: (2000, 1500) を正規化した (0.8, 0.6) であり、真正面 (0, 1) から 53.13 度
#: 離れる。**投擲ごとに視線方向が変わる**入力を作るために使う。
SIDE_IMPACT_MM = (2000.0, 0.0, 0.0)

#: `BIAS_ALONG_X` などの平均まわり RMS（テスト局所の算術で組む）。
TRIPLE_SCATTER_RMS_MM = math.sqrt(50.0 / 3.0)


def camera_ray_unit_to(point_world_mm: tuple[float, float, float]) -> tuple[float, ...]:
    """カメラから対象点へ向かう単位ベクトル（World）。**テスト局所で解く。**

    `seam.camera_ray_unit()` を呼ばないのは、実装の写経にしないためである。
    """
    dx = point_world_mm[0] - CAMERA_X_MM
    dy = point_world_mm[1] - CAMERA_Y_MM
    dz = point_world_mm[2] - CAMERA_Z_MM
    norm = math.sqrt(dx * dx + dy * dy + dz * dz)
    return (dx / norm, dy / norm, dz / norm)


def spread_rays(
    base: tuple[float, ...], spread_deg: float, count: int
) -> list[tuple[float, float, float]]:
    """観測点ごとに**左右対称へずらした**視線方向を作る。

    ずらし方を ±spread の交互にしてあるので、**平均は元の向きへ厳密に戻る**
    （水平の x 成分が対をなして打ち消し合う。`count` は偶数を前提とする）。
    先頭や末尾の1件だけを見る実装は平均から spread だけ外れるので、
    **平均を採っているかどうか**がここで分かれる。

    1投擲のあいだ視線方向がまったく動かないフィクスチャでは、平均・先頭・
    末尾がすべて同値になり、取り違えが原理的に現れない（既知の空振り形1）。
    """
    if not spread_deg:
        return [(base[0], base[1], base[2])] * count
    rays: list[tuple[float, float, float]] = []
    for index in range(count):
        angle = math.radians(spread_deg) * (1.0 if index % 2 == 0 else -1.0)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        rays.append(
            (
                base[0] * cos_a - base[1] * sin_a,
                base[0] * sin_a + base[1] * cos_a,
                base[2],
            )
        )
    return rays


def make_prediction(*, residual_mm: float) -> Prediction:
    """残差だけを制御した予測。他のフィールドは帰属の判定に効かない。

    残差は**記録に残っている値をそのまま読む**ことを固定するために、
    サンプル列とは独立に置く（実装が残差を自前で計算し直すと、`prediction_core`
    の申告値ではなく自前実装の性質を見ることになる）。
    """
    trajectory = TrajectoryParameters(
        t_ref_ms=FIRST_SAMPLE_T_MS,
        x0_mm=RELEASE_X_MM,
        y0_mm=RELEASE_Y_MM,
        z0_mm=RELEASE_HEIGHT_MM,
        estimated_vx_mm_s=VX_MM_MS * 1000.0,
        estimated_vy_mm_s=VY_MM_MS * 1000.0,
        estimated_vz_mm_s=VZ_MM_MS * 1000.0,
        gravity_mm_s2=G_MM_MS2 * 1_000_000.0,
    )
    return Prediction(
        predicted_hit_x_mm=380.0,
        predicted_hit_y_mm=-96.0,
        predicted_hit_time_ms=5600.0,
        remaining_time_ms=400.0,
        estimated_vx_mm_s=VX_MM_MS * 1000.0,
        estimated_vy_mm_s=VY_MM_MS * 1000.0,
        estimated_vz_mm_s=VZ_MM_MS * 1000.0,
        residual=residual_mm,
        trajectory=trajectory,
        sample_count=SAMPLE_COUNT,
        based_on_time_ms=FIRST_SAMPLE_T_MS,
        elapsed_ms=None,
        config=CONFIG,
    )


def build_attributed_record(
    record_id: str,
    *,
    impact_point_mm: tuple[float, float, float] = NEAR_IMPACT_MM,
    residual_mm: float | None = 1.0,
    noise_sigma_mm: float = 0.0,
    sample_count: int = SAMPLE_COUNT,
    calibration_bias_mm: list[float] | None = None,
    calibration_scatter_rms_mm: float | None = CAL_SCATTER_RMS_MM,
    verified: bool = True,
    m1_extra_version: str = "1.0",
    ray_spread_deg: float = 0.0,
    with_provenance: bool = True,
    with_truth: bool = True,
) -> ThrowRecord:
    """帰属が読む材料を載せた投擲記録。

    `extra["m1"]` の形は `runner._with_m1_extra()` が書くものと同じであり、
    帰属は**記録に埋め込まれた要約からしか**検証レポートを読まない
    （上流パッケージを import しない。要件 13.1）。
    """
    ray = camera_ray_unit_to(impact_point_mm)
    predictions = () if residual_mm is None else (make_prediction(residual_mm=residual_mm),)
    payload: dict[str, object] = {
        "m1_extra_version": m1_extra_version,
        "calibration": {
            "calibration_id": CAL_ID,
            "verification_state": "passed" if verified else "not_verified",
            "verified": verified,
            "verified_at_wall_ms": 1_700_000_500_000.0,
            "bias_mm": calibration_bias_mm,
            "scatter_rms_mm": calibration_scatter_rms_mm,
            "max_error_norm_mm": 9.0,
            "point_count": 6,
            "independent_point_count": 4,
        },
        "verified": verified,
        "failed_reason": None,
    }
    if with_provenance:
        payload["provenance"] = [
            {"camera_ray_unit": list(item)}
            for item in spread_rays(ray, ray_spread_deg, sample_count)
        ]
    if with_truth:
        payload["truth"] = {
            "record_id": record_id,
            "impact_point_world_mm": {
                "value": list(impact_point_mm),
                "method": "measured",
                "uncertainty_mm": 5.0,
                "uncertainty_ms": None,
                "source": "床のマークをメジャーで実測",
            },
        }
    return ThrowRecord(
        record_id=record_id,
        source=SourceKind.LIVE,
        config=CONFIG,
        samples=build_samples(noise_sigma_mm=noise_sigma_mm, count=sample_count),
        predictions=predictions,
        extra={"m1": payload},
    )


def build_row(
    record_id: str, error_vector_mm: tuple[float, float] | None
) -> ThrowRow:
    """集計の行（帰属が record_id と誤差ベクトルの対応付けに使う）。"""
    return ThrowRow(
        record_id=record_id,
        session_id="session-a",
        source="live",
        live=True,
        truth_available=error_vector_mm is not None,
        error_vector_mm=error_vector_mm,
        values={},
    )


def build_aggregate(
    rows: Sequence[ThrowRow],
    *,
    verified: bool = True,
    provisional: bool = False,
    error_vectors: tuple[tuple[float, float], ...] | None = None,
) -> ThrowAggregate:
    """帰属の入力になる投擲群の集計（本ファイル局所の組み立て）。

    `error_vectors` を明示できるようにしてあるのは、**行と誤差ベクトル群が
    食い違う入力**（タスク4.6 が実際に空振りを起こした軸）を作れるようにする
    ためである。
    """
    derived = tuple(
        row.error_vector_mm for row in rows if row.error_vector_mm is not None
    )
    return ThrowAggregate(
        calibration_id=CAL_ID,
        verified=verified,
        session_ids=("session-a",),
        throw_count=len(rows),
        failed_throw_count=0,
        valid_throw_count=len(derived),
        live_throw_count=len(rows),
        converged_count=len(rows),
        not_converged_count=0,
        not_measurable_count=0,
        single_prediction_throw_count=0,
        provisional=provisional,
        provisional_reasons=("insufficient_valid_throws",) if provisional else (),
        items={},
        error_vectors=derived if error_vectors is None else error_vectors,
        per_throw=tuple(rows),
    )


def attribute_group(
    vectors: tuple[tuple[float, float], ...],
    *,
    settings: M1Settings,
    calibration_bias_mm: list[float] | None = CAL_BIAS_X_MM,
    calibration_scatter_rms_mm: float | None = CAL_SCATTER_RMS_MM,
    residual_mm: float | None = 1.0,
    noise_sigma_mm: float = 0.0,
    sample_count: int = SAMPLE_COUNT,
    impact_points: Sequence[tuple[float, float, float]] | None = None,
    noise_sigmas: Sequence[float] | None = None,
    residuals: Sequence[float] | None = None,
    ray_spread_deg: float = 0.0,
    verified: bool = True,
) -> AttributionResult:
    """誤差ベクトル群と記録を組み立てて帰属を求める（本ファイルの共通経路）。

    `noise_sigmas` / `residuals` は**投擲ごとに違う値**を与えるためにある。
    全投擲を同じ記録にすると、投擲群の代表値の採り方（中央値か最大か最小か）
    がどれも同じ値になり、**代表値の取り違えがまるごと素通りする**。
    """
    points = list(impact_points or [NEAR_IMPACT_MM] * len(vectors))
    sigmas = list(noise_sigmas or [noise_sigma_mm] * len(vectors))
    residual_list: list[float | None] = (
        list(residuals) if residuals is not None else [residual_mm] * len(vectors)
    )
    rows = [build_row(f"throw-{index:04d}", vector) for index, vector in enumerate(vectors)]
    records = [
        build_attributed_record(
            f"throw-{index:04d}",
            impact_point_mm=points[index],
            residual_mm=residual_list[index],
            noise_sigma_mm=sigmas[index],
            sample_count=sample_count,
            ray_spread_deg=ray_spread_deg,
            calibration_bias_mm=calibration_bias_mm,
            calibration_scatter_rms_mm=calibration_scatter_rms_mm,
            verified=verified,
        )
        for index in range(len(vectors))
    ]
    return attribute(
        build_aggregate(rows, verified=verified), records, settings=settings
    )


@pytest.fixture
def attr_settings(tmp_path: Path) -> M1Settings:
    """帰属の検査で使う設定（再抽出回数と種だけを小さく固定する）。"""
    return _resolve_settings(
        tmp_path,
        overrides={
            "bootstrap_iterations": ATTR_ITERATIONS,
            "bootstrap_seed": ATTR_SEED,
        },
    )


class TestLayoutInvariants:
    """テスト局所のリテラルが、レイアウトの値と一致していることを独立に固定する。

    期待値をレイアウトから読んで組むと**自己比較**になり、実装とフィクスチャが
    揃って壊れたときに気付けない（tasks.md「Implementation Notes」タスク4.5）。
    """

    def test_camera_position_matches_the_fixture_layout(
        self, attr_settings: M1Settings
    ) -> None:
        assert attr_settings.layout.camera_position_world_mm == (
            CAMERA_X_MM,
            CAMERA_Y_MM,
            CAMERA_Z_MM,
        )


class TestInsufficientTrials:
    """誤差ベクトルが0件の入力は**試行数不足として失敗する**（完了状態）。"""

    def test_no_error_vector_fails_as_insufficient_trials(
        self, attr_settings: M1Settings
    ) -> None:
        """帰属は「誤差が無かった」のではなく「材料が無い」——値を返さない。

        0件で `bias=(0,0)` を返すと、**偏りが無い**という積極的な結論に見える。
        """
        rows = [build_row("throw-0000", None)]
        records = [build_attributed_record("throw-0000")]
        with pytest.raises(M1ValidationError) as excinfo:
            attribute(build_aggregate(rows), records, settings=attr_settings)
        assert excinfo.value.context["reason"] == "insufficient_trials"

    def test_a_row_without_its_record_is_rejected(
        self, attr_settings: M1Settings
    ) -> None:
        """誤差ベクトルに対応する記録が無ければ、黙って飛ばさない。

        飛ばすと**カメラ視線方向も残差も距離帯も欠けたまま**判定が進み、
        「どちらとも整合しない → 判別不能」が記録の欠落によって作られる。
        """
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [build_attributed_record("throw-0000")]
        with pytest.raises(M1ConfigError):
            attribute(build_aggregate(rows), records, settings=attr_settings)

    def test_rows_and_error_vectors_must_agree(
        self, attr_settings: M1Settings
    ) -> None:
        """行の誤差ベクトルと `error_vectors` の件数が食い違う入力を拒否する。

        タスク4.6 で実際に空振りが見つかった軸である（絞り込み条件を
        「近いが別の軸」に差し替えると `error_vectors` に余分が混ざる）。
        件数がずれたまま帯や視線方向と対応付けると、**別の投擲の誤差に
        別の投擲の視線方向を当てる**ことになる。
        """
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [build_attributed_record(f"throw-{i:04d}") for i in range(3)]
        aggregate = build_aggregate(rows, error_vectors=BIAS_ALONG_X[:2])
        with pytest.raises(M1ConfigError):
            attribute(aggregate, records, settings=attr_settings)

    def test_an_unknown_extra_version_is_not_guessed(
        self, attr_settings: M1Settings
    ) -> None:
        """`extra["m1"]` の形式版が未知なら**内容を推測して読まない**。"""
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [
            build_attributed_record(f"throw-{i:04d}", m1_extra_version="9.9")
            for i in range(3)
        ]
        with pytest.raises(M1ConfigError):
            attribute(build_aggregate(rows), records, settings=attr_settings)


class TestDecomposition:
    """誤差ベクトル群を共通の偏り成分とばらつき成分へ分解する（要件 6.2）。"""

    def test_bias_is_the_component_wise_mean(
        self, attr_settings: M1Settings
    ) -> None:
        """偏りは**ベクトル**である。x と y を別々の値にして取り違えを落とす。

        期待値 (30, 0) と (0, -30) は符号も成分も違うので、成分の取り違え・
        片成分の複製 `(bx, bx)`・向きの反転がそのまま差として現れる。
        """
        along_x = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert along_x.bias.vector_mm[0] == pytest.approx(30.0)
        assert along_x.bias.vector_mm[1] == pytest.approx(0.0, abs=1e-9)

        toward_camera = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
        )
        assert toward_camera.bias.vector_mm[0] == pytest.approx(0.0, abs=1e-9)
        assert toward_camera.bias.vector_mm[1] == pytest.approx(-30.0)

    def test_bias_norm_and_scatter_use_the_population_rms(
        self, attr_settings: M1Settings
    ) -> None:
        """ばらつきは**平均まわり・分母 N**（タスク5.1 と同じ分母）。

        3件の偏差は (0,0) / (4,3) / (-4,-3) で、平方和は 50。N=3 で割って
        平方根を取ると sqrt(50/3) = 4.0824…。**N-1 で割ると 5.0** になり、
        要件 6.6 の「観測由来の範囲に収まるか」が片側へ倒れる。
        """
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert result.bias.norm_mm == pytest.approx(30.0)
        assert result.scatter.rms_mm == pytest.approx(TRIPLE_SCATTER_RMS_MM)
        assert result.scatter.rms_mm != pytest.approx(5.0)

    def test_significance_is_the_ratio_of_bias_to_scatter(
        self, attr_settings: M1Settings
    ) -> None:
        """有意性は「偏りの大きさ ÷ ばらつき」。**分子と分母を取り違えない。**

        30 / 4.0824… = 7.348…。逆にすると 0.136 であり、桁が違う。
        """
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert result.bias.significance_ratio == pytest.approx(
            30.0 / TRIPLE_SCATTER_RMS_MM
        )

    def test_a_single_error_vector_has_no_scatter(
        self, attr_settings: M1Settings
    ) -> None:
        """1件では**ばらつきが定義できない**。0.0 で埋めず欠測にする。

        0.0 を入れると比が発散し、どんな小さな偏りも「有意」になる。
        """
        result = attribute_group(((30.0, 0.0),), settings=attr_settings)
        assert result.scatter.rms_mm is None
        assert result.bias.significance_ratio is None
        assert result.bias.attribution is Attribution.UNDETERMINED
        # **判定できなかったことを「整合しなかった」と記録しない。**
        # どちらも `undetermined` なので、判定値だけでは取り違えが現れない。
        assert_only_this_rule(
            result.judgement.rationale, BIAS_SENTENCES, "not_applicable"
        )
        assert_only_this_rule(
            result.judgement.rationale, SCATTER_SENTENCES, "no_scatter"
        )


class TestBiasAttribution:
    """共通の偏りを向きで判別する（要件 6.3 / 6.4 / 6.5 / 6.10）。"""

    def test_a_bias_matching_the_report_offset_is_calibration(
        self, attr_settings: M1Settings
    ) -> None:
        """検証レポートの平均オフセット方向と整合 → キャリブレーション由来。

        レポートの偏りは +X、カメラ視線方向は +Y なので**2方向は縮退しない**。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, calibration_bias_mm=CAL_BIAS_X_MM
        )
        assert result.bias.attribution is Attribution.CALIBRATION
        assert result.bias.degenerate is False
        assert result.bias.world_fixed_agreement_deg == pytest.approx(0.0, abs=1e-6)
        assert result.bias.camera_ray_agreement_deg == pytest.approx(90.0, abs=1e-6)

    def test_a_bias_along_the_camera_ray_is_a_detection_candidate(
        self, attr_settings: M1Settings
    ) -> None:
        """カメラ視線方向に沿い、レポートに偏りが無い → 検出由来の**候補**。

        偏りは (0, -30)、カメラは対象の -Y 側にあるので視線方向は +Y である。
        **Depth は対象のカメラ側表面を測る**ので偏りはカメラへ寄る向き、
        つまり視線方向とは逆を向く。整合は**軸として**評価する——符号で
        見ると物理的に正しい向きのほうが落ちる。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
        )
        assert result.bias.attribution is Attribution.DETECTION
        assert result.bias.degenerate is False
        assert result.bias.camera_ray_agreement_deg == pytest.approx(0.0, abs=1e-6)

    def test_degenerate_directions_are_undetermined(
        self, attr_settings: M1Settings
    ) -> None:
        """レポートの偏りとカメラ視線方向が縮退したら**判別不能**（要件 6.10）。

        投擲位置が1箇所だと、World 固定方向とカメラ視線方向が一致しうる
        （research.md Decision 4 の Trade-offs）。どちらに由来するかは
        **原理的に決められない**ので、例外ではなく値として返す。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_Y_MM,
        )
        assert result.bias.degenerate is True
        assert result.bias.attribution is Attribution.UNDETERMINED

    def test_degeneracy_wins_over_the_calibration_rule(
        self, attr_settings: M1Settings
    ) -> None:
        """縮退は**規則2より先に**当たる（判定順序そのものの固定）。

        誤差の平均 (0, +30) は検証レポートの平均オフセット (0, 30, 0) と
        **角度差 0 度で整合する**——規則2 だけを見ればキャリブレーション由来
        である。しかし同じ向きはカメラ視線方向 (+Y) の軸にも乗っており、
        **どちらに由来するかは原理的に決められない**。

        `test_degenerate_directions_are_undetermined` は平均 (0, -30) を使う
        ので `world_deg = 180` になり、規則2 の分岐がそもそも成立しない
        ——**順序が働かない入力**でしか縮退を見ていないことになる。
        ここは規則2 が成立する入力で順序を固定する。

        design.md「ErrorAttributor」Risks が名指しする「投擲位置が1箇所だと
        World 固定方向とカメラ視線方向が縮退する」まさにその場合であり、
        A-9 で投擲位置を固定する本 Spec では**最も起こりやすい**。ここで
        キャリブレーション由来と断定すると、実際には検出側が原因でも
        較正をやり直しに行くことになる。
        """
        result = attribute_group(
            BIAS_AWAY_FROM_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_Y_MM,
        )
        assert result.bias.world_fixed_agreement_deg == pytest.approx(0.0, abs=1e-9)
        assert result.bias.camera_ray_agreement_deg == pytest.approx(0.0, abs=1e-9)
        assert result.bias.degenerate is True
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert_only_this_rule(
            result.judgement.rationale, BIAS_SENTENCES, "degenerate"
        )

    def test_degeneracy_holds_for_both_orientations_of_the_axis(
        self, attr_settings: M1Settings
    ) -> None:
        """縮退は**軸**の共有で決まる（向きが逆でも縮退している）。

        検証レポートの平均オフセット (0, -30, 0) はカメラ視線方向 (+Y) と
        同じ軸に乗っており、向きだけが逆である。較正のずれが対象から見て
        カメラ側を向いていれば起こる配置であり、**World 固定方向とカメラ
        視線方向はやはり区別できない**。

        `test_degeneracy_wins_over_the_calibration_rule` はレポート偏りが
        視線と**同じ向き**の場合を見ている。この2件は対であり、縮退の判定を
        符号付きの角度差に変えた実装は**半分の場合をすり抜ける**——ここでは
        `world_deg = 0` なので規則2 が成立し、`calibration` と断定される。
        実害は、**検出側が原因でも較正をやり直しに行く**ことである（要件
        6.10 / design.md「ErrorAttributor」Risks）。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_NEG_Y_MM,
        )
        assert result.bias.world_fixed_agreement_deg == pytest.approx(0.0, abs=1e-9)
        assert result.bias.camera_ray_agreement_deg == pytest.approx(0.0, abs=1e-9)
        assert result.bias.degenerate is True
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert_only_this_rule(
            result.judgement.rationale, BIAS_SENTENCES, "degenerate"
        )

    def test_the_camera_ray_is_averaged_over_the_observed_points(
        self, attr_settings: M1Settings
    ) -> None:
        """カメラ視線方向は**その投擲の観測点すべての平均**である。

        観測点ごとの視線を ±40 度へ交互にずらすと、平均は元の向き（+Y）へ
        厳密に戻るが、**先頭や末尾の1件だけを見る実装は 40 度外れる**。
        整合とみなす角度差は 30 度なので、平均を採らない実装はここで
        検出由来の候補を落とす。

        1投擲内で視線方向がまったく動かないフィクスチャだと、平均・先頭・
        末尾がすべて同値になり、この取り違えは原理的に現れない
        （既知の空振り形1）。実際の投擲では対象が動くので視線は動く。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
            ray_spread_deg=40.0,
        )
        assert result.bias.camera_ray_agreement_deg == pytest.approx(0.0, abs=1e-9)
        assert result.bias.attribution is Attribution.DETECTION

    def test_a_bias_matching_neither_direction_is_undetermined(
        self, attr_settings: M1Settings
    ) -> None:
        """どちらとも整合しない偏りは判別不能。**無理に割り当てない。**

        偏り (30, 30) は +X からも +Y からも 45 度離れており、整合とみなす
        角度差 30 度を超える。
        """
        result = attribute_group(
            BIAS_DIAGONAL, settings=attr_settings, calibration_bias_mm=CAL_BIAS_X_MM
        )
        assert result.bias.degenerate is False
        assert result.bias.attribution is Attribution.UNDETERMINED
        assert result.bias.world_fixed_agreement_deg == pytest.approx(45.0, abs=1e-6)
        assert result.bias.camera_ray_agreement_deg == pytest.approx(45.0, abs=1e-6)

    def test_an_insignificant_bias_is_reported_as_none(
        self, attr_settings: M1Settings
    ) -> None:
        """偏りがばらつきに比べて小さければ「偏り成分なし」。

        平均 (1, 0) に対しばらつきは 15.81 であり、比は 0.063。閾値 1.0 を
        下回るので**有意でない**。判別不能（決めきれない）とは別の結果である。
        """
        result = attribute_group(BIAS_INSIGNIFICANT, settings=attr_settings)
        assert result.bias.attribution is Attribution.NONE
        assert result.bias.significance_ratio == pytest.approx(
            1.0 / math.sqrt(1000.0 / 4.0)
        )

    def test_the_significance_threshold_is_actually_applied(
        self, attr_settings: M1Settings, tmp_path: Path
    ) -> None:
        """閾値を上げれば同じ入力が「偏り成分なし」へ変わる。

        閾値を無視して常に有意とする実装・常に有意でないとする実装を、
        **同じ入力の判定が閾値で動くこと**で落とす。比は 7.348 なので、
        閾値 7.0 では有意、8.0 では有意でない。
        """
        strict = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "bias_significance_ratio": 8.0,
            },
        )
        loose = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "bias_significance_ratio": 7.0,
            },
        )
        assert (
            attribute_group(BIAS_ALONG_X, settings=strict).bias.attribution
            is Attribution.NONE
        )
        assert (
            attribute_group(BIAS_ALONG_X, settings=loose).bias.attribution
            is Attribution.CALIBRATION
        )

    def test_the_direction_threshold_is_actually_applied(
        self, attr_settings: M1Settings, tmp_path: Path
    ) -> None:
        """向きの角度差の閾値を広げれば、45 度離れた偏りも整合とみなされる。

        閾値を無視する実装（常に整合 / 常に不整合）をここで落とす。
        """
        wide = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "direction_agreement_deg": 50.0,
            },
        )
        assert (
            attribute_group(
                BIAS_DIAGONAL, settings=attr_settings, calibration_bias_mm=CAL_BIAS_X_MM
            ).bias.attribution
            is Attribution.UNDETERMINED
        )
        assert (
            attribute_group(
                BIAS_DIAGONAL, settings=wide, calibration_bias_mm=CAL_BIAS_X_MM
            ).bias.attribution
            is Attribution.CALIBRATION
        )

    def test_a_report_without_a_bias_cannot_be_the_calibration(
        self, attr_settings: M1Settings
    ) -> None:
        """レポートの偏りが**未実施（欠測）**なら、方向の整合は測れない。

        `None` を (0,0) と同一視して「整合した」ことにすると、
        **測っていない**ことが根拠になってしまう。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, calibration_bias_mm=None
        )
        assert result.bias.world_fixed_agreement_deg is None
        assert result.bias.attribution is Attribution.UNDETERMINED


    def test_the_significance_boundary_is_inclusive(self, tmp_path: Path) -> None:
        """比が**ちょうど閾値どおり**なら有意側に含める（境界の向きを固定する）。

        2件の誤差ベクトル (45,0) / (15,0) は平均 (30,0)、平均まわり・分母 N の
        RMS がちょうど 15.0 になる（どちらも浮動小数で厳密に表せる値である）。
        閾値 2.0 に対し比はちょうど 2.0 であり、`>=` なら有意、`>` なら
        「偏り成分なし」になる。**境界の向きは規則の一部**である。
        """
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "bias_significance_ratio": 2.0,
            },
        )
        result = attribute_group(BIAS_BOUNDARY, settings=settings)
        assert result.scatter.rms_mm == pytest.approx(15.0)
        assert result.bias.significance_ratio == pytest.approx(2.0)
        assert result.bias.attribution is Attribution.CALIBRATION

    def test_a_bias_opposite_to_the_report_offset_is_not_the_calibration(
        self, attr_settings: M1Settings
    ) -> None:
        """レポートの平均オフセットとは**符号付きの向き**で比べる。

        較正のずれは World 上で符号を保つ——レポートが +X のずれを報告して
        いるのに誤差の偏りが -X なら、それは同じずれでは説明できない。
        軸として（同じ向きと逆向きを区別せずに）比べる実装は、ここで
        **真逆の偏りをキャリブレーション由来と誤断定する**。
        """
        result = attribute_group(
            BIAS_AGAINST_REPORT,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_X_MM,
        )
        assert result.bias.world_fixed_agreement_deg == pytest.approx(180.0)
        assert result.bias.attribution is Attribution.UNDETERMINED

    def test_the_camera_ray_must_agree_consistently_across_throws(
        self, attr_settings: M1Settings
    ) -> None:
        """カメラ視線方向との整合は**すべての投擲に対して**成り立つこと。

        要件 6.5 の「一貫して整合」は、最も外れた投擲でも閾値内という意味で
        ある。投擲2件は真正面 (0, 1) だが1件は横 (0.8, 0.6) を向いており、
        偏り (0, -30) との軸の角度差は 53.13 度で閾値 30 度を超える。
        **最小値を採る実装**（1件でも整合すれば良しとする）はここで落ちる。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
            impact_points=[NEAR_IMPACT_MM, NEAR_IMPACT_MM, SIDE_IMPACT_MM],
        )
        assert result.bias.camera_ray_agreement_deg == pytest.approx(
            math.degrees(math.acos(0.6))
        )
        assert result.bias.attribution is Attribution.UNDETERMINED


    def test_the_direction_boundary_is_inclusive(self, tmp_path: Path) -> None:
        """角度差が**ちょうど閾値どおり**なら整合とみなす（境界の向きを固定）。

        偏りの平均 (30, 40) は大きさ 50 ちょうどで、単位ベクトルが (0.6, 0.8)
        に厳密へ落ちる。検証レポートの +X 方向との内積はちょうど 0.6 なので、
        角度差は acos(0.6) = 53.13… 度に**厳密に**一致する。閾値をその値
        ちょうどに置くと、`<=` なら整合（キャリブレーション由来）、`<` なら
        不整合（判別不能）になる。
        """
        boundary_deg = math.degrees(math.acos(0.6))
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "direction_agreement_deg": boundary_deg,
            },
        )
        result = attribute_group(
            BIAS_AT_BOUNDARY_ANGLE,
            settings=settings,
            calibration_bias_mm=CAL_BIAS_X_MM,
        )
        assert result.bias.world_fixed_agreement_deg == boundary_deg
        assert result.bias.attribution is Attribution.CALIBRATION

    def test_a_report_bias_below_its_own_scatter_is_not_recognised(
        self, attr_settings: M1Settings
    ) -> None:
        """レポートの偏りが「認められる」かは**レポート自身のばらつき**と比べる。

        平均オフセット 2.0 mm はレポートのばらつき 4.0 mm より小さいので、
        偏りは認められない——だからカメラ視線方向に沿う偏りを検出由来の候補
        として報告できる（要件 6.5）。**絶対値の目標を置かず**、記録された値
        どうしの相対比較で決めることをここで固定する。0 でなければ何でも
        「偏りあり」とみなす実装は、ここで規則3を殺してしまう。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_SMALL_MM,
            calibration_scatter_rms_mm=CAL_SCATTER_RMS_MM,
        )
        assert result.bias.attribution is Attribution.DETECTION

    def test_an_unmeasured_report_bias_is_not_treated_as_no_bias(
        self, attr_settings: M1Settings
    ) -> None:
        """**測っていない**ことを「偏りが認められない」の根拠にしない。

        要件 6.5 の規則3は「キャリブレーション検証では偏りが認められない」
        ことを条件にしている。検証を実施していない（レポートに平均オフセット
        が無い）群でこれを満たしたことにすると、**検証していないだけの群が
        まるごと検出由来へ倒れる**——そして検出側の改善に誤って時間を使う。
        偏りがカメラ視線方向に沿っていても、判別不能である。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=None,
        )
        assert result.bias.camera_ray_agreement_deg == pytest.approx(0.0, abs=1e-6)
        assert result.bias.attribution is Attribution.UNDETERMINED


    def test_the_camera_direction_boundary_is_inclusive(
        self, tmp_path: Path
    ) -> None:
        """カメラ視線方向との角度差も**ちょうど閾値どおり**なら整合とみなす。

        偏りの単位ベクトルは (0.6, 0.8)、カメラ視線方向の水平成分は
        (0.0, 1.0) に厳密へ落ちるので、内積はちょうど 0.8、角度差は
        acos(0.8) = 36.87… 度に厳密に一致する。境界の向きは規則の一部であり、
        偏り側（規則1）とカメラ側（規則3）で別々に固定する必要がある
        ——片方だけ直した実装がもう片方で生き残る。
        """
        boundary_deg = math.degrees(math.acos(0.8))
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "direction_agreement_deg": boundary_deg,
            },
        )
        result = attribute_group(
            BIAS_AT_BOUNDARY_ANGLE,
            settings=settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
        )
        assert result.bias.camera_ray_agreement_deg == boundary_deg
        assert result.bias.attribution is Attribution.DETECTION

    def test_a_negligible_report_bias_cannot_be_degenerate(
        self, attr_settings: M1Settings
    ) -> None:
        """**認められない偏りとは縮退しようがない**（要件 6.10）。

        レポートの平均オフセット 2.0 mm はレポート自身のばらつき 4.0 mm より
        小さく、World 固定の向きとして採用できる量ではない。それがたまたま
        カメラ視線方向と同じ軸に乗っているだけで「2方向が縮退した」と
        報告すると、**判別できたはずの検出由来の候補が判別不能へ落ちる**。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_SMALL_Y_MM,
        )
        assert result.bias.degenerate is False
        assert result.bias.attribution is Attribution.DETECTION

    def test_a_group_without_any_error_has_no_bias_component(
        self, attr_settings: M1Settings
    ) -> None:
        """誤差が 0 の投擲群は「偏り成分なし」であって判別不能ではない。

        偏りもばらつきも 0 のとき「大きさ 0 の偏りを有意としない」規則を
        外すと、`0 >= 倍率 × 0` が成り立って**有意な偏りがある**ことになり、
        向きが定まらないまま判別不能へ落ちる。**誤差が無い群を「原因を
        決めきれない群」と報告しない。**
        """
        result = attribute_group(ZERO_ERRORS, settings=attr_settings)
        assert result.bias.norm_mm == 0.0
        assert result.bias.attribution is Attribution.NONE


class TestScatterAttribution:
    """ばらつき成分を観測由来かモデル由来かへ分ける（要件 6.6 / 6.7 / 6.8）。"""

    def test_scatter_within_the_bootstrap_range_is_observation_noise(
        self, attr_settings: M1Settings
    ) -> None:
        """再抽出で見積もった範囲に収まるばらつきは観測ノイズ由来。

        観測に σ=2 mm のノイズを載せた記録では、再抽出による予測ばらつきは
        10〜30 mm の帯に入る（実測 18.8 mm）。投擲群のばらつき 4.08 mm は
        その内側である。**帯は本ファイルのリテラル**であり、実装の出力を
        許容差に使わない（タスク4.1 の教訓）。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        assert result.scatter.bootstrap_rms_mm is not None
        assert 10.0 < result.scatter.bootstrap_rms_mm < 30.0
        assert result.scatter.attribution is Attribution.OBSERVATION_NOISE

    def test_scatter_beyond_the_range_with_a_large_residual_is_the_model(
        self, attr_settings: M1Settings
    ) -> None:
        """範囲を超え、かつ残差が大きければモデル由来（予測）。

        ノイズ 0 の観測では再抽出のばらつきが浮動小数の桁（1e-6 mm 未満）
        まで落ちるので、4.08 mm のばらつきは範囲を明確に超える。残差
        8.0 mm はその範囲より大きい。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=8.0
        )
        assert result.scatter.bootstrap_rms_mm is not None
        assert result.scatter.bootstrap_rms_mm < 1e-6
        assert result.scatter.residual_median_mm == pytest.approx(8.0)
        assert result.scatter.attribution is Attribution.PREDICTION

    def test_scatter_beyond_the_range_with_a_small_residual_is_undetermined(
        self, attr_settings: M1Settings
    ) -> None:
        """範囲を超えるが残差が小さければ判別不能（規則7）。

        観測ノイズ σ=2 mm の記録（再抽出のばらつき 10〜30 mm）に対し、
        投擲群のばらつきは 163.3 mm で範囲を超える。一方で残差 1.0 mm は
        その範囲より小さい。**モデル由来と断定しない。**
        """
        result = attribute_group(
            SCATTER_ONLY,
            settings=attr_settings,
            noise_sigma_mm=LOW_NOISE_MM,
            residual_mm=1.0,
        )
        assert result.scatter.rms_mm == pytest.approx(math.sqrt(80000.0 / 3.0))
        assert result.scatter.attribution is Attribution.UNDETERMINED

    def test_an_unavailable_bootstrap_is_not_filled_with_zero(
        self, attr_settings: M1Settings
    ) -> None:
        """再抽出で見積もれなければ、ばらつき成分は判別不能である。

        `0.0` で埋めると観測由来の範囲が空になり、**すべてのばらつきが
        モデル由来へ倒れる**（タスク5.1 の `rms_mm: float | None`）。
        サンプル2件の投擲では再予測が1件も成立しない。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, sample_count=2
        )
        assert result.scatter.bootstrap_rms_mm is None
        assert result.scatter.attribution is Attribution.UNDETERMINED
        assert_only_this_rule(
            result.judgement.rationale, SCATTER_SENTENCES, "no_bootstrap"
        )

    def test_a_missing_residual_is_not_filled_with_zero(
        self, attr_settings: M1Settings
    ) -> None:
        """残差の記録が1件も無ければ、大小を比べずに判別不能とする。

        0.0 を入れると「残差が小さい」と読まれ、**モデル由来を見落とす**。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=None
        )
        assert result.scatter.residual_median_mm is None
        assert result.scatter.attribution is Attribution.UNDETERMINED
        assert_only_this_rule(
            result.judgement.rationale, SCATTER_SENTENCES, "no_residual"
        )

    def test_the_residual_threshold_is_actually_applied(
        self, attr_settings: M1Settings, tmp_path: Path
    ) -> None:
        """残差の閾値を上げれば、同じ入力がモデル由来から判別不能へ変わる。

        再抽出のばらつきは 10〜30 mm（σ=2 mm の観測）。残差 40 mm は倍率
        1.0 では「大きい」が、倍率 4.0 では 40〜120 mm が要求されるので
        「大きくない」側に落ちる。
        """
        strict = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "residual_significance_ratio": 4.0,
            },
        )
        assert (
            attribute_group(
                SCATTER_ONLY,
                settings=attr_settings,
                noise_sigma_mm=LOW_NOISE_MM,
                residual_mm=40.0,
            ).scatter.attribution
            is Attribution.PREDICTION
        )
        assert (
            attribute_group(
                SCATTER_ONLY,
                settings=strict,
                noise_sigma_mm=LOW_NOISE_MM,
                residual_mm=40.0,
            ).scatter.attribution
            is Attribution.UNDETERMINED
        )

    def test_the_bootstrap_representative_is_the_median(
        self, attr_settings: M1Settings
    ) -> None:
        """再抽出の代表値は投擲群の**中央値**である（最大でも最小でもない）。

        全投擲を同じ記録にすると3つの代表値が一致してしまい、取り違えが
        素通りする。ここでは**投擲ごとにノイズ量を変える**。

        - ノイズ (0, 0, 2 mm): 見積もりは (≈0, ≈0, ≈20)。中央値は ≈0 なので
          ばらつき 4.08 mm は範囲を超え、残差 1.0 mm がそれを上回って
          モデル由来になる。**最大**を採ると ≈20 になり観測ノイズ由来へ倒れる。
        - ノイズ (0, 2, 2 mm): 見積もりは (≈0, ≈20, ≈20)。中央値は ≈20 なので
          観測ノイズ由来。**最小**を採ると ≈0 になりモデル由来へ倒れる。
        """
        low_median = attribute_group(
            BIAS_ALONG_X,
            settings=attr_settings,
            noise_sigmas=[0.0, 0.0, LOW_NOISE_MM],
        )
        assert low_median.scatter.bootstrap_rms_mm is not None
        assert low_median.scatter.bootstrap_rms_mm < 1e-6
        assert low_median.scatter.attribution is Attribution.PREDICTION

        high_median = attribute_group(
            BIAS_ALONG_X,
            settings=attr_settings,
            noise_sigmas=[0.0, LOW_NOISE_MM, LOW_NOISE_MM],
        )
        assert high_median.scatter.bootstrap_rms_mm is not None
        assert 10.0 < high_median.scatter.bootstrap_rms_mm < 30.0
        assert high_median.scatter.attribution is Attribution.OBSERVATION_NOISE

    def test_the_residual_representative_is_the_median(
        self, attr_settings: M1Settings
    ) -> None:
        """残差の代表値も投擲群の**中央値**である（最大でも最小でもない）。

        再抽出の見積もりは 10〜30 mm（σ=2 mm の観測）で全投擲そろえてある。

        - 残差 (1, 40, 40) mm: 中央値 40 は見積もりを上回りモデル由来。
          **最小**を採ると 1 になり判別不能へ落ちる。
        - 残差 (1, 1, 40) mm: 中央値 1 は下回るので判別不能。
          **最大**を採ると 40 になりモデル由来へ倒れる。
        """
        large = attribute_group(
            SCATTER_ONLY,
            settings=attr_settings,
            noise_sigma_mm=LOW_NOISE_MM,
            residuals=[1.0, 40.0, 40.0],
        )
        assert large.scatter.residual_median_mm == pytest.approx(40.0)
        assert large.scatter.attribution is Attribution.PREDICTION

        small = attribute_group(
            SCATTER_ONLY,
            settings=attr_settings,
            noise_sigma_mm=LOW_NOISE_MM,
            residuals=[1.0, 1.0, 40.0],
        )
        assert small.scatter.residual_median_mm == pytest.approx(1.0)
        assert small.scatter.attribution is Attribution.UNDETERMINED

    def test_bias_and_scatter_are_judged_independently(
        self, attr_settings: M1Settings
    ) -> None:
        """偏りとばらつきは**独立に判定する**（design.md 流れ上の決定）。

        同じ誤差ベクトル群でも、記録の観測ノイズだけを変えるとばらつき成分
        の判定が動き、偏り成分の判定は動かない。**2つを返り値ごと取り違えた
        実装**（偏りとばらつきの入れ替え）はここで落ちる。
        """
        quiet = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=8.0
        )
        noisy = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        assert quiet.bias.attribution is Attribution.CALIBRATION
        assert noisy.bias.attribution is Attribution.CALIBRATION
        assert quiet.scatter.attribution is Attribution.PREDICTION
        assert noisy.scatter.attribution is Attribution.OBSERVATION_NOISE


class TestRangeBands:
    """距離帯ごとの誤差を提示する（要件 6.11）。"""

    def test_errors_are_split_into_camera_range_bands(
        self, attr_settings: M1Settings
    ) -> None:
        """遠方でのみ誤差が大きい場合を読み分けられるようにする。

        カメラは (0, -1500, 1000)。落下地点 (0,0,0) までは 1802.8 mm で
        帯 [1500, 2000)、(0,1500,0) までは 3162.3 mm で帯 [3000, 3500)。
        **全誤差を1つの帯へ入れる実装**も、**帯の境界を変える実装**も、
        ここで落ちる。
        """
        vectors = ((10.0, 0.0), (14.0, 0.0), (100.0, 0.0), (104.0, 0.0))
        points = [NEAR_IMPACT_MM, NEAR_IMPACT_MM, FAR_IMPACT_MM, FAR_IMPACT_MM]
        result = attribute_group(
            vectors, settings=attr_settings, impact_points=points
        )
        assert [
            (band.range_lo_mm, band.range_hi_mm, band.throw_count)
            for band in result.range_bands
        ] == [(1500.0, 2000.0, 2), (3000.0, 3500.0, 2)]
        assert result.range_bands[0].mean_error_norm_mm == pytest.approx(12.0)
        assert result.range_bands[1].mean_error_norm_mm == pytest.approx(102.0)

    def test_the_range_is_the_three_dimensional_distance(
        self, attr_settings: M1Settings
    ) -> None:
        """距離は**カメラとの3次元距離**である（高さの差を捨てない）。

        カメラは床から 1000 mm 高いところにある。落下地点 (0, 900, 0) までの
        水平距離は 2400 mm だが、3次元距離はちょうど 2600 mm である。
        高さを捨てる実装は帯 [2000, 2500) へ入れてしまい、**奥行き計測の
        距離特性を読み分ける**という要件 6.11 の目的から外れる
        （Depth の誤差はセンサからの距離で効くのであって、床上の投影距離で
        効くのではない）。
        """
        vectors = ((10.0, 0.0), (14.0, 0.0))
        result = attribute_group(
            vectors,
            settings=attr_settings,
            impact_points=[DEPTH_SENSITIVE_IMPACT_MM, DEPTH_SENSITIVE_IMPACT_MM],
        )
        assert [
            (band.range_lo_mm, band.range_hi_mm) for band in result.range_bands
        ] == [(2500.0, 3000.0)]

    def test_the_band_width_is_configurable(
        self, tmp_path: Path
    ) -> None:
        """帯域の幅は設定であり、コードに埋め込まれていない（要件 13.5）。

        幅 1000 mm では 1802.8 mm と 3162.3 mm が [1000,2000) と [3000,4000)
        になる。幅を無視する実装をここで落とす。
        """
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "range_band_mm": 1000.0,
            },
        )
        vectors = ((10.0, 0.0), (14.0, 0.0), (100.0, 0.0))
        points = [NEAR_IMPACT_MM, NEAR_IMPACT_MM, FAR_IMPACT_MM]
        result = attribute_group(vectors, settings=settings, impact_points=points)
        assert [
            (band.range_lo_mm, band.range_hi_mm) for band in result.range_bands
        ] == [(1000.0, 2000.0), (3000.0, 4000.0)]

    def test_throws_without_a_truth_impact_point_have_no_band(
        self, attr_settings: M1Settings
    ) -> None:
        """落下地点の真値が無ければ距離が分からない。**0 mm の帯に入れない。**

        入れると最も近い帯の平均誤差が汚れ、「遠方でだけ悪い」という読みが
        壊れる。
        """
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [
            build_attributed_record(f"throw-{i:04d}", with_truth=(i != 0))
            for i in range(3)
        ]
        result = attribute(
            build_aggregate(rows), records, settings=attr_settings
        )
        assert [band.throw_count for band in result.range_bands] == [2]


class TestBreakdownIsNeverFolded:
    """**合計誤差の単一値を返さず、常に成分ごとの内訳を返す**（要件 6.9）。"""

    def test_the_result_exposes_components_not_a_total(self) -> None:
        """公開フィールドの並びを固定する。合計値のフィールドを増やさない。"""
        names = tuple(field.name for field in dataclasses.fields(AttributionResult))
        assert names == (
            "bias",
            "scatter",
            "range_bands",
            "calibration_reference",
            "judgement",
        )

    def test_components_are_separate_objects(
        self, attr_settings: M1Settings
    ) -> None:
        """偏り成分とばらつき成分は別の型で、別の帰属先を持てる。"""
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=8.0
        )
        assert isinstance(result.bias, BiasComponent)
        assert isinstance(result.scatter, ScatterComponent)
        assert result.bias.attribution is not result.scatter.attribution


class TestCalibrationReference:
    """検証レポートの値を**記録に埋め込まれた要約から**取り込む（要件 2.4）。"""

    def test_the_report_values_are_carried_into_the_result(
        self, attr_settings: M1Settings
    ) -> None:
        """平均オフセット・ばらつき・距離帯別誤差の材料を結果に残す。

        取り込んだ値を残さないと、**あとから「何と突き合わせたのか」が
        分からない**。期待値は本ファイルのリテラルである。
        """
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert result.calibration_reference["bias_mm"] == [30.0, 0.0, 0.0]
        assert result.calibration_reference["scatter_rms_mm"] == 4.0
        assert result.calibration_reference["calibration_id"] == CAL_ID

    def test_records_with_disagreeing_reports_are_rejected(
        self, attr_settings: M1Settings
    ) -> None:
        """同じ群の記録が違う検証レポートを載せていたら混ぜない（要件 2.5）。"""
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [
            build_attributed_record(
                f"throw-{i:04d}",
                calibration_bias_mm=(CAL_BIAS_X_MM if i == 0 else CAL_BIAS_Y_MM),
            )
            for i in range(3)
        ]
        with pytest.raises(M1ConfigError):
            attribute(build_aggregate(rows), records, settings=attr_settings)

    def test_the_reference_is_copied_not_shared(
        self, attr_settings: M1Settings
    ) -> None:
        """取り込んだ要約は**複製して**持つ（記録側と共有しない）。

        記録のマッピングを抱えたままだと、レポートに出る「突き合わせた値」が
        算出時のものと食い違い得る（`Judgement.evidence` と同じ方針）。
        **同じ記録から2回求める**ことで、共有していれば1回目の書き換えが
        2回目に現れる。
        """
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [
            build_attributed_record(
                f"throw-{i:04d}", calibration_bias_mm=CAL_BIAS_X_MM
            )
            for i in range(3)
        ]
        aggregate = build_aggregate(rows)

        first = attribute(aggregate, records, settings=attr_settings)
        assert isinstance(first.calibration_reference, dict)
        first.calibration_reference["bias_mm"] = "壊した"  # type: ignore[index]

        again = attribute(aggregate, records, settings=attr_settings)
        assert again.calibration_reference["bias_mm"] == [30.0, 0.0, 0.0]


class TestCriterionText:
    """**判定規則の説明文を結果に埋め込む**（要件 6.1）。

    タスク4.4 の教訓: 振る舞いを固定しても**記録される規則の文面**が未固定
    だと、単語を個別に `in` で見るテストは一文を削っても通る。ここでは
    **識別可能な一文**をリテラルで固定し、規則の別は**相互排他**で押さえる。
    """

    def test_the_judgement_carries_a_non_empty_criterion(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert result.judgement.question == "attribution"
        assert result.judgement.criterion.strip() != ""

    def test_the_criterion_states_each_rule(
        self, attr_settings: M1Settings
    ) -> None:
        """7つの規則それぞれの一文を固定する（一文を削ると落ちる）。"""
        criterion = attribute_group(BIAS_ALONG_X, settings=attr_settings).judgement.criterion
        assert "共通偏りの大きさがばらつきの" in criterion
        assert "検証レポートの平均オフセット方向と整合するなら" in criterion
        assert "カメラ視線方向の軸に沿い、かつ検証レポートで偏りが認められないなら" in criterion
        assert "両者が縮退して区別できないなら判別不能とする" in criterion
        assert "再抽出で見積もった予測ばらつきの範囲内なら観測ノイズ由来とする" in criterion
        assert "範囲を超え、かつフィットの残差代表値が" in criterion
        assert "範囲を超えるが残差が大きくないなら判別不能とする" in criterion

    def test_the_criterion_states_how_the_report_bias_is_recognised(
        self, attr_settings: M1Settings
    ) -> None:
        """「偏りが認められる」の定義と、測っていない場合の扱いを残す。

        この一文が無いと、規則3の条件が読み手ごとに別の意味になる。
        """
        criterion = attribute_group(
            BIAS_ALONG_X, settings=attr_settings
        ).judgement.criterion
        assert "大きさがレポートのばらつきの" in criterion
        assert (
            "平均オフセットが記録されていない（検証を実施していない）場合は"
            "「認められない」とも言えないので、規則3は適用しない"
        ) in criterion

    def test_the_criterion_states_the_sign_conventions(
        self, attr_settings: M1Settings
    ) -> None:
        """符号の扱いは規則の一部である（読み手が再現できなければ意味がない）。

        レポートの偏りは**符号付き**で、カメラ視線方向は**軸として**比べる。
        この一文を落とすと、同じ文面で逆の判定が正当化できる。
        """
        criterion = attribute_group(BIAS_ALONG_X, settings=attr_settings).judgement.criterion
        assert (
            "検証レポートの平均オフセットとは符号付きの向きで比べ、"
            "カメラ視線方向とは軸（同じ向きと逆向きを区別しない）として比べる"
        ) in criterion

    def test_the_criterion_states_that_undetermined_is_a_normal_result(
        self, attr_settings: M1Settings
    ) -> None:
        criterion = attribute_group(BIAS_ALONG_X, settings=attr_settings).judgement.criterion
        assert "判別不能は異常ではなく正常な結果である" in criterion
        assert "合計誤差の単一値へ畳まない" in criterion
        assert "絶対値の目標を置かず、同一測定内の量どうしの相対比較で定義する" in criterion

    def test_the_criterion_records_the_thresholds_actually_used(
        self, tmp_path: Path
    ) -> None:
        """規則の文だけを残して閾値を伏せると、同じ文で違う判定が正当化できる。"""
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED,
                "bias_significance_ratio": 2.5,
                "direction_agreement_deg": 17.0,
                "residual_significance_ratio": 3.5,
                "range_band_mm": 750.0,
            },
        )
        criterion = attribute_group(BIAS_ALONG_X, settings=settings).judgement.criterion

        # どの閾値が**どの規則に**入ったかを一文まるごとで固定する。
        # 数字を個別に `in` で見るだけだと、規則1に残差の倍率が、規則6に
        # 偏りの倍率が入っていても通ってしまう（既知の空振り形2）。
        assert "共通偏りの大きさがばらつきの 2.5 倍に満たないなら" in criterion
        assert "整合するなら（角度差 17 度以内）キャリブレーション由来とする" in criterion
        assert "その見積もりの 3.5 倍以上ならモデル由来（予測）とする" in criterion
        assert "大きさがレポートのばらつきの 2.5 倍以上であることをいう" in criterion
        assert "距離を 750 mm 幅で区切り" in criterion

        # **取り違えた組み合わせが現れないこと**を相互排他で押さえる
        # （`TestRationaleNamesTheAppliedRule` と同じ形）。4つの閾値を
        # すべて違う値にしてあるので、どの2つを入れ替えてもここで落ちる。
        assert "共通偏りの大きさがばらつきの 3.5 倍に満たないなら" not in criterion
        assert "その見積もりの 2.5 倍以上ならモデル由来（予測）とする" not in criterion
        assert "大きさがレポートのばらつきの 3.5 倍以上であることをいう" not in criterion
        assert "角度差 750 度以内" not in criterion
        assert "距離を 17 mm 幅で区切り" not in criterion

    def test_the_criterion_is_the_same_text_whatever_the_verdict(
        self, attr_settings: M1Settings
    ) -> None:
        """規則は**実測前に固定**されている——結果によって文面が変わらない。

        判定に合わせて規則が動くなら、それは規則ではない（要件 6.1）。
        """
        calibration = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        detection = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
        )
        assert calibration.judgement.criterion == detection.judgement.criterion


#: 偏り成分の判定ごとに記録されるべき一文（**テスト局所のリテラル**）。
BIAS_SENTENCES: dict[str, str] = {
    "none": "共通の偏りは有意でない（規則1）。",
    "calibration": "共通の偏りは検証レポートの平均オフセット方向と整合する（規則2）。",
    "detection": (
        "共通の偏りはカメラ視線方向の軸に沿い、"
        "検証レポートでは偏りが認められない（規則3）。"
    ),
    "degenerate": (
        "検証レポートの平均オフセット方向とカメラ視線方向が縮退しており、"
        "向きで区別できない（規則4）。"
    ),
    "mismatch": "共通の偏りはどちらの向きとも整合しない（規則4）。",
    "not_applicable": (
        "ばらつきが見積もれず、共通の偏りの有意性を判定できない（規則1の適用不能）。"
    ),
}

#: ばらつき成分の判定ごとに記録されるべき一文（**テスト局所のリテラル**）。
SCATTER_SENTENCES: dict[str, str] = {
    "observation_noise": (
        "ばらつきは再抽出で見積もった観測由来の範囲に収まる（規則5）。"
    ),
    "prediction": (
        "ばらつきが観測由来の範囲を超え、フィットの残差も大きい（規則6）。"
    ),
    "small_residual": (
        "ばらつきは観測由来の範囲を超えるが、フィットの残差は大きくない（規則7）。"
    ),
    "no_scatter": (
        "投擲群のばらつきを算出できない（有効な誤差ベクトルが2件未満）。"
    ),
    "no_bootstrap": (
        "観測由来のばらつきを見積もれず、ばらつき成分を判定できない"
        "（規則5〜7の適用不能）。"
    ),
    "no_residual": (
        "フィットの残差が記録されておらず、ばらつき成分を判定できない"
        "（規則6・7の適用不能）。"
    ),
}


def assert_only_this_rule(
    rationale: str, sentences: dict[str, str], key: str
) -> None:
    """記録された規則が**その一文だけ**であることを相互排他で固定する。

    「別の判定なら別の文になる」だけでは、2つの規則を入れ替えた実装が素通り
    する（入れ替えても「別」のままだから）。

    **規則が「適用不能」だった場合の文も辞書に入れてある。** 判定値はどれも
    `undetermined` なので verdict では区別が付かないが、「どちらの向きとも
    整合しない」と「そもそも判定できなかった」は読み手にとって**別の診断**
    である。後者を前者として記録すると、**存在しない不整合を追いかける**
    ことになる。
    """
    assert sentences[key] in rationale
    for other, sentence in sentences.items():
        if other != key:
            assert sentence not in rationale


class TestRationaleNamesTheAppliedRule:
    """**適用した規則と記録した規則を取り違えない**（タスク4.4 の最危険形）。

    各判定について**自分の一文を含み、他の判定の一文を含まない**ことを
    固定する。
    """

    def _assert_only(self, rationale: str, sentences: dict[str, str], key: str) -> None:
        assert_only_this_rule(rationale, sentences, key)

    def test_calibration_rule_is_recorded_as_rule_two(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        self._assert_only(
            result.judgement.rationale, BIAS_SENTENCES, "calibration"
        )

    def test_detection_rule_is_recorded_as_rule_three(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
        )
        self._assert_only(result.judgement.rationale, BIAS_SENTENCES, "detection")

    def test_degeneracy_is_recorded_as_degeneracy(
        self, attr_settings: M1Settings
    ) -> None:
        """「どちらとも整合しない」と「縮退」は**別の理由**である。

        直し方が違う——前者は原因が別にあり、後者は**投擲位置を増やせば
        判別できるようになる**（research.md Decision 4 の Follow-up）。
        """
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_Y_MM,
        )
        self._assert_only(result.judgement.rationale, BIAS_SENTENCES, "degenerate")

    def test_mismatch_is_recorded_as_mismatch(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(
            BIAS_DIAGONAL, settings=attr_settings, calibration_bias_mm=CAL_BIAS_X_MM
        )
        self._assert_only(result.judgement.rationale, BIAS_SENTENCES, "mismatch")

    def test_no_bias_is_recorded_as_rule_one(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(BIAS_INSIGNIFICANT, settings=attr_settings)
        self._assert_only(result.judgement.rationale, BIAS_SENTENCES, "none")

    def test_observation_noise_is_recorded_as_rule_five(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        self._assert_only(
            result.judgement.rationale, SCATTER_SENTENCES, "observation_noise"
        )

    def test_model_origin_is_recorded_as_rule_six(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=8.0
        )
        self._assert_only(
            result.judgement.rationale, SCATTER_SENTENCES, "prediction"
        )

    def test_a_small_residual_is_recorded_as_rule_seven(
        self, attr_settings: M1Settings
    ) -> None:
        result = attribute_group(
            SCATTER_ONLY,
            settings=attr_settings,
            noise_sigma_mm=LOW_NOISE_MM,
            residual_mm=1.0,
        )
        self._assert_only(
            result.judgement.rationale, SCATTER_SENTENCES, "small_residual"
        )


class TestVerdictAndEvidence:
    """判定値と根拠も**成分ごと**に残る（要件 6.9 / 5.9）。"""

    def test_the_verdict_names_both_components(
        self, attr_settings: M1Settings
    ) -> None:
        """判定値は偏りとばらつきの**対**である。単一の帰属先へ畳まない。

        期待値は本ファイルのリテラルであり、実装の列挙型から組まない
        （既知の空振り形3: ラベルを実装の定数と自己比較する）。
        """
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=8.0
        )
        assert result.judgement.verdict == "bias=calibration/scatter=prediction"

    def test_the_verdict_components_are_not_swapped(
        self, attr_settings: M1Settings
    ) -> None:
        """偏り側とばらつき側を入れ替えた実装をここで落とす。"""
        result = attribute_group(
            BIAS_TOWARD_CAMERA,
            settings=attr_settings,
            calibration_bias_mm=CAL_BIAS_ZERO_MM,
            noise_sigma_mm=LOW_NOISE_MM,
        )
        assert result.judgement.verdict == "bias=detection/scatter=observation_noise"

    def test_the_evidence_carries_the_numbers_behind_the_judgement(
        self, attr_settings: M1Settings
    ) -> None:
        evidence = attribute_group(
            BIAS_ALONG_X, settings=attr_settings
        ).judgement.evidence
        assert evidence["throw_count"] == 3
        assert evidence["bias_norm_mm"] == pytest.approx(30.0)
        assert evidence["scatter_rms_mm"] == pytest.approx(TRIPLE_SCATTER_RMS_MM)
        assert evidence["bootstrap_seed"] == ATTR_SEED

    def test_missing_numbers_are_recorded_as_missing_not_zero(
        self, attr_settings: M1Settings
    ) -> None:
        """**記録へ写す段**でも 0 で埋めない（変異は経路の各段に置く）。

        成分の型が `None` を返すことは `TestDecomposition` /
        `TestScatterAttribution` が固定しているが、それを
        `judgement.evidence` へ写すところは別の段である。`x or 0.0` のような
        写し方をすると、**成分は欠測なのに証跡だけが 0** になり、レポートを
        読んだ人が「ばらつきが無かった」「残差が無かった」と読む
        （タスク4.6 / 5.1 の「0 で埋めない」がここで抜ける）。
        """
        single = attribute_group(((30.0, 0.0),), settings=attr_settings)
        assert single.judgement.evidence["scatter_rms_mm"] is None
        assert single.judgement.evidence["significance_ratio"] is None

        no_bootstrap = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, sample_count=2
        )
        assert no_bootstrap.judgement.evidence["bootstrap_rms_mm"] is None

        no_residual = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, residual_mm=None
        )
        assert no_residual.judgement.evidence["residual_median_mm"] is None


class TestJudgementBoundaries:
    """説明文が明言している3つの境界の**包含性**を固定する。

    `criterion` は「境界はちょうどの値を範囲内に含む」「倍以上」と書いている。
    振る舞いが排他側（`<` / `>`）に倒れていても、**記録される規則は同じ文面
    のまま**なので、規則と実際の判定が食い違ったまま誰も気付けない
    （タスク4.4 が防ごうとした形の裏返し）。

    ここだけ判定関数を直接呼ぶのは、境界の値を**公開経路の入力側から作れ
    ない**ためである——規則5・6 の相手は再抽出の見積もりであり、入力データ
    から決まる量なので、それとちょうど等しいばらつきを外から与えられない。
    判定関数はモジュール直下にあってリテラル引数から呼べる（本ファイルの
    docstring 参照）。**判定に使う値はすべてテスト局所のリテラル**であり、
    実装の出力を許容差にも参照解にも使っていない。
    """

    def test_scatter_exactly_at_the_bootstrap_estimate_is_observation_noise(
        self, attr_settings: M1Settings
    ) -> None:
        """規則5 の境界: ちょうど見積もりどおりのばらつきは**範囲内**である。

        排他（`<`）にすると、観測ノイズで説明しきれる群がモデル由来へ倒れ、
        **予測アルゴリズムの改良に誤って時間を使う**。
        """
        verdict, _ = attribution._scatter_attribution(
            scatter_rms_mm=20.0,
            bootstrap_rms_mm=20.0,
            residual_median_mm=1.0,
            config=attr_settings.attribution,
        )
        assert verdict is Attribution.OBSERVATION_NOISE

        criterion = attribute_group(
            BIAS_ALONG_X, settings=attr_settings
        ).judgement.criterion
        assert "境界はちょうどの値を範囲内に含む" in criterion

    def test_a_residual_exactly_at_the_threshold_is_the_model(
        self, attr_settings: M1Settings
    ) -> None:
        """規則6 の境界: ちょうど倍率どおりの残差は「大きい」側に含める。

        倍率 2.0・見積もり 10.0 mm に対し残差 20.0 mm はちょうど 2 倍である。
        排他（`>`）にすると、規則6 と規則7 の境目が説明文（「倍以上」）と
        食い違う。
        """
        config = dataclasses.replace(
            attr_settings.attribution, residual_significance_ratio=2.0
        )
        verdict, _ = attribution._scatter_attribution(
            scatter_rms_mm=50.0,
            bootstrap_rms_mm=10.0,
            residual_median_mm=20.0,
            config=config,
        )
        assert verdict is Attribution.PREDICTION

        criterion = attribution.attribution_criterion(
            direction_agreement_deg=30.0,
            bias_significance_ratio=1.0,
            residual_significance_ratio=2.0,
            range_band_mm=500.0,
        )
        assert "その見積もりの 2 倍以上ならモデル由来（予測）とする" in criterion

    def test_a_report_bias_exactly_at_the_threshold_is_recognised(self) -> None:
        """レポート偏りの境界: ちょうど倍率どおりなら「認められる」側である。

        期待値は**実装の状態ラベル定数と比べない**（既知の空振り形3: ラベル
        を実装の定数と自己比較すると、定数を全部同じ値へ潰しても通る）。
        代わりに、明らかに認められる入力・明らかに認められない入力を並べ、
        **境界の値が前者と同じ側へ落ちる**ことを固定する。
        """
        recognised = attribution._report_bias_state(
            {"bias_mm": [100.0, 0.0, 0.0], "scatter_rms_mm": 1.0},
            bias_significance_ratio=2.0,
        )
        negligible = attribution._report_bias_state(
            {"bias_mm": [1.0, 0.0, 0.0], "scatter_rms_mm": 100.0},
            bias_significance_ratio=2.0,
        )
        boundary = attribution._report_bias_state(
            {"bias_mm": [4.0, 0.0, 0.0], "scatter_rms_mm": 2.0},
            bias_significance_ratio=2.0,
        )
        assert recognised != negligible
        assert boundary == recognised

        # **倍率が実際に効いていること**（配線の固定）。境界に選んだ
        # 偏り 4.0 / ばらつき 2.0 は倍率 1.0 でも 2.0 でも「認められる」側に
        # 落ちるので、**倍率を定数へ潰した実装を識別できない**。倍率で判定が
        # 動く入力を1組併置する——偏り 3.0 / ばらつき 2.0 は、倍率 1.0 なら
        # 認められ、2.0 なら認められない。`criterion` はこの倍率を
        # 「ばらつきの N 倍以上」として記録するので、配線が切れていると
        # **記録した規則と適用した規則が食い違う**（要件 6.1）。
        strict = attribution._report_bias_state(
            {"bias_mm": [3.0, 0.0, 0.0], "scatter_rms_mm": 2.0},
            bias_significance_ratio=2.0,
        )
        loose = attribution._report_bias_state(
            {"bias_mm": [3.0, 0.0, 0.0], "scatter_rms_mm": 2.0},
            bias_significance_ratio=1.0,
        )
        assert strict == negligible
        assert loose == recognised

        criterion = attribution.attribution_criterion(
            direction_agreement_deg=30.0,
            bias_significance_ratio=2.0,
            residual_significance_ratio=1.0,
            range_band_mm=500.0,
        )
        assert "大きさがレポートのばらつきの 2 倍以上であることをいう" in criterion


class TestUnverifiedCalibration:
    """未検証のキャリブレーションで得た群からは帰属できない（要件 2.2）。"""

    def test_an_unverified_group_is_marked_provisional(
        self, attr_settings: M1Settings
    ) -> None:
        """値は返すが、**判断に用いてよい状態ではない**印を付ける。"""
        result = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, verified=False
        )
        assert result.judgement.provisional is True
        assert "誤差の帰属ができない" in result.judgement.rationale

    def test_a_verified_group_is_not_marked_by_default(
        self, attr_settings: M1Settings
    ) -> None:
        """印が常に立つ実装をここで落とす（印の意味が失われる）。"""
        result = attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert result.judgement.provisional is False
        assert "誤差の帰属ができない" not in result.judgement.rationale

    def test_an_aggregate_marked_provisional_stays_provisional(
        self, attr_settings: M1Settings
    ) -> None:
        """試行数の下限未達（要件 5.10）は帰属でも引き継ぐ。"""
        rows = [build_row(f"throw-{i:04d}", BIAS_ALONG_X[i]) for i in range(3)]
        records = [build_attributed_record(f"throw-{i:04d}") for i in range(3)]
        result = attribute(
            build_aggregate(rows, provisional=True), records, settings=attr_settings
        )
        assert result.judgement.provisional is True


class TestAttributionDeterminism:
    """同一入力・同一種に対して同一結果（要件 12.4）。"""

    def test_the_same_settings_give_an_identical_result(
        self, attr_settings: M1Settings
    ) -> None:
        first = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        second = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        assert first.scatter.bootstrap_rms_mm == second.scatter.bootstrap_rms_mm

    def test_a_different_seed_changes_the_bootstrap_estimate(
        self, attr_settings: M1Settings, tmp_path: Path
    ) -> None:
        """**変えると変わる**ことと対で固定する（タスク5.1 の教訓）。

        一致だけを見ると、設定の種を無視して定数種を使う実装が素通りする。
        """
        other = _resolve_settings(
            tmp_path,
            overrides={
                "bootstrap_iterations": ATTR_ITERATIONS,
                "bootstrap_seed": ATTR_SEED + 991,
            },
        )
        first = attribute_group(
            BIAS_ALONG_X, settings=attr_settings, noise_sigma_mm=LOW_NOISE_MM
        )
        second = attribute_group(
            BIAS_ALONG_X, settings=other, noise_sigma_mm=LOW_NOISE_MM
        )
        assert first.scatter.bootstrap_rms_mm != second.scatter.bootstrap_rms_mm

    def test_the_iteration_count_comes_from_the_settings(
        self, attr_settings: M1Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """再抽出回数は設定から渡る（既定値に落ちない）。

        3投擲 × 24 反復で 72 回。回数を数えるのは、**引数を写しただけで
        既定回数しか回さない**実装を落とすためである（タスク5.1 と同型）。
        """
        calls = _count_predict_calls(monkeypatch)
        attribute_group(BIAS_ALONG_X, settings=attr_settings)
        assert len(calls) == 3 * ATTR_ITERATIONS


class TestAttributionSettingsWiring:
    """帰属の閾値は設定から解決される（要件 13.5 / 13.7）。"""

    def test_the_thresholds_are_resolvable(self, tmp_path: Path) -> None:
        settings = _resolve_settings(
            tmp_path,
            overrides={
                "direction_agreement_deg": 12.5,
                "bias_significance_ratio": 2.0,
                "residual_significance_ratio": 3.0,
                "range_band_mm": 250.0,
            },
        )
        assert settings.attribution.direction_agreement_deg == 12.5
        assert settings.attribution.bias_significance_ratio == 2.0
        assert settings.attribution.residual_significance_ratio == 3.0
        assert settings.attribution.range_band_mm == 250.0

        described = settings.describe()["attribution"]
        assert described["residual_significance_ratio"] == 3.0  # type: ignore[index]
        assert described["range_band_mm"] == 250.0  # type: ignore[index]

    def test_the_defaults_match_the_literals_used_in_this_file(
        self, attr_settings: M1Settings
    ) -> None:
        """本ファイルの閾値リテラルが既定値と同じであることを独立に固定する。

        判定の期待値は**リテラル側**から組んであるので、この検査が落ちたら
        直すのは期待値ではなく**このファイルのリテラル**である。
        """
        assert attr_settings.attribution.direction_agreement_deg == (
            DIRECTION_AGREEMENT_DEG
        )
        assert attr_settings.attribution.bias_significance_ratio == (
            BIAS_SIGNIFICANCE_RATIO
        )
        assert attr_settings.attribution.residual_significance_ratio == (
            RESIDUAL_SIGNIFICANCE_RATIO
        )
        assert attr_settings.attribution.range_band_mm == RANGE_BAND_MM

    @pytest.mark.parametrize(
        "key", ["residual_significance_ratio", "range_band_mm"]
    )
    def test_non_positive_thresholds_are_rejected(
        self, tmp_path: Path, key: str
    ) -> None:
        """不正な設定は実行開始前に拒否する（要件 13.6）。"""
        with pytest.raises(M1ConfigError):
            _resolve_settings(tmp_path, overrides={key: 0.0})

    def test_the_new_defaults_are_declared_provisional(
        self, tmp_path: Path
    ) -> None:
        """既定の閾値は**暫定の評価候補**であって合否条件ではない（要件 13.7）。"""
        assert "residual_significance_ratio" in PROVISIONAL_NOTICE
        assert "range_band_mm" in PROVISIONAL_NOTICE
        notice = str(_resolve_settings(tmp_path).describe()["provisional_notice"])
        assert "residual_significance_ratio" in notice
        assert "range_band_mm" in notice


class TestAttributionBoundary:
    """評価側は上流パッケージを直接 import しない（要件 13.1）。"""

    @pytest.mark.parametrize(
        "package",
        [
            "sensing_foundation",
            "flying_object_tracking",
            "world_frame_calibration",
            "numpy",
            "cv2",
            "pyrealsense2",
        ],
    )
    def test_the_module_does_not_import_forbidden_packages(
        self, package: str
    ) -> None:
        """検証レポートの値は**記録に埋め込まれた要約から**読む（要件 13.1）。

        上流を直接呼ぶと、`seam.py` に閉じた接点が増え、評価側が実機の
        キャリブレーション結果を開けてしまう。
        """
        source = Path(inspect.getfile(attribution)).read_text(encoding="utf-8")
        roots: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        assert package not in roots
