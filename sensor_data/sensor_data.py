from __future__ import annotations
import os
import pickle
import lzma
import hashlib
import struct
from dataclasses import dataclass, asdict, field

import numpy as np
import cv2

import rosbags.rosbag1
import rosbags.serde

import usim.umap


DEFAULT_SENSOR_DATA_DIR = os.path.join('data', 'sensor_data')
if not os.path.isdir('data'):
    os.mkdir('data')
if not os.path.isdir(DEFAULT_SENSOR_DATA_DIR):
    os.mkdir(DEFAULT_SENSOR_DATA_DIR)


@dataclass
class SimulationData:
    sampling_time: float
    robot_pose: np.ndarray   # (N,3) array of robot poses (x,y,theta[rad])
    map: usim.umap.UsimMap
    def __post_init__(self):
        if type(self.map) is dict:
            self.map = usim.umap.UsimMap(**self.map)

@dataclass
class SensorData:
    odometry: list[tuple[int, np.ndarray]]                  # (timestamp, [theta, x, y])
    lidar: list[tuple[int, np.ndarray]]                     # (timestamp, [phi, r])
    # camera is: (timestamp, list[id, [phi, r]], CompressedImg)
    # decompress example: Img = cv2.imdecode(CompressedImg, cv2.IMREAD_COLOR)
    camera: list[tuple[int, list[tuple[int, np.ndarray]], np.ndarray]]


    comment: str = ''
    from_rosbag: bool = False
    sim_data: SimulationData = None

    _hash_str: str = field(default=None, init=True)

    def save(self, filename: str) -> None:
        """Save the sensor data to a file.

        Args:
            filename: The filename to save the data to.
        """
        save_sensor_data(self, filename)

    def __post_init__(self) -> None:
        if type(self.sim_data) is dict:
            self.sim_data = SimulationData(**self.sim_data)
    
    def hash_str(self):
        if self._hash_str is not None:
            return self._hash_str
        hash_repr = hashlib.sha1(b"")
        for t, arr in self.odometry:
            arr.flags.writeable = False
            tbytes =  bytearray(struct.pack("f", t))
            arrbytes = arr.data
            hash_repr.update(tbytes)
            hash_repr.update(arrbytes)

        for t, arr in self.lidar:
            arr.flags.writeable = False
            tbytes =  bytearray(struct.pack("f", t))
            arrbytes = arr.data
            hash_repr.update(tbytes)
            hash_repr.update(arrbytes)
        
        for t, landmarks, compressed_image in self.camera:
            tbytes =  bytearray(struct.pack("f", t))
            hash_repr.update(tbytes)
            for id, arr in landmarks:
                idbytes =  bytearray(struct.pack("f", t))
                hash_repr.update(idbytes)
                arr.flags.writeable = False
                arrbytes = arr.data
                hash_repr.update(arrbytes)

        self._hash_str = hash_repr.hexdigest()
        return self._hash_str

    def __hash__(self) -> int:
        return int(self.hash_str(), 16)


def load_sensor_data(filename: str, dir: os.PathLike=DEFAULT_SENSOR_DATA_DIR) -> SensorData:
    with lzma.open(os.path.join(dir, filename), 'rb') as f:
        data_dict = pickle.load(f)
    return SensorData(**data_dict)


def save_sensor_data(sensor_data: SensorData, filename: str, dir: os.PathLike=DEFAULT_SENSOR_DATA_DIR) -> None:
    data_dict = asdict(sensor_data)
    with lzma.open(os.path.join(dir, filename), 'wb') as f:
        pickle.dump(data_dict, f)


def detect_landmarks(image: np.ndarray, camera_matrix: np.ndarray, distortion_coefficents) -> list[tuple[int, float]]:
    aruco_dict = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_6X6_250)
    parameters = cv2.aruco.DetectorParameters_create()
    corners, ids, rejectedImgPoints = cv2.aruco.detectMarkers(image, aruco_dict, parameters=parameters)
    if ids is None:
        return (image, [])

    angles = []
    distances = []
    orientations = []
    for cornerset in corners:
        LA  = 0.083     # Physical size of the aruco markers. Should be an input parameter

        # Estimate the position of the aruco markers in world coordinates
        rotation, translation, _ = cv2.aruco.estimatePoseSingleMarkers(cornerset, LA, camera_matrix, distortion_coefficents)
        translation = translation.squeeze()
        rotation_matrix, _ = cv2.Rodrigues(rotation)
        normal_vector = rotation_matrix @ np.array([0, 0, 1])
        orientation = np.arctan2(normal_vector[2], normal_vector[0])

        # Use the position of the markers to get the distance and difference in heading to the robot
        distance = np.linalg.norm(translation)
        angle = np.arctan(-translation[0]/translation[2])
        angles.append(angle)
        distances.append(distance)
        orientations.append(orientation)

        # Draw the aruco markers on the image
        corners = cornerset.reshape((4, 2))
        (topLeft, topRight, bottomRight, bottomLeft) = corners
		# convert each of the (x, y)-coordinate pairs to integers
        topRight = (int(topRight[0]), int(topRight[1]))
        bottomRight = (int(bottomRight[0]), int(bottomRight[1]))
        bottomLeft = (int(bottomLeft[0]), int(bottomLeft[1]))
        topLeft = (int(topLeft[0]), int(topLeft[1]))

        cv2.line(image, topLeft, topRight, (0, 255, 0), 2)
        cv2.line(image, topRight, bottomRight, (0, 255, 0), 2)
        cv2.line(image, bottomRight, bottomLeft, (0, 255, 0), 2)
        cv2.line(image, bottomLeft, topLeft, (0, 255, 0), 2)

        cv2.putText(image, f'd: {distance:.3f}', (topLeft[0] - 50, topLeft[1] - 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 255, 0), 2)
        cv2.putText(
            image, f'angle: {np.rad2deg(angle):.1f}', (topLeft[0] - 50, topLeft[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0),
            2)

    return (image, list(zip([id[0] for id in ids], [np.array([distance, angle, orientation]) for angle, distance, orientation in zip(angles, distances, orientations)])))


def rosbag_to_data(rosbag_path: os.PathLike, save_imgs=False) -> SensorData:
    laser_ros = []
    odom_ros = []
    cam_ros_sim = []
    cam_ros_real = []
    camera_matrix = np.empty((3,3))
    distortion_coefficients = np.empty((5,))
    with rosbags.rosbag1.Reader(rosbag_path) as reader:
        connections_laser = []
        connections_odom = []
        connections_cam_sim = []
        connections_cam = []
        connections_cam_info = []
        for x in reader.connections:
            if x.topic == '/scan':
                connections_laser.append(x)
            elif x.topic == '/odom':
                connections_odom.append(x)
            elif x.topic == '/camera/image_raw':
                connections_cam_sim.append(x)
            elif x.topic == '/raspicam_node/image/compressed':
                connections_cam.append(x)
            elif x.topic == '/raspicam_node/camera_info':
                connections_cam_info.append(x)
        if len(connections_laser) != 0:
            for connection, timestamp, rawdata in reader.messages(connections=connections_laser):
                msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
                laser_ros.append((timestamp, msg.ranges))
        if len(connections_odom) != 0:
            for connection, timestamp, rawdata in reader.messages(connections=connections_odom):
                msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
                def euler_from_quaternion(x, y, z, w):
                    import math
                    """
                    Convert a quaternion into euler angles (roll, pitch, yaw)
                    roll is rotation around x in radians (counterclockwise)
                    pitch is rotation around y in radians (counterclockwise)
                    yaw is rotation around z in radians (counterclockwise)
                    """
                    t0 = +2.0 * (w * x + y * z)
                    t1 = +1.0 - 2.0 * (x * x + y * y)
                    roll_x = math.atan2(t0, t1)
                
                    t2 = +2.0 * (w * y - z * x)
                    t2 = +1.0 if t2 > +1.0 else t2
                    t2 = -1.0 if t2 < -1.0 else t2
                    pitch_y = math.asin(t2)
                
                    t3 = +2.0 * (w * z + x * y)
                    t4 = +1.0 - 2.0 * (y * y + z * z)
                    yaw_z = math.atan2(t3, t4)
                
                    return roll_x, pitch_y, yaw_z # in radians
                theta, x, y = euler_from_quaternion(msg.pose.pose.orientation.x, msg.pose.pose.orientation.y,
                              msg.pose.pose.orientation.z, msg.pose.pose.orientation.w)[2], \
                              msg.pose.pose.position.x, msg.pose.pose.position.y
                odom_ros.append((timestamp, np.array([theta, x, y])))
        if len(connections_cam_sim) != 0:
            raise NotImplementedError('Camera on simulation not implemented')
            for connection, timestamp, rawdata in reader.messages(connections=connections_cam_sim):
                msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
                cam_ros_sim.append((detect_landmarks(msg), timestamp))
        if len(connections_cam_info) != 0:
            for connection, timestamp, rawdata in reader.messages(connections=connections_cam_info):
                msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
                camera_matrix = msg.k.reshape((3,3))
                distortion_coefficients = msg.d
        if len(connections_cam) != 0:
            for connection, timestamp, rawdata in reader.messages(connections=connections_cam):
                msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
                img = cv2.imdecode(msg.data, cv2.IMREAD_COLOR)
                annotated_img, landmarks = detect_landmarks(img, camera_matrix, distortion_coefficients)
                Compressed_Annotated = None
                if save_imgs:
                    Compressed_Annotated = cv2.imencode('.jpeg', annotated_img)[1]
                cam_ros_real.append((timestamp, landmarks, Compressed_Annotated))


    return SensorData(odometry=odom_ros, lidar=laser_ros, camera=cam_ros_real, comment='From rosbag', from_rosbag=True)

def rosbag_to_imgs(rosbag_path: os.PathLike) -> list[np.ndarray]:
    with rosbags.rosbag1.Reader(rosbag_path) as reader:
        connections_cam = []
        for x in reader.connections:
            if x.topic == '/raspicam_node/image/compressed':
                connections_cam.append(x)
        imgs = []
        for connection, timestamp, rawdata in reader.messages(connections=connections_cam):
            msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
            img = cv2.imdecode(msg.data, cv2.IMREAD_COLOR)
            imgs.append(img)
    return imgs

def rosbag_camera_info(rosbag_path: os.PathLike) -> list[np.ndarray]:
    with rosbags.rosbag1.Reader(rosbag_path) as reader:
        connections_cam_info = []
        for x in reader.connections:
            if x.topic == '/raspicam_node/camera_info':
                connections_cam_info.append(x)
        camera_matrix = np.empty((3,3))
        distortion_coefficients = np.empty((5,))
        for connection, timestamp, rawdata in reader.messages(connections=connections_cam_info):
            msg = rosbags.serde.deserialize_cdr(rosbags.serde.ros1_to_cdr(rawdata, connection.msgtype), connection.msgtype)
            camera_matrix = msg.k.reshape((3,3))
            distortion_coefficients = msg.d
    return (camera_matrix, distortion_coefficients)

def list_to_data(sensor_data_lst: list[tuple[np.ndarray, list[tuple[int, float]], np.ndarray]], ts: float, comment: str = '') -> SensorData:
    odom: np.ndarray = np.array([sensor_data_lst_elem[0] for sensor_data_lst_elem in sensor_data_lst])
    lidar: np.ndarray = np.array([sensor_data_lst_elem[2] for sensor_data_lst_elem in sensor_data_lst])
    landmarks: list = [sensor_data_lst_elem[1] for sensor_data_lst_elem in sensor_data_lst]
    if not comment:
        comment = 'From list'
    else:
        comment += '\nFrom list'
    return SensorData(ts=ts, odometry=odom, lidar=lidar, camera=landmarks, comment=comment, from_rosbag=False)


def add_comment(comment: str, filename: str, dir: os.PathLike=DEFAULT_SENSOR_DATA_DIR) -> None:
    with lzma.open(os.path.join(dir, filename), 'rb') as f:
        data_dict = pickle.load(f)

    if data_dict['comment'] != '':
        data_dict['comment'] += '\n'
    else:
        data_dict['comment'] = comment

    with lzma.open(os.path.join(dir, filename), 'wb') as f:
        pickle.dump(data_dict, f)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Convert sensor data from rosbag to sensor data')
    parser.add_argument('--rosbag', type=str, help='Path to rosbag file', required=True)
    parser.add_argument('--save_images', action="store_true")

    args = parser.parse_args()

    if args.rosbag:
        sensor_data = rosbag_to_data(args.rosbag, args.save_images)
        lst = os.path.basename(args.rosbag).split('.')
        lst[-1] = lst[-1].replace('bag', 'xz', 1)
        sensor_data.save('.'.join(lst))
        print('Saved to', '.'.join(lst))
