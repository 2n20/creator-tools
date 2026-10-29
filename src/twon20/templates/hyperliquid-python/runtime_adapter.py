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
        external = []
        retained_external = {tuple(row["identity"]): row for row in state.get("externalReductions", [])}
        retained_seen = set()
        try:
            for fill in fills:
                if not isinstance(fill, dict) or type(fill.get("oid")) is not int or type(fill.get("tid")) is not int or type(fill.get("time")) is not int:
                    raise ValueError()
                identity = (fill["oid"], fill["tid"], fill["time"], fill.get("coin"))
                if identity in seen or not state["activitySince"] <= fill["time"] <= end_ms:
                    raise ValueError()
                seen.add(identity)
                if fill["oid"] not in allowed:
                    row = {"identity": list(identity), "startPosition": str(number(fill.get("startPosition"), signed=True)),
                           "size": str(number(fill["sz"])), "side": fill.get("side"), "coin": fill.get("coin")}
                    if identity in retained_external:
                        if row != retained_external[identity]:
                            raise ValueError()
                        retained_seen.add(identity)
                    else:
                        epoch = state.get("settlementEpoch")
                        if (not epoch or not epoch["sinceMs"] - 5000 <= fill["time"] <= epoch["throughMs"]
                                or fill.get("coin") != market or fill.get("side") not in {"A", "B"}):
                            raise Stop("ACCOUNT_ACTIVITY_CONFLICT", "Unexplained external activity requires review; no foreign order is touched.")
                        external.append(row)
                    continue
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
        if retained_seen != set(retained_external) or len(retained_external) + len(external) > 256:
            raise Stop("ACCOUNT_ACTIVITY_UNVERIFIED", "Complete retained external fill coverage is required.")
        # Snapshot runs after the fills read, so a late fill produces a mismatch
        # and another read, never optimistic retirement or replacement.
        snapshot = self.market_snapshot(market)
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
        external_delta = Decimal(0)
        external_pending = bool(external and child and order and detail.get("status") not in TERMINAL)
        if external and not external_pending:
            # Ambiguous/partial own children cannot establish an ordering boundary.
            if child and (not order or detail.get("status") not in TERMINAL):
                raise Stop("EXTERNAL_REDUCTION_UNRESOLVED", "Resolve the exact own child before reconciling an external reduction.")
            for row in sorted(external, key=lambda item: (item["identity"][2], item["identity"][1])):
                start, size = number(row["startPosition"], signed=True), number(row["size"])
                delta = size if row["side"] == "B" else -size
                post = start + delta
                if (start != expected or size <= 0 or start == 0 or delta * start >= 0
                        or post * start < 0 or abs(post) >= abs(start)):
                    raise Stop("ACCOUNT_ACTIVITY_CONFLICT", "External fills do not prove a strict reduction of verified exposure.")
                expected = post
                external_delta += delta
        consistent = position == expected and not external_pending
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
        if external and not external_pending and not consistent:
            raise Stop("EXTERNAL_REDUCTION_UNRESOLVED", "External fill coverage and current exposure disagree.")
        return {"snapshot": snapshot, "evidence": evidence, "position": str(position),
                "externalReductions": [] if external_pending else external, "externalDelta": str(external_delta)}

    def _withdrawal_check(self, plan, intent):
        from twon20.withdrawals import withdrawal_policy
        policy = withdrawal_policy(self.access.pending_withdrawals())
        if plan["reduceOnly"]:
            return
        if not policy.ready_to_increase:
            raise Stop("WITHDRAWAL_HOLD", "Fresh withdrawal policy forbids increasing risk.")
        cap = min(number(intent["targetNotional"]), policy.capital.remaining_usdc * number(intent["capitalMultiple"]))
        if abs(number(intent["quantity"], signed=True)) * number(intent["arrivalPrice"]) > cap * abs(number(intent["fraction"], signed=True)):
            raise Stop("CAPITAL_CHANGED", "Fresh remaining capital requires rebasing the retained target.")

    def _margin_check(self, plan, current, exposure):
        account = self.access.scope["tradingAccount"]
        data = self.read(self.info.post, "/info", {"type": "activeAssetData", "user": account, "coin": plan["market"]})
        leverage = data.get("leverage") if isinstance(data, dict) else None
        if (not isinstance(data, dict) or not isinstance(data.get("user"), str) or data["user"].lower() != account.lower() or data.get("coin") != plan["market"]
                or not isinstance(leverage, dict) or leverage.get("type") != "cross"
                or type(leverage.get("value")) is not int or not 1 <= leverage["value"] <= 100
                or not isinstance(data.get("maxTradeSzs"), list) or len(data["maxTradeSzs"]) != 2
                or not isinstance(data.get("availableToTrade"), list) or len(data["availableToTrade"]) != 2):
            raise Stop("LEVERAGE_UNVERIFIED", "Fresh native cross-margin settings and trade capacity are required. No account setting was changed.")
        side = 0 if plan["buy"] else 1
        fee = current["taker_fee"]
        # Use the current exchange setting for initial margin, retaining both
        # taker fees and the fixed reserve. Notional/capital caps remain separate.
        required = exposure / leverage["value"] + exposure * 2 * fee + Decimal("0.05")
        if (fee > Decimal("0.001") or number(plan["size"]) > number(data["maxTradeSzs"][side])
                or min(current["perp_available"], number(data["availableToTrade"][side])) < required):
            raise Stop("PERP_COLLATERAL_REQUIRED", "Fresh perpetual collateral cannot fund this bounded child at its current cross leverage.")

    def _preflight(self, plan, state, intent, admission):
        admission.check(intent["id"], intent["deadlineMs"])
        # Read-only observation does not need another complete deployment and
        # key check. Every actual child still verifies authority here, after
        # SDK metadata preparation and immediately before private loading.
        self.access.verify()
        admission.check(intent["id"], intent["deadlineMs"])
        self.other_dex_conflicts(self.access.scope["tradingAccount"])
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
        if not reducing:
            self._margin_check(plan, current, exposure)
        self._withdrawal_check(plan, intent)
        admission.check(intent["id"], intent["deadlineMs"])

    def submit_child(self, child, state, intent, admission):
        plan = child["plan"]
        # state is the immutable prechild account state, with the child absent.
        try:
            admission.check(intent["id"], intent["deadlineMs"])
            exchange = self.exchange(intent["deadlineMs"], before_sign=lambda: self._preflight(plan, state, intent, admission))
            self._withdrawal_check(plan, intent)
            admission.check(intent["id"], intent["deadlineMs"])
            _, _, Cloid = sdk_types()
            cloid = Cloid.from_str(child["cloid"])
        except Stop as error:
            return {"classification": "not_submitted", "reason": error.code}
        except Exception:
            # The SDK order call has not been entered. Preserve that fact even
            # when a CLI/public-read exception is not the runner's Stop type.
            return {"classification": "not_submitted", "reason": "ORDER_PREPARATION_UNAVAILABLE"}
        try:
            response = exchange.order(plan["market"], plan["buy"], float(number(plan["size"])), float(number(plan["price"])),
                                      {"limit": {"tif": plan["tif"]}}, reduce_only=plan["reduceOnly"], cloid=cloid)
        except Exception:
            return {"classification": "ambiguous"}
        # Only an unambiguous error-only response proves rejection. Malformed
        # mixed success/error responses remain potentially submitted.
        classification = classify_submission(response)
        if classification["classification"] == "rejected":
            if response.get("status") == "err":
                return classification
            statuses = response.get("response", {}).get("data", {}).get("statuses", [])
            strict = len(statuses) == 1 and set(statuses[0]) == {"error"}
            if not strict:
                return {"classification": "ambiguous"}
            if statuses and isinstance(statuses[0].get("error"), str):
                message = statuses[0]["error"]
                for prefix, reason in (
                    ("Order could not immediately match against any resting orders", "IOC_NO_MATCH"),
                    ("Post only order would have immediately matched", "POST_ONLY_WOULD_CROSS"),
                    ("Reduce only order would increase position", "REDUCE_ONLY_REJECTED"),
                    ("Order must have minimum value of $10", "MINIMUM_NOTIONAL_REJECTED"),
                    ("Insufficient margin to place order", "INSUFFICIENT_MARGIN"),
                ):
                    if message.startswith(prefix):
                        classification["reason"] = reason
                        break
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
            self.other_dex_conflicts(self.access.scope["tradingAccount"])
            check()
        exchange = self.exchange(admission.cleanup_deadline, before_sign=authorize)
        check()
        try:
            # Never use cancel-all, market-wide cancellation or an unowned ID.
            exchange.cancel(child["plan"]["market"], child["oid"])
        except Exception:
            return {"classification": "ambiguous"}
        return {"classification": "cancel_submitted"}
