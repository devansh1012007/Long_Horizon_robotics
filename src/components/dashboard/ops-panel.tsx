"use client";

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { toast } from "sonner";
import {
  Activity,
  Cpu,
  Database,
  HeartPulse,
  KeyRound,
  RefreshCw,
  Server,
  Users,
} from "lucide-react";

type OpsData = {
  engine: {
    status: string;
    version?: string;
    uptime_s?: number;
    pool?: { workers: number; active: number; queued: number };
    jobs?: Record<string, number>;
    episodes_recorded?: number;
    limits?: Record<string, number>;
    sidecar?: { pid: number | null; port: number };
    error?: string;
  };
  platform: {
    users: number;
    jobs: number;
    doneJobs: number;
    episodes: number;
    uptimeS: number;
    node: string;
  };
};

function uptime(seconds: number | undefined): string {
  if (!seconds && seconds !== 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function OpsPanel({ ops, onRefresh }: { ops: OpsData | null; onRefresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const engine = ops?.engine;
  const plat = ops?.platform;
  const pool = engine?.pool;
  const workerPct = pool ? ((pool.active / Math.max(1, pool.workers)) * 100) : 0;

  async function refresh() {
    setBusy(true);
    onRefresh();
    setTimeout(() => setBusy(false), 600);
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <HeartPulse
                className={`h-5 w-5 ${engine?.status === "ok" ? "text-emerald-600" : "text-destructive"}`}
              />
              Engine service
              <Badge
                className={
                  engine?.status === "ok"
                    ? "bg-emerald-100 text-emerald-800 hover:bg-emerald-100"
                    : "bg-destructive text-white"
                }
              >
                {engine?.status ?? "unreachable"}
              </Badge>
            </CardTitle>
            <CardDescription>
              {engine?.version ?? engine?.error ?? "Python sidecar wrapping the validated Milestone-1 engine."}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{pool?.workers ?? "—"}</div>
                <div className="text-xs text-muted-foreground">workers</div>
              </div>
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{pool?.active ?? "—"}</div>
                <div className="text-xs text-muted-foreground">active</div>
              </div>
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{pool?.queued ?? "—"}</div>
                <div className="text-xs text-muted-foreground">queued</div>
              </div>
            </div>
            {pool && (
              <div>
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>worker utilization</span>
                  <span>{Math.round(workerPct)}%</span>
                </div>
                <Progress value={workerPct} className="h-2" />
              </div>
            )}
            <div className="space-y-1 text-sm text-muted-foreground">
              <p className="flex items-center gap-2">
                <Server className="h-4 w-4" /> sidecar PID{" "}
                {engine?.sidecar?.pid ?? "—"} on 127.0.0.1:{engine?.sidecar?.port ?? 3031}
                {engine?.uptime_s !== undefined && ` · uptime ${uptime(engine.uptime_s)}`}
              </p>
              <p className="flex items-center gap-2">
                <Database className="h-4 w-4" /> episodes in engine stores:{" "}
                {engine?.episodes_recorded ?? "—"}
              </p>
              {engine?.jobs && (
                <p className="text-xs">
                  jobs: {Object.entries(engine.jobs)
                    .filter(([, v]) => v > 0)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(" · ") || "none yet"}
                </p>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={refresh} disabled={busy}>
              <RefreshCw className={`mr-2 h-4 w-4 ${busy ? "animate-spin" : ""}`} />
              Re-check health
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Cpu className="h-5 w-5 text-emerald-600" /> Gateway platform
            </CardTitle>
            <CardDescription>
              Next.js gateway: auth, quotas, rate limits, persistent episode DB.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-2 text-center">
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="flex items-center justify-center gap-1 text-xl font-semibold">
                  <Users className="h-4 w-4 text-muted-foreground" />
                  {plat?.users ?? "—"}
                </div>
                <div className="text-xs text-muted-foreground">operators</div>
              </div>
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{plat?.jobs ?? "—"}</div>
                <div className="text-xs text-muted-foreground">batches</div>
              </div>
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{plat?.episodes ?? "—"}</div>
                <div className="text-xs text-muted-foreground">episodes (all users)</div>
              </div>
              <div className="rounded-md bg-muted px-2 py-3">
                <div className="text-xl font-semibold">{plat?.doneJobs ?? "—"}</div>
                <div className="text-xs text-muted-foreground">completed batches</div>
              </div>
            </div>
            <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
              <KeyRound className="h-4 w-4" /> gateway uptime {uptime(plat?.uptimeS)} ·
              node {plat?.node ?? "—"}
            </p>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Activity className="h-5 w-5 text-emerald-600" /> Architecture
          </CardTitle>
          <CardDescription>
            How a request flows, and the guardrails that make this
            multi-user-safe.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-sm text-muted-foreground">
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs leading-relaxed">
{`browser ──► Next.js gateway (:3000)
              │  auth (scrypt + HttpOnly sessions) · rate limits · per-user quotas
              │  Prisma (SQLite): users · sessions · batches · episode rows
              ├──► engine sidecar (python, :3031) — spawned & supervised by the gateway
              │      rg_core + rgsim + systems (Milestone-1 engine, untouched)
              │      3-worker pool · job registry · WebSocket telemetry · episode SQLite
              └──► socket.io ◄── live telemetry rooms (per job)`}
          </pre>
          <ul className="list-inside list-disc space-y-1">
            <li>
              Bounded engine pool (3 workers, ≤48 queued) — a burst of users
              degrades queue latency, never crashes the engine.
            </li>
            <li>
              Per-user quota of 2 active batches; ≤600 episodes per batch;
              ≤100 episodes per cell.
            </li>
            <li>
              Token-bucket rate limits on auth (15/5min), submissions (20/min)
              and reads (240/min), keyed per user/IP.
            </li>
            <li>
              Results are written through to the gateway DB, so history and
              the leaderboard survive engine pruning and restarts.
            </li>
            <li>
              The engine service spawns once and is health-checked; if it
              dies, the gateway restarts it on the next request.
            </li>
          </ul>
          <p className="text-xs">
            Engine contract: identical skills, perception noise and seeds across
            systems; only divergence handling differs (spec 10.3). Statistics:
            Wilson CIs, paired bootstrap (10k), McNemar, Wilcoxon.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Load playbook</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          <p>
            Expecting heavy usage? Batch sizes keep episodes cheap (~5 ms each):
            10 operators submitting 300-episode batches back-to-back still run
            through the 3-worker pool in seconds each. Queue depth and worker
            utilization above are the two numbers to watch; if the queue sits
            above ~20, raise the pool size in
            <code className="mx-1 rounded bg-muted px-1 py-0.5 text-xs">
              engine/core.py
            </code>
            (LIMITS.workers) — no gateway changes needed.
          </p>
          <Button
            variant="ghost"
            size="sm"
            className="mt-2 px-2"
            onClick={() =>
              toast.info("Ops auto-refreshes with the header heartbeat.")
            }
          >
            Why did my job go to queued?
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
