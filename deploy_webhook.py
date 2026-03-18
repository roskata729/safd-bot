import hashlib
import hmac
import json
import os
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
PENDING_CHANGELOG_PATH = BASE_DIR / "pending_changelog.json"
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
DEPLOY_WEBHOOK_HOST = os.getenv("DEPLOY_WEBHOOK_HOST", "0.0.0.0").strip() or "0.0.0.0"
DEPLOY_WEBHOOK_PORT = int(os.getenv("DEPLOY_WEBHOOK_PORT", "9000"))
BOT_SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "discord-activity-bot").strip() or "discord-activity-bot"


def is_valid_signature(body: bytes, provided_signature: str) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        return False
    if not provided_signature.startswith("sha256="):
        return False
    expected = hmac.new(
        GITHUB_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", provided_signature)


def branch_ref() -> str:
    return f"refs/heads/{GITHUB_BRANCH}"


def build_commit_entries(payload: dict) -> list[dict[str, str]]:
    commits: list[dict[str, str]] = []
    for item in payload.get("commits", []):
        message = item.get("message", "").splitlines()[0].strip()
        commit_id = item.get("id", "")
        if not message or not commit_id:
            continue
        commits.append(
            {
                "sha": commit_id,
                "short_sha": commit_id[:7],
                "message": message,
                "url": item.get("url", ""),
                "author": item.get("author", {}).get("name", "Unknown"),
                "timestamp": item.get("timestamp", ""),
            }
        )
    return commits


def trigger_deploy() -> None:
    subprocess.Popen(
        [
            "bash",
            str(BASE_DIR / "deploy" / "deploy_on_push.sh"),
            str(BASE_DIR),
            GITHUB_BRANCH,
        ],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=os.environ | {"BOT_SERVICE_NAME": BOT_SERVICE_NAME},
    )


class GitHubWebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/github-webhook":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return

        if self.headers.get("X-GitHub-Event") != "push":
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b"Ignored non-push event")
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        signature = self.headers.get("X-Hub-Signature-256", "")

        if not is_valid_signature(body, signature):
            self.send_error(HTTPStatus.UNAUTHORIZED, "Invalid signature")
            return

        payload = json.loads(body.decode("utf-8"))
        repository_name = payload.get("repository", {}).get("full_name", "")
        ref = payload.get("ref", "")

        if repository_name != GITHUB_REPOSITORY or ref != branch_ref():
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            self.wfile.write(b"Ignored different repository or branch")
            return

        commits = build_commit_entries(payload)
        if commits:
            PENDING_CHANGELOG_PATH.write_text(
                json.dumps({"commits": commits}, indent=2),
                encoding="utf-8",
            )

        trigger_deploy()
        self.send_response(HTTPStatus.ACCEPTED)
        self.end_headers()
        self.wfile.write(b"Deploy started")

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}")


def validate_environment() -> list[str]:
    errors: list[str] = []
    if not GITHUB_REPOSITORY:
        errors.append("GITHUB_REPOSITORY is missing in .env")
    if not GITHUB_WEBHOOK_SECRET:
        errors.append("GITHUB_WEBHOOK_SECRET is missing in .env")
    if DEPLOY_WEBHOOK_PORT <= 0:
        errors.append("DEPLOY_WEBHOOK_PORT is missing or invalid in .env")
    return errors


if __name__ == "__main__":
    env_errors = validate_environment()
    if env_errors:
        raise RuntimeError("\n".join(env_errors))

    server = ThreadingHTTPServer((DEPLOY_WEBHOOK_HOST, DEPLOY_WEBHOOK_PORT), GitHubWebhookHandler)
    print(f"Webhook server listening on {DEPLOY_WEBHOOK_HOST}:{DEPLOY_WEBHOOK_PORT}")
    server.serve_forever()
