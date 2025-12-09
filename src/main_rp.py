# /src/battery/main_rp.py
# Python 3.11, Flet 0.28.3
# Desktop app for Raspberry Pi 4B (Trixie)
# - SPI: MR793200 (ROHM) via mr793200_controller
# - I2C: PCA9539PWR (TI) via smbus2 on /dev/i2c-1 (addr 0x74)
# - GPIO: RPi.GPIO for GPIO4 (input, PUD_DOWN), GPIO15 (output reset), GPIO27 (SPI enable)
# - Images: base64-loaded PNGs (no assets_dir). Display scaled via Image width/height.
# - Robust error handling with try/except and terminal logs.
# - Flet: use Colors and Icons; update UI with page.update(); run tasks with page.run_task.
import asyncio
import base64
import sys
import time
from pathlib import Path

import flet as ft
import RPi.GPIO as GPIO
from smbus2 import SMBus

# Import mr793200_controller from /src/mr793200/
try:
    THIS_FILE = Path(__file__).resolve()
    SRC_DIR = THIS_FILE.parent.parent  # /src
    MR_DIR = SRC_DIR / "mr793200"
    sys.path.insert(0, str(MR_DIR))
    from mr793200_controller import mr793200_controller
except Exception as e:
    print(f"[IMPORT ERROR] Failed to import mr793200_controller: {e}")
    raise


# --- Constants ---
I2C_BUS_NUM = 1              # /dev/i2c-1
PCA9539_ADDR = 0x74
# PCA9539 Registers
REG_IN0 = 0x00
REG_IN1 = 0x01
REG_OUT0 = 0x02
REG_OUT1 = 0x03
REG_POL0 = 0x04
REG_POL1 = 0x05
REG_CFG0 = 0x06
REG_CFG1 = 0x07

GPIO.setmode(GPIO.BCM)
GPIO4 = 4   # VDET input (PUD_DOWN)
GPIO15 = 15 # RESET output
GPIO27 = 27 # MR793200 control (set High to enable SPI read)

# --- Helpers ---
def load_image_base64(path: Path) -> str:
    try:
        with path.open("rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        print(f"[IMAGE LOAD ERROR] {path}: {e}")
        return ""


def get_bit(val: int, bit: int) -> int:
    return (val >> bit) & 0x1


def get_bits(val: int, high: int, low: int) -> int:
    width = high - low + 1
    mask = (1 << width) - 1
    return (val >> low) & mask


class I2CManager:
    def __init__(self):
        self.bus: SMBus | None = None
        self.initialized: bool = False

    def open(self):
        if self.bus is None:
            try:
                self.bus = SMBus(I2C_BUS_NUM)
                print("[I2C] Bus opened.")
            except Exception as e:
                print(f"[I2C OPEN ERROR] {e}")
                self.bus = None

    def close(self):
        try:
            if self.bus is not None:
                self.bus.close()
                print("[I2C] Bus closed.")
        except Exception as e:
            print(f"[I2C CLOSE ERROR] {e}")
        finally:
            self.bus = None
            self.initialized = False

    def init_pca9539(self) -> bool:
        # Initialize PCA9539: CFG=0x00 (outputs), POL=0x00 (normal), OUT=0x00 (all Low)
        self.open()
        if self.bus is None:
            print("[I2C INIT ERROR] Bus not available.")
            self.initialized = False
            return False
        ok = True
        try:
            self.bus.write_byte_data(PCA9539_ADDR, REG_CFG0, 0x00)
            self.bus.write_byte_data(PCA9539_ADDR, REG_CFG1, 0x00)
            self.bus.write_byte_data(PCA9539_ADDR, REG_POL0, 0x00)
            self.bus.write_byte_data(PCA9539_ADDR, REG_POL1, 0x00)
            self.bus.write_byte_data(PCA9539_ADDR, REG_OUT0, 0x00)
            self.bus.write_byte_data(PCA9539_ADDR, REG_OUT1, 0x00)

            # Read-back verification
            cfg0 = self.bus.read_byte_data(PCA9539_ADDR, REG_CFG0)
            cfg1 = self.bus.read_byte_data(PCA9539_ADDR, REG_CFG1)
            pol0 = self.bus.read_byte_data(PCA9539_ADDR, REG_POL0)
            pol1 = self.bus.read_byte_data(PCA9539_ADDR, REG_POL1)
            out0 = self.bus.read_byte_data(PCA9539_ADDR, REG_OUT0)
            out1 = self.bus.read_byte_data(PCA9539_ADDR, REG_OUT1)

            if (cfg0, cfg1, pol0, pol1, out0, out1) != (0x00, 0x00, 0x00, 0x00, 0x00, 0x00):
                ok = False
                print(f"[I2C INIT VERIFY ERROR] Read-back mismatch: "
                      f"CFG0={cfg0:#04x}, CFG1={cfg1:#04x}, POL0={pol0:#04x}, POL1={pol1:#04x}, OUT0={out0:#04x}, OUT1={out1:#04x}")
            else:
                print("[I2C INIT] PCA9539 configured successfully.")
        except Exception as e:
            ok = False
            print(f"[I2C INIT ERROR] {e}")

        self.initialized = ok
        return ok

    def write_outputs(self, port0_val: int, port1_val: int):
        if self.bus is None or not self.initialized:
            print("[I2C WRITE] Bus not initialized.")
            return
        try:
            self.bus.write_byte_data(PCA9539_ADDR, REG_OUT0, port0_val & 0xFF)
            self.bus.write_byte_data(PCA9539_ADDR, REG_OUT1, port1_val & 0xFF)
        except Exception as e:
            print(f"[I2C WRITE ERROR] {e}")


class AppState:
    def __init__(self):
        # GPIO runtime state
        self.vdet_high: bool | None = None  # None unknown, True high, False low
        self.reset_high: bool | None = None

        # Image base64
        base_dir = Path(__file__).resolve().parent / "img"
        self.img_light_on_b64 = load_image_base64(base_dir / "light_on.png")
        self.img_light_off_b64 = load_image_base64(base_dir / "light_off.png")
        self.img_battery_b64 = load_image_base64(base_dir / "battery.png")

        # I2C
        self.i2c = I2CManager()

        # SPI
        self.spi_ctrl: mr793200_controller | None = None
        self.spi_running: bool = False
        self.spi_stop_requested: bool = False

        # Tasks control
        self.vdet_task_running: bool = True

        # UI references
        self.play_btn: ft.IconButton | None = None
        self.stop_btn: ft.IconButton | None = None
        self.no_tiles: list[dict] = []  # [{container, label, light_img, batt_stack, temp_text}]
        self.i2c_info_text: ft.Text | None = None
        self.vdet_info_text: ft.Text | None = None
        self.reset_info_text: ft.Text | None = None
        self.hot_reset_warn: ft.Text | None = None


def setup_gpio_initial():
    # GPIO4: input with internal pull-down; GPIO15: output Low; GPIO27 not set yet.
    try:
        GPIO.setup(GPIO4, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        print("[GPIO] GPIO4 set as INPUT with PUD_DOWN.")
    except Exception as e:
        print(f"[GPIO SETUP ERROR] GPIO4: {e}")
    try:
        GPIO.setup(GPIO15, GPIO.OUT, initial=GPIO.LOW)
        print("[GPIO] GPIO15 set as OUTPUT, initial LOW.")
    except Exception as e:
        print(f"[GPIO SETUP ERROR] GPIO15: {e}")


def gpio_cleanup_on_exit():
    # I2C close and GPIO cleanup for GPIO4 and GPIO15 at app exit
    try:
        GPIO.cleanup(GPIO4)
        print("[GPIO] GPIO4 cleaned up.")
    except Exception as e:
        print(f"[GPIO CLEANUP ERROR] GPIO4: {e}")
    try:
        GPIO.cleanup(GPIO15)
        print("[GPIO] GPIO15 cleaned up.")
    except Exception as e:
        print(f"[GPIO CLEANUP ERROR] GPIO15: {e}")


def build_no_tile(state: AppState, idx: int) -> ft.Container:
    # idx: 1..16
    title = ft.Text(f"No. {idx}", weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK)
    light_img = ft.Image(
        src_base64=state.img_light_off_b64,
        width=180,
        height=180,
        fit=ft.ImageFit.CONTAIN,
    )
    temp_text = ft.Text("-°C", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLACK)
    batt_img = ft.Image(
        src_base64=state.img_battery_b64,
        width=180,
        height=180,
        fit=ft.ImageFit.CONTAIN,
    )
    batt_stack = ft.Stack(
        controls=[batt_img, temp_text],
        alignment=ft.alignment.center,
        width=180,
        height=180,
    )
    content = ft.Column(controls=[title, light_img, batt_stack], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER)
    tile = ft.Container(
        content=content,
        border=ft.border.all(1, ft.Colors.GREY_300),
        bgcolor=ft.Colors.GREY_50,
        padding=6,
    )
    state.no_tiles.append({
        "container": tile,
        "label": title,
        "light_img": light_img,
        "batt_stack": batt_stack,
        "temp_text": temp_text,
    })
    return tile


def compute_outputs_from_on_flags(on_flags: list[bool]) -> tuple[int, int]:
    # Map No.1..8 -> P00..P07; No.9..16 -> P10..P17
    port0 = 0
    port1 = 0
    for i in range(8):
        if on_flags[i]:
            port0 |= (1 << i)  # P0i
    for i in range(8):
        if on_flags[8 + i]:
            port1 |= (1 << i)  # P1i
    return port0, port1


def parse_user_memory(values: dict[int, int]) -> tuple[list[bool], list[int | None]]:
    # Returns on_flags[16], temps[16]
    # values keys: 0x22,0x24,0x26,0x28,0x2A,0x2C,0x2E,0x30,0x32 -> int
    # If any required value missing, corresponding temp or on flag defaults to False/None.
    def val(addr: int) -> int | None:
        return values.get(addr, None)

    A22, A24, A26, A28, A2A, A2C, A2E, A30, A32 = (
        val(0x22), val(0x24), val(0x26), val(0x28), val(0x2A), val(0x2C), val(0x2E), val(0x30), val(0x32)
    )
    on = [False] * 16
    t = [None] * 16

    # No.1
    if A22 is not None:
        on[0] = bool(get_bit(A22, 15))
        t[0] = get_bits(A22, 14, 7)
    # No.2
    if A22 is not None and A24 is not None:
        on[1] = bool(get_bit(A22, 6))
        t[1] = (get_bits(A22, 5, 0) << 2) | get_bits(A24, 15, 14)
    # No.3
    if A24 is not None:
        on[2] = bool(get_bit(A24, 13))
        t[2] = get_bits(A24, 12, 5)
    # No.4
    if A24 is not None and A26 is not None:
        on[3] = bool(get_bit(A24, 4))
        t[3] = (get_bits(A24, 3, 0) << 4) | get_bits(A26, 15, 12)
    # No.5
    if A26 is not None:
        on[4] = bool(get_bit(A26, 11))
        t[4] = get_bits(A26, 10, 3)
    # No.6
    if A26 is not None and A28 is not None:
        on[5] = bool(get_bit(A26, 2))
        t[5] = (get_bits(A26, 1, 0) << 6) | get_bits(A28, 15, 10)
    # No.7
    if A28 is not None:
        on[6] = bool(get_bit(A28, 9))
        t[6] = get_bits(A28, 8, 1)
    # No.8
    if A28 is not None and A2A is not None:
        on[7] = bool(get_bit(A28, 0))
        t[7] = get_bits(A2A, 15, 8)
    # No.9
    if A2A is not None and A2C is not None:
        on[8] = bool(get_bit(A2A, 7))
        t[8] = (get_bits(A2A, 6, 0) << 1) | get_bits(A2C, 15, 15)
    # No.10
    if A2C is not None:
        on[9] = bool(get_bit(A2C, 14))
        t[9] = get_bits(A2C, 13, 6)
    # No.11
    if A2C is not None and A2E is not None:
        on[10] = bool(get_bit(A2C, 5))
        t[10] = (get_bits(A2C, 4, 0) << 3) | get_bits(A2E, 15, 13)
    # No.12
    if A2E is not None:
        on[11] = bool(get_bit(A2E, 12))
        t[11] = get_bits(A2E, 11, 4)
    # No.13
    if A2E is not None and A30 is not None:
        on[12] = bool(get_bit(A2E, 3))
        t[12] = (get_bits(A2E, 2, 0) << 5) | get_bits(A30, 15, 11)
    # No.14
    if A30 is not None:
        on[13] = bool(get_bit(A30, 10))
        t[13] = get_bits(A30, 9, 2)
    # No.15
    if A30 is not None and A32 is not None:
        on[14] = bool(get_bit(A30, 1))
        t[14] = (get_bits(A30, 0, 0) << 7) | get_bits(A32, 15, 9)
    # No.16
    if A32 is not None:
        on[15] = bool(get_bit(A32, 8))
        t[15] = get_bits(A32, 7, 0)

    return on, t


async def vdet_polling_task(page: ft.Page, state: AppState):
    # Poll GPIO4 every 500ms; manage GPIO15 according to spec and I2C init sequencing
    prev_vdet = None
    while state.vdet_task_running:
        vdet = None
        try:
            vdet = GPIO.input(GPIO4)
            state.vdet_high = bool(vdet)
        except Exception as e:
            print(f"[GPIO READ ERROR] GPIO4: {e}")
            state.vdet_high = None

        # Update VDET info text
        if state.vdet_info_text:
            if state.vdet_high is True:
                state.vdet_info_text.value = "High"
            elif state.vdet_high is False:
                state.vdet_info_text.value = "Low"
            else:
                state.vdet_info_text.value = "-"

        # Manage RESET (GPIO15)
        try:
            if state.vdet_high is True:
                # If VDET is High and RESET is Low, release reset after 100ms
                if state.reset_high is not True:
                    await asyncio.sleep(0.1)
                    GPIO.output(GPIO15, GPIO.HIGH)
                    state.reset_high = True
                    # After 100ms of High, start I2C init
                    await asyncio.sleep(0.1)
                    ok = state.i2c.init_pca9539()
                    if state.i2c_info_text:
                        if ok:
                            state.i2c_info_text.value = "Succeeded."
                            state.i2c_info_text.color = ft.Colors.GREEN
                        else:
                            state.i2c_info_text.value = "Waiting reset released..."
                            state.i2c_info_text.color = ft.Colors.BLACK
                else:
                    # Already High; show status according to initialization
                    if state.i2c_info_text:
                        if state.i2c.initialized:
                            state.i2c_info_text.value = "Succeeded."
                            state.i2c_info_text.color = ft.Colors.GREEN
                        else:
                            state.i2c_info_text.value = "Waiting reset released..."
                            state.i2c_info_text.color = ft.Colors.BLACK
            elif state.vdet_high is False:
                # Immediate RESET Low
                GPIO.output(GPIO15, GPIO.LOW)
                state.reset_high = False
                state.i2c.initialized = False
                if state.i2c_info_text:
                    state.i2c_info_text.value = "Not initialized."
                    state.i2c_info_text.color = ft.Colors.BLACK
            else:
                # Unknown state
                if state.i2c_info_text:
                    state.i2c_info_text.value = "Not initialized."
                    state.i2c_info_text.color = ft.Colors.BLACK
        except Exception as e:
            print(f"[GPIO WRITE ERROR] GPIO15: {e}")

        # Update RESET info text
        if state.reset_info_text:
            if state.reset_high is True:
                state.reset_info_text.value = "High"
            elif state.reset_high is False:
                state.reset_info_text.value = "Low"
            else:
                state.reset_info_text.value = "-"

        page.update()
        await asyncio.sleep(0.5)


async def spi_reader_task(page: ft.Page, state: AppState):
    # SPI loop: every 500ms read USER memory and update UI + I2C outputs
    try:
        # Prepare GPIO27 and SPI controller
        try:
            GPIO.setup(GPIO27, GPIO.OUT, initial=GPIO.HIGH)
            print("[GPIO] GPIO27 set as OUTPUT, set HIGH.")
        except Exception as e:
            print(f"[GPIO SETUP ERROR] GPIO27: {e}")

        try:
            state.spi_ctrl = mr793200_controller(sclk_frequency=1_000_000)
        except Exception as e:
            print(f"[SPI INIT ERROR] {e}")
            state.spi_ctrl = None

        state.spi_running = True
        state.spi_stop_requested = False

        # Loop
        while state.spi_running and not state.spi_stop_requested:
            # Read addresses: 0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32
            values: dict[int, int] = {}
            if state.spi_ctrl is None:
                print("[SPI] Controller not available.")
            else:
                for addr in [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]:
                    try:
                        hexstr = state.spi_ctrl.read_nvm1(0x04, addr, 1)  # returns hex string e.g., "9a8a"
                        val = int(hexstr, 16)
                        values[addr] = val
                    except Exception as e:
                        print(f"[SPI READ ERROR] addr=0x{addr:02X}: {e}")
                        values[addr] = None

            # Parse results
            on_flags, temps = parse_user_memory(values)

            # Update UI tiles
            for i in range(16):
                tile = state.no_tiles[i]
                light_img: ft.Image = tile["light_img"]
                temp_text: ft.Text = tile["temp_text"]
                if on_flags[i]:
                    light_img.src_base64 = state.img_light_on_b64
                else:
                    light_img.src_base64 = state.img_light_off_b64
                if temps[i] is not None:
                    temp_text.value = f"{temps[i]}°C"
                else:
                    temp_text.value = "-°C"

            # I2C outputs update (mirror No.X On/Off)
            try:
                if state.i2c.initialized:
                    port0, port1 = compute_outputs_from_on_flags(on_flags)
                    state.i2c.write_outputs(port0, port1)
            except Exception as e:
                print(f"[I2C MIRROR ERROR] {e}")

            page.update()
            await asyncio.sleep(0.5)

    except Exception as e:
        print(f"[SPI TASK ERROR] {e}")
    finally:
        # SPI end processing even on exception:
        try:
            GPIO.cleanup(GPIO27)
            print("[GPIO] GPIO27 cleaned up.")
        except Exception as e:
            print(f"[GPIO CLEANUP ERROR] GPIO27: {e}")
        try:
            if state.spi_ctrl and hasattr(state.spi_ctrl, "spi") and state.spi_ctrl.spi:
                state.spi_ctrl.spi.close()
                print("[SPI] Port closed.")
        except Exception as e:
            print(f"[SPI CLOSE ERROR] {e}")
        state.spi_running = False
        state.spi_ctrl = None


def main(page: ft.Page):
    page.title = "Battery Monitor (Raspberry Pi 4B)"
    page.window_width = 1280
    page.window_height = 900
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.vertical_alignment = ft.MainAxisAlignment.START
    page.padding = 12

    state = AppState()
    setup_gpio_initial()

    # Upper container: Play / Stop
    def on_play_click(e):
        if state.play_btn.disabled:
            return
        # Deactivate Play; Activate Stop; start SPI
        state.play_btn.disabled = True
        state.stop_btn.disabled = False
        page.update()
        # Start SPI task
        state.spi_stop_requested = False
        page.run_task(spi_reader_task, page, state)

    def on_stop_click(e):
        if state.stop_btn.disabled:
            return
        # Deactivate Stop
        state.stop_btn.disabled = True
        # Request SPI stop
        state.spi_stop_requested = True

        # Reset tiles: all Off and temperature "-°C"; set light_off image
        for i in range(16):
            tile = state.no_tiles[i]
            light_img: ft.Image = tile["light_img"]
            temp_text: ft.Text = tile["temp_text"]
            light_img.src_base64 = state.img_light_off_b64
            temp_text.value = "-°C"

        # I2C: set outputs Low (all Off)
        try:
            if state.i2c.initialized:
                state.i2c.write_outputs(0x00, 0x00)
        except Exception as e2:
            print(f"[I2C RESET OUTPUTS ERROR] {e2}")

        # Reactivate Play
        state.play_btn.disabled = False
        page.update()

    play_btn = ft.IconButton(
        icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
        icon_size=40,
        tooltip="Play (Start SPI read)",
        on_click=on_play_click,
        disabled=False,
        style=ft.ButtonStyle(color={"": ft.Colors.GREEN_ACCENT_400}),
    )
    stop_btn = ft.IconButton(
        icon=ft.Icons.STOP_CIRCLE_ROUNDED,
        icon_size=40,
        tooltip="Stop (Stop SPI read)",
        on_click=on_stop_click,
        disabled=True,
        style=ft.ButtonStyle(color={"": ft.Colors.RED_400}),
    )
    state.play_btn = play_btn
    state.stop_btn = stop_btn
    upper = ft.Container(content=ft.Row(controls=[play_btn, stop_btn], spacing=12))

    # Middle container: 2 rows x 8 cols
    row1_tiles = [build_no_tile(state, i + 1) for i in range(8)]
    row2_tiles = [build_no_tile(state, i + 9) for i in range(8)]
    middle = ft.Column(
        controls=[
            ft.Row(controls=row1_tiles, spacing=8, alignment=ft.MainAxisAlignment.CENTER),
            ft.Row(controls=row2_tiles, spacing=8, alignment=ft.MainAxisAlignment.CENTER),
        ],
        spacing=8,
    )

    # Lower container: status panels and Hot Reset
    # I2C Status + info
    i2c_status_label = ft.Container(
        content=ft.Row(controls=[ft.Text("I2C Status", weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_300,
        width=160,
        height=40,
        padding=0,
    )
    i2c_info_text = ft.Text("Not initialized.", color=ft.Colors.BLACK)
    state.i2c_info_text = i2c_info_text
    i2c_info_panel = ft.Container(
        content=ft.Row(controls=[i2c_info_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_50,
        width=400,
        height=40,
        padding=0,
    )
    i2c_row = ft.Row(controls=[i2c_status_label, i2c_info_panel], spacing=0)

    # VDET + info
    vdet_label = ft.Container(
        content=ft.Row(controls=[ft.Text("VDET", weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_300,
        width=160,
        height=40,
        padding=0,
    )
    vdet_info_text = ft.Text("-", color=ft.Colors.BLACK)
    state.vdet_info_text = vdet_info_text
    vdet_info_panel = ft.Container(
        content=ft.Row(controls=[vdet_info_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_50,
        width=400,
        height=40,
        padding=0,
    )
    vdet_row = ft.Row(controls=[vdet_label, vdet_info_panel], spacing=0)

    # RESET + info
    reset_label = ft.Container(
        content=ft.Row(controls=[ft.Text("RESET", weight=ft.FontWeight.BOLD)], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_300,
        width=160,
        height=40,
        padding=0,
    )
    reset_info_text = ft.Text("-", color=ft.Colors.BLACK)
    state.reset_info_text = reset_info_text
    reset_info_panel = ft.Container(
        content=ft.Row(controls=[reset_info_text], alignment=ft.MainAxisAlignment.CENTER),
        bgcolor=ft.Colors.GREY_50,
        width=400,
        height=40,
        padding=0,
    )
    reset_row = ft.Row(controls=[reset_label, reset_info_panel], spacing=0)

    # Hot Reset row
    def on_hot_reset(e):
        warn_text = state.hot_reset_warn
        if state.vdet_high is True:
            # GPIO4 High -> perform hot reset: GPIO15 Low 500ms then High
            try:
                GPIO.output(GPIO15, GPIO.LOW)
                state.reset_high = False
                page.update()
                time.sleep(0.5)  # 500ms
                GPIO.output(GPIO15, GPIO.HIGH)
                state.reset_high = True
                # After 100ms, re-init I2C
                time.sleep(0.1)
                ok = state.i2c.init_pca9539()
                if ok:
                    state.i2c_info_text.value = "Succeeded."
                    state.i2c_info_text.color = ft.Colors.GREEN
                else:
                    state.i2c_info_text.value = "Waiting reset released..."
                    state.i2c_info_text.color = ft.Colors.BLACK
                # Hide warning if shown
                if warn_text:
                    warn_text.value = ""
            except Exception as e2:
                print(f"[HOT RESET ERROR] {e2}")
        else:
            # GPIO4 Low -> show availability message, keep GPIO15 Low
            try:
                GPIO.output(GPIO15, GPIO.LOW)
                state.reset_high = False
            except Exception as e2:
                print(f"[HOT RESET GPIO ERROR] {e2}")
            if warn_text:
                warn_text.value = 'Available when VDET is "High".'
                warn_text.color = ft.Colors.RED
        page.update()

    hot_reset_btn = ft.FilledButton(text="Hot Reset", on_click=on_hot_reset)
    hot_reset_warn = ft.Text("", color=ft.Colors.RED)
    state.hot_reset_warn = hot_reset_warn
    hot_reset_row = ft.Row(controls=[hot_reset_btn, hot_reset_warn], spacing=12, alignment=ft.MainAxisAlignment.START)

    lower = ft.Column(controls=[i2c_row, vdet_row, reset_row, hot_reset_row], spacing=8)

    # Build page
    page.add(upper, ft.Divider(), middle, ft.Divider(), lower)
    page.update()

    # Start VDET polling task
    page.run_task(vdet_polling_task, page, state)

    # Close handling
    def on_close(e):
        print("[APP] Closing...")
        # Stop tasks
        state.vdet_task_running = False
        state.spi_stop_requested = True

        # SPI end process if running
        try:
            # Cleanup GPIO27 and close SPI if the controller exists
            GPIO.cleanup(GPIO27)
            print("[GPIO] GPIO27 cleaned up (on close).")
        except Exception as ex:
            print(f"[GPIO CLEANUP ERROR] GPIO27 (on close): {ex}")
        try:
            if state.spi_ctrl and hasattr(state.spi_ctrl, "spi") and state.spi_ctrl.spi:
                state.spi_ctrl.spi.close()
                print("[SPI] Port closed (on close).")
        except Exception as ex:
            print(f"[SPI CLOSE ERROR] (on close): {ex}")

        # I2C close
        try:
            state.i2c.close()
        except Exception as ex:
            print(f"[I2C CLOSE ERROR] (on close): {ex}")

        # GPIO cleanup
        gpio_cleanup_on_exit()

    page.on_close = on_close


if __name__ == "__main__":
    ft.app(target=main)

# if __name__ == "__main__":
#     # Desktop app
#     ft.app(target=main, view=ft.AppView.FLET_APP)
