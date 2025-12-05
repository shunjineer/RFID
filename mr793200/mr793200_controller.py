import spidev
import time

READ = (0x09 << 4)
WRITE = (0x0D << 4)
READ_NVM0 = (0x00 << 4)
READ_NVM1 = (0x01 << 4)
READ_NVM2 = (0x02 << 4)
READ_NVM3 = (0x03 << 4)
READ_NVM4 = (0x04 << 4)
WRITE_NVM = (0x0E << 4)
READ_SPIST = (0x08 << 4)
WRITE_SPIST = (0x0C << 4)

class mr793200_controller():
    def __init__(self, sclk_frequency=1000000):
        self.spi = spidev.SpiDev()
        self.spi.open(0,0)
        self.spi.mode = 0    # MR793200はSPI mode0で動作
        self.spi.max_speed_hz = sclk_frequency  # 0.39 ~ 5.0 MHz
        print(f"sclk_frequency = {sclk_frequency/1000000} MHz")

    def read_nvm1(self, addr_msb, addr_lsb, word_len):
        read_data = self.spi.xfer2([READ_NVM1 | addr_msb, addr_lsb, 0x00, 0x00] + [0x00]*word_len*2)[2:]
        read_data_hex = []
        for i in range(word_len):
            read_data_hex = read_data_hex + read_data[2*(2*i+1):2*(2*i+1)+2]
        return bytearray(read_data_hex).hex()

    # NVM領域への書き込み許可要求の申請。
    # SPI_STATレジスタのSPI_EXCLビットに”1”を設定し，書き込み許可状態とします。
    def enable_write_nvm(self):
        self.spi.xfer2([WRITE_SPIST | 0x00, 0x00, 0x00, 0x01])

    def write_nvm(self, addr_msb, addr_lsb, data):
        word_len = len(data) // 2
        for i in range(word_len):
            self.spi.xfer2([WRITE_NVM | addr_msb, addr_lsb + i * 2] + data[2*i:2*i+2])
            time.sleep(0.008)    # 1ワード辺り8msの書き込み時間が必要

    def read_model_number(self):
        return self.read_nvm1(0x04, 0x18, 1)

    def read_nvm_user_memory(self, word_len):
        return self.read_nvm1(0x04, 0x22, word_len)

    def write_nvm_user_memory(self, addr_offset, data):
        self.write_nvm(0x04, 0x22 + addr_offset, data)

# Test
if __name__ == '__main__':
    mr793200 = mr793200_controller()
    print(f"read_model_number = {mr793200.read_model_number()}")
    mr793200.enable_write_nvm()
    mr793200.write_nvm_user_memory(0, [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11, 0x12,])
    print(f"USER memoey = {mr793200.read_nvm_user_memory(18)}")
    mr793200.write_nvm_user_memory(0, [0x00]*18)
    print(f"USER memoey = {mr793200.read_nvm_user_memory(18)}")

