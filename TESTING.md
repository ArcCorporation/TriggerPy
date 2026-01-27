# ArcTrigger Testing Checklist - MV 1.0

## Pre-Release Testing

### ✅ Critical Functionality Tests

#### 1. Option Premium Fetching
- [ ] **Test:** Connect to TWS and fetch option premium during market hours
  - Expected: Premium returned from IBKR
  - Verify: Log shows "Uses ONLY IBKR TWS"
  
- [ ] **Test:** Try to fetch premium when TWS disconnected
  - Expected: Returns `None`, no crash
  - Verify: Error logged but app continues
  
- [ ] **Test:** Try to fetch premium in premarket
  - Expected: Returns `None` (no Polygon fallback)
  - Verify: No fake fallback price used

#### 2. Strike Loading & Validation
- [ ] **Test:** Select symbol, maturity, and verify strikes populate
  - Expected: Valid strikes appear in combo box
  - Verify: No empty/invalid strikes shown
  
- [ ] **Test:** Try to place order without selecting strike
  - Expected: Error message "Please select a strike price"
  - Verify: No crash, clear error message
  
- [ ] **Test:** Verify strike filtering works
  - Expected: Only valid numeric strikes > 0 appear
  - Verify: No None, empty, or negative strikes

#### 3. Order Placement Flow
- [ ] **Test:** Place order with all fields filled correctly
  - Expected: Order created and queued
  - Verify: Order appears in watchers/positions
  
- [ ] **Test:** Place order in premarket (if applicable)
  - Expected: Order waits for market open
  - Verify: No premium fetch attempted in premarket

#### 4. Premarket Trigger Rebase
- [ ] **Test:** Create order in premarket, trigger hits
  - Expected: Trigger rebased, strike remains original
  - Verify: Strike not recalculated with 2.5 step
  - Verify: Only trigger price updated

#### 5. Quantity Calculation
- [ ] **Test:** Place order with position size, verify quantity calculated correctly
  - Expected: Quantity = position_size / (premium * 100)
  - Verify: No 4x quantity bug
  - Verify: Uses actual premium from IBKR (not fallback)

### 🔧 Integration Tests

#### TWS Connection
- [ ] Connect to TWS paper trading
- [ ] Verify connection status shows "🟢 CONNECTED"
- [ ] Verify market data farm connections OK

#### Order Execution
- [ ] Place test order (small size, paper trading)
- [ ] Verify order sent to TWS
- [ ] Verify order status updates correctly
- [ ] Verify fill confirmation received

### 🐛 Regression Tests

#### Known Issues Fixed
- [ ] **4x Quantity Bug:** Verify quantity calculated correctly
- [ ] **Strike Loading Error:** Verify no "could not convert string to float" errors
- [ ] **Polygon Fallback:** Verify no Polygon calls for option pricing
- [ ] **2.5 Step:** Verify original strike preserved on rebase

### 📋 UI/UX Tests

- [ ] Version number "MV 1.0" visible in banner
- [ ] All buttons functional
- [ ] Order frames create correctly
- [ ] Status messages clear and informative
- [ ] Error messages user-friendly

### ⚠️ Edge Cases

- [ ] Market closed - verify graceful handling
- [ ] TWS disconnects mid-operation - verify reconnection
- [ ] Invalid symbol entered - verify error handling
- [ ] Missing required fields - verify validation
- [ ] Multiple orders simultaneously - verify no conflicts

---

## Post-Release Monitoring

### Logs to Watch
- `[TWSService] get_option_premium` - Verify IBKR only
- `[WaitService]` - Verify order flow
- `Order failed` - Check for any new errors
- `Quantity recalc error` - Should not appear

### Metrics to Track
- Order success rate
- Premium fetch success rate
- Trigger hit accuracy
- Quantity calculation accuracy

---

## Known Limitations (MV 1.0)

1. **No Polygon Fallback:** If IBKR unavailable, option premium returns `None`
2. **No Premium in Premarket:** Premium only fetched when trigger hits (after market open)
3. **Original Strike Preserved:** Strike not auto-adjusted on premarket rebase

---

## Test Environment

- **TWS Version:** Paper Trading
- **Market Hours:** Test during RTH and premarket
- **Symbols:** Test with liquid options (SPY, QQQ, TSLA, NVDA)

---

**Last Updated:** MV 1.0 Build
**Test Status:** ⚠️ Pending
