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
