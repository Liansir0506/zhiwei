"""qa_core 包初始化。

⚠️ 本文件必须在任何 transformers/sentence_transformers import 之前执行：
本机（Windows + anaconda venv）的 sentencepiece 0.2.2 wheel 在 import 时直接
segfault（进程级崩溃，非 Python 异常，无法 try/except）。transformers 的
models/__init__ → mt5 → t5 链条会模块级 `import sentencepiece` 并连带崩溃。
此处无条件用 stub 占位 sys.modules。fast tokenizer（tokenizer.json）路径完全不
依赖 sentencepiece，stub 无副作用。

逃生门：环境变量 TELECOM_RAG_ALLOW_SENTENCEPIECE=1 表示本机 sentencepiece 可正常
import（其他机器部署时如有需要），跳过 stub 使用真库。
"""
import importlib.machinery
import os
import sys
import types

if os.environ.get("TELECOM_RAG_ALLOW_SENTENCEPIECE") != "1":
    _stub = types.ModuleType("sentencepiece")
    _stub.SentencePieceProcessor = None
    _stub.SentencePieceTrainer = None
    _stub.__version__ = "stub"
    _stub.__spec__ = importlib.machinery.ModuleSpec("sentencepiece", None)
    _stub.__path__ = []
    sys.modules["sentencepiece"] = _stub
