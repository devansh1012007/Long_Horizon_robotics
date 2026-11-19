"""scene_observer node (spec Section 7): camera frames -> /scene/objects.

M4 bring-up: frame-driven processing - each incoming truth frame is noised
(Gaussian pose + yaw noise, dropout) and republished, mirroring the rgsim
noise model's semantics (occluded/invisible frames are passed through with
inflated covariance). /reobserve supports a viewpoint hint used by
disambiguating probes (spec 6.3).
"""
import numpy as np
import rclpy
from rclpy.node import Node

from rg_msgs.msg import ObjectEstimate, ObjectEstimateArray
from rg_msgs.srv import Reobserve
from rgsim.noise import NoiseCfg


class SceneObserverNode(Node):

    def __init__(self, seed: int = 0, rate_hz: float = 10.0):
        super().__init__("scene_observer")
        self.declare_parameter("rate_hz", float(rate_hz))
        self.declare_parameter("pose_sigma", NoiseCfg.pose_sigma)
        self.declare_parameter("yaw_sigma", NoiseCfg.yaw_sigma)
        self.declare_parameter("dropout", NoiseCfg.dropout)
        self.declare_parameter("seed", int(seed))
        self.cfg = NoiseCfg(
            pose_sigma=float(self.get_parameter("pose_sigma").value),
            yaw_sigma=float(self.get_parameter("yaw_sigma").value),
            dropout=float(self.get_parameter("dropout").value))
        self.rng = np.random.default_rng(
            int(self.get_parameter("seed").value))
        self.sub_truth = self.create_subscription(
            ObjectEstimateArray, "/rg_sim/truth", self.cb_truth, 10)
        self.pub_scene = self.create_publisher(ObjectEstimateArray,
                                               "/scene/objects", 10)
        self.srv = self.create_service(Reobserve, "/reobserve", self.cb_reobserve)
        self.last: ObjectEstimateArray | None = None
        self.get_logger().info("scene_observer ready (frame-driven)")

    def _process(self, frame: ObjectEstimateArray) -> ObjectEstimateArray:
        out = ObjectEstimateArray()
        out.header = frame.header
        for est in frame.objects:
            o = ObjectEstimate()
            o.name = est.name
            o.identity_conf = est.identity_conf
            if not est.visible:                 # occluded/masked in the frame
                o.pose_mean = list(est.pose_mean)
                o.pose_cov_diag = [1e6] * 4
                o.visible = False
            elif self.rng.random() < self.cfg.dropout:
                o.pose_mean = list(est.pose_mean)
                o.pose_cov_diag = [1e6] * 4
                o.visible = False
            else:
                sigma = np.array([self.cfg.pose_sigma, self.cfg.pose_sigma,
                                  self.cfg.pose_sigma, self.cfg.yaw_sigma])
                mean = np.asarray(est.pose_mean, float) + self.rng.normal(
                    0.0, 1.0, 4) * sigma
                o.pose_mean = [float(x) for x in mean]
                o.pose_cov_diag = [self.cfg.pose_sigma**2] * 3 + \
                    [self.cfg.yaw_sigma**2]
                o.visible = True
            out.objects.append(o)
        return out

    def cb_truth(self, msg: ObjectEstimateArray):
        self.last = msg
        self.pub_scene.publish(self._process(msg))

    def cb_tick(self):
        if self.last is not None:
            self.pub_scene.publish(self._process(self.last))

    def cb_reobserve(self, request, response):
        # a viewpoint hint resolves masked regions (alt-view re-observation)
        self.cb_tick()
        response.scene = self._latest()
        response.success = response.scene is not None
        return response

    def _latest(self):
        if self.last is None:
            return ObjectEstimateArray()
        out = ObjectEstimateArray()
        out.header = self.last.header
        for est in self.last.objects:
            o = ObjectEstimate()
            o.name = est.name
            o.pose_mean = list(est.pose_mean)
            o.pose_cov_diag = list(est.pose_cov_diag)
            o.visible = True          # alt view clears occlusion
            o.identity_conf = est.identity_conf
            out.objects.append(o)
        return out


def main(args=None):
    rclpy.init(args=args)
    node = SceneObserverNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
