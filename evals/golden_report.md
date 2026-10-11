# Golden-set evaluation (50 hand-labelled reviews)

Enricher configuration: `gpt-6-luna@none|enrich_p2|schema-s1|4aab826f`. Predictions: `runs/spotify_reviews_18months__gpt-6-luna-none-4aab826f/enriched.jsonl.gz`.

| Field | Agreement (all 50) | On unambiguous cases |
|---|---|---|
| Topic | 74% (37/50) | 78% |
| Intent | 90% (45/50) | 98% |
| Severity (exact) | 82% (41/50) | 85% |
| All three | 68% (34/50) | 71% |

- Severity mean absolute error: **0.24**; sentiment mean absolute error: **0.12** (100% within the predeclared ±0.5).
- Ambiguous cases (human ticked needs_review): **9**. Missing/quarantined predictions: 0.
- Evidence quotes: 100% are exact source substrings (checked in code); 98% overlap the human-chosen quote (heuristic; inspect `golden_cases.jsonl`).
- Entities: I left the optional entities column blank for all 50 reviews, so there is no human list to compare against. Code extracted 32 entity mentions; each is a lexicon term literally present in the text, so none is an unsupported addition (listed per case in `golden_cases.jsonl`).
- needs_review as a prediction: {"model_flag_and_human_flag": 5, "model_flag_only": 0, "human_flag_only": 4, "neither": 41, "model_flagged_and_label_wrong": 0, "model_unflagged_and_label_wrong": 16}

## Per-topic counts

| Topic | Human labels | Model labels |
|---|---|---|
| access | 1 | 0 |
| usability | 8 | 9 |
| playback | 4 | 4 |
| downloads | 1 | 1 |
| catalog | 1 | 3 |
| billing | 1 | 5 |
| support | 0 | 0 |
| other | 34 | 28 |

## Topic confusion

| human \ model | access | usability | playback | downloads | catalog | billing | other |
|---|---|---|---|---|---|---|---|
| **access** | · | · | 1 | · | · | · | · |
| **usability** | · | 4 | 1 | · | · | 3 | · |
| **playback** | · | 1 | 2 | · | · | 1 | · |
| **downloads** | · | · | · | 1 | · | · | · |
| **catalog** | · | · | · | · | 1 | · | · |
| **billing** | · | · | · | · | · | 1 | · |
| **other** | · | 4 | · | · | 2 | · | 28 |

## Intent confusion

| human \ model | cancellation | complaint | request | praise | unclear |
|---|---|---|---|---|---|
| **cancellation** | 1 | · | · | · | · |
| **complaint** | · | 18 | · | · | · |
| **request** | · | · | 1 | · | · |
| **praise** | · | · | · | 19 | · |
| **unclear** | · | 4 | · | 1 | 6 |

## Severity confusion

| human \ model | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| **1** | 27 | 2 | 1 | 1 |
| **2** | · | 8 | 4 | · |
| **3** | · | · | 4 | 1 |
| **4** | · | · | · | 2 |

## Disagreements

| Review ID | Text | Human (topic / intent / severity) | Model (topic.subtopic / intent / severity) | Model flagged |
|---|---|---|---|---|
| `991b6b3a` | Ads were okay, but limited functionality..it's just too much | usability / complaint / 2 | billing.paywall / complaint / 3 | False |
| `1fc8b08f` | Duo Premium .... No ads at all! | other / unclear / 1 (ambiguous) | usability.ads / praise / 1 | False |
| `723f07de` | Update: I reached out to Spotify Support and with some help from Sarah I was able to get the app working again. Old Post:Normally, the app works great, and I us | access / unclear / 1 (ambiguous) | playback.crash / complaint / 4 | False |
| `46842184` | asem, lirik nya kdang gaada kek mana?? | other / unclear / 1 (ambiguous) | catalog.lyrics / complaint / 3 | False |
| `372d4e67` | Please ye ads ko thoda kumm Karo harr ek song ke baad ad 🙏🙄 | other / unclear / 1 (ambiguous) | usability.ads / complaint / 2 | False |
| `3ddb3f4e` | Deleting this App. The new update suck, we can't choose our fvrt songs after some clicks, we can't CHOOSE song after a song played, and we can't turn off shuffl | usability / cancellation / 2 | billing.paywall / cancellation / 3 | False |
| `16d640e9` | It's my best music app but now it's not...everything basic features is premium.... | billing / complaint / 2 | billing.paywall / complaint / 3 | False |
| `8cc4fad4` | I hate this app and sweden .I love islam | other / unclear / 1 | other.boycott / complaint / 2 | False |
| `215463ae` | What is happening with Spotify?? Recently I'm trying to play a song but it doesn't working. So I unstalled and thn again install it now I can't even open the ap | usability / complaint / 4 | playback.crash / complaint / 4 | False |
| `2aa566c6` | Nice app, very less advertising | other / praise / 1 | usability.ads / praise / 1 | False |
| `8c0546b0` | 1-Lyrics not getting loaded, 2-can't go back to or start song where to wanted. Pathetic update | playback / complaint / 3 | usability.controls / complaint / 3 | False |
| `dceb14e7` | I gave one star because I can't give any less than that. the new update is sooooooo annoying like I can't even play the songs that I like and I can't rewind son | playback / complaint / 3 | billing.paywall / complaint / 3 | False |
| `5fba12ea` | I find Spotify very good, it finds and play whatever music you want and also adds songs that you might like, judging from your playlist if you choose to arrange | other / praise / 1 | catalog.recs / praise / 1 | False |
| `ac4e860a` | paying for subscribing but unable to play in offline mode really? | downloads / complaint / 3 | downloads.offline / complaint / 4 | False |
| `d2f3874f` | Too expensive and the free version is pretty much unusable with constant ads... Every two or three songs.. Forget it | usability / complaint / 2 | billing.price / complaint / 3 | False |
| `99b93e31` | This update very bad for everyone.old Spotify. we all want. | other / complaint / 2 | usability.ui / complaint / 2 | False |
