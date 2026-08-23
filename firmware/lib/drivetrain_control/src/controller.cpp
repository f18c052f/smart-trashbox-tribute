#include "drivetrain_control/controller.hpp"

#include "drivetrain_control/units.hpp"

// drivetrain_control DrivetrainController implementation (design.md L10
// "DrivetrainController", タスク 6.2「制御ステップの骨格（設定・時刻・
// 計測）」). See controller.hpp for the full contract, preconditions, and
// the placement-new-based composition rationale. 要件 3.1, 3.2, 3.4, 3.5,
// 3.6, 15.4。

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
  last_step_ms_ = now;
  last_result_ = StepResult{};

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

  // タスク 6.2 は「骨格」のみ。PID の compute/commit、PWM 上限、等比縮小、
  // 保護①〜④の合成・遮断、出力極性の適用、MotorOutputPort への書き出しは
  // タスク 6.3 が配線する。ここでは出力デューティは既定値（全ゼロ）の
  // StepResult を返す。
  StepResult result{};
  last_result_ = result;
  return last_result_;
}

}  // namespace drivetrain_control
