"""ビルド構成の静的境界検査（タスク 1.3、要件 1.3, 1.4, 1.6, 1.7, 16.3, 16.7）。

`firmware/platformio.ini` / `firmware/CMakeLists.txt` /
`firmware/lib/drivetrain_control/CMakeLists.txt` / `firmware/src/build_profile.hpp` /
`firmware/sdkconfig.defaults.production` をテキストとして読み、
`tests/prediction_core/test_boundaries.py` /
`tests/trajectory_sim/test_trajectory_sim_boundaries.py` と同じ方針
（検査ロジックを純粋関数として切り出し、実ファイルへの適用と「違反を含む
架空の入力文字列」への適用の両方をテストする）で、以下の8点を固定する。

1. **ソース集合の一致**: `lib/drivetrain_control/CMakeLists.txt` の
   `idf_component_register(SRCS ...)` が列挙するファイル集合と、
   `lib/drivetrain_control/src/*.cpp` の実体集合が一致すること
   （タスク1.1 Risk R2: 「`SRCS` 更新漏れで `native` だけ通る」の回帰）。
2. **テスト専用ライブラリの非混入**: ルート `CMakeLists.txt` の
   `EXTRA_COMPONENT_DIRS` が `lib` ディレクトリ全体ではなく
   `lib/drivetrain_control` を直接指しており、将来追加されるテスト専用
   ライブラリ（`lib/test_support/` 等）が組込みビルドの探索対象へ
   自動的に混入しない構造になっていること（要件16.3）。
3. **3つのビルド環境の存在とホストテスト環境の実機非依存**（要件1.1, 1.5）。
4. **テレオペ用と本番用のビルド構成マクロの排他性**（要件1.3）。
5. **本番用ビルドの無線非依存と無線無効化設定**（要件1.4）。
6. **ファーム2環境の対象基板が classic ESP32 に固定され、他系統の環境が
   定義されていないこと**（要件1.6）。
7. **外部プラットフォーム定義が更新で内容の変化しない成果物として固定
   されていること**（要件1.7）。
8. **ホストテストと実機テストの振り分け設定の存在**（要件16.7）。

**本ファイルは PlatformIO/CMake/ESP-IDF のいずれも実行しない**（`pio run` /
`cmake` を呼び出さない）。`configparser` による INI 解析と正規表現による
テキスト走査のみで完結する、上流2ファイルと同じ「静的走査」の流儀を
ファームウェアのビルド構成へ適用したものである。駆動制御ロジック
（逆運動学・PID・保護状態機械等）の複製ではなく、あくまでビルド構成の
構造検査であるため、`.kiro/steering/tech.md` 開発標準3（二重実装の禁止）
には当たらない。

**観測可能な完了状態（タスク文言どおり）**: 「上記すべての違反をわざと
作り込んだ場合にテストが赤くなり、現状の構成では緑になる」ことを、
実ファイルを書き換える代わりに、検査ロジックを関数として切り出し、
その関数へ「違反を含む架空の入力文字列」を渡すテストケース群
（`test_detects_*` 系）と、「現状の実ファイル」を渡すテストケース群の
両方で証明する。
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_DIR = REPO_ROOT / "firmware"
PLATFORMIO_INI_PATH = FIRMWARE_DIR / "platformio.ini"
ROOT_CMAKE_PATH = FIRMWARE_DIR / "CMakeLists.txt"
LIB_CMAKE_PATH = FIRMWARE_DIR / "lib" / "drivetrain_control" / "CMakeLists.txt"
LIB_SRC_DIR = FIRMWARE_DIR / "lib" / "drivetrain_control" / "src"
BUILD_PROFILE_HPP_PATH = FIRMWARE_DIR / "src" / "build_profile.hpp"
SDKCONFIG_PRODUCTION_PATH = FIRMWARE_DIR / "sdkconfig.defaults.production"
TEST_NATIVE_DIR = FIRMWARE_DIR / "test" / "native"
TEST_EMBEDDED_DIR = FIRMWARE_DIR / "test" / "embedded"
LIB_INCLUDE_ROOT_DIR = FIRMWARE_DIR / "lib" / "drivetrain_control" / "include" / "drivetrain_control"

PLATFORMIO_INI_TEXT = PLATFORMIO_INI_PATH.read_text(encoding="utf-8")
ROOT_CMAKE_TEXT = ROOT_CMAKE_PATH.read_text(encoding="utf-8")
LIB_CMAKE_TEXT = LIB_CMAKE_PATH.read_text(encoding="utf-8")
BUILD_PROFILE_HPP_TEXT = BUILD_PROFILE_HPP_PATH.read_text(encoding="utf-8")
SDKCONFIG_PRODUCTION_TEXT = SDKCONFIG_PRODUCTION_PATH.read_text(encoding="utf-8")


def _load_ini(text: str) -> configparser.ConfigParser:
    """`platformio.ini` 形式のテキストを `configparser.ConfigParser` へ読み込む。

    `read_string` のみを用い、実ファイルへは触れない（架空の入力文字列にも
    同じ経路で適用できるようにするため）。
    """
    cp = configparser.ConfigParser()
    cp.read_string(text)
    return cp


# ---------------------------------------------------------------------------
# 1. ソース集合の一致（lib/drivetrain_control の CMakeLists.txt vs 実体）
# ---------------------------------------------------------------------------


def parse_cmake_srcs(cmake_text: str) -> list[str]:
    """`idf_component_register(SRCS "a.cpp" "b.cpp" ...)` の SRCS 引数を抽出する。"""
    match = re.search(
        r"idf_component_register\s*\(.*?SRCS\s+((?:\"[^\"]*\"\s*)+)", cmake_text, re.S
    )
    if match is None:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def find_source_set_mismatch(cmake_text: str, actual_relative_paths: set[str]) -> list[str]:
    """CMakeLists.txt の SRCS 列挙と実ソースファイル集合のずれを検出する。

    ずれた項目を人間可読な文字列の列として返す。空列であればずれなし。
    """
    declared = set(parse_cmake_srcs(cmake_text))
    missing_from_cmake = actual_relative_paths - declared
    missing_from_disk = declared - actual_relative_paths
    violations = [f"CMakeLists.txt に列挙されていない: {p}" for p in sorted(missing_from_cmake)]
    violations += [f"実体が存在しない: {p}" for p in sorted(missing_from_disk)]
    return violations


def _actual_lib_source_paths() -> set[str]:
    # design.md の File Structure Plan は L7 保護層以降を `src/protection/*.cpp`
    # のようなサブディレクトリへ配置する（drivetrain-core タスク 5.1 以降）。
    # 非再帰の `glob("*.cpp")` はこれらを走査対象から取りこぼし、CMakeLists.txt
    # に正しく列挙されているファイルを「実体が存在しない」と誤検知する。
    # `rglob` でサブディレクトリを含め、`LIB_SRC_DIR` からの相対パス
    # （サブディレクトリ部分を保持したもの）を返す。
    return {
        f"src/{p.relative_to(LIB_SRC_DIR).as_posix()}" for p in LIB_SRC_DIR.rglob("*.cpp")
    }


def test_lib_cmakelists_srcs_matches_actual_source_files() -> None:
    """`lib/drivetrain_control/CMakeLists.txt` の SRCS 集合が実体と一致する。"""
    violations = find_source_set_mismatch(LIB_CMAKE_TEXT, _actual_lib_source_paths())
    assert violations == [], f"lib/drivetrain_control の SRCS 集合が実体とずれている: {violations}"


def test_actual_lib_source_set_is_non_empty() -> None:
    """走査対象そのものが空振りでないことを確認する（前提の健全性）。"""
    assert _actual_lib_source_paths() != set()


def test_detects_source_missing_from_cmakelists_in_crafted_input() -> None:
    """違反ケース: 実体にはあるが SRCS に列挙されていないファイルが検出される。"""
    fake_cmake = 'idf_component_register(\n    SRCS "src/a.cpp"\n    INCLUDE_DIRS "include"\n)\n'
    violations = find_source_set_mismatch(fake_cmake, {"src/a.cpp", "src/b.cpp"})
    assert violations != []
    assert any("src/b.cpp" in v for v in violations)


def test_detects_stale_source_declared_but_missing_on_disk_in_crafted_input() -> None:
    """違反ケース: SRCS に列挙されているが実体が既に削除されたファイルが検出される。"""
    fake_cmake = (
        'idf_component_register(\n    SRCS "src/a.cpp" "src/removed.cpp"\n'
        '    INCLUDE_DIRS "include"\n)\n'
    )
    violations = find_source_set_mismatch(fake_cmake, {"src/a.cpp"})
    assert violations != []
    assert any("src/removed.cpp" in v for v in violations)


def test_source_set_matches_when_synced_in_crafted_input() -> None:
    """誤検知回避: SRCS と実体が一致していれば違反なし。"""
    fake_cmake = 'idf_component_register(\n    SRCS "src/a.cpp"\n    INCLUDE_DIRS "include"\n)\n'
    assert find_source_set_mismatch(fake_cmake, {"src/a.cpp"}) == []


# ---------------------------------------------------------------------------
# 2. テスト専用ライブラリが組込みの探索対象に含まれていないこと
# ---------------------------------------------------------------------------


def parse_extra_component_dirs_append_args(cmake_text: str) -> list[str]:
    """`list(APPEND EXTRA_COMPONENT_DIRS ...)` 呼び出しの引数（クォート文字列）を抽出する。"""
    args: list[str] = []
    for match in re.finditer(
        r"list\s*\(\s*APPEND\s+EXTRA_COMPONENT_DIRS\s+((?:\"[^\"]*\"\s*)+)\)",
        cmake_text,
    ):
        args += re.findall(r'"([^"]*)"', match.group(1))
    return args


def find_test_only_library_leak_into_embedded_search(cmake_text: str) -> list[str]:
    """`EXTRA_COMPONENT_DIRS` が `lib` ディレクトリ全体を指しており、将来追加
    されるテスト専用ライブラリ（`lib/test_support/` 等）まで組込みビルドの
    探索対象へ含めてしまう設定を検出する（要件16.3）。

    違反時は問題の引数文字列の一覧を返す。空列であれば違反なし。
    """
    violations = []
    for arg in parse_extra_component_dirs_append_args(cmake_text):
        normalized = arg.replace("\\", "/").rstrip("/")
        if normalized.split("/")[-1] == "lib":
            violations.append(arg)
    return violations


def test_root_cmakelists_extra_component_dirs_points_only_at_drivetrain_control() -> None:
    """`EXTRA_COMPONENT_DIRS` が `lib/drivetrain_control` を直接指す（`lib` 丸ごとではない）。"""
    args = parse_extra_component_dirs_append_args(ROOT_CMAKE_TEXT)
    assert args != [], "EXTRA_COMPONENT_DIRS への list(APPEND ...) が見つからない"
    assert all(
        arg.replace("\\", "/").rstrip("/").endswith("lib/drivetrain_control") for arg in args
    )
    assert find_test_only_library_leak_into_embedded_search(ROOT_CMAKE_TEXT) == []


def test_detects_bare_lib_directory_in_extra_component_dirs_in_crafted_input() -> None:
    """違反ケース: `EXTRA_COMPONENT_DIRS` が `lib` ディレクトリ全体を指す架空の入力が検出される。"""
    fake_cmake = 'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib")\n'
    violations = find_test_only_library_leak_into_embedded_search(fake_cmake)
    assert violations != []


def test_does_not_flag_specific_subdirectory_in_crafted_input() -> None:
    """誤検知回避: 特定のサブディレクトリを直接指す場合は違反にしない。"""
    fake_cmake = 'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/drivetrain_control")\n'
    assert find_test_only_library_leak_into_embedded_search(fake_cmake) == []


# ---------------------------------------------------------------------------
# 3. 3つのビルド環境の存在とホストテスト環境の実機非依存（要件1.1, 1.5）
# ---------------------------------------------------------------------------

EXPECTED_ENV_NAMES: frozenset[str] = frozenset({"teleop", "production", "native"})


def find_build_environment_set_violations(ini_text: str) -> list[str]:
    """`platformio.ini` の `[env:*]` セクション集合が想定の3つと一致するかを検査する。"""
    cp = _load_ini(ini_text)
    env_names = {s.split(":", 1)[1] for s in cp.sections() if s.startswith("env:")}
    violations = []
    violations += [f"欠落: {name}" for name in sorted(EXPECTED_ENV_NAMES - env_names)]
    violations += [f"想定外: {name}" for name in sorted(env_names - EXPECTED_ENV_NAMES)]
    return violations


def find_native_env_requires_hardware(ini_text: str) -> list[str]:
    """`[env:native]` が ESP32 実機・組込みフレームワークへ依存する設定を持っていないかを検出する。"""
    cp = _load_ini(ini_text)
    if not cp.has_section("env:native"):
        return ["[env:native] セクションが存在しない"]
    violations = []
    platform = cp.get("env:native", "platform", fallback="")
    if platform != "native":
        violations.append(f"platform が native ではない: {platform!r}")
    if cp.has_option("env:native", "board"):
        violations.append("board が指定されている（実機ボード依存）")
    if cp.has_option("env:native", "framework"):
        violations.append("framework が指定されている（組込みフレームワーク依存）")
    return violations


def test_platformio_ini_defines_exactly_three_expected_environments() -> None:
    assert find_build_environment_set_violations(PLATFORMIO_INI_TEXT) == []


def test_native_environment_does_not_require_real_hardware() -> None:
    assert find_native_env_requires_hardware(PLATFORMIO_INI_TEXT) == []


def test_detects_missing_environment_in_crafted_input() -> None:
    """違反ケース: `[env:production]` が丸ごと欠落している架空の入力が検出される。"""
    fake_ini = "[env:teleop]\nplatform = x\n\n[env:native]\nplatform = native\n"
    violations = find_build_environment_set_violations(fake_ini)
    assert any("production" in v for v in violations)


def test_detects_unexpected_extra_environment_in_crafted_input() -> None:
    """違反ケース: 想定外の4つめの環境が追加された架空の入力が検出される。"""
    fake_ini = (
        "[env:teleop]\nplatform = x\n\n"
        "[env:production]\nplatform = x\n\n"
        "[env:native]\nplatform = native\n\n"
        "[env:esp32s3]\nplatform = x\n"
    )
    violations = find_build_environment_set_violations(fake_ini)
    assert any("esp32s3" in v for v in violations)


def test_detects_native_env_with_board_in_crafted_input() -> None:
    """違反ケース: `[env:native]` に `board` が指定された架空の入力が検出される。"""
    fake_ini = "[env:native]\nplatform = native\nboard = esp32dev\n"
    assert find_native_env_requires_hardware(fake_ini) != []


def test_detects_native_env_with_non_native_platform_in_crafted_input() -> None:
    """違反ケース: `[env:native]` の `platform` が `native` でない架空の入力が検出される。"""
    fake_ini = "[env:native]\nplatform = espressif32\n"
    assert find_native_env_requires_hardware(fake_ini) != []


# ---------------------------------------------------------------------------
# 4. テレオペ用と本番用のビルド構成マクロが互いに排他であること（要件1.3）
# ---------------------------------------------------------------------------


def find_build_flags_macro_violations(ini_text: str) -> list[str]:
    """各ファーム環境の `build_flags` が、自分専用の排他マクロだけを持つかを検査する。"""
    cp = _load_ini(ini_text)
    expectations = {
        "env:teleop": ("-DDRIVETRAIN_BUILD_TELEOP", "-DDRIVETRAIN_BUILD_PRODUCTION"),
        "env:production": ("-DDRIVETRAIN_BUILD_PRODUCTION", "-DDRIVETRAIN_BUILD_TELEOP"),
    }
    violations = []
    for section, (expected_macro, forbidden_macro) in expectations.items():
        if not cp.has_section(section):
            violations.append(f"{section} が存在しない")
            continue
        flags = cp.get(section, "build_flags", fallback="")
        if expected_macro not in flags:
            violations.append(f"{section} に {expected_macro} が無い")
        if forbidden_macro in flags:
            violations.append(f"{section} に {forbidden_macro} が混入している")
    return violations


def has_exclusivity_error_guard(header_text: str, *, both_defined: bool) -> bool:
    """`build_profile.hpp` に、両方定義／どちらも未定義を検出する `#error` ガードがあるかを判定する。"""
    if both_defined:
        pattern = re.compile(
            r"#if\s+defined\(DRIVETRAIN_BUILD_TELEOP\)\s*&&\s*"
            r"defined\(DRIVETRAIN_BUILD_PRODUCTION\).*?#error",
            re.S,
        )
    else:
        pattern = re.compile(
            r"#if\s+!defined\(DRIVETRAIN_BUILD_TELEOP\)\s*&&\s*"
            r"!defined\(DRIVETRAIN_BUILD_PRODUCTION\).*?#error",
            re.S,
        )
    return pattern.search(header_text) is not None


def test_platformio_ini_build_flags_are_mutually_exclusive() -> None:
    assert find_build_flags_macro_violations(PLATFORMIO_INI_TEXT) == []


def test_build_profile_header_guards_against_both_macros_defined() -> None:
    assert has_exclusivity_error_guard(BUILD_PROFILE_HPP_TEXT, both_defined=True)


def test_build_profile_header_guards_against_neither_macro_defined() -> None:
    assert has_exclusivity_error_guard(BUILD_PROFILE_HPP_TEXT, both_defined=False)


def test_detects_missing_expected_macro_in_crafted_input() -> None:
    """違反ケース: `[env:teleop]` に自分専用のマクロが無い架空の入力が検出される。"""
    fake_ini = "[env:teleop]\nbuild_flags =\n    -std=gnu++17\n"
    violations = find_build_flags_macro_violations(fake_ini)
    assert any("DRIVETRAIN_BUILD_TELEOP" in v for v in violations)


def test_detects_both_macros_present_in_same_environment_in_crafted_input() -> None:
    """違反ケース: 1つの環境に両方のマクロが混入した架空の入力が検出される。"""
    fake_ini = (
        "[env:teleop]\nbuild_flags =\n"
        "    -DDRIVETRAIN_BUILD_TELEOP\n    -DDRIVETRAIN_BUILD_PRODUCTION\n"
    )
    violations = find_build_flags_macro_violations(fake_ini)
    assert any("混入" in v for v in violations)


def test_detects_missing_both_defined_guard_in_crafted_input() -> None:
    """違反ケース: 両方定義時の `#error` ガードを持たない架空のヘッダが検出される。"""
    fake_header = "#pragma once\n// no guards here\n"
    assert has_exclusivity_error_guard(fake_header, both_defined=True) is False


def test_detects_missing_neither_defined_guard_in_crafted_input() -> None:
    """違反ケース: どちらも未定義時の `#error` ガードを持たない架空のヘッダが検出される。"""
    fake_header = (
        "#if defined(DRIVETRAIN_BUILD_TELEOP) && defined(DRIVETRAIN_BUILD_PRODUCTION)\n"
        "#error both\n"
        "#endif\n"
    )
    assert has_exclusivity_error_guard(fake_header, both_defined=False) is False


# ---------------------------------------------------------------------------
# 5. 本番用ビルドが無線関連の外部依存を持たず、無線無効化の設定を持つこと（要件1.4）
# ---------------------------------------------------------------------------

RADIO_LIB_DEPS_KEYWORDS: tuple[str, ...] = ("wifi", "bluetooth", "bluepad", "btstack", "ble")
RADIO_COMPONENT_KEYWORDS: tuple[str, ...] = ("wifi", "bt", "ble", "bluepad", "btstack")


def find_radio_dependency_in_production_lib_deps(ini_text: str) -> list[str]:
    """`[env:production]` の `lib_deps` に無線関連ライブラリへの参照が無いかを検出する。"""
    cp = _load_ini(ini_text)
    lib_deps = cp.get("env:production", "lib_deps", fallback="")
    lowered = lib_deps.lower()
    return [kw for kw in RADIO_LIB_DEPS_KEYWORDS if kw in lowered]


def parse_components_allowlist(cmake_text: str) -> list[str]:
    """`[env:production]` 限定で有効化される `COMPONENTS` allowlist の要素を抽出する。"""
    match = re.search(
        r"if\s*\(\s*DEFINED\s+ENV\{DRIVETRAIN_BUILD_PRODUCTION\}\s*\)\s*"
        r"list\s*\(\s*APPEND\s+COMPONENTS\s+((?:\"[^\"]*\"\s*)+)\)",
        cmake_text,
        re.S,
    )
    if match is None:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def find_radio_component_in_production_allowlist(cmake_text: str) -> list[str]:
    """production 限定の `COMPONENTS` allowlist に無線関連コンポーネントが無いかを検出する。"""
    return [
        component
        for component in parse_components_allowlist(cmake_text)
        if any(kw in component.lower() for kw in RADIO_COMPONENT_KEYWORDS)
    ]


def is_bluetooth_disabled_in_sdkconfig(sdkconfig_text: str) -> bool:
    return "CONFIG_BT_ENABLED=n" in sdkconfig_text


def test_production_lib_deps_has_no_radio_dependency() -> None:
    assert find_radio_dependency_in_production_lib_deps(PLATFORMIO_INI_TEXT) == []


def test_production_components_allowlist_exists_and_excludes_radio() -> None:
    """production 限定の positive allowlist が実在し、かつ無線関連コンポーネントを含まない。

    allowlist 自体が空/不在だと ESP-IDF は既定のコンポーネント全探索へ戻り、
    無線スタックが再混入し得るため、allowlist の存在そのものも確認する。
    """
    components = parse_components_allowlist(ROOT_CMAKE_TEXT)
    assert components != [], "production 限定の COMPONENTS allowlist が見つからない"
    assert find_radio_component_in_production_allowlist(ROOT_CMAKE_TEXT) == []


def test_sdkconfig_production_disables_bluetooth() -> None:
    assert is_bluetooth_disabled_in_sdkconfig(SDKCONFIG_PRODUCTION_TEXT)


def test_detects_radio_lib_dep_in_production_env_in_crafted_input() -> None:
    """違反ケース: `lib_deps` に Bluepad32 を追加した架空の入力が検出される。"""
    fake_ini = "[env:production]\nlib_deps =\n    ricardoquesada/Bluepad32\n"
    violations = find_radio_dependency_in_production_lib_deps(fake_ini)
    assert violations != []


def test_detects_radio_component_in_allowlist_in_crafted_input() -> None:
    """違反ケース: production allowlist に `esp_wifi` を追加した架空の入力が検出される。"""
    fake_cmake = (
        "if(DEFINED ENV{DRIVETRAIN_BUILD_PRODUCTION})\n"
        '    list(APPEND COMPONENTS "src" "drivetrain_control" "esp_wifi")\n'
        "endif()\n"
    )
    violations = find_radio_component_in_production_allowlist(fake_cmake)
    assert violations != []


def test_detects_missing_allowlist_in_crafted_input() -> None:
    """違反ケース: production 限定の allowlist ブロックが丸ごと無い架空の入力が検出される。"""
    fake_cmake = "cmake_minimum_required(VERSION 3.16)\n"
    assert parse_components_allowlist(fake_cmake) == []


def test_detects_missing_bt_disable_setting_in_crafted_input() -> None:
    """違反ケース: `CONFIG_BT_ENABLED=n` を持たない架空の sdkconfig が検出される。"""
    assert is_bluetooth_disabled_in_sdkconfig("# nothing here\n") is False


# ---------------------------------------------------------------------------
# 6. ファーム2環境の対象基板が classic ESP32 に固定され、他系統の環境が
#    定義されていないこと（要件1.6）
# ---------------------------------------------------------------------------


def find_board_pin_violations(ini_text: str) -> list[str]:
    """`[env:teleop]` / `[env:production]` の `board` が `esp32dev` 固定であるかを検査する。"""
    cp = _load_ini(ini_text)
    violations = []
    for section in ("env:teleop", "env:production"):
        if not cp.has_section(section):
            violations.append(f"{section} が存在しない")
            continue
        board = cp.get(section, "board", fallback="")
        if board != "esp32dev":
            violations.append(f"{section} の board が esp32dev ではない: {board!r}")
    return violations


def find_other_mcu_family_environments(ini_text: str) -> list[str]:
    """`board` を持つ全環境の `board` 値が単一（classic ESP32）であるかを検査する。

    3節の環境集合チェックと組み合わせることで、「他系統チップ向けの環境が
    追加で定義されていない」ことを固定する。
    """
    cp = _load_ini(ini_text)
    boards = {
        cp.get(section, "board")
        for section in cp.sections()
        if section.startswith("env:") and cp.has_option(section, "board")
    }
    return sorted(boards - {"esp32dev"})


def test_both_firmware_environments_are_pinned_to_classic_esp32() -> None:
    assert find_board_pin_violations(PLATFORMIO_INI_TEXT) == []


def test_no_environment_targets_a_different_mcu_family() -> None:
    assert find_other_mcu_family_environments(PLATFORMIO_INI_TEXT) == []


def test_detects_non_classic_esp32_board_in_crafted_input() -> None:
    """違反ケース: `[env:teleop]` の board が classic ESP32 でない架空の入力が検出される。"""
    fake_ini = "[env:teleop]\nboard = esp32-s3-devkitc-1\n\n[env:production]\nboard = esp32dev\n"
    violations = find_board_pin_violations(fake_ini)
    assert violations != []
    assert "teleop" in violations[0]


def test_detects_additional_environment_targeting_other_mcu_family_in_crafted_input() -> None:
    """違反ケース: S3 系チップ向けの想定外環境が追加された架空の入力が検出される。"""
    fake_ini = (
        "[env:teleop]\nboard = esp32dev\n\n"
        "[env:production]\nboard = esp32dev\n\n"
        "[env:s3_variant]\nboard = esp32-s3-devkitc-1\n"
    )
    violations = find_other_mcu_family_environments(fake_ini)
    assert violations != []
    assert "esp32-s3-devkitc-1" in violations


# ---------------------------------------------------------------------------
# 7. 外部プラットフォーム定義が更新で内容の変化しない成果物として固定されて
#    いること（要件1.7）
# ---------------------------------------------------------------------------

PLATFORM_PIN_PATTERN = re.compile(
    r"^https://github\.com/[^/\s]+/[^/\s]+/releases/download/[^/\s]+/[^/\s]+\.zip$"
)
"""GitHub リリースの固定成果物（zip）への直リンクのみを「不変」とみなす。
リポジトリ直参照（`.git` / ブランチ・タグの HEAD 追従）や、ブランチ
アーカイブ（`archive/refs/heads/...`）は更新のたびに内容が変わり得るため
不変ではない。"""


def find_mutable_platform_reference(ini_text: str) -> list[str]:
    """ファーム2環境の `platform` が固定成果物への参照であるかを検査する。"""
    cp = _load_ini(ini_text)
    violations = []
    for section in ("env:teleop", "env:production"):
        if not cp.has_section(section):
            violations.append(f"{section} が存在しない")
            continue
        platform = cp.get(section, "platform", fallback="")
        if not PLATFORM_PIN_PATTERN.match(platform):
            violations.append(f"{section} の platform が固定成果物への参照ではない: {platform!r}")
    return violations


def test_firmware_environments_pin_platform_to_immutable_release_artifact() -> None:
    assert find_mutable_platform_reference(PLATFORMIO_INI_TEXT) == []


@pytest.mark.parametrize(
    "mutable_platform",
    [
        "espressif32",
        "https://github.com/pioarduino/platform-espressif32.git",
        "https://github.com/pioarduino/platform-espressif32.git#develop",
        "https://github.com/pioarduino/platform-espressif32/archive/refs/heads/main.zip",
    ],
)
def test_detects_mutable_platform_reference_in_crafted_input(mutable_platform: str) -> None:
    """違反ケース: 可変参照（パッケージ名／`.git`／ブランチアーカイブ）が4パターン検出される。"""
    fake_ini = f"[env:teleop]\nplatform = {mutable_platform}\n\n[env:production]\nplatform = pinned\n"
    violations = find_mutable_platform_reference(fake_ini)
    assert any("teleop" in v for v in violations)


# ---------------------------------------------------------------------------
# 8. ホストテストと実機テストの振り分け設定が存在すること（要件16.7）
# ---------------------------------------------------------------------------

EXPECTED_TEST_FILTERS: dict[str, str] = {
    "env:native": "native/*",
    "env:teleop": "embedded/*",
    "env:production": "embedded/*",
}


def find_test_filter_violations(ini_text: str) -> list[str]:
    """各環境の `test_filter` が想定どおりホスト/実機を振り分けているかを検査する。"""
    cp = _load_ini(ini_text)
    violations = []
    for section, expected_filter in EXPECTED_TEST_FILTERS.items():
        if not cp.has_section(section):
            violations.append(f"{section} が存在しない")
            continue
        actual = cp.get(section, "test_filter", fallback="")
        if actual != expected_filter:
            violations.append(f"{section} の test_filter が想定と異なる: {actual!r}")
    return violations


def find_missing_test_directory_split() -> list[str]:
    """`test/native/` と `test/embedded/` の両方がディレクトリとして存在するかを検査する。"""
    violations = []
    if not TEST_NATIVE_DIR.is_dir():
        violations.append(f"{TEST_NATIVE_DIR} が存在しない")
    if not TEST_EMBEDDED_DIR.is_dir():
        violations.append(f"{TEST_EMBEDDED_DIR} が存在しない")
    return violations


def test_each_environment_declares_a_test_filter_for_host_vs_embedded_split() -> None:
    assert find_test_filter_violations(PLATFORMIO_INI_TEXT) == []


def test_native_and_embedded_test_directories_both_exist() -> None:
    assert find_missing_test_directory_split() == []


def test_detects_missing_test_filter_in_crafted_input() -> None:
    """違反ケース: `[env:native]` に `test_filter` が無い架空の入力が検出される。"""
    fake_ini = "[env:native]\nplatform = native\n"
    violations = find_test_filter_violations(fake_ini)
    assert any("native" in v for v in violations)


def test_detects_wrong_test_filter_value_in_crafted_input() -> None:
    """違反ケース: `[env:teleop]` の `test_filter` が実機側 (`embedded/*`) でない架空の入力が検出される。"""
    fake_ini = "[env:teleop]\ntest_filter = native/*\n"
    violations = find_test_filter_violations(fake_ini)
    assert violations != []


# =============================================================================
# タスク 8: 純ロジックのソースを静的に検査する（要件 2.2, 2.4, 4.6, 16.3, 17.3, 17.4）
# =============================================================================
#
# タスク 1.3 が検査したのは `platformio.ini` / `CMakeLists.txt` /
# `sdkconfig.defaults*` という「ビルド構成」だけであった。ここから先は
# design.md FirmwareBoundaryCheck の Batch/Job Contract 表が挙げる残りの
# 検査対象、すなわち `lib/drivetrain_control/**` の実ソース（.hpp/.cpp）
# そのものを走査する。
#
# **コメントを剥がしてから走査する（設計判断）**: `strip_cpp_comments()` で
# `//` 行コメントと `/* */` ブロックコメントを除去し、文字列/文字リテラル
# は保持したテキストに対して禁止トークン・禁止 include・依存方向・
# シミュレータ参照のすべての検査をかける。理由は、このリポジトリの
# 実コードに以下の**確認済みの誤検知リスク**があるため:
#   - `controller.hpp` は「`unsigned char` でも動作は同じだが `unsigned `
#     はタスク8の検査と衝突するため `std::byte` を使う」という設計判断を
#     コメントで説明しており、そのコメント自体が `unsigned ` / `size_t` /
#     `<vector>` / `new ` を字面として含む（本ファイル冒頭でも実測済み）。
#   - `voltage_scaler.hpp` は「float/double を用いない」という設計判断を
#     コメントで説明しており `double` を字面として含む。
#   - `types.hpp` は「`long` / `size_t` はこの名前空間に置かない」という
#     規約をコメントで説明しており `long` / `size_t` を字面として含む。
#   - `config.hpp` / `controller.hpp` / `drivetrain_control.hpp` は要件17.1
#     が要求する `trajectory_sim.DrivetrainParams` との意味論対応をコメント
#     で説明しており `trajectory_sim` を字面として含む。
# これらはすべて「禁止事項をコード上で回避した理由」または「要件が要求する
# 意味論対応の説明」であり、違反ではない。生テキストへの単純な文字列一致で
# 検査すると、規約を最も丁寧に守っているファイルほど誤検知される逆説的な
# 結果になるため、コメントを除去してから走査する。
#
# **`new` の扱い（設計判断）**: `controller.hpp` の `LazySlot<T>` は
# `alignas(T) std::byte storage_[sizeof(T)]` 上へ **placement new**
# （`new (&storage) T(args...)`）で構築する、動的メモリ確保を伴わない
# embedded C++ の定石を使う（ヒープ確保をしない `DrivetrainController`
# を既定構築可能にするため）。ヒープ確保の `new T(...)` と placement new
# `new (ptr) T(...)` を区別できないと、この正当な実装が誤検出される
# （`controller.hpp` 自身のコメントが将来のタスク8実装者へこの区別を
# 明示的に要求している）。`\bnew\b(?!\s*\()` — 「`new` の直後（空白を
# 挟んでよい）が `(` でなければヒープ確保とみなす」で区別する。


def _all_pure_logic_files() -> list[Path]:
    """`lib/drivetrain_control` 配下の純ロジック実ファイル（ヘッダ＋実装）を列挙する。"""
    return sorted(LIB_INCLUDE_ROOT_DIR.rglob("*.hpp")) + sorted(LIB_SRC_DIR.rglob("*.cpp"))


def test_all_pure_logic_files_is_non_empty() -> None:
    """走査対象そのものが空振りでないことを確認する（前提の健全性）。"""
    assert _all_pure_logic_files() != []


_COMMENT_OR_LITERAL_PATTERN = re.compile(
    r"//[^\n]*"  # 行コメント
    r"|/\*.*?\*/"  # ブロックコメント
    r'|"(?:\\.|[^"\\])*"'  # 文字列リテラル（保持する）
    r"|'(?:\\.|[^'\\])*'",  # 文字リテラル（保持する）
    re.S,
)


def strip_cpp_comments(text: str) -> str:
    """C++ の `//` 行コメントと `/* */` ブロックコメントを除去する。

    文字列/文字リテラルは保持する（リテラル内の `//` や `/*` をコメント
    開始と誤認しないように、走査を1パスの正規表現で行う）。コメントは
    1個の空白へ置き換える（トークンの隣接によるすり抜けを防ぐ）。
    """

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("//") or token.startswith("/*"):
            return " "
        return token

    return _COMMENT_OR_LITERAL_PATTERN.sub(_replace, text)


def test_strip_cpp_comments_removes_comments_but_keeps_string_literals() -> None:
    fake_cpp = (
        '// unsigned という語を含む行コメント\n'
        "/* long を含む\n   複数行ブロックコメント */\n"
        'const char* kMsg = "size_t という語を含む文字列リテラル";\n'
    )
    stripped = strip_cpp_comments(fake_cpp)
    assert "unsigned" not in stripped
    assert "long" not in stripped
    assert "size_t という語を含む文字列リテラル" in stripped


def _violations_across_pure_logic_files(
    check: Callable[[str], list[str]],
) -> dict[str, list[str]]:
    """`check` を全純ロジックファイルへ適用し、違反があったファイルのみを集める。"""
    result: dict[str, list[str]] = {}
    for path in _all_pure_logic_files():
        violations = check(path.read_text(encoding="utf-8"))
        if violations:
            result[str(path.relative_to(REPO_ROOT))] = violations
    return result


# ---------------------------------------------------------------------------
# 9. 禁止 include: ペリフェラルおよび組込みフレームワークに固有のヘッダ
#    （要件 2.2, 2.4）
# ---------------------------------------------------------------------------

_INCLUDE_TARGET_PATTERN = re.compile(r'#include\s*[<"]([^">]+)[>"]')


def classify_forbidden_include(header: str) -> str | None:
    """include 対象がペリフェラル/組込みフレームワーク固有ヘッダか分類する。

    該当すれば design.md の表記そのままのカテゴリ名を返す。該当しなければ
    `None`（`drivetrain_control/...` や `<cstdint>` 等の標準ヘッダは該当しない）。
    """
    basename = header.rsplit("/", 1)[-1]
    if basename == "Arduino.h":
        return "Arduino.h"
    if basename.startswith("esp_"):
        return "esp_*"
    if header.startswith("driver/"):
        return "driver/*"
    if header.startswith("freertos/"):
        return "freertos/*"
    if basename == "sdkconfig.h":
        return "sdkconfig.h"
    if basename.startswith("WiFi"):
        return "WiFi*"
    if basename.startswith("Bluetooth"):
        return "Bluetooth*"
    return None


def find_forbidden_peripheral_includes(text: str) -> list[str]:
    stripped = strip_cpp_comments(text)
    violations = []
    for match in _INCLUDE_TARGET_PATTERN.finditer(stripped):
        header = match.group(1)
        category = classify_forbidden_include(header)
        if category is not None:
            violations.append(f"{header} ({category})")
    return violations


def test_no_pure_logic_file_includes_forbidden_peripheral_headers() -> None:
    violations = _violations_across_pure_logic_files(find_forbidden_peripheral_includes)
    assert violations == {}, f"禁止ヘッダの参照が混入している: {violations}"


@pytest.mark.parametrize(
    ("include_line", "expected_category"),
    [
        ("#include <Arduino.h>\n", "Arduino.h"),
        ('#include "esp_timer.h"\n', "esp_*"),
        ("#include <driver/gpio.h>\n", "driver/*"),
        ("#include <freertos/FreeRTOS.h>\n", "freertos/*"),
        ('#include "sdkconfig.h"\n', "sdkconfig.h"),
        ("#include <WiFi.h>\n", "WiFi*"),
        ("#include <BluetoothSerial.h>\n", "Bluetooth*"),
    ],
)
def test_detects_forbidden_peripheral_include_in_crafted_input(
    include_line: str, expected_category: str
) -> None:
    """違反ケース: 7カテゴリそれぞれの禁止ヘッダが検出される。"""
    violations = find_forbidden_peripheral_includes(include_line)
    assert violations != []
    assert any(expected_category in v for v in violations)


def test_does_not_flag_legitimate_includes_in_crafted_input() -> None:
    """誤検知回避: 標準ヘッダと `drivetrain_control/...` は禁止ヘッダに該当しない。"""
    fake_cpp = (
        "#include <cstdint>\n"
        "#include <cstddef>\n"
        "#include <cmath>\n"
        "#include <new>\n"
        "#include <type_traits>\n"
        '#include "drivetrain_control/config.hpp"\n'
        '#include "drivetrain_control/protection/motor_lock.hpp"\n'
    )
    assert find_forbidden_peripheral_includes(fake_cpp) == []


# ---------------------------------------------------------------------------
# 汎用: 「コメント除去後のテキスト」に対する複数トークンの一括検出
# ---------------------------------------------------------------------------


def _find_token_matches(text: str, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    stripped = strip_cpp_comments(text)
    return [name for name, pattern in patterns.items() if pattern.search(stripped)]


# ---------------------------------------------------------------------------
# 10. 禁止トークン: 現在時刻を取得する手段（要件 3.1, 2.4）
# ---------------------------------------------------------------------------

FORBIDDEN_TIME_SOURCE_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "millis(": re.compile(r"\bmillis\s*\("),
    "micros(": re.compile(r"\bmicros\s*\("),
    "esp_timer_": re.compile(r"\besp_timer_\w*"),
    "<chrono>": re.compile(r"<chrono>"),
    "<ctime>": re.compile(r"<ctime>"),
    "time(": re.compile(r"\btime\s*\("),
}


def find_forbidden_time_source_tokens(text: str) -> list[str]:
    return _find_token_matches(text, FORBIDDEN_TIME_SOURCE_TOKEN_PATTERNS)


def test_no_pure_logic_file_references_a_current_time_source() -> None:
    violations = _violations_across_pure_logic_files(find_forbidden_time_source_tokens)
    assert violations == {}, f"現在時刻を取得する手段の参照が混入している: {violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        "auto t = millis();\n",
        "auto t = micros();\n",
        "auto t = esp_timer_get_time();\n",
        "#include <chrono>\n",
        "#include <ctime>\n",
        "auto t = time(nullptr);\n",
    ],
)
def test_detects_forbidden_time_source_token_in_crafted_input(snippet: str) -> None:
    """違反ケース: 6種の現在時刻取得手段トークンそれぞれが検出される。"""
    assert find_forbidden_time_source_tokens(snippet) != []


def test_does_not_flag_time_like_identifiers_in_crafted_input() -> None:
    """誤検知回避: `time(` の語境界を跨がない識別子（`getRuntime()` 等）は検出しない。"""
    fake_cpp = (
        "std::int64_t since_reset_ms = 0;\n"
        "std::int32_t last_step_interval_ms = 0;\n"
        "int getRuntime() { return 1; }\n"
    )
    assert find_forbidden_time_source_tokens(fake_cpp) == []


# ---------------------------------------------------------------------------
# 11. 禁止型トークン: 処理系依存幅の整数型と倍精度浮動小数点（要件 4.1, 4.6）
# ---------------------------------------------------------------------------

FORBIDDEN_TYPE_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "long": re.compile(r"\blong\b"),
    "double": re.compile(r"\bdouble\b"),
    "unsigned ": re.compile(r"\bunsigned\s"),
    "size_t": re.compile(r"\bsize_t\b"),
}


def find_forbidden_type_tokens(text: str) -> list[str]:
    return _find_token_matches(text, FORBIDDEN_TYPE_TOKEN_PATTERNS)


def test_no_pure_logic_file_uses_platform_dependent_width_or_double_precision_types() -> None:
    violations = _violations_across_pure_logic_files(find_forbidden_type_tokens)
    assert violations == {}, f"処理系依存幅の整数型/倍精度浮動小数点の混入: {violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        "long value = 0;\n",
        "double value = 0.0;\n",
        "unsigned char storage_[4];\n",
        "size_t n = 0;\n",
    ],
)
def test_detects_forbidden_type_token_in_crafted_input(snippet: str) -> None:
    """違反ケース: `long` / `double` / `unsigned ` / `size_t` それぞれが検出される。"""
    assert find_forbidden_type_tokens(snippet) != []


def test_does_not_flag_fixed_width_types_in_crafted_input() -> None:
    """誤検知回避: 固定幅整数・`float`・`bool`・`std::byte` は design.md の許可型であり検出しない。"""
    fake_cpp = (
        "std::uint32_t a = 0;\n"
        "std::uint8_t b = 0;\n"
        "std::int64_t c = 0;\n"
        "float f = 0.0F;\n"
        "bool ok = true;\n"
        "alignas(T) std::byte storage_[sizeof(T)];\n"
    )
    assert find_forbidden_type_tokens(fake_cpp) == []


def test_does_not_flag_banned_type_words_mentioned_only_in_comment_in_crafted_input() -> None:
    """誤検知回避: `controller.hpp` 実物と同種の「回避理由を説明するコメント」は検出しない。"""
    fake_cpp = (
        "// std::byte（<cstddef>）を使う。`unsigned char` でも動作は同じだが、\n"
        "// 「unsigned 」（末尾スペース込み）は design.md FirmwareBoundaryCheck\n"
        "// の固定幅整数トークン検査と衝突するため std::byte を使う。size_t/long/double も同様。\n"
        "alignas(T) std::byte storage_[sizeof(T)];\n"
    )
    assert find_forbidden_type_tokens(fake_cpp) == []


# ---------------------------------------------------------------------------
# 12. 禁止: 例外・RTTI・動的メモリ確保・重量級の標準ライブラリ
#     （Allowed Dependencies）
# ---------------------------------------------------------------------------

FORBIDDEN_EXCEPTION_RTTI_ALLOCATION_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "throw": re.compile(r"\bthrow\b"),
    # ヒープ確保の `new T(...)` のみを禁止し、placement new `new (ptr) T(...)` と
    # 標準ヘッダ `#include <new>`（placement new 自体に必要）は許可する。
    # `new` の直後（空白を挟んでよい）が `(` でなければヒープ確保とみなす。
    # 加えて `<new>`（ヘッダ名そのもの）は直前が `<` であることで判別し除外する。
    "new ": re.compile(r"(?<!<)\bnew\b(?!\s*\()"),
    "malloc": re.compile(r"\bmalloc\s*\("),
    "<vector>": re.compile(r"<vector>"),
    "<string>": re.compile(r"<string>"),
    "<iostream>": re.compile(r"<iostream>"),
    # design.md の表は例示トークンを挙げていないが、要件・タスク文言が明示する
    # 「実行時型情報 (RTTI)」を具体的に検出できるよう、無曖昧な2トークンを追加する。
    "typeid": re.compile(r"\btypeid\b"),
    "dynamic_cast": re.compile(r"\bdynamic_cast\b"),
}


def find_forbidden_exception_rtti_allocation_tokens(text: str) -> list[str]:
    return _find_token_matches(text, FORBIDDEN_EXCEPTION_RTTI_ALLOCATION_TOKEN_PATTERNS)


def test_no_pure_logic_file_uses_exceptions_rtti_or_dynamic_allocation() -> None:
    violations = _violations_across_pure_logic_files(
        find_forbidden_exception_rtti_allocation_tokens
    )
    assert violations == {}, f"例外・RTTI・動的確保・重量級標準ライブラリの参照が混入: {violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        'throw std::runtime_error("x");\n',
        "T* p = new T(1);\n",
        "void* p = malloc(4);\n",
        "#include <vector>\n",
        "#include <string>\n",
        "#include <iostream>\n",
        "if (typeid(a) == typeid(b)) {}\n",
        "auto* d = dynamic_cast<Derived*>(base);\n",
    ],
)
def test_detects_forbidden_exception_rtti_allocation_token_in_crafted_input(
    snippet: str,
) -> None:
    """違反ケース: 8種のトークンそれぞれが検出される（RTTI用の2種を含む）。"""
    assert find_forbidden_exception_rtti_allocation_tokens(snippet) != []


def test_does_not_flag_new_standard_header_in_crafted_input() -> None:
    """誤検知回避: `#include <new>`（placement new に必要な標準ヘッダ）は検出しない。

    `controller.hpp` の実物がこのヘッダを実際に include している
    （placement new を使うため）。
    """
    fake_cpp = "#include <new>\n"
    assert find_forbidden_exception_rtti_allocation_tokens(fake_cpp) == []


def test_does_not_flag_placement_new_in_crafted_input() -> None:
    """誤検知回避: `controller.hpp` の `LazySlot<T>` が使う placement new は検出しない。

    `controller.hpp` 自身のコメントがこの区別を将来のタスク8実装者へ要求している
    （ヒープ確保の `new T(...)` とは異なり、`new (&storage) T(args...)` はヒープを
    一切使わない）。
    """
    fake_cpp = "T* ptr = new (static_cast<void*>(&storage_)) T(static_cast<Args&&>(args)...);\n"
    assert find_forbidden_exception_rtti_allocation_tokens(fake_cpp) == []


def test_does_not_flag_new_or_vector_mentioned_only_in_comment_in_crafted_input() -> None:
    """誤検知回避: `controller.hpp` 実物と同種の「ヒープ確保を避けた理由の説明」コメントは検出しない。"""
    fake_cpp = (
        "// ヒープ確保（`new` によるヒープ割り当て・`malloc`、`<vector>` 等の\n"
        "// ヒープ利用）は一切行わない。placement new（`new (&storage) T(args...)`）\n"
        "// だけを使う。\n"
        "alignas(T) std::byte storage_[sizeof(T)];\n"
    )
    assert find_forbidden_exception_rtti_allocation_tokens(fake_cpp) == []


# ---------------------------------------------------------------------------
# 13. 禁止: ビルド構成マクロの参照が純ロジックに無いこと（要件 1.2）
# ---------------------------------------------------------------------------

FORBIDDEN_BUILD_MACRO_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "DRIVETRAIN_BUILD_TELEOP": re.compile(r"\bDRIVETRAIN_BUILD_TELEOP\b"),
    "DRIVETRAIN_BUILD_PRODUCTION": re.compile(r"\bDRIVETRAIN_BUILD_PRODUCTION\b"),
}


def find_forbidden_build_macro_references(text: str) -> list[str]:
    return _find_token_matches(text, FORBIDDEN_BUILD_MACRO_TOKEN_PATTERNS)


def test_no_pure_logic_file_references_build_configuration_macros() -> None:
    violations = _violations_across_pure_logic_files(find_forbidden_build_macro_references)
    assert violations == {}, f"ビルド構成マクロの参照が純ロジックに混入している: {violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        "#ifdef DRIVETRAIN_BUILD_TELEOP\n#endif\n",
        "#if defined(DRIVETRAIN_BUILD_PRODUCTION)\n#endif\n",
    ],
)
def test_detects_build_macro_reference_in_crafted_input(snippet: str) -> None:
    """違反ケース: `DRIVETRAIN_BUILD_TELEOP` / `DRIVETRAIN_BUILD_PRODUCTION` の参照が検出される。"""
    assert find_forbidden_build_macro_references(snippet) != []


def test_does_not_flag_unrelated_macro_names_in_crafted_input() -> None:
    """誤検知回避: 無関係なマクロ名は検出しない。"""
    fake_cpp = "#pragma once\n#define DRIVETRAIN_CONTROL_VERSION 1\n"
    assert find_forbidden_build_macro_references(fake_cpp) == []


# ---------------------------------------------------------------------------
# 14. 禁止: `test_support` への参照（要件 16.3）
# ---------------------------------------------------------------------------

FORBIDDEN_TEST_SUPPORT_TOKEN_PATTERNS: dict[str, re.Pattern[str]] = {
    "test_support": re.compile(r"\btest_support\b"),
}


def find_forbidden_test_support_references(text: str) -> list[str]:
    return _find_token_matches(text, FORBIDDEN_TEST_SUPPORT_TOKEN_PATTERNS)


def test_no_pure_logic_file_references_test_support_library() -> None:
    violations = _violations_across_pure_logic_files(find_forbidden_test_support_references)
    assert violations == {}, f"test_support への参照が純ロジックに混入している: {violations}"


@pytest.mark.parametrize(
    "snippet",
    [
        '#include "test_support/fake_ports.hpp"\n',
        "test_support::FakeEncoderPort port;\n",
    ],
)
def test_detects_test_support_reference_in_crafted_input(snippet: str) -> None:
    """違反ケース: include・名前空間参照のどちらの形でも `test_support` 参照が検出される。"""
    assert find_forbidden_test_support_references(snippet) != []


def test_does_not_flag_unrelated_identifiers_in_crafted_input() -> None:
    """誤検知回避: 無関係な識別子は検出しない。"""
    fake_cpp = "drivetrain_control::EncoderPort* port = nullptr;\n"
    assert find_forbidden_test_support_references(fake_cpp) == []


# ---------------------------------------------------------------------------
# 15. 依存方向: 各ファイルの include が design.md "Dependency Direction"
#     表の許可範囲に収まること
# ---------------------------------------------------------------------------
#
# design.md の層表（L0〜L11）をそのままデータ化する。キーはファイルの
# ベース名（拡張子・ディレクトリを除いたもの）。ベース名はツリー全体で
# 一意（`protection/motor_lock.hpp` と衝突する `motor_lock.*` は他に無い）。

LAYER_OF: dict[str, int] = {
    "units": 0,
    "errors": 0,
    "types": 1,
    "config": 2,
    "ports": 3,
    "wrap_accumulator": 4,
    "voltage_scaler": 4,
    "kinematics": 5,
    "odometry": 6,
    "velocity_pid": 6,
    "motor_lock": 7,
    "low_voltage": 7,
    "pwm_ceiling": 7,
    "command_watchdog": 7,
    "supervisor": 8,
    "command_input": 9,
    "controller": 10,
    "drivetrain_control": 11,
}

# 層番号 → 「依存してよい層番号の集合」。design.md の表の「依存してよい層」列を
# そのまま転記したもの（単純な「自分より小さい番号なら何でも可」ではない。
# 例えば L9 command_input は L5 kinematics へ直接依存してよいが L6/L7/L8 へは
# 依存できない、という表が明示する層飛ばしの許可/不許可をそのまま反映する）。
ALLOWED_LOWER_LAYERS: dict[int, frozenset[int]] = {
    0: frozenset(),
    1: frozenset({0}),
    2: frozenset({0, 1}),
    3: frozenset({0, 1}),
    4: frozenset({0, 1, 2}),
    5: frozenset({0, 1, 2}),
    6: frozenset({0, 1, 2, 5}),
    7: frozenset({0, 1, 2}),
    8: frozenset({0, 1, 2, 7}),
    9: frozenset({0, 1, 2, 5}),
    10: frozenset(range(10)),
    11: frozenset(range(11)),
}

_DRIVETRAIN_CONTROL_INCLUDE_PATTERN = re.compile(r'#include\s*"drivetrain_control/([^"]+)"')


def parse_drivetrain_control_include_basenames(text: str) -> list[str]:
    """`#include "drivetrain_control/..."` の対象をベース名（拡張子除く）の列として抽出する。"""
    stripped = strip_cpp_comments(text)
    includes = _DRIVETRAIN_CONTROL_INCLUDE_PATTERN.findall(stripped)
    return [Path(inc).stem for inc in includes]


def find_dependency_direction_violations(basename: str, text: str) -> list[str]:
    """`basename` のファイルが自身に許可された層の外を include していないかを検査する。

    自分自身のベース名（`.cpp` が対になる `.hpp` を include する自己参照）は、
    層表の対象外の自己参照であり違反にしない。
    """
    if basename not in LAYER_OF:
        return [f"未知のファイル（Dependency Direction 表に無い）: {basename}"]
    own_layer = LAYER_OF[basename]
    allowed = ALLOWED_LOWER_LAYERS[own_layer]
    violations = []
    for included_basename in parse_drivetrain_control_include_basenames(text):
        if included_basename == basename:
            continue
        included_layer = LAYER_OF.get(included_basename)
        if included_layer is None:
            violations.append(f"未知の include（層表に無い）: {included_basename}")
            continue
        if included_layer not in allowed:
            violations.append(
                f"L{own_layer}({basename}) が L{included_layer}({included_basename}) を"
                " include している（依存方向の許可範囲外）"
            )
    return violations


def test_every_pure_logic_file_stays_within_dependency_direction_table() -> None:
    violations: dict[str, list[str]] = {}
    for path in _all_pure_logic_files():
        file_violations = find_dependency_direction_violations(
            path.stem, path.read_text(encoding="utf-8")
        )
        if file_violations:
            violations[str(path.relative_to(REPO_ROOT))] = file_violations
    assert violations == {}, f"Dependency Direction 表の許可範囲を外れた include: {violations}"


def test_detects_upward_layer_reference_in_crafted_input() -> None:
    """違反ケース: L5 kinematics が L8 supervisor を include する（上位層への参照）。"""
    fake_hpp = '#include "drivetrain_control/protection/supervisor.hpp"\n'
    violations = find_dependency_direction_violations("kinematics", fake_hpp)
    assert violations != []


def test_detects_disallowed_layer_skip_in_crafted_input() -> None:
    """違反ケース: L2 config が L5 kinematics を include する（表で許可されていない層飛ばし）。"""
    fake_hpp = '#include "drivetrain_control/kinematics.hpp"\n'
    violations = find_dependency_direction_violations("config", fake_hpp)
    assert violations != []


def test_does_not_flag_explicitly_allowed_layer_skip_in_crafted_input() -> None:
    """誤検知回避: L9 command_input が L5 kinematics を直接 include するのは表で明示的に許可されている。"""
    fake_hpp = '#include "drivetrain_control/kinematics.hpp"\n'
    assert find_dependency_direction_violations("command_input", fake_hpp) == []


def test_does_not_flag_own_paired_header_self_include_in_crafted_input() -> None:
    """誤検知回避: `.cpp` が自分自身の対になる `.hpp` を include するのは層表の対象外の自己参照。"""
    fake_cpp = '#include "drivetrain_control/protection/supervisor.hpp"\n'
    assert find_dependency_direction_violations("supervisor", fake_cpp) == []


# ---------------------------------------------------------------------------
# 16. シミュレータのコードを参照していないこと（要件 17.3, 17.4）
# ---------------------------------------------------------------------------
#
# `trajectory_sim` はコメント上で意味論対応を説明する目的で正当に言及される
# （要件 17.1 が要求する記述であり、これ自体はコード参照ではない）。禁止
# されるのは実コード上の参照（#include・文字列リテラルでのパス参照）のみ
# であるため、ここでもコメント除去後のテキストを走査する。


def find_simulator_source_reference(text: str) -> list[str]:
    stripped = strip_cpp_comments(text)
    return [line.strip() for line in stripped.splitlines() if "trajectory_sim" in line]


def test_no_pure_logic_file_references_simulator_source() -> None:
    violations: dict[str, list[str]] = {}
    for path in _all_pure_logic_files():
        file_violations = find_simulator_source_reference(path.read_text(encoding="utf-8"))
        if file_violations:
            violations[str(path.relative_to(REPO_ROOT))] = file_violations
    assert violations == {}, f"シミュレータのコードへの参照が混入している: {violations}"


def test_detects_simulator_include_reference_in_crafted_input() -> None:
    """違反ケース: `#include` でのシミュレータ側パス参照が検出される。"""
    fake_hpp = '#include "../../src/trajectory_sim/drivetrain.hpp"\n'
    assert find_simulator_source_reference(fake_hpp) != []


def test_detects_simulator_string_literal_reference_in_crafted_input() -> None:
    """違反ケース: 文字列リテラルでのシミュレータ側パス参照が検出される。"""
    fake_cpp = 'const char* kPath = "src/trajectory_sim/drivetrain.py";\n'
    assert find_simulator_source_reference(fake_cpp) != []


def test_does_not_flag_semantic_parity_comment_mentioning_simulator_in_crafted_input() -> None:
    """誤検知回避: `config.hpp` 実物と同種の「意味論対応の説明」コメントは検出しない（要件17.1）。"""
    fake_hpp = (
        "float max_body_speed_mm_s;  "
        "// trajectory_sim.DrivetrainParams.max_speed_mm_s と同一定義\n"
    )
    assert find_simulator_source_reference(fake_hpp) == []


# =============================================================================
# teleop-bringup タスク 1.1: テレオペ用ビルドの無線有効化とパーティション指定
# （teleop-bringup requirements.md 1.1, 1.3, 1.5 / design.md TeleopBuildProfile）
# =============================================================================
#
# ⚠️ **本節の要件番号は `.kiro/specs/teleop-bringup/requirements.md` の採番である。**
# 本ファイル冒頭（1〜8節）の要件番号は `drivetrain-core` 側の採番であり、別物である。
#
# 固定する事項:
#
# A. `firmware/sdkconfig.defaults.teleop` が実在し、無線スタックを有効化する
#    （teleop-bringup 1.1）。かつ classic ESP32 の Bluetooth Classic（BR/EDR）を
#    前提とした構成を持つ（同 1.5）。
# B. 無線込み成果物が収まる**大きい側のパーティション構成**を指定する（同 1.3）。
#    ⚠️ **指定は PlatformIO の `board_build.partitions` と Kconfig の
#    `CONFIG_PARTITION_TABLE_*` の両方に書き、両者が同じテーブルを指すことを
#    不変条件として固定する**（→ research.md「パーティションは INI と Kconfig の
#    両方で指定し、一致を不変条件として固定する」/ design.md Technology Stack）。
#    役割が異なる:
#      - `board_build.partitions` … 実際に生成・焼き込みされるテーブルを決める
#      - `CONFIG_PARTITION_TABLE_*` … ESP-IDF 自身のアプリサイズ検査が読む値を決める
#    実測（タスク 1.1 実装中）:
#      `platforms/espressif32/builder/frameworks/espidf.py:2846` が
#      `partitions_csv = board.get("build.partitions", "partitions_singleapp.csv")`
#      として `board_build.partitions` だけから CSV を決め、
#      `CONFIG_PARTITION_TABLE_FILENAME` を参照しない。直後の 2847 行は
#      `sdk_config.get("PARTITION_TABLE_OFFSET", 0x8000)` を読んでいるため、
#      これは意図的な部分参照である。Kconfig だけを設定してビルドしたとき、
#      焼かれるテーブルは `factory,app,factory,0x10000,1M` のままだった。
#    ⚠️ **`src_filter` が espidf で効かないのは事実だが、
#    「espidf では INI オプションが効かない」という一般化は誤りである。**
#    `board_build.partitions` は効く側のオプションであり、本節の当初版が
#    採っていた「Kconfig だけで指定する」という前提はこの実測で否定された。
#    ⚠️ 両者がずれると「サイズ検査は通るのに焼き込みか起動で失敗する」という
#    最も切り分けの難しい形で発現するため、一致検査がこの決定と不可分である。
# C. `[env:teleop]` が `[env:production]` と**同じ実証済みの層重ね機構**
#    （`board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="...;..."`）で
#    `sdkconfig.defaults.teleop` を共有設定の上へ重ねる。
# D. ⚠️ **共有設定 `sdkconfig.defaults` と本番専用 `sdkconfig.defaults.production`
#    へ無線有効化が漏れない。** 本番の無線非依存（同 1.2）はタスク 1.2 が別途
#    `firmware.map` で検証するが、「テレオペ専用設定が本番側の層へ現れない」ことは
#    ここで静的に固定できる。
#
# 生成物（`firmware/sdkconfig.teleop`）は `.gitignore` 済みのビルド出力であり、
# クリーンチェックアウトには存在しない。したがって生成物に対する検査は
# **存在する場合のみ**実施する（存在しなければ skip）。⚠️ プロンプトを持たない
# Kconfig シンボルへの代入が黙って無視される実測記録があるため
# （`sdkconfig.defaults.production` のコメント）、**「defaults に書いた」ことを
# 「反映された」ことの証拠にしない。** 反映の証拠は生成物の側にある。

SDKCONFIG_SHARED_PATH = FIRMWARE_DIR / "sdkconfig.defaults"
SDKCONFIG_TELEOP_PATH = FIRMWARE_DIR / "sdkconfig.defaults.teleop"
GENERATED_SDKCONFIG_TELEOP_PATH = FIRMWARE_DIR / "sdkconfig.teleop"

TELEOP_REQUIRED_SDKCONFIG_SETTINGS: dict[str, str] = {
    # 無線スタックの有効化（1.1）
    "CONFIG_BT_ENABLED": "y",
    # classic ESP32 のコントローラを BR/EDR 側で動かす（1.5）。
    # 既定は BLE only であり、明示的に選ばないと Bluetooth Classic にならない。
    "CONFIG_BTDM_CTRL_MODE_BR_EDR_ONLY": "y",
    # ホストスタック側の Classic Bluetooth（1.5）
    "CONFIG_BT_CLASSIC_ENABLED": "y",
    # 無線込み成果物が収まる大きい側のパーティション構成（1.3）
    "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE": "y",
}

TELEOP_ONLY_SDKCONFIG_KEYS: frozenset[str] = frozenset(TELEOP_REQUIRED_SDKCONFIG_SETTINGS)


def parse_sdkconfig_assignments(sdkconfig_text: str) -> dict[str, str]:
    """`sdkconfig` 形式のテキストから `CONFIG_X=value` の代入を抽出する。

    `# CONFIG_X is not set` 形式の行はコメントであり代入ではないが、生成物側
    では「無効」を表す正規の表現であるため、値 `"n"` として同じ辞書へ収める
    （defaults 側と生成物側を同じ検査関数で比較できるようにするため）。
    """
    assignments: dict[str, str] = {}
    for raw_line in sdkconfig_text.splitlines():
        line = raw_line.strip()
        not_set = re.match(r"^#\s*(CONFIG_[A-Za-z0-9_]+)\s+is not set$", line)
        if not_set is not None:
            assignments[not_set.group(1)] = "n"
            continue
        if line.startswith("#"):
            continue
        match = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", line)
        if match is not None:
            assignments[match.group(1)] = match.group(2).strip()
    return assignments


def find_teleop_radio_and_partition_violations(sdkconfig_text: str) -> list[str]:
    """テレオペ用設定に無線有効化とパーティション指定が揃っているかを検査する。"""
    assignments = parse_sdkconfig_assignments(sdkconfig_text)
    violations = []
    for key, expected in TELEOP_REQUIRED_SDKCONFIG_SETTINGS.items():
        actual = assignments.get(key)
        if actual is None:
            violations.append(f"{key} が指定されていない")
        elif actual != expected:
            violations.append(f"{key} が {expected!r} ではない: {actual!r}")
    return violations


# --- パーティション指定の一致（INI 側 と Kconfig 側） -----------------------
#
# ESP-IDF の `components/partition_table/Kconfig.projbuild` が
# `CONFIG_PARTITION_TABLE_FILENAME` の default 節で定めている
# 「選択肢シンボル → CSV ファイル名」の対応を、検査側にも持つ。
# ⚠️ 二重実装ではない: ここが持つのは「どのシンボルがどの CSV を意味するか」
# という対応表だけであり、パーティションテーブルの生成そのものは行わない。
# INI 側と Kconfig 側という2つの指定を突き合わせるには、両者を同じ土俵
# （CSV ファイル名）へ正規化する必要があり、その正規化に必要な最小の知識である。
KCONFIG_PARTITION_CHOICE_TO_CSV: dict[str, str] = {
    "CONFIG_PARTITION_TABLE_SINGLE_APP": "partitions_singleapp.csv",
    "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE": "partitions_singleapp_large.csv",
    "CONFIG_PARTITION_TABLE_TWO_OTA": "partitions_two_ota.csv",
}

IDF_DEFAULT_PARTITION_CSV = "partitions_singleapp.csv"
"""Kconfig の choice PARTITION_TABLE_TYPE の既定（`default PARTITION_TABLE_SINGLE_APP`）。"""

PLATFORMIO_FALLBACK_PARTITION_CSV = "partitions_singleapp.csv"
"""⚠️ `board_build.partitions` 未指定時に PlatformIO が使うハードコードの既定。

`platforms/espressif32/builder/frameworks/espidf.py:2846` の
`board.get("build.partitions", "partitions_singleapp.csv")` に由来する。
**Kconfig 側で大きいテーブルを選んでも、INI 側が未指定ならこの既定が焼かれる。**
一致検査がこの既定を「INI 側の実効値」として扱わないと、まさにその取りこぼし
（タスク 1.1 で実測された不具合）を検出できない。
"""


def resolve_partition_csv_from_ini(ini_text: str, section: str) -> str:
    """`board_build.partitions` の実効値（未指定なら PlatformIO の既定）を返す。"""
    cp = _load_ini(ini_text)
    if not cp.has_section(section):
        return PLATFORMIO_FALLBACK_PARTITION_CSV
    return cp.get(
        section, "board_build.partitions", fallback=PLATFORMIO_FALLBACK_PARTITION_CSV
    ).strip()


def resolve_partition_csv_from_kconfig(sdkconfig_text: str) -> str:
    """Kconfig 側の選択が意味する CSV ファイル名（未選択なら IDF の既定）を返す。"""
    assignments = parse_sdkconfig_assignments(sdkconfig_text)
    for symbol, csv_name in KCONFIG_PARTITION_CHOICE_TO_CSV.items():
        if assignments.get(symbol) == "y":
            return csv_name
    custom = assignments.get("CONFIG_PARTITION_TABLE_CUSTOM_FILENAME")
    if assignments.get("CONFIG_PARTITION_TABLE_CUSTOM") == "y" and custom is not None:
        return custom.strip('"')
    return IDF_DEFAULT_PARTITION_CSV


def find_partition_table_disagreement(
    ini_text: str, sdkconfig_text: str, section: str = "env:teleop"
) -> list[str]:
    """INI 側と Kconfig 側が同じパーティションテーブルを指しているかを検査する。

    ⚠️ **この一致こそが検査対象である。** どちらか片方だけを変更すると、
    「ESP-IDF のサイズ検査は 1.5MB を前提に通るのに、焼かれるテーブルは 1MB」
    という、最も切り分けの難しい失敗の形が生まれる（→ research.md の実測）。
    """
    from_ini = resolve_partition_csv_from_ini(ini_text, section)
    from_kconfig = resolve_partition_csv_from_kconfig(sdkconfig_text)
    if from_ini == from_kconfig:
        return []
    return [
        f"{section} のパーティション指定が一致しない: "
        f"board_build.partitions={from_ini!r} / Kconfig={from_kconfig!r}"
    ]


def parse_sdkconfig_defaults_layers(ini_text: str, section: str) -> list[str]:
    """`board_build.cmake_extra_args` の `-DSDKCONFIG_DEFAULTS="a;b"` を層の列として抽出する。"""
    cp = _load_ini(ini_text)
    if not cp.has_section(section):
        return []
    extra_args = cp.get(section, "board_build.cmake_extra_args", fallback="")
    match = re.search(r'-DSDKCONFIG_DEFAULTS=(?:"([^"]*)"|(\S+))', extra_args)
    if match is None:
        return []
    value = match.group(1) if match.group(1) is not None else match.group(2)
    return [layer.strip() for layer in value.split(";") if layer.strip()]


EXPECTED_SDKCONFIG_LAYERS: dict[str, list[str]] = {
    "env:teleop": ["sdkconfig.defaults", "sdkconfig.defaults.teleop"],
    "env:production": ["sdkconfig.defaults", "sdkconfig.defaults.production"],
}


def find_sdkconfig_layering_violations(ini_text: str) -> list[str]:
    """ファーム2環境の `SDKCONFIG_DEFAULTS` の層構成を検査する。

    - 共有設定を最初に置き、環境専用の設定をその上へ重ねること
    - 相手側の環境専用設定を取り込まないこと（層の交差を禁じる）
    """
    violations = []
    for section, expected in EXPECTED_SDKCONFIG_LAYERS.items():
        layers = parse_sdkconfig_defaults_layers(ini_text, section)
        if layers != expected:
            violations.append(f"{section} の SDKCONFIG_DEFAULTS が想定と異なる: {layers!r}")
    return violations


def find_teleop_only_settings_leaked(sdkconfig_text: str) -> list[str]:
    """テレオペ専用のキーが共有／本番専用の設定ファイルへ漏れていないかを検出する。"""
    assignments = parse_sdkconfig_assignments(sdkconfig_text)
    return [
        f"{key}={assignments[key]}"
        for key in sorted(TELEOP_ONLY_SDKCONFIG_KEYS)
        if key in assignments and assignments[key] != "n"
    ]


def test_teleop_sdkconfig_defaults_file_exists() -> None:
    """テレオペ専用の設定ファイルが実在する（1.1, 1.3）。"""
    assert SDKCONFIG_TELEOP_PATH.is_file(), f"{SDKCONFIG_TELEOP_PATH} が存在しない"


def test_teleop_sdkconfig_enables_radio_and_specifies_large_partition() -> None:
    """テレオペ専用設定が無線を有効化し、大きい側のパーティションを指定する（1.1, 1.3, 1.5）。"""
    text = SDKCONFIG_TELEOP_PATH.read_text(encoding="utf-8")
    assert find_teleop_radio_and_partition_violations(text) == []


def test_teleop_partition_table_agrees_between_ini_and_kconfig() -> None:
    """⚠️ INI 側と Kconfig 側が同じパーティションテーブルを指す（1.3 / research.md の決定）。"""
    teleop_sdkconfig_text = SDKCONFIG_TELEOP_PATH.read_text(encoding="utf-8")
    assert (
        find_partition_table_disagreement(PLATFORMIO_INI_TEXT, teleop_sdkconfig_text, "env:teleop")
        == []
    )


def test_teleop_ini_names_the_large_partition_table() -> None:
    """焼かれるテーブルを決める側（INI）が大きい側を指す（1.3）。

    ⚠️ Kconfig 側だけを大きくしても焼かれるテーブルは変わらない
    （`espidf.py:2846` の実測）。「実際に焼かれる側」を独立して固定する。
    """
    assert (
        resolve_partition_csv_from_ini(PLATFORMIO_INI_TEXT, "env:teleop")
        == "partitions_singleapp_large.csv"
    )


def test_production_partition_table_agrees_between_ini_and_kconfig() -> None:
    """本番側も両指定が一致している（どちらも既定のまま。テレオペの変更が波及していない）。"""
    assert (
        find_partition_table_disagreement(
            PLATFORMIO_INI_TEXT, SDKCONFIG_PRODUCTION_TEXT, "env:production"
        )
        == []
    )


def test_teleop_layers_its_own_sdkconfig_on_top_of_the_shared_one() -> None:
    """`[env:teleop]` が production と同じ機構で専用設定を層に重ねる（1.1）。"""
    assert find_sdkconfig_layering_violations(PLATFORMIO_INI_TEXT) == []


def test_shared_sdkconfig_defaults_has_no_teleop_only_setting() -> None:
    """共有設定へテレオペ専用の設定が漏れていない（既存ファイルへ触れない制約）。"""
    shared_text = SDKCONFIG_SHARED_PATH.read_text(encoding="utf-8")
    assert find_teleop_only_settings_leaked(shared_text) == []


def test_production_sdkconfig_defaults_has_no_teleop_only_setting() -> None:
    """本番専用設定へテレオペ専用の設定が漏れていない（1.2 を壊さないこと）。"""
    assert find_teleop_only_settings_leaked(SDKCONFIG_PRODUCTION_TEXT) == []


def test_generated_teleop_sdkconfig_reflects_radio_and_partition_when_present() -> None:
    """⚠️ 生成された設定に実際に反映されていることを確認する（1.1, 1.3, 1.5 の観測可能な完了状態）。

    プロンプトを持たない Kconfig シンボルへの代入は黙って無視されるため
    （`sdkconfig.defaults.production` の実測記録）、`sdkconfig.defaults.teleop`
    へ書いたことは反映の証拠にならない。生成物 `firmware/sdkconfig.teleop`
    はビルド出力（`.gitignore` 済み）なので、存在する場合のみ検査する。
    """
    if not GENERATED_SDKCONFIG_TELEOP_PATH.is_file():
        pytest.skip(
            f"{GENERATED_SDKCONFIG_TELEOP_PATH} が未生成。"
            "`pio run -e teleop` を実行すると生成される"
        )
    generated_text = GENERATED_SDKCONFIG_TELEOP_PATH.read_text(encoding="utf-8")
    assert find_teleop_radio_and_partition_violations(generated_text) == []
    assignments = parse_sdkconfig_assignments(generated_text)
    assert assignments.get("CONFIG_PARTITION_TABLE_FILENAME") == '"partitions_singleapp_large.csv"'
    # ⚠️ マージ後の設定が意味するテーブルと、実際に焼かれる側（INI）が一致すること。
    # 一致検査を defaults ではなく生成物に対しても適用する（生成物側でずれていれば、
    # そのビルドの成果物が「サイズ検査は通るのに起動しない」状態にある）。
    assert (
        find_partition_table_disagreement(PLATFORMIO_INI_TEXT, generated_text, "env:teleop") == []
    )


def test_detects_missing_radio_enable_in_crafted_input() -> None:
    """違反ケース: 無線有効化を欠いた架空のテレオペ設定が検出される。"""
    fake_sdkconfig = "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y\n"
    violations = find_teleop_radio_and_partition_violations(fake_sdkconfig)
    assert any("CONFIG_BT_ENABLED" in v for v in violations)


def test_detects_missing_partition_setting_in_crafted_input() -> None:
    """違反ケース: パーティション指定を欠いた架空のテレオペ設定が検出される。"""
    fake_sdkconfig = (
        "CONFIG_BT_ENABLED=y\n"
        "CONFIG_BTDM_CTRL_MODE_BR_EDR_ONLY=y\n"
        "CONFIG_BT_CLASSIC_ENABLED=y\n"
    )
    violations = find_teleop_radio_and_partition_violations(fake_sdkconfig)
    assert any("PARTITION_TABLE_SINGLE_APP_LARGE" in v for v in violations)


def test_detects_ble_only_controller_mode_in_crafted_input() -> None:
    """違反ケース: BR/EDR を選ばず既定の BLE only のままの架空の設定が検出される（1.5）。"""
    fake_sdkconfig = (
        "CONFIG_BT_ENABLED=y\n"
        "CONFIG_BTDM_CTRL_MODE_BLE_ONLY=y\n"
        "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y\n"
    )
    violations = find_teleop_radio_and_partition_violations(fake_sdkconfig)
    assert any("BR_EDR_ONLY" in v for v in violations)


def test_detects_radio_disabled_assignment_in_crafted_input() -> None:
    """違反ケース: 無線を明示的に無効化した架空のテレオペ設定が検出される。"""
    fake_sdkconfig = "CONFIG_BT_ENABLED=n\nCONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y\n"
    violations = find_teleop_radio_and_partition_violations(fake_sdkconfig)
    assert any("CONFIG_BT_ENABLED" in v for v in violations)


def test_detects_not_set_comment_form_as_disabled_in_crafted_input() -> None:
    """違反ケース: 生成物側の `# CONFIG_X is not set` 形式が無効として扱われる。"""
    fake_generated = "# CONFIG_BT_ENABLED is not set\nCONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y\n"
    violations = find_teleop_radio_and_partition_violations(fake_generated)
    assert any("CONFIG_BT_ENABLED" in v for v in violations)


_LARGE_CSV = "partitions_singleapp_large.csv"
_INI_WITH_LARGE = f"[env:teleop]\nboard_build.partitions = {_LARGE_CSV}\n"
_INI_WITHOUT_PARTITIONS = "[env:teleop]\nlib_ldf_mode = off\n"
_SDKCONFIG_WITH_LARGE = "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE=y\n"
_SDKCONFIG_WITHOUT_PARTITION_CHOICE = "CONFIG_BT_ENABLED=y\n"


def test_partition_agreement_holds_when_both_sides_name_the_large_table() -> None:
    """誤検知回避: 両側が同じテーブルを指していれば違反なし。"""
    assert find_partition_table_disagreement(_INI_WITH_LARGE, _SDKCONFIG_WITH_LARGE) == []


def test_detects_kconfig_only_partition_change_in_crafted_input() -> None:
    """違反ケース: ⚠️ **Kconfig 側だけを大きくした**架空の入力が検出される。

    タスク 1.1 で実際に踏んだ不具合そのものである。INI 側が未指定だと
    PlatformIO のハードコード既定（1MB）が焼かれるため、Kconfig だけの
    変更は成果物に届かない。
    """
    violations = find_partition_table_disagreement(
        _INI_WITHOUT_PARTITIONS, _SDKCONFIG_WITH_LARGE
    )
    assert violations != []
    assert "partitions_singleapp.csv" in violations[0]
    assert _LARGE_CSV in violations[0]


def test_detects_ini_only_partition_change_in_crafted_input() -> None:
    """違反ケース: ⚠️ **INI 側だけを大きくした**架空の入力が検出される。

    焼かれるテーブルは 1.5MB になるが、ESP-IDF のアプリサイズ検査は 1MB を
    前提に通り続ける。逆向きのずれも同じく検出できることを示す。
    """
    violations = find_partition_table_disagreement(
        _INI_WITH_LARGE, _SDKCONFIG_WITHOUT_PARTITION_CHOICE
    )
    assert violations != []
    assert _LARGE_CSV in violations[0]


def test_detects_two_sides_naming_different_non_default_tables_in_crafted_input() -> None:
    """違反ケース: 両側が指定済みでも別々のテーブルを指していれば検出される。"""
    fake_ini = "[env:teleop]\nboard_build.partitions = partitions_two_ota.csv\n"
    violations = find_partition_table_disagreement(fake_ini, _SDKCONFIG_WITH_LARGE)
    assert violations != []


def test_partition_agreement_is_not_hardcoded_to_the_large_table_in_crafted_input() -> None:
    """誤検知回避: 一致検査は「大きいテーブルであること」ではなく「一致」を見る。

    将来 OTA 構成へ移る等でテーブルを変えたとき、両側を揃えれば通ること。
    """
    fake_ini = "[env:teleop]\nboard_build.partitions = partitions_two_ota.csv\n"
    fake_sdkconfig = "CONFIG_PARTITION_TABLE_TWO_OTA=y\n"
    assert find_partition_table_disagreement(fake_ini, fake_sdkconfig) == []


def test_partition_agreement_holds_when_neither_side_specifies_in_crafted_input() -> None:
    """誤検知回避: 両側とも未指定なら、それぞれの既定が一致するので違反なし。"""
    assert (
        find_partition_table_disagreement(
            _INI_WITHOUT_PARTITIONS, _SDKCONFIG_WITHOUT_PARTITION_CHOICE
        )
        == []
    )


def test_detects_missing_large_table_on_the_flashed_side_in_crafted_input() -> None:
    """違反ケース: 焼かれる側が既定の 1MB のままの架空の入力が検出される（1.3）。"""
    assert resolve_partition_csv_from_ini(_INI_WITHOUT_PARTITIONS, "env:teleop") != _LARGE_CSV


def test_detects_missing_sdkconfig_layer_in_crafted_input() -> None:
    """違反ケース: `[env:teleop]` が専用設定を層に重ねていない架空の入力が検出される。"""
    fake_ini = (
        "[env:teleop]\nlib_ldf_mode = off\n\n"
        "[env:production]\n"
        'board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;'
        'sdkconfig.defaults.production"\n'
    )
    violations = find_sdkconfig_layering_violations(fake_ini)
    assert any("teleop" in v for v in violations)


def test_detects_teleop_layer_dropping_the_shared_defaults_in_crafted_input() -> None:
    """違反ケース: 共有設定を外して専用設定だけを指す架空の入力が検出される。"""
    fake_ini = (
        "[env:teleop]\n"
        'board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults.teleop"\n\n'
        "[env:production]\n"
        'board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;'
        'sdkconfig.defaults.production"\n'
    )
    violations = find_sdkconfig_layering_violations(fake_ini)
    assert any("teleop" in v for v in violations)


def test_detects_crossed_sdkconfig_layers_in_crafted_input() -> None:
    """違反ケース: 本番の層へテレオペ専用設定が混入した架空の入力が検出される（1.2 の保全）。"""
    fake_ini = (
        "[env:teleop]\n"
        'board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;'
        'sdkconfig.defaults.teleop"\n\n'
        "[env:production]\n"
        'board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;'
        'sdkconfig.defaults.production;sdkconfig.defaults.teleop"\n'
    )
    violations = find_sdkconfig_layering_violations(fake_ini)
    assert any("production" in v for v in violations)


def test_detects_teleop_only_setting_leaked_into_shared_file_in_crafted_input() -> None:
    """違反ケース: 共有設定へ無線有効化が書かれた架空の入力が検出される。"""
    assert find_teleop_only_settings_leaked("CONFIG_BT_ENABLED=y\n") != []


def test_does_not_flag_production_style_disabled_assignment_in_crafted_input() -> None:
    """誤検知回避: 本番側の `CONFIG_BT_ENABLED=n` は「漏れ」ではない。"""
    assert find_teleop_only_settings_leaked("CONFIG_BT_ENABLED=n\n") == []
