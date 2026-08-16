import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_server_unipile.dual_recruiter import (
    Backend,
    DualRecruiterGateway,
    default_config,
    load_dual_config,
)


class FakeRecruiterClient:
    def __init__(self, version, projects=None):
        self.api_version = version
        self.projects = list(projects or [])
        self.profile_calls = 0
        self.saves = []

    def discover_linkedin_account(self):
        return f"{self.api_version}-account"

    def list_projects(self, _account_id, **_kwargs):
        return {"items": self.projects, "total_count": len(self.projects)}

    def get_project(self, _account_id, project_id):
        return next(item for item in self.projects if item["id"] == project_id)

    def open_to_work(self, _account_id, identifier):
        return {"public_identifier": identifier, "is_open_to_work": True, "profile_calls": 1}

    def resolve_recruiter_profile(self, _account_id, identifier):
        self.profile_calls += 1
        return ({"candidate_id": identifier, "first_name": "Ada", "last_name": "Lovelace"}, 1)

    def save_candidate(self, account_id, project_id, candidate_id, stage):
        self.saves.append((account_id, project_id, candidate_id, stage))
        return {"saved": True}


def config_with_project(writer="disabled"):
    config = default_config()
    config["write_backend"] = writer
    config["projects"] = {
        "strala_150": {
            "name": "Strala 150",
            "ids": {"v1": "v1-project", "v2": "v2-project"},
            "stages": {
                "uncontacted": {"v1": "UNCONTACTED", "v2": "stage-new"}
            },
        }
    }
    return config


@unittest.skip("legacy dual v1/v2 bridge is deprecated and excluded from the supported package")
class DualRecruiterTests(unittest.TestCase):
    def make_gateway(self, writer="disabled"):
        v1 = FakeRecruiterClient("v1", [{"id": "v1-project", "name": "Strala 150"}])
        v2 = FakeRecruiterClient("v2", [{"id": "v2-project", "name": "Strala 150"}])
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1), "v2": Backend("v2", v2)},
            config_with_project(writer),
        )
        return gateway, v1, v2

    def test_default_config_prefers_v2_and_blocks_writes(self):
        with TemporaryDirectory() as directory:
            config = load_dual_config(Path(directory) / "missing.json")
        self.assertEqual(config["read_preference"], "v2")
        self.assertEqual(config["write_backend"], "disabled")

    def test_open_to_work_prefers_v2_and_can_force_v1(self):
        gateway, _v1, _v2 = self.make_gateway()
        self.assertEqual(gateway.open_to_work("candidate")["reader"], "v2")
        self.assertEqual(gateway.open_to_work("candidate", "v1")["reader"], "v1")

    def test_missing_v2_falls_back_to_v1(self):
        v1 = FakeRecruiterClient("v1")
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1)}, default_config()
        )
        self.assertEqual(gateway.open_to_work("candidate")["reader"], "v1")

    def test_project_comparison_correlates_names_not_ids(self):
        gateway, _v1, _v2 = self.make_gateway()
        result = gateway.compare_projects()
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["matched"][0]["v1_ids"], ["v1-project"])
        self.assertEqual(result["matched"][0]["v2_ids"], ["v2-project"])

    def test_disabled_writer_blocks_before_profile_lookup(self):
        gateway, v1, v2 = self.make_gateway()
        with self.assertRaisesRegex(ValueError, "Writes are disabled"):
            gateway.save_plan("candidate", "strala_150", "uncontacted")
        self.assertEqual(v1.profile_calls + v2.profile_calls, 0)

    def test_v2_is_the_only_writer_when_enabled(self):
        gateway, v1, v2 = self.make_gateway("v2")
        plan = gateway.save_plan("candidate", "strala_150", "uncontacted")
        self.assertEqual(plan["writer"], "v2")
        self.assertEqual(plan["project_id"], "v2-project")
        self.assertEqual(plan["stage"], "stage-new")
        self.assertNotIn("account_id", plan)
        gateway.execute_save(plan)
        self.assertEqual(v1.saves, [])
        self.assertEqual(
            v2.saves,
            [("v2-account", "v2-project", "candidate", "stage-new")],
        )


if __name__ == "__main__":
    unittest.main()
