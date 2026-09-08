"""サブコマンド入口（design.md `#### Cli` / 要件 1.11, 2.3, 4.4, 9.8, 10.6-10.8）。

`python -m chassis_mechanism <subcommand>` の実体である。⚠️ **1回きりのバッチで
あり常駐しない**——サーバもソケットも監視ループも持たない。

| サブコマンド | 動作 | 形状ライブラリ |
|---|---|---|
| `build` | 形状を生成し STEP / STL / 3MF を書き出す（`--update-baseline` で記録を更新） | 必要 |
| `check` | 寸法・隙間・接合・形状指標の総合検査（`--digest-only` は再生成しない） | 一部のみ |
| `layout` | 幾何の導出と記録の書き出し（`--check CONFIG` でシミュレータ設定と照合） | 不要 |
| `joints` | 接合部と締結部品一覧の導出と書き出し | 不要 |

## 終了コード（⚠️ 本モジュールが本 Spec の唯一の決定箇所である）

design.md「Error Categories and Responses」:

- `0` 正常
- `1` 検査の不一致・違反（本 Spec の `GeometryError` / `ClearanceError` /
  `MeasurementError` / `ConsistencyError`、および形状指標の照合が
  `MetricsMismatch` を返したこと）
- `2` 使い方の誤り・入力不正（本 Spec の `ParameterError`、`argparse` の使用法
  エラー、`OSError`、`UnicodeDecodeError`、および⚠️ **上流の失敗の大半**）
- `3` 形状生成の環境が無い（`CadUnavailableError` と、形状層から届く裸の
  `ImportError`）

⚠️ **終了コード表は上流の例外階層も含む**（design.md `#### Cli`）。上流の失敗を
包み直さない方針（`errors.py`:「どちらの設定が壊れているかをメッセージから
消さない」）を採る以上、上流の型のまま終了コードへ写せなければ、上流の失敗は
表に無い例外として既定値へ黙って落ちる。

⚠️ **`GeometryError` は綴りが同じで終了コードが違う。** 本 Spec の
`GeometryError` は「形状・分割・干渉」であり検査の不成立（`1`）だが、上流の
`GeometryError` は本 Spec から見れば**上流の設定または呼び出しの誤り**であり
入力不正（`2`）である（上流 `cli.py` 自身も 2 に割り当てている）。
tasks.md「Implementation Notes」タスク 1.2 が名指しで警告するとおり、
⚠️ **上流例外を先に捕捉する順序で書くと取り違える**。本モジュールは順序に
依らない写像（`exit_code_for` は**最も派生した**一致族を選ぶ）でこれを塞ぐ
——表には両階層の**基底**も載っているため、素朴な「最初の一致」走査では
上流 `CadUnavailableError`（3）が基底の 2 に化ける。

## 遅延 import

⚠️ **`shapes` / `export` は関数の内側で import する**（design.md
「Dependency Direction」）。モジュール直下に書くと `import chassis_mechanism.cli`
が形状層へ到達しうる形になり、`test_chassis_boundaries.py` の
`find_module_level_cad_imports` が落ちる。⚠️ 形状ライブラリ自身（`build123d` /
`OCP`）は**関数内であっても** import してはならない（`find_cad_import_violations`
は遅延 import も対象にする）。したがって本モジュールは形状ライブラリの有無を
自分で調べず、形状層が送出する `CadUnavailableError`（および万一の裸の
`ImportError`）を観測して判断する。

⚠️ **形状ライブラリの不在を成功として黙って読み飛ばさない**（要件 1.11）。
生成したつもりで生成物が無い状態は、造形の直前まで気付けない事故になる。
⚠️ 2（入力の誤り）へ落としてもいけない——入力は正しく、導入すれば直る。

## `params` → `layout` の対

⚠️ **幾何を組み立てるのは `_params_and_layout` の1箇所だけである。**
`shapes.check_before_build(params, layout)` は渡された `layout` をそのまま信じ、
`params` から引き直さない（tasks.md 3.6 の申し送り）。2箇所目の組み立てを作れば、
⚠️ **古い `layout` を新しい `params` に対して検査する**経路——隙間の高さだけが
古い寸法のまま「足りている」と報告される経路——が生まれる。
`test_chassis_cli.py` が `ast` と同一性の両方で1箇所であることを固定する。

## 実効転がり半径の配線（tasks.md「実効転がり半径の配線」）

⚠️ **観測を幾何へ差し込むのは本モジュールの責務である。** `layout` は
`assembly` を import できない（依存方向が逆になる）ため、`measurements.json` の
代表値を読んで `ObservedRollingRadius`（値＋出所）を組み立て、`derive_layout` へ
渡すのはコマンド入口しかできない。⚠️ **値だけでは足りない**——出所を伴わないと
`weakest_provenance` の畳み込みが「使わなかった公称値」の出所を継承し続け、
実測を使ったのに仮値を名乗る（またはその逆の）幾何が下流へ流れる。
⚠️ **`ResolvedParams` へ観測を混ぜない**（パラメータ識別子が観測のたびに動き、
観測のたびに形状の再生成が要求される）。

## 観測記録の充足検査は既定に混ぜない

⚠️ **`--observations` は独立した指定である**（tasks.md タスク 4.1）。観測は
組立（6群）まで必ず未充足であり、既定の総合検査へ混ぜると⚠️ **組立前に寸法・
隙間・接合の検査を1度も回せなくなる**。代表値の**読み取り**（実効転がり半径の
配線）と観測の**充足検査**は別物であり、前者は常に行い後者は指定されたときだけ
行う。

## 出力先

`--output-dir` の既定は `None` であり、解決は `export.DEFAULT_OUTPUT_DIR` に
委ねる。⚠️ `cli` が既定値を書き写すにはモジュール直下で CAD 層を import せねば
ならず、上記の遅延 import の規律に反する。
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

from catch_mechanism import (
    ABSENT,
    PRESENCE_FIELD,
    PRESENT,
    GeometryBaseline,
    MetricsMismatch,
    PartMetrics,
    compare_metrics,
    load_baseline,
)
from catch_mechanism import (
    CadUnavailableError as UpstreamCadUnavailableError,
)
from catch_mechanism import (
    CatchMechanismError as UpstreamCatchMechanismError,
)
from catch_mechanism import (
    ConsistencyError as UpstreamConsistencyError,
)
from catch_mechanism import (
    GeometryError as UpstreamGeometryError,
)
from catch_mechanism import (
    ParameterError as UpstreamParameterError,
)
from catch_mechanism import (
    SelectionError as UpstreamSelectionError,
)

from chassis_mechanism.assembly import (
    DEFAULT_MEASUREMENTS_PATH,
    REPRESENTATIVE_DIAMETER_PATH,
    load_assembly_record,
    missing_observations,
    representative_rolling_radius_mm,
)
from chassis_mechanism.baseline import DEFAULT_BASELINE_PATH, dump_baseline, verify_digest
from chassis_mechanism.config import (
    DEFAULT_DIMENSIONS_PATH,
    SCHEMA_VERSION,
    ResolvedParams,
    load_params,
    parameters_digest,
)
from chassis_mechanism.errors import (
    CadUnavailableError,
    ChassisMechanismError,
    ClearanceError,
    ConsistencyError,
    GeometryError,
    MeasurementError,
    ParameterError,
)
from chassis_mechanism.joints import (
    DEFAULT_JOINT_SCHEDULE_PATH,
    derive_fastener_schedule,
    dump_fastener_schedule,
)
from chassis_mechanism.layout import (
    DEFAULT_LAYOUT_PATH,
    ChassisLayout,
    ObservedRollingRadius,
    derive_layout,
    dump_layout,
    load_compression_mm,
)

__all__ = [
    "EXIT_OK",
    "EXIT_MISMATCH",
    "EXIT_USAGE",
    "EXIT_CAD_UNAVAILABLE",
    "EXIT_CODE_BY_ERROR",
    "PARAMS_AND_LAYOUT_FUNCTION",
    "SIMULATOR_WHEEL_DIAMETER_KEY",
    "VANISHED_LABEL",
    "APPEARED_LABEL",
    "DEVIATION_LABEL",
    "build_parser",
    "exit_code_for",
    "main",
]

PROGRAM: Final[str] = "chassis_mechanism"
"""メッセージの接頭辞。⚠️ 標準エラーの1行目からどのコマンドの失敗かが読める。"""


# ---------------------------------------------------------------------------
# 終了コード（design.md `#### Cli`）
# ---------------------------------------------------------------------------

EXIT_OK: Final[int] = 0
"""正常終了。"""

EXIT_MISMATCH: Final[int] = 1
"""検査の不一致・違反（記録と現在が食い違う、隙間が足りない、観測が足りない）。"""

EXIT_USAGE: Final[int] = 2
"""使い方の誤り・入力不正。⚠️ `argparse` の使用法エラーもこの値である。"""

EXIT_CAD_UNAVAILABLE: Final[int] = 3
"""形状生成の環境が無い。⚠️ **成功にしない**（要件 1.11）。"""

EXIT_CODE_BY_ERROR: Final[Mapping[type, int]] = {
    # 本 Spec の6系統（`errors.py`。互いに素である）
    ParameterError: EXIT_USAGE,
    GeometryError: EXIT_MISMATCH,
    ClearanceError: EXIT_MISMATCH,
    MeasurementError: EXIT_MISMATCH,
    ConsistencyError: EXIT_MISMATCH,
    CadUnavailableError: EXIT_CAD_UNAVAILABLE,
    ChassisMechanismError: EXIT_USAGE,
    # 上流の5系統と基底（⚠️ **包み直さないため、上流の型のまま写す**）
    UpstreamParameterError: EXIT_USAGE,
    UpstreamSelectionError: EXIT_USAGE,
    UpstreamGeometryError: EXIT_USAGE,
    UpstreamConsistencyError: EXIT_MISMATCH,
    UpstreamCadUnavailableError: EXIT_CAD_UNAVAILABLE,
    UpstreamCatchMechanismError: EXIT_USAGE,
    # ⚠️ **設定ファイルが UTF-8 でない場合の失敗はここにしか無い。**
    # `Path.read_text(encoding="utf-8")` が投げる `UnicodeDecodeError` は
    # `ValueError` の一種であり `OSError` ではない——読み手が `except OSError` しか
    # 持たなければ表を素通りする。本 Spec の設定ファイルは日本語を含み、Windows の
    # 編集器が CP932 で保存する経路は現実にある。⚠️ **1（照合の不一致）にしない**
    # ——読めないことと値が食い違うことは、直し方も CI の扱いも違う。
    UnicodeDecodeError: EXIT_USAGE,
}
"""例外の系統から終了コードへの表（本モジュール docstring「終了コード」）。

⚠️ **2つの階層は互いに素である。** 本 Spec の `ChassisMechanismError` は
`ValueError` を直接継承しており、上流の `CatchMechanismError` とは無関係な型で
ある（`errors.py`）。⚠️ **だが表には両階層の基底も載っている**ため、
「最初に一致した族」を返す走査は**表の並び順で答えが変わる**——基底が先に来れば
上流 `CadUnavailableError`（3）が 2 に化ける。`exit_code_for` は最も派生した
一致族を選ぶことでこれを塞ぎ、`test_chassis_cli.py` の
`test_exit_code_lookup_does_not_depend_on_the_table_order` が固定する。

⚠️ **`GeometryError` の2行は同じ綴りで違う値である**（本 Spec 1 / 上流 2）。
別名で import してあるのは、片方だけを書いて満足する事故を避けるためである。
"""


# ---------------------------------------------------------------------------
# 出力の印
# ---------------------------------------------------------------------------

VANISHED_LABEL: Final[str] = "[部品が消えた]"
"""記録にあって再生成に無い部品の印（`PRESENCE_FIELD` の不一致）。"""

APPEARED_LABEL: Final[str] = "[部品が増えた]"
"""再生成にあって記録に無い部品の印（`PRESENCE_FIELD` の不一致）。"""

DEVIATION_LABEL: Final[str] = "[乖離]"
"""体積・境界箱・立体数の不一致の印。"""

PARAMS_AND_LAYOUT_FUNCTION: Final[str] = "_params_and_layout"
"""寸法と幾何を組み立てる唯一の関数の名（本モジュール docstring「`params` → `layout` の対」）。

⚠️ **名前を値として公開するのは、検査が本モジュールの外にあるからである。**
`test_chassis_cli.py` はこの名を使って「`derive_layout` の呼び出しがこの関数
1箇所に閉じている」ことを `ast` で確かめる——手書きの名前を試験側に置くと、
関数を改名しただけで検査が空振りする。
"""

SIMULATOR_WHEEL_DIAMETER_KEY: Final[str] = "wheel_diameter_mm"
"""シミュレータの駆動系設定における実効ホイール径のキー（要件 10.6-10.8）。

⚠️ **設定はフラットであり、出所を持つ構造ではない**
（`configs/trajectory_sim/drivetrain-wheel60.json`）。したがって出所は本 Spec の
記録側（`measurements.json` / `layout.json`）にのみ現れる。
⚠️ **スキーマ・キー名・ファイル名には触れない**（design.md「Modified Files」）。
"""

_SHAPE_LIBRARY_DISTRIBUTION: Final[str] = "build123d"
"""形状ライブラリの配布名。

⚠️ **これは import ではない。** `importlib.metadata.version` は配布メタデータを
読むだけでモジュールを実行しないため、本モジュールが形状ライブラリを import
しない規律（docstring「遅延 import」）を破らない。版が要るのは
`GeometryBaseline.generator_version` の記録時だけであり、その時点では形状生成が
成功しているため配布は必ず存在する。
"""

_DEFAULT_VOLUME_REL_TOLERANCE: Final[float] = 1e-6
"""記録を新規に作るときの体積の相対許容差。

⚠️ **1e-7 を下回らせない。** 同一ライブラリが同一形状に対して出す求積誤差は
版差で動くため、0 や 1e-9 を記録すると確実に破綻する（上流 `cli.py` の同名定数の
根拠と同じ）。⚠️ 既存の記録があるときはその許容差を引き継ぐ——記録の内容は
タスク 4.2 の所有であり、`--update-baseline` が黙って上書きしてよい値ではない。
"""

_DEFAULT_BBOX_ABS_TOLERANCE_MM: Final[float] = 1e-3
"""記録を新規に作るときの境界箱の絶対許容差（mm）。1μm は形状として無意味な幅である。"""


# ---------------------------------------------------------------------------
# 終了コードの写像
# ---------------------------------------------------------------------------


def exit_code_for(error: BaseException) -> int:
    """例外から終了コードを決める（本モジュール docstring「終了コード」）。

    ⚠️ **最も派生した一致族を選ぶ。** 表には両階層の基底も載っているため、
    「最初に一致した族」を返す実装では表の並び順で答えが変わり、上流の
    `CadUnavailableError`（3）が基底の 2 に化ける。

    Args:
        error: 送出された例外。

    Returns:
        `EXIT_MISMATCH` / `EXIT_USAGE` / `EXIT_CAD_UNAVAILABLE` のいずれか。
        表に無い `ImportError` は `EXIT_CAD_UNAVAILABLE` へ写す——形状層から
        届く裸の `ImportError` は形状ライブラリの不在そのものであり、
        ⚠️ これを 2 にすると「入力が悪い」と読めて、導入すれば直ることが
        伝わらない。それ以外は `EXIT_USAGE`（呼び出し方の誤りの既定）。
    """
    best: type | None = None
    for family in EXIT_CODE_BY_ERROR:
        if not isinstance(error, family):
            continue
        if best is None or issubclass(family, best):
            best = family
    if best is not None:
        return EXIT_CODE_BY_ERROR[best]
    if isinstance(error, ImportError):
        return EXIT_CAD_UNAVAILABLE
    return EXIT_USAGE


# ---------------------------------------------------------------------------
# 寸法と幾何の組み立て（⚠️ ここ1箇所だけ）
# ---------------------------------------------------------------------------


def _observed_rolling_radius(path: Path) -> ObservedRollingRadius | None:
    """観測記録から実効転がり半径を**値と出所の対で**取り出す（要件 10.3）。

    ⚠️ **代表値が未記入（組立前）なら `None` を返して失敗にしない。** 組立前に
    寸法・隙間・接合の検査を回せることは本 Spec の設計そのものである（要件 1.5,
    1.9）。このとき `derive_layout` は公称値の半分を用い、出所も公称値のものを
    継承する。

    ⚠️ **これは観測の「充足検査」ではない**（`--observations` の担当）。読むのは
    代表値1つとその出所だけであり、他の観測が未記入でも構わない。

    Raises:
        MeasurementError: 記録が存在しない、または記録として成立しない場合。
        ParameterError: 記録の項目が値として成立しない場合。
    """
    record = load_assembly_record(path)
    radius_mm = representative_rolling_radius_mm(record)
    if radius_mm is None:
        return None
    return ObservedRollingRadius(
        radius_mm=radius_mm,
        provenance=record.provenance[REPRESENTATIVE_DIAMETER_PATH],
    )


def _params_and_layout(args: argparse.Namespace) -> tuple[ResolvedParams, ChassisLayout]:
    """寸法を読み、その寸法から幾何を導出する（⚠️ **この順序が本モジュールの約束**）。

    ⚠️ **本関数以外で `load_params` / `derive_layout` を呼ばない。**
    `shapes.check_before_build(params, layout)` も `joints.derive_fastener_schedule
    (layout, params)` も、渡された対をそのまま信じて片方から引き直さない
    （tasks.md 3.6 の申し送り）。2箇所目の組み立てを作れば、⚠️ **古い `layout` を
    新しい `params` に対して検査する**経路が生まれ、隙間の高さだけが古い寸法の
    まま「足りている」と報告されうる。

    ⚠️ **観測はここで幾何へ差し込む**（本モジュール docstring「実効転がり半径の
    配線」）。`ResolvedParams` へは混ぜない。

    Returns:
        `(params, layout)`。⚠️ **必ず対で扱うこと。**
    """
    params = load_params(args.dimensions)
    layout = derive_layout(params, _observed_rolling_radius(args.measurements))
    return params, layout


# ---------------------------------------------------------------------------
# layout（要件 3.1-3.6, 10.4, 10.6-10.8）
# ---------------------------------------------------------------------------


def _read_json_document(path: Path, label: str) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ⚠️ 読めない・解析できないは**入力の誤り**（終了コード 2）である。照合の
    不一致（1）と混ぜない——ファイルが壊れていることと、値が食い違っていることは、
    直し方が違う。

    ⚠️ **「読めない」には文字符号化の誤りも含む。** `UnicodeDecodeError` は
    `ValueError` の一種であり `OSError` ではないため、`except OSError` だけでは
    素通りする。本 Spec の設定ファイルは日本語を含み、⚠️ CP932 で保存された
    ファイルを渡される経路は現実にある——そこでパス名を持たない裸の例外を
    投げれば、**どのファイルが読めないのか**が失敗から消える。

    Raises:
        ParameterError: 読み込み・復号・解析のいずれかに失敗した場合。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{label} {path} を読めない（{exc}）。") from exc
    except UnicodeDecodeError as exc:
        raise ParameterError(
            f"{label} {path} は UTF-8 として読めない（{exc}）。"
            "設定ファイルは UTF-8 で保存すること。"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{label} {path} を JSON として解析できない（{exc}）。") from exc


def _check_simulator_config(path: Path, layout: ChassisLayout) -> None:
    """シミュレータ設定のホイール径が現在の幾何と一致することを検査する（要件 10.8）。

    ⚠️ **還元（値の書き込み）は本関数の仕事ではない**——それはタスク 7.1 が
    観測の確定後に行う（要件 10.6, 10.7）。本関数が持つのは一致の**検査**だけで
    ある。

    Raises:
        ConsistencyError: 値が食い違う場合、または値が記録されていない場合。
            ⚠️ **記録が無いことを一致として読み飛ばさない**——還元の済んでいない
            設定は「一致していない」のであって「検査対象外」ではない。
        ParameterError: 設定が読めない・構造が違う場合。
    """
    document = _read_json_document(path, "--check の設定")
    if not isinstance(document, dict):
        raise ParameterError(f"--check {path}: 設定はオブジェクトでなければならない。")

    recorded = document.get(SIMULATOR_WHEEL_DIAMETER_KEY)
    if recorded is None:
        raise ConsistencyError(
            f"--check {path}: {SIMULATOR_WHEEL_DIAMETER_KEY} が記録されていない。"
            "本 Spec が保持する実効ホイール径が還元されていない（要件 10.6, 10.8）。"
        )
    if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        raise ParameterError(
            f"--check {path}: {SIMULATOR_WHEEL_DIAMETER_KEY}={recorded!r} は数でなければならない。"
        )

    effective_diameter_mm = 2.0 * layout.vertical.effective_rolling_radius_mm
    if float(recorded) != effective_diameter_mm:
        raise ConsistencyError(
            f"{path} の {SIMULATOR_WHEEL_DIAMETER_KEY}={recorded!r} は、"
            f"本 Spec の幾何が持つ実効ホイール径 {effective_diameter_mm!r}mm と"
            f"一致しない（要件 10.8）。参照元は幾何が {DEFAULT_LAYOUT_PATH}、"
            f"観測が {DEFAULT_MEASUREMENTS_PATH} である。"
            "⚠️ 還元は設定ファイルの**値のみ**で行うこと（要件 10.7）。"
        )


def _cmd_layout(args: argparse.Namespace) -> int:
    """幾何を導出し、導出記録へ書き出す（要件 3.1-3.6, 3.9）。

    ⚠️ 導出そのものは `layout.derive_layout` にしか無い。本関数は導出しない
    ——読み、呼び、書く。
    """
    params, layout = _params_and_layout(args)
    dump_layout(layout, args.output)

    vertical = layout.vertical
    nominal_diameter_mm = params.chassis.wheel.nominal_diameter_mm
    effective_diameter_mm = 2.0 * vertical.effective_rolling_radius_mm
    print(
        f"{PROGRAM} layout: base_radius_mm={layout.base_radius_mm!r}"
        f"（出所: {layout.provenance.value}）を {args.output} へ書き出した"
    )
    print(f"  取付角 {list(layout.wheel_angles_deg)!r} 度（機体 +x から反時計回り）")
    print(f"  アーム長 {layout.arm_length_mm!r}mm / 導出式 {layout.__class__.__name__}")
    # ⚠️ 要件 10.4「実効転がり径の測定手順と、**公称値との差**を記録する」。
    # 差は `layout.load_compression_mm` が唯一の導出であり、ここは表示だけを行う
    # ——差を3つ目の設定ファイルへ書けば、同じ値を2箇所で持つことになる。
    print(
        f"  実効転がり径 {effective_diameter_mm!r}mm"
        f"（公称 {nominal_diameter_mm!r}mm / 差 "
        f"{2.0 * load_compression_mm(layout, params)!r}mm、"
        f"沈み込み {load_compression_mm(layout, params)!r}mm）"
    )
    for name in (
        "motor_body_bottom_height_mm",
        "axle_center_height_mm",
        "fastener_bottom_height_mm",
        "mount_face_height_mm",
    ):
        print(f"  鉛直スタック {name}={getattr(vertical, name)!r}mm")
    print(
        f"  転倒余裕の見積もり {layout.tipping.accel_limit_mm_s2!r}mm/s^2"
        f"（合否条件ではない: {layout.tipping.is_pass_criterion}）"
    )

    if args.check is not None:
        _check_simulator_config(args.check, layout)
        print(
            f"{PROGRAM} layout: {args.check} の "
            f"{SIMULATOR_WHEEL_DIAMETER_KEY} は現在の幾何と一致する"
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# joints（要件 2.1, 2.6-2.10）
# ---------------------------------------------------------------------------


def _cmd_joints(args: argparse.Namespace) -> int:
    """接合部と締結部品の一覧を導出し、記録へ書き出す（要件 2.10 / OQ-09）。

    ⚠️ **手書きの一覧を持たない**——件数も長さも幾何と寸法から決まる
    （`joints.derive_joints`）。⚠️ **形状ライブラリを要さない。**
    """
    params, layout = _params_and_layout(args)
    schedule = derive_fastener_schedule(layout, params)
    dump_fastener_schedule(schedule, args.output)

    print(
        f"{PROGRAM} joints: 接合部 {len(schedule.joints)} 件 / "
        f"締結部品 {len(schedule.lines)} 行を {args.output} へ書き出した"
    )
    for line in schedule.lines:
        print(f"  {line.kind} {line.designation} 長さ {line.length_mm!r}mm × {line.count}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# check（要件 1.12, 2.3, 4.4, 9.8）
# ---------------------------------------------------------------------------


def _regenerate_metrics(
    params: ResolvedParams, layout: ChassisLayout
) -> Mapping[str, PartMetrics]:
    """形状を再生成し、部品名から指標への対応表を返す（要件 1.12）。

    ⚠️ **形状層はここで初めて import される**（docstring「遅延 import」）。
    非導入の環境では `shapes` が `CadUnavailableError` を送出するため、
    ⚠️ **包み直さずそのまま伝播させる**——それが終了コード 3 の唯一の出どころで
    ある。万一の裸の `ImportError` も `exit_code_for` が 3 へ写す。

    ⚠️ **`params` から `layout` を引き直さない。** 渡された対をそのまま使う。
    """
    from chassis_mechanism.shapes import build_parts

    return {part.name: part.metrics for part in build_parts(params, layout)}


def _presence(value: float) -> str:
    """`PRESENCE_FIELD` の値を人の読む語にする（`PRESENT` / `ABSENT`）。"""
    if value == PRESENT:
        return "在"
    if value == ABSENT:
        return "不在"
    return repr(value)  # pragma: no cover - `compare_metrics` はこの2値しか作らない


def _format_mismatch(mismatch: MetricsMismatch) -> str:
    """不一致1件を1行に整形する。

    ⚠️ **部品の不在／余剰を他の乖離と区別する。** 「部品が消えた」ことと
    「体積が 1% ずれた」ことは、直し方も緊急度も違う。
    """
    if mismatch.field_name == PRESENCE_FIELD:
        label = VANISHED_LABEL if mismatch.regenerated == ABSENT else APPEARED_LABEL
        return (
            f"  {label} {mismatch.part_name}: "
            f"記録={_presence(mismatch.recorded)} 再生成={_presence(mismatch.regenerated)}"
        )
    return (
        f"  {DEVIATION_LABEL} {mismatch.part_name} {mismatch.field_name}: "
        f"記録={mismatch.recorded!r} 再生成={mismatch.regenerated!r}"
    )


def _check_observations(path: Path) -> int:
    """観測記録の充足を検査する（要件 9.8 / ⚠️ **独立した指定**）。

    ⚠️ **判定にモータへ通電する項目は1つも無い**（要件 9.8, 9.9）。判定式そのものは
    `assembly.missing_observations` が持ち、本関数は呼んで並べるだけである。
    """
    record = load_assembly_record(path)
    outstanding = missing_observations(record)
    if not outstanding:
        print(f"{PROGRAM} check: 観測記録 {path} の必須項目はすべて充足している（要件 9.8）")
        return EXIT_OK

    print(
        f"{PROGRAM} check: 観測記録 {path} に未了の必須項目が "
        f"{len(outstanding)} 件ある（要件 9.8。組立前は未充足が正常である）",
        file=sys.stderr,
    )
    for entry in outstanding:
        print(f"  {entry}", file=sys.stderr)
    return EXIT_MISMATCH


def _cmd_check(args: argparse.Namespace) -> int:
    """寸法・隙間・接合・形状指標の総合検査（要件 1.12, 2.3, 4.4）。

    順序は「形状ライブラリを要さない検査を先に」である。⚠️ **CAD 非導入の環境でも
    寸法の不正・隙間の不足・接合の不成立・記録の陳腐化は同じ終了コードで検出
    される**。

    1. 寸法と幾何（`_params_and_layout`）
    2. 材料・造形可能寸法・床との隙間・要素の実現（`shapes.check_before_build`）。
       ⚠️ **関門は `shapes` の1箇所にしかなく、本関数はそれを呼ぶだけである**
       （tasks.md 3.6 の申し送り）。⚠️ この関門は形状ライブラリを import しない
    3. 接合部と締結部品の導出（当たり面が上流と本 Spec の両方の下限を満たすこと）
    4. `--observations` が指定されたときだけ観測記録の充足（⚠️ **既定に混ぜない**）
    5. 記録のパラメータ識別子（`baseline.verify_digest`）
    6. `--digest-only` でなければ形状を再生成して指標を照合（⚠️ ここだけ CAD が要る）

    ⚠️ **観測の充足検査は形状指標の記録の読み込みより前に置く。** 充足検査は
    `measurements.json` だけを入力とし、記録に論理的に依存しない——後ろに置くと、
    記録がまだ無い間（タスク 4.2 より前）は `--observations` を指定しても
    「記録が無い」で止まり、⚠️ **観測の未了一覧を1度も見られない**。前に置けば、
    記録の有無に関わらず未了項目が標準エラーへ出る。

    ⚠️ **記録が無ければ失敗する。** 既定の記録
    `configs/chassis_mechanism/geometry-baseline.json` を作るのは**タスク 4.2**で
    あり、それまでこの経路は「記録が無い」ことをパスつきで報せるところまで完走
    する（上流 `load_baseline` が終了コード 2 の `ParameterError` を送出する）。
    ⚠️ **無いことを成功にしない**——部品を持たない記録はどんな再生成結果とも
    一致してしまう。
    """
    params, layout = _params_and_layout(args)
    print(f"{PROGRAM} check: 寸法 {args.dimensions or DEFAULT_DIMENSIONS_PATH} を読み、幾何を導出した")

    from chassis_mechanism.shapes import check_before_build

    check_before_build(params, layout)
    print(f"{PROGRAM} check: 材料・造形可能寸法・床との隙間・要素の実現は成立している")

    schedule = derive_fastener_schedule(layout, params)
    print(
        f"{PROGRAM} check: 接合部 {len(schedule.joints)} 件と "
        f"締結部品 {len(schedule.lines)} 行が導出できる"
    )

    # ⚠️ **記録の読み込みより前に置く**（本関数 docstring）。充足検査の入力は
    # 観測記録だけであり、形状指標の記録に依存しない。
    code = EXIT_OK
    if args.observations:
        code = _check_observations(args.measurements)

    baseline = load_baseline(args.baseline)
    verify_digest(baseline, params.chassis)
    print(f"{PROGRAM} check: {args.baseline} のパラメータ識別子は現在の寸法と一致する")

    if args.digest_only:
        print(
            f"{PROGRAM} check: --digest-only のため形状を再生成しない"
            "（形状ライブラリを要さない検査のみを行った）"
        )
    else:
        measured = _regenerate_metrics(params, layout)
        mismatches = compare_metrics(baseline, measured)
        if mismatches:
            print(
                f"{PROGRAM} check: 記録 {args.baseline} と再生成の間に "
                f"{len(mismatches)} 件の不一致がある（要件 1.12）",
                file=sys.stderr,
            )
            for mismatch in mismatches:
                print(_format_mismatch(mismatch), file=sys.stderr)
            code = EXIT_MISMATCH
        else:
            print(f"{PROGRAM} check: {len(measured)} 部品の形状指標が記録と一致する")
    return code


# ---------------------------------------------------------------------------
# build（要件 1.11）
# ---------------------------------------------------------------------------


def _generator_version() -> str:
    """記録に残す形状ライブラリの版（情報用。照合には使わない）。"""
    try:
        version = importlib.metadata.version(_SHAPE_LIBRARY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - 生成成功後は在る
        return f"{_SHAPE_LIBRARY_DISTRIBUTION} (版不明)"
    return f"{_SHAPE_LIBRARY_DISTRIBUTION} {version}"


def _existing_tolerances(path: Path) -> tuple[float, float]:
    """既存の記録から許容差を引き継ぐ（無ければ既定値）。

    ⚠️ **記録の内容はタスク 4.2 の所有である。** `--update-baseline` は指標と
    識別子を更新するためのものであって、許容差を黙って既定値へ戻してよいもの
    ではない——許容差の見直しは形状の実測に基づく判断であり、再生成の副作用として
    消えてはならない。読めない記録に当たった場合は、既定値へ戻すことを
    ⚠️ **標準エラーへ明示してから**行う。
    """
    if not path.exists():
        return _DEFAULT_VOLUME_REL_TOLERANCE, _DEFAULT_BBOX_ABS_TOLERANCE_MM
    try:
        previous = load_baseline(path)
    except UpstreamParameterError as exc:
        print(
            f"{PROGRAM} build: 既存の記録 {path} を読めなかったため、許容差を既定値"
            f"（体積 {_DEFAULT_VOLUME_REL_TOLERANCE!r} / 境界箱 "
            f"{_DEFAULT_BBOX_ABS_TOLERANCE_MM!r}mm）で作り直す（{exc}）",
            file=sys.stderr,
        )
        return _DEFAULT_VOLUME_REL_TOLERANCE, _DEFAULT_BBOX_ABS_TOLERANCE_MM
    return previous.volume_rel_tolerance, previous.bbox_abs_tolerance_mm


def _update_baseline(
    path: Path, params: ResolvedParams, measured: Mapping[str, PartMetrics]
) -> None:
    """形状指標の記録を書き出す（要件 1.12 の書き出し経路。タスク 4.2 が用いる）。

    ⚠️ **識別子は寸法パラメータだけから作る**（`config.parameters_digest`）。
    観測（`measurements.json`）を含めると、観測のたびに形状の再生成が要求される。
    """
    volume_tolerance, bbox_tolerance = _existing_tolerances(path)
    dump_baseline(
        GeometryBaseline(
            schema_version=SCHEMA_VERSION,
            parameters_digest=parameters_digest(params.chassis),
            volume_rel_tolerance=volume_tolerance,
            bbox_abs_tolerance_mm=bbox_tolerance,
            generator_version=_generator_version(),
            parts=dict(measured),
        ),
        path,
    )
    print(f"{PROGRAM} build: 形状指標の記録を {path} へ更新した（{len(measured)} 部品）")


def _cmd_build(args: argparse.Namespace) -> int:
    """全部品を生成し、3形式で原子的に書き出す（要件 1.11）。

    ⚠️ **画面表示・対話操作・外部 CAD の起動を要さない**（要件 1.11）。

    ⚠️ **関門は `shapes.build_parts` の先頭にある**（`check_before_build`）。
    本関数は関門を通った戻り値だけを `export_parts` へ渡す——形を作らずに
    ファイル名だけを組み立てる経路を作ると、関門を通らない生成物が出る
    （tasks.md 3.6 の申し送り）。

    ⚠️ 形状ライブラリが無い環境では `shapes` が `CadUnavailableError` を送出する。
    これを専用の終了コード 3 へ写すのが本モジュールの責務であり、⚠️ **成功として
    黙って読み飛ばさない**（要件 1.11）。
    """
    params, layout = _params_and_layout(args)

    from chassis_mechanism.shapes import build_parts

    parts = build_parts(params, layout)

    from chassis_mechanism.export import export_parts

    exported = export_parts(parts, args.output_dir)

    print(f"{PROGRAM} build: {len(exported)} 部品を書き出した")
    for part in parts:
        metrics = part.metrics
        print(
            f"  {part.name}: 体積={metrics.volume_mm3!r}mm^3 "
            f"外接箱={metrics.bbox_mm!r}mm 立体数={metrics.solid_count}"
        )
    for part in exported:
        for file_name in part.file_names:
            print(f"    {file_name}")

    if args.update_baseline:
        _update_baseline(
            args.baseline, params, {part.name: part.metrics for part in parts}
        )
    return EXIT_OK


# ---------------------------------------------------------------------------
# 引数の解析と入口
# ---------------------------------------------------------------------------

_HANDLERS: Final[Mapping[str, Callable[[argparse.Namespace], int]]] = {
    "build": _cmd_build,
    "check": _cmd_check,
    "layout": _cmd_layout,
    "joints": _cmd_joints,
}


def _common_parser() -> argparse.ArgumentParser:
    """4サブコマンドが共有する入力の指定（⚠️ **既定は出荷の設定ファイルである**）。

    ⚠️ **`--dimensions` と `--measurements` を全サブコマンドが持つ。** 幾何は
    どのサブコマンドでも同じ手順（`load_params` → `derive_layout`）で組み立てられ、
    その入力は寸法と観測の2つだからである——片方だけを差し替えられる形にすると、
    サブコマンドごとに違う幾何を見ることになる。
    """
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--dimensions",
        type=Path,
        default=None,
        help=f"寸法パラメータの設定ファイル。省略時は {DEFAULT_DIMENSIONS_PATH}。",
    )
    parser.add_argument(
        "--measurements",
        type=Path,
        default=DEFAULT_MEASUREMENTS_PATH,
        help=(
            "組立後の観測記録。実効転がり径の代表値が記入されていれば、それを"
            "幾何の実効転がり半径として用いる（未記入なら公称値の半分）。"
        ),
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    """4サブコマンドの引数解析器を組み立てる（design.md `#### Cli`）。"""
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "機体の形状生成・総合検査・幾何の導出と照合・接合部と締結部品の導出を行う"
            "1回きりのバッチ入口。常駐しない。終了コードは "
            f"{EXIT_OK} 正常 / {EXIT_MISMATCH} 検査の不一致・違反 / "
            f"{EXIT_USAGE} 使い方の誤り・入力不正 / "
            f"{EXIT_CAD_UNAVAILABLE} 形状生成の環境が無い、である。"
        ),
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True, metavar="subcommand")

    build = subparsers.add_parser(
        "build",
        parents=[common],
        help="形状を生成し STEP / STL / 3MF を書き出す（形状ライブラリが要る）",
        description=(
            "寸法パラメータから全部品を構築し、3形式で原子的に書き出す。"
            "形状ライブラリが無い環境では終了コード "
            f"{EXIT_CAD_UNAVAILABLE} で失敗する（成功にはしない）。"
        ),
    )
    build.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="生成物の出力先。省略時は export の既定（var/cad/chassis/）を用いる。",
    )
    build.add_argument(
        "--update-baseline",
        action="store_true",
        help="生成した指標で形状指標の記録を更新する。既定では記録に触れない。",
    )
    build.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="--update-baseline の書き出し先となる形状指標の記録。",
    )

    check = subparsers.add_parser(
        "check",
        parents=[common],
        help="寸法・隙間・接合・形状指標の総合検査（--digest-only は再生成しない）",
        description=(
            "寸法・床との隙間・接合部・形状指標の記録を照合する。不一致・違反は"
            f"終了コード {EXIT_MISMATCH} である。⚠️ 観測記録の充足検査は"
            "既定に含まれない（--observations で明示すること）——観測は組立まで"
            "必ず未充足であり、混ぜると組立前に他の検査を回せなくなる。"
        ),
    )
    check.add_argument(
        "--digest-only",
        action="store_true",
        help=(
            "形状を再生成せず、記録のパラメータ識別子だけを現在の寸法設定と"
            "突き合わせる。形状ライブラリを要さない。"
        ),
    )
    check.add_argument(
        "--observations",
        action="store_true",
        help=(
            "観測記録の必須項目が充足していることを併せて検査する（要件 9.8）。"
            "⚠️ 組立前は必ず未充足であるため既定では行わない。"
        ),
    )
    check.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_PATH,
        help="照合する形状指標の記録。⚠️ 既定の記録を作るのはタスク 4.2 である。",
    )

    layout_parser = subparsers.add_parser(
        "layout",
        parents=[common],
        help="幾何を導出して記録へ書き出す（--check でシミュレータ設定と照合）",
        description=(
            "ホイール配置半径・取付角・鉛直/軸方向スタック・転倒余裕の見積もりを"
            "導出し、入力・式・出所つきの記録として書き出す。--check を与えると、"
            "シミュレータの駆動系設定に記録されたホイール径と突き合わせる"
            f"（不一致は終了コード {EXIT_MISMATCH}）。"
        ),
    )
    layout_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_LAYOUT_PATH,
        help="導出記録の書き出し先。",
    )
    layout_parser.add_argument(
        "--check",
        type=Path,
        default=None,
        metavar="CONFIG",
        help=(
            "シミュレータの駆動系設定ファイル。"
            f"{SIMULATOR_WHEEL_DIAMETER_KEY} を現在の幾何と比較する（要件 10.8）。"
        ),
    )

    joints_parser = subparsers.add_parser(
        "joints",
        parents=[common],
        help="接合部と締結部品の一覧を導出して書き出す",
        description=(
            "幾何と寸法から接合部を導出し、当たり面・造形姿勢・締結部品の"
            "種別・呼び・長さ・数量を一覧として書き出す（要件 2.10 / OQ-09）。"
        ),
    )
    joints_parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_JOINT_SCHEDULE_PATH,
        help="接合部と締結部品の記録の書き出し先。",
    )
    return parser


def _exit_code_of_system_exit(exc: SystemExit) -> int:
    """`argparse` が投げる `SystemExit` を終了コードへ写す。

    `--help` は 0、使用法の誤りは 2 である（`argparse` の既定と design.md の
    「使い方の誤り」が一致している）。⚠️ `main()` を総関数に保つために吸収する
    ——in-process の検査が `pytest.raises(SystemExit)` を強いられずに済む。
    """
    code = exc.code
    if code is None:
        return EXIT_OK
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    """コマンド入口の実処理。1回呼ぶと1回だけ処理し、終了コードを返す。

    常駐しない。サーバ・ソケット・監視ループを持たない。

    Args:
        argv: 引数列。省略時は `sys.argv[1:]`。

    Returns:
        `EXIT_OK` / `EXIT_MISMATCH` / `EXIT_USAGE` / `EXIT_CAD_UNAVAILABLE`。

    Notes:
        ⚠️ 例外は**送出せずに終了コードへ写す**。本 Spec の6系統と⚠️ **上流の
        5系統**を `EXIT_CODE_BY_ERROR` が、形状層から届く裸の `ImportError` を
        `exit_code_for` が受ける。⚠️ **上流の失敗を包み直さない**——包めば
        「上流の設定が壊れている」のか「本 Spec の設定が壊れている」のかが
        メッセージから消える（`errors.py`）。

        `OSError`（記録の書き出し先が書けない等）も受けて 2 を返す。
        ⚠️ 書き出し先の不備は利用者が直せる**入力の誤り**であり、追跡情報を
        並べて異常終了するより、1行で理由を示すほうが直しやすい。
        `export_parts` 内部の書き出し失敗は `GeometryError` に包まれて届く
        （`export._write_part`）ため、そちらは 1 になる。

        ⚠️ **`UnicodeDecodeError` もここで受ける。** UTF-8 でない設定ファイルを
        渡されたときに `Path.read_text(encoding="utf-8")` が投げる例外であり、
        `ValueError` の一種で `OSError` ではない——⚠️ **4つの読み手
        （`config` / `assembly` / `baseline` / 本モジュール）のうち自前で包み直すのは
        本モジュールだけである**ため、ここで受けなければ CP932 で保存された
        `dimensions.json` が終了コード 1 と追跡情報つきの異常終了になる。
        1 は「検査の不一致・違反」であり、⚠️ CI が「読めないファイル」と
        「乖離した形状指標」を区別できなくなる。
    """
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return _exit_code_of_system_exit(exc)

    handler = _HANDLERS[args.subcommand]
    try:
        return handler(args)
    except (
        ChassisMechanismError,
        UpstreamCatchMechanismError,
        ImportError,
        OSError,
        UnicodeDecodeError,
    ) as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return exit_code_for(exc)
