import threading
import time
import logging
from Services.nasdaq_info import is_market_closed_or_pre_market, rth_proximity_factor
from Services.tws_service import create_tws_service, TWSService
from Services.polygon_service import polygon_service, PolygonService
#from model import general_app
from Helpers.Order import Order


class OrderQueueService:
    """
    Queues fully-prepared Order objects during premarket
    and submits them to GeneralApp at RTH open.
    """

    def __init__(
        self,
        tws: TWSService = create_tws_service(),
        polyg: PolygonService = polygon_service,
    ):
        self._tws_service = tws
        self._polygon_service = polyg

        self._queued_orders: list[Order] = []
        self._lock = threading.Lock()

        self._running = True
        self._thread_started = False
        self.general_app = None



    def set_app(self, general_app):
        self.general_app = general_app
    

        logging.info("[OrderQueueService] Initialized (ORDER-based queue).")

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------
    def queue_order(self, order: Order):
        """
        Queue a fully prepared Order object for RTH execution.
        """
        with self._lock:
            self._queued_orders.append(order)
            logging.info(
                f"[OrderQueueService] Queued order {order.order_id} "
                f"({order.symbol}) — waiting for RTH."
            )

        cb = getattr(order, "_status_callback", None)
        if cb:
            cb("Queued pre-market — will execute at market open.", "orange")

        # Start monitor thread once
        if not self._thread_started:
            self._thread_started = True
            t = threading.Thread(
                target=self._monitor_market_open,
                daemon=True
            )
            t.start()
            logging.info("[OrderQueueService] Market-open monitor thread started.")

    def cancel_queued_orders_for_model(self, model):
        """
        Cancel ALL queued orders belonging to the given AppModel.
        """
        with self._lock:
            before = len(self._queued_orders)
            self._queued_orders = [
                o for o in self._queued_orders
                if getattr(o, "appmodel", None) is not model
            ]
            after = len(self._queued_orders)

        logging.info(
            f"[OrderQueueService] Cancelled {before - after} queued orders for {model.symbol}"
        )

        cb = getattr(model, "_status_callback", None)
        if cb:
            cb("Queued order(s) cancelled.", "red")

    def rebase_queued_premarket_order(self, order: Order, new_trigger: float) -> bool:
        """
        Rebase trigger price of a queued premarket ORDER.
        """
        with self._lock:
            for queued_order in self._queued_orders:
                if queued_order is order:
                    queued_order.trigger = new_trigger

                    logging.info(
                        f"[OrderQueueService] Rebased queued order "
                        f"{order.order_id} → trigger {new_trigger}"
                    )

                    cb = getattr(order, "_status_callback", None)
                    if cb:
                        cb(
                            f"Queued order rebased to trigger {new_trigger:.2f}",
                            "blue"
                        )

                    return True

        logging.warning(
            f"[OrderQueueService] No queued order found to rebase "
            f"(order_id={getattr(order, 'order_id', 'UNKNOWN')})"
        )
        return False


    # ------------------------------------------------------------------
    # MARKET MONITOR
    # ------------------------------------------------------------------
    def _monitor_market_open(self):
        logging.info("[OrderQueueService] Monitoring for market open...")

        while self._running:
            try:
                delay = rth_proximity_factor()

                if not is_market_closed_or_pre_market():
                    logging.info(
                        "[OrderQueueService] Market OPEN → executing queued orders."
                    )
                    self._on_market_open()
                    return

                with self._lock:
                    count = len(self._queued_orders)

                if count > 0:
                    logging.info(
                        f"[OrderQueueService] Market closed/pre-market. "
                        f"{count} order(s) queued."
                    )

                time.sleep(delay)

            except Exception as e:
                logging.error(f"[OrderQueueService] Monitor error: {e}")
                time.sleep(5)

    # ------------------------------------------------------------------
    # EXECUTION
    # ------------------------------------------------------------------
    def _on_market_open(self):
        with self._lock:
            orders = list(self._queued_orders)
            self._queued_orders.clear()

        if not orders:
            logging.info("[OrderQueueService] No queued orders to execute.")
            return

        logging.info(
            f"[OrderQueueService] Submitting {len(orders)} queued orders."
        )

        for order in orders:
            threading.Thread(
                target=self._execute_order,
                args=(order,),
                daemon=True
            ).start()

    def _execute_order(self, order: Order):
        try:
            logging.info(
                f"[OrderQueueService] Executing queued order "
                f"{order.order_id} ({order.symbol})"
            )

            self.general_app.add_order(order)

        except Exception as e:
            logging.error(
                f"[OrderQueueService] Failed to execute order "
                f"{order.order_id}: {e}"
            )

            cb = getattr(order, "_status_callback", None)
            if cb:
                cb(f"Execution failed: {e}", "red")

    # ------------------------------------------------------------------
    # STOP
    # ------------------------------------------------------------------
    def stop(self):
        self._running = False
        logging.info("[OrderQueueService] Monitor stopped gracefully.")


# ----------------------------------------------------------------------
# SINGLETON EXPORT
# ----------------------------------------------------------------------
order_queue = OrderQueueService()
