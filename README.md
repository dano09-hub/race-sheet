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

Twelve factors, each scored 0–100 within the race, combined with fixed weights.
The eleven GB/IE factors total 100; Draw adds 4 where a card carries it, so the
full array totals 104. The weights are relative: the scorer divides by the
active weight, so effective shares still total 100. Weights live in one place,
the `FACTORS` array:

```js
var FACTORS = [
  ['market', 'Market', 18],      // Betfair/SP-derived implied probability, normalised to sum 100
  ['form', 'Form', 15],          // last-6 form string, position-weighted
  ['tips', 'Tipsters', 5],       // number of tipsters naming the horse
  ['rpr', 'Card rating', 13],    // Racing Post Rating, or Timeform on Racing TV cards
  ['rating', 'Official rating', 15],
  ['cd', 'Course & trip', 10],   // course winner / distance winner flags
  ['draw', 'Draw', 4],            // post position; US cards only, null elsewhere so GB scores are untouched
  ['speed', 'Topspeed', 12],
  ['going', 'Won last time out', 4],  // the card's LTO flag, not a going preference
  ['fitness', 'Fitness', 3],     // days since last run
  ['weight', 'Weight', 2],
  ['connections', 'Stable form', 3]  // jockey + trainer 30-day strike rate
];
```

Missing or field-constant factors are dropped and the remaining weights
renormalise, so a race with no odds still scores sensibly. The full array sums
to 104 (100 without Draw); that absolute total does not affect the ranking.

## Changing the ratings

1. Edit `FACTORS` in `index.html` (and the matching labels in the Method tab).
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
enough fields to test, but there is no settled history for them yet — see the
Known gaps section for what that would take.

## Where the data comes from, and how to continue

| Region | Source | Cost | Reached from this sandbox? | What it gives |
|---|---|---|---|---|
| Great Britain & Ireland | `horseracing.net` racecards | free | yes, plain HTTP | full card: form, OR, RPR, Topspeed, tips, weights, going |
| USA | `entries.horseracingnation.com` | free | yes, plain HTTP | morning-line price, HRN speed figure, draw, trainer, jockey |
| USA (deeper) | `horseracingnation.com/horse/...` | free | **no** — 202 empty body | form lines, last-run dates (needs a browser) |
| Australia / NZ | `punters.com.au` form guide | free | **no** — 403, Cloudflare | form, barrier, weight, their rating, win/place %, price |
| Hong Kong | `info.cld.hkjc.com/graphql/base/` (see `tooling/scrape_hk.py`) | free | **yes, plain POST** | rating, last six, barrier, weight, jockey, trainer |
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

* **Hong Kong is live** via the HKJC GraphQL gateway (`tooling/scrape_hk.py`) —
  the one region that answers plain HTTP with no browser: rating, last six,
  barrier, weight, connections. Odds arrive once betting opens.
* **Australia/NZ now carry real fields.** 74 of 75 cards were filled from
  Punters' form guide harvested through a browser: last-ten form, barrier,
  weight (kg converted to stone-pounds), their rating and the price. Two race
  pages did not render in time and remain names-only.
* **`scrape_punters.py` price guard**: the parser used to read the `$0` in the
  prize column as a price. It now ignores anything below $1.01.
* **US cards use 3 of 12 factors** (price, speed figure, draw). The Overlay's
  public feeds carry a form string, official rating and last-run date for almost
  every US runner, so refresh archives them on the card as `form_live`,
  `or_live` and `days_live`. Those keys are not runner keys in the page's
  serialiser, so the published pick is unchanged. `tooling/us_form_day.py`
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
4. pulls today's settled results into `data/YYYY-MM-DD.json`
5. rewrites the three card blocks inside `index.html`
6. copies the result to `dist/public/index.html`

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
settled are scored, and they are scored from the archived card.

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

`tooling/export_training.py` scores every archived card with the page's own model
(`tooling/score_cards.js`) and joins each runner to its settled result, writing one
row per runner to `data/training/runners.csv`: the twelve 0-100 factor scores, the
finishing position, the pre-race price and a winner label. The file is derived, so
it is not committed; regenerate it after a refresh.

`tooling/train_weights.py` fits the twelve weights and one scale by conditional
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
tipster counts, so Tips is the same for every runner and the page drops it; and
its RPR and Topspeed columns are the figures **awarded for the run**, not the
pre-race ratings a card carries — the winner holds the top RPR in about seven
races out of ten. The importer therefore leaves RPR and Topspeed out, and a fit
on this file says nothing about the tips, rpr or speed weights: those stay at
their live values while the other nine are fitted around them. The database also
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
stops doing the sheet's job of disagreeing with the market on purpose. That is
not a candidate for the page. The useful finding is the other way round: on four
years of racing the twelve fixed weights are already close to the best available,
and the market is the ceiling.

So the vector runs as a shadow instead. `tooling/market_arm_day.py` snapshots its
pick for each day's GB/IRE cards — the page's own factor scores, simply
re-weighted, so there is no second copy of any factor — and `tooling/ab_tally.py`
scores both arms against the settled winners. The Results tab carries the running
comparison under "Market-heavy shadow"; the published pick, the weights and the
stored results are untouched. The six backfilled days give 123 races: 31 winners
for the arm against 30 for the page, with 29 picks changed (won 4, lost 3). A
tie, exactly as the fit predicted. It stays a shadow until it separates.

The share the page prints is a normalised score, and it is compressed. On the
held-out 2,251 races the pick was shown at 19.2% and won 32.6%, and runners shown
at 30% or more won 47.1% against a 36.3% claim. `tooling/calibrate_share.py`
measures this: one softmax scale (0.0498) fitted on earlier races turns the same
totals into probabilities within 0.6 points of the record, with the pick at 33.9%
against 32.6%. The ranking does not move, so this is a display question rather
than a model one, and nothing on the page has been changed by it.

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
recoverable from our own archive, which is why the shipped 12-factor model cannot
be back-tested on `data/dataset.csv.gz` as-is. What can be tested there: market,
tips, form, days since run, course/distance record and jockey/trainer form — six
of the twelve factors. The three pre-race ratings — OR, RPR and Topspeed — plus
draw and weight are not recoverable from this archive. Snapshot racecards on the
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
proxy for the live page, not the twelve-factor production model: pre-race OR,
RPR, Topspeed, draw and weight cannot be reconstructed from the result archive.

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
a reliable local front-runner edge, and the live twelve-factor page remains
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

## Deploying

The site is on Freebuff Sites (site id `2a8edd98-2d40-49b4-92b1-33f3df2436f0`).
`dist/deploy.json` is a standard manifest; any static host works just as well,
since `dist/public/index.html` is the whole site.

## Known caveats

* The model is a favourite-ranker. On this card it found 10 of the 13 winning
  favourites and 1 of the 8 winners that weren't the favourite.
* 21 races is a tiny sample. The weights were tuned in-sample; treat any
  improvement of one or two winners as noise.
* The Results tab is a snapshot. It does not poll for new results.
* The Results tab is UK/IRE only. The **US results** tab holds the US ledger and
  the US speed-figure A/B; both ledgers show the off time beside each course.
* The race-by-race audit on Results is rebuilt from the latest settled UK/IRE
  day whenever the page or results are rebuilt; the model notes below it remain
  the 12 November development note.
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
| HKJC GraphQL | `date` variable is ignored - every call returns the current meeting; `finalPosition` is always 0 |
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
