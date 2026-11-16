# Design system – infra-ops

Single reference for every module, current and future. Tokens live in
`frontend/src/core/styles/tokens.css`; shared components in `frontend/src/core/ui/`.

## Direction: "Bàn điều phối" (switchboard)

The daily question for IT Ops is *"is this person consistent across every system?"*.
The one memorable element is the **System Rail** (`ui/system-rail.tsx`): a fixed sequence of
system cells (SDP · Tài sản · Entra · AD · M365 · BioStar · Biên bản), always in the same order and
position – reconciliation rows, job headers, dashboard. Everything else stays quiet: no gradients,
no decorative shadows, no card-in-card.

Sources: `ui-ux-pro-max` (style *Data-Dense Dashboard* + *Minimalism & Swiss*, typography
*Vietnamese Friendly*, density 8/10, motion 2/10, bullet charts for KPI vs threshold) combined with
`frontend-design`. Decisions not taken from the skill output are explained in
`docs/phase-0-proposal.md` §8.0.

## Colour

All colours use `light-dark()`; never hard-code hex in components – use Tailwind token classes
(`bg-surface`, `text-muted`, `text-failed`…).

| Token | Light | Dark | Use |
|---|---|---|---|
| `ink` | #1C2430 | #E6EAF0 | Primary text |
| `muted` | #566171 | #9AA5B4 | Secondary text |
| `paper` | #F6F7F9 | #161B22 | App background |
| `surface` | #FFFFFF | #1E242D | Panels, tables |
| `surface-2` | #EEF1F4 | #252C36 | Table header, hover, code |
| `line` / `line-strong` | #DDE2E8 / #C3CAD3 | #2E3642 / #3E4757 | Dividers (decorative) |
| `control-border` | #7C8593 | #707B8C | Form control borders (≥ 3:1) |
| `primary` (petrol) | #0F5F6B | #4FB3BF | Primary buttons, links, active nav |
| `focus` | #0F5F6B | #72C6D0 | Focus ring (2px outline, offset 2) |

Petrol is deliberately not blue: blue is reserved for *In Progress*, so a button never looks like a status.

### Status vocabulary (`ui/status.tsx`)

Always icon + label + colour, never colour alone.

| Status | Label | Icon | Token |
|---|---|---|---|
| done / match | Hoàn tất / Khớp | circle-check | `done` |
| in_progress | Đang chạy | loader (spins; static with reduced motion) | `progress` |
| waiting | Đang chờ | clock | `waiting` |
| failed | Lỗi | circle-x | `failed` |
| skipped / pending | Bỏ qua / Chưa làm | circle-minus / circle-dashed | `skipped` |
| mismatch | Lệch | triangle-alert | `mismatch` |
| warning | Cần chú ý | triangle-alert | `waiting` |
| disabled | Đang tắt | power-off | `skipped` |

Every text/background pair passes WCAG AA (4.5:1) in both themes; control borders and focus ≥ 3:1.
See [Verifying contrast](#verifying-contrast).

## Typography

- **Be Vietnam Pro** (400/500/600) – all UI text. Chosen for clean Vietnamese diacritics at 13px.
  Fallback: Noto Sans, system-ui.
- **JetBrains Mono** – *only* for identifiers people compare or copy: employee ID, UPN, serial,
  asset tag, DN, document number, URLs. Never for labels.
- Scale: 12 (`text-xs`, minimum) · 13 (`text-sm`, table cells/secondary) · 14 (`text-base`, body) ·
  16 (`text-lg`, panel titles) · 20 (`text-xl`, page title) · 28 (`text-2xl`, KPI).
- Sentence case everywhere; no all-caps labels. `tabular` class for numeric columns.

## Layout & density

- Sidebar 240px (collapsed 56px), grouped by module; topbar 48px.
- Spacing base 4px. Table rows 36px (compact, default) / 44px (comfortable).
- Radius by hierarchy: 4px badges/cells, 6px controls/buttons, 8px panels/dialogs.
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
- Visible focus ring everywhere; `scroll-padding-top` keeps focus clear of the sticky topbar.
- Theme: three states (system/light/dark) through `<meta name="color-scheme">`; works without JS.
- `prefers-reduced-motion` disables animation.
- Verified with axe-core on every screen in light and dark (0 violations, Phase 1).

## Verifying contrast

Token pairs are checked by computing WCAG ratios from `tokens.css` (text ≥ 4.5, controls/focus ≥ 3).
When you add or change a token, re-run the check and record the result in the PR.
