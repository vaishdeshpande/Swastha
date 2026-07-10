import VoiceAssistant from "@/components/VoiceAssistant";

export default function Home() {
  return (
    <main style={{ padding: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "flex-end", maxWidth: 960, margin: "0 auto 0.5rem" }}>
        <a
          href="/admin"
          className="neo-btn"
          style={{
            padding: "6px 14px",
            fontSize: 11,
            color: "var(--neo-text-muted)",
            textDecoration: "none",
            display: "inline-block",
          }}
        >
          Admin →
        </a>
      </div>
      <VoiceAssistant />
    </main>
  );
}
