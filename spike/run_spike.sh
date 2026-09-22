#!/usr/bin/env bash
# LowBit Trace Corpus -- Spike backend (RISC-V ISA simulator)
# ---------------------------------------------------------
# Lives in spike/, alongside this backend's own results. Cross-compiles
# every *.c program in ../programs for RISC-V and runs it under
# `spike` (the official `riscv-isa-sim` golden-model simulator), via
# the RISC-V proxy kernel (`pk`).
#
# NOT execution-verified in the environment that wrote this script (no
# spike/pk install available there) -- same status as gem5/GEM5.md's
# own admission. Treat this as a documented starting point and adjust
# to your installed spike/pk versions if a flag doesn't match.
#
# WHERE THIS SITS RELATIVE TO THE OTHER BACKENDS (see ../README.md):
#   - Like qemu/, spike is a FUNCTIONAL simulator by default: it
#     executes RISC-V instructions correctly but does not model a
#     pipeline, branch predictor, or cache hierarchy. There is no
#     "misprediction" or "cache miss" happening at the architectural
#     level to measure -- spike will run the benchmark and reproduce
#     its own self-reported taken/nottaken/sink/roi_elapsed_seconds
#     fields, but there is no timing model the way gem5's CPU models
#     provide.
#   - Unlike qemu-riscv64 (which translates guest RISC-V into host
#     x86-64 and runs it, so a `perf stat` wrapped around the process
#     measures the HOST), spike is an interpretive ISA simulator that
#     does not go through your host's real branch predictor or cache
#     the same way -- wrapping `perf stat` around a spike invocation
#     mostly tells you about the spike PROCESS itself, not about guest
#     RISC-V or host hardware behavior in any meaningful sense. This
#     script does NOT run perf around spike for that reason; see
#     REPORT.md in this folder.
#   - What spike is actually good for in this corpus: a second,
#     independent GOLDEN-MODEL correctness check of the benchmark
#     binaries (do taken/nottaken/validation match what qemu-riscv64
#     reported?), and a source of `spike --log-commits`
#     instruction-by-instruction traces if you need real trace-level
#     ground truth rather than aggregate counters.
#
# Setup:
#   git clone https://github.com/riscv-software-src/riscv-isa-sim.git
#   cd riscv-isa-sim && mkdir build && cd build
#   ../configure --prefix=/opt/riscv
#   make -j$(nproc) && make install
#   git clone https://github.com/riscv-software-src/riscv-pk.git
#   cd riscv-pk && mkdir build && cd build
#   ../configure --prefix=/opt/riscv --host=riscv64-unknown-elf
#   make -j$(nproc) && make install
#   export SPIKE_ROOT=/opt/riscv
#   export PATH="$SPIKE_ROOT/bin:$PATH"
#
# Usage:
#   ./run_spike.sh sample 20        # evenly-spaced sample of ~20 programs
#   ./run_spike.sh list 057 112 301 # specific program IDs
#   ./run_spike.sh all              # all 500

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BUILD_DIR="$SCRIPT_DIR/bin_spike"
OUT_DIR="$SCRIPT_DIR/spike_traces"
mkdir -p "$BUILD_DIR" "$OUT_DIR"

if [ ! -d "$PROGRAMS_DIR" ]; then
    echo "Programs directory not found at $PROGRAMS_DIR"
    echo "This script expects the corpus layout: <repo>/programs/*.c and <repo>/spike/$(basename "${BASH_SOURCE[0]}")"
    exit 1
fi

SPIKE_N="${SPIKE_N:-20000}"
SPIKE_SIZE="${SPIKE_SIZE:-4096}"
# Spike has no timing model, so cycles/IPC/branch-miss/cache-miss are
# not obtainable here -- see the header above. What Spike DOES give
# for free is an exact, zero-sampling-error count of retired
# instructions via its golden-model commit log. That's genuinely
# useful ground truth (better than perf's sampled/derived counts on
# the other backends), so we capture it. Set to 0 to skip if you only
# want the original functional-correctness-only behavior back.
SPIKE_LOG_COMMITS="${SPIKE_LOG_COMMITS:-1}"
# The commit log can be large for high SPIKE_N; keep it by default
# only if you want to inspect it later, otherwise it's deleted right
# after the exact instruction count is extracted from it.
SPIKE_KEEP_COMMIT_LOG="${SPIKE_KEEP_COMMIT_LOG:-0}"

RVGCC=""
for cand in riscv64-unknown-elf-gcc riscv64-unknown-linux-gnu-gcc riscv64-linux-gnu-gcc; do
    if command -v "$cand" >/dev/null 2>&1; then RVGCC="$cand"; break; fi
done
if [ -z "$RVGCC" ]; then
    echo "No RISC-V cross compiler found (riscv64-*-gcc)."
    exit 1
fi
if ! command -v spike >/dev/null 2>&1; then
    echo "spike not found on PATH. See the setup block at the top of this script."
    exit 1
fi
PK_BIN="$(command -v pk 2>/dev/null || true)"
if [ -z "$PK_BIN" ]; then
    echo "pk (RISC-V proxy kernel) not found on PATH. See the setup block above."
    exit 1
fi

# -static not required for pk-hosted binaries, but harmless and keeps
# behavior consistent with the other backends. riscv64-unknown-elf-gcc
# targets bare-metal/pk directly; if using a *-linux-gnu-gcc instead,
# keep -static so spike+pk don't need a dynamic linker.
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -static -DLOWBIT_BACKEND=\"spike\" -D_POSIX_C_SOURCE=199309L -I$PROGRAMS_DIR"

build_and_run_one() {
    local src="$1"
    local base
    base="$(basename "${src%.c}")"
    local bin="$BUILD_DIR/$base"
    local outfile="$OUT_DIR/${base}.spike.txt"

    echo "Building for spike (SIZE=$SPIKE_SIZE, N=$SPIKE_N): $base"
    $RVGCC $CFLAGS -DSIZE="$SPIKE_SIZE" -DN="$SPIKE_N" -o "$bin" "$src" 2>"$OUT_DIR/${base}.build.log"
    if [ ! -f "$bin" ]; then
        echo "  BUILD FAILED -- see $OUT_DIR/${base}.build.log"
        return
    fi

    echo "Running under spike: $base"
    if [ "$SPIKE_LOG_COMMITS" -eq 1 ]; then
        local commitlog="$OUT_DIR/${base}.commits.log"
        # --log-commits writes one line per retired instruction to
        # stderr in the form "core   0: 0x... (0x...) <mnemonic>";
        # -l alone (without --log-commits) is a coarser trace and
        # won't match the grep below, so both flags are required.
        {
            echo "backend_declared=spike"
            echo "size_override=$SPIKE_SIZE"
            echo "n_override=$SPIKE_N"
            spike -l --log-commits "$PK_BIN" "$bin" 2>"$commitlog"
        } > "$outfile"
        local instret
        instret=$(grep -c '^core' "$commitlog" 2>/dev/null || echo 0)
        echo "spike_exact_instructions_retired=$instret" >> "$outfile"
        if [ "$SPIKE_KEEP_COMMIT_LOG" -eq 0 ]; then
            rm -f "$commitlog"
        fi
    else
        {
            echo "backend_declared=spike"
            echo "size_override=$SPIKE_SIZE"
            echo "n_override=$SPIKE_N"
            spike "$PK_BIN" "$bin"
        } > "$outfile" 2>&1
    fi
}

MODE="${1:-sample}"
shift || true

case "$MODE" in
    sample)
        COUNT="${1:-20}"
        mapfile -t ALL_SRCS < <(ls "$PROGRAMS_DIR"/*.c | sort)
        TOTAL=${#ALL_SRCS[@]}
        STEP=$(( TOTAL / COUNT ))
        [ "$STEP" -lt 1 ] && STEP=1
        i=0
        for s in "${ALL_SRCS[@]}"; do
            if [ $((i % STEP)) -eq 0 ]; then build_and_run_one "$s"; fi
            i=$((i + 1))
        done
        ;;
    list)
        if [ "$#" -eq 0 ]; then
            echo "Usage: $0 list ID [ID ...]   e.g. $0 list 057 112 301"
            exit 1
        fi
        for id in "$@"; do
            match=$(ls "$PROGRAMS_DIR" | grep "^${id}_.*\.c$" | head -1)
            if [ -z "$match" ]; then
                echo "No source found matching ID $id in $PROGRAMS_DIR"
                continue
            fi
            build_and_run_one "$PROGRAMS_DIR/$match"
        done
        ;;
    all)
        for s in "$PROGRAMS_DIR"/*.c; do
            build_and_run_one "$s"
        done
        ;;
    *)
        echo "Usage: $0 [sample [COUNT] | list ID [ID ...] | all]"
        exit 1
        ;;
esac

echo "spike traces written to: $OUT_DIR"
echo "Parse with: python3 $SCRIPT_DIR/parse_spike_traces_to_csv.py $OUT_DIR $SCRIPT_DIR/lowbit_traces_spike.csv"