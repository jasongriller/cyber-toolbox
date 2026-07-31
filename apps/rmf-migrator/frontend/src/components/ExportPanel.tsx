// Export panel — generate the structure-preserving Rev 5 .docx and download it,
// plus download the per-control decision log (CSV). Export runs async on the
// backend; this triggers it and polls the job to completion.

import { useCallback, useEffect, useRef, useState } from "react";
import { DownloadSimple, FileDoc, FileCsv } from "@phosphor-icons/react";
import { ApiClient } from "../api/client";
import { waitForExportJob } from "../api/polling";
import type { DocumentStatus } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
  /** The closing move once the export exists — back to the project browser. */
  onDone?: () => void;
}

export default function ExportPanel({ client, projectId, documentId, onDone }: Props) {
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Monotonic id per request: only the latest response lands, so nothing
  // writes state after unmount or a document switch.
  const refreshId = useRef(0);

  const refresh = useCallback(async () => {
    const id = ++refreshId.current;
    try {
      const doc = await client.getDocument(projectId, documentId);
      if (id !== refreshId.current) return;
      setStatus(doc.status);
      setFilename(doc.filename);
      setError(null);
    } catch (e) {
      if (id === refreshId.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [client, projectId, documentId]);

  useEffect(() => {
    void refresh();
    return () => {
      refreshId.current += 1; // invalidate in-flight work on dep change/unmount
    };
  }, [refresh]);

  // The export poll runs up to 5 minutes; abort it when the panel unmounts so
  // it stops hitting the API from a screen nobody is looking at.
  const pollAbort = useRef<AbortController | null>(null);
  useEffect(() => {
    return () => pollAbort.current?.abort();
  }, []);

  const generate = async () => {
    setBusy(true);
    setError(null);
    const controller = new AbortController();
    pollAbort.current = controller;
    try {
      const { job } = await client.startExport(projectId, documentId);
      await waitForExportJob(client, projectId, job.job_id, { signal: controller.signal });
      await refresh();
    } catch (e) {
      if (controller.signal.aborted) return; // unmounted; nobody to tell
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (!controller.signal.aborted) setBusy(false);
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
      // Name the download after the source document, matching the docx
      // export's convention; the id is only a fallback for a not-yet-loaded
      // record.
      const stem = (filename ?? documentId).replace(/\.[^.]+$/, "");
      a.download = `decision-log-${stem}.csv`;
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
      {error && <p role="alert" className="banner banner--error">{error}</p>}

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

      {exported && onDone && (
        <button className="btn" style={{ marginTop: "1rem" }} onClick={onDone}>
          Done — back to projects
        </button>
      )}
    </section>
  );
}
