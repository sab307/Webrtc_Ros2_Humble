"""
Image conversion utilities for ROS2 Image messages
"""

import cv2
import numpy as np


def imgmsg_to_cv2(img_msg):
    """
    Convert ROS Image message to OpenCV image
    
    Args:
        img_msg: sensor_msgs.msg.Image message
        
    Returns:
        numpy.ndarray: OpenCV BGR image
        
    Raises:
        ValueError: If encoding is not supported
    """
    if img_msg.encoding == 'rgb8':
        dtype = np.uint8
        n_channels = 3
    elif img_msg.encoding == 'bgr8':
        dtype = np.uint8
        n_channels = 3
    elif img_msg.encoding == 'mono8':
        dtype = np.uint8
        n_channels = 1
    elif img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
        dtype = np.uint8
        n_channels = 2
    else:
        raise ValueError(f"Unsupported encoding: {img_msg.encoding}")

    img_buf = np.asarray(img_msg.data, dtype=dtype)
    
    if img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
        cv_img = img_buf.reshape((img_msg.height, img_msg.width, 2))
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_YUV2BGR_YUY2)
    elif n_channels == 1:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width)
    else:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, n_channels)
    
    if img_msg.encoding == 'rgb8':
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)
        
    return cv_img