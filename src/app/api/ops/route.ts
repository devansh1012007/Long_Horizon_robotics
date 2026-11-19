import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, unauthorized } from "@/lib/session";
import { EngineError, engine } from "@/lib/engine-client";
import { engineStatus } from "@/lib/engine-supervisor";
import { rateLimit, RULES } from "@/lib/rate-limit";

export async function GET(req: Request) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`read:${user.id}`, RULES.read);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const [users, jobs, episodes, doneJobs] = await Promise.all([
    db.user.count(),
    db.job.count(),
    db.episodeRow.count(),
    db.job.count({ where: { status: "done" } }),
  ]);
  let engineHealth: Awaited<ReturnType<typeof engine.health>> | null = null;
  let engineError: string | null = null;
  try {
    engineHealth = await engine.health();
  } catch (e) {
    engineError =
      e instanceof EngineError ? e.message : "Engine sidecar unreachable.";
  }
  return NextResponse.json({
    engine: engineHealth
      ? { ...engineHealth, sidecar: engineStatus() }
      : { status: "unreachable", sidecar: engineStatus(), error: engineError },
    platform: {
      users,
      jobs,
      doneJobs,
      episodes,
      uptimeS: Math.round(process.uptime()),
      node: process.version,
    },
  });
}
