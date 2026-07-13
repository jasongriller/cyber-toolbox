// M2 shell. A minimal harness to open a document's mapping-review view. Project
// creation, upload, and navigation get a proper UI in later milestones; for now
// this lets the reviewer paste a project/document id and work the checkpoint.

import { useState } from "react";
import { ApiClient } from "./api/client";
import MappingReview from "./components/MappingReview";

const client = new ApiClient();

export default function App() {
  const [projectId, setProjectId] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [open, setOpen] = useState<{ projectId: string; documentId: string } | null>(null);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 960 }}>
      <h1>RMF Rev 5 Migrator</h1>
      <p style={{ color: "#666" }}>
        Milestone M2 — control mapping review. Enter a project and document id to review the
        proposed Rev 4 control mapping before Rev 5 drafting.
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
        <MappingReview
          client={client}
          projectId={open.projectId}
          documentId={open.documentId}
        />
      )}
    </main>
  );
}
