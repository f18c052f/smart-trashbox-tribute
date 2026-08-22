"""コマンドラインエントリポイント（design.md「Cli」/ 要件 7.5, 11.2）。

`python -m trajectory_sim --config <path> --output <path> [--drivetrain <path>]`
という1回きりのバッチ実行経路を提供する。**常駐しない。** サーバ・ソケット・
監視ループの類は一切持たず、`main()` は設定を読み、掃引を実行し、結果を
書き出して終了する（要件11.2）。

設定 JSON は `ScenarioParams` と `SweepSpec` の両方を含む（design.md「Cli」
Responsibilities）。両者の JSON → データクラス変換は、`dataclasses` の型
検査のみに基づく汎用的な再帰関数 `_build_dataclass` / `_convert_value` に
一本化する。設定ファイルの構造は `serialize.sweep_result_to_dict` が組み立
てる出力の `parameters` と対称になるように定めており（design.md「Cli」
Implementation Notes Risks: 「変換は1箇所（cli）に閉じる」）、この変換ロジック
を `serialize.py` 側に重複させない。

未知のキーは、トップレベル（`parameters` / `sweep` 以外のキー）だけでなく、
`_build_dataclass` が再帰するあらゆるネストの階層でも黙って無視せず
`trajectory_sim.errors.ParameterError` として拒否する（tasks.md タスク4.3:
「設定ファイルの未知のキーを黙って無視せず、パラメータ不正として報告する」）。

`--drivetrain <path>` は、機体パラメータ（`DrivetrainParams`）のみを別
ファイルから取り込むための指定である（要件4.2, design.md「Cli」
Responsibilities: 「60mm / 48mm の差し替えを1オプションで行えるように
する」）。**指定された場合、`--config` 側の `parameters.drivetrain` が
あってもなくても、常に `--drivetrain` ファイルの内容が優先される**
（`--config` 側の値は使われない）。この優先順位は本 CLI の `--help` にも
明記する。

設定の不備は、`run_sweep` / `write_sweep_result` を呼び出す前に完全に
検出し、出力ファイルへは一切触れない（要件7.5 に隣接する運用要件:
「不正な設定では出力ファイルを生成しない」）。`run_sweep` 自身も評価前に
一括検証し（タスク3.2）、`write_sweep_result` も書き出し前に検証する
（タスク4.2）ため、設定パース → `run_sweep` → `write_sweep_result` の順で
呼び出すだけで、3層の fail-fast 検証が自然に合成される。
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

from trajectory_sim.errors import ParameterError, TrajectorySimError
from trajectory_sim.params import DrivetrainParams, ScenarioParams
from trajectory_sim.serialize import write_sweep_result
from trajectory_sim.sweep import SweepSpec, run_sweep

__all__ = ["main"]

# `X | None` という PEP604 記法の実行時の型は `types.UnionType` だが、
# 本モジュールの許可 import 一覧に `types` が無いため、`types.UnionType`
# を import せずに同じ型オブジェクトを得るためこの式で求める
# （`typing.get_origin(float | None)` はこの型オブジェクトを返す）。
_UNION_TYPE: type = type(int | None)

# `DrivetrainParams.from_wheel` のキーワード引数名（`src/trajectory_sim/params.py`
# 参照）。`inspect` を import せずに手書きで固定する（許可 import 一覧に
# `inspect` が無いため）。この集合と `DrivetrainParams.from_wheel` の実際の
# シグネチャがずれた場合は、本モジュールのテスト
# （wheel 形式の `--drivetrain` 系テスト）が破綻して検知される。
_WHEEL_SHAPE_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "wheel_diameter_mm",
        "motor_rpm",
        "speed_efficiency",
        "max_accel_mm_s2",
        "max_decel_mm_s2",
        "control_period_ms",
        "command_latency_ms",
    }
)
_WHEEL_SHAPE_OPTIONAL_KEYS: frozenset[str] = frozenset({"integration_step_ms"})
_WHEEL_SHAPE_ALL_KEYS: frozenset[str] = _WHEEL_SHAPE_REQUIRED_KEYS | _WHEEL_SHAPE_OPTIONAL_KEYS

_CONFIG_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"parameters", "sweep"})
_SWEEP_KEYS: frozenset[str] = frozenset(
    {
        "kind",
        "axes",
        "trials_per_cell",
        "seed",
        "catch_ratio_threshold",
        "keep_representative_record",
    }
)


def _build_dataclass(cls: type, data: object, path: str) -> object:
    """JSON の `dict` から `cls`（データクラス）のインスタンスを構築する。

    `cls` のフィールド名と厳密に一致しないキーは `ParameterError` で拒否する
    （このチェックが、あらゆるネスト階層で未知キーを拒否する仕組みの本体）。
    値の変換は `_convert_value` に委譲し、フィールドの型（`typing.get_type_hints`
    で解決した実体の型）に応じて再帰・enum coerce・素通しを行う。
    """
    if not isinstance(data, dict):
        raise ParameterError(f"{path}: オブジェクト（dict）を期待したが {type(data).__name__} だった")
    hints = get_type_hints(cls)
    field_names = {f.name for f in dataclasses.fields(cls)}
    unknown = set(data.keys()) - field_names
    if unknown:
        raise ParameterError(f"{path}: 未知のキー {sorted(unknown)!r}")
    kwargs: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _convert_value(data[f.name], hints[f.name], f"{path}.{f.name}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ParameterError(f"{path}: 必須フィールドが不足している ({exc})") from exc


def _convert_value(value: object, type_hint: object, path: str) -> object:
    """`value`（JSON 由来の値）を `type_hint` が指す型へ変換する。

    優先順位（design.md「Cli」の下請けとしてこのタスクで定めた変換規則）:
      1. データクラス型 -> `_build_dataclass` へ再帰する。
      2. `enum.Enum` のサブクラス -> 文字列から `type_hint(value)` で coerce する。
      3. `Mapping[str, SomeEnum]`（`ScenarioParams.provenance` の形） ->
         各値を `SomeEnum` へ coerce した辞書にする。
      4. `X | None` -> `value is None` ならそのまま `None`、それ以外は `X` へ
         再帰する（`X | str` のように `None` を含まない Union は、コード末尾の
         フォールバックでそのまま素通しする）。
      5. `tuple[X, ...]` / `list[X]`（`SweepSpec.axes: tuple[AxisSpec, ...]` /
         `AxisSpec.values: tuple[float | str, ...]` を扱うための拡張。上記
         6 分類には無いが、掃引側のデータクラス木を汎用機構だけで組み立てる
         ために本タスクで追加した）-> 各要素を `X` へ再帰し、`tuple`/`list`
         として返す。
      6. それ以外（`float` / `int` / `str` / `bool`）-> そのまま返す。
    """
    if isinstance(type_hint, type) and dataclasses.is_dataclass(type_hint):
        return _build_dataclass(type_hint, value, path)

    if isinstance(type_hint, type) and issubclass(type_hint, enum.Enum):
        if not isinstance(value, str):
            raise ParameterError(
                f"{path}: 列挙値には文字列を期待したが {type(value).__name__} だった"
            )
        try:
            return type_hint(value)
        except ValueError as exc:
            raise ParameterError(
                f"{path}: 未知の列挙値 {value!r}（{type_hint.__name__}）"
            ) from exc

    origin = get_origin(type_hint)

    if origin is not None and (
        origin is Mapping or (isinstance(origin, type) and issubclass(origin, Mapping))
    ):
        args = get_args(type_hint)
        if len(args) == 2 and isinstance(args[1], type) and issubclass(args[1], enum.Enum):
            enum_type = args[1]
            if not isinstance(value, dict):
                raise ParameterError(
                    f"{path}: オブジェクト（dict）を期待したが {type(value).__name__} だった"
                )
            converted: dict[object, object] = {}
            for key, item in value.items():
                if not isinstance(item, str):
                    raise ParameterError(
                        f"{path}.{key}: 列挙値には文字列を期待したが "
                        f"{type(item).__name__} だった"
                    )
                try:
                    converted[key] = enum_type(item)
                except ValueError as exc:
                    raise ParameterError(
                        f"{path}.{key}: 未知の列挙値 {item!r}（{enum_type.__name__}）"
                    ) from exc
            return converted

    if origin is _UNION_TYPE:
        args = get_args(type_hint)
        non_none_args = [a for a in args if a is not type(None)]
        if value is None and len(non_none_args) != len(args):
            return None
        if len(non_none_args) == 1:
            return _convert_value(value, non_none_args[0], path)
        # `X | Y`（`None` を含まない Union、例: `float | str`）は JSON 側の
        # 値がすでにいずれかの型に一致しているはずなので、そのまま返す
        # （分類6のフォールバックと同じ扱い）。
        return value

    if origin in (tuple, list):
        if not isinstance(value, list):
            raise ParameterError(f"{path}: 配列（list）を期待したが {type(value).__name__} だった")
        args = get_args(type_hint)
        if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
            item_hint: object = args[0]
        elif args:
            item_hint = args[0]
        else:
            item_hint = object
        converted_items = [
            _convert_value(item, item_hint, f"{path}[{index}]") for index, item in enumerate(value)
        ]
        return tuple(converted_items) if origin is tuple else converted_items

    return value


def _read_json_file(path: Path, label: str) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `ParameterError` へ
    統一する。これにより `main()` 側は単一の `except TrajectorySimError` で
    設定読み込みからパイプライン全体の失敗をまとめて捕捉できる。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParameterError(f"{label}: ファイルを読み込めない（{path}）: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParameterError(f"{label}: JSON として解析できない（{path}）: {exc}") from exc


def _parse_config_document(document: object) -> tuple[object, object]:
    """`--config` の JSON 文書から `parameters` / `sweep` の生データを取り出す。

    最上位キーは `parameters` / `sweep` の2つのみを許す（design.md「Cli」
    Responsibilities: 「設定 JSON は `ScenarioParams` と `SweepSpec` の両方を
    含む」）。それ以外のキーの混入、いずれかの欠落は `ParameterError` とする。
    """
    if not isinstance(document, dict):
        raise ParameterError(
            f"--config: オブジェクト（dict）を期待したが {type(document).__name__} だった"
        )
    unknown = set(document.keys()) - _CONFIG_TOP_LEVEL_KEYS
    if unknown:
        raise ParameterError(f"--config: 未知のキー {sorted(unknown)!r}")
    if "parameters" not in document:
        raise ParameterError("--config: 'parameters' キーが必須である")
    if "sweep" not in document:
        raise ParameterError("--config: 'sweep' キーが必須である")
    return document["parameters"], document["sweep"]


def _load_drivetrain_override(data: object, path: str) -> DrivetrainParams:
    """`--drivetrain` ファイルの内容から `DrivetrainParams` を構築する。

    2つの排他的な形式を、この順で判定する（design.md「Cli」Responsibilities /
    tasks.md タスク4.3: 「機体パラメータのみを別ファイルから取り込む」）:

      1. ホイール由来形式: `wheel_diameter_mm` / `motor_rpm` /
         `speed_efficiency` の3キーが揃っている形式。`max_speed_mm_s` と
         同時に指定された場合は、どちらが優先されるかが暗黙になるため
         `ParameterError` で拒否する。
      2. 直接形式: 上記以外はすべて `DrivetrainParams` のフィールド集合
         そのものとして汎用デシリアライザへ渡す。
    """
    if not isinstance(data, dict):
        raise ParameterError(f"{path}: オブジェクト（dict）を期待したが {type(data).__name__} だった")

    has_wheel_trio = {"wheel_diameter_mm", "motor_rpm", "speed_efficiency"} <= data.keys()
    has_max_speed = "max_speed_mm_s" in data

    if has_wheel_trio and has_max_speed:
        raise ParameterError(
            f"{path}: wheel_diameter_mm/motor_rpm/speed_efficiency によるホイール"
            "由来の指定と、max_speed_mm_s による直接指定は同時に指定できない"
            "（どちらが優先されるかを暗黙に決めない）。"
        )

    if has_wheel_trio:
        unknown = set(data.keys()) - _WHEEL_SHAPE_ALL_KEYS
        if unknown:
            raise ParameterError(f"{path}: 未知のキー {sorted(unknown)!r}")
        missing = _WHEEL_SHAPE_REQUIRED_KEYS - data.keys()
        if missing:
            raise ParameterError(f"{path}: 必須フィールドが不足している {sorted(missing)!r}")
        kwargs = {key: data[key] for key in data}
        try:
            return DrivetrainParams.from_wheel(**kwargs)
        except TypeError as exc:
            raise ParameterError(f"{path}: 必須フィールドが不足している ({exc})") from exc

    result = _build_dataclass(DrivetrainParams, data, path)
    assert isinstance(result, DrivetrainParams)
    return result


def _load_scenario_params(
    parameters_data: object, path: str, drivetrain_from_file: DrivetrainParams | None
) -> ScenarioParams:
    """`parameters` の生データから `ScenarioParams` を構築する。

    `--drivetrain` が指定された場合、`--config` 側の `parameters.drivetrain`
    （あってもなくても）を無視し、常に `drivetrain_from_file` を最終的な値
    として使う（design.md「Cli」Implementation Notes Risks: 「変換は1箇所
    （cli）に閉じる」ため、この優先順位もここに閉じ込める）。

    ただし「使わない」ことは「検証しない」ことを意味しない。`--config` 側
    に `parameters.drivetrain` が存在する場合は、`--drivetrain` で上書き
    されて最終的に使われなくなるとしても、その内容自体は
    `_build_dataclass(DrivetrainParams, ...)` に通して検証だけは必ず行う
    （design.md「Cli」Batch/Job Contract: 「未知のキーは `ParameterError`
    で拒否する（黙って無視しない）」。tasks.md タスク4.3:
    「設定ファイルの未知のキーを黙って無視せず、パラメータ不正として
    報告する」。これに上書き有無による例外は無い）。検証に成功しても、
    その結果（`DrivetrainParams` インスタンス）はここでは破棄し、最終的な
    値には使わない — 使うのはあくまで `drivetrain_from_file` そのもの。
    `parameters.drivetrain` が存在しない場合（省略）は検証対象が無いため
    何もしない。

    汎用デシリアライザ（`_build_dataclass`）自体はこの優先順位を知らない
    ため、`drivetrain_from_file` が与えられた場合は一旦そのフィールド値を
    プレースホルダの `dict` として差し込み構築を通してから、
    `dataclasses.replace` で最終的に `drivetrain_from_file` そのものへ
    置き換える（`--config` 側が `parameters.drivetrain` を省略していても
    構築が必須フィールド不足で失敗しないようにするため）。
    """
    if not isinstance(parameters_data, dict):
        raise ParameterError(
            f"{path}: オブジェクト（dict）を期待したが {type(parameters_data).__name__} だった"
        )
    data = dict(parameters_data)
    if drivetrain_from_file is not None:
        if "drivetrain" in data:
            # 検証のためだけに構築する。結果は使わず破棄する（上記 docstring 参照）。
            _build_dataclass(DrivetrainParams, data["drivetrain"], f"{path}.drivetrain")
        data["drivetrain"] = dataclasses.asdict(drivetrain_from_file)

    scenario_params = _build_dataclass(ScenarioParams, data, path)
    assert isinstance(scenario_params, ScenarioParams)

    if drivetrain_from_file is not None:
        scenario_params = dataclasses.replace(scenario_params, drivetrain=drivetrain_from_file)

    return scenario_params


def _build_sweep_spec(sweep_data: object, path: str) -> SweepSpec:
    """`sweep` の生データから `SweepSpec` を構築する。

    未知キーの検査を含め、`_build_dataclass` の汎用機構をそのまま用いる。
    トップレベルで許されるキーの集合（`_SWEEP_KEYS`）は
    `dataclasses.fields(SweepSpec)` と一致するため、二重管理の表を持たない
    （`_SWEEP_KEYS` はドキュメント化のためだけに保持し、検査自体は
    `_build_dataclass` が `dataclasses.fields` から動的に行う）。
    """
    result = _build_dataclass(SweepSpec, sweep_data, path)
    assert isinstance(result, SweepSpec)
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trajectory_sim",
        description=(
            "設定 JSON を読み、掃引（sweep）を実行し、結果 JSON を書き出す"
            "バッチ実行経路。1回の実行で完了し、常駐サーバ・ソケット・"
            "監視ループは持たない（要件11.2）。"
        ),
    )
    parser.add_argument(
        "--config",
        required=True,
        help=(
            "'parameters'（ScenarioParams）と 'sweep'（SweepSpec）の2キーのみを"
            "持つ設定 JSON ファイルへのパス。未知のキーはパラメータ不正として"
            "拒否する。"
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="掃引結果 JSON の書き出し先パス。",
    )
    parser.add_argument(
        "--drivetrain",
        default=None,
        help=(
            "機体パラメータ（DrivetrainParams）のみを含む別ファイルへのパス。"
            "指定した場合、--config 側の parameters.drivetrain が指定されて"
            "いても常にこのファイルの内容が優先される（60mm/48mm ホイールの"
            "差し替えを1オプションで行うための指定）。"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI の実処理。設定を読み、掃引を実行し、結果を書き出して終了する。

    サーバ・ソケット・監視ループは一切持たない。1回呼び出すと1回だけ処理を
    行い、戻り値の終了コードを返して制御を戻す（要件11.2）。

    設定エラー（未知キー・不正値・掃引定義不正・ファイル入出力不能）は、
    `--output` に一切触れる前に検出し、標準エラー出力へ1行のメッセージを
    出したうえで非ゼロの終了コードを返す（tasks.md タスク4.3: 「不正な
    設定では出力ファイルが生成されずにエラー終了する」）。成功時は標準
    出力へ要約1行のみを出す（design.md「Cli」Batch / Job Contract）。
    """
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    output_path = Path(args.output)

    try:
        config_document = _read_json_file(Path(args.config), "--config")
        parameters_data, sweep_data = _parse_config_document(config_document)

        drivetrain_from_file: DrivetrainParams | None = None
        if args.drivetrain is not None:
            drivetrain_document = _read_json_file(Path(args.drivetrain), "--drivetrain")
            drivetrain_from_file = _load_drivetrain_override(drivetrain_document, "--drivetrain")

        scenario_params = _load_scenario_params(parameters_data, "parameters", drivetrain_from_file)
        sweep_spec = _build_sweep_spec(sweep_data, "sweep")

        result = run_sweep(sweep_spec, scenario_params)
        write_sweep_result(result, output_path)
    except TrajectorySimError as exc:
        print(f"trajectory_sim: {exc}", file=sys.stderr)
        return 1

    print(f"trajectory_sim: {len(result.cells)} 格子点を {output_path} へ書き出した")
    return 0
