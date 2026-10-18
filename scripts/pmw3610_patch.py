"""Apply startup recovery and passive diagnostics to the pinned Inorichi driver."""

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile


REVISION = "1d9c2c68ca76012e1b1e5f6ef02fa5eadc4ca399"
PATCH = Path(__file__).resolve().parents[1] / "patches/pmw3610-startup-retries.patch"
PATCHES = (PATCH, PATCH.with_name("pmw3610-quality-observer.patch"))


def git(driver, *args):
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(driver), *args],
        capture_output=True, text=True,
    )


def is_applied(driver):
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
        for patch in PATCHES:
            result = git(root, "apply", str(patch))
            if result.returncode:
                raise RuntimeError(f"Invalid patch stack: {result.stderr.strip()}")
        return all((Path(driver) / name).exists() and
                   (Path(driver) / name).read_bytes() == (root / name).read_bytes() for name in names)


def apply_patch(driver):
    revision = git(driver, "rev-parse", "HEAD")
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise RuntimeError(f"PMW3610 patch requires Inorichi revision {REVISION}")
    if is_applied(driver):
        return
    for patch in PATCHES:
        # Accept a west cache that already has the previous startup-only patch.
        if git(driver, "apply", "--reverse", "--check", str(patch)).returncode == 0:
            continue
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
    print("PMW3610 patches ready: two startup retries and passive quality observers")
