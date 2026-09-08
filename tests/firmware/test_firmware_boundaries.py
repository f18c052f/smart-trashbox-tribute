"""ビルド構成の静的境界検査（タスク 1.3、要件 1.3, 1.4, 1.6, 1.7, 16.3, 16.7）。

`firmware/platformio.ini` / `firmware/CMakeLists.txt` /
`firmware/lib/drivetrain_control/CMakeLists.txt` / `firmware/src/build_profile.hpp` /
`firmware/sdkconfig.defaults.production` をテキストとして読み、
`tests/prediction_core/test_boundaries.py` /
`tests/trajectory_sim/test_trajectory_sim_boundaries.py` と同じ方針
（検査ロジックを純粋関数として切り出し、実ファイルへの適用と「違反を含む
架空の入力文字列」への適用の両方をテストする）で、以下の点を固定する。

1. **ソース集合の一致**: `lib/drivetrain_control/CMakeLists.txt` の
   `idf_component_register(SRCS ...)` が列挙するファイル集合と、
   `lib/drivetrain_control/src/*.cpp` の実体集合が一致すること
   （タスク1.1 Risk R2: 「`SRCS` 更新漏れで `native` だけ通る」の回帰）。
2. **テスト専用ライブラリの非混入**: ルート `CMakeLists.txt` の
   `EXTRA_COMPONENT_DIRS` が `lib` ディレクトリ全体ではなく、許可した
   コンポーネントディレクトリを個別に直接指しており、テスト専用
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
9. **端子割当コンポーネント `board_pins` の登録**（teleop-bringup 要件 2.1）:
   `firmware/lib/board_pins/` が IDF コンポーネント manifest（`CMakeLists.txt`）と
   PlatformIO manifest（`library.json`）を同居させ、ホストビルドと実機ビルドの
   双方から見えること。ルート `CMakeLists.txt` へ個別に登録されていること。
   そして `firmware/src/CMakeLists.txt` がこれをテレオペ用ビルドのときだけ
   `REQUIRES` へ足し、本番の `COMPONENTS` allowlist へ触れずに済む形で
   あること。
10. **パッド入力コンポーネント `teleop_input` の登録**（teleop-bringup
    タスク 4.1、要件 8.6, 8.7, 8.8, 8.9）: `firmware/lib/teleop_input/` が
    board_pins と同じ二重マニフェストの形でホストビルドと実機ビルドの
    双方から見えること。ルート `CMakeLists.txt` へ個別に登録されていること。
    そして `firmware/src/CMakeLists.txt` がこれをテレオペ用ビルドのときだけ
    `REQUIRES` へ足し、本番の `COMPONENTS` allowlist へ触れずに済む形で
    あること。

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
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_DIR = REPO_ROOT / "firmware"
PLATFORMIO_INI_PATH = FIRMWARE_DIR / "platformio.ini"
ROOT_CMAKE_PATH = FIRMWARE_DIR / "CMakeLists.txt"
LIB_CMAKE_PATH = FIRMWARE_DIR / "lib" / "drivetrain_control" / "CMakeLists.txt"
LIB_SRC_DIR = FIRMWARE_DIR / "lib" / "drivetrain_control" / "src"
# teleop-bringup タスク 1.3（要件 2.1）: 端子割当の正のコンポーネント。
APP_CMAKE_PATH = FIRMWARE_DIR / "src" / "CMakeLists.txt"
BOARD_PINS_DIR = FIRMWARE_DIR / "lib" / "board_pins"
BOARD_PINS_CMAKE_PATH = BOARD_PINS_DIR / "CMakeLists.txt"
BOARD_PINS_LIBRARY_JSON_PATH = BOARD_PINS_DIR / "library.json"
BOARD_PINS_SRC_DIR = BOARD_PINS_DIR / "src"
BOARD_PINS_INCLUDE_DIR = BOARD_PINS_DIR / "include" / "board_pins"
# teleop-bringup タスク 4.1（要件 8.6, 8.7, 8.8, 8.9）: 正規化済みパッド状態と
# 変換パラメータの型のコンポーネント。
TELEOP_INPUT_DIR = FIRMWARE_DIR / "lib" / "teleop_input"
TELEOP_INPUT_CMAKE_PATH = TELEOP_INPUT_DIR / "CMakeLists.txt"
TELEOP_INPUT_LIBRARY_JSON_PATH = TELEOP_INPUT_DIR / "library.json"
TELEOP_INPUT_SRC_DIR = TELEOP_INPUT_DIR / "src"
TELEOP_INPUT_INCLUDE_DIR = TELEOP_INPUT_DIR / "include" / "teleop_input"
TEST_SUPPORT_DIR = FIRMWARE_DIR / "lib" / "test_support"
BUILD_PROFILE_HPP_PATH = FIRMWARE_DIR / "src" / "build_profile.hpp"
SDKCONFIG_PRODUCTION_PATH = FIRMWARE_DIR / "sdkconfig.defaults.production"
TEST_NATIVE_DIR = FIRMWARE_DIR / "test" / "native"
TEST_EMBEDDED_DIR = FIRMWARE_DIR / "test" / "embedded"
LIB_INCLUDE_ROOT_DIR = FIRMWARE_DIR / "lib" / "drivetrain_control" / "include" / "drivetrain_control"
# teleop-bringup タスク 3.4（要件 17.3, 17.5）: アダプタ層（3.1〜3.3 の3実装）。
TELEOP_SRC_DIR = FIRMWARE_DIR / "src" / "teleop"

PLATFORMIO_INI_TEXT = PLATFORMIO_INI_PATH.read_text(encoding="utf-8")
ROOT_CMAKE_TEXT = ROOT_CMAKE_PATH.read_text(encoding="utf-8")
LIB_CMAKE_TEXT = LIB_CMAKE_PATH.read_text(encoding="utf-8")
BUILD_PROFILE_HPP_TEXT = BUILD_PROFILE_HPP_PATH.read_text(encoding="utf-8")
APP_CMAKE_TEXT = APP_CMAKE_PATH.read_text(encoding="utf-8")
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


def find_unexpected_embedded_component_dirs(
    cmake_text: str, allowlist: frozenset[str]
) -> list[str]:
    """`EXTRA_COMPONENT_DIRS` の各引数が、想定したコンポーネントディレクトリを
    **個別に**指しているかを検査する（要件16.3）。

    許可された名前以外の末尾要素（`lib` 丸ごと・`test_support` 等）を持つ引数を
    違反として返す。空列であれば違反なし。
    """
    return [
        arg
        for arg in parse_extra_component_dirs_append_args(cmake_text)
        if arg.replace("\\", "/").rstrip("/").split("/")[-1] not in allowlist
    ]


# 組込みビルドの探索対象として明示的に許可するコンポーネントディレクトリ名。
# ⚠️ ここへ名前を足すことは「そのディレクトリを実機ビルドから見えるようにする」
# 決定そのものである。`lib/test_support/`（ホスト専用）は決して含めない。
#   - drivetrain_control: 純ロジックの核（drivetrain-core タスク 1.1）
#   - board_pins: 端子割当の正（teleop-bringup タスク 1.3、要件 2.1）
#   - teleop_input: 正規化済みパッド状態と変換パラメータの型
#     （teleop-bringup タスク 4.1、要件 8.6-8.9）
#   - components: `firmware/.deps/bluepad32/src/components`（Bluepad32 +
#     BTstack。teleop-bringup タスク 5.1、要件 7.1）を指す
#     `list(APPEND EXTRA_COMPONENT_DIRS ...)` の末尾要素。⚠️ この照合は
#     パス末尾要素のみを見るため、他の目的の `.../components` ディレクトリ
#     も同名で通ってしまう（タスク 1.3 実装ノートに記録済みの既知の緩み）。
#     firmware/CMakeLists.txt 側では `.deps/bluepad32/src/components` への
#     参照が `DEFINED ENV{DRIVETRAIN_BUILD_TELEOP}` でも重ねてゲートされて
#     おり、そちらが実質的な絞り込みを担う。lib/ 丸ごとの混入を防ぐという
#     本節の主目的（`lib/test_support/` を除外すること）は変わらず成立する。
EMBEDDED_COMPONENT_DIR_ALLOWLIST: frozenset[str] = frozenset(
    {"drivetrain_control", "board_pins", "teleop_input", "components"}
)


def test_root_cmakelists_extra_component_dirs_points_at_individual_components() -> None:
    """`EXTRA_COMPONENT_DIRS` が各コンポーネントを直接指す（`lib` 丸ごとではない）。"""
    args = parse_extra_component_dirs_append_args(ROOT_CMAKE_TEXT)
    assert args != [], "EXTRA_COMPONENT_DIRS への list(APPEND ...) が見つからない"
    assert (
        find_unexpected_embedded_component_dirs(
            ROOT_CMAKE_TEXT, EMBEDDED_COMPONENT_DIR_ALLOWLIST
        )
        == []
    )
    assert find_test_only_library_leak_into_embedded_search(ROOT_CMAKE_TEXT) == []
    assert "test_support" not in EMBEDDED_COMPONENT_DIR_ALLOWLIST


def test_detects_unlisted_component_dir_in_extra_component_dirs_in_crafted_input() -> None:
    """違反ケース: 許可していないディレクトリ（`lib/test_support`）の追加が検出される。"""
    fake_cmake = (
        'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/drivetrain_control")\n'
        'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/test_support")\n'
    )
    violations = find_unexpected_embedded_component_dirs(
        fake_cmake, EMBEDDED_COMPONENT_DIR_ALLOWLIST
    )
    assert violations != []
    assert any("test_support" in v for v in violations)


def test_does_not_flag_allowlisted_component_dirs_in_crafted_input() -> None:
    """誤検知回避: 許可済みのコンポーネントを個別に列挙する形は違反にしない。"""
    fake_cmake = (
        'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/drivetrain_control")\n'
        'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/lib/board_pins")\n'
    )
    assert (
        find_unexpected_embedded_component_dirs(fake_cmake, EMBEDDED_COMPONENT_DIR_ALLOWLIST)
        == []
    )


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
    # teleop-bringup タスク 5.1（requirements.md 7.1）: ホストスタックを
    # BTstack へ切り替える。BTstack は自前のホストスタックであり既定の
    # Bluedroid とは共存しないため、Bluedroid ホストを無効化する
    # CONFIG_BT_CONTROLLER_ONLY=y が要る（Bluepad32 の raw ESP-IDF 版・
    # bluekitchen/btstack の port/esp32 参照 sdkconfig.defaults の双方で
    # 実測確認済み）。⚠️ タスク1.1が置いた CONFIG_BT_CLASSIC_ENABLED は
    # Bluedroid ホスト配下のシンボルであり、BT_CONTROLLER_ONLY=y の下では
    # プロンプトを失い代入が黙って無視されるため、この置き換えで正しい
    # （sdkconfig.defaults.teleop の該当コメント参照）。
    "CONFIG_BT_CONTROLLER_ONLY": "y",
    # 無線込み成果物が収まる大きい側のパーティション構成（1.3）
    "CONFIG_PARTITION_TABLE_SINGLE_APP_LARGE": "y",
    # teleop-bringup タスク 5.2（requirements.md 7.2-7.6, 14.8）: Bluepad32
    # のターゲットプラットフォームをカスタムへ切り替える。タスク 5.1 は既定の
    # UNIJOYSTICLE のまま残し、「CUSTOM への切替とコールバック実装
    # （vtable 供給）はタスク 5.2 の担当」と明記していた（5.1 Implementation
    # Notes）。CUSTOM でなければ firmware/src/teleop/controller_link.cpp が
    # 実装する `struct uni_platform` vtable（DualSense の生値を
    # teleop_input::PadState へ正規化する、唯一 Bluepad32 の型に触れる場所）
    # が使われず、既定の Unijoysticle デモプラットフォームのままになる。
    "CONFIG_BLUEPAD32_PLATFORM_CUSTOM": "y",
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
        "CONFIG_BT_CONTROLLER_ONLY=y\n"
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


# =============================================================================
# teleop-bringup タスク 1.2: 本番成果物への無線非混入 / 排他機構の再確認 /
# 無線ライブラリのライセンス制約の記載
# （teleop-bringup requirements.md 1.2, 1.4, 1.6 /
#   design.md TeleopBuildProfile・Security Considerations・
#   Testing Strategy「実機検証」5）
# =============================================================================
#
# ⚠️ **本節の要件番号も `.kiro/specs/teleop-bringup/requirements.md` の採番である。**
#
# 固定する事項:
#
# A. **本番成果物のリンク結果に無線ライブラリへの参照が1件も無い**（1.2）。
#    検証手法は `drivetrain-core` タスク 1.2 が実証済みのもの
#    （`firmware/CMakeLists.txt` のコメント参照）をそのまま使う。
#    ⚠️ **本番のビルド構成には一切触れずに確かめる。** テレオペ側へ無線を足した
#    あと（タスク 1.1）も本番が影響を受けていないことが本タスクの主張である。
# B. **両プロファイル同時指定でビルドが失敗する**（1.4）。⚠️ `#error` が
#    「書いてある」ことではなく、**実際にコンパイルが失敗すること**を示す。
#    `firmware/src/build_profile.hpp` は自己完結したヘッダであり、ホストの
#    C++ コンパイラで `-fsyntax-only` にかけるだけで排他機構そのものを
#    再現できる（ESP-IDF もクロスツールチェーンも要らない）。
# C. **無線ライブラリのライセンス制約が、配布時に誤読されない形で記載されている**
#    （1.6、OQ-42）。記載先はリポジトリ直下の `README.md`。
#
# ---------------------------------------------------------------------------
# ⚠️⚠️ 実測記録: `firmware.map` は「リンクマップとは限らない」（本タスクで判明）
# ---------------------------------------------------------------------------
#
# `pio run` が置く `.pio/build/<env>/firmware.map` は、**アプリのリンクマップで
# ある場合と、CMake のコンパイラ ABI 検査（try-compile）が残した別物である場合の
# 両方がある。** 後者を素朴に grep すると「無線ライブラリへの参照ゼロ」が
# **常に成立してしまい、検査が空虚になる。**
#
# 機構（`~/.platformio/platforms/espressif32/builder/frameworks/espidf.py:2677-2679`
# を実測）:
#
#   extra_cmake_args.append(
#       f"-DCMAKE_EXE_LINKER_FLAGS=-Wl,-Map={BUILD_DIR}/{PROGNAME}.map")
#
# `CMAKE_EXE_LINKER_FLAGS` は **そのビルドディレクトリで行われるすべての実行形式
# リンクへ適用される。** CMake が configure のたびに走らせるコンパイラ ABI 検査
# （`CMakeFiles/cmTC_*.dir/CMakeCXXCompilerABI.cpp.obj` をリンクする try-compile）
# もその対象であり、**リンク後に configure が走ると `firmware.map` は 8,538 バイトの
# ABI 検査マップで上書きされる。**
#
# 実測（2026-09-08、ESP-IDF 5.5.5 / pioarduino 55.03.311）:
#
#   - 本番: 再リンクを伴う `pio run -e production` 直後の `firmware.map` は
#     25,609 行の本物のリンクマップで、`libbt.a` / `libesp_wifi.a` /
#     `libesp_phy.a` / `libwpa_supplicant.a` への参照は **0 件**。
#     参照されているアーカイブは libdrivetrain_control.a / libfreertos.a /
#     libesp_system.a / libesp_driver_gpio.a などの非無線コンポーネントのみ。
#   - テレオペ: 同条件の本物のリンクマップ（87,634 行）には **無線ライブラリへの
#     参照が 9,770 件**あり、ビルドツリーにも libbt.a / libesp_wifi.a /
#     libesp_phy.a / libwpa_supplicant.a / libesp_coex.a の5アーカイブが生成
#     されている（本番のビルドツリーは 0）。
#     → 検査関数が実データで確かに発火することの陽性対照であり、
#       本番側の 0 件が「見ていないから 0」ではないことの根拠である。
#   - ⚠️ 一方、**再リンクが起きなかった `pio run` の直後**（2回目以降の実行や、
#     リンク後に configure が走った実行）の `firmware.map` は 326 行の ABI 検査
#     マップであり、テレオペ環境でさえ無線参照が 0 件になった。
#
# したがって本節の検査は、**参照を数える前にそのファイルが本物のアプリリンク
# マップかを判定する**（`classify_link_map`）。判定できない成果物に対して
# 「参照ゼロ」と結論しない。

PRODUCTION_BUILD_DIR = FIRMWARE_DIR / ".pio" / "build" / "production"
TELEOP_BUILD_DIR = FIRMWARE_DIR / ".pio" / "build" / "teleop"
LINK_MAP_FILENAME = "firmware.map"

# リンク結果に現れてはならない無線ライブラリのアーカイブ名。
# 前5つは ESP-IDF 標準の無線スタック（`drivetrain-core` タスク1.2 で実証済みの対象）。
# 後3つはテレオペが今後取り込む予定の外部スタックで、取り込み後も本番へ
# 漏れないことを同じ検査で押さえるために先に挙げてある。
RADIO_ARCHIVE_NAMES: tuple[str, ...] = (
    "libbt.a",
    "libbtdm_app.a",
    "libesp_wifi.a",
    "libesp_phy.a",
    "libwpa_supplicant.a",
    "libesp_coex.a",
    "libbtstack.a",
    "libbluepad32.a",
)

# 本物のアプリリンクマップだけが持つ痕跡。ESP-IDF のアプリリンクでは必ず
# 現れるコンポーネントと、本プロジェクト自身の純ロジックコンポーネント。
APP_LINK_MAP_SENTINELS: tuple[str, ...] = (
    "libfreertos.a(",
    "libesp_system.a(",
    "libdrivetrain_control.a(",
)

# CMake の try-compile が生成したオブジェクトの名前。ABI 検査マップの目印。
CMAKE_TRY_COMPILE_MARKER = "cmTC_"


def classify_link_map(map_text: str) -> str:
    """`firmware.map` の中身が何であるかを判定する。

    戻り値:
      - ``"application"``       … アプリのリンクマップ（参照ゼロの主張が意味を持つ）
      - ``"cmake-try-compile"`` … CMake のコンパイラ ABI 検査が残したマップ
      - ``"unrecognized"``      … どちらとも判定できない
    """
    if CMAKE_TRY_COMPILE_MARKER in map_text:
        return "cmake-try-compile"
    if all(sentinel in map_text for sentinel in APP_LINK_MAP_SENTINELS):
        return "application"
    return "unrecognized"


def find_radio_references_in_link_map(map_text: str) -> list[str]:
    """リンクマップ本文から無線ライブラリのアーカイブへの参照を洗い出す。"""
    violations = []
    for lineno, line in enumerate(map_text.splitlines(), start=1):
        for archive in RADIO_ARCHIVE_NAMES:
            if archive in line:
                violations.append(f"{lineno}行目: {archive}")
    return violations


def find_radio_archive_files(build_dir: Path) -> list[str]:
    """ビルドディレクトリに無線ライブラリのアーカイブそのものが生成されていないか調べる。

    リンクマップ（参照の有無）とは独立した第2の証拠。参照が無いだけでなく、
    そもそもコンパイルもされていないことを見る。
    """
    return sorted(
        str(path.relative_to(build_dir))
        for path in build_dir.rglob("lib*.a")
        if path.name in RADIO_ARCHIVE_NAMES
    )


def _load_link_map_or_skip(build_dir: Path, env_name: str) -> str:
    """本物のアプリリンクマップを読む。無ければ理由を示して skip する。"""
    map_path = build_dir / LINK_MAP_FILENAME
    if not map_path.is_file():
        pytest.skip(f"{map_path} が未生成。`cd firmware && pio run -e {env_name}` で生成される")
    map_text = map_path.read_text(encoding="utf-8", errors="replace")
    kind = classify_link_map(map_text)
    if kind != "application":
        pytest.skip(
            f"{map_path} はアプリのリンクマップではない（判定: {kind}）。"
            "再リンクを伴わない `pio run` の後は CMake の ABI 検査マップで上書きされている。"
            f"`cd firmware && rm .pio/build/{env_name}/firmware.elf && "
            f"pio run -e {env_name}` で本物のリンクマップを再生成すること"
        )
    return map_text


# --- A. 本番成果物への無線非混入（1.2） -------------------------------------


def test_production_link_map_has_zero_radio_library_references() -> None:
    """⚠️ 本番のリンク結果に無線ライブラリへの参照が1件も無い（1.2 の観測可能な完了状態）。

    テレオペ側へ無線を足したあと（タスク 1.1）も本番が影響を受けていないことを、
    **本番のビルド構成を一切変更せずに**確かめる。
    """
    map_text = _load_link_map_or_skip(PRODUCTION_BUILD_DIR, "production")
    assert find_radio_references_in_link_map(map_text) == []


def test_production_build_tree_contains_no_radio_archive() -> None:
    """本番のビルドツリーに無線ライブラリのアーカイブ自体が生成されていない（1.2 の補強）。"""
    if not PRODUCTION_BUILD_DIR.is_dir():
        pytest.skip(
            f"{PRODUCTION_BUILD_DIR} が未生成。"
            "`cd firmware && pio run -e production` で生成される"
        )
    assert find_radio_archive_files(PRODUCTION_BUILD_DIR) == []


def test_teleop_link_map_does_contain_radio_library_references() -> None:
    """⚠️ 陽性対照: テレオペのリンク結果には無線ライブラリへの参照が**ある**。

    これが無いと、本番側の「参照ゼロ」が「検査が何も見ていないから 0」なのか
    「本当に入っていないから 0」なのか区別できない。実測では 9,770 件。
    """
    map_text = _load_link_map_or_skip(TELEOP_BUILD_DIR, "teleop")
    assert find_radio_references_in_link_map(map_text) != []


def test_detects_radio_reference_in_crafted_link_map() -> None:
    """違反ケース: 無線ライブラリを参照するリンクマップが検出される。

    入力は実測したテレオペのリンクマップから逐語コピーした行である。
    """
    real_teleop_map_excerpt = (
        "Archive member included to satisfy reference by file (symbol)\n"
        "\n"
        ".pio/build/teleop/esp-idf/bt/libbt.a(hli_vectors.S.o)\n"
        "                              (ld_include_hli_vectors_bt)\n"
        ".pio/build/teleop/esp-idf/bt/libbt.a(hli_api.c.o)\n"
        "                              .pio/build/teleop/esp-idf/bt/libbt.a"
        "(hli_vectors.S.o) (hli_c_handler)\n"
        ".pio/build/teleop/esp-idf/esp_phy/libesp_phy.a(phy_override.c.o)\n"
        ".pio/build/teleop/esp-idf/drivetrain_control/libdrivetrain_control.a"
        "(controller.cpp.o)\n"
        ".pio/build/teleop/esp-idf/freertos/libfreertos.a(idf_additions.c.o)\n"
        ".pio/build/teleop/esp-idf/esp_system/libesp_system.a(panic.c.o)\n"
    )
    assert classify_link_map(real_teleop_map_excerpt) == "application"
    violations = find_radio_references_in_link_map(real_teleop_map_excerpt)
    assert any("libbt.a" in v for v in violations)
    assert any("libesp_phy.a" in v for v in violations)


def test_does_not_flag_clean_link_map_in_crafted_input() -> None:
    """誤検知回避: 無線を含まないリンクマップは違反にならない。

    入力は実測した本番のリンクマップから逐語コピーした行である。
    """
    real_production_map_excerpt = (
        "Archive member included to satisfy reference by file (symbol)\n"
        "\n"
        ".pio/build/production/esp-idf/drivetrain_control/libdrivetrain_control.a"
        "(controller.cpp.o)\n"
        "                              .pio/build/production/src/main.cpp.o "
        "(_ZN18drivetrain_control20DrivetrainController4stepEx)\n"
        ".pio/build/production/esp-idf/freertos/libfreertos.a(idf_additions.c.o)\n"
        ".pio/build/production/esp-idf/esp_system/libesp_system.a(panic.c.o)\n"
        ".pio/build/production/esp-idf/esp_driver_gpio/libesp_driver_gpio.a(gpio.c.o)\n"
    )
    assert classify_link_map(real_production_map_excerpt) == "application"
    assert find_radio_references_in_link_map(real_production_map_excerpt) == []


def test_cmake_try_compile_map_is_not_accepted_as_a_link_result() -> None:
    """⚠️ 空虚化の防止: ABI 検査マップを「参照ゼロの本番リンク結果」と認めない。

    入力は実測した 8,538 バイト版 `firmware.map` からの逐語コピーである。
    無線参照は 0 件だが、これは**リンク結果ではない**ため
    「本番に無線が入っていない」ことの証拠にならない。
    """
    real_try_compile_map_excerpt = (
        "\nThere are no discarded input sections\n"
        "\nMemory Configuration\n"
        "\nLinker script and memory map\n"
        "\nLOAD CMakeFiles/cmTC_28ca2.dir/CMakeCXXCompilerABI.cpp.obj\n"
        "LOAD /home/user/.platformio/packages/toolchain-xtensa-esp-elf/bin/../lib/"
        "gcc/xtensa-esp-elf/14.2.0/../../../../xtensa-esp-elf/lib/esp32/no-rtti/"
        "libstdc++.a\n"
    )
    assert find_radio_references_in_link_map(real_try_compile_map_excerpt) == []
    assert classify_link_map(real_try_compile_map_excerpt) == "cmake-try-compile"


def test_unrecognized_map_is_not_accepted_as_a_link_result() -> None:
    """空虚化の防止: アプリリンクの痕跡を持たない任意のテキストも受け入れない。"""
    assert classify_link_map("") == "unrecognized"
    assert classify_link_map("Linker script and memory map\n") == "unrecognized"


def test_detects_radio_archive_file_in_crafted_build_tree(tmp_path: Path) -> None:
    """違反ケース: ビルドツリーに `libbt.a` が生成された状態が検出される。"""
    (tmp_path / "esp-idf" / "bt").mkdir(parents=True)
    (tmp_path / "esp-idf" / "bt" / "libbt.a").write_bytes(b"")
    (tmp_path / "esp-idf" / "log").mkdir(parents=True)
    (tmp_path / "esp-idf" / "log" / "liblog.a").write_bytes(b"")
    assert find_radio_archive_files(tmp_path) != []


def test_does_not_flag_non_radio_archive_in_crafted_build_tree(tmp_path: Path) -> None:
    """誤検知回避: 無線と無関係のアーカイブだけのビルドツリーは違反にならない。"""
    (tmp_path / "esp-idf" / "log").mkdir(parents=True)
    (tmp_path / "esp-idf" / "log" / "liblog.a").write_bytes(b"")
    assert find_radio_archive_files(tmp_path) == []


# --- B. 両プロファイル同時指定でビルドが失敗すること（1.4） -----------------
#
# ⚠️ **`#error` が書いてあることの確認では足りない。** 実際にコンパイルさせて
# 失敗することを示す。`build_profile.hpp` は他のヘッダを include しない自己完結
# ヘッダなので、ホストの C++ コンパイラで `-fsyntax-only` にかけるだけで
# 排他機構そのものを再現できる。

HOST_CXX_COMPILER = shutil.which("g++") or shutil.which("clang++") or shutil.which("c++")


def compile_build_profile_header(*defined_macros: str) -> subprocess.CompletedProcess[str]:
    """指定したビルドプロファイルマクロの下で `build_profile.hpp` を構文検査する。"""
    assert HOST_CXX_COMPILER is not None
    return subprocess.run(
        [
            HOST_CXX_COMPILER,
            "-x",
            "c++",
            "-std=c++17",
            "-fsyntax-only",
            *[f"-D{macro}" for macro in defined_macros],
            str(BUILD_PROFILE_HPP_PATH),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


requires_host_cxx = pytest.mark.skipif(
    HOST_CXX_COMPILER is None,
    reason="ホストの C++ コンパイラが見つからない（g++ / clang++ / c++）",
)


@requires_host_cxx
def test_compilation_fails_when_both_build_profile_macros_are_defined() -> None:
    """⚠️ 両プロファイル同時指定で**実際にコンパイルが失敗する**（1.4）。

    テレオペ側へ無線設定を足したあと（タスク 1.1）も排他機構が働くことの再確認。
    """
    result = compile_build_profile_header(
        "DRIVETRAIN_BUILD_TELEOP", "DRIVETRAIN_BUILD_PRODUCTION"
    )
    assert result.returncode != 0, "両方定義したのにコンパイルが通ってしまった"
    assert "mutually exclusive" in result.stderr


@requires_host_cxx
def test_compilation_fails_when_neither_build_profile_macro_is_defined() -> None:
    """どちらのプロファイルも指定しない場合も実際にコンパイルが失敗する（1.4）。"""
    result = compile_build_profile_header()
    assert result.returncode != 0, "どちらも未定義なのにコンパイルが通ってしまった"
    assert "Exactly one build-profile macro" in result.stderr


@requires_host_cxx
@pytest.mark.parametrize("macro", ["DRIVETRAIN_BUILD_TELEOP", "DRIVETRAIN_BUILD_PRODUCTION"])
def test_compilation_succeeds_when_exactly_one_build_profile_macro_is_defined(
    macro: str,
) -> None:
    """陰性対照: 片方だけならコンパイルは通る（`#error` が常に出るわけではない）。"""
    result = compile_build_profile_header(macro)
    assert result.returncode == 0, result.stderr


# --- C. 無線ライブラリのライセンス制約の記載（1.6） -------------------------

README_PATH = REPO_ROOT / "README.md"
RADIO_LICENSE_SECTION_HEADING = "## ライセンスと再配布上の制約"

# 記載が満たすべき点。⚠️ 「BTstack を使う」だけでは配布時の誤読を防げない。
# 制約の内容・及ぶ範囲・及ばない範囲の3つが揃って初めて誤読されない。
RADIO_LICENSE_REQUIRED_TOKENS: dict[str, tuple[str, ...]] = {
    "無線ライブラリの名指し": ("BTstack",),
    "オープンソースではない旨": ("オープンソースではない",),
    "商用利用の制限": ("商用",),
    "制約が及ぶ範囲（テレオペ用ビルド）": ("テレオペ",),
    "制約が及ばない範囲（本番用ビルド）": ("本番",),
    "包括的な利用許諾との関係": ("LICENSE",),
}


def extract_markdown_section(markdown_text: str, heading: str) -> str:
    """指定した見出しから次の同レベル以上の見出しまでの本文を取り出す（見出し行を含む）。"""
    lines = markdown_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return ""
    level = len(heading.split(" ", 1)[0])
    end = len(lines)
    for offset, line in enumerate(lines[start + 1 :], start=start + 1):
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped.split(" ", 1)[0]) <= level:
            end = offset
            break
    return "\n".join(lines[start:end])


def find_radio_license_note_violations(markdown_text: str) -> list[str]:
    """無線ライブラリのライセンス制約の記載が、誤読されない要件を満たすか検査する。"""
    section = extract_markdown_section(markdown_text, RADIO_LICENSE_SECTION_HEADING)
    if not section.strip():
        return [f"{RADIO_LICENSE_SECTION_HEADING} の節が無い"]
    return [
        f"{label} の記載が無い"
        for label, tokens in RADIO_LICENSE_REQUIRED_TOKENS.items()
        if not any(token in section for token in tokens)
    ]


def test_readme_records_radio_library_license_constraint() -> None:
    """⚠️ 無線ライブラリのライセンス制約が、配布時に誤読されない形で記載されている（1.6）。

    記載先をリポジトリ直下の `README.md` としたのは、**成果物を再配布する側が
    最初に読む場所がそこだから**である（design.md Security Considerations
    「トップレベル LICENSE が BT 版ファームまで自由に再利用可能だと
    誤読させない記載を伴う」）。⚠️ 本リポジトリにはトップレベル `LICENSE` が
    まだ無く、利用許諾を新たに定めることは本タスクの範囲外であるため、
    「将来 LICENSE を置くときにテレオペ成果物を対象へ含めてはならない」ことを
    含めて README 側に記載する。
    """
    readme_text = README_PATH.read_text(encoding="utf-8")
    assert find_radio_license_note_violations(readme_text) == []


def test_detects_missing_license_section_in_crafted_input() -> None:
    """違反ケース: ライセンス制約の節が丸ごと無い架空の入力が検出される。"""
    assert find_radio_license_note_violations("# Title\n\n## 概要\n本文\n") != []


@pytest.mark.parametrize("dropped_label", sorted(RADIO_LICENSE_REQUIRED_TOKENS))
def test_detects_incomplete_license_note_in_crafted_input(dropped_label: str) -> None:
    """違反ケース: 記載すべき点を1つ落とした架空の入力が、その点だけ検出される。"""
    sentences = {label: tokens[0] for label, tokens in RADIO_LICENSE_REQUIRED_TOKENS.items()}
    del sentences[dropped_label]
    fake_readme = (
        f"{RADIO_LICENSE_SECTION_HEADING}\n\n"
        + "".join(f"- {phrase} についての記載\n" for phrase in sentences.values())
        + "\n## 次の節\n"
    )
    violations = find_radio_license_note_violations(fake_readme)
    assert violations == [f"{dropped_label} の記載が無い"]


def test_license_section_extraction_stops_at_the_next_section() -> None:
    """節の切り出しが次の見出しで止まる（隣の節の文言を誤って拾わない）。"""
    fake_readme = (
        f"{RADIO_LICENSE_SECTION_HEADING}\n\nBTstack についての記載\n\n"
        "## 次の節\n\nオープンソースではない\n"
    )
    section = extract_markdown_section(fake_readme, RADIO_LICENSE_SECTION_HEADING)
    assert "BTstack" in section
    assert "オープンソースではない" not in section


# ---------------------------------------------------------------------------
# 9. 端子割当コンポーネント board_pins の登録（teleop-bringup タスク 1.3、要件 2.1）
#
# 本節が固定するのは「board_pins がホストビルドと実機ビルドの双方から見える
# 形で登録されており、かつテレオペ側からのみ参照される」ことである。
#
# ⚠️ ホスト側から見えることが要件 2.7 の前提そのものである。`firmware/src/`
# は `test_build_src` 既定 `no` によりネイティブビルドに含まれないため、
# 端子割当をアプリ層へ置くと成立検査をホストで回せない。二重マニフェスト
# （CMakeLists.txt + library.json）の同居がその担保であり、`lib/test_support/`
# が CMakeLists.txt を**持たない**ことでホスト専用に留まっているのと対になる。
# ---------------------------------------------------------------------------

_CMAKE_IF_RE = re.compile(r"^\s*if\s*\(", re.IGNORECASE)
_CMAKE_ENDIF_RE = re.compile(r"^\s*endif\s*\(", re.IGNORECASE)
_TELEOP_GATE_RE = re.compile(
    r"^\s*if\s*\(\s*DEFINED\s+ENV\{DRIVETRAIN_BUILD_TELEOP\}\s*\)", re.IGNORECASE
)


def find_ungated_teleop_component_references(cmake_text: str, component: str) -> list[str]:
    """テレオペ限定であるべきコンポーネント名が、`if(DEFINED ENV{DRIVETRAIN_BUILD_TELEOP})`
    ブロックの外側で参照されている箇所を検出する。

    ⚠️ 本番側でも参照すると、ESP-IDF の要求展開により board_pins が本番の
    コンポーネントグラフへ入り、`firmware/CMakeLists.txt` の `COMPONENTS`
    allowlist（本番から無線を除外している機構、要件 1.4）の変更を強いられる。
    テレオペ限定に保つことが、その allowlist へ触れずに済ませる条件である。

    コメント（`#` 以降）は除外する。違反箇所を `行番号: 行` の形で返す。
    """
    violations: list[str] = []
    gate_stack: list[bool] = []
    for lineno, raw in enumerate(cmake_text.splitlines(), start=1):
        line = raw.split("#", 1)[0]
        if _CMAKE_ENDIF_RE.match(line):
            if gate_stack:
                gate_stack.pop()
            continue
        if _CMAKE_IF_RE.match(line):
            gate_stack.append(bool(_TELEOP_GATE_RE.match(line)))
        if component in line and not any(gate_stack):
            violations.append(f"{lineno}: {raw.strip()}")
    return violations


def _actual_board_pins_source_paths() -> set[str]:
    return {f"src/{p.relative_to(BOARD_PINS_SRC_DIR).as_posix()}" for p in BOARD_PINS_SRC_DIR.rglob("*.cpp")}


def test_board_pins_has_both_manifests() -> None:
    """board_pins が IDF コンポーネント manifest と PlatformIO manifest を同居させる。

    片方だけだと、実機ビルドかホストビルドのどちらかから見えなくなる。
    """
    assert BOARD_PINS_CMAKE_PATH.is_file(), "board_pins に CMakeLists.txt が無い（実機から見えない）"
    assert BOARD_PINS_LIBRARY_JSON_PATH.is_file(), "board_pins に library.json が無い（ホストから見えない）"


def test_test_support_still_has_no_idf_manifest() -> None:
    """対比: ホスト専用の test_support は CMakeLists.txt を持たないままである（要件16.3）。"""
    assert (TEST_SUPPORT_DIR / "library.json").is_file()
    assert not (TEST_SUPPORT_DIR / "CMakeLists.txt").exists()


def test_board_pins_cmakelists_srcs_matches_actual_source_files() -> None:
    """`lib/board_pins/CMakeLists.txt` の SRCS 集合が実体と一致する。

    drivetrain_control と同じ Risk R2（`SRCS` 更新漏れで `native` だけ通る）の回帰。
    """
    cmake_text = BOARD_PINS_CMAKE_PATH.read_text(encoding="utf-8")
    violations = find_source_set_mismatch(cmake_text, _actual_board_pins_source_paths())
    assert violations == [], f"board_pins の SRCS 集合が実体とずれている: {violations}"


def test_board_pins_has_at_least_one_translation_unit() -> None:
    """board_pins が翻訳単位を持つ（ヘッダのみだと実機ビルドで一度もコンパイルされない）。

    ヘッダのみのコンポーネントは IDF では INTERFACE ライブラリになり、利用側が
    現れるまでコンパイルされない。タスク 1.3 の観測可能な完了状態
    「ホスト向けビルドと実機向けビルドの双方でこの部品がコンパイルされ」を
    利用側の有無に依存させないために、実体を持つソースを1本以上要求する。
    """
    assert _actual_board_pins_source_paths() != set()


def test_board_pins_headers_exist_at_the_planned_paths() -> None:
    """design.md "File Structure Plan" が定めた 2 ヘッダが実在する。"""
    assert (BOARD_PINS_INCLUDE_DIR / "pin_map.hpp").is_file()
    assert (BOARD_PINS_INCLUDE_DIR / "pin_rules.hpp").is_file()


def test_board_pins_is_registered_individually_in_extra_component_dirs() -> None:
    """ルート `CMakeLists.txt` が `lib/board_pins` を個別に登録している。"""
    args = [a.replace("\\", "/").rstrip("/") for a in parse_extra_component_dirs_append_args(ROOT_CMAKE_TEXT)]
    assert any(a.endswith("lib/board_pins") for a in args), f"lib/board_pins の個別登録が無い: {args}"


def test_board_pins_requirement_is_gated_on_the_teleop_profile() -> None:
    """`firmware/src/CMakeLists.txt` が board_pins をテレオペ時のみ REQUIRES へ足す。"""
    violations = find_ungated_teleop_component_references(APP_CMAKE_TEXT, "board_pins")
    assert violations == [], f"board_pins がテレオペ限定の外側で参照されている: {violations}"


def test_gate_check_is_not_vacuous_against_the_real_app_cmakelists() -> None:
    """空虚な緑の防止: 同じ検査を、ゲートの外側にある `drivetrain_control` へ
    実ファイル上で適用すると違反として報告される。

    ⚠️ これが無いと、`board_pins` 側の緑が「検査が実ファイルに対して何も
    見ていないだけ」でも成立してしまう。`drivetrain_control` は3環境すべてで
    必要な核であり、意図的にゲートの外側にある。
    """
    assert find_ungated_teleop_component_references(APP_CMAKE_TEXT, "drivetrain_control") != []


def test_production_components_allowlist_does_not_carry_board_pins() -> None:
    """本番の `COMPONENTS` allowlist は board_pins を含まない（allowlist へ触れずに済む）。"""
    assert "board_pins" not in parse_components_allowlist(ROOT_CMAKE_TEXT)


def test_detects_ungated_component_requirement_in_crafted_input() -> None:
    """違反ケース: プロファイル判定の外側で board_pins を REQUIRES へ足す形が検出される。"""
    fake_cmake = (
        "set(DRIVETRAIN_APP_REQUIRES drivetrain_control board_pins)\n"
        "idf_component_register(SRCS main.cpp REQUIRES ${DRIVETRAIN_APP_REQUIRES})\n"
    )
    assert find_ungated_teleop_component_references(fake_cmake, "board_pins") != []


def test_detects_component_gated_on_the_production_profile_in_crafted_input() -> None:
    """違反ケース: 本番プロファイル側の判定へ入れてしまった形が検出される。"""
    fake_cmake = (
        "if(DEFINED ENV{DRIVETRAIN_BUILD_PRODUCTION})\n"
        "    list(APPEND DRIVETRAIN_APP_REQUIRES board_pins)\n"
        "endif()\n"
    )
    assert find_ungated_teleop_component_references(fake_cmake, "board_pins") != []


def test_does_not_flag_teleop_gated_component_in_crafted_input() -> None:
    """誤検知回避: テレオペ判定の内側での参照は違反にしない。"""
    fake_cmake = (
        "if(DEFINED ENV{DRIVETRAIN_BUILD_TELEOP})\n"
        "    list(APPEND DRIVETRAIN_APP_REQUIRES board_pins)\n"
        "endif()\n"
    )
    assert find_ungated_teleop_component_references(fake_cmake, "board_pins") == []


def test_gate_detection_survives_an_unrelated_enclosing_if_in_crafted_input() -> None:
    """入れ子の判定を取り違えない: 無関係な `if` の内側は「ゲート済み」にならない。"""
    fake_cmake = (
        "if(NOT DEFINED ENV{DRIVETRAIN_BUILD_TELEOP} AND NOT DEFINED ENV{DRIVETRAIN_BUILD_PRODUCTION})\n"
        "    message(FATAL_ERROR \"...\")\n"
        "endif()\n"
        "list(APPEND DRIVETRAIN_APP_REQUIRES board_pins)\n"
    )
    assert find_ungated_teleop_component_references(fake_cmake, "board_pins") != []


def test_gate_detection_ignores_comments_in_crafted_input() -> None:
    """コメント中のコンポーネント名を参照と誤認しない。"""
    fake_cmake = "# board_pins は teleop 限定である\nset(X 1)\n"
    assert find_ungated_teleop_component_references(fake_cmake, "board_pins") == []


# ---------------------------------------------------------------------------
# 9b. パッド入力コンポーネント teleop_input の登録（teleop-bringup タスク 4.1、
#     要件 8.6, 8.7, 8.8, 8.9）
#
# セクション9（board_pins）と同じ観点を、正規化済みパッド状態と変換
# パラメータの型を持つ teleop_input へ適用する。検査ロジック自体
# （find_source_set_mismatch / find_ungated_teleop_component_references /
# parse_components_allowlist 等）は使い回し、新規に定義し直さない
# （それらが component 名やパスを引数に取る汎用関数であることの裏付けは
# セクション9の crafted-input テストが既に示している）。
# ---------------------------------------------------------------------------


def _actual_teleop_input_source_paths() -> set[str]:
    return {
        f"src/{p.relative_to(TELEOP_INPUT_SRC_DIR).as_posix()}"
        for p in TELEOP_INPUT_SRC_DIR.rglob("*.cpp")
    }


def test_teleop_input_has_both_manifests() -> None:
    """teleop_input が IDF コンポーネント manifest と PlatformIO manifest を同居させる。

    片方だけだと、実機ビルドかホストビルドのどちらかから見えなくなる。
    """
    assert (
        TELEOP_INPUT_CMAKE_PATH.is_file()
    ), "teleop_input に CMakeLists.txt が無い（実機から見えない）"
    assert (
        TELEOP_INPUT_LIBRARY_JSON_PATH.is_file()
    ), "teleop_input に library.json が無い（ホストから見えない）"


def test_teleop_input_cmakelists_srcs_matches_actual_source_files() -> None:
    """`lib/teleop_input/CMakeLists.txt` の SRCS 集合が実体と一致する。

    drivetrain_control / board_pins と同じ Risk R2（`SRCS` 更新漏れで
    `native` だけ通る）の回帰。
    """
    cmake_text = TELEOP_INPUT_CMAKE_PATH.read_text(encoding="utf-8")
    violations = find_source_set_mismatch(cmake_text, _actual_teleop_input_source_paths())
    assert violations == [], f"teleop_input の SRCS 集合が実体とずれている: {violations}"


def test_teleop_input_has_at_least_one_translation_unit() -> None:
    """teleop_input が翻訳単位を持つ（ヘッダのみだと実機ビルドで一度もコンパイルされない）。

    ヘッダのみのコンポーネントは IDF では INTERFACE ライブラリになり、利用側が
    現れるまでコンパイルされない。タスク 4.1 の観測可能な完了状態
    「ホスト向けビルドと実機向けビルドの双方でこの部品がコンパイルされ」を
    利用側の有無に依存させないために、実体を持つソースを1本以上要求する。
    """
    assert _actual_teleop_input_source_paths() != set()


def test_teleop_input_headers_exist_at_the_planned_paths() -> None:
    """design.md "File Structure Plan" が定めた 2 ヘッダが実在する。"""
    assert (TELEOP_INPUT_INCLUDE_DIR / "pad_state.hpp").is_file()
    assert (TELEOP_INPUT_INCLUDE_DIR / "mapping.hpp").is_file()


def test_teleop_input_is_registered_individually_in_extra_component_dirs() -> None:
    """ルート `CMakeLists.txt` が `lib/teleop_input` を個別に登録している。"""
    args = [
        a.replace("\\", "/").rstrip("/") for a in parse_extra_component_dirs_append_args(ROOT_CMAKE_TEXT)
    ]
    assert any(
        a.endswith("lib/teleop_input") for a in args
    ), f"lib/teleop_input の個別登録が無い: {args}"


def test_teleop_input_requirement_is_gated_on_the_teleop_profile() -> None:
    """`firmware/src/CMakeLists.txt` が teleop_input をテレオペ時のみ REQUIRES へ足す。"""
    violations = find_ungated_teleop_component_references(APP_CMAKE_TEXT, "teleop_input")
    assert violations == [], f"teleop_input がテレオペ限定の外側で参照されている: {violations}"


def test_production_components_allowlist_does_not_carry_teleop_input() -> None:
    """本番の `COMPONENTS` allowlist は teleop_input を含まない（allowlist へ触れずに済む）。"""
    assert "teleop_input" not in parse_components_allowlist(ROOT_CMAKE_TEXT)


# ---------------------------------------------------------------------------
# 10. board_pins のペリフェラル非参照を検査する（要件 17.3、teleop-bringup タスク 1.4）
#
# セクション8の「純ロジックファイル」走査（`_all_pure_logic_files`）は
# `lib/drivetrain_control` だけを対象にしており、`lib/board_pins` を含まない。
# タスク 1.3 の Implementation Notes（tasks.md）が「board_pins にも同種の
# 検査が要る」ことを 1.4 宛に残しており、本節がそれを満たす。
#
# ⚠️ 検査ロジックそのもの（`find_forbidden_peripheral_includes` /
# `classify_forbidden_include`、セクション9直前の禁止 include 検査）は
# セクション8がすでに固定・検証済みのものをそのまま再利用する（重複実装を
# 避ける、`tech.md` 開発標準3）。ここで新設するのは「どのファイル集合へ
# 適用するか」（board_pins のヘッダ＋実装）だけである。
# ---------------------------------------------------------------------------


def _board_pins_pure_logic_files() -> list[Path]:
    """`lib/board_pins` 配下の純ロジック実ファイル（ヘッダ＋実装）を列挙する。"""
    return sorted(BOARD_PINS_INCLUDE_DIR.rglob("*.hpp")) + sorted(BOARD_PINS_SRC_DIR.rglob("*.cpp"))


def test_board_pins_pure_logic_files_is_non_empty() -> None:
    """走査対象そのものが空振りでないことを確認する（前提の健全性）。"""
    assert _board_pins_pure_logic_files() != []


def test_board_pins_file_list_contains_the_known_files() -> None:
    """空虚な緑の防止: 走査対象が実際に `pin_map.hpp` / `pin_rules.hpp` /
    `board_pins.cpp` を含んでいることを確かめる。含んでいなければ、直後の
    検査が「何も見ていないだけ」で緑になりうる。
    """
    names = {p.name for p in _board_pins_pure_logic_files()}
    assert {"pin_map.hpp", "pin_rules.hpp", "board_pins.cpp"} <= names


def _violations_across_board_pins_files(
    check: Callable[[str], list[str]],
) -> dict[str, list[str]]:
    """`check` を `lib/board_pins` の全純ロジックファイルへ適用し、違反があった
    ファイルのみを集める（`_violations_across_pure_logic_files` の board_pins 版）。
    """
    result: dict[str, list[str]] = {}
    for path in _board_pins_pure_logic_files():
        violations = check(path.read_text(encoding="utf-8"))
        if violations:
            result[str(path.relative_to(REPO_ROOT))] = violations
    return result


def test_no_board_pins_file_includes_forbidden_peripheral_headers() -> None:
    """要件 17.3: `board_pins` がペリフェラル API を一切参照しない。

    `pin_map.hpp` / `pin_rules.hpp` それぞれの冒頭コメントが宣言する制約
    （`driver/*.h` / `esp_adc/*.h` を include しない）を、コメントではなく
    実体として固定する。ホスト・実機の双方でコンパイルできることの前提
    （要件 2.7）がこの制約に依拠している。
    """
    violations = _violations_across_board_pins_files(find_forbidden_peripheral_includes)
    assert violations == {}, f"board_pins へペリフェラル API 参照が混入している: {violations}"


def test_detects_forbidden_peripheral_include_in_board_pins_style_crafted_input() -> None:
    """違反ケース: `board_pins` 風のヘッダへ `driver/*.h` を紛れ込ませた入力が検出される。

    ⚠️ 実ファイルへの適用が緑であることは、検査ロジックが機能しているから
    なのか、単に何も検出していないだけなのかを区別できない。crafted 入力で
    実際に赤くなることを示す。
    """
    fake_header = (
        "#pragma once\n"
        "#include <cstdint>\n"
        "#include <driver/gpio.h>\n"  # 混入させた禁止 include
        "namespace board_pins {\n"
        "}  // namespace board_pins\n"
    )
    assert find_forbidden_peripheral_includes(fake_header) != []


def test_does_not_flag_board_pins_legitimate_includes_in_crafted_input() -> None:
    """誤検知回避: `<cstdint>` と自身のペア相手ヘッダは違反にしない。"""
    fake_header = (
        "#pragma once\n"
        "#include <cstdint>\n"
        '#include "board_pins/pin_map.hpp"\n'
        "namespace board_pins {\n"
        "}  // namespace board_pins\n"
    )
    assert find_forbidden_peripheral_includes(fake_header) == []


# =============================================================================
# 11. アダプタ層に判断が持ち込まれていないことを静的に検査する
#     （teleop-bringup タスク 3.4、要件 17.3, 17.5）
#
# タスク 3.1〜3.3 が実装した3アダプタ（`firmware/src/teleop/*`）は、いずれも
# 「判断・計算を持たない」ことを自身のヘッダコメントで明言している
# （`EncoderPcntAdapter` は折り返しの桁上げを `WrapAccumulator` へ、
# `BatteryAdcAdapter` は生値→ミリボルト換算を `VoltageScaler` へ委譲し、
# `MotorLedcAdapter` は独自のクランプ・補正を一切加えない）。本節はこれを
# コメントの言明としてではなく、静的検査として固定する。
#
# 要件 17.5（公開されていない内部構造への依存を検査で検出する）について:
# `drivetrain_control/drivetrain_control.hpp` 自身の冒頭コメントが
# 「下流はこのヘッダだけを include する」と明言しており、これが本節の
# 適用対象である `firmware/src/teleop/**` そのものを指す（teleop-bringup は
# `drivetrain_control` の唯一の下流 Spec の一つ）。ところが3アダプタの
# `.hpp` は実装時点で `ports.hpp` / `types.hpp` / `wrap_accumulator.hpp` /
# `voltage_scaler.hpp` / `config.hpp` を個別に直接 include しており、この
# 契約に反していた。⚠️ **本タスクはこれを検査の新設と同時に是正した**
# （include 行の置き換えのみ。ロジックは一切変更していない）:
#   - `encoder_pcnt.hpp`: `ports.hpp` / `types.hpp` / `wrap_accumulator.hpp`
#     → `drivetrain_control/drivetrain_control.hpp` の1行へ統合。
#   - `motor_ledc.hpp`: `ports.hpp` / `types.hpp` → 同上。
#   - `battery_adc.hpp`: `config.hpp` / `ports.hpp` / `types.hpp` /
#     `voltage_scaler.hpp` → 同上。
# 置き換え後に必要なシンボル（`EncoderPort` / `MotorOutputPort` /
# `BatteryVoltagePort` / `EncoderCounts` / `WheelOutputs` / `VoltageSample` /
# `WrapAccumulator` / `VoltageScaler` / `VoltageScalerParams`）はいずれも
# `drivetrain_control.hpp` が再エクスポートする一覧に含まれる
# （同ヘッダ冒頭コメント参照）。`.cpp` 側は元々それぞれの `.hpp` のみを
# include しており、変更不要だった。
#
# 要件 17.3（判断・計算ロジックの回帰的検査）について: 「アダプタが独自の
# 判断ロジックを持ち込んでいない」ことを一般に機械検査するのは困難だが、
# タスク 3.1〜3.3 の設計判断が「委譲すべき計算の入口」を明確に1本ずつに
# 絞っている（`WrapAccumulator::update()` / `VoltageScaler::toMilliVolts()`）
# ため、「核が提供する換算結果を出力フィールドへ渡す代入の右辺が、必ず
# その委譲呼び出し1つに一致する」という形で固定できる。`MotorLedcAdapter`
# は委譲すべき部品を持たない（クランプ自体を禁じる設計）ため、
# クランプ的パターン（`std::min/max/clamp` 呼び出し、または
# `if (x 比較) x = ...;` という自己代入）の不在を直接検査する。
# ---------------------------------------------------------------------------


def _teleop_files() -> list[Path]:
    """`firmware/src/teleop/` 配下の実ファイル（ヘッダ＋実装）を列挙する。"""
    return sorted(TELEOP_SRC_DIR.glob("*.hpp")) + sorted(TELEOP_SRC_DIR.glob("*.cpp"))


def test_teleop_files_is_non_empty() -> None:
    """走査対象そのものが空振りでないことを確認する（前提の健全性）。"""
    assert _teleop_files() != []


def test_teleop_file_list_contains_the_known_adapter_files() -> None:
    """空虚な緑の防止: 走査対象が実際に3アダプタの `.hpp`/`.cpp` を含んでいる。"""
    names = {p.name for p in _teleop_files()}
    assert {
        "encoder_pcnt.hpp",
        "encoder_pcnt.cpp",
        "motor_ledc.hpp",
        "motor_ledc.cpp",
        "battery_adc.hpp",
        "battery_adc.cpp",
    } <= names


def _violations_across_teleop_files(
    check: Callable[[str], list[str]],
) -> dict[str, list[str]]:
    """`check` を `firmware/src/teleop` の全ファイルへ適用し、違反があった
    ファイルのみを集める（`_violations_across_pure_logic_files` のアダプタ版）。
    """
    result: dict[str, list[str]] = {}
    for path in _teleop_files():
        violations = check(path.read_text(encoding="utf-8"))
        if violations:
            result[str(path.relative_to(REPO_ROOT))] = violations
    return result


# ---------------------------------------------------------------------------
# 11a. 公開入口だけを使うこと（要件 17.5）
# ---------------------------------------------------------------------------


def find_disallowed_drivetrain_control_includes(text: str) -> list[str]:
    """`drivetrain_control/` 配下のヘッダのうち、公開入口
    （`drivetrain_control/drivetrain_control.hpp`）以外への直接 include を検出する。

    該当すれば `#include` のヘッダ文字列（例:
    `drivetrain_control/wrap_accumulator.hpp`）の一覧を返す。空列であれば
    違反なし。`drivetrain_control/drivetrain_control.hpp` 自身の include は
    許可する（それが公開入口そのものである）。
    """
    stripped = strip_cpp_comments(text)
    violations = []
    for match in _INCLUDE_TARGET_PATTERN.finditer(stripped):
        header = match.group(1)
        if header.startswith("drivetrain_control/") and header != "drivetrain_control/drivetrain_control.hpp":
            violations.append(header)
    return violations


def test_no_teleop_file_includes_drivetrain_control_internal_headers_directly() -> None:
    """要件 17.5: `firmware/src/teleop/**` は `drivetrain_control` の公開入口
    （`drivetrain_control.hpp`）だけを include し、内部の個別ヘッダを直接
    include しない。
    """
    violations = _violations_across_teleop_files(find_disallowed_drivetrain_control_includes)
    assert violations == {}, f"drivetrain_control の内部ヘッダへの直接 include が混入している: {violations}"


@pytest.mark.parametrize(
    "internal_header",
    [
        "drivetrain_control/ports.hpp",
        "drivetrain_control/types.hpp",
        "drivetrain_control/wrap_accumulator.hpp",
        "drivetrain_control/voltage_scaler.hpp",
        "drivetrain_control/config.hpp",
    ],
)
def test_detects_internal_drivetrain_control_header_include_in_crafted_input(
    internal_header: str,
) -> None:
    """違反ケース: 公開入口ではない内部ヘッダへの直接 include が検出される。"""
    fake_header = f'#pragma once\n#include "{internal_header}"\n'
    violations = find_disallowed_drivetrain_control_includes(fake_header)
    assert violations != []
    assert internal_header in violations


def test_does_not_flag_public_entry_header_in_crafted_input() -> None:
    """誤検知回避: 公開入口 `drivetrain_control.hpp` 自身の include は違反にしない。"""
    fake_header = (
        "#pragma once\n"
        '#include "board_pins/pin_map.hpp"\n'
        '#include "drivetrain_control/drivetrain_control.hpp"\n'
    )
    assert find_disallowed_drivetrain_control_includes(fake_header) == []


# ---------------------------------------------------------------------------
# 11b. 関数本体の抽出（波括弧の深さを数える汎用ヘルパ）
#
# 11c/11d/11e が「特定のメンバ関数の本体だけ」を検査対象にするために使う。
# 正規表現の単純な非貪欲マッチ（`\{.*?\}`）は関数内のネストした波括弧
# （for/if 等）で誤って早期に閉じてしまうため、深さを数える。
# ---------------------------------------------------------------------------


def extract_function_body(text: str, signature_pattern: str) -> str | None:
    """`signature_pattern` にマッチする関数シグネチャ直後の `{` から、対応する
    閉じ `}` までの本体テキスト（波括弧自身は含まない）を取り出す。

    `signature_pattern` は末尾が `\\{` で終わる正規表現であること（マッチ
    末尾の直前の文字が開き波括弧である前提）。シグネチャが見つからない、
    または対応する閉じ波括弧が無い場合は `None` を返す（「検査対象の関数が
    見当たらない」ことを、違反0件の場合と区別できるようにする）。
    """
    match = re.search(signature_pattern, text)
    if match is None:
        return None
    start = match.end() - 1
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
    return None


def test_extract_function_body_handles_nested_braces() -> None:
    fake_cpp = (
        "int Foo::bar() {\n"
        "  for (int i = 0; i < 1; ++i) {\n"
        "    if (i == 0) {\n"
        "      doStuff();\n"
        "    }\n"
        "  }\n"
        "  return 1;\n"
        "}\n"
    )
    body = extract_function_body(fake_cpp, r"Foo::bar\s*\(\s*\)\s*\{")
    assert body is not None
    assert "doStuff();" in body
    assert body.count("{") == body.count("}")


def test_extract_function_body_returns_none_when_signature_absent() -> None:
    assert extract_function_body("int Foo::other() { return 0; }\n", r"Foo::bar\s*\(\s*\)\s*\{") is None


# ---------------------------------------------------------------------------
# 汎用: 「出力フィールドへの代入の右辺が、指定した委譲呼び出し1つに一致するか」
# ---------------------------------------------------------------------------

_ASSIGNMENT_STATEMENT_PATTERN = re.compile(
    r"([A-Za-z_][\w.]*(?:\[[^\]]*\])?)\s*(?<![=!<>+\-*/%&|^])=(?!=)\s*([^;{}]+);"
)
"""`lhs = rhs;` の形の単純代入文を抽出する。`==` / `!=` / `<=` / `>=` /
`+=` 等の複合代入・比較演算子は、代入演算子 `=` の直前の文字を除外する
否定後読みで取り除く。"""


def find_assignments_not_delegated(
    body: str, lhs_keyword: str, allowed_rhs_pattern: re.Pattern[str]
) -> list[str]:
    """`body` 内の代入文のうち、左辺に `lhs_keyword`（小文字比較）を含むものを
    対象に、右辺が `allowed_rhs_pattern` に完全一致（`fullmatch`）しないものを
    違反として返す。`lhs = rhs` の文字列表現の一覧。空列であれば違反なし。
    """
    violations = []
    for match in _ASSIGNMENT_STATEMENT_PATTERN.finditer(body):
        lhs, rhs = match.group(1), match.group(2).strip()
        if lhs_keyword not in lhs.lower():
            continue
        if not allowed_rhs_pattern.fullmatch(rhs):
            violations.append(f"{lhs} = {rhs}")
    return violations


# ---------------------------------------------------------------------------
# 11c. EncoderPcntAdapter::read(): 折り返しの桁上げは WrapAccumulator へ
#      委譲されていること（要件 17.3, 4.3）
# ---------------------------------------------------------------------------

_ENCODER_READ_SIGNATURE_PATTERN = r"EncoderPcntAdapter::read\s*\(\s*\)\s*\{"
_ENCODER_UPDATE_DELEGATION_PATTERN = re.compile(r"[A-Za-z_]\w*\[[^\]]*\]\.update\([^;]*\)")


def find_encoder_wrap_carry_not_delegated(text: str) -> list[str]:
    """`EncoderPcntAdapter::read()` 内で、累積カウント（`count` を含む変数）
    への代入の右辺が `accumulators_[...].update(...)` の呼び出し1つに一致
    しない箇所を検出する（要件 17.3, 4.3）。

    `read()` が見つからないファイル（他の2アダプタ等）には適用対象が無い
    ため空列を返す。
    """
    stripped = strip_cpp_comments(text)
    body = extract_function_body(stripped, _ENCODER_READ_SIGNATURE_PATTERN)
    if body is None:
        return []
    return find_assignments_not_delegated(body, "count", _ENCODER_UPDATE_DELEGATION_PATTERN)


def test_encoder_read_delegates_wrap_carry_to_wrap_accumulator() -> None:
    text = (TELEOP_SRC_DIR / "encoder_pcnt.cpp").read_text(encoding="utf-8")
    assert find_encoder_wrap_carry_not_delegated(text) == []


def test_extract_function_body_locates_the_real_encoder_read_method() -> None:
    """空虚な緑の防止: 実ファイルに対して `read()` の本体が実際に見つかり、
    委譲呼び出しを含んでいることを確かめる（シグネチャの変更で常に `None`
    を返しているだけ、ではないことの担保）。
    """
    stripped = strip_cpp_comments((TELEOP_SRC_DIR / "encoder_pcnt.cpp").read_text(encoding="utf-8"))
    body = extract_function_body(stripped, _ENCODER_READ_SIGNATURE_PATTERN)
    assert body is not None
    assert "accumulators_" in body
    assert ".update(" in body


def test_detects_manual_wrap_carry_arithmetic_in_crafted_encoder_input() -> None:
    """違反ケース: 折り返しの桁上げを自前の加減算で行う架空の `read()` が検出される。"""
    fake_cpp = (
        "drivetrain_control::EncoderCounts EncoderPcntAdapter::read() {\n"
        "  drivetrain_control::EncoderCounts counts{};\n"
        "  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {\n"
        "    int raw = 0;\n"
        "    ESP_ERROR_CHECK(pcnt_unit_get_count(units_[wheel], &raw));\n"
        "    std::int64_t delta = raw - last_raw_[wheel];\n"
        "    if (delta < -32768) { delta += 65536; }\n"
        "    if (delta > 32768) { delta -= 65536; }\n"
        "    accum_[wheel] += delta;\n"
        "    counts.count[wheel] = accum_[wheel];\n"
        "  }\n"
        "  return counts;\n"
        "}\n"
    )
    violations = find_encoder_wrap_carry_not_delegated(fake_cpp)
    assert violations != []
    assert any("accum_" in v for v in violations)


def test_detects_manual_modulus_wrap_arithmetic_in_crafted_encoder_input() -> None:
    """違反ケース: `.update()` を呼ばず法演算を直接右辺へ書いた架空の `read()` が検出される。"""
    fake_cpp = (
        "drivetrain_control::EncoderCounts EncoderPcntAdapter::read() {\n"
        "  drivetrain_control::EncoderCounts counts{};\n"
        "  int raw = 0;\n"
        "  ESP_ERROR_CHECK(pcnt_unit_get_count(units_[0], &raw));\n"
        "  counts.count[0] = static_cast<std::int64_t>(raw) % 65536;\n"
        "  return counts;\n"
        "}\n"
    )
    violations = find_encoder_wrap_carry_not_delegated(fake_cpp)
    assert violations != []
    assert any("% 65536" in v for v in violations)


def test_does_not_flag_unrelated_file_for_encoder_check_in_crafted_input() -> None:
    """誤検知回避: `read()` を持たない架空の入力には適用対象が無い（空列）。"""
    assert find_encoder_wrap_carry_not_delegated("void SomethingElse() {}\n") == []


# ---------------------------------------------------------------------------
# 11d. BatteryAdcAdapter::read(): 生値→ミリボルト換算は VoltageScaler へ
#      委譲されていること（要件 17.3, 6.3）
# ---------------------------------------------------------------------------

_BATTERY_READ_SIGNATURE_PATTERN = r"BatteryAdcAdapter::read\s*\(\s*\)\s*\{"
_VOLTAGE_SCALER_DELEGATION_PATTERN = re.compile(r"[A-Za-z_]\w*\.toMilliVolts\([^;]*\)")


def find_battery_voltage_conversion_not_delegated(text: str) -> list[str]:
    """`BatteryAdcAdapter::read()` 内で、`milli_volts` を含む変数への代入の
    右辺が `scaler_.toMilliVolts(...)` の呼び出し1つに一致しない箇所を検出
    する（要件 17.3, 6.3）。`read()` が見つからないファイルには適用対象が
    無いため空列を返す。
    """
    stripped = strip_cpp_comments(text)
    body = extract_function_body(stripped, _BATTERY_READ_SIGNATURE_PATTERN)
    if body is None:
        return []
    return find_assignments_not_delegated(body, "milli_volts", _VOLTAGE_SCALER_DELEGATION_PATTERN)


def test_battery_read_delegates_voltage_conversion_to_voltage_scaler() -> None:
    text = (TELEOP_SRC_DIR / "battery_adc.cpp").read_text(encoding="utf-8")
    assert find_battery_voltage_conversion_not_delegated(text) == []


def test_extract_function_body_locates_the_real_battery_read_method() -> None:
    """空虚な緑の防止: 実ファイルに対して `read()` の本体が実際に見つかり、
    委譲呼び出しを含んでいることを確かめる。
    """
    stripped = strip_cpp_comments((TELEOP_SRC_DIR / "battery_adc.cpp").read_text(encoding="utf-8"))
    body = extract_function_body(stripped, _BATTERY_READ_SIGNATURE_PATTERN)
    assert body is not None
    assert "scaler_" in body
    assert ".toMilliVolts(" in body


def test_detects_manual_voltage_scaling_arithmetic_in_crafted_battery_input() -> None:
    """違反ケース: 分圧比の線形換算を自前の乗除算で行う架空の `read()` が検出される。"""
    fake_cpp = (
        "drivetrain_control::VoltageSample BatteryAdcAdapter::read() {\n"
        "  drivetrain_control::VoltageSample sample;\n"
        "  int raw = 0;\n"
        "  if (adc_oneshot_read(unit_, channel_, &raw) != ESP_OK) { return sample; }\n"
        "  sample.valid = true;\n"
        "  sample.milli_volts = static_cast<std::int32_t>(raw) * 3300 / 4095;\n"
        "  return sample;\n"
        "}\n"
    )
    violations = find_battery_voltage_conversion_not_delegated(fake_cpp)
    assert violations != []
    assert any("3300" in v for v in violations)


def test_does_not_flag_unrelated_file_for_battery_check_in_crafted_input() -> None:
    """誤検知回避: `read()` を持たない架空の入力には適用対象が無い（空列）。"""
    assert find_battery_voltage_conversion_not_delegated("void SomethingElse() {}\n") == []


# ---------------------------------------------------------------------------
# 11e. MotorLedcAdapter::write(): クランプ・制限ロジックを持たないこと
#      （要件 17.3, 5.2）
# ---------------------------------------------------------------------------

_MOTOR_WRITE_SIGNATURE_PATTERN = r"MotorLedcAdapter::write\s*\([^)]*\)\s*\{"
_CLAMP_HELPER_CALL_TOKENS: tuple[str, ...] = ("std::min(", "std::max(", "std::clamp(")
_IF_SELF_REASSIGNMENT_PATTERN = re.compile(
    r"if\s*\(([^()]*)\)\s*\{?\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*=\s*[^;]+;"
)


def find_motor_duty_clamping(text: str) -> list[str]:
    """`MotorLedcAdapter::write()` 内に、大きさをクランプ/制限する形跡が無いかを
    検出する（要件 17.3, 5.2 — 独自の制限・補正を加えない）。

    2種類を検出する: (a) `std::min`/`std::max`/`std::clamp` の呼び出し、
    (b) `if (cond) var = ...;` で `var` が `cond` にも登場する自己代入
    （`if (x > max) x = max;` 型のクランプ）。`write()` が見つからない
    ファイルには適用対象が無いため空列を返す。
    """
    stripped = strip_cpp_comments(text)
    body = extract_function_body(stripped, _MOTOR_WRITE_SIGNATURE_PATTERN)
    if body is None:
        return []
    violations = [f"{token} が write() 内で使われている" for token in _CLAMP_HELPER_CALL_TOKENS if token in body]
    for match in _IF_SELF_REASSIGNMENT_PATTERN.finditer(body):
        condition, var = match.group(1), match.group(2)
        bare_var = var.rsplit(".", 1)[-1]
        if re.search(r"\b" + re.escape(bare_var) + r"\b", condition):
            violations.append(f"if ({condition.strip()}) {var} = ...;")
    return violations


def test_motor_write_has_no_clamping_logic() -> None:
    text = (TELEOP_SRC_DIR / "motor_ledc.cpp").read_text(encoding="utf-8")
    assert find_motor_duty_clamping(text) == []


def test_extract_function_body_locates_the_real_motor_write_method() -> None:
    """空虚な緑の防止: 実ファイルに対して `write()` の本体が実際に見つかる。"""
    stripped = strip_cpp_comments((TELEOP_SRC_DIR / "motor_ledc.cpp").read_text(encoding="utf-8"))
    body = extract_function_body(stripped, _MOTOR_WRITE_SIGNATURE_PATTERN)
    assert body is not None
    assert "ledc_set_duty(" in body


def test_detects_if_based_clamp_in_crafted_motor_input() -> None:
    """違反ケース: `if (x > max) x = max;` 型のクランプが検出される。"""
    fake_cpp = (
        "void MotorLedcAdapter::write(const drivetrain_control::WheelOutputs& outputs) {\n"
        "  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {\n"
        "    float magnitude = outputs.duty[wheel];\n"
        "    if (magnitude > 1.0f) {\n"
        "      magnitude = 1.0f;\n"
        "    }\n"
        "    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, pwm_channel_[wheel], 0));\n"
        "  }\n"
        "}\n"
    )
    violations = find_motor_duty_clamping(fake_cpp)
    assert violations != []
    assert any("magnitude" in v for v in violations)


def test_detects_std_clamp_helper_call_in_crafted_motor_input() -> None:
    """違反ケース: `std::clamp` ヘルパの直接呼び出しが検出される。"""
    fake_cpp = (
        "void MotorLedcAdapter::write(const drivetrain_control::WheelOutputs& outputs) {\n"
        "  float magnitude = std::clamp(outputs.duty[0], 0.0f, 1.0f);\n"
        "  ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, pwm_channel_[0], 0));\n"
        "}\n"
    )
    violations = find_motor_duty_clamping(fake_cpp)
    assert violations != []
    assert any("std::clamp(" in v for v in violations)


def test_does_not_flag_direction_selection_if_in_crafted_motor_input() -> None:
    """誤検知回避: 向き反転（要件 5.4）のような、条件式に登場しない変数への
    代入は違反にしない。"""
    fake_cpp = (
        "void MotorLedcAdapter::write(const drivetrain_control::WheelOutputs& outputs) {\n"
        "  const bool forward = outputs.duty[0] >= 0.0f;\n"
        "  int dir_level = 0;\n"
        "  if (forward != configs_[0].invert_direction) {\n"
        "    dir_level = 1;\n"
        "  }\n"
        "  ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, pwm_channel_[0], 0));\n"
        "}\n"
    )
    assert find_motor_duty_clamping(fake_cpp) == []


def test_does_not_flag_unrelated_file_for_motor_check_in_crafted_input() -> None:
    """誤検知回避: `write()` を持たない架空の入力には適用対象が無い（空列）。"""
    assert find_motor_duty_clamping("void SomethingElse() {}\n") == []


# =============================================================================
# 12. Bluepad32 / BTstack の取り込みがテレオペ限定であること
#     （teleop-bringup タスク 5.1、要件 7.1）
#
# セクション9/9bが board_pins / teleop_input（本リポジトリ自身の `lib/`
# コンポーネント）に適用した「テレオペ限定で REQUIRES へ足し、本番の
# COMPONENTS allowlist へは触れない」という形を、本節は Bluepad32
# （firmware/.deps/bluepad32/src/components からフェッチする第三者
# コンポーネント）へ適用する。検査ロジック自体
# （find_ungated_teleop_component_references / parse_components_allowlist）
# は使い回し、新規に定義し直さない。
#
# ⚠️ board_pins / teleop_input と異なり、EXTRA_COMPONENT_DIRS の登録自体も
# テレオペ限定でゲートしてある（firmware/CMakeLists.txt 参照: フェッチが
# 走っていないチェックアウトではディレクトリそのものが存在しないため）。
# `find_ungated_teleop_component_references` はコンポーネント名に限らず
# 任意の部分文字列を対象にできる汎用関数であり、この EXTRA_COMPONENT_DIRS
# 行のゲートも同じ関数で検査できる。
# =============================================================================


def test_bluepad32_extra_component_dir_is_gated_on_the_teleop_profile() -> None:
    """`firmware/CMakeLists.txt` の `.deps/bluepad32` への EXTRA_COMPONENT_DIRS
    追加が `DEFINED ENV{DRIVETRAIN_BUILD_TELEOP}` の内側にある。

    board_pins/teleop_input の EXTRA_COMPONENT_DIRS 登録は無条件（常に存在
    する自前の lib/ ディレクトリのため）だが、.deps/bluepad32 はフェッチが
    走るまで存在しない第三者ディレクトリであり、ゲートが要る。
    """
    violations = find_ungated_teleop_component_references(ROOT_CMAKE_TEXT, ".deps/bluepad32")
    assert violations == [], f".deps/bluepad32 への参照がテレオペ限定の外側にある: {violations}"


def test_bluepad32_extra_component_dir_is_registered() -> None:
    """`.deps/bluepad32/src/components` への EXTRA_COMPONENT_DIRS 追加が実在する。"""
    args = [a.replace("\\", "/").rstrip("/") for a in parse_extra_component_dirs_append_args(ROOT_CMAKE_TEXT)]
    assert any(
        a.endswith(".deps/bluepad32/src/components") for a in args
    ), f".deps/bluepad32/src/components の登録が無い: {args}"


def test_bluepad32_requirement_is_gated_on_the_teleop_profile() -> None:
    """`firmware/src/CMakeLists.txt` が bluepad32 をテレオペ時のみ REQUIRES へ足す。"""
    violations = find_ungated_teleop_component_references(APP_CMAKE_TEXT, "bluepad32")
    assert violations == [], f"bluepad32 がテレオペ限定の外側で参照されている: {violations}"


def test_production_components_allowlist_does_not_carry_bluepad32_or_btstack() -> None:
    """本番の `COMPONENTS` allowlist は bluepad32 / btstack を含まない
    （allowlist へ触れずに済む -- 本番の無線非依存を壊さない）。
    """
    allowlist = parse_components_allowlist(ROOT_CMAKE_TEXT)
    assert "bluepad32" not in allowlist
    assert "btstack" not in allowlist


def test_detects_ungated_bluepad32_component_dir_reference_in_crafted_input() -> None:
    """違反ケース: `.deps/bluepad32` への EXTRA_COMPONENT_DIRS 追加をゲートの
    外側へ書いた架空の入力が検出される。
    """
    fake_cmake = (
        'list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/.deps/bluepad32/src/components")\n'
    )
    assert find_ungated_teleop_component_references(fake_cmake, ".deps/bluepad32") != []


def test_fetch_bluepad32_script_pins_an_immutable_tag() -> None:
    """`scripts/fetch_bluepad32.py` が可変参照（ブランチ/HEAD）ではなく
    git タグへ固定している（要件 1.7 と同じ「不変参照への固定」の原則。
    research.md Risks「BTstack の設置がスクリプト依存」への対処）。
    """
    script_text = (FIRMWARE_DIR / "scripts" / "fetch_bluepad32.py").read_text(encoding="utf-8")
    match = re.search(r'BLUEPAD32_REF\s*=\s*"([^"]+)"', script_text)
    assert match is not None, "BLUEPAD32_REF の定義が見つからない"
    ref = match.group(1)
    # ⚠️ Bluepad32 のタグ命名規則は "release_vX.Y.Z"（3.10.3 まで）から
    # 素の "X.Y.Z"（4.x 系以降）へ変わっている（実測: 両方の命名規則が
    # 混在する実在のタグを確認済み）。したがって固定の接頭辞は要求せず、
    # 「ブランチ／HEAD の類ではない」ことと「バージョンらしき記法である」
    # ことだけを検査する。
    MUTABLE_REF_NAMES = {"main", "master", "HEAD", "develop", "latest"}
    assert ref not in MUTABLE_REF_NAMES, f"BLUEPAD32_REF が可変参照になっている: {ref!r}"
    assert re.match(r"^[A-Za-z_]*\d+(\.\d+)+$", ref), (
        f"BLUEPAD32_REF がバージョンタグの記法に見えない: {ref!r}"
    )
    assert "--branch" in script_text and "--depth" in script_text and "1" in script_text
