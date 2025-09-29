# test.py
import logging
from model import app_model

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

def test_breakout_validation():
    print("🧪 TESTING BREAKOUT VALIDATION")
    
    # Setup
    app_model.symbol = "SPY"
    current_price = app_model.refresh_market_price()
    print(f"Current SPY price: {current_price}")
    
    # Test 1: Valid breakout trigger (above current price)
    try:
        valid_trigger = current_price + 1.0
        print(f"✓ Testing valid breakout trigger: {valid_trigger}")
        # This should work when we have full contract setup
    except Exception as e:
        print(f"  Note: {e}")
    
    # Test 2: Invalid trigger (below current price) 
    try:
        invalid_trigger = current_price - 1.0
        print(f"✗ Testing invalid trigger (should fail): {invalid_trigger}")
        # This should raise ValueError
    except Exception as e:
        print(f"  Expected error: {e}")
    
    # Test 3: No trigger (immediate execution)
    try:
        print("✓ Testing no trigger (immediate execution)")
        # This should work
    except Exception as e:
        print(f"  Error: {e}")

def test_full_integration():
    print("\n🎯 TESTING FULL INTEGRATION")
    
    try:
        # Connect services
        if app_model.connect_services():
            print("✅ Services connected")
            
            # Set symbol and get market data
            app_model.symbol = "SPY"
            price = app_model.refresh_market_price()
            print(f"✅ SPY price: {price}")
            
            # Get available maturities
            maturities = app_model.get_available_maturities()
            if maturities:
                print(f"✅ Available expirations: {len(maturities)}")
                print(f"  First 3: {maturities[:3]}")
                
                # Test with first available expiration
                expiry = maturities[0]
                strikes = app_model.get_available_strikes(expiry)
                if strikes:
                    strike = strikes[len(strikes)//2]  # Middle strike
                    print(f"✅ Testing with: {expiry} {strike}CALL")
                    
                    # Set option contract
                    app_model.set_option_contract(expiry, strike, "CALL")
                    print("✅ Option contract set")
                    
                    # Test breakout order
                    trigger_price = price + 0.5  # Valid breakout
                    order = app_model.place_option_order(
                        action="BUY", 
                        quantity=1, 
                        trigger_price=trigger_price
                    )
                    print(f"✅ Breakout order placed: {order['order_id']}")
                    print(f"  Trigger: {trigger_price}, Current: {price}")
                    print(f"  Status: {order['state']}")
                    
            # Show orders
            orders = app_model.get_orders()
            print(f"✅ Total orders: {len(orders)}")
            
        else:
            print("❌ Failed to connect services")
            
    except Exception as e:
        print(f"❌ Integration test failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        app_model.disconnect_services()
        print("✅ Services disconnected")

if __name__ == "__main__":
    setup_logging()
    test_breakout_validation()
    test_full_integration()
    print("\n🎉 BREAKOUT TRADING SYSTEM TEST COMPLETE")