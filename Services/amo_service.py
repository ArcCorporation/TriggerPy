# amo_service.py
# ARC Mediator Object (AMO)
# Phase-sealed object reference registry

from typing import Any, Dict
from enum import Enum
LOSS = "SET_LOSS"

class AMOState(Enum):
    INIT_LOCKED = "INIT_LOCKED"
    READY = "READY"


class AMOService:
    """
    ARC Mediator Object

    Responsibilities:
    - Store object references
    - Enforce lifecycle phases
    - Block premature access
    - Guarantee graph completeness
    """

    def __init__(self) -> None:
        self._state: AMOState = AMOState.INIT_LOCKED
        self._registry: Dict[str, Any] = {}

    # -------------------------
    # Lifecycle control
    # -------------------------

    def seal(self) -> None:
        if self._state != AMOState.INIT_LOCKED:
            raise RuntimeError("AMO already sealed or invalid state")
        self._state = AMOState.READY

    @property
    def state(self) -> AMOState:
        return self._state

    # -------------------------
    # Registration phase
    # -------------------------

    def register(self, key: str, obj: Any) -> None:
        if self._state != AMOState.INIT_LOCKED:
            raise RuntimeError(
                f"Registration closed (state={self._state.value})"
            )
        if key in self._registry:
            raise RuntimeError(f"Duplicate registration for key '{key}'")
        self._registry[key] = obj

    # -------------------------
    # Runtime access phase
    # -------------------------

    def get(self, key: str) -> Any:
        if self._state != AMOState.READY:
            raise RuntimeError(
                f"Access before initialization complete (state={self._state.value})"
            )
        if key not in self._registry:
            raise KeyError(f"Object '{key}' not found in AMO registry")
        return self._registry[key]

    # -------------------------
    # Introspection (optional)
    # -------------------------

    def keys(self):
        return list(self._registry.keys())


# -------------------------------------------------
# ARC-style global export (explicit, visible, honest)
# -------------------------------------------------

amo = AMOService()
