# Racing ratings sheet — 12 Nov 2026 (Hereford, Market Rasen, Thurles)

A single self-contained page that scores every runner on the day's UK/Irish cards
and ranks them. No build step, no dependencies, no network calls at runtime.

## What's here

```
index.html                 the entire app: CSS, JS, 21 embedded race cards, settled results
dist/public/index.html     the copy that is actually served (must be refreshed after edits)
dist/worker.js             Cloudflare Worker: returns env.ASSETS.fetch(request)
dist/deploy.json           deploy manifest (entrypoint, compatibility date, assets dir)
.work/                     scratch: scraped racecard/result HTML used to build the data (not needed)
```

`index.html` is the whole product. Open it directly in a browser, or drop it on any
static host, and it works — there is nothing to install.

## How it scores

Six inputs, each scored 0–100 within the race, combined with fixed weights
totalling 56. A race is rated only when **every runner** carries all six: a card
with a gap anywhere in the field is not rated, and the page names the missing
field instead of scoring the horses that happen to be complete. A value every
runner shares is filed, not missing: it cannot separate the field, so it is
scored a neutral 50 rather than refusing the race. The inputs are
the ones the feeds file for every runner — the `RATED_FACTORS` array:

```js
var RATED_FACTORS = [
  ['market', 'Market', 18],           // implied probability from the price, margin removed
  ['form', 'Form', 15],               // last-6 form string, position-weighted
  ['rating', 'Official rating', 15],
  ['weight', 'Weight', 2],
  ['fitness', 'Days since a run', 3],
  ['connections', 'Jockey & trainer', 3]
];
```

Racing Post ratings, Topspeed, tipster counts and naps, the course/trip/going
flags and the draw are **not** model inputs. They are not filed for every
runner on every card, and dropping them per horse would score one horse on less
evidence than its rivals. If those feeds become complete they can enter with a
weight.

Every pick carries a paragraph generated from the filed values only: price and
its share of the book, the form string and what it contains, the official rating
and its distance from the field high, the weight, the days since a run, and the
jockey and trainer win counts. The place figure is the Harville sum of the
model's own win chances, labelled as such on the card.

## Changing the ratings

1. Edit `RATED_FACTORS` in `index.html` (and the matching labels in the Method tab).
2. `cp index.html dist/public/index.html`
3. Redeploy `dist/` (see below).

To check a change against the settled card, the back-test harness is ~60 lines of
Node: stub `document`/`window`/`localStorage`, slice out the `<script>` block,
expose `compute()` and `RACE_CARDS` by appending a line before the closing `})();`,
then loop over `RACE_CARDS`, calling `compute()` per card and comparing the top
horse to the winner in the results page. Winner names are in the `RESULTS` array.

## Where the data comes from

* Racecards: `https://www.horseracing.net/{hereford|market-rasen|thurles}/12-11-26`
* Results: `https://www.horseracing.net/results/{course}/12-11-26`

Both are scraped with regex and baked into the page. Nothing is fetched at runtime,
so the page never breaks — but it also never updates itself. Refreshing the data
means re-scraping and redeploying.

## Scheduling

`tooling/refresh.sh` runs the whole pipeline — UK (today and tomorrow), US,
Hong Kong, settled results, rebuild, stage. Drop it in cron:

```cron
0 7 * * * cd /path/to/race-sheet && ./tooling/refresh.sh >> .refresh.log 2>&1
```

Best run before the Australian and Hong Kong cards open (07:00 UTC catches the
UK afternoon, the US evening and the next AUS morning). The last step is still
publishing `dist/` — Sites has no callable deploy from the box, so that stays a
button (or one tool call).

**Weights are not re-tuned per region.** The 18/15/5/13/15/10/12/4/3/2/3/4 split
was fitted on British results only. US, Australian and Hong Kong cards now carry
enough fields to test. Hong Kong settles from the official HKJC result feed;
that history is still one meeting, so nothing is re-tuned for it — see the
Known gaps section for what that would take.

## Where the data comes from, and how to continue

| Region | Source | Cost | Reached from this sandbox? | What it gives |
|---|---|---|---|---|
| Great Britain & Ireland | `horseracing.net` racecards | free | yes, plain HTTP | full card: form, OR, RPR, Topspeed, tips, weights, going |
| USA | `entries.horseracingnation.com` | free | yes, plain HTTP | morning-line price, HRN speed figure, draw, trainer, jockey |
| USA (deeper) | `horseracingnation.com/horse/...` | free | **no** — 202 empty body | form lines, last-run dates (needs a browser) |
| Australia / NZ | `punters.com.au` form guide | free | **no** — 403, Cloudflare | form, barrier, weight, their rating, win/place %, price |
| Hong Kong | HKJC GraphQL gateway plus the official race-card page (see `tooling/scrape_hk.py`) | free | **yes, plain HTTP** | official rating, last six, barrier, weight, connections, venue, race name, post time, going |
| France | PMU programme and participants (`tooling/france.py`) | free | **yes, plain HTTP** | declared field, French form string, handicap value, weight, draw, forecast dividend, definitive arrival |
| Chile | the same PMU programme (`tooling/france.py`) | free | **yes, plain HTTP** | declared field, form, handicap value, weight and draw; **no prices**, plus definitive arrivals |
| Argentina | the same PMU programme | free | **yes** | cards only; the participant payload is often 204 and no named arrival has been proven |
| Anything else | `punters.com.au` covers GB, IRE, US, CA, AUS, NZ, HK, JP, ZA, FR, TR, AE | free | no, same Cloudflare wall | same block as Australia |
| Training history, GB/IRE | Delta Romeo's results set on Kaggle | free | **no** — needs a Kaggle API token | 1988 onwards: OR, RPR, Topspeed, SP, finishing position |
| Starting prices | `promo.betfair.com/betfairsp/prices` | free | **no** — 403 from this sandbox | Betfair SP per runner, 2008 onwards |
| Racecards and results | `racingpost.com`, via `rpscrape` | free | **no** — 406 to a plain request | full form and result history |

**The wall is the network, not the code.** Punters and HRN horse profiles refuse
datacenter IPs. From an ordinary home connection the requests usually succeed
untouched — which is why `tooling/scrape_punters.py` is written for `requests`
and only needs a browser when you are on a blocked network.

### tooling/scrape_punters.py

Walks a day's meetings on Punters and parses the runner block:

```
1. Bella Tatiana (2)
   J: Tom Sherry (58.5kg)  T: Mick Price & Michael Kent Jnr
   Last 10: -  Career: 00-0-0  Rating: -  Win: 0%  Place: 0%
   Flucs: 2%  Odds: $5.5
```

→ barrier, weight, last-ten form, rating, price, jockey, trainer.

```bash
python3 tooling/scrape_punters.py 2026-11-13 --regions AU,NZ   # writes cards JSON
python3 tooling/scrape_punters.py 2026-11-13 --regions US,HK
```

`parse_runner_block()` is tested against that layout; feed it page innerText if
you are harvesting through a browser. Output matches the card schema in
`index.html`, so a new block is a copy-paste away.

### Known gaps

* **Hong Kong is live** via the HKJC GraphQL gateway plus the official
  race-card page (`tooling/scrape_hk.py`) — the one region that answers plain
  HTTP with no browser: rating, last six, barrier, weight, connections, venue,
  race name, post time and going. Reserves are excluded from the field. Odds
  arrive once betting opens. HK meetings also appear in the US feed;
  `scrape_us.py` skips them so they are never shown or scored as US races.
* **Australia/NZ now carry real fields.** 74 of 75 cards were filled from
  Punters' form guide harvested through a browser: last-ten form, barrier,
  weight (kg converted to stone-pounds), their rating and the price. Two race
  pages did not render in time and remain names-only.
* **`scrape_punters.py` price guard**: the parser used to read the `$0` in the
  prize column as a price. It now ignores anything below $1.01.
* **US cards cannot pass the strict gate.** The feed files no weight at all, no
  jockey or trainer record, and often no form string, official rating or
  last-run date. The Overlay's public feeds carry the last three for almost
  every US runner, and refresh archives them as `form_live`, `or_live` and
  `days_live`, but they are not runner keys the page scores. A US race is
  therefore marked **Not rated**, with the missing inputs named, rather than
  scored on a subset. `tooling/us_form_day.py`
  scores a shadow arm with the form string in the form slot and
  `tooling/ab_tally.py` (snap dir `picks_ab_form`) judges it over time; the
  weights only move if that tally separates.
* **US results keep the full finishing order.** The payout table names the first
  four home; `tooling/us_form.py` fills every other starter's position from the
  live feed, so an unplaced pick is known rather than assumed.
* The page is a **static build**: every number is baked at refresh time. The
  countdown is live; the cards are not.

## One-command refresh

```bash
python3 tooling/refresh.py
```

That single command:

1. scrapes today's UK racecards -> `RACE_CARDS`
2. scrapes tomorrow's UK racecards -> `CARDS_FRI`
3. scrapes today's US entries (entries.horseracingnation.com) -> `CARDS_US`
4. files today's and tomorrow's pre-off French and Chilean cards (PMU) -> `CARDS_FR`, `CARDS_CL`
5. pulls today's settled results into `data/YYYY-MM-DD.json`
6. rewrites the card blocks inside `index.html`
7. copies the result to `dist/public/index.html`

Then publish `dist/`. A block that scrapes empty is left untouched, so a quiet
day or a blocked request can never wipe a card that already works — that is why
today's finished UK card survives a run made after racing.

Every card a run fetches is archived twice: a dated snapshot at
`data/cards/<date>.json` and a merged per-day file under `data/cards/by_date/`,
filed by the day the card runs (so tomorrow's card is stored under tomorrow).
Merging means a later run that only covers some meetings cannot drop cards an
earlier run captured, while a race already on file is refreshed with the newer
card. Settlement reads that by-date file, which is how a day is still scored
from the card the page actually showed.

Settled picks are immutable. When a day's results are scraped again, the
stored pick, finish and fate are carried over; only races that were never
settled are scored, and they are scored from the archived card. The UK and
Irish feeds rewrite their own rows only: a day's US rows are kept from the
earlier ledger, because US results come from a separate feed.

Options: `--days=3` widens the UK window. The US scrape only covers the date
given (today). `--skip-checks` bypasses the fixture gate for an emergency run:
it prints a `CHECKS SKIPPED` banner and appends a timestamped line to
`data/checks_skipped.log`, so a bypass always leaves a trace.

Before scraping anything it runs `tooling/test_checks.py`. If a check fails the
build stops with exit code 3, `index.html` and `dist/` are left exactly as they
were, and the failing assertions are printed — a regression cannot ship silently.

## GitHub Pages and the Android app

The sheet installs as a standalone app. Open the published URL in Chrome on
Android and choose **Add to Home screen** / **Install app**: `manifest.json`
supplies the icon and standalone display, `sw.js` keeps the last sheet available
offline, and `index.html` registers it on load. The service worker is
network-first for navigation, so online visitors always get the current picks.

To host it on GitHub, with the data refresh running there too:

1. Create an empty **public** repository (Pages from a private repository needs a
   paid plan) and push this folder as the repository root, so `index.html`,
   `dist/` and `tooling/` sit at the top level.
2. In the repository: Settings -> Pages -> Build and deployment -> Source:
   **GitHub Actions**.
3. Push to `main`. The **Deploy sheet** workflow publishes `dist/public` and
   prints the Pages URL.
4. Run **Refresh data** once from the Actions tab. It scrapes, runs the fixture
   gate, commits the refreshed cards/results/page and deploys. After that it runs
   on the UTC schedule: 08:10 (cards), 12:10 (prices), 18:40 (results). GitHub's
   scheduler can start jobs a few minutes late, which is why the times are not
   tight to a race.
5. Open the Pages URL on Android and install it from the browser menu.

Two things to check on the first run: some sources may treat GitHub's runner IPs
differently from a home connection, and a failed scrape stops that run without
publishing. The Actions log names the source that failed.

A Play Store APK is a further step: wrap the same Pages URL with Bubblewrap (a
Trusted Web Activity) or Capacitor, sign it with a keystore you keep, and attach
the APK to a GitHub Release. A Trusted Web Activity also needs
`/.well-known/assetlinks.json` at the origin root, which a GitHub project page
cannot serve without a custom domain. The installed PWA avoids that entirely and
is the recommended first app.

## Training from the archive

`tooling/export_training.py` scores every archived card with the twelve-factor
scorer the page used before the strict-model change (`tooling/score_cards.js`) and
joins each runner to its settled result, writing one row per runner to
`data/training/runners.csv`: that old model's twelve 0-100 factor scores, the
finishing position, the pre-race price and a winner label. The file is derived, so
it is not committed; regenerate it after a refresh.

`tooling/train_weights.py` fits the old twelve weights and one scale by conditional
logit (one race is one choice), holds out the latest race days, and reports
held-out log loss and top-1 against the live weights and the market. It writes
nothing: no weight, pick or result is changed by training. With a few hundred
races the fit is a pipeline test, not evidence, and a weight vector only becomes a
candidate after it wins on a much larger sample and then through the shadow arm.

Scaling up needs an external history. The free Delta Romeo GB/IRE set on Kaggle is
the natural input: `tooling/fetch_kaggle.py` downloads it with a Kaggle API token
(account name plus key, from `kaggle.json`), and `tooling/import_kaggle.py` turns
the raceform rows into the same runner file, deriving form, days, course and trip
record, won-last-time-out and jockey/trainer form from each horse's earlier runs
only, so no row can see its own race.

```bash
python3 tooling/fetch_kaggle.py --dataset deltaromeo/horse-racing-results-ukireland-2015-2025
python3 tooling/import_kaggle.py --db data/kaggle/raceform.db --since 2023-01-01
python3 tooling/train_weights.py --csv data/training/kaggle_runners.csv
```

Two things in that source are not what a card would give the page. It carries no
tipster counts, so Tips is the same for every runner and the twelve-factor
scorer drops it; and
its RPR and Topspeed columns are the figures **awarded for the run**, not the
pre-race ratings a card carries — the winner holds the top RPR in about seven
races out of ten. The importer therefore leaves RPR and Topspeed out, and a fit
on this file says nothing about the tips, rpr or speed weights: those stay at
their historical twelve-factor values while the other nine are fitted around
them. This training path predates the six-input model and is not the live page. The database also
carries French, Hong Kong and American meetings, some written without a country
suffix and some sharing a name with a British track, so home meetings are
recognised from a whitelist and everything else is dropped and reported.

The first run over 2023-01-01 to 2026-11-15 covered 50,196 races and 468,903
runners. Holding out the last 60 race days (2,251 races):

| held-out | log loss | top-1 |
| --- | --- | --- |
| live weights | 1.8819 | 32.6% |
| fitted | 1.8117 | 34.9% |
| market | — | 35.0% |

The fit improves, but almost entirely by moving weight onto the market (18 to
51): it becomes a market follower that only draws level with the favourite, and
stops doing the sheet's job of disagreeing with the market on purpose.

Pulling the older archives as well (1988-2004 and 2005-2014, same columns) puts
426,000 GB/IRE races and 3.9 million runner rows behind the same fit, 1988-01-01
to 2026-11-15. Holding out the same last 60 days (2,251 races) does not move the
answer: live weights 1.8822 / 32.6%, fitted 1.8141 / 35.3%, market top-1 35.0%.
Seven times the history buys nothing over the 2023+ run (1.8117 / 34.9%) — the
fit is still market-led (18 to 43.8) and still only edges the favourite. The
ceiling is the information a card supplies, not the sample size: this source
cannot see the pre-race tips, RPR and Topspeed figures the old twelve-factor
cards carried, so those weights stay at their historical values and the fit cannot learn what the
market already knows. That is
not a candidate for the page. The useful finding is the other way round: across
the whole history those twelve fixed weights were already close to the best
available in that model, and the market is the ceiling.

So the vector runs as a shadow instead. `tooling/market_arm_day.py` snapshots its
pick for each day's GB/IRE cards — the page's own factor scores, simply
re-weighted, so there is no second copy of any factor — and `tooling/ab_tally.py`
scores both arms against the settled winners. The Results tab carries the running
comparison under "Market-heavy shadow"; the published pick, the weights and the
stored results are untouched. The six backfilled days give 123 races: 31 winners
for the arm against 30 for the page, with 29 picks changed (won 4, lost 3). A
tie, exactly as the fit predicted.

The arm does not get to win on a good week. Its decision rule is fixed in
advance, in `tooling/shadow_rule.py`: judge it at 2,000 settled races; adopt
only if its strike-rate lead over the page is at least 3.0 points and an exact
one-sided McNemar test on the races where the two picks disagree gives p below
0.05; retire it otherwise. Two thousand races is what one pick per race can
resolve — with the arms disagreeing on about 30% of races, the 95% interval on
the lead is about ±2.4 points there, so a three-point lead is detectable at
roughly 80% power, while a 1.5-point gain would need about 8,300 races to see.
There is no early stopping: an interim look that could end the test early would
invalidate it, so the page prints the running lead, the p value and the races
remaining rather than a verdict. The earlier 200-race, non-overlapping-interval
rule was replaced by this one because at 200 races it would call a coin flip a
result.

The share the page prints is a normalised score, and it is compressed. On the
held-out 2,251 races the pick was shown at 19.2% and won 32.6%, and runners shown
at 30% or more won 47.1% against a 36.3% claim. `tooling/calibrate_share.py`
measures this: one softmax scale (0.0498) fitted on earlier races turns the same
totals into probabilities within 0.6 points of the record, with the pick at 33.9%
against 32.6%. The ranking does not move, so this is a display question rather
than a model one. The card table now carries it: a **Model** column prints
`softmax(0.04 * total)` across the field, so the pick that used to be shown at
19.2% reads about 30%. The scale is 0.04 rather than the fitted 0.0498 — it sits
at the low end of the 0.0404-0.0498 range the two datasets allow — and the
constant is display-only: the weights, the selection and the ranking are
unchanged, and the Score column still shows the raw rating.

The live archive agrees on the direction but not yet on the size. On its own 117
GB/IRE races the pick is shown at 17.0% and won 24.8% (95% CI 17.8-33.3%), and
the archive's own best scale is 0.0404 against the four-year 0.0498: at 0.0404
the pick reads 26.0%, at 0.0498 it reads 30.1%. 117 races cannot separate those
scales, so the applied scale sits between the two at 0.04 and should be re-checked
as the archive grows.

That re-check now runs on the six-input re-score. `tooling/rescore_results.py`
writes the rated pick and its chance to `data/results_rated/<date>.json`, and the
Results tab's **Model chance audit** block reads it: the average stated chance
of the rated picks, their actual win rate, and a Wilson interval around it. At
this build that is 48 rated races and 8 winners, with the re-scored picks carrying
19.8% on average and a 95% interval of 8.7-29.6%, so the block says the
sample is not yet informative. At 350 races the interval is about +/-4.5 points, enough to
catch a gross error but not to separate the 0.04 and 0.0498 scales, whose pick
chances differ by about four points; at 1,600 races it narrows to about +/-2
points and the question can be settled.

The paragraphs below describe the twelve-factor chance column that remains in
`data/results` for the historical record. Days settled before that column
existed were backfilled by
`tooling/backfill_chance.py` from the archived cards, and only where the rebuilt
pick matched the stored pick exactly. A race whose card cannot reproduce its own
pick is left blank rather than given a number the page never showed. Only seven
days still have their cards, so the rest of the record carries no chance.

Twenty of the 118 recent races stay blank, and the reason is the archive rather
than the model. The intraday results sweep wrote their picks from the card as it
stood at the off; the next day's refresh overwrote `data/cards/by_date/`, and
`results.yml` commits `data/results` but not `data/cards`. The surviving card
versions are therefore post-off re-scrapes: re-scoring every git version of the
card, or the page code as it stood on the day, gives a different horse in 17 of
the 20 (3 are reproduced by an earlier version, which confirms the re-scrape).
The stored pick remains the only contemporaneous record of those races, so a
chance derived from a later card would be a reconstruction, not a record.

That gap cannot recur. Every pick settled from now on stores a compact card
snapshot beside it (`card` in the results row): the pick, its chance, and every
runner's total, share and factor scores exactly as the page produced them. A
future backfill reads what the model saw at the off instead of re-scraping a
card that may have moved. Rows settled before this change have no snapshot, and
the twenty above will simply stay blank.

The pick is only one horse per race, so `tooling/field_audit.py` checks every
runner instead: it buckets the printed chance and compares each bucket with how
often those runners actually won, with a Wilson interval per bucket. It reads
the frozen card snapshots once fifty races carry them (the Results block adds a
whole-field line and the band table at that point) and can read the archive CSV
for an immediate answer. On the live archive's 117 races / 1,266 runners at scale 0.04 it gives
ECE 0.7 points and Brier 0.0777 per runner, with every band holding: 0-5%
stated 3.5% and won 3.8%, 5-15% stated 8.6% and won 8.6%, 15-30% stated 19.8%
and won 17.2%, 30%+ stated 41.1% and won 50.0% (each inside its interval).

## Does disagreeing with the market pay?

The sheet's premise is that it disagrees with the price on purpose. That is
testable: `tooling/disagreement_audit.py` compares each runner's printed chance
with the de-vigged market chance from its SP, then asks what the disagreement was
worth. On 3.5 million priced runners across 366,516 races (1988-2026):

| at the same market price | gap between the page and the price | winners, page-above minus page-below |
| --- | --- | --- |
| any disagreement | > 0 points either way | between -0.1 and +0.2 points, depending on stratum width |
| strong disagreement | 10+ points | -3.0 points (95% CI -3.6 to -2.3), stable at 50, 100 and 200 strata |

The comparison is made inside price strata, so the market's own favourite-longshot
bias cancels: two runners in one stratum have the same market chance, and the
only difference is whether the page rates one above the other. Backing every
priced runner at SP returns -26.0%, and the one bucket with a positive return
(the page likes the horse by 10+ points: +20.4% on 34,832 runners) is carried by
a handful of longshots: its strike rate is 3 points *below* comparable runners
the page dislikes, so the return is payout skew, not skill. The mild-gap number
is reported as a range because it moves with the width of the price strata -
which is itself the answer: there is no stable effect to find there.

The honest reading: there is no evidence the sheet beats the price, and its
strongest disagreements are its weakest. Mild disagreement is worth about two
tenths of a point, which is not a business. That is why the page shows a
calibrated chance with an interval rather than a value claim, and why a weight
change still has to clear the pre-registered shadow bar rather than a good week.

```bash
python3 tooling/disagreement_audit.py --csv data/training/runners.csv
python3 tooling/disagreement_audit.py --csv data/training/runners.csv --bins 200
```

### Which factor carries it?

`tooling/factor_audit.py` runs the same fixed-price comparison factor by factor:
the top score band (80-100) against the bottom (0-20), inside the same market
price band, over 366,516 priced races. Only factors with 100+ runners in both
extremes of a price band can be measured:

| factor | top minus bottom | 95% CI | runners |
| --- | --- | --- | --- |
| market | +5.47 points | +5.04 to +5.89 | 376,160 |
| form | +0.64 points | +0.37 to +0.91 | 251,927 |
| official rating | +0.19 points | +0.10 to +0.27 | 1,174,384 |
| weight | 0.00 points | -0.05 to +0.05 | 1,799,746 |

Tips, RPR and Topspeed are blank in this source (their weights stay at the
historical twelve-factor values; the live six-input model does not use them), and
course & trip, draw, going, fitness and stable form cluster too
tightly to have both extremes present in one price band, so those eight cannot
be measured this way. No measurable factor is anti-predictive on its own: the
market factor carries most of the signal, form adds about half a point, official
rating and weight add nothing. The sheet's large disagreements are therefore a
combination effect rather than one bad factor - consistent with the disagreement
audit above, and still not a reason to tune the weights by hand.

```bash
python3 tooling/chance_audit.py                  # stored chances, twelve-factor record
python3 tooling/field_audit.py                   # every runner, from snapshots
python3 tooling/field_audit.py --csv data/training/runners.csv
python3 tooling/backfill_chance.py               # reattach chances where a card agrees
```

## Checks

```bash
python3 tooling/test_checks.py
python3 tooling/check_factor_docs.py
```

The second check derives the factor count, full nominal weight and weight
without Draw from `FACTORS`, then verifies the README, README code block,
Method tab and served `dist` copy. It also checks the archived `ice` README and
Method tab. GitHub Actions runs both checks on pushes to `main` and on pull
requests.

The page render calls the page's own scorer through Node, and its numbers go
straight into the ledgers and the screen panels. Use the version in `.nvmrc`
(`nvm use`) for a local refresh, check or test run: the three workflows that
render the page read that file too, and a different Node can print a figure
differently, which the committed-page check would report as a stale page.

Fixture tests for both blocks on the Results tab. For the rating A/B tally: empty
project, a pending-only day, two days with known hits, all three verdict branches
at 300 races, the real 14 Nov snapshot with simulated winners, escaping and
idempotent block replacement. For the market re-check: missing cards, no published
prices, prices that match no runner, price merge by course/time and horse name
(including apostrophes and `(IRE)`), kept vs changed counts, races with no prices
being left out, the all-held wording, the CLI banner and escaping. The archive
checks cover merging across runs, deduplicating a repeated race, refreshing a
stored card, filing tomorrow's GB card under the day it runs, and the dated
snapshot. Everything runs in temp directories with no network and no race day;
exits non-zero on any failure.

## Australia & New Zealand

`CARDS_AU` holds the AUS/NZ cards for the day, scraped from Punters
(`punters.com.au/form-guide`). Their pages carry a schema.org `SportsEvent`
block per race: course, UTC start, and every runner as
`Horse - J: jockey - T: trainer`.

Two things to know:

* **Punters is Cloudflare-fronted.** Plain HTTP requests get a 403, and their
  GraphQL host (`api.punters.com.au`) is a CloudFront WAF that refuses this
  network outright. The block is keyed on datacenter IPs, so the same request
  often succeeds from an ordinary home connection. In this workspace the pages
  were captured through a real browser instead.
* **No prices and no ratings.** There is no SP, no official rating and no
  Topspeed on that feed, so an AUS/NZ race has no score to compute. Those cards
  render as a field list (horse, jockey, trainer) with the verdict suppressed,
  and the next-race panel skips them rather than invent a pick.

To refresh them, open a race page in a browser, save its `ld+json`
`SportsEvent` block, and rebuild with the parser in `tooling/`.

## The back-test dataset

`tooling/scrape_results.py` and `tooling/build_dataset.py` build a season of settled
results into a modelling table. Current contents: **315 days, 11,419 races,
114,396 runners (1 Jan – 12 Nov 2026)**.

```
python3 tooling/scrape_results.py 01-01-26 12-11-26 --workers=6   # resumable, one JSON per day
python3 tooling/build_dataset.py                                  # -> data/dataset.csv.gz
```

Every feature is computed from results strictly *before* the race, and the state
is only updated after a whole race is written out, so the table is safe to
back-test against without look-ahead bias. Columns:

* identity — date, course, time, race, going, distance, horse, jockey, trainer
* outcome — pos, pos_num, won, placed
* market — sp, dec, field, mkt_rank
* tipsters — tips, tips_rank
* horse history — form (last six), runs, wins, days_since, course/dist runs and wins
* connections — j_30_runs, j_30_wr, t_30_runs, t_30_wr (rolling 30-day strike rate)

Baselines on this data: the favourite wins **37.6%**, the most-tipped horse
**31.6%**, and 79.2% of runners arrive with a prior-run form string.

### The gap you must know about

The source keeps only *results* for past dates — past racecards 301 redirect to
the course page. The **pre-race card figures** for OR, RPR and Topspeed are not
recoverable from our own archive, which is why the old 12-factor model could not
be back-tested on `data/dataset.csv.gz` as-is. For the live six-input model the
archive covers four — market, form, days since run and jockey/trainer form — but
not official rating or weight, so a full six-input back-test still needs cards
snapshotted on the day. The three pre-race ratings — OR, RPR and Topspeed — plus
draw and weight belonged to the old model and are not recoverable here. Snapshot racecards on the
day; `refresh.py` already archives what it scores.

A free Kaggle archive, [Horse Racing results — UK/Ireland 1988–2026](https://www.kaggle.com/datasets/deltaromeo/horse-racing-results-ukireland-2015-2025),
does include OR, RPR and Topspeed. Its RPR/TS are the figures **earned in that
race**, not the figures printed on the pre-race card, so using the current row
would leak the result. `tooling/backtest_ratings.py` therefore uses the official
rating carried into the race plus each horse's **previous known** RPR/TS, form,
days since a run and prior course/distance wins.

One caveat on `dec`: it is the starting price, i.e. the sharpest version of the
market. A model trained against it looks better than one betting at a price you
could actually have taken an hour earlier.

Data quality: 15 of 11,419 races (0.13%) have no parsed winner, almost all of
them overseas meetings (Flemington) where positions are not rendered in the same
markup. Filter them out with `won` when back-testing; the UK/Irish cards that
matter here parse cleanly.

### Rating-factor back-test (free archive)

The free Kaggle database is not committed to this repo. To run the test:

```bash
pip install pandas numpy scipy
python3 tooling/backtest_ratings.py --db /path/to/raceform.db
```

The script filters to GB/IRE, keeps non-finishers as losers, and uses only
information available before the race. It fits a race-normalised conditional
logit and compares it with the SP market, then chooses a market/model blend on a
chronological calibration period and reports the untouched test period. Results
are written to `data/backtest_ratings.json`.

The first run (2016–2025, 18,504 priced test races) found:

| Model | Top pick won | Log loss | SP ROI |
|---|---:|---:|---:|
| SP market | **34.9%** | **1.766** | −9.1% |
| Ratings only | 24.9% | 2.031 | −12.9% |
| Market + ratings blend | 34.9% | 1.766 | −9.1% |

The blend weight selected on calibration was **0**, i.e. the rating factors added
no out-of-sample value in this test. That is a warning, not a verdict: the test
lacks tips, connections, going and current-card RPR/TS, and SP is a post-time
price. It does show that a more complex model should have to beat the market
baseline before it ships.

### Place model and each-way EV

`tooling/backtest_places.py` fits a direct top-k place model on the same free
archive, using standard GB/IRE each-way terms (2 places at 1/4 for 5–7 runners;
3 at 1/5 for 8–11 handicaps; 3 at 1/4 for 12–15 handicaps; 4 at 1/4 for 16+
handicaps; 3 at 1/5 for 8+ non-handicaps). It chooses a temperature and a
per-race offset on a chronological calibration period so each race's place
probabilities sum to the number of paid places.

The first run (19,490 test races) found:

| Measure | Result |
|---|---:|
| Place log loss | 0.551 |
| Place Brier | 0.185 |
| Calibration ECE | 0.004 |
| Placed runners captured in model top-k | **44.2%** |
| Placed runners captured by SP top-k | **53.1%** |
| Each-way EV > 0: bets | 60,342 |
| Each-way EV > 0: ROI at SP | **−32.4%** |

The probabilities are calibrated but the ranking is worse than the market, and
the positive-EV selection loses after correct settlement. It is therefore **not
wired into the live page** and should not be used for staking. The reusable EV
calculator is available from the command line:

```bash
python3 tooling/eachway.py --odds 8/1 --p-win 0.12 --p-place 0.35 --field 10 --handicap
```

### Market-aware place model

The free archive has only the starting price, not the current card price, so the
market-aware test uses race-normalised SP as the odds proxy. It fits three
direct top-k models on exactly the same priced races:

```bash
python3 tooling/backtest_places_market.py --db /path/to/raceform.db
```

| Model | Top-k capture | Log loss | Brier | ECE |
|---|---:|---:|---:|---:|
| SP top-k baseline | 53.1% | — | — | — |
| Market only | 53.1% | 0.5026 | 0.1659 | 0.0342 |
| Ratings only | 44.3% | 0.5509 | 0.1848 | 0.0048 |
| Market + ratings | **53.1%** | **0.5024** | **0.1658** | 0.0342 |

The ratings improve test log loss by only 0.0002 and top-k capture by 0.04
percentage points (53.08% to 53.12%) — effectively no ranking gain. The
positive-EV each-way selection still loses: 29,078 bets, **−41.7% ROI** at SP
(market-only: −42.6%). The market-aware model therefore does **not** ship
either. SP is sharper than a real morning price, so this is an upper bound on
what the odds feature can do, not evidence of an edge.

### True pre-race Betfair prices

Betfair's free daily price files add two real pre-race inputs: `MORNINGWAP`
(weighted average of bets before 11am GMT) and `PPWAP` (weighted average of all
bets before the off), plus BSP. They are public CSV files, mirrored on
[Kaggle](https://www.kaggle.com/datasets/eonsky/betfair-sp). The free mirror
ends in April 2024, so this test covers 2020–2024 rather than 2025.

```bash
python3 tooling/backtest_places_betfair.py \
  --db /path/to/raceform.db --prices /path/to/betfair/win/csvs
```

The join uses date, normalised horse name and off-time (raceform writes
12-hour times; the join compares clocks modulo 12 hours). It matched 404,721
runners across 44,570 races with all three prices; 41,752 races have paid
places. The untouched test set is 18,312 races from July 2022 to April 2024.

| Model | Top-k capture | Log loss | EV>0 bets | ROI |
|---|---:|---:|---:|---:|
| BSP market | 53.1% | — | — | — |
| Ratings only | 45.0% | 0.5586 | — | — |
| Morning market | 51.2% | 0.5240 | — | — |
| Morning market + ratings | **51.3%** | **0.5235** | 57,710 | **−3.4%** |
| Pre-off market | 52.8% | 0.5134 | — | — |
| Pre-off market + ratings | **52.9%** | **0.5132** | 49,815 | **−0.8%** |

The pre-off model gets close to break-even on Betfair's pre-off average, but it
is still negative before commission, and the morning model loses more. Ratings
add only 0.09 percentage points of top-k capture. Neither is wired into the
live page. `MORNINGWAP`/`PPWAP` are exchange averages, not a bookmaker price you
could necessarily take; BSP remains the strongest benchmark.

### Forward each-way price snapshots

The sheet archives current card win prices, not a separate place market.
`tooling/collect_eachway_prices.py` turns each priced runner into an append-only
snapshot using the race's standard place terms:

```bash
python3 tooling/collect_eachway_prices.py --date 2026-11-14
```

Each JSONL row records the win price, field size, handicap flag, places,
place fraction, derived place decimal and `place_odds_kind`. The current cards
produce `derived_standard_terms`; if a card supplies `place_odds`, that value is
used and labelled `bookmaker_quoted`. The first snapshot (14 November 2026,
246 cards, 608 priced runners) is in `data/eachway_prices.jsonl`.

`refresh.py` calls the collector after archiving each run, so the scheduled
refresh builds the forward history automatically. It does not change any pick
or model weight. This is a **derived** place price from the win price and
standard terms, not a separately quoted place market; enhanced place terms are
not captured.

### Extending the Betfair test

Betfair's public directory has files after April 2024, but `promo.betfair.com`
returns HTTP 403 from this cloud network (US egress). I did **not** fabricate an
extension or pad the report with the stale mirror. The downloader is ready for
an unblocked UK/IE host:

```bash
python3 tooling/fetch_betfair_prices.py \
  --from 2024-05-01 --to 2026-11-30 --out /tmp/betfair_win
python3 tooling/backtest_places_betfair.py \
  --db /path/to/raceform.db --prices /tmp/betfair_win
```

The second command rebuilds `data/place_model_betfair.json` with the new dates.
Until it is run from a host Betfair serves, the published report remains the
2020–April 2024 test.

### FormFav form comparison (free tier)

[FormFav](https://formfav.com/) exposes GB/IE runner form and career stats on
its free plan (1,000 requests/day). `tooling/fetch_formfav.py` matches the
meetings and races to our archived cards and writes a side-by-side CSV. It is a
**comparison only**: it never edits a card, changes a score, or changes a pick.

1. Sign in at <https://formfav.com/get-api-key> and copy the free key.
2. Put it in the environment as `FORMFAV_API_KEY` or `FORM_FAV` (the project
   secret name), or in the untracked file `race-sheet/.formfav_key` (already
   gitignored), never in a tracked file:

```bash
export FORMFAV_API_KEY=...   # FORM_FAV also works
python3 tooling/fetch_formfav.py 2026-11-14
```

Outputs are `data/cards/<date>_formfav.json` and
`data/picks_ab/formfav_compare_<date>.csv`. `refresh.py` runs the same comparison
for today's and tomorrow's GB/IE cards when `FORMFAV_API_KEY` is set; without the
key it prints a skip and the rest of the refresh is unchanged. The API's date
window is only 30 days back to 7 days ahead, so this is not a historical odds
source and cannot extend the Betfair report.

### Pace feature back-test (research only)

The free result pages include an in-running comment for almost every runner.
`tooling/backtest_pace.py` turns the opening of each comment into an early-style
label (held-up, mid-division, prominent, led), builds each horse's profile from
its **previous** runs only, and tests a conditional-logit model on a chronological
holdout. The current race's comment is applied only after every runner's features
have been emitted, so there is no same-race leakage.

```bash
python3 tooling/backtest_pace.py   # -> data/backtest_pace.json
```

Coverage: 11,419 races / 114,396 runners; 102,688 usable comments; 88,526
runners with prior pace history. The priced test set is 981 races, with 8,079
training races and 1,717 calibration races.

The base model below uses the seven factors that survive in the free result
archive: market, form, tips, fitness, course, distance and connections. It is a
historical proxy, not the live six-input model: it shares market, form, fitness
and connections with the page, adds tips, course and distance, and cannot
reconstruct official rating or weight.

| Model | Top pick won | Winner in top 3 | Log loss | Brier | SP ROI |
|---|---:|---:|---:|---:|---:|
| SP market | **35.8%** | **69.5%** | **1.7806** | **0.7704** | −7.9% |
| Current seven-factor score | 33.5% | 63.7% | — | — | −10.5% |
| Fitted base | 35.3% | 69.3% | 1.8383 | 0.7820 | −9.6% |
| Base + pace | 35.0% | 69.1% | 1.8369 | 0.7819 | −10.8% |
| Pace only | 15.8% | 42.9% | 2.1629 | 0.8719 | +1.9% |

Adding pace changes test log loss by only **−0.0013**, while top-pick and top-3
capture both fall slightly and SP ROI gets worse. The pace-only model is much
weaker, and its positive ROI is not evidence of an edge at a 15.8% top-pick rate.

**Decision: no pace factor is shipped.** The comments are subjective, the test
is a seven-factor proxy, and SP is a post-time benchmark. A production change
would need a larger incremental gain, not a rounding-level log-loss move.

### Course/going front-runner bias (research only)

`tooling/backtest_course_bias.py` tests a separate hypothesis: do prior
front-runners beat their SP-implied chance more often at particular courses,
goings, or course/going combinations? A front-runner is a horse that led or
disputed the lead in at least half of its last six starts.

The bias is **market-adjusted** and **shrunk**: prior front-runner wins minus
the normalised SP probability those runners carried, blended toward the global
front-runner excess with a pseudo-sample of 20 runners. This stops a course with
a handful of front-runner runs from dominating. The current race is applied only
after all features are emitted.

```bash
python3 tooling/backtest_course_bias.py   # -> data/backtest_course_bias.json
```

Coverage: **10,777 fully priced races**, **9,192 global front-runner runs**,
**85 courses**, **12 going categories** and **218 course/going cells**. The test
split is 981 races; the training and calibration windows are the same as the
pace test.

| Model | Top pick won | Winner in top 3 | Log loss | Brier | SP ROI |
|---|---:|---:|---:|---:|---:|
| SP market | **35.8%** | **69.5%** | **1.7806** | **0.7704** | −7.9% |
| Base | 35.3% | 69.3% | 1.8383 | 0.7820 | −9.6% |
| Base + front-runner flag | 35.3% | 69.3% | 1.8383 | 0.7819 | −9.6% |
| Base + front + course bias | 35.0% | 69.2% | 1.8391 | 0.7822 | −10.8% |
| Base + front + going bias | 35.3% | 69.3% | 1.8383 | 0.7818 | −9.6% |
| Base + front + course/going bias | 35.1% | 69.4% | 1.8389 | 0.7821 | −10.6% |
| Base + front + all biases | 35.0% | 69.2% | 1.8391 | 0.7821 | −10.8% |

Against the base-plus-front model, course bias makes log loss **+0.0008 worse**,
going bias is **−0.0001** (flat), course/going is **+0.0005 worse**, and all
three together are **+0.0008 worse**. No variant beats the market. The
front-runner flag itself is effectively neutral.

**Decision: no course/going bias is shipped.** The free comments do not provide
a reliable local front-runner edge, and the live six-input page remains
untested by this proxy. Cards, weights and picks are unchanged.

## Racing Post ratings (comparison only)

```bash
python3 -m venv .venv && .venv/bin/pip install curl_cffi
.venv/bin/python tooling/fetch_rp_ratings.py 2026-11-14
```

Racing Post answers plain Python with 406 and Chrome-grade TLS with 200, hence
`curl_cffi`. The tool reads one racecard page per race and writes RPR, Topspeed and
official rating to `data/cards/<date>_rp.json`, then a per-runner CSV beside our
picks at `data/picks_ab/rp_compare_<date>.csv` (our card rating, RP RPR, RP TS, RP OR,
pick flag). It never edits the cards the model scores and is not imported by
`refresh.py`, so no pick changes. Past-date racecards redirect to results and
results carry no ratings, so it can only capture a card before its races run.

## Laying at Smarkets

Smarkets is a betting exchange, so the same market can be laid as well as
backed: you offer a price, and your liability is the backer's stake times the
decimal price minus one. Its commission is 2% of net market winnings (1% on the
Pro tier, 3% above £25,000 net profit in a year). Market data comes from the
REST v3 API: `/v3/markets/{market_ids}/quotes/` returns the current exchange
prices for both sides, and `/v3/markets/{market_ids}/last_executed_prices/`
the last traded price. Authentication is OAuth2, and API access is granted only
after an application is approved, with a one-time £250 administration fee; API
users are rate-limited to 50 requests a minute and unauthenticated calls get
delayed prices. Smarkets' terms allow automated access only after that
approval.

**Decision: the Lays tab does not show a Smarkets price.** No feed this project
archives carries an exchange ladder: the archived cards hold racecard win
prices, which are back prices with a bookmaker margin, and nothing holds the
lay side, the money available at it, or the commission paid on a winning
market. A lay price cannot be derived from a card price without inventing the
spread, so the tab quotes the card price, the model's rank and chance, and the
liability that price would imply per unit staked, and labels all of it as such.
Its settled record reports the net against the total liability risked rather
than a strike rate, because 51 winning lays at one unit each say nothing about
the one loser that risks thirty times as much.
Wiring in real lay prices would need an approved Smarkets API key (or an
archived ladder) plus a job that stores the ladder before each off; the page
would then show the exchange price beside the card price rather than replace
the record with it. Until then a lay row is a record of the model, not a price
feed, and no stake is taken.

## Deploying

The site is on Freebuff Sites (site id `2a8edd98-2d40-49b4-92b1-33f3df2436f0`).
`dist/deploy.json` is a standard manifest; any static host works just as well,
since `dist/public/index.html` is the whole site.

## The page

Seventeen destinations, each a job rather than a data source. Old tab names
still resolve to Today, so saved links and the browser's remembered tab keep
working. Every race list gives the off time and runs in start-time order, and
settled ledgers put each day's races in clock order. Times are held as real
instants, so the order does not change with the viewer's timezone.

Four screens read the strict model rather than a second rating. **Lays** names
the runner the model rates last in every race it can rate, with the liability
the card price implies. **Longshot results**, **Overhyped results** and **Place
banker results** replay their rules on settled days and settle each row from the
stored result. All four are derived from the same six-input re-score as the
ledgers: a race with a gap is left out and the gap is named, never filled, and
no row is invented. Bet slips and Upcoming slips remain withdrawn, and nothing
on the page presents a pick that did not pass the six-input check.

| Tab | What it is for |
| --- | --- |
| Today | The next rated race, every rated pick with its evidence paragraph, and the list of races not rated with the missing input named. |
| Cards | Pick any race and inspect every runner the model scored, with the full rating table. |
| Lays | The runner the model rates last in each rated race, with the card price, its model chance and the liability that price implies. |
| UK results | Settled UK results, market checks and the standing model audits. |
| Irish results | Settled Irish races, split from the UK ledger by course. |
| US results | Settled US races with the off time and the speed-figure A/B. |
| US racing | The US flags and the full angle board. |
| Hong Kong | Today's official HKJC card, with each race's model pick, official rating, last-six form, barrier draw and the settled official outcome once the race has run. |
| HK results | Every settled Hong Kong race, with the winner, the first three home and where the model pick finished. |
| France | Today's and tomorrow's PMU cards: French form, handicap value, draw and the live forecast dividend. |
| France results | Every settled French race, from PMU's definitive arrival, with the pick from the pre-race card. |
| Chile | Valparaíso cards from PMU, scored on form, handicap value, weight and draw because the meeting carries no prices. |
| Chile results | Every settled Chilean race, from PMU's definitive arrival, with the pick from the pre-race card. |
| Longshot results | Every longshot the model flagged on a settled day it could rate: wins, the frame and the card price. |
| Overhyped results | Every favourite the model doubted, and how it finished. A doubt record, not a bet. |
| Place banker results | How often the model-and-market banker placed, under the terms stored with its result. |
| Method | The fixed weights, how the chance is calibrated, the measured country coverage, and the evidence behind both. |

## Known caveats

* The model is a favourite-ranker. On this card it found 10 of the 13 winning
  favourites and 1 of the 8 winners that weren't the favourite.
* 21 races is a tiny sample. The weights were tuned in-sample; treat any
  improvement of one or two winners as noise.
* The Results tab is a snapshot. It does not poll for new results.
* The **UK results**, **Irish results**, **US results**, **HK results** and
  **France results** tabs hold one ledger each. All show the off time beside
  each course.
* Every settled UK, Irish, US and Hong Kong ledger is a six-input re-score.
  `tooling/rescore_results.py`
  scores each day's archived card with the page's own `SCORE_RATED_CARD`,
  writes `data/results_rated/<date>.json`, and `refresh.py` renders that pick
  and marks a race whose card has a gap as **Not rated**. Each unrated race
  records `blocker`, the field missing from most of its runners, and a run
  prints those ranked with the races each would unlock, so the next data fix can
  be picked by how many races it rates. A price the archived card lost is
  restored from a stored copy of the same day -- the append-only each-way
  snapshots or the day's own card pulls -- but only from a copy taken before the
  off on the course's own clock (the snapshot is UTC, the off local track time),
  never over a price the card already carries, and never estimated: the
  source of each restored price is written beside the race as `price_source`.
  The pre-race pick on
  record is never rewritten: `data/results` keeps it and the ledger says so.
  `python3 tooling/refresh.py --data-only` re-renders the UK, Irish and US
  ledgers, the race-by-race audit and the Model chance audit from stored data
  without scraping, so a new re-score reaches the page without a fresh pull.
  <!-- coverage:start -->
  Coverage at this build is 52 of 272 settled races (9 winners), all on
  British cards; 29 of those were held out by price alone and are rated only
  because every runner's price was found in a stored pre-off copy.

  *Generated from `data/results_rated` by `tooling/coverage_figures.py`;
  `refresh.py --data-only` rewrites it, and the checks job fails when the
  committed README differs.*
  <!-- coverage:end -->

  US
  cards file no weight, Hong Kong no days or jockey/trainer records, and the
  French and Chilean cards no price. The race-by-race audit uses the same
  re-score. The archive keeps the card as it stood at the last pull, so where
  that is a post-off re-scrape the rated pick is a reconstruction; the ledger
  labels it a re-score and keeps the stored pre-race pick in `data/results`.
* French cards carry no price in the model's `odds` field, no days-since-run
  and no jockey or trainer records, so no French race is rated. The tab shows
  the card with its French form and handicap value but no pick, and the missing
  inputs are named rather than filled in. The French forecast dividend is a pool
  estimate, not a bookmaker price.
* The **Chile** tab has no market factor at all: PMU publishes no price for
  Valparaíso. The price column stays blank rather than estimated, and no Chilean
  race is rated because the six inputs are incomplete.
* The **Hong Kong** tab carries today's HKJC card from the official source and
  settles its picks from the same feed's official finishing positions. A race is
  only settled once every declared runner has a position, and a stored pick is
  never rewritten. Prices stay blank until HKJC betting opens rather than being
  estimated.
* The race-by-race audit on Results is rebuilt from the latest settled UK/IRE
  day whenever the page or results are rebuilt, and uses the six-input re-score.
  The model notes below it are kept as the 12 November development note and are
  labelled as superseded.
* **Add a result manually** on the Results tab keeps entries in this browser's
  `localStorage`; it never edits the published ledger. Once the auto pull has
  officially settled the same race, the entry is marked "auto result live". The
  **Pull results now** button opens the scheduled results workflow on GitHub so it
  can be run on demand; the 20-minute schedule is unchanged.

## Results: what is usable, and what is not

Weights can only be tuned against settled races, so the results feed matters as much
as the cards. Current state, honestly:

| Source | Status |
|---|---|
| Racing Post `/results/YYYY-MM-DD/` | loads, but the table is client-gated: 0 rows reachable, results not in the HTML |
| Equibase | free summary results, but its terms **prohibit automated access** - do not scrape |
| horse racing nation `entries-results` | past dates return the *entries* table (morning line), never finishing positions |
| HKJC GraphQL | official card and, once a race is off, every runner's finishing position; only the current meeting is served, so past dates return today's runners |
| horseracingapi.org | free key, **yesterday's** results for US + GB + IRE + FR + SA, 50 calls/day - the workable route |

Using the API (key is yours, never commit it):

```bash
export AHR_KEY=...
python3 tooling/fetch_results.py                 # yesterday
python3 tooling/fetch_results.py 2026-11-12      # a specific day
python3 tooling/fetch_results.py --import saved.json 2026-11-12   # no key needed
python3 tooling/measure.py 2026-11-12
```

`measure.py` prints top-pick winners, places and "winner in our top 3", per region.
Until it has run on real settled cards, no accuracy claim is made anywhere in this project.

### Hong Kong settlement

`python3 tooling/settle_hk.py [YYYY-MM-DD]` reads the official HKJC meeting again
and takes every runner's finishing position from the feed's `finalPosition`. A
race is settled only when the whole declared field has one, so a partial feed
leaves it open. The pick is the page's own model pick, scored from the archived
card, and a pick already on file is never rewritten. Outcomes are written to
`data/results_hk/<date>.json`, embedded in the Hong Kong tab and listed on the
**HK results** tab. The scheduled results workflow settles Hong Kong in its own
step, before the page is written, so a published page cannot carry a ledger that
lags the official result. The feed only serves the current meeting, so the tool
refuses any other date rather than tag a card with the wrong result.

### Hong Kong pre-race odds

The morning card is scraped before betting opens, so the Market factor - the
largest single weight - is blank for most Hong Kong races and the pick rests on
form, official rating, draw and weight. `tooling/snapshot_hk.py` files one odds
snapshot per race between twenty and three minutes before its post time into
`data/hk_preoff/<date>/r<N>.json`; the **Hong Kong pre-race odds** workflow runs
it every ten minutes through the meeting. A filed snapshot is never overwritten
and a race that has already gone off is never captured, so the archive cannot
hold a price that only existed after the result. A feed that cannot be read
while a race is inside its window fails the run and names it, rather than
reporting the quiet zero that hid the earlier results outages. Nothing scores
from these files yet: they are the history a Hong Kong factor has to be
measured against before any weight changes.

`tooling/audit_hk.py` is that measurement. It scores form, official rating,
weight, barrier draw and price on a chronological split of the archive, reading the price only
from pre-off snapshots and discarding the archived card's own odds, then compares
each factor head to head with the favourite on the held-back meetings. It returns
no verdict until the holdout holds 40 races at five meetings, and a clean result
there still earns only a pre-registered shadow arm.

The results job runs that audit after Hong Kong settles and commits its report
to `data/hk_audit/latest.txt`, so the record refreshes itself as meetings
accumulate without anyone remembering to run it. The file is regenerated over
the whole archive rather than appended to, which means it can never drift from
the snapshots it describes; the commit history holds the earlier readings.

## France and Chile (PMU)

France is the one country outside GB/IRE, the US and Hong Kong where a free,
plain-HTTP feed carries a complete card **and** the official result.
`tooling/france.py` reads PMU's public programme: the declared field, the French
form string (`musique`), the handicap value, the weight, the draw and the live
forecast dividend, then the definitive finishing order once a race has run.

Four rules keep it honest:

* **Only pre-off races are filed.** `fetch_cards()` drops anything whose
  `heureDepart` has passed, so an afternoon or evening refresh cannot replace a
  card with a post-race re-scrape. The archive merges on course/time/race, so
  the version filed before the off survives.
* **A short field is not a field.** PMU's `nombreDeclaresPartants` counts
  declared runners including non-runners; the probe requires the full declared
  list and at least two starters, and skips the race otherwise.
* **An arrival is only settled when it can be named.** Every number in
  `ordreArrivee` must map to a declared runner, and the arrival must be
  definitive; otherwise the race is left open rather than given a guessed
  winner.
* **A stored pick is never rewritten.** A re-run carries the pick, chance and
  finishing position already on file.

A French card cannot be rated. Its form is usable (the `musique` is converted
to the page's alphabet, never fed in raw — a French `p` means *plat*, while the
page reads `P` as pulled up), and the handicap value can stand in for the
official rating, but the card carries no price in the model's `odds` field, no
days-since-run and no jockey or trainer records. The card is shown and the
missing inputs are named; no pick is invented.

The **France** tab shows today's and tomorrow's cards; the **France results**
tab holds every settled French race. The scheduled results job settles France
after Hong Kong and before the page is written, so a published page cannot carry
a French ledger that lags the arrival.

PMU is the pari-mutuel operator rather than the racing authority, so the page
calls its arrival *PMU's definitive arrival*, and the dividend is described as a
pool estimate, not a bookmaker price.

Chile uses the same code with `region="CL"`: Valparaíso cards and definitive
arrivals, but PMU publishes no price for that meeting. The Market factor drops
and the pick is scored on form, handicap value, weight and draw. The Chile tab
and the Chile results ledger say that explicitly, and the price column stays
blank rather than showing an estimate. `tooling/settle_chile.py` settles it in
the results job, before the page is written.

The results cron covers 21:00-23:40 UTC as well as the daytime blocks, so a
meeting that finishes in the evening is settled the same night instead of
waiting for the midnight run. Each run files only arrivals PMU has already
called definitive; a race still in progress is left open for the next sweep.

Every results tab also states when its ledger last changed in UTC. The time is
written only when a race or pick actually changes, so a no-op sweep preserves
it. Rows filed before that field existed show their latest day and say the
settlement time was not recorded rather than guessing one. The results
workflow also writes the UK and Irish ledger times to its Actions run summary,
so the same freshness is visible without opening the page.

## Which countries can we actually cover?

`tooling/audit_coverage.py` probes every jurisdiction the betting board shows
from the same network the workflow runs on. A country earns a tab only when a
machine-readable card and a named result both exist; reachability alone is not
enough. The audit writes `data/coverage/latest.txt` / `.json` and replaces the
coverage table on the Method tab (the refresh job runs it with `--inject`).

Measured 18 November 2026:

| Country | Cards | Results | Prices | Verdict |
|---|---:|---:|---:|---|
| Great Britain & Ireland | yes | yes | yes | live (horseracing.net + Sporting Life / irishracing) |
| United States | yes | yes | yes | live (HRN entries and payout table) |
| Hong Kong | yes | yes | no | live (HKJC card and finishing positions) |
| France | yes | yes | yes | live (PMU) — **wired, with its own tabs** |
| Chile | yes | yes | no | live (PMU) — **wired, scores without a market price** |
| Argentina | yes | no | no | cards only; the field is often not served |
| Australia & New Zealand | no | no | no | blocked (Punters Cloudflare 403, racing.com 403) |
| Japan | no | no | no | HTML only; no machine-readable race list |
| South Africa | no | no | no | HTML only; no meeting/field payload found |
| Germany | no | no | no | HTML only; no structured field feed found |
| Brazil | no | no | no | no race-data feed (the domain redirects to an investment site) |
| United Arab Emirates | no | no | no | unreachable from this network |

Chile is the only wired region without prices: PMU serves its field and its
arrival but no price, so a Chile pick is scored without the market factor and
the page says so. Argentina's participant payload is frequently 204 (no field),
which is why its arrival numbers cannot be turned into named results.

## Back-test: what the British history actually says

`tooling/build_scores.py` rebuilds per-race factor scores from `data/dataset.csv.gz`
(11,419 races, 114,396 runners, 315 days). `tooling/tune_weights.py` then splits the
days chronologically - 7,798 races up to 10 August to fit on, 3,610 held back - and
grid-searches weights on the training half only.

Result, with the blank-factor handling fixed (blank must never score a neutral 50 -
doing that was a bug in an earlier version of the harness):

| weights | train won | holdout won | holdout top-3 |
|---|---|---|---|
| current (18/12/8/6/4/3/3) | 33.9% | 39.5% | 78.6% |
| best found on train | 34.8% | 40.2% | 78.0% |

The grid-search winner beats the current weights by 0.7 points on holdout winners and
loses 0.6 on top-3. That is noise, not an edge, so **the production weights stay as
they are** - now for a measured reason rather than a hunch.

Two bugs found while doing this, both worth remembering:

1. The `dec` column looks like decimal odds. It isn't: median 13.0, maximum 99,999, and
   its ordering tracks the finishing position. Using it produced a 52% "win rate" from
   the market factor alone. `build_scores.py` now parses SP only.
2. Scoring missing factors as 50.0 let a runner with *no data* outrank a runner with
   *bad data*. Every single-factor test was corrupted by it until fixed.

Features were checked for look-ahead and are clean: a horse's form string starts with
today's finishing position only 8.9% of the time (the base rate), and win rate rises
smoothly with tipster count (5.0% at zero tips to 20.6% at four).
