"""teleop-bringup task 5.1: fetch and integrate Bluepad32 (raw ESP-IDF
platform) + BTstack into firmware/.deps/, scoped to [env:teleop] only
(requirements.md 7.1; design.md ControllerLink "External: Bluepad32 / BTstack
(P0)" / Technology Stack "Bluepad32 (raw ESP-IDF platform) + BTstack").

Why this exists (do not delete without re-reading): research.md's Bluepad32
research log and design.md's ControllerLink Implementation Notes both flag
that BTstack cannot be pulled in as a plain IDF component -- there is no
idf_component.yml dependency that resolves it. Bluepad32's own docs
(docs/plat_esp32.md) and the pinned btstack commit's port/esp32/README.md
require a scripted two-step process, verified against the actual upstream
sources during this task (not from documentation alone):

  1. Patch BTstack's ESP32 port: `external/patches/*.patch` (shipped inside
     the bluepad32 repo) applied with `git apply` from inside
     `external/btstack` (a git submodule of bluepad32, pinned to a specific
     commit by bluepad32's own tree at BLUEPAD32_REF).
  2. Run `external/btstack/port/esp32/integrate_btstack.py`. Its own source
     (fetched and read during this task) unconditionally writes to
     "$IDF_PATH/components/btstack" -- it does not have an "install inside
     Bluepad32 instead" mode. Bluepad32's example CMakeLists.txt comment
     achieves that outcome by *overriding* IDF_PATH for just this one
     subprocess call, pointed at Bluepad32's own src/ directory:
     `IDF_PATH=../../../../src ./integrate_btstack.py`, run from
     external/btstack/port/esp32. Four `../` from that directory resolves
     to <bluepad32 checkout>/src, so the script writes to
     src/components/btstack instead of touching the real ESP-IDF tree at
     all. This function reproduces that exact trick, with IDF_PATH
     overridden only for this one subprocess invocation -- the *real*
     IDF_PATH (needed later, by the actual firmware build) is left alone.

Reproducibility (research.md Risks "BTstack の設置がスクリプト依存" --
"手元では通るが他の環境で再現しない" is the failure mode being defended
against here): this script pins Bluepad32 to an immutable git TAG, not a
branch or HEAD (BLUEPAD32_REF below) -- the same "pin to an immutable
reference" discipline platformio.ini already applies to the ESP-IDF platform
package (requirement 1.7 / find_mutable_platform_reference). `git clone
--recurse-submodules` then resolves BTstack to the exact submodule commit
that tag's tree records; both resolved commit hashes are written to
_MARKER_PATH after a successful integration -- both as an idempotency check
(skip re-fetching when a matching integration already exists) and as an
audit trail of exactly which upstream commit produced the tree in front of
you.

Not vendored into git (deliberate; see README.md "ライセンスと再配布上の
制約" / research.md "Decision: 無線ライブラリのライセンス表明の出典"):
BTstack is not open source -- its license permits personal use only, not
commercial use or monetary gain. Committing its source into this
repository's git history would redistribute it as part of the repository
regardless of which build profile a given clone ever actually builds.
Fetching it on demand into a gitignored directory (firmware/.gitignore's
`.deps/` entry), scoped to run only from [env:teleop]'s extra_scripts
(platformio.ini), keeps the encumbrance confined the same way object code
containment does for the link result (requirements.md 1.2 / 1.6):
`[env:production]` never sets DRIVETRAIN_BUILD_TELEOP, so nothing about this
script -- nor the directory it writes to -- is ever reached from a
production-only build, and a clone that never builds [env:teleop] never
fetches BTstack's source onto disk at all.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

Import("env")  # noqa: F821 -- PlatformIO injects this at exec time

# ⚠️ Not Path(__file__): PlatformIO executes extra_scripts via SCons'
# SConscript(), which exec()s the script body directly (no module import),
# so __file__ is not defined here (verified empirically -- NameError).
# env["PROJECT_DIR"] is PlatformIO's own project-root property (this
# project's platformio.ini lives in firmware/, so PROJECT_DIR *is*
# FIRMWARE_DIR), the same kind of PlatformIO-injected value
# set_build_profile_cmake_env.py relies on (env["PIOENV"]).
FIRMWARE_DIR = Path(env["PROJECT_DIR"])  # noqa: F821
DEPS_DIR = FIRMWARE_DIR / ".deps"
BLUEPAD32_DIR = DEPS_DIR / "bluepad32"
_MARKER_PATH = BLUEPAD32_DIR / ".fetch_bluepad32.json"

BLUEPAD32_REPO = "https://github.com/ricardoquesada/bluepad32.git"
# Immutable tag, not a branch: same discipline as platformio.ini's ESP-IDF
# platform pin. Newest tagged GitHub Release available at implementation
# time (2026-09; note the "release_vX.Y.Z" naming used through 3.10.3 was
# dropped starting with the 4.x series, whose tags are the bare version
# number). Verified empirically during this task against the pinned
# pioarduino platform-espressif32 55.03.311 (ESP-IDF 5.5.5) toolchain:
# release_v3.10.3 fails to compile there --
# src/components/bluepad32/uni_esp32.c references the newlib-internal
# symbols __sf_fake_stdin/__sf_fake_stdout/__sf_fake_stderr without
# declaring them, which this repo's newer toolchain-xtensa-esp-elf
# (14.2.0+20260121) no longer exposes via any public header (confirmed:
# the symbols still exist as real, valid link targets -- ESP-IDF's own
# esp_rom/esp32/ld/esp32.rom.newlib-data.ld defines
# __sf_fake_stdin/__sf_fake_stdout/__sf_fake_stderr at fixed ROM
# addresses -- so this is a missing-declaration/toolchain-version
# mismatch upstream, not a linkable-symbol problem). Upstream removed the
# whole UART-disable code path that referenced these symbols in a later
# refactor (the file moved to arch/uni_system_esp32.c, which contains no
# such reference) -- 4.2.0 is the newest tagged release carrying that fix.
BLUEPAD32_REF = "4.2.0"

# teleop-bringup task 7.1 (requirements.md 10.6; design.md "BenchApp";
# research.md "Decision: E-3 の開ループ確認を teleop 系の第3プロファイルと
# して分離する"): [env:bench] keeps DRIVETRAIN_BUILD_TELEOP defined (same
# "teleop family" framing as set_build_profile_cmake_env.py's
# _PROFILE_ENV_VARS_BY_PIOENV), so firmware/src/CMakeLists.txt's
# DRIVETRAIN_BUILD_TELEOP-gated REQUIRES still adds bluepad32 to the "src"
# component for [env:bench] too -- and the teleop/*.cpp glob it compiles
# still includes controller_link.cpp/teleop_app.cpp (both #include <uni.h>),
# even though app_main only ever references BenchApp there. Those
# translation units must still be *compilable* (Bluepad32's headers must be
# discoverable), so this script must still run for [env:bench]. bench_app.cpp
# itself never includes Bluepad32 headers -- the actual isolation claim
# (requirement 10.6: no PCNT/core/Bluepad32 in the bench *link*) is verified
# against .pio/build/bench/firmware.map instead, not by skipping this fetch.
_EXPECTED_PIOENVS = frozenset({"teleop", "bench"})


def _rmtree_force(path: Path) -> None:
    """`shutil.rmtree` that can also remove a git checkout on Windows.

    ⚠️ Verified empirically: a plain `shutil.rmtree` on a previous
    `.deps/bluepad32` checkout fails with
    `PermissionError: [WinError 5]` on
    `.git/modules/external/btstack/objects/pack/pack-*.idx`. Git marks pack
    files read-only, and on Windows the read-only attribute blocks
    `os.unlink` outright (on POSIX the *directory* write bit is what
    governs removal, so the same tree deletes fine there -- which is why
    this only ever bites Windows users).

    The handler clears the read-only bit and retries the operation that
    failed. Anything that still fails after that is a real error and is
    allowed to propagate.
    """

    def _on_error(func, target, _exc_info):  # type: ignore[no-untyped-def]
        os.chmod(target, stat.S_IWRITE)
        func(target)

    # `onerror` (not `onexc`) because PlatformIO pins Python 3.11 here; the
    # newer `onexc` spelling does not exist before 3.12.
    shutil.rmtree(path, onerror=_on_error)


def _run(args: list[str], cwd: Path, extra_env: dict[str, str] | None = None) -> None:
    merged_env = dict(os.environ)
    if extra_env:
        merged_env.update(extra_env)
    subprocess.run(args, cwd=str(cwd), env=merged_env, check=True)


def _resolve_commit(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_dir),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _btstack_component_dir() -> Path:
    return BLUEPAD32_DIR / "src" / "components" / "btstack"


def _bluepad32_component_dir() -> Path:
    return BLUEPAD32_DIR / "src" / "components" / "bluepad32"


def _integration_artifacts_present() -> bool:
    """Filesystem-only check: do the two components integrate_btstack.py /
    the initial clone are supposed to produce actually exist?

    ⚠️ Deliberately independent of _MARKER_PATH: this is also used to verify
    a *just-completed* integration, before _MARKER_PATH has been written --
    gating it on the marker's own existence would make that post-integration
    check vacuously false every time (the bug this comment replaced).
    """
    return (
        (_btstack_component_dir() / "CMakeLists.txt").is_file()
        and (_bluepad32_component_dir() / "CMakeLists.txt").is_file()
    )


def _already_integrated() -> bool:
    if not _MARKER_PATH.is_file():
        return False
    try:
        marker = json.loads(_MARKER_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if marker.get("bluepad32_ref") != BLUEPAD32_REF:
        return False
    return _integration_artifacts_present()


def _apply_local_compat_patches() -> None:
    """Apply small, narrowly-scoped fixes for known incompatibilities between
    Bluepad32 4.2.0 (BLUEPAD32_REF) and this project's pinned, notably newer
    toolchain (pioarduino platform-espressif32 55.03.311 / ESP-IDF 5.5.5 /
    toolchain-xtensa-esp-elf 14.2.0+20260121).

    ⚠️ Every fix here was verified empirically (a real `pio run -e teleop`
    compile failure) and cross-checked against Bluepad32's own upstream
    `main` branch to confirm it is a real, later-fixed upstream bug and not
    a misunderstanding on this project's part -- these are not speculative
    patches. Each one names the upstream commit that eventually fixes it (no
    tagged release newer than 4.2.0 exists yet to pick up that fix instead
    of patching locally, as of this task).
    """
    # cmd_system.c (bundled inside Bluepad32's own component tree; not
    # BTstack) calls gpio_wakeup_enable() but only includes
    # <driver/rtc_io.h>, not <driver/gpio.h> where that function is actually
    # declared in ESP-IDF 5.x's split-out esp_driver_gpio component. Real
    # compile error observed: "implicit declaration of function
    # 'gpio_wakeup_enable'". Fixed upstream in commit e9b755f ("fix esp32
    # example build issue (#165)", 2025-06-12) by adding the missing
    # include -- that commit landed after the 4.2.0 release and has not
    # been included in any tagged release since.
    cmd_system_c = BLUEPAD32_DIR / "src" / "components" / "cmd_system" / "cmd_system.c"
    _insert_line_after(
        cmd_system_c,
        anchor="#include <driver/rtc_io.h>",
        new_line="#include <driver/gpio.h>",
    )


def _insert_line_after(path: Path, *, anchor: str, new_line: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new_line in text:
        return  # already present (e.g. a future BLUEPAD32_REF bump fixed it upstream)
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == anchor:
            newline = "\n" if line.endswith("\n") else ""
            lines.insert(i + 1, f"{new_line}{newline}")
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise RuntimeError(
        f"_insert_line_after: anchor line {anchor!r} not found in {path} -- "
        "upstream file layout changed; re-check the local compat patch "
        "against the current BLUEPAD32_REF."
    )


def fetch_and_integrate() -> None:
    if _already_integrated():
        print(
            f"[fetch_bluepad32] already integrated at {BLUEPAD32_DIR} "
            f"(ref={BLUEPAD32_REF}); skipping fetch"
        )
        return

    if BLUEPAD32_DIR.exists():
        print(f"[fetch_bluepad32] removing incomplete previous checkout at {BLUEPAD32_DIR}")
        _rmtree_force(BLUEPAD32_DIR)

    DEPS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"[fetch_bluepad32] cloning {BLUEPAD32_REPO} @ {BLUEPAD32_REF} (recursive, shallow)")
    # ⚠️ Disable end-of-line translation for this clone and every git
    # subprocess it spawns (submodules included).
    #
    # Verified empirically on Windows with a global `core.autocrlf=true`:
    # the checkout rewrites Bluepad32's own `external/patches/*.patch` to
    # CRLF, and the `git apply` below then fails with
    # "error: corrupt patch at line 20" -- git's patch parser rejects the
    # CR before the newline in the hunk body. The checked-out BTstack
    # sources would be CRLF too, so even a byte-correct patch could fail to
    # match its context lines. Both halves of the problem disappear if the
    # working tree is left as-is (LF).
    #
    # `-c` (as opposed to `--config`) is what propagates: git exports it via
    # GIT_CONFIG_PARAMETERS, which child git processes inherit -- that is
    # how `--recurse-submodules` picks it up for the BTstack submodule.
    # `--config` is kept as well so that any *later* git operation inside
    # the clone (e.g. a manual re-checkout while debugging) keeps the same
    # setting rather than silently reverting to the user's global value.
    #
    # ⚠️ This project's own `.gitattributes` cannot fix this: it governs
    # this repository, not a third-party repository cloned into `.deps/`.
    _run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "clone",
            "--config",
            "core.autocrlf=false",
            "--config",
            "core.eol=lf",
            "--branch",
            BLUEPAD32_REF,
            "--depth",
            "1",
            "--recurse-submodules",
            "--shallow-submodules",
            BLUEPAD32_REPO,
            str(BLUEPAD32_DIR),
        ],
        cwd=DEPS_DIR,
    )

    bluepad32_commit = _resolve_commit(BLUEPAD32_DIR)
    btstack_dir = BLUEPAD32_DIR / "external" / "btstack"
    btstack_commit = _resolve_commit(btstack_dir)

    _apply_local_compat_patches()

    patches_dir = BLUEPAD32_DIR / "external" / "patches"
    patches = sorted(patches_dir.glob("*.patch"))
    print(f"[fetch_bluepad32] applying {len(patches)} BTstack patch(es) from {patches_dir}")
    for patch in patches:
        _run(["git", "apply", str(patch)], cwd=btstack_dir)

    port_esp32_dir = btstack_dir / "port" / "esp32"
    print(
        "[fetch_bluepad32] running integrate_btstack.py "
        "(installs BTstack as a component inside Bluepad32's own src/, "
        "per docs/plat_esp32.md -- see module docstring for the IDF_PATH trick)"
    )
    _run(
        [sys.executable, "integrate_btstack.py"],
        cwd=port_esp32_dir,
        # ../../../../src, evaluated from btstack_dir/port/esp32, resolves to
        # BLUEPAD32_DIR/src -- redirects integrate_btstack.py's hardcoded
        # "$IDF_PATH/components/btstack" write target there instead of the
        # real ESP-IDF tree. Only this one subprocess call sees this
        # override; the real IDF_PATH used by the actual firmware build
        # (set by PlatformIO's espidf builder later in this same `pio run`)
        # is untouched.
        extra_env={"IDF_PATH": "../../../../src"},
    )

    if not _integration_artifacts_present():
        raise RuntimeError(
            "integrate_btstack.py ran but "
            f"{_btstack_component_dir()} was not produced as expected. "
            "Re-check external/btstack/port/esp32/integrate_btstack.py "
            "against BLUEPAD32_REF for upstream layout changes."
        )

    _MARKER_PATH.write_text(
        json.dumps(
            {
                "bluepad32_ref": BLUEPAD32_REF,
                "bluepad32_commit": bluepad32_commit,
                "btstack_commit": btstack_commit,
                "patches_applied": [p.name for p in patches],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"[fetch_bluepad32] integrated bluepad32@{bluepad32_commit[:12]} "
        f"btstack@{btstack_commit[:12]} -> {BLUEPAD32_DIR}"
    )


_pioenv = env["PIOENV"]  # noqa: F821
if _pioenv not in _EXPECTED_PIOENVS:
    raise RuntimeError(
        "fetch_bluepad32.py was invoked for unexpected PlatformIO environment "
        f"'{_pioenv}'. This script must only be referenced from [env:teleop] "
        "and [env:bench] extra_scripts in platformio.ini -- fetching BTstack "
        "for any other environment would defeat its containment to the "
        "teleop build family."
    )

fetch_and_integrate()
