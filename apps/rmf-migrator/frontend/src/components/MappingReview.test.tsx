import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { ControlMapping, Section } from "../api/types";
import MappingReview from "./MappingReview";

const SECTION: Section = {
  section_id: "s1",
  document_id: "doc_1",
  project_id: "proj_1",
  order: 1,
  level: 1,
  heading: "Access Control Policy",
  parent_id: null,
  text: "original text",
  char_length: 13,
};

const MAPPING: ControlMapping = {
  mapping_id: "m1",
  project_id: "proj_1",
  document_id: "doc_1",
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

function stubClient(overrides: Record<string, unknown> = {}) {
  return {
    listSections: vi.fn().mockResolvedValue({ sections: [SECTION] }),
    getMappings: vi
      .fn()
      .mockResolvedValue({ document_status: "mapped", mappings: [MAPPING] }),
    approveMappings: vi
      .fn()
      .mockResolvedValue({ document_status: "mapping_approved", approved_count: 1 }),
    ...overrides,
  } as unknown as ApiClient;
}

describe("MappingReview", () => {
  it("continues to drafting once the mapping approval lands", async () => {
    const onContinue = vi.fn();
    render(
      <MappingReview
        client={stubClient()}
        projectId="proj_1"
        documentId="doc_1"
        onContinue={onContinue}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /approve mapping & continue/i }),
    );

    expect(onContinue).toHaveBeenCalled();
  });

  it("stays put and shows the error when approval fails", async () => {
    const onContinue = vi.fn();
    const client = stubClient({
      approveMappings: vi.fn().mockRejectedValue(new Error("approval failed")),
    });
    render(
      <MappingReview
        client={client}
        projectId="proj_1"
        documentId="doc_1"
        onContinue={onContinue}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /approve mapping & continue/i }),
    );

    // role="alert" so assistive tech announces the failure — matching the
    // convention Login.tsx establishes for every error banner.
    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/approval failed/i);
    expect(onContinue).not.toHaveBeenCalled();
  });
});
