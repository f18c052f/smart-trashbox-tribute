#pragma once

// drivetrain_control CommandWatchdog (design.md L7-L8 保護層
// "CommandWatchdog", 要件 13.1, 13.2, 13.3, 13.4, 13.7, 13.8).
//
// 保護④: 「最後に有効な指令を受けてからの経過時間」だけを見て発火する
// 判定器。design.md "CommandWatchdog" Postconditions のとおり、
//
//   has_command && (now - last_command_at_ms) <= timeout_ms
//
// のときのみ非発火。それ以外（has_command が偽、または経過時間が
// timeout_ms を厳密に超える）は常に発火中として扱う。
//
// ⚠️ 指令元に固有の情報を一切参照しない（要件 13.2, 13.7）。引数は時刻
// 2つ（now, last_command_at_ms）と真偽値1つ（has_command）だけであり、
// 指令元・通信方式・入力機器の状態は一切受け取らない。M3 で指令元が
// 固定側からの受信メッセージへ変わっても、**この部品は1文字も変わらない**
// （design.md 明記）。
//
// 判定は制御ステップ側から毎周期呼ばれる（要件 13.4）。指令の到着イベント
// には依存しない。update() を呼ばない期間があっても、次に呼ばれた時点の
// now と last_command_at_ms だけで判定が決まる（状態機械のような
// 「継続時間の計測」を内部に持たない。継続時間そのものが
// now - last_command_at_ms という引数の差分として与えられるため）。
//
// ⚠️ 本保護を物理的な非常停止手段の代替として位置付けない（要件 13.8）。
// 無線に依存する停止手段は「運転操作」であって安全装置ではない
// （tech.md 開発標準2）。物理的な非常停止手段は別途 OQ-13 として決まる。
//
// 発火中の目標速度ゼロ化・出力遮断（要件 13.5）と、解除後も出力許可が
// 別途必要であること（要件 13.6）は、このクラス自身の責務ではなく
// 呼び出し側（DrivetrainController / ProtectionSupervisor、design.md
// 参照）が扱う。本クラスは tripped() の真偽だけを提供する。
//
// L7 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include "drivetrain_control/config.hpp"

namespace drivetrain_control {

class CommandWatchdog {
 public:
  explicit CommandWatchdog(const WatchdogParams& params) noexcept;

  // now:               単調増加する現在時刻。
  // last_command_at_ms: CommandInput が保持する最後の有効指令の有効時刻
  //                      （指令の内容や指令元には一切依存しない）。
  // has_command:        起動後まだ一度も有効な指令を受けていなければ
  //                      false（要件 14.10）。
  // 戻り値: 発火中（タイムアウト、または一度も指令を受けていない）なら
  //         true。tripped() が返す値と同じ。
  bool update(TimeMs now, TimeMs last_command_at_ms, bool has_command) noexcept;

  bool tripped() const noexcept;

 private:
  WatchdogParams params_;
  bool tripped_ = true;  // has_command が真になるまでは発火中扱い（要件 14.10）。
};

}  // namespace drivetrain_control
