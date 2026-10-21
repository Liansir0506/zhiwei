"""r2 修订服务器壳：装载 r2 运行时与检索适配器（避免旧壳经历史层陈旧校验）。"""
from scripts import demo_release_runtime_r2 as release


def install_retrieval():
    from qa_core.application import qa_usecase
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
    from qa_core.retrieval.supported_evidence_v2 import relevant_docs
    qa_usecase.Searcher = SupportedFaqSearcher
    qa_usecase.relevant_docs = relevant_docs


def main():
    if not release.SELECTION.exists():
        raise ValueError('approved r2 amendment selection required')
    release.install_adapter()
    install_retrieval()
    # original_local_server 直接引用 original_local_runtime.build_profile（import 绑定的本地名），
    # 在 r2 链上以模块属性替换为 r2 的 selected_settings（与历史安装器的接管机制一致）。
    from scripts import original_local_server as server
    server.build_profile = release.selected_settings
    server.main()


if __name__ == '__main__':
    main()
