"""呼び出し方の誤りを表す例外階層（design.md「Error Handling」/ 要件 1.3, 1.4, 5.3）。

**この階層は評価結果を表さない。** design.md「Error Strategy」が定める区分を、
上流の `trajectory_sim`（`src/trajectory_sim/errors.py`）および
`prediction_core` と同じ形でここに固定する。

- **評価結果（不適合・不一致）は値で返す。** 候補が選定基準を満たさないことは
  `CandidateVerdict.accepted = False`（`selection.py`）、再生成した形状指標が
  記録と食い違うことは `compare_metrics` が返す `MetricsMismatch` の並び
  （`metrics.py`）である。どちらも**正常系の結果**であり、理由の一覧を伴って
  返らなければ「どこが駄目なのか」が呼び出し側に届かない。
  ⚠️ **これらを例外にしてはならない。** 最初の不適合で打ち切る実装へ滑り、
  要件 6.2 の「不適合であった項目名を示す」が成立しなくなる。
- **呼び出し方の誤り・入力の不正だけを例外にする。** パラメータ不正・選定不正・
  形状不正・整合不一致・形状環境不在の5系統を本モジュールが持つ。

`CatchMechanismError` は `ValueError` を直接継承する基底例外である。5つの具象
例外はその派生であり、したがって `ValueError` の派生でもあるため、次のいずれでも
捕捉できる（tasks.md タスク 1.2 の観測可能な完了状態）。

- `except CatchMechanismError` — 本パッケージ由来の失敗だけをまとめて捕捉する
- `except ValueError` — `catch_mechanism` を知らない呼び出し側が既に書いている
  防御をそのまま働かせる

5系統を独立したクラスに分けるのは、`cli` が系統ごとに違う終了コードを返すため
である（design.md「Cli」）。0 正常 / 1 検査の不一致（`ConsistencyError`）/
2 使い方の誤り・入力不正（`ParameterError`・`SelectionError`・`GeometryError`）/
3 形状生成の環境が無い（`CadUnavailableError`）。⚠️ 系統の間に継承関係を作ると
`except` の順序で終了コードが変わってしまうため、5系統は**互いに素**に保つ。

本モジュールは依存を持たない（design.md「Components and Interfaces」で Errors の
Key Dependencies は「なし」）。標準ライブラリも自パッケージの他モジュールも
import しない。とりわけ形状ライブラリ（build123d）を import しないことは、
形状環境を持たない実行環境でも `CadUnavailableError` を送出できるために必要で
ある——「ライブラリが無いことを報せる例外」がそのライブラリを要求しては、
要件 5.3 の「明示的な失敗として扱う」が成立しない。
"""

from __future__ import annotations

__all__ = [
    "CatchMechanismError",
    "ParameterError",
    "SelectionError",
    "GeometryError",
    "ConsistencyError",
    "CadUnavailableError",
]


class CatchMechanismError(ValueError):
    """`catch_mechanism` が送出する、呼び出し方の誤りを表す例外の基底。

    `ValueError` を直接継承するため、本パッケージを知らない呼び出し側の
    `except ValueError` による既存の防御をそのまま働かせられる。
    候補の不適合や指標の不一致（本モジュール docstring 参照）は、この例外では
    表現しない。それらは値として返す。
    """


class ParameterError(CatchMechanismError):
    """寸法パラメータ・設定ファイルの内容が不正な場合に送出する（要件 1.3, 1.4）。

    未知の項目、必須の寸法値の欠損、物理的にあり得ない符号・範囲、許可されていない
    材料の指定など、値そのものが成立していないときに用いる。
    メッセージには**該当する項目名**（および値）を載せる——「設定が不正」とだけ
    言われても、どの項目を直せばよいかが分からなければ直せない。
    """


class SelectionError(CatchMechanismError):
    """選定の**入力**が不正な場合に送出する（要件 1.3 の選定基準・候補への適用）。

    ⚠️ **候補が基準を満たさないこと（不適合）はこの例外ではない。** それは
    `CandidateVerdict.accepted = False` という値であり、不適合項目の一覧を伴って
    返る（本モジュール docstring の区分）。

    この例外が表すのは選定の**呼び出し方の誤り**である。例えば選定基準の
    設定ファイルが未知の項目名を挙げている、候補の諸元に必須項目が欠けている、
    存在しない候補を名指しで評価しようとした、といった場合である。
    """


class GeometryError(CatchMechanismError):
    """形状が成立しない・生成物を出力できない場合に送出する（要件 2.4, 2.7, 3.3-3.6）。

    造形制約の違反（`check_envelope` が返す `BuildViolation` を伴う）、現実的な
    上限までに収まる分割数が存在しない場合、および書き出しの失敗（`OSError` を
    包む）に用いる。いずれの場合も**生成物を出力しない**——部分的な成果物が
    出力先に残ることは、造形へ回せない形状を回せると誤認させるためである。

    ⚠️ 違反の内容（軸・超過量）を運ぶ `BuildViolation` は `constraints.py` の
    値型であり、本モジュールは依存を持たないため import しない。送出側が
    メッセージと属性に載せる。
    """


class ConsistencyError(CatchMechanismError):
    """記録された値と現在の値が食い違う場合に送出する（要件 4.5, 7.6, 7.7）。

    形状指標の記録に含まれるパラメータ識別子が現在の寸法設定ファイルの識別子と
    一致しない場合や、シミュレータ設定へ還元した値・出所が導出記録と一致しない
    場合に用いる。メッセージには**双方の値と参照元**を載せる。

    ⚠️ `compare_metrics` が返す `MetricsMismatch`（部品名・項目名・双方の値を
    持つ値型）とは役割が異なる。照合そのものは一覧を値として返し、それを
    失敗として扱うかどうかは呼び出し側（`cli` の `check`）が決める。
    この例外は、照合を待たずに成立していない整合を拒否するためのものである。
    """


class CadUnavailableError(CatchMechanismError):
    """形状生成に必要な外部ライブラリが利用できない場合に送出する（要件 5.3）。

    ⚠️ **形状生成の要求を成功として黙って読み飛ばさない。** 生成したつもりで
    生成物が無い状態は、造形の直前まで気付けない事故になる。`cli` はこの例外を
    専用の終了コード 3 に対応させ、他の入力不正（終了コード 2）と区別する
    （design.md「Cli」/「Error Categories and Responses」）。

    メッセージには**導入方法**を載せる。ライブラリは任意依存 `cad` として宣言
    されており、既定ではインストールされないため、利用できないことは異常では
    なく既定の状態である。

    この失敗は寸法パラメータの利用を妨げない（要件 5.2, 5.3）——形状を要さない
    読み込み・導出・下流への提供は、この例外が送出される環境でも成立する。
    """
