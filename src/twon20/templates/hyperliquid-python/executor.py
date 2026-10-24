"""Incremental maker-first execution. One child identity survives every ambiguity."""
import copy
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import hashlib

from models import validate_market
from risk import Stop, number


def execution_state(now_ms):
    return {"active": None, "pending": None, "child": None, "phase": "ready", "position": "0",
            "activitySince": now_ms, "ownedOids": [], "sequence": 0, "childCount": 0, "filledSize": "0", "filledValue": "0",
            "fees": "0", "makerFills": 0, "takerFills": 0, "error": None}


def plan_child(intent, state, snapshot, limits, now_ms):
    validate_market(intent, snapshot)
    position, target = number(state["position"], signed=True), number(intent["quantity"], signed=True)
    delta = target - position
    if not delta:
        return None
    buy = delta > 0
    reducing = position != 0 and delta * position < 0
    residual = min(abs(delta), abs(position)) if reducing else abs(delta)
    urgent = limits["iocAfterSeconds"] is not None and now_ms - intent["createdMs"] >= limits["iocAfterSeconds"] * 1000
    near = snapshot["ask"] if (buy and urgent) or (not buy and not urgent) else snapshot["bid"]
    reference = number(intent["arrivalPrice"])
    bound = reference * (1 + Decimal(limits["slippageBps"]) / 10000 if buy else 1 - Decimal(limits["slippageBps"]) / 10000)
    if near <= 0 or (buy and near > bound) or (not buy and near < bound):
        raise Stop("PRICE_BOUND", "The market moved beyond the retained arrival-price bound. No child is authorized.")
    # IOC needs a marketable limit throughout the permitted slippage range;
    # pinning it to a sampled best quote makes small moves cause zero fills.
    quote = bound if urgent else near
    decimals = snapshot["decimals"]
    places = max(0, min(6 - decimals, 4 - quote.adjusted()))
    price = quote.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN if buy else ROUND_UP)
    if price <= 0 or (buy and price > bound) or (not buy and price < bound):
        raise Stop("PRICE_BOUND", "No valid tick fits the retained arrival-price bound.")
    max_notional = number(limits["childNotional"])
    caps = [max_notional]
    if limits.get("depthFraction") is not None:
        depth = snapshot.get("askDepth" if (buy and urgent) or (not buy and not urgent) else "bidDepth")
        if depth is None:
            raise Stop("DEPTH_UNAVAILABLE", "Configured depth participation requires verified public depth.")
        caps.append(number(depth) * number(limits["depthFraction"]))
    if limits.get("dailyVolumeFraction") is not None:
        volume = snapshot.get("dayNotionalVolume")
        if volume is None:
            raise Stop("VOLUME_UNAVAILABLE", "Configured volume participation requires verified public volume.")
        caps.append(number(volume) * number(limits["dailyVolumeFraction"]) * limits["requoteSeconds"] / 86400)
    size = min(residual, min(caps) / max(price, snapshot["mark"]))
    size = size.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_DOWN)
    if size <= 0 or size * min(price, snapshot["mark"]) < 10:
        return None
    if size * max(price, snapshot["mark"]) > max_notional:
        raise Stop("CHILD_BOUND", "Rounded child notional exceeds its hard cap.")
    return {"market": intent["market"], "buy": buy, "size": str(size), "price": str(price), "reduceOnly": reducing,
            "tif": "Ioc" if urgent else "Alo", "decimals": decimals, "maxNotional": str(max_notional),
            "slippageBps": limits["slippageBps"], "depthFraction": limits.get("depthFraction"),
            "dailyVolumeFraction": limits.get("dailyVolumeFraction"), "requoteSeconds": limits["requoteSeconds"]}


class PaperExecutor:
    """Immediate fictional fills at decision price, same frozen intent semantics."""
    def __init__(self, state, commit, report, admission):
        self.state, self.commit, self.report, self.admission = state, commit, report, admission

    def recover(self):
        pass

    def set_target(self, intent):
        self.state["pending"] = intent
        self.admission.generation = intent["id"]

    def step(self, now_ms):
        if not self.state["pending"]:
            return
        intent = self.state["pending"]
        try:
            self.admission.check(intent["id"], intent["deadlineMs"])
        except Stop:
            return
        old = number(self.state["position"], signed=True)
        self.state.update(active=intent, pending=None, position=intent["quantity"], phase="simulated")
        self.report("simulated", intent, {"filledSize": str(abs(number(intent["quantity"], signed=True) - old)),
                                         "vwap": intent["arrivalPrice"], "simulated": True})
        self.commit()

    def shutdown(self):
        self.admission.stopping.set()


class LimitChaser:
    def __init__(self, state, commit, report, admission, worker, adapter, limits):
        self.state, self.commit, self.report = state, commit, report
        self.admission, self.worker, self.adapter, self.limits = admission, worker, adapter, limits
        self.job, self.snapshot, self.observed_ms, self.next_read = None, None, 0, 0
        self.open_risk_paused = False

    def recover(self):
        # Prepared means never dispatched, but restarting does not reuse its
        # stale plan. Only that provably unentered phase can discard a child.
        child = self.state["child"]
        if child and child["phase"] == "prepared":
            self.state["child"] = None
            self.state["phase"] = "ready"
            self.commit()
        active = self.state["pending"] or self.state["active"]
        self.admission.generation = active["id"] if active else None

    def set_target(self, intent):
        self.state["pending"] = intent
        # Late worker results still belong to the retained child, but new
        # signing against the superseded target is stopped immediately.
        self.admission.generation = intent["id"]

    def _blocked(self, code):
        if self.state["error"] != code:
            self.state["phase"], self.state["error"] = "blocked", code
            self.report("blocked", self.state["active"], {"reason": code})
            self.commit()

    def _record_external(self, value):
        # Called only after all own-child regression/coverage checks succeed.
        # The ledger and resulting position are committed together below.
        self.state.setdefault("externalReductions", []).extend(value["externalReductions"])
        self.report("external_risk_reduction_observed", self.state["active"],
                    {"externalDelta": value["externalDelta"], "externalFills": value["externalReductions"],
                     "attribution": "Public fills prove risk reduction, not the submitting caller."})

    def _collect(self, now_ms):
        if self.job is None:
            return
        response = self.worker.take(self.job[0])
        if response is None:
            return
        _, kind, identity = self.job
        self.job = None
        ok, value = response
        child = self.state["child"]
        if identity is not None and (child is None or child["cloid"] != identity):
            raise Stop("EFFECT_IDENTITY_CONFLICT", "A worker result does not belong to the retained child.")
        if not ok:
            self._blocked(value)
            self.next_read = now_ms + 1000
            return
        if kind == "submit" and value.get("classification") in {"not_submitted", "rejected"}:
            # An observed pre-call failure or strict exchange rejection proves
            # this child never filled. Crashes before this result do not.
            reason = value.get("reason", value.get("code", "ORDER_REJECTED"))
            self.state["child"] = None
            status = "child_rejected" if value["classification"] == "rejected" else "child_not_submitted"
            self.report(status, self.state["active"], {"childId": child["cloid"], "reason": reason})
            self.snapshot = None
            self._blocked(reason)
            self.commit()
            self.next_read = now_ms + 1000
            return
        if kind != "observe":
            # Acknowledgements are hints. Never add their size to fills or
            # promote one filled child into a whole-target completion.
            child["acknowledgement"] = value.get("classification", "ambiguous")
            child["phase"] = "evidence_pending"
            self.state["phase"] = "evidence_pending"
            self.commit()
            self.next_read = 0
            return
        self.snapshot, self.observed_ms = value["snapshot"], now_ms
        evidence = value["evidence"]
        external = value.get("externalReductions", [])
        if child is None:
            if external:
                self.state["position"] = value["position"]
                self._record_external(value)
            if number(value["position"], signed=True) != number(self.state["position"], signed=True):
                self._blocked("OWNERSHIP_CONFLICT")
                self.snapshot = None
            else:
                self.state["error"] = None
            if external:
                self.commit()
            return
        if not evidence["known"]:
            self._blocked("CHILD_SUBMISSION_UNRESOLVED")
            self.snapshot = None
            return
        if child.get("oid") is not None and child["oid"] != evidence["oid"]:
            raise Stop("ORDER_ID_CHANGED", "Owned child order identity changed.")
        child["oid"] = evidence["oid"]
        child["cancelAwaitingEvidence"] = False
        old_size = number(child.get("filled", "0"))
        filled = number(evidence["filled"])
        if filled < old_size or filled > number(child["plan"]["size"]):
            self._blocked("FILL_EVIDENCE_REGRESSED")
            self.snapshot = None
            return
        old_fills = {(row["oid"], row["tid"], row["time"]): row for row in child.get("fills", [])}
        new_fills = {(row["oid"], row["tid"], row["time"]): row for row in evidence["fills"]}
        if any(new_fills.get(key) != value for key, value in old_fills.items()):
            self._blocked("FILL_EVIDENCE_CHANGED")
            self.snapshot = None
            return
        child.update(filled=str(filled), fills=evidence["fills"], value=evidence["value"], fees=evidence["fees"],
                     maker=evidence["maker"], taker=evidence["taker"], terminal=evidence["terminal"])
        if not evidence["consistent"] or evidence["terminal"] and not evidence["complete"]:
            child["phase"] = "evidence_pending"
            self._blocked("CHILD_FILL_COVERAGE_PENDING")
            self.commit()
            self.snapshot = None
            return
        if evidence["complete"]:
            self.state["position"] = value["position"]
            if external:
                self._record_external(value)
            self.state["filledSize"] = str(number(self.state["filledSize"]) + filled)
            self.state["filledValue"] = str(number(self.state["filledValue"]) + number(evidence["value"]))
            self.state["fees"] = str(number(self.state["fees"], signed=True) + number(evidence["fees"], signed=True))
            self.state["makerFills"] += evidence["maker"]
            self.state["takerFills"] += evidence["taker"]
            if child["oid"] not in self.state["ownedOids"]:
                self.state["ownedOids"].append(child["oid"])
            self.state["child"], self.state["phase"], self.state["error"] = None, "ready", None
            self.report("child_complete", self.state["active"], {"childId": child["cloid"], "oid": child["oid"],
                        "filledSize": str(filled), "vwap": str(number(evidence["value"]) / filled) if filled else None,
                        "feesUsdc": evidence["fees"], "makerFills": evidence["maker"], "takerFills": evidence["taker"]})
        else:
            child["phase"] = "child_open"
            self.state["phase"], self.state["error"] = "child_open", None
        self.commit()

    def _read(self, now_ms):
        state = copy.deepcopy(self.state)
        identity = state["child"]["cloid"] if state["child"] else None
        token = "executor-observe"
        market = (state["active"] or state["pending"])["market"]
        if self.worker.submit(token, lambda: self.adapter.observe(market, state)):
            self.job = (token, "observe", identity)
            self.next_read = now_ms + (10000 if self.open_risk_paused and state["child"] is None else 5000)

    def step(self, now_ms):
        self._collect(now_ms)
        if self.job is not None or not self.worker.idle:
            return
        state, active, child = self.state, self.state["active"], self.state["child"]
        if not active and not state["pending"]:
            return
        if child:
            needs_cancel = (self.admission.stopping.is_set() or not self.admission.feed_ready.is_set()
                            or not self.admission.feed_check() or state["pending"] is not None
                            or now_ms >= active["deadlineMs"] or now_ms - child["startedMs"] >= self.limits["requoteSeconds"] * 1000
                            or self.open_risk_paused and not child["plan"]["reduceOnly"])
            if self.open_risk_paused and child["plan"]["reduceOnly"] and not self.admission.stopping.is_set():
                needs_cancel = False
            if needs_cancel and child.get("oid") is not None and child["phase"] in {"child_open", "evidence_pending"} and not child.get("terminal") and not child.get("cancelAwaitingEvidence"):
                child["phase"] = "cancel_uncertain"
                child["cancelAwaitingEvidence"] = True
                state["phase"] = "cancel_uncertain"
                self.commit()
                frozen, retained, intent = copy.deepcopy(child), copy.deepcopy(state), copy.deepcopy(active)
                token = "executor-cancel"
                self.worker.submit(token, lambda: self.adapter.cancel_child(frozen, retained, intent, self.admission))
                self.job = (token, "cancel", child["cloid"])
            elif now_ms >= self.next_read:
                self._read(now_ms)
            return
        if state["pending"]:
            state["active"], state["pending"] = state["pending"], None
            active = state["active"]
            state.update(phase="ready", error=None, childCount=0, filledSize="0", filledValue="0", fees="0", makerFills=0, takerFills=0)
            self.admission.generation = active["id"]
            self.snapshot = None
            self.commit()
        if self.admission.stopping.is_set():
            return
        if not self.admission.feed_ready.is_set() or not self.admission.feed_check():
            return
        if now_ms >= active["deadlineMs"]:
            if state["phase"] != "expired":
                state["phase"] = "expired"
                self.report("expired", active)
                self.commit()
            return
        if state["phase"] in {"target_reached", "residual_dust"} and not self.open_risk_paused:
            return
        if self.snapshot is None or now_ms - self.observed_ms > 1000:
            if now_ms >= self.next_read:
                self._read(now_ms)
            return
        if number(state["position"], signed=True) == number(active["quantity"], signed=True):
            if state["phase"] != "target_reached":
                state["phase"] = "target_reached"
                self.report("target_reached", active)
                self.commit()
            return
        if state["childCount"] >= self.limits["maxChildren"] or len(state["ownedOids"]) >= 256:
            self._blocked("CHILD_COUNT_BOUND")
            return
        try:
            plan = plan_child(active, state, self.snapshot, self.limits, now_ms)
            self.admission.check(active["id"], active["deadlineMs"])
        except Stop as error:
            self._blocked(error.code)
            return
        if plan is None:
            if state["phase"] != "residual_dust":
                state["phase"] = "residual_dust"
                self.report("residual_dust", active)
                self.commit()
            return
        if self.open_risk_paused and not plan["reduceOnly"]:
            if state["phase"] != "release_hold":
                state["phase"] = "release_hold"
                self.report("release_hold", active)
                self.commit()
            return
        state["sequence"] += 1
        state["childCount"] += 1
        cloid = "0x" + hashlib.sha256((active["id"] + ":" + str(state["sequence"])).encode()).hexdigest()[:32]
        child = {"cloid": cloid, "plan": plan, "phase": "prepared", "startedMs": now_ms,
                 "basePosition": state["position"], "filled": "0", "fills": [], "oid": None}
        prechild = copy.deepcopy(state)
        state["child"], state["phase"] = child, "prepared"
        self.commit()
        child["phase"], state["phase"] = "submission_uncertain", "submission_uncertain"
        self.commit()
        frozen, intent = copy.deepcopy(child), copy.deepcopy(active)
        token = "executor-submit"
        self.worker.submit(token, lambda: self.adapter.submit_child(frozen, prechild, intent, self.admission))
        self.job = (token, "submit", cloid)
        self.snapshot = None

    def shutdown(self):
        self.admission.stopping.set()
