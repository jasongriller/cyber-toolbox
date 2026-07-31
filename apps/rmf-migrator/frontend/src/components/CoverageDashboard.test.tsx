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

  it("offers every baseline the backend accepts, not just the FIPS tiers", async () => {
    // The API's ?baseline= accepts low/moderate/high plus the four FedRAMP
    // sets; a project created as fedramp_high must be viewable against its
    // own baseline name here.
    const getCoverage = vi.fn().mockResolvedValue(COVERAGE);
    render(
      <CoverageDashboard client={stubClient({ getCoverage })} projectId="proj_1" />,
    );
    await screen.findByText("80%");

    const { fireEvent } = await import("@testing-library/react");
    const select = screen.getByDisplayValue("(project default)");
    for (const name of [
      "low",
      "moderate",
      "high",
      "fedramp_low",
      "fedramp_moderate",
      "fedramp_high",
      "fedramp_li_saas",
    ]) {
      expect(
        Array.from(select.querySelectorAll("option")).map((o) => o.getAttribute("value")),
      ).toContain(name);
    }
    fireEvent.change(select, { target: { value: "fedramp_high" } });
    await vi.waitFor(() =>
      expect(getCoverage).toHaveBeenLastCalledWith("proj_1", "fedramp_high"),
    );
  });

  it("ignores a stale response that lands after a newer request", async () => {
    // Switching baselines fires a second getCoverage while the first may still
    // be in flight; if the first resolves last, its data must not overwrite
    // the newer baseline's numbers.
    let resolveFirst!: (c: Coverage) => void;
    const first = new Promise<Coverage>((resolve) => {
      resolveFirst = resolve;
    });
    const second = { ...COVERAGE, baseline: "high", coverage_pct: 55 };
    const getCoverage = vi
      .fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(second);
    render(
      <CoverageDashboard client={stubClient({ getCoverage })} projectId="proj_1" />,
    );

    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(screen.getByDisplayValue("(project default)"), {
      target: { value: "high" },
    });
    expect(await screen.findByText("55%")).toBeInTheDocument();

    resolveFirst(COVERAGE); // the stale 80% answer arrives late
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText("80%")).not.toBeInTheDocument();
    expect(screen.getByText("55%")).toBeInTheDocument();
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
