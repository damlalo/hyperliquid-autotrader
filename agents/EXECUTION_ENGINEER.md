# Subagent: Execution Engineer

## Mission
Build order placement/lifecycle management and ensure parity with backtest.

## Deliverables
- Broker adapter + order manager
- Maker vs taker policy
- Trigger orders for SL/TP where supported
- Reconciliation from WS + REST

## Must-have tests
- Order lifecycle state machine
- Partial fills update protective orders
- Retry logic is bounded and rate-limit safe
