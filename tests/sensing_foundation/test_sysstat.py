"""`sensing_foundation.sysstat.Sysstat` に対するテスト（タスク 1.4）。

観測可能な完了状態（tasks.md 1.4）を固定する:
- `/proc` が無い（読めない）状況を模したテストで例外が出ず、
  すべて欠測（`None`）の `ResourceSample` が返る
- CPU 使用率は前回サンプルとの差分で算出し、初回は欠測として返る
- 搭載 RAM 総量（`system_total_bytes`）は CPU の差分を必要とせず、
  初回の `sample()` 呼び出しでも単独で取得できる
- `Sysstat.available()` は `/proc` が読めるかどうかを例外なく返す

実際の生 `/proc` の内容は CI/実行環境ごとに非決定的なので、モジュール内部の
`/proc` 参照先（`_PROC_ROOT`）を monkeypatch で `tmp_path` 配下の偽 `/proc`
ライクなディレクトリへ差し替え、既知の内容から既知の出力を検証する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import sensing_foundation.sysstat as sysstat_module
from sensing_foundation.sysstat import ResourceSample, Sysstat


def _write_proc_stat(proc_root: Path, *, cpu_fields: list[int]) -> None:
    line = "cpu  " + " ".join(str(v) for v in cpu_fields)
    (proc_root / "stat").write_text(line + "\ncpu0 0 0 0 0 0 0 0 0 0 0\n", encoding="utf-8")


def _write_self_stat(proc_root: Path, *, utime: int, stime: int) -> None:
    self_dir = proc_root / "self"
    self_dir.mkdir(parents=True, exist_ok=True)
    # フィールドは 1-indexed: 1=pid, 2=comm, 3=state, ..., 14=utime, 15=stime
    fields = ["1234", "(python3)", "R", "1", "1", "1", "0", "-1", "0", "0", "0", "0", "0"]
    fields += [str(utime), str(stime)]
    fields += ["0"] * 30  # 残りのフィールドは本テストでは使わない
    (self_dir / "stat").write_text(" ".join(fields) + "\n", encoding="utf-8")


def _write_self_status(proc_root: Path, *, rss_kb: int) -> None:
    self_dir = proc_root / "self"
    self_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "Name:\tpython3\n"
        "State:\tR (running)\n"
        f"VmRSS:\t{rss_kb} kB\n"
        "Threads:\t1\n"
    )
    (self_dir / "status").write_text(content, encoding="utf-8")


def _write_meminfo(proc_root: Path, *, total_kb: int, available_kb: int) -> None:
    content = (
        f"MemTotal:       {total_kb} kB\n"
        f"MemFree:        {available_kb} kB\n"
        f"MemAvailable:   {available_kb} kB\n"
        "Buffers:            1234 kB\n"
    )
    (proc_root / "meminfo").write_text(content, encoding="utf-8")


def _setup_fake_proc(
    proc_root: Path,
    *,
    cpu_fields: list[int],
    utime: int,
    stime: int,
    rss_kb: int,
    total_kb: int,
    available_kb: int,
) -> None:
    proc_root.mkdir(parents=True, exist_ok=True)
    _write_proc_stat(proc_root, cpu_fields=cpu_fields)
    _write_self_stat(proc_root, utime=utime, stime=stime)
    _write_self_status(proc_root, rss_kb=rss_kb)
    _write_meminfo(proc_root, total_kb=total_kb, available_kb=available_kb)


@pytest.fixture
def fake_proc_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`sysstat._PROC_ROOT` を偽の `/proc` ディレクトリへ差し替える。"""
    proc_root = tmp_path / "proc"
    monkeypatch.setattr(sysstat_module, "_PROC_ROOT", proc_root)
    return proc_root


class TestResourceSampleShape:
    """`ResourceSample` が frozen かつ slots のデータクラスであることを固定する。"""

    def test_is_frozen(self) -> None:
        sample = ResourceSample(
            cpu_percent=None,
            process_rss_bytes=None,
            system_available_bytes=None,
            system_total_bytes=None,
        )
        with pytest.raises(AttributeError):
            sample.cpu_percent = 1.0  # type: ignore[misc]

    def test_has_no_dict_due_to_slots(self) -> None:
        sample = ResourceSample(
            cpu_percent=None,
            process_rss_bytes=None,
            system_available_bytes=None,
            system_total_bytes=None,
        )
        assert not hasattr(sample, "__dict__")


class TestFirstSampleCpuIsMissing:
    """初回サンプルは前回値が無いため cpu_percent が欠測になることを固定する。"""

    def test_first_sample_reports_cpu_percent_as_none(self, fake_proc_root: Path) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )

        stat = Sysstat()
        sample = stat.sample()

        assert sample.cpu_percent is None

    def test_first_sample_still_reports_memory_fields(self, fake_proc_root: Path) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )

        stat = Sysstat()
        sample = stat.sample()

        assert sample.process_rss_bytes == 2048 * 1024
        assert sample.system_total_bytes == 16_000_000 * 1024
        assert sample.system_available_bytes == 8_000_000 * 1024


class TestSecondSampleComputesCpuDelta:
    """2回目以降のサンプルは前回との差分から実数の cpu_percent を返すことを固定する。"""

    def test_second_sample_returns_numeric_cpu_percent(self, fake_proc_root: Path) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],  # total=10000
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )
        stat = Sysstat()
        first = stat.sample()
        assert first.cpu_percent is None

        # システム全体の総 jiffies を 10000 -> 11000 (+1000) 進め、
        # プロセスの utime+stime を 15 -> 115 (+100) 進める。
        # => cpu_percent = 100 / 1000 * 100 = 10.0
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1100, 0, 600, 9300, 0, 0, 0, 0, 0, 0],  # total=11000
            utime=60,
            stime=55,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )
        second = stat.sample()

        assert second.cpu_percent is not None
        assert second.cpu_percent == pytest.approx(10.0)

    def test_cpu_percent_is_bounded_between_zero_and_hundred(self, fake_proc_root: Path) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )
        stat = Sysstat()
        stat.sample()

        # システム total の delta が 0 の異常系でも例外を出さないこと。
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],  # 変化なし
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )
        second = stat.sample()

        assert second.cpu_percent is not None
        assert 0.0 <= second.cpu_percent <= 100.0


class TestUnreadableProcFallsBackToAllMissing:
    """`/proc` が読めない環境では例外を出さず、すべて欠測を返すことを固定する。"""

    def test_sample_returns_all_none_when_proc_directory_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # tmp_path 配下に存在しないパスを /proc として差し替える。
        missing_proc_root = tmp_path / "no_such_proc"
        monkeypatch.setattr(sysstat_module, "_PROC_ROOT", missing_proc_root)

        stat = Sysstat()
        sample = stat.sample()

        assert sample == ResourceSample(
            cpu_percent=None,
            process_rss_bytes=None,
            system_available_bytes=None,
            system_total_bytes=None,
        )

    def test_sample_does_not_raise_when_read_raises_oserror(
        self, fake_proc_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )

        def _raise_oserror(*args: object, **kwargs: object) -> str:
            raise OSError("permission denied (simulated)")

        monkeypatch.setattr(Path, "read_text", _raise_oserror)

        stat = Sysstat()
        sample = stat.sample()  # 例外を送出してはならない

        assert sample.cpu_percent is None
        assert sample.process_rss_bytes is None
        assert sample.system_available_bytes is None
        assert sample.system_total_bytes is None

    def test_sample_does_not_raise_on_malformed_proc_content(
        self, fake_proc_root: Path
    ) -> None:
        fake_proc_root.mkdir(parents=True, exist_ok=True)
        (fake_proc_root / "stat").write_text("not a valid cpu line\n", encoding="utf-8")
        self_dir = fake_proc_root / "self"
        self_dir.mkdir(parents=True, exist_ok=True)
        (self_dir / "stat").write_text("garbage\n", encoding="utf-8")
        (self_dir / "status").write_text("garbage\n", encoding="utf-8")
        (fake_proc_root / "meminfo").write_text("garbage\n", encoding="utf-8")

        stat = Sysstat()
        sample = stat.sample()  # 例外を送出してはならない

        assert sample == ResourceSample(
            cpu_percent=None,
            process_rss_bytes=None,
            system_available_bytes=None,
            system_total_bytes=None,
        )

    def test_available_returns_false_when_proc_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing_proc_root = tmp_path / "no_such_proc"
        monkeypatch.setattr(sysstat_module, "_PROC_ROOT", missing_proc_root)

        assert Sysstat.available() is False

    def test_available_does_not_raise_on_oserror(
        self, fake_proc_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )

        def _raise_oserror(*args: object, **kwargs: object) -> bool:
            raise OSError("permission denied (simulated)")

        monkeypatch.setattr(Path, "is_file", _raise_oserror)

        assert Sysstat.available() is False  # 例外を送出してはならない


class TestProcAvailableHappyPath:
    """`/proc` が読める環境では `available()` が True を返すことを固定する。"""

    def test_available_returns_true_when_meminfo_present(self, fake_proc_root: Path) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )

        assert Sysstat.available() is True


class TestResetAfterFailureRestartsFirstSampleSemantics:
    """読み取り失敗を挟んだ後は、次の成功サンプルが再び初回扱い（cpu None）になることを固定する。

    失敗を挟んで内部状態が古いまま次回の差分計算に使われると、
    長時間欠測後の再開時に誤った（巨大な経過分の）差分から不正確な
    cpu_percent を算出してしまう。失敗時は内部の前回値を破棄すべきである。
    """

    def test_cpu_percent_is_none_again_after_a_failed_sample_in_between(
        self, fake_proc_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_fake_proc(
            fake_proc_root,
            cpu_fields=[1000, 0, 500, 8500, 0, 0, 0, 0, 0, 0],
            utime=10,
            stime=5,
            rss_kb=2048,
            total_kb=16_000_000,
            available_kb=8_000_000,
        )
        stat = Sysstat()
        first = stat.sample()
        assert first.cpu_percent is None

        # 一時的に /proc を読めなくする。
        missing_proc_root = fake_proc_root.parent / "gone"
        monkeypatch.setattr(sysstat_module, "_PROC_ROOT", missing_proc_root)
        failed = stat.sample()
        assert failed.cpu_percent is None
        assert failed.system_total_bytes is None

        # 復旧後、次のサンプルは再び「初回」扱いとなり cpu_percent は None。
        monkeypatch.setattr(sysstat_module, "_PROC_ROOT", fake_proc_root)
        recovered = stat.sample()
        assert recovered.cpu_percent is None
        assert recovered.system_total_bytes == 16_000_000 * 1024


class TestSysstatIsStandardLibraryOnly:
    """design.md の「追加依存なし」（L0 レイヤ）を静的に固定する。"""

    def test_module_does_not_import_third_party_or_sibling_modules(self) -> None:
        import ast
        import inspect

        source = inspect.getsource(sysstat_module)
        tree = ast.parse(source)

        top_level_modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level_modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    top_level_modules.add(node.module.split(".")[0])

        # 標準ライブラリの範囲に収まっていること（sensing_foundation の
        # 姉妹モジュール errors / timebase を import していないこと）を含む。
        disallowed = {"errors", "timebase", "numpy", "sensing_foundation"}
        assert not (top_level_modules & disallowed)
