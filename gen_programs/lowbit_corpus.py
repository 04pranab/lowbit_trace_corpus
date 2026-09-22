#!/usr/bin/env python3
"""
lowbit_corpus_extend.py -- self-contained. No dependency on any other
gen_programs*.py file.

Produces (all inside programs/, alongside the existing 500):
  - up to 2000 new "grid_..." files: the branch x cache cells NOT
    already in the original 500 (which only covers a 10x10
    block-diagonal per batch). Combined with the existing 500 this
    gives full 50x50 = 2500 coverage.
  - 50 "base_branch_..." files: one per branch family, paired with a
    fixed synthetic sequential-scan index computed inline in this
    script -- NOT one of the 50 cataloged cache families -- so a
    branch-only measurement never depends on the cache catalogue.
  - 50 "base_cache_..." files: one per cache family, paired with a
    fixed synthetic always-taken branch computed inline -- NOT one of
    the 50 cataloged branch families -- so a cache-only measurement
    never depends on the branch catalogue.
"""
import csv
import os

OUT_DIR = "programs"
MANIFEST_PATH = "MANIFEST.csv"

TIERS = [
    ("small_L1", 4096, 1000000),
    ("medium_L2", 65536, 3000000),
    ("large_L3", 1048576, 5000000),
    ("xlarge_DRAM", 8388608, 8000000),
]

BRANCHES = [{'code': 'taken = 1;',
  'desc': 'single direction, always-taken loop guard (best case for predictor)',
  'id': 'b01',
  'name': 'always_taken'},
 {'code': 'taken = 0;',
  'desc': 'single direction, always-not-taken rare-event guard',
  'id': 'b02',
  'name': 'always_not_taken'},
 {'code': 'taken = (int)(i & 1u) == 0;',
  'desc': 'period-2 alternating pattern (T,N,T,N,...)',
  'id': 'b03',
  'name': 'alternating'},
 {'code': 'taken = (i % 3u) != 0u;',
  'desc': 'period-3 repeating pattern (T,T,N,T,T,N,...)',
  'id': 'b04',
  'name': 'periodic3'},
 {'code': 'taken = (i % 8u) < 5u;',
  'desc': 'period-8 pattern biased 5/8 taken',
  'id': 'b05',
  'name': 'periodic8_biased'},
 {'code': 'taken = (int)((lcg_next(bseed) >> 16) & 1u);',
  'desc': 'data-independent pseudo-random ~50% taken (stresses 2-bit/GShare predictors)',
  'id': 'b06',
  'name': 'pseudo_random_50'},
 {'code': '{ uint32_t outer_ = (uint32_t)((i >> 4) & 1u);\n'
          '  uint32_t inner_ = (uint32_t)(i & 15u);\n'
          '  taken = inner_ < (outer_ ? 12u : 4u); }',
  'desc': 'nested-loop branch whose inner threshold depends on outer parity',
  'id': 'b07',
  'name': 'nested_dependent'},
 {'code': '{ uint32_t route_ = (uint32_t)(i % 5u);\n'
          '  switch (route_) {\n'
          '    case 0: taken = 1; v += 1u; break;\n'
          '    case 1: taken = 0; v += 2u; break;\n'
          '    case 2: taken = 1; v += 3u; break;\n'
          '    case 3: taken = 0; v += 5u; break;\n'
          '    default: taken = 1; v += 7u; break;\n'
          '  } }',
  'desc': '5-way switch/jump-table dispatch driving an indirect-branch-like trace',
  'id': 'b08',
  'name': 'switch_multiway'},
 {'code': '{ uint32_t depth_ = (uint32_t)(i % 8u) + 1u;\n'
          '  uint32_t r_ = recurse_helper(depth_, v);\n'
          '  taken = (r_ & 1u) == 0u; v = r_; }',
  'desc': 'variable-depth recursion driving call/return branch-stack behavior',
  'id': 'b09',
  'name': 'recursive_calls'},
 {'code': '{ uint32_t hist_ = (uint32_t)((*prev_taken) & 3u);\n'
          '  static const uint32_t corr_table_[4] = {1u, 0u, 1u, 1u};\n'
          '  taken = (int)corr_table_[hist_];\n'
          '  *prev_taken = ((*prev_taken) << 1) | taken; }',
  'desc': 'outcome correlated with previous two branch outcomes (correlator-predictor stressor)',
  'id': 'b10',
  'name': 'correlated_history'},
 {'code': 'taken = (v & 0xFFu) < 128u;',
  'desc': 'branch outcome determined by the loaded data value itself (load-to-branch dependency, '
          'not just loop index)',
  'id': 'b11',
  'name': 'value_dependent_threshold'},
 {'code': '{ static uint64_t run_pos_ = 0, run_len_ = 1, dir_ = 1;\n'
          '  taken = (int)dir_;\n'
          '  run_pos_++;\n'
          '  if (run_pos_ >= run_len_) { run_pos_ = 0; run_len_ = (run_len_ < 4096u) ? run_len_ * '
          '2u : 1u; dir_ ^= 1u; } }',
  'desc': 'run-length-doubling phases (1,2,4,8,... taken then flip) -- long predictable runs that '
          'stress saturating-counter warm-up',
  'id': 'b12',
  'name': 'doubling_run_lengths'},
 {'code': 'taken = i < (N / 2u);',
  'desc': 'one hard phase change: always-taken for the first half of the run, always-not-taken for '
          'the second half (predictor re-training stress)',
  'id': 'b13',
  'name': 'single_phase_change'},
 {'code': '{ uint32_t r_ = v % 10u;\n'
          '  if (r_ < 7u) taken = 1; else if (r_ < 8u) taken = 0; else if (r_ < 9u) taken = 1; '
          'else taken = 0; }',
  'desc': 'if/else-if/else-if/else with 4 outcomes skewed 70/10/10/10 collapsed to a '
          'taken/not-taken signal',
  'id': 'b14',
  'name': 'four_way_skewed'},
 {'code': 'taken = ((i >> 2) & 1u) == 0u;',
  'desc': 'branch only evaluated once every 4 logical iterations (manually-unrolled inner body, '
          'sparse branch density)',
  'id': 'b15',
  'name': 'manual_unroll4_sparse'},
 {'code': 'taken = (v % 97u) != 0u;',
  'desc': "linear-search-style loop that keeps taking the 'not found yet' branch until a "
          'data-dependent sentinel value is hit (variable, unpredictable trip length)',
  'id': 'b16',
  'name': 'sentinel_search_loop'},
 {'code': '{ int b1_ = (int)((i % 5u) < 3u);\n'
          '  int b2_ = b1_ ^ (int)(i & 1u);\n'
          '  taken = b2_;\n'
          '  (void)b1_; }',
  'desc': 'two logically chained branches per iteration whose second outcome is the XOR of the '
          'first outcome and the loop parity (stresses global-history / GShare-style predictors)',
  'id': 'b17',
  'name': 'correlated_two_branch_xor'},
 {'code': '{ uint32_t sel_ = (uint32_t)(i % 4u);\n'
          '  v = dispatch_table[sel_](v);\n'
          '  taken = (v & 1u) == 0u; }',
  'desc': 'indirect call through a function-pointer table (distinct from the switch/jump-table '
          'pattern: tests indirect-branch-target prediction)',
  'id': 'b18',
  'name': 'indirect_fn_dispatch'},
 {'code': '{ uint32_t bound_ = 4u + (v % 12u);\n'
          '  uint32_t j_ = (uint32_t)(i % 16u);\n'
          '  taken = j_ < bound_; }',
  'desc': 'outer/inner nested loop where the inner trip count is derived from data, so both branch '
          'outcome AND loop-exit point vary per outer step',
  'id': 'b19',
  'name': 'data_dependent_break_trip'},
 {'code': '{ static uint32_t bias_ = 32u;\n'
          '  uint32_t r_ = lcg_next(bseed) & 63u;\n'
          '  taken = r_ < bias_;\n'
          '  bias_ = 8u + ((bias_ + 7u) % 56u); }',
  'desc': 'pseudo-random per-iteration threshold that itself drifts over time (non-stationary '
          'bias, harder than fixed pseudo-random 50/50)',
  'id': 'b20',
  'name': 'random_threshold_walk'},
 {'code': '{ static uint32_t acc21_ = 0u; acc21_ += v; taken = (acc21_ & 1u) == 0u; }',
  'desc': 'branch depends on an accumulator carried across iterations (sum of loaded values), not '
          'just the current value or index',
  'id': 'b21',
  'name': 'loop_carried_running_state'},
 {'code': '{ static uint32_t thresh22_ = 500u;\n'
          '  taken = (v % 1000u) > thresh22_;\n'
          '  thresh22_ = (thresh22_ > 5u) ? thresh22_ - 5u : 500u; }',
  'desc': 'threshold drifts downward each iteration (convergence-check style) then resets '
          'periodically -- distinct from a pure random walk or a one-shot phase change',
  'id': 'b22',
  'name': 'drifting_threshold_periodic_reset'},
 {'code': '{ uint32_t pc_ = v;\n'
          '  pc_ = pc_ - ((pc_ >> 1) & 0x55555555u);\n'
          '  pc_ = (pc_ & 0x33333333u) + ((pc_ >> 2) & 0x33333333u);\n'
          '  pc_ = (pc_ + (pc_ >> 4)) & 0x0F0F0F0Fu;\n'
          '  pc_ = (pc_ * 0x01010101u) >> 24;\n'
          '  taken = (pc_ & 1u) == 0u; }',
  'desc': 'branch decided by the population count (bit parity) of the loaded value -- a '
          'value-dependent pattern with no exploitable run structure for simple bimodal predictors',
  'id': 'b23',
  'name': 'popcount_parity'},
 {'code': '{ static uint32_t bias24_ = 0u;\n'
          '  taken = ((uint32_t)(i % 100u)) < (bias24_ / 100u);\n'
          '  if (bias24_ < 9900u) bias24_ += 3u; }',
  'desc': 'bias grows monotonically from always-not-taken toward always-taken over the run and '
          'then saturates (no reset) -- single long-horizon adaptation, unlike the cyclic drift in '
          'b22',
  'id': 'b24',
  'name': 'monotonic_drift_saturating'},
 {'code': 'taken = (i & 1023u) == 0u;',
  'desc': 'extremely rare taken branch (1 in 1024), simulating error-handling / exceptional-path '
          'checks -- tests predictor confidence saturation and the cost of a rare misprediction',
  'id': 'b25',
  'name': 'rare_exception_1_in_1024'},
 {'code': '{ uint64_t warm26_ = N / 20u; if (warm26_ == 0u) warm26_ = 1u;\n'
          '  taken = (i < warm26_) ? (int)((i & 1u) == 0u) : 1; }',
  'desc': 'short warm-up phase (first 5% of iterations) alternates, then the branch becomes '
          'always-taken for the remainder -- loop-peeling / prologue-epilogue style transition',
  'id': 'b26',
  'name': 'warmup_then_steady'},
 {'code': '{ uint32_t outer27_ = (uint32_t)((i >> 5) & 1u);\n'
          '  uint32_t inner27_ = (uint32_t)(i & 31u);\n'
          '  taken = outer27_ ? (int)((inner27_ % 3u) != 0u) : (int)((inner27_ % 7u) < 4u); }',
  'desc': 'outer-loop parity selects which of two different inner periodicities governs the branch '
          '-- a pattern-of-patterns, harder than single-level nested dependence (b07)',
  'id': 'b27',
  'name': 'meta_correlated_nested'},
 {'code': '{ static int depth28_ = 0; static int stack28_[64];\n'
          '  uint32_t op28_ = v & 3u;\n'
          '  if (op28_ != 0u && depth28_ < 63) { stack28_[depth28_++] = (int)v; taken = 1; }\n'
          '  else if (depth28_ > 0) { depth28_--; taken = 0; }\n'
          '  else { taken = 1; } }',
  'desc': 'a small explicit push/pop stack whose depth is driven by the loaded data value, '
          'simulating irregular call/return imbalance distinct from the fixed-depth recursion in '
          'b09',
  'id': 'b28',
  'name': 'data_driven_stack_machine'},
 {'code': '{ static int burst29_remaining_ = 0;\n'
          '  static uint32_t bseed29_ = 0xA1B2C3D4u;\n'
          '  if (burst29_remaining_ > 0) { taken = 0; burst29_remaining_--; }\n'
          '  else {\n'
          '    taken = 1;\n'
          '    if ((lcg_next(&bseed29_) % 50u) == 0u) burst29_remaining_ = 8;\n'
          '  } }',
  'desc': 'mostly always-taken with occasional Poisson-triggered bursts of 8 consecutive not-taken '
          'outcomes -- rare-event clustering rather than the single-branch rarity of b25',
  'id': 'b29',
  'name': 'bursty_streak_pattern'},
 {'code': '{ if ((i % 64u) == 0u) { taken = 1; }\n'
          '  else { taken = (int)((lcg_next(bseed) >> 16) & 1u); } }',
  'desc': 'pseudo-random 50/50 base pattern, overridden to always-taken every 64th iteration -- '
          'simulates a periodic interrupt/check cutting across otherwise unpredictable control '
          'flow',
  'id': 'b30',
  'name': 'periodic_interrupt_override'},
 {'code': 'taken = (i % 13u) < 7u;',
  'desc': 'period-13 repeating pattern -- a prime period avoids the power-of-two aliasing that '
          'many synthetic periodic branches share',
  'id': 'b31',
  'name': 'prime_period13'},
 {'code': '{ static int sat32_ = 2;\n'
          '  uint32_t bit32_ = v & 1u;\n'
          '  if (bit32_) { if (sat32_ < 3) sat32_++; } else { if (sat32_ > 0) sat32_--; }\n'
          '  taken = sat32_ >= 2; }',
  'desc': 'explicitly emulates a classic 2-bit saturating counter state machine, driven by the '
          'parity bitstream of the loaded data',
  'id': 'b32',
  'name': 'emulated_2bit_saturating_counter'},
 {'code': '{ static uint64_t pos33_ = 0, len33_ = 1; static int dir33_ = 1;\n'
          '  static uint32_t bseed33_ = 0x33333333u;\n'
          '  taken = dir33_;\n'
          '  pos33_++;\n'
          '  if (pos33_ >= len33_) { pos33_ = 0; len33_ = 1u + (lcg_next(&bseed33_) % 64u); dir33_ '
          '^= 1; } }',
  'desc': "run-length is drawn uniformly from 1-64 each time a run ends -- unlike b12's doubling "
          'runs, block lengths here are i.i.d. random',
  'id': 'b33',
  'name': 'uniform_random_block_runs'},
 {'code': '{ static int cnt34_ = 0;\n'
          '  if (cnt34_ <= 0) { cnt34_ = (int)(v % 5u) + 1; }\n'
          '  taken = cnt34_ > 0;\n'
          '  cnt34_--; }',
  'desc': 'a do-while-style countdown loop whose initial count comes from the loaded data value '
          '(0-4), taken while counting down',
  'id': 'b34',
  'name': 'data_driven_countdown'},
 {'code': '{ uint32_t h35_ = (uint32_t)i ^ v;\n'
          '  h35_ ^= h35_ << 13; h35_ ^= h35_ >> 17; h35_ ^= h35_ << 5;\n'
          '  taken = (h35_ & 1u) == 0u; }',
  'desc': 'branch decided by an xorshift mix of the loop index and the loaded value -- a different '
          '(non-LCG) mixing function from the pseudo-random classes elsewhere in the corpus',
  'id': 'b35',
  'name': 'xorshift_hash_mix'},
 {'code': '{ static int state36_ = 0; static int dwell36_ = 0;\n'
          '  static const int dwell_times36_[3] = {10, 3, 20};\n'
          '  taken = (state36_ == 0);\n'
          '  dwell36_++;\n'
          '  if (dwell36_ >= dwell_times36_[state36_]) { dwell36_ = 0; state36_ = (state36_ + 1) % '
          '3; } }',
  'desc': '3-state cyclic finite-state machine with unequal fixed dwell times (10/3/20 iterations '
          'per state) -- taken only in state 0',
  'id': 'b36',
  'name': 'traffic_light_fsm'},
 {'code': '{ static uint32_t rem37_ = 0;\n'
          '  if (rem37_ == 0u) { rem37_ = 4u + (v % 12u); }\n'
          '  taken = 1;\n'
          '  rem37_--;\n'
          '  if (rem37_ == 0u) { taken = 0; } }',
  'desc': 'loop-unswitching-style run: taken for a data-dependent number of iterations (4-15) '
          'before a single not-taken tick, then repeats',
  'id': 'b37',
  'name': 'value_driven_unswitch_run'},
 {'code': '{ static uint32_t bseed38_ = 0x38383838u;\n'
          '  static uint32_t hazard38_ = 4000000000u;\n'
          '  uint32_t r38_ = lcg_next(&bseed38_);\n'
          '  taken = r38_ < hazard38_;\n'
          '  hazard38_ = hazard38_ - (hazard38_ >> 10);\n'
          '  if (hazard38_ < 1000000u) hazard38_ = 4000000000u; }',
  'desc': 'probability of taken decays geometrically each iteration (simulating a decreasing '
          'hazard rate) then resets high',
  'id': 'b38',
  'name': 'decaying_hazard_probability'},
 {'code': '{ static uint32_t s1_39 = 0x11111111u, s2_39 = 0x22222222u;\n'
          '  uint32_t r39_ = lcg_next(&s1_39) ^ lcg_next(&s2_39);\n'
          '  taken = (r39_ & 1u) == 0u; }',
  'desc': 'two independent LCG streams combined via XOR -- a chaotic-looking but fully '
          'deterministic and reproducible mix',
  'id': 'b39',
  'name': 'dual_lcg_xor_chaos'},
 {'code': '{ uint32_t cv40_ = v | 1u;\n'
          '  if ((cv40_ & 1u) == 0u) { taken = 1; v = cv40_ >> 1; }\n'
          '  else { taken = 0; v = cv40_ * 3u + 1u; } }',
  'desc': 'applies one step of the Collatz transform to the loaded value each iteration and '
          'branches on its parity, feeding the transformed value forward -- models real '
          'chaotic-but-deterministic value-dependent control flow',
  'id': 'b40',
  'name': 'collatz_value_transform'},
 {'code': '{ static int mstate41_ = 0; static uint32_t mseed41_ = 0x41414141u;\n'
          '  static const uint32_t trans41_[3] = {70u, 40u, 90u};\n'
          '  uint32_t r41_ = lcg_next(&mseed41_) % 100u;\n'
          '  taken = (mstate41_ == 1);\n'
          '  if (r41_ >= trans41_[mstate41_]) { mstate41_ = (mstate41_ + 1) % 3; } }',
  'desc': 'stochastic 3-state Markov chain with state-dependent transition probabilities '
          '(70%/40%/90% stay-chance) -- taken only while in state 1, a genuinely probabilistic FSM '
          'rather than the fixed-dwell FSM in b36',
  'id': 'b41',
  'name': 'markov_chain_3state'},
 {'code': '{ uint32_t r42_ = (uint32_t)i ^ v;\n'
          '  r42_ = ((r42_ & 0xAAAAAAAAu) >> 1) | ((r42_ & 0x55555555u) << 1);\n'
          '  r42_ = ((r42_ & 0xCCCCCCCCu) >> 2) | ((r42_ & 0x33333333u) << 2);\n'
          '  r42_ = ((r42_ & 0xF0F0F0F0u) >> 4) | ((r42_ & 0x0F0F0F0Fu) << 4);\n'
          '  r42_ = ((r42_ & 0xFF00FF00u) >> 8) | ((r42_ & 0x00FF00FFu) << 8);\n'
          '  r42_ = (r42_ >> 16) | (r42_ << 16);\n'
          '  taken = (r42_ & 1u) == 0u; }',
  'desc': 'branch decided by the low bit of the 32-bit-reversed XOR of the loop index and the '
          'loaded value -- combines a deterministic index permutation with data dependence',
  'id': 'b42',
  'name': 'bit_reversal_index_data_mix'},
 {'code': '{ static uint32_t hist43_[4] = {0u, 0u, 0u, 0u}; static int pos43_ = 0;\n'
          '  uint32_t avg43_ = (hist43_[0] + hist43_[1] + hist43_[2] + hist43_[3]) / 4u;\n'
          '  taken = v > avg43_;\n'
          '  hist43_[pos43_] = v; pos43_ = (pos43_ + 1) & 3; }',
  'desc': 'branch taken if the current value exceeds the moving average of the last 4 values -- a '
          'statistical/adaptive control-style branch, common in signal-processing code',
  'id': 'b43',
  'name': 'moving_average_threshold'},
 {'code': '{ static int recover44_remaining_ = 0;\n'
          '  if (recover44_remaining_ > 0) { taken = 0; recover44_remaining_--; }\n'
          '  else if ((i & 2047u) == 0u) { taken = 1; recover44_remaining_ = 4; }\n'
          '  else { taken = 1; } }',
  'desc': 'a periodic (every 2048th iteration) rare trigger followed by a fixed 4-iteration '
          'not-taken recovery burst -- models real exception-handling cleanup paths, distinct from '
          "b25's unadorned rare branch and b29's randomly-triggered bursts",
  'id': 'b44',
  'name': 'exception_then_recovery_burst'},
 {'code': '{ static uint32_t cA45_ = 0u, cB45_ = 0u;\n'
          '  if ((v & 1u) == 0u) cA45_ += (v & 7u) + 1u; else cB45_ += ((v >> 3) & 7u) + 1u;\n'
          '  taken = cA45_ >= cB45_; }',
  'desc': "two independent monotonic counters accumulate based on the loaded value's parity; "
          "branch taken depends on which counter currently leads -- a relative-magnitude 'race' "
          'branch',
  'id': 'b45',
  'name': 'two_counters_race'},
 {'code': '{ static unsigned char tbl46_[256]; static int init46_ = 0;\n'
          '  if (!init46_) {\n'
          '    uint32_t ts46_ = 0x46464646u;\n'
          '    for (int k46_ = 0; k46_ < 256; k46_++) { tbl46_[k46_] = (unsigned '
          'char)((lcg_next(&ts46_) >> 16) & 1u); }\n'
          '    init46_ = 1;\n'
          '  }\n'
          '  taken = tbl46_[v & 0xFFu]; }',
  'desc': 'a static 256-entry table of pseudo-random taken/not-taken bits, indexed by the low byte '
          'of the loaded value -- an arbitrary-but-deterministic dense value-to-outcome mapping',
  'id': 'b46',
  'name': 'lookup_table_driven'},
 {'code': '{ uint32_t depth47_ = (v % 10u) + 1u;\n'
          '  uint32_t r47_ = mutA47(depth47_, v);\n'
          '  taken = (r47_ & 1u) == 0u; v = r47_; }',
  'desc': 'two mutually recursive functions call each other to a data-dependent depth (1-10) -- '
          'distinct call-graph/return-stack behavior from the single-function recursion in b09',
  'id': 'b47',
  'name': 'mutual_ping_pong_recursion'},
 {'code': '{ static int32_t val48_ = 0;\n'
          '  if (val48_ <= 0) { val48_ = (int32_t)(v % 1000u) + 1; }\n'
          '  taken = val48_ > 0;\n'
          '  val48_ -= (int32_t)((v & 7u) + 1u); }',
  'desc': 'a signed countdown from a data-dependent starting value (0-999), decremented by a '
          "data-dependent step (1-8) each time -- both trip length and step vary, unlike b34's "
          'fixed decrement-by-1 countdown',
  'id': 'b48',
  'name': 'variable_step_signed_countdown'},
 {'code': '{ if ((i & 1u) == 0u) { taken = (i % 3u) != 0u; }\n'
          '  else { taken = (int)((lcg_next(bseed) >> 16) & 1u); } }',
  'desc': 'even iterations follow a period-3 pattern, odd iterations follow pseudo-random 50/50 -- '
          'two different paradigms multiplexed together within a single branch site',
  'id': 'b49',
  'name': 'interleaved_two_pattern_multiplex'},
 {'code': '{ static int state50_ = 0; static int counter50_ = 0;\n'
          '  int signal50_ = (int)(v & 1u);\n'
          '  if (signal50_ == state50_) { counter50_ = 0; }\n'
          '  else { counter50_++; if (counter50_ >= 3) { state50_ = signal50_; counter50_ = 0; } '
          '}\n'
          '  taken = state50_; }',
  'desc': "the branch's effective state only flips after 3 consecutive opposite-signal readings "
          'accumulate -- hysteresis/debounce logic as found in control systems',
  'id': 'b50',
  'name': 'hysteresis_debounce'}]

CACHES = [{'desc': 'stride-1 sequential sweep over the whole buffer (best case, prefetcher-friendly)',
  'id': 'c01',
  'index': '(uint32_t)(i % SIZE)',
  'name': 'sequential_unit_stride',
  'setup': '',
  'teardown': ''},
 {'desc': 'stride-1 sequential sweep repeated across a working set sized for this tier',
  'id': 'c02',
  'index': '(uint32_t)(i % SIZE)',
  'name': 'sequential_wraparound_large',
  'setup': '',
  'teardown': ''},
 {'desc': 'fixed stride-8 access pattern (moderate spatial locality)',
  'id': 'c03',
  'index': '(uint32_t)((i * 8u) % SIZE)',
  'name': 'fixed_stride_small',
  'setup': '',
  'teardown': ''},
 {'desc': 'fixed stride-64 access pattern (poor spatial locality, likely crosses cache lines every '
          'access)',
  'id': 'c04',
  'index': '(uint32_t)((i * 64u) % SIZE)',
  'name': 'fixed_stride_large',
  'setup': '',
  'teardown': ''},
 {'desc': 'pseudo-random indices confined to a small working set (tests capacity vs. L1/L2 '
          'residency)',
  'id': 'c05',
  'index': '(uint32_t)(lcg_next(&cseed_) & small_mask_)',
  'name': 'random_small_working_set',
  'setup': 'uint32_t cseed_ = SEED0 ^ 0xABCDEFu; uint32_t small_mask_ = (SIZE > 4096u ? 4096u : '
           'SIZE) - 1u;',
  'teardown': ''},
 {'desc': 'pseudo-random indices spanning the entire (large) buffer (stresses LLC / DRAM misses)',
  'id': 'c06',
  'index': '(uint32_t)(lcg_next(&cseed_) % SIZE)',
  'name': 'random_full_working_set',
  'setup': 'uint32_t cseed_ = SEED0 ^ 0x13572468u;',
  'teardown': ''},
 {'desc': '2D matrix traversal in row-major order (good locality: matches memory layout)',
  'id': 'c07',
  'index': '(uint32_t)(((i / mcols_) % mrows_) * mcols_ + (i % mcols_))',
  'name': 'matrix_row_major',
  'setup': 'uint32_t mrows_ = 1u; uint32_t mcols_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows_ = d_; '
           '}\n'
           '    mcols_ = SIZE / (mrows_ == 0u ? 1u : mrows_);\n'
           '    if (mrows_ == 0u) mrows_ = 1u;',
  'teardown': ''},
 {'desc': '2D matrix traversal in column-major order over a row-major buffer (deliberately poor '
          'locality)',
  'id': 'c08',
  'index': '(uint32_t)(((i % mrows_)) * mcols_ + ((i / mrows_) % mcols_))',
  'name': 'matrix_col_major',
  'setup': 'uint32_t mrows_ = 1u; uint32_t mcols_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows_ = d_; '
           '}\n'
           '    mcols_ = SIZE / (mrows_ == 0u ? 1u : mrows_);\n'
           '    if (mrows_ == 0u) mrows_ = 1u;',
  'teardown': ''},
 {'desc': 'blocked/tiled traversal with 16x16 tiles (locality-optimized compromise between row and '
          'col order)',
  'id': 'c09',
  'index': '({ uint32_t tile_lin_ = (uint32_t)((i / elems_per_tile_) % (tiles_per_row_ * '
           'tiles_per_col_));\n'
           '     uint32_t tr_ = tile_lin_ / tiles_per_row_, tc_ = tile_lin_ % tiles_per_row_;\n'
           '     uint32_t within_ = (uint32_t)(i % elems_per_tile_);\n'
           '     uint32_t lr_ = within_ / TILE_, lc_ = within_ % TILE_;\n'
           '     uint32_t r_ = tr_ * TILE_ + lr_, c_ = tc_ * TILE_ + lc_;\n'
           '     (r_ < mrows_ && c_ < mcols_) ? (r_ * mcols_ + c_) : (uint32_t)(i % SIZE); })',
  'name': 'matrix_tiled_blocked',
  'setup': 'uint32_t mrows_ = 1u; uint32_t mcols_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows_ = d_; '
           '}\n'
           '    mcols_ = SIZE / (mrows_ == 0u ? 1u : mrows_);\n'
           '    if (mrows_ == 0u) mrows_ = 1u;\n'
           '    const uint32_t TILE_ = 16u;\n'
           '    uint32_t tiles_per_row_ = (mcols_ + TILE_ - 1u) / TILE_;\n'
           '    uint32_t tiles_per_col_ = (mrows_ + TILE_ - 1u) / TILE_;\n'
           '    uint32_t elems_per_tile_ = TILE_ * TILE_;',
  'teardown': ''},
 {'desc': 'random permutation walked as a linked list (one dependent load per step: no prefetcher '
          'help)',
  'id': 'c10',
  'index': '(walk_ = perm_[walk_])',
  'name': 'pointer_chase_linked_list',
  'setup': 'uint32_t *perm_ = (uint32_t *)malloc(sizeof(uint32_t) * SIZE);\n'
           '    if (!perm_) { fprintf(stderr, "alloc failure\\n"); return 1; }\n'
           '    for (uint32_t k_ = 0u; k_ < SIZE; k_++) perm_[k_] = k_;\n'
           '    { uint32_t pseed_ = SEED0 ^ 0x2468ACEu;\n'
           '      for (uint32_t k_ = SIZE - 1u; k_ > 0u; k_--) {\n'
           '          uint32_t j_ = lcg_next(&pseed_) % (k_ + 1u);\n'
           '          uint32_t tmp_ = perm_[k_]; perm_[k_] = perm_[j_]; perm_[j_] = tmp_;\n'
           '      } }\n'
           '    uint32_t walk_ = 0u;',
  'teardown': 'free(perm_);'},
 {'desc': 'overlapping sliding-window sweep: base position advances by 1 every 1000 accesses, '
          'width 32 -- typical of convolution / attention window access',
  'id': 'c11',
  'index': '(uint32_t)((i + (i / 1000u) + (i % WIN_)) % SIZE)',
  'name': 'sliding_window',
  'setup': 'const uint32_t WIN_ = 32u;',
  'teardown': ''},
 {'desc': 'stride using a prime number (97) chosen to avoid power-of-two cache-set aliasing that a '
          'naive power-of-two stride would hit',
  'id': 'c12',
  'index': '(uint32_t)((i * 97u) % SIZE)',
  'name': 'prime_stride_anti_alias',
  'setup': '',
  'teardown': ''},
 {'desc': 'alternates between two independent regions of the same buffer each step (two concurrent '
          'streams, stresses stream-prefetcher confusion)',
  'id': 'c13',
  'index': '((i & 1u) == 0u) ? (uint32_t)((i / 2u) % half_) : (uint32_t)(half_ + ((i / 2u) % '
           'half_))',
  'name': 'two_stream_interleaved',
  'setup': 'uint32_t half_ = SIZE / 2u;',
  'teardown': ''},
 {'desc': 'revisits the same cache line 8 times (temporal-locality burst) before jumping to a new, '
          'distant location',
  'id': 'c14',
  'index': '(uint32_t)(((i / BURST_) * 131u) % SIZE)',
  'name': 'temporal_burst_reuse',
  'setup': 'const uint32_t BURST_ = 8u;',
  'teardown': ''},
 {'desc': 'stride -1 sequential sweep from the end of the buffer backward (tests whether '
          'prefetchers tuned for forward strides degrade)',
  'id': 'c15',
  'index': '(uint32_t)(SIZE - 1u - (i % SIZE))',
  'name': 'reverse_sequential',
  'setup': '',
  'teardown': ''},
 {'desc': 'touches only 1 of every 4 fixed-size blocks (simulates sparse-matrix / sparse-attention '
          'access with structured gaps)',
  'id': 'c16',
  'index': '({ uint32_t blk_ = (uint32_t)((i / BLOCK_) % nblocks_);\n'
           '     uint32_t within_ = (uint32_t)(i % BLOCK_);\n'
           '     ((blk_ % 4u) == 0u) ? (blk_ * BLOCK_ + within_) % SIZE : (within_) % SIZE; })',
  'name': 'block_sparse',
  'setup': 'const uint32_t BLOCK_ = 64u; uint32_t nblocks_ = (SIZE / BLOCK_) > 0u ? (SIZE / '
           'BLOCK_) : 1u;',
  'teardown': ''},
 {'desc': "deterministic power-law-ish access: 80% of accesses land in a small 'hot' region, 20% "
          'spread across the full buffer (simulates hash-table / embedding-table hot-key skew)',
  'id': 'c17',
  'index': '({ uint32_t r_ = lcg_next(&cseed17_) % 10u;\n'
           '     (r_ < 8u) ? (lcg_next(&cseed17_) % hot_size_) : (lcg_next(&cseed17_) % SIZE); })',
  'name': 'zipf_like_hot_buckets',
  'setup': 'uint32_t cseed17_ = SEED0 ^ 0x777333u; uint32_t hot_size_ = (SIZE > 256u ? 256u : '
           'SIZE);',
  'teardown': ''},
 {'desc': 'two independent pointers into the same buffer advancing at different rates (write '
          'pointer fast, read pointer slow), mimicking a ring-buffer / queue access pattern',
  'id': 'c18',
  'index': '({ wp_ = (wp_ + 3u) % SIZE;\n'
           '     rp_ = (rp_ + 1u) % SIZE;\n'
           '     ((i & 1u) == 0u) ? wp_ : rp_; })',
  'name': 'producer_consumer_two_pointer',
  'setup': 'uint32_t wp_ = 0u, rp_ = 0u;',
  'teardown': ''},
 {'desc': 'anti-diagonal wavefront traversal of a 2D matrix, as seen in dynamic-programming '
          'kernels (edit distance, LCS) -- distinct locality profile from row/col/tiled traversal',
  'id': 'c19',
  'index': '({ uint32_t diag_ = (uint32_t)((i / (mrows19_ < mcols19_ ? mrows19_ : mcols19_)) % '
           'ndiag19_);\n'
           '     uint32_t within_ = (uint32_t)(i % (mrows19_ < mcols19_ ? mrows19_ : mcols19_));\n'
           '     uint32_t r_ = (diag_ < mrows19_) ? (diag_ - (within_ % (diag_ + 1u))) : (mrows19_ '
           '- 1u - within_ % mrows19_);\n'
           '     uint32_t c_ = diag_ - r_;\n'
           '     (r_ < mrows19_ && c_ < mcols19_) ? (r_ * mcols19_ + c_) : (uint32_t)(i % SIZE); '
           '})',
  'name': 'matrix_diagonal_wavefront',
  'setup': 'uint32_t mrows19_ = 1u; uint32_t mcols19_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows19_ = '
           'd_; }\n'
           '    mcols19_ = SIZE / (mrows19_ == 0u ? 1u : mrows19_);\n'
           '    if (mrows19_ == 0u) mrows19_ = 1u;\n'
           '    uint32_t ndiag19_ = mrows19_ + mcols19_ - 1u;',
  'teardown': ''},
 {'desc': "alternates in long phases between a tiny 'hot' region (fits L1) and a scan across the "
          "full 'cold' region (exceeds LLC), mimicking real application phase behavior (e.g. "
          'attention block vs. weight stream)',
  'id': 'c20',
  'index': '((((i / PHASE_LEN_) & 1u) == 0u) ? (uint32_t)(i % hot_size20_) : '
           '(uint32_t)(lcg_next(&cseed20_) % SIZE))',
  'name': 'hot_cold_phase_alternation',
  'setup': 'const uint64_t PHASE_LEN_ = N / 8u > 0u ? N / 8u : 1u;\n'
           '    uint32_t hot_size20_ = (SIZE > 512u ? 512u : SIZE);\n'
           '    uint32_t cseed20_ = SEED0 ^ 0x8899AAu;',
  'teardown': ''},
 {'desc': 'stride doubles across successive blocks of iterations (1,2,4,8x), like sweeping a '
          'multi-resolution image pyramid',
  'id': 'c21',
  'index': '({ uint32_t lvl_ = (uint32_t)((i / BLOCK21_) % NLEVELS21_);\n'
           '     uint32_t stride_ = 1u << lvl_;\n'
           '     (uint32_t)((i * stride_) % SIZE); })',
  'name': 'pyramid_multiresolution_stride',
  'setup': 'const uint32_t NLEVELS21_ = 4u;\n'
           '    const uint32_t BLOCK21_ = (SIZE / 16u) > 0u ? (SIZE / 16u) : 1u;',
  'teardown': ''},
 {'desc': 'indexes a synthetic 3D tensor (D0 x 16 x 16) with the memory-innermost axis iterated '
          'outermost -- deliberately poor locality, as in a mis-ordered tensor slice/permute op',
  'id': 'c22',
  'index': '({ uint32_t i2_ = (uint32_t)((i / (d0_22_ * d1_22_)) % d2_22_);\n'
           '     uint32_t i0_ = (uint32_t)((i / d1_22_) % d0_22_);\n'
           '     uint32_t i1_ = (uint32_t)(i % d1_22_);\n'
           '     (uint32_t)(((i0_ * d1_22_ + i1_) * d2_22_ + i2_) % SIZE); })',
  'name': 'tensor_3d_slice_poor_locality',
  'setup': 'uint32_t d2_22_ = 16u; uint32_t d1_22_ = 16u;\n'
           '    uint32_t d0_22_ = (SIZE / (d1_22_ * d2_22_)) > 0u ? (SIZE / (d1_22_ * d2_22_)) : '
           '1u;',
  'teardown': ''},
 {'desc': 'visits 3 consecutive elements (simulated struct fields) then jumps by a fixed struct '
          'stride of 20 -- models array-of-structs field access rather than a plain fixed stride',
  'id': 'c23',
  'index': '(uint32_t)((((i / FIELDS23_) * STRUCT_STRIDE23_) + (i % FIELDS23_)) % SIZE)',
  'name': 'struct_of_array_field_burst',
  'setup': 'const uint32_t STRUCT_STRIDE23_ = 20u; const uint32_t FIELDS23_ = 3u;',
  'teardown': ''},
 {'desc': 'round-robins across 8 memory banks, sequential within each bank -- models '
          'bank-interleaved memory / SIMD-lane access',
  'id': 'c24',
  'index': '({ uint32_t bank_ = (uint32_t)(i % NBANKS24_);\n'
           '     uint32_t off_ = (uint32_t)((i / NBANKS24_) % bank_size24_);\n'
           '     (uint32_t)(bank_ * bank_size24_ + off_) % SIZE; })',
  'name': 'banked_interleaved_access',
  'setup': 'const uint32_t NBANKS24_ = 8u;\n'
           '    uint32_t bank_size24_ = (SIZE / NBANKS24_) > 0u ? (SIZE / NBANKS24_) : 1u;',
  'teardown': ''},
 {'desc': '70% of accesses reuse one of the last 16 touched indices (a small recency ring), 30% '
          'jump to a fresh random index -- models the reuse-distance profile of a real working set '
          'rather than uniform random access',
  'id': 'c25',
  'index': '({ uint32_t choice_;\n'
           '     uint32_t r_ = lcg_next(&cseed25_) % 10u;\n'
           '     if (r_ < 7u) { choice_ = ring25_buf[lcg_next(&cseed25_) % RING25_]; }\n'
           '     else { choice_ = lcg_next(&cseed25_) % SIZE; }\n'
           '     ring25_buf[ring25_pos_] = choice_;\n'
           '     ring25_pos_ = (ring25_pos_ + 1u) % RING25_;\n'
           '     choice_ % SIZE; })',
  'name': 'lru_recency_weighted_reuse',
  'setup': 'uint32_t cseed25_ = SEED0 ^ 0x25252525u;\n'
           '    const uint32_t RING25_ = 16u;\n'
           '    uint32_t ring25_buf[16]; uint32_t ring25_pos_ = 0u;\n'
           '    for (uint32_t k_ = 0u; k_ < RING25_; k_++) ring25_buf[k_] = 0u;',
  'teardown': ''},
 {'desc': 'stride doubles every access (1,2,4,...,4096) then resets to 1 -- a fractal-like '
          'growing-then-collapsing stride sequence',
  'id': 'c26',
  'index': '({ base26_ = (base26_ + stride26_) % SIZE;\n'
           '     stride26_ = (stride26_ < MAXSTRIDE26_) ? stride26_ * 2u : 1u;\n'
           '     base26_; })',
  'name': 'geometric_stride_growth',
  'setup': 'uint32_t base26_ = 0u; uint32_t stride26_ = 1u;\n'
           '    const uint32_t MAXSTRIDE26_ = 4096u;',
  'teardown': ''},
 {'desc': '1-in-4 accesses hit a tiny 8-element accumulator region, the rest stream sequentially '
          '-- models a streaming reduction (e.g. dot-product accumulate) common in ML kernels',
  'id': 'c27',
  'index': '(((i % 4u) == 0u) ? (uint32_t)(i % NACC27_) : (uint32_t)(i % SIZE))',
  'name': 'reduction_broadcast_pattern',
  'setup': 'const uint32_t NACC27_ = 8u;',
  'teardown': ''},
 {'desc': 'Morton (Z-order) curve traversal of a 2D matrix via bit interleaving -- a genuinely '
          'different locality profile from row-major, column-major, tiled, or diagonal traversal',
  'id': 'c28',
  'index': '({ uint32_t code_ = (uint32_t)(i % ((uint32_t)bound28_ * (uint32_t)bound28_));\n'
           '     uint32_t rx_ = code_ & 0x55555555u;\n'
           '     rx_ = (rx_ | (rx_ >> 1)) & 0x33333333u;\n'
           '     rx_ = (rx_ | (rx_ >> 2)) & 0x0F0F0F0Fu;\n'
           '     rx_ = (rx_ | (rx_ >> 4)) & 0x00FF00FFu;\n'
           '     rx_ = (rx_ | (rx_ >> 8)) & 0x0000FFFFu;\n'
           '     uint32_t cy_ = (code_ >> 1) & 0x55555555u;\n'
           '     cy_ = (cy_ | (cy_ >> 1)) & 0x33333333u;\n'
           '     cy_ = (cy_ | (cy_ >> 2)) & 0x0F0F0F0Fu;\n'
           '     cy_ = (cy_ | (cy_ >> 4)) & 0x00FF00FFu;\n'
           '     cy_ = (cy_ | (cy_ >> 8)) & 0x0000FFFFu;\n'
           '     (rx_ < mrows28_ && cy_ < mcols28_) ? (uint32_t)(rx_ * mcols28_ + cy_) : '
           '(uint32_t)(i % SIZE); })',
  'name': 'morton_zorder_traversal',
  'setup': 'uint32_t mrows28_ = 1u; uint32_t mcols28_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows28_ = '
           'd_; }\n'
           '    mcols28_ = SIZE / (mrows28_ == 0u ? 1u : mrows28_);\n'
           '    if (mrows28_ == 0u) mrows28_ = 1u;\n'
           '    uint32_t bound28_ = 1u;\n'
           '    while (bound28_ < mrows28_ || bound28_ < mcols28_) bound28_ <<= 1u;',
  'teardown': ''},
 {'desc': 'alternates every 5000 accesses between two disjoint halves of the buffer -- stresses '
          'conflict/capacity thrashing when both halves compete for the same cache capacity',
  'id': 'c29',
  'index': '((((i / PPHASE29_) & 1u) == 0u) ? (uint32_t)(regA29_ + (i % half29_)) : '
           '(uint32_t)(regB29_ + (i % half29_)))',
  'name': 'ping_pong_cache_thrash',
  'setup': 'const uint64_t PPHASE29_ = 5000u;\n'
           '    uint32_t half29_ = (SIZE / 2u) > 0u ? SIZE / 2u : 1u;\n'
           '    uint32_t regA29_ = 0u; uint32_t regB29_ = half29_;',
  'teardown': ''},
 {'desc': 'indices come from a separate precomputed lookup table of 4096 pseudo-random ids, '
          'gathered round-robin -- models embedding-table / gather-op access in ML workloads',
  'id': 'c30',
  'index': '(idtab30_[i % TABLE_LEN30_])',
  'name': 'gather_scatter_indirect_table',
  'setup': 'const uint32_t TABLE_LEN30_ = 4096u;\n'
           '    uint32_t *idtab30_ = (uint32_t *)malloc(sizeof(uint32_t) * TABLE_LEN30_);\n'
           '    if (!idtab30_) { fprintf(stderr, "alloc failure\\n"); return 1; }\n'
           '    { uint32_t tseed_ = SEED0 ^ 0x30303030u;\n'
           '      for (uint32_t k_ = 0u; k_ < TABLE_LEN30_; k_++) idtab30_[k_] = lcg_next(&tseed_) '
           '% SIZE; }',
  'teardown': 'free(idtab30_);'},
 {'desc': 'advances by successive Fibonacci numbers (mod SIZE) each step -- an irregular, '
          'non-power-of-two, non-geometric stride sequence',
  'id': 'c31',
  'index': '({ base31_ = (base31_ + fib_a31_) % SIZE;\n'
           '     uint32_t next31_ = (fib_a31_ + fib_b31_) % SIZE;\n'
           '     if (next31_ == 0u) next31_ = 1u;\n'
           '     fib_b31_ = fib_a31_; fib_a31_ = next31_;\n'
           '     base31_; })',
  'name': 'fibonacci_stride',
  'setup': 'uint32_t fib_a31_ = 1u, fib_b31_ = 1u; uint32_t base31_ = 0u;',
  'teardown': ''},
 {'desc': 'Hilbert (space-filling) curve traversal of a 2D matrix via the classic iterative d2xy '
          'algorithm -- distinct locality profile from Morton/Z-order, row/col, or diagonal '
          'traversal',
  'id': 'c32',
  'index': '({ uint32_t d32_ = (uint32_t)(i % ((uint32_t)bound32_ * (uint32_t)bound32_));\n'
           '     uint32_t t32_ = d32_, x32_ = 0u, y32_ = 0u;\n'
           '     for (uint32_t s32_ = 1u; s32_ < bound32_; s32_ <<= 1u) {\n'
           '       uint32_t rx32_ = 1u & (t32_ / 2u);\n'
           '       uint32_t ry32_ = 1u & (t32_ ^ rx32_);\n'
           '       if (ry32_ == 0u) {\n'
           '         if (rx32_ == 1u) { x32_ = s32_ - 1u - x32_; y32_ = s32_ - 1u - y32_; }\n'
           '         { uint32_t tmp32_ = x32_; x32_ = y32_; y32_ = tmp32_; }\n'
           '       }\n'
           '       x32_ += s32_ * rx32_; y32_ += s32_ * ry32_;\n'
           '       t32_ /= 4u;\n'
           '     }\n'
           '     (x32_ < mrows32_ && y32_ < mcols32_) ? (uint32_t)(x32_ * mcols32_ + y32_) : '
           '(uint32_t)(i % SIZE); })',
  'name': 'hilbert_curve_traversal',
  'setup': 'uint32_t mrows32_ = 1u; uint32_t mcols32_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows32_ = '
           'd_; }\n'
           '    mcols32_ = SIZE / (mrows32_ == 0u ? 1u : mrows32_);\n'
           '    if (mrows32_ == 0u) mrows32_ = 1u;\n'
           '    uint32_t bound32_ = 1u;\n'
           '    while (bound32_ < mrows32_ || bound32_ < mcols32_) bound32_ <<= 1u;',
  'teardown': ''},
 {'desc': 'fixed stride of 4096 elements (~16 KiB), chosen to alias repeatedly into a small number '
          'of cache sets across ways once the working set exceeds L1 capacity',
  'id': 'c33',
  'index': '(uint32_t)((i * CONFLICT_STRIDE33_) % SIZE)',
  'name': 'cache_set_conflict_stress',
  'setup': 'const uint32_t CONFLICT_STRIDE33_ = 4096u;',
  'teardown': ''},
 {'desc': 'revisits a randomly chosen base index for a randomly chosen burst length (1-16) before '
          "jumping -- unlike c14's fixed 8-length burst, both the target and the burst length vary",
  'id': 'c34',
  'index': '({ if (burst_remaining34_ == 0u) {\n'
           '       base34_ = lcg_next(&cseed34_) % SIZE;\n'
           '       burst_remaining34_ = 1u + (lcg_next(&cseed34_) % 16u);\n'
           '     }\n'
           '     burst_remaining34_--;\n'
           '     base34_; })',
  'name': 'variable_length_burst_reuse',
  'setup': 'uint32_t cseed34_ = SEED0 ^ 0x34343434u; uint32_t base34_ = 0u; uint32_t '
           'burst_remaining34_ = 0u;',
  'teardown': ''},
 {'desc': 'approximate checkerboard (red-black) matrix pattern -- only visits cells whose '
          'row+column parity is even, as in red-black Gauss-Seidel / stencil codes',
  'id': 'c35',
  'index': '({ uint32_t lin35_ = (uint32_t)(i % ncells35_);\n'
           '     uint32_t r35_ = lin35_ / mcols35_, c35_ = lin35_ % mcols35_;\n'
           '     if (((r35_ + c35_) & 1u) != 0u) { lin35_ = (lin35_ + 1u) % ncells35_; }\n'
           '     lin35_; })',
  'name': 'checkerboard_red_black',
  'setup': 'uint32_t mrows35_ = 1u; uint32_t mcols35_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows35_ = '
           'd_; }\n'
           '    mcols35_ = SIZE / (mrows35_ == 0u ? 1u : mrows35_);\n'
           '    if (mrows35_ == 0u) mrows35_ = 1u;\n'
           '    uint32_t ncells35_ = mrows35_ * mcols35_;',
  'teardown': ''},
 {'desc': 'gathers overlapping 5-element windows starting every 3 elements (patch width 5, stride '
          '3) -- the classic im2col convolution-lowering access pattern',
  'id': 'c36',
  'index': '(uint32_t)((((i / PATCH36_) * STRIDE36_) + (i % PATCH36_)) % SIZE)',
  'name': 'im2col_overlapping_patch_gather',
  'setup': 'const uint32_t PATCH36_ = 5u; const uint32_t STRIDE36_ = 3u;',
  'teardown': ''},
 {'desc': 'processes one half of the buffer completely, then the other half completely, '
          'alternating each full epoch -- double-buffering, distinct from the fixed 5000-access '
          'ping-pong period',
  'id': 'c37',
  'index': '((((i / half37_) & 1u) == 0u) ? (uint32_t)(i % half37_) : (uint32_t)(half37_ + (i % '
           'half37_)))',
  'name': 'double_buffer_epoch_alternation',
  'setup': 'uint32_t half37_ = (SIZE / 2u) > 0u ? SIZE / 2u : 1u;',
  'teardown': ''},
 {'desc': 'alternates between two independent deterministic hash functions of the iteration index '
          '-- models cuckoo-hashing double-probe access',
  'id': 'c38',
  'index': '((((i) & 1u) == 0u) ? (uint32_t)((i * 2654435761u) % SIZE) : (uint32_t)((i * 40503u + '
           '1u) % SIZE))',
  'name': 'cuckoo_double_hash_probe',
  'setup': '',
  'teardown': ''},
 {'desc': 'a bounded 1D random walk (step in [-8,+8], clamped to the buffer) -- '
          'Brownian-motion-like locality, correlated with recent position rather than '
          'uniform-random or ring-based reuse',
  'id': 'c39',
  'index': '({ int32_t delta39_ = (int32_t)(lcg_next(&cseed39_) % 17u) - 8;\n'
           '     int64_t np39_ = (int64_t)pos39_ + delta39_;\n'
           '     if (np39_ < 0) np39_ = 0;\n'
           '     if (np39_ >= (int64_t)SIZE) np39_ = (int64_t)SIZE - 1;\n'
           '     pos39_ = (uint32_t)np39_;\n'
           '     pos39_; })',
  'name': 'bounded_random_walk',
  'setup': 'uint32_t cseed39_ = SEED0 ^ 0x39393939u; uint32_t pos39_ = SIZE / 2u;',
  'teardown': ''},
 {'desc': 'triangular (causal) access: for synthetic row r, only visits columns 0..r -- models '
          'causal self-attention masking, where each query position only attends to earlier '
          'positions',
  'id': 'c40',
  'index': '({ uint32_t r40_ = (uint32_t)((i / mcols40_) % mrows40_);\n'
           '     uint32_t limit40_ = r40_ + 1u;\n'
           '     uint32_t c40_ = (uint32_t)(i % limit40_);\n'
           '     (uint32_t)(r40_ * mcols40_ + c40_) % SIZE; })',
  'name': 'causal_attention_triangular',
  'setup': 'uint32_t mrows40_ = 1u; uint32_t mcols40_ = SIZE;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) mrows40_ = '
           'd_; }\n'
           '    mcols40_ = SIZE / (mrows40_ == 0u ? 1u : mrows40_);\n'
           '    if (mrows40_ == 0u) mrows40_ = 1u;',
  'teardown': ''},
 {'desc': 'hierarchical hop distances (mostly tiny, occasionally medium, rarely huge) mimicking '
          'skip-list level traversal -- distinct from the single-scale pointer chase in c10',
  'id': 'c41',
  'index': '({ uint32_t lvl41_ = lcg_next(&cseed41_) % 16u;\n'
           '     uint32_t bigjump41_ = (SIZE / 4u) > 0u ? SIZE / 4u : 1u;\n'
           '     uint32_t hop41_;\n'
           '     if (lvl41_ == 0u) hop41_ = 1u + (lcg_next(&cseed41_) % bigjump41_);\n'
           '     else if (lvl41_ < 4u) hop41_ = 1u + (lcg_next(&cseed41_) % 64u);\n'
           '     else hop41_ = 1u + (lcg_next(&cseed41_) % 4u);\n'
           '     pos41_ = (pos41_ + hop41_) % SIZE;\n'
           '     pos41_; })',
  'name': 'skip_list_multilevel_hop',
  'setup': 'uint32_t cseed41_ = SEED0 ^ 0x41414141u; uint32_t pos41_ = 0u;',
  'teardown': ''},
 {'desc': 'stride derived from a recursive-halving depth that cycles 0-19, in the spirit of '
          'cache-oblivious divide-and-conquer algorithms (e.g. funnelsort, van Emde Boas layout)',
  'id': 'c42',
  'index': '({ uint32_t depth42_ = (uint32_t)(i % 20u);\n'
           '     uint32_t k42_ = 1u;\n'
           '     for (uint32_t s42_ = 0u; s42_ < depth42_; s42_++) k42_ <<= 1u;\n'
           '     uint32_t half42_ = SIZE / (k42_ > 0u ? k42_ : 1u);\n'
           '     if (half42_ == 0u) half42_ = 1u;\n'
           '     base42_ = (base42_ + half42_) % SIZE;\n'
           '     base42_; })',
  'name': 'cache_oblivious_funnel_stride',
  'setup': 'uint32_t base42_ = 0u;',
  'teardown': ''},
 {'desc': 'each complete pass through the buffer (SIZE accesses) starts at a rotated offset '
          '(shifted by 97 each pass) -- rotation across full passes, distinct from the local '
          'overlapping window in c11',
  'id': 'c43',
  'index': '({ rot_offset43_ = (uint32_t)((i / ROT_PERIOD43_) * 97u) % SIZE;\n'
           '     (uint32_t)((i % SIZE) + rot_offset43_) % SIZE; })',
  'name': 'whole_buffer_rotation',
  'setup': 'const uint64_t ROT_PERIOD43_ = SIZE; uint32_t rot_offset43_ = 0u;',
  'teardown': ''},
 {'desc': "alternates every access between a unit-stride 'A row' read and a large-stride 'B "
          "column' read over a synthetic square matrix -- models the classic "
          'good-locality/bad-locality split inside a naive triple-nested-loop GEMM inner loop',
  'id': 'c44',
  'index': '({ uint32_t k44_ = (uint32_t)(i % dim44_);\n'
           '     uint32_t row44_ = (uint32_t)((i / dim44_) % dim2_44_);\n'
           '     ((i & 1u) == 0u) ? (uint32_t)((row44_ * dim44_ + k44_) % SIZE)\n'
           '                      : (uint32_t)((k44_ * dim2_44_ + row44_) % SIZE); })',
  'name': 'naive_gemm_row_col_alternation',
  'setup': 'uint32_t dim44_ = 1u;\n'
           '    for (uint32_t d_ = 2u; d_ * d_ <= SIZE; d_++) { if (SIZE % d_ == 0u) dim44_ = d_; '
           '}\n'
           '    if (dim44_ == 0u) dim44_ = 1u;\n'
           '    uint32_t dim2_44_ = SIZE / dim44_; if (dim2_44_ == 0u) dim2_44_ = 1u;',
  'teardown': ''},
 {'desc': '80% of accesses stay confined to the first half of the buffer (read-hot region), 20% '
          'touch the second half (write-cold scratch) -- models separated hot constant data vs. '
          'cold scratch data',
  'id': 'c45',
  'index': '(((i % 5u) != 0u) ? (uint32_t)(i % half45_) : (uint32_t)(half45_ + (i % half45_)))',
  'name': 'read_hot_write_cold_split',
  'setup': 'uint32_t half45_ = (SIZE / 2u) > 0u ? SIZE / 2u : 1u;',
  'teardown': ''},
 {'desc': 'visits only indices whose base-3 representation has no digit equal to 1 (the classic '
          'Cantor-set indicator function) -- a deterministic, self-similar sparse pattern',
  'id': 'c46',
  'index': '({ uint32_t n46_ = (uint32_t)(i % SIZE); uint32_t t46_ = n46_; int valid46_ = 1;\n'
           '     for (int d46_ = 0; d46_ < 16 && t46_ > 0u; d46_++) {\n'
           '       if ((t46_ % 3u) == 1u) { valid46_ = 0; break; }\n'
           '       t46_ /= 3u;\n'
           '     }\n'
           '     valid46_ ? n46_ : (n46_ ^ 1u) % SIZE; })',
  'name': 'cantor_gap_sparse',
  'setup': '',
  'teardown': ''},
 {'desc': "four simulated 'threads' round-robin through adjacent offsets within the same "
          '16-element block before advancing -- models false-sharing-style adjacent access without '
          'actual concurrency',
  'id': 'c47',
  'index': '({ uint32_t thread47_ = (uint32_t)(i % NTHREADS47_);\n'
           '     uint32_t nblocks47_ = (SIZE / LINEWIDTH47_) > 0u ? SIZE / LINEWIDTH47_ : 1u;\n'
           '     uint32_t block47_ = (uint32_t)((i / NTHREADS47_) % nblocks47_);\n'
           '     (uint32_t)(block47_ * LINEWIDTH47_ + thread47_) % SIZE; })',
  'name': 'false_sharing_line_cycle',
  'setup': 'const uint32_t NTHREADS47_ = 4u; const uint32_t LINEWIDTH47_ = 16u;',
  'teardown': ''},
 {'desc': 'jump distance from a base position doubles each attempt (1,2,4,...,32768) until a '
          'random reset picks a new base -- models retry-with-backoff memory/lock probing',
  'id': 'c48',
  'index': '({ uint32_t jump48_ = 1u << (attempt48_ % 16u);\n'
           '     uint32_t idx48_ = (base48_ + jump48_) % SIZE;\n'
           '     attempt48_++;\n'
           '     if ((lcg_next(&cseed48_) % 8u) == 0u) { base48_ = lcg_next(&cseed48_) % SIZE; '
           'attempt48_ = 0u; }\n'
           '     idx48_; })',
  'name': 'exponential_backoff_probe',
  'setup': 'uint32_t base48_ = 0u; uint32_t attempt48_ = 0u; uint32_t cseed48_ = SEED0 ^ '
           '0x48484848u;',
  'teardown': ''},
 {'desc': 'randomly walks parent/child relationships of an implicit binary heap (index -> '
          '2*index+1, 2*index+2, or (index-1)/2) -- a tree-structured access pattern common in '
          'heap-sort / priority-queue code',
  'id': 'c49',
  'index': '({ uint32_t choice49_ = lcg_next(&cseed49_) % 3u; uint32_t next49_;\n'
           '     if (choice49_ == 0u) next49_ = 2u * heappos49_ + 1u;\n'
           '     else if (choice49_ == 1u) next49_ = 2u * heappos49_ + 2u;\n'
           '     else next49_ = (heappos49_ > 0u) ? (heappos49_ - 1u) / 2u : 0u;\n'
           '     if (next49_ >= SIZE) next49_ = 0u;\n'
           '     heappos49_ = next49_;\n'
           '     heappos49_; })',
  'name': 'binary_heap_parent_child',
  'setup': 'uint32_t heappos49_ = 0u; uint32_t cseed49_ = SEED0 ^ 0x49494949u;',
  'teardown': ''},
 {'desc': 'mostly sequential streaming access, but jumps back to index 0 every 10000 accesses -- '
          'tests transient cold-start effects layered on top of otherwise good locality',
  'id': 'c50',
  'index': '(uint32_t)(((i % FLUSH_PERIOD50_) == 0u) ? 0u : (i % SIZE))',
  'name': 'streaming_with_periodic_flush',
  'setup': 'const uint64_t FLUSH_PERIOD50_ = 10000u;',
  'teardown': ''}]

RECURSE_HELPER = '\nstatic uint32_t recurse_helper(uint32_t depth, uint32_t acc) {\n    if (depth == 0u) return acc;\n    if ((depth & 1u) != 0u) {\n        return recurse_helper(depth - 1u, acc + depth);\n    } else {\n        return recurse_helper(depth - 1u, acc ^ depth);\n    }\n}\n'
DISPATCH_TABLE_HELPER = '\nstatic uint32_t dfn0(uint32_t x) { return x + 3u; }\nstatic uint32_t dfn1(uint32_t x) { return x ^ 0x5A5Au; }\nstatic uint32_t dfn2(uint32_t x) { return (x << 1) | (x >> 31); }\nstatic uint32_t dfn3(uint32_t x) { return x - 7u; }\ntypedef uint32_t (*dispatch_fn_t)(uint32_t);\nstatic dispatch_fn_t dispatch_table[4] = { dfn0, dfn1, dfn2, dfn3 };\n'
MUTUAL_RECURSE_HELPER = '\nstatic uint32_t mutB47(uint32_t depth, uint32_t acc);\nstatic uint32_t mutA47(uint32_t depth, uint32_t acc) {\n    if (depth == 0u) return acc;\n    return mutB47(depth - 1u, acc + depth);\n}\nstatic uint32_t mutB47(uint32_t depth, uint32_t acc) {\n    if (depth == 0u) return acc;\n    return mutA47(depth - 1u, acc ^ depth);\n}\n'
MAIN_TEMPLATE = '\n{recurse_helper_block}\nint main(void) {{\n    uint32_t *buf = (uint32_t *)lowbit_aligned_alloc(sizeof(uint32_t) * SIZE);\n    if (!buf) {{ fprintf(stderr, "alloc failure\\n"); return 1; }}\n    {{\n        uint32_t iseed_ = SEED0;\n        for (uint32_t k_ = 0u; k_ < SIZE; k_++) buf[k_] = lcg_next(&iseed_);\n    }}\n\n    uint32_t bseed_local = SEED0 ^ 0xC0FFEEu;\n    uint32_t *bseed = &bseed_local;\n    int prev_taken_local = 0;\n    int *prev_taken = &prev_taken_local;\n\n    {cache_setup}\n\n    /* ---- warm-up (untimed): primes caches / predictor / pattern state\n     * with the exact same access pattern before measurement begins ---- */\n    uint64_t warmup_n = N / 10u;\n    if (warmup_n > 200000u) warmup_n = 200000u;\n    volatile uint64_t warmup_sink = 0;\n    for (uint64_t i = 0; i < warmup_n; i++) {{\n        uint32_t idx = {cache_index};\n        uint32_t v = buf[idx];\n        int taken;\n        {branch_code}\n        if (taken) {{ warmup_sink += v; }} else {{ warmup_sink -= (v >> 1); }}\n        buf[idx] = v + 1u;\n    }}\n\n    /* ---- region of interest (timed): this is what gets reported ---- */\n    volatile uint64_t sink = 0;\n    uint64_t taken_count = 0, nottaken_count = 0;\n\n    lowbit_timer_t timer;\n    lowbit_roi_start(&timer);\n\n    for (uint64_t i = 0; i < N; i++) {{\n        uint32_t idx = {cache_index};\n        uint32_t v = buf[idx];\n        int taken;\n        {branch_code}\n        if (taken) {{\n            sink += v;\n            taken_count++;\n        }} else {{\n            sink -= (v >> 1);\n            nottaken_count++;\n        }}\n        buf[idx] = v + 1u;\n    }}\n\n    lowbit_roi_stop(&timer);\n\n    {cache_teardown}\n\n    int validation_pass = ((taken_count + nottaken_count) == N) ? 1 : 0;\n\n    printf("program={fname}\\n");\n    printf("branch_pattern={bname}\\n");\n    printf("cache_pattern={cname}\\n");\n    printf("predictor_stress_type={bstress}\\n");\n    printf("expected_dominant_miss_type={cmiss}\\n");\n    printf("size_tier={tier_name}\\n");\n    printf("backend=%s\\n", LOWBIT_BACKEND);\n    printf("compiler=%s\\n", lowbit_compiler_version());\n    printf("arch=%s\\n", lowbit_arch_name());\n    printf("SIZE=%u N=%u SEED=%u\\n", (unsigned)SIZE, (unsigned)N, (unsigned)SEED0);\n    printf("warmup_iterations=%llu\\n", (unsigned long long)warmup_n);\n    printf("taken=%llu nottaken=%llu\\n",\n           (unsigned long long)taken_count, (unsigned long long)nottaken_count);\n    printf("sink=%llu\\n", (unsigned long long)sink);\n    printf("validation=%s\\n", validation_pass ? "PASS" : "FAIL");\n    printf("roi_elapsed_seconds=%.9f\\n", lowbit_roi_seconds(&timer));\n\n    /* Explicit fflush + _Exit (not return/exit): modern statically-\n     * linked glibc\'s normal exit() path does pthread-related futex\n     * cleanup that some emulators\' syscall-emulation modes (notably\n     * gem5 SE mode) cannot resolve, causing the process to hang\n     * indefinitely at 100% CPU after main() has already finished and\n     * printed all its output. _Exit() skips that cleanup path\n     * entirely and goes straight to the exit syscall; fflush(stdout)\n     * first guarantees the printf output above isn\'t lost, since\n     * _Exit() does NOT flush stdio buffers on its own (this matters\n     * because stdout is fully buffered, not line-buffered, whenever\n     * it\'s redirected to a file -- exactly how every run script here\n     * captures it). */\n    fflush(stdout);\n    _Exit(validation_pass ? 0 : 2);\n}}\n'


# Heuristic classification labels (same rules as the original catalogues) --
# used only for the self-reported predictor_stress_type /
# expected_dominant_miss_type metadata fields, never as ground truth.


def classify_branch(entry):
    text = (entry["id"] + " " + entry["name"] + " " + entry["desc"]).lower()
    def has(*words):
        return any(w in text for w in words)
    if has("always", "never"):
        return "static/trivial"
    if has("random", "pseudo", "chaos", "hash", "xorshift", "hazard"):
        return "stochastic"
    if has("correlat", "history", "markov", "meta_correlated", "xor"):
        return "history-correlated"
    if has("recursi", "stack", "call", "mutual"):
        return "control-flow/call-return"
    if has("switch", "indirect", "dispatch", "jump"):
        return "indirect-branch"
    if has("period", "altern", "prime_period"):
        return "static/periodic"
    if has("drift", "phase", "warmup", "warm_up", "burst", "hysteresis", "debounce"):
        return "phase-changing/non-stationary"
    if has("value", "popcount", "collatz", "threshold", "parity", "sign"):
        return "value-dependent"
    if has("countdown", "loop_carried", "counters_race", "saturating"):
        return "stateful/loop-carried"
    return "mixed/other"


def classify_cache(entry):
    text = (entry["id"] + " " + entry["name"] + " " + entry["desc"]).lower()
    def has(*words):
        return any(w in text for w in words)
    if has("sequential", "stride") and not has("random", "conflict"):
        return "compulsory+streaming (prefetch-friendly)"
    if has("pointer_chase", "linked_list", "skip_list"):
        return "compulsory+latency-bound (dependent loads, no prefetch)"
    if has("conflict", "bank", "false_sharing", "set_conflict"):
        return "conflict/associativity"
    if (has("random") and has("small")) or has("lru", "recency"):
        return "temporal reuse (capacity-sensitive)"
    if has("random"):
        return "capacity (uniform random)"
    if has("matrix", "tiled", "morton", "hilbert", "diagonal", "transpose", "wavefront"):
        return "mixed spatial (traversal-order dependent)"
    if has("zipf", "hot", "hot_cold"):
        return "temporal reuse (skewed/hot-set)"
    if has("sparse", "checkerboard", "cantor", "block_sparse"):
        return "compulsory (sparse, low reuse)"
    if has("gather", "scatter", "hash_probe", "cuckoo"):
        return "capacity+compulsory (indirect gather/scatter)"
    if has("ping_pong", "double_buffer", "thrash"):
        return "conflict+capacity (thrashing/alternation)"
    return "mixed/unclassified"



# Synthetic baselines for the 100 single-family programs. Deliberately NOT
# entries from BRANCHES/CACHES above -- a branch-only program must not
# depend on "which cache family we picked as neutral", and vice versa.


SYNTHETIC_CACHE_NEUTRAL = dict(
    setup="", teardown="",
    index="((uint32_t)(i % SIZE))",   # plain sequential scan, no catalogue entry
)
SYNTHETIC_BRANCH_NEUTRAL = dict(code="taken = 1;")  # always-taken, no catalogue entry


# Crisp per-file header. States what the file is, nothing else.


HEADER = """/*
 * LowBit -- {fname}
 * Branch: {bname} | Cache: {cname}
 * Generated by lowbit_corpus_extend.py. Do not hand-edit;
 * change the catalogue in that script and regenerate instead.
 */

#include "lowbit_runtime.h"

#ifndef SIZE
#define SIZE {size}u
#endif
#ifndef N
#define N {n}u
#endif
#define SEED0 0x9E3779B9u
"""

MAIN_TEMPLATE_TAIL = MAIN_TEMPLATE  # from the literal block above


def build_program(fname, b, c, tier):
    bstress = classify_branch(b) if "desc" in b else "n/a (synthetic baseline)"
    cmiss = classify_cache(c) if "desc" in c else "n/a (synthetic baseline)"
    bname = b.get("name", "synthetic_always_taken")
    cname = c.get("name", "synthetic_sequential_scan")

    header = HEADER.format(fname=fname, bname=bname, cname=cname,
                            size=tier[1], n=tier[2])

    extra_helpers = ""
    if "recurse_helper(" in b["code"]:
        extra_helpers += RECURSE_HELPER
    if "dispatch_table" in b["code"]:
        extra_helpers += DISPATCH_TABLE_HELPER
    if "mutA47(" in b["code"]:
        extra_helpers += MUTUAL_RECURSE_HELPER

    body = MAIN_TEMPLATE_TAIL.format(
        recurse_helper_block=extra_helpers,
        cache_setup=("    " + c["setup"].replace("\n", "\n    ")) if c.get("setup") else "",
        cache_index=c["index"],
        branch_code=b["code"],
        cache_teardown=("    " + c["teardown"]) if c.get("teardown") else "",
        fname=fname, bname=bname, cname=cname, tier_name=tier[0],
        bstress=bstress, cmiss=cmiss,
    )
    return header + body


def write_manifest(rows):
    fieldnames = ["idx", "file", "branch_id", "branch_name", "cache_id", "cache_name",
                  "size_tier", "SIZE", "N", "predictor_stress_type",
                  "expected_dominant_miss_type", "kind"]
    with open(MANIFEST_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def gen_full_grid(rows, start_idx):
    """All 2500 branch x cache cells, numbered sequentially from
    start_idx -- a full fresh sweep, not just the 2000 the original 500
    didn't cover. Re-running this overwrites the same 2500 filenames
    each time (deterministic), so it's naturally idempotent."""
    idx = start_idx
    for bi, b in enumerate(BRANCHES):
        for ci, c in enumerate(CACHES):
            tier = TIERS[(bi + ci) % len(TIERS)]
            fname = f"{idx:04d}_grid_{b['id']}_{b['name']}__{c['id']}_{c['name']}.c"
            text = build_program(fname, b, c, tier)
            with open(os.path.join(OUT_DIR, fname), "w") as f:
                f.write(text)
            rows.append(dict(
                idx=idx, file=fname, branch_id=b["id"], branch_name=b["name"],
                cache_id=c["id"], cache_name=c["name"], size_tier=tier[0],
                SIZE=tier[1], N=tier[2],
                predictor_stress_type=classify_branch(b),
                expected_dominant_miss_type=classify_cache(c),
                kind="grid",
            ))
            idx += 1
    print(f"[grid] generated {idx - start_idx} programs, full 50x50 = 2500 coverage")
    return idx


def gen_pure_families(rows, start_idx):
    idx = start_idx
    tier = TIERS[0]

    for b in BRANCHES:
        fname = f"{idx:04d}_base_branch_{b['id']}_{b['name']}.c"
        text = build_program(fname, b, SYNTHETIC_CACHE_NEUTRAL, tier)
        with open(os.path.join(OUT_DIR, fname), "w") as f:
            f.write(text)
        rows.append(dict(
            idx=idx, file=fname, branch_id=b["id"], branch_name=b["name"],
            cache_id="", cache_name="synthetic_sequential_scan",
            size_tier=tier[0], SIZE=tier[1], N=tier[2],
            predictor_stress_type=classify_branch(b),
            expected_dominant_miss_type="n/a (synthetic baseline)",
            kind="branch_only",
        ))
        idx += 1

    for c in CACHES:
        fname = f"{idx:04d}_base_cache_{c['id']}_{c['name']}.c"
        text = build_program(fname, SYNTHETIC_BRANCH_NEUTRAL, c, tier)
        with open(os.path.join(OUT_DIR, fname), "w") as f:
            f.write(text)
        rows.append(dict(
            idx=idx, file=fname, branch_id="", branch_name="synthetic_always_taken",
            cache_id=c["id"], cache_name=c["name"],
            size_tier=tier[0], SIZE=tier[1], N=tier[2],
            predictor_stress_type="n/a (synthetic baseline)",
            expected_dominant_miss_type=classify_cache(c),
            kind="cache_only",
        ))
        idx += 1

    print(f"[pure] generated 50 branch_only + 50 cache_only baseline programs")
    return idx


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    idx = gen_full_grid(rows, start_idx=1)
    gen_pure_families(rows, start_idx=idx)
    write_manifest(rows)
    print(f"Done. programs/ has {len(rows)} programs, numbered 0001..{len(rows):04d}; "
          f"MANIFEST.csv written fresh at the repo root.")


if __name__ == "__main__":
    main()