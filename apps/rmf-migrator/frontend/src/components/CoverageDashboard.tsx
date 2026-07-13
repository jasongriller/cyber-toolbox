// Package-level coverage dashboard. Shows how much of the selected Rev 5
// baseline the project's drafts cover, lists the gaps, and highlights Rev 5-new
// controls (e.g. the SR supply-chain family) that no Rev 4 document carried
// forward. Also downloads the conversion summary matrix (CSV).

import { useCallback, useEffect, useState } from "react";
import { ApiClient } from "../api/client";
import type { Coverage } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
}

const BASELINES = ["(project default)", "low", "moderate", "high"];

export default function CoverageDashboard({ client, projectId }: Props) {
  const [baseline, setBaseline] = useState("(project default)");
  const [coverage, setCoverage] = useState<Coverage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const override = baseline === "(project default)" ? undefined : baseline;
      setCoverage(await client.getCoverage(projectId, override));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [client, projectId, baseline]);

  useEffect(() => {
    void load();
  }, [load]);

  const downloadMatrix = async () => {
    try {
      const csv = await client.getConversionMatrixCsv(projectId);
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `conversion-matrix-${projectId}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const pct = coverage?.coverage_pct ?? null;

  return (
    <section>
      <h2>Coverage dashboard</h2>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", flexWrap: "wrap" }}>
        <label>
          Baseline:{" "}
          <select value={baseline} onChange={(e) => setBaseline(e.target.value)}>
            {BASELINES.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => void load()} disabled={loading}>
          Refresh
        </button>
        <button onClick={() => void downloadMatrix()}>Download conversion matrix (CSV)</button>
      </div>

      {error && <p style={{ color: "#b00" }}>Error: {error}</p>}
      {loading && <p>Loading…</p>}

      {coverage && (
        <div style={{ marginTop: "1rem" }}>
          {pct !== null ? (
            <>
              <div style={{ marginBottom: "0.25rem" }}>
                Baseline coverage: <strong>{pct}%</strong> ({coverage.baseline_covered}/
                {coverage.baseline_total} controls, baseline: {coverage.baseline})
              </div>
              <div style={bar}>
                <div style={{ ...barFill, width: `${pct}%` }} />
              </div>
            </>
          ) : (
            <p>
              Covered {coverage.covered_count} Rev 5 controls. Select a baseline for gap analysis.
            </p>
          )}

          <div style={{ display: "flex", gap: "2rem", flexWrap: "wrap", marginTop: "1rem" }}>
            <GapList
              title={`Baseline gaps (${coverage.baseline_gaps.length})`}
              ids={coverage.baseline_gaps}
              empty="No baseline gaps — every required control is addressed."
            />
            <GapList
              title={`New in Rev 5, not covered (${coverage.new_in_rev5_gaps.length})`}
              ids={coverage.new_in_rev5_gaps}
              empty="All applicable Rev 5-new controls are covered."
              highlightPrefix="SR-"
            />
          </div>
        </div>
      )}
    </section>
  );
}

function GapList({
  title,
  ids,
  empty,
  highlightPrefix,
}: {
  title: string;
  ids: string[];
  empty: string;
  highlightPrefix?: string;
}) {
  return (
    <div style={{ flex: "1 1 260px" }}>
      <h3 style={{ fontSize: "1rem" }}>{title}</h3>
      {ids.length === 0 ? (
        <p style={{ color: "#690" }}>{empty}</p>
      ) : (
        <ul style={{ maxHeight: 280, overflowY: "auto", columns: 2, paddingLeft: "1.2rem" }}>
          {ids.map((id) => (
            <li
              key={id}
              style={
                highlightPrefix && id.startsWith(highlightPrefix)
                  ? { fontWeight: 700, color: "#a15" }
                  : undefined
              }
            >
              {id}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const bar: React.CSSProperties = {
  height: 16,
  background: "#eee",
  borderRadius: 8,
  overflow: "hidden",
  maxWidth: 480,
};
const barFill: React.CSSProperties = { height: "100%", background: "#4a8" };
