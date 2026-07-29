import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

// App constructs its own module-scope ApiClient, so the class itself is mocked.
// Both `./api/client` and the components' `../api/client` resolve to this.
const stubs = vi.hoisted(() => ({
  listProjects: vi.fn(),
  listDocuments: vi.fn(),
  listSections: vi.fn(),
  getMappings: vi.fn(),
  getCoverage: vi.fn(),
}));

vi.mock("./api/client", () => ({
  ApiClient: class {
    listProjects = stubs.listProjects;
    listDocuments = stubs.listDocuments;
    listSections = stubs.listSections;
    getMappings = stubs.getMappings;
    getCoverage = stubs.getCoverage;
  },
  parseControlIds: (raw: string) =>
    raw.split(",").map((s) => s.trim()).filter(Boolean),
}));

const PROJECT = {
  project_id: "p1",
  name: "Alpha",
  baseline: "generic_800_53",
  created_at: "",
  created_by: "",
  document_count: 1,
};

const DOCUMENT = {
  document_id: "doc1",
  project_id: "p1",
  filename: "policy.docx",
  s3_key: "",
  status: "mapped",
  uploaded_at: "",
  uploaded_by: "",
  section_count: 1,
  parse_error: null,
};

const SECTION = {
  section_id: "s1",
  document_id: "doc1",
  project_id: "p1",
  order: 1,
  level: 1,
  heading: "Access Control Policy",
  parent_id: null,
  text: "original text",
  char_length: 13,
};

const MAPPING = {
  mapping_id: "m1",
  project_id: "p1",
  document_id: "doc1",
  section_id: "s1",
  order: 1,
  proposed_control_ids: ["AC-1"],
  confidence: 0.9,
  rationale: "",
  final_control_ids: null,
  status: "proposed",
  reviewed_by: null,
  reviewed_at: null,
};

const COVERAGE = {
  baseline: "moderate",
  covered_count: 1,
  covered_controls: ["AC-1"],
  baseline_total: 10,
  baseline_covered: 1,
  coverage_pct: 10,
  baseline_gaps: [],
  new_in_rev5_gaps: [],
};

async function openTheDocument() {
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /alpha/i }));
  await userEvent.click(await screen.findByRole("button", { name: /^open$/i }));
  await screen.findByRole("navigation", { name: /conversion steps/i });
}

beforeEach(() => {
  localStorage.clear();
  // jsdom has no matchMedia; useTheme reads `.matches` from it once on mount.
  window.matchMedia = vi
    .fn()
    .mockReturnValue({ matches: false } as unknown as MediaQueryList);
  stubs.listProjects.mockResolvedValue({ projects: [PROJECT] });
  stubs.listDocuments.mockResolvedValue({ documents: [DOCUMENT] });
  stubs.listSections.mockResolvedValue({ sections: [SECTION] });
  stubs.getMappings.mockResolvedValue({ document_status: "mapped", mappings: [MAPPING] });
  stubs.getCoverage.mockResolvedValue(COVERAGE);
});

describe("App coverage navigation", () => {
  it("keeps the document stepper when coverage is opened from it", async () => {
    await openTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /coverage/i }));

    // The stepper survives the hop, with Coverage marked current …
    const nav = screen.getByRole("navigation", { name: /conversion steps/i });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /coverage/i })).toBeDisabled();

    // … and step 1 leads straight back to the document being worked on.
    await userEvent.click(screen.getByRole("button", { name: /mapping review/i }));
    expect(
      await screen.findByRole("heading", { name: /control mapping review/i }),
    ).toBeInTheDocument();
  });

  it("shows no stepper when coverage is opened from the project browser", async () => {
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /alpha/i }));

    await userEvent.click(
      await screen.findByRole("button", { name: /package coverage & gaps/i }),
    );

    expect(
      screen.queryByRole("navigation", { name: /conversion steps/i }),
    ).not.toBeInTheDocument();
  });
});

describe("App project memory", () => {
  it("returns to the browser with the project still selected", async () => {
    await openTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /all projects/i }));

    // Not the cold "Select a project…" state: the project's documents render
    // and its row is marked as the current selection.
    expect(await screen.findByText("policy.docx")).toBeInTheDocument();
    expect(screen.queryByText(/select a project to see/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /alpha/i })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });
});
