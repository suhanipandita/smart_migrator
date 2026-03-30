"""
phase5_dns/dns_manager.py
──────────────────────────
§4.6  Phase 5 — DNS-Based Traffic Cutover and Verification

Implements:
  §4.6.1  Low-TTL pre-staging (T_ttl = 30s, enforced at onboarding)
  §4.6.2  Algorithm 3 — weighted DNS transition
           Partial split (200:55) → observe → full cutover (0:255) → verify
  §4.6.3  Source decommissioning after T_hold hold period
"""

import time, logging, boto3
import requests
from config.settings import (
    HOSTED_ZONE_ID, DOMAIN, AWS_REGION,
    T_TTL_SEC, DNS_WEIGHT_SRC_PARTIAL, DNS_WEIGHT_TGT_PARTIAL,
    DNS_WEIGHT_FULL, DNS_WEIGHT_ZERO,
    T_VERIFY_DNS_SEC, T_HOLD_SEC, TERMINATION_GRACE_SEC,
    CONSECUTIVE_OK, HEALTH_POLL_DT, KUBE_CONTEXTS,
    HELM_RELEASE, NAMESPACE,
)
from audit.logger import log_rollback, log_migration_complete
import subprocess

log = logging.getLogger("phase5.dns")


class DNSManager:
    """
    §4.6 — manages Route 53 weighted A records.
    Provides low-TTL pre-staging, weighted cutover, and source decommissioning.
    """

    def __init__(self):
        self.r53 = boto3.client("route53", region_name=AWS_REGION)
        self._endpoint_cache: dict[str, str] = {}   # provider → IP/hostname

    # ── §4.6.1  Low-TTL pre-staging — called during Phase 1 onboarding ────
    def prestage_low_ttl(self, provider_endpoints: dict[str, str]) -> None:
        """
        §4.6.1 — sets TTL=30s on all A records during system initialization,
        well before any migration event. This ensures resolver caches have
        already expired by the time a real migration occurs.

        Also creates Route 53 health checks for each provider.
        """
        self._endpoint_cache = provider_endpoints
        log.info("Pre-staging low TTL (%ds) for %d providers", T_TTL_SEC, len(provider_endpoints))

        changes = []
        for provider, ip in provider_endpoints.items():
            hc_id = self._create_health_check(provider, ip)
            changes.append({
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name":            DOMAIN,
                    "Type":            "A",
                    "SetIdentifier":   f"migrator-{provider}",
                    "Weight":          0,    # start with zero — current provider set separately
                    "TTL":             T_TTL_SEC,
                    "HealthCheckId":   hc_id,
                    "ResourceRecords": [{"Value": ip}],
                },
            })

        self.r53.change_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID,
            ChangeBatch={"Changes": changes},
        )
        log.info("Low-TTL pre-staging complete")

    def _create_health_check(self, provider: str, ip: str) -> str:
        try:
            resp = self.r53.create_health_check(
                CallerReference=f"migrator-hc-{provider}-v2",
                HealthCheckConfig={
                    "IPAddress":        ip,
                    "Port":             80,
                    "Type":             "HTTP",
                    "ResourcePath":     "/health",
                    "RequestInterval":  10,
                    "FailureThreshold": 2,   # §4.3.2 — HEALTH_FAIL_CONSECUTIVE
                },
            )
            hc_id = resp["HealthCheck"]["Id"]
            self.r53.change_tags_for_resource(
                ResourceType="healthcheck",
                ResourceId=hc_id,
                AddTags=[{"Key": "Name", "Value": f"migrator-{provider}"}],
            )
            return hc_id
        except self.r53.exceptions.HealthCheckAlreadyExists:
            # Return existing ID
            existing = self.r53.list_health_checks()
            for hc in existing["HealthChecks"]:
                tags = self.r53.list_tags_for_resource(
                    ResourceType="healthcheck", ResourceId=hc["Id"]
                )["ResourceTagSet"]["Tags"]
                if any(t["Key"] == "Name" and t["Value"] == f"migrator-{provider}" for t in tags):
                    return hc["Id"]
        return ""

    # ── §4.6.2  Algorithm 3 — weighted DNS transition ─────────────────────
    def cutover(self, event_id: str, source: str, target: str,
                target_endpoint: str, source_endpoint: str) -> bool:
        """
        Algorithm 3 (§4.6.2):

            Step 1–4:   Partial split (src=200, tgt=55 → ~21% to target)
            Step 5–6:   Observe target under live traffic
            Step 7–10:  Full cutover (src=0, tgt=255)
            Step 11–14: Post-cutover verification window
            ROLLBACK:   Restore src=255, tgt=0 on any failure

        Returns True on CUTOVER_COMPLETE, False on ROLLBACK.
        """
        log.info("Algorithm 3: DNS cutover  %s → %s", source, target)

        # ── Algorithm 3, steps 1–4: partial split ─────────────────────────
        log.info("Step 1: partial split  src=%d  tgt=%d (≈21%% to target)",
                 DNS_WEIGHT_SRC_PARTIAL, DNS_WEIGHT_TGT_PARTIAL)
        self._set_weights({
            source: DNS_WEIGHT_SRC_PARTIAL,
            target: DNS_WEIGHT_TGT_PARTIAL,
        })
        time.sleep(T_TTL_SEC)   # Algorithm 3, step 4 — allow propagation

        # ── Algorithm 3, step 5–6: health under partial traffic ────────────
        if not self._health_ok(target_endpoint):
            log.warning("Target unhealthy under partial traffic — initiating rollback")
            return self._rollback(event_id, source, target, "partial_split",
                                  "health check failed under partial live traffic")

        # ── Algorithm 3, steps 7–10: full cutover ─────────────────────────
        log.info("Step 7: full cutover  src=%d  tgt=%d",
                 DNS_WEIGHT_ZERO, DNS_WEIGHT_FULL)
        self._set_weights({
            source: DNS_WEIGHT_ZERO,
            target: DNS_WEIGHT_FULL,
        })
        time.sleep(T_TTL_SEC)   # Algorithm 3, step 10 — allow propagation

        # ── Algorithm 3, steps 11–14: post-cutover verification ───────────
        log.info("Step 11: post-cutover verification window (%ds)", T_VERIFY_DNS_SEC)
        deadline = time.time() + T_VERIFY_DNS_SEC
        while time.time() < deadline:
            if not self._health_ok(target_endpoint):
                log.warning("Target unhealthy post-cutover — initiating rollback")
                return self._rollback(event_id, source, target, "post_cutover",
                                      "health degradation detected post full cutover")
            time.sleep(HEALTH_POLL_DT)

        log.info("Algorithm 3: CUTOVER_COMPLETE — %s is now active", target)
        return True

    # ── §4.6.3  Source decommissioning ────────────────────────────────────
    def decommission_source(self, event_id: str, source: str,
                             start_time: float) -> None:
        """
        §4.6.3 — T_hold window (10 min) then graceful Helm uninstall.
        Source deployment stays running (weight=0) for T_hold to preserve
        instant rollback capability.
        """
        elapsed = time.time() - start_time
        hold_remaining = max(0, T_HOLD_SEC - elapsed)

        log.info(
            "Decommissioning hold: %.0fs remaining (T_hold=%ds)",
            hold_remaining, T_HOLD_SEC
        )
        if hold_remaining > 0:
            time.sleep(hold_remaining)

        log.info("Decommissioning source deployment on %s", source)
        ctx = KUBE_CONTEXTS[source]
        subprocess.run(["kubectl", "config", "use-context", ctx], check=False)
        subprocess.run(
            ["helm", "uninstall", HELM_RELEASE, "--namespace", NAMESPACE],
            capture_output=True, text=True
        )
        log.info("Source %s gracefully decommissioned "
                 "(terminationGracePeriodSeconds=%d)", source, TERMINATION_GRACE_SEC)

    # ── Helpers ───────────────────────────────────────────────────────────
    def _set_weights(self, weights: dict[str, int]) -> None:
        """Update Route 53 weighted A record weights for each provider."""
        changes = []
        for provider, weight in weights.items():
            ip = self._endpoint_cache.get(provider, "0.0.0.0")
            changes.append({
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name":            DOMAIN,
                    "Type":            "A",
                    "SetIdentifier":   f"migrator-{provider}",
                    "Weight":          weight,
                    "TTL":             T_TTL_SEC,
                    "ResourceRecords": [{"Value": ip}],
                },
            })
        self.r53.change_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID,
            ChangeBatch={"Changes": changes},
        )
        log.info("DNS weights updated: %s", weights)

    def _health_ok(self, endpoint: str) -> bool:
        """Single health check poll used within Algorithm 3."""
        try:
            resp = requests.get(f"http://{endpoint}/health", timeout=5)
            return resp.status_code == 200 and resp.json().get("status") == "ok"
        except Exception as exc:
            log.warning("Health poll failed: %s", exc)
            return False

    def _rollback(self, event_id: str, source: str, target: str,
                  stage: str, reason: str) -> bool:
        """Algorithm 3 ROLLBACK procedure (steps 19–23)."""
        log.error("ROLLBACK: stage=%s  reason=%s", stage, reason)
        self._set_weights({source: DNS_WEIGHT_FULL, target: DNS_WEIGHT_ZERO})
        time.sleep(T_TTL_SEC)
        log_rollback(event_id, source, target, f"dns_{stage}", reason)
        return False

    def shift_all_traffic(self, provider: str) -> None:
        """
        Convenience method for manual traffic shifts (e.g., emergency override).
        Sets provider weight=255, all others=0.
        """
        from config.settings import PROVIDERS
        weights = {p: DNS_WEIGHT_FULL if p == provider else DNS_WEIGHT_ZERO
                   for p in PROVIDERS}
        self._set_weights(weights)
        log.info("Manual traffic shift: 100%% → %s", provider)