// Shared dashboard metadata mirrored from the engine capabilities.
// (The engine remains the source of truth at runtime; these constants keep
// the UI instant and usable even before the engine finishes booting.)

export type SystemMeta = {
  id: string;
  name: string;
  family: "causal" | "baseline" | "ablation";
  desc: string;
};

export const SYSTEMS: SystemMeta[] = [
  {
    id: "RG",
    name: "RecoverGraph",
    family: "causal",
    desc: "Full stack: Mahalanobis divergence, Bayes diagnosis, cost-optimized recovery, DAG invalidation, loop guard.",
  },
  {
    id: "B1",
    name: "B1 — No recovery",
    family: "baseline",
    desc: "Low-level skills only; divergence ignored.",
  },
  {
    id: "B2",
    name: "B2 — Fixed retry",
    family: "baseline",
    desc: "Blind retry up to 3 attempts.",
  },
  {
    id: "B3",
    name: "B3 — Full replan",
    family: "baseline",
    desc: "Replan from the current observed state on every divergence.",
  },
  {
    id: "B4",
    name: "B4 — Verification orchestrator",
    family: "baseline",
    desc: "LADDER-style hard effect verification + re-observe/retry.",
  },
  {
    id: "B5",
    name: "B5 — Fault pipeline",
    family: "baseline",
    desc: "Classic threshold fault pipeline; strongest baseline.",
  },
  {
    id: "A1",
    name: "A1 — No diagnosis",
    family: "ablation",
    desc: "RG without the Bayes diagnoser.",
  },
  {
    id: "A2",
    name: "A2 — No invalidation",
    family: "ablation",
    desc: "RG without dependency-graph invalidation.",
  },
  {
    id: "A3",
    name: "A3 — Point estimates",
    family: "ablation",
    desc: "Detection without uncertainty (no Mahalanobis).",
  },
  {
    id: "A4",
    name: "A4 — Fixed priority",
    family: "ablation",
    desc: "RG with fixed-priority recovery instead of cost optimization.",
  },
  {
    id: "A5",
    name: "A5 — Learned likelihood",
    family: "ablation",
    desc: "RG with learned likelihood artifact (honest fallback).",
  },
  {
    id: "A6",
    name: "A6 — Double noise (RG)",
    family: "ablation",
    desc: "RG under the pre-registered doubled perception-noise model.",
  },
];

export const FAULTS = [
  "f_none",
  "f_miss",
  "f_slip",
  "f_pose",
  "f_collision",
  "f_perception",
  "f_controller",
  "stale_dependency",
] as const;

export const FAULT_LABELS: Record<string, string> = {
  f_none: "No fault (control)",
  f_miss: "Grasp miss (offset)",
  f_slip: "In-grip slip",
  f_pose: "Object pose shift",
  f_collision: "Collision / blocked",
  f_perception: "Perception dropout / noise",
  f_controller: "Controller tracking fault",
  stale_dependency: "Stale dependency (moved object)",
};

export const CONDITIONS = [
  { id: "none", label: "Fault-free" },
  { id: "single", label: "Single fault" },
  { id: "compound", label: "Compound faults" },
];

export const HORIZONS = [1, 2, 3, 5, 8];

export const SYSTEM_COLORS: Record<string, string> = {
  RG: "#10b981", // emerald
  B1: "#a8a29e", // stone
  B2: "#f59e0b", // amber
  B3: "#f97316", // orange
  B4: "#8b5cf6", // violet
  B5: "#ef4444", // red
  A1: "#14b8a6",
  A2: "#06b6d4",
  A3: "#eab308",
  A4: "#d946ef",
  A5: "#84cc16",
  A6: "#64748b",
};

export type SystemSummary = {
  n: number;
  successes?: number;
  success_rate?: number;
  ci_lo?: number;
  ci_hi?: number;
  extra_actions_mean?: number;
  recovery_time_mean?: number;
  diag_n?: number;
  diag_accuracy?: number | null;
  escalations?: number;
  interventions?: number;
  unsafe_executions?: number;
  false_alarm_episode_rate?: number;
};

export type PairedStat = {
  vs: string;
  n_pairs: number;
  delta_success_mean: number;
  delta_ci_lo: number;
  delta_ci_hi: number;
  ci_excludes_zero: boolean;
  mcnemar_p: number;
  wilcoxon_p: number;
};

export type JobView = {
  id: string;
  label: string;
  spec: {
    systems: string[];
    horizons: number[];
    condition: string;
    fault: string | null;
    episodes: number;
    base_seed: number;
    label: string;
  };
  status: "queued" | "running" | "done" | "failed";
  progress: number;
  total: number;
  summary: Record<string, SystemSummary> | null;
  stats: PairedStat[] | null;
  error: string | null;
  createdAt?: string | number;
  finishedAt?: string | number | null;
};

export type LeaderboardEntry = {
  system: string;
  n: number;
  successes: number;
  successRate: number;
  ciLo: number;
  ciHi: number;
  extraActionsMean: number;
  recoveryTimeMean: number;
  diagN: number;
  diagAccuracy: number | null;
  escalations: number;
  interventions: number;
  unsafeExecutions: number;
  falseAlarmEpisodeRate: number;
};

export type EpisodeRowView = {
  id: string;
  jobId: string;
  jobLabel: string;
  epId: string;
  system: string;
  condition: string;
  horizon: number;
  seed: number;
  fault: string;
  outcome: string;
  success: boolean;
  extraActions: number;
  recoveryTimeS: number;
  diagCorrect: number | null;
  escalations: number;
  interventions: number;
};

export type EpisodeDetail = {
  episode: Record<string, unknown>;
  metrics: Record<string, number | boolean | string>;
  events: { t: number; node: string; etype: string; payload: Record<string, unknown> }[];
  diagnoses: (Record<string, unknown> & {
    step: number;
    argmax: string;
    truth: string;
  })[];
  recoveries: {
    step: number;
    chosen: string;
    result: string;
    cost_terms: Record<string, number>;
  }[];
};

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}

export function faultShort(fault: string): string {
  if (!fault || fault === "none") return "none";
  return fault
    .split("|")
    .map((f) => f.replace("stale_dependency", "stale-dep").replace("f_", ""))
    .join("+");
}
