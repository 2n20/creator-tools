"""Reviewed official SDK adapter. Paper/public checks never construct a signer."""
import importlib.metadata
import logging
import os
from pathlib import Path
import re
import stat
import time

from risk import Stop, number

SDK_VERSION = "0.24.0"
API = "https://api.hyperliquid.xyz"


def sdk_types():
    if importlib.metadata.version("hyperliquid-python-sdk") != SDK_VERSION:
        raise Stop("SDK_VERSION_UNSUPPORTED", "Install the reviewed requirements-runner.txt in a separate virtual environment.")
    from hyperliquid.info import Info
    from hyperliquid.exchange import Exchange
    from hyperliquid.utils.types import Cloid
    return Info, Exchange, Cloid


def public_info():
    Info, _, _ = sdk_types()
    try:
        return Info(API, skip_ws=True, timeout=5)
    except Exception:
        raise Stop("PUBLIC_API_UNAVAILABLE", "Retry public reads later; no signer was loaded.") from None


def fresh(timestamp, maximum_age=15000):
    now = int(time.time() * 1000)
    if type(timestamp) is not int or not now - maximum_age <= timestamp <= now + 5000:
        raise Stop("STALE_PUBLIC_DATA", "Check the machine clock and retry fresh public reads.")


class Access:
    """Bind a selected CLI directory using its public record, then fresh authority."""
    def __init__(self, vault, directory):
        from twon20.discovery import address, vault_link
        from twon20.files import load_setup, open_output
        self.vault = vault_link(vault)[2]
        self.directory = Path(directory).expanduser().absolute()
        self.output = open_output(self.directory)
        try:
            self.metadata = load_setup(self.output)
            if vault_link(self.metadata["vaultLink"])[2] != self.vault:
                raise Stop("SETUP_BINDING_CONFLICT", "Select the existing setup directory for this vault. No private file was read.")
            self.key = address(self.metadata["tradingKey"])
            info = os.stat("strategy.key", dir_fd=self.output.descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise Stop("UNSAFE_KEY_PATH", "The retained key must be a regular, owned file with mode 600. Never inspect or upload its contents.")
            self.scope = None
            self.verify()
        except BaseException:
            self.output.close()
            raise

    def close(self):
        self.output.close()

    def verify(self):
        from twon20.discovery import ZERO, discover
        from twon20.rpc import Rpc
        from twon20.status import status
        rpc = Rpc("mainnet")
        found = discover(self.vault, rpc=rpc)
        if self.output.exists("onboarding.public.json"):
            from twon20.onboarding import load_state
            retained = load_state(self.output, found)
            if retained.get("tradingKey", "").lower() != self.key:
                raise Stop("SETUP_BINDING_CONFLICT", "The public onboarding checkpoint differs from the selected local key. Preserve the existing directory.")
        if found.network != "mainnet" or found.anchor["chainId"] != 999 or found.trading_account in {ZERO, found.vault, found.approval_contract, self.key}:
            raise Stop("UNSUPPORTED_ACCOUNT", "Use the CLI-verified mainnet trading account. Legacy vault-account fallback is unsupported.")
        if found.requested_key != self.key:
            raise Stop("KEY_CONFLICT", "The approved/requested key differs from this retained local key. Do not replace keys or change approvals automatically.")
        scope = {"vault": found.vault, "network": "mainnet", "chainId": 999,
                 "approvalContract": found.approval_contract, "tradingAccount": found.trading_account,
                 "owner": found.owner, "tradingKey": self.key}
        if self.scope is not None and scope != self.scope:
            raise Stop("ACCESS_CHANGED", "The verified account, owner or key binding changed. Review recovery yourself.")
        self.evidence = status(found, rpc)
        if not self.evidence.get("tradingAccessReady") or self.evidence.get("evidence") != "verified-public-reads":
            raise Stop("ACCESS_UNVERIFIED", "Current approval, funding or standard account mode is unverified. Resume 2n20 onboard/status; approval alone does not authorize this runner.")
        self.scope = scope
        return self.evidence

    def signer(self):
        # The only private read belongs inside the intended runtime, immediately
        # before an explicitly authorized SDK write. Never return key material.
        from twon20.files import load_key
        return load_key(self.output, self.metadata)


class Adapter:
    def __init__(self, access, info=None):
        self.access = access
        self.info = info if info is not None else public_info()

    def read(self, function, *args):
        try:
            return function(*args)
        except Stop:
            raise
        except Exception:
            raise Stop("PUBLIC_API_UNAVAILABLE", "A public read failed or was rate limited. No unavailable evidence counts as readiness.") from None

    def snapshot(self, market):
        self.access.verify()
        account = self.access.scope["tradingAccount"]
        try:
            context = self.read(self.info.meta_and_asset_ctxs)
            universe, contexts = context[0]["universe"], context[1]
            if not isinstance(universe, list) or not isinstance(contexts, list) or len(universe) != len(contexts) or len(universe) > 2000:
                raise ValueError()
            matches = [index for index, asset in enumerate(universe) if asset.get("name") == market]
            if len(matches) != 1:
                raise Stop("UNSUPPORTED_MARKET", "Choose a listed native USDC perpetual, without a spot or builder-dex alias.")
            index = matches[0]
            asset = universe[index]
            if asset.get("isDelisted", False) or type(asset.get("szDecimals")) is not int or not 0 <= asset["szDecimals"] <= 6:
                raise Stop("PRECISION_UNAVAILABLE", "The native market is delisted or its precision is unsupported.")
            book = self.read(self.info.l2_snapshot, market)
            fresh(book["time"], 5000)
            if book.get("coin") != market or len(book["levels"]) != 2:
                raise ValueError()
            bid, ask = [number(level[0]["px"]) for level in book["levels"]]
            if not 0 < bid <= ask or any(number(level[0]["sz"]) <= 0 for level in book["levels"]):
                raise ValueError()
            state = self.read(self.info.user_state, account)
            fresh(state["time"])
            summary = state["marginSummary"]
            perp_available = min(number(state["withdrawable"]), max(number(summary["accountValue"]) - number(summary["totalMarginUsed"]), number(0)))
            positions, seen_coins = {}, set()
            if not isinstance(state["assetPositions"], list) or len(state["assetPositions"]) > 2000:
                raise ValueError()
            for item in state["assetPositions"]:
                position = item["position"]
                coin, size = position["coin"], number(position["szi"], signed=True)
                if not isinstance(coin, str) or coin in seen_coins:
                    raise ValueError()
                seen_coins.add(coin)
                if size:
                    positions[coin] = size
            orders = self.read(self.info.open_orders, account)
            if not isinstance(orders, list) or len(orders) > 2000:
                raise ValueError()
            # Account-wide conflicts, including spot/HIP-3, are also refused.
            front = self.read(self.info.frontend_open_orders, account)
            if not isinstance(front, list) or len(front) > 2000:
                raise ValueError()
            orders = orders or front
            self.other_dex_conflicts(account)
            spot = self.read(self.info.spot_user_state, account)
            balances = spot["balances"]
            if not isinstance(balances, list) or len(balances) > 2000:
                raise ValueError()
            if any(not isinstance(balance, dict) or type(balance.get("token")) is not int for balance in balances):
                raise ValueError()
            usdc = [balance for balance in balances if balance["token"] == 0]
            if len(usdc) > 1 or usdc and usdc[0].get("coin") != "USDC":
                raise ValueError()
            spot_available = number(0)
            if usdc:
                total, hold = number(usdc[0]["total"]), number(usdc[0]["hold"])
                if hold > total: raise ValueError()
                spot_available = total - hold
            fee = number(self.read(self.info.user_fees, account)["userCrossRate"])
            mark = number(contexts[index]["markPx"])
            if mark <= 0: raise ValueError()
            fresh(book["time"], 5000)
            fresh(state["time"])
            return {"market": market, "decimals": asset["szDecimals"], "mark": mark,
                    "bid": bid, "ask": ask, "positions": positions, "orders": orders,
                    "perp_available": perp_available, "spot_available": spot_available, "taker_fee": fee,
                    "checkedAt": int(time.time()), "accountMode": "standard", "collateral": "native USDC"}
        except Stop:
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            raise Stop("PUBLIC_DATA_INVALID", "Fresh public market/account evidence is inconsistent. No signer was loaded.") from None

    def other_dex_conflicts(self, account):
        """Refuse hidden builder-perp exposure using documented SDK reads."""
        from concurrent.futures import ThreadPoolExecutor, wait
        dexs = self.read(self.info.perp_dexs)
        if not isinstance(dexs, list) or not 1 <= len(dexs) <= 128 or dexs[0] is not None:
            raise Stop("ACCOUNT_SCOPE_UNVERIFIED", "The complete public perpetual account scope is unavailable or exceeds this example's bounded check.")
        names = [dex.get("name") if isinstance(dex, dict) else None for dex in dexs[1:]]
        if len(set(names)) != len(names) or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", name) for name in names):
            raise Stop("ACCOUNT_SCOPE_UNVERIFIED", "Builder-perp metadata is inconsistent. No order will be submitted.")
        if not names: return
        Info, _, _ = sdk_types()

        def inspect(name):
            # Separate official SDK sessions, no signer, no websocket. Supply
            # empty metadata because only account endpoints are used here.
            info = Info(API, skip_ws=True, timeout=2, meta={"universe": []}, spot_meta={"universe": [], "tokens": []})
            state = self.read(info.user_state, account, name)
            fresh(state.get("time"))
            positions = state.get("assetPositions")
            orders = self.read(info.open_orders, account, name)
            if not isinstance(positions, list) or not isinstance(orders, list):
                raise Stop("ACCOUNT_SCOPE_UNVERIFIED", "Builder-perp account evidence is unavailable.")
            if orders or any(number(item["position"]["szi"], signed=True) for item in positions):
                raise Stop("ACCOUNT_CONFLICT", "Existing builder-perp orders or positions require your review. This native-perp example will not touch them.")

        pool = ThreadPoolExecutor(max_workers=16)
        futures = [pool.submit(inspect, name) for name in names]
        try:
            _, pending = wait(futures, timeout=20)
            if pending:
                raise Stop("ACCOUNT_SCOPE_UNVERIFIED", "The bounded account-scope check timed out. Stay in paper mode or retry public reads later.")
            for future in futures:
                future.result()
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    def order_evidence(self, cloid, plan, started_ms):
        _, _, Cloid = sdk_types()
        account = self.access.scope["tradingAccount"]
        value = self.read(self.info.query_order_by_cloid, account, Cloid.from_str(cloid))
        if isinstance(value, dict) and value.get("status") == "unknownOid":
            return {"known": False, "terminal": False, "filled": "0"}
        try:
            detail = value["order"]
            order, state = detail["order"], detail["status"]
            if value.get("status") != "order" or order.get("cloid") != cloid or order.get("coin") != plan["market"] or order.get("side") != ("B" if plan["buy"] else "A") or number(order["origSz"]) != number(plan["size"]) or number(order["limitPx"]) != number(plan["price"]) or order.get("reduceOnly") is not plan["reduceOnly"] or type(order.get("oid")) is not int or type(order.get("timestamp")) is not int or not started_ms - 5000 <= order["timestamp"] <= int(time.time() * 1000) + 5000:
                raise ValueError()
            # Bind authoritative fills to this exact public order ID. Duplicate
            # IDs and oversized result sets are rejected rather than guessed.
            fills = self.read(self.info.user_fills_by_time, account, started_ms, int(time.time() * 1000))
            if not isinstance(fills, list) or len(fills) >= 2000:
                raise ValueError()
            seen, filled, notional = set(), number(0), number(0)
            for fill in fills:
                if fill.get("oid") != order["oid"]: continue
                if fill.get("coin") != plan["market"] or fill.get("side") != ("B" if plan["buy"] else "A") or type(fill.get("tid")) is not int or fill["tid"] in seen or type(fill.get("time")) is not int or not started_ms - 5000 <= fill["time"] <= int(time.time() * 1000) + 5000:
                    raise ValueError()
                seen.add(fill["tid"])
                size, price = number(fill["sz"]), number(fill["px"])
                if size <= 0 or price <= 0 or (plan["buy"] and price > number(plan["price"])) or (not plan["buy"] and price < number(plan["price"])):
                    raise ValueError()
                filled += size
                notional += size * price
            if filled > number(plan["size"]): raise ValueError()
            if notional > number(plan["maxNotional"]) * (1 + number(plan["slippageBps"]) / 10000 if plan["reduceOnly"] else 1):
                raise ValueError()
            terminal = state in {"filled", "canceled", "rejected", "marginCanceled", "reduceOnlyCanceled", "openInterestCapCanceled", "selfTradeCanceled", "siblingFilledCanceled", "delistedCanceled", "scheduledCancel", "tickRejected", "minTradeNtlRejected", "perpMarginRejected", "reduceOnlyRejected", "badAloPxRejected", "iocCancelRejected", "badTriggerPxRejected", "marketOrderNoLiquidityRejected", "positionIncreaseAtOpenInterestCapRejected", "positionFlipAtOpenInterestCapRejected", "tooAggressiveAtOpenInterestCapRejected", "openInterestIncreaseRejected", "insufficientSpotBalanceRejected", "oracleRejected", "perpMaxPositionRejected"}
            if state == "filled" and filled != number(plan["size"]):
                return {"known": True, "terminal": False, "filled": str(filled), "oid": order["oid"]}
            return {"known": True, "terminal": terminal, "filled": str(filled), "oid": order["oid"]}
        except (KeyError, TypeError, ValueError):
            raise Stop("ORDER_EVIDENCE_INVALID", "Order/fill evidence does not bind to the durable intent. Review the journal; do not resubmit.") from None

    def verify_activity(self, cloids, started_ms):
        """A matching net size cannot excuse another process's close/reopen."""
        _, _, Cloid = sdk_types()
        account = self.access.scope["tradingAccount"]
        allowed = set()
        for cloid in cloids:
            value = self.read(self.info.query_order_by_cloid, account, Cloid.from_str(cloid))
            if not isinstance(value, dict):
                raise Stop("ORDER_EVIDENCE_INVALID", "Current client-order evidence is unavailable.")
            if value.get("status") == "unknownOid": continue
            try:
                order = value["order"]["order"]
                if value.get("status") != "order" or order.get("cloid") != cloid or type(order.get("oid")) is not int:
                    raise ValueError()
                allowed.add(order["oid"])
            except (KeyError, TypeError, ValueError):
                raise Stop("ORDER_EVIDENCE_INVALID", "Client-order IDs could not be bound to public order IDs.") from None
        fills = self.read(self.info.user_fills_by_time, account, started_ms, int(time.time() * 1000))
        if not isinstance(fills, list) or len(fills) >= 2000:
            raise Stop("ACCOUNT_ACTIVITY_UNVERIFIED", "The bounded account-wide fill history is unavailable.")
        seen = set()
        for fill in fills:
            if not isinstance(fill, dict) or type(fill.get("oid")) is not int or type(fill.get("tid")) is not int or fill["tid"] in seen or type(fill.get("time")) is not int or fill["time"] < started_ms:
                raise Stop("ACCOUNT_ACTIVITY_UNVERIFIED", "Account-wide fill history is inconsistent.")
            seen.add(fill["tid"])
            if fill["oid"] not in allowed:
                raise Stop("ACCOUNT_ACTIVITY_CONFLICT", "Another order filled after this intent began. Matching net position size is insufficient; no unrelated exposure will be reduced or called complete.")

    def exchange(self, expires_ms=None, before_sign=None):
        self.access.verify()
        _, Exchange, _ = sdk_types()
        meta, spot_meta = self.read(self.info.meta), self.read(self.info.spot_meta)
        if before_sign is not None: before_sign()
        if expires_ms is not None and int(time.time() * 1000) >= expires_ms:
            raise Stop("DEADLINE_EXPIRED", "The retained order deadline expired before private signing. No order was submitted; reconcile the original intent.")
        # SDK 0.24.0 debug logging includes signed payloads. This dedicated
        # runner disables Python logging before constructing a signing runtime.
        logging.disable(logging.CRITICAL)
        try:
            exchange = Exchange(self.access.signer(), API, account_address=self.access.scope["tradingAccount"], vault_address=None,
                                meta=meta, spot_meta=spot_meta, timeout=5)
            exchange.set_expires_after(expires_ms)
            return exchange
        except Exception:
            raise Stop("SIGNER_UNAVAILABLE", "The local signing runtime could not initialize. Keep the retained key private and review its path/permissions.") from None

    def order_preflight(self, plan, expires_ms):
        current = self.snapshot(plan["market"])
        size, price = number(plan["size"]), number(plan["price"])
        expected = size * (-1 if plan["buy"] else 1)
        if current["orders"] or (plan["reduceOnly"] and (set(current["positions"]) != {plan["market"]} or current["positions"][plan["market"]] != expected)) or (not plan["reduceOnly"] and current["positions"]):
            raise Stop("ACCOUNT_CONFLICT", "Account state changed before submission. No unrelated exposure will be modified.")
        bound = (current["ask"] if plan["buy"] else current["bid"]) * (1 + number(plan["slippageBps"]) / 10000 if plan["buy"] else 1 - number(plan["slippageBps"]) / 10000)
        exposure = size * max(current["mark"], price)
        maximum = number(plan["maxNotional"]) * (1 + number(plan["slippageBps"]) / 10000 if plan["reduceOnly"] else 1)
        if (plan["buy"] and price > bound) or (not plan["buy"] and price < bound) or exposure > maximum or int(time.time() * 1000) >= expires_ms:
            raise Stop("MARKET_CHANGED", "The market or deadline changed beyond the retained intent. Reconcile it; no order was submitted and no budget was increased.")
        if not plan["reduceOnly"] and (current["taker_fee"] > number("0.001") or current["perp_available"] < exposure * (1 + 2 * current["taker_fee"]) + number("0.05")):
            raise Stop("PERP_COLLATERAL_REQUIRED", "Fresh usable margin is insufficient. No leverage change, deposit or order was submitted.")

    def submit_order(self, plan, cloid, expires_ms):
        if type(expires_ms) is not int or int(time.time() * 1000) >= expires_ms:
            raise Stop("DEADLINE_EXPIRED", "The retained order deadline expired. No signing runtime was constructed; reconcile the original intent.")
        _, _, Cloid = sdk_types()
        self.order_preflight(plan, expires_ms)
        exchange = self.exchange(expires_ms, before_sign=lambda: self.order_preflight(plan, expires_ms))
        if int(time.time() * 1000) >= expires_ms:
            raise Stop("DEADLINE_EXPIRED", "The deadline expired during runtime initialization. No order was submitted; preserve and reconcile the intent.")
        # Exactly one official SDK call. Every response is still reconciled by
        # public order/fill/account reads; exceptions never cause a retry here.
        try:
            response = exchange.order(plan["market"], plan["buy"], float(number(plan["size"])), float(number(plan["price"])),
                                      {"limit": {"tif": "Ioc"}}, reduce_only=plan["reduceOnly"], cloid=Cloid.from_str(cloid))
        except Exception:
            return {"classification": "ambiguous"}
        return classify_submission(response)

    def usdc_token(self):
        meta = self.read(self.info.spot_meta)
        try:
            if any(not isinstance(token, dict) or type(token.get("index")) is not int for token in meta["tokens"]):
                raise ValueError()
            tokens = [token for token in meta["tokens"] if token["index"] == 0]
            if len(tokens) != 1 or tokens[0].get("name") != "USDC" or not re.fullmatch(r"0x[0-9a-fA-F]{32}", tokens[0].get("tokenId", "")):
                raise ValueError()
            return "USDC:" + tokens[0]["tokenId"]
        except (KeyError, TypeError, ValueError):
            raise Stop("COLLATERAL_TOKEN_UNVERIFIED", "Native USDC metadata is unavailable; no transfer can be authorized.") from None

    def submit_collateral(self, amount, nonce, token, market="BTC"):
        from execution import collateral_plan
        def preflight():
            collateral_plan(self.snapshot(market), amount)
            if self.usdc_token() != token:
                raise Stop("COLLATERAL_TOKEN_UNVERIFIED", "Native USDC metadata changed before signing. No transfer was submitted.")
        preflight()
        exchange = self.exchange(before_sign=preflight)
        from hyperliquid.utils.signing import sign_l1_action
        # SDK 0.24.0 has no agentSendAsset convenience method. The documented
        # action uses its public signer and HTTP API; no signing implementation
        # or custom transport is duplicated here. Owner-signed usdClassTransfer
        # and sendAsset are deliberately not used for an approved agent key.
        action = {"type": "agentSendAsset", "destination": self.access.scope["tradingAccount"],
                  "sourceDex": "spot", "destinationDex": "", "token": token,
                  "amount": str(amount), "fromSubAccount": "", "nonce": nonce}
        try:
            signature = sign_l1_action(exchange.wallet, action, None, nonce, None, True)
            exchange.post("/exchange", {"action": action, "nonce": nonce, "signature": signature})
        except Exception:
            return "ambiguous"
        return "submitted_unverified"


def classify_submission(response):
    """Retain a tiny classification, never provider text or signed payloads."""
    if not isinstance(response, dict): return {"classification": "ambiguous"}
    if response.get("status") == "err": return {"classification": "rejected", "code": "EXCHANGE_REJECTED"}
    try:
        statuses = response["response"]["data"]["statuses"]
        if response.get("status") != "ok" or not isinstance(statuses, list) or len(statuses) != 1 or not isinstance(statuses[0], dict):
            return {"classification": "ambiguous"}
        status = statuses[0]
        if "error" in status:
            return {"classification": "rejected", "code": "ORDER_REJECTED"}
        if "filled" in status or "resting" in status:
            return {"classification": "accepted_unverified"}
    except (KeyError, TypeError):
        pass
    return {"classification": "ambiguous"}


def public_candles(market):
    if not re.fullmatch(r"[A-Z][A-Z0-9]{0,19}", market):
        raise Stop("UNSUPPORTED_MARKET", "Choose a native perpetual symbol.")
    info = public_info()
    now = int(time.time() * 1000)
    try:
        universe = info.meta()["universe"]
        if sum(asset.get("name") == market and not asset.get("isDelisted", False) for asset in universe) != 1:
            raise ValueError()
        candles = info.candles_snapshot(market, "1m", now - 60 * 60 * 1000, now)
        closes = [number(candle["c"]) for candle in sorted(candles, key=lambda value: value["t"]) if candle["T"] < now]
        if not 6 <= len(closes) <= 60 or any(value <= 0 for value in closes): raise ValueError()
        fresh(max(candle["T"] for candle in candles), 120000)
        return closes
    except Exception:
        raise Stop("PUBLIC_API_UNAVAILABLE", "Fresh public candle data is unavailable. Use deterministic fixture paper or retry later.") from None
