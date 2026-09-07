"""投擲レイアウト。数値をコードへ埋め込まず、設定として外部化する。

design.md「Components and Interfaces / ThrowLayout」、tasks.md タスク 1.4、
要件 4.3, 5.6, 13.7, 13.8, 13.9。

実測の意味は**投擲レイアウトに全面的に依存する**。狙い誤差は「待機位置から
実際の落下地点までの水平距離」（要件 5.6）であり、リリース時刻は「推定軌道を
リリース高さまで外挿した時刻」（要件 4.3）である。待機位置やリリース高さが
コードに埋まっていると、レイアウトを動かしたときに**数値の意味だけが静かに
変わる**——だからここは設定であって定数ではない（要件 13.8）。

**投擲位置は2箇所以上にできる。** 1ファイルに複数のレイアウトを書き、
実行時に識別子で選ぶ。誤差の帰属（要件 6）は「共通の偏りが World 固定方向を
向くのか、カメラ視線方向を向くのか」で原因を切り分けるが、**投擲位置を固定
するとこの2つの向きが縮退して判別できなくなる**（`research.md` Decision 4）。
いま2箇所目を用意していなくても、後から足せる形にしておくことがこの
モジュールの役目である。

**レイアウトの確定は本 Spec の範囲外である**（要件 13.9）。同梱の
`.kiro/specs/m1-prediction-validation/layout.example.json` は形式を示すための
**仮値**であり、実験の前に実測値へ置き換える。

本モジュールは L2 層であり、`errors`（L0）と標準ライブラリだけを参照する。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from m1_validation.errors import M1ConfigError

#: レイアウトファイルの形式版。未知の版は内容を推測して読まない
#: （上流3 Spec の `RECORDING_FORMAT_VERSION` / `PLAN_FORMAT_VERSION` と同じ方針）。
LAYOUT_FORMAT_VERSION: str = "1.0"

#: `release_position_world_mm` / `camera_position_world_mm` の要素数。
_POINT3_LENGTH = 3

#: `standby_position_world_mm` の要素数（床面上の位置なので水平2成分）。
_POINT2_LENGTH = 2


@dataclass(frozen=True, slots=True)
class ThrowLayout:
    """1つの投擲レイアウト（要件 13.8）。

    Attributes:
        layout_id: このレイアウトの識別子。**空にできない**——投擲群を
            レイアウトごとに分けて集計するための鍵である（帰属は投擲位置
            ごとに向きが変わることを使うので、混ぜると結論が壊れる）。
        release_position_world_mm: 投擲位置（World 座標 mm）。分かる場合のみ
            与え、不明なら `None`。
        release_height_mm: リリース高さ（mm）。推定軌道を観測開始より前へ
            外挿し、**この高さに達する時刻**をリリース時刻とする（要件 4.3）。
        throw_direction_deg: 投擲方向。World +X からの角度（deg）。
        standby_position_world_mm: ゴミ箱の待機位置（World 座標 mm の水平2成分）。
            **狙い誤差の基準**である（要件 5.6）。
        object_diameter_mm: 対象物の代表寸法（mm）。
        aperture_diameter_mm: 開口の寸法（mm）。
        camera_position_world_mm: カメラ位置（World 座標 mm）。**World 系で
            表したカメラ視線方向**の算出に使う（要件 6.3。誤差の共通偏りが
            どちらの向きを向いているかで原因を切り分ける）。
        notes: このレイアウトについての注記。仮値である旨・測り方・未決事項を
            ここへ書く（要件 13.7, 13.9）。

    Invariants（構築時に拒否する。要件 13.6「不正な設定を実行開始前に拒否」）:
        `layout_id` が空文字列・空白のみでない。
        `release_height_mm > 0`。
        `0 < object_diameter_mm < aperture_diameter_mm`。
    """

    layout_id: str
    release_position_world_mm: tuple[float, float, float] | None
    release_height_mm: float
    throw_direction_deg: float
    standby_position_world_mm: tuple[float, float]
    object_diameter_mm: float
    aperture_diameter_mm: float
    camera_position_world_mm: tuple[float, float, float]
    notes: str

    def __post_init__(self) -> None:
        if not self.layout_id.strip():
            raise M1ConfigError(
                "ThrowLayout.layout_id を空にはできない: "
                "投擲群をレイアウトごとに分けて集計するための鍵である",
                {"field": "layout_id", "value": self.layout_id},
            )
        if self.release_height_mm <= 0:
            raise M1ConfigError(
                "ThrowLayout.release_height_mm は正でなければならない: "
                "リリース時刻の外挿はこの高さを基準にする（要件 4.3）",
                {"release_height_mm": self.release_height_mm},
            )
        if not 0 < self.object_diameter_mm < self.aperture_diameter_mm:
            raise M1ConfigError(
                "対象物の寸法は 0 より大きく開口寸法より小さくなければならない: "
                "そうでなければ位置許容窓が 0 以下になり、評価の意味が無くなる",
                {
                    "object_diameter_mm": self.object_diameter_mm,
                    "aperture_diameter_mm": self.aperture_diameter_mm,
                },
            )

    @property
    def position_tolerance_mm(self) -> float:
        """位置精度の**暫定目標値**（mm）= 開口半径 − 対象寸法/2。

        `docs/requirements.md` NFR-5 の導出（開口 φ200・対象 φ65 なら
        100 − 32.5 = ±67.5mm）をレイアウトから計算し直したものである。
        ゴミを大きさのない点として扱わないための窓であり、単純な
        「開口半径」より厳しい。

        ⚠️ **これは合否条件ではない。** 暫定目標値であって、超えたから失敗・
        下回ったから成功と判定してよい値ではない（要件 8.3。design.md
        「ThrowLayout」Risks が「一人歩きしやすい」と名指ししている）。
        NFR-7 の成立判定は「開口に入り、かつ跳ね出さずに留まる」ことであり、
        本 Spec はそこまで判断しない。図と表に出すときも**暫定目標値である
        旨を必ず併記すること**。
        """
        return self.aperture_diameter_mm / 2.0 - self.object_diameter_mm / 2.0


_FIELD_NAMES: frozenset[str] = frozenset(field.name for field in fields(ThrowLayout))


def _require_mapping(value: object, *, what: str, path: Path) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise M1ConfigError(
            f"{what}が object でない: {type(value).__name__}（path={path}）",
            {"path": str(path)},
        )
    return value


def _coerce_point(
    value: object, *, key: str, length: int, layout_id: str, path: Path
) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise M1ConfigError(
            f"{key} は数値の配列でなければならない（layout_id={layout_id}, path={path}）",
            {"key": key, "layout_id": layout_id},
        )
    if len(value) != length:
        raise M1ConfigError(
            f"{key} の要素数が {length} でない: {len(value)} 件"
            f"（layout_id={layout_id}, path={path}）",
            {"key": key, "layout_id": layout_id, "length": len(value)},
        )
    try:
        return tuple(float(item) for item in value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise M1ConfigError(
            f"{key} に数値でない要素がある（layout_id={layout_id}, path={path}）",
            {"key": key, "layout_id": layout_id},
        ) from exc


def _coerce_float(value: object, *, key: str, layout_id: str, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise M1ConfigError(
            f"{key} は数値でなければならない: {value!r}"
            f"（layout_id={layout_id}, path={path}）",
            {"key": key, "layout_id": layout_id},
        )
    return float(value)


def _layout_from_dict(data: Mapping[str, object], *, path: Path) -> ThrowLayout:
    """1件ぶんの dict を `ThrowLayout` へ変換する。

    未知のキーも欠けたキーも拒否する。**綴り間違いが黙って無視されると、
    書いたつもりの値が効いていないまま実験が進む**——レイアウトは実測の
    意味そのものを決めるので、ここでの見落としは全投擲に効く。
    """
    layout_id = data.get("layout_id")
    label = layout_id if isinstance(layout_id, str) else "<layout_id 不明>"

    present = set(data)
    unknown = sorted(present - _FIELD_NAMES)
    if unknown:
        raise M1ConfigError(
            f"レイアウトに未知のキーがある: {unknown}"
            f"（layout_id={label}, path={path}）。綴り間違いの可能性がある",
            {"unknown_keys": unknown, "layout_id": label},
        )
    missing = sorted(_FIELD_NAMES - present)
    if missing:
        raise M1ConfigError(
            f"レイアウトに必須のキーが無い: {missing}（layout_id={label}, path={path}）",
            {"missing_keys": missing, "layout_id": label},
        )

    if not isinstance(layout_id, str):
        raise M1ConfigError(
            f"layout_id は文字列でなければならない: {layout_id!r}（path={path}）",
            {"key": "layout_id"},
        )
    notes = data["notes"]
    if not isinstance(notes, str):
        raise M1ConfigError(
            f"notes は文字列でなければならない: {notes!r}（layout_id={label}, path={path}）",
            {"key": "notes", "layout_id": label},
        )

    release_position = data["release_position_world_mm"]
    return ThrowLayout(
        layout_id=layout_id,
        release_position_world_mm=(
            None
            if release_position is None
            else _coerce_point(  # type: ignore[arg-type]
                release_position,
                key="release_position_world_mm",
                length=_POINT3_LENGTH,
                layout_id=label,
                path=path,
            )
        ),
        release_height_mm=_coerce_float(
            data["release_height_mm"], key="release_height_mm", layout_id=label, path=path
        ),
        throw_direction_deg=_coerce_float(
            data["throw_direction_deg"], key="throw_direction_deg", layout_id=label, path=path
        ),
        standby_position_world_mm=_coerce_point(  # type: ignore[arg-type]
            data["standby_position_world_mm"],
            key="standby_position_world_mm",
            length=_POINT2_LENGTH,
            layout_id=label,
            path=path,
        ),
        object_diameter_mm=_coerce_float(
            data["object_diameter_mm"], key="object_diameter_mm", layout_id=label, path=path
        ),
        aperture_diameter_mm=_coerce_float(
            data["aperture_diameter_mm"], key="aperture_diameter_mm", layout_id=label, path=path
        ),
        camera_position_world_mm=_coerce_point(  # type: ignore[arg-type]
            data["camera_position_world_mm"],
            key="camera_position_world_mm",
            length=_POINT3_LENGTH,
            layout_id=label,
            path=path,
        ),
        notes=notes,
    )


def load_layouts(path: Path | str) -> tuple[ThrowLayout, ...]:
    """レイアウトファイルを読み、含まれるレイアウトをすべて返す（要件 13.8）。

    ファイルは次の形の JSON である。`layouts` は**1件以上**の配列であり、
    **投擲位置を2箇所以上にする場合はここへ足す**（`research.md` Decision 4）::

        {"format_version": "1.0", "layouts": [{...}, {...}]}

    Args:
        path: レイアウトファイルのパス。

    Returns:
        ファイルに書かれた順の `ThrowLayout` のタプル。

    Raises:
        M1ConfigError: ファイルが読めない・JSON でない・形式版が未知・
            キーが欠けている／未知・識別子が重複している・寸法の大小関係が
            崩れている、のいずれか。**すべて読み込みの時点で拒否する**
            （要件 13.6。読み込んだ後で気づくと、その投擲群は撮り直しになる）。
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise M1ConfigError(
            f"レイアウトファイルを読み込めない: {target}", {"path": str(target)}
        ) from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise M1ConfigError(
            f"レイアウトファイルが正しい JSON でない: {target}", {"path": str(target)}
        ) from exc

    document = _require_mapping(data, what="レイアウトファイルの最上位", path=target)

    format_version = document.get("format_version")
    if format_version != LAYOUT_FORMAT_VERSION:
        raise M1ConfigError(
            "未知のレイアウト形式版を読み込もうとした: "
            f"{format_version!r}（対応する形式版: {LAYOUT_FORMAT_VERSION!r}, path={target}）",
            {"format_version": format_version, "expected": LAYOUT_FORMAT_VERSION},
        )

    raw_layouts = document.get("layouts")
    if not isinstance(raw_layouts, Sequence) or isinstance(raw_layouts, str | bytes):
        raise M1ConfigError(
            f"layouts は配列でなければならない（path={target}）", {"path": str(target)}
        )
    if not raw_layouts:
        raise M1ConfigError(
            f"layouts が空である（path={target}）", {"path": str(target)}
        )

    layouts = tuple(
        _layout_from_dict(
            _require_mapping(entry, what="layouts の要素", path=target), path=target
        )
        for entry in raw_layouts
    )

    seen: dict[str, int] = {}
    for position, item in enumerate(layouts):
        if item.layout_id in seen:
            raise M1ConfigError(
                f"layout_id が重複している: {item.layout_id!r} が "
                f"{seen[item.layout_id]} 件目と {position} 件目の両方に現れる"
                f"（path={target}）。同じ識別子が2つのレイアウトを指すと"
                "投擲群を分けられない",
                {"layout_id": item.layout_id, "path": str(target)},
            )
        seen[item.layout_id] = position

    return layouts


def load_layout(path: Path | str, *, layout_id: str | None = None) -> ThrowLayout:
    """レイアウトファイルから1つを選んで返す。

    Args:
        path: レイアウトファイルのパス。
        layout_id: 選ぶレイアウトの識別子。ファイルに1件しか無い場合のみ
            省略できる。

    Returns:
        選ばれた `ThrowLayout`。

    Raises:
        M1ConfigError: `load_layouts()` が送出する条件に加えて、
            **複数あるのに `layout_id` が指定されていない**場合と、
            指定された識別子が存在しない場合。

            複数あるときに黙って先頭を選ばないのは、**別の投擲位置の
            レイアウトで撮ったことになった投擲群**が生まれるからである。
            誤差の帰属は投擲位置ごとに向きが変わることを使って原因を
            切り分けるので、取り違えは結論を直接ねじ曲げる。
    """
    layouts = load_layouts(path)
    available = [item.layout_id for item in layouts]

    if layout_id is None:
        if len(layouts) == 1:
            return layouts[0]
        raise M1ConfigError(
            f"レイアウトが複数あるので layout_id を指定すること: {available}"
            f"（path={path}）",
            {"available": available, "path": str(path)},
        )

    for item in layouts:
        if item.layout_id == layout_id:
            return item
    raise M1ConfigError(
        f"指定された layout_id がファイルに無い: {layout_id!r}"
        f"（実在する識別子: {available}, path={path}）",
        {"layout_id": layout_id, "available": available},
    )
