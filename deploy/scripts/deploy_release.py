#!/usr/bin/env python3
"""Deploy one tested HistoryIT commit onto its own host.

The script is the only activation path used by GitHub Actions. It refuses to
guess. Paths, the Compose project, and the public URL come from the
environment profile, not from workflow inputs.

GitHub Actions sets HISTORYIT_GITHUB_RUN_ID. A first install of the admission
guard cannot be done from Actions; the running process does not have the
guard yet, so an unattended run fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "musabkashif/history-it"
SCHEMA = "historyit.deployment-admission.v1"
FIRST_INSTALL = "I_CONFIRMED_THE_HOST_IS_IDLE"
IN_FLIGHT = {"queued", "in_progress", "waiting", "pending", "requested", "action_required"}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^historyit-(?:api|web):(?:cand|keep)-[0-9a-f]{40}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ENV_KEYS = (
    "JOB_INDEX_SPREADSHEET_ID",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "APP_LOGIN_USERNAME",
    "APP_LOGIN_PASSWORD_HASH",
    "OPENAI_API_KEY",
)
WORKFLOWS = {"ci.yml", "deploy-staging.yml", "deploy-production.yml"}
HOLD_PATH = "/internal/deployment-admission/hold"
RELEASE_PATH = "/internal/deployment-admission/release"

COMPOSE_MARKERS = (
    "deploy/nginx/https.conf",
    "certbot_certs:/etc/letsencrypt:ro",
    '"443:443"',
    '"80:80"',
)
NGINX_MARKERS = (
    "ssl_certificate     /etc/letsencrypt/live/historyit/fullchain.pem",
    "ssl_certificate_key /etc/letsencrypt/live/historyit/privkey.pem",
    "listen 443 ssl",
    "proxy_pass http://historyit_api",
    "location = /health",
    "location /api/",
)


class DeployError(Exception):
    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Profile:
    name: str
    hostname: str
    checkout: str
    branch: str
    project: str
    public_url: str
    workflow: str

    @property
    def state_dir(self) -> str:
        return f"/home/ubuntu/historyit-deploy-state/{self.name}"

    @property
    def build_root(self) -> str:
        return f"/home/ubuntu/historyit-release-builds/{self.name}"

    @property
    def lock_dir(self) -> str:
        return f"{self.state_dir}/deploy.lock"


@dataclass(frozen=True)
class DeployConfig:
    environment: str
    sha: str = ""
    repository: str = REPO
    run_id: str | None = None
    token: str | None = None
    first_install: str | None = None
    wait_attempts: int = 12
    pause_seconds: float = 5


PROFILES = {
    "production": Profile(
        name="production",
        hostname="ip-172-31-5-212",
        checkout="/home/ubuntu/history-it",
        branch="master",
        project="history-it",
        public_url="https://13.62.111.185",
        workflow="deploy-production.yml",
    ),
    "staging": Profile(
        name="staging",
        hostname="ip-172-31-6-193",
        checkout="/home/ubuntu/history-it-staging",
        branch="staging",
        project="historyit-staging",
        public_url="https://56.228.63.165",
        workflow="deploy-staging.yml",
    ),
}


def log(message: str) -> None:
    print(f"historyit-deploy: {message}", file=sys.stderr)


def assert_safe_command(args: list[str]) -> None:
    if not args or any(not isinstance(arg, str) for arg in args):
        raise DeployError("refusing an empty or non-textual command")
    lowered = [arg.lower() for arg in args]
    if {"--hard", "--force", "-k", "--insecure", "--privileged"} & set(lowered):
        raise DeployError("refusing a forbidden command flag")
    if "prune" in lowered:
        raise DeployError("refusing prune")
    if "git" in lowered and ({"clean", "reset", "push"} & set(lowered)):
        raise DeployError("refusing git clean, reset, or push")
    if "compose" in lowered and "down" in lowered:
        raise DeployError("refusing docker compose down")
    if "volume" in lowered and ({"rm", "remove", "prune"} & set(lowered)):
        raise DeployError("refusing volume deletion")


def environment_digest(text: str) -> str:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in ENV_KEYS:
            values[key] = value
    missing = [key for key in ENV_KEYS if not values.get(key, "").strip()]
    if missing:
        raise DeployError("host .env is missing required keys: " + ", ".join(missing))
    digest = hashlib.sha256()
    for key in ENV_KEYS:
        digest.update(key.encode())
        digest.update(b"=")
        digest.update(values[key].encode())
        digest.update(b"\n")
    return digest.hexdigest()


def normalize_image_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[0-9a-f]{64}", value):
        value = "sha256:" + value
    if not IMAGE_ID_RE.fullmatch(value):
        raise DeployError("container image id is not a sha256 digest")
    return value


def classify_admission(status: int, body: str) -> str:
    """Return proceed, active, absent, or unknown. Unknown fails closed."""
    if status == 404:
        return "absent"
    if status != 200:
        return "unknown"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return "unknown"
    jobs = data.get("active_jobs")
    if not isinstance(jobs, list) or any(not isinstance(job, str) for job in jobs):
        return "unknown"
    if data.get("state") == "active" or data.get("admitting") is True or jobs:
        return "active"
    if (
        data.get("state") == "idle"
        and data.get("hold") == "held"
        and jobs == []
        and data.get("admitting") is False
    ):
        return "proceed"
    return "unknown"


def classify_release(status: int, body: str) -> str:
    if status == 404:
        return "absent"
    if status != 200:
        return "unknown"
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return "unknown"
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return "unknown"
    if data.get("state") == "active" or data.get("admitting") is True or data.get("active_jobs"):
        return "active"
    if data.get("state") == "idle" and data.get("hold") == "open" and data.get("active_jobs") == []:
        return "open"
    return "unknown"


def superseded(runs: list[dict], run_id: str) -> int | None:
    mine = int(run_id)
    newer = None
    for run in runs:
        if run.get("status") not in IN_FLIGHT:
            continue
        other = int(run["id"])
        if other > mine and (newer is None or other > newer):
            newer = other
    return newer


def require_markers(text: str, markers: tuple[str, ...], label: str) -> None:
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise DeployError(f"{label} is missing required HTTPS configuration")


def rollback_command(profile: Profile) -> str:
    return (
        f"HISTORYIT_ENV={profile.name} python3 "
        f"{profile.checkout}/deploy/scripts/deploy_release.py rollback"
    )


def release_command(profile: Profile) -> str:
    return (
        f"HISTORYIT_ENV={profile.name} python3 "
        f"{profile.checkout}/deploy/scripts/deploy_release.py release-hold"
    )


def accept_staging_auto(
    *,
    repository: str,
    autodeploy: str | None,
    ci_event: str,
    conclusion: str,
    branch: str,
    sha: str,
    head_repository: str,
    head_fork: str,
) -> str:
    """Accept one successful push CI run from this repository's staging branch.

    Anything else, including a pull request, a fork, or a missing autodeploy
    variable, fails closed. The variable is not a credential.
    """
    if repository != REPO:
        raise DeployError("repository is not musabkashif/history-it")
    if autodeploy != "true":
        raise DeployError("STAGING_AUTODEPLOY_ENABLED is not true; automatic deployment was not started")
    if ci_event != "push":
        raise DeployError("only a push CI run can start automatic staging deployment")
    if conclusion != "success":
        raise DeployError("CI did not succeed for the staging commit")
    if branch != "staging":
        raise DeployError("CI run is not for the staging branch")
    if not SHA_RE.fullmatch(sha or ""):
        raise DeployError("CI SHA is not the 40-character tested commit")
    if head_repository != REPO or head_fork != "false":
        raise DeployError("CI run is not from this repository")
    return sha


def wait_for_ci(*, sha: str, repository: str, list_runs, sleep, attempts: int = 40, pause_seconds: float = 15) -> None:
    if repository != REPO:
        raise DeployError("repository is not musabkashif/history-it")
    if not SHA_RE.fullmatch(sha or ""):
        raise DeployError("CI SHA must be 40 hexadecimal characters")
    last = "CI run not found"
    for _ in range(attempts):
        runs = [run for run in list_runs() if run.get("head_sha") == sha]
        inflight = [run for run in runs if run.get("status") in IN_FLIGHT]
        if inflight or not runs:
            last = "CI still running" if inflight else "CI run not created yet"
            sleep(pause_seconds)
            continue
        completed = [run for run in runs if run.get("status") == "completed"]
        if not completed:
            last = "CI has no completed run"
            sleep(pause_seconds)
            continue
        newest = max(completed, key=lambda run: int(run["id"]))
        conclusion = newest.get("conclusion")
        if conclusion == "success":
            return
        raise DeployError(f"CI concluded {conclusion} for {sha}")
    raise DeployError(f"timed out waiting for CI: {last}")


def github_workflow_runs(workflow: str, *, token: str, branch: str | None = None, head_sha: str | None = None) -> list[dict]:
    if workflow not in WORKFLOWS:
        raise DeployError("unexpected workflow name")
    if not token:
        raise DeployError("GH_TOKEN is required")
    query = ["per_page=50"]
    if branch is not None:
        if branch not in {"master", "staging"}:
            raise DeployError("unexpected branch")
        query.append("branch=" + branch)
    if head_sha is not None:
        if not SHA_RE.fullmatch(head_sha):
            raise DeployError("unexpected SHA")
        query.append("head_sha=" + head_sha)
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/runs?{'&'.join(query)}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "historyit-deploy",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise DeployError(f"GitHub API returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise DeployError("GitHub API request failed") from None
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise DeployError("GitHub API response has no workflow run list")
    normalized = []
    for run in runs:
        if not isinstance(run, dict):
            raise DeployError("GitHub API returned an unexpected run")
        normalized.append(
            {
                "id": int(run["id"]),
                "status": run.get("status") or "",
                "conclusion": run.get("conclusion") or "",
                "head_sha": run.get("head_sha") or "",
            }
        )
    return normalized


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _remove_lock(path: Path) -> None:
    pid_file = path / "pid"
    if pid_file.exists():
        pid_file.unlink()
    path.rmdir()


def acquire_deploy_lock(path: str, *, owner: int, pid_alive, sleep, attempts: int) -> None:
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(attempts):
        try:
            lock.mkdir()
        except FileExistsError:
            try:
                pid = int((lock / "pid").read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                raise DeployError(f"deployment lock at {path} is unreadable; refusing to take it")
            if pid_alive(pid):
                sleep(1)
                continue
            try:
                _remove_lock(lock)
            except OSError as exc:
                raise DeployError(f"could not clear a stale deployment lock: {exc}") from exc
            continue
        (lock / "pid").write_text(str(owner), encoding="utf-8")
        return
    raise DeployError(f"deployment lock at {path} is held by a live process")


def release_deploy_lock(path: str, *, owner: int) -> None:
    lock = Path(path)
    try:
        current = int((lock / "pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    if current != owner:
        return
    try:
        _remove_lock(lock)
    except OSError as exc:
        log(f"could not remove deployment lock: {exc}")


def _probe_script(path: str) -> str:
    url = "http://127.0.0.1:8000" + path
    return "\n".join(
        [
            "import json, urllib.request, urllib.error",
            f"url = {url!r}",
            "req = urllib.request.Request(url, data=b'', method='POST')",
            "try:",
            "    resp = urllib.request.urlopen(req, timeout=10)",
            "    raw = resp.read().decode('utf-8', 'replace')",
            "    print('HTTP %s' % resp.status)",
            "    print(raw)",
            "except urllib.error.HTTPError as exc:",
            "    raw = exc.read().decode('utf-8', 'replace')",
            "    print('HTTP %s' % exc.code)",
            "    print(raw)",
            "except Exception as exc:",
            "    print('HTTP 0')",
            "    print(json.dumps({'error': type(exc).__name__}))",
            "",
        ]
    )


def parse_probe(stdout: str) -> tuple[int, str]:
    lines = stdout.splitlines()
    if not lines or not lines[0].startswith("HTTP "):
        return 0, stdout
    try:
        code = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return 0, stdout
    return code, "\n".join(lines[1:])


class RealIO:
    def hostname(self) -> str:
        return subprocess.run(["hostname"], text=True, capture_output=True, check=False).stdout.strip()

    def is_dir(self, path: str) -> bool:
        return Path(path).is_dir()

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def read(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write(self, path: str, data: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(data, encoding="utf-8")
        os.replace(temporary, target)

    def ensure_dir(self, path: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def run(self, args: list[str], timeout: int = 120) -> Completed:
        assert_safe_command(args)
        try:
            completed = subprocess.run(
                args,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "COMPOSE_ANSI": "never"},
            )
        except subprocess.TimeoutExpired as exc:
            raise DeployError(f"command timed out: {args[0]}", code=1) from exc
        return Completed(completed.returncode, completed.stdout, completed.stderr)

    def github_runs(self, workflow: str, branch: str, token: str) -> list[dict]:
        return github_workflow_runs(workflow, token=token, branch=branch)

    def acquire_lock(self, path: str) -> None:
        acquire_deploy_lock(path, owner=os.getpid(), pid_alive=_pid_alive, sleep=time.sleep, attempts=600)

    def release_lock(self, path: str) -> None:
        release_deploy_lock(path, owner=os.getpid())

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _checked(io, args: list[str], timeout: int = 120) -> str:
    assert_safe_command(args)
    result = io.run(args, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise DeployError(f"command failed ({result.returncode}): {args[0]} {args[1] if len(args) > 1 else ''}\n{detail[:2000]}", code=1)
    return result.stdout


def _line(io, args: list[str], timeout: int = 120) -> str:
    return _checked(io, args, timeout).strip()


def _compose(prefix: list[str], project: str, directory: str, files: list[str], env_file: str | None, rest: list[str]) -> list[str]:
    command = [*prefix, "compose", "-p", project, "--project-directory", directory]
    for file in files:
        command.extend(["-f", file])
    if env_file:
        command.extend(["--env-file", env_file])
    command.extend(rest)
    return command


def _one_id(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", lines[0]):
        raise DeployError("expected exactly one container id")
    return lines[0]


def _override(api_image: str, web_image: str) -> str:
    if not IMAGE_RE.fullmatch(api_image) or not IMAGE_RE.fullmatch(web_image):
        raise DeployError("refusing to write an image override with an unexpected tag")
    return (
        "services:\n"
        "  api:\n"
        f"    image: {api_image}\n"
        "    environment:\n"
        '      HISTORYIT_BOOT_DEPLOY_HOLD: "1"\n'
        "  web:\n"
        f"    image: {web_image}\n"
    )


def _docker_prefix(io) -> list[str]:
    info = io.run(["docker", "info"], timeout=60)
    if info.returncode == 0:
        return ["docker"]
    return ["sudo", "-n", "docker"]


def _probe(io, prefix: list[str], profile: Profile, path: str) -> tuple[int, str]:
    files = [posixpath.join(profile.checkout, "docker-compose.prod.yml")]
    listing = _checked(io, _compose(prefix, profile.project, profile.checkout, files, None, ["ps", "-q", "api"]))
    container = _one_id(listing)
    result = io.run([*prefix, "exec", "-i", container, "python", "-c", _probe_script(path)], timeout=30)
    if result.returncode != 0 and not result.stdout.startswith("HTTP "):
        return 0, result.stderr or result.stdout
    return parse_probe(result.stdout)


def _curl(io, config: DeployConfig, url: str) -> tuple[int, str]:
    if not url.startswith("https://"):
        raise DeployError("smoke checks must use HTTPS")
    last = "no response"
    for _ in range(config.wait_attempts):
        result = io.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--max-time",
                "15",
                "--proto",
                "=https",
                "--max-redirs",
                "0",
                "--output",
                "-",
                "--write-out",
                "\n%{http_code}",
                url,
            ],
            timeout=30,
        )
        if result.returncode != 0 or "\n" not in result.stdout:
            last = (result.stderr or "curl failed").strip()[:500]
            io.sleep(config.pause_seconds)
            continue
        body, code_text = result.stdout.rsplit("\n", 1)
        if not code_text.strip().isdigit():
            last = "curl returned no status"
            io.sleep(config.pause_seconds)
            continue
        return int(code_text.strip()), body
    raise DeployError(f"HTTPS check failed for {url}: {last}", code=1)


def _wait_http(io, config: DeployConfig, url: str, expected: int) -> str:
    last = "no response"
    body = ""
    for _ in range(config.wait_attempts):
        try:
            code, body = _curl(io, DeployConfig(
                environment=config.environment,
                sha=config.sha,
                wait_attempts=1,
                pause_seconds=0,
            ), url)
        except DeployError as exc:
            last = str(exc)
            io.sleep(config.pause_seconds)
            continue
        if code == expected:
            return body
        last = f"HTTP {code}"
        io.sleep(config.pause_seconds)
    raise DeployError(f"{url} did not return {expected}: {last}", code=1)


def _smoke(io, config: DeployConfig, profile: Profile) -> None:
    health = _wait_http(io, config, profile.public_url + "/health", 200)
    try:
        payload = json.loads(health)
    except json.JSONDecodeError as exc:
        raise DeployError("health response is not JSON", code=1) from exc
    if payload.get("login_configured") is not True:
        raise DeployError("login_configured is not true", code=1)
    _wait_http(io, config, profile.public_url + "/", 200)
    _wait_http(io, config, profile.public_url + "/api/jobs/HIT-DEPLOY-PROBE", 401)


def _preserved(io, profile: Profile) -> None:
    root = profile.checkout
    required_dirs = ("server/credentials", "server/governing", "server/logs")
    for relative in required_dirs:
        if not io.is_dir(posixpath.join(root, relative)):
            raise DeployError(f"host directory missing: {relative}")
    for relative in (".env", "deploy/nginx/https.conf", "deploy/scripts/renew-certs.sh"):
        if not io.exists(posixpath.join(root, relative)):
            raise DeployError(f"host file missing: {relative}")


def _volumes(io, prefix: list[str], profile: Profile) -> None:
    text = _checked(io, [*prefix, "volume", "ls", "--format", "{{.Name}}"])
    names = {line.strip() for line in text.splitlines() if line.strip()}
    required = {f"{profile.project}_certbot_certs", f"{profile.project}_certbot_www"}
    if not required <= names:
        raise DeployError("certificate volumes are not present; refusing to recreate them")


def _wait_image(io, config: DeployConfig, prefix: list[str], profile: Profile, service: str, image: str) -> None:
    files = [posixpath.join(profile.checkout, "docker-compose.prod.yml")]
    last = "missing"
    for _ in range(config.wait_attempts):
        listing = _checked(io, _compose(prefix, profile.project, profile.checkout, files, None, ["ps", "-q", service]))
        container = _one_id(listing)
        health = io.run([*prefix, "inspect", "--format", "{{.State.Health.Status}}", container], timeout=30)
        running = io.run([*prefix, "inspect", "--format", "{{.Config.Image}}", container], timeout=30)
        last = health.stdout.strip() or health.stderr.strip()
        if health.returncode == 0 and last == "healthy" and running.stdout.strip() == image:
            return
        io.sleep(config.pause_seconds)
    raise DeployError(f"{service} did not become healthy on {image} ({last})", code=1)


def _sync_tip(io, profile: Profile, sha: str) -> None:
    refspec = f"+refs/heads/{profile.branch}:refs/remotes/origin/{profile.branch}"
    _checked(io, ["git", "-C", profile.checkout, "fetch", "origin", refspec], timeout=180)
    tip = _line(io, ["git", "-C", profile.checkout, "rev-parse", f"refs/remotes/origin/{profile.branch}"])
    if tip != sha:
        raise DeployError(f"{sha} is not the tip of {profile.branch}; activation refused", code=3)


def _assert_clean(io, profile: Profile) -> None:
    porcelain = _checked(io, ["git", "-C", profile.checkout, "status", "--porcelain"])
    if porcelain.strip():
        raise DeployError("checkout has uncommitted changes; refusing to discard them:\n" + porcelain[:2000])


def _assert_not_superseded(io, profile: Profile, config: DeployConfig) -> None:
    if not config.run_id:
        if config.first_install != FIRST_INSTALL:
            raise DeployError("HISTORYIT_GITHUB_RUN_ID is required")
        log("manual first install: GitHub superseded-run check skipped")
        return
    if not re.fullmatch(r"[0-9]+", config.run_id):
        raise DeployError("HISTORYIT_GITHUB_RUN_ID is not numeric")
    if not config.token:
        raise DeployError("GH_TOKEN is required to reject superseded runs")
    runs = io.github_runs(profile.workflow, profile.branch, config.token)
    newer = superseded(runs, config.run_id)
    if newer is not None:
        raise DeployError(f"superseded by workflow run {newer}; activation refused", code=3)


def _identity(io, profile: Profile, config: DeployConfig, *, require_sha: bool) -> None:
    if config.repository != REPO:
        raise DeployError("repository is not musabkashif/history-it")
    if require_sha and not SHA_RE.fullmatch(config.sha):
        raise DeployError("HISTORYIT_SHA must be the 40-character tested commit")
    host = io.hostname().strip()
    if host != profile.hostname:
        raise DeployError(f"host {host!r} cannot run the {profile.name} deployment")
    if not io.is_dir(profile.checkout):
        raise DeployError(f"checkout is missing: {profile.checkout}")
    branch = _line(io, ["git", "-C", profile.checkout, "rev-parse", "--abbrev-ref", "HEAD"])
    if branch != profile.branch:
        raise DeployError(f"checkout is on {branch}, not {profile.branch}")


def _load_state(io, profile: Profile) -> dict:
    path = posixpath.join(profile.state_dir, "release.json")
    if not io.exists(path):
        raise DeployError(f"no release state at {path}")
    try:
        state = json.loads(io.read(path))
    except json.JSONDecodeError as exc:
        raise DeployError("release state is not valid JSON") from exc
    if not isinstance(state, dict):
        raise DeployError("release state is not an object")
    for key in ("previous_sha", "previous_api_image", "previous_web_image", "previous_api_id", "previous_web_id"):
        if key not in state:
            raise DeployError(f"release state is missing {key}")
    if not SHA_RE.fullmatch(state["previous_sha"]):
        raise DeployError("release state previous SHA is not 40 hex characters")
    if not IMAGE_RE.fullmatch(state["previous_api_image"]) or not IMAGE_RE.fullmatch(state["previous_web_image"]):
        raise DeployError("release state image tags are not unique rollback tags")
    state["previous_api_id"] = normalize_image_id(state["previous_api_id"])
    state["previous_web_id"] = normalize_image_id(state["previous_web_id"])
    if state.get("environment") not in (None, profile.name):
        raise DeployError("release state belongs to another environment")
    return state


def _activate(io, config: DeployConfig, profile: Profile, prefix: list[str], api_image: str, web_image: str, override_name: str) -> None:
    override = posixpath.join(profile.state_dir, override_name)
    io.write(override, _override(api_image, web_image))
    command = _compose(
        prefix,
        profile.project,
        profile.checkout,
        [
            posixpath.join(profile.checkout, "docker-compose.prod.yml"),
            override,
        ],
        posixpath.join(profile.checkout, ".env"),
        ["up", "-d", "--no-build"],
    )
    _checked(io, command, timeout=300)
    _wait_image(io, config, prefix, profile, "api", api_image)
    _wait_image(io, config, prefix, profile, "web", web_image)


def _restore_git(io, profile: Profile, previous_sha: str) -> None:
    porcelain = _checked(io, ["git", "-C", profile.checkout, "status", "--porcelain"])
    if porcelain.strip():
        log("checkout has uncommitted changes; containers were restored and the checkout was left as it is")
        return
    _checked(io, ["git", "-C", profile.checkout, "switch", "-C", profile.branch, previous_sha])


UNGUARDED_REPORT = (
    "restored application has no admission route; "
    "guarded automatic deployment is not available"
)


def _release_admission(io, prefix: list[str], profile: Profile, *, required: bool) -> str:
    """Return guarded or unguarded from the running process only.

    The deploy script being present on disk is not evidence of a guard.
    """
    status, body = _probe(io, prefix, profile, RELEASE_PATH)
    decision = classify_release(status, body)
    if decision == "absent":
        if required:
            raise DeployError("admission release endpoint is absent", code=4)
        log(UNGUARDED_REPORT)
        return "unguarded"
    if decision == "active":
        raise DeployError("admission release refused because a worker is active", code=4)
    if decision != "open":
        raise DeployError("admission release returned an unknown result", code=4)
    return "guarded"


def restore_previous(io, config: DeployConfig, profile: Profile, prefix: list[str], state: dict) -> str:
    log(f"restoring images {state['previous_api_image']} and {state['previous_web_image']}")
    _activate(
        io,
        config,
        profile,
        prefix,
        state["previous_api_image"],
        state["previous_web_image"],
        "compose.rollback.yml",
    )
    try:
        _restore_git(io, profile, state["previous_sha"])
    except DeployError as exc:
        log(f"previous containers are up; checkout was not moved: {exc}")
    _smoke(io, config, profile)
    guard = _release_admission(io, prefix, profile, required=False)
    log("previous release restored")
    if guard == "unguarded":
        log("automatic staging deployment must stay disabled until a guarded release is running")
    log("rollback command if needed again: " + rollback_command(profile))
    log("release state remains at " + posixpath.join(profile.state_dir, "release.json"))
    return guard


def deploy(io, config: DeployConfig) -> None:
    profile = PROFILES[config.environment]
    if config.first_install and config.run_id:
        raise DeployError("GitHub Actions cannot perform a first install")
    _identity(io, profile, config, require_sha=True)
    _assert_clean(io, profile)
    _sync_tip(io, profile, config.sha)
    _assert_not_superseded(io, profile, config)
    io.acquire_lock(profile.lock_dir)
    admission_held = False
    replaced = False
    prefix: list[str] = []
    try:
        _assert_clean(io, profile)
        _sync_tip(io, profile, config.sha)
        _assert_not_superseded(io, profile, config)
        previous_sha = _line(io, ["git", "-C", profile.checkout, "rev-parse", "HEAD"])
        if not SHA_RE.fullmatch(previous_sha):
            raise DeployError("checkout HEAD is not a commit")
        destination = posixpath.join(profile.build_root, config.sha)
        io.ensure_dir(profile.build_root)
        if io.exists(destination):
            existing = _line(io, ["git", "-C", destination, "rev-parse", "HEAD"])
            if existing != config.sha:
                raise DeployError(f"build directory {destination} is at {existing}; remove that build directory manually")
        else:
            _checked(io, ["git", "-C", profile.checkout, "worktree", "add", "--detach", destination, config.sha])
        guard = posixpath.join(destination, "server/app/deployment_admission.py")
        if not io.exists(guard):
            raise DeployError("candidate commit has no deployment admission guard; activation refused", code=4)
        require_markers(io.read(posixpath.join(destination, "docker-compose.prod.yml")), COMPOSE_MARKERS, "docker-compose.prod.yml")
        require_markers(io.read(posixpath.join(destination, "deploy/nginx/https.conf")), NGINX_MARKERS, "https.conf")
        prefix = _docker_prefix(io)
        base_files = [posixpath.join(profile.checkout, "docker-compose.prod.yml")]
        api_container = _one_id(_checked(io, _compose(prefix, profile.project, profile.checkout, base_files, None, ["ps", "-q", "api"])))
        web_container = _one_id(_checked(io, _compose(prefix, profile.project, profile.checkout, base_files, None, ["ps", "-q", "web"])))
        api_id = normalize_image_id(_line(io, [*prefix, "inspect", "--format", "{{.Image}}", api_container]))
        web_id = normalize_image_id(_line(io, [*prefix, "inspect", "--format", "{{.Image}}", web_container]))
        keep_api = f"historyit-api:keep-{previous_sha}"
        keep_web = f"historyit-web:keep-{previous_sha}"
        cand_api = f"historyit-api:cand-{config.sha}"
        cand_web = f"historyit-web:cand-{config.sha}"
        _checked(io, [*prefix, "tag", api_id, keep_api])
        _checked(io, [*prefix, "tag", web_id, keep_web])
        override = posixpath.join(profile.state_dir, "compose.candidate.yml")
        io.ensure_dir(profile.state_dir)
        io.write(override, _override(cand_api, cand_web))
        _checked(
            io,
            _compose(
                prefix,
                profile.project + "-candidate",
                destination,
                [posixpath.join(destination, "docker-compose.prod.yml"), override],
                None,
                ["build"],
            ),
            timeout=2700,
        )
        if normalize_image_id(_line(io, [*prefix, "image", "inspect", "--format", "{{.Id}}", keep_api])) != api_id:
            raise DeployError("rollback API image tag changed during the build")
        if normalize_image_id(_line(io, [*prefix, "image", "inspect", "--format", "{{.Id}}", keep_web])) != web_id:
            raise DeployError("rollback web image tag changed during the build")
        _line(io, [*prefix, "image", "inspect", "--format", "{{.Id}}", cand_api])
        _line(io, [*prefix, "image", "inspect", "--format", "{{.Id}}", cand_web])
        _assert_not_superseded(io, profile, config)
        status, body = _probe(io, prefix, profile, HOLD_PATH)
        decision = classify_admission(status, body)
        if decision == "active":
            raise DeployError("a worker is active; containers were not replaced", code=4)
        if decision == "unknown":
            raise DeployError("admission guard returned an unknown result; containers were not replaced", code=4)
        if decision == "absent":
            if config.first_install != FIRST_INSTALL:
                raise DeployError(
                    "running process has no admission guard; containers were not replaced. "
                    "The first install is a manual host command during a confirmed idle window.",
                    code=4,
                )
            log("ADMISSION GUARD ABSENT. Idle was not verified by the application.")
            log("Continuing only because the operator set the first-install acknowledgement.")
        elif decision != "proceed":
            raise DeployError("admission guard did not grant a hold; containers were not replaced", code=4)
        else:
            admission_held = True
        _assert_not_superseded(io, profile, config)
        before = environment_digest(io.read(posixpath.join(profile.checkout, ".env")))
        _preserved(io, profile)
        _volumes(io, prefix, profile)
        _assert_clean(io, profile)
        _checked(io, ["git", "-C", profile.checkout, "merge", "--ff-only", config.sha])
        state = {
            "environment": profile.name,
            "activation": "pending",
            "target_sha": config.sha,
            "previous_sha": previous_sha,
            "api_image": cand_api,
            "web_image": cand_web,
            "previous_api_image": keep_api,
            "previous_web_image": keep_web,
            "previous_api_id": api_id,
            "previous_web_id": web_id,
        }
        io.write(posixpath.join(profile.state_dir, "release.json"), json.dumps(state, indent=2, sort_keys=True) + "\n")
        replaced = True
        _activate(io, config, profile, prefix, cand_api, cand_web, "compose.candidate.yml")
        status, body = _probe(io, prefix, profile, HOLD_PATH)
        if classify_admission(status, body) != "proceed":
            raise DeployError("replacement process did not report an idle admission hold", code=4)
        _smoke(io, config, profile)
        if environment_digest(io.read(posixpath.join(profile.checkout, ".env"))) != before:
            raise DeployError("host .env changed during activation", code=1)
        _preserved(io, profile)
        _volumes(io, prefix, profile)
        state["activation"] = "complete"
        io.write(posixpath.join(profile.state_dir, "release.json"), json.dumps(state, indent=2, sort_keys=True) + "\n")
        io.write(posixpath.join(profile.state_dir, "deployed-sha"), config.sha + "\n")
        _release_admission(io, prefix, profile, required=True)
        admission_held = False
        log(f"deployed {config.sha} on {profile.name}")
        log("rollback: " + rollback_command(profile))
    except DeployError as exc:
        if replaced:
            log(f"activation failed: {exc}")
            state_path = posixpath.join(profile.state_dir, "release.json")
            try:
                guard = restore_previous(io, config, profile, prefix, _load_state(io, profile))
            except DeployError as rollback_error:
                raise DeployError(
                    f"activation failed: {exc}; rollback failed: {rollback_error}. "
                    f"Recover with: {rollback_command(profile)}. Release state remains at {state_path}",
                    code=1,
                ) from exc
            detail = f"activation failed: {exc}; previous release restored"
            if guard == "unguarded":
                detail = f"{detail}; {UNGUARDED_REPORT}"
            raise DeployError(detail, code=1) from exc
        raise
    finally:
        if admission_held and not replaced:
            try:
                _release_admission(io, prefix, profile, required=False)
            except DeployError as exc:
                log(f"could not release the admission hold: {exc}")
                log("release: " + release_command(profile))
        io.release_lock(profile.lock_dir)


def rollback(io, config: DeployConfig) -> None:
    profile = PROFILES[config.environment]
    _identity(io, profile, config, require_sha=False)
    state = _load_state(io, profile)
    prefix = _docker_prefix(io)
    status, body = _probe(io, prefix, profile, HOLD_PATH)
    decision = classify_admission(status, body)
    if decision == "active":
        raise DeployError("a worker is active; rollback did not replace containers", code=4)
    if decision != "proceed":
        raise DeployError("admission guard did not grant a hold; rollback refused", code=4)
    io.acquire_lock(profile.lock_dir)
    try:
        restore_previous(io, config, profile, prefix, state)
    finally:
        io.release_lock(profile.lock_dir)


def release_hold(io, config: DeployConfig) -> None:
    profile = PROFILES[config.environment]
    _identity(io, profile, config, require_sha=False)
    prefix = _docker_prefix(io)
    _release_admission(io, prefix, profile, required=True)
    log("admission hold released; no containers were replaced")


def config_from_environ(*, require_sha: bool) -> DeployConfig:
    environment = os.environ.get("HISTORYIT_ENV", "")
    if environment not in PROFILES:
        raise DeployError("HISTORYIT_ENV must be production or staging")
    sha = os.environ.get("HISTORYIT_SHA", "")
    if require_sha and not SHA_RE.fullmatch(sha):
        raise DeployError("HISTORYIT_SHA must be the 40-character tested commit")
    first = os.environ.get("HISTORYIT_ADMISSION_FIRST_INSTALL") or None
    run_id = os.environ.get("HISTORYIT_GITHUB_RUN_ID") or None
    if first and first != FIRST_INSTALL:
        raise DeployError("unrecognized first-install acknowledgement")
    if first and run_id:
        raise DeployError("GitHub Actions cannot perform a first install")
    repository = os.environ.get("HISTORYIT_REPOSITORY") or REPO
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None
    return DeployConfig(
        environment=environment,
        sha=sha,
        repository=repository,
        run_id=run_id,
        token=token,
        first_install=first,
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] not in {"deploy", "rollback", "release-hold", "wait-for-ci", "accept-staging-auto"}:
        print(
            "usage: deploy_release.py deploy|rollback|release-hold|wait-for-ci|accept-staging-auto",
            file=sys.stderr,
        )
        return 2
    try:
        if args[0] == "accept-staging-auto":
            sha = accept_staging_auto(
                repository=os.environ.get("HISTORYIT_REPOSITORY", ""),
                autodeploy=os.environ.get("STAGING_AUTODEPLOY_ENABLED"),
                ci_event=os.environ.get("HISTORYIT_CI_EVENT", ""),
                conclusion=os.environ.get("HISTORYIT_CI_CONCLUSION", ""),
                branch=os.environ.get("HISTORYIT_CI_BRANCH", ""),
                sha=os.environ.get("HISTORYIT_SHA", ""),
                head_repository=os.environ.get("HISTORYIT_HEAD_REPOSITORY", ""),
                head_fork=os.environ.get("HISTORYIT_HEAD_FORK", ""),
            )
            log(f"accepted staging CI run {sha}")
            return 0
        if args[0] == "wait-for-ci":
            sha = os.environ.get("HISTORYIT_SHA", "")
            repository = os.environ.get("HISTORYIT_REPOSITORY", "")
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
            wait_for_ci(
                sha=sha,
                repository=repository,
                list_runs=lambda: github_workflow_runs("ci.yml", token=token, head_sha=sha),
                sleep=time.sleep,
            )
            log(f"CI succeeded for {sha}")
            return 0
        config = config_from_environ(require_sha=args[0] == "deploy")
        io = RealIO()
        if args[0] == "deploy":
            deploy(io, config)
        elif args[0] == "rollback":
            rollback(io, config)
        else:
            release_hold(io, config)
    except DeployError as exc:
        print(f"historyit-deploy: {exc}", file=sys.stderr)
        return exc.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
