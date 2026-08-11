// Typed client for the M1 API surface: create project, register + upload a
// document, trigger parse, poll job status.
//
// The upload itself is a direct PUT to the presigned S3 URL — document bytes go
// browser -> S3 and never transit our API.

import type {
  ApproveResponse,
  Baseline,
  ChatMessage,
  Coverage,
  DocumentRecord,
  Draft,
  DraftsResponse,
  ExportJob,
  MappingsResponse,
  ParseJob,
  PresignedGet,
  Project,
  ProjectPurgeResult,
  Section,
  UploadTarget,
} from "./types";
import { getIdToken } from "../contexts/AuthContext";

/** Resolves the current session's raw ID token, or null when signed out. */
export type TokenGetter = () => Promise<string | null>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Identify file bytes that cannot be a .docx; returns a human-readable
 * problem or null when the bytes look like a real Word document (zip magic).
 * Mirrors the backend's sniff in common/limits.py so both layers tell the
 * same story.
 */
export function sniffDocxProblem(head: Uint8Array): string | null {
  if (head.length === 0) return "file is empty";
  if (head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04) {
    return null; // zip magic — plausible .docx
  }
  const ole2 = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
  if (ole2.every((b, i) => head[i] === b)) {
    // Never send the user to Word here. These bytes were *classified* as a
    // Word 97-2003 container, and one mailed in under a .docx name is the
    // shape a hostile document takes; opening it locally hands it the macro
    // surface, network and credentials the converter sandbox exists to deny.
    // Re-uploading under the true .doc name routes it into that sandbox.
    return (
      "this is a legacy Word .doc under a .docx name — rename it to .doc and " +
      "upload it again so the server converts it in its sandbox. Do not open " +
      "it locally; if the .doc upload is refused too, this deployment cannot " +
      "convert it (user manual §4.1)"
    );
  }
  const text = new TextDecoder("utf-8", { fatal: false }).decode(head).trimStart();
  if (text.startsWith("<")) {
    return "this looks like an HTML page saved with a .docx extension, not a Word document";
  }
  if (text.startsWith("%PDF-")) {
    return "this is a PDF renamed to .docx, not a Word document";
  }
  return "this file is not a valid .docx document";
}

/**
 * Decide whether a chosen file may be uploaded, from its bytes and its name.
 *
 * OLE2 bytes are legitimate when the file is honestly named .doc — the backend
 * converts those, and is the single source of truth for whether conversion is
 * enabled in this environment. The same bytes under a .docx name are a renamed
 * file, which is a real user error worth naming here rather than shipping to a
 * server that will only reject it.
 */
export function sniffUploadProblem(head: Uint8Array, filename: string): string | null {
  if (head.length === 0) return "file is empty";
  if (filename.toLowerCase().endsWith(".doc")) {
    const ole2 = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
    if (ole2.every((b, i) => head[i] === b)) return null;
    if (head[0] === 0x50 && head[1] === 0x4b && head[2] === 0x03 && head[3] === 0x04) {
      return null; // a .docx misnamed .doc; the backend routes on bytes anyway
    }
  }
  return sniffDocxProblem(head);
}

/** First bytes of a file; FileReader fallback for environments (jsdom)
 *  whose Blob lacks arrayBuffer(). */
async function readFileHead(file: File, length = 512): Promise<Uint8Array> {
  const blob = file.slice(0, length);
  if (typeof blob.arrayBuffer === "function") {
    return new Uint8Array(await blob.arrayBuffer());
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error);
    reader.readAsArrayBuffer(blob);
  });
}

/** Join a base URL and a path without doubling or dropping slashes. */
export function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/, "");
  const p = path.replace(/^\/+/, "");
  return `${b}/${p}`;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly getToken: TokenGetter;

  constructor(
    baseUrl: string = import.meta.env.VITE_API_BASE_URL ?? "/api",
    getToken: TokenGetter = getIdToken,
  ) {
    this.baseUrl = baseUrl;
    this.getToken = getToken;
  }

  /** Authorization header for the current session (raw ID token, no "Bearer "
   *  prefix — see AuthContext.getIdToken), or {} when signed out. Spreadable
   *  into any fetch() call's headers without a conditional at the call site. */
  private async authHeaders(): Promise<Record<string, string>> {
    const token = await this.getToken();
    return token ? { Authorization: token } : {};
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers = await this.authHeaders();
    if (body) headers["Content-Type"] = "application/json";
    const res = await fetch(joinUrl(this.baseUrl, path), {
      method,
      headers,
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

  listProjects(): Promise<{ projects: Project[] }> {
    return this.request("GET", "/projects");
  }

  deleteProject(projectId: string, projectName: string): Promise<ProjectPurgeResult> {
    return this.request("DELETE", `/projects/${projectId}`, {
      confirm_project_name: projectName,
    });
  }

  listDocuments(projectId: string): Promise<{ documents: DocumentRecord[] }> {
    return this.request("GET", `/projects/${projectId}/documents`);
  }

  /**
   * Register a document, PUT its bytes straight to S3, and kick off parsing.
   * Parsing auto-chains into control mapping on the backend.
   *
   * The file's magic bytes and name are checked first: a renamed legacy .doc,
   * an HTML download page, or a PDF can never parse, so rejecting here gives an
   * immediate, specific message instead of a failed document row minutes
   * later — and the bad bytes never leave the browser.
   */
  async uploadDocument(
    projectId: string,
    file: File,
  ): Promise<{ document: DocumentRecord; job: ParseJob }> {
    const head = await readFileHead(file);
    const problem = sniffUploadProblem(head, file.name);
    if (problem) {
      throw new ApiError(400, problem);
    }
    const { document, upload } = await this.registerDocument(projectId, file.name);
    await this.uploadBytes(upload, file);
    const { job } = await this.startParse(projectId, document.document_id);
    return { document, job };
  }

  registerDocument(
    projectId: string,
    filename: string,
  ): Promise<{ document: DocumentRecord; upload: UploadTarget }> {
    return this.request("POST", `/projects/${projectId}/documents`, { filename });
  }

  /** Upload the file bytes directly to S3 using the presigned POST target.
   *  S3 requires every policy field before the file part, and the file last. */
  async uploadBytes(target: UploadTarget, file: Blob): Promise<void> {
    const form = new FormData();
    for (const [name, value] of Object.entries(target.fields)) {
      form.append(name, value);
    }
    form.append("file", file);
    const res = await fetch(target.url, {
      method: target.method,
      body: form,
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
      { headers: await this.authHeaders() },
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    return res.text();
  }

  // ---- Coverage dashboard + conversion matrix (M5) ----

  getCoverage(projectId: string, baseline?: string): Promise<Coverage> {
    const qs = baseline ? `?baseline=${encodeURIComponent(baseline)}` : "";
    return this.request("GET", `/projects/${projectId}/coverage${qs}`);
  }

  /** Fetch the conversion-matrix CSV as text (not JSON). */
  async getConversionMatrixCsv(projectId: string): Promise<string> {
    const res = await fetch(
      joinUrl(this.baseUrl, `/projects/${projectId}/conversion-matrix.csv`),
      { headers: await this.authHeaders() },
    );
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    return res.text();
  }

  /** Fetch the eMASS control-implementation CSV as text (not JSON). */
  async getEmassCsv(projectId: string): Promise<string> {
    const res = await fetch(joinUrl(this.baseUrl, `/projects/${projectId}/emass.csv`), {
      headers: await this.authHeaders(),
    });
    if (!res.ok) {
      throw new ApiError(res.status, res.statusText);
    }
    return res.text();
  }

  /** Fetch the OSCAL component-definition as pretty-printed JSON text. */
  async getOscalJson(projectId: string): Promise<string> {
    const res = await fetch(joinUrl(this.baseUrl, `/projects/${projectId}/oscal.json`), {
      headers: await this.authHeaders(),
    });
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
