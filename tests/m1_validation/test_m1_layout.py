"""投擲レイアウトの外部化の検証（タスク 1.4 / 要件 4.3, 5.6, 13.7, 13.8, 13.9）。

観測可能な完了状態（tasks.md 1.4）を固定する:

- **レイアウトファイルから読み込んだ値で許容窓が導出される**
- **寸法の大小関係が崩れる入力が起動時に拒否される**

あわせて design.md「ThrowLayout」と `research.md` Decision 4 が定める点も固定する:

- **投擲位置を2箇所以上にできる形**である（誤差帰属で World 固定方向と
  カメラ視線方向が縮退したときの対策。後から追加できるようにしておく）
- `position_tolerance_mm` が**合否条件ではない**旨が docstring に書かれている
  （design.md「ThrowLayout」Risks: 一人歩きしやすい）

ファイル名に `m1_` を冠しているのは本ディレクトリの規約である
（tasks.md「Implementation Notes」タスク1.1）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from m1_validation import layout as layout_module
from m1_validation.errors import M1ConfigError
from m1_validation.layout import (
    LAYOUT_FORMAT_VERSION,
    ThrowLayout,
    load_layout,
    load_layouts,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_LAYOUT_PATH = (
    REPO_ROOT / ".kiro" / "specs" / "m1-prediction-validation" / "layout.example.json"
)

#: `layout` が import してよい先（design.md「Dependency Direction」:
#: errors → types → layout）。
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


def _layout_dict(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": [-1500.0, 0.0, 1400.0],
        "release_height_mm": 1400.0,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": [0.0, 0.0],
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": [0.0, -1200.0, 1000.0],
        "notes": "仮値。確定ではない。",
    }
    base.update(overrides)
    return base


def _write_layout_file(tmp_path: Path, *layouts: dict[str, object], **top: object) -> Path:
    payload: dict[str, object] = {
        "format_version": LAYOUT_FORMAT_VERSION,
        "layouts": list(layouts) if layouts else [_layout_dict()],
    }
    payload.update(top)
    path = tmp_path / "layout.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _make_layout(**overrides: object) -> ThrowLayout:
    base: dict[str, object] = {
        "layout_id": "throw-a",
        "release_position_world_mm": (-1500.0, 0.0, 1400.0),
        "release_height_mm": 1400.0,
        "throw_direction_deg": 0.0,
        "standby_position_world_mm": (0.0, 0.0),
        "object_diameter_mm": 65.0,
        "aperture_diameter_mm": 200.0,
        "camera_position_world_mm": (0.0, -1200.0, 1000.0),
        "notes": "仮値。確定ではない。",
    }
    base.update(overrides)
    return ThrowLayout(**base)  # type: ignore[arg-type]


class TestShape:
    def test_is_a_frozen_slotted_dataclass(self) -> None:
        assert dataclasses.is_dataclass(ThrowLayout)
        assert ThrowLayout.__dataclass_params__.frozen  # type: ignore[attr-defined]

    def test_carries_every_field_the_requirement_names(self) -> None:
        """投擲位置・方向・待機位置・対象物の寸法・リリース高さ（要件 13.8）と
        開口寸法・カメラ位置（要件 6.3 / NFR-5 の窓）。"""
        names = {field.name for field in dataclasses.fields(ThrowLayout)}
        assert names == {
            "layout_id",
            "release_position_world_mm",
            "release_height_mm",
            "throw_direction_deg",
            "standby_position_world_mm",
            "object_diameter_mm",
            "aperture_diameter_mm",
            "camera_position_world_mm",
            "notes",
        }

    def test_release_position_may_be_unknown(self) -> None:
        """投擲位置は分かる場合のみ与える（design.md「ThrowLayout」）。"""
        assert _make_layout(release_position_world_mm=None).release_position_world_mm is None

    def test_value_equality(self) -> None:
        assert _make_layout() == _make_layout()
        assert _make_layout() != _make_layout(layout_id="throw-b")


class TestPositionTolerance:
    def test_window_is_the_aperture_radius_minus_half_the_object(self) -> None:
        """開口 φ200 / 対象 φ65 なら (200 − 65) / 2 = 67.5mm。"""
        assert _make_layout().position_tolerance_mm == pytest.approx(67.5)

    def test_window_follows_the_layout_not_a_constant(self) -> None:
        """数値をコードへ埋め込まない（要件 13.8）。レイアウトを変えれば窓も変わる。"""
        wider = _make_layout(aperture_diameter_mm=300.0)
        assert wider.position_tolerance_mm == pytest.approx(117.5)

    def test_docstring_says_it_is_not_a_pass_fail_criterion(self) -> None:
        """**合否条件ではない**旨が docstring にある（design.md「ThrowLayout」Risks）。

        この値は「暫定目標値」であって NFR-5 の合否条件ではない。放っておくと
        一人歩きして「67.5mm を超えたから失敗」という判断に使われる——
        docstring への明記が design.md の挙げた唯一の緩和策なので、
        それが消えていないことをここで固定する。
        """
        doc = ThrowLayout.position_tolerance_mm.__doc__ or ""
        assert "暫定" in doc
        assert "合否条件ではない" in doc


class TestConstructionRejectsBrokenDimensions:
    """寸法の大小関係が崩れる入力を拒否する（tasks.md 1.4 の完了状態）。"""

    @pytest.mark.parametrize("height", [0.0, -1.0])
    def test_non_positive_release_height_is_rejected(self, height: float) -> None:
        """リリース高さは正でなければ外挿の基準にならない（要件 4.3）。"""
        with pytest.raises(M1ConfigError):
            _make_layout(release_height_mm=height)

    def test_object_wider_than_the_aperture_is_rejected(self) -> None:
        """対象が開口より大きいと、そもそも入らない。"""
        with pytest.raises(M1ConfigError):
            _make_layout(object_diameter_mm=250.0, aperture_diameter_mm=200.0)

    def test_object_equal_to_the_aperture_is_rejected(self) -> None:
        """等しい場合も拒否する。許容窓が 0 になり、意味のある評価にならない。"""
        with pytest.raises(M1ConfigError):
            _make_layout(object_diameter_mm=200.0, aperture_diameter_mm=200.0)

    def test_non_positive_object_diameter_is_rejected(self) -> None:
        with pytest.raises(M1ConfigError):
            _make_layout(object_diameter_mm=0.0)

    def test_empty_layout_id_is_rejected(self) -> None:
        """識別子が無いと、どのレイアウトで撮った投擲かを後から分けられない。"""
        with pytest.raises(M1ConfigError):
            _make_layout(layout_id="  ")

    def test_rejection_names_the_offending_values(self) -> None:
        with pytest.raises(M1ConfigError) as exc:
            _make_layout(object_diameter_mm=250.0, aperture_diameter_mm=200.0)
        assert exc.value.context.get("object_diameter_mm") == 250.0
        assert exc.value.context.get("aperture_diameter_mm") == 200.0


class TestLoadingFromFile:
    """レイアウトをファイルから読み込める（要件 13.8）。"""

    def test_single_layout_file_is_loaded(self, tmp_path: Path) -> None:
        path = _write_layout_file(tmp_path)
        loaded = load_layout(path)
        assert loaded.layout_id == "throw-a"
        assert loaded.position_tolerance_mm == pytest.approx(67.5)

    def test_tuples_are_reconstructed_from_json_arrays(self, tmp_path: Path) -> None:
        """JSON の配列は tuple として持つ（値オブジェクトの不変性を保つ）。"""
        loaded = load_layout(_write_layout_file(tmp_path))
        assert loaded.standby_position_world_mm == (0.0, 0.0)
        assert loaded.camera_position_world_mm == (0.0, -1200.0, 1000.0)

    def test_two_or_more_throw_positions_fit_in_one_file(self, tmp_path: Path) -> None:
        """**投擲位置を2箇所以上にできる形**である（`research.md` Decision 4 Follow-up）。

        投擲位置を固定すると World 固定方向とカメラ視線方向が縮退し、
        誤差の帰属が判別不能になる。後から2箇所目を足せることが要件である。
        """
        path = _write_layout_file(
            tmp_path,
            _layout_dict(layout_id="throw-a"),
            _layout_dict(
                layout_id="throw-b",
                release_position_world_mm=[0.0, -1500.0, 1400.0],
                throw_direction_deg=90.0,
            ),
        )
        layouts = load_layouts(path)
        assert [item.layout_id for item in layouts] == ["throw-a", "throw-b"]
        assert layouts[1].throw_direction_deg == 90.0

    def test_layout_is_selected_by_id(self, tmp_path: Path) -> None:
        path = _write_layout_file(
            tmp_path, _layout_dict(layout_id="throw-a"), _layout_dict(layout_id="throw-b")
        )
        assert load_layout(path, layout_id="throw-b").layout_id == "throw-b"

    def test_ambiguous_selection_is_rejected_instead_of_picking_the_first(
        self, tmp_path: Path
    ) -> None:
        """複数あるのに指定が無ければ拒否する。

        黙って先頭を選ぶと、**別の投擲位置のレイアウトで撮ったことになった
        投擲群**が生まれる。帰属は投擲位置ごとに向きが変わることを使って
        判別するので、これは結論を直接ねじ曲げる。
        """
        path = _write_layout_file(
            tmp_path, _layout_dict(layout_id="throw-a"), _layout_dict(layout_id="throw-b")
        )
        with pytest.raises(M1ConfigError) as exc:
            load_layout(path)
        assert "throw-a" in str(exc.value)
        assert "throw-b" in str(exc.value)

    def test_unknown_layout_id_lists_what_exists(self, tmp_path: Path) -> None:
        path = _write_layout_file(tmp_path, _layout_dict(layout_id="throw-a"))
        with pytest.raises(M1ConfigError) as exc:
            load_layout(path, layout_id="throw-z")
        assert "throw-a" in str(exc.value)

    def test_duplicate_layout_id_is_rejected(self, tmp_path: Path) -> None:
        """同じ識別子が2つのレイアウトを指す状態を許さない。"""
        path = _write_layout_file(
            tmp_path, _layout_dict(layout_id="throw-a"), _layout_dict(layout_id="throw-a")
        )
        with pytest.raises(M1ConfigError):
            load_layouts(path)

    def test_broken_dimensions_are_rejected_at_load_time(self, tmp_path: Path) -> None:
        """**起動時に拒否する**（tasks.md 1.4 の完了状態）。読み込んだ後で気づかない。"""
        path = _write_layout_file(
            tmp_path, _layout_dict(object_diameter_mm=250.0, aperture_diameter_mm=200.0)
        )
        with pytest.raises(M1ConfigError):
            load_layouts(path)

    def test_unknown_format_version_is_rejected(self, tmp_path: Path) -> None:
        """未知の形式版は内容を推測して読まない（上流3 Spec と同じ方針）。"""
        path = _write_layout_file(tmp_path, format_version="99.0")
        with pytest.raises(M1ConfigError) as exc:
            load_layouts(path)
        assert "99.0" in str(exc.value)

    def test_missing_file_fails_cleanly(self, tmp_path: Path) -> None:
        with pytest.raises(M1ConfigError):
            load_layouts(tmp_path / "does-not-exist.json")

    def test_malformed_json_fails_cleanly(self, tmp_path: Path) -> None:
        path = tmp_path / "layout.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(M1ConfigError):
            load_layouts(path)

    def test_missing_required_key_is_rejected(self, tmp_path: Path) -> None:
        incomplete = _layout_dict()
        del incomplete["release_height_mm"]
        path = _write_layout_file(tmp_path, incomplete)
        with pytest.raises(M1ConfigError) as exc:
            load_layouts(path)
        assert "release_height_mm" in str(exc.value)

    def test_unknown_key_is_rejected(self, tmp_path: Path) -> None:
        """綴り間違いが黙って無視されない（`throw_direction_deg` を `deg` と書く等）。"""
        path = _write_layout_file(tmp_path, _layout_dict(throw_direction="0.0"))
        with pytest.raises(M1ConfigError) as exc:
            load_layouts(path)
        assert "throw_direction" in str(exc.value)


class TestShippedExample:
    """同梱の例ファイルが、そのまま読めて仮値である旨を運ぶ（要件 13.7, 13.9）。"""

    def test_example_file_loads(self) -> None:
        layouts = load_layouts(EXAMPLE_LAYOUT_PATH)
        assert layouts

    def test_example_shows_two_throw_positions(self) -> None:
        """2箇所目を足せる形の実例を同梱する（`research.md` Decision 4 Follow-up）。"""
        layouts = load_layouts(EXAMPLE_LAYOUT_PATH)
        assert len(layouts) >= 2
        directions = {item.throw_direction_deg for item in layouts}
        assert len(directions) >= 2, "投擲方向が全部同じでは縮退対策の実例にならない"

    def test_every_example_layout_says_the_numbers_are_provisional(self) -> None:
        """**既定値が仮値であり確定ではない**旨を併記する（要件 13.7 / 13.9）。

        数値はコードではなくデータ側にあるので、注記もデータ側に置く。
        これが無いと φ65 / φ200 が確定値として一人歩きする。
        """
        for item in load_layouts(EXAMPLE_LAYOUT_PATH):
            assert "仮値" in item.notes, item.layout_id


class TestLayerTwo:
    """`layout` は `errors` / `types` までしか依存しない（design.md「Dependency Direction」）。"""

    def test_module_imports_only_allowed_roots(self) -> None:
        source = Path(inspect.getfile(layout_module)).read_text(encoding="utf-8")
        roots = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        assert roots <= ALLOWED_IMPORT_ROOTS, sorted(roots - ALLOWED_IMPORT_ROOTS)

    def test_module_does_not_import_layers_above_itself(self) -> None:
        source = Path(inspect.getfile(layout_module)).read_text(encoding="utf-8")
        modules = {
            node.module
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        forbidden = {
            "m1_validation.config",
            "m1_validation.upstream",
            "m1_validation.seam",
            "m1_validation.runner",
        }
        assert modules.isdisjoint(forbidden), sorted(modules & forbidden)
