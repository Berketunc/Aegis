#!/usr/bin/env bash
# Compile an ONNX model with the IBM Z Deep Learning Compiler (zDLC) container.
# Plan.md section 8.3. Produces a CPU build always and an NNPA build when asked,
# plus the per-operation statistics that show which ops run on the accelerator.
#
#   bash zdlc/compile.sh <model.onnx> [output-stem] [--cpu-only]
#
# Outputs, next to this script:
#   zdlc/build/<stem>-cpu.so            CPU-only shared library
#   zdlc/build/<stem>-nnpa.so           NNPA build (unless --cpu-only)
#   zdlc/op_stats/<stem>-{cpu,nnpa}.txt onnx-op-stats (zhigh.* = accelerator, onnx.* = CPU)
#   zdlc/build/PyRuntime*.so            ONNX-MLIR Python runtime copied out of the image
#
# Status: NOT YET RUN. Pulling icr.io/ibmz/zdlc failed with an authentication
# error on the team VM on 17 Oct 2026, so the deployed detector path uses the
# plan's PyTorch + torch_nnpa fallback. Keep this script so the retry is one
# command once the pull works (try `docker logout icr.io` first; the image is
# public and anonymous pulls normally succeed). Check the current tag and flags in
# https://github.com/IBM/zDLC before relying on the defaults below.
set -euo pipefail
if [[ $# -lt 1 ]]; then sed -n 2,20p "$0" >&2; exit 2; fi
model="$1"; shift
stem="${1:-model}"; [[ $# -gt 0 ]] && shift
cpu_only=false
for argument in "$@"; do
  case "$argument" in
    --cpu-only) cpu_only=true ;;
    *) echo "Unknown option: $argument" >&2; exit 2 ;;
  esac
done
[[ -f "$model" ]] || { echo "ONNX model not found: $model" >&2; exit 2; }
if [[ "$(uname -m)" != s390x ]]; then
  echo "zDLC emits s390x code; run this on the LinuxONE VM." >&2; exit 2
fi

: "${ZDLC_IMAGE:=icr.io/ibmz/zdlc:5.1.0}"   # check the README for the current tag
: "${ZDLC_MCPU:=z16}"                        # z16 or z17; newer tags may use --march
script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_dir="$script_dir/build"; stats_dir="$script_dir/op_stats"
mkdir -p "$build_dir" "$stats_dir"
work="$(mktemp -d "$build_dir/.work-XXXXXX")"
trap 'rm -rf "$work"' EXIT
cp "$model" "$work/model.onnx"

docker_command=(docker)
if ! docker info >/dev/null 2>&1; then docker_command=(sudo docker); fi
"${docker_command[@]}" pull "$ZDLC_IMAGE"

compile() {   # compile <label> [extra zDLC flags...]
  local label="$1"; shift
  echo "== zDLC $label build"
  "${docker_command[@]}" run --rm -v "$work:/workdir:z" "$ZDLC_IMAGE" \
    --EmitLib --O3 --mtriple=s390x-ibm-loz "--mcpu=$ZDLC_MCPU" "$@" \
    --onnx-op-stats TXT -o "/workdir/$stem-$label" /workdir/model.onnx \
    2>&1 | tee "$stats_dir/$stem-$label.txt"
  mv "$work/$stem-$label.so" "$build_dir/$stem-$label.so"
  echo "   -> $build_dir/$stem-$label.so ; op stats: $stats_dir/$stem-$label.txt"
}

compile cpu
if ! "$cpu_only"; then
  if grep -qw nnpa /proc/cpuinfo; then
    compile nnpa --maccel=NNPA
  else
    echo "No nnpa flag in /proc/cpuinfo: compiling the NNPA variant anyway for the op-stats evidence (it will not run here)."
    compile nnpa --maccel=NNPA
  fi
fi

# Python runtime for loading the .so (plan 8.3 step 4).
"${docker_command[@]}" run --rm -v "$build_dir:/files:z" --entrypoint /bin/sh "$ZDLC_IMAGE" \
  -c 'cp /usr/local/lib/PyRuntime*.so /files/ 2>/dev/null || cp /usr/local/lib/PyRuntime* /files/'
echo "Done. Load with: PYTHONPATH=$build_dir python3 -c 'from PyRuntime import OMExecutionSession as S; s=S(\"$build_dir/$stem-cpu.so\"); print(s.input_signature())'"
echo "Screenshot the op-stats files for the evidence pack (plan 12.3)."
