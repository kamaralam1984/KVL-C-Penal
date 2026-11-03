export interface AppRow {
  id: string;
  name: string;
  slug: string;
  runtime: "php" | "wordpress" | "nodejs" | "python";
  runtime_version: string;
  repo_url: string | null;
  branch: string;
  entrypoint: string | null;
  health_path: string;
  port: number | null;
  status: string;
  provision_state: string;
  provision_message: string | null;
  current_release: string | null;
  previous_release: string | null;
  controllable: boolean;
  created_at: string;
  usage?: Record<string, number | null> | null;
  last_job?: { id: string; operation: string; status: string; error: string | null } | null;
  env_count?: number;
}

export interface RuntimeCatalog {
  state: "ok" | "not_reported" | "not_configured";
  message: string | null;
  installed: Record<string, string[]>;
  supported: Record<string, string[]>;
}

export interface Deployment {
  id: string;
  release_id: string;
  kind: "deploy" | "rollback";
  branch: string | null;
  commit: string | null;
  status: string;
  is_live: boolean;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface QuotaRow {
  resource: string;
  limit: number;
  used: number;
}

export const RUNTIME_LABEL: Record<string, string> = { php: "PHP (PHP-FPM)", wordpress: "WordPress (PHP)", nodejs: "Node.js (PM2)", python: "Python (venv)" };
/** wordpress shares the php catalog */
export const catalogKey = (runtime: string) => (runtime === "wordpress" ? "php" : runtime);
