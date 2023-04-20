# pi-lights

>***NOTE***: To monitor sensors as well as control lights, check out [pi-home](https://github.com/dschuurman/pi-home)
which is an update and an extension of this project.

This project automates home lighting using using the [Zigbee](https://en.wikipedia.org/wiki/Zigbee) 
wireless protocol. The software automatically turns lights on at dusk and then turns them off at a preset time.
The motivation for the project was to illuminate porch lights in the evening
while ensuring they are not needlessly left on during the day. Furthermore, by including the option to
controlling a smart outlet, it provides the functionality of a traditional light timer while away on vacation.

This code was written for a Raspberry Pi using a Zigbee USB stick, but it could be run on other 
POSIX compliant systems using a compatible Zigbee adapter.


## Software Structure
The program parses a the `pi-lights.conf` file at start-up to set initial settings.
The software uses two threads: a main thread runs the control software and another
thread runs a flask web service on the local network.
The code uses a timer signal to turn Zigbee lights and outlets on at dusk 
(where dusk is determined by your location) and then turns them off at a preset time each day.
Light on and off events are implemented using a scheduler which stores events in a priority queue. 
The flask web interface provides a means for configuration and manually controlling the
lights and outlets using a web browser.

# Installation
This project was developed on a Raspberry Pi running 
[Raspberry Pi OS Lite](https://www.raspberrypi.org/software/operating-systems/)
(32-bit or 64-bit) and written in Python version 3. 
The code relies heavily on [Zigbee2MQTT](https://www.zigbee2mqtt.io/)
to bridge a network of Zigbee devices to MQTT (a common IoT networking protocol). 
Zigbee2MQTT supports various [Zigbee USB adapters](https://www.zigbee2mqtt.io/guide/adapters/) 
along with [numerous Zigbee devices](https://www.zigbee2mqtt.io/supported-devices/).

## Install Mosquitto
The first step is to install `mosquitto` which provides an open source MQTT broker.
This can be installed from the command-line as follows:
```
sudo apt install -y mosquitto mosquitto-clients
```
Since we will be connecting to the MQTT broker locally, we can edit the mosquitto 
congifuration file to explicitly listen *only* on the local loopback interface.
This can be done by adding the following lines in `/etc/mosquitto/conf.d/local.conf`:
```
listener 1883 127.0.0.1
allow_anonymous true
```
Next, enable the mosquitto service as follows:
```
sudo systemctl enable mosquitto.service
```
Ensure the `mosquitto` service is now running by typing:
```
sudo service mosquitto status
```

## Install Zigbee2MQTT
The next step is to install [Zigbee2MQTT](https://www.zigbee2mqtt.io/) on the Raspberry Pi. 
First, there are several dependencies that need to be installed from the command-line as follows:
```
sudo apt-get install -y npm git make g++ gcc
```
Unfortunately, the Raspberry Pi repos may have an older version of the nodejs package,
and Zigbee2MQTT requires a recent version of nodejs. You can add the repository and install a
recent version of nodejs as follows:
```
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install nodejs
```
Once the dependencies are installed, Zigbee2MQTT can be installed from github by typing the 
following commands:
```
sudo mkdir /opt/zigbee2mqtt
sudo chown -R ${USER}: /opt/zigbee2mqtt
git clone --depth 1 https://github.com/Koenkk/zigbee2mqtt.git /opt/zigbee2mqtt
cd /opt/zigbee2mqtt
npm ci
```
Note that the `npm ci` may produce some warnings which can be ignored.

Zigbee2MQTT requires a [YAML](https://en.wikipedia.org/wiki/YAML) configuration file which
may be edited by typing:
```
sudo nano /opt/zigbee2mqtt/data/configuration.yaml
``` 
Edit the configuration file so that it includes the following settings:
```
homeassistant: false
permit_join: true

# MQTT settings
mqtt:
  base_topic: zigbee2mqtt
  server: 'mqtt://127.0.0.1'

# Location of Zigbee USB adapter
serial:
  port: /dev/ttyACM0

# use a custom network key
advanced:
    network_key: GENERATE

# Start web frontend
frontend:
  port: 8081

# Enable over-the-air (OTA) updates for devices
ota:
    update_check_interval: 1440
    disable_automatic_update_check: false

```
Note that this configuration is for a Zigbee USB adapter which appears as `/dev/ttyACM0`. 
You can use the `dmesg` command to find the device file associated with
your particular Zigbee USB adapter and then update the configuration file accordingly.
Rather than hard-coding a unique network key, the `network_key` setting used above generates 
a new random key when Zigbee2MQTT is first run.

> ***Security Notes***
>
> It's recommended to disable `permit_join` after all the Zigbee devices
have been paired with your Zigbee adapter to prevent further devices
from attempting to join and possibly exposing the network key.
>
> Note that the `frontend` setting provides a web frontend for viewing the Zigbee
network running on the specified port. While this can be useful for
setup and debugging, you may wish to disable it later.
>
> It is recommended to enable over-the-air (OTA) updates for all devices to keep them up-to-date.

Once the setup and configuration are complete, ensure the Zigbee USB adapter
is inserted in the Raspberry Pi and start Zigbee2MQTT as follows:
```
cd /opt/zigbee2mqtt
npm start
```
This will build and launch `zigbee2mqtt` from the command-line. Once the
it builds and launches successfully, you can exit the program by hitting ctrl-c.
To launch automaticlaly on boot under Linux, 
[setup Zigbee2MQTT to run using systemctl](https://www.zigbee2mqtt.io/guide/installation/01_linux.html#starting-zigbee2mqtt).
For more detailed informatoin about installing Zigbee2MQTT, refer to the official
[Zigbee2MQTT installation instructions](https://www.zigbee2mqtt.io/guide/installation/01_linux.html#installing).

## Setup a Zigbee Network of Devices
Next, we need to establish a network of Zigbee devices by pairing each new device 
with the Zigbee hub on the Raspberry Pi. Zigbee2MQTT supports a plethora of Zigbee devices 
and a [friendly device webpage](https://www.zigbee2mqtt.io/supported-devices/)
includes notes on compatibility, pairing, and details on what values are exposed.

### Pairing Zigbee devices
Pairing can be easily accomplished using the web frontend to Zigbee2MQTT. 
The web frontend can be found by pointing a web browser to the IP address 
of the Raspberry Pi and the port number specified in the `configuration.yaml` file 
(port 8081 in the example file above). In the web frontend, click the `Devices` tab and 
then the button labelled `Permit join (All)`. Once this button is clicked a countdown will 
proceed during which time new devices can be paired to the Zigbee network 
(typically the countdown lasts for 255 seconds).

Typically a new device is paired by performing a factory reset of the device.
The way to perform a factory reset varies by device type and manufacturer. 
For example, Ikea Tradfri bulbs can be factory reset by toggling the power 6 times
and Ikea Tradfri outlets can be factory reset using a reset button in a small pinhole.
A few moments after reseting a device, the web frontend should report the pairing of the device. 
Clicking on the devices heading in the web frontend should display a list of paired devices 
along with each manufacturer, model, and IEEE address. The web frontend provides many nifty 
features like displaying a network map and the ability to perform updates on connected devices.

In addition to the IEEE address each Zigbee device may be configured with a "friendly name."
By default, the "friendly name" is initialized to the IEEE address, but
it is recommended that you assign a more meaningful "friendly name" using the web frontend. 
For example, a bulb could be named "bulb1" or "porch light".
This allows devices to be controlled and referenced using a *name* rather than
relying on a cumbersome IEEE address. Keep a list of the "friendly names" since
these will later need to be included in the pi-home configuration file.

### Binding Zigbee Devices
One helpful feature of Zigbee networks is the ability to *bind* devices. This feature allows
devices to directly control each other. For example, a switch (such as this 
[IKEA E1743](https://www.zigbee2mqtt.io/devices/E1743.html))
can bind to an outlet or bulb so that it can be controlled directly by the switch. 
This can be configured in the Zigbee2MQTT web frontend using the `bind` tab shown
in the device view. For example, to control a device like a bulb or an outlet with a switch, 
bind the switch to the corresponding device. Pi-Home can control lights and outlets
at preset times, but binding a switch enables the device to be manually controlled as well.

## Notes on Controlling Zigbee devices over MQTT
Once devices have been paired, they can be controlled simply by sending 
specially crafted MQTT messages. These messages must be published to the topic
`zigbee2mqtt/FRIENDLY_NAME/set` where `FRIENDLY_NAME` is the friendly name for a device. 
In the case of a bulb or smartplug, sending a message of "ON"
or "OFF" to the appropriate topic for the device will turn the device on or off.

MQTT messages can be sent from the command line on the Raspberry Pi using tools included with 
with the mosquitto package. For example, to turn on a light bulb with the friendly name of "bulb1" using the mostquitto client tool, type:
```
mosquitto_pub -h 127.0.0.1 -t zigbee2mqtt/bulb1/set -m "ON"
```
where `127.0.0.1` is the local loopback address to connect to the local MQTT broker and 
`zigbee2mqtt/bulb1/set` is the MQTT topic to control the settings for
the device with the friendly name `bulb1`. 
Consult the Zigbee2MQTT documentation for a [complete list of MQTT topics and
messages](https://www.zigbee2mqtt.io/guide/usage/mqtt_topics_and_messages.html).

## Setting up the Python control software
Once Zigbee2MQTT is installed and devices are successfully paired we can setup the
`pi-lights` control program. This program controls devices by sending MQTT messages
to the MQTT broker which are then bridged to the Zigbee network via Zigbee2MQTT.
The control program is written in Python 3 and uses the 
[paho-mqtt](https://www.eclipse.org/paho/index.php?page=clients/python/index.php) library to send
MQTT messages. The dependencies for `pi-lights` can all be installed from the command-line as follows:
```
$ pip3 install paho-mqtt astral configparser flask waitress
```
The `pi-lights` program and the `pi-lights.conf` configuration file should be placed in the same folder.
The `templates` folder should also be placed in this folder since it is required for the web interface.
By default, a log file named `pi-lights.log` will be written in the same folder where the program resides
(but this can be set elsewhere in the configuration file). The configuration file should be edited to 
reflect your local settings (in particular, set your city so that the dusk time can be properly computed).

The `pi-lights` program can be launched at boot time, but should be started only *after* the network is up and running.
One way to ensure this is to launch the program as a systemd service which is configured to wait for the network to come online. 
See the [example of of using systemd with Zigbee2MQTT](https://www.zigbee2mqtt.io/guide/installation/01_linux.html#optional-running-as-a-daemon-with-systemctl).

This program uses the [flask](https://palletsprojects.com/p/flask/) web framework to provide a 
convenient web interface for status and control. Flask’s built-in development WSGI server is 
[not designed to be particularly efficient, stable, or secure](https://flask.palletsprojects.com/en/master/server/)
so this project uses the [waitress](https://github.com/Pylons/waitress) WSGI instead.
The web server runs on port 8080 by default (hence the Zigbee2MQTT web front end is 
configured to run on port 8081 to avoid a port conflict).
The `pi-lights.conf` configuration file includes an option to easily enable or disable the web interface.
The web interface is convenient for testing and setup, but if it is enabled it should 
be run on a secure local network since the web pages are open and unencrypted.

## Security considerations
This server is intended to be run on a secure local network since the web pages to control the lights
are open and unencrypted. The logfile is also accessible via the web interface.

This program is provided "as is" without any warranty, expressed or implied, about merchantability or fitness for a particular purpose. *Your mileage may vary*.
