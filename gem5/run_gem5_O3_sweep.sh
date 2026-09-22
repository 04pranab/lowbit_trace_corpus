#!/usr/bin/env bash
# run_gem5_O3_sweep.sh -- seed / size / predictor sweep for O3CPU
# ---------------------------------------------------------------
# Dedicated O3CPU counterpart to run_gem5_MC_sweep.sh (MinorCPU) --
# separate script, separate output tree (bin_gem5_o3/, gem5_stats_o3/,
# matching this project's existing O3 naming convention), same tested
# logic: real predictor probing (not a --help text guess), non-empty
# stats.txt checking, FAILED_RUNS.txt logging, list-bp/probe/screen/
# sweep modes.
#
# CONFIRMED (2026-09), on this project's real gem5 build: explicit
# --bp-type selection of EITHER compiled-in predictor (BranchPredictor,
# GshareBP) fails identically on BOTH MinorCPU and O3CPU with
# "system.cpu.branchPred.conditionalBranchPred without default or user
# set value". This is not O3-specific -- it's a build/se.py-wide
# limitation. Only the implicit default (no --bp-type flag) correctly
# auto-wires conditionalBranchPred. GEM5_PREDICTORS therefore defaults
# to empty here -- see the GEM5_PREDICTORS comment below before
# re-enabling it against this same build.
#
# Kept as a separate script rather than a --cpu-type axis on the
# MinorCPU script: O3CPU is a slower model per run, and its default
# predictor resolves through a different sub-object wiring
# (conditionalBranchPred -> TournamentBP, confirmed via config.ini)
# than whatever MinorCPU's default resolves to -- keeping the two
# pipelines' data in separate trees makes it harder to accidentally
# mix them during analysis.
#
# For a genuinely controlled Minor-vs-O3 comparison: run this script
# and run_gem5_MC_sweep.sh with the SAME GEM5_SEEDS and GEM5_SIZES
# against the same program IDs. Nothing here enforces that
# automatically -- it's on you to keep the two invocations matched.
#
# Usage:
#   ./run_gem5_O3_sweep.sh list-bp
#   ./run_gem5_O3_sweep.sh probe
#   ./run_gem5_O3_sweep.sh screen [COUNT]
#   ./run_gem5_O3_sweep.sh sweep list ID [ID ...]
#   ./run_gem5_O3_sweep.sh sweep all
#
# Same GEM5_ROOT / GEM5_BINARY / cross-compiler requirements as
# run_gem5_MC.sh -- see GEM5.md.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROGRAMS_DIR="$SCRIPT_DIR/../programs"
BUILD_DIR_BASE="$SCRIPT_DIR/bin_gem5_o3"
OUT_DIR_BASE="$SCRIPT_DIR/gem5_stats_o3"
# Matches this project's existing gem5_stats_o3 / bin_gem5_o3 naming.

if [ ! -d "$PROGRAMS_DIR" ]; then
    echo "Programs directory not found at $PROGRAMS_DIR"
    echo "This script expects the corpus layout: <repo>/programs/*.c and <repo>/gem5/$(basename "${BASH_SOURCE[0]}")"
    exit 1
fi

GEM5_ROOT="${GEM5_ROOT:-}"
GEM5_BINARY="${GEM5_BINARY:-}"
GEM5_N="${GEM5_N:-20000}"
GEM5_JOBS="${GEM5_JOBS:-6}"
GEM5_L1D_SIZE="${GEM5_L1D_SIZE:-32kB}"
GEM5_L1I_SIZE="${GEM5_L1I_SIZE:-32kB}"
GEM5_L2_SIZE="${GEM5_L2_SIZE:-256kB}"

# --- the four sweep axes -------------------------------------------
# GEM5_SEEDS and GEM5_PREDICTORS default to their full lists, since
# replication and cross-predictor comparison are this script's actual
# purpose, not an opt-in extra. GEM5_SIZES defaults to a single value
# on purpose: whether it's worth crossing with predictor at all is
# exactly the open question `screen` mode exists to answer first (see
# top-of-file comment).
#
# For a true single-config run identical to run_gem5_MC.sh's default
# (flat output directory, no subfolders): set
#   GEM5_SEEDS=2654435769 GEM5_PREDICTORS="" ./run_gem5_O3_sweep.sh sweep list ID
GEM5_SEEDS="${GEM5_SEEDS:-2654435769 2246822519 3266489917 668265263 374761393}"
GEM5_SIZES="${GEM5_SIZES:-4096}"
# Defaults to EMPTY -- confirmed, for real, on this project's actual
# gem5 build, that neither BranchPredictor nor GshareBP can be
# explicitly selected via --bp-type on ANY CPU type tested (MinorCPU
# and O3CPU both hit the identical "conditionalBranchPred without
# default or user set value" fatal). This is not a CPU-type-specific
# quirk -- it's a build/se.py-wide limitation: only the IMPLICIT
# default (no --bp-type flag at all) correctly auto-wires
# conditionalBranchPred; every explicit selection tested fails the
# same way. So by default this script only runs the one predictor
# config that's actually confirmed to work -- the CPU model's own
# default -- rather than spending probe/sweep time re-confirming a
# dead end. Override GEM5_PREDICTORS explicitly (e.g. "GshareBP") only
# if you've rebuilt gem5 differently or are testing a new build where
# this might not hold; don't re-enable it against this same build
# expecting a different answer.
GEM5_PREDICTORS="${GEM5_PREDICTORS-}"
GEM5_CPU_TYPE="${GEM5_CPU_TYPE:-O3CPU}"

if [ -z "$GEM5_ROOT" ] || [ -z "$GEM5_BINARY" ]; then
    echo "GEM5_ROOT and GEM5_BINARY must be set. See GEM5.md for setup."
    exit 1
fi
if [ ! -x "$GEM5_BINARY" ]; then
    echo "GEM5_BINARY ($GEM5_BINARY) not found or not executable."
    exit 1
fi
SE_PY="$GEM5_ROOT/configs/deprecated/example/se.py"
[ -f "$SE_PY" ] || SE_PY="$GEM5_ROOT/configs/example/se.py"
if [ ! -f "$SE_PY" ]; then
    echo "Could not find se.py under $GEM5_ROOT. Is GEM5_ROOT correct?"
    exit 1
fi
echo "Using se.py: $SE_PY"

RVGCC=""
for cand in riscv64-unknown-linux-gnu-gcc riscv64-linux-gnu-gcc riscv64-unknown-elf-gcc; do
    if command -v "$cand" >/dev/null 2>&1; then RVGCC="$cand"; break; fi
done
if [ -z "$RVGCC" ]; then
    echo "No RISC-V cross compiler found (riscv64-*-gcc)."
    exit 1
fi
CFLAGS="-O2 -std=c99 -fno-tree-vectorize -ffp-contract=off -fno-if-conversion -fno-if-conversion2 -static -DLOWBIT_BACKEND=\"gem5\" -I$PROGRAMS_DIR"

# Which of GEM5_PREDICTORS this se.py actually accepts -- verified by
# ACTUALLY TRYING each one once, up front, not by grepping --help text.
# --help does not enumerate valid --bp-type class names (it's a
# dynamically-typed SimObject parameter, not an argparse choices=[...]
# list), so a substring grep against it is close to meaningless: it
# will "pass" a name only by accident (e.g. if that name happens to
# appear in an unrelated help-text example) and reject everything
# else, even predictors this gem5 build genuinely supports. That bug
# shipped in an earlier version of this script and, on at least one
# real run, resulted in 4 of 5 requested predictors silently falling
# back to the CPU default for every single row while being logged as
# if they'd been applied under their own separate label -- caught by
# comparing CPI across supposedly-different predictor labels for the
# same program and finding them identical. Probing for real, once,
# before the sweep starts (not per-program) is the fix.
HELP_OUTPUT="$("$GEM5_BINARY" "$SE_PY" --help 2>/dev/null)"
BP_TYPE_SUPPORTED=0
echo "$HELP_OUTPUT" | grep -q -- "--bp-type" && BP_TYPE_SUPPORTED=1
if [ "$BP_TYPE_SUPPORTED" -eq 0 ]; then
    echo "WARNING: this gem5 build's se.py does not expose --bp-type at all."
    echo "Every predictor in GEM5_PREDICTORS will run with the CPU's default"
    echo "predictor instead -- the predictor axis of the sweep will be a no-op."
    echo "(Same limitation run_gem5_MC.sh already documents.)"
fi

sanitize() { echo "$1" | tr -c 'A-Za-z0-9' '_'; }

# Populated below, before the sweep loop starts, and READ-ONLY from
# every build_and_run_one() call after that -- including from inside
# `&`-backgrounded jobs. That ordering matters: an associative array
# populated entirely before any subshell forks is inherited correctly
# by every forked child (they each get their own copy of the already-
# complete array); one populated DURING concurrent background jobs
# would not propagate writes back to siblings or the parent, so probe
# results have to be settled up front, sequentially, not lazily.
# Keyed by "cpu|bp" -- predictor support is NOT assumed to carry over
# between CPU models (O3CPU's branch predictor wiring is genuinely
# different from MinorCPU's, confirmed: O3's "default" resolves to a
# TournamentBP sub-object under a conditionalBranchPred split that
# MinorCPU doesn't necessarily have), so each CPU type gets its own
# probe results.
declare -A BP_SUPPORTED
declare -A BP_EXTRA_FLAG

# Runs one gem5 invocation with the given extra flag(s), returns 0/1
# via return code and leaves the log at $2 for the caller to inspect.
_try_bp_invocation() {
    local bp="$1" out="$2" extra="$3"
    "$GEM5_BINARY" --outdir="$out" "$SE_PY" \
        --cmd="$PROBE_BIN" --cpu-type="$GEM5_CPU_TYPE" --bp-type="$bp" $extra \
        --caches --l2cache \
        --l1d_size="$GEM5_L1D_SIZE" --l1i_size="$GEM5_L1I_SIZE" --l2_size="$GEM5_L2_SIZE" \
        > "$out/probe.log" 2>&1
    # -s (non-empty), not -f (merely exists): gem5 creates a 0-byte
    # stats.txt even on total failure -- e.g. an se.py argparse
    # rejection like "invalid choice: 'X' (choose from A, B)" for a
    # --bp-type value this build doesn't support. That error never
    # matches "^fatal:" (it's Python argparse's own convention, not
    # gem5's), so relying on stats.txt's mere existence plus a fatal:
    # grep is blind to exactly this failure mode -- confirmed for
    # real: it let a probe report every one of 5 tested predictors as
    # "OK" when none of them actually worked, and the resulting
    # 2000-run sweep produced 2000 empty stats.txt files. Checking for
    # real content is the fix; a genuine successful run's stats.txt is
    # never anywhere close to empty.
    [ -s "$out/stats.txt" ] && ! grep -qiE "^fatal:|error: argument" "$out/probe.log"
}

probe_predictors() {
    if [ "$BP_TYPE_SUPPORTED" -eq 0 ] || [ -z "$GEM5_PREDICTORS" ] || [ "$GEM5_PREDICTORS" = "-" ]; then
        return
    fi
    local probe_src bp probe_out key
    probe_src="$(ls "$PROGRAMS_DIR"/*.c | head -1)"
    PROBE_BIN="$BUILD_DIR_BASE/.bpprobe_bin"
    mkdir -p "$BUILD_DIR_BASE"
    $RVGCC $CFLAGS -DSIZE=64 -DN=10 -DSEED=1 -o "$PROBE_BIN" "$probe_src" >/dev/null 2>&1

    echo "Probing GEM5_PREDICTORS against $GEM5_CPU_TYPE on this gem5 build (real"
    echo "invocations, not a --help text guess) before starting the sweep..."
    for bp in $GEM5_PREDICTORS; do
        key="$bp"
        probe_out="$OUT_DIR_BASE/.bpprobe_$(sanitize "$bp")"
        mkdir -p "$probe_out"

        if _try_bp_invocation "$bp" "$probe_out" ""; then
            BP_SUPPORTED["$key"]=1
            BP_EXTRA_FLAG["$key"]=""
            echo "  $bp: OK"
            continue
        fi

        # Print the REAL reason, not a guess -- this is what an earlier
        # version of this fix got wrong: acting on an assumed error
        # message instead of the one actually produced. Two different
        # failure shapes are both handled here: gem5's own "fatal:"
        # convention, and Python argparse's "error: argument --bp-type:
        # invalid choice" (se.py restricts --bp-type to a fixed choices
        # list -- if the requested predictor isn't in it, this is what
        # you get, and it's a hard rejection, not something a retry
        # flag can work around).
        local fatal_line valid_choices
        fatal_line="$(grep -iE "^fatal:|error: argument" "$probe_out/probe.log" | head -1)"
        echo "  $bp: first attempt failed"
        echo "      ${fatal_line:-<no recognizable error line found -- see $probe_out/probe.log in full>}"
        if echo "$fatal_line" | grep -qi "invalid choice"; then
            valid_choices="$(echo "$fatal_line" | grep -oiP '(?<=choose from )[A-Za-z0-9, ]+')"
            echo "      This gem5 build's se.py only accepts these --bp-type values: ${valid_choices:-<see line above>}"
            echo "      No retry flag fixes this -- it's a hard argparse restriction, not a config gap."
            BP_SUPPORTED["$key"]=0
            BP_EXTRA_FLAG["$key"]=""
            continue
        fi

        # One documented, narrowly-targeted retry -- and only for the
        # failure it actually addresses. --indirect-bp-type fixes a
        # missing indirectBranchPred sub-object; it does NOT fix a
        # missing conditionalBranchPred sub-object, even though both
        # errors look superficially similar ("X without default or
        # user set value"). Confirmed the hard way: a real run against
        # O3CPU hit "conditionalBranchPred without default or user set
        # value" for BOTH BranchPredictor and GshareBP, the retry below
        # fired on both (the old, looser check matched either name),
        # and failed identically both times -- because the flag was
        # never going to fix that specific sub-object. So: retry only
        # for indirectBranchPred; for conditionalBranchPred, report it
        # as a known, currently-unfixable-via-this-CLI limitation
        # immediately, rather than spend a wasted invocation "trying"
        # something already shown not to work.
        if echo "$fatal_line" | grep -qi "conditionalBranchPred"; then
            echo "      conditionalBranchPred is unset and se.py's deprecated CLI has no flag"
            echo "      for it (--indirect-bp-type only fixes indirectBranchPred, a different"
            echo "      sub-object) -- confirmed not fixable this way, not retrying."
            BP_SUPPORTED["$key"]=0
            BP_EXTRA_FLAG["$key"]=""
            continue
        fi
        if echo "$fatal_line" | grep -qi "indirectBranchPred"; then
            echo "      retrying with --indirect-bp-type=SimpleIndirectPredictor ..."
            if _try_bp_invocation "$bp" "$probe_out" "--indirect-bp-type=SimpleIndirectPredictor"; then
                BP_SUPPORTED["$key"]=1
                BP_EXTRA_FLAG["$key"]="--indirect-bp-type=SimpleIndirectPredictor"
                echo "  $bp: OK (needed --indirect-bp-type=SimpleIndirectPredictor)"
                continue
            fi
            echo "      retry also failed: $(grep -iE "^fatal:|error: argument" "$probe_out/probe.log" | head -1)"
        fi

        BP_SUPPORTED["$key"]=0
        BP_EXTRA_FLAG["$key"]=""
        echo "  $bp: NOT supported by this gem5 build -- full log at $probe_out/probe.log"
        echo "       Every run requesting this predictor will use the CPU default instead,"
        echo "       logged honestly as such. If you want this predictor working, the fatal"
        echo "       line above is the thing to search gem5's issue tracker / your version's"
        echo "       release notes for -- I can help interpret it if you paste it back."
    done
}
# $1 is still the raw mode argument here (MODE/shift happen later,
# right before the case statement) -- checked directly so `list-bp`
# can skip the (comparatively expensive) predictor probe entirely and
# stay the cheapest possible check, as documented above.
if [ "${1:-}" != "list-bp" ]; then
    probe_predictors
fi

# --- core build+run, parameterized over seed/size/predictor --------
# Same body as run_gem5_MC.sh's build_and_run_one(), generalized to
# take seed/size/predictor as arguments and to lay runs out under
# per-combination subdirectories instead of one flat OUT_DIR. With
# every sweep list left at its single-value default, the resulting
# path collapses to exactly $OUT_DIR_BASE/$base -- run_gem5_MC.sh's
# original layout -- so default invocations stay drop-in compatible.
build_and_run_one() {
    local src="$1" seed="$2" size="$3" bp="$4"
    local base; base="$(basename "${src%.c}")"

    local seed_tag size_tag bp_tag bp_flag bp_desc
    seed_tag="seed$(sanitize "$seed")"
    size_tag="size$(sanitize "$size")"
    if [ -n "$bp" ]; then
        bp_tag="bp$(sanitize "$bp")"
        if [ "${BP_SUPPORTED[$bp]:-0}" -eq 1 ]; then
            bp_flag="--bp-type=$bp ${BP_EXTRA_FLAG[$bp]:-}"
            if [ -n "${BP_EXTRA_FLAG[$bp]:-}" ]; then
                bp_desc="$bp (applied: yes -- confirmed by probe run, needed ${BP_EXTRA_FLAG[$bp]})"
            else
                bp_desc="$bp (applied: yes -- confirmed by probe run)"
            fi
        else
            bp_flag=""
            bp_desc="$bp (applied: no -- failed probe run on $GEM5_CPU_TYPE, ran with CPU default instead)"
        fi
    else
        bp_tag="bpdefault"
        bp_flag=""
        bp_desc="default (CPU model's built-in predictor)"
    fi

    # collapse to the flat layout when every axis is at its
    # single-value default (seed=2654435769, size=4096, bp unset)
    local subdir=""
    [ "$seed" != "2654435769" ] && subdir="$subdir/$seed_tag"
    [ "$size" != "4096" ] && subdir="$subdir/$size_tag"
    [ -n "$bp" ] && subdir="$subdir/$bp_tag"

    local build_dir="${BUILD_DIR_BASE}${subdir}"
    local out_dir="${OUT_DIR_BASE}${subdir}"
    local bin="$build_dir/$base"
    local outdir="$out_dir/$base"
    mkdir -p "$build_dir" "$outdir"

    echo "Building for gem5 (SEED=$seed, SIZE=$size, N=$GEM5_N, cpu=$GEM5_CPU_TYPE, bp=${bp:-default}): $base"
    $RVGCC $CFLAGS -DSIZE="$size" -DN="$GEM5_N" -DSEED="$seed" -o "$bin" "$src" 2>"$outdir/build.log"
    if [ ! -f "$bin" ]; then
        echo "  BUILD FAILED -- see $outdir/build.log"
        return
    fi

    echo "Running under gem5 (cpu=$GEM5_CPU_TYPE, bp=${bp:-default}): $base"
    "$GEM5_BINARY" --outdir="$outdir" "$SE_PY" \
        --cmd="$bin" \
        --cpu-type="$GEM5_CPU_TYPE" \
        $bp_flag \
        --caches --l2cache \
        --l1d_size="$GEM5_L1D_SIZE" --l1i_size="$GEM5_L1I_SIZE" --l2_size="$GEM5_L2_SIZE" \
        > "$outdir/gem5_run.log" 2>&1

    {
        echo "source=$src"
        echo "binary=$bin"
        echo "cpu_type=$GEM5_CPU_TYPE"
        echo "bp_type=${bp_desc}"
        echo "l1d_size=$GEM5_L1D_SIZE l1i_size=$GEM5_L1I_SIZE l2_size=$GEM5_L2_SIZE"
        echo "size_override=$size"
        echo "n_override=$GEM5_N"
        echo "seed_override=$seed"
        echo "note=the binary's own printed size_tier field is STALE -- use"
        echo "     size_override/n_override/seed_override above for this run"
        echo "backend=gem5"
    } > "$outdir/meta.txt"

    if [ ! -s "$outdir/stats.txt" ]; then
        echo "  WARNING: no/empty stats.txt for $base (bp=${bp:-default}) -- check $outdir/gem5_run.log"
        # A screen full of interleaved concurrent output is exactly how
        # this failure mode went unnoticed across an entire 2000-run
        # sweep last time -- one line per failure in a dedicated file
        # means `wc -l` or `cat` at the end tells the real story
        # without having to scroll back through hours of log.
        echo "$outdir" >> "$OUT_DIR_BASE/FAILED_RUNS.txt"
    fi
}

# Same throttling as run_gem5_MC.sh -- caps concurrent gem5 processes
# at GEM5_JOBS, since gem5 barely uses more than one host core each.
run_throttled() {
    while [ "$(jobs -rp | wc -l)" -ge "$GEM5_JOBS" ]; do
        wait -n
    done
    build_and_run_one "$1" "$2" "$3" "$4" &
}

sweep_one_source() {
    local src="$1"
    for seed in $GEM5_SEEDS; do
        for size in $GEM5_SIZES; do
            if [ "$BP_TYPE_SUPPORTED" -eq 1 ] && [ -n "$GEM5_PREDICTORS" ] && [ "$GEM5_PREDICTORS" != "-" ]; then
                for bp in $GEM5_PREDICTORS; do
                    run_throttled "$src" "$seed" "$size" "$bp"
                done
            else
                run_throttled "$src" "$seed" "$size" ""
            fi
        done
    done
}

MODE="${1:-}"
shift || true

case "$MODE" in
    list-bp)
        # Cheapest possible check, no probing/building/running anything:
        # ask gem5 directly which BranchPredictor/IndirectPredictor
        # classes are actually compiled into this binary. This answers
        # "does LTAGE etc. even exist here" definitively -- if a name
        # doesn't appear in this list, no Python config trick will
        # conjure it; if it DOES appear here but se.py's --bp-type
        # rejected it anyway, that's a CLI-only restriction and worth
        # working around (see run_gem5_DC_sweep.sh's approach, or ask
        # for the same treatment here).
        echo "=== --list-bp-types ==="
        "$GEM5_BINARY" "$SE_PY" --list-bp-types 2>&1
        echo ""
        echo "=== --list-indirect-bp-types ==="
        "$GEM5_BINARY" "$SE_PY" --list-indirect-bp-types 2>&1
        exit 0
        ;;
    probe)
        # Just the predictor check, nothing else -- seconds, not hours.
        # probe_predictors() already ran unconditionally above (before
        # this case statement) if GEM5_PREDICTORS is set, so by the
        # time execution reaches here the real answer is already known
        # and printed; this branch only adds a summary and exits
        # before touching PROGRAMS_DIR at all.
        if [ -z "$GEM5_PREDICTORS" ] || [ "$GEM5_PREDICTORS" = "-" ]; then
            echo ""
            echo "GEM5_PREDICTORS is empty -- by design, since this build's default"
            echo "predictor is the only config confirmed to work (see this script's"
            echo "comment on the conditionalBranchPred fatal). A sweep will run at"
            echo "$GEM5_CPU_TYPE's own default predictor only. Set GEM5_PREDICTORS"
            echo "explicitly to test specific values against a different/rebuilt gem5."
            exit 0
        fi
        echo ""
        echo "=== probe summary ($GEM5_CPU_TYPE) ==="
        any_working=0
        for bp in $GEM5_PREDICTORS; do
            if [ "${BP_SUPPORTED[$bp]:-0}" -eq 1 ]; then
                extra="${BP_EXTRA_FLAG[$bp]:-}"
                echo "  $bp: WORKS${extra:+ (needs $extra)}"
                any_working=1
            else
                echo "  $bp: does not work on this gem5 build"
            fi
        done
        if [ "$any_working" -eq 0 ]; then
            echo ""
            echo "None of GEM5_PREDICTORS worked on $GEM5_CPU_TYPE. Paste the"
            echo "'fatal:'/error lines printed above back for a real diagnosis instead of a guess."
        fi
        exit 0
        ;;
    screen)
        # Cheap pilot: small representative subset x full size x
        # predictor cross, single seed. Purpose is ONLY to test for a
        # size x predictor interaction before committing to `sweep`.
        COUNT="${1:-20}"
        mapfile -t ALL_SRCS < <(ls "$PROGRAMS_DIR"/*.c | sort)
        TOTAL=${#ALL_SRCS[@]}
        STEP=$(( TOTAL / COUNT )); [ "$STEP" -lt 1 ] && STEP=1
        GEM5_SIZES="4096 65536 1048576 8388608"
        echo "SCREEN MODE ($GEM5_CPU_TYPE): $COUNT programs x sizes($GEM5_SIZES) x predictors($GEM5_PREDICTORS) x 1 seed"
        echo "This is a pilot for testing size x predictor interaction, not a final result."
        i=0
        for s in "${ALL_SRCS[@]}"; do
            if [ $((i % STEP)) -eq 0 ]; then
                sweep_one_source "$s"
            fi
            i=$((i + 1))
        done
        wait
        ;;
    sweep)
        SUBMODE="${1:-}"; shift || true
        case "$SUBMODE" in
            list)
                if [ "$#" -eq 0 ]; then
                    echo "Usage: $0 sweep list ID [ID ...]"
                    exit 1
                fi
                N_SEEDS=$(echo $GEM5_SEEDS | wc -w)
                N_SIZES=$(echo $GEM5_SIZES | wc -w)
                N_BPS=1
                [ "$BP_TYPE_SUPPORTED" -eq 1 ] && N_BPS=$(echo $GEM5_PREDICTORS | wc -w)
                EST=$(( $# * N_SEEDS * N_SIZES * N_BPS ))
                echo "About to run ~$EST gem5 invocations for $# program(s). Ctrl-C now to abort."
                for id in "$@"; do
                    match=$(ls "$PROGRAMS_DIR" | grep "^${id}_.*\.c$" | head -1)
                    if [ -z "$match" ]; then
                        echo "No source found matching ID $id in $PROGRAMS_DIR"
                        continue
                    fi
                    sweep_one_source "$PROGRAMS_DIR/$match"
                done
                wait
                ;;
            all)
                TOTAL_SRCS=$(ls "$PROGRAMS_DIR"/*.c | wc -l)
                N_SEEDS=$(echo $GEM5_SEEDS | wc -w)
                N_SIZES=$(echo $GEM5_SIZES | wc -w)
                N_BPS=1
                [ "$BP_TYPE_SUPPORTED" -eq 1 ] && N_BPS=$(echo $GEM5_PREDICTORS | wc -w)
                EST=$(( TOTAL_SRCS * N_SEEDS * N_SIZES * N_BPS ))
                echo "About to run ~$EST gem5 invocations across $TOTAL_SRCS programs."
                echo "If that number looks too large: run 'screen' first, confirm whether"
                echo "size and predictor actually interact, then set GEM5_SIZES or"
                echo "GEM5_PREDICTORS to a single value to sweep the other axis alone."
                echo "Ctrl-C now to abort, or wait 5s to proceed."
                sleep 5
                for s in "$PROGRAMS_DIR"/*.c; do
                    sweep_one_source "$s"
                done
                wait
                ;;
            *)
                echo "Usage: $0 sweep list ID [ID ...]"
                echo "       $0 sweep all"
                exit 1
                ;;
        esac
        ;;
    *)
        echo "Usage: $0 list-bp"
        echo "       $0 probe"
        echo "       $0 screen [COUNT]"
        echo "       $0 sweep list ID [ID ...]"
        echo "       $0 sweep all"
        echo ""
        echo "Env vars: GEM5_SEEDS GEM5_SIZES GEM5_PREDICTORS (space-separated lists)"
        echo "          GEM5_N GEM5_JOBS GEM5_CPU_TYPE GEM5_L1D_SIZE GEM5_L1I_SIZE GEM5_L2_SIZE"
        exit 1
        ;;
esac

echo "gem5 stats written under: $OUT_DIR_BASE (see per-combination subdirs for non-default seed/size/bp)"
echo "Next: run a 2-way ANOVA (size x predictor) on the screen-mode output before deciding"
echo "whether the full sweep needs both axes crossed or can be split into two sweeps."
