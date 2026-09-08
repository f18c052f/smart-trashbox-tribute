// run_recorder.cpp (teleop-bringup タスク 6.1, requirements.md 15.1-15.5).
//
// 非テンプレートのメンバ関数の実体をここへ置く。`board_pins`（タスク 1.3の
// 実装メモ）と同じ理由: SRCS を持たないコンポーネントは ESP-IDF 上で
// INTERFACE ライブラリになり、利用側（task 6.2 の TeleopApp）が現れるまで
// 一度もコンパイルされない。この翻訳単位を持つことで、
// `firmware/src/CMakeLists.txt` の REQUIRES に加えた時点で（消費者の有無に
// かかわらず）ホスト・実機の両方で実際にコンパイルされることを保証する。

#include "run_recorder/run_recorder.hpp"

namespace run_recorder {

RunRecorder::RunRecorder(std::size_t capacity) noexcept {
  if (capacity == 0) {
    capacity = 1;
  } else if (capacity > kMaxCapacity) {
    capacity = kMaxCapacity;
  }
  capacity_ = capacity;
}

void RunRecorder::beginRun(TimeMs started_at_ms) noexcept {
  started_at_ms_ = started_at_ms;
  running_ = true;
  write_index_ = 0;
  count_ = 0;
  total_appended_ = 0;
  overflowed_ = false;
}

void RunRecorder::endRun() noexcept { running_ = false; }

void RunRecorder::append(const RunSample& sample) noexcept {
  if (!running_) {
    return;
  }

  samples_[write_index_] = sample;
  write_index_ = (write_index_ + 1) % capacity_;
  ++total_appended_;

  if (count_ < capacity_) {
    ++count_;
  } else {
    // 容量に達している: 今回の書き込みが最も古いサンプルを上書きした
    // （リングバッファの上限到達方針。run_recorder.hpp ファイル冒頭コメント
    // 参照）。
    overflowed_ = true;
  }
}

const RunSample& RunRecorder::sampleAt(std::size_t index) const noexcept {
  if (index >= count_) {
    static const RunSample kEmpty{};
    return kEmpty;
  }
  // count_ < capacity_ の間は書き込みが折り返していないため先頭は 0。
  // 容量に達した後は write_index_ が次に上書きする位置＝最古のサンプルの
  // 位置と一致する。
  std::size_t start = (count_ < capacity_) ? 0 : write_index_;
  return samples_[(start + index) % capacity_];
}

}  // namespace run_recorder
