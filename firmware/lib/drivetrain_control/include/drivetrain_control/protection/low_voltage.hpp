#pragma once

// drivetrain_control LowVoltageProtector (design.md L7-L8 保護層
// "LowVoltageProtector", 要件 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.8).
//
// 保護②: 急加速時の一時的な電圧降下では誤停止せず、真に危険な低電圧では
// 警告 → 停止まで進める状態機械。design.md "保護② LiPo 低電圧の状態遷移"
// のとおり、4状態を持つ:
//
//   Normal      --[移動平均が warn_milli_volts を下回る]--> Warning
//   Warning     --[移動平均が warn_milli_volts 以上へ復帰]--> Normal
//   Warning     --[移動平均が stop_milli_volts 未満の状態が
//                  stop_duration_ms を超えて継続]--> Tripped
//   Normal      --[同上]--> Tripped
//   Tripped     --[移動平均が recover_milli_volts 以上 かつ
//                  latching == false（自動復帰）]--> Normal
//   Tripped     --[latching == true]--> Tripped（reset() まで維持）
//   Normal      --[有効な読み値が得られない状態が
//                  unavailable_duration_ms を超えて継続]--> Unavailable
//   Warning     --[同上]--> Unavailable
//   Unavailable --[有効な読み値が復帰し 移動平均が recover_milli_volts
//                  以上]--> Normal（`latching` に関わらず無条件。
//                  design.md の遷移表に latching の条件が付いていない）
//
// ⚠️ Tripped からは Unavailable への遷移が存在しない（design.md の
// state diagram に矢印がない）。Tripped 中に読み値が欠測し続けても、
// 状態は Tripped のまま（`latching` による復帰判定のみが有効）に留まる。
//
// 移動平均（要件 11.3, 11.8）:
//   - `valid == false` のサンプルは移動平均へ**取り込まない**。
//   - 動的メモリ確保を行わない固定長リングバッファ（`kMaxVoltageWindow`
//     容量の配列）に、直近の**有効な**サンプルを最大 `average_window`
//     件保持する（`average_window` は 1..kMaxVoltageWindow。設定検証
//     [config.cpp] で保証される）。
//   - 判定はすべて `std::int32_t` の mV 整数演算で行う（要件 11.3 の
//     移動平均を含む）。合計は `std::int64_t` へ積算してから
//     `count_` で除算する。C++ の整数除算は 0 方向への切り捨てと
//     処理系非依存であることが規格で保証されており（[expr.mul]）、
//     浮動小数点を経由する場合に生じ得る丸めのプラットフォーム依存を
//     構造的に排除する。
//   - **設計判断（design.md に明記されていない補完）**: `average_window`
//     件のサンプルがまだ1件も蓄積されていない場合（`count_ == 0`）、
//     警告・停止のしきい値判定はまだ行わない（既定値 0 mV を「危険な
//     低電圧」と誤判定して起動直後に即座に警告／停止してしまうのを
//     避けるため）。`voltage_valid()` はこの `count_ > 0` を反映する。
//
// 欠測の継続時間と低電圧の継続時間は独立した2本のタイマーで管理する。
// 同一の update() 呼び出しで両方の継続時間がしきい値を超えた場合
// （例: 直前まで低電圧が続いていた状態で読み値が欠測し始めてから
// unavailable_duration_ms が経過した瞬間に、たまたま古い平均値が
// stop_duration_ms も超えていた場合）は、**欠測（Unavailable）を優先**
// する（要件 11.8「低電圧とは別の理由として停止する」を、事象が競合した
// ときにも欠測理由が埋もれないように優先させることで満たす）。
//
// reset() の意味論（設計判断。タスク 5.1 MotorLockDetector::reset() と
// 同じ精度で揃える）: `reset(TimeMs now)` は電圧サンプルを引数に取らない
// ため、呼ばれた時点で低電圧条件がまだ成立しているかをこのクラス自身は
// 再評価できない。そのため `reset()` は**無条件に** Normal へ戻す
// （Tripped からも Unavailable からも）。「解除条件を満たす保護だけを
// 解く」というゲーティング（要件 14.6）は `ProtectionSupervisor::
// resetProtections()`（タスク 5.5 の範囲）の責務であり、このクラスの
// 責務ではない。呼び出し側が「まだ低電圧条件が続いている間は reset() を
// 呼ばない」ことを保証する必要がある。
//
// L7 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include <cstdint>

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

enum class LowVoltageState : std::uint8_t { kNormal, kWarning, kTripped, kUnavailable };

class LowVoltageProtector {
 public:
  explicit LowVoltageProtector(const LowVoltageParams& params) noexcept;

  // sample: バッテリ電圧ポートから得た1サンプル（要件 11.8: valid ==
  //         false のときは移動平均へ取り込まない）。
  // now:    単調増加する現在時刻。
  // 戻り値: 更新後の状態（state() と同じ値）。
  LowVoltageState update(const VoltageSample& sample, TimeMs now) noexcept;

  LowVoltageState state() const noexcept;

  // 判定に使った移動平均値（mV）。voltage_valid() が false のときは
  // 意味を持たない（まだ1件も有効なサンプルが無い、既定値 0）。
  std::int32_t averaged_milli_volts() const noexcept;

  // 移動平均に少なくとも1件の有効なサンプルが反映されているか。
  bool voltage_valid() const noexcept;

  // 手動リセット。Tripped/Unavailable を無条件に解除し Normal へ戻す
  // （上記ファイル先頭の reset() の意味論を参照。ゲーティングは
  // 呼び出し側の責務）。
  void reset(TimeMs now) noexcept;

 private:
  void pushValidSample(std::int32_t milli_volts) noexcept;

  LowVoltageParams params_;
  LowVoltageState state_ = LowVoltageState::kNormal;

  // 固定長リングバッファ（動的メモリ確保を行わない。要件 11.3 の冒頭）。
  // 実際に使う容量は params_.average_window (<= kMaxVoltageWindow)。
  std::int32_t buffer_[kMaxVoltageWindow] = {};
  std::uint8_t count_ = 0;        // 現在蓄積されている有効サンプル数
  std::uint8_t next_index_ = 0;   // 次に書き込むリングバッファの添字
  std::int64_t sum_ = 0;          // buffer_[0..count_) の合計（mV、int64 で桁あふれを避ける）

  // 低電圧（停止しきい値未満）の継続時間タイマー。
  bool low_voltage_timer_active_ = false;
  TimeMs low_voltage_started_at_ms_ = 0;

  // 欠測（valid == false）の継続時間タイマー。
  bool unavailable_timer_active_ = false;
  TimeMs unavailable_started_at_ms_ = 0;
};

}  // namespace drivetrain_control
