"""Run configuration: models, prompt versions, limits and the label_config identity string."""

import csv
import json
import math
from pathlib import Path

from . import labels
from .util import ROOT, sha_text

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
MOCK_MODEL = "mock-rule-v0"


class Config:
    def __init__(self, path=None, provider=None, overrides=None):
        self.raw = json.loads(Path(path or ROOT / "pipeline_config.json").read_text(encoding="utf-8"))
        if provider:
            self.raw["provider"] = provider
        for dotted, value in (overrides or {}).items():
            node = self.raw
            *parents, leaf = dotted.split(".")
            for key in parents:
                node = node[key]
            node[leaf] = value
        if self.provider == "mock":
            for role in self.raw["roles"].values():
                role["model"], role["effort"] = MOCK_MODEL, "none"
        for name, role in self.raw["roles"].items():
            if role["model"] not in self.raw["model_allowlist"]:
                raise ValueError(f"Model {role['model']!r} for role {name!r} is not in model_allowlist")
        if self.raw["limits"]["max_fallback_fraction"] != 0:
            raise ValueError("No stronger-model fallback is implemented; max_fallback_fraction must stay 0.")

    @property
    def provider(self):
        return self.raw["provider"]

    @property
    def tier(self):
        return self.raw["tier"]

    @property
    def limits(self):
        return self.raw["limits"]

    def role(self, name):
        return self.raw["roles"][name]

    def instructions(self, name):
        text = (PROMPT_DIR / (self.role(name)["prompt"] + ".txt")).read_text(encoding="utf-8")
        if name == "enrich":
            code_list = "\n".join(f"{code} = {topic}: {meaning}" for code, (topic, meaning) in labels.SUBTOPICS.items())
            text = text.replace("{CODE_LIST}", code_list)
        return text

    def _identity(self, name, extra=""):
        role = self.role(name)
        digest = sha_text(self.instructions(name) + extra)[:8]
        return f"{role['model']}@{role['effort']}|{role['prompt']}|schema-{labels.SCHEMA_VERSION}|{digest}"

    @property
    def label_config(self):
        """Everything that can change an enrichment label: model, effort, prompt text, taxonomy,
        schema and the deterministic quote/entity rules. Any change yields a new cache namespace."""
        extra = f"|q{labels.SHORT_QUOTE_MAX}|{labels.ENTITY_VERSION}|{sha_text(repr(sorted(labels._ENTITY_TERMS.items())))[:6]}"
        return self._identity("enrich", extra)

    def stage_config(self, name):
        return self.label_config if name == "enrich" else self._identity(name)

    def verify_sample_size(self, n_completed):
        s = self.raw["verify_sample"]
        return min(n_completed, max(s["min"], min(s["max"], math.ceil(s["fraction"] * n_completed))))


def load_rates(path=None):
    """rates.csv -> {(model, tier, item): price per single unit}. Prices stay editable in the CSV."""
    rates = {}
    with Path(path or ROOT / "cost" / "rates.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rates[(row["model"], row["tier"], row["item"])] = float(row["price"]) / float(row["per_units"])
    return rates


BILLING_ITEMS = ("input_uncached", "input_cached", "output")


def billed_units(usage):
    """Mutually exclusive billing items. Cached input is subtracted from total input; reasoning
    tokens are already inside output_tokens and are not added again."""
    cached = usage.get("cached_input_tokens") or 0
    return {
        "input_uncached": max((usage.get("input_tokens") or 0) - cached, 0),
        "input_cached": cached,
        "output": usage.get("output_tokens") or 0,
    }


def cost_usd(usage, model, tier, rates):
    units = billed_units(usage)
    return sum(units[item] * rates[(model, tier, item)] for item in BILLING_ITEMS)
