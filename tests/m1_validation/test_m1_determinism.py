"""決定性と記録再生の一致の検証（tasks.md タスク 8.3、要件 12.4, 12.5）。

観測可能な完了状態（tasks.md 8.3）を固定する:

- **同一入力に対する繰り返し実行が、同一の集計値と同一の判定を返す**
- **記録済みデータを実機なしで読み直し、同じ集計と判断が再現される**
- **再抽出を含む処理が乱数種の固定によって決定的である**

ファイル名について: `tests/trajectory_sim/test_determinism.py` と衝突する
ため（`tests/` 配下に `__init__.py` が無く、pytest の既定 import mode では
同名ファイルがツリー全体で一意でなければならない）、`sensing_foundation`
タスク1.5 の回避規約に従い `test_m1_determinism.py` とする
（tasks.md「Implementation Notes」タスク1.1・8.2 と同じ理由）。

合成トラジェクトリ・環境構築のヘルパは `test_e2e_synthetic.py`
（タスク 8.2、本タスクの `_Depends:`）のものをそのまま再利用する
（同ディレクトリ内 import。`tests/` 非パッケージのため、同じディレクトリの
モジュールをファイル名で直接 import できる——`m1fixtures.py` と同じ仕組み）。
軌道を再定義すると2つの合成入力が独立に食い違っていく余地を生むため、
1つの定義だけを持つ。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from test_e2e_synthetic import (
    Env,
    _camera_points,
    _evaluation_argv,
    _make_env,
    _run,
    _throw,
    _write_truth,
)

from m1_validation.cli import main

RECORD_IDS: tuple[str, ...] = ("throw-0001", "throw-0002")

#: 比較の前に取り除くキー。**値そのものが問いたい対象ではない**フィールド
#: （セッション識別子・書き出し先パス）は、テスト環境ごとに異なる `tmp_path`
#: 由来で必然的に違う値になる。ここを比較に含めると「決定的でない」ではなく
#: 「テスト環境が違う」という無関係な差分で落ちる。
_VOLATILE_KEYS = {"session_id", "session_ids", "written"}


def _strip_volatile(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _strip_volatile(item)
            for key, item in value.items()
            if key not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


def _generate_records(
    env: Env,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *,
    record_ids: Sequence[str],
    points: list,
) -> None:
    for record_id in record_ids:
        code, _, err = _throw(env, capsys, monkeypatch, record_id=record_id, points=points)
        assert code == 0, err
    _write_truth(env, record_ids)


def _run_evaluation_chain(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> dict[str, object]:
    results: dict[str, object] = {}
    for command in ("measure", "attribute", "judge-oq27", "material-oq05", "budget"):
        code, payload, err = _run(env, _evaluation_argv(env, command), capsys)
        assert code == 0, err
        results[command] = payload
    return results


# ---------------------------------------------------------------------------
# 1. 同一入力の2回実行が完全一致する
# ---------------------------------------------------------------------------


class TestRepeatedExecutionIsIdentical:
    def test_repeated_evaluation_of_the_same_recorded_input_is_identical(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """要件 12.4: 同一入力に対する繰り返し実行が同一の集計値・同一の判定を返す。

        **記録は1回だけ作る。** `measure` / `attribute` / `judge-oq27` /
        `material-oq05` / `budget` は評価系サブコマンドであり、
        `--records` / `--truth` / `--log` を読むだけで新しい投擲を実行しない
        （`run-throw` を経由しない）。この評価チェーンを同じ記録に対して
        2回呼び、出力が完全一致することを確かめる。

        **`run-throw` 自体を2回（2つの独立した環境で）実行して比べる設計を
        あえて採らない。** ログの段階別レイテンシは実時計（wall-clock）に
        基づく実測値であり、2回の実行が実際に異なる時刻に起きる以上、
        レイテンシの数値そのものは原理的に再現しない——これは実装の非決定性
        ではなく、計測が実時間を相手にしていることの当然の帰結である
        （`tech.md` 開発標準5「計測が計測対象を歪めないこと」と対を成す、
        「計測対象そのものが実時間である」という別の性質）。要件 12.4 が
        求める決定性は**同じ観測データに対する集計・判定のロジック**の
        決定性であり、それは記録を固定して評価だけを繰り返すことで検査できる。
        """
        env = _make_env(tmp_path, "repeat")
        points = _camera_points()
        _generate_records(
            env, capsys, monkeypatch, record_ids=RECORD_IDS, points=points
        )

        first = _run_evaluation_chain(env, capsys)
        second = _run_evaluation_chain(env, capsys)

        assert first == second


# ---------------------------------------------------------------------------
# 2. 再抽出（ブートストラップ）を含む処理の決定性
# ---------------------------------------------------------------------------


class TestBootstrapResamplingIsDeterministic:
    def test_repeated_attribute_calls_on_the_same_records_are_identical(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """要件 12.4: 帰属（`attribute`）は観測品質からの再抽出を含む
        （`ErrorAttributor` の観測ノイズ由来ばらつき見積もり）。同じ記録・同じ
        真値に対して**新しい記録を作らずに** `attribute` だけを繰り返し呼び、
        乱数種の固定によって出力が揺れないことを確かめる。
        """
        env = _make_env(tmp_path, "bootstrap")
        points = _camera_points()
        record_ids = ("throw-0001", "throw-0002", "throw-0003")
        for record_id in record_ids:
            code, _, err = _throw(
                env, capsys, monkeypatch, record_id=record_id, points=points
            )
            assert code == 0, err
        _write_truth(env, record_ids)

        payloads = []
        for _ in range(4):
            code, payload, err = _run(env, _evaluation_argv(env, "attribute"), capsys)
            assert code == 0, err
            payloads.append(payload)

        first = payloads[0]
        assert first["groups"][0]["attribution"], "帰属結果が空では決定性を検査できない"
        for other in payloads[1:]:
            assert other == first


# ---------------------------------------------------------------------------
# 3. 記録済みデータの実機なし再生
# ---------------------------------------------------------------------------


class TestRecordedDataReplayReproducesTheSameResult:
    def test_replaying_records_copied_elsewhere_without_gateway_or_supplier_matches(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """要件 12.5: 記録済みの実データに対して、実機なしで同じ集計と判断が
        再現できる。

        記録一式（投擲記録・真値・ログ）を**別ディレクトリへコピー**し
        （`tech.md`「WSL で開発 → Git push → Pi で pull → 実機テスト・実データ
        記録 → WSL へ持ち帰り解析」を模す）、そのコピーに対して
        `gateway`/`supplier` を一切注入せず `main()` を直接呼ぶ
        （評価系サブコマンドは上流基盤に触れないので、ダブルなしで動く
        ことが「実機なし」の最も強い証拠になる）。集計・判断が完全一致
        することを確かめる。
        """
        env = _make_env(tmp_path, "replay-src")
        points = _camera_points()
        for record_id in RECORD_IDS:
            code, _, err = _throw(
                env, capsys, monkeypatch, record_id=record_id, points=points
            )
            assert code == 0, err
        _write_truth(env, RECORD_IDS)

        original: dict[str, object] = {}
        for command in ("measure", "attribute", "judge-oq27", "budget"):
            code, payload, err = _run(env, _evaluation_argv(env, command), capsys)
            assert code == 0, err
            original[command] = payload

        replay_root = tmp_path / "replay-dest"
        replay_root.mkdir()
        replay_records = replay_root / "throws.ndjson"
        replay_records.write_text(
            env.records_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        replay_truth = replay_root / "truth.json"
        replay_truth.write_text(
            env.truth_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        source_log = env.tmp_path / "logs" / f"{env.session_id}.ndjson"
        replay_log = replay_root / "session.ndjson"
        replay_log.write_text(source_log.read_text(encoding="utf-8"), encoding="utf-8")

        replayed: dict[str, object] = {}
        for command in ("measure", "attribute", "judge-oq27", "budget"):
            argv = [
                command,
                "--layout-file",
                str(env.layout_path),
                "--layout-id",
                "throw-a",
                "--output-root",
                str(replay_root / "out"),
                "--bootstrap-iterations",
                "8",
                "--session-id",
                env.session_id,
                "--records",
                str(replay_records),
                "--truth",
                str(replay_truth),
                "--log",
                str(replay_log),
            ]
            code = main(argv)
            captured = capsys.readouterr()
            assert code == 0, captured.err
            replayed[command] = json.loads(captured.out)

        assert _strip_volatile(replayed) == _strip_volatile(original)
