#pragma once

// board_pins: 端子割当の単一の正（teleop-bringup タスク 1.3、要件 2.1）。
//
// 本ヘッダは「1本の端子割当をどう表すか」（`PinRole` / `PinAssignment`）に
// 加えて、出荷する具体的な端子番号（`kShippedPinPlan`、タスク 1.5）と、
// それを役割から引く `gpioFor()` を持つ。ESP32 classic の端子特性
// （入力専用・ストラッピング・内蔵フラッシュ用・ADC1 の系統）とペリフェラル
// の本数、成立検査の述語（`checkPinPlan`）は `pin_rules.hpp` が持つ
// （タスク 1.4）。
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

// ---------------------------------------------------------------------------
// 出荷する端子割当（タスク 1.5、要件 2.1, 4.7, 6.5）。
//
// ESP32 DevKit（`platformio.ini` の `board = esp32dev`、WROOM-32・PSRAM 無し）
// を前提に、以下を避けて選んでいる（`pin_rules.hpp` の各表・研究ログ
// research.md "ESP32 の端子制約" が根拠）:
//   - ストラッピング端子 0/2/5/12/15
//   - 内蔵フラッシュ用端子 6〜11
//   - UART0（USB シリアル、書き込み・ログに使用）の 1/3
//   - 入力専用端子 34/35/36/39（エンコーダ・モータ出力のいずれも避ける。
//     エンコーダについては要件 4.7 が要求する。モータ出力は 2.3 の成立検査
//     が出力用途への割当自体を禁じるため、避けなければ端子割当が成立しない）
//
// ⚠️ **モータ出力は輪ごとに2本（kMotorPwm + kMotorDir）を占める。**
// `drivetrain_control/ports.hpp` の `MotorOutputPort::write()` は
// `WheelOutputs`（輪ごとの符号つきデューティ [-1, +1] 1個）だけを渡す契約で
// あり、その境界だけを見れば回転方向はデューティの符号で表現でき、GPIO を
// 分ける理由が無いように見える。しかし ESP32 の LEDC（PWM 生成器）はデュー
// ティの大きさしか出力できず、符号（回転方向）を運べない。受け取った
// デューティの符号を物理的なモータ回転方向へ変換するには、ドライバIC
// （2輪駆動でよく使われる TB6612FNG / DRV8833 系の H ブリッジ）の DIR 入力
// へ別の GPIO を与える必要がある。この変換はアダプタ層（タスク 3.2
// MotorLedcAdapter、本タスクの対象外）が行うが、そのために必要な GPIO は
// 端子割当の正（本ファイル）が持たねばならない。`pin_map.hpp` の
// `PinRole::kMotorDir`（タスク 1.3 で導入済み）と `pin_rules.hpp` の
// `isOutputRole` / `isGeneratorRole` の使い分け（kMotorDir は素の GPIO
// 出力であり LEDC チャネルを消費しない、というタスク 1.4 の既存コメント）
// は、この決定を前提に書かれている。本タスクはその前提のとおり両ロールへ
// 具体的な端子を与える。
inline constexpr std::uint8_t kShippedPinPlanCount = 14;
inline constexpr PinAssignment kShippedPinPlan[kShippedPinPlanCount] = {
    // 輪0
    {PinRole::kEncoderA, 0, 4},
    {PinRole::kEncoderB, 0, 13},
    {PinRole::kMotorPwm, 0, 19},
    {PinRole::kMotorDir, 0, 23},
    // 輪1
    {PinRole::kEncoderA, 1, 14},
    {PinRole::kEncoderB, 1, 16},
    {PinRole::kMotorPwm, 1, 21},
    {PinRole::kMotorDir, 1, 25},
    // 輪2
    {PinRole::kEncoderA, 2, 17},
    {PinRole::kEncoderB, 2, 18},
    {PinRole::kMotorPwm, 2, 22},
    {PinRole::kMotorDir, 2, 26},
    // 輪に紐づかない用途
    {PinRole::kBatterySense, kNoWheel, 32},  // ADC1（要件 6.5）
    {PinRole::kBenchPot, kNoWheel, 33},      // ADC1（要件 2.4 の対象外だが、
                                              // 可変抵抗の読み取りには変換器
                                              // が要るため実務上 ADC1 を選ぶ）
};

// 端子割当の集合から、用途（と輪、輪に紐づかない用途は既定で `kNoWheel`）に
// 対応する端子番号を引く。以降のアダプタ（タスク 3.1〜3.3, 7.1）は GPIO
// 番号をリテラルで持たず、本関数と `kShippedPinPlan` だけを参照すること
// （観測可能な完了状態、タスク 1.5）。該当が無ければ `kUnassigned` を返す。
template <std::uint8_t N>
constexpr std::int8_t gpioFor(const PinAssignment (&plan)[N], PinRole role,
                              std::uint8_t wheel_index = kNoWheel) noexcept {
  for (std::uint8_t i = 0; i < N; ++i) {
    if (plan[i].role == role && plan[i].wheel_index == wheel_index) {
      return plan[i].gpio;
    }
  }
  return kUnassigned;
}

}  // namespace board_pins
