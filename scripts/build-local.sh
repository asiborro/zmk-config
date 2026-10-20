#!/usr/bin/env bash
# Same SDK, target matrix and build command as GitHub Actions.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_workspace=${ZMK_BUILD_WORKSPACE:-"$repo_dir"}
if [[ "$build_workspace" != "$repo_dir" ]]; then
  python3 "$repo_dir/scripts/stage-build.py" "$build_workspace"
  build_workspace=$(cd -- "$build_workspace" && pwd)
fi
sdk_image='zmkfirmware/zmk-build-arm:3.5@sha256:0979a8de325e3d822e48bd8d1d9efaee2984ad3750253119f920a342f0ce5cde'
exec docker run --rm --init \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$build_workspace,target=/work" \
  --workdir /work \
  "$sdk_image" python3 scripts/build-firmware.py "$@"
