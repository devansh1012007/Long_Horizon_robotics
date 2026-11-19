import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, unauthorized } from "@/lib/session";
import { rateLimit, RULES } from "@/lib/rate-limit";

// Cross-job leaderboard: aggregate per-system metrics over every episode the
// user has recorded, with Wilson score intervals. Cached 10s per user.
type Agg = {
  n: number;
  successes: number;
  extraSum: number;
  recoverySum: number;
  recoveryN: number;
  diagN: number;
  diagSum: number;
  escalations: number;
  interventions: number;
  unsafe: number;
  falseAlarmEpisodes: number;
};

function wilson(successes: number, n: number): [number, number, number] {
  if (n === 0) return [0, 0, 0];
  const z = 1.959963984540054;
  const p = successes / n;
  const denom = 1 + (z * z) / n;
  const centre = p + (z * z) / (2 * n);
  const spread =
    z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n));
  return [
    p,
    Math.max(0, (centre - spread) / denom),
    Math.min(1, (centre + spread) / denom),
  ];
}

const g = globalThis as unknown as {
  __rgLeaderboardCache?: { at: number; data: unknown };
};

export async function GET(req: Request) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`read:${user.id}`, RULES.read);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const cache = g.__rgLeaderboardCache;
  if (cache && Date.now() - cache.at < 10_000 && cache.data) {
    return NextResponse.json(cache.data);
  }

  const grouped = await db.episodeRow.groupBy({
    by: ["system"],
    where: { userId: user.id },
    _count: { system: true, success: true },
    _sum: {
      extraActions: true,
      recoveryTimeS: true,
      escalations: true,
      interventions: true,
      unsafeExec: true,
    },
  });

  // diag + false-alarm need scalar aggregates per system (small data: fine).
  const systems: string[] = grouped.map((g2) => g2.system);
  const entries = await Promise.all(
    systems.map(async (sys) => {
      const agg: Agg = {
        n: 0,
        successes: 0,
        extraSum: 0,
        recoverySum: 0,
        recoveryN: 0,
        diagN: 0,
        diagSum: 0,
        escalations: 0,
        interventions: 0,
        unsafe: 0,
        falseAlarmEpisodes: 0,
      };
      const eps = await db.episodeRow.findMany({
        where: { userId: user.id, system: sys },
        select: {
          success: true,
          extraActions: true,
          recoveryTimeS: true,
          diagCorrect: true,
          escalations: true,
          interventions: true,
          unsafeExec: true,
          falseAlarms: true,
        },
      });
      for (const e of eps) {
        agg.n += 1;
        if (e.success) agg.successes += 1;
        agg.extraSum += e.extraActions;
        if (e.recoveryTimeS > 0) {
          agg.recoverySum += e.recoveryTimeS;
          agg.recoveryN += 1;
        }
        if (e.diagCorrect !== null && e.diagCorrect !== undefined) {
          agg.diagN += 1;
          agg.diagSum += e.diagCorrect;
        }
        agg.escalations += e.escalations;
        agg.interventions += e.interventions;
        agg.unsafe += e.unsafeExec;
        if (e.falseAlarms > 0) agg.falseAlarmEpisodes += 1;
      }
      const [rate, lo, hi] = wilson(agg.successes, agg.n);
      return {
        system: sys,
        n: agg.n,
        successes: agg.successes,
        successRate: Number(rate.toFixed(4)),
        ciLo: Number(lo.toFixed(4)),
        ciHi: Number(hi.toFixed(4)),
        extraActionsMean:
          agg.n > 0 ? Number((agg.extraSum / agg.n).toFixed(2)) : 0,
        recoveryTimeMean:
          agg.recoveryN > 0
            ? Number((agg.recoverySum / agg.recoveryN).toFixed(3))
            : 0,
        diagN: agg.diagN,
        diagAccuracy:
          agg.diagN > 0 ? Number((agg.diagSum / agg.diagN).toFixed(3)) : null,
        escalations: agg.escalations,
        interventions: agg.interventions,
        unsafeExecutions: agg.unsafe,
        falseAlarmEpisodeRate:
          agg.n > 0
            ? Number((agg.falseAlarmEpisodes / agg.n).toFixed(3))
            : 0,
      };
    }),
  );
  entries.sort((a, b) => b.successRate - a.successRate);

  const totalEpisodes = await db.episodeRow.count({
    where: { userId: user.id },
  });
  const data = { systems: entries, totalEpisodes };
  g.__rgLeaderboardCache = { at: Date.now(), data };
  return NextResponse.json(data);
}
