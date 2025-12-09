```
pi@raspberrypi:~/RFID $ uv run flet run src/battery/main_rp.py

(flet:8276): Atk-CRITICAL **: 19:21:04.116: atk_socket_embed: assertion 'plug_id != NULL' failed
/home/pi/RFID/src/battery/main_rp.py:121: RuntimeWarning: This channel is already in use, continuing anyway.  Use GPIO.setwarnings(False) to disable warnings.
  GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
/home/pi/RFID/src/battery/main_rp.py:446: RuntimeWarning: This channel is already in use, continuing anyway.  Use GPIO.setwarnings(False) to disable warnings.
  GPIO.setup(GPIO_SPI_EN, GPIO.OUT, initial=GPIO.HIGH)
sclk_frequency = 1.0 MHz
[ERROR] SPI start failed: 'BatteryApp' object has no attribute 'spi_read_task'
Traceback (most recent call last):
  File "/home/pi/RFID/src/battery/main_rp.py", line 464, in on_play
    self.page.run_task(self.spi_read_task)
                       ^^^^^^^^^^^^^^^^^^
AttributeError: 'BatteryApp' object has no attribute 'spi_read_task'
embedder.cc (2519): 'FlutterEngineRemoveView' returned 'kInvalidArguments'. Remove view info was invalid. The implicit view cannot be removed.

** (flet:8276): WARNING **: 19:21:17.513: Attempted to set message handler on an FlBinaryMessenger without an engine
```
