import time
import logging
import json
import requests,urllib
import re
import traceback
from datetime import datetime
import os
import sys
sys.path.append('/opt/autopi/salt/modules/')
# Structure and lots of initial work from @plord12 on GitHub:
# https://github.com/plord12/autopi-tools

log = logging.getLogger(__name__)
global_debug = False
first_run_time = datetime.now()

def check_restart():
  modtime = datetime.fromtimestamp(os.stat(os.path.abspath(__file__)).st_mtime)
  if modtime > first_run_time:
    safelog("Script has been updated, restarting to incorporate updates.",always=True)
    os._exit(1)


def tlm(test=False,testdata=None, token=None, car_model=None, debug=False, scripts=None):
  global global_debug
  global_debug = debug
  safelog("========> Initializing ABRP Script <========",always=True)
  safelog("car_model="+car_model,always=True)
  last_run = 0
  poller = Poller(car_model,token,scripts)
  while(True):
    now = time.time()
    next_run = last_run + 5
    if now < next_run:
      time.sleep(next_run - now)
    last_run = time.time()
    check_restart()
    try:
      poller.get_tlm()
    except Exception:
      safelog(traceback.format_exc(), always=True)
      os._exit(1)


class Poller():
  def __init__(self, typecode, token, scripts):
    self.apikey = '6f6a554f-d8c8-4c72-8914-d5895f58b1eb'
    self.token = token
    self.last_data_sent = {}
    self.last_data_time = time.time() - 600 # 10 minutes ago to ensure we check send on first run.
    self.last_sleep_time = time.time() - 600 # 10 minutes ago, to ensure we check sleep on first run.
    
    if scripts is not None:
      scripts = scripts.split(',')
      for i,script in enumerate(scripts):
        try:
          module = __import__(script)
          scripts[i] = module.ABRPAddOn() # ABRP Addon scripts are expected to have this class.
        except ImportError:
          safelog("No such script exists: "+script, always=True)
          os._exit(1)
        except SyntaxError:
          safelog("Syntax Error in "+script)
          safelog(traceback.format_exc(), always=True)
          os._exit(1)
        except:
          safelog("Some other exception occurred in "+script)
          safelog(traceback.format_exc(), always=True)
          os._exit(1)
    else:
      safelog("No scripts given in kwargs")
    self.scripts = scripts
    self.tc = TypeCode(typecode)
    if self.tc.make in ['chevy','opel']:
      self.car = Chevy(typecode)
    elif self.tc.make in ['hyundai','kia']:
      self.car = HKMC(typecode)
    else:
      self.car = CarOBD(typecode)
  
  def get_tlm(self):
    self.car.get_location()
    self.car.get_obd()
    self.car.clean_up_data()
    if not self.token:
      safelog("Token or Car Model missing from job kwargs")
      return None
    if "soc" in self.car.data:
      min_changed = ["soc","power","is_charging"]
      should_send = False
      for param in min_changed:
        if param in self.car.data and param in self.last_data_sent and self.car.data[param] != self.last_data_sent[param]:
          should_send = True
          break
      # Don't send if we're not charging or driving.
      if not self.car.is_charging() and not self.car.is_driving():
        safelog('Not sending because not charging or driving')
        should_send = False
      # Do send at least once every 60s if we're charging
      dt = time.time() - self.last_data_time
      if self.car.is_charging() and dt > 60:
        safelog('Sending because charging timeout')
        should_send = True
      # Do send at least once every 30s if we're driving.
      elif self.car.is_driving() and dt > 30:
        safelog('Sending because driving timeout')
        should_send = True
      # Send if the last status was charging, but this one is not.
      elif 'is_charging' in self.last_data_sent and self.last_data_sent['is_charging'] and not self.car.is_charging():
        safelog('Sending because just stopped charging.')
        should_send = True
      
      # Always send the first data point to initialize the session.    
      if self.last_data_sent == {}:
        should_send = True
      elif 'soc' in self.last_data_sent and 'soc' in self.car.data and self.last_data_sent['soc'] != self.car.data['soc']:
        should_send = True

      safelog("Sending: "+str(should_send))
      safelog(self.car.data)
      if should_send:
        data = self.car.get_pruned_data()
        params = {'token': self.token, 'api_key': self.apikey, 'tlm': json.dumps(data, separators=(',',':'))}
        url = 'https://api.iternio.com/1/tlm/send?'+urllib.urlencode(params)
        try:
          status = requests.get(url)
          self.last_data_sent = self.car.data
          self.last_data_time = time.time()
          safelog(url)
          safelog(status)
          safelog(status.text)
        except:
          status = None
      else:
        safelog("Not sending data, not recent or not driving/charging.")
    else:
      safelog("Not sending data, missing soc, or power")
      safelog(self.car.data)
    # Manage sleep as the last thing in the script.
    self.manage_sleep()
    if self.scripts is not None:
      for script in self.scripts:
        try:
          script.on_cycle(self.car.data)
        except:
          safelog(traceback.format_exc(), always=True)
          pass


  def manage_sleep(self,force=False):
    if time.time() - self.last_sleep_time < 60 and not force:
      return
    else:
      self.last_sleep_time = time.time()
    should_be_awake = False
    if not force:
      should_be_awake = self.car.should_be_awake()
    else:
      should_be_awake = True
    if should_be_awake:
      # clear all sleep timers and re-set timers for fail-safe.
      safelog("Should be awake:" +str(should_be_awake) + ' - Resetting sleep timer')
      try:
        __salt__['power.sleep_timer'](*[],**{'clear': '*', 'add': 'ABRP Sleep Timer', 'period': 600, 'reason': 'Vehicle inactive'})
      except:
        safelog(traceback.format_exc(), always=True)
        pass

###############################################################################
# Define functions to retrieve the PIDs (Modes and Codes) for each vehicle

# bytes_to_int(message.data[15:16])
# where 15:16 is off by 3 from the true index (accounting for header, 3 bytes)
# header = byte 0:1,1:2,2:3
# {1} = byte 3:4


var = re.compile(r'\{(\d+)\}')
bit = re.compile(r'\{(\d+):(\d+)\}')
signed = re.compile(r'\{([us]+):([\d:]+)\}')

def parse_pid_entry(pid):
  if "," not in pid:
    # Using the emulator values:
    return (None, pid, None, None)
  (mode,pid,formula,header) = re.split(",",pid)
  variables = var.findall(formula)
  if variables:
    for v in variables:
      code = get_mdata_to_bytes(v)
      this_var = r"\{"+re.escape(v)+r"\}"
      formula = re.sub(this_var,code,formula)
  bitwises = bit.findall(formula)
  if bitwises:
    for b in bitwises:
      code="(("
      code += get_mdata_to_bytes(b[0])
      ander = str(2**int(b[1]))
      code+="&"+ander+")/"+ander+")"
      this_var = r"\{"+re.escape(b[0]+":"+b[1])+r"\}"
      formula = re.sub(this_var,code,formula)
  signeds = signed.findall(formula)
  if signeds:
    # Convert:
    # {s:1:2} = twos_comp(bytes_to_int(message.data[3:4])*256 + bytes_to_int(message.data[4:5]),16)
    # {us:1:2} = bytes_to_int(message.data[3:4])*256 + bytes_to_int(message.data[4:5]
    for s in signeds:
      code = ""
      if s[0] == "s":
        code = "twos_comp(("
      if ":" in s[1]:
        byte_idx = s[1].split(":")
        code += get_mdata_to_bytes(byte_idx[0])
        code += "*256+"
        code += get_mdata_to_bytes(byte_idx[1])
        if s[0] == "s":
          code+="),16)"
      else:
        v=s[1]
        code+="bytes_to_int(message.data["
        code+=str(int(v)+2)+ ":"+str(int(v)+3)
        code+="])"
        if s[0] == "s":
          code+="),8)"

      this_var = r"\{[us]+"+re.escape(":"+s[1])+r"\}"
      formula = re.sub(this_var,code,formula)
  return (mode,pid,formula,header)

def get_mdata_to_bytes(i):
  code="bytes_to_int(message.data["
  code+=str(int(i)+2)+ ":"+str(int(i)+3)
  code+="])"
  return code

  #Notes: 
  # Converting from a Torque PID list may require some trial and error.  AutoPi uses two-part PIDs
  # Per the CAN methodology, service code and PID.
  # The "formula" can be easily converted for use here:
  #   A -> {1}
  #   B -> {2}
  #   C -> {3}
  #   Signed(A)*256+B -> {s:1:2}
  #   A*256+B -> {us:1:2}
  #   {J:0} -> {10:0}
  # If converting from an existing AutoPi script, just subtract two from the first position in your
  # message.data[] statement:
  # message.data[34:35] -> {32}
  # message.data[3:4] -> {1}
  # And then you can simplify your twos_comp and bytes_to_int by just calling the right version:
  # {s:1:2} = twos_comp(bytes_to_int(message.data[3:4])*256 + bytes_to_int(message.data[4:5]),16)
  # {us:1:2} = bytes_to_int(message.data[3:4])*256 + bytes_to_int(message.data[4:5])

class CarOBD:
  def __init__(self, typecode):
    self.tc = TypeCode(typecode)
    self.typecode = typecode
    # Default case is the emulator:
    self.pids = {
      # Emulator uses basic mode 01 PIDs for now, engine tab on the Freematics Emulator
      'soc':            "ABSOLUTE_LOAD", # Absolute Load Value
      'voltage':        "RPM", # Engine RPM
      'current':        "COOLANT_TEMP", # Engine Temperature
      'charge_voltage': "RPM", # Engine RPM
      'charge_current': "OIL_TEMP", # Engine Oil Temp
      'is_charging':    "TIMING_ADVANCE", # Timing Advance
      'speed':          "SPEED", # Vehicle Speed
    }
    self.data = {}
    
  def inflate_pids(self):
    for name in self.pids:
      (mode,pid,formula,header) = parse_pid_entry(self.pids[name])
      self.pids[name] = {
        'mode':     mode,
        'pid':      pid,
        'formula':  formula,
        'header':   header,
      }
  
  def get_obd(self):
    self.data = {} # Reset data to prevent old values from being carried forever.
    for name in self.pids:
      check_restart()
      pid = self.pids[name]
      if 'pid' not in pid:
        self.inflate_pids()
        pid = self.pids[name]
      try:
        args = [name]
        if pid['mode'] is None and pid['pid'] is not None:
          # using emulator, simpler call:
          args = [pid['pid']]
          kwargs = {}
        else:
          kwargs = {
            'mode': pid['mode'],
            'pid': pid['pid'],
            'header': pid['header'],
            'formula': pid['formula'],
            'verify': False,
            'force': True,
          }
        self.data[name] = __salt__['obd.query'](*args, **kwargs)['value']
      except:
        # Data doesn't exist for this PID, skip it.
        pass
  
  def get_location(self):
    check_restart()
    self.location = None
    try:
      self.location = __salt__['ec2x.gnss_location'](*[], **{})
    except:
      # Didn't get location data, skip it.
      pass

  def should_be_awake(self):
    should_be_awake = False
    if 'is_charging' in self.data and self.data['is_charging']:
      should_be_awake = True
    elif 'speed' in self.data and round(self.data['speed']) != 0:
      should_be_awake = True
    elif 'power' in self.data and abs(self.data['power']) > 0.3:
      should_be_awake = True
    elif self.is_driving() is not None:
      should_be_awake = self.is_driving() # Charging cases should be caught above
    return should_be_awake

  def is_driving(self):
    # Simple version if we don't have anything better. Override these per-vehicle if we have something better.
    if self.in_and_true('is_driving'):
      return True
    elif 'is_driving' in self.data:
      return False
    else:
      return None

  def is_charging(self):
    if self.in_and_true('is_charging'):
      return True
    elif 'is_charging' in self.data:
      return False
    else:
      return None

  def clean_up_data(self):
    data = self.data
    location = self.location
    if "speed" not in data and location is not None:
      data['speed'] = location['sog_km']
    if "is_charging" in data and data["is_charging"] != 0:
      # Standardize the "is_charging" parameters, not all cars have simple 0/1
      data["is_charging"] = 1
    else:
      data["is_charging"] = int(self.is_charging())
    for s in ["soh", "soc"]:
      # Constrain SOH and SOC to realistic values.  May need to rethink this later.
      if s in data and data[s] > 100:
        data[s] = 100
      elif s in data and data[s] < 0:
        data[s] = 0
    if "power" not in data and "current" in data and "voltage" in data:
      data["power"] = float(data["current"]) * float(data["voltage"]) / 1000.0 #kW
    if data["is_charging"] and "charge_voltage" in data and "charge_current" in data and round(data["charge_current"]) != 0:
      if data["charge_current"] > 0:
        data["charge_current"] *= -1
      data["power"] = float(data["charge_current"]) * float(data["charge_voltage"]) / 1000.0
      data["voltage"] = float(data["charge_voltage"])
      data["current"] = float(data["charge_current"])
      safelog("Using charge power instead of raw value")
    if "is_charging" in data and data["is_charging"] and "power" in data and int(data['power']) == 0:
      data["is_charging"] = 0 # Ignore non-charge events.
    # Truncate data to reduce bandwidth usage
    for d in ['soc','soh','capacity','voltage','current','power','ext_temp','batt_temp']:
      if d in data:
        data[d] = round(data[d]*10)/10
    # utc - Current UTC timestamp in seconds
    data['utc'] = round(time.time())
    if location is not None:
      data['lat'] = location['lat']
      data['lon'] = location['lon']
      data['heading'] = location['cog']
    elif self.typecode == "emulator":
      data['lat'] = 28.608321
      data['lon'] = -80.604153

  def get_pruned_data(self):
    data = self.data.copy()
    allowed_params = ["utc", "soc", "soh", "speed", "lat", "lon", "elevation", "heading", "is_charging", "power", 
    "ext_temp", "current", "voltage", "batt_temp", "car_model", "session_id", "timestamp", "location", 
    "heading", "odometer", "kwh_charged", "is_dcfc", "capacity"]
    for d in self.data:
      if d not in allowed_params:
        del data[d]
    return data

  def in_and_true(self,param):
    return param in self.data and self.data[param]

class Chevy(CarOBD):
  def __init__(self,typecode):
    CarOBD.__init__(self, typecode)
    self.pids = {
      'soc':            "22,8334,({1}*100.0/255.0),7E4",
      'voltage':        "22,2885,({us:1:2})/100.0,7E1",
      'charge_voltage': "22,436B,({us:1:2})/2.0,7E4",
      'current':        "22,2414,({s:1:2})/20.0,7E1",
      'charge_current': "22,436C,({s:1:2})/20.0,7E4",
      'is_charging':    "22,436C,({s:1:2})/20.0,7E4",
      'ext_temp':       "22,801E,({1}/2)-40.0,7E4",
      'batt_temp':      "22,434F,({1}-40.0),7E4",
      'prnd':           "22,2889,({1}),7E1", # 8=P, 3=D, 7=R, 6=N, 1=L
      'might_be_dcfc':  "22,4369,(not {1}),7E4", #AC current.  If AC Current is 0, then it could be a DCFC
    }
    if int(self.tc.year) < 19:
      self.pids['capacity'] = "22,41A3,({us:1:2})/30.0,7E4" #Reports strange results in post-2019 Bolts.
    self.inflate_pids()

  ###################################################################################################
  # Override functions go here:
  def is_driving(self):
    if 'is_charging' in self.data and self.data['is_charging']:
      return False
    if 'prnd' in self.data and self.data['prnd'] != 8:
      return True
    elif 'prnd' in self.data and self.data['prnd'] == 8:
      return False
    else:
      return False # No response means we're not driving.
  
  def is_charging(self):
    if self.in_and_true('is_charging') and self.in_and_true('might_be_dcfc'):
      self.data['is_dcfc'] = 1
    return self.in_and_true('is_charging')

class HKMC(CarOBD):
  def __init__(self,typecode):
    CarOBD.__init__(self, typecode)
    if int(self.tc.year) >= 19:
      self.pids = {
        'soc':        "220,105,({32}/2.0),7E4",
        'soh':        "220,105,({us:26:27})/10.0,7E4",
        'voltage':    "220,101,({us:13:14})/10.0,7E4",
        'current':    "220,101,({s:11:12})/10.0,7E4", 
        # 'is_charging':"220,101,int(not {51:2}),7E4",
        'ext_temp':   "220,100,({7}/2.0)-40.0,7B3",
        'batt_temp':  "220,101,{s:17},7E4",
        #'odometer':   "22,B002,{us:9:12},7C6" # Need to add 3-byte support.
        'is_bms':     "220,101,{10:0},7E4",
        'is_ignit':   "220,101,{51:2},7E4",
        'rpm':  "220,101,{s:54:55},7E4"
      }
    # elif int(self.tc.year) < 19:
    #   # older cars
    #   self.inflate_pidspids = {
    #     'soc':        "2,105,({32}/2.0),7E4",
    #     'soh':        "2,105,({us:26:27})/10.0,7E4",
    #     'voltage':    "2,101,({us:13:14})/10.0,7E4",
    #     'current':    "2,101,({s:11:12})/10.0,7E4", 
    #     'is_charging':"2,101,int(not {51:2}),7E4",
    #     'ext_temp':   "2,100,({7}/2.0)-40.0,7B3",
    #     'batt_temp':  "2,101,({s:17}),7E4", # Average the modules?
    #     #'odometer':   "22,B002,{us:11:14},7C6"
    #   }
    self.inflate_pids()

  def is_charging(self):
    if 'power' not in self.data and {'voltage', 'current'}.issubset(self.data.keys()):
      self.data['power'] = self.data['voltage'] * self.data['current']
    if {'is_bms','power','rpm'}.issubset(self.data.keys()) \
      and self.data['is_bms'] and self.data['power'] < -1 and abs(self.data['rpm']) < 1:
      return True
    else:
      return False

  def is_driving(self):
    # Don't have a shifter PID, so check not charging and ignition
    if self.is_charging():
      return False
    elif 'is_ignit' in self.data and self.data['is_ignit']:
      return True
    else:
      return False


# Following are testing functions to make sure things are working right. Ish.
msg_data = re.compile(r'message.data')
def bytes_to_int(bytes):
  result = 0
  for b in bytes:
    result = result * 256 + int(b)
  return result

def twos_comp(bytes,bitness):
  return bytes

def check_formula(formula):
  #Given a formula in a string, evaluate it.
  #Assuming we are given a data string:
  formula = re.sub("message.data","mdata",formula,flags=re.I)
  mdata = "7E81014490201314731"
  eval(formula)

def safelog(text,always=False):
  if global_debug or always:
    try:
      text = 'ABRP: ' + text
      text = re.sub(r'\n',"\nABRP: ",text)
      log.info(text)
      if command_line:
        print(text)
    except:
      print(text)

class TypeCode:
  def __init__(self, typecode):
    self.code = typecode
    self.array = self.code.split(":")
    self.manufacturer = self.array[0]
    self.make = self.array[0]
    if len(self.array) > 1:
      self.model = self.array[1]
      self.year = self.array[2]
      self.battery = self.array[3]
      if len(self.array) > 4: 
        self.options = self.array[4:]
      else:
        self.options = []

if __name__ == "__main__":
  global_debug = True
  command_line = True
  import pprint
  pp = pprint.PrettyPrinter(indent=2)

  print "Running from command line."
  # last_data = {}
  # last_data_time = time.time()
  typecodes = ['hyundai:ioniq:14:28:other','chevy:bolt:17:60:other','hyundai:kona:19:64:other','emulator']
  for typecode in typecodes:
    poller = Poller(typecode,'test',None)
    pp.pprint(poller.car.pids)
    poller.get_tlm()

  # tlm(test=True,testdata={"soc": 88.4, "soh":100, "voltage":388.0, "current": 40,
  #   "is_charging": 0, "ext_temp":20, "batt_temp": 20, "lat":29.5641, "lon":-95.0255, "speed":113.2
  # },token="test",car_model='chevy:bolt:17:60:other')