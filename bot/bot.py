import discord
import os
import asyncio
import aiosqlite
import aiohttp
from datetime import datetime, timedelta
from discord.ext import commands
from discord.ui import View, Select, Modal, TextInput, Button
from dotenv import load_dotenv
from typing import Optional, Dict, Set
from discord import app_commands
from time import time
import weakref
import gc
from raiderio_api import obter_score_raiderio
from mensagens import (
    BOAS_VINDAS, CADASTRO_SUCESSO, ERRO_CADASTRO, LIMITE_PERSONAGENS, FUNCAO_INVALIDA,
    RATE_LIMIT, PERSONAGEM_EXISTENTE, RAIDERIO_INVALIDO, PERFIL_VAZIO, CADASTRO_EM_ANDAMENTO,
    CADASTRO_CANCELADO, PERSONAGEM_REMOVIDO, PERSONAGEM_NAO_ENCONTRADO, ERRO_GERAL,
    ERRO_ATUALIZAR_RAIDERIO, ERRO_ATUALIZAR_DISPONIBILIDADE, ERRO_DELETAR_PERSONAGEM,
    ERRO_CARREGAR_PERSONAGEM, ERRO_INICIAR_CADASTRO, SISTEMA_SOBRECARGADO, CADASTRO_CONCLUIDO,
    MUITAS_INTERACOES, SESSAO_EXPIRADA, SEM_PERMISSAO_VIEW, AGUARDE_BOTAO, AGUARDE_RAIDERIO,
    NAO_POSSIVEL_ATUALIZAR_SCORE, LINK_RAIDERIO_NAO_ENCONTRADO
)

INSTRUCOES_CANAL_ID = 1394566723448995982
BOASVINDAS_MSG_ID_FILE = "bot/mensagens/boasvindas_msg_id.txt"
# Carrega variáveis de ambiente
load_dotenv()
RAIDERIO_COOLDOWN_SECONDS = 300
BUTTON_COOLDOWN_SECONDS = 30
MAX_ATTEMPTS_PER_HOUR = 5  # Máximo de tentativas por hora
MAX_ACTIVE_VIEWS = 50  # Máximo de views ativas por vez

# Dicionários para controle
raiderio_cooldowns = {}
button_cooldowns = {}
active_cadastros = {}
failed_attempts = {}  # user_id: [(timestamp, tipo_falha), ...]
active_views_count = 0
view_registry = weakref.WeakSet()  # Registro fraco para cleanup automático

# --- FUNÇÕES DE SEGURANÇA ---

def limpar_cooldowns_expirados():
    """Remove cooldowns expirados para liberar memória"""
    now = time()
    
    # Limpa cooldowns do Raider.IO expirados
    expired_keys = [k for k, v in raiderio_cooldowns.items() if now - v > RAIDERIO_COOLDOWN_SECONDS]
    for key in expired_keys:
        del raiderio_cooldowns[key]
    
    # Limpa cooldowns de botões expirados
    expired_keys = [k for k, v in button_cooldowns.items() if now - v > BUTTON_COOLDOWN_SECONDS]
    for key in expired_keys:
        del button_cooldowns[key]
    
    # Limpa tentativas falhadas antigas (mais de 1 hora)
    for user_id in list(failed_attempts.keys()):
        failed_attempts[user_id] = [
            (timestamp, tipo) for timestamp, tipo in failed_attempts[user_id]
            if now - timestamp < 3600
        ]
        if not failed_attempts[user_id]:
            del failed_attempts[user_id]

def registrar_tentativa_falhada(user_id: int, tipo_falha: str) -> bool:
    """Registra tentativa falhada e verifica se usuário excedeu limite"""
    now = time()
    if user_id not in failed_attempts:
        failed_attempts[user_id] = []
    
    failed_attempts[user_id].append((now, tipo_falha))
    
    # Remove tentativas antigas (mais de 1 hora)
    failed_attempts[user_id] = [
        (timestamp, tipo) for timestamp, tipo in failed_attempts[user_id]
        if now - timestamp < 3600
    ]
    
    return len(failed_attempts[user_id]) >= MAX_ATTEMPTS_PER_HOUR

def validar_entrada_usuario(texto: str, max_len: int = 100) -> str:
    """Valida e sanitiza entrada do usuário"""
    if not texto or not isinstance(texto, str):
        raise ValueError("Entrada inválida")
    
    # Remove caracteres perigosos
    texto = texto.strip()[:max_len]
    
    # Remove caracteres de controle
    texto = ''.join(char for char in texto if ord(char) >= 32 or char in '\n\t')
    
    return texto

async def verificar_rate_limit(user_id: int, acao: str) -> bool:
    """Verifica se usuário está sendo rate limited"""
    key = f"{user_id}:{acao}"
    now = time()
    
    if key not in raiderio_cooldowns:
        return False
        
    return now - raiderio_cooldowns[key] < RAIDERIO_COOLDOWN_SECONDS

# Add this function near the top of your file with other helper functions
def get_armor_type(class_name: str) -> str:
    """
    Determines armor type based on character class
    """
    cloth_classes = ["Mage", "Priest", "Warlock"]
    leather_classes = ["Demon Hunter", "Druid", "Monk", "Rogue"]
    mail_classes = ["Hunter", "Shaman", "Evoker"]
    plate_classes = ["Death Knight", "Paladin", "Warrior"]
    
    if class_name in cloth_classes:
        return "Cloth"
    elif class_name in leather_classes:
        return "Leather"
    elif class_name in mail_classes:
        return "Mail"
    elif class_name in plate_classes:
        return "Plate"
    else:
        return "Unknown"

# --- CLASSES DE VIEW COM PROTEÇÃO MELHORADA ---

class PrivateView(View):
    """Classe base para todas as views privadas com proteção melhorada"""
    def __init__(self, interaction):
        super().__init__(timeout=300)
        self.autor_id = interaction.user.id
        self.criado_em = time()
        self.interacoes_count = 0
        self.max_interacoes = 20  # Máximo de interações por view
        
        # Registra a view para cleanup
        view_registry.add(self)
        global active_views_count
        active_views_count += 1
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Verifica ownership
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "🚫 Você não pode interagir com este menu! Use `/cadastrar` para iniciar seu próprio cadastro.",
                ephemeral=True
            )
            return False
        
        # Verifica rate limiting
        self.interacoes_count += 1
        if self.interacoes_count > self.max_interacoes:
            await interaction.response.send_message(
                "⚠️ Muitas interações. Por favor, reinicie o processo.",
                ephemeral=True
            )
            return False
        
        # Verifica se view não está muito antiga
        if time() - self.criado_em > 600:  # 10 minutos
            await interaction.response.send_message(
                "⏰ Esta sessão expirou. Por favor, inicie novamente.",
                ephemeral=True
            )
            return False
        
        return True
    
    def stop(self):
        super().stop()
        global active_views_count
        active_views_count = max(0, active_views_count - 1)

class CadastroView(PrivateView):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(interaction)
        self.interaction = interaction
        self.user_id = str(interaction.user.id)
        self.nome = interaction.user.display_name
        
        # Atributos que serão preenchidos durante o cadastro
        self.personagem_nome = None
        self.personagem_classe = None
        self.funcao = None
        self.armadura = None
        self.raiderio_url = None
        self.raiderio_score = None

    @discord.ui.button(label="📝 Iniciar Cadastro", style=discord.ButtonStyle.primary)
    async def iniciar_cadastro(self, interaction: discord.Interaction, button: Button):
        try:
            # Verifica limite de personagens
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM jogadores WHERE user_id = ?",
                    (self.user_id,)
                )
                count = (await cursor.fetchone())[0]
                
                if count >= 4:
                    return await interaction.response.send_message(
                        LIMITE_PERSONAGENS,
                        ephemeral=True
                    )

            # Abre modal de cadastro
            modal = CadastroModal(self)
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"[ERRO INICIAR_CADASTRO] {e}")
            await interaction.response.send_message(
                ERRO_INICIAR_CADASTRO,
                ephemeral=True
            )

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        # Desabilita os botões
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass  # Ignora erro se a mensagem não existir mais

        await interaction.response.send_message(
            CADASTRO_CANCELADO,
            ephemeral=True
        )
        active_cadastros.pop(interaction.user.id, None)

class CadastroModal(Modal, title="Cadastro de Personagem"):
    def __init__(self, cadastro_view):
        super().__init__()
        self.cadastro_view = cadastro_view
        
        self.nick_input = TextInput(
            label="Nick do personagem",
            placeholder="Ex: Arthas",
            required=True,
            max_length=50
        )
        
        self.funcao_input = TextInput(
            label="Função (Tank/Healer/DPS)",
            placeholder="Digite: Tank, Healer ou DPS",
            required=True,
            max_length=6
        )
        
        self.raiderio_input = TextInput(
            label="Link do Raider.IO",
            placeholder="Ex: https://raider.io/characters/us/azralon/Arthas",
            required=True,
            max_length=200
        )
        
        self.add_item(self.nick_input)
        self.add_item(self.funcao_input)
        self.add_item(self.raiderio_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Validar entrada
            nick = validar_entrada_usuario(self.nick_input.value)
            nick = nick.capitalize()  # ou nick.title() para cada palavra
            
            # Normaliza a função para aceitar qualquer formato
            funcao = self.funcao_input.value.lower().strip()
            if funcao in ["tank", "tanker", "tk"]:
                funcao = "Tank"
            elif funcao in ["healer", "heal", "hl"]:
                funcao = "Healer"
            elif funcao in ["dps", "damage", "dd"]:
                funcao = "DPS"
            else:
                return await interaction.response.send_message(
                    FUNCAO_INVALIDA,
                    ephemeral=True
                )
                
            raiderio_url = validar_entrada_usuario(self.raiderio_input.value)
            
            # Validar função
            if funcao not in ["Tank", "Healer", "DPS"]:
                return await interaction.response.send_message(
                    FUNCAO_INVALIDA,
                    ephemeral=True
                )
            
            # Verificar rate limit
            if await verificar_rate_limit(interaction.user.id, "cadastro"):
                return await interaction.response.send_message(
                    RATE_LIMIT,
                    ephemeral=True
                )
            
            # Verificar limite de personagens
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT COUNT(*) FROM jogadores WHERE user_id = ?",
                    (str(interaction.user.id),)
                )
                count = (await cursor.fetchone())[0]
                if count >= 4:  # Permite até 4 personagens
                    return await interaction.response.send_message(
                        LIMITE_PERSONAGENS,
                        ephemeral=True
                    )
                
                # Verificar se personagem já existe
                cursor = await db.execute(
                    "SELECT user_id FROM jogadores WHERE LOWER(personagem_nome) = LOWER(?)",
                    (nick,)
                )
                existing = await cursor.fetchone()
                if existing and str(existing[0]) != str(interaction.user.id):
                    return await interaction.response.send_message(
                        PERSONAGEM_EXISTENTE,
                        ephemeral=True
                    )
            
            # Validar com Raider.IO e obter score atual
            score, classe, server = await obter_score_raiderio(raiderio_url)
            if score is None or classe is None:
                return await interaction.response.send_message(
                    RAIDERIO_INVALIDO,
                    ephemeral=True
                )

            armadura = get_armor_type(classe)

            # Update the confirmation embed to show armor type
            embed = discord.Embed(
                title="📝 Confirmar Cadastro",
                description="Verifique os dados antes de confirmar:",
                color=discord.Color.blue()
            )
            embed.add_field(name="Personagem", value=nick, inline=True)
            embed.add_field(name="Classe", value=classe, inline=True)
            embed.add_field(name="Função", value=funcao, inline=True)
            embed.add_field(name="Armadura", value=f"{armadura}", inline=True)  # Added this line
            embed.add_field(name="Score M+", value=f"{score:.1f}", inline=True)
            embed.add_field(name="Raider.IO", value=f"[Link]({raiderio_url})", inline=False)
            embed.add_field(name="Servidor", value=server, inline=True)

            # Store armor type in cadastro_view
            self.cadastro_view.armadura = armadura
            self.cadastro_view.personagem_nome = nick
            self.cadastro_view.personagem_classe = classe
            self.cadastro_view.funcao = funcao
            self.cadastro_view.raiderio_url = raiderio_url
            self.cadastro_view.raiderio_score = score
            self.cadastro_view.personagem_server = server  # <-- ADICIONE ESTA LINHA

            await interaction.response.send_message(
                embed=embed,
                view=ConfirmarCadastroView(interaction, self.cadastro_view),
                ephemeral=True
            )
            
        except Exception as e:
            print(f"[ERRO CADASTRO] {e}")
            await interaction.response.send_message(
                ERRO_GERAL,
                ephemeral=True
            )

class ConfirmarCadastroView(View):
    def __init__(self, interaction, cadastro_view):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.cadastro_view = cadastro_view
        self.confirmado = False

    @discord.ui.button(label="✅ Confirmar Cadastro", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: Button):
        if self.confirmado:
            return await interaction.response.send_message(
                "❌ Cadastro já foi processado.", 
                ephemeral=True
            )
        self.confirmado = True

        try:
            # Primeiro desabilita os botões
            for item in self.children:
                item.disabled = True
            try:
                await interaction.message.edit(view=self)
            except Exception:
                pass  # Ignora erro se a mensagem não existir mais

            # Insere no banco de dados
            async with aiosqlite.connect("data/raiderio.db") as db:
                await db.execute("""
                    INSERT INTO jogadores 
                    (user_id, nome, funcao, armadura, raiderio_url, raiderio_score, 
                     personagem_nome, personagem_classe, personagem_server, disponibilidade, ultima_atualizacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
                """, (
                    self.cadastro_view.user_id,
                    self.cadastro_view.nome,
                    self.cadastro_view.funcao,
                    self.cadastro_view.armadura,
                    self.cadastro_view.raiderio_url,
                    self.cadastro_view.raiderio_score,
                    self.cadastro_view.personagem_nome,
                    self.cadastro_view.personagem_classe,
                    self.cadastro_view.personagem_server  # NOVO
                ))
                await db.commit()

            # Envia mensagem de sucesso
            embed = discord.Embed(
                title="✅ Cadastro Concluído!",
                description=(
                    f"**Personagem:** {self.cadastro_view.personagem_nome}\n"
                    f"**Classe:** {self.cadastro_view.personagem_classe}\n"
                    f"**Função:** {self.cadastro_view.funcao}\n"
                    f"**Score M+:** {int(self.cadastro_view.raiderio_score)}\n\n"
                    "Use `/perfil` para gerenciar seu personagem."
                ),
                color=discord.Color.green()
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Limpa o cadastro ativo
            active_cadastros.pop(interaction.user.id, None)
            
        except Exception as e:
            self.confirmado = False
            print(f"[ERRO NO CADASTRO] {e}")
            await interaction.response.send_message(
                "❌ **Erro ao completar cadastro.** Tente novamente.",
                ephemeral=True
            )

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        # Desabilita os botões
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass  # Ignora erro se a mensagem não existir mais
        
        await interaction.response.send_message(
            CADASTRO_CANCELADO,
            ephemeral=True
        )
        active_cadastros.pop(interaction.user.id, None)

class GerenciarPersonagemView(View):
    def __init__(self, personagem_nome):
        super().__init__(timeout=60)
        self.personagem_nome = validar_entrada_usuario(personagem_nome, 50)

    async def _check_cooldown(self, interaction, acao):
        key = (interaction.user.id, self.personagem_nome.lower(), acao)
        now = time()
        last = button_cooldowns.get(key, 0)
        if now - last < BUTTON_COOLDOWN_SECONDS:
            restante = int(BUTTON_COOLDOWN_SECONDS - (now - last))
            await interaction.response.send_message(
                f"⏳ Aguarde {restante} segundos para usar este botão novamente.",
                ephemeral=True
            )
            return False
        button_cooldowns[key] = now
        return True

    @discord.ui.button(label="🟢Disponível", style=discord.ButtonStyle.success)
    async def disponivel(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "disponivel"):
            return
        await self._atualizar_disponibilidade(interaction, 1)

    @discord.ui.button(label="🔴Indisponível", style=discord.ButtonStyle.danger)
    async def indisponivel(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "indisponivel"):
            return
        await self._atualizar_disponibilidade(interaction, 0)

    async def _atualizar_disponibilidade(self, interaction, disponibilidade):
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                await db.execute(
                    "UPDATE jogadores SET disponibilidade = ? WHERE personagem_nome = ? AND user_id = ?",
                    (disponibilidade, self.personagem_nome, str(interaction.user.id))
                )
                await db.commit()
                
                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao, personagem_server "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
                
            if not dados:
                return await interaction.response.send_message(
                    PERSONAGEM_NAO_ENCONTRADO,
                    ephemeral=True
                )
                
            embed = self._criar_embed_perfil(dados)
            await interaction.response.edit_message(embed=embed, view=self, content=None)
        except Exception as e:
            print(f"[ERRO] _atualizar_disponibilidade: {e}")
            await interaction.response.send_message(
                ERRO_ATUALIZAR_DISPONIBILIDADE,
                ephemeral=True
            )

    def _criar_embed_perfil(self, dados):
        embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
        embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
        embed.add_field(name="Função", value=dados[1] or "—", inline=True)
        embed.add_field(name="Servidor", value=dados[9] or "—", inline=True)  # NOVO
        embed.add_field(name="Armadura", value=dados[2] or "—", inline=True)
        embed.add_field(name="Disponível", value="🟢 Sim" if dados[3] else "🔴 Não", inline=True)
        embed.add_field(name="Raider.IO", value=f"[Link]({dados[4]})" if dados[4] else "—", inline=False)
        embed.add_field(name="Score M+", value=str(int(dados[5])) if dados[5] else "—", inline=True)
        embed.add_field(name="Última atualização", value=dados[8] or "—", inline=True)
        return embed

    @discord.ui.button(label="⚠️Deletar Cadastro⚠️", style=discord.ButtonStyle.secondary)
    async def deletar(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "deletar"):
            return
            
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                await db.execute(
                    "DELETE FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                await db.commit()
                
            try:
                await interaction.message.edit(
                    content="Para atualizar seus personagens use /perfil novamente.",
                    embed=None,
                    view=None
                )
            except Exception:
                pass
                
            await interaction.response.send_message(
                PERSONAGEM_REMOVIDO(self.personagem_nome),
                ephemeral=True
            )
        except Exception as e:
            print(f"[ERRO] deletar: {e}")
            await interaction.response.send_message(
                ERRO_DELETAR_PERSONAGEM,
                ephemeral=True
            )

    @discord.ui.button(label="🔄 Atualizar Raider.IO", style=discord.ButtonStyle.primary)
    async def atualizar_raiderio(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "atualizar_raiderio"):
            return
            
        user_key = f"{interaction.user.id}:{self.personagem_nome.lower()}"
        now = time()
        cooldown = raiderio_cooldowns.get(user_key, 0)
        
        if now - cooldown < RAIDERIO_COOLDOWN_SECONDS:
            restante = int(RAIDERIO_COOLDOWN_SECONDS - (now - cooldown))
            await interaction.response.send_message(
                f"⏳ Aguarde {restante} segundos para atualizar novamente.",
                ephemeral=True
            )
            return

        raiderio_cooldowns[user_key] = now

        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT raiderio_url FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                row = await cursor.fetchone()
                
                if not row or not row[0]:
                    await interaction.response.send_message(
                        "❌ Link Raider.IO não encontrado para este personagem.", 
                        ephemeral=True
                    )
                    return
                    
                url = row[0]

            score_tuple = await obter_score_raiderio(url)
            if isinstance(score_tuple, tuple):
                score = score_tuple[0]
            else:
                score = score_tuple

            if score is None:
                await interaction.response.send_message(
                    "❌ Não foi possível atualizar o score. Verifique o link Raider.IO.", 
                    ephemeral=True
                )
                return

            hoje = datetime.now().date().isoformat()
            async with aiosqlite.connect("data/raiderio.db") as db:
                await db.execute(
                    "UPDATE jogadores SET raiderio_score = ?, ultima_atualizacao = ? WHERE personagem_nome = ? AND user_id = ?",
                    (score, hoje, self.personagem_nome, str(interaction.user.id))
                )
                await db.commit()

                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao, personagem_server "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
                
            embed = self._criar_embed_perfil(dados)
            await interaction.response.edit_message(embed=embed, view=self, content=None)
            
        except Exception as e:
            print(f"[ERRO] atualizar_raiderio: {e}")
            await interaction.response.send_message(
                ERRO_ATUALIZAR_RAIDERIO,
                ephemeral=True
            )

# --- BOT CLASS COM MELHORIAS ---

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents)
        self.db_conn = None
        self.db_lock = asyncio.Lock()
        self.cleanup_task = None

    async def setup_hook(self):
        self.db_conn = await aiosqlite.connect("data/raiderio.db")
        await self.db_conn.execute("""
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
                personagem_server TEXT,  -- NOVO CAMPO
                ultima_atualizacao TEXT,
                UNIQUE(user_id, personagem_nome)
            )
        """)
        await self.db_conn.commit()
        await self.tree.sync()
        
        # Inicia task de limpeza periódica
        self.cleanup_task = asyncio.create_task(self.cleanup_periodico())

    async def cleanup_periodico(self):
        """Task que roda periodicamente para limpar memória"""
        while True:
            try:
                await asyncio.sleep(300)  # 5 minutos
                limpar_cooldowns_expirados()
                gc.collect()  # Força garbage collection
                print(f"[CLEANUP] Views ativas: {active_views_count}, Cooldowns: {len(raiderio_cooldowns)}")
            except Exception as e:
                print(f"[ERRO CLEANUP] {e}")

    async def close(self):
        if self.cleanup_task:
            self.cleanup_task.cancel()
        if self.db_conn:
            await self.db_conn.close()
        await super().close()

bot = Bot()

# --- COMANDOS ---

@bot.tree.command(name="cadastrar", description="Inicia um cadastro privado")
async def cadastrar_slash(interaction: discord.Interaction):
    # Verifica se há muitas views ativas
    if active_views_count > MAX_ACTIVE_VIEWS:
        return await interaction.response.send_message(
            SISTEMA_SOBRECARGADO,
            ephemeral=True
        )
    
    if interaction.user.id in active_cadastros:
        if active_cadastros[interaction.user.id] == "concluido":
            active_cadastros.pop(interaction.user.id, None)
            return await interaction.response.send_message(
                CADASTRO_CONCLUIDO,
                ephemeral=True
            )
        return await interaction.response.send_message(
            CADASTRO_EM_ANDAMENTO,
            ephemeral=True
        )
        
    try:
        view = CadastroView(interaction)
        active_cadastros[interaction.user.id] = view
        await interaction.response.send_message(
            f"{interaction.user.mention}, iniciando seu cadastro privado...",
            view=view,
            ephemeral=True
        )
    except Exception as e:
        print(f"[ERRO CADASTRAR] {e}")
        await interaction.response.send_message(
            ERRO_INICIAR_CADASTRO,
            ephemeral=True
        )

@bot.event
async def on_ready():
    print(f"✅ Bot online como {bot.user.name}")
    canal = bot.get_channel(INSTRUCOES_CANAL_ID)
    if canal:
        try:
            if os.path.exists(BOASVINDAS_MSG_ID_FILE):
                with open(BOASVINDAS_MSG_ID_FILE, "r") as f:
                    msg_id = int(f.read().strip())
                msg = await canal.fetch_message(msg_id)
                await msg.delete()
        except Exception as e:
            print(f"[Boas-vindas] Erro ao deletar mensagem antiga: {e}")

        embed = discord.Embed(
            title="🎉 Bem-vindo ao Cadastro do BakersM+!",
            description=BOAS_VINDAS,
            color=discord.Color.gold()
        )
        msg = await canal.send(embed=embed)
        with open(BOASVINDAS_MSG_ID_FILE, "w") as f:
            f.write(str(msg.id))

class PersonagemButton(Button):
    def __init__(self, personagem_nome):
        self.personagem_nome = personagem_nome
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=personagem_nome
        )

    async def setup(self):
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT funcao FROM jogadores WHERE personagem_nome = ?",
                    (self.personagem_nome,)
                )
                dados = await cursor.fetchone()
                if dados:
                    self.funcao = dados[0]
                    icone = "🛡️" if self.funcao == "Tank" else \
                            "💚" if self.funcao == "Healer" else \
                            "⚔️" if self.funcao == "DPS" else "❔"
                    self.label = f"{icone} {self.personagem_nome}"
        except Exception as e:
            print(f"[ERRO SETUP_BUTTON] {e}")

    async def callback(self, interaction: discord.Interaction):
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao, personagem_server "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
            if not dados:
                return await interaction.response.send_message(
                    PERSONAGEM_NAO_ENCONTRADO,
                    ephemeral=True
                )
            embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
            embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
            embed.add_field(name="Função", value=dados[1] or "—", inline=True)
            embed.add_field(name="Servidor", value=dados[9] or "—", inline=True)  # NOVO
            embed.add_field(name="Armadura", value=dados[2] or "—", inline=True)
            embed.add_field(name="Disponível", value="🟢 Sim" if dados[3] else "🔴 Não", inline=True)
            embed.add_field(name="Raider.IO", value=f"[Link]({dados[4]})" if dados[4] else "—", inline=False)
            embed.add_field(name="Score M+", value=str(int(dados[5])) if dados[5] else "—", inline=True)
            embed.add_field(name="Última atualização", value=dados[8] or "—", inline=True)
            await interaction.response.send_message(
                embed=embed,
                view=GerenciarPersonagemView(self.personagem_nome),
                ephemeral=True
            )
        except Exception as e:
            print(f"[ERRO PERSONAGEM_BUTTON] {e}")
            await interaction.response.send_message(
                ERRO_CARREGAR_PERSONAGEM,
                ephemeral=True
            )

class ListaPersonagensView(View):
    def __init__(self, personagens, interaction):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.personagens = personagens
        
    async def setup_buttons(self):
        """Configura os botões de forma assíncrona"""
        # Adiciona botões de personagem
        for nome in self.personagens[:10]:
            button = PersonagemButton(nome)
            await button.setup()  # Aguarda a configuração do botão
            self.add_item(button)
            
        # Adiciona botões de disponibilidade geral se tiver 2+ personagens
        if len(self.personagens) >= 2:
            self.add_item(DisponibilidadeGeralButton(True))
            self.add_item(DisponibilidadeGeralButton(False))

@bot.tree.command(name="perfil", description="Veja seus personagens registrados")
async def perfil_slash(interaction: discord.Interaction):
    try:
        async with bot.db_lock:
            cursor = await bot.db_conn.execute(
                "SELECT personagem_nome, funcao, raiderio_score, disponibilidade, personagem_server FROM jogadores WHERE user_id = ? LIMIT 10",
                (str(interaction.user.id),)
            )
            personagens = await cursor.fetchall()

        if not personagens:
            return await interaction.response.send_message(
                "❌ Você ainda não registrou nenhum personagem com este ID.",
                ephemeral=True
            )

        # Adiciona a contagem de personagens no topo do embed
        embed = discord.Embed(
            title="📋 Seus Personagens Registrados",
            description=f"Você cadastrou {len(personagens)} de 4 personagens permitidos.",
            color=discord.Color.gold()
        )
        for p in personagens:
            status = "🟢 Disponível" if p[3] else "🔴 Indisponível"
            func_icon = "🛡️" if p[1] == "Tank" else "💚" if p[1] == "Healer" else "⚔️"
            embed.add_field(
                name=f"{func_icon} {p[0]}",
                value=f"Servidor: {p[4] or '—'}\nRaiderIO: {int(p[2])}\nStatus: {status}",
                inline=False
            )

        nomes_personagens = [p[0] for p in personagens]
        servidores = [p[4] for p in personagens]

        view = PerfilView(nomes_personagens, servidores, interaction)
        await view.setup_buttons()  # Configura os botões antes de enviar

        await interaction.response.send_message(
            embed=embed,
            content="Selecione um personagem para ver os detalhes:",
            view=view,
            ephemeral=True
        )
    except Exception as e:
        print(f"[ERRO PERFIL] {e}")
        await interaction.response.send_message(
            "❌ Erro ao carregar perfil. Tente novamente.",
            ephemeral=True
        )

class PerfilView(View):
    def __init__(self, personagens, servidores, interaction):
        super().__init__(timeout=60)
        self.interaction = interaction
        self.personagens = personagens
        self.servidores = servidores

    async def setup_buttons(self):
        # Row 1: Disponibilidade geral e atualizar
        if len(self.personagens) >= 2:
            self.add_item(DisponibilidadeGeralButton(True, row=1))
            self.add_item(DisponibilidadeGeralButton(False, row=1))
        self.add_item(AtualizarPerfilButton(row=1))

        # Row 2: Botões de personagem
        for nome in self.personagens[:10]:
            button = PersonagemButton(nome)
            await button.setup()
            button.row = 2
            self.add_item(button)

class AtualizarPerfilButton(Button):
    def __init__(self, row=1):
        super().__init__(
            label="🔄 Atualizar",
            style=discord.ButtonStyle.secondary,
            row=row
        )

    async def callback(self, interaction: discord.Interaction):
        await perfil_slash.callback(interaction)

class DisponibilidadeGeralButton(Button):
    def __init__(self, disponivel: bool, row=1):
        super().__init__(
            label="🟢 Todos Disponíveis" if disponivel else "🔴 Todos Indisponíveis",
            style=discord.ButtonStyle.success if disponivel else discord.ButtonStyle.danger,
            row=row
        )
        self.disponivel = disponivel

    async def callback(self, interaction: discord.Interaction):
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                await db.execute(
                    "UPDATE jogadores SET disponibilidade = ? WHERE user_id = ?",
                    (1 if self.disponivel else 0, str(interaction.user.id))
                )
                await db.commit()
                cursor = await db.execute(
                    "SELECT personagem_nome, funcao, raiderio_score, disponibilidade, personagem_server FROM jogadores WHERE user_id = ? LIMIT 10",
                    (str(interaction.user.id),)
                )
                personagens = await cursor.fetchall()

            embed = discord.Embed(
                title="📋 Seus Personagens Registrados",
                color=discord.Color.green() if self.disponivel else discord.Color.red()
            )
            for p in personagens:
                status = "🟢 Disponível" if p[3] else "🔴 Indisponível"
                func_icon = "🛡️" if p[1] == "Tank" else "💚" if p[1] == "Healer" else "⚔️"
                embed.add_field(
                    name=f"{func_icon} {p[0]}",
                    value=f"Servidor: {p[4] or '—'}\nScore Raider.IO: {int(p[2])}\nStatus: {status}",
                    inline=False
                )

            nomes_personagens = [p[0] for p in personagens]
            servidores = [p[4] for p in personagens]
            view = PerfilView(nomes_personagens, servidores, interaction)
            await view.setup_buttons()

            await interaction.response.edit_message(
                embed=embed,
                view=view,
                content="Selecione um personagem para ver os detalhes:"
            )
        except Exception as e:
            print(f"[ERRO DISPONIBILIDADE_GERAL] {e}")
            await interaction.response.send_message(
                "❌ Erro ao atualizar disponibilidade.",
                ephemeral=True
            )

if __name__ == "__main__":
    # Carrega o token do .env
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Token não encontrado no arquivo .env!")
        exit(1)
    
    try:
        print("🔄 Iniciando bot...")
        bot.run(TOKEN)
    except Exception as e:
        print(f"❌ Erro ao iniciar o bot: {e}")