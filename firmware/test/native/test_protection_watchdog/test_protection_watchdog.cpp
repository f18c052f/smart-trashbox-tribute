#include <unity.h>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/protection/command_watchdog.hpp"
#include "drivetrain_control/types.hpp"

// drivetrain-core task 5.4: 保護④ 指令ウォッチドッグ（CommandWatchdog）を
// 検証するホストテスト。
//
// design.md "CommandWatchdog"（L7-L8 保護層）の厳密なシグネチャ・純判定
// 契約を対象にする。要件 13.1, 13.2, 13.3, 13.4, 13.7, 13.8。
//
// 以下を検証する:
//   A. 指令を止めて時刻だけを進めるとタイムアウトで発火する（要件
//      13.1, 13.4: last_command_at_ms を固定したまま now だけを進める）
//   B. 境界値: 経過時間 == timeout_ms は非発火、timeout_ms を1でも
//      超えると発火する（design.md Postconditions の "<=" 境界）
//   C. 新しい有効指令（last_command_at_ms が現在時刻に近い値へ更新される）
//      で発火が解除される（要件 13.6 前半: タイムアウト状態の解除。
//      出力再許可のゲーティングは ProtectionSupervisor の責務であり
//      対象外）
//   D. 指令の投入を一切行わずに時刻だけを進めても判定が働く: has_command
//      が常に false（起動後まだ一度も有効な指令を受けていない）のとき、
//      last_command_at_ms の値に関わらず常に発火する（要件 13.4, 14.10。
//      コマンド投入 API を一切呼ばずに now だけを複数回進めて確認する）
//   E. 指令元に固有の情報を一切参照しない（要件 13.2, 13.7）:
//      引数は時刻2つと真偽値1つだけであることをシグネチャそのもので
//      裏取りする（コンパイルが通ること自体が証拠）。あわせて、
//      同じ (now, last_command_at_ms, has_command) の組であれば結果が
//      再現すること（呼び出し履歴に依存しない）を確認する
//   F. tripped() は直近の update() の戻り値と一致する（構築直後は
//      要件 14.10 により発火中）

using drivetrain_control::CommandWatchdog;
using drivetrain_control::DurationMs;
using drivetrain_control::TimeMs;
using drivetrain_control::WatchdogParams;

namespace {

WatchdogParams makeParams(DurationMs timeout_ms) {
  WatchdogParams p{};
  p.timeout_ms = timeout_ms;
  return p;
}

}  // namespace

void setUp(void) {}
void tearDown(void) {}

// ---------------------------------------------------------------------------
// F: 構築直後は発火中扱い（要件 14.10: 起動後まだ一度も有効な指令を
// 受けていない）。tripped() は最後の update() の結果を反映する。
// ---------------------------------------------------------------------------

void test_initial_state_is_tripped(void) {
  CommandWatchdog wd(makeParams(/*timeout_ms=*/100));
  TEST_ASSERT_TRUE(wd.tripped());
}

// ---------------------------------------------------------------------------
// A + C: 指令を止めて時刻だけを進めるとタイムアウトで発火し、新しい
// 有効指令（last_command_at_ms の更新）で解除される（要件 13.1, 13.4,
// 13.6 前半）。
// ---------------------------------------------------------------------------

void test_times_out_when_commands_stop_and_clears_on_new_command(void) {
  const DurationMs kTimeoutMs = 100;
  CommandWatchdog wd(makeParams(kTimeoutMs));

  const TimeMs kLastCommandAtMs = 1000;

  // 指令直後: 経過時間 0 <= timeout。非発火。
  TEST_ASSERT_FALSE(wd.update(/*now=*/1000, kLastCommandAtMs, /*has_command=*/true));
  TEST_ASSERT_FALSE(wd.tripped());

  // 新しい指令が来ないまま時刻だけが進む（last_command_at_ms は固定）。
  // timeout 未満の間は非発火のまま。
  TEST_ASSERT_FALSE(wd.update(/*now=*/1050, kLastCommandAtMs, /*has_command=*/true));
  TEST_ASSERT_FALSE(wd.tripped());

  // timeout を超えて時刻が進む -> 発火。
  TEST_ASSERT_TRUE(wd.update(/*now=*/1150, kLastCommandAtMs, /*has_command=*/true));
  TEST_ASSERT_TRUE(wd.tripped());

  // さらに時刻が進んでも発火したまま（新しい指令が来ない限り解除されない）。
  TEST_ASSERT_TRUE(wd.update(/*now=*/2000, kLastCommandAtMs, /*has_command=*/true));
  TEST_ASSERT_TRUE(wd.tripped());

  // 新しい有効指令が到着（last_command_at_ms が現在時刻に近い値へ更新
  // される）-> 発火が解除される（要件 13.6 前半）。
  TEST_ASSERT_FALSE(wd.update(/*now=*/2010, /*last_command_at_ms=*/2005, /*has_command=*/true));
  TEST_ASSERT_FALSE(wd.tripped());
}

// ---------------------------------------------------------------------------
// B: 境界値。経過時間 == timeout_ms は非発火、timeout_ms を1でも超えると
// 発火する（design.md Postconditions の "<=" 境界）。
// ---------------------------------------------------------------------------

void test_boundary_at_exactly_timeout_is_not_tripped(void) {
  const DurationMs kTimeoutMs = 100;
  CommandWatchdog wd(makeParams(kTimeoutMs));
  const TimeMs kLastCommandAtMs = 500;

  // elapsed == timeout_ms ちょうど -> 非発火。
  TEST_ASSERT_FALSE(wd.update(/*now=*/600, kLastCommandAtMs, /*has_command=*/true));

  // elapsed == timeout_ms + 1 -> 発火。
  TEST_ASSERT_TRUE(wd.update(/*now=*/601, kLastCommandAtMs, /*has_command=*/true));
}

// ---------------------------------------------------------------------------
// D: 指令の投入を一切行わずに時刻だけを進めても判定が働く。has_command が
// 常に false（起動後まだ一度も有効な指令を受けていない）のとき、
// last_command_at_ms の値に関わらず常に発火する（要件 13.4, 14.10）。
// このテストは指令投入 API を一切呼ばない（このクラスにそもそも指令投入
// API は存在しない。has_command/last_command_at_ms は毎回直接構築する）。
// ---------------------------------------------------------------------------

void test_works_by_advancing_time_alone_without_ever_submitting_a_command(void) {
  const DurationMs kTimeoutMs = 100;
  CommandWatchdog wd(makeParams(kTimeoutMs));

  // has_command=false のまま、last_command_at_ms=0 固定で now だけを
  // 複数回進める。指令投入は一度も行わない。
  TEST_ASSERT_TRUE(wd.update(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/false));
  TEST_ASSERT_TRUE(wd.tripped());

  TEST_ASSERT_TRUE(wd.update(/*now=*/10, /*last_command_at_ms=*/0, /*has_command=*/false));
  TEST_ASSERT_TRUE(wd.tripped());

  TEST_ASSERT_TRUE(wd.update(/*now=*/100000, /*last_command_at_ms=*/0, /*has_command=*/false));
  TEST_ASSERT_TRUE(wd.tripped());

  // has_command=false であれば、たとえ経過時間が 0（last_command_at_ms が
  // now と同一）でも発火する。「指令元固有の経過時間」ではなく
  // has_command 自体が支配的であることの裏取り。
  TEST_ASSERT_TRUE(wd.update(/*now=*/500, /*last_command_at_ms=*/500, /*has_command=*/false));
  TEST_ASSERT_TRUE(wd.tripped());
}

// ---------------------------------------------------------------------------
// E: 状態が (now, last_command_at_ms, has_command) の組だけで決まる
// （呼び出し履歴に依存しない再現性）。指令元固有の情報を持たないことの
// 間接的な裏取り。
// ---------------------------------------------------------------------------

void test_result_is_reproducible_for_same_argument_triple(void) {
  const DurationMs kTimeoutMs = 100;
  CommandWatchdog wd(makeParams(kTimeoutMs));

  const bool first = wd.update(/*now=*/1000, /*last_command_at_ms=*/850, /*has_command=*/true);

  // 間に別の呼び出し（発火・非発火どちらも経由）を挟む。
  wd.update(/*now=*/0, /*last_command_at_ms=*/0, /*has_command=*/false);
  wd.update(/*now=*/5000, /*last_command_at_ms=*/100, /*has_command=*/true);

  // 同じ引数の組へ戻せば同じ結果になる。
  const bool second = wd.update(/*now=*/1000, /*last_command_at_ms=*/850, /*has_command=*/true);

  TEST_ASSERT_EQUAL(first, second);
  // elapsed = 1000 - 850 = 150 > timeout_ms(100) -> 発火(true)。
  TEST_ASSERT_TRUE(second);
}

int main(int argc, char **argv) {
  (void)argc;
  (void)argv;
  UNITY_BEGIN();

  RUN_TEST(test_initial_state_is_tripped);
  RUN_TEST(test_times_out_when_commands_stop_and_clears_on_new_command);
  RUN_TEST(test_boundary_at_exactly_timeout_is_not_tripped);
  RUN_TEST(test_works_by_advancing_time_alone_without_ever_submitting_a_command);
  RUN_TEST(test_result_is_reproducible_for_same_argument_triple);

  return UNITY_END();
}
