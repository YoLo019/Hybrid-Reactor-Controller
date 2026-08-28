# -*- coding: utf-8 -*-

import importlib.util
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "research_execution" / "04_experiments" / "configs"
SCRIPT_PATH = PROJECT_ROOT / "4_4" / "flexibility" / "prepare_e4_e5.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("prepare_e4_e5", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class E4E5PreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_script()
        with (CONFIG_ROOT / "e4_validation_v1.json").open(encoding="utf-8") as handle:
            cls.e4 = json.load(handle)
        with (CONFIG_ROOT / "e5_capacity_elasticity_v1.json").open(
            encoding="utf-8"
        ) as handle:
            cls.e5 = json.load(handle)

    def test_e4_freezes_exact_independent_binomial_contract(self):
        self.assertEqual(self.e4["dangerous_miss"]["confidence_level"], 0.95)
        self.assertIn("Clopper-Pearson", self.e4["dangerous_miss"]["interval"])
        self.assertEqual(
            self.e4["data_isolation"]["required_roles"],
            ["boundary_construction", "independent_validation", "adversarial_stress"],
        )
        self.assertEqual(self.e4["data_isolation"]["locked_splits_accessed"], [])

    def test_e4_keeps_unknown_formal_values_unfrozen(self):
        self.assertIsNone(
            self.e4["data_isolation"]["formal_role_to_time_window_assignment"]
        )
        self.assertEqual(self.e4["trajectory_manifests"]["independent_validation"], [])
        self.assertIsNone(
            self.e4["dangerous_miss"]["upper_bound_acceptance_threshold"]
        )
        self.assertIsNone(self.e4["boundary_error"]["acceptance_thresholds"])
        self.assertIsNone(
            self.e4["temporary_constraint_sensitivity"]["perturbation_levels"]
        )

    def test_e5_freezes_three_step_structure_without_inventing_values(self):
        levels = self.e5["relative_step_levels"]
        self.assertEqual([item["label"] for item in levels], ["small", "nominal", "large"])
        self.assertTrue(all(item["fraction"] is None for item in levels))
        self.assertEqual(
            [item["id"] for item in self.e5["capacity_dimensions"]],
            ["bess_power", "bess_energy", "valve_rate", "rod_rate"],
        )

    def test_e5_freezes_normalization_and_nondifferentiability_report(self):
        finite_difference = self.e5["finite_difference"]
        self.assertEqual(finite_difference["method"], "normalized central finite difference")
        self.assertIn("c_i / rho(c)", finite_difference["dimensionless_elasticity_formula"])
        nondifferentiability = self.e5["active_set_and_nondifferentiability"]
        self.assertEqual(
            nondifferentiability["required_active_sets"], ["minus", "base", "plus"]
        )
        self.assertTrue(nondifferentiability["one_sided_slopes_required_on_switch"])
        self.assertIsNone(
            nondifferentiability["one_sided_slope_disagreement_tolerance"]
        )

    def test_machine_gate_passes_preparation_but_blocks_formal_execution(self):
        report = self.module.build_preparation_report(PROJECT_ROOT)
        self.assertTrue(report["preparation_gate"]["pass"])
        self.assertFalse(report["formal_execution_gate"]["pass"])
        self.assertTrue(report["formal_execution_gate"]["e4_blockers"])
        self.assertTrue(report["formal_execution_gate"]["e5_blockers"])
        self.assertEqual(report["data_access"]["locked_splits_accessed"], [])
        self.assertEqual(report["data_access"]["boundary_results_accessed"], [])
        self.assertFalse(report["model_execution"]["launched"])


if __name__ == "__main__":
    unittest.main()
