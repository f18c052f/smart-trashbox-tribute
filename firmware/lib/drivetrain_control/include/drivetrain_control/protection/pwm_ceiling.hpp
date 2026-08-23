#pragma once

// drivetrain_control PwmCeiling (design.md L7-L8 保護層 "PwmCeiling", 要件
// 12.1, 12.2, 12.3, 12.5, 12.6).
//
// 保護③: 計測されたバッテリ電圧に応じて出力デューティの上限を決定する、
// 状態を持たない純関数オブジェクト。design.md "PwmCeiling" Invariants の
// とおり、①②④のような「継続時間を伴う成立／解除」を持たないため
// BlockReason のビットを持たない。
//
// ⚠️ 所有権（design review で訂正済み。design.md "PwmCeiling"
// Implementation Notes）: このインスタンスは ProtectionSupervisor では
// なく DrivetrainController が直接保持し、evaluate() を直接呼ぶ。
// 上限値は VelocityPid::compute() の直後・3輪の等比縮小に必要であり、
// ProtectionSupervisor::compose()（PID の commit より後に呼ばれる）へ
// 含めると呼び出し順序が破綻するため（design.md System Flows「フローの
// 決定事項」）。本クラス自身はこの呼び出し元を知らない（知る必要もない）。
//
// evaluate() の優先順位（design.md Postconditions を基に確定）:
//   1. enabled == false             -> 常に absolute_max_duty（要件 12.5 無効化）
//   2. sample.valid == false        -> fallback_duty（要件 12.6 欠測時の安全側既定上限）
//   3. override_fn != nullptr       -> override_fn(...) を [0, absolute_max_duty] へクランプ（要件 12.5 差し替え）
//   4. 既定の式                      -> min(absolute_max_duty, reference_milli_volts / measured_milli_volts)
//
// ⚠️ 優先順位 1 と 2 の間の設計判断（design.md に明記されていない補完）:
// design.md の Postconditions は enabled / override_fn / 既定式の3項目を
// 列挙するのみで、「enabled == false のときに欠測サンプルが来たらどちらを
// 優先するか」を明記していない。要件 12.5（無効化）は「常に
// absolute_max_duty」であり条件が付いていないため、無効化を最優先にする
// （enabled == false は欠測の有無に関わらず常に absolute_max_duty を返す）。
// この優先順位はテストで固定する。
//
// 既定式の単位（設計判断。design.md 本文の再確認）: `reference_milli_volts
// / measured_milli_volts` は mV 同士の比であり無次元。design.md が「相当
// する」と参照する元の式（`docs/drivetrain-spec.md` §9.3）は
// `PWM_MAX = min(1.0, 12.0 / measured_battery_voltage)` であり、[0,1] へ
// 正規化されたデューティ空間ではこの比そのものがデューティ上限として直接
// 使われる（別の係数を掛けない）。本実装もこの比をそのまま duty として
// 扱う。
//
// 既定式のゼロ割り／非物理値ガード（design.md に明記されていない補完）:
// design.md の Postconditions は sample.valid のみを欠測判定の条件として
// 挙げているが、valid == true かつ measured_milli_volts <= 0 は電圧セン
// サが物理的にあり得ない値を返した場合であり、そのまま除算すると 0 除算
// （未定義動作）や負のデューティを生みかねない。この状態は「実質的に
// 読み値が得られていない」ものとして扱い、valid == false と同様に
// fallback_duty を返す。
//
// L7 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include "drivetrain_control/config.hpp"
#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

class PwmCeiling {
 public:
  // absolute_max_duty: 出力段の最大デューティ（DrivetrainConfig::output の
  // 値。設定検証で <= 1.0 が保証される）。evaluate() の戻り値はこれを
  // 決して超えない（要件 12.3）。
  PwmCeiling(const PwmCeilingParams& params, float absolute_max_duty) noexcept;

  // sample: バッテリ電圧ポートから得た1サンプル（要件 12.6: valid ==
  //         false のときは fallback_duty を返す）。
  // 戻り値: [0, absolute_max_duty] に収まる出力デューティ上限。状態を
  //         持たない純関数であり、呼び出し順序や過去の呼び出しに依存
  //         しない。
  float evaluate(const VoltageSample& sample) const noexcept;

 private:
  PwmCeilingParams params_;
  float absolute_max_duty_;
};

}  // namespace drivetrain_control
