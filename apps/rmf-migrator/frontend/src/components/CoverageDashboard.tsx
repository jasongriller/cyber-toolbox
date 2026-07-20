// Package-level coverage dashboard. Shows how much of the selected Rev 5
// baseline the project's drafts cover, lists the gaps, and highlights Rev 5-new
// controls (e.g. the SR supply-chain family) that no Rev 4 document carried
// forward. Also downloads the conversion summary matrix (CSV).

import { useCallback, useEffect, useState } from "react";
import { ArrowsClockwise, FileCsv, BracketsCurly } from "@phosphor-icons/react";
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

  const downloadBlob = (data: string, mime: string, filename: string) => {
    const url = URL.createObjectURL(new Blob([data], { type: mime }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  const downloadMatrix = async () => {
    try {
      const csv = await client.getConversionMatrixCsv(projectId);
      downloadBlob(csv, "text/csv", `conversion-matrix-${projectId}.csv`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const downloadOscal = async () => {
    try {
      const jsonText = await client.getOscalJson(projectId);
      downloadBlob(jsonText, "application/json", `oscal-component-${projectId}.json`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const pct = coverage?.coverage_pct ?? null;

  return (
    <section>
      <div className="section-head">
        <h2>Coverage dashboard</h2>
      </div>

      <div className="toolbar">
        <label className="field-label" style={{ marginBottom: 0 }}>
          Baseline
        </label>
        <select
          className="field"
          style={{ width: "auto" }}
          value={baseline}
          onChange={(e) => setBaseline(e.target.value)}
        >
          {BASELINES.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>
        <button className="btn" onClick={() => void load()} disabled={loading}>
          <ArrowsClockwise size={14} /> Refresh
        </button>
        <button className="btn" onClick={() => void downloadMatrix()}>
          <FileCsv size={14} /> Conversion matrix (CSV)
        </button>
        <button className="btn" onClick={() => void downloadOscal()}>
          <BracketsCurly size={14} /> OSCAL (JSON)
        </button>
      </div>

      {error && (
        <p className="banner banner--error" style={{ marginTop: "1rem" }}>
          {error}
        </p>
      )}
      {loading && (
        <p className="loading" style={{ marginTop: "1rem" }}>
          <span className="spinner" /> Loading…
        </p>
      )}

      {coverage && (
        <div style={{ marginTop: "1.5rem" }}>
          {pct !== null ? (
            <>
              <div className="cov-metric">
                <span className="big">{pct}%</span>
                <span className="muted">
                  {coverage.baseline_covered}/{coverage.baseline_total} controls · baseline{" "}
                  <span className="mono">{coverage.baseline}</span>
                </span>
              </div>
              <div className="bar">
                <div className="bar__fill" style={{ width: `${pct}%` }} />
              </div>
            </>
          ) : (
            <p>
              Covered <strong className="mono">{coverage.covered_count}</strong> Rev 5 controls.
              Select a baseline for gap analysis.
            </p>
          )}

          <div className="gap-grid">
            <GapList
              title={`Baseline gaps (${coverage.baseline_gaps.length})`}
              ids={coverage.baseline_gaps}
              empty="No baseline gaps. Every required control is addressed."
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
    <div>
      <h3>{title}</h3>
      {ids.length === 0 ? (
        <p className="gap-empty">{empty}</p>
      ) : (
        <ul className="gap-list">
          {ids.map((id) => (
            <li key={id} className={highlightPrefix && id.startsWith(highlightPrefix) ? "hl" : ""}>
              {id}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
