import { NextResponse } from "next/server";
import { currentUser } from "@/lib/session";

export async function GET(req: Request) {
  const user = await currentUser(req);
  return NextResponse.json({ user });
}
