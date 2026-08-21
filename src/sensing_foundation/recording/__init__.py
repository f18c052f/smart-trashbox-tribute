"""セッション記録（record/replay）を担うサブパッケージ（design.md「L5: 取得と永続化」）。

`layout.py`（ディレクトリ規約・ファイル名・形式版・manifest スキーマ）を皮切りに、
`ringbuffer.py`（直近N秒保持）・`writer.py`（`SessionRecorder`）・`reader.py`
（`SessionReader`）を今後このパッケージへ追加していく（design.md File Structure
Plan）。本ファイル自体はサブパッケージのマーカーであり、ロジックを持たない。
"""

from __future__ import annotations
