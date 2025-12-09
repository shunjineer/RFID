pi@raspberrypi:~/RFID $ uv run flet run src/battery/main_rp.py

(flet:9025): Atk-CRITICAL **: 19:40:28.095: atk_socket_embed: assertion 'plug_id != NULL' failed
[ERROR] I2C write failed: reg=0x02, val=0x00 err=[Errno 5] Input/output error
Traceback (most recent call last):
  File "/home/pi/RFID/src/battery/main_rp.py", line 143, in _i2c_write_byte
    self.bus.write_byte_data(PCA9539_ADDR, reg, value & 0xFF)
  File "/home/pi/RFID/.venv/lib/python3.11/site-packages/smbus2/smbus2.py", line 457, in write_byte_data
    ioctl(self.fd, I2C_SMBUS, msg)
OSError: [Errno 5] Input/output error
[ERROR] I2C outputs_all_low failed: [Errno 5] Input/output error
Traceback (most recent call last):
  File "/home/pi/RFID/src/battery/main_rp.py", line 163, in i2c_outputs_all_low
    self._i2c_write_byte(REG_OUTPUT0, 0x00)
  File "/home/pi/RFID/src/battery/main_rp.py", line 143, in _i2c_write_byte
    self.bus.write_byte_data(PCA9539_ADDR, reg, value & 0xFF)
  File "/home/pi/RFID/.venv/lib/python3.11/site-packages/smbus2/smbus2.py", line 457, in write_byte_data
    ioctl(self.fd, I2C_SMBUS, msg)
OSError: [Errno 5] Input/output error
