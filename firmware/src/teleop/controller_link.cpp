#include "teleop/controller_link.hpp"

#include <cstring>

// ⚠️ 本ファイルだけが Bluepad32 の型に触れる（controller_link.hpp の冒頭
// コメント参照）。<uni.h> は Bluepad32 の全ヘッダを束ねた集約ヘッダで、
// bt/uni_bt.h 経由で BTstack の <btstack.h>（btstack_run_loop.h を含む）
// も引き込む（firmware/.deps/bluepad32/src/components/bluepad32/include/
// uni.h、bt/uni_bt.h の #include 列で確認済み）。
#include <uni.h>

namespace teleop {

namespace {

// 要件 8.1 が指すデッドマン用の「指定ボタン」は requirements.md / design.md
// のどちらも具体的なボタンを規定していない。両スティックの親指を離さずに
// 押し続けられるよう、左肩ボタン (L1) を割り当てる（実装判断。タスク 4.2
// Implementation Notes の「専用フィールドが要ると分かったら、そのときに
// 足すこと」と同じ性質 — OQ-17 / M2a の実走調整対象）。
constexpr std::uint16_t kDeadmanButtonMask = BUTTON_SHOULDER_L;

// [-1, +1] へ正規化する。uni_gamepad_t の軸は AXIS_NORMALIZE_RANGE
// （1024、controller/uni_gamepad.c）を中心 0 とした符号付き範囲
// （概ね -512..511、uni_gamepad.h のコメント）。
float NormalizeAxis(std::int32_t raw) {
  float v = static_cast<float>(raw) / (static_cast<float>(AXIS_NORMALIZE_RANGE) / 2.0f);
  if (v > 1.0f) {
    v = 1.0f;
  } else if (v < -1.0f) {
    v = -1.0f;
  }
  return v;
}

// 輪単体テスト対象の選択（要件 8.5）。D-pad の左/下/右へ 0/1/2 を割り当て、
// 何も押していなければ -1（機体走行）とする。requirements.md / design.md
// のどちらもどの入力を割り当てるかは規定していない実装判断であり、
// NormalizeAxis 同様 M2a の実走調整対象である。
std::int8_t SelectedWheelFromDpad(std::uint8_t dpad) {
  if (dpad & DPAD_LEFT) {
    return 0;
  }
  if (dpad & DPAD_DOWN) {
    return 1;
  }
  if (dpad & DPAD_RIGHT) {
    return 2;
  }
  return -1;
}

void CopyAddr(const bd_addr_t src, std::uint8_t (&dst)[6]) {
  std::memcpy(dst, src, 6);
}

// ---- Bluepad32 プラットフォームコールバック ----------------------------
// `struct uni_platform` の vtable シグネチャそのまま。BTstack のメイン
// スレッドから呼ばれる。ここで Bluepad32 の型（uni_hid_device_t,
// uni_controller_t 等）を扱い、その結果を POD のみの内部更新 API
// （ControllerLink の public だが「内部専用」なメソッド群）へ渡す。

void OnInit(int argc, const char** argv) {
  (void)argc;
  (void)argv;
}

void OnInitComplete() {
  // 要件 7.3: 保存済みリンク鍵での自動再接続と、新規ペアリングの受理を
  // 有効化する。BTstack 自身が NVS に保存したリンク鍵を用いて自動的に
  // 再接続する（controller_link.hpp 冒頭コメント「再接続」節）。
  // ⚠️ on_init_complete から呼ぶ「_unsafe」系は Bluepad32 の契約上
  // BTstack スレッドから直接呼んでよい（examples/esp32/main/my_platform.c
  // の on_init_complete と同じ呼び出し）。
  uni_bt_start_scanning_and_autoconnect_unsafe();
  uni_bt_allow_incoming_connections(true);
}

uni_error_t OnDeviceDiscovered(bd_addr_t addr, const char* name, std::uint16_t cod, std::uint8_t rssi) {
  (void)addr;
  (void)name;
  (void)cod;
  (void)rssi;
  // 要件 7.2: 発見されたデバイスを無条件に受理する。レガシーペアリング
  // 手順（controller_link.hpp 冒頭コメント参照）に従うかどうかは操作者が
  // 物理コントローラに対して行う操作であり、本コールバック側でのフィルタ
  // は不要（DualSense 以外の HID デバイスを弾く必要が生じたら、ここへ
  // Class of Device でのフィルタを足す）。
  return UNI_ERROR_SUCCESS;
}

void OnDeviceConnected(uni_hid_device_t* d) {
  // HID記述子の解析等がまだ完了していない可能性があるため
  // (uni_platform.h の on_device_connected のコメント)、状態はここでは
  // 更新しない。実際に使える状態になった時点 (on_device_ready) で更新する。
  (void)d;
}

void OnDeviceDisconnected(uni_hid_device_t* d) {
  std::uint8_t addr[6];
  CopyAddr(d->conn.btaddr, addr);
  ControllerLink::instance().MarkDeviceDisconnected(addr);
}

uni_error_t OnDeviceReady(uni_hid_device_t* d) {
  std::uint8_t addr[6];
  CopyAddr(d->conn.btaddr, addr);
  ControllerLink::instance().MarkDeviceReady(addr);
  return UNI_ERROR_SUCCESS;
}

void OnControllerData(uni_hid_device_t* d, uni_controller_t* ctl) {
  if (ctl->klass != UNI_CONTROLLER_CLASS_GAMEPAD) {
    // DualSense がマウス等の仮想子デバイスを持つケース (uni_hid_device_t
    // の parent/child) は本コンポーネントの対象外。teleop_input::PadState
    // はゲームパッド由来の軸/ボタンしか表現しない。
    return;
  }
  const uni_gamepad_t& gp = ctl->gamepad;

  teleop_input::PadState pad;
  pad.left_x = NormalizeAxis(gp.axis_x);
  pad.left_y = NormalizeAxis(gp.axis_y);
  pad.right_x = NormalizeAxis(gp.axis_rx);
  pad.deadman = (gp.buttons & kDeadmanButtonMask) != 0;
  pad.selected_wheel = SelectedWheelFromDpad(gp.dpad);

  std::uint8_t addr[6];
  CopyAddr(d->conn.btaddr, addr);
  ControllerLink::instance().UpdatePad(addr, pad);
}

const uni_property_t* OnGetProperty(uni_property_idx_t idx) {
  (void)idx;
  return nullptr;
}

void OnOobEvent(uni_platform_oob_event_t event, void* data) {
  (void)event;
  (void)data;
}

}  // namespace

ControllerLink::ControllerLink() = default;

ControllerLink& ControllerLink::instance() {
  static ControllerLink link;
  return link;
}

uni_platform* ControllerLink::platform() {
  // 関数ローカル static: プロセス生存期間で1つだけの vtable
  // （examples/esp32/main/my_platform.c の get_my_platform() と同じ形）。
  static uni_platform plat = {
      .name = "teleop-controller-link",
      .init = &OnInit,
      .on_init_complete = &OnInitComplete,
      .on_device_discovered = &OnDeviceDiscovered,
      .on_device_connected = &OnDeviceConnected,
      .on_device_disconnected = &OnDeviceDisconnected,
      .on_device_ready = &OnDeviceReady,
      .on_gamepad_data = nullptr,  // deprecated（uni_platform.h）。on_controller_data を使う。
      .on_controller_data = &OnControllerData,
      .get_property = &OnGetProperty,
      .on_oob_event = &OnOobEvent,
      .device_dump = nullptr,
      .register_console_cmds = nullptr,  // 任意。鍵消去は eraseStoredKeys() で提供する（要件 7.4）。
  };
  return &plat;
}

ControllerSnapshot ControllerLink::read() {
  ControllerSnapshot out;
  taskENTER_CRITICAL(&lock_);
  out = snapshot_;
  taskEXIT_CRITICAL(&lock_);
  return out;
}

void ControllerLink::eraseStoredKeys() {
  // 要件 7.4。uni_bt_del_keys_safe() は「どのタスク／コアからでも安全に
  // 呼べる」(uni_bt.h) — 内部で btstack_run_loop_execute_on_main_thread()
  // を使い BTstack のメインスレッドへ処理を委譲する
  // (firmware/.deps/bluepad32/src/components/bluepad32/bt/uni_bt.c)。
  uni_bt_del_keys_safe();
}

namespace {

// notifyMotorLockHaptic() が BTstack のメインスレッドへ処理を委譲するための
// 小さな固定プール。uni_bt.c 自身の `_safe` API 実装
// (cmd_callback_registration[CMD_CALLBACK_MAX] 、round-robin) と同じ流儀:
// btstack_context_callback_registration_t はまだ処理されていないノードを
// 再利用してはいけない（リンクリストへ二重に繋がる）ため、複数枠を
// round-robin して使い回す。
constexpr int kRumbleCallbackSlots = 4;
btstack_context_callback_registration_t g_rumble_registrations[kRumbleCallbackSlots];
std::uint8_t g_rumble_addrs[kRumbleCallbackSlots][6];
int g_rumble_slot = 0;

void RumbleCallback(void* context) {
  auto* addr = static_cast<std::uint8_t*>(context);
  uni_hid_device_t* d = uni_hid_device_get_instance_for_address(addr);
  if (d == nullptr) {
    // 通知を委譲した後に切断された等。無害に無視する
    // （要件 7.5 と同じ「状態を示すだけ」の精神 — ここでは何も遮断しない）。
    return;
  }
  if (d->report_parser.play_dual_rumble == nullptr) {
    // 要件 14.8 の "Where" 条件: 触覚に対応しないコントローラでは何もしない。
    return;
  }
  d->report_parser.play_dual_rumble(d, /*start_delay_ms=*/0, /*duration_ms=*/250,
                                     /*weak_magnitude=*/0, /*strong_magnitude=*/220);
}

}  // namespace

void ControllerLink::notifyMotorLockHaptic() {
  std::uint8_t addr[6];
  bool connected;
  taskENTER_CRITICAL(&lock_);
  connected = has_connected_device_;
  if (connected) {
    std::memcpy(addr, connected_addr_, 6);
  }
  taskEXIT_CRITICAL(&lock_);

  if (!connected) {
    // 要件 14.8 の "Where" 条件: 未接続なら通知先が無い。何もしない。
    return;
  }

  // ⚠️ ここから先で Bluepad32/BTstack の API
  // (uni_hid_device_get_instance_for_address, report_parser.play_dual_rumble)
  // を直接呼ばない。呼び出し元 (TeleopApp、タスク 6.2) のスレッドから見て
  // 安全なのは btstack_run_loop_execute_on_main_thread() 経由での委譲だけ
  // である (uni_bt_del_keys_safe() と同じ流儀。controller_link.hpp の
  // notifyMotorLockHaptic() コメント参照)。
  const int slot = g_rumble_slot;
  g_rumble_slot = (g_rumble_slot + 1) % kRumbleCallbackSlots;
  std::memcpy(g_rumble_addrs[slot], addr, 6);
  g_rumble_registrations[slot].callback = &RumbleCallback;
  g_rumble_registrations[slot].context = g_rumble_addrs[slot];
  btstack_run_loop_execute_on_main_thread(&g_rumble_registrations[slot]);
}

void ControllerLink::MarkDeviceReady(const std::uint8_t (&addr)[6]) {
  taskENTER_CRITICAL(&lock_);
  snapshot_.state = LinkState::kConnectedNoInput;
  snapshot_.pad = teleop_input::PadState{};
  has_connected_device_ = true;
  std::memcpy(connected_addr_, addr, 6);
  taskEXIT_CRITICAL(&lock_);
}

void ControllerLink::MarkDeviceDisconnected(const std::uint8_t (&addr)[6]) {
  taskENTER_CRITICAL(&lock_);
  if (has_connected_device_ && std::memcmp(connected_addr_, addr, 6) == 0) {
    snapshot_.state = LinkState::kNotConnected;
    snapshot_.pad = teleop_input::PadState{};
    has_connected_device_ = false;
  }
  taskEXIT_CRITICAL(&lock_);
}

void ControllerLink::UpdatePad(const std::uint8_t (&addr)[6], const teleop_input::PadState& pad) {
  taskENTER_CRITICAL(&lock_);
  if (has_connected_device_ && std::memcmp(connected_addr_, addr, 6) == 0) {
    snapshot_.state = LinkState::kConnectedWithInput;
    snapshot_.pad = pad;
  }
  taskEXIT_CRITICAL(&lock_);
}

}  // namespace teleop
