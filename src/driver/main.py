import flet as ft
import threading
import time
import math
import atexit
import sys
from typing import Dict, List, Optional
from serial.tools import list_ports

import os, sys
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
from src.driver.ini_manager import (
    get_setting, set_setting,
    get_float_setting, get_int_setting, ensure_ini
)
from src.driver.image_cache import start_cache_process, temp_to_level
from src.rfid_rw.rfid_rw_wine7_uart_for_autoread_axzon_sensor import (
    rfid_rw_wine7_uart_for_autoread_axzon_sensor,
    memory_bank
)


# ユーティリティ
def list_com_devices() -> List[str]:
    return [p.device for p in list_ports.comports()]

def group_hex_bytes_no_space(hex_str: str) -> str:
    # "E283..." -> "E2 83 ..."
    s = hex_str.upper().replace(" ", "")
    return " ".join([s[i:i+2] for i in range(0, len(s), 2)])

def now_sec() -> float:
    return time.time()

class DriverMonitorApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "Seat Sensing"
        # self.page.window.width = 1920
        # self.page.window.height = 1080
        self.page.window.maximized = True
        self.page.theme_mode = ft.ThemeMode.LIGHT
        self.page.on_window_event = self.on_window_event
        self._cleaned = False
        atexit.register(self.cleanup)

        ensure_ini()

        # 状態
        self.rfid: Optional[rfid_rw_wine7_uart_for_autoread_axzon_sensor] = None
        self.connected = False
        self.monitoring_active = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.polling_ms = get_int_setting("Polling Interval (ms)", 1000)
        self.write_throttle = 1  # 1..5
        self.last_valid_send_to_hex = get_setting("Send to", "")
        if self.last_valid_send_to_hex is None:
            self.last_valid_send_to_hex = ""

        # 画像キャッシュ（別プロセス）
        self.cache_proc, self.image_cache = start_cache_process()

        # pubsub受信
        self.page.pubsub.subscribe(self.on_pubsub_message)

        # UI構築
        self.build_ui()
        self.page.update()

    def on_window_event(self, e: ft.WindowEvent):
        if e.event == ft.WindowEventType.CLOSE:
            self.cleanup()
            # ここで sys.exit() は不要。Flet がウィンドウを閉じてプロセスを終了させます。

    def cleanup(self):
        if getattr(self, "_cleaned", False):
            return
        self._cleaned = True
        # 監視停止とスレッドの終了待ち
        try:
            self.monitoring_active = False
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.monitor_thread.join(timeout=1.0)
        except Exception:
            pass
        # シリアルクローズ
        try:
            if self.rfid:
                self.rfid.close()
        except Exception:
            pass

    # pubsubメッセージハンドラ
    def on_pubsub_message(self, data):
        if not isinstance(data, dict):
            return
        msg_type = data.get("type")
        if msg_type == "conn":
            self.connected = data.get("connected", False)
            self.conn_status_text.value = "Connected." if self.connected else "Not connected."
            self.conn_status_text.color = ft.Colors.GREEN if self.connected else ft.Colors.GREY
            # ボタン有効/無効
            self.btn_update_epc.disabled = not self.connected
            self.btn_start_monitoring.disabled = not self.connected
            self.page.update()
        elif msg_type == "temps":
            # temps_by_seat: {FL:{Headrest:..., Backrest:..., Seat:...}, ...}
            temps_by_seat = data.get("temps_by_seat", {})
            ts = data.get("ts", now_sec())
            # UI更新（テキスト、画像、グラフ）
            self.update_temps_ui(temps_by_seat, ts)
            self.page.update()

    # UI構築
    def build_ui(self):
        # Tabs
        self.tabs = ft.Tabs(
            selected_index=0,
            tabs=[
                ft.Tab(text="Setting", icon=ft.Icons.SETTINGS, content=self.build_setting_tab()),
                ft.Tab(text="Seat sensing", icon=ft.Icons.MONITOR, content=self.build_seat_sensing_tab()),
            ],
            expand=True
        )
        self.page.add(self.tabs)

    # Settingタブ
    def build_setting_tab(self) -> ft.Control:
        # 左右50%をResponsiveRowで分割
        # 左レフトルート: RFID設定 + Criteria
        left_col = ft.Container(
            content=ft.Column(
                controls=[
                    self.build_rfid_rw_setting_container(),
                    self.build_criteria_container()
                ],
                spacing=10,
                tight=True
            ),
            padding=0
        )
        right_col = self.build_rfid_tag_setting_container()

        row = ft.ResponsiveRow(
            controls=[
                ft.Container(content=left_col, col={"xs": 12, "md": 6, "lg": 6}),
                ft.Container(content=right_col, col={"xs": 12, "md": 6, "lg": 6}),
            ],
            columns=12
        )
        return row

    def build_rfid_rw_setting_container(self) -> ft.Container:
        # COM, Output Power, Polling Interval, Update & Connect + 接続状態
        com_init = get_setting("COM", "")
        self.dd_com = ft.Dropdown(
            label="COM",
            options=[ft.dropdown.Option(c) for c in list_com_devices()],
            value=com_init if com_init in list_com_devices() else None,
            on_change=self.on_com_changed,
            width=300,
        )
        # Output Power (24..40)
        op_init = get_setting("Output Power (dBm)", "24")
        op_options = [str(i) for i in range(24, 41)]
        self.dd_output_power = ft.Dropdown(
            label="Output Power (dBm)",
            options=[ft.dropdown.Option(o) for o in op_options],
            value=op_init if op_init in op_options else op_options[0],
            on_change=self.on_output_power_changed,
            width=300,
        )
        # Polling Interval (ms)
        pi_init = get_setting("Polling Interval (ms)", "1000")
        pi_options = ["500", "1000", "2000", "3000", "4000", "5000"]
        self.dd_polling = ft.Dropdown(
            label="Polling Interval (ms)",
            options=[ft.dropdown.Option(o) for o in pi_options],
            value=pi_init if pi_init in pi_options else pi_options[1],
            on_change=self.on_polling_changed,
            width=300,
        )

        self.btn_update_com = ft.ElevatedButton(
            text="Update", icon=ft.Icons.REFRESH, on_click=self.on_update_com_clicked
        )
        self.btn_connect = ft.ElevatedButton(
            text="Connect", icon=ft.Icons.LINK, on_click=self.on_connect_clicked
        )
        self.conn_status_text = ft.Text(
            value="Not connected.", color=ft.Colors.GREY, size=14
        )
        button_row = ft.Row(
            controls=[self.btn_update_com, self.btn_connect, self.conn_status_text],
            spacing=10
        )

        container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("RFID Reader Writer Setting", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                    self.dd_com,
                    self.dd_output_power,
                    self.dd_polling,
                    button_row
                ],
                spacing=10
            ),
            padding=10,
            bgcolor=ft.Colors.BLUE_GREY_50,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=5,
            margin=ft.margin.only(top=10)
        )
        return container

    def on_com_changed(self, e: ft.ControlEvent):
        v = self.dd_com.value or ""
        set_setting("COM", v)

    def on_output_power_changed(self, e: ft.ControlEvent):
        v = self.dd_output_power.value or ""
        set_setting("Output Power (dBm)", v)

    def on_polling_changed(self, e: ft.ControlEvent):
        v = self.dd_polling.value or "1000"
        set_setting("Polling Interval (ms)", v)
        try:
            self.polling_ms = int(v)
        except Exception:
            self.polling_ms = 1000

    def on_update_com_clicked(self, e: ft.ControlEvent):
        ports = list_com_devices()
        self.dd_com.options = [ft.dropdown.Option(c) for c in ports]
        # 既存値がなければNone
        ini_val = get_setting("COM", "")
        self.dd_com.value = ini_val if ini_val in ports else None
        self.page.update()

    def on_connect_clicked(self, e: ft.ControlEvent):
        def worker():
            try:
                # すでに接続済みなら一度切断
                if self.rfid is not None:
                    try:
                        self.rfid.close()
                    except Exception:
                        pass
                    time.sleep(0.2)
                com = self.dd_com.value
                if not com:
                    # COM未選択
                    self.page.pubsub.send_all({"type": "conn", "connected": False})
                    return
                op = int(self.dd_output_power.value or "24")
                self.rfid = rfid_rw_wine7_uart_for_autoread_axzon_sensor(com, op)
                time.sleep(1.0)
                # 自動読取開始
                self.rfid.start_autoread_axzon_temperature_sensor()
                self.page.pubsub.send_all({"type": "conn", "connected": True})
            except Exception:
                # 失敗
                self.page.pubsub.send_all({"type": "conn", "connected": False})

        threading.Thread(target=worker, daemon=True).start()

    def build_criteria_container(self) -> ft.Container:
        # 初期値（空欄なら0）
        ac_on_init = get_float_setting("AC On Temperature (°C)", 0.0)
        ac_max_init = get_float_setting("AC Max. Temperature (°C)", 0.0)
        heater_on_init = get_float_setting("Heater On Temperature (°C)", 0.0)
        heater_max_init = get_float_setting("Heater Max. Temperature (°C)", 0.0)

        self.slider_ac_on = ft.Slider(min=0, max=40, divisions=400, value=ac_on_init, width=400, on_change=self.on_ac_on_changed, active_color=ft.Colors.GREEN, thumb_color=ft.Colors.GREY_100)
        self.text_ac_on = ft.Text(f"{self.slider_ac_on.value:.1f} °C", size=16, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD)

        self.slider_ac_max = ft.Slider(min=self.slider_ac_on.value, max=40, divisions=400, value=ac_max_init, width=400, on_change=self.on_ac_max_changed, active_color=ft.Colors.GREEN, thumb_color=ft.Colors.GREY_100)
        self.text_ac_max = ft.Text(f"{self.slider_ac_max.value:.1f} °C", size=16, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD)

        self.slider_heater_on = ft.Slider(min=0, max=self.slider_ac_on.value, divisions=400, value=heater_on_init, width=400, on_change=self.on_heater_on_changed, active_color=ft.Colors.GREEN, thumb_color=ft.Colors.GREY_100)
        self.text_heater_on = ft.Text(f"{self.slider_heater_on.value:.1f} °C", size=16, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD)

        self.slider_heater_max = ft.Slider(min=0, max=self.slider_heater_on.value, divisions=400, value=heater_max_init, width=400, on_change=self.on_heater_max_changed, active_color=ft.Colors.GREEN, thumb_color=ft.Colors.GREY_100)
        self.text_heater_max = ft.Text(f"{self.slider_heater_max.value:.1f} °C", size=16, color=ft.Colors.GREY_800, weight=ft.FontWeight.BOLD)

        col = ft.Column(
            controls=[
                ft.Row([ft.Container(content=ft.Text("AC On Temperature (°C)"), width=190), self.slider_ac_on, self.text_ac_on], spacing=10),
                ft.Row([ft.Container(content=ft.Text("AC Max. Temperature (°C)"), width=190), self.slider_ac_max, self.text_ac_max], spacing=10),
                ft.Row([ft.Container(content=ft.Text("Heater On Temperature (°C)"), width=190), self.slider_heater_on, self.text_heater_on], spacing=10),
                ft.Row([ft.Container(content=ft.Text("Heater Max. Temperature (°C)"), width=190), self.slider_heater_max, self.text_heater_max], spacing=10),
            ],
            spacing=10
        )
        container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("A/C Heater Control Setting", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                    col,
                ],
            ),
            padding=10,
            bgcolor=ft.Colors.BLUE_GREY_50,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=5,
        )
        return container

    def on_ac_on_changed(self, e: ft.ControlEvent):
        v = float(self.slider_ac_on.value or 0.0)
        self.text_ac_on.value = f"{v:.1f} °C"
        set_setting("AC On Temperature (°C)", f"{v:.1f}")
        # 他の制約更新
        self.slider_ac_max.min = v
        self.slider_heater_on.max = v
        self.slider_heater_max.max = self.slider_heater_on.value
        self.page.update()

    def on_ac_max_changed(self, e: ft.ControlEvent):
        v = float(self.slider_ac_max.value or 0.0)
        # AC On未満にできない
        if v < self.slider_ac_on.value:
            v = self.slider_ac_on.value
            self.slider_ac_max.value = v
        self.text_ac_max.value = f"{v:.1f} °C"
        set_setting("AC Max. Temperature (°C)", f"{v:.1f}")
        self.page.update()

    def on_heater_on_changed(self, e: ft.ControlEvent):
        v = float(self.slider_heater_on.value or 0.0)
        # AC On以上は選べない
        if v > self.slider_ac_on.value:
            v = self.slider_ac_on.value
            self.slider_heater_on.value = v
        self.text_heater_on.value = f"{v:.1f} °C"
        set_setting("Heater On Temperature (°C)", f"{v:.1f}")
        # Heater MaxはHeater On以下
        self.slider_heater_max.max = v
        if self.slider_heater_max.value > v:
            self.slider_heater_max.value = v
            self.text_heater_max.value = f"{v:.1f} °C"
            set_setting("Heater Max. Temperature (°C)", f"{v:.1f}")
        self.page.update()

    def on_heater_max_changed(self, e: ft.ControlEvent):
        v = float(self.slider_heater_max.value or 0.0)
        # Heater Onより大きくできない
        if v > self.slider_heater_on.value:
            v = self.slider_heater_on.value
            self.slider_heater_max.value = v
        self.text_heater_max.value = f"{v:.1f} °C"
        set_setting("Heater Max. Temperature (°C)", f"{v:.1f}")
        self.page.update()

    def build_rfid_tag_setting_container(self) -> ft.Container:
        # Update EPC Listボタン
        self.btn_update_epc = ft.ElevatedButton(text="Update EPC List", icon=ft.Icons.REFRESH, on_click=self.on_update_epc_clicked, disabled=not self.connected)

        # 各ゾーンのドロップダウンを準備（初期はUnselectedのみ）
        def make_zone_dd(prefix: str):
            return {
                "Headrest": ft.Dropdown(label=f"{prefix} Headrest", options=[ft.dropdown.Option("Unselected")], value=get_setting(f"{prefix} Headrest", "") or "Unselected", on_change=lambda e, p=prefix, k="Headrest": self.on_epc_dd_changed(p, k),width=300),
                "Backrest": ft.Dropdown(label=f"{prefix} Backrest", options=[ft.dropdown.Option("Unselected")], value=get_setting(f"{prefix} Backrest", "") or "Unselected", on_change=lambda e, p=prefix, k="Backrest": self.on_epc_dd_changed(p, k),width=300),
                "Seat": ft.Dropdown(label=f"{prefix} Seat", options=[ft.dropdown.Option("Unselected")], value=get_setting(f"{prefix} Seat", "") or "Unselected", on_change=lambda e, p=prefix, k="Seat": self.on_epc_dd_changed(p, k),width=300),
            }

        self.zone_dd = {
            "FL": make_zone_dd("FL"),
            "FR": make_zone_dd("FR"),
            "RL": make_zone_dd("RL"),
            "RR": make_zone_dd("RR"),
        }

        # Divider、Send toテキストフィールド
        send_to_init = get_setting("Send to", "")
        self.tf_send_to = ft.TextField(
            label="Send to",
            value=group_hex_bytes_no_space(send_to_init) if send_to_init else "",
            tooltip="Input MR793200's EPC",
            on_change=self.on_send_to_changed,
            width=400,
        )

        # Start monitoring + Throttle
        self.dd_throttle = ft.Dropdown(
            label="Write throttle (x)",
            options=[ft.dropdown.Option(str(i)) for i in range(1, 6)],
            value="1",
            on_change=self.on_throttle_changed,
            width=200,
        )
        self.btn_start_monitoring = ft.ElevatedButton(text="Start monitoring", icon=ft.Icons.PLAY_CIRCLE, on_click=self.on_start_monitoring_clicked, disabled=not self.connected)

        # レイアウト: FL/FR、RL/RRコンテナを横並び
        fl_fr_row = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Column(controls=[ft.Text("FL", size=16, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), self.zone_dd["FL"]["Headrest"], self.zone_dd["FL"]["Backrest"], self.zone_dd["FL"]["Seat"]], spacing=10),
                    width=None,
                    border=ft.border.all(1, ft.Colors.GREY_400),
                    border_radius=5,
                    padding=10,
                ),
                ft.Container(
                    content=ft.Column(controls=[ft.Text("FR", size=16, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), self.zone_dd["FR"]["Headrest"], self.zone_dd["FR"]["Backrest"], self.zone_dd["FR"]["Seat"]], spacing=10),
                    width=None,
                    border=ft.border.all(1, ft.Colors.GREY_400),
                    border_radius=5,
                    padding=10,
                ),
            ],
            spacing=10
        )
        rl_rr_row = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Column(controls=[ft.Text("RL", size=16, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), self.zone_dd["RL"]["Headrest"], self.zone_dd["RL"]["Backrest"], self.zone_dd["RL"]["Seat"]], spacing=10),
                    width=None,
                    border=ft.border.all(1, ft.Colors.GREY_400),
                    border_radius=5,
                    padding=10,
                ),
                ft.Container(
                    content=ft.Column(controls=[ft.Text("RR", size=16, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD), self.zone_dd["RR"]["Headrest"], self.zone_dd["RR"]["Backrest"], self.zone_dd["RR"]["Seat"]], spacing=10),
                    width=None,
                    border=ft.border.all(1, ft.Colors.GREY_400),
                    border_radius=5,
                    padding=10,
                ),
            ],
            spacing=10
        )

        container = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Text("RFID Tag Setting", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                    self.btn_update_epc,
                    fl_fr_row,
                    rl_rr_row,
                    ft.Divider(),
                    self.tf_send_to,
                    ft.Divider(),
                    ft.Row(controls=[self.btn_start_monitoring, self.dd_throttle], spacing=10),
                ],
                spacing=10
            ),
            padding=10,
            bgcolor=ft.Colors.BLUE_GREY_50,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=5,
            margin=ft.margin.only(top=10)
        )
        return container

    def on_update_epc_clicked(self, e: ft.ControlEvent):
        if not self.connected or self.rfid is None:
            return
        try:
            epcs = self.rfid.get_epc_list() or []
        except Exception:
            epcs = []
        # ここで降順ソート（重複は維持）
        epcs_sorted = sorted(epcs)

        options = [ft.dropdown.Option("Unselected")] + [ft.dropdown.Option(epc) for epc in epcs_sorted]
        for prefix in ["FL", "FR", "RL", "RR"]:
            for part in ["Headrest", "Backrest", "Seat"]:
                dd = self.zone_dd[prefix][part]
                dd.options = options
                # 既存のINI値を維持（存在しないならUnselected）
                ini_val = get_setting(f"{prefix} {part}", "")
                dd.value = ini_val if ini_val and (ini_val == "Unselected" or ini_val in epcs) else "Unselected"
        self.page.update()

    def on_epc_dd_changed(self, zone_prefix: str, part: str):
        dd = self.zone_dd[zone_prefix][part]
        val = dd.value or ""
        set_setting(f"{zone_prefix} {part}", "" if val == "Unselected" else val)

    def on_send_to_changed(self, e: ft.ControlEvent):
        raw = self.tf_send_to.value or ""
        # 入力検証：大文字16進（A-F,0-9）とスペースのみ許可
        cleaned = raw.replace(" ", "").upper()
        if any(c for c in cleaned if c not in "0123456789ABCDEF"):
            # 無効文字は受け付けない
            self.tf_send_to.error_text = "Only hexadecimal numbers are valid."
            # 現在値を直前の有効値に戻す
            self.tf_send_to.value = group_hex_bytes_no_space(self.last_valid_send_to_hex) if self.last_valid_send_to_hex else ""
            self.page.update()
            return
        # エラーなし、2桁毎にスペース入れ
        self.tf_send_to.error_text = None
        # 長さチェック（偶数桁）
        if len(cleaned) % 2 != 0:
            self.tf_send_to.error_text = "Invalid length."
        else:
            self.last_valid_send_to_hex = cleaned
            set_setting("Send to", cleaned)
        self.tf_send_to.value = group_hex_bytes_no_space(cleaned)
        self.page.update()

    def on_throttle_changed(self, e: ft.ControlEvent):
        try:
            self.write_throttle = int(self.dd_throttle.value or "1")
        except Exception:
            self.write_throttle = 1

    # Seat sensingタブ
    def build_seat_sensing_tab(self) -> ft.Control:
        # Upper: 2x2 グリッド風（GridViewは使わない）
        self.zone_views = {}
        def make_zone_view(prefix: str):
            # テキスト
            title = ft.Text(prefix, size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD)
            # Stack: 300x300
            img_seat = ft.Image(src="src/driver/img/seat.png", width=300, height=300)
            img_back = ft.Image(src="src/driver/img/backrest.png", width=300, height=300)
            img_head = ft.Image(src="src/driver/img/headrest.png", width=300, height=300)
            img_seat.tag = "seat"
            img_back.tag = "backrest"
            img_head.tag = "headrest"
            img_seat.base_src = "src/driver/img/seat.png"
            img_back.base_src = "src/driver/img/backrest.png"
            img_head.base_src = "src/driver/img/headrest.png"
            stack = ft.Stack(controls=[img_seat, img_back, img_head], width=300, height=300)

            # 温度テキスト
            t_head = ft.Text("Headrest: N/A")
            t_back = ft.Text("Backrest: N/A")
            t_seat = ft.Text("Seat: N/A")

            # LineChart
            lc = ft.LineChart(
                data_series=[
                    ft.LineChartData(data_points=[], color="#404040", stroke_width=1, curved=False),  # Headrest
                    ft.LineChartData(data_points=[], color="#FF3300", stroke_width=1, curved=False),  # Backrest
                    ft.LineChartData(data_points=[], color="#0066FF", stroke_width=1, curved=False),  # Seat
                ],
                border=ft.border.all(1, ft.Colors.GREY_600),
                min_x=0, max_x=600, min_y=0, max_y=60,
                left_axis=ft.ChartAxis(
                    title=ft.Container(
                        content=ft.Text("Temperature [°C]", size=16),
                        margin=ft.margin.only(bottom=6, left=30),
                    ),
                    title_size=24,
                    labels=[ft.ChartAxisLabel(value=v, label=ft.Text(str(int(v)))) for v in [0,10,20,30,40,50,60]]
                ),
                horizontal_grid_lines=ft.ChartGridLines(
                    interval=10,
                    color=ft.Colors.GREY_400,
                    width=0.5,
                ),
                bottom_axis=ft.ChartAxis(
                    title=ft.Container(
                        content=ft.Text("Time [s]", size=16),
                        # margin=ft.margin.only(bottom=1),
                    ),
                    title_size=24,
                    labels=[ft.ChartAxisLabel(value=v, label=ft.Text(str(int(v)))) for v in [0,100,200,300,400,500,600]]
                ),
                vertical_grid_lines=ft.ChartGridLines(
                    interval=60,
                    color=ft.Colors.GREY_400,
                    width=0.5,
                ),
                width=600,
                height=280,
                expand=True,
            )
            return {
                "title": title,
                "img_seat": img_seat,
                "img_back": img_back,
                "img_head": img_head,
                "stack": stack,
                "t_head": t_head,
                "t_back": t_back,
                "t_seat": t_seat,
                "lc": lc,
                "x_min": 0.0,
                "x_max": 600.0,
            }

        for z in ["FL", "FR", "RL", "RR"]:
            self.zone_views[z] = make_zone_view(z)

        # 上段: FL, FR / 下段: RL, RR
        def zone_container(z):
            v = self.zone_views[z]
            legend_head = ft.Row(
                controls=[
                    ft.Container(width=45),
                    ft.Icon(ft.Icons.HORIZONTAL_RULE, color="#404040"),
                    v["t_head"]
                ]
            )
            legend_back = ft.Row(
                controls=[
                    ft.Container(width=45),
                    ft.Icon(ft.Icons.HORIZONTAL_RULE, color="#FF3300"),
                    v["t_back"]
                ]
            )
            legend_seat = ft.Row(
                controls=[
                    ft.Container(width=45),
                    ft.Icon(ft.Icons.HORIZONTAL_RULE, color="#0066FF"),
                    v["t_seat"]
                ]
            )
            content = ft.Column(
                controls=[
                    v["title"],
                    ft.Row(controls=[v["stack"], ft.Column(controls=[legend_head, legend_back, legend_seat, v["lc"]], spacing=5)], spacing=10),
                ],
                spacing=10
            )
            return ft.Container(
                content=content,
                padding=10,
                bgcolor=ft.Colors.BLUE_GREY_50,
                border=ft.border.all(1, ft.Colors.GREY_300),
                border_radius=5,
                width=940,
                height=420,
            )

        upper = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(controls=[zone_container("FL"), zone_container("FR")], spacing=5),
                    ft.Row(controls=[zone_container("RL"), zone_container("RR")], spacing=5),
                ],
                spacing=5
            ),
            margin=ft.margin.only(top=10),
        )

        # Lower: Restart X axis at 0
        self.btn_restart_x = ft.ElevatedButton(text="Restart X axis at 0", icon=ft.Icons.REPLAY, on_click=self.on_restart_x_clicked)

        # 高さ比 23:2 は、上部に比率を寄せた見た目で再現（Containerで目安）
        upper_container = ft.Container(content=upper)
        lower_container = ft.Container(content=ft.Row(controls=[self.btn_restart_x]), padding=10)

        return ft.Column(controls=[upper_container, lower_container], spacing=10)

    def on_restart_x_clicked(self, e: ft.ControlEvent):
        # タイムスタンプ基準をリセット、過去データ削除
        for z in ["FL", "FR", "RL", "RR"]:
            view = self.zone_views[z]
            # 3系列をクリア
            for ds in view["lc"].data_series:
                ds.data_points.clear()
            view["x_min"] = 0.0
            view["x_max"] = 600.0
            view["lc"].min_x = 0.0
            view["lc"].max_x = 600.0
            view["lc"].bottom_axis = ft.ChartAxis(
                title=ft.Text("Time [s]", size=16),
                title_size=24,
                labels=[ft.ChartAxisLabel(value=v, label=ft.Text(str(int(v)))) for v in [0,100,200,300,400,500,600]]
            )
        # 内部開始時間を再設定
        self.monitor_start_time = now_sec()
        self.page.update()

    # temps UI更新
    def update_temps_ui(self, temps_by_seat: Dict[str, Dict[str, Optional[float]]], ts: float):
        # テキスト、画像、グラフ
        # 開始時間初期化
        if not hasattr(self, "monitor_start_time") or self.monitor_start_time is None:
            self.monitor_start_time = ts
        x = ts - self.monitor_start_time
        # 軸シフト: 600超過なら300秒分右へ（0-600 -> 300-900 -> ...）
        def shift_axis_if_needed(view):
            if x > view["x_max"]:
                view["x_min"] += 300.0
                view["x_max"] += 300.0
                view["lc"].min_x = view["x_min"]
                view["lc"].max_x = view["x_max"]
                # 目盛更新
                ticks = [view["x_min"] + i*100 for i in range(0, int((view["x_max"]-view["x_min"])//100)+1)]
                view["lc"].bottom_axis = ft.ChartAxis(
                    title=ft.Text("Time [s]"),
                    labels=[ft.ChartAxisLabel(value=v, label=ft.Text(str(int(v)))) for v in ticks[:7]]  # 7つ程度
                )
                # 古い点は削除
                for ds in view["lc"].data_series:
                    ds.data_points[:] = [p for p in ds.data_points if p.x >= view["x_min"]]

        for z in ["FL", "FR", "RL", "RR"]:
            view = self.zone_views[z]
            head = temps_by_seat.get(z, {}).get("Headrest")
            back = temps_by_seat.get(z, {}).get("Backrest")
            seat = temps_by_seat.get(z, {}).get("Seat")

            # テキスト
            view["t_head"].value = f"Headrest: {head:.1f} °C" if head is not None else "Headrest: N/A"
            view["t_back"].value = f"Backrest: {back:.1f} °C" if back is not None else "Backrest: N/A"
            view["t_seat"].value = f"Seat: {seat:.1f} °C" if seat is not None else "Seat: N/A"

            # 画像
            # levelがNoneなら画像変更しない（初期は空のまま）
            def set_img(img_ctrl, level):
                # キャッシュ未準備 or 温度がN/Aなら素のPNGを表示
                try:
                    cache_ready = bool(self.image_cache.get("__ready__", False))
                except Exception:
                    cache_ready = False

                if (not cache_ready) or (level is None):
                    # 素のPNGに戻す（src_base64は解除）
                    img_ctrl.src = getattr(img_ctrl, "base_src", None)
                    img_ctrl.src_base64 = None
                    return

                # 色付きキャッシュがある場合のみ置換
                try:
                    b64 = self.image_cache.get(img_ctrl.tag, {}).get(level)
                    if b64:
                        # base_srcは維持、表示のみsrc_base64へ切り替え（srcは解除）
                        img_ctrl.src = None
                        img_ctrl.src_base64 = b64
                except Exception:
                    # 取得失敗時は素のPNGにフォールバック
                    img_ctrl.src = getattr(img_ctrl, "base_src", None)
                    img_ctrl.src_base64 = None

            set_img(view["img_seat"], temp_to_level(seat))
            set_img(view["img_back"], temp_to_level(back))
            set_img(view["img_head"], temp_to_level(head))

            # グラフ
            shift_axis_if_needed(view)
            # 各系列に追加（Noneは追加しない）
            if head is not None:
                view["lc"].data_series[0].data_points.append(ft.LineChartDataPoint(x=x, y=float(f"{head:.1f}")))
            if back is not None:
                view["lc"].data_series[1].data_points.append(ft.LineChartDataPoint(x=x, y=float(f"{back:.1f}")))
            if seat is not None:
                view["lc"].data_series[2].data_points.append(ft.LineChartDataPoint(x=x, y=float(f"{seat:.1f}")))

    # Start monitoring
    def on_start_monitoring_clicked(self, e: ft.ControlEvent):
        if not self.connected or self.rfid is None:
            return
        self.monitoring_active = True
        # タブをSeat sensingへ
        self.tabs.selected_index = 1
        self.page.update()
        # 監視スレッド開始
        threading.Thread(target=self.monitor_loop, daemon=True).start()

    def monitor_loop(self):
        tick = 0
        while self.monitoring_active:
            ts = now_sec()
            try:
                # 温度取得
                temps_dict = {}
                if self.rfid:
                    # rfid.get_temperature() は {epc: {"timestamp":..., "value":...}} を返す
                    td = self.rfid.get_temperature()
                else:
                    td = {}
                # ドロップダウン選択から各ゾーンの温度抽出
                def get_temp_from_dd(val: str, ts_now: float) -> Optional[float]:
                    if not val or val == "Unselected":
                        return None
                    entry = td.get(val)
                    if not entry:
                        return None
                    # ここで「古いデータ」をN/A扱いにする（例: ポーリング3周期分を閾値）
                    age = ts_now - float(entry.get("timestamp", 0.0))
                    stale_sec = max(1.0, (self.polling_ms / 1000.0) * 3)
                    if age > stale_sec:
                        return None
                    v = entry.get("value")
                    if v is None:
                        return None
                    return round(float(v), 1)

                for z in ["FL", "FR", "RL", "RR"]:
                    temps_dict[z] = {
                        "Headrest": get_temp_from_dd(get_setting(f"{z} Headrest", ""), ts),
                        "Backrest": get_temp_from_dd(get_setting(f"{z} Backrest", ""), ts),
                        "Seat": get_temp_from_dd(get_setting(f"{z} Seat", ""), ts),
                    }

                # UIへ通知（Polling Interval毎に1回、page.updateもこのタイミング）
                self.page.pubsub.send_all({"type": "temps", "temps_by_seat": temps_dict, "ts": ts})

                # 書き込み（スロットリング）
                throttle = self.write_throttle if self.write_throttle >= 1 else 1
                if tick % throttle == 0:
                    self.perform_access_write(temps_dict)
                    # 書込後は自動読取再開
                    try:
                        self.rfid.start_autoread_axzon_temperature_sensor()
                    except Exception:
                        pass

                tick += 1
            except Exception:
                pass
            time.sleep(self.polling_ms / 1000.0)

    def perform_access_write(self, temps_dict: Dict[str, Dict[str, Optional[float]]]):
        # Send toが空なら何もしない
        send_to_hex = get_setting("Send to", "")
        if not send_to_hex:
            return
        send_to_spaced = group_hex_bytes_no_space(send_to_hex)

        ac_on = get_float_setting("AC On Temperature (°C)", 0.0)
        ac_max = get_float_setting("AC Max. Temperature (°C)", 0.0)
        heater_on = get_float_setting("Heater On Temperature (°C)", 0.0)
        heater_max = get_float_setting("Heater Max. Temperature (°C)", 0.0)

        def ac_fan_level(seat_temp: Optional[float]) -> int:
            # 未取得は0
            if seat_temp is None:
                return 0
            if seat_temp < ac_on:
                # AC On未満は0（Heater On〜AC Onはオフ）
                return 0
            # 分割: (ac_max - ac_on) / 10
            div = (ac_max - ac_on) / 10.0
            if div <= 0.0:
                # ac_max <= ac_on の場合、閾値以上は10、未満は0
                return 10 if seat_temp >= ac_max else 0
            step = int(math.floor((min(seat_temp, ac_max) - ac_on) / div)) + 1
            if seat_temp >= ac_max:
                step = 10
            return max(1, min(10, step))

        def heater_fan_level(seat_temp: Optional[float]) -> int:
            # 未取得は10段階目の扱いになるが、Seat Heaterでは未取得はオフ扱いなのでここでは0にする
            if seat_temp is None:
                return 0
            if seat_temp > heater_on:
                return 0
            div = (heater_on - heater_max) / 10.0
            if div <= 0.0:
                return 10 if seat_temp <= heater_max else 0
            step = int(math.floor((heater_on - max(seat_temp, heater_max)) / div)) + 1
            if seat_temp <= heater_max:
                step = 10
            return max(1, min(10, step))

        # h00: FL,FR,RL,RRのAC Fan Level（Heater領域/未取得は0）
        nibbles_h00 = []
        # for z in ["FL", "FR", "RL", "RR"]:
        #     seat_temp = temps_dict[z]["Seat"]
        #     level = ac_fan_level(seat_temp)
        #     nibbles_h00.append(level & 0xF)
        # word_h00 = (nibbles_h00[3] << 12) | (nibbles_h00[2] << 8) | (nibbles_h00[1] << 4) | nibbles_h00[0]

###
        for z in ["FL", "FR", "RL", "RR"]:
            t = temps_dict[z]["Seat"]
            if t is None:
                lvl = 0                      # 未取得はオフ
            elif t >= ac_on:
                lvl = ac_fan_level(t)        # AC側 1..10
            elif t <= heater_on:
                lvl = heater_fan_level(t)    # Heater側 1..10
            else:
                lvl = 0                      # Heater On ～ AC On の間はオフ
            nibbles_h00.append(lvl & 0xF)
        word_h00 = (nibbles_h00[3] << 12) | (nibbles_h00[2] << 8) | (nibbles_h00[1] << 4) | nibbles_h00[0]
###
        # h01: Seat Heater Levelの4段階マップ
        nibbles_h01 = []
        for z in ["FL", "FR", "RL", "RR"]:
            seat_temp = temps_dict[z]["Seat"]
            flv = heater_fan_level(seat_temp)
            if flv == 0:
                sl = 0x0
            elif 1 <= flv <= 5:
                sl = 0x1
            elif 6 <= flv <= 9:
                sl = 0x2
            elif flv == 10:
                sl = 0x3
            else:
                sl = 0x0
            nibbles_h01.append(sl & 0xF)
        word_h01 = (nibbles_h01[3] << 12) | (nibbles_h01[2] << 8) | (nibbles_h01[1] << 4) | nibbles_h01[0]

        def word_to_hex_be(word: int) -> str:
            hi = (word >> 8) & 0xFF
            lo = word & 0xFF
            return f"{hi:02x} {lo:02x}"

        def word_to_hi_lo(word: int) -> tuple[int, int]:
            return (word >> 8) & 0xFF, word & 0xFF

        hi00, lo00 = word_to_hi_lo(word_h00)  # h00 をビッグエンディアン
        hi01, lo01 = word_to_hi_lo(word_h01)  # h01 をビッグエンディアン

        # 2ワード連結（各ワードはBE: 上位→下位。連続順は h00, h01）
        data_4bytes = f"{hi00:02x} {lo00:02x} {hi01:02x} {lo01:02x}"

        # 書き込み2ワード
        try:
            # # h00
            # self.rfid.access_write(send_to_spaced, memory_bank.USER, 0, word_to_hex_be(word_h00))
            # # h01
            # self.rfid.access_write(send_to_spaced, memory_bank.USER, 1, word_to_hex_be(word_h01))
            self.rfid.access_write(send_to_spaced, memory_bank.USER, 0, data_4bytes)
            print(f"access_write({send_to_spaced}, {memory_bank.USER.value}, 0, {data_4bytes})")
        except Exception:
            pass

def main(page: ft.Page):
    app = DriverMonitorApp(page)

if __name__ == "__main__":
    ft.app(target=main)
