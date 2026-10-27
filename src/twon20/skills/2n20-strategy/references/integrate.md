# Integrate a strategy

Finish with the selected project prepared for its next step and trading stopped. Use the creator's existing tests and execution code where they fit. The bundled Python starter supplies operating modules when they are missing.

## 1. Establish the current behavior

Inspect safe source and tests in the authorized project. Record a compact assessment in its non-secret `STRATEGY.md`:

| Area | Establish before editing |
| --- | --- |
| Project | Language, entry point, dependency lock, test command, machine and project path |
| Data | Exchange, market identifiers, instrument type, candle/tick timing, warmup, gaps and stale-data behavior |
| Decisions | Signal function, checkpoint state, side, units, target exposure versus incremental order |
| Sizing | Equity pool, leverage assumption, caps, minimum sizes, rounding and protective exits |
| Execution | API client, account selection, order types, cancellation, partial fills and retry behavior |
| Recovery | Durable order IDs, positions, restart handling, concurrent traders and retained state |
| Vault | Existing setup directory, verified public account binding, pending withdrawals and capital available after settlement |

For missing facts, name the file or decision needed. Reuse prior answers. Do not inspect credentials to discover an account. Avoid running an existing entry point until its effects are understood; importing a bot or running its tests can start a trader.

Classify the integration as **direct adaptation**, **adaptation with gaps**, or **unsupported by the current runner**. Explain the evidence and the next implementable change. Do not convert “unsupported by the runner” into “impossible on Hyperliquid.”

The current starter supports one native USDC perpetual in standard account mode. A multi-market portfolio, spot strategy, HIP-3 market, non-candle signal, another collateral asset or unified/portfolio-margin account needs a separate compatibility assessment. Do not discard instruments, change account modes or substitute markets to fit the example. A symbol match alone does not establish equivalent contracts, settlement currency, price data or funding behavior.

## 2. Choose the smallest integration

| Existing project | Integration path |
| --- | --- |
| New strategy | Generate the starter once; explain its strategy entry point and public settings. Keep the example optional. |
| Signals or a backtest, with no operating loop | Keep the signal and research code. Add an adapter to the starter's target interface and reuse its feed, execution and recovery where compatible. |
| Hyperliquid bot with working execution | Keep its loop and executor. Add verified vault account selection, withdrawal coordination and checks at its order boundary. |
| Bot for another exchange | Isolate the exchange adapter. Map observations and orders to Hyperliquid, preserving signal and sizing behavior. Explain differences that need creator decisions. |
| Another language or runtime | Keep it. Use the CLI for setup and public account verification; implement the matching account, order and withdrawal checks in the existing runtime. The Python modules are references, not an installed cross-language SDK. |

Present the plan before editing: changed files, reused modules, expected behavior and unresolved decisions. Continue authorized reversible work. Ask only when a choice changes strategy semantics, capital allocation, supported markets or live operation. Do not require approval for each routine adapter edit.

A creator may choose to retain their own executor. That executor must retain its own order ownership, ambiguous-response recovery, precision, margin and risk checks. A CLI approval or a small equity deduction does not supply those controls.

## 3. Adapt the boundaries

Read the starter README and actual module contracts. Preserve the original logic behind a small adapter instead of scattering exchange calls through the signal code.

| Module | Responsibility |
| --- | --- |
| `models.py` | `Bar`, `BarBatch`, `Target` and Feed/Strategy/Executor/Reporter contracts |
| `strategy.py` | `build_strategy(parameters)`, strategy decisions and public checkpoint state |
| `feed.py` | Validated observations, warmup, continuity and recovery |
| `runtime.py` | Composition, checkpoints, remaining-capital sizing and withdrawal holds |
| `executor.py` | Order planning, bounded chase, cancellation and fill reconciliation |
| `runtime_adapter.py` | Verified account and venue checks before signed effects |
| `runtime_journal.py`, `journal.py` | Durable order ownership and recovery state |
| `reporting.py` | Decisions and execution outcomes without secrets |

In the starter, `strategy.py` owns the `build_strategy(parameters)` factory. Return the creator's strategy from that factory and validate its public parameters there. Configuration checks and runtime use the same factory. Keep construction free of network requests, credentials and trading effects. Preserve shared execution-bound validation when adapting strategy parameters.

The strategy protocol exposes `warmup_bars` and `evaluate(bars, checkpoint) -> (target, checkpoint)`. Inputs are validated bars and the prior public strategy checkpoint. Return a `Target` with a Decimal fraction from `-1` to `1` and a short public reason code. The fraction is desired total exposure relative to the configured capital budget. `0` requests flat exposure and can cause closing trades. `None` keeps the existing intent; it does not stop execution or cancel an outstanding child. Follow the runtime's stop/recovery path for shutdown.

The stock runtime calculates signed notional as `fraction * min(targetNotional, capitalMultiple * remainingCapital)`. A target of `1` is neither one coin nor an extra order. Preserve an existing strategy's sizing by mapping its desired exposure into that budget or retaining its sizing/executor boundary. Explain any intentional difference. Exchange leverage and available margin remain separate constraints.

Map instrument identifiers, direction, quantity units, tick/lot rules, reduce-only behavior, time-in-force, order IDs and fill semantics. Use current official API documentation and the reviewed installed SDK for a new exchange adapter. Do not treat an accepted order as a fill or a canceled order as proof of zero fills. Keep unknown outcomes unresolved until exact identity, order state, fills and current position agree.

Use the verified `tradingAccount` as `account_address` with the approved agent key as signer. Preserve the existing local credential loader and keys. Add a read-only connection path if the project lacks one. It must verify deployment/account/key scope and public venue reads without loading a signer for an order, sending a transfer or invoking the trading entry point.

## 4. Coordinate withdrawals

Read the starter's `WITHDRAWALS.md`. Its runtime keeps evaluating signals during a withdrawal, holds new risk, cancels only owned entry orders and preserves reduce-only protection. After settlement it reconciles actual reductions and sizes against remaining capital. An unchanged signal must not rebuild the withdrawn exposure.

For another executor, `twon20.withdrawals.poll_withdrawal_policy(scope, rpc)` provides `ready_to_increase`, `reason` and `capital.remaining_usdc` from a verified scope. Recheck fresh policy before submission. Hold new risk on pending demand, unknown evidence or an account operation. Preserve protective reductions and reconcile service reductions apart from the bot's own fills. This is cooperative coordination; a client that bypasses it can delay withdrawals.

For a project that already implements admission and recovery, `pending_withdrawal_usdc(snapshot)` and `poll_pending_withdrawal_usdc(scope, rpc)` return a Decimal USDC deduction or `None` for unknown evidence. The deduction is gross unsettled withdrawal value, including cash-covered requests and excluding settled reserves. Use it only when the same equity pool funds the full withdrawal and it has not already been deducted. Vault `totalAssets` excludes settled reserves. `crossMarginSummary.accountValue` includes P&L and is not available margin. Mixed pools need whole-vault adjusted capital and separate collateral checks. The amount helper alone does not make an executor withdrawal-compatible.

## 5. Verify without money

Use existing tests and small disposable fixtures. Before running commands, inspect their entry points and replace exchange writes and secret loaders with stubs. Do not import production configuration or submit orders to validate code.

Verify the affected behaviors:

- Original and adapted signals and sizing agree on representative identical inputs, including no-signal cases. Record changed data timing or units.
- Warmup, stale/missing data and repaired history cannot replay historical orders.
- Partial fills, cancellations, uncertain responses and restarts retain exact order ownership without duplicate exposure.
- Pending withdrawals and unavailable valuation inhibit new risk. A constant target uses remaining capital after settlement, including across a restart.
- Preview, configuration and connection commands cannot invoke order submission, collateral transfers or service startup.

Skip irrelevant fixtures and state why. Preserve a failing equivalence case as a blocker until the creator chooses the changed behavior. Do not create a backtester or paper engine just to finish onboarding. Simulation cannot establish real fills, live execution quality or profitability.

## 6. Finish at the supported stage

Update `STRATEGY.md` with the changed files, preserved rules, public configuration path, exact safe check commands/results, unsupported features and next action. Preserve useful existing content. Keep credentials and raw proprietary strategy source out of the report.

| Stage | Evidence needed | Next step |
| --- | --- | --- |
| Prepared | Integration code and relevant offline checks pass; gaps are explicit | Create a vault or continue its Step 2 with this existing project |
| Connected | Retained approval, verified account binding and the configured project's read-only connection check pass | Follow Step 3's completion report, then leave trading stopped |
| Live | Separate bounded authorization and observed execution results | Report submitted orders, actual fills and remaining exposure separately |

If no vault exists, stop at prepared. If public evidence is unavailable, keep connection unverified and retain the same setup directory. If the creator has supplied Step 3, follow `2n20-setup` and call `complete-setup --connection-checked` only after the project's check passes. A missing executor can coexist with a verified connection; state the execution gap instead of claiming live readiness. Do not start or restart trading to mark setup complete.
