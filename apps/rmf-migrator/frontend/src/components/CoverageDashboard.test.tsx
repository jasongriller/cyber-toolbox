import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { Coverage } from "../api/types";
import CoverageDashboard from "./CoverageDashboard";

const COVERAGE: Coverage = {
  baseline: "moderate",
  covered_count: 80,
  covered_controls: ["AC-2"],
  baseline_total: 100,
  baseline_covered: 80,
  coverage_pct: 80,
  baseline_gaps: ["AC-9"],
  new_in_rev5_gaps: ["SR-3"],
};

function stubClient(overrides: Record<string, unknown> = {}) {
  return {
    getCoverage: vi.fn().mockResolvedValue(COVERAGE),
    ...overrides,
  } as unknown as ApiClient;
}

describe("CoverageDashboard", () => {
  it("shows the coverage metric and both gap lists", async () => {
    render(<CoverageDashboard client={stubClient()} projectId="proj_1" />);

    expect(await screen.findByText("80%")).toBeInTheDocument();
    expect(screen.getByText("AC-9")).toBeInTheDocument();
    expect(screen.getByText("SR-3")).toBeInTheDocument();
  });

  it("announces a coverage load failure as an alert", async () => {
    const client = stubClient({
      getCoverage: vi.fn().mockRejectedValue(new Error("coverage failed")),
    });
    render(<CoverageDashboard client={client} projectId="proj_1" />);

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/coverage failed/i);
  });
});
