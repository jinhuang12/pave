#!/usr/bin/env python3
"""
Neuron Device Validator Template
Reusable validation utilities for verifying Neuron device execution

Usage:
    from neuron_device_validator import assert_on_neuron, validate_inference_result

    # Pre-inference validation
    assert_on_neuron(compiled_path='/path/to/compiled/model')

    # Run inference
    success, response, metrics = run_inference_with_classes(...)

    # Post-inference validation
    is_valid, msg = validate_inference_result(success, metrics, min_tokens_per_sec=20.0)
"""

import os
import re
import subprocess


class NeuronDeviceQueryError(RuntimeError):
    """neuron-ls failed, or its output had no device table this template can read."""


def check_neuron_runtime_available():
    """
    Check if Neuron runtime is available by running neuron-ls command.

    Returns:
        bool: True if Neuron runtime is available, False otherwise
    """
    try:
        result = subprocess.run(
            ['neuron-ls'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def parse_neuron_ls_table(text):
    """
    Count Neuron devices in the plain `neuron-ls` table.

    The table is read by its header, not by column position: the "NEURON DEVICE"
    column holds one integer per device row. Rows with an empty device cell are
    process continuation rows (--show-all-procs) and are skipped. The plain table
    is used because the neuron-ls JSON flags (--json-output, -j) end in Neuron 2.34.

    Returns:
        int: Number of Neuron devices, always >= 1

    Raises:
        NeuronDeviceQueryError: no device table, no "NEURON DEVICE" column, or no device rows
    """
    text = re.sub(r'\x1b\[[0-9;]*m', '', text)
    rows = [
        [cell.strip() for cell in line.strip()[1:-1].split('|')]
        for line in text.splitlines()
        if line.strip().startswith('|') and line.strip().endswith('|')
    ]
    if len(rows) < 3:
        raise NeuronDeviceQueryError(
            "neuron-ls printed no device table; output starts: %r" % text[:200]
        )
    header = [' '.join(parts).strip() for parts in zip(rows[0], rows[1])]
    if 'NEURON DEVICE' not in header:
        raise NeuronDeviceQueryError(
            "neuron-ls table has no NEURON DEVICE column; header: %r" % header
        )
    col = header.index('NEURON DEVICE')
    devices = {row[col] for row in rows[2:] if len(row) > col and row[col].isdigit()}
    if not devices:
        raise NeuronDeviceQueryError(
            "neuron-ls table has no device rows; header: %r" % header
        )
    return len(devices)


def get_neuron_device_count():
    """
    Get the number of available Neuron devices from the plain `neuron-ls` table.

    Returns:
        int: Number of Neuron devices, always >= 1

    Raises:
        NeuronDeviceQueryError: neuron-ls is missing, times out, exits non-zero,
            or prints output this template cannot read. It never returns 0.
    """
    try:
        result = subprocess.run(
            ['neuron-ls'],
            capture_output=True,
            text=True,
            timeout=5
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise NeuronDeviceQueryError("neuron-ls did not run: %s" % exc) from exc
    if result.returncode != 0:
        raise NeuronDeviceQueryError(
            "neuron-ls exited %d: %s" % (result.returncode, result.stderr.strip()[:200])
        )
    return parse_neuron_ls_table(result.stdout)


def check_neff_format_exists(compiled_path):
    """
    Check if compiled model exists in NEFF format.

    Args:
        compiled_path (str): Path to compiled model directory

    Returns:
        bool: True if NEFF format files exist, False otherwise
    """
    model_pt = os.path.join(compiled_path, 'model.pt')
    neuron_config = os.path.join(compiled_path, 'neuron_config.json')

    model_exists = os.path.exists(model_pt)
    config_exists = os.path.exists(neuron_config)

    return model_exists and config_exists


def validate_inference_result(success, metrics, min_tokens_per_sec=20.0):
    """
    Validate inference result based on throughput and success status.

    Args:
        success (bool): Whether inference succeeded
        metrics (dict): Dictionary with 'tokens_per_second' key
        min_tokens_per_sec (float): Minimum expected throughput for Neuron execution
                                    Adjust based on model size:
                                    - Small models (0.6B-1B): 20 tokens/sec
                                    - Medium models (7B-13B): 10 tokens/sec
                                    - Large models (70B+): 2 tokens/sec

    Returns:
        tuple: (is_valid, message)
            is_valid (bool): True if validation passed, False otherwise
            message (str): Validation result message
    """
    if not success:
        return False, "Inference failed - check error logs"

    tokens_per_sec = metrics.get('tokens_per_second', 0)

    if tokens_per_sec < min_tokens_per_sec:
        return False, (
            f"Throughput too low: {tokens_per_sec:.2f} tokens/sec "
            f"(expected >= {min_tokens_per_sec:.2f} tokens/sec) - "
            f"Model is likely running on CPU!"
        )

    return True, f"Validation passed: {tokens_per_sec:.2f} tokens/sec"


def assert_on_neuron(compiled_path, verbose=True):
    """
    Assert that environment is properly configured for Neuron execution.
    Raises AssertionError if validation fails.

    Args:
        compiled_path (str): Path to compiled NEFF model
        verbose (bool): Print validation status messages

    Raises:
        AssertionError: If validation checks fail
    """
    # Check 1: Neuron runtime available
    if not check_neuron_runtime_available():
        raise AssertionError(
            "Neuron runtime not available! "
            "Run 'neuron-ls' to verify Neuron devices are accessible."
        )

    try:
        device_count = get_neuron_device_count()
    except NeuronDeviceQueryError as exc:
        raise AssertionError(f"Could not count Neuron devices: {exc}") from exc
    if verbose:
        print(f"✓ Neuron runtime available ({device_count} devices detected)")

    # Check 2: NEFF format exists
    if not check_neff_format_exists(compiled_path):
        raise AssertionError(
            f"Compiled NEFF model not found at {compiled_path}. "
            f"Expected files: model.pt and neuron_config.json"
        )

    if verbose:
        print(f"✓ NEFF format detected at {compiled_path}")

    if verbose:
        print("✓ Pre-inference validation passed: Ready for Neuron execution")


def print_validation_summary(
    pre_validation_passed,
    inference_success,
    post_validation_passed,
    metrics
):
    """
    Print a comprehensive validation summary.

    Args:
        pre_validation_passed (bool): Pre-inference validation result
        inference_success (bool): Inference execution result
        post_validation_passed (bool): Post-inference validation result
        metrics (dict): Performance metrics
    """
    print("\n" + "=" * 80)
    print("NEURON DEVICE VALIDATION SUMMARY")
    print("=" * 80)

    # Pre-inference
    status = "✅ PASSED" if pre_validation_passed else "❌ FAILED"
    print(f"Pre-inference validation:  {status}")

    # Inference
    status = "✅ SUCCESS" if inference_success else "❌ FAILED"
    print(f"Inference execution:       {status}")

    # Post-inference
    status = "✅ PASSED" if post_validation_passed else "❌ FAILED"
    print(f"Post-inference validation: {status}")

    # Metrics
    if inference_success and metrics:
        tokens_per_sec = metrics.get('tokens_per_second', 0)
        print(f"\nPerformance: {tokens_per_sec:.2f} tokens/sec")

    # Final assessment
    all_passed = pre_validation_passed and inference_success and post_validation_passed
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ FINAL ASSESSMENT: Model is running on Neuron devices")
    else:
        print("❌ FINAL ASSESSMENT: Validation failed - check logs above")
    print("=" * 80)


if __name__ == "__main__":
    # Self-test
    print("Running Neuron Device Validator self-test...")
    print()

    # Test 1: Runtime availability
    print("Test 1: Neuron runtime availability")
    runtime_available = check_neuron_runtime_available()
    print(f"  Result: {'✓ Available' if runtime_available else '✗ Not available'}")

    if runtime_available:
        try:
            print(f"  Devices: {get_neuron_device_count()}")
        except NeuronDeviceQueryError as exc:
            print(f"  Devices: ✗ {exc}")

    print()

    # Test 2: NEFF format check (example path)
    print("Test 2: NEFF format detection")
    print("  Note: This will fail unless you provide a valid compiled model path")
    example_path = "/root/equiv-check-rst/model_compiled"
    neff_exists = check_neff_format_exists(example_path)
    print(f"  Result: {'✓ Found' if neff_exists else '✗ Not found'} at {example_path}")

    print()
    print("Self-test complete!")
