"""値オブジェクトと、すべての判断が載る共通の形。

design.md「Components and Interfaces / L0-L1: 型・レイアウト・設定 / M1Types」、
tasks.md タスク 1.3、要件 1.8, 3.3, 4.4, 5.9, 6.9, 9.1。

本モジュールが持つのは「実測と判断が扱う値の形」だけであり、算出も判定も
しない。design.md が挙げる次の2つの一般化がここに現れる。

**すべての判断は `Judgement` という1つの形に載せる。** OQ-27 の可否も、
収束したかどうかも、誤差の帰属も、同じ形で返す。要点は
**「実測前に固定した規則の説明文」を判定値と同じ場所に持たせる**ことである
（要件 9.1）。規則が結果から離れた場所にあると、あとから規則のほうを結果に
合わせて読み替えられてしまう——本 Spec は「Pi 4 では不足」といった後戻り
できない判断を出すためのものなので、これは致命的である。

**すべての真値は `TruthValue` という1つの形に載せる。** 値だけでなく
**求め方と不確かさと測り方の記述**を必ず伴わせる（要件 4.4）。落下地点
「1200mm」とだけ書かれた真値は、±5mm で測ったのか ±100mm なのかが
分からず、そこから出した誤差が意味を持たない。

`prediction_core` の型（`Sample` / `Prediction` / `SourceKind` /
`ThrowRecord`）は**公開入口から参照し、再定義しない**（要件 13.1）。
`prediction_core` は層に属さない共通語彙であり、どの層からも参照してよい
（design.md「Dependency Direction」）。

本モジュールは L1 層であり、`errors`（L0）と標準ライブラリ、そして共通語彙
としての `prediction_core` だけを参照する。`layout` / `config` 以降を
import しない。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from m1_validation.errors import M1ConfigError
from prediction_core import Sample

#: `ThrowRecord.extra["m1"]` に本 Spec が書き込む拡張情報の形の版（要件 3.4）。
#: **この値の変更は下流（M2 / M3 / `trajectory-simulator`）の再検証を要求する**
#: （design.md「Revalidation Triggers / 本 Spec 由来」）。
M1_EXTRA_VERSION: str = "1.0"


def _require_non_empty(value: str, *, owner: str, field: str, why: str) -> None:
    """空文字列・空白のみの文字列を拒否する（`M1ConfigError`）。

    空白だけの文字列も拒否するのは、`" "` を入れれば通る抜け道を残すと
    **必須にした意味が失われる**からである。呼び出し方の誤りなので例外に
    する（本パッケージの `errors` モジュール docstring の方針）。

    Args:
        value: 検査する文字列。
        owner: 検査対象の型名（メッセージ用）。
        field: フィールド名（メッセージ用。`context` にも載せる）。
        why: なぜ空にできないのかの説明。
    """
    if value.strip():
        return
    raise M1ConfigError(
        f"{owner}.{field} を空にはできない: {why}",
        {"owner": owner, "field": field, "value": value},
    )


class TruthMethod(StrEnum):
    """真値の求め方（要件 4.4）。値と一緒に必ず記録する。

    どう求めた値かによって信頼度が違う。床にマークを付けてメジャーで測った
    落下地点と、観測点列から内挿した落下時刻と、観測開始より前へ外挿した
    リリース時刻は、同じ「真値」でも不確かさの性質が別物である。
    """

    MEASURED = "measured"
    # メジャー等による実測（要件 4.1）。

    INTERPOLATED = "interpolated"
    # 観測点の内挿（要件 4.2。床面高さを跨ぐ区間から落下時刻を求める）。

    EXTRAPOLATED = "extrapolated"
    # 推定軌道の外挿（要件 4.3。観測開始より前のリリース時刻を求める）。

    EXTERNAL_MARK = "external_mark"
    # 外部の合図による独立の値（要件 4.5。外挿値との突き合わせに使う）。

    MISSING = "missing"
    # 欠測（要件 4.6）。当該項目のみ欠測とし、他項目の集計は止めない。


class SampleReject(StrEnum):
    """継ぎ目が観測点をサンプルから除外した理由（要件 1.7）。

    理由ごとの件数を残すのは、**除外が多い投擲を「たまたま予測が悪い投擲」と
    取り違えない**ためである。
    """

    NOT_FINITE = "not_finite"
    # 座標が有限値でない（欠測）。

    BELOW_FLOOR = "below_floor"
    # 床面より下（設定した余裕を超えて負の高さ）。

    DEPTH_SPREAD_TOO_LARGE = "depth_spread_too_large"
    # 奥行きのばらつきが上限を超える（別物を掴んでいる疑い）。

    INSUFFICIENT_VALID_PIXELS = "insufficient_valid_pixels"
    # 有効画素数が下限に届かない。


class Attribution(StrEnum):
    """誤差の帰属先（要件 6.9, 6.10）。

    `UNDETERMINED` は失敗ではなく**正常な結果**である（要件 6.10）。
    決めきれないものを無理に1つの原因へ割り当てると、そのあと間違った側を
    直しにいくことになる。
    """

    CALIBRATION = "calibration"
    DETECTION = "detection"
    PREDICTION = "prediction"
    OBSERVATION_NOISE = "observation_noise"
    NONE = "none"
    # 有意な誤差が無い（帰属すべき原因が見当たらない）。

    UNDETERMINED = "undetermined"
    # 判別不能。無理に割り当てない（要件 6.10）。


class Oq27Verdict(StrEnum):
    """OQ-27（Pi 4 継続可否）の判定値（要件 9.1）。

    `DEFERRED` は「まだ判断できない」であり、`INSUFFICIENT`（不足と判断した）
    とは別物である。試行数下限に届かない・改善項目が未適用といった状態で
    「不足」を出さないための区別である（要件 9.3）。
    """

    CONTINUE = "continue"
    CONTINUE_WITH_CONSTRAINTS = "continue_with_constraints"
    INSUFFICIENT = "insufficient"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class TruthValue:
    """真値。値だけでなく求め方と不確かさを必ず伴う（要件 4.4）。

    Attributes:
        value: 真値。位置なら World 座標 mm の3つ組、時刻なら ms のスカラ。
            欠測なら `None`（`method` も `MISSING` になる。要件 4.6）。
        method: 求め方（実測 / 内挿 / 外挿 / 外部の合図 / 欠測）。
        uncertainty_mm: 位置の真値の不確かさ（mm）。該当しなければ `None`。
        uncertainty_ms: 時刻の真値の不確かさ（ms）。該当しなければ `None`。
        source: **測り方の記述。空にできない**（要件 4.1）。

    Invariants:
        `source` は空文字列・空白のみを許さない（構築時に拒否する）。
    """

    value: float | tuple[float, float, float] | None
    method: TruthMethod
    uncertainty_mm: float | None
    uncertainty_ms: float | None
    source: str

    def __post_init__(self) -> None:
        _require_non_empty(
            self.source,
            owner="TruthValue",
            field="source",
            why=(
                "どう測った値かが分からない真値からは誤差の意味を読み取れない"
                "（要件 4.1）"
            ),
        )


@dataclass(frozen=True, slots=True)
class ThrowTruth:
    """1投擲ぶんの真値一式（design.md「TruthDeriver」Contracts、要件 4.1-4.5）。

    3つの真値を**同じ形（`TruthValue`）で、しかし別々の求め方で**持つ。
    落下地点は人が測った実測、落下時刻は観測点列の内挿、リリース時刻は
    推定軌道の外挿であり、**不確かさの性質がそれぞれ違う**。1つにまとめて
    「真値」と呼ぶと、この違いが消える。

    3つは**独立に欠測しうる**（要件 4.6）。落下地点が未記入でも落下時刻は
    求まるし、床面を跨ぐ区間が無くてもリリース時刻は求まる。欠測した項目を
    必要とする実測項目だけが欠測になり、他の集計は止まらない。

    Attributes:
        record_id: どの投擲の真値か。`ThrowRecord.record_id` と対応する。
        impact_point_world_mm: 実際の落下地点（World 座標 mm。要件 4.1）。
            外部から与えられる実測値であり、**測り方の記述が必須**である。
        impact_time_ms: 実際の落下時刻（ms。要件 4.2）。観測サンプル列が
            床面高さを跨ぐ隣接2点の内挿。
        release_time_ms: リリース時刻（ms。要件 4.3）。推定軌道を観測開始
            より前へ外挿し、レイアウトのリリース高さに達する時刻。
        external_mark_delta_ms: 外部の合図と外挿値の差（ms。要件 4.5）。
            合図が記録されていない、または外挿が欠測なら `None`。
            **0 で埋めない**——「差が 0 だった」と「突き合わせていない」は
            別である。
    """

    record_id: str
    impact_point_world_mm: TruthValue
    impact_time_ms: TruthValue
    release_time_ms: TruthValue
    external_mark_delta_ms: float | None


@dataclass(frozen=True, slots=True)
class SampleProvenance:
    """`Sample` に入れられない観測品質（要件 1.8）。

    `prediction_core.Sample` は時刻と3次元位置だけを持つ——予測はデバイス
    固有の情報に依存してはならないからである。しかし**誤差の帰属には
    観測品質が要る**（「その予測が外れたのは観測が悪かったからか」を分ける）。
    そこで品質情報を別の列として持ち、`ThrowSamples` が
    **`samples` と同じ順序・同じ長さで**保持する。

    Attributes:
        frame_index: 元になったフレームのセッション内通し番号。
        frame_seq: 元になったフレームのデバイス側連番。
        valid_depth_px: 有効だった Depth 画素数（要件 1.8）。
        depth_spread_mm: 奥行きのばらつき（mm。要件 1.8）。
        apparent_diameter_px: 観測された見かけの直径（px）。
        expected_diameter_px: 距離と対象寸法から期待される直径（px）。
        rivals: 同時に存在した競合候補の数（要件 1.8）。
        gap_before: 直前のフレーム欠落数（要件 1.8。0 なら欠落なし）。
        camera_ray_unit: **World 系で表したカメラ視線方向**の単位ベクトル
            （要件 6.3）。誤差の共通偏りが World 固定の方向を向いているのか
            カメラ視線方向を向いているのかを分けるために使う——前者は
            キャリブレーション、後者は観測・検出を疑う手がかりになる。
    """

    frame_index: int
    frame_seq: int
    valid_depth_px: int
    depth_spread_mm: float
    apparent_diameter_px: float
    expected_diameter_px: float
    rivals: int
    gap_before: int
    camera_ray_unit: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class ThrowSamples:
    """継ぎ目の出力（要件 1.1 / 1.8）。

    Attributes:
        samples: World 座標系のサンプル列（`prediction_core.Sample`）。
            そのまま予測へ渡せる形である。
        provenance: `samples` と**同じ順序・同じ長さ**の観測品質列。
        rejected: 除外理由とその件数の対（要件 1.7）。
        handoff_version: 追跡側の受け渡し形式版（要件 1.5 の照合に使った値）。
        calibration_id: 使用したキャリブレーション結果の識別子（要件 2.3）。
        verification_state: その検証状態（要件 2.3）。
        verified: 検証を通過していたか。`False` なら生成物すべてに
            未検証の印が付き、レポートに「誤差の帰属ができない」旨が出る
            （要件 2.2）。

    Preconditions:
        `len(samples) == len(provenance)`。

        **この長さ一致は構築時に検証しない**（design.md「M1Types」
        Validation。上流3 Spec と同じ「生成時に検証しない。検証は各サービスの
        境界で行う」方針）。検査の持ち主は本型を組み立てる継ぎ目
        （`seam.py`、タスク 2.2）である。

        添字で対応させる設計は壊れやすい（design.md「M1Types」Risks）。
        2つの列を突き合わせるときは **`zip(..., strict=True)` で辿ること**
        ——既定の `zip()` は黙って短い方へ切り詰めるため、**別の観測点の
        品質情報がサンプルに付いたまま集計へ流れる**。
    """

    samples: tuple[Sample, ...]
    provenance: tuple[SampleProvenance, ...]
    rejected: tuple[tuple[SampleReject, int], ...]
    handoff_version: str
    calibration_id: str
    verification_state: str
    verified: bool


@dataclass(frozen=True, slots=True)
class Judgement:
    """すべての判断が載る共通の形（要件 5.9 / 6.9 / 9.1）。

    Attributes:
        question: 何についての判断か（例 `"OQ-27"` / `"convergence"` /
            `"attribution"`）。
        criterion: **実測前に固定した規則の説明文。空にできない**（要件 9.1）。
        verdict: 判定値。語彙は `question` ごとに違う（OQ-27 なら
            `Oq27Verdict`、帰属なら `Attribution`）ため、型としては `str` で
            受ける——1つの形にすべての判断を載せるための意図的な選択である。
        rationale: なぜその判定になったのかの説明。
        evidence: 判定の根拠になった数値（代表値・ばらつき・試行数など。
            要件 5.9）。渡されたマッピングは**複製して**保持する。
        provisional: 暫定の印。試行数下限未達・実機由来の投擲なし・改善項目
            未適用のときに立て、**判断に用いてよい状態ではない**ことを示す
            （要件 5.10 / 9.3）。

    Invariants:
        `criterion` は空文字列・空白のみを許さない（構築時に拒否する）。
    """

    question: str
    criterion: str
    verdict: str
    rationale: str
    evidence: Mapping[str, object]
    provisional: bool

    def __post_init__(self) -> None:
        _require_non_empty(
            self.criterion,
            owner="Judgement",
            field="criterion",
            why=(
                "判定値だけが残って規則が失われると、あとから規則のほうを"
                "結果に合わせて読み替えられる（要件 9.1）"
            ),
        )
        # 判断は「その時点の根拠でそう決めた」という記録である。呼び出し側が
        # 使い回す辞書をそのまま抱えると、レポートの証跡が判断と食い違い得る。
        object.__setattr__(self, "evidence", dict(self.evidence))
