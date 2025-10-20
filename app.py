from flask import Flask, Response, request, render_template, jsonify
from flask_cors import CORS
from collections import deque
import json
import time
import threading

app = Flask(__name__)
CORS(app)

# ---- single-chat in-memory store ----
MESSAGES = deque(maxlen=1000)  # keep last 1000
_next_id = 0
_lock = threading.Lock()
_new_message = threading.Condition(_lock)

# clear epoch: increments when /api/clear happens so SSE can emit a 'clear' event
_clear_epoch = 0

def _add_message(role: str, content: str, name: str):
    global _next_id
    with _lock:
        _next_id += 1
        msg = {"id": _next_id, "ts": time.time(), "role": role, "content": content, "name": name}
        MESSAGES.append(msg)
        _new_message.notify_all()
        return msg
# ---- routes ----

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/stream")
def stream():
    """
    SSE stream:
      - Sends backlog on connect
      - Emits `event: message` for new messages
      - Emits `event: clear` when /api/clear is called
      - Sends heartbeats so proxies keep the connection open
    """
    def event_stream(last_id: int, local_epoch: int):
        HEARTBEAT_EVERY = 15
        next_heartbeat = time.time() + HEARTBEAT_EVERY

        # backlog snapshot (under lock), then send (outside lock)
        with _lock:
            backlog = [m for m in MESSAGES if m["id"] > last_id]
        for m in backlog:
            yield f"id: {m['id']}\n"
            yield "event: message\n"
            yield f"data: {json.dumps(m)}\n\n"
            last_id = m["id"]

        # live loop
        while True:
            # wait under lock (either new msg or clear or heartbeat timeout)
            with _lock:
                timeout = max(0, next_heartbeat - time.time())
                notified = _new_message.wait(timeout=timeout)
                epoch_changed = (_clear_epoch != local_epoch)
                # If cleared, don't send backlog here; we’ll send a 'clear' first
                new_msgs = [] if epoch_changed else (
                    [m for m in MESSAGES if m["id"] > last_id] if notified else []
                )
                if epoch_changed:
                    local_epoch = _clear_epoch

            # outside lock: first send clear if needed
            if epoch_changed:
                # tell the client to wipe UI
                yield "event: clear\n"
                yield "data: {}\n\n"
                # DO NOT reset last_id here; keep it so only higher ids stream
                # (we never reset _next_id, so new messages will have higher ids)

            # then send any new messages
            for m in new_msgs:
                yield f"id: {m['id']}\n"
                yield "event: message\n"
                yield f"data: {json.dumps(m)}\n\n"
                last_id = m["id"]

            # heartbeat
            now = time.time()
            if now >= next_heartbeat:
                yield ": ping\n\n"
                next_heartbeat = now + HEARTBEAT_EVERY

    # support resume
    last_id_header = request.headers.get("Last-Event-ID")
    last_id = int(last_id_header) if (last_id_header and last_id_header.isdigit()) else 0
    with _lock:
        local_epoch = _clear_epoch

    return Response(
        event_stream(last_id, local_epoch),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.post("/api/message")
def api_message():
    data = request.get_json(silent=True) or {}
    role = (data.get("role") or "").strip().lower()
    content = (data.get("content") or "").strip()
    name = (data.get("name") or "").strip()

    if role not in {"user", "assistant", "system"}:
        return jsonify(error="role must be one of: user, assistant, system"), 400
    if not content:
        return jsonify(error="content is required"), 400

    msg = _add_message(role, content, name)
    return jsonify(msg), 201

@app.get("/api/messages")
def list_messages():
    with _lock:
        return jsonify(list(MESSAGES))

@app.post("/api/clear")
def clear_messages():
    global _clear_epoch
    # 1) clear history (do NOT reset _next_id)
    with _lock:
        MESSAGES.clear()
        _clear_epoch += 1
        _new_message.notify_all()  # wake SSE to emit 'clear'

    return jsonify({"status": "cleared"}), 200

if __name__ == "__main__":
    # threaded dev server so /stream doesn’t block other requests
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False, threaded=True)
