"""`EffectiveWindow` の観測可能な完了状態を固定する（タスク 5.2）。

design.md「L3: 計測と画像処理の基礎 → EffectiveWindow」・要件 4.5, 8.4 を対象に
する。

検証する観測可能な完了状態（tasks.md 5.2 verbatim）:

- 既知の時刻列に対して窓あたりの値（`value()`）と最密窓の値（`peak()`）が
  手計算と一致すること
- `peak()` が真のスライディングウィンドウであり、原点整列した固定ビンとは
  異なる結果を生む具体例で、両アルゴリズムの差そのものを回帰検出できること
  （`TestSlidingWindowVsFixedBinning`。`TestHandComputedExample` の時刻列は
  偶然この差を検出しないため、判別可能な別の時刻列を専用に用意している）

加えて、本タスクの実装ノートが要求する周辺条件も固定する:

- 空・単一サンプルでの安全な既定動作（ゼロ除算やクラッシュをしない）
- `window_ms` が真にコンストラクタ引数であり、固定値でないこと（同一の
  時刻列に異なる `window_ms` を与えると結果が変わること）
- クラス名・メソッド名が上流 `sensing_foundation`
  （`effective_samples_per_window`、フレーム層の指標）と衝突しないこと
- 実装内に `window_ms` の固定値リテラルが埋め込まれていないこと
  （grep 的に検査する）

`value()` / `peak()` の定義（本ファイルのテストが固定する仕様。
`src/flying_object_tracking/metrics.py` の `EffectiveWindow` docstring と
同一）:

- `value()`（窓あたりの平均点数）: `add()` された全タイムスタンプの観測
  区間長 `span_ms = max(t) - min(t)` を `window_ms` で割った値を「窓の
  個数」とみなし（`span_ms < window_ms` のときは1個とみなす）、`add()`
  された総数をその窓数で割った連続値（float）を返す。サンプルが1つも
  無ければ `0.0`。
- `peak()`（最も密な窓での点数）: 開始位置を任意の観測時刻に固定できる、
  幅 `window_ms` の半開区間 `[t, t+window_ms)` のうち、含まれるタイム
  スタンプ数が最大のものを返す（真のスライディングウィンドウ最大値。
  固定ビンではない）。サンプルが1つも無ければ `0`。
"""

from __future__ import annotations

import inspect

from flying_object_tracking.metrics import EffectiveWindow

# ==========================================================================
# 1. 空・安全な既定動作
# ==========================================================================


class TestEmpty:
    def test_value_is_zero_when_nothing_added(self) -> None:
        window = EffectiveWindow(window_ms=100.0)
        assert window.value() == 0.0

    def test_peak_is_zero_when_nothing_added(self) -> None:
        window = EffectiveWindow(window_ms=100.0)
        assert window.peak() == 0

    def test_no_division_by_zero_exception(self) -> None:
        window = EffectiveWindow(window_ms=100.0)
        # 例外を送出しないことそのものがアサーションである。
        window.value()
        window.peak()


# ==========================================================================
# 2. 単一サンプル
# ==========================================================================


class TestSingleSample:
    def test_value_is_one_for_a_single_sample(self) -> None:
        # 手計算: span_ms = 0（唯一のサンプルなので max - min = 0）。
        # windows = max(1.0, 0 / 100.0) = 1.0。value = 1 / 1.0 = 1.0。
        window = EffectiveWindow(window_ms=100.0)
        window.add(5.0)
        assert window.value() == 1.0

    def test_peak_is_one_for_a_single_sample(self) -> None:
        # 手計算: 唯一のサンプルを含む窓は1点しか持てない。
        window = EffectiveWindow(window_ms=100.0)
        window.add(5.0)
        assert window.peak() == 1


# ==========================================================================
# 3. 手計算で検証可能な既知の時刻列（tasks.md 5.2 の完了条件そのもの）
# ==========================================================================


class TestHandComputedExample:
    """window_ms=100 の下で、既知の時刻列に対する手計算結果を固定する。

    **本クラスは tasks.md 5.2 の完了条件（既知の時刻列に対して手計算と
    一致すること）を満たす一般的な worked example であり、
    「真のスライディングウィンドウでなければ計算を誤る」ことの証明では
    ない。** 実際に計算し直すと、この時刻列は固定ビン（原点 0 に整列した
    幅100msのビン: [0,100), [100,200), [200,300), ...）でも `peak()` が
    偶然同じ値（5）になる——先頭クラスタ [0,10,20,50,60] も後続クラスタ
    [200,210,220,230] も、たまたまビン境界をまたがずに1つのビンへ収まる
    ためである。したがって本クラスのアサーションは「真のスライディング
    ウィンドウと固定ビンの実装差」を検出しない（両者を区別する回帰テストは
    `TestSlidingWindowVsFixedBinning` を参照）。本クラスの役割は、あくまで
    `value()` / `peak()` の定義そのものが仕様どおりに実装されていることを、
    手計算で追える具体例で固定することに限る。

    時刻列（ms）: [0, 10, 20, 50, 60, 200, 210, 220, 230, 500]
    （2つの密なクラスタ [0,10,20,50,60] と [200,210,220,230] に、孤立点
    500 を加えた、意図的にバースト性のあるパターン）

    --- value() の手計算 ---
    総サンプル数 = 10。
    span_ms = max(t) - min(t) = 500 - 0 = 500。
    windows = max(1.0, span_ms / window_ms) = max(1.0, 500/100) = 5.0。
    value = 10 / 5.0 = 2.0。

    --- peak() の手計算（真のスライディングウィンドウ） ---
    候補となる各開始点 t_i について、半開区間 [t_i, t_i + 100) に入る
    タイムスタンプ数を数える（時刻列はソート済みとして左から検討）:

      開始 t=0   → [0, 100)   に入る: 0, 10, 20, 50, 60           → 5個
      開始 t=10  → [10, 110)  に入る: 10, 20, 50, 60              → 4個
      開始 t=20  → [20, 120)  に入る: 20, 50, 60                  → 3個
      開始 t=50  → [50, 150)  に入る: 50, 60                      → 2個
      開始 t=60  → [60, 160)  に入る: 60                          → 1個
      開始 t=200 → [200, 300) に入る: 200, 210, 220, 230          → 4個
      開始 t=210 → [210, 310) に入る: 210, 220, 230               → 3個
      開始 t=220 → [220, 320) に入る: 220, 230                    → 2個
      開始 t=230 → [230, 330) に入る: 230                         → 1個
      開始 t=500 → [500, 600) に入る: 500                         → 1個

    最大は t=0 を開始点とする窓の **5個**（[0,10,20,50,60] が単一の
    100ms 区間に収まる）。したがって peak() = 5。

    （この5個という値は、固定ビン方式で計算しても同じ5になる。上述の
    とおり、これは「アルゴリズムの選択が結果に無関係」という意味ではなく、
    「たまたまこの時刻列ではビン境界を挟むデータが無かった」という意味
    であることに注意。真のアルゴリズム差を確認するテストは別クラスに
    分離してある。）
    """

    TIMESTAMPS_MS = [0.0, 10.0, 20.0, 50.0, 60.0, 200.0, 210.0, 220.0, 230.0, 500.0]

    def _build(self, window_ms: float) -> EffectiveWindow:
        window = EffectiveWindow(window_ms=window_ms)
        for t_ms in self.TIMESTAMPS_MS:
            window.add(t_ms)
        return window

    def test_value_matches_hand_computation(self) -> None:
        window = self._build(window_ms=100.0)
        assert window.value() == 2.0

    def test_peak_matches_hand_computation(self) -> None:
        window = self._build(window_ms=100.0)
        assert window.peak() == 5

    def test_add_order_does_not_matter(self) -> None:
        """`add()` は時刻順である必要がない（内部でソートする）ことを確認する。"""
        window = EffectiveWindow(window_ms=100.0)
        for t_ms in reversed(self.TIMESTAMPS_MS):
            window.add(t_ms)
        assert window.value() == 2.0
        assert window.peak() == 5


# ==========================================================================
# 4. window_ms が真にコンストラクタ引数であること（固定値埋め込みでない）
# ==========================================================================


class TestWindowMsIsAGenuineConstructorParameter:
    """同一の時刻列に異なる `window_ms` を与えると、結果が変わることを確認する。

    手計算（window_ms=300 の場合、TestHandComputedExample と同じ時刻列）:
    windows = max(1.0, 500/300) = 1.6666... 。value = 10 / (500/300) = 6.0。
    peak(): 開始 t=0 → [0, 300) に入るのは 0,10,20,50,60,200,210,220,230
    の9点（500 は含まれない）。他のどの開始点より多いので peak = 9。
    """

    TIMESTAMPS_MS = TestHandComputedExample.TIMESTAMPS_MS

    def test_wider_window_yields_larger_value_and_peak(self) -> None:
        narrow = EffectiveWindow(window_ms=100.0)
        wide = EffectiveWindow(window_ms=300.0)
        for t_ms in self.TIMESTAMPS_MS:
            narrow.add(t_ms)
            wide.add(t_ms)

        assert narrow.value() == 2.0
        assert narrow.peak() == 5

        assert wide.value() == 6.0
        assert wide.peak() == 9

        # 広い窓の方が値が大きい（同じデータでもより多くの点を1窓に含む）。
        assert wide.value() > narrow.value()
        assert wide.peak() > narrow.peak()


# ==========================================================================
# 4.5. peak(): 真のスライディングウィンドウであることの判別テスト
# ==========================================================================


class TestSlidingWindowVsFixedBinning:
    """`peak()` が真のスライディングウィンドウであり、原点整列した固定ビン
    （`floor(t / window_ms)` によるビン分け）とは異なる結果を生む具体例を
    固定する。

    `TestHandComputedExample` の時刻列はこの2つのアルゴリズムを判別
    **しない**（両者がたまたま同じ結果になる。同クラスの docstring 参照）。
    本クラスはその欠落を埋め、`peak()` の実装が実際に固定ビンへ後退したら
    失敗する回帰テストを提供する。

    時刻列（ms）: [95.0, 100.0, 105.0, 110.0]、window_ms=100.0
    （4点が密集しているが、境界値 100.0 をまたぐ配置——原点整列した固定
    ビンなら 95.0 だけが手前のビンに落ち、残り3点が次のビンに落ちる、と
    いう「バーストがビン境界をまたぐ」ケースそのもの）

    --- 真のスライディングウィンドウでの手計算 ---
    開始 t=95  → [95, 195)  に入る: 95, 100, 105, 110  → 4個
    開始 t=100 → [100, 200) に入る: 100, 105, 110      → 3個
    開始 t=105 → [105, 205) に入る: 105, 110           → 2個
    開始 t=110 → [110, 210) に入る: 110                → 1個
    最大は開始 t=95 の **4個**。したがって peak() = 4。

    --- 原点整列した固定ビン（floor(t / 100)）での手計算（参考、実装が
        採用していない方式） ---
    ビン0 [0, 100):   95                    → 1個
    ビン1 [100, 200): 100, 105, 110         → 3個
    最大ビンは3個。**固定ビン方式なら peak() は 3 になり、4 にはならない。**

    したがって本テストが `peak() == 4` を要求することは、実装が固定ビン
    方式へ後退していないことの直接証拠になる（固定ビン実装へ変えると
    `3 != 4` でこのテストは確実に落ちる）。
    """

    TIMESTAMPS_MS = [95.0, 100.0, 105.0, 110.0]
    WINDOW_MS = 100.0

    def test_peak_uses_true_sliding_window_not_fixed_bins(self) -> None:
        window = EffectiveWindow(window_ms=self.WINDOW_MS)
        for t_ms in self.TIMESTAMPS_MS:
            window.add(t_ms)

        # 固定ビン方式（floor(t / window_ms)）なら 3 を返すはずの入力で、
        # 真のスライディングウィンドウの結果である 4 を要求する。
        assert window.peak() == 4

    def test_peak_does_not_equal_the_naive_fixed_bin_result(self) -> None:
        """固定ビン方式で同じ入力を計算すると 3 になることを、テスト内で
        直接計算して対比させる（`peak()` がこの過小評価に陥っていないこと
        を、単なる期待値の暗記ではなく計算過程ごと示す）。
        """
        import math

        naive_fixed_bin_counts: dict[int, int] = {}
        for t_ms in self.TIMESTAMPS_MS:
            bin_index = math.floor(t_ms / self.WINDOW_MS)
            naive_fixed_bin_counts[bin_index] = naive_fixed_bin_counts.get(bin_index, 0) + 1
        naive_peak = max(naive_fixed_bin_counts.values())
        assert naive_peak == 3  # 参考: 固定ビン方式ならこうなる

        window = EffectiveWindow(window_ms=self.WINDOW_MS)
        for t_ms in self.TIMESTAMPS_MS:
            window.add(t_ms)

        assert window.peak() != naive_peak
        assert window.peak() == 4


# ==========================================================================
# 5. 名前が上流のフレーム層の指標と衝突しないこと
# ==========================================================================


class TestNameIsDistinctFromUpstreamFrameLayerMetric:
    """`sensing_foundation` の `effective_samples_per_window`
    （フレーム層の実効サンプル数）と、本クラスの `value()`
    （`effective_points_per_window`、点層の実効点数）は対象が異なる指標
    であり、取り違えると集計側（`DetectorComparison`）が誤る
    （design.md「EffectiveWindow / Implementation Notes」、
    `sensing_foundation/bench/modes.py` モジュール docstring「指標名の
    対応関係」）。本テストは、実装がこの上流の名前をそのまま再エクスポート
    したりエイリアスしたりしていないことを軽量に確認する。
    """

    def test_effective_window_class_name_is_its_own(self) -> None:
        assert EffectiveWindow.__name__ == "EffectiveWindow"
        assert not hasattr(EffectiveWindow, "effective_samples_per_window")

    def test_does_not_alias_the_upstream_frame_layer_function(self) -> None:
        from sensing_foundation.bench.modes import effective_samples_per_window

        # EffectiveWindow.value は本クラス固有のメソッドであり、上流の
        # フレーム層関数をそのまま再エクスポートしたものではない。
        assert EffectiveWindow.value is not effective_samples_per_window
        assert EffectiveWindow.peak is not effective_samples_per_window


# ==========================================================================
# 6. window_ms の固定値埋め込みが無いこと（grep 的な静的検査）
# ==========================================================================


class TestNoHardcodedWindowMsLiteral:
    def test_source_assigns_window_ms_only_from_the_constructor_parameter(self) -> None:
        # クラス docstring は「既定 600.0 は初期評価候補」という説明文の
        # 一部として数値に言及するため、実装コード（__init__ / value /
        # peak の本体）だけを対象に検査する（docstring を含む
        # inspect.getsource(EffectiveWindow) 全体を対象にすると、この
        # 説明文自体が誤検出される）。
        init_source = inspect.getsource(EffectiveWindow.__init__)
        value_source = inspect.getsource(EffectiveWindow.value)
        peak_source = inspect.getsource(EffectiveWindow.peak)
        code_source = init_source + value_source + peak_source

        assert "self._window_ms = window_ms" in init_source
        # MeasurementConfig の既定値（初期評価候補としての 600.0）などの
        # 固定値リテラルが EffectiveWindow の実コードに直接現れないことを
        # 確認する。
        forbidden_literals = ("600.0", "600,", " 600)", "= 600")
        for literal in forbidden_literals:
            assert literal not in code_source, (
                f"window_ms の固定値らしきリテラルが見つかった: {literal!r}"
            )
