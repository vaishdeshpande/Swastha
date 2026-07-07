import { getBackendUrl } from "@/lib/livekit";
import { createLogger } from "@/lib/logger";

const log = createLogger("api/token");

export async function POST(req: Request) {
  const body = await req.json();
  const { room, participant, preferred_lang } = body;

  log.info("Token request received", { room, participant, preferred_lang });

  const backendUrl = `${getBackendUrl()}/api/token`;
  log.debug("Forwarding token request to backend", { url: backendUrl });

  let res: Response;
  try {
    res = await fetch(backendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room_name: room,
        participant_name: participant,
        preferred_lang: preferred_lang ?? "auto",
      }),
    });
  } catch (err) {
    log.error("Network error reaching backend", { error: String(err) });
    return Response.json({ error: "Backend unreachable" }, { status: 502 });
  }

  if (!res.ok) {
    log.error("Backend returned error status", { status: res.status, room, participant });
    return Response.json({ error: "Failed to fetch token from backend" }, { status: res.status });
  }

  const data = await res.json();
  log.info("Token issued successfully", { room, participant });
  return Response.json(data);
}
