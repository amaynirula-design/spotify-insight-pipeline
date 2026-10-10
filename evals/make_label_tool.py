#!/usr/bin/env python3
"""Build evals/label_golden.html: a local, offline page for hand-labelling the 50 golden reviews.

    python evals/make_label_tool.py && open evals/label_golden.html

The page shows only review text (no star rating, no model output). Labels stay in the browser
until you press "Download CSV"; save that file as evals/golden_50_labeled.csv.
"""

import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "data" / "golden_50_to_label.csv"

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Golden 50 labelling</title>
<style>
  :root { --bg:#f7f7f5; --card:#fff; --ink:#1c1c1a; --muted:#6b6b66; --line:#dcdcd6; --accent:#1a6b4a; --warn:#b3541e; }
  @media (prefers-color-scheme: dark) { :root { --bg:#161615; --card:#1f1f1d; --ink:#ecece8; --muted:#9a9a93; --line:#35352f; --accent:#5fc79a; --warn:#e39a6a; } }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; }
  header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; z-index:2; }
  header strong { font-size:16px; } #progress { color:var(--muted); }
  button { font:inherit; padding:6px 12px; border-radius:6px; border:1px solid var(--line); background:var(--card); color:var(--ink); cursor:pointer; }
  button.primary { background:var(--accent); color:#fff; border-color:var(--accent); }
  main { max-width:860px; margin:0 auto; padding:16px; }
  details.rubric { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:10px 14px; margin-bottom:16px; }
  details.rubric table { border-collapse:collapse; width:100%; font-size:13.5px; } details.rubric td { border-top:1px solid var(--line); padding:4px 6px; vertical-align:top; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:14px; margin-bottom:14px; }
  .card.done { border-left:4px solid var(--accent); } .card.todo { border-left:4px solid var(--warn); }
  .num { color:var(--muted); font-size:12.5px; } .text { white-space:pre-wrap; font-size:16px; margin:6px 0 10px; overflow-wrap:anywhere; }
  .row { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:5px 0; } .row > span.k { width:86px; color:var(--muted); font-size:13px; }
  label.opt { border:1px solid var(--line); border-radius:999px; padding:2px 10px; cursor:pointer; font-size:13.5px; user-select:none; }
  label.opt input { display:none; } label.opt:has(input:checked) { background:var(--accent); color:#fff; border-color:var(--accent); }
  input[type=text] { flex:1; min-width:200px; font:inherit; padding:4px 8px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--ink); }
  .bad { color:var(--warn); font-size:12.5px; }
</style></head><body>
<header><strong>Golden 50: hand labels</strong><span id="progress"></span>
  <button class="primary" id="download">Download CSV</button><button id="next">Jump to next unlabelled</button></header>
<main>
<details class="rubric"><summary><b>Shared label definitions</b> (from GRADING_CONTRACT.md). Label the text only; stars are deliberately hidden.</summary>
<table>
<tr><td><b>access</b></td><td>Login, signup, password or account access</td></tr>
<tr><td><b>usability</b></td><td>Navigation, controls, layout, queue/playlist management, ad interruptions</td></tr>
<tr><td><b>playback</b></td><td>Playback failure, crashes, lag, connection failures, audio quality, resource use</td></tr>
<tr><td><b>downloads</b></td><td>Downloading, saved music, offline listening, disappearing downloads</td></tr>
<tr><td><b>catalog</b></td><td>Missing songs/artists, search/discovery, recommendations, lyrics availability</td></tr>
<tr><td><b>billing</b></td><td>Price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls go here</td></tr>
<tr><td><b>support</b></td><td>Contacting support and the support response</td></tr>
<tr><td><b>other</b></td><td>General praise/criticism, unrelated content, or no supported specific topic</td></tr>
<tr><td colspan="2">Topic: the problem with the highest severity; on a tie the first specific problem mentioned. Positive review: first specific praised feature; general praise is other. A paid-plan mention alone is not billing.</td></tr>
<tr><td><b>intent</b></td><td>First that applies: <b>cancellation</b> (explicitly leaving/uninstalling/cancelling or threatening to) → <b>complaint</b> (negative experience, including mixed) → <b>request</b> (desired change, no failure) → <b>praise</b> → <b>unclear</b> (bare boycott slogans, unrelated or meaningless text).</td></tr>
<tr><td><b>severity</b></td><td>1 no reported problem (praise, unclear, pure request) · 2 dislike, generic criticism, minor annoyance · 3 degraded or restricted function, some use remains · 4 core task clearly blocked (cannot log in / play) · 5 explicit serious financial, privacy or data harm. Stars, anger or cancellation alone do not raise severity.</td></tr>
<tr><td><b>sentiment</b></td><td>Tone of the text from −1 (very negative) to 1 (very positive); 0 is neutral or mixed.</td></tr>
<tr><td><b>evidence</b></td><td>An exact piece of the review that supports your topic/severity. Select text in the review and press “Use selection”, or use the whole review when it is short.</td></tr>
<tr><td><b>needs review</b></td><td>Tick when you find the case genuinely ambiguous or missing context.</td></tr>
</table></details>
<div id="cards"></div>
</main>
<script>
const REVIEWS = __REVIEWS__;
const COLUMNS = __COLUMNS__;
const TOPICS = ["access","usability","playback","downloads","catalog","billing","support","other"];
const INTENTS = ["cancellation","complaint","request","praise","unclear"];
const SENT = ["-1","-0.5","0","0.5","1"], SEV = ["1","2","3","4","5"];
const KEY = "golden50-labels-v1";
let state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };
const lab = id => (state[id] = state[id] || {topic:"",intent:"",sentiment:"",severity:"",entities:"",evidence_quote:"",needs_review:false});
const complete = (r) => { const l = lab(r.review_id); return l.topic && l.intent && l.sentiment !== "" && l.severity && l.evidence_quote.trim() && r.review_text.includes(l.evidence_quote); };
function el(tag, attrs, ...kids) { const n = document.createElement(tag); for (const k in attrs || {}) { if (k === "class") n.className = attrs[k]; else n.setAttribute(k, attrs[k]); } kids.forEach(c => n.append(c)); return n; }
function options(r, field, values) {
  const row = el("div", {class:"row"}, el("span", {class:"k"}, field));
  values.forEach(v => { const input = el("input", {type:"radio", name:field + r.review_id, value:v}); input.checked = lab(r.review_id)[field] === v;
    input.addEventListener("change", () => { lab(r.review_id)[field] = v; save(); refresh(r); }); row.append(el("label", {class:"opt"}, input, v)); });
  return row;
}
const cards = document.getElementById("cards");
function refresh(r) {
  const card = document.getElementById("c" + r.review_id), l = lab(r.review_id);
  card.className = "card " + (complete(r) ? "done" : "todo");
  card.querySelector(".bad").textContent = l.evidence_quote && !r.review_text.includes(l.evidence_quote) ? "Evidence must be an exact piece of the review text." : "";
  document.getElementById("progress").textContent = REVIEWS.filter(complete).length + " / " + REVIEWS.length + " complete";
}
REVIEWS.forEach((r, i) => {
  const l = lab(r.review_id);
  const text = el("div", {class:"text"}, r.review_text);
  const quote = el("input", {type:"text", placeholder:"exact phrase from the review"}); quote.value = l.evidence_quote;
  quote.addEventListener("input", () => { l.evidence_quote = quote.value; save(); refresh(r); });
  const useSel = el("button", {type:"button"}, "Use selection"); const useAll = el("button", {type:"button"}, "Whole review");
  useSel.addEventListener("click", () => { const s = String(window.getSelection()); if (s.trim() && r.review_text.includes(s)) { quote.value = l.evidence_quote = s; save(); refresh(r); } });
  useAll.addEventListener("click", () => { quote.value = l.evidence_quote = r.review_text; save(); refresh(r); });
  const ents = el("input", {type:"text", placeholder:"optional; features/competitors named in the text, separated by ;"}); ents.value = l.entities;
  ents.addEventListener("input", () => { l.entities = ents.value; save(); });
  const flag = el("input", {type:"checkbox"}); flag.checked = !!l.needs_review; flag.addEventListener("change", () => { l.needs_review = flag.checked; save(); });
  cards.append(el("div", {class:"card todo", id:"c" + r.review_id},
    el("div", {class:"num"}, (i + 1) + " of " + REVIEWS.length + " · " + r.review_id), text,
    options(r, "topic", TOPICS), options(r, "intent", INTENTS), options(r, "sentiment", SENT), options(r, "severity", SEV),
    el("div", {class:"row"}, el("span", {class:"k"}, "evidence"), quote, useSel, useAll),
    el("div", {class:"row"}, el("span", {class:"k"}, "entities"), ents),
    el("div", {class:"row"}, el("span", {class:"k"}, "needs review"), el("label", {}, flag, " ambiguous / missing context")),
    el("div", {class:"bad"})));
  refresh(r);
});
document.getElementById("next").addEventListener("click", () => { const r = REVIEWS.find(x => !complete(x)); if (r) document.getElementById("c" + r.review_id).scrollIntoView({behavior:"smooth", block:"center"}); });
const cell = v => '"' + String(v).replace(/"/g, '""') + '"';
document.getElementById("download").addEventListener("click", () => {
  const missing = REVIEWS.filter(r => !complete(r)).length;
  if (missing && !confirm(missing + " reviews are not fully labelled yet. Download anyway?")) return;
  const lines = [COLUMNS.join(",")];
  REVIEWS.forEach(r => { const l = lab(r.review_id); lines.push(COLUMNS.map(c => cell(c in l ? (c === "needs_review" ? (l[c] ? "true" : "false") : l[c]) : r[c])).join(",")); });
  const a = el("a", {href: URL.createObjectURL(new Blob([lines.join("\n") + "\n"], {type:"text/csv"})), download:"golden_50_labeled.csv"});
  document.body.append(a); a.click(); a.remove();
});
</script></body></html>
"""


def main():
    with SOURCE.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        rows = [{k: row[k] for k in columns[:6]} for row in reader]
    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE.replace("__REVIEWS__", payload).replace("__COLUMNS__", json.dumps(columns))
    (HERE / "label_golden.html").write_text(page, encoding="utf-8")
    print(f"Wrote {HERE / 'label_golden.html'} with {len(rows)} reviews")


if __name__ == "__main__":
    main()
