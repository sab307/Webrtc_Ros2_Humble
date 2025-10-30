# Webrtc_Ros2_Humble
A basic implementation of webrtc on ros2 humble camera topic.

## Sourcing
´´´
source /opt/ros/humble/setup.bash
sudo apt-get update
rosdep install -i -y --from-paths src --rosdistro humble
colcon build
source install/setup.bash
´´´

## The usb_cam package
The launch file camera.launch.py is important.
Besidess that params_1.yaml or params_2.yaml needs to be considered.
These params are responsible for the camera data format.
``` bash
ros2 launch usb_cam camera.launch.py
ros2 topic list #check whether camera topic /camera1/image_raw is there
``` 

## Webrtc_ros2_streamer package
``` bash
cd src/webrtc_ros2_streamer/begginer/begginer/nodes
chmod a+x server.py
python3 server.py #launches the webrtc streaming for /camera1/image_raw topic
```
For now, the processing power for this web streaming is too much 3.3GHz almost.
Trying to reduce that! Experimentation is on!

