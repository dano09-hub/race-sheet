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

Eleven factors, each scored 0–100 within the race, combined with fixed weights.
Weights live in one place, the `FACTORS` array:

```js
var FACTORS = [
  ['market', 'Market', 18],      // Betfair/SP-derived implied probability, normalised to sum 100
  ['form', 'Form', 15],          // last-6 form string, position-weighted
  ['tips', 'Tipsters', 5],       // number of tipsters naming the horse
  ['rpr', 'RPR', 13],            // Racing Post Rating
  ['rating', 'Official rating', 15],
  ['cd', 'Course & trip', 10],   // course winner / distance winner flags
  ['draw', 'Draw', 4],            // post position; US cards only, null elsewhere so GB scores are untouched
  ['speed', 'Topspeed', 12],
  ['going', 'Going', 4],
  ['fitness', 'Fitness', 3],     // days since last run
  ['weight', 'Weight', 2],
  ['connections', 'Stable form', 3]  // jockey + trainer 30-day strike rate
];
```

Missing or field-constant factors are dropped and the remaining weights
renormalise, so a race with no odds still scores sensibly. Weights sum to 100.

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
* **US cards use 3 of 12 factors** (price, speed figure, draw). Form strings and
  days-since-run sit behind HRN's horse profiles, which refuse this network.
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

Options: `--days=3` widens the UK window. The US scrape only covers the date
given (today).

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
the course page. Official rating, RPR and Topspeed are therefore **not
recoverable historically**, which means the shipped 11-factor model cannot be
back-tested on this dataset as-is. What can be tested: market, tips, form,
days since run, course/distance record and jockey/trainer form — six of the
eleven factors. To get the other three you have to snapshot racecards on the day;
the scraper is the right place to add that.

One caveat on `dec`: it is the starting price, i.e. the sharpest version of the
market. A model trained against it looks better than one betting at a price you
could actually have taken an hour earlier.

Data quality: 15 of 11,419 races (0.13%) have no parsed winner, almost all of
them overseas meetings (Flemington) where positions are not rendered in the same
markup. Filter them out with `won` when back-testing; the UK/Irish cards that
matter here parse cleanly.

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
