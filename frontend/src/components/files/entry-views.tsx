"use client";

import * as React from "react";
import { Archive, Download, File as FileIcon, FileArchive, Folder, FolderInput, Info, Link2, MoreVertical, PackageOpen, Pencil, Trash2 } from "lucide-react";
import type { Entry } from "./api";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { IconButton } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { fmtBytes, fmtDate } from "@/components/resource/helpers";

export interface EntryActions {
  open: (e: Entry) => void;
  details: (e: Entry) => void;
  download: (e: Entry) => void;
  rename: (e: Entry) => void;
  move: (e: Entry) => void;
  archive: (e: Entry) => void;
  extract: (e: Entry) => void;
  remove: (e: Entry) => void;
}

export const isArchive = (e: Entry) => e.type === "file" && /\.(zip|tar\.gz|tgz|tar)$/i.test(e.name);

function Icon({ e }: { e: Entry }) {
  const c = "h-5 w-5 shrink-0";
  if (e.type === "dir") return <Folder className={`${c} text-primary`} />;
  if (e.type === "symlink") return <Link2 className={`${c} text-warning`} />;
  if (isArchive(e)) return <FileArchive className={`${c} text-muted`} />;
  return <FileIcon className={`${c} text-muted`} />;
}

export function EntryMenu({ e, a, canWrite, canDelete }: { e: Entry; a: EntryActions; canWrite: boolean; canDelete: boolean }) {
  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton label={`Actions for ${e.name}`} className="h-11 w-11 md:h-9 md:w-9">
          <MoreVertical className="h-4 w-4" />
        </IconButton>
      </MenuTrigger>
      <MenuContent>
        <MenuItem icon={Info} onSelect={() => a.details(e)}>Details</MenuItem>
        {e.type === "file" && <MenuItem icon={Download} onSelect={() => a.download(e)}>Download</MenuItem>}
        {canWrite && e.type !== "other" && (
          <>
            <MenuItem icon={Pencil} onSelect={() => a.rename(e)}>Rename</MenuItem>
            <MenuItem icon={FolderInput} onSelect={() => a.move(e)}>Move</MenuItem>
            {e.type !== "symlink" && <MenuItem icon={Archive} onSelect={() => a.archive(e)}>Archive…</MenuItem>}
            {isArchive(e) && <MenuItem icon={PackageOpen} onSelect={() => a.extract(e)}>Extract here</MenuItem>}
          </>
        )}
        {canDelete && e.type !== "other" && (
          <MenuItem icon={Trash2} danger onSelect={() => a.remove(e)}>Move to trash</MenuItem>
        )}
      </MenuContent>
    </Menu>
  );
}

export function EntryList({ entries, view, a, canWrite, canDelete }: { entries: Entry[]; view: "list" | "grid"; a: EntryActions; canWrite: boolean; canDelete: boolean }) {
  if (view === "grid")
    return (
      <ul className="grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {entries.map((e) => (
          <li key={e.path}>
            <Card className="flex items-center gap-2 p-2">
              <button type="button" onClick={() => a.open(e)} className="flex min-h-11 min-w-0 flex-1 flex-col items-start gap-1 rounded-md p-1 text-left hover:bg-subtle">
                <Icon e={e} />
                <span className="w-full truncate text-sm font-medium text-text">{e.name}</span>
                <span className="tnum text-xs text-muted">{e.type === "dir" ? "Folder" : fmtBytes(e.size)}</span>
              </button>
              <EntryMenu e={e} a={a} canWrite={canWrite} canDelete={canDelete} />
            </Card>
          </li>
        ))}
      </ul>
    );
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-border text-left text-xs text-muted uppercase">
          <tr>
            <th className="px-4 py-2 font-medium">Name</th>
            <th className="hidden px-3 py-2 font-medium md:table-cell">Modified</th>
            <th className="px-3 py-2 text-right font-medium">Size</th>
            <th className="hidden px-3 py-2 font-medium lg:table-cell">Permissions</th>
            <th className="w-12 px-2 py-2"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {entries.map((e) => (
            <tr key={e.path} className="hover:bg-subtle/60">
              <td className="max-w-0 px-4 py-1">
                <button type="button" onClick={() => a.open(e)} className="flex min-h-11 w-full items-center gap-2.5 text-left">
                  <Icon e={e} />
                  <span className="truncate font-medium text-text">{e.name}</span>
                  {e.type === "symlink" && <span className="text-xs text-warning">link (blocked)</span>}
                </button>
              </td>
              <td className="tnum hidden px-3 text-muted md:table-cell">{fmtDate(e.mtime)}</td>
              <td className="tnum px-3 text-right text-muted">{e.type === "dir" ? "—" : fmtBytes(e.size)}</td>
              <td className="hidden px-3 font-mono text-xs text-muted lg:table-cell">{e.mode}</td>
              <td className="px-2 text-right"><EntryMenu e={e} a={a} canWrite={canWrite} canDelete={canDelete} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
