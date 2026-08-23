#pragma once

// drivetrain_control DrivetrainController (design.md L10
// "DrivetrainController", 要件 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 6.5, 12.4,
// 15.4, 15.5, 17.1, 17.7).
//
// ⚠️ タスク 6.2/6.3 スコープ: このヘッダは design.md が定義する
// `DrivetrainController` の完全なインターフェースのうち、「制御ステップの
// 骨格（設定・時刻・計測、タスク6.2）」と「出力段（上限・PID・縮小・遮断・
// 極性、タスク6.3）」を実装する。以下は未実装で、後続タスクが追加する:
//   - タスク 6.4: status()/DrivetrainStatus、実測周期・指令反映遅れ
//   - タスク 6.5: submit()/setOutputEnabled()/resetProtections()/
//     resetOdometry() の公開入口としての配線
// このため本ヘッダはこれらの公開メソッドをまだ宣言しない
// （`CommandInput::submit()` へは task 6.5 まで外部から到達できないため、
// タスク6.3 のホストテストは friend アクセサ経由で内部の `CommandInput` へ
// 直接指令を投入する。controller.cpp 側のロジックはこの制約を前提にしない
// ―― `command_input_.get()` を通じて通常どおり呼ぶだけであり、将来 6.5 が
// 公開 `submit()` を追加しても controller.cpp の変更は不要）。
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
//   返す（要件 15.4）。
//
// step() の出力段（タスク 6.3。design.md System Flows「制御ステップ1回の
// フロー」の `ReadBatt` 以降。要件 6.5, 12.4, 13.5, 13.6, 14.1, 14.2,
// 14.3）:
//   `BatteryVoltagePort::read()` → `ProtectionSupervisor::
//   updateLowVoltage()` → `PwmCeiling::evaluate()`（`DrivetrainController`
//   自身が直接保持して呼ぶ。`ProtectionSupervisor` を介さない） →
//   `ProtectionSupervisor::updateWatchdog()` →
//   `CommandInput::latched()` から輪目標速度を取得 →
//   非公開ヘルパー `applyWheelOutputs()`（`VelocityPid::compute()` を
//   3輪ぶん呼んで生の出力を得た後、`scaleToLimit()` で PWM 上限へ3輪
//   まとめて等比縮小し、`VelocityPid::commit()` を各輪の縮小後の値で
//   呼ぶ。ウォッチドッグ発火中はこの `compute→scale→commit` の代わりに
//   3輪すべてで `VelocityPid::holdReset()` を呼び、適用デューティを 0
//   にする） → `ProtectionSupervisor::updateLock()`（輪ごと、上限適用後・
//   遮断前の出力指令で判定） → `ProtectionSupervisor::compose()` →
//   `ProtectionSupervisor::applyGate()` → 出力極性
//   （`OutputParams::polarity`）の適用（最後の1箇所だけ） →
//   `MotorOutputPort::write()`。
//
//   `VelocityPid::compute()`/`commit()` を直接呼ぶコードパスは
//   `applyWheelOutputs()` の外に作らない（design.md "DrivetrainController"
//   Implementation Notes: 2段プロトコルの誤用防止）。
//
// Preconditions: `configure()` が `ok()` を返していること。そうでない場合
// `step()` は `kNotConfigured` を立てて遮断値を書き出す。
// Postconditions: `now <= last_step_ms_` のとき、ポートを読まず状態も
// 変えず、前回の `StepResult` をそのまま返す（要件 3.4）。経過時間は
// `now - last_step_ms_` から求める（要件 3.5）。PWM 上限の適用と等比縮小は
// 遮断より前に行う（要件 12.4, 6.5）。
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
#include "drivetrain_control/protection/pwm_ceiling.hpp"
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
  // タスク 6.2「骨格」（configure 未完了時の遮断・過去時刻の短絡・dt
  // 計算・計測速度算出・Odometry 更新）に、タスク 6.3「出力段」
  // （PwmCeiling・PID compute/scale/commit・保護①〜④の合成・出力極性・
  // MotorOutputPort への書き出し）を続けて実装する。
  StepResult step(TimeMs now) noexcept;

 private:
  // タスク6.2/6.3 時点では status()（タスク6.4）が無く、dt→計測速度→
  // Odometry 更新や出力段（PID/上限/保護/極性）の配線を外部から観測する
  // 公開手段が無い。同じく `CommandInput::submit()` も公開されていない
  // （task 6.5）。test_controller_step だけがこの内部状態
  // （last_measured_mm_s_ / last_encoder_counts_ / odometry_ /
  // last_commanded_duty_ / command_input_ / pid_ の状態）を読み・指令を
  // 直接投入できるようにする、テスト専用の friend アクセサ。status() の
  // 先取り実装ではない（DrivetrainStatus 型もそのフィールドもここでは一切
  // 定義しない）。タスク6.4/6.5 が該当メソッドを追加した後は不要になる
  // 想定。
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

  // タスク 6.3: `VelocityPid::compute()` → `scaleToLimit()`（PWM 上限で
  // 3輪まとめての等比縮小） → `VelocityPid::commit()` の呼び出し順序を
  // この1箇所へ閉じ込める非公開ヘルパー（design.md "DrivetrainController"
  // Implementation Notes）。`compute()`/`commit()` を直接呼ぶコードパスは
  // これ以外に作らない。
  //
  // ウォッチドッグ発火中（`watchdog_tripped == true`）は、
  // `targets_mm_s` を無視し、3輪すべてで `compute()`/`commit()` の代わりに
  // `VelocityPid::holdReset()` を呼び、`out_applied_duty` を全輪 0 にする
  // （design.md「フローの決定事項」: 「発火中は CommandInput の保持する
  // 輪目標速度が PID へ渡らない（目標を 0 とし、PID を holdReset に
  // 置く）」。`VelocityPid` の Preconditions は同一ステップ内での
  // compute/commit と holdReset の混在を禁じる）。
  //
  // `out_applied_duty` は「上限適用後・遮断前」の出力指令であり、
  // `ProtectionSupervisor::updateLock()` の判定入力にそのまま使う
  // （design.md「フローの決定事項」）。
  void applyWheelOutputs(const float targets_mm_s[kWheelCount], DurationMs dt_ms, float ceiling,
                         bool watchdog_tripped, float out_applied_duty[kWheelCount]) noexcept;

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

  // タスク 6.3: 直近ステップで `applyWheelOutputs()` が返した「上限適用後・
  // 遮断前」の出力指令（`OutputParams::polarity` 適用前。design.md
  // 「フローの決定事項」の `Commit`〜`Lock` ノード間の値）。
  // `ProtectionSupervisor::updateLock()` の判定入力として同一ステップ内で
  // 再利用するほか、ホストテストが「ウォッチドッグ発火中に PID の内部
  // ターゲットが実際に 0 化されたこと」を、最終出力の遮断（ゲート）で
  // マスクされずに検証できるようにするため、メンバとして保持する
  // （friend アクセサ経由。他のどのコンポーネントもこの値を保持しない）。
  float last_commanded_duty_[kWheelCount] = {0.0f, 0.0f, 0.0f};

  // --- 合成オブジェクト（configure() 成功時のみ構築） --------------------
  // 宣言順 = 構築の依存順（Kinematics が先。Odometry/CommandInput は
  // Kinematics への参照を保持するため後）。C++ はメンバを宣言の逆順で
  // 破棄するため、DrivetrainController の暗黙のデストラクタでも
  // Odometry/CommandInput が Kinematics より先に破棄され、参照の生存期間
  // が壊れない。`PwmCeiling` は他の合成メンバへの参照を保持しないため、
  // 構築・破棄の順序に制約が無い。
  LazySlot<Kinematics> kinematics_;
  LazySlot<Odometry> odometry_;
  LazySlot<CommandInput> command_input_;
  LazySlot<ProtectionSupervisor> protection_;
  // 要件 12.4／design.md "PwmCeiling" Implementation Notes: `PwmCeiling` は
  // `ProtectionSupervisor` を介さず `DrivetrainController` が直接保持する。
  LazySlot<PwmCeiling> pwm_ceiling_;
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
