// Session helper for API routes.
import { NextResponse } from "next/server";
import { getUserByToken, SESSION_COOKIE, type SafeUser } from "@/lib/auth";

export async function currentUser(req: Request): Promise<SafeUser | null> {
  const cookie = req.headers
    .get("cookie")
    ?.split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`));
  const token = cookie?.split("=")[1];
  return getUserByToken(token);
}

export function unauthorized() {
  return NextResponse.json(
    { error: "Sign in to use the RecoverGraph control room." },
    { status: 401 },
  );
}

export function badRequest(message: string) {
  return NextResponse.json({ error: message }, { status: 400 });
}

export function serverError(message: string, status = 500) {
  return NextResponse.json({ error: message }, { status });
}
