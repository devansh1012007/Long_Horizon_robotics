"use client";

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Progress,
} from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Slider } from "@/components/ui/slider";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "sonner";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDashed,
  FlaskConical,
  Loader2,
  Radio,
  Rocket,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  CONDITIONS,
  FAULTS,
  FAULT_LABELS,
  HORIZONS,
  PairedStat,
  SYSTEMS,
  SYSTEM_COLORS,
  SystemSummary,
  faultShort,
  fmtNum,
  fmtPct,
  JobView,
} from "@/lib/dashboard-constants";

const PRESETS: {
  id: string;
  label: string;
  systems: string[];
  horizons: number[];
  episodes: number;
  fault: string | null;
}[] = [
  {
    id: "demo",
    label: "Demo: RG vs all baselines",
    systems: ["RG", "B1", "B2", "B3", "B4", "B5"],
    horizons: [1, 3],
    episodes: 8,
    fault: "f_slip",
  },
  {
    id: "rg-b5",
    label: "RG vs B5 (strongest baseline)",
    systems: ["RG", "B5"],
    horizons: [1, 3, 5],
    episodes: 10,
    fault: "f_slip",
  },
  {
    id: "ablations",
    label: "Ablations A1-A5",
    systems: ["RG", "A1", "A2", "A3", "A4", "A5"],
    horizons: [1, 3],
    episodes: 6,
    fault: "f_pose",
  },
  {
    id: "faultfree",
    label: "Fault-free false-alarm check",
    systems: ["RG", "B4"],
    horizons: [1, 2, 3],
    episodes: 10,
    fault: null,
  },
];

const FAMILY_LABEL: Record<string, string> = {
  causal: "Causal recovery layer",
  baseline: "Baselines",
  ablation: "Ablations",
};

function StatusBadge({ status }: { status: JobView["status"] }) {
  if (status === "done")
    return (
      <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
        <CheckCircle2 className="mr-1 h-3 w-3" /> done
      </Badge>
    );
  if (status === "running")
    return (
      <Badge className="bg-amber-100 text-amber-800 hover:bg-amber-100">
        <Loader2 className="mr-1 h-3 w-3 animate-spin" /> running
      </Badge>
    );
  if (status === "failed")
    return (
      <Badge variant="destructive">
        <XCircle className="mr-1 h-3 w-3" /> failed
      </Badge>
    );
  return (
    <Badge variant="secondary">
      <CircleDashed className="mr-1 h-3 w-3" /> queued
    </Badge>
  );
}

function SummaryChips({ summary }: { summary: Record<string, SystemSummary> }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(summary)
        .sort((a, b) => (b[1].success_rate ?? 0) - (a[1].success_rate ?? 0))
        .map(([sys, s]) => (
          <TooltipProvider key={sys}>
            <Tooltip>
              <TooltipTrigger asChild>
                <span
                  className="rounded-md px-2 py-0.5 text-xs font-medium text-white"
                  style={{ background: SYSTEM_COLORS[sys] ?? "#64748b" }}
                >
                  {sys} {fmtPct(s.success_rate, 0)}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-xs">
                n={s.n} · CI [{fmtPct(s.ci_lo, 0)}, {fmtPct(s.ci_hi, 0)}] ·
                extra actions {fmtNum(s.extra_actions_mean)} · diag{" "}
                {fmtPct(s.diag_accuracy)}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        ))}
    </div>
  );
}

function StatsTable({ stats }: { stats: PairedStat[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Comparison</TableHead>
          <TableHead>Δ success (bootstrap 95% CI)</TableHead>
          <TableHead>McNemar p</TableHead>
          <TableHead>Wilcoxon p</TableHead>
          <TableHead>Significant</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {stats.map((st) => (
          <TableRow key={st.vs}>
            <TableCell className="font-medium">RG vs {st.vs}</TableCell>
            <TableCell>
              {fmtNum(st.delta_success_mean, 3)} [
              {fmtNum(st.delta_ci_lo, 3)}, {fmtNum(st.delta_ci_hi, 3)}]
            </TableCell>
            <TableCell>{fmtNum(st.mcnemar_p, 4)}</TableCell>
            <TableCell>{fmtNum(st.wilcoxon_p, 4)}</TableCell>
            <TableCell>
              {st.ci_excludes_zero ? (
                <Badge className="bg-emerald-100 text-emerald-800 hover:bg-emerald-100">
                  CI excludes 0
                </Badge>
              ) : (
                <Badge variant="secondary">n.s.</Badge>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export type SubmitPayload = {
  systems: string[];
  horizons: number[];
  condition: string;
  fault: string | null;
  episodes: number;
  base_seed: number;
  label: string;
};

export function ControlRoom({
  jobs,
  detailed,
  onSubmit,
  onDelete,
  onWatch,
}: {
  jobs: JobView[];
  detailed: Record<
    string,
    { summary: Record<string, SystemSummary>; stats: PairedStat[] } | undefined
  >;
  onSubmit: (payload: SubmitPayload) => Promise<boolean>;
  onDelete: (id: string) => Promise<void>;
  onWatch: (id: string) => void;
}) {
  const [selected, setSelected] = useState<string[]>(["RG", "B1", "B5"]);
  const [condition, setCondition] = useState("single");
  const [fault, setFault] = useState<string>("f_slip");
  const [horizons, setHorizons] = useState<number[]>([1, 3]);
  const [episodes, setEpisodes] = useState(8);
  const [baseSeed, setBaseSeed] = useState(4200);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [openJob, setOpenJob] = useState<string | null>(null);

  const totalEpisodes = selected.length * horizons.length * episodes;
  const tooBig = totalEpisodes > 600;
  const activeCount = jobs.filter(
    (j) => j.status === "queued" || j.status === "running",
  ).length;

  const grouped = useMemo(() => {
    const fams: Record<string, typeof SYSTEMS> = {};
    for (const s of SYSTEMS) (fams[s.family] ??= []).push(s);
    return fams;
  }, []);

  function toggleSystem(id: string) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id],
    );
  }

  function toggleHorizon(h: number) {
    setHorizons((prev) =>
      prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h].sort(),
    );
  }

  async function submit() {
    if (selected.length === 0 || horizons.length === 0) {
      toast.error("Select at least one system and one horizon.");
      return;
    }
    setBusy(true);
    const ok = await onSubmit({
      systems: selected,
      horizons,
      condition,
      fault: fault === "auto" ? null : fault,
      episodes,
      base_seed: baseSeed,
      label,
    });
    setBusy(false);
    if (ok) {
      toast.success("Batch submitted to the engine pool.");
    }
  }

  async function loadDemo() {
    const p = PRESETS[0];
    setSelected(p.systems);
    setHorizons(p.horizons);
    setEpisodes(p.episodes);
    setFault(p.fault ?? "auto");
    setCondition("single");
    setLabel(p.label);
    setBusy(true);
    const ok = await onSubmit({
      systems: p.systems,
      horizons: p.horizons,
      condition: "single",
      fault: p.fault,
      episodes: p.episodes,
      base_seed: 4200,
      label: p.label,
    });
    setBusy(false);
    if (ok) toast.success("Demo batch submitted — watch it in Live Telemetry.");
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
      {/* Submission panel */}
      <Card className="h-fit">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Rocket className="h-4 w-4 text-emerald-600" /> New evaluation batch
          </CardTitle>
          <CardDescription>
            Identical skills, perception noise and seeds across systems — only
            the recovery logic differs (spec 10.3).
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Button
            variant="outline"
            className="w-full border-emerald-600 text-emerald-700 hover:bg-emerald-50"
            onClick={loadDemo}
            disabled={busy}
          >
            <FlaskConical className="mr-2 h-4 w-4" /> Load demo comparison
          </Button>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Systems ({selected.length})</Label>
              <span
                className={`text-xs ${selected.length === 0 ? "text-destructive" : "text-muted-foreground"}`}
              >
                max 12
              </span>
            </div>
            {Object.entries(grouped).map(([fam, list]) => (
              <div key={fam}>
                <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  {FAMILY_LABEL[fam] ?? fam}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {list.map((s) => {
                    const active = selected.includes(s.id);
                    return (
                      <TooltipProvider key={s.id}>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <button
                              type="button"
                              onClick={() => toggleSystem(s.id)}
                              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                                active
                                  ? "border-transparent text-white"
                                  : "bg-background text-foreground hover:bg-accent"
                              }`}
                              style={
                                active
                                  ? {
                                      background:
                                        SYSTEM_COLORS[s.id] ?? "#10b981",
                                    }
                                  : undefined
                              }
                            >
                              {s.id}
                            </button>
                          </TooltipTrigger>
                          <TooltipContent side="bottom" className="max-w-56 text-xs">
                            <p className="font-medium">{s.name}</p>
                            <p>{s.desc}</p>
                          </TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>Fault injection</Label>
              <Select value={fault} onValueChange={setFault}>
                <SelectTrigger aria-label="Fault">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto (schedule)</SelectItem>
                  {FAULTS.map((f) => (
                    <SelectItem key={f} value={f}>
                      {FAULT_LABELS[f]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Condition</Label>
              <Select value={condition} onValueChange={setCondition}>
                <SelectTrigger aria-label="Condition">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CONDITIONS.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Task horizons</Label>
            <div className="flex gap-1.5">
              {HORIZONS.map((h) => {
                const active = horizons.includes(h);
                return (
                  <button
                    key={h}
                    type="button"
                    onClick={() => toggleHorizon(h)}
                    className={`h-9 w-11 rounded-md border text-sm font-medium transition-colors ${
                      active
                        ? "border-emerald-600 bg-emerald-600 text-white"
                        : "bg-background hover:bg-accent"
                    }`}
                  >
                    {h}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label>Episodes per cell</Label>
              <span className="text-sm font-medium">{episodes}</span>
            </div>
            <Slider
              value={[episodes]}
              min={1}
              max={100}
              step={1}
              onValueChange={(v) => setEpisodes(v[0])}
              aria-label="Episodes per cell"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="seed">Base seed</Label>
              <Input
                id="seed"
                type="number"
                value={baseSeed}
                min={0}
                onChange={(e) =>
                  setBaseSeed(Math.max(0, Number(e.target.value) || 0))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="label">Label (optional)</Label>
              <Input
                id="label"
                value={label}
                placeholder="morning regression"
                onChange={(e) => setLabel(e.target.value)}
              />
            </div>
          </div>

          <div className="flex items-center justify-between rounded-md bg-muted px-3 py-2 text-sm">
            <span className="text-muted-foreground">
              {selected.length} systems × {horizons.length} horizons ×{" "}
              {episodes} eps
            </span>
            <span
              className={`font-semibold ${tooBig ? "text-destructive" : "text-emerald-700"}`}
            >
              {totalEpisodes} episodes
            </span>
          </div>
          {tooBig && (
            <p className="text-xs text-destructive">
              Over the 600-episode/job cap — trim systems, horizons or episodes
              to keep queue latency fair for everyone.
            </p>
          )}

          <Button
            className="w-full bg-emerald-600 text-white hover:bg-emerald-700"
            onClick={submit}
            disabled={
              busy || tooBig || selected.length === 0 || horizons.length === 0
            }
          >
            {busy ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Rocket className="mr-2 h-4 w-4" />
            )}
            Submit batch
          </Button>
          <p className="text-xs text-muted-foreground">
            {activeCount > 0
              ? `${activeCount} of your jobs are queued/running (quota: 2 concurrent).`
              : "Engine pool: 3 workers, shared queue, per-user quota of 2 active jobs."}
          </p>
        </CardContent>
      </Card>

      {/* Jobs list */}
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4 text-emerald-600" /> Your batches
            </CardTitle>
            <CardDescription>
              Progress updates stream over WebSocket; results persist to the
              episode database.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {jobs.length === 0 && (
              <p className="rounded-md border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
                No batches yet. Submit one on the left, or press
                &quot;Load demo comparison&quot;.
              </p>
            )}
            {jobs.map((job) => {
              const pct = job.total > 0 ? (job.progress / job.total) * 100 : 0;
              const det = detailed[job.id];
              const open = openJob === job.id;
              return (
                <div
                  key={job.id}
                  className="rounded-lg border p-3 transition-shadow hover:shadow-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge status={job.status} />
                    <span className="text-sm font-medium">{job.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {job.spec.systems.join(", ")} · h
                      {job.spec.horizons.join("/")} ·{" "}
                      {job.spec.fault
                        ? FAULT_LABELS[job.spec.fault] ?? job.spec.fault
                        : "auto faults"}{" "}
                      · seed {job.spec.base_seed}
                    </span>
                    <div className="ml-auto flex items-center gap-1">
                      {(job.status === "running" ||
                        job.status === "queued") && (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => onWatch(job.id)}
                        >
                          <Radio className="mr-1 h-3.5 w-3.5 text-amber-600" />
                          Watch
                        </Button>
                      )}
                      {(job.status === "done" || job.status === "failed") && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setOpenJob(open ? null : job.id)}
                          aria-expanded={open}
                        >
                          {open ? (
                            <ChevronUp className="h-4 w-4" />
                          ) : (
                            <ChevronDown className="h-4 w-4" />
                          )}
                          Results
                        </Button>
                      )}
                      {(job.status === "done" || job.status === "failed") && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => onDelete(job.id)}
                          aria-label={`Delete batch ${job.label}`}
                        >
                          <Trash2 className="h-4 w-4 text-muted-foreground" />
                        </Button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <Progress value={pct} className="h-2 flex-1" />
                    <span className="w-24 text-right text-xs text-muted-foreground">
                      {job.progress}/{job.total} eps
                    </span>
                  </div>
                  {job.summary && <div className="mt-2"><SummaryChips summary={job.summary} /></div>}
                  {job.error && (
                    <p className="mt-2 whitespace-pre-wrap rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">
                      {job.error.split("\n")[0]}
                    </p>
                  )}
                  {open && det && (
                    <div className="mt-3 space-y-3">
                      <Separator />
                      <div>
                        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Per-system aggregates (Wilson 95% CI)
                        </h4>
                        {det.summary && (
                          <SummaryChips summary={det.summary} />
                        )}
                      </div>
                      {det.stats && det.stats.length > 0 && (
                        <div>
                          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Paired statistics vs RG (10k resamples)
                          </h4>
                          <StatsTable stats={det.stats} />
                        </div>
                      )}
                      {!det.stats?.length && (
                        <p className="text-xs text-muted-foreground">
                          Paired stats require RG plus another system in the
                          same batch.
                        </p>
                      )}
                    </div>
                  )}
                  {open && !det && job.status === "done" && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      Loading results…
                    </p>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              What each metric means (spec 10.5)
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
            <p>
              <span className="font-medium text-foreground">Success rate</span>{" "}
              — verified task completion incl. invariants; Wilson CI shown.
            </p>
            <p>
              <span className="font-medium text-foreground">Extra actions</span>{" "}
              — actions beyond the nominal plan (recovery cost).
            </p>
            <p>
              <span className="font-medium text-foreground">Diag accuracy</span>{" "}
              — Bayes posterior argmax vs injected truth (stale-dependency
              excluded by design).
            </p>
            <p>
              <span className="font-medium text-foreground">Escalations</span> —
              Section-9 oracle-human handoffs after loop-guard exhaustion.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
