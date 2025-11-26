"""
ROS2 Camera Node for receiving and processing camera images
"""

import threading
import cv2
from rclpy.node import Node
from sensor_msgs.msg import Image

from .image_utils import imgmsg_to_cv2


class CameraNode(Node):
    """
    ROS2 Node that receives camera images and maintains latest frame
    """
    
    def __init__(self, camera_topic, resolution=(640, 480)):
        """
        Initialize camera node
        
        Args:
            camera_topic (str): ROS2 topic to subscribe to
            resolution (tuple): Target resolution (width, height)
        """
        super().__init__('webrtc_sender_node')
        
        self.latest_frame = None
        self.resolution = resolution
        self.frame_lock = threading.Lock()
        self.frame_count = 0
        
        self.subscription = self.create_subscription(
            Image,
            camera_topic,
            self.image_callback,
            10
        )
        
        self.get_logger().info(f'Sender subscribed to: {camera_topic}')
        self.get_logger().info(f'Resolution: {resolution}')
        
    def image_callback(self, msg):
        """
        Process incoming camera images
        
        Args:
            msg: sensor_msgs.msg.Image message
        """
        try:
            if self.latest_frame is None:
                self.get_logger().info(f'First frame: {msg.width}x{msg.height}, {msg.encoding}')
                
            cv_image = imgmsg_to_cv2(msg)
            resized = cv2.resize(cv_image, self.resolution, interpolation=cv2.INTER_LANCZOS4)
            
            with self.frame_lock:
                self.latest_frame = resized
                self.frame_count += 1
                
        except Exception as e:
            self.get_logger().error(f'Error in image callback: {str(e)}')
            
    def get_frame(self):
        """
        Return the latest frame (thread-safe)
        
        Returns:
            numpy.ndarray or None: Copy of latest frame
        """
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None