from flask import Flask, request, jsonify
import dateparser
import pytz
import os
import logging
import sys

app = Flask(__name__)

# Log to stdout so Render shows it in the live logs
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


def extract_datetime_str(body):
    """
    ReTell (and other webhook callers) may wrap arguments in various
    envelopes. Try the common shapes before giving up.
    """
    if not isinstance(body, dict):
        return None

    # Flat: {"datetime_str": "..."}
    if "datetime_str" in body and isinstance(body["datetime_str"], str):
        return body["datetime_str"]

    # Common nesting keys
    for key in ("args", "arguments", "parameters", "params", "input"):
        nested = body.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("datetime_str"), str):
            return nested["datetime_str"]

    return None


@app.route("/check-business-hours", methods=["POST"])
def check_business_hours():
    # Capture raw body for diagnostics regardless of parse outcome
    raw_body = request.get_data(as_text=True)
    log.info("Incoming request. Content-Type=%s Body=%s",
             request.headers.get("Content-Type"), raw_body)

    data = request.get_json(force=True, silent=True)
    datetime_str = extract_datetime_str(data)

    if not datetime_str:
        log.warning("Could not find datetime_str in body. Parsed=%s", data)
        # Echo what we received so it shows up in ReTell's call logs
        return jsonify({
            "is_valid": False,
            "error": "Missing required field: datetime_str",
            "received_body": data,
            "received_keys": list(data.keys()) if isinstance(data, dict) else None,
        }), 400

    datetime_str = datetime_str.strip()

    parsed_dt = dateparser.parse(
        datetime_str,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": BUSINESS_TIMEZONE,
        }
    )

    if parsed_dt is None:
        log.info("dateparser failed on input: %r", datetime_str)
        return jsonify({
            "is_valid": False,
            "parsed_datetime": None,
            "message": f'Could not understand the date/time: "{datetime_str}"'
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
