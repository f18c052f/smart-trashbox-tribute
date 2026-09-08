#pragma once

// teleop-bringup EncoderPcntAdapter (design.md "EncoderPcntAdapter /
// MotorLedcAdapter / BatteryAdcAdapter", 要件 4.1-4.6, タスク 3.1).
//
// drivetrain_control::EncoderPort の実装。ESP-IDF の PCNT ドライバ
// (driver/pulse_cnt.h) を用いて3輪ぶんのエンコーダを直交（クアドラチャ）
// デコードし、折り返しを含まない64bit累積カウントを返す。
//
// ⚠️ 判断・計算を持たない（design.md "Responsibilities & Constraints"、
// ports.hpp の契約）。
//   - 折り返しの桁上げは drivetrain_control::WrapAccumulator に委ねる
//     （要件 4.3）。ハードウェアカウンタ（符号付き16bit、-32768〜32767）
//     は法 65536 で自然に折り返すため、PCNT の low_limit/high_limit を
//     この全域（PCNT_LL_MIN_LIM/PCNT_LL_MAX_LIM、ESP32 classic）へ設定し、
//     ESP-IDF 自身のウォッチポイント桁上げ機構（accum_count）は使わない。
//     使うと桁上げが WrapAccumulator へ委ねられていることがコードから
//     読み取れなくなる（観測可能な完了状態、タスク 3.1）。read() は
//     生値をそのまま WrapAccumulator::update() へ渡すだけである。
//   - 輪ごとの向き反転（要件 4.4）は演算ではなく、2つの固定アクション表
//     （encoder_pcnt.cpp の kForwardQuadratureActions /
//     kReverseQuadratureActions）のどちらを使うかという設定選択でしか
//     ない。
//   - ノイズ除去（要件 4.5, 4.6）は PCNT のグリッチフィルタへ設定値を
//     そのまま渡す。ハードウェアの上限は 1023 APB クロック
//     （PCNT_LL_MAX_GLITCH_WIDTH、80MHz で約12.8us）であり、これは硬い
//     天井である。kMaxGlitchFilterNs はこの天井の内側に収めた目安上限
//     であり、それ以上を設定へ渡すと初期化時に ESP_ERROR_CHECK が中断
//     する（判断してクランプするのではなく、失敗を露出させる）。
//   - 端子番号は board_pins（端子割当の正）からのみ取得する。
//
// ⚠️ このアダプタ自体はホストで検証できない（`firmware/src/` は
// `[env:native]` の対象外、`test_build_src` 既定 `no`）。だからこそ
// 上記のとおり判断を持たせない（design.md "Validation" for
// EncoderPcntAdapter — 判断を持たせた瞬間に検証不能な領域が増える）。

#include <cstdint>

#include "driver/pulse_cnt.h"

#include "board_pins/pin_map.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/wrap_accumulator.hpp"

namespace teleop {

// PCNT グリッチフィルタのハードウェア上限（1023 APB クロック、
// PCNT_LL_MAX_GLITCH_WIDTH）を ns に換算した値の内側に収めた目安上限。
// APB クロックは ESP32 classic では 80MHz 固定（PCNT はこの周波数でのみ
// 動作する）。1023 / 80,000,000 秒 = 12,787.5 ns であり、整数変換の
// 丸め（glitch_filter_thres = apb_MHz * max_glitch_ns / 1000、ESP-IDF
// 実装）で 1023 を超えないよう切り捨てて 12787 ns を上限とする
// （要件 4.6 — 設定可能範囲をハードウェアが許す上限の内側に収める）。
inline constexpr std::uint32_t kMaxGlitchFilterNs = 12787;

// 輪ごとの設定値。演算を伴わない、そのまま使われる値のみを持つ
// （design.md「輪ごとの向き反転（4.4）は設定値として持ち、演算はしない」）。
struct EncoderPcntConfig {
  bool invert_direction = false;  // 要件 4.4。増減の向きの反転
  // 要件 4.5, 4.6。短いパルスを計数から除外する幅（ns）。
  // kMaxGlitchFilterNs 以下に収めること（実測校正前の目安値）。
  std::uint32_t glitch_filter_ns = 1000;
};

// drivetrain_control::EncoderPort の実装（要件 4.1〜4.6）。
class EncoderPcntAdapter final : public drivetrain_control::EncoderPort {
 public:
  // plan: 端子割当の正（通常 board_pins::kShippedPinPlan）。
  // configs: 輪ごとの設定（drivetrain_control::kWheelCount 個、輪の添字順）。
  // Preconditions: plan は各輪の PinRole::kEncoderA / kEncoderB の割当を
  // 持つこと。コンストラクタは ESP-IDF PCNT ペリフェラルを初期化し、
  // 開始した状態で戻る（value() == 0 が起動時点の原点）。
  EncoderPcntAdapter(
      const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
      const EncoderPcntConfig (&configs)[drivetrain_control::kWheelCount]);

  ~EncoderPcntAdapter() override;

  EncoderPcntAdapter(const EncoderPcntAdapter&) = delete;
  EncoderPcntAdapter& operator=(const EncoderPcntAdapter&) = delete;
  EncoderPcntAdapter(EncoderPcntAdapter&&) = delete;
  EncoderPcntAdapter& operator=(EncoderPcntAdapter&&) = delete;

  // 折り返しを含まない累積カウントを返す（要件 4.1, 4.2）。
  // Postconditions: 核の状態を変えない（ports.hpp の契約）。
  drivetrain_control::EncoderCounts read() override;

 private:
  void InitWheel(
      std::uint8_t wheel,
      const board_pins::PinAssignment (&plan)[board_pins::kShippedPinPlanCount],
      const EncoderPcntConfig& config);

  pcnt_unit_handle_t units_[drivetrain_control::kWheelCount] = {};
  pcnt_channel_handle_t channels_a_[drivetrain_control::kWheelCount] = {};
  pcnt_channel_handle_t channels_b_[drivetrain_control::kWheelCount] = {};
  // 折り返しの桁上げ処理そのもの（要件 4.3）。EncoderPort::read() が
  // 唯一使う「計算」はこのメンバへの委譲であり、アダプタ自身は持たない。
  drivetrain_control::WrapAccumulator accumulators_[drivetrain_control::kWheelCount];
};

}  // namespace teleop
