"""結線表 (`wiring.md`) と端子割当の正 (`board_pins::kShippedPinPlan`) の
機械的な一致を固定する（タスク 2.2、要件 3.8）。

要件 3.8: 「When 端子割当と図が示す接続関係が食い違った場合, the teleop-bringup
shall その不一致を検出する」。tasks.md 2.2 の注記が明記するとおり、本ファイルは
このうち**機械化できる部分だけ**を担う: 結線表 §2 の端子対照表（機械可読）が
持つ (PinRole, wheel_index, GPIO) の集合と、`kShippedPinPlan` が持つ同じ形の
集合が一致することだけを検査する。図（回路図）が表す接続関係のうち端子に
関わらない部分（接地の合流・実装位置・極性）はタスク 2.3 の目視照合が担い、
ここでは扱わない。

`wiring.md` 側のパースはタスク 2.1 の `test_wiring_doc.parse_pin_cross_reference_rows`
をそのまま再利用する（同一文書に対する2本目のパーサを書かない）。本ファイルが
新設するのは `pin_map.hpp` の `kShippedPinPlan` 配列リテラルを同じ形の
タプルへ変換するパーサと、両者を**真の集合一致**（どちらか一方向の包含だけで
済ませない）として比較する検査だけである。
"""

from __future__ import annotations

import re
from pathlib import Path

from test_wiring_doc import WIRING_MD_TEXT, parse_pin_cross_reference_rows

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_MAP_HPP_PATH = (
    REPO_ROOT / "firmware" / "lib" / "board_pins" / "include" / "board_pins" / "pin_map.hpp"
)

# `kShippedPinPlan` 配列リテラル本体（`kShippedPinPlan[kShippedPinPlanCount] = { ... };`
# の `{ ... }` の中身）を切り出す。非貪欲マッチにより、配列リテラル内に現れない
# `};` が最初に出現した箇所（= 配列そのものの終端）で止まる。
_SHIPPED_PIN_PLAN_BLOCK_PATTERN = re.compile(
    r"kShippedPinPlan\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", re.S
)

# 配列本体の中の1エントリ `{PinRole::kEncoderA, 0, 4}` を抽出する。
# `wheel_index` は数値リテラル（"0"）か `kNoWheel` 識別子のいずれかであり、
# どちらも `\w+` で一様に文字列として捉える（wiring.md 側のパーサが
# `kNoWheel` をそのまま文字列として保持するのと同じ形に揃えるため）。
_PIN_ASSIGNMENT_ENTRY_PATTERN = re.compile(
    r"\{\s*PinRole::(\w+)\s*,\s*(\w+)\s*,\s*(-?\d+)\s*\}"
)


def parse_shipped_pin_plan_rows(cpp_text: str) -> list[tuple[str, str, int]]:
    """`kShippedPinPlan` 配列リテラルから (PinRole 列挙子名, wheel_index, GPIO) を抽出する。

    `parse_pin_cross_reference_rows`（wiring.md 用）と同じタプル形状
    （role は `PinRole::` を除いた列挙子名の文字列、wheel_index は数値または
    `kNoWheel` を表す文字列、gpio は整数）で返す。`kShippedPinPlan` の配列
    リテラルの外側にある `PinRole::` への言及（コメント・他の配列・関数本体の
    `plan[i].role == role` 等）は、まず配列本体だけを切り出してから走査する
    ため拾わない。
    """
    block_match = _SHIPPED_PIN_PLAN_BLOCK_PATTERN.search(cpp_text)
    if block_match is None:
        return []
    block_text = block_match.group(1)
    return [
        (role, wheel_index, int(gpio))
        for role, wheel_index, gpio in _PIN_ASSIGNMENT_ENTRY_PATTERN.findall(block_text)
    ]


def diff_pin_role_wheel_gpio_sets(
    plan_rows: list[tuple[str, str, int]],
    wiring_rows: list[tuple[str, str, int]],
) -> tuple[set[tuple[str, str, int]], set[tuple[str, str, int]]]:
    """2つの (PinRole, wheel_index, GPIO) 集合を**両方向**で比較する。

    片方向の包含（wiring.md が kShippedPinPlan の部分集合であること、または
    その逆）だけでは、もう一方にだけ存在する食い違いを見逃す。戻り値は
    `(kShippedPinPlan にあって wiring.md に無いもの, wiring.md にあって
    kShippedPinPlan に無いもの)` の組であり、両方が空集合であることが
    真の集合一致の証拠になる。
    """
    plan_set = set(plan_rows)
    wiring_set = set(wiring_rows)
    missing_from_wiring = plan_set - wiring_set
    missing_from_plan = wiring_set - plan_set
    return missing_from_wiring, missing_from_plan


PIN_MAP_HPP_TEXT = (
    PIN_MAP_HPP_PATH.read_text(encoding="utf-8") if PIN_MAP_HPP_PATH.is_file() else ""
)


# ---------------------------------------------------------------------------
# 前提の健全性: 両方のパーサが実ファイルから空でない行を取り出せること
# ---------------------------------------------------------------------------


def test_pin_map_hpp_exists() -> None:
    assert PIN_MAP_HPP_PATH.is_file(), (
        "端子割当の正 firmware/lib/board_pins/include/board_pins/pin_map.hpp が存在しない"
        "（タスク1.5の成果物）"
    )


def test_parse_shipped_pin_plan_rows_extracts_fourteen_rows_from_real_file() -> None:
    rows = parse_shipped_pin_plan_rows(PIN_MAP_HPP_TEXT)
    assert len(rows) == 14, (
        f"kShippedPinPlan から抽出できた行数が想定(14)と異なる: {len(rows)} 件抽出: {rows}"
    )


def test_parse_shipped_pin_plan_rows_extracts_crafted_valid_entry() -> None:
    """RED-phase 用の最小データで抽出関数そのものが正しく動くことを確認する。"""
    fake_cpp = (
        "inline constexpr PinAssignment kShippedPinPlan[kShippedPinPlanCount] = {\n"
        "    {PinRole::kEncoderA, 0, 4},\n"
        "    {PinRole::kBatterySense, kNoWheel, 32},\n"
        "};\n"
    )
    assert parse_shipped_pin_plan_rows(fake_cpp) == [
        ("kEncoderA", "0", 4),
        ("kBatterySense", "kNoWheel", 32),
    ]


def test_parse_shipped_pin_plan_rows_ignores_pin_role_mentions_outside_the_array_in_crafted_input() -> (
    None
):
    """誤検知回避: 配列本体の外にある `PinRole::` 言及（コメントや別の関数）を拾わない。"""
    fake_cpp = (
        "// PinRole::kMotorPwm はここでは無視されるべきコメントである\n"
        "constexpr std::int8_t gpioFor(PinRole role) {\n"
        "  if (plan[i].role == PinRole::kMotorDir) { return 1; }\n"
        "}\n"
        "inline constexpr PinAssignment kShippedPinPlan[kShippedPinPlanCount] = {\n"
        "    {PinRole::kEncoderA, 0, 4},\n"
        "};\n"
    )
    assert parse_shipped_pin_plan_rows(fake_cpp) == [("kEncoderA", "0", 4)]


# ---------------------------------------------------------------------------
# 本検査: 実ファイル同士の一致（観測可能な完了状態そのもの）
# ---------------------------------------------------------------------------


def test_pin_map_and_wiring_doc_agree_on_full_pin_role_wheel_gpio_set() -> None:
    """結線表と端子割当を独立に変更すると落ち、両方を揃えると通る検査の本体。

    片方向の包含ではなく、両方向の差分がともに空であることを要求する
    （wiring.md が kShippedPinPlan の部分集合であるだけでは通らない）。
    """
    plan_rows = parse_shipped_pin_plan_rows(PIN_MAP_HPP_TEXT)
    wiring_rows = parse_pin_cross_reference_rows(WIRING_MD_TEXT)
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(plan_rows, wiring_rows)
    assert missing_from_wiring == set() and missing_from_plan == set(), (
        "結線表(wiring.md)と端子割当の正(kShippedPinPlan)が一致しない: "
        f"kShippedPinPlan にあるが wiring.md に無い={sorted(missing_from_wiring)}, "
        f"wiring.md にあるが kShippedPinPlan に無い={sorted(missing_from_plan)}"
    )


# ---------------------------------------------------------------------------
# 検査が実際に赤くなることの証明（片方だけをずらした入力）
# ---------------------------------------------------------------------------


def test_detects_disagreement_when_wiring_md_alone_is_mutated() -> None:
    """`wiring.md` だけをずらし、`pin_map.hpp` は実物のまま扱う。検査が赤くなること。

    `board_pins::kShippedPinPlan` の輪0 kEncoderA は GPIO 4 である
    （pin_map.hpp 実物）。wiring.md の対応する行だけを GPIO 99 へ書き換えた
    コピーを作り、実ファイルの `pin_map.hpp` には一切触れない。
    """
    real_plan_rows = parse_shipped_pin_plan_rows(PIN_MAP_HPP_TEXT)
    assert ("kEncoderA", "0", 4) in real_plan_rows, (
        "前提が崩れている: 実物の kShippedPinPlan に (kEncoderA, 0, 4) が無い"
    )

    mutated_wiring_text = WIRING_MD_TEXT.replace(
        "| kEncoderA | 0 | 4 |", "| kEncoderA | 0 | 99 |"
    )
    assert mutated_wiring_text != WIRING_MD_TEXT, (
        "変異が実際に文字列へ効いていない（対象行のテキストが想定と違う）"
    )

    mutated_wiring_rows = parse_pin_cross_reference_rows(mutated_wiring_text)
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(
        real_plan_rows, mutated_wiring_rows
    )
    assert ("kEncoderA", "0", 4) in missing_from_wiring, (
        "wiring.md だけを書き換えたのに、kShippedPinPlan 側の (kEncoderA, 0, 4) が"
        "『wiring.md に無い』として検出されない"
    )
    assert ("kEncoderA", "0", 99) in missing_from_plan, (
        "wiring.md だけを書き換えたのに、書き換え後の (kEncoderA, 0, 99) が"
        "『kShippedPinPlan に無い』として検出されない"
    )


def test_detects_disagreement_when_pin_map_hpp_alone_is_mutated() -> None:
    """`pin_map.hpp` 相当のテキストだけをずらし、`wiring.md` は実物のまま扱う。

    ⚠️ 出荷済みでレビュー済みの実ファイル `pin_map.hpp` 自体は一切書き換えない。
    実ファイルのテキストを読み取った**メモリ上のコピー**に対して文字列置換で
    変異を加えた合成入力を C++ パーサへ渡す（タスク文の指示どおり）。
    """
    real_wiring_rows = parse_pin_cross_reference_rows(WIRING_MD_TEXT)
    assert ("kEncoderA", "0", 4) in real_wiring_rows, (
        "前提が崩れている: 実物の wiring.md に (kEncoderA, 0, 4) が無い"
    )

    mutated_pin_map_text = PIN_MAP_HPP_TEXT.replace(
        "{PinRole::kEncoderA, 0, 4},", "{PinRole::kEncoderA, 0, 99},"
    )
    assert mutated_pin_map_text != PIN_MAP_HPP_TEXT, (
        "変異が実際に文字列へ効いていない（対象行のテキストが想定と違う）"
    )

    mutated_plan_rows = parse_shipped_pin_plan_rows(mutated_pin_map_text)
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(
        mutated_plan_rows, real_wiring_rows
    )
    assert ("kEncoderA", "0", 99) in missing_from_wiring, (
        "pin_map.hpp 相当のテキストだけを書き換えたのに、書き換え後の"
        "(kEncoderA, 0, 99) が『wiring.md に無い』として検出されない"
    )
    assert ("kEncoderA", "0", 4) in missing_from_plan, (
        "pin_map.hpp 相当のテキストだけを書き換えたのに、wiring.md 側の"
        "(kEncoderA, 0, 4) が『kShippedPinPlan に無い』として検出されない"
    )


def test_agreement_passes_when_crafted_plan_and_wiring_inputs_genuinely_match() -> None:
    """誤検知回避: 独立に作った2つの合成入力が実際に一致していれば検査は通る。"""
    fake_cpp = (
        "inline constexpr PinAssignment kShippedPinPlan[kShippedPinPlanCount] = {\n"
        "    {PinRole::kEncoderA, 0, 4},\n"
        "    {PinRole::kBatterySense, kNoWheel, 32},\n"
        "};\n"
    )
    fake_wiring_md = (
        "| PinRole | wheel_index | GPIO | 信号 |\n"
        "|---|---|---|---|\n"
        "| kEncoderA | 0 | 4 | 輪0 エンコーダA相 |\n"
        "| kBatterySense | kNoWheel | 32 | バッテリ電圧監視 |\n"
    )
    plan_rows = parse_shipped_pin_plan_rows(fake_cpp)
    wiring_rows = parse_pin_cross_reference_rows(fake_wiring_md)
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(plan_rows, wiring_rows)
    assert missing_from_wiring == set()
    assert missing_from_plan == set()


# ---------------------------------------------------------------------------
# 「片方向の包含チェックで済ませていない」ことそのものの証明
# ---------------------------------------------------------------------------


def test_diff_catches_extra_row_that_exists_only_in_wiring_side_in_crafted_input() -> None:
    """wiring.md 側にだけ余分な行があるケース（kShippedPinPlan がその真部分集合）。

    もし検査が「kShippedPinPlan ⊆ wiring.md」という片方向の包含だけで
    済ませていた場合、このケースは見逃される。両方向で見ていることを示す。
    """
    plan_rows = [("kEncoderA", "0", 4)]
    wiring_rows = [("kEncoderA", "0", 4), ("kEncoderB", "0", 13)]
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(plan_rows, wiring_rows)
    assert missing_from_wiring == set()
    assert missing_from_plan == {("kEncoderB", "0", 13)}


def test_diff_catches_extra_row_that_exists_only_in_plan_side_in_crafted_input() -> None:
    """kShippedPinPlan 側にだけ余分な行があるケース（wiring.md がその真部分集合）。

    もし検査が「wiring.md ⊆ kShippedPinPlan」という片方向の包含だけで
    済ませていた場合、このケースは見逃される。両方向で見ていることを示す。
    """
    plan_rows = [("kEncoderA", "0", 4), ("kEncoderB", "0", 13)]
    wiring_rows = [("kEncoderA", "0", 4)]
    missing_from_wiring, missing_from_plan = diff_pin_role_wheel_gpio_sets(plan_rows, wiring_rows)
    assert missing_from_wiring == {("kEncoderB", "0", 13)}
    assert missing_from_plan == set()
