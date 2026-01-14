from enum import StrEnum
import serial
from threading import Thread
from queue import Queue
import time

class memory_bank(StrEnum):
    RFU = "00"
    EPC = "01"
    TID = "02"
    USER = "03"

class rfid_rw_wine7_uart_for_autoread_axzon_sensor():
    def __init__(self, com, tx_power_db):
        # COMオープン
        self.rfid_rw = serial.Serial(com, 460800, timeout=1)
        # スレッド処理
        self.queue = Queue()
        self.queue.put(("SETTING", {"TxPower": f"{int(tx_power_db*10):04x}"}))
        self.t = Thread(target=self.rfid_rw_wine7_worker, args=(self.queue, ))
        self.t.start()

    # RFID RW命令処理スレッド
    def rfid_rw_wine7_worker(self, queue):
        while True:
            message, data = queue.get()
            print(message)
            if message == "SETTING":
                self.temperature = {}
                self.trx_rcp_packet("11 08", "")    # System reset
                self.trx_rcp_packet("14 01", "02 01 01 03 01 04 04 01 0F 05 01 00 06 01 00 07 01 00 08 01 00 09 01 01 0B 02 00 00 0C 02 00 00 0D 02 00 00 0E 01 00 0F 01 00 10 01 01 11 01 03 12 02 00 E0 13 01 00 14 00 0E 01 01 0F 01 00 10 01 00 11 01 00 12 02 00 00 13 01 00 14 00 0E 01 02 0F 01 00 10 01 00 11 01 00 12 02 00 00 13 01 00 14 00 0E 01 03 0F 01 00 10 01 00 11 01 00 12 02 00 00 13 01 00 14 00 15 01 0E 16 04 00 0D FA 20 17 02 00 C8 18 01 19 19 01 01 1B 01 01 1C 01 00 1D 02 FD 1C 1E 01 05 1F 01 00 20 01 01 21 02 00 F0 24 02 00 C8 1F 01 01 20 01 00 21 02 01 2C 24 02 00 C8 1F 01 02 20 01 00 21 02 01 2C 24 02 00 C8 1F 01 03 20 01 00 21 02 01 2C 24 02 00 C8 25 01 2C")    # ?
                self.trx_rcp_packet("11 31", "00 04") # ?
                self.trx_rcp_packet("11 14", "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01") # Set channel mask
                self.trx_rcp_packet("01 1A", "")    # Get channel
                self.trx_rcp_packet("01 38", "")    # Get frequency hopping state
                self.trx_rcp_packet("11 19", "0E")  # Set region code
                self.trx_rcp_packet("11 13", "01")  # Set RF preset
                self.trx_rcp_packet("11 10", "00 0D FA 20")   # Set start frequency
                self.trx_rcp_packet("11 11", "00 C8") # Set spacing
                self.trx_rcp_packet("11 12", "19")    # Set channel count
                self.trx_rcp_packet("11 31", "00 04") # ?
                self.trx_rcp_packet("11 14", "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01 01")  # Set channel mask
                self.trx_rcp_packet("01 1A", "")    # Read channel
                self.trx_rcp_packet("01 38", "")    # Get frequency hopping state
                self.trx_rcp_packet("11 38", "01")    # Set frequency hopping state
                self.trx_rcp_packet("11 55", "2C")    # Set reader mode
                self.trx_rcp_packet("11 39", "00")    # Set LBT state
                self.trx_rcp_packet("11 3A", "FD 1C") # Set LBT RF level
                self.trx_rcp_packet("11 3B", "05")    # Set carrier sense time
                self.trx_rcp_packet("11 90", "00")    # Set index
                self.trx_rcp_packet("11 91", "01")    # Set port number
                self.trx_rcp_packet("11 20", data["TxPower"]) # Set Tx power
                self.trx_rcp_packet("11 30", "00 C8") # Set dwell time
                self.trx_rcp_packet("11 90", "01")    # Set index
                self.trx_rcp_packet("11 91", "00")    # Set port number
                self.trx_rcp_packet("11 20", data["TxPower"]) # Set Tx power
                self.trx_rcp_packet("11 30", "00 C8") # Set dwell time
                self.trx_rcp_packet("11 90", "02")    # Set index
                self.trx_rcp_packet("11 91", "00")    # Set port number
                self.trx_rcp_packet("11 20", data["TxPower"]) # Set Tx power
                self.trx_rcp_packet("11 30", "00 C8") # Set dwell time
                self.trx_rcp_packet("11 90", "03")    # Set index
                self.trx_rcp_packet("11 91", "00")    # Set port number
                self.trx_rcp_packet("11 20", data["TxPower"]) # Set Tx power
                self.trx_rcp_packet("11 30", "00 C8") # Set dwell time
            elif message == "AUTOREAD_AXZON_TEMPERATURE_SENSOR":
                self.trx_rcp_packet("11 78", "00")  # Set report mode
                self.trx_rcp_packet("11 70", "80")  # Set access mode
                self.trx_rcp_packet("14 82", "")    # Start access
                self.autoread_axzon_temperature_sensor()
            elif message == "AUTOREAD_AXZON_TEMPERATURE_SENSOR_HIGH_FREQUENCY":
                self.trx_rcp_packet("11 78", "00")  # Set report mode
                self.trx_rcp_packet("11 70", "80")  # Set access mode
                self.trx_rcp_packet("14 82", "")    # Start access
                for target_epc in data["TargetEPCs"]:
                    self.trx_rcp_packet("14 C9", "01")  # Enable AXZON Tx power modulation
                    self.trx_rcp_packet("11 82", target_epc)    # Set target EPC
                self.trx_rcp_packet("14 C9", "00")  # Disable AXZON Tx power modulation
                self.trx_rcp_packet("11 82", "")    # Set target EPC
                self.autoread_axzon_temperature_sensor()
            elif message == "ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR":
                self.trx_rcp_packet("14 84", "")  # Abort
            elif message == "READ":
                self.trx_rcp_packet("11 78", "00")  # Set report mode
                self.trx_rcp_packet("11 70", "50")  # Set access mode
                self.trx_rcp_packet("12 2C", "00 00 00 00") # Set access password
                self.trx_rcp_packet("11 82", data["TargetEPC"])   # Set target EPC
                self.trx_rcp_packet("12 20", data["MemBank"]) # Set MemBank
                self.trx_rcp_packet("12 21", data["WordPtr"]) # Set WordPtr
                self.trx_rcp_packet("12 22", data["WordCount"])   # Set WordCount
                self.trx_rcp_packet("14 82", "")  # Start access
                index, rx_payload_length, rx_payload = self.rx_rcp_packet_multiple_expected_hex_sequences(["50 00", "24 82"])
                if index == 0:
                    self.access_read_data = rx_payload.hex()
                    self.access_read_error = 0
                else:
                    self.access_read_data = ""
                    self.access_read_error = 1
                self.access_read_done = True
            elif message == "WRITE":
                self.trx_rcp_packet("11 78", "00")    # Set report mode
                self.trx_rcp_packet("11 70", "51")    # Set access mode
                self.trx_rcp_packet("12 2C", "00 00 00 00")   # Set access password
                self.trx_rcp_packet("11 82", data["TargetEPC"])
                self.trx_rcp_packet("12 24", data["MemBank"])    # Set MemBank
                self.trx_rcp_packet("12 25", data["WordPtr"]) # Set WordPtr
                self.trx_rcp_packet("12 26", data["Data"]) # Set Data
                self.trx_rcp_packet("14 82", "")  # Start access
                rx_payload_length, rx_payload = self.rx_rcp_packet("24 82")
                if rx_payload_length != 0:
                    if rx_payload[0] == 0:
                        self.access_write_error = 0
                    else:
                        self.access_write_error = 1    
                else:
                    self.access_write_error = 1
                self.access_write_done = True
            elif message == "CLOSE":
                self.rfid_rw.close()
                break
            else:
                print(f"{__file__} received \"{message}\". It's invalid message.")
                pass

    # RCP packet送受信関数
    def trx_rcp_packet(self, header, tx_payload):
        tx_payload_length = self.calc_payload_length(tx_payload)
        checksum = self.calc_checksum("CC", header, tx_payload_length, tx_payload)
        self.rfid_rw.write(bytes.fromhex(f"CC {header} {tx_payload_length} {tx_payload} {checksum}"))
        rx_payload_length, rx_payload = self.rx_rcp_packet(header)
        return rx_payload_length, rx_payload

    # RCP packet受信関数
    def rx_rcp_packet(self, header):
        expected_bytes = bytes.fromhex(f"AA {header}")
        self.rfid_rw.read_until(expected_bytes)
        rx_payload_length_1 = int.from_bytes(self.rfid_rw.read(1), "big")
        if (rx_payload_length_1 >> 7) == 0:
            rx_payload_length = rx_payload_length_1
        else:
            rx_payload_length_2 = int.from_bytes(self.rfid_rw.read(1), "big")
            rx_payload_length = ((rx_payload_length_1 & 0b01111111) << 7) + (rx_payload_length_2 & 0b01111111)
        rx_payload = self.rfid_rw.read(rx_payload_length + 1)[0:rx_payload_length]
        return rx_payload_length, rx_payload

    # RCP packet受信関数 (複数の期待値があるときに使う)
    def rx_rcp_packet_multiple_expected_hex_sequences(self, expected_hex_sequences):
        buffer = b''
        while True:
            # 1バイト読み込み
            byte = self.rfid_rw.read(1)
            if not byte:
                continue  # タイムアウトの場合はスキップ
            buffer += byte
            # バッファの長さが3になったら比較
            buffer_len = len(buffer)
            if buffer_len == 3:
                for index, expected_hex_sequence in enumerate(expected_hex_sequences):
                    if buffer == bytes.fromhex(f"AA {expected_hex_sequence}"):
                        rx_payload_length_1 = int.from_bytes(self.rfid_rw.read(1), "big")
                        if (rx_payload_length_1 >> 7) == 0:
                            rx_payload_length = rx_payload_length_1
                        else:
                            rx_payload_length_2 = int.from_bytes(self.rfid_rw.read(1), "big")
                            rx_payload_length = ((rx_payload_length_1 & 0b01111111) << 7) + (rx_payload_length_2 & 0b01111111)
                        rx_payload = self.rfid_rw.read(rx_payload_length + 1)[0:rx_payload_length]
                        return index, rx_payload_length, rx_payload
                buffer = buffer[1:3]
            else:
                pass

    # ペイロード長計算
    def calc_payload_length(self, payload):
        payload_length_dec = len(payload.replace(" ", "")) // 2
        if payload_length_dec <= 127:
            payload_length = f"{payload_length_dec:02x}"
        else:
            payload_length_1 = 0b10000000 | (payload_length_dec >> 7)
            payload_length_2 = payload_length_dec & 0b01111111
            payload_length = f"{payload_length_1:02x} {payload_length_2:02x}"
        return payload_length

    # チェックサム計算
    def calc_checksum(self, preamble, header, tx_payload_length, tx_payload):
        packet = bytes.fromhex(f"{preamble} {header} {tx_payload_length} {tx_payload}")
        combined_value = 0
        # Step 1 & 2: 1バイトずつ処理し、合計値が256を超えたらMSBとLSBを合成
        for byte in packet:
            combined_value += byte
            if combined_value >= 256:
                combined_value = ((combined_value & 0xFF) + (combined_value >> 8))
        # Step 3: ビット反転
        checksum = combined_value ^ 0xFF
        # Step 4: 特定の値に対して-1の調整
        if checksum == 0xCC:
            checksum = 0xCB
        elif checksum == 0xAA:
            checksum = 0xA9
        return f"{checksum:02x}"

    # AXZON温度センサーの自動読み取り処理
    def autoread_axzon_temperature_sensor(self):
        self.autoread = True
        while True:
            if self.autoread == False:
                break
            rx_payload_length, rx_payload = self.rx_rcp_packet("80 00")
            if rx_payload_length == 0x20:   # AXZON AZN3300-AFRの応答のペイロード長は0x20
                epc = rx_payload[2:10]
                temperature_code = int.from_bytes(rx_payload[22:24], "big")
                calibration_data = rx_payload[24:32]
                code1 = int.from_bytes(calibration_data[2:4], "big") >> 4
                temp1 = ((int.from_bytes(calibration_data[2:4], "big") & 0b0000000000001111) << 7) + (int.from_bytes(calibration_data[4:6], "big") >> 9)
                code2 = ((int.from_bytes(calibration_data[4:6], "big") & 0b0000000111111111) << 3) + (int.from_bytes(calibration_data[6:8], "big") >> 13)
                temp2 = (int.from_bytes(calibration_data[6:8], "big") >> 2) & 0b0000011111111111
                self.temperature[epc.hex()] = {"timestamp": time.time(), "value": (((temp2 - temp1) / (code2 - code1)) * (temperature_code - code1) + temp1 - 800) / 10}

    # AXZON温度センサーの自動読み取り処理開始
    def start_autoread_axzon_temperature_sensor(self):
        self.autoread = False
        self.queue.put(("ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))
        self.queue.put(("AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))

    # AXZON温度センサーの高速自動読み取り処理開始
    def start_autoread_axzon_temperature_sensor_high_frequency(self, target_axzon_sensor_epc_list):
        self.autoread = False
        self.queue.put(("ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))
        self.queue.put(("AUTOREAD_AXZON_TEMPERATURE_SENSOR_HIGH_FREQUENCY", {"TargetEPCs": target_axzon_sensor_epc_list}))

    # 検出済みのEPCリスト取得
    def get_epc_list(self):
        return list(self.temperature.keys())

    # 温度取得
    def get_temperature(self):
        return self.temperature

    # Read
    def access_read(self, target_epc, mem_bank, word_ptr, word_count):
        self.autoread = False
        self.access_read_done = False
        self.queue.put(("ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))
        self.queue.put(("READ", {"TargetEPC": target_epc, "MemBank": mem_bank, "WordPtr": f"{word_ptr:04x}", "WordCount": f"{word_count:02x}"}))
        while True:
            if self.access_read_done == True:
                return self.access_read_error, self.access_read_data
            time.sleep(0.001)

    # Write
    def access_write(self, target_epc, mem_bank, word_ptr, data):
        self.autoread = False
        self.access_write_done = False
        self.queue.put(("ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))
        self.queue.put(("WRITE", {"TargetEPC": target_epc, "MemBank": mem_bank, "WordPtr": f"{word_ptr:04x}", "Data": data}))
        while True:
            if self.access_write_done == True:
                return self.access_write_error
            time.sleep(0.001)

    # Close
    def close(self):
        self.autoread = False
        self.queue.put(("ABORT_AUTOREAD_AXZON_TEMPERATURE_SENSOR", None))
        self.queue.put(("CLOSE", None))

# # Test
if __name__ == '__main__':
    # 実環境に合わせてCOMポートと送信電力(dBm)を設定
    COM_PORT = "COM4"
    TX_POWER_DB = 30.0  # 例: 24dBm

    # リーダ制御インスタンス作成（初期設定は内部のワーカーで自動実行）
    rfid_rw = rfid_rw_wine7_uart_for_autoread_axzon_sensor(COM_PORT, TX_POWER_DB)
    time.sleep(1)

    # AXZON温度センサの標準自動読み取り開始
    # rfid_rw.start_autoread_axzon_temperature_sensor()
    # time.sleep(1)

    epc_rohm = "E2 83 38 06 20 00 00 00 01 C7 4D 68"
    test_data_clear = "00 00"
    test_data = "9A 80"

    # hf_start = time.time()
    cnt = 0
    while cnt < 8: # time.time() - hf_start < 10:
        # error, data = rfid_rw.access_read(epc_rohm, memory_bank.USER, 0, 1)
        # if error == 0:
            if cnt % 2 == 0:
                rfid_rw.access_write(epc_rohm, memory_bank.USER, 0, test_data)
                print("On")
                cnt += 1
                time.sleep(0.1)
            elif cnt % 2 == 1:
                rfid_rw.access_write(epc_rohm, memory_bank.USER, 0, test_data_clear)
                print("Off")
                cnt += 1
                time.sleep(0.1)
                # break
        # else:
        #     print("Read error.")
    

    
    # # EPCが見つかった場合は高速モードで読み取り頻度を上げて数秒間収集
    # epc_list = rfid_rw.get_epc_list()
    # print(epc_list)
    # if epc_list:
    #     rfid_rw.start_autoread_axzon_temperature_sensor_high_frequency(epc_list)
    #     time.sleep(1)
        
        

        # hf_start = time.time()
        # while time.time() - hf_start < 3:
        #     cnt += 1
        #     print(cnt)
        #     temps = rfid_rw.get_temperature()
        #     for epc_hex, info in temps.items():
        #         print(f"[HF] EPC={epc_hex}, temperature={info['value']:.1f} C")
        #     time.sleep(0.5)
    
    # 後処理
    rfid_rw.close()



    # time.sleep(1)
    # rfid_rw.start_autoread_axzon_temperature_sensor_high_frequency(rfid_rw.get_epc_list())
    # time.sleep(1)
    # temperature = rfid_rw.get_temperature()
    # print(temperature)
    # error, data = rfid_rw.access_read("32 00 F2 20 50 02 41 19", memory_bank.EPC, 0, 10)
    # print(f"error = {error}, data = {data}")
    # error = rfid_rw.access_write("32 00 F2 20 50 02 41 19", memory_bank.USER, 0, "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 58 48 91 78 BF 3F F6 40")
    # print(f"access write error = {error}")
    # rfid_rw.close()
