// SPDX-License-Identifier: AGPL-3.0-or-later
// Every read the dashboard makes must have a file behind it in the static demo, or a page would fail only for visitors.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { READS } from "./demoApi";
import { parseNote, PLACEHOLDER } from "./EngineeringNote";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    if (statSync(p).isDirectory()) return n === "demo" ? [] : sources(p);
    return /\.tsx?$/.test(n) && !n.endsWith(".test.ts") ? [p] : [];
  });
}

/** Paths passed to `api(...)` / `api<T>(...)`: string or template literals, with ${...} replaced by a sample id. */
function reads(): string[] {
  const out: string[] = [];
  const re = /\bapi(?:<[^(]*>)?\(\s*(?:`([^`]*)`|"([^"]*)")/g;
  for (const f of sources(join(__dirname, ".."))) {
    const text = readFileSync(f, "utf8");
    for (const m of text.matchAll(re)) {
      const raw = (m[1] ?? m[2]).replace(/\$\{[^}]*\}/g, "x");
      if (raw.startsWith("/")) out.push(raw);
    }
  }
  return out;
}

describe("static demo reads", () => {
  const paths = reads();
  it("finds the call sites", () => expect(paths.length).toBeGreaterThan(15));
  // reads that only the live Workspace page makes (it is replaced by the replay in the demo) or that are mutations written with api()
  const live = [/^\/fs/, /^\/workspace/, /^\/agent\//, /^\/workspaces/];
  for (const p of [...new Set(paths)]) {
    if (live.some((r) => r.test(p))) continue;
    it(`has a file for ${p}`, () => {
      const [route] = p.split("?");
      expect(READS.some(([re]) => re.test(route))).toBe(true);
    });
  }
});

describe("engineering note", () => {
  it("parses headings and flags unwritten sections", () => {
    const blocks = parseNote("# T\n\n## A\n\n[AUTHOR TO WRITE BY HAND]\n\n## B\n\nText.");
    expect(blocks.map((b) => b.kind)).toEqual(["h1", "h2", "p", "h2", "p"]);
    expect(blocks[2].text[0]).toContain(PLACEHOLDER);
  });
});
