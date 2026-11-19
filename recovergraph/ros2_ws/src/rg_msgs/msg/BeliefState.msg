# Belief s_t = (q_t, o_t, g_t, h_t, c_t) (spec 5.3). Heavy payloads are JSON.
std_msgs/Header header
float64[] ee_pose
float64[] ee_cov_diag
string gripper_state
float64 gripper_width
float64 gripper_force
float64 gripper_commanded_force
rg_msgs/ObjectEstimate[] objects
string confidence_json
string belief_json
