#include "teleop/encoder_pcnt.hpp"

#include <cassert>

#include "esp_err.h"

namespace teleop {

namespace {

using board_pins::PinRole;

// ハードウェアカウンタの表現範囲（符号付き16bit、-32768〜32767）を
// そのまま PCNT の low_limit/high_limit に使う。この2値は ESP32 classic の
// PCNT_LL_MIN_LIM / PCNT_LL_MAX_LIM（<limits.h> の SHRT_MIN/SHRT_MAX）と
// 一致し、法（modulus）65536 は high - low + 1 に等しい
// （drivetrain_control::WrapAccumulator の modulus 引数、要件 4.2）。
constexpr int kPcntLowLimit = -32768;
constexpr int kPcntHighLimit = 32767;
constexpr std::int32_t kPcntModulus = 65536;

// クアドラチャ（直交）デコードのチャネルアクション表。輪ごとの向き反転
// （要件 4.4）は、この2つの固定表のどちらを使うかという設定選択でしか
// ない（演算をしない）。チャネルAはA相をエッジ信号・B相をレベル信号として
// 使い、チャネルBはその逆（B相をエッジ・A相をレベル）とする、A/B両エッジを
// 数える4逓倍の標準構成である
// （drivetrain_control::WrapAccumulator ヘッダの見積りコメントが前提とする
// 「4逓倍（直交エンコーダのA/B両エッジを数える方式）」と同じもの）。
struct QuadratureActions {
  pcnt_channel_edge_action_t chan_a_pos;
  pcnt_channel_edge_action_t chan_a_neg;
  pcnt_channel_edge_action_t chan_b_pos;
  pcnt_channel_edge_action_t chan_b_neg;
};

constexpr QuadratureActions kForwardQuadratureActions = {
    PCNT_CHANNEL_EDGE_ACTION_DECREASE,
    PCNT_CHANNEL_EDGE_ACTION_INCREASE,
    PCNT_CHANNEL_EDGE_ACTION_INCREASE,
    PCNT_CHANNEL_EDGE_ACTION_DECREASE,
};

constexpr QuadratureActions kReverseQuadratureActions = {
    PCNT_CHANNEL_EDGE_ACTION_INCREASE,
    PCNT_CHANNEL_EDGE_ACTION_DECREASE,
    PCNT_CHANNEL_EDGE_ACTION_DECREASE,
    PCNT_CHANNEL_EDGE_ACTION_INCREASE,
};

}  // namespace

EncoderPcntAdapter::EncoderPcntAdapter(
    const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
    const EncoderPcntConfig (&configs)[drivetrain_control::kWheelCount])
    // 起動時点を原点とする（WrapAccumulator の事前状態）。実際の生値は
    // InitWheel が PCNT 開始後に読み直して reset() で置き換える。
    : accumulators_{drivetrain_control::WrapAccumulator(kPcntModulus, 0),
                     drivetrain_control::WrapAccumulator(kPcntModulus, 0),
                     drivetrain_control::WrapAccumulator(kPcntModulus, 0)} {
  static_assert(drivetrain_control::kWheelCount == 3,
                "accumulators_ initializer list has exactly kWheelCount entries");
  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    InitWheel(wheel, plan, configs[wheel]);
  }
}

void EncoderPcntAdapter::InitWheel(
    std::uint8_t wheel,
    const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
    const EncoderPcntConfig& config) {
  // 端子番号は端子割当の正からのみ取得する。GPIO リテラルを自前で持たない。
  const std::int8_t gpio_a = board_pins::gpioFor(plan, PinRole::kEncoderA, wheel);
  const std::int8_t gpio_b = board_pins::gpioFor(plan, PinRole::kEncoderB, wheel);
  assert(gpio_a != board_pins::kUnassigned);
  assert(gpio_b != board_pins::kUnassigned);

  pcnt_unit_config_t unit_config = {};
  unit_config.low_limit = kPcntLowLimit;
  unit_config.high_limit = kPcntHighLimit;
  unit_config.flags.accum_count = 0;  // 桁上げは WrapAccumulator に委ねる（要件 4.3）
  ESP_ERROR_CHECK(pcnt_new_unit(&unit_config, &units_[wheel]));

  pcnt_glitch_filter_config_t filter_config = {};
  filter_config.max_glitch_ns = config.glitch_filter_ns;  // 要件 4.5, 4.6
  ESP_ERROR_CHECK(pcnt_unit_set_glitch_filter(units_[wheel], &filter_config));

  pcnt_chan_config_t chan_a_config = {};
  chan_a_config.edge_gpio_num = gpio_a;
  chan_a_config.level_gpio_num = gpio_b;
  ESP_ERROR_CHECK(pcnt_new_channel(units_[wheel], &chan_a_config, &channels_a_[wheel]));

  pcnt_chan_config_t chan_b_config = {};
  chan_b_config.edge_gpio_num = gpio_b;
  chan_b_config.level_gpio_num = gpio_a;
  ESP_ERROR_CHECK(pcnt_new_channel(units_[wheel], &chan_b_config, &channels_b_[wheel]));

  // 向き反転は演算ではなく設定選択（要件 4.4）。
  const QuadratureActions& actions =
      config.invert_direction ? kReverseQuadratureActions : kForwardQuadratureActions;
  ESP_ERROR_CHECK(pcnt_channel_set_edge_action(channels_a_[wheel], actions.chan_a_pos, actions.chan_a_neg));
  ESP_ERROR_CHECK(pcnt_channel_set_level_action(
      channels_a_[wheel], PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));
  ESP_ERROR_CHECK(pcnt_channel_set_edge_action(channels_b_[wheel], actions.chan_b_pos, actions.chan_b_neg));
  ESP_ERROR_CHECK(pcnt_channel_set_level_action(
      channels_b_[wheel], PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE));

  ESP_ERROR_CHECK(pcnt_unit_enable(units_[wheel]));
  ESP_ERROR_CHECK(pcnt_unit_clear_count(units_[wheel]));
  ESP_ERROR_CHECK(pcnt_unit_start(units_[wheel]));

  int initial_raw = 0;
  ESP_ERROR_CHECK(pcnt_unit_get_count(units_[wheel], &initial_raw));
  accumulators_[wheel].reset(initial_raw, 0);
}

EncoderPcntAdapter::~EncoderPcntAdapter() {
  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    if (units_[wheel] != nullptr) {
      pcnt_unit_stop(units_[wheel]);
      pcnt_unit_disable(units_[wheel]);
    }
    if (channels_a_[wheel] != nullptr) {
      pcnt_del_channel(channels_a_[wheel]);
    }
    if (channels_b_[wheel] != nullptr) {
      pcnt_del_channel(channels_b_[wheel]);
    }
    if (units_[wheel] != nullptr) {
      pcnt_del_unit(units_[wheel]);
    }
  }
}

drivetrain_control::EncoderCounts EncoderPcntAdapter::read() {
  drivetrain_control::EncoderCounts counts{};
  for (std::uint8_t wheel = 0; wheel < drivetrain_control::kWheelCount; ++wheel) {
    int raw = 0;
    ESP_ERROR_CHECK(pcnt_unit_get_count(units_[wheel], &raw));
    // 折り返しの桁上げは上流の累積器へ委ねる（要件 4.3）。ここでは生値を
    // そのまま渡すだけで、独自の桁上げ・判断は一切行わない。
    counts.count[wheel] = accumulators_[wheel].update(static_cast<std::int32_t>(raw));
  }
  return counts;
}

}  // namespace teleop
