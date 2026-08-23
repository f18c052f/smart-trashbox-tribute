#include "drivetrain_control/controller.hpp"

#include "drivetrain_control/units.hpp"

// drivetrain_control DrivetrainController implementation (design.md L10
// "DrivetrainController", タスク 6.2「制御ステップの骨格（設定・時刻・
// 計測）」・タスク 6.3「制御ステップの出力段（上限・PID・縮小・遮断・
// 極性）」). See controller.hpp for the full contract, preconditions, and
// the placement-new-based composition rationale. 要件 3.1, 3.2, 3.4, 3.5,
// 3.6, 6.5, 12.4, 13.5, 13.6, 14.1, 14.2, 14.3, 15.4。

namespace drivetrain_control {

ConfigDiagnostic DrivetrainController::checkPortsNonNull(const Ports& ports) noexcept {
  // controller.hpp 冒頭コメント参照: errors.hpp の ConfigError/ConfigField
  // はどちらも Ports 専用の値を持たないため、ConfigError::kOutOfRange
  // （定義域外）を流用し、index でどのポートが null かを表す
  // （0=encoder, 1=motor, 2=battery）。
  if (ports.encoder == nullptr) {
    return ConfigDiagnostic{ConfigError::kOutOfRange, ConfigField::kNone, /*index=*/0};
  }
  if (ports.motor == nullptr) {
    return ConfigDiagnostic{ConfigError::kOutOfRange, ConfigField::kNone, /*index=*/1};
  }
  if (ports.battery == nullptr) {
    return ConfigDiagnostic{ConfigError::kOutOfRange, ConfigField::kNone, /*index=*/2};
  }
  return ConfigDiagnostic{};
}

void DrivetrainController::resetComposedState() noexcept {
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    pid_[i].reset();
  }
  pwm_ceiling_.reset();
  protection_.reset();
  command_input_.reset();
  odometry_.reset();
  kinematics_.reset();
}

ConfigDiagnostic DrivetrainController::configure(const DrivetrainConfig& config, const Ports& ports,
                                                  TimeMs now) noexcept {
  // 検証に失敗した状態では制御を開始させない（要件 15.4）。再設定の場合も
  // 含め、まず「未設定」へ戻してから検証する。これにより、直前に成功した
  // 設定が今回の失敗を隠して生き残ることが無い。
  configured_ = false;
  resetComposedState();

  ConfigDiagnostic diag = validate(config);
  if (diag.ok()) {
    diag = checkPortsNonNull(ports);
  }
  diagnostic_ = diag;
  if (!diag.ok()) {
    return diagnostic_;
  }

  config_ = config;
  ports_ = ports;

  // 依存順に構築する（Kinematics が先。Odometry/CommandInput はこれへの
  // 参照を保持する）。
  Kinematics& kinematics = kinematics_.emplace(config_.geometry);
  Odometry& odometry = odometry_.emplace(kinematics);
  odometry.reset(Pose2D{}, now);  // 原点を configure() 時点で初期化する。
  command_input_.emplace(kinematics, config_.limits);
  protection_.emplace(config_);
  // 要件 12.4／design.md "PwmCeiling" Implementation Notes: ProtectionSupervisor
  // を介さず DrivetrainController が直接保持する。
  pwm_ceiling_.emplace(config_.pwm_ceiling, config_.output.absolute_max_duty);
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    pid_[i].emplace(config_.pid[i]);
  }

  // エンコーダ累積カウントの基準値を1回読む。以降の step() はこの値からの
  // 差分で計測速度を求める（「configure() 時点からの変化」を最初のステップ
  // の速度計算の基準にするため）。
  last_encoder_counts_ = ports_.encoder->read();
  last_measured_mm_s_[0] = 0.0f;
  last_measured_mm_s_[1] = 0.0f;
  last_measured_mm_s_[2] = 0.0f;
  last_commanded_duty_[0] = 0.0f;
  last_commanded_duty_[1] = 0.0f;
  last_commanded_duty_[2] = 0.0f;
  last_step_ms_ = now;
  last_result_ = StepResult{};

  // タスク 6.4: status() が使う固有状態を、CommandInput::latched() の既定値
  // （WheelTargets{}、issued_at_ms=0）と揃えてリセットする（controller.hpp
  // の当該メンバのコメント参照）。
  last_step_interval_ms_ = 0;
  last_applied_command_issued_at_ms_ = 0;
  command_apply_latency_ms_ = 0;
  last_pwm_ceiling_ = 0.0f;
  last_low_voltage_state_ = LowVoltageState::kNormal;

  configured_ = true;
  return diagnostic_;  // ok()
}

StepResult DrivetrainController::step(TimeMs now) noexcept {
  if (!configured_) {
    // ポートを一切読まない（設定が無い状態でポートへ触れない）。専用の
    // 遮断理由を立てた遮断値（デューティ全ゼロ）を返す（要件 15.4）。
    StepResult blocked{};
    blocked.global_reasons = static_cast<BlockMask>(BlockReason::kNotConfigured);
    last_result_ = blocked;
    return last_result_;
  }

  // 与えられた時刻が前回以前のときは、ポートも読まず状態も更新せず、
  // 前回の結果をそのまま返す副作用の無い短絡（要件 3.4）。
  if (now <= last_step_ms_) {
    return last_result_;
  }

  // 経過時間を実際の時刻差から求める。制御周期を固定値として前提にしない
  // （要件 3.5）。
  const DurationMs dt_ms = static_cast<DurationMs>(now - last_step_ms_);
  last_step_ms_ = now;
  const float dt_s = units::millisToSeconds(dt_ms);

  // 要件 17.1: 実測の制御周期。新たな計算は持たず、この dt_ms をそのまま
  // status() 用にキャッシュする。
  last_step_interval_ms_ = dt_ms;

  // エンコーダ累積カウントの差と経過時間から各輪の計測速度を求める。
  // EncoderParams::polarity をここで適用する（A/B 逆結線の吸収、要件 6.6
  // ―― カウント→距離の変換自体は units::countsToMillimetres() のみが行う
  // という要件 7.7 の「1箇所」原則を崩さない。極性は変換後のスカラーへの
  // 符号適用であり、変換の一部ではない）。
  const EncoderCounts counts = ports_.encoder->read();
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    const std::int64_t delta_counts = counts.count[i] - last_encoder_counts_.count[i];
    const float delta_mm = units::countsToMillimetres(delta_counts, config_.encoder.counts_per_wheel_rev,
                                                        config_.encoder.wheel_diameter_mm);
    const float polarity = static_cast<float>(config_.encoder.polarity[i]);
    last_measured_mm_s_[i] = polarity * delta_mm / dt_s;
  }
  last_encoder_counts_ = counts;

  // オドメトリを更新する（要件 8.2 系。Odometry::update() の中点法積分に
  // 委譲する。DrivetrainController はここでは順運動学・積分の計算を持たな
  // い）。
  odometry_.get().update(last_measured_mm_s_, dt_ms, now);

  // --- 出力段（タスク 6.3。design.md System Flows「制御ステップ1回の
  // フロー」の ReadBatt 以降。要件 6.5, 12.4, 13.5, 13.6, 14.1, 14.2,
  // 14.3） -------------------------------------------------------------

  // ReadBatt → LowV: 保護②（低電圧）の検出器を1回進める。戻り値は
  // status() の low_voltage_state 用にそのまま保存する（design.md
  // "ProtectionSupervisor" Implementation Notes: GateOutcome に重複して
  // 持たせない）。
  const VoltageSample battery_sample = ports_.battery->read();
  last_low_voltage_state_ = protection_.get().updateLowVoltage(battery_sample, now);

  // Ceil: PwmCeiling は ProtectionSupervisor を介さず DrivetrainController
  // が直接呼ぶ（design.md「フローの決定事項」）。上限は PID の compute
  // 直後の等比縮小に使うため、Supervisor::compose() より前に確定させる。
  // 状態を持たない純関数のため、status() 用にここでキャッシュする。
  const float ceiling = pwm_ceiling_.get().evaluate(battery_sample);
  last_pwm_ceiling_ = ceiling;

  // Wd: 保護④（ウォッチドッグ）の検出器を1回進める。判定は最後に有効
  // だった指令の有効時刻と「起動後に有効な指令を受けたか」のみを見る
  // （指令元に固有の情報を参照しない、要件 13.2, 13.7）。
  const WheelTargets& targets = command_input_.get().latched();

  // 要件 17.1: 指令反映遅れ。latched().issued_at_ms が毎ステップ保持して
  // いる直前値から変化していれば「このステップで初めて適用された」とみなし
  // 遅れを算出し直す。変化していなければ前回の値を保持する（design.md
  // "DrivetrainController" Postconditions の算出式を正確に踏襲。
  // controller.hpp 冒頭 status() コメント参照）。
  if (targets.issued_at_ms != last_applied_command_issued_at_ms_) {
    command_apply_latency_ms_ = static_cast<DurationMs>(now - targets.issued_at_ms);
    last_applied_command_issued_at_ms_ = targets.issued_at_ms;
  }

  const bool watchdog_tripped = protection_.get().updateWatchdog(now, targets.issued_at_ms, targets.valid);

  // Target → Pid → Scale → Commit: 2段プロトコルの呼び出し順序を
  // applyWheelOutputs() へ閉じ込める（compute()/commit() を直接呼ぶ経路は
  // これ以外に作らない）。ウォッチドッグ発火中は目標速度を無視し、PID を
  // holdReset の経路に置く（直前の指令を継続実行しない、要件 13.5）。
  applyWheelOutputs(targets.mm_s, dt_ms, ceiling, watchdog_tripped, last_commanded_duty_);

  // Lock: 保護①（モータロック）を輪ごとに1回進める。判定入力は「上限
  // 適用後・遮断前」の出力指令（last_commanded_duty_、極性適用前）である
  // （design.md「フローの決定事項」）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    protection_.get().updateLock(i, last_commanded_duty_[i], last_measured_mm_s_[i], now);
  }

  // Sup: 直近の updateLowVoltage/updateWatchdog/updateLock の結果と、
  // configured/output_enabled/has_command を合成する（検出器は呼び直さ
  // ない）。
  const GateOutcome outcome =
      protection_.get().compose(configured_, command_input_.get().outputEnabled(), targets.valid);

  // Gate: 理由が立つ輪のデューティを遮断値（0）にする。
  WheelOutputs outputs{};
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    outputs.duty[i] = last_commanded_duty_[i];
  }
  ProtectionSupervisor::applyGate(outcome, outputs);

  // Polarity: 出力極性パラメータの適用は最後のこの1箇所だけで行う
  // （要件 6.6 を出力側で担保する）。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    outputs.duty[i] *= static_cast<float>(config_.output.polarity[i]);
  }

  // Write: MotorOutputPort へ書き出す。
  ports_.motor->write(outputs);

  // Snap: StepResult を確定させて返す。
  StepResult result{};
  result.outputs = outputs;
  result.global_reasons = outcome.global_reasons;
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    result.wheel_reasons[i] = outcome.wheel_reasons[i];
  }
  result.outputs_written = true;

  last_result_ = result;
  return last_result_;
}

void DrivetrainController::applyWheelOutputs(const float targets_mm_s[kWheelCount], DurationMs dt_ms,
                                              float ceiling, bool watchdog_tripped,
                                              float out_applied_duty[kWheelCount]) noexcept {
  if (watchdog_tripped) {
    // design.md「フローの決定事項」: 発火中は CommandInput の保持する輪
    // 目標速度が PID へ渡らない。目標を 0 とし、PID を holdReset の経路に
    // 置く。VelocityPid の Preconditions（velocity_pid.hpp）は同一ステップ
    // 内で compute()/commit() と holdReset() を混在させることを禁じるため、
    // ここでは compute()/commit() を一切呼ばない。
    for (std::uint8_t i = 0; i < kWheelCount; ++i) {
      pid_[i].get().holdReset(last_measured_mm_s_[i]);
      out_applied_duty[i] = 0.0f;
    }
    return;
  }

  // compute(): 上限でクランプしない生の出力を3輪ぶん算出する。
  float raw[kWheelCount];
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    raw[i] = pid_[i].get().compute(targets_mm_s[i], last_measured_mm_s_[i], dt_ms);
  }

  // Scale: 輪ごとの個別クリップではなく、3輪まとめての等比縮小として PWM
  // 上限を適用する（要件 6.5, 12.4）。すでに上限以内なら scaleToLimit() は
  // 値を変更しない（呼び分けを条件分岐で書く必要が無い）。
  scaleToLimit(raw, ceiling);

  // commit(): 各輪は自分自身の（縮小後の）値で確定する。これにより
  // commit() 内部のアンチワインドアップ判定（「この輪の値が群としての
  // 縮小で小さくされたか」）が輪ごとに正しく機能する。
  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    pid_[i].get().commit(raw[i]);
    out_applied_duty[i] = raw[i];
  }
}

DrivetrainStatus DrivetrainController::status() const noexcept {
  // 副作用を持たず、ポートを読まず、ロックを取らない（要件 17.7。
  // controller.hpp 冒頭の status() コメント参照）。
  DrivetrainStatus status{};
  status.now_ms = last_step_ms_;
  status.configured = configured_;

  if (!configured_) {
    // 合成オブジェクト（kinematics_/odometry_/command_input_/protection_/
    // pwm_ceiling_/pid_）は configure() が成功したときだけ placement new
    // で構築される。未設定のときにこれらへ触れるのは未定義動作になるため、
    // 一切参照せず、既定値（configured=false 以外はすべてゼロ相当）の
    // スナップショットを返す。
    return status;
  }

  status.output_enabled = command_input_.get().outputEnabled();
  status.last_command_clamped = command_input_.get().lastCommandClamped();

  const WheelTargets& targets = command_input_.get().latched();
  status.has_valid_command = targets.valid;
  status.last_command_at_ms = targets.issued_at_ms;

  for (std::uint8_t i = 0; i < kWheelCount; ++i) {
    status.wheel_target_mm_s[i] = targets.mm_s[i];
    status.wheel_measured_mm_s[i] = last_measured_mm_s_[i];
    // last_result_.outputs.duty はゲート適用後・出力極性適用後の最終値
    // （実際に MotorOutputPort::write() へ渡した値と同一）。「上限適用後・
    // 遮断前」の last_commanded_duty_（MotorLockDetector の判定入力専用）
    // とは異なる（controller.hpp 冒頭 status() コメント参照）。
    status.wheel_duty[i] = last_result_.outputs.duty[i];
    status.encoder_count[i] = last_encoder_counts_.count[i];
    status.wheel_reasons[i] = last_result_.wheel_reasons[i];
  }

  status.pwm_ceiling = last_pwm_ceiling_;
  status.battery_milli_volts = protection_.get().averagedBatteryMilliVolts();
  status.battery_valid = protection_.get().batteryVoltageValid();
  status.low_voltage_state = last_low_voltage_state_;

  status.global_reasons = last_result_.global_reasons;

  status.odometry = odometry_.get().state();

  status.last_step_interval_ms = last_step_interval_ms_;
  status.command_apply_latency_ms = command_apply_latency_ms_;

  return status;
}

}  // namespace drivetrain_control
