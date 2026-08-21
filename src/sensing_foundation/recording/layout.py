"""セッション記録のディレクトリ規約・形式版・manifest スキーマを1箇所に固定する
（design.md「RecordingLayout」、要件 5.2 / 5.9 / 12.6）。

**セッション記録は Throw Record とは別の記録である。** 本モジュールが扱う
「観測フレームの記録」（`var/sessions/<session_id>/` 配下の4ファイル: 生の
Depth フレーム列とその索引・メタ情報）は、投擲の検出結果を1行1件で蓄積する
Throw Record（`var/throws/<name>.ndjson`、`prediction_core.ThrowRecord` の
スキーマをそのまま使う別モジュールの責務）とは**別の記録**であり、Throw
Record のスキーマへ観測フレームの情報を押し込まない（要件 5.9）。両者は
`session_id` という同じ識別子（後述）を介してのみ対応付く。この対応付けは
`ThrowRecord.extra["sensing"] = {"session_id", "frame_index_from",
"frame_index_to"}` という規約で行われる（design.md「Throw Record」節、
要件 7.7）が、その配線自体は本モジュールの責務ではなく、Throw Record
保存モジュール（将来のタスク）が行う。

**ディレクトリ規約（design.md「セッション記録（フレーム層）」）**::

    <root>/<session_id>/
    ├── manifest.json      # セッションメタ（1個）
    ├── frames.ndjson      # 1行1フレームの索引
    ├── depth.bin          # Depth の生バッファを連結
    └── summary.json       # 終了時サマリ（書きかけなら不在）

4ファイルの名前は `MANIFEST_NAME` / `INDEX_NAME` / `BLOB_NAME` /
`SUMMARY_NAME` としてこのモジュールにのみ定義し、他モジュールはこれらの
定数を参照する（ファイル名を各所に埋め込まない）。`summary.json` の**不在**
が「書きかけ（正常に閉じていない）セッション」の唯一の識別手段である
（design.md「Consistency & Integrity」）。

**形式版**: `RECORDING_FORMAT_VERSION` は manifest の `format_version` に
書き込む値であり、記録の内容・並びを変更する際は必ず上げる。**読み側は
未知の版を推測して読み替えてはならない**（design.md「Risks」。版の検証・
拒否そのものは `SessionReader`、将来のタスクの責務であり、本モジュールは
版の定数を提供するのみである）。

**セッション識別子**: `new_session_id()` が `"YYYYMMDDTHHMMSSZ-<8 hex>"`
形式（UTC）の識別子を生成する。時刻部分は呼び出し側が渡す `now_wall_ms`
（epoch ms）から導出し、本関数自身は `time.time()` を読まない（`SessionClock`
のような呼び出し側がアンカ済みの壁時計を渡せるようにするための純関数設計）。
8桁の16進接尾辞（`secrets.token_hex(4)`）が衝突を防ぐ主体であり、同一秒に
複数セッションが開始しても衝突しない（同一の `now_wall_ms` を渡し続けても
接尾辞の乱数だけで衝突しないことをテストで固定している）。

この `session_id` は、記録ディレクトリ名・構造化ログのファイル名
（`var/logs/<session_id>.ndjson`）・`SessionClock.session_id`・Throw
Record の `extra["sensing"]["session_id"]` の4箇所で**同じ値**を共有する
（design.md「RecordingLayout / Integration」、要件 7.7）。ただし `session_id`
を実際に生成して各所へ配るのは `SessionClock`（既存タスク 1.3）や
`SessionRecorder`（将来のタスク 4.3）の責務であり、本モジュールが提供する
のは生成規則そのもの（`new_session_id()`）とディレクトリ計算
（`session_dir()`）のみである。

**出力先の既定**: `DEFAULT_SESSIONS_ROOT` は `var/sessions` を指す相対
`Path` であり、リポジトリの版管理対象外である（`.gitignore` が `var/` を
一括で除外する。要件 12.6）。実際にどの working directory を基準に解決
するかは呼び出し側（CLI・`RuntimeSettings`）の責務であり、本モジュールは
既定値の提供のみを行う。

**既存ディレクトリへの上書き拒否**: `session_dir()` はディレクトリの
存在確認や作成を一切行わない、副作用のない純粋なパス計算である
（`root / session_id` を返すのみ）。ディレクトリの**作成**と、既存
ディレクトリへの上書き拒否という Idempotency 制約（design.md「Batch /
Job Contract」）は `create_session_dir()` が担う。`session_id` はそもそも
衝突しない設計だが、それでも呼び出し側が同じ `session_id` を再利用した
場合（例: バグ、テスト、リプレイの誤操作）に記録を静かに上書きしないための
最後の防衛線として、`create_session_dir()` は既存ディレクトリを検出すると
`errors.SensingConfigError` を送出する。これは「呼び出し方の誤り」区分
（`errors.py` 参照）に該当する——正しく使えば `session_id` は毎回新規で
あり、同じディレクトリの再利用は呼び出し側の誤りだからである。

**manifest の読み書き**: `write_manifest()` / `read_manifest()` は
`manifest.json` に対する薄い JSON 往復のみを行う。渡された辞書を
`json.dumps(..., ensure_ascii=False, allow_nan=False)` で直列化して書き、
読み出し時は `json.load()` の結果をそのまま返す。**manifest の中身
（`format_version` / `session_id` / `source` / `profile` / `intrinsics` /
`device` / `runtime` / `capture` / `blob` という design.md の Data Models
が定めるキー構成）を検証するバリデータはここには持たない**——manifest
辞書を正しく組み立てる責務は `SessionRecorder`（将来のタスク 4.3）にあり、
本モジュールが保証するのは「書いた辞書を読み戻すと同一になる」という
往復の正しさのみである。`write_manifest()` はディレクトリの作成を
行わない（`create_session_dir()` で作成済みのディレクトリへ書き込む
ことを前提とする）。
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from sensing_foundation.errors import SensingConfigError

RECORDING_FORMAT_VERSION: str = "1.0"
"""manifest.json の `format_version` に書く現行の記録形式版。

記録の内容・並びを変更する際は必ず値を上げる。読み側（`SessionReader`、
将来のタスク）は未知の版を推測して読み替えてはならない。
"""

MANIFEST_NAME: str = "manifest.json"
"""セッションメタ情報（1個）のファイル名。"""

INDEX_NAME: str = "frames.ndjson"
"""1行1フレームの索引のファイル名。"""

BLOB_NAME: str = "depth.bin"
"""Depth の生バッファを連結したファイル名。"""

SUMMARY_NAME: str = "summary.json"
"""終了時サマリのファイル名。書きかけのセッションではこのファイルが存在しない。"""

DEFAULT_SESSIONS_ROOT: Path = Path("var") / "sessions"
"""セッション記録の既定出力先。版管理対象外（`.gitignore` の `var/` に含まれる）。"""

_SESSION_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def new_session_id(now_wall_ms: float) -> str:
    """`"YYYYMMDDTHHMMSSZ-<8 hex>"` 形式（UTC）のセッション識別子を生成する。

    時刻部分は `now_wall_ms`（epoch ms）から導出する。本関数自身は
    `time.time()` を読まない純関数であり、`SessionClock` のように既に
    アンカ済みの壁時計を持つ呼び出し側がその値をそのまま渡せる。

    8桁の16進接尾辞（`secrets.token_hex(4)`）が衝突を防ぐ主体である。
    同一の `now_wall_ms`（同一秒）を渡して繰り返し呼び出しても、接尾辞の
    暗号論的乱数により実用上衝突しない。

    Args:
        now_wall_ms: セッション開始の壁時計（epoch ms）。

    Returns:
        `"YYYYMMDDTHHMMSSZ-<8 hex>"` 形式の識別子。
    """
    started_at = datetime.fromtimestamp(now_wall_ms / 1000.0, tz=timezone.utc)
    timestamp_part = started_at.strftime(_SESSION_ID_TIMESTAMP_FORMAT)
    suffix = secrets.token_hex(4)
    return f"{timestamp_part}-{suffix}"


def session_dir(root: Path, session_id: str) -> Path:
    """セッションディレクトリのパスを計算する（副作用なし）。

    ディレクトリの存在確認・作成は一切行わない、純粋なパス計算である。
    ディレクトリを実際に作る場合は `create_session_dir()` を使うこと。

    Args:
        root: セッション記録の出力先ルート（例: `DEFAULT_SESSIONS_ROOT`）。
        session_id: `new_session_id()` が生成した識別子。

    Returns:
        `root / session_id`。
    """
    return Path(root) / session_id


def create_session_dir(root: Path, session_id: str) -> Path:
    """セッションディレクトリを新規作成する。既存ディレクトリへの上書きは拒否する。

    design.md「RecordingLayout / Batch, Job Contract」の Idempotency &
    recovery 制約（「既存ディレクトリへの上書きは拒否する」）を実装する。
    `root` が未作成の場合は親ディレクトリごと作成する。

    Args:
        root: セッション記録の出力先ルート。
        session_id: `new_session_id()` が生成した識別子。

    Returns:
        新規作成したセッションディレクトリのパス。

    Raises:
        SensingConfigError: `session_dir(root, session_id)` が既に存在する
            場合（呼び出し方の誤り。`session_id` を再生成するか、既存の
            記録を残したまま新しいセッションとして扱うこと）。
    """
    directory = session_dir(root, session_id)
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SensingConfigError(
            f"セッションディレクトリは既に存在するため上書きを拒否した: {directory}"
            "（既存の記録を上書きしない。session_id を再生成すること）"
        ) from exc
    return directory


def write_manifest(session_dir: Path, manifest: Mapping[str, object]) -> None:
    """manifest 辞書を `session_dir/manifest.json` へ書く。

    渡された辞書の内容を検証しない（manifest を正しく組み立てる責務は
    呼び出し側にある）。ディレクトリの作成も行わない
    （`create_session_dir()` で作成済みのディレクトリへ書き込むことを前提
    とする）。

    Args:
        session_dir: `session_dir()` / `create_session_dir()` が返した
            セッションディレクトリ。
        manifest: 書き込む manifest 辞書（design.md の Data Models が定める
            キー構成。本関数はキー構成を検証しない）。
    """
    manifest_path = Path(session_dir) / MANIFEST_NAME
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, allow_nan=False)


def read_manifest(session_dir: Path) -> dict[str, object]:
    """`session_dir/manifest.json` を読み、辞書として返す。

    `write_manifest()` で書いた辞書をそのまま読み戻す（往復の正しさのみを
    保証し、内容の検証は行わない）。

    Args:
        session_dir: `session_dir()` / `create_session_dir()` が返した
            セッションディレクトリ。

    Returns:
        `manifest.json` の内容をそのまま `dict` として返す。
    """
    manifest_path = Path(session_dir) / MANIFEST_NAME
    with manifest_path.open(encoding="utf-8") as f:
        return json.load(f)
