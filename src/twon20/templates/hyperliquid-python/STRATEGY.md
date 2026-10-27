# Write your strategy

Start in `strategy.py`. The runner calls `build_strategy(parameters)` with the `strategy` object from your public configuration. That factory validates your parameters and returns your signal implementation. The included `BandBreakout` shows the interface; replace it with your own logic when ready.

Setup creates no trading process. A connection check verifies account access and market configuration. You choose a separate live start after reviewing its limits.

## Edit and check

1. Put signal logic and parameter validation in `strategy.py`. Keep constructor and evaluation work local, bounded and free of signing or network calls.
2. Update the `strategy` object in your chosen `*.public.json` file. Keep execution limits separate. Pass the same file to `check` and `run`.
3. Run your signal tests with recorded or synthetic bars. Check long, short, flat, insufficient history and restart behavior. Existing backtests can stay in your own project.
4. When a vault is available, run the README's read-only connection check. Finish setup after it passes. Simulation and live testing remain optional.

For agent-assisted development, install the bundled guides with `2n20 skill install --agent codex` from this project, or select your coding agent as described in the package README. Ask the agent to use `2n20-strategy`, preserve your intended signals and sizing, and keep trading stopped. Use the [integration guide](https://github.com/2n20/creator-tools/blob/main/docs/strategy-integration.md) for an existing bot or another exchange.

## Strategy contract

`models.py` defines `Strategy`, `Bar` and `Target`. A strategy exposes `warmup_bars` and `evaluate(bars, checkpoint)`. The runtime supplies a bounded history of validated closed candles for one configured native USDC perpetual and interval. Evaluation also runs while warming up or repairing history; the runtime admits new targets only from fresh contiguous decisions. Return `None` until your inputs suffice.

Evaluation returns `(target, checkpoint)`. Use decimal arithmetic for exposure and prices. Keep the checkpoint JSON-serializable and limited to signal state, such as the prior direction or indicator values. The runtime persists it across restarts. It can retain at most 128 bars; longer or different data needs require a feed/state adaptation.

| Return value for target | Meaning |
| --- | --- |
| `Target(Decimal("1"), "long_signal")` | Full normalized long exposure |
| `Target(Decimal("-1"), "short_signal")` | Full normalized short exposure |
| `Target(Decimal("0.25"), "small_long")` | One quarter of the capped long target |
| `Target(Decimal("0"), "exit_signal")` | Target a flat position |
| `None` | Keep the previous intent, including any outstanding execution |

Reason codes use one to 64 lowercase letters, digits or underscores. A target describes desired exposure, not an instruction to buy that quantity on every bar. Returning the same target does not intentionally open another independent position.

For a structural starting point, the following component remains idle until you add your rules:

```python
class MyStrategy:
    warmup_bars = 1

    def evaluate(self, bars, checkpoint):
        # Add your signal rules and return a Target when they call for a change.
        return None, checkpoint


def build_strategy(parameters):
    if parameters:
        raise ValueError("This strategy has no parameters yet")
    return MyStrategy()
```

Use `"strategy": {}` with this component. Keep the bundled finite `paper()` diagnostic if you use that command; `run --mode paper` exercises your selected strategy through the runtime. Neither proves live fills or profitability.

## What the operating modules do

`feed.py` validates and repairs candles. `runtime.py` owns the checkpoint, converts your target to exposure, and coordinates pending withdrawals. `executor.py` and `runtime_adapter.py` handle bounded orders, account verification, partial fills, cancellation and recovery. See the README's module table before replacing any operating component.

In live mode the unsigned target notional is `min(targetNotional, capitalMultiple * remainingCapital) * abs(fraction)`. The runtime obtains remaining investor capital from current vault evidence and the executor rounds quantity down. Keep this sizing only if it matches your strategy. Fixed coin quantities, independent portfolio weights and external leverage policies need an explicit mapping; do not scale them twice.

During a pending withdrawal, the standard runtime continues signals and holds new risk. It reconciles supported settlement reductions and recalculates exposure from remaining capital. Read [withdrawal coordination](WITHDRAWALS.md) before adapting another execution engine.

The starter supports one native USDC perpetual in standard account mode. Multi-market, spot, HIP-3, borrowing and alternate account modes need a separate compatibility assessment. Changing a signal cannot make an unsupported portfolio compatible.

## Change a configured project

Keep the project, retained setup key and runtime journals. Edits to `strategy.py` or public configuration invalidate a retained runtime policy. The fingerprint does not cover modules your strategy imports; review those changes and reconcile exposure before resuming as well. Reconcile orders and exposure before a reviewed migration; `--new-session` renews an unchanged policy and does not migrate it. Never delete a journal to bypass that check. Session expiry stops new orders and does not automatically flatten an open position.
