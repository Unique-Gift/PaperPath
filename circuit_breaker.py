import time
from typing import Dict
from database import get_recent_failures, log_api_call

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------

# How many failures before we open the circuit (skip the source)
FAILURE_THRESHOLD = 3

# How long to keep the circuit OPEN before trying again (seconds)
RECOVERY_TIMEOUT = {
    "timeout":    60,   # Source took too long
    "rate_limit": 300,  # We got a 429 — back off for 5 minutes
    "server_error": 120 # Source returned 500
}

# In-memory state for each source
# Structure: { "unpaywall": { "state": "CLOSED", "opened_at": None, "reason": None } }
_circuit_state: Dict[str, dict] = {
    "unpaywall":         {"state": "CLOSED", "opened_at": None, "reason": None},
    "openalex":          {"state": "CLOSED", "opened_at": None, "reason": None},
    "semantic_scholar":  {"state": "CLOSED", "opened_at": None, "reason": None},
}


# ------------------------------------------------------------
# CIRCUIT STATES
#
# CLOSED  → working normally, all calls go through
# OPEN    → source failed recently, skip it entirely
# HALF    → recovery timeout passed, try one call to test
# ------------------------------------------------------------

def get_state(source: str) -> str:
    """Returns the current circuit state for a source."""
    circuit = _circuit_state.get(source)
    if not circuit:
        return "CLOSED"

    if circuit["state"] == "OPEN":
        # Check if recovery timeout has passed
        recovery = RECOVERY_TIMEOUT.get(circuit["reason"], 60)
        elapsed = time.time() - (circuit["opened_at"] or 0)

        if elapsed >= recovery:
            # Move to HALF — allow one test call
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

    return False  # CLOSED or HALF — allow the call


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
    # Track failures in memory
    if not hasattr(record_failure, "_counts"):
        record_failure._counts = {}
    record_failure._counts[source] = record_failure._counts.get(source, 0) + 1
    recent_failures = record_failure._counts[source]

    if recent_failures >= FAILURE_THRESHOLD or _circuit_state[source]["state"] == "HALF":
        # Open the circuit
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
    return "server_error"  # default


# ------------------------------------------------------------
# RESULT AGGREGATION
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# TEST
# ------------------------------------------------------------

if __name__ == "__main__":
    print("Testing circuit breaker...\n")

    # Test 1: Normal operation
    print("Test 1: Normal operation")
    print(f"  unpaywall state: {get_state('unpaywall')}")
    print(f"  should skip: {should_skip('unpaywall')}")
    record_success("unpaywall")
    print(f"  after success: {get_state('unpaywall')}\n")

    # Test 2: Simulate failures opening the circuit
    print("Test 2: Simulating 3 failures")
    for i in range(3):
        record_failure("openalex", reason="timeout")
    print(f"  openalex state after 3 failures: {get_state('openalex')}")
    print(f"  should skip openalex: {should_skip('openalex')}\n")

    # Test 3: Rate limit
    print("Test 3: Rate limit failure")
    record_failure("semantic_scholar", reason="rate_limit")
    print(f"  semantic_scholar state: {get_state('semantic_scholar')}")
    print(f"  recovery timeout: {RECOVERY_TIMEOUT['rate_limit']}s\n")

    # Test 4: Full status
    print("Test 4: Full circuit status")
    status = get_circuit_status()
    for source, info in status.items():
        print(f"  {source}: {info['state']}")