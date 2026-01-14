# 手順
1) uv をインストール
- Windows: PowerShell 管理者で `iwr -useb https://astral.sh/uv/install.ps1 | iex`

2) Python 3.11の用意
- 指定バージョンを uv で入れる: `uv python install 3.11`
  - 必ずしも3.11が必要というわけではないので既にPython導入済ならここはスキップしてOKです。
  - うまくいかなかったら上記を実行。

3) 依存の同期
- プロジェクトのルートで: `uv sync`
- これで .venv が生成され、uv.lock に固定された依存関係がインストールされます。

4) 実行
- シートセンシング: `uv run flet run .\src\driver\main.py`
- EVバッテリーセンシング: `uv run flet run .\src\battery\main.py`
- 2回目以降はここだけやればOK。


