"""コマンドライン入口（design.md「Components and Interfaces / CLI」、
tasks.md タスク 8.1、要件 12.6, 13.5, 13.6, 13.7）。

`run-throw` / `ingest-truth` / `measure` / `attribute` / `judge-oq27` /
`material-oq05` / `budget` / `bench-overhead` / `report` / `plot` の10サブ
コマンドを提供する、本 Spec の最上位の統合点である（design.md 依存方向表
「CLI | L9 | 全層」——本パッケージのどの層も参照してよい唯一のモジュール）。

============================================================================
入口はロジックを持たない
============================================================================

**判定も算出も本モジュールでは書かない**（`tech.md` 開発標準3）。やることは
3つだけである。

1. 設定を解決する（`M1Settings.resolve()` へ委譲する。**解決順序を書き直さない**）
2. 上流由来の値を調達して各層へ素通しする
3. 各層を並べて呼び、結果を表示・保存する

分布も判定も注記も、すべて呼んだ層が返した値をそのまま写しているだけである。

============================================================================
設定の解決順序（要件 13.5）
============================================================================

**実行時指定 > 環境変数（`STB_M1_*`）> 設定ファイル（`--config`）> 既定値。**
実装は `M1Settings.resolve()` が持っており、本モジュールは3層を集めて渡す
だけである。`--print-settings` は解決結果と**優先順位そのもの**を表示して
本処理を実行せずに終わる。

⚠️ **既定値は暫定の評価候補であって必須性能ではない**（要件 13.7）。
`--help` は `config.PROVISIONAL_NOTICE` を**そのまま**掲げる（注記を
再発明すると、片方だけ直したときに設定の説明と食い違う）。あわせて
`budget.segment3_assumed_ms` が「**設定でありながら実測値ではない据え置き**」
である旨も出す——区間3（予測確定〜移動体が動き出す）は本 Spec の範囲外で
あり、M3 で実測するまで埋めない値である（tasks.md タスク 6.3 の申し送り）。

============================================================================
上流由来の値の調達（要件 13.1）
============================================================================

投擲を実行するには、本 Spec が**生成できない**値が要る。

- 上流の**ログ器**: `UpstreamGateway.get_logger_handle()` からしか取れない
- 上流の**追跡設定**: `Seam.resolve_tracking_settings()`（上流の
  `TrackingSettings.resolve()` への素通し）からしか取れない
- 現在の入力元の**ストリーム識別**: `UpstreamGateway.stream_profile()` で
  取り出し、`Seam.stream_identity()` で整合性検査の2値へ写す

調達を担うのは `run-throw` / `bench-overhead` の入口であるが、
**本モジュールは上流パッケージを直接 import しない。** 接点（`upstream.py`）
と継ぎ目（`seam.py`）を必ず経由する——入口だから例外、としてしまうと
「上流の公開面が変わったときに直す場所が1箇所で済む」という境界の効き目が
そこから崩れる。上流の例外型も名指しできないので、上流の設定解決が落ちた
ときは**位置で判断して**本 Spec の `M1ConfigError` へ翻訳する。

⚠️ **ログ器を CLI が持ち回らないのは意図である。** `run_throw()` は
`logger` ではなく `gateway` を受け取り（tasks.md タスク 3.1 の逸脱3件目）、
その中で `get_logger_handle()` を呼ぶ。入口の責務は「ログ器を取り出せる
唯一の窓口である `UpstreamGateway` を用意して実行層へ渡す」ところまでで
あり、値を経由させると同じ実体が2つの経路を通ることになる。

**調達した値は本 Spec の設定へ写し取らない。** 写し取ると、本 Spec が追跡の
方式を決めたことになる（OQ-26 は上流の担当である）。不透明値のまま実行層へ
渡す。上流の設定は `--runtime-set KEY=VALUE` / `--tracking-set KEY=VALUE` で
指定する——**本 Spec は上流の設定キーを列挙しない**（列挙した時点で、上流が
キーを足したときに本 Spec が追随を強いられる）。

============================================================================
実機を要する作業と要さない作業（要件 12.6）
============================================================================

各サブコマンドの `--help` は**実機の要否**を3値（要 / 不要 / 要（推奨））で
明示する（`HARDWARE_REQUIREMENT`）。実機が要るのは投擲の実行だけであり、
真値の取り込み・実測算出・帰属・判断・レポート・可視化は記録済みの入力から
実機なしで再現できる（要件 12.5）。

============================================================================
テスト専用の注入点（`argv` では表現できない引数）
============================================================================

`main(argv, *, gateway=None, supplier=None, plot_backend_factory=None)`。
`world_frame_calibration.cli` の `source` / `logger`、`sensing_foundation.cli`
の `supplier` と同じ考え方であり、実機・SDK の無い環境で10サブコマンドすべてを
最後まで駆動できるようにするためのものである（要件 12.1）。

- `gateway`: 開き済みの上流窓口。上流の合成入力は
  `StreamProfile.intrinsics` を常に `None` で返す（合成入力にカメラ内部
  パラメータという概念が無い）ため、整合性検査（要件 1.6）を実機なしで
  通す経路が他に無い
- `supplier`: 合成入力のフレーム供給関数（本 Spec は `numpy` を import
  しないので、生成器を自前で持てない）
- `plot_backend_factory`: 描画バックエンドの生成器。**依存が無い環境**の
  縮退（要件 8.9）と字形の欠落を実機なしで通すために要る

============================================================================
終了コード
============================================================================

    0  正常終了
    1  実行時の失敗（継ぎ目の不成立・投擲の不成立・可視化の利用不可）
    2  設定・入力の誤り（**実行開始前に拒否する**。要件 13.6）

**`plot` の利用不可は 1 である。** 要件 8.9 が求めるのは「計測・集計・判断の
経路を停止させずに、可視化のみを利用不可として報告する」ことであり、他の
サブコマンドは影響を受けない。終了コードはその報告の一部である。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from m1_validation import bench, plot, report, seam, upstream
from m1_validation.attribution import attribute
from m1_validation.config import PROVISIONAL_NOTICE, M1Settings
from m1_validation.errors import M1ConfigError, M1ValidationError
from m1_validation.judgement.budget import compute_budget_update
from m1_validation.judgement.oq05 import oq05_material
from m1_validation.judgement.oq27 import judge_oq27
from m1_validation.metrics.accuracy import measure_accuracy
from m1_validation.metrics.aggregate import (
    Distribution,
    ThrowAggregate,
    ThrowMetrics,
    aggregate,
)
from m1_validation.metrics.convergence import analyze_convergence
from m1_validation.metrics.flight import measure_flight
from m1_validation.metrics.latency import LatencyResult, aggregate_latency
from m1_validation.runner import run_throw, successful_throws
from m1_validation.truth import ingest_truth, load_truth_file

__all__ = ["main"]


# ---------------------------------------------------------------------------
# 表示用の定数（すべて文面であり、判定には一切使わない）
# ---------------------------------------------------------------------------

#: 実機の要否の3値（design.md「CLI」のサブコマンド表）。
HARDWARE_REQUIRED: str = "要"
HARDWARE_NOT_REQUIRED: str = "不要"
HARDWARE_RECOMMENDED: str = "要（推奨）"

#: サブコマンドごとの実機の要否（要件 12.6）。**表の3値をそのまま反映する。**
HARDWARE_REQUIREMENT: Mapping[str, str] = {
    "run-throw": HARDWARE_REQUIRED,
    "ingest-truth": HARDWARE_NOT_REQUIRED,
    "measure": HARDWARE_NOT_REQUIRED,
    "attribute": HARDWARE_NOT_REQUIRED,
    "judge-oq27": HARDWARE_NOT_REQUIRED,
    "material-oq05": HARDWARE_NOT_REQUIRED,
    "budget": HARDWARE_NOT_REQUIRED,
    "bench-overhead": HARDWARE_RECOMMENDED,
    "report": HARDWARE_NOT_REQUIRED,
    "plot": HARDWARE_NOT_REQUIRED,
}

#: サブコマンドの役割（design.md「CLI」のサブコマンド表）。
SUBCOMMAND_ROLE: Mapping[str, str] = {
    "run-throw": "1投擲を実行し記録する",
    "ingest-truth": "真値ファイルを取り込み、記録へ対応付ける",
    "measure": "実測7項目と段階別レイテンシを算出する",
    "attribute": "誤差の帰属を判定する",
    "judge-oq27": "Pi 4 継続可否を判定する",
    "material-oq05": "NFR-7 の判断材料を出す",
    "budget": "時間予算表の更新値を算出する",
    "bench-overhead": "計測 ON/OFF 比較",
    "report": "要約と JSON を出す",
    "plot": "図を出す",
}

#: 設定の解決順序を**優先順位の高い順**に並べたもの（要件 13.5）。
#: 実装は `M1Settings.resolve()` が持っており、これは表示用の写しである。
RESOLUTION_ORDER: tuple[str, str, str, str] = (
    "実行時指定",
    "環境変数",
    "設定ファイル",
    "既定値",
)

#: 区間3 の想定値が「設定でありながら実測値ではない」旨（タスク 6.3 の申し送り）。
SEGMENT3_HOLDOVER_NOTE: str = (
    "budget.segment3_assumed_ms は**設定でありながら実測値ではない据え置き**"
    "である。区間3（予測確定〜移動体が動き出す）は本 Spec の範囲外であり、"
    "M3 で実測するまで時間予算表の当該行は欠測のままにする。"
    "設定として与えられるのは NFR-3 の暫定目標を導くためだけであり、"
    "この値を実測値として読んではならない。"
)

#: 上流の設定を本 Spec が決めない旨（design.md「CLI」Implementation Notes）。
UPSTREAM_SETTINGS_NOTE: str = (
    "上流の取得設定（--runtime-set）と追跡設定（--tracking-set）は"
    "**本 Spec の設定へ写し取らない**。写し取ると本 Spec が追跡の方式を"
    "決めたことになる（OQ-26 は上流の担当である）。解決結果は不透明値の"
    "まま実行層へ渡すため、--print-settings にも現れない。"
)

#: 推定軌道の点列とカメラ視線方向が調達できない旨（タスク 7.2 の申し送り）。
UNRESOLVED_PLOT_INPUT_NOTE: str = (
    "推定軌道の点列とカメラ視線方向の水平成分は、上流のどの公開結果型も"
    "持っていない。**でっち上げずに欠測のまま渡している**"
    "（`Prediction.trajectory` から点列を起こすのは放物運動モデルの解き直しで"
    "あり、要件 8.10「可視化にアルゴリズムを持たせない」に反する）。"
    "軌道図の推定軌道と帰属図のカメラ視線は描かれない。"
)

_RESOLUTION_ORDER_NOTE = (
    f"設定の解決順序（優先順位の高い順）: {' > '.join(RESOLUTION_ORDER)}。"
    "--print-settings で解決結果を表示できる（要件 13.5）。"
)

_HARDWARE_LEGEND = (
    "各サブコマンドの見出しにある「実機:」は、その作業に実機"
    "（Raspberry Pi 4 / RealSense D435）が要るかどうかである（要件 12.6）。"
)

_HELP_EPILOGUE = (
    f"{_RESOLUTION_ORDER_NOTE}\n\n"
    f"{PROVISIONAL_NOTICE}\n\n"
    f"{SEGMENT3_HOLDOVER_NOTE}\n\n"
    f"{UPSTREAM_SETTINGS_NOTE}\n\n"
    f"{_HARDWARE_LEGEND}"
)

_TOP_LEVEL_DESCRIPTION = (
    "smart-trashbox-tribute m1-prediction-validation の CLI 入口。\n"
    "投擲実行・真値取り込み・実測算出・帰属・OQ-27 判定・OQ-05 材料・"
    "時間予算更新値・計測比較・レポート・可視化を提供する。\n\n"
    "本入口はロジックを持たず、各層へ委譲するだけである。"
)


def _subcommand_description(command: str) -> str:
    """サブコマンドの説明文（**実機の要否を必ず含める**。要件 12.6）。"""
    return (
        f"実機: {HARDWARE_REQUIREMENT[command]}。{SUBCOMMAND_ROLE[command]}。"
    )


# ---------------------------------------------------------------------------
# 設定キーと引数の対応（**キーは `M1Settings.resolve()` のものをそのまま使う**）
# ---------------------------------------------------------------------------

#: 値を文字列として受け取る設定キー。**型変換は `M1Settings.resolve()` が
#: 行う**——入口で変換すると、設定ファイル・環境変数と別の変換規則が2つ目
#: として生まれる。
_VALUE_FLAGS: tuple[tuple[str, str], ...] = (
    ("layout_file", "投擲レイアウトファイル（要件 13.8。与えないと動かない）。"),
    ("layout_id", "レイアウトファイル内で使うレイアウトの識別子。"),
    ("min_valid_depth_px", "観測点を採用する有効 Depth 画素数の下限（整数）。"),
    ("max_depth_spread_mm", "観測点を採用する奥行きばらつきの上限（mm）。"),
    ("floor_margin_mm", "これより下を床面下として除外する高さ（mm）。"),
    (
        "convergence_band_mm",
        "収束とみなす帯域（mm）。none ならレイアウトの暫定許容窓に揃える。",
    ),
    ("bootstrap_iterations", "帰属の再抽出回数（整数）。"),
    ("bootstrap_seed", "再抽出の乱数種（整数。要件 12.4 の決定性）。"),
    ("direction_agreement_deg", "2つの向きが整合するとみなす角度差の上限（deg）。"),
    ("bias_significance_ratio", "共通偏りを有意とみなす偏り/ばらつきの下限。"),
    ("residual_significance_ratio", "残差を大きいとみなす倍率。"),
    ("range_band_mm", "距離帯の幅（mm）。"),
    ("cpu_saturation_ratio", "CPU 使用率を飽和とみなす割合。"),
    ("fps_shortfall_ratio", "実処理 fps が取得 fps に追いつけていないとみなす割合。"),
    ("confidence_level", "OQ-05 の材料が用いる信頼水準。"),
    ("interval_widths", "OQ-05 の材料が求める信頼区間の全幅（カンマ区切り）。"),
    (
        "segment3_assumed_ms",
        f"時間予算表の区間3 の据え置き想定値（ms）。{SEGMENT3_HOLDOVER_NOTE}",
    ),
    ("overhead_cycles", "計測 ON/OFF 比較の交互実行の巡回数（整数）。"),
    ("overhead_min_samples", "計測 ON/OFF 比較が暫定でなくなる計測値の件数（整数）。"),
    ("min_valid_throws", "有効投擲数の下限（整数）。"),
    ("min_sessions", "セッション数の下限（整数）。"),
    (
        "improvements_applied",
        "development-environment.md §13.2 のうち適用済みの項目（カンマ区切り）。",
    ),
    ("output_root", "出力先の根（既定 var/m1）。"),
)

#: 真偽値として受け取る設定キー（`--x` / `--no-x` の両方を取る）。
_FLAG_FLAGS: tuple[tuple[str, str], ...] = (
    (
        "require_verified_calibration",
        "検証を通していないキャリブレーションでの実行を拒否するか（既定: 拒否する）。",
    ),
    ("require_monotonic_tail", "収束判定で末尾が帯域内に留まることを要求するか。"),
    ("require_live_source", "OQ-27 の判断に実機由来の投擲を必須とするか。"),
)

_SETTINGS_KEYS: frozenset[str] = frozenset(
    [key for key, _ in _VALUE_FLAGS] + [key for key, _ in _FLAG_FLAGS]
)


def _flag_of(key: str) -> str:
    return "--" + key.replace("_", "-")


# ---------------------------------------------------------------------------
# 引数の組み立て
# ---------------------------------------------------------------------------


def _settings_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_argument_group(
        "本 Spec の設定（実行時指定 > 環境変数 STB_M1_* > 設定ファイル > 既定値）"
    )
    group.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help="本 Spec の設定ファイル（JSON）。",
    )
    for key, description in _VALUE_FLAGS:
        group.add_argument(_flag_of(key), dest=key, default=None, help=description)
    for key, description in _FLAG_FLAGS:
        group.add_argument(
            _flag_of(key),
            dest=key,
            action=argparse.BooleanOptionalAction,
            default=None,
            help=description,
        )
    group.add_argument(
        "--print-settings",
        action="store_true",
        default=False,
        help=(
            "解決済みの設定と優先順位を JSON で表示し、"
            "サブコマンドの本処理を実行せずに終了する（要件 13.5）。"
        ),
    )
    group.add_argument(
        "--session-id",
        default=None,
        help="セッション識別子（出力ファイル名とログの対応付けに使う）。",
    )

    upstream_group = parser.add_argument_group(
        "上流の設定（**本 Spec は上流の設定値も方式も決めない**。不透明値として素通しする）"
    )
    upstream_group.add_argument(
        "--runtime-config",
        metavar="PATH",
        default=None,
        help="上流 sensing-foundation の設定ファイル。",
    )
    upstream_group.add_argument(
        "--runtime-set",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help=(
            "上流 sensing-foundation の設定を実行時に上書きする（複数指定可）。"
            "例: --runtime-set source=recorded"
        ),
    )
    return parser


def _throwing_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--calibration", metavar="PATH", default=None, help="キャリブレーション結果ファイル。"
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        default=False,
        help=(
            "未検証のキャリブレーションでの実行を**明示的に許可する**（要件 2.2）。"
            "許可した事実は生成物すべてに印として残り、レポートは"
            "「誤差の帰属ができない」旨を明示する。"
            "**この許可は本引数でしか与えられない**（環境変数・設定ファイルでは立たない）。"
        ),
    )
    parser.add_argument(
        "--tracking-config",
        metavar="PATH",
        default=None,
        help="上流 flying-object-tracking の設定ファイル。",
    )
    parser.add_argument(
        "--tracking-set",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help=(
            "上流 flying-object-tracking の設定を実行時に上書きする（複数指定可）。"
            "環境変数 STB_FOT_* とともに上流の解決器へそのまま渡す。"
        ),
    )
    return parser


def _records_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--records", metavar="PATH", default=None, help="Throw Record（NDJSON）のパス。"
    )
    parser.add_argument(
        "--truth",
        metavar="PATH",
        default=None,
        help="真値ファイル（truth.json）。無ければ落下地点は欠測として扱う。",
    )
    parser.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help="構造化ログ（NDJSON）のパス。段階別レイテンシの出所である。",
    )
    return parser


def _build_parser() -> argparse.ArgumentParser:
    settings = _settings_parser()
    throwing = _throwing_parser()
    records = _records_parser()

    parser = argparse.ArgumentParser(
        prog="m1-validation",
        description=_TOP_LEVEL_DESCRIPTION,
        # サブコマンド一覧は argparse も出すが、そちらは端末幅で折り返される。
        # **実機の要否は折り返しで壊してよい情報ではない**（要件 12.6）ので、
        # 折り返さない epilog にも一覧を置く。
        epilog=_HELP_EPILOGUE
        + "\n\n実機の要否一覧（要件 12.6。折り返さない形で再掲する）:\n"
        + "\n".join(
            f"  {name:<16}{_subcommand_description(name)}"
            for name in HARDWARE_REQUIREMENT
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name: str, parents: list[argparse.ArgumentParser]) -> argparse.ArgumentParser:
        return subparsers.add_parser(
            name,
            parents=parents,
            help=_subcommand_description(name),
            description=_subcommand_description(name),
            epilog=_HELP_EPILOGUE,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

    run_parser = add("run-throw", [settings, throwing])
    run_parser.add_argument("--record-id", default=None, help="投擲の識別子。")
    run_parser.add_argument(
        "--records", metavar="PATH", default=None, help="記録の追記先（NDJSON）。"
    )

    ingest_parser = add("ingest-truth", [settings, records])
    ingest_parser.add_argument(
        "--out", metavar="PATH", default=None, help="真値を対応付けた記録の書き出し先。"
    )

    add("measure", [settings, records])
    add("attribute", [settings, records])
    add("judge-oq27", [settings, records])
    add("material-oq05", [settings, records])
    add("budget", [settings, records])

    bench_parser = add("bench-overhead", [settings, throwing])
    bench_parser.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help=(
            "構造化ログ（NDJSON）のパス。**取りこぼし件数の出所であり必須である**"
            "——供給しないと取りこぼしが常に欠測になり、判定が常に判定不能へ落ちる。"
        ),
    )

    add("report", [settings, records])
    add("plot", [settings, records])
    return parser


# ---------------------------------------------------------------------------
# 設定の解決（**解決順序は `M1Settings.resolve()` が持つ。ここでは集めるだけ**）
# ---------------------------------------------------------------------------


def _key_values(pairs: Sequence[str], *, flag: str) -> dict[str, object]:
    """`KEY=VALUE` の並びをマッピングへ写す。**値は解釈しない。**"""
    overrides: dict[str, object] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise M1ConfigError(
                f"{flag} は KEY=VALUE の形で指定する: {pair!r}",
                {"flag": flag, "value": pair},
            )
        overrides[key.strip()] = value
    return overrides


def _normalise_upstream_overrides(args: argparse.Namespace) -> None:
    """`--runtime-set` / `--tracking-set` を**実行開始前に**マッピングへ正規化する。

    書式の誤り（`=` が無い・キーが空）は設定の誤りであり、要件 13.6 は
    それを実行開始前に拒否することを求める。**窓口を開く直前や投擲の直前に
    解析すると、注入の有無や実行経路によって拒否の時機が変わる**——ここで
    1度だけ済ませておけば、どのサブコマンドでも必ず先頭で落ちる。

    解析結果を同じ属性へ書き戻すのは、**同じ文字列を2度解析しない**ため
    である（2度解析すると、片方だけ検査する実装が成立してしまう）。
    """
    args.runtime_set = _key_values(args.runtime_set, flag="--runtime-set")
    args.tracking_set = _key_values(
        getattr(args, "tracking_set", []), flag="--tracking-set"
    )


def _settings_overrides(args: argparse.Namespace) -> dict[str, object]:
    """実行時指定として与えられた**本 Spec の設定**だけを集める。

    **上流由来の値（追跡設定・ストリーム識別・ログ器）は決して入れない。**
    入れた時点で本 Spec が追跡の方式を決めたことになる。
    """
    return {
        key: getattr(args, key)
        for key in sorted(_SETTINGS_KEYS)
        if getattr(args, key, None) is not None
    }


def _resolve_settings(args: argparse.Namespace) -> M1Settings:
    return M1Settings.resolve(
        file=None if args.config is None else Path(args.config),
        env=os.environ,
        overrides=_settings_overrides(args),
    )


def _settings_payload(settings: M1Settings) -> dict[str, object]:
    """`--print-settings` の出力（要件 13.5, 13.7）。"""
    return {
        "settings": settings.describe(),
        "resolution_order": list(RESOLUTION_ORDER),
        "notes": [SEGMENT3_HOLDOVER_NOTE, UPSTREAM_SETTINGS_NOTE],
    }


# ---------------------------------------------------------------------------
# 上流由来の値の調達（**接点と継ぎ目を必ず経由する**）
# ---------------------------------------------------------------------------


def _translate_upstream_config_failure(what: str, exc: Exception) -> M1ConfigError:
    """上流の設定解決の失敗を本 Spec の語彙へ翻訳する。

    **本モジュールは上流の例外型を名指しできない**（import してはならない）
    ので、失敗した位置で判断する。`M1ValidationError` は本 Spec 自身の
    失敗なので、呼び出し側が先に通す。
    """
    return M1ConfigError(f"{what}を解決できない: {exc}", {"what": what})


def _open_gateway(
    args: argparse.Namespace, injected: object | None
) -> tuple[object, bool]:
    """上流基盤への窓口を用意する。戻り値の2つ目は「閉じる責任があるか」。"""
    if injected is not None:
        return injected, False
    try:
        source_spec = upstream.resolve_runtime_settings(
            file=None if args.runtime_config is None else Path(args.runtime_config),
            env=os.environ,
            overrides=dict(args.runtime_set),
        )
    except M1ValidationError:
        raise
    # 上流の例外型を名指しできない（import してはならない）ので、位置で判断する。
    except Exception as exc:
        raise _translate_upstream_config_failure("上流の実行時設定", exc) from exc
    session_id = _session_id(args)
    return upstream.UpstreamGateway.open(
        session_id=session_id, source_spec=source_spec
    ), True


def _tracking_settings(args: argparse.Namespace) -> object:
    """上流の追跡設定を継ぎ目の素通し入口から得る。

    **`file` / `env` / `overrides` を3つとも供給する。** どれかを空に埋めると
    上流の設定ファイル・環境変数・上書きが黙って捨てられ、本 CLI が掲げる
    優先順位と食い違う（design.md「CLI」Implementation Notes）。
    """
    try:
        return seam.resolve_tracking_settings(
            config_path=(
                None if args.tracking_config is None else Path(args.tracking_config)
            ),
            env=os.environ,
            overrides=dict(args.tracking_set),
        )
    except M1ValidationError:
        raise
    # 上流の例外型を名指しできない（import してはならない）ので、位置で判断する。
    except Exception as exc:
        raise _translate_upstream_config_failure("上流の追跡設定", exc) from exc


def _stream_identity(gateway: object, supplier: object) -> tuple[object, object]:
    """現在の入力元のストリーム識別を接点から取り出し、継ぎ目で2値へ写す。

    どちらも**不透明値**であり、本モジュールは中身を一切見ない。
    """
    profile = gateway.stream_profile(supplier=supplier)  # type: ignore[attr-defined]
    return seam.stream_identity(profile)


def _session_id(args: argparse.Namespace) -> str:
    if args.session_id:
        return str(args.session_id)
    return f"m1-{int(time.time() * 1000)}"


# ---------------------------------------------------------------------------
# 表示（写すだけ。算出しない）
# ---------------------------------------------------------------------------


def _json_default(obj: object) -> object:
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"JSON化できない値: {obj!r}")


def _dump_json(payload: object) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False, indent=2)


def _distribution_payload(distribution: Distribution) -> dict[str, object]:
    return {
        "count": distribution.count,
        "median": distribution.median,
        "p95": distribution.p95,
        "iqr": distribution.iqr,
        "minimum": distribution.minimum,
        "maximum": distribution.maximum,
        "missing": distribution.missing,
    }


def _aggregate_payload(group: ThrowAggregate) -> dict[str, object]:
    """投擲群の集計を写す（**欠測は `None` のまま。0 で埋めない**）。"""
    return {
        "calibration_id": group.calibration_id,
        "verified": group.verified,
        "session_ids": list(group.session_ids),
        "throw_count": group.throw_count,
        "failed_throw_count": group.failed_throw_count,
        "valid_throw_count": group.valid_throw_count,
        "live_throw_count": group.live_throw_count,
        "converged_count": group.converged_count,
        "not_converged_count": group.not_converged_count,
        "not_measurable_count": group.not_measurable_count,
        "single_prediction_throw_count": group.single_prediction_throw_count,
        "provisional": group.provisional,
        "provisional_reasons": list(group.provisional_reasons),
        "items": {
            key: _distribution_payload(value) for key, value in group.items.items()
        },
    }


def _latency_payload(latency: LatencyResult) -> dict[str, object]:
    """段階別レイテンシを写す（要件 7.1, 7.2, 7.3）。"""
    return {
        "definition": latency.definition,
        "first_prediction_basis": latency.first_prediction_basis,
        "stage_note": latency.stage_note,
        "stages": [
            {
                "stage": stage.stage,
                "event": stage.event,
                "field": stage.field,
                "source": stage.source,
                "count": stage.count,
                "p50_ms": stage.p50_ms,
                "p95_ms": stage.p95_ms,
                "mean_ms": stage.mean_ms,
                "min_ms": stage.min_ms,
                "max_ms": stage.max_ms,
            }
            for stage in (*latency.stages, latency.end_to_end)
        ],
        "detect_to_first_prediction": [
            {
                "record_id": item.record_id,
                "detection_start_ms": item.detection_start_ms,
                "first_prediction_at_ms": item.first_prediction_at_ms,
                "first_prediction_sample_count": item.first_prediction_sample_count,
                "detect_to_first_prediction_ms": item.detect_to_first_prediction_ms,
            }
            for item in latency.detect_to_first_prediction
        ],
        "capture_fps": latency.capture_fps,
        "process_fps": latency.process_fps,
        "cpu_percent_mean": latency.cpu_percent_mean,
        "rss_bytes_max": latency.rss_bytes_max,
        "frames_dropped": latency.frames_dropped,
        "frames_missing": latency.frames_missing,
        "unknown_stages": list(latency.unknown_stages),
        "foreign_prediction_events": latency.foreign_prediction_events,
        "unusable_prediction_events": latency.unusable_prediction_events,
        "log_lines_dropped": latency.log_lines_dropped,
        "log_lines_skipped": latency.log_lines_skipped,
    }


# ---------------------------------------------------------------------------
# 評価側の共通の並び（**各層を順に呼ぶだけ**）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Evaluation:
    """評価側サブコマンドが共有する中間結果。"""

    records: tuple[object, ...]
    truths: tuple[object, ...]
    unknown_record_ids: tuple[str, ...]
    metrics: tuple[ThrowMetrics, ...]
    latency: LatencyResult | None
    groups: tuple[ThrowAggregate, ...]


def _require(value: object | None, flag: str, why: str) -> str:
    if value is None:
        raise M1ConfigError(f"{flag} が指定されていない: {why}", {"flag": flag})
    return str(value)


def _load_truth_entries(
    args: argparse.Namespace, settings: M1Settings
) -> Mapping[str, Mapping[str, object]]:
    if args.truth is None:
        return {}
    return load_truth_file(
        Path(args.truth), expected_layout_id=settings.layout.layout_id
    )


def _evaluate(
    args: argparse.Namespace,
    settings: M1Settings,
    gateway: object,
    *,
    require_log: bool,
) -> _Evaluation:
    """記録 → 真値 → 実測 → 集計、という並びを実行する（算出はすべて各層）。

    **引数の検査は1つ残らず先頭で済ませる**（要件 13.6）。投擲実験は1回ごとに
    人が物を投げる作業であり、記録の読み出しと実測算出をすべて終えてから
    「`--log` が無い」と言われるのでは「実行開始前に拒否する」ことにならない。
    `--records` だけが先頭で、`--log` が実作業のあとという**兄弟間の不揃い**を
    作らない。
    """
    records_path = _require(
        args.records, "--records", "評価は記録済みの Throw Record から行う"
    )
    # ⚠️ **`--log` の検査をここから下げないこと。** 下げると、判断側が
    # `LatencyResult` を非 optional で要求する保険に助けられて最終的には
    # 落ちるものの、**落ちるのが全投擲を測り終えたあと**になる。
    log_path = (
        _require(args.log, "--log", "段階別レイテンシと実測項目3 の出所である")
        if require_log
        else args.log
    )

    # 真値ファイルの取り込みも**記録を1行も読む前に**済ませる。
    # `load_truth_file()` は全件を取り込みの時点で検査する（要件 13.6）と
    # 定めており、その検査を記録の読み出しのあとに置くと、**30件目の綴り
    # 間違いに気付くのが全記録を読んだあと**になる。
    entries = _load_truth_entries(args, settings)

    raw = tuple(gateway.load_records(Path(records_path)))  # type: ignore[attr-defined]
    # **失敗投擲もここでは落とさない**（要件 3.8）。除外は「捨てる」ことでは
    # なく、除いた数を残すことである——`aggregate()` が自分で除いたうえで
    # `failed_throw_count` に数を残す。入口で先に間引くと、その数が常に 0 に
    # なり、「何回失敗したのか」が誰にも読めなくなる。
    ingest = ingest_truth(raw, entries, layout=settings.layout)

    results: list[ThrowMetrics] = []
    for record, truth in zip(ingest.records, ingest.truths, strict=True):
        accuracy = measure_accuracy(record, truth)
        results.append(
            ThrowMetrics(
                record=record,
                truth=truth,
                flight=measure_flight(record, truth, layout=settings.layout),
                accuracy=accuracy,
                convergence=analyze_convergence(record, accuracy, settings=settings),
            )
        )

    latency: LatencyResult | None = None
    if log_path is not None:
        latency = aggregate_latency(
            Path(log_path),
            # **こちらは呼び出し側が除く**（`aggregate_latency()` の docstring
            # が明示している）。失敗投擲の初回予測は成立していないので、
            # 混ぜると実測項目3 が欠測ばかりの行で薄まる。
            successful_throws(ingest.records),
            summarize=gateway.summarize_stages,  # type: ignore[attr-defined]
        )

    return _Evaluation(
        records=ingest.records,
        truths=ingest.truths,
        unknown_record_ids=ingest.unknown_record_ids,
        metrics=tuple(results),
        latency=latency,
        groups=aggregate(tuple(results), settings=settings, latency=latency),
    )


def _group_stem(session_id: str, group: ThrowAggregate) -> str:
    """群ごとの出力ファイル名の幹。

    **1レポート = 1キャリブレーション群**（識別子 × 検証状態）であり、
    幹に両方を入れないと、同じ識別子の検証済み群と未検証群が同じファイルへ
    書き出されて片方が消える。
    """
    state = "verified" if group.verified else "unverified"
    return f"{session_id}-{group.calibration_id}-{state}"


def _build_group_report(
    evaluation: _Evaluation,
    group: ThrowAggregate,
    *,
    settings: M1Settings,
    session_id: str,
) -> report.M1Report:
    """1群ぶんのレポートを組み立てる（帰属・判断はすべて各層が出す）。"""
    latency = evaluation.latency
    if latency is None:  # pragma: no cover - `require_log` で先に弾く
        raise M1ConfigError("--log が要る: 判断には段階別レイテンシが要る")
    return report.build_report(
        session_id=session_id,
        aggregate=group,
        attribution=attribute(group, evaluation.records, settings=settings),
        oq27=judge_oq27(latency, group, settings=settings),
        oq05=oq05_material(group, settings=settings),
        budget=compute_budget_update(group, latency, settings=settings),
    )


def _group_reports(
    evaluation: _Evaluation, *, settings: M1Settings, session_id: str
) -> list[tuple[ThrowAggregate, report.M1Report]]:
    return [
        (
            group,
            _build_group_report(
                evaluation, group, settings=settings, session_id=session_id
            ),
        )
        for group in evaluation.groups
    ]


# ---------------------------------------------------------------------------
# サブコマンド
# ---------------------------------------------------------------------------


def _run_throw_command(
    args: argparse.Namespace,
    settings: M1Settings,
    gateway: object,
    supplier: object,
) -> tuple[int, dict[str, object]]:
    calibration = _require(
        args.calibration, "--calibration", "投擲の前に検証ゲートを通す（要件 2.1）"
    )
    record_id = _require(args.record_id, "--record-id", "1投擲を1レコードとして記録する")
    records_path = _require(args.records, "--records", "記録の追記先が要る")
    # **上流の設定解決を入力元より先に置く**（要件 13.6）。`--tracking-set` の
    # 書式の誤りは設定の誤りであり、**入力元を開いたあとで気付くのでは
    # 「実行開始前に拒否する」ことにならない**（実機なら装置を掴んでから
    # 落ちることになる）。解決そのものは副作用を持たない。
    tracking_settings = _tracking_settings(args)

    signature, intrinsics = _stream_identity(gateway, supplier)
    result = run_throw(
        settings=settings,
        gateway=gateway,  # type: ignore[arg-type]
        calibration_path=Path(calibration),
        record_id=record_id,
        tracking_settings=tracking_settings,
        signature=signature,
        intrinsics=intrinsics,
        supplier=supplier,
        allow_unverified=args.allow_unverified,
    )
    gateway.store_record(result.record, Path(records_path))  # type: ignore[attr-defined]

    payload: dict[str, object] = {
        "record_id": result.record_id,
        "records_path": str(records_path),
        "samples_appended": result.samples_appended,
        "rejected": [
            {"reason": str(reason), "count": count} for reason, count in result.rejected
        ],
        "first_valid_sample_count": result.first_valid_sample_count,
        "failed_reason": result.failed_reason,
        "allow_unverified": args.allow_unverified,
    }
    return (0 if result.failed_reason is None else 1), payload


def _ingest_truth_command(
    args: argparse.Namespace, settings: M1Settings, gateway: object
) -> tuple[int, dict[str, object]]:
    records_path = _require(args.records, "--records", "真値は記録へ対応付ける")
    truth_path = _require(args.truth, "--truth", "取り込む真値ファイルが要る")
    out_path = Path(
        _require(args.out, "--out", "真値を対応付けた記録の書き出し先が要る")
    )

    records = tuple(gateway.load_records(Path(records_path)))  # type: ignore[attr-defined]
    entries = load_truth_file(
        Path(truth_path), expected_layout_id=settings.layout.layout_id
    )
    ingest = ingest_truth(records, entries, layout=settings.layout)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    for record in ingest.records:
        gateway.store_record(record, out_path)  # type: ignore[attr-defined]

    return 0, {
        "out": str(out_path),
        "record_count": len(ingest.records),
        # **黙って捨てない**（要件 4.7）。記録に無い識別子は警告として残す。
        "unknown_record_ids": list(ingest.unknown_record_ids),
    }


def _measure_command(evaluation: _Evaluation, session_id: str) -> dict[str, object]:
    latency = evaluation.latency
    if latency is None:  # pragma: no cover - `require_log=True` で先に弾く
        raise M1ConfigError("--log が要る: 段階別レイテンシの出所である")
    return {
        "session_id": session_id,
        "latency": _latency_payload(latency),
        "groups": [_aggregate_payload(group) for group in evaluation.groups],
    }


def _judgement_command(
    evaluation: _Evaluation,
    *,
    settings: M1Settings,
    session_id: str,
    section: str,
) -> dict[str, object]:
    """レポートの1節だけを出す（`attribute` / `judge-oq27` / `material-oq05` / `budget`）。"""
    groups: list[dict[str, object]] = []
    for group, built in _group_reports(
        evaluation, settings=settings, session_id=session_id
    ):
        payload = report.report_to_dict(built)
        groups.append(
            {
                "calibration_id": group.calibration_id,
                "verified": group.verified,
                "provisional": group.provisional,
                section: payload[section],
            }
        )
    return {"session_id": session_id, "groups": groups}


def _report_command(
    evaluation: _Evaluation, *, settings: M1Settings, session_id: str
) -> dict[str, object]:
    groups: list[dict[str, object]] = []
    written: list[str] = []
    for group, built in _group_reports(
        evaluation, settings=settings, session_id=session_id
    ):
        path = report.write_report(
            built, settings.output_root, _group_stem(session_id, group)
        )
        written.append(str(path))
        groups.append(
            {
                "calibration_id": group.calibration_id,
                "verified": group.verified,
                "summary": report.render_summary(built),
                "report": report.report_to_dict(built),
            }
        )
    return {"session_id": session_id, "written": written, "groups": groups}


def _plot_command(
    evaluation: _Evaluation,
    *,
    settings: M1Settings,
    session_id: str,
    backend_factory: Callable[[], object] | None,
) -> tuple[int, dict[str, object], list[str]]:
    """図を出す。**依存が無ければ利用不可として報告し、他の経路は止めない**（要件 8.9）。"""
    availability = plot.visualization_availability(
        backend_factory=backend_factory  # type: ignore[arg-type]
    )
    warnings: list[str] = []
    figures: list[dict[str, object]] = []
    by_id = {metrics.record.record_id: metrics for metrics in evaluation.metrics}

    for group in evaluation.groups:
        attribution = attribute(group, evaluation.records, settings=settings)
        oq05 = oq05_material(group, settings=settings)
        for row in group.per_throw:
            metrics = by_id[row.record_id]
            result = plot.render_figures(
                output_root=settings.output_root,
                record=metrics.record,
                truth=metrics.truth,
                accuracy=metrics.accuracy,
                convergence=metrics.convergence,
                layout=settings.layout,
                oq05=oq05,
                aggregate=group,
                attribution=attribution,
                # **調達できないものはでっち上げず欠測のまま渡す**
                # （`UNRESOLVED_PLOT_INPUT_NOTE` 参照）。
                trajectory_points_world_mm=(),
                camera_ray_horizontal=None,
                backend_factory=backend_factory,  # type: ignore[arg-type]
            )
            if result.font_warning is not None:
                warnings.append(f"{row.record_id}: {result.font_warning}")
            figures.append(
                {
                    "record_id": row.record_id,
                    "calibration_id": group.calibration_id,
                    "available": result.available,
                    "reason": result.reason,
                    "kinds": list(result.kinds),
                    "paths": [str(path) for path in result.paths],
                    "missing_glyph_count": result.missing_glyph_count,
                    "font_warning": result.font_warning,
                }
            )

    payload: dict[str, object] = {
        "session_id": session_id,
        "available": availability.available,
        "reason": availability.reason,
        "library": availability.library,
        "backend": availability.backend,
        # 調達できなかった2値は**欠測のまま**であることを出力に残す。
        "trajectory_points_world_mm": None,
        "camera_ray_horizontal": None,
        "notes": [UNRESOLVED_PLOT_INPUT_NOTE],
        "figures": figures,
    }
    return (0 if availability.available else 1), payload, warnings


def _bench_overhead_command(
    args: argparse.Namespace,
    settings: M1Settings,
    gateway: object,
    supplier: object,
    session_id: str,
) -> dict[str, object]:
    calibration = _require(
        args.calibration, "--calibration", "投擲の前に検証ゲートを通す（要件 2.1）"
    )
    log_path = Path(
        _require(
            args.log,
            "--log",
            "条件ごとの取りこぼし件数の出所である（供給しないと判定が常に判定不能へ落ちる）",
        )
    )
    # `run-throw` と同じ理由で、上流の設定解決を入力元より先に置く（要件 13.6）。
    tracking_settings = _tracking_settings(args)
    signature, intrinsics = _stream_identity(gateway, supplier)

    def dropped_probe() -> int | None:
        """取りこぼし件数を**本 Spec のログ集計から**読む（タスク 6.4 の申し送り）。

        `UpstreamGateway` は `CaptureMetrics` を公開していないが、同じ値は
        `aggregate_latency(...).frames_dropped` としてログから読める。
        まだ書かれていなければ**欠測**であり、0 では埋めない。
        """
        if not log_path.exists():
            return None
        return aggregate_latency(
            log_path,
            (),
            summarize=gateway.summarize_stages,  # type: ignore[attr-defined]
        ).frames_dropped

    segments = bench.ThrowSegmentRunner(
        settings=settings,
        gateway=gateway,
        calibration_path=Path(calibration),
        tracking_settings=tracking_settings,
        signature=signature,
        intrinsics=intrinsics,
        supplier=supplier,
        record_id_prefix=f"overhead-{session_id}",
        dropped_probe=dropped_probe,
        allow_unverified=args.allow_unverified,
    )
    result = bench.run_overhead_bench(segments=segments, settings=settings)
    path = bench.write_overhead_report(result, settings.output_root, session_id)
    payload = bench.report_to_dict(result)
    payload["written"] = str(path)
    return payload


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

#: レポートの1節を出すサブコマンドと、その節の対応。
_SECTION_COMMANDS: Mapping[str, str] = {
    "attribute": "attribution",
    "judge-oq27": "oq27",
    "material-oq05": "oq05",
    "budget": "budget",
}

#: 段階別レイテンシ（＝`--log`）を必要とするサブコマンド。
_LOG_REQUIRED: frozenset[str] = frozenset(
    {"measure", "attribute", "judge-oq27", "material-oq05", "budget", "report"}
)


def _dispatch(
    args: argparse.Namespace,
    settings: M1Settings,
    gateway: object,
    *,
    supplier: object,
    plot_backend_factory: Callable[[], object] | None,
) -> tuple[int, dict[str, object], list[str]]:
    session_id = _session_id(args)
    command = args.command

    if command == "run-throw":
        code, payload = _run_throw_command(args, settings, gateway, supplier)
        return code, payload, []
    if command == "ingest-truth":
        code, payload = _ingest_truth_command(args, settings, gateway)
        return code, payload, []
    if command == "bench-overhead":
        return (
            0,
            _bench_overhead_command(args, settings, gateway, supplier, session_id),
            [],
        )

    evaluation = _evaluate(
        args, settings, gateway, require_log=command in _LOG_REQUIRED
    )
    if command == "measure":
        return 0, _measure_command(evaluation, session_id), []
    if command in _SECTION_COMMANDS:
        return (
            0,
            _judgement_command(
                evaluation,
                settings=settings,
                session_id=session_id,
                section=_SECTION_COMMANDS[command],
            ),
            [],
        )
    if command == "report":
        return (
            0,
            _report_command(evaluation, settings=settings, session_id=session_id),
            [],
        )
    if command == "plot":
        return _plot_command(
            evaluation,
            settings=settings,
            session_id=session_id,
            backend_factory=plot_backend_factory,
        )
    raise AssertionError(f"未知のサブコマンド: {command}")  # argparse の choices で到達しない


def main(
    argv: Sequence[str] | None = None,
    *,
    gateway: object | None = None,
    supplier: object | None = None,
    plot_backend_factory: Callable[[], object] | None = None,
) -> int:
    """CLI エントリポイント（`python -m m1_validation.cli` からも呼ばれる）。

    `gateway` / `supplier` / `plot_backend_factory` は **`argv` では表現でき
    ない、テスト専用のキーワード引数**である（モジュール docstring 参照）。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        # **引数の書式の誤りは、窓口を開く前・投擲を始める前に拒否する**
        # （要件 13.6）。注入の有無で拒否の時機が変わらないよう、
        # 設定の解決と同じ位置で1度だけ済ませる。
        _normalise_upstream_overrides(args)
        settings = _resolve_settings(args)
    except M1ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2

    if args.print_settings:
        print(_dump_json(_settings_payload(settings)))
        return 0

    window: object | None = None
    opened = False
    try:
        window, opened = _open_gateway(args, gateway)
        code, payload, warnings = _dispatch(
            args,
            settings,
            window,
            supplier=supplier,
            plot_backend_factory=plot_backend_factory,
        )
    except M1ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except M1ValidationError as exc:
        print(f"実行エラー: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 上流の例外型を名指しできない
        print(f"上流エラー: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if opened and window is not None:
            window.close()  # type: ignore[attr-defined]

    for warning in warnings:
        # **握り潰さない。** 読めない字形は「図に明示した」ことにならない。
        print(f"警告: {warning}", file=sys.stderr)
    print(_dump_json(payload))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
