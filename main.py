"""
main.py
────────
Top-level pipeline — wires Phases 1–5 together as specified in §4.1.

Phases 1  runs once at onboarding (or when a new workload is registered).
Phase  2  runs as a perpetual background loop.
Phases 3–5 execute sequentially when the monitoring trigger fires.

Usage:
    # Onboard an application (Phase 1)
    python main.py onboard --app ./app --tag v1.0

    # Start the monitoring loop (Phases 2–5 run automatically)
    python main.py run --provider aws

    # Manual migration (bypasses monitoring trigger)
    python main.py migrate --source aws --target azure --tag v1.0
"""

import argparse, logging, sys, time
from config.settings import PROVIDERS, KUBE_CONTEXTS
from phase1_containerization.assessor  import WorkloadAssessor
from phase2_monitoring.monitor         import MonitoringLoop
from phase3_decision.engine            import DecisionEngine
from phase4_orchestration.orchestrator import MigrationOrchestrator
from phase5_dns.dns_manager            import DNSManager
from audit.logger                      import (
    log_migration_start, log_migration_complete, new_event_id
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)-28s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("main")

# ── Active state (in production: persist to DynamoDB / Redis) ──────────────
_state = {
    "active_provider": "aws",
    "image_tag":       "v1.0",
    "endpoints": {      # populated after Phase 1 / onboarding
        "aws":   "",
        "azure": "",
        "gcp":   "",
    },
}


# ── Phase 3–5 migration pipeline ───────────────────────────────────────────
def run_migration_pipeline(snapshots: dict, image_tag: str | None = None) -> bool:
    """
    Called by the monitoring loop when Trigger = TRUE.
    Executes Phases 3 → 4 → 5 in sequence.
    Returns True on CUTOVER_COMPLETE.
    """
    current   = _state["active_provider"]
    tag       = image_tag or _state["image_tag"]
    event_id  = new_event_id()

    # ── Phase 3 — cloud selection (§4.4 / Algorithm 1) ───────────────────
    engine = DecisionEngine()
    result = engine.select(snapshots, current)

    if result.action != "migrate":
        log.info("Phase 3: no migration warranted — %s", result.reason)
        return False

    target = result.target
    log.info("Phase 3: migrate %s → %s  (score %.4f → %.4f)",
             current, target, result.current_score, result.target_score)
    log_migration_start(event_id, current, target,
                         result.current_score, result.target_score)
    t_start = time.time()

    # ── Phase 4 — orchestration (§4.5 / Algorithm 2) ─────────────────────
    orch   = MigrationOrchestrator()
    deploy = orch.run(event_id, target, tag, current)

    if not deploy.success:
        log.error("Phase 4 FAILED: %s — source %s continues serving traffic",
                  deploy.reason, current)
        return False

    target_endpoint = deploy.endpoint
    source_endpoint = _state["endpoints"].get(current, "")

    # ── Phase 5 — DNS cutover (§4.6 / Algorithm 3) ───────────────────────
    dns = DNSManager()
    dns._endpoint_cache = _state["endpoints"]   # load cached IPs
    dns._endpoint_cache[target] = target_endpoint

    cutover_ok = dns.cutover(
        event_id, current, target,
        target_endpoint=target_endpoint,
        source_endpoint=source_endpoint,
    )

    if not cutover_ok:
        log.error("Phase 5 FAILED: DNS cutover rolled back — %s still active", current)
        return False

    # ── Migration complete ────────────────────────────────────────────────
    duration = time.time() - t_start
    log_migration_complete(event_id, current, target, duration)

    # Update active state
    _state["active_provider"] = target
    _state["endpoints"][target] = target_endpoint

    # §4.6.3 — decommission source (runs in background after T_hold)
    dns.decommission_source(event_id, current, t_start)

    log.info("Migration pipeline complete  %s → %s  (%.0fs)", current, target, duration)
    return True


# ── CLI commands ──────────────────────────────────────────────────────────
def cmd_onboard(args):
    """Phase 1 — assess and containerize a workload."""
    assessor = WorkloadAssessor()
    result   = assessor.assess(
        app_path=args.app,
        image_tag=args.tag,
        verify_health=args.verify_health,
    )
    if result.registry_pushed:
        _state["image_tag"] = args.tag
        log.info("Onboarding complete: %s", result.image_uri)
    else:
        log.error("Onboarding failed — image not pushed")
        sys.exit(1)

    # §4.6.1 — pre-stage low TTL DNS at onboarding time
    if args.prestage_dns:
        dns = DNSManager()
        dns.prestage_low_ttl(provider_endpoints=_state["endpoints"])


def cmd_run(args):
    """Start monitoring loop (Phases 2–5 run automatically)."""
    _state["active_provider"] = args.provider

    def on_trigger(snapshots):
        run_migration_pipeline(snapshots)

    loop = MonitoringLoop(on_trigger=on_trigger)
    loop.run()


def cmd_migrate(args):
    """Manual migration — bypasses monitoring trigger, runs Phases 3–5."""
    from phase2_monitoring.monitor import MetricsCollector
    collector = MetricsCollector()
    snapshots = collector.collect_all()
    _state["active_provider"] = args.source
    _state["image_tag"]       = args.tag

    success = run_migration_pipeline(snapshots, image_tag=args.tag)
    sys.exit(0 if success else 1)


# ── Entry point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Smart Multi-Cloud Workload Migrator")
    sub    = parser.add_subparsers(dest="command", required=True)

    # onboard
    p_on = sub.add_parser("onboard", help="Phase 1 — assess and containerize")
    p_on.add_argument("--app",           default="./app",  help="Path to application directory")
    p_on.add_argument("--tag",           default="v1.0",   help="Semantic image version tag")
    p_on.add_argument("--verify-health", action="store_true",
                      help="Spin up local container and verify /health endpoint")
    p_on.add_argument("--prestage-dns",  action="store_true",
                      help="Pre-stage low-TTL DNS records (§4.6.1)")

    # run (monitoring loop)
    p_run = sub.add_parser("run", help="Start monitoring loop (Phases 2–5)")
    p_run.add_argument("--provider", default="aws", choices=PROVIDERS,
                       help="Currently active cloud provider")

    # migrate (manual)
    p_mig = sub.add_parser("migrate", help="Manual migration (Phases 3–5)")
    p_mig.add_argument("--source", required=True, choices=PROVIDERS)
    p_mig.add_argument("--target", required=True, choices=PROVIDERS)
    p_mig.add_argument("--tag",    default="v1.0")

    args = parser.parse_args()

    if   args.command == "onboard": cmd_onboard(args)
    elif args.command == "run":     cmd_run(args)
    elif args.command == "migrate": cmd_migrate(args)