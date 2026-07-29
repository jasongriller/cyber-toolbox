import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import ExportPanel from "./ExportPanel";

describe("ExportPanel", () => {
  it("renders export actions disabled until document status loads", () => {
    const html = renderToStaticMarkup(
      <ExportPanel
        client={{} as ApiClient}
        projectId="proj_1"
        documentId="doc_1"
      />,
    );

    expect(html).toContain("Generate Rev 5 .docx");
    expect(html).toContain("loading…");
    // Before the document status loads, the generate action is disabled.
    expect(html).toMatch(/<button[^>]*\bdisabled=""[^>]*>(?:(?!<\/button>).)*Generate Rev 5 \.docx/);
  });

  it("returns to projects once the export is done", async () => {
    const client = {
      getDocument: vi.fn().mockResolvedValue({ status: "exported" }),
    } as unknown as ApiClient;
    const onDone = vi.fn();
    render(
      <ExportPanel client={client} projectId="proj_1" documentId="doc_1" onDone={onDone} />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: /back to projects/i }),
    );

    expect(onDone).toHaveBeenCalled();
  });

  it("offers no done shortcut before an export exists", async () => {
    const client = {
      getDocument: vi.fn().mockResolvedValue({ status: "review_approved" }),
    } as unknown as ApiClient;
    render(
      <ExportPanel client={client} projectId="proj_1" documentId="doc_1" onDone={vi.fn()} />,
    );

    await screen.findByText("review_approved");
    expect(
      screen.queryByRole("button", { name: /back to projects/i }),
    ).not.toBeInTheDocument();
  });
});
