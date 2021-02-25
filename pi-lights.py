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
from threading import Thread, Lock
from flask import Flask, render_template, request
import signal
import sys
import os

# Constants
VERSION = 0.24
ON = True
OFF = False
SECONDS_PER_MINUTE = 60.0
MESSAGE_DELAY = 0.25             # time delay between messages sent to the gateway

#### Class definitions ####

class State:
    ''' class to store device state for lights and an outlet
    '''
    def __init__(self, dimmer_setting):
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

        # Initialize dimmer setting
        self.dimmer_setting = dimmer_setting

        # Use a mutex for thread synchronization
        self.lock = Lock()

        # Turn off everything to start
        self.turn_off_lights()
        self.turn_off_outlet()

        # Initialize outlet to be disabled
        self.outlet_enable = False
        self.outlet_enable_msg = 'OFF'

    def turn_on_lights(self):
        ''' Method to turn on all bulb(s)
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            self.api(bulb.light_control.set_dimmer(self.dimmer_setting))
            sleep(MESSAGE_DELAY)
            self.api(bulb.light_control.set_state(True))
            sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.bulb_state = ON
        self.bulb_msg = 'ON'
        self.lock.release()
        logging.debug('Lights turned on')

    def turn_off_lights(self):
        ''' Method to turn off all bulb(s)
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            self.api(bulb.light_control.set_state(False))
            sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.bulb_state = OFF
        self.bulb_msg = 'OFF'
        self.lock.release()
        logging.debug('Lights turned off')

    def turn_on_outlet(self):
        ''' Method to turn on outlet
        '''
        self.lock.acquire()
        self.api(self.outlet.socket_control.set_state(True))
        sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.outlet_state = ON
        self.outlet_msg = "ON"
        self.lock.release()
        logging.debug('Outlet turned on')

    def turn_off_outlet(self):
        ''' Method to turn off outlet
        '''
        self.lock.acquire()
        self.api(self.outlet.socket_control.set_state(False))
        sleep(MESSAGE_DELAY)            # Add a delay for transmission time
        self.outlet_state = OFF
        self.outlet_msg = "OFF"
        self.lock.release()
        logging.debug('Outlet turned off')

    def disconnect(self):
        ''' Graceful disconnect from gateway
        '''
        self.api_factory.shutdown()

class Timer:
    ''' Timer class used to control periodic actions with one tick per minute
    '''
    def __init__(self, state, city, lights_out_time):
        ''' Constructor 
        '''
        self.state = state
        self.city = city
        self.lights_out_hour = lights_out_time.hour
        self.lights_out_minute = lights_out_time.minute
    
    def lights_on(self, signum, frame):
        ''' Signal handler that turns lights on
        '''
        logging.info('*** Turning lights ON at {} ***'.format(datetime.now().strftime("%m/%d/%Y %H:%M")))
        self.state.turn_on_lights()

        # If outlet is enabled then turn it on as well
        if self.state.outlet_enable:
            logging.info('*** Turning outlet ON at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            self.state.turn_on_outlet()

        # set next lights off time
        signal.signal(signal.SIGALRM, self.lights_off)
        logging.info('Next event = Lights OFF at: {}'.format(self.get_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))
        seconds = round((get_lights_out_time() - datetime.now()).total_seconds())
        signal.alarm(seconds)

    def lights_off(self, signum, frame):
        ''' Signal handler that turns lights off
        '''
        logging.info('*** Turning lights OFF at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
        self.state.turn_off_lights()

        # If outlet mode is enabled then turn it off as well
        if self.state.outlet_enable:
            logging.info('*** Turning outlet OFF at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            self.state.turn_off_outlet()       

        # set next lights on time
        signal.signal(signal.SIGALRM, self.lights_on)
        dusk_time = self.get_dusk_time()
        logging.info('Next event = Lights ON at: {} (dusk time)'.format(dusk_time.strftime("%m/%d/%Y, %H:%M:%S")))
        seconds = round((dusk_time - datetime.now()).total_seconds())
        signal.alarm(seconds)

    def set_lights_out_time(self, hour, minute):
        ''' Set lights out time
        '''
        # Update new lights out time
        self.lights_out_hour = hour
        self.lights_out_minute = minute
        logging.info('Lights out time changed to: {}:{:02}'.format(self.lights_out_hour, self.lights_out_minute))

        # If alarm signal is currently waiting to turn off lights, make adjustments
        if signal.getsignal(signal.SIGALRM) == self.lights_off:
            # if new off time will not come around until after next on time, just turn off lights now
            # (ie. off time was updated to a time earlier than now)
            if self.get_lights_out_time() > self.get_dusk_time():
                logging.info('New lights out time has passed... turning off lights now...')
                signal.alarm(1)
            else: # otherwise update signal to turn off lights at new time
                seconds = round((self.get_lights_out_time() - datetime.now()).total_seconds())
                signal.alarm(seconds)
                logging.info('Adjusting lights out time for today: {}'.format(self.get_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))

    def get_lights_out_time(self):
        ''' Get next lights out time
        '''
        lights_out_time = datetime.now().replace(hour=self.lights_out_hour, minute=self.lights_out_minute, second=0)
        # If lights out time has already passed for today, return lights out time for tomorrow
        if lights_out_time < datetime.now():
            lights_out_time += timedelta(days=1)
        return lights_out_time

    def get_dusk_time(self):
        ''' Determine next dusk time for local city using astral library
        '''
        try:
            city = lookup(self.city, database())
        except KeyError:         # Log error and return 5PM by default if city not found
            logging.error('Unrecognized city {}, using default dusk time.'.format(self.city))
            return datetime.today().replace(hour=17, minute=0)
        # Compute dusk time for today corresponding to a solar depression angle of 6 degrees
        s = sun(city.observer, tzinfo=city.timezone)
        dusk = s['dusk']
        dusk = dusk.replace(tzinfo=None)  # remove time zone to be compatible with datetime
        # If dusk time has already passed for today, return dusk time for tomorrow
        if dusk < datetime.now():
            dusk += timedelta(days=1)
        return dusk

class FlaskThread(Thread):
    ''' Class definition to run flask in a separate thread
    '''
    def __init__(self, port, state, timer, logfile):
        self.port = port
        self.state = state
        self.timer = timer
        self.logfile = logfile
        Thread.__init__(self)
        # Create a flask object and initialize web pages
        self.app = Flask(__name__)
        self.app.add_url_rule('/log', 'show_log', self.show_log)
        self.app.add_url_rule('/off-time', 'off-time', self.off_time, methods=['POST'])
        self.app.add_url_rule('/', 'index', self.index, methods=['GET', 'POST'])

    def run(self):
        self.app.run(host='0.0.0.0', port=self.port, debug=False, use_reloader=False)

    # Methods for each flask webpage route
    def index(self):
        ''' Returns index.html webpage, methods=['GET', 'POST']
        '''
        # Sync web messages with current state
        status_msg = 'Timer On-time (dusk time): {}<br>Auto Off-time: {}'.format(self.timer.get_dusk_time().strftime("%H:%M"), self.timer.get_lights_out_time().strftime("%H:%M"))

        # Process POST actions if requested
        if request.method == 'POST':
            # Get form post as a dictionary
            form_dict = request.form
            if form_dict.get('bulb', None) == 'on':
                # turn bulbs on
                self.state.turn_on_lights()
            elif form_dict.get('bulb', None) == 'off':
                # turn bulbs off
                self.state.turn_off_lights()
            elif form_dict.get('outlet', None) == 'on':
                # Turn outlet on
                self.state.turn_on_outlet()
            elif form_dict.get('outlet', None) == 'off':
                # Turn outlet off
                self.state.turn_off_outlet()
            elif form_dict.get('outlet_enable', None) == 'on':
                # Enable outlet
                logging.info('Timer control of outlet ENABLED at {}'.format(datetime.now()))
                self.state.outlet_enable = True
                self.state.outlet_enable_msg = 'ON'
            elif form_dict.get('outlet_enable', None) == 'off':
                # Disable outlet
                logging.info('Timer control of outlet DISABLED at {}'.format(datetime.now()))
                self.state.outlet_enable = False
                self.state.outlet_enable_msg = 'OFF'

            # Return success (201) and stay on the same page
            return render_template('index.html', status_msg=status_msg, outlet_msg=self.state.outlet_msg, bulb_msg=self.state.bulb_msg, outlet_enable_msg=self.state.outlet_enable_msg), 200

        elif request.method == 'GET':
            # pass the output state to index.html to display current state on webpage
            return render_template('index.html', status_msg=status_msg, outlet_msg=self.state.outlet_msg, bulb_msg=self.state.bulb_msg, outlet_enable_msg=self.state.outlet_enable_msg)

    def show_log(self):
        ''' Returns webpage /log
        '''
        f = open(self.logfile, 'r')
        log = f.read()
        f.close()
        log = log.replace('\n', '\n<br>')
        return render_template('log.html', log=log)

    def off_time(self):
        ''' Returns /off-time webpage, methods=['POST']
        '''
        time = request.form['off_time']
        if time == '':
            logging.error('Invalid lights out time requested.')
            return render_template('off-time.html', status_msg="Invalid time"), 200
        t = time.split(':')
        self.timer.set_lights_out_time(int(t[0]),int(t[1]))
        status_msg = 'Timer lights off-time is now set to: {}'.format(time)

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

# Configuration settings that are required
try:
    GATEWAY_IP = conf.get('pi-lights', 'gateway_ip')
    SECURITY_KEY = conf.get('pi-lights', 'security_key')
    SECURITY_ID = conf.get('pi-lights', 'security_id')
except configparser.NoOptionError as e:
    print('Missing parameter in configuration file: {}'.format(e))
    sys.exit(os.EX_CONFIG)
# Configuration settings with fallback values
PORT = conf.getint('pi-lights', 'port',fallback=8080)
DIMMER_SETTING = conf.getint('pi-lights', 'dimmer_setting',fallback=255)   # Dimmer setting out of 255
CITY = conf.get('pi-lights', 'city',fallback='Detroit')
WEB_INTERFACE = conf.getboolean('pi-lights', 'web_interface',fallback=False)
OFF_TIME = conf.get('pi-lights', 'off_time',fallback='23:00')
LOG_FILE = conf.get('pi-lights', 'logfile',fallback='/tmp/pi-lights.conf')
LOG_LEVEL = conf.get('pi-lights', 'loglevel',fallback='info')

# Start logging and set logging level; default to INFO level
if LOG_LEVEL == 'error':
    logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, filemode='w')
elif LOG_LEVEL == 'debug':
    logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG, filemode='w')
else:
    logging.basicConfig(filename=LOG_FILE, level=logging.INFO, filemode='w')

# Start log file
logging.info('Starting at: {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
logging.info('Software version: {}'.format(VERSION))
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
    OFF_TIME = "23:00"

# setup a sigint handler
signal.signal(signal.SIGINT, sigint_handler)

# Create an object to control state of all lights and outlet
state = State(DIMMER_SETTING)

# Set default lights off time for today
lights_out_time = datetime.now().replace(hour=int(OFF_TIME.split(':')[0]), minute=int(OFF_TIME.split(':')[1]))
logging.info('Default lights OFF time set to: {}'.format(lights_out_time.strftime("%H:%M")))

# Create a timer object
timer = Timer(state, CITY, lights_out_time)

# Get the lights on time for today
lights_on_time = timer.get_dusk_time()
today = datetime.now().date()
lights_on_time = lights_on_time.replace(year=today.year, month=today.month, day=today.day)
logging.info('Lights ON time for today: {}'.format(lights_on_time.strftime("%m/%d/%Y, %H:%M:%S")))

# If current time is between lights ON and OFF time then 
# turn lights ON and set SIGALARM to turn lights OFF
if lights_on_time <= datetime.now() < lights_out_time:
    state.turn_on_lights()
    signal.signal(signal.SIGALRM, timer.lights_off)
    seconds = round((timer.get_lights_out_time() - datetime.now()).total_seconds())
    signal.alarm(seconds)
    logging.info('Turning lights ON and initializing SIGALARM to turn lights OFF at: {}'.format(timer.get_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))
# Otherwise set SIGALARM to turn lights ON at next dusk time
else:
    signal.signal(signal.SIGALRM, timer.lights_on)
    seconds = round((timer.get_dusk_time() - datetime.now()).total_seconds())
    signal.alarm(seconds)
    logging.info('Initializing SIGALARM to turn lights ON at: {}'.format(timer.get_dusk_time().strftime("%m/%d/%Y, %H:%M:%S")))

# If enabled, start flask web server in a thread
if WEB_INTERFACE:
    logging.info('Web interface ENABLED')
    server = FlaskThread(PORT,state,timer,LOG_FILE)
    server.start()
else:
    logging.info('Web interface DISABLED')

# Continuously loop blocking on alarm signal
while True:
    signal.pause()      # block until signal fires, then repeat
