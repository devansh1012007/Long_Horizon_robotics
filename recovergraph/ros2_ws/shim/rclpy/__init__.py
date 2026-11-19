"""rclpy shim: message bus core (topics, services, timers, latency log)."""
from __future__ import annotations

import sys
import time
from collections import defaultdict

_RUNNING = False
TOPICS: dict = defaultdict(list)          # topic -> [subscription callbacks]
SERVICES: dict = {}                      # service -> (srv_type, callback)
TIMERS: list = []                        # TimerHandle list
LATENCY_LOG: list = []                   # per-delivery latency records
MSG_COUNT = defaultdict(int)             # (channel) -> messages
RECORD = False                           # bag recording switch
BAG: list = []                           # recorded messages (channel, payload)


def start_recording() -> None:
    global RECORD
    RECORD = True


def stop_recording() -> list:
    global RECORD
    RECORD = False
    return list(BAG)


def clear_recording() -> None:
    BAG.clear()


def init(args=None) -> None:
    global _RUNNING
    _RUNNING = True


def shutdown() -> None:
    global _RUNNING
    _RUNNING = False


def ok() -> bool:
    return _RUNNING


def reset_bus() -> None:
    """Fresh bus state (used by tests and per-run bring-up)."""
    TOPICS.clear()
    SERVICES.clear()
    TIMERS.clear()
    LATENCY_LOG.clear()
    MSG_COUNT.clear()
    BAG.clear()
    global RECORD
    RECORD = False


def _now() -> float:
    return time.perf_counter()


def _deliver(topic: str, msg, publisher) -> None:
    data = msg.to_dict()
    if RECORD:
        BAG.append({"channel": topic, "kind": "topic",
                    "source": getattr(publisher, "node_name", ""),
                    "payload": data})
    MSG_COUNT[topic] += 1                 # wire-level tap: count publishes
    t0 = _now()
    for sub in list(TOPICS.get(topic, [])):
        # ROS topic semantics: delivery only on matching message types
        if sub.msg_type is not None and not isinstance(msg, sub.msg_type):
            continue
        try:
            sub.cb(type(msg).from_dict(data))
        except Exception:                  # subscriber isolation
            import traceback
            traceback.print_exc(file=sys.stderr)
        LATENCY_LOG.append(
            {"channel": topic, "kind": "topic", "latency_ms":
             (_now() - t0) * 1000.0})


def _call_service(name: str, request):
    if name not in SERVICES:
        raise RuntimeError(f"service {name} not available")
    srv_type, cb = SERVICES[name]
    t0 = _now()
    wire = type(request).to_dict(request)
    if RECORD:
        BAG.append({"channel": name, "kind": "service_req", "payload": wire})
    request2 = type(request).from_dict(wire)
    response = srv_type.Response()
    cb(request2, response)
    out = srv_type.Response.from_dict(response.to_dict())
    if RECORD:
        BAG.append({"channel": name, "kind": "service_resp",
                    "payload": out.to_dict()})
    LATENCY_LOG.append(
        {"channel": name, "kind": "service",
         "latency_ms": (_now() - t0) * 1000.0})
    MSG_COUNT[name] += 1
    return out


class _Future:
    """Completed-by-construction future (services are synchronous here)."""

    def __init__(self, result):
        self._result = result

    def done(self) -> bool:
        return True

    def result(self):
        return self._result


def spin(node, timeout_sec: float | None = None) -> None:
    """Fire all registered timers while the bus runs (single process)."""
    t_end = None if timeout_sec is None else _now() + timeout_sec
    while _RUNNING:
        if t_end is not None and _now() >= t_end:
            return
        fired = False
        for tm in list(TIMERS):
            if tm._fire_if_due():
                fired = True
        if not fired:
            time.sleep(0.002)


def spin_once(node, timeout_sec: float | None = None) -> None:
    for tm in list(TIMERS):
        tm._fire_if_due()
    time.sleep(timeout_sec or 0.0)


def spin_until_future_complete(node, future, timeout_sec=None) -> None:
    # services complete synchronously in this bus
    return


def latency_stats() -> dict:
    by = defaultdict(list)
    for rec in LATENCY_LOG:
        by[rec["channel"]].append(rec["latency_ms"])
    out = {}
    for ch, xs in by.items():
        xs = sorted(xs)
        n = len(xs)
        out[ch] = {
            "n": n,
            "p50_ms": xs[n // 2],
            "p95_ms": xs[min(n - 1, int(0.95 * n))],
            "max_ms": xs[-1],
        }
    return out


def message_counts() -> dict:
    return dict(MSG_COUNT)


def time_msg():
    """Time.to_msg() equivalent: a Header stamp."""
    from std_msgs.msg import Header
    t = time.time()
    return Header.stamp_type()(sec=int(t), nanosec=int((t % 1) * 1e9))


from . import node  # noqa: E402,F401  (rclpy.node import support)
from .node import Node  # noqa: E402,F401
