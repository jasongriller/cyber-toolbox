// Mapping review — the human checkpoint. Shows each parsed section beside the
// LLM's proposed Rev 4 control mapping, lets the reviewer correct any row, then
// approve the whole document (which gates M3 drafting).
//
// While the backend is still mapping (document_status "parsing"/"mapping"), this
// polls until proposals are ready.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiClient, parseControlIds } from "../api/client";
import type { ControlMapping, DocumentStatus, Section } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
}

const POLL_MS = 2000;
const IN_PROGRESS: DocumentStatus[] = ["uploaded", "parsing", "parsed", "mapping"];

export default function MappingReview({ client, projectId, documentId }: Props) {
  const [status, setStatus] = useState<DocumentStatus | null>(null);
  const [sections, setSections] = useState<Section[]>([]);
  const [mappings, setMappings] = useState<ControlMapping[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const headingBySection = useMemo(() => {
    const m: Record<string, Section> = {};
    for (const s of sections) m[s.section_id] = s;
    return m;
  }, [sections]);

  const load = useCallback(async () => {
    try {
      const [sec, map] = await Promise.all([
        client.listSections(projectId, documentId),
        client.getMappings(projectId, documentId),
      ]);
      setSections(sec.sections);
      setMappings(map.mappings);
      setStatus(map.document_status);
      setDrafts((prev) => {
        const next = { ...prev };
        for (const mp of map.mappings) {
          if (next[mp.section_id] === undefined) {
            next[mp.section_id] = (mp.final_control_ids ?? mp.proposed_control_ids).join(", ");
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

  // Poll while mapping is still in progress.
  useEffect(() => {
    if (status === null || !IN_PROGRESS.includes(status)) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [status, load]);

  const saveRow = async (sectionId: string) => {
    setBusy(true);
    try {
      const ids = parseControlIds(drafts[sectionId] ?? "");
      const updated = await client.updateMapping(projectId, documentId, sectionId, ids);
      setMappings((prev) => prev.map((m) => (m.section_id === sectionId ? updated : m)));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    setBusy(true);
    try {
      const res = await client.approveMappings(projectId, documentId);
      setStatus(res.document_status);
      await load();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (status !== null && IN_PROGRESS.includes(status)) {
    return (
      <div className="panel">
        <span className="loading">
          <span className="spinner" /> Mapping in progress ({status}). This refreshes automatically.
        </span>
      </div>
    );
  }

  const approved = status === "mapping_approved";

  return (
    <section>
      <div className="section-head">
        <h2>Control mapping review</h2>
        <span className={approved ? "pill pill--ok" : "pill"}>{status ?? "loading…"}</span>
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {approved
          ? "Mapping approved. Ready for Rev 5 drafting."
          : "Correct the proposed Rev 4 controls per section, then approve to start drafting."}
      </p>
      {error && <p className="banner banner--error">{error}</p>}

      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              <th>Section</th>
              <th>Rev 4 controls</th>
              <th>Confidence</th>
              <th>State</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {mappings.map((m) => {
              const section = headingBySection[m.section_id];
              return (
                <tr key={m.section_id}>
                  <td className="idx">{m.order}</td>
                  <td>{section?.heading || <em className="muted">(preamble)</em>}</td>
                  <td>
                    <input
                      className="field mono"
                      value={drafts[m.section_id] ?? ""}
                      disabled={approved || busy}
                      onChange={(e) => setDrafts((d) => ({ ...d, [m.section_id]: e.target.value }))}
                      aria-label={`controls for section ${m.order}`}
                    />
                  </td>
                  <td className="num">{(m.confidence * 100).toFixed(0)}%</td>
                  <td>
                    <span className="mono muted">{m.status}</span>
                  </td>
                  <td>
                    {!approved && (
                      <button
                        className="btn btn--sm"
                        disabled={busy}
                        onClick={() => void saveRow(m.section_id)}
                      >
                        Save
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {!approved && (
        <button
          className="btn btn--accent"
          style={{ marginTop: "1rem" }}
          disabled={busy || status !== "mapped"}
          onClick={() => void approve()}
        >
          Approve mapping &amp; continue
        </button>
      )}
    </section>
  );
}
