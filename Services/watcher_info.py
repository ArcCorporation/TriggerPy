import threading
from datetime import datetime
from typing import Dict, Optional


class ThreadInfo:
    """
    Holds metadata about one watcher thread (trigger or stop-loss).
    This is just a data object. Uniqueness is enforced by WatcherInfo, not here.
    """
    def __init__(self,
                 order_id: str,
                 symbol: str,
                 watcher_type: str = "trigger",
                 mode: str = "poll",
                 stop_loss: Optional[float] = None):
        self.order_id = order_id
        self.symbol = symbol
        self.watcher_type = watcher_type      # "trigger" or "stop_loss"
        self.mode = mode                      # "poll" or "ws"
        self.stop_loss = stop_loss            # stop-loss level if any
        self.status = "PENDING"               # "PENDING", "RUNNING", "TRIGGERED", "FINALIZED", "CANCELLED", "FAILED"
        self.start_time = datetime.utcnow()
        self.last_price: Optional[float] = None
        self.info: Dict = {}
        self._lock = threading.Lock()

    def update_status(self, new_status: str, last_price: Optional[float] = None, info: Optional[Dict] = None):
        """
        Update runtime status of this watcher.
        """
        with self._lock:
            self.status = new_status
            if last_price is not None:
                self.last_price = last_price
            if info:
                self.info.update(info)
            self.info["last_update"] = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        """
        Export thread info as a dictionary for UI / JSON.
        """
        with self._lock:
            return {
                "order_id": self.order_id,
                "symbol": self.symbol,
                "watcher_type": self.watcher_type,
                "mode": self.mode,
                "stop_loss": self.stop_loss,
                "status": self.status,
                "start_time": self.start_time.isoformat(),
                "last_price": self.last_price,
                "info": dict(self.info),  # shallow copy for safety
            }


class WatcherInfo:
    """
    Global registry of all watcher threads (trigger + stop-loss).
    Single instance, meant to be imported everywhere.
    """
    def __init__(self):
        self._watchers: Dict[str, ThreadInfo] = {}
        self._lock = threading.Lock()

    def add_watcher(self, thread_info: ThreadInfo):
        with self._lock:
            self._watchers[thread_info.order_id] = thread_info

    def update_watcher(self, order_id: str, status: str, last_price: Optional[float] = None, info: Optional[Dict] = None):
        with self._lock:
            if order_id in self._watchers:
                self._watchers[order_id].update_status(status, last_price, info)

    def get_watcher(self, order_id: str) -> Optional[ThreadInfo]:
        with self._lock:
            return self._watchers.get(order_id)

    def list_all(self):
        with self._lock:
            return [w.to_dict() for w in self._watchers.values()]


# ✅ Global singleton instance (the library)
watcher_info = WatcherInfo()
