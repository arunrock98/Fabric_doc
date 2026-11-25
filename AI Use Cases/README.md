# AI Use Cases

End-to-end implementation blueprints for AI-augmented processes — from business framing through architecture, data, policy, agent design, governance, deployment, and monitoring.

Each case is generic and employer-neutral. Where a case uses Microsoft Fabric, it references the platform patterns in the sibling folders (`sap-to-fabric/`, `semantic-models/`, `governance/`, `performance/`, `ai/`, `ai/claude/`).

## Cases

- [`credit-block-release-agent/`](credit-block-release-agent/README.md) — AI-assisted release of SAP credit blocks: rules + AI fusion, shadow-mode evaluation, human-in-the-loop, SAP write-back, kill switch.

- [`real-time-sensor-analytics-tiger-cloud/`](real-time-sensor-analytics-tiger-cloud/README.md) — IoT sensor analytics + AI hybrid search on Tiger Cloud (PostgreSQL + TimescaleDB): agent-driven provisioning via MCP, hypertables, seasonality-adjusted anomaly detection, continuous aggregates, compression, forks, BM25 + vector RRF search — with a Fabric RTI mapping.

_More cases land here as I write them up._
