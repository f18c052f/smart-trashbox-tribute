"""コマンド入口の検証（design.md「Cli」/ 要件 3.2, 4.3, 5.3, 6.2, 6.7, 7.5）。

tasks.md タスク 4.1 の観測可能な完了状態——「形状ライブラリ非導入の環境で、
選定・許容誤差導出・識別子のみの照合が正常終了し、生成の要求が専用の終了コードで
失敗すること」——を、次の3系統で固定する。

1. **終了コードの写像**（`exit_code_for`）: `errors.py` の5系統が互いに素であること、
   および `0` 正常 / `1` 検査の不一致 / `2` 使い方の誤り・入力不正 /
   `3` 形状生成の環境が無い、の対応が動かないこと
2. **サブコマンドの振る舞い**: `main()` を in-process で呼び、形状を要さない経路
   （`select` / `tolerance` / `check --digest-only`）が形状層に触れずに完了し、
   形状を要する経路が `ImportError` を終了コード 3 へ写すこと
3. **実プロセスでの通し**: 形状ライブラリを遮断した `PYTHONPATH` の下で
   `python -m catch_mechanism` を起動し、上記が**本物の非導入環境**で成り立つこと

⚠️ **本ファイルは `configs/catch_mechanism/geometry-baseline.json` の有無にも
内容にも依存しない。** 既定の記録を作るのはタスク 4.2 であり、ここで出荷ファイルへ
ピン留めすると 4.2 が自力で緑に戻せなくなる（tasks.md「Implementation Notes」
タスク 2.3(b) / 2.4(a) の再発防止）。記録が要る検査は `--baseline` で `tmp_path`
の記録を指す。

⚠️ サブプロセスの復号は `encoding="utf-8"` を明示する。`text=True` はロケール
依存であり、lib3mf がプロセスのロケールを `C` へ落とす既知の副作用
（tasks.md「Implementation Notes」タスク 3.4(a)）に巻き込まれうる。
"""

from __future__ import annotations

import json
import locale
import os
import subprocess
import sys
from pathlib import Path

import pytest

from catch_mechanism import cli
from catch_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    load_params,
    parameters_digest,
)
from catch_mechanism.errors import (
    CadUnavailableError,
    CatchMechanismError,
    ConsistencyError,
    GeometryError,
    ParameterError,
    SelectionError,
)
from catch_mechanism.metrics import (
    DEFAULT_BASELINE_PATH,
    GeometryBaseline,
    PartMetrics,
    load_baseline,
    write_baseline,
)
from catch_mechanism.params import MechanismParams
from catch_mechanism.selection import DEFAULT_CANDIDATES_PATH
from catch_mechanism.shapes import BuiltPart, segment_part_names
from catch_mechanism.tolerance import (
    DEFAULT_DERIVATION_PATH,
    derive_position_tolerance,
    load_derivation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# 形状ライブラリの有無（要件 5.7）。
# ---------------------------------------------------------------------------

try:  # pragma: no cover - 環境によって分岐する
    import build123d as _build123d
except ImportError:  # pragma: no cover - `cad` extra 非導入の環境
    _build123d = None

requires_cad = pytest.mark.skipif(
    _build123d is None,
    reason="形状ライブラリ（build123d / `cad` extra）が未導入である。"
    "要件 5.7 により、形状生成を除く検査はこの環境でも完了する。",
)


# ---------------------------------------------------------------------------
# 補助
# ---------------------------------------------------------------------------


def _params() -> MechanismParams:
    """出荷の寸法設定ファイルを読む（`cli` が既定で読むものと同一）。"""
    return load_params()


def _fake_metrics(name: str, volume_mm3: float = 1000.0) -> PartMetrics:
    """形状ライブラリを要さない指標（素の数値だけを持つ）。"""
    return PartMetrics(
        part_name=name,
        volume_mm3=volume_mm3,
        bbox_mm=(10.0, 20.0, 30.0),
        solid_count=1,
    )


def _fake_parts(names: tuple[str, ...], volume_mm3: float = 1000.0) -> tuple[BuiltPart, ...]:
    """`shapes.build_parts` の差し替え用。`solid` は使われないため素のオブジェクト。"""
    return tuple(
        BuiltPart(name=name, solid=object(), metrics=_fake_metrics(name, volume_mm3))
        for name in names
    )


def _baseline_of(
    parts: tuple[BuiltPart, ...],
    *,
    digest: str | None = None,
) -> GeometryBaseline:
    return GeometryBaseline(
        schema_version=SCHEMA_VERSION,
        parameters_digest=digest if digest is not None else parameters_digest(_params()),
        volume_rel_tolerance=1e-6,
        bbox_abs_tolerance_mm=1e-3,
        generator_version="test-fixture",
        parts={part.name: part.metrics for part in parts},
    )


def _write_baseline_file(
    path: Path,
    parts: tuple[BuiltPart, ...],
    *,
    digest: str | None = None,
) -> Path:
    write_baseline(_baseline_of(parts, digest=digest), path)
    return path


def _nocad_stub(tmp_path: Path) -> Path:
    """`import build123d` が `ImportError` になるスタブ置き場を作って返す。"""
    stub_dir = tmp_path / "nocad"
    stub_dir.mkdir()
    (stub_dir / "build123d.py").write_text(
        'raise ImportError("No module named \'build123d\' (test stub)")\n',
        encoding="utf-8",
    )
    return stub_dir


def _run_module(
    args: list[str],
    *,
    stub_dir: Path | None = None,
    timeout: float = 180.0,
) -> subprocess.CompletedProcess[str]:
    """`python -m catch_mechanism <args>` を実プロセスとして起動する。

    `stub_dir` を与えると形状ライブラリを遮断した環境で起動する。
    ⚠️ 復号は `encoding="utf-8"` を明示する（本モジュール docstring）。
    """
    env = os.environ.copy()
    if stub_dir is not None:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(stub_dir) + (os.pathsep + existing if existing else "")
    return subprocess.run(
        [sys.executable, "-m", "catch_mechanism", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. 終了コードの写像（design.md「Cli」/「Error Categories and Responses」）
# ---------------------------------------------------------------------------


def test_exit_codes_have_the_documented_values() -> None:
    """design.md「Cli」の「0 正常 / 1 検査の不一致 / 2 入力の誤り / 3 CAD 不在」。"""
    assert cli.EXIT_OK == 0
    assert cli.EXIT_MISMATCH == 1
    assert cli.EXIT_USAGE == 2
    assert cli.EXIT_CAD_UNAVAILABLE == 3


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConsistencyError("記録と現在が食い違う"), 1),
        (ParameterError("項目名が不正"), 2),
        (SelectionError("選定の入力が不正"), 2),
        (GeometryError("造形制約に違反"), 2),
        (CadUnavailableError("形状ライブラリが無い"), 3),
    ],
)
def test_each_error_family_maps_to_its_own_exit_code(
    error: CatchMechanismError, expected: int
) -> None:
    """5系統それぞれが design.md の終了コードへ写る（`errors.py` の docstring と一致）。

    ⚠️ `SelectionError` の割り当ては tasks.md「Implementation Notes」タスク 1.2(a)
    が**本タスク（4.1）の所有**と定めたものである。選定の**入力**の誤りであり
    「使い方の誤り・入力不正」（終了コード 2）に属する——候補の不適合は例外では
    なく `CandidateVerdict.accepted = False` という値であって、この経路を通らない。
    """
    assert cli.exit_code_for(error) == expected


def test_error_families_are_pairwise_disjoint() -> None:
    """5系統に継承関係が無い（`except` の順序で終了コードが変わらない）。

    tasks.md「Implementation Notes」タスク 1.2:「系統の間に継承関係を作ると
    `except` の順序で終了コードが変わってしまう」。
    """
    families = (
        ParameterError,
        SelectionError,
        GeometryError,
        ConsistencyError,
        CadUnavailableError,
    )
    for left in families:
        for right in families:
            if left is not right:
                assert not issubclass(left, right), f"{left.__name__} が {right.__name__} の派生"


def test_exit_code_table_covers_every_error_family() -> None:
    """終了コード表が `errors.__all__` の具象5系統をちょうど覆う。

    ⚠️ 系統が増えたときに黙って既定値へ落ちることを防ぐ番人である。
    """
    from catch_mechanism import errors as errors_module

    concrete = {
        getattr(errors_module, name)
        for name in errors_module.__all__
        if getattr(errors_module, name) is not CatchMechanismError
    }
    assert set(cli.EXIT_CODE_BY_ERROR) == concrete


def test_bare_import_error_maps_to_the_cad_exit_code() -> None:
    """裸の `ImportError` も終了コード 3 である。

    `export.py` は形状ライブラリ非導入を `ImportError` のまま通す（design.md の
    Traceability が要件 5.1〜5.3 の実現を `Cli` に置くため）。⚠️ これを 2 や 1 へ
    落とすと「入力が悪い」と読めてしまい、導入すれば直ることが伝わらない。
    """
    assert cli.exit_code_for(ImportError("No module named 'build123d'")) == 3
    assert cli.exit_code_for(ModuleNotFoundError("No module named 'build123d'")) == 3


# ---------------------------------------------------------------------------
# 2. 使い方の誤り（argparse）と既定値
# ---------------------------------------------------------------------------


def test_missing_subcommand_is_a_usage_error() -> None:
    """サブコマンドを与えない起動は終了コード 2（使い方の誤り）。"""
    assert cli.main([]) == cli.EXIT_USAGE


def test_unknown_subcommand_is_a_usage_error() -> None:
    """未知のサブコマンドは終了コード 2。"""
    assert cli.main(["frobnicate"]) == cli.EXIT_USAGE


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    """`--help` は正常終了である（使い方の誤りではない）。"""
    assert cli.main(["--help"]) == cli.EXIT_OK
    captured = capsys.readouterr()
    for subcommand in ("build", "check", "select", "tolerance"):
        assert subcommand in captured.out


def test_default_paths_come_from_the_owning_modules() -> None:
    """既定パスを `cli` が再定義していない（値の正は各モジュール）。

    ⚠️ `--output-dir` の既定は `None` である。`export.DEFAULT_OUTPUT_DIR` を
    参照するにはモジュール直下で CAD 層を import せねばならず、
    `find_module_level_cad_imports`（タスク 1.6(c)）に反する。既定の解決は
    `export_parts` 側に委ねる。
    """
    parser = cli.build_parser()
    assert parser.parse_args(["tolerance"]).output == DEFAULT_DERIVATION_PATH
    assert parser.parse_args(["check"]).baseline == DEFAULT_BASELINE_PATH
    assert parser.parse_args(["build"]).baseline == DEFAULT_BASELINE_PATH
    assert parser.parse_args(["build"]).output_dir is None


# ---------------------------------------------------------------------------
# 3. `select`（要件 6.2 / tasks.md タスク 2.2(d)）
# ---------------------------------------------------------------------------


def test_select_succeeds_and_shows_failed_item_names(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`select` は正常終了し、不適合候補の**項目名**を出す（要件 6.2）。

    ⚠️ 候補の不適合は失敗ではない（`errors.py` の区分）。評価が完了した以上、
    終了コードは 0 である——不適合を 1 にすると、次点が基準を外れている現状で
    「検査の不一致」と読めてしまい、記録の不整合と区別できなくなる。
    """
    assert cli.main(["select"]) == cli.EXIT_OK
    out = capsys.readouterr().out

    assert "yamada-kagaku-no335" in out
    # 次点はテーパー上限超過で不適合（tasks.md タスク 2.2(b)）。項目名が出る。
    assert "seria-brooklyn-dustbox" in out
    assert "taper_deg" in out


def test_select_marks_illustrative_candidates(capsys: pytest.CaptureFixture[str]) -> None:
    """`illustrative-` 接頭辞の候補が**例示**として出力に現れる（タスク 2.2(d)）。

    ⚠️ 「実売調査した品」と「基準の説明のために置いた例」を取り違えると、
    調べていない品を買いに行く事故になる。`load_candidates` は `role` を戻り値に
    載せないため、識別子の接頭辞が唯一の手掛かりである。

    ⚠️ **双条件で固定する。** 「例示に印が付く」だけでは、全件に印を付ける実装が
    通ってしまう。
    """
    assert cli.main(["select"]) == cli.EXIT_OK
    out = capsys.readouterr().out

    document = json.loads(DEFAULT_CANDIDATES_PATH.read_text(encoding="utf-8"))
    entries = document["candidates"]
    assert entries, "出荷の候補ファイルが空である"

    lines = out.splitlines()
    for entry in entries:
        identifier = entry["identifier"]
        matched = [line for line in lines if identifier in line]
        assert len(matched) == 1, f"{identifier} の行が一意でない: {matched}"
        is_illustrative = entry["role"] == "illustrative_non_example"
        assert identifier.startswith(cli.ILLUSTRATIVE_PREFIX) == is_illustrative
        assert (cli.ILLUSTRATIVE_LABEL in matched[0]) == is_illustrative, (
            f"{identifier}: 例示の印と役割が一致しない ({matched[0]!r})"
        )


def test_select_reports_selection_input_errors_as_usage_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """選定の**入力**の誤り（`SelectionError`）は終了コード 2 である。

    ⚠️ `cli` は `selection` の関数をモジュール直下で import する（中核層であり
    遅延 import の対象ではない）ため、差し替えは**参照される側**である `cli` の
    名前に対して行う。
    """

    def _boom(path: Path | None = None) -> tuple[object, ...]:
        raise SelectionError("candidates.json: 未知のキー 'colour'")

    monkeypatch.setattr(cli, "load_candidates", _boom)
    assert cli.main(["select"]) == cli.EXIT_USAGE
    assert "colour" in capsys.readouterr().err


def test_select_does_not_touch_the_shape_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """`select` は形状層を一切呼ばない（CAD 非導入環境で完走する根拠）。"""
    from catch_mechanism import shapes as shapes_module

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("select が形状構築を呼んだ")

    monkeypatch.setattr(shapes_module, "build_parts", _forbidden)
    assert cli.main(["select"]) == cli.EXIT_OK


# ---------------------------------------------------------------------------
# 4. `tolerance`（要件 7.5）
# ---------------------------------------------------------------------------


def test_tolerance_writes_the_derivation_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`tolerance --output` が導出記録を書き、再導出と一致する（要件 7.5）。"""
    output = tmp_path / "catch-opening.json"
    assert cli.main(["tolerance", "--output", str(output)]) == cli.EXIT_OK
    assert output.exists()
    assert load_derivation(output) == derive_position_tolerance(_params())

    out = capsys.readouterr().out
    assert "position_tolerance_mm" in out
    assert str(output) in out


def _sim_config(
    tolerance_mm: float | None, *, provenance: str | None = None
) -> dict[str, object]:
    """`trajectory_sim` 設定の該当部分だけを持つ最小の文書。"""
    catch: dict[str, object] = {"policy": "stop_and_wait"}
    if tolerance_mm is not None:
        catch["position_tolerance_mm"] = tolerance_mm
    parameters: dict[str, object] = {"catch": catch}
    if provenance is not None:
        parameters["provenance"] = {"catch.position_tolerance_mm": provenance}
    return {"parameters": parameters}


def test_tolerance_check_passes_when_the_config_agrees(tmp_path: Path) -> None:
    """`--check` は設定値が導出値と一致すれば正常終了する（要件 7.5, 7.6）。"""
    derivation = derive_position_tolerance(_params())
    config = tmp_path / "sim.json"
    config.write_text(
        json.dumps(
            _sim_config(
                derivation.position_tolerance_mm,
                provenance=derivation.provenance.value,
            )
        ),
        encoding="utf-8",
    )
    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "rec.json"), "--check", str(config)]
    )
    assert exit_code == cli.EXIT_OK


def test_tolerance_check_fails_with_both_values_when_the_config_disagrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """不一致は終了コード 1 で、双方の値と参照元を示す（要件 7.7）。"""
    derivation = derive_position_tolerance(_params())
    stale = derivation.position_tolerance_mm - 10.0
    config = tmp_path / "sim.json"
    config.write_text(json.dumps(_sim_config(stale)), encoding="utf-8")

    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "rec.json"), "--check", str(config)]
    )
    assert exit_code == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    assert str(stale) in err
    assert str(derivation.position_tolerance_mm) in err
    assert str(config) in err


def test_tolerance_check_fails_when_the_config_does_not_record_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """設定に値が記録されていない場合も検査の不一致（終了コード 1）である。

    ⚠️ **記録が無いことを一致として読み飛ばさない。** 還元（タスク 5.4）が済んで
    いない設定は「一致していない」のであって「検査対象外」ではない。
    """
    config = tmp_path / "sim.json"
    config.write_text(json.dumps(_sim_config(None)), encoding="utf-8")
    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "rec.json"), "--check", str(config)]
    )
    assert exit_code == cli.EXIT_MISMATCH
    assert "position_tolerance_mm" in capsys.readouterr().err


def test_tolerance_check_fails_when_the_recorded_provenance_disagrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """設定が出所を記録していて、それが導出の出所と食い違えば終了コード 1。

    ⚠️ 値が合っていても出所が「実測」を名乗っていれば、まだ測っていない値が
    実測として下流へ流れる（要件 1.5 / 7.4）。
    """
    derivation = derive_position_tolerance(_params())
    wrong = "measured" if derivation.provenance.value != "measured" else "assumed"
    config = tmp_path / "sim.json"
    config.write_text(
        json.dumps(_sim_config(derivation.position_tolerance_mm, provenance=wrong)),
        encoding="utf-8",
    )
    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "rec.json"), "--check", str(config)]
    )
    assert exit_code == cli.EXIT_MISMATCH
    assert wrong in capsys.readouterr().err


def test_tolerance_check_rejects_a_config_that_is_not_json(tmp_path: Path) -> None:
    """設定が JSON として読めない場合は入力の誤り（終了コード 2）である。"""
    config = tmp_path / "sim.json"
    config.write_text("{ not json", encoding="utf-8")
    exit_code = cli.main(
        ["tolerance", "--output", str(tmp_path / "rec.json"), "--check", str(config)]
    )
    assert exit_code == cli.EXIT_USAGE


def test_tolerance_reports_an_unwritable_output_as_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """書き出し先が書けない場合、追跡情報ではなく終了コード 2 と1行の理由を返す。"""
    unwritable = tmp_path / "no-such-directory" / "rec.json"
    assert cli.main(["tolerance", "--output", str(unwritable)]) == cli.EXIT_USAGE
    assert "rec.json" in capsys.readouterr().err


def test_tolerance_does_not_touch_the_shape_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tolerance` は形状層を一切呼ばない（CAD 非導入環境で完走する根拠）。"""
    from catch_mechanism import shapes as shapes_module

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("tolerance が形状構築を呼んだ")

    monkeypatch.setattr(shapes_module, "build_parts", _forbidden)
    assert cli.main(["tolerance", "--output", str(tmp_path / "rec.json")]) == cli.EXIT_OK


# ---------------------------------------------------------------------------
# 5. `check`（要件 4.3 / タスク 4.1「識別子のみの検査を選べる切り替え」）
# ---------------------------------------------------------------------------


def test_check_digest_only_succeeds_without_regenerating_the_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--digest-only` は形状を再生成せずに完了する（CAD 非導入環境で正常終了）。"""
    from catch_mechanism import shapes as shapes_module

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("--digest-only が形状構築を呼んだ")

    monkeypatch.setattr(shapes_module, "build_parts", _forbidden)

    parts = _fake_parts(segment_part_names(_params()))
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)

    assert cli.main(["check", "--digest-only", "--baseline", str(baseline)]) == cli.EXIT_OK
    assert "--digest-only" in capsys.readouterr().out


def test_check_digest_only_detects_a_stale_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録の識別子が現在の寸法設定と食い違えば終了コード 1（要件 4.5 の入口）。"""
    parts = _fake_parts(segment_part_names(_params()))
    stale_digest = "sha256:" + "0" * 64
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts, digest=stale_digest)

    assert cli.main(["check", "--digest-only", "--baseline", str(baseline)]) == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    assert stale_digest in err
    assert parameters_digest(_params()) in err
    assert DEFAULT_DIMENSIONS_PATH.name in err


def test_check_reports_a_missing_record_as_an_input_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録が無い場合は、そのファイル名を示して終了コード 2 で失敗する。

    ⚠️ **記録の不在を成功として読み飛ばさない**（`metrics.load_baseline` の規律）。
    既定の記録を作るのはタスク 4.2 であり、それまで既定パスでの `check` は
    ここで失敗する。
    """
    missing = tmp_path / "does-not-exist.json"
    assert cli.main(["check", "--digest-only", "--baseline", str(missing)]) == cli.EXIT_USAGE
    assert str(missing) in capsys.readouterr().err


def test_check_regenerates_and_agrees_with_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--digest-only` を付けない `check` は再生成して照合する（要件 4.3）。"""
    from catch_mechanism import shapes as shapes_module

    parts = _fake_parts(segment_part_names(_params()))
    monkeypatch.setattr(shapes_module, "build_parts", lambda params: parts)
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_OK


def test_check_reports_part_name_and_both_values_on_a_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """不一致は終了コード 1 で、部品名と双方の値を示す（要件 4.4）。"""
    from catch_mechanism import shapes as shapes_module

    recorded = _fake_parts(segment_part_names(_params()), volume_mm3=1000.0)
    regenerated = _fake_parts(segment_part_names(_params()), volume_mm3=2000.0)
    monkeypatch.setattr(shapes_module, "build_parts", lambda params: regenerated)
    baseline = _write_baseline_file(tmp_path / "baseline.json", recorded)

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    assert recorded[0].name in err
    assert "volume_mm3" in err
    assert "1000.0" in err
    assert "2000.0" in err


def test_check_distinguishes_a_vanished_part_from_other_deviations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """部品が消えた／増えたを、体積などの乖離と**区別して**出力する。

    tasks.md「Implementation Notes」タスク 2.4(b):「`PRESENCE_FIELD` / `PRESENT` /
    `ABSENT` は公開定数なので、タスク 4.1 の `cli check` はこの項目名で
    『部品が消えた／増えた』を他の乖離と区別して出力できる」。
    """
    from catch_mechanism import shapes as shapes_module

    names = segment_part_names(_params())
    assert len(names) >= 2, "分割数が 1 の設定では本検査が成立しない"
    recorded = _fake_parts(names)
    regenerated = _fake_parts(names[:-1])
    monkeypatch.setattr(shapes_module, "build_parts", lambda params: regenerated)
    baseline = _write_baseline_file(tmp_path / "baseline.json", recorded)

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    vanished = names[-1]
    lines = [line for line in err.splitlines() if vanished in line]
    assert len(lines) == 1, f"消えた部品の行が一意でない: {lines}"
    assert cli.VANISHED_LABEL in lines[0]
    # ⚠️ 不在の部品について体積・境界箱の行を併せて出さない（タスク 2.4(b)）。
    assert "volume_mm3" not in lines[0]


def test_check_reports_an_extra_part_as_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録に無い部品が再生成に現れた場合は「増えた」と出す（`compare_metrics` の逆向き）。"""
    from catch_mechanism import shapes as shapes_module

    names = segment_part_names(_params())
    recorded = _fake_parts(names[:-1])
    regenerated = _fake_parts(names)
    monkeypatch.setattr(shapes_module, "build_parts", lambda params: regenerated)
    baseline = _write_baseline_file(tmp_path / "baseline.json", recorded)

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_MISMATCH

    err = capsys.readouterr().err
    appeared = names[-1]
    lines = [line for line in err.splitlines() if appeared in line]
    assert len(lines) == 1, f"増えた部品の行が一意でない: {lines}"
    assert cli.APPEARED_LABEL in lines[0]


def test_check_without_digest_only_needs_the_shape_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """形状ライブラリが無い環境の `check`（非 `--digest-only`）は終了コード 3。

    ⚠️ **成功として黙って読み飛ばさない。** 照合したつもりで何も照合していない
    状態は、記録が壊れていることに気付けないまま流れる。
    """
    from catch_mechanism import shapes as shapes_module

    def _no_cad(params: object) -> object:
        raise ImportError("No module named 'build123d'")

    monkeypatch.setattr(shapes_module, "build_parts", _no_cad)
    parts = _fake_parts(segment_part_names(_params()))
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_CAD_UNAVAILABLE
    assert "cad" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 6. `build`（要件 3.2, 5.3）
# ---------------------------------------------------------------------------


def test_build_maps_a_missing_shape_library_to_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`export_parts` の裸の `ImportError` が終了コード 3 になる（要件 5.3）。"""
    from catch_mechanism import export as export_module

    def _no_cad(params: object, output_dir: object = None) -> object:
        raise ImportError("No module named 'build123d'")

    monkeypatch.setattr(export_module, "export_parts", _no_cad)
    assert cli.main(["build", "--output-dir", str(tmp_path)]) == cli.EXIT_CAD_UNAVAILABLE

    err = capsys.readouterr().err
    assert "cad" in err  # 導入方法を示す（`errors.CadUnavailableError` の規律）
    assert list(tmp_path.iterdir()) == []


def test_build_maps_the_cad_unavailable_error_to_its_own_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`CadUnavailableError` も終了コード 3 である。"""
    from catch_mechanism import export as export_module

    def _no_cad(params: object, output_dir: object = None) -> object:
        raise CadUnavailableError("形状ライブラリが無い")

    monkeypatch.setattr(export_module, "export_parts", _no_cad)
    assert cli.main(["build", "--output-dir", str(tmp_path)]) == cli.EXIT_CAD_UNAVAILABLE


def test_build_maps_a_geometry_error_to_the_usage_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """造形制約の違反（`GeometryError`）は終了コード 2 であり 3 ではない。"""
    from catch_mechanism import export as export_module

    def _violation(params: object, output_dir: object = None) -> object:
        raise GeometryError("axis='x' excess_mm=12.0")

    monkeypatch.setattr(export_module, "export_parts", _violation)
    assert cli.main(["build", "--output-dir", str(tmp_path)]) == cli.EXIT_USAGE


def _patch_export(monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    """`export_parts` を、形状ライブラリを要さない偽の書き出しへ差し替える。"""
    from catch_mechanism import export as export_module
    from catch_mechanism.export import ExportedPart

    names = segment_part_names(_params())
    exported = tuple(
        ExportedPart(name=name, metrics=_fake_metrics(name), paths=()) for name in names
    )
    monkeypatch.setattr(
        export_module, "export_parts", lambda params, output_dir=None: exported
    )
    return names


def test_build_updates_the_baseline_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--update-baseline` を付けない `build` は記録を書かない。"""
    _patch_export(monkeypatch)
    baseline = tmp_path / "baseline.json"
    exit_code = cli.main(
        ["build", "--output-dir", str(tmp_path / "cad"), "--baseline", str(baseline)]
    )
    assert exit_code == cli.EXIT_OK
    assert not baseline.exists()


def test_build_update_baseline_records_the_current_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--update-baseline` が現在の識別子と全部品の指標を記録する。

    ⚠️ 記録の**内容**（出荷ファイル）はタスク 4.2 の所有であり、ここでは
    `tmp_path` の記録に対して書き出し経路だけを固定する。
    """
    names = _patch_export(monkeypatch)
    baseline = tmp_path / "baseline.json"
    exit_code = cli.main(
        [
            "build",
            "--output-dir",
            str(tmp_path / "cad"),
            "--update-baseline",
            "--baseline",
            str(baseline),
        ]
    )
    assert exit_code == cli.EXIT_OK

    recorded = load_baseline(baseline)
    assert recorded.parameters_digest == parameters_digest(_params())
    assert set(recorded.parts) == set(names)
    # ⚠️ 体積の相対許容差は 1e-7 を下回らない（tasks.md タスク 3.3 の申し送り）。
    assert recorded.volume_rel_tolerance >= 1e-7


def test_build_update_baseline_preserves_existing_tolerances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既存の記録がある場合、許容差はそのまま引き継ぐ（記録の所有者はタスク 4.2）。"""
    names = _patch_export(monkeypatch)
    baseline = tmp_path / "baseline.json"
    write_baseline(
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest="sha256:" + "0" * 64,
            volume_rel_tolerance=2.5e-5,
            bbox_abs_tolerance_mm=0.125,
            generator_version="previous",
            parts={names[0]: _fake_metrics(names[0])},
        ),
        baseline,
    )

    exit_code = cli.main(
        [
            "build",
            "--output-dir",
            str(tmp_path / "cad"),
            "--update-baseline",
            "--baseline",
            str(baseline),
        ]
    )
    assert exit_code == cli.EXIT_OK

    recorded = load_baseline(baseline)
    assert recorded.volume_rel_tolerance == 2.5e-5
    assert recorded.bbox_abs_tolerance_mm == 0.125
    assert recorded.parameters_digest == parameters_digest(_params())


# ---------------------------------------------------------------------------
# 7. 遅延 import と入口の配線（要件 5.2, 5.7 / タスク 1.6(c)）
# ---------------------------------------------------------------------------


def test_importing_the_cli_does_not_import_the_shape_library() -> None:
    """`import catch_mechanism.cli` が `build123d` を読み込まない（遅延 import）。"""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import catch_mechanism.cli; "
            "print(any(name == 'build123d' or name.startswith('build123d.') "
            "for name in sys.modules))",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "False"


def test_module_entrypoint_runs_select_without_the_shape_library(tmp_path: Path) -> None:
    """形状ライブラリを遮断した実プロセスで `select` が正常終了する（要件 5.7）。"""
    completed = _run_module(["select"], stub_dir=_nocad_stub(tmp_path))
    assert completed.returncode == 0, completed.stderr
    assert "yamada-kagaku-no335" in completed.stdout


def test_module_entrypoint_runs_tolerance_without_the_shape_library(tmp_path: Path) -> None:
    """形状ライブラリを遮断した実プロセスで `tolerance` が正常終了する（要件 5.7, 7.5）。"""
    output = tmp_path / "catch-opening.json"
    completed = _run_module(
        ["tolerance", "--output", str(output)], stub_dir=_nocad_stub(tmp_path)
    )
    assert completed.returncode == 0, completed.stderr
    assert load_derivation(output) == derive_position_tolerance(_params())


def test_module_entrypoint_runs_digest_only_check_without_the_shape_library(
    tmp_path: Path,
) -> None:
    """形状ライブラリを遮断した実プロセスで `check --digest-only` が正常終了する。"""
    parts = _fake_parts(segment_part_names(_params()))
    baseline = _write_baseline_file(tmp_path / "baseline.json", parts)
    completed = _run_module(
        ["check", "--digest-only", "--baseline", str(baseline)],
        stub_dir=_nocad_stub(tmp_path),
    )
    assert completed.returncode == 0, completed.stderr


def test_module_entrypoint_build_fails_with_the_cad_exit_code(tmp_path: Path) -> None:
    """形状ライブラリを遮断した実プロセスで `build` が終了コード 3 で失敗する。

    ⚠️ **これが本タスクの観測可能な完了状態の核心である**——生成の要求が
    「成功」でも「入力の誤り」でもなく、**専用の終了コード**で失敗する。
    """
    output_dir = tmp_path / "cad"
    completed = _run_module(
        ["build", "--output-dir", str(output_dir)], stub_dir=_nocad_stub(tmp_path)
    )
    assert completed.returncode == 3, (completed.stdout, completed.stderr)
    assert "cad" in completed.stderr
    assert not output_dir.exists() or list(output_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# 8. 形状ライブラリ導入環境での通し（要件 3.2, 4.3）
# ---------------------------------------------------------------------------


@requires_cad
def test_build_then_check_round_trip_with_the_shape_library(tmp_path: Path) -> None:
    """`build --update-baseline` → `check` が通しで成立する（要件 3.2, 4.3）。

    ⚠️ **ロケールを壊さないことも併せて固定する。** `build` は 3MF の書き出しで
    lib3mf を呼ぶ。lib3mf はプロセスのロケールを `C` へ落として戻さないため
    （tasks.md「Implementation Notes」タスク 3.4(a)）、`export.py` の退避・復元が
    `cli` の経路でも効いていることをここで観測する。⚠️ 効いていないと、
    サブプロセスの日本語出力を復号する上流の検査が**フルスイート実行時のみ**
    `UnicodeDecodeError` で落ちる。
    """
    output_dir = tmp_path / "cad"
    baseline = tmp_path / "baseline.json"

    before = locale.setlocale(locale.LC_ALL)
    exit_code = cli.main(
        [
            "build",
            "--output-dir",
            str(output_dir),
            "--update-baseline",
            "--baseline",
            str(baseline),
        ]
    )
    assert exit_code == cli.EXIT_OK
    assert locale.setlocale(locale.LC_ALL) == before

    produced = sorted(path.name for path in output_dir.iterdir())
    assert produced, "生成物が出力先に無い"
    assert all(name.endswith((".step", ".stl", ".3mf")) for name in produced)

    recorded = load_baseline(baseline)
    assert set(recorded.parts) == set(segment_part_names(_params()))
    assert recorded.generator_version

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_OK


@requires_cad
def test_check_fails_when_the_record_is_perturbed(tmp_path: Path) -> None:
    """記録を人為的にずらすと `check` が終了コード 1 で失敗する（要件 4.4）。"""
    baseline = tmp_path / "baseline.json"
    exit_code = cli.main(
        [
            "build",
            "--output-dir",
            str(tmp_path / "cad"),
            "--update-baseline",
            "--baseline",
            str(baseline),
        ]
    )
    assert exit_code == cli.EXIT_OK

    document = json.loads(baseline.read_text(encoding="utf-8"))
    first = sorted(document["parts"])[0]
    document["parts"][first]["volume_mm3"] *= 1.5
    baseline.write_text(json.dumps(document), encoding="utf-8")

    assert cli.main(["check", "--baseline", str(baseline)]) == cli.EXIT_MISMATCH
