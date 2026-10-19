# 2n20 creator tools

Connect your strategy software to a [2n20](https://2n20.org) vault on Hyperliquid. Use your existing project or start with the included Python example.

Give your coding agent the `2n20-setup` skill, or run the `2n20` CLI yourself. Run setup on the computer or server that will operate your strategy. You receive an approval link to review with your creator wallet. Your private trading key stays on the strategy machine.

After approval, resume the same setup directory to verify trading access and get your strategy settings. You define and operate the strategy. The starter includes Hyperliquid candles, an educational band-breakout strategy, a replaceable limit chaser and execution reporting. It makes no profitability claim. The Python import is `twon20`.

The published 0.5.2 package supports the existing deployed generation. This checkout prepares trading-key version 4 and requires a matching future deployment; its source is not the published package. Build local artifacts for verification only.

## Install

Use Python 3.10 or later on your strategy computer or server:

```sh
python3 -m venv .venv-2n20
source .venv-2n20/bin/activate
python -m pip --isolated install '2n20==0.5.2'
2n20 --version
```

You install the Hyperliquid SDK with the starter's separate runner dependency lock. The base CLI needs no execution SDK. See the [release instructions](https://github.com/2n20/creator-tools/blob/main/docs/releases.md) to build from source or check release artifacts.

## Start or connect a project

Generate a new project in a directory that does not exist:

```sh
2n20 init my-strategy --template hyperliquid-python
```

Follow the installation and connection commands that `init` prints. It copies the bundled template and records its version. It does not install dependencies, create keys or start trading. Inspect the [template source](https://github.com/2n20/creator-tools/tree/main/src/twon20/templates/hyperliquid-python) before using it. Review future template changes before updating your generated project.

For an existing strategy, continue in its authorized project directory. Never search unrelated projects or read their credentials to discover access.

```sh
2n20 onboard --vault '<official vault page URL>' --json
```

Save the returned `resumeCommand` and `approvalUrl`. If you run setup on a VPS, open the link on your wallet computer. Review the prepared request and approve with your creator wallet. Opening the link does not authorize a transaction.

Then run the exact resume command with the same setup directory. Read the CLI's separate results for contract approval, HyperCore activation and funding. An unavailable check leaves access unverified.

Resume with the existing key after an interruption. For expired consent or a changed nonce, follow the CLI's same-key renewal instruction. Resolve a conflicting requested or approved key before continuing. Do not generate another key to bypass a failed check.

The CLI automatically retries temporary RPC and approval-link failures within a bounded budget, preserving the same key and public consent. If the handoff service remains unavailable, import the exact public consent file printed by the CLI into the vault's Step 2. Resume the same directory. Renewal creates a new public file and retains the earlier files and private key.

## Use your coding agent

For a new starter, run `2n20 init` first, then enter the generated project. From that project or your existing strategy directory, install both guides for your coding agent:

```sh
2n20 skill install --agent codex
```

Use `cursor`, `claude` or `hermes` instead of `codex` for those agents. Codex installs into the project's `.agents/skills`, Cursor into `.cursor/skills`, and Claude Code into `.claude/skills`. Hermes uses `~/.hermes/skills` or the configured `HERMES_HOME`. Reload the agent's skills after installation. Identical copies stay unchanged; conflicting local copies are preserved for your review.

Use `2n20-strategy` to adapt an existing backtest or bot, or develop the starter before a vault exists. The guide keeps your signals, sizing and research tools, and helps add only the operating modules you need. For example:

> Use 2n20-strategy to adapt this project's backtested strategy for Hyperliquid and a future 2n20 vault. Keep its signals and sizing. Use the starter feed and limit chaser where operating code is missing. Preserve my tests and help configure the project.

Use `2n20-setup` when you have a vault. Give your agent the vault URL, project location and machine. It uses the CLI through authorized terminal access; you open the approval link and use your wallet. A project prepared before vault creation is the same existing project in Step 2.

Copy Step 2 into your coding agent to prepare the approval link. Approve with your wallet on the website and wait for Step 3 to open. Then copy Step 3's connection instructions into the same agent conversation to finish setup. The two requests select the setup skill's corresponding guides. The guides retain native multiple-choice questions, prior-answer reuse and continuation with the same key. Step 2 pauses for website approval and the copied Step 3 request. Setup requires configuration and a read-only connection check; paper trading and live orders are not prerequisites. Your agent can read the released files in the current conversation if skill discovery is unavailable. Upgrading the CLI does not replace an installed skill; preserve differing local copies and use the release linked in the request or export it to a new comparison directory.

For another client, export a portable guide:

```sh
2n20 skill export --name 2n20-strategy --directory ./strategy-guide
2n20 skill export --name 2n20-setup --directory ./setup-guide
```

Give the complete exported directory to the client, including `SKILL.md` and any `references` files. Execution requires its own authorized terminal and filesystem tools. No MCP server is required for the local coding-agent flow. See [Agent skills](https://2n20.org/docs/agents) for installation options.

After configuring your project and passing its read-only connection check, your agent reports success with the retained key. The website completes Step 3 automatically. To report the checks yourself:

```sh
2n20 complete-setup --vault '<official vault page URL>' --directory '<retained setup directory>' --connection-checked --json
```

Report completion only after configuration and a successful connection check. This sends a signed public setup report, never your private key or project files. It submits no blockchain transaction and does not enable live trading. Reporting can be retried from the same directory without replacing the key.

## Keep setup and execution separate

Use `setup`, `onboard`, `renew`, `config`, `status`, `check-consent`, `init`, `skill export` and `skill install` without submitting trades or collateral transfers. Keep `strategy.key` private on the strategy machine. Public configuration names its path without embedding the key. Agents must never inspect, print, upload or commit private-key contents. The CLI sets ignore rules and restrictive file permissions before creating a key.

The optional developer simulation needs no signing key. Its results are simulated fills and P&L. For live execution, the starter uses the official Hyperliquid SDK with the approved trading key as signer and the verified trading account as `account_address`. It does not use the SDK's legacy `vault_address`.

Before a live test, choose the market, side, maximum notional, slippage and time limit. Authorize that plan as a separate action; key approval does not authorize orders. Check usable perpetual collateral, since spot USDC is a separate balance. The starter refuses insufficient margin, unavailable approval, conflicting account state and unsupported account modes. Read the [starter README](https://github.com/2n20/creator-tools/blob/main/src/twon20/templates/hyperliquid-python/README.md) before considering a live plan. You decide whether to install and start a service.

`2n20 status` reports the withdrawal release and whether the trading account is priced. The release fields are `active`, `noticeAt`, `roundSeconds`, `openedThisRound`, `misses`, `lastMissAt` and `cutsAllowedAt`. `noticeAt` is the earliest time a round may cancel orders or reduce positions for the current shortfall. `roundSeconds` is 600. `openedThisRound` is true when a report in the open round showed the strategy adding risk. `misses` counts missed rounds. The count is information, and the key stays approved. `cutsAllowedAt` is the earliest time a later round may reduce positions. Zero, or a time already passed, means the next round may reduce positions. For an unpriced account, status names each reason and whether that reason waits for data, gets a venue repair during a release, or needs the strategy. Before the first valuation report, `pricing.action` is `unreported`. The report includes the key's validUntil and warns when expiry is inside 14 days. During a release the strategy keeps its key and needs no new approval, and the owner can approve a key at most once every 10 minutes. The key stays approved through an ordinary withdrawal and while the account is unpriced. After expiry, approve a fresh key. A key that was already used cannot be approved again. The vault keeps operating after a key expires, and trading stays stopped until the new key is approved. Missing evidence leaves access unverified. Status does not establish that the strategy can trade, and it does not mean every HyperCore state can be repaired.

While an authorized live session is running and the vault is restricted, the starter stops opening new risk, cancels its open entry order and leaves its reduce-only orders in place, including take-profit and stop-loss orders marked reduce-only. New risk also waits while the starter's release read fails or goes stale. The vault moves free collateral itself. A strategy you write yourself should do the same.

The default withdrawal policy follows Hyperliquid Legacy Vaults: pay from vault cash and free spot USDC first. Native perp margin is used only when the full remaining payment fits while retaining margin for positions at their set leverages. Otherwise the service cancels margin-consuming non-reduce-only orders, smallest estimated margin first, then reduces native positions by 20% per wave until enough collateral is available. Cancellations, reductions and transfers are separate operations with fresh complete observations between them. HIP-3 collateral is a final fallback. The optional owner setting Always proportional instead reduces each observed holding by the withdrawing shares’ fraction before settlement. It is off by default and cannot change while requests are pending.

The service preserves protective reduce-only orders. These are real Hyperliquid take-profit/stop-loss orders, not a separate kind of off-exchange protection. The contract bounds sizes, prices, operation phases and permissions; it relies on the operator for order classification and the completed proportional target vector. Reductions stay within the existing 0.5% price band and exchange lot/minimum rules. A thin book or below-minimum remainder can delay a withdrawal. A full protective order book causes no repeated doomed submissions: three distinct fresh blocked observations spanning 20 minutes escalate to an explicit creator-action state, with five-minute rechecks. This bounds silent retries, not payout time.

A release starts after a two-minute notice. Native cuts are capped at 20% of the current position, rounded down, in default mode; each market can be cut at most once per 10-minute round. Proportional mode instead uses the pending request’s share fraction. Receipt-confirmed failures retain persistent backoff. After two missed rounds later reductions wait, with further misses doubling the wait up to one day; independently safe reconciliation and eligible captures continue. The optional cash target and pending liabilities remain excluded from new allocations. The creator key stays approved. A creator client can still reopen exposure before a delayed CoreWriter capture, so neither this policy nor the Python signal guarantees an exit against a bypassing strategy.

Use standard account mode. Do not deposit into a Hyperliquid vault, stake HYPE, open a sub-account, borrow, lend, or hold builder-deployed market collateral without a USDC price. Those states leave the account unpriced, as do a missing collateral price and an unfinished account read. Missing prices and an unfinished read clear when the data returns. During a release, or while no trading key is active, the vault restores standard account mode and withdraws Hyperliquid vault equity after its lockup. The strategy undoes every other unpriced state. For voluntary wind-down, stop new strategy actions, clear outstanding orders and TWAPs, and undo those unsupported states while the key still works. Remove the trading key in the application, wait for confirmed removal, then obtain a fresh full account observation. Submitting removal is not confirmation, and removal does not cancel orders or TWAPs already accepted. The vault refuses irreversible wind-down while authorization is live or pending, orders or TWAPs remain, the report is stale or incomplete, or a strategy-only repair remains. If authorization is already absent, no removal transaction is needed. If removal happened too early, the owner can approve a fresh key while the vault is still active to complete cleanup, then remove it again. This is voluntary shutdown recovery; an ordinary withdrawal never requires reapproval. After wind-down neither the vault nor the service can undo a state only the strategy could. Unverified venue ordering and delayed accepted actions remain release blockers. While the account is unpriced, holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Deposits, share transfers, settlement and the owner's withdrawal requests wait. Before wind-down the key stays approved.

Keep at least 0.01 HYPE in the trading account for Core transfer fees. Use client order ids of 2^64 or more. The vault's reduction orders use the operation nonce shifted left by 8 bits, plus the reduction's index in that round, and an order under one of those ids counts as that reduction. In the transactions observed so far, none of which mixed orders with transfers, Hyperliquid ran a transaction's actions in order, 5 or 6 L1 blocks after its HyperEVM block (about 0.3 to 0.4 seconds). Hyperliquid documents only that CoreWriter order actions and vault transfers wait a few seconds. It does not document that the actions stay in that order, or that one failed action leaves the later ones running. A reduction can fill while the transfer after it fails, and the freed collateral then waits in the trading account for the next round. A thin book can leave a reduction unfilled. The vault can remain in a Core state this software does not repair.

## Documentation and contributions

- [Creator quickstart](https://2n20.org/docs/quickstart)
- [SDK and strategy integration](https://2n20.org/docs/sdks)
- [Contribution and verification](https://github.com/2n20/creator-tools/blob/main/CONTRIBUTING.md)
- [Security reporting](https://github.com/2n20/creator-tools/blob/main/SECURITY.md)
- [Dependency attribution](https://github.com/2n20/creator-tools/blob/main/THIRD_PARTY.md)
- [MIT license](https://github.com/2n20/creator-tools/blob/main/LICENSE)

We maintain the CLI, skills and starter in this public repository. We keep the application and financial contracts in a separate private repository.


`status.pendingWithdrawals` reports fresh public withdrawal demand before a release becomes restricted, including cash-covered requests. `pendingRequestCount`, `escrowedShares`, `owedAssets`, `reservedAssets` and `uncoveredAssets` are decimal strings. `owedAssets` is the rounded-up current NAV value of escrowed shares, not a guaranteed payout. Reserved settled claims are separate from that value. The snapshot includes `restricted`, `operationInProgress`, `operationPhase` and its canonical block identity. Reads share one block hash. Missing reads, a changed block or chain evidence older than 45 seconds return `evidence: unavailable` with null values, never zero demand. During an operation, or while valuation is unpriced or stale, the count and reserves remain visible but owed and uncovered amounts are null.

Custom Python bots can call `twon20.withdrawals.poll_pending_withdrawals(scope, rpc)` with a previously verified scope containing `vault`, `approvalContract` and `tradingAccount`. The starter exposes the same read as `Access.pending_withdrawals()` and includes the signal in `Access.verify()` authority evidence. A bot can voluntarily back off, reduce exposure or avoid reopening while requests remain, even when `restricted` is false. Treat unavailable evidence as unknown. This is cooperative signaling only; a direct Hyperliquid client can bypass it. The starter's existing release restriction behavior remains unchanged. These reads neither load a signer nor authorize orders or custody actions.
