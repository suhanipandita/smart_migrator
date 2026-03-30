# Smart Multi-Cloud Workload Migrator

## Overview
The Smart Multi-Cloud Workload Migrator is an intelligent system that enables organizations to dynamically manage and relocate containerized applications across heterogeneous cloud providers (AWS, Azure, GCP). It avoids vendor lock-in by standardizing workloads to containers and executing traffic redirection via DNS endpoints.

Driven by a Smart Decision Engine, the system continuously monitors infrastructure, identifies optimal deployment environments based on cost and performance, and safely orchestrates workload migrations without downtime.

## 5-Phase Architecture
The system operates sequentially through five methodological phases:
1. **Phase 1: Containerization & Assessment**: Legacy workloads are adapted into containers, verified for health endpoints, and pushed to a centralized registry.
2. **Phase 2: Continuous Monitoring**: Operational metrics are continuously gathered (CPU, memory, latency, cost). Threshold breaches trigger migration evaluations.
3. **Phase 3: Multi-Criteria Cloud Selection**: A Decision Engine computes optimal targets using a weighted scoring model considering cost, latency, availability, policy, and capacity constraints.
4. **Phase 4: Migration Orchestration**: Workloads are safely deployed to the target cluster via Helm with atomic rollbacks protecting against failed deployments.
5. **Phase 5: DNS Cutover**: Traffic is intelligently redirected to the new environment utilizing low-TTL DNS updates and weighted routing for seamless service continuity.

## Project Structure

```
smart-migrator/
├── main.py                          # CLI entry point (onboard/run/migrate)
├── web/
│   ├── app.py                       # Flask backend API (REST endpoints)
│   └── templates/
│       └── index.html               # Web dashboard (frontend)
├── phase1_containerization/
│   └── assessor.py                  # Workload assessment & containerization
├── phase2_monitoring/
│   └── monitor.py                   # Metrics collection & trigger evaluation
├── phase3_decision/
│   └── engine.py                    # Multi-criteria cloud selection (Algorithm 1)
├── phase4_orchestration/
│   └── orchestrator.py              # Helm-based deployment & health verification
├── phase5_dns/
│   └── dns_manager.py               # DNS cutover with weighted routing (Algorithm 3)
├── config/
│   └── settings.py                  # All methodology constants & cloud config
├── audit/
│   └── logger.py                    # Structured JSON audit logging
├── app/                             # Sample containerized application
├── charts/                          # Helm chart for Kubernetes deployment
├── terraform/                       # Infrastructure as Code (EKS cluster)
├── architecture.py                  # ASCII architecture diagram
├── dashboard.py                     # Terminal TUI dashboard (curses)
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

## Prerequisites
- macOS / Linux
- Python 3.10+
- `pip3 install flask` (for web dashboard)

## Quick Start — Web Dashboard

```bash
# Install dependencies
pip3 install flask

# Start the server
cd smart-migrator
PYTHONPATH=. python3 web/app.py

# Open in browser
open http://localhost:5050
```

The web dashboard provides:
- **📊 Dashboard**: Live cloud provider monitoring with real-time CPU, memory, latency charts
- **📈 Analysis**: Multi-criteria scoring breakdown, cost comparison, radar charts
- **🚀 Migration**: One-click migration controls and migration history
- **📋 Logs**: Real-time metrics collection log

## CLI Usage

### Onboarding a new workload (Phase 1)
```bash
python main.py onboard --app ./app --tag v1.0 --verify-health --prestage-dns
```

### Starting the monitoring loop (Phases 2-5)
```bash
python main.py run --provider aws
```

### Manual Migration (Phases 3-5)
```bash
python main.py migrate --source aws --target azure --tag v1.0
```

### Terminal Architecture Diagram
```bash
python architecture.py
```

## System Dependencies
- `flask` — Web framework for the dashboard
- `requests` — HTTP client for health checks
- `boto3` — AWS SDK (Route 53, CloudWatch, ECR)
- Standard tools: `kubectl`, `helm`, `Docker` (for production deployment)

## Algorithm Reference
- **Algorithm 1** (§4.4): Multi-Criteria Cloud Provider Selection with weighted scoring
- **Algorithm 2** (§4.5.3): Post-Deployment Health Verification (consecutive_ok ≥ 3)
- **Algorithm 3** (§4.6.2): DNS Traffic Cutover with Weighted Routing (200:55 → 0:255)
