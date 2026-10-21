# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Stage 0: Structural Scaffolding.

Builds the foundation that all later stages rely on:
  Step 0.1 — Build model trees for both reference and ported implementations
  Step 0.2 — Construct component mapping between the two trees
  Step 0.3 — Define alignment functions for tensor comparison

Output: component_mapping dict and alignment function registry.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import product
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    import torch
    import torch.nn as nn


# ---------------------------------------------------------------------------
# Step 0.1 — Model Tree Generation
# ---------------------------------------------------------------------------

def module_to_dict(module, name: str = "") -> dict:
    """Convert an nn.Module hierarchy into an uncompressed tree dict."""
    node = {
        "name": name,
        "type": module.__class__.__name__,
        "children": [],
    }
    for child_name, child_module in module.named_children():
        node["children"].append(module_to_dict(child_module, name=child_name))
    return node


def _structure_hash(subtree: dict) -> str:
    """Hash a subtree for deduplication based on type and child structure."""
    m = hashlib.md5()
    m.update(subtree.get("type", "").encode())
    for child in subtree.get("children", []):
        m.update(child.get("type", "").encode())
        m.update(child.get("name", "").encode())
        m.update(_structure_hash(child).encode())
    return m.hexdigest()


def compress_module_tree(tree: dict) -> dict:
    """Compress a module tree by deduplicating structurally identical subtrees.

    Repeated layers (e.g., 48 identical decoder layers) are collapsed into
    a single node with a names list like ["0", "1", ..., "47"].
    """
    def compress_node(node: dict) -> dict:
        local_seen = {}
        compressed_children = []
        for child in node.get("children", []):
            compressed_child = compress_node(child)
            h = _structure_hash(compressed_child)
            if h in local_seen:
                local_seen[h]["names"].append(compressed_child["names"][0])
            else:
                local_seen[h] = compressed_child
                compressed_children.append(compressed_child)
        name = node.get("name", "")
        return {
            "type": node.get("type", ""),
            "names": [name if name is not None else ""],
            "children": compressed_children,
        }

    return compress_node(tree)


def _summarize_names(names: List[str]) -> Tuple[str, int]:
    """Summarize a list of names for pretty-printing."""
    if not names:
        return "", 0
    n = len(names)
    try:
        ints = sorted(set(int(x) for x in names))
        if len(ints) > 1 and ints == list(range(ints[0], ints[-1] + 1)):
            return f"[{ints[0]}..{ints[-1]}]", n
        return f"[{', '.join(str(i) for i in ints)}]", n
    except ValueError:
        if n <= 6:
            return f"[{', '.join(names)}]", n
        return f"[{', '.join(names[:3])}, ..., {names[-1]}]", n


def compressed_tree_to_pretty_string(root: dict) -> str:
    """Render a compressed tree as an ASCII string."""
    lines = []
    root_names = root.get("names", [""])
    root_type = root.get("type", "")
    lines.append(f"{root_names[0]}: {root_type}" if root_names[0] else root_type)

    def _build(node, prefix="", is_last=True):
        children = node.get("children", [])
        # Handle ModuleList with single repeated child
        if node["type"] == "ModuleList" and len(children) == 1:
            child = children[0]
            child_names = child.get("names", [])
            if len(child_names) > 1:
                mod_name = node.get("names", [""])[0]
                idx_summary, count = _summarize_names(child_names)
                label = f"{mod_name}{idx_summary} (N={count}): {child['type']}"
                lines.append(prefix + ("└── " if is_last else "├── ") + label)
                new_prefix = prefix + ("    " if is_last else "│   ")
                for i, gc in enumerate(child.get("children", [])):
                    _build(gc, new_prefix, i == len(child["children"]) - 1)
                return

        names = node.get("names", [])
        node_type = node["type"]
        if len(names) == 1 and names[0]:
            label = f"{names[0]}: {node_type}"
        elif len(names) > 1:
            summary, count = _summarize_names(names)
            label = f"{summary} (N={count}): {node_type}"
        else:
            label = node_type

        lines.append(prefix + ("└── " if is_last else "├── ") + label)
        new_prefix = prefix + ("    " if is_last else "│   ")
        for i, c in enumerate(children):
            _build(c, new_prefix, i == len(children) - 1)

    for i, child in enumerate(root.get("children", [])):
        _build(child, "", i == len(root["children"]) - 1)

    return "\n".join(lines)


def expand_flat_paths(node: dict, prefixes: Optional[List[str]] = None) -> List[str]:
    """Expand a compressed tree into a flat list of all module paths."""
    if prefixes is None:
        prefixes = [""]
    all_paths = []
    names = node.get("names", [""])
    current_paths = []
    for prefix in prefixes:
        for nm in names:
            if not nm:
                current_paths.append(prefix)
            elif prefix:
                current_paths.append(f"{prefix}.{nm}")
            else:
                current_paths.append(nm)
    all_paths.extend(current_paths)
    for child in node.get("children", []):
        all_paths.extend(expand_flat_paths(child, current_paths))
    return all_paths


def build_model_tree(model, name: str = "model") -> Tuple[dict, dict, List[str]]:
    """Generate all tree artifacts for a model.

    Returns:
        (compressed_tree, full_tree, flat_paths)
    """
    full_tree = module_to_dict(model, name=name)
    compressed_tree = compress_module_tree(full_tree)
    flat_paths = expand_flat_paths(compressed_tree)
    return compressed_tree, full_tree, flat_paths


def save_model_tree(
    compressed_tree: dict,
    full_tree: dict,
    flat_paths: List[str],
    output_dir: str,
    prefix: str = "model_tree",
):
    """Save all tree artifacts to disk."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    with open(out / f"{prefix}.json", "w") as f:
        json.dump(compressed_tree, f, indent=2)
    with open(out / f"{prefix}_full.json", "w") as f:
        json.dump(full_tree, f, indent=2)
    with open(out / f"{prefix}_flat_paths.txt", "w") as f:
        f.write("\n".join(flat_paths))
    pretty = compressed_tree_to_pretty_string(compressed_tree)
    with open(out / f"{prefix}_pretty.txt", "w") as f:
        f.write(pretty)
    return pretty


# ---------------------------------------------------------------------------
# Step 0.2 — Component Mapping
# ---------------------------------------------------------------------------

@dataclass
class MappingEntry:
    """A single mapping between reference and port module paths.

    Supports one-to-one, one-to-many, and many-to-one mappings.
    Variables (e.g., {i}) are used for repeated structures.
    """
    ref_paths: List[str]
    port_paths: List[str]


@dataclass
class ComponentMapping:
    """Complete component mapping between reference and port models."""
    entries: List[MappingEntry] = field(default_factory=list)
    variables: Dict[str, List[str]] = field(default_factory=dict)
    reasoning: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "ref": {}, "port": {}
    })

    def add_entry(self, ref_paths: List[str], port_paths: List[str]):
        self.entries.append(MappingEntry(ref_paths=ref_paths, port_paths=port_paths))

    def add_variable(self, name: str, values: List[str]):
        self.variables[name] = values

    def add_unmapped_reasoning(self, side: str, path: str, reason: str):
        self.reasoning[side][path] = reason

    def expand_entry(self, entry: MappingEntry) -> List[Tuple[List[str], List[str]]]:
        """Expand a mapping entry by substituting all variable combinations."""
        if not self.variables:
            return [(entry.ref_paths, entry.port_paths)]

        # Find all variables used in this entry
        all_paths = entry.ref_paths + entry.port_paths
        pattern = re.compile(r"\{(\w+)\}")
        used_vars = set()
        for p in all_paths:
            used_vars.update(pattern.findall(p))

        if not used_vars:
            return [(entry.ref_paths, entry.port_paths)]

        # Generate cartesian product of variable values
        ordered_vars = sorted(used_vars)
        value_lists = []
        for v in ordered_vars:
            if v not in self.variables:
                raise ValueError(f"Variable '{v}' not defined in mapping")
            value_lists.append(self.variables[v])

        expanded = []
        for combo in product(*value_lists):
            ref_expanded = list(entry.ref_paths)
            port_expanded = list(entry.port_paths)
            for var_name, val in zip(ordered_vars, combo):
                ref_expanded = [p.replace(f"{{{var_name}}}", val) for p in ref_expanded]
                port_expanded = [p.replace(f"{{{var_name}}}", val) for p in port_expanded]
            expanded.append((ref_expanded, port_expanded))
        return expanded

    def get_all_concrete_mappings(self) -> List[Tuple[List[str], List[str]]]:
        """Return all mappings with variables fully expanded."""
        result = []
        for entry in self.entries:
            result.extend(self.expand_entry(entry))
        return result

    def to_dict(self) -> dict:
        return {
            "mapping_size": len(self.entries),
            "component_mapping": [
                [e.ref_paths, e.port_paths] for e in self.entries
            ],
            "variables": self.variables,
            "reasoning": self.reasoning,
        }

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str) -> "ComponentMapping":
        with open(path) as f:
            data = json.load(f)
        mapping = cls()
        for ref_paths, port_paths in data.get("component_mapping", []):
            mapping.add_entry(ref_paths, port_paths)
        mapping.variables = data.get("variables", {})
        mapping.reasoning = data.get("reasoning", {"ref": {}, "port": {}})
        return mapping


def build_leaf_mapping_by_type(
    ref_model,
    port_model,
    ref_prefix: str = "model",
    port_prefix: str = "model",
) -> ComponentMapping:
    """Auto-generate a best-effort leaf-level mapping by matching module types.

    This is a heuristic starting point. For production use, the mapping
    should be reviewed and refined (especially for fused operators).
    """
    mapping = ComponentMapping()

    ref_leaves = {}
    for name, mod in ref_model.named_modules():
        if len(list(mod.children())) == 0:
            full_path = f"{ref_prefix}.{name}" if name else ref_prefix
            ref_leaves[full_path] = mod.__class__.__name__

    port_leaves = {}
    for name, mod in port_model.named_modules():
        if len(list(mod.children())) == 0:
            full_path = f"{port_prefix}.{name}" if name else port_prefix
            port_leaves[full_path] = mod.__class__.__name__

    # Simple type-based matching (works for identical architectures)
    ref_by_type: Dict[str, List[str]] = {}
    for path, typ in ref_leaves.items():
        ref_by_type.setdefault(typ, []).append(path)

    port_by_type: Dict[str, List[str]] = {}
    for path, typ in port_leaves.items():
        port_by_type.setdefault(typ, []).append(path)

    for typ in ref_by_type:
        if typ in port_by_type:
            ref_paths = sorted(ref_by_type[typ])
            port_paths = sorted(port_by_type[typ])
            if len(ref_paths) == len(port_paths):
                for rp, pp in zip(ref_paths, port_paths):
                    mapping.add_entry([rp], [pp])

    return mapping


# ---------------------------------------------------------------------------
# Step 0.3 — Alignment Functions
# ---------------------------------------------------------------------------

@dataclass
class AlignmentFunction:
    """Defines how to transform a ported tensor into the reference coordinate system."""
    name: str
    ref_paths: List[str]
    port_paths: List[str]
    transform_fn: Callable  # (port_tensors: List[Tensor], ref_tensors: List[Tensor]) -> (aligned_port, aligned_ref)
    description: str = ""


class AlignmentRegistry:
    """Registry of alignment functions keyed by component mapping entries."""

    def __init__(self):
        self._registry: Dict[str, AlignmentFunction] = {}

    def register(self, alignment: AlignmentFunction):
        self._registry[alignment.name] = alignment

    def get(self, name: str) -> Optional[AlignmentFunction]:
        return self._registry.get(name)

    def identity_align(self, port_tensor, ref_tensor):
        """Default alignment: no transformation needed."""
        return port_tensor, ref_tensor

    def get_or_identity(self, name: str) -> Callable:
        af = self._registry.get(name)
        if af:
            return af.transform_fn
        return self.identity_align

    def register_squeeze_batch(self, name: str, ref_paths: List[str], port_paths: List[str]):
        """Register an alignment that removes the batch dimension from reference."""
        def _align(port_tensors, ref_tensors):
            aligned_ref = [t.squeeze(0) if t.dim() > port_tensors[0].dim() else t
                           for t in ref_tensors]
            return port_tensors, aligned_ref
        self.register(AlignmentFunction(
            name=name, ref_paths=ref_paths, port_paths=port_paths,
            transform_fn=_align, description="Remove batch dim from reference",
        ))

    def register_concat_qkv(
        self, name: str,
        ref_q_path: str, ref_k_path: str, ref_v_path: str,
        port_qkv_path: str, dim: int = -1,
    ):
        """Register alignment for fused QKV projections."""
        def _align(port_tensors, ref_tensors):
            import torch as _torch
            # ref_tensors = [q, k, v], port_tensors = [qkv]
            ref_concat = _torch.cat(ref_tensors, dim=dim)
            return port_tensors, [ref_concat]
        self.register(AlignmentFunction(
            name=name,
            ref_paths=[ref_q_path, ref_k_path, ref_v_path],
            port_paths=[port_qkv_path],
            transform_fn=_align,
            description=f"Concat Q/K/V along dim={dim} to match fused QKV",
        ))

    def register_transpose(self, name: str, ref_paths: List[str], port_paths: List[str],
                           dims: Tuple[int, int] = (0, 1)):
        """Register alignment that transposes the port tensor."""
        def _align(port_tensors, ref_tensors):
            aligned_port = [t.transpose(*dims) for t in port_tensors]
            return aligned_port, ref_tensors
        self.register(AlignmentFunction(
            name=name, ref_paths=ref_paths, port_paths=port_paths,
            transform_fn=_align, description=f"Transpose port dims {dims}",
        ))

    def register_custom(self, name: str, ref_paths: List[str], port_paths: List[str],
                        fn: Callable, description: str = ""):
        """Register a custom alignment function."""
        self.register(AlignmentFunction(
            name=name, ref_paths=ref_paths, port_paths=port_paths,
            transform_fn=fn, description=description,
        ))
