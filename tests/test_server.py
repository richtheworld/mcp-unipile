import json
import unittest

from mcp_server_unipile.server import UnipileWrapper


class RecordingProfileClient:
    def __init__(self):
        self.calls = []

    def get_linkedin_profile(self, account_id, identifier, linkedin_api):
        self.calls.append((account_id, identifier, linkedin_api))
        if linkedin_api is None:
            raise AssertionError("Recruiter identifiers must not be routed through Classic")
        return {
            "provider": "LINKEDIN",
            "provider_id": identifier,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "is_open_to_work": True,
        }


class UnipileWrapperTests(unittest.TestCase):
    def test_ae_identifier_routes_directly_to_recruiter(self):
        wrapper = UnipileWrapper.__new__(UnipileWrapper)
        wrapper.client = RecordingProfileClient()

        result = json.loads(
            wrapper.get_linkedin_open_to_work("acc_123", "AE123456789")
        )

        self.assertTrue(result["is_open_to_work"])
        self.assertEqual(
            wrapper.client.calls,
            [("acc_123", "AE123456789", "recruiter")],
        )


if __name__ == "__main__":
    unittest.main()
