"""std_msgs shim (Header, String)."""
from msgbase import make_msg

Stamp = make_msg("Stamp", [("sec", int), ("nanosec", int)])
Header = make_msg("Header", [("stamp", ("msg", Stamp))])
String = make_msg("String", [("data", str)])

Header.stamp_type = staticmethod(lambda: Stamp)
