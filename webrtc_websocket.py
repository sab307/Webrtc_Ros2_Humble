# #!/usr/bin/env python3
# """
# WebRTC Sender -- Streams ROS2 camera to a specific receiver IP via WebSocket.
# Usage: python3 webrtc_websocket_sender.py --receiver-ip localhost --receiver-port 8080
# """

# import argparse
# import asyncio
# import fractions
# import json
# import logging
# import threading
# import time

# # ✅ FIXED: Use 'websockets' (async library)
# import websockets

# # ✅ FIXED: Added RTCIceServer to imports
# from aiortc import (
#     MediaStreamTrack, 
#     RTCPeerConnection, 
#     RTCSessionDescription, 
#     RTCConfiguration, 
#     RTCIceServer
# )
# from av import VideoFrame
# import cv2
# from rclpy.node import Node
# import rclpy
# from sensor_msgs.msg import Image
# import numpy as np

# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)


# def imgmsg_to_cv2(img_msg):
#     """Convert ROS Image message to OpenCV image"""
#     if img_msg.encoding == 'rgb8':
#         dtype = np.uint8
#         n_channels = 3
#     elif img_msg.encoding == 'bgr8':
#         dtype = np.uint8
#         n_channels = 3
#     elif img_msg.encoding == 'mono8':
#         dtype = np.uint8
#         n_channels = 1
#     elif img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
#         dtype = np.uint8
#         n_channels = 2
#     else:
#         raise ValueError(f"Unsupported encoding: {img_msg.encoding}")

#     img_buf = np.asarray(img_msg.data, dtype=dtype)
    
#     if img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
#         cv_img = img_buf.reshape((img_msg.height, img_msg.width, 2))
#         cv_img = cv2.cvtColor(cv_img, cv2.COLOR_YUV2BGR_YUY2)
#     elif n_channels == 1:
#         cv_img = img_buf.reshape(img_msg.height, img_msg.width)
#     else:
#         cv_img = img_buf.reshape(img_msg.height, img_msg.width, n_channels)
    
#     if img_msg.encoding == 'rgb8':
#         cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)
        
#     return cv_img


# class CameraNode(Node):
#     """ROS2 Node that receives camera images"""
    
#     def __init__(self, camera_topic, resolution=(640, 480)):
#         super().__init__('webrtc_sender_node')
        
#         self.latest_frame = None
#         self.resolution = resolution
#         self.frame_lock = threading.Lock()
#         self.frame_count = 0
        
#         self.subscription = self.create_subscription(
#             Image,
#             camera_topic,
#             self.image_callback,
#             10
#         )
        
#         self.get_logger().info(f'Sender subscribed to: {camera_topic}')
#         self.get_logger().info(f'Resolution: {resolution}')
        
#     def image_callback(self, msg):
#         """Process incoming camera images"""
#         try:
#             if self.latest_frame is None:
#                 self.get_logger().info(f'First frame: {msg.width}x{msg.height}, {msg.encoding}')
                
#             cv_image = imgmsg_to_cv2(msg)
#             resized = cv2.resize(cv_image, self.resolution, interpolation=cv2.INTER_LANCZOS4)
            
#             with self.frame_lock:
#                 self.latest_frame = resized
#                 self.frame_count += 1
                
#         except Exception as e:
#             self.get_logger().error(f'Error in image callback: {str(e)}')
            
#     def get_frame(self):
#         """Return the latest frame (thread-safe)"""
#         with self.frame_lock:
#             return self.latest_frame.copy() if self.latest_frame is not None else None


# class VideoStreamTrack(MediaStreamTrack):
#     """Video track for WebRTC streaming"""
#     kind = 'video'
    
#     def __init__(self, ros_node, fps=30):
#         super().__init__()
#         self.ros_node = ros_node
#         self.fps = fps
#         self._start = None
        
#     async def recv(self):
#         """Generate video frames"""
#         if self._start is None:
#             self._start = time.time()
        
#         # Calculate timestamp
#         now = time.time()
#         elapsed = now - self._start
#         pts = int(elapsed * 90000)  # 90kHz clock
#         time_base = fractions.Fraction(1, 90000)
        
#         frame = self.ros_node.get_frame()
        
#         if frame is None:
#             frame = np.zeros((480, 640, 3), dtype=np.uint8)
#             cv2.putText(frame, "Waiting for camera...", (150, 240),
#                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
#         frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
#         video_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
#         video_frame.pts = pts
#         video_frame.time_base = time_base
        
#         await asyncio.sleep(1.0 / self.fps)
#         return video_frame


# async def send_stream(receiver_url, camera_topic, resolution, fps):
#     """Connect to receiver via WebSocket and stream video"""
    
#     # Initialize ROS2
#     rclpy.init()
#     ros_node = CameraNode(camera_topic, resolution)
    
#     # Run ROS2 node in separate thread
#     ros_thread = threading.Thread(target=lambda: rclpy.spin(ros_node), daemon=True)
#     ros_thread.start()
    
#     logger.info("Waiting for camera frames...")
#     await asyncio.sleep(2)
    
#     # ✅ Configure WebRTC - NO ICE servers needed for localhost
#     pc = RTCPeerConnection()
    
#     # ✅ Force VP8 codec by creating a custom media description
#     from aiortc.rtcrtpsender import RTCRtpSender
    
#     # Get VP8 codec
#     codecs = RTCRtpSender.getCapabilities("video").codecs
#     vp8_codec = None
#     for codec in codecs:
#         if codec.mimeType == "video/VP8":
#             vp8_codec = codec
#             logger.info(f"✅ Found VP8 codec: {codec}")
#             break
    
#     if not vp8_codec:
#         logger.error("❌ VP8 codec not available!")
#         return
    
#     # Add video track with VP8 preference
#     video_track = VideoStreamTrack(ros_node, fps)
    
#     # ✅ Add transceiver with VP8 codec preference
#     transceiver = pc.addTransceiver(
#         video_track,
#         direction="sendonly"
#     )
    
#     # ✅ Set codec preferences to VP8 only
#     transceiver.setCodecPreferences([vp8_codec])
    
#     logger.info("✅ Video track added with VP8 codec")
    
#     @pc.on("connectionstatechange")
#     async def on_connectionstatechange():
#         logger.info(f"Connection state: {pc.connectionState}")
#         if pc.connectionState == "connected":
#             logger.info("✅ Connected! Streaming VP8 video")
#         elif pc.connectionState == "failed":
#             logger.error("❌ Connection failed!")
#         elif pc.connectionState == "closed":
#             logger.info("Connection closed")
            
#     @pc.on("iceconnectionstatechange")
#     async def on_iceconnectionstatechange():
#         logger.info(f"ICE connection state: {pc.iceConnectionState}")
    
#     # Store websocket for ICE candidate handler
#     websocket_connection = None
    
#     @pc.on("icecandidate")
#     async def on_ice_candidate(candidate):
#         """Send ICE candidates to server via WebSocket"""
#         if candidate and websocket_connection:
#             logger.info(f"Sending ICE candidate to server")
#             try: 
#                 await websocket_connection.send(json.dumps({
#                     "type": "ice-candidate",
#                     "candidate": {
#                         "candidate": candidate.candidate,
#                         "sdpMid": candidate.sdpMid,
#                         "sdpMLineIndex": candidate.sdpMLineIndex,
#                     }
#                 }))
#             except Exception as e:
#                 logger.error(f"Failed to send ICE candidate: {e}")
    
#     logger.info(f"Connecting to receiver at {receiver_url}...")
    
#     try:
#         async with websockets.connect(
#             receiver_url,
#             ping_interval=10,
#             ping_timeout=30
#         ) as websocket:
#             websocket_connection = websocket
#             logger.info("✓ WebSocket connection established")
            
#             # Create offer
#             offer = await pc.createOffer()
#             await pc.setLocalDescription(offer)
            
#             # ✅ Log the SDP to verify VP8
#             logger.info(f"📤 SDP Offer created with VP8")
#             for line in offer.sdp.split('\n'):
#                 if 'rtpmap' in line or 'm=video' in line:
#                     logger.info(f"  {line.strip()}")
            
#             # Wait for ICE gathering
#             await asyncio.sleep(1)
            
#             # Send offer to Go server
#             logger.info("Sending offer to server...")
#             await websocket.send(json.dumps({
#                 "type": "offer",
#                 "sdp": pc.localDescription.sdp
#             }))
#             logger.info("✓ Offer sent to server")
            
#             # Handle incoming messages from server
#             async def receive_messages():
#                 """Process messages from Go server"""
#                 try:
#                     async for message in websocket:
#                         data = json.loads(message)
#                         msg_type = data.get("type")
                        
#                         logger.info(f"Received from server: type={msg_type}")
                        
#                         if msg_type == "answer":
#                             logger.info("Processing SDP answer...")
#                             answer = RTCSessionDescription(
#                                 sdp=data["sdp"],
#                                 type="answer"
#                             )
#                             await pc.setRemoteDescription(answer)
#                             logger.info("✓ Remote description set")
                            
#                             # ✅ Log negotiated codec
#                             for transceiver in pc.getTransceivers():
#                                 if transceiver.sender and transceiver.sender.track:
#                                     logger.info(f"✅ Negotiated codec: {transceiver.sender.transport._rtp._codecs}")
                        
#                         elif msg_type == "ice-candidate":
#                             candidate_data = data.get("candidate")
#                             if candidate_data:
#                                 logger.info(f"Adding server ICE candidate")
#                                 await pc.addIceCandidate(candidate_data)
#                                 logger.info("✓ Server ICE candidate added")
                
#                 except websockets.exceptions.ConnectionClosed:
#                     logger.info("WebSocket closed by server")
#                 except Exception as e:
#                     logger.error(f"Error receiving messages: {e}")
            
#             # Start message receiver
#             receive_task = asyncio.create_task(receive_messages())
            
#             # Keep connection alive
#             try:
#                 logger.info("🔄 Keeping connection alive with VP8 stream...")
#                 while pc.connectionState != "closed":
#                     await asyncio.sleep(1)
                    
#                     # Log status every 10 seconds
#                     if int(time.time()) % 10 == 0:
#                         logger.info(f"Status: PC={pc.connectionState}, Frames={ros_node.frame_count}")
            
#             except KeyboardInterrupt:
#                 logger.info("Shutting down...")
#             finally:
#                 receive_task.cancel()
#                 try:
#                     await receive_task
#                 except asyncio.CancelledError:
#                     pass
                
#                 await pc.close()
#                 ros_node.destroy_node()
#                 rclpy.shutdown()
    
#     except websockets.exceptions.WebSocketException as e:
#         logger.error(f"WebSocket connection failed: {e}")
#     except Exception as e:
#         logger.error(f"Error: {e}")
#         import traceback
#         traceback.print_exc()

# # async def send_stream(receiver_url, camera_topic, resolution, fps):
# #     """Connect to receiver via WebSocket and stream video"""
    
# #     # Initialize ROS2
# #     rclpy.init()
# #     ros_node = CameraNode(camera_topic, resolution)
    
# #     # Run ROS2 node in separate thread
# #     ros_thread = threading.Thread(target=lambda: rclpy.spin(ros_node), daemon=True)
# #     ros_thread.start()
    
# #     logger.info("Waiting for camera frames...")
# #     await asyncio.sleep(2)
    
# #     # Configure WebRTC
# #     config = RTCConfiguration(
# #         iceServers=[
# #             RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
# #             RTCIceServer(urls=["stun:stun1.l.google.com:19302"])
# #         ]
# #     )
    
# #     pc = RTCPeerConnection(configuration=config)
    
# #     # Add video track
# #     video_track = VideoStreamTrack(ros_node, fps)
# #     pc.addTrack(video_track)
    
# #     # ✅ Log what codec will be used
# #     for transceiver in pc.getTransceivers():
# #         if transceiver.sender and transceiver.sender.track:
# #             logger.info(f"📹 Added track: kind={transceiver.sender.track.kind}")
    
# #     @pc.on("connectionstatechange")
# #     async def on_connectionstatechange():
# #         logger.info(f"Connection state: {pc.connectionState}")
# #         if pc.connectionState == "connected":
# #             logger.info("✅ Connected! Streaming video")
# #         elif pc.connectionState == "failed":
# #             logger.error("❌ Connection failed!")
# #         elif pc.connectionState == "closed":
# #             logger.info("Connection closed")
            
# #     @pc.on("iceconnectionstatechange")
# #     async def on_iceconnectionstatechange():
# #         logger.info(f"ICE connection state: {pc.iceConnectionState}")
    
# #     # Store websocket for ICE candidate handler
# #     websocket_connection = None
    
# #     @pc.on("icecandidate")
# #     async def on_ice_candidate(candidate):
# #         """Send ICE candidates to server via WebSocket"""
# #         if candidate and websocket_connection:
# #             logger.info(f"Sending ICE candidate to server: {candidate.candidate}")
# #             try: 
# #                 await websocket_connection.send(json.dumps({
# #                     "type": "ice-candidate",
# #                     "candidate": {
# #                         "candidate": candidate.candidate,
# #                         "sdpMid": candidate.sdpMid,
# #                         "sdpMLineIndex": candidate.sdpMLineIndex,
# #                     }
# #                 }))
# #             except Exception as e:
# #                 logger.error(f"Failed to send ICE candidate: {e}")
    
# #     logger.info(f"Connecting to receiver at {receiver_url}...")
    
# #     try:
# #         # ✅ NEW: Add ping_interval and ping_timeout to keep connection alive
# #         async with websockets.connect(
# #             receiver_url,
# #             ping_interval=10,  # Send ping every 10 seconds
# #             ping_timeout=30    # Wait 30 seconds for pong
# #         ) as websocket:
# #             websocket_connection = websocket
# #             logger.info("✓ WebSocket connection established")
            
# #             # Create offer
# #             offer = await pc.createOffer()
# #             await pc.setLocalDescription(offer)
# #             logger.info("SDP offer created")
            
# #             # Wait for ICE gathering
# #             await asyncio.sleep(1)
            
# #             # Send offer to Go server
# #             logger.info("Sending offer to server...")
# #             await websocket.send(json.dumps({
# #                 "type": "offer",
# #                 "sdp": pc.localDescription.sdp
# #             }))
# #             logger.info("✓ Offer sent to server")
            
# #             # Handle incoming messages from server
# #             async def receive_messages():
# #                 """Process messages from Go server"""
# #                 try:
# #                     async for message in websocket:
# #                         data = json.loads(message)
# #                         msg_type = data.get("type")
                        
# #                         logger.info(f"Received from server: type={msg_type}")
                        
# #                         if msg_type == "answer":
# #                             logger.info("Processing SDP answer...")
# #                             answer = RTCSessionDescription(
# #                                 sdp=data["sdp"],
# #                                 type="answer"
# #                             )
# #                             await pc.setRemoteDescription(answer)
# #                             logger.info("✓ Remote description set")
                        
# #                         elif msg_type == "ice-candidate":
# #                             candidate_data = data.get("candidate")
# #                             if candidate_data:
# #                                 logger.info(f"Adding server ICE candidate")
# #                                 await pc.addIceCandidate(candidate_data)
# #                                 logger.info("✓ Server ICE candidate added")
                
# #                 except websockets.exceptions.ConnectionClosed:
# #                     logger.info("WebSocket closed by server")
# #                 except Exception as e:
# #                     logger.error(f"Error receiving messages: {e}")
            
# #             # Start message receiver
# #             receive_task = asyncio.create_task(receive_messages())
            
# #             # Keep connection alive
# #             try:
# #                 logger.info("🔄 Keeping connection alive...")
# #                 while pc.connectionState != "closed":
# #                     await asyncio.sleep(1)
                    
# #                     # Log status every 10 seconds
# #                     if int(time.time()) % 10 == 0:
# #                         logger.info(f"Status: PC={pc.connectionState}, Frames={ros_node.frame_count}")
            
# #             except KeyboardInterrupt:
# #                 logger.info("Shutting down...")
# #             finally:
# #                 receive_task.cancel()
# #                 try:
# #                     await receive_task
# #                 except asyncio.CancelledError:
# #                     pass
                
# #                 await pc.close()
# #                 ros_node.destroy_node()
# #                 rclpy.shutdown()
    
# #     except websockets.exceptions.WebSocketException as e:
# #         logger.error(f"WebSocket connection failed: {e}")
# #     except Exception as e:
# #         logger.error(f"Error: {e}")
# #         import traceback
# #         traceback.print_exc()

# def main():
#     parser = argparse.ArgumentParser(description="WebRTC Video Sender (WebSocket)")
#     parser.add_argument("--receiver-ip", required=True, help="Receiver IP address")
#     parser.add_argument("--receiver-port", type=int, default=8080, help="Receiver port")
#     parser.add_argument("--camera-topic", default="/camera1/image_raw", help="ROS2 camera topic")
#     parser.add_argument("--resolution", default="640x480", help="Video resolution (WxH)")
#     parser.add_argument("--fps", type=int, default=30, help="Frames per second")
#     args = parser.parse_args()
    
#     # Parse resolution
#     width, height = map(int, args.resolution.split('x'))
#     resolution = (width, height)
    
#     # ✅ Construct WebSocket URL
#     receiver_url = f"ws://{args.receiver_ip}:{args.receiver_port}/ws"
    
#     logger.info("=" * 60)
#     logger.info("WebRTC Video Sender (WebSocket)")
#     logger.info("=" * 60)
#     logger.info(f"Camera topic: {args.camera_topic}")
#     logger.info(f"Resolution: {resolution}")
#     logger.info(f"FPS: {args.fps}")
#     logger.info(f"Receiver: {receiver_url}")
#     logger.info("=" * 60)
    
#     asyncio.run(send_stream(receiver_url, args.camera_topic, resolution, args.fps))


# if __name__ == "__main__":
#     main()


#!/usr/bin/env python3
"""
WebRTC Sender with Keepalive -- Streams ROS2 camera via WebSocket with heartbeat
Usage: python3 webrtc_sender_keepalive.py --receiver-ip localhost --receiver-port 8080
"""

import argparse
import asyncio
import fractions
import json
import logging
import threading
import time

import websockets

from aiortc import (
    MediaStreamTrack, 
    RTCPeerConnection, 
    RTCSessionDescription,
)
from av import VideoFrame
import cv2
from rclpy.node import Node
import rclpy
from sensor_msgs.msg import Image
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def imgmsg_to_cv2(img_msg):
    """Convert ROS Image message to OpenCV image"""
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


class CameraNode(Node):
    """ROS2 Node that receives camera images"""
    
    def __init__(self, camera_topic, resolution=(640, 480)):
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
        """Process incoming camera images"""
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
        """Return the latest frame (thread-safe)"""
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None


class VideoStreamTrack(MediaStreamTrack):
    """Video track for WebRTC streaming"""
    kind = 'video'
    
    def __init__(self, ros_node, fps=30):
        super().__init__()
        self.ros_node = ros_node
        self.fps = fps
        self._start = None
        
    async def recv(self):
        """Generate video frames"""
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


async def send_stream(receiver_url, camera_topic, resolution, fps):
    """Connect to receiver via WebSocket and stream video"""
    
    # Initialize ROS2
    rclpy.init()
    ros_node = CameraNode(camera_topic, resolution)
    
    # Run ROS2 node in separate thread
    ros_thread = threading.Thread(target=lambda: rclpy.spin(ros_node), daemon=True)
    ros_thread.start()
    
    logger.info("Waiting for camera frames...")
    await asyncio.sleep(2)
    
    # Configure WebRTC
    pc = RTCPeerConnection()
    
    # Force VP8 codec
    from aiortc.rtcrtpsender import RTCRtpSender
    
    codecs = RTCRtpSender.getCapabilities("video").codecs
    vp8_codec = None
    for codec in codecs:
        if codec.mimeType == "video/VP8":
            vp8_codec = codec
            logger.info(f"Found VP8 codec: {codec}")
            break
    
    if not vp8_codec:
        logger.error("VP8 codec not available!")
        return
    
    # Add video track with VP8 preference
    video_track = VideoStreamTrack(ros_node, fps)
    transceiver = pc.addTransceiver(video_track, direction="sendonly")
    transceiver.setCodecPreferences([vp8_codec])
    
    logger.info("Video track added with VP8 codec")
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state changed to: {pc.connectionState}")
        if pc.connectionState == "connected":
            logger.info("WebRTC CONNECTED! Streaming VP8 video")
        elif pc.connectionState == "failed":
            logger.error("WebRTC connection failed!")
        elif pc.connectionState == "closed":
            logger.info("WebRTC connection closed")
            
    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE state changed to: {pc.iceConnectionState}")
    
    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():
        logger.info(f"ICE gathering state: {pc.iceGatheringState}")
    
    # Store websocket for ICE candidate handler
    websocket_connection = None
    
    @pc.on("icecandidate")
    async def on_ice_candidate(candidate):
        """Send ICE candidates to server via WebSocket"""
        if candidate and websocket_connection:
            logger.info(f"Sending ICE candidate to server")
            try: 
                await websocket_connection.send(json.dumps({
                    "type": "ice-candidate",
                    "candidate": {
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    }
                }))
                logger.info("ICE candidate sent")
            except Exception as e:
                logger.error(f"Failed to send ICE candidate: {e}")
    
    logger.info(f"Connecting to receiver at {receiver_url}...")
    
    try:
        async with websockets.connect(
            receiver_url,
            ping_interval=20,
            ping_timeout=60
        ) as websocket:
            websocket_connection = websocket
            logger.info("WebSocket connection established")
            
            # Create offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            logger.info(f"Created SDP offer")
            
            # Wait for ICE gathering to complete
            logger.info("Waiting for ICE candidates...")
            await asyncio.sleep(2)
            
            # Send offer to Go server
            logger.info("Sending offer to server...")
            await websocket.send(json.dumps({
                "type": "offer",
                "sdp": pc.localDescription.sdp
            }))
            logger.info("Offer sent successfully")
            
            # Handle incoming messages from server
            async def receive_messages():
                """Process messages from Go server"""
                try:
                    async for message in websocket:
                        try:
                            # Use json.loads (not json.dumps!)
                            data = json.loads(message)
                            msg_type = data.get("type")
                            
                            logger.info(f"Received from server: type={msg_type}")
                            
                            if msg_type == "answer":
                                logger.info("Processing SDP answer...")
                                answer = RTCSessionDescription(
                                    sdp=data["sdp"],
                                    type="answer"
                                )
                                await pc.setRemoteDescription(answer)
                                logger.info("Remote description (answer) set successfully!")
                            
                            elif msg_type == "ice-candidate":
                                candidate_data = data.get("candidate")
                                if candidate_data:
                                    logger.info(f"Adding server ICE candidate")
                                    await pc.addIceCandidate(candidate_data)
                                    logger.info("Server ICE candidate added")
                            
                            elif msg_type == "pong":
                                # Server acknowledging our ping
                                logger.debug("Pong received from server")
                        
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse JSON message: {e}")
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")
                            import traceback
                            traceback.print_exc()
                
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"WebSocket closed: {e}")
                except Exception as e:
                    logger.error(f"Error in receive_messages: {e}")
                    import traceback
                    traceback.print_exc()
            
            # Send periodic heartbeat to keep WebSocket alive
            async def send_heartbeat():
                """Send periodic ping messages"""
                try:
                    while pc.connectionState != "closed":
                        await asyncio.sleep(5)
                        try:
                            await websocket.send(json.dumps({"type": "ping"}))
                            logger.debug("Heartbeat sent")
                        except Exception as e:
                            logger.error(f"Heartbeat failed: {e}")
                            break
                except asyncio.CancelledError:
                    logger.debug("Heartbeat task cancelled")
            
            # Start background tasks
            receive_task = asyncio.create_task(receive_messages())
            heartbeat_task = asyncio.create_task(send_heartbeat())
            
            # Keep connection alive
            try:
                logger.info("Streaming... (Press Ctrl+C to stop)")
                while pc.connectionState != "closed":
                    await asyncio.sleep(1)
                    
                    # Log status every 10 seconds
                    if int(time.time()) % 10 == 0:
                        logger.info(f"Status: WebRTC={pc.connectionState}, ICE={pc.iceConnectionState}, Frames={ros_node.frame_count}")
            
            except KeyboardInterrupt:
                logger.info("Shutting down...")
            finally:
                receive_task.cancel()
                heartbeat_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                
                await pc.close()
                ros_node.destroy_node()
                rclpy.shutdown()
    
    except websockets.exceptions.WebSocketException as e:
        logger.error(f"WebSocket connection failed: {e}")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="WebRTC Video Sender with Keepalive")
    parser.add_argument("--receiver-ip", required=True, help="Receiver IP address")
    parser.add_argument("--receiver-port", type=int, default=8080, help="Receiver port")
    parser.add_argument("--camera-topic", default="/camera1/image_raw", help="ROS2 camera topic")
    parser.add_argument("--resolution", default="640x480", help="Video resolution (WxH)")
    parser.add_argument("--fps", type=int, default=30, help="Frames per second")
    args = parser.parse_args()
    
    # Parse resolution
    width, height = map(int, args.resolution.split('x'))
    resolution = (width, height)
    
    receiver_url = f"ws://{args.receiver_ip}:{args.receiver_port}/ws"
    
    logger.info("=" * 60)
    logger.info("WebRTC Video Sender with Keepalive")
    logger.info("=" * 60)
    logger.info(f"Camera topic: {args.camera_topic}")
    logger.info(f"Resolution: {resolution}")
    logger.info(f"FPS: {args.fps}")
    logger.info(f"Receiver: {receiver_url}")
    logger.info("=" * 60)
    
    asyncio.run(send_stream(receiver_url, args.camera_topic, resolution, args.fps))


if __name__ == "__main__":
    main()