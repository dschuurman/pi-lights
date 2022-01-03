# Home Automation script for Zigbee lights and sockets to run on Raspberry Pi
# (C) 2020 Derek Schuurman
# License: GNU General Public License (GPL) v3
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

import sys, os
import paho.mqtt.client as mqtt
import logging
import configparser, json
from datetime import date, datetime, timezone, timedelta
from astral.sun import sun
from astral.geocoder import lookup, database
from threading import Thread, Lock
from flask import Flask, render_template, request
import signal

# Constants
VERSION = 1.0
ON = True
OFF = False
MQTT_KEEPALIVE = 60

#### Class definitions ####

class State:
    ''' class to manage device state for lights and outlets
    '''
    def __init__(self, bulbs, outlets, brightness, client):
        ''' Constructor: connect to MQTT broker and initialize state variables
        '''
        self.client = client
 
        # Store bulbs, outlets, and brightness settings
        self.bulbs = bulbs
        self.outlets = outlets
        self.set_brightness(brightness)
        logging.info('Devices: {},{}'.format(bulbs, outlets))
       
        # Use a mutex for thread synchronization
        self.lock = Lock()

        # Turn off everything to start
        self.turn_off_lights()
        self.turn_off_outlets()

        # Initialize timer control of lights to be enabled (normally used for porch lights)
        self.lights_enable = True
        self.lights_enable_msg = 'ON'

        # Initialize timer control of outlets to be disabled (normally used for vacation)
        self.outlets_enable = False
        self.outlets_enable_msg = 'OFF'

    def turn_on_lights(self):
        ''' Method to turn on all bulbs
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish("zigbee2mqtt/{}/set/state".format(bulb), "ON")
            if rc != 0:
                logging.error('MQTT publish return codes: {},{}'.format(rc))
        self.bulb_state = ON
        self.lights_msg = 'ON'
        self.lock.release()
        logging.debug('Lights turned on')

    def turn_off_lights(self):
        ''' Method to turn off all bulbs
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish("zigbee2mqtt/{}/set/state".format(bulb), "OFF")
            if rc != 0:
                logging.error('MQTT publish return code: {}'.format(rc))       
        self.bulb_state = OFF
        self.lights_msg = 'OFF'
        self.lock.release()
        logging.debug('Lights turned off')

    def turn_on_outlets(self):
        ''' Method to turn on outlets
        '''
        self.lock.acquire()
        for outlet in self.outlets:
            (rc, msg_id) = self.client.publish("zigbee2mqtt/{}/set/state".format(outlet), "ON")
            if rc != 0:
                logging.error('MQTT publish return code: {}'.format(rc))
        self.outlets_state = ON
        self.outlets_msg = "ON"
        self.lock.release()
        logging.debug('Outlets turned on')

    def turn_off_outlets(self):
        ''' Method to turn off outlets
        '''
        self.lock.acquire()
        for outlet in self.outlets:
            (rc, msg_id) = self.client.publish("zigbee2mqtt/{}/set/state".format(outlet), "OFF")
            if rc != 0:
                logging.error('MQTT publish return code: {}'.format(rc))
        self.outlets_state = OFF
        self.outlets_msg = "OFF"
        self.lock.release()
        logging.debug('Outlets turned off')

    def set_brightness(self, value):
        ''' Method to set brightness of lights
        '''
        self.brightness = value
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish("zigbee2mqtt/{}/set/brightness".format(bulb), self.brightness)
            if rc != 0:
                logging.error('MQTT publish return codes: {},{}'.format(rc1,rc2))
        logging.info('Brightness set to: {}'.format(self.brightness))

    def disconnect(self):
        ''' Graceful disconnect from MQTT broker
        '''
        self.client.disconnect()

class Timer:
    ''' Timer class used to control periodic events
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
        logging.info('*** Turning lights ON at {} ***'.format(datetime.now().strftime("%m/%d/%Y %H:%M:%S")))
        self.state.turn_on_lights()

        # If outlets are enabled then turn them on as well
        if self.state.outlets_enable:
            logging.info('*** Turning outlets ON at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            self.state.turn_on_outlets()

        # set next lights off time
        signal.signal(signal.SIGALRM, self.lights_off)
        logging.info('Next event = Lights OFF at: {}'.format(self.get_next_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))
        seconds = round((self.get_next_lights_out_time() - datetime.now()).total_seconds())
        signal.alarm(seconds)

    def lights_off(self, signum, frame):
        ''' Signal handler that turns lights off
        '''
        logging.info('*** Turning lights OFF at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
        self.state.turn_off_lights()

        # If outlets are enabled then turn them off as well
        if self.state.outlets_enable:
            logging.info('*** Turning outlets OFF at {} ***'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            self.state.turn_off_outlets()       

        # set next lights on time
        signal.signal(signal.SIGALRM, self.lights_on)
        dusk_time = self.get_next_dusk_time()
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

        # The following logic determines the implications of the new off-time
        # for the current state of the light and updates the alarm time as needed

        # If alarm signal is waiting to turn off lights, then lights are currently on
        if signal.getsignal(signal.SIGALRM) == self.lights_off:
            # if new off-time is after the next on-time, then lights should go off now
            # (ie. off-time was updated to a time earlier than now)
            if self.get_next_lights_out_time() > self.get_next_dusk_time():
                logging.info('New lights out time has passed... turning off lights now...')
                signal.alarm(1)
            else:                   # otherwise update alarm time to turn off lights at new off-time
                seconds = round((self.get_next_lights_out_time() - datetime.now()).total_seconds())
                signal.alarm(seconds)
                logging.info('Adjusting lights out time for today: {}'.format(self.get_next_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))
        # otherwise lights are currently off so check current time relative to new off-time
        elif (datetime.now() < self.get_next_lights_out_time() < self.get_next_dusk_time()):
                logging.info('New light off time implies lights should be on now...')
                signal.alarm(1)     # If current time falls within new on-time, update alarm to turn lights on now

    def get_next_lights_out_time(self):
        ''' Get next lights out time
        '''
        lights_out_time = datetime.now().replace(hour=self.lights_out_hour, minute=self.lights_out_minute, second=0)
        # If lights out time has already passed for today, return lights out time for tomorrow
        if lights_out_time < datetime.now():
            lights_out_time += timedelta(days=1)
        return lights_out_time

    def get_next_dusk_time(self):
        ''' Determine next dusk time for local city using astral library
        '''
        try:
            city = lookup(self.city, database())
        except KeyError:         # Log error and return 5PM by default if city not found
            logging.error('Unrecognized city {}, using default dusk time.'.format(self.city))
            return datetime.today().replace(hour=17, minute=0)
        # Compute dusk time for today (corresponding to a solar depression angle of 6 degrees)
        s = sun(city.observer, tzinfo=city.timezone)
        dusk = s['dusk']
        dusk = dusk.replace(tzinfo=None)  # remove timezone to be compatible with datetime
        # If dusk time has already passed for today, return next dusk time for tomorrow
        if dusk < datetime.now():
            s = sun(city.observer, tzinfo=city.timezone, date=date.today()+timedelta(days=1))
            dusk = s['dusk']
            dusk = dusk.replace(tzinfo=None)
        return dusk

class FlaskThread(Thread):
    ''' Class definition to run flask
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
        timer_msg = 'Timer On-time (dusk time): {}<br>Auto Off-time: {}'.format(self.timer.get_next_dusk_time().strftime("%H:%M"), self.timer.get_next_lights_out_time().strftime("%H:%M"))

        # Process POST actions if requested
        if request.method == 'POST':
            # Get form post as a dictionary
            form_dict = request.form
            if form_dict.get('lights', None) == 'on':
                # turn bulbs on
                self.state.turn_on_lights()
                logging.info('Light(s) turned on via web interface at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('lights', None) == 'off':
                # turn bulbs off
                self.state.turn_off_lights()
                logging.info('Light(s) turned off via web interface at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('lights_enable', None) == 'on':
                # Enable timer control of lights
                self.state.lights_enable = True
                self.state.lights_enable_msg = 'ON'
                logging.info('Timer control of lights ENABLED at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('lights_enable', None) == 'off':
                # Disable timer control of lights
                self.state.lights_enable = False
                self.state.lights_enable_msg = 'OFF'
                logging.info('Timer control of lights DISABLED at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('outlets', None) == 'on':
                # Turn outlet on
                self.state.turn_on_outlets()
                logging.info('Outlet(s) turned on via web interface at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('outlets', None) == 'off':
                # Turn outlet off
                self.state.turn_off_outlets()
                logging.info('Outlet(s) turned off via web interface at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('outlets_enable', None) == 'on':
                # Enable timer control of outlet
                self.state.outlets_enable = True
                self.state.outlets_enable_msg = 'ON'
                logging.info('Timer control of outlet ENABLED at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('outlets_enable', None) == 'off':
                # Disable timer control of outlet
                self.state.outlets_enable = False
                self.state.outlets_enable_msg = 'OFF'
                logging.info('Timer control of outlet DISABLED at {}'.format(datetime.now().strftime("%m/%d/%Y, %H:%M:%S")))
            elif form_dict.get('brightness', None) != None:
                self.state.set_brightness(int(form_dict.get('brightness')))

            # Return success (201) and stay on the same page
            return render_template('index.html', timer_msg=timer_msg, lights_msg=self.state.lights_msg, lights_enable_msg=self.state.lights_enable_msg, outlets_msg=self.state.outlets_msg, outlets_enable_msg=self.state.outlets_enable_msg, brightness=str(self.state.brightness)), 200

        elif request.method == 'GET':
            # pass the output state to index.html to display current state on webpage
            return render_template('index.html', timer_msg=timer_msg, lights_msg=self.state.lights_msg, lights_enable_msg=self.state.lights_enable_msg, outlets_msg=self.state.outlets_msg, outlets_enable_msg=self.state.outlets_enable_msg, brightness=str(self.state.brightness))

    def show_log(self):
        ''' Returns webpage /log
        '''
        f = open(self.logfile, 'r')
        log = f.read()
        f.close()
        log = log.replace('\n', '\n<br>')
        return render_template('log.html', log=log)

    def off_time(self):
        ''' Returns /off-time webpage, method=['POST']
        '''
        time = request.form['off_time']
        if time == '':
            logging.error('Invalid lights out time requested.')
            return render_template('off-time.html', timer_msg="Invalid time"), 200
        t = time.split(':')
        self.timer.set_lights_out_time(int(t[0]),int(t[1]))
        timer_msg = 'Timer lights off-time is now set to: {}'.format(time)

        # Return a page showing new times and return success (201)
        return render_template('off-time.html', timer_msg=timer_msg), 200

#### Function definitions ####

def sigint_handler(signum, frame):
    ''' SIGINT signal handler to quit gracefully
    '''
    # Cancel alarm timer
    signal.alarm(0)
    logging.info('Program recevied SIGINT at: {}'.format(datetime.now()))
    logging.shutdown()
    os._exit(0)

# ------------- Main code -------------

# Read settings from configuration file (located in the same folder as the program)
conf = configparser.ConfigParser()
conf.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'pi-lights.conf'))

# Configuration settings that are required
try:
    BULBS = json.loads(conf.get('pi-lights', 'bulbs'))
    OUTLETS = json.loads(conf.get('pi-lights', 'outlets'))
except configparser.NoOptionError as e:
    print('Missing parameters in configuration file: {}'.format(e))
    sys.exit(os.EX_CONFIG)

# Configuration settings with fallback values
BROKER_IP = conf.get('pi-lights', 'broker_ip', fallback="127.0.0.1")
BROKER_PORT = conf.getint('pi-lights', 'broker_port', fallback=1883)
PORT = conf.getint('pi-lights', 'port',fallback=8080)
BRIGHTNESS = conf.getint('pi-lights', 'brightness',fallback=254)
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

# Check configuration settings
if not (0 <= BRIGHTNESS <=254):
    logging.error("Invalid brightness setting in configuration file: {}".format(BRIGHTNESS))
else:
    logging.info('Brightness settting: {}'.format(BRIGHTNESS))

try:
    lookup(CITY, database())
except KeyError:
    logging.error('Unrecognized city in configuration file: {}'.format(CITY))

if not ((':' in OFF_TIME) and (4 <= len(OFF_TIME) <= 5) and (0 <= int(OFF_TIME.split(':')[0]) < 24) and (0 <= int(OFF_TIME.split(':')[1])<60)):
    logging.error('Invalid off_time in conf file {} - using default off-time 23:00'.format(OFF_TIME))
    OFF_TIME = "23:00"

# setup a sigint handler
signal.signal(signal.SIGINT, sigint_handler)

# Connect to MQTT broker and create object to control state of all lights and outlets
client = mqtt.Client()
ret = client.connect(BROKER_IP, BROKER_PORT, MQTT_KEEPALIVE)
if ret != 0:
    logging.error('MQTT connect return code: {}'.format(ret))
state = State(BULBS, OUTLETS, BRIGHTNESS, client)

# Set default lights off-time for today
lights_out_time = datetime.now().replace(hour=int(OFF_TIME.split(':')[0]), minute=int(OFF_TIME.split(':')[1]))
logging.info('Default lights OFF time set to: {}'.format(lights_out_time.strftime("%H:%M")))

# Create a timer object
timer = Timer(state, CITY, lights_out_time)

# Get the lights on-time for today
lights_on_time = timer.get_next_dusk_time()
today = datetime.now().date()
lights_on_time = lights_on_time.replace(year=today.year, month=today.month, day=today.day)

# If current time is between lights ON and OFF time then 
# turn lights ON and set SIGALARM to turn lights OFF
if lights_on_time <= datetime.now() < lights_out_time:
    state.turn_on_lights()
    signal.signal(signal.SIGALRM, timer.lights_off)
    seconds = round((timer.get_next_lights_out_time() - datetime.now()).total_seconds())
    signal.alarm(seconds)
    logging.info('Turning lights ON and initializing SIGALARM to turn lights OFF at: {}'.format(timer.get_next_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")))
# Otherwise set SIGALARM to turn lights ON at next dusk time
else:
    signal.signal(signal.SIGALRM, timer.lights_on)
    seconds = round((timer.get_next_dusk_time() - datetime.now()).total_seconds())
    signal.alarm(seconds)
    logging.info('Initializing SIGALARM to turn lights ON at: {}'.format(timer.get_next_dusk_time().strftime("%m/%d/%Y, %H:%M:%S")))

# If web interface is enabled, start the flask web server in a thread
if WEB_INTERFACE:
    logging.info('Web interface ENABLED')
    server = FlaskThread(PORT,state,timer,LOG_FILE)
    server.start()
else:
    logging.info('Web interface DISABLED')

try:
    client.loop_forever()
except KeyboardInterrupt:
    client.disconnect()
    logging.info('Exiting')
