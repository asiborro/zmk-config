"""Apply our bounded-startup patch to the pinned Inorichi driver."""

import argparse
import hashlib
from pathlib import Path
import subprocess


REVISION = "1d9c2c68ca76012e1b1e5f6ef02fa5eadc4ca399"
PATCH = Path(__file__).resolve().parents[1] / "patches/pmw3610-startup-retries.patch"


def git(driver, *args):
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(driver), *args],
        capture_output=True, text=True,
    )


def is_applied(driver):
    return git(driver, "apply", "--reverse", "--check", str(PATCH)).returncode == 0


def apply_patch(driver):
    revision = git(driver, "rev-parse", "HEAD")
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise RuntimeError(f"PMW3610 patch requires Inorichi revision {REVISION}")
    if is_applied(driver):
        return
    checked = git(driver, "apply", "--check", str(PATCH))
    if checked.returncode:
        raise RuntimeError(f"Cannot apply PMW3610 startup patch: {checked.stderr.strip()}")
    applied = git(driver, "apply", str(PATCH))
    if applied.returncode:
        raise RuntimeError(applied.stderr.strip())


def patch_metadata(driver):
    if not is_applied(driver):
        return []
    return [{
        "path": "patches/" + PATCH.name,
        "sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest(),
    }]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    try:
        apply_patch(args.driver)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    print("PMW3610 startup patch ready: two retries, 500 ms between attempts")
