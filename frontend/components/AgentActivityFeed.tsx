"use client";

import { AGENT_ORDER } from "@/lib/types";

interface AgentActivityFeedProps {
  activeAgent: string | null;
  completedAgents: string[];
}

export default function AgentActivityFeed({ activeAgent, completedAgents }: AgentActivityFeedProps) {
  return (
    <div className="neo-card">
      <p className="neo-label">Agent Activity</p>
      <div className="flex flex-col gap-1">
        {AGENT_ORDER.map(({ key, label, sublabel }) => {
          const isActive = activeAgent === key;
          const isDone = completedAgents.includes(key) && !isActive;
          const state = isActive ? "active" : isDone ? "done" : "pending";
          return (
            <div key={key} className={`neo-agent-row ${state}`}>
              <span className={`neo-agent-dot ${state}`} />
              <div>
                <div className="neo-agent-name">{label}</div>
                {isActive && <div className="neo-agent-sub">{sublabel}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
