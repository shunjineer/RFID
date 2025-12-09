# /src/battery/main_rp.py
# Python 3.11 / Flet 0.28.3
# Raspberry Pi 4B (Trixie) Desktop アプリ
# 要件:
# - Flet Colors/Icons を使用
# - page.update() のみで画面更新
# - page.run_task にはコルーチン関数参照を渡す（コルーチンオブジェクトを渡さない）
# - assets_dir 不使用。画像は base64 で読み込む
# - RPi.GPIO, smbus2 を使用
# - PCA9539PWR(I2C addr 0x74) 初期化と確認、出力制御
# - MR793200 SPI 読み取り（mr793200_controller）と UI/I2C 反映
# - 各種 I/O 例外ハンドリングと終了処理の厳守
# - Play/Stop の非アクティブ時グレーアウト（disabled時はアイコン色をGREY_300に変更）
# - 画面閉じるボタンで終了時に PCA9539 全出力を Low に確実に設定

import os
import sys
import asyncio
import base64
import traceback
from typing import List, Dict, Tuple

import flet as ft

# RPi.GPIO と smbus2 は Raspberry Pi 実機で使用されます
import RPi.GPIO as GPIO
from smbus2 import SMBus

# mr793200_controller の import（/src/mr793200/ 配下）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # /src/battery
MR_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "mr793200"))
if MR_DIR not in sys.path:
    sys.path.append(MR_DIR)
from mr793200_controller import mr793200_controller


# GPIO 定義（BCM）
GPIO_VDET = 4    # 物理Pin 7
GPIO_RESET = 15  # 物理Pin 10
GPIO_SPI_EN = 27 # 物理Pin 13

# I2C
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

# MR793200 USERメモリアドレス群
USER_ADDRS = [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]


class BatteryApp:
    def __init__(self, page: ft.Page):
        self.page = page

        # 画像のbase64文字列（起動時一回読み込み）
        self.light_on_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_on.png"))
        self.light_off_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_off.png"))
        self.battery_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "battery.png"))

        # UI部品保持
        self.play_btn: ft.IconButton | None = None
        self.stop_btn: ft.IconButton | None = None

        # 16個のセル（No.1～No.16）
        self.cells: List[Dict] = []

        # Lower 情報表示
        self.i2c_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.vdet_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.reset_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.hot_reset_msg = ft.Text("", color=ft.Colors.RED, size=14)

        # 状態
        self.app_running = True

        # I2C
        self.bus: SMBus | None = None
        self.i2c_ready = False
        self.cached_out0 = 0x00
        self.cached_out1 = 0x00

        # SPI / MR793200
        self.mr: mr793200_controller | None = None
        self.spi_running = False

        # タスク稼働フラグ
        self.vdet_task_running = False
        self.spi_task_running = False

        # GPIO 初期化
        self._init_gpio_base()

    # ------------------------
    # 画像読込み（base64）
    # ------------------------
    def _load_img_b64(self, path: str) -> str:
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            print(f"[ERROR] Image load failed: {path}: {e}")
            traceback.print_exc()
            return ""

    # ------------------------
    # GPIO 初期化（VDET/RESETのみ）
    # ------------------------
    def _init_gpio_base(self):
        try:
            GPIO.setmode(GPIO.BCM)
            # VDET: 入力 + PUD_DOWN
            GPIO.setup(GPIO_VDET, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            # RESET: 出力 Low 初期化
            GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)
            # SPI_EN(GPIO27)はSPI開始時にセットアップするのでここでは未設定
        except Exception as e:
            print(f"[ERROR] GPIO base init failed: {e}")
            traceback.print_exc()

    # ------------------------
    # I2C 初期化（PCA9539）
    # ------------------------
    def i2c_init_and_verify(self) -> bool:
        # GPIO15 High の100ms後に呼ばれる前提
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)

            # Configuration 0x06, 0x07 = 0x00（全出力）
            self._i2c_write_byte(REG_CONFIG0, 0x00)
            self._i2c_write_byte(REG_CONFIG1, 0x00)

            # Polarity 0x04, 0x05 = 0x00（反転なし）
            self._i2c_write_byte(REG_POLARITY0, 0x00)
            self._i2c_write_byte(REG_POLARITY1, 0x00)

            # Output 0x02, 0x03 = 0x00（全Low）
            self._i2c_write_byte(REG_OUTPUT0, 0x00)
            self._i2c_write_byte(REG_OUTPUT1, 0x00)
            self.cached_out0 = 0x00
            self.cached_out1 = 0x00

            # 読み戻し確認（不一致時は1回再試行）
            ok = True
            ok &= (self._i2c_read_byte(REG_CONFIG0) == 0x00)
            ok &= (self._i2c_read_byte(REG_CONFIG1) == 0x00)
            ok &= (self._i2c_read_byte(REG_POLARITY0) == 0x00)
            ok &= (self._i2c_read_byte(REG_POLARITY1) == 0x00)
            ok &= (self._i2c_read_byte(REG_OUTPUT0) == 0x00)
            ok &= (self._i2c_read_byte(REG_OUTPUT1) == 0x00)
            if not ok:
                print("[WARN] I2C verify failed, retrying once...")
                # 再試行
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
        # 終了処理の1) Output Port 0x02/0x03 に 0x00
        try:
            if self.bus is None:
                # 終了時でも確実に Low を出すため、一時的にオープンして書く
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

    # ------------------------
    # PCA9539 出力更新（No.1..16 に対応）
    # ------------------------
    def update_pca9539_outputs(self, on_states: List[int]):
        # on_states: 16個の0/1
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

    # ------------------------
    # VDET ポーリングタスク（500ms）
    # ------------------------
    async def vdet_poll_task(self):
        self.vdet_task_running = True
        try:
            last_vdet = None
            while self.app_running:
                try:
                    vdet = GPIO.input(GPIO_VDET)  # 0 or 1
                except Exception as e:
                    print(f"[ERROR] GPIO read VDET failed: {e}")
                    traceback.print_exc()
                    vdet = 0
                # 変化時のRESET制御
                if last_vdet is None or vdet != last_vdet:
                    if vdet == 1:
                        # High -> 100ms 後に RESET=High
                        await asyncio.sleep(0.1)
                        try:
                            GPIO.output(GPIO_RESET, GPIO.HIGH)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET High failed: {e}")
                            traceback.print_exc()
                        # さらに100ms後に I2C 初期化
                        await asyncio.sleep(0.1)
                        self.i2c_init_and_verify()
                    else:
                        # Low -> 即座に RESET=Low, I2C close
                        try:
                            GPIO.output(GPIO_RESET, GPIO.LOW)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET Low failed: {e}")
                            traceback.print_exc()
                        # 終了時も含めて安全に Low 出力へ
                        self.i2c_outputs_all_low()
                        self.i2c_close()
                    last_vdet = vdet

                # 情報表示更新
                self._update_status_texts()
                self.page.update()

                await asyncio.sleep(0.5)
        finally:
            self.vdet_task_running = False

    def _update_status_texts(self):
        try:
            vdet_val = GPIO.input(GPIO_VDET)
            reset_val = GPIO.input(GPIO_RESET)
            # VDET info
            if vdet_val == 0:
                self.vdet_info_text.value = "Low"
            elif vdet_val == 1:
                self.vdet_info_text.value = "High"
            else:
                self.vdet_info_text.value = "-"
            # RESET info
            if reset_val == 0:
                self.reset_info_text.value = "Low"
            elif reset_val == 1:
                self.reset_info_text.value = "High"
            else:
                self.reset_info_text.value = "-"
            # I2C Status info（要件通り GPIOの状態で表示を決定）
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

    # ------------------------
    # USERメモリパース（On/Off と 温度）
    # ------------------------
    def _parse_user_words(self, words: Dict[int, int]) -> Tuple[List[int], List[int | None]]:
        # words: {0x22: val, 0x24: val, ...}
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

        # No.1
        on[0] = (w22 >> 15) & 0x1
        temp[0] = (w22 >> 7) & 0xFF

        # No.2
        on[1] = (w22 >> 6) & 0x1
        temp[1] = ((w22 & 0x3F) << 2) | ((w24 >> 14) & 0x3)

        # No.3
        on[2] = (w24 >> 13) & 0x1
        temp[2] = (w24 >> 5) & 0xFF

        # No.4
        on[3] = (w24 >> 4) & 0x1
        temp[3] = ((w24 & 0xF) << 4) | ((w26 >> 12) & 0xF)

        # No.5
        on[4] = (w26 >> 11) & 0x1
        temp[4] = (w26 >> 3) & 0xFF

        # No.6
        on[5] = (w26 >> 2) & 0x1
        temp[5] = ((w26 & 0x3) << 6) | ((w28 >> 10) & 0x3F)

        # No.7
        on[6] = (w28 >> 9) & 0x1
        temp[6] = (w28 >> 1) & 0xFF

        # No.8
        on[7] = (w28 >> 0) & 0x1
        temp[7] = (w2A >> 8) & 0xFF

        # No.9
        on[8] = (w2A >> 7) & 0x1
        temp[8] = ((w2A & 0x7F) << 1) | ((w2C >> 15) & 0x1)

        # No.10
        on[9] = (w2C >> 14) & 0x1
        temp[9] = (w2C >> 6) & 0xFF

        # No.11
        on[10] = (w2C >> 5) & 0x1
        temp[10] = ((w2C & 0x1F) << 3) | ((w2E >> 13) & 0x7)

        # No.12
        on[11] = (w2E >> 12) & 0x1
        temp[11] = (w2E >> 4) & 0xFF

        # No.13
        on[12] = (w2E >> 3) & 0x1
        temp[12] = ((w2E & 0x7) << 5) | ((w30 >> 11) & 0x1F)

        # No.14
        on[13] = (w30 >> 10) & 0x1
        temp[13] = (w30 >> 2) & 0xFF

        # No.15
        on[14] = (w30 >> 1) & 0x1
        temp[14] = ((w30 & 0x1) << 7) | ((w32 >> 9) & 0x7F)

        # No.16
        on[15] = (w32 >> 8) & 0x1
        temp[15] = w32 & 0xFF

        return on, temp

    # ------------------------
    # Play / Stop / Hot Reset
    # ------------------------
    def _set_play_state(self, active: bool):
        # active=True: 押下可能、緑色。active=False: 押下不可、グレーアウト
        if self.play_btn is None:
            return
        self.play_btn.disabled = not active
        self.play_btn.icon_color = ft.Colors.GREEN_ACCENT_400 if active else ft.Colors.GREY_300

    def _set_stop_state(self, active: bool):
        # active=True: 押下可能、赤色。active=False: 押下不可、グレーアウト
        if self.stop_btn is None:
            return
        self.stop_btn.disabled = not active
        self.stop_btn.icon_color = ft.Colors.RED_400 if active else ft.Colors.GREY_300

    def on_play(self, e):
        # Play 押下：Play無効化（グレーアウト）、Stop有効化、SPI開始
        if self.spi_running:
            return
        # UI
        self._set_play_state(active=False)
        self._set_stop_state(active=True)
        self.page.update()

        # SPI開始
        try:
            # GPIO27 設定・High
            try:
                GPIO.setup(GPIO_SPI_EN, GPIO.OUT, initial=GPIO.HIGH)
            except Exception as ge:
                print(f"[ERROR] GPIO27 setup failed: {ge}")
                traceback.print_exc()
            # MR793200 インスタンス生成
            try:
                self.mr = mr793200_controller(sclk_frequency=1_000_000)  # 1MHz
            except Exception as se:
                print(f"[ERROR] MR793200 init failed: {se}")
                traceback.print_exc()
                # 失敗時はボタン状態を元に戻す
                self._set_play_state(active=True)
                self._set_stop_state(active=False)
                self.page.update()
                return

            self.spi_running = True
            # SPI読み取りタスク開始
            self.page.run_task(self.spi_read_task)

        except Exception as e2:
            print(f"[ERROR] SPI start failed: {e2}")
            traceback.print_exc()
            # UI戻す
            self._set_play_state(active=True)
            self._set_stop_state(active=False)
            self.page.update()

    def on_stop(self, e):
        # Stop 押下：SPI通信の終了処理、UI/出力のリセット
        self._set_stop_state(active=False)
        self.page.update()
        # SPI停止と終了処理
        self._spi_cleanup()

        # UI: 全Off & "-°C"、Light Off に戻す
        for i in range(16):
            self.cells[i]["light_img"].src_base64 = self.light_off_b64
            self.cells[i]["temp_text"].value = "-°C"
        self.page.update()

        # PCA9539 を全Low
        self.i2c_outputs_all_low()

        # Play を再度有効化（緑）
        self._set_play_state(active=True)
        self.page.update()

    def _spi_cleanup(self):
        # 途中例外があっても指示された順序で終了:
        # 1) GPIO27 に対し cleanup()
        # 2) SPI ポート close()
        try:
            # 停止フラグ
            self.spi_running = False
        except Exception as e:
            print(f"[ERROR] SPI stop flag set failed: {e}")
            traceback.print_exc()
        finally:
            # GPIO27 cleanup
            try:
                GPIO.cleanup(GPIO_SPI_EN)
            except Exception as e:
                print(f"[ERROR] GPIO27 cleanup failed: {e}")
                traceback.print_exc()

            # SPI close
            try:
                if self.mr is not None and hasattr(self.mr, "spi") and self.mr.spi is not None:
                    self.mr.spi.close()
            except Exception as e:
                print(f"[ERROR] SPI close failed: {e}")
                traceback.print_exc()
            self.mr = None

    def on_hot_reset(self, e):
        # Hot Reset ボタン: VDET Highなら RESETを500ms Low -> High -> 100ms後 I2C再初期化
        self.hot_reset_msg.value = ""
        self.page.update()

        try:
            vdet_val = GPIO.input(GPIO_VDET)
        except Exception as ex:
            print(f"[ERROR] GPIO read VDET in hot reset failed: {ex}")
            traceback.print_exc()
            vdet_val = 0

        if vdet_val == 1:
            # 非同期でリセット操作
            self.page.run_task(self._hot_reset_sequence)
        else:
            # 利用不可メッセージ表示
            self.hot_reset_msg.value = 'Available when VDET is "High."'
            self.hot_reset_msg.color = ft.Colors.RED
            self.page.update()

    async def _hot_reset_sequence(self):
        try:
            GPIO.output(GPIO_RESET, GPIO.LOW)
            await asyncio.sleep(0.5)
            GPIO.output(GPIO_RESET, GPIO.HIGH)
            # 100ms 後 I2C 再初期化
            await asyncio.sleep(0.1)
            self.i2c_init_and_verify()
        except Exception as e:
            print(f"[ERROR] Hot reset sequence failed: {e}")
            traceback.print_exc()
        finally:
            self._update_status_texts()
            self.page.update()

    # ------------------------
    # UI 構築
    # ------------------------
    def build(self) -> ft.Column:
        # Upper: Play / Stop
        self.play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREEN_ACCENT_400,  # 初期値: Activate（緑）
            icon_size=36,
            disabled=False,
            tooltip="Start SPI reading",
            on_click=self.on_play,
        )
        self.stop_btn = ft.IconButton(
            icon=ft.Icons.STOP_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREY_300,  # 初期値: Deactivate（グレーアウト）
            icon_size=36,
            disabled=True,
            tooltip="Stop SPI",
            on_click=self.on_stop,
        )
        upper_row = ft.Row([self.play_btn, self.stop_btn], spacing=10)
        upper_container = ft.Container(content=upper_row, padding=10)

        # Middle: 2行 x 8列（GridViewは使わない）
        self.cells = []
        rows: List[ft.Row] = []
        for r in range(2):
            row_children = []
            for c in range(8):
                idx = r * 8 + c  # 0..15 -> No. idx+1
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

        # Lower: ステータス領域
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
        hot_reset_row = ft.Row(
            [hot_reset_btn, ft.Container(width=10), self.hot_reset_msg],
            spacing=0,
            alignment=ft.MainAxisAlignment.START,
        )

        lower_container = ft.Container(
            content=ft.Column([i2c_row, vdet_row, reset_row, hot_reset_row], spacing=8),
            padding=10,
        )

        # ルート
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

    # ------------------------
    # アプリ開始時のタスク起動
    # ------------------------
    def start_background_tasks(self):
        # VDET ポーリング開始（500ms）
        if not self.vdet_task_running:
            self.page.run_task(self.vdet_poll_task)

    # ------------------------
    # アプリ終了処理
    # ------------------------
    def on_close(self, e=None):
        # アプリの停止フラグ
        self.app_running = False
        # SPI 停止・終了処理
        self._spi_cleanup()
        # I2C 終了処理（1) 出力全Low -> 2) bus close）
        # bus が未初期化でも Low を確実に出す
        self.i2c_outputs_all_low()
        self.i2c_close()
        # GPIO4/15 cleanup（個別指定）
        try:
            GPIO.cleanup([GPIO_VDET, GPIO_RESET])
        except Exception as ex:
            print(f"[ERROR] GPIO cleanup for VDET/RESET failed: {ex}")
            traceback.print_exc()


def main(page: ft.Page):
    # デスクトップアプリ設定
    page.title = "Battery Monitor (Raspberry Pi)"
    page.window.maximized = True
    page.padding = 10
    page.bgcolor = ft.Colors.WHITE

    app = BatteryApp(page)
    root = app.build()
    page.add(root)
    page.update()

    # ステータス初期更新
    app._update_status_texts()
    page.update()

    # 背景タスク開始
    app.start_background_tasks()

    # アプリ終了時のクリーンアップ
    page.on_close = app.on_close


if __name__ == "__main__":
    # flet desktop
    ft.app(target=main, view=ft.AppView.FLET_APP)



# if __name__ == "__main__":
#     ft.app(target=main)
