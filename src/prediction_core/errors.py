"""呼び出し方の誤りを表す例外階層（要件 9.3 / 10.3）。

**この階層は「予測の失敗」を表さない。** 本パッケージは失敗を 3 カテゴリに
分け、扱いを次のとおり固定する（design.md「Error Handling / Error Strategy」）。

===========================  ==========================  ============================
カテゴリ                     表現                        例
===========================  ==========================  ============================
予測が構造的に成立しない     戻り値 `InvalidPrediction`  サンプル不足、時刻縮退、
                             （例外にしない）            未来側交点なし、非有限値、
                                                         入力契約違反
呼び出し方の誤り             例外                        `min_samples < 3`、
                                                         `gravity_mm_s2 <= 0`
記録の不整合                 例外                        必須キー欠落、型不一致、
                                                         非有限値の JSON 化
===========================  ==========================  ============================

したがって、要件 6.1-6.7 に対応する無効理由のための例外型はここに存在しない。
予測経路の実装は、無効を **値** として返すこと。例外を送出してはならない。

`PredictionCoreError` はパッケージ由来の失敗を一括捕捉するための入口であり、
それ自体は `ValueError` を継承しない。一方、実際に送出される 3 系統は
`ValueError` も継承する。本パッケージを知らない利用側が既に書いている
`except ValueError` による防御をそのまま働かせるためである。

このモジュールは L0 層であり、標準ライブラリ以外も
パッケージ内の他モジュールも import しない（design.md「Dependency Direction」）。
"""

from __future__ import annotations


class PredictionCoreError(Exception):
    """prediction_core が送出するすべての例外の基底。

    利用側はこれを捕捉することで、本パッケージ由来の失敗だけを
    まとめて扱える。予測が無効であることはこの例外では表現されない。
    """


class PredictionConfigError(PredictionCoreError, ValueError):
    """`PredictionConfig` の値が不変条件を満たさない（要件 10.3）。

    `min_samples` に 3 未満を与えた場合など、設定そのものが成立しない
    ときに構築時点で送出する。予測を実行する前に失敗させることで、
    不正な設定のまま予測系列が生成される事態を防ぐ。
    """


class RecordSchemaError(PredictionCoreError, ValueError):
    """Throw Record の復元時にスキーマが一致しない（要件 9.3）。

    `from_dict` / `from_json` で必須キーが欠けている、または
    値の型が定義と一致しない場合に送出する。
    """


class RecordSerializationError(PredictionCoreError, ValueError):
    """Throw Record を直列化できない（要件 9.3）。

    `to_json` の対象に NaN / Infinity が含まれる場合など、
    RFC 8259 準拠の JSON として表現できない値があるときに送出する。
    メモリ上の忠実性を保つため `to_dict` では送出しない。
    """
