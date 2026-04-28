import time
from dataclasses import dataclass, field
from typing import Dict
from enum import Enum


class CircuitState(str, Enum):
    CLOSED    = "closed"      # healthy — requests go through
    OPEN      = "open"        # broken  — requests blocked instantly
    HALF_OPEN = "half_open"   # testing — one request allowed through


@dataclass
class ProviderCircuit:
    state:             CircuitState = CircuitState.CLOSED
    failure_count:     int          = 0
    last_failure_time: float        = 0.0
    opened_at:         float        = 0.0

    # Config
    FAILURE_THRESHOLD: int   = 3      # open after 3 consecutive failures
    RESET_TIMEOUT_SEC: float = 60.0   # try again after 60 seconds


class CircuitBreakerRegistry:
    """
    Holds one circuit per provider.
    Single instance lives for the lifetime of the app process.
    In-memory is fine — circuit state is operational, not persistent.
    """

    def __init__(self):
        self._circuits: Dict[str, ProviderCircuit] = {}

    def _get(self, provider: str) -> ProviderCircuit:
        if provider not in self._circuits:
            self._circuits[provider] = ProviderCircuit()
        return self._circuits[provider]

    def is_available(self, provider: str) -> bool:
        """
        Can we send a request to this provider right now?
        CLOSED    → yes
        OPEN      → only if reset timeout has passed (transition to HALF_OPEN)
        HALF_OPEN → yes (we're testing recovery)
        """
        circuit = self._get(provider)

        if circuit.state == CircuitState.CLOSED:
            return True

        if circuit.state == CircuitState.OPEN:
            elapsed = time.monotonic() - circuit.opened_at
            if elapsed >= circuit.RESET_TIMEOUT_SEC:
                # Timeout passed — allow one test request through
                circuit.state = CircuitState.HALF_OPEN
                return True
            return False   # still open, block immediately

        # HALF_OPEN — allow through for testing
        return True

    def record_success(self, provider: str) -> None:
        """Call this when a provider request succeeds."""
        circuit = self._get(provider)
        circuit.failure_count = 0
        circuit.state         = CircuitState.CLOSED

    def record_failure(self, provider: str) -> None:
        """
        Call this when a provider request fails.
        Increments failure count and opens circuit if threshold reached.
        """
        circuit = self._get(provider)
        circuit.failure_count    += 1
        circuit.last_failure_time = time.monotonic()

        if circuit.failure_count >= circuit.FAILURE_THRESHOLD:
            circuit.state     = CircuitState.OPEN
            circuit.opened_at = time.monotonic()
            print(f"[CircuitBreaker] ⚡ {provider} circuit OPENED after "
                  f"{circuit.failure_count} failures")

    def get_state(self, provider: str) -> str:
        """Returns current state string for API responses."""
        return self._get(provider).state.value

    def get_all_states(self) -> Dict[str, dict]:
        """Returns state of all tracked providers — for /health endpoint."""
        result = {}
        for provider, circuit in self._circuits.items():
            result[provider] = {
                "state":          circuit.state.value,
                "failure_count":  circuit.failure_count,
                "last_failure":   circuit.last_failure_time,
            }
        return result

    def reset(self, provider: str) -> None:
        """Manually reset a circuit — for admin/testing use."""
        self._circuits[provider] = ProviderCircuit()


# ── Single global instance — imported wherever needed ────────────────
registry = CircuitBreakerRegistry()