# /src/battery/main_rp.py
# Python 3.11 / Flet 0.28.3

import os
import sys
import asyncio
import base64
import traceback
from typing import List, Dict, Tuple

import flet as ft

import RPi.GPIO as GPIO
from smbus2 import SMBus

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # /src/battery
MR_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "mr793200"))
if MR_DIR not in sys.path:
    sys.path.append(MR_DIR)
from mr793200_controller import mr793200_controller

GPIO_VDET = 4
GPIO_RESET = 15
GPIO_SPI_EN = 27

I2C_BUS_NO = 1
PCA9539_ADDR = 0x74
REG_INPUT0 = 0x00
REG_INPUT1 = 0x01
REG_OUTPUT0 = 0x02
REG_OUTPUT1 = 0x03
REG_POLARITY0 = 0x04
REG_POLARITY1 = 0x05
REG_CONFIG0 = 0x06
REG_CONFIG1 = 0x07

USER_ADDRS = [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]


class BatteryApp:
    def __init__(self, page: ft.Page):
        self.page = page

        self.light_on_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_on.png"))
        self.light_off_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_off.png"))
        self.battery_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "battery.png"))

        self.play_btn: ft.IconButton | None = None
        self.stop_btn: ft.IconButton | None = None

        self.cells: List[Dict] = []

        self.i2c_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.vdet_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.reset_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.hot_reset_msg = ft.Text("", color=ft.Colors.RED, size=14)

        self.app_running = True

        self.bus: SMBus | None = None
        self.i2c_ready = False
        self.cached_out0 = 0x00
        self.cached_out1 = 0x00

        self.mr: mr793200_controller | None = None
        self.spi_running = False

        self.vdet_task_running = False
        self.spi_task_running = False

        self._init_gpio_base()

    def _load_img_b64(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[ERROR] Image load failed: {path}: {e}")
            traceback.print_exc()
            return ""

    def _init_gpio_base(self):
        try:
            GPIO.setwarnings(False)  # 既使用警告を抑制
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(GPIO_VDET, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
        except Exception as e:
            print(f"[ERROR] GPIO base init failed: {e}")
            traceback.print_exc()

    def i2c_init_and_verify(self) -> bool:
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)

            self._i2c_write_byte(REG_CONFIG0, 0x00)
            self._i2c_write_byte(REG_CONFIG1, 0x00)

            self._i2c_write_byte(REG_POLARITY0, 0x00)
            self._i2c_write_byte(REG_POLARITY1, 0x00)

            self._i2c_write_byte(REG_OUTPUT0, 0x00)
            self._i2c_write_byte(REG_OUTPUT1, 0x00)
            self.cached_out0 = 0x00
            self.cached_out1 = 0x00

            ok = True
            ok &= (self._i2c_read_byte(REG_CONFIG0) == 0x00)
            ok &= (self._i2c_read_byte(REG_CONFIG1) == 0x00)
            ok &= (self._i2c_read_byte(REG_POLARITY0) == 0x00)
            ok &= (self._i2c_read_byte(REG_POLARITY1) == 0x00)
            ok &= (self._i2c_read_byte(REG_OUTPUT0) == 0x00)
            ok &= (self._i2c_read_byte(REG_OUTPUT1) == 0x00)
            if not ok:
                print("[WARN] I2C verify failed, retrying once...")
                self._i2c_write_byte(REG_CONFIG0, 0x00)
                self._i2c_write_byte(REG_CONFIG1, 0x00)
                self._i2c_write_byte(REG_POLARITY0, 0x00)
                self._i2c_write_byte(REG_POLARITY1, 0x00)
                self._i2c_write_byte(REG_OUTPUT0, 0x00)
                self._i2c_write_byte(REG_OUTPUT1, 0x00)
                ok = True
                ok &= (self._i2c_read_byte(REG_CONFIG0) == 0x00)
                ok &= (self._i2c_read_byte(REG_CONFIG1) == 0x00)
                ok &= (self._i2c_read_byte(REG_POLARITY0) == 0x00)
                ok &= (self._i2c_read_byte(REG_POLARITY1) == 0x00)
                ok &= (self._i2c_read_byte(REG_OUTPUT0) == 0x00)
                ok &= (self._i2c_read_byte(REG_OUTPUT1) == 0x00)
            self.i2c_ready = ok
            return ok
        except Exception as e:
            print(f"[ERROR] I2C init/verify failed: {e}")
            traceback.print_exc()
            self.i2c_ready = False
            return False

    def _i2c_write_byte(self, reg: int, value: int):
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)
            self.bus.write_byte_data(PCA9539_ADDR, reg, value & 0xFF)
        except Exception as e:
            print(f"[ERROR] I2C write failed: reg=0x{reg:02X}, val=0x{value:02X} err={e}")
            traceback.print_exc()
            raise

    def _i2c_read_byte(self, reg: int) -> int:
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)
            return self.bus.read_byte_data(PCA9539_ADDR, reg) & 0xFF
        except Exception as e:
            print(f"[ERROR] I2C read failed: reg=0x{reg:02X} err={e}")
            traceback.print_exc()
            raise

    def i2c_outputs_all_low(self):
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)
            self._i2c_write_byte(REG_OUTPUT0, 0x00)
            self._i2c_write_byte(REG_OUTPUT1, 0x00)
            self.cached_out0 = 0x00
            self.cached_out1 = 0x00
        except Exception as e:
            print(f"[ERROR] I2C outputs_all_low failed: {e}")
            traceback.print_exc()

    def i2c_close(self):
        if self.bus is not None:
            try:
                self.bus.close()
            except Exception as e:
                print(f"[ERROR] I2C close failed: {e}")
                traceback.print_exc()
            self.bus = None
        self.i2c_ready = False

    def update_pca9539_outputs(self, on_states: List[int]):
        if not self.i2c_ready or self.bus is None:
            return
        try:
            out0 = 0
            out1 = 0
            for i in range(8):
                out0 |= ((1 if on_states[i] else 0) << i)
            for i in range(8, 16):
                out1 |= ((1 if on_states[i] else 0) << (i - 8))
            if out0 != self.cached_out0:
                self._i2c_write_byte(REG_OUTPUT0, out0)
                self.cached_out0 = out0
            if out1 != self.cached_out1:
                self._i2c_write_byte(REG_OUTPUT1, out1)
                self.cached_out1 = out1
        except Exception as e:
            print(f"[ERROR] update_pca9539_outputs failed: {e}")
            traceback.print_exc()

    async def vdet_poll_task(self):
        self.vdet_task_running = True
        try:
            last_vdet = None
            while self.app_running:
                try:
                    vdet = GPIO.input(GPIO_VDET)
                except Exception as e:
                    print(f"[ERROR] GPIO read VDET failed: {e}")
                    traceback.print_exc()
                    vdet = 0
                if last_vdet is None or vdet != last_vdet:
                    if vdet == 1:
                        await asyncio.sleep(0.1)
                        try:
                            GPIO.output(GPIO_RESET, GPIO.HIGH)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET High failed: {e}")
                            traceback.print_exc()
                        await asyncio.sleep(0.1)
                        self.i2c_init_and_verify()
                    else:
                        try:
                            GPIO.output(GPIO_RESET, GPIO.LOW)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET Low failed: {e}")
                            traceback.print_exc()
                        self.i2c_outputs_all_low()
                        self.i2c_close()
                    last_vdet = vdet

                self._update_status_texts()
                self.page.update()
                await asyncio.sleep(0.5)
        finally:
            self.vdet_task_running = False

    def _update_status_texts(self):
        try:
            vdet_val = GPIO.input(GPIO_VDET)
            reset_val = GPIO.input(GPIO_RESET)
            if vdet_val == 0:
                self.vdet_info_text.value = "Low"
            elif vdet_val == 1:
                self.vdet_info_text.value = "High"
            else:
                self.vdet_info_text.value = "-"
            if reset_val == 0:
                self.reset_info_text.value = "Low"
            elif reset_val == 1:
                self.reset_info_text.value = "High"
            else:
                self.reset_info_text.value = "-"
            if vdet_val == 0 and reset_val == 0:
                self.i2c_info_text.value = "Not initialized."
                self.i2c_info_text.color = ft.Colors.BLACK
            elif vdet_val == 1 and reset_val == 0:
                self.i2c_info_text.value = "Waiting reset released..."
                self.i2c_info_text.color = ft.Colors.BLACK
            elif vdet_val == 1 and reset_val == 1:
                self.i2c_info_text.value = "Succeeded."
                self.i2c_info_text.color = ft.Colors.GREEN
            else:
                self.i2c_info_text.value = "-"
                self.i2c_info_text.color = ft.Colors.BLACK
        except Exception as e:
            print(f"[ERROR] Update status texts failed: {e}")
            traceback.print_exc()

    # 追加: SPI 読み取りタスク（500ms）
    async def spi_read_task(self):
        self.spi_task_running = True
        try:
            while self.spi_running and self.mr is not None:
                words = {}
                try:
                    for addr in USER_ADDRS:
                        hexstr = self.mr.read_nvm1(0x04, addr, 1)
                        words[addr] = int(hexstr, 16)
                except Exception as e:
                    print(f"[ERROR] SPI read failed: {e}")
                    traceback.print_exc()
                    await asyncio.sleep(0.5)
                    continue

                on_states, temps = self._parse_user_words(words)

                for i in range(16):
                    img = self.cells[i]["light_img"]
                    img.src_base64 = self.light_on_b64 if on_states[i] else self.light_off_b64
                    ttxt = self.cells[i]["temp_text"]
                    ttxt.value = f"{temps[i]}°C" if temps[i] is not None else "-°C"
                self.page.update()

                self.update_pca9539_outputs(on_states)
                await asyncio.sleep(0.5)
        finally:
            self.spi_task_running = False

    def _parse_user_words(self, words: Dict[int, int]) -> Tuple[List[int], List[int | None]]:
        on = [0] * 16
        temp = [None] * 16

        w22 = words.get(0x22, 0)
        w24 = words.get(0x24, 0)
        w26 = words.get(0x26, 0)
        w28 = words.get(0x28, 0)
        w2A = words.get(0x2A, 0)
        w2C = words.get(0x2C, 0)
        w2E = words.get(0x2E, 0)
        w30 = words.get(0x30, 0)
        w32 = words.get(0x32, 0)

        on[0] = (w22 >> 15) & 0x1
        temp[0] = (w22 >> 7) & 0xFF

        on[1] = (w22 >> 6) & 0x1
        temp[1] = ((w22 & 0x3F) << 2) | ((w24 >> 14) & 0x3)

        on[2] = (w24 >> 13) & 0x1
        temp[2] = (w24 >> 5) & 0xFF

        on[3] = (w24 >> 4) & 0x1
        temp[3] = ((w24 & 0xF) << 4) | ((w26 >> 12) & 0xF)

        on[4] = (w26 >> 11) & 0x1
        temp[4] = (w26 >> 3) & 0xFF

        on[5] = (w26 >> 2) & 0x1
        temp[5] = ((w26 & 0x3) << 6) | ((w28 >> 10) & 0x3F)

        on[6] = (w28 >> 9) & 0x1
        temp[6] = (w28 >> 1) & 0xFF

        on[7] = (w28 >> 0) & 0x1
        temp[7] = (w2A >> 8) & 0xFF

        on[8] = (w2A >> 7) & 0x1
        temp[8] = ((w2A & 0x7F) << 1) | ((w2C >> 15) & 0x1)

        on[9] = (w2C >> 14) & 0x1
        temp[9] = (w2C >> 6) & 0xFF

        on[10] = (w2C >> 5) & 0x1
        temp[10] = ((w2C & 0x1F) << 3) | ((w2E >> 13) & 0x7)

        on[11] = (w2E >> 12) & 0x1
        temp[11] = (w2E >> 4) & 0xFF

        on[12] = (w2E >> 3) & 0x1
        temp[12] = ((w2E & 0x7) << 5) | ((w30 >> 11) & 0x1F)

        on[13] = (w30 >> 10) & 0x1
        temp[13] = (w30 >> 2) & 0xFF

        on[14] = (w30 >> 1) & 0x1
        temp[14] = ((w30 & 0x1) << 7) | ((w32 >> 9) & 0x7F)

        on[15] = (w32 >> 8) & 0x1
        temp[15] = w32 & 0xFF

        return on, temp

    def _set_play_state(self, active: bool):
        if self.play_btn is None:
            return
        self.play_btn.disabled = not active
        self.play_btn.icon_color = ft.Colors.GREEN_ACCENT_400 if active else ft.Colors.GREY_300

    def _set_stop_state(self, active: bool):
        if self.stop_btn is None:
            return
        self.stop_btn.disabled = not active
        self.stop_btn.icon_color = ft.Colors.RED_400 if active else ft.Colors.GREY_300

    def on_play(self, e):
        if self.spi_running:
            return
        self._set_play_state(active=False)
        self._set_stop_state(active=True)
        self.page.update()

        try:
            try:
                GPIO.setup(GPIO_SPI_EN, GPIO.OUT, initial=GPIO.HIGH)
            except Exception as ge:
                print(f"[ERROR] GPIO27 setup failed: {ge}")
                traceback.print_exc()
            try:
                self.mr = mr793200_controller(sclk_frequency=1_000_000)
            except Exception as se:
                print(f"[ERROR] MR793200 init failed: {se}")
                traceback.print_exc()
                self._set_play_state(active=True)
                self._set_stop_state(active=False)
                self.page.update()
                return

            self.spi_running = True
            self.page.run_task(self.spi_read_task)

        except Exception as e2:
            print(f"[ERROR] SPI start failed: {e2}")
            traceback.print_exc()
            self._set_play_state(active=True)
            self._set_stop_state(active=False)
            self.page.update()

    def on_stop(self, e):
        self._set_stop_state(active=False)
        self.page.update()
        self._spi_cleanup()

        for i in range(16):
            self.cells[i]["light_img"].src_base64 = self.light_off_b64
            self.cells[i]["temp_text"].value = "-°C"
        self.page.update()

        self.i2c_outputs_all_low()

        self._set_play_state(active=True)
        self.page.update()

    def _spi_cleanup(self):
        try:
            self.spi_running = False
        except Exception as e:
            print(f"[ERROR] SPI stop flag set failed: {e}")
            traceback.print_exc()
        finally:
            try:
                GPIO.cleanup(GPIO_SPI_EN)
            except Exception as e:
                print(f"[ERROR] GPIO27 cleanup failed: {e}")
                traceback.print_exc()
            try:
                if self.mr is not None and hasattr(self.mr, "spi") and self.mr.spi is not None:
                    self.mr.spi.close()
            except Exception as e:
                print(f"[ERROR] SPI close failed: {e}")
                traceback.print_exc()
            self.mr = None

    def on_hot_reset(self, e):
        self.hot_reset_msg.value = ""
        self.page.update()

        try:
            vdet_val = GPIO.input(GPIO_VDET)
        except Exception as ex:
            print(f"[ERROR] GPIO read VDET in hot reset failed: {ex}")
            traceback.print_exc()
            vdet_val = 0

        if vdet_val == 1:
            self.page.run_task(self._hot_reset_sequence)
        else:
            self.hot_reset_msg.value = 'Available when VDET is "High."'
            self.hot_reset_msg.color = ft.Colors.RED
            self.page.update()

    async def _hot_reset_sequence(self):
        try:
            GPIO.output(GPIO_RESET, GPIO.LOW)
            await asyncio.sleep(0.5)
            GPIO.output(GPIO_RESET, GPIO.HIGH)
            await asyncio.sleep(0.1)
            self.i2c_init_and_verify()
        except Exception as e:
            print(f"[ERROR] Hot reset sequence failed: {e}")
            traceback.print_exc()
        finally:
            self._update_status_texts()
            self.page.update()

    def build(self) -> ft.Column:
        self.play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREEN_ACCENT_400,
            icon_size=36,
            disabled=False,
            tooltip="Start SPI reading",
            on_click=self.on_play,
        )
        self.stop_btn = ft.IconButton(
            icon=ft.Icons.STOP_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREY_300,
            icon_size=36,
            disabled=True,
            tooltip="Stop SPI",
            on_click=self.on_stop,
        )
        upper_row = ft.Row([self.play_btn, self.stop_btn], spacing=10)
        upper_container = ft.Container(content=upper_row, padding=10)

        self.cells = []
        rows: List[ft.Row] = []
        for r in range(2):
            row_children = []
            for c in range(8):
                idx = r * 8 + c
                title = ft.Text(f"No. {idx+1}", size=14, weight=ft.FontWeight.W_600)
                light_img = ft.Image(src_base64=self.light_off_b64, width=180, height=180)
                temp_text = ft.Text("-°C", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.BLACK)
                battery_stack = ft.Stack(
                    controls=[
                        ft.Image(src_base64=self.battery_b64, width=180, height=180),
                        ft.Container(content=temp_text, width=180, height=180, alignment=ft.alignment.center),
                    ],
                    width=180,
                    height=180,
                )
                cell_col = ft.Column([title, light_img, battery_stack], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
                cell_container = ft.Container(
                    content=cell_col,
                    padding=6,
                    border=ft.border.all(1, ft.Colors.GREY_300),
                    bgcolor=ft.Colors.GREY_50,
                )
                row_children.append(cell_container)
                self.cells.append({"light_img": light_img, "temp_text": temp_text})
            rows.append(ft.Row(row_children, spacing=8))
        middle_container = ft.Column(rows, spacing=8, padding=10)

        label_w = 160
        info_w = 420
        row_h = 40

        def mk_label(text: str) -> ft.Container:
            return ft.Container(
                width=label_w,
                height=row_h,
                bgcolor=ft.Colors.GREY_300,
                content=ft.Row([ft.Text(text, size=14, weight=ft.FontWeight.W_600)], alignment=ft.MainAxisAlignment.CENTER),
                padding=0,
            )

        def mk_info(content: ft.Control) -> ft.Container:
            return ft.Container(
                width=info_w,
                height=row_h,
                bgcolor=ft.Colors.GREY_50,
                content=ft.Row([content], alignment=ft.MainAxisAlignment.START),
                padding=ft.padding.only(left=10),
            )

        i2c_row = ft.Row([mk_label("I2C Status"), mk_info(self.i2c_info_text)], spacing=0)
        vdet_row = ft.Row([mk_label("VDET"), mk_info(self.vdet_info_text)], spacing=0)
        reset_row = ft.Row([mk_label("RESET"), mk_info(self.reset_info_text)], spacing=0)

        hot_reset_btn = ft.ElevatedButton("Hot Reset", on_click=self.on_hot_reset)
        hot_reset_row = ft.Row([hot_reset_btn, ft.Container(width=10), self.hot_reset_msg], spacing=0, alignment=ft.MainAxisAlignment.START)

        lower_container = ft.Container(content=ft.Column([i2c_row, vdet_row, reset_row, hot_reset_row], spacing=8), padding=10)

        root = ft.Column(
            controls=[
                upper_container,
                ft.Divider(height=1, thickness=1),
                middle_container,
                ft.Divider(height=1, thickness=1),
                lower_container,
            ],
            spacing=6,
            expand=False,
        )
        return root

    def start_background_tasks(self):
        if not self.vdet_task_running:
            self.page.run_task(self.vdet_poll_task)

    def on_close(self, e=None):
        self.app_running = False
        self._spi_cleanup()
        self.i2c_outputs_all_low()  # busがNoneでも一時的に開いてLow化
        self.i2c_close()
        try:
            GPIO.cleanup([GPIO_VDET, GPIO_RESET])
        except Exception as ex:
            print(f"[ERROR] GPIO cleanup for VDET/RESET failed: {ex}")
            traceback.print_exc()


def main(page: ft.Page):
    page.title = "Battery Monitor (Raspberry Pi)"
    page.window.maximized = True
    page.padding = 10
    page.bgcolor = ft.Colors.WHITE

    app = BatteryApp(page)
    root = app.build()
    page.add(root)
    page.update()

    app._update_status_texts()
    page.update()

    app.start_background_tasks()

    page.on_close = app.on_close


if __name__ == "__main__":
    ft.app(target=main, view=ft.AppView.FLET_APP)


# if __name__ == "__main__":
#     ft.app(target=main)
