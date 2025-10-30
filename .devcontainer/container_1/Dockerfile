FROM ros:humble-ros-base
ARG USERNAME=root
ARG USER_UID=1000
ARG USER_GID=$USER_UID

# Delete user if it exists in container (e.g Ubuntu Noble: ubuntu)
RUN if id -u $USER_UID ; then userdel `id -un $USER_UID` ; fi

# --- Define Environment Variables--- #
ENV ROS_DISTRO=humble

# Create the user
#RUN groupadd --gid $USER_GID $USERNAME 
#    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME \
    #
    # [Optional] Add sudo support. Omit if you don't need to install software after connecting.
RUN apt-get update \
    && apt-get install -y sudo \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME
RUN apt-get update && apt-get upgrade -y
RUN apt-get install -y python3-pip
RUN apt-get install ros-$ROS_DISTRO-rviz2 -y
ENV SHELL /bin/bash

# --- Install basic tools --- #
RUN  apt update -q && apt install -y \
        software-properties-common \
        gnupg2 \
        curl \
        wget \
        vim \
        git \
        byobu \
        net-tools\
        ca-certificates \
        apt-transport-https \
        build-essential \
        locales \
        lsb-release


# --- Install ROS packages for tutorials (Optional)--- #
RUN apt-get update && \
    apt-get install -y  x11-apps \
    #ros-${ROS_DISTRO}-gazebo-* \
    ros-${ROS_DISTRO}-cartographer \
    ros-${ROS_DISTRO}-dynamixel-sdk \
    ros-${ROS_DISTRO}-turtlebot3* \
    ros-${ROS_DISTRO}-slam-toolbox \
    ros-${ROS_DISTRO}-urdf-launch \
    ros-${ROS_DISTRO}-urdf-tutorial \
    ros-${ROS_DISTRO}-turtle-tf2-py \
    ros-${ROS_DISTRO}-tf2-tools \
    ros-${ROS_DISTRO}-tf-transformations

RUN apt-get update && apt-get upgrade -y
RUN apt-get install -y python3-pip
RUN pip3 install --break-system-packages aiortc aiohttp
RUN apt-get install ros-$ROS_DISTRO-rviz2 -y



# --- Install gazebo --- #
#RUN curl https://packages.osrfoundation.org/gazebo.gpg --output /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
#RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/gazebo-stable.list > /dev/null
#RUN apt-get update && \
#    apt-get install -y gz-ionic ros-${ROS_DISTRO}-ros-gz

# ********************************************************
# * Anything else you want to do like clean up goes here *
# ********************************************************

# [Optional] Set the default user. Omit if you want to keep the default as root.
#USER $USERNAME
RUN apt-get update && apt-get install -y bash
SHELL ["/bin/bash"]