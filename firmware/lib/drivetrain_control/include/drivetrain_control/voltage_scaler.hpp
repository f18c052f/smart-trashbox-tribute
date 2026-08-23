#pragma once

// drivetrain_control VoltageScaler (design.md L4 "VoltageScaler", 要件
// 11.7, 15.6).
//
// ADC 生値からバッテリ電圧（mV）への換算を、校正作業の出力形（複数点で
// 実測した「生値と実電圧の対」）そのままのパラメータで行う区分線形の純
// ロジックである。分圧比・非線形補正のいずれも実装内に固定値として持たない
// （要件 15.6）。`docs/drivetrain-spec.md` §9.1 の 27/(100+27) はテーブルの
// 初期作成の根拠であって、コード内の定数ではない。
//
// アルゴリズム: `VoltageScalerParams::table[0..point_count)` は
// `validate()`（config.hpp）が既に「点数 2 以上・`raw` が狭義単調増加」を
// 保証済みである（本クラスはこれを事前条件として受け取り、再検証しない。
// 二重実装を避けるため）。`toMilliVolts(raw)` は:
//   - `raw` がテーブル範囲内なら、`raw` を挟む2点間を線形補間する
//   - `raw` がテーブル先頭点より小さければ、先頭区間（点0→点1）の傾きで
//     外挿する
//   - `raw` がテーブル末尾点より大きければ、末尾区間の傾きで外挿する
//     （クランプではない）
// 点が2つだけのときは、上記のテーブル内補間そのものが分圧比のみの線形換算
// と一致する（別の分岐は不要。design.md "VoltageScaler" Postconditions）。
//
// 整数演算: `raw` / `milli_volts` はいずれも int32_t。補間・外挿はすべて
// int64_t の整数演算で行い、丸めは四捨五入（0 からの絶対値が大きくなる側）
// で確定する。これによりテーブル点ちょうどの上では常に厳密一致する
// （分母が割り切れるため丸め自体が発生しない）。float/double を用いない
// ことで、ホストと ESP32 の丸め挙動が処理系に依らず一致する。
//
// L4 は L0（units.hpp, errors.hpp）–L2（config.hpp）にのみ依存する
// （design.md "Dependency Direction"）。このファイルはビルド構成マクロ・
// ペリフェラル固有の型・現在時刻を取得する手段のいずれも参照しない。

#include <cstdint>

#include "drivetrain_control/config.hpp"

namespace drivetrain_control {

class VoltageScaler {
 public:
  // Preconditions: params は config.hpp の validate() を通過済みであること
  // （点数 2 以上・table[0..point_count) の raw が狭義単調増加）。本
  // コンストラクタはこれを再検証しない（config.cpp の責務との二重実装を
  // 避けるため）。
  explicit VoltageScaler(const VoltageScalerParams& params) noexcept;

  // 単調増加を検証済みの区分線形テーブルで raw を mV へ換算する。
  // テーブル範囲外は両端の区間の傾きで外挿する（クランプではない）。
  // Postconditions: テーブル点の raw と厳密に一致する入力では、対応する
  // milli_volts と厳密に一致する値を返す。
  std::int32_t toMilliVolts(std::int32_t raw) const noexcept;

 private:
  VoltageScalerParams params_;
};

}  // namespace drivetrain_control
