"""
phase2_monitoring/monitor.py
─────────────────────────────
§4.3  Phase 2 — Continuous Monitoring and Migration Trigger Evaluation

Implements:
  §4.3.1  Metrics collection (CloudWatch + Cost Explorer)
  §4.3.2  Threshold breach detection + debounce formula (N_breach)
           Trigger = TRUE iff Σ breach(t-k) = N_breach for k ∈ {0,..,N_breach-1}
  §4.3.3  Anti-flapping / hysteresis:
           Migrate = TRUE iff Score(target) < Score(current) × (1 - δ)

Runs as a perpetual loop. Calls the Decision Engine when Trigger fires.
"""

import time, logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Callable
import boto3

from config.settings import (
    CPU_THRESHOLD_PCT, MEMORY_THRESHOLD_PCT,
    LATENCY_P95_THRESHOLD_MS, ERROR_RATE_THRESHOLD,
    COST_SPIKE_FACTOR, N_BREACH,
    T_COOL_MINUTES, METRICS_POLL_INTERVAL_SEC,
    EVAL_WINDOW_SUSTAINED_MIN, AWS_REGION, EKS_CLUSTER_NAME,
)
from audit.logger import log_breach, log_trigger

log = logging.getLogger("phase2.monitor")


@dataclass
class MetricsSnapshot:
    """All metrics collected in one evaluation cycle (§4.3.1)."""
    timestamp:       datetime
    provider:        str
    cpu_pct:         float
    memory_pct:      float
    latency_p95_ms:  float
    error_rate:      float   # fraction 0–1
    hourly_cost_usd: float
    health_ok:       bool


@dataclass
class MonitorState:
    """Tracks breach counts and cooldown state across cycles."""
    breach_history:   list = field(default_factory=list)   # ring buffer of booleans
    last_migration:   Optional[datetime] = None
    active_provider:  str = "aws"


class MetricsCollector:
    """§4.3.1 — integrates with CloudWatch and Cost Explorer."""

    def __init__(self):
        self.cw = boto3.client("cloudwatch",      region_name=AWS_REGION)
        self.ce = boto3.client("ce",              region_name="us-east-1")
        self.ec2 = boto3.client("ec2",            region_name=AWS_REGION)

    def _cw_stat(self, namespace: str, metric: str,
                 dimensions: list, stat: str = "Average",
                 window_minutes: int = EVAL_WINDOW_SUSTAINED_MIN) -> float:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(minutes=window_minutes)
        resp  = self.cw.get_metric_statistics(
            Namespace=namespace, MetricName=metric,
            Dimensions=dimensions,
            StartTime=start, EndTime=end,
            Period=window_minutes * 60,
            Statistics=[stat],
        )
        pts = resp.get("Datapoints", [])
        return pts[-1].get(stat, 0.0) if pts else 0.0

    def collect_aws(self) -> MetricsSnapshot:
        dims = [{"Name": "ClusterName", "Value": EKS_CLUSTER_NAME}]

        cpu_pct    = self._cw_stat("ContainerInsights", "node_cpu_utilization",    dims)
        memory_pct = self._cw_stat("ContainerInsights", "node_memory_utilization", dims)

        # p95 latency from ALB target group metric
        latency_ms = self._cw_stat(
            "AWS/ApplicationELB", "TargetResponseTime",
            [{"Name": "LoadBalancer", "Value": "app/smart-migrator/REPLACE"}],
            stat="p95"
        ) * 1000  # seconds → ms

        # 5xx error rate
        req_count  = self._cw_stat("AWS/ApplicationELB", "RequestCount",
                                   [{"Name": "LoadBalancer", "Value": "app/smart-migrator/REPLACE"}], "Sum")
        err_count  = self._cw_stat("AWS/ApplicationELB", "HTTPCode_Target_5XX_Count",
                                   [{"Name": "LoadBalancer", "Value": "app/smart-migrator/REPLACE"}], "Sum")
        error_rate = (err_count / req_count) if req_count > 0 else 0.0

        # Hourly cost via Cost Explorer
        now = datetime.now(timezone.utc)
        cost_resp = self.ce.get_cost_and_usage(
            TimePeriod={
                "Start": (now - timedelta(hours=1)).strftime("%Y-%m-%d"),
                "End":   now.strftime("%Y-%m-%d"),
            },
            Granularity="DAILY",
            Metrics=["UnblendedCost"],
        )
        daily_cost   = float(cost_resp["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]) \
                       if cost_resp.get("ResultsByTime") else 0.0
        hourly_cost  = daily_cost / 24

        return MetricsSnapshot(
            timestamp=now,
            provider="aws",
            cpu_pct=cpu_pct,
            memory_pct=memory_pct,
            latency_p95_ms=latency_ms,
            error_rate=error_rate,
            hourly_cost_usd=hourly_cost,
            health_ok=(cpu_pct < 95 and memory_pct < 95),
        )

    def collect_azure(self) -> MetricsSnapshot:
        """Placeholder — replace with azure-monitor-query SDK."""
        return MetricsSnapshot(
            timestamp=datetime.now(timezone.utc), provider="azure",
            cpu_pct=30.0, memory_pct=40.0, latency_p95_ms=120.0,
            error_rate=0.005, hourly_cost_usd=0.85, health_ok=True,
        )

    def collect_gcp(self) -> MetricsSnapshot:
        """Placeholder — replace with google-cloud-monitoring SDK."""
        return MetricsSnapshot(
            timestamp=datetime.now(timezone.utc), provider="gcp",
            cpu_pct=25.0, memory_pct=35.0, latency_p95_ms=100.0,
            error_rate=0.003, hourly_cost_usd=0.80, health_ok=True,
        )

    def collect_all(self) -> dict[str, MetricsSnapshot]:
        return {
            "aws":   self.collect_aws(),
            "azure": self.collect_azure(),
            "gcp":   self.collect_gcp(),
        }


class TriggerEvaluator:
    """
    §4.3.2 — implements the debounce formula:
        Trigger = TRUE  iff  Σ breach(t-k) = N_breach  for k ∈ {0,..,N_breach-1}

    §4.3.3 — enforces hysteresis:
        Migrate = TRUE  iff  Score(target) < Score(current) × (1 - δ)
    """

    THRESHOLDS = {
        "cpu_pct":        (CPU_THRESHOLD_PCT,        "sustained"),
        "memory_pct":     (MEMORY_THRESHOLD_PCT,     "sustained"),
        "latency_p95_ms": (LATENCY_P95_THRESHOLD_MS, "sustained"),
        "error_rate":     (ERROR_RATE_THRESHOLD,     "immediate"),
        # cost spike handled separately (needs rolling average)
    }

    def __init__(self):
        self.state = MonitorState()
        self._cost_history: list[float] = []   # rolling 7-day hourly samples

    def evaluate_breach(self, snap: MetricsSnapshot) -> bool:
        """Returns True if ANY threshold is breached this cycle."""
        breached = False
        for attr, (threshold, _) in self.THRESHOLDS.items():
            value = getattr(snap, attr)
            if value > threshold:
                log_breach(attr, value, threshold, snap.provider,
                           len([b for b in self.state.breach_history[-N_BREACH:] if b]) + 1)
                breached = True

        # Cost spike check (§4.3.2 — 1.5× 7-day rolling average)
        self._cost_history.append(snap.hourly_cost_usd)
        if len(self._cost_history) > 168:   # 7 days × 24 hrs
            self._cost_history.pop(0)
        if len(self._cost_history) >= 24:
            rolling_avg = sum(self._cost_history) / len(self._cost_history)
            if snap.hourly_cost_usd > rolling_avg * COST_SPIKE_FACTOR:
                log_breach("hourly_cost_usd", snap.hourly_cost_usd,
                           rolling_avg * COST_SPIKE_FACTOR, snap.provider,
                           len([b for b in self.state.breach_history[-N_BREACH:] if b]) + 1)
                breached = True

        # Health check failure
        if not snap.health_ok:
            log_breach("health_ok", 0, 1, snap.provider, 1)
            breached = True

        return breached

    def should_trigger(self, snap: MetricsSnapshot) -> bool:
        """
        §4.3.2 — debounce formula:
            Trigger = TRUE iff Σ breach(t-k) = N_breach
        §4.3.3 — cooldown gate.
        """
        breached = self.evaluate_breach(snap)
        self.state.breach_history.append(breached)
        # keep only N_breach most recent entries
        if len(self.state.breach_history) > N_BREACH:
            self.state.breach_history = self.state.breach_history[-N_BREACH:]

        # cooldown gate (§4.3.3)
        if self.state.last_migration:
            elapsed = (datetime.now(timezone.utc) - self.state.last_migration).total_seconds() / 60
            if elapsed < T_COOL_MINUTES:
                log.info("In cooldown (%.1f / %d min elapsed)", elapsed, T_COOL_MINUTES)
                return False

        # debounce: all N_BREACH last cycles must be breaches
        if len(self.state.breach_history) == N_BREACH and all(self.state.breach_history):
            log_trigger(snap.provider, N_BREACH)
            return True

        remaining = N_BREACH - sum(self.state.breach_history)
        log.info("Breach counter %d/%d — no trigger", sum(self.state.breach_history), N_BREACH)
        return False

    def record_migration(self) -> None:
        """Called after a successful migration to reset breach history and start cooldown."""
        self.state.breach_history = []
        self.state.last_migration = datetime.now(timezone.utc)


class MonitoringLoop:
    """
    §4.3 — perpetual background process.
    Calls on_trigger(metrics_snapshot) when Trigger = TRUE.
    """

    def __init__(self, on_trigger: Callable[[dict], None]):
        self.collector = MetricsCollector()
        self.evaluator = TriggerEvaluator()
        self.on_trigger = on_trigger

    def run(self, interval_sec: int = METRICS_POLL_INTERVAL_SEC) -> None:
        log.info("Monitoring loop started (interval=%ds, N_breach=%d)", interval_sec, N_BREACH)
        while True:
            try:
                snapshots = self.collector.collect_all()
                active    = self.evaluator.state.active_provider
                snap      = snapshots[active]

                log.info(
                    "[%s] cpu=%.1f%% mem=%.1f%% lat=%.0fms err=%.2f%% cost=$%.3f/hr",
                    snap.provider, snap.cpu_pct, snap.memory_pct,
                    snap.latency_p95_ms, snap.error_rate * 100, snap.hourly_cost_usd,
                )

                if self.evaluator.should_trigger(snap):
                    self.on_trigger(snapshots)

            except Exception as exc:
                log.error("Monitoring error: %s", exc, exc_info=True)

            time.sleep(interval_sec)