"""Host deployment decisions, with mocked git, docker, curl, and GitHub."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import sys

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SCRIPTS))

import deploy_release as deploy_mod  # noqa: E402
from deploy_release import (  # noqa: E402
    FIRST_INSTALL,
    UNGUARDED_REPORT,
    accept_staging_auto,
    REPO as EXPECT_REPO,
    SCHEMA,
    Completed,
    DeployConfig,
    DeployError,
    PROFILES,
    acquire_deploy_lock,
    classify_admission,
    classify_release,
    deploy,
    environment_digest,
    release_hold,
    rollback,
    superseded,
    wait_for_ci,
)

PREV = "a" * 40
TARGET = "b" * 40
API_ID = "sha256:" + "1" * 64
WEB_ID = "sha256:" + "2" * 64
COMPOSE_TEXT = """
services:
  api:
    image: historyit-api:prod
  web:
    image: historyit-web:prod
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/nginx/https.conf:/etc/nginx/conf.d/default.conf:ro
      - certbot_certs:/etc/letsencrypt:ro
volumes:
  certbot_certs:
"""
HTTPS_TEXT = """
server {
    listen 443 ssl;
    ssl_certificate     /etc/letsencrypt/live/historyit/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/historyit/privkey.pem;
    location = /health { proxy_pass http://historyit_api; }
    location /api/ { proxy_pass http://historyit_api; }
}
"""
ENV_TEXT = "\n".join(
    [
        "JOB_INDEX_SPREADSHEET_ID=sheet-staging",
        "GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/google-service-account.json",
        "APP_LOGIN_USERNAME=demo",
        "APP_LOGIN_PASSWORD_HASH=$argon2id$example",
        "OPENAI_API_KEY=sk-test-secret",
        "",
    ]
)


def idle_hold() -> str:
    return json.dumps(
        {
            "schema": SCHEMA,
            "state": "idle",
            "hold": "held",
            "active_jobs": [],
            "admitting": False,
        }
    )


class FakeIO:
    def __init__(self, environment="staging"):
        self.profile = PROFILES[environment]
        self.hostname_value = self.profile.hostname
        self.branch = self.profile.branch
        self.head = PREV
        self.tip = TARGET
        self.porcelain = ""
        self.candidate_has_guard = True
        self.running_has_guard = True
        self.keep_image_has_guard = True
        self.admission = "idle"
        self.fail_docker_info = False
        self.health_codes: list[int] = []
        self.runs: list[dict] = []
        self.run_lists: list[list[dict]] | None = None
        self.files: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.worktree_added = False
        self.built = False
        self.tags: dict[str, str] = {}
        self.api_cid = "a" * 12
        self.web_cid = "b" * 12
        self.image_ids = {self.api_cid: API_ID, self.web_cid: WEB_ID}
        self.api_image = "historyit-api:prod"
        self.web_image = "historyit-web:prod"
        self.volumes = [
            f"{self.profile.project}_certbot_certs",
            f"{self.profile.project}_certbot_www",
        ]
        self.holds = 0
        self.releases = 0

    def hostname(self) -> str:
        return self.hostname_value

    def is_dir(self, path: str) -> bool:
        if path == self.profile.checkout:
            return True
        if path.endswith(("/server/credentials", "/server/governing", "/server/logs")):
            return True
        if path == self.profile.build_root:
            return True
        return False

    def exists(self, path: str) -> bool:
        if path in self.files:
            return True
        if path.endswith("deployment_admission.py"):
            return self.candidate_has_guard
        if path.endswith((".env", "https.conf", "renew-certs.sh", "docker-compose.prod.yml")):
            return True
        if path == f"{self.profile.build_root}/{TARGET}":
            return self.worktree_added
        return False

    def read(self, path: str) -> str:
        if path in self.files:
            return self.files[path]
        if path.endswith(".env"):
            return ENV_TEXT
        if path.endswith("docker-compose.prod.yml"):
            return COMPOSE_TEXT
        if path.endswith("https.conf"):
            return HTTPS_TEXT
        raise FileNotFoundError(path)

    def write(self, path: str, data: str) -> None:
        self.files[path] = data

    def ensure_dir(self, path: str) -> None:
        return None

    def sleep(self, seconds: float) -> None:
        return None

    def acquire_lock(self, path: str) -> None:
        self.calls.append(["lock", path])

    def release_lock(self, path: str) -> None:
        self.calls.append(["unlock", path])

    def github_runs(self, workflow: str, branch: str, token: str) -> list[dict]:
        self.calls.append(["github-api", workflow, branch])
        if token != "test-token":
            raise DeployError("unexpected token")
        if self.run_lists is not None:
            if self.run_lists:
                return self.run_lists.pop(0)
            return []
        return list(self.runs)

    def run(self, args: list[str], timeout: int = 120) -> Completed:
        self.calls.append(list(args))
        deploy_mod.assert_safe_command(args)
        if args[:3] == ["sudo", "-n", "docker"]:
            args = args[2:]
        if args == ["docker", "info"]:
            if self.fail_docker_info:
                return Completed(1, "", "permission denied")
            return Completed(0, "", "")
        if args[0] == "git":
            return self._git(args)
        if args[0] == "docker":
            return self._docker(args)
        if args[0] == "curl":
            return self._curl(args)
        return Completed(1, "", "unexpected command")

    def _git(self, args: list[str]) -> Completed:
        if args[1] != "-C":
            return Completed(1, "", "git")
        command = args[3:]
        if command == ["status", "--porcelain"]:
            return Completed(0, self.porcelain, "")
        if command == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return Completed(0, self.branch + "\n", "")
        if command == ["rev-parse", "HEAD"]:
            return Completed(0, self.head + "\n", "")
        if command[:1] == ["rev-parse"] and command[1].startswith("refs/remotes/origin/"):
            return Completed(0, self.tip + "\n", "")
        if command[:1] == ["fetch"]:
            return Completed(0, "", "")
        if command[:2] == ["worktree", "add"]:
            self.worktree_added = True
            return Completed(0, "", "")
        if command[:2] == ["merge", "--ff-only"]:
            if command[2] != self.tip:
                return Completed(1, "", "not fast-forward")
            self.head = command[2]
            return Completed(0, "ok\n", "")
        if command[:2] == ["switch", "-C"]:
            self.branch = command[2]
            self.head = command[3]
            return Completed(0, "", "")
        return Completed(1, "", "unexpected git")

    def _docker(self, args: list[str]) -> Completed:
        if args[1:] == ["info"]:
            return Completed(0, "", "")
        if args[1] == "compose":
            return self._compose(args)
        if args[1] == "inspect":
            fmt = args[args.index("--format") + 1]
            container = args[-1]
            if fmt == "{{.Image}}":
                return Completed(0, self.image_ids[container] + "\n", "")
            if fmt == "{{.State.Health.Status}}":
                return Completed(0, "healthy\n", "")
            if fmt == "{{.Config.Image}}":
                image = self.api_image if container == self.api_cid else self.web_image
                return Completed(0, image + "\n", "")
        if args[1:3] == ["image", "inspect"]:
            tag = args[-1]
            if tag in self.tags:
                return Completed(0, self.tags[tag] + "\n", "")
            if self.built and ":cand-" in tag:
                ident = "3" if tag.startswith("historyit-api:") else "4"
                return Completed(0, "sha256:" + ident * 64 + "\n", "")
            return Completed(1, "", "missing image")
        if args[1] == "tag":
            self.tags[args[3]] = args[2]
            return Completed(0, "", "")
        if args[1:3] == ["volume", "ls"]:
            return Completed(0, "\n".join(self.volumes) + "\n", "")
        if args[1] == "exec":
            return self._exec(args[-1])
        return Completed(1, "", "unexpected docker")

    def _compose(self, args: list[str]) -> Completed:
        if "build" in args:
            self.built = True
            return Completed(0, "", "")
        if "up" in args:
            files = [args[index + 1] for index, value in enumerate(args) if value == "-f"]
            images = re.findall(r"image:\s*(\S+)", self.read(files[-1]))
            self.api_image, self.web_image = images[0], images[1]
            if ":keep-" in self.api_image:
                self.running_has_guard = self.keep_image_has_guard
            else:
                self.running_has_guard = True
            return Completed(0, "", "")
        if "ps" in args and "-q" in args:
            cid = self.api_cid if args[-1] == "api" else self.web_cid
            return Completed(0, cid + "\n", "")
        return Completed(1, "", "unexpected compose")

    def _exec(self, script: str) -> Completed:
        if "deployment-admission/release" in script:
            self.releases += 1
            if not self.running_has_guard:
                return Completed(0, "HTTP 404\nmissing\n", "")
            body = {
                "schema": SCHEMA,
                "state": "idle",
                "hold": "open",
                "active_jobs": [],
                "admitting": False,
            }
            return Completed(0, "HTTP 200\n" + json.dumps(body) + "\n", "")
        if "deployment-admission/hold" in script:
            self.holds += 1
            if self.admission == "offline":
                return Completed(1, "", "connection refused")
            if not self.running_has_guard:
                return Completed(0, "HTTP 404\nmissing\n", "")
            if self.admission == "active":
                body = {
                    "schema": SCHEMA,
                    "state": "active",
                    "hold": "open",
                    "active_jobs": ["HIT-1"],
                    "admitting": False,
                }
                return Completed(0, "HTTP 200\n" + json.dumps(body) + "\n", "")
            if self.admission == "unknown":
                return Completed(0, "HTTP 500\nnope\n", "")
            if self.admission == "html":
                return Completed(0, "HTTP 200\n<html></html>\n", "")
            return Completed(0, "HTTP 200\n" + idle_hold() + "\n", "")
        return Completed(1, "", "unexpected exec")

    def _curl(self, args: list[str]) -> Completed:
        url = args[-1]
        if url.endswith("/health"):
            code = self.health_codes.pop(0) if self.health_codes else 200
            body = json.dumps({"status": "ok", "login_configured": True, "service": "historyit-archive-mvp"})
            return Completed(0, f"{body}\n{code}", "")
        if url.endswith("/api/jobs/HIT-DEPLOY-PROBE"):
            return Completed(0, "\n401", "")
        return Completed(0, "\n200", "")


def config(environment="staging", **overrides) -> DeployConfig:
    data = dict(
        environment=environment,
        sha=TARGET,
        repository=EXPECT_REPO,
        run_id="10",
        token="test-token",
        first_install=None,
        wait_attempts=1,
        pause_seconds=0,
    )
    data.update(overrides)
    return DeployConfig(**data)


def compose_actions(calls: list[list[str]]) -> list[str]:
    found = []
    for args in calls:
        if "compose" not in args:
            continue
        if "up" in args:
            found.append("up")
        if "build" in args:
            found.append("build")
    return found


class DecisionTests(unittest.TestCase):
    def test_admission_classifier_fails_closed(self):
        self.assertEqual(classify_admission(200, idle_hold()), "proceed")
        self.assertEqual(
            classify_admission(
                200,
                json.dumps({"schema": SCHEMA, "state": "active", "hold": "held", "active_jobs": ["HIT-1"], "admitting": False}),
            ),
            "active",
        )
        self.assertEqual(classify_admission(404, "missing"), "absent")
        self.assertEqual(classify_admission(500, idle_hold()), "unknown")
        self.assertEqual(classify_admission(200, "<html></html>"), "unknown")
        self.assertEqual(
            classify_admission(
                200,
                json.dumps({"schema": SCHEMA, "state": "idle", "hold": "open", "active_jobs": [], "admitting": False}),
            ),
            "unknown",
        )
        self.assertEqual(classify_release(200, json.dumps({"schema": SCHEMA, "state": "idle", "hold": "open", "active_jobs": []})), "open")

    def test_superseded_run_is_the_newer_in_flight_id(self):
        runs = [
            {"id": 10, "status": "in_progress"},
            {"id": 9, "status": "queued"},
            {"id": 11, "status": "completed"},
            {"id": 12, "status": "queued"},
        ]
        self.assertEqual(superseded(runs, "10"), 12)
        self.assertIsNone(superseded([{"id": 4, "status": "queued"}], "10"))

    def test_environment_digest_ignores_unrelated_keys_and_hides_values(self):
        first = environment_digest(ENV_TEXT + "UNRELATED=secret\n")
        second = environment_digest(ENV_TEXT.replace("UNRELATED=secret\n", "") + "OTHER=1\n")
        self.assertEqual(first, second)
        try:
            environment_digest("JOB_INDEX_SPREADSHEET_ID=\n")
        except DeployError as exc:
            self.assertNotIn("sk-test-secret", str(exc))
        else:
            self.fail("missing keys should fail")

    def test_wait_for_ci_requires_the_newest_completed_success(self):
        calls = {"n": 0}

        def list_runs():
            calls["n"] += 1
            if calls["n"] == 1:
                return []
            return [
                {"id": 1, "status": "completed", "conclusion": "failure", "head_sha": TARGET},
                {"id": 2, "status": "completed", "conclusion": "success", "head_sha": TARGET},
            ]

        wait_for_ci(sha=TARGET, repository=EXPECT_REPO, list_runs=list_runs, sleep=lambda _seconds: None, attempts=3, pause_seconds=0)
        with self.assertRaises(DeployError):
            wait_for_ci(
                sha=TARGET,
                repository=EXPECT_REPO,
                list_runs=lambda: [{"id": 3, "status": "completed", "conclusion": "failure", "head_sha": TARGET}],
                sleep=lambda _seconds: None,
                attempts=1,
                pause_seconds=0,
            )


class DeployFlowTests(unittest.TestCase):
    def deploy(self, io=None, **overrides):
        host = io or FakeIO()
        deploy(host, config(**overrides))
        return host

    def test_staging_deploy_uses_unique_tags_and_releases_the_hold(self):
        host = self.deploy()
        self.assertEqual(compose_actions(host.calls), ["build", "up"])
        self.assertEqual(host.holds, 2)
        self.assertEqual(host.releases, 1)
        self.assertIn("lock", [call[0] for call in host.calls])
        self.assertIn("unlock", [call[0] for call in host.calls])
        projects = []
        env_files = []
        for args in host.calls:
            if "-p" in args:
                projects.append(args[args.index("-p") + 1])
            if "--env-file" in args:
                env_files.append(args[args.index("--env-file") + 1])
            self.assertNotIn("-k", args)
            self.assertNotIn("--insecure", args)
            self.assertNotIn("test-token", args)
        self.assertEqual(set(projects), {"historyit-staging", "historyit-staging-candidate"})
        self.assertEqual(env_files, ["/home/ubuntu/history-it-staging/.env"])
        self.assertTrue(any(path.endswith("deployed-sha") and host.files[path] == TARGET + "\n" for path in host.files))
        self.assertTrue(any(path.endswith("compose.candidate.yml") and "HISTORYIT_BOOT_DEPLOY_HOLD" in host.files[path] for path in host.files))
        self.assertNotIn("/home/ubuntu/history-it-staging/.env", host.files)
        joined = " ".join(" ".join(call) for call in host.calls)
        self.assertNotIn("/home/ubuntu/history-it/", joined)
        self.assertNotIn("actions-runner", joined)

    def test_production_profile_rejects_the_staging_host_before_git(self):
        host = FakeIO("staging")
        with self.assertRaises(DeployError) as caught:
            deploy(host, config(environment="production"))
        self.assertIn("cannot run the production", str(caught.exception))
        self.assertFalse(any(call[0] == "git" for call in host.calls))

    def test_staging_profile_rejects_the_production_host(self):
        host = FakeIO("staging")
        host.hostname_value = PROFILES["production"].hostname
        with self.assertRaises(DeployError):
            deploy(host, config())
        self.assertEqual(compose_actions(host.calls), [])

    def test_wrong_branch_is_rejected(self):
        host = FakeIO()
        host.branch = "master"
        with self.assertRaises(DeployError):
            deploy(host, config())
        self.assertEqual(compose_actions(host.calls), [])

    def test_dirty_checkout_is_rejected(self):
        host = FakeIO()
        host.porcelain = " M server/app/main.py\n"
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertIn("uncommitted", str(caught.exception))
        self.assertEqual(compose_actions(host.calls), [])

    def test_sha_that_is_not_the_branch_tip_is_rejected(self):
        host = FakeIO()
        host.tip = "c" * 40
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(compose_actions(host.calls), [])

    def test_newer_queued_run_is_rejected_after_build_and_before_activation(self):
        host = FakeIO()
        host.run_lists = [
            [],
            [],
            [{"id": 20, "status": "queued", "head_sha": "d" * 40}],
        ]
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 3)
        self.assertEqual(compose_actions(host.calls), ["build"])
        self.assertEqual(host.holds, 0)

    def test_active_worker_does_not_replace_containers(self):
        host = FakeIO()
        host.admission = "active"
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(compose_actions(host.calls), ["build"])
        self.assertEqual(host.holds, 1)
        self.assertEqual(host.releases, 0)

    def test_unknown_guard_does_not_replace_containers(self):
        host = FakeIO()
        host.admission = "html"
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 4)
        self.assertNotIn("up", compose_actions(host.calls))

    def test_absent_guard_is_not_bypassed_by_actions(self):
        host = FakeIO()
        host.running_has_guard = False
        with self.assertRaises(DeployError) as caught:
            deploy(host, config(first_install=FIRST_INSTALL, run_id="10"))
        self.assertIn("GitHub Actions cannot", str(caught.exception))
        self.assertEqual(compose_actions(host.calls), [])

    def test_manual_first_install_builds_then_confirms_the_new_guard(self):
        host = FakeIO()
        host.running_has_guard = False
        deploy(host, config(run_id=None, token=None, first_install=FIRST_INSTALL))
        self.assertEqual(compose_actions(host.calls), ["build", "up"])
        self.assertGreaterEqual(host.holds, 2)
        self.assertEqual(host.releases, 1)

    def test_candidate_without_the_guard_never_activates(self):
        host = FakeIO()
        host.candidate_has_guard = False
        with self.assertRaises(DeployError) as caught:
            deploy(host, config(run_id=None, token=None, first_install=FIRST_INSTALL))
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(compose_actions(host.calls), [])

    def test_first_install_does_not_bypass_active_or_unexplained_responses(self):
        for admission in ("active", "unknown", "offline", "html"):
            host = FakeIO()
            host.admission = admission
            with self.assertRaises(DeployError) as caught:
                deploy(host, config(run_id=None, token=None, first_install=FIRST_INSTALL))
            self.assertEqual(caught.exception.code, 4, admission)
            self.assertNotIn("up", compose_actions(host.calls))
            self.assertNotIn("Continuing only because", str(caught.exception))

    def test_rollback_to_pre_guard_release_reports_the_missing_route(self):
        host = FakeIO()
        host.keep_image_has_guard = False
        host.files[f"{host.profile.state_dir}/release.json"] = json.dumps(
            {
                "environment": "staging",
                "previous_sha": PREV,
                "previous_api_image": f"historyit-api:keep-{PREV}",
                "previous_web_image": f"historyit-web:keep-{PREV}",
                "previous_api_id": API_ID,
                "previous_web_id": WEB_ID,
            }
        )
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stderr(buffer):
            rollback(host, config(sha=""))
        text = buffer.getvalue()
        self.assertIn(UNGUARDED_REPORT, text)
        self.assertIn("guarded automatic deployment is not available", text)
        self.assertNotIn("admission guard is installed", text)
        self.assertNotIn("guard is active", text)
        self.assertIn("up", compose_actions(host.calls))
        self.assertFalse(host.running_has_guard)

    def test_failed_rollback_leaves_the_recorded_release_state(self):
        host = FakeIO()
        host.health_codes = [500, 500]
        host.keep_image_has_guard = False
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("rollback failed", str(caught.exception))
        self.assertIn("Recover with:", str(caught.exception))
        self.assertIn("release.json", str(caught.exception))
        state = json.loads(host.files[f"{host.profile.state_dir}/release.json"])
        self.assertEqual(state["activation"], "pending")
        self.assertEqual(state["previous_sha"], PREV)
        self.assertEqual(state["previous_api_image"], f"historyit-api:keep-{PREV}")
        self.assertEqual(state["previous_web_image"], f"historyit-web:keep-{PREV}")

    def test_smoke_failure_restores_the_keep_tags(self):
        host = FakeIO()
        host.health_codes = [500]
        host.keep_image_has_guard = False
        with self.assertRaises(DeployError) as caught:
            deploy(host, config())
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("previous release restored", str(caught.exception))
        self.assertIn(UNGUARDED_REPORT, str(caught.exception))
        self.assertEqual(compose_actions(host.calls), ["build", "up", "up"])
        joined = " ".join(" ".join(call) for call in host.calls)
        self.assertIn(f"historyit-api:keep-{PREV}", joined)
        self.assertIn("switch", joined)
        self.assertNotIn("reset", joined)
        self.assertNotIn("--hard", joined)

    def test_sudo_docker_is_used_when_the_daemon_is_not_directly_available(self):
        host = FakeIO()
        host.fail_docker_info = True
        deploy(host, config())
        docker_calls = [call for call in host.calls if call and call[0] in {"docker", "sudo"} and "info" not in call]
        self.assertTrue(docker_calls)
        self.assertTrue(all(call[:3] == ["sudo", "-n", "docker"] for call in docker_calls))

    def test_operator_rollback_refuses_an_active_worker(self):
        host = FakeIO()
        host.admission = "active"
        host.files[f"{host.profile.state_dir}/release.json"] = json.dumps(
            {
                "environment": "staging",
                "previous_sha": PREV,
                "previous_api_image": f"historyit-api:keep-{PREV}",
                "previous_web_image": f"historyit-web:keep-{PREV}",
                "previous_api_id": API_ID,
                "previous_web_id": WEB_ID,
            }
        )
        with self.assertRaises(DeployError) as caught:
            rollback(host, config(sha=""))
        self.assertEqual(caught.exception.code, 4)
        self.assertEqual(compose_actions(host.calls), [])

    def test_release_hold_does_not_replace_containers(self):
        host = FakeIO()
        release_hold(host, config(sha=""))
        self.assertEqual(compose_actions(host.calls), [])
        self.assertEqual(host.releases, 1)


class StagingTrustTests(unittest.TestCase):
    def accept(self, **overrides):
        values = dict(
            repository=EXPECT_REPO,
            autodeploy="true",
            ci_event="push",
            conclusion="success",
            branch="staging",
            sha=TARGET,
            head_repository=EXPECT_REPO,
            head_fork="false",
        )
        values.update(overrides)
        return accept_staging_auto(**values)

    def test_accepts_only_this_repository_successful_staging_push(self):
        self.assertEqual(self.accept(), TARGET)

    def test_rejects_untrusted_or_disabled_runs(self):
        rejected = (
            {"autodeploy": None},
            {"autodeploy": ""},
            {"autodeploy": "false"},
            {"autodeploy": "TRUE"},
            {"ci_event": "pull_request"},
            {"ci_event": "workflow_dispatch"},
            {"conclusion": "failure"},
            {"conclusion": "cancelled"},
            {"branch": "master"},
            {"sha": "abc"},
            {"sha": ""},
            {"head_repository": "other/history-it"},
            {"head_fork": "true"},
            {"head_fork": ""},
            {"repository": "other/history-it"},
        )
        for item in rejected:
            with self.assertRaises(DeployError, msg=str(item)):
                self.accept(**item)


class LockTests(unittest.TestCase):
    def test_lock_waits_out_a_live_owner_and_recovers_a_dead_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "deploy.lock")
            acquire_deploy_lock(path, owner=100, pid_alive=lambda _pid: False, sleep=lambda _seconds: None, attempts=2)
            self.assertEqual((Path(path) / "pid").read_text(encoding="utf-8"), "100")
            with self.assertRaises(DeployError):
                acquire_deploy_lock(path, owner=200, pid_alive=lambda _pid: True, sleep=lambda _seconds: None, attempts=2)
            self.assertTrue(Path(path).is_dir())
            acquire_deploy_lock(path, owner=300, pid_alive=lambda _pid: False, sleep=lambda _seconds: None, attempts=2)
            self.assertEqual((Path(path) / "pid").read_text(encoding="utf-8"), "300")
            (Path(path) / "pid").write_text("nope", encoding="utf-8")
            with self.assertRaises(DeployError):
                acquire_deploy_lock(path, owner=400, pid_alive=lambda _pid: False, sleep=lambda _seconds: None, attempts=2)
            self.assertTrue((Path(path) / "pid").exists())


class WorkflowContractTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_third_party_actions_are_pinned(self):
        uses = re.compile(r"uses:\s*(\S+)")
        allowed = re.compile(r"actions/(checkout|setup-python|setup-node)@[0-9a-f]{40}")
        for name in ("ci.yml", "deploy-staging.yml", "deploy-production.yml"):
            refs = uses.findall(self.read(name))
            self.assertTrue(refs)
            for ref in refs:
                self.assertRegex(ref, allowed)

    def test_ci_never_selects_a_self_hosted_runner(self):
        text = self.read("ci.yml")
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("historyit-prod", text)
        self.assertNotIn("historyit-staging", text)
        self.assertIn("pull_request:", text)
        self.assertIn("python-version: \"3.12\"", text)
        self.assertIn("node-version: \"22\"", text)

    def test_deploy_workflows_route_only_their_own_runner(self):
        staging = self.read("deploy-staging.yml")
        production = self.read("deploy-production.yml")
        staging_on = staging.split("jobs:", 1)[0]
        self.assertIn("workflow_run:", staging_on)
        self.assertIn('workflows: ["CI"]', staging_on)
        self.assertIn("branches: [staging]", staging_on)
        self.assertNotIn("\n  push:", staging_on)
        self.assertNotIn("pull_request", staging)
        self.assertIn("vars.STAGING_AUTODEPLOY_ENABLED == 'true'", staging)
        self.assertIn("github.event.workflow_run.head_sha", staging)
        self.assertIn("github.event.workflow_run.event == 'push'", staging)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", staging)
        self.assertIn("head_repository.full_name == 'musabkashif/history-it'", staging)
        self.assertIn("head_repository.fork == false", staging)
        self.assertIn("[self-hosted, linux, x64, historyit-staging]", staging)
        self.assertNotIn("historyit-prod", staging)
        manual = staging.split("manual-gate:", 1)[1].split("\n  deploy:", 1)[0]
        self.assertNotIn("STAGING_AUTODEPLOY_ENABLED", manual)
        self.assertNotIn("workflow_run", production)
        self.assertNotIn("push:", production)
        self.assertNotIn("pull_request", production)
        self.assertIn("github.ref == 'refs/heads/master'", production)
        self.assertIn("workflow_dispatch", production)
        self.assertIn("[self-hosted, linux, x64, historyit-prod]", production)
        self.assertNotIn("historyit-staging", production)
        self.assertNotIn("STAGING_AUTODEPLOY_ENABLED", production)
        for text in (staging, production):
            self.assertIn("cancel-in-progress: false", text)
            self.assertIn("actions: read", text)
            self.assertIn("contents: read", text)
            self.assertNotIn("contents: write", text)
            self.assertIn("HISTORYIT_GITHUB_RUN_ID", text)
            self.assertNotIn("HISTORYIT_ADMISSION_FIRST_INSTALL", text)
            self.assertNotIn("I_CONFIRMED_THE_HOST_IS_IDLE", text)
            run_blocks = re.findall(r"run:\s*[|>]?\n(?P<body>(?:[ \t].*\n)*)", text)
            for body in run_blocks:
                self.assertNotIn("github.event.inputs", body)
                self.assertNotIn("inputs.", body)

    def test_shell_entry_does_not_embed_host_commands(self):
        script = (SCRIPTS / "deploy-release.sh").read_text(encoding="utf-8")
        for phrase in ("reset --hard", "git clean", "system prune", "compose down", "volume rm", "docker"):
            self.assertNotIn(phrase, script)
        self.assertIn("python3", script)

    def test_runtime_guard_rejects_destructive_commands(self):
        samples = (
            ["git", "reset", "--hard", "HEAD"],
            ["git", "clean", "-fd"],
            ["git", "push", "origin", "master"],
            ["docker", "system", "prune", "-af"],
            ["docker", "compose", "down", "-v"],
            ["docker", "volume", "rm", "history-it_certbot_certs"],
            ["curl", "-k", "https://13.62.111.185/health"],
        )
        for args in samples:
            with self.assertRaises(DeployError):
                deploy_mod.assert_safe_command(args)

    def test_compose_image_names_stay_stable_for_manual_starts(self):
        text = (REPO / "docker-compose.prod.yml").read_text(encoding="utf-8")
        self.assertIn("image: historyit-api:prod", text)
        self.assertIn("image: historyit-web:prod", text)
        self.assertIn("deploy/nginx/https.conf", text)


if __name__ == "__main__":
    unittest.main()
