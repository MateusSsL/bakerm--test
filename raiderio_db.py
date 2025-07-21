import aiosqlite
from datetime import datetime
from typing import Optional, Tuple, List

DB_NAME = "raiderio.db"

async def inicializar_banco(db_conn: aiosqlite.Connection) -> None:
    """Cria a tabela se não existir"""
    await db_conn.execute("""
    CREATE TABLE IF NOT EXISTS jogadores (
        user_id TEXT PRIMARY KEY,
        nome TEXT,
        funcao TEXT,
        armadura TEXT,
        disponibilidade INTEGER DEFAULT 0,
        raiderio_url TEXT,
        raiderio_score REAL,
        personagem_nome TEXT,  # Novo campo
        personagem_classe TEXT,  # Novo campo
        ultima_atualizacao TEXT
    )
""")
    await db_conn.commit()

async def buscar_perfil(db_conn: aiosqlite.Connection, user_id: str) -> Optional[Tuple]:
    """Busca perfil do jogador"""
    cursor = await db_conn.execute("""
        SELECT nome, funcao, armadura, disponibilidade, raiderio_url, raiderio_score, ultima_atualizacao
        FROM jogadores WHERE user_id = ?
    """, (user_id,))
    return await cursor.fetchone()

async def buscar_disponiveis(db_conn: aiosqlite.Connection) -> List[Tuple]:
    """Lista jogadores disponíveis"""
    cursor = await db_conn.execute("""
        SELECT user_id, nome, funcao, armadura, raiderio_score
        FROM jogadores
        WHERE disponibilidade = 1
        ORDER BY raiderio_score DESC
    """)
    return await cursor.fetchall()

async def atualizar_raiderio(
    db_conn: aiosqlite.Connection,
    user_id: str,
    nome: str,
    url: str,
    score: float
) -> bool:
    """Atualiza dados do Raider.IO com validação de data"""
    hoje = datetime.utcnow().date().isoformat()
    cursor = await db_conn.execute(
        "SELECT ultima_atualizacao FROM jogadores WHERE user_id = ?", 
        (user_id,)
    )
    row = await cursor.fetchone()
    
    if row and row[0] == hoje:
        return False
        
    await db_conn.execute("""
        INSERT INTO jogadores (user_id, nome, raiderio_url, raiderio_score, ultima_atualizacao)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            nome=excluded.nome,
            raiderio_url=excluded.raiderio_url,
            raiderio_score=excluded.raiderio_score,
            ultima_atualizacao=excluded.ultima_atualizacao
    """, (user_id, nome, url, score, hoje))
    await db_conn.commit()
    return True