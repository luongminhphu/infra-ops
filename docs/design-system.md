# Design system – infra-ops

Single reference for every module, current and future. Tokens live in
`frontend/src/core/styles/tokens.css`; shared components in `frontend/src/core/ui/`.

## Direction since §48: Playbook-first, bright and quiet

The app opens on Infra Playbook ("Bạn cần xử lý việc gì?"); module pages are *tools* opened from playbook steps or
from Công cụ. The page is white (`paper` = `surface`), structure is drawn with hairlines (`line`) rather than boxes,
shadows are near-invisible, and logo-blue `primary` is the only accent – reserved for the main action and links. Status
colours appear only when something needs attention (always with an icon and a label). Lists are rows with
dividers, not grids of same-size cards.

## Previous direction: "Phòng điều khiển" (control room) – Phase 7

Chosen by the product owner from two directions (mockup `docs/design/redesign-mockup.html`, direction B).
Cool slate neutrals, one logo-blue accent (the company logo colour), an **icon rail** of modules and a **sub-navigation** for the pages of
the active module, KPI cards with a soft shadow, pill status badges. The **System Rail**
(`ui/system-rail.tsx`) keeps the fixed system order (SDP · Tài sản · BioStar · Biên bản) wherever a person's
state across systems is shown. Still quiet: no gradients, no card-in-card, colour only for meaning.

## Colour

All colours use `light-dark()`; never hard-code hex in components – use Tailwind token classes
(`bg-surface`, `text-muted`, `text-failed`…).

| Token | Light | Dark | Use |
|---|---|---|---|
| `ink` | #111827 | #E5EAF3 | Primary text |
| `muted` | #5B6472 | #95A1B6 | Secondary text |
| `paper` | #FFFFFF | #0B1120 | App background (same as `surface` in light mode) |
| `surface` | #FFFFFF | #111A2C | Panels, tables, cards |
| `surface-2` | #F4F6F9 | #18233A | Table header, hover, code |
| `rail` | #FAFBFC | #0E1626 | Module rail |
| `line` / `line-strong` | #E4E8EE / #C2CCDA | #233049 / #33425F | Dividers (decorative) |
| `control-border` | #74819A | #6F7D97 | Form control borders (≥ 3:1) |
| `primary` (logo blue) | #074B9B | #79AEF0 | Primary buttons, links, active nav |
| `focus` | #074B9B | #A5C9F5 | Focus ring (2px outline, offset 2) |
| `shadow-card` | slate 7 % | none | Cards and panels (light theme only) |

Status colours (`done` #166534 / #4ADE80, `progress` #1D4ED8 / #93B4FF, `waiting` #92400E / #FBBF24,
`failed` #B91C1C / #F87171, `skipped`, `mismatch`) each have a `-soft` background; *In Progress* is a deeper
blue than the logo-blue primary and always carries its spinner icon and label, so a button never reads as a status.

### Charts

Series colours are `--series-1` (blue) and `--series-2` (orange), categorical slots validated with the dataviz
validator against the light and dark surfaces (CVD ΔE ≥ 24). Never use status tokens for data series. Every chart
has a legend, hover/focus values per period and a "Xem dạng bảng" table view; text keeps text tokens.

## Typography

- **Inter** (variable, Vietnamese subset) – all UI text; tabular figures for numbers.
  Fallback: Noto Sans, system-ui.
- **JetBrains Mono** – *only* for identifiers people compare or copy: employee ID, UPN, serial,
  asset tag, DN, document number, URLs. Never for labels.
- Scale: 12 (`text-xs`, minimum) · 13 (`text-sm`, table cells/secondary) · 14 (`text-base`, body) ·
  16 (`text-lg`, panel titles) · 20 (`text-xl`, page title) · 28 (`text-2xl`, KPI). Rail labels 11px.
- Sentence case everywhere; no all-caps labels. `tabular` class for numeric columns.

## Layout & density

- **Rail** 76px (`core/layout/AppShell.tsx` + `nav.ts`): Playbook (entry), Việc của tôi (with a count badge of the
  next-actions queue), Công cụ, Báo cáo, and *Hệ thống* (platform settings) at the bottom. "/" is named Trang chủ. Opening an entry goes to its first page.
- **Sub-navigation** 220px with the pages of the active module (headed by the module name); below 1024px it
  becomes a horizontally scrolling bar under the topbar. Detail pages highlight their module.
- Topbar 48px: search / command palette (Ctrl K), theme, account. On phones the search shows its icon only.
- Spacing base 4px. Table rows 36px (compact, default) / 44px (comfortable).
- Radius by hierarchy: 4px cells, 6px controls/buttons, 10px panels/cards/dialogs, full for status badges.
- KPI tiles highlight only non-zero problem counts (accent edge + status colour).
- Left aligned; numbers right aligned; content max width 1600px. Optimised for 1366–1920px.

## Components (core)

| Component | File | Notes |
|---|---|---|
| Button | `ui/button.tsx` | primary / secondary / ghost / danger / link; `loading` sets `aria-busy` |
| Field, Input, Select, Textarea, Switch | `ui/field.tsx` | Errors shown and linked only after interaction (`showError`) |
| Dialog | `ui/dialog.tsx` | Native `<dialog>` + `showModal()`; no custom focus trap |
| ConfirmDangerDialog | `ui/confirm-danger-dialog.tsx` | States consequences; retype identifier (employee ID / name) to enable |
| StatusBadge, StatusIcon | `ui/status.tsx` | Unified status vocabulary |
| SystemRail | `ui/system-rail.tsx` | Signature element |
| DataTable | `ui/data-table.tsx` | Sticky header, sortable `<button>` in `<th>`, `aria-sort` on sorted column only |
| Tabs | `ui/tabs.tsx` | Roving tabindex, arrow keys |
| Menu | `ui/menu.tsx` | Esc / outside click / arrow keys |
| Panel, PageHeader, EmptyState, ErrorState, Skeleton, LoadingRows, Mono, Kbd | `ui/feedback.tsx` | |
| LiveRegion / announce() | `ui/live-region.tsx` | One polite live region per app |
| DateInput | `ui/date-input.tsx` | dd/MM/yyyy text entry independent of browser locale; emits ISO dates |

### Reconciliation grid (lifecycle)

- One row per employee; system columns in rail order (SDP · Tài sản · BioStar; Entra/AD/M365 later).
- A system cell with an open issue renders as **Lệch** (`mismatch`: triangle icon, raspberry ring) – never
  colour alone. Unread systems render as *Chưa đọc được* (`skipped`) rather than as discrepancies.
- Severity: Cao (`failed`), Trung bình (`mismatch`), Thấp (`warning`); count + word in the badge.
- Rows sorted by severity; detail opens in a side panel (non-modal) so the list stays usable.

## States

Every data view designs four states: **loading** (skeleton rows, `role=status`), **empty** (icon,
what it means, next action), **error** (`role=alert`, message from the API + "Thử lại"),
**success** (toast via `sonner`, same verb as the button: "Lưu credential" → "Đã lưu credential").

## Writing (Vietnamese UI)

- Buttons say exactly what happens: "Thêm kết nối", "Lưu credential", "Xoá kết nối" (not "OK"/"Submit").
- Errors explain what happened and how to fix it ("Cách khắc phục: …"); never blame the user.
- Dates `dd/MM/yyyy`, time `HH:mm`, time zone Asia/Ho_Chi_Minh (`lib/format.ts`).
- Technical terms kept in English when that is what the team uses: credential, Read/Write, API key, Job.

## Accessibility baseline (WCAG 2.2 AA)

- Skip link, landmarks (`nav`, `main`, `aside`), one `h1` per page, focus moved to `h1` on navigation.
- Visible focus ring everywhere; `scroll-padding-top` keeps focus clear of the sticky topbar and `scroll-padding-bottom` of the phone tab bar.
- Theme: three states (system/light/dark) through `<meta name="color-scheme">`; works without JS.
- `prefers-reduced-motion` disables animation.
- Verified with axe-core on every screen in light and dark (0 violations, re-checked in Phase 7 on 16 pages + login).

## Verifying contrast

Token pairs are checked by computing WCAG ratios from `tokens.css` (text ≥ 4.5, controls/focus ≥ 3).
When you add or change a token, re-run the check and record the result in the PR.
