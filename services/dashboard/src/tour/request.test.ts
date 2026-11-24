// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from "vitest";
import { tourRequested } from "./Tour";

describe("a link that asks for the tour", () => {
  it("accepts ?tour and ?tour=1, also next to other parameters", () => {
    expect(tourRequested("?tour")).toBe(true);
    expect(tourRequested("?tour=1")).toBe(true);
    expect(tourRequested("?a=1&tour=1")).toBe(true);
  });
  it("does not start it for nothing, for tour=0, or for a similar name", () => {
    expect(tourRequested("")).toBe(false);
    expect(tourRequested("?tour=0")).toBe(false);
    expect(tourRequested("?tours=1")).toBe(false);
  });
});
