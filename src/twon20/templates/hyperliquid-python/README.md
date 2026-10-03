# Hyperliquid Python starter

An educational moving-average crossover and a small official SDK adapter for a CLI-verified 2n20 trading account. Paper is the default. The crossover demonstrates software structure, with no profitability claim. The separately invoked live smoke test is one bounded entry followed by one reduction of confirmed fills; it does not run the moving-average strategy live.

You operate this software on your own computer or server. Python 3.10 or later, an existing 2n20 vault and a place to run the software are prerequisites for connecting the account. Deterministic paper can run before creating or funding a vault.

## Install and run paper

This project was exported by `2n20 init`. Keep the CLI installed at the released version that generated it. The official SDK is an additional runner dependency, excluded from the CLI's base dependencies. The full reviewed third-party graph is pinned and hashed in `requirements-runner.txt`; registry timestamps and hashes are in `dependency-review.json`.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip --isolated install '2n20==<RELEASED_CLI_VERSION>'
python -m pip install --require-hashes --only-binary=:all: -r requirements-runner.txt
python -m unittest discover -s tests -v
python runner.py
```

Replace `<RELEASED_CLI_VERSION>` with the version in `2n20-template.public.json`, using the exact `installPublishedCli` command returned by `2n20 init`. Run that command inside this new environment before the tests. A source preview of an unreleased version must use a locally built reviewed wheel instead; do not assume a registry version exists until the release is published.

`python runner.py` uses invented deterministic closes in `fixture.candles.public.json`. It never loads a signer, submits an order, transfers collateral or starts a service. Its fictional fills omit fees, funding, latency and market impact. To simulate against public recent candles instead:

```sh
python runner.py paper --public-data --market BTC
```

`strategy.py` contains only the crossover and finite simulation. `adapter.py` holds the official SDK boundary, `risk.py` the explicit limits and `execution.py` the finite smoke workflow. Protocol discovery, consent, approval and key recovery stay in the CLI. `config.public.example.json` is a placeholder reference, never a place for credentials.

## Connect the existing vault

Run setup where this project will operate, using the vault link copied from 2n20. Resume the existing directory when one already exists; never generate another key to bypass an interrupted flow.

```sh
2n20 onboard --vault '<VAULT_URL>' --json
# Open the returned approval link yourself and approve with the creator wallet.
2n20 onboard --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --json
2n20 config --vault '<VAULT_URL>'
2n20 status --vault '<VAULT_URL>' --json
python runner.py check --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --market BTC
```

The read-only `check` verifies the reviewed installed SDK, public CLI setup record, retained key path permissions, fresh matching key/account approval, supported standard account mode, native market precision and public spot/perpetual balances. It reports account conflicts. It does not read the private key, sign, submit an order or prove order execution or a complete working strategy. Unavailable evidence remains unverified. This release supports the reviewed mainnet deployment and native USDC perpetuals only, without a legacy vault-address fallback.

The trading key remains at `<SETUP_DIRECTORY>/strategy.key` with mode 600 inside a mode 700 directory. Only the intended runtime reads it internally for an explicitly authorized SDK action. Agents must never inspect, print, paste, upload or commit its contents. Public consent and configuration files are safe to share; `strategy.key` is private. Keep the same key and journal through errors. The setup CLI handles consent expiry/nonce changes and the public-file fallback when the approval service is unavailable. A creator-wallet approval is separate from verified HyperCore activation and usable collateral.

## Preview a bounded live smoke test

First use paper and read-only checks. Live orders require a separate explicit decision including market, side, notional, slippage and time. The values below illustrate command syntax; choose your own limits after reviewing the preview. No setup or approval command automatically enables these actions.

```sh
python runner.py live-smoke --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' \
  --market BTC --side buy --max-notional 12 --slippage-bps 20 --duration-seconds 30
```

The preview never loads a signer or writes an execution journal. The example accepts a 10 to 100 USDC maximum notional, 1 to 50 basis points of slippage and 5 to 120 seconds. Market lot rounding must still form a valid minimum 10 USDC order. A budget of 10 USDC may be too small after rounding; a balance below the minimum plus fees cannot execute this example. The check conservatively requires enough usable perpetual collateral for the full notional, both taker fees and a reserve. It never raises leverage, deposits, increases a budget or deploys a contract.

Only if you deliberately authorize the displayed plan, repeat the exact preview command with `--execute LIVE_SMOKE`. It sends at most two immediate-or-cancel orders: one entry, then one reduce-only reduction sized to confirmed entry fills. Each write rechecks the local key's approval and account binding. Per-order rejection, partial fills, stale reads and unavailable access are explicit outcomes. A small partial entry can produce exposure below the normal minimum; the reduction may reject. No dust exemption is promised and no automatic size increase occurs.

The runner refuses any existing native or builder-perp positions or open orders before an entry. It will not cancel unrelated orders, close unrelated positions or alter account settings. The closing account position must equal the runner's verified fill exactly. The market may move beyond the closing exposure/slippage bound, the key may be revoked or a close may reject or fill partially. The result then reports unresolved exposure for your own review. A complete result requires matching public order/fill evidence and a flat account, never a submitted transaction, accepted response or stored journal.

## Spot USDC is separate from perpetual collateral

Vault funding can arrive as spot USDC. That is not immediately usable perpetual margin in the supported standard account mode. An optional separate command previews an explicit amount moved within the same verified account:

```sh
python runner.py collateral --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --amount 5
```

It refuses an amount equal to the entire available balance, more than 100 USDC, more than six decimals, existing positions/orders or unavailable native-USDC metadata. To authorize that specific transfer, repeat the same command with `--execute TRANSFER_TO_PERPS`. This does not authorize an order. It uses the documented `agentSendAsset` action through the official SDK's public signer and transport. The pinned SDK does not expose a convenience method for this action; the adapter does not implement signing or a replacement HTTP API.

The command reports matching spot/perpetual balance changes separately from the identity of an individual transfer. The reviewed API response does not provide a transaction identifier that this example can bind to a ledger event, so it does not claim a specific transfer was confirmed. Recheck current collateral using `check` before a separately reviewed smoke preview. If submission is ambiguous, this command will never resend the transfer automatically. Its account reservation remains in place while the individual transfer identity is unresolved.

## Interruptions and recovery

Public durable intent is written atomically with mode 600 before a signer is loaded or an action is submitted. The default state directory is `.runner-state/`, mode 700. Each directory has an exclusive lock, and a durable account registry binds unresolved actions to their original directory. Choosing another state directory cannot bypass an unresolved intent. Keep the directory, original limits and public client order IDs. An account lock prevents simultaneous runs by the same Unix user across project directories. It cannot coordinate other users, containers, machines or arbitrary bots. Use a dedicated key and account; do not run another process with the same key/account on any host.

Lost responses and `unknownOid` are unresolved evidence, never permission to enter again. Repeat the exact `resumeCommand` shown in output to reconcile the existing intent through public reads. A confirmed entry awaiting its first reduction after a restart additionally requires `--execute LIVE_SMOKE --resume-close`. The original order/fills are reverified before that reduction. A reduction already attempted is never repeated, including partial fills or rejections. Other account fills after the intent began invalidate recovery, even if someone closed and reopened the same net position. Completed history is rechecked against current fills and flat account state; a changed or unavailable account never appears currently complete. Review unresolved exposure yourself. The example makes no exactly-once delivery claim.

After matching collateral balance changes have been observed, a human may review the exact retained request and explicitly acknowledge the remaining unverified transfer identity. This command performs public reads and local journaling only:

```sh
python runner.py recovery --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --state-directory '<ORIGINAL_STATE_DIRECTORY>'
# Only after you personally review the displayed request nonce, amount and account:
python runner.py recovery --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --state-directory '<ORIGINAL_STATE_DIRECTORY>' \
  --acknowledge UNVERIFIED_COLLATERAL --request-nonce '<NONCE_SHOWN_IN_REVIEW>'
```

Acknowledgement requires fresh no-conflict account evidence and reverified completion of any retained smoke orders. It records a human residual-risk decision and releases the local reservation; transfer identity stays unverified. It never loads a signer, resubmits, resets an order journal or authorizes another trade. A pending transfer without matching observed balances remains blocked. Do not delete registry files to bypass unresolved evidence. Paper, checks and previews remain available. A new experiment after verified completion or explicit collateral acknowledgement still needs a separately reviewed state directory and limits.

Exit codes are 0 for paper, read-only check, preview, verified complete round trips and explicit human collateral acknowledgement; 2 for recovery review, changed historical completion, explicit submission rejection, unresolved evidence/exposure or observed collateral balance changes without transfer identity; 3 for refused/unavailable checks; 130 for interruption. Every result is JSON with a stable schema/stage and public next action. Validated executions include an exact resume instruction; invalid vault inputs are never echoed into one. Errors omit provider exception text, credentials and signed payloads. Shutdown does not hide unresolved exposure or authorize extra orders. There is no automatic order cancellation, recovery trade or service restart.

## Running on a shared server

Use a separate project, virtual environment, state directory, logs and service definition. Prefer a dedicated unprivileged Unix user or an appropriately isolated container. Directory separation alone is not complete isolation. Keep existing processes, dependencies and credentials untouched. The CLI, skill and starter do not SSH, install a service, enable autostart, restart trading software or start live order submission. Use only access you explicitly authorized and retain responsibility for monitoring and operating your strategy.

Generated setup directories, keys, journals and logs are ignored. Preserve these ignore rules and unrelated configuration. Agents should review only safe source/public settings and use the existing secret-loading boundary. Do not stage private artifacts or runtime state. See [ATTRIBUTION.md](ATTRIBUTION.md) for reviewed SDK/API sources.
