"""Deployment admission. No Drive, Sheets, OpenAI, or live containers."""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import app.main as main
from app.deployment_admission import (
    SCHEMA,
    DeploymentHoldError,
    acquire,
    apply_boot_hold,
    enter_admission,
    leave_admission,
    release,
)
from app.orchestrator import Orchestrator


def orchestrator():
    return Orchestrator(mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock())


class AdmissionTests(unittest.TestCase):
    def test_idle_hold_blocks_start_without_a_worker(self):
        orch = orchestrator()
        body = acquire(orch)
        self.assertEqual(body["schema"], SCHEMA)
        self.assertEqual(body["state"], "idle")
        self.assertEqual(body["hold"], "held")
        self.assertEqual(body["active_jobs"], [])
        with mock.patch.object(orch, "run") as run:
            with self.assertRaises(DeploymentHoldError):
                orch.start("HIT-1")
            run.assert_not_called()
        self.assertEqual(orch.threads, {})
        orch.sheets.rows.assert_not_called()

    def test_live_worker_is_active_and_does_not_take_the_hold(self):
        orch = orchestrator()
        started = threading.Event()
        finished = threading.Event()

        def worker():
            started.set()
            finished.wait(5)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(started.wait(2))
        orch.threads["HIT-9"] = thread
        body = acquire(orch)
        finished.set()
        thread.join(2)
        self.assertEqual(body["state"], "active")
        self.assertEqual(body["hold"], "open")
        self.assertEqual(body["active_jobs"], ["HIT-9"])
        self.assertFalse(orch._deploy_hold)

    def test_admission_in_progress_fails_closed(self):
        orch = orchestrator()
        enter_admission(orch)
        body = acquire(orch)
        self.assertEqual(body["state"], "active")
        self.assertTrue(body["admitting"])
        self.assertFalse(orch._deploy_hold)
        leave_admission(orch)
        self.assertEqual(acquire(orch)["hold"], "held")

    def test_resume_and_retry_do_not_touch_sheets_while_held(self):
        orch = orchestrator()
        acquire(orch)
        with self.assertRaises(DeploymentHoldError):
            orch.resume("HIT-1")
        with self.assertRaises(DeploymentHoldError):
            orch.retry("HIT-1-B0001")
        orch.sheets.rows.assert_not_called()
        orch.sheets.write.assert_not_called()

    def test_release_refuses_to_clear_a_live_worker(self):
        orch = orchestrator()
        acquire(orch)
        started = threading.Event()
        finished = threading.Event()

        def worker():
            started.set()
            finished.wait(5)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.assertTrue(started.wait(2))
        orch.threads["HIT-2"] = thread
        # The hold was taken before the thread existed; release must not open admission.
        orch._deploy_hold = True
        body = release(orch)
        finished.set()
        thread.join(2)
        self.assertEqual(body["state"], "active")
        self.assertTrue(orch._deploy_hold)

    def test_release_clears_an_idle_hold_without_starting_work(self):
        orch = orchestrator()
        acquire(orch)
        body = release(orch)
        self.assertEqual(body["state"], "idle")
        self.assertEqual(body["hold"], "open")
        self.assertFalse(orch._deploy_hold)
        orch.sheets.rows.assert_not_called()

    def test_boot_hold_is_opt_in_and_a_restart_does_not_reapply_it(self):
        with tempfile.TemporaryDirectory() as directory:
            stamp = str(Path(directory) / "consumed")
            held_env = {
                "HISTORYIT_BOOT_DEPLOY_HOLD": "1",
                "HISTORYIT_BOOT_HOLD_STAMP": stamp,
            }
            with mock.patch.dict(os.environ, held_env, clear=False):
                first = orchestrator()
                apply_boot_hold(first)
                self.assertTrue(first._deploy_hold)
                self.assertTrue(Path(stamp).is_file())
                with self.assertRaises(DeploymentHoldError):
                    first.start("HIT-1")
                released = release(first)
                self.assertEqual(released["hold"], "open")
                restarted = orchestrator()
                apply_boot_hold(restarted)
                self.assertFalse(restarted._deploy_hold)
                enter_admission(restarted)
                leave_admission(restarted)
                Path(stamp).unlink()
                recreated = orchestrator()
                apply_boot_hold(recreated)
                self.assertTrue(recreated._deploy_hold)
            plain = orchestrator()
            with mock.patch.dict(os.environ, {"HISTORYIT_BOOT_HOLD_STAMP": stamp}, clear=False):
                os.environ.pop("HISTORYIT_BOOT_DEPLOY_HOLD", None)
                apply_boot_hold(plain)
            self.assertFalse(plain._deploy_hold)


class AdmissionRouteTests(unittest.TestCase):
    def test_health_does_not_report_workers(self):
        client = TestClient(main.app)
        body = client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertNotIn("active_jobs", body)
        self.assertNotIn("hold", body)
        self.assertNotIn("admitting", body)

    def test_public_peer_cannot_see_the_route(self):
        client = TestClient(main.app)
        self.assertEqual(client.get("/internal/deployment-admission").status_code, 404)
        self.assertEqual(client.post("/internal/deployment-admission/hold").status_code, 404)

    def test_loopback_hold_and_release(self):
        client = TestClient(main.app, client=("127.0.0.1", 9))
        orch = orchestrator()
        with mock.patch.object(main, "live", return_value=orch):
            held = client.post("/internal/deployment-admission/hold")
            status = client.get("/internal/deployment-admission")
            opened = client.post("/internal/deployment-admission/release")
        self.assertEqual(held.status_code, 200)
        self.assertEqual(held.json()["hold"], "held")
        self.assertEqual(status.json()["hold"], "held")
        self.assertEqual(opened.json()["hold"], "open")
        self.assertEqual(opened.json()["schema"], SCHEMA)
