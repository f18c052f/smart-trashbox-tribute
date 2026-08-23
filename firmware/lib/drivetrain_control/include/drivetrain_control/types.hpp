#pragma once

// drivetrain_control core types (design.md L1 "Types", 要件 4.1, 4.2, 4.4,
// 5.1, 5.3, 14.4).
//
// 指令・出力・状態・遮断理由の型を、指令元と処理系に依存しない形で定義する。
//
// - 整数を扱うすべての箇所で、幅が処理系によって変化しない型
//   （std::int32_t / std::int64_t / std::uint8_t / std::uint16_t 等）を
//   用いる。`int` / `long` / `size_t` はこの名前空間に一切置かない
//   （要件 4.1, 4.6）。
// - 時刻（TimeMs）は int64_t にすることで、32bit millis() の折り返しを
//   経過時間計算の考慮対象から外す（要件 4.4）。
// - 指令の型（BodyVelocityCommand / WheelVelocityCommand）に、入力機器の
//   状態・通信の詳細といった指令元に固有の情報を一切含めない（要件 5.3）。
// - 遮断理由（BlockReason）は、機体全体に効くものと輪ごとに効くものを
//   同じビット定義で表現する。輪ごとに立つのは kMotorLock のみで、それ以外
//   はすべて機体全体の理由として扱われる（利用側が種別を判別できる形。
//   design.md L1 "Types" の注記、要件 14.4）。
//
// L1 は L0（units.hpp, errors.hpp）と標準ヘッダのみに依存する（design.md
// "Dependency Direction"）。このファイルはビルド構成マクロ・ペリフェラル
// 固有の型・現在時刻を取得する手段のいずれも参照しない。
//
// Invariants: `float` は binary32 としてホストと ESP32 で同一表現である
// という前提に立つ（→ research.md）。

#include <cstdint>
#include <type_traits>

namespace drivetrain_control {

inline constexpr std::uint8_t kWheelCount = 3;

// 動的確保を避けるための容量上限。性能値でも閾値でもない（要件 15.3）。
inline constexpr std::uint8_t kMaxVoltagePoints = 16;  // 電圧校正テーブルの点数上限
inline constexpr std::uint8_t kMaxVoltageWindow = 32;  // 移動平均の窓の上限

// 単調増加ミリ秒。int64 にすることで 32bit millis() の折り返しを
// 経過時間計算の考慮対象から外す（要件 3.1, 4.1, 4.4）。
using TimeMs = std::int64_t;
using DurationMs = std::int32_t;

struct BodyVelocityCommand {  // 要件 5.1
  float vx_mm_s = 0.0f;       // 機体前方が +x
  float vy_mm_s = 0.0f;       // 機体左方が +y
  float omega_rad_s = 0.0f;   // 反時計回りが +
  TimeMs issued_at_ms = 0;    // その指令が有効になった時刻
};

struct WheelVelocityCommand {  // 要件 5.8
  float wheel_mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};  // 各輪の周速
  TimeMs issued_at_ms = 0;
};

struct CommandAcceptance {  // 要件 5.4
  bool accepted = false;    // 未設定・過去時刻なら false
  bool clamped = false;     // 有効範囲へ制限されたか
};

struct WheelOutputs {  // MotorOutputPort へ渡す値
  float duty[kWheelCount] = {0.0f, 0.0f, 0.0f};  // [-1, +1]。0 が遮断値
};

struct VoltageSample {  // 要件 11.8
  bool valid = false;
  std::int32_t milli_volts = 0;
};

struct EncoderCounts {  // 要件 7.5。折り返しを含まない累積値
  std::int64_t count[kWheelCount] = {0, 0, 0};
};

struct Pose2D {
  float x_mm = 0.0f;
  float y_mm = 0.0f;
  float theta_rad = 0.0f;  // (-π, +π]
};

struct BodyVelocity {
  float vx_mm_s = 0.0f;
  float vy_mm_s = 0.0f;
  float omega_rad_s = 0.0f;
};

// 遮断理由。機体全体に効くものと輪ごとに効くものを同じビット定義で表現する。
// 輪ごとに立つのは kMotorLock のみ（それ以外は機体全体の理由。→ Out of
// Boundary、design.md ProtectionSupervisor が種別ごとに振り分ける）。
enum class BlockReason : std::uint16_t {
  kNone = 0,
  kNotConfigured = 1u << 0,       // 要件 15.4
  kOutputDisabled = 1u << 1,      // 要件 14.9
  kNoCommandYet = 1u << 2,        // 要件 14.10
  kCommandTimeout = 1u << 3,      // 保護④
  kLowVoltage = 1u << 4,          // 保護②
  kVoltageUnavailable = 1u << 5,  // 要件 11.8
  kMotorLock = 1u << 6,           // 保護①（輪ごと）
};
using BlockMask = std::uint16_t;

struct StepResult {
  WheelOutputs outputs{};
  BlockMask global_reasons = 0;
  BlockMask wheel_reasons[kWheelCount] = {0, 0, 0};
  bool outputs_written = false;
};

// ---------------------------------------------------------------------------
// コンパイル時表明: 整数幅と各型のサイズを処理系に依らず固定する（要件 4.1,
// 4.4, 4.6）。ヘッダを取り込むだけでホスト・組込みの両方の翻訳単位で検査
// される。
// ---------------------------------------------------------------------------

static_assert(std::is_same_v<TimeMs, std::int64_t>,
              "TimeMs must be a fixed-width int64_t (wrap-safe elapsed-time math)");
static_assert(sizeof(TimeMs) == 8, "TimeMs must be 8 bytes on every target");

static_assert(std::is_same_v<DurationMs, std::int32_t>,
              "DurationMs must be a fixed-width int32_t");
static_assert(sizeof(DurationMs) == 4, "DurationMs must be 4 bytes on every target");

static_assert(std::is_same_v<decltype(kWheelCount), const std::uint8_t>,
              "kWheelCount must be a fixed-width uint8_t");

static_assert(std::is_same_v<std::underlying_type_t<BlockReason>, std::uint16_t>,
              "BlockReason must have a fixed-width uint16_t underlying type");
static_assert(std::is_same_v<BlockMask, std::uint16_t>,
              "BlockMask must be a fixed-width uint16_t");

// BodyVelocityCommand / WheelVelocityCommand は float(align 4) と
// TimeMs=int64_t を混在させる。int64_t のアラインメント要求（4 か 8 か）は
// 一般には ABI 依存だが、この Spec のビルド行列（ホスト g++ の x86-64
// System V ABI と、3構成が実際に使う xtensa-esp32-elf-g++）の両方で実測
// した結果、alignof(int64_t) == 8 で一致し、混在構造体は3つの float
// （12B）の後ろに4Bのパディングが入って合計24Bになることを両ツールチェーン
// で確認済み（研究記録: ホスト g++ 実測、および xtensa-esp32-elf-g++
// 14.2.0 での -fsyntax-only 実測）。したがってサイズそのものも固定値表明
// する。加えて、時刻フィールドの型が処理系依存幅へすり替わっていないこと
// をフィールド単位でも固定する（サイズ一致だけでは「たまたま同じ合計」を
// 見逃す可能性があるため、二重に検査する）。
static_assert(sizeof(BodyVelocityCommand) == 24,
              "BodyVelocityCommand: 3x float (12B) + 4B alignment padding + TimeMs (8B) "
              "== 24B, verified identical on host x86-64 System V and xtensa-esp32-elf");
static_assert(sizeof(WheelVelocityCommand) == 24,
              "WheelVelocityCommand: 3x float (12B) + 4B alignment padding + TimeMs (8B) "
              "== 24B, verified identical on host x86-64 System V and xtensa-esp32-elf");
static_assert(std::is_same_v<decltype(BodyVelocityCommand::issued_at_ms), TimeMs>,
              "BodyVelocityCommand::issued_at_ms must stay TimeMs (int64_t)");
static_assert(std::is_same_v<decltype(WheelVelocityCommand::issued_at_ms), TimeMs>,
              "WheelVelocityCommand::issued_at_ms must stay TimeMs (int64_t)");
static_assert(std::is_same_v<std::remove_extent_t<decltype(WheelVelocityCommand::wheel_mm_s)>, float>,
              "WheelVelocityCommand::wheel_mm_s must stay float[]");

// 以下は int64_t を含まない（= ABI 依存のアラインメント選択に左右されない）
// 構造体で、パディング込みの総サイズがホスト・組込みの双方で一意に定まる
// ため、サイズそのものを固定値表明する。
static_assert(sizeof(CommandAcceptance) == 2, "CommandAcceptance: 2x bool");
static_assert(sizeof(WheelOutputs) == 12, "WheelOutputs: 3x float");
static_assert(sizeof(VoltageSample) == 8, "VoltageSample: bool (+pad) + int32_t");
static_assert(sizeof(Pose2D) == 12, "Pose2D: 3x float");
static_assert(sizeof(BodyVelocity) == 12, "BodyVelocity: 3x float");
static_assert(sizeof(StepResult) == 24,
              "StepResult: WheelOutputs (12B) + BlockMask (2B) + wheel_reasons[3] (6B) + bool (1B), "
              "padded to 4-byte alignment (no int64_t member => padding is ABI-independent)");

// EncoderCounts は int64_t[kWheelCount] のみを持つ均質配列であり、要素間
// パディングが発生し得ないため（配列要素は互いに sizeof(T) 間隔で並ぶ）、
// アラインメント要求が 4/8 のどちらの ABI でも総サイズは要素数 x 要素幅に
// 一致する。
static_assert(sizeof(EncoderCounts) == kWheelCount * sizeof(std::int64_t),
              "EncoderCounts: kWheelCount x int64_t, no interior padding in a homogeneous array");
static_assert(std::is_same_v<std::remove_extent_t<decltype(EncoderCounts::count)>, std::int64_t>,
              "EncoderCounts::count must stay int64_t[]");

static_assert(std::is_same_v<decltype(VoltageSample::milli_volts), std::int32_t>,
              "VoltageSample::milli_volts must stay a fixed-width int32_t");

// 指令の型に指令元固有の情報を持たせない（要件 5.3）ことの一部を、
// 「並進2成分・回転1成分・時刻」以外のメンバを持ち得ないサイズ固定で裏取り
// する。これらを継続的に満たすためのコンパイル時の見張りである。

}  // namespace drivetrain_control
