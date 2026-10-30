"""Shared pytest setup for the ammo plugin tests.

Markers `neuron` and `requires_gpu` are deselected by default: the plugin's
static battery runs on any machine, and Neuron paths are not shipped in v1
(conventions §6). Pass `-m` to choose for yourself, for example
`-m requires_gpu` on a GPU host. Any `-m` expression turns the default off.
"""

from __future__ import annotations

DEFAULT_OFF = ("neuron", "requires_gpu")


def pytest_configure(config):
    config.addinivalue_line("markers", "neuron: Neuron path test; deselected unless -m selects it")
    config.addinivalue_line("markers", "requires_gpu: needs a GPU; deselected unless -m selects it")
    config.addinivalue_line("markers", "unit: unit test that needs no server")


def pytest_collection_modifyitems(config, items):
    if config.getoption("markexpr", ""):
        return
    keep, drop = [], []
    for item in items:
        (drop if any(item.get_closest_marker(name) for name in DEFAULT_OFF) else keep).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
