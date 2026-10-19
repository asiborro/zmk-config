"""Apply startup timing, recovery and passive diagnostics to the pinned driver."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile


REVISION = "1d9c2c68ca76012e1b1e5f6ef02fa5eadc4ca399"
PATCH = Path(__file__).resolve().parents[1] / "patches/pmw3610-startup-retries.patch"
PATCHES = (PATCH, PATCH.with_name("pmw3610-quality-observer.patch"),
           PATCH.with_name("pmw3610-write-clock-delay.patch"))


def git(driver, *args):
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(driver), *args],
        capture_output=True, text=True,
    )


def is_applied(driver, patches=PATCHES):
    # The patches overlap in the init callback. Checking each reverse diff
    # independently cannot recognize the final tree; compare with a scratch
    # tree produced by applying the complete stack to the pinned sources.
    names = ("src/pmw3610.c", "src/pixart.h", "src/pmw3610_diagnostics.h")
    with tempfile.TemporaryDirectory(prefix="pmw-patches-") as directory:
        root = Path(directory)
        (root / "src").mkdir()
        for name in names[:2]:
            original = git(driver, "show", REVISION + ":" + name)
            if original.returncode:
                return False
            (root / name).write_text(original.stdout)
        for patch in patches:
            result = git(root, "apply", str(patch))
            if result.returncode:
                raise RuntimeError(f"Invalid patch stack: {result.stderr.strip()}")
        for name in names:
            actual, expected = Path(driver) / name, root / name
            if actual.exists() != expected.exists():
                return False
            if expected.exists() and actual.read_bytes() != expected.read_bytes():
                return False
        return True


def apply_patch(driver):
    revision = git(driver, "rev-parse", "HEAD")
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise RuntimeError(f"PMW3610 patch requires Inorichi revision {REVISION}")
    # Recognize complete prefixes, including caches from earlier firmware.
    # Reverse-checking one overlapping patch cannot recognize a later stack.
    applied_count = next((count for count in range(len(PATCHES), -1, -1)
                          if is_applied(driver, PATCHES[:count])), None)
    if applied_count is None:
        raise RuntimeError("PMW3610 source differs from the pinned driver and known patch stacks")
    for patch in PATCHES[applied_count:]:
        checked = git(driver, "apply", "--check", str(patch))
        if checked.returncode:
            raise RuntimeError(f"Cannot apply {patch.name}: {checked.stderr.strip()}")
        applied = git(driver, "apply", str(patch))
        if applied.returncode:
            raise RuntimeError(applied.stderr.strip())


def patch_metadata(driver):
    if not is_applied(driver):
        return []
    return [{
        "path": "patches/" + patch.name,
        "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
    } for patch in PATCHES]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    try:
        apply_patch(args.driver)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    print("PMW3610 patches ready: clock startup delay, two retries and passive quality observers")
