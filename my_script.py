from datetime import datetime
import logging
import os

log = logging.getLogger(__name__)
first_run_time = datetime.now()
###############################################################################################
# Code to kill the script if we get updated.  Needed to incorporate changes.
def check_restart():
  modtime = datetime.fromtimestamp(os.stat(os.path.abspath(__file__)).st_mtime)
  if modtime > first_run_time:
    log.info("Script has been updated, restarting to incorporate updates.")
    os._exit(1)
###############################################################################################

class ABRPAddOn():
  def __init__(self):
    # Do things on start:
    log.info("Started the script.")
    self.started = True
  
  def on_cycle(self,data):
    # Do things on every data update:
    log.info("gave us:"+str(data))
    
    check_restart()

