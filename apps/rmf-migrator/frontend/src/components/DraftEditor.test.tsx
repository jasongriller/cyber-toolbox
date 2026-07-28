import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { Draft, DraftStatus, Section } from "../api/types";
import DraftEditor from "./DraftEditor";

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

function draft(sectionId: string, status: DraftStatus): Draft {
  return {
    draft_id: `d-${sectionId}`,
    project_id: "proj_1",
    document_id: "doc_1",
    section_id: sectionId,
    order: 1,
    rev4_control_ids: ["AC-1"],
    rev5_control_ids: ["AC-1"],
    dispositions: [],
    draft_text: "proposed rev 5 text",
    suggestions: [],
    edited_text: null,
    status,
    reviewed_by: null,
    reviewed_at: null,
  };
}

function stubClient(drafts: Draft[], documentStatus: string) {
  return {
    listSections: vi.fn().mockResolvedValue({ sections: [SECTION] }),
    getDrafts: vi.fn().mockResolvedValue({ document_status: documentStatus, drafts }),
  } as unknown as ApiClient;
}

describe("DraftEditor", () => {
  it("offers the export step once every section is approved", async () => {
    const onContinue = vi.fn();
    render(
      <DraftEditor
        client={stubClient([draft("s1", "approved")], "review_approved")}
        projectId="proj_1"
        documentId="doc_1"
        onContinue={onContinue}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /continue to export/i }),
    );

    expect(onContinue).toHaveBeenCalled();
  });

  it("withholds the export step while any section is unapproved", async () => {
    render(
      <DraftEditor
        client={stubClient([draft("s1", "proposed")], "drafted")}
        projectId="proj_1"
        documentId="doc_1"
        onContinue={vi.fn()}
      />,
    );

    await screen.findByText(/0\/1 approved/i);
    expect(
      screen.queryByRole("button", { name: /continue to export/i }),
    ).not.toBeInTheDocument();
  });
});
