# /src/battery/main.py
# Python 3.11
# Flet 0.28.3

import flet as ft
import sys
import re
import time
import threading
import multiprocessing
from multiprocessing import Queue as MPQueue
from pathlib import Path
import configparser
from datetime import datetime

# 依存モジュールインポート準備（/src を import path に追加）
BASE_DIR = Path(__file__).resolve().parents[1]  # .../src
PROJECT_ROOT = Path(__file__).resolve().parents[2]  # プロジェクトルート
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

# RFIDモジュール
from rfid_rw.rfid_rw_wine7_uart_for_autoread_axzon_sensor import (
    rfid_rw_wine7_uart_for_autoread_axzon_sensor,
    memory_bank,
)

# COMポート列挙
from serial.tools import list_ports

# 定数定義
INI_PATH = PROJECT_ROOT / "src" / "battery" / "battery_monitor_initial_data.ini"
LOG_DIR = PROJECT_ROOT / "src" / "battery" / "log"
BATTERY_IMAGE_PATH = PROJECT_ROOT / "src" / "battery" / "img" / "battery.png"

# INI テンプレート（空欄）を作成
def ensure_ini_exists():
    if INI_PATH.exists():
        return
    INI_PATH.parent.mkdir(parents=True, exist_ok=True)
    content = """[setting]
com = 
output power (dBm) = 
polling interval (ms) = 

Maximum Operating Temperature (°C) = 
Resume Temperature (°C) = 
Tolerance Time (s) = 

No. 1 = 
No. 2 = 
No. 3 = 
No. 4 = 
No. 5 = 
No. 6 = 
No. 7 = 
No. 8 = 
No. 9 = 
No. 10 = 
No. 11 = 
No. 12 = 
No. 13 = 
No. 14 = 
No. 15 = 
No. 16 = 

Send to = 
"""
    with open(INI_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

# INI 読み書き
def load_ini():
    ensure_ini_exists()
    cp = configparser.ConfigParser()
    # 設定キーにスペースが含まれるため、optionxformを無効化
    cp.optionxform = str
    cp.read(INI_PATH, encoding="utf-8")
    if "setting" not in cp:
        cp["setting"] = {}
    return cp

def save_ini_value(key: str, value: str):
    cp = load_ini()
    cp["setting"][key] = value
    with open(INI_PATH, "w", encoding="utf-8", newline="\n") as f:
        cp.write(f)

# HEX文字列ユーティリティ
HEX_RE = re.compile(r"^[0-9A-F]*$")

def sanitize_hex_upper_no_spaces(s: str) -> str:
    # 無効文字は受け付けないで削除する（英数字16進のみ許可）
    s = s.upper()
    s = re.sub(r"[^0-9A-F]", "", s)
    return s

def insert_space_every_two_chars(s: str) -> str:
    return " ".join([s[i : i + 2] for i in range(0, len(s), 2)])

# ログワーカープロセス
def log_worker(queue: MPQueue):
    # ディレクトリ作成
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        item = queue.get()
        if item is None:
            break
        # item: (no_index, count, temp_value, timestamp)
        no_i, count, temp_value, ts = item
        # ファイル名
        log_path = LOG_DIR / f"reuse_no{no_i}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # フォーマット: "YYYY-MM-DD HH:MM:SS, No. X, count=N, temp=TT.T"
        dt_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        line = f"{dt_str}, No. {no_i}, count={count}, temp={temp_value:.1f}"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

# 温度データ構造
class BatteryState:
    def __init__(self, count=0):
        self.exceed_start_ts = None  # 連続超過開始時刻
        self.exceed_count = count    # ログカウント
        self.switch_on = True        # 初期ON

# メインFletアプリ
def main(page: ft.Page):
    page.title = "EV Battery Monitoring"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.scroll = ft.ScrollMode.AUTO
    page.window.maximized = True

    # pubsub 購読
    def on_pubsub_message(msg):
        # msgはdict。タイプで分岐
        mtype = msg.get("type")
        if mtype == "conn_state":
            connected = msg.get("connected", False)
            conn_text.value = "Connected." if connected else "Not connected."
            conn_text.color = ft.Colors.GREEN if connected else ft.Colors.GREY
            update_epc_btn.disabled = not connected
            start_monitoring_btn.disabled = not connected
            page.update()
        elif mtype == "monitor_tick":
            # UI更新
            temps = msg.get("temps", [0.0] * 16)
            # 温度テキスト & スタイル更新
            for i in range(16):
                t = temps[i]
                battery_temp_texts[i].value = f"{t:.1f}°C"
                # スタイル: 最大温度を超えたら赤ボールド
                if t > max_temp_state():
                    battery_temp_texts[i].color = ft.Colors.RED
                    battery_temp_texts[i].weight = ft.FontWeight.BOLD
                else:
                    battery_temp_texts[i].color = ft.Colors.BLACK
                    battery_temp_texts[i].weight = ft.FontWeight.NORMAL
                if not epc_selections[i] and battery_switches[i].value:
                    battery_switches[i].value = False

            # LineChart 更新（series visibility考慮）
            now_elapsed = msg.get("elapsed_s", 0.0)
            # X軸シフト: 600超えで300シフト
            # 既に監視スレッド側で削減済みだが、ここでは再構築
            rebuild_linechart_series()
            update_axes()
            chart.update()
            page.update()
        elif mtype == "linechart_reset":
            x_axis_reset_requested["val"] = True
            # チャートリセット
            clear_chart_data()
            update_axes()
            chart.update()
            page.update()
        elif mtype == "error":
            # 必要ならトースト等
            page.snack_bar = ft.SnackBar(ft.Text(msg.get("message", "Error")), bgcolor=ft.Colors.RED)
            page.snack_bar.open = True
            page.update()

    page.pubsub.subscribe(on_pubsub_message)

    # 共有状態
    cp = load_ini()
    setting = cp["setting"]

    # 初期値取り出し
    ini_com = setting.get("com", "")
    ini_output_power = setting.get("output power (dBm)", "30")
    ini_poll_interval_ms = setting.get("polling interval (ms)", "500")

    ini_max_temp = setting.get("Maximum Operating Temperature (°C)", "60")
    ini_resume_temp = setting.get("Resume Temperature (°C)", "50")
    ini_tolerance_sec = setting.get("Tolerance Time (s)", "5")

    ini_no_epcs = [setting.get(f"No. {i+1}", "") for i in range(16)]
    ini_send_to = setting.get("Send to", "")

    # 接続関連
    rfid = {"obj": None}
    connected_flag = {"val": False}

    # 監視関連
    monitoring_flag = {"val": False}
    monitoring_thread = {"obj": None}
    write_thread_lock = threading.Lock()
    write_in_progress = {"val": False}
    write_throttle_x = {"val": 1}  # スロットリング係数（UIで変更可能にする）

    # ログプロセス
    log_queue = MPQueue()
    log_proc = multiprocessing.Process(target=log_worker, args=(log_queue,), daemon=True)
    log_proc.start()

    # Battery状態（16個）
    battery_states = [BatteryState(count=0) for _ in range(16)]

    # EPC選択状態（RFIDタグ設定のドロップダウン）
    epc_selections = ini_no_epcs[:]  # 16
    epc_options = [["Unselected"] for _ in range(16)]  # 初期は Unselected のみ

    # グラフデータ保持
    # series_data[i] = list of (x, y)
    series_data = [[] for _ in range(16)]
    chart_x_min = {"val": 0.0}
    chart_x_max = {"val": 600.0}
    chart_start_ts = {"val": None}
    chart_visible = [True] * 16  # グラフ表示/非表示
    series_specs = [
        {"color": "#404040", "dash": None},
        {"color": "#FF3300", "dash": None},
        {"color": "#FF9900", "dash": None},
        {"color": "#FFCC00", "dash": None},
        {"color": "#33CC33", "dash": None},
        {"color": "#00CCFF", "dash": None},
        {"color": "#0066FF", "dash": None},
        {"color": "#9933FF", "dash": None},
        {"color": "#404040", "dash": [4, 2]},
        {"color": "#FF3300", "dash": [4, 2]},
        {"color": "#FF9900", "dash": [4, 2]},
        {"color": "#FFCC00", "dash": [4, 2]},
        {"color": "#33CC33", "dash": [4, 2]},
        {"color": "#00CCFF", "dash": [4, 2]},
        {"color": "#0066FF", "dash": [4, 2]},
        {"color": "#9933FF", "dash": [4, 2]},
    ]
    x_axis_reset_requested = {"val": False}

    # ヘルパー: 現在の設定値取得
    def max_temp_state() -> float:
        cp2 = load_ini()
        return float(cp2["setting"].get("Maximum Operating Temperature (°C)", "60"))

    def resume_temp_state() -> float:
        cp2 = load_ini()
        return float(cp2["setting"].get("Resume Temperature (°C)", "50"))

    def tolerance_sec_state() -> float:
        cp2 = load_ini()
        return float(cp2["setting"].get("Tolerance Time (s)", "5"))

    def poll_interval_ms_state() -> int:
        cp2 = load_ini()
        return int(cp2["setting"].get("polling interval (ms)", "500"))

    # 接続状態テキスト（設定タブ）
    conn_text = ft.Text(
        value="Not connected.",
        color=ft.Colors.GREY,
        size=12,
    )

    # ===== 設定タブ UI =====

    # COMポート一覧取得
    def list_com_ports():
        return [p.device for p in list_ports.comports()]

    # RFIDリーダライタ設定コンテナ
    com_dropdown = ft.Dropdown(
        label="COM",
        value=ini_com if ini_com else None,
        options=[ft.dropdown.Option(p) for p in list_com_ports()],
        on_change=lambda e: save_ini_value("com", e.control.value or ""),
        width=300,
    )

    output_power_dropdown = ft.Dropdown(
        label="Output Power (dBm)",
        value=ini_output_power if ini_output_power else None,
        options=[ft.dropdown.Option(str(v)) for v in range(24, 41)],
        on_change=lambda e: save_ini_value("output power (dBm)", e.control.value or ""),
        width=300,
    )

    poll_interval_dropdown = ft.Dropdown(
        label="Polling Interval (ms)",
        value=ini_poll_interval_ms if ini_poll_interval_ms else None,
        options=[ft.dropdown.Option(str(v)) for v in [500, 1000, 2000, 3000, 4000, 5000]],
        on_change=lambda e: save_ini_value("polling interval (ms)", e.control.value or ""),
        width=300,
    )

    def on_com_update_click(_):
        com_dropdown.options = [ft.dropdown.Option(p) for p in list_com_ports()]
        page.update()

    update_com_btn = ft.ElevatedButton(
        "Update",
        icon=ft.Icons.REFRESH,
        on_click=on_com_update_click,
    )

    def connect_worker(com_port: str, power_dbm: int):
        try:
            # 再接続時はクローズ
            if rfid["obj"] is not None:
                try:
                    rfid["obj"].close()
                except Exception:
                    pass
                rfid["obj"] = None
                connected_flag["val"] = False
                page.pubsub.send_all({"type": "conn_state", "connected": False})
            # インスタンス生成
            rfid["obj"] = rfid_rw_wine7_uart_for_autoread_axzon_sensor(com=com_port, tx_power_db=float(power_dbm))
            # 1秒バックグラウンド待機
            time.sleep(1.0)
            # 自動読み取り開始
            rfid["obj"].start_autoread_axzon_temperature_sensor()
            connected_flag["val"] = True
            page.pubsub.send_all({"type": "conn_state", "connected": True})
        except Exception as ex:
            connected_flag["val"] = False
            page.pubsub.send_all({"type": "conn_state", "connected": False})
            page.pubsub.send_all({"type": "error", "message": f"Failed to connect: {ex}"})

    def on_connect_click(_):
        cp2 = load_ini()
        com = cp2["setting"].get("com", "")
        power = cp2["setting"].get("output power (dBm)", "30")
        if not com or not power:
            page.pubsub.send_all({"type": "error", "message": "COM or Output Power is not set."})
            return
        threading.Thread(target=connect_worker, args=(com, int(power)), daemon=True).start()

    connect_btn = ft.ElevatedButton(
        "Connect",
        icon=ft.Icons.LINK,
        on_click=on_connect_click,
    )

    rfid_reader_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("RFID Reader Writer Setting", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                com_dropdown,
                output_power_dropdown,
                poll_interval_dropdown,
                ft.Row(controls=[update_com_btn, connect_btn, conn_text], spacing=10),
            ],
            spacing=10,
            tight=True,
        ),
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
        margin=ft.margin.only(top=10),
    )

    # 判定条件コンテナ
    max_temp_dropdown = ft.Dropdown(
        label="Maximum Operating Temperature (°C)",
        value=ini_max_temp if ini_max_temp else None,
        options=[ft.dropdown.Option(str(v)) for v in range(30, 101)],
        on_change=lambda e: (
            save_ini_value("Maximum Operating Temperature (°C)", e.control.value or ""),
            update_resume_options()
        ),
        width=300,
    )

    def update_resume_options():
        cp2 = load_ini()
        mt = int(cp2["setting"].get("Maximum Operating Temperature (°C)", "60"))
        rt_val = cp2["setting"].get("Resume Temperature (°C)", "")
        resume_temp_dropdown.options = [ft.dropdown.Option(str(v)) for v in range(0, mt + 1)]
        # 値が範囲外ならリセット
        if rt_val:
            try:
                rv = int(rt_val)
                if rv > mt:
                    resume_temp_dropdown.value = str(mt)
                    save_ini_value("Resume Temperature (°C)", str(mt))
            except:
                pass
        page.update()

    resume_temp_dropdown = ft.Dropdown(
        label="Resume Temperature (°C)",
        value=ini_resume_temp if ini_resume_temp else None,
        options=[ft.dropdown.Option(str(v)) for v in range(0, int(ini_max_temp) + 1)],
        on_change=lambda e: save_ini_value("Resume Temperature (°C)", e.control.value or ""),
        width=300,
    )

    tolerance_textfield = ft.TextField(
        label="Tolerance Time (s)",
        value=ini_tolerance_sec if ini_tolerance_sec else "",
        on_change=lambda e: save_ini_value("Tolerance Time (s)", e.control.value or ""),
        width=300,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    condition_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Text("Criteria", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                        ft.Text(""),
                    ],
                ),
                max_temp_dropdown,
                resume_temp_dropdown,
                tolerance_textfield,
            ],
            spacing=10,
            tight=True,
        ),
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
    )

    # RFIDタグ設定コンテナ
    def update_epc_list(_):
        if not connected_flag["val"] or rfid["obj"] is None:
            return
        try:
            epcs = rfid["obj"].get_epc_list()
        except Exception as ex:
            page.pubsub.send_all({"type": "error", "message": f"Failed to get EPC list: {ex}"})
            return
        # 各ドロップダウンの候補更新
        for i in range(16):
            items = ["Unselected"] + epcs
            epc_options[i] = items
            epc_dropdowns[i].options = [ft.dropdown.Option(v) for v in items]
        page.update()

    update_epc_btn = ft.ElevatedButton(
        "Update EPC List",
        icon=ft.Icons.REFRESH,
        on_click=update_epc_list,
        disabled=not connected_flag["val"],
    )

    # EPCドロップダウン16個
    epc_dropdowns = []
    for i in range(16):
        dd = ft.Dropdown(
            label=f"No. {i+1}",
            value=epc_selections[i] if epc_selections[i] else "Unselected",
            options=[ft.dropdown.Option(v) for v in epc_options[i]],
            width=300,
        )

        # Unselected のとき OFF にする
        def apply_unselected_rule(idx: int):
            if not epc_selections[idx]:
                battery_switches[idx].value = False
        def make_on_change(idx):
            def _on_change(e):
                val = e.control.value or ""
                epc_selections[idx] = "" if val == "Unselected" else val
                save_ini_value(f"No. {idx+1}", epc_selections[idx])
                apply_unselected_rule(idx)
            return _on_change
        dd.on_change = make_on_change(i)
        epc_dropdowns.append(dd)

    # Send to テキストフィールド
    send_to_helper = ft.Text(value="", color=ft.Colors.RED, size=12)
    def on_send_to_change(e):
        raw = e.control.value or ""
        sanitized = sanitize_hex_upper_no_spaces(raw)
        if sanitized != raw:
            # 無効文字を除去して即座に値を補正
            e.control.value = sanitized
        # バリデーション
        if not HEX_RE.match(sanitized):
            send_to_helper.value = "Only hexadecimal numbers are valid."
        else:
            if len(sanitized) % 2 != 0:
                send_to_helper.value = "Invalid length."
            else:
                send_to_helper.value = ""
        save_ini_value("Send to", sanitized)
        page.update()

    send_to_textfield = ft.TextField(
        label="Send to",
        value=ini_send_to if ini_send_to else "",
        tooltip="Input MR793200's EPC",
        on_change=on_send_to_change,
        width=400,
    )

    # Write Throttle (x)
    write_throttle_dropdown = ft.Dropdown(
        label="Write throttle (x)",
        value=str(write_throttle_x["val"]),
        options=[ft.dropdown.Option(str(v)) for v in [1, 2, 3, 5]],
        on_change=lambda e: (write_throttle_x.__setitem__("val", int(e.control.value or "1")), None),
        width=200,
    )

    start_monitoring_btn = ft.ElevatedButton(
        "Start monitoring",
        icon=ft.Icons.PLAY_CIRCLE,
        disabled=not connected_flag["val"],
    )

    # RFIDタグ設定コンテナUI構成（8行×2列）
    epc_rows = []
    for r in range(8):
        row = ft.Row(spacing=10, controls=[
            epc_dropdowns[r*2],
            epc_dropdowns[r*2+1]
        ])
        epc_rows.append(row)

    rfid_tag_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("RFID Tag Setting", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                update_epc_btn,
                *epc_rows,
                ft.Divider(),
                send_to_textfield,
                send_to_helper,
                ft.Divider(),
                ft.Row(controls=[start_monitoring_btn, write_throttle_dropdown], spacing=10),
            ],
            spacing=10,
            tight=True,
        ),
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
        margin=ft.margin.only(top=10),
    )

    # ===== EVバッテリーセンシング（BMS） =====
    # Battery State コンテナ（左）
    auto_mode_switch = ft.CupertinoSwitch(
        value=True,
        tooltip="Batteries are enabled / disabled automatically. If the switch is off, no logging will be performed.",
        active_track_color=ft.Colors.GREEN,
        inactive_track_color=ft.Colors.GREY_400,
    )

    # No.1〜No.16のコンテナ
    battery_containers = []
    battery_switches = []
    battery_temp_texts = []
    for i in range(16):
        s = ft.CupertinoSwitch(
            value=True,
            active_track_color=ft.Colors.GREEN,
            inactive_track_color=ft.Colors.GREY_400,)
        if not epc_selections[i]:
            s.value = False
        battery_switches.append(s)
        # Auto mode による制御
        s.disabled = auto_mode_switch.value

        # 温度オーバーレイ
        temp_text = ft.Text(value="0.0°C", color=ft.Colors.BLACK, weight=ft.FontWeight.NORMAL, size=14)
        battery_temp_texts.append(temp_text)

        stack = ft.Stack(
            controls=[
                ft.Image(src=str(BATTERY_IMAGE_PATH), width=90, height=90),
                temp_text,
            ],
            width=100,
            height=100,
            alignment=ft.alignment.center,
        )

        cont = ft.Container(
            content=ft.Column(
                controls=[ft.Text(f"No. {i+1}", size=16), s, stack],
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=5,
            ),
            width=100,
            height=160,
            padding=10,
            bgcolor=ft.Colors.BLUE_GREY_50,
            border=ft.border.all(1, ft.Colors.GREY_300),
            border_radius=5,
        )
        battery_containers.append(cont)

    def on_auto_mode_change(e):
        on = e.control.value
        for s in battery_switches:
            s.disabled = on
        page.update()

    auto_mode_switch.on_change = on_auto_mode_change

    # 4x4配置（GridViewは使わない）
    grid_rows = []
    for row_idx in range(4):
        row_controls = []
        for col_idx in range(4):
            idx = row_idx * 4 + col_idx
            row_controls.append(battery_containers[idx])
        grid_rows.append(ft.Row(controls=row_controls, spacing=10))

    battery_state_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Battery State", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                ft.Row(controls=[ft.Text("Auto mode"), auto_mode_switch]),
                *grid_rows,
            ],
            spacing=10,
            tight=True,
        ),
        width=450,
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
        margin=ft.margin.only(top=10),
    )

    # 軸ラベル生成
    def make_left_axis():
        # 0〜60を10刻み
        labels = [
            ft.ChartAxisLabel(
                value=v,
                label=ft.Container(
                    content=ft.Text(str(v), size=16),
                    margin=ft.margin.only(left=10),
                )
            ) for v in range(0, 61, 10)
        ]
        return ft.ChartAxis(
            labels=labels,
            labels_size=40,
            title=ft.Container(
                content=ft.Text("Temperature [°C]", size=20),
                margin=ft.margin.only(bottom=10),
            ),
            title_size=40,
        )

    def make_bottom_axis():
        # 現在の表示範囲 chart_x_min〜chart_x_max を60秒刻みでラベル
        start = int(chart_x_min["val"])
        end = int(chart_x_max["val"])
        step = 60
        labels = []
        x = start
        while x <= end:
            labels.append(
                ft.ChartAxisLabel(
                    value=float(x),
                    label=ft.Container(
                        content=ft.Text(str(x), size=16),
                        margin=ft.margin.only(top=10)
                    )
                )
            )
            x += step
        return ft.ChartAxis(
            labels=labels,
            labels_size=40,
            title=ft.Container(
                content=ft.Text("Time [s]", size=20),
                # margin=ft.margin.only(top=8),
            ),
            title_size=40,
        )

    def make_horizontal_grid_lines():
        return ft.ChartGridLines(
            interval=10,
            color=ft.Colors.GREY_400,
            width=0.5,
        )
    
    def make_vertical_grid_lines():
        return ft.ChartGridLines(
            interval=60,
            color=ft.Colors.GREY_400,
            width=0.5,
        )

    def update_axes():
        chart.left_axis = make_left_axis()
        chart.bottom_axis = make_bottom_axis()

    # リアルタイムモニター（中央）
    chart = ft.LineChart(
        data_series=[],
        border=ft.border.all(2, ft.Colors.GREY_600),
        expand=True,
        # width=600,
        # height=320,
        min_y=0,
        max_y=60,
        min_x=chart_x_min["val"],
        max_x=chart_x_max["val"],
        left_axis=make_left_axis(),
        horizontal_grid_lines=make_horizontal_grid_lines(),
        bottom_axis=make_bottom_axis(),
        vertical_grid_lines=make_vertical_grid_lines(),
        interactive=True,
    )

    def clear_chart_data():
        for i in range(16):
            series_data[i].clear()
        chart_start_ts["val"] = time.time()
        chart.min_x = 0.0
        chart.max_x = 600.0
        chart_x_min["val"] = 0.0
        chart_x_max["val"] = 600.0
        rebuild_linechart_series()
        update_axes()

    def rebuild_linechart_series():
        data_series = []
        for i in range(16):
            if not chart_visible[i]:
                # 非表示でもデータ保持。描画しない
                continue
            pts = [ft.LineChartDataPoint(x, y) for (x, y) in series_data[i]]
            spec = series_specs[i]
            ds = ft.LineChartData(
                data_points=pts,
                stroke_width=1,
                color=spec["color"],
            )
            if spec["dash"] is not None:
                # 破線
                ds.dash_pattern = spec["dash"]
            data_series.append(ds)
        chart.data_series = data_series
        chart.min_x = chart_x_min["val"]
        chart.max_x = chart_x_max["val"]

    restart_x_btn = ft.ElevatedButton(
        "Restart X axis at 0",
        icon=ft.Icons.REPLAY,
        on_click=lambda e: page.pubsub.send_all({"type": "linechart_reset"}),
    )

    realtime_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Temperature Chart", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                chart,
                ft.Row(controls=[restart_x_btn], alignment=ft.MainAxisAlignment.START),
            ],
        ),
        height = 780,
        expand=True,
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
        margin=ft.margin.only(top=10),
    )

    # グラフ On/Off（右）
    legend_checks = []
    legend_rows = []
    for i in range(16):
        # チェックボックス（ラベルは付けず、行の中に配置）
        cb = ft.Checkbox(
            value=True,
            active_color=ft.Colors.GREY_800,
            on_change=lambda e, idx=i: toggle_series_visibility(idx, e.control.value),
        )
        legend_checks.append(cb)

        # アイコン: 1〜8 = HORIZONTAL_RULE、9〜16 = MORE_HORIZ
        icon_name = ft.Icons.HORIZONTAL_RULE if i < 8 else ft.Icons.MORE_HORIZ
        icon = ft.Icon(name=icon_name, color=series_specs[i]["color"], size=18)

        # テキスト: "No. X"
        label_text = ft.Text(f"No. {i+1}", size=16)

        # 1行にまとめる
        row = ft.Row(
            controls=[cb, icon, label_text],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        legend_rows.append(row)

    def toggle_series_visibility(idx: int, visible: bool):
        chart_visible[idx] = visible
        rebuild_linechart_series()
        page.update()

    all_on_btn = ft.ElevatedButton("All on", on_click=lambda e: set_all_series_visibility(True))
    all_off_btn = ft.ElevatedButton("All off", on_click=lambda e: set_all_series_visibility(False))

    def set_all_series_visibility(on: bool):
        for i in range(16):
            chart_visible[i] = on
            legend_checks[i].value = on
        rebuild_linechart_series()
        page.update()

    graph_onoff_container = ft.Container(
        content=ft.Column(
            controls=[
                ft.Text("Line On/Off", size=20, color=ft.Colors.GREY_700, weight=ft.FontWeight.BOLD),
                ft.Column(legend_rows + [ft.Row(controls=[all_on_btn, all_off_btn], spacing=10)])
            ],
            spacing=10,
            tight=True,
        ),
        width=220,
        padding=10,
        bgcolor=ft.Colors.BLUE_GREY_50,
        border=ft.border.all(1, ft.Colors.GREY_300),
        border_radius=5,
        margin=ft.margin.only(top=10),
    )

    # ===== EVバッテリーセンシング（リユース） =====
    # テーブル（Middle）
    # 固定列幅: 150, 250, 200, 160  合計 760
    TABLE_WIDTH = 150 + 250 + 200 + 160

    # リユース表データ構造
    reuse_rows = [{"no": i+1, "epc": epc_selections[i], "judgement": "-", "count": "-"} for i in range(16)]

    def build_reuse_table():
        header = ft.Row(
            controls=[
                ft.Container(ft.Text("Battery No.", color=ft.Colors.WHITE), width=150, bgcolor=ft.Colors.BLUE_GREY_800, padding=10),
                ft.Container(ft.Text("RFID EPC", color=ft.Colors.WHITE), width=250, bgcolor=ft.Colors.BLUE_GREY_800, padding=10),
                ft.Container(ft.Text("Good/bad judgement", color=ft.Colors.WHITE), width=200, bgcolor=ft.Colors.BLUE_GREY_800, padding=10),
                ft.Container(ft.Text("Number of times", color=ft.Colors.WHITE), width=160, bgcolor=ft.Colors.BLUE_GREY_800, padding=10),
            ],
            spacing=0,
        )
        rows = [header]
        for r in reuse_rows:
            rows.append(
                ft.Row(
                    controls=[
                        ft.Container(ft.Text(f"No. {r['no']}"), width=150, bgcolor=ft.Colors.BLUE_GREY_400, padding=10),
                        ft.Container(ft.Text(r["epc"] or "-"), width=250, bgcolor=ft.Colors.BLUE_GREY_50, padding=10),
                        ft.Container(ft.Text(r["judgement"]), width=200, bgcolor=ft.Colors.BLUE_GREY_50, padding=10),
                        ft.Container(ft.Text(r["count"]), width=160, bgcolor=ft.Colors.BLUE_GREY_50, padding=10),
                    ],
                    spacing=0,
                )
            )
        return ft.Column(controls=rows, spacing=0, tight=True)

    reuse_table_column = build_reuse_table()

    middle_container = ft.Container(
        content=reuse_table_column,
        width=TABLE_WIDTH,
        bgcolor=ft.Colors.BLUE_GREY_50,
    )

    # Upper
    def on_update_reuse_log(_):
        # ログ読み出し
        for i in range(16):
            path = LOG_DIR / f"reuse_no{i+1}.log"
            count_val = "-"
            judgement = "Good"
            if path.exists():
                # 最新カウントを取得（最後の行の count=）
                with open(path, "r", encoding="utf-8") as f:
                    lines = [ln.strip() for ln in f.readlines() if ln.strip()]
                if lines:
                    judgement = "Bad"
                    last = lines[-1]
                    m = re.search(r"count=(\d+)", last)
                    if m:
                        count_val = m.group(1)
                    else:
                        count_val = "-"
            else:
                judgement = "Good"
                count_val = "-"
            reuse_rows[i]["epc"] = epc_selections[i] or "-"
            reuse_rows[i]["judgement"] = judgement
            reuse_rows[i]["count"] = count_val
        # テーブル再描画
        reuse_table_column.controls = build_reuse_table().controls
        page.update()

    upper_container = ft.Container(
        content=ft.Row(
            controls=[ft.Container(
                content=ft.Row(controls=[ft.ElevatedButton("Update reuse log", on_click=on_update_reuse_log)], alignment=ft.MainAxisAlignment.END),
                width=TABLE_WIDTH,
            )],
            alignment=ft.MainAxisAlignment.END,
        ),
        width=TABLE_WIDTH,
        margin=ft.margin.only(top=10),
    )

    # Lower
    def on_log_clear(_):
        # CupertinoAlertDialog を overlay に追加して open=True
        def on_ok(_e):
            # 削除
            for i in range(16):
                p = LOG_DIR / f"reuse_no{i+1}.log"
                try:
                    if p.exists():
                        p.unlink()
                except Exception:
                    pass
            dialog.open = False
            page.update()

        def on_cancel(_e):
            dialog.open = False
            page.update()

        dialog = ft.CupertinoAlertDialog(
            title=ft.Text("Delete ALL Logs"),
            content=ft.Text("Do you want to delete all logs?"),
            actions=[
                ft.CupertinoDialogAction(text="Cancel", on_click=on_cancel),
                ft.CupertinoDialogAction(text="OK", is_destructive_action=True, on_click=on_ok),
            ],
        )
        page.overlay.append(dialog)
        dialog.open = True
        page.update()

    lower_container = ft.Container(
        content=ft.Row(
            controls=[ft.Container(
                content=ft.Row(controls=[ft.ElevatedButton("Log clear", on_click=on_log_clear)], alignment=ft.MainAxisAlignment.END),
                width=TABLE_WIDTH,
            )],
            alignment=ft.MainAxisAlignment.END,
        ),
        width=TABLE_WIDTH,
    )

    reuse_tab_content = ft.Column(
        controls=[upper_container, middle_container, lower_container],
        spacing=10,
    )

    # ===== タブ構成 =====
    # 設定タブ: 画面左50%/右50%
    settings_left = ft.Column(controls=[rfid_reader_container, condition_container], spacing=10)
    settings_right = ft.Column(controls=[rfid_tag_container], spacing=10)

    settings_tab = ft.Tab(
        text="Setting",
        icon=ft.Icons.SETTINGS,
        content=ft.ResponsiveRow(
            controls=[
                ft.Container(content=settings_left, col={"xs": 12, "md": 6}),
                ft.Container(content=settings_right, col={"xs": 12, "md": 6}),
            ],
            columns=12,
        ),
    )

    # EVバッテリーセンシングタブ（ネストタブ）
    bms_tab_inner = ft.Tab(
        text="BMS",
        icon=ft.Icons.SENSORS,
        content=ft.ResponsiveRow(
            columns=24,
            controls=[
                # xs: extra small の画面幅（ウィンドウがかなり狭いとき）に適用されるカラム幅指定
                # md: medium の画面幅（中くらいの幅）に適用されるカラム幅指定
                # lg: large の画面幅（広いとき）に適用されるカラム幅指定
                ft.Container(content=battery_state_container, col={"xs": 12, "md": 4, "lg": 5.5}),
                ft.Container(content=realtime_container, col={"xs": 12, "md": 6, "lg": 16}),
                ft.Container(content=graph_onoff_container, col={"xs": 12, "md": 2, "lg": 2.5}),
            ],
            spacing=10,
        ),
    )
    reuse_tab_inner = ft.Tab(
        text="Reuse",
        icon=ft.Icons.REPEAT,
        content=reuse_tab_content,
    )
    inner_tabs = ft.Tabs(
        tabs=[bms_tab_inner, reuse_tab_inner],
        selected_index=0,
    )

    ev_tab = ft.Tab(
        text="Battery sensing",
        icon=ft.Icons.MONITOR,
        content=inner_tabs,
    )

    main_tabs = ft.Tabs(
        tabs=[settings_tab, ev_tab],
        selected_index=0,  # 初期選択
        expand=1,
    )

    page.add(main_tabs)

    # ===== 監視・書込みロジック =====

    # No.1〜No.16 スイッチ状態からビットフィールド（h00）を生成
    def build_h00_bitfield():
        # LSB: No.1, MSB: No.16
        val = 0
        for i in range(16):
            bit = 1 if (battery_switches[i].value and bool(epc_selections[i])) else 0
            val |= (bit << i)
        # big-endian 2 bytes
        high = (val >> 8) & 0xFF
        low = val & 0xFF
        return bytes([high, low])

    # 温度ペア2バイト（big-endian）を作成
    def build_temp_word(idx_low: int, idx_high: int, temps_int: list[int]):
        # [7:0]=No.idx_low, [15:8]=No.idx_high
        low = temps_int[idx_low] & 0xFF
        high = temps_int[idx_high] & 0xFF
        return bytes([high, low])

    # 監視スレッド
    def monitoring_worker():
        chart_start_ts["val"] = time.time()
        elapsed_base_ts = chart_start_ts["val"]
        cycle_count = 0
        while monitoring_flag["val"]:
            # X軸リセット要求処理
            if x_axis_reset_requested["val"]:
                # 起点時刻を現在に更新
                elapsed_base_ts = time.time()
                # X軸範囲も初期化（シフト判定に使う値）
                chart_x_min["val"] = 0.0
                chart_x_max["val"] = 600.0
                # 書込みスロットリングのカウンタも初期化（任意）
                cycle_count = 0
                # フラグをクリア
                x_axis_reset_requested["val"] = False

            t0 = time.time()
            poll_ms = poll_interval_ms_state()
            poll_sec = poll_ms / 1000.0

            # 温度取得
            temps = [0.0] * 16
            temps_int = [0] * 16
            try:
                if rfid["obj"] is not None:
                    temp_map = rfid["obj"].get_temperature()  # dict: epc -> {timestamp, value}
                    for i in range(16):
                        epc = epc_selections[i]
                        if epc and epc in temp_map:
                            val = float(temp_map[epc]["value"])
                            temps[i] = round(val, 1)
                            temps_int[i] = int(round(val))
                        else:
                            temps[i] = 0.0
                            temps_int[i] = 0
                else:
                    temps = [0.0] * 16
                    temps_int = [0] * 16
            except Exception:
                temps = [0.0] * 16
                temps_int = [0] * 16

            # Auto mode ロジック & ログ
            if auto_mode_switch.value:
                max_t = max_temp_state()
                resume_t = resume_temp_state()
                tol_s = tolerance_sec_state()
                now_ts = time.time()
                for i in range(16):
                    # 連続超過判定
                    if temps[i] > max_t:
                        if battery_states[i].exceed_start_ts is None:
                            battery_states[i].exceed_start_ts = now_ts
                        else:
                            if (now_ts - battery_states[i].exceed_start_ts) >= tol_s:
                                # イベント発火: スイッチOFF、ログ記録
                                battery_switches[i].value = False
                                battery_states[i].exceed_start_ts = now_ts  # 次の連続超過計測を再開
                                battery_states[i].exceed_count += 1
                                # ログ（Auto mode ON 時のみ）
                                log_queue.put((i + 1, battery_states[i].exceed_count, temps[i], now_ts))
                    else:
                        # 超過解除：復帰判定
                        battery_states[i].exceed_start_ts = None
                        if temps[i] < resume_t:
                            battery_switches[i].value = True
            # X軸の経過時間
            elapsed_s = time.time() - elapsed_base_ts

            # グラフデータ追加・ウィンドウ管理
            # X軸: 0〜600、600を超えたら300シフト（0-600 → 300-900 → 600-1200 ...）
            # series_data を更新
            for i in range(16):
                series_data[i].append((elapsed_s, temps[i]))
                # シフト処理
            if elapsed_s > chart_x_max["val"]:
                chart_x_min["val"] += 300.0
                chart_x_max["val"] += 300.0
                # 古いデータ削除（新minより左を削る）
                for i in range(16):
                    series_data[i] = [(x, y) for (x, y) in series_data[i] if x >= chart_x_min["val"]]

            # UI更新通知（メインスレッドで page.update）
            page.pubsub.send_all({"type": "monitor_tick", "temps": temps, "elapsed_s": elapsed_s})

            # 書き込み（スロットリング）
            cycle_count += 1
            do_write = (cycle_count % write_throttle_x["val"] == 0)

            if do_write and not write_in_progress["val"]:
                # Send to EPC 檢査
                cp2 = load_ini()
                send_to_raw = cp2["setting"].get("Send to", "")
                if send_to_raw:
                    send_to_spaced = insert_space_every_two_chars(send_to_raw)
                    # 書き込みスレッド開始
                    def write_worker():
                        write_in_progress["val"] = True
                        try:
                            # 送信先（空欄ならスキップ）
                            cp2 = load_ini()
                            send_to_raw = cp2["setting"].get("Send to", "")
                            if not send_to_raw or rfid["obj"] is None:
                                return
                            send_to_spaced = insert_space_every_two_chars(send_to_raw)

                            # h00: スイッチON/OFFビットフィールド（big-endian 2 bytes）
                            h00 = build_h00_bitfield()

                            # 温度整数（0〜255, 四捨五入）
                            # 直前に生成した temps_int を write_worker に渡せない場合は、
                            # ここで再計算してもOK（ただしポーリング周期内で大差なし）
                            temp_map = rfid["obj"].get_temperature() if rfid["obj"] is not None else {}
                            temps_int_local = [0] * 16
                            for i in range(16):
                                epc = epc_selections[i]
                                if epc and epc in temp_map:
                                    val = int(round(float(temp_map[epc]["value"])))
                                    temps_int_local[i] = max(0, min(255, val))
                                else:
                                    temps_int_local[i] = 0

                            # h01〜h08: 2台ずつの温度ペア（[7:0]=No.low, [15:8]=No.high）
                            pairs = [
                                (0, 1), (2, 3), (4, 5), (6, 7),
                                (8, 9), (10, 11), (12, 13), (14, 15)
                            ]
                            words = [h00]
                            for (low_idx, high_idx) in pairs:
                                words.append(build_temp_word(low_idx, high_idx, temps_int_local))

                            # 9ワード(18バイト)を WordPtr=0 に一括書込み
                            data_bytes = b"".join(words)  # 長さ18
                            err = rfid["obj"].access_write(
                                send_to_spaced,
                                memory_bank.USER,
                                0,                # WordPtr=0 (h00から)
                                data_bytes.hex(), # 可変長Data（2バイト×9ワード=18バイト）
                            )
                            print(f"access_write({send_to_spaced}, {memory_bank.USER.value}, 0, {data_bytes.hex().upper()})") # デバッグ用
                            if err != 0:
                                page.pubsub.send_all({"type": "error", "message": "Write failed (bulk)."})
                        except Exception as ex:
                            page.pubsub.send_all({"type": "error", "message": f"Write failed: {ex}"})
                        finally:
                            # 書込み完了後に自動読取りを再開
                            try:
                                if rfid["obj"] is not None:
                                    rfid["obj"].start_autoread_axzon_temperature_sensor()
                            except Exception:
                                pass
                            write_in_progress["val"] = False
                    threading.Thread(target=write_worker, daemon=True).start()

            # 次の周期までスリープ
            t1 = time.time()
            sleep_time = poll_sec - (t1 - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def on_start_monitoring(_):
        if not connected_flag["val"] or rfid["obj"] is None:
            return
        # 監視開始
        monitoring_flag["val"] = True
        if monitoring_thread["obj"] is None or not monitoring_thread["obj"].is_alive():
            monitoring_thread["obj"] = threading.Thread(target=monitoring_worker, daemon=True)
            monitoring_thread["obj"].start()
        # EVタブへ移動（BMS）
        main_tabs.selected_index = 1
        inner_tabs.selected_index = 0
        page.update()

    start_monitoring_btn.on_click = on_start_monitoring

    # ページクローズ時のクリーンアップ
    def on_close(_):
        try:
            monitoring_flag["val"] = False
            time.sleep(0.1)
            if rfid["obj"] is not None:
                try:
                    rfid["obj"].close()
                except Exception:
                    pass
            # ログプロセス停止
            try:
                log_queue.put(None)
            except Exception:
                pass
        except Exception:
            pass

    page.on_close = on_close

# 実行エントリ
if __name__ == "__main__":
    ft.app(target=main)
