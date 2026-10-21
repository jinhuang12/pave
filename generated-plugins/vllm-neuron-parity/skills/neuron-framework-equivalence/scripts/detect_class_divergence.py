#!/usr/bin/env python3
"""
Detect CPU vs Device class divergence in a target modeling file.

Scans the target implementation for patterns where different classes are used
depending on execution mode (CPU vs device). These divergences mean that
component tests run in CPU mode (Stage 2) may pass while E2E device tests
(Stage 5) fail — because the device uses a completely different class.

Common patterns detected:
  1. get_*_cls() factory functions that branch on NXD_CPU_MODE or on_cpu
  2. Conditional imports or class assignments based on device mode
  3. if/else blocks selecting between CPU and device class variants

Usage:
    python3 scripts/detect_class_divergence.py \
        --target-module-file /path/to/modeling_xxx.py \
        --output {EXP_DIR}/class_divergence_report.json

The output JSON lists each divergence with:
  - component: what the factory/conditional produces (e.g., "rmsnorm")
  - cpu_class: class used in CPU mode (e.g., "LlamaRMSNorm")
  - device_class: class used on device (e.g., "CustomRMSNorm")
  - source_location: file:line where the branching occurs
  - pattern: which detection pattern matched
  - recommendation: what to do about it in testing
"""

import argparse
import json
import os
import re
import sys
from typing import List, Dict, Any


def scan_factory_functions(source: str, filename: str) -> List[Dict[str, Any]]:
    """Detect get_*_cls() factory functions that return different classes."""
    divergences = []
    factory_pattern = re.compile(
        r'def\s+(get_\w+_cls)\s*\([^)]*\).*?(?=\ndef\s|\Z)', re.DOTALL)

    for match in factory_pattern.finditer(source):
        func_name = match.group(1)
        func_body = match.group(0)
        func_start = source[:match.start()].count('\n') + 1

        cpu_classes, device_classes = [], []
        lines = func_body.split('\n')
        in_cpu_branch = False
        in_device_branch = False

        for line in lines:
            stripped = line.strip()
            if re.search(r'(NXD_CPU_MODE|on_cpu|cpu_mode|is_cpu)', stripped, re.IGNORECASE):
                if 'if' in stripped:
                    in_cpu_branch, in_device_branch = True, False
            elif stripped.startswith('else'):
                if in_cpu_branch:
                    in_cpu_branch, in_device_branch = False, True

            return_match = re.search(r'return\s+(\w+)', stripped)
            if return_match:
                cls_name = return_match.group(1)
                if in_cpu_branch:
                    cpu_classes.append(cls_name)
                elif in_device_branch:
                    device_classes.append(cls_name)

        if cpu_classes and device_classes and cpu_classes != device_classes:
            component = func_name.replace('get_', '').replace('_cls', '')
            divergences.append({
                "component": component,
                "cpu_class": cpu_classes[0],
                "device_class": device_classes[0],
                "source_location": f"{filename}:{func_start}",
                "pattern": "factory_function",
                "factory_function": func_name,
                "recommendation": (
                    f"Stage 2 CPU tests use {cpu_classes[0]}. On device, "
                    f"{device_classes[0]} runs instead. Write a DUAL test: "
                    f"one with {cpu_classes[0]} (CPU validation) and one "
                    f"reimplementing {device_classes[0]}'s math in pure "
                    f"PyTorch (device algorithm validation). Both must pass."
                ),
            })
    return divergences


def scan_conditional_assignments(source: str, filename: str) -> List[Dict[str, Any]]:
    """Detect conditional class assignments based on device mode."""
    divergences = []

    # Ternary: self.xxx = ClassA(...) if cpu else ClassB(...)
    ternary_pattern = re.compile(
        r'self\.(\w+)\s*=\s*(\w+)\s*\([^)]*\)\s*if\s+.*?'
        r'(NXD_CPU_MODE|on_cpu|cpu_mode|is_cpu).*?else\s+(\w+)\s*\(',
        re.IGNORECASE)

    for match in ternary_pattern.finditer(source):
        attr_name, class_a, _, class_b = match.groups()
        line_num = source[:match.start()].count('\n') + 1
        if class_a != class_b:
            divergences.append({
                "component": attr_name,
                "cpu_class": class_a,
                "device_class": class_b,
                "source_location": f"{filename}:{line_num}",
                "pattern": "conditional_assignment",
                "recommendation": (
                    f"self.{attr_name} uses {class_a} on CPU but "
                    f"{class_b} on device. Test both variants in Stage 2."
                ),
            })

    # Block: if cpu: \n self.xxx = A(...) \n else: \n self.xxx = B(...)
    block_pattern = re.compile(
        r'if\s+.*?(NXD_CPU_MODE|on_cpu|cpu_mode|is_cpu).*?:\s*\n'
        r'\s+self\.(\w+)\s*=\s*(\w+)\s*\('
        r'.*?else\s*:\s*\n'
        r'\s+self\.\2\s*=\s*(\w+)\s*\(',
        re.DOTALL | re.IGNORECASE)

    for match in block_pattern.finditer(source):
        _, attr_name, cpu_class, device_class = match.groups()
        line_num = source[:match.start()].count('\n') + 1
        if cpu_class != device_class:
            already = any(d["component"] == attr_name
                          and d["pattern"] == "conditional_assignment"
                          for d in divergences)
            if not already:
                divergences.append({
                    "component": attr_name,
                    "cpu_class": cpu_class,
                    "device_class": device_class,
                    "source_location": f"{filename}:{line_num}",
                    "pattern": "conditional_block",
                    "recommendation": (
                        f"self.{attr_name} uses {cpu_class} on CPU but "
                        f"{device_class} on device. Test both in Stage 2."
                    ),
                })
    return divergences


def scan_nki_kernel_imports(source: str, filename: str) -> List[Dict[str, Any]]:
    """Detect NKI kernel classes that only run on device."""
    divergences = []
    nki_classes = set()
    for match in re.finditer(r'class\s+(Custom\w+)', source):
        nki_classes.add(match.group(1))
    for match in re.finditer(r'from\s+\S+\s+import\s+.*?(Custom\w+)', source):
        nki_classes.add(match.group(1))

    for nki_cls in nki_classes:
        context_pattern = re.compile(
            rf'({nki_cls})\s*.*?(?:if|else).*?'
            rf'(\w+(?:RMSNorm|LayerNorm|Attention|Linear)\w*)',
            re.IGNORECASE)
        for match in context_pattern.finditer(source):
            cpu_candidate = match.group(2)
            if cpu_candidate != nki_cls:
                line_num = source[:match.start()].count('\n') + 1
                component = nki_cls.replace('Custom', '').lower()
                divergences.append({
                    "component": component,
                    "cpu_class": cpu_candidate,
                    "device_class": nki_cls,
                    "source_location": f"{filename}:{line_num}",
                    "pattern": "nki_kernel",
                    "recommendation": (
                        f"NKI kernel {nki_cls} runs on device but "
                        f"{cpu_candidate} on CPU. Reimplement "
                        f"{nki_cls}'s math in pure PyTorch for Stage 2. "
                        f"The actual kernel is validated at Stage 5."
                    ),
                })
    return divergences


def detect_divergences(filepath: str) -> Dict[str, Any]:
    """Run all detection patterns on a target modeling file."""
    with open(filepath, 'r') as f:
        source = f.read()
    filename = os.path.basename(filepath)

    all_divs = []
    all_divs.extend(scan_factory_functions(source, filename))
    all_divs.extend(scan_conditional_assignments(source, filename))
    all_divs.extend(scan_nki_kernel_imports(source, filename))

    seen = set()
    unique = []
    for d in all_divs:
        key = (d["component"], d["cpu_class"], d["device_class"])
        if key not in seen:
            seen.add(key)
            unique.append(d)

    return {
        "file": filepath,
        "divergence_count": len(unique),
        "divergences": unique,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Detect CPU vs Device class divergence")
    parser.add_argument("--target-module-file", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    result = detect_divergences(args.target_module_file)

    print(f"\n{'=' * 70}")
    print(f"  CPU vs Device Class Divergence Report")
    print(f"  File: {args.target_module_file}")
    print(f"{'=' * 70}")

    if result["divergence_count"] == 0:
        print(f"\n  No CPU/device class divergences detected.")
        print(f"  All components use the same class in both modes.")
    else:
        print(f"\n  Found {result['divergence_count']} divergence(s):\n")
        for d in result["divergences"]:
            print(f"  [{d['pattern']}] {d['component']}")
            print(f"    CPU class:    {d['cpu_class']}")
            print(f"    Device class: {d['device_class']}")
            print(f"    Location:     {d['source_location']}")
            print(f"    Action:       {d['recommendation']}")
            print()

    print(f"{'=' * 70}")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"  Report saved to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
