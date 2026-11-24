/** Pure helpers for the receive wizard: asset tag suggestion and filling serials from a handover file. */

import { fold, words } from "./text";

export interface Unit {
  serial: string;
  asset_tag: string;
  /** asset_tag was suggested by the app (re-suggested when the serial changes). */
  auto?: boolean;
}

export interface HandoverRow {
  serial: string;
  asset_tag: string;
  text: string;
}

/** Code of a price-list group: configured, else the initials of the group name ("Máy in" → "MI"). */
export function typeCode(category: string | undefined, codes: Record<string, string>): string {
  const cat = (category ?? "").trim();
  const hit = Object.entries(codes).find(([k]) => fold(k) === fold(cat));
  if (hit) return hit[1];
  const initials = fold(cat).split(/[^a-z0-9]+/).filter(Boolean).map((w) => w[0]).join("").toUpperCase();
  return initials.slice(0, 3) || "TB";
}

/** <prefix>-<year>-<type>-<last 4 characters of the serial>, e.g. HCM-2026-LT-1234. */
export function suggestTag(prefix: string, year: string, code: string, serial: string): string {
  const tail = serial.trim().replace(/[^A-Za-z0-9]/g, "").slice(-4).toUpperCase();
  return tail ? `${prefix}-${year}-${code}-${tail}` : "";
}

/**
 * Put file rows into empty serial slots. Each slot takes the unused row whose text shares the most
 * words with the slot's device name (file order breaks ties), so a mixed handover lands on the right items.
 */
export function assignSerials(slots: { key: string; name: string; filled: boolean }[], rows: HandoverRow[]): Map<string, HandoverRow> {
  const used = new Set<number>();
  const out = new Map<string, HandoverRow>();
  for (const slot of slots) {
    if (slot.filled) continue;
    const want = words(slot.name);
    let best = -1;
    let score = -1;
    rows.forEach((r, k) => {
      if (used.has(k)) return;
      const have = words(r.text);
      const s = [...want].filter((w) => have.has(w)).length;
      if (s > score) {
        best = k;
        score = s;
      }
    });
    if (best < 0) break;
    used.add(best);
    out.set(slot.key, rows[best]!);
  }
  return out;
}
