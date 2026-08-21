"""`sensing_foundation.timebase.SessionClock` に対するテスト（タスク 1.3）。

観測可能な完了状態（tasks.md 1.3）を固定する:
- 連続呼び出し（`now_ms()`）が単調非減少である
- 壁時計アンカ（`started_wall_ms`）は構築時に1度だけ取得され、
  以降の複数回のプロパティアクセスで再取得されない
- `to_wall_ms()` はアンカを起点にした純粋な換算関数であり、
  新たな壁時計の読み取りを行わない
- `session_id` は保持され、そのまま公開される

`time.perf_counter_ns` / `time.time_ns` をモンキーパッチし、下層の時刻源を
差し替えて挙動を固定する。
"""

from __future__ import annotations

import time

import pytest

from sensing_foundation.timebase import SessionClock


class TestSessionIdentifier:
    """session_id がそのまま保持・公開されることを固定する。"""

    def test_session_id_is_exposed_as_given(self) -> None:
        clock = SessionClock(session_id="20260821-000000-abcd")
        assert clock.session_id == "20260821-000000-abcd"

    def test_session_id_is_read_only_property(self) -> None:
        clock = SessionClock(session_id="s1")
        with pytest.raises(AttributeError):
            clock.session_id = "s2"  # type: ignore[misc]


class TestWallAnchorCapturedOnce:
    """壁時計アンカが構築時に1度だけ取得されることを固定する。"""

    def test_started_wall_ms_matches_time_at_construction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # time.time_ns を固定値にモンキーパッチし、アンカがその値から
        # 導出されることを確認する。
        fixed_ns = 1_700_000_000_123_456_789
        monkeypatch.setattr(time, "time_ns", lambda: fixed_ns)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock = SessionClock(session_id="s1")

        assert clock.started_wall_ms == pytest.approx(fixed_ns / 1_000_000)

    def test_started_wall_ms_is_stable_across_multiple_accesses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # time.time_ns が呼ばれるたびに異なる値を返すようにしておき、
        # started_wall_ms への複数回のアクセスで値が変化しないことを
        # 確認する（= 構築時の1回しか呼ばれていないことの証拠）。
        call_count = 0

        def fake_time_ns() -> int:
            nonlocal call_count
            call_count += 1
            # 呼ばれるたびに増加する値を返す。構築後に再取得されていれば
            # started_wall_ms が変化してしまうはず。
            return 1_000_000_000_000_000_000 + call_count * 10_000_000_000

        monkeypatch.setattr(time, "time_ns", fake_time_ns)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock = SessionClock(session_id="s1")
        assert call_count == 1  # 構築時に1度だけ呼ばれている

        first = clock.started_wall_ms
        second = clock.started_wall_ms
        third = clock.started_wall_ms

        assert first == second == third
        # アクセスのたびに time.time_ns が再度呼ばれていないことを確認する。
        assert call_count == 1

    def test_started_monotonic_ns_matches_perf_counter_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 123_456_789)

        clock = SessionClock(session_id="s1")

        assert clock.started_monotonic_ns == 123_456_789


class TestNowMsMonotonicity:
    """`now_ms()` が単調非減少であり、経過 ms を返すことを固定する。"""

    def test_now_ms_is_zero_at_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 500_000_000)

        clock = SessionClock(session_id="s1")

        # 構築直後、perf_counter_ns が変化していなければ経過は 0ms。
        assert clock.now_ms() == pytest.approx(0.0)

    def test_now_ms_reflects_elapsed_perf_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)

        perf_values = iter([1_000_000_000])  # 構築時のアンカ
        monkeypatch.setattr(time, "perf_counter_ns", lambda: next(perf_values))

        clock = SessionClock(session_id="s1")

        # 構築後は now_ms() 呼び出しのたびに perf_counter_ns を新たに読む。
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 1_005_000_000)  # +5ms
        assert clock.now_ms() == pytest.approx(5.0)

        monkeypatch.setattr(time, "perf_counter_ns", lambda: 1_012_000_000)  # +12ms
        assert clock.now_ms() == pytest.approx(12.0)

    def test_now_ms_is_monotonically_non_decreasing_across_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        base_ns = 2_000_000_000
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: base_ns)

        clock = SessionClock(session_id="s1")

        # 増加していく（一部は同一値=変化なしを含む）perf_counter_ns の系列で
        # now_ms() が単調非減少であることを確認する。
        sequence_ns = [
            base_ns,
            base_ns + 1_000_000,
            base_ns + 1_000_000,  # 変化なし（同一値）も許容される
            base_ns + 3_500_000,
            base_ns + 10_000_000,
        ]
        seq_iter = iter(sequence_ns)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: next(seq_iter))

        observed = [clock.now_ms() for _ in sequence_ns]

        for earlier, later in zip(observed, observed[1:]):
            assert later >= earlier

        assert observed == [pytest.approx(v) for v in [0.0, 1.0, 1.0, 3.5, 10.0]]

    def test_now_ms_is_not_affected_by_wall_clock_rewind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 壁時計（time.time_ns）が NTP 補正等で巻き戻っても、now_ms() の
        # 計算は perf_counter_ns のみに依存し影響を受けないことを確認する。
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 5_000_000_000)

        clock = SessionClock(session_id="s1")

        monkeypatch.setattr(time, "perf_counter_ns", lambda: 5_020_000_000)  # +20ms
        before_rewind = clock.now_ms()

        # 壁時計を大きく巻き戻す。
        monkeypatch.setattr(time, "time_ns", lambda: 1_600_000_000_000_000_000)

        # perf_counter_ns は変化させない -> now_ms() は変わらないはず。
        after_rewind = clock.now_ms()

        assert before_rewind == pytest.approx(after_rewind)
        assert after_rewind == pytest.approx(20.0)


class TestToWallMs:
    """`to_wall_ms()` がアンカを起点にした純粋な換算関数であることを固定する。"""

    def test_to_wall_ms_adds_elapsed_to_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fixed_wall_ns = 1_700_000_000_000_000_000
        monkeypatch.setattr(time, "time_ns", lambda: fixed_wall_ns)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock = SessionClock(session_id="s1")
        anchor_ms = clock.started_wall_ms

        assert clock.to_wall_ms(0.0) == pytest.approx(anchor_ms)
        assert clock.to_wall_ms(1500.0) == pytest.approx(anchor_ms + 1500.0)

    def test_to_wall_ms_does_not_read_wall_clock_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # to_wall_ms() は新たな壁時計の読み取りを行わない、純粋な計算
        # であることを固定する: time.time_ns の呼び出し回数が構築時の
        # 1回のみで増えないことを確認する。
        call_count = 0

        def fake_time_ns() -> int:
            nonlocal call_count
            call_count += 1
            return 1_700_000_000_000_000_000

        monkeypatch.setattr(time, "time_ns", fake_time_ns)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock = SessionClock(session_id="s1")
        assert call_count == 1

        clock.to_wall_ms(0.0)
        clock.to_wall_ms(1000.0)
        clock.to_wall_ms(9999.0)

        assert call_count == 1

    def test_to_wall_ms_is_deterministic_pure_function_of_input(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock = SessionClock(session_id="s1")

        assert clock.to_wall_ms(42.0) == clock.to_wall_ms(42.0)


class TestSharedInstanceFriendliness:
    """単一インスタンス共有を妨げる隠れた状態が無いことを固定する。"""

    def test_two_independent_instances_do_not_share_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 0)

        clock_a = SessionClock(session_id="a")
        clock_b = SessionClock(session_id="b")

        assert clock_a.session_id != clock_b.session_id
        assert clock_a.session_id == "a"
        assert clock_b.session_id == "b"

    def test_now_ms_has_no_side_effects_beyond_elapsed_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # now_ms() を複数回呼んでも started_wall_ms / started_monotonic_ns
        # は変化しない（構築時の値のまま）ことを確認する。
        monkeypatch.setattr(time, "time_ns", lambda: 1_700_000_000_000_000_000)
        monkeypatch.setattr(time, "perf_counter_ns", lambda: 1_000_000_000)

        clock = SessionClock(session_id="s1")
        anchor_wall = clock.started_wall_ms
        anchor_mono = clock.started_monotonic_ns

        monkeypatch.setattr(time, "perf_counter_ns", lambda: 2_000_000_000)
        clock.now_ms()
        clock.now_ms()

        assert clock.started_wall_ms == anchor_wall
        assert clock.started_monotonic_ns == anchor_mono


class TestDocstringDocumentsCrossSessionRisk:
    """セッションをまたぐ時刻比較は to_wall_ms() を通す必要がある旨の明記を固定する。"""

    def test_module_or_class_docstring_mentions_cross_session_caveat(self) -> None:
        import sensing_foundation.timebase as timebase_module

        combined_doc = (timebase_module.__doc__ or "") + (SessionClock.__doc__ or "")
        assert "to_wall_ms" in combined_doc
