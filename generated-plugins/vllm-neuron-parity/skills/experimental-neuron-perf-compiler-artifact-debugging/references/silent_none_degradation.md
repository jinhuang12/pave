# Root-causing "'NoneType' object is not callable" and similar mystery TypeErrors

A recurring pattern in this framework stack (torch.dynamo HigherOrderOperator
dispatch, NKI kernel wrappers, any module with an optional-dependency import):
a deeply-nested traceback ends in a generic `TypeError("'NoneType' object is not
callable")` or `'NoneType' object has no attribute ...` with no explanation of
*which* name was `None` or *why*. Do not accept a subagent's (or your own) first
plausible-sounding explanation for this shape of error without following the
trail below — it is almost always a **silently-swallowed exception several frames
away** from where the crash actually surfaces, not a genuinely missing handler at
the crash site.

## The pattern to search for

```python
try:
    from .some_module import thing_a, thing_b
except (ImportError, KeyError):
    thing_a = None
    thing_b = None
```

Any module-level `try/except ImportError` (or `except (ImportError, KeyError)`,
`except Exception`, etc.) that degrades a name to `None` instead of re-raising or
logging is a landmine: the module imports fine, everything looks normal, and the
failure only surfaces later — often in a completely different subsystem (e.g. a
`torch.dynamo` FakeTensorMode dispatch path) — as `None(...)`, far from the `try`
block that caused it.

## Verification procedure (do this before trusting any "missing handler" claim)

1. **Get the real traceback, not a summary.** Find the actual crash log/worker
   output on disk. A subagent's prose description of a traceback is not primary
   evidence — read the raw file yourself (`grep -B3 -A30 "Traceback"`, or
   `fs_read`/`cat` the log directly). Confirm the *exact* call chain: which
   function called which function called the thing that ended up `None`.

2. **Find the call site of the thing that's `None`.** The traceback's last few
   frames before the `TypeError` show which name was called. Trace it back to its
   *definition*, not just its usage — `grep`/`search_symbols` for where that name
   is assigned, not just where it's called. If a static `grep` of the apparent
   call site finds nothing, the name may be a dynamically-registered op (e.g. a
   `torch._ops.HigherOrderOperator` instance registered via
   `allow_in_graph`/`py_impl`) rather than a plain function — check one frame up
   in the traceback for the module that actually defines/registers it.

3. **Check every `try/except ImportError` (or broader) between the crash site and
   the module's top-level imports.** This is the single most common location for
   this bug class. Reproduce the import failure directly and standalone:
   ```bash
   <venv>/bin/python3 -c "from <package>.<submodule> import <thing>"
   ```
   If this raises `ModuleNotFoundError`/`ImportError` on its own, you've found the
   real root cause — the crash-site `None` is a downstream symptom, not the bug.

4. **Confirm the module genuinely doesn't exist** (vs. a path/env issue) — `ls`/
   `find` the package's installed directory directly:
   ```bash
   ls <site-packages>/<package>/<subpackage>/
   ```
   If the submodule file is absent, check whether it exists in a **different,
   newer install of the same package** elsewhere on the machine (other venvs,
   other project checkouts, a local wheel cache) — compare versions
   (`pip show <package>` in each) before assuming a fix requires new code. A
   missing submodule is often just an incomplete/stale package build, fixable by
   a version-matched upgrade, not evidence the feature was never implemented.

5. **Do not conflate "no FakeTensorMode/dispatch handler registered" with "a
   handler is registered but one of its dependencies silently failed to
   import."** These sound similar in a subagent's summary but have completely
   different fixes (write a new handler vs. install/repair a package). Read the
   handler's actual source to tell them apart — if a `py_impl(FakeTensorMode)` (or
   equivalent) function exists and looks complete, the bug is almost certainly
   upstream of it (an import it silently depends on), not in it.

## Worked example (real incident)

`torch._dynamo` FakeTensor tracing of an NKI-kernel `HigherOrderOperator` call
raised `TypeError("'NoneType' object is not callable")` deep inside
`_get_fake_value_impl`. A subagent claimed "missing FakeTensorMode handler for the
op" — this was checked and found **false**: a `@nki_kernel_wrapper.py_impl(FakeTensorMode)`
handler existed, complete and correct. The real cause, found by reading the
handler's own call chain: it called `compile_nki(...)`, which was set to `None` by
a module-level

```python
try:
    from .nki_compile import NKICompileResult, compile_nki
except (ImportError, KeyError):
    NKICompileResult = None
    compile_nki = None
```

Reproducing the import standalone (`python3 -c "from pkg.nki import nki_compile"`)
gave a direct `ModuleNotFoundError` — the submodule was simply missing from that
venv's installed package build. A newer local build of the same package (found via
`find / -iname "nki_compile.py"`, comparing `pip show` versions across venvs on the
same machine) had the file. The fix is a version-matched package upgrade, not a
new FakeTensorMode implementation — the opposite of the original claim's implied
fix (which would have meant writing new dispatch code).
