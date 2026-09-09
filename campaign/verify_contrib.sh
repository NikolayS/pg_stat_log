#!/usr/bin/env bash
# Run as the unprivileged benchmark user, only outside timed benchmark windows.
# Independent source/build/install directories: never modifies baseline binaries.
set -euo pipefail
umask 022
if (( $# != 3 )); then
  echo "usage: $0 BASELINE_POSTGRES_SOURCE EXACT_V1_PATCH OUTPUT_ROOT" >&2
  exit 2
fi
baseline=$(realpath "$1")
patch=$(realpath "$2")
mkdir -p "$3"
output=$(realpath "$3")
source_dir="$output/source"
build_dir="$output/build"
jobs=${CONTRIB_BUILD_JOBS:-8}
expected_sha=${POSTGRES_SHA:-86f7c82cf1023e3599f40f939727791a7090cd44}
if [[ $(id -u) == 0 ]]; then
  echo "Run as an unprivileged user; PostgreSQL test clusters reject root." >&2
  exit 2
fi
if [[ -e "$source_dir" || -e "$build_dir" ]]; then
  echo "Choose a fresh OUTPUT_ROOT; preserving existing evidence." >&2
  exit 2
fi
exec > >(tee "$output/verify.log") 2>&1
trap 'rc=$?; printf "exit_code=%s\n" "$rc" > "$output/status.txt"' EXIT
printf 'started_utc=%s\npostgres_sha=%s\n' "$(date -u +%FT%TZ)" "$expected_sha" > "$output/provenance.txt"
sha256sum "$patch" >> "$output/provenance.txt"
# Local clone borrows only immutable Git objects; working tree is independent.
git clone --shared --no-checkout "$baseline" "$source_dir"
git -C "$source_dir" checkout --detach "$expected_sha"
git -C "$source_dir" apply --check "$patch"
git -C "$source_dir" apply "$patch"
git -C "$source_dir" diff --stat > "$output/patch-stat.txt"
mkdir "$build_dir"
cd "$build_dir"
"$source_dir/configure" --prefix="$output/install" --enable-cassert --enable-debug --enable-tap-tests CFLAGS='-O2 -g' > "$output/configure.log" 2>&1
make -j "$jobs" > "$output/build.log" 2>&1
make -C contrib/pg_stat_log -j "$jobs" > "$output/contrib-build.log" 2>&1
# Native in-tree check installs a temporary server and runs SQL regression + TAP.
# NO_INSTALLCHECK in the submitted patch intentionally disables installcheck.
make -C contrib/pg_stat_log check > "$output/contrib-check.log" 2>&1
printf 'completed_utc=%s\n' "$(date -u +%FT%TZ)" >> "$output/provenance.txt"
echo "PASS: exact submitted contrib patch built and native check completed."
echo "Evidence: $output"
