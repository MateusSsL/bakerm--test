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
                        "❌ Você já atingiu o limite de 4 personagens cadastrados.",
                        ephemeral=True
                    )

            # Abre modal de cadastro
            modal = CadastroModal(self)
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"[ERRO INICIAR_CADASTRO] {e}")
            await interaction.response.send_message(
                "❌ Erro ao iniciar cadastro. Tente novamente.",
                ephemeral=True
            )

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        active_cadastros.pop(interaction.user.id, None)
        await interaction.message.delete()

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
                    "❌ Função inválida! Use:\n"
                    "• Tank (ou tk)\n"
                    "• Healer (ou heal)\n"
                    "• DPS\n",
                    ephemeral=True
                )
                
            raiderio_url = validar_entrada_usuario(self.raiderio_input.value)
            
            # Validar função
            if funcao not in ["Tank", "Healer", "DPS"]:
                return await interaction.response.send_message(
                    "❌ Função inválida! Use Tank, Healer ou DPS.",
                    ephemeral=True
                )
            
            # Verificar rate limit
            if await verificar_rate_limit(interaction.user.id, "cadastro"):
                return await interaction.response.send_message(
                    "⏳ Aguarde alguns minutos antes de tentar novamente.",
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
                        "❌ Você já atingiu o limite de 4 personagens cadastrados.",
                        ephemeral=True
                    )
                
                # Verificar se personagem já existe
                cursor = await db.execute(
                    "SELECT user_id FROM jogadores WHERE personagem_nome = ?",
                    (nick,)
                )
                existing = await cursor.fetchone()
                if existing and str(existing[0]) != str(interaction.user.id):
                    return await interaction.response.send_message(
                        "❌ Este personagem já está cadastrado por outro usuário.",
                        ephemeral=True
                    )
            
            # Validar com Raider.IO e obter score atual
            score, classe = await obter_score_raiderio(raiderio_url)
            if score is None or classe is None:
                return await interaction.response.send_message(
                    "❌ Erro ao validar perfil no Raider.IO. Verifique o link.",
                    ephemeral=True
                )
            
            # Criar embed de confirmação
            embed = discord.Embed(
                title="📝 Confirmar Cadastro",
                description="Verifique os dados antes de confirmar:",
                color=discord.Color.blue()
            )
            embed.add_field(name="Personagem", value=nick, inline=True)
            embed.add_field(name="Classe", value=classe, inline=True)
            embed.add_field(name="Função", value=funcao, inline=True)
            embed.add_field(name="Score M+", value=f"{score:.1f}", inline=True)
            embed.add_field(name="Raider.IO", value=f"[Link]({raiderio_url})", inline=False)
            
            # Salvar dados temporariamente
            self.cadastro_view.personagem_nome = nick
            self.cadastro_view.personagem_classe = classe
            self.cadastro_view.funcao = funcao
            self.cadastro_view.raiderio_url = raiderio_url
            self.cadastro_view.raiderio_score = score
            
            await interaction.response.send_message(
                embed=embed,
                view=ConfirmarCadastroView(interaction, self.cadastro_view),
                ephemeral=True
            )
            
        except Exception as e:
            print(f"[ERRO CADASTRO] {e}")
            await interaction.response.send_message(
                "❌ Erro ao processar cadastro. Tente novamente.",
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
            await interaction.response.send_message("❌ Cadastro já foi processado.", ephemeral=True)
            return
            
        self.confirmado = True
        
        try:
            await interaction.response.defer(ephemeral=True)
            async with aiosqlite.connect("data/raiderio.db") as db:
                # Remove a cláusula ON CONFLICT e adiciona todos os campos necessários
                await db.execute("""
                    INSERT INTO jogadores 
                    (user_id, nome, funcao, armadura, raiderio_url, raiderio_score, 
                     personagem_nome, personagem_classe, disponibilidade, ultima_atualizacao)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
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
                f"▸ **Score M+:** {int(self.cadastro_view.raiderio_score)}\n\n"
                f"Use `/perfil` para ver seu perfil completo.",
                ephemeral=True
            )

            # Edita mensagens e limpa registros
            await interaction.message.edit(
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
            self.confirmado = False
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
            async with aiosqlite.connect("data/raiderio.db") as db:
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

            score = await obter_score_raiderio(url)
            
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

class PersonagemButton(Button):
    def __init__(self, personagem_nome):
        self.personagem_nome = personagem_nome
        # Começa com um label padrão
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=personagem_nome  # Label inicial temporário
        )

    async def setup(self):
        """Configura o botão de forma assíncrona"""
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                cursor = await db.execute(
                    "SELECT funcao FROM jogadores WHERE personagem_nome = ?",
                    (self.personagem_nome,)
                )
                dados = await cursor.fetchone()
                if dados:
                    self.funcao = dados[0]
                    # Define o ícone baseado na função
                    icone = "🛡️" if self.funcao == "Tank" else \
                           "💚" if self.funcao == "Healer" else \
                           "⚔️" if self.funcao == "DPS" else "❔"
                    
                    # Atualiza o label do botão com o ícone
                    self.label = f"{icone} {self.personagem_nome}"
                    
        except Exception as e:
            print(f"[ERRO SETUP_BUTTON] {e}")

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
        await view.setup_buttons()  # Configura os botões antes de enviar
        
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

class ListaPaginadaView(discord.ui.View):
    def __init__(self, jogadores, por_pagina=8, timeout=120):
        super().__init__(timeout=timeout)
        self.jogadores = jogadores
        self.por_pagina = por_pagina
        self.pagina_atual = 0
        self.total_paginas = max(1, (len(jogadores) + por_pagina - 1) // por_pagina)
        self.filtro_atual = "todos"
        
        # Adiciona select de filtro
        self.add_item(self.FiltroSelect())
        
    def get_pagina_atual(self):
        inicio = self.pagina_atual * self.por_pagina
        fim = inicio + self.por_pagina
        return self.jogadores[inicio:fim]
        
    def criar_embed(self):
        cor = discord.Color.blue() if self.filtro_atual == "Tank" else \
              discord.Color.green() if self.filtro_atual == "Healer" else \
              discord.Color.red() if self.filtro_atual == "DPS" else \
              discord.Color.gold()
              
        embed = discord.Embed(
            title=f"📋 Jogadores Disponíveis ({self.filtro_atual})",
            color=cor
        )
        
        jogadores_pagina = self.get_pagina_atual()
        for j in jogadores_pagina:
            icon = "🛡️" if j[2] == "Tank" else "💚" if j[2] == "Healer" else "⚔️"
            embed.add_field(
                name=f"{icon} {j[1]} ({j[3]})",
                value=f"Score: {int(j[6])}\nDiscord: <@{j[0]}>",
                inline=False
            )
            
        embed.set_footer(text=f"Página {self.pagina_atual + 1}/{self.total_paginas}")
        return embed

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.gray)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.pagina_atual > 0:
            self.pagina_atual -= 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    @discord.ui.button(label="▶️ Próxima", style=discord.ButtonStyle.gray)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.pagina_atual < self.total_paginas - 1:
            self.pagina_atual += 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    class FiltroSelect(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label="Todos", value="todos", emoji="📋"),
                discord.SelectOption(label="Tank", value="Tank", emoji="🛡️"),
                discord.SelectOption(label="Healer", value="Healer", emoji="💚"),
                discord.SelectOption(label="DPS", value="DPS", emoji="⚔️")
            ]
            super().__init__(
                placeholder="Filtrar por função...",
                options=options
            )

        async def callback(self, interaction: discord.Interaction):
            view: ListaPaginadaView = self.view
            view.filtro_atual = self.values[0]
            
            if view.filtro_atual != "todos":
                view.jogadores = [j for j in view.jogadores if j[2] == view.filtro_atual]
            
            view.pagina_atual = 0
            view.total_paginas = max(1, (len(view.jogadores) + view.por_pagina - 1) // view.por_pagina)
            
            await interaction.response.edit_message(embed=view.criar_embed(), view=view)

class ListaDisponiveisView(discord.ui.View):
    def __init__(self, jogadores, timeout=120):
        super().__init__(timeout=timeout)
        self.jogadores = jogadores
        self.pagina_atual = 0
        self.por_pagina = 8
        self.total_paginas = max(1, (len(jogadores) + self.por_pagina - 1) // self.por_pagina)
        self.ultimo_click = {}  # Controle de cooldown

    async def _check_cooldown(self, interaction):
        user_id = interaction.user.id
        now = time()
        cooldown = self.ultimo_click.get(user_id, 0)
        if now - cooldown < BUTTON_COOLDOWN_SECONDS:
            restante = int(BUTTON_COOLDOWN_SECONDS - (now - cooldown))
            await interaction.response.send_message(
                f"⏳ Aguarde {restante} segundos para usar este botão novamente.",
                ephemeral=True
            )
            return False
        self.ultimo_click[user_id] = now
        return True
        
    def get_pagina_atual(self):
        inicio = self.pagina_atual * self.por_pagina
        fim = inicio + self.por_pagina
        return self.jogadores[inicio:fim]
    
    def criar_embed(self):
        embed = discord.Embed(
            title="👥 Jogadores Disponíveis",
            description="Lista de todos os jogadores prontos para M+",
            color=discord.Color.gold()
        )
        
        # Organiza jogadores por função
        tanks = []
        healers = []
        dps = []
        
        for jogador in self.get_pagina_atual():
            # [user_id, nome, funcao, classe, score, personagem_nome]
            info = f"**{jogador[5]}** ({jogador[3]})\n" \
                   f"Score: {int(jogador[4])}\n" \
                   f"<@{jogador[0]}>\n"
                   
            if jogador[2] == "Tank":
                tanks.append(info)
            elif jogador[2] == "Healer":
                healers.append(info)
            else:
                dps.append(info)
        
        # Adiciona campos por função
        if tanks:
            embed.add_field(
                name="🛡️ Tanks",
                value="\n".join(tanks),
                inline=False
            )
        if healers:
            embed.add_field(
                name="💚 Healers",
                value="\n".join(healers),
                inline=False
            )
        if dps:
            embed.add_field(
                name="⚔️ DPS",
                value="\n".join(dps),
                inline=False
            )
            
        embed.set_footer(text=f"Página {self.pagina_atual + 1}/{self.total_paginas}")
        return embed

    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.gray)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_cooldown(interaction):
            return
            
        if self.pagina_atual > 0:
            self.pagina_atual -= 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    @discord.ui.button(label="▶️ Próxima", style=discord.ButtonStyle.gray)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_cooldown(interaction):
            return
            
        if self.pagina_atual < self.total_paginas - 1:
            self.pagina_atual += 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

class ListaAdminView(discord.ui.View):
    def __init__(self, jogadores, thread, timeout=None):
        super().__init__(timeout=timeout)
        self.jogadores = jogadores
        self.thread = thread
        self.pagina_atual = 0
        self.por_pagina = 8
        self.total_paginas = max(1, (len(jogadores) + self.por_pagina - 1) // self.por_pagina)
    
    def get_pagina_atual(self):  # Adiciona este método
        inicio = self.pagina_atual * self.por_pagina
        fim = inicio + self.por_pagina
        return self.jogadores[inicio:fim]
    
    @discord.ui.button(label="◀️ Anterior", style=discord.ButtonStyle.gray)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.pagina_atual > 0:
            self.pagina_atual -= 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    @discord.ui.button(label="▶️ Próxima", style=discord.ButtonStyle.gray)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.pagina_atual < self.total_paginas - 1:
            self.pagina_atual += 1
            await interaction.response.edit_message(embed=self.criar_embed(), view=self)

    def criar_embed(self):
        embed = discord.Embed(
            title="📋 Painel Administrativo - Jogadores Disponíveis",
            description="Lista de jogadores disponíveis para M+",
            color=discord.Color.blue()
        )
        
        for jogador in self.get_pagina_atual():
            # Cria link para DM
            dm_link = f"discord://-/users/{jogador[0]}"
            
            # Cria botão de convite com ID do thread
            convite_callback = f"convite:{self.thread.id}:{jogador[0]}"
            
            info = (
                f"[{jogador[5]}]({dm_link}) ({jogador[3]})\n"
                f"Score: {int(jogador[4])} • [📨 Convidar]({convite_callback})\n\n"
            )
            
            if jogador[2] == "Tank":
                embed.add_field(name="🛡️ Tank", value=info, inline=False)
            elif jogador[2] == "Healer":
                embed.add_field(name="💚 Healer", value=info, inline=False)
            else:
                embed.add_field(name="⚔️ DPS", value=info, inline=False)
        
        embed.set_footer(text=f"Página {self.pagina_atual + 1}/{self.total_paginas}")
        return embed

class GerenciarTopicoView(discord.ui.View):
    def __init__(self, thread):
        super().__init__(timeout=None)
        self.thread = thread
    
    @discord.ui.button(label="➕ Adicionar Membro", style=discord.ButtonStyle.green)
    async def adicionar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_threads:
            return await interaction.response.send_message(
                "❌ Você não tem permissão para gerenciar este tópico!", 
                ephemeral=True
            )
        modal = AdicionarMembroModal(self.thread)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="➖ Remover Membro", style=discord.ButtonStyle.red)
    async def remover(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_threads:
            return await interaction.response.send_message(
                "❌ Você não tem permissão para gerenciar este tópico!", 
                ephemeral=True
            )
        modal = RemoverMembroModal(self.thread)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🗑️ Fechar Tópico", style=discord.ButtonStyle.gray)
    async def fechar_topico(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_threads:
            return await interaction.response.send_message(
                "❌ Você não tem permissão para gerenciar este tópico!", 
                ephemeral=True
            )
        await self.thread.edit(archived=True, locked=True)
        await interaction.response.send_message("✅ Tópico arquivado e trancado!", ephemeral=True)

class AdicionarMembroModal(discord.ui.Modal):
    def __init__(self, thread):
        super().__init__(title="Adicionar Membro")
        self.thread = thread
        self.membro = discord.ui.TextInput(
            label="ID ou @menção do membro",
            placeholder="Ex: 123456789 ou @usuário",
            required=True
        )
        self.add_item(self.membro)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Tenta encontrar o membro por ID ou menção
            membro_id = ''.join(filter(str.isdigit, self.membro.value))
            membro = interaction.guild.get_member(int(membro_id))
            
            if not membro:
                # Tenta buscar via API se não estiver em cache
                try:
                    membro = await interaction.guild.fetch_member(int(membro_id))
                except discord.NotFound:
                    return await interaction.response.send_message(
                        "❌ Membro não encontrado!", ephemeral=True
                    )
            
            # Verifica se já está no tópico
            permissions = self.thread.permissions_for(membro)
            if permissions.read_messages:
                return await interaction.response.send_message(
                    f"❌ {membro.mention} já está no tópico!", ephemeral=True
                )
            
            # Adiciona ao tópico
            await self.thread.add_user(membro)
            await interaction.response.send_message(
                f"✅ {membro.mention} adicionado ao tópico!", ephemeral=True
            )
            
            # Notifica o membro
            try:
                embed = discord.Embed(
                    title="🎮 Convite para grupo M+",
                    description=f"Você foi adicionado a um grupo M+!\nClique para ir ao tópico: {self.thread.jump_url}",
                    color=discord.Color.green()
                )
                await membro.send(embed=embed)
            except discord.Forbidden:
                pass  # Usuário pode ter DMs desativadas
            
        except Exception as e:
            print(f"[ERRO ADICIONAR_MEMBRO] {e}")
            await interaction.response.send_message(
                "❌ Erro ao adicionar membro!", ephemeral=True
            )

class RemoverMembroModal(discord.ui.Modal):
    def __init__(self, view):
        super().__init__(title="Remover Membro")
        self.view = view
        self.membro = discord.ui.TextInput(
            label="ID ou @menção do membro",
            placeholder="Ex: 123456789 ou @usuário",
            required=True
        )
        self.add_item(self.membro)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            membro_id = ''.join(filter(str.isdigit, self.membro.value))
            membro = interaction.guild.get_member(int(membro_id))
            
            if not membro:
                return await interaction.response.send_message(
                    "❌ Membro não encontrado!", ephemeral=True
                )
                
            await interaction.channel.set_permissions(membro, overwrite=None)
            await interaction.response.send_message(
                f"✅ {membro.mention} removido do tópico!", ephemeral=True
            )
            
        except Exception as e:
            await interaction.response.send_message(
                "❌ Erro ao remover membro!", ephemeral=True
            )

@bot.tree.command(name="admin_listar", description="[ADM] Lista e gerencia jogadores disponíveis")
async def admin_listar(interaction: discord.Interaction):
    if not any(role.name == "ADM" for role in interaction.user.roles):
        return await interaction.response.send_message(
            "🚫 Você não tem permissão para usar este comando.",
            ephemeral=True
        )

    try:
        # Cria thread com permissões corretas
        thread = await interaction.channel.create_thread(
            name=f"M+ Group {datetime.now().strftime('%d/%m %H:%M')}",
            type=discord.ChannelType.public_thread,
            auto_archive_duration=1440  # 24 horas
        )
        
        # Configura permissões iniciais
        await thread.edit(invitable=True)
        
        # Envia painel de controle
        embed = discord.Embed(
            title="🛠️ Painel de Controle",
            description=(
                "Use os botões abaixo para gerenciar este grupo:\n"
                "➕ Adicionar Membro - Adiciona um jogador ao tópico\n"
                "➖ Remover Membro - Remove um jogador do tópico\n"
                "🗑️ Fechar Tópico - Arquiva e tranca este tópico"
            ),
            color=discord.Color.blue()
        )
        
        await thread.send(embed=embed, view=GerenciarTopicoView(thread))
        
        # Lista jogadores
        query = """
            SELECT user_id, nome, funcao, personagem_classe, 
                   raiderio_score, personagem_nome
            FROM jogadores 
            WHERE disponibilidade = 1
            ORDER BY 
                CASE funcao 
                    WHEN 'Tank' THEN 1 
                    WHEN 'Healer' THEN 2 
                    ELSE 3 
                END,
                raiderio_score DESC
        """
        
        async with bot.db_lock:
            cursor = await bot.db_conn.execute(query)
            jogadores = await cursor.fetchall()
            
        if not jogadores:
            return await interaction.response.send_message(
                "❌ Nenhum jogador disponível no momento.",
                ephemeral=True
            )
            
        view = ListaAdminView(jogadores, thread)
        await interaction.response.send_message(
            embed=view.criar_embed(),
            view=view
        )
        
    except Exception as e:
        print(f"[ERRO ADMIN_LISTAR] {e}")
        await interaction.response.send_message(
            "❌ Erro ao listar jogadores. Tente novamente.",
            ephemeral=True
        )

class DisponibilidadeGeralButton(Button):
    def __init__(self, disponivel: bool):
        super().__init__(
            label="🟢 Todos Disponíveis" if disponivel else "🔴 Todos Indisponíveis",
            style=discord.ButtonStyle.success if disponivel else discord.ButtonStyle.danger
        )
        self.disponivel = disponivel

    async def callback(self, interaction: discord.Interaction):
        try:
            async with aiosqlite.connect("data/raiderio.db") as db:
                # Atualiza todos os personagens do usuário
                await db.execute(
                    "UPDATE jogadores SET disponibilidade = ? WHERE user_id = ?",
                    (1 if self.disponivel else 0, str(interaction.user.id))
                )
                await db.commit()
                
                # Busca dados atualizados
                cursor = await db.execute(
                    "SELECT personagem_nome, personagem_classe, funcao, disponibilidade "
                    "FROM jogadores WHERE user_id = ?",
                    (str(interaction.user.id),)
                )
                personagens = await cursor.fetchall()

            # Cria embed com resultado
            embed = discord.Embed(
                color=discord.Color.green() if self.disponivel else discord.Color.red()
            )

            for p in personagens:
                status = "🟢" if self.disponivel else "🔴"
                embed.add_field(
                    name=f"{status} {p[0]}",
                    value=f"Classe: {p[1]}\nFunção: {p[2]}",
                    inline=True
                )

            await interaction.response.edit_message(
                embed=embed,
                view=ListaPersonagensView(self.view.personagens, interaction)
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