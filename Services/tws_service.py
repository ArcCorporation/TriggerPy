# tws_service.py
import time
import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order as IBOrder
from ibapi.common import BarData
from threading import Thread, Event
from Helpers.Order import Order


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class TWSService(EWrapper, EClient):
    def __init__(self, host="127.0.0.1", port=7497, client_id=1):
        EClient.__init__(self, self)
        self.host = host
        self.port = port
        self.client_id = client_id

        # Synchronization
        self.thread = None
        self.stop_event = Event()

        # IBKR state
        self.req_id = 9000
        self.nextOrderId = None
        self.conid_map = {}              # symbol → conId
        self.contract_maturities = {}    # symbol → [expiries]
        self.details_data = {}           # reqId → [details]

    # ---------------- Connection ----------------
    def connect_and_run(self):
        self.connect(self.host, self.port, self.client_id)
        self.thread = Thread(target=self.run, daemon=True)
        self.thread.start()
        logging.info("Connected to TWS")

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.nextOrderId = orderId
        logging.info(f"NextValidId set: {orderId}")

    # ---------------- Symbol Search ----------------
    def search_symbol(self, pattern: str):
        req_id = self._next_req_id()
        self.symbols = []
        self.reqMatchingSymbols(req_id, pattern)
        time.sleep(2)
        return self.symbols

    def symbolSamples(self, reqId, contractDescriptions):
        results = []
        for desc in contractDescriptions:
            results.append({
                "symbol": desc.contract.symbol,
                "secType": desc.contract.secType,
                "currency": desc.contract.currency,
                "exchange": desc.contract.exchange,
                "primaryExchange": desc.contract.primaryExchange,
                "description": desc.derivativeSecTypes
            })
        self.symbols = results

    # ---------------- Contract Details ----------------
    def resolve_conid(self, symbol: str, secType="STK", currency="USD", exchange="SMART"):
        req_id = self._next_req_id()
        contract = Contract()
        contract.symbol = symbol
        contract.secType = secType
        contract.currency = currency
        contract.exchange = exchange
        self.details_data[req_id] = []
        self.reqContractDetails(req_id, contract)
        time.sleep(2)
        if self.details_data[req_id]:
            conId = self.details_data[req_id][0].contract.conId
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

    def get_option_chain(self, symbol: str, expiry: str):
        req_id = self._next_req_id()
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        self.details_data[req_id] = []
        self.reqContractDetails(req_id, contract)
        time.sleep(3)
        chain = []
        for cd in self.details_data[req_id]:
            chain.append({
                "symbol": cd.contract.symbol,
                "expiry": cd.contract.lastTradeDateOrContractMonth,
                "strike": cd.contract.strike,
                "right": cd.contract.right,
                "exchange": cd.contract.exchange,
                "currency": cd.contract.currency,
                "conId": cd.contract.conId,
            })
        return chain

    def contractDetails(self, reqId, contractDetails):
        self.details_data.setdefault(reqId, []).append(contractDetails)
        sym = contractDetails.contract.symbol
        exp = contractDetails.contract.lastTradeDateOrContractMonth
        if exp:
            self.contract_maturities.setdefault(sym, []).append(exp)

    def contractDetailsEnd(self, reqId):
        logging.info(f"contractDetailsEnd for reqId {reqId}")

    # ---------------- Order Helpers ----------------
    def option_contract(self, symbol, expiry, strike, right, action, exchange="SMART"):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = strike
        contract.right = right
        return contract

    def place_bracket_order(self, order: Order):
        if self.nextOrderId is None:
            logging.error("No valid order ID available yet")
            return []

        parent_id = self.nextOrderId
        self.nextOrderId += 3

        # Parent
        parent = IBOrder()
        parent.orderId = parent_id
        parent.action = order.action
        parent.totalQuantity = order.qty
        parent.orderType = "LMT"
        parent.lmtPrice = order.entry_price
        parent.transmit = False

        # Take profit
        tp = IBOrder()
        tp.orderId = parent_id + 1
        tp.action = "SELL" if order.action == "BUY" else "BUY"
        tp.totalQuantity = order.qty
        tp.orderType = "LMT"
        tp.lmtPrice = order.tp_price
        tp.parentId = parent_id
        tp.transmit = False

        # Stop loss
        sl = IBOrder()
        sl.orderId = parent_id + 2
        sl.action = "SELL" if order.action == "BUY" else "BUY"
        sl.totalQuantity = order.qty
        sl.orderType = "STP"
        sl.auxPrice = order.sl_price
        sl.parentId = parent_id
        sl.transmit = True

        contract = self.option_contract(order.symbol, order.expiry, order.strike, order.right, order.action)
        self.placeOrder(parent.orderId, contract, parent)
        self.placeOrder(tp.orderId, contract, tp)
        self.placeOrder(sl.orderId, contract, sl)

        logging.info(f"Placed bracket order: parent {parent_id}, tp {parent_id+1}, sl {parent_id+2}")
        return [parent_id, parent_id+1, parent_id+2]

    # ---------------- Utils ----------------
    def _next_req_id(self):
        self.req_id += 1
        return self.req_id


# ---------------- Test Main ----------------
if __name__ == "__main__":
    tws = TWSService()
    tws.connect_and_run()

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
