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
        self.assertEqual(response.json()["mode"], "real")
        self.assertEqual(response.json()["modes"], ["real", "fake"])
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

    def test_remote_client_cannot_spoof_localhost(self):
        remote = TestClient(module.app, client=("192.0.2.10", 12345))
        self.assertEqual(remote.get("/api/runs", headers={"Host": "localhost"}).status_code, 403)
        self.assertEqual(remote.post("/api/runs", headers={"Host": "localhost", "X-UI-Request": "1"},
                                     json={"template": "development", "goal": "test"}).status_code, 403)

    def test_real_default_missing_inputs_is_not_fake_fallback(self):
        with patch.object(module, "RUNNER_TOKEN", "test-only"), patch.object(module, "WORKFLOW_TOKEN", "test-only"), patch.object(module, "n8n_ready", return_value=True):
            response = self.client.post("/api/runs", json={"template": "development", "goal": "test"}, headers={"X-UI-Request": "1"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("id", response.json())

    def test_real_gate_rejections_do_not_mutate_run(self):
        from agentautomation.runtime import Store
        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "runner.sqlite3"))
            run = store.create("development", "test")
            run["mode"] = "real"
            run["status"] = "awaiting_approval"
            run["phase"] = "planned"
            run["options"] = {}
            store.add_approval(run, "plan")
            store.save(run)
            before = store.get(run["id"])
            with patch.object(module, "store", store), patch.object(module, "RUNNER_TOKEN", "test-only"):
                for stage, expected in [("code", 409), ("integrate", 409), ("unknown", 422)]:
                    response = self.client.post(f"/api/runs/{run['id']}/stages/{stage}",
                        headers={"X-Runner-Token": "test-only"}, json={"request_id": stage, "execution_id": "test"})
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(store.get(run["id"]), before)

    def test_real_stage_returns_before_completion_and_deduplicates(self):
        import asyncio
        from agentautomation.runtime import Store
        from agentautomation.execution import RealExecutor
        calls = []

        async def execute(executor, snapshot, stage):
            calls.append(stage)
            await asyncio.sleep(0.2)
            return {"plan": {"title": "test", "instructions": "test", "acceptance": ["test"]}, "tasks": []}

        with tempfile.TemporaryDirectory() as directory:
            store = Store(os.path.join(directory, "runner.sqlite3"))
            run = store.create("development", "test")
            run.update(mode="real", status="running")
            store.save(run)
            with patch.object(module, "store", store), patch.object(module, "RUNNER_TOKEN", "test-only"), patch.object(RealExecutor, "execute", execute):
                with TestClient(module.app) as client:
                    endpoint = f"/api/runs/{run['id']}/stages/plan"
                    body = {"request_id": "plan-one", "execution_id": "test"}
                    headers = {"X-Runner-Token": "test-only"}
                    first = client.post(endpoint, json=body, headers=headers)
                    self.assertEqual(first.status_code, 200)
                    self.assertEqual(first.json()["inflight"], "plan")
                    second = client.post(endpoint, json=body, headers=headers)
                    self.assertEqual(second.status_code, 200)
                    self.assertEqual(calls, ["plan"])
                    self.assertEqual(store.get(run["id"])["cycles"], 1)

    def test_bad_limits_rejected(self):
        response = self.client.post("/api/runs", json={"template": "development", "goal": "test", "max_concurrency": 99}, headers={"X-UI-Request": "1"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
