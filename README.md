# ABRP for AutoPi

Thanks for checking out our API code for AutoPi! You can find the installation instructions in the app by selecting one of the following compatible cars and then selecting "Add my Car":
 1. Chevy Bolt EV (all years)  
 2. Hyundai Kona (all models)  

If you'd like to add another car, the code is open source, feel free to modify the code and submit a pull request. If you have questions on how to do this contact us at contact@abetterrouteplanner.com

## Add-Ons for the ABRP Script

The ABRP script now supports Add-Ons.  We've found that the obd.query calls via `__salt__` take quite a long time to execute, so making multiple redundant calls really slows the whole system down.

To add one of your scripts to be run, start by making of copy of `my_script.py`.  Rename it and write your code.  Once you're ready to test:  
1. Add it to AutoPi Custom Code
2. Set Type to **Execution**
3. Add a Kwarg to the ABRP Job `scripts=my_script` after all the other kwargs
4. Sync the script and job to your AutoPi and reboot it.

If you have more than one script to run, just add them all comma-separated to the scripts kwarg: `scripts=my_script,my_other_script,my_third_script`

The ABRP Script has to be restarted to see new scripts, this can be done by rebooting the AutoPi, or simply making a small change (adding a comment) and syncing the script to the device.

To work on development with your script I recommend:  
1. SSHing into the Autopi ([how-to from AutoPi](https://community.autopi.io/t/guide-how-to-ssh-to-your-dongle/386))
2. Tailing the Logfile using a grep command  
`sudo tail -f /var/log/salt/minion | grep my_script`   
  which will filter to only show the logs from your script (insert the name of your script instead of my_script)

Then you can make changes and see the results in real time (after the script restarts)

Finally, some notes on the functions in the template (You'll want to keep all of these functions, though you're welcome to add your own to refine the behavior):
 - The `__init__` function will be called on when the ABRP script starts up.
 - The `on_cycle` function will be called about once every 5 seconds (depending on how long it takes to retrieve OBD data).  The ABRP Script will pass this function a `data` dictionary with all the OBD data it retrieves for your car.  At minimum this will typically contain ( Others might be available depending on your car.  Try logging this to see what you get.):
   - `soc` State of Charge
   - `soh` State of Health
   - `voltage` Main battery voltage (V)
   - `current` Main battery current (negative if charging) (A)
   - `is_charging` 1 if charging, 0 otherwise
   - `power` Power input/output of the battery (kW)
   - `lat`, `lon` Measured by GPS/GNSS
   - `speed` km/h - measured by GPS/GNSS  

 - The `check_restart` function can be called whenever you like (I have it called during `on_cycle`) to see if the script has updated and quit if it has.

Feel free to post any questions you have on the ABRP Forums, or email me directly (jason@abetterrouteplanner.com) 

## License and Thanks
This code is published under the open Apache license, however the app sourcecode itself is not open.

Thank you so much for helping out!

Jason and the Iternio Team