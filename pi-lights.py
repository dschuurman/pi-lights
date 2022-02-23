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
import configparser
from datetime import date, datetime, timezone, timedelta
from astral.sun import sun
from astral.geocoder import lookup, database
from threading import Thread, Lock
from flask import Flask, render_template, request
from waitress import serve
import signal
import sched, time

# Constants
VERSION = 1.04
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
        logging.info(f'Devices: {bulbs},{outlets}')
       
        # Use a mutex for thread synchronization
        self.lock = Lock()

        # Turn off everything to start
        self.turn_off_bulbs()
        self.turn_off_outlets()

        # Initialize timer control of lights to be enabled (normally used for porch lights)
        self.light_timer = True

        # Initialize timer control of outlets to be disabled (normally used for vacation)
        self.outlet_timer = False

    def turn_on_bulbs(self):
        ''' Method to turn on all bulbs
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish(f'zigbee2mqtt/{bulb}/set/state', 'ON')
            if rc != 0:
                logging.error(f'MQTT publish return codes: {rc}')
        self.light_state = True
        self.lock.release()
        logging.debug('Lights turned on')

    def turn_off_bulbs(self):
        ''' Method to turn off all bulbs
        '''
        self.lock.acquire()
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish(f'zigbee2mqtt/{bulb}/set/state', 'OFF')
            if rc != 0:
                logging.error(f'MQTT publish return code: {rc}')       
        self.light_state = False
        self.lock.release()
        logging.debug('Lights turned off')

    def turn_on_outlets(self):
        ''' Method to turn on outlets
        '''
        self.lock.acquire()
        for outlet in self.outlets:
            (rc, msg_id) = self.client.publish(f'zigbee2mqtt/{outlet}/set/state', 'ON')
            if rc != 0:
                logging.error(f'MQTT publish return code: {rc}')
        self.outlet_state = True
        self.lock.release()
        logging.debug('Outlets turned on')

    def turn_off_outlets(self):
        ''' Method to turn off outlets
        '''
        self.lock.acquire()
        for outlet in self.outlets:
            (rc, msg_id) = self.client.publish(f'zigbee2mqtt/{outlet}/set/state', 'OFF')
            if rc != 0:
                logging.error(f'MQTT publish return code: {rc}')
        self.outlet_state = False
        self.lock.release()
        logging.debug('Outlets turned off')

    def set_brightness(self, value):
        ''' Method to set brightness of lights
        '''
        self.brightness = value
        for bulb in self.bulbs:
            (rc, msg_id) = self.client.publish(f'zigbee2mqtt/{bulb}/set/brightness', self.brightness)
            if rc != 0:
                logging.error(f'MQTT publish return codes: {rc}')
        logging.info(f'Brightness set to: {self.brightness}')

    def disconnect(self):
        ''' Graceful disconnect from MQTT broker
        '''
        self.client.disconnect()

class LightTimer:
    ''' Light_Timer class used to schedule and control lights
    '''
    def __init__(self, scheduler, state, city, lights_out_time):
        ''' Constructor 
        '''
        self.scheduler = scheduler
        self.state = state
        self.city = city
        self.lights_out_hour = lights_out_time.hour
        self.lights_out_minute = lights_out_time.minute

        # Get the lights on-time for today
        lights_on_time = self.get_next_dusk_time()
        today = datetime.now().date()
        lights_on_time = lights_on_time.replace(year=today.year, month=today.month, day=today.day)

        # Initialize lights and schedule events
        # If current time is between lights ON and OFF then set lights ON and schedule event for OFF time
        if lights_on_time <= datetime.now() < lights_out_time:
            self.lights_on()
        # Otherwise turn lights OFF and schedule event to turn lights ON at next dusk time
        else:
            self.lights_off()

    def lights_on(self):
        ''' turn lights on and schedule next event to turn lights off
        '''
        logging.info(f'*** Turning lights ON at {datetime.now().strftime("%m/%d/%Y %H:%M:%S")} ***')
        self.state.turn_on_bulbs()

        # If outlets are enabled then turn them on as well
        if self.state.outlet_timer:
            logging.info(f'*** Turning outlets ON at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")} ***')
            self.state.turn_on_outlets()

        # set next lights off time
        logging.info(f'Next event = Lights OFF at: {self.get_next_lights_out_time().strftime("%m/%d/%Y, %H:%M:%S")}')
        seconds = round((self.get_next_lights_out_time() - datetime.now()).total_seconds())
        self.scheduler.enter(seconds, 1, self.lights_off)

    def lights_off(self):
        ''' turn lights off and schedule next event to turn lights on
        '''
        logging.info(f'*** Turning lights OFF at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")} ***')
        self.state.turn_off_bulbs()

        # If outlets are enabled then turn them off as well
        if self.state.outlet_timer:
            logging.info(f'*** Turning outlets OFF at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")} ***')
            self.state.turn_off_outlets()       

        # set next lights on time
        dusk_time = self.get_next_dusk_time()
        logging.info(f'Next event = Lights ON at: {dusk_time.strftime("%m/%d/%Y, %H:%M:%S")} (dusk time)')
        seconds = round((dusk_time - datetime.now()).total_seconds())
        self.scheduler.enter(seconds, 1, self.lights_on)

    def set_lights_out_time(self, hour, minute):
        ''' Set lights out time
        '''
        # Update new lights out time
        self.lights_out_hour = hour
        self.lights_out_minute = minute
        logging.info(f'Lights out time changed to: {self.lights_out_hour}:{self.lights_out_minute:02}')

        # Retrieve current event in the queue and load new events
        event = self.scheduler.queue[0]
        # If lights should now be on: turn them on (and add next event to the queue)
        if datetime.now() < self.get_next_lights_out_time() < self.get_next_dusk_time():
            self.lights_on()
        else:   # Otherwise turn lights off (and add the next event to the queue)
            self.lights_off()
        self.scheduler.cancel(event)   # Purge old event from the queue

    def get_next_lights_out_time(self):
        ''' Get next lights out time
        '''
        lights_out_time = datetime.now().replace(hour=self.lights_out_hour, minute=self.lights_out_minute, second=0)
        # If lights out time has already passed for today, return lights out time for tomorrow
        if lights_out_time < datetime.now():
            lights_out_time += timedelta(days=1)
        return lights_out_time

    def get_next_dusk_time(self):
        ''' Determine next dusk time for local city
        '''
        try:
            city = lookup(self.city, database())
        except KeyError:         # Log error and return 5PM by default if city not found
            logging.error(f'Unrecognized city {self.city}, using default dusk time.')
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
    def __init__(self, port, state, light_timer, logfile):
        self.port = port
        self.state = state
        self.light_timer = light_timer
        self.logfile = logfile
        Thread.__init__(self)
        # Create a flask object and initialize web pages
        self.app = Flask(__name__)
        self.app.add_url_rule('/log', 'show_log', self.show_log)
        self.app.add_url_rule('/off-time', 'off-time', self.off_time, methods=['POST'])
        self.app.add_url_rule('/', 'index', self.index, methods=['GET', 'POST'])

    def run(self):
        # Start the waitress WSGI server on the specified port
        serve(self.app, host='0.0.0.0', port=self.port)

    # Methods for each flask webpage route
    def index(self):
        ''' Returns index.html webpage, methods=['GET', 'POST']
        '''
        on_time=self.light_timer.get_next_dusk_time().strftime("%H:%M")
        off_time=self.light_timer.get_next_lights_out_time().strftime("%H:%M")

        # Process POST actions if requested
        if request.method == 'POST':
            # Get form post as a dictionary
            form_dict = request.form
            if form_dict.get('light_state', None) == 'on':
                # turn bulbs on
                self.state.turn_on_bulbs()
                logging.info(f'Bulb(s) turned on via web interface at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('light_state', None) == 'off':
                # turn bulbs off
                self.state.turn_off_bulbs()
                logging.info(f'Bulb(s) turned off via web interface at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('light_timer', None) == 'on':
                # Enable timer control of lights
                self.state.light_timer = True
                logging.info(f'Timer control of lights ENABLED at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('light_timer', None) == 'off':
                # Disable timer control of lights
                self.state.light_timer = False
                logging.info(f'Timer control of lights DISABLED at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('outlet_state', None) == 'on':
                # Turn outlet on
                self.state.turn_on_outlets()
                logging.info(f'Outlet(s) turned on via web interface at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('outlet_state', None) == 'off':
                # Turn outlet off
                self.state.turn_off_outlets()
                logging.info(f'Outlet(s) turned off via web interface at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('outlet_timer', None) == 'on':
                # Enable timer control of outlet
                self.state.outlet_timer = True
                logging.info(f'Timer control of outlet ENABLED at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('outlet_timer', None) == 'off':
                # Disable timer control of outlet
                self.state.outlet_timer = False
                logging.info(f'Timer control of outlet DISABLED at {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
            elif form_dict.get('brightness', None) != None:
                self.state.set_brightness(int(form_dict.get('brightness')))

            # Return success (201) and stay on the same page
            return render_template('index.html', on_time=on_time, off_time=off_time, lights=self.state.bulbs, outlets=self.state.outlets, light_state=self.state.light_state, light_timer=self.state.light_timer, outlet_state=self.state.outlet_state, outlet_timer=self.state.outlet_timer, brightness=str(self.state.brightness)), 200

        elif request.method == 'GET':
            # pass the output state to index.html to display current state on webpage
            return render_template('index.html', on_time=on_time, off_time=off_time, lights=self.state.bulbs, outlets=self.state.outlets, light_state=self.state.light_state, light_timer=self.state.light_timer, outlet_state=self.state.outlet_state, outlet_timer=self.state.outlet_timer, brightness=str(self.state.brightness))

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
            return render_template('off-time.html', off_time="Invalid time"), 200
        t = time.split(':')
        self.light_timer.set_lights_out_time(int(t[0]),int(t[1]))

        # Return a page showing new times and return success (201)
        return render_template('off-time.html', off_time=self.light_timer.get_next_lights_out_time().strftime("%H:%M")), 200

#### Function definitions ####

def sigint_handler(signum, frame):
    ''' SIGINT signal handler to quit gracefully
    '''
    logging.info(f'Program recevied SIGINT at: {datetime.now()}')
    logging.shutdown()
    os._exit(0)

# ------------- Main code -------------

# Read settings from configuration file (located in the same folder as the program)
conf = configparser.ConfigParser()
conf.read(os.path.join(os.path.abspath(os.path.dirname(__file__)),'pi-lights.conf'))

# Read and make a list of bulbs and outlets from config file
try:
    BULBS = conf.get('pi-lights', 'bulbs')
    if BULBS != None:
        BULBS = BULBS.split(',')
        for i in range(len(BULBS)):
            BULBS[i] = BULBS[i].strip()
    OUTLETS = conf.get('pi-lights', 'outlets')
    if OUTLETS != None:
        OUTLETS = OUTLETS.split(',')
        for i in range(len(OUTLETS)):
            OUTLETS[i] = OUTLETS[i].strip()
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
logging.info(f'Starting at: {datetime.now().strftime("%m/%d/%Y, %H:%M:%S")}')
logging.info(f'Software version: {VERSION}')

# Check configuration settings
if not (0 <= BRIGHTNESS <=254):
    logging.error(f'Invalid brightness setting in configuration file: {BRIGHTNESS}')
else:
    logging.info(f'Brightness settting: {BRIGHTNESS}')

try:
    lookup(CITY, database())
except KeyError:
    logging.error(f'Unrecognized city in configuration file: {CITY}')

if not ((':' in OFF_TIME) and (4 <= len(OFF_TIME) <= 5) and (0 <= int(OFF_TIME.split(':')[0]) < 24) and (0 <= int(OFF_TIME.split(':')[1])<60)):
    logging.error(f'Invalid off_time in conf file {OFF_TIME} - using default off-time 23:00')
    OFF_TIME = "23:00"

# setup a SIGINT handler for graceful exit
signal.signal(signal.SIGINT, sigint_handler)

# Connect to MQTT broker and create object to control state of all lights and outlets
client = mqtt.Client()
ret = client.connect(BROKER_IP, BROKER_PORT, MQTT_KEEPALIVE)
if ret != 0:
    logging.error(f'MQTT connect return code: {ret}')
state = State(BULBS, OUTLETS, BRIGHTNESS, client)

# Set default lights off-time for today
lights_out_time = datetime.now().replace(hour=int(OFF_TIME.split(':')[0]), minute=int(OFF_TIME.split(':')[1]))
logging.info(f'Default lights OFF time set to: {lights_out_time.strftime("%H:%M")}')

# Create scheduler to control lights
# Set delayfunc to run with (at most) 1 second sleep so that it can periodically wake up to adjust 
# to any changes to the scheduler queue (which can occur in the flask thread)
scheduler = sched.scheduler(time.time, delayfunc=lambda time_to_sleep: time.sleep(min(1, time_to_sleep)))

# Create a light timer object
light_timer = LightTimer(scheduler, state, CITY, lights_out_time)

# If web interface is enabled, start the flask web server in a thread
if WEB_INTERFACE:
    logging.info('Web interface ENABLED')
    server = FlaskThread(PORT,state,light_timer,LOG_FILE)
    server.start()
else:
    logging.info('Web interface DISABLED')

client.loop_start()
scheduler.run()  
logging.info('Exiting...')
