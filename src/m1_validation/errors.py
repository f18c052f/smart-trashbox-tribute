"""m1-prediction-validation が送出する例外階層と失敗理由の列挙。

design.md「Error Handling」/「Components and Interfaces / Errors」、
tasks.md タスク 1.2、要件 1.4, 1.5, 1.6, 2.1。

**なぜ「継ぎ目の不成立」だけを例外にするのか。**

上流4パッケージは「**無効は値、例外は呼び出し方の誤り**」という方針を採る。
`prediction_core` は予測が構造的に成立しない場合でも例外にせず
`InvalidPrediction` を返す——予測が出ないことは正常系の一部だからである。
本 Spec もこの方針を**基本的には踏襲する**。有効サンプルが0件だった投擲、
真値が測れなかった項目、試行数が下限に届かない判断は、いずれも
**値として扱い**、理由を付けて記録し集計から外すだけである。実験は続く。

ただし**継ぎ目の不成立だけは例外にする**。継ぎ目（カメラ座標系の点列 →
World 座標系の予測入力）で座標系・形式版・ストリーム設定が食い違ったまま
値が下流へ流れることは、**本 Spec が防ごうとしている事故そのもの**だから
である。この食い違いは症状が「予測が悪い」としか見えず、得られた誤差が
系統誤差なのか予測誤差なのかを事後に分離できない。値として返して呼び出し側が
確認を怠れば、**間違った座標のサンプルが誰にも気づかれずに実測データへ
入り込み、その投擲群は丸ごと使えなくなる**。したがってここだけは、
呼び出し側が戻り値の確認を怠っても処理が止まる形にする。

「拒否を例外にすると実験の最中に落ちる」という懸念に対しては、
**継ぎ目を投擲の前に評価する**という実行順序で応える（design.md
「System Flows / 1投擲の実行」、Errors の Risks）。投擲を1回無駄にする
のではなく、投げる前に止める。

失敗は次の3階層で表す。

- `M1ValidationError`: 本パッケージ由来の失敗を一括捕捉する基底
- `M1ConfigError`: 設定・入力の誤り。**起動時**に拒否する
- `SeamFailure`: 継ぎ目の不成立。`reason`（`FailureReason`）を伴う

`FailureReason` は `StrEnum` として定義する。呼び出し側は
`except SeamFailure` で捕捉したあと、本モジュールの列挙型を import せずに
`failure.reason == "frame_mismatch"` という**文字列比較だけで分岐できる**
（tasks.md 1.2 の観測可能な完了状態）。

**`FailureReason` は例外専用の語彙ではない。** 9件のうち例外として送出
されるのは継ぎ目の不成立に当たるものだけであり、`NO_VALID_SAMPLE` /
`TRUTH_MISSING` / `INSUFFICIENT_TRIALS` は**値として運ばれる**（失敗投擲の
記録・欠測項目・保留の判断が同じ語彙で理由を書けるようにするための共有語彙
である。各メンバのコメントに区分を記した）。

本モジュールは L0 層であり、標準ライブラリ以外も自パッケージの他モジュールも
import しない（design.md「Dependency Direction」の層表）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class FailureReason(StrEnum):
    """本 Spec の失敗を識別する理由（design.md「Errors」/ 要件 1.4, 1.5, 1.6, 2.1）。

    値は下流が文字列比較で分岐する契約である。**値の変更・削除は下流の
    再検証を要する**（design.md「Revalidation Triggers」）。

    区分は design.md「Error Categories and Responses」に従う。
    `[例外]` は `SeamFailure` として送出されるもの、`[値]` は失敗投擲の
    記録・欠測・保留として運ばれるものである。
    """

    # --- 継ぎ目の不成立（[例外] `SeamFailure`。投擲を始める前に評価する）---

    FRAME_MISMATCH = "frame_mismatch"
    # [例外] 点列の座標系がカメラ座標系でない（要件 1.4）。

    UNKNOWN_HANDOFF_VERSION = "unknown_handoff_version"
    # [例外] 追跡側の受け渡し形式版が既知でない。内容を推測して読まない（要件 1.5）。

    UNKNOWN_CALIBRATION_VERSION = "unknown_calibration_version"
    # [例外] キャリブレーション結果の形式版が既知でない（要件 1.5）。

    PROFILE_MISMATCH = "profile_mismatch"
    # [例外] 記録時のストリーム設定・内部パラメータが実行時のそれと食い違う（要件 1.6）。

    CALIBRATION_NOT_VERIFIED = "calibration_not_verified"
    # [例外] キャリブレーション結果が検証を通過していない（要件 2.1）。
    #        明示的に許可された場合のみ、印を付けたうえで実行を続ける（要件 2.2）。

    UNKNOWN_RECORD_SCHEMA = "unknown_record_schema"
    # [例外] Throw Record の記録スキーマ版が予測コアの定義値と一致しない。
    #        design.md の分類表では「未知の形式版」に当たる。

    # --- 観測・真値・判断の不成立（[値]。理由を付けて記録し、実験は続ける）---

    NO_VALID_SAMPLE = "no_valid_sample"
    # [値] 有効サンプルが0件（追跡不成立を含む）。失敗投擲として記録し集計から除く。

    TRUTH_MISSING = "truth_missing"
    # [値] 真値が測れなかった。当該項目のみ欠測とし、他項目の集計は継続する。

    INSUFFICIENT_TRIALS = "insufficient_trials"
    # [値] 試行数が下限に届かない。判断を `deferred` / `provisional` として返す。


class M1ValidationError(Exception):
    """m1_validation が送出するすべての例外の基底。

    呼び出し側はこれを捕捉することで、本パッケージ由来の失敗をひとまとめに
    扱える（tasks.md 1.2 の観測可能な完了状態）。

    すべての例外が `context` を持つ。「閾値が不正」「設定が食い違う」とだけ
    言われても、**どの値がどの基準を外れたのか**が分からなければ直せない
    ——design.md「Errors」が「例外には実測値と判定に用いた基準を載せる」と
    定めるのはこのためである。

    Attributes:
        detail: 人が読める失敗の説明。理由の接頭辞を含まない生の文である
            （サブクラスが表示用に整形しても、ここは材料のまま残る）。
        context: 実測値と判定に用いた基準を保持するマッピング
            （例 `{"trials": 7, "minimum_trials": 20}`）。渡された
            マッピングは**複製して**保持する——例外が運ぶのはその時点で
            観測された事実であり、呼び出し側が使い回す辞書を書き換えても
            変わってはならない。未指定なら空のマッピングであり `None`
            にはしない。
    """

    def __init__(
        self,
        detail: str,
        context: Mapping[str, object] | None = None,
        *,
        message: str | None = None,
    ) -> None:
        """例外を組み立てる。

        Args:
            detail: 人が読める失敗の説明。
            context: 実測値と判定基準。省略時は空のマッピングになる。
            message: `str(exc)` として見える文字列。省略時は `detail` を使う。
                理由を前置したいサブクラス（`SeamFailure`）が指定する。
        """
        super().__init__(detail if message is None else message)
        self.detail = detail
        self.context: Mapping[str, object] = dict(context) if context is not None else {}


class M1ConfigError(M1ValidationError):
    """設定・入力の誤り。**起動時**に拒否する（design.md「Error Categories」）。

    不正な閾値、レイアウト未指定、範囲外の値など、実行を始める前に直すべき
    誤りを表す。継ぎ目が成立するかどうかの判断には至っていない段階の失敗
    であり、`SeamFailure` とは区別する（両者は対処が違う——こちらは設定を
    直す、あちらは実験の前提を整え直す）。
    """


class SeamFailure(M1ValidationError):
    """継ぎ目が成立しない条件（本モジュール docstring「なぜ例外にするのか」）。

    座標系・形式版・ストリーム設定・検証状態のいずれかが食い違ったまま値を
    下流へ流さないために、サンプルを生成せず必ずこの例外を送出する
    （要件 1.4, 1.5, 1.6, 2.1）。部分的な結果や既定値で埋めたサンプル列を
    返す代わりに、処理そのものを止める。

    Attributes:
        reason: 失敗理由。`FailureReason` の値であり、`StrEnum` なので
            `failure.reason == "frame_mismatch"` と文字列比較できる。
        detail: 人が読める失敗の説明（基底クラス参照）。
        context: 実測値と判定に用いた基準（基底クラス参照）。
    """

    def __init__(
        self,
        reason: FailureReason,
        detail: str,
        context: Mapping[str, object] | None = None,
    ) -> None:
        """継ぎ目の不成立を組み立てる。

        Args:
            reason: 失敗理由。
            detail: 人が読める失敗の説明。
            context: 実測値と判定基準（例 `{"expected_fps": 60, "actual_fps": 30}`）。
        """
        super().__init__(detail, context, message=f"{reason}: {detail}")
        self.reason: FailureReason = reason
