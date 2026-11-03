"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Select } from "@/components/ui/input";
import { ApiError } from "@/lib/http";
import { useToast } from "@/components/ui/toast";

export interface FieldDef {
  name: string;
  label: string;
  type?: "text" | "email" | "password" | "number" | "select" | "textarea" | "checkbox";
  required?: boolean;
  hint?: string;
  placeholder?: string;
  options?: { value: string; label: string }[];
  defaultValue?: string | number | boolean;
  /** Hide the field unless this returns true for the current values. */
  showIf?: (values: Record<string, string | number | boolean>) => boolean;
}

type Values = Record<string, string | number | boolean>;

/**
 * Generic create/edit dialog. Submits via `onSubmit`; ApiError field messages are shown inline,
 * other errors in a banner. Closes and toasts on success.
 */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  fields,
  submitLabel = "Save",
  onSubmit,
  successMessage = "Saved",
  destructive,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description?: string;
  fields: FieldDef[];
  submitLabel?: string;
  onSubmit: (values: Values) => Promise<unknown>;
  successMessage?: string;
  destructive?: boolean;
}) {
  const toast = useToast();
  const init = React.useMemo<Values>(() => Object.fromEntries(fields.map((f) => [f.name, f.defaultValue ?? (f.type === "checkbox" ? false : "")])), [fields]);
  const [values, setValues] = React.useState<Values>(init);
  const [pending, setPending] = React.useState(false);
  const [err, setErr] = React.useState<ApiError>();

  React.useEffect(() => {
    if (open) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setValues(init);
      setErr(undefined);
    }
  }, [open, init]);

  const fieldErr = (name: string) => err?.fields?.find((f) => f.field === name || f.field.endsWith(`.${name}`))?.message;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setErr(undefined);
    try {
      const clean: Values = {};
      for (const f of fields) {
        if (f.showIf && !f.showIf(values)) continue;
        const v = values[f.name];
        if (v === "" && !f.required) continue;
        clean[f.name] = f.type === "number" && v !== "" ? Number(v) : v;
      }
      await onSubmit(clean);
      toast({ title: successMessage, tone: "success" });
      onOpenChange(false);
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2 : new ApiError(0, "network", "Network error"));
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={title} description={description}>
        <form onSubmit={submit} className="space-y-4" noValidate>
          {fields.map((f) => {
            if (f.showIf && !f.showIf(values)) return null;
            const set = (v: string | number | boolean) => setValues((s) => ({ ...s, [f.name]: v }));
            if (f.type === "checkbox")
              return (
                <label key={f.name} className="flex items-center gap-2 text-sm text-text">
                  <input type="checkbox" className="h-4 w-4 accent-[var(--primary)]" checked={Boolean(values[f.name])} onChange={(e) => set(e.target.checked)} />
                  {f.label}
                </label>
              );
            return (
              <Field key={f.name} label={f.label} required={f.required} hint={f.hint} error={fieldErr(f.name)}>
                {(p) =>
                  f.type === "select" ? (
                    <Select {...p} value={String(values[f.name])} onChange={(e) => set(e.target.value)}>
                      {!f.required && <option value="">—</option>}
                      {f.options?.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </Select>
                  ) : f.type === "textarea" ? (
                    <textarea
                      {...p}
                      rows={4}
                      value={String(values[f.name])}
                      placeholder={f.placeholder}
                      onChange={(e) => set(e.target.value)}
                      className="w-full rounded-md border border-border-strong bg-surface px-3 py-2 text-base text-text placeholder:text-faint focus-visible:border-primary"
                    />
                  ) : (
                    <Input
                      {...p}
                      type={f.type ?? "text"}
                      value={String(values[f.name])}
                      placeholder={f.placeholder}
                      autoComplete={f.type === "password" ? "new-password" : "off"}
                      onChange={(e) => set(e.target.value)}
                    />
                  )
                }
              </Field>
            );
          })}
          {err && !err.fields?.length && (
            <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">
              {err.message}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" variant={destructive ? "danger" : "primary"} loading={pending}>
              {submitLabel}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
