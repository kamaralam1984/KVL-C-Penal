"use client";

import * as React from "react";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { CopyField } from "@/components/servers/enrollment-reveal";
import type { Credentials } from "./types";

/** One-time credential display. The password lives only in this component's props and is dropped on close. */
export function CredentialsModal({ creds, title, onClose }: { creds: Credentials | null; title: string; onClose: () => void }) {
  return (
    <Dialog open={creds !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        title={title}
        description="Store these in your application's configuration now."
        footer={
          <DialogClose asChild>
            <Button>I have saved the password</Button>
          </DialogClose>
        }
      >
        {creds && (
          <div className="space-y-4">
            <p role="alert" className="rounded-md bg-warning/14 px-3 py-2 text-sm text-warning">
              This password is shown once and is never stored by the panel. If you lose it, rotate the credentials to get a new one.
            </p>
            <CopyField label="Database" value={creds.database} />
            <CopyField label="Username" value={creds.username} />
            <CopyField label="Password" value={creds.password} />
            <CopyField label="Host : port" value={`${creds.host}:${creds.port}`} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
