"use client";

import * as React from "react";
import { Save } from "lucide-react";
import { Dialog, DialogClose, DialogContent } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/http";
import { type Entry, readText, saveText } from "./api";

const msg = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");

/** Plain-text editor for one file. Loads through the agent, saves as an overwrite upload (audited, quota-checked). */
export function EditorDialog({ entry, site, scope, canWrite, onClose, onSaved }: { entry: Entry | null; site: string; scope: string; canWrite: boolean; onClose: () => void; onSaved: () => void }) {
  const toast = useToast();
  const [text, setText] = React.useState("");
  const [orig, setOrig] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [err, setErr] = React.useState<string>();
  const dirty = text !== orig;

  React.useEffect(() => {
    if (!entry) return;
    let alive = true;
    /* eslint-disable react-hooks/set-state-in-effect -- reset when a new file opens */
    setLoading(true);
    setErr(undefined);
    setText("");
    setOrig("");
    /* eslint-enable react-hooks/set-state-in-effect */
    readText(site, scope, entry.path)
      .then((t) => alive && (setText(t), setOrig(t)))
      .catch((e) => alive && setErr(msg(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [entry, site, scope]);

  async function save() {
    if (!entry) return;
    setSaving(true);
    try {
      await saveText(site, scope, entry.path, text);
      setOrig(text);
      toast({ title: `Saved ${entry.name}`, tone: "success" });
      onSaved();
    } catch (e) {
      toast({ title: "Save failed", description: msg(e), tone: "danger" });
    } finally {
      setSaving(false);
    }
  }

  const lines = text ? text.split("\n").length : 0;
  return (
    <Dialog open={!!entry} onOpenChange={(o) => { if (!o && (!dirty || confirmLeave())) onClose(); }}>
      <DialogContent title={entry ? `Edit ${entry.name}` : "Edit"} className="max-w-5xl">
        <p className="mb-2 truncate text-xs text-muted">/{entry?.path}</p>
        {loading ? (
          <div className="h-[60vh] animate-pulse rounded-md bg-subtle" />
        ) : err ? (
          <p role="alert" className="rounded-md bg-subtle px-3 py-4 text-sm text-danger">{err}</p>
        ) : (
          <textarea
            aria-label={`Contents of ${entry?.name ?? "file"}`}
            className="h-[60vh] w-full resize-none rounded-md border border-border bg-surface p-3 font-mono text-[13px] leading-5 text-text focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
            spellCheck={false}
            readOnly={!canWrite}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (canWrite && dirty && !saving) void save(); }
              if (e.key === "Tab" && !e.shiftKey) {
                e.preventDefault();
                const t = e.currentTarget, a = t.selectionStart, b = t.selectionEnd;
                setText(text.slice(0, a) + "  " + text.slice(b));
                requestAnimationFrame(() => t.setSelectionRange(a + 2, a + 2));
              }
            }}
          />
        )}
        <div className="mt-3 flex items-center justify-between gap-3">
          <span className="text-xs text-muted tabular-nums">{lines} lines · {new Blob([text]).size.toLocaleString()} bytes{dirty ? " · unsaved changes" : ""}</span>
          <div className="flex gap-2">
            <DialogClose asChild><Button type="button" variant="outline">Close</Button></DialogClose>
            {canWrite && <Button type="button" loading={saving} disabled={!dirty || loading || !!err} onClick={() => void save()}><Save className="h-4 w-4" />Save</Button>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );

  function confirmLeave() {
    return window.confirm("Discard unsaved changes?");
  }
}
