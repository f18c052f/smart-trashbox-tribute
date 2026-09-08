#pragma once

// board_pins: 端子割当の単一の正（teleop-bringup タスク 1.3、要件 2.1）。
//
// 本ヘッダは「1本の端子割当をどう表すか」だけを定める。ESP32 classic の
// 端子特性（入力専用・ストラッピング・内蔵フラッシュ用・ADC1 の系統）と
// ペリフェラルの本数は `pin_rules.hpp` が持ち、成立検査の述語も同ヘッダが
// 後続タスク（1.4）で追加する。出荷する具体的な端子番号はタスク 1.5 が
// 本ヘッダへ追加する。
//
// ⚠️ **ペリフェラルの API を一切参照しない。** `driver/*.h` / `esp_adc/*.h`
// を include しない。これによりホスト（`[env:native]`）と実機
// （`[env:teleop]`）の双方で同一のソースがコンパイルできる。
// この制約が本コンポーネントの存在理由そのものである: `firmware/src/` は
// `test_build_src` 既定 `no` によりホストビルドに含まれないため、端子割当を
// アプリ層へ置くと要件 2.7（成立検査を実機へ書き込むことなく実行できること）
// が原理的に満たせない（design.md "Existing Architecture Analysis" /
// research.md "Decision: 端子割当の正をアプリ層に置き、核へ持ち込まない"）。
//
// ⚠️ 幅が処理系によって変化しない型のみを用いる（`int` / `long` / `size_t`
// をこの名前空間へ置かない）。ホストと ESP32 で整数幅が一致することを
// 前提に置かないための規約であり、`drivetrain_control/types.hpp` の流儀に
// 揃えている。
//
// ⚠️ 本コンポーネントは `drivetrain_control` に依存しない（design.md
// board_pins の Dependencies "Outbound: なし"）。アダプタが端子番号を得る
// ためだけに核を引き込むことを避ける。

#include <cstdint>

namespace board_pins {

// 端子の用途。design.md board_pins の Service Interface が列挙する6用途で
// あり、要件 2.1 が「単一の箇所で定める」と述べる対象（3輪ぶんのエンコーダ
// 入力・モータ出力・バッテリ電圧監視）に、机上確認用（要件 10.6 / タスク
// 7.1 の BenchApp）の可変抵抗を加えたものである。
enum class PinRole : std::uint8_t {
  kEncoderA = 0,      // エンコーダA相入力（輪ごと）
  kEncoderB = 1,      // エンコーダB相入力（輪ごと）
  kMotorPwm = 2,      // モータ出力の大きさ（輪ごと）
  kMotorDir = 3,      // モータ出力の向き（輪ごと）
  kBatterySense = 4,  // バッテリ電圧監視（輪に紐づかない）
  kBenchPot = 5,      // 机上確認用の可変抵抗（輪に紐づかない）
};

// 輪に紐づかない用途を表す `wheel_index`。実在の輪の添字（0 始まり）と
// 衝突しない値として、`std::uint8_t` の最大値を用いる。
inline constexpr std::uint8_t kNoWheel = 0xFFu;

// 端子が割り当てられていないことを表す `gpio`。ESP32 の端子番号は 0 以上で
// あるから、負値であれば実在の端子と衝突しない。`gpio` を符号付きにして
// いるのはこの番兵のためである。
inline constexpr std::int8_t kUnassigned = -1;

// 1本の端子割当。「どの用途が」「どの輪の」「どの端子を使うか」の3点のみを
// 持ち、ペリフェラルの設定（PCNT ユニット番号・LEDC 分解能・ADC の減衰）は
// 一切含まない。それらは `drivetrain_control/ports.hpp` が定めた境界の
// 向こう側（アダプタ）に留まる。
//
// `role` に「未設定」を表す値を置いていないのは意図的である。割当は常に
// 何らかの用途を名指しており、端子が定まっているかどうかは
// `gpio == kUnassigned` で表現する。
struct PinAssignment {
  PinRole role = PinRole::kEncoderA;
  std::uint8_t wheel_index = kNoWheel;
  std::int8_t gpio = kUnassigned;
};

}  // namespace board_pins
