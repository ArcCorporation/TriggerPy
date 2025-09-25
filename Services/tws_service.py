# Services/tws_service.py
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
import threading
import time


class TWSService(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None
        self.symbol_samples = {}
        self.contract_details = {}

    # --- Connection / Lifecycle ---
    def connect_and_run(self, host="127.0.0.1", port=7497, client_id=1):
        self.connect(host, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(1)

    def nextValidId(self, orderId: int):
        self.nextOrderId = orderId

    def error(self, reqId, errorCode, errorString):
        # suppress IB spam (only show real errors)
        if errorCode < 2000:
            print(f"[Error] reqId={reqId}, code={errorCode}, msg={errorString}")

    # --- Symbol Search ---
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

    def search_symbol(self, name: str, reqId: int = 9001):
        self.reqMatchingSymbols(reqId, name)
        time.sleep(2)
        return self.symbol_samples.get(reqId, [])

    # --- Option Chain Fetch ---
    def contractDetails(self, reqId, contractDetails):
        cd = contractDetails.contract
        if reqId not in self.contract_details:
            self.contract_details[reqId] = []
        self.contract_details[reqId].append({
            "symbol": cd.symbol,
            "expiry": cd.lastTradeDateOrContractMonth,
            "strike": cd.strike,
            "right": cd.right,
            "exchange": cd.exchange,
            "currency": cd.currency
        })

    def get_option_chain(self, symbol: str, reqId: int = 9101, expiry: str = None):
        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        if expiry:
            c.lastTradeDateOrContractMonth = expiry
        self.reqContractDetails(reqId, c)
        time.sleep(2)
        return self.contract_details.get(reqId, [])

    # --- Contract Builders ---
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

    # --- Order Builder ---
    def create_order(self, action, quantity, order_type="MKT",
                     limit_price=None, stop_price=None,
                     parent_id=None, transmit=True):
        o = Order()
        o.action = action
        o.totalQuantity = quantity
        o.orderType = order_type
        if order_type == "LMT" and limit_price is not None:
            o.lmtPrice = limit_price
        if order_type == "STP" and stop_price is not None:
            o.auxPrice = stop_price
        if parent_id is not None:
            o.parentId = parent_id
        o.transmit = transmit
        o.eTradeOnly = False
        o.firmQuoteOnly = False
        return o

    # --- Place Bracket Order (Options) ---
    def place_bracket_order(self, symbol, expiry, strike, right,
                            action, quantity, entry_price,
                            take_profit_price, stop_loss_price):
        if self.nextOrderId is None:
            raise Exception("NextValidId not received, call connect_and_run() first")

        parent_id = self.nextOrderId
        self.nextOrderId += 1

        contract = self.option_contract(symbol, expiry, strike, right)

        parent = self.create_order(action, quantity, "LMT",
                                   limit_price=entry_price,
                                   transmit=False)
        parent.orderId = parent_id

        tp = self.create_order("SELL" if action == "BUY" else "BUY",
                               quantity, "LMT",
                               limit_price=take_profit_price,
                               parent_id=parent_id,
                               transmit=False)
        tp.orderId = self.nextOrderId
        self.nextOrderId += 1

        sl = self.create_order("SELL" if action == "BUY" else "BUY",
                               quantity, "STP",
                               stop_price=stop_loss_price,
                               parent_id=parent_id,
                               transmit=True)
        sl.orderId = self.nextOrderId
        self.nextOrderId += 1

        self.placeOrder(parent.orderId, contract, parent)
        self.placeOrder(tp.orderId, contract, tp)
        self.placeOrder(sl.orderId, contract, sl)

        return {
            "parent": parent.orderId,
            "take_profit": tp.orderId,
            "stop_loss": sl.orderId
        }


# --- Example CLI Run ---
if __name__ == "__main__":
    tws = TWSService()
    tws.connect_and_run()

    # 1. Symbol search
    syms = tws.search_symbol("TSLA")
    print("Search result:", syms[:3])  # first 3 matches

    # 2. Option chain fetch
    chain = tws.get_option_chain("TSLA", expiry="20250926")
    print("Chain sample:", chain[:3])

    # 3. Place example order
    if chain:
        order_ids = tws.place_bracket_order(
            symbol="TSLA",
            expiry=chain[0]["expiry"],
            strike=chain[0]["strike"],
            right=chain[0]["right"],
            action="BUY",
            quantity=1,
            entry_price=5.0,
            take_profit_price=7.0,
            stop_loss_price=4.0
        )
        print("Placed bracket:", order_ids)

    time.sleep(5)
