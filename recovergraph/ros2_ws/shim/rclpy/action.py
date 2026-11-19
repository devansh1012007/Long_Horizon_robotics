"""ActionServer shim: synchronous goal execution via the execute callback."""
from __future__ import annotations

from . import MSG_COUNT, LATENCY_LOG, _now


class GoalHandle:
    def __init__(self, action_type, request):
        self._action_type = action_type
        self.request = request
        self._status = "active"

    def succeed(self):
        self._status = "succeeded"

    def abort(self):
        self._status = "aborted"

    def is_succeeded(self):
        return self._status == "succeeded"

    @property
    def status(self):
        return self._status


class ActionServer:
    """Registers an execute callback; goals are sent synchronously via
    `send_goal(goal_msg)` by in-process clients (the e2e driver / tests)."""

    def __init__(self, node, action_type, name, execute_callback,
                 callback_group=None):
        self.node = node
        self.action_type = action_type
        self.name = name
        self.execute_callback = execute_callback
        self._goals = []

    def send_goal(self, goal_request):
        gh = GoalHandle(self.action_type, goal_request)
        self._goals.append(gh)
        t0 = _now()
        result = self.execute_callback(gh)
        LATENCY_LOG.append(
            {"channel": self.name, "kind": "action",
             "latency_ms": (_now() - t0) * 1000.0})
        MSG_COUNT[self.name] += 1
        return gh, result

    @property
    def goal_count(self):
        return len(self._goals)
