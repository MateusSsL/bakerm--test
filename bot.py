
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

INSTRUCOES_CANAL_ID = 1394566723448995982
BOASVINDAS_MSG_ID_FILE = "boasvindas_msg_id.txt"

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
            label="Função (Tank, Healer ou DPS)",
            placeholder="Digite exatamente: Tank, Healer ou DPS",
            required=True,
            max_length=10
        )
        self.raiderio_input = TextInput(
            label="Link do Raider.IO",
            placeholder="https://raider.io/characters/us/realm/name",
            required=True,
            max_length=200
        )
        self.add_item(self.nick_input)
        self.add_item(self.funcao_input)
        self.add_item(self.raiderio_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Valida entradas
            nick = validar_entrada_usuario(self.nick_input.value, 50)
            funcao = validar_entrada_usuario(self.funcao_input.value, 10).capitalize()
            link = validar_entrada_usuario(self.raiderio_input.value, 200)
            
            # Verifica se usuário excedeu limite de tentativas
            if registrar_tentativa_falhada(interaction.user.id, "cadastro"):
                return await interaction.response.send_message(
                    "⚠️ Muitas tentativas falhadas. Aguarde 1 hora antes de tentar novamente.",
                    ephemeral=True
                )

            # Validação da função
            if funcao not in ["Tank", "Healer", "Dps"]:
                return await interaction.response.send_message(
                    "❌ Função inválida. Digite exatamente: Tank, Healer ou DPS.",
                    ephemeral=True
                )

            # Validação do Raider.IO
            link = link.strip().rstrip("/")
            if not link.startswith("https://raider.io/characters/"):
                return await interaction.response.send_message(
                    "❌ Link deve começar com https://raider.io/characters/",
                    ephemeral=True
                )

            parts = link.split("/")
            if len(parts) < 7 or "characters" not in parts:
                return await interaction.response.send_message(
                    "❌ Formato do link inválido. Use o link completo do seu personagem.",
                    ephemeral=True
                )

            idx = parts.index("characters")
            if idx + 3 >= len(parts):
                return await interaction.response.send_message(
                    "❌ Link incompleto. Verifique se contém região/realm/nome.",
                    ephemeral=True
                )

            region = validar_entrada_usuario(parts[idx + 1], 10)
            realm = validar_entrada_usuario(parts[idx + 2], 50)
            name = validar_entrada_usuario(parts[idx + 3], 50)

            # Timeout para requisição HTTP
            timeout = aiohttp.ClientTimeout(total=10)
            api_url = (
                f"https://raider.io/api/v1/characters/profile"
                f"?region={region}&realm={realm}&name={name}"
                f"&fields=mythic_plus_scores_by_season:current"
            )
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        return await interaction.response.send_message(
                            "❌ Link inválido ou jogador não encontrado.",
                            ephemeral=True
                        )
                    
                    data = await resp.json()
                    
                    # Validação dos dados da API
                    if not isinstance(data, dict):
                        raise ValueError("Resposta da API inválida")
                    
                    score = data.get("mythic_plus_scores_by_season", [{}])[0].get("scores", {}).get("all", 0)
                    classe = data.get("class", "Desconhecida")
                    nome_personagem_api = data.get("name", "")
                    
                    if not nome_personagem_api:
                        raise ValueError("Nome do personagem não encontrado")

            # Verificação de nick
            if nome_personagem_api.lower() != nick.lower():
                return await interaction.response.send_message(
                    f"❌ O nick informado (**{nick}**) não corresponde ao personagem do Raider.IO (**{nome_personagem_api}**). Verifique e tente novamente.",
                    ephemeral=True
                )

        except asyncio.TimeoutError:
            return await interaction.response.send_message(
                "⏰ Timeout na consulta ao Raider.IO. Tente novamente.",
                ephemeral=True
            )
        except Exception as e:
            print(f"[ERRO CADASTRO] {str(e)}")
            return await interaction.response.send_message(
                "❌ Erro ao buscar dados do Raider.IO. Verifique o link e tente novamente.",
                ephemeral=True
            )

        # Mapeamento de armadura
        classe_lower = classe.lower()
        armadura_map = {
            "priest": "Tecido", "mage": "Tecido", "warlock": "Tecido",
            "druid": "Couro", "monk": "Couro", "rogue": "Couro", "demon hunter": "Couro",
            "evoker": "Malha", "shaman": "Malha", "hunter": "Malha",
            "death knight": "Placa", "paladin": "Placa", "warrior": "Placa"
        }
        armadura = armadura_map.get(classe_lower, "Desconhecida")

        # Salva dados na view
        self.cadastro_view.armadura = armadura
        self.cadastro_view.personagem_nome = nome_personagem_api
        self.cadastro_view.funcao = funcao
        self.cadastro_view.raiderio_url = link
        self.cadastro_view.raiderio_score = score
        self.cadastro_view.personagem_classe = classe

        embed = discord.Embed(
            title="Confirme seu Cadastro",
            description=f"Nick: **{nome_personagem_api}**\nClasse: **{classe}**\nFunção: **{funcao}**\nArmadura: **{armadura}**\nScore: **{score}**",
            color=discord.Color.orange()
        )
        view = ConfirmarCadastroView(interaction, self.cadastro_view)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class CadastroView(PrivateView):
    def __init__(self, interaction):
        super().__init__(interaction)
        self.interaction = interaction
        self.user_id = str(interaction.user.id)
        self.nome = interaction.user.name
        self.funcao = None
        self.raiderio_url = None
        self.raiderio_score = None
        self.personagem_nome = None
        self.personagem_classe = None
        self.armadura = None

    @discord.ui.button(label="Iniciar Cadastro", style=discord.ButtonStyle.primary, custom_id="iniciar_cadastro")
    async def iniciar_cadastro(self, interaction: discord.Interaction, button: Button):
        if not await self.interaction_check(interaction):
            return
        
        # Verifica limite de views ativas
        if active_views_count > MAX_ACTIVE_VIEWS:
            await interaction.response.send_message(
                "⚠️ Sistema temporariamente sobrecarregado. Tente novamente em alguns minutos.",
                ephemeral=True
            )
            return
            
        await interaction.response.send_modal(CadastroModal(self))

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger, row=4)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        if await self.interaction_check(interaction):
            await interaction.response.edit_message(
                content="Cadastro cancelado.",
                view=None
            )
            active_cadastros.pop(interaction.user.id, None)
            self.stop()

class ConfirmarCadastroView(View):
    def __init__(self, interaction, cadastro_view):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.cadastro_view = cadastro_view
        self.confirmado = False  # Previne múltiplos cliques

    @discord.ui.button(label="✅ Confirmar Cadastro", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: Button):
        if self.confirmado:
            await interaction.response.send_message("❌ Cadastro já foi processado.", ephemeral=True)
            return
            
        self.confirmado = True
        
        try:
            await interaction.response.defer(ephemeral=True)
            async with aiosqlite.connect("raiderio.db") as db:
                # Verifica se o personagem já existe para outro usuário
                cursor = await db.execute(
                    "SELECT user_id FROM jogadores WHERE personagem_nome = ? AND user_id != ?",
                    (self.cadastro_view.personagem_nome, self.cadastro_view.user_id)
                )
                if await cursor.fetchone():
                    return await interaction.followup.send(
                        "❌ Este personagem já está registrado por outro jogador!",
                        ephemeral=True
                    )
                    
                await db.execute("""
                    INSERT INTO jogadores 
                    (user_id, nome, funcao, armadura, raiderio_url, raiderio_score, 
                     personagem_nome, personagem_classe, disponibilidade)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        funcao=excluded.funcao,
                        armadura=excluded.armadura,
                        raiderio_url=excluded.raiderio_url,
                        raiderio_score=excluded.raiderio_score,
                        personagem_nome=excluded.personagem_nome,
                        personagem_classe=excluded.personagem_classe,
                        disponibilidade=1
                """, (
                    self.cadastro_view.user_id,
                    self.cadastro_view.nome,
                    self.cadastro_view.funcao,
                    self.cadastro_view.armadura,
                    self.cadastro_view.raiderio_url,
                    self.cadastro_view.raiderio_score,
                    self.cadastro_view.personagem_nome,
                    self.cadastro_view.personagem_classe
                ))
                await db.commit()

            await interaction.followup.send(
                f"🎉 **Cadastro concluído com sucesso!**\n\n"
                f"▸ **Personagem:** {self.cadastro_view.personagem_nome}\n"
                f"▸ **Classe:** {self.cadastro_view.personagem_classe}\n"
                f"▸ **Função:** {self.cadastro_view.funcao}\n"
                f"▸ **Armadura:** {self.cadastro_view.armadura}\n"
                f"▸ **Score M+:** {int(self.cadastro_view.raiderio_score)}\n\n"
                f"Use `/perfil` para ver seu perfil completo.",
                ephemeral=True
            )

            # Edita mensagens e limpa registros
            await interaction.edit_original_response(
                content="✅ Cadastro concluído! Esta janela será fechada automaticamente.",
                embed=None,
                view=None
            )

            try:
                await self.cadastro_view.interaction.edit_original_response(
                    content="✅ Cadastro concluído! Use `/cadastrar` novamente para outro personagem.",
                    embed=None,
                    view=None
                )
            except Exception:
                pass

            active_cadastros.pop(interaction.user.id, None)
            
        except Exception as e:
            self.confirmado = False  # Permite tentar novamente
            print(f"ERRO NO CADASTRO: {str(e)}")
            await interaction.followup.send(
                f"❌ **Erro crítico:** Falha ao completar cadastro\n"
                f"Por favor, tente novamente ou contate um administrador.",
                ephemeral=True
            )

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
            async with aiosqlite.connect("raiderio.db") as db:
                await db.execute(
                    "UPDATE jogadores SET disponibilidade = ? WHERE personagem_nome = ? AND user_id = ?",
                    (disponibilidade, self.personagem_nome, str(interaction.user.id))
                )
                await db.commit()
                
                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
                
            if not dados:
                return await interaction.response.send_message(
                    "❌ Personagem não encontrado.", ephemeral=True
                )
                
            embed = self._criar_embed_perfil(dados)
            await interaction.response.edit_message(embed=embed, view=self, content=None)
        except Exception as e:
            print(f"[ERRO] _atualizar_disponibilidade: {e}")
            await interaction.response.send_message(
                "❌ Erro ao atualizar disponibilidade.", ephemeral=True
            )

    def _criar_embed_perfil(self, dados):
        embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
        embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
        embed.add_field(name="Função", value=dados[1] or "—", inline=True)
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
            async with aiosqlite.connect("raiderio.db") as db:
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
                f"❌ O personagem **{self.personagem_nome}** foi removido do seu perfil.",
                ephemeral=True
            )
        except Exception as e:
            print(f"[ERRO] deletar: {e}")
            await interaction.response.send_message(
                "❌ Erro ao deletar personagem.", ephemeral=True
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
            async with aiosqlite.connect("raiderio.db") as db:
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

            # Importa função específica para evitar import circular
            from raiderio_api import obter_score_raiderio
            score = await obter_score_raiderio(url)
            
            if score is None:
                await interaction.response.send_message(
                    "❌ Não foi possível atualizar o score. Verifique o link Raider.IO.", 
                    ephemeral=True
                )
                return

            hoje = datetime.now().date().isoformat()
            async with aiosqlite.connect("raiderio.db") as db:
                await db.execute(
                    "UPDATE jogadores SET raiderio_score = ?, ultima_atualizacao = ? WHERE personagem_nome = ? AND user_id = ?",
                    (score, hoje, self.personagem_nome, str(interaction.user.id))
                )
                await db.commit()

                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
                
            embed = self._criar_embed_perfil(dados)
            await interaction.response.edit_message(embed=embed, view=self, content=None)
            
        except Exception as e:
            print(f"[ERRO] atualizar_raiderio: {e}")
            await interaction.response.send_message(
                "❌ Erro ao atualizar Raider.IO.", ephemeral=True
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
        self.db_conn = await aiosqlite.connect("raiderio.db")
        await self.db_conn.execute("""
            CREATE TABLE IF NOT EXISTS jogadores (
                user_id TEXT PRIMARY KEY,
                nome TEXT,
                funcao TEXT,
                armadura TEXT,
                disponibilidade INTEGER DEFAULT 0,
                raiderio_url TEXT,
                raiderio_score REAL,
                personagem_nome TEXT UNIQUE, 
                personagem_classe TEXT, 
                ultima_atualizacao TEXT
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
            "⚠️ Sistema temporariamente sobrecarregado. Tente novamente em alguns minutos.",
            ephemeral=True
        )
    
    if interaction.user.id in active_cadastros:
        if active_cadastros[interaction.user.id] == "concluido":
            active_cadastros.pop(interaction.user.id, None)
            return await interaction.response.send_message(
                "✅ Cadastro concluído! Caso queira cadastrar outro personagem, use o comando `/cadastrar` novamente!",
                ephemeral=True
            )
        return await interaction.response.send_message(
            "Você já tem um cadastro em andamento! Complete ou cancele antes de iniciar outro.",
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
            "❌ Erro ao iniciar cadastro. Tente novamente.",
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
            description=(
                "Para participar dos grupos de Mythic+, você precisa se cadastrar!\n\n"
                "📌 Informe:\n"
                "➤ Sua **função** (Tank, Healer, DPS)\n"
                "➤ Seu **Nick** do Personagem corretamente!\n"
                "➤ Seu link do **Raider.IO** do seu personagem!\n\n"
                "Use `/cadastrar` para começar!\n"
                "Se você já se cadastrou, use `/perfil` e nos atualize sobre sua disponibilidade."
            ),
            color=discord.Color.gold()
        )
        msg = await canal.send(embed=embed)
        with open(BOASVINDAS_MSG_ID_FILE, "w") as f:
            f.write(str(msg.id))

class ListaPersonagensView(View):
    def __init__(self, personagens, interaction):
        super().__init__(timeout=60)
        self.interaction = interaction
        for nome in personagens[:10]:  # Limita a 10 personagens para evitar sobrecarga
            self.add_item(PersonagemButton(nome))

class PersonagemButton(Button):
    def __init__(self, personagem_nome):
        super().__init__(label=personagem_nome[:20], style=discord.ButtonStyle.primary)  # Limita label
        self.personagem_nome = personagem_nome

    async def callback(self, interaction: discord.Interaction):
        try:
            async with aiosqlite.connect("raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                    "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                    "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                    (self.personagem_nome, str(interaction.user.id))
                )
                dados = await cursor.fetchone()
                
            if not dados:
                return await interaction.response.send_message(
                    "❌ Personagem não encontrado.", ephemeral=True
                )

            embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
            embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
            embed.add_field(name="Função", value=dados[1] or "—", inline=True)
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
                "❌ Erro ao carregar personagem.", ephemeral=True
            )

@bot.tree.command(name="perfil", description="Veja seus personagens registrados")
async def perfil_slash(interaction: discord.Interaction):
    try:
        async with bot.db_lock:
            cursor = await bot.db_conn.execute(
                "SELECT personagem_nome FROM jogadores WHERE user_id = ? LIMIT 10",
                (str(interaction.user.id),)
            )
            personagens = [row[0] for row in await cursor.fetchall()]

        if not personagens:
            return await interaction.response.send_message(
                "❌ Você ainda não registrou nenhum personagem com este ID.",
                ephemeral=True
            )

        view = ListaPersonagensView(personagens, interaction)
        await interaction.response.send_message(
            "Selecione um personagem para ver os detalhes:",
            view=view,
            ephemeral=True
        )
    except Exception as e:
        print(f"[ERRO PERFIL] {e}")
        await interaction.response.send_message(
            "❌ Erro ao carregar perfil. Tente novamente.",
            ephemeral=True
        )

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Variável DISCORD_TOKEN não encontrada no .env!")
    else:
        bot.run(TOKEN)
