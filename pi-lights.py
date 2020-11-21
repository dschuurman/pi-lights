# Home Automation script for Tradfri intelligent lights and socket to run on Raspberry Pi
# (C) 2020 Derek Schuurman
# License: GNU General Public License (GPL) v3
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import sys
import logging
from time import time, sleep
import configparser
from datetime import date, datetime, timezone, timedelta
from astral.sun import sun
from astral.geocoder import lookup, database
from pytradfri import Gateway
from pytradfri.api.libcoap_api import APIFactory
from pytradfri.error import PytradfriError
from threading import Thread
from flask import Flask, render_template, request
import signal
import sys
import os

# Constants
VERSION = 0.1
ON = True
OFF = False
SECONDS_PER_MINUTE = 60.0
MESSAGE_DELAY = 0.25             # time delay between messages sent to the gateway

#### Class definitions ####

class State:
    ''' class to store device state for lights and an outlet
    '''
    def __init__(self):
        ''' Constructor: connect to Tradfri gateway and devices
        '''
        self.api_factory = APIFactory(host=GATEWAY_IP, psk=SECURITY_KEY, psk_id=SECURITY_ID)
        self.api = self.api_factory.request
        gateway = Gateway()
        devices_command = gateway.get_devices()
        devices_commands = self.api(devices_command)
        devices = self.api(devices_commands)

        # Discover devices
        # Assume multiple smart bulbs and one smart outlet
        self.bulbs = []
        logging.info('Devices found: {}'.format(devices))
        for dev in devices:
            if dev.has_light_control:
                self.bulbs.append(dev)
            elif dev.has_socket_control:
                self.outlet = dev
            else:
                self.switch = dev

        # Turn off everything to start
        self.turn_off_lights()
        self.turn_off_outlet()

        # Initialize outlet to be disabled
        self.outlet_enable = False
        self.outlet_enable_msg = 'OFF'

    def turn_on_lights(self):
        ''' Method to turn ON all bulb(s)
        '''
        for bulb in self.bulbs:
            self.api(bulb.light_control.set_dimmer(DIMMER_SETTING))
            sleep(MESSAGE_DELAY)
            self.api(bulb.light_control.set_state(True))
            sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.bulb_state = ON
        self.bulb_msg = 'ON'

    def turn_off_lights(self):
        ''' Method to turn OFF all bulb(s)
        '''
        for bulb in self.bulbs:
            self.api(bulb.light_control.set_state(False))
            sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.bulb_state = OFF
        self.bulb_msg = 'OFF'

    def turn_on_outlet(self):
        ''' Method to turn ON outlet
        '''
        self.api(self.outlet.socket_control.set_state(True))
        sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.outlet_state = ON
        self.outlet_msg = "ON"

    def turn_off_outlet(self):
        ''' Method to turn OFF outlet
        '''
        self.api(self.outlet.socket_control.set_state(False))
        sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.outlet_state = OFF
        self.outlet_msg = "OFF"

    def disconnect(self):
        ''' Graceful disconnect from gateway
        '''
        self.api_factory.shutdown()

class Timer:
    ''' Timer class used to control periodic actions with one tick per minute
    '''
    def __init__(self, state):
        ''' Constructor 
        '''
        self.state = state
        self.dusk_time = self.get_dusk_time()
        logging.info('Light on time (dusk): {}\n'.format(self.dusk_time.strftime("%H:%M")))

        # Initialize light on/off times
        self.lights_out_hour = DEFAULT_LIGHTS_OUT_HOUR
        self.lights_out_minute = DEFAULT_LIGHTS_OUT_MINUTE
        logging.info('Lights out time: {}:{:02}'.format(self.lights_out_hour, self.lights_out_minute))

    def handler(self, signum, frame):
        ''' Signal handler that runs every minute 
        '''
        current_time = datetime.now()
        #print('Signal handler called with signal', signum, ' at ', datetime.now())

        # Reset dusk time at the beginning of each new day
        if current_time.hour == 0 and current_time.minute == 0:
            logging.info('Reset status for a new day at: {}'.format(datetime.now()))
            # Set lights-out time for a new day
            logging.info('Lights out time: {}:{:02}'.format(self.lights_out_hour, self.lights_out_minute))
            # Determine dusk time for a new day
            self.dusk_time = self.get_dusk_time()
            logging.info('Dusk time: {}\n'.format(self.dusk_time.strftime("%H:%M")))

        # Check if it's time for light(s) to come on
        #if abs((current_time-self.dusk_time).total_seconds()) < 60:
        if current_time.hour == self.dusk_time.hour and current_time.minute == self.dusk_time.minute:
            logging.info('*** Turning lights ON for the evening at {} ***\n'.format(datetime.now()))
            self.state.turn_on_lights()

            # If outlet is enabled then turn it on as well
            if self.state.outlet_enable:
                logging.info('*** Turning outlet ON for the evening at {} ***\n'.format(datetime.now()))
                self.state.turn_on_outlet()

        # Check for lights out
        elif current_time.hour == self.lights_out_hour and current_time.minute == self.lights_out_minute:
            logging.info('*** Turning lights OFF at {} ***\n'.format(datetime.now()))
            self.state.turn_off_lights()

            # If outlet mode is enabled then turn it off as well
            if self.state.outlet_enable:
                logging.info('*** Turning outlet off at {} ***\n'.format(datetime.now()))
                self.state.turn_off_outlet()

    def get_dusk_time(self):
        ''' Determine dusk time today for local city using astral library
        '''
        try:
            city = lookup(CITY, database())
        except KeyError:         # Log error and return 5PM by default if city not found
            logging.error('Unrecognized city {}, using default dusk time.'.format(CITY))
            return datetime.today().replace(hour=17, minute=0)
        # Compute dusk time for today corresponding to a solar depression angle of 6 degrees
        s = sun(city.observer,tzinfo=city.timezone)
        dusk = s['dusk']
        dusk = dusk.replace(tzinfo=None)
        return dusk

class FlaskThread(Thread):
    ''' Class definition to run flask in a separate thread
    '''
    def __init__(self):
        global __name__
        Thread.__init__(self)
        # Create a flask object and initialize web pages
        self.app = Flask(__name__)
        self.app.add_url_rule('/log', 'show_log', self.show_log)
        self.app.add_url_rule('/off-time', 'off-time', self.off_time, methods=['POST'])
        self.app.add_url_rule('/', 'index', self.index, methods=['GET', 'POST'])

    def run(self):
        self.app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    # Methods for each flask webpage route
    def index(self):
        ''' Returns index.html webpage, methods=['GET', 'POST']
        '''
        global state
        global timer

        # Sync web messages with current state
        status_msg = 'Auto On-time (set daily to dusk time): %s<br>Auto Off-time: %d:%02d' % (
            timer.dusk_time.strftime("%H:%M"), timer.lights_out_hour, timer.lights_out_minute)

        # Process POST actions if requested
        if request.method == 'POST':
            # Get form post as a dictionary
            form_dict = request.form
            if form_dict.get('bulb', None) == 'on':
                # turn bulbs on
                state.turn_on_lights()
            elif form_dict.get('bulb', None) == 'off':
                # turn bulbs off
                state.turn_off_lights()
            elif form_dict.get('outlet', None) == 'on':
                # Turn outlet on
                state.turn_on_outlet()
            elif form_dict.get('outlet', None) == 'off':
                # Turn outlet off
                state.turn_off_outlet()
            elif form_dict.get('outlet_enable', None) == 'on':
                # Enable outlet
                logging.info('outlet ENABLED at {}'.format(datetime.now()))
                state.outlet_enable = True
                state.outlet_enable_msg = 'ON'
            elif form_dict.get('outlet_enable', None) == 'off':
                # Disable outlet
                logging.info('outlet DISABLED at {}'.format(datetime.now()))
                state.outlet_enable = False
                state.outlet_enable_msg = 'OFF'

            # Return success (201) and stay on the same page
            return render_template('index.html', status_msg=status_msg, outlet_msg=state.outlet_msg, bulb_msg=state.bulb_msg, outlet_enable_msg=state.outlet_enable_msg), 200

        elif request.method == 'GET':
            # pass the output state to index.html to display current state on webpage
            return render_template('index.html', status_msg=status_msg, outlet_msg=state.outlet_msg, bulb_msg=state.bulb_msg, outlet_enable_msg=state.outlet_enable_msg)

    def show_log(self):
        ''' Returns webpage /log
        '''
        f = open(LOG_FILE, 'r')
        log = f.read()
        f.close()
        log = log.replace('\n', '\n<br>')
        return render_template('log.html', log=log)

    def off_time(self):
        ''' Returns /off-time webpage, methods=['POST']
        '''
        global state
        global timer

        time = request.form['off_time']
        if time == '':
            logging.info('Invalid lights out time requested.')
            return render_template('off-time.html', status_msg="Invalid time"), 200
        time = time.split(':')
        timer.lights_out_hour = int(time[0])
        timer.lights_out_minute = int(time[1])
        status_msg = 'Automatic Off-time is now set to: {}:{:02}'.format(timer.lights_out_hour, timer.lights_out_minute)
        logging.info('Lights out time changed to: {}:{:02}'.format(timer.lights_out_hour, timer.lights_out_minute))

        # Return a page showing new times and return success (201)
        return render_template('off-time.html', status_msg=status_msg), 200

#### Function definitions ####

def sigint_handler(signum, frame):
    ''' SIGINT signal handler to quit gracefully
    '''
    # Cancel interval timer
    signal.setitimer(signal.ITIMER_REAL, 0, 0.0)
    logging.info('Program recevied SIGINT at: {}'.format(datetime.now()))
    logging.shutdown()
    os._exit(0)

# ------------- Main code -------------

# Read other constants from configuration file (located in the same folder as the program)
conf = configparser.ConfigParser()
conf.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'pi-lights.conf'))
GATEWAY_IP = conf.get('pi-lights', 'gateway_ip')
SECURITY_KEY = conf.get('pi-lights', 'security_key')
SECURITY_ID = conf.get('pi-lights', 'security_id')
PORT = conf.getint('pi-lights', 'port')
DIMMER_SETTING = conf.getint('pi-lights', 'dimmer_setting')      # Dimmer setting: out of 255
CITY = conf.get('pi-lights', 'city')
WEB_INTERFACE = conf.getboolean('pi-lights', 'web_interface')
LOG_FILE = conf.get('pi-lights', 'logfile')
OFF_TIME = conf.get('pi-lights', 'off_time')

# Start logging
logging.basicConfig(filename=LOG_FILE, level=logging.INFO, filemode='w')

# Log INFO messages or higher
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.info('Starting at {} with version'.format(datetime.now(),VERSION))
logging.info('Gateway address: {}'.format(GATEWAY_IP))

# Check configuration settings
if len(SECURITY_KEY) != 16:
    logging.error("Invalid security key length in configuration file.")
if not (1 <= DIMMER_SETTING <=255):
    logging.error("Invalid dimmer setting in configuration file: {}".format(DIMMER_SETTING))
else:
    logging.info('Dimmer settting: {}'.format(DIMMER_SETTING))
try:
    lookup(CITY, database())
except KeyError:
    logging.error('Unrecognized city in configuration file: {}'.format(CITY))
if not ((':' in OFF_TIME) and (4 <= len(OFF_TIME) <= 5) and (0 <= int(OFF_TIME.split(':')[0]) < 24) and (0 <= int(OFF_TIME.split(':')[1])<60)):
    logging.error('Invalid off_time in configuration file {} - using default off time 23:00'.format(OFF_TIME))
    OFF_TIME = '23:00'

# Set default timer off-time from configuration file
DEFAULT_LIGHTS_OUT_HOUR = int(OFF_TIME.split(':')[0])
DEFAULT_LIGHTS_OUT_MINUTE = int(OFF_TIME.split(':')[1])
logging.info('Timer off-time set to {}:{:02}'.format(DEFAULT_LIGHTS_OUT_HOUR,DEFAULT_LIGHTS_OUT_MINUTE))

# Create an object to track and control state of all lights and outlet
state = State()

# Create and setup an interval timer object
timer = Timer(state)

# Start flask web server in a thread only if enabled in config file
if WEB_INTERFACE:
    logging.info('Web interface ENABLED')
    server = FlaskThread()
    server.start()
else:
    logging.info('Web interface DISABLED')

# Since this is not a real-time operating system, adjust the ticks
# so that they occur in the middle of each minute (when seconds=30)
# to prevent potential issues with jitter near the boundary of a change in the minutes
current_time = datetime.now()
current_time = current_time.replace(second=30, microsecond=0)
start_time = current_time + timedelta(seconds=SECONDS_PER_MINUTE)

# Setup signal to call handler every minute on the 30 seconds
signal.signal(signal.SIGALRM, timer.handler)
signal.setitimer(signal.ITIMER_REAL, start_time.timestamp()-time(), SECONDS_PER_MINUTE)
logging.info('Timer handler established with first tick set for {}'.format(start_time))

# setup a sigint handler
signal.signal(signal.SIGINT, sigint_handler)

# Continuously loop blocking on timer signal
while True:
    signal.pause()      # block until periodic timer fires, then repeat
