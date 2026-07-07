/**
 * Reference copy of stored responses spoken by the agent worker (Python side,
 * agents/prompts/). The frontend never sends these — it only displays them
 * when they arrive via the "agent-events" data channel transcript events.
 */
export const STORED_RESPONSE_KEYS = {
  greeting_hi: "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?",
  greeting_mr: "नमस्कार! मी तुमची काय मदत करू शकतो?",
  greeting_auto: "Hello! Namaste! Namaskar! How can I help you today?",

  slot_confirmed_hi: "आपका appointment {doctor} के साथ {date} को {time} बजे confirm हो गया है।",
  slot_confirmed_mr: "तुमची appointment {doctor} यांच्याकडे {date} ला {time} वाजता confirm झाली आहे।",

  no_slots_hi: "माफ़ कीजिए, उस तारीख को कोई slot उपलब्ध नहीं है। मैं आपको अगले तीन available slots बता सकता हूँ।",
  no_slots_mr: "माफ करा, त्या दिवशी कोणतेही slot उपलब्ध नाहीत। मी तुम्हाला पुढील तीन available slots सांगतो।",

  escalation_hi: "मैं आपको अभी हमारे receptionist से connect करता हूँ। एक moment रुकिए।",
  escalation_mr: "मी तुम्हाला आमच्या receptionist शी connect करतो. एक क्षण थांबा।",

  goodbye_hi: "धन्यवाद! आपका दिन शुभ हो।",
  goodbye_mr: "धन्यवाद! तुमचा दिवस चांगला जाओ।",
} as const;

export function localizedLabel(langCode: string, hi: string, mr: string, fallback: string): string {
  if (langCode === "hi-IN") return hi;
  if (langCode === "mr-IN") return mr;
  return fallback;
}
