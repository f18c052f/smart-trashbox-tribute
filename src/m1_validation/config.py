"""設定の解決と起動時検証。

design.md「Components and Interfaces / M1Settings」、tasks.md タスク 1.5、
要件 5.10, 9.9, 13.5, 13.6, 13.7。

解決順序は **既定値 → 設定ファイル → 環境変数 → 実行時指定**（要件 13.5）。
上流3 Spec（`sensing_foundation.config` / `flying_object_tracking.config`）と
同じ構造を採り、環境変数の接頭辞だけを `STB_M1_` にして衝突を避ける。

**不正な設定は実行開始前に拒否する**（要件 13.6）。投擲実験は1回ごとに人が
物を投げる作業であり、設定の誤りに実行の途中で気づくと、その投擲群が
まるごと無駄になる。

⚠️ **既定値は暫定の評価候補であって必須性能ではない。** `min_valid_throws=20`
は OQ-05 の「最低20回程度」に揃えた出発点、収束帯域はレイアウトの暫定許容窓
（NFR-5 の導出値）、`direction_agreement_deg=30` は向きが整合するとみなす
角度差の仮置きである。これらを合否条件として扱うと、**根拠のない基準から
逆算した「不足」判断が独り歩きする**——`describe()` はこの旨を
`provisional_notice` として必ず出力し、`--print-settings` を見た人が
取り違えないようにする（要件 13.7）。

**レイアウトはコードに持たない**（要件 13.8）。`layout_file` を与えずに設定を
解決することはできない。数値をコードへ埋め込まないという要求を、
「与えないと動かない」という形で構造的に満たす。

本モジュールは L3 層であり、`errors` / `layout` と標準ライブラリだけを
参照する（design.md「Dependency Direction」: errors → types → layout → config）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from m1_validation.errors import M1ConfigError
from m1_validation.layout import ThrowLayout, load_layout

#: 環境変数の接頭辞。上流の `STB_SF_`（sensing-foundation）・
#: `STB_FOT_`（flying-object-tracking）と衝突しない
#: （design.md「M1Settings」Integration）。
ENV_PREFIX = "STB_M1_"

#: `describe()` が必ず出力する注記。`--print-settings` の読み手が既定値を
#: 必須条件と取り違えないようにする（要件 13.7。design.md「M1Settings」Risks が
#: 名指しする3つを明示的に挙げる）。
PROVISIONAL_NOTICE = (
    "ここに出ている既定値は**暫定の評価候補であって必須性能ではない**。"
    "特に min_valid_throws（試行数の下限）・band_mm（収束の帯域）・"
    "direction_agreement_deg（向きが整合するとみなす角度差）・"
    "bootstrap_iterations（帰属の再抽出回数）・"
    "residual_significance_ratio（残差を大きいとみなす倍率）・"
    "range_band_mm（距離帯の幅）・"
    "cpu_saturation_ratio（CPU 使用率を飽和とみなす割合）・"
    "fps_shortfall_ratio（実処理 fps が取得 fps に追いつけていないとみなす割合）は、"
    "実測前に置いた仮の値である。合否条件として扱ってはならない。"
)


@dataclass(frozen=True, slots=True)
class SeamConfig:
    """継ぎ目の除外規則（要件 1.7, 2.1）。

    Attributes:
        require_verified_calibration: 検証を通していないキャリブレーション
            結果での実行を拒否するか。**既定で有効**（要件 2.1）——検証前の
            座標系で撮ると、得られた誤差が系統誤差か予測誤差か分離できない。
        min_valid_depth_px: 観測点を採用する有効 Depth 画素数の下限。
        max_depth_spread_mm: 観測点を採用する奥行きばらつきの上限（mm）。
        floor_margin_mm: これより下は床面下として除外する高さ（mm）。
            負の値は床面より下側の余裕を意味する。
    """

    require_verified_calibration: bool = True
    min_valid_depth_px: int = 8
    max_depth_spread_mm: float = 200.0
    floor_margin_mm: float = -50.0


@dataclass(frozen=True, slots=True)
class ConvergenceConfig:
    """収束の判定規則（要件 5.7, 5.8）。

    Attributes:
        band_mm: 誤差が収束したとみなす帯域（mm）。`None` なら
            **レイアウトの暫定許容窓**（`ThrowLayout.position_tolerance_mm`）に
            揃える。実効値は `M1Settings.effective_convergence_band_mm` で得る。
        require_monotonic_tail: 収束の判定に、末尾が帯域内へ入ったまま
            戻らないことを要求するか。
    """

    band_mm: float | None = None
    require_monotonic_tail: bool = True


@dataclass(frozen=True, slots=True)
class AttributionConfig:
    """誤差帰属のパラメータ（要件 6.8, 6.9）。

    Attributes:
        bootstrap_iterations: 観測サンプルの再抽出回数（要件 6.8）。
            ⚠️ **既定値 200 は暫定の評価候補であって必須性能ではない**
            （要件 13.7）。ブートストラップの慣行的な出発点であり、何回で
            見積もりが安定するかは実測してから決める。**合否条件として
            扱わない**——`PROVISIONAL_NOTICE` が `--print-settings` の
            読み手へ必ずその旨を出す。
        bootstrap_seed: 再抽出の乱数種。**決定性のため既定で固定する**
            （要件 3.7: 同一入力に同一の集計値と同一の判定）。
        direction_agreement_deg: 2つの向きが整合するとみなす角度差の上限
            （deg）。共通偏りが World 固定方向を向くのかカメラ視線方向を
            向くのかの判別に使う。
        bias_significance_ratio: 共通偏りを有意とみなす「偏りの大きさ /
            ばらつき」の下限。
        residual_significance_ratio: ばらつきが観測由来の範囲を超えたとき、
            フィットの残差代表値を「大きい」とみなす倍率（要件 6.7）。
            残差代表値が**再抽出で見積もった予測ばらつき**のこの倍以上なら
            モデル由来（予測）とする。⚠️ **既定値 1.0 は暫定の評価候補で
            あって必須性能ではない**（要件 13.7）。絶対値の目標を置かず、
            同一測定内の量どうしの相対比較にするための倍率である。
        range_band_mm: 距離帯の幅（mm。要件 6.11）。カメラから落下地点まで
            の距離をこの幅で区切り、帯ごとの誤差を提示する。⚠️ **既定値
            500.0 は暫定の評価候補**であり、実験レイアウトの奥行きが決まって
            から見直す事項である。
    """

    bootstrap_iterations: int = 200
    bootstrap_seed: int = 0
    direction_agreement_deg: float = 30.0
    bias_significance_ratio: float = 1.0
    residual_significance_ratio: float = 1.0
    range_band_mm: float = 500.0


@dataclass(frozen=True, slots=True)
class Oq27Config:
    """OQ-27（Pi 4 継続可否）の判定に使う相対比較の割合（要件 9.2, 9.7）。

    **どちらも同一測定内の量どうしの比であり、絶対値の目標ではない**
    （要件 9.2、`tech.md` 開発標準1）。fps も CPU 使用率も「何 fps なら十分」
    「何 % なら十分」という形では持たない——実測がまだ無い以上そういう数値に
    根拠は無く、置いた瞬間に既成事実化する。

    Attributes:
        cpu_saturation_ratio: CPU 使用率の平均が**全負荷**（使用率という
            測定量そのものの上限である 100%）のこの割合以上なら、計算資源が
            飽和しているとみなす。100% は性能目標ではなく**その量の目盛りの
            端**であり、「飽和」とは目盛りの端に張り付いている状態を指す。
            ⚠️ **既定値 0.9 は暫定の評価候補であって必須性能ではない**
            （要件 13.7）。
        fps_shortfall_ratio: 実処理 fps が取得 fps のこの割合を下回れば、
            取得した分を処理し切れていない（＝資源が飽和している）とみなす。
            **同一測定内の2量の比**であり、絶対値の fps 目標を置かない。
            ⚠️ **既定値 0.95 も暫定の評価候補**である。
    """

    cpu_saturation_ratio: float = 0.9
    fps_shortfall_ratio: float = 0.95


@dataclass(frozen=True, slots=True)
class TrialLimits:
    """判断を出してよい試行数の下限（要件 5.10, 9.9, 9.10）。

    Attributes:
        min_valid_throws: 有効な投擲数の下限。下回れば結果は暫定であり、
            判断に用いてよい状態にしない（要件 5.10）。
        min_sessions: セッション数の下限（要件 9.9）。
        require_live_source: 実機由来の投擲を必須とするか（OQ-27 の GATE 2。
            要件 9.10: 実機以外の入力しか無ければ実機の結論として扱わない）。
    """

    min_valid_throws: int = 20
    min_sessions: int = 2
    require_live_source: bool = True


_DEFAULT_SEAM = SeamConfig()
_DEFAULT_CONVERGENCE = ConvergenceConfig()
_DEFAULT_ATTRIBUTION = AttributionConfig()
_DEFAULT_OQ27 = Oq27Config()
_DEFAULT_TRIALS = TrialLimits()


@dataclass(frozen=True, slots=True)
class M1Settings:
    """解決済みの設定一式（要件 13.5）。

    Attributes:
        layout: 投擲レイアウト（タスク 1.4）。`layout_file` から読み込む。
        seam: 継ぎ目の除外規則。
        convergence: 収束の判定規則。
        attribution: 誤差帰属のパラメータ。
        oq27: OQ-27 の判定に使う相対比較の割合。
        trials: 試行数の下限。
        improvements_applied: `development-environment.md §13.2` の改善項目の
            うち適用済みのもの（要件 9.4。未適用が残る間は「不足」を出さない）。
        output_root: 出力先。既定は `var/m1`（`.gitignore` の `var/` 規則の傘）。

    Postconditions:
        解決後の設定は不変であり、実行中に変更されない。
    """

    layout: ThrowLayout
    seam: SeamConfig = _DEFAULT_SEAM
    convergence: ConvergenceConfig = _DEFAULT_CONVERGENCE
    attribution: AttributionConfig = _DEFAULT_ATTRIBUTION
    oq27: Oq27Config = _DEFAULT_OQ27
    trials: TrialLimits = _DEFAULT_TRIALS
    improvements_applied: tuple[str, ...] = ()
    output_root: Path = Path("var/m1")

    @property
    def effective_convergence_band_mm(self) -> float:
        """実際に使う収束帯域（mm）。

        `convergence.band_mm` が `None` のときは**レイアウトの暫定許容窓**に
        揃える（要件 5.8。design.md「M1Settings」の `band_mm` 注記）。
        導出をここに1つだけ置くのは、利用側が
        `band_mm or layout.position_tolerance_mm` と書き散らすと、
        **設定した値と実際に使う値が食い違う経路が増える**からである。

        ⚠️ この帯域は暫定の評価候補であり、合否条件ではない
        （`ThrowLayout.position_tolerance_mm` の注記を参照）。
        """
        if self.convergence.band_mm is None:
            return self.layout.position_tolerance_mm
        return self.convergence.band_mm

    @classmethod
    def resolve(
        cls,
        *,
        file: Path | None,
        env: Mapping[str, str],
        overrides: Mapping[str, object],
    ) -> M1Settings:
        """設定を **既定値 → 設定ファイル → 環境変数 → 実行時指定** の順で解決する。

        Args:
            file: JSON 設定ファイルのパス。`None` なら設定ファイル層をスキップ
                する。読めない・JSON でない場合は `M1ConfigError`。
            env: 環境変数のマッピング（例 `os.environ`）。`STB_M1_` を前置した
                キーのみを参照する（例 `STB_M1_MIN_VALID_THROWS`）。
                **未知の `STB_M1_*` は無視する**——環境変数は本パッケージが
                知らない用途にも使われ得るため。
            overrides: CLI 引数など、最優先で適用する値。キーは `file` / `env`
                と同じフラットな名前を使う。**未知のキーは拒否する**
                （綴り間違いが黙って無視されると、指定したつもりの値が
                効かないまま実験が進む）。

        Returns:
            検証済みで不変な `M1Settings`。

        Raises:
            M1ConfigError: `layout_file` が与えられていない、値が定義域の外、
                未知の設定キー、設定ファイルまたはレイアウトファイルが
                読めない・不正、のいずれか。**すべて実行開始前に拒否する**
                （要件 13.6）。
        """
        values: dict[str, object] = {
            key: _default_value(spec) for key, spec in _FIELD_SPECS.items()
        }

        if file is not None:
            _apply_layer(values, _load_file(file), layer_name="設定ファイル")
        _apply_layer(values, _extract_env(env), layer_name="環境変数")
        _apply_layer(values, overrides, layer_name="実行時指定")

        _validate_values(values)

        layout_file = values["layout_file"]
        if layout_file is None:
            raise M1ConfigError(
                "layout_file が指定されていない: 投擲レイアウトはコードに持たず"
                "設定として外部から与える（要件 13.8）。"
                "例: .kiro/specs/m1-prediction-validation/layout.example.json",
                {"key": "layout_file"},
            )
        layout = load_layout(
            layout_file,  # type: ignore[arg-type]
            layout_id=values["layout_id"],  # type: ignore[arg-type]
        )

        return cls(
            layout=layout,
            seam=SeamConfig(
                require_verified_calibration=values["require_verified_calibration"],  # type: ignore[arg-type]
                min_valid_depth_px=values["min_valid_depth_px"],  # type: ignore[arg-type]
                max_depth_spread_mm=values["max_depth_spread_mm"],  # type: ignore[arg-type]
                floor_margin_mm=values["floor_margin_mm"],  # type: ignore[arg-type]
            ),
            convergence=ConvergenceConfig(
                band_mm=values["convergence_band_mm"],  # type: ignore[arg-type]
                require_monotonic_tail=values["require_monotonic_tail"],  # type: ignore[arg-type]
            ),
            attribution=AttributionConfig(
                bootstrap_iterations=values["bootstrap_iterations"],  # type: ignore[arg-type]
                bootstrap_seed=values["bootstrap_seed"],  # type: ignore[arg-type]
                direction_agreement_deg=values["direction_agreement_deg"],  # type: ignore[arg-type]
                bias_significance_ratio=values["bias_significance_ratio"],  # type: ignore[arg-type]
                residual_significance_ratio=values["residual_significance_ratio"],  # type: ignore[arg-type]
                range_band_mm=values["range_band_mm"],  # type: ignore[arg-type]
            ),
            oq27=Oq27Config(
                cpu_saturation_ratio=values["cpu_saturation_ratio"],  # type: ignore[arg-type]
                fps_shortfall_ratio=values["fps_shortfall_ratio"],  # type: ignore[arg-type]
            ),
            trials=TrialLimits(
                min_valid_throws=values["min_valid_throws"],  # type: ignore[arg-type]
                min_sessions=values["min_sessions"],  # type: ignore[arg-type]
                require_live_source=values["require_live_source"],  # type: ignore[arg-type]
            ),
            improvements_applied=values["improvements_applied"],  # type: ignore[arg-type]
            output_root=values["output_root"],  # type: ignore[arg-type]
        )

    def describe(self) -> dict[str, object]:
        """解決結果を JSON 化できる形で返す（`--print-settings`。要件 13.5）。

        `convergence` には設定値（`band_mm`）と**実際に使われる値**
        （`effective_band_mm`）の両方を出す。`null` だけを見せると
        「収束判定が無効」と誤解されるためである。

        `provisional_notice` を必ず含める（要件 13.7）。
        """
        return {
            "layout": {
                "layout_id": self.layout.layout_id,
                "release_position_world_mm": _as_list(
                    self.layout.release_position_world_mm
                ),
                "release_height_mm": self.layout.release_height_mm,
                "throw_direction_deg": self.layout.throw_direction_deg,
                "standby_position_world_mm": _as_list(
                    self.layout.standby_position_world_mm
                ),
                "object_diameter_mm": self.layout.object_diameter_mm,
                "aperture_diameter_mm": self.layout.aperture_diameter_mm,
                "camera_position_world_mm": _as_list(
                    self.layout.camera_position_world_mm
                ),
                "position_tolerance_mm": self.layout.position_tolerance_mm,
                "notes": self.layout.notes,
            },
            "seam": {
                "require_verified_calibration": self.seam.require_verified_calibration,
                "min_valid_depth_px": self.seam.min_valid_depth_px,
                "max_depth_spread_mm": self.seam.max_depth_spread_mm,
                "floor_margin_mm": self.seam.floor_margin_mm,
            },
            "convergence": {
                "band_mm": self.convergence.band_mm,
                "effective_band_mm": self.effective_convergence_band_mm,
                "require_monotonic_tail": self.convergence.require_monotonic_tail,
            },
            "attribution": {
                "bootstrap_iterations": self.attribution.bootstrap_iterations,
                "bootstrap_seed": self.attribution.bootstrap_seed,
                "direction_agreement_deg": self.attribution.direction_agreement_deg,
                "bias_significance_ratio": self.attribution.bias_significance_ratio,
                "residual_significance_ratio": (
                    self.attribution.residual_significance_ratio
                ),
                "range_band_mm": self.attribution.range_band_mm,
            },
            "oq27": {
                "cpu_saturation_ratio": self.oq27.cpu_saturation_ratio,
                "fps_shortfall_ratio": self.oq27.fps_shortfall_ratio,
            },
            "trials": {
                "min_valid_throws": self.trials.min_valid_throws,
                "min_sessions": self.trials.min_sessions,
                "require_live_source": self.trials.require_live_source,
            },
            "improvements_applied": list(self.improvements_applied),
            "output_root": str(self.output_root),
            "provisional_notice": PROVISIONAL_NOTICE,
        }


# --------------------------------------------------------------------------
# 型変換（各層の生の値を設定値へ）
# --------------------------------------------------------------------------


def _as_list(value: tuple[float, ...] | None) -> list[float] | None:
    return None if value is None else list(value)


def _coerce_int(raw: object) -> int:
    if isinstance(raw, bool):
        raise M1ConfigError(f"整数を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise M1ConfigError(f"整数として解釈できない: {raw!r}") from exc
    raise M1ConfigError(f"整数を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_float(raw: object) -> float:
    if isinstance(raw, bool):
        raise M1ConfigError(f"数値を期待したが真偽値を受け取った: {raw!r}")
    if isinstance(raw, int | float):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise M1ConfigError(f"数値として解釈できない: {raw!r}") from exc
    raise M1ConfigError(f"数値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip().lower() in ("", "none", "null"):
        return None
    return _coerce_float(raw)


def _coerce_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise M1ConfigError(f"真偽値として解釈できない: {raw!r}")
    raise M1ConfigError(f"真偽値を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw if raw.strip() else None
    raise M1ConfigError(f"文字列を期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_path(raw: object) -> Path:
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    raise M1ConfigError(f"パスを期待したが {type(raw).__name__} を受け取った: {raw!r}")


def _coerce_optional_path(raw: object) -> Path | None:
    if raw is None:
        return None
    if isinstance(raw, str) and not raw.strip():
        return None
    return _coerce_path(raw)


def _coerce_str_tuple(raw: object) -> tuple[str, ...]:
    """文字列の並びへ変換する。環境変数のためにカンマ区切りも受ける。"""
    if isinstance(raw, str):
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    if isinstance(raw, Sequence):
        items = list(raw)
        if all(isinstance(item, str) for item in items):
            return tuple(items)  # type: ignore[arg-type]
        raise M1ConfigError(f"文字列でない要素が含まれている: {raw!r}")
    raise M1ConfigError(
        f"文字列の並びを期待したが {type(raw).__name__} を受け取った: {raw!r}"
    )


@dataclass(frozen=True, slots=True)
class _FieldSpec:
    """1つの設定キーが対応する（グループ, 属性名, 型変換）。"""

    group: (
        Literal["seam", "convergence", "attribution", "oq27", "trials"] | None
    )
    attr: str
    coerce: Callable[[object], object]


_FIELD_SPECS: dict[str, _FieldSpec] = {
    "layout_file": _FieldSpec(None, "layout_file", _coerce_optional_path),
    "layout_id": _FieldSpec(None, "layout_id", _coerce_optional_str),
    "require_verified_calibration": _FieldSpec(
        "seam", "require_verified_calibration", _coerce_bool
    ),
    "min_valid_depth_px": _FieldSpec("seam", "min_valid_depth_px", _coerce_int),
    "max_depth_spread_mm": _FieldSpec("seam", "max_depth_spread_mm", _coerce_float),
    "floor_margin_mm": _FieldSpec("seam", "floor_margin_mm", _coerce_float),
    "convergence_band_mm": _FieldSpec("convergence", "band_mm", _coerce_optional_float),
    "require_monotonic_tail": _FieldSpec(
        "convergence", "require_monotonic_tail", _coerce_bool
    ),
    "bootstrap_iterations": _FieldSpec(
        "attribution", "bootstrap_iterations", _coerce_int
    ),
    "bootstrap_seed": _FieldSpec("attribution", "bootstrap_seed", _coerce_int),
    "direction_agreement_deg": _FieldSpec(
        "attribution", "direction_agreement_deg", _coerce_float
    ),
    "bias_significance_ratio": _FieldSpec(
        "attribution", "bias_significance_ratio", _coerce_float
    ),
    "residual_significance_ratio": _FieldSpec(
        "attribution", "residual_significance_ratio", _coerce_float
    ),
    "range_band_mm": _FieldSpec("attribution", "range_band_mm", _coerce_float),
    "cpu_saturation_ratio": _FieldSpec(
        "oq27", "cpu_saturation_ratio", _coerce_float
    ),
    "fps_shortfall_ratio": _FieldSpec("oq27", "fps_shortfall_ratio", _coerce_float),
    "min_valid_throws": _FieldSpec("trials", "min_valid_throws", _coerce_int),
    "min_sessions": _FieldSpec("trials", "min_sessions", _coerce_int),
    "require_live_source": _FieldSpec("trials", "require_live_source", _coerce_bool),
    "improvements_applied": _FieldSpec(None, "improvements_applied", _coerce_str_tuple),
    "output_root": _FieldSpec(None, "output_root", _coerce_path),
}

_DEFAULT_OBJECTS = {
    "seam": _DEFAULT_SEAM,
    "convergence": _DEFAULT_CONVERGENCE,
    "attribution": _DEFAULT_ATTRIBUTION,
    "oq27": _DEFAULT_OQ27,
    "trials": _DEFAULT_TRIALS,
}

#: グループに属さないキーの既定値。`layout_file` / `layout_id` に既定が無いのは
#: 「レイアウトを与えないと動かない」ことを構造で表すためである（要件 13.8）。
_TOP_LEVEL_DEFAULTS: dict[str, object] = {
    "layout_file": None,
    "layout_id": None,
    "improvements_applied": (),
    "output_root": Path("var/m1"),
}


def _default_value(spec: _FieldSpec) -> object:
    if spec.group is None:
        return _TOP_LEVEL_DEFAULTS[spec.attr]
    return getattr(_DEFAULT_OBJECTS[spec.group], spec.attr)


def _extract_env(env: Mapping[str, str]) -> dict[str, str]:
    """既知のキーに対応する `STB_M1_` 環境変数のみを取り出す。

    未知の `STB_M1_*` や無関係な環境変数は無視する（上流2 Spec と同じ方針。
    環境変数は本パッケージが知らない用途にも使われ得る）。
    """
    return {
        key: env[ENV_PREFIX + key.upper()]
        for key in _FIELD_SPECS
        if ENV_PREFIX + key.upper() in env
    }


def _load_file(file: Path) -> Mapping[str, object]:
    try:
        content = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise M1ConfigError(
            f"設定ファイルを読み込めない: {file}", {"path": str(file)}
        ) from exc
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise M1ConfigError(
            f"設定ファイルが JSON として解釈できない: {file}", {"path": str(file)}
        ) from exc
    if not isinstance(data, Mapping):
        raise M1ConfigError(
            f"設定ファイルの最上位は JSON オブジェクトでなければならない: {file}",
            {"path": str(file)},
        )
    return data


def _apply_layer(
    values: dict[str, object], raw_layer: Mapping[str, object], *, layer_name: str
) -> None:
    for key, raw in raw_layer.items():
        spec = _FIELD_SPECS.get(key)
        if spec is None:
            raise M1ConfigError(
                f"未知の設定キー {key!r}（{layer_name}で指定）。綴り間違いの可能性がある",
                {"key": key, "layer": layer_name, "known_keys": sorted(_FIELD_SPECS)},
            )
        try:
            values[key] = spec.coerce(raw)
        except M1ConfigError as exc:
            raise M1ConfigError(
                f"設定キー {key!r} の値が解釈できない（{layer_name}で指定）: {exc.detail}",
                {"key": key, "layer": layer_name, "value": raw},
            ) from exc


# --------------------------------------------------------------------------
# 起動時検証（design.md「M1Settings」Preconditions / 要件 13.6）
# --------------------------------------------------------------------------


def _require_positive(values: Mapping[str, object], key: str, *, why: str) -> None:
    value = values[key]
    if not isinstance(value, int | float) or value <= 0:
        raise M1ConfigError(
            f"{key} は正でなければならない: {value!r}（{why}）",
            {"key": key, "value": value},
        )


def _validate_values(values: Mapping[str, object]) -> None:
    """解決後の値が定義域に収まっているかを確かめる。

    ここで拒否するのは**実行を始める前**である（要件 13.6）。投擲実験は
    1回ごとに人が物を投げる作業であり、途中で設定の誤りに気づくと
    その投擲群がまるごと無駄になる。
    """
    _require_positive(
        values, "bootstrap_iterations", why="再抽出を1回も行わないと帰属のばらつきが出ない"
    )
    _require_positive(
        values, "min_valid_throws", why="1投擲も要求しないと下限の意味が無い"
    )
    _require_positive(values, "min_sessions", why="1セッションも要求しないと下限の意味が無い")
    _require_positive(
        values, "min_valid_depth_px", why="有効画素0でも採用すると観測品質の下限が無くなる"
    )
    _require_positive(
        values, "max_depth_spread_mm", why="上限0では全ての観測点が除外される"
    )
    _require_positive(
        values, "bias_significance_ratio", why="比の下限0では偏りが常に有意になる"
    )
    _require_positive(
        values,
        "residual_significance_ratio",
        why="倍率0では残差が常に大きいとみなされ、規則7（判別不能）が消える",
    )
    _require_positive(
        values,
        "cpu_saturation_ratio",
        why="割合0では CPU 使用率が常に飽和とみなされ、規則3（条件付き継続）が消える",
    )
    _require_positive(
        values,
        "fps_shortfall_ratio",
        why="割合0では実処理 fps がどれだけ落ちても飽和とみなされない",
    )
    _require_positive(
        values, "range_band_mm", why="幅0の距離帯は作れない（要件 6.11）"
    )

    direction = values["direction_agreement_deg"]
    if not isinstance(direction, int | float) or not 0 < direction < 90:
        raise M1ConfigError(
            f"direction_agreement_deg は 0 より大きく 90 より小さくなければならない: "
            f"{direction!r}（90 以上にすると、どの向きとも整合すると判定され"
            "World 固定方向とカメラ視線方向を区別できなくなる）",
            {"key": "direction_agreement_deg", "value": direction},
        )

    band = values["convergence_band_mm"]
    if band is not None and (not isinstance(band, int | float) or band <= 0):
        raise M1ConfigError(
            f"convergence_band_mm は正でなければならない: {band!r}"
            "（レイアウトの暫定許容窓に揃えるなら None を指定する）",
            {"key": "convergence_band_mm", "value": band},
        )
