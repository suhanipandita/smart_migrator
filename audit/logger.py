"""
audit/logger.py
───────────────
§4.7 — Structured audit log.
Every migration event, rollback, and breach is written as a JSON line to
AUDIT_LOG_PATH. Fields match those specified in the methodology:
  migration_event_id, source, target, failure_stage, failure_reason,
  timestamp_start, timestamp_end, resulting_state.
"""

import json, uuid, logging
from datetime import datetime, timezone
from pathlib import Path
from config.settings import AUDIT_LOG_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _write(record: dict) -> None:
    Path(AUDIT_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(record) + "\n")

def log_breach(metric: str, value: float, threshold: float,
               provider: str, breach_count: int) -> None:
    record = {
        "event":        "threshold_breach",
        "timestamp":    _now(),
        "provider":     provider,
        "metric":       metric,
        "value":        round(value, 4),
        "threshold":    threshold,
        "breach_count": breach_count,
    }
    logging.getLogger("monitor").warning(
        "BREACH #%d  %s=%.2f (threshold %.2f) on %s",
        breach_count, metric, value, threshold, provider
    )
    _write(record)

def log_trigger(provider: str, breach_count: int) -> None:
    record = {
        "event":        "migration_trigger",
        "timestamp":    _now(),
        "provider":     provider,
        "breach_count": breach_count,
    }
    logging.getLogger("monitor").warning(
        "TRIGGER fired after %d consecutive breaches on %s", breach_count, provider
    )
    _write(record)

def log_migration_start(event_id: str, source: str, target: str,
                         source_score: float, target_score: float) -> None:
    record = {
        "event":            "migration_start",
        "migration_event_id": event_id,
        "timestamp_start":  _now(),
        "source":           source,
        "target":           target,
        "source_score":     round(source_score, 4),
        "target_score":     round(target_score, 4),
    }
    logging.getLogger("orchestrator").info(
        "MIGRATION START  %s → %s  (score %.3f → %.3f)",
        source, target, source_score, target_score
    )
    _write(record)

def log_migration_complete(event_id: str, source: str, target: str,
                            duration_sec: float) -> None:
    record = {
        "event":              "migration_complete",
        "migration_event_id": event_id,
        "timestamp_end":      _now(),
        "source":             source,
        "target":             target,
        "duration_sec":       round(duration_sec, 1),
        "resulting_state":    target,
    }
    logging.getLogger("orchestrator").info(
        "MIGRATION COMPLETE  %s → %s  (%.0fs)", source, target, duration_sec
    )
    _write(record)

def log_rollback(event_id: str, source: str, target: str,
                 failure_stage: str, failure_reason: str) -> None:
    record = {
        "event":              "rollback",
        "migration_event_id": event_id,
        "timestamp":          _now(),
        "source":             source,
        "target":             target,
        "failure_stage":      failure_stage,
        "failure_reason":     failure_reason,
        "resulting_state":    source,
    }
    logging.getLogger("orchestrator").error(
        "ROLLBACK  stage=%s  reason=%s  → restored %s",
        failure_stage, failure_reason, source
    )
    _write(record)

def log_selection(provider: str, score: float, reason: str) -> None:
    record = {
        "event":     "provider_selected",
        "timestamp": _now(),
        "provider":  provider,
        "score":     round(score, 4),
        "reason":    reason,
    }
    logging.getLogger("decision").info(
        "SELECTED  %s  score=%.4f  (%s)", provider, score, reason
    )
    _write(record)

def new_event_id() -> str:
    return str(uuid.uuid4())