# main.py
# Python 3.11
# Flet desktop app controlling TI PCA9539PWR via I2C on Raspberry Pi 4B (Debian Trixie).
# - Uses GPIO4 as input with internal pull-down.
# - Uses GPIO15 as output (reset for PCA9539): initially Low, set High 100ms after GPIO4 becomes High.
# - Starts I2C initialization 100ms after GPIO15 becomes High.
# - Provides 16 CupertinoSwitches (4x4) to control P00..P07, P10..P17.
# - Displays GPIO4 and GPIO15 logical levels, updated every 0.5s.

import threading
import time
from typing import Optional, Tuple

import flet as ft

try:
    import RPi.GPIO as GPIO
except Exception as e:
    raise RuntimeError("RPi.GPIO のインポートに失敗しました。Raspberry Pi 上で実行しているか、ライブラリがインストールされているか確認してください。") from e

try:
    from smbus2 import SMBus
except Exception as e:
    raise RuntimeError("smbus2 のインポートに失敗しました。'pip install smbus2' を実行してください。") from e


# Raspberry Pi pins (BCM numbering)
GPIO_IN_PIN = 4    # physical pin 7
GPIO_RST_PIN = 15  # physical pin 10

# I2C
I2C_BUS_ID = 1  # Raspberry Pi 4B: I2C-1 on GPIO2(SDA)/GPIO3(SCL)
PCA9539_ADDR = 0x74

# PCA9539 Registers
REG_INPUT_0 = 0x00
REG_INPUT_1 = 0x01
REG_OUTPUT_0 = 0x02
REG_OUTPUT_1 = 0x03
REG_POLARITY_0 = 0x04
REG_POLARITY_1 = 0x05
REG_CONFIG_0 = 0x06  # 1: input, 0: output
REG_CONFIG_1 = 0x07

# Delays (seconds)
DELAY_AFTER_GPIO4_HIGH_TO_RST_HIGH = 0.100  # 100ms
DELAY_AFTER_RST_HIGH_TO_I2C = 0.100         # 100ms


class PCA9539:
    def __init__(self, bus: SMBus, addr: int = PCA9539_ADDR, lock: Optional[threading.Lock] = None):
        self.bus = bus
        self.addr = addr
        self.lock = lock or threading.Lock()

    def _write_reg(self, reg: int, value: int):
        with self.lock:
            self.bus.write_byte_data(self.addr, reg, value & 0xFF)

    def _read_reg(self, reg: int) -> int:
        with self.lock:
            return self.bus.read_byte_data(self.addr, reg) & 0xFF

    def _write_and_verify(self, reg: int, value: int, retries: int = 3, delay: float = 0.005):
        last = None
        for _ in range(retries):
            self._write_reg(reg, value)
            time.sleep(delay)
            last = self._read_reg(reg)
            if last == (value & 0xFF):
                return
            time.sleep(delay)
        raise IOError(f"PCA9539 register 0x{reg:02X} verify failed (wrote 0x{value:02X}, read 0x{(last if last is not None else -1):02X})")

    def probe(self, retries: int = 3, delay: float = 0.010):
        last_exc = None
        for _ in range(retries):
            try:
                _ = self._read_reg(REG_INPUT_0)
                return
            except Exception as e:
                last_exc = e
                time.sleep(delay)
        raise IOError(f"PCA9539 at 0x{self.addr:02X} not responding") from last_exc

    def init_safe(self):
        # Safe sequence:
        # 1) Polarity = 0x00/0x00 (no inversion)
        # 2) Output Port = 0x00/0x00 (desired initial output level)
        # 3) Configuration = 0x00/0x00 (set all as output)
        self.probe()
        self._write_and_verify(REG_POLARITY_0, 0x00)
        self._write_and_verify(REG_POLARITY_1, 0x00)
        self._write_and_verify(REG_OUTPUT_0, 0x00)
        self._write_and_verify(REG_OUTPUT_1, 0x00)
        self._write_and_verify(REG_CONFIG_0, 0x00)
        self._write_and_verify(REG_CONFIG_1, 0x00)

    def set_outputs(self, port0_value: int, port1_value: int, verify: bool = False):
        self._write_reg(REG_OUTPUT_0, port0_value)
        self._write_reg(REG_OUTPUT_1, port1_value)
        if verify:
            r0 = self._read_reg(REG_OUTPUT_0)
            r1 = self._read_reg(REG_OUTPUT_1)
            if (r0 != (port0_value & 0xFF)) or (r1 != (port1_value & 0xFF)):
                raise IOError(f"Output write verify failed: wrote (0x{port0_value:02X},0x{port1_value:02X}) read (0x{r0:02X},0x{r1:02X})")


def main(page: ft.Page):
    page.title = "PCA9539 Controller (Flet Desktop)"
    page.window_width = 700
    page.window_height = 600
    page.padding = 20

    # Shared state
    state = {
        "bus": None,                 # SMBus instance
        "pca": None,                 # PCA9539 instance
        "lock": threading.Lock(),    # I2C lock
        "i2c_ready": False,          # True after successful init
        "rst_released": False,       # True after GPIO15 set High
        "port0": 0x00,               # P00..P07 output image
        "port1": 0x00,               # P10..P17 output image
        "stop_event": threading.Event(),
    }

    # UI elements
    info_text = ft.Text(value="Initializing...", selectable=True)
    gpio4_label = ft.Text(value="GPIO4: Low")
    gpio15_label = ft.Text(value="GPIO15: Low")
    i2c_status = ft.Text(value="I2C: Not initialized")

    # pubsub handler (runs on UI thread)
    def on_msg(msg):
        t = msg.get("type")
        if t == "log":
            info_text.value = msg.get("text", "")
            page.update()
        elif t == "gpio":
            if "gpio4" in msg:
                gpio4_label.value = f"GPIO4: {'High' if msg['gpio4'] else 'Low'}"
            if "gpio15" in msg:
                gpio15_label.value = f"GPIO15: {'High' if msg['gpio15'] else 'Low'}"
            page.update()
        elif t == "enable_switches":
            enable = bool(msg.get("enable", False))
            for sw in switches:
                sw.disabled = not enable
            page.update()
        elif t == "i2c_status":
            i2c_status.value = msg.get("text", "")
            page.update()

    page.pubsub.subscribe(on_msg)

    # Helpers for background thread to request UI updates
    def log(msg: str):
        page.pubsub.send_all({"type": "log", "text": msg})

    def set_gpio_labels(gpio4_high=None, gpio15_high=None):
        payload = {"type": "gpio"}
        if gpio4_high is not None:
            payload["gpio4"] = bool(gpio4_high)
        if gpio15_high is not None:
            payload["gpio15"] = bool(gpio15_high)
        page.pubsub.send_all(payload)

    def enable_switches(enable: bool):
        page.pubsub.send_all({"type": "enable_switches", "enable": bool(enable)})

    def set_i2c_status(text: str):
        page.pubsub.send_all({"type": "i2c_status", "text": text})

    # Build 16 CupertinoSwitches in 4x4 grid
    switches = []

    def make_switch(cell_index: int) -> Tuple[ft.Container, ft.CupertinoSwitch]:
        i = cell_index - 1
        label = f"Cell {cell_index:02d}"
        sw = ft.CupertinoSwitch(value=False, on_change=None)
        if i < 8:
            mapping = ("P0", i)  # P00..P07
        else:
            mapping = ("P1", i - 8)  # P10..P17
        sw.data = mapping
        sw.disabled = True  # disabled until I2C ready

        def on_switch_change(e: ft.ControlEvent):
            if not state["i2c_ready"]:
                # Guard
                sw.value = not sw.value
                page.update()
                return
            port, bit = sw.data
            if port == "P0":
                if sw.value:
                    state["port0"] |= (1 << bit)
                else:
                    state["port0"] &= ~(1 << bit)
            else:
                if sw.value:
                    state["port1"] |= (1 << bit)
                else:
                    state["port1"] &= ~(1 << bit)
            try:
                state["pca"].set_outputs(state["port0"], state["port1"])
                i2c_status.value = f"I2C OK: OUT0=0x{state['port0']:02X}, OUT1=0x{state['port1']:02X}"
            except Exception as ex:
                # Rollback
                if port == "P0":
                    if sw.value:
                        state["port0"] &= ~(1 << bit)
                    else:
                        state["port0"] |= (1 << bit)
                else:
                    if sw.value:
                        state["port1"] &= ~(1 << bit)
                    else:
                        state["port1"] |= (1 << bit)
                sw.value = not sw.value
                i2c_status.value = f"I2C ERROR: {ex}"
            page.update()

        sw.on_change = on_switch_change

        container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text(label),
                    sw,
                ],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=5,
            ),
            padding=10,
            border=ft.border.all(1, ft.colors.GREY_400),
            border_radius=6,
            alignment=ft.alignment.center,
        )
        return container, sw

    grid_rows = []
    for r in range(4):
        row_controls = []
        for c in range(4):
            idx = r * 4 + c + 1
            container, sw = make_switch(idx)
            switches.append(sw)
            row_controls.append(container)
        grid_rows.append(ft.Row(controls=row_controls, alignment=ft.MainAxisAlignment.SPACE_EVENLY))

    page.add(
        ft.Column(
            controls=[
                ft.Text("PCA9539 16-channel Output (P00..P07, P10..P17)"),
                *grid_rows,
                ft.Row(controls=[gpio4_label, gpio15_label], alignment=ft.MainAxisAlignment.START, spacing=20),
                i2c_status,
                ft.Divider(),
                info_text,
            ],
            spacing=12,
        )
    )

    # Hardware thread
    def hw_worker():
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(GPIO_IN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.setup(GPIO_RST_PIN, GPIO.OUT, initial=GPIO.LOW)

            set_gpio_labels(GPIO.input(GPIO_IN_PIN), GPIO.input(GPIO_RST_PIN))
            log("GPIO initialized. Waiting for GPIO4 High to release reset...")

            released = False
            i2c_inited = False
            if GPIO.input(GPIO_IN_PIN) and not released:
                time.sleep(DELAY_AFTER_GPIO4_HIGH_TO_RST_HIGH)
                GPIO.output(GPIO_RST_PIN, GPIO.HIGH)
                set_gpio_labels(gpio15_high=True)
                released = True
                log("GPIO4 already High at start. Reset released (GPIO15 High).")

                time.sleep(DELAY_AFTER_RST_HIGH_TO_I2C)
                try:
                    state["bus"] = SMBus(I2C_BUS_ID)
                except Exception as bex:
                    set_i2c_status(f"I2C open failed: {bex}")
                    log(f"I2C open failed: {bex}")

                if state["bus"] is not None:
                    try:
                        state["pca"] = PCA9539(state["bus"], PCA9539_ADDR, state["lock"])
                        state["pca"].init_safe()
                        state["i2c_ready"] = True
                        i2c_inited = True
                        enable_switches(True)
                        set_i2c_status("I2C initialized: Polarity=0x00/0x00, Output=0x00/0x00, Config=0x00/0x00")
                        log("PCA9539 initialization succeeded.")
                    except Exception as iex:
                        set_i2c_status(f"I2C init failed: {iex}")
                        log(f"PCA9539 init failed: {iex}")

            while not state["stop_event"].is_set():
                cur_gpio4 = GPIO.input(GPIO_IN_PIN)
                cur_gpio15 = GPIO.input(GPIO_RST_PIN)
                set_gpio_labels(gpio4_high=cur_gpio4, gpio15_high=cur_gpio15)

                if (not released) and cur_gpio4:
                    time.sleep(DELAY_AFTER_GPIO4_HIGH_TO_RST_HIGH)
                    GPIO.output(GPIO_RST_PIN, GPIO.HIGH)
                    set_gpio_labels(gpio15_high=True)
                    released = True
                    log("GPIO4 High detected. Reset released (GPIO15 High).")

                if released and (not i2c_inited):
                    time.sleep(DELAY_AFTER_RST_HIGH_TO_I2C)
                    if state["bus"] is None:
                        try:
                            state["bus"] = SMBus(I2C_BUS_ID)
                        except Exception as bex:
                            set_i2c_status(f"I2C open failed: {bex}")
                            log(f"I2C open failed: {bex}")
                    if state["bus"] is not None:
                        try:
                            state["pca"] = PCA9539(state["bus"], PCA9539_ADDR, state["lock"])
                            state["pca"].init_safe()
                            state["i2c_ready"] = True
                            i2c_inited = True
                            enable_switches(True)
                            set_i2c_status("I2C initialized: Polarity=0x00/0x00, Output=0x00/0x00, Config=0x00/0x00")
                            log("PCA9539 initialization succeeded.")
                        except Exception as iex:
                            set_i2c_status(f"I2C init failed: {iex}")
                            log(f"PCA9539 init failed: {iex}")

                time.sleep(0.5)

        except Exception as ex:
            log(f"HW thread error: {ex}")
        finally:
            # Cleanup is handled on page close
            pass

    worker_thread = threading.Thread(target=hw_worker, daemon=True)
    worker_thread.start()

    def on_close(e):
        state["stop_event"].set()
        try:
            worker_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if state["bus"] is not None:
                state["bus"].close()
        except Exception:
            pass
        try:
            GPIO.cleanup()
        except Exception:
            pass

    page.on_close = on_close


if __name__ == "__main__":
    # Flet desktop
    ft.app(target=main)
