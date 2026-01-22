# ArcTrigger Changelog

## MoneyVersion 1.0 (MV 1.0) - Current Build

### Major Changes

#### 1. Polygon Service Removal for Options Pricing
- **Removed:** All Polygon fallback logic for option premium fetching
- **Changed:** `get_option_premium()` now uses ONLY IBKR TWS
- **Impact:** Option premiums are fetched exclusively from Interactive Brokers
- **Files Modified:**
  - `Services/tws_service.py` - Removed Polygon fallback calls
  - Removed `polygon_service` import from `tws_service.py`

#### 2. Strike Loading Validation & Filtering
- **Added:** Validation to check if strike is selected before order placement
- **Added:** Filtering of invalid strikes (None, non-numeric, <= 0) when populating strike combo box
- **Impact:** Prevents "could not convert string to float" errors
- **Files Modified:**
  - `view.py` - Added validation in `place_order()` and filtering in `_populate_strike_combo()`

#### 3. 2.5 Step Calculation Removal
- **Removed:** Hardcoded 2.5 step calculation for strike recalculation
- **Changed:** Premarket trigger rebasing now keeps original strike instead of recalculating
- **Impact:** Original strike is preserved when rebasing premarket triggers
- **Files Modified:**
  - `view.py` - Removed "Use 2.5-step" checkbox
  - `Services/order_wait_service.py` - Removed step calculation in `_handle_premarket_trigger()`
  - `model.py` - Removed step calculation in rebase logic

### Technical Details

#### Option Premium Fetching
- **Before:** Tried IBKR first, fell back to Polygon if unavailable
- **After:** IBKR only - returns `None` if TWS unavailable or market closed
- **Rationale:** Eliminates inconsistent pricing from multiple sources

#### Strike Validation
- Validates strike selection before converting to float
- Filters invalid strikes from option chain data
- Provides clear error message if no strike selected

#### Strike Preservation
- When premarket trigger hits, only trigger price is updated
- Original strike remains unchanged
- No automatic strike recalculation based on trigger price

---

## Previous Versions

### Version 0.x (Pre-MV 1.0)
- Initial development
- Polygon fallback for option pricing
- 2.5 step strike calculation
- Basic strike loading without validation
