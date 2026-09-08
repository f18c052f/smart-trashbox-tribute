#pragma once

// teleop-bringup ControllerLink (design.md "ControllerLink", 要件
// 7.2-7.6, 14.8, タスク 5.2).
//
// Bluepad32 のカスタムプラットフォーム（CONFIG_BLUEPAD32_PLATFORM_CUSTOM、
// タスク 5.1 の Implementation Notes「タスク 5.2 の担当」）を実装し、
// DualSense（BR/EDR HID、B-8）の接続状態と生の入力値を
// teleop_input::PadState へ正規化する。
// ⚠️ ここが唯一 Bluepad32 の型に触れる場所である（design.md ControllerLink
// "Responsibilities & Constraints"）。本ヘッダは `struct uni_platform` を
// 前方宣言（ポインタ型としてのみ使用）するに留め、実体・コールバック本体は
// controller_link.cpp が `<uni.h>` を include して定義する。teleop_input
// 側（PadState）は Bluepad32 の型を一切知らないままでよい。
//
// ⚠️ 本タスクの外（タスク 6.2 / TeleopApp の担当）:
//   - `uni_platform_set_custom(ControllerLink::instance().platform())` の
//     呼び出し（`uni_init()` より前に必要 — Bluepad32 自身の契約。
//     `uni_platform.c` の `uni_platform_init()` は `platform_` が
//     未設定のまま `CONFIG_BLUEPAD32_PLATFORM_CUSTOM` に入ると無限ループで
//     停止する）
//   - `uni_init()` / `btstack_run_loop_execute()` の呼び出し
//   - `notifyMotorLockHaptic()` を実際にロック保護発火のタイミングで呼ぶこと
// 本ファイルはこれらの呼び出し元にはならない
// （design.md Dependencies: "Inbound: TeleopApp (P0)"）。
//
// 初回ペアリング（要件 7.2、B-15）:
//   ⚠️ 新しい DualSense ファームウェアの既定「マルチペア」モードは
//   無言で接続に失敗する既知の不具合がある（B-15）。実機で再現可能な
//   手順は、DualSense のレガシーペアリング手順を使うことである:
//     1. DualSense の Create（旧 Share）ボタンと PS ボタンを同時に
//        長押しし、ライトバーが点滅を始めるまで待つ（レガシー
//        ペアリングモードに入る。「マルチペア」モードでは入らない）。
//     2. ESP32 側はスキャン中（`on_init_complete` で
//        `uni_bt_start_scanning_and_autoconnect_unsafe()` を呼んだ状態）
//        であること。
//     3. 接続が成立するとライトバーの点滅が止まる。
//   この手順自体は操作者が物理コントローラに対して行う操作であり、
//   コード側の責務は「発見されたデバイスとの接続を無条件に受理する」
//   （`on_device_discovered` が常に `UNI_ERROR_SUCCESS` を返す）ことと、
//   「一度ペアリングした鍵を BTstack の NVS 保存に委ねる」ことだけである
//   （下記 "再接続" 参照）。
//
// 再接続（要件 7.3）:
//   BTstack はペアリング成立時に自身でリンク鍵を NVS へ保存し、次回の
//   `uni_bt_start_scanning_and_autoconnect_*` 呼び出し時にそれを使って
//   自動的に再接続する（Bluepad32/BTstack 自身の機能）。
//   ⚠️ 本コンポーネントはこの自動再接続と競合する独自の再接続処理を
//   一切実装しない（重複実装しない）。`on_init_complete` が呼ぶのは
//   スキャン開始と着信接続の許可のみである。
//
// 鍵消去（要件 7.4、B-15）: `eraseStoredKeys()`。
//   ⚠️ 残留したリンク鍵が再接続失敗の頻出原因である（B-15）。
//
// State model（design.md "State Management"、要件 7.6）:
//   未接続 / 接続済み入力なし / 接続済み入力あり の3状態。
//   ⚠️ 接続断を「停止」へ変換しない。状態を示すだけである（要件 7.5、
//   design.md Error Handling「接続断（実行時）」行: 「ControllerLink は
//   状態を示すだけ。停止は核のウォッチドッグ（kCommandTimeout）が行う」）。
//
// Concurrency（design.md "Concurrency"）:
//   Bluepad32 のコールバックは BTstack のメインスレッド（制御ループとは
//   別コンテキスト、ESP32 classic ではコアも異なりうる）で呼ばれる。核の
//   `status()` が単一スレッド前提であるのと同じく、`read()` を呼ぶ制御
//   ループ側との同期は本コンポーネントの責務である。
//   ⚠️ パッド状態と接続状態を別々の `std::atomic` にすると、読み手が
//   「新しい state と古い pad」のような torn read を観測しうる（どの
//   フィールドを先に読むかで組み合わせが変わる、複数フィールドの
//   個別アトミック化では防げない古典的な罠）。そのため両者を1つの
//   `ControllerSnapshot` にまとめ、`portMUX_TYPE`
//   （FreeRTOS の `taskENTER_CRITICAL`/`taskEXIT_CRITICAL` が使う
//   スピンロック。ESP32 classic のデュアルコア間でも有効）で守った単一の
//   クリティカルセクション内でコピーする。Bluepad32 のコールバックは
//   BTstack のタスクコンテキストから呼ばれ ISR からは呼ばれないため、
//   ISR 安全版（`taskENTER_CRITICAL_ISR`）は不要である。
//   パッド状態はコールバック側 (BTstack スレッド) で更新し、制御ループは
//   `read()` を1周期に1回だけ呼んでスナップショットを取る
//   （design.md「パッド状態はコールバック側で更新し、制御ループが1周期に
//   1回だけ読む」）。

#include <cstdint>

#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

#include "teleop_input/pad_state.hpp"

// Bluepad32 の型は前方宣言のみ（ポインタ型として `platform()` の戻り値に
// 使うだけ）。実体は controller_link.cpp が定義する。
struct uni_platform;

namespace teleop {

// 要件 7.6: 未接続 / 接続済み入力なし / 接続済み入力あり の3状態。
enum class LinkState {
  kNotConnected,        // コントローラが接続されていない
  kConnectedNoInput,     // 接続済みだが、まだ入力(on_controller_data)が届いていない
  kConnectedWithInput,   // 接続済みで、入力が少なくとも1回届いている
};

// 制御ループが1周期に1回だけ読むスナップショット（design.md「パッド状態は
// コールバック側で更新し、制御ループが1周期に1回だけ読む」）。state と pad
// をまとめて1つのクリティカルセクションでコピーすることで、個別フィールド
// の torn read を避ける（上記 Concurrency 節）。
struct ControllerSnapshot {
  LinkState state = LinkState::kNotConnected;
  teleop_input::PadState pad;
};

// Bluepad32 のカスタムプラットフォームを実装するシングルトン
// （要件 7.1〜7.6, 14.8）。
//
// ⚠️ 現状 DualSense 1台の接続のみを状態追跡の対象とする（B-1, B-16 が
// 前提とする単一操作者・単一コントローラのテレオペ運用に合わせた判断。
// design.md / requirements.md のどちらも複数コントローラの同時運用を
// 要求していない）。`CONFIG_BLUEPAD32_MAX_DEVICES` は Bluepad32 側の既定
// （4）のままだが、2台目以降は接続そのものは許可されつつ状態追跡・
// パッド変換の対象にはならない。
class ControllerLink final {
 public:
  static ControllerLink& instance();

  ControllerLink(const ControllerLink&) = delete;
  ControllerLink& operator=(const ControllerLink&) = delete;
  ControllerLink(ControllerLink&&) = delete;
  ControllerLink& operator=(ControllerLink&&) = delete;

  // Bluepad32 のカスタムプラットフォーム vtable（`struct uni_platform`）を
  // 返す。呼び出し側（TeleopApp、タスク 6.2）が `uni_init()` より前に
  // `uni_platform_set_custom(ControllerLink::instance().platform())` を
  // 呼ぶ必要がある。本メソッド自体はその呼び出しを行わない
  // （このファイルの冒頭コメント「本タスクの外」を参照）。
  uni_platform* platform();

  // 制御ループが1周期に1回だけ呼ぶ、状態とパッド値のスナップショット
  // （要件 7.5, 7.6）。
  ControllerSnapshot read();

  // 要件 7.4: 保存済みのペアリング鍵を消去する。内部で
  // `uni_bt_del_keys_safe()`（Bluepad32 が提供する、BTstack のメイン
  // スレッドへ処理を委譲する「どのタスク／コアからでも安全に呼べる」
  // API）を経由するため、本メソッドも任意のタスク・コアから安全に呼べる。
  void eraseStoredKeys();

  // 要件 14.8: モータロック保護の発火を、対応していれば触覚（振動）で
  // 操作者へ通知する（EARS の "Where" 条件 — 非対応のコントローラや
  // 未接続時は何もしない）。⚠️ 呼び出し元のスレッドから直接 Bluepad32 の
  // report_parser API を呼ばず、`uni_bt_del_keys_safe()` と同じ流儀で
  // BTstack のメインスレッドへ処理を委譲する
  // （`btstack_run_loop_execute_on_main_thread`）。ここでは経路の提供のみ
  // を行う。実際にロック保護発火を検出してこれを呼ぶのはタスク 6.2
  // （TeleopApp）の担当である（design.md 6.2「ロック保護の発火を検出した
  // とき、5.2 が用意した触覚通知の経路を呼ぶ」）。
  void notifyMotorLockHaptic();

  // ---- 以下は Bluepad32 コールバックのトランポリン専用（内部 API） ----
  // ⚠️ TeleopApp（タスク 6.2）からは呼ばない。controller_link.cpp が
  // `<uni.h>` を include して定義する static トランポリン関数（Bluepad32の
  // 生の vtable シグネチャを持つ）だけがこれらを呼ぶ。引数はいずれも
  // Bluepad32 の型を含まない POD のみとし、Bluepad32 の型に触れる変換
  // ロジック自体はトランポリン関数側（.cpp）に閉じ込める。public に置く
  // のは、それらのトランポリンが素の自由関数であり（uni_platform の
  // vtable は関数ポインタであって非静的メンバ関数を代入できない）、
  // friend 宣言のために本ヘッダへ Bluepad32 の型を持ち込むよりも単純な
  // ためである。
  void MarkDeviceReady(const std::uint8_t (&addr)[6]);
  void MarkDeviceDisconnected(const std::uint8_t (&addr)[6]);
  void UpdatePad(const std::uint8_t (&addr)[6], const teleop_input::PadState& pad);

 private:
  ControllerLink();

  // コールバック側 (BTstack スレッド) が書き、read() が1周期に1回読む状態を
  // 守る単一のクリティカルセクション（上記 Concurrency 節）。ESP32 classic
  // のデュアルコア間でも有効なスピンロック
  // （`taskENTER_CRITICAL`/`taskEXIT_CRITICAL` が使う型そのもの）。
  mutable portMUX_TYPE lock_ = portMUX_INITIALIZER_UNLOCKED;

  // 以下はすべて lock_ で守られる（ControllerSnapshot を1回の
  // クリティカルセクションでまとめてコピーするための実体）。
  ControllerSnapshot snapshot_;
  bool has_connected_device_ = false;
  std::uint8_t connected_addr_[6] = {};
};

}  // namespace teleop
