#!/usr/bin/env python3
"""Regression tests for composition validation in validate_pave.py.

Structural coverage only: these cases protect validator behavior at the
parent-child boundary. They do not test semantic PAVE conformance.

Run: python3 test_validate_pave_composition.py
"""

from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

from validate_pave import validate_document


CHILD = {
    "pave": {
        "version": "0.3.0",
        "name": "child_flow",
        "purpose": "Produce a reviewed candidate.",
        "entrypoints": ["do_work"],
        "roles": {"worker": {"purpose": "Do the bounded work."}},
        "evidence": {
            "return_bundle": {"kind": "observation", "produced_by": "do_work"},
        },
        "checks": {},
        "nodes": {
            "do_work": {
                "intent": "execute",
                "purpose": "Do the work.",
                "roles": ["worker"],
                "produces": ["return_bundle"],
                "outcomes": {
                    "done": {"meaning": "Work complete."},
                    "stuck": {"meaning": "Cannot proceed."},
                },
            },
        },
        "edges": [
            {"id": "done_to_accepted", "from": "do_work.done", "to": "child_accepted"},
            {"id": "stuck_to_blocked", "from": "do_work.stuck", "to": "child_blocked"},
        ],
        "control_endpoints": {
            "child_accepted": {"kind": "terminal", "meaning": "Accepted."},
            "child_blocked": {"kind": "terminal", "meaning": "Blocked."},
        },
        "state": {"required": ["completed_outcomes"]},
        "completion": {"accepted": "The child accepted its candidate."},
    }
}

PARENT = {
    "pave": {
        "version": "0.3.0",
        "name": "parent_flow",
        "purpose": "Deliver the ported model.",
        "entrypoints": ["port_model"],
        "roles": {"orchestrator": {"purpose": "Run the port."}},
        "evidence": {
            "port_return": {"kind": "observation", "produced_by": "port_model"},
        },
        "checks": {},
        "nodes": {
            "port_model": {
                "intent": "execute",
                "purpose": "Produce a reviewed port.",
                "roles": ["orchestrator"],
                "produces": ["port_return"],
                "allowed_effects": ["modify_candidate", "run_validation"],
                "outcomes": {
                    "candidate_ready": {"required_evidence": ["port_return"]},
                    "port_blocked": {"required_evidence": ["port_return"]},
                },
            },
        },
        "edges": [
            {"id": "ready_to_complete", "from": "port_model.candidate_ready", "to": "complete"},
            {"id": "blocked_to_blocked", "from": "port_model.port_blocked", "to": "blocked"},
        ],
        "control_endpoints": {
            "complete": {"kind": "terminal", "meaning": "Accepted."},
            "blocked": {"kind": "terminal", "meaning": "Blocked."},
        },
        "state": {"required": ["completed_outcomes"]},
        "completion": {"accepted": "The port is delivered."},
        "extensions": {
            "required": ["composition"],
            "composition": {
                "version": "1.0.0",
                "realizations": {
                    "port_model": {
                        "kind": "child_profile",
                        "profile": "child.pave.yaml",
                        "evidence_exports": [
                            {"child": "return_bundle", "parent": "port_return"},
                        ],
                        "terminal_map": {
                            "child_accepted": "candidate_ready",
                            "child_blocked": "port_blocked",
                        },
                        "delegated_effects": ["modify_candidate"],
                    },
                },
            },
        },
    }
}


class CompositionValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name: str, document: dict) -> Path:
        path = self.dir / name
        path.write_text(yaml.safe_dump(document, sort_keys=False))
        return path

    def validate(self, parent: dict, child: dict | None = None) -> list[str]:
        if child is not None:
            self.write("child.pave.yaml", child)
        parent_path = self.write("parent.pave.yaml", parent)
        return validate_document(parent, parent_path)

    def test_valid_atomic_workflow(self) -> None:
        atomic = copy.deepcopy(PARENT)
        del atomic["pave"]["extensions"]
        self.assertEqual(self.validate(atomic), [])

    def test_valid_parent_child_composition(self) -> None:
        self.assertEqual(self.validate(copy.deepcopy(PARENT), copy.deepcopy(CHILD)), [])

    def test_missing_child_profile(self) -> None:
        errors = self.validate(copy.deepcopy(PARENT), child=None)
        self.assertTrue(any("child profile not found" in e for e in errors), errors)

    def test_digest_mismatch(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["profile_digest"] = "sha256:" + "0" * 64
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("digest mismatch" in e for e in errors), errors)

    def test_digest_match_passes(self) -> None:
        child_path = self.write("child.pave.yaml", copy.deepcopy(CHILD))
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["profile_digest"] = (
            "sha256:" + hashlib.sha256(child_path.read_bytes()).hexdigest()
        )
        parent_path = self.write("parent.pave.yaml", parent)
        self.assertEqual(validate_document(parent, parent_path), [])

    def test_unknown_child_entrypoint(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["entrypoint"] = "missing_node"
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("unknown child node missing_node" in e for e in errors), errors)

    def test_unknown_parent_outcome(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["terminal_map"]["child_blocked"] = "no_such_outcome"
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("unknown parent outcome no_such_outcome" in e for e in errors), errors)

    def test_unmapped_child_terminal(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        del realization["terminal_map"]["child_blocked"]
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("unmapped child terminal endpoint child_blocked" in e for e in errors), errors)

    def test_terminal_key_not_a_child_terminal(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["terminal_map"]["do_work"] = "candidate_ready"
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("does not name a child terminal endpoint" in e for e in errors), errors)

    def test_mapped_outcome_requires_evidence(self) -> None:
        parent = copy.deepcopy(PARENT)
        del parent["pave"]["nodes"]["port_model"]["outcomes"]["candidate_ready"]["required_evidence"]
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(
            any("terminal-mapped outcome must declare required_evidence" in e for e in errors),
            errors,
        )

    def test_invalid_evidence_export(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["evidence_exports"] = [{"child": "ghost", "parent": "port_return"}]
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("unknown child evidence 'ghost'" in e for e in errors), errors)

    def test_profile_reference_cycle(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["profile"] = "parent.pave.yaml"
        errors = self.validate(parent)
        self.assertTrue(any("profile reference cycle" in e for e in errors), errors)

    def test_depth_limit(self) -> None:
        grandchild = copy.deepcopy(CHILD)
        grandchild["pave"]["name"] = "grandchild_flow"
        self.write("grandchild.pave.yaml", grandchild)

        middle = copy.deepcopy(CHILD)
        middle["pave"]["nodes"]["do_work"]["outcomes"]["done"] = {
            "required_evidence": ["return_bundle"]
        }
        middle["pave"]["nodes"]["do_work"]["outcomes"]["stuck"] = {
            "required_evidence": ["return_bundle"]
        }
        middle["pave"]["extensions"] = {
            "required": ["composition"],
            "composition": {
                "version": "1.0.0",
                "realizations": {
                    "do_work": {
                        "kind": "child_profile",
                        "profile": "grandchild.pave.yaml",
                        "terminal_map": {
                            "child_accepted": "done",
                            "child_blocked": "stuck",
                        },
                    },
                },
            },
        }

        deep_child = copy.deepcopy(CHILD)
        deep_child["pave"]["name"] = "deep_child"
        deep_child["pave"]["nodes"]["do_work"]["outcomes"]["done"] = {
            "required_evidence": ["return_bundle"]
        }
        deep_child["pave"]["nodes"]["do_work"]["outcomes"]["stuck"] = {
            "required_evidence": ["return_bundle"]
        }
        deep_child["pave"]["extensions"] = {
            "required": ["composition"],
            "composition": {
                "version": "1.0.0",
                "realizations": {
                    "do_work": {
                        "kind": "child_profile",
                        "profile": "middle.pave.yaml",
                        "terminal_map": {
                            "child_accepted": "done",
                            "child_blocked": "stuck",
                        },
                    },
                },
            },
        }
        self.write("middle.pave.yaml", middle)
        self.write("child.pave.yaml", deep_child)
        parent_path = self.write("parent.pave.yaml", copy.deepcopy(PARENT))
        errors = validate_document(copy.deepcopy(PARENT), parent_path)
        self.assertTrue(any("composition depth exceeds 2" in e for e in errors), errors)

    def test_delegated_effect_exceeds_parent_authority(self) -> None:
        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["delegated_effects"] = ["delete_repository"]
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(
            any("effect delete_repository exceeds parent allowed_effects" in e for e in errors),
            errors,
        )

    def test_composition_without_required_declaration(self) -> None:
        parent = copy.deepcopy(PARENT)
        parent["pave"]["extensions"]["required"] = []
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(
            any("must list composition when the composition extension is used" in e for e in errors),
            errors,
        )

    def test_realization_names_unknown_node(self) -> None:
        parent = copy.deepcopy(PARENT)
        realizations = parent["pave"]["extensions"]["composition"]["realizations"]
        realizations["ghost_node"] = realizations.pop("port_model")
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(any("does not name a declared node" in e for e in errors), errors)

    def test_undeclared_node_field_rejected(self) -> None:
        parent = copy.deepcopy(PARENT)
        del parent["pave"]["extensions"]
        parent["pave"]["nodes"]["port_model"]["subgraph"] = {"nodes": {}}
        errors = self.validate(parent)
        self.assertTrue(any("unknown node field" in e for e in errors), errors)

    def test_malformed_composition_block_fails_closed(self) -> None:
        for bad_value in (None, "oops", [1, 2], {}):
            parent = copy.deepcopy(PARENT)
            parent["pave"]["extensions"]["composition"] = bad_value
            errors = self.validate(parent, copy.deepcopy(CHILD))
            self.assertTrue(
                any("must be a non-empty mapping" in e for e in errors),
                (bad_value, errors),
            )

    def test_required_composition_without_block_fails_closed(self) -> None:
        parent = copy.deepcopy(PARENT)
        del parent["pave"]["extensions"]["composition"]
        errors = self.validate(parent, copy.deepcopy(CHILD))
        self.assertTrue(
            any("declares composition but no composition block is present" in e for e in errors),
            errors,
        )

    def test_traceability_cycle_raises_value_error(self) -> None:
        from validate_traceability import expected_rows

        parent = copy.deepcopy(PARENT)
        realization = parent["pave"]["extensions"]["composition"]["realizations"]["port_model"]
        realization["profile"] = "parent.pave.yaml"
        parent_path = self.write("parent.pave.yaml", parent)
        with self.assertRaisesRegex(ValueError, "profile reference cycle"):
            expected_rows(parent["pave"], parent_path)

    def test_traceability_expects_qualified_child_rows(self) -> None:
        from validate_traceability import expected_rows

        self.write("child.pave.yaml", copy.deepcopy(CHILD))
        parent = copy.deepcopy(PARENT)
        parent_path = self.write("parent.pave.yaml", parent)
        expected = expected_rows(parent["pave"], parent_path)
        self.assertIn(("realization", "port_model"), expected)
        self.assertIn(("node", "port_model/do_work"), expected)
        self.assertIn(("contract", "port_model/state"), expected)

    def test_invalid_child_profile_fails_parent(self) -> None:
        child = copy.deepcopy(CHILD)
        del child["pave"]["control_endpoints"]["child_blocked"]
        child["pave"]["edges"] = [{"from": "do_work.done", "to": "child_accepted"}]
        errors = self.validate(copy.deepcopy(PARENT), child)
        self.assertTrue(any("profile[child.pave.yaml]" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
