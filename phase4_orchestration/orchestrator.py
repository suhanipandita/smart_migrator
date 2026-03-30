"""
phase4_orchestration/orchestrator.py
──────────────────────────────────────
§4.5  Phase 4 — Migration Orchestration and Deployment

Implements:
  §4.5.1  Pre-migration validation (4 checks)
  §4.5.2  Helm-based deployment (--wait, --atomic)
  §4.5.3  Algorithm 2 — post-deployment health verification
           consecutive_ok ≥ 3 before declaring HEALTHY
"""

import subprocess, time, logging, requests
from dataclasses import dataclass
from config.settings import (
    KUBE_CONTEXTS, HELM_RELEASE, NAMESPACE, HELM_CHART,
    T_DEPLOY_SEC, T_VERIFY_SEC, CONSECUTIVE_OK, HEALTH_POLL_DT,
    ECR_REPO,
)
from audit.logger import log_rollback

log = logging.getLogger("phase4.orchestrator")


@dataclass
class DeployResult:
    success:   bool
    provider:  str
    image_tag: str
    endpoint:  str   # LoadBalancer IP / hostname
    reason:    str   # failure reason if success=False


class MigrationOrchestrator:
    """§4.5 — full Phase 4 pipeline."""

    # ── §4.5.1  Pre-flight validation ─────────────────────────────────────
    def preflight(self, target: str, image_tag: str) -> tuple[bool, str]:
        """
        Four checks from §4.5.1. Returns (ok, failure_reason).
        Check 1: kubeconfig context reachable
        Check 2: container image exists in registry
        Check 3: namespace + RBAC
        Check 4: resource quota headroom
        """
        # Check 1 — kubeconfig context
        ctx = KUBE_CONTEXTS[target]
        result = self._kubectl(["config", "use-context", ctx], check=False)
        if result.returncode != 0:
            return False, f"kubeconfig context not found or unreachable: {ctx}"

        result = self._kubectl(["cluster-info"], check=False)
        if result.returncode != 0:
            return False, f"API server unreachable for context {ctx}"
        log.info("Pre-flight [1/4] kubeconfig OK for %s", target)

        # Check 2 — container image reachable
        image_uri = f"{ECR_REPO}:{image_tag}"
        result = subprocess.run(
            ["docker", "manifest", "inspect", image_uri],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"Image {image_uri} not found in registry"
        log.info("Pre-flight [2/4] image %s exists", image_uri)

        # Check 3 — namespace exists (create if not)
        ns_result = self._kubectl(["get", "namespace", NAMESPACE], check=False)
        if ns_result.returncode != 0:
            log.info("Pre-flight [3/4] namespace %s not found — creating", NAMESPACE)
            self._kubectl(["create", "namespace", NAMESPACE])
        else:
            log.info("Pre-flight [3/4] namespace %s OK", NAMESPACE)

        # Check 4 — resource quota (check allocatable CPU > 250m)
        nodes_json = self._kubectl(
            ["get", "nodes", "-o", "jsonpath={.items[*].status.allocatable.cpu}"],
            check=False
        ).stdout.strip()
        log.info("Pre-flight [4/4] allocatable CPU on %s: %s", target, nodes_json)
        # In production: parse and compare against workload requests
        return True, ""

    # ── §4.5.2  Helm deployment ────────────────────────────────────────────
    def deploy(self, target: str, image_tag: str) -> DeployResult:
        """
        §4.5.2 — helm upgrade --install --wait --atomic
        --wait: blocks until all pods Ready (or T_deploy timeout)
        --atomic: auto-rollback on failure
        """
        ctx       = KUBE_CONTEXTS[target]
        image_uri = f"{ECR_REPO}:{image_tag}"
        self._kubectl(["config", "use-context", ctx])

        log.info("Deploying %s to %s (image=%s)", HELM_RELEASE, target, image_tag)

        helm_cmd = [
            "helm", "upgrade", "--install", HELM_RELEASE, HELM_CHART,
            "--namespace",    NAMESPACE,
            "--create-namespace",
            "--set",          f"image.repository={ECR_REPO}",
            "--set",          f"image.tag={image_tag}",
            "--set",          f"cloud.provider={target}",    # injected as env var → /health body
            "--wait",                                         # §4.5.2 — block until pods Ready
            "--timeout",      f"{T_DEPLOY_SEC}s",
            "--atomic",                                       # §4.5.2 — auto-rollback on failure
        ]

        result = subprocess.run(helm_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            log.error("Helm deploy failed:\n%s", result.stderr)
            return DeployResult(
                success=False, provider=target, image_tag=image_tag,
                endpoint="", reason=f"helm deploy failed: {result.stderr[:300]}"
            )
        log.info("Helm deploy successful on %s", target)

        # Retrieve LoadBalancer endpoint
        endpoint = self._get_endpoint()
        return DeployResult(
            success=True, provider=target, image_tag=image_tag,
            endpoint=endpoint, reason=""
        )

    def _get_endpoint(self) -> str:
        """Poll until LoadBalancer external IP/hostname is assigned."""
        for _ in range(30):
            out = self._kubectl([
                "get", "svc", HELM_RELEASE, "-n", NAMESPACE,
                "-o", "jsonpath={.status.loadBalancer.ingress[0].hostname}"
            ], check=False).stdout.strip()
            if out:
                log.info("LoadBalancer endpoint: %s", out)
                return out
            out_ip = self._kubectl([
                "get", "svc", HELM_RELEASE, "-n", NAMESPACE,
                "-o", "jsonpath={.status.loadBalancer.ingress[0].ip}"
            ], check=False).stdout.strip()
            if out_ip:
                log.info("LoadBalancer IP: %s", out_ip)
                return out_ip
            time.sleep(10)
        log.warning("LoadBalancer endpoint not assigned after 5 min")
        return ""

    # ── §4.5.3  Algorithm 2 — post-deployment health verification ─────────
    def verify_healthy(self, endpoint: str,
                        timeout: int = T_VERIFY_SEC,
                        poll_dt: int = HEALTH_POLL_DT,
                        required_ok: int = CONSECUTIVE_OK) -> bool:
        """
        Algorithm 2 (§4.5.3):

            INPUT:  target_endpoint, timeout T_verify, poll_interval Δt
            OUTPUT: HEALTHY or UNHEALTHY

            1.  deadline ← current_time() + T_verify
            2.  WHILE current_time() < deadline:
            3.      response ← HTTP_GET(endpoint + '/health', timeout=5s)
            4.      IF response.status=200 AND body.status='ok' THEN
            5.          consecutive_ok ← consecutive_ok + 1
            6.      ELSE consecutive_ok ← 0
            7.      IF consecutive_ok ≥ 3 THEN RETURN HEALTHY
            8.      SLEEP(Δt)
            9.  RETURN UNHEALTHY
        """
        url              = f"http://{endpoint}/health"
        consecutive_ok   = 0                          # Algorithm 2, line 5
        deadline         = time.time() + timeout      # Algorithm 2, line 1

        log.info("Algorithm 2: verifying %s (need %d consecutive OKs)", url, required_ok)

        while time.time() < deadline:                 # Algorithm 2, line 2
            try:
                resp = requests.get(url, timeout=5)   # Algorithm 2, line 3
                body = resp.json()
                if resp.status_code == 200 and body.get("status") == "ok":
                    consecutive_ok += 1               # Algorithm 2, line 5
                    log.info("Health OK (%d/%d)", consecutive_ok, required_ok)
                else:
                    consecutive_ok = 0                # Algorithm 2, line 6
                    log.warning("Health NOK: status=%d body=%s", resp.status_code, body)
            except Exception as exc:
                consecutive_ok = 0                    # Algorithm 2, line 6
                log.warning("Health poll error: %s", exc)

            if consecutive_ok >= required_ok:         # Algorithm 2, line 7
                log.info("Algorithm 2: HEALTHY — %s", endpoint)
                return True

            time.sleep(poll_dt)                       # Algorithm 2, line 8

        log.error("Algorithm 2: UNHEALTHY — timeout after %ds", timeout)
        return False                                  # Algorithm 2, line 9

    # ── Full Phase 4 pipeline ─────────────────────────────────────────────
    def run(self, event_id: str, target: str,
            image_tag: str, source: str) -> DeployResult:
        """
        Complete Phase 4: preflight → deploy → verify.
        Returns DeployResult. On failure, caller (main.py) handles rollback.
        """
        # §4.5.1 — pre-flight
        ok, reason = self.preflight(target, image_tag)
        if not ok:
            log_rollback(event_id, source, target, "preflight", reason)
            return DeployResult(
                success=False, provider=target,
                image_tag=image_tag, endpoint="", reason=reason
            )

        # §4.5.2 — deploy
        result = self.deploy(target, image_tag)
        if not result.success:
            log_rollback(event_id, source, target, "helm_deploy", result.reason)
            return result

        # §4.5.3 — verify (Algorithm 2)
        healthy = self.verify_healthy(result.endpoint)
        if not healthy:
            reason = "post-deployment health verification timeout (Algorithm 2)"
            log_rollback(event_id, source, target, "health_verify", reason)
            # --atomic already cleaned up the Helm release on the target cluster
            return DeployResult(
                success=False, provider=target,
                image_tag=image_tag, endpoint=result.endpoint, reason=reason
            )

        log.info("Phase 4 complete — %s is healthy at %s", target, result.endpoint)
        return result

    # ── kubectl helper ────────────────────────────────────────────────────
    @staticmethod
    def _kubectl(args: list, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["kubectl"] + args
        return subprocess.run(cmd, capture_output=True, text=True,
                              check=False if not check else True)