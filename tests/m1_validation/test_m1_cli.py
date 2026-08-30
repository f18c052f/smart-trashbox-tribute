"""コマンド入口の検証（タスク 8.1、要件 12.6, 13.5, 13.6, 13.7）。

観測可能な完了状態（tasks.md 8.1）を固定する:

- **全サブコマンドが合成入力で成功終了する**
- **解決済み設定の表示が優先順位どおりになる**

あわせて design.md「CLI」が定める点も固定する:

- 設定の解決順序は **実行時指定 > 環境変数 > 設定ファイル > 既定値**
  （**4層すべてに違う値を置いて、勝つ層を1つずつ確かめる**）
- 各サブコマンドのヘルプに**実機の要否**を明示する（3値をそのまま反映する）
- `--allow-unverified` は**明示的に与えたときのみ**有効
- ヘルプに「既定値は暫定の評価候補であり必須性能ではない」旨を明記する
- **上流由来の値の調達は入口が担うが、入口は上流パッケージを直接 import
  しない**（ログ器・追跡設定・ストリーム識別のいずれも接点／継ぎ目を経由する）
- 調達した値を**本 Spec の設定へ写し取らない**

**追跡パイプラインはダブルにする**（`test_m1_runner.py` と同じ理由）。本タスクが
持つのは「各層へ委譲する並び」であり、検出そのものは上流の責務である。
**ストリーム識別も注入する**——上流の合成入力は
`StreamProfile.intrinsics` を常に `None` で返す（合成入力にカメラ内部
パラメータという概念が無いため）ので、実機なしで整合性検査を通す経路が
他に無い。`world_frame_calibration/cli.py` が `source` / `logger` に対して
採ったのと同じ「argv では表現できない注入点」である。
"""

from __future__ import annotations

import ast
import json
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

import numpy as np
import pytest
from m1fixtures import verification_summary, write_calibration, write_layout

from flying_object_tracking import (
    HANDOFF_VERSION,
    CameraPoint,
    CameraTrack,
    CoordinateFrame,
    SourceKind,
    TrackPoint,
    TrackState,
    TrackUpdate,
)
from m1_validation import cli as cli_module
from m1_validation import runner as runner_module
from m1_validation import seam as seam_module
from m1_validation import upstream as upstream_module
from m1_validation.cli import main
from m1_validation.config import PROVISIONAL_NOTICE
from m1_validation.errors import M1ConfigError
from m1_validation.upstream import UpstreamGateway, resolve_runtime_settings
from sensing_foundation import INVALID_DEPTH_RAW, SensingConfigError
from world_frame_calibration import (
    CalibrationConfigError,
    to_intrinsics,
    to_signature,
)

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "m1_validation"

WIDTH_PX = 8
HEIGHT_PX = 6
FPS = 30
DEPTH_SCALE_MM = 1.0
FX_PX = 385.0

LAYOUT_ID = "throw-a"

#: 重力加速度（mm/ms²）。**テスト局所のリテラル**であり、実装の定数を
#: 参照しない（Implementation Notes タスク 4.1: 参照解を実装の定数から
#: 組むと、その定数を変えたとき参照解が一緒に動いて差が消える）。
G_MM_MS2 = 0.00980665

#: 合成軌道（World 座標系）。**基準になる量は 0 以外にする**
#: （Implementation Notes タスク 2.2）。
T0_MS = 5000.0
DT_MS = 33.0
SAMPLE_COUNT = 8
X0_MM, Y0_MM, Z0_MM = -1500.0, 0.0, 1500.0
VX, VY, VZ = 3.0, 0.2, -1.0

#: キャリブレーションの平行移動（World = camera + translation）。
TRANSLATION_MM = (0.0, 0.0, -1000.0)


# ---------------------------------------------------------------------------
# 合成入力
# ---------------------------------------------------------------------------


def _world_at(t_ms: float) -> tuple[float, float, float]:
    dt = t_ms - T0_MS
    return (
        X0_MM + VX * dt,
        Y0_MM + VY * dt,
        Z0_MM + VZ * dt - 0.5 * G_MM_MS2 * dt * dt,
    )


def _impact_point_world_mm() -> tuple[float, float, float]:
    """解析解による落下地点（z = 0 との交点）。テスト局所のリテラルだけで解く。"""
    a = 0.5 * G_MM_MS2
    t_impact = (-VZ - math.sqrt(VZ * VZ + 4.0 * a * Z0_MM)) / (-2.0 * a)
    return (X0_MM + VX * t_impact, Y0_MM + VY * t_impact, 0.0)


def _camera_points() -> list[CameraPoint]:
    points: list[CameraPoint] = []
    for index in range(SAMPLE_COUNT):
        t_ms = T0_MS + DT_MS * index
        world = _world_at(t_ms)
        points.append(
            CameraPoint(
                frame=CoordinateFrame.CAMERA,
                t_ms=t_ms,
                x_mm=world[0] - TRANSLATION_MM[0],
                y_mm=world[1] - TRANSLATION_MM[1],
                z_mm=world[2] - TRANSLATION_MM[2],
                valid_depth_px=40,
                depth_spread_mm=10.0,
                apparent_diameter_px=9.0,
                expected_diameter_px=8.5,
                intrinsics_source="stream_profile",
            )
        )
    return points


class FakePipeline:
    """フレーム1枚につき1点を追加する追跡パイプライン（`process()` だけを持つ）。"""

    def __init__(self, points: list[CameraPoint], *, track_id: int = 7) -> None:
        self._points = points
        self._track_id = track_id
        self._appended: list[TrackPoint] = []
        self.logger_handles: list[object] = []
        self.tracking_settings: list[object] = []

    def process(self, frame: object) -> TrackUpdate:
        index = len(self._appended)
        appended: TrackPoint | None = None
        if index < len(self._points):
            appended = TrackPoint(
                point=self._points[index],
                frame_index=index,
                frame_seq=1000 + index,
                gap_before=0,
                rivals=0,
            )
            self._appended.append(appended)
        return TrackUpdate(
            track=self._track(),
            appended=appended,
            candidates=1 if appended is not None else 0,
            rejections=(),
            point_failures=(),
        )

    def _track(self) -> CameraTrack:
        return CameraTrack(
            handoff_version=HANDOFF_VERSION,
            frame=CoordinateFrame.CAMERA,
            track_id=self._track_id,
            started_t_ms=T0_MS,
            points=tuple(self._appended),
            state=TrackState.TRACKING if self._appended else TrackState.IDLE,
            end_reason=None,
            source=SourceKind.SIMULATED,
            detector_kind="depth_band",
        )


def _depth_supplier(frame_count: int = SAMPLE_COUNT):
    def supplier(index: int):
        if index >= frame_count:
            return None
        return np.full((HEIGHT_PX, WIDTH_PX), INVALID_DEPTH_RAW + 1, dtype=np.uint16)

    return supplier


# ---------------------------------------------------------------------------
# ストリーム識別のダブル（`to_signature` / `to_intrinsics` は属性しか見ない）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeIntrinsics:
    width_px: int = WIDTH_PX
    height_px: int = HEIGHT_PX
    fx_px: float = FX_PX
    fy_px: float = 385.0
    ppx_px: float = 320.0
    ppy_px: float = 240.0
    model: str = "brown_conrady"
    coeffs: tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class FakeStreamProfile:
    width_px: int = WIDTH_PX
    height_px: int = HEIGHT_PX
    fps: int = FPS
    depth_scale_mm: float = DEPTH_SCALE_MM
    color_enabled: bool = False
    intrinsics: object | None = field(default_factory=FakeIntrinsics)


class ProfileGateway:
    """実物の `UpstreamGateway` を包み、**ストリーム識別だけ**を差し替える。

    取得・ログ・保存・集計はすべて実物へ委ねる。差し替えるのは
    `stream_profile()` の1つだけであり、上流の合成入力が内部パラメータを
    持たないという制約を回避するためだけに存在する。
    """

    def __init__(self, inner: UpstreamGateway, profile: object) -> None:
        self._inner = inner
        self._profile = profile
        self.stream_profile_calls = 0
        #: 受け取った供給関数と再生速度。**捨てない。**
        #: `**kwargs` で握り潰すと、実物の `stream_profile()` が
        #: `open_source()` へ渡すべき `supplier` を CLI が落としても
        #: どのテストも落ちなくなる——合成入力では
        #: `open_source()` が `supplier` 無しを拒否するので、
        #: 「実機なしで投擲を回す」経路（要件 12.1）が実運用で壊れる。
        self.stream_profile_suppliers: list[object] = []
        self.stream_profile_speeds: list[object] = []
        #: 記録の読み出しが**実際に走ったか**。引数の検査が実作業のあとに
        #: 来ていないことを、文面ではなく行動で観測するための計数である。
        self.load_records_calls: list[Path] = []

    def stream_profile(
        self, *, supplier: object = None, speed: str = "fast"
    ) -> object:
        self.stream_profile_calls += 1
        self.stream_profile_suppliers.append(supplier)
        self.stream_profile_speeds.append(speed)
        return self._profile

    def load_records(self, path: Path) -> object:
        self.load_records_calls.append(Path(path))
        return self._inner.load_records(Path(path))

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# 環境
# ---------------------------------------------------------------------------


@dataclass
class Env:
    """1テスト分の合成環境（パスと共通引数）。"""

    tmp_path: Path
    layout_path: Path
    calibration_path: Path
    records_path: Path
    truth_path: Path
    log_path: Path
    output_root: Path
    session_id: str
    gateway: ProfileGateway

    def common(self, *, with_session_id: bool = True) -> list[str]:
        argv = [
            "--layout-file",
            str(self.layout_path),
            "--layout-id",
            LAYOUT_ID,
            "--output-root",
            str(self.output_root),
            "--bootstrap-iterations",
            "8",
        ]
        if with_session_id:
            argv += ["--session-id", self.session_id]
        return argv


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    session_id = "cli-session"
    spec = resolve_runtime_settings(
        file=None,
        env={},
        overrides={
            "source": "simulated",
            "width_px": WIDTH_PX,
            "height_px": HEIGHT_PX,
            "fps": FPS,
            "logging_path": str(tmp_path / "logs"),
            "recording_root": str(tmp_path / "sessions"),
        },
    )
    inner = UpstreamGateway.open(session_id=session_id, source_spec=spec)
    gateway = ProfileGateway(inner, FakeStreamProfile())
    yield Env(
        tmp_path=tmp_path,
        layout_path=write_layout(tmp_path),
        calibration_path=write_calibration(
            tmp_path,
            width_px=WIDTH_PX,
            height_px=HEIGHT_PX,
            fx_px=FX_PX,
            verification=verification_summary(),
        ),
        records_path=tmp_path / "throws.ndjson",
        truth_path=tmp_path / "truth.json",
        log_path=tmp_path / "logs" / f"{session_id}.ndjson",
        output_root=tmp_path / "out",
        session_id=session_id,
        gateway=gateway,
    )
    inner.close()


def _run(
    env: Env,
    argv: Sequence[str],
    capsys: pytest.CaptureFixture[str],
    **kwargs: object,
) -> tuple[int, dict[str, object], str]:
    code = main(list(argv), gateway=env.gateway, **kwargs)  # type: ignore[arg-type]
    captured = capsys.readouterr()
    payload: dict[str, object] = {}
    if captured.out.strip():
        payload = json.loads(captured.out)
    return code, payload, captured.err


def _write_truth(env: Env, record_ids: Sequence[str], *, extra_id: str = "") -> None:
    impact = _impact_point_world_mm()
    entries: dict[str, object] = {
        record_id: {
            "impact_point_world_mm": [impact[0] + 37.0, impact[1] - 21.0, 0.0],
            "impact_point_source": "メジャー実測。原点マーカー中心から床上を計測",
            "impact_point_uncertainty_mm": 15.0,
        }
        for record_id in record_ids
    }
    if extra_id:
        entries[extra_id] = {
            "impact_point_world_mm": [0.0, 0.0, 0.0],
            "impact_point_source": "メジャー実測",
            "impact_point_uncertainty_mm": 15.0,
        }
    env.truth_path.write_text(
        json.dumps(
            {
                "truth_format_version": "1.0",
                "layout_id": LAYOUT_ID,
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _throw(
    env: Env,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    record_id: str = "throw-0001",
    calibration_path: Path | None = None,
    extra_argv: Sequence[str] = (),
    supplier: object | None = None,
) -> tuple[int, dict[str, object], str]:
    monkeypatch.setattr(
        runner_module, "open_tracking", lambda *a, **k: FakePipeline(_camera_points())
    )
    return _run(
        env,
        [
            "run-throw",
            *env.common(),
            "--calibration",
            str(calibration_path or env.calibration_path),
            "--record-id",
            record_id,
            "--records",
            str(env.records_path),
            *extra_argv,
        ],
        capsys,
        supplier=_depth_supplier() if supplier is None else supplier,
    )


def _runtime_set_argv(tmp_path: Path, *, suffix: str) -> list[str]:
    """上流 `RuntimeSettings` を**実行時指定だけで**合成入力へ寄せる引数。

    これを与えると `main()` は `gateway` の注入なしに
    `upstream.resolve_runtime_settings()` → `UpstreamGateway.open()` を
    実物で通る（実機は一切要らない）。
    """
    return [
        "--runtime-set",
        "source=simulated",
        "--runtime-set",
        f"width_px={WIDTH_PX}",
        "--runtime-set",
        f"height_px={HEIGHT_PX}",
        "--runtime-set",
        f"fps={FPS}",
        "--runtime-set",
        f"logging_path={tmp_path / f'logs-{suffix}'}",
        "--runtime-set",
        f"recording_root={tmp_path / f'sessions-{suffix}'}",
    ]


def _throwing_argv(env: Env, command: str, *, record_id: str) -> list[str]:
    """投擲を伴うサブコマンド（`run-throw` / `bench-overhead`）の共通引数。

    2つは受け取る引数が違う（`run-throw` は記録の識別子と追記先、
    `bench-overhead` は取りこぼしの出所となるログ）ので、ここで振り分ける。
    """
    argv = [command, *env.common(), "--calibration", str(env.calibration_path)]
    if command == "run-throw":
        return [*argv, "--record-id", record_id, "--records", str(env.records_path)]
    return [*argv, "--log", str(env.log_path), "--overhead-cycles", "1"]


def _evaluation_argv(
    env: Env, command: str, *, log: bool = True, with_session_id: bool = True
) -> list[str]:
    argv = [
        command,
        *env.common(with_session_id=with_session_id),
        "--records",
        str(env.records_path),
        "--truth",
        str(env.truth_path),
    ]
    if log:
        argv += ["--log", str(env.log_path)]
    return argv


@pytest.fixture
def populated(
    env: Env, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Env:
    """2投擲ぶんの記録と真値を用意した環境。"""
    for record_id in ("throw-0001", "throw-0002"):
        code, _, err = _throw(env, capsys, monkeypatch, record_id=record_id)
        assert code == 0, err
    _write_truth(env, ("throw-0001", "throw-0002"))
    return env


# ---------------------------------------------------------------------------
# サブコマンドの一覧と、実機の要否（要件 12.6）
# ---------------------------------------------------------------------------

#: design.md「CLI」のサブコマンド表。**テスト局所のリテラル**であり、
#: 実装の定数を import して自分自身と比べない（空振り形3）。
EXPECTED_HARDWARE = {
    "run-throw": "要",
    "ingest-truth": "不要",
    "measure": "不要",
    "attribute": "不要",
    "judge-oq27": "不要",
    "material-oq05": "不要",
    "budget": "不要",
    "bench-overhead": "要（推奨）",
    "report": "不要",
    "plot": "不要",
}


def _help_of(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as excinfo:
        main(list(argv))
    assert excinfo.value.code == 0
    return capsys.readouterr().out


class TestSubcommands:
    def test_all_ten_subcommands_exist(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**サブコマンドを1つでも落とすと落ちる。**"""
        text = _help_of(["--help"], capsys)
        for command in EXPECTED_HARDWARE:
            assert command in text, command

    @pytest.mark.parametrize("command", sorted(EXPECTED_HARDWARE))
    def test_help_states_whether_hardware_is_required(
        self, command: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**各サブコマンドのヘルプに実機の要否を明示する**（要件 12.6）。

        3値（要 / 不要 / 要（推奨））をそのまま反映していることを、
        **一致と非一致の対**で固定する——「要」だけを見ると
        「要（推奨）」と区別が付かず、取り違えが素通りする。
        """
        text = _help_of([command, "--help"], capsys)
        expected = EXPECTED_HARDWARE[command]
        assert f"実機: {expected}。" in text
        for other in {"要", "不要", "要（推奨）"} - {expected}:
            assert f"実機: {other}。" not in text

    def test_hardware_requirement_has_all_three_values(self) -> None:
        """3値がすべて実際に使われている（表が1値へ潰れていない）。"""
        assert set(EXPECTED_HARDWARE.values()) == {"要", "不要", "要（推奨）"}

    def test_top_level_help_lists_the_hardware_requirement(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`bench-overhead` だけが「要（推奨）」であることを一覧側でも固定する。"""
        text = _help_of(["--help"], capsys)
        assert "実機: 要（推奨）。" in text


class TestProvisionalNotice:
    def test_help_says_defaults_are_provisional(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ヘルプに「暫定の評価候補であり必須性能ではない」旨を明記する（要件 13.7）。"""
        text = _help_of(["--help"], capsys)
        assert "暫定の評価候補" in text
        assert "必須性能ではない" in text

    def test_help_reuses_the_settings_notice(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """注記を**再発明しない**（`config.PROVISIONAL_NOTICE` をそのまま出す）。"""
        text = _help_of(["--help"], capsys)
        assert PROVISIONAL_NOTICE in text

    def test_help_says_segment3_is_a_holdover(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """`segment3_assumed_ms` が**据え置き**である旨（タスク 6.3 の申し送り）。"""
        text = _help_of(["--help"], capsys)
        assert "segment3_assumed_ms" in text
        assert "据え置き" in text
        assert "実測値ではない" in text


# ---------------------------------------------------------------------------
# 設定の解決順序（要件 13.5）
# ---------------------------------------------------------------------------

#: 4層すべてに**違う値**を置く。実装の既定値（20 / 200）と重ならない値に
#: すること（Implementation Notes タスク 6.1: テストが渡す設定値が実装の
#: 既定値と一致していると、設定を無視する実装が区別できない）。
FILE_THROWS = 31
ENV_THROWS = 42
CLI_THROWS = 53
DEFAULT_THROWS = 20

FILE_ITERATIONS = 11
ENV_ITERATIONS = 12
CLI_ITERATIONS = 13
DEFAULT_ITERATIONS = 200


class TestSettingsResolutionOrder:
    """**実行時指定 > 環境変数 > 設定ファイル > 既定値**（要件 13.5）。"""

    @pytest.fixture
    def config_file(self, tmp_path: Path) -> Path:
        path = tmp_path / "m1.json"
        path.write_text(
            json.dumps(
                {
                    "min_valid_throws": FILE_THROWS,
                    "bootstrap_iterations": FILE_ITERATIONS,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _resolved(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        config_file: Path | None,
        env: bool,
        cli: bool,
    ) -> dict[str, object]:
        monkeypatch.delenv("STB_M1_MIN_VALID_THROWS", raising=False)
        monkeypatch.delenv("STB_M1_BOOTSTRAP_ITERATIONS", raising=False)
        if env:
            monkeypatch.setenv("STB_M1_MIN_VALID_THROWS", str(ENV_THROWS))
            monkeypatch.setenv("STB_M1_BOOTSTRAP_ITERATIONS", str(ENV_ITERATIONS))
        argv = [
            "measure",
            "--layout-file",
            str(write_layout(tmp_path)),
            "--layout-id",
            LAYOUT_ID,
            "--print-settings",
        ]
        if config_file is not None:
            argv += ["--config", str(config_file)]
        if cli:
            argv += [
                "--min-valid-throws",
                str(CLI_THROWS),
                "--bootstrap-iterations",
                str(CLI_ITERATIONS),
            ]
        assert main(argv) == 0
        payload = json.loads(capsys.readouterr().out)
        return payload["settings"]  # type: ignore[no-any-return]

    def test_runtime_argument_wins_over_every_other_layer(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        config_file: Path,
    ) -> None:
        settings = self._resolved(
            capsys, tmp_path, monkeypatch, config_file=config_file, env=True, cli=True
        )
        assert settings["trials"]["min_valid_throws"] == CLI_THROWS
        assert settings["attribution"]["bootstrap_iterations"] == CLI_ITERATIONS

    def test_environment_wins_when_no_runtime_argument(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        config_file: Path,
    ) -> None:
        settings = self._resolved(
            capsys, tmp_path, monkeypatch, config_file=config_file, env=True, cli=False
        )
        assert settings["trials"]["min_valid_throws"] == ENV_THROWS
        assert settings["attribution"]["bootstrap_iterations"] == ENV_ITERATIONS

    def test_config_file_wins_when_no_environment(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        config_file: Path,
    ) -> None:
        settings = self._resolved(
            capsys, tmp_path, monkeypatch, config_file=config_file, env=False, cli=False
        )
        assert settings["trials"]["min_valid_throws"] == FILE_THROWS
        assert settings["attribution"]["bootstrap_iterations"] == FILE_ITERATIONS

    def test_default_applies_when_nothing_is_given(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = self._resolved(
            capsys, tmp_path, monkeypatch, config_file=None, env=False, cli=False
        )
        assert settings["trials"]["min_valid_throws"] == DEFAULT_THROWS
        assert settings["attribution"]["bootstrap_iterations"] == DEFAULT_ITERATIONS

    def test_the_four_layer_values_are_all_distinct(self) -> None:
        """4層の値が互いに異なる（同値だと勝つ層を区別できない）。"""
        assert len({FILE_THROWS, ENV_THROWS, CLI_THROWS, DEFAULT_THROWS}) == 4
        assert (
            len(
                {
                    FILE_ITERATIONS,
                    ENV_ITERATIONS,
                    CLI_ITERATIONS,
                    DEFAULT_ITERATIONS,
                }
            )
            == 4
        )

    def test_resolution_order_is_displayed_highest_priority_first(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """表示される優先順位が**実際の振る舞いと同じ向き**である。

        振る舞い側（上の4本）と表示側は**別の経路**である。順序を入れ替える
        変異は片方だけでは死なない。
        """
        assert (
            main(
                [
                    "measure",
                    "--layout-file",
                    str(write_layout(tmp_path)),
                    "--layout-id",
                    LAYOUT_ID,
                    "--print-settings",
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["resolution_order"] == [
            "実行時指定",
            "環境変数",
            "設定ファイル",
            "既定値",
        ]

    def test_print_settings_shows_the_provisional_notice(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`--print-settings` の読み手が既定値を必須条件と取り違えないこと。"""
        assert (
            main(
                [
                    "measure",
                    "--layout-file",
                    str(write_layout(tmp_path)),
                    "--layout-id",
                    LAYOUT_ID,
                    "--print-settings",
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["settings"]["provisional_notice"] == PROVISIONAL_NOTICE
        notes = payload["notes"]
        assert any("segment3_assumed_ms" in note and "据え置き" in note for note in notes)

    def test_print_settings_does_not_run_the_subcommand(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`--print-settings` は本処理を実行せずに終わる（記録が無くても成功する）。"""
        assert (
            main(
                [
                    "measure",
                    "--layout-file",
                    str(write_layout(tmp_path)),
                    "--layout-id",
                    LAYOUT_ID,
                    "--records",
                    str(tmp_path / "missing.ndjson"),
                    "--print-settings",
                ]
            )
            == 0
        )
        payload = json.loads(capsys.readouterr().out)
        assert "settings" in payload

    def test_environment_prefix_is_not_the_upstream_one(
        self,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """上流の接頭辞（`STB_SF_` / `STB_FOT_`）を本 Spec の設定として読まない。"""
        monkeypatch.setenv("STB_SF_MIN_VALID_THROWS", str(ENV_THROWS))
        monkeypatch.setenv("STB_FOT_MIN_VALID_THROWS", str(ENV_THROWS))
        settings = self._resolved(
            capsys, tmp_path, monkeypatch, config_file=None, env=False, cli=False
        )
        assert settings["trials"]["min_valid_throws"] == DEFAULT_THROWS


class TestInvalidSettingsAreRejectedBeforeRunning:
    def test_invalid_value_exits_with_the_config_error_code(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """不正な設定を実行開始前に拒否する（要件 13.6）。"""
        code = main(
            [
                "measure",
                "--layout-file",
                str(write_layout(tmp_path)),
                "--layout-id",
                LAYOUT_ID,
                "--min-valid-throws",
                "0",
            ]
        )
        assert code == 2
        assert "設定エラー" in capsys.readouterr().err

    def test_missing_layout_is_rejected(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """レイアウトを与えないと動かない（要件 13.8 の構造的な担保）。"""
        assert main(["measure"]) == 2
        assert "layout_file" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# run-throw（要件 3.x）
# ---------------------------------------------------------------------------


class TestRunThrow:
    def test_synthetic_throw_succeeds_and_is_stored(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        code, payload, err = _throw(env, capsys, monkeypatch)
        assert code == 0, err
        assert payload["record_id"] == "throw-0001"
        assert payload["samples_appended"] == SAMPLE_COUNT
        assert payload["failed_reason"] is None
        assert env.records_path.exists()
        assert len(env.records_path.read_text(encoding="utf-8").splitlines()) == 1

    def test_stream_identity_is_procured_from_the_gateway(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ストリーム識別を**接点の取り出し入口から**調達する。"""
        code, _, err = _throw(env, capsys, monkeypatch)
        assert code == 0, err
        assert env.gateway.stream_profile_calls == 1

    def test_mismatching_stream_identity_stops_the_throw(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """調達した識別が保存された結果と食い違えば止まる（要件 1.6）。

        キャリブレーションファイルから識別を読み直す実装（＝現在の入力元を
        見ていない実装）は、この検査を**構造的に通せない**。
        """
        env.gateway._profile = FakeStreamProfile(width_px=WIDTH_PX + 4)
        code, _, err = _throw(env, capsys, monkeypatch)
        assert code == 1
        assert "profile_mismatch" in err

    def test_tracking_settings_are_procured_through_the_seam(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """追跡設定は継ぎ目の素通し入口から得る（`cli.py` は上流を import しない）。"""
        seen: list[object] = []
        real = cli_module.seam.resolve_tracking_settings

        def spy(**kwargs: object) -> object:
            seen.append(dict(kwargs))
            return real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(cli_module.seam, "resolve_tracking_settings", spy)
        code, _, err = _throw(env, capsys, monkeypatch)
        assert code == 0, err
        assert len(seen) == 1
        assert set(seen[0]) == {"config_path", "env", "overrides"}  # type: ignore[arg-type]

    def test_tracking_environment_and_overrides_are_not_filled_in_blank(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """`env` / `overrides` を空に埋めない（design.md「CLI」Integration）。

        埋めると上流の環境変数と実行時上書きが**黙って捨てられ**、本 CLI が
        掲げる優先順位と食い違う。**両方が実際に届いていること**を固定する。
        """
        seen: list[dict[str, object]] = []

        def spy(**kwargs: object) -> object:
            seen.append(dict(kwargs))
            return object()

        monkeypatch.setattr(cli_module.seam, "resolve_tracking_settings", spy)
        monkeypatch.setenv("STB_FOT_OUTPUT_ROOT", str(tmp_path / "fot-env"))
        code, _, err = _throw(
            env,
            capsys,
            monkeypatch,
            extra_argv=[
                "--tracking-set",
                "tracker_max_points=41",
                "--tracking-config",
                str(tmp_path / "fot.json"),
            ],
        )
        assert code == 0, err
        assert seen[0]["overrides"] == {"tracker_max_points": "41"}
        assert seen[0]["env"]["STB_FOT_OUTPUT_ROOT"] == str(tmp_path / "fot-env")  # type: ignore[index]
        assert seen[0]["config_path"] == tmp_path / "fot.json"

    def test_procured_values_are_not_copied_into_the_m1_settings(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """調達した2値を**本 Spec の設定へ写し取らない**。

        写し取ると、本 Spec が追跡の方式を決めたことになる（OQ-26 は上流の担当）。
        """
        seen: list[dict[str, object]] = []
        real = cli_module.M1Settings.resolve

        def spy(**kwargs: object) -> object:
            seen.append(dict(kwargs))
            return real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(cli_module.M1Settings, "resolve", spy)
        code, _, err = _throw(
            env, capsys, monkeypatch, extra_argv=["--tracking-set", "tracker_max_points=41"]
        )
        assert code == 0, err
        overrides = seen[0]["overrides"]
        assert "tracker_max_points" not in overrides  # type: ignore[operator]
        assert not any(
            key.startswith("tracking") or key in {"signature", "intrinsics", "logger"}
            for key in overrides  # type: ignore[union-attr]
        )

    def test_failed_throw_is_recorded_and_reported_by_the_exit_code(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """検出が1点も出ない投擲は**記録に残したうえで**失敗として終わる（要件 3.8）。"""
        monkeypatch.setattr(
            runner_module, "open_tracking", lambda *a, **k: FakePipeline([])
        )
        code, payload, _ = _run(
            env,
            [
                "run-throw",
                *env.common(),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-fail",
                "--records",
                str(env.records_path),
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 1
        assert payload["failed_reason"] == "no_valid_sample"
        assert env.records_path.exists()


class TestAllowUnverified:
    """`--allow-unverified` は**明示的に与えたときのみ**有効（要件 2.2）。"""

    def _unverified_calibration(self, env: Env) -> Path:
        path = env.tmp_path / "unverified.json"
        payload = json.loads(env.calibration_path.read_text(encoding="utf-8"))
        payload["verification"] = verification_summary(verdict="failed")
        payload["calibration_id"] = "cal-unverified"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_unverified_calibration_is_refused_by_default(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        code, _, err = _throw(
            env, capsys, monkeypatch, calibration_path=self._unverified_calibration(env)
        )
        assert code == 1
        assert "calibration_not_verified" in err

    def test_explicit_permission_allows_the_run(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        code, payload, err = _throw(
            env,
            capsys,
            monkeypatch,
            calibration_path=self._unverified_calibration(env),
            extra_argv=["--allow-unverified"],
        )
        assert code == 0, err
        assert payload["allow_unverified"] is True

    def test_environment_variable_cannot_grant_the_permission(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """許可は CLI 引数だけである。環境変数で立てられると「明示的」ではない。

        `allow_unverified` は**本 Spec の設定キーではない**（`--print-settings`
        にも現れない）。1回の実行に対して人が書く許可であり、環境に置いて
        おけるものにすると「明示的に与えた」という要件 2.2 の条件が崩れる。

        ⚠️ `require_verified_calibration`（ゲートの既定そのもの）は別の軸で
        あり、設定として与えられる。そちらを偽にする行為は「ゲートを外す」
        という設定変更であって、投擲1回ぶんの許可ではない。
        """
        monkeypatch.setenv("STB_M1_ALLOW_UNVERIFIED", "true")
        code, payload, err = _throw(
            env, capsys, monkeypatch, calibration_path=self._unverified_calibration(env)
        )
        assert code == 1
        assert "calibration_not_verified" in err
        assert payload == {}

    def test_permission_is_not_a_settings_key(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`--print-settings` に許可が現れない（設定へ写し取っていない）。"""
        assert (
            main(
                [
                    "run-throw",
                    "--layout-file",
                    str(write_layout(tmp_path)),
                    "--layout-id",
                    LAYOUT_ID,
                    "--allow-unverified",
                    "--print-settings",
                ]
            )
            == 0
        )
        text = capsys.readouterr().out
        assert "allow_unverified" not in text


# ---------------------------------------------------------------------------
# 評価側のサブコマンド
# ---------------------------------------------------------------------------


class TestEvaluationSubcommands:
    def test_ingest_truth_attaches_and_reports_unknown_ids(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """記録に存在しない識別子は警告として報告する（黙って捨てない。要件 4.7）。"""
        _write_truth(populated, ("throw-0001", "throw-0002"), extra_id="throw-0009")
        out_path = populated.tmp_path / "with-truth.ndjson"
        code, payload, err = _run(
            populated,
            [
                *_evaluation_argv(populated, "ingest-truth", log=False),
                "--out",
                str(out_path),
            ],
            capsys,
        )
        assert code == 0, err
        assert payload["unknown_record_ids"] == ["throw-0009"]
        assert payload["record_count"] == 2
        assert out_path.exists()

    def test_measure_reports_the_seven_items_and_stage_latency(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "measure"), capsys
        )
        assert code == 0, err
        groups = payload["groups"]
        assert len(groups) == 1
        assert groups[0]["calibration_id"] == "cal-test-0001"
        assert groups[0]["throw_count"] == 2
        assert set(groups[0]["items"]) >= {"total_flight_ms"}
        assert payload["latency"]["definition"]
        assert payload["latency"]["stages"]

    def test_missing_measurements_stay_missing_and_are_not_zero_filled(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**欠測は `None` である。0 で埋めない。**

        0 で埋めると「測れなかった項目」が「0 だった項目」として代表値へ
        算入され、時間予算の判断がそのぶん楽観側へ倒れる。
        """
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "measure"), capsys
        )
        assert code == 0, err
        items = payload["groups"][0]["items"]
        absent = [key for key, item in items.items() if item["count"] == 0]
        assert absent, "0件の項目が1つも無いと欠測側の分岐へ入力が届かない"
        for key in absent:
            assert items[key]["median"] is None, key
            assert items[key]["p95"] is None, key
            assert items[key]["iqr"] is None, key
            assert items[key]["missing"] == payload["groups"][0]["throw_count"], key
        present = [key for key, item in items.items() if item["count"] > 0]
        assert present, "値が入る項目も無いと、対の検査にならない"
        for key in present:
            assert items[key]["median"] is not None, key

    def test_failed_throws_are_excluded_but_counted(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**除外は「捨てる」ことではない**（要件 3.8）。

        入口で先に間引くと `failed_throw_count` が常に 0 になり、「何回
        失敗したのか」が誰にも読めなくなる。除くのは集計器の仕事である。
        """
        code, _, err = _throw(env, capsys, monkeypatch, record_id="throw-0001")
        assert code == 0, err
        monkeypatch.setattr(
            runner_module, "open_tracking", lambda *a, **k: FakePipeline([])
        )
        code, _, _ = _run(
            env,
            [
                "run-throw",
                *env.common(),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-fail",
                "--records",
                str(env.records_path),
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 1
        _write_truth(env, ("throw-0001",))

        code, payload, err = _run(env, _evaluation_argv(env, "measure"), capsys)
        assert code == 0, err
        group = payload["groups"][0]
        assert group["throw_count"] == 1
        assert group["failed_throw_count"] == 1
        # 段階別レイテンシ側は**呼び出し側が除く**契約である
        # （`aggregate_latency()` の docstring）。失敗投擲を混ぜると、
        # 初回予測が成立していない行が実測項目3 の分布へ入り込む。
        assert [
            item["record_id"]
            for item in payload["latency"]["detect_to_first_prediction"]
        ] == ["throw-0001"]

    def test_attribute_reports_components_without_a_single_total(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """帰属は成分ごとの内訳である（合計の単一値を持たない。要件 6.9）。"""
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "attribute"), capsys
        )
        assert code == 0, err
        attribution = payload["groups"][0]["attribution"]
        assert set(attribution) >= {"bias", "scatter", "range_bands", "judgement"}
        assert "total_error_mm" not in attribution

    def test_judge_oq27_reports_a_verdict_with_its_criterion(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "judge-oq27"), capsys
        )
        assert code == 0, err
        oq27 = payload["groups"][0]["oq27"]
        assert oq27["verdict"] == "deferred"
        assert oq27["judgement"]["criterion"]

    def test_material_oq05_stays_material_only(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """OQ-05 は決着させない（要件 10.4）。"""
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "material-oq05"), capsys
        )
        assert code == 0, err
        assert payload["groups"][0]["oq05"]["verdict"] == "material_only"

    def test_budget_does_not_update_before_the_items_are_complete(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """実測値が揃わない状態では時間予算表の更新値を出さない（要件 11.1）。"""
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "budget"), capsys
        )
        assert code == 0, err
        budget = payload["groups"][0]["budget"]
        assert budget["ready"] is False
        assert budget["missing_items"]

    def test_summary_marks_provisional_judgements_on_both_sides(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """要約の判定規則一覧が**暫定の有無を両方の値で**書き分ける。

        タスク 7.1 の申し送り (4): 「`暫定` を常に『なし』にする変異が
        要約側で生存する」。`Oq27Result` の保留も `Oq05Result` の暫定も
        実際に到達する状態なので、**両側を通す入力**で固定する。
        """
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "report"), capsys
        )
        assert code == 0, err
        summary = payload["groups"][0]["summary"]
        assert "- 判定値: deferred / 暫定: あり" in summary

        code, payload, err = _run(
            populated,
            [
                *_evaluation_argv(populated, "report"),
                "--min-valid-throws",
                "1",
                "--min-sessions",
                "1",
                "--no-require-live-source",
            ],
            capsys,
        )
        assert code == 0, err
        relaxed = payload["groups"][0]["summary"]
        # **同じ要約の中に両方の値が現れる。** 片方へ焼き付ける変異は
        # どちらの向きでもここで落ちる。
        assert "/ 暫定: なし" in relaxed
        assert "/ 暫定: あり" in relaxed
        # 設定が実際に効いている（試行数の下限を緩めると暫定が1つ以上減る）。
        assert relaxed.count("/ 暫定: なし") > summary.count("/ 暫定: なし")
        # ⚠️ OQ-27 の GATE 2（実機由来の投擲が1件も無い）は**無条件**であり、
        # `--no-require-live-source` では開かない。合成入力では常に保留である。
        assert "- 判定値: deferred / 暫定: あり" in relaxed

    def test_report_writes_one_file_per_calibration_group(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**1レポート = 1キャリブレーション群**（タスク 7.1 の申し送り）。"""
        second = env.tmp_path / "calibration-b.json"
        payload = json.loads(env.calibration_path.read_text(encoding="utf-8"))
        payload["calibration_id"] = "cal-test-0002"
        second.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        for record_id, calibration in (
            ("throw-0001", env.calibration_path),
            ("throw-0002", second),
        ):
            code, _, err = _throw(
                env,
                capsys,
                monkeypatch,
                record_id=record_id,
                calibration_path=calibration,
            )
            assert code == 0, err
        _write_truth(env, ("throw-0001", "throw-0002"))

        code, out, err = _run(env, _evaluation_argv(env, "report"), capsys)
        assert code == 0, err
        written = sorted(Path(path).name for path in out["written"])
        assert written == [
            f"report-{env.session_id}-cal-test-0001-verified.json",
            f"report-{env.session_id}-cal-test-0002-verified.json",
        ]
        assert len(out["groups"]) == 2
        assert {group["calibration_id"] for group in out["groups"]} == {
            "cal-test-0001",
            "cal-test-0002",
        }
        for path in out["written"]:
            assert Path(path).exists()


# ---------------------------------------------------------------------------
# bench-overhead（要件 7.5-7.8）
# ---------------------------------------------------------------------------


class TestBenchOverhead:
    def test_dropped_probe_is_supplied_so_the_verdict_is_not_always_undetermined(
        self,
        populated: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**取りこぼしの probe を必ず供給する**（タスク 6.4 の申し送り）。

        probe を渡さないと取りこぼしは常に欠測になり、判定の第2項が立たず
        **判定は常に判定不能へ落ちる**（安全側だが実機で使えない）。
        """
        monkeypatch.setattr(
            runner_module,
            "open_tracking",
            lambda *a, **k: FakePipeline(_camera_points()),
        )
        code, payload, err = _run(
            populated,
            [
                "bench-overhead",
                *populated.common(),
                "--calibration",
                str(populated.calibration_path),
                "--log",
                str(populated.log_path),
                "--overhead-cycles",
                "2",
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 0, err
        assert payload["frames_dropped"]["measurement_on"] is not None
        assert payload["frames_dropped"]["measurement_off"] is not None
        assert all(
            verdict["dropped_not_increased"] is not None
            for verdict in payload["verdicts"]
        )

    def test_report_is_written(
        self,
        populated: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            runner_module,
            "open_tracking",
            lambda *a, **k: FakePipeline(_camera_points()),
        )
        code, payload, err = _run(
            populated,
            [
                "bench-overhead",
                *populated.common(),
                "--calibration",
                str(populated.calibration_path),
                "--log",
                str(populated.log_path),
                "--overhead-cycles",
                "1",
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 0, err
        assert Path(payload["written"]).exists()
        assert payload["judgement"]["criterion"]


# ---------------------------------------------------------------------------
# plot（要件 8.x）
# ---------------------------------------------------------------------------


class RecordingBackend:
    """字形の欠落を申告する記録用バックエンド（`PlotBackend` の7操作）。"""

    def __init__(self, *, missing_glyphs: int) -> None:
        self._missing = missing_glyphs
        self.saved: list[Path] = []

    def open_figure(self, **kwargs: object) -> None:
        return None

    def points(self, **kwargs: object) -> None:
        return None

    def polyline(self, **kwargs: object) -> None:
        return None

    def reference_line(self, **kwargs: object) -> None:
        return None

    def circle(self, **kwargs: object) -> None:
        return None

    def arrow(self, **kwargs: object) -> None:
        return None

    def note(self, **kwargs: object) -> None:
        return None

    def save(self, path: Path) -> None:
        Path(path).write_bytes(b"png")
        self.saved.append(Path(path))

    def missing_glyph_count(self) -> int:
        return self._missing


MISSING_GLYPH_KINDS = 16


class TestPlot:
    def test_font_warning_is_surfaced_to_the_operator(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """字形の警告を握り潰さない（タスク 7.2 の申し送り。要件 8.3）。

        読めない字形は「図に明示した」ことにならない。
        """
        code, payload, err = _run(
            populated,
            _evaluation_argv(populated, "plot", log=False),
            capsys,
            plot_backend_factory=lambda: RecordingBackend(
                missing_glyphs=MISSING_GLYPH_KINDS
            ),
        )
        assert code == 0, err
        figures = payload["figures"]
        assert figures[0]["missing_glyph_count"] == MISSING_GLYPH_KINDS
        assert figures[0]["font_warning"]
        assert str(MISSING_GLYPH_KINDS) in err

    def test_no_font_warning_when_every_glyph_is_available(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """欠落が無ければ警告は立たない（真偽の**両方**を固定する）。"""
        code, payload, err = _run(
            populated,
            _evaluation_argv(populated, "plot", log=False),
            capsys,
            plot_backend_factory=lambda: RecordingBackend(missing_glyphs=0),
        )
        assert code == 0, err
        assert payload["figures"][0]["missing_glyph_count"] == 0
        assert payload["figures"][0]["font_warning"] is None
        assert "字形" not in err

    def test_unresolved_inputs_are_reported_as_missing_not_fabricated(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """調達できない2値は欠測のまま渡し、その旨を出力に残す（タスク 7.2）。"""
        code, payload, err = _run(
            populated,
            _evaluation_argv(populated, "plot", log=False),
            capsys,
            plot_backend_factory=lambda: RecordingBackend(missing_glyphs=0),
        )
        assert code == 0, err
        assert payload["trajectory_points_world_mm"] is None
        assert payload["camera_ray_horizontal"] is None
        assert any("推定軌道" in note for note in payload["notes"])

    def test_missing_dependency_only_disables_the_plots(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """描画依存が無い環境で `plot` のみが利用不可になる（要件 8.9）。"""

        def broken() -> object:
            raise ImportError("matplotlib is not installed")

        code, payload, err = _run(
            populated,
            _evaluation_argv(populated, "plot", log=False),
            capsys,
            plot_backend_factory=broken,
        )
        assert code == 1
        assert payload["available"] is False
        assert payload["reason"]

        code, _, err = _run(
            populated, _evaluation_argv(populated, "measure"), capsys
        )
        assert code == 0, err

    def test_real_backend_writes_image_files(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """既定（matplotlib）の経路でも図が出る。"""
        code, payload, err = _run(
            populated, _evaluation_argv(populated, "plot", log=False), capsys
        )
        assert code == 0, err
        paths = [Path(path) for figure in payload["figures"] for path in figure["paths"]]
        assert paths
        assert all(path.exists() for path in paths)


# ---------------------------------------------------------------------------
# 上流基盤の窓口の調達（**注入しない実経路**。要件 13.1, 13.5）
# ---------------------------------------------------------------------------


class TestGatewayIsOpenedByTheEntrypoint:
    """`gateway` を注入しないときの `_open_gateway()` の実経路。

    ⚠️ **他のテストが常に窓口を注入していると、この分岐は丸ごと死ぬ。**
    実際に一度そうなっており、非注入分岐の先頭へ `raise` を置いても全件が
    通っていた。評価側のサブコマンドは実機を一切要さず実物の窓口で通せるので、
    ここで必ず1本通す。
    """

    def test_evaluation_runs_with_the_gateway_the_entrypoint_opened(
        self, populated: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """**窓口の注入なしで `measure` が通る。**

        記録の読み出しもログ集計も実物の `UpstreamGateway` が担う。あわせて
        `--runtime-set` で与えたログ出力先に**実際にログが書かれる**ことまで
        見る——`overrides` を空に埋める実装では既定の出力先へ逃げるので、
        この主張は成立しない。
        """
        session_id = "opened-by-the-entrypoint"
        code = main(
            [
                *_evaluation_argv(populated, "measure"),
                *_runtime_set_argv(tmp_path, suffix="opened"),
                "--session-id",
                session_id,
            ]
        )
        captured = capsys.readouterr()
        assert code == 0, captured.err
        payload = json.loads(captured.out)
        assert payload["session_id"] == session_id
        assert payload["groups"][0]["throw_count"] == 2
        assert (tmp_path / "logs-opened" / f"{session_id}.ndjson").exists()

    def test_the_gateway_the_entrypoint_opened_is_closed_again(
        self, populated: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """**自分で開いた窓口は自分で閉じる。**

        上流のログ器は投入キューを持ち、閉じて初めて排出と `session_end` の
        書き込みが起きる。閉じ忘れると**書き終えていないログ**が残り、
        次の集計がその上で行われる。
        """
        session_id = "closed-by-the-entrypoint"
        code = main(
            [
                *_evaluation_argv(populated, "measure"),
                *_runtime_set_argv(tmp_path, suffix="closed"),
                "--session-id",
                session_id,
            ]
        )
        assert code == 0, capsys.readouterr().err
        written = (tmp_path / "logs-closed" / f"{session_id}.ndjson").read_text(
            encoding="utf-8"
        )
        assert '"session_end"' in written

    def test_the_default_session_id_differs_between_runs(
        self, populated: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`--session-id` 未指定の既定は**実行ごとに異なる**。

        定数に潰すと、`_group_stem()` が2回目の実行で同じ幹を返し、
        **design.md「1レポート = 1キャリブレーション群」のために作った幹が
        セッション側の衝突で上書きされる**。
        """
        seen: list[str] = []
        for suffix in ("first", "second"):
            code = main(
                [
                    *_evaluation_argv(
                        populated, "measure", with_session_id=False
                    ),
                    *_runtime_set_argv(tmp_path, suffix=suffix),
                ]
            )
            captured = capsys.readouterr()
            assert code == 0, captured.err
            seen.append(json.loads(captured.out)["session_id"])
        assert seen[0] != seen[1]
        assert all(value.startswith("m1-") for value in seen)

    @pytest.mark.parametrize("flag", ["--runtime-set", "--tracking-set"])
    def test_malformed_key_value_pairs_are_rejected(
        self, flag: str, env: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """`KEY=VALUE` の書式違反を**入口が自分で拒否する**（要件 13.6）。

        入口が持つ数少ない自前の検証である。素通ししてしまうと、`=` を書き
        忘れた指定が上流の型変換まで流れてから落ちる——**どの引数が悪いのか
        が読めない失敗**になる。
        """
        code = main(
            [
                "run-throw",
                *env.common(),
                *_runtime_set_argv(tmp_path, suffix="malformed"),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-malformed",
                "--records",
                str(env.records_path),
                flag,
                "tracker_max_points",
            ],
            gateway=env.gateway,
            supplier=_depth_supplier(),
        )
        captured = capsys.readouterr()
        assert code == 2
        assert f"{flag} は KEY=VALUE の形で指定する" in captured.err
        assert "tracker_max_points" in captured.err
        # **書式で落ちた時点で投擲は始まっていない。**
        assert not env.records_path.exists()

    @pytest.mark.parametrize("command", ["run-throw", "bench-overhead"])
    def test_upstream_settings_are_resolved_before_the_source_is_opened(
        self,
        command: str,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """上流の設定解決を**入力元を開くより先に**置く（要件 13.6）。

        値の誤り（ここでは整数でない `tracker_max_points`）は設定の誤りで
        あって、**実機を掴んだあとで気付くのでは「実行開始前に拒否する」
        ことにならない**。書式検査は入口の先頭で済むが、**値の妥当性は
        上流の解決器しか知らない**ので、その呼び出しの位置が効く。
        文面ではなく「入力元を開いていない」ことで固定する。
        """
        monkeypatch.setattr(
            runner_module,
            "open_tracking",
            lambda *a, **k: FakePipeline(_camera_points()),
        )
        code, _, err = _run(
            env,
            [
                *_throwing_argv(env, command, record_id="throw-bad-tracking"),
                "--tracking-set",
                "tracker_max_points=not-a-number",
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 2
        assert "上流の追跡設定" in err
        # **入力元を1度も開いていない。**
        assert env.gateway.stream_profile_calls == 0

    @pytest.mark.parametrize("command", ["run-throw", "bench-overhead"])
    def test_the_source_is_opened_once_the_upstream_settings_resolve(
        self,
        command: str,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**対の検査**: 設定が通れば入力元は実際に開かれる。"""
        monkeypatch.setattr(
            runner_module,
            "open_tracking",
            lambda *a, **k: FakePipeline(_camera_points()),
        )
        code, _, err = _run(
            env,
            [
                *_throwing_argv(env, command, record_id="throw-good-tracking"),
                "--tracking-set",
                "tracker_max_points=41",
            ],
            capsys,
            supplier=_depth_supplier(),
        )
        assert code == 0, err
        assert env.gateway.stream_profile_calls == 1

    @pytest.mark.parametrize("flag", ["--runtime-set", "--tracking-set"])
    def test_an_empty_key_is_rejected(
        self, flag: str, env: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """**対の検査**: `=` はあるがキーが空の指定も拒否する。"""
        code = main(
            [
                "run-throw",
                *env.common(),
                *_runtime_set_argv(tmp_path, suffix="emptykey"),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-empty-key",
                "--records",
                str(env.records_path),
                flag,
                "=41",
            ],
            gateway=env.gateway,
            supplier=_depth_supplier(),
        )
        captured = capsys.readouterr()
        assert code == 2
        assert f"{flag} は KEY=VALUE の形で指定する" in captured.err

    def test_runtime_environment_and_overrides_are_not_filled_in_blank(
        self,
        populated: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """取得側も `file` / `env` / `overrides` を**3つとも供給する**。

        追跡側（`test_tracking_environment_and_overrides_are_not_filled_in_blank`）
        と**対称**の検査である。片方だけ置くと、design.md「CLI」が名指しで
        警告している「空に埋めると上流の環境変数・上書きが黙って捨てられる」
        当の失敗が、取得側だけ野放しになる。
        """
        runtime_config = tmp_path / "sf.json"
        runtime_config.write_text("{}", encoding="utf-8")
        seen: list[dict[str, object]] = []
        real = cli_module.upstream.resolve_runtime_settings

        def spy(**kwargs: object) -> object:
            seen.append(dict(kwargs))
            return real(**kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(cli_module.upstream, "resolve_runtime_settings", spy)
        monkeypatch.setenv("STB_SF_QUEUE_CAPACITY", "2048")

        code = main(
            [
                *_evaluation_argv(populated, "measure"),
                *_runtime_set_argv(tmp_path, suffix="spied"),
                "--runtime-config",
                str(runtime_config),
                "--session-id",
                "spied-runtime",
            ]
        )
        captured = capsys.readouterr()
        assert code == 0, captured.err
        assert len(seen) == 1
        assert set(seen[0]) == {"file", "env", "overrides"}
        assert seen[0]["file"] == runtime_config
        assert seen[0]["env"]["STB_SF_QUEUE_CAPACITY"] == "2048"  # type: ignore[index]
        assert seen[0]["overrides"] == {
            "source": "simulated",
            "width_px": str(WIDTH_PX),
            "height_px": str(HEIGHT_PX),
            "fps": str(FPS),
            "logging_path": str(tmp_path / "logs-spied"),
            "recording_root": str(tmp_path / "sessions-spied"),
        }

    def test_synthetic_input_cannot_supply_camera_intrinsics(
        self, env: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """**注入点が要る理由そのもの**を、実行される形で残す。

        上流の合成入力は `StreamProfile.intrinsics` を常に `None` で返す
        （合成入力にカメラ内部パラメータという概念が無い）。較正側の安全弁は
        それを既定値で埋めずに弾くので、**窓口を注入しない `run-throw` は
        整合性検査そのものへ到達できない**。この事実が変われば注入点は不要になる。
        """
        code = main(
            [
                "run-throw",
                *env.common(),
                *_runtime_set_argv(tmp_path, suffix="nointr"),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-real-gateway",
                "--records",
                str(env.records_path),
                "--session-id",
                "no-intrinsics",
            ],
            # 窓口は**注入しない**。供給関数だけを渡して、実物の窓口が
            # 合成入力を実際に開けるところまで通す。
            supplier=_depth_supplier(),
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "設定エラー" in captured.err
        assert "カメラ内部パラメータ" in captured.err
        # 記録は1件も作られない（整合性検査へ到達していない）。
        assert not env.records_path.exists()

    def test_a_synthetic_source_cannot_be_opened_without_a_supplier(
        self, env: Env, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        """供給関数が無ければ実物の窓口は合成入力を開けない。

        **本 Spec は `numpy` を import しない**ので、CLI が合成フレームの
        生成器を自前で持つことはできない（要件 13.2 / 13.3）。だから
        `supplier` は `argv` では表現できない注入点である。
        """
        code = main(
            [
                "run-throw",
                *env.common(),
                *_runtime_set_argv(tmp_path, suffix="nosupplier"),
                "--calibration",
                str(env.calibration_path),
                "--record-id",
                "throw-no-supplier",
                "--records",
                str(env.records_path),
                "--session-id",
                "no-supplier",
            ]
        )
        captured = capsys.readouterr()
        assert code == 1
        assert "上流エラー" in captured.err
        assert "supplier" in captured.err


class TestSupplierIsPassedThrough:
    """合成入力の供給関数を**そのまま**接点へ渡す（要件 12.1）。"""

    def test_the_same_supplier_object_reaches_the_stream_profile_entrance(
        self,
        env: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CLI が渡した供給関数と**同一オブジェクト**が届く。

        落とすと、実物の `stream_profile()` は `open_source()` の中で
        `SourceKind.SIMULATED` かつ `supplier is None` を拒否する。
        """
        supplier = _depth_supplier()
        code, _, err = _throw(env, capsys, monkeypatch, supplier=supplier)
        assert code == 0, err
        assert env.gateway.stream_profile_suppliers == [supplier]
        assert env.gateway.stream_profile_suppliers[0] is supplier

    def test_the_real_entrance_refuses_a_synthetic_source_without_a_supplier(
        self, env: Env
    ) -> None:
        """**対の検査**: 実物は供給関数が無いと合成入力を開けない。

        これが成り立つからこそ、上の素通しは load-bearing である。
        """
        with pytest.raises(SensingConfigError):
            env.gateway._inner.stream_profile()

    def test_the_real_entrance_succeeds_once_a_supplier_is_given(
        self, env: Env
    ) -> None:
        """供給関数を与えれば実物でも開ける（真偽の両側を通す）。"""
        profile = env.gateway._inner.stream_profile(supplier=_depth_supplier())
        assert profile.width_px == WIDTH_PX  # type: ignore[union-attr]
        # ⚠️ 合成入力は内部パラメータを持たない。これが注入点の存在理由である。
        assert profile.intrinsics is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# サブコマンド側のヘルプ（利用者が設定フラグを実際に読む面。要件 13.5, 13.7）
# ---------------------------------------------------------------------------

#: 解決順序の注記の**全文**（テスト局所のリテラル。実装の定数を import しない）。
RESOLUTION_ORDER_SENTENCE = (
    "設定の解決順序（優先順位の高い順）: "
    "実行時指定 > 環境変数 > 設定ファイル > 既定値。"
    "--print-settings で解決結果を表示できる（要件 13.5）。"
)

HARDWARE_LEGEND_SENTENCE = (
    "各サブコマンドの見出しにある「実機:」は、その作業に実機"
    "（Raspberry Pi 4 / RealSense D435）が要るかどうかである（要件 12.6）。"
)


class TestSubcommandHelpCarriesTheNotices:
    """**最上位ヘルプだけでは足りない。**

    設定フラグ（`--min-valid-throws` など）はサブコマンドの parser に居る。
    要件 13.7 が求めるのは「既定値が暫定である旨を**設定の説明に明示する**」
    ことであり、利用者がその説明を読む面はサブコマンドの `--help` である。
    """

    @pytest.mark.parametrize("command", ["run-throw", "measure"])
    def test_provisional_notice_is_shown(
        self, command: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = _help_of([command, "--help"], capsys)
        assert PROVISIONAL_NOTICE in text
        assert "--min-valid-throws" in text

    @pytest.mark.parametrize("command", ["run-throw", "measure"])
    def test_resolution_order_sentence_is_shown_in_full(
        self, command: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**一文まるごと**で照合する。

        単語を個別に見ると、順序を入れ替えても一文を削っても素通りする。
        """
        text = _help_of([command, "--help"], capsys)
        assert RESOLUTION_ORDER_SENTENCE in text

    @pytest.mark.parametrize("command", ["run-throw", "measure"])
    def test_segment3_holdover_is_shown(
        self, command: str, capsys: pytest.CaptureFixture[str]
    ) -> None:
        text = _help_of([command, "--help"], capsys)
        assert "segment3_assumed_ms" in text
        assert "据え置き" in text
        assert "実測値ではない" in text

    def test_the_hardware_legend_is_shown(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """「実機:」という見出しが何を意味するかの説明が残っている。"""
        assert HARDWARE_LEGEND_SENTENCE in _help_of(["run-throw", "--help"], capsys)

    def test_the_unwrapped_hardware_table_is_in_the_top_level_epilogue(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """折り返さない形の一覧が最上位ヘルプに残っている。

        argparse 自身のサブコマンド一覧欄は端末幅で折り返されるため、実機の
        要否がそこで途切れうる。**要件 12.6 の情報は折り返しで壊れてよい
        ものではない。**
        """
        text = _help_of(["--help"], capsys)
        assert "実機の要否一覧（要件 12.6。折り返さない形で再掲する）:" in text


# ---------------------------------------------------------------------------
# `--log` の線引き（段階別レイテンシを要する側と要さない側）
# ---------------------------------------------------------------------------

#: `--log` を必須とするサブコマンド（テスト局所のリテラル）。
#: 判断側は `LatencyResult` を非 optional で要求し、`measure` は段階別
#: レイテンシそのものが出力である。
LOG_REQUIRING_COMMANDS = (
    "measure",
    "attribute",
    "judge-oq27",
    "material-oq05",
    "budget",
    "report",
)


class TestLogRequirement:
    @pytest.mark.parametrize("command", LOG_REQUIRING_COMMANDS)
    def test_missing_log_is_rejected_before_running(
        self,
        command: str,
        populated: Env,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """**要する側**: `--log` が無ければ**実行開始前に**拒否する（要件 13.6）。

        文面は `_require()` が出す一文まるごとで照合する。**どこで拒否したか**
        は `test_nothing_runs_before_the_log_argument_is_checked` が別に
        固定する——文面だけに頼ると、判断側の保険（`LatencyResult` を非
        optional で要求する）に助けられて「全部計算し終えてから落ちる」実装と
        区別が付かなくなる。
        """
        code, _, err = _run(
            populated, _evaluation_argv(populated, command, log=False), capsys
        )
        assert code == 2
        assert (
            "--log が指定されていない: 段階別レイテンシと実測項目3 の出所である"
        ) in err
        # **記録の読み出しすら走っていない。**
        assert populated.gateway.load_records_calls == []

    @pytest.mark.parametrize("command", LOG_REQUIRING_COMMANDS)
    def test_nothing_runs_before_the_log_argument_is_checked(
        self,
        command: str,
        populated: Env,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """**拒否が実作業より前に来る**ことを、文面ではなく行動で固定する。

        ⚠️ これが無いと、`_LOG_REQUIRED` の線引きは**偶然の文面差**でしか
        守られない——判断側の保険の文面を `_require()` と揃えたうえで線引きを
        空集合にする複合変異が、文面照合だけのテストを素通りする。
        番兵が呼ばれた時点で「実行開始前に拒否する」（要件 13.6）は破れている。
        """

        def sentinel(*args: object, **kwargs: object) -> object:
            raise AssertionError("ingest_truth が --log の検査より先に走った")

        monkeypatch.setattr(cli_module, "ingest_truth", sentinel)
        code, _, err = _run(
            populated, _evaluation_argv(populated, command, log=False), capsys
        )
        assert code == 2, err
        assert "ingest_truth が --log の検査より先に走った" not in err
        assert populated.gateway.load_records_calls == []

    def test_a_broken_truth_file_is_rejected_before_records_are_read(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """真値ファイルの検査も**記録を1行も読む前に**済ませる（要件 13.6）。

        `load_truth_file()` は「全件を取り込みの時点で検査する」と定めている
        ——その検査が記録の読み出しのあとに来ると、**30件目の綴り間違いに
        気付くのが全記録を読んだあと**になる。
        """
        populated.truth_path.write_text(
            json.dumps(
                {
                    "truth_format_version": "1.0",
                    "layout_id": LAYOUT_ID,
                    "entries": {
                        "throw-0001": {
                            "impact_point_world_mm": [0.0, 0.0, 0.0],
                            "impact_point_source": "メジャー実測",
                            "impact_point_uncertainty_mm": 15.0,
                            "impact_point_uncertainy_mm": 15.0,
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        code, _, err = _run(
            populated, _evaluation_argv(populated, "measure"), capsys
        )
        assert code == 2
        assert "impact_point_uncertainy_mm" in err
        assert populated.gateway.load_records_calls == []

    def test_the_evaluation_actually_runs_once_the_log_is_given(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**対の検査**: `--log` を与えれば実作業は走る（番兵が空振りでない）。"""
        code, _, err = _run(
            populated, _evaluation_argv(populated, "measure"), capsys
        )
        assert code == 0, err
        assert populated.gateway.load_records_calls == [populated.records_path]

    def test_plot_does_not_need_a_log(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**要さない側**: 可視化は段階別レイテンシを使わない。"""
        code, _, err = _run(
            populated,
            _evaluation_argv(populated, "plot", log=False),
            capsys,
            plot_backend_factory=lambda: RecordingBackend(missing_glyphs=0),
        )
        assert code == 0, err

    def test_ingest_truth_does_not_need_a_log(
        self, populated: Env, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """**要さない側**: 真値の取り込みも段階別レイテンシを使わない。"""
        code, _, err = _run(
            populated,
            [
                *_evaluation_argv(populated, "ingest-truth", log=False),
                "--out",
                str(populated.tmp_path / "with-truth.ndjson"),
            ],
            capsys,
        )
        assert code == 0, err


# ---------------------------------------------------------------------------
# ストリーム識別の調達経路（本タスクが接点・継ぎ目へ足した2つの入口）
# ---------------------------------------------------------------------------


class SpySource:
    """入力元のダブル。**取得開始の前後で別のプロファイルを返す。**

    実機アダプタは `start()` で暫定プロファイル（内部パラメータ `None`）を
    実測値へ差し替える。開かずに属性だけ覗くと、**内部パラメータを持たない
    値を掴む**——この差を観測できる形にしてある。
    """

    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self._profile: object = FakeStreamProfile(intrinsics=None)

    @property
    def profile(self) -> object:
        return self._profile

    def __enter__(self) -> Self:
        self.entered += 1
        self._profile = FakeStreamProfile()
        return self

    def __exit__(self, *exc: object) -> None:
        self.exited += 1


class TestStreamProfileProcurement:
    """`UpstreamGateway.stream_profile()`（接点に足した唯一の入口）。"""

    def test_the_source_is_opened_before_the_profile_is_read(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """**取得を開始してから読む。** 開かずに覗くと内部パラメータが欠測になる。"""
        source = SpySource()
        monkeypatch.setattr(
            upstream_module, "open_source", lambda *a, **k: source
        )
        profile = env.gateway._inner.stream_profile()
        assert source.entered == 1
        assert profile.intrinsics is not None  # type: ignore[union-attr]

    def test_the_source_is_closed_again(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """開いた入力元は必ず閉じる（実機を掴んだままにしない）。"""
        source = SpySource()
        monkeypatch.setattr(
            upstream_module, "open_source", lambda *a, **k: source
        )
        env.gateway._inner.stream_profile()
        assert source.exited == 1

    def test_supplier_and_speed_reach_the_upstream_generator(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """供給関数と再生速度を**そのまま**上流の生成口へ渡す。

        `open_frames()` と**同じ2引数**を受けるのは意図である——同じ入力元を
        同じ条件で開かないと、整合性検査に使ったストリーム識別が実際に投擲へ
        使うそれと食い違いうる。⚠️ **`speed` は記録再生でしか効かない**ので、
        合成入力のフィクスチャからは行動として観測できない。ここで引数の
        素通しとして固定しておく。
        """
        seen: list[dict[str, object]] = []
        source = SpySource()

        def spy(*args: object, **kwargs: object) -> object:
            seen.append(dict(kwargs))
            return source

        monkeypatch.setattr(upstream_module, "open_source", spy)
        supplier = _depth_supplier()
        env.gateway._inner.stream_profile(supplier=supplier, speed="realtime")
        assert seen[0]["supplier"] is supplier
        assert seen[0]["speed"] == "realtime"


class TestStreamIdentity:
    """`seam.stream_identity()`（継ぎ目に足した唯一の入口）。"""

    def test_the_upstream_mapping_is_passed_through_not_rewritten(self) -> None:
        """**上流の写像をそのまま呼ぶ**（自前で写し直さない）。"""
        profile = FakeStreamProfile()
        signature, intrinsics = seam_module.stream_identity(profile)
        assert signature == to_signature(profile)  # type: ignore[arg-type]
        assert intrinsics == to_intrinsics(profile)  # type: ignore[arg-type]

    def test_the_upstream_safety_valve_is_preserved(self) -> None:
        """**安全弁は較正側が所有したままである**（上流の要件 8.4）。

        入力元が内部パラメータを提供できないとき、上流の `to_intrinsics()` は
        独自の固定値で埋めずに弾く。下流が写像を書き直すとこの弁が落ち、
        **既定値で埋めた変換が静かに成立してしまう**——座標系の数 cm のずれは
        「予測が悪い」という症状としてしか現れない。
        """
        with pytest.raises(M1ConfigError) as excinfo:
            seam_module.stream_identity(FakeStreamProfile(intrinsics=None))
        assert excinfo.value.context["reason"] == "intrinsics_unavailable"

    def test_the_upstream_exception_type_does_not_leak(self) -> None:
        """上流の例外型をそのまま外へ出さない（接点を例外の経路からも崩さない）。"""
        with pytest.raises(M1ConfigError):
            seam_module.stream_identity(FakeStreamProfile(intrinsics=None))
        assert not isinstance(
            M1ConfigError("x"), CalibrationConfigError
        )  # 語彙が別であることを明示する


# ---------------------------------------------------------------------------
# 境界（要件 13.1）
# ---------------------------------------------------------------------------

UPSTREAM_PACKAGES = frozenset(
    {"sensing_foundation", "flying_object_tracking", "world_frame_calibration"}
)


class TestBoundaries:
    def test_cli_does_not_import_the_upstream_packages(self) -> None:
        """**入口層も上流パッケージを直接 import しない。**

        調達は接点（`upstream.py`）と継ぎ目（`seam.py`）を経由する。
        """
        tree = ast.parse((SRC_DIR / "cli.py").read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & UPSTREAM_PACKAGES)
        assert "matplotlib" not in imported
        assert "numpy" not in imported

    def test_cli_delegates_and_does_not_reimplement_the_resolution_order(self) -> None:
        """解決順序を CLI で書き直さない（`M1Settings.resolve()` へ委譲する）。"""
        source = (SRC_DIR / "cli.py").read_text(encoding="utf-8")
        assert "M1Settings.resolve" in source
