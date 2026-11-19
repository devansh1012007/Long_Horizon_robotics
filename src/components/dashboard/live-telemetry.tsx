"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { io, type Socket } from "socket.io-client";
import {
  AlertTriangle,
  CircleStop,
  Gauge,
  Radio,
  Stethoscope,
  Wrench,
  Zap,
} from "lucide-react";
import { FAULT_LABELS, JobView } from "@/lib/dashboard-constants";

type LiveEvent = {
  job: string;
  etype: string;
  ep?: string;
  t?: number;
  step?: number;
  node?: string;
  payload?: Record<string, unknown>;
  subgoal?: string;
  skill?: string;
  D?: number;
  fault_free?: boolean;
  argmax?: string;
  truth?: string;
  entropy?: number;
  top?: [string, number][];
  chosen?: string;
  result?: string;
  cost?: Record<string, number>;
  outcome?: string;
  success?: boolean;
  extra_actions?: number;
  recovery_time_s?: number;
  escalations?: number;
  interventions?: number;
  steps?: number;
};

const ETYPE_STYLE: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  divergence: {
    label: "divergence",
    cls: "border-l-rose-500 bg-rose-50/60",
    icon: <AlertTriangle className="h-3.5 w-3.5 text-rose-500" />,
  },
  diagnosis: {
    label: "diagnosis",
    cls: "border-l-violet-500 bg-violet-50/60",
    icon: <Stethoscope className="h-3.5 w-3.5 text-violet-500" />,
  },
  recovery: {
    label: "recovery",
    cls: "border-l-emerald-500 bg-emerald-50/60",
    icon: <Wrench className="h-3.5 w-3.5 text-emerald-500" />,
  },
  outcome: {
    label: "outcome",
    cls: "border-l-teal-500 bg-teal-50/60",
    icon: <Zap className="h-3.5 w-3.5 text-teal-600" />,
  },
  veto: {
    label: "safety veto",
    cls: "border-l-red-600 bg-red-50/60",
    icon: <CircleStop className="h-3.5 w-3.5 text-red-600" />,
  },
  escalation: {
    label: "escalation",
    cls: "border-l-amber-500 bg-amber-50/60",
    icon: <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />,
  },
};

const FILTERS = ["divergence", "diagnosis", "recovery", "outcome", "veto", "escalation"];

function describe(ev: LiveEvent): string {
  switch (ev.etype) {
    case "divergence":
      return `D=${ev.D} on ${ev.subgoal ?? "?"} (${ev.skill ?? "?"})${ev.fault_free ? " · fault-free step" : ""}`;
    case "diagnosis":
      return `argmax ${ev.argmax} (truth ${ev.truth}) · top ${(ev.top ?? [])
        .map(([k, v]) => `${k} ${Math.round(v * 100)}%`)
        .join(", ")}`;
    case "recovery":
      return `${ev.chosen} → ${ev.result}${
        ev.cost ? ` · cost { ${Object.entries(ev.cost).map(([k, v]) => `${k} ${v}`).join(", ")} }` : ""
      }`;
    case "outcome":
      return `${ev.outcome} · extra ${ev.extra_actions} · recovery ${ev.recovery_time_s}s · esc ${ev.escalations}`;
    case "veto":
      return JSON.stringify(ev.payload ?? {}).slice(0, 160);
    default:
      return ev.node ? `${ev.node}: ${JSON.stringify(ev.payload ?? {}).slice(0, 140)}` : "";
  }
}

export function LiveTelemetry({
  jobs,
  activeJobId,
  setActiveJobId,
}: {
  jobs: JobView[];
  activeJobId: string | null;
  setActiveJobId: (id: string | null) => void;
}) {
  const sockRef = useRef<Socket | null>(null);
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<LiveEvent[]>([]);
  const [jobProgress, setJobProgress] = useState<
    Record<string, { progress: number; total: number; status: string }>
  >({});
  const [active, setActive] = useState<string[]>(FILTERS);
  const feedRef = useRef<HTMLDivElement>(null);

  const watchable = jobs.filter(
    (j) => j.status === "running" || j.status === "queued" || j.status === "done",
  );

  // socket lifecycle (socket kept in a ref; state only mirrors connection)
  // URL/path are build-time envs: sandbox sidecar uses the gateway hack
  // (/?XTransformPort=3031 with path "/"), production (nginx) uses
  // NEXT_PUBLIC_SOCKET_URL=/ with NEXT_PUBLIC_SOCKET_PATH=/socket.io/.
  useEffect(() => {
    const s = io(
      process.env.NEXT_PUBLIC_SOCKET_URL ?? "/?XTransformPort=3031",
      {
        path: process.env.NEXT_PUBLIC_SOCKET_PATH ?? "/",
        transports: ["websocket", "polling"],
        reconnectionDelay: 1200,
      },
    );
    sockRef.current = s;
    s.on("connect", () => setConnected(true));
    s.on("disconnect", () => setConnected(false));
    s.on("connect_error", () => setConnected(false));
    return () => {
      s.disconnect();
      sockRef.current = null;
    };
  }, []);

  // join room of the selected job once the socket is live
  useEffect(() => {
    const s = sockRef.current;
    if (!s || !connected || !activeJobId) return;
    s.emit("join", activeJobId);
    return () => {
      s.emit("leave", activeJobId);
    };
  }, [connected, activeJobId]);

  // event handling (subscribed once the socket is live)
  useEffect(() => {
    const s = sockRef.current;
    if (!s || !connected) return;
    const onEvent = (ev: LiveEvent) => {
      setEvents((prev) => {
        const next = [ev, ...prev];
        return next.length > 300 ? next.slice(0, 300) : next;
      });
    };
    const onJobUpdate = (job: {
      id: string;
      progress: number;
      total: number;
      status: string;
    }) => {
      setJobProgress((prev) => ({
        ...prev,
        [job.id]: {
          progress: job.progress,
          total: job.total,
          status: job.status,
        },
      }));
    };
    s.on("ep:event", onEvent);
    s.on("job:update", onJobUpdate);
    return () => {
      s.off("ep:event", onEvent);
      s.off("job:update", onJobUpdate);
    };
  }, [connected]);

  const filtered = useMemo(
    () => (active.length === FILTERS.length ? events : events.filter((e) => active.includes(e.etype))),
    [events, active],
  );

  const counters = useMemo(() => {
    let outcomes = 0,
      successes = 0,
      escalations = 0,
      diagnoses = 0,
      correct = 0;
    for (const e of events) {
      if (e.etype === "outcome") {
        outcomes += 1;
        if (e.success) successes += 1;
        escalations += e.escalations ?? 0;
      }
      if (e.etype === "diagnosis") {
        diagnoses += 1;
        if (e.argmax === e.truth) correct += 1;
      }
    }
    return { outcomes, successes, escalations, diagnoses, correct };
  }, [events]);

  const prog = activeJobId ? jobProgress[activeJobId] : null;

  function toggleFilter(f: string) {
    setActive((prev) =>
      prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f],
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_280px]">
      <Card className="order-2 lg:order-1">
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Radio
                className={`h-4 w-4 ${connected ? "text-emerald-600" : "text-muted-foreground"}`}
              />
              Live episode telemetry
            </CardTitle>
            <Badge
              variant={connected ? "secondary" : "outline"}
              className={connected ? "bg-emerald-100 text-emerald-800" : ""}
            >
              {connected ? "socket connected" : "connecting…"}
            </Badge>
          </div>
          <CardDescription>
            Streams divergence → diagnosis → recovery → outcome events from the
            engine pool (WebSocket room per job).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Select
              value={activeJobId ?? ""}
              onValueChange={(v) => {
                setActiveJobId(v || null);
                setEvents([]);
              }}
            >
              <SelectTrigger className="w-[320px]" aria-label="Job to watch">
                <SelectValue placeholder="Select a batch to watch" />
              </SelectTrigger>
              <SelectContent>
                {watchable.length === 0 && (
                  <SelectItem value="" disabled>
                    No batches yet — submit one in the Control Room
                  </SelectItem>
                )}
                {watchable.map((j) => (
                  <SelectItem key={j.id} value={j.id}>
                    {j.label} ({j.status})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <div className="flex flex-wrap gap-1.5">
              {FILTERS.map((f) => {
                const on = active.includes(f);
                const style = ETYPE_STYLE[f];
                return (
                  <button
                    key={f}
                    onClick={() => toggleFilter(f)}
                    className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                      on
                        ? "border-foreground/20 bg-foreground/5 text-foreground"
                        : "text-muted-foreground opacity-60 hover:opacity-100"
                    }`}
                  >
                    {style?.icon}
                    <span className="ml-1">{f}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {prog && (
            <div className="mb-3 rounded-md bg-muted px-3 py-2 text-sm">
              <span className="font-medium">{prog.status}</span> · {prog.progress}/
              {prog.total} episodes recorded
            </div>
          )}

          <div
            ref={feedRef}
            className="max-h-[420px] min-h-[280px] overflow-y-auto rounded-md border bg-background"
          >
            {filtered.length === 0 && (
              <p className="px-4 py-10 text-center text-sm text-muted-foreground">
                {activeJobId
                  ? "Waiting for events… submit a batch or re-run one to see live telemetry."
                  : "Select a batch above to subscribe to its WebSocket room."}
              </p>
            )}
            {filtered.map((ev, i) => {
              const style = ETYPE_STYLE[ev.etype] ?? {
                label: ev.etype,
                cls: "border-l-zinc-400",
                icon: <Gauge className="h-3.5 w-3.5 text-zinc-400" />,
              };
              return (
                <div
                  key={`${ev.ep}-${ev.etype}-${i}-${ev.t ?? ""}`}
                  className={`flex items-start gap-2 border-b border-l-4 px-3 py-1.5 text-sm last:border-b-0 ${style.cls}`}
                >
                  <span className="mt-0.5 shrink-0">{style.icon}</span>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {ev.ep}
                      </span>
                      <span className="text-xs font-medium">{style.label}</span>
                    </div>
                    <p className="break-words text-xs text-muted-foreground">
                      {describe(ev)}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <div className="order-1 space-y-4 lg:order-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Session counters</CardTitle>
            <CardDescription>Since the page opened</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2 text-sm">
            <div className="rounded-md bg-muted px-3 py-2">
              <div className="text-lg font-semibold">{counters.outcomes}</div>
              <div className="text-xs text-muted-foreground">episodes done</div>
            </div>
            <div className="rounded-md bg-muted px-3 py-2">
              <div className="text-lg font-semibold text-emerald-700">
                {counters.outcomes > 0
                  ? Math.round((counters.successes / counters.outcomes) * 100)
                  : 0}
                %
              </div>
              <div className="text-xs text-muted-foreground">success</div>
            </div>
            <div className="rounded-md bg-muted px-3 py-2">
              <div className="text-lg font-semibold">{counters.diagnoses}</div>
              <div className="text-xs text-muted-foreground">diagnoses</div>
            </div>
            <div className="rounded-md bg-muted px-3 py-2">
              <div className="text-lg font-semibold text-violet-700">
                {counters.diagnoses > 0
                  ? Math.round((counters.correct / counters.diagnoses) * 100)
                  : 0}
                %
              </div>
              <div className="text-xs text-muted-foreground">diag correct</div>
            </div>
            <div className="col-span-2 rounded-md bg-amber-50 px-3 py-2">
              <div className="text-lg font-semibold text-amber-700">
                {counters.escalations}
              </div>
              <div className="text-xs text-amber-700/80">
                oracle-human escalations
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Pipeline stages</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs text-muted-foreground">
            <p>
              <AlertTriangle className="mr-1 inline h-3 w-3 text-rose-500" />
              Divergence — Mahalanobis D vs χ² gate (Bonferroni per step)
            </p>
            <p>
              <Stethoscope className="mr-1 inline h-3 w-3 text-violet-500" />
              Diagnosis — Bayes posterior + entropy-gated probes
            </p>
            <p>
              <Wrench className="mr-1 inline h-3 w-3 text-emerald-500" />
              Recovery — cost-optimized macro (α1 β0.5 γ20 δ60 ε0.01)
            </p>
            <p>
              <Zap className="mr-1 inline h-3 w-3 text-teal-600" />
              Outcome — verified state update, DAG invalidation
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
