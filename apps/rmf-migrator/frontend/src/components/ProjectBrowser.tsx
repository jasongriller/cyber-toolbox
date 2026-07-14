// The front door: list and create projects, upload policy documents, watch them
// parse and map, then open one to review.
//
// Uploading a .docx does three things in one go (see client.uploadDocument):
// register the document, PUT the bytes straight to S3 via a presigned URL, and
// start parsing. The backend then auto-chains parse -> control mapping, so the
// document lands in "mapped" ready for the human review checkpoint.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient } from "../api/client";
import type { Baseline, DocumentRecord, DocumentStatus, Project } from "../api/types";

interface Props {
  client: ApiClient;
  onOpenDocument: (projectId: string, documentId: string) => void;
  onOpenCoverage: (projectId: string) => void;
}

const BASELINES: { value: Baseline; label: string }[] = [
  { value: "generic_800_53", label: "Generic 800-53" },
  { value: "fips199_low", label: "FIPS 199 Low" },
  { value: "fips199_moderate", label: "FIPS 199 Moderate" },
  { value: "fips199_high", label: "FIPS 199 High" },
  { value: "fedramp", label: "FedRAMP" },
  { value: "dod_cnssi_1253", label: "DoD / CNSSI 1253" },
];

// Statuses where the backend is still working and the list should keep refreshing.
const BUSY: DocumentStatus[] = ["uploaded", "parsing", "parsed", "mapping", "drafting", "exporting"];

const POLL_MS = 2500;

export default function ProjectBrowser({ client, onOpenDocument, onOpenCoverage }: Props) {
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
      await client.uploadDocument(selected.project_id, file);
      await loadDocuments(selected.project_id);
      if (fileInput.current) fileInput.current.value = "";
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      {error && <p style={{ color: "#b00" }}>Error: {error}</p>}

      <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap" }}>
        {/* ---- Projects ---- */}
        <div style={{ flex: "1 1 300px" }}>
          <h2>Projects</h2>
          <p style={hint}>A project is one system&apos;s A&amp;A package.</p>

          {projects.length === 0 ? (
            <p style={hint}>No projects yet — create one below.</p>
          ) : (
            <ul style={list}>
              {projects.map((p) => (
                <li key={p.project_id}>
                  <button
                    onClick={() => setSelected(p)}
                    style={{
                      ...rowButton,
                      fontWeight: selected?.project_id === p.project_id ? 700 : 400,
                    }}
                  >
                    {p.name}
                    <span style={hint}>
                      {" "}
                      · {p.baseline} · {p.document_count} doc
                      {p.document_count === 1 ? "" : "s"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={createProject} style={{ marginTop: "1rem" }}>
            <input
              placeholder="new project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              aria-label="new project name"
            />
            <select
              value={baseline}
              onChange={(e) => setBaseline(e.target.value as Baseline)}
              aria-label="baseline"
              style={{ marginLeft: "0.5rem" }}
            >
              {BASELINES.map((b) => (
                <option key={b.value} value={b.value}>
                  {b.label}
                </option>
              ))}
            </select>
            <button type="submit" disabled={busy || !name.trim()} style={{ marginLeft: "0.5rem" }}>
              Create
            </button>
          </form>
        </div>

        {/* ---- Documents ---- */}
        <div style={{ flex: "1 1 380px" }}>
          <h2>Documents</h2>
          {!selected ? (
            <p style={hint}>Select a project to see its policy documents.</p>
          ) : (
            <>
              <p style={hint}>
                {selected.name} — upload the Rev 4 policy documents for this system.
              </p>

              {documents.length === 0 ? (
                <p style={hint}>No documents yet.</p>
              ) : (
                <table style={{ borderCollapse: "collapse", width: "100%" }}>
                  <tbody>
                    {documents.map((d) => (
                      <tr key={d.document_id}>
                        <td style={cell}>{d.filename}</td>
                        <td style={cell}>
                          <StatusBadge status={d.status} />
                        </td>
                        <td style={cell}>
                          {d.section_count > 0 ? `${d.section_count} sections` : ""}
                        </td>
                        <td style={cell}>
                          <button
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
              )}

              <div style={{ marginTop: "1rem" }}>
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
                {busy && <span style={hint}> uploading…</span>}
                <p style={hint}>
                  .docx only. Upload starts parsing and control mapping automatically.
                </p>
              </div>

              <button style={{ marginTop: "0.5rem" }} onClick={() => onOpenCoverage(selected.project_id)}>
                Package coverage &amp; gaps →
              </button>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function StatusBadge({ status }: { status: DocumentStatus }) {
  const working = BUSY.includes(status);
  const failed = status === "failed";
  const color = failed ? "#b00" : working ? "#a70" : "#160";
  return (
    <span style={{ color }}>
      {working && status !== "parsed" ? `${status}…` : status}
    </span>
  );
}

const hint: React.CSSProperties = { color: "#666", fontSize: "0.85rem" };
const list: React.CSSProperties = { listStyle: "none", padding: 0, margin: 0 };
const rowButton: React.CSSProperties = {
  background: "none",
  border: "none",
  padding: "0.35rem 0",
  cursor: "pointer",
  textAlign: "left",
  width: "100%",
  font: "inherit",
};
const cell: React.CSSProperties = {
  borderBottom: "1px solid #eee",
  padding: "0.4rem 0.5rem 0.4rem 0",
  textAlign: "left",
};
