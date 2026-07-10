import AdminDashboard from "@/components/AdminDashboard";

export default function AdminPage() {
  return (
    <main style={{ padding: "2rem 1rem" }}>
      <div className="neo-screen" style={{ maxWidth: 960 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: "1.5rem" }}>
          <span style={{ fontSize: 24 }}>📊</span>
          <h1 style={{ fontSize: 18, fontWeight: 700, color: "var(--neo-text)" }}>Admin Dashboard</h1>
          <a
            href="/"
            className="neo-btn"
            style={{
              marginLeft: "auto",
              padding: "6px 14px",
              fontSize: 12,
              color: "var(--neo-text-muted)",
              textDecoration: "none",
            }}
          >
            ← Back to App
          </a>
        </div>
        <AdminDashboard />
      </div>
    </main>
  );
}
