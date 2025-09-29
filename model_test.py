import logging
import time
from model import AppModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if __name__ == "__main__":
    model = AppModel()
    model.connect()
    print("=== Automated AppModel Test (TSLA) ===")

    # Step 1: Set symbol
    model.set_symbol("TSLA")
    print(f"Symbol set: TSLA, underlying price={model.underlying_price}")

    # Step 2: Get expiries
    expiries = model.get_maturities("TSLA")
    print(f"Available expiries: {expiries[:5]} ... total={len(expiries)}")

    # Test expiry
    expiry = expiries[0]

    # Step 3: Get option chain for expiry
    chain = model.get_option_chain("TSLA", expiry)
    print(f"Option chain sample ({expiry}): {chain[:3]}")

    # --- FIX: zincirin cache’e oturmasını bekle ---
    retries = 5
    while expiry not in model.option_chains and retries > 0:
        logging.info("Option chain not ready yet, retrying...")
        time.sleep(1)
        chain = model.get_option_chain("TSLA", expiry)
        retries -= 1

    if expiry not in model.option_chains:
        raise RuntimeError(f"Option chain failed to load for {expiry}")

    # Step 4: Pick first strike
    strike = chain[0]["strike"]
    right = chain[0]["right"]

    # Step 5: Set option
    model.set_option(expiry, strike, right)
    print(f"Selected option: {expiry} {strike} {right}")
