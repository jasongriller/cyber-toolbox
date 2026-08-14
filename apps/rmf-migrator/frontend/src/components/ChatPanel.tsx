// Per-section chat assistant. Stateless on the server: this component holds the
// running conversation and sends it each turn.

import { useRef, useState } from "react";
import { PaperPlaneRight } from "@phosphor-icons/react";
import { ApiClient } from "../api/client";
import type { ChatMessage } from "../api/types";

// ChatMessage plus a render-only id for stable React keys; stripped before the
// history is sent — the backend's schema is exactly {role, content}.
type LocalMessage = ChatMessage & { id: number };

interface Props {
  client: ApiClient;
  projectId: string;
  documentId: string;
  sectionId: string;
}

export default function ChatPanel({ client, projectId, documentId, sectionId }: Props) {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nextId = useRef(0);

  const send = async () => {
    const content = input.trim();
    if (!content || busy) return;
    const history: LocalMessage[] = [
      ...messages,
      { role: "user", content, id: nextId.current++ },
    ];
    setMessages(history);
    setInput("");
    setBusy(true);
    try {
      const payload = history.map(({ role, content: c }) => ({ role, content: c }));
      const { reply } = await client.chat(projectId, documentId, sectionId, payload);
      setMessages([...history, { role: "assistant", content: reply, id: nextId.current++ }]);
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
        {messages.map((m) => (
          <div key={m.id} className={`chat__msg chat__msg--${m.role === "user" ? "user" : "bot"}`}>
            <span className="chat__role">{m.role === "user" ? "You" : "Assistant"}</span>
            {m.content}
          </div>
        ))}
      </div>
      {error && <p role="alert" className="banner banner--error">{error}</p>}
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
