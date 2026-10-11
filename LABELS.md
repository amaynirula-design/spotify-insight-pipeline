# Label guide: shared definitions and how I applied them

The topics, intents and severity scale are the course's common labels (`GRADING_CONTRACT.md`). The subtopic codes
are mine. Every example below is a real review from the final run, shown with the first eight characters of its
`review_id` and the labels saved in `grading/records.jsonl.gz`. Examples were chosen after the run to document the
rules; they were never shown to a model. The prompt's own examples are illustrative sentences
(`pipeline/prompts/enrich_p2.txt`).

## Topics and my subtopic codes

The enricher picks one of 22 codes; code derives the topic, so the two can never disagree
(`pipeline/labels.py`). `issue_id` = `topic.code`.

| Topic (course definition) | Codes | Real example (id → saved label) |
|---|---|---|
| **access** — login, signup, password or account access | `login`, `account` | "Not able to login through phone number Pls rectify the problem" (`08312ea1` → access.login, complaint, 4) · "Now I cannot have access to Spotify, because it thinks I am in a different country." (`af8cb141` → access.account, complaint, 4) |
| **usability** — navigation, controls, layout, queue/playlist management, ad interruptions | `ads`, `ui`, `controls` | "Music is good, but just too many freaking ads. In the past 33minutes, I have been interrupted by ads 4 times." (`9ead0f20` → usability.ads, complaint, 2) · "In new update i can note forward and backword song its anoying" (`3fa114bd` → usability.controls, complaint, 3) |
| **playback** — playback failure, crashes, lag, connection failures, audio quality, resource use | `crash`, `stops`, `connect`, `audio`, `perf` | "The app isnt opening it was fine a week ago then i suddenly cant open it" (`272ca9a4` → playback.crash, complaint, 4) · "The sound quality is very good" (`e6577e8c` → playback.audio, praise, 1) |
| **downloads** — downloading, saved music, offline listening | `offline` | "The app does not play my music when offline even when I download" (`178158c9` → downloads.offline, complaint, 4) |
| **catalog** — missing songs/artists, search/discovery, recommendations, lyrics | `missing`, `recs`, `lyrics`, `library` | "Terrible, it never show the song I want to hear." (`eca78de8` → catalog.recs, complaint, 3) · "Some more rare Bengali semi- classical songs required" (`e4258a64` → catalog.missing, request, 1) |
| **billing** — price, charges, subscriptions, paywalls, premium entitlement; explicitly premium-only controls | `paywall`, `price`, `charges` | "The most basic of features are now premium." (`850a4562` → billing.paywall, complaint, 3) · "Cost could be lower but I love spotify" (`a2d70f0f` → billing.price, complaint, 2) |
| **support** — contacting support and the response | `support` | "Hard 2 contact administration!!!" (`af35f87c` → support.support, complaint, 2) |
| **other** — general praise/criticism, unrelated content, no supported specific topic | `general`, `boycott`, `noise` | "Wrost app. Just reported this app" (`0dd65539` → other.general, complaint, 2) · "Dude the tacos from this place are fantastic!…" (`2f42fbc2` → other.noise, unclear, 1) |

Rules I applied on top of the definitions:

- Several problems in one review: the one with the highest severity; on a tie, the first specific one mentioned.
- Positive review: the first specific praised feature ("Good music, and I barely get ads", `ea16290d` →
  usability.ads, praise). General praise is `other.general` ("Spotify rocks! My go to music app!", `72af84c8`).
- A paid-plan mention alone is not billing: "Worst app I am a premium user still I can't download songs…"
  (`628c9fb3`) is downloads.offline. A control that the reviewer says now needs Premium is billing.paywall.
- "Bad app" with no detail is `other.general`; no defect is inferred.

## Intent (first that applies)

| Intent | Rule | Real example |
|---|---|---|
| cancellation | explicitly leaving, uninstalling, cancelling, or threatening to | "After updating there is no option for loop the songs. Finally uninstalling the app." (`924e0b90` → usability.controls, cancellation, 3) |
| complaint | a negative experience, including mixed praise and criticism | "Good music app.. but so many limitations on free." (`5dfe990b` → billing.paywall, complaint, 2) |
| request | a desired change with no reported failure | "Can you have an option to not have pop ups every single time I open the app?" (`342787ae` → usability.ui, request, 1) |
| praise | | "Right on time every time I use it" (`e7fab09c` → other.general, praise, 1) |
| unclear | bare boycott slogans, unrelated or meaningless text | "Very Shameful Swedish government" (`225f9ec1` → other.boycott, unclear, 1) |

A boycott review keeps the code `boycott`; its intent follows the same order (cancellation if the reviewer says
they are uninstalling, complaint with severity 2 if it also calls the app itself bad, otherwise unclear).

## Severity

| Level | Shared meaning | Real examples |
|---|---|---|
| 1 | no reported problem: praise, unclear content, or a pure request | "Always know what i want to listen to like no one else" (`16efdaf3`, praise) |
| 2 | dislike, generic criticism, minor annoyance; no functional loss stated | "It was good app before updates. Now it's worst app and I am uninstalling it." (`45f9214e`, cancellation — leaving does not raise severity) · "There's a lot of ads if u don't want ads get premium" (`587e113b`) |
| 3 | a function is degraded or restricted; some use remains | "Update. All is well I have to skip to the next song.. I can't turn repeat off." (`daeed196`) · "Since the last update the app has been very unstable. The app freezes and lags all the time" (`93a80365`) |
| 4 | a core task is clearly blocked | "I can't play anymore please fix before its to late thanks" (`4c54d501`) · "Why can't i download music??? I literally just subscribed what is this?????" (`3ee01d2f`) |
| 5 | explicit serious financial, privacy or data harm | "Where my refund? From RM10 you cut my credit until rm7 and my premium spotify didn't access" (`4851a916`, billing.charges) |

Code enforces what the definitions imply: praise, unclear and request records are severity 1 and complaints are at
least 2. When the model's output broke that (729 distinct texts), code corrected the severity, recorded a note and
set `needs_review`.

## Other fields

- **sentiment**: the model gives −2…2 for the tone of the text; code stores it as −1, −0.5, 0, 0.5, 1.
- **evidence_quote**: for reviews of 80 characters or fewer, the complete original text; otherwise a phrase chosen
  by the model and accepted only if it is an exact substring of the source (whitespace and case differences are
  repaired to the source span). If the quote fails twice, the opening of the review is used and the record is flagged.
- **entities**: feature and competitor terms found literally in the text by a fixed lexicon (`labels.py`).
- **needs_review**: set by the model when it is unsure, and by code after a severity correction or a fallback quote.

## Where the labels are weakest

See the README's golden-set and verifier sections. The main soft boundary is free-tier control complaints that do
not say "premium": the model tends to file them under `billing.paywall`, while a stricter reading puts them in
`usability.controls`.
