# SentinelAI — Production-Grade LLM Gateway

> A system-level gateway that sits between your application and LLM providers to handle **reliability, performance optimization, cost control, and observability**.

---

##  Overview

Modern AI applications often call LLM APIs directly. This works for demos—but breaks down in production:

* ❌ No fallback when providers fail
* ❌ Repeated requests waste cost and time
* ❌ No visibility into latency, failures, or usage
* ❌ No control over prompt behavior or experimentation

**SentinelAI solves this by acting as a centralized control layer for all LLM interactions.**

---

## 🧠 What SentinelAI Does

### ⚡ Semantic Caching

Avoids redundant LLM calls by serving similar requests from cache.

* Cache hit latency: **~15–50ms**
* LLM call latency: **~1500–2500ms**
* Dramatically reduces cost and response time

---

### 🛠️ Circuit Breaker + Failover

Ensures system reliability when providers fail.

* Detects repeated failures
* Automatically switches to backup provider (e.g., Groq → Gemini)
* Prevents cascading failures

---

### 💸 Cost Tracking

Tracks estimated cost per request based on token usage and provider pricing.

* Per-request cost
* Aggregated cost insights
* Cache savings visibility

---

### 🔍 Observability & Tracing

Logs every request with full metadata:

* provider used
* latency
* cache hit / miss
* fallback events
* request + response trace

---

### 📊 Real-Time Dashboard

A production-style observability dashboard that visualizes:

* latency trends
* cache performance
* provider usage
* request logs
* system impact metrics

---

## 📈 Live Performance (from load test)

| Metric                  | Value                                 |
| ----------------------- | ------------------------------------- |
| Cache Hit Rate          | ~57%                                  |
| Avg Latency (Cache Hit) | ~15–50ms                              |
| Avg Latency (LLM Call)  | ~1.5–2.5s                             |
| Latency Reduction       | **98%+**                              |
| Fallback Handling       | Zero downtime during provider failure |

---

## 🏗️ System Architecture

```plaintext
Client / Dashboard
        ↓
   SentinelAI Gateway
        ↓
 ┌─────────────────────┐
 │ Prompt Routing      │
 │ Semantic Cache      │
 │ Circuit Breaker     │
 │ Provider Abstraction│
 └─────────────────────┘
        ↓
  LLM Providers (Groq / Gemini / etc.)
        ↓
 Logging & Metrics Storage
        ↓
 Observability Dashboard
```

---

## ⚙️ Key Engineering Concepts

SentinelAI is not just an API wrapper—it demonstrates:

* **LLM infrastructure design**
* **Latency optimization through caching**
* **Fault tolerance via circuit breakers**
* **Observability-first architecture**
* **Separation of control layer vs model layer**

---

## 🧪 Load Testing

The system was benchmarked using concurrent simulated traffic:

* Mixed workload (repeated + unique prompts)
* Cache hit/miss behavior validated
* Latency improvements measured under load
* Failover behavior tested during simulated provider outages

---

## 🚀 Quick Start

### Prerequisites

* Docker
* Docker Compose

---

### Run the system

```bash
git clone <your-repo-url>
cd SentinelAI
docker-compose up
```

---

### Access

* Backend API → http://localhost:8000
* Dashboard → http://localhost:3000

---

## 🔌 Example API Usage

```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Authorization: Bearer sentinel-dev-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "Explain Redis"}]
  }'
```

---

## 📌 Roadmap / Future Enhancements

* [ ] Redis-based distributed cache
* [ ] Celery-based async processing
* [ ] Prompt versioning & A/B testing
* [ ] PostgreSQL migration for scalability
* [ ] Advanced evaluation metrics for responses

---

## 🎯 Why This Project Matters

Most AI projects demonstrate *usage* of LLMs.
SentinelAI demonstrates **control, optimization, and production readiness**.

It answers:

> “How do you actually run LLMs reliably at scale?”

---

## 👨‍💻 Author

Built as a production-oriented system to explore real-world LLM infrastructure challenges.

---
