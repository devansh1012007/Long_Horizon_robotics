# Action-effect ledger entry (spec 5.2).
string episode_id
int64 step
string subgoal
string skill
string preconditions_json
string expected_effects_json
float64 predicted_state_conf
string observed_state_json
string actual_effects_json
float64 divergence_d
bool divergence_flagged
string[] downstream
float64 timestamp
bool fault_free
string entry_json
