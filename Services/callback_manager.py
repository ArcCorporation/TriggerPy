from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable, Dict, List
import logging
class ThreadedCallbackService:
    def __init__(self, max_workers: int = 5):
        self._callbacks: Dict[str, List[Callable[[float], None]]] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def add_callback(self, symbol: str, callback: Callable[[float], None]):
        """Register a callback for a given symbol."""
        with self._lock:
            if symbol not in self._callbacks:
                self._callbacks[symbol] = []
            self._callbacks[symbol].append(callback)

    def remove_callback(self, symbol: str, callback: Callable[[float], None]):
        """Remove a specific callback."""
        with self._lock:
            if symbol in self._callbacks:
                try:
                    self._callbacks[symbol].remove(callback)
                    if not self._callbacks[symbol]:
                        del self._callbacks[symbol]
                except ValueError as e:
                    logging.error(f"[Callback Error] {symbol}: {e}")

    def trigger(self, symbol: str, value: float):
        """Submit all callbacks to worker threads."""
        with self._lock:
            callbacks = list(self._callbacks.get(symbol, []))

        for cb in callbacks:
            self._executor.submit(self._safe_execute, cb, value)

    def _safe_execute(self, cb: Callable[[float], None], value: float):
        try:
            cb(value)
        except Exception as e:
            logging.error(f"[Callback Error] {cb.__name__}: {e}")

    def clear_symbol(self, symbol: str):
        with self._lock:
            self._callbacks.pop(symbol, None)

    def list_symbols(self):
        with self._lock:
            return list(self._callbacks.keys())


callback_manager = ThreadedCallbackService()