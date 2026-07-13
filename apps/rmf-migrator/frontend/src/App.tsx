// v1 shell. Enter a project id (and a document id for the per-document steps),
// then work the pipeline: mapping review, Rev 5 editor, export, and the
// project-level coverage dashboard. Full project navigation lands later.

import { useState } from "react";
import { ApiClient } from "./api/client";
import MappingReview from "./components/MappingReview";
import DraftEditor from "./components/DraftEditor";
import ExportPanel from "./components/ExportPanel";
import CoverageDashboard from "./components/CoverageDashboard";

const client = new ApiClient();

type View = "mapping" | "drafting" | "export" | "dashboard";

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [open, setOpen] = useState<{ projectId: string; documentId: string } | null>(null);
  const [view, setView] = useState<View>("mapping");

  const needsDocument = view !== "dashboard";

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1040 }}>
      <h1>RMF Rev 5 Migrator</h1>
      <p style={{ color: "#666" }}>
        Convert RMF Rev 4 policy documents to Rev 5. Enter a project id (and a document id for the
        per-document steps), then review the control mapping, draft the Rev 5 language, export the
        Rev 5 document, and check package coverage.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (projectId) setOpen({ projectId, documentId });
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
          placeholder="document id (steps 1-3)"
          value={documentId}
          onChange={(e) => setDocumentId(e.target.value)}
          aria-label="document id"
        />
        <button type="submit" disabled={!projectId}>
          Open
        </button>
      </form>

      {open && (
        <>
          <nav style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
            <button onClick={() => setView("mapping")} disabled={view === "mapping"}>
              1 · Mapping review
            </button>
            <button onClick={() => setView("drafting")} disabled={view === "drafting"}>
              2 · Rev 5 editor
            </button>
            <button onClick={() => setView("export")} disabled={view === "export"}>
              3 · Export
            </button>
            <button onClick={() => setView("dashboard")} disabled={view === "dashboard"}>
              4 · Coverage
            </button>
          </nav>

          {needsDocument && !open.documentId && (
            <p style={{ color: "#a15" }}>This step needs a document id — enter one above and reopen.</p>
          )}

          {view === "mapping" && open.documentId && (
            <MappingReview client={client} projectId={open.projectId} documentId={open.documentId} />
          )}
          {view === "drafting" && open.documentId && (
            <DraftEditor client={client} projectId={open.projectId} documentId={open.documentId} />
          )}
          {view === "export" && open.documentId && (
            <ExportPanel client={client} projectId={open.projectId} documentId={open.documentId} />
          )}
          {view === "dashboard" && (
            <CoverageDashboard client={client} projectId={open.projectId} />
          )}
        </>
      )}
    </main>
  );
}
