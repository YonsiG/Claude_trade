"""让 pytest 能直接 import 项目模块。

项目内部用的是扁平导入（`from data.loader import load`），依赖工作目录是
Claude_trade/。放一个根级 conftest.py 把项目根塞进 sys.path，这样在任何
目录下执行 `pytest` 都能跑。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
