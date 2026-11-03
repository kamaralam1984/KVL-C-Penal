export interface Cert {
  id: string;
  domain: string;
  status: string;
  environment: string;
  trusted: boolean;
  issuer: string | null;
  not_after: string | null;
  days_remaining: number | null;
  severity: "ok" | "notice" | "warning" | "critical";
  serial: string | null;
  fingerprint_sha256: string | null;
  sans: string[];
  auto_renew: boolean;
  last_error: string | null;
  last_attempt_at: string | null;
  renewal_failures: number;
  last_renewed_at: string | null;
  tenant_id: string;
}
export interface CertDetail extends Cert {
  history: { action: string; at: string; detail: Record<string, unknown> | null }[];
}
export interface Overview {
  counts: Record<string, number>;
  total: number;
  expiring_7d: number;
  expiring_14d: number;
  expiring_30d: number;
  staging_only: number;
  certificates: Cert[];
}
export interface DomainRow {
  domain: string;
  tenant_id: string;
  certificate: Cert | null;
}
export interface Prereq {
  domain: string;
  ok: boolean;
  checks: { check: string; ok: boolean; message: string }[];
}
export interface DnsRec {
  id: string;
  type: string;
  name: string;
  content: string;
  ttl: number;
  priority: number | null;
  proxied: boolean | null;
}
export interface RecordsResp {
  domain: string;
  status: string;
  zone: { id: string; name: string } | null;
  records: DnsRec[];
  synced_at: string | null;
}
export interface Change {
  id: string;
  domain: string;
  action: "create" | "update" | "delete";
  before: Omit<DnsRec, "id"> | null;
  after: Omit<DnsRec, "id"> | null;
  diff: { field: string; before: unknown; after: unknown }[];
  warnings: string[];
  status: string;
  production: boolean;
  rollback_instructions: string | null;
  verification: string;
  verification_detail: Record<string, unknown> | null;
  error: string | null;
  applied_at: string | null;
  created_at: string;
  unchanged_record_count: number | null;
}
export interface Connection {
  status: string;
  configured: boolean;
  label?: string | null;
  fingerprint?: string;
  zones: { id: string; name: string; status: string | null }[];
  last_checked_at?: string | null;
  last_error?: string | null;
  required_permissions: string[];
}
export interface AuditItem {
  id: string;
  action: string;
  at: string;
  detail: Record<string, unknown> | null;
}
