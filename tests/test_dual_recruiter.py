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
    def __init__(self, version, pages=None):
        self.api_version = version
        self.pages = list(pages or [{"items": []}])
        self.calls = []

    def discover_linkedin_account(self):
        return f"{self.api_version}-account"

    def list_projects(self, _account_id, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)


class DualRecruiterTests(unittest.TestCase):
    def test_default_config_blocks_writes(self):
        with TemporaryDirectory() as directory:
            config = load_dual_config(Path(directory) / "missing.json")
        self.assertEqual(config["write_backend"], "disabled")

    def test_v1_can_never_be_configured_as_writer(self):
        config = default_config()
        config["write_backend"] = "v1"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "dual.json"
            path.write_text(__import__("json").dumps(config))
            with self.assertRaisesRegex(ValueError, "disabled or v2"):
                load_dual_config(path)

    def test_backend_selection_is_explicit_and_never_falls_back(self):
        v1 = FakeRecruiterClient("v1")
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1)}, default_config()
        )
        self.assertIs(gateway.backend("v1").client, v1)
        with self.assertRaisesRegex(ValueError, "v2 backend is not configured"):
            gateway.backend("v2")
        with self.assertRaisesRegex(ValueError, "explicitly set"):
            gateway.backend("auto")

    def test_project_comparison_correlates_names_not_ids(self):
        v1 = FakeRecruiterClient(
            "v1", [{"items": [{"id": "v1-project", "name": "Strala 150"}]}]
        )
        v2 = FakeRecruiterClient(
            "v2", [{"items": [{"id": "v2-project", "name": "Strala 150"}]}]
        )
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1), "v2": Backend("v2", v2)},
            default_config(),
        )
        result = gateway.compare_projects()
        self.assertEqual(result["counts"]["matched"], 1)
        self.assertEqual(result["matched"][0]["v1_ids"], ["v1-project"])
        self.assertEqual(result["matched"][0]["v2_ids"], ["v2-project"])

    def test_v1_project_pagination_uses_provider_cursors(self):
        v1 = FakeRecruiterClient(
            "v1",
            [
                {"items": [{"id": "one"}], "cursor": "next"},
                {"items": [{"id": "two"}]},
            ],
        )
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1)}, default_config()
        )
        self.assertEqual([row["id"] for row in gateway.all_projects("v1")], ["one", "two"])
        self.assertIsNone(v1.calls[0]["cursor"])
        self.assertEqual(v1.calls[1]["cursor"], "next")

    def test_write_backend_is_disabled_or_v2_only(self):
        v1 = FakeRecruiterClient("v1")
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1)}, default_config()
        )
        with self.assertRaisesRegex(ValueError, "Writes are disabled"):
            gateway.write_backend()

        config = default_config()
        config["write_backend"] = "v2"
        v2 = FakeRecruiterClient("v2")
        gateway = DualRecruiterGateway(
            {"v1": Backend("v1", v1), "v2": Backend("v2", v2)}, config
        )
        self.assertIs(gateway.write_backend().client, v2)


if __name__ == "__main__":
    unittest.main()
