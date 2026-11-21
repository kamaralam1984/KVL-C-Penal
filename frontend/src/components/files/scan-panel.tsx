"use client";

import * as React from "react";
import { ShieldAlert, ShieldCheck, ShieldQuestion, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardHeader } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { ApiError, api } from "@/lib/http";
import { type Job, runOp, waitJob } from "./api";

interface Finding { path: string; severity: "high" | "medium" | "low"; rule: string; line: number | null }
interface ScanResult { verdict: "clean" | "review" | "suspicious"; files_scanned: number; files_skipped: number; truncated: boolean; clamav: string; counts: Record<string, number>; findings: Finding[] }

const RULE_TEXT: Record<string, string> = {
  eval_decode_chain: "Runs hidden (encoded) code",
  exec_request_input: "Runs system commands from a web request",
  eval_request_input: "Runs code sent in a web request",
  preg_replace_eval: "Old code-execution trick (preg_replace /e)",
  known_webshell: "Matches a known web shell",
  variable_function_input: "Calls a function chosen by the visitor",
  long_base64_blob: "Very long encoded blob",
  hex_escaped_code: "Heavily hex-encoded code",
  chr_chain: "Obfuscated character chain",
  file_put_remote: "Downloads remote code into a file",
  htaccess_php_handler: "Makes images run as PHP",
  error_suppressed_include: "Hidden include of a non-PHP file",
  php_in_non_php_file: "PHP code hidden inside an image/text file",
  php_in_uploads: "PHP file in the uploads folder",
  suspicious_file_name: "Suspicious file name",
};
const TONE = { high: "text-danger", medium: "text-warning", low: "text-muted" } as const;
const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");

/** Read-only malware scan; "Move to trash" uses the normal recoverable delete. */
export function ScanPanel({ site, scope, canDelete, onChanged }: { site: string; scope: string; canDelete: boolean; onChanged: () => void }) {
  const toast = useToast();
  const [res, setRes] = React.useState<ScanResult | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const [del, setDel] = React.useState<Finding | null>(null);

  async function scan() {
    setBusy(true);
    setErr(undefined);
    try {
      const q = scope ? `?tenant_id=${encodeURIComponent(scope)}` : "";
      const j = await api<Job>(`/files/${encodeURIComponent(site)}/scan${q}`, { method: "POST", body: {} });
      const done = await waitJob<ScanResult>(j.id, scope, undefined, 20 * 60 * 1000);
      setRes(done.result as unknown as ScanResult);
    } catch (e) {
      setErr(msg(e));
    } finally {
      setBusy(false);
    }
  }

  const Icon = !res ? ShieldQuestion : res.verdict === "clean" ? ShieldCheck : ShieldAlert;
  return (
    <Card>
      <CardHeader title="Security scan" description="Looks for web shells, backdoors and hidden code in this site's files. Read-only: files are only moved when you choose to." actions={<Button size="sm" loading={busy} onClick={() => void scan()}>{res ? "Scan again" : "Start scan"}</Button>} />
      <div className="space-y-3 px-5 pb-5">
        {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err}</p>}
        {busy && <p role="status" className="text-sm text-muted">Scanning… large sites can take a few minutes.</p>}
        {res && (
          <>
            <div className={`flex items-center gap-3 rounded-lg border p-3 ${res.verdict === "clean" ? "border-success/40 bg-success/5" : res.verdict === "review" ? "border-warning/40 bg-warning/5" : "border-danger/40 bg-danger/5"}`}>
              <Icon className={`h-6 w-6 ${res.verdict === "clean" ? "text-success" : res.verdict === "review" ? "text-warning" : "text-danger"}`} aria-hidden />
              <div className="text-sm">
                <p className="font-semibold text-text">{res.verdict === "clean" ? "No threats found" : res.verdict === "review" ? "Some files need a look" : "Suspicious files found"}</p>
                <p className="text-xs text-muted">{res.files_scanned.toLocaleString()} files scanned · high {res.counts.high ?? 0} · medium {res.counts.medium ?? 0} · ClamAV: {res.clamav === "ok" ? "used" : res.clamav.replace("_", " ")}{res.truncated ? " · stopped early (limit reached)" : ""}</p>
              </div>
            </div>
            {!!res.findings.length && (
              <ul className="divide-y divide-border rounded-md border border-border">
                {res.findings.map((f, i) => (
                  <li key={`${f.path}-${f.rule}-${i}`} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                    <div className="min-w-0">
                      <p className="truncate font-mono text-xs text-text" title={f.path}>{f.path}{f.line ? `:${f.line}` : ""}</p>
                      <p className={`text-xs ${TONE[f.severity]}`}>{f.severity.toUpperCase()} · {RULE_TEXT[f.rule] ?? f.rule}</p>
                    </div>
                    {canDelete && <Button size="sm" variant="ghost" onClick={() => setDel(f)}><Trash2 className="h-4 w-4" /><span className="sr-only">Move {f.path} to trash</span></Button>}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-xs text-muted">Findings are warnings, not proof: review a file before removing it. Removed files go to Trash and can be restored.</p>
          </>
        )}
      </div>
      <ConfirmDialog open={!!del} onOpenChange={(o) => !o && setDel(null)} destructive title="Move file to trash?" description={`“${del?.path}” will be moved to the trash. You can restore it later.`} confirmLabel="Move to trash" onConfirm={async () => {
        if (!del) return;
        try {
          await runOp(site, scope, "delete", { path: del.path, confirm: true });
          setRes((r) => r && { ...r, findings: r.findings.filter((x) => x.path !== del.path) });
          toast({ title: "Moved to trash", tone: "success" });
          onChanged();
        } catch (e) {
          toast({ title: "Could not move file", description: msg(e), tone: "danger" });
        }
      }} />
    </Card>
  );
}
