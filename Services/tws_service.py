from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order as IBOrder
import threading
import time
import random
import logging
from Order import Order



class TWSService(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.nextOrderId = None
        self.symbol_samples = {}
        self.contract_details = {}

    # --- Connection / Lifecycle ---
    def connect_and_run(self, host="127.0.0.1", port=7497, client_id=1):
        id = random.randint(1, 10000)
        try:
            self.connect(host, port, id)
        except Exception:
            logging.debug("connection erroe")
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

    # --- Contract Builder ---
    def option_contract(self, symbol, expiry, strike, right,
                        exchange="SMART", currency="USD"):
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

    # --- Place Bracket Order (Options) ---
    def place_bracket_order(self, order: Order):
        if self.nextOrderId is None:
            raise Exception("NextValidId not received, call connect_and_run() first")

        parent_id = self.nextOrderId
        self.nextOrderId += 1

        contract = self.option_contract(order.symbol, order.expiry, order.strike, order.right)

        # Parent: limit entry
        parent = order.to_ib_order(
            order_type="LMT",
            limit_price=order.entry_price,
            parent_id=None,
            transmit=False
        )
        parent.orderId = parent_id

        # Take Profit: opposite side, limit
        tp = order.to_ib_order(
            action="SELL" if order.action == "BUY" else "BUY",
            order_type="LMT",
            limit_price=order.tp_price,
            parent_id=parent_id,
            transmit=False
        )
        tp.orderId = self.nextOrderId
        self.nextOrderId += 1

        # Stop Loss: opposite side, stop
        sl = order.to_ib_order(
            action="SELL" if order.action == "BUY" else "BUY",
            order_type="STP",
            stop_price=order.sl_price,
            parent_id=parent_id,
            transmit=True
        )
        sl.orderId = self.nextOrderId
        self.nextOrderId += 1

        # Place all 3 orders
        self.placeOrder(parent.orderId, contract, parent)
        self.placeOrder(tp.orderId, contract, tp)
        self.placeOrder(sl.orderId, contract, sl)

        result = {
            "parent": parent.orderId,
            "take_profit": tp.orderId,
            "stop_loss": sl.orderId
        }

        order.mark_active(result)
        return result


# --- Example CLI Run ---
if __name__ == "__main__":
    from Order import Order

    tws = TWSService()
    tws.connect_and_run()

    syms = tws.search_symbol("TSLA")
    print("Search result:", syms[:3])

    chain = tws.get_option_chain("TSLA", expiry="20250926")
    print("Chain sample:", chain[:3])

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
        print("Placed bracket:", order_ids)

    time.sleep(5)
