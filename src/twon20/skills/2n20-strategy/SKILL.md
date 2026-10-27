---
name: 2n20-strategy
description: Build a new strategy or adapt an existing bot or backtest for Hyperliquid and a 2n20 vault. Assess compatibility, implement the integration, and verify it with trading stopped.
---

Help the creator prepare their strategy in the project and machine they authorize. Follow [the integration flow](references/integrate.md). Use the same flow when the creator supplies a request copied from the website.

## Choose the project

Reuse project, machine and strategy decisions from the conversation and the project's non-secret `STRATEGY.md`. A project generated from the starter is an existing project on subsequent visits. Inspect only the selected project's safe source, dependency locks and tests. Do not search unrelated strategies, services or agent configuration.

For missing details, ask one short question at a time. Use your question controls when available, with two or three relevant choices for decisions such as existing project or starter, and this computer or VPS. Allow a custom answer using the built-in option when provided. Ask for paths and SSH destinations as text. If question controls are unavailable, ask the same concise questions in chat. Wait for my answer. A default selection is not consent.

For a new project, use `2n20 init '<new project directory>' --template hyperliquid-python --json`. Init needs an absent destination. Let it create the directory before installing project-local skills. Read the generated README and command help; use the released package version and reviewed runner dependency lock. Do not invent flags or install an unpublished release.

For an existing project, preserve its strategy rules, sizing, research tools and structure. Read its exchange boundary before choosing an integration. A working executor can stay in place. A signal-only project can reuse the starter's operating modules. Another language does not require a rewrite into Python. Explain any missing port or wrapper and verify it before calling the integration complete. Do not initialize a second project to inspect the starter.

## Assess, adapt, verify

1. Record the current inputs, target meaning, execution and recovery behavior in `STRATEGY.md`. Identify assumptions that differ on Hyperliquid or in a shared vault. Link each finding to source or a test.
2. Present a short implementation plan: files to change, modules to keep or reuse, behavior that must stay equivalent, and decisions the creator still needs to make. Implement reversible work covered by the creator's request. Ask before changing strategy semantics, risk choices or live operation; a conversion request does not authorize those changes.
3. Use the reference flow to adapt the data and execution boundaries. Keep strategy logic independently testable. Integrate account verification, withdrawal coordination and durable order reconciliation before claiming compatibility.
4. Run the project's existing checks and focused disposable tests. Compare original and adapted decisions on the same inputs. Inspect test commands before running them; replace real exchange clients with stubs. Use read-only public checks for venue/account evidence. Paper trading is an optional development tool, not an onboarding requirement.
5. Report the exact outcome and next action in `STRATEGY.md`. Separate prepared code, verified connection and live execution. Keep trading stopped when finishing this flow.

The canonical starter is `src/twon20/templates/hyperliquid-python` in the public creator-tools repository, also bundled in the installed package. Read its README, `models.py` and `WITHDRAWALS.md` before reusing modules. In the starter, change `strategy.py` and its `build_strategy(parameters)` factory to select your strategy. The factory validates the strategy's public parameters; configuration checks and runtime use that same factory. Keep one public configuration for checks and execution. Derive precision at the execution boundary from venue metadata. Preserve an existing project's configuration and secret loader.

## Connect when a vault exists

Before a vault exists, prepare code without calling `onboard`, creating a key or requiring funding. When the creator reaches Step 2, use `2n20-setup` and select the existing-project path. Preserve the project, environment and retained setup directory. If the guide is unavailable, export it with `2n20 skill export --name 2n20-setup --directory '<new skill directory>'` and read it.

After creator-wallet approval, follow Step 3 for the configured project's read-only connection check and completion report. Use the verified `tradingAccount` as the Hyperliquid account, never the SDK's native `vault_address`. An unavailable check leaves the connection unverified. A successful connection check proves public access and configuration, not strategy quality or live fills.

## Operating boundaries

Keep live execution disabled until the creator authorizes a separate plan with concrete market, size, slippage, duration and collateral bounds. Setup and strategy adaptation submit no orders or transfers and start no service. Do not restart an existing trader, confirm a wallet transaction or claim that a fill is guaranteed. Preserve journals and unresolved order identities; do not retry an uncertain submission as a new order.

Keep key handling and deployment discovery inside the CLI and existing secret loader. Never read, paste, upload or log private keys, wallet seeds, `.env*`, `config.json`, `notes.txt` or wallet backups. Inspect loader interfaces and public configuration instead. Keep credentials, proprietary source and raw account data out of reports and external uploads unless the creator authorized the specific disclosure.

The guide works in a coding harness with authorized terminal and filesystem tools. An exported copy includes this file and its references. Do not invent a connector, installation directory or execution capability for another client.
