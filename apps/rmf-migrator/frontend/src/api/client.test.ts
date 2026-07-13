import { describe, expect, it } from "vitest";
import { joinUrl } from "./client";

describe("joinUrl", () => {
  it("joins without doubling slashes", () => {
    expect(joinUrl("https://api.example/", "/projects")).toBe("https://api.example/projects");
  });

  it("joins when neither side has a slash", () => {
    expect(joinUrl("https://api.example", "projects")).toBe("https://api.example/projects");
  });

  it("handles a sub-path base", () => {
    expect(joinUrl("/api", "/projects/p1/jobs/j1")).toBe("/api/projects/p1/jobs/j1");
  });
});
