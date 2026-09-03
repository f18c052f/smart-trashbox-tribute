"""ゴミ箱の選定基準と候補判定（design.md「Selection」/ 要件 6.1, 6.2, 6.3, 6.4）。

**基準の正は `.kiro/steering/roadmap.md`「ゴミ箱の選定基準（購入前に確定させる）」で
あり、本モジュールはそれを機械可読なしきい値へ写したものにすぎない。**
⚠️ **推測で基準を書き換えない**（2026-08-30 の実売調査で確定済み。design.md
「Selection」Responsibilities）。しきい値は `configs/catch_mechanism/
selection-criteria.json` に、候補の諸元は `candidates.json` に置き、コードへ数値を
埋め込まない——「百均で適当に買う」を許さないための基準が、コードを読まなければ
分からない場所にあっては意味がない。転記が roadmap からずれていないことは
`tests/catch_mechanism/test_catch_selection.py` が roadmap 本文と突き合わせて固定する。

## 転記表（roadmap → `selection-criteria.json`）

| roadmap の記載 | しきい値 |
|---|---|
| 円形（ほぼ必須） | `shape = "round"` |
| 最低 φ200 | `opening_inner_diameter_min_mm = 200.0` |
| φ180 未満は不可 | `opening_inner_diameter_reject_below_mm = 180.0` |
| φ215〜225（実質これが上限） | `opening_inner_diameter_typical_max_mm = 225.0` |
| 高さ 200〜300mm | `height_min_mm = 200.0` / `height_max_mm = 300.0` |
| 重量 300g 以下 | `mass_max_g = 300.0` |
| テーパーは緩いものを選ぶ | `taper_max_deg = 8.5`（下記の導出） |
| 110円で足りる。220円以上へ上げない | `price_max_jpy = 110` |
| フタなしを選ぶ | `requires_lidless = true` |
| 外向きリムがあるものが望ましい | `prefers_outward_rim = true` |

⚠️ **テーパー上限は roadmap に数値が無い。** roadmap は上限を**2つの例**で述べる
（上φ220→底φ158 の「片側 約7°」は可、上φ225→底φ145 の「約10°」は強すぎる）。
前者を実寸から起こすと `atan((220−158)/2 / 244) = 7.24°` であり、後者は 10.0° で
ある。上限はこの2つを分けねばならず、かつどちらにも接していてはならない
（接していると、例の側がわずかに動くだけで判定が反転する）。両者の中点
`(7.24 + 10.0)/2 = 8.62°` を保守側へ丸めた **8.5°** を採る。7.24° に対して +1.26°、
10.0° に対して −1.5° の余裕がある。

⚠️ **`price_max_jpy` は 110 である。** roadmap は「110円で足りる。220円以上へ
上げない」と述べ、実態欄で「5〜7L 帯は4チェーンとも110円で φ215〜225 を満たす」
と裏付けている。したがって 110 を上限に置いても取りこぼす品は無く、
「220円以上へ上げない」という禁止も自動的に満たされる。中間帯（165円）を落とす
方向の**保守側**の転記である。

## 開口内径の2つのしきい値

`opening_inner_diameter_min_mm`（φ200）が判定に効く下限であり、
`opening_inner_diameter_reject_below_mm`（φ180）は roadmap が「不可」と言い切った
**絶対の床**である。後者は候補ではなく**基準の側**を縛る不変条件
（`reject_below <= min <= typical_max`）として働き、将来「下限を φ190 へ緩める」と
いった変更が入ったときに構築時点で拒否する。⚠️ 判定側で両方を評価して
`failed_items` へ2件積むことはしない——同じ項目名が2回並ぶ一覧は「同じ項目が
二重に落ちた」と読めてしまい、要件 6.2 の「不適合であった項目名を示す」を濁らせる。

`opening_inner_diameter_typical_max_mm`（φ225）は**不適合の条件ではない**。開口が
広いことは位置許容誤差の上で有利であり落とす理由が無い。しかし roadmap が
「100均の丸型は φ215〜225 で頭打ちであり φ240 以上は存在しない」と述べる以上、
これを超える値は**外径を内径として転記した疑い**があるため警告として示す。

## 判定の規律

- **全項目を評価し、最初の不適合で打ち切らない**（design.md「Selection」
  Responsibilities）。⚠️ 打ち切る実装は「不適合である」ことだけを伝え、
  **何をどれだけ直せば候補になるのか**を伝えない。買い物の前に知りたいのは後者である
- **不適合は例外ではなく値で返す**（`errors.py` docstring の区分）。
  `CandidateVerdict.accepted = False` と `failed_items` の一覧が正常系の結果である
- **望ましいが必須でない項目は `warnings` にのみ現れ、`accepted` を左右しない**
  （design.md「Selection」Invariants）。現在 `warnings` に載るのは3種類で、
  いずれも「落とす理由にはならないが、人が見るべき」ものである
  - `has_outward_rim` — 外向きリムが無い／不明。無い場合は挟み込み式になり設計が増える
  - `mass_g` — 重量が未記録。要件 6.7 により未採寸でも判定を完了させる
  - `opening_inner_diameter_mm` — 実売の実質上限を超える（転記の疑い、上記）

## 送出する例外は `SelectionError` である

⚠️ `ParameterError` ではない。`errors.py` の `SelectionError` docstring が
「選定基準の設定ファイルが未知の項目名を挙げている、候補の諸元に必須項目が
欠けている」をこの型に割り当てている。`ParameterError` が受け持つのは
`dimensions.json`（寸法パラメータ）であり、両者は `cli` で同じ終了コード 2 へ
落ちるが、**どちらの設定ファイルの話なのかを型で区別できる**ことに意味がある。

読み込みの規律そのものは `config.py` と同一である——**あらゆる階層で未知キーを
拒否し、欠損を既定値で埋めず、項目名を添えて拒否する**（要件 1.3）。⚠️ 未知キーを
黙って読み飛ばすと、綴りを1文字誤った項目が「設定したつもり」のまま既定値で動き、
設定ファイルが正であるという前提そのものが崩れる。許容されるキーの表は
`config.py` と同じく**手書きせず**、データクラスのフィールドから導く。

`schema_version` は `config.SCHEMA_VERSION` を**そのまま用いる**（tasks.md
「Implementation Notes」タスク 1.4(b): この定数は `config.py` が所有する）。
⚠️ ここで独自の版定数を定義すると、記録形式の版が2箇所で別々に動きうる。

## 候補ファイルの `role`

`candidates.json` の各候補は `role` を持つ。`primary` / `runner_up` /
`not_recommended` は roadmap が名指しした実在の品であり、
`illustrative_non_example` は**基準の説明のために置いた例**である
（roadmap に対応する品名が無い）。⚠️ 両者を取り違えると、「調べた」ことになって
いない品を買いに行く事故になる。`role` は `Candidate`（design.md が定める形）の
フィールドではないため読み込み時に検証して落とすが、`schema_version` と同様、
**ファイルの側では必須のメタデータ**である。

⚠️ **`candidates.json` の数値のうち、roadmap に直接書かれていないものが3つある。**
いずれも roadmap 自身の数値からの導出であり、推測ではない。
- 次点（セリア）のテーパー 12.1° — roadmap は φ215 × H220 × 5L とのみ述べる。
  円錐台の体積式 `V = πh(D₁² + D₁D₂ + D₂²)/12` から底径 φ120.85 が出て片側
  12.078° となる。同じ式は第一候補（φ220 × φ158 × H244）に対して roadmap 記載の
  6.9L を 0.1% 以内で再現する。⚠️ **この値は roadmap が次点として推す判断と
  整合しない**（上限 8.5° を超える）。⚠️ **楽観側（テーパーが緩い側＝合格側）へ
  丸めない。** 12.078° を小数第1位で切り上げた 12.1 を記録し、購入前の実測で
  決着させる（`constraints.py` と同じ「知らない以上、楽観的に見積もっては
  ならない」に従う）
- 非推奨例（ワッツ Re.B）の高さ 227mm — roadmap の「上φ225→底φ145・約10°」から
  `40 / tan(10°) = 226.85mm`。これを mm 単位へ丸めた。⚠️ 高さの基準は 200〜300mm
  の範囲内であり、どちらへ丸めても判定は変わらない（テーパーと違い合否に接して
  いない）。高さを不明のまま置くと高さ項目でも落ち、「テーパーだけが理由」という
  roadmap の判断が読み取れなくなる
- 非推奨例の価格 110円 — roadmap 実態欄「5〜7L 帯は4チェーンとも110円」による

候補の `provenance` は**候補1件につき1つ**である（design.md の型がそう定めている）。
第一候補の重量 228g は実測だが開口内径ほかは公称であるため、`params.py` の
「1つでも仮値を含めば仮値」に従い候補全体としては `assumed` を記録する。
⚠️ **未実測の値から実測を名乗らない。**
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Final, get_args, get_type_hints

from catch_mechanism.config import SCHEMA_VERSION
from catch_mechanism.errors import SelectionError
from catch_mechanism.params import Provenance

__all__ = [
    "DEFAULT_CRITERIA_PATH",
    "DEFAULT_CANDIDATES_PATH",
    "CANDIDATE_ROLES",
    "SelectionCriteria",
    "Candidate",
    "CandidateVerdict",
    "evaluate_candidate",
    "load_criteria",
    "load_candidates",
]


_CONFIG_DIRECTORY: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "catch_mechanism"
)

DEFAULT_CRITERIA_PATH: Final[Path] = _CONFIG_DIRECTORY / "selection-criteria.json"
"""選定基準のしきい値の既定パス（design.md「Directory Structure」）。

`config.DEFAULT_DIMENSIONS_PATH` と同じく `parents[2]` がリポジトリルートである
（`src` レイアウト）。設定ファイルはパッケージデータではなくリポジトリの成果物である。
"""

DEFAULT_CANDIDATES_PATH: Final[Path] = _CONFIG_DIRECTORY / "candidates.json"
"""候補機種の諸元（第一候補・次点・非推奨例・例示）の既定パス。"""

CANDIDATE_ROLES: Final[frozenset[str]] = frozenset(
    {"primary", "runner_up", "not_recommended", "illustrative_non_example"}
)
"""候補ファイルが認める `role` の一覧（本モジュール docstring「候補ファイルの `role`」）。

⚠️ `illustrative_non_example` 以外は **roadmap が名指しした実在の品**にのみ用いる。
"""


_SCHEMA_VERSION_KEY: Final[str] = "schema_version"
_CANDIDATES_KEY: Final[str] = "candidates"
_ROLE_KEY: Final[str] = "role"

_PROVENANCE_VALUES: Final[Mapping[str, Provenance]] = {
    provenance.value: provenance for provenance in Provenance
}


def _require_positive_finite(value: float, name: str) -> None:
    """`value` が正の有限値であることを検証する。

    ⚠️ `params.py` に同名の非公開ヘルパがあるが、あちらは `ParameterError` を
    送出する（寸法パラメータの検証）。本モジュールの失敗は選定の入力の不正で
    あり `SelectionError` でなければならないため、共有せず持ち分ける。
    """
    if not (math.isfinite(value) and value > 0.0):
        raise SelectionError(f"{name}={value!r} は正の有限値でなければならない。")


def _require_nonneg_finite(value: float, name: str) -> None:
    """`value` が 0 以上の有限値であることを検証する。"""
    if not (math.isfinite(value) and value >= 0.0):
        raise SelectionError(f"{name}={value!r} は 0 以上の有限値でなければならない。")


def _require_angle_deg(value: float, name: str) -> None:
    """`value` が 0 以上 90 度未満の有限値であることを検証する（`params.py` と同じ値域）。

    テーパーは**片側**の角度であり、90 度に達すると壁が底面と平行になって形状が
    成立しない。0 は許す（テーパー 0 = 円筒は有効である）。
    """
    if not (math.isfinite(value) and 0.0 <= value < 90.0):
        raise SelectionError(f"{name}={value!r} は 0 以上 90 度未満の有限値でなければならない。")


def _require_nonempty_str(value: str, name: str) -> None:
    """`value` が空でない文字列であることを検証する。"""
    if not isinstance(value, str) or not value:
        raise SelectionError(f"{name}={value!r} は空でない文字列でなければならない。")


@dataclass(frozen=True, slots=True)
class SelectionCriteria:
    """選定基準のしきい値（design.md「Selection」Service Interface / 要件 6.1）。

    形状・開口内径・高さ・重量・テーパー・縁・付属品・価格帯の8項目すべてを
    判定可能な形で持つ。値の正は roadmap であり、本型はその転記である
    （本モジュール docstring の転記表）。

    Attributes:
        shape: 許す形状（`"round"`）。角形は有効開口が方向依存になるため不可。
        opening_inner_diameter_min_mm: 開口内径の下限。これを下回れば不適合。
        opening_inner_diameter_reject_below_mm: roadmap が「不可」と言い切った床。
            判定ではなく**基準の側**を縛る（下限をこの値未満へ緩められない）。
        opening_inner_diameter_typical_max_mm: 実売の実質上限。超過は警告のみ。
        height_min_mm: 高さの下限。空き缶が寝て収まる深さが要る。
        height_max_mm: 高さの上限。
        mass_max_g: 重量の上限。⚠️ 軽さを求める理由は転倒ではなく加速性能である。
        taper_max_deg: **片側**のテーパー角の上限。緩いテーパーのみ許す。
        price_max_jpy: 価格の上限。
        requires_lidless: 蓋の無いことを要求するか。蓋・スイングトップ・内枠は
            いずれも有効開口を狭める。
        prefers_outward_rim: 外向きリムを**望ましい**とするか。⚠️ 必須ではなく、
            満たさなくても `accepted` は偽にならない。
    """

    shape: str
    opening_inner_diameter_min_mm: float
    opening_inner_diameter_reject_below_mm: float
    opening_inner_diameter_typical_max_mm: float
    height_min_mm: float
    height_max_mm: float
    mass_max_g: float
    taper_max_deg: float
    price_max_jpy: int
    requires_lidless: bool
    prefers_outward_rim: bool

    def __post_init__(self) -> None:
        """基準そのものが成立していることを検証する（違反項目名と値を添えて拒否）。"""
        _require_nonempty_str(self.shape, "shape")
        _require_positive_finite(
            self.opening_inner_diameter_reject_below_mm,
            "opening_inner_diameter_reject_below_mm",
        )
        _require_positive_finite(
            self.opening_inner_diameter_min_mm, "opening_inner_diameter_min_mm"
        )
        _require_positive_finite(
            self.opening_inner_diameter_typical_max_mm,
            "opening_inner_diameter_typical_max_mm",
        )
        # 3つのしきい値は「不可の床 ≤ 下限 ≤ 実質上限」の順でなければならない。
        # ⚠️ 下限を床より下へ動かすと、roadmap が不可と断じた φ180 未満が通る。
        if not (
            self.opening_inner_diameter_reject_below_mm <= self.opening_inner_diameter_min_mm
        ):
            raise SelectionError(
                "opening_inner_diameter_reject_below_mm="
                f"{self.opening_inner_diameter_reject_below_mm!r} は "
                f"opening_inner_diameter_min_mm={self.opening_inner_diameter_min_mm!r} "
                "以下でなければならない（不可の床が下限を上回ることはない）。"
            )
        if not (self.opening_inner_diameter_min_mm <= self.opening_inner_diameter_typical_max_mm):
            raise SelectionError(
                f"opening_inner_diameter_min_mm={self.opening_inner_diameter_min_mm!r} は "
                "opening_inner_diameter_typical_max_mm="
                f"{self.opening_inner_diameter_typical_max_mm!r} 以下でなければならない。"
            )
        _require_positive_finite(self.height_min_mm, "height_min_mm")
        _require_positive_finite(self.height_max_mm, "height_max_mm")
        if not (self.height_min_mm <= self.height_max_mm):
            raise SelectionError(
                f"height_min_mm={self.height_min_mm!r} は "
                f"height_max_mm={self.height_max_mm!r} 以下でなければならない。"
            )
        _require_positive_finite(self.mass_max_g, "mass_max_g")
        _require_angle_deg(self.taper_max_deg, "taper_max_deg")
        if not isinstance(self.price_max_jpy, int) or isinstance(self.price_max_jpy, bool):
            raise SelectionError(f"price_max_jpy={self.price_max_jpy!r} は整数でなければならない。")
        if self.price_max_jpy <= 0:
            raise SelectionError(f"price_max_jpy={self.price_max_jpy!r} は正でなければならない。")
        for name in ("requires_lidless", "prefers_outward_rim"):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise SelectionError(f"{name}={value!r} は真偽値でなければならない。")


@dataclass(frozen=True, slots=True)
class Candidate:
    """候補1件の諸元（design.md「Selection」Service Interface）。

    Attributes:
        identifier: 機種名 / 型番 / JAN。⚠️ 第一候補の識別子は
            `dimensions.json` の `trash_can.model_id` と一致させる（要件 6.8）。
        shape: 形状。
        opening_inner_diameter_mm: 開口**内**径（外径ではない。縁の巻き込み分を引く）。
        height_mm: 高さ。
        mass_g: 重量。⚠️ **未記録を `None` で表せる**——公称値に重量が無い品は
            珍しくなく、未記録を 0 や推定値で埋めれば「実測したことになる」（要件 6.7）。
        taper_deg: **片側**のテーパー角。
        price_jpy: 価格。
        has_lid: 蓋・スイングトップ・内枠の有無。
        has_outward_rim: 外向きリムの有無。`None` は不明を表す。
        provenance: 諸元全体の出所。⚠️ **候補1件につき1つ**であり、一部の項目だけが
            実測でも全体としては最弱（`ASSUMED`）を記録する。
    """

    identifier: str
    shape: str
    opening_inner_diameter_mm: float
    height_mm: float
    mass_g: float | None
    taper_deg: float
    price_jpy: int
    has_lid: bool
    has_outward_rim: bool | None
    provenance: Provenance

    def __post_init__(self) -> None:
        """諸元が物理的に成立していることを検証する（違反項目名と値を添えて拒否）。"""
        _require_nonempty_str(self.identifier, "identifier")
        _require_nonempty_str(self.shape, "shape")
        _require_positive_finite(
            self.opening_inner_diameter_mm, "opening_inner_diameter_mm"
        )
        _require_positive_finite(self.height_mm, "height_mm")
        if self.mass_g is not None:
            _require_positive_finite(self.mass_g, "mass_g")
        _require_angle_deg(self.taper_deg, "taper_deg")
        if not isinstance(self.price_jpy, int) or isinstance(self.price_jpy, bool):
            raise SelectionError(f"price_jpy={self.price_jpy!r} は整数でなければならない。")
        _require_nonneg_finite(float(self.price_jpy), "price_jpy")
        if not isinstance(self.has_lid, bool):
            raise SelectionError(f"has_lid={self.has_lid!r} は真偽値でなければならない。")
        if self.has_outward_rim is not None and not isinstance(self.has_outward_rim, bool):
            raise SelectionError(
                f"has_outward_rim={self.has_outward_rim!r} は真偽値または未記録（None）"
                "でなければならない。"
            )
        if not isinstance(self.provenance, Provenance):
            raise SelectionError(
                f"provenance={self.provenance!r} は Provenance でなければならない。"
            )


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    """候補1件の判定結果（design.md「Selection」Service Interface）。

    ⚠️ 本型は判定の**出力**であり、本モジュール自身が矛盾のない値で構築するため
    構築時検証を持たない（`constraints.BuildViolation` と同じ扱い）。
    Postconditions「`accepted` が偽なら `failed_items` は空でない」は
    `evaluate_candidate` の構成——`accepted = not failed_items`——によって成り立つ。

    Attributes:
        identifier: 判定した候補の識別子。
        accepted: 全ての**必須**項目を満たすか。
        failed_items: 不適合であった項目名の一覧（評価順・重複なし）。
        warnings: 適合判定を左右しない指摘の項目名の一覧
            （本モジュール docstring「判定の規律」）。
    """

    identifier: str
    accepted: bool
    failed_items: tuple[str, ...]
    warnings: tuple[str, ...]


def evaluate_candidate(candidate: Candidate, criteria: SelectionCriteria) -> CandidateVerdict:
    """候補を全項目について評価し、不適合項目と警告の一覧を返す（要件 6.2, 6.3, 6.4）。

    ⚠️ **最初の不適合で打ち切らない。** 7項目すべてを評価して一覧を組み立てる
    （design.md「Selection」Responsibilities）。打ち切る実装は「不適合である」ことしか
    伝えず、何をどれだけ直せば候補になるのかを伝えない。

    ⚠️ **不適合は例外ではない。** 候補が基準を満たさないことは正常系の結果であり、
    `accepted = False` と `failed_items` として返る（`errors.py` の区分）。

    Args:
        candidate: 評価する候補の諸元。
        criteria: 判定に用いるしきい値。

    Returns:
        `accepted` が偽なら `failed_items` は空でない `CandidateVerdict`。
        外向きリムの不在・不明、重量の未記録、実売の実質上限を超える開口内径は
        `warnings` にのみ現れ、`accepted` を左右しない。
    """
    failed: list[str] = []
    warnings: list[str] = []

    if candidate.shape != criteria.shape:
        failed.append("shape")

    if candidate.opening_inner_diameter_mm < criteria.opening_inner_diameter_min_mm:
        # 不可の床（reject_below）未満もここに含まれる。床 ≤ 下限 は
        # SelectionCriteria の不変条件であり、二重に項目名を積むことはしない
        # （本モジュール docstring「開口内径の2つのしきい値」）。
        failed.append("opening_inner_diameter_mm")
    elif candidate.opening_inner_diameter_mm > criteria.opening_inner_diameter_typical_max_mm:
        warnings.append("opening_inner_diameter_mm")

    if not (criteria.height_min_mm <= candidate.height_mm <= criteria.height_max_mm):
        failed.append("height_mm")

    if candidate.mass_g is None:
        # 未記録は不適合ではない（要件 6.7）。ただし黙って通してはならない。
        warnings.append("mass_g")
    elif candidate.mass_g > criteria.mass_max_g:
        failed.append("mass_g")

    if candidate.taper_deg > criteria.taper_max_deg:
        failed.append("taper_deg")

    if candidate.price_jpy > criteria.price_max_jpy:
        failed.append("price_jpy")

    if criteria.requires_lidless and candidate.has_lid:
        failed.append("has_lid")

    if criteria.prefers_outward_rim and not candidate.has_outward_rim:
        # ⚠️ 望ましいが必須ではない（design.md「Selection」Invariants）。
        # `failed` へ入れてはならない。None（不明）も False と同じ扱いである。
        warnings.append("has_outward_rim")

    return CandidateVerdict(
        identifier=candidate.identifier,
        accepted=not failed,
        failed_items=tuple(failed),
        warnings=tuple(warnings),
    )


def _field_annotations(dataclass_type: type) -> Mapping[str, object]:
    """データクラスのフィールド名から注釈への対応を、宣言順で返す。

    許容されるキーと値の型の正はこの走査結果だけである（`config.py` と同じく
    **項目名の表を手書きしない**）。手書きの表であれば、フィールドを1つ足したときに
    読み込み側から黙って漏れる。
    """
    hints = get_type_hints(dataclass_type)
    return {field.name: hints[field.name] for field in fields(dataclass_type)}


_CRITERIA_ANNOTATIONS: Final[Mapping[str, object]] = _field_annotations(SelectionCriteria)
_CANDIDATE_ANNOTATIONS: Final[Mapping[str, object]] = _field_annotations(Candidate)

_CRITERIA_KEYS: Final[frozenset[str]] = frozenset({_SCHEMA_VERSION_KEY, *_CRITERIA_ANNOTATIONS})
_CANDIDATE_KEYS: Final[frozenset[str]] = frozenset({_ROLE_KEY, *_CANDIDATE_ANNOTATIONS})
_CANDIDATES_TOP_LEVEL_KEYS: Final[frozenset[str]] = frozenset(
    {_SCHEMA_VERSION_KEY, _CANDIDATES_KEY}
)


def _read_document(path: Path) -> object:
    """`path` を UTF-8 テキストとして読み `json.loads` する。

    ファイル未存在・読み込み不能・JSON 不正のいずれも `SelectionError` へ統一し、
    呼び出し側が選定設定の読み込み失敗を単一の `except` で扱えるようにする
    （`config._read_document` と同形）。
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SelectionError(f"{path}: 設定ファイルを読み込めない: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SelectionError(f"{path}: JSON として解析できない: {exc}") from exc


def _require_object(value: object, label: str) -> Mapping[str, object]:
    """`value` が JSON オブジェクトであることを要求する。"""
    if not isinstance(value, dict):
        raise SelectionError(
            f"{label}: オブジェクト（{{...}}）を期待したが {type(value).__name__} だった。"
        )
    return value


def _reject_unknown_and_missing(
    data: Mapping[str, object], allowed: frozenset[str], label: str
) -> None:
    """未知キーと欠損キーを、いずれも項目名を示して拒否する（要件 1.3 の選定への適用）。

    未知キーを先に見るのは、綴り誤り（`taper_max_degrees`）が「未知キー1件 +
    欠損1件」として現れるとき、直すべき側を先に示すためである。
    """
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SelectionError(
            f"{label}: 未知のキー {unknown!r}。指定できるのは {sorted(allowed)!r} のみである。"
        )
    missing = sorted(allowed - set(data))
    if missing:
        raise SelectionError(
            f"{label}: 必須のキーが欠けている {missing!r}"
            "（欠けている項目を既定値で埋めない）。"
        )


def _convert(value: object, annotation: object, name: str, label: str) -> object:
    """JSON の値を、フィールドの注釈が定める型へ変換する（型違いは項目名つきで拒否）。

    ⚠️ **`bool` を数値として通さない。** Python の `bool` は `int` の派生であるため、
    明示的に除かなければ JSON の `true` が「重量 1g」「価格 1円」として黙って通る。

    整数は浮動小数点の項目へ受け入れて `float` へ広げる（`220` と `220.0` は同じ値で
    ある）。`X | None` の注釈は `null`（未記録）を受け入れる。
    """
    args = get_args(annotation)
    if args:
        if type(None) not in args:  # pragma: no cover - 現在の型定義には現れない
            raise SelectionError(f"{label}: {name} の注釈 {annotation!r} は設定ファイルで扱えない。")
        if value is None:
            return None
        annotation = next(argument for argument in args if argument is not type(None))

    if annotation is bool:
        if not isinstance(value, bool):
            raise SelectionError(
                f"{label}: {name}={value!r} は真偽値でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
    if annotation is Provenance:
        if not isinstance(value, str) or value not in _PROVENANCE_VALUES:
            raise SelectionError(
                f"{label}: {name}={value!r} は出所として認められない"
                f"（指定できるのは {sorted(_PROVENANCE_VALUES)!r} のみ）。"
            )
        return _PROVENANCE_VALUES[value]
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SelectionError(
                f"{label}: {name}={value!r} は整数でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SelectionError(
                f"{label}: {name}={value!r} は数値でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return float(value)
    if annotation is str:
        if not isinstance(value, str):
            raise SelectionError(
                f"{label}: {name}={value!r} は文字列でなければならない"
                f"（{type(value).__name__} だった）。"
            )
        return value
    raise SelectionError(  # pragma: no cover - 構造上の防御
        f"{label}: {name} の値の型 {annotation!r} は設定ファイルで表現できない。"
    )


def _require_schema_version(document: Mapping[str, object], label: str) -> None:
    """記録形式の版が `config.SCHEMA_VERSION` と一致することを要求する。"""
    version = document[_SCHEMA_VERSION_KEY]
    if version != SCHEMA_VERSION:
        raise SelectionError(
            f"{label}: {_SCHEMA_VERSION_KEY}={version!r} は未対応である"
            f"（対応しているのは {SCHEMA_VERSION!r} のみ）。"
        )


def load_criteria(path: Path | None = None) -> SelectionCriteria:
    """選定基準の設定ファイルを読み込み、検証済みの `SelectionCriteria` を返す。

    Args:
        path: 読み込む設定ファイル。省略時は `DEFAULT_CRITERIA_PATH`。

    Returns:
        構築時検証（値域としきい値の順序）を通った `SelectionCriteria`。

    Raises:
        SelectionError: ファイルを読めない場合、JSON として解析できない場合、
            未知キー・欠損キーがある場合、値の型が違う場合、値が範囲外・
            不変条件違反である場合、または記録形式の版が未対応の場合。
    """
    target = DEFAULT_CRITERIA_PATH if path is None else path
    label = str(target)

    document = _require_object(_read_document(target), label)
    _reject_unknown_and_missing(document, _CRITERIA_KEYS, label)
    _require_schema_version(document, label)

    kwargs = {
        name: _convert(document[name], annotation, name, label)
        for name, annotation in _CRITERIA_ANNOTATIONS.items()
    }
    try:
        return SelectionCriteria(**kwargs)  # type: ignore[arg-type]
    except SelectionError as exc:
        # 構築時検証（値域・しきい値の順序）の違反。項目名と値は例外側が持つため、
        # どのファイルの話かだけを補って再送する。
        raise SelectionError(f"{label}: {exc}") from exc


def _build_candidate(entry: object, index: int, label: str) -> Candidate:
    """候補1件分の JSON オブジェクトから `Candidate` を構築する。"""
    where = f"{label}: {_CANDIDATES_KEY}[{index}]"
    values = _require_object(entry, where)
    _reject_unknown_and_missing(values, _CANDIDATE_KEYS, where)

    role = values[_ROLE_KEY]
    if not isinstance(role, str) or role not in CANDIDATE_ROLES:
        raise SelectionError(
            f"{where}: {_ROLE_KEY}={role!r} は認められない"
            f"（指定できるのは {sorted(CANDIDATE_ROLES)!r} のみ）。"
        )

    kwargs = {
        name: _convert(values[name], annotation, name, where)
        for name, annotation in _CANDIDATE_ANNOTATIONS.items()
    }
    try:
        return Candidate(**kwargs)  # type: ignore[arg-type]
    except SelectionError as exc:
        raise SelectionError(f"{where}: {exc}") from exc


def load_candidates(path: Path | None = None) -> tuple[Candidate, ...]:
    """候補の設定ファイルを読み込み、検証済みの `Candidate` をファイルの順で返す。

    ⚠️ 各候補の `role`（`primary` / `runner_up` / `not_recommended` /
    `illustrative_non_example`）は**必須**であり、認められた語だけを受け付ける。
    `Candidate` は design.md が定める形であり `role` を持たないため、検証したうえで
    戻り値には載せない——ファイルの側で「調べた品」と「説明のための例」が
    区別できていることが目的である（本モジュール docstring）。

    Args:
        path: 読み込む設定ファイル。省略時は `DEFAULT_CANDIDATES_PATH`。

    Returns:
        ファイルに現れる順の `Candidate` の並び。

    Raises:
        SelectionError: ファイルを読めない場合、JSON として解析できない場合、
            いずれかの階層に未知キー・欠損キーがある場合、値の型が違う場合、
            値が範囲外である場合、`role` が認められない場合、記録形式の版が
            未対応の場合、または識別子が重複する場合。
    """
    target = DEFAULT_CANDIDATES_PATH if path is None else path
    label = str(target)

    document = _require_object(_read_document(target), label)
    _reject_unknown_and_missing(document, _CANDIDATES_TOP_LEVEL_KEYS, label)
    _require_schema_version(document, label)

    entries = document[_CANDIDATES_KEY]
    if not isinstance(entries, list):
        raise SelectionError(
            f"{label}: {_CANDIDATES_KEY} は配列（[...]）でなければならない"
            f"（{type(entries).__name__} だった）。"
        )

    candidates = tuple(
        _build_candidate(entry, index, label) for index, entry in enumerate(entries)
    )

    seen: set[str] = set()
    for candidate in candidates:
        # ⚠️ 識別子は候補を名指しするための鍵である。重複したまま読み込むと、
        # 「どちらの諸元で判定したのか」が呼び出し側から決められなくなる。
        if candidate.identifier in seen:
            raise SelectionError(
                f"{label}: identifier={candidate.identifier!r} が重複している。"
            )
        seen.add(candidate.identifier)
    return candidates
