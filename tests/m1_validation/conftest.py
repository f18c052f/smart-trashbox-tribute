"""m1_validation のテスト共通フィクスチャ。

一時ディレクトリ・既定設定・既定レイアウトに関する共通フィクスチャの器を
ここに置く。`M1Settings`（タスク 1.5）と `ThrowLayout`（タスク 1.4）は
まだ存在しないため、対応するフィクスチャは**それぞれの実装が landing した
時点で中身を実装する**。

⚠️ **`synthetic.py` / `fakes.py` を追加するタスクへの申し送り**

design.md「File Structure Plan」は本ディレクトリに `synthetic.py`（既知の
放物軌道の合成器）と `fakes.py`（上流公開型を模した最小ダブル）を置くと
定めている。**この2つのベース名は `tests/sensing_foundation/synthetic.py` /
`tests/flying_object_tracking/{synthetic,fakes}.py` と衝突する。**
`tests/` 配下はパッケージ化されていない（`__init__.py` を置かない）ため、
モジュール名は pytest セッション全体でフラットな `sys.modules` 名前空間を
共有する。素朴な `from synthetic import ...`（bare import）を書くと、
コレクション順序次第でどのファイルが `sys.modules["synthetic"]` に入るかが
決まり、**他 Spec のテストを実際に壊す**（`flying-object-tracking` の
タスク 1.5 が実際に踏んだ事故であり、仮定の懸念ではない）。

`tests/flying_object_tracking/conftest.py` が確立した回避策を踏襲すること:
`importlib.util.spec_from_file_location` でファイルパスから直接、衝突しない
一意な名前（`_m1_validation_tests.<basename>`）でロードし、フィクスチャと
してモジュールオブジェクトを渡す。**衝突を避ける側は常に後から入る Spec
——すなわち本 Spec——が持つ**（`tests/sensing_foundation/**` と
`tests/flying_object_tracking/**` はどちらも本 Spec の変更対象外である）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_output_dir(tmp_path: Path) -> Path:
    """本 Spec の出力（記録・真値・レポート・図）の書き出し先となる一時ディレクトリ。

    実運用の既定出力先は `var/m1/` であり、既存の `.gitignore` の `var/`
    規則の傘に入る（`test_m1_package_skeleton.py` が根拠を固定している）。
    テストは実体の `var/` を汚さないよう、必ず本フィクスチャを経由すること。

    出力レイアウトの規約はまだ存在しないため、現時点では単純な一時
    ディレクトリの払い出しのみを行う。
    """
    output_dir = tmp_path / "m1"
    output_dir.mkdir()
    return output_dir
