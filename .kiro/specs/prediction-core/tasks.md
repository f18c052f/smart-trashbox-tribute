# Implementation Plan

> 本計画は `requirements.md`（要件1〜10 / 受入基準53件）と `design.md` に基づく。
> 実行時のサードパーティ依存はゼロ。すべてのタスクはハードウェア非接続で完了・検証できる。

## 1. 基盤: パッケージ骨組みと共有型

- [ ] 1. 基盤: パッケージ骨組みと共有型

- [x] 1.1 パッケージ構成とテスト実行基盤を用意する
  - `pyproject.toml` を PEP 621 形式で作成し、`requires-python = ">=3.11"`、**実行時依存なし**、開発依存を `pytest` のみとする
  - `src/` レイアウトで `src/prediction_core/__init__.py` と `tests/prediction_core/` を作成する
  - `tests/prediction_core/conftest.py` に既定設定を返す共通フィクスチャの器を置く
  - Raspberry Pi OS Bookworm 系（Python 3.11）で動く構文に限定し、PEP 695 のジェネリクス構文を使わない
  - 観測可能な完了状態: ハードウェアを接続しない環境で `python -m pytest` が成功終了し、パッケージが `import prediction_core` できる
  - _Requirements: 1.5, 7.1_

- [x] 1.2 (P) 単位換算モジュールを実装する
  - `MS_PER_S` と、mm/ms から mm/s、mm/s² から mm/ms² への換算関数を定義する
  - 換算係数はこのモジュールにのみ置き、他モジュールが `1000` や `1e6` を直接書かない方針を成立させる
  - 観測可能な完了状態: 換算の往復（mm/ms → mm/s → mm/ms²）が定義どおりであることを `tests/prediction_core/test_units.py` で検証し、テストが通る
  - _Requirements: 1.2, 2.5, 10.4_
  - _Boundary: Units_

- [x] 1.3 (P) 例外階層を定義する
  - 基底例外と、設定不正・レコードスキーマ不整合・直列化不能の3系統を定義する
  - 「予測の無効は値で返し、呼び出し方の誤りだけを例外にする」という区分を、この階層で表現する
  - 観測可能な完了状態: 各例外が基底例外として捕捉でき、`ValueError` としても捕捉できることを `tests/prediction_core/test_config.py` で固定する
  - _Requirements: 9.3, 10.3_
  - _Boundary: Errors_

- [x] 1.4 値オブジェクトと予測結果の直和型を定義する
  - 観測サンプル、ソース種別、無効理由、軌道パラメータ、予測結果、無効予測を不変（frozen かつ slots）で定義する
  - 無効予測には**落下地点・落下時刻のフィールドを持たせない**ことで、無効を正常値として読む経路を型で塞ぐ
  - 予測結果に、何サンプル目か・基準となる観測時刻・使用した設定・処理時間を持たせる
  - 距離・時刻・速度のフィールド名に単位を含める。`residual` は単位を名前に持たないが**値は mm** である旨を docstring に明記する
  - ソース種別は記録用メタ情報としてのみ定義し、予測経路の引数には現れないようにする
  - 観測可能な完了状態: 無効予測に落下地点フィールドが存在しないこと、および全型が不変であることを検証するテストが通る
  - _Requirements: 1.1, 3.3, 4.3, 5.3, 6.6, 6.7, 10.4, 10.6_
  - _Depends: 1.2_
  - _Boundary: CoreTypes_

- [x] 1.5 設定オブジェクトと構築時検証を実装する
  - 重力加速度・最小サンプル数・計測有効フラグ・時刻縮退の相対閾値を、導出根拠つきの既定値で公開する
  - 最小サンプル数が3未満、重力加速度が0以下または非有限、相対閾値が範囲外の場合は構築時に拒否する
  - 内部計算用に重力加速度を mm/ms² へ換算した参照手段を提供する
  - 不変かつ値等価にして、予測結果へそのまま同梱できるようにする
  - 観測可能な完了状態: 最小サンプル数 0/1/2 と不正な重力加速度で設定例外が送出され、既定値が 9806.65 / 3 / 有効 であることをテストで固定する
  - _Requirements: 8.3, 10.1, 10.2, 10.3, 10.5_
  - _Depends: 1.2, 1.3_
  - _Boundary: PredictionConfig_

- [x] 1.6 解析的サンプル生成ヘルパをテストツリーに用意する
  - 既知の軌道パラメータと重力加速度から、誤差を含まないサンプル列を生成するヘルパを `tests/prediction_core/analytic.py` に置く
  - 決定的な擬似乱数による誤差重畳と、解析的な落下地点・落下時刻の算出も同ヘルパに置く
  - **パッケージ本体には置かない**（投擲物理・ノイズ生成は `trajectory-simulator` の責務であり、本 Spec の責務外）
  - 以降のタスク 2.1 / 2.2 / 5.2 / 5.3 の完了条件はこのヘルパを前提とする
  - 観測可能な完了状態: 同じ引数で2回生成したサンプル列が完全に一致し、生成元の解析解も同時に取得できる
  - _Requirements: 7.1_
  - _Depends: 1.4, 1.5_
  - _Boundary: tests/prediction_core/analytic.py_

## 2. コア: 軌道推定と床面交点

- [ ] 2. コア: 軌道推定と床面交点

- [x] 2.1 (P) 閉形式最小二乗による軌道推定と残差算出を実装する
  - 時刻をサンプル列の最小値で原点シフトしてから累積し、絶対時刻のまま二乗して桁落ちする事態を避ける
  - 重力加速度が既知であることを利用して z 軸を線形化し、3軸それぞれ2パラメータの単回帰として解く
  - 正規方程式の行列式に**相対閾値**を用いた縮退判定を行い、縮退時は無効理由を返す（例外を送出しない）
  - 残差を「3軸合計の残差二乗和を残差自由度で割った平方根」として算出する。単位は mm
  - 推定速度は mm/s へ換算して軌道パラメータに格納する
  - 累積は時刻昇順の固定順序で行い、演算順序を決定的にする
  - 観測可能な完了状態: 誤差を含まない理想放物線から生成したサンプル列で、推定パラメータが解析解と丸め誤差の範囲で一致し残差が 0 になる
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 6.2, 10.2_
  - _Depends: 1.6_
  - _Boundary: TrajectoryFitter_

- [x] 2.2 (P) 床面との未来側最早交点の算出を実装する
  - 床面は z = 0 固定とし、床平面パラメータを引数に取らない（床平面の推定を責務に含めない）
  - 判別式を評価し、桁落ちに強い形式の根の公式で2根を求める
  - 最新観測時刻より**真に大きい**根のうち最小のものを落下点として選ぶ
  - 実根が無い場合、および全根が最新観測時刻以下の場合は「未来側交点なし」の無効理由を返す
  - 落下時刻は原点シフト分を戻し、入力サンプルと同一の時間基準で返す
  - 観測可能な完了状態: 過去側と未来側に根を持つ上昇中の軌道で未来側の根が選ばれ、判別式が負の軌道では無効理由が返ることをテストで固定する
  - _Requirements: 3.1, 3.2, 3.4, 3.6, 6.3_
  - _Depends: 1.6_
  - _Boundary: ImpactSolver_

## 3. 予測の統合

- [ ] 3. 予測の統合

- [x] 3.1 予測の正常系を統合する
  - 入力を時刻昇順に安定ソートしてから下位へ渡し、並び順が違っても結果が一致するようにする
  - 軌道推定と交点算出を結び、要件が定める8フィールドを備えた予測結果を組み立てる
  - 残り時間を「落下時刻 − 最新観測時刻」として算出する
  - 予測結果直下の速度成分が軌道パラメータの同名フィールドと厳密に一致するようにする
  - サンプル数・基準観測時刻・使用した設定を予測結果に同梱する
  - 引数を「サンプル列と設定」だけに限定し、デバイス固有型・カメラパラメータ・ファイル入出力を受け取らない
  - 観測可能な完了状態: 既知の放物線で8フィールドが揃った予測結果が返り、同一サンプル集合を並び順を変えて渡しても処理時間以外の全フィールドが一致する
  - _Requirements: 1.1, 1.3, 1.4, 3.3, 3.5, 10.6_
  - _Depends: 2.1, 2.2_
  - _Boundary: Predictor_

- [x] 3.2 無効判定5種と判定順序を実装する
  - 入力契約違反・非有限値・サンプル数不足・時刻縮退・未来側交点なしの5理由を実装する
  - 判定順序を契約として固定し、複数条件が同時に成立する入力でも返る理由が決定的になるようにする
  - どの失敗でも例外を送出せず、無効予測を値として返す
  - 無効理由に加えて、人が読める文脈（サンプル数・時刻範囲・判別式の値など）を添える
  - 残差の大小や収束度による採否判定を**行わない**（残差は判断材料として返すにとどめる）
  - 観測可能な完了状態: 5条件を単独および同時に成立させた入力で、契約どおりの理由が返り、いずれも例外にならないことをテストで固定する
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_
  - _Boundary: Predictor_

- [x] 3.3 処理時間の計測と実行時無効化を実装する
  - 単調時計を用いて予測1回の所要時間を ms で算出し、成功・失敗の双方に付与する
  - 設定で計測を無効化したときは、開始時刻の取得自体を行わず未計測として返す
  - ロギング基盤・`logging` モジュールへの依存を持たず、記録・集計・送出を行わない
  - 目標値との比較や充足判定を行わず、計測値の提供にとどめる
  - 観測可能な完了状態: 計測有効時に処理時間が有限の非負値として取得でき、無効時は未計測として返ることをテストで固定する
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  - _Boundary: Predictor_

## 4. Throw Record と逐次予測

- [ ] 4. Throw Record と逐次予測

- [x] 4.1 Throw Record の構造と dict 往復を実装する
  - 1投擲を1レコードとして表す構造を定義し、記録ID・ソース種別・設定・観測サンプル系列・予測結果系列を保持する
  - 予測処理時間は各予測結果が持つ値として扱い、レコード専用フィールドを設けない
  - 予測結果と無効予測を判別するための種別キーを付与し、復元時に直和型を復元できるようにする
  - 非有限値を含む場合も dict 化は成功させ、メモリ上の忠実性を保つ
  - 観測可能な完了状態: dict へ変換して復元したレコードが元のレコードと等価になることをテストで固定する
  - _Requirements: 9.1, 9.2, 9.3_
  - _Depends: 3.3_
  - _Boundary: ThrowRecordCodec_

- [x] 4.2 JSON 直列化とスキーマ拡張規則を実装する
  - dict と JSON 文字列の相互変換を提供する。ファイル・ストレージへの読み書きは行わない
  - 非有限値を許可しない設定で直列化し、規格外の JSON を出力せず例外で拒否する
  - スキーマ版を持たせ、未知のトップレベルキーを退避領域へ保存して再出力する
  - 必須キーの欠落・型不一致は復元時に例外とし、問題のキーをメッセージに含める
  - 観測可能な完了状態: JSON 往復で元のレコードと等価になり、未知キーが往復後も失われず、非有限値を含むレコードの JSON 化が例外になることをテストで固定する
  - _Requirements: 9.3, 9.5, 9.6_
  - _Boundary: ThrowRecordCodec_

- [x] 4.3 Replay と予測系列の同値判定を実装する
  - 記録された観測サンプル系列を**前置列**（1点目、1〜2点目、…、全点）に分け、記録された設定で予測を再実行する
  - 逐次蓄積器に依存せず予測関数だけで再構成する（スキーマ層が蓄積器を引きずり込まないため）
  - 系列の同値判定を提供し、実測値である処理時間を比較対象から除外する
  - 観測可能な完了状態: 記録済みの予測系列と Replay 結果が同値判定で真になり、JSON 往復を挟んだレコードでも成立することをテストで固定する
  - _Requirements: 9.4_
  - _Boundary: ThrowRecordCodec_

- [x] 4.4 逐次予測トラッカーを実装する
  - サンプルを1点ずつ受け取り、そのつど蓄積済みの全点で予測をやり直す
  - 最小サンプル数未満でも常に結果を返し、常に予測系列へ追加する（系列が投擲のタイムラインになる）
  - 観測サンプル系列と予測系列を変更不可の形で公開し、最新結果と最初の有効予測を取得できるようにする
  - 蓄積内容から Throw Record を生成できるようにする
  - 駆動制御・目標座標の送信を行わない
  - 観測可能な完了状態: 1点ずつ追加したとき最小サンプル数の点で最初の有効予測が返り、予測系列の長さがサンプル数と一致し、サンプル数が単調増加することをテストで固定する
  - _Requirements: 4.1, 4.2, 4.3, 5.1, 5.2, 5.3, 5.4_
  - _Depends: 4.1_
  - _Boundary: ThrowPredictionTracker_

## 5. 公開 API と全体検証

- [ ] 5. 公開 API と全体検証

- [x] 5.1 公開 API を確定する
  - パッケージ入口を再エクスポートのみとし、ロジックを持たせない
  - 公開シンボルを明示列挙し、ここに無いものを内部実装として扱う契約を成立させる
  - Throw Record スキーマの単一定義元として、下流 Spec がこの入口から参照できるようにする
  - 観測可能な完了状態: 公開シンボルすべてがパッケージ入口から import でき、明示列挙と実際の公開範囲が一致することをテストで固定する
  - _Requirements: 9.7_
  - _Depends: 4.4_
  - _Boundary: PublicApi_

- [x] 5.2 (P) 解析解との end-to-end 一致を検証する
  - 落下地点と落下時刻が解析的に既知の投擲を複数パターン用意する（水平投射・斜方投射・初期高度の高低）
  - 検証は `tests/prediction_core/test_analytic_e2e.py` に置き、サンプル生成は 1.6 のヘルパを使う
  - 大きな絶対時刻オフセットを与え、落下時刻が入力と同一基準で返ることを確認する
  - 観測可能な完了状態: 全パターンで落下地点・落下時刻が解析値と丸め誤差の範囲で一致し、ハードウェア非接続で完走する
  - _Requirements: 2.3, 3.4, 7.1, 7.2_
  - _Depends: 1.6, 5.1_
  - _Boundary: tests/prediction_core/test_analytic_e2e.py_

- [x] 5.3 (P) サンプル数と予測誤差の評価出力を検証する
  - 検証は `tests/prediction_core/test_error_behavior.py` に置き、1.6 のヘルパで既知の放物線へ決定的な誤差を重畳して、サンプル数を最小値から増やした予測系列を得る
  - 各予測のサンプル数・残差・落下地点誤差が同一系列から取り出せることを確認する
  - **誤差が単調に減少することを合否条件にしない**。検証対象は「評価に必要な出力が揃っていること」に限る
  - 観測可能な完了状態: サンプル数の異なる予測を相互比較できる形で系列から取得できることをテストで固定する
  - _Requirements: 7.3, 7.4_
  - _Depends: 1.6, 5.1_
  - _Boundary: tests/prediction_core/test_error_behavior.py_

- [x] 5.4 (P) 境界と依存ゼロの回帰テストを実装する
  - 検証は `tests/prediction_core/test_boundaries.py` に置き、パッケージ配下の import 文を走査して標準ライブラリの許可リスト外が無いことを検査する
  - 記録層が逐次蓄積層を import していないこと（依存方向の逆流が無いこと）を検査する
  - ファイル入出力・ネットワーク・ロギング基盤への依存が持ち込まれていないことを検査する
  - 観測可能な完了状態: 許可外の import や依存方向の逆流を故意に入れると失敗し、現状のコードでは成功するテストが通る
  - _Requirements: 1.4, 1.5, 7.1, 8.2, 9.5_
  - _Depends: 5.1_
  - _Boundary: tests/prediction_core/test_boundaries.py_

## Implementation Notes

> タスク実行中に判明した、後続タスクにも効く事項を1行ずつ記録する。

- **1.1**: 実行環境はリポジトリが Windows、Python/uv が WSL。すべての Python コマンドは `wsl -e bash -lc 'export PATH="$HOME/.local/bin:$PATH"; cd /mnt/c/Users/user/repos/smart-trashbox-tribute && <cmd>'` の形で実行する。
- **1.1**: WSL 側から `git status` を見ると全ファイルが変更扱いに見える（autocrlf の artifact）。**git の状態判定とコミットは必ず Windows 側の git で行う。**
- **1.1**: `core.autocrlf=true` と WSL ツールの LF 出力が衝突するため `.gitattributes` で `*.py` / `*.toml` / `uv.lock` を `eol=lf` に固定した。`*.md` は既存規約どおり CRLF。
- **1.1**: ビルドバックエンドは `hatchling`（design.md 未指定のため選定）。ビルド時依存のみで実行時依存ゼロは維持。
- **1.1**: `conftest.py` の `default_config` フィクスチャは `pytest.importorskip` で遅延解決している。**タスク 1.5 で `PredictionConfig` を実装したら中身を差し替えること。**
- **1.1**: 足場テスト `tests/prediction_core/test_packaging.py` は依存メタデータのみを検査する。**タスク 5.4 の `test_boundaries.py`（import 文の静的走査）は別途必要で、本テストでは充足されない。**
- **1.2**: 「他モジュールが `1000` / `1e6` を直接書かない」（要件 10.5）は、他モジュール未存在のため 1.2 では検証できず**タスク 5.4 へ申し送り**。`src/prediction_core/**` を走査して裸の換算係数を禁じるガードを 5.4 に含めること。
- **1.3**: `tests/prediction_core/test_config.py` は 1.3（例外階層）と 1.5（設定検証）の共有ファイル。1.3 分は `TestErrorHierarchy` に収めてあるので、**1.5 は末尾に節を追加する形で拡張すること**（既存部の再構成は不可）。
- **1.3**: `test_module_defines_exactly_the_declared_exceptions` が例外型を4種に固定している。**後続タスクは既存の例外型を使うだけで新設しないこと。**新設が必要なら design.md の Errors を先に更新する。
- **1.3**: `errors.py` の docstring に「予測無効は値で返し例外にしない」契約を記載済み。**タスク 3.2（無効判定5種）は例外を送出してはならない**（要件 6.7）。
- **1.4**: `types.py` は宣言専用で `units` を import しない。design.md の CoreTypes 依存表が不正確だったため実態に合わせて修正済み。**換算は TrajectoryFitter / ImpactSolver（タスク 2.1 / 2.2）が行う。**
- **1.4**: `Prediction.config` の型参照は `TYPE_CHECKING` ガード内。**実行時の types → config import を作らないこと**（上向き依存かつ循環）。型検査器は 1.5 完了まで未解決 import を報告するが実行時に影響しない。
- **1.4**: CPython 3.11 では frozen+slots dataclass への**未宣言**属性の setattr は `FrozenInstanceError` でなく `TypeError` になる。不変性のテストは宣言済みフィールドに対して行うこと。
- **1.5**: `gravity_mm_ms2` は `units.mm_per_s2_to_mm_per_ms2` へ委譲する（自前で `/1e6` しない）。委譲テストは戻り値の数値まで見ること（呼び出しの有無だけでは不十分）。
- **1.5**: 依存方向の訂正 — design.md の依存表は **L2 の `config` が L1 の `types` を import してよい**としている（禁止は逆方向。`types` が `config` を実行時 import すること）。以前の申し送りで逆に伝えていたため訂正する。
- **1.6**: `tests/prediction_core/analytic.py` の確定 API（タスク 2.1/2.2/5.2/5.3 が使う）:
  `KnownTrajectory(x0_mm, vx_mm_s, y0_mm, vy_mm_s, z0_mm, vz_mm_s, gravity_mm_s2)` — `.position_at_ms(t_ms) -> (x_mm, y_mm, z_mm)`。
  `generate_samples(trajectory, times_ms) -> list[Sample]`。`add_noise(samples, *, seed, stddev_mm) -> list[Sample]`（`t_ms` は変更しない。ローカル `random.Random(seed)` でグローバル状態を汚染しない）。
  `AnalyticFloorImpact(hit_x_mm, hit_y_mm, hit_time_ms)`。`analytic_floor_impact(trajectory, after_time_ms) -> AnalyticFloorImpact | None`（最も早い未来根、無ければ None）。
- **1.6**: レビュー1巡目で「重力項が t>0 で未検証」「根が2つある場合の最早選択が未検証」の2件が REJECTED。**後続タスクでもオラクル的なテストヘルパを書く際は、単一のハッピーパスだけでなく分岐（複数根・境界値）を実際に作るフィクスチャで検証すること。**
- **2.1**: `TrajectoryParameters` の `x0_mm`/`y0_mm`/`z0_mm`/`estimated_v*_mm_s` は **`t_ref_ms`（サンプル最小時刻）時点の値**であり、`t=0` 時点の値ではない。`tests/prediction_core/analytic.py` の `KnownTrajectory` は `t=0` 基準なので、テストで比較する際は `KnownTrajectory.position_at_ms(t_ref_ms)` で評価してから比較すること。**混同すると本番コードではなくテスト側が壊れる**（2.1 実装時に一度混同しかけた）。
- **2.2**: `units.py` に mm/s → 内部 mm/ms の逆変換が存在しないため、`impact.py` は**秒単位で直接計算**する（`trajectory.estimated_v*_mm_s` と `gravity_mm_s2` を外部単位のまま使い、ms↔s の境界のみ `units.MS_PER_S` で変換）。`fitting.py` の内部 mm/ms 方式とは異なるが、両者は同一階層で独立しており import し合わないため問題ない。
- **2.2**: `solve_floor_impact` は bare な `InvalidReason` を返すのみ。design.md が言う「detail 文字列での区別」は**タスク 3.2（Predictor）の責務**（`InvalidPrediction.detail` の構築時）。
- **2.2**: タスク文の2シナリオ（過去/未来根・判別式負）はどちらも未来根が1つしかなく、根の選択ロジック（min/max）や境界（`>` vs `>=`）を検証できない。**「複数の未来根を持つフィクスチャ」を明示的に作ること**（タスク 1.6 の教訓と同型）。
- **3.1**: `predict()` は現時点で **ハッピーパス＋2種の無効理由（DEGENERATE_TIME・NO_FUTURE_FLOOR_CROSSING）の素通しのみ**。`MALFORMED_INPUT`・`NON_FINITE_VALUE`（入力/出力とも）・`INSUFFICIENT_SAMPLES` は**未実装**（3.2 の担当）。`elapsed_ms` は無条件で `None`、`config.measure_elapsed` は未参照（3.3 の担当）。制御フローは早期リターンの線形列。**3.2 は前段に検証ステップを追加し、後段に出力有限性チェックを追加する形で拡張すること**（構造の作り直しは不要）。
- **3.2**: `predict()` に6段階の検証順序を実装済み（1.MALFORMED_INPUT 2.NON_FINITE_VALUE入力 3.INSUFFICIENT_SAMPLES 4.DEGENERATE_TIME 5.NO_FUTURE_FLOOR_CROSSING 6.NON_FINITE_VALUE出力）。1〜3 は既存パイプラインの前段で短絡、6 は `Prediction` 組み立て後・return 前に実行。
- **3.2**: `MALFORMED_INPUT` は `based_on_time_ms=None` 固定（型不正な要素への `.t_ms` アクセスを避けるため）。他の早期リターンは `max((s.t_ms for s in samples), default=None)` で空リストのクラッシュを防ぐ。
- **3.2**: 出力有限性チェック（ステップ6）は `vz_mm_s=1e160` のような極端な有限入力で `(-v)*(-v)` が`OverflowError` を送出せず静かに `inf` になる（`**` とは挙動が異なる）ことを利用して実際にオーバーフローを起こし検証した。モンキーパッチに頼らず本番パイプラインを通した実例。
- **3.2**: レビューで2点の非ブロッキングな検証ギャップが見つかった —(a) ステップ6は `trajectory` のネストしたフィールドも含むが、`gravity_mm_s2` は `PredictionConfig` の構築時検証で常に有限のため、この部分だけが壊れるケースは現行パイプラインでは自然に構成できない。(b) ステップ2の非有限値検査は t_ms 以外のフィールド単体を検証する専用テストが無く、ステップ6のセーフティネット経由でも同じ reason になるため見かけ上は通ってしまう。**将来この2つを独立に切り分けるテストを追加する余地がある。**
- **3.3**: `predict()` の返り値構築点は**7箇所**（MALFORMED_INPUT・NON_FINITE_VALUE入力・INSUFFICIENT_SAMPLES・DEGENERATE_TIME・NO_FUTURE_FLOOR_CROSSING・NON_FINITE_VALUE出力・成功時 Prediction）。各所で `_elapsed_ms(start_ns)` を**1回だけ**呼ぶこと。レビュー1巡目で「ステップ6（出力有限性）が候補オブジェクトを構築してから捨てて再構築するため計測が2回走る」欠陥が見つかった。**有限性チェックは最終オブジェクトを組み立てる前に生の値（`impact`/`fit_result`）に対して行い、最終オブジェクトは1回だけ構築すること。**
- **3.3**: `config.measure_elapsed=False` 時は `time.perf_counter_ns()` を**一度も呼ばない**（結果を捨てるのではなく取得自体をスキップする、要件8.3）。呼び出し回数のスパイでのみ機械的に証明できる。
- **3.3**: 処理時間の妥当性テストは、実測値が数十マイクロ秒オーダーのため**単純な上限値チェックでは単位バグ（/1e6 を /1e3 等に変える）を検出できない**。外部の壁時計計測で `predict()` 呼び出しを挟み込み、内部計測値がその区間に収まることを確認する形のテストが必要。
- **3.3**: `predict()` の実装が完了。これで Predictor（タスク3.1/3.2/3.3）が完成した。
- **4.1**: `src/prediction_core/record.py`（L5）は `types`/`config`/`errors` のみを import する。`predictor.py`（Replay に必要）は**タスク4.3で追加**するまで import しない。**`record.py`/`tests/prediction_core/test_record.py` は 4.1/4.2/4.3 の共有ファイル。** 4.1 で実装したのは `ThrowRecord` 本体・`to_dict`/`from_dict` のみ（`to_json`/`from_json`/`replay`/`predictions_equivalent` は未実装・スタブなし）。dict 変換は `dataclasses.asdict()` を使わず手動で書いた（`kind` 判別キーの付与、`StrEnum.value` への明示変換、ネストした `PredictionConfig`/`TrajectoryParameters` の個別変換が必要なため）。
- **4.1**: レビューで「実装は正しいが、その正しさを固定するテストが無い」という2件の指摘（Important、共にREJECTED理由）があった。(a) `InvalidPrediction` の dict 形に禁止フィールド（`predicted_hit_x_mm` 等）を注入しても既存テストが検知しなかった → キー集合の**完全一致**（部分一致ではなく）を検証するテストが必要。(b) `from_dict` の `extra=dict(data.get("extra", {}))` を参照コピーに変えても検知しなかった → `from_dict` 呼び出し**後**に入力 dict 側の `extra` を mutate し、復元済みオブジェクトが影響を受けないことを検証するテストが必要。**後続タスク（4.2 の未知トップレベルキー退避、Tracker/Record の他の不変フィールド）でも「型としては正しいが往復テストでは見えない」契約は個別に固定すること。**
- **4.1**: NaN/Infinity を含む `ThrowRecord` を `from_dict(to_dict(r)) == r` で検証すると `NaN != NaN` のため必ず失敗する。非有限値のテストは個別フィールドを `math.isnan`/`math.isinf` で確認し、往復等価性テストは有限値のみのレコードで行うこと。
- **4.2**: `to_json`/`from_json`・必須キー/型検証（`RecordSchemaError`、メッセージにキー名を含む）・未知トップレベルキーの `extra` への退避を実装（152→172件）。`from_json` は独自の検証経路を持たず `from_dict` に完全委譲するため、検証ロジックの単一定義元が保たれている。非有限値は `to_dict()` では引き続き成功し、`to_json()` のみ `RecordSerializationError`（`json.dumps(allow_nan=False)` の `ValueError` を変換）で拒否する。
- **4.2**: レビューで承認されたが、非ブロッキングの申し送り2件。(a) 未知トップレベルキーと明示的な `extra` キーが同名衝突した場合、現状は未知キー側が勝つ実装だが、この挙動を固定するテストが無い（design.md も優先順位を規定していない）。(b) `ensure_ascii=False` の非ASCII文字（日本語の `record_id` 等）での往復を確認するテストが無い。**task 4.3 またはそれ以降でこれらのテストを追加する余地がある（ブロッキングではない）。**
- **4.3**: `record.py` が `predictor.py`（L4）を初めて import した（design.md 依存表どおり L5 は 0〜4層を import 可）。`tracker.py`（L6、まだ存在しない）には依存しない。`replay` は `record.samples[: i + 1]`（i は0始まり）の前置列に `predict(prefix, record.config)` を適用するだけで、`ThrowPredictionTracker` を一切使わない。`predict()` が入力を `t_ms` 昇順に安定ソートするため、前置列を事前にソートし直す必要はない。
- **4.3**: `predictions_equivalent` は「長さ一致 → 各要素の型一致（`type(l) is not type(r)`）→ `dataclasses.replace(obj, elapsed_ms=None)` で揃えてから `==`」の3段階。**ミューテーションテストで判明した点**: 型チェック(`type(l) is not type(r)`)を削除しても既存の全テストは通り続けた。理由は `@dataclass` の自動生成 `__eq__` が `other.__class__ is self.__class__` を内部で既にチェックしており、`Prediction != InvalidPrediction` は型チェックを明示しなくても常に真になるため。したがって型チェック行は**契約を明文化する目的のためだけに残した安全な冗長コード**であり、テストでは独立に検出できない（`Prediction`/`InvalidPrediction` のフィールド構成が偶然一致することがない限り原理的に検出不能）。後続タスクで同種の直和型を比較するコードを書く際、この「dataclass の型チェックはテストで検出できない」という性質を踏まえること。
- **4.3**: `tests/prediction_core/test_replay.py` の `_build_record` は `ThrowPredictionTracker` を使わず、`predict()` を前置列へ手動適用して `ThrowRecord` を組み立てている。5点のサンプル（`min_samples=3` の既定設定）を使うことで、先頭2件が `InsufficientSamples`、残り3件が有効な `Prediction` という混在系列を自然に作れる。**4.4（Tracker 実装）でも同じ5点パターンが使い回せる可能性がある。**
- **4.4**: `src/prediction_core/tracker.py`（L6）は `config`/`predictor`/`record`/`types` のみを import する。`add_sample` は毎回 `tuple(self._samples)`（蓄積済み全点）で `predict()` を呼び直す（スライディングウィンドウ・部分和は持たない、design.mdの明示的な禁止どおり）。`samples`/`predictions` プロパティは呼び出しごとに新しい `tuple(...)` を返すため、外部からの変更が内部状態に伝播しない。`add_sample` の戻り値は `self._predictions` へ追加したオブジェクトそのもの（`is` 同一性が成立）。
- **4.4**: `record_id` が空文字列の場合は素の `ValueError` を送出する（`errors.py` の4例外は新設しない方針を踏襲。4例外はいずれも `ValueError` を継承しているため呼び出し側の `except ValueError` 網と矛盾しない）。`first_valid` はキャッシュせず毎回 `predictions` を先頭から線形走査する（design.mdが専用フラグ・経路を作らないよう明示しているため）。
- **4.4**: `ThrowPredictionTracker` の公開インターフェースは `add_sample`/`samples`/`predictions`/`latest`/`first_valid`/`to_record` の6つのみ（要件5.4、駆動制御・送信メソッドなし）。~~5.1（公開API確定）でこの6つを `prediction_core` の `__all__` に含めること。~~ **（5.1での訂正）** この6つはインスタンスメソッド/プロパティであり、`__all__` に含める対象は `ThrowPredictionTracker` クラス自体（design.mdの18シンボル列挙どおり）。この6つは同クラスを公開すれば自動的にアクセス可能になる。
- **5.1**: `src/prediction_core/__init__.py` を18シンボル（design.md PublicApi節の列挙どおり）の明示 re-export のみに実装。ロジック無し（AST検査でも固定）。**この変更により `prediction_core.types` を import すると `__init__.py` 経由で全モジュールが `sys.modules` に載るようになった。** task 1.4 由来の `test_importing_types_does_not_import_config_at_runtime`（サブプロセスで `sys.modules` を検査する方式）はこの影響で常に失敗する状態になったため、`importlib.util.spec_from_file_location` で `types.py` を単体ファイルとしてパッケージ機構を経由せず直接ロードする方式に書き換えた（`types.py` 自体は無変更）。**後続タスク（5.4の境界回帰テスト等）でも同様に、`__init__.py` 経由の import では個別モジュールの実行時依存を独立検証できない点に注意すること。**
- **5.2/5.3/5.4**: 3タスクとも設計時から1タスク1ファイルに分離されており（design.mdの明示的な並行実装安全性の配慮）、実際に並列実装・並列レビューを衝突なく完走できた（209→221→263件）。いずれもプロダクションコード（`src/prediction_core/**`）は無変更。
- **5.2**: `test_analytic_e2e.py` は6パターン（水平投射・斜方投射上向き/下向き・高高度/低高度・絶対時刻オフセット1e9ms）。許容誤差は位置 `abs_tol=1e-6`mm・時刻 `abs_tol=1e-6`ms（実測誤差は`1e-13`〜`1e-15`程度で、緩すぎず厳しすぎない値であることをレビューで確認済み）。残差閾値による合否判定は使っていない。
- **5.3**: `test_error_behavior.py` は `ThrowPredictionTracker` で決定的ノイズ（`add_noise(seed=..., stddev_mm=3.0)`）を1点ずつ蓄積し、`[(sample_count, residual, hit_error_mm), ...]` の系列を検証。**誤差の単調減少は一切アサートしない**（design.md開発標準1の明示的禁止）。レビューで判明した点: このテストは `predicted_hit_x_mm`/`predicted_hit_y_mm` の値そのものの物理的正しさ（解析解との一致）は検出できない（意図的なスコープ限定。それは5.2の責務）。
- **5.4**: `test_boundaries.py`（34件）は `ast` による静的解析のみで `import prediction_core` を一切行わない（`__init__.py` 経由だと個別モジュールの実行時依存を独立検証できないため）。既存コードに `predictor.py:63` の `1e6`（`perf_counter_ns` のns→ms変換、`units.py` が管轄するmm/msドメインとは別関心事）が唯一の例外として行番号ピン留めで許容されている。この例外が悪用されていないか（別行への新規裸リテラル混入を検出できるか）をレビューで独立検証済み。
