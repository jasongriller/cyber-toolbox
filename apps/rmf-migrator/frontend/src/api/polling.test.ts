import { describe, expect, it, vi } from "vitest";
import type { ExportJob, JobStatus } from "./types";
import { waitForExportJob } from "./polling";

function job(status: JobStatus, errorType: string | null = null): ExportJob {
  return {
    job_id: "xjob_1",
    project_id: "p1",
    document_id: "d1",
    status,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    error_type: errorType,
  };
}

describe("waitForExportJob", () => {
  it("returns after a pending job succeeds", async () => {
    const getExportJob = vi
      .fn()
      .mockResolvedValueOnce(job("pending"))
      .mockResolvedValueOnce(job("succeeded"));
    const sleep = vi.fn().mockResolvedValue(undefined);

    const result = await waitForExportJob(
      { getExportJob },
      "p1",
      "xjob_1",
      { intervalMs: 1, maxAttempts: 3, sleep },
    );

    expect(result.status).toBe("succeeded");
    expect(getExportJob).toHaveBeenCalledTimes(2);
    expect(sleep).toHaveBeenCalledOnce();
  });

  it("surfaces a failed export", async () => {
    const getExportJob = vi.fn().mockResolvedValue(job("failed", "DocumentError"));

    await expect(
      waitForExportJob({ getExportJob }, "p1", "xjob_1", { maxAttempts: 1 }),
    ).rejects.toThrow("DocumentError");
  });

  it("stops polling after the configured bound", async () => {
    const getExportJob = vi.fn().mockResolvedValue(job("running"));
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(
      waitForExportJob(
        { getExportJob },
        "p1",
        "xjob_1",
        { intervalMs: 1, maxAttempts: 3, sleep },
      ),
    ).rejects.toThrow("timed out");
    expect(getExportJob).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenCalledTimes(2);
  });
});
