// Export panel — generate the structure-preserving Rev 5 .docx and download it,
// plus download the per-control decision log (CSV). Export runs async on the
// backend; this triggers it and polls the job to completion.

import { useCallback, useEffect, useState } from "react";
import { DownloadSimple, FileDoc, FileCsv } from "@phosphor-icons/react";
import { ApiClient } from "../api/client";
import { waitForExportJob } from "../api/polling";
import type { DocumentStatus } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
}

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
      await waitForExportJob(client, projectId, job.job_id);
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
  const canExport =
    status === "review_approved" || status === "exported" || status === "exporting";

  return (
    <section>
      <div className="section-head">
        <h2>Export</h2>
        <span className={exported ? "pill pill--ok" : "pill"}>{status ?? "loading…"}</span>
      </div>
      {error && <p className="banner banner--error">{error}</p>}

      <div className="toolbar">
        <button className="btn btn--accent" disabled={busy || !canExport} onClick={() => void generate()}>
          {busy ? (
            <>
              <span className="spinner" /> Generating…
            </>
          ) : (
            <>
              <FileDoc size={15} /> Generate Rev 5 .docx
            </>
          )}
        </button>
        <button className="btn" disabled={!exported} onClick={() => void downloadDocx()}>
          <DownloadSimple size={15} /> Download Rev 5 .docx
        </button>
        <button className="btn" onClick={() => void downloadCsv()}>
          <FileCsv size={15} /> Download decision log (CSV)
        </button>
      </div>

      {!canExport && status !== null && (
        <p className="banner banner--info" style={{ marginTop: "1rem", marginBottom: 0 }}>
          Approve the control mapping and every draft before exporting.
        </p>
      )}
    </section>
  );
}
