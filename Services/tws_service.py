import time
import random
import logging
import threading
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from Helpers.Order import Order

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class TWSService(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None
        self.symbol_samples = {}
        self.contract_details = {}
        self.contract_maturities = {}   # symbol → expiries
        self.conid_map = {}             # symbol → conId
        self.details_data = {}
        self.req_id = 9000

    # ---------------- Connection ----------------
    def connect_and_run(self, host="127.0.0.1", port=7497, client_id=None):
        if client_id is None:
            client_id = random.randint(1, 10000)
        self.connect(host, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        logging.info("Connected to TWS")

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId
        logging.info(f"NextValidId set: {orderId}")

    def error(self, reqId, errorCode, errorString, *args):
        if errorCode < 2000:
            logging.error(f"ERROR {reqId} {errorCode} {errorString}")

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
            reqId = self._next_req_id()
        self.reqMatchingSymbols(reqId, name)
        time.sleep(2)
        return self.symbol_samples.get(reqId, [])

    # ---------------- Contract Details ----------------
    def contractDetails(self, reqId, contractDetails):
        cd = contractDetails.contract
        self.contract_details.setdefault(reqId, []).append({
            "symbol": cd.symbol,
            "expiry": cd.lastTradeDateOrContractMonth,
            "strike": cd.strike,
            "right": cd.right,
            "exchange": cd.exchange,
            "currency": cd.currency,
            "conId": cd.conId
        })
        if cd.lastTradeDateOrContractMonth:
            self.contract_maturities.setdefault(cd.symbol, []).append(cd.lastTradeDateOrContractMonth)

    def contractDetailsEnd(self, reqId):
        logging.info(f"contractDetailsEnd for reqId {reqId}")

    def resolve_conid(self, symbol: str, secType="STK", currency="USD", exchange="SMART"):
        req_id = self._next_req_id()
        c = Contract()
        c.symbol = symbol
        c.secType = secType
        c.currency = currency
        c.exchange = exchange
        self.details_data[req_id] = []
        self.reqContractDetails(req_id, c)
        time.sleep(2)
        if self.contract_details.get(req_id):
            conId = self.contract_details[req_id][0]["conId"]
            self.conid_map[symbol] = conId
            logging.info(f"Resolved {symbol} {secType} → conId {conId}")
            return conId
        return None

    def get_maturities(self, symbol: str):
        conId = self.conid_map.get(symbol)
        if not conId:
            conId = self.resolve_conid(symbol, secType="OPT")
        if not conId:
            logging.warning(f"No conId for {symbol}")
            return []
        return self.contract_maturities.get(symbol, [])

    def get_option_chain(self, symbol: str, expiry: str, reqId: int = None):
        if reqId is None:
            reqId = self._next_req_id()
        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        c.lastTradeDateOrContractMonth = expiry
        self.reqContractDetails(reqId, c)
        time.sleep(2)
        return self.contract_details.get(reqId, [])

    # ---------------- Order Helpers ----------------
    def option_contract(self, symbol, expiry, strike, right, exchange="SMART", currency="USD"):
        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = exchange
        c.currency = currency
        c.lastTradeDateOrContractMonth = expiry
        c.strike = float(strike)
        c.right = right
        c.multiplier = "100"
        return c

    def place_bracket_order(self, order: Order):
        if self.nextOrderId is None:
            raise Exception("NextValidId not received, call connect_and_run() first")

        parent_id = self.nextOrderId
        self.nextOrderId += 1

        contract = self.option_contract(order.symbol, order.expiry, order.strike, order.right)

        # Parent
        parent = order.to_ib_order("LMT", limit_price=order.entry_price, parent_id=None, transmit=False)
        parent.orderId = parent_id

        # Take profit (same action set inside order.to_ib_order)
        tp = order.to_ib_order("LMT", limit_price=order.tp_price, parent_id=parent_id, transmit=False)
        tp.orderId = self.nextOrderId
        self.nextOrderId += 1

        # Stop loss (same action set inside order.to_ib_order)
        sl = order.to_ib_order("STP", stop_price=order.sl_price, parent_id=parent_id, transmit=True)
        sl.orderId = self.nextOrderId
        self.nextOrderId += 1

        self.placeOrder(parent.orderId, contract, parent)
        self.placeOrder(tp.orderId, contract, tp)
        self.placeOrder(sl.orderId, contract, sl)

        result = {"parent": parent.orderId, "tp": tp.orderId, "sl": sl.orderId}
        order.mark_active(result)
        logging.info(f"Placed bracket: {result}")
        return result

    # ---------------- Utils ----------------
    def _next_req_id(self):
        self.req_id += 1
        return self.req_id


# ---------------- CLI Test ----------------
if __name__ == "__main__":
    tws = TWSService()
    tws.connect_and_run()

    while tws.nextOrderId is None:
        logging.info("Waiting for IBKR connection to be ready...")
        time.sleep(1)

    syms = tws.search_symbol("TSLA")
    logging.info(f"Search result: {syms[:3]}")

    conid = tws.resolve_conid("TSLA", secType="OPT")
    maturities = tws.get_maturities("TSLA")
    logging.info(f"Available expiries: {maturities}")

    if maturities:
        expiry = maturities[0]
        chain = tws.get_option_chain("TSLA", expiry)
        logging.info(f"Chain sample: {chain[:3]}")

        if chain:
            my_order = Order(
                symbol="TSLA",
                expiry=chain[0]["expiry"],
                strike=chain[0]["strike"],
                right=chain[0]["right"],
                qty=1,
                entry_price=5.0,
                tp_price=7.0,
                sl_price=4.0,
                action="BUY"
            )
            order_ids = tws.place_bracket_order(my_order)
            logging.info(f"Placed bracket: {order_ids}")

    time.sleep(5)
