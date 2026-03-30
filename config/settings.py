"""
config/settings.py
──────────────────
Configured for:
  AWS Account : 408258691668
  Region      : ap-south-1 (Mumbai)
  Domain      : smartmigrator.local  (test domain — swap with real one later)
  EKS Cluster : smart-migrator-cluster

All methodology constants (N_breach, δ, TTL, weights …) are unchanged from
the paper. Only the AWS-specific identifiers below have been filled in.

⚠  HOSTED_ZONE_ID and ECR_REPO are filled in AFTER you run:
     Step A → terraform apply   (creates the cluster)
     Step B → aws ecr create-repository  (creates the registry)
     Step C → aws route53 create-hosted-zone  (creates the DNS zone)
   The setup_config.py script (in this folder) fetches and writes them for you.
"""

from typing import Dict

# ── §4.3.2  Trigger thresholds ────────────────────────────────────────────
CPU_THRESHOLD_PCT        = 80.0    # %
MEMORY_THRESHOLD_PCT     = 85.0    # %
LATENCY_P95_THRESHOLD_MS = 500.0   # ms
ERROR_RATE_THRESHOLD     = 0.05    # fraction (5%)
COST_SPIKE_FACTOR        = 1.5     # 1.5× 7-day rolling average
HEALTH_FAIL_CONSECUTIVE  = 2       # consecutive Route53 failures

# Evaluation windows
EVAL_WINDOW_SUSTAINED_MIN = 5
EVAL_WINDOW_COST_HR       = 1
METRICS_POLL_INTERVAL_SEC = 60

# ── §4.3.2  Debounce ──────────────────────────────────────────────────────
N_BREACH = 3

# ── §4.3.3  Anti-flapping ─────────────────────────────────────────────────
T_COOL_MINUTES        = 15
DELTA_MIN_IMPROVEMENT = 0.10

# ── §4.4.3  Scoring weights w₁…w₅ ────────────────────────────────────────
WEIGHTS: Dict[str, float] = {
    "cost":         0.30,
    "latency":      0.25,
    "availability": 0.25,
    "policy":       0.10,
    "resource":     0.10,
}

# ── §4.4.4  Hard-constraint thresholds ───────────────────────────────────
R_MIN_RESOURCE_INDEX = 0.20

# ── §4.5  Deployment timeouts ─────────────────────────────────────────────
T_DEPLOY_SEC   = 300
T_VERIFY_SEC   = 120
CONSECUTIVE_OK = 3
HEALTH_POLL_DT = 10

# ── §4.6  DNS ─────────────────────────────────────────────────────────────
T_TTL_SEC              = 30
DNS_WEIGHT_SRC_PARTIAL = 200
DNS_WEIGHT_TGT_PARTIAL = 55
DNS_WEIGHT_FULL        = 255
DNS_WEIGHT_ZERO        = 0
T_VERIFY_DNS_SEC       = 60
T_HOLD_SEC             = 600
TERMINATION_GRACE_SEC  = 60

# ── Cloud providers ───────────────────────────────────────────────────────
PROVIDERS = ["aws", "azure", "gcp"]

# ════════════════════════════════════════════════════════════════════════════
#  AWS IDENTITY — filled in from your aws sts get-caller-identity output
# ════════════════════════════════════════════════════════════════════════════
ACCOUNT_ID       = "408258691668"
AWS_REGION       = "ap-south-1"
EKS_CLUSTER_NAME = "smart-migrator-cluster"

# ── These three are populated by setup_config.py after infra is created ───
# Run:  python config/setup_config.py
# It will fetch real values and rewrite these lines automatically.
HOSTED_ZONE_ID = "PENDING_RUN_SETUP_CONFIG"
DOMAIN         = "smartmigrator.local"      # test domain — no real DNS needed
ECR_REPO       = "PENDING_RUN_SETUP_CONFIG"

# ── Kubeconfig context (written by aws eks update-kubeconfig) ─────────────
# This exact ARN format is what EKS creates in ~/.kube/config
KUBE_CONTEXTS = {
    "aws":   f"arn:aws:eks:{AWS_REGION}:{ACCOUNT_ID}:cluster/{EKS_CLUSTER_NAME}",
    "azure": "smart-migrator-aks",           # fill in after Azure setup
    "gcp":   "smart-migrator-gke",           # fill in after GCP setup
}

# ── Helm / K8s ────────────────────────────────────────────────────────────
HELM_RELEASE = "smart-migrator-app"
NAMESPACE    = "production"
HELM_CHART   = "./charts/app"

# ── Audit log ─────────────────────────────────────────────────────────────
AUDIT_LOG_PATH = "./audit/migration_events.jsonl"