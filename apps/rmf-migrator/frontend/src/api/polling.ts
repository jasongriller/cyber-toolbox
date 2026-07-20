import type { ExportJob } from "./types";

export interface ExportJobSource {
  getExportJob(projectId: string, jobId: string): Promise<ExportJob>;
}

interface PollOptions {
  intervalMs?: number;
  maxAttempts?: number;
  sleep?: (milliseconds: number) => Promise<void>;
}

const DEFAULT_INTERVAL_MS = 2_000;
const DEFAULT_MAX_ATTEMPTS = 150;

export async function waitForExportJob(
  source: ExportJobSource,
  projectId: string,
  jobId: string,
  options: PollOptions = {},
): Promise<ExportJob> {
  const intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS;
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const sleep = options.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const job = await source.getExportJob(projectId, jobId);
    if (job.status === "succeeded") return job;
    if (job.status === "failed") {
      throw new Error(`export failed (${job.error_type ?? "unknown"})`);
    }
    if (attempt < maxAttempts - 1) await sleep(intervalMs);
  }

  throw new Error("export timed out after 5 minutes; the job may still be running");
}
