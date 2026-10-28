# German Pop Culture Dataset: Architecture

This document describes the structure of the pipeline: scope and decisions, the design rules for a long collection period, the datasets and their variables, how the dataset is correlated with other datasets, the questions it answers, the sources and the admission of new sources, storage and data volume, the run, failure handling, the known data issues, the measurement log and the implementation status. The pipeline adopts the structure of [dataset.sociodemographics.germany](https://github.com/kleinnconrad/dataset.sociodemographics.germany), [dataset.politics.germany](https://github.com/kleinnconrad/dataset.politics.germany) and [dataset.economics.germany](https://github.com/kleinnconrad/dataset.economics.germany). Usage and configuration are described in the [README](README.md).

## Table of Contents
- [1. Scope and Decisions](#1-scope-and-decisions)
- [2. Design Rules](#2-design-rules)
- [3. Datasets and Variables](#3-datasets-and-variables)
  - [3.1 Common Fields](#31-common-fields)
  - [3.2 Chart Snapshots](#32-chart-snapshots)
  - [3.3 Entities and Attention](#33-entities-and-attention)
  - [3.4 News and Press Releases](#34-news-and-press-releases)
  - [3.5 Text Annotations](#35-text-annotations)
  - [3.6 Vocabularies](#36-vocabularies)
- [4. Correlation with Other Datasets](#4-correlation-with-other-datasets)
- [5. Questions and Indicators](#5-questions-and-indicators)
- [6. Sources and Fetch Rules](#6-sources-and-fetch-rules)
  - [6.1 Charts](#61-charts)
  - [6.2 Outlets and Press Sources](#62-outlets-and-press-sources)
  - [6.3 Admission of Sources](#63-admission-of-sources)
- [7. Storage, Partitioning and Data Volume](#7-storage-partitioning-and-data-volume)
- [8. Repository Structure](#8-repository-structure)
- [9. Pipeline Run](#9-pipeline-run)
- [10. Failure Handling](#10-failure-handling)
- [11. Known Data Issues](#11-known-data-issues)
- [12. Measurement Log](#12-measurement-log)
- [13. Implementation Status](#13-implementation-status)

---

## 1. Scope and Decisions

- **Scope:** German pop culture, understood as the popular and everyday culture that German media present and that people in Germany consume: music, film, TV and streaming, celebrities and royals, influencers, games, books, podcasts, comedy, fashion and beauty, food, fitness, relationships, family, home and consumer products, and the opinions that pop culture voices on politics, religion and ways of living.
- **Unit of analysis:** the ISO week. Every source is stored with its own dates; `scripts/build_weekly.py` aggregates them to weeks, the unit in which the other datasets of the series are compared.
- **Volatile sources first.** Charts and trending lists show only their current state, and feeds of celebrity media cover a few hours. They are the core of the dataset and are read in every run: the live lists twice a week, the daily Wikipedia lists for every day. A stable backbone (the daily Wikipedia views of a fixed registry of 215 entities since 2015) gives series that keep their meaning over the years.
- **Horizon:** the data is collected over many years and analyzed as long time series. The design favors stable definitions, durable sources and a moderate data volume over the completeness of single items.
- **Repository:** public. No license is granted for the code or the data of this repository while the dataset builds its history. Every record names the license or terms of its source in the field `license`; the sources are listed in the [README](README.md#sources-and-attribution).
- **Schedule:** one workflow runs the due collectors on Tuesdays and Fridays at 02:07 Europe/Berlin, the schedule of dataset.politics.germany and dataset.economics.germany, so that the news of the datasets cover the same windows. The runs are three and four days apart. The Tuesday run sees the charts and the coverage after the release weekend (new music and many series appear on Fridays, shows on Saturdays and Sundays), the Friday run the state at the end of the week. A run takes at most 30 minutes; the history is imported by a separate `backfill` run or by the first runs.
- **Model and cost:** Google Gemini (`gemini-2.5-flash` by default, thinking off) labels up to 350 news articles and press releases per run, about 700 per week, with the request handling of the other datasets. The cost depends on the number of labelled items, not on the number of runs; at the list prices it is about 0.01 US dollars per request of ten items, about 0.35 US dollars per run and 3 to 4 US dollars per month. The run summary reports the measured tokens and cost.
- **No paid access:** every source is free and needs no key. The Gemini API key is the only secret.

**Consequence of the schedule for news.** RSS feeds and news sitemaps list only recent items; the feeds of Promiflash and Mein-MMO cover a few hours. With runs every three or four days and a lookback of 96 hours, the pipeline collects what the feeds still list at run time, so news coverage is a sample, not a complete record; feeds that cover a few hours contribute only the items of the hours before a run. The labelled share is smaller still: about 350 of more than 1,000 new pop culture items per run (Section 11).

---

## 2. Design Rules

1. **Point-in-time correctness.** Every record has `observed_at`, the UTC time at which the pipeline observed it. Filtering `observed_at <= t` reproduces what was known at time t. Records of items older than the start of collection have `backfilled: true`.
2. **Snapshots on change.** A chart is stored when its list differs from the stored state: live lists as revisions of the record of the day of observation, daily lists once per day of the list. Unchanged lists are not stored again, so the data volume follows the change rate of the sources, not the number of runs.
3. **Five kinds of datasets.**
   - *Snapshot:* `chart_snapshots` (rule 2).
   - *Day series:* `wikipedia_pageviews` stores, per entity and run, the days since the last stored day.
   - *Change:* `chart_catalog` and `entities` store an entry when it appears and again when a field changes.
   - *Event:* `news_items`, `press_releases` and `text_annotations` store each item once.
4. **Raw data and annotations are separated.** Gemini labels are stored in `text_annotations`, keyed by item and `extraction_hash`, so that they can be regenerated with another model or prompt without changing the raw data.
5. **Declarative registries.** The charts (`config/charts.toml`), the entities (`config/entities.toml`) and the news and press sources (`config/sources.toml`) are configuration files with `added` and `removed` dates. The charts and entities are written to datasets, so that the coverage of every chart and entity is known from the data alone.
6. **Permanent identifiers.** Chart ids (`<provider>.<chart>`), entity ids, source ids, dataset names, field names and `record_id` formats are never reused for a different meaning.
7. **Additive schema changes.** New fields get a description and are added without changing existing ones; a changed meaning requires a new field and a new `SCHEMA_VERSION`. A snapshot test guards the record schema.
8. **Fixed vocabularies.** Fields, opinion targets, family forms, lifestyle topics, platforms and product categories are closed vocabularies in `src/schema.py`, so that counts are comparable over time. Values outside a vocabulary are mapped to `Other` or dropped. A vocabulary is only extended, and every extension is logged (Section 12).
9. **No full texts, no private persons.** Only titles, URLs, metadata and derived variables of articles and press releases are stored. People are stored only as articles of the German Wikipedia (Wikidata id and title); other names are counted, not stored. Author names are not stored.
10. **Logged measurement changes.** Every change of a source, a chart, a filter, a vocabulary or a counting rule is recorded in the measurement log (Section 12), so that structural breaks can be identified.

---

## 3. Datasets and Variables

All fields are defined with descriptions in [src/schema.py](src/schema.py); the README shows the data model and one example record per dataset.

### 3.1 Common Fields

Every record has: `record_id`, `revision`, `dataset`, `schema_version`, `observed_at`, `source_id`, `source_url`, `license`, `backfilled`.

### 3.2 Chart Snapshots

`chart_snapshots` stores the lists of the charts in [config/charts.toml](config/charts.toml):

| Domain | Charts | Period |
|---|---|---|
| `music` | Apple Music most played songs and albums, Germany | live |
| `podcasts` | Apple Podcasts top shows and trending episodes, Germany | live |
| `apps` | App Store top free and top paid iPhone apps, Germany | live |
| `books` | Apple Books top paid and top free e-books, top audiobooks, Germany | live |
| `search` | Google Trends trending searches, Germany | live |
| `attention` | Most viewed articles of the German Wikipedia; most viewed articles from Germany across all editions | day |

- **Record ids:** `chart:<chart_id>:<chart_date>`. A live list observed several times a day with different contents has one revision per state.
- **Entries:** `rank`, `title`, `creator`, `item_id`, `url`, `genres`, `release_date`, `metric` and `details`. The meaning of `metric` is given by `metric_name` (views; views rounded up to the next thousand; approximate searches as a lower bound). `details` holds further source values as text: the content advisory of Apple entries, for trending searches the start time, the volume as published (`approx_traffic`, e.g. `500+`) and the first linked news article, for the Wikipedia lists the rank at the source before main and special pages were removed (`source_rank`).
- **Filters:** the Wikipedia lists drop the main page, special pages and pages outside the article namespace, then keep the first 100 articles.

### 3.3 Entities and Attention

[config/entities.toml](config/entities.toml) lists 215 entities with type and field: people (62), concepts (39), platforms (20), festivals and events (18), TV shows (17), products (11), brands (10), sports (9), music groups (8), video games (8), institutions (5), film series (4), movements (3) and awards (1). They are chosen to cover the fields of the annotations and the opinion targets with stable articles, e.g. Bundeswehr, Wehrpflicht in Deutschland, Römisch-katholische Kirche in Deutschland, Kirchenaustritt, Ehe, Kinderlosigkeit, Polyamorie, Veganismus. Every title was checked against the German Wikipedia on 2026-10-28 (an existing article, no redirect, no disambiguation page).

`wikipedia_pageviews` holds the daily views by people (agent `user`) of these articles since 2015-07-01, the start of the Pageviews API. The collector runs once a week and resolves the titles through redirects first; `wikidata_qid` identifies the article independently of renames.

### 3.4 News and Press Releases

`news_items` and `press_releases` store the metadata of pop culture items: title, URL, publication time, section, feed categories, keywords and, from the article page where permitted, schema.org type, agency credit, number of authors, word count, paywall and video. The outlet fields (`outlet_type`, `owner_group`, `outlet_focus`, `audience`) describe the author, so that items can be compared between tabloids, celebrity magazines, music and film magazines, trade press and general outlets.

**Prefilter.** Items of specialist outlets (with `focus`) pass. Items of general outlets pass if their feed or the section of their URL is a culture, entertainment or lifestyle section (`POP_PATH_TOKENS`, matched only on path segments of at most two words, not on the slug of the title), or if title, teaser, categories or keywords contain a pop culture term (`POP_TERMS`). Items of sports sections pass only through a pop culture section. The filter is broad on purpose; the annotation decides with `is_pop_culture`.

### 3.5 Text Annotations

Gemini reads title, teaser and, where permitted, the lead text of up to 1,500 characters, and returns per item:

| Group | Fields |
|---|---|
| Classification | `is_pop_culture`, `domain`, `domains_secondary`, `issue_detail`, `article_type`, `overall_tone`, `reports_controversy` |
| Consumption | `works` (title, kind, role, tone), `people` (resolved to German Wikipedia articles), `unresolved_person_count`, `content_origin_countries`, `upcoming_events` |
| Products | `products` (name, category, role), `platforms`, `purchase_recommendation`, `sponsored` |
| Ways of living | `lifestyle_advice`, `promoted_values`, `generations`, `family_forms` (form to tone) |
| Opinions | `stances` (target, stance, holder) |
| Press releases | `communication_type` (`Not applicable` for news) |

The instructions ask the model to label only what the text states or clearly implies, to record opinions only when a holder evaluates a target, and to separate the opinion of the outlet from the opinions of the people it reports. `clean_annotation` in [src/annotation.py](src/annotation.py) normalizes the answers: unknown values become `Other` or `Mentioned`, duplicates are merged, lists are cut (15 mentions, two secondary fields, three values), dates of upcoming events must lie within two years, and country codes follow ISO 3166-1 (UK and EL become GB and GR).

**People.** The names that Gemini returns are looked up in the German Wikipedia (MediaWiki API, through redirects). A person is stored with the Wikidata id and title of the article if the article exists, is not a disambiguation page and is not a redirect to a section of another article; otherwise only `unresolved_person_count` is raised. At most 15 names per item are looked up.

### 3.6 Vocabularies

| Vocabulary | Size | Content |
|---|---|---|
| `PopDomain` | 26 | Fields of pop culture and society, from Music to Politics and society |
| `StanceTarget` | 62 | Politics (Bundeswehr and military, military service and conscription, police, government, parties, AfD, democracy, EU, USA, Russia, support for Ukraine, Israel, migration, climate, public broadcasting, welfare state, taxes), society (gender-neutral language, feminism, LGBTQ and trans rights, diversity, cancel culture, patriotism, Heimat), religion (Christianity and the churches, Catholic Church, Islam, Judaism, religion in general, spirituality), family and relationships (marriage, having children, childfree living, traditional gender roles, stay-at-home parenting, working parents, single life, divorce, polyamory, same-sex partnerships), consumption and ways of living (veganism, meat, alcohol, cannabis, smoking, fitness culture, body positivity, cosmetic surgery, weight-loss drugs, sustainability, consumerism, luxury, flying, cars, career ambition, work-life balance) and media (social media, screen time, AI, influencers, streaming) |
| `FamilyForm` | 13 | Married and unmarried couples with children, couples without children, single parents, patchwork and rainbow families, same-sex couples, singles, open or polyamorous relationships, long-distance relationships, multigenerational families, divorce or separation |
| `LifestyleTopic` | 23 | Diet, plant-based eating, alcohol-free living, fitness, mental health, sleep, beauty, style, home, sustainability, minimalism, travel, saving, career, work-life balance, dating, sex, parenting, pets, digital detox, spirituality, self-optimization |
| `PromotedValue` | 10 | The basic values of Schwartz |
| `Platform` | 38 | Streaming, social media, messaging, music, audiobook, gaming, shopping, dating and AI chatbot platforms |
| `ProductCategory` | 20 | From fashion and beauty to finance and insurance |

---

## 4. Correlation with Other Datasets

The dataset is built to be joined with [dataset.sociodemographics.germany](https://github.com/kleinnconrad/dataset.sociodemographics.germany), [dataset.politics.germany](https://github.com/kleinnconrad/dataset.politics.germany), [dataset.economics.germany](https://github.com/kleinnconrad/dataset.economics.germany) and later datasets. The datasets share these conventions:

| Key | Use |
|---|---|
| `observed_at` and the event dates (`chart_date`, `published_at`, `daily_views`) | Time is the main join key: aggregate all datasets to days, ISO weeks (`YYYY-Www`) or months in UTC. `observed_at` keeps the joins point-in-time correct. |
| `record_id` of news items (`news:` + hash of the normalized URL) | All datasets build the id with the same URL normalization (`src/urls.py`), so the same article has the same `record_id` in each. An article that several pipelines collected can be analyzed with all its labels. |
| Outlet ids (`spiegel`, `welt`, `bild`, `t_online`, `tagesspiegel`, `zdfheute`, `dwdl`, ...) | The outlets that several datasets collect have the same id, so outlet effects can be compared across topics. |
| Wikidata ids (`wikidata_qid`) | People of the annotations and the registry articles here, politicians in dataset.politics.germany and companies in dataset.economics.germany carry Wikidata ids, which link entities across datasets and to external data. |
| ISO 3166 codes | Countries (`content_origin_countries`) and German states (`home_state`) use ISO codes in all datasets. |

Examples of cross-dataset questions:
- **Politics:** does the balance of opinions on the Bundeswehr and on conscription in pop culture move with the debate and the polls on defence policy (dataset.politics.germany)? Do celebrities who speak about parties or migration precede changes in the attention to those parties?
- **Society:** do the presentation of family forms, the stances on marriage, children and childfree living and the share of parenting advice relate to the trends of births, marriages and household forms (dataset.sociodemographics.germany)? Do religion and the churches receive more or less favorable mentions over the years, against church membership figures?
- **Economy:** do purchase recommendations, sponsored items and the paid app and book charts move with consumer sentiment and retail sales (dataset.economics.germany)? Which brands of the registry gain attention before their companies report results?
- **Press across topics:** how much of the coverage of an outlet is pop culture versus politics or economy, and do outlets that report the same celebrities differ in tone?

---

## 5. Questions and Indicators

| Question | Main variables | Weekly indicators of `scripts/build_weekly.py` |
|---|---|---|
| What does pop culture favour and reject, also in politics and religion? | `stances`, `promoted_values`, `reports_controversy`, `overall_tone` | `stance_mentions`, `stance_balance`, `stance_balance_celebrities`, `promoted_value`, `reports_controversy_share`, `tone_share` |
| What do people consume? | `chart_snapshots`, `works`, `people`, `upcoming_events`, `wikipedia_pageviews` | `chart_rank`, `chart_days_listed`, `work`, `person`, `pageviews`, `domain_share` |
| Which products does pop culture push? | `products`, `platforms`, `purchase_recommendation`, `sponsored`, app charts | `product`, `product_recommended`, `product_category`, `platform`, `purchase_recommendation_share`, `sponsored_share` |
| Which ways of living does it recommend? | `lifestyle_advice`, `promoted_values`, `generations`, `article_type` | `lifestyle_advice`, `promoted_value`, `generation`, `article_type_share` |
| What does it think of family, marriage, children and alternative lifestyles? | `family_forms`, stances on family targets | `family_form_mentions`, `family_form_tone_balance`, `stance_balance` of the family targets |
| Who speaks to whom? | `audience`, `outlet_type`, `generations`, `content_origin_countries` | `items_by_outlet_type`, `items_by_audience`, `origin_country` |

A balance is (favorable − unfavorable) / mentions, from −1 to 1, and every row carries the number of observations `n`, so that weeks with few mentions can be weighted or dropped. Counts of the annotations are counts in the labelled sample; divide by `annotated_items` or `pop_culture_items` of the same week before comparing weeks.

---

## 6. Sources and Fetch Rules

All requests go through `src/web.py` with the user agent `GermanPopCultureDataset/0.1 (+https://github.com/kleinnconrad/dataset.popculture.germany)`:

- **robots.txt** is checked for every website request (RFC 9309, longest match). Robots files that answer with a server error are requested again before the host is skipped for the run. The documented APIs of Wikimedia (REST and MediaWiki API) are called with `respect_robots=False` under their terms.
- **TDM reservations:** a TDMRep header, `/.well-known/tdmrep.json` or meta tag sets a source to `tdm_reserved`. A reservation in natural language in robots.txt or the imprint sets `fetch_pages = false`.
- **Bot protection:** HTTP 401/403, challenge pages and CAPTCHAs set a source to `blocked` and pause a chart for 30 days. No headless browsers.
- **Pacing:** one request per second per host, ten per second for wikimedia.org; retries with backoff for server errors and rate limits; crawl delays up to 120 seconds are honored.

### 6.1 Charts

| Provider | Access | Terms | Notes |
|---|---|---|---|
| Apple (RSS Builder of Apple Marketing Tools) | JSON feeds `rss.marketingtools.apple.com/api/v2/de/...`, robots.txt allows them | Apple website terms; only positions and metadata are stored | Feeds of 100 entries for songs, albums, podcasts, episodes, free and paid apps, paid and free books, audiobooks |
| Google Trends | RSS `trends.google.com/trending/rss?geo=DE`, robots.txt allows it | Google Terms of Service | About 10 trending searches per list with approximate volume and linked news |
| Wikimedia | Pageviews REST API (top and top-per-country) | CC0 1.0 | Daily lists; the per-country list rounds views up to the next thousand and omits articles with few views |

**Not admitted** (entries `[[excluded]]` of `config/charts.toml`, checked on 2026-10-28):

| Candidate | Reason |
|---|---|
| iTunes film charts (`itunes.apple.com/de/rss/...`) | robots.txt of itunes.apple.com disallows `/*/rss/*` |
| Deezer charts (`api.deezer.com/chart`) | robots.txt disallows all paths, and `tdmrep.json` reserves text and data mining |
| Netflix Top 10 | robots.txt disallows all paths for user agents that it does not name |
| Steam top sellers | the Steam Subscriber Agreement forbids scripts and bots that interact with Steam; the store API is not documented |
| mydealz trending deals | the terms count the copying of platform content and making it available to third parties as misuse |
| Spotify charts | the chart download requires an account; not checked live |
| YouTube trending | the API needs a key and its terms limit the storage of API data |
| Offizielle Deutsche Charts | not checked live; to be checked with the probe of `source_review.yml` |

### 6.2 Outlets and Press Sources

Admitted on 2026-10-28 after a live check of feeds, robots.txt, TDM reservations and imprint (details in the comments of [config/sources.toml](config/sources.toml)):

| Group | Sources | Page fetching |
|---|---|---|
| General outlets | Der Spiegel, Die Welt, Süddeutsche Zeitung, Der Tagesspiegel, Focus Online, t-online, n-tv.de, Bild, ZDFheute, Berliner Zeitung, taz, Hamburger Abendblatt | off for Spiegel, SZ, Tagesspiegel, t-online, taz and Abendblatt (TDM reservation in natural language) |
| Celebrity and lifestyle | Promiflash, Utopia | off for Utopia |
| Music | Musikexpress, Rolling Stone, laut.de | off for Musikexpress and Rolling Stone |
| Film, TV and games | DWDL.de, Quotenmeter.de, Moviepilot, Mein-MMO | on |
| Press releases | game (association of the German games industry) | on |

The candidates that were not admitted are listed with status and reason in the `[[excluded]]` entries: Vogue, GQ and Glamour (Condé Nast terms forbid collecting content), watson (terms forbid the transfer into a machine-readable form), GameStar and GamePro (HTTP 403), Serienjunkies (robots.txt), giga, Filmstarts, rap.de, jetzt, familie.de, Bitkom and BVMI (no usable feed), Stern (bot protection) and Zeit (machine-readable TDM reservation), and further candidates with the status `unchecked` (Bunte, Gala, Brigitte, Elle, Men's Health, Eltern, 1LIVE, PULS, Deutschlandfunk Nova, Deutschlandfunk Kultur, Börsenverein, FFA, ZDF press, ProSiebenSat.1), which every source review checks.

### 6.3 Admission of Sources

The same rule applies to every candidate, so that the decisions are consistent across the datasets of the series:

1. **Excluded (`terms`):** the terms or the imprint forbid the inclusion in databases, automated collection or the transfer of content into a machine-readable form.
2. **Excluded (`tdm_reserved`, `blocked`, `robots`):** a machine-readable TDM reservation, bot protection, or a robots.txt that disallows the feeds.
3. **Admitted with `fetch_pages = false`:** robots.txt or the imprint reserves text and data mining under § 44b UrhG in natural language. The feed items (title, teaser, categories) are used; the pages are not fetched.
4. **Admitted:** a general reservation of rights or a ban on reprints and on the inclusion in other online services without consent. The dataset stores no texts, only metadata and derived variables, as the other datasets do for outlets with the same notices (e.g. t-online).

The source review applies rules 2 and 3 automatically and quotes the clauses of the imprint in its pull request, so that rules 1 and 4 can be decided by reading them before the merge.

---

## 7. Storage, Partitioning and Data Volume

```text
data/
├── chart_snapshots/year=2026/month=10/chart_snapshots_2026-10-28.json
├── news_items/year=2026/month=10/news_items_2026-10-28.json
├── ...
└── state/
    ├── seen/<dataset>.json             # written items with content hash and revision
    ├── collectors.json                 # first run, last run, failures, cursor per collector
    ├── sources.json                    # health status per news and press source
    └── run_log.jsonl                   # one line per run
```

- **Partition key:** the UTC day of the run that observed the record. A run writes at most one file per dataset; records are merged by `record_id` and `revision`.
- **Folder names:** `year=` and `month=` let DuckDB, Polars and pyarrow read a dataset as one table with partition pruning.
- **Format:** one JSON list per file with one record per line.
- **Seen stores:** the keys of news and press items are kept for 30 days; charts, entities and catalog entries permanently.
- **Measured sizes** (test run on 2026-10-28): an Apple list of 100 entries takes 28 to 37 KB, a list of trending searches 4 KB, a news item about 1 KB, the registry of 215 entities 116 KB.
- **Expected volume:** the news dominate with about 150 MB per year, the annotations add about 55 MB. The charts add about 45 MB per year: about 270 KB per run if every Apple list changed since the previous run (about 28 MB per year), two daily Wikipedia lists of about 24 KB (about 17 MB per year) and the trending searches. The pageviews add a few MB. Git compresses the files to a fraction of their size.
- **Long-term plan:** when the repository approaches 1 GB, the snapshots and news of completed years move to a separate data repository with the same layout, or are archived as compressed Parquet files per year and dataset.

---

## 8. Repository Structure

```text
dataset.popculture.germany/
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── ci.yml              # lint, tests, commit messages
│       ├── notify.yml          # run summary as a commit comment that mentions the owner
│       ├── pipeline.yml        # due collectors, Tuesdays and Fridays 02:07 Europe/Berlin; backfill by hand
│       └── source_review.yml   # monthly review of the sources as a pull request
├── config/
│   ├── charts.toml             # chart providers, charts and excluded candidates
│   ├── entities.toml           # registry of the entities
│   └── sources.toml            # news outlets, press-release sources and excluded candidates
├── data/                       # datasets and state, committed by the workflow
├── src/
│   ├── main.py                 # entry point
│   ├── pipeline.py             # collector order, isolation, run log
│   ├── settings.py             # environment variables
│   ├── web.py                  # HTTP client with the fetch rules
│   ├── schema.py               # record models, vocabularies and Gemini response models
│   ├── chart_catalog.py        # chart configuration
│   ├── entities.py             # entity registry
│   ├── sources.py              # source configuration and health rules
│   ├── wikipedia.py            # title resolution through the MediaWiki API
│   ├── content.py              # metadata and lead text of article pages
│   ├── annotation.py           # Gemini labels
│   ├── storage.py, state.py, urls.py
│   ├── generate_dataset_overview.py
│   ├── source_review.py        # monthly review of the sources, charts and registry
│   └── collectors/
│       ├── base.py             # collector interface and run context
│       ├── registries.py       # chart catalog and entities
│       ├── charts.py           # chart snapshots and their parsers
│       ├── feeds.py            # news, press releases, annotations, people
│       └── pageviews.py        # Wikipedia views of the entities
├── scripts/
│   ├── run_summary.py          # run summary for notify.yml
│   └── build_weekly.py         # weekly indicators
├── tests/                      # offline tests with a fake web and a fake Gemini client
├── pyproject.toml
├── uv.lock
└── README.md
```

---

## 9. Pipeline Run

`src/main.py` starts the run; `src/pipeline.py` runs the collectors in the order of `COLLECTOR_ORDER`:

1. **chart_catalog, entities:** write new and changed entries of the registries (no requests).
2. **charts:** read every due chart with conditional requests; store live lists that changed; fetch the missing days of the daily lists. The state of the charts (validators, pauses, days fetched) is saved after every chart.
3. **feeds:** read the RSS/Atom feeds and news sitemaps of all due sources; keep new pop culture items published within 96 hours; select up to 350 items (press releases first, then news items in turns across the outlets); fetch article pages where permitted, annotate ten items per Gemini request with up to four requests at the same time, resolve the named people, and write items and annotations; write the other new items without labels.
4. **wikipedia_pageviews** (weekly): fetch the days since the last stored day per entity.
5. The workflow updates the README overview and commits `data/` and `README.md`. `notify.yml` then posts a summary of the run, with the Gemini tokens and their estimated cost, as a commit comment that mentions the repository owner.

**Run modes.** A regular run (`normal`) has a deadline of 25 minutes (`RUN_DEADLINE_MINUTES`) and a job timeout of 30 minutes. A `backfill` run imports the history of the charts and the pageviews without the run intervals (deadline 85 minutes, timeout 90 minutes). `shadow` and `dry-run` work on a copy of the state and commit nothing.

**Measured duration** (test runs on 2026-10-28, outside GitHub Actions):

| Step | Duration |
|---|---|
| chart_catalog, entities | under 1 s |
| charts: 10 lists of Apple and Google (robots.txt of Apple answered with server errors and was requested again) | 2.7 min |
| feeds: reading 22 sources (dry run, 1,271 new pop culture items within 72 hours) | 1.4 min |
| feeds: pages and Gemini (350 items) | 6 to 8 min (estimate, as for the 400 items of dataset.economics.germany) |
| wikipedia_pageviews (215 articles, weekly) | about 2 min (estimate; the test environment had no access to the API) |

---

## 10. Failure Handling

- **A collector fails:** the error is logged, recorded in `collectors.json` and the run log, and the next collector starts. Data already written stays. The run exits with code 1.
- **A chart fails** (HTTP error, invalid response, parser error): the other charts continue; the failure is counted and named in the run summary. The charts collector fails only if every chart read failed. A blocked chart is paused for 30 days.
- **A news or press source fails:** the other sources continue; the source health rules apply (`broken`, `blocked`, `tdm_reserved`).
- **Gemini returns an unusable response:** the batch is split in halves down to single items; items without usable labels are written without annotation.
- **Gemini quota, call budget or API error:** the annotation stops; the remaining items are written without annotation. A quota or API error marks the collector as failed; reaching the call budget does not.
- **People cannot be resolved** (MediaWiki API unreachable): the annotation is written with the people that were resolved; the failed requests are counted (`people_resolution_failed`).
- **Run deadline:** after `RUN_DEADLINE_MINUTES` the collectors start no new work; a collector that stops early has the status `partial` and continues in the next run.
- **Concurrent pushes:** the workflow pulls with rebase and retries the push up to three times.

---

## 11. Known Data Issues

- **Labelled sample.** About 350 of more than 1,000 new pop culture items per run are labelled. The selection takes press releases first and then news items in turns across the outlets, newest first, so every outlet is represented, but large outlets are labelled at a lower rate than small ones. Weight by the item counts of `news_items` per outlet when estimating shares of all coverage.
- **Feeds that cover a few hours.** Promiflash and Mein-MMO list only the items of a few hours; most of their items published between two runs are missed.
- **Live charts twice a week.** The Apple charts and the trending searches are observed only at the runs; states between two runs are not seen. Trending searches that rise and fall within a few days are therefore mostly missed. The daily Wikipedia lists are not affected.
- **Publication times.** The feeds of Quotenmeter.de have no publication times, and every item of the laut.de feeds carries the generation time of the feed; `published_at` is then unknown or the time of the first observation.
- **Apple update times.** Apple states the time at which it generated the response as `updated`, not the time at which the chart changed; `source_updated` of Apple lists is therefore close to `observed_at`.
- **Trending searches.** Google publishes the search terms in lower case and with approximate volumes (`500+`); `metric` is the lower bound. Terms and linked news titles can name people; they are stored as Google publishes them.
- **Wikipedia per-country list.** Views are rounded up to the next thousand, and articles below a threshold are omitted by Wikimedia.
- **Prefilter of general outlets.** The terms are broad (e.g. `Show`, `Fans`, `Prinz`), so some items of general outlets are not pop culture; `is_pop_culture` of the annotation marks them. Feeds of culture sections include high culture (theatre, art, literature), which the field `domain` separates (`Art, stage and museums`, `Books and literature`).
- **Opinions are those of the texts.** `stances` records opinions that the items state or report, not the opinion of the population; the holder distinguishes the outlet, celebrities, fans and ordinary people.

---

## 12. Measurement Log

Changes of sources, charts, filters, vocabularies and counting rules, and known breaks in the sources. Add a line for every change.

| Date | Dataset | Change |
|---|---|---|
| 2026-10-28 | all | Design of the pipeline, with runs on Tuesdays and Fridays at 02:07 Europe/Berlin and a news lookback of 96 hours; 12 charts of Apple, Google Trends and Wikimedia, registry of 215 entities, 21 outlets and 1 press source, all checked live on this day (Wikimedia through curl, because the Pageviews API refused the HTTP client of the test environment). |
| 2026-10-28 | `chart_snapshots` | Live lists dated by the UTC day of observation; one revision per new state of a day. Daily Wikipedia lists without main page, special pages and pages outside the article namespace; 100 entries. |
| 2026-10-28 | `news_items` | Prefilter for general outlets: pop culture feeds and URL sections (`POP_PATH_TOKENS`, path segments of at most two words) or terms (`POP_TERMS`); sports sections only through a pop culture section; outlets with a `focus` pass without the filter. Media sections (`medien`) do not count as pop culture sections; the crime series Tatort counts as a term only in quotes or as `Tatort-` and `Tatort:`. |
| 2026-10-28 | `text_annotations` | Vocabularies of Section 3.6; values outside them become `Other` or `Mentioned` or are dropped. People resolved to German Wikipedia articles; redirects to sections and disambiguation pages do not count. Up to 350 items per run. |

---

## 13. Implementation Status

| Part | Status |
|---|---|
| Chart snapshots of Apple, Google Trends and Wikimedia with conditional requests and pauses | implemented |
| Chart catalog and entity registry with their datasets | implemented |
| Wikipedia pageviews of the entities | implemented |
| News items, press releases, Gemini annotations with people resolved to Wikidata ids | implemented |
| Monthly source review with pull requests, probe of candidates, checks of charts, providers and registry titles | implemented |
| Weekly indicators (`scripts/build_weekly.py`) | implemented |
| Check the `unchecked` candidates (Bunte, Gala, Brigitte, 1LIVE, PULS and others) with the source review; check the Offizielle Deutsche Charts with the probe | planned |
| Film and TV charts from a source whose terms permit collection (cinema admissions, streaming rankings) | planned |
| Festival and concert calendars as a dataset of events | planned |
| Review sheet for the annotation and a hand-labelled sample to measure agreement | planned |
