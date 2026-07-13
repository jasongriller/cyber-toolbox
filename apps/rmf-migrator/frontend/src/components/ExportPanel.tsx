// Export panel — generate the structure-preserving Rev 5 .docx and download it,
// plus download the per-control decision log (CSV). Export runs async on the
// backend; this triggers it and polls the job to completion.

import { useCallback, useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import type { DocumentStatus } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
}

const POLL_MS = 2000;

export default function ExportPanel({ client, projectId, documentId }: Props) {
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const doc = await client.getDocument(projectId, documentId);
      setStatus(doc.status);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client, projectId, documentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const generate = async () => {
    setBusy(true);
    setError(null);
    try {
      const { job } = await client.startExport(projectId, documentId);
      // Poll the export job until it finishes.
      for (;;) {
        await new Promise((r) => setTimeout(r, POLL_MS));
        const j = await client.getExportJob(projectId, job.job_id);
        if (j.status === "succeeded") break;
        if (j.status === "failed") throw new Error(`export failed (${j.error_type ?? "unknown"})`);
      }
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const downloadDocx = async () => {
    try {
      const { url } = await client.getExportDownload(projectId, documentId);
      window.open(url, "_blank", "noopener");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const downloadCsv = async () => {
    try {
      const csv = await client.getDecisionLogCsv(projectId, documentId);
      const blob = new Blob([csv], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `decision-log-${documentId}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const exported = status === "exported";
  const canExport = status === "drafted" || status === "exported" || status === "exporting";

  return (
    <section>
      <h2>Export</h2>
      <p>
        Status: <strong>{status ?? "loading…"}</strong>
      </p>
      {error && <p style={{ color: "#b00" }}>Error: {error}</p>}

      <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
        <button disabled={busy || !canExport} onClick={() => void generate()}>
          {busy ? "Generating…" : "Generate Rev 5 .docx"}
        </button>
        <button disabled={!exported} onClick={() => void downloadDocx()}>
          Download Rev 5 .docx
        </button>
        <button onClick={() => void downloadCsv()}>Download decision log (CSV)</button>
      </div>

      {!canExport && status !== null && (
        <p style={{ color: "#666", marginTop: "0.5rem" }}>
          Approve the control mapping and drafts before exporting.
        </p>
      )}
    </section>
  );
}
