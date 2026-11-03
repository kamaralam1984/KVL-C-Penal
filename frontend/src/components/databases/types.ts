export interface DbRow {
  id: string;
  name: string;
  label: string;
  engine: "mariadb" | "postgresql";
  status: string;
  message: string | null;
  host: string;
  port: number | null;
  size_bytes: number | null;
  size_checked_at: string | null;
  created_at: string;
  users?: DbUser[];
  verified_backup_at?: string | null;
}
export interface DbUser {
  id: string;
  username: string;
  privilege: "readwrite" | "readonly";
  status: string;
  grants: string;
  password_rotated_at: string | null;
}
export interface Credentials {
  username: string;
  password: string;
  host: string;
  port: number;
  database: string;
  shown_once: boolean;
}
export interface BackupReq {
  id: string;
  kind: "backup" | "restore";
  state: string;
  artifact: string | null;
  source_request_id: string | null;
  checksum: string | null;
  size_bytes: number | null;
  verified: boolean;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}
export interface EnginesResp {
  state: "ok" | "not_reported" | "not_configured";
  message: string | null;
  available: string[];
  supported: string[];
}
export const ENGINE_LABEL: Record<string, string> = { mariadb: "MariaDB / MySQL", postgresql: "PostgreSQL" };
export const dbBusy = (d: DbRow) =>
  ["provisioning", "deleting"].includes(d.status) || !!d.users?.some((u) => ["pending", "rotating", "dropping"].includes(u.status));
