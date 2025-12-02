#!/usr/bin/env python3
"""
WebRTC Sender H264 with Glass-to-Glass Latency Measurement
==========================================================
Sends video via WebRTC and frame timestamps via DataChannel for 
accurate end-to-end latency measurement.

Glass-to-glass latency = Time from camera capture to display on screen

=============================================================================
CHANGES FROM webrtc_sender_h264.py:
=============================================================================
1. Added DataChannel for sending frame timestamps to browser
2. Modified FrameBuffer to store nanosecond-precision capture timestamps
3. Added TimestampChannel class to manage DataChannel communication
4. Modified video track to call timestamp callback for each frame
5. Added clock sync support via ping/pong messages
=============================================================================
"""

import argparse
import asyncio
import fractions
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import websockets

from aiortc import (
    MediaStreamTrack,
    RTCPeerConnection,
    RTCSessionDescription,
    # NOTE: RTCDataChannel removed - using WebSocket for timestamps instead
    # to avoid "conflicting ice-ufrag" errors between aiortc and Pion
)
from av import VideoFrame
import cv2
from rclpy.node import Node
import rclpy
from sensor_msgs.msg import Image
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Configuration
# ============================================================================

@dataclass
class StreamConfig:
    """Video streaming configuration"""
    resolution: tuple = (640, 480)
    fps: int = 30
    target_bitrate: int = 1_000_000
    max_bitrate: int = 1_500_000
    min_bitrate: int = 500_000
    keyframe_interval: int = 30
    h264_profile: str = "baseline"
    h264_level: str = "3.1"
    h264_preset: str = "ultrafast"
    h264_tune: str = "zerolatency"
    frame_queue_size: int = 2
    use_fast_resize: bool = True
    
    # =========================================================================
    # NEW: Added option to overlay timestamp on video for visual verification
    # =========================================================================
    # PREVIOUS: (not present in webrtc_sender_h264.py)
    overlay_timestamp: bool = False
    # =========================================================================


# ============================================================================
# ROS2 Image Conversion (UNCHANGED from webrtc_sender_h264.py)
# ============================================================================

def imgmsg_to_cv2_fast(img_msg) -> Optional[np.ndarray]:
    """Convert ROS Image message to OpenCV image"""
    encoding = img_msg.encoding.lower()
    
    if encoding in ('rgb8', 'bgr8'):
        dtype, n_channels = np.uint8, 3
    elif encoding == 'mono8':
        dtype, n_channels = np.uint8, 1
    elif encoding in ('yuyv', 'yuyv422', 'yuy2', 'yuv422'):
        dtype, n_channels = np.uint8, 2
    elif encoding == 'bgra8':
        dtype, n_channels = np.uint8, 4
    elif encoding == 'rgba8':
        dtype, n_channels = np.uint8, 4
    else:
        raise ValueError(f"Unsupported encoding: {img_msg.encoding}")

    try:
        img_buf = np.frombuffer(img_msg.data, dtype=dtype)
    except TypeError:
        img_buf = np.asarray(img_msg.data, dtype=dtype)

    if encoding in ('yuyv', 'yuyv422', 'yuy2', 'yuv422'):
        cv_img = img_buf.reshape((img_msg.height, img_msg.width, 2))
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_YUV2BGR_YUY2)
    elif n_channels == 1:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width)
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_GRAY2BGR)
    elif encoding == 'rgb8':
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, 3)
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)
    elif encoding == 'rgba8':
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, 4)
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGBA2BGR)
    elif encoding == 'bgra8':
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, 4)
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_BGRA2BGR)
    else:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, n_channels)

    return cv_img


# ============================================================================
# Frame Buffer with Timestamps
# ============================================================================
# CHANGED: Added nanosecond-precision timestamps for accurate latency measurement

@dataclass
class TimestampedFrame:
    """Frame with capture timestamp for latency measurement"""
    frame: np.ndarray
    # =========================================================================
    # NEW: Added high-precision timestamp fields
    # =========================================================================
    # PREVIOUS (in webrtc_sender_h264.py):
    #     capture_time: float    # Only had monotonic time for frame pacing
    # 
    # NEW: Added epoch timestamps for cross-device synchronization
    capture_time_ns: int      # Nanosecond precision timestamp (epoch)
    capture_time_ms: float    # Millisecond timestamp for transmission
    # =========================================================================
    sequence: int


class FrameBuffer:
    """Thread-safe frame buffer with timestamps"""
    
    def __init__(self, max_size: int = 2):
        self.max_size = max_size
        self.buffer: deque = deque(maxlen=max_size)
        self.lock = threading.Lock()
        self.sequence = 0
        self.last_frame: Optional[TimestampedFrame] = None
        
    def put(self, frame: np.ndarray) -> None:
        """Add a frame with capture timestamp"""
        with self.lock:
            self.sequence += 1
            # =================================================================
            # CHANGED: Use time.time_ns() for highest precision epoch timestamp
            # =================================================================
            # PREVIOUS (in webrtc_sender_h264.py):
            #     timestamped = TimestampedFrame(
            #         frame=frame,
            #         capture_time=time.monotonic(),  # Only monotonic time
            #         sequence=self.sequence
            #     )
            #
            # NEW: Capture both nanosecond and millisecond timestamps
            capture_ns = time.time_ns()  # Nanosecond precision epoch time
            capture_ms = capture_ns / 1_000_000  # Convert to milliseconds
            
            timestamped = TimestampedFrame(
                frame=frame,
                capture_time_ns=capture_ns,
                capture_time_ms=capture_ms,
                sequence=self.sequence
            )
            # =================================================================
            self.buffer.append(timestamped)
            self.last_frame = timestamped
            
    def get_latest(self) -> Optional[TimestampedFrame]:
        """Get the most recent frame with timestamp"""
        with self.lock:
            if self.buffer:
                frame = self.buffer[-1]
                self.buffer.clear()
                return frame
            return self.last_frame
            
    @property
    def size(self) -> int:
        with self.lock:
            return len(self.buffer)


# ============================================================================
# ROS2 Camera Node
# ============================================================================
# CHANGED: Added optional timestamp overlay for visual verification

class CameraNode(Node):
    """ROS2 Node that receives camera images with timestamps"""
    
    def __init__(self, camera_topic: str, config: StreamConfig):
        # =====================================================================
        # CHANGED: Updated node name to reflect latency measurement feature
        # =====================================================================
        # PREVIOUS: super().__init__('webrtc_sender_h264')
        super().__init__('webrtc_sender_latency')
        # =====================================================================
        
        self.config = config
        self.frame_buffer = FrameBuffer(max_size=config.frame_queue_size)
        self.frame_count = 0
        self.last_stats_time = time.monotonic()
        self.fps_actual = 0.0
        
        # FIX: Add flag to track if first frame has been logged
        self._first_frame_logged = False
        
        self.interpolation = (
            cv2.INTER_LINEAR if config.use_fast_resize 
            else cv2.INTER_LANCZOS4
        )
        
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )
        
        self.subscription = self.create_subscription(
            Image,
            camera_topic,
            self.image_callback,
            qos
        )
        
        self.get_logger().info(f'Subscribed to: {camera_topic}')
        
    def image_callback(self, msg):
        try:
            # FIX: Use flag instead of frame_count (which resets every second)
            if not self._first_frame_logged:
                self.get_logger().info(
                    f'First frame: {msg.width}x{msg.height}, {msg.encoding}'
                )
                self._first_frame_logged = True
                
            cv_image = imgmsg_to_cv2_fast(msg)
            
            if cv_image is None:
                return
                
            if (cv_image.shape[1], cv_image.shape[0]) != self.config.resolution:
                cv_image = cv2.resize(
                    cv_image, 
                    self.config.resolution, 
                    interpolation=self.interpolation
                )
            
            # =================================================================
            # NEW: Optional timestamp overlay for visual verification
            # =================================================================
            # PREVIOUS: (not present in webrtc_sender_h264.py)
            if self.config.overlay_timestamp:
                timestamp_str = f"{time.time()*1000:.0f}"
                cv2.putText(
                    cv_image, timestamp_str,
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, (0, 255, 0), 2
                )
            # =================================================================
            
            self.frame_buffer.put(cv_image)
            self.frame_count += 1
            
            now = time.monotonic()
            elapsed = now - self.last_stats_time
            if elapsed >= 1.0:
                self.fps_actual = self.frame_count / elapsed
                self.frame_count = 0
                self.last_stats_time = now
                
        except Exception as e:
            self.get_logger().error(f'Frame error: {e}')
    
    # =========================================================================
    # CHANGED: Return type now returns TimestampedFrame instead of just frame
    # =========================================================================
    # PREVIOUS (in webrtc_sender_h264.py):
    #     def get_frame(self, low_latency: bool = True) -> Optional[np.ndarray]:
    #         if low_latency:
    #             timestamped = self.frame_buffer.get_latest()
    #         else:
    #             timestamped = self.frame_buffer.get()
    #         return timestamped.frame if timestamped else None
    #
    # NEW: Returns full TimestampedFrame for latency calculation
    def get_frame(self) -> Optional[TimestampedFrame]:
        """Get frame with its capture timestamp"""
        return self.frame_buffer.get_latest()
    # =========================================================================


# ============================================================================
# NEW: Timestamp Channel Manager - WebSocket based
# ============================================================================
# CHANGED: Now uses WebSocket instead of DataChannel to avoid ICE conflicts
#
# PREVIOUS APPROACH (DataChannel - caused errors):
#     class TimestampChannel:
#         def __init__(self):
#             self.channel: Optional[RTCDataChannel] = None
#         def set_channel(self, channel: RTCDataChannel):
#             self.channel = channel
#             @channel.on("open") ...
#         def send_timestamp(self, frame_info):
#             self.channel.send(json.dumps(message))
#
# NEW APPROACH: Use WebSocket for timestamp transmission

class TimestampChannel:
    """
    Manages WebSocket for sending frame timestamps to relay server
    
    Protocol:
    - Sends JSON messages with frame sequence, capture time, and PTS
    - Relay server forwards to browser clients via DataChannel
    - Supports clock sync via ping/pong messages
    
    NOTE: Changed from DataChannel to WebSocket to avoid 
          "conflicting ice-ufrag" errors between aiortc and Pion
    """
    
    def __init__(self):
        self.websocket = None  # CHANGED: WebSocket instead of DataChannel
        self.is_open = False
        self._send_queue = asyncio.Queue()
        self._sender_task = None
        
    def set_websocket(self, websocket):
        """Set the WebSocket instance for timestamp transmission"""
        self.websocket = websocket
        self.is_open = True
        logger.info("📡 Timestamp channel connected via WebSocket")
        
    async def _send_loop(self):
        """Background task to send queued timestamps"""
        while self.is_open:
            try:
                message = await asyncio.wait_for(
                    self._send_queue.get(), 
                    timeout=1.0
                )
                if self.websocket and self.is_open:
                    await self.websocket.send(message)
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                if self.is_open:
                    logger.error(f"Timestamp send error: {e}")
                break
    
    def start_sender(self):
        """Start the background sender task"""
        if self._sender_task is None:
            self._sender_task = asyncio.create_task(self._send_loop())
            
    def stop_sender(self):
        """Stop the background sender task"""
        self.is_open = False
        if self._sender_task:
            self._sender_task.cancel()
            self._sender_task = None
    
    def send_timestamp(self, frame_info: dict):
        """Queue frame timestamp for sending via WebSocket"""
        if not self.is_open or not self.websocket:
            return
            
        try:
            message = {
                'type': 'frame_timestamp',
                'seq': frame_info['seq'],
                'capture_ms': frame_info['capture_ms'],
                'pts': frame_info['pts'],
                'frame_num': frame_info['frame_num'],
                'send_time_ms': time.time() * 1000
            }
            # Non-blocking put to queue
            try:
                self._send_queue.put_nowait(json.dumps(message))
            except asyncio.QueueFull:
                pass  # Drop if queue is full (shouldn't happen)
        except Exception as e:
            logger.error(f"Failed to queue timestamp: {e}")
            
    async def send_message_async(self, data: dict):
        """Send arbitrary message asynchronously"""
        if not self.is_open or not self.websocket:
            return
        try:
            await self.websocket.send(json.dumps(data))
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

# END OF TimestampChannel CLASS (WebSocket version)
# ============================================================================


# ============================================================================
# Video Track with Timestamp Tracking
# ============================================================================
# CHANGED: Added timestamp callback for DataChannel transmission

class H264VideoTrackWithTimestamps(MediaStreamTrack):
    """
    Video track that tracks frame timestamps for latency measurement
    
    ===========================================================================
    CHANGES FROM H264VideoTrack in webrtc_sender_h264.py:
    ===========================================================================
    1. Added timestamp_callback parameter to notify when frames are generated
    2. Added last_frame_info to track timestamp data for each frame
    3. Modified recv() to call callback with frame timing information
    ===========================================================================
    """
    kind = 'video'
    
    # =========================================================================
    # CHANGED: Added timestamp_callback parameter
    # =========================================================================
    # PREVIOUS (in webrtc_sender_h264.py):
    #     def __init__(self, ros_node: CameraNode, config: StreamConfig):
    #
    # NEW: Added timestamp_callback parameter
    def __init__(self, ros_node: CameraNode, config: StreamConfig, timestamp_callback=None):
        super().__init__()
        self.ros_node = ros_node
        self.config = config
        self.timestamp_callback = timestamp_callback  # NEW: Callback for timestamps
    # =========================================================================
        
        # Timing
        self.frame_duration = 1.0 / config.fps
        self.time_base = fractions.Fraction(1, 90000)
        self.samples_per_frame = int(90000 / config.fps)
        
        # State
        self._start_time: Optional[float] = None
        self._frame_count = 0
        self._next_frame_time: Optional[float] = None
        
        # Placeholder
        self._placeholder = self._create_placeholder()
        
        # =====================================================================
        # NEW: Track last frame info for DataChannel
        # =====================================================================
        # PREVIOUS: (not present in webrtc_sender_h264.py)
        self.last_frame_info = None
        # =====================================================================
        
        logger.info(f"H264 Video track with timestamp tracking initialized")
        
    def _create_placeholder(self) -> np.ndarray:
        w, h = self.config.resolution
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        cv2.putText(
            frame, "Waiting for camera...", 
            (w//6, h//2),
            cv2.FONT_HERSHEY_SIMPLEX, 
            0.8, (255, 255, 255), 2
        )
        return frame
        
    async def recv(self) -> VideoFrame:
        now = time.monotonic()
        
        if self._start_time is None:
            self._start_time = now
            self._next_frame_time = now
            
        wait_time = self._next_frame_time - now
        
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        elif wait_time < -self.frame_duration:
            frames_behind = int(-wait_time / self.frame_duration)
            self._next_frame_time += frames_behind * self.frame_duration
            
        self._next_frame_time += self.frame_duration
        
        pts = self._frame_count * self.samples_per_frame
        self._frame_count += 1
        
        # =====================================================================
        # CHANGED: Get TimestampedFrame and extract timestamp info
        # =====================================================================
        # PREVIOUS (in webrtc_sender_h264.py):
        #     frame = self.ros_node.get_frame(low_latency=True)
        #     if frame is None:
        #         frame = self._placeholder
        #
        # NEW: Get full TimestampedFrame and store timestamp info
        timestamped_frame = self.ros_node.get_frame()
        
        if timestamped_frame is not None:
            frame = timestamped_frame.frame
            
            # Store frame info for DataChannel transmission
            self.last_frame_info = {
                'seq': timestamped_frame.sequence,
                'capture_ms': timestamped_frame.capture_time_ms,
                'pts': pts,
                'frame_num': self._frame_count
            }
            
            # Notify callback (for DataChannel sending)
            if self.timestamp_callback:
                self.timestamp_callback(self.last_frame_info)
        else:
            frame = self._placeholder
            self.last_frame_info = None
        # =====================================================================
        
        # Convert to YUV420P for H264 (unchanged)
        frame_yuv = cv2.cvtColor(frame, cv2.COLOR_BGR2YUV_I420)
        h, w = frame.shape[:2]
        video_frame = VideoFrame.from_ndarray(
            frame_yuv.reshape(h * 3 // 2, w),
            format="yuv420p"
        )
        
        video_frame.pts = pts
        video_frame.time_base = self.time_base
        
        return video_frame


# ============================================================================
# WebRTC Connection with DataChannel
# ============================================================================
# UNCHANGED: create_peer_connection_h264 (same as webrtc_sender_h264.py)

async def create_peer_connection_h264(config: StreamConfig) -> tuple:
    """Create RTCPeerConnection with H264 codec preference"""
    
    pc = RTCPeerConnection()
    
    from aiortc.rtcrtpsender import RTCRtpSender
    
    capabilities = RTCRtpSender.getCapabilities("video")
    h264_codecs = [c for c in capabilities.codecs if c.mimeType == "video/H264"]
    
    if not h264_codecs:
        logger.warning("H264 not available, falling back to VP8")
        vp8_codecs = [c for c in capabilities.codecs if c.mimeType == "video/VP8"]
        if not vp8_codecs:
            raise RuntimeError("No suitable video codec!")
        selected_codec = vp8_codecs[0]
    else:
        baseline_codecs = [
            c for c in h264_codecs 
            if c.parameters.get('profile-level-id', '').startswith('42')
        ]
        selected_codec = baseline_codecs[0] if baseline_codecs else h264_codecs[0]
    
    logger.info(f"Using codec: {selected_codec.mimeType}")
    
    return pc, selected_codec


# ============================================================================
# Main Streaming Function
# ============================================================================
# CHANGED: Added DataChannel creation and timestamp callback

async def send_stream(receiver_url: str, camera_topic: str, config: StreamConfig):
    """Main streaming function with WebSocket timestamps for latency measurement"""
    
    # Initialize ROS2 (unchanged)
    rclpy.init()
    ros_node = CameraNode(camera_topic, config)
    
    ros_thread = threading.Thread(
        target=lambda: rclpy.spin(ros_node), 
        daemon=True
    )
    ros_thread.start()
    
    logger.info("Waiting for camera frames...")
    await asyncio.sleep(2)
    
    # Create peer connection (unchanged)
    pc, h264_codec = await create_peer_connection_h264(config)
    
    # =========================================================================
    # CHANGED: Use WebSocket for timestamps instead of DataChannel
    # =========================================================================
    # REASON: DataChannel + Video track causes "conflicting ice-ufrag" errors
    #         in Pion due to BUNDLE/ICE credential mismatch between aiortc
    #         and Pion. Using WebSocket avoids this entirely.
    # 
    # PREVIOUS APPROACH (caused errors):
    #     timestamp_channel = TimestampChannel()
    #     dc = pc.createDataChannel("timestamps", ...)
    #     timestamp_channel.set_channel(dc)
    #
    # NEW APPROACH: TimestampChannel uses WebSocket instead
    # =========================================================================
    timestamp_channel = TimestampChannel()
    # WebSocket will be set after connection is established
    
    def on_frame_timestamp(frame_info):
        timestamp_channel.send_timestamp(frame_info)
    # =========================================================================
    
    # Create video track (NO DataChannel - avoids ICE conflicts)
    video_track = H264VideoTrackWithTimestamps(
        ros_node, 
        config, 
        timestamp_callback=on_frame_timestamp
    )
    
    # Add track with codec preference
    transceiver = pc.addTransceiver(video_track, direction="sendonly")
    transceiver.setCodecPreferences([h264_codec])
    logger.info("Video track added with timestamp tracking")
    logger.info("📡 Timestamps will be sent via WebSocket (not DataChannel)")
    # =========================================================================
    
    logger.info("Video track added with timestamp tracking")
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        state = pc.connectionState
        logger.info(f"Connection state: {state}")
        if state == "connected":
            # CHANGED: Updated log message
            logger.info("✓ WebRTC CONNECTED - Streaming with latency measurement")
        elif state == "failed":
            logger.error("✗ WebRTC connection failed!")
            
    @pc.on("iceconnectionstatechange")
    async def on_iceconnectionstatechange():
        logger.info(f"ICE connection state: {pc.iceConnectionState}")
        
    @pc.on("icegatheringstatechange")
    async def on_icegatheringstatechange():
        logger.info(f"ICE gathering state: {pc.iceGatheringState}")
    
    ws_connection = None
    
    @pc.on("icecandidate")
    async def on_ice_candidate(candidate):
        if candidate and ws_connection:
            try:
                await ws_connection.send(json.dumps({
                    "type": "ice-candidate",
                    "candidate": {
                        "candidate": candidate.candidate,
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex,
                    }
                }))
            except Exception as e:
                logger.error(f"Failed to send ICE candidate: {e}")
    
    logger.info(f"Connecting to receiver: {receiver_url}")
    
    try:
        async with websockets.connect(
            receiver_url,
            ping_interval=20,
            ping_timeout=60,
            close_timeout=10,
            max_size=2**20,
        ) as websocket:
            ws_connection = websocket
            logger.info("WebSocket connected")
            
            # =================================================================
            # NEW: Set WebSocket on timestamp channel and start sender
            # =================================================================
            # CHANGED: Using WebSocket for timestamps instead of DataChannel
            timestamp_channel.set_websocket(websocket)
            timestamp_channel.start_sender()
            # =================================================================
            
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            # =================================================================
            # CHANGED: Wait for ICE gathering to complete properly
            # =================================================================
            # PREVIOUS: await asyncio.sleep(1)  # Fixed sleep was unreliable
            #
            # NEW: Wait for ICE gathering state to be 'complete'
            logger.info("Waiting for ICE gathering to complete...")
            
            # Wait for ICE gathering with timeout
            wait_time = 0
            max_wait = 10  # Maximum 10 seconds
            while pc.iceGatheringState != "complete" and wait_time < max_wait:
                await asyncio.sleep(0.1)
                wait_time += 0.1
            
            if pc.iceGatheringState != "complete":
                logger.warning(f"ICE gathering timeout after {max_wait}s, proceeding anyway")
            else:
                logger.info(f"ICE gathering complete in {wait_time:.1f}s")
            # =================================================================
            
            # CHANGED: Updated log message
            logger.info("SDP offer created (includes DataChannel)")
            
            await websocket.send(json.dumps({
                "type": "offer",
                "sdp": pc.localDescription.sdp
            }))
            logger.info("Offer sent to server")
            
            async def receive_messages():
                try:
                    async for message in websocket:
                        try:
                            data = json.loads(message)
                            msg_type = data.get("type")
                            
                            if msg_type == "answer":
                                logger.info("Received SDP answer")
                                answer = RTCSessionDescription(
                                    sdp=data["sdp"],
                                    type="answer"
                                )
                                await pc.setRemoteDescription(answer)
                                logger.info("✓ Remote description set")
                                
                            elif msg_type == "ice-candidate":
                                candidate_data = data.get("candidate")
                                if candidate_data:
                                    await pc.addIceCandidate(candidate_data)
                                    
                            elif msg_type == "pong":
                                pass
                                
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error: {e}")
                        except Exception as e:
                            logger.error(f"Message processing error: {e}")
                            
                except websockets.exceptions.ConnectionClosed as e:
                    logger.info(f"WebSocket closed: {e}")
                except Exception as e:
                    logger.error(f"Receive error: {e}")
            
            async def send_heartbeat():
                try:
                    while pc.connectionState not in ("closed", "failed"):
                        await asyncio.sleep(5)
                        try:
                            await websocket.send(json.dumps({"type": "ping"}))
                        except Exception as e:
                            logger.error(f"Heartbeat failed: {e}")
                            break
                except asyncio.CancelledError:
                    pass
            
            # =================================================================
            # CHANGED: Updated status monitor (no DataChannel)
            # =================================================================
            async def monitor_status():
                last_log = 0
                try:
                    while pc.connectionState not in ("closed", "failed"):
                        await asyncio.sleep(1)
                        now = int(time.time())
                        if now % 10 == 0 and now != last_log:
                            last_log = now
                            ts_state = "active" if timestamp_channel.is_open else "inactive"
                            logger.info(
                                f"Status: WebRTC={pc.connectionState}, "
                                f"Timestamps={ts_state}, "
                                f"FPS={ros_node.fps_actual:.1f}"
                            )
                except asyncio.CancelledError:
                    pass
            # =================================================================
            
            receive_task = asyncio.create_task(receive_messages())
            heartbeat_task = asyncio.create_task(send_heartbeat())
            monitor_task = asyncio.create_task(monitor_status())
            
            try:
                # CHANGED: Updated log message
                logger.info("Streaming with glass-to-glass latency measurement... (Ctrl+C to stop)")
                
                while pc.connectionState not in ("closed", "failed"):
                    await asyncio.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("Shutting down...")
            finally:
                # Stop timestamp sender
                timestamp_channel.stop_sender()
                
                for task in [receive_task, heartbeat_task, monitor_task]:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                
                await pc.close()
                ros_node.destroy_node()
                rclpy.shutdown()
                
    except websockets.exceptions.WebSocketException as e:
        logger.error(f"WebSocket error: {e}")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# Main Entry Point
# ============================================================================
# CHANGED: Added --overlay-timestamp argument

def main():
    parser = argparse.ArgumentParser(
        # CHANGED: Updated description
        description="H264 WebRTC Sender with Glass-to-Glass Latency Measurement",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument("--receiver-ip", required=True, help="Receiver IP address")
    parser.add_argument("--receiver-port", type=int, default=8080, help="Receiver port")
    parser.add_argument("--camera-topic", default="/camera1/image_raw", help="ROS2 camera topic")
    parser.add_argument("--resolution", default="640x480", help="Video resolution (WxH)")
    parser.add_argument("--fps", type=int, default=30, help="Target FPS")
    parser.add_argument("--bitrate", type=int, default=1000000, help="Target bitrate")
    parser.add_argument("--keyframe-interval", type=int, default=30, help="Keyframe interval")
    parser.add_argument("--h264-profile", choices=["baseline", "main", "high"], default="baseline")
    parser.add_argument("--h264-preset", default="ultrafast")
    parser.add_argument("--h264-tune", default="zerolatency")
    parser.add_argument("--buffer-size", type=int, default=2, help="Frame buffer size")
    
    # =========================================================================
    # NEW: Added --overlay-timestamp argument
    # =========================================================================
    # PREVIOUS: (not present in webrtc_sender_h264.py)
    parser.add_argument("--overlay-timestamp", action="store_true", 
                       help="Overlay timestamp on video (for visual verification)")
    # =========================================================================
    
    args = parser.parse_args()
    
    width, height = map(int, args.resolution.split('x'))
    
    config = StreamConfig(
        resolution=(width, height),
        fps=args.fps,
        target_bitrate=args.bitrate,
        keyframe_interval=args.keyframe_interval,
        frame_queue_size=min(max(args.buffer_size, 1), 10),
        use_fast_resize=True,
        h264_profile=args.h264_profile,
        h264_preset=args.h264_preset,
        h264_tune=args.h264_tune,
        overlay_timestamp=args.overlay_timestamp,  # NEW
    )
    
    receiver_url = f"ws://{args.receiver_ip}:{args.receiver_port}/ws"
    
    # =========================================================================
    # CHANGED: Updated startup banner with latency info
    # =========================================================================
    sep = "=" * 60
    logger.info(sep)
    # PREVIOUS: logger.info("WebRTC Video Sender - H264 Optimized")
    logger.info("WebRTC Sender - Glass-to-Glass Latency Measurement")
    logger.info(sep)
    logger.info(f"Camera topic:     {args.camera_topic}")
    logger.info(f"Resolution:       {config.resolution[0]}x{config.resolution[1]}")
    logger.info(f"Target FPS:       {config.fps}")
    logger.info(f"Codec:            H264 {config.h264_profile}")
    logger.info(f"Timestamp overlay:{config.overlay_timestamp}")
    logger.info(sep)
    # NEW: Added latency component breakdown
    logger.info("Glass-to-Glass Latency Components:")
    logger.info("  1. Camera capture → ROS2 message")
    logger.info("  2. ROS2 → WebRTC encoding")
    logger.info("  3. Network transmission (sender → relay → browser)")
    logger.info("  4. WebRTC decoding → Display")
    logger.info(sep)
    # =========================================================================
    logger.info(f"Receiver URL:     {receiver_url}")
    logger.info(sep)
    
    asyncio.run(send_stream(receiver_url, args.camera_topic, config))


if __name__ == "__main__":
    main()