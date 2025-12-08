# /src/battery/main_rp.py
# Python 3.11
# Flet 0.28.3 desktop app targeting Raspberry Pi 4B (Trixie)
# - Uses RPi.GPIO for GPIO4/15/27
# - Uses smbus2 for I2C (PCA9539PWR at 0x74 on bus 1)
# - Uses mr793200_controller for SPI (MR793200)
# - Assets directory: src/battery/img with battery.png, light_on.png, light_off.png
# - UI: Upper (Play/Stop), Divider, Middle (2 rows x 8 columns), Divider, Lower (Status)
# - Page.update() only; no Expanded, no colors/icons modules (use Colors/Icons instead)
# - page.run_task receives coroutine function references (no coroutine objects)

import asyncio
import logging
import os
import sys
from typing import Dict, List, Tuple

import flet as ft

# GPIO
import RPi.GPIO as GPIO

# I2C (smbus2)
from smbus2 import SMBus, i2c_msg

# SPI MR793200 controller import (add src/mr793200 to sys.path)
_THIS_DIR = os.path.dirname(__file__)
_PROJECT_SRC_DIR = os.path.abspath(os.path.join(_THIS_DIR, ".."))
_MR793200_DIR = os.path.join(_PROJECT_SRC_DIR, "mr793200")
if _MR793200_DIR not in sys.path:
    sys.path.append(_MR793200_DIR)

from mr793200_controller import mr793200_controller


# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# PCA9539PWR constants
PCA9539_ADDR = 0x74
REG_INPUT_0 = 0x00
REG_INPUT_1 = 0x01
REG_OUTPUT_0 = 0x02
REG_OUTPUT_1 = 0x03
REG_POLARITY_0 = 0x04
REG_POLARITY_1 = 0x05
REG_CONFIG_0 = 0x06
REG_CONFIG_1 = 0x07

# GPIO numbers (BCM)
GPIO_VDET = 4   # input (pull-down)
GPIO_RESET = 15 # output (reset to PCA9539), init Low
GPIO_SPI_EN = 27  # output High when SPI task active (per spec)

# USER MEM addresses to read from MR793200
USER_ADDRS = [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]


def setup_gpio():
    try:
        GPIO.setmode(GPIO.BCM)
        # VDET: input with pull-down
        GPIO.setup(GPIO_VDET, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        # RESET: output, initial Low
        GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
    except Exception as e:
        logging.error(f"GPIO setup failed: {e}")


def gpio_get_vdet() -> int:
    try:
        return GPIO.input(GPIO_VDET)
    except Exception as e:
        logging.error(f"GPIO read (VDET) failed: {e}")
        return 0


def gpio_get_reset() -> int:
    try:
        return GPIO.input(GPIO_RESET)
    except Exception as e:
        logging.error(f"GPIO read (RESET) failed: {e}")
        return 0


def gpio_set_reset(level: int):
    try:
        GPIO.output(GPIO_RESET, GPIO.HIGH if level else GPIO.LOW)
    except Exception as e:
        logging.error(f"GPIO write (RESET) failed: {e}")


def gpio_setup_spi_en():
    try:
        GPIO.setup(GPIO_SPI_EN, GPIO.OUT, initial=GPIO.HIGH)
    except Exception as e:
        logging.error(f"GPIO setup/write (SPI_EN) failed: {e}")


def gpio_cleanup_spi_en():
    try:
        GPIO.cleanup(GPIO_SPI_EN)
    except Exception as e:
        logging.error(f"GPIO cleanup (SPI_EN) failed: {e}")


class PCA9539:
    def __init__(self, bus_id: int = 1, addr: int = PCA9539_ADDR):
        self.bus_id = bus_id
        self.addr = addr
        self.bus: SMBus | None = None
        self.initialized: bool = False

    def open(self):
        if self.bus is None:
            try:
                self.bus = SMBus(self.bus_id)
            except Exception as e:
                logging.error(f"I2C open failed: {e}")
                self.bus = None

    def close(self):
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception as e:
                logging.error(f"I2C close failed: {e}")
            finally:
                self.bus = None
                self.initialized = False

    def _write_byte(self, reg: int, val: int) -> bool:
        try:
            assert self.bus is not None
            self.bus.write_byte_data(self.addr, reg, val & 0xFF)
            return True
        except Exception as e:
            logging.error(f"I2C write failed (reg=0x{reg:02X}, val=0x{val:02X}): {e}")
            return False

    def _read_byte(self, reg: int) -> Tuple[bool, int]:
        try:
            assert self.bus is not None
            val = self.bus.read_byte_data(self.addr, reg)
            return True, val
        except Exception as e:
            logging.error(f"I2C read failed (reg=0x{reg:02X}): {e}")
            return False, 0

    async def initialize(self, retries: int = 3) -> bool:
        # Per spec: Config 0/1 = 0x00 (outputs), Polarity 0/1 = 0x00, Output 0/1 = 0x00
        self.open()
        if self.bus is None:
            return False

        for attempt in range(1, retries + 1):
            ok = True
            ok &= self._write_byte(REG_CONFIG_0, 0x00)
            ok &= self._write_byte(REG_CONFIG_1, 0x00)
            ok &= self._write_byte(REG_POLARITY_0, 0x00)
            ok &= self._write_byte(REG_POLARITY_1, 0x00)
            ok &= self._write_byte(REG_OUTPUT_0, 0x00)
            ok &= self._write_byte(REG_OUTPUT_1, 0x00)
            if not ok:
                logging.info(f"I2C init attempt {attempt} write failure; retrying...")
                await asyncio.sleep(0.1)
                continue

            r0_ok, r0 = self._read_byte(REG_CONFIG_0)
            r1_ok, r1 = self._read_byte(REG_CONFIG_1)
            p0_ok, p0 = self._read_byte(REG_POLARITY_0)
            p1_ok, p1 = self._read_byte(REG_POLARITY_1)
            o0_ok, o0 = self._read_byte(REG_OUTPUT_0)
            o1_ok, o1 = self._read_byte(REG_OUTPUT_1)
            if all([r0_ok, r1_ok, p0_ok, p1_ok, o0_ok, o1_ok]) and (r0 == 0 and r1 == 0 and p0 == 0 and p1 == 0 and o0 == 0 and o1 == 0):
                self.initialized = True
                logging.info("PCA9539 initialized successfully.")
                return True

            logging.info(f"I2C init attempt {attempt} verify mismatch; retrying...")
            await asyncio.sleep(0.1)

        self.initialized = False
        logging.error("PCA9539 initialization failed after retries.")
        return False

    def set_outputs(self, bitmask16: int) -> bool:
        # Map: No.1->P00(Lsb) ... No.8->P07, No.9->P10 ... No.16->P17
        if not self.initialized or self.bus is None:
            return False
        low = bitmask16 & 0xFF      # P00..P07
        high = (bitmask16 >> 8) & 0xFF  # P10..P17
        ok = self._write_byte(REG_OUTPUT_0, low)
        ok &= self._write_byte(REG_OUTPUT_1, high)
        return ok

    def all_off(self):
        if self.bus is None:
            return
        self._write_byte(REG_OUTPUT_0, 0x00)
        self._write_byte(REG_OUTPUT_1, 0x00)


def int_from_hexstr(hexstr: str) -> int:
    # mr793200_controller.read_nvm1 returns lowercase hex string (no "0x")
    try:
        return int(hexstr, 16) & 0xFFFF
    except Exception as e:
        logging.error(f"Parse hex string failed ({hexstr}): {e}")
        return 0


def get_bits(val: int, high: int, low: int) -> int:
    width = high - low + 1
    mask = (1 << width) - 1
    return (val >> low) & mask


def get_bit(val: int, bit_index: int) -> int:
    return (val >> bit_index) & 1


def combine_parts(parts: List[Tuple[int, int, int]], values: Dict[int, int]) -> int:
    """
    parts: list of (addr, high_bit, low_bit), ordered from MSB part to LSB part
    values: {addr: 16-bit value}
    Returns combined integer of total bits (typically 8-bit temperature).
    """
    total_widths = [p[1] - p[2] + 1 for p in parts]
    result = 0
    for i, (addr, high, low) in enumerate(parts):
        part_val = get_bits(values.get(addr, 0), high, low)
        shift = sum(total_widths[i + 1:])  # shift left by width of lower parts
        result |= (part_val << shift)
    return result & 0xFF


def parse_user_memory(values: Dict[int, int]) -> Tuple[int, List[str]]:
    """
    values: dict of {addr: 16-bit word} for 0x22..0x32
    Returns:
      - on_mask: 16-bit bitmask No.1..No.16 (bit0=No.1 ...)
      - temps: list of 16 strings like "53°C" or "-°C"
    """
    on_map = [
        (0x22, 15),  # No.1
        (0x22, 6),   # No.2
        (0x24, 13),  # No.3
        (0x24, 4),   # No.4
        (0x26, 11),  # No.5
        (0x26, 2),   # No.6
        (0x28, 9),   # No.7
        (0x28, 0),   # No.8
        (0x2A, 7),   # No.9
        (0x2C, 14),  # No.10
        (0x2C, 5),   # No.11
        (0x2E, 12),  # No.12
        (0x2E, 3),   # No.13
        (0x30, 10),  # No.14
        (0x30, 1),   # No.15
        (0x32, 8),   # No.16
    ]

    # temp parts per No., ordered MSB->LSB
    temp_parts = [
        [(0x22, 14, 7)],                                 # No.1: 8 bits
        [(0x24, 15, 14), (0x22, 5, 0)],                  # No.2: 2 + 6
        [(0x24, 12, 5)],                                 # No.3: 8 bits
        [(0x26, 15, 12), (0x24, 3, 0)],                  # No.4: 4 + 4
        [(0x26, 10, 3)],                                 # No.5: 8 bits
        [(0x28, 15, 10), (0x26, 1, 0)],                  # No.6: 6 + 2
        [(0x28, 8, 1)],                                  # No.7: 8 bits
        [(0x2A, 15, 8)],                                 # No.8: 8 bits
        [(0x2C, 15, 15), (0x2A, 6, 0)],                  # No.9: 1 + 7
        [(0x2C, 13, 6)],                                 # No.10: 8 bits
        [(0x2E, 15, 13), (0x2C, 4, 0)],                  # No.11: 3 + 5
        [(0x2E, 11, 4)],                                 # No.12: 8 bits
        [(0x30, 15, 11), (0x2E, 2, 0)],                  # No.13: 5 + 3
        [(0x30, 9, 2)],                                  # No.14: 8 bits
        [(0x32, 15, 9), (0x30, 0, 0)],                   # No.15: 7 + 1
        [(0x32, 7, 0)],                                  # No.16: 8 bits
    ]

    on_mask = 0
    temps: List[str] = []
    for i in range(16):
        addr_on, bit_on = on_map[i]
        on_bit = get_bit(values.get(addr_on, 0), bit_on)
        if on_bit:
            on_mask |= (1 << i)

        t_val = combine_parts(temp_parts[i], values)
        temps.append(f"{t_val}°C")

    return on_mask, temps


def build_middle_cells():
    # Returns list of dicts per No. with controls references
    cells = []
    for idx in range(16):
        no_text = ft.Text(f"No. {idx + 1}", weight=ft.FontWeight.BOLD)

        light_img = ft.Image(src="light_off.png", width=180, height=180, fit=ft.ImageFit.CONTAIN)

        temp_text = ft.Text("-°C", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.BLACK, text_align=ft.TextAlign.CENTER)
        battery_img = ft.Image(src="battery.png", width=180, height=180, fit=ft.ImageFit.CONTAIN)
        temp_stack = ft.Stack(
            controls=[battery_img, temp_text],
            alignment=ft.alignment.center,
            width=180,
            height=180,
        )

        inner_col = ft.Column(
            controls=[no_text, light_img, temp_stack],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        border = ft.border.all(1, ft.Colors.GREY_300)
        cont = ft.Container(
            content=inner_col,
            border=border,
            bgcolor=ft.Colors.GREY_50,
            padding=8,
            width=220,
            height=320,
        )

        cells.append({
            "container": cont,
            "no_text": no_text,
            "light_img": light_img,
            "temp_text": temp_text,
        })
    return cells


def build_middle_grid(cells):
    # 2 rows x 8 columns, GridView not permitted
    rows = []
    for r in range(2):
        row_controls = []
        for c in range(8):
            idx = r * 8 + c
            row_controls.append(cells[idx]["container"])
        rows.append(
            ft.Row(controls=row_controls, spacing=8, alignment=ft.MainAxisAlignment.START)
        )
    col = ft.Column(controls=rows, spacing=8)
    return col


def main(page: ft.Page):
    page.title = "Battery Monitor"
    page.window_width = 1920
    page.window_height = 1080
    page.padding = 12
    page.spacing = 8

    # State objects
    setup_gpio()
    i2c = PCA9539(bus_id=1, addr=PCA9539_ADDR)

    spi_stop_event: asyncio.Event | None = None
    spi_task_running = False

    # Upper controls: Play / Stop
    def set_play_state(active: bool):
        # active True: button accepts presses
        play_btn.disabled = not active
        play_btn.opacity = 1.0 if active else 0.4

    def set_stop_state(active: bool):
        stop_btn.disabled = not active
        stop_btn.opacity = 1.0 if active else 0.4

    async def run_spi_task():
        nonlocal spi_stop_event, spi_task_running
        spi_task_running = True
        spi_stop_event = asyncio.Event()

        # GPIO27 High and prepare MR793200 SPI
        try:
            gpio_setup_spi_en()
        except Exception as e:
            logging.error(f"SPI EN setup failed: {e}")

        controller = None
        try:
            try:
                controller = mr793200_controller()  # default 1MHz
            except Exception as e:
                logging.error(f"MR793200 controller init failed: {e}")
                # Even on init failure, ensure cleanup below
                raise

            while not spi_stop_event.is_set():
                # Read USER memory addresses
                vals: Dict[int, int] = {}
                try:
                    for addr in USER_ADDRS:
                        hexstr = controller.read_nvm1(0x04, addr, 1)
                        vals[addr] = int_from_hexstr(hexstr)
                except Exception as e:
                    logging.error(f"SPI read_nvm1 failed: {e}")

                # Parse and update UI + I2C outputs
                try:
                    on_mask, temps = parse_user_memory(vals)

                    # Update middle cells
                    for i in range(16):
                        # Light on/off
                        if (on_mask >> i) & 1:
                            middle_cells[i]["light_img"].src = "light_on.png"
                        else:
                            middle_cells[i]["light_img"].src = "light_off.png"
                        # Temperature
                        middle_cells[i]["temp_text"].value = temps[i]

                    # Update PCA9539 outputs
                    if i2c.initialized:
                        ok = i2c.set_outputs(on_mask)
                        if not ok:
                            logging.error("Failed to set PCA9539 outputs.")
                except Exception as e:
                    logging.error(f"Parsing/UI update failed: {e}")

                page.update()
                await asyncio.sleep(0.5)  # 500ms cycle

        finally:
            # Cleanup per spec: 1) GPIO27.cleanup(), 2) SPI close()
            try:
                gpio_cleanup_spi_en()
            except Exception as e:
                logging.error(f"SPI EN cleanup failed: {e}")
            try:
                if controller is not None and hasattr(controller, "spi") and controller.spi is not None:
                    controller.spi.close()
            except Exception as e:
                logging.error(f"SPI close failed: {e}")
            spi_task_running = False

    def on_play_click(e: ft.ControlEvent):
        # Deactivate Play, Activate Stop, start SPI task
        set_play_state(False)
        set_stop_state(True)
        page.update()
        page.run_task(run_spi_task)

    def reset_middle_to_initial():
        # Set all light off and temp "-°C"
        for i in range(16):
            middle_cells[i]["light_img"].src = "light_off.png"
            middle_cells[i]["temp_text"].value = "-°C"

    def on_stop_click(e: ft.ControlEvent):
        # Deactivate Stop, stop SPI communication gracefully and reset UI + PCA9539 outputs
        set_stop_state(False)

        # Signal stop
        if spi_stop_event is not None and not spi_stop_event.is_set():
            spi_stop_event.set()

        # Reset middle UI and outputs
        reset_middle_to_initial()
        if i2c.initialized:
            i2c.all_off()

        # Reactivate Play
        set_play_state(True)
        page.update()

    play_btn = ft.IconButton(
        icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
        icon_size=36,
        tooltip="Play",
        on_click=on_play_click,
        style=ft.ButtonStyle(color=ft.Colors.GREEN_ACCENT_400),
        disabled=False,
    )

    stop_btn = ft.IconButton(
        icon=ft.Icons.STOP_CIRCLE_ROUNDED,
        icon_size=36,
        tooltip="Stop",
        on_click=on_stop_click,
        style=ft.ButtonStyle(color=ft.Colors.RED_400),
        disabled=True,  # initial Deactivate
        opacity=0.4,
    )

    upper_row = ft.Row(
        controls=[play_btn, stop_btn],
        spacing=12,
        alignment=ft.MainAxisAlignment.START,
    )

    # Middle grid
    middle_cells = build_middle_cells()
    middle_grid = build_middle_grid(middle_cells)

    # Lower status containers
    def status_box(text: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, weight=ft.FontWeight.BOLD),
            bgcolor=ft.Colors.GREY_300,
            padding=8,
            width=180,
            height=40,
        )

    def info_box(initial_text: str = "") -> ft.Container:
        return ft.Container(
            content=ft.Text(initial_text),
            bgcolor=ft.Colors.GREY_50,
            padding=8,
            width=400,
            height=40,
        )

    i2c_status_box = status_box("I2C Status")
    i2c_info_text = ft.Text("Not initialized.")
    i2c_info_box = ft.Container(
        content=i2c_info_text,
        bgcolor=ft.Colors.GREY_50,
        padding=8,
        width=400,
        height=40,
    )

    vdet_box = status_box("VDET")
    vdet_info_text = ft.Text("-")
    vdet_info_box = ft.Container(
        content=vdet_info_text,
        bgcolor=ft.Colors.GREY_50,
        padding=8,
        width=400,
        height=40,
    )

    reset_box = status_box("RESET")
    reset_info_text = ft.Text("-")
    reset_info_box = ft.Container(
        content=reset_info_text,
        bgcolor=ft.Colors.GREY_50,
        padding=8,
        width=400,
        height=40,
    )

    hot_reset_warn_text = ft.Text("", color=ft.Colors.RED)
    hot_reset_btn = ft.ElevatedButton(text="Hot Reset")

    def update_lower_info():
        # VDET
        vdet_val = gpio_get_vdet()
        vdet_info_text.value = "High" if vdet_val == 1 else "Low" if vdet_val == 0 else "-"

        # RESET
        rst_val = gpio_get_reset()
        reset_info_text.value = "High" if rst_val == 1 else "Low" if rst_val == 0 else "-"

        # I2C info
        if vdet_val == 0 and rst_val == 0:
            i2c_info_text.value = "Not initialized."
            i2c_info_text.color = None
        elif vdet_val == 1 and rst_val == 0:
            i2c_info_text.value = "Waiting reset released..."
            i2c_info_text.color = None
        elif vdet_val == 1 and rst_val == 1:
            i2c_info_text.value = "Succeeded."
            i2c_info_text.color = ft.Colors.GREEN
        else:
            i2c_info_text.value = "-"
            i2c_info_text.color = None

    async def init_i2c_sequence_if_ready():
        # Called 100ms after RESET set High (VDET must be High)
        if gpio_get_vdet() == 1 and gpio_get_reset() == 1:
            await i2c.initialize()
        update_lower_info()
        page.update()

    def on_hot_reset_click(e: ft.ControlEvent):
        hot_reset_warn_text.value = ""
        if gpio_get_vdet() == 1:
            try:
                gpio_set_reset(0)
                page.update()
                # 500ms Low then High
                async def do_reset_then_init():
                    await asyncio.sleep(0.5)
                    gpio_set_reset(1)
                    update_lower_info()
                    page.update()
                    # 100ms after High, init I2C
                    await asyncio.sleep(0.1)
                    await init_i2c_sequence_if_ready()

                page.run_task(do_reset_then_init)
            except Exception as ex:
                logging.error(f"Hot reset failed: {ex}")
        else:
            hot_reset_warn_text.value = 'Available when VDET is "High."'
            page.update()

    hot_reset_btn.on_click = on_hot_reset_click

    lower_rows = ft.Column(
        controls=[
            ft.Row(controls=[i2c_status_box, i2c_info_box], spacing=0),
            ft.Row(controls=[vdet_box, vdet_info_box], spacing=0),
            ft.Row(controls=[reset_box, reset_info_box], spacing=0),
            ft.Row(controls=[hot_reset_btn, hot_reset_warn_text], spacing=8),
        ],
        spacing=8,
    )

    # Compose page
    page.add(
        ft.Container(content=upper_row),
        ft.Divider(),
        ft.Container(content=middle_grid),
        ft.Divider(),
        ft.Container(content=lower_rows),
    )
    page.update()

    # GPIO monitor task: polls VDET every 500ms; handles RESET logic per spec
    async def run_gpio_monitor():
        last_vdet = gpio_get_vdet()
        update_lower_info()
        page.update()
        while True:
            vdet = gpio_get_vdet()
            rst = gpio_get_reset()

            # Handle transitions
            if last_vdet == 0 and vdet == 1:
                # After 100ms set RESET High
                await asyncio.sleep(0.1)
                gpio_set_reset(1)
                update_lower_info()
                page.update()
                # After another 100ms start I2C init
                await asyncio.sleep(0.1)
                await init_i2c_sequence_if_ready()
            elif last_vdet == 1 and vdet == 0:
                # Immediately set RESET Low and close I2C
                gpio_set_reset(0)
                i2c.close()
                update_lower_info()
                page.update()

            last_vdet = vdet

            # Refresh info text each cycle
            update_lower_info()
            page.update()
            await asyncio.sleep(0.5)

    page.run_task(run_gpio_monitor)


if __name__ == "__main__":
    try:
        # このファイルの場所: /home/pi/RFID/src/battery/main_rp.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(script_dir, "..", ".."))  # -> /home/pi/RFID
        os.chdir(project_root)
        logging.info(f"Changed working directory to {project_root}")
    except Exception as e:
        logging.error(f"Failed to change working directory: {e}")

    # デスクトップアプリ起動（画像はファイル名のみで参照、assets_dir は相対パス指定）
    ft.app(target=main, assets_dir="src/battery/img", view=ft.AppView.FLET_APP)

