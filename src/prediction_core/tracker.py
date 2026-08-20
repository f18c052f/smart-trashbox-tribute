"""投擲1回分の観測を蓄積し、逐次予測を行う唯一の状態コンポーネント（要件 4.1-4.3 / 5.1-5.4）。

本モジュールは L6 層であり、design.md「Dependency Direction」表のとおり
0〜5層（`units` / `errors` / `types` / `config` / `fitting` / `impact` /
`predictor` / `record`）を import してよい。これより内側に状態を持つ層は
存在せず、これより上位の層は `__init__.py`（L7、再エクスポート専用）のみ
である。

**本 Spec で唯一状態を持つコンポーネント**（design.md「ThrowPredictionTracker /
Responsibilities & Constraints」）。`Predictor` 以下（`predict()` /
`fit_trajectory` / `solve_floor_impact`)はすべて無状態の純関数であり、
本モジュールがその上に薄い状態レイヤを1枚だけ被せる（design.md「Selected
pattern: 純関数コア + 薄い状態レイヤ」）。

**毎回全観測点から再計算する**（design.md Implementation Notes）:
`add_sample` はスライディングウィンドウやインクリメンタルな部分和を保持
せず、そのつど `self._samples` の全件を `predict()` に渡す。バッチ計算
（`record.replay`）と逐次計算（本モジュール）の2つの経路を作らないため、
両者は最終的に同じ `predict()` 1つへ収束する。

**検証は行わない**（design.md Implementation Notes「Validation」）:
`add_sample` は要素の妥当性（`Sample` 型であるか、フィールドが有限か等）
を自ら検査しない。検査は `predict()` の境界に一元化されており、判定
ロジックを二重に持たない。

**駆動制御・目標座標の送信を行わない**（要件 5.4）: 本クラスの公開
インターフェースは `add_sample` / `samples` / `predictions` / `latest` /
`first_valid` / `to_record` のみであり、モータ駆動や座標送信に関する
メソッドを持たない。

**record_id の空文字列検証について**（design.md「Preconditions: record_id
は空文字列でないこと」)。本タスクでは、この事前条件を `__init__` で
即座に `ValueError` として拒否する方針を採用した。根拠:

- design.md はこれを Precondition として明記しており、Precondition は
  「呼び出し側が満たすべき契約」である。契約違反を静かに許容すると、
  空の `record_id` を持つ `ThrowRecord` が後段（永続化・可視化）まで
  伝播し、そこで初めて気づく事態になりかねない（Fail Fast）。
- `errors.py`（L0）の3分類（予測無効=値/呼び出し方の誤り=例外/記録の
  不整合=例外）に照らすと、空文字列の `record_id` は「予測が構造的に
  成立しない」ケースではなく「呼び出し方の誤り」に属する。この分類の
  先例である `PredictionConfigError`（`min_samples < 3` など、
  `PredictionConfig.__post_init__` が構築時に例外で拒否する)と同型の
  判断である。
- ただし「新しい例外クラスを新設しない」という制約（1.3 の申し送り、
  `errors.py` は4種で固定）があるため、`PredictionCoreError` 系の新種は
  作らず、素の `ValueError` を送出するにとどめる。`errors.py` が定義
  する3種の例外もすべて `ValueError` を継承しており、本モジュールが
  `errors.py` を経由しない `ValueError` を送出することは、利用側が
  既に持つ `except ValueError` の防御網と矛盾しない。
"""

from __future__ import annotations

from prediction_core.config import PredictionConfig
from prediction_core.predictor import predict
from prediction_core.record import ThrowRecord
from prediction_core.types import Prediction, PredictionOutcome, Sample, SourceKind

__all__ = ["ThrowPredictionTracker"]


class ThrowPredictionTracker:
    """投擲1回分の観測サンプルを逐次蓄積し、そのつど予測系列を更新する。

    1投擲 = 1インスタンスで、単一スレッドから使う前提であり、スレッド
    安全性は提供しない（design.md「State Management / Concurrency
    strategy」）。
    """

    def __init__(
        self,
        *,
        record_id: str,
        source: SourceKind,
        config: PredictionConfig | None = None,
    ) -> None:
        """トラッカーを初期化する。

        Args:
            record_id: 投擲の識別子。空文字列は `ValueError` で拒否する
                （モジュール docstring の「record_id の空文字列検証に
                ついて」を参照）。
            source: 観測サンプル列の入力元。
            config: この投擲の予測に使用するパラメータ。省略時は
                既定の `PredictionConfig()` を用いる。

        Raises:
            ValueError: `record_id` が空文字列の場合。
        """
        if record_id == "":
            raise ValueError(
                "record_id は空文字列であってはならない"
                "（design.md ThrowPredictionTracker の Precondition）。"
            )

        self._record_id = record_id
        self._source = source
        self._config = config if config is not None else PredictionConfig()
        self._samples: list[Sample] = []
        self._predictions: list[PredictionOutcome] = []

    def add_sample(self, sample: Sample) -> PredictionOutcome:
        """観測サンプルを1点追加し、蓄積済みの全点で予測をやり直す。

        最小サンプル数（`config.min_samples`)未満の間も常に
        `PredictionOutcome`（この場合は `InsufficientSamples` 理由の
        `InvalidPrediction`)を返し、常に予測系列へ追加する
        （design.md「`add_sample()` は常に `PredictionOutcome` を返し、
        常に予測系列へ追加する」）。

        要素の妥当性検証は行わない。検証は `predict()` の境界に一元化
        されている（design.md Implementation Notes「Validation」）。

        Returns:
            この呼び出しによって生成された `PredictionOutcome`。
            `self.predictions[-1]` と同一のオブジェクトである
            （design.md「Postconditions」）。
        """
        self._samples.append(sample)
        # 毎回全観測点から再計算する。スライディングウィンドウや部分和は
        # 保持しない（design.md「毎回全観測点から再計算する」）。
        outcome = predict(tuple(self._samples), self._config)
        self._predictions.append(outcome)
        return outcome

    @property
    def samples(self) -> tuple[Sample, ...]:
        """これまでに追加された観測サンプル系列（追加順）。

        `tuple` として公開し、外部からの変更が内部状態へ影響しないよう
        にする（design.md Implementation Notes「Integration」）。
        """
        return tuple(self._samples)

    @property
    def predictions(self) -> tuple[PredictionOutcome, ...]:
        """これまでに生成された予測系列（生成順）。

        `len(self.predictions) == len(self.samples)` が常に成立する
        （design.md「Postconditions」、要件 5.2）。`tuple` として公開する
        理由は `samples` と同様。
        """
        return tuple(self._predictions)

    @property
    def latest(self) -> PredictionOutcome | None:
        """直近の `add_sample` が返した `PredictionOutcome`。

        まだ1件も追加されていない場合は `None`。
        """
        if not self._predictions:
            return None
        return self._predictions[-1]

    @property
    def first_valid(self) -> Prediction | None:
        """予測系列中で最初に得られた有効な `Prediction`。

        まだ有効な予測が1件も無ければ `None`（design.md「Postconditions」、
        要件 4.1）。専用のフラグや別経路を設けず、`predictions` を
        先頭から走査して最初の `Prediction` インスタンスを返すだけである
        （design.md「初回予測は系列上で最初の `Prediction` として特定
        できる。専用の経路・フラグを設けない」）。
        """
        for outcome in self._predictions:
            if isinstance(outcome, Prediction):
                return outcome
        return None

    def to_record(self) -> ThrowRecord:
        """現在の蓄積内容から `ThrowRecord` のスナップショットを生成する。

        永続化は行わない。`ThrowRecord` は不変の値オブジェクトであり、
        以降 `self` へ追加してもこの戻り値は影響を受けない
        （design.md「State Management / Persistence & consistency」）。
        """
        return ThrowRecord(
            record_id=self._record_id,
            source=self._source,
            config=self._config,
            samples=self.samples,
            predictions=self.predictions,
        )
