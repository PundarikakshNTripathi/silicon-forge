// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from "vitest";
import { DEMO_TOUR, FIRST_RUN, HELP, PAGE_TOURS, helpKey } from "./steps";

describe("tour content", () => {
  it("starts by asking, ends on a card without a target, and every step says something", () => {
    expect(DEMO_TOUR[0].welcome).toBe(true);
    expect(DEMO_TOUR.at(-1)?.target).toBeUndefined();
    for (const s of [...DEMO_TOUR, ...Object.values(PAGE_TOURS).flat()]) {
      expect(s.title.length).toBeGreaterThan(2);
      expect(s.body.length).toBeGreaterThan(20);
    }
  });
  it("only sends the full tour to pages that exist", () => {
    const known = /^\/($|runs$|runs\/|workspace$|evals|cost$|note$|models$|knowledge$|system$|new$)/;
    for (const s of DEMO_TOUR) if (s.route) expect(s.route).toMatch(known);
    expect(DEMO_TOUR.some((s) => s.route === `/runs/${FIRST_RUN}`)).toBe(true);
  });
  it("has help text for every page that has a short tour", () => {
    for (const k of Object.keys(PAGE_TOURS)) expect(HELP[k]).toBeDefined();
  });
  it("maps routes to help entries", () => {
    expect(helpKey("/")).toBeNull();
    expect(helpKey("/runs")).toBe("runs");
    expect(helpKey("/runs/abc")).toBe("run");
    expect(helpKey("/evals")).toBe("evals");
    expect(helpKey("/nonsense")).toBeNull();
  });
  it("keeps plain punctuation: no em or en dashes in what visitors read", () => {
    const all = JSON.stringify([DEMO_TOUR, PAGE_TOURS, HELP]);
    expect(all).not.toMatch(/[–—]/);
  });
});
