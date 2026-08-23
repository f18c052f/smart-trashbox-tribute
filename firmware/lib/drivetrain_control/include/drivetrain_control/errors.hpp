#pragma once

// drivetrain_control config errors (design.md L0 / "Errors", 要件 15.4).
//
// 設定検証の失敗を、例外を使わずに「原因の種別」（ConfigError）・
// 「違反したパラメータ」（ConfigField）・「輪番号やテーブル位置の添字」
// （ConfigDiagnostic::index）の3点で返せる型を定義する。
//
// Invariants: 核は例外を投げない。IDF の既定設定（例外無効）で動くこと。
// L0 は標準ヘッダのみに依存する（design.md "Dependency Direction"）。

#include <cstdint>

namespace drivetrain_control {

enum class ConfigError : std::uint16_t {
  kNone = 0,
  kNotFinite,           // NaN / inf
  kNotPositive,         // <= 0 が許されない箇所
  kOutOfRange,          // 定義域外（デューティが 1 を超える等）
  kOrderingViolated,    // 復帰閾値 <= 停止閾値 等の順序違反
  kDegenerateGeometry,  // 逆運動学行列が特異に近い
  kTableNotMonotonic,   // 電圧校正テーブルが単調でない
  kTableTooShort,       // 校正点が 2 点未満
  kTableTooLong,        // 校正点が上限を超える
  kWindowTooLarge,      // 移動平均窓が上限を超える
};

enum class ConfigField : std::uint16_t {
  kNone = 0,
  // 各パラメータ構造体を定義するタスク（2.3 以降）で、パラメータへ 1:1 で
  // 対応する列挙子をここへ追加する。本タスク（2.1）ではまだどのパラメータ
  // 構造体も存在しないため、型の骨格（kNone のみ）を確定させるにとどめる。
};

struct ConfigDiagnostic {
  ConfigError code   = ConfigError::kNone;
  ConfigField field  = ConfigField::kNone;
  std::uint8_t index = 0;  // 輪ごとパラメータのときの輪番号、テーブルのときの要素位置
  constexpr bool ok() const noexcept { return code == ConfigError::kNone; }
};

}  // namespace drivetrain_control
