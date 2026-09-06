"""本 Spec の失敗を表す例外階層（design.md `#### Errors` / 要件 1.4, 2.3, 4.4）。

**この階層は評価結果を表さない。** design.md「Error Strategy」が定める区分を、
上流の `catch_mechanism`（`src/catch_mechanism/errors.py`）と同じ形でここに固定する。

- **評価結果（違反の一覧）は値で返す。** 造形可能寸法の違反は上流の `BuildViolation`、
  床との隙間の違反は `ClearanceViolation`（後続タスクの `clearance.py`）であり、
  どちらも**全件を値として返す**。⚠️ **これらを例外にしてはならない。**
  最初の違反で打ち切る実装へ滑り、要件 4.4 の「該当する部位と不足量を示す」が
  1件ずつ直す往復に化ける。
- **呼び出し方の誤り・入力の不正・成立しない状態だけを例外にする。** パラメータ不正・
  形状不正・隙間不足・観測不足・整合不一致・形状環境不在の6系統を本モジュールが持つ。

`ChassisMechanismError` は `ValueError` を直接継承する基底例外である。6つの具象例外は
その派生であり、したがって `ValueError` の派生でもあるため、次のいずれでも捕捉できる
（tasks.md タスク 1.2 の観測可能な完了状態）。

- `except ChassisMechanismError` — 本パッケージ由来の失敗だけをまとめて捕捉する
- `except ValueError` — `chassis_mechanism` を知らない呼び出し側が既に書いている
  防御をそのまま働かせる

⚠️ **上流 `catch_mechanism` の例外階層を継承しない。また包み直さない**
（design.md `#### Errors` / 「Error Strategy」）。`ParameterError` / `GeometryError` /
`ConsistencyError` / `CadUnavailableError` の4つは**綴りが上流と衝突する**が、
両者は無関係な型である。上流の失敗は上流の型のまま伝播させること——包み直すと、
また継承させると、「上流 `dimensions.json` が壊れている」のか
「本 Spec の設定が壊れている」のかが同じ `except` に落ち、**どちらの設定を直せばよいかが
メッセージから消える**。本 Spec は上流の公開契約の唯一の消費者であり、この2つの設定を
同時に扱うため、区別が失われた時点で原因究明が総当たりになる。

6系統を独立したクラスに分けるのは、`cli` が系統ごとに違う終了コードを返すためである
（design.md「Error Categories and Responses」）。0 正常 / 1 検査の不成立
（`GeometryError`・`ClearanceError`・`MeasurementError`・`ConsistencyError`）/
2 入力不正（`ParameterError`）/ 3 形状生成の環境が無い（`CadUnavailableError`）。
⚠️ 系統の間に継承関係を作ると `except` の順序で終了コードが変わってしまうため、
6系統は**互いに素**に保つ。

⚠️ **違反の詳細を運ぶのは例外メッセージだけである。** 本モジュールは依存を持たない層
（design.md「Components and Interfaces」で Errors の Dependencies は「なし」、
「Dependency Direction」でも最左）であり、違反を表す値型（上流 `BuildViolation` /
本 Spec の `ClearanceViolation`）を import できない。したがって**部位名・軸・超過量・
不足量・欠けている観測項目名は、送出側が例外メッセージへ載せる**——上流と同じ方針である。
この方針の帰結として、本モジュールの例外クラスは詳細を運ぶ独自の `__init__` や属性を
**持たない**。持たせれば値型を受け取る署名へ滑り、依存の無い層という前提が崩れる。

本モジュールは標準ライブラリも自パッケージの他モジュールも import しない。とりわけ
形状ライブラリ（build123d）を import しないことは、形状環境を持たない実行環境でも
`CadUnavailableError` を送出できるために必要である——「ライブラリが無いことを報せる例外」
がそのライブラリを要求しては、その失敗を明示的に扱えない。
"""

from __future__ import annotations

__all__ = [
    "ChassisMechanismError",
    "ParameterError",
    "GeometryError",
    "ClearanceError",
    "MeasurementError",
    "ConsistencyError",
    "CadUnavailableError",
]


class ChassisMechanismError(ValueError):
    """`chassis_mechanism` が送出する失敗の基底。

    `ValueError` を直接継承するため、本パッケージを知らない呼び出し側の
    `except ValueError` による既存の防御をそのまま働かせられる。

    ⚠️ **上流の `CatchMechanismError` を継承しない。** 両者は互いに素な階層であり、
    上流の失敗はこの例外としては捕捉できない（本モジュール docstring 参照）。
    造形可能寸法や床との隙間の違反の一覧（本モジュール docstring の区分）は、
    この例外では表現しない。それらは値として返す。
    """


class ParameterError(ChassisMechanismError):
    """寸法パラメータ・設定ファイルの内容が不正な場合に送出する（要件 1.4）。

    未知の項目、必須の寸法値の欠損、物理的にあり得ない符号・範囲、出所の欠落など、
    値そのものが成立していないときに用いる。
    メッセージには**該当する項目名**（および値）を載せる——「設定が不正」とだけ
    言われても、どの項目を直せばよいかが分からなければ直せない。

    ⚠️ **上流 `dimensions.json` の不正はこの例外ではない。** それは
    `catch_mechanism.ParameterError` のまま伝播させる（design.md
    「Error Categories and Responses」が両者を別の行として持つ）。綴りは同じでも
    型が違うのは、直すべき設定ファイルが違うからである。
    """


class GeometryError(ChassisMechanismError):
    """形状が成立しない・生成物を出力できない場合に送出する（要件 2.3）。

    造形可能寸法を超える断片が残る場合、現実的な上限までに収まる分割数が存在しない
    場合、部品同士の干渉、および書き出しの失敗（`OSError` を包む）に用いる。
    いずれの場合も**生成物を出力しない**——部分的な成果物が出力先に残ることは、
    造形へ回せない形状を回せると誤認させるためである。

    ⚠️ 違反の内容を運ぶ上流の `BuildViolation` は本モジュールが import できない
    値型であるため、**部品名・超過している軸・超過量はメッセージへ載せる**
    （要件 2.3 の「超過している軸と超過量を示す」）。
    """


class ClearanceError(ChassisMechanismError):
    """床との隙間が下限値を下回る場合に送出する（要件 4.4）。

    ⚠️ **隙間の算出結果そのものはこの例外ではない。** 5部位の隙間と違反の一覧は
    `ClearanceViolation` の並びとして**全件が値で**返り、失敗として扱うかどうかは
    呼び出し側（`cli`）が決める。最初の違反で打ち切ると、1件直しては再実行する
    往復になり、要件 4.4 の「該当する部位と不足量を示す」が部分的にしか成立しない。

    この例外が表すのは、隙間の不足を**失敗として確定させた**状態である。
    メッセージには**該当する部位と不足量を全件**載せる（`ClearanceViolation` は
    `clearance.py` の値型であり、依存を持たない本モジュールは import しない）。
    """


class MeasurementError(ChassisMechanismError):
    """観測記録が不足している・記録どうしが整合しない場合に送出する。

    組立後の実測記録に必須の観測項目が欠けている、代表値が個々の測定値から導かれる
    規則（3個の平均）と一致しない、転動を伴わない測定なのに限界の注記が空である、
    といった場合に用いる。
    メッセージには**欠けている観測項目名**を載せる——組立の完了判定はこの記録に
    依存しており、どの観測が足りないかが分からなければ現物に戻れない。

    ⚠️ **観測の不足を既定値で埋めない。** 埋めた瞬間に、実測されていない値が
    実測値として下流へ流れる。
    """


class ConsistencyError(ChassisMechanismError):
    """記録された値と現在の値が食い違う場合に送出する。

    形状指標の記録に含まれるパラメータ識別子が現在の寸法設定ファイルの識別子と
    一致しない場合や、シミュレータ設定へ還元した値・出所が導出記録と一致しない
    場合に用いる。メッセージには**記録側と現在値の双方**（および参照元）を載せる。

    ⚠️ 上流 `catch_mechanism.ConsistencyError` とは別の型である。整合が壊れている
    のが上流の記録なのか本 Spec の記録なのかは、型でも区別できなければならない。
    """


class CadUnavailableError(ChassisMechanismError):
    """形状生成に必要な外部ライブラリが利用できない場合に送出する。

    ⚠️ **形状生成の要求を成功として黙って読み飛ばさない。** 生成したつもりで
    生成物が無い状態は、造形の直前まで気付けない事故になる。`cli` はこの例外を
    専用の終了コード 3 に対応させ、他の入力不正（終了コード 2）と区別する
    （design.md「Error Categories and Responses」の ⚠️「成功にしない」）。

    メッセージには**導入方法**を載せる。ライブラリは上流が導入済みの任意依存
    `cad` として宣言されており、既定ではインストールされないため、利用できない
    ことは異常ではなく既定の状態である。

    この失敗は寸法パラメータの利用を妨げない——形状を要さない読み込み・導出・
    隙間の算出・下流への提供は、この例外が送出される環境でも成立する。
    """
