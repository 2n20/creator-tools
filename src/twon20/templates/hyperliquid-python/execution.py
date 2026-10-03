"""One bounded, explicitly authorized round trip with durable public intent."""
from decimal import Decimal
import re
import secrets
import time

from risk import Stop, close_plan, entry_plan, number

SMOKE = "smoke.public.json"
COLLATERAL = "collateral.public.json"
CLOID = re.compile(r"0x[0-9a-f]{32}\Z")


def report(stage, journal, name, **details):
    return {"schema": "2n20-runner-result-v1", "stage": stage,
            "journal": journal.public_path(name), "scope": journal.scope,
            "strategyStarted": False, **details}


def validate_intent(intent, bounds):
    allowed = {"schema", "scope", "bounds", "phase", "startedMs", "entryCloid", "entry", "filled", "close", "closeCloid", "entrySubmission", "closeSubmission"}
    if set(intent) - allowed or intent.get("bounds") != bounds.public() or intent.get("phase") not in {"entry_pending", "entry_confirmed", "close_pending", "complete", "entry_rejected"} or type(intent.get("startedMs")) is not int or not CLOID.fullmatch(intent.get("entryCloid", "")):
        raise Stop("JOURNAL_CONFLICT", "The existing intent has different limits or unsupported state. Keep the journal; never create another entry to resolve it.")
    plan = intent.get("entry")
    if not isinstance(plan, dict) or plan.get("market") != bounds.market or plan.get("buy") is not (bounds.side == "buy") or plan.get("reduceOnly") is not False or number(plan.get("size")) <= 0 or number(plan.get("price")) <= 0 or number(plan["size"]) * number(plan["price"]) > bounds.max_notional:
        raise Stop("JOURNAL_CONFLICT", "The stored entry does not match its explicit limits.")
    for field in ("entrySubmission", "closeSubmission"):
        submission = intent.get(field)
        if submission is not None and (not isinstance(submission, dict) or set(submission) - {"classification", "code"} or submission.get("classification") not in {"ambiguous", "rejected", "accepted_unverified"} or submission.get("code") not in {None, "ORDER_REJECTED", "EXCHANGE_REJECTED"}):
            raise Stop("JOURNAL_CONFLICT", "The retained submission classification is unsupported.")
    if intent.get("phase") in {"close_pending", "complete"}:
        close = intent.get("close")
        if not CLOID.fullmatch(intent.get("closeCloid", "")) or not isinstance(close, dict) or close.get("market") != bounds.market or close.get("buy") is not (bounds.side == "sell") or close.get("reduceOnly") is not True or number(close.get("size")) != number(intent.get("filled")) or not 0 < number(intent.get("filled")) <= number(plan["size"]):
            raise Stop("JOURNAL_CONFLICT", "The closing intent does not match the runner's confirmed fill.")


def smoke(adapter, bounds, journal, resume_close=False, clock=time.time, sleep=time.sleep):
    intent = journal.read(SMOKE)
    resumed = intent is not None
    if intent:
        validate_intent(intent, bounds)
        if intent["phase"] in {"complete", "entry_rejected"}:
            adapter.verify_activity([intent["entryCloid"], *([intent["closeCloid"]] if intent["closeCloid"] else [])], intent["startedMs"])
            snapshot = adapter.snapshot(bounds.market)
            # A complete journal does not authorize another entry or pretend
            # another process's new exposure is this runner's position.
            entry = adapter.order_evidence(intent["entryCloid"], intent["entry"], intent["startedMs"])
            verified = not snapshot["positions"] and not snapshot["orders"] and entry["known"] and entry["terminal"]
            if intent["phase"] == "complete":
                close = adapter.order_evidence(intent["closeCloid"], intent["close"], intent["startedMs"])
                verified = verified and close["known"] and close["terminal"] and number(entry["filled"]) == number(intent["filled"]) == number(close["filled"])
            else:
                verified = verified and number(entry["filled"]) == 0
            return report(intent["phase"] if verified else "historical_completion", journal, SMOKE, ordersSubmitted=0,
                          currentCompletionVerified=verified, accountCurrentlyFlat=not snapshot["positions"] and not snapshot["orders"],
                          nextAction="Keep the retained journal. Current completion needs matching public fills and a flat account; never start another experiment to resolve unavailable or changed evidence.")
        if not resume_close and intent["phase"] == "entry_confirmed":
            return report("recovery_required", journal, SMOKE, ordersSubmitted=0,
                          nextAction="Review the confirmed entry. Repeat this exact command with --execute LIVE_SMOKE --resume-close to authorize its first reduction only.")
    else:
        if resume_close:
            raise Stop("RECOVERY_MISSING", "There is no existing intent to recover. Remove --resume-close only when you deliberately want a new reviewed entry.")
        snapshot = adapter.snapshot(bounds.market)
        plan = entry_plan(bounds, snapshot)
        intent = {"bounds": bounds.public(), "phase": "entry_pending", "startedMs": int(clock() * 1000),
                  "entryCloid": "0x" + secrets.token_hex(16), "entry": plan,
                  "filled": "0", "close": None, "closeCloid": None}
        journal.save(SMOKE, intent)  # Durable before private key read or SDK call.
    deadline = clock() + bounds.duration_seconds
    submitted = 0
    if not resumed:
        intent["entrySubmission"] = adapter.submit_order(intent["entry"], intent["entryCloid"], int(deadline * 1000))
        journal.save(SMOKE, intent)
        submitted += 1
    delay = 1
    while clock() < deadline:
        # Every iteration refreshes CLI approval/account authority. Revocation
        # or unavailable evidence stops with durable intent still retained.
        snapshot = adapter.snapshot(bounds.market)
        if intent["phase"] == "entry_pending":
            adapter.verify_activity([intent["entryCloid"]], intent["startedMs"])
            evidence = adapter.order_evidence(intent["entryCloid"], intent["entry"], intent["startedMs"])
            if not evidence["known"] and intent.get("entrySubmission", {}).get("classification") == "rejected":
                return report("submission_rejected", journal, SMOKE, ordersSubmitted=submitted,
                              submissionCode=intent["entrySubmission"]["code"],
                              accountCurrentlyFlat=not snapshot["positions"] and not snapshot["orders"],
                              nextAction="The SDK returned an explicit rejection. Current account state was checked; fills remain unverified by order ID. Review precision, minimum notional and collateral. This durable intent will not be resubmitted.")
            if evidence["known"] and evidence["terminal"]:
                filled = number(evidence["filled"])
                if not filled:
                    if snapshot["positions"] or snapshot["orders"]:
                        raise Stop("EXPOSURE_CONFLICT", "The rejected entry has conflicting current account state. No unrelated state will be modified.")
                    intent["phase"] = "entry_rejected"
                    journal.save(SMOKE, intent)
                    return report("entry_rejected", journal, SMOKE, ordersSubmitted=submitted,
                                  confirmedFilled="0", nextAction="The entry was rejected or had no fills. Review the public journal; this intent will never be resubmitted.")
                close_plan(bounds, snapshot, filled)  # Prove exact own exposure.
                intent.update(phase="entry_confirmed", filled=str(filled))
                journal.save(SMOKE, intent)
                if resumed and not resume_close:
                    return report("recovery_required", journal, SMOKE, ordersSubmitted=0, confirmedFilled=str(filled),
                                  nextAction="The retained entry is now confirmed. Review it and repeat the exact command with --execute LIVE_SMOKE --resume-close to authorize its first reduction.")
        if intent["phase"] == "entry_confirmed":
            adapter.verify_activity([intent["entryCloid"]], intent["startedMs"])
            evidence = adapter.order_evidence(intent["entryCloid"], intent["entry"], intent["startedMs"])
            if not evidence["known"] or not evidence["terminal"] or number(evidence["filled"]) != number(intent["filled"]):
                raise Stop("ENTRY_RECONCILIATION_REQUIRED", "The original entry ID/fills do not revalidate the retained amount. No reduction was authorized by journal contents alone.")
            # One IOC entry and at most one IOC reduction. Never increase the
            # amount to solve a dust rejection or partially filled close.
            plan = close_plan(bounds, adapter.snapshot(bounds.market), intent["filled"])
            if clock() >= deadline:
                break
            intent.update(phase="close_pending", close=plan, closeCloid="0x" + secrets.token_hex(16))
            journal.save(SMOKE, intent)
            intent["closeSubmission"] = adapter.submit_order(plan, intent["closeCloid"], int(deadline * 1000))
            journal.save(SMOKE, intent)
            submitted += 1
        if intent["phase"] == "close_pending":
            adapter.verify_activity([intent["entryCloid"], intent["closeCloid"]], intent["startedMs"])
            evidence = adapter.order_evidence(intent["closeCloid"], intent["close"], intent["startedMs"])
            snapshot = adapter.snapshot(bounds.market)
            if not evidence["known"] and intent.get("closeSubmission", {}).get("classification") == "rejected":
                return report("unresolved_exposure", journal, SMOKE, ordersSubmitted=submitted,
                              submissionCode=intent["closeSubmission"]["code"], confirmedEntry=str(intent["filled"]),
                              remainingExposureVerified=False,
                              nextAction="The first reduction was explicitly rejected, potentially due to a small partial fill. Review current exposure yourself. Never increase size or resend a reduction blindly.")
            if evidence["known"] and evidence["terminal"]:
                closed = number(evidence["filled"])
                remaining = number(intent["filled"]) - closed
                expected = remaining * (1 if bounds.side == "buy" else -1)
                if set(snapshot["positions"]) - {bounds.market} or snapshot["positions"].get(bounds.market, Decimal(0)) != expected or snapshot["orders"]:
                    raise Stop("EXPOSURE_CONFLICT", "Current state differs from confirmed runner fills. Review recovery; no additional order will be sent.")
                if remaining == 0:
                    intent["phase"] = "complete"
                    journal.save(SMOKE, intent)
                    return report("complete", journal, SMOKE, ordersSubmitted=submitted,
                                  confirmedEntry=str(intent["filled"]), confirmedClosed=str(closed), remainingExposure="0",
                                  nextAction="The public fills and flat account were verified. No strategy or service was started.")
                return report("unresolved_exposure", journal, SMOKE, ordersSubmitted=submitted,
                              confirmedEntry=str(intent["filled"]), confirmedClosed=str(closed), remainingExposure=str(remaining),
                              nextAction="The first reduction was rejected or partial. Review remaining exposure in Hyperliquid. This runner will not send another order, increase the budget or touch unrelated state.")
        remaining_time = deadline - clock()
        if remaining_time <= 0: break
        sleep(min(delay, remaining_time))
        delay = min(delay * 2, 4)
    return report("unresolved_evidence", journal, SMOKE, ordersSubmitted=submitted,
                  confirmedEntry=str(intent.get("filled", "0")), readinessVerified=False,
                  nextAction="The bounded wait ended. Preserve the journal and repeat the same command to reconcile only. unknownOid or a lost response never authorizes another entry. A first reduction after restart additionally requires --resume-close.")


def collateral_plan(snapshot, amount):
    amount = number(amount)
    if not 0 < amount <= 100 or amount.quantize(Decimal("0.000001")) != amount:
        raise Stop("INVALID_TRANSFER_AMOUNT", "Choose an explicit amount above zero and at most 100 USDC with at most six decimal places.")
    if snapshot["positions"] or snapshot["orders"]:
        raise Stop("ACCOUNT_CONFLICT", "Existing orders or positions require your review. No transfer will be made.")
    if amount >= number(snapshot["spot_available"]):
        raise Stop("SPOT_COLLATERAL_REQUIRED", "Available spot USDC must exceed the explicit amount. This example never transfers the entire balance, deposits funds or increases a budget.")
    return {"amount": str(amount), "source": "spot", "destination": "native USDC perpetual collateral",
            "spotBefore": str(snapshot["spot_available"]), "perpBefore": str(snapshot["perp_available"])}


def validate_collateral_intent(intent, amount):
    allowed = {"schema", "scope", "amount", "source", "destination", "spotBefore", "perpBefore", "phase", "nonce", "token", "humanAcknowledgement", "acknowledgedAt"}
    if set(intent) - allowed or intent.get("amount") != str(number(amount)) or intent.get("phase") not in {"pending", "balance_observed", "human_acknowledged"} or type(intent.get("nonce")) is not int or intent.get("source") != "spot" or intent.get("destination") != "native USDC perpetual collateral" or not re.fullmatch(r"USDC:0x[0-9a-fA-F]{32}", intent.get("token", "")):
        raise Stop("JOURNAL_CONFLICT", "The retained transfer has a different amount or unsupported state. Never send another transfer to resolve ambiguity.")
    if not 0 < number(intent["amount"]) < number(intent["spotBefore"]) or number(intent["amount"]) > 100:
        raise Stop("JOURNAL_CONFLICT", "The retained transfer exceeds its original explicit balance limit.")
    if intent["phase"] == "human_acknowledged" and (intent.get("humanAcknowledgement") != "UNVERIFIED_COLLATERAL" or type(intent.get("acknowledgedAt")) is not int):
        raise Stop("JOURNAL_CONFLICT", "The recorded human recovery acknowledgement is invalid.")


def review_collateral(adapter, journal, market, acknowledge=False, request_nonce=None):
    """Only public reads and an explicit local decision, never an SDK action."""
    intent = journal.read(COLLATERAL)
    if intent is None:
        raise Stop("RECOVERY_MISSING", "This state directory has no retained collateral request to review.")
    validate_collateral_intent(intent, intent.get("amount"))
    if acknowledge and (type(request_nonce) is not int or request_nonce != intent["nonce"]):
        raise Stop("RECOVERY_INTENT_MISMATCH", "Acknowledge only the exact public request nonce shown in the recovery review. No journal or registry was cleared.")
    if intent["phase"] == "pending":
        raise Stop("RECOVERY_PENDING", "The collateral request still lacks matching observed balances. Reconcile its exact original command; this review cannot resend it or silently reset the account registry.")
    financial = journal.read(SMOKE)
    if financial is not None:
        if financial.get("phase") not in {"complete", "entry_rejected"}:
            raise Stop("OTHER_INTENT_UNRESOLVED", "The retained smoke order is unresolved. Reconcile that original intent before acknowledging collateral recovery.")
        limits = financial.get("bounds", {})
        from risk import Bounds
        limits = Bounds(limits["market"], limits["side"], number(limits["maxNotional"]), limits["slippageBps"], limits["durationSeconds"])
        verified = smoke(adapter, limits, journal)
        if verified["stage"] not in {"complete", "entry_rejected"}:
            raise Stop("FINANCIAL_RECOVERY_UNVERIFIED", "The retained order's fills and flat completion could not be reverified. No registry was cleared.")
    snapshot = adapter.snapshot(market)
    if snapshot["positions"] or snapshot["orders"]:
        raise Stop("ACCOUNT_CONFLICT", "Current account exposure or orders require your review. A recovery acknowledgement cannot modify them.")
    if acknowledge:
        intent.update(phase="human_acknowledged", humanAcknowledgement="UNVERIFIED_COLLATERAL", acknowledgedAt=int(time.time()))
        journal.save(COLLATERAL, intent)
    return report("recovery_acknowledged" if acknowledge else "recovery_review", journal, COLLATERAL,
                  ordersSubmitted=0, transfersSubmitted=0, signerLoaded=False, transferIdentityVerified=False,
                  spotUsdcAvailable=str(snapshot["spot_available"]), perpUsdcAvailable=str(snapshot["perp_available"]),
                  collateralIntent={"nonce": intent["nonce"], "amount": intent["amount"], "source": "spot",
                                    "destination": "native USDC perpetual collateral", "tradingAccount": journal.scope["tradingAccount"]},
                  humanAcknowledgementRequired=not acknowledge,
                  nextAction="This explicit human decision accepts that the individual transfer identity remains unverified. No action was resent; any new experiment needs separate authorization." if acknowledge else "Review the retained public request and current balances yourself. Only --acknowledge UNVERIFIED_COLLATERAL --request-nonce <shown nonce> records your residual-risk decision and releases the account reservation; it never verifies or resends a transfer.")


def collateral(adapter, journal, amount, market, clock=time.time, sleep=time.sleep):
    intent = journal.read(COLLATERAL)
    submitted = False
    if intent:
        validate_collateral_intent(intent, amount)
        if intent["phase"] in {"balance_observed", "human_acknowledged"}:
            return report("balance_change_observed", journal, COLLATERAL, transfersSubmitted=0, transferIdentityVerified=False,
                          nextAction="Matching balance changes were previously observed. They do not identify a specific transfer. Run the read-only check; this command will not repeat it.")
    else:
        intent = collateral_plan(adapter.snapshot(market), amount)
        intent.update(phase="pending", nonce=int(clock() * 1000), token=adapter.usdc_token())
        journal.save(COLLATERAL, intent)
        adapter.submit_collateral(number(amount), intent["nonce"], intent["token"], market)
        submitted = True
    deadline, delay = clock() + 30, 1
    while clock() < deadline:
        snapshot = adapter.snapshot(market)
        if snapshot["positions"] or snapshot["orders"]:
            raise Stop("ACCOUNT_CONFLICT", "Account activity appeared during transfer verification. Review it yourself; no further transfer or order will be sent.")
        # No response, balance increase alone or web success is completion.
        if number(snapshot["spot_available"]) == number(intent["spotBefore"]) - number(amount) and number(snapshot["perp_available"]) == number(intent["perpBefore"]) + number(amount):
            intent["phase"] = "balance_observed"
            journal.save(COLLATERAL, intent)
            return report("balance_change_observed", journal, COLLATERAL, transfersSubmitted=int(submitted),
                          amount=str(number(amount)), balanceChangeVerified=True, transferIdentityVerified=False,
                          nextAction="Matching spot/perpetual balance changes were observed, without proof of a specific transfer identity. Run the read-only check. No order was authorized by this transfer.")
        sleep(min(delay, max(0, deadline - clock())))
        delay = min(delay * 2, 4)
    return report("unresolved_evidence", journal, COLLATERAL, transfersSubmitted=int(submitted),
                  balanceChangeVerified=False, nextAction="Preserve the journal and repeat this exact command to read balances only. Never repeat an ambiguous collateral submission.")
