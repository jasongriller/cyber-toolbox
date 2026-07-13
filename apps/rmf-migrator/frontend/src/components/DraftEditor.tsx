// Rev 5 draft editor — side-by-side original vs. proposed Rev 5 language, with
// improvement suggestions and a per-section chat assistant. The reviewer edits
// and approves each section's draft.
//
// While the backend is still drafting (status mapping_approved/drafting), this
// polls until drafts are ready.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient } from "../api/client";
import type { Draft, DocumentStatus, Section } from "../api/types";
import ChatPanel from "./ChatPanel";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
}

const POLL_MS = 2500;
const DRAFTING: DocumentStatus[] = ["mapping_approved", "drafting"];

export default function DraftEditor({ client, projectId, documentId }: Props) {
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [sections, setSections] = useState<Section[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [text, setText] = useState<Record<string, string>>({});
  const [openChat, setOpenChat] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const sectionById = useMemo(() => {
    const m: Record<string, Section> = {};
    for (const s of sections) m[s.section_id] = s;
    return m;
  }, [sections]);

  const load = useCallback(async () => {
    try {
      const [sec, dr] = await Promise.all([
        client.listSections(projectId, documentId),
        client.getDrafts(projectId, documentId),
      ]);
      setSections(sec.sections);
      setDrafts(dr.drafts);
      setStatus(dr.document_status);
      setText((prev) => {
        const next = { ...prev };
        for (const d of dr.drafts) {
          if (next[d.section_id] === undefined) {
            next[d.section_id] = d.edited_text ?? d.draft_text;
          }
        }
        return next;
      });
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [client, projectId, documentId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (status === null || !DRAFTING.includes(status)) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [status, load]);

  const save = async (sectionId: string) => {
    setBusy(true);
    try {
      const updated = await client.updateDraft(projectId, documentId, sectionId, text[sectionId] ?? "");
      setDrafts((prev) => prev.map((d) => (d.section_id === sectionId ? updated : d)));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async (sectionId: string) => {
    setBusy(true);
    try {
      // Persist current edits first so approval freezes what the reviewer sees.
      await client.updateDraft(projectId, documentId, sectionId, text[sectionId] ?? "");
      const updated = await client.approveDraft(projectId, documentId, sectionId);
      setDrafts((prev) => prev.map((d) => (d.section_id === sectionId ? updated : d)));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (status !== null && DRAFTING.includes(status)) {
    return <p>Drafting Rev 5 language ({status})… this refreshes automatically.</p>;
  }

  return (
    <section>
      <h2>Rev 5 draft editor</h2>
      <p>
        Status: <strong>{status ?? "loading…"}</strong>
      </p>
      {error && <p style={{ color: "#b00" }}>Error: {error}</p>}

      {drafts.map((d) => {
        const section = sectionById[d.section_id];
        const approved = d.status === "approved";
        return (
          <article key={d.section_id} style={card}>
            <h3 style={{ margin: "0 0 0.25rem" }}>
              {section?.heading || <em>(preamble)</em>}{" "}
              <span style={{ fontWeight: 400, color: "#666" }}>
                → {d.rev5_control_ids.join(", ") || "no Rev 5 target"}
              </span>
            </h3>
            <p style={{ margin: "0 0 0.5rem", fontSize: "0.85rem", color: "#666" }}>
              {d.dispositions
                .map((n) => `${n.rev4_id}→${n.rev5_ids.join("/") || "—"} (${n.relationship})`)
                .join("  ·  ")}
              {"  ·  "}
              <span>state: {d.status}</span>
            </p>

            <div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 320px" }}>
                <label style={lbl}>Original (Rev 4)</label>
                <textarea readOnly value={section?.text ?? ""} style={{ ...ta, background: "#f7f7f7" }} />
              </div>
              <div style={{ flex: "1 1 320px" }}>
                <label style={lbl}>Proposed Rev 5</label>
                <textarea
                  value={text[d.section_id] ?? ""}
                  disabled={approved || busy}
                  onChange={(e) => setText((t) => ({ ...t, [d.section_id]: e.target.value }))}
                  style={ta}
                  aria-label={`rev5 draft for ${section?.heading ?? d.section_id}`}
                />
              </div>
            </div>

            {d.suggestions.length > 0 && (
              <details style={{ marginTop: "0.5rem" }}>
                <summary>Suggestions ({d.suggestions.length})</summary>
                <ul>
                  {d.suggestions.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </details>
            )}

            <div style={{ marginTop: "0.5rem", display: "flex", gap: "0.5rem" }}>
              {!approved && (
                <>
                  <button disabled={busy} onClick={() => void save(d.section_id)}>
                    Save
                  </button>
                  <button disabled={busy} onClick={() => void approve(d.section_id)}>
                    Approve section
                  </button>
                </>
              )}
              <button
                onClick={() => setOpenChat((c) => (c === d.section_id ? null : d.section_id))}
              >
                {openChat === d.section_id ? "Hide assistant" : "Ask assistant"}
              </button>
            </div>

            {openChat === d.section_id && (
              <ChatPanel
                client={client}
                projectId={projectId}
                documentId={documentId}
                sectionId={d.section_id}
              />
            )}
          </article>
        );
      })}
    </section>
  );
}

const card: React.CSSProperties = {
  border: "1px solid #ddd",
  borderRadius: 6,
  padding: "1rem",
  marginBottom: "1rem",
};
const lbl: React.CSSProperties = { display: "block", fontSize: "0.8rem", color: "#555" };
const ta: React.CSSProperties = { width: "100%", minHeight: 140, fontFamily: "inherit" };
