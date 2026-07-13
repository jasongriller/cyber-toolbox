// Per-section chat assistant. Stateless on the server: this component holds the
// running conversation and sends it each turn.

import { useState } from "react";
import { ApiClient } from "../api/client";
import type { ChatMessage } from "../api/types";

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
  sectionId: string;
}

export default function ChatPanel({ client, projectId, documentId, sectionId }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    const content = input.trim();
    if (!content || busy) return;
    const history: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages(history);
    setInput("");
    setBusy(true);
    try {
      const { reply } = await client.chat(projectId, documentId, sectionId, history);
      setMessages([...history, { role: "assistant", content: reply }]);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ marginTop: "0.75rem", borderTop: "1px dashed #ccc", paddingTop: "0.5rem" }}>
      <div style={{ maxHeight: 220, overflowY: "auto", marginBottom: "0.5rem" }}>
        {messages.length === 0 && (
          <p style={{ color: "#888", margin: 0 }}>
            Ask the assistant to refine wording or explain a control.
          </p>
        )}
        {messages.map((m, i) => (
          <p key={i} style={{ margin: "0.25rem 0" }}>
            <strong>{m.role === "user" ? "You" : "Assistant"}:</strong> {m.content}
          </p>
        ))}
      </div>
      {error && <p style={{ color: "#b00", margin: "0.25rem 0" }}>Error: {error}</p>}
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <input
          style={{ flex: 1 }}
          value={input}
          disabled={busy}
          placeholder="Ask about this control…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
          }}
          aria-label="chat message"
        />
        <button disabled={busy || !input.trim()} onClick={() => void send()}>
          {busy ? "…" : "Send"}
        </button>
      </div>
    </div>
  );
}
