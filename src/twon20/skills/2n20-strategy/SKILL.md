---
name: 2n20-strategy
description: Adapt an existing backtest or bot into a modular Hyperliquid strategy for 2n20, check its configuration, and prepare its later vault connection.
---

Work in the creator's authorized project and machine. Preserve their strategy logic, parameters and risk choices. Inspect safe source and tests first; identify the signal, sizing, portfolio state, data assumptions and execution interfaces. Do not replace an existing strategy with a demonstration or imply profitable results. Ask only for missing non-secret decisions that affect implementation.

For missing details, ask one short question at a time. Use your question controls when available, with two or three relevant choices for decisions such as existing project or starter, and this computer or VPS. Allow a custom answer using the built-in option when provided. Ask for paths and SSH destinations as text. If question controls are unavailable, ask the same concise questions in chat. Wait for my answer. A default selection is not consent.

For a new project, use the official `2n20 init '<new project directory>' --template hyperliquid-python --json` command after the creator chooses the project location. Init requires an absent destination. Let init create it before installing project-local agent skills; read this guide directly until then. Read the generated starter README for exact supported commands and dependency installation instructions. Do not invent runtime flags or install an unpublished release.

For missing operating code, inspect and reuse or adapt the bundled starter's candle feed and limit chaser through their documented interfaces. Preserve an existing feed or executor. Read the reviewed starter source from the installed package or official public repository; do not initialize a second project just to adapt this one.

Adapt incrementally around these modules, retaining existing equivalents where appropriate:

- `models.py`: explicit market observations, strategy decisions and bounded order intentions.
- `feed.py`: Hyperliquid public data, timestamps and validity checks; keep stale or missing data from producing orders.
- The strategy module: the creator's existing signal and sizing logic, independently testable with recorded or synthetic inputs.
- `runtime.py`: feed/strategy/executor coordination and bounded lifecycle.
- `executor.py`: separate paper and live execution, account binding, limits and recovery; strategy code must not directly send orders.
- `reporting.py`: public decisions and outcomes, with no secrets or misleading execution claims.

Compare adapted signals and sizing with the original backtest or bot on the same representative inputs. Explain intentional differences such as fill assumptions, fees, latency and data timing. Paper results do not establish live execution or profitability. Keep changes scoped and reviewable. Follow the starter README for actual paper, read-only check and runtime commands.

For the starter, keep the selected market, interval, strategy parameters, limits and reporting preferences in one public configuration. Pass that same file to its connection check and paper/live runtime. Ask only for missing choices, write the agreed public settings, and label example limits as examples. Precision comes from Hyperliquid metadata at the execution boundary; do not configure decimals. Preserve an existing project's equivalent source of configuration and secret loader. A replacement feed must satisfy the Bar/BarBatch contract, including the configured market, interval and continuity rules; other sampling methods need an explicit adaptation of that contract.

For a custom strategy that only needs an equity deduction, the unreleased `0.6.1.dev0` SDK adds `pending_withdrawal_usdc(snapshot)` and `poll_pending_withdrawal_usdc(scope, rpc)`. Preserve its strategy and executor; do not require adopting the starter engine. Read `WITHDRAWALS.md` and the pure `withdrawal_sizing.py` example. The Decimal amount is gross unsettled NAV value, including cash-covered requests, excluding settled reserves. Unknown evidence is `None`, never zero. Subtract it only when the same equity pool funds the full withdrawal and it has not already been deducted. `crossMarginSummary.accountValue` includes P&L and is not available margin. For mixed pools prefer whole-vault adjusted capital and separate collateral limits. A desired total target is not an additional order or trade permission. Preserve existing risk admission, reconciliation and protective orders. The helper is absent from published `0.6.0`; do not present the published install pin as providing it.

Integrate withdrawal coordination at the execution boundary. Read the starter's `WITHDRAWALS.md`. Supported runner strategies return signals; the runner handles pending-demand holds, remaining-capital sizing and bounded reconciliation. Choose an explicit `capitalMultiple` and hard `targetNotional` ceiling, preserving the creator's risk choice. For another executor choosing the full conservative coordination policy, use `twon20.withdrawals.poll_withdrawal_policy` with verified scope, recheck before submission, withhold new risk on pending or unknown evidence, preserve reduce-only protection and reconcile actual reductions before resuming. Do not subtract settled reserves twice from vault totalAssets. This is cooperative coordination, not a guarantee against bypassing clients. Test a constant-exposure strategy through a partial withdrawal and restart; it must not rebuild exposure using withdrawal collateral. Signal processing continues during the hold, but risk-increasing orders wait.

Keep a non-secret `STRATEGY.md` with the machine and project path, strategy rules, agreed limits, exact check commands, results and remaining work. Preserve existing content and distinguish paper checks from live execution.

Before a vault exists, develop without live orders. The starter fixture simulation is an optional development utility; use the creator's existing tests where available and do not build a paper engine as a setup requirement. Do not run `2n20 onboard`, generate a trading key or require funding just to build a strategy. When the creator later reaches vault Step 2, choose the existing-project path and retain this project. Use the bundled `2n20-setup` guidance for resumable onboarding, creator-wallet approval, verified account binding and completion reporting. If that guidance is unavailable, export it with `2n20 skill export --name 2n20-setup --directory '<new skill directory>'` and read it before setup.

Resume the same setup directory and retained key after interruption. Never make a replacement key to bypass conflicts or outages. Use the verified `tradingAccount` as the Hyperliquid account; it is not an SDK native `vault_address`. Keep discovery and key handling in the CLI and existing local secret loader.

Never read, paste, upload or log private keys, wallet seeds, `.env*`, `config.json`, `notes.txt` or wallet backups. Work from loader interfaces and public configuration. Do not inspect unrelated strategies, services or agent configuration.

Keep live execution disabled until separately and explicitly authorized with concrete market, size, slippage, duration and collateral bounds. Setup approval does not authorize orders, transfers, service restarts or automatic wallet confirmation. Preserve recovery state and stop when the execution result is uncertain; do not retry beyond the authorization.

This is portable guidance. Other clients, including ChatGPT or Grok, can read an exported copy; running commands requires their own authorized local tools. Do not invent an installation directory, connector or execution capability for those clients.
