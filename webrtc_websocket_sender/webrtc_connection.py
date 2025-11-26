"""
WebRTC Peer Connection setup and configuration
"""

import logging
from aiortc import RTCPeerConnection
from aiortc.rtcrtpsender import RTCRtpSender

from .video_track import VideoStreamTrack

logger = logging.getLogger(__name__)


def create_peer_connection(ros_node, fps=30):
    """
    Create and configure WebRTC peer connection with VP8 codec
    
    Args:
        ros_node: CameraNode instance for video frames
        fps (int): Target frames per second
        
    Returns:
        RTCPeerConnection: Configured peer connection
    """
    pc = RTCPeerConnection()
    
    # Find VP8 codec
    codecs = RTCRtpSender.getCapabilities("video").codecs
    vp8_codec = None
    
    for codec in codecs:
        logger.debug(f"Available codec: {codec.mimeType}")
        if codec.mimeType == "video/VP8":
            vp8_codec = codec
            logger.info(f"Found VP8 codec: {codec}")
            break
    
    if not vp8_codec:
        logger.error("VP8 codec not available!")
        raise RuntimeError("VP8 codec not available")
    
    # Add video track with VP8 preference
    video_track = VideoStreamTrack(ros_node, fps)
    transceiver = pc.addTransceiver(video_track, direction="sendonly")
    transceiver.setCodecPreferences([vp8_codec])
    
    logger.info("Video track added with VP8 codec")
    
    return pc


def setup_connection_handlers(pc):
    """
    Setup event handlers for peer connection state changes
    
    Args:
        pc: RTCPeerConnection instance
    """
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