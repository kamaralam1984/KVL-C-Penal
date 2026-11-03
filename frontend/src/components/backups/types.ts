export interface Rec {
  id: string; backup_type: string; resource_ref: string | null; status: string; verification_status: string;
  confirmed_recovery_point: boolean; verified_at: string | null; size_bytes: number | null; sha256: string | null;
  encrypted: boolean; key_id: string | null; entries: number | null; trigger: string; offsite_status: string;
  started_at: string | null; finished_at: string | null; error: string | null; created_at: string;
}
export interface Restore {
  id: string; backup_id: string; mode: string; status: string; location: string | null; error: string | null; created_at: string;
  requested_by: string | null; approved_by: string | null;
  impact: { target: string; will_overwrite_live_data: boolean; previous_data_kept: boolean; requires_approval: boolean; verified: boolean; notes: string[]; requires_typed_confirmation: string };
  result: Record<string, unknown> | null;
}
export interface Policy { id: string; tenant_id: string | null; name: string; keep_last: number; keep_daily: number; keep_weekly: number; keep_monthly: number; min_keep: number }
export interface Schedule { id: string; name: string; backup_type: string; resource_ref: string | null; cron: string; enabled: boolean; encrypt: boolean; retention_policy_id: string | null; last_run_at: string | null; next_run_at: string | null }
export interface Dest { id: string; name: string; kind: string; config: Record<string, unknown>; has_credentials: boolean; status: string; last_error: string | null; note: string | null }
export interface Overview {
  total: number; completed: number; failed_7d: number; running: number; verified: number; unverified: number; verification_problems: number;
  total_size_bytes: number; last_success_by_type: Record<string, string>; active_schedules: number; next_run_at: string | null;
  offsite: { configured: boolean; status: string }; recent: Rec[];
}
export interface PlanRow { id: string; created_at: string; size_bytes: number | null; verification_status: string; keep: boolean; reasons: string[] }
export interface Plan { dry_run: boolean; deleted: number; total_considered: number; would_keep: PlanRow[]; would_prune: PlanRow[]; would_free_bytes: number; prune_blocked_reason: string | null; recovery_point_id: string | null }
export const TYPES = [
  { value: "website_files", label: "Website files" }, { value: "database", label: "Customer database" },
  { value: "website_config", label: "Website configuration" }, { value: "saas_config", label: "SaaS configuration metadata" },
];
export const typeLabel = (t: string) => TYPES.find((x) => x.value === t)?.label ?? t;
export interface Ctx { tid: string | null; q: Record<string, string>; body: (b: object) => object; needTenant: boolean }
