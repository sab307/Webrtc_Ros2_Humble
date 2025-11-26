"""
Stream Manager - Orchestrates WebRTC streaming pipeline
"""

import asyncio
import json
import logging
import threading
import time
import rclpy
import websockets

from .ros_camera import CameraNode
from .webrtc_connection import create_peer_connection, setup_connection_handlers
from .websocket_handlers import (
    handle_incoming_messages,
    send_heartbeat,
    send_ice_candidate
)

logger = logging.getLogger(__name__)


async def stream_video(receiver_url, camera_topic, resolution, fps):
    """
    Main streaming pipeline - connects to receiver and streams video
    
    Args:
        receiver_url (str): WebSocket URL of receiver
        camera_topic (str): ROS2 camera topic to subscribe to
        resolution (tuple): Video resolution (width, height)
        fps (int): Target frames per second
    """
    # Initialize ROS2
    rclpy.init()
    ros_node = CameraNode(camera_topic, resolution)
    
    # Run ROS2 node in separate thread
    ros_thread = threading.Thread(target=lambda: rclpy.spin(ros_node), daemon=True)
    ros_thread.start()
    
    logger.info("Waiting for camera frames...")
    await asyncio.sleep(2)
    
    # Create WebRTC peer connection
    pc = create_peer_connection(ros_node, fps)
    setup_connection_handlers(pc)
    
    # Store websocket reference for ICE candidate handler
    websocket_connection = None
    
    @pc.on("icecandidate")
    async def on_ice_candidate(candidate):
        """Send ICE candidates to server via WebSocket"""
        await send_ice_candidate(websocket_connection, candidate)
    
    logger.info(f"Connecting to receiver at {receiver_url}...")
    
    try:
        async with websockets.connect(
            receiver_url,
            ping_interval=20,
            ping_timeout=60
        ) as websocket:
            websocket_connection = websocket
            logger.info("WebSocket connection established")
            
            # Create and send offer
            await send_offer(websocket, pc)
            
            # Start background tasks
            receive_task = asyncio.create_task(
                handle_incoming_messages(websocket, pc)
            )
            heartbeat_task = asyncio.create_task(
                send_heartbeat(websocket, pc)
            )
            
            # Keep connection alive
            await monitor_connection(pc, ros_node, receive_task, heartbeat_task)
            
            # Cleanup
            await cleanup(pc, ros_node, receive_task, heartbeat_task)
    
    except websockets.exceptions.WebSocketException as e:
        logger.error(f"WebSocket connection failed: {e}")
    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()


async def send_offer(websocket, pc):
    """
    Create and send SDP offer to server
    
    Args:
        websocket: WebSocket connection
        pc: RTCPeerConnection instance
    """
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    
    logger.info("Created SDP offer")
    
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


async def monitor_connection(pc, ros_node, receive_task, heartbeat_task):
    """
    Monitor connection status and log periodic updates
    
    Args:
        pc: RTCPeerConnection instance
        ros_node: CameraNode instance
        receive_task: Asyncio task for receiving messages
        heartbeat_task: Asyncio task for sending heartbeats
    """
    try:
        logger.info("Streaming... (Press Ctrl+C to stop)")
        while pc.connectionState != "closed":
            await asyncio.sleep(1)
            
            # Log status every 10 seconds
            if int(time.time()) % 10 == 0:
                logger.info(
                    f"Status: WebRTC={pc.connectionState}, "
                    f"ICE={pc.iceConnectionState}, "
                    f"Frames={ros_node.frame_count}"
                )
    
    except KeyboardInterrupt:
        logger.info("Shutting down...")


async def cleanup(pc, ros_node, receive_task, heartbeat_task):
    """
    Clean up resources and tasks
    
    Args:
        pc: RTCPeerConnection instance
        ros_node: CameraNode instance
        receive_task: Asyncio task for receiving messages
        heartbeat_task: Asyncio task for sending heartbeats
    """
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
    
    logger.info("Cleanup complete")