# ABRP for AutoPi

Thanks for checking out our API code for AutoPi! You can find the installation instructions in the app by selecting one of the following compatible cars and then selecting "Add my Car":
 1. Chevy Bolt EV (all years)  
 2. Hyundai Kona (all models)  

If you'd like to add another car, the code is open source, feel free to modify the code and submit a pull request. If you have questions on how to do this contact us at contact@abetterrouteplanner.com

## Add-Ons for the ABRP Script

The ABRP script now supports Add-Ons.  We've found that the obd.query calls via `__salt__` take quite a long time to execute, so making multiple redundant calls really slows the whole system down.

To add one of your scripts to be run, simple add copy our example code from my_script.py, and add it to the AutoPi Custom Code as an Execute script.  Make your modifications, and then add a `scripts` kwarg to the ABRP Job:
`scripts=my_script`
If you have more than one script, they must be comma-separated:
`scripts=my_script,my_other_script,my_third_script`
Some helpful use tips:

 - The `__init__` function will be called on when the ABRP script starts up.
 - The `on_cycle` function will be called about once every 5 seconds (depending on how long it takes to retrieve OBD data).

Just like the main script, as long as you leave the `check_restart` call in place, it'll stop and let the job restart the script whenever you make an update to make troubleshooting / development easier.

## License and Thanks
This code is published under the open Apache license, however the app sourcecode itself is not open.

  

Thank you so much for helping out!

  

Jason and the Iternio Team