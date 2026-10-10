# Decision memo: next-quarter product priority

## Recommendation

Prioritize **billing/support**, focused on the free-tier experience and how Premium restrictions are communicated. The strongest reason is that Basic Music Controls Locked Behind Premium (`billing.paywall`) leads the ranked issues and is actionable: the complaints describe specific listening controls and features that customers say are gated, rather than a vague dissatisfaction signal. This does not establish that changing the paywall would improve retention or revenue; it does make the free-to-paid boundary the clearest product area to investigate next.

## Evidence

Basic Music Controls Locked Behind Premium (`billing.paywall`) ranks first, with {{C001}} complaints, mean severity {{C003}}, and priority score {{C004}}. It represents {{F031}} of complaint or cancellation reviews. Its share of completed reviews was {{F034}} in the prior comparable period and {{F035}} in the recent comparable period. Customer comments include “forcing us to purchase Spotify premium” [review:86adc9b6-be58-400c-9a08-aa082ff0535c] and “Good but everything is premium” [review:5c581e49-727a-4221-be0a-a59344c78d60].

The next actionable ranked issue is Frequent and Intrusive Ads (`usability.ads`), ranked third, with {{C009}} complaints and priority score {{C012}}. One customer wrote, “The ads are really annoying” [review:b9ae23d2-1152-4bfe-a876-afc09c005377]. Playback Stops or Fails to Start (`playback.stops`), ranked fourth, has fewer complaints, {{C013}}, but the highest mean severity among these leading actionable issues, {{C015}}. A customer reported that playback “Continously pauses songs and podcasts randomly” [review:f1abd13b-2391-4b04-8109-efa7f3cfed0b].

General dissatisfaction with the app (`other.general`) ranks second, but its reviews lack a specific product problem; it is not an actionable product area. The top actionable issue remains the paywall.

## Alternatives

Ads are the next strongest volume signal, but the comparable-period shares are {{F044}} and {{F045}}, unlike the upward pattern for the paywall. Playback interruptions may merit a targeted reliability investigation because reported impact is high, though the issue ranks below the paywall by the baseline score.

Users unable to log in (`access.login`) ranks eighth, with {{C029}} complaints. Its severity is high at {{C031}}, so access should remain on the watch list, but the supplied reviews provide a smaller signal than the leading options.

## Limitations

These are self-selected public reviews, not a representative customer sample: the source contains {{F001}} rows, of which {{F007}} are complaint or cancellation reviews. Classification covered {{F003}} of nonempty reviews, but labels are model-generated; the verifier’s topic agreement was {{F072}} and exact severity agreement was {{F074}}. The baseline score weights review volume by described impact, not business value. Cancellation language is expressed intent, not observed churn; revenue, plan tier, and causal retention effects are unknown. The window’s first and last calendar months are partial.

## What to check next

- Validate paywall complaints with support contacts, product analytics, and plan-tier data.
- Test whether clearer free-tier feature explanations or alternative control access address the specific complaints.
- Audit playback-stop rates and login failure telemetry before reallocating effort to reliability or access.
