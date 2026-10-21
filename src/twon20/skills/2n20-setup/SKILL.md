---
name: 2n20-setup
description: Connect an existing strategy or create a Hyperliquid Python starter on a 2n20 creator's strategy machine, resume public wallet approval, and verify access using the official CLI.
---

## Website entry points

Read the complete guide for the requested step before asking questions or running setup. Both guides retain the website's wallet handoff and native multiple-choice questions. Use the actual vault URL from the creator's request wherever the guide says `<official vault page URL>`.

### Step 2: Approve trading access

Read [Step 2 instructions](references/step-2.md), then follow the CLI procedure below through preparation of the public approval link. Present that link and pause. Tell the creator to approve on the website, wait for Step 3 to open, then copy its connection instructions into this same conversation. Retain the project, setup directory and key. Wait for the Step 3 request before configuring the strategy or reporting completion.

### Step 3: Connect your strategy

The creator pastes Step 3's connection instructions after completing wallet approval on the website, normally in the same conversation as Step 2. Read [Step 3 instructions](references/step-3.md). Resume the retained project and key, check approval before configuration, and complete the connection check and completion report below. Ask only for missing details; do not restart project selection or generate a replacement key. If approval is still needed, resume its existing flow.

Use these guides from creator-tools 0.6.0. Installing or upgrading the CLI does not update a previously installed skill. If a local copy differs, preserve it and read this release's skill and references from the public source, or export this version to a new comparison directory. If the current agent session does not discover an installed skill, read its files directly in the same conversation. Read relative links beside this file, or against this file's released source URL. If a required guide cannot be read, explain the missing file and stop rather than reconstructing its instructions.

## CLI procedure

For strategy development or adapting a backtest before a vault exists, use the bundled `2n20-strategy` skill. Develop without live orders and return here when a vault is available. Use the project's own tests; the starter simulation is optional.

Use the creator's supplied host and project path. Do not ask them to choose either again. If they already have a strategy, work in that existing project. If they have no project, offer the official Hyperliquid Python starter and obtain their choice before creating it. If host access is unresolved, prepare the exact command and ask for that missing action. Do not generate a trading key on another machine.

For a requested starter without a chosen location, ask **Where should I create the starter?** Offer **Create a new folder for me (Recommended)** and **I will choose the location**, with free-text input. When the creator asks you to create the folder, choose a descriptive name under their home directory on the authorized machine, such as **2n20-vault-<short vault address>**. Check the proposed path yourself; do not ask whether it is "unused". Use a supplied path when provided. Once folder creation is authorized and the path is absent, proceed without asking for the same permission again. If the path exists or cannot be verified, resolve that conflict without reading or changing its contents. Let **2n20 init** create the destination; do not pre-create an empty project folder with mkdir, since init refuses existing directories. Create any missing parent only within the authorized location.

For missing details, ask one short question at a time. Use the harness's native question controls when available in the current mode. Offer two or three relevant choices for decisions, such as **Existing project / Official Python starter** or **This computer / VPS**. Retain an Other/free-text route, using the harness's built-in option when it provides one. Recommend an option only when the creator's context supports it. Ask for existing project paths and SSH destinations as short text inputs; never invent them. If question controls are unavailable, ask the same concise questions in chat without changing modes or requiring an integration. Reuse earlier answers and wait for an answer before dependent work. A preselected option or silence is not consent. These questions do not replace creator-wallet approval or authorize live trading.

Use the installed official `2n20` CLI, version 0.6.0 or later for this deployment and withdrawal coordination. Its public source is https://github.com/2n20/creator-tools. Treat the starter's demonstration strategy as code to inspect and adapt, not a promise of profitability or an assessment of the creator's strategy. Export or install this skill into a harness only on explicit instruction; preserve unrelated agent configuration.

Check `python3 --version`, `2n20 --version` and `python3 -m pip show 2n20`. Python 3.10 or later on Linux, macOS or WSL is supported. If installation or upgrading is needed, use the currently published commands at https://2n20.org/docs/quickstart and verify that exact release is available on https://pypi.org/project/2n20/ first. Do not invent an unpublished version or use an unrelated installer.

Use the creator's vault page URL as the only public setup input. The CLI verifies deployments, account bindings, nonce and consent itself. Do not copy RPC, approval-contract, trading-account or nonce values from an unverified page or API.

For a new starter, run:

```sh
2n20 init '<new project directory>' --template hyperliquid-python --json
```

Init refuses an existing directory, including an empty one. It writes the reviewed bundled source and a public template-version manifest without installing dependencies, creating a key, starting a service or placing a trade. Follow its returned environment and reviewed installation commands only for an available published release. Install project-local agent skills after init has created the starter directory. Read this guide directly while preparing the project. Do not pre-create the destination to install skills. Inspect the generated README and verify the installed CLI and runner help from the project's own environment. The starter's fixture simulation remains an optional development utility, not an onboarding requirement. Preserve generated source edits; rerunning init is not an upgrade mechanism.

For an existing project, inspect its safe source and public configuration to identify the strategy's account and secret-loader interfaces. Do not replace the project with a starter or claim arbitrary bot support. Keep changes scoped and reviewable. Both entry paths continue through the same CLI onboarding below.

Run from the strategy's working directory:

```sh
2n20 onboard --vault '<official vault page URL>' --json
```

Repeat that exact command to resume its deterministic directory. When the CLI reports `selection_required`, show the public resume choices and obtain the creator's choice, then add `--directory '<chosen existing setup directory>'`. Never create another key to work around a conflict or a lost response. An explicitly supplied directory also resumes a 0.1.0 setup.

Read the CLI's public result; never read `strategy.key`, existing wallet keys, environment files, config.json or backups with agent file tools. Keep private material on the strategy machine. The CLI handles only its own retained key internally. Never paste, upload, commit or log private material.

The CLI must confirm the exact key path is Git-ignored before private creation or access. Preserve existing ignore patterns. A tracked key path or a missing recorded key is a conflict; ask the creator to resolve it rather than deleting tracked files or generating a replacement.

For `approval_required`, present the official `approvalUrl` and ask the creator to approve with their creator wallet. Retain the returned directory and requestId in the task checkpoint. Reuse that same handoff when unchanged; do not create or repeatedly present another approval request. If the service is unavailable, the result identifies the public consent file for Step 2 import or paste. Use only that public file. The creator performs wallet approval; do not submit transactions or wallet confirmations yourself.

After the creator supplies the website's Step 3 request, verify the outcome using the same command, optionally with `--wait 60 --json`. Public service state or the creator's confirmation alone never proves trading access. The CLI polls verified contract discovery and Hyperliquid public evidence for the exact retained key. Exit 0 and stage `ready` establish public trading access; exit 2 remains pending or needs a user action; exit 3 needs a later retry; exit 4 is a conflict requiring resolution. Exit 130 is interrupted and resumes from the same directory. Polling is bounded, with backoff. Do not run an unbounded loop.

For `consent_expired`, renew in the returned directory on the same strategy machine:

```sh
2n20 renew --directory '<existing setup directory>'
2n20 onboard --vault '<official vault page URL>' --directory '<existing setup directory>' --json
```

Renew preserves the key and writes a new public consent file. A key already used elsewhere, a changed creator or a mismatched vault requires resolving that conflict, not silently replacing the key. Preserve the existing directory after failures or interruption.

An expired approval link with still-valid consent may be renewed by onboard only after fresh contract checks confirm that same retained key remains eligible. Keep the newly returned handoff; no new key is generated.

The CLI retries temporary RPC and approval-link failures automatically. Let the current command finish before starting another. If it still reports unavailable evidence, retain its exact resume command and public-file fallback. Do not reset the project, replace the key or repeat a deposit to repair a provider outage.

When the CLI reaches `ready`, obtain public strategy settings and verify access:

```sh
2n20 config --vault '<official vault page URL>'
2n20 status --vault '<official vault page URL>'
```

Inspect safe source files to identify the existing strategy's supported Hyperliquid account and secret-loading interfaces. Exclude private keys, `.env*`, config.json, notes.txt and wallet backups from all searches and reads. Make only scoped, reviewable source or public configuration changes that point the existing loader to the retained local key path; do not read or copy its private contents. If an interface is unsupported or unclear, state the precise integration boundary and ask the creator for the missing non-secret information. Do not claim arbitrary bot compatibility or add an order framework.

For the starter, review `config.public.example.json` with the creator. Reuse known choices and ask only for missing market, candle interval, strategy parameters, target and child notional limits, slippage, chase duration and reporting preferences. Write the agreed values to that public configuration or a chosen `*.public.json` file. Explain that the bundled numbers are examples, not authorization to trade. Precision comes from Hyperliquid metadata in the executor; no size or price decimals belong in user configuration. Keep secrets separate in the retained CLI key and existing local secret loader. For an existing project, preserve its configuration format and loader. Inspect safe source or a redacted schema; if a private config needs manual editing, provide the field names and have the creator perform that step locally without sharing secret values.

Configure the existing or generated strategy using `tradingAccount` as its Hyperliquid account address and its own local secret loader. A 2n20 trading account is not an SDK native vault: do not pass it as `vault_address`. Do not fall back to a legacy key, the creator wallet or the key's own account when the verified binding is unavailable. Keep `vault` and `approvalContract` distinct. For the generated starter, run its read-only connection check:

```sh
.venv/bin/python runner.py check --vault '<official vault page URL>' --directory '<retained setup directory>' --config config.public.example.json
```

Use the same reviewed public configuration file for the starter connection check and its paper/live runtime. Replace the example filename above when the project uses another public configuration. The check verifies its selected market and discovers precision from Hyperliquid metadata; do not ask the creator to configure decimals or add a separate market override. Keep an older or customized starter intact and inspect its supported commands before adapting it.

For an existing strategy, run its supported read-only connection check. Do not require a paper mode, add a simulator or place an order to complete setup. If the check is missing or fails, explain the integration gap and keep setup incomplete. Verified access and a read-only connection do not prove live execution or profitability.

After current public access and the configured project's read-only connection check both pass, report completion from the same retained setup directory:

```sh
2n20 complete-setup --vault '<official vault page URL>' --directory '<retained setup directory>' --connection-checked --json
```

Pass --connection-checked only after configuration and a successful read-only check for this project and account. The CLI verifies current public access and signs a separate software-setup report internally. It sends only public account bindings, the connection-check result, timestamps and a signature to the official website. It sends no private key, project files or logs and submits no blockchain transaction. The vault page completes Step 3 automatically after the service acknowledges the report. A failed or unavailable check must remain incomplete. If reporting fails, retain the exact directory and retry the same command after the service recovers; never replace its key or claim completion without acknowledgement.

Finish with a clear result after acknowledgement. State that the project is configured and connected, setup is complete, and no orders were placed during setup. Offer one optional next action: a bounded live test. Ask whether the creator wants to prepare that test or finish here, using native choices when available. Do not require a live test for setup completion or present paper trading as a competing onboarding path. State any remaining limitations specific to the project. Keep live trading disabled during setup.

For an optional live test, use the existing project's bounded test if it has one, or the starter's documented `live-smoke` command. Reuse the configured market. Ask for any missing side, maximum notional, slippage and duration. Show the read-only preview and obtain explicit approval of that exact plan before executing it. Do not invent a budget or transfer collateral to make the test pass. If collateral preparation is needed, preview it and obtain its own authorization. Report the live test result separately from setup completion, including unresolved exposure and its recovery command. A preview, rejected order or incomplete round trip is not a successful live test.

The starter's continuous `run --mode live`, live-smoke and collateral commands default to public previews. The continuous runner needs its own explicit session duration and execution confirmation. Do not execute them, enable orders, restart live trading, move collateral, recreate the vault or repeat funding without separate concrete authorization that names the action and its bounds. Before an authorized live attempt, show the exact market, side, notional, slippage, duration and any proposed collateral movement. Keep retries within that authorization and preserve the runner's recovery journal. Browser automation and automatic wallet approval are outside this skill. Future venues remain outside the current Hyperliquid workflow.

`2n20 status` reports the withdrawal release and whether the trading account is priced. The release fields are `active`, `noticeAt`, `roundSeconds`, `openedThisRound`, `misses`, `lastMissAt` and `cutsAllowedAt`. `noticeAt` is the earliest time a round may cancel orders or reduce positions for the current shortfall. `roundSeconds` is 600. `openedThisRound` is true when a report in the open round showed the strategy adding risk. `misses` counts missed rounds. The count is information, and the key stays approved. `cutsAllowedAt` is the earliest time a later round may reduce positions. Zero, or a time already passed, means the next round may reduce positions. For an unpriced account, status names each reason and whether that reason waits for data, gets a venue repair during a release, or needs the strategy. Before the first valuation report, `pricing.action` is `unreported`. The report includes the key's `validUntil` and warns when expiry is inside 14 days. During a release the strategy keeps its key and needs no new approval, and the creator can approve a key at most once every 10 minutes. The key stays approved through an ordinary withdrawal and while the account is unpriced. A miss is not a reason to generate a new key. After expiry the creator approves a fresh key. A key that was already used cannot be approved again. The vault keeps operating when a key expires, and trading stays stopped until that approval. Missing evidence leaves access unverified. Do not describe status as proof that the strategy can trade or that every HyperCore state can be repaired.

While the vault is restricted, an authorized live runtime stops opening new risk, cancels its open entry order and leaves its reduce-only orders in place, including take-profit and stop-loss orders marked reduce-only. New risk also waits while the runtime's release read fails or goes stale. The vault moves free collateral itself. A strategy you write yourself should do the same. Proportional withdrawals are the default. Before settlement, the service reduces native perpetual positions by the withdrawing shares’ fraction and confirms the frozen reduction targets, including when vault cash covers the request. This aims to preserve similar leverage and liquidation prices, with costs, rounding and concurrent trading affecting the result. Supported portfolios contain native perps, USDC and a small unheld HYPE gas reserve; other spot holdings or HIP-3 activity require portfolio changes before settlement. Creators can choose Margin-first at creation or change the on-chain mode while the vault is open and idle with no pending withdrawals. Margin-first uses vault cash and free spot USDC, then native margin if it covers the full remainder at the set leverages. Otherwise the service cancels margin-consuming non-reduce-only orders, smallest margin first, then repeats bounded 20% native-position waves. It considers supported HIP-3 collateral last. Removing margin without resizing positions can increase actual leverage and move liquidation prices closer. Both modes preserve reduce-only protection and creator trading authorization, use fresh observations between operations and retain execution, gas and retry limits. Direct API clients can bypass bot coordination and delay withdrawals.

A release starts after a two-minute notice. Native cuts are capped at 20% of the current position, rounded down, in margin-first mode; each market can be cut at most once per 10-minute round. Proportional mode instead uses the pending request’s share fraction. The service preserves reduce-only orders, uses a 0.4% mark-price limit and observed depth; the contract enforces a 0.5% band. No native minimum-size uplift exceeds the cap. The contract permits at most eight cancellations or five cuts in one round call and relies on the operator to classify protective orders. A full protective book escalates after three distinct fresh observations across 20 minutes, with five-minute rechecks. This bounds silent waiting, not time to payout. Confirmed-revert backoff and gas controls remain; independently safe work can proceed. The creator key stays approved, but a bypassing strategy can reuse freed margin before capture.

Use standard account mode. Do not deposit into a Hyperliquid vault, stake HYPE, open a sub-account, borrow, lend, or hold builder-deployed market collateral without a USDC price. Those states leave the account unpriced, as do a missing collateral price and an unfinished account read. Missing prices and an unfinished read clear when the data returns. During a release, or while no trading key is active, the vault restores standard account mode and withdraws Hyperliquid vault equity after its lockup. The strategy undoes every other unpriced state. For voluntary wind-down, stop the strategy, clear outstanding orders and TWAPs, and undo strategy-only unsupported states while authorization still works. Remove the trading key in the application and wait for confirmed removal, then obtain a fresh complete account observation. Wind-down does not remove the key itself. The contract refuses wind-down while authorization or an account operation is pending, orders or TWAPs remain, or the report is stale, incomplete or shows a blocking unsupported state. If authorization is already absent, no removal transaction is needed. After wind-down neither the vault nor the service can undo strategy-only states. While the account is unpriced, holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Deposits, share transfers, settlement and the owner's withdrawal requests wait. Before wind-down the key stays approved. Keep at least 0.01 HYPE in the trading account for Core transfer fees. Use client order ids of 2^64 or more. The vault's reduction orders use the operation nonce shifted left by 8 bits, plus the reduction's index in that round, and an order under one of those ids counts as that reduction. In the transactions observed so far, none of which mixed orders with transfers, Hyperliquid ran a transaction's actions in order, 5 or 6 L1 blocks after its HyperEVM block (about 0.3 to 0.4 seconds). Hyperliquid documents only that CoreWriter order actions and vault transfers wait a few seconds. It does not document that the actions stay in that order, or that one failed action leaves the later ones running. A reduction can fill while the transfer after it fails, and the freed collateral then waits in the trading account for the next round. A thin book can leave a reduction unfilled. The vault can remain in a Core state this software does not repair. Do not start that runtime, or move collateral yourself, without a separate authorization that names the action and its bounds.

For collateral recovery, show the retained public request and its exact nonce. Obtain the human's explicit decision to accept the remaining risk before running `recovery --acknowledge UNVERIFIED_COLLATERAL --request-nonce '<shown nonce>'`. Acknowledgement releases only the local reservation; keep `transferIdentityVerified=false`. Never treat it as transfer verification, resend an action or delete recovery state to bypass unresolved evidence.

At a genuine human boundary, retain the checkpoint with public directory, stage, requestId or public file path, verification state and exact resume command. State only the action needed, where to take it, and the response needed to resume. Never request secrets.
