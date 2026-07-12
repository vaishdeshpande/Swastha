// Server-side proxy — use BACKEND_URL (not exposed to browser) with fallback
function getServerBackendUrl() {
  return (
    process.env.BACKEND_URL ||
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    "http://localhost:8000"
  );
}

export async function POST(req: Request) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json({ detail: "Invalid request body" }, { status: 400 });
  }

  const backendUrl = `${getServerBackendUrl()}/api/demo/outbound/reply`;
  let res: Response;
  try {
    res = await fetch(backendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    console.error("[proxy/outbound/reply] fetch failed:", err);
    return Response.json({ detail: `Backend unreachable: ${String(err)}` }, { status: 502 });
  }

  let data: unknown;
  try {
    data = await res.json();
  } catch (err) {
    console.error("[proxy/outbound/reply] res.json() failed:", err);
    return Response.json({ detail: "Backend returned non-JSON response" }, { status: 502 });
  }

  return Response.json(data, { status: res.status });
}
