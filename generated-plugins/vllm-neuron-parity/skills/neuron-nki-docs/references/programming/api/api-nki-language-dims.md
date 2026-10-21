# NKI Language - Dimensions

> **Module**: nki.language
> **Total Functions**: 7

## Overview

Dimension and range management functions.

## Functions

### nki.language.affine_range {#nki-language-affine_range}

# nki.language.affine_range

nki.language.affine_range

nki.language.affine_range(*start*, *stop=None*, *step=1*)[[source]](../../../_modules/nki/language.html#affine_range)

**Deprecated.** Use standard Python `range` instead. In NKI 0.3.0, all range iterators (`affine_range`, `sequential_range`, `static_range`) have identical effect.

---

### nki.language.sequential_range {#nki-language-sequential_range}

# nki.language.sequential_range

nki.language.sequential_range

nki.language.sequential_range(*start*, *stop=None*, *step=1*)[[source]](../../../_modules/nki/language.html#sequential_range)

**Deprecated.** Use standard Python `range` instead. In NKI 0.3.0, all range iterators (`affine_range`, `sequential_range`, `static_range`) have identical effect.

---

### nki.language.static_range {#nki-language-static_range}

# nki.language.static_range

nki.language.static_range

nki.language.static_range(*start*, *stop=None*, *step=1*)[[source]](../../../_modules/nki/language.html#static_range)

**Deprecated.** Use standard Python `range` instead. In NKI 0.3.0, all range iterators (`affine_range`, `sequential_range`, `static_range`) have identical effect.

---

### nki.language.num_programs {#nki-language-num_programs}

# nki.language.num_programs

nki.language.num_programs

nki.language.num_programs(*axes=None*)[[source]](../../../_modules/nki/language.html#num_programs)
Number of SPMD programs along the given axes in the launch grid. If `axes` is not provided,
returns the total number of programs.

Parameters:
**axes** – The axes of the ND launch grid. If not provided, returns the total number of programs along the entire launch grid.

Returns:
The number of SPMD(single process multiple data) programs along `axes` in the launch grid

---

### nki.language.program_id {#nki-language-program_id}

# nki.language.program_id

nki.language.program_id

nki.language.program_id(*axis*)[[source]](../../../_modules/nki/language.html#program_id)
Index of the current SPMD program along the given axis in the launch grid.

Parameters:
**axis** – The axis of the ND launch grid.

Returns:
The program id along `axis` in the launch grid

---

### nki.language.program_ndim {#nki-language-program_ndim}

# nki.language.program_ndim

nki.language.program_ndim

nki.language.program_ndim()[[source]](../../../_modules/nki/language.html#program_ndim)
Number of dimensions in the SPMD launch grid.

Returns:
The number of dimensions in the launch grid, i.e. the number of axes

---

### nki.language.tile_size {#nki-language-tile_size}

# nki.language.tile_size

nki.language.tile_size

*class *nki.language.tile_size[[source]](../../../_modules/nki/language.html#tile_size)
Tile size constants.

Attributes


| bn_stats_fmax | Maximum free dimension of BN_STATS |
| --- | --- |
| gemm_moving_fmax | Maximum free dimension of the moving operand of General Matrix Multiplication on Tensor Engine |
| gemm_stationary_fmax | Maximum free dimension of the stationary operand of General Matrix Multiplication on Tensor Engine |
| pmax | Maximum partition dimension of a tile |
| psum_fmax | Maximum free dimension of a tile on PSUM buffer, in FP32 elements |
| psum_fmax_bytes | Maximum free dimension of a tile on PSUM buffer, in bytes |
| psum_num_banks | Number of usable PSUM banks per partition |
| sbuf_size_bytes | Total SBUF capacity in bytes (all partitions combined) |
| sbuf_fmax | Maximum free dimension of a tile on SBUF buffer, in FP32 elements |
| sbuf_fmax_bytes | Maximum free dimension of a tile on SBUF buffer, in bytes |
| psum_min_align | Minimum byte alignment requirement for PSUM free dimension address |
| sbuf_min_align | Minimum byte alignment requirement for SBUF free dimension address |
| total_available_sbuf_size | **Deprecated.** Use `sbuf_fmax_bytes` (per-partition) or `sbuf_size_bytes` (total) |

---

### nki.language.dynamic_range {#nki-language-dynamic_range}

`nki.language.dynamic_range(start, stop, step)`

**Signature:**
```python
language.dynamic_range(start, stop=None, step=1)
```

Create a sequence for **dynamic** loop iteration.

Create a sequence of numbers for use as **dynamic** loop iterators in NKI.
The loop runs on device with dynamic bounds.

- **start** — start value (or stop if `stop` is None), can be VirtualRegister.
- **stop** — stop value (exclusive), can be VirtualRegister.
- **step** — step size, must be a compile-time positive integer (not VirtualRegister).
**Returns:** an iterator yielding integer values from start to stop.

Examples:

```python
import nki.language as nl

# nki.language.dynamic_range -- dynamic iteration with runtime bounds
for _ in nl.dynamic_range(1):
    tile = nl.load(input_tensor[0:128, 0:512])
    result = nl.multiply(tile, tile)
    nl.store(out_tensor[0:128, 0:512], result)
```

---
