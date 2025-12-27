# TriggerPy

TriggerPy is a standalone Python application for managing and executing
live, condition-based trading operations.

It is designed as a **single-process, in-memory runtime** with explicit
service orchestration, real-time market watchers, and operator supervision.

TriggerPy is not a persistence-driven system and does not rely on databases
as a source of truth.

---

## Core Design Principles

- In-memory state is authoritative
- Explicit lifecycle control over abstraction
- Event- and callback-driven execution
- Thread-based concurrency (no async frameworks)
- Operator-visible runtime state
- Designed for standalone EXE packaging

---

## Architecture Overview

### Runtime Control
- `runtime_manager.py`  
  Global run/stop switch controlling all threads and services.

### Watchers & Live State
- `watcher_info.py`  
  In-memory registry of all active watcher threads and their status.
- `price_watcher.py`  
  Live price polling with UI callbacks.

### Execution & Concurrency
- `thread_pool.py`  
  Custom serialized thread pool with explicit shutdown and execution control.
- `callback_manager.py`  
  Threaded callback dispatch for market events.

### Order & Strategy Services
- `order_manager.py`
- `order_queue_service.py`
- `order_wait_service.py`
- `order_fixer_service.py`
- `options_manager.py`
- `amo_service.py`  
  (Phase-locked service registry ensuring safe access after graph completion)

### Market Integrations
- `tws_service.py`  
  Interactive Brokers (IBKR) integration — live orders, positions, callbacks.
- `polygon_service.py`
- `nasdaq_info.py`

### Domain & UI
- `model.py`  
  Shared in-memory application state.
- `view.py`, `opmng_ui.py`  
  Operator-facing GUI components.

---

## Persistence Model (Important)

TriggerPy does **not** use persistence as an architectural pillar.

- Runtime behavior is driven entirely by live memory and events.
- Any local files or artifacts are auxiliary and non-authoritative.
- If the process stops, runtime state is intentionally lost.

This is a deliberate design choice for correctness and clarity.

---

## Running

```bash
python main.py
