import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import type { DocumentRecord, Project } from "../api/types";
import ProjectBrowser from "./ProjectBrowser";

const project: Project = {
  project_id: "proj_1",
  name: "System Alpha",
  baseline: "generic_800_53",
  created_at: "2026-07-30T00:00:00Z",
  created_by: "anonymous",
  document_count: 1,
};

function failedDoc(overrides: Partial<DocumentRecord> = {}): DocumentRecord {
  return {
    document_id: "doc_1",
    project_id: "proj_1",
    filename: "ac-policy.docx",
    s3_key: "projects/proj_1/doc_1/ac-policy.docx",
    status: "failed",
    uploaded_at: "2026-07-30T00:00:00Z",
    uploaded_by: "anonymous",
    section_count: 0,
    parse_error: null,
    failure_stage: null,
    active_job_id: null,
    ...overrides,
  };
}

function makeClient(documents: DocumentRecord[]) {
  return {
    listProjects: vi.fn().mockResolvedValue({ projects: [project] }),
    listDocuments: vi.fn().mockResolvedValue({ documents }),
    startParse: vi.fn().mockResolvedValue({ job: { job_id: "job_doc_1" } }),
  } as unknown as ApiClient;
}

describe("ProjectBrowser failed-document handling", () => {
  it("shows which stage failed and the error type on the status badge", async () => {
    const client = makeClient([
      failedDoc({ failure_stage: "parse", parse_error: "DocxTooLarge" }),
    ]);
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    expect(await screen.findByText(/failed \(parse: DocxTooLarge\)/i)).toBeInTheDocument();
  });

  it("labels a mapping-stage failure without a parse error", async () => {
    const client = makeClient([failedDoc({ failure_stage: "mapping" })]);
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    expect(await screen.findByText(/failed \(mapping\)/i)).toBeInTheDocument();
  });

  it("retries a failed document via the parse endpoint and refreshes the list", async () => {
    const client = makeClient([failedDoc({ failure_stage: "mapping" })]);
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: /retry/i }));

    expect(client.startParse).toHaveBeenCalledWith("proj_1", "doc_1");
    // List refreshes after the retry kicks off (initial load + post-retry).
    expect(client.listDocuments).toHaveBeenCalledTimes(2);
  });

  it("deletes only after the project name is typed into the inline confirm", async () => {
    // In-app confirm (not window.prompt): stylable, focus-managed, testable.
    const client = makeClient([]);
    (client as unknown as Record<string, unknown>).deleteProject = vi
      .fn()
      .mockResolvedValue({ project_id: "proj_1" });
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: /delete project/i }));

    // Revealed confirm: nothing deleted yet, and the confirm button is
    // disabled until the typed name matches exactly.
    const confirmButton = screen.getByRole("button", { name: /permanently delete/i });
    expect(client.deleteProject).not.toHaveBeenCalled();
    expect(confirmButton).toBeDisabled();

    const nameInput = screen.getByLabelText(/type the project name/i);
    await userEvent.type(nameInput, "System Alph");
    expect(confirmButton).toBeDisabled();

    await userEvent.type(nameInput, "a");
    expect(confirmButton).toBeEnabled();
    await userEvent.click(confirmButton);

    expect(client.deleteProject).toHaveBeenCalledWith("proj_1", "System Alpha");
  });

  it("cancel hides the delete confirm without deleting", async () => {
    const client = makeClient([]);
    (client as unknown as Record<string, unknown>).deleteProject = vi.fn();
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    await userEvent.click(await screen.findByRole("button", { name: /delete project/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByLabelText(/type the project name/i)).not.toBeInTheDocument();
    expect(client.deleteProject).not.toHaveBeenCalled();
  });

  it("offers no retry on deterministic wrong-format failures", async () => {
    // Same bytes, same verdict on every run — the remedy is a corrected
    // re-upload, and the reason row already says so.
    const client = makeClient([
      failedDoc({ failure_stage: "parse", parse_error: "UnsupportedDocumentFormat" }),
    ]);
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    await screen.findByText("ac-policy.docx");
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });

  it("offers no retry action on documents that have not failed", async () => {
    const client = makeClient([failedDoc({ status: "mapped", failure_stage: null })]);
    render(
      <ProjectBrowser
        client={client}
        initialProjectId="proj_1"
        onOpenDocument={vi.fn()}
        onOpenCoverage={vi.fn()}
      />,
    );

    await screen.findByText("ac-policy.docx");
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();
  });
});
