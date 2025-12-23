# /src/battery/main_rp.py
# Python 3.11 / Flet 0.28.3
# Raspberry Pi 4B (Desktop) 用アプリ
# 目的:
# - Flet UIで MR793200 のUSERメモリから取得した状態を表示
# - PCA9539PWR(I2C addr 0x74) の全出力制御（UIに連動）
# - アプリ終了（画面の閉じるボタン含む）時に確実に PCA9539 の出力を Low にする
# - I2C EIO ([Errno 5] Input/output error) 発生時の対策（RESETをHighにしてから再試行）
# - Play/Stop アイコンボタンの非アクティブ時グレーアウト（disabled時はGREY_300）
# 注意:
# - 画像はassetsディレクトリを使わず、base64で読み込む
# - page.run_task()にはコルーチン関数参照を渡す
# - RPi.GPIO と smbus2 を使用
# - 終了処理は on_close だけに頼らず、atexit/OSシグナルでも実行

import os
import sys
import asyncio
import base64
import traceback
import time
import signal
import atexit
from threading import Lock
from typing import List, Dict, Tuple

import flet as ft

# Raspberry Pi 実機向けライブラリ
import RPi.GPIO as GPIO
from smbus2 import SMBus

# mr793200_controller の import（/src/mr793200 配下）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # /src/battery
MR_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "mr793200"))
if MR_DIR not in sys.path:
    sys.path.append(MR_DIR)
from mr793200_controller import mr793200_controller

# ------------------------
# GPIO 定義（BCM番号）
# ------------------------
GPIO_VDET = 4     # VDET入力
GPIO_RESET = 15   # RESET出力（PCA9539等のリセット制御）
GPIO_SPI_EN = 27  # SPI有効化制御（MR793200）

# ------------------------
# I2C / PCA9539 レジスタ定義
# ------------------------
I2C_BUS_NO = 1
PCA9539_ADDR = 0x74
REG_OUTPUT0 = 0x02
REG_OUTPUT1 = 0x03
REG_POLARITY0 = 0x04
REG_POLARITY1 = 0x05
REG_CONFIG0 = 0x06
REG_CONFIG1 = 0x07

# ------------------------
# MR793200 USERメモリ アドレス群
# ------------------------
USER_ADDRS = [0x22, 0x24, 0x26, 0x28, 0x2A, 0x2C, 0x2E, 0x30, 0x32]

# ------------------------
# グローバルなクリーンアップ制御
# ------------------------
_APP_INSTANCE = None         # atexit/シグナルから参照するためのアプリインスタンス
_CLEANUP_LOCK = Lock()       # 冪等なクリーンアップのためのロック
_CLEANED = False             # クリーンアップ済みのフラグ


def _fallback_i2c_all_low():
    """
    アプリインスタンスが取得できない場合の最終手段。
    SMBusを一時的に開いて PCA9539 の出力ポートを 0x00 に書いて Low にする。
    RESETはここでは触らない（文脈が不明のため）。
    """
    try:
        bus = SMBus(I2C_BUS_NO)
        bus.write_byte_data(PCA9539_ADDR, REG_OUTPUT0, 0x00)
        bus.write_byte_data(PCA9539_ADDR, REG_OUTPUT1, 0x00)
        try:
            bus.close()
        except Exception:
            pass
        print("[INFO] Fallback: PCA9539 outputs forced Low.")
    except Exception as e:
        print(f"[ERROR] Fallback I2C Low failed: {e}")
        traceback.print_exc()


def _safe_cleanup():
    """
    冪等なクリーンアップ関数。finalize() が呼ばれていない状況でも
    PCA9539 を Low にして GPIO をクリーンアップする。
    """
    global _CLEANED, _APP_INSTANCE
    with _CLEANUP_LOCK:
        if _CLEANED:
            return
        try:
            if _APP_INSTANCE is not None:
                _APP_INSTANCE.finalize()
            else:
                _fallback_i2c_all_low()
                try:
                    GPIO.cleanup()
                except Exception:
                    pass
        finally:
            _CLEANED = True


def _signal_handler(signum, frame):
    """
    SIGINT/SIGTERM/SIGHUP を捕捉して、確実にクリーンアップしてから終了する。
    """
    print(f"[INFO] Caught signal {signum}, cleaning up...")
    _safe_cleanup()
    os._exit(0)


# atexit とシグナルハンドラ登録（終了時のフェイルセーフ）
atexit.register(_safe_cleanup)
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)
try:
    signal.signal(signal.SIGHUP, _signal_handler)
except Exception:
    # 一部環境では SIGHUP 未サポート
    pass


class BatteryApp:
    """
    FletページにUIを構築し、GPIO/I2C/SPI制御を行うメインクラス。
    """
    def __init__(self, page: ft.Page):
        self.page = page

        # 画像（base64）
        self.light_on_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_on.png"))
        self.light_off_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "light_off.png"))
        self.battery_b64 = self._load_img_b64(os.path.join(BASE_DIR, "img", "battery.png"))

        # 上段の Play/Stop ボタン参照
        self.play_btn: ft.IconButton | None = None
        self.stop_btn: ft.IconButton | None = None

        # セル表示（ライト画像＋温度テキスト）を保持
        self.cells: List[Dict] = []

        # 下段ステータス表示コントロール
        self.i2c_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.vdet_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.reset_info_text = ft.Text("-", color=ft.Colors.BLACK, size=14)
        self.hot_reset_msg = ft.Text("", color=ft.Colors.RED, size=14)

        # アプリ状態
        self.app_running = True

        # I2C関連
        self.bus: SMBus | None = None
        self.i2c_ready = False
        self.cached_out0 = 0x00  # 直近書き込み値（Port0）
        self.cached_out1 = 0x00  # 直近書き込み値（Port1）

        # SPI / MR793200
        self.mr: mr793200_controller | None = None
        self.spi_running = False

        # 背景タスク稼働フラグ
        self.vdet_task_running = False
        self.spi_task_running = False

        # GPIO初期化
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
    # GPIO 初期化（VDET入力/RESET出力）
    # ------------------------
    def _init_gpio_base(self):
        try:
            GPIO.setwarnings(False)  # 「already in use」警告を抑制
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(GPIO_VDET, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)  # VDETはプルダウン
            GPIO.setup(GPIO_RESET, GPIO.OUT, initial=GPIO.LOW)          # RESETはLow初期化
            # SPI_EN(GPIO27)は SPI開始時にセットアップ
        except Exception as e:
            print(f"[ERROR] GPIO base init failed: {e}")
            traceback.print_exc()

    # ------------------------
    # I2C 初期化（PCA9539 を出力モード＋全Low）
    # ------------------------
    def i2c_init_and_verify(self) -> bool:
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)

            # 全出力・非反転・出力0で初期化
            self._i2c_write_byte(REG_CONFIG0, 0x00)
            self._i2c_write_byte(REG_CONFIG1, 0x00)
            self._i2c_write_byte(REG_POLARITY0, 0x00)
            self._i2c_write_byte(REG_POLARITY1, 0x00)
            self._i2c_write_byte(REG_OUTPUT0, 0x00)
            self._i2c_write_byte(REG_OUTPUT1, 0x00)
            self.cached_out0 = 0x00
            self.cached_out1 = 0x00

            # 読み戻し確認（不一致なら1回再試行）
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

    # ------------------------
    # I2C 書き込み／読み出し（共通）
    # ------------------------
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

    # ------------------------
    # 終了時の Low 強制（EIO対策付き）
    # ------------------------
    def i2c_outputs_all_low(self):
        """
        PCA9539 の出力ポートを 0x00 に設定して Low にする。
        EIO発生時（RESETがLowでスレーブが応答不能）に備え、RESETをHighにしてから再試行。
        """
        try:
            if self.bus is None:
                self.bus = SMBus(I2C_BUS_NO)
            # 1回目の試行
            try:
                self._i2c_write_byte(REG_OUTPUT0, 0x00)
                self._i2c_write_byte(REG_OUTPUT1, 0x00)
                self.cached_out0 = 0x00
                self.cached_out1 = 0x00
                return
            except Exception as e_first:
                print(f"[WARN] First attempt to force Low failed: {e_first}")
                # RESET を High にして一定時間待機（I2C有効化）
                try:
                    GPIO.setup(GPIO_RESET, GPIO.OUT)
                    GPIO.output(GPIO_RESET, GPIO.HIGH)
                    time.sleep(0.12)  # 120msほどの待機
                except Exception as ge:
                    print(f"[ERROR] Ensure RESET High failed: {ge}")
                    traceback.print_exc()
                # SMBus を再オープンして再試行
                try:
                    if self.bus is not None:
                        try:
                            self.bus.close()
                        except Exception:
                            pass
                    self.bus = SMBus(I2C_BUS_NO)
                    self._i2c_write_byte(REG_OUTPUT0, 0x00)
                    self._i2c_write_byte(REG_OUTPUT1, 0x00)
                    self.cached_out0 = 0x00
                    self.cached_out1 = 0x00
                    print("[INFO] PCA9539 outputs forced Low (retry succeeded).")
                except Exception as e_second:
                    print(f"[ERROR] Retry to force Low failed: {e_second}")
                    traceback.print_exc()
        except Exception as e:
            print(f"[ERROR] I2C outputs_all_low failed: {e}")
            traceback.print_exc()

    # ------------------------
    # SMBus クローズ
    # ------------------------
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
    # PCA9539 出力更新（No.1..16に対応）
    # ------------------------
    def update_pca9539_outputs(self, on_states: List[int]):
        """
        on_states: 16要素の0/1配列。これを PCA9539 出力に反映する。
        """
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
    # VDET ポーリングタスク（500ms周期）
    # ------------------------
    async def vdet_poll_task(self):
        """
        VDETの変化を監視し、VDET=HighでRESETをHighにして I2C を初期化。
        VDET=LowでRESETをLowにし、I2Cはクローズ（Low強制は終了処理で実施）。
        """
        self.vdet_task_running = True
        try:
            last_vdet = None
            while self.app_running:
                # VDET読み取り（例外時は0扱い）
                try:
                    vdet = GPIO.input(GPIO_VDET)
                except Exception as e:
                    print(f"[ERROR] GPIO read VDET failed: {e}")
                    traceback.print_exc()
                    vdet = 0

                # 変化時のみ処理
                if last_vdet is None or vdet != last_vdet:
                    if vdet == 1:
                        # VDET=High -> 少し待って RESET=High -> さらに待って I2C初期化
                        await asyncio.sleep(0.1)
                        try:
                            GPIO.output(GPIO_RESET, GPIO.HIGH)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET High failed: {e}")
                            traceback.print_exc()
                        await asyncio.sleep(0.1)
                        self.i2c_init_and_verify()
                    else:
                        # VDET=Low -> RESET=Low, I2Cは閉じる（Low強制は finalize 側で安全に実施）
                        try:
                            GPIO.output(GPIO_RESET, GPIO.LOW)
                        except Exception as e:
                            print(f"[ERROR] GPIO set RESET Low failed: {e}")
                            traceback.print_exc()
                        self.i2c_close()
                    last_vdet = vdet

                # ステータス表示更新
                self._update_status_texts()
                self.page.update()
                await asyncio.sleep(0.5)
        finally:
            self.vdet_task_running = False

    # ------------------------
    # ステータス表示更新
    # ------------------------
    def _update_status_texts(self):
        try:
            vdet_val = GPIO.input(GPIO_VDET)
            reset_val = GPIO.input(GPIO_RESET)
            # VDET状態
            self.vdet_info_text.value = "High" if vdet_val == 1 else ("Low" if vdet_val == 0 else "-")
            # RESET状態
            self.reset_info_text.value = "High" if reset_val == 1 else ("Low" if reset_val == 0 else "-")
            # I2Cステータス（GPIO状態から表示）
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
    # SPI 読み取りタスク（500ms周期）
    # ------------------------
    async def spi_read_task(self):
        """
        MR793200 の USERメモリから値を読み、UI表示＋PCA9539出力に反映。
        """
        self.spi_task_running = True
        try:
            while self.spi_running and self.mr is not None:
                words = {}
                # USERメモリ一括取得（例外時は継続）
                try:
                    for addr in USER_ADDRS:
                        hexstr = self.mr.read_nvm1(0x04, addr, 1)  # 1ワード読み
                        words[addr] = int(hexstr, 16)
                except Exception as e:
                    print(f"[ERROR] SPI read failed: {e}")
                    traceback.print_exc()
                    await asyncio.sleep(0.02)
                    continue

                # on/off と温度配列に変換
                on_states, temps = self._parse_user_words(words)

                # UI更新（ライト画像/温度）
                for i in range(16):
                    img = self.cells[i]["light_img"]
                    img.src_base64 = self.light_on_b64 if on_states[i] else self.light_off_b64
                    ttxt = self.cells[i]["temp_text"]
                    ttxt.value = f"{temps[i]}°C" if temps[i] is not None else "-°C"
                self.page.update()

                # I2C出力反映
                self.update_pca9539_outputs(on_states)

                # 次周期まで待機
                await asyncio.sleep(0.02)
        finally:
            self.spi_task_running = False

    # ------------------------
    # USERメモリの値を on/off と 温度にパース
    # ------------------------
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

        # 以下、仕様に沿ってビット展開
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

    # ------------------------
    # Play/Stop の非アクティブ時グレーアウト制御
    # ------------------------
    def _set_play_state(self, active: bool):
        """
        active=True: 押下可能、緑色
        active=False: 押下不可、グレーアウト（GREY_300）
        """
        if self.play_btn is None:
            return
        self.play_btn.disabled = not active
        self.play_btn.icon_color = ft.Colors.GREEN_ACCENT_400 if active else ft.Colors.GREY_300

    def _set_stop_state(self, active: bool):
        """
        active=True: 押下可能、赤色
        active=False: 押下不可、グレーアウト（GREY_300）
        """
        if self.stop_btn is None:
            return
        self.stop_btn.disabled = not active
        self.stop_btn.icon_color = ft.Colors.RED_400 if active else ft.Colors.GREY_300

    # ------------------------
    # Play ボタン押下：SPI開始
    # ------------------------
    def on_play(self, e):
        if self.spi_running:
            return
        # UI更新（Play無効化、Stop有効化）
        self._set_play_state(active=False)
        self._set_stop_state(active=True)
        self.page.update()

        # SPI開始処理
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

            # タスク開始
            self.spi_running = True
            self.page.run_task(self.spi_read_task)

        except Exception as e2:
            print(f"[ERROR] SPI start failed: {e2}")
            traceback.print_exc()
            # UI戻す
            self._set_play_state(active=True)
            self._set_stop_state(active=False)
            self.page.update()

    # ------------------------
    # Stop ボタン押下：SPI停止＋UI/I2C整理
    # ------------------------
    def on_stop(self, e):
        # Stop無効化
        self._set_stop_state(active=False)
        self.page.update()

        # SPI停止と終了処理
        self._spi_cleanup()

        # UI: 全Off & "-°C"、Light Off に戻す
        for i in range(16):
            self.cells[i]["light_img"].src_base64 = self.light_off_b64
            self.cells[i]["temp_text"].value = "-°C"
        self.page.update()

        # PCA9539 を全Low（EIO対策付き）
        self.i2c_outputs_all_low()

        # Play を再度有効化（緑）
        self._set_play_state(active=True)
        self.page.update()

    # ------------------------
    # SPI停止とポートクローズ
    # ------------------------
    def _spi_cleanup(self):
        try:
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

    # ------------------------
    # Hot Reset ボタン（VDET High時のみ）
    # ------------------------
    def on_hot_reset(self, e):
        # メッセージ消去
        self.hot_reset_msg.value = ""
        self.page.update()

        # VDET状態確認
        try:
            vdet_val = GPIO.input(GPIO_VDET)
        except Exception as ex:
            print(f"[ERROR] GPIO read VDET in hot reset failed: {ex}")
            traceback.print_exc()
            vdet_val = 0

        if vdet_val == 1:
            # 非同期でリセット操作（RESET Low -> High）
            self.page.run_task(self._hot_reset_sequence)
        else:
            # 利用不可メッセージ表示
            self.hot_reset_msg.value = 'Available when VDET is "High."'
            self.hot_reset_msg.color = ft.Colors.RED
            self.page.update()

    # ------------------------
    # Hot Reset 処理本体
    # ------------------------
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
    # UI構築（上段/中央/下段）
    # ------------------------
    def build(self) -> ft.Column:
        # Upper: Play / Stop
        self.play_btn = ft.IconButton(
            icon=ft.Icons.PLAY_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREEN_ACCENT_400,  # 初期値: Activate（緑）
            icon_size=54,
            disabled=False,
            tooltip="Start battery monitoring",
            on_click=self.on_play,
        )
        self.stop_btn = ft.IconButton(
            icon=ft.Icons.STOP_CIRCLE_ROUNDED,
            icon_color=ft.Colors.GREY_300,          # 初期値: Deactivate（グレーアウト）
            icon_size=54,
            disabled=True,
            tooltip="Stop battery monitoring",
            on_click=self.on_stop,
        )
        upper_row = ft.Row([self.play_btn, self.stop_btn], spacing=10)
        upper_container = ft.Container(content=upper_row, padding=10)

        # Middle: 2行 x 8列
        self.cells = []
        rows: List[ft.Row] = []
        for r in range(2):
            row_children = []
            for c in range(8):
                idx = r * 8 + c  # 0..15 -> No. idx+1
                title = ft.Text(f"No. {idx+1}", size=20, weight=ft.FontWeight.W_600)
                light_img = ft.Image(src_base64=self.light_off_b64, width=120, height=120)
                temp_text = ft.Text("-°C", size=20, weight=ft.FontWeight.W_600, color=ft.Colors.BLACK)
                battery_stack = ft.Stack(
                    controls=[
                        ft.Image(src_base64=self.battery_b64, width=120, height=120),
                        ft.Container(content=temp_text, width=120, height=120, alignment=ft.alignment.center),
                    ],
                    width=120,
                    height=120,
                )
                cell_col = ft.Column([title, light_img, battery_stack], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
                cell_container = ft.Container(
                    content=cell_col,
                    padding=6,
                    border=ft.border.all(1, ft.Colors.GREY_300),
                    bgcolor=ft.Colors.GREY_50,
                    height=300,
                    width=220,
                    border_radius=10,
                )
                row_children.append(cell_container)
                self.cells.append({"light_img": light_img, "temp_text": temp_text})
            rows.append(ft.Row(row_children, spacing=8))
        middle_container = ft.Column(rows, spacing=8)

        # Lower: ステータス領域（ラベル＋情報表示）
        def mk_label(text: str) -> ft.Container:
            return ft.Container(
                width=160,
                height=40,
                bgcolor=ft.Colors.GREY_300,
                content=ft.Row([ft.Text(text, size=14, weight=ft.FontWeight.W_600)], alignment=ft.MainAxisAlignment.CENTER),
                padding=0,
            )

        def mk_info(content: ft.Control) -> ft.Container:
            return ft.Container(
                width=420,
                height=40,
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

        # ルートレイアウト
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
    # アプリ開始時の背景タスク起動
    # ------------------------
    def start_background_tasks(self):
        # VDET ポーリング開始（500ms）
        if not self.vdet_task_running:
            self.page.run_task(self.vdet_poll_task)

    # ------------------------
    # 終了処理（on_close および atexit/シグナルから呼ばれる）
    # ------------------------
    def finalize(self):
        """
        冪等な終了処理。タスクを停止し、RESETをHighにしてI2Cを有効化した後、
        PCA9539 の出力を Low に強制してからクローズする。
        """
        # 先にアプリ停止フラグを落とし、VDETタスクが抜ける猶予（競合回避）
        try:
            self.app_running = False
        except Exception:
            pass
        time.sleep(0.2)

        # SPI 停止
        self._spi_cleanup()

        # RESET を一時的に High にして I2C を有効化（EIO対策）
        try:
            GPIO.setup(GPIO_RESET, GPIO.OUT)
            GPIO.output(GPIO_RESET, GPIO.HIGH)
            time.sleep(0.12)
        except Exception as ex:
            print(f"[ERROR] Prepare RESET High for finalize failed: {ex}")
            traceback.print_exc()

        # PCA9539 の出力を Low にしてから I2Cクローズ
        self.i2c_outputs_all_low()
        self.i2c_close()

        # GPIO のクリーンアップ（VDET/RESET）
        try:
            GPIO.cleanup([GPIO_VDET, GPIO_RESET])
        except Exception as ex:
            print(f"[ERROR] GPIO cleanup for VDET/RESET failed: {ex}")
            traceback.print_exc()

    # ------------------------
    # Fletの閉じるイベントハンドラ
    # ------------------------
    def on_close(self, e=None):
        self.finalize()


# ------------------------
# Flet エントリポイント
# ------------------------
def main(page: ft.Page):
    # デスクトップアプリ設定
    page.title = "Battery Monitor (Raspberry Pi)"
    page.window.maximized = True
    page.padding = 10
    page.bgcolor = ft.Colors.WHITE

    # アプリインスタンス生成（atexit/シグナル用に保持）
    app = BatteryApp(page)
    global _APP_INSTANCE
    _APP_INSTANCE = app

    # UI構築・追加
    root = app.build()
    page.add(root)
    page.update()

    # 初期ステータス更新
    app._update_status_texts()
    page.update()

    # 背景タスク開始
    app.start_background_tasks()

    # 終了イベント登録
    page.on_close = app.on_close


if __name__ == "__main__":
    # Fletデスクトップとして起動
    ft.app(target=main, view=ft.AppView.FLET_APP)


# if __name__ == "__main__":
#     ft.app(target=main)
