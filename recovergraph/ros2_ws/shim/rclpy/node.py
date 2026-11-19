"""Node shim: parameters, pub/sub, services, timers, clock, logger."""
from __future__ import annotations

import sys
import time

from . import (TIMERS, TOPICS, SERVICES, _call_service, _deliver, _Future,
               reset_bus)


class _Param:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class _Logger:
    def __init__(self, name: str):
        self._name = name

    def _print(self, level, msg):
        print(f"[{level}]: [{self._name}] {msg}", file=sys.stderr)

    def info(self, msg):
        self._print("INFO", msg)

    def warn(self, msg):
        self._print("WARN", msg)

    def error(self, msg):
        self._print("ERROR", msg)

    def debug(self, msg):
        pass


class _Stamp:
    def __init__(self, sec: int = 0, nanosec: int = 0):
        self.sec, self.nanosec = int(sec), int(nanosec)

    def to_dict(self):
        return {"sec": self.sec, "nanosec": self.nanosec}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("sec", 0), d.get("nanosec", 0))


class _Time:
    def __init__(self, stamp: _Stamp):
        self._stamp = stamp

    def to_msg(self):
        return self._stamp


class _Clock:
    def now(self) -> _Time:
        t = time.time()
        return _Time(_Stamp(int(t), int((t % 1) * 1e9)))


class TimerHandle:
    def __init__(self, period: float, cb):
        self.period = max(1e-6, float(period))
        self.cb = cb
        self._next = time.perf_counter() + self.period

    def _fire_if_due(self) -> bool:
        now = time.perf_counter()
        if now >= self._next:
            self._next = now + self.period
            self.cb()
            return True
        return False

    def cancel(self):
        if self in TIMERS:
            TIMERS.remove(self)


class Publisher:
    def __init__(self, msg_type, topic: str, qos=10, node_name: str = ""):
        self.msg_type = msg_type
        self.topic = topic
        self.node_name = node_name

    def publish(self, msg) -> None:
        _deliver(self.topic, msg, self)


class Subscription:
    def __init__(self, msg_type, topic: str, cb, qos=10):
        self.msg_type = msg_type
        self.topic = topic
        self.cb = cb
        TOPICS[topic].append(self)

    def destroy(self):
        subs = TOPICS.get(self.topic, [])
        if self in subs:
            subs.remove(self)


class Service:
    def __init__(self, srv_type, name: str, cb):
        self.srv_type = srv_type
        self.name = name
        self.cb = cb
        SERVICES[name] = (srv_type, cb)


class Client:
    def __init__(self, srv_type, name: str):
        self.srv_type = srv_type
        self.name = name

    def call_async(self, request):
        return _Future(_call_service(self.name, request))

    def wait_for_service(self, timeout_sec=None) -> bool:
        return self.name in SERVICES


class Node:
    def __init__(self, name: str):
        self._name = name
        self._params: dict = {}
        self._timers: list = []

    # -- lifecycle ---------------------------------------------------------
    def destroy_node(self):
        for tm in list(self._timers):
            tm.cancel()
        self._timers = []

    # -- parameters --------------------------------------------------------
    def declare_parameter(self, name, value):
        self._params[name] = _Param(name, value)

    def get_parameter(self, name) -> _Param:
        if name not in self._params:
            self._params[name] = _Param(name, None)
        return self._params[name]

    # -- io ---------------------------------------------------------------
    def create_publisher(self, msg_type, topic, qos=10) -> Publisher:
        return Publisher(msg_type, topic, qos, node_name=self._name)

    def create_subscription(self, msg_type, topic, cb, qos=10) -> Subscription:
        return Subscription(msg_type, topic, cb, qos)

    def create_service(self, srv_type, name, cb) -> Service:
        return Service(srv_type, name, cb)

    def create_client(self, srv_type, name) -> Client:
        return Client(srv_type, name)

    def create_timer(self, period, cb) -> TimerHandle:
        tm = TimerHandle(period, cb)
        TIMERS.append(tm)
        self._timers.append(tm)
        return tm

    # -- misc ---------------------------------------------------------------
    def get_logger(self) -> _Logger:
        return _Logger(self._name)

    def get_clock(self) -> _Clock:
        return _Clock()

    def spin(self):
        from . import spin
        spin(self)
