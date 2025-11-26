"""
WebRTC Video Stream Track
"""

import asyncio
import fractions
import time
import cv2
import numpy as np
from aiortc import MediaStreamTrack
from av import VideoFrame


class VideoStreamTrack(MediaStreamTrack):
    """
    Video track for WebRTC streaming from ROS2 camera
    """
    kind = 'video'
    
    def __init__(self, ros_node, fps=30):
        """
        Initialize video stream track
        
        Args:
            ros_node: CameraNode instance providing frames
            fps (int): Target frames per second
        """
        super().__init__()
        self.ros_node = ros_node
        self.fps = fps
        self._start = None
        
    async def recv(self):
        """
        Generate video frames for WebRTC stream
        
        Returns:
            VideoFrame: Next video frame with timestamp
        """
        if self._start is None:
            self._start = time.time()
        
        # Calculate timestamp
        now = time.time()
        elapsed = now - self._start
        pts = int(elapsed * 90000)  # 90kHz clock
        time_base = fractions.Fraction(1, 90000)
        
        frame = self.ros_node.get_frame()
        
        if frame is None:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(frame, "Waiting for camera...", (150, 240),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        video_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        
        await asyncio.sleep(1.0 / self.fps)
        return video_frame