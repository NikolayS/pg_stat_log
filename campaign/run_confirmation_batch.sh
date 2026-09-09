#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

# Sequential, preregistered batch. Caller must own the exclusive VM slot.
source_root="${1:?usage: run_confirmation_batch.sh SOURCE_ROOT OUTPUT_ROOT}"
output_root="${2:?usage: run_confirmation_batch.sh SOURCE_ROOT OUTPUT_ROOT}"
if [[ -e "${output_root}" ]]; then
  echo "refusing existing output root: ${output_root}" >&2
  exit 2
fi
mkdir -p -- "${output_root}"
common=(
  python3 "${source_root}/campaign/benchmark.py"
  --pg-bin /home/bench/pg-release/bin
  --postgres-source /home/bench/postgres
  --forge /home/bench/forge/bin/forge
)

run_phase() {
  local name="$1"
  shift
  printf '%s\t%s\tstarted\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${name}" >> "${output_root}/batch-status.tsv"
  if "${common[@]}" --output "${output_root}/${name}" "$@"; then
    printf '%s\t%s\tpassed\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${name}" >> "${output_root}/batch-status.tsv"
  else
    local result=$?
    printf '%s\t%s\tfailed:%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${name}" "${result}" >> "${output_root}/batch-status.tsv"
    exit "${result}"
  fi
}

run_phase primary \
  --phase confirmation --blocks 5 --trials 3 --duration 30 --warmup 10 \
  --clients 1,8 --workloads quiet,hot --treatments baseline,disabled,on \
  --log-sink file
run_phase saturation \
  --phase confirmation --blocks 5 --trials 3 --duration 30 --warmup 10 \
  --clients 8,16 --workloads full_miss --treatments baseline,on \
  --log-sink file
run_phase devnull \
  --phase confirmation --blocks 5 --trials 3 --duration 30 --warmup 10 \
  --clients 8,16 --workloads hot --treatments baseline,disabled,on \
  --log-sink devnull
run_phase optional_paths \
  --phase pilot --blocks 1 --trials 3 --duration 5 --warmup 3 \
  --clients 1,8 --workloads reader,readwrite \
  --treatments baseline,disabled,filtered,on --log-sink file
