// M1 placeholder shell. The full workflow UI (mapping review, side-by-side
// editor, chat, coverage dashboard) arrives in M2-M5. This shell exists so the
// app builds and the API client wiring is exercised end to end.

export default function App() {
  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>RMF Rev 5 Migrator</h1>
      <p>
        Convert RMF Rev 4 policy documents to Rev 5, self-hosted in your own AWS account.
      </p>
      <p style={{ color: "#666" }}>
        Milestone M1 (ingest &amp; parse pipeline) is in place. Project workspace, control
        mapping review, drafting, and the coverage dashboard land in later milestones.
      </p>
    </main>
  );
}
