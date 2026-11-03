"use client";

import * as React from "react";
import type { Entry, StatResult } from "./api";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { DetailList, fmtBytes, fmtDate } from "@/components/resource/helpers";
import { Skeleton } from "@/components/ui/skeleton";
import { type EntryActions, EntryMenu } from "./entry-views";

export function DetailsSheet({ entry, stat, loading, error, onClose, a, canWrite, canDelete }: { entry: Entry | null; stat?: StatResult; loading: boolean; error?: string; onClose: () => void; a: EntryActions; canWrite: boolean; canDelete: boolean }) {
  const e = stat?.entry ?? entry;
  return (
    <Sheet open={!!entry} onOpenChange={(o) => !o && onClose()}>
      {entry && e && (
        <SheetContent title={entry.name} description={`${entry.type === "dir" ? "Folder" : "File"} details`}>
          <div className="space-y-4 p-5">
            {loading && <Skeleton className="h-40 w-full" />}
            {error && <p role="alert" className="rounded-md bg-danger/10 px-3 py-2 text-sm text-danger">{error}</p>}
            <DetailList
              items={[
                ["Path", `/${e.path}`],
                ["Type", e.type],
                ["Size", e.type === "dir" ? "—" : `${fmtBytes(e.size)} (${e.size.toLocaleString()} bytes)`],
                ["Modified", fmtDate(e.mtime)],
                ["Permissions", `${e.mode} (${e.octal})`],
                ["Owner uid / gid", stat ? `${stat.permissions.owner_uid} / ${stat.permissions.owner_gid}` : "—"],
                ["Agent can read", stat ? (stat.permissions.readable ? "Yes" : "No") : "—"],
                ["Agent can write", stat ? (stat.permissions.writable ? "Yes" : "No") : "—"],
              ]}
            />
            <div className="flex flex-wrap gap-2">
              {e.type === "file" && <Button variant="outline" onClick={() => a.download(entry)}>Download</Button>}
              {e.type === "dir" && <Button variant="outline" onClick={() => { a.open(entry); onClose(); }}>Open</Button>}
              <EntryMenu e={entry} a={{ ...a, details: () => {} }} canWrite={canWrite} canDelete={canDelete} />
            </div>
          </div>
        </SheetContent>
      )}
    </Sheet>
  );
}
