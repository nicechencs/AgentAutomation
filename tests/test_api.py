"""HTTP boundary checks; these do not stand in for n8n execution tests."""
import os
import tempfile
import unittest
from unittest.mock import patch

# Use a disposable database and test-only tokens, never an n8n credential store.
_temp = tempfile.TemporaryDirectory()
from fastapi.testclient import TestClient
with patch.dict(os.environ, {"DATA_DIR": _temp.name}):
    from agentautomation import app as module


def tearDownModule():
    _temp.cleanup()


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(module.app)

    def test_health_and_same_origin_ui(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "fake")
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_browser_cannot_start_stage(self):
        with patch.object(module, "RUNNER_TOKEN", "test-only"):
            response = self.client.post("/api/runs/unknown/stages/plan", json={"request_id": "one", "execution_id": "one"}, headers={"X-UI-Request": "1"})
            self.assertEqual(response.status_code, 401)

    def test_cross_origin_and_rebound_host_are_rejected(self):
        self.assertEqual(self.client.get("/api/runs", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"template": "development", "goal": "test"}, headers={"Origin": "https://evil.example", "X-UI-Request": "1"}).status_code, 403)
        self.assertEqual(self.client.post("/api/runs", json={"template": "development", "goal": "test"}).status_code, 403)

    def test_missing_configuration_does_not_create_success(self):
        with patch.object(module, "RUNNER_TOKEN", ""), patch.object(module, "WORKFLOW_TOKEN", ""):
            response = self.client.post("/api/runs", json={"template": "development", "goal": "test"}, headers={"X-UI-Request": "1"})
            self.assertEqual(response.status_code, 503)
            self.assertIn("尚未配置", response.json()["detail"])

    def test_bad_limits_rejected(self):
        response = self.client.post("/api/runs", json={"template": "development", "goal": "test", "max_concurrency": 99}, headers={"X-UI-Request": "1"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
