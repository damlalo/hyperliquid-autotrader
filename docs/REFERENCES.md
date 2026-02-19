# References

## Official Hyperliquid docs

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint  
  Context: /info endpoint; candleSnapshot rules; only most recent 5000 candles; supported intervals; other info methods.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint  
  Context: /exchange endpoint used for trading actions; batching/weights; order/modify/cancel actions; signature usage guidance.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket  
  Context: WebSocket endpoint URLs (mainnet/testnet) and general WS usage.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions  
  Context: WebSocket subscription message formats and examples (candles, book, trades, user/order updates, etc.).

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits  
  Context: REST aggregated weight limit per IP (1200/min), exchange request weight formula, and other limits.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets  
  Context: Nonce rules (stored highest nonces, must be unique, time window constraints) and agent wallet nonce tracking.

- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/margining  
  Context: Cross vs isolated margin behavior; default cross; how isolated is constrained.

- https://hyperliquid.gitbook.io/hyperliquid-docs/trading/liquidations  
  Context: Liquidation behavior; cross liquidation price independence from leverage; isolated liquidation depends on leverage.

- https://hyperliquid.gitbook.io/hyperliquid-docs/historical-data  
  Context: Monthly S3 archive bucket; what data is/is not provided (no candles via S3); guidance to record via API.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api  
  Context: Hyperliquid API overview page; links to official/community SDKs.

- https://app.hyperliquid.xyz/API  
  Context: Hyperliquid UI page for creating/authorizing API wallets (agent wallets) with no withdrawal permissions.

- https://github.com/hyperliquid-dex/hyperliquid-python-sdk  
  Context: Official Python SDK referenced by Hyperliquid docs for signing/trading client patterns.

- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/hyperevm/raw-hyperevm-block-data  
  Context: HyperEVM raw block data on S3 (MessagePack + LZ4), for indexers needing chain-level archives.

- https://app.hyperliquid.xyz/  
  Context: Hyperliquid main app landing page (general product entry point).

## Third-party and community references

- https://docs.chainstack.com/reference/hyperliquid-exchange-place-order  
  Context: Third-party reference doc summarizing place-order support (limit/market/trigger), signature authentication.

- https://docs.chainstack.com/reference/hyperliquid-info-liquidatable  
  Context: Third-party reference doc about liquidation risk signal fields (liquidatable true/false) and interpretation.

- https://www.quicknode.com/docs/hyperliquid/info-endpoints/candleSnapshot  
  Context: Third-party explanation of candleSnapshot; notes official HL public API access details.

- https://www.quicknode.com/docs/hyperliquid/info-endpoints/historicalOrders  
  Context: Third-party explanation of historicalOrders endpoint behavior/limits.

- https://hummingbot.org/exchanges/hyperliquid/  
  Context: Practical "how to create API wallet" steps; points to the HL API wallet page.

- https://github.com/nomeida/hyperliquid  
  Context: Community TypeScript SDK; mentions WS subscription limits and subscription management patterns.

- https://docs.rs/hyperliquid/latest/hyperliquid/struct.Info.html  
  Context: Rust SDK documentation for Info client, including candle snapshot argument patterns.

- https://docs.rs/hyperliquid/latest/hyperliquid/struct.Exchange.html  
  Context: Rust SDK documentation for Exchange client; order placement/modify signatures and parameters.

- https://hexdocs.pm/hyperliquid/  
  Context: Elixir SDK documentation index (community SDK example).

- https://github.com/sonirico/go-hyperliquid  
  Context: Community Go SDK capabilities summary (ws/rest/trading ops).

- https://pkg.go.dev/github.com/slicken/go_hyperliquid  
  Context: Another Go SDK listing emphasizing low-latency trading and WS handling.

- https://docs.ccxt.com/exchanges/hyperliquid  
  Context: CCXT exchange page referencing HL docs for candleSnapshot and exchange behavior.

- https://github.com/ccxt/ccxt/issues/23243  
  Context: Discussion confirming HL returns most recent 5000 candles; practical gotchas in client defaults.

- https://www.dwellir.com/blog/hyperliquid-websocket-subscription-limits  
  Context: Third-party discussion about websocket subscription limits and tradeoffs vs REST polling.

- https://onekey.so/blog/ecosystem/beginners-guide-to-hyperliquid/  
  Context: Third-party overview of HL concepts like cross vs isolated margin for general understanding.

- https://docs.privy.io/recipes/hyperliquid/trading-patterns  
  Context: Third-party integration notes summarizing cross vs isolated margin patterns.

- https://github.com/c-i/hyperliquid-historical  
  Context: CLI tooling example for downloading/processing certain HL historical archives (S3-based workflows).

- https://app.artemis.xyz/docs/snowflake-share/tables/hyperliquid  
  Context: Third-party dataset availability (starts Aug 17, 2025) as an alternative data source.

- https://www.dwellir.com/docs/hyperliquid/trade-data  
  Context: Third-party raw archive product listing; indicates availability ranges for trade/fill style datasets.

- https://zerion.io/api/hyperliquid  
  Context: Third-party overview describing Hyperliquid network/HyperEVM in broad terms.
