from flask import Flask, request, jsonify
import dateparser
import pytz
import os

app = Flask(__name__)

# --- Configuration via Render environment variables ---
BUSINESS_TIMEZONE   = os.environ.get("BUSINESS_TIMEZONE",   "America/Los_Angeles")
BUSINESS_HOUR_START = int(os.environ.get("BUSINESS_HOUR_START", 9))   # 9 = 9:00 AM
BUSINESS_HOUR_END   = int(os.environ.get("BUSINESS_HOUR_END",   17))  # 17 = 5:00 PM
# 0=Mon … 4=Fri; extend to include 5 (Sat) or 6 (Sun) if needed
BUSINESS_DAYS       = [0, 1, 2, 3, 4]


@app.route("/check-business-hours", methods=["POST"])
def check_business_hours():
    data = request.get_json(silent=True)

    if not data or "datetime_str" not in data:
        return jsonify({
            "is_valid": False,
            "error": "Missing required field: datetime_str"
        }), 400

    datetime_str = data["datetime_str"].strip()

    # Parse natural language.
    # - TIMEZONE sets the assumed timezone when the caller omits one
    #   (e.g. "Thursday at 4pm" → assumes business timezone).
    # - PREFER_DATES_FROM: future means "Thursday at 4pm" always resolves
    #   to the next upcoming Thursday, never one in the past.
    parsed_dt = dateparser.parse(
        datetime_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": BUSINESS_TIMEZONE,
        }
    )

    if parsed_dt is None:
        return jsonify({
            "is_valid": False,
            "parsed_datetime": None,
            "message": f'Could not understand the date/time: "{datetime_str}"'
        }), 200  # 200 so ReTell treats it as a valid function response

    # Convert to the business timezone for the hours/day check
    biz_tz   = pytz.timezone(BUSINESS_TIMEZONE)
    local_dt = parsed_dt.astimezone(biz_tz)

    is_business_day  = local_dt.weekday() in BUSINESS_DAYS
    is_business_hour = BUSINESS_HOUR_START <= local_dt.hour < BUSINESS_HOUR_END
    is_valid         = is_business_day and is_business_hour

    # Human-readable reason (useful for the LLM to relay back to caller)
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

    return jsonify({
        "is_valid":        is_valid,
        "parsed_datetime": local_dt.isoformat(),
        "day_of_week":     local_dt.strftime("%A"),
        "time_local":      local_dt.strftime("%I:%M %p %Z"),
        "message":         reason
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
