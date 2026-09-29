"""Executes tool calls requested by the model and audits every attempt.

The model's JSON arguments are parsed, dispatched to the real function, and the
outcome (success or failure) is persisted to tool_call_logs — this is the trail
you later mine for evals and fine-tuning data.
"""

import inspect
import json
import time

from sqlalchemy.orm import Session

from app.db.models import ToolCallLog
from app.tools import booking

TOOL_FUNCS = {
    "check_availability": booking.check_availability,
    "book_appointment": booking.book_appointment,
    "cancel_appointment": booking.cancel_appointment,
    "reschedule_appointment": booking.reschedule_appointment,
    "get_business_info": booking.get_business_info,
    "escalate_to_human": booking.escalate_to_human,
}


def execute_tool(
    db: Session,
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
        else:
            try:
                # Context keys are injected only into tools that declare them;
                # the model's own arguments are always passed through.
                kwargs = dict(args)
                params = inspect.signature(func).parameters
                for key in ("business_id", "conversation_id"):
                    if key in params:
                        kwargs[key] = business_id if key == "business_id" else conversation_id
                result = func(db, **kwargs)
            except TypeError as e:
                result = {"success": False, "error": f"bad arguments for {name}: {e}"}
            except Exception as e:  # tool bugs must not kill the conversation
                db.rollback()
                result = {"success": False, "error": f"{type(e).__name__}: {e}"}

    ok = bool(result.get("success"))
    latency_ms = int((time.perf_counter() - started) * 1000)
    db.add(
        ToolCallLog(
            conversation_id=conversation_id,
            tool_name=name,
            arguments=args or {},
            result=result,
            success=ok,
            error=None if ok else result.get("error"),
            latency_ms=latency_ms,
        )
    )
    db.commit()
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
    return name
