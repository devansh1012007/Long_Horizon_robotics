"""sensor_msgs shim (JointState)."""
from msgbase import make_msg
from std_msgs.msg import Header

JointState = make_msg("JointState", [
    ("header", ("msg", Header)),
    ("name", list),
    ("position", list),
    ("velocity", list),
    ("effort", list),
])
