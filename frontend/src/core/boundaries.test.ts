import { describe, expect, it } from "vitest";

// Frontend counterpart of the backend import-linter contracts (docs/architecture.md):
// core never imports modules (only src/app wires them in), modules never import each other
// or core pages (shared pieces live in core/lib and core/ui).
const sources = import.meta.glob("/src/**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const IMPORT = /\bfrom\s+["']([^"']+)["']|\bimport\s*\(?\s*["']([^"']+)["']/g;

function violations(): string[] {
  const out: string[] = [];
  for (const [file, src] of Object.entries(sources)) {
    const own = file.match(/^\/src\/modules\/([^/]+)\//)?.[1];
    for (const m of src.matchAll(IMPORT)) {
      const spec = m[1] ?? m[2]!;
      if (file.startsWith("/src/core/") && /^@\/modules(\/|$)/.test(spec)) out.push(`${file} → ${spec} (core imports modules)`);
      if (own && spec.startsWith("@/core/pages")) out.push(`${file} → ${spec} (module imports a core page)`);
      const other = spec.match(/^@\/modules\/([^/]+)/)?.[1];
      if (own && other && other !== own) out.push(`${file} → ${spec} (module imports another module)`);
    }
  }
  return out;
}

describe("import boundaries", () => {
  it("scans the sources", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(50);
  });

  it("keeps core, modules and core pages apart", () => {
    expect(violations()).toEqual([]);
  });
});
