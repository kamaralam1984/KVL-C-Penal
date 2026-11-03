"use client";

import * as React from "react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { DetailList } from "@/components/resource/helpers";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";
import { useToast } from "@/components/ui/toast";
import { ENGINE_LABEL, type Credentials, type DbRow, type EnginesResp } from "./types";

type Created = DbRow & { credentials: Credentials | null };
const STEPS = ["Engine", "Name", "Review"];

export function CreateDbWizard({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (d: Created) => void }) {
  const toast = useToast();
  const eng = useApi<EnginesResp>(open ? "/databases/engines" : null);
  const [step, setStep] = React.useState(0);
  const [engine, setEngine] = React.useState("");
  const [name, setName] = React.useState("");
  const [pending, setPending] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();

  React.useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStep(0);
      setEngine("");
      setName("");
      setErr(undefined);
    }
  }, [open]);

  const reported = eng.data?.state === "ok";
  const options = reported ? eng.data!.available : (eng.data?.supported ?? []);
  const nameOk = /^[a-z][a-z0-9]{1,13}$/.test(name);

  async function submit() {
    setPending(true);
    setErr(undefined);
    try {
      const d = await post<Created>("/databases", { engine, name });
      toast({ title: d.status === "pending_server" ? "Database recorded (waiting for a server)" : "Database provisioning started", tone: "success" });
      onOpenChange(false);
      onCreated(d);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Create database"
        description={`Step ${step + 1} of ${STEPS.length}: ${STEPS[step]}`}
        footer={
          <>
            {step > 0 && <Button variant="outline" onClick={() => setStep(step - 1)}>Back</Button>}
            {step < 2 ? (
              <Button disabled={step === 0 ? !engine : !nameOk} onClick={() => setStep(step + 1)}>Next</Button>
            ) : (
              <Button loading={pending} onClick={submit}>Create database</Button>
            )}
          </>
        }
      >
        <div className="space-y-4">
          {step === 0 && (
            eng.loading ? <Skeleton className="h-20 w-full" /> : (
              <>
                {eng.data && !reported && (
                  <p role="status" className="rounded-md bg-warning/14 px-3 py-2 text-sm text-warning">
                    {eng.data.state === "not_configured" ? "No server is registered yet." : "The server has not reported which database engines are installed."} The record will be created and provisioned when a server is available; your choice is verified then.
                  </p>
                )}
                {reported && options.length === 0 && <p className="text-sm text-danger">No database engine is available on the server.</p>}
                <div role="radiogroup" aria-label="Database engine" className="grid gap-2">
                  {options.map((e) => (
                    <button key={e} type="button" role="radio" aria-checked={engine === e} onClick={() => setEngine(e)} className={`min-h-11 rounded-lg border px-4 py-3 text-left text-sm font-medium transition-colors ${engine === e ? "border-primary bg-primary-soft text-text" : "border-border bg-surface text-text hover:bg-subtle"}`}>
                      {ENGINE_LABEL[e] ?? e}
                    </button>
                  ))}
                </div>
              </>
            )
          )}
          {step === 1 && (
            <Field label="Database name" required hint="2-14 characters: lowercase letters and digits, starting with a letter. A unique suffix is added automatically." error={err?.fields?.find((x) => x.field === "name")?.message}>
              {(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} maxLength={14} placeholder="shop" />}
            </Field>
          )}
          {step === 2 && (
            <>
              <DetailList items={[["Engine", ENGINE_LABEL[engine]], ["Name", `${name}_xxxxxx`], ["Primary user", "Read/write on this database only"], ["Access", "Local to the server (no remote access)"]]} />
              <p className="text-xs text-muted">A password is generated and shown once when provisioning starts.</p>
              {err && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{err.message}</p>}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
