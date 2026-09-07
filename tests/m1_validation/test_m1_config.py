"""設定の解決と起動時検証の検証（タスク 1.5 / 要件 5.10, 9.9, 13.5, 13.6, 13.7）。

観測可能な完了状態（tasks.md 1.5）を固定する:

- **4段の解決順序**（既定値 → 設定ファイル → 環境変数 → 実行時指定）が
  優先度どおりに効く
- **不正値が起動時に拒否される**

あわせて design.md「M1Settings」と要件が定める点も固定する:

- **キャリブレーション検証の要求が既定で有効**である（要件 2.1）
- 環境変数の接頭辞が上流（`STB_SF_` / `STB_FOT_`）と衝突しない
- 解決結果を表示でき、**既定値が暫定の評価候補であって必須性能ではない**旨が
  そこに含まれる（要件 13.5 / 13.7）

ファイル名に `m1_` を冠しているのは `tests/prediction_core/test_config.py` と
衝突するためである（tasks.md「Implementation Notes」タスク1.1）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from m1_validation import config as config_module
from m1_validation.config import (
    ENV_PREFIX,
    AttributionConfig,
    ConvergenceConfig,
    M1Settings,
    SeamConfig,
    TrialLimits,
)
from m1_validation.errors import M1ConfigError
from m1_validation.layout import LAYOUT_FORMAT_VERSION

ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "collections",
    "dataclasses",
    "enum",
    "json",
    "pathlib",
    "typing",
    "m1_validation",
    "prediction_core",
}


def _layout_entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": [-2000.0, 0.0, 1500.0],
        "release_height_mm": 1500.0,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": [0.0, 0.0],
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": [0.0, -1500.0, 1000.0],
        "notes": "仮値。確定ではない。",
    }
    base.update(overrides)
    return base


@pytest.fixture
def layout_file(tmp_path: Path) -> Path:
    path = tmp_path / "layout.json"
    path.write_text(
        json.dumps(
            {"format_version": LAYOUT_FORMAT_VERSION, "layouts": [_layout_entry()]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _resolve(layout_file: Path, **kwargs: object) -> M1Settings:
    """既定の呼び出し（`layout_file` だけ渡す）を短く書くための補助。"""
    overrides: dict[str, object] = {"layout_file": str(layout_file)}
    overrides.update(kwargs.pop("overrides", {}))  # type: ignore[arg-type]
    return M1Settings.resolve(
        file=kwargs.pop("file", None),  # type: ignore[arg-type]
        env=kwargs.pop("env", {}),  # type: ignore[arg-type]
        overrides=overrides,
    )


class TestDefaults:
    def test_calibration_verification_is_required_by_default(self, layout_file: Path) -> None:
        """**既定でキャリブレーション未検証を拒否する**（要件 2.1）。

        既定を逆にすると、検証を通していない座標系のまま実測が進み、
        得られた誤差が系統誤差か予測誤差か分離できなくなる。
        """
        assert _resolve(layout_file).seam.require_verified_calibration is True

    def test_defaults_match_the_design(self, layout_file: Path) -> None:
        settings = _resolve(layout_file)
        assert settings.seam == SeamConfig(
            require_verified_calibration=True,
            min_valid_depth_px=8,
            max_depth_spread_mm=200.0,
            floor_margin_mm=-50.0,
        )
        assert settings.convergence == ConvergenceConfig(
            band_mm=None, require_monotonic_tail=True
        )
        assert settings.attribution == AttributionConfig(
            bootstrap_iterations=200,
            bootstrap_seed=0,
            direction_agreement_deg=30.0,
            bias_significance_ratio=1.0,
        )
        assert settings.trials == TrialLimits(
            min_valid_throws=20, min_sessions=2, require_live_source=True
        )
        assert settings.improvements_applied == ()
        assert settings.output_root == Path("var/m1")

    def test_bootstrap_seed_is_fixed_for_determinism(self, layout_file: Path) -> None:
        """乱数種を既定で固定する（要件 12.4 / 3.7 の再現性）。"""
        assert _resolve(layout_file).attribution.bootstrap_seed == 0


class TestFourLayerResolution:
    """既定値 → 設定ファイル → 環境変数 → 実行時指定（要件 13.5）。"""

    def test_each_layer_beats_the_one_below(self, tmp_path: Path, layout_file: Path) -> None:
        config_path = tmp_path / "m1.json"
        config_path.write_text(json.dumps({"min_valid_throws": 30}), encoding="utf-8")

        # 1. 既定値のみ
        assert _resolve(layout_file).trials.min_valid_throws == 20

        # 2. 設定ファイル > 既定値
        settings = _resolve(layout_file, file=config_path)
        assert settings.trials.min_valid_throws == 30

        # 3. 環境変数 > 設定ファイル
        settings = _resolve(
            layout_file, file=config_path, env={f"{ENV_PREFIX}MIN_VALID_THROWS": "40"}
        )
        assert settings.trials.min_valid_throws == 40

        # 4. 実行時指定 > 環境変数
        settings = _resolve(
            layout_file,
            file=config_path,
            env={f"{ENV_PREFIX}MIN_VALID_THROWS": "40"},
            overrides={"min_valid_throws": 50},
        )
        assert settings.trials.min_valid_throws == 50

    def test_untouched_keys_keep_their_default(self, tmp_path: Path, layout_file: Path) -> None:
        """1つ上書きしても他は既定のまま（層の適用がキー単位である）。"""
        config_path = tmp_path / "m1.json"
        config_path.write_text(json.dumps({"min_valid_throws": 30}), encoding="utf-8")
        settings = _resolve(layout_file, file=config_path)
        assert settings.trials.min_sessions == 2
        assert settings.attribution.bootstrap_iterations == 200


class TestEnvironmentPrefix:
    def test_prefix_does_not_collide_with_upstream(self) -> None:
        """上流の接頭辞と衝突しない（design.md「M1Settings」Integration）。"""
        assert ENV_PREFIX == "STB_M1_"
        assert ENV_PREFIX not in ("STB_SF_", "STB_FOT_")

    def test_upstream_variables_are_not_read(self, layout_file: Path) -> None:
        """`STB_SF_*` / `STB_FOT_*` を拾わない（同名キーの取り違えを防ぐ）。"""
        settings = _resolve(
            layout_file,
            env={"STB_SF_MIN_VALID_THROWS": "99", "STB_FOT_MIN_VALID_THROWS": "98"},
        )
        assert settings.trials.min_valid_throws == 20

    def test_unknown_prefixed_variable_is_ignored(self, layout_file: Path) -> None:
        """未知の `STB_M1_*` は無視する（上流と同じ方針）。

        環境変数は本パッケージが知らない用途にも使われ得るので、
        ファイル・実行時指定と違って未知キーで落とさない。
        """
        settings = _resolve(layout_file, env={f"{ENV_PREFIX}NOT_A_SETTING": "x"})
        assert settings.trials.min_valid_throws == 20


class TestStartupValidation:
    """不正な設定を**実行開始前に**拒否する（要件 13.6）。"""

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("bootstrap_iterations", 0),
            ("bootstrap_iterations", -1),
            ("direction_agreement_deg", 0.0),
            ("direction_agreement_deg", 90.0),
            ("direction_agreement_deg", 120.0),
            ("min_valid_throws", 0),
            ("min_sessions", 0),
            ("min_valid_depth_px", 0),
            ("max_depth_spread_mm", 0.0),
            ("bias_significance_ratio", 0.0),
            ("convergence_band_mm", 0.0),
            ("convergence_band_mm", -1.0),
        ],
    )
    def test_out_of_domain_value_is_rejected(
        self, layout_file: Path, key: str, value: object
    ) -> None:
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, overrides={key: value})

    def test_rejection_names_the_key_and_the_value(self, layout_file: Path) -> None:
        with pytest.raises(M1ConfigError) as exc:
            _resolve(layout_file, overrides={"bootstrap_iterations": 0})
        assert "bootstrap_iterations" in str(exc.value)

    def test_unknown_key_in_overrides_is_rejected(self, layout_file: Path) -> None:
        with pytest.raises(M1ConfigError) as exc:
            _resolve(layout_file, overrides={"min_valid_throw": 30})
        assert "min_valid_throw" in str(exc.value)

    def test_unknown_key_in_file_is_rejected(self, tmp_path: Path, layout_file: Path) -> None:
        config_path = tmp_path / "m1.json"
        config_path.write_text(json.dumps({"typo_key": 1}), encoding="utf-8")
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, file=config_path)

    def test_malformed_config_file_fails_cleanly(
        self, tmp_path: Path, layout_file: Path
    ) -> None:
        config_path = tmp_path / "m1.json"
        config_path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, file=config_path)

    def test_missing_config_file_fails_cleanly(
        self, tmp_path: Path, layout_file: Path
    ) -> None:
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, file=tmp_path / "absent.json")


class TestLayoutIsRequired:
    """レイアウトはコードに無いので、設定として必ず与える（要件 13.8）。"""

    def test_resolution_without_a_layout_file_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError) as exc:
            M1Settings.resolve(file=None, env={}, overrides={})
        assert "layout_file" in str(exc.value)

    def test_layout_is_loaded_from_the_file(self, layout_file: Path) -> None:
        settings = _resolve(layout_file)
        assert settings.layout.layout_id == "throw-a"
        assert settings.layout.position_tolerance_mm == pytest.approx(67.5)

    def test_layout_id_selects_among_several(self, tmp_path: Path) -> None:
        path = tmp_path / "layout.json"
        path.write_text(
            json.dumps(
                {
                    "format_version": LAYOUT_FORMAT_VERSION,
                    "layouts": [
                        _layout_entry(layout_id="throw-a"),
                        _layout_entry(layout_id="throw-b", throw_direction_deg=90.0),
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings = _resolve(path, overrides={"layout_id": "throw-b"})
        assert settings.layout.throw_direction_deg == 90.0

    def test_ambiguous_layout_selection_is_rejected(self, tmp_path: Path) -> None:
        """複数あるのに `layout_id` 未指定なら拒否される（タスク 1.4 の契約が効く）。"""
        path = tmp_path / "layout.json"
        path.write_text(
            json.dumps(
                {
                    "format_version": LAYOUT_FORMAT_VERSION,
                    "layouts": [
                        _layout_entry(layout_id="throw-a"),
                        _layout_entry(layout_id="throw-b"),
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with pytest.raises(M1ConfigError):
            _resolve(path)

    def test_broken_layout_is_rejected_here_too(self, tmp_path: Path) -> None:
        """レイアウト側の不正も設定解決の時点で出る（起動時に拒否される）。"""
        path = tmp_path / "layout.json"
        path.write_text(
            json.dumps(
                {
                    "format_version": LAYOUT_FORMAT_VERSION,
                    "layouts": [_layout_entry(object_diameter_mm=250.0)],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with pytest.raises(M1ConfigError):
            _resolve(path)


class TestDescribe:
    """解決結果を表示できる（要件 13.5）。"""

    def test_describe_is_json_serialisable(self, layout_file: Path) -> None:
        payload = _resolve(layout_file).describe()
        assert json.loads(json.dumps(payload, ensure_ascii=False))

    def test_describe_shows_the_resolved_values(self, layout_file: Path) -> None:
        payload = _resolve(layout_file, overrides={"min_valid_throws": 33})
        described = payload.describe()
        assert described["trials"]["min_valid_throws"] == 33  # type: ignore[index]
        assert described["layout"]["layout_id"] == "throw-a"  # type: ignore[index]

    def test_describe_shows_the_effective_convergence_band(self, layout_file: Path) -> None:
        """`band_mm=None` はレイアウトの暫定許容窓に揃う（要件 5.8）。

        解決結果の表示に**実際に使われる値**が出ないと、`null` を見た人が
        「収束判定が無効」と誤解する。設定された値と実効値の両方を出す。
        """
        described = _resolve(layout_file).describe()["convergence"]
        assert described["band_mm"] is None  # type: ignore[index]
        assert described["effective_band_mm"] == pytest.approx(67.5)  # type: ignore[index]

    def test_explicit_band_is_used_as_is(self, layout_file: Path) -> None:
        described = _resolve(layout_file, overrides={"convergence_band_mm": 50.0})
        payload = described.describe()["convergence"]
        assert payload["band_mm"] == 50.0  # type: ignore[index]
        assert payload["effective_band_mm"] == 50.0  # type: ignore[index]

    def test_describe_says_the_defaults_are_provisional(self, layout_file: Path) -> None:
        """**暫定の評価候補であって必須性能ではない**旨を含める（要件 13.7）。

        `--print-settings` を見た人が 20 回・67.5mm・30° を必須条件だと
        受け取ると、そこから逆算した「不足」判断が独り歩きする。
        design.md「M1Settings」Risks が名指しする3つを明示的に挙げる。
        """
        notice = str(_resolve(layout_file).describe()["provisional_notice"])
        assert "暫定" in notice
        assert "必須性能ではない" in notice
        for named in ("min_valid_throws", "band_mm", "direction_agreement_deg"):
            assert named in notice


class TestEffectiveConvergenceBand:
    """収束帯域の実効値は1箇所で導出する（要件 5.8）。"""

    def test_none_falls_back_to_the_layout_tolerance(self, layout_file: Path) -> None:
        settings = _resolve(layout_file)
        assert settings.convergence.band_mm is None
        assert settings.effective_convergence_band_mm == pytest.approx(
            settings.layout.position_tolerance_mm
        )

    def test_explicit_value_wins(self, layout_file: Path) -> None:
        settings = _resolve(layout_file, overrides={"convergence_band_mm": 40.0})
        assert settings.effective_convergence_band_mm == 40.0

    def test_derivation_lives_in_one_place(self, layout_file: Path) -> None:
        """利用側が `band_mm or layout.position_tolerance_mm` と書き散らさない。

        導出が散ると、設定した値と実際に使う値が食い違う経路が増える。
        """
        settings = _resolve(layout_file)
        assert settings.describe()["convergence"]["effective_band_mm"] == (  # type: ignore[index]
            settings.effective_convergence_band_mm
        )


class TestImmutability:
    @pytest.mark.parametrize(
        "cls", [SeamConfig, ConvergenceConfig, AttributionConfig, TrialLimits, M1Settings]
    )
    def test_is_a_frozen_dataclass(self, cls: type) -> None:
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_resolved_settings_cannot_be_modified(self, layout_file: Path) -> None:
        """解決後の設定は不変である（design.md「M1Settings」Postconditions）。"""
        settings = _resolve(layout_file)
        with pytest.raises(dataclasses.FrozenInstanceError):
            settings.output_root = Path("/tmp")  # type: ignore[misc]


class TestCoercion:
    def test_strings_from_the_environment_are_coerced(self, layout_file: Path) -> None:
        settings = _resolve(
            layout_file,
            env={
                f"{ENV_PREFIX}BOOTSTRAP_ITERATIONS": "500",
                f"{ENV_PREFIX}REQUIRE_VERIFIED_CALIBRATION": "false",
                f"{ENV_PREFIX}DIRECTION_AGREEMENT_DEG": "45.5",
            },
        )
        assert settings.attribution.bootstrap_iterations == 500
        assert settings.seam.require_verified_calibration is False
        assert settings.attribution.direction_agreement_deg == 45.5

    def test_improvements_applied_accepts_a_list(self, layout_file: Path) -> None:
        settings = _resolve(
            layout_file, overrides={"improvements_applied": ["13.2-a", "13.2-b"]}
        )
        assert settings.improvements_applied == ("13.2-a", "13.2-b")

    def test_non_numeric_value_is_rejected(self, layout_file: Path) -> None:
        with pytest.raises(M1ConfigError):
            _resolve(layout_file, env={f"{ENV_PREFIX}BOOTSTRAP_ITERATIONS": "たくさん"})


class TestLayerThree:
    """`config` は `errors` / `types` / `layout` までしか依存しない。"""

    def test_module_imports_only_allowed_roots(self) -> None:
        source = Path(inspect.getfile(config_module)).read_text(encoding="utf-8")
        roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert roots <= ALLOWED_IMPORT_ROOTS, sorted(roots - ALLOWED_IMPORT_ROOTS)

    def test_module_does_not_import_layers_above_itself(self) -> None:
        source = Path(inspect.getfile(config_module)).read_text(encoding="utf-8")
        modules = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        forbidden = {
            "m1_validation.upstream",
            "m1_validation.seam",
            "m1_validation.runner",
            "m1_validation.report",
        }
        assert modules.isdisjoint(forbidden), sorted(modules & forbidden)
