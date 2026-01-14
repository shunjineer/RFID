import os
import configparser

INI_PATH = os.path.join("src", "driver", "driver_monitor_initial_data.ini")

# テンプレートは全項目を空欄で作成（= の後に半角スペース1個、値は空欄）
TEMPLATE = """[setting]
COM = 
Output Power (dBm) = 
Polling Interval (ms) = 

AC On Temperature (°C) = 
AC Max. Temperature (°C) = 
Heater On Temperature (°C) = 
Heater Max. Temperature (°C) = 

FL Headrest = 
FL Backrest = 
FL Seat = 
FR Headrest = 
FR Backrest = 
FR Seat = 
RL Headrest = 
RL Backrest = 
RL Seat = 
RR Headrest = 
RR Backrest = 
RR Seat = 

Send to = 
"""

def ensure_ini():
    if not os.path.exists(INI_PATH):
        os.makedirs(os.path.dirname(INI_PATH), exist_ok=True)
        # LFで保存
        with open(INI_PATH, "w", newline="\n", encoding="utf-8") as f:
            f.write(TEMPLATE)

def read_ini():
    ensure_ini()
    config = configparser.ConfigParser()
    config.read(INI_PATH, encoding="utf-8")
    if "setting" not in config:
        config["setting"] = {}
    return config

def get_setting(key: str, default: str | None = None) -> str:
    config = read_ini()
    return config["setting"].get(key, default if default is not None else "")

def set_setting(key: str, value: str):
    config = read_ini()
    if "setting" not in config:
        config["setting"] = {}
    config["setting"][key] = value
    with open(INI_PATH, "w", newline="\n", encoding="utf-8") as f:
        config.write(f)

def get_float_setting(key: str, default: float = 0.0) -> float:
    s = get_setting(key, "")
    try:
        return float(s) if s != "" else default
    except Exception:
        return default

def get_int_setting(key: str, default: int = 0) -> int:
    s = get_setting(key, "")
    try:
        return int(s) if s != "" else default
    except Exception:
        return default
