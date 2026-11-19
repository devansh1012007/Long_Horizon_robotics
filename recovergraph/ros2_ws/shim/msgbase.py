"""Shared declarative message machinery for the shim's msg packages.

`_Message` subclasses declare `_spec_` = [(field, kind)] where kind is:
  int | float | str | bool | list          (primitives; list = untyped array)
  ("msg", Cls)                             (nested message)
  ("msg[]", Cls)                            (list of nested messages)

to_dict/from_dict round-trip every message through a JSON-able dict (wire
fidelity; also what the bag writer records).
"""
from __future__ import annotations


class _Message:
    _spec_: list = []

    def __init__(self, **kwargs):
        for fname, kind in self._spec_:
            setattr(self, fname, _default(kind))
        names = {f for f, _k in self._spec_}
        for k, v in kwargs.items():
            if k not in names:
                raise TypeError(f"{type(self).__name__} has no field {k!r}")
            setattr(self, k, v)

    def to_dict(self) -> dict:
        out = {}
        for fname, kind in self._spec_:
            v = getattr(self, fname)
            out[fname] = _encode(kind, v)
        return out

    @classmethod
    def from_dict(cls, d: dict):
        self = cls.__new__(cls)
        for fname, kind in self._spec_:
            if fname in d:
                setattr(self, fname, _decode(kind, d[fname]))
            else:
                setattr(self, fname, _default(kind))
        return self

    def __repr__(self) -> str:
        fields = ", ".join(f"{f}={getattr(self, f)!r}" for f, _k in self._spec_)
        return f"{type(self).__name__}({fields})"

    def __eq__(self, other) -> bool:
        return isinstance(other, _Message) and self.to_dict() == other.to_dict()


def _default(kind):
    if kind in (int, float, str, bool):
        return kind()
    if kind is list:
        return []
    if isinstance(kind, tuple):
        tag, cls = kind
        if tag == "msg":
            return cls()
        if tag == "msg[]":
            return []
    raise TypeError(kind)


def _encode(kind, v):
    if kind in (int, float, str, bool):
        return kind(v)
    if kind is list:
        return list(v)
    if isinstance(kind, tuple):
        tag, cls = kind
        if tag == "msg":
            return v.to_dict()
        if tag == "msg[]":
            return [m.to_dict() for m in v]
    raise TypeError(kind)


def _decode(kind, v):
    if kind in (int, float, str, bool):
        return kind(v)
    if kind is list:
        return list(v)
    if isinstance(kind, tuple):
        tag, cls = kind
        if tag == "msg":
            return cls.from_dict(v)
        if tag == "msg[]":
            return [cls.from_dict(m) for m in v]
    raise TypeError(kind)


def make_msg(name: str, spec: list) -> type:
    """Build a message class from a declarative spec."""
    return type(name, (_Message,), {"_spec_": spec})


def make_srv(name: str, req_spec: list, resp_spec: list) -> type:
    """Build a service class carrying Request/Response message classes."""
    srv = type(name, (), {})
    srv.Request = make_msg(f"{name}.Request", req_spec)
    srv.Response = make_msg(f"{name}.Response", resp_spec)
    return srv


def make_action(name: str, goal_spec: list, result_spec: list,
                feedback_spec: list) -> type:
    act = type(name, (), {})
    act.Goal = make_msg(f"{name}.Goal", goal_spec)
    act.Result = make_msg(f"{name}.Result", result_spec)
    act.Feedback = make_msg(f"{name}.Feedback", feedback_spec)
    return act
