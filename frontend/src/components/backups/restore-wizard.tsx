"use client";

import * as React from "react";
import { AlertTriangle } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusPill, DetailList, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { ApiError, get, post } from "@/lib/http";
import { useToast } from "@/components/ui/toast";
import { typeLabel, type Ctx, type Rec, type Restore } from "./types";

const STEPS = ["Source", "Target", "Impact", "Confirm"];

export function RestoreWizard({ rec, ctx, onClose, onVerify }: { rec: Rec | null; ctx: Ctx; onClose: () => void; onVerify: (r: Rec) => void }) {
  const toast = useToast();
  const [step, setStep] = React.useState(0);
  const [mode, setMode] = React.useState<"isolated" | "overwrite">("isolated");
  const [rr, setRr] = React.useState<Restore>();
  const [typed, setTyped] = React.useState("");
  const [ow, setOw] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();

  React.useEffect(() => {
    if (rec) {
      /* eslint-disable react-hooks/set-state-in-effect */
      setStep(0); setMode("isolated"); setRr(undefined); setTyped(""); setOw(false); setErr(undefined);
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [rec]);

  // poll progress after submit
  React.useEffect(() => {
    if (!rr || !["running", "pending_approval"].includes(rr.status) || step < 4) return;
    const id = setInterval(() => get<Restore>(`/backups/restores/${rr.id}`, ctx.q).then(setRr).catch(() => {}), 4000);
    return () => clearInterval(id);
  }, [rr, step, ctx.q]);

  if (!rec) return null;
  const call = async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true); setErr(undefined);
    try { return await fn(); } catch (e) { setErr(e instanceof ApiError ? e : new ApiError(0, "network", "Network error")); } finally { setBusy(false); }
  };
  const preview = async () => {
    const r = await call(() => post<Restore>(`/backups/${rec.id}/restore-preview`, ctx.body({ mode })));
    if (r) { setRr(r); setStep(2); }
  };
  const submit = async () => {
    if (!rr) return;
    const r = await call(() => post<Restore>(`/backups/restores/${rr.id}/submit`, { confirm: true, confirm_overwrite: ow, confirmation: typed }));
    if (r) { setRr(r); setStep(4); toast({ title: r.status === "pending_approval" ? "Sent for approval" : "Restore started", tone: "success" }); }
  };
  const phrase = rr?.impact.requires_typed_confirmation ?? "";
  const canSubmit = typed === phrase && (mode === "isolated" || ow);

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent title="Restore wizard" description={`${typeLabel(rec.backup_type)}${rec.resource_ref ? ` · ${rec.resource_ref}` : ""}`} className="!w-[min(100vw,30rem)]">
        <div className="space-y-4 p-5">
          <ol className="flex gap-1 text-xs" aria-label="Steps">
            {STEPS.map((s, i) => (
              <li key={s} className={`flex-1 rounded px-2 py-1 text-center font-medium ${i === Math.min(step, 3) ? "bg-primary-soft text-primary" : "bg-subtle text-muted"}`}>{i + 1}. {s}</li>
            ))}
          </ol>
          {err && (
            <div role="alert" className="space-y-2 rounded-md bg-danger/10 p-3 text-sm text-danger">
              <p>{err.message}</p>
              {err.code === "backup_unverified" && <Button size="sm" variant="outline" onClick={() => onVerify(rec)}>Verify this backup first</Button>}
              {err.code === "overwrite_not_supported" && <Button size="sm" variant="outline" onClick={() => { setMode("isolated"); setStep(1); setErr(undefined); }}>Use isolated restore</Button>}
            </div>
          )}
          {step === 0 && (
            <>
              <DetailList items={[["Type", typeLabel(rec.backup_type)], ["Resource", rec.resource_ref], ["Created", fmtDate(rec.created_at)], ["Size", fmtBytes(rec.size_bytes)], ["Verification", <StatusPill key="v" status={rec.verification_status} />], ["Encrypted", rec.encrypted ? "Yes" : "No"]]} />
              <Button className="min-h-11 w-full" onClick={() => setStep(1)}>Continue</Button>
            </>
          )}
          {step === 1 && (
            <>
              <fieldset className="space-y-2">
                <legend className="text-sm font-medium text-text">Restore target</legend>
                <label className="flex min-h-11 items-start gap-2 rounded-md border border-border p-3 text-sm">
                  <input type="radio" name="mode" className="mt-1" checked={mode === "isolated"} onChange={() => setMode("isolated")} />
                  <span><strong>Isolated location (recommended)</strong><br /><span className="text-muted">Extracted to a separate folder. Live data is not touched.</span></span>
                </label>
                {rec.backup_type === "website_files" && (
                  <label className="flex min-h-11 items-start gap-2 rounded-md border border-warning/50 p-3 text-sm">
                    <input type="radio" name="mode" className="mt-1" checked={mode === "overwrite"} onChange={() => setMode("overwrite")} />
                    <span><strong>Overwrite live site</strong><br /><span className="text-muted">Replaces the live files. Needs a verified backup and approval by a second person. Old files are moved aside, not deleted.</span></span>
                  </label>
                )}
              </fieldset>
              {mode === "overwrite" && <p className="flex gap-2 rounded-md bg-warning/14 p-3 text-sm text-warning"><AlertTriangle className="h-4 w-4 shrink-0" />Overwriting affects your live website.</p>}
              <div className="flex gap-2"><Button variant="outline" className="min-h-11" onClick={() => setStep(0)}>Back</Button><Button className="min-h-11 flex-1" loading={busy} onClick={preview}>Preview impact</Button></div>
            </>
          )}
          {step === 2 && rr && (
            <>
              <DetailList items={[["Target", rr.impact.target], ["Overwrites live data", rr.impact.will_overwrite_live_data ? "Yes" : "No"], ["Previous data kept", rr.impact.previous_data_kept ? "Yes" : "n/a"], ["Needs approval", rr.impact.requires_approval ? "Yes" : "No"], ["Backup verified", rr.impact.verified ? "Yes" : "Not yet"]]} />
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted">{rr.impact.notes.map((n) => <li key={n}>{n}</li>)}</ul>
              <div className="flex gap-2"><Button variant="outline" className="min-h-11" onClick={() => setStep(1)}>Back</Button><Button className="min-h-11 flex-1" onClick={() => setStep(3)}>Continue</Button></div>
            </>
          )}
          {step === 3 && rr && (
            <>
              <p className="text-sm text-muted">Type <code className="rounded bg-subtle px-1 font-mono text-text">{phrase}</code> to confirm.</p>
              <Input aria-label="Confirmation phrase" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
              {mode === "overwrite" && (
                <label className="flex min-h-11 items-center gap-2 text-sm text-text"><input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={ow} onChange={(e) => setOw(e.target.checked)} />I understand this overwrites the live site.</label>
              )}
              <div className="flex gap-2"><Button variant="outline" className="min-h-11" onClick={() => setStep(2)}>Back</Button><Button variant={mode === "overwrite" ? "danger" : "primary"} className="min-h-11 flex-1" disabled={!canSubmit} loading={busy} onClick={submit}>{mode === "overwrite" ? "Request overwrite restore" : "Start restore"}</Button></div>
            </>
          )}
          {step === 4 && rr && (
            <div className="space-y-3" role="status">
              <p className="flex items-center gap-2 text-sm">Status: <StatusPill status={rr.status} /></p>
              {rr.status === "pending_approval" && <p className="text-sm text-muted">Waiting for a different person with backup management permission to approve.</p>}
              {rr.status === "running" && <p className="text-sm text-muted">Restoring… this page updates automatically.</p>}
              {rr.status === "completed" && <p className="text-sm text-success">Restore complete{rr.location ? ` at ${rr.location}` : ""}.</p>}
              {rr.status === "failed" && <p className="rounded-md bg-danger/10 p-3 text-sm text-danger">{rr.error ?? "Restore failed."}</p>}
              <Button variant="outline" className="min-h-11 w-full" onClick={onClose}>Close</Button>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
