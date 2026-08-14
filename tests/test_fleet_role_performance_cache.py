import importlib.util
import json
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "scripts"
    / "fleet-role-performance"
    / "run_unipile_role_performance_report.py"
)
SPEC = importlib.util.spec_from_file_location("fleet_role_performance", MODULE_PATH)
assert SPEC and SPEC.loader
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class Response:
    ok = True
    reason = "OK"

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = []

    def get(self, url, params=None, timeout=60):
        self.calls.append((url, params))
        job_id = url.rsplit("/", 1)[-1]
        return Response(self.jobs[job_id])


def test_cached_inventory_refreshes_only_known_jobs(tmp_path, monkeypatch):
    inventory = [
        {
            "project_id": f"project-{index}",
            "job_id": f"job-{index}",
            "project_name": f"Fleet AI - Role {index}",
            "public_title": f"Role {index}",
        }
        for index in range(9)
    ]
    jobs = {
        role["job_id"]: {
            "id": role["job_id"],
            "project_id": role["project_id"],
            "state": "LISTED",
            "title": role["public_title"],
            "views_count": 100,
            "applications_count": 10,
        }
        for role in inventory
    }
    session = FakeSession(jobs)
    loaded, project_count, job_count, closed, mode = report._load_cached_fleet_roles(
        session,
        "account",
        inventory,
        2.0,
        "test",
    )

    assert mode == "cached"
    assert len(loaded) == 9
    assert len(session.calls) == 9
    assert project_count == 0
    assert job_count == 9
    assert closed == []


def test_job_payload_accepts_wrapped_data():
    payload = {"data": {"id": "job-1"}}
    assert report._job_payload(payload, "job-1") == {"id": "job-1"}
