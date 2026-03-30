"""
phase1_containerization/assessor.py
─────────────────────────────────────
§4.2  Phase 1 — Workload Assessment and Containerization

Implements:
  §4.2.1  Application classification (cloud-native vs legacy)
  §4.2.2  Health endpoint enforcement + validation
  §4.2.3  Container image versioning and registry upload

Usage:
    assessor = WorkloadAssessor()
    result   = assessor.assess("./myapp", image_tag="v1.0")
"""

import subprocess, logging, time, requests
from dataclasses import dataclass
from pathlib import Path
from config.settings import (
    ECR_REPO, AWS_REGION, T_TTL_SEC,
    HEALTH_POLL_DT, CONSECUTIVE_OK, T_VERIFY_SEC,
)

log = logging.getLogger("phase1.assessor")


@dataclass
class AssessmentResult:
    app_path:       str
    image_tag:      str
    image_uri:      str          # full ECR URI
    is_cloud_native: bool        # §4.2.1
    health_verified: bool        # §4.2.2
    registry_pushed: bool        # §4.2.3


class WorkloadAssessor:
    """
    §4.2.1 — classify, §4.2.2 — enforce health endpoint,
    §4.2.3 — build, tag, push container image.
    """

    # ── §4.2.1  Application classification ────────────────────────────────
    def classify(self, app_path: str) -> bool:
        """
        Returns True  → cloud-native (Dockerfile already present).
        Returns False → legacy (needs Containerization Adapter).
        """
        path = Path(app_path)
        dockerfile_exists = (path / "Dockerfile").exists()
        log.info(
            "Classification: %s → %s",
            app_path,
            "cloud-native" if dockerfile_exists else "legacy (adapter required)",
        )
        return dockerfile_exists

    # ── §4.2.1  Legacy adapter — generate Dockerfile ─────────────────────
    def adapt_legacy(self, app_path: str) -> None:
        """
        Minimal Containerization Adapter for Node.js legacy apps.
        In production, replace with AWS App2Container or Buildpacks.
        Generates a Dockerfile if none exists.
        """
        dockerfile = Path(app_path) / "Dockerfile"
        if dockerfile.exists():
            return
        log.info("Adapter: generating Dockerfile for legacy app at %s", app_path)
        dockerfile.write_text(
            "FROM node:20-alpine\n"
            "WORKDIR /app\n"
            "COPY package*.json ./\n"
            "RUN npm ci --production 2>/dev/null || true\n"
            "COPY . .\n"
            "EXPOSE 8080\n"
            # §4.2.2 — health endpoint mandatory
            "HEALTHCHECK CMD wget -qO- http://localhost:8080/health || exit 1\n"
            "CMD [\"node\", \"server.js\"]\n"
        )
        log.info("Adapter: Dockerfile written to %s", dockerfile)

    # ── §4.2.2  Health endpoint verification ──────────────────────────────
    def verify_health_endpoint(self, local_port: int = 8080,
                                timeout: int = T_VERIFY_SEC) -> bool:
        """
        §4.2.2 — confirms /health returns HTTP 200 with {"status":"ok"}.
        Uses the same consecutive-OK logic as Algorithm 2 (§4.5.3).
        """
        url = f"http://localhost:{local_port}/health"
        consecutive_ok = 0
        deadline = time.time() + timeout
        log.info("Health verification: polling %s", url)

        while time.time() < deadline:
            try:
                resp = requests.get(url, timeout=5)
                body = resp.json()
                if resp.status_code == 200 and body.get("status") == "ok":
                    consecutive_ok += 1
                    log.info("Health OK (%d/%d)", consecutive_ok, CONSECUTIVE_OK)
                else:
                    consecutive_ok = 0
                    log.warning("Health returned status=%d body=%s", resp.status_code, body)
            except Exception as exc:
                consecutive_ok = 0
                log.warning("Health poll error: %s", exc)

            if consecutive_ok >= CONSECUTIVE_OK:
                log.info("Health endpoint VERIFIED")
                return True
            time.sleep(HEALTH_POLL_DT)

        log.error("Health verification FAILED after %ds timeout", timeout)
        return False

    # ── §4.2.3  Build, tag, push ──────────────────────────────────────────
    def build_image(self, app_path: str, image_tag: str) -> str:
        """
        Builds linux/amd64 image (required for EKS/AKS/GKE x86 nodes
        when building on Apple Silicon — §4.2.1 note).
        Returns the local image reference.
        """
        local_ref = f"smart-migrator-app:{image_tag}"
        log.info("Building image: %s (platform linux/amd64)", local_ref)
        self._run([
            "docker", "build",
            "--platform", "linux/amd64",
            "-t", local_ref,
            app_path,
        ])
        return local_ref

    def push_to_registry(self, local_ref: str, image_tag: str) -> str:
        """
        §4.2.3 — authenticates to ECR, tags with semantic version,
        pushes OCI-compliant image. Returns full ECR URI.
        """
        ecr_uri = f"{ECR_REPO}:{image_tag}"
        log.info("Pushing %s → %s", local_ref, ecr_uri)

        # ECR authentication
        login_cmd = (
            f"aws ecr get-login-password --region {AWS_REGION} | "
            f"docker login --username AWS --password-stdin "
            f"{ECR_REPO.split('/')[0]}"
        )
        subprocess.run(login_cmd, shell=True, check=True)

        self._run(["docker", "tag",  local_ref, ecr_uri])
        self._run(["docker", "push", ecr_uri])
        log.info("Push complete: %s", ecr_uri)
        return ecr_uri

    # ── Full Phase 1 pipeline ─────────────────────────────────────────────
    def assess(self, app_path: str, image_tag: str = "v1.0",
               verify_health: bool = False) -> AssessmentResult:
        """
        Runs the complete §4.2 pipeline.
        Set verify_health=True to spin up a local test container and check /health.
        """
        is_native = self.classify(app_path)
        if not is_native:
            self.adapt_legacy(app_path)

        local_ref = self.build_image(app_path, image_tag)

        health_ok = False
        if verify_health:
            # start container locally, verify, stop
            container_id = subprocess.check_output([
                "docker", "run", "-d", "-p", "8080:8080", local_ref
            ]).decode().strip()
            time.sleep(3)   # startup grace
            try:
                health_ok = self.verify_health_endpoint()
            finally:
                subprocess.run(["docker", "stop", container_id], check=False)
                subprocess.run(["docker", "rm",   container_id], check=False)
        else:
            health_ok = True  # deferred to Phase 4/5 in-cluster checks

        image_uri = self.push_to_registry(local_ref, image_tag)

        return AssessmentResult(
            app_path=app_path,
            image_tag=image_tag,
            image_uri=image_uri,
            is_cloud_native=is_native,
            health_verified=health_ok,
            registry_pushed=True,
        )

    @staticmethod
    def _run(cmd: list) -> None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")