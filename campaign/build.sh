#!/usr/bin/env bash
# Run unprivileged after installing build-essential bison flex libreadline-dev
# zlib1g-dev libicu-dev pkg-config libipc-run-perl python3 sysstat git.
set -euo pipefail
if (( $# != 2 )); then echo 'usage: build.sh POSTGRES_SOURCE OUTPUT_ROOT' >&2; exit 2; fi
source_dir=$(realpath "$1")
mkdir -p "$2"
output=$(realpath "$2")
extension=$(cd "$(dirname "$0")/.." && pwd)
for flavor in release assert; do
  mkdir "$output/build-$flavor"
  cd "$output/build-$flavor"
  extra=()
  flags='-O2 -g -fno-omit-frame-pointer'
  if [[ $flavor == assert ]]; then extra+=(--enable-cassert); flags='-O1 -g -fno-omit-frame-pointer'; fi
  "$source_dir/configure" --prefix="$output/pg-$flavor" --enable-debug --enable-tap-tests "${extra[@]}" CFLAGS="$flags" > configure.log 2>&1
  make -j "${BUILD_JOBS:-8}" > build.log 2>&1
  make install > install.log 2>&1
  # Distinct extension build copies avoid mixing .o files between prefixes.
  mkdir "$output/extension-$flavor"
  git -C "$extension" archive HEAD | tar -x -C "$output/extension-$flavor"
  cd "$output/extension-$flavor"
  make clean PG_CONFIG="$output/pg-$flavor/bin/pg_config" > build.log 2>&1
  make -j "${BUILD_JOBS:-8}" PG_CONFIG="$output/pg-$flavor/bin/pg_config" ERRCODES_FILE="$source_dir/src/backend/utils/errcodes.txt" >> build.log 2>&1
  make install PG_CONFIG="$output/pg-$flavor/bin/pg_config" >> build.log 2>&1
  make installcheck PG_CONFIG="$output/pg-$flavor/bin/pg_config" > installcheck.log 2>&1
  make -C campaign/hook_probe PG_CONFIG="$output/pg-$flavor/bin/pg_config" all install > hook-build.log 2>&1
done
