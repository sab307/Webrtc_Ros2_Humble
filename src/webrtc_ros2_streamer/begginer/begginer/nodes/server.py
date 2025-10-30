#!/usr/bin/env python3

import argparse
import asyncio
import fractions
import json
import logging
import os
import ssl
import threading
import time
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from av import VideoFrame
import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def imgmsg_to_cv2(img_msg):
    """
    Convert ROS Image message to OpenCV image without cv_bridge
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
    elif img_msg.encoding == 'mono16' or img_msg.encoding == '16UC1':
        dtype = np.uint16
        n_channels = 1
    elif img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
        dtype = np.uint8
        n_channels = 2  # YUYV = 2 bytes per pixel
    else:
        raise ValueError(f"Unsupported encoding: {img_msg.encoding}")
    
    # Reshape data into image
    img_buf = np.asarray(img_msg.data, dtype=dtype)
    
    if img_msg.encoding in ['yuyv', 'yuyv422', 'yuy2']:
        # YUYV422 has 2 bytes per pixel (Y, U, Y, V)
        cv_img = img_buf.reshape((img_msg.height, img_msg.width, 2))
        # Convert YUYV → BGR
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_YUV2BGR_YUY2)
    
    if n_channels == 1:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width)
    else:
        cv_img = img_buf.reshape(img_msg.height, img_msg.width, n_channels)
    
    # Convert RGB to BGR if needed (OpenCV uses BGR)
    if img_msg.encoding == 'rgb8':
        cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)
    
    return cv_img


class Intermediate(Node):
    """ROS2 Node that receives camera images and adapts them for WebRTC"""
    
    def __init__(self, mode='auto', quality='high'):
        super().__init__('webrtc_intermediate_node')
        
        # Declare and get camera topic parameter
        self.declare_parameter('camera_topic', '/camera1/image_raw')
        camera_topic = self.get_parameter('camera_topic').value
        
        self.latest_frame = None
        self.mode = mode
        self.quality = quality
        self.fps = 30
        
        # Quality presets
        if quality == 'high':
            self.resolution = (1280, 720)  # 720p
            self.jpeg_quality = 95
        elif quality == 'medium':
            self.resolution = (640, 480)   # VGA
            self.jpeg_quality = 85
        else:  # low
            self.resolution = (320, 240)   # QVGA
            self.jpeg_quality = 75
            
        self.rtt = 0  # Round trip time
        self.frame_lock = threading.Lock()
        
        # Subscribe to camera topic
        self.subscription = self.create_subscription(
            Image,
            camera_topic,
            self.image_callback,
            10
        )
        
        self.get_logger().info(f'WebRTC Server subscribed to: {camera_topic}')
        self.get_logger().info(f'Mode: {mode}, Quality: {quality}, Resolution: {self.resolution}')
    
    def image_callback(self, msg):
        """Process incoming camera images"""
        try:
            # Log first frame
            if self.latest_frame is None:
                self.get_logger().info(f'First frame received! Size: {msg.width}x{msg.height}, Encoding: {msg.encoding}')
            
            # Convert ROS Image to OpenCV format
            cv_image = imgmsg_to_cv2(msg)
            
            # Adjust resolution based on mode
            if self.mode == 'auto':
                # Auto adjust based on network conditions (RTT)
                if self.rtt > 200:  # High latency
                    self.resolution = (480, 360)
                    self.jpeg_quality = 70
                elif self.rtt > 100:
                    self.resolution = (640, 480)
                    self.jpeg_quality = 80
                else:
                    # Use quality setting
                    if self.quality == 'high':
                        self.resolution = (1280, 720)
                        self.jpeg_quality = 95
                    else:
                        self.resolution = (640, 480)
                        self.jpeg_quality = 85
            
            # Resize image with high-quality interpolation
            resized = cv2.resize(cv_image, self.resolution, interpolation=cv2.INTER_LANCZOS4)
            
            # Store the latest frame with thread safety
            with self.frame_lock:
                self.latest_frame = resized
            
        except Exception as e:
            self.get_logger().error(f'Error in image callback: {str(e)}')
    
    def get_frame(self):
        """Return the latest frame (thread-safe)"""
        with self.frame_lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None
    
    def update_rtt(self, rtt_ms):
        """Update round trip time for adaptive streaming"""
        self.rtt = rtt_ms
        self.get_logger().debug(f'RTT updated: {rtt_ms}ms')
    
    def set_resolution(self, width, height):
        """Manually set resolution (for manual mode)"""
        self.resolution = (width, height)
        self.get_logger().info(f'Resolution set to: {width}x{height}')


class VideoImageTrack(MediaStreamTrack):
    """
    Video track that reads frames from ROS2 node
    """
    kind = "video"
    
    def __init__(self, ros_node):
        super().__init__()
        self.ros_node = ros_node
        self.frame_count = 0
        self._timestamp = 0
        self._start = None
        self._running = True
    
    async def recv(self):
        """Generate video frames from ROS images"""
        if not self._running:
            raise Exception("Track stopped")
            
        # Initialize timing
        if self._start is None:
            self._start = time.time()
        
        # Calculate timestamp
        now = time.time()
        pts = int((now - self._start) * 90000)  # 90kHz clock
        time_base = fractions.Fraction(1, 90000)
        
        # Get latest frame from ROS node
        frame = self.ros_node.get_frame()
        
        if frame is None:
            # Return blank frame if no image available
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            # Add "No Signal" text
            cv2.putText(frame, "Waiting for Camera...", (120, 240), 
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        # Convert OpenCV image (BGR) to VideoFrame (RGB)
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        video_frame = VideoFrame.from_ndarray(frame_rgb, format="rgb24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        
        self.frame_count += 1
        
        # Control frame rate (30 fps) - small sleep to prevent busy loop
        await asyncio.sleep(0.033)  # ~30fps
        
        return video_frame
    
    def stop(self):
        """Stop the track"""
        super().stop()
        self._running = False


# Global variables
pcs = set()
ros_node = None


async def index(request):
    """Serve the main HTML page"""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(html_path):
        # Return a simple HTML if file doesn't exist
        content = """
<!DOCTYPE html>
<html>
<head>
    <title>WebRTC Camera Stream</title>
    <style>
        body { font-family: Arial; text-align: center; padding: 20px; }
        video { max-width: 90%; border: 2px solid #333; }
        button { padding: 10px 20px; margin: 10px; font-size: 16px; }
    </style>
</head>
<body>
    <h1>ROS2 WebRTC Camera Stream</h1>
    <video id="video" autoplay playsinline></video>
    <br>
    <button id="start">Start Stream</button>
    <button id="stop">Stop Stream</button>
    <script src="client.js"></script>
</body>
</html>
        """
    else:
        content = open(html_path, "r").read()
    return web.Response(content_type="text/html", text=content)


async def javascript(request):
    """Serve the JavaScript client"""
    js_path = os.path.join(os.path.dirname(__file__), "client.js")
    if not os.path.exists(js_path):
        # Return a simple client.js if file doesn't exist
        content = """
let pc = null;
let dc = null;

document.getElementById('start').onclick = start;
document.getElementById('stop').onclick = stop;

async function start() {
    pc = new RTCPeerConnection({
        iceServers: [{urls: 'stun:stun.l.google.com:19302'}]
    });
    
    pc.ontrack = (event) => {
        document.getElementById('video').srcObject = event.streams[0];
    };
    
    dc = pc.createDataChannel('chat');
    dc.onopen = () => console.log('Data channel opened');
    dc.onmessage = (evt) => console.log('Message:', evt.data);
    
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    
    const response = await fetch('/offer', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type
        })
    });
    
    const answer = await response.json();
    await pc.setRemoteDescription(answer);
    
    console.log('Stream started');
}

async function stop() {
    if (dc) dc.close();
    if (pc) pc.close();
    pc = null;
    dc = null;
    document.getElementById('video').srcObject = null;
    console.log('Stream stopped');
}
        """
    else:
        content = open(js_path, "r").read()
    return web.Response(content_type="application/javascript", text=content)


async def offer(request):
    """Handle WebRTC offer from client"""
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    # Configure RTCPeerConnection with proper ICE settings
    from aiortc import RTCConfiguration, RTCIceServer
    
    config = RTCConfiguration(
        iceServers=[
            RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
            RTCIceServer(urls=["stun:stun1.l.google.com:19302"])
        ]
    )
    
    # Force IPv4 by filtering SDP
    sdp_lines = offer.sdp.split('\r\n')
    filtered_sdp = []
    for line in sdp_lines:
        # Skip .local candidates that can't be resolved
        if '.local' not in line:
            filtered_sdp.append(line)
    
    modified_offer = RTCSessionDescription(
        sdp='\r\n'.join(filtered_sdp),
        type=offer.type
    )
    
    pc = RTCPeerConnection(configuration=config)
    pcs.add(pc)
    
    @pc.on("datachannel")
    def on_datachannel(channel):
        @channel.on("message")
        def on_message(message):
            # Handle messages from client (e.g., RTT updates, ping)
            try:
                data = json.loads(message)
                if "rtt" in data:
                    ros_node.update_rtt(data["rtt"])
                elif "resolution" in data and ros_node.mode == "manual":
                    width, height = data["resolution"]
                    ros_node.set_resolution(width, height)
                elif data.get("type") == "ping":
                    # Respond to ping with pong
                    pong = json.dumps({
                        "type": "pong",
                        "timestamp": data["timestamp"]
                    })
                    channel.send(pong)
            except Exception as e:
                logger.error(f"Error processing datachannel message: {e}")
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info(f"Connection state: {pc.connectionState}")
        if pc.connectionState == "failed":
            logger.error("Connection failed!")
            await pc.close()
            pcs.discard(pc)
        elif pc.connectionState == "closed":
            logger.info("Connection closed by peer")
            pcs.discard(pc)
        elif pc.connectionState == "connected":
            logger.info("✓ Connection established and streaming!")
    
    # Add video track
    video_track = VideoImageTrack(ros_node)
    pc.addTrack(video_track)
    
    # Monitor track state
    @video_track.on("ended")
    def on_track_ended():
        logger.warning("Video track ended!")
    
    await pc.setRemoteDescription(modified_offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type
        })
    )


async def on_shutdown(app):
    """Close all peer connections on shutdown"""
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


def run_ros_node(node):
    """Run ROS2 node in separate thread"""
    rclpy.spin(node)


def main():
    global ros_node
    
    parser = argparse.ArgumentParser(description="WebRTC Camera Streaming Server")
    parser.add_argument("--cert-file", help="SSL certificate file (for HTTPS)")
    parser.add_argument("--key-file", help="SSL key file (for HTTPS)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8081, help="Port to bind to")
    parser.add_argument("--mode", default="auto", choices=["auto", "manual"],
                       help="Streaming mode: auto (adaptive) or manual")
    parser.add_argument("--camera-topic", default="/camera/image_raw",
                       help="ROS2 camera topic to subscribe to")
    args = parser.parse_args()
    
    # Initialize ROS2
    rclpy.init()
    ros_node = Intermediate(mode=args.mode)
    
    # Set camera topic parameter
    ros_node.set_parameters([
        rclpy.parameter.Parameter('camera_topic', 
                                 rclpy.Parameter.Type.STRING, 
                                 args.camera_topic)
    ])
    
    # Run ROS2 node in separate thread
    ros_thread = threading.Thread(target=run_ros_node, args=(ros_node,), daemon=True)
    ros_thread.start()
    
    # Give ROS node time to initialize
    time.sleep(1)
    
    logger.info("ROS2 node started, waiting for camera data...")
    
    # Setup web application
    app = web.Application()
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", javascript)
    app.router.add_post("/offer", offer)
    
    # SSL context for HTTPS (optional)
    ssl_context = None
    if args.cert_file and args.key_file:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(args.cert_file, args.key_file)
    
    logger.info(f"Starting server on {args.host}:{args.port}")
    logger.info(f"Camera topic: {args.camera_topic}")
    logger.info(f"Access at: http://{args.host}:{args.port}")
    logger.info("Make sure your camera is publishing to the topic!")
    
    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_context)
    finally:
        ros_node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()