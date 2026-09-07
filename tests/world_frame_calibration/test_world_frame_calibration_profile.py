"""上流 `StreamProfile` から自 Spec の値オブジェクトへの写像を検証する
(tasks.md タスク 9.1 / design.md「PublicApi」/ 要件 6.4, 3.6, 8.2, 8.4, 8.6)。

`profile.py` は `upstream.py` から**写像だけを切り出した**モジュールである。
切り出しの理由は1つだけ: 下流（`m1-prediction-validation`）が公開入口から
`check_compatibility(result, signature, intrinsics)` を呼ぶには、その引数
（`StreamSignature` / `Intrinsics`）を**現在の入力元から作る手段**が要る
のに、`upstream.py` は `sensing_foundation` をモジュールレベルで import
するため、公開入口へ出すと「`sensing_foundation` 未導入の環境でも
`import world_frame_calibration` が成功する」という既存契約が壊れるためで
ある（`test_world_frame_calibration_public_api.py` が実測で固定している）。

本テストが固定すること:

- **写像の値そのもの**: `to_signature` / `to_intrinsics` の全フィールドが、
  上流 `StreamProfile` / `CameraIntrinsics` の対応フィールドから
  取り違えなく写されること。フィクスチャは**意味の違う数をすべて別の値**に
  して（幅 641 / 高さ 482 / fps 31 / fx 615.5 / fy 616.25 / ppx 321.125 /
  ppy 239.875 ...）、フィールドの取り違えが必ず露見するようにする。
  さらに `StreamProfile` 側の解像度（641x482）と `CameraIntrinsics` 側の
  解像度（643x484）を**わざと食い違わせ**、`to_signature` が内部パラメータ側
  の解像度を読んでいない（およびその逆でない）ことも同時に固定する
- **`None` ガード**（要件 8.4 の安全弁）: `profile.intrinsics is None` が
  `CalibrationConfigError` として弾かれること。**0件側・欠測側の分岐へ
  実際に入力を届ける**
- **安全弁の所有者**: その安全弁を「較正側が所有したまま」であること、
  および「下流に写像を書かせるとこの弁が落ちる」という理由が
  `to_intrinsics` の docstring に**文面として**書かれていること
  （謳っただけでは固定されないため、文面自体をテストで固定する）
- **移設であって二重定義ではないこと**: `upstream.to_intrinsics` /
  `upstream.to_signature` と公開入口の同名シンボルが、`profile.py` の
  関数と**同一オブジェクト**であること（`upstream.py` が自前で写像を
  書き直していれば別オブジェクトになって落ちる）
- **`sensing_foundation` への実行時依存を持たないこと**: 上流を
  `sys.meta_path` で遮断したサブプロセスで `profile.py` を import し、
  ダック型の入力に対して**写像を実際に実行**できること（import が通るだけ
  でなく、関数本体が上流の型を要求しないことまで確かめる）

ファイル名について: `tests/**` に `__init__.py` が無いため、裸の
`test_profile.py` は使わずプレフィックス付きの
`test_world_frame_calibration_profile.py` とする（既存タスクの命名規約。
tasks.md 実装ノート・タスク1.6 参照）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import world_frame_calibration
from sensing_foundation import CameraIntrinsics, StreamProfile
from world_frame_calibration import profile as profile_module
from world_frame_calibration import upstream as upstream_module
from world_frame_calibration.errors import CalibrationConfigError
from world_frame_calibration.types import Intrinsics, StreamSignature

REPO_ROOT = Path(__file__).resolve().parents[2]

# 意味の違う数はフィクスチャ上でも別の値にする（写しの取り違えを露見させる）。
# `StreamProfile` の解像度と `CameraIntrinsics` の解像度も**わざと**違える。
_PROFILE_WIDTH_PX = 641
_PROFILE_HEIGHT_PX = 482
_INTRINSICS_WIDTH_PX = 643
_INTRINSICS_HEIGHT_PX = 484


def _camera_intrinsics(**overrides: object) -> CameraIntrinsics:
    kwargs: dict[str, object] = {
        "width_px": _INTRINSICS_WIDTH_PX,
        "height_px": _INTRINSICS_HEIGHT_PX,
        "fx_px": 615.5,
        "fy_px": 616.25,
        "ppx_px": 321.125,
        "ppy_px": 239.875,
        "model": "brown_conrady",
        "coeffs": (0.11, 0.22, 0.33, 0.44, 0.55),
    }
    kwargs.update(overrides)
    return CameraIntrinsics(**kwargs)  # type: ignore[arg-type]


_UNSET = object()
"""`intrinsics` 引数の「未指定」を表す番人。

`intrinsics=None` は「入力元が内部パラメータを提供できない」という
テスト対象の正当な状態を明示するために使うため、既定値を `None` に
すると「未指定」と「明示的な None」を区別できなくなる。
"""


def _stream_profile(
    intrinsics: CameraIntrinsics | None = _UNSET,  # type: ignore[assignment]
    **overrides: object,
) -> StreamProfile:
    intr = _camera_intrinsics() if intrinsics is _UNSET else intrinsics
    kwargs: dict[str, object] = {
        "width_px": _PROFILE_WIDTH_PX,
        "height_px": _PROFILE_HEIGHT_PX,
        "fps": 31,
        "depth_scale_mm": 0.123,
        "color_enabled": True,
        "intrinsics": intr,
    }
    kwargs.update(overrides)
    return StreamProfile(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# to_signature: 値そのものの固定
# ---------------------------------------------------------------------------


def test_to_signature_produces_exactly_the_expected_value_object() -> None:
    """`to_signature` の戻り値が、期待するリテラル値の `StreamSignature` と等しい。

    フィールドごとの比較ではなく**値オブジェクト全体の等価性**で固定する
    （`StreamSignature` は `frozen=True` の値等価なデータクラスである）。
    期待値は実装の定数から組まず、テスト局所のリテラルで書く。
    """
    result = profile_module.to_signature(_stream_profile())

    assert result == StreamSignature(
        width_px=641,
        height_px=482,
        fps=31,
        depth_scale_mm=0.123,
        color_enabled=True,
    )


def test_to_signature_reads_stream_resolution_not_intrinsics_resolution() -> None:
    """`to_signature` が解像度を `StreamProfile` 側から読む（内部パラメータ側
    の解像度と食い違っていても、ストリーム設定の値がそのまま写る）。
    """
    result = profile_module.to_signature(_stream_profile())

    assert (result.width_px, result.height_px) == (641, 482)
    assert (result.width_px, result.height_px) != (
        _INTRINSICS_WIDTH_PX,
        _INTRINSICS_HEIGHT_PX,
    )


@pytest.mark.parametrize("color_enabled", [True, False])
def test_to_signature_carries_both_color_enabled_values(color_enabled: bool) -> None:
    """真偽値は両方の値で固定する（既定値を返すだけの実装を通さない）。"""
    result = profile_module.to_signature(_stream_profile(color_enabled=color_enabled))

    assert result.color_enabled is color_enabled


# ---------------------------------------------------------------------------
# to_intrinsics: 値そのものの固定
# ---------------------------------------------------------------------------


def test_to_intrinsics_produces_exactly_the_expected_value_object() -> None:
    """`to_intrinsics` の戻り値が、期待するリテラル値の `Intrinsics` と等しい。

    `fx_px` / `fy_px` / `ppx_px` / `ppy_px` はすべて別の値であり、
    どの2つを取り違えても本アサーションが落ちる。
    """
    result = profile_module.to_intrinsics(_stream_profile())

    assert result == Intrinsics(
        width_px=643,
        height_px=484,
        fx_px=615.5,
        fy_px=616.25,
        ppx_px=321.125,
        ppy_px=239.875,
        model="brown_conrady",
        coeffs=(0.11, 0.22, 0.33, 0.44, 0.55),
    )


def test_to_intrinsics_reads_intrinsics_resolution_not_stream_resolution() -> None:
    """`to_intrinsics` が解像度を `CameraIntrinsics` 側から読む
    （`StreamProfile` 側の解像度と食い違っていても内部パラメータの値が写る）。
    """
    result = profile_module.to_intrinsics(_stream_profile())

    assert (result.width_px, result.height_px) == (643, 484)
    assert (result.width_px, result.height_px) != (_PROFILE_WIDTH_PX, _PROFILE_HEIGHT_PX)


# ---------------------------------------------------------------------------
# to_intrinsics: 欠測側の分岐（要件 8.4 の安全弁）
# ---------------------------------------------------------------------------


def test_to_intrinsics_rejects_missing_intrinsics_as_config_error() -> None:
    """入力元が内部パラメータを提供できない場合、独自に保持した固定値で
    埋めずに `CalibrationConfigError` で失敗する（要件 8.4）。

    **欠測側の分岐へ実際に入力を届ける。** ここを通らない実装（ガードを
    落とした実装）は、`profile.intrinsics` が `None` のまま属性アクセスへ
    進んで `AttributeError` になるか、既定値で静かに埋めることになる。
    """
    bad_profile = _stream_profile(intrinsics=None)

    with pytest.raises(CalibrationConfigError) as excinfo:
        profile_module.to_intrinsics(bad_profile)

    message = str(excinfo.value)
    assert "intrinsics" in message
    assert "8.4" in message


def test_to_intrinsics_missing_intrinsics_is_not_an_attribute_error() -> None:
    """ガードを外した実装との差を明示的に固定する。

    ガードが無ければ `None.width_px` で `AttributeError` になる。
    `CalibrationConfigError` は `AttributeError` の派生ではないため、
    「例外が飛べば何でもよい」テストになっていない。
    """
    bad_profile = _stream_profile(intrinsics=None)

    with pytest.raises(CalibrationConfigError):
        profile_module.to_intrinsics(bad_profile)

    assert not issubclass(CalibrationConfigError, AttributeError)


def test_to_signature_does_not_require_intrinsics_at_all() -> None:
    """`to_signature` は内部パラメータを見ないため、`intrinsics=None` でも成功する
    （安全弁は `to_intrinsics` 側だけが持つ、という責務の切れ目の固定）。
    """
    result = profile_module.to_signature(_stream_profile(intrinsics=None))

    assert result == StreamSignature(
        width_px=641,
        height_px=482,
        fps=31,
        depth_scale_mm=0.123,
        color_enabled=True,
    )


# ---------------------------------------------------------------------------
# 安全弁の所有者を docstring の文面として固定する（要件 8.4 / design.md
# PublicApi「Risks」）
# ---------------------------------------------------------------------------


def test_to_intrinsics_docstring_states_that_calibration_side_owns_the_guard() -> None:
    """`to_intrinsics` の docstring が、安全弁を**較正側が所有したまま**である
    ことと、**下流に写像を書かせるとこの弁が落ちる**という理由を述べている
    （tasks.md タスク 9.1 / design.md PublicApi「Risks」）。

    説明文が謳う性質は、謳っただけでは固定されない。公開面を広げる本タスク
    では、この一文が消えたら誰も気付かないまま「下流が自分で写像を書けば
    よい」という運用へ滑る。だから文面そのものを回帰として固定する。
    """
    doc = profile_module.to_intrinsics.__doc__
    assert doc is not None
    assert "較正側が所有" in doc
    assert "下流" in doc
    assert "8.4" in doc


# ---------------------------------------------------------------------------
# 移設であって二重定義ではないこと
# ---------------------------------------------------------------------------


def test_upstream_reexports_the_same_mapping_functions_not_copies() -> None:
    """`upstream.py` は写像を自前で書き直さず、`profile.py` の関数を
    そのまま再 import して使う（二重定義の禁止）。

    `upstream.py` に写像がもう一組あると、片方だけを直したときに
    「保存された結果」と「現在の入力元」の突き合わせが静かに壊れる。
    """
    assert upstream_module.to_intrinsics is profile_module.to_intrinsics
    assert upstream_module.to_signature is profile_module.to_signature


def test_public_entry_exposes_the_same_mapping_functions_not_copies() -> None:
    """公開入口の `to_signature` / `to_intrinsics` が `profile.py` の関数と
    同一オブジェクトである（再エクスポートであり、コピーや再定義ではない）。
    """
    assert world_frame_calibration.to_intrinsics is profile_module.to_intrinsics
    assert world_frame_calibration.to_signature is profile_module.to_signature


def test_profile_module_defines_both_mappings_itself() -> None:
    """写像の**定義**が `profile.py` にある（`upstream.py` からの再 import を
    `profile.py` 側が受け取っているのではない、という向きの固定）。
    """
    assert profile_module.to_intrinsics.__module__ == "world_frame_calibration.profile"
    assert profile_module.to_signature.__module__ == "world_frame_calibration.profile"


# ---------------------------------------------------------------------------
# sensing_foundation への実行時依存を持たないこと（実測）
# ---------------------------------------------------------------------------


_BLOCKER_PREAMBLE = (
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
)


def _run_blocked_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCKER_PREAMBLE + script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,  # 終了コードはテスト側で明示的に検査する
    )


def test_profile_mappings_run_without_sensing_foundation_installed() -> None:
    """`sensing_foundation` を遮断したサブプロセスで `profile.py` を import し、
    ダック型の入力に対して**写像を実際に実行**できる（要件 8.2, 8.6）。

    import が通ることだけでは足りない。`StreamProfile` を型注釈にしか
    使っていない（`from __future__ import annotations` により実行時には
    評価されない）という設計上の主張は、**関数本体を実際に走らせて**初めて
    裏付けられる。
    """
    script = (
        "from world_frame_calibration.profile import to_intrinsics, to_signature\n"
        "\n"
        "class _Intr:\n"
        "    width_px = 643\n"
        "    height_px = 484\n"
        "    fx_px = 615.5\n"
        "    fy_px = 616.25\n"
        "    ppx_px = 321.125\n"
        "    ppy_px = 239.875\n"
        "    model = 'brown_conrady'\n"
        "    coeffs = (0.11, 0.22, 0.33, 0.44, 0.55)\n"
        "\n"
        "class _Profile:\n"
        "    width_px = 641\n"
        "    height_px = 482\n"
        "    fps = 31\n"
        "    depth_scale_mm = 0.123\n"
        "    color_enabled = True\n"
        "    intrinsics = _Intr()\n"
        "\n"
        "sig = to_signature(_Profile())\n"
        "intr = to_intrinsics(_Profile())\n"
        "assert (sig.width_px, sig.height_px, sig.fps) == (641, 482, 31)\n"
        "assert sig.depth_scale_mm == 0.123 and sig.color_enabled is True\n"
        "assert (intr.width_px, intr.height_px) == (643, 484)\n"
        "assert (intr.fx_px, intr.fy_px) == (615.5, 616.25)\n"
        "assert (intr.ppx_px, intr.ppy_px) == (321.125, 239.875)\n"
        "assert intr.model == 'brown_conrady'\n"
        "assert intr.coeffs == (0.11, 0.22, 0.33, 0.44, 0.55)\n"
        "assert 'sensing_foundation' not in sys.modules\n"
        "print('OK')\n"
    )
    result = _run_blocked_subprocess(script)

    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "OK" in result.stdout


def test_missing_intrinsics_guard_still_fires_without_sensing_foundation() -> None:
    """要件 8.4 の安全弁は、上流が入っていない環境でも同じように働く。

    `sensing_foundation` を遮断したサブプロセスで `intrinsics=None` の
    ダック型入力を渡し、`CalibrationConfigError` が送出されることを確かめる
    （欠測側の分岐が、公開面を広げた後も較正側に残っていることの実測）。
    """
    script = (
        "from world_frame_calibration.errors import CalibrationConfigError\n"
        "from world_frame_calibration.profile import to_intrinsics\n"
        "\n"
        "class _Profile:\n"
        "    width_px = 641\n"
        "    height_px = 482\n"
        "    fps = 31\n"
        "    depth_scale_mm = 0.123\n"
        "    color_enabled = True\n"
        "    intrinsics = None\n"
        "\n"
        "try:\n"
        "    to_intrinsics(_Profile())\n"
        "except CalibrationConfigError:\n"
        "    print('GUARD_FIRED')\n"
        "else:\n"
        "    print('GUARD_MISSING')\n"
    )
    result = _run_blocked_subprocess(script)

    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "GUARD_FIRED" in result.stdout


def test_blocking_finder_actually_blocks_sensing_foundation_as_a_sanity_check() -> None:
    """上記2つのサブプロセステストの前提が機能していることを確認する
    （遮断できていないのに偶然成功していた、という誤検出を防ぐ）。
    """
    result = _run_blocked_subprocess(
        "try:\n"
        "    import sensing_foundation\n"
        "except ModuleNotFoundError:\n"
        "    print('BLOCKED_AS_EXPECTED')\n"
        "else:\n"
        "    print('NOT_BLOCKED')\n"
    )

    assert result.returncode == 0, (
        f"サブプロセスが失敗した:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "BLOCKED_AS_EXPECTED" in result.stdout
