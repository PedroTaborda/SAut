from __future__ import annotations
from cmath import inf

from typing import Callable
import copy
from unicodedata import ucd_3_2_0

import numpy as np
import matplotlib.pyplot as plt

from slam.map import OrientedLandmarkSettings, Map, Observation, UnorientedObservation, LineObservation, get_Dhn_line, get_Dhx_line, h_inv_line, h_line

import scipy.stats
import time

def diff_t1(rh_th1, rh_th2):
    return np.array([rh_th1[0] - rh_th2[0], np.mod(rh_th1[1] - rh_th2[1] + np.pi, 2*np.pi) - np.pi])

def diff_t2(rh_th1, rh_th2):
    return np.block([rh_th1[:2] - rh_th2[:2], np.mod(rh_th1[2] - rh_th2[2] + np.pi, 2*np.pi) - np.pi])


def h_uo(x, parameters):    # z is observed position of landmark in robot's reference frame
    p, R, n_gain = parameters
    z_no_noise = R @ (x - p)
    return z_no_noise

def h_inv_uo(z, parameters):
    p, R, n_gain = parameters
    return R.T @ z + p

def get_Dhx_uo(x, parameters):
    p, R, n_gain = parameters
    return R

def get_Dhn_uo(x, parameters):
    p, R, n_gain = parameters
    z = R @ (x - p)
    return np.array([[z[0], -z[1]], [z[1], z[0]]]) @ n_gain
        
def h_o(x, parameters):    # z is observed position of landmark in robot's reference frame
    p, theta, R, n_gain = parameters
    x_prime = x[0:2]
    psi_prime = x[2]
    z_no_noise = R @ (x_prime - p)
    z_psi_no_noise = psi_prime - theta
    return np.block([z_no_noise, z_psi_no_noise ])

def h_inv_o(z, parameters):
    p, theta, R, n_gain = parameters
    z_prime = z[0:2]
    z_psi = z[2]
    return np.array([*(R.T @ z_prime + p), z_psi + theta])

def get_Dhx_o(x, parameters):
    p, theta, R, n_gain = parameters
    Dh = np.zeros((3,3))
    Dh[0:2, 0:2] = R
    Dh[2, 2] = 1
    return Dh

def get_Dhn_o(x, parameters):
    p, theta, R, n_gain = parameters
    Dh = np.zeros((3,3))
    z = R @ (x[0:2] - p)
    Dhz = np.array([[z[0], -z[1]], [z[1], z[0]]]) @ n_gain[0:2, 0:2]
    Dh[0:2, 0:2] = Dhz
    Dh[2, 2] = n_gain[2, 2]
    return Dh
        

class Particle:
    canonical_arrow: np.ndarray = np.array(
        [[0, 0],
         [1, 0],
         [0.5, 0.5],
         [1, 0],
         [0.5, -0.5],
         [1, 0],
         ])*0.1
    arrow_size = 0.1

    def __init__(self, map: Map = None, pose=(0, 0, 0), weight: float = 1.0, default_landmark_settings=None) -> None:
        if map is None:
            # Brand new particle being created: prepare new everything
            self.map = Map()
            self.pose = np.array(pose)  # np.random.uniform(low=-1, high=1, size=3)
            self.weight = 1.0
            return
        self.map: Map = map
        self.pose: np.ndarray = np.array(pose)
        self.weight: float = weight

    def apply_action(self, action: Callable[[np.ndarray], np.ndarray]) -> None:
        self.pose = action(self.pose)

    def make_line_observation(self, obs_data: tuple[int, tuple[float, float]], n_gain: np.ndarray) -> None:
        """Observe a line on the map. Measurements are in the robot's reference frame.

        Args:
            obs_data: A tuple of the form (landmark_id, (rh, th)),
            where rh, th describes a line in the robot's reference frame as 
            (orthogonal distance, angle from robot's heading).

        Side effects:
            -The map is updated with the observation (a landmark may be added)
            -The particle's weight is updated (possibly)
        Returns True if particle's weight was updated
        """

        px, py, theta = self.pose

        rh, th = obs_data[1]
        p = np.array([px, py])
        R = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        lidar_vector = np.array([-0.0625, 0])
        parameters = (p, theta, R, lidar_vector, n_gain)

        observed_landmarks = self.map.landmarks
        observed_lines_keys = [landmark for landmark in observed_landmarks if landmark < 0]      
        best_MahDistSqr, best_key = inf, 0
        for key in observed_lines_keys:
            MahDistSqr = self.map.landmarks[key].get_Mahalanobis_squared(np.array([rh, th]), diff = diff_t1, parameters = parameters)
            if MahDistSqr < best_MahDistSqr:
                best_MahDistSqr, best_key = MahDistSqr, key
        landmark_id = best_key

        if best_MahDistSqr > 3*3:
            landmark_id = min(observed_lines_keys) - 1 if observed_lines_keys else -1

        obs = LineObservation(
            landmark_id=landmark_id,
            z=np.array([rh, th]),
            h=h_line,
            h_inv=h_inv_line,
            get_Dhx=get_Dhx_line,
            get_Dhn=get_Dhn_line
        )
        update_factor = self.map.update(obs, diff = diff_t1, parameters = parameters)
        if update_factor is None:
            return False
        self.weight *= update_factor
        return True
        
    def make_unoriented_observation(self, obs_data: tuple[int, tuple[float, float]], n_gain: np.ndarray) -> None:
        """Make an observation of a landmark on the map.

        Side effects:
            -The map is updated with the observation (a landmark may be added)
            -The particle's weight is updated (possibly)
        Returns True if particle's weight was updated
        """
        px, py, theta = self.pose
        
        r, phi = obs_data[1]
        p = np.array([px, py])
        R = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        parameters = p, R, n_gain

        obs = UnorientedObservation(
            landmark_id=obs_data[0]+100,
            z=np.array([r*np.cos(phi), r*np.sin(phi)]),
            h=h_uo,
            h_inv=h_inv_uo,
            get_Dhx=get_Dhx_uo,
            get_Dhn=get_Dhn_uo,
        )
        update_factor = self.map.update(obs, parameters = parameters)
        if update_factor is None:
            return False
        self.weight *= update_factor
        return True

    def make_oriented_observation(self, obs_data: tuple[int, tuple[float, float, float]], n_gain: np.ndarray) -> None:
        """Make an observation of a landmark on the map, considering that the landmark is an Aruco.

        Side effects:
            -The map is updated with the observation (a landmark may be added)
            -The particle's weight is updated
        """
        px, py, theta = self.pose
        
        r, phi, psi = obs_data[1]
        p = np.array([px, py])
        R = np.array([[np.cos(theta), np.sin(theta)], [-np.sin(theta), np.cos(theta)]])
        parameters = p, theta, R, n_gain

        obs = Observation(
            landmark_id=obs_data[0],
            z=np.array([r*np.cos(phi), r*np.sin(phi), psi]),
            h=h_o,
            h_inv=h_inv_o,
            get_Dhx=get_Dhx_o,
            get_Dhn=get_Dhn_o,
        )
        update_factor = self.map.update(obs,  diff = diff_t2, parameters=parameters)        
        if update_factor is None:
            return False
        self.weight *= update_factor
        return True

    def copy(self) -> Particle:
        """Copy the particle, creating a new particle sharing the same map.
        """
        return Particle(self.map.copy(), copy.copy(self.pose), copy.copy(self.weight))

    def _draw(self, line: plt.Line2D) -> None:
        R = np.array([[np.cos(self.pose[2]), -np.sin(self.pose[2])],
                      [np.sin(self.pose[2]), np.cos(self.pose[2])]])
        arrow = (R @ self.canonical_arrow.T)
        arrow = (arrow.T + self.pose[:2]).T
        line.set_data(arrow[0, :], arrow[1, :])

    def __repr__(self) -> str:
        return f'Particle(pose={self.pose}, weight={self.weight})'

    def __str__(self) -> str:
        return self.__repr__()
