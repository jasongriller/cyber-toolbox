import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiError, joinUrl, parseControlIds } from "./client";

describe("joinUrl", () => {
  it("joins without doubling slashes", () => {
    expect(joinUrl("https://api.example/", "/projects")).toBe("https://api.example/projects");
  });

  it("joins when neither side has a slash", () => {
    expect(joinUrl("https://api.example", "projects")).toBe("https://api.example/projects");
  });

  it("handles a sub-path base", () => {
    expect(joinUrl("/api", "/projects/p1/jobs/j1")).toBe("/api/projects/p1/jobs/j1");
  });
});

describe("parseControlIds", () => {
  it("splits on commas and whitespace, uppercases, dedupes", () => {
    expect(parseControlIds("ac-2, AC-2  au-2")).toEqual(["AC-2", "AU-2"]);
  });

  it("returns empty for blank input", () => {
    expect(parseControlIds("   ")).toEqual([]);
  });
});

describe("ApiClient", () => {
  afterEach(() => vi.restoreAllMocks());

  function mockFetch(status: number, body: unknown) {
    return vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }

  it("updateMapping PUTs control ids to the section URL", async () => {
    const spy = mockFetch(200, { section_id: "sec_1", final_control_ids: ["AC-2"] });
    const client = new ApiClient("/api");
    await client.updateMapping("p1", "d1", "sec_1", ["AC-2"]);

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/projects/p1/documents/d1/mappings/sec_1");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({ control_ids: ["AC-2"] });
  });

  it("getMappings GETs the mappings URL", async () => {
    const spy = mockFetch(200, { document_status: "mapped", mappings: [] });
    const client = new ApiClient("/api");
    const res = await client.getMappings("p1", "d1");

    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/documents/d1/mappings");
    expect(res.document_status).toBe("mapped");
  });

  it("throws ApiError with server message on non-2xx", async () => {
    mockFetch(400, { error: "unknown Rev 4 control ids: ZZ-99" });
    const client = new ApiClient("/api");
    await expect(client.updateMapping("p1", "d1", "s1", ["ZZ-99"])).rejects.toThrowError(
      ApiError,
    );
  });

  it("updateDraft PUTs text to the draft URL", async () => {
    const spy = mockFetch(200, { section_id: "s1", status: "edited" });
    const client = new ApiClient("/api");
    await client.updateDraft("p1", "d1", "s1", "new text");

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/projects/p1/documents/d1/drafts/s1");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(init?.body as string)).toEqual({ text: "new text" });
  });

  it("chat POSTs messages to the section chat URL", async () => {
    const spy = mockFetch(200, { reply: "sure" });
    const client = new ApiClient("/api");
    const res = await client.chat("p1", "d1", "s1", [{ role: "user", content: "hi" }]);

    const [url, init] = spy.mock.calls[0];
    expect(url).toBe("/api/projects/p1/documents/d1/sections/s1/chat");
    expect(init?.method).toBe("POST");
    expect(res.reply).toBe("sure");
  });

  it("approveDraft POSTs to the approve URL", async () => {
    const spy = mockFetch(200, { section_id: "s1", status: "approved" });
    const client = new ApiClient("/api");
    await client.approveDraft("p1", "d1", "s1");
    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/documents/d1/drafts/s1/approve");
  });

  it("startExport POSTs to the export URL", async () => {
    const spy = mockFetch(202, { job: { job_id: "xjob_1", status: "pending" } });
    const client = new ApiClient("/api");
    const res = await client.startExport("p1", "d1");
    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/documents/d1/export");
    expect(res.job.job_id).toBe("xjob_1");
  });

  it("getExportDownload GETs the download URL", async () => {
    const spy = mockFetch(200, { url: "https://s3/...", expires_in: 300 });
    const client = new ApiClient("/api");
    const res = await client.getExportDownload("p1", "d1");
    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/documents/d1/export/download");
    expect(res.url).toContain("s3");
  });

  it("getDecisionLogCsv fetches CSV text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("order,heading\n0,AC Policy\n", {
        status: 200,
        headers: { "Content-Type": "text/csv" },
      }),
    );
    const client = new ApiClient("/api");
    const csv = await client.getDecisionLogCsv("p1", "d1");
    expect(csv).toContain("order,heading");
  });

  it("getCoverage GETs coverage with a baseline query", async () => {
    const spy = mockFetch(200, { baseline: "low", covered_count: 2, covered_controls: [] });
    const client = new ApiClient("/api");
    const res = await client.getCoverage("p1", "low");
    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/coverage?baseline=low");
    expect(res.baseline).toBe("low");
  });

  it("getCoverage omits the query when no baseline given", async () => {
    const spy = mockFetch(200, { baseline: null, covered_count: 0, covered_controls: [] });
    const client = new ApiClient("/api");
    await client.getCoverage("p1");
    expect(spy.mock.calls[0][0]).toBe("/api/projects/p1/coverage");
  });

  it("getConversionMatrixCsv fetches CSV text", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("rev4_control,rev4_title\nAC-1,Policy\n", {
        status: 200,
        headers: { "Content-Type": "text/csv" },
      }),
    );
    const client = new ApiClient("/api");
    const csv = await client.getConversionMatrixCsv("p1");
    expect(csv).toContain("rev4_control");
  });
});
