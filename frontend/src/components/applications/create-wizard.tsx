"use client";

import * as React from "react";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useApi } from "@/hooks/use-api";
import { ApiError, post } from "@/lib/http";
import { useToast } from "@/components/ui/toast";
import { DetailList } from "@/components/resource/helpers";
import { RUNTIME_LABEL, catalogKey, type AppRow, type RuntimeCatalog } from "./types";

const STEPS = ["Runtime", "Source", "Review"];

export function CreateAppWizard({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (o: boolean) => void; onCreated: (a: AppRow) => void }) {
  const toast = useToast();
  const cat = useApi<RuntimeCatalog>(open ? "/apps/runtimes" : null);
  const [step, setStep] = React.useState(0);
  const [f, setF] = React.useState({ name: "", runtime: "nodejs", version: "", repo_url: "", branch: "main", entrypoint: "", health_path: "/" });
  const [pending, setPending] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();

  React.useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStep(0);
      setErr(undefined);
      setF({ name: "", runtime: "nodejs", version: "", repo_url: "", branch: "main", entrypoint: "", health_path: "/" });
    }
  }, [open]);

  const key = catalogKey(f.runtime);
  const reported = cat.data?.state === "ok";
  const versions = (reported ? cat.data?.installed[key] : cat.data?.supported[key]) ?? [];
  const isProc = f.runtime === "nodejs" || f.runtime === "python";
  const set = (k: keyof typeof f, v: string) => setF((s) => ({ ...s, [k]: v, ...(k === "runtime" ? { version: "", entrypoint: "" } : {}) }));
  const fe = (n: string) => err?.fields?.find((x) => x.field === n)?.message;

  const step0ok = f.name.trim().length >= 2 && !!f.version;
  const step1ok = f.runtime === "wordpress" || !!f.repo_url;

  async function submit() {
    setPending(true);
    setErr(undefined);
    try {
      const body: Record<string, string> = { name: f.name.trim(), runtime: f.runtime, runtime_version: f.version, branch: f.branch, health_path: f.health_path };
      if (f.repo_url) body.repo_url = f.repo_url;
      if (f.entrypoint) body.entrypoint = f.entrypoint;
      const a = await post<AppRow>("/apps", body);
      toast({ title: "Application created", tone: "success" });
      onOpenChange(false);
      onCreated(a);
    } catch (e) {
      setErr(e instanceof ApiError ? e : new ApiError(0, "network", "Network error"));
      if (e instanceof ApiError && e.code.startsWith("invalid_")) setStep(1);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="New application"
        description={`Step ${step + 1} of ${STEPS.length}: ${STEPS[step]}`}
        footer={
          <>
            {step > 0 && (
              <Button variant="outline" onClick={() => setStep(step - 1)}>
                Back
              </Button>
            )}
            {step < 2 ? (
              <Button disabled={step === 0 ? !step0ok : !step1ok} onClick={() => setStep(step + 1)}>
                Next
              </Button>
            ) : (
              <Button loading={pending} onClick={submit}>
                Create application
              </Button>
            )}
          </>
        }
      >
        <div className="space-y-4">
          {step === 0 && (
            <>
              <Field label="Application name" required>
                {(p) => <Input {...p} value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="My storefront" maxLength={120} />}
              </Field>
              <Field label="Runtime" required>
                {(p) => (
                  <Select {...p} value={f.runtime} onChange={(e) => set("runtime", e.target.value)}>
                    {Object.entries(RUNTIME_LABEL).map(([k, l]) => (
                      <option key={k} value={k}>
                        {l}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
              {cat.loading ? (
                <Skeleton className="h-10 w-full" />
              ) : (
                <>
                  {cat.error && <p className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">Could not load the server runtime inventory.</p>}
                  {cat.data && !reported && (
                    <p role="status" className="rounded-md bg-warning/14 px-3 py-2 text-sm text-warning">
                      {cat.data.state === "not_configured" ? "No server is registered yet. " : "The server has not reported its installed runtimes. "}
                      {cat.data.message} The list below shows supported versions; the server verifies the version at deployment.
                    </p>
                  )}
                  <Field label="Version" required hint={reported ? "Only versions installed on the server are offered." : undefined} error={fe("runtime_version")}>
                    {(p) => (
                      <Select {...p} value={f.version} onChange={(e) => set("version", e.target.value)}>
                        <option value="">Select a version…</option>
                        {versions.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </Select>
                    )}
                  </Field>
                  {reported && versions.length === 0 && <p className="text-sm text-danger">This runtime is not installed on the server, so it cannot be used.</p>}
                </>
              )}
            </>
          )}
          {step === 1 && (
            <>
              <Field label="Git repository URL" required={f.runtime !== "wordpress"} hint={f.runtime === "wordpress" ? "Leave empty to use the official WordPress release." : "https://github.com/owner/repo.git (GitHub, GitLab, Bitbucket, Codeberg)"} error={fe("repo_url")}>
                {(p) => <Input {...p} value={f.repo_url} onChange={(e) => set("repo_url", e.target.value)} placeholder="https://github.com/acme/site.git" inputMode="url" />}
              </Field>
              <Field label="Branch" error={fe("branch")}>
                {(p) => <Input {...p} value={f.branch} onChange={(e) => set("branch", e.target.value)} />}
              </Field>
              {isProc && (
                <Field label={f.runtime === "nodejs" ? "Entry script" : "Entrypoint (module:callable)"} required={f.runtime === "python"} hint={f.runtime === "nodejs" ? "Default server.js" : "e.g. app:app"}>
                  {(p) => <Input {...p} value={f.entrypoint} onChange={(e) => set("entrypoint", e.target.value)} placeholder={f.runtime === "nodejs" ? "server.js" : "app:app"} />}
                </Field>
              )}
              <Field label="Health check path" hint="Must return 2xx/3xx before traffic is switched.">
                {(p) => <Input {...p} value={f.health_path} onChange={(e) => set("health_path", e.target.value)} />}
              </Field>
            </>
          )}
          {step === 2 && (
            <>
              <DetailList
                items={[
                  ["Name", f.name],
                  ["Runtime", `${RUNTIME_LABEL[f.runtime]} ${f.version}`],
                  ["Repository", f.repo_url || "WordPress official release"],
                  ["Branch", f.branch],
                  ["Entrypoint", f.entrypoint || "—"],
                  ["Health check", f.health_path],
                ]}
              />
              {err && (
                <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
                  {err.message}
                </p>
              )}
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
