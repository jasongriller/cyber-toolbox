// Shared shapes mirroring backend/src/rmf_migrator/common/models.py.

export type Baseline =
  | "dod_cnssi_1253"
  | "fedramp"
  | "fips199_low"
  | "fips199_moderate"
  | "fips199_high"
  | "generic_800_53";

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export type DocumentStatus =
  | "uploaded"
  | "parsing"
  | "parsed"
  | "mapping"
  | "mapped"
  | "mapping_approved"
  | "drafting"
  | "drafted"
  | "failed";

export type MappingStatus = "proposed" | "edited" | "approved";

export type DraftStatus = "proposed" | "edited" | "approved";

export interface Project {
  project_id: string;
  name: string;
  baseline: Baseline;
  created_at: string;
  created_by: string;
  document_count: number;
}

export interface DocumentRecord {
  document_id: string;
  project_id: string;
  filename: string;
  s3_key: string;
  status: DocumentStatus;
  uploaded_at: string;
  uploaded_by: string;
  section_count: number;
  parse_error: string | null;
}

export interface UploadTarget {
  url: string;
  method: "PUT";
  headers: Record<string, string>;
  expires_in: number;
}

export interface ParseJob {
  job_id: string;
  project_id: string;
  document_id: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  error_type: string | null;
}

export interface Section {
  section_id: string;
  document_id: string;
  project_id: string;
  order: number;
  level: number;
  heading: string;
  parent_id: string | null;
  text: string;
  char_length: number;
}

export interface ControlMapping {
  mapping_id: string;
  project_id: string;
  document_id: string;
  section_id: string;
  order: number;
  proposed_control_ids: string[];
  confidence: number;
  rationale: string;
  final_control_ids: string[] | null;
  status: MappingStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface MappingsResponse {
  document_status: DocumentStatus;
  mappings: ControlMapping[];
}

export interface ApproveResponse {
  document_status: DocumentStatus;
  approved_count: number;
  draft_job_id?: string;
}

export interface DispositionNote {
  rev4_id: string;
  rev5_ids: string[];
  relationship: string;
}

export interface Draft {
  draft_id: string;
  project_id: string;
  document_id: string;
  section_id: string;
  order: number;
  rev4_control_ids: string[];
  rev5_control_ids: string[];
  dispositions: DispositionNote[];
  draft_text: string;
  suggestions: string[];
  edited_text: string | null;
  status: DraftStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface DraftsResponse {
  document_status: DocumentStatus;
  drafts: Draft[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}
