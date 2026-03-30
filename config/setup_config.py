"""
config/setup_config.py
───────────────────────
Run this script ONCE after infrastructure is created.
It fetches the real ECR URI and Route 53 Hosted Zone ID from AWS
and writes them into settings.py automatically — no manual editing needed.

Usage:
    cd ~/smart-migrator
    python config/setup_config.py

What it does:
  1. Creates the ECR repository if it doesn't exist
  2. Creates a Route 53 private hosted zone for smartmigrator.local
  3. Fetches both real values
  4. Rewrites the PENDING lines in config/settings.py with the real values
  5. Prints a summary of everything that was set
"""

import boto3, re, sys, os
from pathlib import Path

ACCOUNT_ID       = "408258691668"
AWS_REGION       = "ap-south-1"
ECR_REPO_NAME    = "smart-migrator-app"
DOMAIN           = "smartmigrator.local"
SETTINGS_PATH    = Path(__file__).parent / "settings.py"


def step(n, msg):
    print(f"\n[{n}] {msg}")


def create_ecr_repo(ecr_client) -> str:
    """Create ECR repo if it doesn't exist. Returns the repositoryUri."""
    try:
        resp = ecr_client.create_repository(
            repositoryName=ECR_REPO_NAME,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="MUTABLE",
        )
        uri = resp["repository"]["repositoryUri"]
        print(f"    Created ECR repo: {uri}")
        return uri
    except ecr_client.exceptions.RepositoryAlreadyExistsException:
        resp = ecr_client.describe_repositories(repositoryNames=[ECR_REPO_NAME])
        uri = resp["repositories"][0]["repositoryUri"]
        print(f"    ECR repo already exists: {uri}")
        return uri


def create_hosted_zone(r53_client) -> str:
    """
    Create a private hosted zone for smartmigrator.local.
    Returns the hosted zone ID (without the /hostedzone/ prefix).
    """
    # Check if it already exists
    zones = r53_client.list_hosted_zones_by_name(DNSName=DOMAIN)
    for zone in zones.get("HostedZones", []):
        if zone["Name"].rstrip(".") == DOMAIN:
            zone_id = zone["Id"].split("/")[-1]
            print(f"    Hosted zone already exists: {zone_id}")
            return zone_id

    # Need a VPC ID to create a private hosted zone
    # Use the default VPC in ap-south-1
    ec2 = boto3.client("ec2", region_name=AWS_REGION)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id = vpcs["Vpcs"][0]["VpcId"] if vpcs["Vpcs"] else None

    if not vpc_id:
        print("    WARNING: No default VPC found — creating public hosted zone instead")
        resp = r53_client.create_hosted_zone(
            Name=DOMAIN,
            CallerReference=f"smart-migrator-{ACCOUNT_ID}",
            HostedZoneConfig={"Comment": "Smart Migrator test zone", "PrivateZone": False},
        )
    else:
        resp = r53_client.create_hosted_zone(
            Name=DOMAIN,
            CallerReference=f"smart-migrator-{ACCOUNT_ID}",
            HostedZoneConfig={"Comment": "Smart Migrator private zone", "PrivateZone": True},
            VPC={"VPCRegion": AWS_REGION, "VPCId": vpc_id},
        )

    zone_id = resp["HostedZone"]["Id"].split("/")[-1]
    print(f"    Created hosted zone: {zone_id}  (domain: {DOMAIN})")
    return zone_id


def patch_settings(ecr_uri: str, zone_id: str) -> None:
    """Rewrite the two PENDING lines in settings.py with real values."""
    text = SETTINGS_PATH.read_text()

    text = re.sub(
        r'^HOSTED_ZONE_ID\s*=\s*".*"',
        f'HOSTED_ZONE_ID = "{zone_id}"',
        text, flags=re.MULTILINE
    )
    text = re.sub(
        r'^ECR_REPO\s*=\s*".*"',
        f'ECR_REPO       = "{ecr_uri}"',
        text, flags=re.MULTILINE
    )

    SETTINGS_PATH.write_text(text)
    print(f"    settings.py updated ✓")


def main():
    print("=" * 60)
    print("  Smart Migrator — Config Setup")
    print(f"  Account: {ACCOUNT_ID}  |  Region: {AWS_REGION}")
    print("=" * 60)

    ecr = boto3.client("ecr",     region_name=AWS_REGION)
    r53 = boto3.client("route53", region_name=AWS_REGION)

    step(1, "Setting up ECR repository...")
    ecr_uri = create_ecr_repo(ecr)

    step(2, f"Setting up Route 53 hosted zone for {DOMAIN}...")
    zone_id = create_hosted_zone(r53)

    step(3, "Writing values into config/settings.py...")
    patch_settings(ecr_uri, zone_id)

    step(4, "Verifying settings.py...")
    # Quick import check
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import importlib
    import config.settings as s
    importlib.reload(s)
    assert s.ECR_REPO       != "PENDING_RUN_SETUP_CONFIG", "ECR_REPO not set"
    assert s.HOSTED_ZONE_ID != "PENDING_RUN_SETUP_CONFIG", "HOSTED_ZONE_ID not set"

    print("\n" + "=" * 60)
    print("  Setup complete! Values written:")
    print(f"  ECR_REPO       = {ecr_uri}")
    print(f"  HOSTED_ZONE_ID = {zone_id}")
    print(f"  DOMAIN         = {DOMAIN}")
    print(f"  AWS_REGION     = {AWS_REGION}")
    print(f"  ACCOUNT_ID     = {ACCOUNT_ID}")
    print("=" * 60)
    print("\nNext step: python main.py onboard --app ./app --tag v1.0")


if __name__ == "__main__":
    main()