"""Throw Record の保存層 `ThrowRecordStore`（design.md「ThrowRecordStore」、
タスク 5、要件 7.1-7.8）。

`prediction_core.ThrowRecord`（サンプル層のスキーマ、`SCHEMA_VERSION` 1.0）を
NDJSON（1行1レコード）で追記・読み出しする**薄い**永続化層である。**スキーマは
`prediction_core` が確定済みであり、本モジュールは再定義しない**（要件 7.1）。
直列化・復元も `ThrowRecord.to_json()` / `ThrowRecord.from_dict()` という
`prediction_core` 自身が提供する手段のみを用いる（要件 7.2）。

**本モジュールは `types.py` と並び、`prediction_core` を import してよい
2モジュールのうちの1つである**（design.md「Dependency Direction」）。
`types.py` の参照が `SourceKind` の再エクスポートのみに限られるのに対し、
**Throw Record スキーマ関連シンボル（`ThrowRecord` / `SCHEMA_VERSION` /
`RecordSchemaError`）を参照するのは本モジュールだけ**であり、「スキーマの
参照点が1箇所しかない」ことを物理的に保証する（要件 7.8）。**`prediction_core`
の内部モジュール（`prediction_core.record` など）は一切 import せず、公開入口
（`from prediction_core import ...`）のみを経由する。**

**`iter_records()` と `iter_with_issues()` の使い分け**（design.md のコード
ブロックの `# 破損行で停止する` という注記の解釈。tasks.md 5 の観測可能な
完了状態を、簡潔な経路と頑健な経路の2つへ分けて満たす）:

- `iter_records()` は**簡潔・厳格な経路**である。行が JSON として解析できない、
  `prediction_core` のスキーマ検証に失敗する、または `schema_version` が
  `prediction_core.SCHEMA_VERSION` と異なる場合、その行に到達した時点で
  `ThrowRecordFormatError`（版不一致は `ThrowRecordVersionError`、
  `ThrowRecordFormatError` の一種）を送出してジェネレータを終了する。
  それより前に読めた行はすでに呼び出し側へ yield 済みであり、失われない
  （`SessionReader` が索引行の破損に対して `RecordingFormatError` を送出する
  のと対になる設計）。自分の書いたデータを信頼する呼び出し側、または
  fail-fast を望む呼び出し側のための経路である。
- `iter_with_issues()` は**頑健な経路**である。破損行・版不一致行に到達しても
  例外を送出せず、`ThrowRecordReadIssue`（行番号・種別・詳細）を値として
  yield し、後続の健全な行の読み出しを続ける（要件 7.5）。`schema_version`
  不一致は `"version_mismatch"` として報告し、**内容を推測して読み替える
  ことはしない**（`from_dict` へは渡さず、`schema_version` を確認した時点で
  即座に issue を返す。要件 7.6）。

**行の検証は互いに独立している**（design.md「Validation」）: 各行を個別に
`json.loads` → `schema_version` 照合 → `ThrowRecord.from_dict` の順で検証し、
ある行の破損は隣接する行の読み出し可否に一切影響しない。

**セッション記録との対応付け**（要件 7.7）: `ThrowRecord.extra` は
`prediction_core` が用意した加算的拡張の退避先である（D-8）。本モジュールは
`link_to_session()` を通じて `extra["sensing"] = {"session_id",
"frame_index_from", "frame_index_to"}` という**1つの名前空間キー**にのみ
書き込み、`extra` の他のキーを一切変更しない（既存の他ユーザーの拡張と
衝突しないため）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from prediction_core import SCHEMA_VERSION, RecordSchemaError, ThrowRecord

from sensing_foundation.errors import ThrowRecordFormatError, ThrowRecordVersionError

__all__ = ["ThrowRecordReadIssue", "ThrowRecordStore", "link_to_session"]


@dataclass(frozen=True, slots=True)
class ThrowRecordReadIssue:
    """`ThrowRecordStore.iter_with_issues()` が破損行・版不一致行を報告する値。

    Attributes:
        line_no: 1始まりの行番号（NDJSON ファイル中の物理行）。
        kind: 問題の種別。
            - `"malformed_json"`: 行が JSON として解析できない。
            - `"schema_error"`: JSON としては解析できたが、
              `prediction_core.ThrowRecord.from_dict()` のスキーマ検証に
              失敗した（必須キー欠落・型不一致など）。
            - `"version_mismatch"`: `schema_version` が
              `prediction_core.SCHEMA_VERSION` と異なる。内容は
              `from_dict` へ渡さず、推測して読み替えない（要件 7.6）。
        detail: 人が読める詳細説明（元の例外メッセージ、または不一致の内容）。
    """

    line_no: int
    kind: Literal["malformed_json", "schema_error", "version_mismatch"]
    detail: str


def _parse_line(line_no: int, raw: str) -> ThrowRecord | ThrowRecordReadIssue:
    """NDJSON の1行を解析する（`iter_records()` / `iter_with_issues()` の共通実装）。

    例外を送出せず、常に `ThrowRecord`（成功）か `ThrowRecordReadIssue`
    （失敗、行番号・種別・詳細つき）のいずれかを返す。呼び出し側
    （`iter_records()`）は issue を例外へ変換するかどうかを自分で決める。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return ThrowRecordReadIssue(line_no=line_no, kind="malformed_json", detail=str(e))

    if not isinstance(data, dict):
        return ThrowRecordReadIssue(
            line_no=line_no,
            kind="schema_error",
            detail=f"行の JSON が object ではない（{type(data).__name__} だった）",
        )

    # schema_version の照合は from_dict へ渡す前に行う。不一致の行の内容を
    # 推測して読み替えることはしない（要件 7.6）。
    line_schema_version = data.get("schema_version", SCHEMA_VERSION)
    if line_schema_version != SCHEMA_VERSION:
        return ThrowRecordReadIssue(
            line_no=line_no,
            kind="version_mismatch",
            detail=(
                f"schema_version が不一致: このストアが扱えるのは "
                f"{SCHEMA_VERSION!r} だが、行の値は {line_schema_version!r}"
            ),
        )

    try:
        return ThrowRecord.from_dict(data)
    except RecordSchemaError as e:
        return ThrowRecordReadIssue(line_no=line_no, kind="schema_error", detail=str(e))


class ThrowRecordStore:
    """`prediction_core.ThrowRecord` を NDJSON へ追記・読み出しする（design.md「ThrowRecordStore」）。

    Preconditions:
        `append()` に渡すレコードの `schema_version` は
        `prediction_core.SCHEMA_VERSION` と一致すること。

    Postconditions:
        `append()` した内容は `iter_records()` で等価なレコードとして
        読み戻せる（要件 7.3）。

    Invariants:
        1行 = 1レコード。行内に改行を含めない
        （`ThrowRecord.to_json(indent=None)` を用いる）。
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: ThrowRecord) -> None:
        """`record` を1行の JSON として末尾へ追記する（要件 7.2 / 7.4）。

        親ディレクトリが存在しない場合は作成する。1レコード = 1回の
        `open()`/`write()`/`close()` であり、投擲のたびに呼ばれる程度の
        頻度を想定する（高頻度のログ送出とは異なりファイルを開いたまま
        保持しない）。
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        line = record.to_json(indent=None)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _iter_raw_lines(self) -> Iterator[tuple[int, str]]:
        """ファイルの各行を `(1始まりの行番号, 行内容)` として返す。

        ファイルが未作成（一度も `append()` されていない）場合は空の反復に
        なる。末尾の空行は無視する。
        """
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for line_no, raw in enumerate(f, start=1):
                stripped = raw.rstrip("\n")
                if not stripped:
                    continue
                yield line_no, stripped

    def iter_records(self) -> Iterator[ThrowRecord]:
        """保存された `ThrowRecord` を先頭から1件ずつ返す（**破損行で停止する**）。

        破損行（JSON 解析失敗・スキーマ検証失敗）に到達すると
        `ThrowRecordFormatError` を、`schema_version` 不一致に到達すると
        その一種である `ThrowRecordVersionError` を送出してジェネレータを
        終了する。それより前に yield 済みの行は失われない。後続の健全な
        行も読み進めたい場合は `iter_with_issues()` を使うこと（要件 7.5）。
        """
        for line_no, raw in self._iter_raw_lines():
            result = _parse_line(line_no, raw)
            if isinstance(result, ThrowRecordReadIssue):
                if result.kind == "version_mismatch":
                    raise ThrowRecordVersionError(f"{line_no}行目: {result.detail}")
                raise ThrowRecordFormatError(
                    f"{line_no}行目 [{result.kind}]: {result.detail}"
                )
            yield result

    def iter_with_issues(self) -> Iterator[ThrowRecord | ThrowRecordReadIssue]:
        """保存内容を先頭から1件ずつ返す（**破損行・版不一致行でも読み進める**）。

        健全な行は `ThrowRecord` として、破損行・版不一致行は
        `ThrowRecordReadIssue`（行番号・種別・詳細）として、ファイル中の
        元の順序のまま yield する。1つの行の問題が他の行の読み出しを
        妨げない（要件 7.5 / 7.6）。
        """
        for line_no, raw in self._iter_raw_lines():
            yield _parse_line(line_no, raw)

    def count(self) -> int:
        """保存されている行（レコード）数を返す。"""
        return sum(1 for _ in self._iter_raw_lines())


def link_to_session(
    record: ThrowRecord,
    session_id: str,
    frame_index_from: int,
    frame_index_to: int,
) -> ThrowRecord:
    """観測フレーム記録（セッション記録）との対応付けを `record.extra` へ設定した**新しい**レコードを返す。

    `prediction_core.ThrowRecord` は不変（`frozen=True`）であるため、`record`
    自体は変更せず `dataclasses.replace()` で新しいインスタンスを構築する。

    対応付けは `extra["sensing"] = {"session_id", "frame_index_from",
    "frame_index_to"}` という**1つの名前空間キー**にのみ書き込む。`record.extra`
    に既に存在する他のキーはそのまま維持され、上書き・削除されない（要件 7.7、
    design.md「ThrowRecordStore」Implementation Notes）。

    Args:
        record: 対応付けを足す元のレコード（変更しない）。
        session_id: 対応付け先のセッション記録の識別子。
        frame_index_from: 範囲の先頭。**記録側の通し番号**（セッション記録の
            索引行の `i`。記録時の `CaptureFrame.index`）であり、**索引ファイル
            の行位置ではない**。両者はリングが古いフレームを追い出したときに
            食い違う（タスク 4.7。design.md「SessionReader /『フレーム番号』が
            指す3つの量」）。
        frame_index_to: 範囲の末尾。`frame_index_from` と同じ量で、**両端を
            含む閉区間**である（`frame_index_from == frame_index_to` は1枚を
            指す）。

    Returns:
        `extra["sensing"]` を設定した新しい `ThrowRecord`。

    保存した範囲からフレームを取り直すには
    `SessionReader.iter_recorded_range(frame_index_from, frame_index_to)` を
    使う。行位置を期待する `SessionReader.read()` へそのまま渡してはならない
    ——追い出しが起きた記録では例外にならずに**別のフレームを返し得る**。

    行位置ではなく記録側の通し番号を採るのは、要件 7.7 が求めるのが「後から
    対応付けられる**識別子**」だからである。行位置はファイル内の位置であって
    識別子ではなく、記録を切り詰めれば同じ値が別のフレームを指す。
    """
    new_extra = dict(record.extra)
    new_extra["sensing"] = {
        "session_id": session_id,
        "frame_index_from": frame_index_from,
        "frame_index_to": frame_index_to,
    }
    return replace(record, extra=new_extra)
