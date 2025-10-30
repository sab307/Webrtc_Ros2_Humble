#!/usr/bin/env python3

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

class CameraSubscriber(Node):
    """
    ROS2 node that subscribes to a camera topic and republishes it
    for WebRTC streaming (without cv_bridge dependency)
    """
    def __init__(self):
        super().__init__('camera_subscriber')
        
        # Declare parameters
        self.declare_parameter('camera_topic', '/camera1/image_raw')
        self.declare_parameter('output_topic', '/image/webrtc')
        
        # Get parameters
        camera_topic = self.get_parameter('camera_topic').value
        output_topic = self.get_parameter('output_topic').value
        
        # Create subscriber to camera topic
        self.subscription = self.create_subscription(
            Image,
            camera_topic,
            self.image_callback,
            10
        )
        
        # Create publisher for WebRTC consumption
        self.publisher = self.create_publisher(
            Image,
            output_topic,
            10
        )
        
        self.get_logger().info(f'Subscribed to: {camera_topic}')
        self.get_logger().info(f'Publishing to: {output_topic}')
    
    def image_callback(self, msg):
        """
        Callback function that receives camera images and republishes them
        """
        try:
            # Simply republish the image message
            # No conversion needed - just pass through
            self.publisher.publish(msg)
            
        except Exception as e:
            self.get_logger().error(f'Error processing image: {str(e)}')

def main(args=None):
    rclpy.init(args=args)
    node = CameraSubscriber()
    
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()