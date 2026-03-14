import time
from typing import Dict
from database import get_recent_failures, log_api_call


FAILURE_THRESHOLD = 3
RECOVERY_TIMEOUT = {
    "timeout":    60, 
    "rate_limit": 300, 
    "server_error": 120 
}

# Structure: { "unpaywall": { "state": "CLOSED", "opened_at": None, "reason": None } }
_circuit_state: Dict[str, dict] = {
    "unpaywall":         {"state": "CLOSED", "opened_at": None, "reason": None},
    "openalex":          {"state": "CLOSED", "opened_at": None, "reason": None},
    "semantic_scholar":  {"state": "CLOSED", "opened_at": None, "reason": None},
}

# CLOSED  → working normally, all calls go through
# OPEN    → source failed recently, skip it entirely
# HALF    → recovery timeout passed, try one call to test

def get_state(source: str) -> str:
    """Returns the current circuit state for a source."""
    circuit = _circuit_state.get(source)
    if not circuit:
        return "CLOSED"

    if circuit["state"] == "OPEN":
        recovery = RECOVERY_TIMEOUT.get(circuit["reason"], 60)
        elapsed = time.time() - (circuit["opened_at"] or 0)

        if elapsed >= recovery:
            _circuit_state[source]["state"] = "HALF"
            print(f"⚡ Circuit HALF-OPEN for {source} — testing recovery")
            return "HALF"

    return circuit["state"]


def should_skip(source: str) -> bool:
    """
    Returns True if this source should be skipped.
    Called before every API request.
    """
    state = get_state(source)

    if state == "OPEN":
        print(f"⚡ Circuit OPEN for {source} — skipping")
        return True

    return False 

def record_success(source: str):
    """
    Called after a successful API response.
    Resets the circuit to CLOSED.
    """
    circuit = _circuit_state.get(source)
    if not circuit:
        return

    if circuit["state"] in ("OPEN", "HALF"):
        print(f"✅ Circuit CLOSED for {source} — recovered")

    _circuit_state[source] = {
        "state": "CLOSED",
        "opened_at": None,
        "reason": None
    }


def record_failure(source: str, reason: str, status_code: int = None):
    """
    Called after a failed API response.
    Opens the circuit if failure threshold is exceeded.

    reason: "timeout" | "rate_limit" | "server_error"
    """
    if not hasattr(record_failure, "_counts"):
        record_failure._counts = {}
    record_failure._counts[source] = record_failure._counts.get(source, 0) + 1
    recent_failures = record_failure._counts[source]

    if recent_failures >= FAILURE_THRESHOLD or _circuit_state[source]["state"] == "HALF":
        _circuit_state[source] = {
            "state": "OPEN",
            "opened_at": time.time(),
            "reason": reason
        }
        recovery = RECOVERY_TIMEOUT.get(reason, 60)
        print(f"🔴 Circuit OPEN for {source} — reason: {reason}, recovery in {recovery}s")
    else:
        print(f"⚠️  Failure recorded for {source} ({recent_failures + 1}/{FAILURE_THRESHOLD}) — reason: {reason}")


def classify_failure(status_code: int = None, is_timeout: bool = False) -> str:
    """
    Classifies a failure type based on status code or timeout flag.
    Returns: "timeout" | "rate_limit" | "server_error"
    """
    if is_timeout:
        return "timeout"
    if status_code == 429:
        return "rate_limit"
    if status_code and status_code >= 500:
        return "server_error"
    return "server_error" 

def aggregate_results(results: list, sources: list) -> dict:
    """
    Takes raw results from asyncio.gather (which may include
    exceptions) and returns a clean summary.

    results: list of dicts or Exceptions from gather()
    sources: list of source names in same order as results
    """
    available = []
    failed = []
    data = {}

    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            failed.append(source)
            record_failure(source, reason="server_error")
            print(f"❌ {source} raised exception: {result}")
        elif result is None:
            failed.append(source)
            print(f"❌ {source} returned None")
        else:
            available.append(source)
            record_success(source)
            data[source] = result

    is_partial = len(failed) > 0
    all_failed = len(available) == 0

    if all_failed:
        print("🔴 All sources failed — returning empty result")

    return {
        "data": data,
        "sources_available": available,
        "sources_failed": failed,
        "partial_result": is_partial,
        "all_failed": all_failed
    }


def get_circuit_status() -> dict:
    """
    Returns current state of all circuits.
    Useful for debugging and health checks.
    """
    status = {}
    for source, circuit in _circuit_state.items():
        state = get_state(source)
        status[source] = {
            "state": state,
            "opened_at": circuit["opened_at"],
            "reason": circuit["reason"]
        }
    return status