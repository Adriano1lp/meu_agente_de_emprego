from services.main_rag import rebuild_vectorstore


if __name__ == "__main__":
    resultado = rebuild_vectorstore()
    print(resultado)
