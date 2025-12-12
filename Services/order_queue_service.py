import threading
import time
import logging
from datetime import datetime
from Services.nasdaq_info import is_market_closed_or_pre_market
from Services.tws_service import create_tws_service, TWSService
from Services.polygon_service import polygon_service, PolygonService

class OrderQueueService:
    def __init__(self, tws: TWSService = create_tws_service(), polyg: PolygonService = polygon_service):
        self._tws_service = tws
        self._polygon_service = polyg
        self._queued_actions = []   # [(model, args, kwargs)]
        self._lock = threading.Lock()
        self._running = True
        self._thread_started = False
        logging.info("[OrderQueueService] Initialized for deferred AppModel actions.")

    # ------------------------------------------------------------------
    # PUBLIC ENTRY
    # ------------------------------------------------------------------
    def rebase_queued_premarket_order(
    self,
    model,
    new_trigger: float
) -> bool:
        """
        Update the trigger price of an already queued premarket order
        for the given model. Returns True if updated.
        """
        with self._lock:
            for i, (m, args, kwargs) in enumerate(self._queued_actions):
                if m is model:
                    # args layout (from place_option_order queue):
                    # action, position, quantity, trigger_price, status_callback, arcTick, type
                    args = list(args)
                    args[3] = new_trigger  # 🔥 replace trigger_price
                    self._queued_actions[i] = (m, tuple(args), kwargs)

                    logging.info(
                        f"[OrderQueueService] Rebasing queued order for {model.symbol} → trigger {new_trigger}"
                    )

                    if model._status_callback:
                        model._status_callback(
                            f"Queued order rebased to trigger {new_trigger:.2f}",
                            "blue"
                        )
                    return True

        logging.warning(
            f"[OrderQueueService] No queued action found to rebase for {model.symbol}"
        )
        return False


    def queue_action(self, model, *args, **kwargs):
        """Store the AppModel.place_option_order() call for deferred execution."""
        with self._lock:
            self._queued_actions.append((model, args, kwargs))
            logging.info(f"[OrderQueueService] Queued action for {model.symbol} — will run at RTH.")
        if hasattr(model, "_status_callback") and model._status_callback:
            model._status_callback("Queued pre-market — will execute at market open.", "orange")

        # Start monitoring thread once
        if not self._thread_started:
            self._thread_started = True
            t = threading.Thread(target=self._monitor_market_open, daemon=True)
            t.start()
            logging.info("[OrderQueueService] Market-open monitor thread started.")

    
    def cancel_queued_actions_for_model(self, model):
        """Remove ALL queued actions belonging to this model."""
        with self._lock:
            before = len(self._queued_actions)
            self._queued_actions = [
                (m, a, k) for (m, a, k) in self._queued_actions
                if m is not model
            ]
            after = len(self._queued_actions)

        logging.info(
            f"[OrderQueueService] Cancelled {before - after} queued actions for {model.symbol}"
        )

        # notify UI
        if hasattr(model, "_status_callback") and model._status_callback:
            model._status_callback("Queued order cancelled.", "red")


    # ------------------------------------------------------------------
    # MONITOR LOOP
    # ------------------------------------------------------------------
    def _monitor_market_open(self):
        logging.info("[OrderQueueService] Monitoring for market open...")
        while self._running:
            try:
                if not is_market_closed_or_pre_market():
                    logging.info("[OrderQueueService] Market is OPEN → replaying queued actions.")
                    self._on_market_open()
                    time.sleep(60)
                else:
                    with self._lock:
                        count = len(self._queued_actions)
                    if count > 0:
                        logging.info(f"[OrderQueueService] Market closed/pre-market. {count} action(s) queued.")
                    time.sleep(5)
            except Exception as e:
                logging.error(f"[OrderQueueService] Monitor loop error: {e}")
                time.sleep(10)

    # ------------------------------------------------------------------
    # EXECUTION LOGIC
    # ------------------------------------------------------------------
    def _on_market_open(self):
        with self._lock:
            actions = list(self._queued_actions)
            self._queued_actions.clear()

        if not actions:
            logging.info("[OrderQueueService] No queued actions to process.")
            return

        logging.info(f"[OrderQueueService] Executing {len(actions)} queued AppModel actions...")

        for model, args, kwargs in actions:
            threading.Thread(target=self._execute_action, args=(model, args, kwargs), daemon=True).start()

    def _execute_action(self, model, args, kwargs):
        try:
            logging.info(f"[OrderQueueService] Executing deferred place_option_order for {model.symbol}")
            model.place_option_order(*args, **kwargs)
        except Exception as e:
            logging.error(f"[OrderQueueService] Error executing queued action for {model.symbol}: {e}")
            if hasattr(model, "_status_callback") and model._status_callback:
                model._status_callback(f"Execution failed: {e}", "red")

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
