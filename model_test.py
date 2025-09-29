import logging
from model import AppModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

print("=== Automated AppModel Test (TSLA) ===")

model = AppModel()

# 1. Sembol ayarla
model.set_symbol("TSLA")
print(f"Symbol set: TSLA")
print(f"Available expiries: {model.expiries[:5]} ... total={len(model.expiries)}")

# 2. İlk expiry ve strike seç
if model.expiries:
    expiry = model.expiries[0]
    sample_chain = model.chain_cache.get(expiry, [])
    if sample_chain:
        strike = sample_chain[0]["strike"]
        right = sample_chain[0]["right"]

        print(f"Testing option: expiry={expiry}, strike={strike}, right={right}")
        model.set_option(expiry, strike, right)

        # 3. Bracket order simülasyonu
        try:
            result = model.place_bracket_order(price=5.0, take_profit=7.0, stop_loss=4.0)
            print("Bracket order placed:", result)
        except Exception as e:
            print("Order placement failed:", e)
    else:
        print(f"No option chain data for expiry {expiry}")
else:
    print("No expiries available.")
