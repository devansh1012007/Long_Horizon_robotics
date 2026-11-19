"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toast } from "sonner";
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  Loader2,
  Search,
} from "lucide-react";
import {
  EpisodeDetail,
  EpisodeRowView,
  FAULTS,
  FAULT_LABELS,
  SYSTEM_COLORS,
  SYSTEMS,
  fmtNum,
} from "@/lib/dashboard-constants";

const OUTCOMES = ["all", "success", "failure", "budget_exceeded"];

function outcomeBadge(outcome: string, success: boolean) {
  if (success)
    return (
      <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
        success
      </Badge>
    );
  if (outcome === "budget_exceeded")
    return <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">budget</Badge>;
  return <Badge variant="destructive">failure</Badge>;
}

function TimelineStep({ ev }: { ev: EpisodeDetail["events"][number] }) {
  const tone =
    ev.etype === "veto"
      ? "text-red-600"
      : ev.etype === "escalation"
        ? "text-amber-600"
        : ev.etype === "verified" || ev.etype === "outcome"
          ? "text-emerald-600"
          : "text-muted-foreground";
  return (
    <div className="flex gap-2 border-b py-1 last:border-b-0">
      <span className="w-12 shrink-0 font-mono text-[11px] text-muted-foreground">
        t{ev.t?.toFixed(2)}
      </span>
      <span className="w-28 shrink-0 text-xs font-medium">{ev.node}</span>
      <span className={`text-xs ${tone}`}>
        {ev.etype}
        {ev.payload && Object.keys(ev.payload).length > 0 && (
          <span className="ml-1 text-muted-foreground">
            {JSON.stringify(ev.payload).slice(0, 180)}
          </span>
        )}
      </span>
    </div>
  );
}

export function EpisodeExplorer() {
  const [rows, setRows] = useState<EpisodeRowView[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [system, setSystem] = useState("all");
  const [fault, setFault] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState<EpisodeDetail | null>(null);
  const [detailFor, setDetailFor] = useState<EpisodeRowView | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        page: String(page),
        pageSize: String(pageSize),
        system,
        fault,
        outcome,
      });
      const res = await fetch(`/api/episodes?${params}`);
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.error ?? "Failed to load episodes.");
        return;
      }
      setRows(data.rows);
      setTotal(data.total);
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, system, fault, outcome]);

  useEffect(() => {
    void load();
  }, [load]);

  const pages = Math.max(1, Math.ceil(total / pageSize));

  async function openDetail(row: EpisodeRowView) {
    setDetailFor(row);
    setDetailLoading(true);
    try {
      const res = await fetch(
        `/api/jobs/${row.jobId}/episodes/${encodeURIComponent(row.epId)}`,
      );
      const data = await res.json();
      if (!res.ok) {
        toast.error(
          data.error ??
            "Episode timeline is no longer on the engine (job pruned).",
        );
        setDetail(null);
        return;
      }
      setDetail(data as EpisodeDetail);
    } catch {
      toast.error("Engine unreachable.");
    } finally {
      setDetailLoading(false);
    }
  }

  const m = detail?.metrics as Record<string, number> | undefined;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Database className="h-4 w-4 text-emerald-600" /> Episode database
        </CardTitle>
        <CardDescription>
          Every episode you have run, persisted server-side. Click a row for
          the full timeline: events, diagnoses and recovery decisions from the
          engine&apos;s SQLite store.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Select
            value={system}
            onValueChange={(v) => {
              setSystem(v);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-[150px]" aria-label="System filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All systems</SelectItem>
              {SYSTEMS.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  {s.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={fault}
            onValueChange={(v) => {
              setFault(v);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-[190px]" aria-label="Fault filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All faults</SelectItem>
              {FAULTS.map((f) => (
                <SelectItem key={f} value={f}>
                  {FAULT_LABELS[f]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={outcome}
            onValueChange={(v) => {
              setOutcome(v);
              setPage(1);
            }}
          >
            <SelectTrigger className="w-[150px]" aria-label="Outcome filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OUTCOMES.map((o) => (
                <SelectItem key={o} value={o}>
                  {o === "all" ? "All outcomes" : o}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="ml-auto text-sm text-muted-foreground">
            {total} episodes
          </span>
        </div>

        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Episode</TableHead>
                <TableHead>System</TableHead>
                <TableHead>Fault</TableHead>
                <TableHead>H</TableHead>
                <TableHead>Seed</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead>Extra</TableHead>
                <TableHead>Rec.t</TableHead>
                <TableHead>Diag</TableHead>
                <TableHead>Esc.</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading && (
                <TableRow>
                  <TableCell colSpan={10} className="py-8 text-center">
                    <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
                  </TableCell>
                </TableRow>
              )}
              {!loading && rows.length === 0 && (
                <TableRow>
                  <TableCell colSpan={10} className="py-8 text-center text-sm text-muted-foreground">
                    No episodes match. Run a batch first.
                  </TableCell>
                </TableRow>
              )}
              {rows.map((r) => (
                <TableRow
                  key={r.id}
                  className="cursor-pointer hover:bg-accent"
                  onClick={() => void openDetail(r)}
                >
                  <TableCell className="font-mono text-xs">{r.epId}</TableCell>
                  <TableCell>
                    <span className="flex items-center gap-1.5">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ background: SYSTEM_COLORS[r.system] }}
                      />
                      {r.system}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs">{r.fault.replace("stale_dependency", "stale-dep").replace("f_", "")}</TableCell>
                  <TableCell>{r.horizon}</TableCell>
                  <TableCell>{r.seed}</TableCell>
                  <TableCell>{outcomeBadge(r.outcome, r.success)}</TableCell>
                  <TableCell>{r.extraActions}</TableCell>
                  <TableCell>{fmtNum(r.recoveryTimeS, 2)}</TableCell>
                  <TableCell>
                    {r.diagCorrect === null ? "—" : r.diagCorrect ? "✓" : "✗"}
                  </TableCell>
                  <TableCell>{r.escalations}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <div className="mt-3 flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1}
          >
            <ChevronLeft className="h-4 w-4" /> Prev
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} / {pages}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.min(pages, p + 1))}
            disabled={page >= pages}
          >
            Next <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </CardContent>

      <Dialog
        open={!!detailFor}
        onOpenChange={(open) => {
          if (!open) {
            setDetailFor(null);
            setDetail(null);
          }
        }}
      >
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">
              {detailFor?.epId}
            </DialogTitle>
            <DialogDescription>
              {detailFor && (
                <>
                  {detailFor.system} · horizon {detailFor.horizon} · seed{" "}
                  {detailFor.seed} · {detailFor.jobLabel}
                </>
              )}
            </DialogDescription>
          </DialogHeader>
          {detailLoading && (
            <div className="py-8 text-center">
              <Loader2 className="mx-auto h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          )}
          {!detailLoading && detail && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["outcome", String(detail.episode?.outcome ?? "—")],
                  ["extra actions", String(m?.extra_actions ?? "—")],
                  ["recovery t", `${fmtNum(Number(m?.recovery_time_s ?? 0), 2)}s`],
                  ["steps", String(m?.steps ?? "—")],
                ].map(([k, v]) => (
                  <div key={k} className="rounded-md bg-muted px-3 py-2">
                    <div className="text-xs text-muted-foreground">{k}</div>
                    <div className="text-sm font-semibold">{v}</div>
                  </div>
                ))}
              </div>

              {detail.diagnoses.length > 0 && (
                <div>
                  <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Diagnoses
                  </h4>
                  <div className="space-y-1">
                    {detail.diagnoses.map((d, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 rounded border px-2 py-1 text-xs"
                      >
                        <Clock className="h-3 w-3 text-muted-foreground" />
                        <span className="font-medium">
                          step {d.step}: {d.argmax}
                        </span>
                        <span className="text-muted-foreground">
                          (truth {d.truth})
                        </span>
                        {d.argmax === d.truth ? (
                          <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                            correct
                          </Badge>
                        ) : (
                          <Badge variant="destructive">miss</Badge>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {detail.recoveries.length > 0 && (
                <div>
                  <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Recovery decisions
                  </h4>
                  <div className="space-y-1">
                    {detail.recoveries.map((r, i) => (
                      <div key={i} className="rounded border px-2 py-1 text-xs">
                        <span className="font-medium">
                          step {r.step}: {r.chosen}
                        </span>
                        <span className="ml-2 text-muted-foreground">
                          → {r.result}
                        </span>
                        {r.cost_terms && (
                          <span className="ml-2 text-muted-foreground">
                            cost:{" "}
                            {Object.entries(r.cost_terms)
                              .map(([k, v]) => `${k} ${Number(v).toFixed(2)}`)
                              .join(", ")}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Event timeline ({detail.events.length})
                </h4>
                <div className="max-h-64 overflow-y-auto rounded-md border px-3 py-2">
                  {detail.events.map((ev, i) => (
                    <TimelineStep key={i} ev={ev} />
                  ))}
                </div>
              </div>
            </div>
          )}
          {!detailLoading && detailFor && !detail && (
            <p className="text-sm text-muted-foreground">
              The engine pruned this job&apos;s SQLite store (keeps the latest
              60). The row above remains in the persistent episode database.
            </p>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
