"""sensing_foundation のテスト共通フィクスチャ。

一時ディレクトリと設定に関する共通フィクスチャの器をここに置く。
後続タスク（記録・設定まわり）が必要とするフィクスチャは、
実装が進んだ時点で本ファイルへ追加する。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_session_dir(tmp_path: Path) -> Path:
    """記録・ログの出力先として使う一時ディレクトリを返す。

    実際の記録レイアウト（layout.py）や設定解決（config.py）は
    後続タスクで実装されるため、現時点では単純な一時ディレクトリの
    払い出しのみを行う。
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    return session_dir
