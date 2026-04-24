from __future__ import annotations

from langchain_community.document_loaders import TextLoader
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import CHROMA_DB_DIR, CV_FILE, OPENAI_EMBEDDING_MODEL, ensure_openai_api_key

OPENAI_API_KEY = ensure_openai_api_key()


def rebuild_vectorstore() -> None:
    if not CV_FILE.exists():
        raise FileNotFoundError(f"Arquivo de curriculo nao encontrado em: {CV_FILE}")

    embeddings = OpenAIEmbeddings(
        model=OPENAI_EMBEDDING_MODEL,
        openai_api_key=OPENAI_API_KEY,
    )

    loader = TextLoader(str(CV_FILE), encoding="utf-8")
    documentos = loader.load()

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=700,
        chunk_overlap=200,
        separators=["\n\n", "\n", " ", ""],
        length_function=len,
    )
    txt_chunks = text_splitter.split_documents(documentos)
    txt_chunks = [doc for doc in txt_chunks if len(doc.page_content.strip()) > 10]

    for index, chunk in enumerate(txt_chunks):
        chunk.metadata = {"id": index, "source": str(CV_FILE.name)}

    Chroma.from_documents(
        txt_chunks,
        embedding=embeddings,
        persist_directory=str(CHROMA_DB_DIR),
    )


if __name__ == "__main__":
    rebuild_vectorstore()
