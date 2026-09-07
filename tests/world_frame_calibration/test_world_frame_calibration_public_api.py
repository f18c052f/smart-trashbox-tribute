"""公開 API の確定を検証する（design.md「PublicApi」/ tasks.md タスク 6.3 /
要件 3.6, 8.6）。

`world_frame_calibration/__init__.py` は再エクスポートのみを行い、ロジックを
一切持たない契約である（design.md PublicApi「Validation」: 「`__init__` は
ロジックを持たず再エクスポートのみ。`prediction_core` を import しない」）。
本テストは以下を固定する。

- `world_frame_calibration.__all__` が本タスクで確定した公開シンボル一覧と
  **完全一致**すること（多すぎても少なすぎても失敗）。design.md
  PublicApi「Integration」が名指しする最小集合
  （`load_calibration` / `CalibrationResult` / `WorldTransform` /
  `check_compatibility` / `CalibrationFailure` / `FailureReason`）に加え、
  同じ「結果の読み込み・結果型・変換型・整合性検査・**失敗例外**・失敗理由」
  という tasks.md タスク 6.3 のカテゴリ列挙を満たすため、例外階層の残り2つ
  （基底例外 `WorldFrameCalibrationError` と設定誤り例外
  `CalibrationConfigError`）も含める。呼び出し側が `except
  WorldFrameCalibrationError` で本パッケージ由来の失敗を一括捕捉できず、
  `load_calibration` が送出しうる `CalibrationConfigError`
  （不正な JSON・欠損キーなど、`result.py` の docstring 参照）を型として
  捕捉できないのでは、「失敗例外」というカテゴリの契約を満たさない
- `__all__` に列挙された各名前が実際に `world_frame_calibration` から
  import/`getattr` できること
- `dir(world_frame_calibration)` から dunder と内部サブモジュール名を除いた
  ものが `__all__` の集合と一致すること（意図しない漏れ export が無いこと）
- 公開シンボルが元の定義モジュールと**同一オブジェクト**であること
  （再エクスポートであり、コピーや再定義ではないことの確認）
- `__init__.py` 自身のソースが import 文・`__all__` 代入・docstring のみで
  構成され、関数/クラス定義や制御構文を一切持たないこと（ゼロロジック）
- `__init__.py` 自身のソースが `prediction_core` を一切参照しないこと、
  および実際に `import world_frame_calibration` した後の
  `sys.modules` に `prediction_core` が現れないこと（要件 8.6）。後者は
  同一プロセス内で他のテストファイルが `prediction_core` を import 済みの
  可能性があり `sys.modules` の状態がテスト実行順序に依存してしまうため、
  独立したサブプロセスで検証する（`test_sensing_public_api.py` が
  `dir()` 汚染について指摘するのと同種の問題を避ける）
- **実測**: `sensing_foundation` が一切 import できない環境（`sys.meta_path`
  で `sensing_foundation` を遮断したサブプロセス）でも
  `import world_frame_calibration` および `__all__` の全シンボルへの
  アクセスが成功すること（観測可能な完了状態そのもの。要件 8.6）。これは
  `deproject.py` / `upstream.py` がモジュールレベルで `sensing_foundation`
  を import する一方、公開契約の12シンボルは `errors.py` / `result.py` /
  `transform.py` / `types.py` / `profile.py` からのみ再エクスポートされ、
  その依存連鎖（`result` → `frame` → `plan` / `transform` / `types` /
  `linalg` / `errors`、および `profile` → `types` / `errors`）のどこにも
  `sensing_foundation` の module-level import が無いことを裏付ける
- 本パッケージが Throw Record スキーマ相当のものを一切定義していないこと
  （`__init__.py` にクラス定義が無いこと自体が `test_init_module_source_
  contains_no_logic` で固定されるため、ここでは `ThrowRecord` という名前が
  公開面に一切現れないことを追加で固定する）

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_public_api.py` は使わずプレフィックス付きの
`test_world_frame_calibration_public_api.py` とする（既存タスクの命名規約。
`tests/world_frame_calibration/test_world_frame_calibration_synthetic.py`
等が踏襲する tasks.md タスク1.6 実装ノートの方針）。
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import world_frame_calibration

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_PATH = REPO_ROOT / "src" / "world_frame_calibration" / "__init__.py"

# design.md PublicApi「Integration」が名指しする6シンボルに、tasks.md タスク
# 6.3 の「失敗例外」カテゴリを満たすために必要な例外階層の残り2つ
# （`WorldFrameCalibrationError` / `CalibrationConfigError`）を加えた8個。
# さらに tasks.md タスク 9.1 が、`check_compatibility` の**引数を作る手段**
# として型2つ（`StreamSignature` / `Intrinsics`）と写像2つ
# （`to_signature` / `to_intrinsics`）を加え、合計 12 個とする。
EXPECTED_PUBLIC_SYMBOLS: frozenset[str] = frozenset(
    {
        # errors.py（失敗例外・失敗理由）
        "WorldFrameCalibrationError",
        "CalibrationConfigError",
        "CalibrationFailure",
        "FailureReason",
        # result.py（結果の読み込み・結果型・整合性検査）
        "CalibrationResult",
        "load_calibration",
        "check_compatibility",
        # transform.py（変換型）
        "WorldTransform",
        # types.py（整合性検査の引数の型。タスク 9.1）
        "StreamSignature",
        "Intrinsics",
        # profile.py（上流 StreamProfile からの写像。タスク 9.1）
        "to_signature",
        "to_intrinsics",
    }
)

# `world_frame_calibration` パッケージ配下に実在する、あらゆるトップレベル
# サブモジュール名。`__init__.py` が直接 import するのは `errors` /
# `result` / `transform` の3つだが、`result` の import 連鎖で `frame` /
# `plan` / `types` / `linalg` も推移的に読み込まれる。加えて、Python の
# import 機構は `import world_frame_calibration.deproject`
# （`test_world_frame_calibration_deproject.py` など、本 Spec の**他の**
# テストファイルが行う）を1度でも実行すると、`world_frame_calibration`
# モジュールオブジェクトへその名前を属性として束縛する——これは
# `__init__.py` が何を import するかとは**無関係**に発生する CPython の
# 仕様であり、同一プロセス内で他のテストファイルが先に実行されたかどうかに
# テストの成否が依存しないよう、`dir()` ベースの「漏れ export」検査では
# サブモジュール名を**すべて**除外する（`test_sensing_public_api.py` の
# `INTERNAL_SUBMODULE_NAMES` と同じ方針）。`__init__.py` 自身がこれらを
# re-export しないことは `test_init_module_source_contains_no_logic` と
# `test_all_matches_expected_public_symbols_exactly` が別途固定する。
INTERNAL_SUBMODULE_NAMES: frozenset[str] = frozenset(
    {
        "errors",
        "result",
        "transform",
        "frame",
        "plan",
        "types",
        "linalg",
        "deproject",
        "plane",
        "anchors",
        "verify",
        "report",
        "upstream",
        # profile.py（タスク 9.1）。上流 `StreamProfile` からの写像を持つが、
        # `sensing_foundation` への**実行時**依存は持たない。
        "profile",
        # cli.py（タスク 6.4）。CLI の入口 `main` はパッケージの公開 API
        # （`__init__.py` の12シンボル）には含まれない（design.md PublicApi
        # は下流ライブラリ利用者向けの契約であり、CLI はエンドユーザー向けの
        # 別の入口であるため）。したがってサブモジュール名としてのみここへ
        # 列挙し、`EXPECTED_PUBLIC_SYMBOLS` には追加しない。
        "cli",
        # `from __future__ import annotations` により `dir()` に現れる
        # 言語機能オブジェクト。公開シンボルではない。
        "annotations",
    }
)


def test_all_matches_expected_public_symbols_exactly() -> None:
    """`__all__` が確定した12シンボルと完全一致する（多くも少なくもない）。"""
    assert set(world_frame_calibration.__all__) == EXPECTED_PUBLIC_SYMBOLS


def test_all_has_no_duplicate_entries() -> None:
    """`__all__` に重複が無い（完全一致だけでは重複+別の欠落の相殺を見逃すため）。"""
    assert len(world_frame_calibration.__all__) == len(set(world_frame_calibration.__all__))


def test_every_symbol_in_all_is_accessible_via_getattr() -> None:
    """`__all__` に列挙された各名前が `world_frame_calibration` から実際に取得できる。"""
    for name in world_frame_calibration.__all__:
        assert hasattr(world_frame_calibration, name), (
            f"{name} が world_frame_calibration に存在しない"
        )


def test_every_symbol_in_all_is_importable_via_from_import() -> None:
    """`from world_frame_calibration import <name>` の形で全シンボルが import できる。"""
    import importlib

    module = importlib.import_module("world_frame_calibration")
    for name in world_frame_calibration.__all__:
        # `getattr` が例外を送出しないこと自体が「import できる」ことの確認である。
        getattr(module, name)


def test_namespace_has_no_unintended_public_leaks() -> None:
    """`dir(world_frame_calibration)` のうち非 dunder・非内部モジュール名が `__all__` と一致する。

    ここに `__all__` に無い名前が余分に現れる場合、意図せず内部実装を
    re-export してしまっている（漏れ export）ことを意味する。
    """
    visible_names = {
        name
        for name in dir(world_frame_calibration)
        if not name.startswith("_") and name not in INTERNAL_SUBMODULE_NAMES
    }
    assert visible_names == EXPECTED_PUBLIC_SYMBOLS


def test_reexported_symbols_are_identical_objects_not_copies() -> None:
    """公開シンボルが元の定義モジュールと同一オブジェクトである（再エクスポート）。"""
    from world_frame_calibration import errors as errors_module
    from world_frame_calibration import result as result_module
    from world_frame_calibration import transform as transform_module

    assert world_frame_calibration.WorldFrameCalibrationError is (
        errors_module.WorldFrameCalibrationError
    )
    assert world_frame_calibration.CalibrationConfigError is errors_module.CalibrationConfigError
    assert world_frame_calibration.CalibrationFailure is errors_module.CalibrationFailure
    assert world_frame_calibration.FailureReason is errors_module.FailureReason

    assert world_frame_calibration.CalibrationResult is result_module.CalibrationResult
    assert world_frame_calibration.load_calibration is result_module.load_calibration
    assert world_frame_calibration.check_compatibility is result_module.check_compatibility

    assert world_frame_calibration.WorldTransform is transform_module.WorldTransform

    from world_frame_calibration import profile as profile_module
    from world_frame_calibration import types as types_module

    assert world_frame_calibration.StreamSignature is types_module.StreamSignature
    assert world_frame_calibration.Intrinsics is types_module.Intrinsics
    assert world_frame_calibration.to_signature is profile_module.to_signature
    assert world_frame_calibration.to_intrinsics is profile_module.to_intrinsics


def test_failure_exception_hierarchy_is_publicly_catchable() -> None:
    """`CalibrationConfigError` / `CalibrationFailure` の双方が公開された
    `WorldFrameCalibrationError` の派生であり、下流が `except
    world_frame_calibration.WorldFrameCalibrationError` の一箇所で本パッケージ
    由来のあらゆる失敗を捕捉できる（tasks.md タスク 6.3「失敗例外」カテゴリ）。
    """
    assert issubclass(
        world_frame_calibration.CalibrationConfigError,
        world_frame_calibration.WorldFrameCalibrationError,
    )
    assert issubclass(
        world_frame_calibration.CalibrationFailure,
        world_frame_calibration.WorldFrameCalibrationError,
    )


def test_init_module_source_contains_no_logic() -> None:
    """`__init__.py` が import 文と `__all__` 代入とモジュール docstring のみで構成される。

    再エクスポート専用という設計契約（design.md PublicApi「Validation」:
    「`__init__` はロジックを持たず再エクスポートのみ」）を、ソース構造
    そのものから機械的に確認する（`tests/prediction_core/test_public_api.py`
    ・`tests/trajectory_sim/test_trajectory_sim_public_api.py` と同じ技法）。
    関数定義・クラス定義・if/for 等の制御構文が無いことを AST で検査する。
    """
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))

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
            assert isinstance(node.value, ast.Constant), (
                "__init__.py の式文は docstring のみ許可される"
            )


def test_init_module_source_does_not_reference_prediction_core() -> None:
    """`__init__.py` のソースが `prediction_core` を一切 import しない
    （要件 8.6 / tasks.md タスク 6.3「`prediction_core` を import しない」）。

    `sys.modules` によるランタイム検査は他のテストファイルが
    `prediction_core` を先に import 済みだと汚染されるため、`__init__.py`
    自身のソースコードを AST で静的解析する（動的な確認は
    `test_importing_package_does_not_pull_in_prediction_core_in_subprocess`
    がサブプロセス分離で別途行う）。
    """
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            assert not node.module.startswith("prediction_core"), (
                f"__init__.py が prediction_core を import している（行 {node.lineno}）"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("prediction_core"), (
                    f"__init__.py が prediction_core を import している（行 {node.lineno}）"
                )


def test_no_throw_record_schema_symbol_is_publicly_reachable() -> None:
    """Throw Record スキーマ相当のシンボルが公開面に一切現れない
    （tasks.md タスク 6.3「Throw Record スキーマを再定義しない」。本 Spec は
    そもそもそのようなクラスを定義していないため、公開面からも到達できない
    ことを回帰として固定する）。
    """
    assert "ThrowRecord" not in world_frame_calibration.__all__
    assert not hasattr(world_frame_calibration, "ThrowRecord")


def test_importing_package_does_not_pull_in_prediction_core_in_subprocess() -> None:
    """`import world_frame_calibration` の実行後、`sys.modules` に
    `prediction_core` が一切現れない（要件 8.6）ことを、他のテストの import
    履歴に汚染されない独立したサブプロセスで確認する。
    """
    script = (
        "import sys\n"
        "import world_frame_calibration\n"
        "leaked = sorted(m for m in sys.modules if m == 'prediction_core' "
        "or m.startswith('prediction_core.'))\n"
        "assert not leaked, f'prediction_core が import されている: {leaked!r}'\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_import_succeeds_without_sensing_foundation_installed() -> None:
    """`sensing_foundation` が一切 import できない環境でも
    `import world_frame_calibration` と `__all__` 全シンボルへのアクセスが
    成功する（design.md PublicApi の観測可能な完了状態そのもの / 要件 8.6）。

    `sys.meta_path` の先頭へ `sensing_foundation`（およびそのサブモジュール）
    の import を必ず失敗させる `MetaPathFinder` を挿入したサブプロセスで
    検証する。これにより、`deproject.py` / `upstream.py` がモジュールレベルで
    `sensing_foundation` を import していても、公開契約の12シンボルの依存連鎖
    （`errors` / `result` → `frame` → `plan` / `transform` / `types` /
    `linalg`、および `profile` → `types` / `errors`）がそれらを経由していない限り `import world_frame_calibration`
    は成功する、という設計上の主張を実測で裏付ける。
    """
    script = (
        "import sys\n"
        "\n"
        "class _BlockSensingFoundation:\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'sensing_foundation' or name.startswith('sensing_foundation.'):\n"
        "            raise ModuleNotFoundError(\n"
        "                f'simulated missing dependency: {name}'\n"
        "            )\n"
        "        return None\n"
        "\n"
        "sys.meta_path.insert(0, _BlockSensingFoundation())\n"
        "\n"
        "import world_frame_calibration\n"
        "\n"
        "for _name in world_frame_calibration.__all__:\n"
        "    getattr(world_frame_calibration, _name)\n"
        "\n"
        "assert 'sensing_foundation' not in sys.modules\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_blocking_finder_actually_blocks_sensing_foundation_as_a_sanity_check() -> None:
    """上記サブプロセステストの前提そのものが機能していることを確認する。

    `_BlockSensingFoundation` を挿入した状態で `import sensing_foundation`
    自体を試みると `ModuleNotFoundError` になることを確認し、
    `test_import_succeeds_without_sensing_foundation_installed` が
    「実際には遮断できていないのに偶然成功していた」誤検出でないことを保証する。
    """
    script = (
        "import sys\n"
        "\n"
        "class _BlockSensingFoundation:\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'sensing_foundation' or name.startswith('sensing_foundation.'):\n"
        "            raise ModuleNotFoundError(\n"
        "                f'simulated missing dependency: {name}'\n"
        "            )\n"
        "        return None\n"
        "\n"
        "sys.meta_path.insert(0, _BlockSensingFoundation())\n"
        "\n"
        "try:\n"
        "    import sensing_foundation\n"
        "except ModuleNotFoundError:\n"
        "    print('BLOCKED_AS_EXPECTED')\n"
        "else:\n"
        "    print('NOT_BLOCKED')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "BLOCKED_AS_EXPECTED" in result.stdout


# ---------------------------------------------------------------------------
# タスク 9.1: 下流が**公開入口だけ**で整合性検査を呼べること
# ---------------------------------------------------------------------------


def _saved_calibration_path(tmp_path: Path, upstream_profile: object) -> Path:
    """検査対象となるキャリブレーション結果を1件保存し、そのパスを返す。

    ここは**下流の契約の外側**（較正を実施する側）であるため、内部モジュール
    を使って組み立ててよい。本テストが固定したいのは、この結果を読み込んだ
    **下流**が公開入口だけで `check_compatibility` を呼べるかどうかである。
    """
    from world_frame_calibration.frame import build_world_frame
    from world_frame_calibration.plan import PlanLimits
    from world_frame_calibration.profile import to_intrinsics, to_signature
    from world_frame_calibration.result import (
        assemble_calibration_result,
        save_calibration,
    )
    from world_frame_calibration.types import (
        AnchorObservation,
        AnchorRole,
        PixelRegion,
        Plane,
        PlaneQuality,
    )

    region = PixelRegion(x0_px=0, y0_px=0, x1_px=10, y1_px=10)
    quality = PlaneQuality(
        points_considered=5000,
        inlier_count=4500,
        inlier_ratio=0.9,
        residual_abs_p50_mm=1.2,
        residual_abs_p95_mm=3.4,
        residual_rms_mm=1.8,
        frames_used=5,
        incidence_angle_deg=42.0,
        rng_seed=7,
    )
    plane = Plane(normal=(0.0, 0.0, 1.0), distance_mm=1000.0, quality=quality)

    def _anchor(label: str, role: AnchorRole, point: tuple[float, float, float]):
        return AnchorObservation(
            label=label,
            role=role,
            point_camera_mm=point,
            point_on_plane_mm=point,
            height_above_plane_mm=0.0,
            range_from_camera_mm=1000.0,
            sample_count=120,
            spread_mm=2.5,
            region=region,
            frames_used=5,
        )

    establishment = build_world_frame(
        plane,
        _anchor("origin", AnchorRole.ORIGIN, (0.0, 0.0, 1000.0)),
        _anchor("x_axis", AnchorRole.X_AXIS, (1000.0, 0.0, 1000.0)),
        PlanLimits(min_baseline_mm=800.0),
    )
    result = assemble_calibration_result(
        establishment,
        source_kind="simulated",
        session_path=None,
        signature=to_signature(upstream_profile),  # type: ignore[arg-type]
        intrinsics=to_intrinsics(upstream_profile),  # type: ignore[arg-type]
        plan_digest={"notes": "task 9.1"},
        notes="",
    )
    path = tmp_path / "calibration.json"
    save_calibration(result, path)
    return path


class _FakeIntrinsics:
    """上流 `CameraIntrinsics` を構造的に模したダブル。

    `to_intrinsics` は属性アクセスしか行わないため、上流型そのものを
    要求しない（`sensing_foundation` 未導入の環境でも写像が動くことの
    裏返し。`test_world_frame_calibration_profile.py` が実測で固定する）。
    """

    width_px = 643
    height_px = 484
    fx_px = 615.5
    fy_px = 616.25
    ppx_px = 321.125
    ppy_px = 239.875
    model = "brown_conrady"
    coeffs = (0.0, 0.0, 0.0, 0.0, 0.0)


class _FakeStreamProfile:
    """上流 `StreamProfile` を構造的に模したダブル。"""

    def __init__(self, **overrides: object) -> None:
        self.width_px = 641
        self.height_px = 482
        self.fps = 31
        self.depth_scale_mm = 0.123
        self.color_enabled = True
        self.intrinsics: object = _FakeIntrinsics()
        for key, value in overrides.items():
            setattr(self, key, value)


def test_downstream_can_call_check_compatibility_using_only_public_symbols(
    tmp_path: Path,
) -> None:
    """下流が `world_frame_calibration` の公開入口だけを使って
    `check_compatibility` を呼べる（tasks.md タスク 9.1 / 要件 6.4）。

    要件 6.4 は「読み込んだ結果の……が**現在の入力元のそれ**と一致しない
    場合」と定めている。その「現在の入力元のそれ」は
    `to_signature` / `to_intrinsics` でしか作れない——検査は
    `result.signature != signature` という**オブジェクト同士の比較**であり、
    構造が同じだけの別クラスを下流が自前で定義しても常に不一致になるため
    である。本テストは、内部モジュールへ一切触れずに検査を通せることを
    公開シンボルだけで実演する。
    """
    upstream_profile = _FakeStreamProfile()
    path = _saved_calibration_path(tmp_path, upstream_profile)

    # ここから下は公開入口のシンボルだけを使う（下流の視点）。
    result = world_frame_calibration.load_calibration(path)
    signature = world_frame_calibration.to_signature(upstream_profile)
    intrinsics = world_frame_calibration.to_intrinsics(upstream_profile)

    assert isinstance(signature, world_frame_calibration.StreamSignature)
    assert isinstance(intrinsics, world_frame_calibration.Intrinsics)

    # 一致していれば何も起きない（例外を送出しないことが「有効」の意味）。
    assert world_frame_calibration.check_compatibility(result, signature, intrinsics) is None


def test_downstream_public_path_detects_a_changed_stream_configuration(
    tmp_path: Path,
) -> None:
    """設定が変わっていれば、公開入口だけの経路でも
    `PROFILE_MISMATCH` として検出される（要件 6.4 / A-9）。

    一致側しか通さないテストは「常に成功する検査」を見逃す。不一致側を
    必ず通し、失敗理由が分岐可能な値として届くことまで固定する。
    """
    saved_profile = _FakeStreamProfile()
    path = _saved_calibration_path(tmp_path, saved_profile)

    # 解像度だけを変えた「現在の入力元」。
    current_profile = _FakeStreamProfile(width_px=1281)

    result = world_frame_calibration.load_calibration(path)
    signature = world_frame_calibration.to_signature(current_profile)
    intrinsics = world_frame_calibration.to_intrinsics(current_profile)

    with pytest.raises(world_frame_calibration.CalibrationFailure) as excinfo:
        world_frame_calibration.check_compatibility(result, signature, intrinsics)

    assert excinfo.value.reason is world_frame_calibration.FailureReason.PROFILE_MISMATCH
