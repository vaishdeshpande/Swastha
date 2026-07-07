"use client";

import { useEffect, useState } from "react";
import {
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { getBackendUrl } from "@/lib/livekit";
import { createLogger } from "@/lib/logger";

const log = createLogger("component/AdminDashboard");

interface CallAnalytics {
  total_calls: number;
  avg_duration_sec: number;
  language_breakdown: Record<string, number>;
  agent_activations: Record<string, number>;
  sentiment_avg: number;
  pending_followups: number;
  escalations_today: number;
}

const PIE_COLORS = ["#2563eb", "#9333ea", "#94a3b8"];
const REFRESH_MS = 30_000;

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function AdminDashboard() {
  const [data, setData] = useState<CallAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      log.debug("Fetching analytics data");
      try {
        const url = `${getBackendUrl()}/api/analytics/calls?days=7`;
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Request failed: ${res.status}`);
        const json = await res.json();
        if (!cancelled) {
          log.info("Analytics data loaded", {
            totalCalls: json.total_calls,
            pendingFollowups: json.pending_followups,
          });
          setData(json);
          setError(null);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load analytics";
        if (!cancelled) {
          log.error("Failed to fetch analytics", { error: message });
          setError(message);
        }
      }
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (error) {
    return <p className="text-sm text-red-600">Failed to load analytics: {error}</p>;
  }

  if (!data) {
    return <p className="text-sm text-gray-500">Loading analytics...</p>;
  }

  const resolutionRate =
    data.total_calls > 0 ? Math.round((data.sentiment_avg > 0 ? data.sentiment_avg : 0) * 100) : 0;

  const languageData = Object.entries(data.language_breakdown).map(([lang, count]) => ({
    name: lang,
    value: count,
  }));

  const agentData = Object.entries(data.agent_activations).map(([agent, count]) => ({
    name: agent,
    count,
  }));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Calls (7d)" value={data.total_calls.toString()} />
        <StatCard label="Avg Duration" value={formatDuration(data.avg_duration_sec)} />
        <StatCard label="Resolution" value={`${resolutionRate}%`} />
        <StatCard label="Pending Follow-ups" value={data.pending_followups.toString()} />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="rounded-lg bg-gray-50 p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Language Breakdown</h3>
          {languageData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={languageData} dataKey="value" nameKey="name" outerRadius={80} label>
                  {languageData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400">No call data yet.</p>
          )}
        </div>

        <div className="rounded-lg bg-gray-50 p-4 shadow-sm">
          <h3 className="mb-2 text-sm font-semibold text-gray-700">Agent Activations</h3>
          {agentData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={agentData}>
                <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill="#2563eb" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-sm text-gray-400">No agent activity yet.</p>
          )}
        </div>
      </div>

      <div className="rounded-lg bg-gray-50 p-4 shadow-sm">
        <h3 className="mb-2 text-sm font-semibold text-gray-700">Recent Calls</h3>
        <p className="text-sm text-gray-400">
          Detailed per-call history requires a call-log listing endpoint (not yet exposed by the API).
        </p>
      </div>

      <div className="rounded-lg bg-gray-50 p-4 shadow-sm">
        <h3 className="mb-2 text-sm font-semibold text-gray-700">
          Pending Follow-ups ({data.pending_followups})
        </h3>
        <p className="text-sm text-gray-400">
          Detailed follow-up listing requires a follow-up listing endpoint (not yet exposed by the API).
        </p>
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-gray-50 p-4 text-center shadow-sm">
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className="text-xs text-gray-500">{label}</p>
    </div>
  );
}
