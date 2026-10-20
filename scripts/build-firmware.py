#!/usr/bin/env python3
"""Build the selected entries from build.yaml with the installed ZMK toolchain."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ("cosmos_lemon_wireless", "cosmos_lemon_wireless_v4")


def entries(keyboard=None, artifact=None, board=None):
    # Available in the pinned SDK and the CI matrix job.
    import yaml
    matrix = yaml.safe_load((ROOT / "build.yaml").read_text())["include"]
    selected = [dict(row, board=board or row["board"]) for row in matrix
                if (keyboard is None or row.get("keyboard") == keyboard)
                and (artifact is None or row["artifact"] == artifact)]
    if not selected:
        raise ValueError("No build.yaml entries match the requested selection")
    return selected


def command(row):
    build = ROOT / "build" / f'{row["artifact"]}-{row["board"]}'
    args = ["west", "build", "-p", "always", "-s", str(ROOT / "zmk/app"),
            "-d", str(build), "-b", row["board"]]
    if row.get("snippet"):
        args += ["-S", row["snippet"]]
    args += ["--", f"-DZephyr_DIR={ROOT / 'zephyr/share/zephyr-package/cmake'}",
             f'-DSHIELD={row["shield"]}', f"-DZMK_CONFIG={ROOT / 'config'}",
             f"-DZMK_EXTRA_MODULES={ROOT / 'config'}"]
    if row.get("snippet"):
        args += [f"-DEXTRA_CONF_FILE={ROOT / 'config/diagnostics.conf'}"]
    return args, build


def run(args, **kwargs):
    print("+ " + " ".join(map(str, args)), flush=True)
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)


def build(row):
    args, directory = command(row)
    artifact = ROOT / "artifacts" / directory.name
    # Remove only this target's old exported output before building. A failure
    # must not leave an old UF2 looking like a successful result.
    if artifact.exists():
        shutil.rmtree(artifact)
    run(args)
    manifest = run(["python3", "scripts/freeze-manifest.py"], capture_output=True, text=True).stdout
    (directory / "manifest-frozen.yml").write_text(manifest)
    artifact.mkdir(parents=True)
    shutil.copy2(directory / "manifest-frozen.yml", artifact)
    (artifact / "build-target.json").write_text(json.dumps(row, indent=2) + "\n")
    (artifact / "zephyr").mkdir()
    for name in ("zmk.uf2", "zephyr.dts", ".config", "zmk.map", "zmk.elf"):
        shutil.copy2(directory / "zephyr" / name, artifact / "zephyr" / name)
    print(f"Built {artifact / 'zephyr/zmk.uf2'}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--keyboard", choices=("darkblue", "roseblue"))
    selection.add_argument("--artifact", help="One exact artifact name from build.yaml")
    parser.add_argument("--board", choices=BOARDS)
    parser.add_argument("--list", action="store_true", help="Print the selected matrix; do not build")
    parser.add_argument("--no-update", action="store_true", help="Use an already initialized west workspace")
    args = parser.parse_args()
    try:
        selected = entries(args.keyboard, args.artifact, args.board)
    except ValueError as exc:
        parser.error(str(exc))
    if args.list:
        print(json.dumps({"include": selected}, indent=2))
        return
    if not args.no_update:
        if not (ROOT / ".west/config").exists():
            run(["west", "init", "-l", "config"])
        run(["west", "update", "--fetch-opt=--filter=tree:0"])
    # Avoid a per-user zephyr-export registration; usable by an unprivileged
    # Docker UID as well as CI and a native Zephyr SDK installation.
    os.environ["ZEPHYR_BASE"] = str(ROOT / "zephyr")
    for row in selected:
        build(row)


if __name__ == "__main__":
    main()
