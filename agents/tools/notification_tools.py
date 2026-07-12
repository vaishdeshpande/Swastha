"""Slack + SMS escalation notifications for the on-call doctor."""

import logging
import os

import httpx
from twilio.rest import Client

from agents.tools.db_tools import get_patient_record_by_id

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")
ON_CALL_DOCTOR_PHONE = os.environ.get("ON_CALL_DOCTOR_PHONE", "")


async def send_slack_alert(message: str) -> None:
    """Send escalation alert to Slack channel via webhook.
    No-ops with a warning when SLACK_WEBHOOK_URL is unset — an unconfigured
    webhook must never crash the escalation path (the patient-facing handoff
    message has already been queued by the caller)."""
    if not SLACK_WEBHOOK_URL.strip():
        logger.warning("notifications: SLACK_WEBHOOK_URL not configured — skipping Slack alert")
        return
    logger.info("notifications: sending Slack alert (%d chars)", len(message))
    async with httpx.AsyncClient() as client:
        response = await client.post(SLACK_WEBHOOK_URL, json={"text": message})
        if response.status_code != 200:
            logger.warning("notifications: Slack webhook returned status=%d", response.status_code)
        else:
            logger.debug("notifications: Slack alert delivered")


async def send_sms(phone: str, message: str) -> None:
    """Send SMS via Twilio. No-ops with a warning when Twilio isn't configured."""
    if not (TWILIO_ACCOUNT_SID.strip() and TWILIO_AUTH_TOKEN.strip() and TWILIO_PHONE_NUMBER.strip()):
        logger.warning("notifications: Twilio not configured — skipping SMS to %s", phone)
        return
    logger.info("notifications: sending SMS to %s (%d chars)", phone, len(message))
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    msg = twilio_client.messages.create(
        body=message,
        from_=TWILIO_PHONE_NUMBER,
        to=phone,
    )
    logger.debug("notifications: SMS sent sid=%s status=%s", msg.sid, msg.status)


async def escalate_to_doctor(patient_id: str, reason: str) -> None:
    """Fire both Slack alert and SMS to on-call doctor."""
    logger.info("notifications: escalating to doctor (patient_id=%s, reason=%s)", patient_id, reason)
    patient = await get_patient_record_by_id(patient_id)
    message = (
        f"🚨 ESCALATION: Patient {patient['name']} (ID: {patient_id})\n"
        f"Reason: {reason}\nLang: {patient['lang_pref']}\nPhone: {patient['phone']}"
    )
    await send_slack_alert(message)
    # SMS to on-call doctor (hardcoded for demo, would be from a roster table in production)
    await send_sms(ON_CALL_DOCTOR_PHONE, message)
    logger.info("notifications: escalation complete for patient_id=%s", patient_id)
