"""Shared label taxonomy, record validation and deterministic extraction. No model calls here.

The eight topics, five intents and the 1-5 severity scale are the course's common labels
(GRADING_CONTRACT.md). The 22 codes below are this project's custom subtopics: each code belongs
to exactly one topic, so the enricher picks one code and code derives the topic.
"""

import re

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")
PROBLEM_INTENTS = ("complaint", "cancellation")
SCHEMA_VERSION = "s1"

# code -> (topic, meaning shown to the model)
SUBTOPICS = {
    "login": ("access", "cannot log in or sign up; password, verification or 'logged out' problems"),
    "account": ("access", "account hacked, banned, deleted, lost or stuck in the wrong country/profile"),
    "ads": ("usability", "too many, too long, too loud or badly timed ads"),
    "ui": ("usability", "layout, navigation, redesigns, home screen, notifications, widgets, removed interface features"),
    "controls": ("usability", "shuffle, skip, repeat, queue, playlist editing, autoplay or voice controls that work badly, NOT described as a Premium-only restriction"),
    "crash": ("playback", "app crashes, freezes, will not open or is stuck loading"),
    "stops": ("playback", "music or podcasts will not play, stop, pause, buffer, skip or restart by themselves"),
    "connect": ("playback", "'no internet connection' errors, or playback via bluetooth, car, cast, speaker, watch, another device or a voice assistant"),
    "audio": ("playback", "sound quality or volume problems"),
    "perf": ("playback", "lag, slowness, battery, storage or mobile-data use"),
    "offline": ("downloads", "downloads fail, disappear or will not play offline"),
    "missing": ("catalog", "songs, artists, podcasts, videos or languages missing, removed or unavailable"),
    "recs": ("catalog", "recommendations, discovery, search, generated mixes, unwanted suggested songs"),
    "lyrics": ("catalog", "lyrics missing, wrong or unavailable"),
    "library": ("catalog", "praise for how much or what kind of music/podcasts is available (\"has every song\", \"huge selection\"); not for \"great music\" in general"),
    "paywall": ("billing", "a feature is locked behind Premium or limited on the free tier (choosing a song, skips, rewind, repeat, turning shuffle off), or the app pressures users to buy Premium"),
    "price": ("billing", "price, price increases, plans, value for money"),
    "charges": ("billing", "wrong or repeated charges, refunds, payment failures, Premium not activating after paying"),
    "support": ("support", "contacting customer support and the response received"),
    "general": ("other", "general praise or criticism with no specific feature"),
    "boycott": ("other", "political, religious, ethical or boycott statements that are not about how the product works"),
    "noise": ("other", "meaningless, unrelated or unintelligible text"),
}
CODES = tuple(SUBTOPICS)

# Reviews at or below this length use their complete original text as the evidence quote
# (deterministic, no output tokens). Longer reviews need a model-selected exact substring.
SHORT_QUOTE_MAX = 80
MAX_TEXT_CHARS = 2000  # longest source text is 1,693 chars; the cap only guards arbitrary inputs

# Deterministic entity lexicon: canonical feature/competitor name -> regex of explicit surface terms.
# Every entity is a term literally present in the text, so there are no unsupported additions.
ENTITY_VERSION = "e1"
_ENTITY_TERMS = {
    "ads": r"\bads?\b|\badvert\w*|\bcommercials?\b",
    "premium": r"\bpremium\b",
    "free tier": r"\bfree (?:version|tier|plan|users?|account)\b|\bnon[- ]?premium\b",
    "subscription": r"\bsubscri\w+",
    "price": r"\bprice\w*|\bexpensive\b|\bcost(?:s|ly)?\b",
    "payment": r"\bpayments?\b|\bcharged?\b|\bbilling\b|\brefund\w*",
    "shuffle": r"\bshuffl\w+",
    "skip": r"\bskip\w*",
    "repeat": r"\brepeat\w*|\bloop\w*",
    "rewind": r"\brewind\w*|\bseek\w*",
    "queue": r"\bqueue\w*",
    "playlist": r"\bplay ?lists?\b",
    "lyrics": r"\blyrics?\b",
    "podcast": r"\bpodcasts?\b",
    "audiobook": r"\baudio ?books?\b",
    "recommendations": r"\brecommend\w*|\bsuggest\w*|\bdiscover\w*",
    "search": r"\bsearch\w*",
    "download": r"\bdownload\w*",
    "offline": r"\boffline\b",
    "login": r"\blog(?:ged|ging)? ?(?:in|out)\b|\blogin\b|\bsign(?:ed|ing)? ?(?:in|up)\b",
    "password": r"\bpassword\b",
    "account": r"\baccounts?\b",
    "update": r"\bupdat\w+",
    "crash": r"\bcrash\w*|\bfreez\w*|\bfrozen?\b",
    "connection": r"\binternet\b|\bconnection\b|\bwi-?fi\b|\boffline mode\b",
    "bluetooth": r"\bbluetooth\b",
    "car": r"\bcar\b|\bandroid auto\b|\bcarplay\b",
    "sound quality": r"\b(?:sound|audio|music) quality\b",
    "volume": r"\bvolume\b",
    "battery": r"\bbattery\b",
    "storage": r"\bstorage\b",
    "interface": r"\bui\b|\binterface\b|\blayout\b|\bhome ?(?:screen|page)\b",
    "widget": r"\bwidgets?\b",
    "notifications": r"\bnotifications?\b",
    "support": r"\b(?:customer )?support\b|\bcustomer (?:service|care)\b",
    "youtube": r"\byou ?tube\b|\byt music\b",
    "apple music": r"\bapple music\b|\bitunes\b",
    "amazon music": r"\bamazon(?: music)?\b",
    "pandora": r"\bpandora\b",
    "wynk": r"\bwynk\b",
    "jiosaavn": r"\bjio ?saavn\b|\bsaavn\b",
    "gaana": r"\bgaana\b",
    "deezer": r"\bdeezer\b",
    "tidal": r"\btidal\b",
    "soundcloud": r"\bsound ?cloud\b",
    "resso": r"\bresso\b",
}
_ENTITY_RX = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _ENTITY_TERMS.items()]


def extract_entities(text):
    return [name for name, rx in _ENTITY_RX if rx.search(text)]


def locate_quote(text, quote):
    """Return the exact source substring matching a model quote, or None.

    Exact match first; otherwise tolerate differences in whitespace, case and surrounding
    quote marks/ellipses, and return the span as it appears in the source so that the stored
    evidence is always a true substring.
    """
    if not quote or not quote.strip():
        return None
    if quote in text:
        return quote
    cleaned = quote.strip().strip('"“”\'').strip().strip(".…").strip()
    if not cleaned:
        return None
    if cleaned in text:
        return cleaned
    tokens = cleaned.split()
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    for flags in (0, re.IGNORECASE):
        m = re.search(pattern, text, flags)
        if m and m.group(0).strip():
            return m.group(0)
    return None


def fallback_quote(text, limit=200):
    """Deterministic quote when a model quote cannot be verified: the opening of the source text."""
    head = text[:limit]
    m = re.search(r"[.!?\n]", head[20:])
    quote = head[:20 + m.end()] if m else head
    return quote if quote.strip() else text


def validate_record(rec, source_text=None):
    """Contract schema for a completed record. Returns a list of problems (empty when valid)."""
    problems = []
    if rec.get("topic") not in TOPICS:
        problems.append("topic")
    if rec.get("intent") not in INTENTS:
        problems.append("intent")
    sev = rec.get("severity")
    if type(sev) is not int or not 1 <= sev <= 5:
        problems.append("severity")
    sent = rec.get("sentiment")
    if type(sent) not in (int, float) or not -1 <= sent <= 1:
        problems.append("sentiment")
    if type(rec.get("needs_review")) is not bool:
        problems.append("needs_review")
    ents = rec.get("entities")
    if not isinstance(ents, list) or not all(isinstance(x, str) and x.strip() for x in ents):
        problems.append("entities")
    quote = rec.get("evidence_quote")
    if not isinstance(quote, str) or not quote.strip():
        problems.append("evidence_quote")
    elif source_text is not None and quote not in source_text:
        problems.append("quote_not_in_source")
    if not isinstance(rec.get("label_config"), str) or not rec.get("label_config", "").strip():
        problems.append("label_config")
    return problems
