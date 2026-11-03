"use client";

import * as React from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select, SearchInput } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { EmptyState, ErrorState } from "@/components/ui/states";
import { FormDialog } from "@/components/resource/form-dialog";
import { StatusPill, fmtDate } from "@/components/resource/helpers";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/components/providers/auth-provider";
import { useApi } from "@/hooks/use-api";
import { patch, post } from "@/lib/http";
import type { Paged } from "./common";

interface Cat { id: string; name: string; articles: number }
interface Art { id: string; slug: string; title: string; summary: string | null; category: string | null; category_id: string | null; status: string; updated_at: string; helpful_yes: number; helpful_no: number }
interface ArtFull extends Art { body: string; related: Art[]; my_feedback: string | null }

/** Search + reader for everyone; authors (kb:write) also get a manager with drafts. */
export function KbPanel() {
  const { can } = useAuth();
  const writer = can("kb:write");
  const toast = useToast();
  const [q, setQ] = React.useState("");
  const [cat, setCat] = React.useState("");
  const [page, setPage] = React.useState(1);
  const [size, setSize] = React.useState(10);
  const [open, setOpen] = React.useState<string | null>(null);
  const [editing, setEditing] = React.useState<Art | "new" | null>(null);
  const [newCat, setNewCat] = React.useState(false);
  const cats = useApi<Cat[]>("/kb/categories");
  const list = useApi<Paged<Art>>("/kb/articles", { q, category_id: cat, limit: size, offset: (page - 1) * size });
  const art = useApi<ArtFull>(open ? `/kb/articles/${open}` : null);

  async function vote(helpful: boolean) {
    if (!art.data) return;
    await post(`/kb/articles/${art.data.id}/feedback`, { helpful });
    toast({ title: "Thanks for the feedback", tone: "success" });
    art.reload();
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="flex flex-wrap items-center gap-2 pt-4">
          <SearchInput value={q} onChange={(v) => { setQ(v); setPage(1); }} placeholder="Search help articles…" className="min-w-48 flex-1" />
          <Select aria-label="Category" className="w-44" value={cat} onChange={(e) => { setCat(e.target.value); setPage(1); }}>
            <option value="">All categories</option>
            {cats.data?.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.articles})</option>)}
          </Select>
          {writer && <Button size="sm" variant="outline" onClick={() => setNewCat(true)}>New category</Button>}
          {writer && <Button size="sm" onClick={() => setEditing("new")}>New article</Button>}
        </CardBody>
      </Card>
      <Card className="overflow-hidden">
        {list.loading && <p className="p-5 text-sm text-muted">Loading…</p>}
        {list.error && <ErrorState onRetry={list.reload} />}
        {list.data?.items.length === 0 && <EmptyState title="No articles found" description={q ? "Try different keywords." : "Nothing has been published yet."} />}
        <ul className="divide-y divide-border">
          {list.data?.items.map((a) => (
            <li key={a.id} className="flex items-start justify-between gap-3 px-4 py-3">
              <button type="button" className="min-h-11 min-w-0 flex-1 text-left" onClick={() => setOpen(a.slug)}>
                <span className="block font-medium text-text">{a.title}</span>
                <span className="block text-sm text-muted">{a.summary ?? a.category ?? ""}</span>
                <span className="text-xs text-faint">Updated {fmtDate(a.updated_at)}</span>
              </button>
              <div className="flex shrink-0 items-center gap-2">
                {writer && <StatusPill status={a.status} />}
                {writer && <Button size="sm" variant="outline" onClick={() => setEditing(a)}>Edit</Button>}
              </div>
            </li>
          ))}
        </ul>
        {list.data && <Pagination page={page} pageSize={size} total={list.data.total} onPage={setPage} onPageSize={(s) => { setSize(s); setPage(1); }} />}
      </Card>

      <Sheet open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent title={art.data?.title ?? "Article"} description={art.data?.category ?? "Knowledge base article"} className="w-[min(100vw,40rem)]">
          {art.data && (
            <div className="space-y-5 p-5">
              {/* Plain text only: article bodies are never rendered as HTML. */}
              <div className="text-base break-words whitespace-pre-wrap text-text">{art.data.body}</div>
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4 text-sm">
                <span className="text-muted">Was this helpful?</span>
                <Button size="sm" variant={art.data.my_feedback === "helpful" ? "primary" : "outline"} onClick={() => vote(true)}><ThumbsUp className="h-4 w-4" /> Yes ({art.data.helpful_yes})</Button>
                <Button size="sm" variant={art.data.my_feedback === "not_helpful" ? "primary" : "outline"} onClick={() => vote(false)}><ThumbsDown className="h-4 w-4" /> No ({art.data.helpful_no})</Button>
              </div>
              {art.data.related.length > 0 && (
                <div>
                  <h3 className="mb-1 text-sm font-medium text-text">Related articles</h3>
                  <ul className="space-y-1">{art.data.related.map((r) => <li key={r.id}><button className="text-sm text-primary underline" onClick={() => setOpen(r.slug)}>{r.title}</button></li>)}</ul>
                </div>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>

      <FormDialog open={newCat} onOpenChange={setNewCat} title="New category" fields={[{ name: "name", label: "Name", required: true }, { name: "description", label: "Description" }]}
        onSubmit={async (v) => { await post("/kb/categories", v); cats.reload(); }} />
      <FormDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        title={editing === "new" ? "New article" : "Edit article"}
        description="Drafts are visible only to staff with knowledge-base access. Content is shown as plain text."
        fields={[
          { name: "title", label: "Title", required: true, defaultValue: editing && editing !== "new" ? editing.title : "" },
          { name: "summary", label: "Summary", defaultValue: editing && editing !== "new" ? (editing.summary ?? "") : "" },
          { name: "category_id", label: "Category", type: "select", options: (cats.data ?? []).map((c) => ({ value: c.id, label: c.name })), defaultValue: editing && editing !== "new" ? (editing.category_id ?? "") : "" },
          { name: "status", label: "Status", type: "select", required: true, options: [{ value: "draft", label: "Draft" }, { value: "published", label: "Published" }], defaultValue: editing && editing !== "new" ? editing.status : "draft" },
          { name: "body", label: "Body", type: "textarea", required: editing === "new", hint: editing === "new" ? undefined : "Leave blank to keep the current body." },
        ]}
        onSubmit={async (v) => {
          if (editing === "new") await post("/kb/articles", v);
          else if (editing) await patch(`/kb/articles/${editing.id}`, v);
          list.reload();
          cats.reload();
        }}
      />
    </div>
  );
}

export { CardHeader };
