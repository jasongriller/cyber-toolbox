// The front door: list and create projects, upload policy documents, watch them
// parse and map, then open one to review.
//
// Uploading a .docx does three things in one go (see client.uploadDocument):
// register the document, PUT the bytes straight to S3 via a presigned URL, and
// start parsing. The backend then auto-chains parse -> control mapping, so the
// document lands in "mapped" ready for the human review checkpoint.

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, Trash } from "@phosphor-icons/react";
import { ApiClient } from "../api/client";
import type { Baseline, DocumentRecord, DocumentStatus, Project } from "../api/types";

interface Props {
  client: ApiClient;
  /** Reselect this project once the list loads — the browser remounts on every
   *  return from a document, and forgetting the selection each time made "← All
   *  projects" land on a cold "Select a project" state. */
  initialProjectId?: string;
  onOpenDocument: (projectId: string, documentId: string) => void;
  onOpenCoverage: (projectId: string) => void;
}

const BASELINES: { value: Baseline; label: string }[] = [
  { value: "generic_800_53", label: "Generic 800-53" },
  { value: "fips199_low", label: "FIPS 199 Low" },
  { value: "fips199_moderate", label: "FIPS 199 Moderate" },
  { value: "fips199_high", label: "FIPS 199 High" },
  { value: "fedramp_low", label: "FedRAMP Low" },
  { value: "fedramp_moderate", label: "FedRAMP Moderate" },
  { value: "fedramp_high", label: "FedRAMP High" },
  { value: "fedramp_li_saas", label: "FedRAMP Tailored LI-SaaS" },
  { value: "dod_cnssi_1253", label: "DoD / CNSSI 1253" },
];

// Statuses where the backend is still working and the list should keep refreshing.
const BUSY: DocumentStatus[] = [
  "upload_pending",
  "uploaded",
  "parsing",
  "parsed",
  "mapping",
  "drafting",
  "exporting",
];

const POLL_MS = 2500;

const OLE2_MAGIC = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];

// FileReader rather than Blob.arrayBuffer: identical support in every real
// browser, and it also exists in the jsdom test environment.
function readHeadBytes(file: File): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error("could not read the file"));
    reader.readAsArrayBuffer(file.slice(0, 8));
  });
}

// The same check the backend's parse guard runs, moved to the moment of file
// selection: a renamed legacy .doc opens fine in Word (Word sniffs content,
// not extensions), so the person uploading cannot tell — the first 8 bytes can.
async function sniffWordFormat(file: File): Promise<"docx" | "legacy-doc" | "unknown"> {
  const head = await readHeadBytes(file);
  if (OLE2_MAGIC.every((b, i) => head[i] === b)) return "legacy-doc";
  if (head[0] === 0x50 && head[1] === 0x4b) return "docx";
  return "unknown";
}

export default function ProjectBrowser({
  client,
  initialProjectId,
  onOpenDocument,
  onOpenCoverage,
}: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState<Project | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [name, setName] = useState("");
  const [baseline, setBaseline] = useState<Baseline>("generic_800_53");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadProjects = useCallback(async () => {
    try {
      const { projects } = await client.listProjects();
      setProjects(projects);
      setError(null);
    } catch (e) {
      fail(e);
    }
  }, [client]);

  const loadDocuments = useCallback(
    async (projectId: string) => {
      try {
        const { documents } = await client.listDocuments(projectId);
        setDocuments(documents);
        setError(null);
      } catch (e) {
        fail(e);
      }
    },
    [client],
  );

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  // One-shot: applies once the project list holds the remembered project, and
  // never again after that (or after the operator picks anything themselves) —
  // so a later deselect (e.g. post-purge) stays deselected.
  const appliedInitial = useRef(false);
  useEffect(() => {
    if (appliedInitial.current || selected || !initialProjectId) return;
    const match = projects.find((p) => p.project_id === initialProjectId);
    if (!match) return;
    appliedInitial.current = true;
    setSelected(match);
  }, [projects, selected, initialProjectId]);

  useEffect(() => {
    if (!selected) return;
    void loadDocuments(selected.project_id);
  }, [selected, loadDocuments]);

  // Keep refreshing while anything is still parsing/mapping.
  useEffect(() => {
    if (!selected) return;
    if (!documents.some((d) => BUSY.includes(d.status))) return;
    const id = setInterval(() => void loadDocuments(selected.project_id), POLL_MS);
    return () => clearInterval(id);
  }, [selected, documents, loadDocuments]);

  const createProject = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const project = await client.createProject(name.trim(), baseline);
      setName("");
      await loadProjects();
      setSelected(project);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    if (!selected) return;
    setBusy(true);
    try {
      const format = await sniffWordFormat(file);
      if (format !== "docx") {
        setError(
          format === "legacy-doc"
            ? "This is an older binary .doc file — open it in Word, use Save As to make a real .docx, and upload that instead."
            : "That file is not a .docx Word document.",
        );
        if (fileInput.current) fileInput.current.value = "";
        return;
      }
      await client.uploadDocument(selected.project_id, file);
      await loadDocuments(selected.project_id);
      if (fileInput.current) fileInput.current.value = "";
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const purgeProject = async () => {
    if (!selected) return;
    const confirmation = window.prompt(
      `This permanently deletes every document, export, and audit record in ${selected.name}. ` +
        `Type the project name (${selected.name}) to continue.`,
    );
    const typedName = confirmation?.trim();
    if (typedName !== selected.name) return;

    setBusy(true);
    try {
      await client.deleteProject(selected.project_id, typedName);
      setSelected(null);
      setDocuments([]);
      await loadProjects();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      {error && <p className="banner banner--error">{error}</p>}

      <div className="two-col">
        {/* ---- Projects ---- */}
        <div>
          <div className="section-head">
            <h2>Projects</h2>
          </div>
          <p className="muted" style={{ marginTop: 0 }}>
            A project is one system&apos;s A&amp;A package.
          </p>

          {projects.length === 0 ? (
            <p className="muted">No projects yet. Create one below.</p>
          ) : (
            <ul className="rowlist">
              {projects.map((p) => (
                <li key={p.project_id}>
                  <button
                    className="row-select"
                    aria-current={selected?.project_id === p.project_id}
                    onClick={() => setSelected(p)}
                  >
                    <span className="row-title">{p.name}</span>
                    <span className="mono muted">
                      {p.baseline} · {p.document_count} doc{p.document_count === 1 ? "" : "s"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={createProject} className="toolbar" style={{ marginTop: "1rem" }}>
            <input
              className="field"
              style={{ flex: "1 1 160px" }}
              placeholder="New project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="new project name"
            />
            <select
              className="field"
              style={{ flex: "0 1 auto", width: "auto" }}
              value={baseline}
              onChange={(e) => setBaseline(e.target.value as Baseline)}
              aria-label="baseline"
            >
              {BASELINES.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
            <button className="btn btn--accent" type="submit" disabled={busy || !name.trim()}>
              Create
            </button>
          </form>
        </div>

        {/* ---- Documents ---- */}
        <div>
          <div className="section-head">
            <h2>Documents</h2>
            {selected && <span className="mono muted">{selected.name}</span>}
          </div>

          {!selected ? (
            <p className="muted">Select a project to see its policy documents.</p>
          ) : (
            <div className="stack">
              {documents.length === 0 ? (
                <p className="muted">
                  No documents yet. Upload the Rev 4 policy documents for this system.
                </p>
              ) : (
                <div className="table-wrap">
                  <table className="data">
                    <thead>
                      <tr>
                        <th>Document</th>
                        <th>Status</th>
                        <th>Sections</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {documents.map((d) => (
                        <tr key={d.document_id}>
                          <td>
                            {d.filename}
                            {d.status === "failed" && (
                              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
                                {failureReason(d)}
                              </p>
                            )}
                          </td>
                          <td>
                            <StatusBadge status={d.status} />
                          </td>
                          <td className="num">{d.section_count > 0 ? d.section_count : ""}</td>
                          <td>
                            <button
                              className="btn btn--sm"
                              onClick={() => onOpenDocument(selected.project_id, d.document_id)}
                              disabled={BUSY.includes(d.status) && d.status !== "parsed"}
                            >
                              Open
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="file-drop">
                <input
                  ref={fileInput}
                  type="file"
                  accept=".docx"
                  disabled={busy}
                  aria-label="upload a .docx policy document"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void upload(file);
                  }}
                />
                {busy && (
                  <span className="loading" style={{ marginLeft: "0.5rem" }}>
                    <span className="spinner" /> uploading…
                  </span>
                )}
                <p className="muted" style={{ margin: "0.5rem 0 0" }}>
                  .docx only. Upload starts parsing and control mapping automatically.
                </p>
              </div>

              <div className="toolbar">
                <button className="btn" onClick={() => onOpenCoverage(selected.project_id)}>
                  Package coverage &amp; gaps <ArrowRight size={14} />
                </button>
                <button
                  className="btn btn--danger"
                  disabled={busy}
                  onClick={() => void purgeProject()}
                >
                  <Trash size={14} /> Delete project
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

// The backend records failures as an error type only (never content); this is
// where those types become words an operator can act on.
function failureReason(d: DocumentRecord): string {
  switch (d.parse_error) {
    case "UnsupportedDocumentFormat":
      return "This is an older binary .doc file — open it in Word, use Save As to make a real .docx, and re-upload.";
    case "DocxTooLarge":
      return "Too large or too complex to parse safely (25 MB limit).";
    case "ParsedDocumentTooLarge":
      return "The parsed text exceeds the size limit.";
    default:
      return d.parse_error
        ? `Processing failed (${d.parse_error}). Fix the file and re-upload.`
        : "Processing failed. Re-upload the document to retry.";
  }
}

function StatusBadge({ status }: { status: DocumentStatus }) {
  const working = BUSY.includes(status) && status !== "parsed";
  const failed = status === "failed";
  const cls = failed ? "pill pill--crit" : working ? "pill pill--work" : "pill pill--ok";
  return <span className={cls}>{status}</span>;
}
