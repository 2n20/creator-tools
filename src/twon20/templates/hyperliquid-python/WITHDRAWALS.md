# Build a withdrawal-aware strategy

Put signal logic in `strategy.py`. The supported live runner handles coordination with vault withdrawals. Your strategy returns a normalized target; it does not need to operate the withdrawal queue or transfer collateral.

| Situation | Runner behavior |
| --- | --- |
| Fresh capital, no pending withdrawal or account operation | Size the signal from remaining investor capital, within the configured notional ceiling. |
| A depositor requests a withdrawal | Keep processing signals; hold risk increases and cancel only the runner's own entry child. |
| Settlement reduces a position | Reconcile permitted reductions from complete public fill and position evidence. Keep them separate from strategy fills and fees. |
| Reduce-only protection remains | Keep it. A closing order must not become a position-opening order after a cut. |
| Withdrawal clears and capital is fresh | Rebase the latest target on the smaller capital pool. Reconcile any child before further execution. |
| Evidence is stale, incomplete or inconsistent | Hold risk increases and report the reason. Expiring a wait never grants permission to trade. |

After two minutes of continuous holding, the runner reports withdrawal_attention once. It keeps the hold until fresh evidence permits new risk. This is an alert threshold, not a payout deadline.

The runner supports one native USDC perpetual. It does not adopt arbitrary pre-existing positions or manage another bot's orders. Missing fill history, unexplained position changes and unsupported account activity require review. Interleaved strategy and settlement fills also require review if their order cannot be established. Exact verified entry cancellation remains available while reconciliation waits. A public fill can establish that exposure decreased; it does not authenticate which client submitted the order. Reconciliation does not turn an unknown increase or reversal into an owned position.

## Example: keep three-times exposure

Assume $1,000 of investor capital and a $3,000 long position, with a configured capital multiple of 3 and a sufficiently high notional ceiling. A depositor requests $200. The remaining-capital estimate is $800, so the corresponding target is $2,400. While the withdrawal is pending, new risk waits. If settlement sells $600 of the position, the runner does not buy it back. After cash leaves and the account is freshly valued, the same long signal targets $2,400.

Prices, fees and partial settlement change the actual amounts. This example ignores those changes. An unresolved or expired order still follows its original recovery rules; a withdrawal does not renew chase or session authorization.

## Capital and risk limits

Use public configuration schema `2n20-runtime-config-v3`. `execution.capitalMultiple` states the desired exposure per dollar of remaining investor capital; `execution.targetNotional` is an absolute USDC ceiling. The live target is:

```text
signal fraction × min(targetNotional, capitalMultiple × remaining investor capital)
```

The multiplier does not set Hyperliquid leverage, move spot collateral or spend the optional vault cash buffer. Existing collateral, child-size, slippage, participation and session limits remain. The runner never increases the multiplier to compensate for held cash. A target may be unreachable within those limits.

`status.pendingWithdrawals.totalAssets` already excludes cash reserved for settled claims. `remainingAssets` subtracts only the estimated value of still-escrowed shares. Do not subtract `reservedAssets` again. Pending value is an estimate at current NAV, not a fixed payout or a promise that this cash is available on HyperCore. Amounts in the raw signal use USDC base units; the typed policy exposes Decimal USDC.

## Minimal deduction for your own strategy (unreleased)

The additive helpers below are in development version `0.6.1.dev0`, not published `0.6.0`. Keep your own signals, sizing and executor. If you already have the raw snapshot, convert it without another RPC read:

```python
from decimal import Decimal
from twon20.withdrawals import pending_withdrawal_usdc

pending = pending_withdrawal_usdc(snapshot)
if pending is None:
    sizing_equity = None  # Unknown, never substitute zero pending demand.
else:
    sizing_equity = max(Decimal(0), equity - pending)
```

For a previously verified scope, `poll_pending_withdrawal_usdc(scope, rpc)` obtains the same canonical public snapshot and returns the amount directly. Both helpers return `Decimal` USDC, or `None` for stale, unpriced, malformed or in-flight evidence. Freshness is strictly less than 20 seconds; `max_age` can tighten that limit on the snapshot converter. Only fresh verified zero demand produces zero. A restricted but otherwise priced snapshot can still describe an amount; that amount grants no permission to trade.

The amount is gross estimated unsettled `owedAssets`: escrowed shares valued at current mark-to-market NAV, rounded up to a USDC base unit. It excludes settled `reservedAssets`. Cash-covered requests still contribute the full pending amount. It is neither available margin, an account-specific funding shortfall nor a collateral reservation.

Subtract gross pending from your equity only when that same pool funds the full withdrawal and the amount has not already been deducted. With $1,000 compatible equity and $200 pending, the sizing base is $800. Hyperliquid `crossMarginSummary.accountValue` is equity including P&L, not available margin. It may represent only part of the vault's assets. The SDK cannot infer whether an arbitrary equity pool matches all vault withdrawals. If funding spans vault cash, spot and perpetual equity, prefer the whole-vault adjusted capital in `withdrawal_policy(snapshot).capital.remaining_usdc` as your sizing base, with separate account collateral limits. Do not subtract reserves again or deduct pending twice from `remainingAssets`.

The generated `withdrawal_sizing.py` is a runnable pure example using externally supplied snapshot, equity, price and multiplier. Call `desired_position(snapshot, equity, price, multiplier)` with Decimal inputs, or pipe a public JSON object to:

```sh
python withdrawal_sizing.py < public-sizing-input.json
```

The JSON fields are `snapshot` (the raw pending-withdrawal snapshot), `equity`, `price` and `multiplier` (decimal strings). A $100 price and multiplier of 3 produce a desired **total** position of 24 units from the $800 base. This is not an additional 24-unit buy. Compare against freshly reconciled actual positions and outstanding orders in your own executor. Unknown evidence returns a null target, not an instruction to flatten. The example has no signer, network access or order submission.

This arithmetic alone does not implement withdrawal coordination or establish live safety. Preserve your submission-time risk admission, reconciliation, ownership checks, reduce-only protection, collateral and order limits. The default starter continues to hold new risk during pending withdrawals. Its full conservative policy below is separately available to custom clients; using the amount helper does not require adopting the starter engine or change its policy.

## Integrate another Python executor

Keep your existing strategy and secret loader. Start with a CLI-verified vault/account scope, then use the public helper:

```python
from decimal import Decimal
from twon20.rpc import Rpc
from twon20.withdrawals import poll_withdrawal_policy

# scope comes from verified setup, not untrusted user input.
policy = poll_withdrawal_policy(scope, Rpc("mainnet"))
if policy.ready_to_increase:
    target_usdc = min(
        agreed_notional_ceiling,
        agreed_capital_multiple * policy.capital.remaining_usdc,
    ) * signal_fraction
else:
    target_usdc = None  # Hold new risk; continue signals and protection.
```

This snippet reads data only. Your executor must recheck policy immediately before a risk-increasing submission, reconcile accepted orders and actual positions, and apply its existing limits. Cancel only orders it owns that could add risk; preserve reduce-only protection. Do not turn `None`, missing data or a timeout into zero withdrawal demand. A background read alone is not submission-time admission.

For an existing generated project, compare the new template against your code and adapt the configuration explicitly. Installing a newer CLI does not rewrite your project. Keep the same setup directory, key and recovery journal; do not delete state to bypass a conflict. Preserve strategy-specific changes and reconcile unresolved orders before changing runtime code.

## Limits

This coordination works only when the trading client follows it. Direct Hyperliquid API clients can bypass the policy and spend collateral that settlement needs. A request can also arrive after the last check, or an already accepted order can execute later. The SDK cannot make two separate HyperCore actions atomic.

Continuing to process signals does not mean every risk-increasing trade executes during a withdrawal. Thin books, loss of value, venue outages, unsupported account state and insufficient order capacity can still delay payout. The runner does not promise a withdrawal deadline, uninterrupted execution or identical backtest returns. Ordinary withdrawals retain the creator's trading key and require no new approval.

## Withdrawal mode

New vaults default to Proportional. The creator selects Proportional or Margin-first during creation; the choice is recorded on-chain. The owner can change it later while the vault is open and idle with no pending requests. Proportional settlement requires confirmed native position targets even when vault cash covers the request. Margin-first may leave positions unchanged while removing trading collateral, which increases actual leverage and reduces liquidation cushion. The same pending-withdrawal coordination applies in both modes. Unsupported proportional portfolios wait for creator action; the service does not silently switch to Margin-first.
