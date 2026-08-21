"""呼び出し方の誤りを表す例外階層(design.md「Errors」/ 要件 6.1, 11.5)。

**この階層はシナリオの成否・評価対象外を表さない。** design.md が定める
方針を、`prediction_core`(`src/prediction_core/errors.py`)と同じ区分で
ここに固定する。

- シナリオの成否・評価対象外は例外にしない。値として返す。
- 呼び出し方の誤り(パラメータ不正・掃引定義不正・出力不正)だけを
  例外にする。

`TrajectorySimError` は `ValueError` を直接継承する基底例外である。
`prediction_core.errors.PredictionCoreError` とは異なり、
このパッケージの基底自身が `ValueError` を継承する。3 つの具象例外は
`TrajectorySimError` のサブクラスであり、したがって `ValueError` の
サブクラスでもあるため、次のいずれでも捕捉できる。

- `except TrajectorySimError` -- このパッケージ由来の失敗だけをまとめて捕捉する
- `except ValueError` -- `trajectory_sim` を知らない呼び出し側が既に
  書いている防御をそのまま働かせる

上流(`prediction_core`)由来の例外(`PredictionConfigError` 等)は、
この階層に握りつぶさず、そのまま伝播させる方針である
(design.md「Errors」)。この方針は呼び出し側での取り扱いであり、
本モジュールはそれらの例外を re-export も継承もしない。

このモジュールは標準ライブラリ以外に依存しない。
"""

from __future__ import annotations


class TrajectorySimError(ValueError):
    """`trajectory_sim` が送出する、呼び出し方の誤りを表す例外の基底。

    `ValueError` を直接継承するため、本パッケージを知らない呼び出し側の
    `except ValueError` による既存の防御をそのまま素通りさせずに捕捉できる。
    シナリオの成否や評価対象外(Requirement 1.6 等)はこの例外では
    表現しない。それらは値として返す。
    """


class ParameterError(TrajectorySimError):
    """パラメータの値が不変条件を満たさない場合に送出する。

    例: 掃引以前の単一シナリオの構築時点で、負であってはならない
    値に負値が指定された場合など、`ScenarioParams` 一式が構築時点で
    成立しないとき。
    """


class SweepDefinitionError(TrajectorySimError):
    """掃引の定義そのものが不正な場合に送出する。

    例: 掃引軸が空である、格子点数が 0 以下である等、
    掃引を実行する前提が成立しないとき(要件 6.1)。
    """


class OutputError(TrajectorySimError):
    """成果物の出力が不正な場合に送出する。

    例: 掃引結果をまとめた成果物を書き出す際、非有限値が
    混入している等、出力契約を満たせないとき。
    """
