import { NextResponse } from "next/server";
import { listRecentJobs } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export async function GET() {
  const jobs = await listRecentJobs(30);
  return NextResponse.json({ jobs });
}
