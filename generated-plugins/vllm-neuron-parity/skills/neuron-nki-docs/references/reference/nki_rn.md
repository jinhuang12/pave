# Neuron Kernel Interface (NKI) release notes

Neuron Kernel Interface (NKI) release notes

## Neuron Kernel Interface (NKI) [0.7.0] (Neuron 2.33.0 Release)

Date of Release: 10/19/2026

AWS Neuron SDK 2.33.0 introduces NKI 0.7.0. In this release, NKI specializes kernels by tracing them with Python, so kernel authors can use the full Python language for meta-programming. This release also adds tiled/blocked on-chip memory layout APIs and prepare/trigger splits for variable-length collectives.

### New Features

* **Specialization by Python tracing**: NKI processes a kernel in three stages: specialization, compilation, and graph-compiler linking (see the compilation model in [NKI Language Guide](../programming/nki-language-guide.md#nki-language-guide)). In previous NKI versions, specialization ran as dedicated compiler lowering passes over the kernel code, producing the IR to be compiled. In NKI 0.7.0, specialization traces the kernel code with Python, and the NKI ISA APIs emit the IR to be compiled. Kernels can use all of Python for meta-programming, including constructs the previous specialization did not support, such as generators and class inheritance. Meta-programming code follows standard Python semantics. Kernels using a dynamic loop construct should move to [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop) or [`while_loop()`](../programming/api/api-nki-language-dims.md#nki-language-while_loop). To continue using kernels without updating, set `NKI_COMPILER_FRONTEND=custom`. To update kernels, see [Migrating from NKI 0.6.0 to NKI 0.7.0](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/migration/index.html#nki-migrate-0-6-to-0-7).

* **Tiled and blocked memory layouts**: [`tiles()`](../programming/api/api-nki-language-layout.md#nki-language-tiles) decomposes a tensor into a logical grid of fixed-size tiles addressed by tile coordinate, and [`blocks()`](../programming/api/api-nki-language-layout.md#nki-language-blocks) groups tiles into a coarser transfer or reuse grain above the compute grain. [`alloc_tiles()`](../programming/api/api-nki-language-layout.md#nki-language-alloc_tiles) and [`alloc_blocks()`](../programming/api/api-nki-language-layout.md#nki-language-alloc_blocks) allocate storage organized the same way. Creating or indexing a grid view ([`NkiTileGrid`](../programming/api/api-nki-language-layout.md#nki-language-NkiTileGrid), [`NkiBlockGrid`](../programming/api/api-nki-language-layout.md#nki-language-NkiBlockGrid)) allocates no storage and moves no data.

* **nl.log1p**: New [`log1p()`](../programming/api/api-nki-language-operators.md#nki-language-log1p) computes the natural log of `1 + x` on the GpSimd Engine on NeuronCore-v4+, available as an activation function through [`activation()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation) and [`activate2()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activate2). See the supported activation functions table in [nki.api.shared](../programming/api/nki.api.shared.md).

* **Profiler annotation regions**: `nki.language.profiler_region_open` and `nki.language.profiler_region_close` mark a named region; every instruction between them appears as a named region on the Neuron Explorer timeline. The annotation adds only compile-time metadata and has no execution-time cost. Regions nest, and leaving one open is a compilation error. To see the regions in Neuron Explorer, set `NKI_DEBUG_INFO=True` at compile time. See [Define NKI Regions in Your Kernel](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/guides/use-neuron-profile.html#nki-define-regions).

* **Collectives**:

**Variable-length prepare/trigger split**: [`all_to_all_v()`](../programming/api/api-nki-collectives.md#nki-collectives-all_to_all_v) and [`all_gather_v()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather_v) can be split into a `prepare` and a `trigger` with [`all_to_all_v_prepare()`](../programming/api/api-nki-collectives.md#nki-collectives-all_to_all_v_prepare) and [`all_to_all_v_trigger()`](../programming/api/api-nki-collectives.md#nki-collectives-all_to_all_v_trigger), or [`all_gather_v_prepare()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather_v_prepare) and [`all_gather_v_trigger()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather_v_trigger). The `prepare` sets up the collective’s metadata and can be issued before `srcs` are filled, so the setup runs ahead of the instruction that fills `srcs` and overlaps with unrelated compute. The `trigger` runs the phase that reads `srcs` and writes `dsts`, and takes only the paired `prepare` instruction; each `prepare` pairs with exactly one `trigger`, with no other `prepare` in between. This helps when the per-rank counts are known before the data, as in a mixture-of-experts layer.

* **ReplicaGroup.from_partition_op**: Build a [`ReplicaGroup`](../programming/api/api-nki-collectives.md#nki-collectives-ReplicaGroup) by partitioning ranks `[0, world_size)` with `nki.collectives.rg_partition_op`, where ranks sharing the same `op(rank, operand)` form one rank-list: `div` for contiguous rank-lists, `mod` for strided, `bitwise_and` for a bitmask. This replaces spelling out the full list of rank-lists.

* **nki.isa.sendrecv_compute**: [`sendrecv_compute()`](../programming/api/api-nki-isa-local-collective.md#nki-isa-sendrecv_compute) performs point-to-point communication between NeuronCores that reduces the local and received tiles into a single result using the DMA engine. The current NeuronCore sends its `src` to `send_to_rank`, receives the peer tile from `recv_from_rank`, and writes `reduce_op(src, peer)` to `dst`. Available only at LNC degree greater than 1; `reduce_op` is `nl.add`, `nl.maximum`, or `nl.minimum`.

* **Multi-rank CPU simulation for fixed-size collectives**: [`simulate()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html#nki.simulate) runs a kernel’s fixed-size [`all_reduce()`](../programming/api/api-nki-collectives.md#nki-collectives-all_reduce), [`all_gather()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather), [`reduce_scatter()`](../programming/api/api-nki-collectives.md#nki-collectives-reduce_scatter), and [`all_to_all()`](../programming/api/api-nki-collectives.md#nki-collectives-all_to_all), along with [`rank_id()`](../programming/api/api-nki-collectives.md#nki-collectives-rank_id), across multiple ranks on CPU, which validates a collective kernel’s numerics without hardware. Ranks exchange data through a communicator: in a `torch.distributed` world the simulator uses the framework’s ranks, and it reduces to a single rank otherwise. Supported at LNC1 and LNC2. Variable-length and permute collectives remain hardware-only.

* **NKI kernel LNC configuration**: The Logical NeuronCore (LNC) degree for a NKI kernel can now be set using the `NEURON_LOGICAL_NC_CONFIG` environment variable, which is the preferred way to explicitly configure the kernel LNC degree. The existing launch-grid bracket syntax, such as `kernel[1](...)` or `kernel[2](...)`, remains supported for backward compatibility. If both are specified, bracket syntax takes precedence over the environment variable. For more information, see [LNC Overview](../programming/lnc.md).

* **nki.isa.topk sorted option**: [`topk()`](../programming/api/api-nki-isa-tensor.md#nki-isa-topk) accepts `sorted=True` to order each 16-partition group’s results descending by value, keeping `idx_dst[j]` the source index of `val_dst[j]`. The default leaves the order unspecified. With `sorted=True`, `k` must be at most 8192, and ties between equal values may resolve in any order.

* **Scalar values**: New [`NkiScalar`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.isa.NkiScalar.html#nki.isa.NkiScalar) holds one integer computed on device, loaded from an `nl.int32` or `nl.int64` tile with [`scalar_load()`](../programming/api/api-nki-isa-misc.md#nki-isa-scalar_load) and written back with [`scalar_store()`](../programming/api/api-nki-isa-misc.md#nki-isa-scalar_store). A scalar can be used as an operand of arithmetic, as the `scalar_offset` of an access pattern, and, as an `nl.int32` scalar, as a bound of [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop). An `nl.int64` scalar carries 64-bit range, supporting HBM offsets beyond 4 GB, but cannot bound a loop.

* **GpSimd Engine for activation and activate2**: [`activation()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation) and [`activate2()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activate2) accept an `engine` argument to run on the Scalar Engine, the default, or the GpSimd Engine on NeuronCore-v4+, where a limited set of activation functions is available. `engine.unknown` is not accepted. On the GpSimd Engine, `data` and `dst` must be in SBUF.

* **Dynamic indexing with runtime offsets**: `NkiTensor.__getitem__`, [`select()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.select), and [`ds()`](../programming/api/api-nki-language-dims.md#nki-language-ds) accept a runtime index: a `NkiTensor` 1-element tile, a `VirtualRegister`, or a [`NkiScalar`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.isa.NkiScalar.html#nki.isa.NkiScalar). A runtime index either selects a single element and removes the dimension (via `NkiTensor.__getitem__` or [`select()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.select)), or serves as the `start` of `nl.ds`, which keeps the dimension and slides a fixed-size window. The result is an indirect view (see [`is_indirect()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.is_indirect)). A runtime index cannot target the partition dimension for on-chip tensors, and a view accepts one runtime index.

* **New parameters for fused reductions**: [`tensor_scalar_reduce()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_reduce) adds `reduce_res_negate` to negate the reduction result before it is written to `reduce_res`. [`scalar_tensor_tensor()`](../programming/api/api-nki-isa-tensor.md#nki-isa-scalar_tensor_tensor) adds an optional Vector Engine reduction along the free dimension via `reduce_cmd`, `reduce_op` (`nl.add` only), and `reduce_res`.

* **empty_like transpose and placement**: [`empty_like()`](../programming/api/api-nki-language-layout.md#nki-language-empty_like) adds `transpose_axes` to prepare an SBUF destination for a later [`dma_transpose()`](../programming/api/api-nki-isa-memory.md#nki-isa-dma_transpose), and `address` for manual placement with the same contract as [`ndarray()`](../programming/api/api-nki-language-creation.md#nki-language-ndarray).

* **DMA batching**: [`dma_copy()`](../programming/api/api-nki-isa-memory.md#nki-isa-dma_copy) now supports a 2D `vector_offset` (shape `[P, M]`) on the source tensor, enabling the pattern where indices are read in a snake pattern and batched into the free dimension of the destination.

### Deprecated and Removed APIs

* **Custom Pythonic language**: Now that specialization by Python tracing is the default (see New Features), the custom Pythonic language NKI previously required is deprecated. Use `NKI_COMPILER_FRONTEND=custom` to fall back during the deprecation window.

* **Default NKI kernel LNC degree change (future notice)**: Currently, a NKI kernel’s LNC degree defaults to LNC=1 when it is not explicitly configured through `NEURON_LOGICAL_NC_CONFIG` or launch-grid bracket syntax. In a future release, the default will change to LNC=2 when targeting `trn2` or later, including in the CPU simulator. Set `NEURON_LOGICAL_NC_CONFIG=1` to preserve the existing behavior. NKI emits a warning if the kernel LNC degree is not explicitly specified.

* **rank_id return type (deprecation notice)**: [`rank_id()`](../programming/api/api-nki-collectives.md#nki-collectives-rank_id) and [`collective_permute_implicit_current_processing_rank_id()`](../programming/api/api-nki-collectives.md#nki-collectives-collective_permute_implicit_current_processing_rank_id) will return an [`NkiScalar`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.isa.NkiScalar.html#nki.isa.NkiScalar) instead of a `VirtualRegister` in a future release, so that offset arithmetic (e.g. `rank * chunk_size`) works. NKI emits a deprecation warning ahead of the change.

### Breaking Changes

* **Dynamic loop constructs**: On the default frontend, `nl.dynamic_range` for-loops and the `while reg:` form are no longer supported. Use [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop) and [`while_loop()`](../programming/api/api-nki-language-dims.md#nki-language-while_loop) instead, or set `NKI_COMPILER_FRONTEND=custom` to continue using kernels without updating during the deprecation window.

* **NKI kernel and compiler LNC degrees must match**: The LNC degree configured for a NKI kernel must now match the LNC degree used by `neuronx-cc` for the surrounding compute graph. A mismatch raises the compile-time error [NCC_EVRF066](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/compiler/error-codes/EVRF066.html). If encountered, set `NEURON_LOGICAL_NC_CONFIG` to the desired LNC degree and remove any conflicting launch-grid bracket syntax or compiler `--lnc` overrides.

* **Object equality on user classes**: `==` on instances of user-defined classes follows standard Python: two distinct instances are equal only if the class defines `__eq__`. To compare by value, define `__eq__` or declare the class as a `dataclasses.dataclass`.

### Bug Fixes

* [`simulate()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html#nki.simulate) corrects bitwise partition reductions in [`tensor_partition_reduce()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_partition_reduce): `bitwise_xor` was missing its simulator reducer, and `keepdims` handling for bitwise reductions is fixed, so `bitwise_or`, `bitwise_and`, and `bitwise_xor` match hardware.

### Known Issues

* Known issues carried forward from NKI 0.6.0 still apply unless noted above. See the NKI 0.6.0 release notes below.

## Neuron Kernel Interface (NKI) [0.6.0] (Neuron 2.32.0 Release)

Date of Release: 08/17/2026

AWS Neuron SDK 2.32.0 introduces NKI 0.6.0. This release adds the [`topk()`](../programming/api/api-nki-isa-tensor.md#nki-isa-topk) GpSimdE instruction and the [`all_gather_v()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather_v) variable-length collective, introduces replacement APIs for loops executed at runtime, [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop) and [`while_loop()`](../programming/api/api-nki-language-dims.md#nki-language-while_loop) loop constructs, and relaxes several DMA transpose constraints. It also includes several bug fixes.

### New Features

* **nl.fori_loop and nl.while_loop**: [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop) and [`while_loop()`](../programming/api/api-nki-language-dims.md#nki-language-while_loop) are now available for expressing runtime-bounded and condition-driven loops. These are replacing `for i in nl.dynamic_range(...)` or `while reg:` loops, which will be removed in a future release.

* **all_gather_v collective**: New [`all_gather_v()`](../programming/api/api-nki-collectives.md#nki-collectives-all_gather_v) is an all-gather collective where each rank contributes a variable-sized input tensor, determined at execution time.

* **nisa.topk**: New [`topk()`](../programming/api/api-nki-isa-tensor.md#nki-isa-topk) uses the GpSimd Engine to return the K largest values and their indices from a source tile.

* **DmaTranspose relaxations**: [`dma_transpose()`](../programming/api/api-nki-isa-memory.md#nki-isa-dma_transpose) now accepts `src.shape[0]` in {1,2,4,8,16} when using HWDGE (previously required ==16) and `src.shape[-1] <= 128` (previously required ==128).

* **tensor_scalar_reduce with optional reduce_res**: [`tensor_scalar_reduce()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_reduce) now accepts `reduce_res=None` on non-final iterations of a reduction chain, keeping the result in the VectorE accumulator and only spilling to SBUF on the final call.

* **nki.language.copy**: [`copy()`](../programming/api/api-nki-language-operators.md#nki-language-copy) is a stable API that creates a copy of an SBUF or PSUM tile on SBUF, with the same layout as the input. It uses the Vector engine (`tensor_copy`), making it bit-accurate when source and destination dtypes match.

### Improvements

* Compute instructions (Vector/Scalar/GpSimd/Tensor engines) now raise a clear verification error when an SBUF or PSUM operand exceeds the 128-partition hardware limit.

### Deprecated and Removed APIs

* `nl.dynamic_range` **for-loops and dynamic** `while` **loops**: NKI emits a deprecation warning when it encounters `for i in nl.dynamic_range(...)` or `while reg:` loops. These constructs are deprecated and will be removed in a future release; migrate to [`fori_loop()`](../programming/api/api-nki-language-dims.md#nki-language-fori_loop) and [`while_loop()`](../programming/api/api-nki-language-dims.md#nki-language-while_loop).

### Breaking Changes

None.

### Bug Fixes

* `nki.simulate` correctness fixes. See [`simulate()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html#nki.simulate).

fp32-to-integer casts match hardware (round to nearest, ties to even; saturate to the destination range) instead of numpy’s default behavior.

* [`nc_match_replace8()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_match_replace8) iterates values in reverse to match hardware tie-break ordering when the search values contain duplicates.

* [`nc_find_index8()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_find_index8) unmatched slots now return all-ones, matching hardware (previously returned 0, a valid index). Duplicate search values now advance through successive occurrences, instead of all mapping to the first match.

* [`activation()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation) with a `reduce_res` tile that aliases `dst` no longer clobbers the reduction result.

* [`quantize_mx()`](../programming/api/api-nki-isa-misc.md#nki-isa-quantize_mx) and [`nc_matmul_mx()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul_mx) handle sliced MX scale buffers correctly. Previously a sliced scale view raised a `ValueError` in [`quantize_mx()`](../programming/api/api-nki-isa-misc.md#nki-isa-quantize_mx) and could corrupt scales already written to the shared buffer.

* [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) with `matmul_perf_mode.double_row` is detected correctly in simulation; previously the perf mode could be missed, producing an oversized result.

* [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) accumulation into overlapping PSUM regions (the convolution pattern) accumulates correctly; previously each write was treated as a first write, so the overlap was not accumulated.

* [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) warns on PSUM accumulation patterns that produce incorrect results on hardware.

* [`tensor_copy_predicated()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_copy_predicated) and [`select_reduce()`](../programming/api/api-nki-isa-misc.md#nki-isa-select_reduce) now handle source, destination, and predicate operands with mismatched ranks but matching free-element counts (e.g. a 2D source with a 4D destination/predicate).

* [`range_select()`](../programming/api/api-nki-isa-misc.md#nki-isa-range_select) and [`affine_select()`](../programming/api/api-nki-isa-utility.md#nki-isa-affine_select) now accept a Python `float` `on_false_value` (e.g. `float(np.finfo(np.float32).min)` used as an fp32 sentinel) without raising a dtype error.

### Known Issues

* [`local_gather()`](../programming/api/api-nki-isa-memory.md#nki-isa-local_gather): the value of `num_elem_per_idx` (default or explicit) is currently ignored unless `num_valid_indices * num_elem_per_idx == dst.size / dst.shape[0]`. Pass both `num_valid_indices` and `num_elem_per_idx` explicitly, and size `dst` such that `num_valid_indices * num_elem_per_idx == dst.size / dst.shape[0]`.

* Known issues carried forward from NKI 0.5.0 still apply unless noted above. See the NKI 0.5.0 release notes below.

## Neuron Kernel Interface (NKI) [0.5.0] (Neuron 2.31.0 Release)

Date of Release: 07/07/2026

AWS Neuron SDK 2.31.0 introduces NKI 0.5.0. This release adds the `float8_e8m0fnu` MX scale dtype, introduces tensor indirection (gather/scatter) support for on-chip compute operations, and expands [`NkiTensor`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor) view and query methods. It also tightens several APIs for correctness and includes simulator and compiler bug fixes.

### New Features

* **float8_e8m0fnu dtype**: Added OCP MX scale format support for [`nc_matmul_mx()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul_mx) / [`quantize_mx()`](../programming/api/api-nki-isa-misc.md#nki-isa-quantize_mx) scale operands, where previously scale was represented as `uint8`. See [nki.language.float8_e8m0fnu](../programming/api/api-nki-language-types.md#nki-language-float8_e8m0fnu).

* **New NkiTensor view primitives**: Added composable view methods to [`NkiTensor`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor): [`slice()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.slice), [`select()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.select), [`permute()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.permute), [`broadcast()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.broadcast), [`expand_dim()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.expand_dim), [`squeeze_dim()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.squeeze_dim), [`reshape_dim()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.reshape_dim), [`flatten_dims()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.flatten_dims), [`rearrange()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.rearrange), and [`vector_select()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.vector_select). Also adds query methods [`is_contiguous()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.is_contiguous) and [`is_indirect()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.is_indirect). Added documentation and examples for pre-existing methods [`reshape()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.reshape), [`view()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.view), [`ap()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.ap), and [`get_pattern()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.get_pattern).

* **Tensor indirection on compute APIs**: Tensor indirection (gather/scatter) via [`indirect()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.indirect) — a new addressing mode that uses an index tensor to indirectly select SBUF/PSUM partitions for on-chip compute operations on NeuronCore-v4+. The `dst` and `data` tensors accept `.indirect()` views, subject to quadrant/partition alignment rules. This extends indirect addressing (previously only available on DMA ops via `vector_offset`) to non-DMA ISA operations: [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul), [`nc_matmul_mx()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul_mx), [`tensor_tensor()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_tensor), [`tensor_scalar()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar), [`tensor_reduce()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_reduce), [`tensor_copy()`](../programming/api/api-nki-isa-memory.md#nki-isa-tensor_copy), [`tensor_copy_predicated()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_copy_predicated), [`tensor_scalar_reduce()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_reduce), [`tensor_scalar_cumulative()`](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_cumulative), [`activation()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation), [`activation_reduce()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation_reduce), [`activate2()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activate2), and [`exponential()`](../programming/api/api-nki-isa-misc.md#nki-isa-exponential).

* [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) now supports **8192 elements for bfloat16** dst output (previously only 4096 fp32 elements were supported). Also updates [`nc_matmul_mx()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul_mx) documentation to clarify the correct PSUM limits (full 16384-byte PSUM bank).

* [`dma_transpose()`](../programming/api/api-nki-isa-memory.md#nki-isa-dma_transpose) with `scalar_offset` now supports `oob_mode.skip` for runtime indirect indexing.

* Added [`psum_num_banks`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/nki.language.tile_size.html#nki.language.tile_size.psum_num_banks) to get the number of usable PSUM banks per partition.

* NKI wheel now ships PEP 561 `.pyi` type stubs for IDE autocomplete, hover docs, and type checking. Non-public modules are hidden from IDE autocomplete. Internal modules renamed with `_` prefix per PEP 8 conventions (backward-compatibility shims are in place).

### Improvements

* [`quantize_mx()`](../programming/api/api-nki-isa-misc.md#nki-isa-quantize_mx) now uses a block scale two times larger than the sample conversion algorithm described in the OCP Microscaling specification, mitigating a rounding bias where the largest values in a group are clamped down rather than rounded up. See [nki.isa.quantize_mx](../programming/api/api-nki-isa-misc.md#nki-isa-quantize_mx).

* The CPU simulator now emits a warning when [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) with `accumulate=True` is used on uninitialized PSUM (undefined behavior on hardware).

* All `nki.collectives` APIs now accept an optional `name` parameter, like `nki.isa` instructions, so that `kernel.with_schedule()` edges can target collective instructions with the [NKI Scheduling APIs](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/guides/how-to-scheduling-apis.html#how-to-scheduling-apis).

* [`reshape()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.reshape) now asserts that the total element count is preserved. Invalid reshapes raise a clear error at trace time.

* Neuronx-cc allocator errors are now translated into actionable user-facing messages.

* The experimental [`rsqrt()`](../programming/api/api-nki-language-operators.md#nki-language-rsqrt) API now provides higher numerical precision using the GpSimd Engine. PSUM inputs are no longer supported; use [`activation()`](../programming/api/api-nki-isa-scalar.md#nki-isa-activation) directly if you need PSUM input or Scalar Engine throughput.

### Deprecated and Removed APIs

* **SPMD launch grid requirement for LNC2 kernels (deprecation notice)**: Launching an LNC2 kernel currently relies on an SPMD launch grid whose dimension matches the LNC degree. This requirement will be deprecated in a future release; the behavior does not change in NKI 0.5.0. When this change takes effect:

the SPMD launch grid will no longer be required to launch an LNC2 kernel;

* passing an SPMD dimension that differs from the LNC degree in use will raise a compile-time error; and

* regardless of whether an SPMD dimension is specified, the kernel will always be specialized across all physical NeuronCores (PNCs) in the LNC.

Kernels that continue to pass a matching SPMD grid will keep working for backwards compatibility. See [LNC Overview](../programming/lnc.md).

### Bug Fixes

* Simulator fix for [`dma_copy()`](../programming/api/api-nki-isa-memory.md#nki-isa-dma_copy) out-of-bounds error with x4 dtypes and `oob_mode.skip`.

* Simulator fix for [`nc_matmul()`](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul) with >2D operands in conv-style kernels.

* Simulator fix for while-loop failing to terminate correctly.

* Simulator fix for gather transpose overwriting valid dst with OOB zeros.

### Known Issues

* [`reinterpret_cast()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.reinterpret_cast) + [`vector_select()`](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.language.NkiTensor.html#nki.language.NkiTensor.vector_select): Combined usage is not yet supported.

* Known issues carried forward from NKI 0.4.0 still apply unless noted above. See the NKI 0.4.0 release notes below.

## Neuron Kernel Interface (NKI) [0.4.0] (Neuron 2.30.0 Release)

Date of Release: 05/21/2026

### New Features

* **nki.language.abs_max and nki.language.abs_min**: New callable APIs for element-wise absolute maximum and absolute minimum. These are `trn3` only and run on the Vector Engine. Also usable as `op0` with `nki.isa.tensor_scalar` and `nki.isa.tensor_scalar_reduce`. See [nki.isa.tensor_scalar](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar) and [nki.isa.tensor_scalar_reduce](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_reduce).

* **nki.isa.activate2**: New `trn3` Scalar Engine API that applies an activation function to the result of a two-stage tensor-scalar preprocessing pipeline `(data op0 imm0) op1 imm1`, with an optional reduction, all in a single instruction. Supports six `(op0, op1)` combinations (scale+bias, scale-only, bias-only, etc.) and optional operand reversal for non-commutative operations. Reduces instruction count compared to chaining `nisa.tensor_scalar` with `nisa.activation`. See [nki.isa.activate2](../programming/api/api-nki-isa-scalar.md#nki-isa-activate2).

* **New opcodes for nki.isa.tensor_scalar and tensor_scalar_reduce**: `square` and `relu` are now accepted as `op0` on `trn3`. See [nki.isa.tensor_scalar](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar) and [nki.isa.tensor_scalar_reduce](../programming/api/api-nki-isa-tensor.md#nki-isa-tensor_scalar_reduce).

* **New activation and arithmetic opcodes in nki.language**: `nl.prelu` (parametric ReLU, used as `op=nl.prelu` with `nki.isa.activate2`) and `nl.bypass` (pass-through op for `nki.isa.activate2`). See the supported activation functions and arithmetic operator tables in [nki.api.shared](../programming/api/nki.api.shared.md).

* **tile_size bytes-aware constants**: New properties on `nki.language.tile_size` expose SBUF and PSUM capacity in both elements and bytes:

`tile_size.sbuf_size_bytes` — total SBUF capacity across all 128 partitions, in bytes

* `tile_size.sbuf_fmax` — per-partition usable SBUF free dimension in FP32 elements

* `tile_size.sbuf_fmax_bytes` — per-partition usable SBUF free dimension in bytes

* `tile_size.psum_bank_fmax` — PSUM bank capacity in FP32 elements

* `tile_size.psum_bank_fmax_bytes` — PSUM bank capacity in bytes

See [nki.language.tile_size](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/nki.language.tile_size.html).

* **nki.isa.dma_compute oob_mode parameter**: `dma_compute` now accepts an `oob_mode` parameter (`oob_mode.error` or `oob_mode.skip`) to control handling of out-of-bounds indices in indirect gather/scatter operations with `vector_offset`, mirroring existing `dma_copy` behavior. Validation ensures `oob_mode.skip` is used only with indirect indexing. See [nki.isa.dma_compute](../programming/api/api-nki-isa-misc.md#nki-isa-dma_compute).

* **nc_matmul float8_e4m3fn input dtype**: On `trn3`, `nc_matmul` now accepts `float8_e4m3fn` (OCP FP8) as an input dtype, distinct from the legacy `float8_e4m3`. A new validation prevents mixing legacy `float8_e4m3` with OCP `float8_e4m3fn` operands in the same matmul. See [nki.isa.nc_matmul](../programming/api/api-nki-isa-tensor.md#nki-isa-nc_matmul).

* **JAX dtype support**: JAX scalar dtype types (`jnp.bfloat16`, `jnp.float16`, `jnp.float32`, etc.) are now accepted as kernel arguments and keyword arguments and automatically converted to the equivalent NKI dtype. Unsupported JAX dtypes raise `TypeError`. See [NKI data types](../programming/api/nki.language.md).

### Improvements

* `nki.isa.sendrecv` — Removed the restriction that the `src` and `dst` partition dimension must be a multiple of 16. Note: `sendrecv` is an intra-LNC communication API and is only supported when running on LNC2 (`trn2` or later). See [nki.isa.sendrecv](../programming/api/api-nki-isa-local-collective.md#nki-isa-sendrecv).

* `nki.isa.dma_transpose` with indirect indexing — Relaxed the `src` innermost dimension constraint from exactly `128` to `<= 128` when `src` uses an indirect access pattern (`vector_offset`). See [nki.isa.dma_transpose](../programming/api/api-nki-isa-memory.md#nki-isa-dma_transpose).

* **``nki.simulate`` default accuracy improved**: `NKI_PRECISE_FP=1` is now the default for CPU simulation. Low-precision dtypes (`bfloat16`, `float8`) are now modeled accurately instead of being approximated with `float32`, producing simulator results closer to hardware. Set `NKI_PRECISE_FP=0` to restore the previous behavior. See [nki.simulate](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html).

* **``NKI_SIMULATOR=1`` environment variable**: Setting `NKI_SIMULATOR=1` now works with `torch.Tensor` inputs directly — no manual conversion to NumPy arrays required. See [nki.simulate](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html).

* **Improved error messages for nested NKI calls**: Kernel compilation errors now show the full Python call stack instead of only the innermost frame, making it easier to locate the call site that triggered the error.

### Deprecated and Removed APIs

* `nki.isa.tensor_copy_dynamic_src` / `nki.isa.tensor_copy_dynamic_dst` — Removed. Use `nisa.tensor_copy()` with `.ap()` and `scalar_offset` instead. See [nki.isa.tensor_copy](../programming/api/api-nki-isa-memory.md#nki-isa-tensor_copy).

* `nki.language.tile_size.total_available_sbuf_size` — Deprecated. Despite the name, this attribute returns the usable SBUF free dimension *per partition*, not total SBUF capacity. Use `tile_size.sbuf_size_bytes` for total SBUF capacity across all partitions, or `tile_size.sbuf_fmax_bytes` for the per-partition size. The deprecated attribute continues to work and returns the same value as before. See [nki.language.tile_size](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/nki.language.tile_size.html).

### Breaking Changes

* `nisa.dma_transpose` — Now enforces that `dst.shape` matches the transposed `src.shape` exactly, including rank. Previously, a lower-rank `dst.shape` was silently padded to match a higher-rank `src.shape` (e.g., a 3D `dst` against a 4D `src`). The compiler now raises an assertion error if the ranks differ. To migrate: either match the `dst` rank to the `src` rank (e.g., use `dst.shape=(128, 1, 1, 4096)` for a 4D `src`), or use a `src` and `axes` of the same rank as the intended `dst` (e.g., a 3D `src` with `axes=(2, 1, 0)` instead of a 4D `src` with `axes=(3, 1, 2, 0)`). See [nki.isa.dma_transpose](../programming/api/api-nki-isa-memory.md#nki-isa-dma_transpose).

* `neuronxcc.nki.*` namespace — Usage of the deprecated `neuronxcc.nki.*` namespace inside NKI kernels now raises a compilation error instead of a warning. To migrate, follow the [NKI Beta 2 Migration Guide](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/migration/index.html#nki-migration-guide).

### Bug Fixes

* **``nki.simulate`` correctness fixes**: The CPU simulator was corrected in several areas for closer matching to hardware behavior:

`nki.isa.dma_copy` with `oob_mode=oob_mode.skip` no longer casts integer tiles to `float32` in the OOB skip path — integer bit patterns are now preserved.

* `nki.isa.nc_stream_shuffle` with `mask=255` now preserves existing destination data instead of zeroing it.

* `nki.isa.local_gather` now produces correct results, including when the destination uses a sub-view or `.ap()` access pattern.

* `nki.isa.nc_matmul` with 3D+ operand shapes now copies all elements (previously higher dimensions were silently dropped, producing zero-filled results).

* `nki.isa.quantize_mx` with `float8_e4m3fn_x4` output now simulates correctly.

* `nki.isa.iota` and `nki.isa.affine_select` now handle dynamic register offsets correctly.

* MX x4 packed dtypes (`float8_e4m3fn_x4`, `float8_e5m2_x4`) now simulate correctly when targeting `trn3`.

* `nki.isa.dma_compute` no longer applies a fictional additive scale mode that the hardware does not support.

* `nl.logical_and` in CPU simulation now produces correct results.

See [nki.simulate](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/api/generated/nki.simulate.html).

* Fixed `NKIObject` subclasses decorated with `@dataclass(frozen=True)` failing to instantiate with `FrozenInstanceError`. Frozen NKI object subclasses can now be constructed normally.

* Fixed `nki.jit` cache misses when `NKIObject` subclasses are decorated with `@dataclass`. `@dataclass` removes `__hash__` by default, which prevented the cache key from being computed. `NKIObject` dataclass subclasses are now handled with a consistent cache key regardless of hashability. See [nki.jit](../programming/api/api-nki-tools.md#nki-jit).

* Fixed bool output tensors being returned as `uint8` from PyTorch Native kernels. Bool dtype is now preserved end-to-end so output tensors are returned as `torch.bool` instead of `torch.uint8`.

* Fixed hardware race conditions in dynamic loop kernels when loop-body memory accesses overlapped with pre-loop accesses. Cross-scope memory dependencies are now tracked correctly across loop boundaries.

* Fixed silent, undefined behavior when writing to the induction variable of an `nl.dynamic_range` loop. Writing to the induction variable (e.g., to try to break out of the loop early) had no effect but did not surface any error. Such writes now raise `AssertionError` at trace time. Additionally, the induction variable is now a `VirtualRegister` (previously a bare scalar), so it can be used as a `scalar_offset` in access patterns (e.g., `nisa.dma_copy` with a per-iteration dynamic offset) — resolving a 0.3.0 known issue. See [nki.language.dynamic_range](../programming/api/api-nki-language-dims.md#nki-language-dynamic_range).

* Fixed `nki.isa.nc_transpose` silently ignoring the `engine` argument. An explicit `engine=engine.vector` or `engine=engine.tensor` is now honored; `engine=engine.unknown` (the default) continues to auto-select based on the destination buffer. See [nki.isa.nc_transpose](../programming/api/api-nki-isa-memory.md#nki-isa-nc_transpose).

* Fixed `nki.jit` kernel cache missing when a kernel is invoked with `None` arguments. Previously this triggered unnecessary recompilation on every such call. See [nki.jit](../programming/api/api-nki-tools.md#nki-jit).

* Fixed `nl.device_print` failing verification on 1-D HBM tensors (e.g., after linearization). `device_print` now works with any tensor rank. See [nki.language.device_print](../programming/api/api-nki-language-creation.md#nki-language-device_print).

### Known Issues

**Control Flow**

* Nested `nl.dynamic_range` loops with loop-carried values fail to compile with a “Could not find register” error. Workaround: restructure to avoid nested dynamic loops, or use `nl.static_range` / `nl.affine_range` for the outer loop when the trip count is known at compile time.

**CPU Simulator**

* The CPU simulator has additional known limitations beyond those listed here. See the [Simulation Limitations](https://awsdocs-neuron.readthedocs-hosted.com/en/latest/nki/guides/nki_simulator.html#simulation-limitations) section of the simulator guide for the full list.

**NKI Language (experimental)**

The `nki.language` APIs are convenience wrappers around `nki.isa` instructions. They are experimental and have the following known limitations:

* `nki.language.divide` is not supported — Division is not available as a hardware instruction. As a workaround, multiply by the reciprocal: `nl.multiply(x, nl.reciprocal(y))`.

* `nki.language.fmod` and `nki.language.mod` are not supported — Modulo operations are not available as hardware instructions. These APIs work in simulation but fail when compiled for Trainium hardware.

* `nki.language.power` does not support scalar exponents — `nl.power(tile, scalar)` is not supported. Use `nl.power(tile, tile)` instead, where both operands are tiles.

* Binary operations do not support broadcasting — Operations like `nl.add(a, b)` require both operands to have the same shape. Broadcasting (e.g., adding a `(128, 1)` tile to a `(128, 512)` tile) is not yet supported.

* `nki.language.random_seed` requires a tensor, not a scalar — Pass a `[1, 1]` tensor on SBUF instead of a Python integer. For example: `nl.random_seed(nl.full((1, 1), 42, dtype=nl.int32, buffer=nl.sbuf))`.

* `nki.language.rand` and `nki.language.random_seed` engine behavior — On `trn3`, `rand` uses `nisa.rand2` on the Vector Engine. On earlier targets, `rand` uses `nisa.rng` which may run on a different engine than `random_seed`, potentially causing `random_seed` to have no effect on `rand` output.

* `nki.language.matmul` without `transpose_x=True` is not supported — Calling `nl.matmul(x, y)` without setting `transpose_x=True` will fail. As a workaround, always use `nl.matmul(x, y, transpose_x=True)` and pre-arrange data accordingly.

* `nki.language.copy` uses lossy FP32 casting — `nl.copy` uses the Scalar Engine which internally casts through `float32`, which is lossy for integer types with values exceeding `float32` precision (e.g., `int32` values greater than 2^23). Additionally, cross-buffer copies (e.g., PSUM to SBUF) are not supported.

## Neuron Kernel Interface (NKI) (GA) [2.29]

Date: 2026

NKI 0.3.0 moves NKI to General Availability with a new open-source NKI Standard Library (nki-stdlib), a built-in CPU Simulator, `nki.language` APIs, and several API improvements for correctness and consistency.

* new features:

NKI Standard Library (nki-stdlib) — open-source, developer-visible code for all NKI APIs and native language objects

* NKI CPU Simulator — `nki.simulate(kernel)` executes NKI kernels on CPU without NeuronDevice hardware (experimental)

* `nki.typing` module — type-annotate kernel tensor parameters with `nt.tensor[shape]`

* `nki.language` convenience APIs (experimental) — `nl.load`, `nl.store`, `nl.copy`, `nl.matmul`, `nl.transpose`, `nl.softmax`

* new `nki.isa` APIs:

`nki.isa.exponential` — dedicated exponential instruction (Trn3/NeuronCore-v4 only)

* new `nki.collectives` APIs:

`nki.collectives.all_to_all_v` — variable-length all-to-all collective

* changes to existing APIs:

`nki.isa.nc_matmul` and `nki.isa.nc_matmul_mx` — new `accumulate` parameter for controlling overwrite vs accumulation on PSUM

* `nki.language.ndarray` — new `address` parameter for explicit memory placement

* `nki.isa.dma_copy` — no longer supports reading directly from PSUM; `dst_rmw_op` and `unique_indices` parameters removed (use `nisa.dma_compute` instead); enforces type matching with `dge_mode=hwdge`

* `nki.isa.dma_compute` — `scales` and `reduce_op` parameter positions swapped; `unique_indices` parameter added

* `nki.isa.memset` — `value` must match destination dtype; x4 packed types enforce `value=0`

* `nki.isa.tensor_reduce` — fixed incorrect axis handling for 3D/4D tensors

* `nki.isa.sendrecv` — `use_gpsimd_dma` replaced by `dma_engine` enum

* `nki.isa.affine_select` — `offset` parameter moved to keyword argument

* `nki.isa.register_move` — `imm` parameter renamed to `src`, now accepts `VirtualRegister`

* `nki.jit` — `platform_target` parameter removed (use `NEURON_PLATFORM_TARGET_OVERRIDE` env var); `mode` parameter deprecated and ignored

* Output tensors must use `buffer=nl.shared_hbm`

* Integer enum constants no longer supported (use named enum members)

* String buffer names no longer supported (use buffer objects like `nl.sbuf`, `nl.psum`)

* `nki.isa.tensor_copy_dynamic_src` / `nki.isa.tensor_copy_dynamic_dst` deprecated (use `nisa.tensor_copy()` with `.ap()` and `scalar_offset`)

* default value changes:

`nki.isa.iota` — `offset` now optional with default `0`

* `nki.isa.core_barrier` — `engine` default changed from `unknown` to `gpsimd`

* `nki.language.num_programs` — `axes` default changed from `None` to `0`

* `nki.language.program_id` — `axis` now has default value of `0`

* `nki.language.ndarray` — `buffer` default changed from `None` to `nl.sbuf`

* `nki.language.zeros` — `buffer` default changed from `None` to `nl.sbuf`

* `nki.language.sequential_range` — `stop` and `step` now have default values (`None` and `1`)

* language restrictions:

Keyword-only argument separator (`*`) not supported in kernel function signatures

* `is` / `is not` operators not supported; use `==` / `!=` instead

* `list` not supported as kernel argument type; use tuples instead

* Collectives — `num_channels` removed from `collective_permute_implicit_current_processing_rank_id`

## Neuron Kernel Interface (NKI) (Beta) [2.27]

Date: 12/25/2025

* new `nki.language` APIs:

`nki.language.device_print`

* new `nki.isa` APIs:

`nki.isa.dma_compute`

* `nki.isa.nki.isa.quantize_mx`

* `nki.isa.nki.isa.nc_matmul`

* `nki.isa.nki.isa.nc_n_gather` [used to be `nl.gather_flattened` with free partition limited to 512]

* `nki.isa.rand2`

* `nki.isa.rand_set_state`

* `nki.isa.rand_get_state`

* `nki.isa.set_rng_seed`

* `nki.isa.rng`

* new `dtypes`:

`nki.language.float8_e5m2_x4`

* `nki.language.float4_e2m1fn_x4`

* `nki.language.float8_e4m3fn_x4`

* changes to existing APIs:

several `nki.language` APIs have been removed in NKI Beta 2

* all nki.isa APIs have `dst` as an input param

* all nki.isa APIs removed `dtype` and `mask` support

* `nki.isa.memset` — removed `shape` positional arg , since we have `dst`

* `nki.isa.affine_select` — instead of `pred`, we now take `pattern` and `cmp_op` params

* `nki.isa.iota` — `expr` replaced with `pattern` and `offset`

* `nki.isa.nc_stream_shuffle` - `src` and `dst` order changed

* docs improvements:

restructured NKI Documentation to align with workflows

* added [Trainium3 Architecture Guide for NKI](../architecture/trainium3_arch.md)

* added [About Neuron Kernel Interface (NKI)](../programming/api/index.md)

* added [NKI Environment Setup Guide](../programming/setup-env.md)

* added [Get Started with NKI](../programming/quickstart-implement-run-kernel.md)

* added [NKI Language Guide](../programming/nki-language-guide.md)

* added [About the NKI Compiler](../programming/nki-compiler.md)

* added [About NKI Beta Versions](../optimization/nki-beta-versions.md)

* added [MXFP Matrix Multiplication with NKI](../optimization/mxfp-matmul.md)

* updated [Matrix Multiplication Tutorial](../programming/tutorials/matrix_multiplication.md)

* updated [Profile a NKI Kernel](../optimization/use-neuron-profile.md)

* updated [NKI APIs](../programming/api/index.md)

* updated [NKI Library docs](../programming/api/index.md)

* removed NKI Error Guide

* known issues:

`nki.isa.nki.isa.nc_matmul` - `is_moving_onezero` was incorrectly named `is_moving_zero` in this release

* NKI ISA semantic checks are not available with Beta 2, workaround is to reference the API docs

* NKI Collectives are not available with Beta 2

* `nki.benchmark` and `nki.profile` are not available with Beta 2

## Neuron Kernel Interface (NKI) (Beta) [2.26]

Date: 09/18/2025

* new `nki.language` APIs:

`nki.language.gelu_apprx_sigmoid` - Gaussian Error Linear Unit activation function with sigmoid approximation.

* `nki.language.tile_size.total_available_sbuf_size` to get total available SBUF size

* new `nki.isa` APIs:

`nki.isa.select_reduce` - selectively copy elements with max reduction

* `nki.isa.sequence_bounds` - compute sequence bounds of segment IDs

* `nki.isa.dma_transpose`

`axes` param to define 4D transpose for some supported cases

* `dge_mode` to specify Descriptor Generation Engine (DGE).

* `nl.gelu_apprx_sigmoid` op support on `nki.isa.activation`

* fixes / improvements:

`nki.language.store` supports PSUM buffer with extra additional copy inserted.

* docs/tutorial improvements:

`nki.isa.dma_transpose` API doc and example

* `nki.simulate_kernel` example improvement

* use `nl.fp32.min` in tutorial code instead of a magic number

* better error reporting:

indirect indexing on transpose

* mask expressions

## Neuron Kernel Interface (NKI) (Beta) [2.24]

Date: 06/24/2025

* `sqrt` valid data range extended for accuracy improvement with wider numerical values support.

* `nki.language.gather_flattened` new API

* `nki.isa.nc_match_replace8` additional param `dst_idx`

* improved docs/examples on `nki.isa.nc_match_replace8`, `nki.isa.nc_stream_shuffle`

* improved error messages

## Neuron Kernel Interface (NKI) (Beta) [2.23]

Date: 05/20/2025

* `nki.isa.range_select` (for trn2) new instruction

* `abs`, `power` ops supported on to nki.isa tensor instruction

* `abs` op supported on `nki.isa.activation` instruction

* GpSIMD engine support added to `add`, `multiply` in 32bit integer to nki.isa tensor operations

* `nki.isa.tensor_copy_predicated` support for reversing predicate.

* `nki.isa.tensor_copy_dynamic_src`, `tensor_copy_dynamic_dst` engine selection.

* `nki.isa.dma_copy` additional support with `dge_mode`, `oob_mode`, and in-place add `rmw_op`.

* `+=, -=, /=, *=` operators now work consistently across loop types, PSUM, and SBUF,

* fixed simulation for instructions: `nki.language.rand`, `random_seed`, `nki.isa.dropout`

* fixed simulation masking behavior

* Added warning when the block dimension is used for SBUF and PSUM tensors, see: [NKI Block Dimension Migration Guide](migration/nki_block_dimension_migration_guide.md#nki-block-dimension-migration-guide)

## Neuron Kernel Interface (NKI) (Beta) [2.22]

Date: 04/03/2025

* New modules and APIs:

`nki.profile`

* `nki.isa` new APIs:

`tensor_copy_dynamic_dst`

* `tensor_copy_predicated`

* `max8`, `nc_find_index8`, `nc_match_replace8`

* `nc_stream_shuffle`

* `nki.language` new APIs: `mod`, `fmod`, `reciprocal`, `broadcast_to`, `empty_like`

* Improvements:

`nki.isa.nc_matmul` now supports PE tiling feature

* `nki.isa.activation` updated to support reduce operation and `reduce` commands

* `nki.isa.engine` enum

* `engine` parameter added to more `nki.isa` APIs that support engine selection (ie, `tensor_scalar`, `tensor_tensor`, `memset`)

* Documentation for `nki.kernels` have been moved to the GitHub: [https://aws-neuron.github.io/nki-samples](https://aws-neuron.github.io/nki-samples).
The source code can be viewed at [aws-neuron/nki-samples](https://github.com/aws-neuron/nki-samples).

These kernels are still shipped as part of Neuron package in `neuronxcc.nki.kernels` module

* Documentation updates:

Kernels public repository [https://aws-neuron.github.io/nki-samples](https://aws-neuron.github.io/nki-samples)

* Updated [profiling guide](../optimization/use-neuron-profile.md) to use `nki.profile` instead of `nki.benchmark`

* NKI ISA Activation functions table now have [valid input data ranges](../programming/api/nki.api.shared.md#tbl-act-func) listed

* NKI ISA Supported Math operators now have [supported engine](../programming/api/nki.api.shared.md#tbl-aluop) listed

* Clarify `+=` syntax support/limitation

## Neuron Kernel Interface (NKI) (Beta) [2.21]

Date: 12/16/2024

* New modules and APIs:

`nki.compiler` module with Allocation Control and Kernel decorators,
see guide for more info.

* `nki.isa`: new APIs (`activation_reduce`, `tensor_partition_reduce`,
`scalar_tensor_tensor`, `tensor_scalar_reduce`, `tensor_copy`,
`tensor_copy_dynamic_src`, `dma_copy`), new activation functions(`identity`,
`silu`, `silu_dx`), and target query APIs (`nc_version`, `get_nc_version`).

* `nki.language`: new APIs (`shared_identity_matrix`, `tan`,
`silu`, `silu_dx`, `left_shift`, `right_shift`, `ds`, `spmd_dim`, `nc`).

* New `datatype <nl_datatypes>`: `float8_e5m2`

* New `kernels` (`allocated_fused_self_attn_for_SD_small_head_size`,
`allocated_fused_rms_norm_qkv`) added, kernels moved to public repository.

* Improvements:

Semantic analysis checks for nki.isa APIs to validate supported ops, dtypes, and tile shapes.

* Standardized naming conventions with keyword arguments for common optional parameters.

* Transition from function calls to kernel decorators (`jit`,
`benchmark`, `baremetal`, `simulate_kernel`).

* Documentation updates:

Tutorial for [SPMD usage with multiple Neuron Cores on Trn2](../programming/tutorials/spmd_multiple_nc_tensor_addition.md)

## Neuron Kernel Interface (NKI) (Beta)

Date: 12/03/2024

* NKI support for Trainium2, including full integration with Neuron Compiler.
Users can directly shard NKI kernels across multiple Neuron Cores from an SPMD launch grid.
See [tutorial](../programming/tutorials/spmd_multiple_nc_tensor_addition.md) for more info.
See [Trainium2 Architecture Guide](../architecture/trainium2_arch.md) for an initial version of the architecture specification
(more details to come in future releases).

* New calling convention in NKI kernels, where kernel output tensors are explicitly returned from the kernel instead
of pass-by-reference. See any [NKI tutorial](../programming/api/index.md) for code examples.

## Neuron Kernel Interface (NKI) (Beta) [2.20]

Date: 09/16/2024

* This release includes the beta launch of the Neuron Kernel Interface (NKI) (Beta).
NKI is a programming interface enabling developers to build optimized compute kernels
on top of Trainium and Inferentia. NKI empowers developers to enhance deep learning models
with new capabilities, performance optimizations, and scientific innovation.
It natively integrates with PyTorch and JAX, providing a Python-based programming environment
with Triton-like syntax and tile-level semantics offering a familiar programming experience
for developers. Additionally, to enable bare-metal access precisely programming the instructions
used by the chip, this release includes a set of NKI APIs (`nki.isa`) that directly emit
Neuron Instruction Set Architecture (ISA) instructions in NKI kernels.