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

**私有ヘルパを直接呼ぶ検査が1つだけある**（`TestSpreadReduction`）。
ばらつきの**分母（N か N-1 か）と平均まわりであること**は、公開経路からは
「実装の出力そのもの」を参照せずに独立に導けない——再抽出の引きを
テスト側で再現するのは実装の写経であり、検査にならない。タスク3.1 が
`_with_m1_extra()` を直接呼んだのと同じ理由で、この1件だけ私有関数を突く。
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from pathlib import Path

import pytest
from m1fixtures import write_layout

from m1_validation import attribution
from m1_validation.attribution import (
    BootstrapSpread,
    bootstrap_prediction_spread,
)
from m1_validation.config import PROVISIONAL_NOTICE, M1Settings
from m1_validation.errors import M1ConfigError
from prediction_core import (
    InvalidReason,
    PredictionConfig,
    Sample,
    SourceKind,
    ThrowRecord,
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
