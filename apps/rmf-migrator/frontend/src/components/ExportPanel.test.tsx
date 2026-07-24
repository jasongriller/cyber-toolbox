import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
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
});
