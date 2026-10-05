"""Executes tool calls requested by the model and audits every attempt.

The model's JSON arguments are parsed, dispatched to the real function, and the
outcome (success or failure) is persisted to the store — this is the trail you
later mine for evals and fine-tuning data.
"""

import inspect
import json
import time

from app.db import json_store as store
from app.tools import booking

TOOL_FUNCS = {
    "check_availability": booking.check_availability,
    "book_appointment": booking.book_appointment,
    "cancel_appointment": booking.cancel_appointment,
    "reschedule_appointment": booking.reschedule_appointment,
    "get_business_info": booking.get_business_info,
    "lookup_appointments": booking.lookup_appointments,
    "lookup_customer": booking.lookup_customer,
    "escalate_to_human": booking.escalate_to_human,
}

CONSENT_ERROR = (
    "consent required: present the full booking summary (service, date, time, price) "
    "to the customer and ask for an explicit yes, then book only after they agree in "
    "their next message. Do not book in the same turn you checked availability."
)


def _consent_on_file(conversation_id: int) -> bool:
    """True when the controller flagged the previous turn's reply as a
    confirmation ask (summary shown + availability checked + question posed)."""
    conversation = store.get_conversation(conversation_id)
    return bool(conversation and conversation.get("pending_booking"))


def _consume_consent(conversation_id: int) -> None:
    """One booking per explicit yes — a second booking in the same turn needs
    its own confirmation ask."""
    conversation = store.get_conversation(conversation_id)
    if conversation:
        conversation["pending_booking"] = False
        store.save_conversation(conversation)


def execute_tool(
    name: str,
    arguments_json: str,
    business_id: int,
    conversation_id: int,
) -> tuple[dict, str]:
    """Returns (result_dict, short_summary_for_ui). Never raises."""
    started = time.perf_counter()
    args: dict | None
    result: dict
    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        args, result = None, {"success": False, "error": f"invalid argument JSON: {e}"}

    if args is not None:
        func = TOOL_FUNCS.get(name)
        if func is None:
            result = {"success": False, "error": f"unknown tool '{name}'"}
        elif name == "book_appointment" and not _consent_on_file(conversation_id):
            # The model cannot commit a booking the customer never agreed to:
            # consent is granted by the controller (previous turn asked + was answered)
            # and enforced here, regardless of what the model claims.
            result = {"success": False, "error": CONSENT_ERROR}
        else:
            try:
                # Context keys are injected only into tools that declare them;
                # the model's own arguments are always passed through.
                kwargs = dict(args)
                params = inspect.signature(func).parameters
                for key in ("business_id", "conversation_id"):
                    if key in params:
                        kwargs[key] = business_id if key == "business_id" else conversation_id
                result = func(**kwargs)
            except TypeError as e:
                result = {"success": False, "error": f"bad arguments for {name}: {e}"}
            except Exception as e:  # tool bugs must not kill the conversation
                result = {"success": False, "error": f"{type(e).__name__}: {e}"}
            if name == "book_appointment" and result.get("success"):
                _consume_consent(conversation_id)

    ok = bool(result.get("success"))
    latency_ms = int((time.perf_counter() - started) * 1000)
    store.add_tool_call(
        conversation_id=conversation_id,
        tool_name=name,
        arguments=args or {},
        result=result,
        success=ok,
        error=None if ok else result.get("error"),
        latency_ms=latency_ms,
    )
    return result, _summarize(name, args or {}, result)


def _summarize(name: str, args: dict, result: dict) -> str:
    if name == "check_availability":
        if result.get("success") and result.get("open"):
            n = len(result.get("available_times", []))
            return f"{result.get('service')} on {result.get('date')} -> {n} slot(s)"
        return f"{args.get('service_name', '?')} on {args.get('date', '?')} -> unavailable"
    if name == "book_appointment":
        if result.get("success"):
            return f"booked {result['booking_reference']}"
        return f"booking failed: {result.get('error')}"
    if name == "cancel_appointment":
        return f"cancelled {args.get('booking_reference')}" if result.get("success") else f"cancel failed: {result.get('error')}"
    if name == "reschedule_appointment":
        return f"rescheduled -> {result.get('summary')}" if result.get("success") else f"reschedule failed: {result.get('error')}"
    if name == "escalate_to_human":
        return f"escalated: {args.get('reason', '')}"
    if name == "lookup_appointments":
        if result.get("customer"):
            return f"{result['customer']['name']}: {len(result.get('appointments', []))} active booking(s)"
        return "no customer with that phone"
    if name == "lookup_customer":
        return f"found {result['customer']['name']}" if result.get("customer") else "no customer with that phone"
    return name
