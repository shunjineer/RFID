embedder.cc (2519): 'FlutterEngineRemoveView' returned 'kInvalidArguments'. Remove view info was invalid. The implicit view cannot be removed.
pi@raspberrypi:~/RFID $ uv run flet run src/battery/main_rp.py
2025-12-08 20:35:51,401 [INFO] Working directory set to: /home/pi/RFID
2025-12-08 20:35:51,401 [INFO] Assets path configured: /home/pi/RFID/src/battery/img
2025-12-08 20:35:51,403 [INFO] Assets path configured: /home/pi/RFID/src/battery/src/battery/img
2025-12-08 20:35:51,409 [INFO] Starting up UDS server on /tmp/XXMPCL7Tbp
2025-12-08 20:35:51,410 [INFO] Flet app has started...

(flet:8149): Atk-CRITICAL **: 20:35:51.935: atk_socket_embed: assertion 'plug_id != NULL' failed
2025-12-08 20:35:52,697 [INFO] App session started
/home/pi/RFID/src/battery/main_rp.py:68: RuntimeWarning: This channel is already in use, continuing anyway.  Use GPIO.setwarnings(False) to disable warnings.
  GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
embedder.cc (2519): 'FlutterEngineRemoveView' returned 'kInvalidArguments'. Remove view info was invalid. The implicit view cannot be removed.
