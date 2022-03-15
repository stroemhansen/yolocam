#!/bin/bash

#UPDATE Ubuntu
sudo apt install net-tools -y
sudo apt update
sudo apt upgrade -y

#INSTALL pip3:
sudo apt install python3-pip -y

#INSTALL opencv:
sudo apt install python3-opencv

#INSTALL OTHER MODULES:
pip3 install pykson --user
pip3 install websockets --user
pip3 install Pillow --user
pip3 install netaddr --user
pip3 install openpyxl --user

#INSTALL SAME MODULES TO RUN UNDER A DEMON:
sudo -H python3 -m pip install opencv-python
sudo -H python3 -m pip install pykson
sudo -H python3 -m pip install websockets
sudo -H python3 -m pip install Pillow
sudo -H python3 -m pip install netaddr
sudo -H python3 -m pip install openpyxl

#INSTALL V4L VIDEO UTILITY:
sudo apt install v4l-utils

#ADD DOCKER GROUP:
sudo groupadd docker
sudo usermod -aG docker $$USER

#TYPE SDK LICENSE KEY
# shellcheck disable=SC2162
# shellcheck disable=SC2034
read -e -p "Enter SDK TOKEN: " TOKEN
read -r -p "Enter SDK LICENSE key: " LIC
TXT="python3 sdk.py -t ${TOKEN} -l ${LIC}"
sudo rm sdk.dh
echo "$TXT" >sdk.sh
sudo chmod +x sdk.sh

#INSTALL SUPERVISOR:
sudo apt-get install -y supervisor
sudo service supervisor start

TXT="[program:yolomonitor]
command=/usr/bin/python3 /home/cam/yolocam.py -t ${TOKEN}
directory=/home/cam
autostart=true
autorestart=true
startretries=3
stderr_logfile=/var/log/yolomon.err.log
stdout_logfile=NONE
user=root"
echo "$TXT" >/etc/supervisor/conf.d/yolomon.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status
sudo supervisorctl stop yolomonitor

#SET STATIC IP ADDRESS:
TXT=$(ip route ls)
read -r -a ARR <<<"$TXT"
# shellcheck disable=SC2162
read -e -p "Enter network adapter: " -i "${ARR[4]}" ADAP
# shellcheck disable=SC2162
read -e -p "Enter IP address: " -i "192.168.0.151" IP
TXT="network:
    version: 2
    renderer: networkd
    ethernets:
        ${ADAP}:
            dhcp4: no
            dhcp6: no
            addresses: [${IP}/24]
            gateway4: 192.168.0.1
            nameservers:
                addresses: [8.8.8.8, 8.8.4.4]"
echo "$TXT" >/etc/netplan/00-installer-config.yaml
sudo netplan apply
echo "Reconnect ssh terminal to 192.168.0.151"
echo "and run ./sdk.sh using the [up] option"

