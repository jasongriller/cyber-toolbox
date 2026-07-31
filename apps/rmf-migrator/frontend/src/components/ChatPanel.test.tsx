import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApiClient } from "../api/client";
import ChatPanel from "./ChatPanel";

function stubClient(overrides: Record<string, unknown> = {}) {
  return {
    chat: vi.fn().mockResolvedValue({ reply: "AC-2 requires account reviews." }),
    ...overrides,
  } as unknown as ApiClient;
}

function renderPanel(client: ApiClient) {
  return render(
    <ChatPanel client={client} projectId="proj_1" documentId="doc_1" sectionId="s1" />,
  );
}

describe("ChatPanel", () => {
  it("sends the message and shows the assistant reply", async () => {
    const client = stubClient();
    renderPanel(client);

    await userEvent.type(screen.getByLabelText(/chat message/i), "what does AC-2 need?{Enter}");

    expect(await screen.findByText(/AC-2 requires account reviews\./i)).toBeInTheDocument();
    expect(screen.getByText(/what does AC-2 need\?/i)).toBeInTheDocument();
  });

  it("announces a failed turn as an alert instead of failing silently", async () => {
    const client = stubClient({
      chat: vi.fn().mockRejectedValue(new Error("model unavailable")),
    });
    renderPanel(client);

    await userEvent.type(screen.getByLabelText(/chat message/i), "hello{Enter}");

    const banner = await screen.findByRole("alert");
    expect(banner).toHaveTextContent(/model unavailable/i);
  });
});
