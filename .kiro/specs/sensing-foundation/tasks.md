# Implementation Plan

> 本計画は `requirements.md`（要件1〜12 / 受入基準93件）と `design.md` に基づく。
>
> **タスク1〜8 は実機を必要としない。** live アダプタ（6.1）は SDK が無い環境でも
> import・型検査・モック検証まで到達でき、実際の疎通確認はタスク9で行う。
> **タスク9 はハードウェア必須**であり、Pi 4 と D435 が手元に揃うまで着手できない。
> この分割は「実機待ちの期間に何も進まない」事態を避けるための意図的なものである。
>
> **再検証の引き金（下流 Spec を巻き込む変更）**: 実装中に以下へ触れる必要が生じた場合は、
> 単独で決めずに `design.md` の Revalidation Triggers に従って下流 Spec の再確認を要求する。
> ① `__all__` からのシンボル削除・改名（タスク 8.2。下流3 Spec の境界テストの起点）、
> ② 逆投影の基本演算の規約変更（タスク 1.8。`world-frame-calibration` と `flying-object-tracking` の結果が同時に動く）、
> ③ フレーム層の実効サンプル数の定義変更（タスク 7.2。`flying-object-tracking` の点層の指標と対になる）、
> ④ 記録形式版・ログ形式版の変更。

## 1. 基盤: パッケージ骨組みと共有型

- [ ] 1. 基盤: パッケージ骨組みと共有型

- [x] 1.1 パッケージ骨組みとテスト実行基盤を用意する
  - `src/sensing_foundation/` と `tests/sensing_foundation/` を作成し、`src/` レイアウトの既存慣行に合わせる
  - ルートの `pyproject.toml` に**追記のみ**を行う: wheel の `packages` に `src/sensing_foundation` を足し、`[project.optional-dependencies]` に `sensing = ["numpy>=1.24"]` を新設する
  - **`[project].dependencies` は空のまま変更しない**（`prediction_core` の実行時依存ゼロを壊さないため）
  - **`tests/prediction_core/test_packaging.py` の extras 不変条件を是正する（本 Spec で認可された唯一の例外）**:
    現行の `test_no_third_party_runtime_dependencies` は `[project].dependencies == []` に加えて
    **`[project.optional-dependencies] == {}` も表明**しており、上の `sensing` extras を足した時点で赤くなる。
    後者を **`ALLOWED_OPTIONAL_EXTRAS = {"sensing", "tracking", "calibration", "m1-viz"}` との包含関係
    （`set(optional-dependencies) <= ALLOWED_OPTIONAL_EXTRAS`）**へ差し替え、前者（実行時依存ゼロ）はそのまま残す。
    許可リストを設ける理由（後続4 Spec が同じ赤いテストへ個別に衝突するのを避けるため、
    最初に着地する Spec が不変条件の**表現だけ**を是正する）をテストの docstring に書く
  - **この改訂は「`prediction_core` のツリーに触れない」原則に対する唯一の認可された例外であり、境界違反として扱わない。**
    変更してよいのは当該テストのこの1関数と新設の定数のみで、**`src/prediction_core/**` と他のテストには一切触れない**
  - `.gitignore` に記録・ログ・ベンチ結果の出力先（`var/`）を追加する
  - `tests/sensing_foundation/conftest.py` に一時ディレクトリと設定の共通フィクスチャの器を置く
  - 観測可能な完了状態: `import sensing_foundation` が成功し、`python -m pytest` が既存の `prediction_core` テスト（改訂した `test_packaging.py` を含む）とあわせて成功終了する
  - _Requirements: 12.5, 12.6_
  - _Boundary: pyproject.toml, .gitignore, tests/prediction_core/test_packaging.py（唯一の例外）_

- [x] 1.2 (P) 例外階層を定義する
  - 基底例外と、設定不正・入力元利用不能・デバイス未準備・アダプタ契約違反・記録形式不整合・形式版不一致・記録書き込み失敗を定義する
  - **「観測された事実（破棄・欠落・USB2・欠測・書き込み失敗）は例外にしない」**という区分を docstring で明文化する
  - 入力元利用不能の例外は「次に何を確認すべきか」を必ずメッセージに含める形にする
  - 観測可能な完了状態: 全例外が基底例外として捕捉でき、設定不正が `ValueError` としても捕捉できることをテストで固定する
  - _Requirements: 2.7, 5.8, 6.6_
  - _Boundary: errors_

- [x] 1.3 (P) セッション単調時計を実装する
  - セッション開始時に単調時計と壁時計を**1度だけ**対応付け、以降の時刻を開始からの経過 ms として返す
  - 事後解析のために経過 ms から壁時計へ換算する手段を用意する
  - セッション識別子を保持し、記録ディレクトリ名・ログファイル名・Throw Record の対応付けで共通に使えるようにする
  - 観測可能な完了状態: 連続呼び出しが単調非減少であり、壁時計アンカが複数回取得されないことをテストで固定する
  - _Requirements: 3.4, 8.10_
  - _Boundary: SessionClock_

- [x] 1.4 (P) リソース計測を標準ライブラリのみで実装する
  - `/proc` からシステム CPU・プロセス CPU・RSS・搭載 RAM・利用可能 RAM を読む
  - CPU 使用率は前回サンプルとの差分で算出し、初回は欠測として返す
  - `/proc` が読めない環境では例外を投げず、すべて欠測として返す
  - 搭載 RAM 総量はリングバッファ上限の根拠として使えるよう単独で取得できるようにする
  - 観測可能な完了状態: `/proc` が無い状況を模したテストで例外が出ず、すべて欠測が返る
  - _Requirements: 9.3_
  - _Boundary: Sysstat_

- [x] 1.5 入力元に依存しないフレーム表現を定義する
  - 時刻ドメイン・カメラ内部パラメータ・ストリーム設定・フレーム・取得統計を不変（frozen かつ slots）で定義する
  - Depth は 2 バイト符号なし整数の2次元配列として保持し、**読み取り専用**にして下流の破壊的変更を防ぐ
  - 距離 mm・時刻 ms・画素量 px をフィールド名に含める
  - 入力元の種別は **`prediction_core` の種別をそのまま再エクスポート**し、同義の列挙型を定義しない
  - **本モジュールは `prediction_core` を import してよい2モジュールのうちの1つである**（もう1つは Throw Record 保存）。
    参照は**種別の再エクスポートのみ**に限り、スキーマ関連シンボルには触れない（スキーマの参照点は Throw Record 保存に1本化する）
  - デバイス側時刻とそのドメイン、およびグローバル時刻ドメインのときだけ意味を持つ取得レイテンシを、欠測を表現できる形で持たせる
  - 座標変換・床平面推定に相当するフィールドや処理を**持たせない**旨を docstring に明記する
  - 観測可能な完了状態: Depth 配列への書き込みが拒否されること、種別が `prediction_core` と同一オブジェクトであることをテストで固定する
  - _Requirements: 2.4, 3.1, 3.2, 3.3, 3.5, 3.7, 4.3_
  - _Depends: 1.2_
  - _Boundary: CoreTypes_

- [x] 1.6 実行時設定の解決と起動時検証を実装する
  - 取得設定（解像度・fps・Color 有無・キュー容量・ドレイン可否・取得タイムアウト・取得失敗時の挙動）、記録設定、ログ設定、入力元と再生対象を定義する
  - 解決順序を **CLI 引数 > 環境変数 > 設定ファイル > 既定値**とし、解決結果を不変にする
  - Color を既定で無効にする（改善順序の1番目を既定で満たす）
  - 解像度・fps の既定値には**「初期評価候補であり必須性能ではない」**旨を docstring に明記する
  - リングバッファの必要 RAM を設定値から算出し、上限（既定は搭載 RAM の一定割合）を超える設定を**起動時に拒否**する
  - 再生を指定してセッション未指定の場合、fps が 0 以下の場合なども起動時に拒否する
  - 観測可能な完了状態: 解決順序が上位から順に勝つこと、必要 RAM 超過・不正 fps・再生時のセッション未指定がいずれも起動時に拒否されることをテストで固定する
  - _Requirements: 11.1, 11.7, 12.4_
  - _Depends: 1.5_
  - _Boundary: RuntimeSettings_

- [x] 1.7 決定的な合成フレーム生成ヘルパをテストツリーに用意する
  - 指定した枚数・解像度・フレーム番号列から、内容が一意に決まる Depth 配列を生成するヘルパを `tests/sensing_foundation/synthetic.py` に置く
  - フレーム番号を意図的に飛ばす供給、遅い供給、契約違反（shape / dtype 不一致）の供給を作れるようにする
  - **パッケージ本体には置かない**（投擲物理・ノイズ生成は `trajectory-simulator` の責務であり本 Spec の責務外）
  - 以降のタスク 3.2 / 4.6 / 7.2 / 7.3 / 8.1 の完了条件はこのヘルパを前提とする
  - 観測可能な完了状態: 同じ引数で2回生成した系列が完全に一致する
  - _Requirements: 4.5_
  - _Depends: 1.5_
  - _Boundary: tests/sensing_foundation/synthetic.py_

- [x] 1.8 (P) ピンホール逆投影の基本演算を実装する
  - `geometry.py` に**状態を持たない純関数**として、無効 Depth の判定・生カウントから mm への換算・
    画素からカメラ座標系への逆投影の3つと、無効値を表す定数を置く
  - **画素中心の規約を明文化する**: 整数座標はその画素の中心を指し、**`+0.5` の補正を加えない**
    （RealSense SDK の逆投影と同じ規約）。重心などの小数座標をそのまま渡せるよう画素座標は浮動小数で受ける
  - **mm 換算を行う場所をこの関数1つに限定する**。逆投影関数は**換算済みの奥行き（mm）**を受け取り、
    呼び出し側が自分で `depth_scale_mm` を掛けない（二重適用を防ぐため引数名に単位を含める）
  - **無効 Depth（測距不能）を逆投影すると原点というもっともらしい嘘の点が出る**ため、
    逆投影の前に述語で弾く運用を docstring に明記する
  - 歪み係数は適用しない（現状 Depth ストリームでは恒等）。適用が必要になった場合は本モジュールへ追加する旨を残す
  - World frame への変換・床平面推定は**置かない**。返すのはカメラ座標系までである旨を docstring に明記する
  - **`world-frame-calibration` と `flying-object-tracking` はこの3関数を呼び、同等の式を自前で書かない**旨を
    モジュール docstring に明記する（両 Spec が同じ演算に乗っていることが、片方の校正結果をもう片方へ適用してよい根拠になる）
  - 観測可能な完了状態: 主点画素の逆投影が `x=y=0` になること（画素中心規約に `+0.5` を足していないことの検出）、
    無効値が無効と判定されること、既知の内部パラメータでの逆投影値、内部パラメータの焦点距離 0 が拒否されることをテストで固定する
  - _Requirements: 3.6, 3.8_
  - _Depends: 1.2, 1.5_
  - _Boundary: Geometry, tests/sensing_foundation/test_geometry.py_

## 2. 観測基盤: 構造化ロギングと計測点

- [ ] 2. 観測基盤: 構造化ロギングと計測点

- [x] 2.1 構造化ロギングを実装する
  - 1行1イベントの追記形式（NDJSON）で、固定キー（セッション経過時刻・セッション識別子・段階名・イベント名）と任意の付随値を書き出す
  - 送出は呼び出し側スレッドで**キューへ積むだけ**とし、書き込みは専用スレッドが行う（完了を待たない）
  - キューを有界にし、満杯時は**ログを捨てて取得を優先**し、破棄件数を数える
  - 書き込み失敗時に本来の処理を中断させない
  - 行単位でフラッシュし、末尾行が欠けても先行行が読める状態を保つ
  - 実行時に無効化でき、無効時は**イベントの生成も文字列化も行わない** no-op 実装へ差し替える
  - 先頭行にセッション開始イベント（形式版・壁時計アンカ・解決済み設定）、終了時にセッション終了イベント（各種カウンタ）を書く
  - 段階名を予約分（システム・取得・記録）と自由分に分け、**下流 Spec が自分の段階を足せる**入口を用意する
  - 数値の非有限値はその項目を欠測として落とし、直列化を破綻させない
  - 観測可能な完了状態: 有界キューを小さくした状態で大量送出しても呼び出し側がブロックせず、破棄件数が増え、生成されたファイルが1行ずつ読み取れる
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.9, 8.10_
  - _Depends: 1.3, 1.6_
  - _Boundary: StructuredLogger_

- [x] 2.2 capture 区間の計測点を実装する
  - 待機時間・ドレイン時間・受け渡し時間・合計を1フレームごとに算出し、ログへ送る
  - 破棄件数・欠落件数・取得失敗件数・実測フレームレートをカウンタとして保持し、取得統計として取り出せるようにする
  - CPU・メモリは毎フレームではなく**一定間隔**でサンプルし、間隔を設定できるようにする
  - デバイス側時刻から算出する取得レイテンシは、ドメインが該当する場合にのみ送出し、**それ以外はキーごと省く**（欠測と 0 を区別する）
  - 区間の定義をこのコンポーネント1箇所に集約し、下流が同じ手段で自分の区間を足せる形にする
  - **目標値の充足判定を行わない**（計測値の提供にとどめる）
  - 観測可能な完了状態: 合成フレームを流したとき、区間ごとの計測値と5種のカウンタがログと統計の双方から取得できる
  - _Requirements: 2.2, 2.3, 9.1, 9.2, 9.3, 9.5_
  - _Depends: 1.4, 2.1_
  - _Boundary: CaptureMetrics_

## 3. 取得層: ポートと共通挙動

- [ ] 3. 取得層: ポートと共通挙動

- [x] 3.1 取得の契約と共通挙動を実装する
  - 入力元の契約をイテレータ形式のプロトコルとして定義し、種別・ストリーム設定・統計・開始・停止・文脈管理を含める
  - 基底実装に、ドレイン（待機で1枚受け取った直後に滞留分を捨てて最新へ追いつく）・欠落検出・統計更新・計測点送出を一元化する
  - アダプタが実装するのは「1枚取る」「滞留分から最新を取る」の2操作のみとする
  - 破棄（自分の処理落ち）と欠落（フレーム番号の飛び）を**別のカウンタ**で数える
  - 取得失敗時に継続するか停止するかを設定で制御し、継続時は失敗件数を数える
  - 入力元固有の設定はコンストラクタで受け、契約の引数には現れないようにする
  - 停止後に取得総数・破棄・欠落・実測フレームレートを含む要約を返す
  - 観測可能な完了状態: 遅い下流を模した合成入力に対し、ドレイン有効時のみ破棄件数が増え、通し番号が欠番なく増えることをテストで固定する
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.7, 2.8, 4.1, 4.6_
  - _Depends: 2.2_
  - _Boundary: FrameSource, BaseFrameSource_

- [x] 3.2 合成入力アダプタを実装する
  - 外部から渡された供給関数からフレームを取り出し、共通表現へ変換する
  - 供給関数が終端を返したらイテレーションを終える
  - 供給された配列の形状・型がストリーム設定と食い違う場合は契約違反として拒否する
  - **ドレインを行わない**（再生・合成では取りこぼしを新たに作らない）
  - **投擲物理・ノイズ生成を持たない**（差し替え口の提供にとどめる）
  - 観測可能な完了状態: 実機も SDK も無い環境で合成入力から共通表現の系列が取得でき、供給枚数と取得枚数が一致する
  - _Requirements: 4.1, 4.4, 4.5_
  - _Depends: 1.7, 3.1_
  - _Boundary: SimulatedSource_

## 4. 記録と再生

- [ ] 4. 記録と再生

- [x] 4.1 セッション記録のレイアウトと形式版を定義する
  - セッションディレクトリの規約（メタ情報・フレーム索引・生データブロブ・終了サマリの4ファイル）と各ファイル名を1箇所に固定する
  - 形式版の定数を定義し、セッション識別子の生成規則（時刻＋ランダム接尾辞）を決める
  - メタ情報のスキーマ（形式版・識別子・入力元種別・壁時計アンカ・ストリーム設定・内部パラメータ・デバイス情報・実行環境・取得設定・ブロブ情報）を定義する
  - 出力先の既定を版管理対象外の位置に置く
  - **観測フレーム記録は Throw Record とは別の記録である**旨を docstring に明記する
  - 観測可能な完了状態: メタ情報を書いて読み戻すと同一の辞書が得られ、識別子が連続生成で衝突しない
  - _Requirements: 5.2, 5.9, 12.6_
  - _Depends: 1.5_
  - _Boundary: RecordingLayout_

- [x] 4.2 (P) フレームのリングバッファを実装する
  - 直近 N 秒相当の枚数だけを保持し、古いものから捨てる
  - 秒数とストリーム設定から必要バイト数を算出する手段を提供する
  - 保持時にフレームを複製しない（不変かつ読み取り専用であることを利用する）
  - 記録器へ一括で書き出す操作と、書き出した枚数の返却を提供する
  - 観測可能な完了状態: 秒数×fps を超えて保持されないこと、必要バイト数の算出が解像度・fps・秒数に比例することをテストで固定する
  - _Requirements: 5.5, 5.7_
  - _Depends: 1.5, 1.6_
  - _Boundary: FrameRingBuffer_

- [x] 4.3 セッション記録の書き出しを実装する
  - メタ情報・フレーム索引・生データブロブ・終了サマリの4ファイルを書く
  - 索引行に通し番号・入力元のフレーム番号・セッション経過時刻・デバイス側時刻とドメイン・ブロブ内の位置と長さ・**直前の破棄件数と欠落件数**を含める
  - **索引行はブロブ書き込みの後に書く**（途中で落ちても索引が実体を超えない）
  - 任意で標準ライブラリの圧縮を選べるようにし、圧縮時は展開後の長さも索引に残す
  - 書き込み失敗は件数を数えてログへ送り、**例外を上へ投げずに取得を継続させる**。連続失敗が上限に達したときだけ記録を停止する
  - 記録の開始・終了を実行時に制御でき、終了時に終了サマリを書く
  - 記録が有効な場合の追加負荷を後から比較できるよう、記録経路にも計測点を置く
  - 観測可能な完了状態: 記録を実行するとセッションディレクトリに4ファイルが生成され、索引行数とブロブ内のフレーム数が一致する。書き込み先を書き込み不可にしても取得が止まらず失敗件数が増える
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.7, 5.8_
  - _Depends: 4.1_
  - _Boundary: SessionRecorder_

- [x] 4.4 (P) セッション記録の読み出しを実装する
  - メタ情報からストリーム設定と内部パラメータを復元し、索引とブロブから任意の位置のフレームを再構成する
  - 反復読み出しが内部状態に依存せず、**同一インスタンスを複数回反復しても同一系列**になるようにする
  - 形式版が未知の場合、索引行が壊れている場合、ブロブの位置や長さが実体と合わない場合は、それぞれ専用の例外で通知し**部分結果を成功として返さない**
  - 終了サマリが無い（書きかけの）セッションは、読める範囲まで読めることを警告付きで報告する
  - テスト用の記録は**手書きの最小フィクスチャ**で用意し、書き出し側（4.3）の完成を待たずに検証できるようにする
  - 観測可能な完了状態: 索引欠損・位置改竄・未知の形式版の3ケースでそれぞれ期待した例外が送出され、正常なセッションでは2回の反復が完全一致する
  - _Requirements: 6.2, 6.6_
  - _Depends: 4.1_
  - _Boundary: SessionReader_

- [x] 4.5 記録の再生アダプタを実装する
  - 読み出し器を包み、live と同じ契約でフレーム系列を返す
  - ストリーム設定と内部パラメータを**live と同じ経路**で取得できるようにする
  - 実時間に沿った再生と最速再生を選べるようにし、**どちらでも返る系列は同一**であることを保証する（既定は最速）
  - ドレインを継承せず、再生側の都合で取りこぼしを新たに発生させない
  - RealSense の SDK もハードウェアも参照しない
  - 観測可能な完了状態: SDK 非導入環境で記録を再生でき、実時間再生と最速再生の返す系列が完全一致する
  - _Requirements: 4.1, 4.4, 6.1, 6.3, 6.4, 6.5, 6.7_
  - _Depends: 3.1, 4.4_
  - _Boundary: RecordedSource_

- [x] 4.6 入力元の生成口を用意し、アダプタ共通の契約テストを通す
  - 設定から適切なアダプタを構築する唯一の生成口を実装する（呼び出し側がアダプタを直接構築しない）
  - **合成入力と再生入力に同一のテストを適用する**契約テストを作り、返る系列が等価になることを確認する
  - 下流のフレーム処理コードを変更せずに入力元を差し替えられることを、同一の消費側関数を使って示す
  - 実機アダプタは同テストの対象に含める形にしておき、実行はタスク 9.3 で行う
  - 観測可能な完了状態: 合成入力で記録 → 再生した系列が、元の合成系列と時刻・番号・Depth 内容まで一致する
  - _Requirements: 4.2_
  - _Depends: 3.2, 4.3, 4.5_
  - _Boundary: open_source, tests/sensing_foundation/test_source_contract.py_

- [x] 4.7 「フレーム番号」が指す量を確定させ、記録・再生・対応付けの間で一致させる
  - 同じ「フレーム番号」に見える**3つの別々の量**があり、リングが古いフレームを
    追い出したときだけ食い違う:
    (a) `SessionReader.read(i)` の `i` は**索引ファイルの行位置**、
    (b) 索引行の `i` フィールドは**記録**セッションの通し番号、
    (c) `RecordedSource` が返す `CaptureFrame.index` は**再生**セッションの 0 始まり通し番号
  - タスク9.6 の実機記録（181枚取得し直近60枚を保存）で、**行位置 0 の `i` が 121** であることを実測した
  - `types.py` は `CaptureFrame.index` を「セッション内の 0 始まり通し番号。欠番なく増加する」と
    定義しているが、この不変条件を満たすのは `RecordedSource` の側であり、
    `SessionReader.read()` の戻り値（121 始まり）は満たさない
  - `throw_store.link_to_session()` の `frame_index_from` / `frame_index_to` が
    上記のどれを指すかは、design.md も requirements.md も定めていない。
    下流が「記録側の通し番号」のつもりで値を入れ、利用側が行位置として渡すと
    **静かにずれた範囲を読む**（例外にならない）
  - **投擲の瞬間だけを残すリング運用（要件 5.5）はまさに追い出しが起きる使い方**であり、
    この曖昧さが表面化する経路そのものである
  - **まず design.md に `frame_index_*` がどの量を指すかを明記してから**、
    `SessionReader` / `RecordedSource` / `link_to_session` / `types.CaptureFrame` の
    記述と実装をその定義に合わせる（実装を先に動かして定義を後付けしない）
  - 観測可能な完了状態: **追い出しが起きた記録を明示的に作り**、`link_to_session()` が
    残した範囲から元のフレームを取り直せることをテストで固定する
    （追い出しの無い記録では3つの量が一致してしまい、検証が空振りになる）。
    あわせて `tests/sensing_foundation/test_real_session_roundtrip.py` に置いた
    前提の表明（`index_rows[0]["i"] == 0` と、それが未決である旨のコメント）を
    確定した定義に沿って更新する
  - _Requirements: 5.5, 6.1, 7.7_
  - _Depends: 4.4, 4.5, 5_
  - _Boundary: SessionReader, RecordedSource, ThrowRecordStore.link_to_session, types.CaptureFrame_
  - _Note: 欠陥は3つの境界の**間**の不整合そのものであり、どれか1つの中では契約を定義できない。したがって本タスクの境界は意図的に複数へまたがる。経緯は `measurements.md` タスク9.6「発見1」を参照。_

## 5. Throw Record の保存層

- [x] 5. Throw Record の保存層を実装する
  - `prediction_core` の**公開入口が提供するシンボルのみ**を参照し、スキーマを再定義しない
  - 直列化・復元は `prediction_core` が提供する手段で行い、1行1レコードの追記形式で保存する
  - 追記した内容を1件ずつ読み戻せるようにし、往復で等価なレコードが得られることを保証する
  - 破損した行は行番号と種別を伴う報告値として返し、**後続の健全な行の読み出しを続ける**経路を用意する
  - スキーマ版が現在扱える版と異なる場合は報告のみを行い、**内容を推測して読み替えない**
  - 観測フレーム記録との対応付けを、スキーマが用意している加算的拡張の退避先へ**1つの名前空間キー**として収める（セッション識別子とフレーム範囲）
  - `prediction_core` の内部モジュールを import しない
  - 観測可能な完了状態: 複数レコードを追記して読み戻すと同数・等価で得られ、途中に壊れた行と版違いの行を混ぜても他の行が読め、それぞれ別種の報告として区別できる
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_
  - _Depends: 1.2, 4.1_
  - _Boundary: ThrowRecordStore_

## 6. 実機アダプタと環境診断

- [ ] 6. 実機アダプタと環境診断

- [x] 6.1 RealSense の live アダプタを実装する
  - SDK を**関数内で遅延 import** し、未導入時は「次に何を確認すべきか」を含む専用の例外にする
  - Depth ストリームを設定から構成し、Color は設定で有効化されたときだけ有効にする
  - フレームキュー容量を小さく保ち、待機で1枚受け取った直後に滞留分を捨てて最新へ追いつく
  - フレーム番号から欠落を検出し、デバイス側時刻とその時刻ドメインを記録する
  - グローバル時刻の有効化を試み、**有効化できたかどうか**をメタ情報とログに残す
  - **Point Cloud を生成しない。** 生バッファを必要最小限の1回だけ複製し、読み取り専用にする
  - USB 接続種別を取得し、USB2 の場合は警告として明示する
  - 要求した解像度・fps が拒否された場合は起動時に失敗させ、黙って別のモードで動かさない
  - **SDK への問い合わせ関数（SDK 版・実体の場所・デバイス列挙・USB 接続種別）を本モジュールが公開**し、診断側がそれを経由できるようにする。`pyrealsense2` を import するモジュールを本モジュール1つに限定する
  - GUI 表示を必要としない（headless で完結する）
  - 観測可能な完了状態: SDK 非導入環境では専用の例外が出て他の入力元は動作し、SDK を模したモックに対しては共通表現の系列・欠落検出・USB2 警告が期待どおり得られる
  - _Requirements: 1.4, 1.5, 2.5, 2.6, 3.5, 3.6, 11.7, 12.1_
  - _Depends: 3.1_
  - _Boundary: RealSenseSource_

- [x] 6.2 環境診断を実装する
  - OS・カーネル・64bit 判定、Python の版と実行パス、搭載 RAM と利用可能量、SDK の import 可否と版と実体の場所、デバイスの列挙とシリアル・ファームウェア、USB 接続種別、要求モードの開閉、短時間取得での安定性、記録先の空き容量を確認する
  - SDK 関連の項目は 6.1 が公開する問い合わせ関数を経由し、**診断モジュール自身は SDK を import しない**
  - **各項目は独立して失敗できる**ようにし、1つ落ちても後続をスキップせず全項目の結果を返す
  - 安定性の項目は「問題なし」ではなく**「この条件では観測されなかった」**という表現で報告する
  - 結果を機械可読な形式で出力し、実施結果として保存できるようにする
  - 取得本体とは独立に実行できるようにする
  - 観測可能な完了状態: SDK 非導入の環境で実行すると SDK 項目のみが失敗し、OS・Python・メモリの各項目は正常として全項目が返る
  - _Requirements: 1.3, 1.4, 1.5, 1.10, 12.7_
  - _Depends: 1.4, 6.1_
  - _Boundary: Doctor_

- [ ] 6.3 開いたデバイスの識別情報を live アダプタから取り出せるようにする
  - `LiveSource` は `start()` の中でデバイスオブジェクトを握っているが、
    `global_time_enabled` / `usb2_warning` と違い**識別情報を外へ公開していない**。
    そのため記録側が「どの個体・どのファームウェアで撮ったか」を知る経路が無い
  - `start()` 完了後に確定する属性として、実際に開いたデバイスの
    `name` / `serial_number` / `firmware_version` / `usb_type_descriptor` を公開する
    （`global_time_enabled` と同じ流儀のプロパティ）
  - **`probe_devices()` の流用で代替しない。** あれは接続中の全デバイスを列挙するもので、
    複数台つながっているときに**パイプラインが実際に開いた個体**を特定できない
  - 取得できない項目は `None`（欠測）とし、偽装しない（要件 3.5 の方針をそのまま適用）
  - 観測可能な完了状態: SDK を模したモックに対し、`start()` 後に識別情報が読め、
    `get_info` が例外を送出する項目は `None` になることをテストで固定する
  - _Requirements: 1.4, 5.2_
  - _Depends: 6.1_
  - _Boundary: RealSenseSource_

## 7. 集計と比較の手段

- [ ] 7. 集計と比較の手段

- [x] 7.1 (P) ログと記録の集計を実装する
  - 構造化ログを行単位でストリーム処理し、段階×イベントごとの件数・代表値・分位点・欠測数を集計する
  - **未知の段階名も集計できる**ようにする（下流 Spec が足した段階を、集計側の改修なしに読める）
  - 区間ごとに分解した集計結果を出力する
  - 実機以外の環境で実行できるようにする（集計を取得経路へ持ち込まない）
  - 観測可能な完了状態: 合成ログに未知の段階を混ぜても集計が成立し、段階ごとの分位点が算出される
  - 公開入口へ `summarize_log` / `LogSummary` として出す（`m1-prediction-validation` が委譲先にする単一の集計器）
  - _Requirements: 8.8, 9.4, 9.7, 12.3_
  - _Depends: 2.1_
  - _Boundary: src/sensing_foundation/logsummary.py, tests/sensing_foundation/test_logsummary.py_

- [x] 7.2 (P) 解像度・フレームレートの掃引を実装する
  - 候補設定を同一条件・同一時間で順に実行し、実測フレームレート・破棄件数・欠落件数・取得レイテンシの代表値と分位点・CPU・メモリを記録する
  - **評価軸を一定時間窓あたりの有効フレーム数**とし、フレームレート単体で比較しない。窓長は設定可能とし固定値を埋め込まない
  - 指標名は**フレーム層の実効サンプル数**とし、`flying-object-tracking` が定義する**点層の実効点数**
    （`effective_points_per_window`）と**別物である**ことを docstring と結果 JSON のキー説明に明記する。
    両者は対になる指標であり、**片方だけ定義を変えると比較が成立しない**（定義変更は再検証の引き金に当たる）
  - 既定候補に 640×480 の 30 fps と 60 fps を含め、Color 有無も切り替えられるようにする
  - ウォームアップ区間を必ず設け、モード順を入れ替えた再実行ができるようにする
  - USB2 接続やモード開閉失敗が検出された回の結果は**無効として記録する**
  - **目標値の充足判定を行わず、Pi 4 を継続するかの判断も行わない**（材料の提供にとどめる）
  - 結果を機械可読な形式で保存し、失敗したモードがあっても他のモードの結果を捨てない
  - 観測可能な完了状態: 合成入力で複数モードを掃引すると、全モード分の結果（有効フレーム数を含む）が1つのファイルに揃う
  - _Requirements: 11.2, 11.3, 11.4, 11.6, 9.6_
  - _Depends: 4.6_
  - _Boundary: ModeSweep_

- [x] 7.3 (P) 計測 ON / OFF の比較を実装する
  - ログ無効・ログ有効・記録有効の各条件を、**同一入力元・同一設定・同一時間**で交互（A/B/A/B）に実行する
  - 各条件の処理時間の代表値・分位点・四分位範囲・実測フレームレート・破棄件数を記録する
  - **判定基準を実測前に確定して文字列として出力に含める**: 有効条件と無効条件の中央値の差が無効条件の四分位範囲以内であり、かつ破棄件数が増えていないこと
  - 判定が偽の場合、**計測結果を無条件に有効なものとして扱わない**旨を結果に明記する
  - 実機を用いない入力元でも実行できるようにし、その場合は**実機の結論として扱わない**旨を出力に明記する
  - 生サンプルを残し、判定を後から再計算できるようにする
  - 観測可能な完了状態: 合成入力で実行すると、3条件の実測値と判定基準文字列と判定結果を含む結果ファイルが生成される
  - _Requirements: 5.6, 10.1, 10.2, 10.3, 10.4, 10.5_
  - _Depends: 4.6_
  - _Boundary: LoggingOverheadBench_

- [x] 7.4 計測 ON / OFF の比較を単一入力元の構造へ作り替える
  - 3条件で `FrameSource`・`SessionClock`・`CaptureMetrics` を**1つずつ共有**し、
    セグメントの切り替えでは**ロガーの向き先だけ**を差し替える
    （`logging_off` → `NullLogger`、`logging_on` → `StructuredLogger`、
    `recording_on` → `NullLogger` ＋ `SessionRecorder.write()`）
  - ロガーの差し替えは `obslog.Logger` が `Protocol`（構造的部分型）であることを利用し、
    **ベンチ側の私有クラス**で実現する。**`metrics.py` と `source.py` は変更しない**
  - 条件別の `frames_dropped` は、共有した `CaptureMetrics.counters()` を
    **セグメント境界の前後で読んだ差分**として条件ごとに積算する
  - **A/B/A/B の交互実行と、実測前に確定済みの判定基準の文字列は変更しない**
    （要件 10.3 / 方針 A-10。ここを動かすと 7.3 が固定した前提が崩れる）
  - 3条件が入力の**互いに素な区間**を処理するようになる（合成・再生でも同一内容を見なくなる）。
    これは design.md「3条件が入力元を共有する理由」が許容すると明記した副作用であり、
    A/B/A/B の交互実行と複数サイクルが打ち消す対象である
  - 観測可能な完了状態: `open_source()` の呼び出しが**1回だけ**であることをテストで固定し、
    合成入力に対する 7.3 の既存テスト（3条件の実測値・判定基準文字列・判定結果）が引き続き通過する
  - _Requirements: 10.1, 10.5_
  - _Depends: 7.3_
  - _Boundary: LoggingOverheadBench_

## 8. 入口と境界の確定

- [ ] 8. 入口と境界の確定

- [x] 8.1 コマンドライン入口を実装する
  - 診断・取得・記録・再生・モード掃引・計測比較・集計の各サブコマンドを
    `doctor` / `capture` / `record` / **`replay-session`** / `bench-modes` / `bench-logging` / `summarize` として提供する
  - **再生のサブコマンド名は `replay` ではなく `replay-session` とする。**
    `prediction_core.replay(record)` は Throw Record（サンプル層）から予測を再実行する別操作であり、
    本 Spec の再生はセッション記録（フレーム層）を流し直す操作である。層が違うものへ同じ語を使うと、
    計測メモや `docs/` 側の記述がどちらを指すか判別できなくなる。ヘルプ文にもこの区別を書く
  - 設定を **CLI 引数 > 環境変数 > 設定ファイル > 既定値**の順で解決し、解決結果を表示できるようにする
  - 再生指定でセッション未指定などの不整合は起動前に失敗させる
  - 解像度・fps の既定値がヘルプ上で**「初期評価候補であり必須性能ではない」**と分かる表記にする
  - 入力元は必ず文脈管理で開閉し、途中終了でも停止処理が走るようにする
  - 観測可能な完了状態: 合成入力を指定して `record` → `replay-session` → `summarize` をコマンド列で通すと、セッション記録・ログ・集計結果の3つが生成される
  - _Requirements: 12.4_
  - _Depends: 5, 6.2, 7.1, 7.2, 7.3_
  - _Boundary: CLI_

- [x] 8.2 公開 API を確定し、境界を静的に検証する
  - `__init__` を再エクスポート専用にし、**公開シンボルを散文ではなく `__all__` のリストとして明示列挙する**
    （型・幾何の基本演算・セッション時計・資源計測・設定・ロガーと計測点・取得の契約と生成口・
    記録器・読み出し器・リングバッファ・形式版・Throw Record 保存・**ログ集計**・例外）
  - **ログ集計（`summarize_log` / `LogSummary`）も `__all__` に含める。**
    `m1-prediction-validation` は「集計器を二重に持たない」方針で上流の集計器へ委譲するため、
    これが公開入口に無いと m1 が自分の境界テストを破らずに集計へ到達できない
  - **`CaptureMetrics` / `SessionClock` / `Logger`（および `get_logger`）を必ず `__all__` に含める。**
    下流3 Spec は自分の段階の計測点をロギング基盤へ足し、同一の時間基準で段階別レイテンシを集計するため、
    これらが公開入口に無いと下流が内部モジュールを直接 import することになり、依存方向の静的検証が壊れる
  - アダプタ実装クラスと基底クラスは**公開しない**（入力元の生成は生成口へ一本化する）
  - `tests/sensing_foundation/test_public_api.py` で **`set(__all__)` が期待リストと一致すること**と
    **列挙された全シンボルが実際に import できること**を固定する。このリストは下流3 Spec の境界テストの起点であり、
    **シンボルの削除・改名は再検証の引き金に当たる**（追加は当たらない）
  - 依存方向の階層表に反する import が無いことを検証する
  - **実機アダプタ以外が SDK を import しない**こと、SDK 非導入環境で `import sensing_foundation` が成功することを検証する
  - **`types` と Throw Record 保存の2モジュール以外が `prediction_core` を import しない**ことを検証する。
    `types` の参照は**種別の再エクスポートのみ**に限り、**スキーマ関連シンボルを参照するのは Throw Record 保存だけ**であること、
    および両者とも `prediction_core` の内部モジュールを import しないことを検証する
  - 種別が `prediction_core` と**同一オブジェクト**であること（別オブジェクト化の回帰）を境界テストでも固定する
  - `prediction_core` が本パッケージを import しないこと、`pyproject.toml` の実行時依存が空のままであること、
    および **extras が許可リストに収まっていること**（タスク 1.1 で改訂した `tests/prediction_core/test_packaging.py`）を検証する
  - 観測可能な完了状態: 境界回帰テストと公開 API テストが全項目通過し、SDK も実機も無い環境で `python -m pytest` が全通過する
  - _Requirements: 4.3, 4.4, 12.2, 12.5, 12.8_
  - _Depends: 8.1_
  - _Boundary: PublicApi, tests/sensing_foundation/test_boundaries.py, tests/sensing_foundation/test_public_api.py_

- [ ] 8.3 記録のメタ情報にデバイス識別情報とグローバル時刻の有効化結果を入れる
  - **欠陥1**: `run_record()` が `SessionRecorder(..., device=None, ...)` を固定で渡すため、
    実機で撮った記録でも `manifest.json` の `"device"` が `null` になる。
    **要件 5.2「メタ情報にデバイス識別情報を含める」が実機で未充足**である
    （タスク9.6 の実記録で確認。タスク9.2 では serial 834412071095 / FW 5.17.3.10 が
    読めているので、情報が無いのではなく渡していない）
  - **欠陥2**: `_build_runtime_info()` が `"global_time_enabled": None` を固定で返す。
    **入力元を開く前に組み立てている**ため、実際の有効化結果を入れる経路が無い。
    タスク6.1 の「有効化できたかどうかをメタ情報とログに残す」の字義を満たしていない
    （索引行の `ts_domain` から推定は可能なので影響は限定的だが、メタ情報としては欠測のまま）
  - `runtime` の組み立てを `open_source()` の**後**へ移し、live のときは
    `LiveSource.global_time_enabled` と 6.3 が公開する識別情報を読んで渡す。
    live 以外の入力元では `None`（欠測）のままとし、値を偽装しない
  - 観測可能な完了状態: SDK を模したモックを使った `record` の実行で、
    `manifest.json` の `device` が非 `null` になり `runtime.global_time_enabled` が
    真偽値になることをテストで固定する。**実機での確認は次の実機セッションで
    タスク9.5 と同時に行う**（`device` が実在の個体を指すことは実機でしか確認できない）
  - _Requirements: 5.2, 6.1_
  - _Depends: 6.3, 8.1_
  - _Boundary: src/sensing_foundation/cli.py_

## 9. 実機ブリングアップと実測（ハードウェア必須）

> **ここから先は Raspberry Pi 4 と RealSense D435 の実機が必要である。**
> 順序は `docs/development-environment.md §16` に従い、**認識・USB3・給電・SDK 導入を fps 計測より先に行う。**
> 各タスクの結論は `.kiro/specs/sensing-foundation/measurements.md` に記録する（生データは版管理しない）。

- [ ] 9. 実機ブリングアップと実測

- [x] 9.1 OS を導入し、RAM 容量と選定結果を記録する
  - 64bit かつ headless 運用可能な OS を導入する。**Raspberry Pi OS 64-bit を先に評価する**
  - 実測結果の記録先として `measurements.md` を作成し、確認項目・実施日・結果・使用したコマンドを残す章立てを用意する
  - 診断コマンドで RAM 容量・OS・カーネル・Python 版を取得して記録する
  - リングバッファ上限の既定割合が、確認した RAM 容量に対して妥当かを見直す
  - 観測可能な完了状態: `measurements.md` に OS 名・カーネル・64bit 判定・RAM 容量・Python 版が診断コマンドの出力とともに記録され、headless でログインして実行できる
  - _Requirements: 1.1, 1.3, 1.6, 1.7, 1.8_
  - _Depends: 6.2_

- [x] 9.2 RealSense の導通を確認し、SDK 導入手順を再現可能な形で記録する
  - 認識 → USB3 接続 → 給電安定性 → SDK 導入の順で確認する（**fps 計測より先に行う**）
  - SDK のビルドで USB バックエンドを強制する構成を用い、Python バインディングを生成する
  - import できない場合は「ビルド」「配置」「Python 版の取り違え」「デバイス」「接続速度」のどこで失敗しているかを診断コマンドで切り分ける
  - **SDK の導入または動作が成立しない場合は Ubuntu 24.04 LTS arm64 へ退避**し、退避したかどうかと理由を記録する
  - 実施した手順（コマンド列とビルドオプション）を、同じ手順で再現できる形で記録する
  - 観測可能な完了状態: 診断コマンドの全項目が実機で実行でき、SDK・デバイス・USB 接続種別の各項目が結果とともに `measurements.md` に記録される
  - _Requirements: 1.2, 1.3, 1.9, 12.7_
  - _Depends: 9.1_

- [x] 9.3 live アダプタを実機で通し、契約テストを再実行する
  - 実機から取得した系列に対して、合成・再生と**同じ契約テスト**を実行する
  - 欠落検出・破棄計数・時刻ドメイン・内部パラメータ取得が実機で期待どおり動くことを確認する
  - グローバル時刻が有効化できたか、取得レイテンシが算出可能かを記録する（算出できない場合は欠測として記録する）
  - 観測可能な完了状態: 実機の live 入力で契約テストが通過し、取得統計（総数・破棄・欠落・実測フレームレート）が得られる
  - _Requirements: 3.5, 3.6, 4.2, 2.8_
  - _Depends: 8.2, 9.2_

- [x] 9.4 解像度・fps を掃引し、設定を決定する
  - モード掃引を実機で実行し、少なくとも 640×480 の 30 fps と 60 fps を同一条件で比較する
  - **実効サンプル数**（一定時間窓あたりの有効フレーム数）で比較し、フレームレート単体で決めない
  - USB2 接続が検出された回の結果は無効として扱う
  - 採用する設定とその選定根拠を記録し、既定値へ反映する
  - **Pi 4 を継続するかの判断は行わない**（材料の提供にとどめ、判断は `m1-prediction-validation` へ委ねる）
  - 観測可能な完了状態: `measurements.md` に全候補モードの実測値と、採用設定・選定根拠が記録され、既定設定がその結論に更新されている
  - _Requirements: 11.5, 1.5, 9.6_
  - _Depends: 9.3_

- [ ] 9.5 計測 ON / OFF の影響を実機で確認する
  - _Note: live で3本同時にオープンできず実行不能だった問題は、design.md「3条件が入力元を共有する理由」の追記（A案: 1本の入力元を共有）で決着した。実行にはタスク 7.4 の作り替えが先に要る。経緯は `measurements.md` タスク9.5 を参照。_
  - 計測比較を実機で実行し、ログ無効・ログ有効・記録有効の3条件を交互に比較する
  - 事前に確定した判定基準とともに、実測値・ばらつき・判定結果を記録する
  - 判定が偽の場合は、**計測結果を無条件に有効なものとして扱わない**旨を記録し、送出経路の見直し（付随値の削減・キュー容量・フラッシュ方針）を行う
  - 観測可能な完了状態: `measurements.md` に3条件の実測値・判定基準・判定結果が記録され、判定が真であるか、偽の場合の対応が明記されている
  - _Requirements: 10.2, 10.4_
  - _Depends: 9.4, 7.4_

- [x] 9.6 実データを記録し、WSL で再生する往復を確認する
  - 実際の投擲を Pi 上でリングバッファ方式により記録する
  - 記録一式を WSL へ持ち帰り、**SDK も実機も無い状態で**再生する
  - 同一記録を複数回再生して同一系列になることを確認する
  - 検出後の Throw Record を保存する経路が、セッション識別子で記録と対応付くことを確認する
  - 観測可能な完了状態: Pi で記録したセッションを WSL 上で2回再生して系列が一致し、対応付けたセッション識別子から記録側のフレーム範囲を引ける
  - _Requirements: 5.1, 6.1, 6.2, 6.3, 7.7_
  - _Depends: 9.4_

- [ ] 9.7 決着した未決事項をプロジェクト文書へ反映する
  - OQ-23・OQ-24・OQ-25・OQ-28・OQ-32・OQ-35 の結論を、決定内容と理由とともに決定記録へ移す
  - 未決事項一覧から該当行を削除し、本文側の参照も同時に外す（片方だけ直して食い違う状態を作らない）
  - **OQ-27・OQ-40・OQ-41 は決着させない。** OQ-41 については「SDK を依存表に書けない」という実測結果を判断材料として追記する
  - 観測可能な完了状態: 未決事項一覧に対象6件が残っておらず、決定記録に6件の結論と根拠が記載され、本文側に決着済み項目への未決参照が残っていない
  - _Requirements: 1.8, 11.5_
  - _Depends: 9.5, 9.6_

## Implementation Notes

- タスク1.4: `Sysstat.cpu_percent` は「プロセスの消費 jiffies ÷ システム全体の消費 jiffies × 100」として実装した（`/proc/self/stat` と `/proc/stat` の両方を分子・分母として使う設計）。design.md は両ソースを1つの `cpu_percent` フィールドの根拠として列挙するのみで式を明記していないため、この解釈をモジュール docstring に明記した。タスク2.2（`CaptureMetrics`）がこの値を消費する際は「システム全容量に対するプロセスの専有率（コア数に依存しない 0-100%）」という定義であることを前提にする。
- タスク1.5: `tests/prediction_core/` 配下に `test_types.py` / `test_config.py` / `test_boundaries.py` / `test_public_api.py` が既に存在し、かつ `tests/` 配下のどのディレクトリにも `__init__.py` が無いため、pytest の既定 import mode（prepend）は**同名の test ファイルをツリー全体で一意にしか扱えない**（`import file mismatch` で collection が失敗することを実測で確認済み）。`__init__.py` を追加すると `sensing_foundation` というテストサブパッケージが実パッケージ `src/sensing_foundation` を shadow するリスクがあり、`--import-mode=importlib` へのグローバル切替は `tests/prediction_core/test_analytic.py` の `from analytic import ...` という同階層 import を壊すため、どちらも採用不可。**本 Spec のテストファイルが `prediction_core` 側と衝突する場合は、ファイル名をこの Spec 側だけ書き換えて回避する**（例: `test_types.py` → `test_core_types.py`）。design.md の File Structure Plan が挙げるファイル名と一致しない箇所が出るが、これは意図的な回避であり境界違反ではない。**タスク1.6（config）・8.1（cli）・8.2（public_api / boundaries）は同じ衝突に当たる見込みなので、着手前にこの節を確認し、同じ回避方針（`test_sensing_config.py` 等への改名）を踏襲すること。**（1.6 は `test_sensing_config.py` として実施済み。8.1・8.2 も同様に改名すること。）
- タスク1.6: `RuntimeSettings.resolve()` は design.md の Components 表が `Sysstat` を依存に挙げていないため、リングバッファの RAM 上限チェックに必要な搭載 RAM 量を `installed_ram_bytes: int | None = None`（キーワード専用・追加パラメータ）として呼び出し側から受け取る形にした。`max_ring_bytes` が未指定かつ `installed_ram_bytes` も未指定の場合、**チェックは何もエラーにせず素通りする**（ドキュメント化済み）。**タスク8.1（CLI）は `Sysstat.sample().system_total_bytes` を必ず `installed_ram_bytes` として渡すこと** — さもないと RAM 上限チェックが実運用で常に無効のままになる。また `on_acquire_error` / `recording.mode` / `recording.compression` などの `Literal` 型フィールドは、解決時に許容値集合との照合を行っていない（タイプミスの値がそのまま通る）。将来のタスクでバリデーションを足す場合はこの点を踏まえること。
- タスク2.1: `LoggingConfig(queue_capacity=0)` を `config.py` の検証を経ずに直接構築すると、`queue.Queue(maxsize=0)` は無制限キューになり「有界キューで満杯時は破棄する」という要件8.6の前提が崩れる。`RuntimeSettings.resolve()` 経由なら 1.6 のバリデーションで弾かれるが、`LoggingConfig` を直接構築する経路（テストや将来のタスク）では弾かれない。`StructuredLogger`/`get_logger()` 自体はこれを検証しない設計とした。`close()` は初回呼び出しがタイムアウトすると `_closed` が立ったまま以後 `session_end` を二度と書けなくなる（意図的なトレードオフ、docstring に注記済み）。
- タスク3.1: `BaseFrameSource` は `CaptureConfig.drain_enabled` フラグを見て `_drain_latest()` を呼ぶかどうか自体を制御する（一元化の解釈）。design.md の `RecordedSource` 節は「`_drain_latest()` は常に `(None, 0)` を返す」ともアダプタ側の自己防衛として明記しているため、**タスク3.2（SimulatedSource）・4.5（RecordedSource）は両方の防御線を実装すること**: (a) 自分の `super().__init__()` 呼び出しで `drain_enabled=False` を固定して渡す、**かつ** (b) `_drain_latest()` 自体も常に `(None, 0)` を返す実装にする。どちらか一方だけでは要件6.7（再生・合成では取りこぼしを新たに作らない）の防御として不十分になり得る。`RawFrame`（`source.py` で新設、`types.py` には無い）は `seq` / `depth` / `device_timestamp_ms` / `timestamp_domain` / `capture_latency_ms` の5フィールドを持つ。`_acquire()` は「取得失敗」を `None` 返却、「供給が正常終了した」を `StopIteration` 送出で区別する（design.md 未規定の拡張）。`open_source()` はタスク3.1では実装せず、タスク4.6（3アダプタが揃った後）に委ねた。
- タスク3.2: `SimulatedSource.__init__` は design.md の4引数（`supplier, profile, metrics, fps=30`）に加え、キーワード専用の `clock: SessionClock`（必須。`BaseFrameSource.__init__` が要求するが `CaptureMetrics` は自分の clock を公開しないため回避不能）と `capture_config: CaptureConfig | None = None`（任意）を追加した。**タスク4.6（`open_source`）はこの拡張シグネチャを把握して呼び出すこと。** `RawFrame.seq` は合成入力の呼び出し通し番号（0,1,2,...）をそのまま使い、`tests/sensing_foundation/synthetic.py` の `decode_seq_from_frame()` は使わない（`src/` から `tests/` を import しない境界を守るため）。そのため `seqs` に欠番のある `synthetic.make_supplier` を `SimulatedSource` 経由で流しても、このアダプタの seq は常に連番になり欠落は起きない — 欠落検出のエンドツーエンド確認はタスク4.6の契約テストで別アダプタ（再生など）を使って行うこと。`fps` 引数は受け取るが未使用（供給間隔は呼び出し側の `synthetic.make_supplier(delay_s=...)` の責務）。
- タスク4.3: `SessionRecorder.write()` は連続失敗が上限（既定100）に達しても例外を上げず、内部で `_stopped=True` にして以後の `write()` を no-op にする（`RecordingWriteError` はログ送出のみに使い、送出=raiseではない）。コンストラクタは design.md の7引数に加え `source: SourceKind`（必須。manifest の `source` キーの由来が他に無いため）、`capture: Mapping | None = None`、`failure_cap: int = 100` を追加した。**タスク4.6・8.1（`open_source`/CLI）はこの拡張シグネチャを踏まえて呼び出すこと。** `with SessionRecorder(...) as rec:` で明示的に `close(stats)` を呼ばずにブロックを抜けた場合、`__exit__` は書き込んだフレームの `dropped_before`/`gap_before` から `CaptureStats` を**自前で再構成**して `close()` する保険機構を持つが、これはリングバッファ方式（`FrameRingBuffer.flush_to()` が一部フレームだけを渡す運用）では正確でない可能性がある — **明示的な `close(stats)` 呼び出しを必ず行うこと**（保険機構に頼らない）。`RecordingStats.bytes_written` は「索引 `len` の総和と一致する」とdocstringにあるが、blob書き込み成功→索引書き込み失敗のケースでは索引総和を上回りうる（`bytes_written` は物理バイト数の正であり、この場合はそちらが正しい）。
- タスク4.4: `CaptureFrame`（`types.py`、タスク1.5）は `__eq__` を独自定義していない frozen dataclass であるため、複数画素を持つ `depth`（numpy配列）を含む2つの `CaptureFrame` を素の `==` で比較すると `ValueError: The truth value of an array with more than one element is ambiguous` を送出する。**タスク4.6（`test_source_contract.py`）はフレーム系列の等価性比較で同じ地雷を踏む可能性が高い。** `tests/sensing_foundation/test_reader.py` の `_assert_frames_equal()`（`depth` のみ `np.array_equal`、他フィールドは `==`）と同等のヘルパを流用・踏襲すること。`SessionReader` のテストフィクスチャは `SessionRecorder`（4.3）を呼ばず手書きで用意した（design.md の意図通り、書き出し側と読み出し側の対称なバグが互いを隠さないようにするため）。
- タスク4.5: `BaseFrameSource`（タスク3.1）は `_next_index` / `_kind` / `_profile` / `_metrics` / `_clock` / `_drain_enabled` / `_acquire_timeout_ms` / `_on_acquire_error` / `_last_seq` / `_final_stats` という私有属性を自分の内部状態として持つ。**サブクラス（アダプタ）が独自の読み出しカーソル等を持つ場合、これらの名前と絶対に衝突させないこと。** `RecordedSource` の実装中、内部カーソルを最初 `self._next_index` と命名してしまい、`BaseFrameSource.frames()` 自身が使う同名属性と衝突して1フレームおきに読み飛ばす／偽の `gap_before` を生む不具合が発生した（テストで検出・`self._next_read_index` へ改名して解消）。**タスク6.1（`RealSenseSource`）は同じ罠を踏む可能性が高いので、`BaseFrameSource` の私有属性名と重複しない名前を選ぶこと。** また `RecordedSource.__init__` は design.md 未記載の追加引数 `clock: SessionClock`（必須）・`capture_config: CaptureConfig | None = None`・`speed: Literal["fast","realtime"] = "fast"`・`sleep: Callable[[float], None] = time.sleep`（テスト用の差し替え口）を持つ。**タスク4.6（`open_source`）はこの拡張シグネチャを把握して呼び出すこと。** `RawFrame` に `t_capture_ms` フィールドが無いため、`BaseFrameSource` は常に自分の `SessionClock.now_ms()` で `t_capture_ms` を再計算する。したがって `RecordedSource` が返す `CaptureFrame.t_capture_ms` は元の記録セッションの値ではなく**再生セッション**の経過時間になる（`speed="realtime"` の待機秒数計算だけは `SessionReader` から読んだ元の `t_capture_ms` 差分を内部的に使う）。`t_capture_ms` 以外のフィールド（`seq`/`depth`/`device_timestamp_ms`/`timestamp_domain`/`capture_latency_ms`/`dropped_before`/`gap_before`）は `speed` の値に関わらず完全に一致する。
- タスク4.6: `open_source(settings, metrics, *, clock, supplier=None, speed="fast")` を `source.py`（タスク3.1のファイル、design.mdの記載通り）に追加した。design.md 自身に内部矛盾がある — `source` は依存方向表でレイヤ5（0〜4のみimport可）と規定されているが、`open_source()` はレイヤ6の `SimulatedSource` とレイヤ7の `RecordedSource`/`RealSenseSource` を構築する必要がある。**解決策として、各アダプタのimportを `open_source()` の分岐ブロック内（関数ローカル）に置き、`source.py` のモジュールレベルのimportグラフはレイヤ0〜4のままに保った**（`sources/realsense.py` が `pyrealsense2` を遅延importするのと同じ機構だが、目的は「SDK未導入への対処」ではなく「レイヤ方向制約の回避」である点が異なる）。**タスク8.2（`test_boundaries.py`）はこの正当性を把握した上で、境界検証をモジュールレベルのimportのみを対象に実装すること**（関数内部のimportまで含めて素朴にスキャンすると、この正当な遅延importを誤って違反判定してしまう）。あわせて design.md の依存方向表 `source` 行に、この例外を明記する追記を検討すること（`realsense` 行に既にある注記と同様の形で）。`SourceKind.SIMULATED` 用の `StreamProfile` は `depth_scale_mm=1.0`（テスト群全体の既存フィクスチャと同じ慣習値）・`intrinsics=None`（`types.py` の `StreamProfile.intrinsics` docstring が「入力元がカメラ内部パラメータを提供できない場合は `None`」と明記する正規の値）で内部構築する。
- タスク5: `errors.py`（タスク1.2）に `ThrowRecordFormatError` / `ThrowRecordVersionError`（後者は前者のサブクラス）を新設した。既存の `RecordingFormatError`/`RecordingVersionError` はフレーム層（`SessionReader` の索引・ブロブ整合性）専用であり、Throw Record（サンプル層のNDJSON、`prediction_core.SCHEMA_VERSION` を持つ）に流用すると別レイヤの例外を同じ except で誤って捕まえる事態を招くため、意図的に別クラスとした。`iter_records()` は破損行で例外を送出して停止する（呼び出し側が既に受け取った行は失われない）方式とし、`iter_with_issues()` は破損行を `ThrowRecordReadIssue` として報告しつつ後続行の読み出しを継続する。**design.md の Error Categories コードブロックと `__all__` 一覧（`PublicApi` 節）はこの2例外をまだ含んでいないため、ドキュメント同期の追記が望ましい**（動作・テストには影響しない）。
- タスク6.1: `pyrealsense2` が未導入のこの開発環境では実SDKと突き合わせて検証できないため、モックAPI形状は `research.md` の記述を根拠にした最善の推定である（`wait_for_frames`/`poll_for_frames`/`get_timestamp`/`get_frame_timestamp_domain`/`RS2_OPTION_GLOBAL_TIME_ENABLED`/`usb_type_descriptor` 等）。不確実なSDK呼び出し箇所は広めの `except Exception` と truthy チェックで防御している。**タスク9.2（実機導通確認）で実SDKと突き合わせ、この形状が違っていた場合は本タスクへ差し戻すこと。** `RealSenseSource` は `profile` を construction 時は暫定値（`intrinsics=None`）で持ち、`start()` 内で実機から取得した値へ再代入する（`BaseFrameSource._profile` は private属性であり再代入可能、`SimulatedSource`/`RecordedSource` とは異なる挙動）。`source.py` の `open_source()` docstring/コメントには「`RealSenseSource`（タスク6.1）はまだ存在しない」という記述が残っており、6.1完了後は古くなっている（`source.py` は6.1の境界外のため意図的に触れていない — タスク6.2または8.2で更新すること）。
- タスク7.1: `summarize_log()` は NDJSON ログのみを対象とし、`recording/reader`（セッション記録）は使わない（依存方向表がこのモジュールに `recording/reader` の import を許可しているのは未使用の余地であり、design.md の固定インターフェース（`summarize_log(path, *, stages=None) -> LogSummary`）と本タスクの観測可能な完了状態は log-only で一致する）。**design.md の `LogSummarizer` の Intent 行は「NDJSON ログとセッション記録」という広い表現を使っており曖昧さが残るため、将来 design.md の文言を精査する場合はこの点に注意すること。** 分位点計算は `k=(n-1)*q` の線形補間（numpy の `method='linear'` 相当）。
- タスク7.2: `ModeSweep.run()` の戻り値は design.md の `tuple[ModeResult, ...]` から `tuple[ModeResult | None, ...]` へ拡張した。フレームを1枚も取得できずに失敗したモード（`stream_open` 失敗を含む）は `None`、部分的にでもフレームを取得した後に USB2警告等が検出された場合は `ModeResult` に追加フィールド `valid: bool` / `invalid_reason: str | None` を付けて返す（design.md の Batch/Job Contract「null」記述と Implementation Notes「無効として記録する」記述の食い違いを両方字義通り満たそうとした結果のハイブリッド）。**`stream_open` 失敗は実際には常に `None` 経路になり `valid=False` 経路には到達しない**（`BaseFrameSource.start()` が1度も `frames()` を呼ぶ前に失敗するため）— design.md の文言はこのケースも「無効として記録する」対象に含めているように読めるが、実装は `None` に倒す。`effective_samples_per_window()` は完全な窓のみを数える固定非重複窓方式（`window_ms` 未満の短い実行時のみ代数近似 `measured_fps * window_ms/1000` にフォールバック）。`run()` は design.md 未記載の `supplier`/`speed` キーワード専用引数を持つ（`open_source()` と同じ理由）。テストファイル名は `test_bench_modes.py`（design.md の `test_bench.py` ではない — タスク7.3の将来のテストファイルとの衝突を避けるため）。**タスク7.3は同様の衝突回避（例: `test_bench_logging.py`）を踏襲すること。**（7.3 は `test_bench_logging.py` として実施済み。）
- タスク7.3: `LoggingOverheadBench` は `logging_off`/`logging_on`/`recording_on` の3条件を「1回の比較につき要因を1つだけ変える」方式で測る。**`recording_on` は意図的にロガーを `NullLogger` へ強制し、記録自体（`SessionRecorder.write()` のI/O）のオーバーヘッドのみを単離して測る** — タスク4.3で記録経路にも計測点（`logger.timed("record","write",...)`）を置いたが、この計測点自体のコストは `recording_on_vs_logging_off` の判定には含まれない。つまり実運用（記録＋その計測点のロギングが両方有効）の合算オーバーヘッドは、この判定結果よりわずかに大きくなりうる。**タスク9.5（実機でのON/OFF比較）はこの前提を踏まえ、`measurements.md` に記録する際にこの単離方針（記録のみ・ロギングは別測定）を明記すること。** 判定基準文字列は実測前に固定した定数（design.md の文言と逐語一致）。`median_delta_ms` は `abs()` を取る（ONがOFFより速くなった場合も「有意な差」として同様に検出するため）。
- タスク8.1: `--source simulated` をCLIから直接使う場合（外部supplierの配線が無い場合）向けに、`cli.py` 内部に最小限の決定的パターン生成器 `_smoke_supplier()` を持たせた（`tests/synthetic.py` は import しない — 境界厳守）。物理演算は一切持たず、CLIレベルのスモークテスト専用であり、`trajectory-simulator` の実物理供給とは別物である旨をdocstringに明記。停止は supplier の終端ではなく CLI 自身の `--duration-s` 経過判定（`SessionClock` 基準）で行う。再生サブコマンド名は `replay-session`（`replay` ではない）— `prediction_core.replay(record)` はサンプル層の別操作であるため。`--source recorded` を指定せずとも `replay-session` は内部で `source=recorded` を強制する。argparse の各フラグの `dest` は `config.py` の `_FIELD_SPECS` の20キーと1対1で一致させた。エラーハンドリングは `SensingConfigError`（exit 2）→ `SensingFoundationError`（exit 1）の順で捕捉する。
- タスク8.2: `sensing_foundation.__all__` は design.md 記載の43シンボルに加え、`ThrowRecordFormatError`・`ThrowRecordVersionError`（タスク5で新設、design.md未記載）・`link_to_session`（design.md未記載だが要件7.7の対応付け機構として公開判断）の3つを追加し、計46シンボル。境界テストは「レイヤ方向チェック」（モジュールレベルのimportのみを見る — `open_source()`/`sources/realsense.py` の関数内遅延importを正しく除外）と「pyrealsense2/prediction_core 排他性チェック」（関数内も含む全ASTウォーク — 例外モジュール以外での関数内違反も検出する）を明確に使い分けている。**`source.py` の `open_source()` docstring/コメントに残る「RealSenseSource（タスク6.1）はまだ存在しない」という古い記述は本タスクの境界外のため未修正のまま** — 別途のドキュメント同期タスクで直すこと。境界チェックの補助関数 `collect_module_level_internal_targets()` は `from sensing_foundation import X` のようにトップレベルパッケージへ直接importする形を正しく処理できない（現状どのモジュールもそうしていないため実害なし、将来同様のimportを書くモジュールが現れた場合は要確認）。

## 全体まとめ: タスク1〜8（ハードウェア不要な全作業）が完了

`uv run --extra sensing pytest -q` は822件全通過（SDK非導入・実機非接続のこの開発環境で）。

- タスク9.3: **実機（SDK 有り）では 12 件のテストが失敗する。** すべて「SDK が存在しない環境」を前提に書かれたものであり、着手前から存在する（`test_doctor.py` 7件 / `test_realsense_source.py::TestSdkNotInstalled` 3件 / `test_sensing_cli.py` 1件 / `test_sensing_boundaries.py` 1件）。**このうち `test_import_sensing_foundation_succeeds_without_sdk` だけは性質が異なり、`sys.modules` というグローバル状態を見ているためテスト順序依存になる**（単独では PASS、`probe_sdk()` を呼ぶテストの後では FAIL）。ソース側の遅延 import 設計は正しく、欠陥ではない。design.md は「live 以外は**実機・SDK なしで**全通過すること」としか規定しておらず、**SDK が存在する環境での扱いは未規定**である。**タスク9.4 以降で実機のテストスイートを緑にする必要が生じた場合、まず design レベルで方針を決めること**（skipif で環境を切り分けるか、境界テストを静的解析へ移すか）。
- タスク9.3: **live は終端しない。** `_consume()`（`list(source.frames())`）を live に渡すと戻らない。テストで live を扱う場合は枚数境界の `_take()`（`test_source_contract.py`）または `cli._drain_frames()` の時間境界を使うこと。
- タスク9.3: **取得区間の実時間を測る際、`with` を抜けた後まで測らないこと。** RealSense の `stop()`（pipeline 停止）に約 0.58 秒かかり、`CaptureMetrics` の計測窓（構築時刻 → `stats` 読み取り時点）と食い違う。タスク9.4（fps 掃引）で実時間ベースの比較を行う際に同じ罠がある。
- タスク9.3: **`CaptureStats` の各項目を「在ること」で検証しない。** `frames_dropped >= 0` のような表明は `int` カウンタである限り決して落ちず、壊れた実装を検出できない。また `measured_fps` を `frames_yielded / (duration_ms/1000)` と比較するのは `metrics.py:169` と同じ式であり恒真になる。**観測した系列から独立に数え直して突き合わせること**（`frames_missing` は `seq` 差分と、`frames_dropped` は `dropped_before` の総和と、`measured_fps` はテスト側の時計で測った実時間と）。
- タスク9.3: **実機の D435 Depth ストリームは歪み係数がすべて `0.0` である**ことを実測で確認した。`sensing_foundation/geometry.py`（歪み補正を恒等として扱う）と `world_frame_calibration/deproject.py` の `ensure_supported_distortion()`（非ゼロ係数を**受理せず失敗させる**）が置いた仮定は、実機で成立する。

- タスク9.4: **`bench-modes` の `--warmup-s` の既定値 0.0 は罠である。** design.md「ModeSweep」は「ウォームアップ区間を**必ず**設ける」と定めているのに CLI 既定は 0.0 であり、そのまま測るとウォームアップ込みの値が出る。タスク9.3 で観測した `measured_fps` 17.4〜20.5（30fps 要求時）はこれが原因で、`--warmup-s 2 --duration-s 10` で測り直すと 30.08 fps になった。**タスク9.5（`bench-logging`）でも同種の引数があれば同じ罠を確認すること。**
- タスク9.4: **`src/sensing_foundation/**` を変更すると `world-frame-calibration` の境界テストが失敗する。** 同 Spec タスク7.3 の `test_actual_working_tree_changes_since_main_stay_within_boundary` が `main` からのブランチ全変更に対して `src/sensing_foundation/` への変更を禁止しており、**ブランチが単一 Spec のものであることを前提としている**ため、sensing-foundation 自身の作業ブランチで誤検出する。**本 Spec 側で回避策を当てないこと**（それ自体が越境である）。`world-frame-calibration` 側のタスクとして起こし、マージ前に解決する。
- タスク9.4: **既定 fps を 60 へ変更したことは下流に波及する。** `world-frame-calibration` の `check_compatibility()` は保存結果の解像度・fps・Depth スケール・Color 有無を現在の入力元と突き合わせるため、**30fps で取ったキャリブレーション結果は 60fps の live 入力に対して `PROFILE_MISMATCH` で失敗する**（設計どおりの正しい挙動）。同 Spec のタスク8（実機キャリブレーション）は**必ず 60fps で実施すること**。
- タスク9.4: **実効サンプル数には「フレーム層」と「点層」の2つがある。** 9.4 で倍増を確認したのは**フレーム層**であり、検出処理を含む点層の実効点数は `flying-object-tracking` の測定対象で未測定。下流が 1 フレーム 16.6ms に収まらない場合、ドレインが働いて点層は倍増しない（欠落は生じず、余分な取得コストを払うだけ）。**下流の実測後に既定 fps を再検討してよい。**

- タスク9.6: **`frame_index_from`/`frame_index_to` の意味が未定義である（未解決・要 design 判断）。** 同じ「フレーム番号」に見える3つの量があり、**リングが古いフレームを追い出したときだけ食い違う**: (a) `SessionReader.read(i)` の `i` は索引ファイルの**行位置**、(b) 索引行の `i` フィールドは**記録**セッションの通し番号、(c) `RecordedSource` が返す `CaptureFrame.index` は**再生**セッションの 0 始まり通し番号。実測（181枚取得して直近60枚を保存した記録）では行位置 0 の `i` が 121 だった。`design.md` L1178/L1187/L1491 も `requirements.md` も `frame_index_*` がどれを指すか定義していない。**投擲だけを残すリング運用（要件 5.5）はまさに追い出しが起きる使い方**であり、下流が「記録側の通し番号」のつもりで値を入れて利用側が行位置として渡すと静かにずれた範囲を読む。なお `types.py` は `CaptureFrame.index` を「セッション内の 0 始まり通し番号。欠番なく増加する」と定義しており、この不変条件を満たすのは `RecordedSource` の側で `SessionReader.read()` の戻り値（121 始まり）は満たさない——是正の起点はここになると思われる。**本タスクでは是正していない**（`SessionReader` はタスク4.4、`link_to_session` はタスク5 の境界）。検証テストには前提の表明（`index_rows[0]["i"] == 0`）を置いた。
- タスク9.6: **live 記録の manifest にデバイス識別情報が無い（要件 5.2 未充足・未解決）。** `cli.run_record()` が `SessionRecorder(..., device=None, ...)` を固定で渡しているため、実機で撮った記録でも `"device": null` になる。`sources/realsense.py` の `probe_devices()` は `serial_number`/`firmware_version`/`usb_type_descriptor` を返せる（タスク9.2 で実測済み: serial 834412071095 / FW 5.17.3.10）ので、**情報が無いのではなく渡していない**。どの個体・どのファームウェアで撮った記録かが後から追えない。あわせて `_build_runtime_info()` が `"global_time_enabled": None` を固定で返す（入力元を開く前に組み立てるため実際の有効化結果を入れる経路が無い）——タスク6.1 の「有効化できたかどうかをメタ情報とログに残す」の字義を満たしていない（索引行の `ts_domain` から判断は可能なので影響は限定的）。**いずれも `cli.py`（タスク8.1）の境界**であり、9.6 の要件（5.1/6.1/6.2/6.3/7.7）には含まれない。**実機でしか観測できない欠陥**なのでタスク8.1 への差し戻しとして扱うこと。
- タスク9.6: **記録の実効レートを `summary.json` の `measured_fps` から読まないこと。** 15秒・900枚の記録で `measured_fps` は 10.04 と出るが、`CaptureMetrics` の計測窓が取得後の zlib 圧縮と書き出し（約74秒）を含むためである（9.3 発見4・9.4 発見1 と同根）。**索引の `t_capture_ms` の先頭と末尾から算出すること**（本記録では 899 間隔 15008.6ms = 16.695ms/枚 = 59.9fps、破棄0・欠落0）。
- タスク9.6: **2回の再生を互いに突き合わせるだけでは記録の忠実性を検証できない。** `RecordedSource` は内部で `SessionReader` を使うため、両方が同じように壊れる欠陥を原理的に検出できない。負の対照（`SessionReader.read()` が返すフレーム1枚の Depth を1画素だけ書き換える）で実証済み: 独立参照と突き合わせる2件は失敗したが、`test_two_replays_produce_equivalent_series` は通過した。**期待値は `frames.ndjson`/`depth.bin`/`manifest.json` から素の `json` と `zlib` で独立に組み立てること**（タスク4.4 が手書きフィクスチャを選んだのと同じ理由）。
- タスク9.6: **版管理しない実データを要するテストは環境変数で与え、無ければ skip する。** `tests/sensing_foundation/test_real_session_roundtrip.py` は `SENSING_REAL_SESSION_DIR` が指定されたときだけ実行する（タスク9.3 が実機の有無で `skipif` した構造と同じ）。ただし**指定されたのに読めない場合は skip ではなく失敗させる**——設定の誤りを静かに飛ばすと検証を実行しないまま緑になる。
- タスク9.6（下流 `flying-object-tracking` への申し送り）: **640×480 で 2m 級の距離では、背景差分だけで投擲物を分離するのは困難である。** 実測した背景差分のノイズ下限（8×8ブロックで熱いブロック数の中央値 14）に対し、2.4m 先の紙ボール（直径約7cm）が占める面積は fx≈385px から見積もって約8px四方 = 64px 程度しかない。本タスクの記録で検出できた強い区間（最大96ブロック）は距離約1.0m・継続1.8〜6.1秒であり、投擲物の飛行時間（200〜400ms）ではなく**投げている人の腕や体**である可能性が高い。投擲距離を詰めるか、時間方向の情報（フレーム間差分・軌道の連続性）を使う方式が要る。
- タスク7.4: **`Protocol` で定義した口は「実行中に向き先を差し替える」余地をタダで与える。** `obslog.Logger` が `Protocol`（構造的部分型）であるため、`enabled`/`emit`/`stage`/`timed`/`stats`/`close` の6要素を満たす**私有の転送クラス**（`bench/logging_overhead.py` の `_RoutingLogger`）を1つ置くだけで、`CaptureMetrics` を1インスタンスに保ったままログの向き先をセグメント境界で切り替えられる。**`metrics.py` も `source.py` も1行も変えていない。** 「条件ごとに計測点を作り直す」以外に道が無いように見えたのは、`Logger` を具象型として見ていたからだった——同種の「条件だけ差し替えたい」要求が出たら、まず口が `Protocol` かを確認すること。
- タスク7.4: **計測点を共有したら、条件別の値は累計ではなく「セグメント境界の前後で読んだ差分」で取ること。** `CaptureMetrics.counters()` は構築時からの累計を返すため、共有した状態で各条件が終端の値をそのまま読むと**3条件とも同じ総数**になり、条件間の比較が静かに無意味になる（`frames_dropped` は判定基準の一方の柱なので、これは判定そのものを壊す）。差分の読み取りは `active_elapsed_ms` の計測窓の**外側**に置く（`counters()` 自体が `clock.now_ms()` と除算を行うため）。
- タスク7.4: **入力元を共有すると `StopIteration` は3条件同時に効く。** 旧構造では条件ごとに別の供給を持っていたため「1条件だけ尽きる」ことがあり得たが、1本を分け合う以上それは起こらない。`simulated` で回すときは供給が**3条件ぶんの合計**（おおむね `3 * cycles * segment_s` 秒ぶん）を賄える必要がある——1条件ぶんの見積もりで用意すると、後半の条件がサンプル0で終わる。
- タスク7.4: **構造の作り替えが「測っているもの」を変えていないことは、旧実装との同一入力比較で示せる。** HEAD 版と 7.4 版を同じ合成入力（`--source simulated --segment-s 0.05 --cycles 3`）で走らせ、3条件の `total_ms_p50`（0.0053/0.0264/0.0127 対 0.0055/0.0258/0.0133 ms）と2つの判定（両方とも `False`、`median_delta_ms`・`baseline_iqr_ms` ともほぼ同値）が一致することを確認した。テストの通過だけでは「同じものを測り続けている」ことまでは示せない。
- タスク7.4（タスク8.1 への差し戻し）: **`cli.py` の `run_bench_logging()` の docstring が古くなった。** 「入力元は `LoggingOverheadBench.run()` が内部で `with source_off, source_on, source_rec:` する」と書いてあるが、7.4 以降は `with source:` の1本である（同モジュール冒頭の「`FrameSource` に触れるすべてのサブコマンド」節にも同じ記述がある）。`cli.py` は本タスクの `_Boundary: LoggingOverheadBench_` の外なので**修正していない**。
- タスク4.7: **「同じ名前で呼ばれている量が実は複数ある」欠陥は、たまたま一致する条件でだけテストしていると永久に見つからない。** 行位置・記録側通番・再生側通番の3つは、リングが1枚も追い出していない記録では完全に一致する。既存テストはすべてその条件下で書かれていたため、全部緑のまま欠陥が残っていた。**区別が生じる条件（ここでは追い出し）を意図的に作るフィクスチャを用意し、「いま実際に食い違っていること」を最初のテストで確かめてから本題に入る**構成にした（`test_frame_index_contract.py::test_the_recording_actually_evicted_frames_so_the_quantities_differ`）。この前提テストが無いと、フィクスチャの設定を1つ間違えただけで以降のテスト全部が空振りする。
- タスク4.7: **識別子として保存する値には、「読み出し方に依存しない量」を選ぶこと。** `frame_index_*` は記録側の通し番号（索引行の `i`）と定めた。行位置を選ばなかったのは、要件 7.7 が求めるのが「後から対応付けられる**識別子**」であり、行位置はファイル内の**位置**であって識別子ではないからである——記録を切り詰めれば同じ値が別のフレームを指す。記録側の通し番号は `seq` / `t_capture_ms` と同じく記録に書き込まれた事実である。
- タスク4.7: **取り違えを「静かに間違った答えを返す」から「その場で失敗する」へ変えるのが是正の本体である。** `position_of()` は実在しない番号に対して `IndexError` を送出し、**メッセージへ実在する範囲と行位置の範囲を両方入れる**（取り違えた側がその場でどちらの量を渡したか判別できるため）。あわせて索引行の `i` の重複を構築時に拒否した——重複を許すと `position_of()` が黙って一方を捨て、同じ識別子が2つのフレームを指す。
- タスク4.7: **不変条件は「どの層に適用されるか」まで書かないと、別の層で破れたときに誰も気づけない。** `types.CaptureFrame` の「`index` は 0 から欠番なく増加する」は `FrameSource` が下流へ渡すフレームについてのものであり、`SessionReader.read()` の戻り値は対象外である（記録時の値を保つ）。この適用範囲が書かれていなかったため、`SessionReader` が不変条件を破っているのか正しいのかを判断できない状態が続いていた。
- タスク4.7: **境界の「間」の不整合は、独立したテストファイルへ集めると各境界の単体テストの性格を壊さずに済む。** 本タスクの欠陥は `FrameRingBuffer` / `SessionRecorder` / `SessionReader` / `link_to_session` / `types.CaptureFrame` の**間**にあり、どれか1つの中では契約を定義できない。`test_reader.py`（書き出し側から独立した手書きフィクスチャを主証拠にする方針）を汚さずに、境界をまたぐ約束だけを `test_frame_index_contract.py` へ置いた。
- タスク4.7: **負の対照でテストの非空虚性を確かめた。** `SessionReader.read()` が記録側通番の代わりに行位置を返すよう壊すと7件が失敗する（契約テスト4件すべてを含む）。テストが通ることと、テストが欠陥を検出できることは別である。

## タスク9 進捗（2026-08-27 時点）

実機（Raspberry Pi 4 Model B Rev 1.2 / 4GB モデル）が到着し、タスク9に着手した。
記録先の `.kiro/specs/sensing-foundation/measurements.md` を新設済み。

- **9.1 完了**: Raspberry Pi OS 64-bit（**Desktop 版**、Debian 13 "trixie" ベース）を導入し、
  SSH 経由で `doctor` を実機実行。OS・カーネル・64bit判定・RAM容量・Python版を記録した。
  搭載 RAM は 3,973,906,432 bytes（4GB モデル）で **OQ-24 が決着**。
  リングバッファ上限の既定割合 25% は必要量の約18倍の余裕があり**妥当と判断して変更しない**。
- **9.2 完了**: librealsense **v2.58.3** を `-DFORCE_RSUSB_BACKEND=ON` でソースビルドし（約72分、`make -j3`）、
  Python 3.13 向けバインディングを生成して venv へ配置。`doctor` の**全9項目が `ok`** となった。
  D435 は **USB 3.2 / FW 5.17.3.10 / serial 834412071095**、640×480@30fps でストリームを開け、
  30枚取得で dropped 0 / missing 0。**OQ-23・OQ-28 が決着**（Ubuntu 退避は不要だった）。
  - ✅ **9.1 で申し送った最大リスク（trixie / GCC 14.2 / Python 3.13）は顕在化しなかった。**
    `research.md` が前提とした Bookworm より新しい環境でも v2.58.3 はエラーなくビルドできた。
  - ⚠️ **再ビルド時の注意**: `make install` の配置先（`/usr/local/lib/python3.13/dist-packages/`）は
    venv の探索パスに入らない。venv への `.so` 配置をやり直すこと（詳細は `measurements.md` 手順5）。
- ⚠️ **9.4 への申し送り**: microSD の書き込み速度実測は 31.9 MB/s。60fps の Depth は約 36.9 MB/s で
  **これを上回る**ため、60fps 評価時に連続記録を併用してはならない。
  また実測前に起動ターゲットを `Console` へ切り替え、デスクトップを常駐させないこと。
