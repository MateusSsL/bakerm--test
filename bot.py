import discord
import os
import asyncio
import aiosqlite
import aiohttp
from datetime import datetime
from discord.ext import commands
from discord.ui import View, Select, Modal, TextInput, Button
from dotenv import load_dotenv
from typing import Optional
from discord import app_commands
from time import time

INSTRUCOES_CANAL_ID = 1394566723448995982  # Troque pelo ID do canal
BOASVINDAS_MSG_ID_FILE = "boasvindas_msg_id.txt"

# Carrega variáveis de ambiente
load_dotenv()
RAIDERIO_COOLDOWN_SECONDS = 300  # Altere aqui para ajustar o tempo de cooldown (em segundos)
BUTTON_COOLDOWN_SECONDS = 30

raiderio_cooldowns = {}
button_cooldowns = {}  # chave: (user_id, personagem_nome, acao), valor: timestamp
active_cadastros = {}
# --- CLASSES DE VIEW COM PROTEÇÃO CONTRA INTERFERÊNCIA ---

class PrivateView(View):
    """Classe base para todas as views privadas"""
    def __init__(self, interaction):
        super().__init__(timeout=300)  # 5 minutos de timeout
        self.autor_id = interaction.user.id
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "🚫 Você não pode interagir com este menu! Use `/cadastrar` para iniciar seu próprio cadastro.",
                ephemeral=True
            )
            return False
        return True

class CadastroModal(Modal, title="Cadastro de Personagem"):
    def __init__(self, cadastro_view):
        super().__init__()
        self.cadastro_view = cadastro_view
        self.nick_input = TextInput(
            label="Nick do personagem",
            placeholder="Ex: Arthas",
            required=True
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
            required=True
        )
        self.add_item(self.nick_input)
        self.add_item(self.funcao_input)
        self.add_item(self.raiderio_input)

    async def on_submit(self, interaction: discord.Interaction):
        nick = self.nick_input.value.strip()
        funcao = self.funcao_input.value.strip().capitalize()
        link = self.raiderio_input.value.strip()

        # Validação da função
        if funcao not in ["Tank", "Healer", "Dps"]:
            return await interaction.response.send_message(
                "❌ Função inválida. Digite exatamente: Tank, Healer ou DPS.",
                ephemeral=True
            )

        # Validação do Raider.IO e do nick
        try:
            # Remove barras finais e espaços
            link = link.strip().rstrip("/")
            # Esperado: .../characters/{region}/{realm}/{name}
            parts = link.split("/")
            if len(parts) < 7 or "characters" not in parts:
                raise Exception("Formato do link inválido. Use o link completo do seu personagem.")

            idx = parts.index("characters")
            region = parts[idx + 1]
            realm = parts[idx + 2]
            name = parts[idx + 3]

            api_url = (
                f"https://raider.io/api/v1/characters/profile"
                f"?region={region}&realm={realm}&name={name}"
                f"&fields=mythic_plus_scores_by_season:current"
            )
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as resp:
                    if resp.status != 200:
                        raise Exception("Link inválido ou jogador não encontrado.")
                    data = await resp.json()
                    score = data["mythic_plus_scores_by_season"][0]["scores"]["all"]
                    classe = data.get("class")
                    nome_personagem_api = data.get("name")
            # Verificação de nick
            if nome_personagem_api.lower() != nick.lower():
                return await interaction.response.send_message(
                    f"❌ O nick informado (**{nick}**) não corresponde ao personagem do Raider.IO (**{nome_personagem_api}**). Verifique e tente novamente.",
                    ephemeral=True
                )
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ Erro ao buscar Raider.IO: {e}", ephemeral=True
            )

        # Mapeamento automático de armadura
        classe_lower = classe.lower()
        if classe_lower in ["priest", "mage", "warlock"]:
            armadura = "Tecido"
        elif classe_lower in ["druid", "monk", "rogue", "demon hunter"]:
            armadura = "Couro"
        elif classe_lower in ["evoker", "shaman", "hunter"]:
            armadura = "Malha"
        elif classe_lower in ["death knight", "paladin", "warrior"]:
            armadura = "Placa"
        else:
            armadura = "Desconhecida"

        # Salva na view
        self.cadastro_view.armadura = armadura

        # Loga no terminal para você ajustar futuramente
        print(f"[DEBUG] Classe retornada pela API: {classe} | Armadura atribuída: {armadura}")

        # Salva os dados na view para uso posterior
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

    @discord.ui.button(label="Iniciar Cadastro", style=discord.ButtonStyle.primary, custom_id="iniciar_cadastro")
    async def iniciar_cadastro(self, interaction: discord.Interaction, button: Button):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.send_modal(CadastroModal(self))

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.danger, row=4)
    async def cancelar(self, interaction: discord.Interaction, button: Button):
        if await self.interaction_check(interaction):
            await interaction.response.edit_message(
                content="Cadastro cancelado.",
                view=None
            )
            # Remover o usuário do controle de cadastros ativos
            active_cadastros.pop(interaction.user.id, None)
            self.stop()

class ConfirmarCadastroView(View):
    def __init__(self, interaction, cadastro_view):
        super().__init__(timeout=120)
        self.interaction = interaction
        self.cadastro_view = cadastro_view

    @discord.ui.button(label="✅ Confirmar Cadastro", style=discord.ButtonStyle.success)
    async def confirmar(self, interaction: discord.Interaction, button: Button):
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
                f"Use `/perfil` para ver seu perfil completo ou "
                f"`/perfil @{interaction.user.name}` para compartilhar.",
                ephemeral=True
            )

            # Edita a mensagem ephemeral para um aviso final
            await interaction.edit_original_response(
                content="✅ Cadastro concluído! Esta janela será fechada automaticamente.",
                embed=None,
                view=None
            )

            # Edita a mensagem original do /cadastrar para mostrar apenas o aviso de cadastro concluído
            try:
                await self.cadastro_view.interaction.edit_original_response(
                    content="✅ Cadastro concluído! Caso queira cadastrar outro personagem, use o comando `/cadastrar` novamente.",
                    embed=None,
                    view=None
                )
            except Exception:
                pass

            # Libera o usuário para novo cadastro
            active_cadastros.pop(interaction.user.id, None)
            active_cadastros[interaction.user.id] = "concluido"
        except Exception as e:
            print(f"ERRO NO CADASTRO: {str(e)}")
            await interaction.followup.send(
                f"❌ **Erro crítico:** Falha ao completar cadastro\n"
                f"Motivo: {str(e)}\n\n"
                f"Por favor, tente novamente ou contate um administrador.",
                ephemeral=True
            )

class GerenciarPersonagemView(View):
    def __init__(self, personagem_nome):
        super().__init__(timeout=60)
        self.personagem_nome = personagem_nome

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
        async with aiosqlite.connect("raiderio.db") as db:
            await db.execute(
                "UPDATE jogadores SET disponibilidade = 1 WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            dados = await cursor.fetchone()
        embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
        embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
        embed.add_field(name="Função", value=dados[1] or "—", inline=True)
        embed.add_field(name="Armadura", value=dados[2] or "—", inline=True)
        embed.add_field(name="Disponível", value="🟢 Sim" if dados[3] else "🔴 Não", inline=True)
        embed.add_field(name="Raider.IO", value=f"[Link]({dados[4]})" if dados[4] else "—", inline=False)
        embed.add_field(name="Score M+", value=str(int(dados[5])) if dados[5] else "—", inline=True)
        embed.add_field(name="Última atualização", value=dados[8] or "—", inline=True)
        await interaction.response.edit_message(embed=embed, view=self, content=None)

    @discord.ui.button(label="🔴Indisponível", style=discord.ButtonStyle.danger)
    async def indisponivel(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "indisponivel"):
            return
        async with aiosqlite.connect("raiderio.db") as db:
            await db.execute(
                "UPDATE jogadores SET disponibilidade = 0 WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            await db.commit()
            cursor = await db.execute(
                "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            dados = await cursor.fetchone()
        embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
        embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
        embed.add_field(name="Função", value=dados[1] or "—", inline=True)
        embed.add_field(name="Armadura", value=dados[2] or "—", inline=True)
        embed.add_field(name="Disponível", value="🟢 Sim" if dados[3] else "🔴 Não", inline=True)
        embed.add_field(name="Raider.IO", value=f"[Link]({dados[4]})" if dados[4] else "—", inline=False)
        embed.add_field(name="Score M+", value=str(int(dados[5])) if dados[5] else "—", inline=True)
        embed.add_field(name="Última atualização", value=dados[8] or "—", inline=True)
        await interaction.response.edit_message(embed=embed, view=self, content=None)

    @discord.ui.button(label="⚠️Deletar Cadastro⚠️", style=discord.ButtonStyle.secondary)
    async def deletar(self, interaction: discord.Interaction, button: Button):
        if not await self._check_cooldown(interaction, "deletar"):
            return
        personagem_removido = self.personagem_nome
        async with aiosqlite.connect("raiderio.db") as db:
            await db.execute(
                "DELETE FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                (personagem_removido, str(interaction.user.id))
            )
            await db.commit()
        # Tenta editar a mensagem original, mas ignora se não for possível
        try:
            await interaction.message.edit(
                content="Para atualizar seus personagens use /perfil novamente.",
                embed=None,
                view=None
            )
        except Exception:
            pass
        await interaction.response.send_message(
            f"❌ O personagem **{personagem_removido}** foi removido do seu perfil.",
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

        # Atualiza cooldown
        raiderio_cooldowns[user_key] = now

        # Busca o link Raider.IO do personagem
        async with aiosqlite.connect("raiderio.db") as db:
            cursor = await db.execute(
                "SELECT raiderio_url FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                await interaction.response.send_message("❌ Link Raider.IO não encontrado para este personagem.", ephemeral=True)
                return
            url = row[0]

        # Busca novo score
        score = await obter_score_raiderio(url)
        if score is None:
            await interaction.response.send_message("❌ Não foi possível atualizar o score. Verifique o link Raider.IO.", ephemeral=True)
            return

        hoje = datetime.now().date().isoformat()
        async with aiosqlite.connect("raiderio.db") as db:
            await db.execute(
                "UPDATE jogadores SET raiderio_score = ?, ultima_atualizacao = ? WHERE personagem_nome = ? AND user_id = ?",
                (score, hoje, self.personagem_nome, str(interaction.user.id))
            )
            await db.commit()

        # Atualiza o embed em tempo real
        cursor = await db.execute(
            "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
            "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
            "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
            (self.personagem_nome, str(interaction.user.id))
        )
        dados = await cursor.fetchone()
        embed = discord.Embed(title=f"Perfil de {self.personagem_nome}", color=discord.Color.blue())
        embed.add_field(name="Classe", value=dados[7] or "—", inline=True)
        embed.add_field(name="Função", value=dados[1] or "—", inline=True)
        embed.add_field(name="Armadura", value=dados[2] or "—", inline=True)
        embed.add_field(name="Disponível", value="🟢 Sim" if dados[3] else "🔴 Não", inline=True)
        embed.add_field(name="Raider.IO", value=f"[Link]({dados[4]})" if dados[4] else "—", inline=False)
        embed.add_field(name="Score M+", value=str(int(dados[5])) if dados[5] else "—", inline=True)
        embed.add_field(name="Última atualização", value=dados[8] or "—", inline=True)
        await interaction.response.edit_message(embed=embed, view=self, content=None)
        
# --- DEPOIS DEFINIMOS A CLASSE PRINCIPAL DO BOT ---

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.db_conn = None
        self.db_lock = asyncio.Lock()
        

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
        await self.tree.sync()  # Sincroniza os comandos de barra

bot = Bot()

# --- COMANDOS INICIAL com slash---


@bot.tree.command(name="cadastrar", description="Inicia um cadastro privado")
async def cadastrar_slash(interaction: discord.Interaction):
    """Inicia um cadastro privado via slash command"""
    if interaction.user.id in active_cadastros:
        if active_cadastros[interaction.user.id] == "concluido":
            # Mensagem personalizada após cadastro concluído
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
        # Limpeza quando o cadastro terminar
        view.on_stop = lambda: active_cadastros.pop(interaction.user.id, None)
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Erro ao iniciar cadastro: {e}",
            ephemeral=True
        )


# --- FUNÇÕES AUXILIARES ---

async def obter_score_raiderio(url: str) -> Optional[float]:
    try:
        parts = url.rstrip("/").split("/")
        region, realm, name = parts[-3], parts[-2], parts[-1]
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://raider.io/api/v1/characters/profile"
                f"?region={region}&realm={realm}&name={name}"
                f"&fields=mythic_plus_scores_by_season:current"
            ) as resp:
                data = await resp.json()
                return data["mythic_plus_scores_by_season"][0]["scores"]["all"]
    except:
        return None

# --- INICIALIZAÇÃO DO BOT ---

@bot.event
async def on_ready():
    print(f"✅ Bot online como {bot.user.name}")
    canal = bot.get_channel(INSTRUCOES_CANAL_ID)
    if canal:
        # Tenta deletar a mensagem anterior
        try:
            if os.path.exists(BOASVINDAS_MSG_ID_FILE):
                with open(BOASVINDAS_MSG_ID_FILE, "r") as f:
                    msg_id = int(f.read().strip())
                msg = await canal.fetch_message(msg_id)
                await msg.delete()
        except Exception as e:
            print(f"[Boas-vindas] Nenhuma mensagem antiga para deletar ou erro: {e}")

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
        # Salva o ID da nova mensagem
        with open(BOASVINDAS_MSG_ID_FILE, "w") as f:
            f.write(str(msg.id))

class ListaPersonagensView(View):
    def __init__(self, personagens, interaction):
        super().__init__(timeout=60)
        self.interaction = interaction
        for nome in personagens:
            self.add_item(PersonagemButton(nome))

class PersonagemButton(Button):
    def __init__(self, personagem_nome):
        super().__init__(label=personagem_nome, style=discord.ButtonStyle.primary)
        self.personagem_nome = personagem_nome

    async def callback(self, interaction: discord.Interaction):
        async with aiosqlite.connect("raiderio.db") as db:
            cursor = await db.execute(
                "SELECT nome, funcao, armadura, disponibilidade, raiderio_url, "
                "raiderio_score, personagem_nome, personagem_classe, ultima_atualizacao "
                "FROM jogadores WHERE personagem_nome = ? AND user_id = ?",
                (self.personagem_nome, str(interaction.user.id))
            )
            dados = await cursor.fetchone()
        if not dados:
            await interaction.response.send_message("❌ Personagem não encontrado.", ephemeral=True)
            return

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

# --- AGORA SIM, DEPOIS DISSO, O COMANDO /perfil ---
@bot.tree.command(name="perfil", description="Veja seus personagens registrados")
async def perfil_slash(interaction: discord.Interaction):
    async with bot.db_lock:
        cursor = await bot.db_conn.execute(
            "SELECT personagem_nome FROM jogadores WHERE user_id = ?",
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

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ Variável DISCORD_TOKEN não encontrada no .env!")
    else:
        bot.run(TOKEN)