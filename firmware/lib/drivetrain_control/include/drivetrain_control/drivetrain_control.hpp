#pragma once

// drivetrain_control public entry header (design.md L11 / PublicApi).
//
// ⚠️ PLACEHOLDER — drivetrain-core task 1.1 only.
//
// This header exists solely to prove that the three build configurations
// (teleop / production / native) can all compile and link against a real
// unit living in this co-located CMakeLists.txt + library.json directory
// (see research.md "Decision: 純ロジックを「2マニフェスト同居ディレクトリ」
// として配置する"). It is NOT the final public API.
//
// Task 6.5 replaces this file with the real public entry point described
// in design.md "Components and Interfaces" (PublicApi, L11): the
// DrivetrainController-based surface that re-exports L0-L10 types. Do not
// treat placeholder_link_check() below as part of the eventual design —
// it is a minimal, real, linkable free function and nothing more.

namespace drivetrain_control {

// Minimal linkable unit for the build-skeleton proof (task 1.1).
// Superseded by the real public API in task 6.5.
int placeholder_link_check() noexcept;

}  // namespace drivetrain_control
