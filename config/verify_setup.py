"""
config/verify_setup.py
───────────────────────
Run this first (launch config ⑥) to confirm everything is wired correctly
before spending time on terraform or docker.

Checks:
  ✓ Python version ≥ 3.10
  ✓ All imports resolve (boto3, requests, config.settings)
  ✓ AWS credentials are present and valid
  ✓ Correct region (ap-south-1)
  ✓ Correct account (408258691668)
  ✓ Docker is running
  ✓ kubectl is installed
  ✓ helm is installed
  ✓ terraform is installed
  ✓ settings.py has been populated (no PENDING values)
"""

import sys, subprocess, os

EXPECTED_ACCOUNT = "408258691668"
EXPECTED_REGION  = "ap-south-1"

PASS = "  ✓"
FAIL = "  ✗"

errors = []

def check(label, ok, detail=""):
    symbol = PASS if ok else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"{symbol}  {label}{suffix}")
    if not ok:
        errors.append(label)


def run(cmd) -> tuple[int, str]:
    r = subprocess.run(cmd, capture_output=True, text=True, shell=isinstance(cmd, str))
    return r.returncode, (r.stdout + r.stderr).strip()


print("\n━━━━  Smart Migrator — Setup Verification  ━━━━")
print(f"  Python  : {sys.version.split()[0]}")
print(f"  Expected: account={EXPECTED_ACCOUNT}  region={EXPECTED_REGION}\n")

# ── Python version ────────────────────────────────────────────────────────
check("Python ≥ 3.10", sys.version_info >= (3, 10),
      f"found {sys.version_info.major}.{sys.version_info.minor}")

# ── Imports ───────────────────────────────────────────────────────────────
try:
    import boto3
    check("boto3 importable", True, boto3.__version__)
except ImportError as e:
    check("boto3 importable", False, str(e))

try:
    import requests
    check("requests importable", True, requests.__version__)
except ImportError as e:
    check("requests importable", False, str(e))

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from config import settings as s
    check("config.settings importable", True)
except Exception as e:
    check("config.settings importable", False, str(e))
    s = None

# ── settings.py populated ─────────────────────────────────────────────────
if s:
    check("AWS_REGION = ap-south-1",     s.AWS_REGION == EXPECTED_REGION,       s.AWS_REGION)
    check("ACCOUNT_ID = 408258691668",   s.ACCOUNT_ID == EXPECTED_ACCOUNT,      s.ACCOUNT_ID)
    check("ECR_REPO populated",          "PENDING" not in s.ECR_REPO,           s.ECR_REPO[:50])
    check("HOSTED_ZONE_ID populated",    "PENDING" not in s.HOSTED_ZONE_ID,     s.HOSTED_ZONE_ID)
    check("DOMAIN set",                  bool(s.DOMAIN),                         s.DOMAIN)
    check("KUBE_CONTEXTS has aws entry", "aws" in s.KUBE_CONTEXTS)

# ── AWS credentials ───────────────────────────────────────────────────────
try:
    sts  = boto3.client("sts", region_name=EXPECTED_REGION)
    iden = sts.get_caller_identity()
    check("AWS credentials valid",       True,  iden["Account"])
    check("Correct AWS account",         iden["Account"] == EXPECTED_ACCOUNT,
                                         f"got {iden['Account']}")
except Exception as e:
    check("AWS credentials valid",       False, str(e)[:80])
    check("Correct AWS account",         False, "cannot verify — credentials failed")

# ── CLI tools ─────────────────────────────────────────────────────────────
for tool, cmd in [
    ("Docker running",    ["docker", "info"]),
    ("kubectl installed", ["kubectl", "version", "--client"]),
    ("helm installed",    ["helm", "version", "--short"]),
    ("terraform installed", ["terraform", "version"]),
    ("aws CLI installed", ["aws", "--version"]),
]:
    code, out = run(cmd)
    check(tool, code == 0, out.split("\n")[0][:60] if code == 0 else out[:60])

# ── Summary ───────────────────────────────────────────────────────────────
print()
if not errors:
    print("━━━━  All checks passed — ready to proceed!  ━━━━")
    print("\nNext step:")
    print("  If terraform not yet run:  cd terraform && terraform init && terraform apply")
    print("  If terraform done:         python config/setup_config.py")
else:
    print(f"━━━━  {len(errors)} check(s) failed  ━━━━")
    for e in errors:
        print(f"  ✗  {e}")
    print("\nFix the above before proceeding.")
    sys.exit(1)