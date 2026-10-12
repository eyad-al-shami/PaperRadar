"""Parallel tagging regression tests; API responses are synthetic and event-controlled."""

import json
import queue
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from research_tagger.web import create_app


class ParallelTaggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(Path(self.temp.name) / "parallel.sqlite3")
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.db = self.app.state.db
        self.conference_id = self.client.post("/api/conferences", json={"name":"ECCV", "year":2024}).json()["id"]
        self.db.discover(self.conference_id, [
            {"title":f"Paper {i}", "source_url":f"https://eccv.ecva.net/virtual/2024/poster/{i}"} for i in range(12)
        ])
        self.db.execute("UPDATE papers SET abstract='Synthetic abstract'")
        self.client.put("/api/key", json={"api_key":"synthetic-test-key"})
        self.config = self.client.get("/api/settings").json()["tagging"]
        self.ids = [item["id"] for item in self.config["domains"]]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def wait_until(self, predicate):
        """Wait briefly for the coordinator to commit, with a bounded failure timeout."""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("Timed out waiting for the tagging coordinator")

    def job(self, job_id):
        return self.db.rows("SELECT * FROM jobs WHERE id=?", (job_id,))[0]

    def start(self, retry=False):
        estimate = self.client.post(f"/api/conferences/{self.conference_id}/tag-estimate", json={"domain_ids":self.ids}).json()
        response = self.client.post(f"/api/conferences/{self.conference_id}/jobs", json={
            "kind":"tag", "domain_ids":self.ids, "estimate_signature":estimate["estimate_signature"],
            "retry_failed_only":retry,
        })
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["id"]

    @contextmanager
    def controlled_requests(self, failures=()):
        """Hold synthetic requests until released and ensure no worker outlives its mock."""
        self.entered = queue.Queue()
        self.release = {f"Paper {i}":threading.Event() for i in range(12)}
        self.calls = []
        self.in_flight = 0
        self.peak = 0
        lock = threading.Lock()

        def post(url, *, headers, json):
            title = json["state"]["title"]
            with lock:
                self.calls.append(title)
                self.in_flight += 1
                self.peak = max(self.peak, self.in_flight)
            self.entered.put(title)
            try:
                if not self.release[title].wait(5):
                    raise AssertionError("Test did not release its synthetic request")
                request = httpx.Request("POST", url)
                if title in failures:
                    return httpx.Response(529, request=request, json={"error":"Synthetic overload"})
                score = 0.5 + int(title.split()[1]) / 100
                return httpx.Response(200, request=request, json={
                    "model":"synthetic-model", "answers":{key:{"type":"noul","noul":score} for key in json["questions"]},
                    "usage":{"input_tokens":100,"output_tokens":2},
                })
            finally:
                with lock:
                    self.in_flight -= 1

        with patch("research_tagger.jobs.httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = post
            try:
                yield
            finally:
                self.db.execute("UPDATE jobs SET cancel_requested=1 WHERE status IN ('queued','running')")
                for event in self.release.values():
                    event.set()
                self.app.state.service.executor.submit(lambda: None).result(timeout=5)

    def test_setting_defaults_validation_persistence_and_unchanged_estimate(self):
        self.assertEqual(self.config["parallel_requests"], 4)
        before = self.client.post(f"/api/conferences/{self.conference_id}/tag-estimate", json={"domain_ids":self.ids}).json()
        response = self.client.put("/api/settings", json={**self.config,"parallel_requests":8})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.db.settings().parallel_requests, 8)
        after = self.client.post(f"/api/conferences/{self.conference_id}/tag-estimate", json={"domain_ids":self.ids}).json()
        self.assertEqual(before["estimate_signature"], after["estimate_signature"])
        self.assertEqual(before["estimated_cost"], after["estimated_cost"])
        for invalid in (0, 17, -1, 1.5, True, "4"):
            self.assertEqual(self.client.put("/api/settings", json={**self.config,"parallel_requests":invalid}).status_code, 422)
        legacy = {key:value for key,value in self.config.items() if key != "parallel_requests"}
        self.db.execute("UPDATE settings SET value=? WHERE key='tagging'", (json.dumps(legacy),))
        self.assertEqual(self.db.settings().parallel_requests, 4)

    def test_bound_out_of_order_persistence_and_cancellation_drain(self):
        with self.controlled_requests():
            job_id = self.start()
            initial = [self.entered.get(timeout=5) for _ in range(4)]
            self.assertEqual(self.peak, 4)
            self.assertEqual(len(self.calls), 4)
            self.release[initial[2]].set()
            fifth = self.entered.get(timeout=5)
            self.assertEqual(self.job(job_id)["completed"], 1)
            result = self.db.rows("SELECT p.title, t.probabilities FROM tag_results t JOIN papers p ON p.id=t.paper_id")[0]
            self.assertEqual(result["title"], initial[2])
            self.assertAlmostEqual(next(iter(json.loads(result["probabilities"]).values())), 0.5 + int(initial[2].split()[1]) / 100)
            self.client.post(f"/api/jobs/{job_id}/cancel", json={}).raise_for_status()
            for title in initial + [fifth]:
                self.release[title].set()
            self.wait_until(lambda: self.job(job_id)["status"] == "cancelled")
            self.assertEqual(len(self.calls), 5)
            self.assertEqual(self.peak, 4)
            self.assertEqual(self.job(job_id)["completed"], 5)
            self.assertEqual(len(self.db.rows("SELECT id FROM tag_results")), 5)

    def test_five_completion_errors_stop_launching_but_save_in_flight_success(self):
        self.client.put("/api/settings", json={**self.config,"parallel_requests":3}).raise_for_status()
        # IDs are collected in the database's paper order, so title matching is explicit.
        ordered = [paper["title"] for paper in self.db.papers(self.conference_id)]
        with self.controlled_requests(failures=set(ordered[:5])):
            job_id = self.start()
            launched = [self.entered.get(timeout=5) for _ in range(3)]
            for index in range(5):
                self.release[ordered[index]].set()
                self.wait_until(lambda: self.job(job_id)["failed"] == index + 1)
                if index < 4:
                    launched.append(self.entered.get(timeout=5))
            self.assertEqual(len(self.calls), 7)
            for title in launched:
                self.release[title].set()
            self.wait_until(lambda: self.job(job_id)["status"] == "failed")
            result = self.job(job_id)
            self.assertEqual((result["completed"], result["failed"]), (2, 5))
            self.assertIn("5 consecutive errors", result["error"])
            self.assertEqual(len(self.calls), 7, "Drained successes must not restart submission")
            self.assertEqual(sum(bool(p["tag_error"]) for p in self.db.papers(self.conference_id)), 5)

        with self.controlled_requests():
            for event in self.release.values():
                event.set()
            retry_id = self.start(retry=True)
            self.wait_until(lambda: self.job(retry_id)["status"] == "completed")
            self.assertEqual(set(self.calls), set(ordered[:5]))
            self.assertEqual(len(self.db.rows("SELECT id FROM tag_results")), 7)
            self.assertFalse(any(p["tag_error"] for p in self.db.papers(self.conference_id)))

    def test_full_run_saves_every_paper_once(self):
        with self.controlled_requests():
            for event in self.release.values():
                event.set()
            job_id = self.start()
            self.wait_until(lambda: self.job(job_id)["status"] == "completed")
            self.assertEqual(len(self.calls), 12)
            self.assertEqual(len(set(self.calls)), 12)
            self.assertLessEqual(self.peak, 4)
            self.assertEqual((self.job(job_id)["completed"], self.job(job_id)["failed"]), (12, 0))
            self.assertEqual(len(self.db.rows("SELECT id FROM tag_results")), 12)
