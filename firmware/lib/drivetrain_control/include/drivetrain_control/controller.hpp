#pragma once

// drivetrain_control DrivetrainController (design.md L10
// "DrivetrainController", 要件 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.5, 12.4,
// 15.4, 15.5, 17.1, 17.7).
//
// ⚠️ タスク 6.2 スコープ: このヘッダは design.md が定義する
// `DrivetrainController` の完全なインターフェースのうち、「制御ステップの
// 骨格（設定・時刻・計測）」だけを実装する。以下は未実装で、後続タスクが
// 追加する:
//   - タスク 6.3: PID の compute/commit 配線、PwmCeiling、等比縮小、
//     ProtectionSupervisor の updateLock/updateLowVoltage/updateWatchdog/
//     compose/applyGate 配線、出力極性の適用、MotorOutputPort への書き出し
//   - タスク 6.4: status()/DrivetrainStatus、実測周期・指令反映遅れ
//   - タスク 6.5: submit()/setOutputEnabled()/resetProtections()/
//     resetOdometry() の公開入口としての配線
// このため本ヘッダはこれらの公開メソッドをまだ宣言しない。
//
// configure() の検証（要件 15.4）:
//   `drivetrain_control::validate()`（config.cpp、タスク2.3）で
//   `DrivetrainConfig` を検証したうえで、`Ports` の3つ（encoder/motor/
//   battery）がすべて非 null であることも検証する（design.md
//   "DrivetrainController" Implementation Notes: 「configure() は Ports の
//   3つが非nullであることも検証する。nullは設定エラーとして拒否する」）。
//   検証に失敗した場合（DrivetrainConfig 自体の違反、または null ポート）
//   は内部状態を構築せず、`configured_` を false のままにする。
//
//   ⚠️ null ポートの ConfigDiagnostic 表現について: `errors.hpp`
//   （タスク2.1）の `ConfigError` / `ConfigField` はどちらも `Ports`
//   （タスク2.4。`DrivetrainConfig` のフィールドではなく `configure()` の
//   別引数）の存在を前提に設計されていないため、専用の値が存在しない。
//   本実装は `ConfigError::kOutOfRange`（「定義域外」＝ null は「非null
//   ポインタ」という定義域の外）を流用し、`ConfigField` は該当が無いため
//   `kNone` のまま、`ConfigDiagnostic::index` でどのポートが null かを
//   表す（0=encoder, 1=motor, 2=battery）。専用の enum 値
//   （例: `ConfigField::kPortsEncoder` 等）を `errors.hpp` へ追加すること
//   は本タスクの範囲外と判断し、行っていない（レビュー時に要判断）。
//
// 時刻駆動（要件 3.1, 3.2, 3.6）:
//   状態を変えるすべての公開入口は `TimeMs now` を引数に取り、現在時刻を
//   内部で取得しない。自らループを回さない（呼び出し側が周期を決める）。
//
// step() の骨格（要件 3.4, 3.5）:
//   `now <= last_step_ms_` のとき、ポートも読まず状態も更新せず、前回の
//   `StepResult` をそのまま返す（副作用の無い短絡）。それ以外では
//   `dt_ms = now - last_step_ms_` を実際の時刻差から求める（制御周期を
//   固定値として前提にしない）。`EncoderPort::read()` で得た累積カウントの
//   差と `dt_ms` から各輪の計測速度を求め、`EncoderParams::polarity` を
//   ここで適用する（A/B 逆結線の吸収。design.md "Kinematics"
//   Implementation Notes: 「極性の吸収は EncoderParams::polarity /
//   OutputParams::polarity がそれぞれ1箇所で行う」。raw カウント→距離の
//   変換自体は `units::countsToMillimetres()` のみが行う、要件 7.7）。
//   得られた計測速度で `Odometry::update()` を呼ぶ。
//
//   `!configured_` のときは、ポートを一切読まず、`BlockReason::
//   kNotConfigured` を立てた遮断値（デューティ全ゼロ）の `StepResult` を
//   返す（要件 15.4）。PID・PWM上限・保護①〜④の合成・出力極性・
//   `MotorOutputPort` への書き出しはタスク 6.3 が配線するまでは行わない
//   ため、configured かつ実際にステップが進んだ場合でも `StepResult` の
//   出力デューティはこのタスクの時点ではゼロ（`StepResult{}` の既定値）の
//   ままである。
//
// Preconditions: `configure()` が `ok()` を返していること。そうでない場合
// `step()` は `kNotConfigured` を立てて遮断値を書き出す。
// Postconditions: `now <= last_step_ms_` のとき、ポートを読まず状態も
// 変えず、前回の `StepResult` をそのまま返す（要件 3.4）。経過時間は
// `now - last_step_ms_` から求める（要件 3.5）。
// Invariants: 自らループを回さない（要件 3.2）。現在時刻を内部で取得しない
// （要件 3.1）。`<ctime>` / `<chrono>` / `millis` / `esp_timer_*` を核から
// 参照しない。
//
// 内部合成オブジェクトの構築（動的メモリ確保の回避）:
//   design.md は `DrivetrainController() = default;` を要求しており、かつ
//   `Kinematics` / `Odometry` / `CommandInput` / `ProtectionSupervisor` /
//   `VelocityPid` はいずれも既定構築子を持たない（`DrivetrainConfig` から
//   導かれる引数を要求する）。ヒープ確保（`new` によるヒープ割り当て・
//   `malloc`）を使わずにこれを両立させるため、`LazySlot<T>`（本ヘッダ内の
//   プライベートなネストクラス）でアラインメント済みの生バイト領域を
//   `DrivetrainController` 自身の中に確保し、`configure()` が成功したとき
//   だけ **placement new**（`new (&storage) T(args...)`。ヒープを一切
//   使わない、既存領域上での構築）で実際のオブジェクトを構築する。
//   これは「動的メモリ確保」（Allowed Dependencies が禁止する malloc /
//   `<vector>` 等のヒープ利用）とは異なる、既知の embedded C++ の定石
//   （固定サイズの内部バッファへの遅延構築）である。⚠️ 将来のタスク8
//   （純ロジックの静的境界検査）が禁止トークンとして単純な文字列一致で
//   `new ` を検出する場合、この placement new が誤検出され得る。その際は
//   タスク8の検査側でヒープ確保の `new`（例: `new T(...)` 単体）と
//   placement new（`new (ptr) T(...)`）を区別できるようにするか、本ファイル
//   を明示的に許可リストへ加える対応が必要になる（レビュー時に要判断）。
//
// L10 は L0-L9（units.hpp, errors.hpp, types.hpp, config.hpp, ports.hpp,
// kinematics.hpp, odometry.hpp, velocity_pid.hpp, protection/*.hpp,
// command_input.hpp）にのみ依存する（design.md "Dependency Direction"）。
// このファイルはビルド構成マクロ・ペリフェラル固有の型・現在時刻を取得
// する手段のいずれも参照しない。

#include <cstddef>
#include <cstdint>
#include <new>

#include "drivetrain_control/command_input.hpp"
#include "drivetrain_control/config.hpp"
#include "drivetrain_control/errors.hpp"
#include "drivetrain_control/kinematics.hpp"
#include "drivetrain_control/odometry.hpp"
#include "drivetrain_control/ports.hpp"
#include "drivetrain_control/protection/supervisor.hpp"
#include "drivetrain_control/types.hpp"
#include "drivetrain_control/velocity_pid.hpp"

namespace drivetrain_control {

class DrivetrainController {
 public:
  DrivetrainController() noexcept = default;

  // 設定を検証し（配線されていないポートも設定エラーとして拒否し）、
  // 内部状態を構築する。ok() でない限り step() は出力を許可しない
  // （要件 15.4）。失敗した呼び出しは、直前に成功した設定が残っていても
  // それを破棄し configured() を false にする（「検証に失敗した状態では
  // 制御を開始させない」を再設定の場合にも一貫させるため）。
  ConfigDiagnostic configure(const DrivetrainConfig& config, const Ports& ports, TimeMs now) noexcept;
  bool configured() const noexcept { return configured_; }
  const DrivetrainConfig& effectiveConfig() const noexcept { return config_; }  // 要件 15.5

  // 制御ステップ。状態を変える公開メソッドはすべて now を取る（要件 3.6）。
  // タスク 6.2 時点では「骨格」のみ（configure 未完了時の遮断・過去時刻の
  // 短絡・dt 計算・計測速度算出・Odometry 更新）を実装する。PID・PWM上限・
  // 保護の合成・出力書き出しはタスク 6.3 が追加する。
  StepResult step(TimeMs now) noexcept;

 private:
  // タスク6.2 時点では status()（タスク6.4）が無く、dt→計測速度→Odometry
  // 更新の配線を外部から観測する公開手段が無い。task 6.2 のホストテスト
  // （test_controller_step）だけがこの内部状態（last_measured_mm_s_ /
  // last_encoder_counts_ / odometry_ の状態）を読めるようにする、テスト
  // 専用の friend アクセサ。status() の先取り実装ではない（DrivetrainStatus
  // 型もそのフィールドもここでは一切定義しない）。タスク6.4 が status() を
  // 追加した後は不要になる想定。
  friend struct ControllerStepTestHooks;

  // ヒープを使わず、既定構築子を持たない型 T を「未構築 or 構築済み」の
  // どちらかとして保持する。configure() が成功したときだけ emplace() で
  // placement new により構築する（ファイル先頭コメント参照）。
  //
  // ⚠️ std::launder（[basic.life]p8）: get() は placement new が返した
  // ポインタそのものではなく、生バイト領域のアドレスから reinterpret_cast
  // した新しいポインタ値を使って T へアクセスする。T（Odometry /
  // CommandInput）は const 参照メンバ（const Kinematics&）を持つため、
  // 「placement new 式の戻り値ではないポインタから、そのオブジェクトへ
  // 到達できる」ことは規格上保証されない（reset() → 再 emplace() で同じ
  // storage_ を型が一致する新しいオブジェクトへ使い回す経路がまさにこの
  // 規定の対象になる）。get() の両オーバーロードは std::launder を通す
  // ことでこれを回避する。emplace() 内の `T* ptr = new (...) T(...)` は
  // placement new 式そのものが返すポインタを直接使っているため、
  // launder 無しで問題ない（[basic.life]p8 の例外に該当）。
  template <typename T>
  class LazySlot {
   public:
    LazySlot() noexcept = default;
    ~LazySlot() noexcept { reset(); }
    LazySlot(const LazySlot&) = delete;
    LazySlot& operator=(const LazySlot&) = delete;

    template <typename... Args>
    T& emplace(Args&&... args) noexcept {
      reset();
      T* ptr = new (static_cast<void*>(&storage_)) T(static_cast<Args&&>(args)...);
      engaged_ = true;
      return *ptr;
    }

    void reset() noexcept {
      if (engaged_) {
        get().~T();
        engaged_ = false;
      }
    }

    bool engaged() const noexcept { return engaged_; }
    T& get() noexcept { return *std::launder(reinterpret_cast<T*>(&storage_)); }
    const T& get() const noexcept { return *std::launder(reinterpret_cast<const T*>(&storage_)); }

   private:
    // std::byte（<cstddef>）を使う。`unsigned char` でも動作は同じだが、
    // 「unsigned 」（末尾スペース込み）は design.md FirmwareBoundaryCheck
    // が挙げる禁止型トークンのリテラル文字列であり、タスク8の静的検査が
    // 単純な文字列一致で実装された場合に誤検出し得る（このバッファは
    // 常に sizeof(T) バイトの生ストレージであり、幅が処理系依存になる
    // 整数型の懸念＝要件4.1, 4.6が対象にする問題とは無関係）。
    alignas(T) std::byte storage_[sizeof(T)];
    bool engaged_ = false;
  };

  // 依存関係の無い（他の合成メンバへの参照を持たない）null ポート判定。
  static ConfigDiagnostic checkPortsNonNull(const Ports& ports) noexcept;

  // 合成オブジェクトをすべて未構築へ戻す。参照を保持する側
  // （CommandInput/Odometry が Kinematics を参照）を先に、参照される側
  // （Kinematics）を後に破棄する（宣言順の逆 = 依存の逆順）。
  void resetComposedState() noexcept;

  // --- 実効設定・診断・configured フラグ -------------------------------
  DrivetrainConfig config_{};
  ConfigDiagnostic diagnostic_{};
  bool configured_ = false;
  Ports ports_{};

  // --- step() の骨格が使う固有状態 ---------------------------------------
  // 前回のステップ時刻と結果（要件 3.4 の短絡・要件 3.3 の決定性のため
  // 直近の結果をキャッシュする。この2つと実効設定・[将来]
  // 最後に適用した指令の有効時刻が DrivetrainController 自身の固有状態
  // ―― design.md "DrivetrainController" State Management 参照）。
  TimeMs last_step_ms_ = 0;
  StepResult last_result_{};

  // 前回ステップ時点のエンコーダ累積カウント（差分計算用）。configure()
  // が最初の基準値を1回読み、以降は step() が実際に進んだ回だけ更新する。
  // design.md の「DrivetrainController 自身の固有状態」列挙には明示されて
  // いないが、「カウント差から計測速度を求める」（タスク6.2の要求）には
  // 直前の読み値をどこかに保持する必要があり、他のどのコンポーネントも
  // この責務を持たないため、ここに保持する（task 5.5 の
  // last_lock_condition_ 等、Implementation 上必要な追加内部状態と同種の
  // 扱い）。
  EncoderCounts last_encoder_counts_{};

  // 直近ステップで算出した各輪の計測速度（mm/s、EncoderParams::polarity
  // 適用済み）。タスク6.2では Odometry::update() へ渡す以外に消費者が無い
  // が、タスク6.3 の VelocityPid::compute() の measured 引数、タスク6.4 の
  // status() の wheel_measured_mm_s がここを再利用する前提で保持する。
  float last_measured_mm_s_[kWheelCount] = {0.0f, 0.0f, 0.0f};

  // --- 合成オブジェクト（configure() 成功時のみ構築） --------------------
  // 宣言順 = 構築の依存順（Kinematics が先。Odometry/CommandInput は
  // Kinematics への参照を保持するため後）。C++ はメンバを宣言の逆順で
  // 破棄するため、DrivetrainController の暗黙のデストラクタでも
  // Odometry/CommandInput が Kinematics より先に破棄され、参照の生存期間
  // が壊れない。
  LazySlot<Kinematics> kinematics_;
  LazySlot<Odometry> odometry_;
  LazySlot<CommandInput> command_input_;
  LazySlot<ProtectionSupervisor> protection_;
  LazySlot<VelocityPid> pid_[kWheelCount];

  // LazySlot は placement new で構築したオブジェクトへの生の参照
  // （CommandInput::kinematics_ 等）を跨いで保持され得るため、
  // DrivetrainController のメンバワイズなコピーは内部参照を壊す
  // （コピー先の CommandInput がコピー元の Kinematics を参照したままに
  // なる）。design.md はコピー／ムーブを要求しておらず、コピーを黙って
  // 許すと壊れた参照を生む危険があるため、両方を明示的に禁止する
  // （design.md の Service Interface には現れない、本実装が安全性の
  // ために追加した宣言。レビュー時に要判断）。
 public:
  DrivetrainController(const DrivetrainController&) = delete;
  DrivetrainController& operator=(const DrivetrainController&) = delete;
  DrivetrainController(DrivetrainController&&) = delete;
  DrivetrainController& operator=(DrivetrainController&&) = delete;
};

}  // namespace drivetrain_control
