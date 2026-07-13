// Typed client for the M1 API surface: create project, register + upload a
// document, trigger parse, poll job status.
//
// The upload itself is a direct PUT to the presigned S3 URL — document bytes go
// browser -> S3 and never transit our API.

import type {
  Baseline,
  DocumentRecord,
  ParseJob,
  Project,
  UploadTarget,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Join a base URL and a path without doubling or dropping slashes. */
export function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/, "");
  const p = path.replace(/^\/+/, "");
  return `${b}/${p}`;
}

export class ApiClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string = import.meta.env.VITE_API_BASE_URL ?? "/api") {
    this.baseUrl = baseUrl;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(joinUrl(this.baseUrl, path), {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let message = res.statusText;
      try {
        const data = (await res.json()) as { error?: string };
        if (data.error) message = data.error;
      } catch {
        // non-JSON error body; keep statusText
      }
      throw new ApiError(res.status, message);
    }
    return (await res.json()) as T;
  }

  createProject(name: string, baseline: Baseline): Promise<Project> {
    return this.request<Project>("POST", "/projects", { name, baseline });
  }

  registerDocument(
    projectId: string,
    filename: string,
  ): Promise<{ document: DocumentRecord; upload: UploadTarget }> {
    return this.request("POST", `/projects/${projectId}/documents`, { filename });
  }

  /** Upload the file bytes directly to S3 using the presigned target. */
  async uploadBytes(target: UploadTarget, file: Blob): Promise<void> {
    const res = await fetch(target.url, {
      method: target.method,
      headers: target.headers,
      body: file,
    });
    if (!res.ok) {
      throw new ApiError(res.status, `upload failed: ${res.statusText}`);
    }
  }

  startParse(projectId: string, documentId: string): Promise<{ job: ParseJob }> {
    return this.request("POST", `/projects/${projectId}/documents/${documentId}/parse`);
  }

  getJob(projectId: string, jobId: string): Promise<ParseJob> {
    return this.request("GET", `/projects/${projectId}/jobs/${jobId}`);
  }
}
