"""Continuous-runtime SDK boundary, separate from diagnostic smoke guards.

Only exact owned orders may be canceled. A canceled partial child retires only
when origSz - remaining sz, complete matching fills and owned position agree.
If a venue status does not expose remaining size this stays unresolved.
"""
import time
from decimal import Decimal

from adapter import Adapter, classify_submission, sdk_types
from risk import Stop, number

TERMINAL = {"filled", "canceled", "rejected", "marginCanceled", "reduceOnlyCanceled",
            "openInterestCapCanceled", "selfTradeCanceled", "siblingFilledCanceled", "delistedCanceled",
            "scheduledCancel", "tickRejected", "minTradeNtlRejected", "perpMarginRejected",
            "reduceOnlyRejected", "badAloPxRejected", "iocCancelRejected", "badTriggerPxRejected",
            "marketOrderNoLiquidityRejected", "positionIncreaseAtOpenInterestCapRejected",
            "positionFlipAtOpenInterestCapRejected", "tooAggressiveAtOpenInterestCapRejected",
            "openInterestIncreaseRejected", "insufficientSpotBalanceRejected", "oracleRejected", "perpMaxPositionRejected"}


class RuntimeAdapter(Adapter):
    def observe(self, market, state):
        child = state.get("child")
        account = self.access.scope["tradingAccount"]
        detail, order = None, None
        if child:
            _, _, Cloid = sdk_types()
            value = self.read(self.info.query_order_by_cloid, account, Cloid.from_str(child["cloid"]))
            if not isinstance(value, dict) or value.get("status") not in {"order", "unknownOid"}:
                raise Stop("ORDER_EVIDENCE_INVALID", "Owned order status is unavailable.")
            if value["status"] == "order":
                try:
                    detail = value["order"]
                    order = detail["order"]
                    plan = child["plan"]
                    if (order.get("cloid") != child["cloid"] or order.get("coin") != market
                            or order.get("side") != ("B" if plan["buy"] else "A")
                            or number(order["origSz"]) != number(plan["size"])
                            or number(order["limitPx"]) != number(plan["price"])
                            or order.get("reduceOnly") is not plan["reduceOnly"]
                            or type(order.get("oid")) is not int
                            or type(order.get("timestamp")) is not int
                            or not child["startedMs"] - 5000 <= order["timestamp"] <= int(time.time() * 1000) + 5000):
                        raise ValueError()
                except (KeyError, TypeError, ValueError):
                    raise Stop("ORDER_EVIDENCE_INVALID", "The order does not bind to the exact retained child plan.") from None
        allowed = set(state.get("ownedOids", []))
        if order:
            allowed.add(order["oid"])
        end_ms = int(time.time() * 1000)
        fills = self.read(self.info.user_fills_by_time, account, state["activitySince"], end_ms)
        if not isinstance(fills, list) or len(fills) >= 2000:
            raise Stop("ACCOUNT_ACTIVITY_UNVERIFIED", "The session activity scan reached its bound. Preserve the reservation for review.")
        seen, matching, filled, value, fees = set(), [], Decimal(0), Decimal(0), Decimal(0)
        maker, taker = 0, 0
        try:
            for fill in fills:
                if not isinstance(fill, dict) or type(fill.get("oid")) is not int or type(fill.get("tid")) is not int or type(fill.get("time")) is not int:
                    raise ValueError()
                identity = (fill["oid"], fill["tid"], fill["time"], fill.get("coin"))
                if identity in seen or not state["activitySince"] <= fill["time"] <= end_ms:
                    raise ValueError()
                seen.add(identity)
                if fill["oid"] not in allowed:
                    raise Stop("ACCOUNT_ACTIVITY_CONFLICT", "An unowned order filled during this runtime. It remains untouched.")
                if order is None or fill["oid"] != order["oid"]:
                    continue
                plan = child["plan"]
                size, price = number(fill["sz"]), number(fill["px"])
                if (fill.get("coin") != market or fill.get("side") != ("B" if plan["buy"] else "A")
                        or size <= 0 or price <= 0
                        or (plan["buy"] and price > number(plan["price"]))
                        or (not plan["buy"] and price < number(plan["price"]))):
                    raise ValueError()
                filled += size
                value += size * price
                fee = number(fill["fee"], signed=True) if fill.get("feeToken") == "USDC" else None
                if fee is None:
                    raise ValueError()
                fees += fee
                if type(fill.get("crossed")) is not bool:
                    raise ValueError()
                taker += int(fill["crossed"])
                maker += int(not fill["crossed"])
                matching.append({"oid": fill["oid"], "tid": fill["tid"], "time": fill["time"], "coin": market,
                                 "size": str(size), "price": str(price), "fee": str(fee), "crossed": fill["crossed"]})
            if len(matching) > 256 or child and filled > number(child["plan"]["size"]):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise Stop("FILL_EVIDENCE_INVALID", "Complete bounded fill identity, quantities or prices could not be verified.") from None
        # Snapshot runs after the fills read, so a late fill produces a mismatch
        # and another read, never optimistic retirement or replacement.
        snapshot = self.snapshot(market)
        if set(snapshot["positions"]) - {market}:
            raise Stop("ACCOUNT_CONFLICT", "Unowned market exposure remains untouched.")
        open_ids = set()
        for item in snapshot["orders"]:
            if not isinstance(item, dict) or type(item.get("oid")) is not int or item["oid"] not in allowed or not order or item["oid"] != order["oid"]:
                raise Stop("ACCOUNT_CONFLICT", "Unowned open orders remain untouched.")
            open_ids.add(item["oid"])
        position = snapshot["positions"].get(market, Decimal(0))
        expected = number(state["position"], signed=True)
        if child:
            expected = number(child["basePosition"], signed=True) + filled * (1 if child["plan"]["buy"] else -1)
        consistent = position == expected
        evidence = {"known": order is not None, "terminal": False, "complete": False,
                    "filled": str(filled), "value": str(value), "fees": str(fees),
                    "maker": maker, "taker": taker, "fills": matching, "consistent": consistent}
        if order:
            evidence["oid"] = order["oid"]
            terminal = detail.get("status") in TERMINAL
            try:
                remaining = number(order["sz"])
                covered = 0 <= remaining <= number(order["origSz"]) and number(order["origSz"]) - remaining == filled
                if detail.get("status") == "filled":
                    covered = covered and remaining == 0
            except (KeyError, Stop):
                covered = False
            evidence.update(terminal=terminal, complete=terminal and covered and consistent and order["oid"] not in open_ids)
        elif not child and not consistent:
            raise Stop("OWNERSHIP_CONFLICT", "Current position differs from confirmed runtime-owned exposure.")
        return {"snapshot": snapshot, "evidence": evidence, "position": str(position)}

    def _preflight(self, plan, state, intent, admission):
        admission.check(intent["id"], intent["deadlineMs"])
        observed = self.observe(plan["market"], state)
        current = observed["snapshot"]
        position = number(observed["position"], signed=True)
        size, price = number(plan["size"]), number(plan["price"])
        if current["orders"] or state.get("child"):
            raise Stop("ACCOUNT_CONFLICT", "A new child requires no previous owned or unowned open order.")
        if current["decimals"] != plan["decimals"] or size.quantize(Decimal(1).scaleb(-current["decimals"])) != size:
            raise Stop("PRECISION_UNAVAILABLE", "Market precision changed since this order was planned. Reconcile it before replanning.")
        target = number(intent["quantity"], signed=True)
        delta = target - position
        reducing = position != 0 and delta * position < 0
        allowed = min(abs(delta), abs(position)) if reducing else abs(delta)
        if plan["reduceOnly"] is not reducing or plan["buy"] != (delta > 0) or not 0 < size <= allowed:
            raise Stop("OWNERSHIP_CONFLICT", "The child would exceed its residual or cross the verified flat boundary.")
        reference = number(intent["arrivalPrice"])
        price_bound = reference * (1 + number(plan["slippageBps"]) / 10000 if plan["buy"] else 1 - number(plan["slippageBps"]) / 10000)
        exposure = size * max(price, current["mark"])
        caps = [number(plan["maxNotional"])]
        if plan.get("depthFraction") is not None:
            key = "askDepth" if (plan["buy"] and plan["tif"] == "Ioc") or (not plan["buy"] and plan["tif"] == "Alo") else "bidDepth"
            if current.get(key) is None:
                raise Stop("DEPTH_UNAVAILABLE", "Fresh depth participation evidence is unavailable.")
            caps.append(number(current[key]) * number(plan["depthFraction"]))
        if plan.get("dailyVolumeFraction") is not None:
            if current.get("dayNotionalVolume") is None:
                raise Stop("VOLUME_UNAVAILABLE", "Fresh volume participation evidence is unavailable.")
            caps.append(number(current["dayNotionalVolume"]) * number(plan["dailyVolumeFraction"]) * plan["requoteSeconds"] / 86400)
        if exposure > min(caps):
            raise Stop("PARTICIPATION_BOUND_CHANGED", "The child exceeds a fresh retained depth/volume participation cap.")
        if ((plan["buy"] and price > price_bound) or (not plan["buy"] and price < price_bound)
                or exposure > number(plan["maxNotional"]) or size * min(price, current["mark"]) < 10):
            raise Stop("CHILD_BOUND_CHANGED", "The child exceeds its fixed price/notional bound or venue minimum.")
        if not reducing and (current["taker_fee"] > Decimal("0.001") or current["perp_available"] < exposure * (1 + 2 * current["taker_fee"]) + Decimal("0.05")):
            raise Stop("PERP_COLLATERAL_REQUIRED", "Fresh perpetual collateral cannot fund this bounded child.")
        admission.check(intent["id"], intent["deadlineMs"])

    def submit_child(self, child, state, intent, admission):
        plan = child["plan"]
        # state is the immutable prechild account state, with the child absent.
        try:
            admission.check(intent["id"], intent["deadlineMs"])
            exchange = self.exchange(intent["deadlineMs"], before_sign=lambda: self._preflight(plan, state, intent, admission))
            admission.check(intent["id"], intent["deadlineMs"])
        except Stop as error:
            return {"classification": "not_submitted", "reason": error.code}
        _, _, Cloid = sdk_types()
        try:
            response = exchange.order(plan["market"], plan["buy"], float(number(plan["size"])), float(number(plan["price"])),
                                      {"limit": {"tif": plan["tif"]}}, reduce_only=plan["reduceOnly"], cloid=Cloid.from_str(child["cloid"]))
        except Exception:
            return {"classification": "ambiguous"}
        # Only an unambiguous error-only response proves rejection. Malformed
        # mixed success/error responses remain potentially submitted.
        classification = classify_submission(response)
        if classification["classification"] == "rejected":
            statuses = response.get("response", {}).get("data", {}).get("statuses", [])
            strict = response.get("status") == "err" or (len(statuses) == 1 and set(statuses[0]) == {"error"})
            if not strict:
                return {"classification": "ambiguous"}
        return classification

    def cancel_child(self, child, state, intent, admission):
        admission.check(intent["id"], intent["deadlineMs"], cleanup=True)
        observation = self.observe(child["plan"]["market"], state)
        evidence = observation["evidence"]
        if not evidence["known"] or evidence.get("oid") != child.get("oid"):
            raise Stop("OWNED_ORDER_UNVERIFIED", "Cancellation cannot bind the retained child to an exact public order ID.")
        if evidence["terminal"]:
            return {"classification": "terminal_observed"}
        def check():
            admission.check(intent["id"], intent["deadlineMs"], cleanup=True)
        def authorize():
            self.access.verify()
            check()
        exchange = self.exchange(admission.cleanup_deadline, before_sign=authorize)
        check()
        try:
            # Never use cancel-all, market-wide cancellation or an unowned ID.
            exchange.cancel(child["plan"]["market"], child["oid"])
        except Exception:
            return {"classification": "ambiguous"}
        return {"classification": "cancel_submitted"}
