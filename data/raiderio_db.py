import aiosqlite
from datetime import datetime
from typing import Optional, Tuple, List

DB_NAME = "raiderio.db"

async def inicializar_banco(db_conn: aiosqlite.Connection) -> None:
    """Cria a tabela se não existir (estrutura compatível com o bot)"""
    await db_conn.execute("""
    CREATE TABLE IF NOT EXISTS jogadores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        nome TEXT,
        funcao TEXT,
        armadura TEXT,
        disponibilidade INTEGER DEFAULT 0,
        raiderio_url TEXT,
        raiderio_score REAL,
        personagem_nome TEXT,
        personagem_classe TEXT,
        personagem_server TEXT,
        ultima_atualizacao TEXT,
        UNIQUE(user_id, personagem_nome)
    )
    """)
    await db_conn.commit()

async def buscar_perfis_usuario(db_conn: aiosqlite.Connection, user_id: str) -> List[Tuple]:
    """Busca todos os personagens de um usuário"""
    cursor = await db_conn.execute("""
        SELECT personagem_nome, funcao, armadura, disponibilidade, raiderio_url, raiderio_score, personagem_classe, personagem_server, ultima_atualizacao
        FROM jogadores WHERE user_id = ?
    """, (user_id,))
    return await cursor.fetchall()

async def buscar_disponiveis(db_conn: aiosqlite.Connection) -> List[Tuple]:
    """Lista todos os personagens disponíveis"""
    cursor = await db_conn.execute("""
        SELECT user_id, nome, funcao, personagem_classe, raiderio_score, personagem_nome, personagem_server
        FROM jogadores
        WHERE disponibilidade = 1
        ORDER BY raiderio_score DESC
    """)
    return await cursor.fetchall()

async def atualizar_raiderio(
    db_conn: aiosqlite.Connection,
    user_id: str,
    personagem_nome: str,
    url: str,
    score: float
) -> bool:
    """Atualiza dados do Raider.IO para um personagem específico"""
    hoje = datetime.utcnow().date().isoformat()
    cursor = await db_conn.execute(
        "SELECT ultima_atualizacao FROM jogadores WHERE user_id = ? AND personagem_nome = ?",
        (user_id, personagem_nome)
    )
    row = await cursor.fetchone()

    if row and row[0] == hoje:
        return False

    await db_conn.execute("""
        UPDATE jogadores
        SET raiderio_url = ?, raiderio_score = ?, ultima_atualizacao = ?
        WHERE user_id = ? AND personagem_nome = ?
    """, (url, score, hoje, user_id, personagem_nome))
    await db_conn.commit()
    return True