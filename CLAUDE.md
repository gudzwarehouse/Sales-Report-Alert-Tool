# Sales Report Tool — CA sales threshold alerting

## Hard rule: never touch production, always work on copies

**Never write to, overwrite, or modify any live/production source in
place** — not `M:\Excel Automatic\Sales CA Report\*.csv`, not
`M:\Excel Automatic\StockFeeds.xlsm`, not any other file on `M:\`, and never
send a real alert email to `NOTIFY_EMAIL_TO`/`NOTIFY_EMAIL_CC` while testing.
All modernization work happens on **local copies or newly created files in
this repo**. Pulling a fresh copy of a CSV/workbook *from* the shared drive
to test against is fine (read-only); writing back to it is not.

## Hard rule: ChannelAdvisor ("CA") credentials are read-only, always

`CA_UK_*`/`CA_IRE_*` in `.env` are live ChannelAdvisor OAuth credentials
(pulled from `Amazon Padding Automation` for reference — see
`.env`'s comment above them). **Any use of these credentials against
ChannelAdvisor's *data* endpoints in this project is GET-only** —
`CA_API_URL`/`CA_ORDERS_API_URL` (Fulfillments, Orders) for reading data
back, never a write. The one exception is `CA_TOKEN_URL` — the OAuth
refresh-token exchange is necessarily a `POST` (that's how OAuth works),
but it's an auth handshake, not a ChannelAdvisor data mutation, so it
doesn't violate this rule; `ca_orders_client.py` is the only place that
calls it. Never call ChannelAdvisor's Ship endpoint (`POST
.../Orders(id)/Ship`) or any other mutating ChannelAdvisor call from this
project — that's `Amazon Padding Automation`'s job
(`channeladvisor_ship.py`), not this one's.

As of Phase 3, `CA_UK_*` is load-bearing, not just reference: it's how the
pipeline now reads its order data (see "Data source" below). `CA_IRE_*`
remains unused — a live diff (see "Data source") showed Ireland-shipping
orders already appear inside the UK profile's own export, so IRE looks
unrelated to this tool; kept in `.env` for reference in case that
assumption turns out wrong.

## What this is

A single Excel workbook (`Sales Reports - CA.xlsm`) that polls two
shared-drive CSV drops every cycle and emails an alert when sales volume
crosses configured thresholds. It only runs while the file is **open in
Excel on one machine**, kept alive by VBA `Application.OnTime` timers — the
same single-point-of-failure pattern as the sibling `VB Stock update`
project's `StockFeeds.xlsm`, and the reason this one is also being
modernized.

**Local working copies in this repo folder:**
- `Sales Reports - CA.xlsm` — file-open password removed, VBA project
  unlocked for editing (this is the one to open in Excel)
- `Sales Reports - CA.BACKUP.xlsm` — untouched original (still encrypted,
  VBA still locked) — keep as a rollback reference, don't edit
- `VBA_Source/` — plain-text export of every VBA module/class
  (`oletools.olevba`'s `VBA_Parser.extract_macros()`), kept in sync with the
  unlocked workbook. **Contains hardcoded secrets in plaintext — gitignored,
  never commit.**
- `.env` — real secrets extracted from the VBA (gitignored); `.env.example`
  documents the shape with blanks
- `venv/` — Python venv used for the unlock/extract tooling and the new
  notifier below

## How it runs

`ThisWorkbook.Workbook_Open` (`VBA_Source/ThisWorkbook.cls:27`) arms the
timer loop, skipped entirely if the Windows username is `TEST_USERNAME`
(dev/test gate, `Module1.bas:5`):

| Timer | Interval | Calls |
|---|---|---|
| `RunProcess` | 40 min (`timerInterval`) | Main pipeline, re-arms itself |
| `ImportSKU` | ~daily (`stockImportDelay` then every 24h) | Refreshes `SKU Lookup` sheet from `StockFeeds.xlsm` |

### `RunProcess` (`Module1.bas:633`) — in order:

1. `ReadLastHourSKU` — reads `Orders Last 1 hour*.csv`, maps each row to
   `OurSKU` via the `SKU Lookup` sheet (VLOOKUP), counts orders per SKU, and
   emails per-threshold-row recipients (from the `Emails` sheet, row 1/2)
   when a SKU's count crosses its configured threshold **and** isn't listed
   on the `Exceptions` sheet.
2. `ReadLastHour` — same CSV, but alerts on **total** order count (not
   per-SKU) against thresholds in `Emails` row 2, then **deletes the CSV**
   (`Kill`) — the only place the 1-hour file is ever removed.
3. `ReadLast4Hours` — same per-SKU logic as step 1, but for
   `Orders Last 4 hours*.csv` and `Emails` rows 20/21. Does **not** delete
   the CSV (comment: "don't delete. Need this for the next one").
4. `OldReadLast4Hours` — a second, near-duplicate total-count check against
   the same 4-hour CSV (mirrors `ReadLastHour`'s logic), and this one *does*
   delete the file. Looks like legacy code kept alongside its replacement
   (`ReadLast4Hours`) rather than removed — confirm with the business owner
   before dropping it in the Python port, don't assume it's dead.
5. Saves the workbook, re-arms `RunProcess` for `timerInterval` later.
6. Any error anywhere in 1-5 emails `NOTIFY_EMAIL_TO`/`NOTIFY_EMAIL_CC` via
   `SendEmailNotification` instead of re-arming.

### Threshold configuration (`Emails` sheet)

Thresholds and their recipients live side-by-side as paired rows, read
dynamically by column count (`SetSalesAmounts1`/`4`, `Module1.bas:42-62`):
- Row 1 = per-SKU thresholds (1-hour), row 2 = total thresholds (1-hour)
- Row 20 = per-SKU thresholds (4-hour), row 21 = total thresholds (4-hour)
- For each threshold column `j`, the recipient list is the contiguous
  non-empty cells starting two rows below that same column
  (`GetEMailList`, `Module1.bas:64-81`) — e.g. threshold in row 1 col C →
  recipients in col C starting row 3, downward until a blank cell.
- **Column C's recipient list is longer than column B's, and the difference
  isn't just more people**: on both the 1-hour and 4-hour groups, column C
  (the higher threshold — 6 orders/SKU, 45 total, 20 orders/SKU, 145 total)
  has two extra addresses beyond the 9 named staff:
  `feedcheckersms.<id>@zapiermail.com` and
  `feedcheckphonecall.<id>@zapiermail.com`. These are Zapier email-to-SMS
  and email-to-phone-call trigger addresses — crossing the *higher*
  threshold fires an SMS and a phone call in addition to email, crossing
  only the *lower* one is email-only. Confirmed live in the `Emails` sheet
  (rows 12-13 and 31-32). The Python port's `config/thresholds.json`
  therefore stores recipients per threshold bucket (paired with that
  bucket's own sku/total threshold values), not once per time window.

### `ImportSKU` (`Module1.bas:651`)

Opens `StockFeeds.xlsm` read-only (own hidden Excel instance if not already
open elsewhere) with `STOCKFEEDS_XLSM_PASSWORD`, copies its `Inventory`
sheet's SKU + description columns into this workbook's `SKU Lookup` sheet,
closes it, re-arms itself for ~24h later.

### Email sending (`SendEmailNotification` / `SendEmailList`, `Module1.bas:527-631`)

Both build a `CDO.Message` over Gmail SMTP (SSL, port 465) using
`NOTIFY_EMAIL_FROM` / `NOTIFY_EMAIL_APP_PASSWORD`, plain-text body only. Both
no-op (just `Debug.Print`) when running as `TEST_USERNAME`. `SendEmailList`
differs only in accepting an array of recipients (joined with `;`) instead
of a single to/cc pair.

## Secrets (now in `.env`, originally hardcoded in `Module1.bas`)

| Secret | Source line | `.env` key |
|---|---|---|
| Gmail app password | `Module1.bas:542` (VBA) — Python notifier uses a different account, see below | `NOTIFY_EMAIL_APP_PASSWORD` |
| Gmail sender address | `Module1.bas:539,541` (VBA) — Python notifier uses a different account, see below | `NOTIFY_EMAIL_FROM` |
| Alert recipient (to) | `Module1.bas:32` | `NOTIFY_EMAIL_TO` |
| Alert recipient (cc) | `Module1.bas:33` | `NOTIFY_EMAIL_CC` |
| StockFeeds open password | `Module1.bas:40` | `STOCKFEEDS_XLSM_PASSWORD` |

**The Python notifier (`notifications.py`) deliberately sends from a
different Gmail account than the VBA does** — `.env`'s
`NOTIFY_EMAIL_FROM`/`NOTIFY_EMAIL_APP_PASSWORD` were switched to the shared
`automationstatusgudz@gmail.com` account already used by the other Final
Automations projects (e.g. `Amazon Padding Automation`), instead of the
VBA's original `gudkaveer@gmail.com`. The VBA's own hardcoded credential is
unaffected by this and keeps sending from `gudkaveer@gmail.com` until the
VBA timer loop is retired. The old sender credential is also kept in `.env`
as `OLD_NOTIFY_EMAIL_FROM`/`OLD_NOTIFY_EMAIL_APP_PASSWORD` for
reference/rollback — unused by `notifications.py`.

The VBA's original credentials are **also still present in plaintext inside
`VBA_Source/Module1.bas` and inside the unlocked `.xlsm`'s embedded VBA
project** — both are gitignored for that reason. Rotating the Gmail app password and the
StockFeeds password should happen once the Python replacement is live and
the VBA timer loop is retired, not before (the live workbook still needs
them to keep sending real alerts in the meantime).

## Known issues in the VBA worth fixing in the port, not carrying forward

- `OldReadLast4Hours` duplicates `ReadLast4Hours`'s total-count logic —
  confirm with the business owner whether it's still needed before deciding
  whether the port keeps an equivalent.
- Threshold/recipient layout (`Emails` sheet, position-coupled rows 1/2/20/21
  and "two rows below, read until blank" for recipients) is fragile and
  undocumented in the sheet itself — the port should use an explicit config
  file/table instead (see `Amazon Padding Automation/config/notifications.json`
  for the pattern already used elsewhere in these automations).
- Credentials hardcoded directly in VBA source (fixed here by moving to
  `.env`, but the live workbook's embedded copy still has them until the
  VBA is retired).
- Whole pipeline silently stops the moment nobody has the workbook open —
  same core problem as `VB Stock update`'s `StockFeeds.xlsm`.

## Network paths: UNC, not the M: drive letter

`STOCKFEEDS_XLSM_PATH`/`LAST_HOUR_FOLDER`/`LAST_4_HOURS_FOLDER` in `.env`
use the UNC form (`\\mbc-nt01\Management\...`), not `M:\...`. `M:` is a
per-session drive mapping (confirmed via `net use`: `M:` →
`\\mbc-nt01\Management`) that may not exist under whatever account actually
runs the scheduled task later — same reasoning already documented in
`VB Stock update`'s CLAUDE.md for its shared-drive log path. Always resolve
the real UNC target with `net use` before hardcoding a path like this
elsewhere; don't assume a drive letter's remote target without checking.

**Caveat specific to agent sandboxes**: a sandboxed tool session may not be
able to open fresh SMB connections at all (confirmed here — even mapping a
*new* drive letter to the exact same share failed with "network name
cannot be found," while the pre-existing `M:` mapping kept working because
it reuses an already-open connection). If a UNC path seems unreachable
while working inside such a sandbox, that's not necessarily a real
permissions problem — verify from a normal terminal/Task Scheduler run on
the actual machine before concluding the path is wrong.

## Data source: ChannelAdvisor Orders API (Phase 3), not the CSV exports

`run_sales_alerts.py` no longer polls `LAST_HOUR_FOLDER`/
`LAST_4_HOURS_FOLDER` for CSVs. It calls `ca_orders_client.
fetch_recent_order_rows(window_minutes)` directly against ChannelAdvisor's
read-only Orders API (`CA_ORDERS_API_URL`, `CA_UK_*` creds), once for the
1h bucket (`window_minutes=60`) and once for the 4h bucket
(`window_minutes=240`), per run.

**Why this is safe to trust as equivalent to the old CSV exports**:
confirmed live this session, not assumed — downloaded the actual newest
`Orders Last 1 Hour*.csv`/`Orders Last 4 Hours*.csv` from ChannelAdvisor's
own Export Center (`.../UploadDownload.mvc/List?apid=12015328`, the same
`CA_UK_PROFILE_ID`) and diffed every `(Site Order ID, SKU)` pair against
the same window pulled straight from the API — **zero mismatches** in
either file. Also confirmed: every row in both exports had
`ShippingStatus = Unshipped`, and order dates ranged well past the nominal
"1 hour"/"4 hours" (an 11:00-generated file held orders from 09:03) — so
these were always "currently-Unshipped order backlog, re-pulled
periodically," not a strict rolling time slice. `fetch_recent_order_rows`
reproduces that directly: `CreatedDateUtc` in the trailing window **and**
`ShippingStatus eq 'Unshipped'`.

**No incremental watermark** — every run re-queries its own full trailing
window from "now," so a late/skipped scheduled run is self-healing as long
as the next run happens before the window rolls past it (the 40-minute
schedule has wide margin under both the 60- and 240-minute windows). A
failed API call (network/429/auth) raises rather than returning a partial
list, flowing through the existing error-alert path unchanged. What this
does *not* cover: a multi-hour ChannelAdvisor/scheduler outage spanning
several runs isn't retroactively backfilled — same limitation the CSV
export had; `activity_log`/`daily_summary` are what catch "did the
scheduler actually run," not this.

`LAST_HOUR_FOLDER`/`LAST_4_HOURS_FOLDER` are kept in `.env` marked legacy,
unused by the pipeline, for rollback reference only. `sales_pipeline.py`'s
`run_1h_check`/`run_4h_check` (CSV-file wrappers) and `load_orders_csv`
are also kept, for the same reason and because existing tests still
exercise them — the pure counting logic lives in `evaluate_1h`/
`evaluate_4h`, which both the CSV wrappers and `ca_orders_client`'s rows
feed into identically.

## Modernization plan — Python replacement (historical — superseded by Phases 1-3 above)

Goal: a scheduled script/service (not something living inside an open
Excel session) that reproduces the same alerting behavior, with an explicit
config format instead of position-coupled spreadsheet cells. Mirrors the
approach already taken in `VB Stock update` and `Amazon Padding Automation`.

Planned pieces:
1. **Config**: `config/thresholds.json` — explicit `{window, scope, threshold,
   recipients}` list, replacing the `Emails` sheet's row-position coupling.
   `config/sku_lookup` — either re-synced from `StockFeeds.xlsm`/its future
   SQLite replacement (`VB Stock update/db/inventory.db`), or read directly
   from that DB once it's the source of truth.
2. **CSV ingestion**: read `Orders Last 1 hour*.csv` / `Orders Last 4
   hours*.csv` from the shared drive on a schedule (Task Scheduler / a long-
   running loop), same per-SKU + total-count checks, same `Exceptions` sheet
   equivalent (a config list) to suppress known SKUs.
3. **Email notifier** (this session's deliverable, see `notifications.py`) —
   HTML + plain-text alert emails via Gmail SMTP, same house style as
   `Amazon Padding Automation/src/amazon_padding_automation/notifications.py`,
   driven by `.env`.
4. **Scheduling**: replace `Application.OnTime` with an OS-level schedule
   (Task Scheduler running the script every 40 min) or a small persistent
   loop — removing the "only works while someone has Excel open" failure
   mode entirely.
5. **Decide fate of `OldReadLast4Hours`** with the business owner before
   porting (see above) rather than silently dropping or silently keeping it.

All of the above is now built (Phases 1-3) — see "Data source" above for
what actually replaced CSV ingestion in the end (the ChannelAdvisor API,
not the CSV drop originally planned here).
