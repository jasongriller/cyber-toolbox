// Per-section chat assistant. Stateless on the server: this component holds the
// running conversation and sends it each turn.

import { useState } from "react";
import { PaperPlaneRight } from "@phosphor-icons/react";
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
    <div className="chat">
      <div className="chat__log">
        {messages.length === 0 && (
          <p className="muted" style={{ margin: 0 }}>
            Ask the assistant to refine wording or explain a control.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat__msg chat__msg--${m.role === "user" ? "user" : "bot"}`}>
            <span className="chat__role">{m.role === "user" ? "You" : "Assistant"}</span>
            {m.content}
          </div>
        ))}
      </div>
      {error && <p className="banner banner--error">{error}</p>}
      <div className="chat__input">
        <input
          className="field"
          value={input}
          disabled={busy}
          placeholder="Ask about this control…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void send();
          }}
          aria-label="chat message"
        />
        <button className="btn btn--accent" disabled={busy || !input.trim()} onClick={() => void send()}>
          {busy ? "…" : <PaperPlaneRight size={15} />}
        </button>
      </div>
    </div>
  );
}
