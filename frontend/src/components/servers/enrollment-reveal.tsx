"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import type { Enrollment } from "./types";

export function CopyField({ label, value }: { label: string; value: string }) {
  const [done, setDone] = React.useState(false);
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium text-text">{label}</p>
      <div className="flex items-start gap-2">
        <code className="min-w-0 flex-1 rounded-md border border-border bg-subtle px-3 py-2 text-xs break-all text-text">{value}</code>
        <Button
          type="button"
          size="sm"
          variant="outline"
          aria-label={`Copy ${label}`}
          onClick={() => {
            void navigator.clipboard?.writeText(value).then(() => {
              setDone(true);
              setTimeout(() => setDone(false), 2000);
            });
          }}
        >
          {done ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
      </div>
    </div>
  );
}

/** One-time display of an enrollment token or rotated credential. Nothing here is persisted client-side. */
export function SecretReveal({
  open,
  onClose,
  title,
  description,
  fields,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description: string;
  fields: { label: string; value: string }[];
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        title={title}
        description={description}
        footer={
          <DialogClose asChild>
            <Button>I have saved it</Button>
          </DialogClose>
        }
      >
        <div className="space-y-4">
          <p role="alert" className="rounded-md bg-warning/14 px-3 py-2 text-sm text-warning">
            This is shown once. It cannot be retrieved later; if lost, issue a new one.
          </p>
          {fields.map((f) => (
            <CopyField key={f.label} {...f} />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export const enrollmentFields = (e: Enrollment) => [
  { label: "Enrollment token", value: e.token },
  { label: `Run on the server (valid ${e.expires_in_hours}h, single use)`, value: e.command },
];
