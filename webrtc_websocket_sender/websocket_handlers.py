"""
WebSocket message handlers for signaling
"""

import asyncio
import json
import logging
from aiortc import RTCSessionDescription

logger = logging.getLogger(__name__)


async def handle_incoming_messages(websocket, pc):
    """
    Process incoming messages from Go server
    
    Args:
        websocket: WebSocket connection
        pc: RTCPeerConnection instance
    """
    try:
        async for message in websocket:
            try:
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
                    logger.debug("Pong received from server")
            
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON message: {e}")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                import traceback
                traceback.print_exc()
    
    except Exception as e:
        logger.info(f"WebSocket closed or error: {e}")


async def send_heartbeat(websocket, pc):
    """
    Send periodic ping messages to keep WebSocket alive
    
    Args:
        websocket: WebSocket connection
        pc: RTCPeerConnection instance
    """
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


async def send_ice_candidate(websocket, candidate):
    """
    Send ICE candidate to server via WebSocket
    
    Args:
        websocket: WebSocket connection
        candidate: ICE candidate to send
    """
    if candidate and websocket:
        logger.info("Sending ICE candidate to server")
        try:
            await websocket.send(json.dumps({
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