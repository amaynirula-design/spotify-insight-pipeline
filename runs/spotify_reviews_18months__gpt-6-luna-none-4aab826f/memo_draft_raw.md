# Decision memo: next-quarter product priority

## Recommendation
Put next-quarter effort into **billing/support**, focused on the free-tier experience rather than support operations broadly. The strongest reason is that Free-Tier Features Require Premium `billing.paywall` is the highest-ranked actionable issue: it leads the baseline ranking by severity-weighted review volume. This is a signal to investigate the free-tier boundary and how its limits are communicated—not evidence that changing those limits would improve retention or revenue.

## Evidence
Free-Tier Features Require Premium `billing.paywall` has {{C001}} complaints and a severity sum of {{C002}}; it accounts for {{F034}} of complaint/cancellation reviews. Its recent-period share rose from {{F037}} to {{F038}}. Customers describe frustration with what is included: “REALLY EVERYTHING EVEN SKIP SONGS YOU NEED MONEY A BIG L SPOTIFY” [review:f7218ea9-a26a-4184-a839-2b42c37752fc] and “it asks us to take premium” [review:730160f8-4711-4065-97d0-38a32a87c309]. Cancellation language appears in {{F035}} reviews for this issue, but reflects expressed intent, not observed cancellation.

Usability is the strongest alternative by topic, accounting for {{F010}} complaints and {{F012}} of the ranking population. Within it, Frequent and Intrusive Ads `usability.ads` ranks third, with {{C009}} complaints; Interface Changes and Hidden Controls `usability.ui` ranks fifth, with {{C017}}. Unreliable Playback and Queue Controls `usability.controls` ranks fourth and has a higher mean severity, {{C015}}, despite fewer complaints, {{C013}}. One customer reports “hiding shuffle and repeat buttons” [review:6c356a4e-7088-4736-b26e-885c5aae5582].

## Alternatives
Usability has broader topic-level volume than billing, so it deserves discovery and targeted fixes; however, the top issue-level actionable signal is the paywall/free-tier experience. Playback ranks lower overall: Playback Stops or Skips Unexpectedly `playback.stops` has {{C021}} complaints, while App Crashes or Fails to Open `playback.crash` has fewer complaints, {{C025}}, but higher mean severity, {{C027}}. Access is also a distinct, high-impact problem: Login and Account Access Problems `access.login` has {{C029}} complaints and mean severity {{C031}}, but lower volume than the leading issues. General dissatisfaction `other.general` is not an actionable product area; the top actionable issue remains Free-Tier Features Require Premium `billing.paywall`. Support complaints are {{F031}}, so the evidence does not support making support operations the main focus.

## Limitations
These are self-selected public Google Play reviews from a fixed historical window, not a representative customer sample. The ranking population is {{F007}} reviews. Cancellation is expressed intent only; revenue, plan tier, actual churn, and causal effects are unknown. Classification is imperfect: verifier topic agreement is {{F075}}, while golden-set topic agreement is {{F079}}. Some completed rows reuse exact-duplicate text classifications, {{F006}}. The baseline score weights review volume by described severity, not business value.

## What to check next
- Validate the paywall issue with current free-tier users, separating confusion about limits from dissatisfaction with the limits themselves.
- Audit free-tier flows for consistency between feature availability, prompts, and in-app explanations.
- Compare the leading billing and usability findings with current product telemetry and support contacts before committing scope.
