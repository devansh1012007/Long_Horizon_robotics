"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AuthDialog } from "@/components/dashboard/auth-dialog";
import { ControlRoom, SubmitPayload } from "@/components/dashboard/control-room";
import { EpisodeExplorer } from "@/components/dashboard/episode-explorer";
import { Leaderboard } from "@/components/dashboard/leaderboard";
import { LiveTelemetry } from "@/components/dashboard/live-telemetry";
import { OpsPanel } from "@/components/dashboard/ops-panel";
import { useDashboard, type TabId } from "@/components/dashboard/store";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";
import {
  Activity,
  ChevronDown,
  Database,
  HeartPulse,
  LogOut,
  Radio,
  Table2,
  Timer,
  Trophy,
  Waypoints,
} from "lucide-react";
import {
  LeaderboardEntry,
  PairedStat,
  SystemSummary,
  JobView,
} from "@/lib/dashboard-constants";

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: "control", label: "Control Room", icon: <Waypoints className="h-4 w-4" /> },
  { id: "live", label: "Live Telemetry", icon: <Radio className="h-4 w-4" /> },
  { id: "leaderboard", label: "Leaderboard", icon: <Trophy className="h-4 w-4" /> },
  { id: "episodes", label: "Episodes", icon: <Table2 className="h-4 w-4" /> },
  { id: "ops", label: "Ops", icon: <Activity className="h-4 w-4" /> },
];

export default function Home() {
  const { tab, setTab, user, setUser, activeJobId, setActiveJobId, authOpen, setAuthOpen } =
    useDashboard();
  const [jobs, setJobs] = useState<JobView[]>([]);
  const [detailed, setDetailed] = useState<
    Record<string, { summary: Record<string, SystemSummary>; stats: PairedStat[] } | undefined>
  >({});
  const [lb, setLb] = useState<{ systems: LeaderboardEntry[]; totalEpisodes: number }>({
    systems: [],
    totalEpisodes: 0,
  });
  const [lbKey, setLbKey] = useState(0);
  const [ops, setOps] = useState<Parameters<typeof OpsPanel>[0]["ops"]>(null);
  const knownStatus = useRef<Record<string, string>>({});

  // ---- session ----
  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => r.json())
      .then((d) => setUser(d.user ?? null))
      .catch(() => {});
  }, [setUser]);

  // ---- job polling + result sync ----
  const requestedDetailed = useRef<Set<string>>(new Set());
  const refreshJobs = useCallback(async () => {
    const res = await fetch("/api/jobs");
    if (!res.ok) return;
    const data = await res.json();
    const list: JobView[] = data.jobs;
    setJobs(list);
    let finished = false;
    for (const j of list) {
      const prev = knownStatus.current[j.id];
      if (j.status === "done" && prev && prev !== "done") {
        finished = true;
        toast.success(`Batch "${j.label}" finished.`);
      }
      knownStatus.current[j.id] = j.status;
      // fetch full results once for every finished job (reload-safe)
      if (j.status === "done" && !requestedDetailed.current.has(j.id)) {
        requestedDetailed.current.add(j.id);
        fetch(`/api/jobs/${j.id}/result`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d) => {
            if (d) {
              setDetailed((prev2) => ({
                ...prev2,
                [j.id]: { summary: d.summary, stats: d.stats ?? [] },
              }));
              setLbKey((k) => k + 1);
            }
          });
      }
    }
    if (finished) setLbKey((k) => k + 1);
  }, []);

  useEffect(() => {
    if (!user) return;
    const first = setTimeout(() => void refreshJobs(), 0);
    const t = setInterval(() => void refreshJobs(), 2000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [user, refreshJobs]);

  // ---- ops heartbeat ----
  const refreshOps = useCallback(async () => {
    const res = await fetch("/api/ops");
    if (res.ok) setOps(await res.json());
  }, []);

  useEffect(() => {
    const first = setTimeout(() => void refreshOps(), 0);
    const t = setInterval(() => void refreshOps(), 15000);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [refreshOps]);

  // ---- leaderboard ----
  useEffect(() => {
    if (!user) return;
    fetch("/api/leaderboard")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d) setLb({ systems: d.systems, totalEpisodes: d.totalEpisodes });
      });
  }, [user, lbKey, tab]);

  // ---- submit / delete ----
  const submitJob = useCallback(
    async (payload: SubmitPayload): Promise<boolean> => {
      const res = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast.error(data.error ?? "Submission failed.");
        return false;
      }
      await refreshJobs();
      setActiveJobId(data.job_id);
      return true;
    },
    [refreshJobs, setActiveJobId],
  );

  const deleteJob = useCallback(
    async (id: string) => {
      const res = await fetch(`/api/jobs/${id}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        toast.error(data.error ?? "Delete failed.");
        return;
      }
      toast.success("Batch removed.");
      await refreshJobs();
    },
    [refreshJobs],
  );

  const latestStats: PairedStat[] = (() => {
    const done = jobs.find((j) => j.stats && j.stats.length > 0);
    return done?.stats ?? [];
  })();

  const engineOk = ops?.engine?.status === "ok";
  const activeJobs = jobs.filter((j) => j.status === "running" || j.status === "queued").length;

  function watch(id: string) {
    setActiveJobId(id);
    setTab("live");
  }

  return (
    <div className="flex min-h-screen flex-col bg-zinc-50/60">
      {/* Header */}
      <header className="sticky top-0 z-40 border-b bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-emerald-600 text-white">
              <Waypoints className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-sm font-bold leading-tight sm:text-base">
                RecoverGraph Console
              </h1>
              <p className="text-[11px] leading-tight text-muted-foreground">
                causal recovery layer · long-horizon manipulation
              </p>
            </div>
          </div>

          <div className="ml-2 hidden items-center gap-2 sm:flex">
            <Badge
              variant="outline"
              className={engineOk ? "border-emerald-300 bg-emerald-50 text-emerald-700" : "border-amber-300 bg-amber-50 text-amber-700"}
            >
              <HeartPulse className="mr-1 h-3 w-3" />
              engine {engineOk ? "online" : "starting"}
            </Badge>
            {activeJobs > 0 && (
              <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-700">
                <Timer className="mr-1 h-3 w-3" />
                {activeJobs} active batch{activeJobs > 1 ? "es" : ""}
              </Badge>
            )}
          </div>

          <div className="ml-auto flex items-center gap-2">
            {user ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="gap-1.5">
                    <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-xs font-bold text-emerald-800">
                      {user.name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="hidden max-w-32 truncate sm:inline">{user.name}</span>
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuLabel className="text-xs">
                    {user.email}
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={async () => {
                      await fetch("/api/auth/logout", { method: "POST" });
                      setUser(null);
                      setJobs([]);
                      setLb({ systems: [], totalEpisodes: 0 });
                      toast.success("Signed out.");
                    }}
                  >
                    <LogOut className="mr-2 h-4 w-4" /> Sign out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button
                size="sm"
                className="bg-emerald-600 text-white hover:bg-emerald-700"
                onClick={() => setAuthOpen(true)}
              >
                Sign in
              </Button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <nav className="mx-auto max-w-7xl px-4 sm:px-6" aria-label="Dashboard sections">
          <div className="flex gap-1 overflow-x-auto pb-2">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                aria-current={tab === t.id ? "page" : undefined}
                className={`flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  tab === t.id
                    ? "bg-emerald-600 text-white"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                }`}
              >
                {t.icon}
                {t.label}
              </button>
            ))}
          </div>
        </nav>
      </header>

      {/* Main */}
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-4 sm:px-6">
        {!user ? (
          <div className="mx-auto mt-10 max-w-xl text-center">
            <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-600 text-white">
              <Waypoints className="h-7 w-7" />
            </div>
            <h2 className="text-xl font-bold">
              Sign in to open the control room
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              RecoverGraph runs evaluation batches of the validated
              Milestone-1 engine: Mahalanobis divergence detection, Bayesian
              fault diagnosis, cost-optimized recovery macros, task-graph
              invalidation and safe escalation — benchmarked against five
              baselines and five ablations.
            </p>
            <div className="mt-4 grid gap-2 text-left text-sm">
              {[
                ["Control Room", "compose batches: systems × faults × horizons × seeds, guarded by per-user quotas"],
                ["Live Telemetry", "WebSocket rooms streaming divergence → diagnosis → recovery → outcome"],
                ["Leaderboard", "Wilson CIs and paired bootstrap / McNemar / Wilcoxon statistics"],
                ["Episodes", "persistent episode database with full timelines"],
                ["Ops", "engine pool health, queue depth, rate limits"],
              ].map(([t, d]) => (
                <div key={t} className="flex gap-2 rounded-lg border bg-white p-3">
                  <Database className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                  <p>
                    <span className="font-medium">{t}</span> — {d}
                  </p>
                </div>
              ))}
            </div>
            <Button
              className="mt-6 bg-emerald-600 text-white hover:bg-emerald-700"
              onClick={() => setAuthOpen(true)}
            >
              Sign in / Register
            </Button>
            <p className="mt-2 text-xs text-muted-foreground">
              Demo operator: demo@recovergraph.dev · demo1234
            </p>
          </div>
        ) : (
          <>
            {tab === "control" && (
              <ControlRoom
                jobs={jobs}
                detailed={detailed}
                onSubmit={submitJob}
                onDelete={deleteJob}
                onWatch={watch}
              />
            )}
            {tab === "live" && (
              <LiveTelemetry
                jobs={jobs}
                activeJobId={activeJobId}
                setActiveJobId={setActiveJobId}
              />
            )}
            {tab === "leaderboard" && (
              <Leaderboard
                entries={lb.systems}
                totalEpisodes={lb.totalEpisodes}
                latestStats={latestStats}
                refreshKey={lbKey}
              />
            )}
            {tab === "episodes" && <EpisodeExplorer />}
            {tab === "ops" && <OpsPanel ops={ops} onRefresh={() => void refreshOps()} />}
          </>
        )}
      </main>

      {/* Footer (sticky) */}
      <footer className="mt-auto border-t bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-2 px-4 py-3 text-xs text-muted-foreground sm:px-6">
          <p>
            RecoverGraph Console · Milestone-1 engine (211/211 tests) · spec
            v0.1 · kinematic backend
          </p>
          <p>
            Wilson CIs · paired bootstrap 10k · McNemar · Wilcoxon · Holm-
            Bonferroni — statistics mirror the pre-registered plan
          </p>
        </div>
      </footer>

      <AuthDialog />
    </div>
  );
}
