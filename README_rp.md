# 役割
世界最高峰のPythonエンジニアとして振舞ってください。

# タスク
以下の問題を解いてください。

# 問題
以下の条件を満たすFletデスクトップアプリのPythonコードを作成する。

# 問題の進め方
- コードにあたる部分(コメントを含む)はコードスニペットを利用してください。
- 情報が不足している場合はその旨を明記してください。
- PCA9539PWRの初期化処理で追加すべき処理があればコードを提示する前に教えてください。

## 条件
- Raspberry Pi 4B上で動作すること(OSはTrixie)。
- Python3.11を使用してください。
- Pythonコード名をmain.pyとする。
- GUIライブラリは"Flet"を使用してください。
- デスクトップアプリとして作成してください。
- GPIO2(物理ピンは3番)(SDA)とGPIO3(物理ピンは5番)(SCL)を使ってTI社製PCA9539PWRとI2C通信をします。
- TI社製PCA9539PWRの制御に関しては以下の通りです。
  - PCA9539PWRはTSSOPパッケージで、スレーブアドレスは"0x74"です。
  - まずすべての制御対象ピンを出力に設定するため、Configurationレジスタ(0x06, 0x07)に0x00を書き込みます。
  - 出力極性(Polarity Inversionレジスタ 0x04, 0x05)は0x00(反転なし)を書き込みます。
  - 出力初期値(Output Portレジスタ 0x02, 0x03)は0x00(全てLow)へ初期化してからUIのスイッチ操作に応じて変更します。
- GPIO4(物理ピンは7番)を入力ポートに設定します。チャタリングや浮き対策として内部プルダウンを設定してください。
- GPIO15(物理ピンは10番)を出力ポートに設定します。初期値は"Low"を出力してください。GPIO4が"High"になったら"100ms"後にGPIO15を"High"にします。
  - これはPCA9539PWRのリセット解除を意味します。
- GPIO15を"High"にした後、"100ms"後にI2C通信を開始します。初期化書き込みと同時にレジスタ読出しで設定の反映を確認してください(エラーハンドリング強化)。
- GUIの説明は以下の通りです。
  - CupertinoSwitchを16個使用します。4行4列で配置してください。
  - "Cell 01"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの4番ピン(P00)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 02"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの5番ピン(P01)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 03"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの6番ピン(P02)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 04"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの7番ピン(P03)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 05"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの8番ピン(P04)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 06"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの9番ピン(P05)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 07"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの10番ピン(P06)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 08"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの11番ピン(P07)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 09"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの13番ピン(P10)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 10"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの14番ピン(P11)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 11"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの15番ピン(P12)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 12"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの16番ピン(P13)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 13"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの17番ピン(P14)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 14"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの18番ピン(P15)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 15"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの19番ピン(P16)を"High"にして、オフのとき"Low"にしてください。
  - "Cell 16"CupertinoSwitch(初期値オフ)がオンのときPCA9539PWRの20番ピン(P17)を"High"にして、オフのとき"Low"にしてください。
  - 4行4列のCupertinoSwitchの下部に、GPIO4とGPIO15の論理レベルを表示してください。0.5秒おきに表示を更新してください。


# レジスタの説明
入力ポートレジスタ（レジスタ0および1）は、設定レジスタでそのピンが入力か出力かに関わらず、ピンに入力される論理レベルを反映する。これは読み取り操作のときのみ作用する。これらのレジスタへの書き込みは効果を持たない。既定値（X）は外部から印加された論理レベルによって決定される。読み取り操作の前には、次に入力ポートレジスタへアクセスすることをI2Cデバイスに示すため、コマンドバイトを伴う書き込み転送が送られる。

出力ポートレジスタ（レジスタ2および3）は、設定レジスタで出力に定義されたピンの出力論理レベルを示す。このレジスタ内のビット値は、入力に定義されたピンには影響しない。また、このレジスタの読み取りは、実ピンの値ではなく、出力選択を制御するフリップフロップに保持されている値を反映する。

極性反転レジスタ（レジスタ4および5）は、設定レジスタで入力に定義されたピンの極性反転を可能にする。このレジスタのビットがセット（1を書き込み）されている場合、対応するポートピンの極性は反転される。このレジスタのビットがクリア（0を書き込み）されている場合、対応するポートピンの元の極性が保持される。

設定レジスタ（レジスタ6および7）は、I/Oピンの方向を設定する。このレジスタのビットが1に設定されると、対応するポートピンは入力として有効になり、出力ドライバは高インピーダンス状態になる。このレジスタのビットが0にクリアされると、対応するポートピンは出力として有効になる。


# メモ
## デバイスファイルの確認
```
ls -l /dev/i2c-1
```
でデバイスファイルが無ければ次の「I2Cの有効化」へ。

## I2Cの有効化
```
sudo raspi-config
# Interface Options -> I2C -> Enable
sudo reboot
```
再起動後もう一度以下実施すればOKなはず。
```
ls -l /dev/i2c-1
```

## バスの動作確認
i2c-tools を使うとバス・接続デバイスが見えるか簡単に確認可能
```
sudo apt update
sudo apt install -y i2c-tools

# バス1をスキャン(非rootでもi2cグループに入っていれば動作します)
i2cdetect -y 1
```
PCA9539PWRが接続・電源投入済みで、リセット解除されていて、プルアップが正しく入っている場合は、表に 74 が表示される(スレーブアドレス0x74)。

Pythonで確認する場合は以下スクリプトを使う。
```
# test_i2c.py
from smbus2 import SMBus
try:
    bus = SMBus(1)  # /dev/i2c-1
    print("I2C bus opened OK")
    bus.close()
except Exception as e:
    print("I2C open failed:", e)
```


## uvを使った環境構築

1) uv をインストール
- curl -Ls https://astral.sh/uv/install.sh | sh
- PATH 追加（必要なら）
  - echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
  - exec $SHELL

2) Python 3.11 の用意（uv が自動で取得・管理）
- 明示的に取得したい場合: uv python install 3.11
- インストール済みの確認: uv python list

3) プロジェクト用の仮想環境を Python 3.11 で作成
- 任意のプロジェクトフォルダで:
  - uv init
  - uv sync

4) パッケージ管理（uv を pip 互換で使用）
- 例: uv pip install requests
- 速度が非常に速く、依存解決も安定しています。
- 既存の requirements.txt があれば: uv pip install -r requirements.txt


補足（プロジェクト設定で 3.11 を要求する）
- pyproject.toml に requires-python = ">=3.11, <3.12" を書くと、uv はその範囲に合う Python（3.11）を選びます。これによりチーム全体でのバージョン統一が容易になります。


最短クイックスタート（64bit Pi OS 前提）
- curl -Ls https://astral.sh/uv/install.sh | sh
- echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && exec $SHELL
- mkdir RFID && cd RFID
- uv init
- uv sync
- uv pip install -r requirements.txt


## VSCodeのインストール
Microsoft 公式リポジトリからインストール
- 事前確認
  - uname -m で aarch64 であることを確認
  - sudo apt update でパッケージ情報を最新化

- リポジトリ追加とインストール
  - sudo apt install -y wget gpg apt-transport-https
  - wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor | sudo tee /usr/share/keyrings/packages.microsoft.gpg > /dev/null
  - echo 'deb [arch=arm64 signed-by=/usr/share/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main' | sudo tee /etc/apt/sources.list.d/vscode.list
  - sudo apt update
  - sudo apt install -y code

- 起動
  - メニューから「Visual Studio Code」
  - もしくはターミナルで code

トラブル対処のヒント
- 画面が真っ黒・クラッシュする場合
  - code --disable-gpu で起動してみる（GPU ドライバ相性回避）
- 日本語フォントが欠ける場合
  - sudo apt install -y fonts-noto-cjk fonts-noto-color-emoji
- 自動更新
  - sudo apt update && sudo apt upgrade で更新できます

Python と uv の連携（プロジェクトを VS Code で開く時）
- ワークスペースに .venv がある場合、VS Code の「Python」拡張が自動で検出します
- 見つからない時はコマンドパレットで「Python: Select Interpreter」を選び、プロジェクトの .venv/bin/python を指定
- ターミナルで uv を使う場合は、統合ターミナルを開いて uv コマンドをそのまま実行すればOKです（例: uv sync、uv run）

