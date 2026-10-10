# Decision memo: next-quarter product priority

## Recommendation

Prioritize **billing/support**, specifically billing and free-tier product boundaries—not support operations. The strongest reason is that `billing.paywall` is the highest-ranked actionable issue by the baseline severity-weighted score, and its share of reviews rose in the comparable recent period. This is evidence of a concentrated customer pain point, not proof of lost revenue or churn. Generic dissatisfaction ranks above it but is not an actionable product area; support complaints are present in very small numbers.

## Evidence

`billing.paywall` is rank one among issues: it has {{C001}} complaints and a severity-weighted priority score of {{C004}}, with mean severity {{C003}} on the 1-5 scale. It accounts for {{F034}} of complaint/cancellation reviews. Its share of completed reviews increased from {{F037}} in the prior comparable period to {{F038}} in the recent one. Reviews specifically mention features customers expect to be available without Premium: “Worst app all features needed premium” [review:704a2eb2-d139-4f8f-8fba-40b5357347fd] and “Even lyrics are premium” [review:ff91b91b-1a9c-4391-b4a2-d45200e746c7]. {{F035}} reviews express cancellation intent; that is stated intent, not observed churn.

The second-ranked issue overall, `other.general`, is not a product area to prioritize: it is broad, nonspecific dissatisfaction. The top actionable issue remains `billing.paywall`. At the topic level, usability has {{F010}} complaint/cancellation reviews, compared with {{F013}} for billing; however, the billing issue’s concentration and recent trend make it the clearer focused bet. Within usability, `usability.ads` is rank three overall, with {{C009}} complaints and a priority score of {{C012}}. One customer writes, “FOUR ADS just to listen to music” [review:6ea80aa3-f397-483e-b0f5-04fbfadc4a08].

## Alternatives

- **Usability:** `usability.controls` ranks four overall, with {{C013}} complaints and mean severity {{C015}}. `usability.ui` ranks five and has a rising recent-period share, from {{F057}} to {{F058}}. Both are actionable, but each is a narrower problem than the overall usability topic; validate whether a specific control or interface fix can be scoped.
- **Playback:** `playback.stops` ranks six, with {{C021}} complaints and the highest mean severity among these alternatives, {{C023}}. “Continously pauses songs and podcasts randomly” [review:f1abd13b-2391-4b04-8109-efa7f3cfed0b] signals a serious experience failure; investigate reliability alongside the main bet.
- **Access:** `access.login` ranks eight, with {{C029}} complaints, though its mean severity is {{C031}}. Its smaller volume argues against making it the quarter’s primary focus. Support itself has {{F031}} complaint/cancellation reviews.

## Limitations

These are self-selected public Google Play reviews from a fixed historical window, not a representative customer sample. The ranking population is {{F007}} reviews. Classification covered {{F002}} reviews, but {{F009}} were flagged for review; independent topic agreement was {{F075}}. The baseline score weights review volume by described severity, not business value. Cancellation language is intent only; revenue, plan tier, confirmed churn, and causal retention effects are unknown.

## What to check next

- Break down paywall complaints by feature, plan, and platform; test which free-tier boundaries cause the most friction.
- Validate the rising paywall signal against representative customer research and product usage data.
- Review playback-stop and login telemetry to establish whether those reports reflect widespread, reproducible failures.
