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

const PIE_COLORS = ["#3aab7a", "#5dba8a", "#8b90a8"];
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
    return (
      <div className="neo-card">
        <p className="neo-text" style={{ color: "var(--neo-red)" }}>Failed to load analytics: {error}</p>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="neo-card flex items-center justify-center" style={{ minHeight: 120 }}>
        <span className="neo-status-badge">
          <span className="neo-status-dot processing" />
          Loading analytics...
        </span>
      </div>
    );
  }

  const resolutionRate =
    data.total_calls > 0 ? Math.round((data.sentiment_avg > 0 ? data.sentiment_avg : 0) * 100) : 0;

  const languageData = Object.entries(data.language_breakdown).map(([lang, count]) => ({
    name: lang,
    value: count,
  }));

  const agentData = Object.entries(data.agent_activations).map(([agent, count]) => ({
    name: agent.replace(/_/g, " "),
    count,
  }));

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Calls (7d)" value={data.total_calls.toString()} />
        <StatCard label="Avg Duration" value={formatDuration(data.avg_duration_sec)} />
        <StatCard label="Resolution" value={`${resolutionRate}%`} />
        <StatCard label="Pending Follow-ups" value={data.pending_followups.toString()} accent />
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <div className="neo-card">
          <p className="neo-label">Language Breakdown</p>
          {languageData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <PieChart>
                <Pie data={languageData} dataKey="value" nameKey="name" outerRadius={80} label>
                  {languageData.map((_, i) => (
                    <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "var(--neo-bg)",
                    border: "none",
                    borderRadius: "var(--neo-radius-sm)",
                    boxShadow: "var(--neo-out-sm)",
                    color: "var(--neo-text)",
                    fontSize: 12,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="neo-text-muted">No call data yet.</p>
          )}
        </div>

        <div className="neo-card">
          <p className="neo-label">Agent Activations</p>
          {agentData.length > 0 ? (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={agentData}>
                <XAxis dataKey="name" tick={{ fontSize: 9, fill: "var(--neo-text-muted)" }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: "var(--neo-text-muted)" }} />
                <Tooltip
                  contentStyle={{
                    background: "var(--neo-bg)",
                    border: "none",
                    borderRadius: "var(--neo-radius-sm)",
                    boxShadow: "var(--neo-out-sm)",
                    color: "var(--neo-text)",
                    fontSize: 12,
                  }}
                />
                <Bar dataKey="count" fill="var(--neo-accent)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="neo-text-muted">No agent activity yet.</p>
          )}
        </div>
      </div>

      <div className="neo-card">
        <p className="neo-label">Recent Calls</p>
        <div className="neo-inset">
          <p className="neo-text-muted">
            Detailed per-call history requires a call-log listing endpoint (not yet exposed by the API).
          </p>
        </div>
      </div>

      <div className="neo-card">
        <p className="neo-label">Pending Follow-ups ({data.pending_followups})</p>
        <div className="neo-inset">
          <p className="neo-text-muted">
            Detailed follow-up listing requires a follow-up listing endpoint (not yet exposed by the API).
          </p>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="neo-card flex flex-col items-center gap-1 text-center">
      <p
        style={{
          fontSize: 26,
          fontWeight: 700,
          color: accent ? "var(--neo-accent)" : "var(--neo-text)",
          lineHeight: 1.2,
        }}
      >
        {value}
      </p>
      <p className="neo-text-muted" style={{ fontSize: 10 }}>
        {label}
      </p>
    </div>
  );
}
