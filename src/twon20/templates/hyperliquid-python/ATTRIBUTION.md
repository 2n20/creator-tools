# Sources and dependency review

The educational strategy and finite runtime are authored for 2n20. They call [Hyperliquid's official Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk), without embedding or modifying its source. Each dependency retains its own license, listed in its installed distribution metadata. The creator-tools repository license covers this template's original code.

The reviewed execution dependency is [hyperliquid-python-sdk 0.24.0](https://pypi.org/project/hyperliquid-python-sdk/0.24.0/), published 2026-06-04. The selected wheel APIs were inspected directly, including `Info`, `Exchange.order`, `query_order_by_cloid`, `user_fills_by_time`, `set_expires_after`, `sign_l1_action` and `API.post`. The complete third-party runtime graph and registry artifact hashes are recorded in `requirements-runner.txt` and `dependency-review.json`. Every selected registry artifact was at least 14 days old on 2026-10-03. SDK dependencies are separate from the setup CLI's base dependency graph.

Primary protocol references reviewed on 2026-10-03:

- [Exchange endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint): limit IOC orders, reduce-only actions, client order IDs and same-account `agentSendAsset` collateral transfers.
- [Info endpoint](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint): public account, book, fee, order and fill evidence.
- [Tick and lot size](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size): supported price/size precision.
- [Error responses](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/error-responses): per-order validation errors and minimum order notional.
- [Nonces and API wallets](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets): use a dedicated API key per process. A local filesystem lock does not coordinate other hosts.

Documentation can change. This template validates the pinned SDK identity and refuses unsupported public evidence. Passing mocked tests or a public connection check does not demonstrate successful live trading, profitability or support for an arbitrary bot.
