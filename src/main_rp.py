# /src/battery/main_rp.py
# Python 3.11 / Flet 0.28.3
# Raspberry Pi 4B (Trixie)
# Desktop app using Flet, GPIO, I2C(SMBus), SPI (mr793200_controller)
# - GPIO2 (SDA), GPIO3 (SCL) -> PCA9539PWR (I2C addr 0x74)
# - GPIO4: input with pull-down (VDET), polled every 500 ms
# - GPIO15: output (RESET), default Low; set High 100 ms after GPIO4 becomes High; set Low immediately when GPIO4 becomes Low
# - After GPIO15 High, wait 100 ms then start I2C init (write config/polarity/output and read back to verify)
# - "Play" starts periodic (500 ms) SPI reads from MR793200 USER memory via mr793200_controller
# - "Stop" stops SPI task; on stop, always: GPIO.cleanup(27) then SPI close()
# - Middle UI: 2 rows x 8 columns (No.1~16), shows light_on/off and temperature over battery.png (180x180)
# - Lower UI: status panels per spec; Expanded is not used — fixed widths ensure equal sizes

import asyncio
import sys
import time
import pathlib
from typing import List, Tuple

import flet as ft

# Add /src to sys.path so we can import /src/mr793200/mr793200_controller.py
_THIS_FILE = pathlib.Path(__file__).resolve()
_SRC_DIR = _THIS_FILE.parents[1]  # /src
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

# GPIO & I2C
try:
    import RPi.GPIO as GPIO
except Exception as e:
    GPIO = None

try:
    from smbus2 import SMBus, i2c_msg
except Exception as e:
    SMBus = None

# MR793200 SPI controller
try:
    from mr793200.mr793200_controller import mr793200_controller
except Exception as e:
    mr793200_controller = None


# ====== PCA9539PWR I2C helper ======
class PCA9539:
    # Registers
    REG_INPUT_0 = 0x00
    REG_INPUT_1 = 0x01
    REG_OUTPUT_0 = 0x02
    REG_OUTPUT_1 = 0x03
    REG_POLARITY_0 = 0x04
    REG_POLARITY_1 = 0x05
    REG_CONFIG_0 = 0x06
    REG_CONFIG_1 = 0x07

    def __init__(self, bus_num: int = 1, addr: int = 0x74):
        if SMBus is None:
            raise RuntimeError("smbus2 is not available on this system.")
        self.addr = addr
        self.bus = SMBus(bus_num)
        self.initialized = False

    def close(self):
        try:
            if self.bus:
                self.bus.close()
        except Exception:
            pass

    def write_reg(self, reg: int, value: int):
        self.bus.write_byte_data(self.addr, reg, value & 0xFF)

    def read_reg(self, reg: int) -> int:
        return self.bus.read_byte_data(self.addr, reg) & 0xFF

    def init_outputs(self) -> bool:
        # Configuration: 0x00 => all pins output
        # Polarity: 0x00 => non-inverted
        # Output: 0x00 => all Low
        self.write_reg(self.REG_CONFIG_0, 0x00)
        self.write_reg(self.REG_CONFIG_1, 0x00)
        self.write_reg(self.REG_POLARITY_0, 0x00)
        self.write_reg(self.REG_POLARITY_1, 0x00)
        self.write_reg(self.REG_OUTPUT_0, 0x00)
        self.write_reg(self.REG_OUTPUT_1, 0x00)

        # Read back for verification (error-handling strengthened)
        try:
            c0 = self.read_reg(self.REG_CONFIG_0)
            c1 = self.read_reg(self.REG_CONFIG_1)
            p0 = self.read_reg(self.REG_POLARITY_0)
            p1 = self.read_reg(self.REG_POLARITY_1)
            o0 = self.read_reg(self.REG_OUTPUT_0)
            o1 = self.read_reg(self.REG_OUTPUT_1)
            ok = (c0 == 0x00 and c1 == 0x00 and p0 == 0x00 and p1 == 0x00 and o0 == 0x00 and o1 == 0x00)
            self.initialized = ok
            return ok
        except Exception:
            self.initialized = False
            return False

    def write_outputs(self, port0_val: int, port1_val: int) -> Tuple[bool, int, int]:
        # port0_val: bit0->P00 ... bit7->P07; port1_val: bit0->P10 ... bit7->P17
        self.write_reg(self.REG_OUTPUT_0, port0_val & 0xFF)
        self.write_reg(self.REG_OUTPUT_1, port1_val & 0xFF)
        # Optional read-back
        r0 = self.read_reg(self.REG_OUTPUT_0)
        r1 = self.read_reg(self.REG_OUTPUT_1)
        return ((r0 == (port0_val & 0xFF) and r1 == (port1_val & 0xFF)), r0, r1)


# ====== MR793200 parsing helpers ======
def parse_user_words(words: List[int]) -> List[Tuple[int, int]]:
    """
    words: 9 words [0]=0x22, [1]=0x24, ... [8]=0x32
    Returns list of 16 tuples: (on_off, temp) for No.1..No.16
    on_off: 0 or 1
    temp: integer in decimal
    """
    w = words  # alias
    res = []

    # No.1
    res.append(((w[0] >> 15) & 0x1, (w[0] >> 7) & 0xFF))
    # No.2
    res.append(((w[0] >> 6) & 0x1, ((w[0] & 0x3F) << 2) | ((w[1] >> 14) & 0x3)))
    # No.3
    res.append(((w[1] >> 13) & 0x1, (w[1] >> 5) & 0xFF))
    # No.4
    res.append(((w[1] >> 4) & 0x1, ((w[1] & 0xF) << 4) | ((w[2] >> 12) & 0xF)))
    # No.5
    res.append(((w[2] >> 11) & 0x1, (w[2] >> 3) & 0xFF))
    # No.6
    res.append(((w[2] >> 2) & 0x1, ((w[2] & 0x3) << 6) | ((w[3] >> 10) & 0x3F)))
    # No.7
    res.append(((w[3] >> 9) & 0x1, (w[3] >> 1) & 0xFF))
    # No.8
    res.append(((w[3] >> 0) & 0x1, (w[4] >> 8) & 0xFF))
    # No.9
    res.append(((w[4] >> 7) & 0x1, ((w[4] & 0x7F) << 1) | ((w[5] >> 15) & 0x1)))
    # No.10
    res.append(((w[5] >> 14) & 0x1, (w[5] >> 6) & 0xFF))
    # No.11
    res.append(((w[5] >> 5) & 0x1, ((w[5] & 0x1F) << 3) | ((w[6] >> 13) & 0x7)))
    # No.12
    res.append(((w[6] >> 12) & 0x1, (w[6] >> 4) & 0xFF))
    # No.13
    res.append(((w[6] >> 3) & 0x1, ((w[6] & 0x7) << 5) | ((w[7] >> 11) & 0x1F)))
    # No.14
    res.append(((w[7] >> 10) & 0x1, (w[7] >> 2) & 0xFF))
    # No.15
    res.append(((w[7] >> 1) & 0x1, ((w[7] & 0x1) << 7) | ((w[8] >> 9) & 0x7F)))
    # No.16
    res.append(((w[8] >> 8) & 0x1, (w[8] & 0xFF)))

    return res


# ====== SPI reader task ======
class SPIWorker:
    def __init__(self, page: ft.Page, update_ui_callback, pca_getter):
        """
        update_ui_callback: callable(states) where states is list of dicts per No.X: {"on": int, "temp": int}
        pca_getter: callable() -> PCA9539 or None
        """
        self.page = page
        self.update_ui = update_ui_callback
        self.pca_getter = pca_getter
        self._task = None
        self._stop_event = asyncio.Event()
        self.ctrl = None

    async def start(self):
        self._stop_event.clear
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop_event.set()
        if self._task:
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _run(self):
        ctrl = None
        try:
            # Ensure GPIO27 is output High before SPI
            if GPIO is None:
                raise RuntimeError("RPi.GPIO is not available.")
            GPIO.setup(27, GPIO.OUT, initial=GPIO.HIGH)

            # Create controller (default 1 MHz)
            if mr793200_controller is None:
                raise RuntimeError("mr793200_controller is not importable.")
            ctrl = mr793200_controller()
            self.ctrl = ctrl

            # Addresses to read: 0x22..0x32 step 0x02
            addrs = [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]

            while not self._stop_event.is_set():
                words = []
                for a in addrs:
                    # read_nvm1 returns hex string like "ABCD" for 16-bit word
                    hex_str = ctrl.read_nvm1(0x04, a, 1)
                    try:
                        val = int(hex_str, 16) & 0xFFFF
                    except Exception:
                        val = 0
                    words.append(val)

                parsed = parse_user_words(words)
                # Update GUI
                states = [{"on": on, "temp": temp} for (on, temp) in parsed]
                await self.update_ui(states)

                # Drive PCA9539 outputs according to On/Off states if available
                pca = self.pca_getter()
                if pca is not None and pca.initialized:
                    port0 = 0
                    for i in range(8):
                        if states[i]["on"]:
                            port0 |= (1 << i)  # No.1->bit0 ... No.8->bit7
                    port1 = 0
                    for i in range(8, 16):
                        if states[i]["on"]:
                            port1 |= (1 << (i - 8))  # No.9->bit0 ... No.16->bit7
                    try:
                        pca.write_outputs(port0, port1)
                    except Exception:
                        pass

                await asyncio.sleep(0.5)  # 500 ms interval

        except Exception as e:
            print(f"[SPIWorker] Exception: {e}")
        finally:
            # SPI end processing: (1) GPIO27 cleanup, (2) SPI close()
            try:
                if GPIO is not None:
                    GPIO.cleanup(27)
            except Exception:
                pass
            try:
                if self.ctrl and getattr(self.ctrl, "spi", None) is not None:
                    self.ctrl.spi.close()
            except Exception:
                pass
            self.ctrl = None


async def main(page: ft.Page):
    page.title = "Battery Monitor"
    page.window_width = 1280
    page.window_height = 800
    page.window_resizable = True
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = ft.Colors.WHITE

    # State vars
    vdet_state = {"value": None}        # GPIO4
    reset_state = {"value": None}       # GPIO15
    i2c_initialized = {"value": False}
    pca_ref = {"obj": None}
    pending_reset_task = {"task": None}

    # Init GPIOs
    if GPIO is None:
        raise RuntimeError("RPi.GPIO not available. Install RPi.GPIO and run on Raspberry Pi.")
    GPIO.setwarnings(False)  # suppress "channel already in use" warnings
    GPIO.setmode(GPIO.BCM)
    # GPIO4 input with pulldown
    GPIO.setup(4, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
    # GPIO15 output low
    GPIO.setup(15, GPIO.OUT, initial=GPIO.LOW)

    # ===== UI Components =====
    # Upper: Play / Stop buttons
    play_btn = ft.IconButton(
        icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
        icon_color=ft.Colors.GREEN_ACCENT_400,
        icon_size=42,
        tooltip="Play",
        disabled=False,
    )
    stop_btn = ft.IconButton(
        icon=ft.Icons.STOP_CIRCLE_ROUNDED,
        icon_color=ft.Colors.RED_400,
        icon_size=42,
        tooltip="Stop",
        disabled=True,
    )

    upper_row = ft.Row(
        controls=[play_btn, stop_btn],
        alignment=ft.MainAxisAlignment.START,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Middle: grid 2 rows x 8 columns without GridView
    tile_border = ft.border.all(1, ft.Colors.GREY_300)

    def make_tile(no: int):
        # Light image (default off)
        light_img = ft.Image(
            src="light_off.png",
            width=48,
            height=48,
            fit=ft.ImageFit.CONTAIN,
        )
        # Battery stack with temperature overlay
        batt_img = ft.Image(
            src="battery.png",
            width=180,
            height=180,
            fit=ft.ImageFit.CONTAIN,
        )
        temp_text = ft.Text(
            value="- °C",
            color=ft.Colors.BLACK,
            size=18,
            weight=ft.FontWeight.W_600,
            text_align=ft.TextAlign.CENTER,
        )
        batt_stack = ft.Stack(
            controls=[batt_img, temp_text],
            width=180,
            height=180,
            alignment=ft.alignment.center,
        )
        title = ft.Text(f"No. {no}", weight=ft.FontWeight.W_600)
        col = ft.Column(
            controls=[title, light_img, batt_stack],
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
        cont = ft.Container(
            content=col,
            bgcolor=ft.Colors.GREY_50,
            border=tile_border,
            padding=8,
            alignment=ft.alignment.top_center,
            width=150,
        )
        return cont, light_img, temp_text

    tiles = []
    lights = []
    temps = []
    for i in range(1, 17):
        c, li, tt = make_tile(i)
        tiles.append(c)
        lights.append(li)
        temps.append(tt)

    row1 = ft.Row(controls=tiles[0:8], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
    row2 = ft.Row(controls=tiles[8:16], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    # Lower: Status sections (no Expanded)
    # Fixed widths/heights to ensure equal sizes across rows.
    label_w = 180
    info_w = 900
    box_h = 48

    label_style = dict(bgcolor=ft.Colors.GREY_300, padding=8, border=tile_border, width=label_w, height=box_h)
    info_style = dict(bgcolor=ft.Colors.GREY_50, padding=8, border=tile_border, width=info_w, height=box_h)

    i2c_status_label = ft.Container(content=ft.Text("I2C Status", weight=ft.FontWeight.W_600), **label_style)
    i2c_info_text = ft.Text("Not initialized.")
    i2c_info_cont = ft.Container(content=i2c_info_text, **info_style)

    vdet_label = ft.Container(content=ft.Text("VDET", weight=ft.FontWeight.W_600), **label_style)
    vdet_info_text = ft.Text("-")
    vdet_info_cont = ft.Container(content=vdet_info_text, **info_style)

    reset_label = ft.Container(content=ft.Text("RESET", weight=ft.FontWeight.W_600), **label_style)
    reset_info_text = ft.Text("-")
    reset_info_cont = ft.Container(content=reset_info_text, **info_style)

    hot_reset_msg = ft.Text("", color=ft.Colors.RED)
    hot_reset_btn = ft.ElevatedButton(text="Hot Reset")

    def make_status_row(left: ft.Control, right: ft.Control):
        # No Expanded used; left/right containers already have fixed width/height.
        return ft.Row(
            controls=[left, right],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )

    lower_col = ft.Column(
        controls=[
            make_status_row(i2c_status_label, i2c_info_cont),
            make_status_row(vdet_label, vdet_info_cont),
            make_status_row(reset_label, reset_info_cont),
            ft.Row(controls=[hot_reset_btn, hot_reset_msg], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        spacing=10,
    )

    # Layout
    page.add(
        ft.Container(content=upper_row, padding=10),
        ft.Divider(height=1, color=ft.Colors.GREY_300),
        ft.Container(content=ft.Column([row1, row2], spacing=10), padding=10),
        ft.Divider(height=1, color=ft.Colors.GREY_300),
        ft.Container(content=lower_col, padding=10),
    )

    # ===== UI update helpers =====
    async def update_middle(states: List[dict]):
        # states: list[{"on": 0/1, "temp": int}]
        for idx, st in enumerate(states):
            lights[idx].src = "light_on.png" if st["on"] else "light_off.png"
            temps[idx].value = f"{st['temp']} °C"
        page.update()

    def update_lower_texts():
        v = vdet_state["value"]
        r = reset_state["value"]

        # VDET info
        if v is None:
            vdet_info_text.value = "-"
        else:
            vdet_info_text.value = "High" if v else "Low"

        # RESET info
        if r is None:
            reset_info_text.value = "-"
        else:
            reset_info_text.value = "High" if r else "Low"

        # I2C info
        if v is False and r is False:
            i2c_info_text.value = "Not initialized."
            i2c_info_text.color = ft.Colors.BLACK
        elif v is True and (r is False or r is None):
            i2c_info_text.value = "Waiting reset released..."
            i2c_info_text.color = ft.Colors.BLACK
        elif v is True and r is True:
            i2c_info_text.value = "Succeded."
            i2c_info_text.color = ft.Colors.GREEN
        else:
            i2c_info_text.value = "-"
            i2c_info_text.color = ft.Colors.BLACK

    # ===== PCA getter for SPI worker =====
    def get_pca():
        return pca_ref["obj"]

    spi_worker = SPIWorker(page, update_middle, get_pca)

    # ===== I2C init routine =====
    async def init_i2c_if_ready():
        # Called 100ms after RESET High
        try:
            if vdet_state["value"] and reset_state["value"]:
                # Open PCA if not exists
                if pca_ref["obj"] is None:
                    pca_ref["obj"] = PCA9539(bus_num=1, addr=0x74)
                ok = pca_ref["obj"].init_outputs()
                i2c_initialized["value"] = ok
            else:
                i2c_initialized["value"] = False
        except Exception as e:
            print(f"[I2C init] Exception: {e}")
            i2c_initialized["value"] = False
        finally:
            update_lower_texts()
            page.update()

    # ===== GPIO monitoring and control (VDET/RESET) =====
    async def monitor_gpio_task():
        while True:
            try:
                v_now = bool(GPIO.input(4))
            except Exception:
                v_now = False

            if vdet_state["value"] != v_now:
                vdet_state["value"] = v_now

                if v_now:
                    # VDET High: schedule RESET High after 100 ms
                    if not reset_state["value"]:
                        # Cancel previous pending
                        if pending_reset_task["task"] is not None:
                            pending_reset_task["task"].cancel()
                            pending_reset_task["task"] = None

                        async def do_reset_and_i2c():
                            try:
                                await asyncio.sleep(0.1)
                                GPIO.output(15, GPIO.HIGH)
                                reset_state["value"] = True
                                update_lower_texts()
                                page.update()
                                # After 100 ms, start I2C init
                                await asyncio.sleep(0.1)
                                await init_i2c_if_ready()
                            except asyncio.CancelledError:
                                return
                            except Exception as e:
                                print(f"[RESET/I2C sched] Exception: {e}")

                        pending_reset_task["task"] = asyncio.create_task(do_reset_and_i2c())
                else:
                    # VDET Low: immediately drive RESET Low
                    if pending_reset_task["task"] is not None:
                        pending_reset_task["task"].cancel()
                        pending_reset_task["task"] = None
                    try:
                        GPIO.output(15, GPIO.LOW)
                    except Exception:
                        pass
                    reset_state["value"] = False
                    i2c_initialized["value"] = False

                update_lower_texts()
                page.update()

            await asyncio.sleep(0.5)  # 500 ms polling

    # ===== Hot Reset handler =====
    async def hot_reset_clicked(e):
        hot_reset_msg.value = ""
        page.update()

        v = vdet_state["value"]
        if v:
            # Pulse RESET Low for 500 ms then High; re-init I2C after 100 ms
            try:
                GPIO.output(15, GPIO.LOW)
                reset_state["value"] = False
                update_lower_texts()
                page.update()

                await asyncio.sleep(0.5)

                GPIO.output(15, GPIO.HIGH)
                reset_state["value"] = True
                update_lower_texts()
                page.update()

                await asyncio.sleep(0.1)
                await init_i2c_if_ready()
            except Exception as ex:
                print(f"[Hot Reset] Exception: {ex}")
        else:
            # VDET Low -> show message, keep RESET Low
            try:
                GPIO.output(15, GPIO.LOW)
            except Exception:
                pass
            reset_state["value"] = False
            hot_reset_msg.value = 'Available when VDET is "High."'
            update_lower_texts()
            page.update()

    hot_reset_btn.on_click = lambda e: page.run_task(hot_reset_clicked, e)

    # ===== Play / Stop handlers =====
    async def on_play(e):
        play_btn.disabled = True
        stop_btn.disabled = False
        page.update()
        await spi_worker.start()

    async def on_stop(e):
        stop_btn.disabled = True
        page.update()
        await spi_worker.stop()
        play_btn.disabled = False
        page.update()

    play_btn.on_click = lambda e: page.run_task(on_play, e)
    stop_btn.on_click = lambda e: page.run_task(on_stop, e)

    # Initialize lower texts with current states
    try:
        vdet_state["value"] = bool(GPIO.input(4))
    except Exception:
        vdet_state["value"] = False
    try:
        reset_state["value"] = bool(GPIO.input(15))
    except Exception:
        reset_state["value"] = False
    update_lower_texts()
    page.update()

    # Start GPIO monitor (pass coroutine function, not coroutine object)
    page.run_task(monitor_gpio_task)


if __name__ == "__main__":
    # assets_dir points to /src/battery/img relative to project root
    ft.app(target=main, view=ft.AppView.FLET_APP, assets_dir="src/battery/img")
