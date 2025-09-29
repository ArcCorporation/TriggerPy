# integration.py
import logging
from Services.polygon_service import PolygonService
from Services.tws_service import create_tws_service
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def main():
    print("🚀 Starting Automated Trading System Integration Test")
    
    # Initialize services (same as tws_service.py)
    polygon_service = PolygonService()
    tws_service = create_tws_service()
    wait_service = OrderWaitService(polygon_service, tws_service)
    
    # Connect to TWS (same as tws_service.py)
    if tws_service.connect_and_start():
        print("✅ Connected to TWS Paper Trading")
        
        try:
            # TEST 1: Get option chain data (PROVEN WORKING from tws_service.py)
            print("\n🔍 Getting real option chain data for SPY...")
            maturities = tws_service.get_maturities("SPY")
            
            if maturities:
                print(f"✅ Found {len(maturities['expirations'])} expirations and {len(maturities['strikes'])} strikes")
                
                # Use REAL data from the option chain (not hardcoded)
                real_expiry = list(maturities['expirations'])[0]  # Closest expiration
                real_strike = list(maturities['strikes'])[len(maturities['strikes'])//2]  # Middle strike
                
                print(f"🎯 Using real contract: SPY {real_expiry} {real_strike}CALL")
                
                # TEST 2: Create order with REAL contract data
                print("\n📝 Creating order with real contract details...")
                my_order = Order(
                    symbol="SPY",
                    expiry=real_expiry,
                    strike=real_strike,
                    right="CALL",
                    qty=1,
                    entry_price=0.10,  # Small limit price for paper trading
                    tp_price=0.20,
                    sl_price=0.05,
                    trigger=445.0  # Will trigger immediately since SPY is at ~663
                )
                
                print(f"✅ Created order: {my_order.order_id}")
                print(f"   {my_order.symbol} {my_order.expiry} {my_order.strike}{my_order.right}")
                print(f"   Trigger: {my_order.trigger}, Current SPY: ~663")
                
                # TEST 3: Add to wait service (should trigger immediately)
                print("\n⏳ Adding order to wait service...")
                order_id = wait_service.add_order(my_order)
                
                # The order should trigger immediately since 663 > 445
                # Wait a moment for the trigger to process
                import time
                time.sleep(2)
                
                # Check order status
                print("\n📊 Checking order status...")
                status = wait_service.get_order_status(order_id)
                if status:
                    print(f"✅ Order status: {status.get('state', 'unknown')}")
                else:
                    print("❌ Order status not available")
                
                print("\n🎯 Integration test completed!")
                
            else:
                print("❌ Failed to get option chain data")
                
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            # Clean up (same as tws_service.py)
            tws_service.disconnect_gracefully()
            print("✅ Disconnected from TWS")
            
    else:
        print("❌ Failed to connect to TWS")

if __name__ == "__main__":
    main()