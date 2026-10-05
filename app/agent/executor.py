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
    "propose_booking": booking.propose_booking,
    "book_appointment": booking.book_appointment,
    "cancel_appointment": booking.cancel_appointment,
    "reschedule_appointment": booking.reschedule_appointment,
    "get_business_info": booking.get_business_info,
    "lookup_appointments": booking.lookup_appointments,
    "lookup_customer": booking.lookup_customer,
    "escalate_to_human": booking.escalate_to_human,
}

CONSENT_ERROR = (
    "no valid booking offer: call propose_booking with the service, date and time, "
    "present the returned summary to the customer, and wait for an explicit yes. "
    "Then call book_appointment with the confirmation_token propose_booking returned."
)


def _valid_offer(conversation_id: int, args: dict) -> dict | None:
    """The conversation's pending offer, if args carry its unexpired token."""
    conversation = store.get_conversation(conversation_id)
    offer = (conversation or {}).get("pending_offer")
    if not offer:
        return None
    if (args or {}).get("confirmation_token") != offer.get("token"):
        return None
    if conversation.get("turn_count", 0) - offer.get("turn", 0) > 1:
        return None  # offered too long ago — the model must propose again
    return offer


def _clear_offer(conversation_id: int) -> None:
    """One booking per token."""
    conversation = store.get_conversation(conversation_id)
    if conversation:
        conversation["pending_offer"] = None
        store.save_conversation(conversation)


def _run_tool(func, kwargs: dict) -> dict:
    try:
        return func(**kwargs)
    except TypeError as e:
        return {"success": False, "error": f"bad arguments: {e}"}
    except Exception as e:  # tool bugs must not kill the conversation
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def _context_kwargs(func, args: dict, business_id: int, conversation_id: int) -> dict:
    """The model's arguments plus injected context keys. Context keys are injected
    only into tools that declare them; the model's own arguments pass through."""
    kwargs = dict(args)
    params = inspect.signature(func).parameters
    for key in ("business_id", "conversation_id"):
        if key in params:
            kwargs[key] = business_id if key == "business_id" else conversation_id
    return kwargs


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
        elif name == "book_appointment":
            # The model cannot commit a booking the customer never agreed to:
            # book_appointment executes only a stored offer, only with the
            # server-issued one-use token for it. The offer's values override
            # whatever the model passes — the offer is the source of truth.
            offer = _valid_offer(conversation_id, args)
            if offer is None:
                result = {"success": False, "error": CONSENT_ERROR}
            else:
                kwargs = {
                    "service_name": offer["service_name"],
                    "date": offer["date"],
                    "start_time": offer["start_time"],
                    "customer_name": offer["customer_name"],
                    "customer_phone": offer["customer_phone"],
                    "customer_confirmed": True,
                }
                if offer.get("staff_name"):
                    kwargs["staff_name"] = offer["staff_name"]
                kwargs = _context_kwargs(func, kwargs, business_id, conversation_id)
                result = _run_tool(func, kwargs)
                if result.get("success"):
                    _clear_offer(conversation_id)
        else:
            kwargs = _context_kwargs(func, args, business_id, conversation_id)
            result = _run_tool(func, kwargs)

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
