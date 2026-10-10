# Decision memo: next-quarter product priority

## Recommendation
Prioritize **billing/support**, focused on the free-tier playback rules behind Free-Tier Playback Restrictions `billing.paywall`. This is the strongest single actionable issue in the baseline ranking: it has the highest priority score, and customer complaints about it rose as a share of completed reviews between the comparable historical periods. The recommendation is to investigate the restrictions and how they are explained—not to infer that changing them will improve retention or revenue.

## Evidence
Free-Tier Playback Restrictions `billing.paywall` ranks first among the actionable issues, with {{C001}} complaints, a mean severity of {{C003}} on the 1–5 scale, and a priority score of {{C004}}. It represents {{F031}} of complaint or cancellation reviews. Its share of completed reviews increased from {{F034}} in the prior comparable period to {{F035}} in the recent one. Some customers explicitly describe limits on basic playback: “can't even go back few second to play music” [review:9e5f9745-f8be-48d3-b9de-4d118507d94a] and “forcing you to pay premium for basic things” [review:a78d831a-69f4-428a-9235-cfdd9775c509]. Cancellation language appears in {{F032}} reviews for this issue; that is expressed intent, not observed churn.

General App Dissatisfaction `other.general` ranks second overall, but it is not an actionable product area. Do not direct effort to generic criticism; Free-Tier Playback Restrictions `billing.paywall` is the top actionable issue. The next actionable issue, Frequent and intrusive ads `usability.ads`, ranks third, with {{C009}} complaints and a priority score of {{C012}}. One customer says, “every 1 or 2 song then instant ads” [review:3e8bdc92-4934-457e-85cc-8b9d14bf51c0]. Playback Controls Behave Unexpectedly `usability.controls` ranks fourth, with {{C013}} complaints and a priority score of {{C016}}. Its reports describe distinct control problems, including “plays any song of the Playlist randomly” [review:5c9c17d3-9d6a-4fa1-a2a4-ae98eefbbd25].

## Alternatives
Usability deserves a close second: its issues account for {{F010}} complaint or cancellation reviews, or {{F012}} of the ranking population—more than billing as a topic. But those reports span separate concerns, while Free-Tier Playback Restrictions `billing.paywall` is the highest-ranked actionable issue. Access, represented here by Unable to Log In With Account Credentials `access.login`, has {{C021}} complaints despite a mean severity of {{C023}}. App Crashes or Gets Stuck Loading `playback.crash` has {{C025}} complaints and a mean severity of {{C027}}. Both merit monitoring, but the evidence does not place either ahead of the paywall issue by baseline score.

## Limitations
This is a small, fixed historical sample of self-selected public reviews, not a representative customer survey. The ranking covers {{F007}} complaint or cancellation reviews from {{F001}} source rows. Classification covered {{F002}} reviews, but {{F009}} were flagged for review. The verifier found {{F072}} topic agreement and {{F074}} exact severity agreement. The score weights review volume by described severity; it is not business value. Plan tier, revenue, confirmed cancellations, and causal retention effects are unknown.

## What to check next
- Validate free-tier playback rules and customer understanding with current product data and targeted research.
- Segment complaints by plan, platform, and playback action to identify which restrictions drive frustration.
- Test clearer explanations or alternative rule designs, then measure user comprehension and playback behavior before scaling.
