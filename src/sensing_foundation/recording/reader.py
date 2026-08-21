"""セッション記録の読み出しを実装する `SessionReader`（design.md「SessionReader」、
タスク 4.4、要件 6.2 / 6.6）。

`SessionReader` は `manifest.json` / `frames.ndjson`（索引） / `depth.bin`
（Depth 生バッファの連結） / `summary.json`（終了時サマリ、書きかけなら不在）
の4ファイルを読み、`recording/writer.py`（`SessionRecorder`、タスク 4.3）が
書いた内容を過不足なく復元する**受動的な**部品である。書き出し側の逆写像として
実装する（`_write_frame()` が書いたキー・順序・圧縮の扱いをそのまま逆にたどる）。

**mmap ではなく通常の seek/read を使う（design.md Implementation Notes）**:
`numpy.frombuffer` と組み合わせた通常のファイル I/O のみを用いる。WSL 上の
大きなファイルでも挙動が単純になることを優先する。

**検証の方針（要件 6.6「部分的な読み出しを成功として返さない」）**: 構築時
（`__init__`）に次の2段階を**必ず完了させる**。どちらかで失敗すれば
`SessionReader` インスタンスは一切構築されず、以後 `read()` / `iter_frames()`
を呼べる状態にはならない——「途中まで読めたことにして返す」余地を構造的に
無くすための設計である。

    1. `format_version` の検証（未知なら `RecordingVersionError`）。
    2. `frames.ndjson` の**全行**を JSON として解析し（1行でも壊れていれば
       即座に `RecordingFormatError`）、各行のブロブ位置・長さが
       `depth.bin` の実サイズを超えないことを検証する（超えていれば
       `RecordingFormatError`）。

    これらは「索引行の JSON 破損」「オフセット超過」に対応する
    （design.md「索引行の JSON 破損・オフセット超過・長さ不一致 →
    RecordingFormatError」の前半2つ）。

一方、**圧縮後バイト列を展開した結果の長さが索引の `raw_len` と一致するか**
（同一文の「長さ不一致」）は、対象のバイト列を実際に読んで展開しなければ
検証できないため、`read(i)` / `iter_frames()` の実行時に検証する。この検証は
`try/except` で握り潰さず、呼び出し側へ**そのまま伝播させる**
（`iter_frames()` の反復を打ち切って例外を投げる。残りのフレームを黙って
捨てて「そこまでの結果」を正常終了として返すことはしない）。

**書きかけのセッション（`summary.json` 不在）は例外にしない
（design.md「Risks」）**: `summary.json` が無い場合、`self.summary` は
`None` になるが、構築自体は失敗させず、`warnings.warn()`
（`InProgressSessionWarning`）で警告するのみにとどめる。`frames.ndjson` に
実在する（かつ形式的に正しい）行の範囲であれば通常どおり読める。これは
「クラッシュ・電源断で正常に閉じられなかった記録でも、書けたところまでは
解析に使いたい」という要求（要件の直接の文言ではなく design.md の運用上の
要求）に応えるためであり、要件 6.6 の「破損」（形式が壊れている）とは
区別される——書きかけであること自体は破損ではない。

**`read(i)` は内部状態を持たない（design.md Invariants、要件 6.2）**:
`read(i)` はブロブファイルを毎回 `open()` し直し、索引が記録したオフセット・
長さで `seek()`/`read()` するのみで、「現在位置」のようなカーソルを一切
保持しない。したがって `read(5)` → `read(2)` → `read(5)` の順で呼んでも、
2回目の `read(5)` は1回目と完全に同じ結果を返す。`iter_frames()` もこの
`read(i)` を `i = 0, 1, ..., len(self)-1` の順に呼ぶだけの薄い generator
であり、`SessionReader` インスタンス自体には反復用の可変状態を持たせない
ため、同一インスタンスを複数回反復しても同一系列になる（要件 6.2）。
"""

from __future__ import annotations

import json
import warnings
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sensing_foundation.errors import RecordingFormatError, RecordingVersionError
from sensing_foundation.recording import layout
from sensing_foundation.types import (
    CameraIntrinsics,
    CaptureFrame,
    SourceKind,
    StreamProfile,
    TimestampDomain,
)

__all__ = ["InProgressSessionWarning", "SessionReader"]


class InProgressSessionWarning(UserWarning):
    """`summary.json` が存在しない（書きかけの）セッションを読んだ際に送出する警告。

    構築（`SessionReader.__init__`）は失敗させず、読める範囲までのフレームを
    引き続き読めることを示す（design.md「SessionReader」Risks）。
    """


@dataclass(frozen=True, slots=True)
class _IndexEntry:
    """`frames.ndjson` の1行を解析した結果（`writer.py._build_index_line` の逆写像）。"""

    i: int
    seq: int
    t_capture_ms: float
    device_ts_ms: float | None
    ts_domain: str
    capture_latency_ms: float | None
    off: int
    len: int
    raw_len: int
    dropped_before: int
    gap_before: int


_REQUIRED_INDEX_KEYS = (
    "i",
    "seq",
    "t_capture_ms",
    "device_ts_ms",
    "ts_domain",
    "capture_latency_ms",
    "off",
    "len",
    "raw_len",
    "dropped_before",
    "gap_before",
)


class SessionReader:
    """`SessionRecorder` が書いたセッション記録を読み出す（design.md「SessionReader」）。

    Preconditions:
        `manifest.json` が存在し、`format_version` が既知であること
        （満たさなければ `__init__` が `RecordingVersionError` を送出する）。

    Postconditions:
        `iter_frames()` は索引の順序どおりにフレームを返す。同一
        `SessionReader` インスタンスを複数回反復しても同一系列になる
        （要件 6.2）。

    Invariants:
        `read(i)` はブロブのオフセットとバイト長のみで決まり、内部の
        可変状態（カーソル等）に依存しない。
    """

    def __init__(self, session_dir: Path) -> None:
        self._session_dir = Path(session_dir)
        self._manifest: dict[str, object] = layout.read_manifest(self._session_dir)

        format_version = self._manifest.get("format_version")
        if format_version != layout.RECORDING_FORMAT_VERSION:
            raise RecordingVersionError(
                "未知の記録形式版のため読み出せない: "
                f"format_version={format_version!r}"
                f"（このリーダーが扱えるのは {layout.RECORDING_FORMAT_VERSION!r} のみ。"
                "内容を推測して読み替えることはしない）"
            )

        self._profile = self._build_profile(self._manifest)
        self._source_kind = SourceKind(self._manifest.get("source"))

        blob_meta = self._manifest.get("blob")
        blob_meta = blob_meta if isinstance(blob_meta, Mapping) else {}
        self._compression = blob_meta.get("compression", "none")

        self._blob_path = self._session_dir / layout.BLOB_NAME
        self._blob_size = self._blob_path.stat().st_size if self._blob_path.exists() else 0

        self._summary = self._read_summary()

        self._entries: list[_IndexEntry] = self._read_index()
        self._validate_entries(self._entries)

    # -- 公開インタフェース ---------------------------------------------------

    @property
    def profile(self) -> StreamProfile:
        return self._profile

    @property
    def manifest(self) -> Mapping[str, object]:
        return self._manifest

    @property
    def summary(self) -> Mapping[str, object] | None:
        return self._summary

    def __len__(self) -> int:
        return len(self._entries)

    def read(self, i: int) -> CaptureFrame:
        """索引位置 `i` のフレームを、ブロブから毎回読み直して再構成する。

        内部にカーソルを持たないため、任意の順序・任意の回数呼び出しても
        同じ `i` に対しては常に同じ結果を返す（design.md Invariants）。

        Raises:
            IndexError: `i` が `0 <= i < len(self)` の範囲外の場合。
            RecordingFormatError: ブロブの実体（`off`/`len` が指す範囲）が
                読み出せない、または展開後の長さが索引の `raw_len` と
                一致しない場合。
        """
        if i < 0 or i >= len(self._entries):
            raise IndexError(
                f"読み出し位置が範囲外である: i={i}, 有効範囲=[0, {len(self._entries)})"
            )
        entry = self._entries[i]
        depth = self._reconstruct_depth(entry)
        return CaptureFrame(
            index=entry.i,
            seq=entry.seq,
            t_capture_ms=entry.t_capture_ms,
            device_timestamp_ms=entry.device_ts_ms,
            timestamp_domain=TimestampDomain(entry.ts_domain),
            capture_latency_ms=entry.capture_latency_ms,
            depth=depth,
            profile=self._profile,
            source=self._source_kind,
            dropped_before=entry.dropped_before,
            gap_before=entry.gap_before,
        )

    def iter_frames(self) -> Iterator[CaptureFrame]:
        """索引順に全フレームを返す（要件 6.2: 同一インスタンスを何度反復しても同一系列）。

        `self` に反復用の可変状態を一切持たせず、`read(i)` を
        `i = 0, ..., len(self)-1` の順に呼ぶだけの薄い generator である。
        """
        for i in range(len(self._entries)):
            yield self.read(i)

    # -- 内部実装: 構築時の検証 ------------------------------------------------

    def _build_profile(self, manifest: Mapping[str, object]) -> StreamProfile:
        profile_meta = manifest.get("profile")
        if not isinstance(profile_meta, Mapping):
            raise RecordingFormatError(
                f"manifest.json に profile が無い、または形式が不正: {profile_meta!r}"
            )
        intrinsics_meta = manifest.get("intrinsics")
        intrinsics: CameraIntrinsics | None = None
        if intrinsics_meta is not None:
            if not isinstance(intrinsics_meta, Mapping):
                raise RecordingFormatError(
                    f"manifest.json の intrinsics の形式が不正: {intrinsics_meta!r}"
                )
            try:
                intrinsics = CameraIntrinsics(
                    width_px=profile_meta["width_px"],
                    height_px=profile_meta["height_px"],
                    fx_px=intrinsics_meta["fx_px"],
                    fy_px=intrinsics_meta["fy_px"],
                    ppx_px=intrinsics_meta["ppx_px"],
                    ppy_px=intrinsics_meta["ppy_px"],
                    model=intrinsics_meta["model"],
                    coeffs=tuple(intrinsics_meta["coeffs"]),
                )
            except KeyError as exc:
                raise RecordingFormatError(
                    f"manifest.json の intrinsics に必須キーが無い: {exc}"
                ) from exc
        try:
            return StreamProfile(
                width_px=profile_meta["width_px"],
                height_px=profile_meta["height_px"],
                fps=profile_meta["fps"],
                depth_scale_mm=profile_meta["depth_scale_mm"],
                color_enabled=profile_meta["color_enabled"],
                intrinsics=intrinsics,
            )
        except KeyError as exc:
            raise RecordingFormatError(
                f"manifest.json の profile に必須キーが無い: {exc}"
            ) from exc

    def _read_summary(self) -> dict[str, object] | None:
        summary_path = self._session_dir / layout.SUMMARY_NAME
        if not summary_path.exists():
            warnings.warn(
                f"セッション {self._session_dir} に summary.json が無い"
                "（書きかけ、正常に閉じられなかった記録）。"
                "読める範囲までのフレームのみを読み出す。",
                InProgressSessionWarning,
                stacklevel=3,
            )
            return None
        with summary_path.open(encoding="utf-8") as f:
            return json.load(f)

    def _read_index(self) -> list[_IndexEntry]:
        index_path = self._session_dir / layout.INDEX_NAME
        entries: list[_IndexEntry] = []
        if not index_path.exists():
            return entries

        with index_path.open(encoding="utf-8") as f:
            for line_no, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RecordingFormatError(
                        f"索引行の JSON が壊れている: {index_path} の {line_no} 行目: {exc}"
                    ) from exc
                if not isinstance(record, Mapping):
                    raise RecordingFormatError(
                        f"索引行が JSON オブジェクトでない: {index_path} の {line_no} 行目"
                    )
                missing = [key for key in _REQUIRED_INDEX_KEYS if key not in record]
                if missing:
                    raise RecordingFormatError(
                        f"索引行に必須キーが無い: {index_path} の {line_no} 行目: "
                        f"欠落キー={missing}"
                    )
                entries.append(
                    _IndexEntry(
                        i=record["i"],
                        seq=record["seq"],
                        t_capture_ms=record["t_capture_ms"],
                        device_ts_ms=record["device_ts_ms"],
                        ts_domain=record["ts_domain"],
                        capture_latency_ms=record["capture_latency_ms"],
                        off=record["off"],
                        len=record["len"],
                        raw_len=record["raw_len"],
                        dropped_before=record["dropped_before"],
                        gap_before=record["gap_before"],
                    )
                )
        return entries

    def _validate_entries(self, entries: list[_IndexEntry]) -> None:
        """索引の各行が指すブロブ範囲が実体（`depth.bin` の実サイズ）を超えないことを検証する。

        「オフセット超過」（design.md「索引行の JSON 破損・オフセット超過・
        長さ不一致 → RecordingFormatError」）に対応する。展開後の長さが
        `raw_len` と一致するかどうかの検証は、実際にバイト列を読んで
        展開しなければ行えないため、ここでは行わず `read()` の実行時に行う。
        """
        for entry in entries:
            if entry.off < 0 or entry.len < 0 or entry.off + entry.len > self._blob_size:
                raise RecordingFormatError(
                    "索引が指すブロブの位置または長さが depth.bin の実体と合わない: "
                    f"i={entry.i}, off={entry.off}, len={entry.len}, "
                    f"depth.bin のサイズ={self._blob_size}"
                )

    # -- 内部実装: 読み出し時の復元 ---------------------------------------------

    def _reconstruct_depth(self, entry: _IndexEntry) -> np.ndarray:
        payload = self._read_blob_chunk(entry.off, entry.len)
        decompressed = self._decompress(payload, entry.raw_len)
        return np.frombuffer(decompressed, dtype=np.uint16).reshape(
            self._profile.height_px, self._profile.width_px
        )

    def _read_blob_chunk(self, offset: int, length: int) -> bytes:
        """`depth.bin` を毎回 `open()` し直し、`seek()`/`read()` で1チャンクを読む。

        ファイルハンドルやカーソルを `self` に持たせない
        （design.md Invariants: `read(i)` が内部状態に依存しないため）。
        """
        with self._blob_path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(length)
        if len(chunk) != length:
            raise RecordingFormatError(
                "depth.bin から索引の指す長さぶんを読み出せなかった: "
                f"off={offset}, len={length}, 実際に読めた長さ={len(chunk)}"
            )
        return chunk

    def _decompress(self, payload: bytes, raw_len: int) -> bytes:
        if self._compression == "zlib":
            try:
                decompressed = zlib.decompress(payload)
            except zlib.error as exc:
                raise RecordingFormatError(f"zlib の展開に失敗した: {exc}") from exc
        elif self._compression == "none":
            decompressed = payload
        else:
            raise RecordingFormatError(
                f"未知の圧縮方式のため展開できない: compression={self._compression!r}"
            )

        if len(decompressed) != raw_len:
            raise RecordingFormatError(
                "展開後の長さが索引の raw_len と一致しない"
                "（部分的な読み出しを成功として返さない）: "
                f"展開後の長さ={len(decompressed)}, raw_len={raw_len}"
            )
        return decompressed
