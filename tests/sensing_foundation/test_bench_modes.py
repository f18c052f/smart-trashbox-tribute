"""`sensing_foundation.bench.modes` に対するテスト（タスク 7.2）。

ファイル名は design.md の File Structure Plan が示す `test_bench.py`
（`bench/modes.py` と `bench/logging_overhead.py`（タスク 7.3、未実装）の
両方を1ファイルで扱う想定）から意図的に外れ、`test_bench_modes.py` とする
——タスクの `_Boundary: ModeSweep_` に合わせ、タスク 7.3 が後から
`test_bench_logging_overhead.py`（または同種の名前）を追加した際に本ファイルと
衝突・上書きが起きないようにするため（タスク 1.5 が固定した「タスクごとに
ファイルを分ける」回避方針と同種の判断。tasks.md Implementation Notes
「タスク1.5」参照）。

観測可能な完了状態（tasks.md 7.2）を固定する:

- 合成入力で複数モードを掃引すると、全モード分の結果（`effective_samples_
  per_window` を含む）が1つの `tuple` に揃う
- 評価軸（`effective_samples_per_window`）は `measured_fps` 単体とは別の、
  窓長で決まる指標であり、窓長を変えると値も変わる（固定値を埋め込んで
  いない）
- ウォームアップ区間中のフレーム・計測点は最終結果から除外される
- Color 有無・解像度・fps は `modes` の各 `CaptureConfig` で切り替えられる
- モードの構築・実行が測定値ゼロのまま失敗した場合、そのモードの結果は
  `None` になり、**他のモードの結果は失われない**
- 測定値が部分的に得られた失敗は `ModeResult(valid=False, ...)` として
  記録される（`None` で消えない）
- USB2 検出は `usb2_warning` 属性の有無・値で汎用的に判定される
- 目標値の充足判定に相当するロジック・フィールドを一切持たない
- 結果は `dataclasses.asdict()` + `json.dumps()` でそのまま直列化できる

要件: 11.2, 11.3, 11.4, 11.6, 9.6
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from synthetic import make_supplier

from sensing_foundation.bench.modes import ModeResult, ModeSweep, effective_samples_per_window
from sensing_foundation.config import CaptureConfig, RuntimeSettings
from sensing_foundation.errors import SensingConfigError
from sensing_foundation.sysstat import Sysstat
from sensing_foundation.types import SourceKind

WIDTH_PX = 8
HEIGHT_PX = 6


def _settings() -> RuntimeSettings:
    return RuntimeSettings(source=SourceKind.SIMULATED, capture=CaptureConfig())


def _counting_supplier(seqs, *, delay_s: float = 0.0, violate_at=None):
    """`synthetic.make_supplier` を包み、呼び出し回数を数える。"""
    inner = make_supplier(WIDTH_PX, HEIGHT_PX, seqs, delay_s=delay_s, violate_at=violate_at)
    calls = {"count": 0}

    def supplier(index: int):
        calls["count"] += 1
        return inner(index)

    return supplier, calls


# ----------------------------------------------------------------------------
# effective_samples_per_window(): 純関数の単体テスト（手計算で検証済み）
# ----------------------------------------------------------------------------


class TestEffectiveSamplesPerWindow:
    def test_hand_verified_uniform_frames_600ms_window(self):
        """0,100,...,2900 ms の30フレーム・窓600ms・区間3000ms → 各窓に厳密に6枚、平均6.0。

        手計算: floor(3000/600)=5窓。各窓は600msぶん=6フレーム間隔100msを
        きっちり6個ずつ含む（0-500, 600-1100, ..., 2400-2900）。5*6=30 で
        全フレームが過不足なく使われる。
        """
        frame_times_ms = [float(k * 100) for k in range(30)]
        result = effective_samples_per_window(
            frame_times_ms, origin_ms=0.0, span_ms=3000.0, window_ms=600.0
        )
        assert result == pytest.approx(6.0)

    def test_window_ms_is_genuinely_configurable(self):
        """同じフレーム列でも window_ms を変えると結果が変わる（固定値を埋め込んでいない）。"""
        frame_times_ms = [float(k * 100) for k in range(30)]
        result_600 = effective_samples_per_window(
            frame_times_ms, origin_ms=0.0, span_ms=3000.0, window_ms=600.0
        )
        result_300 = effective_samples_per_window(
            frame_times_ms, origin_ms=0.0, span_ms=3000.0, window_ms=300.0
        )
        assert result_600 == pytest.approx(6.0)
        assert result_300 == pytest.approx(3.0)
        assert result_600 != result_300

    def test_fallback_when_window_longer_than_span(self):
        """`span_ms < window_ms`（完全な窓が1つも取れない）は代数近似へフォールバックする。

        5フレームが 0..400ms に均等分布（100ms間隔）→ 実測fps = 5/(400/1000)
        = 12.5fps。window_ms=600ms の窓なら期待値は 12.5 * 0.6 = 7.5。
        """
        frame_times_ms = [0.0, 100.0, 200.0, 300.0, 400.0]
        result = effective_samples_per_window(
            frame_times_ms, origin_ms=0.0, span_ms=500.0, window_ms=600.0
        )
        assert result == pytest.approx(7.5)

    def test_fallback_with_no_frames_is_zero(self):
        result = effective_samples_per_window([], origin_ms=0.0, span_ms=100.0, window_ms=600.0)
        assert result == 0.0

    def test_rejects_non_positive_window_ms(self):
        with pytest.raises(SensingConfigError):
            effective_samples_per_window([], origin_ms=0.0, span_ms=100.0, window_ms=0.0)


# ----------------------------------------------------------------------------
# ModeSweep.__init__(): 構築時検証
# ----------------------------------------------------------------------------


class TestModeSweepConstruction:
    def test_rejects_empty_modes(self):
        with pytest.raises(SensingConfigError):
            ModeSweep(modes=(), duration_s=1.0, window_ms=600.0, warmup_s=0.0)

    def test_rejects_non_positive_duration(self):
        with pytest.raises(SensingConfigError):
            ModeSweep(modes=(CaptureConfig(),), duration_s=0.0, window_ms=600.0, warmup_s=0.0)

    def test_rejects_negative_warmup(self):
        with pytest.raises(SensingConfigError):
            ModeSweep(modes=(CaptureConfig(),), duration_s=1.0, window_ms=600.0, warmup_s=-0.1)


# ----------------------------------------------------------------------------
# ModeSweep.run(): simulated 入力での掃引（合成入力・実機不要）
# ----------------------------------------------------------------------------


class TestModeSweepRunAgainstSimulated:
    def test_sweeps_640x480_30fps_and_60fps_default_candidates(self):
        """要件 11.2: 少なくとも 640x480/30fps と 640x480/60fps を同一条件で比較できる。

        実際の解像度は本テストでは小さい WIDTH_PX/HEIGHT_PX を使うが、
        「640x480 の 30fps と 60fps という組を候補として渡せる」ことは
        `CaptureConfig` のフィールドとして表現できることを固定する
        （実測は本テストの主眼ではないため、解像度は軽量な値を使う）。
        """
        modes = (
            CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30, color_enabled=False),
            CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=60, color_enabled=False),
        )
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(200)), delay_s=0.005)
        sweep = ModeSweep(modes=modes, duration_s=0.3, window_ms=100.0, warmup_s=0.05)

        results = sweep.run(_settings(), supplier=supplier)

        assert len(results) == 2
        for mode, result in zip(modes, results, strict=True):
            assert result is not None
            assert result.valid is True
            assert result.invalid_reason is None
            assert result.width_px == mode.width_px
            assert result.height_px == mode.height_px
            assert result.fps == mode.fps
            assert result.color_enabled == mode.color_enabled
            assert result.frames_yielded > 0
            assert result.measured_fps > 0.0
            assert result.effective_samples_per_window > 0.0
            assert result.wait_ms_p50 >= 0.0
            assert result.wait_ms_p95 >= result.wait_ms_p50
            assert result.total_ms_p50 >= 0.0
            assert result.total_ms_p95 >= result.total_ms_p50
            assert result.frames_dropped == 0  # simulated は常にドレイン無効
            assert result.frames_missing == 0  # SimulatedSource の seq は連番

    def test_color_enabled_is_switchable_per_mode(self):
        modes = (
            CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30, color_enabled=True),
            CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30, color_enabled=False),
        )
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(100)), delay_s=0.002)
        sweep = ModeSweep(modes=modes, duration_s=0.1, window_ms=50.0, warmup_s=0.0)

        results = sweep.run(_settings(), supplier=supplier)

        assert results[0] is not None and results[0].color_enabled is True
        assert results[1] is not None and results[1].color_enabled is False

    def test_warmup_frames_are_excluded_from_results(self):
        """ウォームアップ区間中に供給されたフレームは最終結果から除外される。

        `warmup_s` と `duration_s` をどちらも正にし、供給関数の呼び出し
        回数（実際に生成された全フレーム数、ウォームアップ分も含む）が
        `frames_yielded`（ウォームアップ除外後の件数）を必ず上回ることを
        確認する。これは実時間ベースの計測（`SessionClock`）でもタイミング
        の揺らぎに左右されない頑健な検証——ウォームアップが正しく除外
        されていれば、供給呼び出し回数（全区間分）は測定対象件数
        （測定区間分のみ）より常に多くなるはずであり、除外が壊れていれば
        （＝ウォームアップ分も測定に混ざっていれば）両者はほぼ一致して
        しまう。
        """
        supplier, calls = _counting_supplier(list(range(200)), delay_s=0.01)
        mode = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30)
        sweep = ModeSweep(modes=(mode,), duration_s=0.3, window_ms=100.0, warmup_s=0.3)

        (result,) = sweep.run(_settings(), supplier=supplier)

        assert result is not None
        assert result.valid is True
        assert result.frames_yielded > 0
        assert calls["count"] > result.frames_yielded, (
            f"supplier が呼ばれた回数（{calls['count']}）はウォームアップ分を含む"
            f"全区間ぶんのはずであり、測定区間のみの frames_yielded"
            f"（{result.frames_yielded}）より多くなければウォームアップ除外が"
            "機能していない"
        )

    def test_mode_order_is_caller_controlled_and_results_match_by_config(self):
        """モード順の入れ替え再実行は呼び出し側が `modes` の順序を変えることで行う。

        同じ2つの候補を順方向・逆方向で2回 `run()` した結果を、fps で
        対応付けて比較できることを確認する（`ModeSweep` 自身が内部で
        シャッフルしないことの構造的な確認）。
        """
        cfg_30 = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30)
        cfg_60 = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=60)

        supplier_forward = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(100)), delay_s=0.003)
        supplier_reversed = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(100)), delay_s=0.003)

        forward = ModeSweep(
            modes=(cfg_30, cfg_60), duration_s=0.1, window_ms=50.0, warmup_s=0.0
        ).run(_settings(), supplier=supplier_forward)
        reversed_ = ModeSweep(
            modes=(cfg_60, cfg_30), duration_s=0.1, window_ms=50.0, warmup_s=0.0
        ).run(_settings(), supplier=supplier_reversed)

        forward_by_fps = {r.fps: r for r in forward if r is not None}
        reversed_by_fps = {r.fps: r for r in reversed_ if r is not None}

        assert set(forward_by_fps) == {30, 60}
        assert set(reversed_by_fps) == {30, 60}
        for fps in (30, 60):
            assert forward_by_fps[fps].valid is True
            assert reversed_by_fps[fps].valid is True
            assert forward_by_fps[fps].frames_yielded > 0
            assert reversed_by_fps[fps].frames_yielded > 0

    def test_results_are_json_serializable(self):
        modes = (CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30),)
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(50)), delay_s=0.002)
        sweep = ModeSweep(modes=modes, duration_s=0.05, window_ms=25.0, warmup_s=0.0)

        results = sweep.run(_settings(), supplier=supplier)

        payload = [dataclasses.asdict(r) if r is not None else None for r in results]
        text = json.dumps(payload, allow_nan=False)
        round_tripped = json.loads(text)
        assert round_tripped[0]["fps"] == 30

    @pytest.mark.skipif(not Sysstat.available(), reason="/proc が読めない環境")
    def test_cpu_and_memory_samples_are_real_non_none_values(self):
        """この WSL 環境では `/proc` が実在するため、CPU/メモリは欠測ではなく実測値になる。"""
        modes = (CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30),)
        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(200)), delay_s=0.02)
        sweep = ModeSweep(modes=modes, duration_s=1.4, window_ms=200.0, warmup_s=0.1)

        (result,) = sweep.run(_settings(), supplier=supplier)

        assert result is not None
        assert result.rss_bytes_max is not None
        assert result.rss_bytes_max > 0
        assert result.cpu_percent_mean is not None
        assert 0.0 <= result.cpu_percent_mean <= 100.0


# ----------------------------------------------------------------------------
# 無効な回の表現: 完全失敗（None）／部分失敗（valid=False）
# ----------------------------------------------------------------------------


class TestInvalidModeHandling:
    def test_mode_open_failure_records_none_without_discarding_other_modes(self):
        """1モードだけ解像度不一致で開閉直後に失敗しても、他モードの結果は残る。

        供給関数は常に (HEIGHT_PX, WIDTH_PX) 形状の配列を返すため、
        `width_px`/`height_px` が一致しないモードは最初の `_acquire()` で
        `SourceContractError` を送出する（`SimulatedSource` の契約違反
        リジェクト）。この時点でそのモードは1フレームも集められていない
        ため `None` として残り、前後の正常なモードの結果は失われない。
        """
        good_a = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30)
        mismatched = CaptureConfig(width_px=999, height_px=999, fps=30)
        good_b = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=60)

        supplier = make_supplier(WIDTH_PX, HEIGHT_PX, list(range(100)), delay_s=0.003)
        sweep = ModeSweep(
            modes=(good_a, mismatched, good_b), duration_s=0.1, window_ms=50.0, warmup_s=0.0
        )

        results = sweep.run(_settings(), supplier=supplier)

        assert len(results) == 3
        assert results[0] is not None and results[0].valid is True
        assert results[1] is None
        assert results[2] is not None and results[2].valid is True

    def test_partial_failure_mid_run_is_recorded_as_invalid_not_discarded(self):
        """測定途中の契約違反は、それまでに集めた実測値つきの `ModeResult(valid=False)` として残る。

        `violate_at=3` により4回目の呼び出し（index=3）だけ契約に反する
        配列を返す。`warmup_s=0` のため index=0,1,2 の3フレームは既に
        測定対象として集計済みであり、そのフレーム数は失われず結果に残る。
        """
        supplier, _ = _counting_supplier(list(range(50)), delay_s=0.005, violate_at=3)
        mode = CaptureConfig(width_px=WIDTH_PX, height_px=HEIGHT_PX, fps=30)
        sweep = ModeSweep(modes=(mode,), duration_s=10.0, window_ms=100.0, warmup_s=0.0)

        (result,) = sweep.run(_settings(), supplier=supplier)

        assert result is not None
        assert result.valid is False
        assert result.invalid_reason is not None
        assert "SourceContractError" in result.invalid_reason
        assert result.frames_yielded == 3


# ----------------------------------------------------------------------------
# USB2 検出（汎用ヘルパの単体テスト。実機無しでも検証できる）
# ----------------------------------------------------------------------------


class TestUsb2Detection:
    def test_true_triggers_invalid_reason(self):
        from sensing_foundation.bench.modes import _detect_usb2_invalid

        class FakeSource:
            usb2_warning = True

        reason = _detect_usb2_invalid(FakeSource())
        assert reason is not None
        assert "USB2" in reason

    def test_false_does_not_trigger(self):
        from sensing_foundation.bench.modes import _detect_usb2_invalid

        class FakeSource:
            usb2_warning = False

        assert _detect_usb2_invalid(FakeSource()) is None

    def test_none_unknown_does_not_trigger(self):
        from sensing_foundation.bench.modes import _detect_usb2_invalid

        class FakeSource:
            usb2_warning = None

        assert _detect_usb2_invalid(FakeSource()) is None

    def test_missing_attribute_does_not_trigger(self):
        """simulated/recorded は `usb2_warning` を持たない——誤検出しない。"""
        from sensing_foundation.bench.modes import _detect_usb2_invalid

        class FakeSourceWithoutUsbConcept:
            pass

        assert _detect_usb2_invalid(FakeSourceWithoutUsbConcept()) is None


# ----------------------------------------------------------------------------
# 目標値の充足判定を行わない（grep 可能な形で固定する）
# ----------------------------------------------------------------------------


class TestNoTargetJudgmentLogic:
    def test_mode_result_has_no_pass_fail_style_field(self):
        field_names = {f.name for f in dataclasses.fields(ModeResult)}
        forbidden = {
            "passed",
            "ok",
            "target_met",
            "meets_target",
            "target_fps",
            "min_fps",
            "required_fps",
        }
        assert field_names.isdisjoint(forbidden)

    def test_module_source_has_no_target_judgment_keywords(self):
        import inspect

        from sensing_foundation.bench import modes as bench_modes

        source = inspect.getsource(bench_modes)
        forbidden_substrings = (
            "target_fps",
            "min_fps",
            "required_fps",
            "PASS",
            "FAIL",
            "合格",
            "不合格",
            "目標達成",
        )
        for substring in forbidden_substrings:
            assert substring not in source, f"目標値判定を示唆する語が見つかった: {substring!r}"


# ----------------------------------------------------------------------------
# ドキュメント整合性: フレーム層/点層の指標対応の明記チェック
# ----------------------------------------------------------------------------


class TestPairedMetricDocumentation:
    def test_module_docstring_mentions_paired_point_layer_metric(self):
        import inspect

        from sensing_foundation.bench import modes as bench_modes

        module_doc = inspect.getdoc(bench_modes) or ""
        assert "effective_points_per_window" in module_doc
        assert "flying-object-tracking" in module_doc

    def test_mode_result_field_docstring_mentions_paired_metric(self):
        source = ModeResult.__doc__ or ""
        assert "effective_points_per_window" in source
