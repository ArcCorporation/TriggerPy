import threading
import time
import logging
from Helpers.Order import Order

class OrderWaitService:
    def __init__(self, polygon_service, tws_service, poll_interval=0.1):
        self.polygon = polygon_service
        self.tws = tws_service
        self.pending_orders = {}
        self.cancelled_orders = set()
        self.lock = threading.Lock()
        self.poll_interval = poll_interval  # seconds, e.g. 0.1s = 100ms

    def add_order(self, order: Order, mode="ws") -> str:
        order_id = order.order_id
        with self.lock:
            self.pending_orders[order_id] = order

        # immediate check
        current_price = self.polygon.get_last_trade(order.symbol)
        if current_price and order.is_triggered(current_price):
            logging.info(f"[WaitService] 🚨 Immediate trigger met {order.symbol} @ {current_price}")
            self._finalize_order(order_id, order)
            return order_id

        if mode == "ws":
            # normal path
            self.polygon.subscribe(order.symbol,
                lambda price, oid=order_id: self._on_tick(oid, price))
        elif mode == "poll":
            # alternate polling thread
            t = threading.Thread(
                target=self._poll_snapshot,
                args=(order_id, order),
                daemon=True
            )
            t.start()

        logging.info(f"[WaitService] Order added {order_id} (mode={mode}, trigger={order.trigger}, current={current_price})")
        return order_id

    def _poll_snapshot(self, order_id, order):
        """Continuously poll snapshot until trigger/cancel."""
        while True:
            with self.lock:
                if order_id not in self.pending_orders or order_id in self.cancelled_orders:
                    return  # cancelled/removed

            snap = self.polygon.get_snapshot(order.symbol)
          
            if not snap:
                time.sleep(self.poll_interval)
                continue

            last_price = snap.get("last")
            msg = f"[WaitService] Poll {order.symbol} → {last_price}"
            logging.info(msg)
            print(msg)
            if last_price and order.is_triggered(last_price):
                self._finalize_order(order_id, order)
                with self.lock:
                    if order_id in self.pending_orders:
                        del self.pending_orders[order_id]
                return

            time.sleep(self.poll_interval)
