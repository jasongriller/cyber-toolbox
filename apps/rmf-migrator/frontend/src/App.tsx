// Shell. Starts at the project browser (create a project, upload documents),
// then opens a document into the review pipeline: mapping -> Rev 5 editor ->
// export, plus the project-level coverage dashboard.

import { useState } from "react";
import { ArrowLeft, Moon, Sun, Cube } from "@phosphor-icons/react";
import { ApiClient } from "./api/client";
import { useTheme } from "./useTheme";
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

const STEPS = [
  { kind: "mapping", label: "Mapping review" },
  { kind: "drafting", label: "Rev 5 editor" },
  { kind: "export", label: "Export" },
] as const;

export default function App() {
  const [view, setView] = useState<View>({ kind: "browse" });
  const [theme, toggleTheme] = useTheme();

  const openDocument = (projectId: string, documentId: string) =>
    setView({ kind: "mapping", projectId, documentId });
  const openCoverage = (projectId: string) => setView({ kind: "coverage", projectId });

  const inDocument = view.kind === "mapping" || view.kind === "drafting" || view.kind === "export";

  return (
    <main className="app">
      <header className="topbar">
        <div>
          <div className="brandline">
            <span className="brand-mark" aria-hidden="true">
              <Cube size={18} weight="duotone" />
            </span>
            <div>
              <p className="eyebrow">Binary Systems</p>
              <h1>RMF Rev 5 Migrator</h1>
            </div>
          </div>
          <p className="lede">
            Convert RMF Rev 4 policy documents to Rev 5. Upload your Rev 4 policies, confirm the
            control mapping, refine the drafted Rev 5 language, then export the document and check
            package coverage.
          </p>
        </div>
        <button
          className="btn btn--ghost icon-btn"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </header>

      {view.kind !== "browse" && (
        <button
          className="btn btn--ghost"
          style={{ marginBottom: "1rem" }}
          onClick={() => setView({ kind: "browse" })}
        >
          <ArrowLeft size={15} /> All projects
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
          <nav className="stepper" aria-label="Conversion steps">
            {STEPS.map((step, i) => (
              <button
                key={step.kind}
                className="step"
                aria-current={view.kind === step.kind}
                onClick={() =>
                  setView({ kind: step.kind, projectId: view.projectId, documentId: view.documentId })
                }
                disabled={view.kind === step.kind}
              >
                <span className="step-idx">{i + 1}</span>
                {step.label}
              </button>
            ))}
            <button className="step" onClick={() => openCoverage(view.projectId)}>
              <span className="step-idx">4</span>
              Coverage
            </button>
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
