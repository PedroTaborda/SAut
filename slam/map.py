from dataclasses import dataclass, field
from enum import Enum
import math
import copy
import os

import scipy.stats
import numpy as np
from matplotlib.collections import PathCollection
from matplotlib.patches import Ellipse

from ekf.ekf import EKF, EKFSettings
from visualization_utils.mpl_video import to_video

@dataclass
class LandmarkSettings(EKFSettings):
    """Settings for the EKF representing a landmark.
    
    A landmark is represented by its position in the xy plane.
    By default, there is a linear measurement model, but this can be
    changed by setting the `h` and `Dh_` functions at measurement time.
    """
    mu0: np.ndarray = np.array([0, 0])
    cov0: np.ndarray = np.square(np.diag([0.1, 0.1]))
    min_cov: np.ndarray = np.square(np.diag([0.01, 0.01]))
    g: callable = lambda x, u, m: x
    get_Dgx: callable = lambda x, u: np.eye(2)
    get_Dgm: callable = lambda x, u: np.zeros((2, 2))

@dataclass
class LineLandmarkSettings(LandmarkSettings):
    """Settings for the EKF representing a line landmark.

    A line landmark is represented by two parameters, (rh, th), where all points
    in the line satisfy:
    rh = x*cos(th) + y*sin(th)
    """
    cov0: np.ndarray = np.square(np.diag([0.1, 0.1])) 
    min_cov: np.ndarray = np.square(np.diag([0.01, 0.01]))

@dataclass
class UnorientedLandmarkSettings(LandmarkSettings):
    """Settings for the EKF representing a landmark.
    
    A landmark is represented by its position in the xy plane, as well as an orientation.
    By default, there is a linear measurement model, but this can be
    changed by setting the `h` and `Dh_` functions at measurement time.
    """
    mu0: np.ndarray = np.array([0, 0])
    cov0: np.ndarray = np.square(np.diag([0.1, 0.1])) 
    min_cov: np.ndarray = np.square(np.diag([0.01, 0.01]))
    g: callable = lambda x, u, m: x
    get_Dgx: callable = lambda x, u: np.eye(2)
    get_Dgm: callable = lambda x, u: np.zeros((2, 2))

@dataclass
class OrientedLandmarkSettings(LandmarkSettings):
    """Settings for the EKF representing a landmark.
    
    A landmark is represented by its position in the xy plane, as well as an orientation.
    By default, there is a linear measurement model, but this can be
    changed by setting the `h` and `Dh_` functions at measurement time.
    """
    mu0: np.ndarray = np.array([0, 0, 0])
    cov0: np.ndarray = np.square(np.diag([0.1, 0.1, 0.05]))
    min_cov: np.ndarray = np.square(np.diag([0.01, 0.01, 0.01]))
    g: callable = lambda x, u, m: x
    get_Dgx: callable = lambda x, u: np.eye(3)
    get_Dgm: callable = lambda x, u: np.zeros((3, 3))


r = 0.2  # std_dev of default linear observation model

class Landmark(EKF):
    def __init__(self, settings: LandmarkSettings):
        super().__init__(settings)
        self.drawn = False
        self.confidence_interval = 0.99 # draw ellipse for this confidence interval
        self.latest_zx = None

    def predict(self):
        super().predict(u=0)

    def update(self, zx, **kwargs): # zx is z with x coords
        super().update(self.h(zx, np.zeros_like(zx)), **kwargs)
        self.latest_zx = zx

    def _undraw(self):
        if self.drawn:
            self.drawn = False
            self.std_ellipse.remove()
            self.z_handle.remove()

    def __del__(self):
        if self.drawn:
            self.std_ellipse.remove()
            self.z_handle.remove()

class OrientedLandmark(Landmark):
    def _draw(self, ax, actual_pos: np.ndarray=None, color_ellipse='C00', color_p='C01', color_z='C02'):
        """Draw the landmark on the given matplotlib axis.

        This drawing includes an ellipse which is the level curve of the
        probability distribution of the landmark for p=confidence_interval.
        It also includes a marker for the mean of this distribution and another
        for the latest observation.
        """
        if self.latest_zx is None:
            return
        p = self.get_mu()
        z = self.latest_zx
        if not self.drawn:
            self.drawn = True    
            self.std_ellipse: Ellipse = Ellipse((0, 0), 1, 1, facecolor='none', edgecolor=color_ellipse)
            ax.add_patch(self.std_ellipse)
            self.z_handle: PathCollection = ax.scatter(z[0], z[1], marker='1', c=color_z)

        # number of std's to include in confidence ellipse
        n_stds = -scipy.stats.norm.ppf((1-self.confidence_interval)/2)

        # Plot ellipse
        self.std_ellipse.set_center(self.get_mu()[0:2])
        [w, v] = np.linalg.eig(self.get_cov()[0:2, 0:2])
        self.std_ellipse.set_width(np.sqrt(w[0])*n_stds*2)
        self.std_ellipse.set_height(np.sqrt(w[1])*n_stds*2)
        angle_deg = math.atan2(v[1, 0], v[0, 0]) * 180/np.pi
        self.std_ellipse.set_angle(angle_deg)

        # Plot latest observation
        self.z_handle.set(offsets=z[0:2])

class UnorientedLandmark(Landmark):
    def _draw(self, ax, actual_pos: np.ndarray=None, color_ellipse='C00', color_p='C01', color_z='C02'):
        """Draw the landmark on the given matplotlib axis.

        This drawing includes an ellipse which is the level curve of the
        probability distribution of the landmark for p=confidence_interval.
        It also includes a marker for the mean of this distribution and another
        for the latest observation.
        """
        if self.latest_zx is None:
            return
        p = self.get_mu()
        z = self.latest_zx
        if not self.drawn:
            self.drawn = True    
            self.std_ellipse: Ellipse = Ellipse((0, 0), 1, 1, facecolor='none', edgecolor=color_ellipse)
            ax.add_patch(self.std_ellipse)
            self.z_handle: PathCollection = ax.scatter(z[0], z[1], marker='1', c=color_z)

        # number of std's to include in confidence ellipse
        n_stds = -scipy.stats.norm.ppf((1-self.confidence_interval)/2)

        # Plot ellipse
        self.std_ellipse.set_center(self.get_mu())
        [w, v] = np.linalg.eig(self.get_cov())
        self.std_ellipse.set_width(np.sqrt(w[0])*n_stds*2)
        self.std_ellipse.set_height(np.sqrt(w[1])*n_stds*2)
        angle_deg = math.atan2(v[1, 0], v[0, 0]) * 180/np.pi
        self.std_ellipse.set_angle(angle_deg)

        # Plot latest observation
        self.z_handle.set(offsets=z)

class LineLandmark(Landmark):
    def _draw(self, ax, actual_pos: np.ndarray=None, color_ellipse='C00', color_p='C01', color_z='C02'):
        """Draw the landmark on the given matplotlib axis.

        This drawing includes a line which is the estimate of the landmark's
        mean value.
        """
        if self.latest_zx is None:
            return
        p = self.get_mu()
        z = self.latest_zx
        if not self.drawn:
            self.drawn = True    
            self.std_ellipse: Ellipse = Ellipse((0, 0), 1, 1, facecolor='none', edgecolor=color_ellipse)
            ax.add_patch(self.std_ellipse)
            self.z_handle: PathCollection = ax.scatter(z[0], z[1], marker='1', c=color_z)

        # number of std's to include in confidence ellipse
        n_stds = -scipy.stats.norm.ppf((1-self.confidence_interval)/2)

        # Plot ellipse
        self.std_ellipse.set_center(self.get_mu())
        [w, v] = np.linalg.eig(self.get_cov())
        self.std_ellipse.set_width(np.sqrt(w[0])*n_stds*2)
        self.std_ellipse.set_height(np.sqrt(w[1])*n_stds*2)
        angle_deg = math.atan2(v[1, 0], v[0, 0]) * 180/np.pi
        self.std_ellipse.set_angle(angle_deg)

        # Plot latest observation
        self.z_handle.set(offsets=z)

class LandmarkType(Enum):
    """Type of observation."""
    MISSING_TYPE = 0
    LINE = LineLandmark
    ORIENTED = OrientedLandmark
    UNORIENTED = UnorientedLandmark

def default_landmark_settings(type: LandmarkType):
    """Returns the settings for the EKF representing an observation.
    
    The observation is represented by its position in the xy plane, as well as an orientation.
    By default, there is a linear measurement model, but this can be
    changed by setting the `h` and `Dh_` functions at measurement time.
    """
    def whatsthisobservation():
        raise ValueError("Observation type unknown or unset")
    vals = {
        LandmarkType.MISSING_TYPE: whatsthisobservation,
        LandmarkType.LINE: LineLandmarkSettings,
        LandmarkType.ORIENTED: OrientedLandmarkSettings,
        LandmarkType.UNORIENTED: UnorientedLandmarkSettings
    }
    return vals[type]()

@dataclass
class Observation:
    """ Observation of a landmark base class.
    Observation model:
        z = h(x, n)
        where
        x - landmark position (unknown)
        n - multi-normal observation noise with identity covariance matrix,
                0 mean and same dimensions as z
        z - observations (known)

        h - invertible and differentiable function
    """
    landmark_id: int
    z: np.ndarray
    h: callable
    h_inv: callable
    get_Dhx: callable
    get_Dhn: callable
    landmark_type: LandmarkType = field(default=LandmarkType.MISSING_TYPE, init=False)
    

@dataclass
class UnorientedObservation(Observation):
    """ Observation of an unoriented landmark
    """
    landmark_id: int = 0
    z: np.ndarray = np.array([0, 0]) 
    h: callable = lambda x, n: 1/0 # user needs to set this, but it requires a default value for dataclass inheritance reasons
    h_inv: callable = lambda z: 1/0
    get_Dhx: callable = lambda x, n: 1/0
    get_Dhn: callable = lambda x, n: 1/0
    type: LandmarkType = field(default=LandmarkType.UNORIENTED, init=False)

@dataclass
class Observation(Observation):
    """ Observation of an oriented landmark.
    """
    landmark_id: int = 0
    z: np.ndarray = np.array([0, 0, 0])
    h: callable = lambda x, n: 1/0
    h_inv: callable = lambda z: 1/0
    get_Dhx: callable = lambda x, n: 1/0
    get_Dhn: callable = lambda x, n: 1/0
    type: LandmarkType = field(default=LandmarkType.ORIENTED, init=False)

@dataclass
class LineObservation():
    """
    Observation of a line.
    """
    landmark_id: int = 0
    z: np.ndarray = np.array([0, 0])
    h: callable = lambda x, n: 1/0
    h_inv: callable = lambda z: 1/0
    get_Dhx: callable = lambda x, n: 1/0
    get_Dhn: callable = lambda x, n: 1/0
    type: LandmarkType = field(default=LandmarkType.LINE, init=False)

class Map:
    def __init__(self) -> None:
        self.landmarks: dict[int, Landmark] = {}

    def update(self, obs: Observation, diff = lambda x, y: x-y):
        if obs.landmark_id not in self.landmarks:
            x0 = obs.h_inv(obs.z)
            Dhn = obs.get_Dhn(x0)
            Dhx_inv = np.linalg.inv(obs.get_Dhx(x0))
            landmark_settings = default_landmark_settings(obs.type)
            landmark_settings.mu0 = x0
            landmark_settings.cov0 = Dhx_inv @ Dhn @ Dhn.T @ Dhx_inv.T

            self.landmarks[obs.landmark_id] = obs.type.value(landmark_settings)
            self.landmarks[obs.landmark_id].set_sensor_model(obs.h, obs.get_Dhx, obs.get_Dhn)
            return 1.0
        else:
            self.landmarks[obs.landmark_id].set_sensor_model(obs.h, obs.get_Dhx, obs.get_Dhn)
            likelyhood = self.landmarks[obs.landmark_id].get_likelihood(obs.z, diff=diff)
            self.landmarks[obs.landmark_id].predict()
            self.landmarks[obs.landmark_id].update(obs.h_inv(obs.z), diff=diff)
            return likelyhood

    def _draw(self, ax, **plot_kwargs):
        for landmark_id in self.landmarks:
            self.landmarks[landmark_id]._draw(ax, **plot_kwargs)

    def _undraw(self):
        for landmark_id in self.landmarks:
            self.landmarks[landmark_id]._undraw()


    def copy(self):
        return copy.copy(self)


if __name__ == '__main__':
    import matplotlib.pyplot as plt

    rng = np.random.default_rng(0)

    # Create a map
    map = Map()

    # Create a figure
    fig, ax = plt.subplots(1, 1)

    # Draw the map
    map._draw(ax)

    # std deviation of r and fi, noise cov is n_gain @ n_gain.T
    r_std = 0.1
    fi_std = 0.2
    n_gain = np.diag([r_std, fi_std])*1

    image_video_dir = os.path.join("data", "images", "map_ex")
    if not os.path.isdir("data"):
        os.mkdir("data")
    if not os.path.isdir(os.path.join("data", "images")):
        os.mkdir(os.path.join("data", "images"))
    if not os.path.isdir(image_video_dir):
        os.mkdir(image_video_dir)

    poses = np.array([
        [0, 0.1, np.pi/2],
        [0, 0.2, np.pi/2],
        [0, 0.3, np.pi/2],
        [0, 0.4, np.pi/2],
        [0, 0.5, np.pi/2],
        [0, 0.6, np.pi/2],
        [0, 0.7, np.pi/2],
        [0, 0.8, np.pi/2],
        [0, 0.9, np.pi/2],
        [0, 1, 0],
        [0.1, 1, 0],
        [0.2, 1, 0],
        [0.3, 1, 0],
        [0.4, 1, 0],
        [0.5, 1, 0],
        [0.6, 1, 0],
        [0.7, 1, 0],
        [0.8, 1, 0],
        [0.9, 1, 0],
        [1, 1, 0],
        [1, 0.9, 3*np.pi/2],
        [1, 0.8, 3*np.pi/2],
        [1, 0.7, 3*np.pi/2],
        [1, 0.6, 3*np.pi/2],
        [1, 0.5, 3*np.pi/2],
        [1, 0.4, 3*np.pi/2],
        [1, 0.3, 3*np.pi/2],
        [1, 0.2, 3*np.pi/2],
        [1, 0.1, 3*np.pi/2],
        [1, 0, 3*np.pi/2],
        [0.9, 0, np.pi],
        [0.8, 0, np.pi],
        [0.7, 0, np.pi],
        [0.6, 0, np.pi],
        [0.5, 0, np.pi],
        [0.4, 0, np.pi],
        [0.3, 0, np.pi],
        [0.2, 0, np.pi],
        [0.1, 0, np.pi],
        [0, 0, np.pi]
    ])
    a = 3
    ax.set_xlim(-0.5*a, 1.5*a)
    ax.set_ylim(-0.5*a, 1.5*a)
    x_real_landmark_0 = np.array([0.5, 0.5])*a
    x_real_landmark_1 = np.array([0.7, 0.7])*a
    plt.scatter(x_real_landmark_0[0], x_real_landmark_0[1], marker='x', c='r')
    plt.scatter(x_real_landmark_1[0], x_real_landmark_1[1], marker='x', c='r')
    plt.xlabel("x [m]")
    plt.ylabel("y [m]")
    
    for i, pose in enumerate(poses):
        px, py, theta = pose
        px *= a
        py *= a
        plt.scatter(px, py, marker=(3, 0, theta*180/np.pi-90), c='r')

        p = np.array([px, py])
        R = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        
        def h(x, n):    # z is observed position of landmark in robot's reference frame
            z_no_noise = R @ (x - p)
            r_err, ang_err = n_gain @ n
            R_error = np.array([[np.cos(ang_err), np.sin(ang_err)], [-np.sin(ang_err), np.cos(ang_err)]])
            return (1 + (r_err/np.linalg.norm(z_no_noise))) * R_error @ z_no_noise

        def h_inv(z):
            return R.T @ z + p

        def get_Dhx(x):
            return R

        def get_Dhn(x):
            z = R @ (x - p)
            return np.array([[z[0], -z[1]], [z[1], z[0]]]) @ n_gain
        landmark_id = 0


        # make an observation with noise
        z = h(x_real_landmark_0, rng.normal(size=(2,)))
        obs1 = UnorientedObservation(landmark_id=0, z=z, h=h, h_inv=h_inv, get_Dhx=get_Dhx, get_Dhn=get_Dhn)
        map.update(obs1)

        # make an observation with noise
        z = h(x_real_landmark_1, rng.normal(size=(2,)))
        obs2 = UnorientedObservation(landmark_id=1, z=z, h=h, h_inv=h_inv, get_Dhx=get_Dhx, get_Dhn=get_Dhn)
        map.update(obs2)

        map._draw(ax)
        plt.pause(0.01)
        plt.savefig(os.path.join(image_video_dir, f"{i:06d}_map_step.png"))

    to_video(image_video_dir, "map_ex.mp4", fps=10)
