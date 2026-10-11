# 2n20 creator tools

Connect your strategy software to a [2n20](https://2n20.org) vault on Hyperliquid. Use your existing project or start with the included Python example.

Give your coding agent the `2n20-setup` skill, or run the `2n20` CLI yourself. Run setup on the computer or server that will operate your strategy. You receive an approval link to review with your creator wallet. Your private trading key stays on the strategy machine.

After approval, resume the same setup directory to verify trading access and get your strategy settings. You define and operate the strategy. The starter includes Hyperliquid candles, an educational band-breakout strategy, a replaceable limit chaser and execution reporting. It makes no profitability claim. The Python import is `twon20`.

## Install

Use Python 3.10 or later on your strategy computer or server:

```sh
python3 -m venv .venv-2n20
source .venv-2n20/bin/activate
python -m pip --isolated install '2n20==0.4.0'
2n20 --version
```

You install the Hyperliquid SDK with the starter's separate runner dependency lock. The base CLI needs no execution SDK. See the [release instructions](https://github.com/2n20/creator-tools/blob/main/docs/releases.md) to build from source or check release artifacts.

## Start or connect a project

Generate a new project in a directory that does not exist:

```sh
2n20 init my-strategy --template hyperliquid-python
```

Follow the installation and paper-run commands that `init` prints. It copies the bundled template and records its version. It does not install dependencies, create keys or start trading. Inspect the [template source](https://github.com/2n20/creator-tools/tree/main/src/twon20/templates/hyperliquid-python) before using it. Review future template changes before updating your generated project.

For an existing strategy, continue in its authorized project directory. Never search unrelated projects or read their credentials to discover access.

```sh
2n20 onboard --vault '<official vault page URL>' --json
```

Save the returned `resumeCommand` and `approvalUrl`. If you run setup on a VPS, open the link on your wallet computer. Review the prepared request and approve with your creator wallet. Opening the link does not authorize a transaction.

Then run the exact resume command with the same setup directory. Read the CLI's separate results for contract approval, HyperCore activation and funding. An unavailable check leaves access unverified.

Resume with the existing key after an interruption. For expired consent or a changed nonce, follow the CLI's same-key renewal instruction. Resolve a conflicting requested or approved key before continuing. Do not generate another key to bypass a failed check.

The CLI automatically retries temporary RPC and approval-link failures within a bounded budget, preserving the same key and public consent. If the handoff service remains unavailable, import the exact public consent file printed by the CLI into the vault's Step 2. Resume the same directory. Renewal creates a new public file and retains the earlier files and private key.

## Use your coding agent

From your strategy project, install both guides for your coding agent:

```sh
2n20 skill install --agent codex
```

Use `cursor`, `claude` or `hermes` instead of `codex` for those agents. Codex installs into the project's `.agents/skills`, Cursor into `.cursor/skills`, and Claude Code into `.claude/skills`. Hermes uses `~/.hermes/skills` or the configured `HERMES_HOME`. Reload the agent's skills after installation. Identical copies stay unchanged; conflicting local copies are preserved for your review.

Use `2n20-strategy` to adapt an existing backtest or bot, or develop the starter before a vault exists. The guide keeps your signals, sizing and research tools, and helps add only the operating modules you need. For example:

> Use 2n20-strategy to adapt this project's backtested strategy for Hyperliquid and a future 2n20 vault. Keep its signals and sizing. Use the starter feed and limit chaser where operating code is missing. Verify paper behavior first.

Use `2n20-setup` when you have a vault. Give your agent the vault URL, project location and machine. It uses the CLI through authorized terminal access; you open the approval link and use your wallet. A project prepared before vault creation is the same existing project in Step 2.

For another client, export a portable guide:

```sh
2n20 skill export --name 2n20-strategy --directory ./strategy-guide
2n20 skill export --name 2n20-setup --directory ./setup-guide
```

Give the exported `SKILL.md` to the client. Execution requires its own authorized terminal and filesystem tools. No MCP server is required for the local coding-agent flow. See [Agent skills](https://2n20.org/docs/agents) for installation options.

After your project's read-only connection and paper checks pass, your agent reports those results with the retained key. The website completes Step 3 automatically. To report the checks yourself:

```sh
2n20 complete-setup --vault '<official vault page URL>' --directory '<retained setup directory>' --connection-checked --paper-checked --json
```

Use the flags only after both checks succeed. This sends a signed public setup report, never your private key or project files. It submits no blockchain transaction and does not enable live trading. Reporting can be retried from the same directory without replacing the key.

## Keep setup and execution separate

Use `setup`, `onboard`, `renew`, `config`, `status`, `check-consent`, `init`, `skill export` and `skill install` without submitting trades or collateral transfers. Keep `strategy.key` private on the strategy machine. Public configuration names its path without embedding the key. Agents must never inspect, print, upload or commit private-key contents. The CLI sets ignore rules and restrictive file permissions before creating a key.

Use paper mode without a signing key. Read its results as simulated fills and P&L. For live execution, the starter uses the official Hyperliquid SDK with the approved trading key as signer and the verified trading account as `account_address`. It does not use the SDK's legacy `vault_address`.

Before a live test, choose the market, side, maximum notional, slippage and time limit. Authorize that plan as a separate action; key approval does not authorize orders. Check usable perpetual collateral, since spot USDC is a separate balance. The starter refuses insufficient margin, unavailable approval, conflicting account state and unsupported account modes. Read the [starter README](https://github.com/2n20/creator-tools/blob/main/src/twon20/templates/hyperliquid-python/README.md) before considering a live plan. You decide whether to install and start a service.

## Documentation and contributions

- [Creator quickstart](https://2n20.org/docs/quickstart)
- [SDK and strategy integration](https://2n20.org/docs/sdks)
- [Contribution and verification](https://github.com/2n20/creator-tools/blob/main/CONTRIBUTING.md)
- [Security reporting](https://github.com/2n20/creator-tools/blob/main/SECURITY.md)
- [Dependency attribution](https://github.com/2n20/creator-tools/blob/main/THIRD_PARTY.md)
- [MIT license](https://github.com/2n20/creator-tools/blob/main/LICENSE)

We maintain the CLI, skills and starter in this public repository. We keep the application and financial contracts in a separate private repository.
