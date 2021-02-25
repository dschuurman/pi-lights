# pi-lights

Automates home lighting using IKEA Trådfri intelligent lights which use the [Zigbee](https://en.wikipedia.org/wiki/Zigbee) Light Link 
wireless protocol. This code was written for a Raspberry Pi, but could be run on other POSIX compliant systems as well.

The code works using a daily schedule, using a timer signal to turn lights on at dusk (based on your location)
and turning them off at a preset time. The code currently supports the Trådfri gateway with one or more 
lights and a smart socket.

## Dependencies

This project uses Python 3 and relies heavily on the [pytradfri](https://github.com/home-assistant-libs/pytradfri) package. 
Other dependencies include astral, configparser, and flask.
These packages can all be installed from the command line as follows:
```
$ pip3 install pytradfri astral configparser flask
```

The program also requires `libcoap`, a library that provides support for the
[Constrained Application Protocol (CoAP)](http://coap.technology/).
For more information, see [https://github.com/obgm/libcoap](https://github.com/obgm/libcoap).

## Setup

To setup and configure the Tradfri gateway for Python, see the installation instructions here: 
[https://github.com/home-assistant-libs/pytradfri](https://github.com/home-assistant-libs/pytradfri).

Once the gateway is configured, place the program and the configuration file `pi-lights.conf`
in the same folder. Adjust the settings in `pi-lights.conf` to reflect your local settings.

The program can be launched at boot time, but should only be started *after* the network is up and running.
One way to esure this is to launch the program as a systemd service which is configured to wait for 
the network to come online.

## Imortant Security Notes

This program currently uses the [flask](https://palletsprojects.com/p/flask/) web framework to provide a 
convenient web interface for status and control. Note that this code uses Flask’s built-in development server which is 
[not designed to be particularly efficient, stable, or secure](https://flask.palletsprojects.com/en/master/server/).
If you do use it, your host *must be running on a secure local network* since the flask web pages are open, unencrypted, 
and not particularly secure. The web interface is convenient for initial testing and setup. However, due to concerns 
described above, the `pi-lights.conf` file includes an option to easily enable or disable the web interface.
