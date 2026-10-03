# 2n20 creator tools

Official integration tooling for [2n20](https://2n20.org): the `2n20` Python CLI, a portable `2n20-setup` agent skill and a small Hyperliquid strategy starter. The Python import is `twon20`.

Connect an existing strategy or generate the educational starter. Run setup on the computer or server that will operate it. The CLI verifies public deployment details, retains a trading key locally and returns a public approval link. Open that link yourself, review the request and approve with your creator wallet. Resume the same setup directory to verify access and obtain public strategy settings.

Creators define and operate their strategies. The moving-average example is educational, with no profitability claim. This repository does not host trading software or operate your account.

## Build and install the reviewed source

Use Python 3.10 or later. Build the exact reviewed source with the pinned dependency lock:

```sh
git clone https://github.com/2n20/creator-tools.git
cd creator-tools
python3 -m venv .venv
.venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-release.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
SOURCE_DATE_EPOCH=1790985600 .venv/bin/python scripts/build_release.py
.venv/bin/python -m pip --isolated install --no-deps dist/*.whl
.venv/bin/2n20 --version
source .venv/bin/activate
```

The CLI dependency lock excludes the Hyperliquid execution SDK. Each generated starter has a separate reviewed runner lock. [Release instructions](https://github.com/2n20/creator-tools/blob/main/docs/releases.md) describe artifact verification and Trusted Publishing.

## Start or connect a project

Generate a new project in a directory that does not exist:

```sh
2n20 init my-strategy --template hyperliquid-python
```

Generation copies bundled, versioned assets. It does not install dependencies, create keys, move collateral or start a process. Follow the exact commands it prints. The canonical template is [src/twon20/templates/hyperliquid-python](https://github.com/2n20/creator-tools/tree/main/src/twon20/templates/hyperliquid-python); it is also available to inspect in a public checkout. Generated projects do not update automatically.

For an existing strategy, continue in its authorized project directory. Never search unrelated projects or read their credentials to discover access.

```sh
2n20 onboard --vault '<official vault page URL>' --json
```

Save the returned `resumeCommand` and `approvalUrl`. On a VPS, open the link on your wallet computer. The link locates a signed public request; opening it cannot authorize a transaction. After human approval, run the exact resume command with the same explicit setup directory. The CLI reports current matching contract approval separately from verified HyperCore activation, funding and unavailable evidence.

Repeated setup preserves the existing key. If consent expires or its nonce changes, follow the CLI's same-key renewal instruction. A conflicting requested or approved key needs an explicit recovery decision. Do not generate another key to bypass a failed check. If the handoff service is unavailable, import only the exact public consent file printed by the CLI into the vault's Step 2 and resume the same directory. Renewal writes a new public filename while retaining earlier files and the private key.

## Use your coding agent

Export the bundled skill explicitly:

```sh
2n20 skill export --directory '<new skill directory>'
```

Give your agent the exported `SKILL.md`, your vault URL and your strategy project location, or ask it to generate the official starter in a new directory. Specify where the strategy runs and which terminal/filesystem access is authorized. Pip installation does not modify agent configuration directories. The skill works through the CLI and authorized terminal access; it needs no browser automation.

For Codex and [Cursor](https://cursor.com/docs/skills), create the project's `.agents/skills` parent directory, then export to an unused `.agents/skills/2n20-setup` directory. For [Claude Code](https://code.claude.com/docs/en/skills), use an unused project `.claude/skills/2n20-setup` directory. User-wide installation is also possible in a supported harness's user skill directory. Review the exported guide before invoking `2n20-setup`. Existing skill directories are never overwritten.

## Keep setup and execution separate

`setup`, `onboard`, `renew`, `config`, `status`, `check-consent`, `init` and `skill export` submit no trades or collateral transfers. A setup directory contains `strategy.key`, which is private and stays on the strategy machine. Public configuration refers to that path without embedding its contents. Agents must never inspect, print, upload or commit private-key contents. Ignore rules and restrictive file permissions are established before key creation.

The starter uses the official Hyperliquid SDK with the approved trading key as signer and the verified trading account as `account_address`. It does not use the SDK's legacy `vault_address`. Paper mode needs no signing key and reports simulated results separately from exchange evidence.

Live execution is a separate bounded action with explicit market, side, notional, slippage and time limits. Approval of a key does not authorize orders. Available spot USDC is not perpetual collateral. Insufficient margin, unavailable approval, conflicting account state or unsupported account mode blocks live execution. See the [starter README](https://github.com/2n20/creator-tools/blob/main/src/twon20/templates/hyperliquid-python/README.md) before considering a live plan. No service is started automatically.

## Documentation and contributions

- [Creator quickstart](https://2n20.org/docs/quickstart)
- [SDK and strategy integration](https://2n20.org/docs/sdks)
- [Contribution and verification](https://github.com/2n20/creator-tools/blob/main/CONTRIBUTING.md)
- [Security reporting](https://github.com/2n20/creator-tools/blob/main/SECURITY.md)
- [Dependency attribution](https://github.com/2n20/creator-tools/blob/main/THIRD_PARTY.md)
- [MIT license](https://github.com/2n20/creator-tools/blob/main/LICENSE)

Only public tooling is maintained here. The private application, financial contracts and their release history are separate.
