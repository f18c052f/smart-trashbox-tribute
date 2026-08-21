"""sensing-foundation が送出する例外階層（design.md「Error Handling」）。

`prediction_core` の「無効は値、例外は呼び出し方の誤り」という区分を踏襲しつつ、
外部デバイス・ファイル I/O が絡む本パッケージでは区分を1つ増やす。

===================  ==========================  ================================
区分                 扱い                        例
===================  ==========================  ================================
呼び出し方の誤り     例外（起動時に失敗させる）  不正な設定値、`recorded` なのに
                                                  セッション未指定、必要 RAM 超過
環境が要求を         例外（明示的な型で）        SDK 未導入、デバイス未接続、
満たさない                                       要求モードを開けない、形式版不一致
**観測された事実**   **値として返す**            フレーム破棄・欠落、USB2 接続、
                     （例外にしない）             メタデータ欠測、ログ破棄、
                                                  書き込み失敗（連続失敗が上限に
                                                  達するまでは計数のみ）
===================  ==========================  ================================

**「観測された事実」を例外にしないのが本モジュールの要点である。** フレームの
破棄・欠落、USB2 接続、メタデータ欠測、ログ破棄、書き込み失敗は異常ではなく
**計測対象そのもの**であり、これらを例外にすると取得ループが止まって計測が
成立しなくなる。呼び出し側はこれらをカウンタ（`frames_dropped` /
`frames_missing` / `write_errors` などの要約値）として受け取り、この
モジュールの例外はいずれも送出しない。この階層が表すのはあくまで
「呼び出し方の誤り」（構築時点で不正な設定など）と「環境が要求を満たさない」
（SDK 未導入・デバイス未接続・記録形式が扱えない、など）の2区分のみである。

`SensingFoundationError` はパッケージ由来の失敗を一括捕捉するための入口であり、
それ自体は `ValueError` を継承しない。一方、「呼び出し方の誤り」を表す
`SensingConfigError` は `ValueError` も継承する。本パッケージを知らない
利用側が既に書いている `except ValueError` による防御をそのまま働かせる
ためである。

`SourceUnavailableError` は必ず「次に何を確認すべきか」をメッセージに含めて
送出すること。例:

    raise SourceUnavailableError(
        "pyrealsense2 を import できない。`doctor` の sdk_import を確認せよ"
    )

このモジュール自体は例外クラスの定義のみを行い、実際の送出（メッセージへの
案内文の付与を含む）は各アダプタ・記録実装の責務である。
"""

from __future__ import annotations


class SensingFoundationError(Exception):
    """sensing_foundation が送出するすべての例外の基底。

    利用側はこれを捕捉することで、本パッケージ由来の失敗だけを
    まとめて扱える。フレーム破棄・欠落・書き込み失敗などの
    「観測された事実」はこの例外では表現されない（値として返る）。
    """


class SensingConfigError(SensingFoundationError, ValueError):
    """設定値が不変条件を満たさない（呼び出し方の誤り）。

    不正な設定値、`recorded` 入力元なのにセッションが未指定、
    必要 RAM がリングバッファの上限を超える、といった構築時点で
    成立しない設定を検出した際に送出する。`ValueError` を継承するため、
    本パッケージを知らない既存の `except ValueError` でも捕捉できる。
    """


class SourceUnavailableError(SensingFoundationError):
    """SDK が未導入、またはデバイスが接続されていない（環境が要求を満たさない）。

    **送出時のメッセージには必ず「次に何を確認すべきか」を含めること。**
    例: 「pyrealsense2 を import できない。`doctor` の sdk_import を確認せよ」。
    利用側がこの例外を見た時点で次のアクションへ進めるようにするための
    このモジュールの要点である。
    """


class DeviceNotReadyError(SourceUnavailableError):
    """デバイスは認識できたが、要求したモードを開けない。

    `SourceUnavailableError` の一種として捕捉できる。SDK 自体は利用でき
    デバイスも認識しているが、要求した解像度・フレームレートなどの
    モードでストリームを開始できない場合に送出する。
    """


class SourceContractError(SensingFoundationError):
    """入力元アダプタが取得インターフェースの契約に違反した。

    live / recorded / simulated のいずれかのアダプタが、共通の取得
    インターフェースが定める契約（フレーム系列の形・単調性など）を
    破った場合に送出する。呼び出し側の誤りではなくアダプタ実装側の
    不整合を表す。
    """


class RecordingFormatError(SensingFoundationError):
    """記録の索引・ブロブが整合しない（環境が要求を満たさない）。

    索引行の欠損、オフセットの不整合など、記録ファイルの内容が
    想定した形式を満たさない場合に送出する。無効なフレームを
    正常なフレームとして返してはならない。
    """


class RecordingVersionError(RecordingFormatError):
    """記録の形式版が未知、または現在扱える版と異なる。

    `RecordingFormatError` の一種として捕捉できる。`format_version` が
    未知の値であるとき、内容を推測して読み替えず、この例外で
    識別可能な形に報告する。
    """


class RecordingWriteError(SensingFoundationError):
    """記録の書き込みが連続失敗の上限（既定 100 回）を超えた。

    書き込み失敗そのものは「観測された事実」として計数するのみで
    例外にしないが、連続失敗が上限に達した場合に限り記録処理を
    止めるためにこの例外を送出する。**フレーム取得自体は継続する。**
    """


class ThrowRecordFormatError(SensingFoundationError):
    """Throw Record NDJSON の1行が壊れている（環境が要求を満たさない）。

    `ThrowRecordStore.iter_records()`（破損行で停止する簡潔な経路）が、
    JSON として解析できない行、または `prediction_core.ThrowRecord` の
    スキーマ検証（`RecordSchemaError`）に失敗した行に到達した際に送出する。
    `SessionReader` が索引行の破損に対して `RecordingFormatError` を送出する
    のと対になる、Throw Record（サンプル層）側の対応物である。

    頑健に読み進めたい呼び出し側は、この例外で止まる `iter_records()` では
    なく `ThrowRecordStore.iter_with_issues()` を使い、`ThrowRecordReadIssue`
    として行ごとに報告を受け取ること（要件 7.5）。
    """


class ThrowRecordVersionError(ThrowRecordFormatError):
    """Throw Record の `schema_version` が `prediction_core.SCHEMA_VERSION` と異なる。

    `ThrowRecordFormatError` の一種として捕捉できる。内容を推測して読み替える
    ことはせず、この例外で識別可能な形に報告する（要件 7.6）。
    """
