# Release procedure

The maintained source is 2n20/creator-tools. Distribution `2n20`, import `twon20` and executable `2n20` retain their identity. Old published artifacts and tags are immutable. The portable skills and canonical Hyperliquid template ship inside both wheel and sdist. Execution dependencies belong to the template's separate requirements-runner.txt.

## Verification

Review dependencies and their licenses before changing either hash lock. Record exact registry file SHA-256 digests and upload timestamps in dependency-review.json. New third-party versions and files must be at least 14 days old. Use local built artifacts during development.

```sh
python3 -m venv .venv
.venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-release.txt
.venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r src/twon20/templates/hyperliquid-python/requirements-runner.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
cd src/twon20/templates/hyperliquid-python
PYTHONPATH=../../.. ../../../../.venv/bin/python -m unittest discover -s tests -v
cd ../../../..
SOURCE_DATE_EPOCH=1790985600 .venv/bin/python scripts/build_release.py
.venv/bin/python -m pip --isolated install --no-deps dist/*.whl
.venv/bin/python scripts/check_release.py
```

Build twice with the same SOURCE_DATE_EPOCH and compare hashes. Install the sdist separately outside the checkout. Verify consent interoperability, existing setup-directory resume, failure recovery, explicit skill export and installation, conflict preservation, template completeness, generation overwrite refusal, deterministic paper runtime and read-only diagnostics. Check candle recovery, ambiguous child orders, partial fills, restarts, target replacement, deadline/stop cleanup and foreign-account activity with bounded fixtures. Execution tests use mocks/disposable fixtures only. Inspect archive and staged Git paths for secrets, generated state/logs and unrelated material. Do not contact a real wallet or send orders/transfers as a test.

## Trusted Publishing

The setup skill includes `references/step-2.md` and `references/step-3.md`. Preserve the approved instruction text during delivery changes. Release 0.5.0 implements the founder-approved connection-only completion and optional live test, so its reviewed guide changes remove the paper requirement while retaining the two-copy wallet handoff and native questions. `test_setup_guides.py` checks its recorded digests, native question instructions and installed/exported reference files. Any change to these guides needs an explicit behavior review; do not update the digests just to make a delivery change pass.

Configure an existing-project GitHub publisher on PyPI, without an API token:

| Setting | Exact value |
| --- | --- |
| PyPI project | `2n20` |
| GitHub owner | `2n20` |
| Repository | `creator-tools` |
| Workflow filename | `creator-cli-publish.yml` |
| Environment | `creator-cli-pypi` |

The GitHub environment accepts only release tags matching `creator-cli-v*`. Increment the package version, complete review and tests, commit, push and create a new matching immutable tag. Dispatch the workflow against that tag:

```sh
gh workflow run creator-cli-publish.yml --repo 2n20/creator-tools --ref creator-cli-v<VERSION>
```

The SHA-pinned workflow builds/tests artifacts, then gives OIDC permission only to the dedicated publishing job. Verify PyPI's actual version, downloaded file hashes and attestations. Publisher identity must name this repository, workflow, environment, release tag and tested source commit. Attach those exact verified files to the matching GitHub release.

Only then update website installation instructions to the published version and deploy through the application's established process. Retire an obsolete publisher only after the new release is verified. Do not move older tags or delete historical release evidence. TestPyPI is optional and was not used for the initial public-source transition. Record skipped checks separately from passing verification.

## Unreleased

Discovery requires trading-key version 4. It reads `books(address)`, `keys(address)`, `releases(address)`, `strikes(address)` and `cutsAllowedAt(address)`. `books(address)` returns 16 words: core, phase, stateNonce, reportNonce, observedAt, restrictedAt, restricted, recovered, priced, unpriced, coreAssets, pendingAssets, evmBaseline, exposure, orders, reportHash. Discovery rejects the read unless `unpriced` is at most 511 and names a reason exactly when `priced` is false. A book no report has reached is the exception: its `reportNonce` is 0, `priced` is false and `unpriced` is 0. `releases(address)` returns 10 words: roundAt, need, released, captured, opened, executed, pending, pendingMargin, cancelled, noticeAt. `strikes(address)` returns misses and lastMissAt. `cutsAllowedAt(address)` returns the earliest time a round may reduce positions again. Zero, or a time already passed, means the next round may reduce positions. `RELEASE_NOTICE()` returns 120 and `RELEASE_ROUND()` returns 600. `consentDigest(address,address,uint64,uint64)` is unchanged. Its EIP-712 domain name is `2n20 SDK venue` and its version is `2`. `keys(address)` is unchanged.

`status` reports whether a withdrawal release is active, `noticeAt`, `roundSeconds`, `openedThisRound`, `misses`, `lastMissAt` and `cutsAllowedAt`. `misses` is a count. The key stays approved. The report names each unpriced reason. Before the first valuation report, `pricing.action` is `unreported`. After wind-down, `pricing.next` says that neither the vault nor the service can undo a state only the strategy could. A missing price or an unfinished account read waits for data. During a release, or while no trading key is active, the venue restores standard account mode and withdraws Hyperliquid vault equity after its lockup. Staked HYPE, sub-accounts, borrowing, lending, spot debt, builder-deployed market collateral without a USDC price, and any other unpriced state need the strategy to undo them. Wind-down removes the key, and after that neither the vault nor the service can undo them: while one lasts, the account stays unpriced, no request settles, including requests the vault's cash covers, and wind-down cannot complete. The vault refuses to start wind-down unless a valuation report from the last 45 seconds reads the whole account and shows none of these states. The key keeps working until Hyperliquid removes it, so a strategy that keeps trading can still set one in between. While the account is unpriced, holders can still cancel the unsettled part of a request and claim settled withdrawals, and holders other than the owner can request withdrawals. Deposits, share transfers, settlement and the owner's withdrawal requests wait. The report includes uncovered assets and key `validUntil`, and it warns when expiry is inside 14 days. During a release the strategy keeps its key and needs no new approval, and the creator can approve a key at most once every 10 minutes. The key stays approved through an ordinary withdrawal. After expiry the creator approves a fresh key. A used key cannot be approved again.

Each round may cancel up to 8 resting orders that are not reduce-only, including orders placed after the notice, and reduce up to 5 holdings. Each reduction stays within 0.5% of the mark. The service sizes each reduction to that holding's share of what free collateral lacks of the shortfall. The contract allows up to twice that share, or 10.15 USDC when that is larger. The service leaves reduce-only orders open. The contract cancels any order id the operator lists. Rounds start two minutes after a shortfall, then once every 10 minutes. A shortfall that returns after the vault had enough cash starts a new two-minute notice, and the vault holds cancels and reductions until it ends, even inside a round already open. After two missed rounds, later reductions wait. Each further miss doubles the wait, up to one day. Free collateral still moves during the wait. The key stays approved.

The starter's authorized live runtime reads that venue state apart from its trading loop, and new risk waits while that read fails or goes stale. While the vault is restricted it stops opening new risk, cancels its open entry order and leaves reduce-only orders in place, including take-profit and stop-loss orders marked reduce-only. The vault moves free collateral itself. A strategy you write yourself should do the same. Use standard account mode. Do not deposit into a Hyperliquid vault, stake HYPE, open a sub-account, borrow, lend, or hold builder-deployed market collateral without a USDC price. Keep at least 0.01 HYPE in the trading account for Core transfer fees. Use client order ids of 2^64 or more. The vault's reduction orders use the operation nonce shifted left by 8 bits, plus the reduction's index in that round. An order under one of those ids counts as that reduction. In the transactions observed so far, none of which mixed orders with transfers, Hyperliquid ran a transaction's actions in order, 5 or 6 L1 blocks after its HyperEVM block (about 0.3 to 0.4 seconds). Hyperliquid documents only that CoreWriter order actions and vault transfers wait a few seconds. It does not document that the actions stay in that order, or that one failed action leaves the later ones running. A reduction can fill while the transfer after it fails. A thin book can leave a reduction unfilled. The package version is unchanged.
