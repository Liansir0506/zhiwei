"""Approved successor server installs retrieval adapters only in this process."""
from scripts import content_quality_release_runtime as release


def install_retrieval():
    from qa_core.application import qa_usecase
    from qa_core.retrieval.supported_searcher import SupportedFaqSearcher
    from qa_core.retrieval.supported_evidence_v2 import relevant_docs
    qa_usecase.Searcher=SupportedFaqSearcher
    qa_usecase.relevant_docs=relevant_docs


def main():
    if not release.SELECTION.exists():raise ValueError('approved successor selection required')
    release.install_adapter()
    install_retrieval()
    from scripts.original_local_server import main as serve
    serve()


if __name__=='__main__':main()
