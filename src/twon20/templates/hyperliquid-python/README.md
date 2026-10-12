# Hyperliquid Python starter

A continuous official-SDK candle feed, a replaceable educational band-breakout strategy, a maker-first limit-chasing executor and local execution reports for a CLI-verified 2n20 trading account. Paper is the default. The same strategy produces normalized targets in paper and live modes. It makes no profitability claim. The separate finite paper and live-smoke diagnostics remain available.

You operate this software on your own computer or server. Python 3.10 or later, an existing 2n20 vault and a place to run the software are prerequisites for connecting the account. Deterministic paper can run before creating or funding a vault.

## Install

This project was exported by `2n20 init`. Keep the CLI installed at the released version that generated it. The official SDK is an additional runner dependency, excluded from the CLI's base dependencies. The full reviewed third-party graph is pinned and hashed in `requirements-runner.txt`; registry timestamps and hashes are in `dependency-review.json`.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip --isolated install '2n20==<RELEASED_CLI_VERSION>'
python -m pip install --require-hashes --only-binary=:all: -r requirements-runner.txt
python runner.py --help
```

Replace `<RELEASED_CLI_VERSION>` with the version in `2n20-template.public.json`, using the exact `installPublishedCli` command returned by `2n20 init`. Run that command inside this new environment before running the project. A source preview of an unreleased version must use a locally built reviewed wheel instead; do not assume a registry version exists until the release is published.

The public feed uses the pinned SDK's `Info.subscribe` candle stream. Websocket `data` is one candle object; `t` opens the interval and `T` closes it. There is no exchange close flag. After close plus a 1.5-second grace, a REST `candles_snapshot` confirms the completed candle. Only fresh contiguous decisions may trade. Initial warmup and repaired history update the strategy without trading historical signals. The runtime recreates silent/disconnected sockets and repairs overlaps/gaps; it does not depend on SDK replay. A changed committed bar stops decisions for review.

The example strategy computes a rolling mean and sample standard deviation. A close above the upper band chooses long, a close below the lower band chooses short, and crossing the rolling mean chooses flat. Direction persists otherwise. This is an educational component with its own small checkpoint, not a complete research strategy or an alpha claim. Paper fills are immediate and fictional, including shorts, and omit fees, funding, latency and market impact.

| Module | Replace or inspect here |
| --- | --- |
| `configuration.py` | Public configuration shared by connection checks and the runtime |
| `models.py` | `Bar`, normalized `Target`, market identity checks and small Feed/Strategy/Executor interfaces |
| `feed.py` | Fixture or official SDK candle feed |
| `strategy.py` | `BandBreakout.evaluate(bars, checkpoint)` and the legacy finite paper example |
| `runtime.py` | Constructor composition, strategy checkpoint and frozen target sizing |
| `executor.py` | Paper fills and incremental `LimitChaser` |
| `runtime_adapter.py` | Verified account reads, exact owned-child submission/cancellation |
| `runtime_io.py` | One serialized network worker and effect-admission checks |
| `runtime_journal.py` | One bounded atomic runtime checkpoint |
| `reporting.py` | Local JSON output and optional Telegram |
| `adapter.py`, `risk.py`, `execution.py` | Existing SDK/account boundary and separate finite diagnostics |

To replace the strategy, change `BandBreakout` in `strategy.py` and its construction in `runtime.py`. Its only inputs are validated bars and its prior public checkpoint. Return `(Target(Decimal("1"), "my_reason"), checkpoint)` for a normalized long target, fraction `-1` for short, or `0` for flat. Return `None` as the target to retain the current intent. Fractions between -1 and 1 scale the configured notional. No SDK client, order ID or signer belongs in the strategy. Use the same constructor in paper and live mode. A custom feed must return Bar/BarBatch observations for the configured market and interval, with the same validation and continuity rules. Other sampling methods require an explicit adaptation of that contract. It implements nonblocking `poll(now_ms)`, `healthy`, `done` and `close`; a custom executor implements `recover`, `set_target`, `step` and `shutdown`. Replace those constructors in the composition, keeping all effect and durability boundaries intact. This starter includes no backtester, optimizer or hosted service.

## Configure your strategy

Review `config.public.example.json`, or save your chosen settings as `*.public.json`. Use schema `2n20-runtime-config-v2`. Set the market, candle interval, strategy parameters, target/child notional, slippage, chase duration and reporting preferences. The bundled values are examples, not authorization to trade. Keep credentials in the retained CLI key and local secret loader, separate from this public file. Preserve an existing project's configuration and loader instead of copying secret contents.

Pass the same file with `--config` to `check` and `run`. Size precision comes from Hyperliquid's `szDecimals` metadata; the executor derives price precision from Hyperliquid's rules and rounds order quantities down. No precision field belongs in user configuration. The read-only check and live preview report the observed precision, and each child order uses fresh metadata. Changed precision invalidates an already prepared order before submission.

`targetNotional` fixes the desired signed coin quantity at the decision's close price. Repeating the same normalized fraction keeps that target across restarts. The executor rounds each actual child order to the current venue lot size; a non-tradeable remainder is dust, not a filled target. The developer simulation uses unrounded fictional quantities and does not model venue precision or fills.

`childNotional`, `maxChildren`, `chaseSeconds`, `requoteSeconds` and `slippageBps` bound the chase. These limits are separate from the diagnostic smoke limits of 10 to 100 USDC. Changing markets does not require editing the feed, strategy or executor. Retained state belongs to its original configuration; do not reuse another market's journal or delete it to force a switch. Existing generated projects require deliberate source/configuration review; a CLI upgrade does not rewrite them.

Children start post-only (`Alo`). Set `iocAfterSeconds` to an explicit time before `chaseSeconds` to allow later IOC urgency; null keeps all children maker-first. Optional `depthFraction` caps a child to a fraction of the selected best book level's notional. Optional `dailyVolumeFraction` caps it to that fraction of reported 24-hour notional volume, prorated over `requoteSeconds`. These caps can only reduce size. A cap below the venue minimum produces `residual_dust`, not a raised budget. Arrival-price bounds are hard admission checks. Partial fills retain their exact quantity and VWAP. A side flip first reduces the owned position and verifies zero before opening the other side. Dust is never reported as flat.

## Optional developer simulation

Use the simulation to exercise changes without orders, or use your existing tests. It is not required for vault setup. Run `python -m unittest discover -s tests -v` for the bundled regression suite.

`python runner.py` uses invented deterministic closes in `fixture.candles.public.json`. It never loads a signer, submits an order, transfers collateral or starts a service. Its fictional fills omit fees, funding, latency and market impact. To simulate against public recent candles instead:

```sh
python runner.py paper --public-data --market BTC
```

### Run a development simulation

```sh
# Finite, deterministic observations with fictional fills. No account needed.
python runner.py run --mode paper --feed fixture --max-bars 30

# Continuous public candle stream, still fictional fills and no signer.
python runner.py run --mode paper --feed public --config config.public.example.json

# A bounded public-data demonstration.
python runner.py run --mode paper --feed public --max-seconds 90
```

Stop the foreground process with Ctrl-C. The fixture contains 32 observations and exits on its own. Public mode remains running until stopped or a bound is reached. Default runtime directories are `.runtime-paper-fixture`, `.runtime-paper-public` and `.runtime-live-public`. Keep each directory for restart, or supply an explicit private `--state-directory`. The diagnostic commands continue to use `.runner-state`. State directories and generated credentials stay ignored.

## Preview and explicitly authorize a live runtime

After the connection check, review a public configuration and choose a bounded session duration. The following is a preview and submits no orders:

```sh
python runner.py run --mode live --feed public --config config.public.example.json \
  --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --max-seconds 300
```

Only after deliberately authorizing those limits, repeat with `--execute LIVE_RUNTIME`. Approval through the website alone does not authorize live trading. The runtime supports one native USDC perpetual on an account exclusively operated by this process. Any unowned orders, fills or unexplained positions block it. It never adopts existing exposure, cancels another order, raises leverage or transfers collateral.

The session deadline is retained. Restarting the same command reconciles the same identities without renewing that deadline. New orders stop at expiry; exact owned-order cancellation has a separately retained 60-second cleanup grace. Stopping or a failed feed also inhibits new children and attempts bounded owned cleanup. It does not flatten positions automatically. Network requests cannot be recalled by a thread timeout. An unresolved child and account reservation remain durable after the cleanup budget ends.

Use `--recover-only` with the same live command, state directory and explicit execution flag to authorize a fresh cleanup-only duration (`--max-seconds`). It never evaluates strategy or submits a new child, and can recover retained order plans even if strategy source has changed. Example:

```sh
python runner.py run --mode live --feed public --config config.public.example.json \
  --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --state-directory '<ORIGINAL_RUNTIME_DIRECTORY>' \
  --max-seconds 60 --recover-only --execute LIVE_RUNTIME
```

After all children are resolved, `--new-session` explicitly reauthorizes the retained configuration and frozen target for another duration. Preview it without `--execute` first. Fresh account activity and exact owned position must agree; a retained child blocks rearming. It preserves quantities and child sequence identity and renews chase/session deadlines only because this flag was supplied. A changed strategy or configuration needs explicit migration/recovery review, not a reset journal. Never delete state or account registry files to force progress.

The single atomic checkpoint includes the bar cursor, bounded strategy history/state, active and latest pending intents, child phase and exact fills. New decisions coalesce while an old child is reconciled. A canceled partial child is retired only when terminal order status, `origSz - sz`, complete matching fills and fresh owned position agree. If canceled-order remaining size is absent or uses unsupported semantics, the child remains blocked. `unknownOid`, missing fills, a full history page or mismatched evidence cannot license replacement. Mock tests do not establish funded acceptance or live execution quality.

The foreground coordinator handles at most eight bars per tick and waits at most 100 ms between iterations, excluding local checkpoint/output I/O and user replacement code. One serialized worker holds at most one network task. It rechecks stop, feed freshness, target generation and retained deadlines immediately before each order. The feed has 128 coalesced ingress entries, 128 retained bars and at most 512 backfill intervals. Overflow latches repair outside the buffer. Eight failed recovery attempts stop the feed; an outage beyond that history needs review.

Checkpoint reads and writes share a 256 KiB ceiling. Active child evidence is capped at 256 fills. Account history retains the original activity baseline and at most 256 completed owned order IDs; a 2,000-row activity response blocks rather than truncates. A verified terminal child compacts exact quantity/value/fees into current-intent totals. The last 64 local report records remain in the checkpoint; stdout can be captured by your own log retention system. Evidence limits require review before further trading, not silent watermark advancement. Account reservations remain associated with the runtime directory at shutdown, including when flat.

## Optional Telegram reports

Local JSON reports are written durably before delivery. They include stable event IDs, target and child IDs, verified size, residual, VWAP, USDC fees and maker/taker fill counts. Paper records are labeled simulated. To enable optional Telegram, set `reporting.telegram` to true in your reviewed public configuration and supply `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to the process through your own local environment. Do not paste their values into chat, configuration, logs or version control.

While a child order is unresolved, `position` and `residual` are null. `knownPosition` includes the child's verified partial fills, and fill totals include those fills once. Further fills remain possible until reconciliation completes. The shutdown result uses the same position and fill fields, so an unresolved partial order cannot appear flat.

Telegram uses a queue of 16 records, a three-second request timeout and at most two attempts per record. Full queues or delivery failure affect remote reporting only. Local records remain authoritative. A lost acknowledgement may cause a duplicate; event IDs help identify it. Exactly-once delivery is not promised.

Protocol discovery, consent, approval and private-key recovery remain in the CLI.

## Connect the existing vault

Run setup where this project will operate, using the vault link copied from 2n20. Resume the existing directory when one already exists; never generate another key to bypass an interrupted flow.

```sh
2n20 onboard --vault '<VAULT_URL>' --json
# Open the returned approval link yourself and approve with the creator wallet.
2n20 onboard --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --json
2n20 config --vault '<VAULT_URL>'
2n20 status --vault '<VAULT_URL>' --json
python runner.py check --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --config config.public.example.json
```

The read-only `check` verifies the reviewed installed SDK, public CLI setup record, retained key path permissions, fresh matching key/account approval, supported standard account mode, native market precision and public spot/perpetual balances. It reports account conflicts. It does not read the private key, sign, submit an order or prove order execution or a complete working strategy. Unavailable evidence remains unverified. This release supports the reviewed mainnet deployment and native USDC perpetuals only, without a legacy vault-address fallback.

The trading key remains at `<SETUP_DIRECTORY>/strategy.key` with mode 600 inside a mode 700 directory. Only the intended runtime reads it internally for an explicitly authorized SDK action. Agents must never inspect, print, paste, upload or commit its contents. Public consent and configuration files are safe to share; `strategy.key` is private. Keep the same key and journal through errors. The setup CLI handles consent expiry/nonce changes and the public-file fallback when the approval service is unavailable. A creator-wallet approval is separate from verified HyperCore activation and usable collateral.

After the configured project's read-only check passes, report completion with the retained key:

```sh
2n20 complete-setup --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' --connection-checked --json
```

The website completes Step 3 after acknowledgement. Setup means configured and connected; it submits no orders and makes no paper or live-test claim.

## Bounded live smoke

Complete configuration and the read-only connection check first. The live test is optional and its result is separate from setup completion. Live orders require a separate explicit decision including market, side, notional, slippage and time. The values below illustrate command syntax; choose your own limits after reviewing the preview. No setup or approval command automatically enables these actions.

```sh
python runner.py live-smoke --vault '<VAULT_URL>' --directory '<SETUP_DIRECTORY>' \
  --market '<CONFIGURED_MARKET>' --side buy --max-notional 12 --slippage-bps 20 --duration-seconds 30
```

Replace `<CONFIGURED_MARKET>` with the market in your reviewed configuration. The preview never loads a signer or writes an execution journal. The example accepts a 10 to 100 USDC maximum notional, 1 to 50 basis points of slippage and 5 to 120 seconds. Market lot rounding must still form a valid minimum 10 USDC order. A budget of 10 USDC may be too small after rounding; a balance below the minimum plus fees cannot execute this example. The check conservatively requires enough usable perpetual collateral for the full notional, both taker fees and a reserve. It never raises leverage, deposits, increases a budget or deploys a contract.

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

Exit codes are 0 for paper, read-only check, preview, verified complete round trips and explicit human collateral acknowledgement; 2 for recovery review, changed historical completion, explicit submission rejection, unresolved evidence/exposure or observed collateral balance changes without transfer identity; 3 for refused/unavailable checks; 130 for interruption. Every result is JSON with a stable schema/stage and public next action. Validated executions include an exact resume instruction; invalid vault inputs are never echoed into one. Errors omit provider exception text, credentials and signed payloads. Shutdown does not hide unresolved exposure or authorize extra orders. These finite diagnostics do not automatically cancel orders or restart services. Continuous-runtime owned cancellation and explicit recovery are described above.

## Running on a shared server

Use a separate project, virtual environment, state directory, logs and service definition. Prefer a dedicated unprivileged Unix user or an appropriately isolated container. Directory separation alone is not complete isolation. Keep existing processes, dependencies and credentials untouched. The CLI, skill and starter do not SSH, install a service, enable autostart, restart trading software or start live order submission. Use only access you explicitly authorized and retain responsibility for monitoring and operating your strategy.

Generated setup directories, keys, journals and logs are ignored. Preserve these ignore rules and unrelated configuration. Agents should review only safe source/public settings and use the existing secret-loading boundary. Do not stage private artifacts or runtime state. See [ATTRIBUTION.md](ATTRIBUTION.md) for reviewed SDK/API sources.
