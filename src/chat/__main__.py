"""chat 子进程入口：python -m src.chat

子进程化的目的（ISS-060 遗留）：chat 退出即由 OS 整体回收全部内存，
包括 torch/faiss 等库的导入开销（实测 ~440MB，Python 进程内无法卸载模块）。
start.py 通过 subprocess 调用本入口，主进程永不 import chat/RAG/torch。
"""
import os
import sys


def main():
    # 与 start.py 一致的代理剥离（金融/API直连）+ UTF-8
    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
              "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        from src.chat.agent import run_chat_repl
        from src.cli.main import load_config
    except ImportError as e:
        print(f"\n  [!] Chat 依赖缺失: {e}")
        print("  [!] 请先安装依赖（事实源 requirements.txt）: pip install -r requirements.txt")
        print("  [!] 或最小补装: pip install openai jieba faiss-cpu sentence-transformers\n")
        return 1

    run_chat_repl(load_config())
    return 0


if __name__ == "__main__":
    sys.exit(main())
