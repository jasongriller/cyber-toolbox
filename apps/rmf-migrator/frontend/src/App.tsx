// M3 shell. Enter a project/document id, then work either the mapping-review
// checkpoint or the Rev 5 draft editor. Full project navigation lands later.

import { useState } from "react";
import { ApiClient } from "./api/client";
import MappingReview from "./components/MappingReview";
import DraftEditor from "./components/DraftEditor";
import ExportPanel from "./components/ExportPanel";

const client = new ApiClient();

type View = "mapping" | "drafting" | "export";

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [open, setOpen] = useState<{ projectId: string; documentId: string } | null>(null);
  const [view, setView] = useState<View>("mapping");

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1040 }}>
      <h1>RMF Rev 5 Migrator</h1>
      <p style={{ color: "#666" }}>
        Milestone M4 — mapping review, Rev 5 drafting, and export. Enter a project and document
        id, then review the mapping, draft the Rev 5 language, and export the Rev 5 document plus
        the per-control decision log.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (projectId && documentId) setOpen({ projectId, documentId });
        }}
        style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}
      >
        <input
          placeholder="project id"
          value={projectId}
          onChange={(e) => setProjectId(e.target.value)}
          aria-label="project id"
        />
        <input
          placeholder="document id"
          value={documentId}
          onChange={(e) => setDocumentId(e.target.value)}
          aria-label="document id"
        />
        <button type="submit">Open</button>
      </form>

      {open && (
        <>
          <nav style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
            <button
              onClick={() => setView("mapping")}
              disabled={view === "mapping"}
            >
              1 · Mapping review
            </button>
            <button
              onClick={() => setView("drafting")}
              disabled={view === "drafting"}
            >
              2 · Rev 5 editor
            </button>
            <button onClick={() => setView("export")} disabled={view === "export"}>
              3 · Export
            </button>
          </nav>

          {view === "mapping" && (
            <MappingReview
              client={client}
              projectId={open.projectId}
              documentId={open.documentId}
            />
          )}
          {view === "drafting" && (
            <DraftEditor
              client={client}
              projectId={open.projectId}
              documentId={open.documentId}
            />
          )}
          {view === "export" && (
            <ExportPanel
              client={client}
              projectId={open.projectId}
              documentId={open.documentId}
            />
          )}
        </>
      )}
    </main>
  );
}
