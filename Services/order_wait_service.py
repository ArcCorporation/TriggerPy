import threading
import time
import logging
import time
from Helpers.Order import Order, OrderState
from Services.order_manager import order_manager
from Services.watcher_info import (
    ThreadInfo, watcher_info,
    STATUS_PENDING, STATUS_RUNNING, STATUS_FINALIZED, STATUS_CANCELLED, STATUS_FAILED
)
from Services.runtime_manager import runtime_man
from Services.tws_service import create_tws_service
from Services.polygon_service import polygon_service


class OrderWaitService:
    def __init__(self, polygon_service, tws_service, poll_interval=0.1):
        self.polygon = polygon_service
        self.tws = tws_service

        # Active pending orders, keyed by order_id
        self.pending_orders = {}
        # Cancelled order IDs
        self.cancelled_orders = set()
        # Lock for thread-safety
        self.lock = threading.Lock()

        # Polling interval for alternate mode (seconds), e.g. 0.1s = 100ms
        self.poll_interval = poll_interval

    def start_trigger_watcher(self, order: Order, mode: str = "poll") -> threading.Thread:
        """
        Start a dedicated thread (or ws subscription) to watch trigger price for an order.
        When trigger condition is met, finalize order via TWS.
        """
        order_id = order.order_id

        # Register ThreadInfo
        tinfo = ThreadInfo(order_id, order.symbol, watcher_type="trigger", stop_loss=order.sl_price, mode=mode)
        watcher_info.add_watcher(tinfo)
        tinfo.update_status(STATUS_RUNNING)

        if mode == "ws":
            # Original WS path
            self.polygon.subscribe(
                order.symbol,
                lambda price, oid=order_id: self._on_tick(oid, price)
            )
            logging.info(f"[TriggerWatcher] Started WS watcher for {order.symbol} (order {order_id})")
            return None  # no thread object for ws

        elif mode == "poll":
            # Old-school polling thread path
            def _poll_snapshot(order_id: str, order: Order, tinfo: ThreadInfo):
                delay = 2
                last = 0
                try:
                    while runtime_man.is_run():
                        with self.lock:
                            if order_id not in self.pending_orders or order_id in self.cancelled_orders:
                                watcher_info.remove(order_id)
                                return
                        snap = self.polygon.get_snapshot(order.symbol)
                        if not snap:
                            time.sleep(self.poll_interval)
                            continue

                        last_price = snap.get("last")
                        msg = f"[WaitService] Poll {order.symbol} → {last_price}"
                        now = time.time()
                        if now - last > delay:
                            logging.info(msg)
                            print(msg)
                            last = now

                        if last_price:
                            tinfo.update_status(STATUS_RUNNING, last_price=last_price)

                        if last_price and order.is_triggered(last_price):
                            self._finalize_order(order_id, order)
                            tinfo.update_status(STATUS_FINALIZED, last_price=last_price)
                            with self.lock:
                                if order_id in self.pending_orders:
                                    del self.pending_orders[order_id]
                            return

                        time.sleep(self.poll_interval)
                except Exception as e:
                    tinfo.update_status(STATUS_FAILED, info={"error": str(e)})

            t = threading.Thread(
                target=_poll_snapshot,
                args=(order_id, order, tinfo),
                daemon=True
            )
            t.start()
            logging.info(f"[TriggerWatcher] Started polling watcher for {order.symbol} (order {order_id})")
            return t

        else:
            logging.warning(f"[TriggerWatcher] Unknown mode '{mode}', defaulting to 'ws'")
            self.polygon.subscribe(
                order.symbol,
                lambda price, oid=order_id: self._on_tick(oid, price)
            )
            return None

    def start_stop_loss_watcher(self, order: Order, stop_loss_price: float):
        """
        Start a dedicated thread to monitor stop-loss for an active order.
        For CALL:  exit if price <= stop_loss_price
        For PUT:   exit if price >= stop_loss_price
        """
        order_id = order.order_id

        # Register ThreadInfo
        tinfo = ThreadInfo(order_id, order.symbol, watcher_type="stop_loss", mode="poll", stop_loss=stop_loss_price)
        watcher_info.add_watcher(tinfo)
        tinfo.update_status(STATUS_RUNNING)

        def _stop_loss_thread():
            last_print = 0
            delay = 5
            try:
                logging.info(f"[StopLoss] Watching {order.symbol} stop-loss @ {stop_loss_price}  ({order.right})")
                while runtime_man.is_run():
                    if order.state not in (OrderState.ACTIVE, OrderState.PENDING):
                        logging.info(f"[StopLoss] Order {order.order_id} no longer active, stopping watcher.")
                        tinfo.update_status(STATUS_CANCELLED)
                        return

                    snap = self.polygon.get_snapshot(order.symbol)
                    if not snap:
                        time.sleep(self.poll_interval)
                        continue
                    now = time.time()
                    last_price = snap.get("last")
                    if now - last_print >= delay:
                        logging.info(f"[StopLoss] Poll {order.symbol} → {last_price}, stop={stop_loss_price}")
                        last_print = now

                    if last_price:
                        tinfo.update_status(STATUS_RUNNING, last_price=last_price)

                    # ----- right-aware trigger -----
                    if order.right in ("P", "PUT"):
                        triggered = last_price >= stop_loss_price   # PUT: rise = loss
                    else:
                        triggered = last_price <= stop_loss_price   # CALL: fall = loss
                    # --------------------------------
                    premium = self.tws.get_option_premium(
                        order.symbol, order.expiry, order.strike, order.right
                    )
                    if premium is None or premium <= 0:          # safety: can't price the option
                        pos = self.tws.get_position_by_order_id(order.previous_id)
                    if pos:
                        premium = pos["avg_price"]
                        logging.warning(f"[StopLoss] Using fallback avg_price={premium} for {order.symbol}")
                    else:
                        logging.error("[StopLoss] No live premium – aborting exit")
                        return


                    if triggered:
                        try:
                            snapshot = self.tws.get_option_snapshot(order.symbol, order.expiry, order.strike, order.right)
                            if not snapshot or snapshot.get("ask") is None:
                                logging.error("[StopLoss] Snapshot timeout – cannot compute premium")
                                tinfo.update_status(STATUS_FAILED, info={"error": "No snapshot"})
                                return

                            mid_premium = snapshot["ask"] * 1.05
                            tick = 0.01 if mid_premium < 3 else 0.05
                            mid_premium = int(round(mid_premium / tick)) * tick
                            mid_premium = round(mid_premium, 2)
                            pos = self.tws.get_position_by_order_id(order.previous_id)
                            if not pos or pos.get("qty", 0) <= 0:
                                logging.warning(f"[StopLoss] No live position for {order.previous_id}, cannot exit.")
                                tinfo.update_status(STATUS_FAILED, info={"error": "No position"})
                                return

                            live_qty = int(pos["qty"])
                            success = self.tws.sell_position_by_order_id(
                                order.order_id,
                                qty=live_qty,
                                limit_price=mid_premium
                            )
                            if success:
                                logging.info(f"[StopLoss] Sold {live_qty} {order.symbol} via TWS position map @ {mid_premium}")
                                order.mark_finalized(f"Stop-loss triggered @ {last_price}")
                                tinfo.update_status(STATUS_FINALIZED, last_price=last_price)
                            else:
                                logging.error(f"[StopLoss] TWS refused stop-loss sell for {order.order_id}")
                                tinfo.update_status(STATUS_FAILED, last_price=last_price)
                       
                            
                            
                        except Exception as e:
                            logging.error(f"[StopLoss] Exception in stop-loss for {order.order_id}: {e}")
                            tinfo.update_status(STATUS_FAILED, info={"error": str(e)})
                        return

                    time.sleep(self.poll_interval)
            except Exception as e:
                tinfo.update_status(STATUS_FAILED, info={"error": str(e)})

        # Spawn the stop-loss thread
        t = threading.Thread(target=_stop_loss_thread, daemon=True)
        t.start()
        return t

    def add_order(self, order: Order, mode: str = "ws") -> str:
        """
        Add an order to be executed once its trigger is met.
        mode="ws"   -> subscribe to live ticks (original behavior)
        mode="poll" -> start a polling thread using snapshot
        """
        order_id = order.order_id
        with self.lock:
            self.pending_orders[order_id] = order

        # ✅ IMMEDIATE TRIGGER CHECK
        current_price = self.polygon.get_last_trade(order.symbol)
        if current_price and order.is_triggered(current_price):
            logging.info(
                f"[WaitService] 🚨 TRIGGER ALREADY MET! Executing immediately. "
                f"Current: {current_price}, Trigger: {order.trigger}"
            )
            self._finalize_order(order_id, order)
            return order_id

        # Subscribe / start poller only if trigger not already met
        if mode == "ws":
            self.polygon.subscribe(
                order.symbol,
                lambda price, oid=order_id: self._on_tick(oid, price)
            )
        else:
            self.start_trigger_watcher(order, mode)

        msg = (
            f"[WaitService] Order added {order_id} "
            f"(mode={mode}, waiting for trigger {order.trigger}, current: {current_price})"
        )
        logging.info(msg)
        return order_id

    def cancel_order(self, order_id: str):
        """Cancel an order. Removes it from pending set and unsubscribes from Polygon."""
        with self.lock:
            if order_id in self.pending_orders:
                order = self.pending_orders[order_id]
                order.mark_cancelled()
                self.cancelled_orders.add(order_id)
                del self.pending_orders[order_id]

                try:
                    self.polygon.unsubscribe(order.symbol)
                except Exception as e:
                    logging.debug(f"[WaitService] Unsubscribe ignored for {order.symbol}: {e}")

                msg = f"[WaitService] Order cancelled {order_id}"
                logging.info(msg)

    def list_pending_orders(self):
        with self.lock:
            return [o.to_dict() for o in self.pending_orders.values()]

    def _on_tick(self, order_id: str, price: float):
        """Callback from PolygonService for live ticks."""
        with self.lock:
            order = self.pending_orders.get(order_id)
            if not order or order_id in self.cancelled_orders:
                return

        msg = f"[WaitService] Tick received for {order.symbol} @ {price}, trigger={order.trigger}"
        logging.info(msg)

        if order.is_triggered(price):
            self._finalize_order(order_id, order)
            try:
                self.polygon.unsubscribe(order.symbol)
            except Exception as e:
                logging.debug(f"[WaitService] Unsubscribe ignored for {order.symbol}: {e}")

            with self.lock:
                if order_id in self.pending_orders:
                    del self.pending_orders[order_id]

    def _poll_snapshot(self, order_id: str, order: Order):
        """Alternate mode: continuously poll snapshot until trigger/cancel."""
        last_print = 0
        delay = 5
        while True:
            with self.lock:
                if order_id not in self.pending_orders or order_id in self.cancelled_orders:
                    return

            snap = self.polygon.get_snapshot(order.symbol)
            if not snap:
                time.sleep(self.poll_interval)
                continue
            now = time.time()
            last_price = snap.get("last")
            msg = f"[WaitService] Poll {order.symbol} → {last_price}"
            if now - last_print >= delay:
                logging.info(msg)
                print(msg)
                last_print = now

            if last_price and order.is_triggered(last_price):
                self._finalize_order(order_id, order)
                with self.lock:
                    if order_id in self.pending_orders:
                        del self.pending_orders[order_id]
                return

            time.sleep(self.poll_interval)

    def _finalize_order(self, order_id: str, order: Order):
        """Sends the order to TWS using the new TWSService."""
        

        try:
            start_ts = time.time() * 1000
            logging.info(f"[TWS-LATENCY] {order.symbol} Trigger hit → sending order "
                        f"({order.right}{order.strike}) at {start_ts:.0f} ms")
            success = self.tws.place_custom_order(order)
            if success:
                end_ts = time.time() * 1000
                latency = end_ts - start_ts
                logging.info(f"[TWS-LATENCY] {order.symbol} Order sent in {latency:.1f} ms "
                            f"(start {start_ts:.0f} → end {end_ts:.0f})")

                order.mark_active(result=f"IB Order ID: {order._ib_order_id}")
                if getattr(order, "_status_callback", None):
                    try:
                        order._status_callback(f"Finalized: {order.symbol} {order.order_id}", "green")
                    except Exception as e:
                        logging.error(f"[WaitService] UI callback failed for finalized order {order.order_id}: {e}")
                
                if getattr(order, "_fill_event", None):
                    filled = order._fill_event.wait(timeout=60)
                    if filled and order.state == OrderState.FINALIZED:
                        order_manager.add_finalized_order(order_id, order)
                        msg = f"[WaitService] Order finalized {order_id} → IB ID: {order._ib_order_id}"
                        logging.info(msg)
                        watcher_info.update_watcher(order_id, STATUS_FINALIZED)
                    else:
                        logging.warning(f"[WaitService] Order {order_id} not filled within timeout window.")

                # ✅ if stop-loss configured, launch stop-loss watcher
                    if order.trigger and order.sl_price and order.state == OrderState.FINALIZED:
                        stop_loss_level = order.trigger - order.sl_price if order.right == 'C' or order.right == "CALL" else order.trigger + order.sl_price
                        exit_order = Order(
                            symbol=order.symbol,
                            expiry=order.expiry,
                            strike=order.strike,
                            right=order.right,
                            qty=order.qty,
                            entry_price=order.entry_price,   # keeps breakeven reference
                            tp_price=None,
                            sl_price=order.sl_price,
                            action="SELL",
                            trigger=None
                        )
                        ex_order = exit_order.set_position_size(order._position_size) 
                        ex_order.mark_active()
                        #logging.info(f"[WAITSERVICE] Spawned exit order {ex_order.order_id} "
                        #        f"stop={stop_loss_level} ({order.right})")
                        ex_order.previous_id = order.order_id
                        #self.start_stop_loss_watcher(ex_order, stop_loss_level)
                        logging.info(f"BEWARE NOT LAUNCHING STOP LOSS")


            else:
                order.mark_failed("Failed to place order with TWS")
                msg = f"[WaitService] Order placement failed {order_id}"
                logging.error(msg)
                watcher_info.update_watcher(order_id, STATUS_FAILED)

        except Exception as e:
            order.mark_failed(str(e))
            msg = f"[WaitService] Finalize failed {order_id}: {e}"
            logging.error(msg)
            watcher_info.update_watcher(order_id, STATUS_FAILED, info={"error": str(e)})

    def get_order_status(self, order_id: str):
        return self.tws.get_order_status(order_id)

    def cancel_active_order(self, order_id: str) -> bool:
        try:
            if self.tws.cancel_custom_order(order_id):
                self.cancel_order(order_id)
                watcher_info.update_watcher(order_id, STATUS_CANCELLED)
                return True
            return False
        except Exception as e:
            logging.error(f"[WaitService] Cancel active order failed {order_id}: {e}")
            watcher_info.update_watcher(order_id, STATUS_FAILED, info={"error": str(e)})
            return False

    def get_all_orders_status(self):
        result = {
            'pending': self.list_pending_orders(),
            'active': {}
        }
        for order_id in list(self.pending_orders.keys()):
            status = self.get_order_status(order_id)
            if status:
                result['active'][order_id] = status
        return result
    


wait_service = OrderWaitService(polygon_service, create_tws_service())
