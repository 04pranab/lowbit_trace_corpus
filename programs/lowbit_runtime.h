/*
 * lowbit_runtime.h -- LowBit Trace Corpus runtime layer
 * =========================================================
 * A small, generic, backend-agnostic runtime that every benchmark
 * includes. The benchmark itself never knows whether it's running
 * natively, under QEMU, under gem5, or on real RISC-V silicon --
 * that's a property of how it's built and invoked, not of anything
 * in the benchmark source. All backend-specific work (hardware
 * counters, simulator statistics, timing wrappers) stays external,
 * driven by build_and_run_riscv.sh and the surrounding tooling.
 *
 * Provides:
 *   - lcg_next()             deterministic PRNG (no OS entropy, ever)
 *   - lowbit_aligned_alloc() 64-byte cache-line-aligned allocation
 *   - lowbit_timer_t / lowbit_roi_start() / lowbit_roi_stop() /
 *     lowbit_roi_seconds()   portable region-of-interest wall-clock
 *                            timing (POSIX clock_gettime only -- no
 *                            architecture-specific instructions)
 *   - lowbit_arch_name()     compile-time-known target architecture
 *   - LOWBIT_BACKEND         a string identifying the execution
 *                            backend (native / qemu_user_mode /
 *                            orange_pi / ...), set at build time via
 *                            -DLOWBIT_BACKEND="..."; defaults to
 *                            "unspecified" if not passed
 *
 * Every benchmark's SIZE/N are `#ifndef`-guarded, not hardcoded, so
 * they can be swept without editing source:
 *     gcc -DSIZE=65536 -DN=3000000 -o out prog.c
 *
 * Every benchmark follows the same lifecycle (see each .c file's
 * main()): allocate -> initialize data -> warm-up (untimed) ->
 * region-of-interest (timed, this is what's reported) -> validate ->
 * report -> cleanup. The warm-up phase runs roughly 10% of the
 * region-of-interest's iteration count (capped) through the exact
 * same access pattern before measurement starts, so the reported
 * region reflects steady-state behavior rather than cold-start
 * effects. Persistent per-pattern state (PRNG streams, walk
 * positions, saturating counters, etc.) is intentionally shared
 * between the warm-up and measured phases, so the measured phase
 * picks up exactly where warm-up left off.
 */

#ifndef LOWBIT_RUNTIME_H
#define LOWBIT_RUNTIME_H

/* Needed for posix_memalign / clock_gettime / CLOCK_MONOTONIC under
 * strict -std=c99, which otherwise hides POSIX extensions. Must be
 * defined before any system header is included. */
#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200112L
#endif

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

/* ---- deterministic LCG (Numerical Recipes constants) ---- */
static inline uint32_t lcg_next(uint32_t *state) {
    *state = (*state) * 1664525u + 1013904223u;
    return *state;
}

/* ---- 64-byte cache-line-aligned allocation ----
 * posix_memalign is POSIX/glibc; newlib (riscv64-unknown-elf-gcc,
 * used by the spike backend) doesn't implement it under any feature-
 * test macro. Fall back to a manual aligned allocation on top of
 * plain malloc in that case -- every other backend is unaffected. */
#if defined(__NEWLIB__)
static inline void *lowbit_aligned_alloc(size_t nbytes) {
    size_t rounded = ((nbytes + 63u) / 64u) * 64u;
    if (rounded == 0u) rounded = 64u;
    /* over-allocate by 64 + sizeof(void*) so we can shift forward to
     * a 64-byte boundary and still recover the original pointer if
     * ever freed (these benchmarks don't free, but keep it correct) */
    unsigned char *raw = (unsigned char *)malloc(rounded + 64u + sizeof(void *));
    if (!raw) return NULL;
    unsigned char *aligned = raw + sizeof(void *);
    uintptr_t addr = (uintptr_t)aligned;
    uintptr_t aligned_addr = (addr + 63u) & ~(uintptr_t)63u;
    aligned = (unsigned char *)aligned_addr;
    ((void **)aligned)[-1] = raw;  /* stash original pointer just before */
    return aligned;
}
#else
static inline void *lowbit_aligned_alloc(size_t nbytes) {
    void *p = NULL;
    size_t rounded = ((nbytes + 63u) / 64u) * 64u;
    if (rounded == 0u) rounded = 64u;
    if (posix_memalign(&p, 64, rounded) != 0) return NULL;
    return p;
}
#endif

/* ---- execution backend label (set at build time, never detected
 * from inside the benchmark) ---- */
#ifndef LOWBIT_BACKEND
#define LOWBIT_BACKEND "unspecified"
#endif

/* ---- compile-time-known target architecture (informational only,
 * not used in any control-flow or timing decision) ---- */
static inline const char *lowbit_arch_name(void) {
#if defined(__riscv)
  #if defined(__riscv_xlen) && __riscv_xlen == 64
    return "riscv64";
  #else
    return "riscv32";
  #endif
#elif defined(__x86_64__)
    return "x86_64";
#elif defined(__aarch64__)
    return "aarch64";
#else
    return "unknown";
#endif
}

static inline const char *lowbit_compiler_version(void) {
#if defined(__VERSION__)
    return __VERSION__;
#else
    return "unknown";
#endif
}

/* ---- region-of-interest (ROI) wall-clock timing ----
 * Deliberately the only timing mechanism embedded in a benchmark:
 * portable POSIX clock_gettime, no rdcycle/rdtsc/perf_event_open --
 * hardware-counter timing is a backend concern (see
 * build_and_run_riscv.sh), not a benchmark concern.
 *
 * Exception: newlib (riscv64-unknown-elf-gcc, used by the spike
 * backend) doesn't implement clock_gettime at all. spike is a
 * functional ISA simulator with no timing model in the first place
 * (see spike/run_spike.sh and spike/REPORT.md), so "wall-clock
 * seconds" from it were always going to be a rough proxy rather than
 * a real measurement -- falling back to the RISC-V `rdcycle` CSR
 * here needs no OS/syscall support at all (unlike clock_gettime) and
 * is the most native counter available on this backend. Converted to
 * seconds using an assumed 1 GHz core, purely so the unit stays
 * comparable within a single spike run -- do not compare these
 * seconds against wall-clock seconds from any other backend. */
#if defined(__NEWLIB__) && defined(__riscv)
typedef struct {
    unsigned long c0, c1;
} lowbit_timer_t;

static inline unsigned long lowbit_rdcycle(void) {
    unsigned long c;
    __asm__ volatile ("rdcycle %0" : "=r" (c));
    return c;
}

static inline void lowbit_roi_start(lowbit_timer_t *t) {
    t->c0 = lowbit_rdcycle();
}

static inline void lowbit_roi_stop(lowbit_timer_t *t) {
    t->c1 = lowbit_rdcycle();
}

static inline double lowbit_roi_seconds(const lowbit_timer_t *t) {
    /* assumed 1 GHz -- see comment above; spike cycle counts are not
     * real wall-clock time on any real core, this is a placeholder
     * unit for internal-to-spike comparisons only */
    return (double)(t->c1 - t->c0) * 1e-9;
}
#else
typedef struct {
    struct timespec t0, t1;
} lowbit_timer_t;

static inline void lowbit_roi_start(lowbit_timer_t *t) {
    clock_gettime(CLOCK_MONOTONIC, &t->t0);
}

static inline void lowbit_roi_stop(lowbit_timer_t *t) {
    clock_gettime(CLOCK_MONOTONIC, &t->t1);
}

static inline double lowbit_roi_seconds(const lowbit_timer_t *t) {
    double s = (double)(t->t1.tv_sec - t->t0.tv_sec);
    double ns = (double)(t->t1.tv_nsec - t->t0.tv_nsec);
    return s + ns * 1e-9;
}
#endif

#endif /* LOWBIT_RUNTIME_H */