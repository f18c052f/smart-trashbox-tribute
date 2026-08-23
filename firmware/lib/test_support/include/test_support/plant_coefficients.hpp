#pragma once

// test_support::PlantCoefficients (drivetrain-core task 7.1, 要件 16.2,
// 16.3, 16.4, 16.6).
//
// ⚠️⚠️⚠️ 仮値の宣言（このコメントを消さないこと） ⚠️⚠️⚠️
//
// ここに置く係数は、すべて「合否条件ではない仮値」である。実測値ではなく、
// ここで得た数値を実機性能の主張に使ってはならない（要件 16.4, 16.6）。
//
// この struct は WheelPlant（test_support/wheel_plant.hpp）が閉ループ検証
// （task 7.2、要件 9.7, 16.1, 16.8）を実行するためだけに存在する「テストを
// 動かすための架空の値」であり、実機の車輪応答特性を近似する意図も校正の
// 意図も一切持たない。閉ループテストが合格・不合格を判定する基準は常に
// 「向き」（定常偏差が縮む方向へ動くか）「発火の有無」「遮断の成立」であり、
// この係数がもたらす具体的な収束時間やゲイン値そのものを合否条件にしない
// （design.md "WheelPlant / FakePorts" Implementation Notes、要件 16.4,
// 16.6）。
//
// このファイルは lib/test_support/ に置かれ、本体（lib/drivetrain_control/）
// のコードには一切含まれない。lib/test_support/ には CMakeLists.txt が
// 存在せず、firmware/CMakeLists.txt の EXTRA_COMPONENT_DIRS にも含まれない
// ため、ファームウェア成果物（teleop / production のどちらのビルドにも）へ
// リンクされる経路が存在しない（要件 16.3）。
//
// ⚠️ この宣言を消したり、係数を「実測値」「確定値」などとして引用したり
// しないこと。値そのものが変わっても、この宣言は必ずここに残す。
// （firmware/test/native/test_wheel_plant/ の
// test_plant_model_is_provisional_declaration_exists_in_plant_coefficients_header
// が本ファイルの内容を直接読み込み、この宣言の文言が存在することを回帰
// 検証している。文言を書き換える場合はそのテストも同時に更新すること。）

namespace test_support {

// ⚠️ ここに置く係数はすべて「合否条件ではない仮値」である（要件 16.4）。
//    実測値ではなく、ここで得た数値を実機性能の主張に使わない（要件 16.6）。
struct PlantCoefficients {
  float duty_to_steady_mm_s;  // 仮値。duty=1.0, supply_ratio=1.0 のときの定常車輪周速 [mm/s]
  float time_constant_ms;     // 仮値。1次遅れの時定数 [ms]
};

}  // namespace test_support
