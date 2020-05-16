import time
import logging
import json
import requests,urllib
import re
import traceback
from datetime import datetime
import os

first_run_time = datetime.now()

# Structure and lots of initial work from @plord12 on GitHub:
# https://github.com/plord12/autopi-tools

log = logging.getLogger(__name__)
###################################################################################################
abrp_apikey = '6f6a554f-d8c8-4c72-8914-d5895f58b1eb'

# Should probably make this into a class...
last_data = {}
last_data_time = time.time()
global_debug = False
last_sleep_time = time.time()


def tlm(test=False,testdata=None, token=None, car_model=None, debug=False):
  global first_run_time
  global global_debug
  global_debug = debug
  safelog("========> Initializing ABRP Script <========",always=True)
  safelog("car_model="+car_model,always=True)
  manage_sleep({},force=True) # Force the device to stay awake for at least a few minutes
  while(True):
    modtime = datetime.fromtimestamp(os.stat(os.path.abspath(__file__)).st_mtime)
    if modtime > first_run_time:
      safelog("ABRP Script has been updated, restarting to incorporate updates.",always=True)
      os._exit(1)
    try:
      get_tlm(test=test,testdata=testdata,token=token,car_model=car_model)
    except Exception:
      safelog(traceback.format_exc(), always=True)
      os._exit(1)

def get_tlm(test=False,testdata=None,token=None,car_model=None):
  global last_data
  global last_data_time
  global abrp_apikey
  data = {}
  location = None
  if test:
    data = testdata
  else:
    try:
      location = get_location()
    except:
      pass
    # Get all available telemetry from the car:
    pids = get_pids(car_model)
    if pids == {}:
      safelog("No PIDs found for car_model: "+car_model)
      os._exit(1)
    for p in pids:
      (mode,pid,formula,header) = parse_pid_entry(pids[p])
      try:
        data[p] = get_obd(p,mode,pid,formula,header)
      except:
        pass
  if data != {}:
    if "speed" not in data and location is not None:
      data['speed'] = location['sog_km']
    if "is_charging" in data and data["is_charging"] != 0:
      # Standardize the "is_charging" parameters, not all cars have simple bool
      data["is_charging"] = 1
    else:
      data["is_charging"] = 0
    for s in ["soh", "soc"]:
      # Constrain SOH and SOC to realistic values.
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
    if "charge_voltage" in data:
      del data["charge_voltage"]
    if "charge_current" in data:
      del data["charge_current"]
  # Truncate data to reduce bandwidth usage
  for d in ['soc','soh','capacity','voltage','current','power','ext_temp','batt_temp']:
    if d in data:
      data[d] = round(data[d]*10)/10
  # utc - Current UTC timestamp in seconds
  data['utc'] = time.time()
  data["car_model"] = car_model
  
  # lat - User's current latitude
  if location is not None:
    data['lat'] = location['lat']
    data['lon'] = location['lon']
    # data['elevation'] = location['alt'] # Don't trust GPS elevation, do lookup instead
    data['heading'] = location['cog']
  elif car_model == "emulator":
    data['lat'] = 28.608321
    data['lon'] = -80.604153

  if not (token and car_model):
    safelog("Token or Car Model missing from job kwargs")
    return None
  
  manage_sleep(data)

  if "soc" in data and "power" in data:
    params = {'token': token, 'api_key': abrp_apikey, 'tlm': json.dumps(data, separators=(',',':'))}
    url = 'https://api.iternio.com/1/tlm/send?'+urllib.urlencode(params)

    min_changed = ["soc","power","is_charging"]
    should_send = False
    for param in min_changed:
      if param in data and param   in last_data and data[param] != last_data[param]:
        should_send = True
        break
    if "is_charging" in data and data["is_charging"] and time.time() - last_data_time > 100:
      should_send = True
    elif "is_charging" in data and not data["is_charging"] and time.time() - last_data_time > 30:
      should_send = True
    elif last_data == {}:
      should_send = True
    
    safelog("Sending: "+str(should_send))
    safelog(last_data)
    if should_send:
      try:
        status = requests.get(url)
        last_data = data
        last_data_time = time.time()
        safelog(url)
        safelog(status)
        safelog(status.text)
      except:
        status = None
    else:
      safelog("Not sending data, hasn't changed recently enough.")
  else:
    safelog("Not sending data, missing soc, or power")
    safelog(data)

###################################################################################################
# Following are methods for retrieving data from the car
def get_location():
  args = []
  kwargs = {}
  return __salt__['ec2x.gnss_location'](*args, **kwargs)

def get_obd(name,mode=None,pid=None,formula=None,header=None):
  args = [name]
  if not header:
    header = "7E4"
  if not formula:
    formula = "message.data"
  if mode is None and pid is not None:
    # using emulator, simpler call:
    args = [pid]
    kwargs = {}
  else:
    kwargs = {
      'mode': mode,
      'pid': pid,
      'header': header,
      'formula': formula,
      'verify': False,
      'force': True,
    }
  
  return __salt__['obd.query'](*args, **kwargs)['value']


###################################################################################################
# Following are methods for managing wake/sleep

def manage_sleep(data, force=False):
  global last_sleep_time
  if time.time() - last_sleep_time < 60 and not force:
    safelog("Not updating sleep timer.")
    return
  else:
    last_sleep_time = time.time()
  should_be_awake = False
  if not force:
    if 'power' in data and round(data['power']) != 0:
      should_be_awake = True
    elif 'speed' in data and round(data['speed']) != 0:
      should_be_awake = True
    elif 'is_charging' in data and data['is_charging']:
      should_be_awake = True
  else:
    # Force to be awake
    should_be_awake = True
  if should_be_awake:
    # clear all sleep timers and re-set timers for fail-safe.
    safelog("Should be awake:" +str(should_be_awake) + ' - Resetting sleep timer')
    __salt__['power.sleep_timer'](*[],**{'clear': '*', 'add': 'ABRP Sleep Timer', 'period': 600, 'reason': 'Vehicle inactive'})

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

def get_pids(car_model):
  pids = {}
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
  
  if car_model in ["chevy:bolt:17:60:other","chevy:bolt:20:66",'chevy:spark:14:19:other',"opel:ampera-e:17:60:other"]:
    pids = {
      'soc':        "22,8334,({1}*100.0/255.0),7E4",
      'capacity':        "22,41a3,({us:1:2})/30.0,7E4",
      'voltage':    "22,2885,({us:1:2})/100.0,7E1",
      'charge_voltage': "22,436B,({us:1:2})/2.0,7E4",
      'current':    "22,2414,({s:1:2})/20.0,7E1",
      'charge_current': "22,436C,({s:1:2})/20.0,7E4",
      # 'speed':      "22,000D,{1},7E0",
      'is_charging':"22,436c,({s:1:2})/20.0,7E4",
      'ext_temp':   "22,801E,({1}/2)-40.0,7E4",
      'batt_temp':  "22,434F,({1}-40.0),7E4",
    }
  elif car_model in ["hyundai:kona:19:64:other","hyundai:kona:19:39:other"]:
    pids = {
      'soc':        "220,105,({32}/2.0),7E4",
      'soh':   "220,105,({us:26:27})/10.0,7E4",
      'voltage':    "220,101,({us:13:14})/10.0,7E4",
      'current':    "220,101,({s:11:12})/10.0,7E4", 
      # 'speed':      "220,100,'{30}',7B3",
      'is_charging':"220,101,{10:0}-{51:2},7E4", # 7th bit in the byte is charging status
      'ext_temp':   "220,100,({7}/2.0)-40.0,7B3",
      'batt_temp':  "220,101,{s:17},7E4",
    }
  elif car_model == 'emulator':
    pids = {
      # Emulator uses basic mode 01 PIDs for now, engine tab on the Freematics Emulator
      'soc':        "ABSOLUTE_LOAD", # Absolute Load Value
      'voltage':    "RPM", # Engine RPM
      'current':    "COOLANT_TEMP", # Engine Temperature
      'charge_voltage':    "RPM", # Engine RPM
      'charge_current':    "OIL_TEMP", # Engine Oil Temp
      'is_charging':"TIMING_ADVANCE", # Timing Advance
      'speed':      "SPEED", # Vehicle Speed
    }
  return pids

###################################################################################################
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
  global global_debug
  if global_debug or always:
    try:
      text = "ABRP Script: "+str(text)
      log.info(text)
    except:
      print(text)

if __name__ == "__main__":
  global global_debug
  global_debug = True
  print "Running from command line."
  last_data = {}
  last_data_time = time.time()
  pids = get_pids("emulator")
  # pids = get_pids("kona:19")
  for pid in pids:
    print (pid,parse_pid_entry(pids[pid]))
    (mode,pid,formula,header) = parse_pid_entry(pids[pid])
    if formula is not None:
      check_formula(formula)

  tlm(test=True,testdata={"soc": 88.4, "soh":100, "voltage":388.0, "current": 40,
    "is_charging": 0, "ext_temp":20, "batt_temp": 20, "lat":29.5641, "lon":-95.0255, "speed":113.2
  },token="test",car_model='chevy:bolt:17:60:other')