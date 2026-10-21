# CONFLICTS / REFINEMENT — armed single-rank device profile: A4's "raise-the-watchdog is the fix" vs the observed SUCCESS recipe

**Status:** a narrow refinement to an existing lesson, driven by a SUCCESS. Recorded per the
"conflicting/refining lesson → doc" rule. Anonymized; applies to any collective-heavy graph on any host.
The positive recipe is now captured as KERNEL lesson **A12**; this doc records why it refines **A4**.

## Headline: single-rank device profiling SUCCEEDED
A single-rank armed **device_profile** of a deep, per-layer-collective decode graph **captured cleanly
with per-kernel source attribution** — the analysis then ran in full mode and ranked the model's
kernels. Single-rank is the right approach, exactly as **A4** already states ("pinning one rank is the
working default"). There is **no conflict about profiling a single rank** — it works.

## The narrow refinement to A4
A4's stated remedy for the armed-profiler hang is: **"Raise the per-execution watchdog before any
instrumented capture. This is the fix."** In this effort the watchdog WAS raised well above the
uninstrumented step time, and a **wide / all-rank** armed window still **deadlocked** the collective
(the runtime reported a collective with a **START notification and no completion** — a barrier that
never releases, which no watchdog value can rescue). The capture only succeeded once the **armed window
was minimized to a single profiled step on one rank**, with the source-attribution debug environment
set.

So A4's watchdog guidance is **necessary but not sufficient**: it fixes the *straggler* case (a rank
that finishes late), but not the *deadlock* case (a barrier that never completes under a heavy armed
window). The **decisive lever A4 omits is window minimization** — keep the armed window as small as one
profiled step, which for per-kernel attribution is usually enough.

## Resolution (fold into A4 when reconciling; already in A12)
Branch on the runtime failure signature:
- **Straggler (finishes late):** plain per-execution timeout, no START-only collective → **raise the
  watchdog** (A4 as written).
- **Deadlock (never completes):** a collective shows **START without completion** and a raised watchdog
  still fires → a longer watchdog cannot help. **Minimize the instrumentation perturbation**: single
  rank, single-step armed window, source-attribution env; re-arm. This is the recipe that SUCCEEDED and
  is captured as A12. If it still deadlocks, fall back to system-trace-only and record a verified blocker.

Net: A4's single-rank default is CONFIRMED by this success. The only correction is that "raise the
watchdog is the fix" is incomplete — window minimization is what makes a single-rank device profile of a
collective-heavy graph actually complete, and it is now the primary rule in A12.
