# Build or connect your strategy

Use your coding agent to prepare a new strategy or adapt an existing project for Hyperliquid and a 2n20 vault. You keep control of the strategy and when it starts trading. The setup flow ends after configuration and a read-only connection check.

## Bring an existing strategy

Open the project in your coding agent. Install the released CLI using the [main README](../README.md#install), then install its guides from that project:

```sh
2n20 skill install --agent codex
```

Use `cursor`, `claude` or `hermes` for those clients. For another harness, export `2n20-strategy` with `2n20 skill export --name 2n20-strategy --directory ./strategy-guide` and give the harness that complete directory. It needs your authorized filesystem and terminal access. You can also read the [portable guide](../src/twon20/skills/2n20-strategy/SKILL.md) yourself.

Copy this request into your coding agent:

> Use 2n20-strategy to adapt this project for Hyperliquid and a 2n20 vault. Inspect this project's safe source and tests. Assess its markets, data, signals, sizing, execution and recovery; tell me which parts can stay and which need adapting. Preserve my strategy rules, risk choices and project structure. Reuse suitable starter modules where operating code is missing. Explain the implementation plan, then make the changes covered by this request. Ask me about choices that change trading behavior or risk. Verify with existing tests, disposable fixtures and read-only checks. Record the result and next step in STRATEGY.md. Keep live trading stopped. Do not read credentials, move funds or start services.

Your agent first identifies what you have. A backtest needs operating code around its signals. A working Hyperliquid bot may only need vault account verification and withdrawal coordination. A bot for another exchange needs a reviewed exchange adapter, including differences in contract units, market data and order behavior. The agent asks you to resolve changes to strategy semantics or risk instead of choosing them for you.

The included runner supports one native USDC perpetual in standard account mode. Multi-market portfolios, spot, HIP-3, other collateral and other account modes need a separate assessment. Your agent should report a gap when the current modules cannot represent your strategy. It should preserve an existing language and framework where practical; the Python starter is a reference for other runtimes, not a cross-language SDK.

## Start a new strategy

Choose a new project directory and run:

```sh
2n20 init my-strategy --template hyperliquid-python --json
```

Follow the generated installation commands and README. Init creates source files and a version manifest. It installs no dependencies, creates no key and starts no trader. Give your agent this request from the generated project:

> Use 2n20-strategy to help me build this strategy in the starter. Show me where my decision logic and public settings belong. Ask for missing strategy requirements, then implement and check them with disposable data. Reuse the starter's execution and withdrawal modules. Leave trading stopped and explain the next step in STRATEGY.md.

You can build and test decisions before creating a vault. When you reach the vault's Step 2, select this existing project. Keep the same environment and setup directory through Step 3.

## Integrate manually

Follow the same sequence without an agent:

1. Map your data, target units, sizing, order behavior and restart state. Keep representative inputs and expected decisions from the original strategy.
2. Choose the operating code to reuse. Keep a working executor; use the starter if your project only produces signals. Read the [module map](../src/twon20/templates/hyperliquid-python/README.md) and [agent integration reference](../src/twon20/skills/2n20-strategy/references/integrate.md).
3. Adapt account selection and exchange calls. Use the CLI-verified trading account with the approved key as signer. Keep credentials inside your local loader.
4. Add [withdrawal coordination](../src/twon20/templates/hyperliquid-python/WITHDRAWALS.md) at the execution boundary, including holds on new risk and reconciliation after settlement.
5. Verify equivalent decisions and affected execution/recovery behavior with stubs or disposable fixtures. Add a read-only connection command if your project lacks one.
6. Follow the vault's setup instructions using this project. Report connection success through Step 3 and keep trading stopped.

An equity deduction helper alone does not provide order ownership, cancellation, margin checks or restart recovery. Keep those controls in your executor and test their interaction with withdrawals. The starter README documents its supported commands and limits.

## The strategy interface

In the starter, the strategy consumes validated bars and its previous public checkpoint. It returns a target and its next checkpoint. The operating modules handle account access, order execution and withdrawal coordination.

| You work on | The starter supplies |
| --- | --- |
| Signal rules, checkpoint and `build_strategy(parameters)` factory in `strategy.py` | Candle continuity and recovery in `feed.py` |
| Public market, interval and risk settings | Capital-based sizing and lifecycle in `runtime.py` |
| Mapping existing decisions to a target | Order planning and reconciliation in `executor.py` |
| Tests that preserve your intended decisions | Verified submission, durable journals and public reports |

`models.Strategy` defines `warmup_bars` and `evaluate(bars, checkpoint)`. Return a `models.Target` with a Decimal fraction from `-1` to `1`: positive for long, negative for short and zero for a flat target. Intermediate fractions scale the configured capital budget. `None` retains the current intent. Neither `None` nor zero is a command to stop the runtime; zero can trigger trades to close exposure.

The stock sizing rule is:

```text
desired notional = fraction × min(notional ceiling, capital multiple × remaining capital)
```

The target describes the total desired position. It is not an additional order. Available margin, exchange leverage and execution limits remain separate checks. If your strategy uses another sizing rule, preserve it through an explicit adapter or your existing executor. The factory validates your strategy's public parameters, and configuration checks and runtime use the same factory. Read the generated README for the entry point before editing.

The example strategy helps explain the interface. Setup never starts it. Starting live execution requires a separate plan and explicit authorization, even when the key has approval.

## Completion and live trading

Your agent records progress in a local, non-secret `STRATEGY.md`:

| Result | What you can expect |
| --- | --- |
| Prepared | Code and relevant offline checks pass. You can proceed to vault setup; listed compatibility gaps remain explicit. |
| Connected | Your configured project passes its read-only connection check and the website acknowledges Step 3. Trading remains stopped. |
| Live | You separately authorized a bounded run. Reports distinguish accepted orders, actual fills and remaining positions. |

Connection success does not prove live execution quality or profitability. A project can connect before it has a complete executor; the agent should state that remaining work. When you choose to trade, review the market, size, slippage, duration, collateral and behavior on expiry. Keep retained order journals through interruptions. An uncertain response must be reconciled before another submission.
