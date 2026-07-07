"use client";

import { LANGUAGE_OPTIONS, LangOption } from "@/lib/types";
import { createLogger } from "@/lib/logger";

const log = createLogger("component/LanguageSelector");

interface LanguageSelectorProps {
  value: LangOption;
  onChange: (lang: LangOption) => void;
  disabled?: boolean;
}

export default function LanguageSelector({ value, onChange, disabled }: LanguageSelectorProps) {
  return (
    <div>
      <p className="neo-label">Language</p>
      <div className="flex gap-2">
        {LANGUAGE_OPTIONS.map((opt) => (
          <button
            key={opt.code}
            type="button"
            disabled={disabled}
            className={`neo-lang-btn ${value === opt.code ? "active" : ""}`}
            onClick={() => {
              log.info("Language selected", { lang: opt.code, label: opt.label });
              onChange(opt.code);
            }}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}
