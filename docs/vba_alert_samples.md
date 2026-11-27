# Sample alerts from the OLD VBA workbook (reference, pasted 2026-11-23)

Subject / recipients / body, as received. No send timestamps were captured.

| # | Subject | Threshold crossed | Total orders |
|---|---|---|---|
| 1 | CA Sales - Orders last 1 hour | > 40 | 44 |
| 2 | CA Sales - Orders last 1 hour | > 40 | 43 |
| 3 | CA Sales - Orders last 4 hours | > 145 | 167 |
| 4 | CA Sales - Orders last 1 hour | > 40 | 42 |
| 5 | CA Sales - Orders last 1 hour | > 45 | 51 |
| 6 | CA Sales - Orders last 4 hours | > 145 | 178 |

Recipients on all: gudkaveer@gmail.com, Veer Gudka, Chamike Senanayake, Ammar Ramees,
Gudz Supply, Anuja Prabashwara, Zainab Ismet, Nimsath Warnakulasuriya, Aflal Hameed.

Body format: `Total orders are greater than <threshold>` / `Total Orders: <n>`.

## Findings (2026-11-23)

- Old "Orders Last 1 Hour" export = orders that ARRIVED in ChannelAdvisor in the hour before
  generation (ImportDateUtc 15:14-15:59 in the 16:00 file), shown with their placed time.
  Not a placed-time slot; Amazon arrives ~55 min after placing.
- Decision: keep order-placed time (CreatedDateUtc) + 60 min settle. Totals will not match
  the VBA's by design; alerts arrive ~70 min after the slot ends.
- Check: placed-time distinct orders per hour on 2026-11-23: 08h 31, 09h 37, 10h 48, 11h 48,
  12h 44, 13h 52, 14h 36, 15h 29. All still Unshipped, so the Unshipped filter loses nothing.
