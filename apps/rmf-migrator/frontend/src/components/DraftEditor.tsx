// Rev 5 draft editor — side-by-side original vs. proposed Rev 5 language, with
// improvement suggestions and a per-section chat assistant. The reviewer edits
// and approves each section's draft.
//
// While the backend is still drafting (status mapping_approved/drafting), this
// polls until drafts are ready.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ChatCircleDots, Check } from "@phosphor-icons/react";
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
      const updated = await client.updateDraft(
        projectId,
        documentId,
        sectionId,
        text[sectionId] ?? "",
      );
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
      await client.approveDraft(projectId, documentId, sectionId);
      // The last approved section advances the document to review_approved.
      // Reload both the drafts and document status so Export reflects that gate.
      await load();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (status !== null && DRAFTING.includes(status)) {
    return (
      <div className="panel">
        <span className="loading">
          <span className="spinner" /> Drafting Rev 5 language ({status}). This refreshes
          automatically.
        </span>
      </div>
    );
  }

  const approvedCount = drafts.filter((d) => d.status === "approved").length;

  return (
    <section>
      <div className="section-head">
        <h2>Rev 5 draft editor</h2>
        <span className="mono muted">
          {approvedCount}/{drafts.length} approved
        </span>
      </div>
      {error && <p className="banner banner--error">{error}</p>}

      {drafts.map((d) => {
        const section = sectionById[d.section_id];
        const approved = d.status === "approved";
        const chatOpen = openChat === d.section_id;
        return (
          <article key={d.section_id} className="draft-card">
            <div className="draft-card__head">
              <h3>{section?.heading || <em className="muted">(preamble)</em>}</h3>
              <span className="draft-card__target">
                → {d.rev5_control_ids.join(", ") || "no Rev 5 target"}
              </span>
              <span className={approved ? "pill pill--ok" : "pill"}>{d.status}</span>
            </div>
            <p className="disp">
              {d.dispositions.map((n) => (
                <span key={n.rev4_id}>
                  {n.rev4_id} → {n.rev5_ids.join("/") || "none"} ({n.relationship})
                </span>
              ))}
            </p>

            <div className="grid-2">
              <div>
                <label className="field-label">Original (Rev 4)</label>
                <textarea className="field" readOnly value={section?.text ?? ""} />
              </div>
              <div>
                <label className="field-label">Proposed Rev 5</label>
                <textarea
                  className="field"
                  value={text[d.section_id] ?? ""}
                  disabled={approved || busy}
                  onChange={(e) => setText((t) => ({ ...t, [d.section_id]: e.target.value }))}
                  aria-label={`rev5 draft for ${section?.heading ?? d.section_id}`}
                />
              </div>
            </div>

            {d.suggestions.length > 0 && (
              <details className="suggest">
                <summary>Suggestions ({d.suggestions.length})</summary>
                <ul>
                  {d.suggestions.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </details>
            )}

            <div className="actionbar">
              {!approved && (
                <>
                  <button className="btn" disabled={busy} onClick={() => void save(d.section_id)}>
                    Save
                  </button>
                  <button
                    className="btn btn--accent"
                    disabled={busy}
                    onClick={() => void approve(d.section_id)}
                  >
                    <Check size={14} /> Approve section
                  </button>
                </>
              )}
              <button
                className="btn btn--ghost"
                onClick={() => setOpenChat((c) => (c === d.section_id ? null : d.section_id))}
              >
                <ChatCircleDots size={14} /> {chatOpen ? "Hide assistant" : "Ask assistant"}
              </button>
            </div>

            {chatOpen && (
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
