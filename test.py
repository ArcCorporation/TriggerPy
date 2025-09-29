# test.py - TSLA Trigger Order Test using AppModel only

import logging
import time
from model import app_model
import Services.nasdaq_info as nasdaq_info


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )


def main(timeout_sec: int = 120):
    setup_logging()

    # ✅ Check market hours first
    if not nasdaq_info.is_market_open():
        print("⚠️ Market is closed!")
        print(nasdaq_info.market_status_string())
        print("➡️ Please try again when the market is open.")
        return

    print("✅ Market is open:", nasdaq_info.market_status_string())

    # Connect
    if not app_model.connect_services():
        print("❌ Failed to connect services")
        return
    print("✅ Connected to services")

    # Choose TSLA
    app_model.symbol = "TSLA"
    current_price = app_model.refresh_market_price()
    if current_price is None:
        print("❌ Could not fetch TSLA price")
        return
    print(f"TSLA current price: {current_price}")

    # Pick first available expiry (already sorted in model)
    maturities = app_model.get_available_maturities()
    if not maturities:
        print("❌ No maturities found")
        return
    expiry = maturities[0]

    # Pick strike nearest to price
    strikes = app_model.get_available_strikes(expiry)
    if not strikes:
        print("❌ No strikes found")
        return
    strike = min(strikes, key=lambda s: abs(s - current_price))

    app_model.set_option_contract(expiry, strike, "CALL")
    print(f"✅ Contract set: {expiry} {strike}C")

    # Place trigger 0.10 above current
    trigger_price = round(current_price + 0.10, 2)
    print(f"🎯 Placing TSLA breakout order with trigger {trigger_price}")

    order = app_model.place_option_order(
        action="BUY",
        quantity=1,
        trigger_price=trigger_price
    )
    print(f"✅ Order created: {order.get('order_id')} (state={order.get('state')})")

    # Monitor
    print(f"\n⏳ Waiting for trigger... (timeout {timeout_sec}s)")
    start = time.time()
    while time.time() - start < timeout_sec:
        orders = app_model.get_orders()
        for o in orders:
            if o.get("state") == "ACTIVE":
                print(f"🚀 Triggered! Order {o.get('order_id')} is ACTIVE")
                app_model.disconnect_services()
                print("✅ Disconnected")
                return
        time.sleep(2)

    print("⌛ Timeout reached, order not triggered yet.")
    app_model.disconnect_services()
    print("✅ Disconnected")


if __name__ == "__main__":
    main()
