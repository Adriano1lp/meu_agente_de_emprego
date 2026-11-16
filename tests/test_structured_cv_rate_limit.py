"""Cota persistente do CV estruturado e estorno pelo token da reserva."""

from __future__ import annotations

import copy
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError


def _criar_usuario(user_id: str) -> None:
    from database.repository import create_user

    agora = "2026-01-01T00:00:00+00:00"
    create_user(
        {
            "user_id": user_id,
            "email": f"{user_id}@example.com",
            "display_name": user_id,
            "password_hash": "hash",
            "terms_accepted": 1,
            "terms_accepted_at": agora,
            "terms_version": "1.0",
            "privacy_accepted": 1,
            "privacy_accepted_at": agora,
            "privacy_version": "1.0",
            "created_at": agora,
            "updated_at": agora,
        }
    )


def _tokens(db_path, user_id: str) -> set[str]:
    with sqlite3.connect(db_path) as connection:
        linhas = connection.execute(
            """
            SELECT token
            FROM structured_cv_write_reservations
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    return {linha[0] for linha in linhas}


def _casa(documento: dict, filtro: dict) -> bool:
    return all(documento.get(chave) == valor for chave, valor in filtro.items())


class _ResultadoMongo:
    def __init__(self, matched_count: int = 0, deleted_count: int = 0):
        self.matched_count = matched_count
        self.modified_count = matched_count
        self.deleted_count = deleted_count


class _ColecaoMongo:
    def __init__(self):
        self.docs: list[dict] = []
        self.lock = threading.Lock()

    def find_one(self, filtro):
        with self.lock:
            for documento in self.docs:
                if _casa(documento, filtro):
                    return copy.deepcopy(documento)
        return None

    def insert_one(self, documento):
        with self.lock:
            if any(item.get("user_id") == documento.get("user_id") for item in self.docs):
                raise DuplicateKeyError("user_id duplicado")
            self.docs.append(copy.deepcopy(documento))

    def update_one(self, filtro, update, upsert=False):
        with self.lock:
            for documento in self.docs:
                if _casa(documento, filtro):
                    for chave, valor in (update.get("$set") or {}).items():
                        documento[chave] = copy.deepcopy(valor)
                    for chave, valor in (update.get("$inc") or {}).items():
                        documento[chave] = int(documento.get(chave) or 0) + int(valor)
                    return _ResultadoMongo(matched_count=1)
        return _ResultadoMongo()

    def delete_one(self, filtro):
        with self.lock:
            for indice, documento in enumerate(self.docs):
                if _casa(documento, filtro):
                    del self.docs[indice]
                    return _ResultadoMongo(deleted_count=1)
        return _ResultadoMongo()


class _BancoMongo:
    def __init__(self):
        self.colecoes: dict[str, _ColecaoMongo] = {}
        self.lock = threading.Lock()

    def colecao(self, nome: str) -> _ColecaoMongo:
        with self.lock:
            if nome not in self.colecoes:
                self.colecoes[nome] = _ColecaoMongo()
            return self.colecoes[nome]


def _usar_mongo(monkeypatch: pytest.MonkeyPatch) -> _BancoMongo:
    from database import mongo_repository

    banco = _BancoMongo()
    monkeypatch.setattr("database.repository.PERSISTENCE_BACKEND", "mongodb")
    monkeypatch.setattr(mongo_repository, "is_configured", lambda: True)
    monkeypatch.setattr(mongo_repository, "_get_collection", banco.colecao)
    return banco


def _tokens_mongo(banco: _BancoMongo, user_id: str) -> set[str]:
    documento = banco.colecao("structured_cv_write_reservations").find_one(
        {"user_id": user_id},
    )
    if not documento:
        return set()
    return {item["token"] for item in documento.get("reservations") or []}


def test_migracao_da_cota_e_idempotente(isolated_db):
    from database.repository import initialize_database

    initialize_database(isolated_db)
    initialize_database(isolated_db)
    with sqlite3.connect(isolated_db) as connection:
        tabela = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'structured_cv_write_reservations'
            """,
        ).fetchone()
        migracoes = connection.execute(
            """
            SELECT COUNT(*) FROM schema_migrations
            WHERE version = 8 AND name = 'structured_cv_write_reservations'
            """,
        ).fetchone()[0]
        colunas = [
            linha[1]
            for linha in connection.execute(
                "PRAGMA table_info(structured_cv_write_reservations)",
            )
        ]
    assert tabela is not None
    assert migracoes == 1
    assert colunas == ["token", "user_id", "expires_at", "created_at"]


def test_estorno_devolve_so_o_token_da_reserva(isolated_db, monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "2")
    from services.structured_cv_rate_limit import (
        consume_structured_cv_write,
        release_structured_cv_write,
    )

    _criar_usuario("ana")
    _criar_usuario("bruno")
    token_a = consume_structured_cv_write("ana")
    token_b = consume_structured_cv_write("ana")
    assert isinstance(token_a, str)
    assert isinstance(token_b, str)
    assert token_a != token_b
    assert isinstance(consume_structured_cv_write("ana"), int)

    release_structured_cv_write("ana", token_a)
    assert _tokens(isolated_db, "ana") == {token_b}

    token_c = consume_structured_cv_write("ana")
    assert isinstance(token_c, str)
    release_structured_cv_write("ana", token_a)
    assert _tokens(isolated_db, "ana") == {token_b, token_c}
    assert isinstance(consume_structured_cv_write("ana"), int)

    release_structured_cv_write("bruno", token_b)
    assert _tokens(isolated_db, "ana") == {token_b, token_c}
    assert isinstance(consume_structured_cv_write("bruno"), str)

    release_structured_cv_write("ana", token_b)
    assert _tokens(isolated_db, "ana") == {token_c}
    release_structured_cv_write("ana", token_b)
    assert _tokens(isolated_db, "ana") == {token_c}


def test_janela_de_uma_hora_e_retry_after_da_mais_antiga(isolated_db, monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "2")
    import services.structured_cv_rate_limit as cota

    _criar_usuario("janela")
    inicio = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    instante = {"agora": inicio}
    monkeypatch.setattr(cota, "_agora", lambda: instante["agora"])

    primeiro = cota.consume_structured_cv_write("janela")
    instante["agora"] = inicio + timedelta(seconds=100)
    segundo = cota.consume_structured_cv_write("janela")
    assert isinstance(primeiro, str)
    assert isinstance(segundo, str)

    espera = cota.consume_structured_cv_write("janela")
    assert espera == 3500

    instante["agora"] = inicio + timedelta(seconds=3600)
    liberada = cota.consume_structured_cv_write("janela")
    assert isinstance(liberada, str)
    assert _tokens(isolated_db, "janela") == {segundo, liberada}

    instante["agora"] = inicio + timedelta(seconds=100 + 3600)
    depois = cota.consume_structured_cv_write("janela")
    assert isinstance(depois, str)


def test_reserva_expira_no_segundo_exato(isolated_db, monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "1")
    import services.structured_cv_rate_limit as cota

    _criar_usuario("limite-um")
    inicio = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    instante = {"agora": inicio}
    monkeypatch.setattr(cota, "_agora", lambda: instante["agora"])
    assert isinstance(cota.consume_structured_cv_write("limite-um"), str)

    instante["agora"] = inicio + timedelta(seconds=3599)
    assert cota.consume_structured_cv_write("limite-um") == 1

    instante["agora"] = inicio + timedelta(seconds=3600)
    assert isinstance(cota.consume_structured_cv_write("limite-um"), str)


def test_reinicio_e_duas_conexoes_mantem_as_dez_reservas(isolated_db, monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "10")
    from services.structured_cv_rate_limit import (
        consume_structured_cv_write,
        reset_structured_cv_rate_limits,
    )

    _criar_usuario("dez")
    tokens = [consume_structured_cv_write("dez") for _ in range(10)]
    assert all(isinstance(item, str) for item in tokens)
    reset_structured_cv_rate_limits()

    with sqlite3.connect(isolated_db) as primeira, sqlite3.connect(isolated_db) as segunda:
        for conexao in (primeira, segunda):
            total = conexao.execute(
                """
                SELECT COUNT(*) FROM structured_cv_write_reservations
                WHERE user_id = ?
                """,
                ("dez",),
            ).fetchone()[0]
            assert total == 10

    espera = consume_structured_cv_write("dez")
    assert isinstance(espera, int)
    assert 1 <= espera <= 3600
    assert len(_tokens(isolated_db, "dez")) == 10


def test_reservas_concorrentes_nao_passam_do_limite(isolated_db, monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "10")
    from services.structured_cv_rate_limit import consume_structured_cv_write

    for rodada in range(5):
        usuario = f"corrida-{rodada}"
        _criar_usuario(usuario)
        erros: list[BaseException] = []
        resultados: list[str | int] = []
        trava = threading.Lock()

        def trabalhar(usuario: str = usuario) -> None:
            try:
                valor = consume_structured_cv_write(usuario)
            except Exception as exc:
                with trava:
                    erros.append(exc)
                return
            with trava:
                resultados.append(valor)

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(lambda _: trabalhar(), range(20)))

        assert erros == []
        tokens = [item for item in resultados if isinstance(item, str)]
        esperas = [item for item in resultados if isinstance(item, int)]
        assert len(tokens) == 10
        assert len(esperas) == 10
        assert len(set(tokens)) == 10
        assert all(item >= 1 for item in esperas)
        assert _tokens(isolated_db, usuario) == set(tokens)
        assert isinstance(consume_structured_cv_write(usuario), int)


def test_estorno_mongo_devolve_so_o_token_da_reserva(monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "2")
    from services.structured_cv_rate_limit import (
        consume_structured_cv_write,
        release_structured_cv_write,
    )

    banco = _usar_mongo(monkeypatch)
    token_a = consume_structured_cv_write("ana")
    token_b = consume_structured_cv_write("ana")
    assert isinstance(token_a, str)
    assert isinstance(token_b, str)
    assert isinstance(consume_structured_cv_write("ana"), int)

    release_structured_cv_write("ana", token_a)
    assert _tokens_mongo(banco, "ana") == {token_b}

    token_c = consume_structured_cv_write("ana")
    assert isinstance(token_c, str)
    release_structured_cv_write("ana", token_a)
    assert _tokens_mongo(banco, "ana") == {token_b, token_c}
    assert isinstance(consume_structured_cv_write("ana"), int)

    release_structured_cv_write("bruno", token_b)
    assert _tokens_mongo(banco, "ana") == {token_b, token_c}
    assert isinstance(consume_structured_cv_write("bruno"), str)

    release_structured_cv_write("ana", token_b)
    assert _tokens_mongo(banco, "ana") == {token_c}
    release_structured_cv_write("ana", token_b)
    assert _tokens_mongo(banco, "ana") == {token_c}
    assert banco.colecao("structured_cv_write_reservations").docs


def test_retry_after_mongo_usa_a_reserva_mais_antiga(monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "2")
    import services.structured_cv_rate_limit as cota

    banco = _usar_mongo(monkeypatch)
    inicio = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    instante = {"agora": inicio}
    monkeypatch.setattr(cota, "_agora", lambda: instante["agora"])
    assert isinstance(cota.consume_structured_cv_write("ana"), str)
    instante["agora"] = inicio + timedelta(seconds=100)
    assert isinstance(cota.consume_structured_cv_write("ana"), str)
    assert cota.consume_structured_cv_write("ana") == 3500

    documento = banco.colecao("structured_cv_write_reservations").find_one({"user_id": "ana"})
    assert documento is not None
    assert documento["expires_at"] == max(
        item["expires_at"] for item in documento["reservations"]
    )
    assert len(documento["reservations"]) == 2


def test_reservas_concorrentes_no_mongo_nao_passam_do_limite(monkeypatch):
    monkeypatch.setenv("STRUCTURED_CV_RATE_LIMIT_PER_HOUR", "10")
    from services.structured_cv_rate_limit import consume_structured_cv_write

    banco = _usar_mongo(monkeypatch)
    for rodada in range(5):
        usuario = f"ana-{rodada}"
        erros: list[BaseException] = []
        resultados: list[str | int] = []
        trava = threading.Lock()

        def trabalhar(usuario: str = usuario) -> None:
            try:
                valor = consume_structured_cv_write(usuario)
            except Exception as exc:
                with trava:
                    erros.append(exc)
                return
            with trava:
                resultados.append(valor)

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(lambda _: trabalhar(), range(20)))

        assert erros == []
        tokens = [item for item in resultados if isinstance(item, str)]
        esperas = [item for item in resultados if isinstance(item, int)]
        assert len(tokens) == 10
        assert len(esperas) == 10
        assert _tokens_mongo(banco, usuario) == set(tokens)
        assert isinstance(consume_structured_cv_write(usuario), int)
