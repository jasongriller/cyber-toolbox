// Shell. Starts at the project browser (create a project, upload documents),
// then opens a document into the review pipeline: mapping -> Rev 5 editor ->
// export, plus the project-level coverage dashboard.

import { useState } from "react";
import { ApiClient } from "./api/client";
import ProjectBrowser from "./components/ProjectBrowser";
import MappingReview from "./components/MappingReview";
import DraftEditor from "./components/DraftEditor";
import ExportPanel from "./components/ExportPanel";
import CoverageDashboard from "./components/CoverageDashboard";

const client = new ApiClient();

type View =
  | { kind: "browse" }
  | { kind: "mapping" | "drafting" | "export"; projectId: string; documentId: string }
  | { kind: "coverage"; projectId: string };

export default function App() {
  const [view, setView] = useState<View>({ kind: "browse" });

  const openDocument = (projectId: string, documentId: string) =>
    setView({ kind: "mapping", projectId, documentId });
  const openCoverage = (projectId: string) => setView({ kind: "coverage", projectId });

  const inDocument = view.kind === "mapping" || view.kind === "drafting" || view.kind === "export";

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1040 }}>
      <h1>RMF Rev 5 Migrator</h1>
      <p style={{ color: "#666" }}>
        Convert RMF Rev 4 policy documents to Rev 5. Upload your Rev 4 policies, confirm the
        control mapping, refine the drafted Rev 5 language, then export the document and check
        package coverage.
      </p>

      {view.kind !== "browse" && (
        <button onClick={() => setView({ kind: "browse" })} style={{ margin: "0.5rem 0 1rem" }}>
          ← All projects
        </button>
      )}

      {view.kind === "browse" && (
        <ProjectBrowser
          client={client}
          onOpenDocument={openDocument}
          onOpenCoverage={openCoverage}
        />
      )}

      {inDocument && (
        <>
          <nav style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem", flexWrap: "wrap" }}>
            {(["mapping", "drafting", "export"] as const).map((kind, i) => (
              <button
                key={kind}
                onClick={() =>
                  setView({ kind, projectId: view.projectId, documentId: view.documentId })
                }
                disabled={view.kind === kind}
              >
                {i + 1} ·{" "}
                {kind === "mapping"
                  ? "Mapping review"
                  : kind === "drafting"
                    ? "Rev 5 editor"
                    : "Export"}
              </button>
            ))}
            <button onClick={() => openCoverage(view.projectId)}>4 · Coverage</button>
          </nav>

          {view.kind === "mapping" && (
            <MappingReview
              client={client}
              projectId={view.projectId}
              documentId={view.documentId}
            />
          )}
          {view.kind === "drafting" && (
            <DraftEditor client={client} projectId={view.projectId} documentId={view.documentId} />
          )}
          {view.kind === "export" && (
            <ExportPanel client={client} projectId={view.projectId} documentId={view.documentId} />
          )}
        </>
      )}

      {view.kind === "coverage" && (
        <CoverageDashboard client={client} projectId={view.projectId} />
      )}
    </main>
  );
}
