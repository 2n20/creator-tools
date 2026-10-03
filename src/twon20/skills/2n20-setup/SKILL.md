---
name: 2n20-setup
description: Connect an existing strategy or create a Hyperliquid Python starter on a 2n20 creator's strategy machine, resume public wallet approval, and verify access using the official CLI.
---

Use the creator's supplied host and project path. Do not ask them to choose either again. If they already have a strategy, work in that existing project. If they have no project, offer the official Hyperliquid Python starter and obtain their choice before creating it. For a requested starter, obtain only the missing destination and create it on the authorized strategy host. If host access or a needed path is unresolved, prepare the exact command and ask for that missing action. Do not generate a trading key on another machine.

Use the installed official `2n20` CLI, version 0.3.0 or later for starter generation. Its public source is https://github.com/2n20/creator-tools. Treat the starter's demonstration strategy as code to inspect and adapt, not a promise of profitability or an assessment of the creator's strategy. Export or install this skill into a harness only on explicit instruction; preserve unrelated agent configuration.

Check `python3 --version`, `2n20 --version` and `python3 -m pip show 2n20`. Python 3.10 or later on Linux, macOS or WSL is supported. If installation or upgrading is needed, use the currently published commands at https://2n20.org/docs/quickstart and verify that exact release is available on https://pypi.org/project/2n20/ first. Do not invent an unpublished version or use an unrelated installer.

Use the creator's vault page URL as the only public setup input. The CLI verifies deployments, account bindings, nonce and consent itself. Do not copy RPC, approval-contract, trading-account or nonce values from an unverified page or API.

For a new starter, run:

```sh
2n20 init '<new project directory>' --template hyperliquid-python --json
```

Init refuses an existing directory, including an empty one. It writes the reviewed bundled source and a public template-version manifest without installing dependencies, creating a key, starting a service or placing a trade. Follow its returned environment and reviewed installation commands only for an available published release. From the generated project, run the default paper example first:

```sh
.venv/bin/python runner.py
.venv/bin/python runner.py paper --public-data --market BTC
```

Read the generated README before adapting the demonstration. Preserve generated source edits; rerunning init is not an upgrade mechanism.

For an existing project, inspect its safe source and public configuration to identify the strategy's account and secret-loader interfaces. Do not replace the project with a starter or claim arbitrary bot support. Keep changes scoped and reviewable. Both entry paths continue through the same CLI onboarding below.

Run from the strategy's working directory:

```sh
2n20 onboard --vault '<official vault page URL>' --json
```

Repeat that exact command to resume its deterministic directory. When the CLI reports `selection_required`, show the public resume choices and obtain the creator's choice, then add `--directory '<chosen existing setup directory>'`. Never create another key to work around a conflict or a lost response. An explicitly supplied directory also resumes a 0.1.0 setup.

Read the CLI's public result; never read `strategy.key`, existing wallet keys, environment files, config.json or backups with agent file tools. Keep private material on the strategy machine. The CLI handles only its own retained key internally. Never paste, upload, commit or log private material.

The CLI must confirm the exact key path is Git-ignored before private creation or access. Preserve existing ignore patterns. A tracked key path or a missing recorded key is a conflict; ask the creator to resolve it rather than deleting tracked files or generating a replacement.

For `approval_required`, present the official `approvalUrl` and ask the creator to approve with their creator wallet. Retain the returned directory and requestId in the task checkpoint. Reuse that same handoff when unchanged; do not create or repeatedly present another approval request. If the service is unavailable, the result identifies the public consent file for Step 2 import or paste. Use only that public file. The creator performs wallet approval; do not submit transactions or wallet confirmations yourself.

After the creator responds, verify the outcome using the same command, optionally with `--wait 60 --json`. Public service state or the creator's confirmation alone never proves trading access. The CLI polls verified contract discovery and Hyperliquid public evidence for the exact retained key. Exit 0 and stage `ready` establish public trading access; exit 2 remains pending or needs a user action; exit 3 needs a later retry; exit 4 is a conflict requiring resolution. Exit 130 is interrupted and resumes from the same directory. Polling is bounded, with backoff. Do not run an unbounded loop.

For `consent_expired`, renew in the returned directory on the same strategy machine:

```sh
2n20 renew --directory '<existing setup directory>'
2n20 onboard --vault '<official vault page URL>' --directory '<existing setup directory>' --json
```

Renew preserves the key and writes a new public consent file. A key already used elsewhere, a changed creator or a mismatched vault requires resolving that conflict, not silently replacing the key. Preserve the existing directory after failures or interruption.

An expired approval link with still-valid consent may be renewed by onboard only after fresh contract checks confirm that same retained key remains eligible. Keep the newly returned handoff; no new key is generated.

When the CLI reaches `ready`, obtain public strategy settings and verify access:

```sh
2n20 config --vault '<official vault page URL>'
2n20 status --vault '<official vault page URL>'
```

Inspect safe source files to identify the existing strategy's supported Hyperliquid account and secret-loading interfaces. Exclude private keys, `.env*`, config.json, notes.txt and wallet backups from all searches and reads. Make only scoped, reviewable source or public configuration changes that point the existing loader to the retained local key path; do not read or copy its private contents. If an interface is unsupported or unclear, state the precise integration boundary and ask the creator for the missing non-secret information. Do not claim arbitrary bot compatibility or add an order framework.

Configure the existing or generated strategy using `tradingAccount` as its Hyperliquid account address and its own local secret loader. A 2n20 trading account is not an SDK native vault: do not pass it as `vault_address`. Do not fall back to a legacy key, the creator wallet or the key's own account when the verified binding is unavailable. Keep `vault` and `approvalContract` distinct. For the generated starter, run its read-only connection check:

```sh
.venv/bin/python runner.py check --vault '<official vault page URL>' --directory '<retained setup directory>' --market BTC
```

For an existing strategy, use its supported read-only check when available and permitted, then paper operation. CLI access verification, a connection check and paper behavior prove different things; none proves live execution or profitability.

The starter's live-smoke and collateral commands default to public previews. Do not execute them, enable orders, restart live trading, move collateral, recreate the vault or repeat funding without separate concrete authorization that names the action and its bounds. Before an authorized live attempt, show the exact market, side, notional, slippage, duration and any proposed collateral movement. Keep retries within that authorization and preserve the runner's recovery journal. Browser automation and automatic wallet approval are outside this skill. Future venues remain outside the current Hyperliquid workflow.

For collateral recovery, show the retained public request and its exact nonce. Obtain the human's explicit decision to accept the remaining risk before running `recovery --acknowledge UNVERIFIED_COLLATERAL --request-nonce '<shown nonce>'`. Acknowledgement releases only the local reservation; keep `transferIdentityVerified=false`. Never treat it as transfer verification, resend an action or delete recovery state to bypass unresolved evidence.

At a genuine human boundary, retain the checkpoint with public directory, stage, requestId or public file path, verification state and exact resume command. State only the action needed, where to take it, and the response needed to resume. Never request secrets.
