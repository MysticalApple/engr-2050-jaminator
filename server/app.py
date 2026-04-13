"""
Temperature Probe Monitoring Server
=====================================

A Flask server that collects timestamped temperature readings from multiple
probes and displays them as a live-updating graph in the browser.

-- HOW TO POST TEMPERATURE DATA --

Endpoint : POST /data
Content-Type: application/json

Payload schema
--------------
{
    "probe_id": <string>,      # unique identifier for the probe (e.g. "probe-1", "oven-left")
    "temperature": <number>,   # temperature value (any unit — label your probes accordingly)
    "timestamp": <string>      # ISO-8601 datetime string, e.g. "2026-04-05T14:30:00Z"
                               # If omitted, the server uses the current UTC time.
}

Example curl command
--------------------
curl -X POST http://localhost:5000/data \\
     -H "Content-Type: application/json" \\
     -d '{"probe_id": "probe-1", "temperature": 72.4, "timestamp": "2026-04-05T14:30:00Z"}'

Batch posting (multiple readings at once)
-----------------------------------------
POST /data/batch
Content-Type: application/json

Payload: a JSON array of the objects described above.

curl -X POST http://localhost:5000/data/batch \\
     -H "Content-Type: application/json" \\
     -d '[
           {"probe_id": "probe-1", "temperature": 72.4, "timestamp": "2026-04-05T14:30:00Z"},
           {"probe_id": "probe-2", "temperature": 68.1, "timestamp": "2026-04-05T14:30:00Z"}
         ]'

-- HOW TO RUN --

    pip install flask
    python app.py

Then open http://localhost:5000 in your browser.
"""

from flask import Flask, request, jsonify, render_template
from datetime import datetime, timezone
from collections import defaultdict
import json

app = Flask(__name__)

# In-memory store: { probe_id: [ {"timestamp": iso_str, "temperature": float}, ... ] }
probe_data: dict[str, list[dict]] = defaultdict(list)

# A monotonically increasing counter — the browser polls this to detect new data.
data_version: int = 0


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def parse_measurement(obj: dict) -> tuple[str, dict] | tuple[None, str]:
    """Validate and normalise a single measurement dict.
    Returns (probe_id, record) on success or (None, error_message) on failure.
    """
    probe_id = obj.get("probe_id")
    if not probe_id or not isinstance(probe_id, str):
        return None, "'probe_id' must be a non-empty string"

    temp = obj.get("temperature")
    if temp is None or not isinstance(temp, (int, float)):
        return None, "'temperature' must be a number"

    ts_raw = obj.get("timestamp")
    if ts_raw:
        try:
            # Accept both "Z" suffix and "+00:00" offset
            ts_raw = ts_raw.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_raw)
            # Normalise to UTC ISO string with Z suffix
            timestamp = (
                dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            )
        except ValueError:
            return None, f"'timestamp' could not be parsed as ISO-8601: {ts_raw!r}"
    else:
        timestamp = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )

    return probe_id, {"timestamp": timestamp, "temperature": float(temp)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("dashboard.html")


@app.post("/data")
def receive_single():
    """Accept a single temperature measurement."""
    global data_version
    obj = request.get_json(silent=True)
    if not obj or not isinstance(obj, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    probe_id, result = parse_measurement(obj)
    if probe_id is None:
        return jsonify({"error": result}), 422

    probe_data[probe_id].append(result)
    data_version += 1
    return jsonify({"status": "ok", "probe_id": probe_id, "stored": result}), 201


@app.post("/data/batch")
def receive_batch():
    """Accept a JSON array of temperature measurements."""
    global data_version
    arr = request.get_json(silent=True)
    if not arr or not isinstance(arr, list):
        return jsonify({"error": "Request body must be a JSON array"}), 400

    stored, errors = [], []
    for i, obj in enumerate(arr):
        probe_id, result = parse_measurement(obj)
        if probe_id is None:
            errors.append({"index": i, "error": result})
        else:
            probe_data[probe_id].append(result)
            stored.append({"probe_id": probe_id, "stored": result})

    if stored:
        data_version += 1

    if errors:
        print(errors)

    status = 201 if not errors else (207 if stored else 422)
    return jsonify({"stored": stored, "errors": errors}), status


@app.get("/api/data")
def api_data():
    """Return all stored measurements as JSON."""
    return jsonify(dict(probe_data))


@app.get("/api/version")
def api_version():
    """Lightweight endpoint the browser polls to check for new data."""
    return jsonify({"version": data_version})


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(__doc__)
    app.run(debug=True, host="0.0.0.0", port=5000)
