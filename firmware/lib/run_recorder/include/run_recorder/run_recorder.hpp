#pragma once

// run_recorder: 走行1回を1レコードとする記録の器
// (teleop-bringup タスク 6.1、要件 15.1, 15.2, 15.3, 15.4, 15.5)。
//
// design.md "Components and Interfaces" の RunRecorder 行は
// Key Dependencies を「なし」と定める。したがって本コンポーネントは
// `drivetrain_control` を含むどのライブラリにも依存しない
// ―― `TimeMs` / 輪数 / 遮断理由ビットマスクはこの名前空間の中に
// 独立して（値だけ揃えて）再定義する。これにより:
//   - 走行記録の形式が固定側の投擲記録（`prediction_core.record.ThrowRecord`,
//     Python/JSON, フィールド: record_id/source/config/samples/predictions,
//     samples 要素は t_ms/x_mm/y_mm/z_mm）とも、`drivetrain_control` の
//     内部型とも構造的に独立になり、要件 15.5（別形式として扱う）が
//     単なる運用上の約束ではなく型の独立性として成り立つ
//   - task 6.2 が `DrivetrainController::status()` / `StepResult` から
//     `RunSample` を組み立てる変換（グルーコード）を持つことになるが、
//     それは意図的にアプリ層（`TeleopApp`）側の責務であり、本コンポーネント
//     には持ち込まない
//
// ⚠️ **走行中は外部へ送出しない。** `tech.md` 開発標準5
// （「計測が計測対象を歪めないこと」）: ESP32 は走行中 RAM にバッファし、
// 走行後に吸い出す。走行中の無線送信は制御ループにジッタを乗せる。
// 本コンポーネントは `append()` 以外のいかなる I/O も行わない
// （ペリフェラル API を一切参照しない。ホスト・実機の両方でコンパイルできる
// ―― `board_pins` / `teleop_input` と同じ理由で `firmware/lib/` に置く。
// design.md Architecture Pattern & Boundary Map の図は RunRecorder を
// DeviceOnly 側に描くが、これは純データ・純ロジックのみで構成され
// ペリフェラルへ一切触れない点で `board_pins` / `teleop_input` と同種であり
// （design.md "ホスト検証境界": 「純データ・純関数だけを firmware/lib/ へ
// 切り出す」）、`ControllerLink` やアダプタ群（Bluepad32・PCNT・LEDC・ADC
// を直接叩く）とは性質が異なる。ホストで検証できる余地をわざわざ
// `firmware/src/` へ置いて捨てる理由が無いため、`firmware/lib/` へ配置する
// ことをこのタスクの実装判断とする）。
//
// ⚠️ **上限到達時に黙って欠落させない**（タスク本文の要求）。本実装は
// 「最も古いサンプルを上書きする」リングバッファを選ぶ:
//   - 安全機能の発火試験（要件14）はデッドマン解放・接続断・ロック・低電圧の
//     いずれも走行の途中〜終盤に起きることが多く、「直近Nサンプルを必ず
//     保持する」ことが「肝心の瞬間が録れていない」という失敗を避ける上で
//     最も安全側である
//   - 走行の先頭を切り捨てて次の追記を拒否する方式（reject-tail）だと、
//     容量に達した後に発火試験の事象が起きた場合、その瞬間が一切
//     記録に残らない恐れがある
//   - 上書きが起きたことは `overflowed()` で、および `extractCsv()` が
//     出力するメタ行で、常に観測可能にする（黙って欠落させない）
//
// ⚠️ **容量 `kMaxCapacity` は未実測の仮値。** 制御ループ周期は
// `docs/open-questions.md` OQ-22 で「目安 100〜200 Hz 程度」としか
// 決まっておらず、ESP32 classic の実際に使える静的 RAM 予算（Bluepad32 /
// BTstack 常駐分を差し引いた残り）も本タスク時点では実測していない。
// 3000 件 × 概算 44〜48 バイト/件 ≈ 130〜145 KiB を静的に確保する値を
// 仮に選んだ（100 Hz で約30秒、200 Hz で約15秒ぶんに相当）。E-0〜E-4 /
// M2a のブリングアップで実際の RAM 使用量と走行時間が判明した時点で
// 見直すこと（タスク 3.2 の PWM 周波数・分解能と同種の「実測前の仮値」）。

#include <cstddef>
#include <cstdint>
#include <cstdio>

namespace run_recorder {

// 単調増加ミリ秒。`drivetrain_control::TimeMs` と同じ型・意味だが、
// このコンポーネントを依存フリーに保つため独立して定義する（ファイル冒頭の
// コメント参照）。
using TimeMs = std::int64_t;

// 3輪ぶん。`drivetrain_control::kWheelCount` と同じ値だが、同じ理由で
// 独立した定数として持つ。
inline constexpr std::uint8_t kWheelCount = 3;

// 1つのレコードが保持できるサンプル数の静的上限（ファイル冒頭コメント
// 参照）。動的確保を避けるための容量上限であり、性能値でも閾値でもない
// （`drivetrain_control/types.hpp` の `kMaxVoltagePoints` 等と同じ考え方）。
inline constexpr std::size_t kMaxCapacity = 3000;

// 1周期ぶんのサンプル（要件 15.3: 指令値・各輪の実測速度・バッテリ電圧・
// 保護の発火状態・出力許可の状態を時刻とともに含める）。
//
// 保護の発火状態は `drivetrain_control::BlockMask`（`std::uint16_t` の
// ビットマスク）と同じ幅・同じビット位置の意味で持つが、型そのものは
// 依存を作らないため `std::uint16_t` のまま素で持つ。呼び出し側
// （task 6.2 の `TeleopApp`）が `StepResult::global_reasons` /
// `wheel_reasons` をそのまま代入できる（値としてビット互換）。
struct RunSample {
  TimeMs t_ms = 0;

  // 指令値: 各輪の目標速度（mm/s）。`DrivetrainStatus::wheel_target_mm_s`
  // に相当する量（速度PID経由・開ループ経由のどちらの制御経路でも、
  // 実際に核へ渡った指令をこの粒度で記録する）。
  float command_wheel_mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};

  // 各輪の実測速度（mm/s）。`DrivetrainStatus::wheel_measured_mm_s` に相当。
  float measured_wheel_mm_s[kWheelCount] = {0.0f, 0.0f, 0.0f};

  // バッテリ電圧。
  std::int32_t battery_milli_volts = 0;
  bool battery_valid = false;

  // 保護の発火状態。機体全体（`global_reasons`）と輪ごと（`wheel_reasons`、
  // ロック保護のみ輪ごとに立つ）の両方を持つ。
  std::uint16_t global_protection_reasons = 0;
  std::uint16_t wheel_protection_reasons[kWheelCount] = {0, 0, 0};

  // 出力許可の状態。
  bool output_enabled = false;
};

// 走行1回＝1レコードの記録の器（要件 15.1）。
//
// 状態遷移: `beginRun()` で新しい記録を開始し（15.2）、`isRunning()` が
// 真の間だけ `append()` が実際にサンプルを溜める。`endRun()` で走行を
// 終える。以降 `append()` を呼んでも無視される（走行の外で追記させない）。
// `beginRun()` を再度呼ぶと、直前の記録内容（上限到達フラグを含む）は
// 破棄され新しい記録が始まる ―― 呼び出し側は次の走行を始める前に
// 前回の記録を取り出しておくこと（要件 15.1「走行1回を1つの記録の単位」）。
//
// ⚠️ **サイズに注意: `sizeof(RunRecorder)` は `kMaxCapacity` 分の
// `RunSample` を常に内包する**（`capacity_` は実行時の上限であり、
// バッキング配列自体は常に `kMaxCapacity` ぶん確保される）。ESP32 の
// FreeRTOS タスクスタック（既定で数〜十数 KiB）へスタック変数として置くと
// スタックオーバーフローする（試算 ~130-145 KiB、ファイル冒頭コメント
// 参照）。**`TeleopApp`（task 6.2）はこれをグローバル/`static` として保持
// するか、ヒープ上に確保すること。** ホストテストがスタック変数として
// 構築しているのは、デスクトップのスタックサイズ（既定で数百 KiB〜数 MiB）
// では問題にならないためであり、埋め込み側の配置指針として真似ないこと。
class RunRecorder {
 public:
  // capacity: このレコーダが実際に使う容量（[1, kMaxCapacity] の範囲へ
  // クランプされる）。既定は `kMaxCapacity`。ホストテストで上限到達を
  // 小さい容量で再現するために、実行時に選べる形にしてある。
  explicit RunRecorder(std::size_t capacity = kMaxCapacity) noexcept;

  // 走行の開始（要件 15.2）。記録を初期状態へ戻し、以降の `append()` を
  // 受け付ける状態にする。
  void beginRun(TimeMs started_at_ms) noexcept;

  // 走行の終了。以降の `append()` は無視される。
  void endRun() noexcept;

  bool isRunning() const noexcept { return running_; }
  TimeMs startedAtMs() const noexcept { return started_at_ms_; }

  // 1周期ぶんのサンプルを追記する（要件 15.1, 15.3）。`isRunning()` が
  // 偽の間は無視する（走行の外で溜めない）。容量に達している場合は
  // 最も古いサンプルを上書きする（ファイル冒頭コメントの上限到達方針）。
  void append(const RunSample& sample) noexcept;

  // 上限に達し、最も古いサンプルが上書きされたか。黙って欠落させないため
  // の観測手段（タスク本文の要求）。
  bool overflowed() const noexcept { return overflowed_; }

  // これまでに `append()` が実際に受理された総回数（上書きで失われた分も
  // 含む。`isRunning()` が偽の間の呼び出しは含まない）。
  std::uint32_t totalAppended() const noexcept { return total_appended_; }

  // 現在保持しているサンプル数（`capacity()` 以下）。
  std::size_t size() const noexcept { return count_; }
  std::size_t capacity() const noexcept { return capacity_; }

  // 記録順（最古→最新）で1件読む。`index >= size()` のときは既定値の
  // `RunSample{}` を返す（embedded では例外を使わないため、境界外を
  // 未定義動作にしない安全側の選択）。
  const RunSample& sampleAt(std::size_t index) const noexcept;

  // 記録順（最古→最新）で全件へコールバックする。
  // `Fn` は `void(const RunSample&)` 相当の呼び出し可能オブジェクト。
  template <typename Fn>
  void forEachSample(Fn&& fn) const {
    for (std::size_t i = 0; i < count_; ++i) {
      fn(sampleAt(i));
    }
  }

  // 記録を機体の外へ取り出す手段（要件 15.4）。
  //
  // ⚠️ 実際の転送経路（UART送出・Wi-Fi送信・ファイル書き込み等）は本
  // コンポーネントの責務外 ―― それはペリフェラル/無線 API を要し、本
  // コンポーネントを `firmware/lib/`（ホスト検証可能・ペリフェラル API
  // 不参照）に置いた前提を壊す。本コンポーネントが提供するのは「機体内の
  // RAM 上の記録を、任意の転送経路へ渡せる形（1行ずつのテキスト）へ
  // 直列化する」ところまでであり、実際に機体の外へ送り出す最後の一手は
  // 呼び出し側（task 6.2 以降のアプリ層、または専用の吸い出し経路）が
  // 担う。
  //
  // `sink` は `void(const char* line, std::size_t length)` 相当の呼び出し
  // 可能オブジェクト。各行は改行 `'\n'` で終わる。1行目は上限到達の有無を
  // 示すメタ行（`#` で始まるコメント行。「上限到達時の挙動が記録から
  // 判別できる」というこのタスクの観測可能な完了状態そのもの）、2行目は
  // CSV ヘッダ、以降が記録順（最古→最新）のサンプル1件につき1行。
  //
  // ⚠️ **事前条件（呼び出し側の責務）: 走行後（`endRun()` の後）に呼ぶこと。**
  // `extractCsv()` 自体は純粋にメモリ上のデータをテキストへ変換するだけで
  // I/O を一切行わないため、`isRunning()` の間に呼んでもこの関数自体が
  // 制御ループへジッタを乗せることはない。しかし `sink` が実際に UART 送出
  // 等の I/O を行う実装であれば、それを走行中に呼ぶことは `tech.md` 開発
  // 標準5（「計測が計測対象を歪めないこと」「走行中は RAM にバッファし、
  // 走行後に吸い出す」）に反する。呼び出し側（task 6.2 以降）が走行後にのみ
  // 呼ぶ規律を守ること。
  template <typename LineSink>
  void extractCsv(LineSink&& sink) const {
    // 3行それぞれの最大想定長（メタ行・CSVヘッダ行・データ行のうち最長は
    // CSVヘッダ行の168バイト）に余裕を持たせたサイズ。
    char buf[224];

    int n = std::snprintf(buf, sizeof(buf),
                           "# run_recorder overflowed=%d total_appended=%lu "
                           "size=%lu capacity=%lu\n",
                           overflowed_ ? 1 : 0,
                           static_cast<unsigned long>(total_appended_),
                           static_cast<unsigned long>(count_),
                           static_cast<unsigned long>(capacity_));
    sink(buf, sizeToWritten(n, sizeof(buf)));

    n = std::snprintf(
        buf, sizeof(buf),
        "t_ms,cmd0_mm_s,cmd1_mm_s,cmd2_mm_s,meas0_mm_s,meas1_mm_s,"
        "meas2_mm_s,battery_mv,battery_valid,global_reasons,"
        "wheel0_reasons,wheel1_reasons,wheel2_reasons,output_enabled\n");
    sink(buf, sizeToWritten(n, sizeof(buf)));

    for (std::size_t i = 0; i < count_; ++i) {
      const RunSample& s = sampleAt(i);
      n = std::snprintf(
          buf, sizeof(buf),
          "%lld,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%ld,%d,%u,%u,%u,%u,%d\n",
          static_cast<long long>(s.t_ms), static_cast<double>(s.command_wheel_mm_s[0]),
          static_cast<double>(s.command_wheel_mm_s[1]),
          static_cast<double>(s.command_wheel_mm_s[2]),
          static_cast<double>(s.measured_wheel_mm_s[0]),
          static_cast<double>(s.measured_wheel_mm_s[1]),
          static_cast<double>(s.measured_wheel_mm_s[2]),
          static_cast<long>(s.battery_milli_volts), s.battery_valid ? 1 : 0,
          static_cast<unsigned>(s.global_protection_reasons),
          static_cast<unsigned>(s.wheel_protection_reasons[0]),
          static_cast<unsigned>(s.wheel_protection_reasons[1]),
          static_cast<unsigned>(s.wheel_protection_reasons[2]),
          s.output_enabled ? 1 : 0);
      sink(buf, sizeToWritten(n, sizeof(buf)));
    }
  }

 private:
  // `std::snprintf` の戻り値（負値=エンコード失敗、buf を超える値=切り詰め）
  // を、実際に `buf` へ書かれた有効バイト数へ丸める。
  static std::size_t sizeToWritten(int snprintf_result, std::size_t buf_size) noexcept {
    if (snprintf_result < 0) {
      return 0;
    }
    std::size_t written = static_cast<std::size_t>(snprintf_result);
    return written < buf_size ? written : (buf_size > 0 ? buf_size - 1 : 0);
  }

  RunSample samples_[kMaxCapacity]{};
  std::size_t capacity_ = kMaxCapacity;
  std::size_t write_index_ = 0;
  std::size_t count_ = 0;
  std::uint32_t total_appended_ = 0;
  bool overflowed_ = false;
  bool running_ = false;
  TimeMs started_at_ms_ = 0;
};

}  // namespace run_recorder
