// Typed client for the M1 API surface: create project, register + upload a
// document, trigger parse, poll job status.
//
// The upload itself is a direct PUT to the presigned S3 URL — document bytes go
// browser -> S3 and never transit our API.

import type {
  ApproveResponse,
  Baseline,
  ChatMessage,
  DocumentRecord,
  Draft,
  DraftsResponse,
  ExportJob,
  MappingsResponse,
  ParseJob,
  PresignedGet,
  Project,
  Section,
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

  // ---- Mapping review (M2) ----

  getDocument(projectId: string, documentId: string): Promise<DocumentRecord> {
    return this.request("GET", `/projects/${projectId}/documents/${documentId}`);
  }

  listSections(projectId: string, documentId: string): Promise<{ sections: Section[] }> {
    return this.request("GET", `/projects/${projectId}/documents/${documentId}/sections`);
  }

  getMappings(projectId: string, documentId: string): Promise<MappingsResponse> {
    return this.request("GET", `/projects/${projectId}/documents/${documentId}/mappings`);
  }

  updateMapping(
    projectId: string,
    documentId: string,
    sectionId: string,
    controlIds: string[],
  ): Promise<import("./types").ControlMapping> {
    return this.request(
      "PUT",
      `/projects/${projectId}/documents/${documentId}/mappings/${sectionId}`,
      { control_ids: controlIds },
    );
  }

  approveMappings(projectId: string, documentId: string): Promise<ApproveResponse> {
    return this.request(
      "POST",
      `/projects/${projectId}/documents/${documentId}/mappings/approve`,
    );
  }

  // ---- Rev 5 drafts (M3) ----

  getDrafts(projectId: string, documentId: string): Promise<DraftsResponse> {
    return this.request("GET", `/projects/${projectId}/documents/${documentId}/drafts`);
  }

  updateDraft(
    projectId: string,
    documentId: string,
    sectionId: string,
    text: string,
  ): Promise<Draft> {
    return this.request(
      "PUT",
      `/projects/${projectId}/documents/${documentId}/drafts/${sectionId}`,
      { text },
    );
  }

  approveDraft(projectId: string, documentId: string, sectionId: string): Promise<Draft> {
    return this.request(
      "POST",
      `/projects/${projectId}/documents/${documentId}/drafts/${sectionId}/approve`,
    );
  }

  chat(
    projectId: string,
    documentId: string,
    sectionId: string,
    messages: ChatMessage[],
  ): Promise<{ reply: string }> {
    return this.request(
      "POST",
      `/projects/${projectId}/documents/${documentId}/sections/${sectionId}/chat`,
      { messages },
    );
  }

  // ---- Rev 5 export + decision log (M4) ----

  startExport(projectId: string, documentId: string): Promise<{ job: ExportJob }> {
    return this.request("POST", `/projects/${projectId}/documents/${documentId}/export`);
  }

  getExportJob(projectId: string, jobId: string): Promise<ExportJob> {
    return this.request("GET", `/projects/${projectId}/export-jobs/${jobId}`);
  }

  getExportDownload(projectId: string, documentId: string): Promise<PresignedGet> {
    return this.request(
      "GET",
      `/projects/${projectId}/documents/${documentId}/export/download`,
    );
  }

  /** Fetch the decision-log CSV as text (not JSON). */
  async getDecisionLogCsv(projectId: string, documentId: string): Promise<string> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/projects/${projectId}/documents/${documentId}/decision-log.csv`),
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    return res.text();
  }
}

/** Parse a comma/space separated control-id string into a normalized list. */
export function parseControlIds(input: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of input.split(/[\s,]+/)) {
    const id = token.trim().toUpperCase();
    if (id && !seen.has(id)) {
      seen.add(id);
      out.push(id);
    }
  }
  return out;
}
