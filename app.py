from flask import Flask, request, jsonify
from datetime import datetime
from anthropic import Anthropic
import pytz
import os
import json
import logging
import sys

app = Flask(__name__)

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# --- Configuration via Render environment variables ---
BUSINESS_TIMEZONE   = os.environ.get("BUSINESS_TIMEZONE",   "America/Los_Angeles")
BUSINESS_HOUR_START = int(os.environ.get("BUSINESS_HOUR_START", 9))
BUSINESS_HOUR_END   = int(os.environ.get("BUSINESS_HOUR_END",   17))
BUSINESS_DAYS       = [0, 1, 2, 3, 4]  # 0=Mon … 4=Fri

# Anthropic client (reads ANTHROPIC_API_KEY from env automatically)
anthropic_client = Anthropic()
PARSER_MODEL = "claude-haiku-4-5-20251001"


def extract_datetime_str(body):
    """ReTell may wrap args in various envelopes; try common shapes."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("datetime_str"), str):
        return body["datetime_str"]
    for key in ("args", "arguments", "parameters", "params", "input"):
        nested = body.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("datetime_str"), str):
            return nested["datetime_str"]
    return None


def parse_datetime_with_claude(natural_language: str) -> datetime | None:
    """
    Use Claude Haiku to parse arbitrary natural language datetime expressions
    into an ISO 8601 timestamp. Returns a timezone-aware datetime or None.
    """
    now_local = datetime.now(pytz.timezone(BUSINESS_TIMEZONE))

    system_prompt = (
        "You convert natural language date/time expressions into ISO 8601 timestamps. "
        f"The current date and time is {now_local.isoformat()}. "
        f"If no timezone is specified in the input, assume {BUSINESS_TIMEZONE}. "
        "For weekday references like 'Thursday' or 'next Monday', resolve to the "
        "soonest upcoming occurrence (never a past date). "
        "Respond ONLY with valid JSON, no markdown, no commentary. "
        'Success: {"iso_datetime": "2026-05-14T16:00:00-07:00"} '
        'Failure: {"iso_datetime": null, "error": "brief reason"}'
    )

    try:
        response = anthropic_client.messages.create(
            model=PARSER_MODEL,
            max_tokens=200,
            system=system_prompt,
            messages=[{"role": "user", "content": natural_language}],
        )
        text = response.content[0].text.strip()
        # Strip any stray markdown fences just in case
        if text.startswith("```"):
            text = text.strip("`").lstrip("json").strip()
        parsed = json.loads(text)
        iso = parsed.get("iso_datetime")
        if not iso:
            log.info("Claude could not parse %r: %s", natural_language, parsed.get("error"))
            return None
        return datetime.fromisoformat(iso)
    except Exception as e:
        log.exception("Claude parse failed for %r: %s", natural_language, e)
        return None


@app.route("/check-business-hours", methods=["POST"])
def check_business_hours():
    raw_body = request.get_data(as_text=True)
    log.info("Incoming. CT=%s Body=%s", request.headers.get("Content-Type"), raw_body)

    data = request.get_json(force=True, silent=True)
    datetime_str = extract_datetime_str(data)

    if not datetime_str:
        return jsonify({
            "is_valid": False,
            "error": "Missing required field: datetime_str",
            "received_keys": list(data.keys()) if isinstance(data, dict) else None,
        }), 400

    datetime_str = datetime_str.strip()
    parsed_dt = parse_datetime_with_claude(datetime_str)

    if parsed_dt is None:
        return jsonify({
            "is_valid": False,
            "parsed_datetime": None,
            "message": f'Could not understand the date/time: "{datetime_str}"',
        }), 200

    biz_tz   = pytz.timezone(BUSINESS_TIMEZONE)
    local_dt = parsed_dt.astimezone(biz_tz)

    is_business_day  = local_dt.weekday() in BUSINESS_DAYS
    is_business_hour = BUSINESS_HOUR_START <= local_dt.hour < BUSINESS_HOUR_END
    is_valid         = is_business_day and is_business_hour

    if not is_business_day:
        reason = f"{local_dt.strftime('%A')} is not a business day (Mon–Fri only)"
    elif not is_business_hour:
        reason = (
            f"{local_dt.strftime('%I:%M %p %Z')} is outside business hours "
            f"({BUSINESS_HOUR_START}:00 AM – {BUSINESS_HOUR_END % 12 or BUSINESS_HOUR_END}:00 PM)"
        )
    else:
        reason = (
            f"{local_dt.strftime('%A')} at {local_dt.strftime('%I:%M %p %Z')} "
            "is within business hours"
        )

    response = {
        "is_valid":        is_valid,
        "parsed_datetime": local_dt.isoformat(),
        "day_of_week":     local_dt.strftime("%A"),
        "time_local":      local_dt.strftime("%I:%M %p %Z"),
        "message":         reason,
    }
    log.info("Returning: %s", response)
    return jsonify(response)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
