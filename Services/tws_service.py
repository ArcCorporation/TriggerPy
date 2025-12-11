# tws_service.py
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order as IBOrder
import threading
import time
import logging
import random
from typing import List, Dict, Optional
from Helpers.Order import Order
import traceback
from Services.polygon_service import polygon_service
from Services.nasdaq_info import is_market_closed_or_pre_market # <-- NEW IMPORT





class TWSService(EWrapper, EClient):
    """
    TWS Service that integrates with Helpers.Order system
    """
    def __init__(self):
        EClient.__init__(self, self)
        self.next_valid_order_id = None
        self.connection_ready = threading.Event()
        self.client_id = random.randint(1, 999999)
        self.connected = False
        
        # For data requests
        self._maturities_data = {}
        self._maturities_req_id = None
        self._maturities_event = threading.Event()
        
        self._contract_details = {}
        self._contract_details_req_id = None
        self._contract_details_event = threading.Event()
        
        self._request_counter = 1
        self.symbol_samples = {}
        self._pre_conid_cache = {}   # key: (symbol, expiry, strike, right) → conId

        # Track custom orders from Helpers.Order
        self.option_chains = {}  # Add this line
        self._pending_orders = {}  # custom_order_id -> Helpers.Order object
        self._last_print = 0
        self._positions_by_order_id: dict[str, dict] = {}
        self._ib_to_order_id: dict[int, str] = {}
    
    
    def conn_status(self) -> bool:
        """
        Returns True if currently connected to TWS and next_valid_order_id has been set.
        This method checks both the IB API connection and the internal event flag.
        """
        is_alive = self.isConnected() and self.connection_ready.is_set() and self.next_valid_order_id is not None
        if not is_alive:
            logging.warning("[TWSService] conn_status: Not connected to TWS")
        else:
            now = time.time()
            if now - self._last_print >= 60:
                logging.info("[TWSService] conn_status: Connected and healthy")
                self._last_print = now
        return is_alive

    def disconnect(self) -> None:
        """
        Wrap the real disconnect() so we can log WHO/WHERE called it,
        then delegate to the genuine EClient implementation.
        """
        # Build a short human-readable caller string (last 2 frames)
        caller = "\n".join(
            f"  {s}" for s in traceback.format_stack(limit=3)[:-1][-2:]
        )
        logging.warning(
            f"[TWSService] disconnect() invoked — socket will close.\n{caller}"
        )

        # *** NOW run the real IB code ***
        super().disconnect()

        # Mark our own state
        self.connected = False
        self.connection_ready.clear()
    def reconnect(self, host: str = "127.0.0.1", port: int = 7497, timeout: int = 10) -> bool:
        """
        Attempts to reconnect to TWS/IB Gateway.
        Safely disconnects first if a stale session exists.
        Returns True if the reconnection is successful.
        """
        try:
            if self.isConnected():
                logging.info("[TWSService] reconnect(): Closing existing connection before retry...")
                try:
                    self.disconnect_gracefully()
                    time.sleep(1)
                except Exception as e:
                    logging.warning(f"[TWSService] reconnect(): Error while disconnecting: {e}")

            logging.info(f"[TWSService] Attempting reconnection to {host}:{port} (Client ID: {self.client_id})")
            result = self.connect_and_start(host=host, port=port, timeout=timeout)
            
            if result:
                logging.info("[TWSService] reconnect(): Reconnected successfully.")
                return True
            else:
                logging.error("[TWSService] reconnect(): Reconnection failed.")
                return False

        except Exception as e:
            logging.error(f"[TWSService] reconnect(): Exception during reconnection: {e}")
            return False


    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_valid_order_id = orderId
        logging.info(f"NextValidId: {orderId} (Client ID: {self.client_id})")
        self.connection_ready.set()

    # ---------------- Symbol Search ----------------
    def symbolSamples(self, reqId, contractDescriptions):
        results = []
        for desc in contractDescriptions:
            c = desc.contract
            results.append({
                "symbol": c.symbol,
                "secType": c.secType,
                "currency": c.currency,
                "exchange": c.exchange,
                "primaryExchange": c.primaryExchange,
                "description": desc.derivativeSecTypes
            })
        self.symbol_samples[reqId] = results

    def search_symbol(self, name: str, reqId: int = None):
        if reqId is None:
            reqId = self._get_next_req_id()
        self.reqMatchingSymbols(reqId, name)
        time.sleep(2)
        return self.symbol_samples.get(reqId, [])

    def error(self, reqId, errorCode, errorString, *args):
        """Error callback - handles both regular and protobuf errors"""
        actual_error_code = errorCode
        if isinstance(errorCode, int) and errorCode > 10000:
            if "errorCode:" in str(errorString):
                try:
                    parts = str(errorString).split("errorCode:")
                    if len(parts) > 1:
                        actual_error_code = int(parts[1].split()[0])
                except:
                    actual_error_code = errorCode
        
        # Handle specific error codes
        if actual_error_code in [2104, 2106, 2158]:
            logging.info(f"TWS Info. Code: {actual_error_code}, Msg: {errorString}")
        elif actual_error_code == 502:
            logging.error("Connection failed - check TWS/IB Gateway")
            self.connection_ready.clear()
        elif actual_error_code == 504:
            logging.error(f"Not connected to TWS: {errorString}")
            self.connection_ready.clear()
        elif actual_error_code == 200:
            logging.warning(f"No security definition for reqId {reqId}")
            if reqId == self._maturities_req_id:
                self._maturities_event.set()
            elif reqId == self._contract_details_req_id:
                self._contract_details_event.set()
        elif actual_error_code == 321:
            logging.error(f"Contract validation error for reqId {reqId}: {errorString}")
            if reqId == self._maturities_req_id:
                self._maturities_event.set()
        else:
            logging.error(f"API Error. reqId: {reqId}, Code: {actual_error_code}, Msg: {errorString}")

    def orderStatus(
    self, orderId, status, filled, remaining, avgFillPrice,
    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice
):
        """Update custom order status and maintain live fill tracking"""
        logging.info(f"[TWSService] Order status: ID={orderId}, status={status}, filled={filled}, avgFill={avgFillPrice}")

        try:
            status_str = (status or "").strip().lower()
            custom_uuid = self._ib_to_order_id.get(orderId)
            custom_order = self._pending_orders.get(custom_uuid)

            if not custom_order:
                logging.debug(f"[TWSService] Untracked IB order {orderId} (status={status})")
                return

            # --- Handle status transitions ---
            if status_str in ("submitted", "presubmitted"):
                # Mark active once order leaves local queue
                custom_order.mark_active(result=f"Submitted → IBID {orderId}")
                logging.info(f"[TWSService] Order {custom_uuid} now ACTIVE on IB")


            elif status_str == "filled":
                # 1.  UPDATE POSITION MAP FIRST (race-free)
                if custom_order.action.upper() == "BUY":
                    old = self._positions_by_order_id.get(custom_order.order_id, {})
                    new_qty = int(filled or old.get("qty", 0))
                    pos = {
                        "uuid": custom_order.order_id,
                        "symbol": custom_order.symbol,
                        "expiry": custom_order.expiry,
                        "strike": custom_order.strike,
                        "right": custom_order.right,
                        "qty": new_qty,
                        "avg_price": float(avgFillPrice or old.get("avg_price", 0.0)),
                        "ib_id": orderId,
                    }
                    self._positions_by_order_id[custom_order.order_id] = pos
                    logging.info(f"[TWSService] Saved BUY position {custom_order.symbol} "
                                f"({custom_order.order_id}) qty={filled} @ {avgFillPrice}")

                elif custom_order.action.upper() == "SELL":
                    target_uuid = self._ib_to_order_id.get(orderId)
                    if target_uuid and target_uuid in self._positions_by_order_id:
                        pos = self._positions_by_order_id[target_uuid]
                        old_qty = pos["qty"]
                        pos["qty"] = max(0, old_qty - int(filled or 0))
                        logging.info(f"[TWSService] SELL fill updated {target_uuid}: "
                                    f"qty {old_qty} → {pos['qty']}")
                        if pos["qty"] == 0:
                            self._positions_by_order_id.pop(target_uuid, None)
                            logging.info(f"[TWSService] Position closed for {target_uuid}")

                # 2.  CHANGE STATE LAST
                custom_order.mark_finalized(result=f"Filled {filled} @ {avgFillPrice}")
                if hasattr(custom_order, "_fill_event"):
                    custom_order._fill_event.set()
                logging.info(f"[TWSService] Order {custom_uuid} FINALIZED "
                            f"(filled={filled} @ {avgFillPrice})")

            elif status_str == "filledOLD":
                custom_order.mark_finalized(result=f"Filled {filled} @ {avgFillPrice}")
                logging.info(f"[TWSService] Order {custom_uuid} FINALIZED (filled={filled} @ {avgFillPrice})")

                # ✅ Save live BUY position correctly for StopLoss reference
                if custom_order.action.upper() == "BUY":
                    # merge instead of blind overwrite
                    old = self._positions_by_order_id.get(custom_order.order_id, {})
                    new_qty = int(filled or old.get("qty", 0))
                    pos = {
                        "uuid": custom_order.order_id,
                        "symbol": custom_order.symbol,
                        "expiry": custom_order.expiry,
                        "strike": custom_order.strike,
                        "right": custom_order.right,
                        "qty": new_qty,
                        "avg_price": float(avgFillPrice or old.get("avg_price", 0.0)),
                        "ib_id": orderId,
                    }
                    self._positions_by_order_id[custom_order.order_id] = pos
                    logging.info(f"[TWSService] Saved BUY position … qty={new_qty}")
                    
                    logging.info(f"[TWSService] Saved BUY position {custom_order.symbol} ({custom_order.order_id}) qty={filled} @ {avgFillPrice}")
                # --- 🔧 Handle SELL fills (close or reduce existing position) ---
                if custom_order.action.upper() == "SELL":
                    target_uuid = self._ib_to_order_id.get(orderId)
                    if target_uuid and target_uuid in self._positions_by_order_id:
                        pos = self._positions_by_order_id[target_uuid]
                        old_qty = pos["qty"]
                        pos["qty"] = max(0, old_qty - int(filled or 0))
                        self._positions_by_order_id[target_uuid] = pos
                        logging.info(f"[TWSService] SELL fill updated {target_uuid}: qty {old_qty} → {pos['qty']}")
                        if pos["qty"] == 0:
                            self._positions_by_order_id.pop(target_uuid, None)
                            logging.info(f"[TWSService] Position closed for {target_uuid}")


            elif status_str in ("cancelled", "apicancelled"):
                custom_order.mark_cancelled()
                logging.info(f"[TWSService] Order {custom_uuid} CANCELLED on IB")

            elif status_str in ("inactive", "pendingcancel"):
                custom_order.mark_failed(reason=f"Inactive or pending cancel (status={status})")

            # --- Always mirror to tracking maps ---
            if custom_uuid not in self._ib_to_order_id.values():
                self._ib_to_order_id[orderId] = custom_uuid
            if custom_uuid not in self._pending_orders:
                self._pending_orders[custom_uuid] = custom_order

            # --- Update position map regardless of fill ---
            pos = self._positions_by_order_id.get(custom_uuid)
            if pos:
                pos["qty"] = int(filled or pos.get("qty", 0))
                pos["avg_price"] = float(avgFillPrice or pos.get("avg_price", 0.0))
                self._positions_by_order_id[custom_uuid] = pos

        except Exception as e:
            logging.error(f"[TWSService] orderStatus() failed for {orderId}: {e}")

    def openOrder(self, orderId, contract, order: IBOrder, orderState):
        logging.info(f"Order opened - ID: {orderId}, Symbol: {contract.symbol}")

    def execDetails(self, reqId, contract, execution):
        order_id = self._ib_to_order_id.get(execution.orderId)
        if not order_id:
            return
        
        pos = self._positions_by_order_id.get(order_id)
        if not pos:
            # 🔧 If not found, check if SELL execution maps to a BUY UUID
            target_uuid = self._ib_to_order_id.get(execution.orderId)
            if target_uuid:
                pos = self._positions_by_order_id.get(target_uuid)
        if not pos:
            return


        side = (execution.side or "").upper()   # BOT or SLD
        old_qty = int(pos["qty"])
        old_avg = float(pos["avg_price"])
        shares = int(execution.shares)
        price = float(execution.price)

        if side == "BOT":
            new_qty = old_qty + shares
            new_avg = ((old_avg * old_qty) + (price * shares)) / new_qty if new_qty > 0 else old_avg
        elif side == "SLD":
            new_qty = max(0, old_qty - shares)
            new_avg = old_avg
        else:
            new_qty, new_avg = old_qty, old_avg

        pos["qty"] = new_qty
        pos["avg_price"] = new_avg
        self._positions_by_order_id[order_id] = pos

        logging.info(f"[TWSService] execDetails update {order_id}: side={side} qty={new_qty}, avg={new_avg}")



    def securityDefinitionOptionParameter(
    self, reqId: int, exchange: str,
    underlyingConId: int, tradingClass: str,
    multiplier: str, expirations: List[str],
    strikes: List[float]
):
        try:
            if reqId not in self._maturities_data or self._maturities_data[reqId] is None:
                self._maturities_data[reqId] = {
                    "exchange": exchange,
                    "underlyingConId": underlyingConId,
                    "tradingClass": tradingClass,
                    "multiplier": multiplier,
                    "expirations": set(),
                    "strikes": set(),
                }
            data = self._maturities_data[reqId]
            before_e, before_s = len(data["expirations"]), len(data["strikes"])
            data["expirations"].update(expirations or [])
            data["strikes"].update(strikes or [])
            logging.info(
                f"[TWSService] Option chain fragment merged for {exchange}: "
                f"{len(data['expirations'])} expirations (+{len(data['expirations']) - before_e}), "
                f"{len(data['strikes'])} strikes (+{len(data['strikes']) - before_s})"
            )
        except Exception as e:
            logging.exception(f"[TWSService] securityDefinitionOptionParameter crash, reqId={reqId}")

    def securityDefinitionOptionParameterEnd(self, reqId: int):
        """Finalize merged option chain"""
        if reqId in self._maturities_data:
            data = self._maturities_data[reqId]
            expirations = sorted(data["expirations"])
            strikes = sorted(data["strikes"])
            self._maturities_data[reqId]["expirations"] = expirations
            self._maturities_data[reqId]["strikes"] = strikes
            logging.info(
                f"[TWSService] Option chain complete: {len(expirations)} expirations, {len(strikes)} strikes"
            )
        self._maturities_event.set()



    def contractDetails(self, reqId: int, contractDetails):
        self._contract_details[reqId] = contractDetails
        self._contract_details_event.set()

    def contractDetailsEnd(self, reqId: int):
        self._contract_details_event.set()

    def connectionClosed(self):
        logging.warning("Connection to TWS closed")
        self.connection_ready.clear()

    def _reader_wrapper(self):
        """Catch everything that kills the reader loop."""
        try:
            self.run()                 # real IB loop
        except Exception as exc:
            logging.exception("IB reader thread died with exception")
        finally:
            logging.warning("IB reader thread ended -> auto-reconnect")
            self.connected = False
            self.connection_ready.clear()
            # optional: schedule reconnect here or raise a flag
            self.connect_and_start()

    def connect_and_start(self, host='127.0.0.1', port=7497, timeout=10):
        """Connect to TWS/IB Gateway"""
        if self.connected:
            return True
        try:
            logging.info(f"Connecting to TWS on {host}:{port} with Client ID: {self.client_id}")
            self.connect(host, port, self.client_id)
            self.connected = True
            api_thread = threading.Thread(
                target=self._reader_wrapper,          # ← new wrapper
                daemon=True,
                name=f"TWS-API-Thread-{self.client_id}"
            )
            
            api_thread.start()
            
            if self.connection_ready.wait(timeout=timeout):
                logging.info("Successfully connected to TWS")
                return True
            else:
                logging.error("Connection timeout")
                return False
                
        except Exception as e:
            logging.error(f"Failed to connect to TWS: {str(e)}")
            return False

    def is_connected(self):
        return self.connection_ready.is_set() and self.next_valid_order_id is not None

    def _get_next_req_id(self):
        req_id = self._request_counter
        self._request_counter += 1
        return req_id

    def get_maturities(self, symbol: str, exchange: str = "SMART", currency: str = "USD", 
                      timeout: int = 10) -> Optional[Dict]:
        """Get option expirations and strikes for a symbol"""
        if not self.is_connected():
            logging.error("Not connected to TWS")
            return None

        # Resolve underlying contract first
        underlying_contract = self.create_stock_contract(symbol, exchange, currency)
        underlying_conid = self.resolve_conid(underlying_contract)
        
        if not underlying_conid:
            logging.error(f"Failed to resolve conId for {symbol}")
            return None

        req_id = self._get_next_req_id()
        self._maturities_req_id = req_id
        self._maturities_data[req_id] = None
        self._maturities_event.clear()

        try:
            logging.info(f"Requesting option chain for {symbol}")
            self.reqSecDefOptParams(
                reqId=req_id, 
                underlyingSymbol=symbol,
                futFopExchange="", 
                underlyingSecType="STK",
                underlyingConId=underlying_conid
            )

            if self._maturities_event.wait(timeout=timeout):
                data = self._maturities_data.get(req_id)
                if data:
                    logging.info(f"Retrieved {len(data['expirations'])} expirations for {symbol}")
                    return data
                else:
                    logging.warning(f"No option chain data for {symbol}")
                    return None
            else:
                logging.error(f"Timeout getting option chain for {symbol}")
                return None

        except Exception as e:
            logging.error(f"Error getting maturities for {symbol}: {str(e)}")
            return None
        finally:
            if req_id in self._maturities_data:
                del self._maturities_data[req_id]

    def resolve_conid(self, contract: Contract, timeout: int = 10) -> Optional[int]:
        """Resolve contract to conId"""
        if not self.is_connected():
            return None

        req_id = self._get_next_req_id()
        event = threading.Event()
        self._contract_details[req_id] = {"event": event, "details": None}

        logging.info(f"[ResolveConId] Starting for {contract.symbol} "
                 f"{getattr(contract, 'lastTradeDateOrContractMonth', '?')} "
                 f"{getattr(contract, 'strike', '?')}{getattr(contract, 'right', '?')} "
                 f"(req_id={req_id}, timeout={timeout}s)")


        def on_contract_details(reqId, contractDetails):
            if reqId == req_id:
                self._contract_details[req_id]["details"] = contractDetails
                event.set()

        def on_contract_details_end(reqId):
            if reqId == req_id:
                event.set()

        # temporarily hook callbacks
        orig_cd = self.contractDetails
        orig_cde = self.contractDetailsEnd
        self.contractDetails = on_contract_details
        self.contractDetailsEnd = on_contract_details_end

        try:
            logging.info(f"[ResolveConId] Requesting contract details from IBKR for {contract.symbol}") 
            start_time = time.time()

            self.reqContractDetails(req_id, contract)
            if event.wait(timeout):
                elapsed = time.time() - start_time
                logging.info(f"[ResolveConId] Callback received for {contract.symbol} after {elapsed:.2f}s")
                data = self._contract_details[req_id]["details"]
                if data:
                    conid = data.contract.conId
                    logging.info(f"[ResolveConId] ✅ Resolved conId={conid} "
                                 f"for {contract.symbol} in {elapsed:.2f}s")
                    return conid
                else:
                    logging.info(f"[ResolveConId] ⚠️ Empty data for {contract.symbol}, "
                                 f"IBKR returned no contract details (elapsed={elapsed:.2f}s)")
                    return None
            else:
                logging.info(f"[ResolveConId] ⏱ Timeout waiting {timeout}s for {contract.symbol} "
                             f"(req_id={req_id})")
                return None

        except Exception as e:
            logging.info(f"[ResolveConId] ❌ Exception resolving {contract.symbol}: {str(e)}")
            return None
        finally:
            self.contractDetails = orig_cd
            self.contractDetailsEnd = orig_cde
            del self._contract_details[req_id]


    def create_option_contract(self, symbol: str, last_trade_date: str, strike: float, right: str, 
                             exchange: str = "SMART", currency: str = "USD") -> Contract:
        """Create IB option contract - converts CALL/PUT to C/P"""
        ib_right = "C" if right.upper() in ["C", "CALL"] else "P"
        
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = currency
        contract.lastTradeDateOrContractMonth = last_trade_date
        contract.strike = float(strike)
        contract.right = ib_right
        contract.multiplier = "100"
        
        return contract

    def create_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Contract:
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract
    
    def get_option_chain(self, symbol: str, expiry: str, exchange: str = "SMART", currency: str = "USD",
                        timeout: int = 10) -> Optional[List[Dict]]:
        """
        Build a basic option chain for a given symbol and expiry.
        Returns a list of dicts with strike/right.
        """
        try:
            maturities = self.get_maturities(symbol, exchange, currency, timeout)
            if not maturities:
                return []

            if expiry not in maturities['expirations']:
                logging.error(f"TWSService: expiry {expiry} not in available expirations for {symbol}")
                return []

            strikes = maturities.get('strikes', [])
            chain = []
            for strike in strikes:
                chain.append({"expiry": expiry, "strike": strike, "right": "C"})
                chain.append({"expiry": expiry, "strike": strike, "right": "P"})
            return chain
        except Exception as e:
            logging.error(f"TWSService: Failed to build option chain for {symbol}: {e}")
            return []

    def get_option_snapshot(self, symbol: str, expiry: str, strike: float, right: str, timeout: int = 3):
        if not self.is_connected():
            logging.error("TWSService.get_option_snapshot(): not connected")
            return None

        contract = self.create_option_contract(symbol, expiry, strike, right)
        conid = self.resolve_conid(contract)
        if not conid:
            logging.error(f"TWSService: Failed to resolve conId for {symbol} {expiry} {strike}{right}")
            return None
        contract.conId = conid

        req_id = self._get_next_req_id()
        result = {"bid": None, "ask": None, "last": None, "mid": None}
        event = threading.Event()

        def tickPrice(reqId, tickType, price, attrib):
            if reqId != req_id or price <= 0:
                return
            if tickType == 1:
                result["bid"] = price
            elif tickType == 2:
                result["ask"] = price
            elif tickType == 4:
                result["last"] = price
            if result["bid"] and result["ask"]:
                result["mid"] = (result["bid"] + result["ask"]) / 2
                event.set()

        original_tick = self.tickPrice
        self.tickPrice = tickPrice

        try:
            self.reqMktData(req_id, contract, "", True, False, [])
            event.wait(timeout)
            bid, ask = result["bid"], result["ask"]
            result["mid"] = (bid + ask) / 2 if bid and ask else bid or ask
            logging.info(f"[TWSService] Snapshot for {symbol} {expiry} {strike}{right}: {result}")
            return result
        finally:
            try:
                self.cancelMktData(req_id)
            except Exception:
                pass
            self.tickPrice = original_tick

    def pre_conid(self, custom_order: Order) -> bool:
        """
        Pre-resolve conId BEFORE order placement.
        Useful for pre-market where we want everything ready.
        """
        logging.info(f"[TWSSwervice] doing pre-conid for order: {custom_order}")
        try:
            key = (
                custom_order.symbol.upper(),
                custom_order.expiry,
                float(custom_order.strike),
                custom_order.right.upper(),
            )

            # 1. Already cached?
            if key in self._pre_conid_cache:
                conid = self._pre_conid_cache[key]
                custom_order._pre_conid = conid
                logging.info(f"[TWSService] pre_conid CACHE HIT {key} → {conid}")
                return True

            # 2. Build contract
            contract = self.create_option_contract(
                symbol=custom_order.symbol,
                last_trade_date=custom_order.expiry,
                strike=custom_order.strike,
                right=custom_order.right,
            )

            # 3. Resolve it
            conid = self.resolve_conid(contract)
            if not conid:
                logging.error(f"[TWSService] pre_conid FAILED {key}")
                return False

            # 4. Save to cache
            self._pre_conid_cache[key] = conid
            custom_order._pre_conid = conid

            logging.info(f"[TWSService] pre_conid READY {key} → {conid}")
            return True

        except Exception as e:
            logging.error(f"[TWSService] pre_conid ERROR {e}")
            return False


    def place_custom_order(self, custom_order: Order, account: str = "") -> bool:
        """
        Place an order using your custom Order object from Helpers.Order.
        """
        if not self.is_connected():
            logging.error(f"Cannot place order: Not connected to TWS")
            return False

        try:
            # Convert your custom order to IB contract
            ib_right = "C" if custom_order.right.upper() in ["C", "CALL"] else "P"
            
            contract = self.create_option_contract(
                symbol=custom_order.symbol,
                last_trade_date=custom_order.expiry,
                strike=custom_order.strike,
                right=ib_right,
                exchange="SMART",
                currency="USD"
            )

            # ✅ Resolve contract to avoid error 200
            key = (
                custom_order.symbol.upper(),
                custom_order.expiry,
                float(custom_order.strike),
                custom_order.right.upper(),
            )
            precon = self._pre_conid_cache.get(key)
            if precon:
                conid = precon 
            else:
                conid = self.resolve_conid(contract)
            if not conid:
                logging.error(f"Could not resolve contract for {custom_order.symbol} {custom_order.expiry} {custom_order.strike}{ib_right}")
                custom_order.mark_failed("Contract resolution failed")
                return False

            contract.conId = conid

            # --- Premium snapshot ---
            # NOTE: We use get_option_premium now for a robust (Polygon fallback) price
            #premium = self.get_option_premium(custom_order.symbol, custom_order.expiry, custom_order.strike, ib_right)
            #if not premium or premium <= 0:
                #raise RuntimeError(f"No live premium for {custom_order.symbol} {custom_order.expiry} {custom_order.strike}{ib_right}")

            base_price = custom_order.entry_price #or premium

            # ✅ FIXED QTY CALC
            if getattr(custom_order, "_position_size", None):
                qty = custom_order.calc_contracts_from_premium(base_price)
            else:
                # fallback to manually set qty (legacy behavior)
                qty = custom_order.qty if getattr(custom_order, "qty", None) else 1

            #Safety clamp
            notional = qty * base_price * 100
            if notional > custom_order._position_size *1.5:
                 logging.error(
                    f"[RISK-GUARD] {custom_order.symbol} notional {notional:.2f} > 1.5× target {custom_order._position_size}. "
                    f"premium_used={base_price}, qty={qty}. Blocking order."
                )
                 return False
            custom_order.qty = qty

            # Debug info
            logging.info(
                f"[TWSService] Calculated qty={qty} for {custom_order.symbol} "
                f"premium={base_price}, position_size={getattr(custom_order, '_position_size', None)}"
            )

            # --- Build IB order ---
            closing = custom_order.action == "SELL"
            # NOTE: to_ib_order needs to be updated to support outside_rth for pre-market orders
            ib_order = custom_order.to_ib_order(
                order_type=custom_order.type,
                limit_price=custom_order.entry_price,
                transmit=True,
                closing=closing
            )
            ib_order.account = account

            ib_order_id = self.next_valid_order_id
            custom_order._ib_order_id = ib_order_id
            self._ib_to_order_id[ib_order_id] = custom_order.order_id

            self._positions_by_order_id[custom_order.order_id] = {
                "qty": 0,
                "avg_price": 0.0,
                "symbol": custom_order.symbol,
                "expiry": custom_order.expiry,
                "strike": custom_order.strike,
                "right": custom_order.right,
            }

            self._pending_orders[custom_order.order_id] = custom_order

            self.placeOrder(ib_order_id, contract, ib_order)
            custom_order._placed_ts = time.time() * 1000

            logging.info(f"[TWSService] Sent order {custom_order.symbol} IBID={ib_order_id} "
                        f"at {custom_order._placed_ts:.0f} ms")
            logging.info(f"Placed custom order: {custom_order.order_id} -> IB ID: {ib_order_id}")

            # Increment order ID for next use
            self.next_valid_order_id += 1
            return True

        except Exception as e:
            logging.error(f"Failed to place custom order {custom_order.order_id}: {str(e)}")
            custom_order.mark_failed(reason=str(e))
            return False

    def cancel_custom_order(self, custom_order_id: str) -> bool:
        """Cancel a custom order"""
        if custom_order_id in self._pending_orders:
            order = self._pending_orders[custom_order_id]
            if hasattr(order, '_ib_order_id'):
                self.cancelOrder(order._ib_order_id)
                order.mark_cancelled()
                logging.info(f"Cancelled order {custom_order_id}")
                return True
        return False

    def get_order_status(self, custom_order_id: str) -> Optional[Dict]:
        """
        Get the status of a custom order.
        """
        if custom_order_id in self._pending_orders:
            order = self._pending_orders[custom_order_id]
            return order.to_dict()
        return None
    def disconnect_gracefully(self):
        logging.info("Disconnecting from TWS...")
        self.connection_ready.clear()
        self.disconnect()
    
    def sell_custom_order(self, custom_order: Order, contract : Contract, account: str = "", ) -> bool:
        """
        Dedicated SELL method for option orders.
        - Uses the correct closing quantity if a position ID is provided (from the StopLoss Watcher).
        - Otherwise, dynamically recalculates quantity from live premium if position_size is set.
        """
        if not self.is_connected():
            logging.error("Cannot place SELL order: Not connected to TWS")
            return False

        try:
            custom_order.action = "SELL"
            ib_right = "C" if custom_order.right.upper() in ["C", "CALL"] else "P"
            
            # --- FIX START: Ensure Quantity for Closing Order is NOT Recalculated ---
            # A closing order should already have custom_order.qty set to the position size 
            # by sell_position_by_order_id, so we skip recalculation.
            is_closing_position = getattr(custom_order, "previous_id", None) and custom_order.qty > 0

            if not is_closing_position:
                # Original dynamic sizing logic for non-closing orders (if intentionally opening new short or complex position)
                premium = self.get_option_premium(custom_order.symbol, custom_order.expiry, custom_order.strike, ib_right)
                if not premium or premium <= 0:
                    raise RuntimeError(f"No live premium for {custom_order.symbol} {custom_order.expiry} {custom_order.strike}{ib_right}")

                if getattr(custom_order, "_position_size", None):
                    qty = custom_order.calc_contracts_from_premium(premium)
                    custom_order.qty = qty
                elif not getattr(custom_order, "qty", None):
                    raise RuntimeError("SELL order has neither qty nor position_size set")
            
            # --- FIX END ---


            # Build IB order
            ib_order = custom_order.to_ib_order(
                order_type=custom_order.type,
                limit_price=custom_order.entry_price,
                transmit=True
            )
            ib_order.account = account

            order_id = self.next_valid_order_id
            custom_order._ib_order_id = order_id
            self._pending_orders[custom_order.order_id] = custom_order
            
            # --- Link SELL IB ID to BUY UUID ---
            buy_order_uuid = custom_order.previous_id
            if buy_order_uuid and buy_order_uuid in self._positions_by_order_id:
                # Link the new SELL IB ID to the original BUY custom UUID
                self._ib_to_order_id[order_id] = buy_order_uuid
                logging.info(f"[TWSService] Linked SELL IBID {order_id} to BUY Position UUID {buy_order_uuid}")
            else:
                # Fallback: link to its own ID if position is not found
                logging.warning(f"[TWSService] No BUY position found for {buy_order_uuid}. Linking SELL to itself.")
                self._ib_to_order_id[order_id] = custom_order.order_id


            self.placeOrder(order_id, contract, ib_order)
            custom_order._placed_ts = time.time() * 1000
            logging.info(
                f"[TWSService] SELL placed: {custom_order.symbol} {custom_order.expiry} "
                f"{custom_order.strike}{ib_right} x{custom_order.qty} @ {custom_order.entry_price} "
                f"→ ID {order_id}"
            )

            self.next_valid_order_id += 1
            return True

        except Exception as e:
            logging.error(f"[TWSService] Failed to sell order {custom_order.order_id}: {e}")
            custom_order.mark_failed(reason=str(e))
            return False


    def get_position_by_order_id(self, order_id: str):
        return self._positions_by_order_id.get(order_id)

    def has_position(self, order_id_or_symbol: str) -> bool:
        # check by UUID first
        pos = self._positions_by_order_id.get(order_id_or_symbol)
        if pos and pos["qty"] > 0:
            return True
        # fallback by symbol
        if hasattr(self, "_positions_by_symbol"):
            uuid = self._positions_by_symbol.get(order_id_or_symbol)
            if uuid:
                pos = self._positions_by_order_id.get(uuid)
                return bool(pos and pos["qty"] > 0)
        return False


    def sell_position_by_order_id(self, order_id: str, contract : Contract, qty: int | None = None,
                              limit_price: float | None = None, account: str = "", ex_order: Optional[Order] = None) -> bool:
        pos = self._positions_by_order_id.get(order_id)
        if not pos or pos["qty"] <= 0:
            logging.warning(f"[TWSService] sell_position_by_order_id: no live position for {order_id}")
            logging.warning(f"The Position {pos}")
            logging.warning(f"the dict {self._positions_by_order_id}")
            return False

        sell_qty = qty or pos["qty"]

        ex_order.symbol = pos["symbol"]
        ex_order.expiry = pos["expiry"]
        ex_order.strike = pos["strike"]
        ex_order.right = pos["right"]
        ex_order.qty  = sell_qty
        
        # 💡 FIX: Set the previous_id on the exit order so sell_custom_order can link it
        ex_order.previous_id = order_id 
        
        # 💡 FIX: Ensure the exit order has the correct limit price if it's LMT (or None if MKT)
        if limit_price is not None:
             ex_order.entry_price = limit_price
        else:
             ex_order.entry_price = ex_order.entry_price # Keep existing for MKT reference

        ok = self.sell_custom_order(ex_order, contract, account=account)
        if ok:
            logging.info(f"[TWSService] SELL order submitted for {order_id}, waiting for fill confirmation.")
            # 🔧 Do NOT modify qty here; handled in orderStatus/execDetails

        return ok


    def get_option_premium(self, symbol: str, expiry: str, strike: float, right: str, timeout: int = 3) -> Optional[float]:
        """
        Live premium for a *single* option contract.
        Prioritizes Polygon data if the market is closed/pre-market.
        """
        # --- 💡 MODIFIED PRE-MARKET BLOCK 💡 ---
        # If market is closed/pre-market, ONLY use Polygon.
        # Do not fall through to TWS logic if Polygon fails.
        if is_market_closed_or_pre_market(): #
            logging.warning("[TWSService] Market closed/pre-market; prioritizing Polygon for premium.")
            try:
                snap = polygon_service.get_option_snapshot(symbol, expiry, strike, right) #
                if snap and snap.get("mid"):
                    logging.info(f"[TWSService] Polygon premium (Outside RTH) {snap['mid']}")
                    return snap["mid"] # <-- Return on success
                else:
                    logging.error(f"[TWSService] Polygon (Outside RTH) failed to find premium for {symbol}.")
                    return None # <-- 💡 MUST return None on failure
            except Exception as e:
                logging.error(f"[TWSService] Polygon primary lookup failed: {e}")
                return None # <-- 💡 MUST return None on exception
        # --- END MODIFIED BLOCK ---

        # --- Regular Trading Hours or Polygon Failed: Try IBKR First ---
        if not self.is_connected():
            logging.warning("[TWSService] Not connected to TWS; falling back to Polygon.")
            try:
                # This part now serves as a final fallback if the initial check failed
                snap = polygon_service.get_option_snapshot(symbol, expiry, strike, right)
                if snap and snap.get("mid"):
                    logging.info(f"[TWSService] Polygon fallback premium {snap['mid']}")
                    return snap["mid"]
                return None
            except Exception as e:
                logging.error(f"[TWSService] Polygon fallback failed: {e}")
                return None

        # --- IBKR Request Logic (Only runs if connected and during RTH or Polygon failed) ---
        contract = self.create_option_contract(symbol, expiry, strike, right)
        conid = self.resolve_conid(contract)
        if not conid:
            logging.warning(f"[TWSService] Failed to resolve conId for {symbol}, trying Polygon fallback.")
            snap = polygon_service.get_option_snapshot(symbol, expiry, strike, right)
            return snap["mid"] if snap else None

        contract.conId = conid
        req_id = self._get_next_req_id()
        tick_snapshot = {"bid": None, "ask": None}
        event = threading.Event()

        def tickPrice(reqId, tickType, price, attrib):
            if reqId != req_id or price <= 0:
                return
            if tickType == 1:
                tick_snapshot["bid"] = price
            elif tickType == 2:
                tick_snapshot["ask"] = price
            if tick_snapshot["bid"] is not None and tick_snapshot["ask"] is not None:
                event.set()

        original_tick = self.tickPrice
        self.tickPrice = tickPrice

        try:
            self.reqMktData(req_id, contract, "", True, False, [])
            event.wait(timeout)
            bid = tick_snapshot["bid"]
            ask = tick_snapshot["ask"]
            mid = (bid + ask) / 2 if (bid and ask) else bid or ask
            if mid:
                logging.info(f"[TWSService] Premium snapshot for {symbol} {expiry} {strike}{right}: bid={bid}, ask={ask}, mid={mid}")
                return mid

            # --- Fallback to Polygon if TWS failed/timed out ---
            logging.warning(f"[TWSService] No IBKR premium for {symbol} {expiry} {strike}{right}, fetching from Polygon...")
            snap = polygon_service.get_option_snapshot(symbol, expiry, strike, right)
            if snap and snap.get("mid"):
                logging.info(f"[TWSService] Polygon final fallback premium {snap['mid']}")
                return snap["mid"]
            elif snap and snap.get("last"):
                logging.info(f"[TWSService] Polygon last-trade fallback {symbol}: {snap['last']}")
                return snap["last"]
            return None
        finally:
            try:
                self.cancelMktData(req_id)
            except Exception:
                pass
            self.tickPrice = original_tick

service = TWSService()


def create_tws_service() -> TWSService:
    
    
    return service




# Test the service
if __name__ == "__main__":
    print("Testing TWSService with Helpers.Order integration")
    
    # Import YOUR Order class
    from Helpers.Order import Order
    
    service = create_tws_service()
    
    if service.connect_and_start(port=7497):
        print("✓ Connected to TWS")
        
        try:
            # Test data retrieval
            print("Testing option chain data...")
            maturities = service.get_maturities("SPY")
            if maturities:
                print(f"✓ Got {len(maturities['expirations'])} expirations")
            
            # Test with YOUR Order class
            print("Testing with Helpers.Order...")
            my_order = Order(
                symbol="SPY",
                expiry="20241220",
                strike=450.0,
                right="CALL",
                qty=1,
                entry_price=2.50,
                tp_price=5.00,
                sl_price=1.00
            )
            
            print(f"✓ Created Helpers.Order: {my_order.order_id}")
            print(f"  {my_order.symbol} {my_order.expiry} {my_order.strike}{my_order.right}")
            
            # Order placement ready (commented for safety)
            print("✓ Order system integrated and ready")
            print("Uncomment to test order placement:")
            print("# service.place_custom_order(my_order)")
            
        except Exception as e:
            print(f"Error: {e}")
        
        service.disconnect_gracefully()
        print("✓ Disconnected")