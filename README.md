```
pi@raspberrypi:~/RFID $ uv run flet run src/battery/main_rp.py
2025-12-09 15:48:21,573 [INFO] Assets path configured: /home/pi/RFID/src/battery/assets
2025-12-09 15:48:21,577 [INFO] Starting up UDS server on /tmp/AILgLlJXfX
2025-12-09 15:48:21,577 [INFO] Flet app has started...

(flet:5567): Atk-CRITICAL **: 15:48:22.465: atk_socket_embed: assertion 'plug_id != NULL' failed
2025-12-09 15:48:23,316 [INFO] App session started
2025-12-09 15:48:23,321 [WARNING] Signal handlers not set: signal only works in main thread of the main interpreter
2025-12-09 15:48:30,330 [INFO] PCA9539 initialized successfully.
sclk_frequency = 1.0 MHz
embedder.cc (2519): 'FlutterEngineRemoveView' returned 'kInvalidArguments'. Remove view info was invalid. The implicit view cannot be removed.
2025-12-09 15:48:36,220 [ERROR] Exception in callback BaseEventLoop.run_in_executor(<concurrent.f... 0x7fa5a11c10>, functools.par...een='false'))))
handle: <Handle BaseEventLoop.run_in_executor(<concurrent.f... 0x7fa5a11c10>, functools.par...een='false'))))>
Traceback (most recent call last):
  File "/home/pi/.local/share/uv/python/cpython-3.11.14-linux-aarch64-gnu/lib/python3.11/asyncio/events.py", line 84, in _run
    self._context.run(self._callback, *self._args)
  File "/home/pi/.local/share/uv/python/cpython-3.11.14-linux-aarch64-gnu/lib/python3.11/asyncio/base_events.py", line 830, in run_in_executor
    executor.submit(func, *args), loop=self)
    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/home/pi/.local/share/uv/python/cpython-3.11.14-linux-aarch64-gnu/lib/python3.11/concurrent/futures/thread.py", line 167, in submit
    raise RuntimeError('cannot schedule new futures after shutdown')
RuntimeError: cannot schedule new futures after shutdown
2025-12-09 15:48:36,246 [INFO] I2C bus closed on shutdown.
```
