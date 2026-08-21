"""`sensing_foundation.sources.simulated` に対するテスト（タスク 3.2）。

観測可能な完了状態（tasks.md 3.2）を固定する:

- 実機も SDK も無い環境で、合成入力から共通表現の系列が取得でき、
  供給枚数と取得枚数が一致する
- 供給関数が `None` を返したら `frames()` は例外なく終わる
- 供給された配列の shape/dtype が `profile` と食い違えば
  `SourceContractError` を送出する（契約違反はサイレントに通さない）
- ドレインを行わない（`drain_enabled` を呼び出し側の指定に関わらず常に
  偽へ固定し、`_drain_latest()` 自体も常に `(None, 0)` を返す。二重防御）
- `kind` は `SourceKind.SIMULATED`
- `seq` には呼び出し通し番号を使う（合成データの中身に埋め込まれた `seq`
  ではない）ため、`synthetic.make_supplier` へ非連続な `seqs` を渡しても
  `SimulatedSource` 越しに観測される欠落（`gap_before` / `frames_missing`）
  は常に 0 になる
- 投擲物理・ノイズ生成に相当する語彙を本体モジュールが持たない

要件: 4.1, 4.4, 4.5
"""

from __future__ import annotations

import inspect

import numpy as np
import pytest
from synthetic import make_supplier

from sensing_foundation import sources as sources_pkg
from sensing_foundation.config import CaptureConfig
from sensing_foundation.errors import SourceContractError
from sensing_foundation.metrics import CaptureMetrics
from sensing_foundation.sources import simulated as simulated_module
from sensing_foundation.sources.simulated import SimulatedSource
from sensing_foundation.timebase import SessionClock
from sensing_foundation.types import CameraIntrinsics, SourceKind, StreamProfile

WIDTH_PX = 8
HEIGHT_PX = 6


class FakeLogger:
    """`Logger` プロトコルに構造的に適合する、`emit()` 呼び出しを記録するだけの偽実装。"""

    def __init__(self) -> None:
        self.enabled = True
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def emit(self, stage: str, event: str, /, **data: object) -> None:
        self.calls.append((stage, event, dict(data)))

    def stage(self, stage: str):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def timed(self, stage: str, event: str, /, **data: object):  # pragma: no cover
        raise NotImplementedError

    def stats(self):  # pragma: no cover - 本テストでは未使用
        raise NotImplementedError

    def close(self, timeout_ms: float = 1000.0) -> None:  # pragma: no cover
        return None


def _make_profile() -> StreamProfile:
    intrinsics = CameraIntrinsics(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fx_px=50.0,
        fy_px=50.0,
        ppx_px=4.0,
        ppy_px=3.0,
        model="brown_conrady",
        coeffs=(0.0, 0.0, 0.0, 0.0, 0.0),
    )
    return StreamProfile(
        width_px=WIDTH_PX,
        height_px=HEIGHT_PX,
        fps=30,
        depth_scale_mm=1.0,
        color_enabled=False,
        intrinsics=intrinsics,
    )


def _make_metrics_and_clock() -> tuple[CaptureMetrics, SessionClock]:
    clock = SessionClock(session_id="test-simulated-source")
    metrics = CaptureMetrics(FakeLogger(), clock, sysstat=None)
    return metrics, clock


def _make_source(
    *,
    seqs: list[int],
    delay_s: float = 0.0,
    violate_at: int | None = None,
    violate_kind: str = "shape",
    capture_config: CaptureConfig | None = None,
) -> SimulatedSource:
    supplier = make_supplier(
        WIDTH_PX,
        HEIGHT_PX,
        seqs,
        delay_s=delay_s,
        violate_at=violate_at,
        violate_kind=violate_kind,
    )
    metrics, clock = _make_metrics_and_clock()
    return SimulatedSource(
        supplier,
        _make_profile(),
        metrics,
        clock=clock,
        capture_config=capture_config,
    )


class TestSuppliedCountEqualsAcquiredCount:
    """観測可能な完了状態: 供給枚数と取得枚数が一致する。"""

    def test_finite_series_yields_exactly_as_many_frames_as_supplied(self) -> None:
        seqs = [0, 1, 2, 3, 4]
        source = _make_source(seqs=seqs)

        with source:
            frames = list(source.frames())

        assert len(frames) == len(seqs)
        assert [f.index for f in frames] == [0, 1, 2, 3, 4]

    def test_empty_supply_yields_zero_frames(self) -> None:
        source = _make_source(seqs=[])

        with source:
            frames = list(source.frames())

        assert frames == []
        assert source.stats.frames_yielded == 0


class TestEndOfStream:
    """観測可能な完了状態: 供給関数が終端を返したら例外なくイテレーションが終わる。"""

    def test_none_from_supplier_ends_iteration_without_error(self) -> None:
        source = _make_source(seqs=[0, 1, 2])

        with source:
            # 供給元にある枚数より多く取ろうとしても、StopIteration/例外の
            # 代わりに frames() が単に終わる（呼び出し側からは通常の
            # イテレータ終了として見える）。
            frames = list(source.frames())

        assert len(frames) == 3
        assert source.stats.frames_yielded == 3


class TestContractViolation:
    """観測可能な完了状態: shape/dtype 不一致は SourceContractError で拒否される。"""

    def test_shape_mismatch_raises_source_contract_error(self) -> None:
        source = _make_source(seqs=[0, 1, 2], violate_at=1, violate_kind="shape")

        with pytest.raises(SourceContractError):
            with source:
                list(source.frames())

    def test_dtype_mismatch_raises_source_contract_error(self) -> None:
        source = _make_source(seqs=[0, 1, 2], violate_at=1, violate_kind="dtype")

        with pytest.raises(SourceContractError):
            with source:
                list(source.frames())

    def test_both_shape_and_dtype_mismatch_raises_source_contract_error(self) -> None:
        source = _make_source(seqs=[0, 1, 2], violate_at=0, violate_kind="both")

        with pytest.raises(SourceContractError):
            with source:
                list(source.frames())

    def test_valid_frames_before_violation_are_yielded_first(self) -> None:
        # violate_at=2 なので index 0, 1 は正常に yield され、index 2 で
        # 契約違反として例外になる（サイレントに通過しない）。
        source = _make_source(seqs=[0, 1, 2, 3], violate_at=2, violate_kind="shape")

        collected = []
        with pytest.raises(SourceContractError):
            with source:
                for frame in source.frames():
                    collected.append(frame)

        assert len(collected) == 2


class TestDrainNeverInvoked:
    """観測可能な完了状態: 再生・合成では取りこぼしを新たに作らない（二重防御）。"""

    def test_drain_enabled_is_forced_false_regardless_of_caller_supplied_config(self) -> None:
        # 呼び出し側が drain_enabled=True を渡しても、SimulatedSource が
        # BaseFrameSource へ渡す時点で常に False に固定される（防御 a）。
        source = _make_source(
            seqs=[0, 1, 2],
            capture_config=CaptureConfig(drain_enabled=True),
        )

        assert source._drain_enabled is False  # noqa: SLF001 - 防御(a)のホワイトボックス検証

    def test_drain_latest_always_returns_none_and_zero_unconditionally(self) -> None:
        # _drain_latest() 自体を直接呼んでも、常に (None, 0) を返す（防御 b）。
        source = _make_source(seqs=[0, 1, 2])

        assert source._drain_latest() == (None, 0)
        assert source._drain_latest() == (None, 0)

    def test_no_discard_count_accumulates_even_with_default_drain_enabled_config(self) -> None:
        source = _make_source(seqs=[0, 1, 2, 3, 4])

        with source:
            frames = list(source.frames())

        assert all(f.dropped_before == 0 for f in frames)
        assert source.stats.frames_dropped == 0


class TestSeqUsesCallIndexNotEmbeddedContent:
    """`seq` は呼び出し通し番号を使うため、非連続な供給でも欠落は検出されない。

    これは design.md が明示しない実装判断（モジュール docstring「実装判断1」）
    であり、この選択自体をここで固定する。欠落検出アルゴリズムそのものの
    正しさはタスク 3.1 の test_source.py が別途固定済みであり、ここでは
    再検証しない。
    """

    def test_gappy_supply_seqs_do_not_produce_gap_before_via_this_adapter(self) -> None:
        # synthetic.py 上は 0, 1, 3, 4 と非連続だが、供給関数の呼び出し位置
        # (index) は 0, 1, 2, 3 と連続する。SimulatedSource は index を
        # RawFrame.seq に使うため、BaseFrameSource からは欠落が見えない。
        source = _make_source(seqs=[0, 1, 3, 4])

        with source:
            frames = list(source.frames())

        assert [f.seq for f in frames] == [0, 1, 2, 3]
        assert all(f.gap_before == 0 for f in frames)
        assert source.stats.frames_missing == 0


class TestKindIsSimulated:
    def test_kind_property_returns_simulated(self) -> None:
        source = _make_source(seqs=[0])

        assert source.kind == SourceKind.SIMULATED


class TestNoHardwareOrSdkDependency:
    """観測可能な完了状態: 実機も SDK も無い環境で動作する。"""

    def test_module_does_not_import_pyrealsense2(self) -> None:
        source_code = inspect.getsource(simulated_module)
        assert "pyrealsense2" not in source_code
        assert "import rs" not in source_code

    def test_end_to_end_common_representation_series_without_hardware(self) -> None:
        # 環境に RealSense SDK / デバイスが一切無い状態（本テスト実行環境
        # そのもの）で、共通表現（CaptureFrame）の系列が得られることを
        # end-to-end で確認する。
        source = _make_source(seqs=[0, 1, 2])

        with source:
            frames = list(source.frames())

        assert len(frames) == 3
        for frame in frames:
            assert frame.depth.shape == (HEIGHT_PX, WIDTH_PX)
            assert frame.depth.dtype == np.uint16
            assert frame.source == SourceKind.SIMULATED


class TestNoThrowPhysicsOrNoiseLogic:
    """設計整合性のサニティチェック: 投擲物理・ノイズ生成の語彙を持たない。

    タスク 1.7（`tests/sensing_foundation/synthetic.py`）が同種の設計制約
    （要件 4.5）を負っており、本モジュールにも同じ制約を適用する。
    """

    # 「trajectory」は含めない: 本モジュールの docstring は将来の連携先
    # Spec 名「trajectory-simulator」を正当に言及する（design.md 自体が
    # 同じ言及をする）。これは物理計算のコードではなくドキュメント上の
    # 連携先の名前であるため、誤検知になる語を除外している。
    _BANNED_TERMS = (
        "gravity",
        "velocity",
        "acceleration",
        "projectile",
        "noise_std",
        "np.random",
        "numpy.random",
    )

    def test_source_module_has_no_physics_or_noise_vocabulary(self) -> None:
        source_code = inspect.getsource(simulated_module).lower()
        for term in self._BANNED_TERMS:
            assert term not in source_code, f"物理・ノイズ用語 {term!r} が含まれている"

    def test_sources_package_marker_has_no_physics_or_noise_vocabulary(self) -> None:
        package_code = inspect.getsource(sources_pkg).lower()
        for term in self._BANNED_TERMS:
            assert term not in package_code, f"物理・ノイズ用語 {term!r} が含まれている"
