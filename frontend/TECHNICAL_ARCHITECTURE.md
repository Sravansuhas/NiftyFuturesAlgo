# Aegis — Master Technical Architecture & System Design (2026)

This document serves as the absolute technical blueprint for the Aegis algorithmic trading system. It translates the Product Requirements Document (PRD) into a highly scalable, edge-native, AI-driven architectural standard suitable for deployment in 2026.

---

## 1. Architectural Philosophy (2026 Standards)
Modern algorithmic trading systems require a strict separation between the **"Hot Path"** (latency-sensitive execution) and the **"Control Plane"** (AI orchestration, analytics, and UX). 

*   **The Hot Path (Execution Engine):** A deterministic, async "Modular Monolith" written in Python (FastAPI/asyncio) designed for sub-millisecond tick processing. It bypasses network mesh hops entirely.
*   **The Control Plane (Super Brain):** A Kubernetes-orchestrated microservices cluster managing GenAI backtesting, strategy formulation, and the React frontend.
*   **Edge-Native Infrastructure:** Containers deployed as close to the NSE exchange servers (colocation) as possible to minimize fiber physics latency.

---

## 2. Core System Components

### 2.1 Frontend (The Command Center)
*   **Stack:** React 18, TypeScript, Vite.
*   **Design Paradigm:** OLED-optimized Dark Mode, Bento-Grid Layout, Progressive Disclosure.
*   **Key Integrations:** 
    *   WebSockets for real-time tick streaming and PnL updates.
    *   REST APIs for backtesting and AI Rationale ("Decision Logs").

### 2.2 API Gateway & Control Plane
*   **Stack:** Python FastAPI, Uvicorn, PostgreSQL (for user/trade metadata).
*   **Functions:**
    *   Authentication & JWT Management.
    *   Historical Data Retrieval.
    *   Routing requests to the LLM (Large Language Model) agents.

### 2.3 The "Hot Path" AI Engine (The Super Brain)
*   **Data Ingestion:** Asynchronous listener connected to Zerodha `KiteTicker` WebSocket. Parses binary packets into JSON instantly.
*   **Multi-Agent Mesh:**
    *   **Alpha Agent:** Analyzes live Greeks (Delta/Theta), PCR, and VIX. It identifies regimes (Trending vs Rangebound) and outputs proposed signals (e.g., *INITIATE NIFTY SHORT STRADDLE*).
    *   **Risk Agent:** The ultimate gatekeeper. Intercepts the Alpha Agent's signal, checks available margin via Kite API, verifies the "Max 5 Trades/Day" guardrail, and calculates the worst-case Max Loss. If approved, it forwards to execution.

---

## 3. Deployment & Containerization (Docker / K8s)

### 3.1 Multi-Stage Docker Builds
Images are hardened and minimized using Alpine/Slim variants.
```dockerfile
# Example Production Build
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY ./backend /app
ENV PATH=/root/.local/bin:$PATH
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3.2 Kubernetes (K8s) Architecture
*   **Zero-Trust Networking:** Using Cilium (eBPF) for default-deny network policies. The UI can only talk to the Gateway; the Gateway can only talk to the Engine.
*   **Node Affinity:** The Execution Engine is tainted/tolerated to run on high-compute, memory-optimized isolated nodes to prevent "noisy neighbor" latency spikes from the Backtesting engine.
*   **Autoscaling:** Predictive scaling based on VIX and time-of-day (e.g., spinning up more nodes right before Market Open or RBI policy announcements).

---

## 4. Backtesting Engine (GenAI & RAG)
*   **Storage:** 5+ years of Indian F&O tick data stored in massive **Parquet data lakes**.
*   **Querying:** Retrieval-Augmented Generation (RAG). Traders type: *"Test an Iron Condor on expiry days when VIX > 15"*.
*   **Processing:** The system translates natural language into `pandas`/`polars` queries against the Parquet lake, executing years of backtests in milliseconds, and returns visually rich SVGs and metrics (Sharpe, Sortino, Max Drawdown).

---

## 5. Comprehensive Edge Case & Error Handling

To survive the Indian capital markets, the system implements structural failsafes against the worst-case scenarios.

### 5.1 NSE / Broker API Failures
*   **Stale Tick Detection:** If the time-delta between incoming NSE ticks exceeds `3000ms`, the system enters **"Blind Mode"**. All new Alpha Agent entries are strictly paused.
*   **Rate Limits:** Kite Connect limits API requests (e.g., 10 req/sec for orders). The engine uses exponential backoff and jitter algorithms to enqueue and throttle requests, preventing `429 Too Many Requests` bans.

### 5.2 Market Anomalies (Black Swans)
*   **VIX Spikes & Flash Crashes:** If India VIX spikes > `15%` intraday, the Risk Agent overrides the Alpha Agent. It converts all pending Limit Exits to Market (SL-M) orders to prioritize capital preservation over slippage. 
*   **Freak Trades (Illiquidity):** The system completely outlaws naked Market Orders (`ORDER_TYPE_MARKET`) for option buying. All orders are `LIMIT` with a dynamically calculated safety buffer (LTP + 0.5%) to prevent getting filled at massive premiums due to shallow order books.

### 5.3 Capital & Margin Safety
*   **Theta Bleed Traps:** The system continuously monitors the Theta decay rate of active OTM options. If a purchased option loses >30% of its premium to time-decay without favorable underlying movement, an automatic "Square Off" signal is generated.
*   **Margin Shortfall Penalties:** Brokers charge massive penalties for intraday margin shortfalls. The Risk Agent polls `/user/margins` every 5 seconds. If Utilization > 92%, it initiates a "Strategic Shedding" sequence, closing the lowest-probability option leg to free up capital instantly.

---
*Document Version: 1.0 (Locked for V1 Build)*
*Author: Aegis Co-Founder & Lead AI Architect*
