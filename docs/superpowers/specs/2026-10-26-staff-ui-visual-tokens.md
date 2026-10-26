# Warm staff UI — measured reference tokens

Extracted from `C:\Users\Lily Ann\Downloads\New Ui Booking\*.png`
(1440×970 desktop, 1360px content column). Use these values when a
spacing or colour choice is not otherwise fixed.

## Page geometry

- Page background `#F1E8D8`; content max-width 1360.
- Title 28/700; subtitle 13/400, inline 16px after the title.
- Tools card: radius 18, white, 1px `#DFD4C5`, padding 16.
- Cream selection bar: radius 14, fill `#EADBBB`, height 60.
- Table card: radius 18, white, 1px `#DFD4C5`; 16px padding.
- Gaps: tools→bar 16, bar→table 15.

## Table

- Header bar inset 16px, height 36, radius 10, fill `#EEEAE1`, text 12/600 `#6F685E`.
- Rows 54px pitch, full-bleed, 1px `#F0EBE2` separators, no zebra.
- Selected row fill `#F6EEDC`, full-bleed, no outline or accent.
- Two-line vehicle: 13/600 ink + 11/400 `#9F968C`, 2px gap.
- Owed positive: 13/700 `#9B2C1F`; zero: em dash `#B0A99F`.
- Footer 64 high, top border `#F3EFE8`, 12/400 `#6A645C`.
- Column order: Ref, Customer, Vehicle, Dates, Status, Payment,
  Total due, Paid, Owed, Channel, Booked.
- Ref/date/plate greys: `#4C453B`, `#6E6E6E`, `#9F968C`.

## Toolbar

- Search 40 high, radius 12, 1px `#E0D6C9`, 14/400, 18px inset.
- Dropdowns 40 high, radius 12, 13px.
- `Overdue only` 32 high, radius 16, outline.
- Filter chips 30 high, radius 15, 13/500, 12px gaps. Selected dark
  `#171717`/white; unselected white/`#E0D6C9`.

## Colour tokens

| Token | Hex |
|---|---|
| Page background | `#F1E8D8` |
| Card | `#FFFFFF` |
| Card border | `#DFD4C5` |
| Control border | `#E0D6C9` |
| Table header | `#EEEAE1` |
| Selected row | `#F6EEDC` |
| Row separator | `#F0EBE2` |
| Selection bar | `#EADBBB` |
| Neutral tile / eyebrow | `#ECE6DD` |
| Owed tile / amber notice | `#F7E3C3` |
| Cream notice / summary | `#F1E7D6` |
| Red notice | `#F8E0DC` |
| Green notice | `#DCEFE0` |
| Ink | `#171717` |
| Muted | `#7D7265` |
| Subtitle | `#8B7F70` |
| Destructive fill | `#A3271D` |
| Destructive text | `#9B2C1F` |
| Destructive outline | `#C07B72` |

Status chips: Confirmed `#DDF0E0`/`#2C6A3C`; Pending
`#F7E4C4`/`#744A15`; Paid in full `#E6E1D6`/`#5F574C`; Unpaid
`#F6DDD8`/`#9B2C1F`; Partial `#F7E4C4`/`#744A15`.

## Type

- Page title 28/700; table header 12/600; body 13; customer/vehicle 600.
- Modal eyebrow 11/600 uppercase; title 24/700; subtitle 13/400.
- Section labels 10/600 uppercase.
- Form label 12/600; input 14/400; field values 14/600.
- Metric label 11/400; metric value 18/700 (large 20/700).
- Summary label 13/400; value 13/700; notice 12/400.
- Footer buttons 13/600; amount field 26/700.

## Components

- Status/payment chip: height 20, radius 10, padding 0 10, flat.
- Filter chip: height 30, radius 15, padding 0 14.
- Modal chip: height 32, radius 16, padding 0 16; fuel 34/radius 17;
  reason 48/radius 24.
- Selection bar: 60 high; right buttons 36 high/radius 18; Record
  payment dark, Cancel booking white with `#C07B72` border and red text.
- Metric tile: 158×52, radius 10, fill `#ECE6DD`; owed tile
  `#F7E3C3`; checkout tile 239×68, radius 12, fill `#F1E7D6`.
- Summary box: radius 10, fill `#F1E7D6`; dividers inset 20px.
- Amount field: 50 high, radius 10, 26/700, inline `₱`.
- Notices: cream 44/radius 10; red and green 36/radius 8; amber
  34/radius 8.
- Footer buttons: 36 high, radius 18, padding 0 18, gap 10, right inset
  24.
- Modal: width 540, radius 20, padding 24; eyebrow 18 high; header
  divider `#F5F1EA`; footer 68 high with divider; body 16/18 padding.

## Per-modal layout

- Details: status chip; 3 metric tiles; Customer/Vehicle two 240px
  columns; field rows ~31 high with `#EDE6DA` dividers; cream notice 44;
  Print receipt + Close.
- Cancel: 5 reason chips 48; optional details field 40; 2-row summary
  88; red notice 36; Keep booking + Cancel booking.
- Check in: odometer 40; fuel row 5×34; damage chips 32; notes 40;
  Back + Check in.
- Check out: 2 large tiles 68; return odometer with floor suffix; fuel
  row; two half-width fields; green notice 36; Back + Check out.
- Extend: two date fields 40; 4 quick chips 32; amber notice 34;
  3-row summary 120; Back + Extend to <date>.
- Record payment: 3 metric tiles; 3 payment-for chips 32; amount 50;
  3 quick chips 32; method row 3×32; two half-width fields 38;
  Back + Record ₱X.

## Easy-to-get-wrong

- Header bar is inset and rounded; rows and selection fill are
  full-bleed.
- Numeric columns share right edges with their headers.
- Three creams are distinct: bar, tile, notice/summary.
- Chip heights differ by role.
- Only the amount field is 50/26; other modal inputs are 40/14.
- Modal footer is fixed 68 with a full-width divider.
- Summary dividers stop 20px inside the box.
- Metric tiles are 158 wide with 9px gaps.
- Destructive red is brick `#A3271D`; text red is `#9B2C1F`.
