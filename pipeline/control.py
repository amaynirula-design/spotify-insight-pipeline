"""Orchestrator controls shared by every model role: one spend ledger with reservations, a global
request/token rate limiter, bounded retries with backoff and jitter, and the per-attempt call log."""

import random
import threading
import time

from .config import cost_usd, load_rates
from .providers import ProviderError


class BudgetExceeded(Exception):
    pass


class Deferred(Exception):
    """Transient failures exhausted the retry allowance; the work stays pending for a later run."""


class Rejected(Exception):
    """The provider refused the request permanently (non-retryable HTTP error)."""


class Budget:
    """Spent + reserved + next reservation must stay under the cap, or no new work is admitted.
    `spent` starts from everything already logged in this state DB, so restarts cannot reset it."""

    def __init__(self, cap_usd, already_spent=0.0):
        self.cap, self.spent, self.reserved = cap_usd, already_spent, 0.0
        self.lock = threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                raise BudgetExceeded(f"spent ${self.spent:.4f} + reserved ${self.reserved:.4f} + next ${amount:.4f} "
                                     f"> cap ${self.cap:.2f}")
            self.reserved += amount

    def settle(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved
            self.spent += actual


class RateLimiter:
    """Sliding 60-second window shared by all workers for requests and (estimated) tokens."""

    def __init__(self, rpm, tpm, stop=None):
        self.rpm, self.tpm, self.stop = rpm, tpm, stop
        self.events, self.lock = [], threading.Lock()

    def acquire(self, tokens):
        while True:
            with self.lock:
                now = time.time()
                self.events = [e for e in self.events if now - e[0] < 60]
                if len(self.events) < self.rpm and sum(e[1] for e in self.events) + tokens <= max(self.tpm, tokens):
                    self.events.append((now, tokens))
                    return
                wait = 60 - (now - self.events[0][0]) + 0.05
            if self.stop is not None and self.stop.wait(min(wait, 1.0)):
                return
            elif self.stop is None:
                time.sleep(min(wait, 1.0))


class Runner:
    def __init__(self, db, cfg, provider, input_id, invocation, phase="initial", stop=None, budget_usd=None, rates=None):
        self.db, self.cfg, self.provider = db, cfg, provider
        self.input_id, self.invocation, self.phase = input_id, invocation, phase
        self.stop = stop or threading.Event()
        self.rates = rates or load_rates()
        row = db.one("SELECT COALESCE(SUM(CASE WHEN outcome='succeeded' THEN cost_usd WHEN outcome='unknown' "
                     "OR (outcome='failed' AND cost_usd IS NULL) THEN reserved_usd ELSE 0 END), 0) AS spent FROM calls")
        self.budget = Budget(cfg.limits["budget_usd"] if budget_usd is None else budget_usd, row["spent"] or 0.0)
        self.limiter = RateLimiter(cfg.limits["requests_per_minute"], cfg.limits["tokens_per_minute"], self.stop)
        self.sleep = time.sleep
        self.backoff_base = cfg.limits.get("backoff_base_seconds", 2.0)

    def call(self, role, input_text, review_ids, attempt_kind="first", worker=0, on_success=None, artifacts=None, save_input=True):
        role_cfg, limits = self.cfg.role(role), self.cfg.limits
        model, tier = role_cfg["model"], self.cfg.tier
        instructions = self.cfg.instructions(role)
        est_in = (len(instructions) + len(input_text)) // 3 + 50
        worst = est_in * self.rates[(model, tier, "input_uncached")] + role_cfg["max_output_tokens"] * self.rates[(model, tier, "output")]
        for attempt in range(limits["max_transient_retries"] + 1):
            self.budget.reserve(worst)
            self.limiter.acquire(est_in + role_cfg["max_output_tokens"] // 2)
            call_id = self.db.open_call(
                invocation=self.invocation, input_id=self.input_id, role=role, phase=self.phase,
                attempt_kind=attempt_kind if attempt == 0 else f"{attempt_kind}+transient{attempt}",
                provider=self.provider.name, model=model, effort=role_cfg["effort"], tier=tier,
                stage_config=self.cfg.stage_config(role), review_ids=list(review_ids), n_items=len(review_ids),
                worker=worker, reserved_usd=worst, artifacts=artifacts)
            started = time.time()
            try:
                resp = self.provider.complete(role=role, model=model, effort=role_cfg["effort"], instructions=instructions,
                                              input_text=input_text, max_output_tokens=role_cfg["max_output_tokens"])
            except ProviderError as e:
                # Known rejections cost nothing; a timeout or 5xx may still be billed, so the
                # reservation is kept in the ledger as a labelled estimate (cost_usd stays NULL).
                with self.db.tx() as c:
                    self.db.close_call(c, call_id, request_id=f"local-{call_id}", outcome="failed", error=str(e)[:500],
                                       duration_s=time.time() - started, usage_source="none",
                                       cost_usd=None if e.outcome_unknown else 0.0)
                self.budget.settle(worst, worst if e.outcome_unknown else 0.0)
                if not e.retryable:
                    raise Rejected(str(e)) from None
                if attempt == limits["max_transient_retries"]:
                    raise Deferred(str(e)) from None
                delay = e.retry_after if e.retry_after is not None else min(60.0, self.backoff_base * 2 ** attempt)
                self.sleep(delay * (0.5 + random.random()))
                continue
            has_usage = resp["input_tokens"] is not None and resp["output_tokens"] is not None
            cost = cost_usd(resp, model, tier, self.rates) if has_usage else None
            with self.db.tx() as c:
                self.db.close_call(
                    c, call_id, request_id=resp["request_id"] or f"local-{call_id}", outcome="succeeded", error=resp["note"],
                    input_tokens=resp["input_tokens"], cached_input_tokens=resp["cached_input_tokens"],
                    output_tokens=resp["output_tokens"], reasoning_tokens=resp["reasoning_tokens"],
                    usage_source=resp["usage_source"], duration_s=time.time() - started, cost_usd=cost)
                c.execute("INSERT OR REPLACE INTO call_io(call_id, input_text, output_text) VALUES(?,?,?)",
                          (call_id, input_text if save_input else None, resp["text"]))
                if on_success:
                    on_success(c, resp, call_id)
            self.budget.settle(worst, cost if cost is not None else worst)
            return resp, call_id
