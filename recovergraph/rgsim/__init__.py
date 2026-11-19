"""rgsim: pure-Python kinematic simulator, fault injection and the episode
runner that compares systems under controlled variables (spec 10.3).
"""
from .fault_injector import FaultEvent, episode_schedule, sample_schedule
from .noise import NoiseCfg, observe
from .world import World, generate_scene, rest_z
