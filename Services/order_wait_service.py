import threading
import time
import uuid
import logging
import Helpers.printer as p


class OrderWaitService:
    def __init__(self, polygon_service, tws_service, max_workers=40, poll_interval=2):
        self.polygon = polygon_service
        self.tws = tws_service
        self.max_workers = max_workers
        self.poll_interval = poll_interval

        self.pending_orders = {}
        self.cancelled_orders = set()
        self.lock = threading.Lock()
        self.queue_cond = threading.Condition()
        self.order_queue = []

        self.threads = []
        for i in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self.threads.append(t)

    def add_order(self, order_data: dict) -> str:
        order_id = str(uuid.uuid4())
        order_data["order_id"] = order_id
        with self.lock:
            self.pending_orders[order_id] = order_data
        with self.queue_cond:
            self.order_queue.append(order_id)
            self.queue_cond.notify()
        msg = f"[WaitService] Order added {order_id}"
        logging.info(msg)
        p.PRINT(msg)
        return order_id

    def cancel_order(self, order_id: str):
        with self.lock:
            if order_id in self.pending_orders:
                self.cancelled_orders.add(order_id)
                del self.pending_orders[order_id]
                msg = f"[WaitService] Order cancelled {order_id}"
                logging.info(msg)
                p.PRINT(msg)

    def list_pending_orders(self):
        with self.lock:
            return list(self.pending_orders.values())

    def _worker_loop(self):
        while True:
            with self.queue_cond:
                while not self.order_queue:
                    self.queue_cond.wait()
                order_id = self.order_queue.pop(0)

            with self.lock:
                order_data = self.pending_orders.get(order_id)

            if not order_data:
                continue

            msg = f"[WaitService] Worker started for {order_id}"
            logging.info(msg)
            p.PRINT(msg)

            while True:
                if order_id in self.cancelled_orders:
                    msg = f"[WaitService] Worker exit (cancelled) {order_id}"
                    logging.info(msg)
                    p.PRINT(msg)
                    break

                snap = self.polygon.get_snapshot(order_data["symbol"])
                if not snap or "last" not in snap:
                    time.sleep(self.poll_interval)
                    continue

                price = snap["last"]
                trigger = order_data["trigger"]
                typ = order_data["type"]

                if typ == "CALL" and price >= trigger:
                    self._finalize_order(order_id, order_data)
                    break
                elif typ == "PUT" and price <= trigger:
                    self._finalize_order(order_id, order_data)
                    break

                time.sleep(self.poll_interval)

    def _finalize_order(self, order_id, order_data):
        try:
            result = self.tws.place_bracket_order(
                symbol=order_data["symbol"],
                expiry=order_data["expiry"],
                strike=order_data["strike"],
                right=order_data["right"],
                action="BUY" if order_data["type"] == "CALL" else "SELL",
                quantity=order_data["qty"],
                entry_price=order_data["entry_price"],
                take_profit_price=order_data["tp_price"],
                stop_loss_price=order_data["sl_price"]
            )
            msg = f"[WaitService] Order finalized {order_id} → {result}"
            logging.info(msg)
            p.PRINT(msg)
        except Exception as e:
            msg = f"[WaitService] Finalize failed {order_id}: {e}"
            logging.error(msg)
            p.PRINT(msg)

        with self.lock:
            if order_id in self.pending_orders:
                del self.pending_orders[order_id]
