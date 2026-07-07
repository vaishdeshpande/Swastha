type LogLevel = "debug" | "info" | "warn" | "error";

const LEVEL_RANK: Record<LogLevel, number> = { debug: 0, info: 1, warn: 2, error: 3 };

const COLORS: Record<LogLevel, string> = {
  debug: "#94a3b8",
  info:  "#2563eb",
  warn:  "#d97706",
  error: "#dc2626",
};

function getMinLevel(): LogLevel {
  if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_LOG_LEVEL) {
    return process.env.NEXT_PUBLIC_LOG_LEVEL as LogLevel;
  }
  return process.env.NODE_ENV === "production" ? "warn" : "debug";
}

function shouldLog(level: LogLevel): boolean {
  return LEVEL_RANK[level] >= LEVEL_RANK[getMinLevel()];
}

function emit(level: LogLevel, namespace: string, message: string, data?: unknown) {
  if (!shouldLog(level)) return;

  const ts = new Date().toISOString();
  const prefix = `[${ts}] [${namespace}]`;

  const logFn =
    level === "error" ? console.error
    : level === "warn"  ? console.warn
    : level === "debug" ? console.debug
    : console.info;

  if (typeof window !== "undefined") {
    // Browser: use %c for colored namespace tag
    logFn(
      `%c${level.toUpperCase()} %c${prefix} %c${message}`,
      `color:${COLORS[level]};font-weight:bold`,
      "color:#64748b",
      "color:inherit",
      ...(data !== undefined ? [data] : []),
    );
  } else {
    // Server-side (API routes): plain text
    const dataStr = data !== undefined ? ` ${JSON.stringify(data)}` : "";
    logFn(`${level.toUpperCase()} ${prefix} ${message}${dataStr}`);
  }
}

export interface Logger {
  debug(message: string, data?: unknown): void;
  info(message: string, data?: unknown): void;
  warn(message: string, data?: unknown): void;
  error(message: string, data?: unknown): void;
}

export function createLogger(namespace: string): Logger {
  return {
    debug: (msg, data) => emit("debug", namespace, msg, data),
    info:  (msg, data) => emit("info",  namespace, msg, data),
    warn:  (msg, data) => emit("warn",  namespace, msg, data),
    error: (msg, data) => emit("error", namespace, msg, data),
  };
}
