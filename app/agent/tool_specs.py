"""Tool schemas handed to the model (OpenAI function-calling format)."""

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": (
                "Check open appointment slots for a service on a date. Always call this "
                "before booking anything. Returns available times, or alternatives when "
                "the requested time is taken."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "exact or partial service name"},
                    "date": {"type": "string", "description": "date in YYYY-MM-DD"},
                    "preferred_time": {"type": "string", "description": "optional, HH:MM"},
                },
                "required": ["service_name", "date"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_booking",
            "description": (
                "Present a booking offer to the customer. Validates the request and "
                "returns a summary plus a one-use confirmation_token. Show the summary, "
                "ask for an explicit yes, and only then call book_appointment with the "
                "token. This commits NOTHING."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "start_time": {"type": "string", "description": "HH:MM"},
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "staff_name": {"type": "string", "description": "optional, only if the customer asked"},
                },
                "required": ["service_name", "date", "start_time", "customer_name", "customer_phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": (
                "Book the appointment you previously proposed with propose_booking. "
                "Only call AFTER the customer explicitly agreed to the exact summary. "
                "Pass the confirmation_token propose_booking returned. The booking is "
                "created from the stored offer — the token is the only proof of consent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmation_token": {
                        "type": "string",
                        "description": "the token returned by propose_booking for this exact booking",
                    },
                },
                "required": ["confirmation_token"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an appointment by its booking reference.",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_reference": {"type": "string", "description": "e.g. SC-1A2B"},
                },
                "required": ["booking_reference"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reschedule_appointment",
            "description": (
                "Move an existing appointment to a new date/time by booking reference. "
                "Confirm the new time with the customer first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_reference": {"type": "string"},
                    "new_date": {"type": "string", "description": "YYYY-MM-DD"},
                    "new_time": {"type": "string", "description": "HH:MM"},
                },
                "required": ["booking_reference", "new_date", "new_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_appointments",
            "description": (
                "Find a customer's active appointments by phone number. Use when a "
                "customer wants to cancel or reschedule but doesn't know their booking "
                "reference. Returns the customer and their booked appointments."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "customer phone, any format"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_customer",
            "description": (
                "Look up a customer's profile and appointments by phone number. Use it "
                "to greet returning patients personally and check whether they have "
                "anything coming up. Ask for the phone number if the customer only "
                "gave a name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {"type": "string", "description": "customer phone, any format"},
                },
                "required": ["phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_business_info",
            "description": (
                "Look up clinic facts: opening hours, services with prices, or booking "
                "rules. Use this whenever you are unsure instead of guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "enum": ["hours", "services", "rules", "all"],
                    },
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Flag the conversation for human staff. Use for emergencies, complaints, "
                "billing disputes, medical questions you must not answer, or when the "
                "customer explicitly asks for a person."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
]
