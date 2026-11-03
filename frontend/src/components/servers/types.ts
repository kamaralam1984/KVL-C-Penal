export type Availability = "connected" | "stale" | "disconnected" | "unknown" | "disabled";

export interface ServerRow {
  id: string;
  name: string;
  hostname: string | null;
  status: string;
  availability: Availability;
  agent_version: string | null;
  os_info: Record<string, unknown> | null;
  labels: Record<string, string> | null;
  last_heartbeat_at: string | null;
  registered_at: string | null;
  created_at: string;
  has_active_credential: boolean;
  credential_key_id: string | null;
}

export interface Enrollment {
  token: string;
  command: string;
  expires_in_hours: number;
}

export interface JobRow {
  id: string;
  server_id: string;
  operation: string;
  status: string;
  attempts: number;
  timeout_seconds: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  requested_by: string | null;
  params?: Record<string, unknown> | null;
  result?: Record<string, unknown> | null;
  error?: string | null;
  correlation_id?: string | null;
}

export interface CredentialRow {
  id: string;
  key_id: string;
  status: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
