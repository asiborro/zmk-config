"""Apply the passive quality observer to the pinned PAW3395 wrapper."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

REVISION = "bffa61761dc1fb87d8bc04133c55a10bc899a359"
PATCH = Path(__file__).resolve().parents[1] / "patches/paw3395-quality-observer.patch"
NAMES = ("src/paw3395.c", "src/paw3395_diagnostics.h")


def git(driver, *args):
    return subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(driver), *args],
                          capture_output=True, text=True)


def is_applied(driver):
    with tempfile.TemporaryDirectory(prefix="paw-quality-") as directory:
        root = Path(directory)
        (root / "src").mkdir()
        original = git(driver, "show", REVISION + ":src/paw3395.c")
        if original.returncode:
            return False
        (root / NAMES[0]).write_text(original.stdout)
        result = git(root, "apply", str(PATCH))
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        return all((Path(driver) / name).exists() and
                   (Path(driver) / name).read_bytes() == (root / name).read_bytes()
                   for name in NAMES)


def apply_patch(driver):
    revision = git(driver, "rev-parse", "HEAD")
    if revision.returncode or revision.stdout.strip() != REVISION:
        raise RuntimeError(f"PAW3395 patch requires badjeff revision {REVISION}")
    if is_applied(driver):
        return
    if git(driver, "diff", "--exit-code", REVISION, "--", *NAMES).returncode or (Path(driver) / NAMES[1]).exists():
        raise RuntimeError("PAW3395 sources differ from the pinned driver and known quality patch")
    result = git(driver, "apply", "--check", str(PATCH))
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    result = git(driver, "apply", str(PATCH))
    if result.returncode:
        raise RuntimeError(result.stderr.strip())


def patch_metadata(driver):
    return [{"path": "patches/" + PATCH.name,
             "sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest()}] if is_applied(driver) else []


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", type=Path)
    args = parser.parse_args()
    try:
        apply_patch(args.driver)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
    print("PAW3395 passive quality observer ready (12-byte motion burst)")
