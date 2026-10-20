"""Public strategy configuration shared by checks and runtime composition."""
from pathlib import Path
import json
import re

from models import INTERVALS
from risk import Stop, number
from strategy import BandBreakout

DEFAULT_CONFIG = "config.public.example.json"


def load_config(path):
    path = Path(path)
    # Public configuration is deliberately named; never load generic config.json
    # or any environment/credential file through this public config interface.
    if not path.name.endswith(".public.json") and path.name != DEFAULT_CONFIG:
        raise Stop("PUBLIC_CONFIG_REQUIRED", "Use config.public.example.json or a reviewed *.public.json configuration.")
    try:
        with path.open(encoding="utf-8") as stream:
            raw = stream.read(16385)
        if len(raw.encode()) > 16384:
            raise ValueError()
        config = json.loads(raw)
        required = {"schema", "market", "interval", "strategy", "execution", "reporting"}
        if set(config) != required or config["schema"] != "2n20-runtime-config-v3":
            raise ValueError()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,19}", config["market"]) or config["interval"] not in INTERVALS:
            raise ValueError()
        if set(config["strategy"]) != {"window", "multiplier"}:
            raise ValueError()
        BandBreakout(**config["strategy"])
        limits = config["execution"]
        if set(limits) != {"capitalMultiple", "targetNotional", "childNotional", "slippageBps", "chaseSeconds", "maxChildren", "requoteSeconds", "iocAfterSeconds", "depthFraction", "dailyVolumeFraction"}:
            raise ValueError()
        if not 0 < number(limits["childNotional"]) <= number(limits["targetNotional"]) <= 1_000_000:
            raise ValueError()
        if not 0 < number(limits["capitalMultiple"]) <= 100:
            raise ValueError()
        for key in ("depthFraction", "dailyVolumeFraction"):
            if limits[key] is not None and not 0 < number(limits[key]) <= 1:
                raise ValueError()
        for key, low, high in (("slippageBps", 1, 500), ("chaseSeconds", 5, 3600), ("maxChildren", 1, 100), ("requoteSeconds", 1, 60)):
            if type(limits[key]) is not int or not low <= limits[key] <= high:
                raise ValueError()
        if limits["iocAfterSeconds"] is not None and (type(limits["iocAfterSeconds"]) is not int or not 1 <= limits["iocAfterSeconds"] < limits["chaseSeconds"]):
            raise ValueError()
        if set(config["reporting"]) != {"telegram"} or type(config["reporting"]["telegram"]) is not bool:
            raise ValueError()
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise Stop("PUBLIC_CONFIG_INVALID", "Use the reviewed public example schema with explicit strategy and execution limits.") from None
