# 2n20 creator tools

Connect your strategy software to a [2n20](https://2n20.org) vault on Hyperliquid. Use your existing project or start with the included Python example.

Give your coding agent the `2n20-setup` skill, or run the `2n20` CLI yourself. Run setup on the computer or server that will operate your strategy. You receive an approval link to review with your creator wallet. Your private trading key stays on the strategy machine.

After approval, resume the same setup directory to verify trading access and get your strategy settings. You define and operate the strategy. The starter's moving-average example teaches the integration; it makes no profitability claim. The Python import is `twon20`.

## Install

Use Python 3.10 or later on your strategy computer or server:

```sh
python3 -m venv .venv-2n20
source .venv-2n20/bin/activate
python -m pip --isolated install '2n20==0.3.1'
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

If the handoff service is unavailable, import the exact public consent file printed by the CLI into the vault's Step 2. Resume the same directory. Renewal creates a new public file and retains the earlier files and private key.

## Use your coding agent

Export the bundled skill:

```sh
2n20 skill export --directory '<new skill directory>'
```

Give your agent the exported `SKILL.md`, your vault URL and your strategy project location. If you need a project, ask it to generate the official starter in a new directory. Tell it where the strategy will run and which terminal/filesystem access you authorize. Your agent uses the CLI through that access; you open the approval link and use your wallet.

For Codex and [Cursor](https://cursor.com/docs/skills), create the project's `.agents/skills` parent directory, then export to an unused `.agents/skills/2n20-setup` directory. For [Claude Code](https://code.claude.com/docs/en/skills), use an unused project `.claude/skills/2n20-setup` directory. You can use your agent application's user skill directory for a user-wide installation. Review the exported guide before invoking `2n20-setup`. Pip installation leaves agent configuration directories untouched; export refuses an existing directory.

## Keep setup and execution separate

Use `setup`, `onboard`, `renew`, `config`, `status`, `check-consent`, `init` and `skill export` without submitting trades or collateral transfers. Keep `strategy.key` private on the strategy machine. Public configuration names its path without embedding the key. Agents must never inspect, print, upload or commit private-key contents. The CLI sets ignore rules and restrictive file permissions before creating a key.

Use paper mode without a signing key. Read its results as simulated fills and P&L. For live execution, the starter uses the official Hyperliquid SDK with the approved trading key as signer and the verified trading account as `account_address`. It does not use the SDK's legacy `vault_address`.

Before a live test, choose the market, side, maximum notional, slippage and time limit. Authorize that plan as a separate action; key approval does not authorize orders. Check usable perpetual collateral, since spot USDC is a separate balance. The starter refuses insufficient margin, unavailable approval, conflicting account state and unsupported account modes. Read the [starter README](https://github.com/2n20/creator-tools/blob/main/src/twon20/templates/hyperliquid-python/README.md) before considering a live plan. You decide whether to install and start a service.

## Documentation and contributions

- [Creator quickstart](https://2n20.org/docs/quickstart)
- [SDK and strategy integration](https://2n20.org/docs/sdks)
- [Contribution and verification](https://github.com/2n20/creator-tools/blob/main/CONTRIBUTING.md)
- [Security reporting](https://github.com/2n20/creator-tools/blob/main/SECURITY.md)
- [Dependency attribution](https://github.com/2n20/creator-tools/blob/main/THIRD_PARTY.md)
- [MIT license](https://github.com/2n20/creator-tools/blob/main/LICENSE)

We maintain the CLI, skill and starter in this public repository. We keep the application and financial contracts in a separate private repository.
