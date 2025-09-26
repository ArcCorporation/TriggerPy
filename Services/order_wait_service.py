# Services/order_wait_service.py
import threading
import time
import uuid
import logging


class OrderWaitService:
    def __init__(self, polygon_service, tws_service, max_workers=40, poll_interval=2):
        """
        :param polygon_service: PolygonService instance
        :param tws_service: TWSService instance
        :param max_workers: kaç thread çalışacak (default 40)
        :param poll_interval: fiyat polling aralığı (saniye)
        """
        self.polygon = polygon_service
        self.tws = tws_service
        self.max_workers = max_workers
        self.poll_interval = poll_interval

        self.pending_orders = {}
        self.cancelled_orders = set()
        self.lock = threading.Lock()
        self.queue_cond = threading.Condition()
        self.order_queue = []
        # worker thread havuzu
        self.threads = []
        for i in range(max_workers):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()
            self.threads.append(t)

        # ortak queue (threadler buradan iş alır)

        #self.queue_cond = threading.Condition()    retard u run worker loop before defining que condition

    def add_order(self, order_data: dict) -> str:
        """
        Order’ı pending queue’ya ekle.
        order_data:
          {
            symbol, expiry, strike, right, qty,
            trigger, type ("CALL"/"PUT"),
            entry_price, tp_price, sl_price
          }
        """
        order_id = str(uuid.uuid4())
        order_data["order_id"] = order_id
        with self.lock:
            self.pending_orders[order_id] = order_data
        with self.queue_cond:
            self.order_queue.append(order_id)
            self.queue_cond.notify()
        logging.info(f"[WaitService] Order added {order_id}")
        return order_id

    def cancel_order(self, order_id: str):
        """
        Pending order’ı iptal et (TWS’e hiç gitmeden).
        """
        with self.lock:
            if order_id in self.pending_orders:
                self.cancelled_orders.add(order_id)
                del self.pending_orders[order_id]
                logging.info(f"[WaitService] Order cancelled {order_id}")

    def list_pending_orders(self):
        with self.lock:
            return list(self.pending_orders.values())

    def _worker_loop(self):
        while True:
            with self.queue_cond:
                while not self.order_queue:
                    self.queue_cond.wait()
                order_id = self.order_queue.pop(0)

            # order’ı bul
            with self.lock:
                order_data = self.pending_orders.get(order_id)

            if not order_data:
                continue  # iptal edilmiş olabilir

            logging.info(f"[WaitService] Worker started for {order_id}")

            # Trigger bekleme döngüsü
            while True:
                # Cancel edilmiş mi?
                if order_id in self.cancelled_orders:
                    logging.info(f"[WaitService] Worker exit (cancelled) {order_id}")
                    break

                # Fiyat çek
                snap = self.polygon.get_snapshot(order_data["symbol"])
                if not snap or "last" not in snap:
                    time.sleep(self.poll_interval)
                    continue

                price = snap["last"]
                trigger = order_data["trigger"]
                typ = order_data["type"]

                # Koşul kontrolü
                if typ == "CALL" and price >= trigger:
                    self._finalize_order(order_id, order_data)
                    break
                elif typ == "PUT" and price <= trigger:
                    self._finalize_order(order_id, order_data)
                    break

                time.sleep(self.poll_interval)

    def _finalize_order(self, order_id, order_data):
        """
        Trigger gerçekleşti → TWS’e gönder
        """
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
            logging.info(f"[WaitService] Order finalized {order_id} → {result}")
        except Exception as e:
            logging.error(f"[WaitService] Finalize failed {order_id}: {e}")

        with self.lock:
            if order_id in self.pending_orders:
                del self.pending_orders[order_id]
