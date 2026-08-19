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


class V2ClassicIdClient(RecordingProfileClient):
    def get_linkedin_profile(self, account_id, identifier, linkedin_api):
        self.calls.append((account_id, identifier, linkedin_api))
        if linkedin_api is None:
            return {
                "id": "ACoA-classic-id",
                "public_identifier": identifier,
                "is_open_to_work": None,
            }
        return {
            "id": "AE-recruiter-id",
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

    def test_public_slug_resolution_uses_v2_classic_id(self):
        wrapper = UnipileWrapper.__new__(UnipileWrapper)
        wrapper.client = V2ClassicIdClient()

        result = json.loads(wrapper.get_linkedin_open_to_work("acc_123", "ada"))

        self.assertEqual(result["provider_id"], "AE-recruiter-id")
        self.assertEqual(
            wrapper.client.calls,
            [
                ("acc_123", "ada", None),
                ("acc_123", "ACoA-classic-id", "recruiter"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
