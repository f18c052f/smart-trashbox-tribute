#pragma once

// drivetrain_control peripheral ports (design.md L3 "Ports", 要件 2.1, 2.3,
// 2.5, 2.6, 7.5, 17.5).
//
// エンコーダ読み出し・出力書き出し・電圧読み出しの3つを抽象として宣言する。
// ⚠️ 実装を1つも持たない（要件 2.1）。実装は teleop-bringup が所有する。
//
// 契約は物理量だけを往復させる。折り返しを含まない64bit累積カウント・
// [-1, +1] へ正規化されたデューティ・ミリボルトと妥当性フラグを扱い、
// ペリフェラル固有の型・分解能・イベント形式を露出させない（要件 2.3）。
// PCNT ユニット番号・LEDC 分解能・ADC 生値は、この境界の向こう側に留まる。
//
// ⚠️ 判断・計算を伴う処理をポートの実装側へ委ねない（要件 2.5）。
//   - 折り返しの桁上げ（生カウント → 64bit 累積カウント）は
//     WrapAccumulator（drivetrain_control/wrap_accumulator.hpp、L4）が核
//     として提供する。EncoderPort::read() の実装はこれを利用すること。
//   - 生値からバッテリ電圧（mV）への換算（分圧比・非線形補正）は
//     VoltageScaler（drivetrain_control/voltage_scaler.hpp、L4）が核とし
//     て提供する。BatteryVoltagePort::read() の実装はこれを利用すること。
// これらの算術をアダプタ側へ沈めると、最も壊れやすい計算がホストで検証
// できなくなる（A-2 / A-11）。判断・計算はあくまで核が提供する契約であり、
// アダプタへ委ねない。
//
// 無線・コントローラ・通信に関するポートは持たない（要件 2.6）。指令は
// 入口（command_input）から「押し込まれる」ものであり、核が取りに行くもの
// ではない。
//
// L3 は L0（units.hpp, errors.hpp）と L1（types.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このヘッダは <cstdint> と
// types.hpp 以外を include しない（要件 2.2 の裏取り。標準の固定幅整数
// ヘッダとコア型ヘッダ以外を参照せずにホスト・組込みの両方でコンパイル
// できることが、このタスクの観測可能な完了状態である）。

#include <cstdint>

#include "drivetrain_control/types.hpp"

namespace drivetrain_control {

class EncoderPort {
 public:
  virtual ~EncoderPort() = default;
  // 折り返しを含まない累積カウントを返す（要件 7.5）。
  // 実装は WrapAccumulator を用いてハードウェアカウンタの桁上げを累積する
  // こと（判断・計算をここへ持ち込まない、要件 2.5）。
  virtual EncoderCounts read() = 0;
};

class MotorOutputPort {
 public:
  virtual ~MotorOutputPort() = default;
  // 受け取った値をそのまま書き出す。判断を持たない（要件 2.5, 14.2）。
  // duty[i] == 0.0f が遮断値。0 を coast にするか brake にするかはアダプタ
  // の責務。
  virtual void write(const WheelOutputs& outputs) = 0;
};

class BatteryVoltagePort {
 public:
  virtual ~BatteryVoltagePort() = default;
  // バッテリ電圧（mV）。読み値が得られない場合は valid == false を返す
  // （要件 11.8）。実装は VoltageScaler を用いて生値から換算すること
  // （判断・計算をここへ持ち込まない、要件 2.5）。
  virtual VoltageSample read() = 0;
};

// Preconditions: configure() に渡す Ports の3つはすべて非 null であること。
// null は設定エラーとして拒否する（core 側、L10 controller の責務）。
// Postconditions: read() は副作用として核の状態を変えない。
struct Ports {
  EncoderPort*        encoder = nullptr;
  MotorOutputPort*    motor   = nullptr;
  BatteryVoltagePort* battery = nullptr;
};

}  // namespace drivetrain_control
