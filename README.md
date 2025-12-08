pi@raspberrypi:~/RFID $ uv run flet run src/battery/main_rp.py

(flet:4916): Atk-CRITICAL **: 16:23:26.706: atk_socket_embed: assertion 'plug_id != NULL' failed
/home/pi/RFID/src/battery/main_rp.py:278: RuntimeWarning: This channel is already in use, continuing anyway.  Use GPIO.setwarnings(False) to disable warnings.
  GPIO.setup(15, GPIO.OUT, initial=GPIO.LOW)
Unhandled error processing page session : Traceback (most recent call last):
  File "/home/pi/RFID/.venv/lib/python3.11/site-packages/flet/app.py", line 244, in on_session_created
    await session_handler(page)
  File "/home/pi/RFID/src/battery/main_rp.py", line 598, in main
    page.run_task(monitor_gpio_task())
  File "/home/pi/RFID/.venv/lib/python3.11/site-packages/flet/core/page.py", line 887, in run_task
    assert asyncio.iscoroutinefunction(handler)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError

/home/pi/RFID/.venv/lib/python3.11/site-packages/flet/app.py:256: RuntimeWarning: coroutine 'main.<locals>.monitor_gpio_task' was never awaited
  page.error(f"There was an error while processing your request: {e}")
RuntimeWarning: Enable tracemalloc to get the object allocation traceback
