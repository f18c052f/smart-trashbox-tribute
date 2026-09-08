"""結線表 (`.kiro/specs/teleop-bringup/wiring.md`) が機械的に読み取れることを固定する
（タスク 2.1、要件 3.1, 3.2, 3.3）。

タスク 2.1 そのものは文書作成タスクである。だが tasks.md 2.1 の注記は
「端子の欄を機械的に読み取れる形式で持つ。2.2 の照合が成立する前提であり、
自由記述にすると照合の対象にならない」と明記しており、後続のタスク 2.2 が
この文書と `board_pins::kShippedPinPlan`（`firmware/lib/board_pins/include/
board_pins/pin_map.hpp`、タスク 1.5）を機械的に照合する前提になっている。

本ファイルはその前提が成立していること、すなわち「結線表の端子欄が実際に
パース可能な構造化データであり、人間が見て表に見えるだけの自由記述では
ない」ことだけを示す。⚠️ **`kShippedPinPlan` 全件との一致検査はタスク 2.2 の
所有物であり、ここでは行わない。** ここでは既知の値の部分集合が抽出できる
ことだけを確認し、パース可能性の証明に留める。

あわせて、要件 3.2（線色の罠の明示）と要件 3.3（A相/B相の未確定欄）が
文書内に構造的に存在することも軽く固定する（内容の正しさそのものは
人間のレビュー対象であり、ここではキーワードの存在という弱い形でしか
検査できない）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIRING_MD_PATH = REPO_ROOT / ".kiro" / "specs" / "teleop-bringup" / "wiring.md"

_ROLE_PATTERN = re.compile(r"^k[A-Z][A-Za-z0-9]*$")
_INT_PATTERN = re.compile(r"^-?\d+$")


def parse_pin_cross_reference_rows(text: str) -> list[tuple[str, str, int]]:
    """Markdown テーブルの行から (PinRole 列挙子名, wheel_index, GPIO) を抽出する。

    抽出対象を「結線表専用のテーブル」に限定するのではなく、文書全体を
    走査して `|` 区切りかつ最初の列が `board_pins::PinRole` の列挙子名の
    形（`k` + 大文字始まりの識別子）と一致し、3列目が整数である行だけを
    拾う。ヘッダ行（`PinRole` という文字そのもの）や区切り線（`---`）は
    このパターンに一致しないため自然に除外される。線色対応表など、他の
    テーブルの行（セルが日本語の "VCC" 等）も同様に除外される。
    """
    rows: list[tuple[str, str, int]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        role, wheel_index, gpio = cells[0], cells[1], cells[2]
        if not _ROLE_PATTERN.match(role):
            continue
        if not _INT_PATTERN.match(gpio):
            continue
        rows.append((role, wheel_index, int(gpio)))
    return rows


def test_wiring_md_exists() -> None:
    assert WIRING_MD_PATH.is_file(), (
        "結線表 .kiro/specs/teleop-bringup/wiring.md が存在しない（タスク2.1の成果物）"
    )


WIRING_MD_TEXT = WIRING_MD_PATH.read_text(encoding="utf-8") if WIRING_MD_PATH.is_file() else ""


def test_pin_cross_reference_rows_are_parseable_and_non_empty() -> None:
    rows = parse_pin_cross_reference_rows(WIRING_MD_TEXT)
    assert rows != [], "端子対照表から (PinRole, wheel_index, GPIO) を1件も抽出できない"


def test_parse_pin_cross_reference_rows_ignores_non_pin_table_rows_in_crafted_input() -> None:
    """誤検知回避: 線色表など、role 列がPinRole列挙子名でない行は抽出しない。"""
    fake_text = (
        "| 線 | 線色 | 確定状態 |\n"
        "|---|---|---|\n"
        "| VCC | 青 | 確定 |\n"
        "| GND | 黒 | 確定 |\n"
    )
    assert parse_pin_cross_reference_rows(fake_text) == []


def test_parse_pin_cross_reference_rows_extracts_crafted_valid_row() -> None:
    """RED-phase 用の最小データで抽出関数そのものが正しく動くことを確認する。"""
    fake_text = "| kEncoderA | 0 | 4 | 輪0 エンコーダA相 |\n"
    assert parse_pin_cross_reference_rows(fake_text) == [("kEncoderA", "0", 4)]


# board_pins::kShippedPinPlan（firmware/lib/board_pins/include/board_pins/pin_map.hpp、
# タスク 1.5、要件 2.1, 4.7, 6.5）が定める14件のうちの部分集合。
# ⚠️ 全件一致の検査はタスク 2.2 が担う。ここでの一致確認は
# 「結線表の記述形式が実際にパース可能である」ことの証明に限定する。
KNOWN_SHIPPED_PIN_PLAN_SUBSET: set[tuple[str, str, int]] = {
    ("kEncoderA", "0", 4),
    ("kEncoderB", "0", 13),
    ("kMotorPwm", "0", 19),
    ("kMotorDir", "0", 23),
    ("kEncoderA", "1", 14),
    ("kEncoderB", "1", 16),
    ("kMotorPwm", "2", 22),
    ("kMotorDir", "2", 26),
    ("kBatterySense", "kNoWheel", 32),
    ("kBenchPot", "kNoWheel", 33),
}


def test_pin_cross_reference_rows_cover_known_shipped_pin_plan_subset() -> None:
    rows = set(parse_pin_cross_reference_rows(WIRING_MD_TEXT))
    missing = KNOWN_SHIPPED_PIN_PLAN_SUBSET - rows
    assert missing == set(), f"結線表の端子対照表に無い既知の割当: {missing}"


def test_pin_cross_reference_rows_use_all_six_pin_roles_and_no_unknown_role() -> None:
    """役割欄が `board_pins::PinRole` の6列挙子だけで構成され、自由記述の
    日本語名等に化けていないことを確認する（要件3.1「機械的に読み取れる
    形式」の直接対応）。"""
    known_roles = {
        "kEncoderA",
        "kEncoderB",
        "kMotorPwm",
        "kMotorDir",
        "kBatterySense",
        "kBenchPot",
    }
    roles_in_doc = {role for role, _, _ in parse_pin_cross_reference_rows(WIRING_MD_TEXT)}
    assert roles_in_doc == known_roles, (
        f"端子対照表の役割集合が想定とずれている: 欠落={known_roles - roles_in_doc}, "
        f"未知={roles_in_doc - known_roles}"
    )


def test_undetermined_ab_phase_marker_is_present_as_a_distinct_section() -> None:
    """A相/B相の線色が「未確定」として、確定済み項目と構造的に見分けられる
    形（見出しを持つ独立節）で存在することを確認する（要件3.3）。"""
    assert "未確定" in WIRING_MD_TEXT
    assert re.search(r"^#+.*未確定.*$", WIRING_MD_TEXT, re.M) is not None, (
        "「未確定」というキーワードだけでなく、見出し付きの独立節として存在すること"
    )


def test_black_white_trap_is_stated_with_concrete_consequence() -> None:
    """黒=エンコーダGND / モータ電源-=白 の取り違えトラップが、
    具体的な結果（エンコーダの破壊）とともに明示されていることを確認する
    （要件3.2）。"""
    assert "黒" in WIRING_MD_TEXT
    assert "白" in WIRING_MD_TEXT
    assert "破壊" in WIRING_MD_TEXT, "取り違えたときの具体的な結果（破壊）が明示されていない"


def test_power_switch_and_terminal_block_placeholder_is_a_distinct_section() -> None:
    """タスク 2.4（要件3.9）: メイン電源スイッチ／電源分岐端子の決着を
    取り込む受け口が、§3の未確定欄と同様に見出し付きの独立した節として
    存在することを確認する。「未確定」節（実測待ち・自力で解消可能）とは
    別の節であるべきなので、両者の見出しが異なることも確認する。"""
    undetermined_headings = re.findall(r"^#+.*未確定.*$", WIRING_MD_TEXT, re.M)
    assert undetermined_headings != [], "§3 の未確定欄の見出しが見つからない（前提が崩れている）"

    power_headings = re.findall(r"^#+.*(?:電源スイッチ|電源分岐端子|電源系統).*$", WIRING_MD_TEXT, re.M)
    assert power_headings != [], (
        "電源スイッチ／電源分岐端子の受け口が、見出し付きの独立した節として存在しない"
    )
    assert set(power_headings).isdisjoint(set(undetermined_headings)), (
        "電源系統の受け口が§3の未確定欄と同一の見出しに同居している"
        "（機構側の決定待ちと実測待ちは別種の未決着であり、区別できる節にすること）"
    )


def test_power_switch_and_terminal_block_section_states_this_spec_does_not_decide() -> None:
    """本 Spec がメイン電源スイッチ／電源分岐端子を決定しないこと、および
    決着責任が chassis-mechanism にあることが明示されていることを確認する
    （要件3.9: 「隣接する機構側の決定として取り込み、自身では決定しない」）。"""
    assert "chassis-mechanism" in WIRING_MD_TEXT
    assert re.search(r"決定しない|決めない|決着しない", WIRING_MD_TEXT) is not None, (
        "本 Spec が電源系統を決定しない旨の明示的な文言が見つからない"
    )
    # OQ-11 (メイン電源スイッチ) と OQ-12 (電源分岐端子) の両方に触れていること
    assert "OQ-11" in WIRING_MD_TEXT
    assert "OQ-12" in WIRING_MD_TEXT


def test_power_switch_and_terminal_block_section_pins_down_where_to_update() -> None:
    """観測可能な完了状態: 機構側の決着が下りたときに、結線表のどこを
    更新すればよいかが一意に定まること。更新先として §4.1 / §4.2 の
    ような具体的な副節番号を名指ししていることを確認する（曖昧な
    「後で書く」ではなく、一意な参照先を持つこと）。"""
    assert re.search(r"§4\.1", WIRING_MD_TEXT) is not None, (
        "メイン電源スイッチの更新先として §4.1 のような具体的な副節番号が示されていない"
    )
    assert re.search(r"§4\.2", WIRING_MD_TEXT) is not None, (
        "電源分岐端子の更新先として §4.2 のような具体的な副節番号が示されていない"
    )


def test_power_switch_and_terminal_block_section_has_no_decided_placeholder_values() -> None:
    """本 Spec が決定を先取りしていない（＝要否・位置・方式の具体的な
    決定値を書いていない）ことを、§4 の未決着マーカーの本数で弱く確認する。
    2件の決定事項（要否×2、方式×2、位置×1、保持箇所・配線経路×1＝計6行）
    それぞれに未決着マーカーが付いていることを期待する。"""
    section4_match = re.search(r"^## 4\..*$(.*?)(?=^## |\Z)", WIRING_MD_TEXT, re.M | re.S)
    assert section4_match is not None, "§4（電源系統の決着待ち）が見つからない"
    section4_text = section4_match.group(0)
    undecided_count = section4_text.count("未決着")
    assert undecided_count >= 6, (
        f"§4 内の「未決着」マーカーが想定より少ない（{undecided_count}件）。"
        "要否・位置・方式のそれぞれについて未決着であることを明示すること"
    )
