"""Model providers. `openai` calls the Responses API over HTTPS with the standard library.
`mock` is a local rule-based stand-in used only by offline tests and demos (zero API calls);
its label_config contains "mock-rule-v0" so its output can never be mistaken for a real run.
"""

import json
import os
import random
import re
import socket
import threading
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, message, retryable, status=None, retry_after=None, outcome_unknown=False):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after
        self.outcome_unknown = outcome_unknown  # the request may have been processed and billed


class OpenAIProvider:
    name = "openai"

    def __init__(self, timeout=180, base_url=None):
        self.key = os.environ.get("OPENAI_API_KEY", "")
        if not self.key:
            raise SystemExit("OPENAI_API_KEY is not set. Copy .env.example to .env and add your key "
                             "(or pass --provider mock for the offline stand-in).")
        self.url = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/") + "/responses"
        self.timeout = timeout

    def complete(self, *, role, model, effort, instructions, input_text, max_output_tokens):
        body = {"model": model, "instructions": instructions, "input": input_text,
                "max_output_tokens": max_output_tokens, "store": False}
        if effort:
            body["reasoning"] = {"effort": effort}
        request = urllib.request.Request(
            self.url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
                header_id = response.headers.get("x-request-id")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            retry_after = e.headers.get("retry-after") if e.headers else None
            try:
                retry_after = float(retry_after) if retry_after else None
            except ValueError:
                retry_after = None
            retryable = e.code in (408, 409, 429) or e.code >= 500
            raise ProviderError(f"HTTP {e.code}: {detail}", retryable, status=e.code, retry_after=retry_after,
                                outcome_unknown=e.code >= 500) from None
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            raise ProviderError(f"{type(e).__name__}: {e}", True, outcome_unknown=True) from None

        parts, refused = [], False
        for item in data.get("output", []):
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refused = True
        usage = data.get("usage") or {}
        note = None
        if data.get("status") != "completed":
            note = "status=%s reason=%s" % (data.get("status"), (data.get("incomplete_details") or {}).get("reason"))
        if refused:
            note = (note + "; " if note else "") + "refusal"
        return {
            "text": "".join(parts),
            "request_id": data.get("id") or header_id,
            "note": note,
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens": (usage.get("output_tokens_details") or {}).get("reasoning_tokens", 0),
            "usage_source": "provider_response" if usage else "missing",
        }


class MockProvider:
    """Deterministic keyword rules plus optional fault injection, e.g.
    MOCK_FAULTS="http429=0.2,timeout=0.05,malformed=0.1,drop=0.1,badquote=0.2,seed=7"."""
    name = "mock"

    RULES = [
        ("login", r"log ?in|password|sign ?in|logged out", 4),
        ("crash", r"crash|won'?t open|not opening|stuck on|freez", 4),
        ("offline", r"offline|download", 3),
        ("paywall", r"premium.*(skip|choose|shuffle|song|feature)|(skip|choose|shuffle|feature).*premium|force.*premium", 3),
        ("charges", r"charged|refund", 5),
        ("price", r"expensive|price", 2),
        ("ads", r"\bads?\b|advert|\badds\b", 2),
        ("lyrics", r"lyric", 3),
        ("stops", r"stop|pause|not play|doesn'?t play|can'?t play", 3),
        ("connect", r"internet|bluetooth|connect", 3),
        ("ui", r"update|layout|interface|home ?screen", 2),
        ("recs", r"recommend|suggest", 2),
        ("support", r"support|customer service", 3),
        ("boycott", r"boycott|sweden|quran", 1),
    ]
    NEGATIVE = r"worst|bad|hate|useless|trash|poor|terrible|annoy|disappoint|pathetic|horrible|sucks|can'?t|cannot|not work|problem|issue|bug|fix|too many|too much"
    LEAVING = r"uninstall|delet(e|ing) (this|the) app|cancel(l?ing)? (my )?(premium|subscription)|switch(ing)? to|bye spotify|leaving"
    TOPIC_OF = None  # filled lazily from labels

    def __init__(self, faults=None):
        spec = faults if faults is not None else os.environ.get("MOCK_FAULTS", "")
        self.faults = {}
        for pair in filter(None, spec.split(",")):
            key, value = pair.split("=")
            self.faults[key.strip()] = float(value)
        self.rng = random.Random(int(self.faults.pop("seed", 0)))
        self.lock = threading.Lock()
        self.counter = 0

    def _roll(self, name):
        p = self.faults.get(name, 0)
        with self.lock:
            return p > 0 and self.rng.random() < p

    def _label(self, text):
        low = text.lower()
        code, severity = "general", 2
        for candidate, pattern, sev in self.RULES:
            if re.search(pattern, low):
                code, severity = candidate, sev
                break
        negative = bool(re.search(self.NEGATIVE, low)) or code in ("login", "crash", "charges", "paywall")
        if code == "boycott":
            intent, severity, sentiment = "unclear", 1, -1
        elif re.search(self.LEAVING, low):
            intent, sentiment = "cancellation", -2
        elif negative:
            intent, sentiment = "complaint", -1
        elif not re.search(r"[a-zA-Z]{3}", text):
            code, intent, severity, sentiment = "noise", "unclear", 1, 0
        else:
            intent, severity, sentiment = "praise", 1, 2
        return code, intent, sentiment, severity

    def complete(self, *, role, model, effort, instructions, input_text, max_output_tokens):
        if self._roll("http429"):
            raise ProviderError("HTTP 429: mock rate limit", True, status=429, retry_after=0.01)
        if self._roll("timeout"):
            raise ProviderError("TimeoutError: mock timeout", True, outcome_unknown=True)
        if self._roll("http400"):
            raise ProviderError("HTTP 400: mock rejected input", False, status=400)
        from . import labels
        if role in ("enrich", "verify"):
            lines = []
            for line in input_text.splitlines():
                ref, _, payload = line.partition("\t")
                text = json.loads(payload)
                code, intent, sentiment, severity = self._label(text)
                if self._roll("drop"):
                    continue
                if role == "verify":
                    lines.append(f"{ref}|{labels.SUBTOPICS[code][0]}|{intent}|{severity}")
                    continue
                quote = ""
                if ref.endswith("q"):
                    quote = "this phrase is not in the review" if self._roll("badquote") else " ".join(text.split()[:8])
                lines.append(f"{ref.rstrip('q')}|{code}|{intent}|{sentiment}|{severity}|0|{quote}")
            text_out = "\n".join(lines)
            if self._roll("malformed"):
                text_out = "Sure! Here are the labels you asked for:\n```\n" + text_out.replace("|", ";") + "\n```"
        elif role == "group":
            pack = json.loads(input_text)
            text_out = json.dumps({"name": "Mock issue " + pack["issue_id"],
                                   "description": "Offline stand-in description for " + pack["definition"],
                                   "off_topic_ids": []})
        else:  # memo
            pack = json.loads(input_text)
            top = pack["ranking"][0]
            claim = {c["metric"]: "{{%s}}" % c["claim_id"] for c in pack["claims"] if c["issue_id"] == top["issue_id"]}
            quote = next((q for quotes in pack["evidence"].values() for q in quotes), None)
            cite = f' One customer wrote: "{quote["quote"][:80]}" [review:{quote["review_id"]}].' if quote else ""
            text_out = ("# Decision memo: next-quarter product priority\n\n## Recommendation\n"
                        f"OFFLINE MOCK MEMO (no model was called). The top baseline issue is {top['name']} "
                        f"`{top['issue_id']}` with {claim['complaint_count']} complaint records, mean severity "
                        f"{claim['mean_severity']} and priority score {claim['priority_score']}.{cite}\n\n"
                        "## Limitations\nMock output for pipeline tests only.\n")
        with self.lock:
            self.counter += 1
            n = self.counter
        return {"text": text_out, "request_id": f"mock-{os.getpid()}-{n}", "note": None,
                "input_tokens": (len(instructions) + len(input_text)) // 4, "cached_input_tokens": 0,
                "output_tokens": len(text_out) // 4, "reasoning_tokens": 0, "usage_source": "mock_estimate"}


def make_provider(cfg):
    if cfg.provider == "mock":
        return MockProvider()
    if cfg.provider == "openai":
        return OpenAIProvider(timeout=cfg.limits["request_timeout_s"])
    raise SystemExit(f"Unknown provider {cfg.provider!r}")
