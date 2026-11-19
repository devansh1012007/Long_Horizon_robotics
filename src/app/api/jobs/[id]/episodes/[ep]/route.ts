import { NextResponse } from "next/server";
import { db } from "@/lib/db";
import { currentUser, unauthorized } from "@/lib/session";
import { EngineError, engine } from "@/lib/engine-client";
import { rateLimit, RULES } from "@/lib/rate-limit";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string; ep: string }> },
) {
  const user = await currentUser(req);
  if (!user) return unauthorized();
  const rl = rateLimit(`read:${user.id}`, RULES.read);
  if (!rl.ok) {
    return NextResponse.json({ error: "Rate limit." }, { status: 429 });
  }
  const { id, ep } = await params;
  const owned = await db.job.findUnique({
    where: { id },
    select: { userId: true },
  });
  if (!owned || owned.userId !== user.id) {
    return NextResponse.json({ error: "Job not found." }, { status: 404 });
  }
  try {
    return NextResponse.json(await engine.episodeDetail(id, ep));
  } catch (e) {
    if (e instanceof EngineError) {
      return NextResponse.json({ error: e.message }, { status: e.status });
    }
    return NextResponse.json({ error: "Engine unreachable." }, { status: 503 });
  }
}
