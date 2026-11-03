"use client";

import * as React from "react";
import { Copy } from "lucide-react";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";

/** Show-once secret dialog. The value lives only in component state and is dropped when closed. */
export function SecretReveal({ title, secret, onClose }: { title: string; secret: string | null; onClose: () => void }) {
  const toast = useToast();
  return (
    <Dialog open={!!secret} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title={title} description="Copy it now. For your security it is shown only once and cannot be retrieved later."
        footer={<DialogClose asChild><Button>I have stored it safely</Button></DialogClose>}>
        <div className="space-y-3">
          <code className="block rounded-md border border-border bg-subtle p-3 text-sm break-all text-text select-all">{secret}</code>
          <Button variant="outline" size="sm" onClick={async () => { await navigator.clipboard.writeText(secret ?? ""); toast({ title: "Copied to clipboard", tone: "success" }); }}><Copy className="h-4 w-4" /> Copy</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
