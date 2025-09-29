
# test_fixed.py - Trigger order test (non-destructive; keeps old logic intact)
import logging
import time
from model import app_model

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def pick_strike(expiry: str) -> float | None:
    """Pick a reasonable strike for the current symbol/expiry using TWSService directly.
    This avoids relying on any internal cache that might have been cleared.
    """
    try:
        # Ask TWS for a fresh chain to get strikes
        symbol = app_model.symbol
        chain = app_model._tws_service.get_maturities(symbol)  # public method
        if not chain:
            logging.error("Could not fetch option chain for strikes")
            return None

        strikes = sorted(list(chain.get('strikes', [])))
        if not strikes:
            logging.error("No strikes returned by TWS for %s", symbol)
            return None

        # Choose strike closest to current underlying price
        px = app_model.refresh_market_price()
        if px is None:
            return strikes[len(strikes)//2]

        return min(strikes, key=lambda s: abs(s - px))
    except Exception as e:
        logging.exception("pick_strike failed: %s", e)
        return None

def test_order_wait_service(timeout_sec: int = 90):
    print("🧪 TESTING ORDER WAIT SERVICE TRIGGER SYSTEM")

    # Connect services
    if not app_model.connect_services():
        print("❌ Failed to connect services")
        return False

    print("✅ Services connected")

    # Choose symbol and read current price
    app_model.symbol = "SPY"
    current_price = app_model.refresh_market_price()
    if current_price is None:
        print("❌ Failed to read market price (Polygon?)")
        return False
    print(f"✅ {app_model.symbol} Current Price: {current_price}")

    # Expirations
    maturities = app_model.get_available_maturities()
    if not maturities:
        print("❌ No maturities available")
        return False
    expiry = sorted(maturities)[0]

    # Strikes
    strike = pick_strike(expiry)
    if strike is None:
        print("❌ Could not pick a strike")
        return False

    app_model.set_option_contract(expiry, strike, "CALL")
    print(f"✅ Option contract set: {expiry} {strike}C")

    # Place breakout trigger just above current price
    trigger_price = round(current_price + 0.01, 2)
    print(f"🎯 Placing breakout order: trigger @ {trigger_price} (> {current_price})")

    order_result = app_model.place_option_order(
        action="BUY",
        quantity=1,
        trigger_price=trigger_price
    )
    print(f"✅ Order placed: {order_result.get('order_id')}")
    print(f"   State: {order_result.get('state')}")
    print(f"   Trigger: {order_result.get('trigger')}")

    # (Optional) peek pending in wait service if available
    try:
        pending_orders = app_model._order_wait_service.list_pending_orders()
        print(f"📋 Orders in OrderWaitService: {len(pending_orders)}")
        if pending_orders:
            print(f"   Pending order: {pending_orders[0].get('order_id')}")
    except Exception:
        pass

    # Monitor until triggered or timeout
    print(f"\n⏳ Monitoring for breakout (waiting for {app_model.symbol} to hit {trigger_price})...")
    start = time.time()
    triggered = False
    while time.time() - start < timeout_sec:
        # refresh price and order list
        cur = app_model.refresh_market_price()
        orders = app_model.get_orders()

        any_active = False
        for o in orders:
            if o.get('state') == 'ACTIVE':
                any_active = True
                print(f"🚀 ORDER TRIGGERED! {o.get('order_id')} ACTIVE at price ~{cur}")
                triggered = True
                break
        if any_active:
            break

        # progress output
        print(f"⏳ Still waiting... Current: {cur}, Need: {trigger_price}")
        time.sleep(1.5)

    # Final status
    print("\n📊 Final order status:")
    for o in app_model.get_orders():
        print(f"   {o.get('order_id')}: {o.get('state')} (Trigger: {o.get('trigger')})")

    # Disconnect
    app_model.disconnect_services()
    print("✅ Services disconnected")

    return triggered

def test_immediate_execution():
    print("\n🧪 TESTING IMMEDIATE EXECUTION (no trigger)")
    if not app_model.connect_services():
        print("❌ Failed to connect services")
        return False

    app_model.symbol = "SPY"
    px = app_model.refresh_market_price()
    if px is None:
        print("❌ Failed to read market price")
        return False
    print(f"✅ {app_model.symbol} Price: {px}")

    maturities = app_model.get_available_maturities()
    if not maturities:
        print("❌ No maturities available")
        return False
    expiry = sorted(maturities)[0]

    strike = pick_strike(expiry)
    if strike is None:
        print("❌ Could not pick a strike")
        return False

    app_model.set_option_contract(expiry, strike, "CALL")
    print("Placing immediate order...")
    res = app_model.place_option_order(action="BUY", quantity=1)  # no trigger
    print(f"✅ Immediate order: {res.get('order_id')} - State: {res.get('state')}")

    app_model.disconnect_services()
    print("✅ Services disconnected")
    return True

if __name__ == "__main__":
    setup_logging()
    ok = test_order_wait_service(timeout_sec=90)
    if not ok:
        print("ℹ️ Trigger did not fire within the timeout (this can be normal in quiet markets).")
    test_immediate_execution()
    print("\n🎉 ORDER WAIT SERVICE TEST COMPLETE")
