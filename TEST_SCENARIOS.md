# Live Test Scenarios — Swastha AI Voice Receptionist

These are real patient conversations to run against the live system.
Each scenario tells you who you are, what to say, and what to verify.

Seed phone numbers: +919876543210 through +919876543219

---

## Scenario 1 — New Patient, Hindi, Appointment Booking

**Who you are:** First-time caller. No record in DB.
**Language:** Hindi (select Auto or Hindi)

**Say:**
> "Namaste, mujhe doctor se milna hai. Mere pet mein bahut dard ho raha hai."

When asked for details:
> "Mera naam Arjun Mehta hai. Mera number hai 9999999999. Mujhe general doctor chahiye."

**What to verify:**
- Agent asks for name + phone (new patient)
- Registers silently — no "you've been registered" announcement
- Offers available General Medicine slots
- Books slot and confirms in Hindi
- Booking card appears on screen

---

## Scenario 2 — Existing Patient, Marathi, Appointment Booking

**Who you are:** Arun Patil (existing patient, mr-IN preference)
**Language:** Marathi (select Marathi)

**Say:**
> "Namaskar, mala doctor Anjali Deshmukh yanchi appointment ghyaychi ahe. माझा नंबर आहे 9876543212."

**What to verify:**
- Agent skips registration (patient found by phone)
- Responds entirely in Marathi
- TTS voice switches to Kavya
- Books ortho slot with Dr. Anjali Deshmukh
- Confirmation message in Marathi

---

## Scenario 3 — Prescription Query, Hindi

**Who you are:** Ramesh Kumar (existing patient, hi-IN)
**Language:** Auto-detect

**Say:**
> "Meri dawai ke baare mein poochna tha. Mera number 9876543210 hai."

**What to verify:**
- Intent detected as "prescription"
- Fetches Ramesh's prescription (Amlodipine + Aspirin)
- Doctor notes translated from English to Hindi before reading out
- Dosage and frequency spoken clearly in Hindi
- No appointment booking attempted

---

## Scenario 4 — Lab Report Ready, Hindi

**Who you are:** Ramesh Kumar (has a ready CBC report in DB)
**Language:** Hindi

**Say:**
> "Mera blood test ka result aaya kya? Number 9876543210."

**What to verify:**
- Intent detected as "lab"
- CBC report found with status "ready"
- Result summary translated to Hindi and spoken
- Lab result card appears on screen
- Report marked "dispatched" (if you call again, it should say no pending reports)

---

## Scenario 5 — Lab Report Pending, Marathi

**Who you are:** Arun Patil (has a pending Lipid Panel)
**Language:** Marathi

**Say:**
> "Maza lipid panel report ready ahe ka? Number 9876543212."

**What to verify:**
- Intent detected as "lab"
- Report found but status is "pending"
- Agent says report is still being processed — in Marathi
- No result summary spoken (nothing to read yet)

---

## Scenario 6 — Bill Enquiry + Payment Link, Hindi

**Who you are:** Sunita Devi (has unpaid bill of ₹3,200)
**Language:** Hindi

**Say:**
> "Mera hospital ka bill kitna baka hai? Mera number 9876543211 hai."

**What to verify:**
- Intent detected as "billing"
- Bill amount ₹3,200 spoken in Hindi
- Payment link dispatched via SMS (Twilio — check logs if Twilio not configured)
- Bill card appears on screen showing amount

---

## Scenario 7 — Split Phone Number Across Two Turns

**Who you are:** Ramesh Kumar (existing patient), dictating number in two parts
**Language:** Hindi

**Say:**
> "Mujhe apni dawaai ke baare mein poochna tha."

When asked for phone:
> "987654"

When asked to complete it:
> "3 2 1 0"

**What to verify:**
- Turn 1: Agent acknowledges prescription intent, asks for name + phone
- Turn 2: Agent detects partial number (< 10 digits), asks for the rest — does NOT look up DB
- Turn 3: Agent assembles "9876543210" from history without needing the full number repeated, fetches prescription, reads medicines in Hindi
- Patient is NOT asked for their number a third time

---

## Scenario 8 — Hinglish, Ambiguous Intent (tests confidence-gated fanout)

**Who you are:** New caller, code-mixed speech
**Language:** Auto-detect

**Say:**
> "Hi, mujhe kuch help chahiye. Mera naam Rahul hai."

When agent asks what help you need:
> "Actually doctor se milna bhi hai, aur apni medicines bhi check karni thi."

**What to verify:**
- 6-intent classifier runs in one LLM call (not 2 separate calls)
- Agent asks a single clarifying question covering both intents
- Does NOT escalate after one ambiguous utterance
- After you clarify ("appointment"), routes to scheduler

---

## Scenario 9 — Mid-Call Language Switch

**Who you are:** Bilingual caller
**Language:** Auto-detect

**Say (Hindi first):**
> "Namaste, mujhe appointment chahiye."

Then switch to Marathi mid-conversation:
> "Maze nav Kavita Joshi ahe. Number 9876543215."

**What to verify:**
- Agent detects language switch
- TTS voice switches from Priya (Hindi) to Kavya (Marathi)
- Subsequent responses come in Marathi
- Transcript shows both languages correctly

---

## Scenario 10 — Call Drop Simulation

**Who you are:** Any patient, mid-booking
**Language:** Hindi

**Say:**
> "Mujhe appointment chahiye. Mera number 9876543210 hai."

Wait until agent offers slots, then click **"simulate dropped call"** button.

**What to verify:**
- UI immediately shows "Call dropped unexpectedly" state
- Reconnect button appears
- Click Reconnect — same room name, agent rejoins
- Agent picks up context from Redis (patient doesn't have to repeat name/number)

---

## Scenario 11 — No Slots Available, Get Alternatives

**Who you are:** Vijay Sharma (existing patient)
**Language:** Hindi

**Say:**
> "Mujhe aaj cardiology mein appointment chahiye. Mera number 9876543214 hai."

(Cardiology slots are only Tuesday/Thursday — today may not have any)

**What to verify:**
- Agent checks slots for today, finds none
- Automatically offers next 3 available cardiology slots across dates
- Reads out all three options: doctor name, date, time
- Waits for patient to pick one ("pehla slot chahiye")
- Books chosen slot and confirms

---

## Quick Reference — Seed Patient Phone Numbers

| Phone | Name | Language | Has |
|---|---|---|---|
| 9876543210 | Ramesh Kumar | Hindi | Prescription, CBC report (ready), dispatched glucose report |
| 9876543211 | Sunita Devi | Hindi | Unpaid bill ₹3,200, discharge followup |
| 9876543212 | Arun Patil | Marathi | Prescription (diabetes), Lipid Panel (pending) |
| 9876543213 | Priya Marathe | Marathi | Paid bill (should show "no outstanding") |
| 9876543214 | Vijay Sharma | Hindi | Prescription (arthritis) |
| 9876543215 | Kavita Joshi | Marathi | Nothing special — clean account |
| 9876543216 | Mohan Gupta | Hindi | Nothing special |
| 9876543217 | Anita Bhosale | Marathi | Nothing special |
| 9876543218 | Deepak Verma | Hindi | Nothing special |
| 9876543219 | Sneha Kulkarni | Marathi | Nothing special |
| 9999999999 | (any new name) | Any | Use for new patient registration test |
