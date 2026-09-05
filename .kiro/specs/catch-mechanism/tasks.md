# Implementation Plan

> 本計画は `requirements.md`（要件1〜10 / 受入基準74件）と `design.md` に基づく。
> **中核層（パラメータ・選定・導出・制約・指標）は標準ライブラリのみで動く。**
> 形状ライブラリ（build123d）は `[project.optional-dependencies].cad` の任意依存とし、
> **`src/prediction_core/` と `src/trajectory_sim/` の実装コードには一切触れない。**
> シミュレータへの還元は `configs/trajectory_sim/*.json` の**値の更新のみ**で行う。
> ⚠️ **ゴミ箱の実物購入・採寸を要するのはタスク 5.2 のみ**であり、それ以外は公称値を仮値として完了・検証できる。

## 1. 基盤: パッケージ骨組みと依存境界

- [x] 1. 基盤: パッケージ骨組みと依存境界

- [x] 1.1 パッケージを追加し、形状ライブラリを任意依存として隔離する
  - 既存の `pyproject.toml` の wheel 対象へ新パッケージを追加する。**`[project] dependencies` は空のまま変更しない**
  - 形状ライブラリを `[project.optional-dependencies]` の `cad` として宣言し、既定でインストールされない状態にする
  - 上流の許可リスト（`ALLOWED_OPTIONAL_EXTRAS`）へ extras 名を**1行だけ**登録する。
    ⚠️ **登録先は2箇所ある。片方だけ直すと必ずテストが落ちる**:
    `tests/prediction_core/test_packaging.py` と
    `tests/sensing_foundation/test_sensing_boundaries.py`
    （`tests/` に `__init__.py` を置けず定数を import できないため意図的に複製されている。
    ⚠️ **この複製の解消は本 Spec の境界外**であり、両方に同じ1行を足すだけに留める）
    ⚠️ **不変条件の表現・主張は変更しない。`test_boundaries.py` / `test_trajectory_sim_boundaries.py` には触れない**
  - `src` レイアウトでパッケージの入口ファイルと、テストツリーの器を作る
  - 観測可能な完了状態: 形状ライブラリを入れていない環境で `python -m pytest` が成功終了し、
    新パッケージが import でき、**上流 `tests/prediction_core/` と `tests/sensing_foundation/` の
    両方**の全テストが引き続き通る
  - _Requirements: 5.1, 5.4, 5.6, 5.7_

- [x] 1.2 例外階層を定義する
  - 基底例外を `ValueError` の派生として定義し、パラメータ不正・選定不正・形状不正・整合不一致・
    形状環境不在の5系統を用意する
  - 「評価結果（不適合・不一致）は値で返し、呼び出し方の誤りだけを例外にする」区分をこの階層で表現する
  - 観測可能な完了状態: 各例外が基底例外としても `ValueError` としても捕捉できることを固定するテストが通る
  - _Requirements: 1.3, 1.4, 5.3_
  - _Boundary: Errors_

- [x] 1.3 寸法パラメータの型と構築時検証を実装する
  - ゴミ箱の採寸値・対象物・造形制約・継手方針・受け口・保持方針を、不変（frozen かつ slots）な
    データクラスとして定義し、集約する型を1つ置く
  - **実物の寸法に既定値を与えない。** 省略した構築は失敗させる（設計上の選択値のみ既定値を持つ）
  - 出所を**実測 / 仮値の2値**で定義し、導出量が「入力の最弱を継承する」規則を関数として実装する。
    ⚠️ **第3の値を作らない**（下流の設定ファイルと値集合を一致させるため）
  - 許可材料の一覧を定義し、一覧に無い材料の指定を構築時に拒否する
  - パラメータパス表をデータクラス木の走査で生成し、手書きの表を二重管理しない
  - 底の平面部径・底の外径・開口内径の大小関係、正値性、角度範囲を構築時に検証し、違反フィールド名と値を示す
  - 観測可能な完了状態: 実物寸法を省略した構築・許可外材料・逆転した径の組み合わせが拒否され、
    「実測＋仮値の導出は仮値」「実測のみの導出は実測」となることをテストで固定する
  - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.8, 2.1, 2.5, 2.6, 8.6, 9.4, 10.1_
  - _Depends: 1.2_
  - _Boundary: Params_

- [x] 1.4 寸法設定ファイルの読み書きとパラメータ識別子を実装する
  - 寸法パラメータの単一の正となる設定ファイルを追加し、第一候補の公称値を**すべて仮値として**記録する
  - あらゆる階層で未知キーを拒否し、欠損・範囲外を項目名つきで拒否する読み込みを実装する
  - 出所表のキーがパラメータパス表と一致することを検証し、一致しないキーを拒否する
  - 正規化した表現に対する識別子（ハッシュ）を算出する関数を実装する
  - 書き出しは行単位の差分が読める整形（インデント・キー整列・末尾改行）で行う
  - 観測可能な完了状態: 未知キーを1つ足した設定が項目名つきで拒否され、読み込み → 書き出し → 読み込みで
    値と出所が保存され、同じ値なら書式に依らず識別子が一致することをテストで固定する
  - _Requirements: 1.1, 1.3, 1.6, 1.7, 4.5, 6.6, 6.7_
  - _Depends: 1.3_
  - _Boundary: Config_

- [x] 1.5 (P) リポジトリ設定を整備し、外部 CAD の作業ファイルを成果物から外す
  - 外部 CAD の作業ファイルをバージョン管理の対象外にする
  - 生成物の出力先が既にバージョン管理外であることを確認し、必要なら出力先の規約を明示する
  - 生成物形式が誤ってコミットされた場合にも内容が壊れないよう、改行変換の対象から外す
  - 観測可能な完了状態: 外部 CAD の作業ファイルと生成物を出力先へ置いた状態で、
    バージョン管理の未追跡ファイル一覧に現れないことを確認できる
  - _Requirements: 3.5, 4.6, 4.7_
  - _Boundary: Repository Settings_

- [x] 1.6 依存境界の静的検査を実装する
  - 自パッケージのソースを静的に走査し、形状ライブラリの import が形状構築と書き出しの2モジュールに
    限られることを検査する
  - パッケージ入口から形状ライブラリへ到達しないことを検査する
  - 上流パッケージ（予測コア・シミュレータ）への import が存在しないことを検査する
  - 依存方向（左の層からのみ import する）に反する内部の辺が無いことを検査する
  - 観測可能な完了状態: 違反を含む架空のソース文字列を検査関数へ渡すと失敗し、現状のツリーでは成功する
    テストが、形状ライブラリ非導入の環境で通る
  - _Requirements: 5.2, 5.5, 5.7, 9.3_
  - _Depends: 1.1_
  - _Boundary: Boundary Tests_

## 2. 中核: 制約・選定・導出・指標

- [x] 2. 中核: 制約・選定・導出・指標

- [x] 2.1 (P) 造形制約の検査と分割数の導出を実装する
  - 造形可能寸法に対する外接箱の検査を実装し、超過している軸と超過量を返す
  - 円環を等分した扇形の弦長から、造形可能寸法に収まる**最小の分割数を導出**する。
    ⚠️ **斜め配置（正方形の対角線）を仮定しない。軸並行の外接箱で判定する**
  - 現実的な上限までに収まる分割数が存在しない場合は例外で拒否する
  - 材料の許可検査と、継手の当たり面の下限検査を実装する
  - 切削加工を要する形状を扱わない方針を、検査の対象範囲としてコメントで明示する
  - 観測可能な完了状態: 外径を増やすと導出される分割数が単調に増え、導出した分割数での扇形が
    造形可能寸法に収まり、収まらない外径では拒否されることをテストで固定する
  - _Requirements: 2.2, 2.3, 2.4, 2.7, 2.8, 8.3_
  - _Depends: 1.3_
  - _Boundary: Constraints_

- [x] 2.2 (P) 選定基準と候補判定を実装する
  - 選定基準のしきい値（形状・開口内径の下限と拒否値・高さ・重量・テーパー上限・価格帯・蓋の有無）を
    設定ファイルとして追加する。⚠️ **基準は roadmap を正とし、推測で書き換えない**
  - 候補の諸元を設定ファイルとして追加する（第一候補・次点・非推奨例を含む）
  - 全項目を評価し、**最初の不適合で打ち切らずに**不適合項目の一覧を返す判定を実装する
  - 望ましいが必須でない項目（外向きリム）は警告として返し、適合判定を左右させない
  - 観測可能な完了状態: 第一候補が適合し、強テーパー品・開口内径が下限未満の品・蓋付きの品がそれぞれ
    不適合となり、理由の項目名が返ることをテストで固定する
  - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - _Depends: 1.4_
  - _Boundary: Selection_

- [x] 2.3 (P) 位置許容誤差の導出と導出記録の直列化を実装する
  - 「開口内半径 − 対象物の代表寸法の半分」を**唯一の導出箇所**として実装する
  - ⚠️ **外向きに張り出す部分の寸法を算入しない。** フランジ幅を変えても導出値が動かないこと
  - 導出結果に、入力の値と出所、導出式の文字列、前提（対象物は M1 の実験条件である空き缶、
    外向き部分の非算入）を併記する
  - 出所は入力の最弱を継承させる
  - 導出記録を設定ファイルとして書き出す・読み戻す関数を実装する
  - 観測可能な完了状態: 公称 φ220・φ65 に対する導出値と、その出所が仮値になること、
    フランジ幅の変更で値が変わらないことをテストで固定する
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.9_
  - _Depends: 1.4_
  - _Boundary: Tolerance_

- [x] 2.4 (P) 形状指標の型・記録・照合を実装する
  - 部品名・体積・境界箱・立体数からなる指標の型を、形状オブジェクトを保持しない素の数値として定義する
  - 記録ファイルの読み書きを実装し、記録時のパラメータ識別子・許容差（体積の相対・境界箱の絶対）・
    生成ライブラリ版を含める
  - 照合を実装し、部品名・項目名・双方の値を含む不一致の一覧を返す。
    記録にあって再生成に無い部品、およびその逆も不一致として報告する
  - 体積と材料密度から質量の目安を算出する関数を実装する
  - 観測可能な完了状態: 記録された指標を人為的にずらすと部品名と双方の値を伴う不一致が返り、
    一致時は空の一覧が返ることを、形状ライブラリ非導入の環境で通るテストとして固定する
  - _Requirements: 4.1, 4.2, 4.4, 8.7_
  - _Depends: 1.4_
  - _Boundary: Metrics_

## 3. CAD 層: 受け口の形状と生成物

- [x] 3. CAD 層: 受け口の形状と生成物

- [x] 3.1 受け口の幾何を寸法パラメータから導出する
  - 取り付け部の内径を「上端外径 ＋ 隙間の2倍」から導出する
  - フランジの内周径を**ゴミ箱の開口内径以上**とし、通過できる最小径を明示的に返す
  - 外径を「取り付け部＋フランジ幅」から導出し、造形制約の検査へ渡す
  - 形状を構築せずに評価できる関数として実装する（不変条件の検査を軽量に保つため）
  - 観測可能な完了状態: 採寸値を変えると取り付け部内径と外径が追随し、通過できる最小径が
    常に開口内径以上であることをテストで固定する
  - _Requirements: 8.1, 8.2, 8.5, 8.6_
  - _Depends: 2.1_
  - _Boundary: Shapes_

- [x] 3.2 ワイドリムのセグメント形状を構築する
  - 導出された分割数で扇形セグメントを構築し、外周が高く内周が低い緩やかな傾斜を与える。
    ⚠️ **内向きの漏斗を作らない**（design.md「受け口形状の決定」の決定1）
  - セグメント端面に貫通ボルト穴と金属インサート座を設け、位置決め用のダボは荷重を受けない形で置く
  - 後付け部品用の締結座を、保持方針のパラメータで指定された数だけ設ける
  - 参照解決は幾何セレクタ（位置・向きによる選択）で明示的に書き、生成名に依存しない
  - ⚠️ **本タスク以降の検証には任意依存 `cad` の導入が要る**（宣言は 1.1 で完了しており、追加の宣言作業は無い）
  - 造形向き（リム面を寝かせる）を前提とし、接合面の法線が層法線と一致しない配置であることを注記する
  - 観測可能な完了状態: 形状ライブラリを導入した環境で全セグメントが構築でき、
    セグメント数が導出値と一致し、締結座の数が指定どおりであることをテストで固定する
  - _Requirements: 2.6, 3.1, 8.3, 8.4, 9.7_
  - _Depends: 3.1_
  - _Boundary: Shapes_

- [x] 3.3 形状指標の抽出と決定性を実装する
  - 構築した部品から体積・境界箱・立体数を抽出し、中核層の指標型へ変換する
  - 抽出は形状オブジェクトを中核層へ渡さない形で行う
  - 観測可能な完了状態: 同一パラメータから2回構築した部品の指標が完全に一致することをテストで固定する
  - _Requirements: 3.7, 4.1_
  - _Depends: 3.2_
  - _Boundary: Shapes_

- [x] 3.4 生成物の原子的な書き出しを実装する
  - 組立確認・図面化に用いる中間形式と、造形に用いるメッシュ形式2種を、単位ミリメートルで書き出す
  - 一時ディレクトリへ全ファイルを書き終えてから出力先へ移し、失敗時は何も残さない
  - 出力先の既定をバージョン管理外の場所とする
  - 書き出し前に造形制約の検査を実行し、違反があれば書き出しを行わない
  - 観測可能な完了状態: 出力先に3形式のファイルが生成され、書き出し途中で失敗させた場合に
    出力先へ部分的なファイルが残らないことをテストで固定する
  - _Requirements: 2.7, 3.3, 3.4, 3.5, 3.6_
  - _Depends: 3.3_
  - _Boundary: Export_

## 4. 入口と検査

- [x] 4. 入口と検査

- [x] 4.1 コマンド入口を実装する
  - 生成・照合・選定・許容誤差導出の4サブコマンドを実装する
  - 形状を要するサブコマンドでは形状ライブラリを関数内で遅延 import し、
    未導入時は専用の失敗として扱う。⚠️ **成功として黙って読み飛ばさない**
  - 終了コードを、正常 / 検査の不一致 / 入力の誤り / 形状環境の不在 で区別する
  - 照合には形状を再生成しない識別子のみの検査を選べる切り替えを設ける
  - 観測可能な完了状態: 形状ライブラリ非導入の環境で、選定・許容誤差導出・識別子のみの照合が
    正常終了し、生成の要求が専用の終了コードで失敗することを確認できる
  - _Requirements: 3.2, 4.3, 5.3, 6.2, 6.7, 7.5_
  - _Depends: 2.2, 2.3, 2.4, 3.4_
  - _Boundary: Cli_

- [x] 4.2 形状指標の記録を初期化し、照合の回帰テストを追加する
  - 現在のパラメータと実装から生成した指標を記録ファイルへ書き出し、パラメータ識別子とライブラリ版を含める
  - 記録を明示的に更新する切り替えを用意し、既定では不一致を失敗として扱う
  - 記録済み指標をずらした状態で照合が失敗し、部品名と双方の値が示されることを検証する
  - 同一パラメータからの2回生成が同一指標になることを検証する
  - 観測可能な完了状態: 形状ライブラリ導入環境で照合が成功し、指標を人為的にずらすと失敗する
    テストが通る。非導入環境では理由が明示されたうえで当該テストのみが実行対象外になる
  - _Requirements: 3.7, 4.2, 4.3, 4.4_
  - _Depends: 4.1_
  - _Boundary: Metrics, Cli_

- [x] 4.3 (P) パラメータ変更と指標記録の不整合検出を追加する
  - 記録に含まれるパラメータ識別子が、現在の寸法設定ファイルの識別子と一致することを検査する
  - この検査が**形状ライブラリ非導入の環境でも実行される**ことを保証する
  - 観測可能な完了状態: 寸法設定ファイルを変更して記録を更新しない状態で、
    形状ライブラリを入れていない環境でも検査が失敗することをテストで固定する
  - _Requirements: 4.5, 5.7_
  - _Depends: 4.2_
  - _Boundary: Metrics_

- [x] 4.4 (P) 受け口の不変条件テストを追加する
  - 通過できる最小径が常に開口内径以上であること（開口を狭めない）を検証する
  - 採寸値の変更に取り付け部の寸法が追随することを検証する
  - 各セグメントの外接箱が造形可能寸法に収まることを検証する
  - 後付け用の締結座の数、追加の深さが 0 であること、底へ加工を行わない指定であることを検証する
  - ⚠️ **これらのしきい値が「設計の自己整合性」の検査であり、プロジェクトの合否条件ではない**旨を
    テストの説明に明記する
  - 観測可能な完了状態: 上記5点を検証するテストが形状ライブラリ導入環境で通り、
    開口を狭める値へ変更すると失敗する
  - _Requirements: 8.2, 8.3, 9.4, 9.6, 9.7_
  - _Depends: 4.2_
  - _Boundary: Shapes, Params_

## 5. 受け口の確定: 選定・採寸・還元

- [ ] 5. 受け口の確定: 選定・採寸・還元

- [ ] 5.1 候補を評価して機種を選定し、選定結果を記録する（OQ-08 の決着）
  - 候補評価を実行し、適合した候補と不適合の理由を得る
  - 選定した機種を識別できる情報（品名・型番・JAN 等）と、選定の根拠となった基準項目を記録する
  - 再調達性（同一品が別ルートで入手できること）を選定記録に含める
  - 観測可能な完了状態: 選定コマンドの出力に第一候補の適合と、非推奨例の不適合理由が現れ、
    選定結果が寸法設定ファイルの機種識別情報と一致する
  - _Requirements: 6.1, 6.2, 6.8_
  - _Depends: 2.2_
  - _Boundary: Selection_

- [ ] 5.2 実物を採寸し、寸法設定ファイルへ反映する
  - ⚠️ **本計画で唯一、実物の購入を要するタスクである**（他タスクは仮値のまま完了できる）
  - 採寸項目（開口内径・上端外径・底の外径・底の平面部径・高さ・実測重量・底の肉厚・テーパー角）を測る。
    ⚠️ **開口内径は外径ではなく内径を測る**（縁の巻き込み分を引く）
  - 測定値を寸法設定ファイルへ書き込み、該当する値の出所を実測へ更新する
  - 実装コードを変更せずに反映が完了することを確認する
  - 観測可能な完了状態: 採寸値を反映した状態で全テストが通り、出所が実測となった項目が
    設定ファイルの差分として行単位で読め、形状の再生成が新しい寸法に追随する
  - _Requirements: 1.6, 6.5, 6.6, 6.7_
  - _Depends: 4.2, 5.1_
  - _Boundary: Config_

- [ ] 5.3 位置許容誤差を確定し、導出記録を更新する
  - 採寸値（開口内径・対象物の実測径）から位置許容誤差を導出し、導出記録を更新する
  - 入力がすべて実測である場合に出所が実測となることを確認する
  - 対象物が M1 の実験条件である空き缶であること、外向き部分を算入していないことを前提として記録する
  - 観測可能な完了状態: 導出記録の値・入力・出所・前提が更新され、
    再導出した値と記録が一致することをテストで固定する
  - _Requirements: 1.5, 7.1, 7.3, 7.4, 7.5, 7.9_
  - _Depends: 5.2_
  - _Boundary: Tolerance_

- [ ] 5.4 シミュレータ設定へ還元し、整合検査を追加する
  - 実行可能なシミュレータ設定の該当項目へ、導出した位置許容誤差と出所を反映する。
    ⚠️ **値の更新のみ。スキーマ・キー構造・実装コードには触れない**
  - 設定に記録された値と導出記録の値・出所が一致することを検査するテストを追加し、
    不一致時に双方の値と参照元を示す
  - 観測可能な完了状態: 設定側の値を意図的にずらすと検査が失敗し、双方の値と参照元が示される。
    シミュレータの実行可能な設定でシミュレータが従来どおり動作する
  - _Requirements: 7.5, 7.6, 7.7, 7.8_
  - _Depends: 5.3_
  - _Boundary: Config, trajectory_sim configs_

## 6. 統合: 下流契約と通し検証

- [ ] 6. 統合: 下流契約と通し検証

- [ ] 6.1 下流が消費する公開契約を確定し、契約テストを追加する
  - パッケージ入口の公開シンボルを確定し、形状ライブラリを import しない状態に保つ
  - ゴミ箱の底の外径・底の平面部径・テーパー角・高さ・実測重量、造形制約、継手方針を
    出所つきで取得できることを検証する
  - 形状ライブラリ非導入の環境で公開入口を import できることを検証する
  - 公開項目の意味・単位・構造の変更が再検証を要する変更であることを、設計の該当箇所と対応付けて記録する
  - 下流の部品（駆動ベース・固定アダプタ・トレイ・整備スタンド）を扱わないことを、
    公開シンボルの範囲として表現する
  - 観測可能な完了状態: 形状ライブラリを入れていない環境で契約テストが通り、
    公開シンボルの一覧が設計の記述と一致する
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_
  - _Depends: 5.4_
  - _Boundary: PublicApi_

- [ ] 6.2 通しで再生成・照合・還元の一貫性を検証する
  - 生成 → 照合 → 許容誤差導出 → 整合検査を通しで実行し、すべて正常終了することを確認する
  - 保持方針（追加の深さを持たない・底に加工を行わない・後付け締結座を残す）の数値的帰結が、
    パラメータと不変条件の検査によって固定されていることを確認する
  - 跳ね返りが評価対象外であるため、シミュレータの出力を保持の根拠として用いていないことを、
    依存の不在（上流パッケージへの import が無いこと）として確認する
  - 緩衝材の材質選定を本計画で扱っていないこと（後から貼れる平面と締結座のみを担保していること）を確認する
  - 観測可能な完了状態: 形状ライブラリ導入環境で全テストが通り、非導入環境でも形状生成を除く
    全テストが通る。両環境での実行結果が終了コードで区別できる
  - _Requirements: 2.7, 3.2, 5.7, 9.1, 9.2, 9.3, 9.5_
  - _Depends: 6.1_
  - _Boundary: 統合_

## Implementation Notes

- **タスク1.1 / 後続への申し送り**: design.md `### Directory Structure` が挙げる `tests/catch_mechanism/` 配下の
  `test_boundaries.py`（1.6）・`test_config.py`（1.4）・`test_metrics.py`（2.4）・`test_errors.py`（1.2）は、既存の
  `tests/prediction_core/test_boundaries.py` / `test_config.py` / `tests/sensing_foundation/test_metrics.py` / `test_errors.py`
  と**ベース名が衝突する**。`tests/` 配下に `__init__.py` が無く `--import-mode` 指定も無い（既定の prepend）ため、
  ⚠️ **design の名前をそのまま使うと pytest の収集時に落ちる。`test_catch_*.py` のような接頭辞付き命名を使うこと**
  （1.1 は `test_catch_packaging.py` を採用）。
- **タスク1.1**: `cad` extra の宣言で `uv.lock` が自動更新される（追加864 / 削除1）。削除は `provides-extras` の置換のみで
  **既存ピンは動かない**。design.md `### Modified Files` に `uv.lock` の記載は無いが、`### Technology Stack` が
  uv.lock を `cad` extra の解決場所として挙げているため境界内。
- **タスク1.2 / 後続への申し送り**: (a) `SelectionError` を終了コード 2 へ割り当てる記述は design.md
  「Error Categories and Responses」に対応行が無く、`errors.py` の docstring での外挿である。
  **確定はタスク 4.1（`_Boundary: Cli_`）の所有**であり、そこで終了コード表を確定させること。
  (b) design.md の「`GeometryError`（`BuildViolation` を伴う）」を満たす機構の所有タスクが本計画に無い。
  Errors の Key Dependencies は「なし」のため `errors.py` は `constraints.py` の値型を import できず、
  1.2 では属性を持たせていない。⚠️ **後から `errors.py` へ属性を足すと `_Boundary: Errors_` の外側からの
  変更になる**ため、タスク 2.1 / 3.4 は**違反の軸と超過量を例外メッセージへ載せる**形で満たすこと。
- **タスク1.3 / 後続への申し送り**: (a) ⚠️ **`MechanismParams.provenance` を `MappingProxyType` で包み直してはならない。**
  `dataclasses.asdict()` が非 dict のマッピングを `copy.deepcopy()` へ回すため
  `TypeError: cannot pickle 'mappingproxy' object` になる（`src/flying_object_tracking/bench/compare.py`
  に同じ罠の記録がある）。上流 `trajectory_sim.ScenarioParams` も素の dict を保持している。
  エイリアス切断は `dict(...)` の複製だけで足りる。退行は `test_params_survive_asdict_deepcopy_and_pickle`
  が捕捉する。なお `PARAMETER_PATHS` 自体の `MappingProxyType` は上流と同形で、データクラスの
  フィールドではないため `asdict` 経路に乗らない。据え置きが正しい。
  (b) design.md `#### Params` の Service Interface には `model_id` が無いが、同じ design.md の
  `## Data Models` の `dimensions.json` 例・要件 6.8・タスク 5.1（「選定結果が寸法設定ファイルの
  機種識別情報と一致する」）には必要である。**承認済み設計の内部矛盾**であり、タスク 1.4 の
  `_Boundary: Config_` は `params.py` を直せないため、所有者である 1.3 で `TrashCanMeasurements` へ
  `model_id: str` を追加して決着させた（`PARAMETER_PATHS` は 32 パス、design の例と双方向差分ゼロ）。
  ⚠️ **design.md の Service Interface 側への追記は `/kiro-validate-impl` で正誤訂正として扱うこと**。
  (c) `printing.segment_margin_mm` のみ 0 を許容している（他は正値必須）。design.md「Params」
  Preconditions の「すべての長さ・直径は有限かつ正」からの**意図的な逸脱**で、タスク 2.2 の分割数導出で
  「余裕を取らない」が意味を持つ設定であるため。
- **タスク1.4 → タスク1.5 への申し送り（重要）**: `.gitattributes` は `*.json` を `text eol=crlf` に固定して
  いるが、`config.py` の `dump_params` はプラットフォーム非依存に **LF** を書く（Windows と WSL で同じ
  コマンドが別バイト列を生むのを避けるため）。clean フィルタ後の blob は同一で `git diff` はゼロ差分だが、
  ⚠️ **`git status --porcelain` はファイルを「変更済み」として報告し続け、`LF will be replaced by CRLF` の
  警告が出る**。kiro-impl 自身が `git status --porcelain` を読むため、タスク 5.2（採寸の書き戻し）以降に
  幽霊の変更が見える。**タスク 1.5 で `.gitattributes` へ `configs/catch_mechanism/*.json text eol=lf` を
  追加し、併せて `config.py:331-334` の docstring「差分は生じない」を実態に合わせて訂正すること。**
- **タスク1.4 / 後続への申し送り**: (a) `parameters_digest` は**出所表を含む**。値が同じまま仮値→実測へ
  昇格しただけでも識別子が変わり、`geometry-baseline.json`（タスク 4.2）の再記録が要る。これは意図的で、
  「まだ仮値だったときの記録」を CAD 非導入環境で可視化する唯一の手段である（要件 4.5）。意味の変わらない
  書き足し（表に無い仮値を明示的に `"assumed"` と書く）では変化しないよう、算出時に出所表を全32パスへ
  補完している。⚠️ `schema_version` は識別子の対象外。
  (b) `SCHEMA_VERSION` は `config.py` が所有する（design.md の公開 `__all__` にあるが所有者の明記が無い）。
  `geometry-baseline.json`（4.2）と `catch-opening.json`（2.3）も `schema_version` を持つため、
  ⚠️ **それらが独立に版を刻む必要が出たらこの単一定数を分割すること**。公開（6.1）の再エクスポート元はここ。
  (c) `dimensions.json` は `retention.added_depth_mm` / `bottom_modification` も**必須**にしている。型には
  既定値があるが、ファイルから消えると「深さを足さない・底に加工しない」という決定が単一の正から読めなくなる。
- **タスク1.5 / 1.4 申し送りの決着**: `.gitattributes` に `configs/catch_mechanism/*.json text eol=lf` を
  一般則 `*.json text eol=crlf` の**後ろ**に追加して解消した（git は最後にマッチした行を採用する）。
  `dump_params` が書く LF と git のチェックアウト内容が一致するため、`git status --porcelain` は
  もう空の変更を報告しない（`parameters_digest` は 1.4 の記録 `sha256:b97c7410…` のまま不変）。
  ⚠️ **この行を `*.json` より前へ動かすと無効になる。** `tests/catch_mechanism/test_catch_repo_settings.py`
  が `git check-attr` の**実効値**で固定しており、順序を入れ替えると落ちる。
  ⚠️ design.md `### Modified Files` の `.gitattributes` 行はこの追加を記載していない。
  `/kiro-validate-impl` で design.md へ正誤訂正として反映する候補（`*.step` / `*.stl` / `*.3mf` の
  `-text` は記載どおり）。
- **タスク1.6 / 後続への申し送り（重要）**: (a) `tests/catch_mechanism/test_catch_boundaries.py` の層表
  `LAYER_ORDER` / `DOCUMENTED_MODULES` は design.md 宣言の**12モジュールを最初から名前で持つ**。走査対象は
  `src/catch_mechanism/*.py` の実在ファイルから取るため、⚠️ **後続タスクが `selection.py` / `shapes.py` などを
  書いた瞬間から、層表を編集せずに全検査が適用される**（12モジュール完成形での全緑を実証済み）。
  本ファイルの編集が要るのは **design.md に無い名前の `.py` を足す場合のみ**で、そのとき
  `test_every_source_file_is_known_to_the_layer_table` が更新指示つきで落ちる。
  (b) ⚠️ **同層 import は禁止**である（`selection` / `tolerance` / `constraints` / `metrics` は互いに import 不可）。
  design.md「各層は左側の層からのみ import する」の厳密な読みで、`## Components and Interfaces` の
  Key Dependencies にも各コンポーネントの Dependencies 節にも兄弟辺は無いことを確認済み。
  後続タスクが例えば `tolerance -> constraints` を必要とした場合はここで落ちる。**その場合は先に
  design.md の依存方向を改めること**（テストを緩めるのではなく）。
  (c) `cli` は `shapes` / `export` を**関数内で遅延 import** する。モジュール直下に書くと
  `find_module_level_cad_imports` が落ちる（タスク 4.1 を拘束する）。
- **タスク1.6 / 整理タスク向けの申し送り（低優先）**: `test_catch_params.py::test_params_module_does_not_reverse_the_dependency_direction`
  と `test_catch_config.py::test_config_does_not_import_upstream_packages` は本ファイルのツリー全体検査に
  **完全に包含された**。境界外のため削除していない。将来まとめて整理してよい。
  また design.md はこのファイルを `test_boundaries.py` と呼ぶが、`tests/prediction_core/test_boundaries.py` と
  衝突するため実名は `test_catch_boundaries.py` である（`/kiro-validate-impl` で文書側を訂正する候補）。
- **タスク2.1 / 後続への申し送り**: (a) design.md `#### Constraints` の Responsibilities は分割数の判定を
  **弦長 `D·sin(π/n)` のみ**で述べるが、実装は**半径方向 `D/2` の検査も併せて**行う。⚠️ 半径方向は分割数を
  増やしても縮まないため、弦長だけを見ると「大きな n を返せばいつか収まる」と誤り、同じ節の
  Postconditions「戻り値 n に対し n 等分した扇形が造形可能寸法に収まる」が成立しない。
  **実部品（リム外径 287.0mm = 225 + 2×1.0 + 2×30、上限 175mm）では両基準とも n=5 で挙動は同一**であり、
  半径項が効くのは D > 350mm の領域だけなのでタスク 3.1 / 3.2 はブロックされない。
  ⚠️ design.md の当該行は `/kiro-validate-impl` で正誤訂正する候補。
  (b) `sector_envelope`（n=1 は `D×D`、n≥2 は `(D/2, D·sin(π/n))`）は design.md の Service Interface に
  無いが、Postconditions を検査可能にするための補完である。⚠️ **形状層（タスク 3.2）は同じ幾何を
  再実装せず、この関数を使うこと。**
  (c) `check_envelope` は上限から `segment_margin_mm` を**引かない**。余裕は分割の見積りにのみ効く
  取り決めであり、これが「導出した n の扇形は必ず `check_envelope` を通る」を保証する。
  (d) `required_segment_count` は外径しか受け取らないため内径を知り得ず、半径方向を「中心を含む扇形」の
  最悪値 `D/2` に固定している。⚠️ 実部品は円環断片なので物理的にはより細い。D > 2×上限 の細い円環を
  扱う必要が出たら、内径を受け取る精密版を足す（design.md のシグネチャ変更を伴う）。
  (e) `MAX_SEGMENT_COUNT = 12` は上方向に固定されていない（半径方向の検査により n > 6 が到達不能なため、
  上限を 100000 にしてもテストは全緑）。挙動は正しいが、整理タスクで観測可能にする余地がある。
  同様に要件 2.8 のテストは docstring の部分文字列照合にすぎず、逆の方針を書いた docstring でも通る。
- **タスク2.2 / 後続への申し送り（重要）**: (a) ⚠️ **design.md「Error Categories and Responses」の
  「設定の不正（未知キー・欠損・範囲外）| `ParameterError`」と、コミット済み `errors.py` の
  `SelectionError` docstring（「選定基準の設定ファイルが未知の項目名を挙げている、候補の諸元に
  必須項目が欠けている」）が正面から矛盾する。** レビューは **`SelectionError` を正**と裁定した
  （より具体的かつコミット済みで、`ParameterError` にすると `SelectionError` が生産者のいない
  死んだ型になる。cli の終了コードはいずれも 2 で観測差なし）。`/kiro-validate-impl` で design.md 側を
  訂正すること。
  (b) ⚠️ **roadmap が次点として推すセリア「ブルックリン調ダストボックス」は、本実装で不適合になる。**
  roadmap は φ215 × H220 × 5L としか述べずテーパーを持たないため、円錐台の体積式から底径 φ120.85 を
  導き片側 12.078°（記録値 12.1）とした。上限 8.5° を超える。**実装の不具合ではなく roadmap 自身の
  諸元不整合**であり、購入前に実物のテーパーを実測して決着させる事項。⚠️ **楽観側（テーパーが緩い側＝
  合格側）へ丸めないこと。** タスク 5.1 の選定でこの点に触れること。
  (c) `taper_max_deg = 8.5` は roadmap が数値でなく2例（φ220→φ158 の 7.24° は可 / φ225→φ145 の 10.0° は
  強すぎる）でしか述べないための転記である。テストはリテラル 8.5 ではなく「両例を 1° 以上の余裕で
  分離する」という**制約**を固定しているため、値の見直しはその制約の範囲で行える。
  (d) `candidates.json` には roadmap に無い**例示の非適合例が2件**ある
  （`illustrative-3l-class-below-minimum` / `illustrative-lidded-upper-price-tier`）。
  `role: "illustrative_non_example"` と `illustrative-` 接頭辞で二重に明示され、テストが双条件で
  固定している。⚠️ **実売調査した品ではない。** `load_candidates` は `role` を戻り値に載せない
  （design.md の `Candidate` に `role` が無いため）ので、**タスク 4.1 の `cli select` は識別子接頭辞で
  例示と実調査品を区別し、出力に例示である旨を出すこと。**
  (e) `price_max_jpy = 110` は roadmap の「220円以上へ上げない」より厳しく、165円帯を落とす。
  roadmap 実態欄「5〜7L 帯は4チェーンとも110円」が裏付けるが、165円の適格品が現れたら見直す。
  (f) `Candidate.provenance` は候補1件につき1つである（design.md の型）。第一候補の重量 228g は実測だが
  開口内径ほかは公称のため、`Provenance.weakest` の半順序に従い候補全体を `assumed` とした。
  ⚠️ 「実測 228g」は値としては残るが**出所としては現れない**。項目別出所が要るなら design の型変更を伴う。
- **タスク2.3 / 後続への申し送り（重要）**: (a) **導出値は出荷 `dimensions.json`（φ220・空き缶φ65、いずれも
  仮値）に対し `220/2 - 65/2 = 77.5mm`・出所 `assumed`。** ⚠️ `trajectory_sim` の現行暫定値 **67.5mm より
  約15% 広い**。⚠️ 還元はタスク 5.4 の担当であり、2.3 は `src/trajectory_sim/` にも `configs/trajectory_sim/` にも
  一切触れていない。
  (b) ⚠️ **タスク 5.3 のトリップワイヤは未設置である。** 5.3 の完了状態「再導出した値と記録が一致することを
  テストで固定する」に従い、`assert load_derivation(DEFAULT_DERIVATION_PATH) == derive_position_tolerance(load_params())`
  相当を**5.3 で新規に追加する**こと。記録の再生成は
  `dump_derivation(derive_position_tolerance(load_params()), DEFAULT_DERIVATION_PATH)` で足りる。
  ⚠️ **このピンを 2.3 に置くと、`_Boundary: Config_` しか持たないタスク 5.2 が自力で緑に戻せなくなる**
  （レビューで2度差し戻された。`test_catch_tolerance.py` は `dimensions.json` の値に依存しない設計に
  なっており、採寸値が公称を上回っても下回っても全件通ることを 218.4 / 221.5 の両側で実証済み）。
  (c) `ToleranceDerivation.__post_init__` は design.md の Postconditions/Invariants より強く、値の再導出
  可能性・`formula == FORMULA`・必須前提の**包含**（厳密一致ではない）も検査する。記録の側が別の値や別の式を
  主張できると、要件 7.1 の「唯一の導出箇所」が記録経由で破れるためである。⚠️ 5.3 は前提の**追記**なら
  `tolerance.py` に触れず JSON だけで行える。既存3件の**文言変更**時のみ `ASSUMPTIONS` 定数の編集が要るが、
  5.3 の境界は `_Boundary: Tolerance_` なので境界違反にならない。
  (d) 要件 7.5（シミュレータが解釈できる形式での出力）は本タスクの `_Requirements:` に無く、
  design.md の Traceability が実現手段を `tolerance` サブコマンド（Cli）としている。**タスク 4.1 と 5.4 の担当。**
  (e) `RimParams` の**全5フィールド**が導出に参加しないことを実測で固定した（`derive_position_tolerance` は
  `params.rim` を一度も読まない）。「入った」と「キャッチできた」は別問題であり、許容誤差は保持まで成立する
  内径で決める。⚠️ フランジ幅を導出に算入してはならない。
- **タスク2.4 / 後続への申し送り**: (a) **`configs/catch_mechanism/geometry-baseline.json` は出荷していない。**
  作成はタスク **4.2** の担当である（build123d と実形状が要る）。⚠️ タスク 2.3 で2度差し戻された
  「出荷ファイルをその場の導出値へピン留めする」形を避けるため、`load_baseline` の既定パスの検査は
  `monkeypatch` で `tmp_path` へ差し替えて行っており、**出荷ファイルの有無も中身も主張していない**。
  レビューで実際に2部品の記録を出荷して全件緑を実証済み（4.2 / 4.3 はブロックされない）。
  `DEFAULT_BASELINE_PATH` は「宣言された場所」としてのみ公開している。
  (b) **不在／余剰の部品は `MetricsMismatch(field_name="presence", recorded/regenerated = PRESENT 1.0 / ABSENT 0.0)`
  で表す。** design.md が `recorded: float` / `regenerated: float` を定めているため、双方の数値が存在しない状態を
  表せる符号化はこれだけである（NaN は `==` で比較できず「不明」としか読めない。片側に実値を入れるのは
  測っていない 0 を測定値として読ませる）。⚠️ 不在の部品について体積・境界箱の行を**併せて出さない**。
  `PRESENCE_FIELD` / `PRESENT` / `ABSENT` は公開定数なので、**タスク 4.1 の `cli check` はこの項目名で
  「部品が消えた／増えた」を他の乖離と区別して出力できる。**
  (c) `parameters_digest` は**書式のみ**検査する（`sha256:` + 64桁の16進小文字）。⚠️ **現在の `dimensions.json` の
  識別子との突き合わせはタスク 4.3 の担当**であり、`ConsistencyError` を伴う。metrics.py が先取りしない。
  (d) `generator_version` は記録するが**照合には使わない**（design.md の表が「情報用」と述べる。版が上がっただけで
  落ちるべきではなく、実差は指標に出る）。
  (e) design.md 側の不整合1件: `#### PublicApi` の `__all__` に `write_baseline` が無いが、`#### Metrics` の
  Service Interface は宣言している。**タスク 6.1 / `/kiro-validate-impl` で整合させること。**
- **タスク1.6 のテストへの申し送り（低優先・境界外）**: `test_catch_boundaries.py` は
  `from catch_mechanism import <sibling>` の import 形を検出しない（`import catch_mechanism.X` と
  `from catch_mechanism.X import ...` は検出する）。現行の全モジュールはこの形を使っていないが、
  整理タスクで塞ぐ余地がある。
- **⚠️ 実物のゴミ箱を購入済み（2026-09-04 / ユーザ報告）。タスク 5.1 / 5.2 はこの実測値で進める。**
  **⚠️ 第一候補として記録した山田化学 No.335（φ220 × H244 × 底φ158）ではない別品である。**
  報告された実測値:

  | 項目 | 値 |
  |---|---|
  | 開口部直径 | **210mm** |
  | 底直径 | **180mm** |
  | 高さ | **235mm** |

  ここから導かれる数値（親が算出、purchase 前の仮値ではなく実測入力に基づく）:
  - **`position_tolerance_mm` = 210/2 − 65/2 = 72.5mm**（`trajectory_sim` の暫定値 67.5 より **+7.4%**。
    出荷仮値 φ220 での 77.5mm ではない）
  - **テーパー（片側）= atan((210−180)/2 / 235) = 3.652°** — 上限 8.5° に対して緩い。
    第一候補 No.335 の 7.24° より良く、固定アダプタ（`chassis-mechanism`）の円錐台座も浅くて済む
  - **リムの分割数 = 5**（上端外径 212〜218mm のいずれでも n=5。弦長 161〜165mm ≤ 175mm、半径 137〜140mm ≤ 175mm）
  - 選定基準は全項目適合（開口 210 ≥ 下限 200 かつ ≥ 拒否値 180、高さ 235 ∈ [200, 300]、テーパー 3.652 ≤ 8.5）

  ⚠️ **タスク 5.2 の前に確認が要る未確定事項**（そのまま `dimensions.json` の項目になる）:
  1. **開口部直径 210mm は「内径」か「外径」か。** 要件 6.5 と roadmap は
     「開口内径は外径ではなく内径を測る（縁の巻き込み分を引く）」と定める。⚠️ 外径だった場合、
     `position_tolerance_mm` は 72.5 より小さくなる（合否条件が厳しい側へ動く）
  2. **底直径 180mm は「底の外径」か「接地する平面部の径」か。** 型は
     `bottom_outer_diameter_mm` と `bottom_flat_diameter_mm` の**両方**を要求し、
     不変条件 `bottom_flat <= bottom_outer <= opening_inner` を構築時に検査する
  3. **上端外径**（`top_outer_diameter_mm`）— リムの取り付け部内径を
     「上端外径 ＋ 隙間の2倍」から導出するため必須（タスク 3.1）
  4. **実測重量**（`mass_g`）— 基準は 300g 以下。⚠️ 軽さが効く理由は転倒ではなく加速性能である
  5. **底の肉厚**（`bottom_thickness_mm`）
  6. **品名・型番・JAN・購入店・価格** — `model_id` と要件 6.8「再調達性（同一品が別ルートで
     入手できること）」の記録に要る。⚠️ **`candidates.json` にこの品の行が無い**ため、
     タスク 5.1 は候補表への追加を伴う（`_Boundary: Selection_` 内）
- **形状ライブラリの導入（2026-09-04 実施済み）**: WSL2 側に `build123d 0.11.1` を導入した。
  ⚠️ **`uv sync --extra cad` を単独で実行してはならない。** `uv sync` は宣言された集合へ**刈り込む**ため、
  他の extras（`tracking` の `opencv-python-headless`、`m1-viz` の `matplotlib`）が**アンインストールされる**。
  実際に一度これを踏み、`cv2` と `matplotlib` が消えた（`numpy` は build123d の依存として残ったため
  気付きにくい）。**必ず `uv sync --all-extras` を使うこと。**
  ⚠️ **Windows 側から `uv sync` を実行しないこと**（Linux の `.venv/` が壊れる）。
  導入前後で全スイートは 4517 passed / 14 skipped の同一結果であり、`uv.lock` にも差分は出ない。
- **タスク3.1 / 後続への申し送り**: (a) ⚠️ **Z 軸は 3.1 で検査されない。** `required_segment_count` は
  高さに `build_z_mm` を置くため常に Z を通す。実高さ `rim.height_mm > build_z_mm` の違反は 3.1 では
  検出されない（高さ 500mm で `BuildViolation(axis="z", excess_mm=320.0)` になることを実測確認済み）。
  **タスク 3.2 / 3.4 は実高さを伴う `check_envelope` を必ず通すこと。**
  (b) `clear_opening_diameter_mm` は `min(取り付け部内径, 開口内径)` である。⚠️ **有効値域では常に
  `trash_can.opening_inner_diameter_mm` と一致する**（敵対的2万件で例外0件）。これは決定1
  （フランジは外向きのみで内向きの絞りを持たない）の内容そのものであり欠陥ではないが、
  **下流（3.4 / 4.1）はこの値から「リムが狭めていない」以上の情報を得られない**。情報は
  「`rim_geometry` が例外を投げなかった」事実の側にある。出力設計時に留意すること。
  (c) `outer_diameter_mm = 取り付け部内径 + 2 × flange_width_mm` であり、**壁の肉厚を算入しない**。
  Note 2.1(a) の 287.0mm = 225 + 2×1.0 + 2×30 と、購入報告欄の分割数計算（上端外径 212〜218mm →
  弦長 161〜165mm）の双方が肉厚項を持たないことで裏付けられる。⚠️ `RimParams.wall_thickness_mm` が
  未使用なのは式の誤りではなく**タスク 3.2 の所有**である。なお wall=8.0（外径 303.0）では両読みが
  n=5 / n=6 に分岐するため「下流が恒久的に非感応」ではない。
  (d) `rim_geometry` は `check_envelope` を呼ばない。design.md の Preconditions が呼び出し側の責務と
  明記しており、`check_envelope` は違反を**値**で返すため失敗へ変えるのはタスク 3.4 の担当である。
  外径は `required_segment_count`（造形制約の検査を内包し `GeometryError` を投げる）へ渡している。
  (e) `PART_NAMES` / `build_parts` / `measure_part` は 3.1 に含めていない（部品名を定めるのは実体を作る 3.2）。
  ⚠️ **`shapes.py` は 3.2 で build123d を module 直下に import してよい唯一のモジュールの一つである**
  （もう一つは `export.py`）。3.1 時点では import していない。
  (f) design.md「Error Categories and Responses」に「受け口の形状が成立しない」の行が無く、
  `GeometryError` の適用は `errors.py` docstring からの外挿である（Note 1.2(a) / 2.2(a) と同構図）。
  `/kiro-validate-impl` での正誤訂正候補。
- **タスク3.2 / 後続への申し送り（重要）**: (a) ⚠️ **`BuiltPart` / `build_parts` / `measure_part` は
  タスク 3.3 の所有である。** 3.2 は `RimSegment(name, index, solid, retrofit_seat_count,
  joint_bearing_area_mm2)` を返す `build_segments` までを実装した。`BuiltPart.metrics: PartMetrics` は
  体積・境界箱・立体数の抽出を要し、それは 3.3 の文言が明示的に所有する。3.3 は `measure_part` を
  実装して `RimSegment` を包めば足りる（`solid` は `object` 型で公開済み）。
  ⚠️ design.md `Traceability`（design.md:315, 319, 320, 340）は要件 1.6 / 2.4 / 2.7 / 3.1 / 3.2 /
  8.1 / 8.5 / 8.6 の実現手段を `build_parts` と記していることに注意。
  (b) ⚠️ **締結座はリング全体で `retrofit_fastener_count` 箇所である**（決定4「6箇所」の読み）。
  出荷値 6箇所 / 5分割では座の配分が `[1,1,2,1,1]` となり、**セグメントは同一形状にならない**
  （実測体積 36524.6 / 36524.6 / **36708.4** / 36524.6 / 36524.6）。⚠️ **タスク 3.4 / 4.2 は
  5部品ぶんの記録行を前提にすること**（1個を5回刷るのではない）。`PART_NAMES` は部品の**種類**
  `("rim_segment",)` を保ち、個々の部品名は `segment_part_names` が `rim_segment_1…` を導く。
  座数が分割数で割り切れる場合（10箇所 / 5分割）は全セグメントが同一形状になる。
  (c) ⚠️ **支圧面積は締結座に局在した有界量である**: `継手座の端面 + boss_radial × wall_thickness`。
  **フランジ・壁の断面のうち座の範囲を外れる部分を算入してはならない。** `check_joint` が名指しする
  破壊モードはボルト座面のめり込みであり支配量は座面圧で、ボルト軸（r=122.1）から 12〜21mm 離れた
  フランジ断面（r=126.7〜143.5）を足しても座面圧は下がらない。⚠️ **レビューで一度この誤りを踏み、
  合否が拒否→受理へ反転する寸法（height=16/wall=1/flange=30/insert_od=1.0）が生じた。**
  退行は `test_joint_bearing_area_is_insensitive_to_the_flange_width`（フランジ幅 30→60 で
  分割数が 5→6 に変わっても 289.0822313139195 のまま不変）が捕捉する。
  (d) `_MIN_FEATURE_EDGE_MM=2.0` / `_RETROFIT_PAD_DROP_MARGIN_MM=2.0` / `_DOWEL_FIT_CLEARANCE_MM=0.2`
  は mm の寸法値でありながら実装コード内にある。要件 1.1 と緊張するが `_Boundary: Shapes_` では
  `dimensions.json` へ移せない。`/kiro-validate-impl` での design 側追記候補。
  (e) ⚠️ **後付け座のパッドと継手座の干渉を検査するガードは無い。** 出荷値では rim_segment_3 で
  両者が融合し `Face.radius` が `None` になるが、穴同士の干渉は無い（ボルト軸 r=122.1/z=11.7、
  座の穴 r=128.5/z=14.3〜20.0）ことを実測で確認済み。
  (f) テストヘルパ `_joint_boss_region` は `shapes_module._JOINT_BOSS_INSERT_DIAMETER_MULTIPLE` を
  読むため、クリップ半径と解析式が座の規則を共変する。⚠️ **これは妥当**（座＝当たり面そのものであり、
  座が正当に広がれば支圧面積が増えるのが正しい。テスト側にリテラルを書くと正当な設計変更で偽陽性）。
  規則は野放しではなく、既存の境界ペア（flange=10.0 拒否 / flange=12.0 構築、wall=1.0・iod=4.6）が
  倍数を **k ∈ (1.957, 2.391]** に挟んでいる。
  (g) `build123d` は `build_segments` 内の**遅延 import** である。⚠️ module 直下へ移すと CAD 非導入
  環境で**収集時 ERROR**（テストモジュール全滅）になり要件 5.7 が壊れる。境界テストは `shapes` に
  module 直下 import を許すが、許可と要求は別である。全ガードは遅延 import より**前**で発火するため、
  拒否系の検査は CAD 非導入環境でも走る。
- **タスク3.3 / タスク4.2 への申し送り（数値・重要）**: 形状指標の決定性は**本物**である。
  同一パラメータからの再構築は同一プロセス内でも別プロセス間でも体積・境界箱が
  **ビット単位で一致**する（`float.hex()` まで確認、別プロセス4回で1ビットも動かない）。
  よって決定性の検査は許容差ではなく `==` で固定してよい。
  ⚠️ **ただし「決定的」と「全部品が同値」は別である。** 出荷値（座6箇所 / 5分割、配分 [1,1,2,1,1]）
  での実測体積は 36524.6065133904 / 36524.606679998156 / **36708.41179572757** /
  36524.60576950924 / 36524.60604935563 mm^3。座2つの `rim_segment_3` が約184mm^3 大きいのは
  **実形状の差**である。
  ⚠️ **一方、座1つの4点が下位桁で分かれるのは求積誤差であり実形状の差ではない**（幅 9.1e-4 mm^3、
  相対 2.5e-8）。根拠3点: (1) `retrofit_fastener_count` を **10 または 5**（全セグメント同一相対角）に
  すると5点は**ビット単位で完全一致**する。(2) `BRepGProp.VolumeProperties_s` の `Eps` を 1e-9 まで
  締めると4点は 36524.60616466… に**相対 2e-13 で収束**する。(3) 同一ソリッドの剛体回転・鏡映による
  体積変化は相対 4e-16（1〜2 ULP）にすぎず、配置は体積を動かさない。
  ⚠️⚠️ **したがって `GeometryBaseline.volume_rel_tolerance` は 1e-7 以上を下限とすること。**
  同一ライブラリが同一形状に対して出す相対誤差の実測が **1.3e-8** であり、0 や 1e-9 を記録すると
  build123d / OCCT の版差で確実に破綻する。収束値の参考: 座1つ 36524.60616466…、
  座2つ 36708.41235303…（Eps=1e-12）。
  ⚠️ **「4点の値が分かれていること」を assert してはならない**（版が上がって正当に一致したときに、
  何も壊れていないのにテストが落ちる）。レビューで一度この誤りを踏み、削除した。
- **タスク3.3 / 後続への申し送り**: (a) `measure_part(name, solid)` は **build123d の名前を署名にも実装にも
  持たない**（`solid.volume` / `solid.bounding_box().size` / `len(solid.solids())` のダックタイピング）。
  これにより CAD 非導入環境でも `measure_part` 自体は評価でき、`_FakeSolid` による検査が成立する。
  ⚠️ **`measure_part` は丸め・量子化を一切行わない。** 記録側 (`GeometryBaseline`) が既に相対・絶対の
  許容差を持つため、抽出側でも丸めると許容差機構が二重化し、記録側を 0 にしても丸め幅より小さい
  ずれが観測できなくなる。
  (b) `BuiltPart` は design.md どおり `name` / `solid` / `metrics` の3項目のみで、`RimSegment` の
  `retrofit_seat_count` / `joint_bearing_area_mm2` を運ばない。継手の数値が要る呼び出し側は
  `build_segments` を使うこと。記録へ支圧面積を載せたい場合は design 側の追記が要る。
  (c) 境界箱は `build_segments` が返す配置のままの軸並行外接箱である（リング中心が原点）。
  実測 (51.67657123844347, 168.69436760793977, 30.03847577293368) は解析値と厳密一致し、
  5部品すべてで同一。⚠️ **タスク 3.4 が造形板向けに再配置する場合、指標の境界箱も一緒に変わる**
  ——記録は配置込みの値である。
- **タスク3.4 / 後続への申し送り（重要）**: (a) ⚠️⚠️ **lib3mf（`build123d.Mesher.write`）はプロセスのロケールを
  `C` に変えたまま戻さない。** 実測: 呼び出し前 `LC_CTYPE=C.UTF-8` / `getpreferredencoding()=='UTF-8'`、
  呼び出し後 `'C'` / `'ANSI_X3.4-1968'`。副作用を持つのは `write` だけで、`export_step` / `export_stl` /
  `Mesher()` / `add_shape` は無害である。これにより `subprocess(..., text=True)` の**日本語出力の復号が壊れ**、
  `tests/sensing_foundation/test_sensing_cli.py::test_module_entrypoint_smoke` と
  `tests/trajectory_sim/test_trajectory_sim_cli.py::test_python_dash_m_trajectory_sim_end_to_end` が
  **フルスイート実行時のみ** `UnicodeDecodeError` で落ちる（単体では通る）。`export.py` は `_write_3mf` で
  `locale.setlocale(locale.LC_ALL)` を退避し `finally` で復元している。⚠️ **`Mesher.write` を呼ぶ
  あらゆる経路（タスク 4.1 の `cli` を含む）で同じ退避・復元が要る。**
  (b) ⚠️ **`build123d.export_step` は `Unit` の全6値（MC/MM/CM/M/IN/FT）で必ず
  `SI_UNIT(.MILLI.,.METRE.)` を書く。** 単位は表明ではなく**座標の倍率**として効く（10mm の箱を
  `Unit.M` で出すと 10000mm、`Unit.IN` で 254mm）。したがって当該文字列の検査は**単位の観測にならない**。
  レビューで一度この空検査を踏んだ。単位は STEP の `CARTESIAN_POINT` 座標から外接箱を組んで
  `PartMetrics.bbox_mm` と突き合わせて観測する（`test_step_vertex_coordinates_are_in_millimetres`）。
  ⚠️ ただし **X 軸は使えない**——円・円筒の**中心点**も `CARTESIAN_POINT` として書かれ、それがリング軸
  （原点）付近にあるためセグメント自身の X 範囲より 64〜81mm 大きく出る。Y と Z で十分である
  （許容差 1e-4mm に対し最小の単位誤り `Unit.CM` でも 168mm の範囲が10倍になる）。
  (c) **原子性の保証範囲**（モジュール docstring に明記済み）: 書き出し中の失敗は出力先を一切触らない
  （15ファイルすべてを staging に書き終えてから移す）。出力先が**存在しない**場合の commit は staging
  ディレクトリの単一 `os.rename` で真に原子的。⚠️ **出力先が既存の場合は per-file `os.replace` の列**
  であり、同時に観測すれば新旧が混在しうる。失敗時は補償ロールバックで復元するが、**二重障害
  （ロールバック自体が失敗）では混在が残りうる**。実測では4ファイル欠落・0破損だった。
  ⚠️ ディレクトリ差し替えを選ばなかったのは、利用者指定の `--output-dir` にある**無関係なファイルを
  消してしまう**ため（`test_files_that_the_exporter_does_not_own_survive_a_successful_run` が固定）。
  (d) staging とロールバックのディレクトリは `tempfile.mkdtemp(dir=destination.parent)` で作る。
  ⚠️ **システムの一時領域を使ってはならない**——実測でファイルシステムが異なり
  （`/tmp` の `st_dev=75` に対しリポジトリは `71`）、`os.rename` が `OSError 18 Invalid cross-device link`
  になる。`shutil.move` は黙って copy+delete に退化するため原子性を失う。
  (e) **再実行性は形式によって異なる。** STEP と STL はバイト一致する（STEP は表題部の時刻を
  `export_step(..., timestamp=...)` で固定している。固定しないと壁時計が入って毎回変わる）。
  ⚠️ **3MF はバイト一致しない**——lib3mf が wrapper の object / component / build item に毎回新しい
  UUID を刻み、`add_shape` の `uuid_value` では形状ひとつぶんしか制御できない。テストは UUID を
  マスクしてモデル XML を比較している。タスク 4.2 は**指標**を記録し成果物をハッシュしないため影響しない。
  (f) **部品は造形板向けに再配置していない。** `build_segments` が返す配置（リング中心が原点）のまま
  書き出すため、`ExportedPart.metrics` はタスク 3.3 が測った値とビット一致する。
  ⚠️ **STL / 3MF は造形板のレイアウトになっていない。スライサ側での配置が要る。**
  (g) `check_material` は `export_parts` が呼ぶ唯一の場所である（`build_segments` は呼ばない）。
  frozen かつ slots のデータクラスは pickle 復元で `__post_init__` を経由しないため、これが要件 2.7
  「検査を通らない形状の生成物を出力しない」の実現箇所である。
  (h) 出荷 `dimensions.json` での成果物は **15ファイル・約1,989,387バイト**。`rim_segment_3`（座2つ）は
  3形式すべてで他より明確に大きい（STEP 158,502B 対 約111,900B）。
- **タスク4.1 / 後続への申し送り**: (a) **例外→終了コードの対応表**（`cli.EXIT_CODE_BY_ERROR`）を確定した:
  `ConsistencyError`→**1**（検査の不一致）/ `ParameterError`→**2** / `SelectionError`→**2** / `GeometryError`→**2** /
  `CadUnavailableError`→**3** / 裸の `ImportError`・`ModuleNotFoundError`→**3**。
  `MetricsMismatch` が空でない場合は**例外にせず値のまま** exit 1 とする。
  ⚠️ Note 2.2(a) が保留していた `SelectionError` の割り当ては **2 で確定**した。`errors.py` の docstring が
  本例外を選定の**入力**の誤り（基準ファイルの未知の項目名・候補諸元の欠損・存在しない候補の名指し）に
  限定しており、候補の不適合は `CandidateVerdict.accepted = False` という**値**でこの経路を通らないため、
  「検査の不一致」(1) と混ざらない。`test_exit_code_table_covers_every_error_family` が
  `errors.__all__` との集合一致を固定するので、⚠️ **新しい例外系統を足したら対応表も更新しないと落ちる。**
  ⚠️ `main()` が捕捉するのは `(CatchMechanismError, ImportError, OSError)` **だけ**である。
  `AttributeError` 等の本物の不具合は traceback ごと伝播する（exit 2 に潰さない）。これは意図した設計。
  (b) ⚠️ **`_verify_digest` は `cli.py` にインライン実装されている**が、Note 2.4(c) は
  「記録の識別子と現在の `dimensions.json` の識別子の照合」を**タスク 4.3 の担当**としている。
  4.1 の境界は `Cli` のみで他に置き場所が無かった。**タスク 4.2 は `_Boundary: Metrics, Cli_` の
  両方を持つので、そこで `metrics.py` へ移すこと。** そうすれば 4.3 は
  `test_check_digest_only_detects_a_stale_record` を重複させずに済む。
  ⚠️ なお要件 4.5 は `--digest-only` 無しでも成立する（`_verify_digest` は CAD の import より**前**に
  走るため、CAD 非導入環境でも古い記録は exit 1 で検出される）。
  (c) ⚠️ **`configs/catch_mechanism/geometry-baseline.json` は出荷していない**（タスク 4.2 の担当）。
  既定パスの `check` は `load_baseline` の `ParameterError` を受けて exit 2 で失敗し、欠けているファイルを
  名指しする。本タスクのテストは既定パスの記録の有無・中身を一切主張せず、記録が要る検査はすべて
  `--baseline <tmp_path>` を使う。レビューで実際に記録を出荷して 772 passed / 0 failed を実証済み。
  新規記録の既定 `volume_rel_tolerance = 1e-6` は Note 3.3 の下限 1e-7 を満たす。
  (d) `tolerance --check configs/trajectory_sim/sweep-layout.json` は**現時点で exit 1** になる
  （`parameters.catch.position_tolerance_mm` が記録されていないため）。これは今日の正しい状態であり、
  ⚠️ **タスク 5.4 が値と出所を書けば 0 になる**（`cli.py` の変更は不要。`CatchCriteria.position_tolerance_mm`
  は `src/trajectory_sim/params.py` に既定 67.5 で既に存在する）。どのテストもこのファイルを参照していない。
  (e) `cli` は `shapes` / `export` を**関数内で遅延 import** する。⚠️ module 直下へ移すと
  `find_module_level_cad_imports` が落ち、CAD 非導入環境も壊れる。`importlib.metadata.version("build123d")`
  は import 文ではないため境界検査に掛からず、`PackageNotFoundError` で守られている。
  (f) ロケール（Note 3.4(a)）は `export.py` の `_write_3mf` が退避・復元しており、`cli build` は
  `export_parts` 経由でしか `Mesher.write` へ到達しないため **CLI 独自のガードは不要**である。
  ⚠️ ただし CLI のサブプロセス検査は `text=True` ではなく **`encoding="utf-8"` を明示**すること。
  (g) ⚠️ `tolerance` を `--output` 無しで実行すると出荷の `configs/catch_mechanism/catch-opening.json` を
  上書きする（design.md が定める動作）。テストは必ず `--output <tmp_path>` を渡すこと。
- **タスク4.2 / 後続への申し送り**: (a) **`configs/catch_mechanism/geometry-baseline.json` を出荷した。**
  `parameters_digest="sha256:b97c7410…89775"` / `generator_version="build123d 0.11.1"` /
  `volume_rel_tolerance=1e-06` / `bbox_abs_tolerance_mm=0.001` / 5部品（体積 36524.6065133904 /
  36524.606679998156 / **36708.41179572757** / 36524.60576950924 / 36524.60604935563、境界箱は5部品とも
  (51.67657123844347, 168.69436760793977, 30.03847577293368)、立体数 1）。
  ⚠️ **記録は `python -m catch_mechanism build --update-baseline` で再生成でき、バイト単位で一致する。**
  許容差は**両側**がテストで固定されている（体積 `1e-7 <= x <= 1e-4`、境界箱 `0 < x <= 1e-2`）ので、
  ⚠️ **「1 を記録して常に緑」にはできない。** 体積 1e-6 は Note 3.3 の下限 1e-7 の10倍上、実測求積誤差
  1.3e-8 の約77倍であり、座1つ／2つの実形状差 184mm^3（相対 5e-3）より3桁小さいので実差は必ず捕まる。
  ⚠️ `bbox_abs_tolerance_mm=1e-3` は 1µm でやや厳しい（境界箱は解析値と厳密一致するため）。
  OCCT の版差で超える余地があり、`/kiro-validate-impl` での見直し候補。**今は緩めないこと**（緩めると検出力だけが落ちる）。
  (b) ⚠️ **Note 4.1(b) の申し送りを消化した。** `cli._verify_digest` を
  **`metrics.verify_baseline_digest(baseline, current_digest, *, baseline_path, dimensions_path) -> str`**
  へ移設した。⚠️ **`MechanismParams` ではなく識別子の文字列を取る**設計である。理由は
  `metrics.py` が `params` へ依存せず要件 5.7 の「中核層は CAD も上位層も要さない」を保てること、
  およびパスを引数に取るため出荷ファイルにも `tmp_path` の記録にも同じ関数を当てられること。
  書式違反の `current_digest` は `ConsistencyError` ではなく `ParameterError`（呼び出し方の誤りと
  不整合を混ぜない）。⚠️ **この検査は CAD の import より前に走るため、`--digest-only` を付けなくても
  CAD 非導入環境で古い記録を exit 1 で検出できる**（要件 4.5）。
  (c) ⚠️⚠️ **タスク 4.3 に実作業が残っている（重複ではない）。** レビューの変異検証で、
  `test_the_shipped_record_matches_the_current_dimensions_digest` に `@requires_cad` を付けても
  **どのテストも落ちない**ことが判明した。「この検査が形状ライブラリ非導入の環境でも実行される」ことを
  保証するピンが無い。⚠️ **4.3 はこれを固定するメタテストを含めること。** 併せて design.md の
  Testing Strategy が別ファイル `test_baseline_digest.py` に割り当てる「`dimensions.json` を変更して
  記録を更新しない**状況**の再現」も 4.3 の担当であり、4.2 の8件は関数の**契約**を合成 digest で
  固定しているだけである。⚠️ 4.3 の境界は `Metrics` のみだが、`tests/catch_mechanism/test_catch_baseline_digest.py`
  を足すだけで完結でき、`cli.py` の編集は不要であることをレビューが確認済み。
  (d) ⚠️ **既定パスの `check` の挙動が変わった**（Note 4.1(c) の解消）。記録が無くて exit 2 だったものが
  exit 0 になる。4.1 のテストは `--baseline <tmp_path>` を使っていたため影響なし。
  (e) design.md `#### Metrics` の Service Interface に `verify_baseline_digest` の宣言が無い。
  Note 2.4(e) の `write_baseline` / PublicApi の不整合と併せて**タスク 6.1 / `/kiro-validate-impl` で
  design 側を整合させること**。ファイル名も design の `test_geometry_regression.py` に対し実名は
  `test_catch_geometry_regression.py`（Note 1.1 の衝突回避規約）。
  (f) ⚠️ **CAD 遮断の検証をするときはスタブを `/tmp` に置かないこと。** 実装者が検証中に WSL の `/tmp` が
  消えてスタブが無効化され、遮断したつもりの実行が CAD 導入時と同じ数字を返す事象を踏んでいる。
  **遮断実行のたびに `PYTHONPATH=... python -c "import build123d"` が `ImportError` になることを
  前後で確認すること。**
- **タスク4.3 / 後続への申し送り**: (a) ⚠️ **`test_catch_baseline_digest.py` は
  `test_catch_geometry_regression.py::test_the_shipped_record_matches_the_current_dimensions_digest` を
  「CAD 依存にされていないか」見張っている。** 3系統の見張りがあり、レビューで次の退行をすべて検出する
  ことを実証済み: `@requires_cad` の付与 / module 直下の `pytest.importorskip("build123d")` /
  素の `@pytest.mark.skip` / **対象関数の改名**（3件が落ちるので見張り自体を黙って失えない） /
  `verify_baseline_digest` 内での遅延 CAD import。
  ⚠️ **これは要件 5.7「形状生成の環境を持たない実行環境でも、形状生成を除くすべての検査が完了できる」の
  唯一の機械的な担保である。対象テストを CAD 依存にしないこと。**
  (b) ⚠️ **`_regression_module()` で `sys.modules` を全走査して `Path.resolve()` してはならない。**
  build123d を読み込んだ状態では module が数千あり、`/mnt/c` の 9p マウント上では1回あたり約7秒かかる
  （2回呼ぶので約14秒）。pytest は素の名前 `test_catch_geometry_regression` で登録するため名前引きで足りる。
  実測で `tests/catch_mechanism` が 56.7s → 42.2s に短縮した。同種の「テストを検分するテスト」を書くときの
  一般的な注意である。
  (c) ⚠️ **タスク 5.2 への申し送り**: `_stale_by_provenance` は出荷の出所表に **`assumed` の項目が
  最低1つ残っていること**に依存する（現在は 3/3 が assumed）。5.2 が採寸値を反映して**全項目を
  `measured` へ昇格させると**、このヘルパは黙って通るのではなく明示的な `AssertionError` を出す。
  そうなったら「出所表のみの変更で digest が古くなる」ことを別の方法で示すこと。
  (d) `metrics.py` は本タスクで**一切変更していない**（4.2 が `verify_baseline_digest` を配置済み）。
  出荷 `dimensions.json` / `geometry-baseline.json` の改変は一切行わず、すべて `tmp_path` の写しに対して
  行っている。⚠️ **テストで出荷ファイルを書き換えないこと**（`test_these_tests_leave_the_shipped_files_untouched`
  が前後のバイト列一致で固定しており、空検査でないことも確認済み）。
- **タスク4.4 / 後続への申し送り**: (a) 受け口の不変条件は**代表点ではなくパラメータ空間の性質**として
  固定してある。掃引は 20,000 件・種固定（`random.Random(20260904)`）で、開口径は 120〜300mm の全相異値。
  ⚠️ **掃引は両枝を厚く踏んでいることを確認済み**（検査1: 拒否 6,571 / 受理 12,359、検査3: 収まる 10,346
  （n=1〜6 に分散）/ Z違反 2,013）。Z違反の 2,013 件はすべて意図した外接箱ガードが投げている。
  ⚠️ **`rim_geometry` / `segment_envelope` / `RetentionParams` の挙動を変えるときは、代表点のテストだけ
  でなくこの掃引も見ること。**
  (b) ⚠️ **「開口を狭めない」の検査は `clear >= opening` の assert ではない。** Note 3.1(b) のとおり
  有効値域では常に等号が成立するため、その形は近似的に恒真である。**本物の内容は「取り付け部内径が
  開口内径を下回るなら `rim_geometry` が拒否する」という二分岐の全域性**であり、5mm の食い込みを許容
  する変異で既存テストは無反応・本ファイルのみ 6 件落ちることを実測している。
  (c) ⚠️ **決定2・決定3（`added_depth_mm == 0.0` / `bottom_modification == "none"`）は「表現不可能」
  として固定してある**（1e-12 / nan / inf / int 1 / "NONE" / 前後空白などの綴りの揺れ / `dataclasses.replace`
  経由の第2の構築経路）。`abs(depth) > 1.0` と `strip().lower()` を入れる変異で、既存の
  `test_catch_params.py::test_added_depth_must_be_zero` などは**緑のまま**で本ファイルのみ 9 件落ちる。
  (d) 既存テストの `retrofit_fastener_count` は 6 / 10 / 19 のみだった。本ファイルが **1 と 7** を追加した。
  ⚠️ `count=1` では `[0, 0, 1, 0, 0]` となり**座を持たないセグメントが生じる**。等配分の上下界
  （`max−min <= 1`、`floor`/`ceil`）もここで初めて固定された。
  (e) 要件 9.6 の警告（「⚠️ 設計の自己整合性の検査であり、合否条件ではない」）を module と**全テスト関数の
  docstring** に置いている。⚠️ **これを固定するメタテストは無い**ので、テスト関数を足すときは手で入れること。
  （Note 2.1(e) のとおり docstring の部分文字列照合は逆の方針でも通る弱い検査であり、追加するなら
  その限界も併記すること。）
  (f) ⚠️ **実装者の重複監査のうち MUTANT-3（外径の過小申告）だけが過大申告だった。** レビューの再現では
  既存 `test_catch_shapes.py::test_segment_count_is_delegated_to_the_constraints_module` が −20mm でも
  −1mm でも反応する。項目3の真の追加分は「実高さでの外接箱」と「掃引の広さ」だけである。
  なお完全重複だった `test_the_shipping_retrofit_count_lands_on_the_recorded_distribution` は削除した
  （`[1,1,2,1,1]` の主張は `test_catch_shapes.py:1800` に残っている）。
  (g) `test_the_derived_split_never_leaves_the_build_plane_over_the_sweep` の `pytest.raises(GeometryError)`
  はメッセージを assert していない。⚠️ 現時点では 2,013 件すべてが意図したガードで落ちることを実測済み
  だが、将来別のガードが先行しても緑のままになる。タスク3.2 の「意図したガードが出したことを固定する」
  規約に合わせるなら補強の余地がある。
  (h) design.md の正誤訂正候補: `### Directory Structure` は `test_rim_invariants.py`、
  「受け口の不変条件テスト」節は「`cad` extra 必要」と記すが、実名は `test_catch_rim_invariants.py`
  （Note 1.1 の衝突回避）であり、48件中 42件は CAD 非導入環境でも走る（要件 5.7 に対して強い方向）。
- **⚠️ 実測値の確定（2026-09-05 / ユーザ回答）。先の記録の未確定事項1・2・3が解消した。**

  | 項目 | 値 | 状態 |
  |---|---|---|
  | `opening_inner_diameter_mm` | **210.0** | ⚠️ **内径であることを確認済み** |
  | `top_outer_diameter_mm` | **220.0** | ⚠️ 「220mm ほど」= 概数。下記参照 |
  | `bottom_outer_diameter_mm` | **180.0** | ⚠️ **底の外径であることを確認済み** |
  | `height_mm` | **235.0** | |

  ここから導かれる値（親が実コードで検証済み）:
  - 縁の巻き込みは**片側 5.0mm**（内径 210 に対し外径 220）
  - **`taper_deg` = atan((220−180)/2 / 235) = 4.865°**（上端外径→底の外径、片側）。上限 8.5° に対し
    余裕がある。⚠️ **固定アダプタ（`chassis-mechanism`）の円錐台座が浅くて済む**
  - **`position_tolerance_mm` = 210/2 − 65/2 = 72.5mm**（暫定値 67.5 より +7.4%）
  - **リムの取り付け部内径 = 222.0mm**（220 + 隙間 1.0mm × 2）
  - **リム外径 = 282.0mm**（出荷仮値 287.0 から 5mm 縮小）
  - **分割数 = 5**（変わらず）

  ⚠️ **タスク 5.2 の前になお確定が要る項目**:
  1. **`bottom_flat_diameter_mm`（接地する平面部の径）** — 底の外径 180.0 とは別項目。型が
     `bottom_flat <= bottom_outer <= opening_inner` を構築時に検査する。底が丸まっていれば平面部は
     180 より小さい。⚠️ **`liner_flat_min_diameter_mm`（緩衝材を貼れる平面の下限、出荷仮値 140.0）と
     突き合わせる対象**でもある（要件 9.4）
  2. **`mass_g`（実測重量）** — 基準は 300g 以下
  3. **`bottom_thickness_mm`（底の肉厚）**
  4. **品名・型番・JAN・購入店・価格** — `model_id` と要件 6.8「再調達性」の記録に要る。
     ⚠️ **この品は `candidates.json` に行が無い**ため、タスク 5.1 は候補表への追加を伴う
     （`_Boundary: Selection_` 内）
  5. ⚠️ **`top_outer_diameter_mm` の「ほど」を潰すこと。** 取り付け部内径は「上端外径 + 隙間 × 2」で
     導出され、隙間の出荷仮値は片側 1.0mm しかない。実測が 221mm なら隙間は実質 0.5mm、222mm なら
     ゼロになる。**リムが物理的に嵌まるかがこの1値に懸かっている。**
     0.1mm 単位で測るか、測れないなら `fit_clearance_mm` を上げること（`dimensions.json` の値変更のみ）。
- **⚠️ `fit_clearance_mm` を 1.0 → 2.0mm へ上げる（2026-09-05 / ユーザ承認済み）。タスク 5.2 で反映する。**
  経緯: 親が当初「上端外径を 0.1mm 単位で測ること」を求めたが、**これは設計判断の誤りだった**。
  ユーザの指摘（「実測する以上その精度の計測は難しい」）が正しい。理由2点:
  (1) 100円ショップの PP 成形品は真円ではなく、φ220 なら ±1〜2mm の歪みは普通にある。
      そもそも「直径」が1つの数字で決まらない。
  (2) A1 mini の XY 精度は ±0.2〜0.3mm で、1層目の潰れ（elephant's foot）も乗る。
  ⚠️ **正しい対処は測定精度を上げることではなく隙間を広げることである。** `fit_clearance_mm` は
  要件 8.6 が「個体差を吸収する隙間」として定義した値であり、広げるのが本来の使い方である。

  | 隙間（片側） | 取り付け部内径 | 実測がこの範囲なら嵌まる | 分割数 |
  |---|---|---|---|
  | 1.0mm（旧仮値） | 222.0mm | 219〜222mm（幅 2mm） | 5 |
  | **2.0mm（採用）** | **224.0mm** | **218〜224mm（幅 4mm）** | 5 |
  | 3.0mm | 226.0mm | 217〜226mm（幅 6mm） | 5 |

  副作用: リムがゴミ箱の上で横にずれる量が片側 2mm になるが、許容誤差 72.5mm に対して 3% 弱で実害なし。
  ⚠️ **設定ファイルの値変更のみ。コードは触らない。** リム外径は 282.0 → 284.0mm、分割数は 5 のまま。
  ⚠️ **上端外径は「220mm ほど」の概数のままでよい。** 0.1mm 単位の測定は要求しない。

- **⚠️ 採寸項目の要求水準を緩めた（同上）**: `bottom_flat_diameter_mm` は概数でよい（緩衝材を貼れる平面が
  あるかの確認が主目的）。`mass_g` はキッチンスケールの 1g 単位でよい。
  ⚠️ **`bottom_thickness_mm` は現設計で使っていないため省略可**（型は必須なので、仮値のまま出所を
  `assumed` に残す扱いとする）。⚠️ **品名・型番・JAN・購入店・価格だけは代替がきかない**
  （要件 6.8「再調達性（同一品が別ルートで入手できること）」が明示的に要求する）。
- **⚠️⚠️ 訂正: 購入品は第一候補そのものだった（2026-09-05）。先の記録「別品である」は親の誤りである。**
  JAN **4965534335027** は roadmap 記載の第一候補**キャンドゥ「ダストボックス丸」＝ 山田化学 No.335**と
  完全に一致する（底面の刻印にも「山田化学株式会社 三重県伊賀市」「No.335」「容量 6.9L」「PP」）。
  ⚠️ **`candidates.json` に `yamada-kagaku-no335` の行が既にあるため、タスク 5.1 で候補表へ行を
  追加する必要はない。既存行の値を実測で更新する作業になる。**

  **公称値と実測値のずれが3点判明した（⚠️ 実測が正。要件 1.5）:**

  | 項目 | 候補表の公称 | パッケージ表記 | **実測** |
  |---|---|---|---|
  | 開口内径 | 220.0 | φ220（**外径**） | **210.0** |
  | 高さ | 244.0 | H224 | **235.0** |
  | 底の外径 | 158（→7.0°） | — | **180.0**（→**4.865°**） |

  ⚠️ **公称 φ220 は外径であり、内径は 210mm である。** roadmap はこれを開口内径として扱っていたため、
  `position_tolerance_mm` の見積もりが 10mm ずれていた（77.5 → **72.5mm**）。高さは公称が3通り
  （244 / 224 / 実測 235）あり、テーパーも実測 4.865° は公称 7.0° より緩い。

- **⚠️ タスク5.2 で `dimensions.json` へ反映する確定値（出所つき）**

  | パス | 値 | 出所 | 根拠 |
  |---|---|---|---|
  | `trash_can.model_id` | `"yamada-kagaku-no335"` | — | JAN 4965534335027 |
  | `trash_can.opening_inner_diameter_mm` | **210.0** | **measured** | ユーザ実測 |
  | `trash_can.top_outer_diameter_mm` | **220.0** | **measured** | ユーザ実測（概数）＋パッケージ表記が一致 |
  | `trash_can.bottom_outer_diameter_mm` | **180.0** | **measured** | ユーザ実測 |
  | `trash_can.bottom_flat_diameter_mm` | **170.0** | **assumed** | ⚠️ ユーザ報告は「底面はほぼ全て平ら」という**定性的**な観察。定量測定ではないため `measured` を名乗らない（Note 1.4「実測を名乗るには明示が要る」）。⚠️ **底外径 180 から隅 R のぶん片側 5mm を保守側に引いた値**。緩衝材の下限 `liner_flat_min_diameter_mm=140` に対し十分な余裕がある |
  | `trash_can.height_mm` | **235.0** | **measured** | ユーザ実測 |
  | `trash_can.taper_deg` | **4.865** | **measured** | `atan((220−180)/2 / 235)`。3入力すべて実測のため `Provenance.weakest` により measured。⚠️ **高さ 235 は縁を含む全高であり、テーパーのついた側壁はそれより短い可能性がある。その場合の実際のテーパーは 4.865° より僅かに急**。上限 8.5° に対し余裕が大きいので判定は変わらない |
  | `trash_can.mass_g` | **228.0** | **assumed** | ⚠️ roadmap が「実測」として記録するが**出典が曖昧**なため `assumed` に留める。基準 300g 以下に対し余裕があり判定は変わらない。実測できれば `measured` へ昇格させること |
  | `trash_can.bottom_thickness_mm` | （出荷仮値のまま） | assumed | ⚠️ **現設計で未使用**。ユーザ承認のうえ省略した |
  | `rim.fit_clearance_mm` | **2.0** | assumed | 設計上の選択値。ユーザ承認済み（1.0 → 2.0） |

  **これらから導かれる値**: 取り付け部内径 **224.0mm** / リム外径 **284.0mm** / 分割数 **5** /
  **`position_tolerance_mm` = 72.5mm（出所 measured）**。
  ⚠️ 開口内径と対象物径の**両方が実測**になれば許容誤差の出所も `measured` になる（要件 7.4）。
  ⚠️ **対象物（空き缶 φ65）はまだ仮値である。** M1 の実験条件として実測されるまで
  `target_object.diameter_mm` は `assumed` であり、その場合 `Provenance.weakest` により
  許容誤差の出所も `assumed` に留まる。5.3 でどちらになるか確認すること。

- **⚠️ タスク4.3 への影響（Note 4.3(c) の再掲）**: `_stale_by_provenance` は出荷の出所表に `assumed` の
  項目が最低1つ残ることに依存する。5.2 で複数項目が `measured` へ昇格しても、
  `bottom_flat_diameter_mm` / `mass_g` / `bottom_thickness_mm` / `target_object.*` が `assumed` に
  残るため条件は満たされる。
- **環境（全タスク共通）**: Python 環境は **WSL2 側にのみ存在する**。Windows 側に `python` / `uv` は無い。
  検証は必ず `wsl -e bash -lc 'cd /mnt/c/Users/user/repos/stb-hardware && uv run pytest -q'` の形で実行する。
  ⚠️ Windows から `uv sync` して `.venv/` を上書きしないこと（Linux venv が壊れる）。
